#!/bin/bash
# Una pasada del corredor de anuncios en el VPS (la lanza anuncios-corredor.timer; tambien se puede lanzar con corredor-ctl correr).
# Mira la cola de la Fabrica y, si hay un pedido que le toca, lo trabaja entero (guion, voz, plan, render, revision, Drive y aviso al dash). Si no hay nada termina en un par de
# segundos. Todo lo que imprime va a logs/corredor_AAAAMMDD.log, con las llaves tapadas. No usa tokens de Claude.
# Sale con el codigo de scripts/corredor_vps.py:
#   0 bien o nada que hacer | 2 bien, pero algo quedo pendiente o un anuncio quedo con error | 3 el dash, Drive o una API no contestaron (se reintenta en la proxima pasada)
#   4 Drive sin permiso (hay que volver a autorizar con Setup-DriveBroll.ps1) | 1 configuracion mala (falta o esta mal una llave o corredor.json)
# NO se corre como root: dejaria archivos de root que despues el servicio no puede escribir y todo falla sin avisar.
if [ "$(id -u)" -eq 0 ]; then
  echo "No corras esto como root; usa:  corredor-ctl correr" >&2
  exit 1
fi
export TZ=America/Bogota          # fechas y nombres de archivo siempre en hora de Bogota, tambien a mano
cd "$(dirname "$0")" || exit 1
mkdir -p logs estado
find logs -name 'corredor_*.log' -mtime +45 -delete 2>/dev/null
LOG="logs/corredor_$(date +%Y%m%d).log"
# La pausa: mientras exista estado/PAUSA no se hace nada (ni a mano). La pone y la quita corredor-ctl pausar / reanudar.
if [ -e estado/PAUSA ]; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') === en pausa (estado/PAUSA): no hago nada" >> "$LOG"
  exit 0
fi
# Una sola pasada a la vez: la del reloj, una a mano y un despliegue (instalar-corredor.sh toma el mismo candado) no se pisan
exec 9>>estado/.candado-corredor || { echo "No puedo abrir el candado (estado/.candado-corredor): revisa su dueno y permisos" >&2; exit 1; }
if ! flock -n 9; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') === ya hay una pasada en curso: esta no arranca" >> "$LOG"
  exit 0
fi
# Argumentos de una pasada a mano: corredor-ctl los deja en estado/proxima-corrida.args y se usan UNA vez (los valida corredor_vps.py: lo que no conoce lo rechaza)
ARGS=()
if [ -f estado/proxima-corrida.args ]; then
  read -r -a ARGS < estado/proxima-corrida.args || true
  rm -f estado/proxima-corrida.args
fi
# Desde la version 2026.11.10-2 el servidor no tiene la llave de APIMart: lo que cuesta lo pide el editor por el dash con la llave del editor (la lee corredor_vps.py de
# /opt/broll-tiktok/secretos/dash.env). Si el entorno todavia trajera una llave de APIMart o la carpeta de credenciales de systemd, se quitan: ningun script la usa.
unset APIMART_API_KEY CREDENTIALS_DIRECTORY
export HF_HUB_OFFLINE=1
export OFERTAS_LATAM_DIR="${OFERTAS_LATAM_DIR:-$PWD/drive/OFERTAS LATAM}"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ${ARGS[*]} ===" >> "$LOG"
PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 /opt/broll-tiktok/venv/bin/python -u scripts/corredor_vps.py "${ARGS[@]}" 2>&1 \
  | sed -u -E 's#(ya29\.|1//0)[A-Za-z0-9._-]+#\1***#g; s#(DASH_CLAVE_EDITOR=|APIMART_API_KEY=|x-clave-editor: ?)[^ ]+#\1***#g; s#(Bearer )[A-Za-z0-9._-]+#\1***#g' >> "$LOG"
codigo="${PIPESTATUS[0]}"
echo "=== fin $(date '+%H:%M:%S') codigo $codigo ===" >> "$LOG"
exit "$codigo"
