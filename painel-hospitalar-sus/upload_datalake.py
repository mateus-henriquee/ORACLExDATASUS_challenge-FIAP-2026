import os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
PAR = os.getenv("OCI_PAR_URL")
pasta = Path("datalake_local")

for arq in sorted(pasta.glob("*.parquet")):
    url = PAR.rstrip("/") + "/" + arq.name
    with open(arq, "rb") as f:
        r = requests.put(url, data=f, timeout=120, headers={"Content-Type":"application/octet-stream"})
    status = "✓" if r.status_code in (200,201) else f"✗ {r.status_code}"
    print(f"{arq.name} → {status}")