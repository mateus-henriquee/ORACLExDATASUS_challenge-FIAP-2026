import io
import os
import re

import oracledb
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
OCI_PAR_URL = os.getenv("OCI_PAR_URL")

NOME_ARQUIVO = "RDSP2402.parquet"       # arquivo bruto no Data Lake 
ARQUIVO_LOCAL_TRATADO = "RM569331.csv"

TABELA_STAGING = "STG_INTERNACAO_DATALAKE"
TABELA_FINAL = "FATO_INTERNACAO_DATALAKE"

MAPA_SEXO = {"1": "M", "3": "F"}


def extrair_competencia_do_nome(nome_arquivo: str) -> str:
    """RDSP2402.parquet -> '202402' (AAAAMM)"""
    m = re.search(r"(\d{4})\.parquet$", nome_arquivo)
    if not m:
        raise ValueError(f"Nao foi possivel extrair a competencia de: {nome_arquivo}")
    aamm = m.group(1)
    return f"20{aamm[:2]}{aamm[2:]}"


def baixar_do_data_lake(par_url: str) -> pd.DataFrame:
    print("Baixando arquivo bruto do Data Lake (OCI Object Storage)...")
    resp = requests.get(par_url, timeout=60)
    resp.raise_for_status()
    df = pd.read_parquet(io.BytesIO(resp.content))
    print(f"{len(df)} linhas lidas do Data Lake.")
    return df


def transformar(df: pd.DataFrame, competencia: str) -> pd.DataFrame:
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
    out = out.sort_values("VALOR_TOTAL", ascending=False).drop_duplicates(subset=["AIH_ORIGINAL"], keep="first")
    return out


def exportar_arquivo_local(df: pd.DataFrame, caminho: str):
    """Item 1c do enunciado: copia do arquivo tratado no ambiente local,
    com o nome padronizado RM9999 (RM do representante do grupo)."""
    df.to_csv(caminho, index=False, encoding="utf-8")
    print(f"Arquivo tratado exportado localmente: {caminho}")


def carregar_staging(conn, df: pd.DataFrame):
    cur = conn.cursor()
    cur.execute(f"TRUNCATE TABLE {TABELA_STAGING}")
    cols = list(df.columns)
    placeholders = ", ".join(f":{i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO {TABELA_STAGING} ({', '.join(cols)}) VALUES ({placeholders})"
    dados = [tuple(row) for row in df[cols].itertuples(index=False, name=None)]
    cur.executemany(sql, dados)
    conn.commit()


def garantir_procedimentos_existem(conn):
    cur = conn.cursor()
    cur.execute(
        f"""
        MERGE INTO DIM_PROCEDIMENTO d
        USING (
            SELECT DISTINCT COD_PROCEDIMENTO FROM {TABELA_STAGING}
            WHERE COD_PROCEDIMENTO IS NOT NULL
        ) s
        ON (d.COD_PROCEDIMENTO = s.COD_PROCEDIMENTO)
        WHEN NOT MATCHED THEN
        INSERT (COD_PROCEDIMENTO, DESCRICAO, GRUPO)
        VALUES (s.COD_PROCEDIMENTO, 'Procedimento nao catalogado na SIGTAP atual', 'Desconhecido')
        """
    )
    conn.commit()


def merge_staging_para_fato(conn):
    cur = conn.cursor()
    cur.execute(
        f"""
        MERGE INTO {TABELA_FINAL} f
        USING {TABELA_STAGING} s
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


def main():
    if not OCI_PAR_URL:
        raise RuntimeError("OCI_PAR_URL nao definida no .env")

    competencia = extrair_competencia_do_nome(NOME_ARQUIVO)
    print(f"Competencia identificada pelo nome do arquivo: {competencia}")

    bruto = baixar_do_data_lake(OCI_PAR_URL)
    transformado = transformar(bruto, competencia)
    print(f"{len(transformado)} linhas apos transformacao (deduplicacao por AIH).")

    exportar_arquivo_local(transformado, ARQUIVO_LOCAL_TRATADO)

    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    carregar_staging(conn, transformado)
    garantir_procedimentos_existem(conn)
    linhas = merge_staging_para_fato(conn)
    print(f"\n{linhas} linhas inseridas em {TABELA_FINAL}.")
    conn.close()


if __name__ == "__main__":
    main()
