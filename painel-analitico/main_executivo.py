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
from datetime import datetime

import oracledb
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

app = FastAPI(title="Painel Executivo - API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # em producao, restrinja ao dominio real do dashboard
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_conn():
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def query(sql: str, params: dict = None) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


@app.get("/api/kpis")
def get_kpis(competencia: str | None = None, sexo: str | None = None,
             municipio: str | None = None, faixa_etaria: str | None = None):
    conds, params = [], {}
    if competencia:  conds.append("COMPETENCIA = :comp");      params["comp"] = competencia
    if sexo:         conds.append("SEXO = :sexo");              params["sexo"] = sexo
    if faixa_etaria: conds.append("FAIXA_ETARIA = :faixa");    params["faixa"] = faixa_etaria
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT
            NVL(SUM(VALOR_TOTAL), 0)          AS custo_total,
            COUNT(*)                           AS total_internacoes,
            NVL(AVG(VALOR_TOTAL), 0)          AS custo_medio,
            NVL(AVG(DIAS_PERMANENCIA), 0)     AS permanencia_media,
            COUNT(DISTINCT COD_IBGE)           AS municipios_distintos
        FROM FATO_INTERNACAO
        {where}
    """
    resultado = query(sql, params)
    linha = resultado[0] if resultado else {}
    return {
        "custo_total":          float(linha.get("custo_total") or 0),
        "total_internacoes":    int(linha.get("total_internacoes") or 0),
        "custo_medio":          float(linha.get("custo_medio") or 0),
        "permanencia_media":    float(linha.get("permanencia_media") or 0),
        "municipios_distintos": int(linha.get("municipios_distintos") or 0),
    }


@app.get("/api/custo-por-mes")
def custo_por_mes():
    """Serie temporal para os graficos de linha e radar."""
    sql = """
        SELECT COMPETENCIA AS competencia,
               SUM(VALOR_TOTAL)         AS custo,
               COUNT(*)                 AS internacoes,
               AVG(DIAS_PERMANENCIA)    AS permanencia_media
        FROM FATO_INTERNACAO
        GROUP BY COMPETENCIA
        ORDER BY COMPETENCIA
    """
    linhas = query(sql)
    return [
        {
            "competencia":      r["competencia"],
            "custo":            float(r["custo"] or 0),
            "internacoes":      int(r["internacoes"] or 0),
            "permanencia_media": float(r["permanencia_media"] or 0),
        }
        for r in linhas
    ]


@app.get("/api/custo-por-hospital")
def custo_por_hospital(limite: int = 5):
    """Top N hospitais por custo, para o grafico de barra e a tabela."""
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
    """Autocomplete da busca de hospital na sidebar."""
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
    """Preenche o dropdown de mes com as competencias que ja tem dado carregado."""
    sql = "SELECT DISTINCT COMPETENCIA AS competencia FROM FATO_INTERNACAO ORDER BY COMPETENCIA"
    return [r["competencia"] for r in query(sql)]


@app.get("/api/analise/sexo")
def analise_sexo(competencia: str | None = None, sexo: str | None = None,
                 municipio: str | None = None, faixa_etaria: str | None = None):
    conds, params = [], {}
    if competencia: conds.append("f.COMPETENCIA = :comp"); params["comp"] = competencia
    if sexo:        conds.append("f.SEXO = :sexo");        params["sexo"] = sexo
    if faixa_etaria: conds.append("f.FAIXA_ETARIA = :faixa"); params["faixa"] = faixa_etaria
    if municipio:
        conds.append("UPPER(m.NOME) LIKE UPPER(:mun)"); params["mun"] = f"%{municipio}%"
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT f.SEXO AS sexo, COUNT(*) AS qtd
        FROM FATO_INTERNACAO f
        JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE
        {where}
        GROUP BY f.SEXO ORDER BY qtd DESC
    """
    return [{"sexo": r["sexo"], "qtd": int(r["qtd"])} for r in query(sql, params)]


@app.get("/api/analise/faixa-etaria")
def analise_faixa_etaria(competencia: str | None = None, sexo: str | None = None,
                         municipio: str | None = None, faixa_etaria: str | None = None):
    conds, params = [], {}
    if competencia: conds.append("f.COMPETENCIA = :comp"); params["comp"] = competencia
    if sexo:        conds.append("f.SEXO = :sexo");        params["sexo"] = sexo
    if faixa_etaria: conds.append("f.FAIXA_ETARIA = :faixa"); params["faixa"] = faixa_etaria
    if municipio:
        conds.append("UPPER(m.NOME) LIKE UPPER(:mun)"); params["mun"] = f"%{municipio}%"
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT f.FAIXA_ETARIA AS faixa_etaria, COUNT(*) AS qtd
        FROM FATO_INTERNACAO f
        JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE
        {where}
        GROUP BY f.FAIXA_ETARIA ORDER BY qtd DESC
    """
    return [{"faixa_etaria": r["faixa_etaria"], "qtd": int(r["qtd"])} for r in query(sql, params)]


@app.get("/api/analise/municipios-top")
def analise_municipios_top(limite: int = 10, competencia: str | None = None,
                           sexo: str | None = None, faixa_etaria: str | None = None):
    conds, params = [], {"limite": limite}
    if competencia:  conds.append("f.COMPETENCIA = :comp");  params["comp"] = competencia
    if sexo:         conds.append("f.SEXO = :sexo");          params["sexo"] = sexo
    if faixa_etaria: conds.append("f.FAIXA_ETARIA = :faixa"); params["faixa"] = faixa_etaria
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT m.NOME AS municipio, COUNT(*) AS internacoes, SUM(f.VALOR_TOTAL) AS custo
        FROM FATO_INTERNACAO f
        JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE
        {where}
        GROUP BY m.NOME
        ORDER BY internacoes DESC
        FETCH FIRST :limite ROWS ONLY
    """
    return [
        {"municipio": r["municipio"], "internacoes": int(r["internacoes"]),
         "custo": float(r["custo"] or 0)}
        for r in query(sql, params)
    ]


# ---------- Chatbot restrito (sem IA -- botao -> SQL real -> resposta) ----------

def fmt_real(valor: float) -> str:
    """Formata numero no padrao brasileiro: R$ 1.234.567,89"""
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


PERGUNTAS_PRONTAS = [
    {"id": "mes_maior_custo", "label": "Qual mês teve o maior custo?"},
    {"id": "hospital_maior_custo", "label": "Qual hospital teve o maior custo?"},
    {"id": "permanencia_media", "label": "Qual a permanência média das internações?"},
    {"id": "custo_medio_internacao", "label": "Qual o custo médio por internação?"},
    {"id": "total_internacoes", "label": "Quantas internações no total?"},
]


@app.get("/api/chatbot/perguntas")
def listar_perguntas():
    """Lista fixa de perguntas que o botao do chatbot oferece."""
    return PERGUNTAS_PRONTAS


@app.get("/api/chatbot/responder")
def responder_pergunta(pergunta_id: str):
    """Cada pergunta tem UMA consulta SQL fixa associada -- nada de texto
    livre, nada de IA. A resposta e sempre calculada na hora, com dado real."""

    if pergunta_id == "mes_maior_custo":
        r = query("""
            SELECT COMPETENCIA AS competencia, SUM(VALOR_TOTAL) AS custo
            FROM FATO_INTERNACAO
            GROUP BY COMPETENCIA
            ORDER BY custo DESC
            FETCH FIRST 1 ROW ONLY
        """)
        if not r:
            return {"resposta": "Ainda não há dados suficientes para responder."}
        return {"resposta": f"O mês com maior custo foi {r[0]['competencia']}, "
                             f"totalizando {fmt_real(float(r[0]['custo']))}."}

    if pergunta_id == "hospital_maior_custo":
        r = query("""
            SELECT h.NOME AS hospital, SUM(f.VALOR_TOTAL) AS custo
            FROM FATO_INTERNACAO f
            JOIN DIM_HOSPITAL h ON f.COD_CNES = h.COD_CNES
            GROUP BY h.NOME
            ORDER BY custo DESC
            FETCH FIRST 1 ROW ONLY
        """)
        if not r:
            return {"resposta": "Ainda não há dados suficientes para responder."}
        return {"resposta": f"O hospital com maior custo no período foi {r[0]['hospital']}, "
                             f"totalizando {fmt_real(float(r[0]['custo']))}."}

    if pergunta_id == "permanencia_media":
        r = query("SELECT ROUND(AVG(DIAS_PERMANENCIA), 1) AS media FROM FATO_INTERNACAO")
        if not r or r[0]["media"] is None:
            return {"resposta": "Ainda não há dados suficientes para responder."}
        media = str(r[0]["media"]).replace(".", ",")
        return {"resposta": f"A permanência média das internações é de {media} dias."}

    if pergunta_id == "custo_medio_internacao":
        r = query("SELECT AVG(VALOR_TOTAL) AS media FROM FATO_INTERNACAO")
        if not r or r[0]["media"] is None:
            return {"resposta": "Ainda não há dados suficientes para responder."}
        return {"resposta": f"O custo médio por internação é de {fmt_real(float(r[0]['media']))}."}

    if pergunta_id == "total_internacoes":
        r = query("SELECT COUNT(*) AS total FROM FATO_INTERNACAO")
        total = r[0]["total"] if r else 0
        return {"resposta": f"O total de internações no período carregado é {total:,}".replace(",", ".") + "."}

    raise HTTPException(status_code=404, detail="Pergunta não reconhecida.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
