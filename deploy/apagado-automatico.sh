#!/usr/bin/env bash
# =====================================================================
# deploy/apagado-automatico.sh - Apaga (DESASIGNA) la VM de Azure todos
# los dias a la 1:00 a. m. hora de Ciudad de Mexico, para no gastar credito.
# Sustituye al "apagado automatico" del portal, que Mexico Central no ofrece.
#
# Requisitos (se hacen una sola vez en el portal de Azure):
#   1. La VM tiene "Identidad administrada asignada por el sistema" activada.
#   2. Esa identidad tiene el rol "Colaborador de maquina virtual" sobre el
#      grupo de recursos aware-rg.
#
# Uso, en el servidor:
#   bash deploy/apagado-automatico.sh            -> instala la tarea diaria
#   bash deploy/apagado-automatico.sh --probar   -> verifica permisos SIN apagar
#   bash deploy/apagado-automatico.sh --ahora    -> apaga la VM en este momento
#
# Importante: "sudo shutdown" desde Ubuntu NO deja de cobrar en Azure.
# Solo "desasignar" (lo que hace este script o el boton Detener) deja de cobrar.
# =====================================================================

set -euo pipefail                                    # se detiene ante cualquier error

IMDS="http://169.254.169.254/metadata"               # servicio interno de Azure, solo visible desde la VM
API_VM="2024-07-01"                                  # version de la API de maquinas virtuales
SCRIPT_APAGAR="/usr/local/bin/aware-apagar"          # donde se instala el script que apaga
TAREA="/etc/cron.d/aware-apagado"                    # tarea programada del sistema (cron)

# ---------------------------------------------------------------------
# Script que realmente desasigna la VM (se instala en /usr/local/bin)
# ---------------------------------------------------------------------
read -r -d '' CONTENIDO <<'EOF' || true
#!/usr/bin/env bash
# aware-apagar: desasigna esta VM de Azure usando su identidad administrada.
#   aware-apagar          -> desasigna (deja de cobrar el procesador)
#   aware-apagar --probar -> solo comprueba token y permisos
set -euo pipefail
IMDS="http://169.254.169.254/metadata"
API_VM="2024-07-01"
# 1) Datos de esta VM (suscripcion, grupo y nombre) desde el servicio interno de Azure
META=$(curl -fsS -H Metadata:true "$IMDS/instance/compute?api-version=2021-02-01")
leer() { python3 -c "import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])" "$META" "$1"; }
SUB=$(leer subscriptionId); RG=$(leer resourceGroupName); VM=$(leer name)
URL="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Compute/virtualMachines/$VM"
# 2) Token de la identidad administrada (sin contrasenas guardadas en el servidor)
TOKEN=$(curl -fsS -H Metadata:true \
  "$IMDS/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])") \
  || { echo "ERROR: la VM no tiene identidad administrada activada"; exit 1; }
if [ "${1:-}" = "--probar" ]; then
  # Lee la VM: si responde 200, la identidad tiene permisos sobre ella
  CODIGO=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" "$URL?api-version=$API_VM")
  if [ "$CODIGO" = "200" ]; then echo "OK: identidad y permisos correctos para $VM ($RG)"
  else echo "ERROR $CODIGO: falta asignar el rol 'Colaborador de máquina virtual' a la identidad"; exit 1; fi
  exit 0
fi
logger -t aware-apagar "Desasignando la VM $VM"     # deja constancia en el registro del sistema
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Length: 0" \
  "$URL/deallocate?api-version=$API_VM" >/dev/null   # pide a Azure desasignar la VM
echo "Solicitud enviada: la VM $VM se desasignará en 1-2 minutos."
EOF

# ---------------------------------------------------------------------
# Opciones de prueba
# ---------------------------------------------------------------------
instalar_script() {
  echo "$CONTENIDO" | sudo tee "$SCRIPT_APAGAR" >/dev/null   # escribe el script
  sudo chmod 755 "$SCRIPT_APAGAR"                            # lo hace ejecutable
}

case "${1:-}" in
  --probar)
    instalar_script
    "$SCRIPT_APAGAR" --probar                        # comprueba sin apagar
    ;;
  --ahora)
    instalar_script
    "$SCRIPT_APAGAR"                                 # apaga ya (para probar)
    ;;
  *)
    instalar_script
    sudo timedatectl set-timezone America/Mexico_City   # el reloj del servidor en hora de CDMX
    # Tarea diaria: 1:00 a. m. (el usuario root ejecuta el script y guarda el resultado en syslog)
    echo "0 1 * * * root $SCRIPT_APAGAR 2>&1 | logger -t aware-apagar" | sudo tee "$TAREA" >/dev/null
    sudo chmod 644 "$TAREA"
    echo "Tarea instalada: la VM se desasignará todos los días a la 1:00 a. m. (hora de CDMX)."
    echo "Comprobando permisos..."
    "$SCRIPT_APAGAR" --probar
    ;;
esac