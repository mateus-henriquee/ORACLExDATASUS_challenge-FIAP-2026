"""
Carrega DIM_MUNICIPIO a partir da API oficial do IBGE.
Fonte real: https://servicodados.ibge.gov.br/api/v1/localidades/estados/{UF}/municipios

IMPORTANTE: o IBGE usa codigo de municipio com 7 digitos (com digito verificador).
O CNES e o SIH-SUS usam a versao de 6 digitos (sem o digito verificador).
Por isso convertemos aqui (ibge_id // 10) antes de gravar -- sem essa conversao,
nenhum hospital do CNES bateria com nenhum municipio no MERGE depois.
"""
import os
import requests
import oracledb
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

UF = "SP"


def buscar_municipios(uf: str) -> list:
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extrair_regiao(municipio: dict):
    try:
        return municipio["microrregiao"]["mesorregiao"]["UF"]["regiao"]["nome"]
    except (KeyError, TypeError):
        return None


def main():
    dados = buscar_municipios(UF)
    print(f"{len(dados)} municipios encontrados no IBGE para {UF}.")

    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    cur = conn.cursor()

    inseridos = 0
    for m in dados:
        cod_ibge_7 = m["id"]
        cod_datasus_6 = cod_ibge_7 // 10  # remove o digito verificador
        nome = m["nome"]
        regiao = extrair_regiao(m)

        try:
            cur.execute(
                """
                MERGE INTO DIM_MUNICIPIO d
                USING (SELECT :cod AS COD_IBGE FROM dual) s
                ON (d.COD_IBGE = s.COD_IBGE)
                WHEN NOT MATCHED THEN
                INSERT (COD_IBGE, NOME, UF, REGIAO)
                VALUES (:cod, :nome, :uf, :regiao)
                """,
                cod=cod_datasus_6, nome=nome, uf=UF, regiao=regiao,
            )
            inseridos += cur.rowcount
        except Exception as e:
            print(f"Erro ao inserir {nome} ({cod_datasus_6}): {e}")

    conn.commit()
    print(f"{inseridos} municipios inseridos em DIM_MUNICIPIO.")
    conn.close()


if __name__ == "__main__":
    main()