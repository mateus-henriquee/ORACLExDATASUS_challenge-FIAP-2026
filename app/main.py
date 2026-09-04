# main.py da IA — usa Materialized Views para respostas rápidas
import json
import logging
import re
import shutil
import traceback
import unicodedata
import urllib.request
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ia-cientista-dados")

from . import database as db
from . import rag
from . import llm
from . import plots
from . import oracle_connector
from .config import UPLOADS_DIR, PLOTS_DIR, DATA_DIR

app = FastAPI(title="IA Cientista de Dados Local")
db.init_db()

PLOT_KEYWORDS = ["grafico", "gráfico", "plot", "plotar", "visualiza", "chart"]

AUTH_API = "http://localhost:8001"


def _usuario_do_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "anonimo"
    token = authorization.split(" ", 1)[1]
    try:
        req = urllib.request.Request(
            f"{AUTH_API}/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("usuario", "anonimo")
    except Exception:
        return "anonimo"


# ──────────────────────────────────────────────────────────────────────────────
#  QUERIES NAS MVS — rápidas, sem full table scan
# ──────────────────────────────────────────────────────────────────────────────

# Query padrão para carregar o DataFrame base do chat
# Usa MV_MUNICIPIO + MV_COMPETENCIA para ser leve
SQL_BASE_DF = """
    SELECT
        m.MUNICIPIO,
        m.INTERNACOES,
        m.CUSTO_TOTAL,
        m.CUSTO_MEDIO,
        m.PERM_MEDIA
    FROM MV_MUNICIPIO m
    ORDER BY m.INTERNACOES DESC
    FETCH FIRST 1000 ROWS ONLY
"""

FONTES_PATH = DATA_DIR / "fontes.json"


def _ler_fontes() -> dict:
    if FONTES_PATH.exists():
        try:
            return json.loads(FONTES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def get_fonte(chat_id: int) -> str:
    return _ler_fontes().get(str(chat_id), "oracle")


def set_fonte(chat_id: int, tipo: str):
    fontes = _ler_fontes()
    fontes[str(chat_id)] = tipo
    FONTES_PATH.write_text(json.dumps(fontes), encoding="utf-8")


def csv_do_chat(chat_id: int):
    arquivos = sorted(UPLOADS_DIR.glob(f"raw_{chat_id}_*"))
    return arquivos[-1] if arquivos else None


def carregar_do_oracle(chat_id: int):
    logger.info("Carregando dados do Oracle (MV) para o chat %s...", chat_id)
    df = oracle_connector.query_to_dataframe(SQL_BASE_DF)
    rag.index_dataframe(chat_id, df)
    set_fonte(chat_id, "oracle")
    logger.info("Oracle MV carregado: %d linhas.", len(df))
    return df


def garantir_dados(chat_id: int):
    df = rag.load_dataframe(chat_id)
    if df is not None:
        return df
    return carregar_do_oracle(chat_id)


def _executar_mv(sql: str) -> pd.DataFrame | None:
    """Executa uma query nas MVs e retorna o DataFrame."""
    try:
        return oracle_connector.query_to_dataframe(sql)
    except Exception as e:
        logger.warning("Falha ao consultar MV: %s", e)
        return None


@app.on_event("startup")
def startup():
    llm.get_llm()


# ---------- Abas de chat ----------

@app.post("/chats")
def create_chat(name: str = Form("Nova conversa"),
                authorization: str | None = Header(default=None)):
    usuario = _usuario_do_token(authorization)
    try:
        chat_id = db.create_chat(name, usuario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": chat_id, "name": name}


@app.get("/chats")
def list_chats(authorization: str | None = Header(default=None)):
    usuario = _usuario_do_token(authorization)
    return db.list_chats(usuario)


@app.put("/chats/{chat_id}")
def rename_chat(chat_id: int, name: str = Form(...)):
    db.rename_chat(chat_id, name)
    return {"ok": True}


@app.delete("/chats/{chat_id}")
def delete_chat(chat_id: int):
    db.delete_chat(chat_id)
    return {"ok": True}


@app.get("/chats/{chat_id}/history")
def history(chat_id: int):
    return db.get_history(chat_id)


# ---------- Ingestão ----------

@app.post("/chats/{chat_id}/upload_csv")
async def upload_csv(chat_id: int, file: UploadFile = File(...)):
    dest = UPLOADS_DIR / f"raw_{chat_id}_{file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    df = pd.read_csv(dest)
    rag.index_dataframe(chat_id, df)
    set_fonte(chat_id, "csv")
    return {"ok": True, "linhas": len(df), "colunas": list(df.columns)}


@app.post("/chats/{chat_id}/load_oracle")
def load_oracle(chat_id: int, sql: str = Form(...)):
    df = oracle_connector.query_to_dataframe(sql)
    rag.index_dataframe(chat_id, df)
    set_fonte(chat_id, "oracle")
    return {"ok": True, "linhas": len(df), "colunas": list(df.columns)}


@app.get("/chats/{chat_id}/fonte")
def ler_fonte(chat_id: int):
    return {
        "tipo": get_fonte(chat_id),
        "csv_disponivel": csv_do_chat(chat_id) is not None,
    }


@app.post("/chats/{chat_id}/fonte")
def trocar_fonte(chat_id: int, tipo: str = Form(...)):
    if tipo not in ("oracle", "csv"):
        raise HTTPException(status_code=400, detail="Fonte invalida.")
    try:
        if tipo == "oracle":
            df = carregar_do_oracle(chat_id)
        else:
            caminho = csv_do_chat(chat_id)
            if caminho is None:
                raise HTTPException(status_code=400, detail="Nenhum CSV enviado ainda.")
            df = pd.read_csv(caminho)
            rag.index_dataframe(chat_id, df)
            set_fonte(chat_id, "csv")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nao foi possivel carregar '{tipo}': {e}")
    return {"ok": True, "tipo": tipo, "linhas": len(df), "colunas": list(df.columns)}


# ---------- Chat ----------

@app.post("/chats/{chat_id}/message")
def send_message(chat_id: int, question: str = Form(...)):
    hist = db.get_history(chat_id)
    db.add_message(chat_id, "user", question)

    wants_plot   = any(k in question.lower() for k in PLOT_KEYWORDS)
    plot_filename = None
    table_filename = None
    chart_data    = None

    df = None
    erro_dados = None
    try:
        df = garantir_dados(chat_id)
    except Exception as e:
        logger.error("Falha ao carregar dados: %s", traceback.format_exc())
        erro_dados = str(e)

    rag_context = rag.retrieve(chat_id, question) if df is not None else []

    # ── Roteador de intenção rápido (substitui generate_sql pesado) ──
    mv_contexto = None
    if not wants_plot and get_fonte(chat_id) == "oracle" and df is not None:
        intencao = llm.rotear_intencao(question)
        if intencao:
            logger.info("Intenção detectada: %s | SQL: %s", intencao["nome"], intencao["sql"])
            df_mv = _executar_mv(intencao["sql"])
            if df_mv is not None and not df_mv.empty:
                linhas = df_mv.head(15).to_string(index=False)
                mv_contexto = [
                    f"Dados pré-calculados ({intencao['descricao']}) — {len(df_mv)} registros:\n{linhas}"
                ]
                logger.info("MV consultada com sucesso: %d linhas.", len(df_mv))

    contexto_final = mv_contexto or rag_context

    try:
        if df is None:
            answer = (
                "Nao consegui acessar os dados do Oracle agora. "
                f"Detalhe: {erro_dados}. "
                "Confira se o banco esta rodando."
            )
        elif wants_plot:
            # Para plots, usa a MV mais adequada baseada na intenção
            intencao = llm.rotear_intencao(question)
            df_plot = df  # fallback: df base

            if intencao and get_fonte(chat_id) == "oracle":
                df_mv = _executar_mv(intencao["sql"])
                if df_mv is not None and not df_mv.empty:
                    df_plot = df_mv

            spec = llm.suggest_plot_spec(list(df_plot.columns), question)
            if not spec or spec.get("x") not in df_plot.columns:
                answer = (
                    "Nao identifiquei quais colunas usar. "
                    f"Colunas disponíveis: {', '.join(df_plot.columns)}."
                )
            else:
                ptype = spec.get("type", "bar")
                x, y, title = spec["x"], spec.get("y"), spec.get("title", "")
                aggf = spec.get("agg", "count")

                if ptype == "hist":
                    plot_filename = Path(plots.hist_plot(df_plot, x, title=title)).name
                elif ptype == "scatter":
                    plot_filename = Path(plots.scatter_plot(df_plot, x, y, title=title)).name
                elif ptype == "line":
                    agg_df = plots.prepare_plot_data(df_plot, x, y, aggf, is_temporal_ok=True)
                    plot_filename = Path(plots.line_plot(agg_df, "categoria", "valor", title=title)).name
                    resumo = ", ".join(f"{r.categoria}: {r.valor}" for r in agg_df.itertuples())
                    contexto_final = contexto_final + [f"Dados exatos do grafico: {resumo}."]
                else:
                    agg_df = plots.prepare_plot_data(df_plot, x, y, aggf, is_temporal_ok=False)
                    value_label = y if (y and aggf != "count") else "Contagem"
                    chart_data = json.dumps({
                        "title": title or f"{x} por categoria",
                        "subtitle": value_label,
                        "value_label": value_label,
                        "labels": agg_df["categoria"].tolist(),
                        "values": agg_df["valor"].tolist(),
                    })
                    resumo = ", ".join(f"{r.categoria}: {r.valor}" for r in agg_df.itertuples())
                    contexto_final = contexto_final + [f"Dados do grafico: {resumo}."]

                answer = llm.generate(hist, contexto_final, question)
        else:
            answer = llm.generate(hist, contexto_final, question)

    except Exception as e:
        logger.error("Falha ao gerar resposta: %s", traceback.format_exc())
        answer = f"Erro interno: {e}."

    db.add_message(chat_id, "assistant", answer, plot_filename, table_filename, chart_data)
    return {
        "answer": answer,
        "plot_url": f"/plots/{plot_filename}" if plot_filename else None,
        "table_url": f"/plots/{table_filename}" if table_filename else None,
        "chart_data": json.loads(chart_data) if chart_data else None,
    }


@app.get("/plots/{filename}")
def get_plot(filename: str):
    path = PLOTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


app.mount("/", StaticFiles(directory="static", html=True), name="static")