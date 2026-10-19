#!/usr/bin/env bash
# =====================================================================
# deploy/instalar.sh - Prepara el servidor de Oracle (Ubuntu) UNA sola vez
# Uso, dentro de la carpeta del proyecto en el servidor:
#   bash deploy/instalar.sh
# Hace: instala Docker, abre los puertos 80 y 443, agrega memoria swap
# y crea el archivo .env con contrasenas aleatorias y el dominio publico.
# =====================================================================

set -euo pipefail                                    # se detiene ante cualquier error

echo "== 1/5 Actualizando el sistema e instalando herramientas =="
sudo apt-get update -y                               # lista de paquetes al dia
sudo apt-get install -y ca-certificates curl git openssl  # utilidades necesarias

echo "== 2/5 Instalando Docker =="
if ! command -v docker >/dev/null 2>&1; then         # solo si aun no esta instalado
  curl -fsSL https://get.docker.com | sudo sh        # instalador oficial de Docker (incluye compose)
fi
sudo usermod -aG docker "$USER"                      # permite usar docker sin sudo (tras reconectar)

echo "== 3/5 Abriendo los puertos 80 y 443 en el firewall de Ubuntu =="
# Las imagenes de Ubuntu de Oracle traen iptables que bloquea todo excepto SSH
for puerto in 80 443; do                             # HTTP y HTTPS
  if ! sudo iptables -C INPUT -p tcp --dport "$puerto" -m state --state NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -p tcp --dport "$puerto" -m state --state NEW -j ACCEPT  # regla antes del bloqueo
  fi
done
if command -v netfilter-persistent >/dev/null 2>&1; then
  sudo netfilter-persistent save                     # guarda las reglas para que sobrevivan reinicios
fi

echo "== 4/5 Agregando 2 GB de memoria swap (respaldo de RAM) =="
if [ ! -f /swapfile ]; then                          # solo si no existe
  sudo fallocate -l 2G /swapfile                     # archivo de 2 GB
  sudo chmod 600 /swapfile                           # solo root puede leerlo
  sudo mkswap /swapfile                              # lo formatea como swap
  sudo swapon /swapfile                              # lo activa
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null  # activo tras reinicios
fi

echo "== 5/5 Creando el archivo .env con secretos aleatorios =="
if [ -f .env ]; then                                 # nunca sobrescribe uno existente
  echo ".env ya existe; no se modifica."
else
  IP=$(curl -fsS https://api.ipify.org)              # IP publica de este servidor
  DOMINIO="${IP//./-}.sslip.io"                      # ej. 129-80-1-2.sslip.io (dominio gratuito que apunta a la IP)
  cat > .env <<EOF
# Generado por deploy/instalar.sh - NO subir a GitHub
DOMAIN=${DOMINIO}
JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n/+=')
USERS_DB_NAME=users
USERS_DB_USER=users_admin
USERS_DB_PASSWORD=$(openssl rand -hex 24)
AWARE_DB_NAME=aware
AWARE_DB_USER=aware_admin
AWARE_DB_PASSWORD=$(openssl rand -hex 24)
EOF
  chmod 600 .env                                     # solo tu usuario puede leerlo
  echo "Dominio publico: https://${DOMINIO}"
fi

echo
echo "Listo. Cierra esta conexion SSH y vuelve a entrar (para usar docker sin sudo)."
