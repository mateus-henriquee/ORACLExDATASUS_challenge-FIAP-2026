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
    """Valida o token contra a API de autenticacao (porta 8001) e retorna o username.
    Se nao houver token valido, retorna 'anonimo'."""
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

# ---------- Fonte de dados (Oracle e o padrao; CSV e opcional) ----------

FONTES_PATH = DATA_DIR / "fontes.json"

# Consulta padrao usada quando a fonte e o Oracle. Traz os nomes ja resolvidos
# (municipio, hospital) para o usuario leigo nao precisar lidar com codigos.
SQL_ORACLE_PADRAO = """
    SELECT
        m.NOME              AS MUNICIPIO,
        h.NOME              AS HOSPITAL,
        f.COMPETENCIA       AS COMPETENCIA,
        f.SEXO              AS SEXO,
        f.FAIXA_ETARIA      AS FAIXA_ETARIA,
        f.DIAS_PERMANENCIA  AS DIAS_PERMANENCIA,
        f.VALOR_TOTAL       AS VALOR_TOTAL
    FROM FATO_INTERNACAO f
    JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE
    JOIN DIM_HOSPITAL  h ON f.COD_CNES = h.COD_CNES
"""


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
    """Devolve o caminho do ultimo CSV enviado neste chat, se houver."""
    arquivos = sorted(UPLOADS_DIR.glob(f"raw_{chat_id}_*"))
    return arquivos[-1] if arquivos else None


def carregar_do_oracle(chat_id: int):
    logger.info("Carregando dados do Oracle para o chat %s...", chat_id)
    df = oracle_connector.query_to_dataframe(SQL_ORACLE_PADRAO)
    rag.index_dataframe(chat_id, df)
    set_fonte(chat_id, "oracle")
    logger.info("Oracle carregado: %d linhas.", len(df))
    return df


def garantir_dados(chat_id: int):
    """Devolve o DataFrame do chat. Se ainda nao houver nada carregado,
    busca automaticamente do Oracle (fonte padrao do perfil leigo)."""
    df = rag.load_dataframe(chat_id)
    if df is not None:
        return df
    return carregar_do_oracle(chat_id)


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


# ---------- Ingestão de dados ----------

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
    """Diz qual fonte esta ativa e se existe um CSV disponivel para trocar."""
    return {
        "tipo": get_fonte(chat_id),
        "csv_disponivel": csv_do_chat(chat_id) is not None,
    }


@app.post("/chats/{chat_id}/fonte")
def trocar_fonte(chat_id: int, tipo: str = Form(...)):
    """Troca entre os dados do Oracle e o CSV enviado pelo usuario."""
    if tipo not in ("oracle", "csv"):
        raise HTTPException(status_code=400, detail="Fonte invalida. Use 'oracle' ou 'csv'.")

    try:
        if tipo == "oracle":
            df = carregar_do_oracle(chat_id)
        else:
            caminho = csv_do_chat(chat_id)
            if caminho is None:
                raise HTTPException(status_code=400, detail="Nenhum CSV foi enviado neste chat ainda.")
            df = pd.read_csv(caminho)
            rag.index_dataframe(chat_id, df)
            set_fonte(chat_id, "csv")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Falha ao trocar fonte: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Nao foi possivel carregar a fonte '{tipo}': {e}")

    return {"ok": True, "tipo": tipo, "linhas": len(df), "colunas": list(df.columns)}


# ---------- Chat / perguntas ----------

@app.post("/chats/{chat_id}/message")
def send_message(chat_id: int, question: str = Form(...)):
    hist = db.get_history(chat_id)
    db.add_message(chat_id, "user", question)

    wants_plot = any(k in question.lower() for k in PLOT_KEYWORDS)
    plot_filename = None
    table_filename = None
    chart_data = None

    # Fonte padrao e o Oracle: se nada foi carregado ainda neste chat,
    # busca automaticamente do banco antes de responder.
    df = None
    erro_dados = None
    try:
        df = garantir_dados(chat_id)
    except Exception as e:
        logger.error("Falha ao carregar dados: %s", traceback.format_exc())
        erro_dados = str(e)

    rag_context = rag.retrieve(chat_id, question)

    # Se a fonte for Oracle e nao for pedido de grafico, tenta gerar SQL dinamico.
    # O modelo gera a query, Python executa no Oracle e usa o resultado como contexto.
    # Isso permite responder qualquer agregacao (ranking, media, contagem por grupo)
    # que o RAG simples nao consegue, porque o RAG so ve linhas brutas, nao agregadas.
    sql_resultado = None
    if not wants_plot and get_fonte(chat_id) == "oracle" and df is not None:
        try:
            sql = llm.generate_sql(question)
            if sql:
                logger.info("SQL gerado pelo modelo:\n%s", sql)
                # substitui o alias DADOS pelo subselect da query padrao
                sql_exec = sql.replace(
                    "FROM DADOS",
                    f"FROM ({SQL_ORACLE_PADRAO.strip()}) DADOS",
                )
                df_sql = oracle_connector.query_to_dataframe(sql_exec)
                if not df_sql.empty:
                    linhas = df_sql.head(20).to_string(index=False)
                    sql_resultado = [
                        f"Resultado da consulta SQL para responder a pergunta ({len(df_sql)} linhas):\n{linhas}"
                    ]
                    logger.info("SQL executado com sucesso: %d linhas retornadas.", len(df_sql))
        except Exception as e:
            logger.warning("Falha ao executar SQL dinamico: %s", e)
            # nao propaga o erro: cai silenciosamente pro RAG normal

    contexto_final = (sql_resultado or rag_context)

    try:
        if df is None:
            answer = (
                "Nao consegui acessar os dados do Oracle agora. "
                f"Detalhe tecnico: {erro_dados}. "
                "Confira se o banco esta rodando, ou envie um CSV para analisar."
            )
        elif wants_plot:
            spec = llm.suggest_plot_spec(list(df.columns), question)
            if not spec or spec.get("x") not in df.columns:
                answer = (
                    "Nao identifiquei quais colunas usar. "
                    f"As colunas disponiveis sao: {', '.join(df.columns)}."
                )
            else:
                ptype = spec.get("type", "bar")
                x, y, title = spec["x"], spec.get("y"), spec.get("title", "")
                aggf = spec.get("agg", "count")

                if ptype == "hist":
                    plot_filename = Path(plots.hist_plot(df, x, title=title)).name
                elif ptype == "scatter":
                    plot_filename = Path(plots.scatter_plot(df, x, y, title=title)).name
                elif ptype == "line":
                    agg_df = plots.prepare_plot_data(df, x, y, aggf, is_temporal_ok=True)
                    plot_filename = Path(plots.line_plot(agg_df, "categoria", "valor", title=title)).name
                    resumo = ", ".join(f"{r.categoria}: {r.valor}" for r in agg_df.itertuples())
                    contexto_final = contexto_final + [f"Dados exatos do grafico gerado agora: {resumo}."]
                else:
                    # grafico de barra: interativo (Chart.js), sem gerar imagem
                    agg_df = plots.prepare_plot_data(df, x, y, aggf, is_temporal_ok=False)
                    value_label = y if (y and aggf != "count") else "Contagem"
                    chart_title = title or f"{x} por categoria"
                    chart_data = json.dumps({
                        "title": chart_title,
                        "subtitle": value_label,
                        "value_label": value_label,
                        "labels": agg_df["categoria"].tolist(),
                        "values": agg_df["valor"].tolist(),
                    })
                    resumo = ", ".join(f"{r.categoria}: {r.valor}" for r in agg_df.itertuples())
                    contexto_final = contexto_final + [
                        f"Dados exatos do grafico gerado agora, ja ordenados do maior para o menor valor: {resumo}."
                    ]

                answer = llm.generate(hist, contexto_final, question)
        else:
            answer = llm.generate(hist, contexto_final, question)
    except Exception as e:
        logger.error("Falha ao gerar resposta: %s", traceback.format_exc())
        answer = f"Erro interno ao gerar a resposta: {e}. Veja o terminal do servidor para o traceback completo."

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
