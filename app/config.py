import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
PLOTS_DIR = DATA_DIR / "plots"
PLOTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

MODEL_PATH = os.getenv("MODEL_PATH", str(BASE_DIR / "models" / "ministral-7b-instruct-q5_k_m.gguf"))
N_THREADS = int(os.getenv("N_THREADS", "4"))
N_CTX = int(os.getenv("N_CTX", "8192"))
MAX_TABS = 3

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

DB_PATH = DATA_DIR / "chat.db"
