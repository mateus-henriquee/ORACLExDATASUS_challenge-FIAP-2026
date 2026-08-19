# IA Cientista de Dados Local (GGUF + FastAPI + RAG)

## 1. Baixar o modelo
Baixe um GGUF do Ministral 7B Instruct, quant Q5_K_M, do Hugging Face.
Salve em: `models/ministral-7b-instruct-q5_k_m.gguf`

## 2. Criar ambiente e instalar dependências
```
cd local-ia-rag
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Configurar variáveis
```
cp .env.example .env
```
Edite `.env`:
- `MODEL_PATH`: caminho do arquivo .gguf
- `N_THREADS=4` (seu i5-1135G7 tem 4 núcleos físicos; 4 evita saturar a CPU)
- Preencha `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN` só se for usar Oracle

## 4. Rodar
```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 5. Usar
Abra: `http://localhost:8000`

- Clique em **+ Nova aba** para criar um chat (máximo 3, pode renomear com duplo-clique, excluir no "x")
- Envie um CSV pelo formulário de upload
- Ou carregue dados Oracle via `POST /chats/{id}/load_oracle` com `sql=SELECT ...`
- Pergunte normalmente no chat. Se a pergunta contiver "gráfico", "plot", "visualiza" etc., a IA gera um gráfico verde minimalista junto da resposta

## Endpoints principais
- `POST /chats` — cria aba
- `GET /chats` — lista abas
- `PUT /chats/{id}` — renomeia
- `DELETE /chats/{id}` — exclui
- `POST /chats/{id}/upload_csv` — ingere CSV no RAG
- `POST /chats/{id}/load_oracle` — ingere resultado de SQL Oracle no RAG
- `POST /chats/{id}/message` — pergunta + resposta (com gráfico se pedido)
- `GET /chats/{id}/history` — histórico salvo

## Erros comuns
- **`ModuleNotFoundError: llama_cpp`** → rode `pip install llama-cpp-python` de novo dentro do venv ativado.
- **Erro ao carregar modelo / arquivo não encontrado** → confira `MODEL_PATH` no `.env`, caminho relativo à pasta onde você roda o `uvicorn`.
- **Oracle: `DPY-4011` ou timeout** → confira `ORACLE_DSN` (formato `host:porta/service_name`) e se a rede/VPN até o banco está acessível.
- **CPU muito lenta na primeira resposta** → normal, o modelo carrega uma vez no startup; perguntas seguintes são mais rápidas.
# ia-localhost
# ia-localhost
