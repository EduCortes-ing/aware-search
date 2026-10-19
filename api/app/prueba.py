# =====================================================================
# api / prueba.py
# Prueba automatica de la seguridad de la API. Se ejecuta DENTRO del contenedor:
#   docker compose exec api python -m app.prueba
# Fabrica tokens de prueba (validos y falsos) y comprueba que la API los trate bien.
# =====================================================================

import json                                          # leer respuestas JSON
import os                                            # leer JWT_SECRET
import urllib.error                                  # errores HTTP
import urllib.parse                                  # codificar parametros
import urllib.request                                # peticiones HTTP sin instalar nada
from datetime import datetime, timedelta, timezone   # fechas de los tokens

import jwt                                           # fabricar tokens de prueba

BASE = "http://localhost:8000"                       # la propia API, desde dentro del contenedor
SECRETO = os.environ["JWT_SECRET"]                   # clave real del sistema
resultados = []                                      # guarda True/False de cada comprobacion


def token(clave=SECRETO, emisor="aware-auth", horas=1, algoritmo="HS256", con_exp=True):
    """Fabrica un token de prueba con los datos que se le indiquen."""
    ahora = datetime.now(timezone.utc)               # momento actual
    carga = {"sub": "prueba", "uid": 0, "role": "user", "iss": emisor, "iat": ahora}
    if con_exp:                                      # la mayoria de los tokens llevan caducidad
        carga["exp"] = ahora + timedelta(hours=horas)
    return jwt.encode(carga, clave, algorithm=algoritmo)


def pedir(ruta, tk=None, metodo="GET", cuerpo=None):
    """Hace una peticion y devuelve (codigo_http, json)."""
    cabeceras = {"Content-Type": "application/json"}
    if tk:                                           # si hay token, va en la cabecera Authorization
        cabeceras["Authorization"] = "Bearer " + tk
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(BASE + ruta, data=datos, headers=cabeceras, method=metodo)
    try:
        with urllib.request.urlopen(peticion) as r:  # respuesta correcta
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:              # respuesta con error (401, 422...)
        return e.code, json.loads(e.read())


def comprobar(nombre, condicion, detalle=""):
    """Anota y muestra si una comprobacion paso."""
    resultados.append(bool(condicion))
    print(("  OK    " if condicion else "  FALLA ") + nombre + (f"  [{detalle}]" if not condicion else ""))


consulta = "/search?" + urllib.parse.urlencode({"q": "azitromicina"})
print("Probando la seguridad de la API...\n")

estado, r = pedir("/health")
comprobar("Ruta pública /health responde (200)", estado == 200, estado)

estado, r = pedir(consulta)
comprobar("Búsqueda SIN token rechazada (401)", estado == 401, estado)

estado, r = pedir(consulta, token())
comprobar("Búsqueda con token válido (200) devuelve Azithromycin",
          estado == 200 and r.get("count") == 1 and r["results"][0]["category"] == "Watch", estado)

estado, r = pedir(consulta, token(horas=-1))
comprobar("Token vencido rechazado (401)", estado == 401, estado)

estado, r = pedir(consulta, token(clave="otra-clave-falsa-de-mas-de-32-caracteres-xx"))
comprobar("Token firmado con otra clave rechazado (401)", estado == 401, estado)

estado, r = pedir(consulta, token(emisor="otro-emisor"))
comprobar("Token de otro emisor rechazado (401)", estado == 401, estado)

estado, r = pedir(consulta, token(con_exp=False))
comprobar("Token sin caducidad rechazado (401)", estado == 401, estado)

estado, r = pedir(consulta, token(clave="", algoritmo="none"))
comprobar("Token sin firma ('alg: none') rechazado (401)", estado == 401, estado)

estado, r = pedir("/search?q=a", token())
comprobar("Texto de búsqueda demasiado corto rechazado (422)", estado == 422, estado)

estado, r = pedir("/me", token())
comprobar("/me devuelve el usuario del token", estado == 200 and r.get("username") == "prueba", estado)

estado, r = pedir("/login", metodo="POST", cuerpo={"username": "nadie_existe_xyz", "password": "ClaveInventada123"})
comprobar("Login con usuario inexistente: 401 genérico vía auth-service", estado == 401, estado)

print(f"\nResultado: {sum(resultados)}/{len(resultados)} comprobaciones correctas")
raise SystemExit(0 if all(resultados) else 1)