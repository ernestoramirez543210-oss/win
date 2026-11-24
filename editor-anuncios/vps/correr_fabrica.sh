#!/bin/bash
# Una vuelta de la Fabrica de ofertas en el VPS (la lanza fabrica-broll.timer cada hora; tambien se puede lanzar a mano).
# Mira la cola de la Fabrica (solo LEE, con la llave de secretos/dash.env) y, por cada oferta nueva, deja en Drive su carpeta con los anuncios originales, lo que dicen,
# los b-rolls de TikTok y un INFORME.md. Si no hay nada nuevo termina en un par de segundos. Todo lo que imprime va a logs/fabrica_AAAAMMDD.log, con cualquier token de
# Google que se colara tapado. No usa tokens de Claude.
# Sale con el codigo de scripts/fabrica_broll.py:
#   0 bien o nada que hacer | 2 bien, pero algo quedo pendiente | 3 el dash, Meta o TikTok no contestaron (se reintenta en la proxima vuelta)
#   4 Drive sin permiso (hay que volver a autorizar con Setup-DriveBroll.ps1) | 1 configuracion mala (falta o esta mal la llave del dash o la carpeta de las ofertas)
# NO se corre como root: dejaria archivos de root que despues el servicio no puede escribir y todo falla sin avisar.
# A mano (opciones de fabrica_broll.py al final, por ejemplo vigilar --solo-cola, vigilar --sin-brolls o manual --enlace ... --nombre ... --slug ...):
#   sudo -u broll /opt/broll-tiktok/correr_fabrica.sh vigilar --solo-cola
if [ "$(id -u)" -eq 0 ]; then
  echo "No corras esto como root; usa:  sudo -u broll $0" >&2
  exit 1
fi
export TZ=America/Bogota          # fechas y nombres de archivo siempre en hora de Bogota, tambien a mano
cd "$(dirname "$0")" || exit 1
mkdir -p logs estado
find logs -name 'fabrica_*.log' -mtime +45 -delete 2>/dev/null
LOG="logs/fabrica_$(date +%Y%m%d).log"
# Una sola corrida a la vez: el b-roll semanal (mismo candado), esta vuelta y una a mano no se pisan
exec 9>estado/.candado || { echo "No puedo abrir el candado (estado/.candado): revisa su dueno y permisos" >&2; exit 1; }
if ! flock -n 9; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') === ya hay una corrida en curso: esta no arranca" >> "$LOG"
  exit 0
fi
ARGS=("$@")
[ "${#ARGS[@]}" -gt 0 ] || ARGS=(vigilar)
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ${ARGS[*]} ===" >> "$LOG"
PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 ./venv/bin/python -u scripts/fabrica_broll.py "${ARGS[@]}" 2>&1 \
  | sed -u -E 's#(ya29\.|1//0)[A-Za-z0-9._-]+#\1***#g; s#(DASH_CLAVE_EDITOR=)[^ ]+#\1***#g' >> "$LOG"
codigo="${PIPESTATUS[0]}"
echo "=== fin $(date '+%H:%M:%S') codigo $codigo ===" >> "$LOG"
exit "$codigo"
