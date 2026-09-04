# sync_sih_sus.py
import asyncio
import logging
import os

import oracledb
import pandas as pd
from pysus.api import PySUSClient
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sync_sih_sus")

load_dotenv()

UF = "SP"
ANO = 2024
COMPETENCIAS = [f"{ANO}{mes:02d}" for mes in range(1, 13)]

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

MAPA_SEXO = {"1": "M", "3": "F"}
uf_atual = "SP"  # atualizado no loop


def competencia_ja_processada(conn, competencia: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM SYNC_LOG
        WHERE SISTEMA = 'SIH-SUS' AND UF = :uf
          AND COMPETENCIA = :comp AND STATUS = 'SUCESSO'
        """,
        uf=UF, comp=competencia,
    )
    (total,) = cur.fetchone()
    return total > 0


def registrar_sync(conn, competencia: str, status: str, linhas: int, mensagem: str = None):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO SYNC_LOG (SISTEMA, UF, COMPETENCIA, STATUS, LINHAS_PROCESSADAS, MENSAGEM)
        VALUES ('SIH-SUS', :uf, :comp, :status, :linhas, :msg)
        """,
        uf=UF, comp=competencia, status=status, linhas=linhas, msg=mensagem,
    )
    conn.commit()


async def _listar_arquivos_rd_async():
    """Busca UMA VEZ todos os arquivos SIH de SP e filtra o grupo RD em Python."""
    async with PySUSClient() as client:
        todos = await client.query(dataset="sih", state=UF)
        return [a for a in todos if "RD" in a.path.parts]


async def _baixar_competencia_async(client, arquivo):
    parquet = await client.download_to_parquet(arquivo)
    return parquet.path


def listar_arquivos_rd():
    return asyncio.run(_listar_arquivos_rd_async())


async def _baixar_varios_async(arquivos):
    async with PySUSClient() as client:
        caminhos = []
        for arquivo in arquivos:
            parquet = await client.download_to_parquet(arquivo)
            caminhos.append(parquet.path)
        if not caminhos:
            return pd.DataFrame()

        resultado = client.read_parquet(caminhos, mode="union")
        if isinstance(resultado, pd.DataFrame):
            return resultado
        # veio como DuckDBPyConnection em vez de DataFrame -- converte
        return resultado.df()


    async with PySUSClient() as client:
        caminhos = []
        for arquivo in arquivos:
            parquet = await client.download_to_parquet(arquivo)
            caminhos.append(parquet.path)
        if not caminhos:
            return pd.DataFrame()
        # PySUSClient.read_parquet nao e async
        return client.read_parquet(caminhos, mode="union")


def baixar_competencia(arquivos_rd, ano: int, mes: int) -> pd.DataFrame:
    ano_str = str(ano)
    mes_str = f"{mes:02d}"
    alvo = [a for a in arquivos_rd if ano_str in a.path.parts and mes_str in a.path.parts]
    if not alvo:
        return pd.DataFrame()
    df = asyncio.run(_baixar_varios_async(alvo))
    return df if df is not None else pd.DataFrame()


def transformar(df: pd.DataFrame, competencia: str) -> pd.DataFrame:
    if df.empty:
        return df

    out = pd.DataFrame()
    out["AIH_ORIGINAL"] = df["N_AIH"].astype(str)
    out["COD_CNES"] = pd.to_numeric(df["CNES"], errors="coerce")
    out["COD_IBGE"] = pd.to_numeric(df["MUNIC_MOV"], errors="coerce")
    out["COD_PROCEDIMENTO"] = df["PROC_REA"].astype(str)
    out["COMPETENCIA"] = competencia
    out["DATA_INTERNACAO"] = pd.to_datetime(df["DT_INTER"], format="%Y%m%d", errors="coerce")
    out["DATA_SAIDA"] = pd.to_datetime(df["DT_SAIDA"], format="%Y%m%d", errors="coerce")
    out["DIAS_PERMANENCIA"] = pd.to_numeric(df.get("DIAS_PERM"), errors="coerce")
    out["VALOR_TOTAL"] = pd.to_numeric(df.get("VAL_TOT"), errors="coerce")
    out["SEXO"] = df["SEXO"].astype(str).map(MAPA_SEXO).fillna("?")
    out["CARATER_INTERNACAO"] = df.get("CAR_INT", "").astype(str)

    idade = pd.to_numeric(df.get("IDADE"), errors="coerce")
    out["FAIXA_ETARIA"] = pd.cut(
        idade,
        bins=[-1, 1, 4, 12, 18, 30, 45, 60, 200],
        labels=["<1", "1-4", "5-12", "13-18", "19-30", "31-45", "46-60", "60+"],
    ).astype(str)

    out = out.dropna(subset=["COD_CNES", "COD_IBGE"])
    # uma mesma AIH pode ter mais de uma linha no SIH-SUS bruto (procedimentos
    # complementares); mantemos so a de maior valor, que normalmente e a linha
    # principal da internacao -- evita violar a unicidade de AIH_ORIGINAL
    out = out.sort_values("VALOR_TOTAL", ascending=False).drop_duplicates(subset=["AIH_ORIGINAL"], keep="first")
    return out


def carregar_staging(conn, df: pd.DataFrame):
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE STG_INTERNACAO")

    cols = list(df.columns)
    placeholders = ", ".join(f":{i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO STG_INTERNACAO ({', '.join(cols)}) VALUES ({placeholders})"

    dados = [tuple(row) for row in df[cols].itertuples(index=False, name=None)]
    cur.executemany(sql, dados)
    conn.commit()


def merge_staging_para_fato(conn):
    cur = conn.cursor()
    cur.execute(
        """
        MERGE INTO FATO_INTERNACAO f
        USING STG_INTERNACAO s
        ON (f.AIH_ORIGINAL = s.AIH_ORIGINAL)
        WHEN NOT MATCHED THEN
        INSERT (COD_CNES, COD_IBGE, COD_PROCEDIMENTO, COMPETENCIA,
                DATA_INTERNACAO, DATA_SAIDA, DIAS_PERMANENCIA, VALOR_TOTAL,
                FAIXA_ETARIA, SEXO, CARATER_INTERNACAO, AIH_ORIGINAL)
        VALUES (s.COD_CNES, s.COD_IBGE, s.COD_PROCEDIMENTO, s.COMPETENCIA,
                s.DATA_INTERNACAO, s.DATA_SAIDA, s.DIAS_PERMANENCIA, s.VALOR_TOTAL,
                s.FAIXA_ETARIA, s.SEXO, s.CARATER_INTERNACAO, s.AIH_ORIGINAL)
        """
    )
    conn.commit()
    return cur.rowcount

def garantir_procedimentos_existem(conn):
    """Cria um registro placeholder em DIM_PROCEDIMENTO para qualquer codigo que
    aparece na carga mas nao existe na dimensao -- comum quando a competencia
    da internacao e anterior a tabela SIGTAP mais recente que carregamos,
    ja que codigos de procedimento mudam/saem de uso ao longo do tempo."""
    cur = conn.cursor()
    cur.execute(
        """
        MERGE INTO DIM_PROCEDIMENTO d
        USING (
            SELECT DISTINCT COD_PROCEDIMENTO FROM STG_INTERNACAO
            WHERE COD_PROCEDIMENTO IS NOT NULL
        ) s
        ON (d.COD_PROCEDIMENTO = s.COD_PROCEDIMENTO)
        WHEN NOT MATCHED THEN
        INSERT (COD_PROCEDIMENTO, DESCRICAO, GRUPO)
        VALUES (s.COD_PROCEDIMENTO, 'Procedimento nao catalogado na SIGTAP atual', 'Desconhecido')
        """
    )
    conn.commit()

def main():
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)

    logger.info("Listando arquivos do grupo RD para %s (uma vez, reaproveitado nos 12 meses)...", UF)
    arquivos_rd = listar_arquivos_rd()
    logger.info("Total de arquivos RD encontrados para %s: %d", UF, len(arquivos_rd))

    for competencia in COMPETENCIAS:
        if competencia_ja_processada(conn, competencia):
            logger.info("Competencia %s ja processada, pulando.", competencia)
            continue

        mes = int(competencia[4:6])
        logger.info("Baixando SIH-SUS %s/%s...", UF, competencia)
        try:
            bruto = baixar_competencia(arquivos_rd, ANO, mes)
            if bruto.empty:
                logger.warning("Nenhum dado retornado para %s.", competencia)
                registrar_sync(conn, competencia, "SUCESSO", 0, "sem dados no periodo")
                continue

            transformado = transformar(bruto, competencia)
            carregar_staging(conn, transformado)
            garantir_procedimentos_existem(conn)
            linhas = merge_staging_para_fato(conn)

            registrar_sync(conn, competencia, "SUCESSO", linhas)
            logger.info("Competencia %s: %d linhas carregadas.", competencia, linhas)

        except Exception as e:
            logger.error("Falha na competencia %s: %s", competencia, e)
            registrar_sync(conn, competencia, "ERRO", 0, str(e)[:500])

    conn.close()


if __name__ == "__main__":
    main()