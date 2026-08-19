import json
import re
import time
from llama_cpp import Llama
from .config import MODEL_PATH, N_THREADS, N_CTX

_llm = None

TIME_BUDGET_SECONDS = 55   # corte real de tempo, com folga p/ ficar sob 1 min
MAX_TOKENS_DEFAULT = 350   # teto de tokens; o corte de tempo normalmente age antes


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
    """Gera a resposta via streaming e corta na marca, no maximo, de TIME_BUDGET_SECONDS."""
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
        return json.loads(match.group())
    except Exception:
        return None
