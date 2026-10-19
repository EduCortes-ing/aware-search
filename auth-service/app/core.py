# =====================================================================
# auth-service / core.py
# Logica de seguridad: contrasenas con hash (bcrypt), tokens JWT,
# codigos de invitacion, registro y login con bloqueo por intentos.
# =====================================================================

import hashlib                                       # sha256 para guardar los codigos de invitacion
import os                                            # leer variables de entorno
import re                                            # validar el nombre de usuario
import secrets                                       # generar codigos aleatorios seguros
from contextlib import contextmanager                # para el manejador de transacciones
from datetime import datetime, timedelta, timezone   # fechas del token

import bcrypt                                        # hash de contrasenas
import jwt                                           # tokens JWT (PyJWT)

ALGORITMO = "HS256"                                  # algoritmo de firma del token
DURACION_HORAS = 8                                   # el token caduca a las 8 horas
MAX_INTENTOS = 5                                     # intentos fallidos antes de bloquear
BLOQUEO_MINUTOS = 15                                 # minutos de bloqueo de la cuenta
EMISOR = "aware-auth"                                # quien emite el token (se valida despues)
PATRON_USUARIO = re.compile(r"^[a-z0-9._-]{3,32}$")  # usuario: minusculas, numeros, . _ -
MSG_CREDENCIALES = "Usuario o contraseña incorrectos"  # mensaje unico: no revela si el usuario existe


class ErrorAuth(Exception):
    """Error controlado con un codigo HTTP y un mensaje para el usuario."""

    def __init__(self, estado, mensaje):
        super().__init__(mensaje)                    # inicializa la excepcion base
        self.estado = estado                         # codigo HTTP (400, 401, 409...)
        self.mensaje = mensaje                       # texto que vera el usuario


def secreto():
    """Lee la clave JWT del entorno y exige que sea larga."""
    valor = os.environ.get("JWT_SECRET", "")         # lee JWT_SECRET (viene del archivo .env)
    if len(valor) < 32:                              # una clave corta se puede adivinar
        raise RuntimeError("JWT_SECRET debe tener al menos 32 caracteres")
    return valor                                     # devuelve la clave


# ---------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------
_pool = None                                         # pool de conexiones (se crea al primer uso)


def _obtener_pool():
    """Crea el pool de conexiones la primera vez y lo reutiliza despues."""
    global _pool                                     # modifica la variable de modulo
    if _pool is None:                                # si aun no existe
        from psycopg2.pool import ThreadedConnectionPool  # se importa aqui para poder probar sin psycopg2
        _pool = ThreadedConnectionPool(1, 5, os.environ["DATABASE_URL"])  # entre 1 y 5 conexiones
    return _pool                                     # devuelve el pool


@contextmanager
def cursor():
    """Transaccion: confirma si todo sale bien y revierte si hay una excepcion."""
    pool = _obtener_pool()                           # obtiene el pool
    conexion = pool.getconn()                        # toma una conexion
    try:
        with conexion.cursor() as cur:               # abre un cursor
            yield cur                                # entrega el cursor al codigo que lo pidio
        conexion.commit()                            # todo bien: guarda los cambios
    except Exception:
        conexion.rollback()                          # algo fallo: deshace los cambios
        raise                                        # vuelve a lanzar el error
    finally:
        pool.putconn(conexion)                       # devuelve la conexion al pool


SQL_TABLAS = """
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,              -- identificador interno
    username        TEXT NOT NULL UNIQUE,            -- nombre de usuario (minusculas)
    password_hash   TEXT NOT NULL,                   -- hash bcrypt: la contrasena nunca se guarda
    role            TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,   -- permite desactivar una cuenta
    failed_attempts INTEGER NOT NULL DEFAULT 0,      -- intentos fallidos seguidos
    locked_until    TIMESTAMPTZ,                     -- si hay fecha futura, la cuenta esta bloqueada
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS invitations (
    id          SERIAL PRIMARY KEY,                  -- identificador interno
    code_hash   TEXT NOT NULL UNIQUE,                -- sha256 del codigo (el codigo real no se guarda)
    note        TEXT,                                -- para quien es, ej. "Farmacia Centro"
    expires_at  TIMESTAMPTZ NOT NULL,                -- fecha de vencimiento
    used_by     INTEGER REFERENCES users(id),        -- quien lo uso
    used_at     TIMESTAMPTZ,                         -- cuando se uso (vacio = disponible)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def crear_tablas():
    """Crea las tablas si no existen (se llama al arrancar el servicio)."""
    with cursor() as cur:                            # abre una transaccion
        cur.execute(SQL_TABLAS)                      # ejecuta el SQL anterior


# ---------------------------------------------------------------------
# Contrasenas y validaciones
# ---------------------------------------------------------------------
def hash_password(password):
    """Convierte la contrasena en un hash bcrypt (con sal aleatoria)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


DUMMY_HASH = hash_password(secrets.token_urlsafe(16))  # hash falso para igualar tiempos si el usuario no existe


def verificar_password(password, hash_guardado):
    """Compara una contrasena con su hash; devuelve True o False."""
    datos = password.encode("utf-8")                 # texto a bytes
    if len(datos) > 72:                              # bcrypt solo lee 72 bytes: se rechaza lo mas largo
        return False
    try:
        return bcrypt.checkpw(datos, hash_guardado.encode("ascii"))  # compara de forma segura
    except ValueError:                               # hash mal formado
        return False


def normalizar_usuario(usuario):
    """Minusculas y sin espacios; exige el formato permitido."""
    limpio = usuario.strip().lower()                 # normaliza
    if not PATRON_USUARIO.match(limpio):             # valida el formato
        raise ErrorAuth(422, "Usuario: 3 a 32 caracteres, solo letras, números, punto, guion y guion bajo")
    return limpio                                    # usuario valido


def validar_password(password, usuario):
    """Devuelve un mensaje de error si la contrasena es debil; None si esta bien."""
    if len(password) < 10:                           # longitud minima
        return "La contraseña debe tener al menos 10 caracteres"
    if len(password.encode("utf-8")) > 72:           # limite tecnico de bcrypt
        return "La contraseña es demasiado larga (máximo 72 bytes)"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "La contraseña debe incluir letras y números"
    if usuario in password.lower():                  # no puede contener el usuario
        return "La contraseña no debe contener tu nombre de usuario"
    return None                                      # contrasena aceptable


def hash_codigo(codigo):
    """sha256 del codigo de invitacion (asi se guarda y se busca)."""
    return hashlib.sha256(codigo.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------
def crear_token(uid, usuario, rol):
    """Genera un token JWT firmado que caduca en DURACION_HORAS."""
    ahora = datetime.now(timezone.utc)               # momento actual en UTC
    carga = {
        "sub": usuario,                              # a quien pertenece el token
        "uid": uid,                                  # id interno
        "role": rol,                                 # user o admin
        "iss": EMISOR,                               # quien lo emitio
        "iat": ahora,                                # cuando se emitio
        "exp": ahora + timedelta(hours=DURACION_HORAS),  # cuando caduca
    }
    return jwt.encode(carga, secreto(), algorithm=ALGORITMO)  # firma con JWT_SECRET


# ---------------------------------------------------------------------
# Invitaciones, registro y login
# ---------------------------------------------------------------------
def crear_invitacion(nota, dias):
    """Genera un codigo de un solo uso y guarda solo su hash."""
    codigo = "AWARE-" + secrets.token_urlsafe(16)    # 128 bits de aleatoriedad: no se puede adivinar
    with cursor() as cur:
        cur.execute(
            "INSERT INTO invitations (code_hash, note, expires_at) "
            "VALUES (%s, %s, now() + make_interval(days => %s))",
            (hash_codigo(codigo), nota, dias),       # parametros: evitan inyeccion SQL
        )
    return codigo                                    # se muestra una sola vez


def registrar(usuario, password, codigo):
    """Crea una cuenta usando un codigo de invitacion valido (se consume)."""
    usuario = normalizar_usuario(usuario)            # valida el usuario
    mensaje = validar_password(password, usuario)    # valida la contrasena
    if mensaje:                                      # si es debil, se rechaza SIN gastar el codigo
        raise ErrorAuth(422, mensaje)
    hash_pw = hash_password(password)                # se calcula antes de abrir la transaccion (es lento)
    from psycopg2 import errors                      # para detectar usuario duplicado
    try:
        with cursor() as cur:
            cur.execute(                             # busca el codigo y lo bloquea para que nadie mas lo use
                "SELECT id FROM invitations WHERE code_hash = %s AND used_at IS NULL "
                "AND expires_at > now() FOR UPDATE",
                (hash_codigo(codigo),),
            )
            invitacion = cur.fetchone()              # None si no existe, vencio o ya se uso
            if invitacion is None:
                raise ErrorAuth(400, "Código de invitación inválido o vencido")
            cur.execute("SELECT 1 FROM users WHERE username = %s", (usuario,))
            if cur.fetchone():                       # el nombre ya existe
                raise ErrorAuth(409, "Ese nombre de usuario ya está en uso")
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                (usuario, hash_pw),
            )
            uid = cur.fetchone()[0]                  # id del usuario nuevo
            cur.execute(                             # marca el codigo como usado
                "UPDATE invitations SET used_by = %s, used_at = now() WHERE id = %s",
                (uid, invitacion[0]),
            )
    except errors.UniqueViolation:                   # dos registros simultaneos con el mismo nombre
        raise ErrorAuth(409, "Ese nombre de usuario ya está en uso")
    return usuario                                   # registro exitoso


def crear_admin(usuario, password):
    """Crea una cuenta de administrador (solo se usa desde la terminal)."""
    usuario = normalizar_usuario(usuario)            # valida el usuario
    mensaje = validar_password(password, usuario)    # valida la contrasena
    if mensaje:
        raise ErrorAuth(422, mensaje)
    with cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE username = %s", (usuario,))
        if cur.fetchone():                           # ya existe
            raise ErrorAuth(409, "Ese nombre de usuario ya está en uso")
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin')",
            (usuario, hash_password(password)),
        )


def iniciar_sesion(usuario, password):
    """Valida usuario y contrasena; devuelve un token o lanza ErrorAuth."""
    usuario = usuario.strip().lower()                # normaliza (sin validar formato)
    error = None                                     # se llena si algo falla
    resultado = None                                 # se llena si todo sale bien
    with cursor() as cur:
        cur.execute(                                 # busca al usuario y bloquea su fila mientras se decide
            "SELECT id, password_hash, role, is_active, "
            "(locked_until IS NOT NULL AND locked_until > now()) "
            "FROM users WHERE username = %s FOR UPDATE",
            (usuario,),
        )
        fila = cur.fetchone()                        # None si no existe
        if fila is None:
            verificar_password(password, DUMMY_HASH)  # gasta el mismo tiempo que un usuario real
            error = (401, MSG_CREDENCIALES)          # mensaje generico
        else:
            uid, hash_guardado, rol, activo, bloqueado = fila
            if bloqueado:                            # cuenta bloqueada temporalmente
                error = (429, "Demasiados intentos fallidos. Intenta de nuevo en unos minutos")
            elif verificar_password(password, hash_guardado) and activo:
                cur.execute(                         # exito: reinicia el contador
                    "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = %s", (uid,))
                resultado = (uid, rol)               # datos para el token
            else:
                cur.execute(                         # fallo: suma un intento
                    "UPDATE users SET failed_attempts = failed_attempts + 1 WHERE id = %s", (uid,))
                cur.execute(                         # si llego al maximo, bloquea la cuenta
                    "UPDATE users SET failed_attempts = 0, locked_until = now() + make_interval(mins => %s) "
                    "WHERE id = %s AND failed_attempts >= %s",
                    (BLOQUEO_MINUTOS, uid, MAX_INTENTOS),
                )
                error = (401, MSG_CREDENCIALES)
    # El error se lanza FUERA de la transaccion para que el conteo de intentos SI se guarde
    if error:
        raise ErrorAuth(*error)
    uid, rol = resultado
    return {
        "access_token": crear_token(uid, usuario, rol),  # token firmado
        "token_type": "bearer",                      # tipo estandar
        "expires_in": DURACION_HORAS * 3600,         # segundos de vida
        "username": usuario,
        "role": rol,
    }