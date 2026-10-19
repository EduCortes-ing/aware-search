# =====================================================================
# auth-service / prueba.py
# Prueba automatica del flujo completo. Se ejecuta DENTRO del contenedor:
#   docker compose exec auth-service python -m app.prueba
# Crea un usuario temporal y lo borra al terminar.
# =====================================================================

import json                                          # codificar y leer JSON
import secrets                                       # nombres aleatorios para la prueba
import urllib.error                                  # errores HTTP
import urllib.request                                # peticiones HTTP sin instalar nada

import jwt                                           # leer el token emitido

from app import core                                 # acceso directo a la BD para limpiar

BASE = "http://localhost:8001"                       # el propio servicio, desde dentro del contenedor
resultados = []                                      # (nombre, paso_o_fallo)


def post(ruta, cuerpo):
    """Envia un POST con JSON y devuelve (codigo_http, respuesta_json)."""
    peticion = urllib.request.Request(
        BASE + ruta, data=json.dumps(cuerpo).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion) as r:  # respuesta exitosa
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:              # respuesta con error (400, 401, 429...)
        return e.code, json.loads(e.read())


def comprobar(nombre, condicion, detalle=""):
    """Registra y muestra si una comprobacion paso."""
    resultados.append(condicion)
    print(("  OK    " if condicion else "  FALLA ") + nombre + (f"  [{detalle}]" if detalle and not condicion else ""))


usuario = "prueba_" + secrets.token_hex(3)           # usuario temporal unico
clave = "ClavePrueba" + secrets.token_hex(3) + "9"   # contrasena valida (letras y numeros)
codigo = core.crear_invitacion("prueba automatica", 1)  # invitacion temporal
print(f"Probando con el usuario temporal '{usuario}'...\n")

try:
    estado, r = post("/register", {"username": usuario, "password": "corta1", "invitation_code": codigo})
    comprobar("Contraseña débil rechazada (422)", estado == 422, estado)

    estado, r = post("/register", {"username": usuario, "password": clave, "invitation_code": "AWARE-falso"})
    comprobar("Código falso rechazado (400)", estado == 400, estado)

    estado, r = post("/register", {"username": usuario, "password": clave, "invitation_code": codigo})
    comprobar("Registro con código válido (201)", estado == 201, estado)

    estado, r = post("/register", {"username": usuario + "x", "password": clave, "invitation_code": codigo})
    comprobar("Código ya usado no sirve otra vez (400)", estado == 400, estado)

    estado, r = post("/login", {"username": usuario, "password": clave})
    comprobar("Login correcto (200) y trae token", estado == 200 and "access_token" in r, estado)
    if estado == 200:
        datos = jwt.decode(r["access_token"], core.secreto(), algorithms=["HS256"], issuer=core.EMISOR)
        comprobar("Token válido: usuario, rol y caducidad", datos["sub"] == usuario and datos["role"] == "user" and "exp" in datos)

    estado, r = post("/login", {"username": "no_existe_" + secrets.token_hex(2), "password": clave})
    comprobar("Usuario inexistente: mismo mensaje genérico (401)", estado == 401 and r["detail"] == core.MSG_CREDENCIALES, estado)

    for _ in range(core.MAX_INTENTOS):               # 5 contrasenas incorrectas seguidas
        estado, r = post("/login", {"username": usuario, "password": "incorrecta123"})
    comprobar("Contraseña incorrecta (401)", estado == 401, estado)

    estado, r = post("/login", {"username": usuario, "password": clave})
    comprobar("Tras 5 fallos la cuenta se bloquea (429) aun con la clave correcta", estado == 429, estado)
finally:
    with core.cursor() as cur:                       # limpia los datos de la prueba
        cur.execute("DELETE FROM invitations WHERE note = 'prueba automatica'")
        cur.execute("DELETE FROM users WHERE username = %s", (usuario,))
    print("\nUsuario e invitación temporales eliminados.")

print(f"\nResultado: {sum(resultados)}/{len(resultados)} comprobaciones correctas")
raise SystemExit(0 if all(resultados) else 1)