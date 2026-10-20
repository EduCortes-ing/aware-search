# Buscador AWaRe (OMS 2025)

Buscador de antibióticos por sustancia (en español o inglés), clase o código ATC
que muestra su categoría AWaRe: **Access**, **Watch**, **Reserve** o **No recomendado**.
Requiere usuario y contraseña; la API valida un token JWT antes de cada búsqueda.
Incluye un asistente de **IA local (Ollama)** que explica para qué sirve cada antibiótico
y sugiere una dosis según edad y peso, con candados de seguridad en el código.

## Arquitectura (microservicios en Docker)
| Contenedor       | Función                                                         | Expuesto |
|------------------|-----------------------------------------------------------------|----------|
| `frontend`       | Nginx: login, buscador, cabeceras de seguridad, límite de login | 8080     |
| `api`            | Puerta de entrada; valida el JWT (Swagger en `/api/docs`)       | no       |
| `auth-service`   | Registro con invitación, login, bloqueo tras 5 fallos           | no       |
| `search-service` | Búsqueda en la BD AWaRe                                         | no       |
| `ai-service`     | IA: para qué sirve y dosis sugerida (prompts KERNEL)            | no       |
| `ollama`         | Motor de IA local con el modelo `qwen2.5:3b` (~2 GB)            | no       |
| `db-users`       | PostgreSQL de usuarios                                          | no       |
| `db-aware`       | PostgreSQL con la clasificación de la OMS                       | no       |
| `etl`            | Carga única del Excel de la OMS (`docker compose run --rm etl`) | no       |

## Arranque local
1. Copia `.env.example` a `.env` y cambia los valores (JWT_SECRET de 32+ caracteres).
2. `docker compose up -d --build`
3. Primera vez: `docker compose run --rm etl` (carga el Excel).
4. Crea tu administrador: `docker compose exec auth-service python -m app.cli crear-admin --usuario angel`
5. Abre http://localhost:8080

## Asistente de IA (Ollama local)
- La primera vez que arranca, `ai-service` descarga el modelo solo (unos 2 GB, varios minutos).
  Avance: `docker compose logs -f ollama`. Estado: `docker compose exec ai-service python -c "import httpx;print(httpx.get('http://localhost:8003/health').json())"`
- Cambiar de modelo: en `.env` pon `OLLAMA_MODEL=qwen2.5:1.5b` (más ligero) o `llama3.2:3b`.
- **Método KERNEL** en `ai-service/app/prompts.py`: una tarea por prompt, salida JSON con esquema,
  temperatura 0.1 y semilla fija, contexto oficial de la OMS, restricciones numeradas y estructura
  CONTEXTO → TAREA → RESTRICCIONES → FORMATO.
- **Candados en código** (no dependen de la IA): peso obligatorio en menores de 12 años; la
  multiplicación por peso la hace Python; tope de dosis máxima diaria; sin dosis en recién nacidos
  ni en combinaciones "No recomendadas"; valores absurdos → "NO confiable"; 6 consultas/min por IP.
- Las sugerencias de la IA son de apoyo: verificar siempre con la receta y la información para prescribir.

## Dar acceso a otra persona
`docker compose exec auth-service python -m app.cli crear-invitacion --dias 7 --nota "Nombre"`
La persona usa ese código en la pestaña **Crear cuenta**.

## Pruebas automáticas
- `docker compose exec auth-service python -m app.prueba` → 9/9
- `docker compose exec api python -m app.prueba` → 13/13
- `docker compose exec ai-service python -m app.prueba` → 6/6 sin modelo; 10/10 con el modelo descargado

## Despliegue público (Oracle Cloud Always Free)
El servidor usa los mismos contenedores más **Caddy**, que da HTTPS gratis y automático.
1. En el servidor (Ubuntu): `git clone https://github.com/EduCortes-ing/aware-search.git && cd aware-search`
2. `bash deploy/instalar.sh` (Docker, firewall, swap y `.env` con secretos aleatorios). Luego sal y vuelve a entrar por SSH.
3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
4. `docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm etl`
5. `docker compose exec auth-service python -m app.cli crear-admin --usuario angel`
6. Abre `https://<DOMAIN>` (el dominio está en el `.env` del servidor).

Actualizar después de un `git push`: `git pull` y repetir el paso 3.

## Estructura
- `frontend/`       Nginx + `login.html`, `index.html`, `app.js`, `styles.css`
- `api/`            API REST en Python (FastAPI)
- `auth-service/`   Microservicio de autenticación (JWT + bcrypt)
- `search-service/` Microservicio de búsqueda
- `ai-service/`     Microservicio de IA (Ollama) con prompts KERNEL
- `etl/`            Carga del Excel de la OMS a la BD
- `deploy/`         Caddyfile e instalador del servidor
- `data/`           Excel original de la OMS y nombres en español (`aliases_es.csv`)
