"""
API do painel executivo. Consulta as tabelas ja populadas no Oracle
(DIM_HOSPITAL, DIM_MUNICIPIO, FATO_INTERNACAO) e devolve os dados agregados
que o dashboard consome.

IMPORTANTE: os endpoints que dependem de FATO_INTERNACAO so devolvem dado
de verdade depois que o sync_sih_sus.py rodar com sucesso. Ate la, eles
devolvem uma lista vazia (nao erro) -- o frontend deve tratar isso mostrando
"sem dados no periodo" em vez de quebrar.
"""
import os

import oracledb
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

app = FastAPI(title="Painel Executivo - API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_conn():
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def query(sql: str, params: dict = None) -> list:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


@app.get("/api/kpis")
def get_kpis(competencia: str = None):
    filtro = "WHERE COMPETENCIA = :comp" if competencia else ""
    params = {"comp": competencia} if competencia else {}

    sql = f"""
        SELECT
            NVL(SUM(VALOR_TOTAL), 0)          AS custo_total,
            COUNT(*)                           AS total_internacoes,
            NVL(AVG(VALOR_TOTAL), 0)          AS custo_medio,
            NVL(AVG(DIAS_PERMANENCIA), 0)     AS permanencia_media
        FROM FATO_INTERNACAO
        {filtro}
    """
    resultado = query(sql, params)
    linha = resultado[0] if resultado else {}
    return {
        "custo_total": float(linha.get("custo_total") or 0),
        "total_internacoes": int(linha.get("total_internacoes") or 0),
        "custo_medio": float(linha.get("custo_medio") or 0),
        "permanencia_media": float(linha.get("permanencia_media") or 0),
    }


@app.get("/api/custo-por-mes")
def custo_por_mes():
    sql = """
        SELECT COMPETENCIA AS competencia, SUM(VALOR_TOTAL) AS custo
        FROM FATO_INTERNACAO
        GROUP BY COMPETENCIA
        ORDER BY COMPETENCIA
    """
    linhas = query(sql)
    return [{"competencia": r["competencia"], "custo": float(r["custo"] or 0)} for r in linhas]


@app.get("/api/custo-por-hospital")
def custo_por_hospital(limite: int = 5):
    sql = """
        SELECT h.NOME AS hospital, m.NOME AS municipio,
               COUNT(*) AS internacoes, SUM(f.VALOR_TOTAL) AS custo
        FROM FATO_INTERNACAO f
        JOIN DIM_HOSPITAL h ON f.COD_CNES = h.COD_CNES
        JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE
        GROUP BY h.NOME, m.NOME
        ORDER BY SUM(f.VALOR_TOTAL) DESC
        FETCH FIRST :limite ROWS ONLY
    """
    linhas = query(sql, {"limite": limite})
    if not linhas:
        return []
    maior_custo = max(float(r["custo"] or 0) for r in linhas)
    return [
        {
            "hospital": r["hospital"],
            "municipio": r["municipio"],
            "internacoes": int(r["internacoes"] or 0),
            "custo": float(r["custo"] or 0),
            "pct": round((float(r["custo"] or 0) / maior_custo) * 100, 1) if maior_custo else 0,
        }
        for r in linhas
    ]


@app.get("/api/hospitais/buscar")
def buscar_hospitais(q: str = ""):
    if len(q) < 2:
        return []
    sql = """
        SELECT COD_CNES AS cod_cnes, NOME AS nome
        FROM DIM_HOSPITAL
        WHERE UPPER(NOME) LIKE UPPER(:termo)
        FETCH FIRST 10 ROWS ONLY
    """
    return query(sql, {"termo": f"%{q}%"})


@app.get("/api/competencias")
def listar_competencias():
    sql = "SELECT DISTINCT COMPETENCIA AS competencia FROM FATO_INTERNACAO ORDER BY COMPETENCIA"
    return [r["competencia"] for r in query(sql)]