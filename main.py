import os
import math
import secrets
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from db import get_conn, load_uf

load_dotenv()

APP_PASSWORD = os.getenv("APP_PASSWORD", "changeme")
# Token estable por sesión de servidor — se regenera al reiniciar
_SESSION_TOKEN = secrets.token_hex(32)

app = FastAPI(title="LAR Rangos de Renta")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── AUTH ─────────────────────────────────────────
class LoginBody(BaseModel):
    password: str

@app.post("/login")
def login(body: LoginBody):
    if not secrets.compare_digest(body.password, APP_PASSWORD):
        raise HTTPException(401, "Contraseña incorrecta")
    return {"token": _SESSION_TOKEN}

def require_auth(authorization: str = Header(default=None)):
    if not authorization or authorization != f"Bearer {_SESSION_TOKEN}":
        raise HTTPException(401, "No autorizado")

# ── UTILS ────────────────────────────────────────
def al_millar(n: float) -> int:
    return math.ceil(n / 1000) * 1000

# ── RUTAS PÚBLICAS ───────────────────────────────
@app.get("/")
def root():
    return FileResponse("index.html")

# ── RUTAS PROTEGIDAS ─────────────────────────────
@app.get("/uf")
def get_uf(_=Depends(require_auth)):
    uf, fecha = load_uf()
    if not uf:
        raise HTTPException(500, "No se pudo obtener el valor UF")
    return {"valor": uf, "fecha": fecha}

@app.get("/proyectos")
def get_proyectos(_=Depends(require_auth)):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT p.id, p.nombre
        FROM propiedades p
        JOIN unidades u ON u.propiedad_id = p.id
        WHERE u.estado = '100'
          AND u.raw->>'tipologia' != ''
        ORDER BY p.nombre;
    """)
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "nombre": r[1]} for r in rows]

@app.get("/tipologias")
def get_tipologias(proyecto_id: str, _=Depends(require_auth)):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.raw->>'tipologia' as tipologia
        FROM unidades u
        WHERE u.propiedad_id = %s
          AND u.estado = '100'
          AND u.raw->>'tipologia' != ''
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(u.precios) pr
              WHERE pr->>'tipo' = 'unidad_divisa_monto.tipo.lista'
                AND pr->>'concepto' = 'Arriendo'
                AND (pr->>'monto')::numeric < 70
                AND (pr->>'monto')::numeric > 0
          )
        GROUP BY u.raw->>'tipologia'
        ORDER BY tipologia;
    """, (proyecto_id,))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

@app.get("/modelos")
def get_modelos(proyecto_id: str, tipologia: str, _=Depends(require_auth)):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT
            CASE
                WHEN u.raw->>'modelo' ILIKE 'Estudio%%' THEN 'Estudio'
                WHEN u.raw->>'modelo' ILIKE 'Studio%%'  THEN 'Studio'
                ELSE u.raw->>'modelo'
            END as modelo_grupo
        FROM unidades u
        WHERE u.propiedad_id = %s
          AND u.estado = '100'
          AND u.raw->>'tipologia' = %s
          AND u.raw->>'modelo' != ''
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(u.precios) pr
              WHERE pr->>'tipo' = 'unidad_divisa_monto.tipo.lista'
                AND pr->>'concepto' = 'Arriendo'
                AND (pr->>'monto')::numeric < 70
                AND (pr->>'monto')::numeric > 0
          )
        ORDER BY modelo_grupo;
    """, (proyecto_id, tipologia))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

@app.get("/rangos")
def get_rangos(proyecto_id: str, tipologia: str, modelo: str,
               uf_manual: float = None, _=Depends(require_auth)):
    uf_valor, uf_fecha = load_uf()
    if uf_manual:
        uf_valor = uf_manual
        uf_fecha = "manual"
    if not uf_valor:
        raise HTTPException(500, "No se pudo obtener el valor UF")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            MIN((pr->>'monto')::numeric) as precio_desde,
            MAX((pr->>'monto')::numeric) as precio_hasta,
            COUNT(DISTINCT u.id) as unidades
        FROM unidades u,
             jsonb_array_elements(u.precios) pr
        WHERE u.propiedad_id = %s
          AND u.estado = '100'
          AND u.raw->>'tipologia' = %s
          AND (
              CASE
                  WHEN %s = 'Estudio' THEN u.raw->>'modelo' ILIKE 'Estudio%%'
                  WHEN %s = 'Studio'  THEN u.raw->>'modelo' ILIKE 'Studio%%'
                  ELSE u.raw->>'modelo' = %s
              END
          )
          AND pr->>'tipo' = 'unidad_divisa_monto.tipo.lista'
          AND pr->>'concepto' = 'Arriendo'
          AND (pr->>'monto')::numeric < 70
          AND (pr->>'monto')::numeric > 0;
    """, (proyecto_id, tipologia, modelo, modelo, modelo))

    row = cur.fetchone()
    conn.close()

    if not row or row[0] is None:
        raise HTTPException(404, "No se encontraron unidades disponibles para esta combinación")

    precio_desde = float(row[0])
    precio_hasta = float(row[1])
    unidades = row[2]

    def rangos(factor):
        return {
            "desde_uf":  round(precio_desde * factor, 2),
            "hasta_uf":  round(precio_hasta * factor, 2),
            "desde_clp": al_millar(precio_desde * factor * uf_valor),
            "hasta_clp": al_millar(precio_hasta * factor * uf_valor),
        }

    return {
        "proyecto_id":       proyecto_id,
        "tipologia":         tipologia,
        "modelo":            modelo,
        "uf_valor":          uf_valor,
        "uf_fecha":          uf_fecha,
        "precio_desde":      precio_desde,
        "precio_hasta":      precio_hasta,
        "unidades":          unidades,
        "renta_minima":      rangos(2.5),
        "renta_ideal":       rangos(3.0),
        "renta_garantizada": rangos(3.5),
    }
