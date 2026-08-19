import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from .config import UPLOADS_DIR
from . import database as db

_vectorizers = {}
_matrices = {}
_chunks_cache = {}


def _df_path(chat_id: int):
    return UPLOADS_DIR / f"chat_{chat_id}.csv"


def save_dataframe(chat_id: int, df: pd.DataFrame):
    df.to_csv(_df_path(chat_id), index=False)


def load_dataframe(chat_id: int):
    path = _df_path(chat_id)
    if path.exists():
        return pd.read_csv(path)
    return None


def build_chunks_from_df(df: pd.DataFrame) -> list:
    chunks = [f"Colunas do dataset: {', '.join(df.columns)}."]
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_numeric_dtype(series):
            desc = (
                f"Coluna '{col}' (numerica): min={series.min()}, max={series.max()}, "
                f"media={series.mean():.2f}, mediana={series.median():.2f}, "
                f"desvio_padrao={series.std():.2f}, nulos={series.isna().sum()}."
            )
        else:
            top = series.value_counts().head(5)
            top_str = ", ".join(f"{k} ({v})" for k, v in top.items())
            desc = f"Coluna '{col}' (categorica): valores_mais_frequentes=[{top_str}], nulos={series.isna().sum()}."
        chunks.append(desc)
    chunks.append(f"Total de linhas: {len(df)}.")
    chunks.append(f"Amostra das 10 primeiras linhas:\n{df.head(10).to_string(index=False)}")
    return chunks


def index_dataframe(chat_id: int, df: pd.DataFrame):
    save_dataframe(chat_id, df)
    db.clear_rag(chat_id)
    chunks = build_chunks_from_df(df)
    db.add_rag_chunks(chat_id, chunks)
    _refresh_index(chat_id)


def _refresh_index(chat_id: int):
    chunks = db.get_rag_chunks(chat_id)
    if not chunks:
        _vectorizers.pop(chat_id, None)
        _matrices.pop(chat_id, None)
        _chunks_cache.pop(chat_id, None)
        return
    vec = TfidfVectorizer(max_features=2000)
    matrix = vec.fit_transform(chunks)
    _vectorizers[chat_id] = vec
    _matrices[chat_id] = matrix
    _chunks_cache[chat_id] = chunks


def retrieve(chat_id: int, query: str, top_k: int = 3) -> list:
    if chat_id not in _vectorizers:
        _refresh_index(chat_id)
    if chat_id not in _vectorizers:
        return []
    vec = _vectorizers[chat_id]
    matrix = _matrices[chat_id]
    chunks = _chunks_cache[chat_id]
    q_vec = vec.transform([query])
    sims = cosine_similarity(q_vec, matrix).flatten()
    top_idx = sims.argsort()[::-1][:top_k]
    return [chunks[i][:600] for i in top_idx if sims[i] > 0]
