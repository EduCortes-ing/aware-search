# =====================================================================
# ai-service / prueba.py
# Prueba automatica del servicio de IA. Se ejecuta DENTRO del contenedor:
#   docker compose exec ai-service python -m app.prueba
# Primero prueba los controles (no necesitan el modelo) y despues, si el
# modelo ya se descargo, hace consultas reales a la IA.
# =====================================================================

import json                                          # leer y enviar JSON
import urllib.error                                  # errores HTTP
import urllib.request                                # peticiones HTTP sin librerias extra

BASE = "http://localhost:8003"                       # el propio servicio, desde dentro del contenedor
resultados = []                                      # True/False de cada comprobacion


def pedir(ruta, cuerpo=None):
    """GET si no hay cuerpo; POST JSON si lo hay. Devuelve (codigo, json)."""
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cabeceras = {"Content-Type": "application/json"} if cuerpo is not None else {}
    peticion = urllib.request.Request(BASE + ruta, data=datos, headers=cabeceras,
                                      method="POST" if cuerpo is not None else "GET")
    try:
        with urllib.request.urlopen(peticion, timeout=200) as r:  # la IA puede tardar
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:              # respuestas 4xx / 5xx
        return e.code, json.loads(e.read())


def comprobar(nombre, condicion, detalle=""):
    """Anota y muestra si una comprobacion paso."""
    resultados.append(bool(condicion))
    print(("  OK    " if condicion else "  FALLA ") + nombre + (f"  [{detalle}]" if not condicion else ""))


print("Probando el servicio de IA...\n")
print("Controles (no usan el modelo):")

estado, salud = pedir("/health")
comprobar("Servicio vivo (/health 200)", estado == 200, estado)

estado, r = pedir("/uso", {"name": "Antibiotico Inventado"})
comprobar("Antibiótico inexistente (404)", estado == 404, estado)

estado, r = pedir("/uso", {"name": "x"})
comprobar("Nombre demasiado corto rechazado (422)", estado == 422, estado)

estado, r = pedir("/dosis", {"name": "Amoxicillin", "edad": 30})
comprobar("La dosis por IA está retirada (404)", estado == 404, estado)

if not salud.get("modelo_listo"):                    # el modelo aun se descarga
    print(f"\nConsultas reales OMITIDAS: {salud.get('detalle')}")
    print("Espera a que termine la descarga y vuelve a correr esta prueba.")
else:
    print(f"\nConsultas reales al modelo {salud.get('modelo')} (pueden tardar):")
    for nombre in ["Amoxicillin", "Meropenem/sulbactam"]:   # uno normal y uno "No recomendado"
        estado, r = pedir("/uso", {"name": nombre})
        uso = r.get("uso", {}) if estado == 200 else {}
        comprobar(f"¿Para qué sirve? {nombre} responde (200)",
                  estado == 200 and len(uso.get("para_que_sirve", "")) > 10, estado)
        if uso:
            print("        →", uso["para_que_sirve"][:160])
            print("          Se usa en:", ", ".join(uso["infecciones_frecuentes"]) or "—")

print(f"\nResultado: {sum(resultados)}/{len(resultados)} comprobaciones correctas")
raise SystemExit(0 if all(resultados) else 1)
