# sync_sih_sus_multi.py
# Pipeline: DATASUS → Parquet local → OCI Object Storage (Data Lake) → Oracle XE
#
# CONFIGURAÇÃO (linha 20-23):
#   UFS  = ["SP", "RJ"]       ← estados para carregar
#   ANOS = [2025, 2026]        ← anos para carregar
#   MESES = range(1, 13)       ← meses (1-12 = ano inteiro)

import asyncio
import logging
import os
import time
from pathlib import Path

import oracledb
import pandas as pd
import requests
from pysus.api import PySUSClient
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("sync")

load_dotenv()

# ══════════════════════════════════════════
#  CONFIGURAÇÃO — MUDE AQUI
# ══════════════════════════════════════════
UFS  = ["RJ"]
ANOS = [2026]
MESES = list(range(1, 13))

ORACLE_USER     = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN      = os.getenv("ORACLE_DSN")
OCI_PAR_URL     = os.getenv("OCI_PAR_URL")

PARQUET_DIR = Path("datalake_local")
PARQUET_DIR.mkdir(exist_ok=True)

MAPA_SEXO = {"1": "M", "3": "F"}


# ══════════════════════════════════════════
#  EXTRAÇÃO — DATASUS via pysus
# ══════════════════════════════════════════

async def _listar_rd(uf):
    async with PySUSClient() as client:
        todos = await client.query(dataset="sih", state=uf)
        return [a for a in todos if "RD" in a.path.parts]


def listar_arquivos_rd(uf):
    return asyncio.run(_listar_rd(uf))


async def _baixar_async(arquivos):
    async with PySUSClient() as client:
        caminhos = []
        for arq in arquivos:
            p = await client.download_to_parquet(arq)
            caminhos.append(p.path)
        if not caminhos:
            return pd.DataFrame()
        resultado = client.read_parquet(caminhos, mode="union")
        if isinstance(resultado, pd.DataFrame):
            return resultado
        return resultado.df()


def baixar_competencia(arquivos_rd, ano, mes):
    ano_s, mes_s = str(ano), f"{mes:02d}"
    alvo = [a for a in arquivos_rd if ano_s in a.path.parts and mes_s in a.path.parts]
    if not alvo:
        return pd.DataFrame()
    df = asyncio.run(_baixar_async(alvo))
    return df if df is not None else pd.DataFrame()


# ══════════════════════════════════════════
#  TRANSFORMAÇÃO
# ══════════════════════════════════════════

def transformar(df, competencia):
    if df.empty:
        return df

    out = pd.DataFrame()
    out["AIH_ORIGINAL"]      = df["N_AIH"].astype(str)
    out["COD_CNES"]          = pd.to_numeric(df["CNES"], errors="coerce")
    out["COD_IBGE"]          = pd.to_numeric(df["MUNIC_MOV"], errors="coerce")
    out["COD_PROCEDIMENTO"]  = df["PROC_REA"].astype(str)
    out["COMPETENCIA"]       = competencia
    out["DATA_INTERNACAO"]   = pd.to_datetime(df["DT_INTER"], format="%Y%m%d", errors="coerce")
    out["DATA_SAIDA"]        = pd.to_datetime(df["DT_SAIDA"], format="%Y%m%d", errors="coerce")
    out["DIAS_PERMANENCIA"]  = pd.to_numeric(df.get("DIAS_PERM"), errors="coerce")
    out["VALOR_TOTAL"]       = pd.to_numeric(df.get("VAL_TOT"), errors="coerce")
    out["SEXO"]              = df["SEXO"].astype(str).map(MAPA_SEXO).fillna("?")
    out["CARATER_INTERNACAO"]= df.get("CAR_INT", "").astype(str)

    idade = pd.to_numeric(df.get("IDADE"), errors="coerce")
    out["FAIXA_ETARIA"] = pd.cut(
        idade,
        bins=[-1, 1, 4, 12, 18, 30, 45, 60, 200],
        labels=["<1", "1-4", "5-12", "13-18", "19-30", "31-45", "46-60", "60+"],
    ).astype(str)

    out = out.dropna(subset=["COD_CNES", "COD_IBGE"])
    out = out.sort_values("VALOR_TOTAL", ascending=False).drop_duplicates(subset=["AIH_ORIGINAL"], keep="first")
    return out


# ══════════════════════════════════════════
#  DATA LAKE — parquet local + OCI upload
# ══════════════════════════════════════════

def salvar_parquet(df, uf, competencia):
    caminho = PARQUET_DIR / f"RD{uf}{competencia[2:]}.parquet"
    df.to_parquet(caminho, index=False)
    logger.info("  Parquet: %s (%d linhas)", caminho.name, len(df))
    return caminho


def upload_oci(caminho):
    if not OCI_PAR_URL:
        logger.info("  OCI_PAR_URL vazia — pulando upload.")
        return
    nome = caminho.name
    url = OCI_PAR_URL.rstrip("/") + "/" + nome
    try:
        with open(caminho, "rb") as f:
            r = requests.put(url, data=f, timeout=120,
                            headers={"Content-Type": "application/octet-stream"})
        if r.status_code in (200, 201):
            logger.info("  Upload OCI OK: %s", nome)
        else:
            logger.warning("  Upload OCI falhou (%d): %s", r.status_code, r.text[:100])
    except Exception as e:
        logger.warning("  Upload OCI erro: %s", e)


# ══════════════════════════════════════════
#  ORACLE — staging + MERGE
# ══════════════════════════════════════════

def ja_processada(conn, uf, comp):
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM SYNC_LOG
        WHERE SISTEMA = 'SIH-SUS' AND UF = :uf
          AND COMPETENCIA = :comp AND STATUS = 'SUCESSO'
    """, uf=uf, comp=comp)
    return cur.fetchone()[0] > 0


def registrar(conn, uf, comp, status, linhas, msg=None):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO SYNC_LOG (SISTEMA, UF, COMPETENCIA, STATUS, LINHAS_PROCESSADAS, MENSAGEM)
        VALUES ('SIH-SUS', :uf, :comp, :status, :linhas, :msg)
    """, uf=uf, comp=comp, status=status, linhas=linhas, msg=msg)
    conn.commit()


def carregar_staging(conn, df):
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE STG_INTERNACAO")
    cols = list(df.columns)
    placeholders = ", ".join(f":{i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO STG_INTERNACAO ({', '.join(cols)}) VALUES ({placeholders})"
    dados = [tuple(row) for row in df[cols].itertuples(index=False, name=None)]
    cur.executemany(sql, dados)
    conn.commit()


def garantir_procedimentos(conn):
    cur = conn.cursor()
    cur.execute("""
        MERGE INTO DIM_PROCEDIMENTO d
        USING (
            SELECT DISTINCT COD_PROCEDIMENTO FROM STG_INTERNACAO
            WHERE COD_PROCEDIMENTO IS NOT NULL
        ) s
        ON (d.COD_PROCEDIMENTO = s.COD_PROCEDIMENTO)
        WHEN NOT MATCHED THEN
        INSERT (COD_PROCEDIMENTO, DESCRICAO, GRUPO)
        VALUES (s.COD_PROCEDIMENTO, 'Procedimento nao catalogado', 'Desconhecido')
    """)
    conn.commit()


def merge_para_fato(conn):
    cur = conn.cursor()
    cur.execute("""
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
    """)
    conn.commit()
    return cur.rowcount


# ══════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════

def main():
    inicio = time.time()
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)

    competencias = [f"{ano}{mes:02d}" for ano in ANOS for mes in MESES]
    total_linhas = 0
    total_comp = 0

    for uf in UFS:
        logger.info("═" * 60)
        logger.info("ESTADO: %s", uf)
        logger.info("═" * 60)

        logger.info("[%s] Listando arquivos RD no DATASUS...", uf)
        arquivos_rd = listar_arquivos_rd(uf)
        logger.info("[%s] %d arquivos RD encontrados.", uf, len(arquivos_rd))

        for comp in competencias:
            ano = int(comp[:4])
            mes = int(comp[4:6])

            if ja_processada(conn, uf, comp):
                logger.info("[%s/%s] Já processada — pulando.", uf, comp)
                continue

            logger.info("[%s/%s] Baixando...", uf, comp)
            try:
                bruto = baixar_competencia(arquivos_rd, ano, mes)
                if bruto.empty:
                    logger.warning("[%s/%s] Sem dados no DATASUS.", uf, comp)
                    registrar(conn, uf, comp, "SUCESSO", 0, "sem dados no periodo")
                    continue

                logger.info("[%s/%s] %d linhas brutas.", uf, comp, len(bruto))

                transformado = transformar(bruto, comp)
                logger.info("[%s/%s] %d linhas transformadas.", uf, comp, len(transformado))

                # Data Lake
                caminho = salvar_parquet(transformado, uf, comp)
                upload_oci(caminho)

                # Oracle
                carregar_staging(conn, transformado)
                garantir_procedimentos(conn)
                linhas = merge_para_fato(conn)

                registrar(conn, uf, comp, "SUCESSO", linhas)
                total_linhas += linhas
                total_comp += 1
                logger.info("[%s/%s] ✓ %d linhas no Oracle.", uf, comp, linhas)

            except Exception as e:
                logger.error("[%s/%s] ✗ Erro: %s", uf, comp, e)
                registrar(conn, uf, comp, "ERRO", 0, str(e)[:500])

    conn.close()
    duracao = time.time() - inicio

    logger.info("═" * 60)
    logger.info("PIPELINE CONCLUÍDO")
    logger.info("  Estados:       %s", ", ".join(UFS))
    logger.info("  Anos:          %s", ", ".join(str(a) for a in ANOS))
    logger.info("  Competências:  %d processadas", total_comp)
    logger.info("  Linhas novas:  %s", f"{total_linhas:,}".replace(",", "."))
    logger.info("  Duração:       %.1f min", duracao / 60)
    logger.info("  Parquets:      %s", PARQUET_DIR.resolve())
    logger.info("═" * 60)


if __name__ == "__main__":
    main()
