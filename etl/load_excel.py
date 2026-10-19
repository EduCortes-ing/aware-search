# =====================================================================
# load_excel.py - ETL del Excel AWaRe (OMS 2025) hacia PostgreSQL
# E = Extraer (leer el Excel), T = Transformar (limpiar), L = Cargar (BD)
# Uso en Docker:  docker compose run --rm etl
# Uso de prueba:  python load_excel.py --dry-run --file ruta.xlsx
# =====================================================================

import os                      # leer variables de entorno (DATABASE_URL)
import re                      # expresiones regulares para separar la via (_IV / _oral)
import sys                     # leer los argumentos de la linea de comandos
import pandas as pd            # leer y limpiar el Excel

# Ruta del Excel dentro del contenedor (la carpeta ./data se monta en /data)
EXCEL_POR_DEFECTO = "/data/B09489-eng.xlsx"

# Instrucciones SQL que crean la tabla (solo si no existe todavia)
SQL_CREAR = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;              -- permite busquedas tolerantes a errores de escritura
CREATE TABLE IF NOT EXISTS antibiotics (
    id          SERIAL PRIMARY KEY,                  -- identificador interno autonumerico
    name        TEXT NOT NULL UNIQUE,                -- nombre mostrado, ej. "Colistin (IV)"
    base_name   TEXT NOT NULL,                       -- sustancia sin via, ej. "Colistin"
    route       TEXT,                                -- via de administracion: IV, oral o vacio
    class       TEXT,                                -- clase antibiotica (vacio en "no recomendados")
    atc_code    TEXT,                                -- codigo ATC (vacio en "no recomendados")
    category    TEXT NOT NULL                        -- clasificacion AWaRe
                CHECK (category IN ('Access','Watch','Reserve','Not recommended')),
    listed_eml  BOOLEAN NOT NULL DEFAULT FALSE,      -- esta en la Lista de Medicamentos Esenciales
    notes       TEXT                                 -- notas de la OMS (alternativas terapeuticas)
);
-- Indice trigram: acelera busquedas con ILIKE '%texto%' y por similitud
CREATE INDEX IF NOT EXISTS idx_antibiotics_name_trgm ON antibiotics USING gin (lower(base_name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_antibiotics_atc ON antibiotics (atc_code);  -- busqueda por codigo ATC
CREATE TABLE IF NOT EXISTS antibiotic_aliases (      -- nombres alternativos (espanol, marcas)
    alias       TEXT NOT NULL,                       -- texto que escribe el usuario (minusculas, sin acentos)
    base_name   TEXT NOT NULL,                       -- sustancia a la que apunta (antibiotics.base_name)
    PRIMARY KEY (alias, base_name)                   -- evita duplicados
);
CREATE INDEX IF NOT EXISTS idx_aliases_trgm ON antibiotic_aliases USING gin (alias gin_trgm_ops);  -- busqueda por parecido
"""

# Ruta del CSV con nombres en espanol (tambien admite marcas comerciales)
ALIAS_POR_DEFECTO = "/data/aliases_es.csv"


def limpiar_texto(valor):
    """Quita espacios normales y no separables (\\xa0) de un texto; vacio -> None."""
    if pd.isna(valor):                                   # si la celda esta vacia
        return None                                      # devuelve nulo
    texto = str(valor).replace("\xa0", " ").strip()      # cambia \xa0 por espacio y recorta
    return texto or None                                 # texto vacio se vuelve nulo


def separar_via(nombre):
    """Separa 'Colistin_IV' en ('Colistin', 'IV'); si no hay via devuelve (nombre, None)."""
    coincide = re.match(r"^(.*)_(IV|oral)$", nombre)     # busca el sufijo _IV o _oral
    if coincide:                                         # si lo encontro
        return coincide.group(1), coincide.group(2)      # devuelve sustancia y via
    return nombre, None                                  # si no, sin via


def extraer_y_transformar(ruta_excel):
    """Lee el Excel y devuelve una lista de diccionarios listos para insertar."""
    # Hoja principal: la fila 4 (indice 3) contiene los encabezados
    principal = pd.read_excel(ruta_excel, sheet_name="AWaRe classification 2025", header=3)
    principal = principal.iloc[:, :6].dropna(subset=["Antibiotic"])  # solo 6 columnas, sin filas vacias
    filas = []                                           # aqui se acumulan los registros
    for _, r in principal.iterrows():                    # recorre cada antibiotico
        original = limpiar_texto(r["Antibiotic"])        # nombre tal como viene del Excel
        base, via = separar_via(original)                # separa sustancia y via
        nombre = f"{base} ({via})" if via else base      # nombre para mostrar
        filas.append({
            "name": nombre,                              # nombre mostrado
            "base_name": base,                           # sustancia sin via
            "route": via,                                # IV, oral o None
            "class": limpiar_texto(r["Class"]),          # clase antibiotica
            "atc_code": None if limpiar_texto(r["ATC code"]) == "not assigned" else limpiar_texto(r["ATC code"]),  # "not assigned" -> vacio
            "category": limpiar_texto(r["Category"]),    # Access / Watch / Reserve
            "listed_eml": limpiar_texto(r["Listed on EML/EMLc 2025"]) == "Yes",  # Yes -> True
            "notes": limpiar_texto(r["Notes"]) if str(r["Notes"]).startswith("*") else None,  # ignora los ceros
        })
    # Hoja "Not recommended": combinaciones que la OMS no recomienda
    no_rec = pd.read_excel(ruta_excel, sheet_name="Not recommended", header=3)
    for valor in no_rec.iloc[:, 0].dropna():             # primera columna = nombre
        nombre = limpiar_texto(valor)                    # quita espacios sobrantes
        nombre = nombre[0].upper() + nombre[1:]          # primera letra en mayuscula
        filas.append({
            "name": nombre, "base_name": nombre, "route": None,  # sin via
            "class": None, "atc_code": None,             # estas hojas no traen clase ni ATC
            "category": "Not recommended",               # categoria propia
            "listed_eml": False, "notes": None,          # nunca estan en la EML
        })
    return filas                                         # lista completa (268 + 116 = 384)


def leer_alias(ruta_csv, filas):
    """Lee el CSV de alias y descarta los que apunten a una sustancia que no existe."""
    if not os.path.exists(ruta_csv):                     # el archivo es opcional
        print("Sin archivo de alias; se omite.")         # avisa y continua
        return []                                        # lista vacia
    validos = {f["base_name"] for f in filas}            # sustancias que si existen en la BD
    alias = []                                           # aqui se acumulan los alias buenos
    for _, r in pd.read_csv(ruta_csv, dtype=str).dropna().iterrows():  # recorre el CSV
        texto = limpiar_texto(r["alias"]).lower()        # alias en minusculas y sin espacios sobrantes
        base = limpiar_texto(r["base_name"])             # sustancia destino
        if base in validos:                              # solo si existe en la tabla
            alias.append({"alias": texto, "base_name": base})
        else:                                            # si no existe, avisa para corregir el CSV
            print(f"  AVISO: alias '{texto}' apunta a '{base}', que no existe")
    return alias                                         # alias listos para insertar


def cargar(filas, alias, url_bd):
    """Borra el contenido anterior y carga las filas en PostgreSQL (todo o nada)."""
    import psycopg2                                      # se importa aqui para que --dry-run no lo necesite
    conexion = psycopg2.connect(url_bd)                  # abre la conexion a la BD AWaRe
    try:
        with conexion:                                   # transaccion: si algo falla, se revierte
            with conexion.cursor() as cur:               # cursor para ejecutar SQL
                cur.execute(SQL_CREAR)                   # crea tabla e indices si faltan
                cur.execute("TRUNCATE antibiotics RESTART IDENTITY")  # vacia para poder repetir la carga
                cur.execute("TRUNCATE antibiotic_aliases")            # vacia tambien los alias
                for f in filas:                          # inserta cada registro
                    cur.execute(
                        """INSERT INTO antibiotics
                           (name, base_name, route, class, atc_code, category, listed_eml, notes)
                           VALUES (%(name)s, %(base_name)s, %(route)s, %(class)s,
                                   %(atc_code)s, %(category)s, %(listed_eml)s, %(notes)s)""",
                        f,                               # los valores van por parametro (evita inyeccion SQL)
                    )
                for a in alias:                          # inserta cada alias
                    cur.execute(
                        "INSERT INTO antibiotic_aliases (alias, base_name) VALUES (%(alias)s, %(base_name)s) ON CONFLICT DO NOTHING",
                        a,                               # valores por parametro (seguro)
                    )
                print(f"  Alias cargados: {len(alias)}") # resumen de alias
                cur.execute("SELECT category, COUNT(*) FROM antibiotics GROUP BY category ORDER BY 1")
                for categoria, total in cur.fetchall():  # muestra el resumen guardado en la BD
                    print(f"  {categoria}: {total}")
    finally:
        conexion.close()                                 # siempre cierra la conexion


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv                        # modo prueba: no toca la BD
    ruta = EXCEL_POR_DEFECTO                             # ruta por defecto
    if "--file" in sys.argv:                             # permite indicar otro archivo
        ruta = sys.argv[sys.argv.index("--file") + 1]
    datos = extraer_y_transformar(ruta)                  # lee y limpia el Excel
    ruta_alias = sys.argv[sys.argv.index("--aliases") + 1] if "--aliases" in sys.argv else ALIAS_POR_DEFECTO
    alias = leer_alias(ruta_alias, datos)                # lee y valida los nombres en espanol
    print(f"Registros listos: {len(datos)} | Alias listos: {len(alias)}")  # cuantos se van a cargar
    if dry:                                              # en prueba solo muestra un resumen
        resumen = pd.DataFrame(datos).category.value_counts()
        print(resumen.to_string())
    else:                                                # en modo normal carga en la BD
        cargar(datos, alias, os.environ["DATABASE_URL"]) # URL viene de docker-compose
        print("Carga completada.")