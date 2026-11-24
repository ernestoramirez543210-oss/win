#!/bin/bash
# Corrida del b-roll de TikTok en el VPS (la lanza broll-tiktok.timer; tambien se puede lanzar a mano).
# Todo lo que imprime va a logs/broll_AAAAMMDD.log, con cualquier token de Google que se colara tapado. No usa tokens de Claude.
# Sale con el codigo de scripts/broll_vps.py:
#   0 bien | 2 bien, pero alguna fuente fallo (por ejemplo una cuenta que TikTok mando a esperar) | 3 TikTok bloqueo toda la corrida
#   4 Drive sin permiso (hay que volver a autorizar con Setup-DriveBroll.ps1) | 1 configuracion mala o error inesperado
# NO se corre como root: dejaria archivos de root que despues el servicio no puede escribir y todo falla sin avisar.
# A mano (opciones de broll_vps.py al final, por ejemplo --solo dinamicas --max-total 5 o --simular):
#   sudo -u broll /opt/broll-tiktok/correr_broll.sh [opciones]
if [ "$(id -u)" -eq 0 ]; then
  echo "No corras esto como root; usa:  sudo -u broll $0" >&2
  exit 1
fi
export TZ=America/Bogota          # fechas y nombres de archivo siempre en hora de Bogota, tambien a mano
cd "$(dirname "$0")" || exit 1
mkdir -p logs estado
find logs -name 'broll_*.log' -mtime +45 -delete 2>/dev/null
LOG="logs/broll_$(date +%Y%m%d).log"
# Una sola corrida a la vez: la del horario y una a mano no se pisan
exec 9>estado/.candado || { echo "No puedo abrir el candado (estado/.candado): revisa su dueno y permisos" >&2; exit 1; }
if ! flock -n 9; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') === ya hay una corrida en curso: esta no arranca" >> "$LOG"
  exit 0
fi
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 ./venv/bin/python -u scripts/broll_vps.py "$@" 2>&1 \
  | sed -u -E 's#(ya29\.|1//0)[A-Za-z0-9._-]+#\1***#g' >> "$LOG"
codigo="${PIPESTATUS[0]}"
echo "=== fin $(date '+%H:%M:%S') codigo $codigo ===" >> "$LOG"
exit "$codigo"
