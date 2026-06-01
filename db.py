import os
import psycopg2
import urllib.request as _urlreq
import json as _json
from dotenv import load_dotenv

load_dotenv()

DB = {
    "host":     os.getenv("DB_HOST", "187.127.29.98"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "dbname":   os.getenv("DB_NAME", "SQLLAR"),
    "user":     os.getenv("DB_USER", "sqllar_user"),
    "password": os.getenv("DB_PASSWORD", ""),
}

def get_conn():
    return psycopg2.connect(**DB)

def load_uf():
    try:
        req = _urlreq.Request(
            "https://mindicador.cl/api/uf",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with _urlreq.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read())
        uf = float(data["serie"][0]["valor"])
        fecha = data["serie"][0]["fecha"][:10]
        print(f"  UF ({fecha}): ${uf:,.2f}")
        return uf, fecha
    except Exception as e:
        print(f"  [WARN] mindicador.cl falló ({e}) — obteniendo UF desde BD...")

    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT divisa_conversion_valor, divisa_conversion_fecha
            FROM liquidacion_cargos
            WHERE divisa = 'Unidad de fomento'
              AND divisa_conversion_valor > 10000
              AND divisa_conversion_fecha IS NOT NULL
            ORDER BY divisa_conversion_fecha DESC LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        if row:
            return float(row[0]), str(row[1])[:10]
    except Exception as e2:
        print(f"  [WARN] BD UF fallback falló: {e2}")

    return None, None
