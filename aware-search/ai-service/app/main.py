# =====================================================================
# ai-service / main.py
# Microservicio de IA local (Ollama):
#   POST /uso -> para que sirve el antibiotico
# Los datos del antibiotico salen de la BD oficial de la OMS (search-service),
# nunca del texto del usuario; la IA solo los explica en lenguaje sencillo.
# Nota: la dosis sugerida por IA se retiro porque, en pruebas con el modelo
# real, dio dosis incorrectas. Se agregara despues desde una tabla verificada.
# Solo es accesible dentro de la red interna de Docker (via la API).
# =====================================================================

import json                                          # leer la respuesta JSON del modelo
import os                                            # leer variables de entorno
import threading                                     # descarga del modelo en segundo plano y turnos
from typing import List                              # tipos para documentar datos

import httpx                                         # cliente HTTP para hablar con Ollama y search-service
from fastapi import FastAPI                          # framework web (Swagger automatico)
from fastapi.responses import JSONResponse           # respuestas de error con codigo propio
from pydantic import BaseModel, Field, ValidationError  # validacion de entradas y de la salida de la IA

from app import prompts                              # instrucciones KERNEL para el modelo

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")      # contenedor de Ollama
MODELO = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")                 # modelo pequeno con buen espanol
SEARCH_URL = os.environ.get("SEARCH_URL", "http://search-service:8002")  # datos oficiales de la OMS

TIEMPO_IA = httpx.Timeout(150.0, connect=5.0)        # el modelo puede tardar en CPU (hasta 2.5 min)
TIEMPO_BD = httpx.Timeout(5.0)                       # la busqueda en BD debe ser rapida
OPCIONES_MODELO = {                                  # KERNEL - Reproducible
    "temperature": 0.1,                              # casi sin aleatoriedad: respuestas estables
    "seed": 42,                                      # misma semilla -> misma respuesta para la misma pregunta
    "num_predict": 400,                              # tope de palabras generadas (respuestas cortas)
    "num_ctx": 2048,                                 # memoria de contexto suficiente y ligera
}
AVISO = ("Información generada por IA con fines de apoyo; puede contener errores. "
         "No sustituye la receta médica ni la información para prescribir.")  # se muestra en cada respuesta

turno = threading.BoundedSemaphore(1)                # solo una consulta al modelo a la vez (CPU limitada)
estado = {"listo": False, "detalle": "verificando modelo"}  # estado de la descarga del modelo
candado_estado = threading.Lock()                    # evita dos descargas al mismo tiempo

app = FastAPI(title="ai-service", description="IA local (Ollama): para qué sirve cada antibiótico")


class ErrorIA(Exception):
    """Error controlado: se convierte en respuesta JSON con su codigo HTTP."""
    def __init__(self, estado_http: int, mensaje: str):
        self.estado = estado_http                    # codigo HTTP (404, 502, 503...)
        self.mensaje = mensaje                       # texto para el usuario


@app.exception_handler(ErrorIA)
async def manejar_error(_peticion, error: ErrorIA):
    """Convierte ErrorIA en {"detail": "..."} como el resto de servicios."""
    return JSONResponse(status_code=error.estado, content={"detail": error.mensaje})


# ---------------------------------------------------------------------
# Modelo: comprobar que exista y descargarlo si falta (solo la primera vez)
# ---------------------------------------------------------------------
def modelo_instalado() -> bool:
    """Pregunta a Ollama si el modelo ya esta descargado."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=TIEMPO_BD)  # lista de modelos instalados
        nombres = {m.get("name") for m in r.json().get("models", [])}
        return MODELO in nombres or f"{MODELO}:latest" in nombres  # acepta "modelo" o "modelo:latest"
    except (httpx.HTTPError, ValueError):            # Ollama aun no arranca
        return False


def descargar_modelo():
    """Descarga el modelo en segundo plano (puede tardar varios minutos la primera vez)."""
    if not candado_estado.acquire(blocking=False):   # ya hay una descarga en curso
        return
    try:
        if modelo_instalado():                       # ya estaba: nada que hacer
            estado.update(listo=True, detalle="listo")
            return
        estado.update(listo=False, detalle=f"descargando {MODELO} (solo la primera vez, unos minutos)")
        r = httpx.post(f"{OLLAMA_URL}/api/pull", json={"model": MODELO, "stream": False},
                       timeout=httpx.Timeout(None, connect=10.0))   # sin limite: el archivo pesa ~2 GB
        if r.status_code == 200 and modelo_instalado():
            estado.update(listo=True, detalle="listo")
        else:
            estado.update(listo=False, detalle=f"no se pudo descargar el modelo ({r.status_code})")
    except httpx.HTTPError as e:                     # sin red o Ollama apagado
        estado.update(listo=False, detalle=f"Ollama no disponible: {type(e).__name__}")
    finally:
        candado_estado.release()                     # libera para un reintento futuro


@app.on_event("startup")
def al_arrancar():
    """Al arrancar, revisa/descarga el modelo sin bloquear el servicio."""
    threading.Thread(target=descargar_modelo, daemon=True).start()


def exigir_modelo_listo():
    """Si el modelo aun no esta listo, responde 503 y reintenta la descarga."""
    if estado["listo"]:
        return
    if modelo_instalado():                           # quiza termino mientras tanto
        estado.update(listo=True, detalle="listo")
        return
    threading.Thread(target=descargar_modelo, daemon=True).start()  # reintento en segundo plano
    raise ErrorIA(503, f"La IA aún no está lista: {estado['detalle']}. Intenta en unos minutos.")


@app.get("/health")
def health():
    """Estado del servicio y del modelo."""
    return {"service": "ai-service", "status": "ok", "modelo": MODELO,
            "modelo_listo": estado["listo"], "detalle": estado["detalle"]}


# ---------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------
def obtener_antibiotico(nombre: str) -> dict:
    """Trae los datos oficiales del antibiotico desde search-service."""
    try:
        r = httpx.get(f"{SEARCH_URL}/detalle", params={"name": nombre}, timeout=TIEMPO_BD)
    except httpx.HTTPError:
        raise ErrorIA(502, "Servicio de búsqueda no disponible")
    if r.status_code == 404:                         # el nombre no existe en la BD
        raise ErrorIA(404, "Antibiótico no encontrado en la base de la OMS")
    if r.status_code != 200:
        raise ErrorIA(502, "Servicio de búsqueda no disponible")
    return r.json()                                  # name, drug_class, atc_code, category, notes...


def preguntar_ia(prompt: str, esquema: dict) -> dict:
    """Envia el prompt a Ollama y devuelve el JSON que responde el modelo."""
    exigir_modelo_listo()                            # 503 si aun se descarga
    if not turno.acquire(timeout=90):                # espera su turno hasta 90 s
        raise ErrorIA(503, "La IA está atendiendo otra consulta; intenta en un momento")
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", timeout=TIEMPO_IA, json={
            "model": MODELO,                         # modelo a usar
            "messages": [                            # conversacion de una sola pregunta
                {"role": "system", "content": prompts.SISTEMA},   # rol fijo
                {"role": "user", "content": prompt},              # pregunta con contexto
            ],
            "format": esquema,                       # KERNEL - Easy to verify: obliga a responder ese JSON
            "stream": False,                         # respuesta completa de una vez
            "options": OPCIONES_MODELO,              # temperatura y semilla fijas
            "keep_alive": "30m",                     # mantiene el modelo en memoria 30 min
        })
        r.raise_for_status()                         # error HTTP de Ollama
        return json.loads(r.json()["message"]["content"])  # texto del modelo -> diccionario
    except httpx.TimeoutException:
        raise ErrorIA(504, "La IA tardó demasiado en responder; intenta de nuevo")
    except (httpx.HTTPError, KeyError, ValueError):  # caido, formato roto o JSON invalido
        raise ErrorIA(502, "La IA no respondió correctamente; intenta de nuevo")
    finally:
        turno.release()                              # libera el turno siempre


def limpiar_lista(valores, maximo=5) -> List[str]:
    """Quita vacios y repetidos, recorta a 'maximo' elementos y 200 caracteres cada uno."""
    vistos, salida = set(), []
    for v in valores or []:
        texto = str(v).strip()[:200]                 # texto acotado
        if texto and texto.lower() not in vistos:    # sin vacios ni duplicados
            vistos.add(texto.lower())
            salida.append(texto)
    return salida[:maximo]


# ---------------------------------------------------------------------
# Salida esperada del modelo (si no cumple, la respuesta se rechaza)
# ---------------------------------------------------------------------
class SalidaUso(BaseModel):
    para_que_sirve: str
    infecciones_frecuentes: List[str] = []
    precauciones: List[str] = []


# ---------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------
class PeticionUso(BaseModel):
    name: str = Field(..., min_length=2, max_length=120, description="Nombre exacto del antibiótico")


@app.post("/uso")
def uso(datos: PeticionUso):
    """Explica para que sirve el antibiotico (con datos oficiales como contexto)."""
    ab = obtener_antibiotico(datos.name)             # contexto oficial de la OMS
    crudo = preguntar_ia(prompts.prompt_uso(ab), prompts.ESQUEMA_USO)  # consulta al modelo
    try:
        salida = SalidaUso(**crudo)                  # valida la forma de la respuesta
    except (ValidationError, TypeError):
        raise ErrorIA(502, "La IA respondió en un formato inválido; intenta de nuevo")
    return {
        "antibiotico": ab,
        "uso": {
            "para_que_sirve": salida.para_que_sirve.strip()[:600],
            "infecciones_frecuentes": limpiar_lista(salida.infecciones_frecuentes),
            "precauciones": limpiar_lista(salida.precauciones, 4),
        },
        "aviso": AVISO,
        "modelo": MODELO,
    }
