"""
Carrega/atualiza DIM_HOSPITAL a partir da API DEMAS (CNES).
Fonte real: https://apidadosabertos.saude.gov.br/cnes/estabelecimentos

Esta versao salva um checkpoint (offset) em disco a cada lote gravado.
Se o script for interrompido (Ctrl+C, queda de rede, timeout persistente),
a proxima execucao retoma de onde parou, em vez de comecar do offset 0
de novo -- evita reprocessar dezenas de milhares de registros ja vistos.
"""
import json
import os
import time
import requests
import oracledb
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

CODIGO_UF_SP = 35
BASE_URL = "https://apidadosabertos.saude.gov.br/cnes/estabelecimentos"
MAX_PAGINAS = 10000
TAMANHO_LOTE_GRAVACAO = 1000
ARQUIVO_CHECKPOINT = "checkpoint_hospital.json"


def carregar_checkpoint() -> int:
    if os.path.exists(ARQUIVO_CHECKPOINT):
        with open(ARQUIVO_CHECKPOINT) as f:
            dado = json.load(f)
        print(f"Checkpoint encontrado: retomando do offset {dado['offset']} "
              f"(pagina ~{dado['offset'] // 20}).")
        return dado["offset"]
    return 0


def salvar_checkpoint(offset: int):
    with open(ARQUIVO_CHECKPOINT, "w") as f:
        json.dump({"offset": offset}, f)


def apagar_checkpoint():
    if os.path.exists(ARQUIVO_CHECKPOINT):
        os.remove(ARQUIVO_CHECKPOINT)


def buscar_pagina_com_retry(params, tentativas=3):
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json().get("estabelecimentos", [])
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"  Tentativa {tentativa}/{tentativas} falhou ({e.__class__.__name__}). "
                  f"Tentando de novo em {tentativa * 2}s...")
            time.sleep(tentativa * 2)
    raise RuntimeError(f"Falhou apos {tentativas} tentativas em offset={params.get('offset')}")


def gravar_lote(conn, lote_estabelecimentos):
    cur = conn.cursor()
    processados = 0
    ignorados = 0
    for est in lote_estabelecimentos:
        cod_cnes = est.get("codigo_cnes")
        nome = est.get("nome_fantasia") or est.get("nome_razao_social")
        cod_ibge = est.get("codigo_municipio")
        natureza = est.get("descricao_natureza_juridica_estabelecimento")
        esfera = est.get("descricao_esfera_administrativa")
        possui_hosp = est.get("estabelecimento_possui_atendimento_hospitalar")
        faz_sus = est.get("estabelecimento_faz_atendimento_ambulatorial_sus")

        if not cod_cnes or not nome or not cod_ibge:
            ignorados += 1
            continue

        try:
            cur.execute(
                """
                MERGE INTO DIM_HOSPITAL d
                USING (SELECT :cnes AS COD_CNES FROM dual) s
                ON (d.COD_CNES = s.COD_CNES)
                WHEN MATCHED THEN UPDATE SET
                    d.POSSUI_ATEND_HOSPITALAR = :hosp,
                    d.FAZ_ATENDIMENTO_SUS = :sus
                WHEN NOT MATCHED THEN
                INSERT (COD_CNES, NOME, COD_IBGE, NATUREZA, ESFERA_ADM,
                        POSSUI_ATEND_HOSPITALAR, FAZ_ATENDIMENTO_SUS)
                VALUES (:cnes, :nome, :ibge, :nat, :esf, :hosp, :sus)
                """,
                cnes=cod_cnes, nome=nome[:200], ibge=cod_ibge, nat=natureza, esf=esfera,
                hosp=possui_hosp, sus=faz_sus,
            )
            processados += 1
        except Exception as e:
            print(f"  Erro ao processar CNES {cod_cnes}: {e}")
            ignorados += 1

    conn.commit()
    return processados, ignorados


def main():
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)

    print("Baixando e atualizando estabelecimentos de SP via API DEMAS...")
    offset = carregar_checkpoint()
    vistos = set()
    buffer = []
    limit = 20
    total_processados = 0
    total_ignorados = 0
    parou_naturalmente = False

    for pagina in range(MAX_PAGINAS):
        params = {"limit": limit, "offset": offset, "codigo_uf": CODIGO_UF_SP}
        lote = buscar_pagina_com_retry(params)

        if not lote:
            print(f"Offset {offset}: vazio -- terminou naturalmente.")
            parou_naturalmente = True
            break

        novos = 0
        for est in lote:
            chave = est.get("codigo_cnes")
            if chave not in vistos:
                vistos.add(chave)
                buffer.append(est)
                novos += 1

        if pagina % 100 == 0:
            print(f"Offset {offset}: {len(vistos)} vistos nesta execucao "
                  f"({total_processados} ja atualizados no banco).")

        if novos == 0:
            print("Nenhum registro novo -- terminou naturalmente.")
            parou_naturalmente = True
            break

        offset += limit

        if len(buffer) >= TAMANHO_LOTE_GRAVACAO:
            proc, ign = gravar_lote(conn, buffer)
            total_processados += proc
            total_ignorados += ign
            buffer = []
            salvar_checkpoint(offset)

        time.sleep(0.15)
    else:
        print(f"AVISO: bateu na trava de MAX_PAGINAS ({MAX_PAGINAS}) sem terminar naturalmente.")

    if buffer:
        proc, ign = gravar_lote(conn, buffer)
        total_processados += proc
        total_ignorados += ign
        salvar_checkpoint(offset)

    conn.close()

    if parou_naturalmente:
        apagar_checkpoint()

    print(f"\nFINAL: {total_processados} registros atualizados/inseridos em DIM_HOSPITAL "
          f"nesta execucao. {total_ignorados} ignorados. Terminou naturalmente: {parou_naturalmente}")


if __name__ == "__main__":
    main()