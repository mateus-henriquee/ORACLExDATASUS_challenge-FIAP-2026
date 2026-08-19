import json
import logging
import shutil
import traceback
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ia-cientista-dados")

from . import database as db
from . import rag
from . import llm
from . import plots
from . import oracle_connector
from .config import UPLOADS_DIR, PLOTS_DIR

app = FastAPI(title="IA Cientista de Dados Local")
db.init_db()

PLOT_KEYWORDS = ["grafico", "gráfico", "plot", "plotar", "visualiza", "chart"]


@app.on_event("startup")
def startup():
    llm.get_llm()


# ---------- Abas de chat ----------

@app.post("/chats")
def create_chat(name: str = Form("Nova conversa")):
    try:
        chat_id = db.create_chat(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": chat_id, "name": name}


@app.get("/chats")
def list_chats():
    return db.list_chats()


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
    return {"ok": True, "linhas": len(df), "colunas": list(df.columns)}


@app.post("/chats/{chat_id}/load_oracle")
def load_oracle(chat_id: int, sql: str = Form(...)):
    df = oracle_connector.query_to_dataframe(sql)
    rag.index_dataframe(chat_id, df)
    return {"ok": True, "linhas": len(df), "colunas": list(df.columns)}


# ---------- Chat / perguntas ----------

@app.post("/chats/{chat_id}/message")
def send_message(chat_id: int, question: str = Form(...)):
    hist = db.get_history(chat_id)
    rag_context = rag.retrieve(chat_id, question)
    db.add_message(chat_id, "user", question)

    wants_plot = any(k in question.lower() for k in PLOT_KEYWORDS)
    plot_filename = None
    table_filename = None
    chart_data = None

    try:
        if wants_plot:
            df = rag.load_dataframe(chat_id)
            if df is None:
                answer = "Nenhum dado carregado ainda. Envie um CSV ou carregue dados do Oracle primeiro."
            else:
                spec = llm.suggest_plot_spec(list(df.columns), question)
                if not spec or spec.get("x") not in df.columns:
                    answer = "Nao identifiquei quais colunas usar. Informe as colunas do grafico."
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
                        rag_context = rag_context + [f"Dados exatos do grafico gerado agora: {resumo}."]
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
                        rag_context = rag_context + [
                            f"Dados exatos do grafico gerado agora, ja ordenados do maior para o menor valor: {resumo}."
                        ]

                    answer = llm.generate(hist, rag_context, question)
        else:
            answer = llm.generate(hist, rag_context, question)
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