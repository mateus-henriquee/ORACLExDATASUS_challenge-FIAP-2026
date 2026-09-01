import json
import re
import time
import unicodedata
from llama_cpp import Llama
from .config import MODEL_PATH, N_THREADS, N_CTX

_llm = None

TIME_BUDGET_SECONDS = 55
MAX_TOKENS_DEFAULT = 350


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
    "Voce e uma IA cientista de dados. "
    "Responda SEMPRE e SOMENTE em portugues do Brasil, mesmo que a pergunta esteja em "
    "outro idioma ou o contexto contenha texto em outro idioma. Nunca escreva em ingles. "
    "Seja direto e tecnico, sem enrolacao, usando apenas o contexto de dados (RAG) e o "
    "historico da conversa fornecidos. Se o contexto nao tiver a resposta, diga que nao ha "
    "dados suficientes. "
    "NUNCA escreva codigo (Python, JavaScript, SQL ou qualquer linguagem), blocos de codigo, "
    "ou instrucoes de como fazer algo em codigo. "
    "NUNCA use markdown: sem asterisco, sem #, sem listas com traço, sem titulos. "
    "Escreva somente em frases corridas de texto puro, como numa conversa falada. "
    "Se um grafico ja foi gerado, NAO explique como criar o grafico nem descreva os eixos "
    "em passos; apenas responda a pergunta do usuario com os numeros que voce ja tem."
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
    llm = get_llm()
    messages = build_messages(history, rag_context, question)
    start = time.time()
    parts = []

    stream = llm.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.3,
        stream=True,
    )
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


def suggest_plot_spec(columns: list, question: str):
    llm = get_llm()
    prompt = (
        "Dado o pedido do usuario e as colunas disponiveis, responda APENAS com um JSON "
        "no formato {\"type\": \"bar|line|hist|scatter\", \"x\": \"coluna\", "
        "\"y\": \"coluna_ou_null\", \"agg\": \"count|sum|mean\", "
        "\"title\": \"titulo curto em portugues do Brasil\"}. "
        "Use type=hist para distribuicao de uma coluna numerica. "
        "Use type=bar para comparar categorias (contagem ou soma/media de uma coluna numerica por categoria). "
        "Use type=line quando a coluna x for uma data/periodo. "
        "Use type=scatter apenas quando x e y forem ambos numericos. "
        "Se o pedido for so 'quantos por categoria', use agg=count e y=null. "
        "IMPORTANTE: o valor de \"x\" e \"y\" deve ser EXATAMENTE um dos nomes de coluna "
        "listados abaixo, letra por letra, sem traduzir ou parafrasear. "
        "Nao escreva mais nada alem do JSON.\n"
        f"Colunas: {columns}\nPedido: {question}"
    )
    out = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
        temperature=0.0,
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


# Schema do banco que o modelo usa pra gerar SQL.
SCHEMA_DESCRICAO = """
Tabela disponivel (resultado ja com JOINs feitos, nomes em portugues):
  MUNICIPIO         - nome do municipio
  HOSPITAL          - nome do hospital/estabelecimento
  COMPETENCIA       - mes de referencia no formato AAAAMM (ex: 202401)
  SEXO              - sexo do paciente: M ou F
  FAIXA_ETARIA      - faixa etaria: <1, 1-4, 5-12, 13-18, 19-30, 31-45, 46-60, 60+
  DIAS_PERMANENCIA  - numero de dias que o paciente ficou internado
  VALOR_TOTAL       - valor do reembolso pago pelo SUS por essa internacao

Regras:
- Para contar internacoes, use COUNT(*).
- Para calcular custo total, use SUM(VALOR_TOTAL).
- Para calcular media de permanencia, use AVG(DIAS_PERMANENCIA).
- Sempre use GROUP BY quando houver agregacao.
- Para limitar resultados, use FETCH FIRST N ROWS ONLY (sintaxe Oracle). NUNCA use LIMIT.
- NUNCA use acentos, cedilha ou caracteres especiais em aliases (AS INTERNACOES, nao AS INTERNAÇÕES).
- Nao use aspas duplas em nomes de coluna. Nao use ponto e virgula no final.
- A tabela se chama DADOS (alias interno). Use: SELECT ... FROM DADOS GROUP BY ...
- NUNCA use LIMIT. Para limitar, use FETCH FIRST N ROWS ONLY.
- NUNCA use acentos ou caracteres especiais em nomes de alias.
"""


def generate_sql(question: str, max_retries: int = 2) -> str | None:
    """Pede ao modelo que gere um SELECT SQL valido para responder a pergunta."""
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
        match = re.search(r"(?i)(SELECT\b.*?)(?:;|\Z)", text, re.DOTALL)
        if match:
            sql = match.group(1).strip()
            sql = re.sub(r"```[a-z]*\n?|```", "", sql).strip()
            if sql.upper().startswith("SELECT"):
                return sql
    return None