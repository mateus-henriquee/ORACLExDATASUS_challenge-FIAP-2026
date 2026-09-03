"""
API do painel executivo. Consulta as tabelas ja populadas no Oracle
(DIM_HOSPITAL, DIM_MUNICIPIO, FATO_INTERNACAO) e devolve os dados agregados
que o dashboard consome.

IMPORTANTE: os endpoints que dependem de FATO_INTERNACAO so devolvem dado
de verdade depois que o sync_sih_sus.py rodar com sucesso. Ate la, eles
devolvem uma lista vazia (nao erro) -- o frontend deve tratar isso mostrando
"sem dados no periodo" em vez de quebrar.
"""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import oracledb
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

app = FastAPI(title="Painel Executivo - API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Auth ----------

_DIR = Path(__file__).parent
USUARIOS_PATH = _DIR / "usuarios.json"
SESSOES: dict = {}  # token -> {usuario, perfil, expira}
SESSAO_HORAS = 8
bearer = HTTPBearer(auto_error=False)


def _usuarios() -> dict:
    if not USUARIOS_PATH.exists():
        return {}
    return json.loads(USUARIOS_PATH.read_text(encoding="utf-8"))


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _validar_token(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    token = creds.token if creds else None
    if not token or token not in SESSOES:
        raise HTTPException(status_code=401, detail="Nao autenticado.")
    sessao = SESSOES[token]
    if datetime.utcnow() > sessao["expira"]:
        del SESSOES[token]
        raise HTTPException(status_code=401, detail="Sessao expirada.")
    return sessao


def _validar_token_header(authorization: str | None) -> dict:
    """Versao que aceita o header Authorization direto (sem Depends)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Nao autenticado.")
    token = authorization.split(" ", 1)[1]
    if token not in SESSOES:
        raise HTTPException(status_code=401, detail="Token invalido.")
    sessao = SESSOES[token]
    if datetime.utcnow() > sessao["expira"]:
        del SESSOES[token]
        raise HTTPException(status_code=401, detail="Sessao expirada.")
    return sessao


@app.post("/auth/login")
def login(username: str = Form(...), password: str = Form(...)):
    usuarios = _usuarios()
    user = usuarios.get(username)
    if not user or user["senha"] != _hash(password):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos.")
    token = secrets.token_urlsafe(32)
    SESSOES[token] = {
        "usuario": username,
        "nome": user["nome"],
        "perfil": user["perfil"],
        "expira": datetime.utcnow() + timedelta(hours=SESSAO_HORAS),
    }
    return {"token": token, "perfil": user["perfil"], "nome": user["nome"]}


@app.get("/auth/me")
def me(sessao=Depends(_validar_token)):
    return {"usuario": sessao["usuario"], "nome": sessao["nome"], "perfil": sessao["perfil"]}

@app.get("/tendencias")
def tendencias():
    f = _DIR / "painel-tendencias.html"
    if not f.exists(): raise HTTPException(status_code=404, detail="painel-tendencias.html nao encontrado")
    return FileResponse(str(f))

@app.get("/capacidade")
def capacidade():
    f = _DIR / "painel-capacidade.html"
    if not f.exists(): raise HTTPException(status_code=404, detail="painel-capacidade.html nao encontrado")
    return FileResponse(str(f))

@app.post("/auth/logout")
def logout(sessao=Depends(_validar_token), creds: HTTPAuthorizationCredentials = Depends(bearer)):
    SESSOES.pop(creds.token, None)
    return {"ok": True}


@app.post("/auth/register")
def register(username: str = Form(...), password: str = Form(...),
             nome: str = Form(...), perfil: str = Form(...)):
    # mapeia os valores que a landing manda para os valores internos
    mapa_perfil = {
        "executivo": "executivo",
        "analitico": "analitico",
        "leigo": "leigo",
        "Analítico": "analitico",
        "Financeiro": "executivo",
        "Padrão": "leigo",
    }
    perfil_interno = mapa_perfil.get(perfil)
    if not perfil_interno:
        raise HTTPException(status_code=400, detail=f"Perfil invalido: '{perfil}'. Use executivo, analitico ou leigo.")
    usuarios = _usuarios()
    if username in usuarios:
        raise HTTPException(status_code=400, detail="Usuário já existe.")
    usuarios[username] = {"nome": nome, "senha": _hash(password), "perfil": perfil_interno}
    USUARIOS_PATH.write_text(json.dumps(usuarios, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


def get_conn():
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def query(sql: str, params: dict = None) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        import logging
        print(f"ERRO SQL: {e} | SQL: {sql[:200]}")
        logging.getLogger("painel").error("Erro SQL: %s | SQL: %s", e, sql[:200])
        return []
    finally:
        conn.close()


@app.get("/api/kpis")
def get_kpis(competencia: str | None = None, sexo: str | None = None,
             municipio: str | None = None, faixa_etaria: str | None = None,
             uf: str | None = None):
    conds = ["h.POSSUI_ATEND_HOSPITALAR = 1"]
    params = {}
    if competencia:  conds.append("f.COMPETENCIA = :comp");   params["comp"] = competencia
    if sexo:         conds.append("f.SEXO = :sexo");           params["sexo"] = sexo
    if faixa_etaria: conds.append("f.FAIXA_ETARIA = :faixa"); params["faixa"] = faixa_etaria
    if uf:           conds.append("m.UF = :uf");               params["uf"] = uf
    where = "WHERE " + " AND ".join(conds)
    sql = f"""
        SELECT
            NVL(SUM(f.VALOR_TOTAL), 0)      AS custo_total,
            COUNT(*)                         AS total_internacoes,
            NVL(AVG(f.VALOR_TOTAL), 0)      AS custo_medio,
            NVL(AVG(f.DIAS_PERMANENCIA), 0) AS permanencia_media,
            COUNT(DISTINCT f.COD_IBGE)       AS municipios_distintos
        FROM FATO_INTERNACAO f
        JOIN DIM_HOSPITAL h  ON f.COD_CNES = h.COD_CNES
        JOIN DIM_MUNICIPIO m ON f.COD_IBGE  = m.COD_IBGE
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
def custo_por_mes(uf: str | None = None):
    conds = ["h.POSSUI_ATEND_HOSPITALAR = 1"]
    params = {}
    if uf: conds.append("m.UF = :uf"); params["uf"] = uf
    where = "WHERE " + " AND ".join(conds)
    sql = f"""
        SELECT f.COMPETENCIA AS competencia,
               SUM(f.VALOR_TOTAL)      AS custo,
               COUNT(*)                AS internacoes,
               AVG(f.DIAS_PERMANENCIA) AS permanencia_media
        FROM FATO_INTERNACAO f
        JOIN DIM_HOSPITAL h  ON f.COD_CNES = h.COD_CNES
        JOIN DIM_MUNICIPIO m ON f.COD_IBGE  = m.COD_IBGE
        {where}
        GROUP BY f.COMPETENCIA ORDER BY f.COMPETENCIA
    """
    linhas = query(sql, params)
    return [{"competencia":r["competencia"],"custo":float(r["custo"] or 0),
             "internacoes":int(r["internacoes"] or 0),"permanencia_media":float(r["permanencia_media"] or 0)}
            for r in linhas]


@app.get("/api/custo-por-hospital")
def custo_por_hospital(limite: int = 5):
    sql = """
        SELECT h.NOME AS hospital, m.NOME AS municipio,
               COUNT(*) AS internacoes, SUM(f.VALOR_TOTAL) AS custo
        FROM FATO_INTERNACAO f
        JOIN DIM_HOSPITAL h ON f.COD_CNES = h.COD_CNES
        JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE
        WHERE h.POSSUI_ATEND_HOSPITALAR = 1
        GROUP BY h.NOME, m.NOME
        ORDER BY SUM(f.VALOR_TOTAL) DESC
        FETCH FIRST :limite ROWS ONLY
    """
    linhas = query(sql, {"limite": limite})
    if not linhas: return []
    maior_custo = max(float(r["custo"] or 0) for r in linhas)
    return [{"hospital":r["hospital"],"municipio":r["municipio"],"internacoes":int(r["internacoes"] or 0),
             "custo":float(r["custo"] or 0),
             "pct":round((float(r["custo"] or 0)/maior_custo)*100,1) if maior_custo else 0}
            for r in linhas]


@app.get("/api/hospitais/mapa")
def hospitais_mapa():
    """Retorna hospitais reais (com atendimento hospitalar) para o mapa da landing.
    Cruza DIM_HOSPITAL com FATO_INTERNACAO para trazer internacoes e custo reais."""
    sql = """
        SELECT
            h.NOME                              AS nome,
            m.NOME                              AS municipio,
            h.NATUREZA                          AS natureza,
            h.ESFERA_ADM                        AS esfera,
            h.FAZ_ATENDIMENTO_SUS               AS sus,
            NVL(f.internacoes, 0)               AS internacoes,
            NVL(f.custo, 0)                     AS custo
        FROM DIM_HOSPITAL h
        JOIN DIM_MUNICIPIO m ON h.COD_IBGE = m.COD_IBGE
        LEFT JOIN (
            SELECT COD_CNES,
                   COUNT(*)          AS internacoes,
                   SUM(VALOR_TOTAL)  AS custo
            FROM FATO_INTERNACAO
            GROUP BY COD_CNES
        ) f ON f.COD_CNES = h.COD_CNES
        WHERE h.POSSUI_ATEND_HOSPITALAR = 1
        ORDER BY NVL(f.internacoes, 0) DESC
        FETCH FIRST 3000 ROWS ONLY
    """
    linhas = query(sql)
    return [
        {
            "nome":        r["nome"],
            "municipio":   r["municipio"],
            "natureza":    r["natureza"],
            "esfera":      r["esfera"],
            "sus":         r["sus"],
            "internacoes": int(r["internacoes"] or 0),
            "custo":       float(r["custo"] or 0),
        }
        for r in linhas
    ]
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
def listar_competencias(uf: str | None = None):
    params = {}
    where = ""
    if uf:
        where = "JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE WHERE m.UF = :uf"
        params["uf"] = uf
    sql = f"SELECT DISTINCT f.COMPETENCIA AS competencia FROM FATO_INTERNACAO f {where} ORDER BY f.COMPETENCIA"
    return [r["competencia"] for r in query(sql, params)]


@app.get("/api/ufs")
def listar_ufs():
    sql = "SELECT DISTINCT UF FROM DIM_MUNICIPIO WHERE UF IS NOT NULL ORDER BY UF"
    return [r["uf"] for r in query(sql)]


@app.get("/api/analise/sexo")
def analise_sexo(competencia: str | None = None, sexo: str | None = None,
                 municipio: str | None = None, faixa_etaria: str | None = None,
                 uf: str | None = None):
    conds, params = [], {}
    if competencia:  conds.append("f.COMPETENCIA = :comp");    params["comp"] = competencia
    if sexo:         conds.append("f.SEXO = :sexo");           params["sexo"] = sexo
    if faixa_etaria: conds.append("f.FAIXA_ETARIA = :faixa"); params["faixa"] = faixa_etaria
    if municipio:    conds.append("UPPER(m.NOME) LIKE UPPER(:mun)"); params["mun"] = f"%{municipio}%"
    if uf:           conds.append("m.UF = :uf");               params["uf"] = uf
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
                         municipio: str | None = None, faixa_etaria: str | None = None,
                         uf: str | None = None):
    conds, params = [], {}
    if competencia: conds.append("f.COMPETENCIA = :comp"); params["comp"] = competencia
    if sexo:        conds.append("f.SEXO = :sexo");        params["sexo"] = sexo
    if faixa_etaria: conds.append("f.FAIXA_ETARIA = :faixa"); params["faixa"] = faixa_etaria
    if municipio:
        conds.append("UPPER(m.NOME) LIKE UPPER(:mun)"); params["mun"] = f"%{municipio}%"
    if uf: conds.append("m.UF = :uf"); params["uf"] = uf
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
                           sexo: str | None = None, faixa_etaria: str | None = None,
                           uf: str | None = None):
    conds, params = [], {"limite": limite}
    if competencia:  conds.append("f.COMPETENCIA = :comp");  params["comp"] = competencia
    if sexo:         conds.append("f.SEXO = :sexo");          params["sexo"] = sexo
    if faixa_etaria: conds.append("f.FAIXA_ETARIA = :faixa"); params["faixa"] = faixa_etaria
    if uf: conds.append("m.UF = :uf"); params["uf"] = uf
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


from pathlib import Path
from fastapi.responses import FileResponse

_DIR = Path(__file__).parent

@app.get("/login")
def pagina_login():
    f = _DIR / "login.html"
    if not f.exists(): raise HTTPException(status_code=404, detail="login.html nao encontrado")
    return FileResponse(str(f))

@app.get("/landing")
def landing():
    f = _DIR / "landing.html"
    if not f.exists(): raise HTTPException(status_code=404, detail="landing.html nao encontrado")
    return FileResponse(str(f))

@app.get("/")
def raiz():
    """Redireciona para a landing page."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/landing")

@app.get("/dashboard")
def dashboard():
    f = _DIR / "painel-executivo-hospitalar.html"
    if not f.exists(): raise HTTPException(status_code=404, detail=f"Arquivo nao encontrado: {f}")
    return FileResponse(str(f))

@app.get("/analitico")
def dashboard_analitico():
    f = _DIR / "painel-analitico-hospitalar.html"
    if not f.exists(): raise HTTPException(status_code=404, detail=f"Arquivo nao encontrado: {f}")
    return FileResponse(str(f))

@app.get("/comparativos")
def pagina_comparativos():
    f = _DIR / "painel-comparativos.html"
    if not f.exists(): raise HTTPException(status_code=404, detail="painel-comparativos.html nao encontrado")
    return FileResponse(str(f))

@app.get("/relatorios")
def pagina_relatorios():
    f = _DIR / "painel-relatorios.html"
    if not f.exists(): raise HTTPException(status_code=404, detail="painel-relatorios.html nao encontrado")
    return FileResponse(str(f))

# --- Relatórios por usuário ---
import time as _time

RELATORIOS_PATH = _DIR / "relatorios.json"

def _ler_relatorios() -> dict:
    if not RELATORIOS_PATH.exists(): return {}
    try: return json.loads(RELATORIOS_PATH.read_text(encoding="utf-8"))
    except: return {}

def _salvar_relatorios(dados: dict):
    RELATORIOS_PATH.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")

@app.get("/api/relatorios")
def listar_relatorios(token: str | None = None):
    if not token or token not in SESSOES:
        raise HTTPException(status_code=401, detail="Nao autenticado.")
    sessao = SESSOES[token]
    if datetime.utcnow() > sessao["expira"]:
        raise HTTPException(status_code=401, detail="Sessao expirada.")
    return _ler_relatorios().get(sessao["usuario"], [])

class RelatorioBody(BaseModel):
    titulo: str
    descricao: str = ""
    campos: dict

@app.post("/api/relatorios")
def criar_relatorio(body: RelatorioBody, token: str | None = None):
    if not token or token not in SESSOES:
        raise HTTPException(status_code=401, detail="Nao autenticado.")
    sessao = SESSOES[token]
    if datetime.utcnow() > sessao["expira"]:
        raise HTTPException(status_code=401, detail="Sessao expirada.")
    usuario = sessao["usuario"]
    todos = _ler_relatorios()
    lista = todos.get(usuario, [])
    novo = {"id": str(int(_time.time()*1000)), "titulo": body.titulo, "descricao": body.descricao,
            "campos": body.campos, "criado_em": _time.strftime("%d/%m/%Y %H:%M")}
    lista.append(novo)
    todos[usuario] = lista
    _salvar_relatorios(todos)
    return novo

@app.delete("/api/relatorios/{relatorio_id}")
def excluir_relatorio(relatorio_id: str, token: str | None = None):
    if not token or token not in SESSOES:
        raise HTTPException(status_code=401, detail="Nao autenticado.")
    sessao = SESSOES[token]
    if datetime.utcnow() > sessao["expira"]:
        raise HTTPException(status_code=401, detail="Sessao expirada.")
    usuario = sessao["usuario"]
    todos = _ler_relatorios()
    todos[usuario] = [r for r in todos.get(usuario, []) if r["id"] != relatorio_id]
    _salvar_relatorios(todos)
    return {"ok": True}

# --- Comparativos ---
@app.get("/api/comparativos")
def comparativos(dim: str = "municipio", top: int = 10):  # noqa
    if dim not in ("municipio","hospital","faixa_etaria","competencia"):
        dim = "municipio"

    if dim == "municipio":
        sql = """
            SELECT NVL(m.NOME, TO_CHAR(f.COD_IBGE)) AS label,
                   COUNT(*) AS internacoes,
                   SUM(f.VALOR_TOTAL) AS custo,
                   AVG(f.DIAS_PERMANENCIA) AS permanencia
            FROM FATO_INTERNACAO f
            LEFT JOIN DIM_MUNICIPIO m ON f.COD_IBGE = m.COD_IBGE
            GROUP BY NVL(m.NOME, TO_CHAR(f.COD_IBGE))
            ORDER BY COUNT(*) DESC
            FETCH FIRST :top ROWS ONLY
        """
    elif dim == "hospital":
        sql = """
            SELECT NVL(h.NOME, TO_CHAR(f.COD_CNES)) AS label,
                   COUNT(*) AS internacoes,
                   SUM(f.VALOR_TOTAL) AS custo,
                   AVG(f.DIAS_PERMANENCIA) AS permanencia
            FROM FATO_INTERNACAO f
            LEFT JOIN DIM_HOSPITAL h ON f.COD_CNES = h.COD_CNES
            GROUP BY NVL(h.NOME, TO_CHAR(f.COD_CNES))
            ORDER BY COUNT(*) DESC
            FETCH FIRST :top ROWS ONLY
        """
    elif dim == "faixa_etaria":
        sql = """
            SELECT NVL(f.FAIXA_ETARIA, 'N/D') AS label,
                   COUNT(*) AS internacoes,
                   SUM(f.VALOR_TOTAL) AS custo,
                   AVG(f.DIAS_PERMANENCIA) AS permanencia
            FROM FATO_INTERNACAO f
            GROUP BY NVL(f.FAIXA_ETARIA, 'N/D')
            ORDER BY COUNT(*) DESC
            FETCH FIRST :top ROWS ONLY
        """
    else:  # competencia
        sql = """
            SELECT f.COMPETENCIA AS label, COUNT(*) AS internacoes,
                   SUM(f.VALOR_TOTAL) AS custo, AVG(f.DIAS_PERMANENCIA) AS permanencia
            FROM FATO_INTERNACAO f
            JOIN DIM_HOSPITAL h ON f.COD_CNES = h.COD_CNES
            GROUP BY f.COMPETENCIA ORDER BY f.COMPETENCIA
            FETCH FIRST :top ROWS ONLY
        """
    linhas = query(sql, {"top": top})
    return [{"label":r["label"],"internacoes":int(r["internacoes"] or 0),
             "custo":float(r["custo"] or 0),"permanencia":float(r["permanencia"] or 0)}
            for r in linhas]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)