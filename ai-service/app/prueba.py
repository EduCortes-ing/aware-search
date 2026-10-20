# =====================================================================
# ai-service / prueba.py
# Prueba automatica del servicio de IA. Se ejecuta DENTRO del contenedor:
#   docker compose exec ai-service python -m app.prueba
# Primero prueba los candados (no necesitan el modelo) y despues, si el
# modelo ya se descargo, hace consultas reales a la IA.
# =====================================================================

import json                                          # leer y enviar JSON
import re                                            # extraer numeros del calculo
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


def numeros(texto):
    """'25 a 50 mg' -> [25.0, 50.0] (quita separadores de miles)."""
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", texto.replace(",", ""))]


print("Probando el servicio de IA...\n")
print("Candados de seguridad (no usan el modelo):")

estado, salud = pedir("/health")
comprobar("Servicio vivo (/health 200)", estado == 200, estado)

estado, r = pedir("/dosis", {"name": "Amoxicillin", "edad": 5})
comprobar("Niño sin peso rechazado (422)", estado == 422, estado)

estado, r = pedir("/dosis", {"name": "Amoxicillin", "edad": 0.5, "unidad_edad": "meses", "peso_kg": 3.5})
comprobar("Recién nacido: no se sugiere dosis (422)", estado == 422, estado)

estado, r = pedir("/dosis", {"name": "Meropenem/sulbactam", "edad": 30})
comprobar("'No recomendado' por la OMS: sin dosis (409)", estado == 409, estado)

estado, r = pedir("/uso", {"name": "Antibiotico Inventado"})
comprobar("Antibiótico inexistente (404)", estado == 404, estado)

estado, r = pedir("/dosis", {"name": "Amoxicillin", "edad": 30, "unidad_edad": "anios", "peso_kg": 900})
comprobar("Peso absurdo rechazado (422)", estado == 422, estado)

if not salud.get("modelo_listo"):                    # el modelo aun se descarga
    print(f"\nConsultas reales OMITIDAS: {salud.get('detalle')}")
    print("Espera a que termine la descarga y vuelve a correr esta prueba.")
else:
    print(f"\nConsultas reales al modelo {salud.get('modelo')} (pueden tardar):")

    estado, r = pedir("/uso", {"name": "Amoxicillin"})
    comprobar("¿Para qué sirve? Amoxicillin responde (200)",
              estado == 200 and len(r.get("uso", {}).get("para_que_sirve", "")) > 10, estado)
    if estado == 200:
        print("        →", r["uso"]["para_que_sirve"][:120])

    estado, r = pedir("/dosis", {"name": "Amoxicillin", "edad": 5, "peso_kg": 18, "indicacion": "otitis media"})
    comprobar("Dosis niño 5 años / 18 kg responde (200)", estado == 200, estado)
    if estado == 200 and r["confiable"]:
        calc = r["dosis"]["calculo"]                 # ej. "80 a 90 mg/kg/día × 18 kg = 1,440 a 1,620 mg al día"
        n = numeros(calc.split("→")[0])              # [kmin, kmax, peso, diamin, diamax]
        ok = len(n) >= 5 and abs(n[0] * 18 - n[3]) < 1 and abs(n[1] * 18 - n[4]) < 1
        comprobar("Cálculo por peso hecho correctamente por Python", ok, calc)
        print("        →", r["dosis"]["por_toma"], r["dosis"]["frecuencia"], "|", calc)
    elif estado == 200:
        print("        → la IA no dio una dosis confiable:", r["advertencias"][0])

    estado, r = pedir("/dosis", {"name": "Azithromycin", "edad": 35})
    comprobar("Dosis adulto Azithromycin responde (200) con aviso WATCH",
              estado == 200 and any("WATCH" in a for a in r.get("advertencias", [])), estado)

print(f"\nResultado: {sum(resultados)}/{len(resultados)} comprobaciones correctas")
raise SystemExit(0 if all(resultados) else 1)
