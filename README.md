# Buscador AWaRe (OMS 2025)

Buscador de antibióticos por sustancia (en español o inglés), clase o código ATC
que muestra su categoría AWaRe: **Access**, **Watch**, **Reserve** o **No recomendado**.
Requiere usuario y contraseña; la API valida un token JWT antes de cada búsqueda.

## Arquitectura (microservicios en Docker)
| Contenedor       | Función                                                         | Expuesto |
|------------------|-----------------------------------------------------------------|----------|
| `frontend`       | Nginx: login, buscador, cabeceras de seguridad, límite de login | 8080     |
| `api`            | Puerta de entrada; valida el JWT (Swagger en `/api/docs`)       | no       |
| `auth-service`   | Registro con invitación, login, bloqueo tras 5 fallos           | no       |
| `search-service` | Búsqueda en la BD AWaRe                                         | no       |
| `db-users`       | PostgreSQL de usuarios                                          | no       |
| `db-aware`       | PostgreSQL con la clasificación de la OMS                       | no       |
| `etl`            | Carga única del Excel de la OMS (`docker compose run --rm etl`) | no       |

## Arranque local
1. Copia `.env.example` a `.env` y cambia los valores (JWT_SECRET de 32+ caracteres).
2. `docker compose up -d --build`
3. Primera vez: `docker compose run --rm etl` (carga el Excel).
4. Crea tu administrador: `docker compose exec auth-service python -m app.cli crear-admin --usuario angel`
5. Abre http://localhost:8080

## Dar acceso a otra persona
`docker compose exec auth-service python -m app.cli crear-invitacion --dias 7 --nota "Nombre"`
La persona usa ese código en la pestaña **Crear cuenta**.

## Pruebas automáticas
- `docker compose exec auth-service python -m app.prueba` → 9/9
- `docker compose exec api python -m app.prueba` → 11/11

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
- `etl/`            Carga del Excel de la OMS a la BD
- `deploy/`         Caddyfile e instalador del servidor
- `data/`           Excel original de la OMS y nombres en español (`aliases_es.csv`)
