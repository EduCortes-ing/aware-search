# =====================================================================
# auth-service / main.py
# Endpoints HTTP: /register (con codigo de invitacion), /login y /health.
# Solo es accesible dentro de la red interna de Docker (via la API).
# =====================================================================

from fastapi import FastAPI                          # framework web
from fastapi.responses import JSONResponse           # respuestas JSON con codigo propio
from pydantic import BaseModel, Field                # validacion de los datos que llegan

from app import core                                 # logica de seguridad

app = FastAPI(title="auth-service", description="Registro con invitacion, login y tokens JWT")


@app.on_event("startup")
def al_arrancar():
    """Valida la clave JWT y crea las tablas al arrancar."""
    core.secreto()                                   # falla pronto si JWT_SECRET es corta
    core.crear_tablas()                              # crea users e invitations si faltan


@app.exception_handler(core.ErrorAuth)
async def manejar_error(_peticion, error: core.ErrorAuth):
    """Convierte los errores controlados en respuestas JSON limpias."""
    return JSONResponse(status_code=error.estado, content={"detail": error.mensaje})


class DatosRegistro(BaseModel):
    """Cuerpo de /register. Los limites evitan cargas gigantes."""
    username: str = Field(..., max_length=64)        # nombre de usuario
    password: str = Field(..., max_length=128)       # contrasena
    invitation_code: str = Field(..., max_length=64) # codigo entregado por el administrador


class DatosLogin(BaseModel):
    """Cuerpo de /login."""
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)


@app.get("/health")
def health():
    """Comprueba que el servicio esta vivo."""
    return {"service": "auth-service", "status": "ok"}


@app.post("/register", status_code=201)
def registrar(datos: DatosRegistro):
    """Crea una cuenta con un codigo de invitacion valido."""
    usuario = core.registrar(datos.username, datos.password, datos.invitation_code)
    return {"username": usuario, "message": "Cuenta creada. Ya puedes iniciar sesión."}


@app.post("/login")
def login(datos: DatosLogin):
    """Devuelve un token JWT si las credenciales son correctas."""
    return core.iniciar_sesion(datos.username, datos.password)