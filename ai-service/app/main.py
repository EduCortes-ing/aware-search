# =====================================================================
# ai-service / main.py
# Microservicio de IA local (Ollama). Dos consultas:
#   POST /uso   -> para que sirve el antibiotico
#   POST /dosis -> dosis sugerida segun edad y peso
# Reglas de seguridad aplicadas EN CODIGO (no dependen de que la IA obedezca):
#   - los datos del antibiotico salen de la BD oficial (search-service), no del usuario
#   - menores de 12 anos: el peso es obligatorio y Python hace la multiplicacion
#   - recien nacidos (< 1 mes): no se sugiere dosis
#   - "Not recommended": no se sugiere dosis
#   - valores absurdos o incoherentes -> la respuesta se marca como NO confiable
# Solo es accesible dentro de la red interna de Docker (via la API).
# =====================================================================

import json                                          # leer la respuesta JSON del modelo
import os                                            # leer variables de entorno
import re                                            # limpiar el texto de la indicacion
import threading                                     # descarga del modelo en segundo plano y turnos
from typing import List, Literal, Optional           # tipos para documentar datos

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
    "num_predict": 500,                              # tope de palabras generadas (respuestas cortas)
    "num_ctx": 2048,                                 # memoria de contexto suficiente y ligera
}
AVISO = ("Sugerencia generada por IA con fines de apoyo. Verifique siempre con la receta médica "
         "y la información para prescribir antes de dispensar.")              # se muestra en cada respuesta

turno = threading.BoundedSemaphore(1)                # solo una consulta al modelo a la vez (CPU limitada)
estado = {"listo": False, "detalle": "verificando modelo"}  # estado de la descarga del modelo
candado_estado = threading.Lock()                    # evita dos descargas al mismo tiempo

app = FastAPI(title="ai-service", description="IA local (Ollama): uso y dosis sugerida de antibióticos")


class ErrorIA(Exception):
    """Error controlado: se convierte en respuesta JSON con su codigo HTTP."""
    def __init__(self, estado_http: int, mensaje: str):
        self.estado = estado_http                    # codigo HTTP (404, 409, 422, 503...)
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


def limpiar_indicacion(texto: Optional[str]) -> Optional[str]:
    """Texto libre del usuario: sin saltos de linea, comillas ni simbolos raros (evita inyeccion de prompt)."""
    if not texto:
        return None
    texto = re.sub(r"[^\w\s.,/()áéíóúüñÁÉÍÓÚÜÑ-]", " ", texto)  # solo letras, numeros y puntuacion basica
    texto = re.sub(r"\s+", " ", texto).strip()[:80]           # espacios simples, maximo 80 caracteres
    return texto or None


def numero(valor: float, unidad: str) -> str:
    """Formatea cantidades: '12.5 mg', '1,500 mg', '300,000 UI'."""
    if unidad == "mg" and valor < 10:
        texto = f"{valor:.1f}".rstrip("0").rstrip(".")       # una decimal para dosis pequenas
    else:
        texto = f"{round(valor):,}"                          # enteros con separador de miles
    return f"{texto} {unidad}"


def rango(a: float, b: float, unidad: str) -> str:
    """'250 mg' si son iguales; '250 a 500 mg' si es rango."""
    if abs(a - b) < 1e-9:
        return numero(a, unidad)
    return f"{numero(a, unidad).rsplit(' ', 1)[0]} a {numero(b, unidad)}"


def frecuencia(tomas: int) -> str:
    """3 -> 'cada 8 horas'; 1 -> 'una vez al día'."""
    if tomas == 1:
        return "una vez al día"
    if 24 % tomas == 0:
        return f"cada {24 // tomas} horas"
    return f"{tomas} veces al día"


# ---------------------------------------------------------------------
# Salidas esperadas del modelo (si no cumplen, la respuesta se rechaza)
# ---------------------------------------------------------------------
class SalidaUso(BaseModel):
    para_que_sirve: str
    infecciones_frecuentes: List[str] = []
    precauciones: List[str] = []


class SalidaDosis(BaseModel):
    unidad: Literal["mg", "UI"] = "mg"
    tomas_al_dia: int
    via: str = ""
    duracion: str = ""
    dosis_max_dia: float = 0
    notas: List[str] = []


class SalidaDosisNino(SalidaDosis):
    por_kg_dia_min: float
    por_kg_dia_max: float


class SalidaDosisAdulto(SalidaDosis):
    por_toma_min: float
    por_toma_max: float


# Topes de sentido comun: si la IA los rebasa, la respuesta NO es confiable
TOPE_KG_DIA = {"mg": 400, "UI": 500_000}             # por kg al dia (ninos)
TOPE_TOMA = {"mg": 8_000, "UI": 12_000_000}          # por toma (adultos)


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------
class PeticionUso(BaseModel):
    name: str = Field(..., min_length=2, max_length=120, description="Nombre exacto del antibiótico")


class PeticionDosis(BaseModel):
    name: str = Field(..., min_length=2, max_length=120, description="Nombre exacto del antibiótico")
    edad: float = Field(..., ge=0, le=1440, description="Edad del paciente")
    unidad_edad: Literal["anios", "meses"] = "anios"                    # unidad de la edad
    peso_kg: Optional[float] = Field(None, gt=0, le=300, description="Obligatorio en menores de 12 años")
    indicacion: Optional[str] = Field(None, max_length=80, description="Ej. otitis, faringitis")


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


@app.post("/dosis")
def dosis(datos: PeticionDosis):
    """Sugiere una dosis segun edad y peso, con calculos y candados en Python."""
    # --- 1. Edad y validaciones que NO dependen de la IA ---
    if datos.unidad_edad == "anios" and datos.edad > 120:
        raise ErrorIA(422, "Edad fuera de rango (máximo 120 años)")
    edad_anios = datos.edad / 12 if datos.unidad_edad == "meses" else datos.edad  # todo en anos
    if edad_anios < 1 / 12:                          # menos de un mes de vida
        raise ErrorIA(422, "Recién nacidos (menos de 1 mes): la dosis debe indicarla el médico "
                           "según edad gestacional y función renal. La IA no sugiere dosis.")
    pediatrico = edad_anios < 12                     # criterio de dosis por peso
    if pediatrico and not datos.peso_kg:
        raise ErrorIA(422, "Para menores de 12 años indica el peso en kg: la dosis se calcula por peso.")

    ab = obtener_antibiotico(datos.name)             # datos oficiales
    if ab["category"] == "Not recommended":
        raise ErrorIA(409, "La OMS no recomienda esta combinación; no se sugiere dosis.")

    indicacion = limpiar_indicacion(datos.indicacion)  # texto libre saneado
    prompt = prompts.prompt_dosis(ab, edad_anios, datos.peso_kg, indicacion, pediatrico)
    esquema = prompts.ESQUEMA_DOSIS_NINO if pediatrico else prompts.ESQUEMA_DOSIS_ADULTO
    crudo = preguntar_ia(prompt, esquema)            # consulta al modelo
    try:
        ia = (SalidaDosisNino if pediatrico else SalidaDosisAdulto)(**crudo)  # valida la forma
    except (ValidationError, TypeError):
        raise ErrorIA(502, "La IA respondió en un formato inválido; intenta de nuevo")

    # --- 2. Advertencias que pone el sistema (no la IA) ---
    advertencias = []
    if ab["category"] == "Reserve":
        advertencias.append("Antibiótico de RESERVA (OMS): solo con indicación de especialista "
                            "y, de preferencia, antibiograma.")
    elif ab["category"] == "Watch":
        advertencias.append("Antibiótico WATCH (OMS): uso vigilado; confirma que la indicación lo justifique.")
    if edad_anios >= 65:
        advertencias.append("Adulto mayor: valorar función renal antes de dispensar.")
    if not pediatrico and datos.peso_kg and datos.peso_kg < 40:
        advertencias.append("Peso menor a 40 kg: considerar dosis por kg.")

    unidad = ia.unidad                               # mg o UI
    tomas = ia.tomas_al_dia
    confiable = True                                 # se apaga si algo no cuadra
    motivo = None                                    # por que no es confiable
    tope = ia.dosis_max_dia if ia.dosis_max_dia > 0 else None  # tope diario (si la IA lo dio)
    if not 1 <= tomas <= 6:                          # nadie toma un antibiotico 0 o 12 veces al dia
        confiable, motivo = False, "número de tomas al día fuera de rango"

    # --- 3. Calculos hechos por Python ---
    if pediatrico:
        kmin, kmax = ia.por_kg_dia_min, ia.por_kg_dia_max
        if kmin <= 0 or kmax <= 0:                   # la IA dijo que no hay dosis establecida
            confiable, motivo = False, "la IA no tiene una dosis establecida para esta edad"
        elif kmin > kmax or kmax > TOPE_KG_DIA[unidad]:
            confiable, motivo = False, "rango por kg incoherente o excesivo"
        dia_min, dia_max = kmin * datos.peso_kg, kmax * datos.peso_kg       # dosis diaria = rango x peso
        calculo = (f"{rango(kmin, kmax, unidad)}/kg/día × {datos.peso_kg:g} kg "
                   f"= {rango(dia_min, dia_max, unidad)} al día")
        if confiable and tope and dia_max > tope:    # nunca pasar la dosis maxima diaria
            dia_min, dia_max = min(dia_min, tope), tope
            calculo += f" → limitado al máximo diario de {numero(tope, unidad)}"
        toma_min, toma_max = (dia_min / tomas, dia_max / tomas) if tomas else (0, 0)
    else:
        toma_min, toma_max = ia.por_toma_min, ia.por_toma_max
        if toma_min <= 0 or toma_max <= 0:
            confiable, motivo = False, "la IA no tiene una dosis establecida para este caso"
        elif toma_min > toma_max or toma_max > TOPE_TOMA[unidad]:
            confiable, motivo = False, "dosis por toma incoherente o excesiva"
        dia_min, dia_max = toma_min * tomas, toma_max * tomas                # dosis diaria = toma x tomas
        palabra = "toma" if tomas == 1 else "tomas"  # singular o plural
        calculo = f"{rango(toma_min, toma_max, unidad)} × {tomas} {palabra} = {rango(dia_min, dia_max, unidad)} al día"
        if confiable and tope and dia_max > tope * 1.001:  # la propia IA se contradice
            confiable, motivo = False, "la dosis diaria supera el máximo que indicó la misma IA"

    if not confiable:                                # no se muestran numeros dudosos
        advertencias.insert(0, f"Sugerencia NO confiable ({motivo}). Consulta la ficha técnica o al médico.")

    return {
        "antibiotico": ab,
        "paciente": {
            "edad_anios": round(edad_anios, 2),
            "peso_kg": datos.peso_kg,
            "pediatrico": pediatrico,
            "indicacion": indicacion,
        },
        "confiable": confiable,
        "dosis": None if not confiable else {
            "por_toma": rango(toma_min, toma_max, unidad),
            "frecuencia": frecuencia(tomas),
            "al_dia": rango(dia_min, dia_max, unidad),
            "via": ia.via.strip()[:40] or "—",
            "duracion": ia.duracion.strip()[:60] or "—",
            "dosis_max_dia": numero(tope, unidad) if tope else "—",
            "calculo": calculo,              # muestra como se obtuvo (verificable)
        },
        "advertencias": advertencias,
        "notas_ia": limpiar_lista(ia.notas, 3),
        "aviso": AVISO,
        "modelo": MODELO,
    }
