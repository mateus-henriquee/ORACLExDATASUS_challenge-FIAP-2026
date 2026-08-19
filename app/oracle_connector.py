import pandas as pd
import oracledb
from .config import ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN


def query_to_dataframe(sql: str) -> pd.DataFrame:
    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        raise ValueError(
            "Credenciais Oracle nao configuradas no .env "
            "(ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN)."
        )
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN) as conn:
        return pd.read_sql(sql, conn)
