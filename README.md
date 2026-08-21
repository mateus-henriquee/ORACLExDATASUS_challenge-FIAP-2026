# Painel Inteligente de Acesso Hospitalar

Como usar dados abertos do DATASUS para entender internações, pressão regional e capacidade hospitalar — combinando SQL, IA local e visualização, com um recorte de acesso por perfil de usuário.

Projeto acadêmico (FIAP), 100% baseado em dados públicos reais do Ministério da Saúde e do IBGE.

## O que este projeto faz

Três perfis de usuário, três formas de consumir a mesma base de dados:

- **Leigo** — chat com uma IA local (roda no seu próprio computador, sem depender de API paga) que responde perguntas sobre um CSV que o usuário sobe na hora. Uso pontual, sem precisar esperar um analista.
- **Analista** — dashboard analítico completo com filtros por mês e busca de hospital, consultando direto a base institucional no Oracle.
- **Executivo** — visão financeira (custo estimado por internação, ranking de hospitais por custo, permanência média), pensada pra decisão rápida.

## Fontes de dados (todas reais, nenhuma inventada)

| Dado | Fonte | Como é obtido |
|---|---|---|
| Municípios | IBGE (API oficial) | `load_dim_municipio.py` |
| Hospitais/estabelecimentos | CNES, via API DEMAS (Ministério da Saúde) | `load_dim_hospital.py` |
| Procedimentos | SIGTAP (mirror do FTP oficial do DATASUS) | `load_dim_procedimento.py` |
| Internações | SIH-SUS, via biblioteca `pysus` (FTP oficial do DATASUS) | `sync_sih_sus.py` |

**Nota sobre o valor financeiro**: o campo `VALOR_TOTAL` é o valor de **reembolso pago pelo SUS** (campo `VAL_TOT` do SIH-SUS), não o custo operacional real do hospital — é a informação financeira mais próxima da realidade que o DATASUS disponibiliza publicamente.

**Recorte atual**: estado de São Paulo, ano de 2024 (último ano fechado disponível no catálogo usado). Ver [Limitações](#limitações-conhecidas) abaixo.

## Arquitetura

```
local-ia-rag/                      ← raiz do projeto
├── app/                           ← backend da IA (perfil leigo)
│   ├── main.py                    ← API FastAPI
│   ├── llm.py                     ← modelo GGUF local (llama-cpp-python)
│   ├── rag.py                     ← indexação do CSV enviado pelo usuário
│   ├── plots.py                   ← geração de gráficos
│   └── database.py                ← histórico de conversas (SQLite)
├── static/                        ← frontend da IA (chat)
├── painel-hospitalar-sus/         ← pipeline de dados (extração + carga no Oracle)
│   ├── load_dim_municipio.py
│   ├── load_dim_hospital.py
│   ├── load_dim_procedimento.py
│   └── sync_sih_sus.py
├── painel-executivo-hospitalar/   ← dashboard (perfil analista/executivo)
│   ├── main.py                    ← API FastAPI que consulta o Oracle
│   └── painel-executivo-hospitalar.html
└── models/                        ← modelo GGUF (baixar separadamente, não vem no repo)
```

Todo o dado passa por um único banco Oracle, organizado em esquema estrela (`FATO_INTERNACAO` + dimensões `DIM_MUNICIPIO`, `DIM_HOSPITAL`, `DIM_PROCEDIMENTO`).

## Pré-requisitos

- Python 3.12+
- Oracle Database (testado com Oracle XE 21c, rodando localmente)
- ~4GB de espaço livre (modelo de IA + dados carregados)
- Git

## Como rodar

### 1. Clonar e configurar credenciais

```bash
git clone <url-deste-repositorio>
cd local-ia-rag
```

Cada uma das três pastas (`app` na raiz, `painel-hospitalar-sus`, `painel-executivo-hospitalar`) tem seu próprio `.env`. Copie o exemplo e preencha com as credenciais do **seu** banco Oracle:

```bash
cp .env.example .env
cp painel-hospitalar-sus/.env.example painel-hospitalar-sus/.env
cp painel-executivo-hospitalar/.env.example painel-executivo-hospitalar/.env
```

### 2. Instalar o Oracle e criar o schema

Instale o Oracle Database (XE é gratuito: [oracle.com/database/technologies/xe-downloads.html](https://www.oracle.com/database/technologies/xe-downloads.html)).

Rode o DDL completo (arquivo `painel-hospitalar-sus/schema.sql`) no seu banco antes de qualquer script Python — ele cria as 7 tabelas (`DIM_MUNICIPIO`, `DIM_HOSPITAL`, `DIM_HOSPITAL_CAPACIDADE`, `DIM_PROCEDIMENTO`, `FATO_INTERNACAO`, `SYNC_LOG`, `STG_INTERNACAO`).

### 3. Popular o banco com dados reais

```bash
cd painel-hospitalar-sus
python -m venv venv
source venv/Scripts/activate   # Windows (Git Bash). Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
```

Rode **nesta ordem exata** (cada um depende do anterior por causa das chaves estrangeiras):

```bash
python load_dim_municipio.py       # ~1 min
python load_dim_hospital.py        # ~30-60 min (baixa ~150 mil registros da API do CNES)
python load_dim_procedimento.py    # ~1 min
python sync_sih_sus.py             # ~15-30 min (baixa e processa 12 meses de internações)
```

`load_dim_hospital.py` salva progresso em `checkpoint_hospital.json` — se cair no meio, é seguro rodar de novo, ele retoma sozinho.

### 4. Rodar a IA (perfil leigo)

Baixe um modelo GGUF (recomendado: Ministral 7B Instruct, quantização Q5_K_M ou Q4_K_M pra máquinas mais fracas) e salve em `models/`.

```bash
cd ..  # volta pra raiz
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra `http://localhost:8000` no navegador.

### 5. Rodar o dashboard (perfil analista/executivo)

Em outro terminal:

```bash
cd painel-executivo-hospitalar
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001
```

Abra o arquivo `painel-executivo-hospitalar.html` direto no navegador (duplo clique).

## Limitações conhecidas

- **Escopo geográfico e temporal**: só São Paulo, só 2024. O SIH-SUS tem defasagem de publicação de ~60 dias e o catálogo usado não tinha 2025 disponível no momento da carga — expandir pra outros estados/anos é só rodar os scripts de novo com outros parâmetros.
- **CNES inclui todo tipo de estabelecimento de saúde**, não só hospitais (consultórios, clínicas, laboratórios também aparecem). Há colunas (`POSSUI_ATEND_HOSPITALAR`, `FAZ_ATENDIMENTO_SUS`) pra filtrar isso quando necessário.
- **Valor financeiro é reembolso do SUS**, não custo operacional real do hospital.
- **Login por perfil** (leigo/analista/executivo) ainda não está implementado — a separação de acesso é um próximo passo planejado (backend com JWT + Oracle, já desenhado).
- **Hospedagem**: atualmente roda 100% local. Migração pro Oracle Cloud (Autonomous Database) está planejada, sem necessidade de mudar código — só a string de conexão no `.env`.

## Stack técnica

- **Banco de dados**: Oracle (XE local, migração planejada pra Autonomous Database)
- **Backend**: Python, FastAPI
- **IA**: modelo GGUF local via `llama-cpp-python` (sem depender de API paga)
- **Extração de dados**: `pysus`, `requests`, APIs oficiais do governo
- **Frontend**: HTML/CSS/JS puro, Chart.js
