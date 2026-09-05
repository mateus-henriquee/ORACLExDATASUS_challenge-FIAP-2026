# llm.py — com roteador de intenção rápido (sem SQL dinâmico pesado)
import json
import re
import time
import unicodedata
from llama_cpp import Llama
from .config import MODEL_PATH, N_THREADS, N_CTX

_llm = None

TIME_BUDGET_SECONDS = 55
MAX_TOKENS_DEFAULT  = 350


def get_llm() -> Llama:
    global _llm
    if _llm is None:
        _llm = Llama(
            model_path=MODEL_PATH,
            n_ctx=N_CTX,
            n_threads=N_THREADS,
            n_batch=256,
            verbose=False,
        )
    return _llm


SYSTEM_PROMPT = (
    "Voce e uma IA especializada EXCLUSIVAMENTE em dados hospitalares do SIH-SUS do Brasil. "
    "Responda SEMPRE e SOMENTE em portugues do Brasil. Nunca escreva em ingles. "
    "Voce SO pode responder perguntas sobre: internacoes hospitalares, hospitais, municipios, "
    "custos do SUS, faixas etarias, competencias (meses), permanencia hospitalar e dados do DATASUS. "
    "Se a pergunta NAO for sobre esses dados hospitalares, responda: "
    "'Posso responder apenas perguntas sobre os dados de internacoes hospitalares do SUS. "
    "Tente perguntar sobre municipios, hospitais, custos ou internacoes.' "
    "Seja direto e tecnico, sem enrolacao, usando apenas o contexto de dados fornecido. "
    "Se o contexto nao tiver a resposta, diga que nao ha dados suficientes. "
    "NUNCA escreva codigo. NUNCA use markdown. Escreva somente em frases corridas de texto puro. "
    "Se um grafico ja foi gerado, apenas responda com os numeros que voce ja tem."
)


def build_messages(history: list, rag_context: list, question: str) -> list:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history[-4:]:
        role = "user" if h["role"] == "user" else "assistant"
        messages.append({"role": role, "content": h["content"]})
    if rag_context:
        ctx = "\n".join(f"- {c}" for c in rag_context)
        messages.append({"role": "system", "content": f"Contexto de dados relevante:\n{ctx}"})
    messages.append({"role": "user", "content": question})
    return messages


def generate(history: list, rag_context: list, question: str, max_tokens: int = MAX_TOKENS_DEFAULT) -> str:
    llm  = get_llm()
    msgs = build_messages(history, rag_context, question)
    start = time.time()
    parts = []
    stream = llm.create_chat_completion(messages=msgs, max_tokens=max_tokens, temperature=0.3, stream=True)
    for chunk in stream:
        delta = chunk["choices"][0].get("delta", {})
        piece = delta.get("content")
        if piece:
            parts.append(piece)
        if time.time() - start > TIME_BUDGET_SECONDS:
            parts.append("\n\n(Resposta cortada: limite de 1 minuto atingido.)")
            break
    answer = "".join(parts).strip()
    return answer or "Nao consegui gerar uma resposta a tempo. Tente reformular a pergunta de forma mais curta."


# ──────────────────────────────────────────────────────────────────────────────
#  ROTEADOR DE INTENÇÃO — substitui o generate_sql dinâmico pesado
#  Identifica palavras-chave e retorna a query certa na MV pré-calculada
# ──────────────────────────────────────────────────────────────────────────────

_INTENCOES = [
    {
        "nome": "kpis_gerais",
        "palavras": ["total", "geral", "resumo", "kpi", "quanto", "quantos", "quantas",
                     "custo total", "custo geral", "permanencia media", "media geral"],
        "sql": "SELECT * FROM MV_KPIS",
        "descricao": "KPIs gerais: total de internacoes, custo e permanencia media"
    },
    {
        "nome": "municipios",
        "palavras": ["municipio", "municipios", "cidade", "cidades", "regiao", "local",
                     "onde", "locais", "por cidade", "por municipio"],
        "sql": "SELECT * FROM MV_MUNICIPIO ORDER BY INTERNACOES DESC FETCH FIRST 20 ROWS ONLY",
        "descricao": "Internacoes e custos por municipio"
    },
    {
        "nome": "mes",
        "palavras": ["mes", "meses", "mensal", "competencia", "periodo", "tempo",
                     "evolucao", "tendencia", "historico", "por mes", "ao longo"],
        "sql": "SELECT * FROM MV_COMPETENCIA ORDER BY COMPETENCIA",
        "descricao": "Internacoes por competencia (mes)"
    },
    {
        "nome": "faixa_etaria",
        "palavras": ["faixa", "etaria", "idade", "idades", "crianca", "criancas",
                     "idoso", "idosos", "jovem", "jovens", "adulto", "adultos",
                     "anos", "grupo etario"],
        "sql": "SELECT * FROM MV_FAIXA_ETARIA ORDER BY INTERNACOES DESC",
        "descricao": "Internacoes por faixa etaria"
    },
    {
        "nome": "sexo",
        "palavras": ["sexo", "genero", "masculino", "feminino", "homem", "mulher",
                     "homens", "mulheres", "por sexo", "por genero"],
        "sql": "SELECT * FROM MV_SEXO ORDER BY INTERNACOES DESC",
        "descricao": "Internacoes por sexo"
    },
    {
        "nome": "hospitais",
        "palavras": ["hospital", "hospitais", "estabelecimento", "estabelecimentos",
                     "unidade", "unidades", "cnes", "por hospital"],
        "sql": "SELECT * FROM MV_HOSPITAL ORDER BY INTERNACOES DESC FETCH FIRST 20 ROWS ONLY",
        "descricao": "Internacoes por hospital"
    },
]


def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().strip()


def rotear_intencao(question: str) -> dict | None:
    """Retorna a intenção detectada {nome, sql, descricao} ou None se não identificar."""
    q = _normalizar(question)
    melhor = None
    melhor_score = 0
    for intencao in _INTENCOES:
        score = sum(1 for p in intencao["palavras"] if _normalizar(p) in q)
        if score > melhor_score:
            melhor_score = score
            melhor = intencao
    return melhor if melhor_score > 0 else None


# ──────────────────────────────────────────────────────────────────────────────
#  DETECÇÃO DE FILTROS ESPECÍFICOS — decide se usa MV ou generate_sql
# ──────────────────────────────────────────────────────────────────────────────

# Padrões que indicam filtro específico que as MVs não suportam
_FILTROS_ESPECIFICOS = [
    r"20\d{2}",          # ano: 2025, 2026
    r"\d{2}/20\d{2}",    # mes/ano: 01/2025
    r"janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro",
    r"jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez",
    r"primeiro semestre|segundo semestre|trimestre",
    r"especific|filtr|somente|apenas|so de|so em",
]

import re as _re

def tem_filtro_especifico(question: str) -> bool:
    """Retorna True se a pergunta tem filtro que as MVs não conseguem responder."""
    q = question.lower()
    for padrao in _FILTROS_ESPECIFICOS:
        if _re.search(padrao, q):
            return True
    return False


# Schema completo para generate_sql (FATO_INTERNACAO via subquery)
SCHEMA_DESCRICAO = """
Tabela disponivel (resultado ja com JOINs feitos, nomes em portugues):
  MUNICIPIO, HOSPITAL, COMPETENCIA (formato AAAAMM ex: 202601),
  SEXO (M ou F), FAIXA_ETARIA (<1 1-4 5-12 13-18 19-30 31-45 46-60 60+),
  DIAS_PERMANENCIA, VALOR_TOTAL
  A tabela se chama DADOS. Use: SELECT ... FROM DADOS GROUP BY ...

Regras:
- Para filtrar por ano use: WHERE COMPETENCIA LIKE '2026%'
- Para filtrar por mes/ano use: WHERE COMPETENCIA = '202601'
- Para contar internacoes use COUNT(*).
- Use FETCH FIRST N ROWS ONLY (nunca LIMIT).
- Sem acentos em aliases. Sem ponto e virgula no final.
- NUNCA use LIMIT. NUNCA use acentos em aliases.
"""


def generate_sql(question: str, max_retries: int = 2) -> str | None:
    """Gera SQL dinâmico quando MVs não conseguem responder (filtros específicos)."""
    llm = get_llm()
    prompt = (
        f"{SCHEMA_DESCRICAO}\n"
        f"Pergunta do usuario: {question}\n\n"
        "Escreva APENAS o SELECT SQL para responder essa pergunta, sem nenhum texto adicional, "
        "sem explicacao, sem markdown, sem ponto e virgula no final. "
        "Apenas o SELECT puro."
    )
    for tentativa in range(max_retries + 1):
        out = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.0 if tentativa == 0 else 0.2,
        )
        text = out["choices"][0]["message"]["content"].strip()
        match = _re.search(r"(?i)(SELECT\b.*?)(?:;|\Z)", text, _re.DOTALL)
        if match:
            sql = match.group(1).strip()
            sql = _re.sub(r"```[a-z]*\n?|```", "", sql).strip()
            if sql.upper().startswith("SELECT"):
                return sql
    return None


def suggest_plot_spec(columns: list, question: str):
    llm = get_llm()
    prompt = (
        "Dado o pedido do usuario e as colunas disponiveis, responda APENAS com um JSON "
        "no formato {\"type\": \"bar|line|hist|scatter\", \"x\": \"coluna\", "
        "\"y\": \"coluna_ou_null\", \"agg\": \"count|sum|mean\", "
        "\"title\": \"titulo curto em portugues do Brasil\"}. "
        "Use type=hist para distribuicao de uma coluna numerica. "
        "Use type=bar para comparar categorias. "
        "Use type=line quando x for data/periodo. "
        "Use type=scatter apenas quando x e y forem ambos numericos. "
        "IMPORTANTE: o valor de \"x\" e \"y\" deve ser EXATAMENTE um dos nomes de coluna listados. "
        "Nao escreva mais nada alem do JSON.\n"
        f"Colunas: {columns}\nPedido: {question}"
    )
    out = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150, temperature=0.0,
    )
    text = out["choices"][0]["message"]["content"].strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        spec = json.loads(match.group())
    except Exception:
        return None
    if spec.get("x"):
        spec["x"] = resolve_column(spec["x"], columns) or spec["x"]
    if spec.get("y"):
        spec["y"] = resolve_column(spec["y"], columns)
    mentioned = find_column_mentioned_in_question(question, columns)
    if mentioned:
        spec["x"] = mentioned
    return spec


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().strip()


def resolve_column(name: str, columns: list):
    if name in columns:
        return name
    norm_map = {_normalize(c): c for c in columns}
    n = _normalize(name)
    if n in norm_map:
        return norm_map[n]
    for norm_c, orig_c in norm_map.items():
        if len(norm_c) >= 4 and len(n) >= 4 and (n in norm_c or norm_c in n):
            return orig_c
    return None


def find_column_mentioned_in_question(question: str, columns: list):
    q = _normalize(question)
    best = None
    for c in columns:
        nc = _normalize(c)
        if nc and len(nc) >= 4 and nc in q:
            if best is None or len(nc) > len(_normalize(best)):
                best = c
    return best