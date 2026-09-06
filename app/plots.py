import uuid
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from .config import PLOTS_DIR

GREEN = "#2E7D32"
GREEN_LIGHT = "#A5D6A7"
TOP_N_CATEGORIAS = 15

# Paleta do cartao escuro (bate com o tema do chat)
CARD_BG = "#30302e"
CARD_BLUE = "#4C8BF5"
CARD_TEXT = "#f2f0ea"
CARD_SUBTEXT = "#9a9587"
CARD_GRID = "#3a3935"


def _norm_col(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", str(s)).encode("ascii","ignore").decode().lower().strip()

# Colunas numéricas que JÁ contêm valores agregados — usar diretamente
_NUMERIC_AGG_COLS = {"internacoes","custo","custo_total","valor_total","perm_media",
                     "permanencia","custo_medio","total_internacoes","qtd","quantidade"}

def prepare_plot_data(df: pd.DataFrame, x: str, y, agg: str = "count", is_temporal_ok: bool = False) -> pd.DataFrame:
    """
    Retorna DataFrame com colunas 'categoria' e 'valor'.
    Detecta se os dados já estão agregados (ex: MV_MUNICIPIO tem INTERNACOES pronto)
    e usa diretamente sem fazer COUNT errado.
    """
    if df is None or df.empty or x not in df.columns:
        return pd.DataFrame({"categoria": [], "valor": []})

    tmp = df.copy()

    # --- Detectar coluna de valor numérico ---
    # Prioridade: y explícito > coluna numérica conhecida > primeira numérica
    val_col = None
    if y and y in tmp.columns and pd.api.types.is_numeric_dtype(tmp[y]):
        val_col = y
    else:
        # Busca coluna numérica conhecida (INTERNACOES, CUSTO_TOTAL, etc.)
        for col in tmp.columns:
            if _norm_col(col) in _NUMERIC_AGG_COLS and pd.api.types.is_numeric_dtype(tmp[col]):
                val_col = col
                break
        # Fallback: primeira coluna numérica que não seja o X
        if not val_col:
            for col in tmp.columns:
                if col != x and pd.api.types.is_numeric_dtype(tmp[col]):
                    val_col = col
                    break

    # --- Temporal (ex: COMPETENCIA) ---
    if is_temporal_ok:
        # COMPETENCIA no formato AAAAMM — ordenar cronologicamente
        if _norm_col(x) in {"competencia"}:
            tmp = tmp.copy()
            tmp["_cat"] = tmp[x].astype(str)
            tmp = tmp.sort_values("_cat")
            if val_col:
                result = tmp[["_cat", val_col]].copy()
                result.columns = ["categoria", "valor"]
            else:
                result = tmp[["_cat"]].copy()
                result["valor"] = 1
                result.columns = ["categoria", "valor"]
            return result.reset_index(drop=True)

    # --- Dados já agregados (uma linha por categoria, ex: MV_MUNICIPIO) ---
    # Detecta se x é coluna de categoria + já tem coluna de valor
    if val_col and x in tmp.columns:
        # Verifica se há duplicatas no X — se não houver, dados já estão agregados
        if tmp[x].nunique() == len(tmp) or tmp[x].nunique() >= len(tmp) * 0.8:
            # Dados já agregados — usa diretamente
            result = tmp[[x, val_col]].copy()
            result.columns = ["categoria", "valor"]
            result["valor"] = pd.to_numeric(result["valor"], errors="coerce").fillna(0)
            result = result.sort_values("valor", ascending=False).head(TOP_N_CATEGORIAS)
            return result.reset_index(drop=True)
        else:
            # Dados brutos — agrega
            grouped = tmp.groupby(x)[val_col]
            if agg == "mean":
                series = grouped.mean().round(2)
            else:
                series = grouped.sum()
            series = series.sort_values(ascending=False).head(TOP_N_CATEGORIAS)
            return pd.DataFrame({"categoria": series.index.astype(str), "valor": series.values})
    else:
        # Sem coluna de valor — count por categoria
        series = tmp[x].value_counts().head(TOP_N_CATEGORIAS)
        return pd.DataFrame({"categoria": series.index.astype(str), "valor": series.values})


def _style_ax(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.tick_params(colors="#555555")
    ax.grid(axis="y", color="#EEEEEE", linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig, facecolor="white") -> str:
    filename = f"{uuid.uuid4().hex}.png"
    path = PLOTS_DIR / filename
    fig.tight_layout()
    fig.savefig(path, dpi=140, facecolor=facecolor)
    plt.close(fig)
    return str(path)


def bar_plot(df: pd.DataFrame, x: str, y: str, title: str = "", subtitle: str = "") -> str:
    """Grafico de barras no estilo cartao escuro (titulo + subtitulo + barras azuis)."""
    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    valores = df[y]
    bars = ax.bar(df[x], valores, color=CARD_BLUE, width=0.55, zorder=3)

    max_v = float(valores.max()) if len(valores) else 0
    for bar, val in zip(bars, valores):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max_v * 0.02,
            f"{val:g}",
            ha="center", va="bottom",
            color="#d8d8d3", fontsize=9,
        )

    if title:
        ax.set_title(title, loc="left", color=CARD_TEXT, fontsize=13.5, fontweight="bold", pad=22)
    if subtitle:
        ax.text(0, 1.055, subtitle, transform=ax.transAxes, color=CARD_SUBTEXT, fontsize=10)

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=CARD_SUBTEXT, labelsize=9.5)
    ax.yaxis.grid(True, color=CARD_GRID, linewidth=0.8, zorder=0)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.set_ylim(bottom=0, top=max_v * 1.18 if max_v else 1)
    plt.xticks(rotation=20, ha="right")

    return _save(fig, facecolor=CARD_BG)


def table_image(df: pd.DataFrame, x_label: str = "Categoria", y_label: str = "Valor",
                 title: str = "", subtitle: str = "") -> str:
    """Renderiza os mesmos dados do bar_plot como uma tabela, no mesmo estilo de cartao escuro."""
    rows = df.reset_index(drop=True)
    n = max(len(rows), 1)
    height = 1.5 + n * 0.42
    fig, ax = plt.subplots(figsize=(7.2, height))
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)
    ax.axis("off")

    top = 1.0
    if title:
        ax.text(0.01, top, title, transform=ax.transAxes, color=CARD_TEXT,
                 fontsize=13.5, fontweight="bold", va="top")
        top -= 0.09
    if subtitle:
        ax.text(0.01, top, subtitle, transform=ax.transAxes, color=CARD_SUBTEXT,
                 fontsize=10, va="top")
        top -= 0.10

    header_y = top - 0.04
    ax.text(0.01, header_y, x_label, transform=ax.transAxes, color=CARD_SUBTEXT,
             fontsize=10.5, fontweight="bold", va="top")
    ax.text(0.58, header_y, y_label, transform=ax.transAxes, color=CARD_SUBTEXT,
             fontsize=10.5, fontweight="bold", va="top")
    ax.axhline(header_y - 0.035, color=CARD_GRID, linewidth=0.9, xmin=0.01, xmax=0.99)

    step = (header_y - 0.035) / (n + 1)
    y = header_y - 0.035 - step
    for _, row in rows.iterrows():
        ax.text(0.01, y, str(row["categoria"]), transform=ax.transAxes,
                 color="#e8e6e0", fontsize=10.5, va="top")
        val = row["valor"]
        val_str = f"{val:g}" if isinstance(val, (int, float)) else str(val)
        ax.text(0.58, y, val_str, transform=ax.transAxes, color="#e8e6e0",
                 fontsize=10.5, va="top")
        y -= step

    return _save(fig, facecolor=CARD_BG)


def line_plot(df: pd.DataFrame, x: str, y: str, title: str = "") -> str:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(df[x], df[y], color=GREEN, linewidth=2, marker="o", markersize=3)
    ax.set_title(title, color="#1B2C5E", fontsize=12)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    _style_ax(ax)
    plt.xticks(rotation=45, ha="right")
    return _save(fig)


def hist_plot(df: pd.DataFrame, col: str, bins: int = 20, title: str = "") -> str:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(df[col].dropna(), bins=bins, color=GREEN_LIGHT, edgecolor=GREEN)
    ax.set_title(title or f"Distribuicao de {col}", color="#371B5E", fontsize=12)
    ax.set_xlabel(col)
    ax.set_ylabel("Frequencia")
    _style_ax(ax)
    return _save(fig)


def scatter_plot(df: pd.DataFrame, x: str, y: str, title: str = "") -> str:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(df[x], df[y], color=GREEN, alpha=0.7, s=25)
    ax.set_title(title, color="#351B5E", fontsize=12)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    _style_ax(ax)
    return _save(fig)