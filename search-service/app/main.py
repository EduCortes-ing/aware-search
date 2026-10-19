# =====================================================================
# search-service / main.py
# Microservicio de busqueda: recibe un texto y devuelve los antibioticos
# que coinciden, con su clasificacion AWaRe.
# Solo es accesible dentro de la red interna de Docker (via la API).
# =====================================================================

import os                                            # leer variables de entorno
import re                                            # expresiones regulares para validar texto
import unicodedata                                   # quitar acentos del texto buscado
from typing import List, Optional                    # tipos para documentar las respuestas

from fastapi import FastAPI, HTTPException, Query   # framework web y manejo de errores
from psycopg2.extras import RealDictCursor          # filas como diccionarios (columna -> valor)
from psycopg2.pool import ThreadedConnectionPool    # conjunto de conexiones reutilizables
from pydantic import BaseModel                      # modelos que describen la respuesta (salen en Swagger)

# Crea la aplicacion; el titulo aparece en /docs
app = FastAPI(title="search-service", description="Busqueda de antibioticos AWaRe (OMS 2025)")

# Pool de conexiones: se crea al arrancar y se reutiliza en cada busqueda
pool: Optional[ThreadedConnectionPool] = None


class Antibiotico(BaseModel):
    """Forma de cada resultado que devuelve la busqueda."""
    name: str                                        # nombre mostrado, ej. "Colistin (IV)"
    base_name: str                                   # sustancia sin via
    route: Optional[str] = None                      # IV, oral o vacio
    drug_class: Optional[str] = None                 # clase antibiotica
    atc_code: Optional[str] = None                   # codigo ATC
    category: str                                    # Access / Watch / Reserve / Not recommended
    listed_eml: bool                                 # esta en la lista de medicamentos esenciales
    notes: Optional[str] = None                      # nota de la OMS


class Respuesta(BaseModel):
    """Envoltorio: el texto buscado, cuantos resultados y la lista."""
    query: str                                       # texto ya normalizado que se uso
    count: int                                       # numero de resultados devueltos
    results: List[Antibiotico]                       # lista de coincidencias


@app.on_event("startup")
def al_arrancar():
    """Abre el pool de conexiones cuando arranca el contenedor."""
    global pool                                      # modifica la variable de modulo
    pool = ThreadedConnectionPool(1, 5, os.environ["DATABASE_URL"])  # entre 1 y 5 conexiones


@app.on_event("shutdown")
def al_apagar():
    """Cierra todas las conexiones cuando el contenedor se detiene."""
    if pool:                                         # si el pool existe
        pool.closeall()                              # las cierra


def normalizar(texto: str) -> str:
    """Minusculas, sin acentos y sin espacios sobrantes: 'Azitromicína ' -> 'azitromicina'."""
    sin_acentos = unicodedata.normalize("NFKD", texto)                       # separa letra y acento
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))  # descarta acentos
    return re.sub(r"\s+", " ", sin_acentos).strip().lower()                 # compacta espacios


def escapar_like(texto: str) -> str:
    """Escapa % _ y \\ para que el usuario no pueda usarlos como comodines."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Consulta SQL parametrizada (los %s se rellenan de forma segura, evita inyeccion SQL)
# Relevancia: 0 exacta, 1 empieza con, 2 contiene o ATC, 3 clase, 4 solo por parecido
SQL_BUSCAR = """
WITH t AS (
  SELECT a.name, a.base_name, a.route, a.class AS drug_class, a.atc_code, a.category,
         a.listed_eml, a.notes,
         similarity(lower(a.base_name), %(q)s) AS sim,              -- parecido con la sustancia
         CASE                                                       -- relevancia por coincidencia directa
           WHEN lower(a.base_name) = %(q)s THEN 0                   -- coincidencia exacta
           WHEN lower(a.base_name) LIKE %(prefijo)s ESCAPE '\\' THEN 1  -- empieza con el texto
           WHEN lower(a.name) LIKE %(contiene)s ESCAPE '\\' THEN 2      -- lo contiene
           WHEN lower(a.atc_code) LIKE %(prefijo)s ESCAPE '\\' THEN 2   -- coincide con el ATC
           WHEN lower(a.class) LIKE %(contiene)s ESCAPE '\\' THEN 3     -- coincide con la clase
           ELSE NULL
         END AS rank_directo,
         (SELECT MIN(CASE                                           -- relevancia por nombre en espanol
                  WHEN al.alias = %(q)s THEN 0                      -- alias exacto
                  WHEN al.alias LIKE %(prefijo)s ESCAPE '\\' THEN 1     -- alias empieza con el texto
                  WHEN al.alias LIKE %(contiene)s ESCAPE '\\' THEN 2    -- alias lo contiene
                END)
            FROM antibiotic_aliases al
           WHERE al.base_name = a.base_name) AS rank_alias,         -- mejor coincidencia directa de alias
         (SELECT MAX(similarity(al.alias, %(q)s))                   -- mejor parecido con un alias
            FROM antibiotic_aliases al
           WHERE al.base_name = a.base_name) AS sim_alias
  FROM antibiotics a
),
u AS (
  SELECT t.*,
         LEAST(COALESCE(rank_directo, 9), COALESCE(rank_alias, 9),   -- mejor relevancia de todas
               CASE WHEN sim >= 0.35 OR COALESCE(sim_alias, 0) >= 0.5
                    THEN 4 ELSE 9 END) AS best                       -- parecido = relevancia 4
  FROM t
)
SELECT name, base_name, route, drug_class, atc_code, category, listed_eml, notes
FROM u
WHERE best <= 3                                                       -- coincidencias directas, o
   OR (best = 4 AND NOT EXISTS (SELECT 1 FROM u WHERE best <= 3))     -- parecido solo si no hay directas
ORDER BY best,                                                        -- mejor relevancia primero
         GREATEST(sim, COALESCE(sim_alias, 0)) DESC,                  -- luego por parecido
         name                                                         -- y por orden alfabetico
LIMIT %(limite)s
"""


@app.get("/health")
def health():
    """Comprueba que el servicio y la base de datos responden."""
    conexion = pool.getconn()                        # toma una conexion del pool
    try:
        with conexion.cursor() as cur:               # abre un cursor
            cur.execute("SELECT COUNT(*) FROM antibiotics")  # cuenta los registros cargados
            total = cur.fetchone()[0]                # lee el resultado
        return {"service": "search-service", "status": "ok", "records": total}
    finally:
        pool.putconn(conexion)                       # devuelve la conexion al pool


@app.get("/search", response_model=Respuesta)
def buscar(
    q: str = Query(..., min_length=2, max_length=60, description="Sustancia, clase o codigo ATC"),
    limit: int = Query(25, ge=1, le=100, description="Maximo de resultados"),
):
    """Busca antibioticos por sustancia, clase o codigo ATC."""
    texto = normalizar(q)                            # limpia el texto del usuario
    if len(texto) < 2:                               # tras limpiar, debe quedar algo util
        raise HTTPException(status_code=422, detail="El texto de busqueda es demasiado corto")
    escapado = escapar_like(texto)                   # evita comodines no deseados
    parametros = {
        "q": texto,                                  # texto para comparar exacto y por parecido
        "contiene": f"%{escapado}%",                 # patron "contiene"
        "prefijo": f"{escapado}%",                   # patron "empieza con"
        "limite": limit,                             # tope de resultados
    }
    conexion = pool.getconn()                        # toma una conexion del pool
    try:
        with conexion.cursor(cursor_factory=RealDictCursor) as cur:  # filas como diccionarios
            cur.execute(SQL_BUSCAR, parametros)      # ejecuta la busqueda de forma segura
            filas = cur.fetchall()                   # lee todos los resultados
        return {"query": texto, "count": len(filas), "results": filas}
    finally:
        pool.putconn(conexion)                       # siempre devuelve la conexion