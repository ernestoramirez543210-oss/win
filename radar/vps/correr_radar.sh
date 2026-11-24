#!/bin/bash
# Una corrida del radar en el servidor. La lanza vps/radar.service (cada 2 horas con vps/radar.timer); no la corras como root.
#   systemctl start radar.service      (corre como el usuario radar y aislado)
if [ "$(id -u)" -eq 0 ]; then
  echo "No corras esto como root; usa:  systemctl start radar.service" >&2
  exit 1
fi
export TZ=America/Bogota          # fechas y topes diarios en hora de Bogota (cambiala si estas en otro pais)
DIR_CODIGO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd -P)" || exit 1
export RADAR_DATOS="${RADAR_DATOS:-/opt/radar-de-ofertas}"
cd "$RADAR_DATOS" || exit 1
mkdir -p logs
find logs -name 'radar_*.log' -mtime +30 -delete 2>/dev/null
LOG="logs/radar_$(date +%Y%m%d).log"
exec 9>.candado || { echo "No puedo abrir el candado (.candado)" >&2; exit 1; }
if ! flock -w 1800 9; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') === otra corrida sigue en curso; esta se salta" >> "$LOG"
  exit 3
fi
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
OPCIONES="--max-ofertas 6 --max-kw 6"
# Las credenciales del proxy (usuario:clave@) se tapan en el registro.
PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 ./venv/bin/python -u "$DIR_CODIGO/radar.py" $OPCIONES 2>&1 \
  | sed -u -E "s#//[^/@ ']*@[^/ '\")]*#//***#g" >> "$LOG"
exit "${PIPESTATUS[0]}"
