# =====================================================================
# api / main.py
# API REST: unica puerta de entrada del sistema (Swagger en /api/docs).
#   /login, /register -> se reenvian al microservicio de autenticacion
#   /search           -> exige token JWT valido y consulta la busqueda
#   /ai/uso           -> exige token JWT y consulta al servicio de IA
# =====================================================================

import os                                            # leer variables de entorno
from typing import Optional                          # tipos opcionales

import httpx                                         # cliente HTTP para llamar a los microservicios
import jwt                                           # validar tokens JWT (PyJWT)
from fastapi import Depends, FastAPI, HTTPException, Query, Request  # framework web
from fastapi.responses import JSONResponse           # respuestas JSON con codigo propio
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # lee "Authorization: Bearer ..."
from pydantic import BaseModel, Field                # validacion de los datos que llegan

AUTH_URL = os.environ["AUTH_URL"]                    # direccion interna del servicio de auth
SEARCH_URL = os.environ["SEARCH_URL"]                # direccion interna del servicio de busqueda
AI_URL = os.environ.get("AI_URL", "http://ai-service:8003")  # direccion interna del servicio de IA
JWT_SECRET = os.environ.get("JWT_SECRET", "")        # misma clave con la que auth firma los tokens
if len(JWT_SECRET) < 32:                             # una clave corta se puede adivinar
    raise RuntimeError("JWT_SECRET debe tener al menos 32 caracteres")

ALGORITMO = "HS256"                                  # algoritmo permitido (se fija: nunca lo elige el token)
EMISOR = "aware-auth"                                # emisor esperado en el token
TIEMPO_MAXIMO = httpx.Timeout(5.0)                   # si un microservicio tarda mas de 5 s, se corta
TIEMPO_IA = httpx.Timeout(170.0, connect=5.0)        # la IA en CPU puede tardar hasta ~2.5 min

# root_path="/api": Nginx publica esta API bajo /api, asi Swagger encuentra sus rutas
app = FastAPI(
    title="Buscador AWaRe - API",
    description="Clasificación AWaRe (OMS 2025). Inicia sesión en /login y pulsa Authorize con el token.",
    root_path="/api",
)

esquema = HTTPBearer(auto_error=False)               # extrae el token; el error lo controlamos nosotros


@app.middleware("http")
async def cabeceras_de_seguridad(peticion: Request, siguiente):
    """Agrega cabeceras de seguridad a todas las respuestas."""
    respuesta = await siguiente(peticion)            # procesa la peticion normalmente
    respuesta.headers["X-Content-Type-Options"] = "nosniff"   # el navegador no adivina tipos de archivo
    respuesta.headers["X-Frame-Options"] = "DENY"             # impide incrustar la pagina en otro sitio
    respuesta.headers["Referrer-Policy"] = "no-referrer"      # no filtra la URL de origen
    respuesta.headers["Cache-Control"] = "no-store"           # no guarda en cache datos sensibles
    return respuesta


def usuario_actual(credenciales: Optional[HTTPAuthorizationCredentials] = Depends(esquema)):
    """Valida el token JWT y devuelve sus datos; si no es valido, responde 401."""
    cabecera = {"WWW-Authenticate": "Bearer"}        # estandar HTTP para pedir un token
    if credenciales is None:                         # no mando token
        raise HTTPException(401, "Falta el token de acceso", headers=cabecera)
    try:
        return jwt.decode(
            credenciales.credentials,                # el token recibido
            JWT_SECRET,                              # verifica la firma con nuestra clave
            algorithms=[ALGORITMO],                  # solo HS256 (bloquea el truco "alg: none")
            issuer=EMISOR,                           # debe venir de nuestro servicio de auth
            options={"require": ["exp", "iat", "iss", "sub"]},  # estos datos son obligatorios
        )
    except jwt.ExpiredSignatureError:                # token vencido
        raise HTTPException(401, "La sesión expiró, inicia sesión de nuevo", headers=cabecera)
    except jwt.PyJWTError:                           # firma falsa, emisor distinto, formato roto...
        raise HTTPException(401, "Token inválido", headers=cabecera)


async def llamar(metodo: str, url: str, tiempo=TIEMPO_MAXIMO, **parametros):
    """Llama a un microservicio interno y devuelve su respuesta tal cual."""
    try:
        async with httpx.AsyncClient(timeout=tiempo) as cliente:  # cliente con tiempo limite
            respuesta = await cliente.request(metodo, url, **parametros)  # hace la llamada
        cuerpo = respuesta.json()                    # el microservicio siempre responde JSON
    except (httpx.HTTPError, ValueError):            # no responde, se agoto el tiempo o no es JSON
        raise HTTPException(502, "Servicio no disponible, intenta más tarde")
    return JSONResponse(status_code=respuesta.status_code, content=cuerpo)  # conserva codigo y mensaje


class DatosLogin(BaseModel):
    """Cuerpo de /login."""
    username: str = Field(..., max_length=64)        # usuario
    password: str = Field(..., max_length=128)       # contrasena


class DatosRegistro(BaseModel):
    """Cuerpo de /register."""
    username: str = Field(..., max_length=64)        # usuario nuevo
    password: str = Field(..., max_length=128)       # contrasena nueva
    invitation_code: str = Field(..., max_length=64) # codigo entregado por el administrador


@app.get("/health")
def health():
    """Ruta publica para comprobar que la API esta viva."""
    return {"service": "api", "status": "ok"}


@app.post("/login")
async def login(datos: DatosLogin):
    """Inicia sesion: devuelve un token JWT si las credenciales son correctas."""
    return await llamar("POST", f"{AUTH_URL}/login", json=datos.model_dump())


@app.post("/register", status_code=201)
async def register(datos: DatosRegistro):
    """Crea una cuenta con un codigo de invitacion."""
    return await llamar("POST", f"{AUTH_URL}/register", json=datos.model_dump())


@app.get("/me")
def me(usuario: dict = Depends(usuario_actual)):
    """Devuelve quien es el dueno del token (el frontend lo usa para saludar)."""
    return {"username": usuario["sub"], "role": usuario.get("role", "user")}


@app.get("/search")
async def search(
    q: str = Query(..., min_length=2, max_length=60, description="Sustancia, clase o código ATC"),
    limit: int = Query(25, ge=1, le=100, description="Máximo de resultados"),
    _usuario: dict = Depends(usuario_actual),        # sin token valido ni se llega a buscar
):
    """Busca antibioticos y devuelve su clasificacion AWaRe (requiere sesion)."""
    return await llamar("GET", f"{SEARCH_URL}/search", params={"q": q, "limit": limit})


# ---------------------------------------------------------------------
# Inteligencia artificial (Ollama local, a traves de ai-service)
# ---------------------------------------------------------------------
class PeticionUso(BaseModel):
    """Cuerpo de /ai/uso."""
    name: str = Field(..., min_length=2, max_length=120, description="Nombre exacto del antibiótico")


@app.post("/ai/uso")
async def ai_uso(datos: PeticionUso, _usuario: dict = Depends(usuario_actual)):
    """IA: para que sirve el antibiotico (requiere sesion)."""
    return await llamar("POST", f"{AI_URL}/uso", tiempo=TIEMPO_IA, json=datos.model_dump())
