"""
Carrega DIM_PROCEDIMENTO a partir do SIGTAP (Tabela de Procedimentos do SUS).

Fonte real: mirror automatizado do FTP oficial do DATASUS, atualizado diariamente:
https://github.com/RenatoKR/SIGTAP

O SIGTAP nao tem API -- e um arquivo de texto de largura fixa (sem separador),
com um arquivo de "layout" junto que declara a posicao exata de cada coluna.
Este script LE esse layout dinamicamente em vez de usar posicoes fixas no
codigo -- assim, se o DATASUS mudar o layout numa competencia futura, o script
se adapta sozinho em vez de ler a coluna errada silenciosamente.
"""
import io
import os
import re
import zipfile
import requests
import oracledb
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

REPO_ZIP_URL = "https://codeload.github.com/RenatoKR/SIGTAP/zip/refs/heads/main"


def baixar_repo_zip() -> zipfile.ZipFile:
    print("Baixando mirror do SIGTAP (repositorio inteiro, ~30MB, e normal demorar um pouco)...")
    resp = requests.get(REPO_ZIP_URL, timeout=120)
    resp.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(resp.content))


def achar_competencia_mais_recente(repo_zip: zipfile.ZipFile) -> str:
    candidatos = [
        n for n in repo_zip.namelist()
        if re.search(r"/tabelas/TabelaUnificada_\d{6}_v\d+\.zip$", n)
    ]
    if not candidatos:
        raise RuntimeError("Nenhuma TabelaUnificada encontrada no repositorio.")
    candidatos.sort()
    return candidatos[-1]


def parse_layout(texto_layout: str) -> list:
    linhas = texto_layout.strip().splitlines()
    campos = []
    for linha in linhas[1:]:
        partes = linha.strip().split(",")
        if len(partes) < 4:
            continue
        nome, _tamanho, inicio, fim = partes[0], partes[1], partes[2], partes[3]
        campos.append({"nome": nome, "inicio": int(inicio), "fim": int(fim)})
    return campos


def parse_linha_fixa(linha: str, campos: list) -> dict:
    registro = {}
    for campo in campos:
        valor = linha[campo["inicio"] - 1: campo["fim"]]
        registro[campo["nome"]] = valor.strip()
    return registro


def main():
    repo_zip = baixar_repo_zip()
    caminho_zip_competencia = achar_competencia_mais_recente(repo_zip)
    print(f"Competencia mais recente encontrada: {caminho_zip_competencia}")

    with repo_zip.open(caminho_zip_competencia) as f:
        competencia_zip = zipfile.ZipFile(io.BytesIO(f.read()))

    layout_grupo = parse_layout(competencia_zip.read("tb_grupo_layout.txt").decode("latin-1"))
    linhas_grupo = competencia_zip.read("tb_grupo.txt").decode("latin-1").splitlines()
    grupos = {}
    for linha in linhas_grupo:
        if not linha.strip():
            continue
        reg = parse_linha_fixa(linha, layout_grupo)
        grupos[reg["CO_GRUPO"]] = reg["NO_GRUPO"]
    print(f"{len(grupos)} grupos carregados.")

    layout_proc = parse_layout(competencia_zip.read("tb_procedimento_layout.txt").decode("latin-1"))
    linhas_proc = competencia_zip.read("tb_procedimento.txt").decode("latin-1").splitlines()
    print(f"{len(linhas_proc)} procedimentos encontrados.")

    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    cur = conn.cursor()

    inseridos = 0
    ignorados = 0
    for linha in linhas_proc:
        if not linha.strip():
            continue
        reg = parse_linha_fixa(linha, layout_proc)
        cod = reg.get("CO_PROCEDIMENTO")
        nome = reg.get("NO_PROCEDIMENTO")
        if not cod or not nome:
            ignorados += 1
            continue

        co_grupo = cod[:2]
        grupo_nome = grupos.get(co_grupo, co_grupo)

        try:
            cur.execute(
                """
                MERGE INTO DIM_PROCEDIMENTO d
                USING (SELECT :cod AS COD_PROCEDIMENTO FROM dual) s
                ON (d.COD_PROCEDIMENTO = s.COD_PROCEDIMENTO)
                WHEN NOT MATCHED THEN
                INSERT (COD_PROCEDIMENTO, DESCRICAO, GRUPO)
                VALUES (:cod, :descricao, :grupo)
                """,
                cod=cod, descricao=nome[:230], grupo=grupo_nome[:100],
            )
            inseridos += cur.rowcount
        
        except Exception as e:
            print(f"Erro ao inserir procedimento {cod}: {e}")
            ignorados += 1

    conn.commit()
    print(f"{inseridos} procedimentos inseridos em DIM_PROCEDIMENTO. {ignorados} ignorados/com erro.")
    conn.close()


if __name__ == "__main__":
    main()