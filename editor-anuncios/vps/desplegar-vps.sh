#!/bin/bash
# Sube al VPS el codigo del b-roll de TikTok DESDE GIT (no desde el arbol de trabajo: en Windows esta en CRLF y en el servidor tiene que ir en LF),
# comprueba que cada archivo llego igual (SHA-256) y despues corre instalar-vps.sh en el servidor. Se corre desde el PC (Git Bash), en el repo del dash:
#
#   bash editor-anuncios/vps/desplegar-vps.sh [--ref HEAD] [--simular] [--sin-instalar] [--activar]
#
#   --ref        el commit que se sube (por defecto HEAD: lo que no esta en un commit NO viaja)
#   --simular    solo dice que archivos cambiarian, no toca el servidor
#   --sin-instalar  copia los archivos pero no corre instalar-vps.sh
#   --activar    se pasa a instalar-vps.sh (enciende el timer semanal)
#
# Entra con la misma llave que el radar (~/.ssh/radar_vps; otra con BROLL_VPS_LLAVE). Nunca sube llaves: google.json, dash.env y .env los ponen los
# scripts Setup-DriveBroll.ps1, Setup-ClaveEditorVps.ps1 y Setup-LlaveScrapeCreators.ps1 que corre el dueno.
set -euo pipefail

VPS="${BROLL_VPS:-root@203.0.113.10}"
LLAVE="${BROLL_VPS_LLAVE:-$HOME/.ssh/radar_vps}"
DESTINO=/opt/broll-tiktok
REF=HEAD
SIMULAR=0
INSTALAR=1
PASAR=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?falta el commit despues de --ref}"; shift ;;
    --simular) SIMULAR=1 ;;
    --sin-instalar) INSTALAR=0 ;;
    --activar) PASAR+=("$1") ;;
    *) echo "Opcion desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done

RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"
git rev-parse --verify --quiet "$REF^{commit}" >/dev/null || { echo "No existe el commit $REF" >&2; exit 1; }
[ -f "$LLAVE" ] || { echo "Falta la llave SSH del servidor: $LLAVE" >&2; exit 1; }
SSH=(ssh -i "$LLAVE" -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=yes "$VPS")

# origen en el repo : destino bajo /opt/broll-tiktok
ARCHIVOS=(
  "editor-anuncios/scripts/_comun.py:scripts/_comun.py"
  "editor-anuncios/scripts/broll_tiktok.py:scripts/broll_tiktok.py"
  "editor-anuncios/scripts/drive_api.py:scripts/drive_api.py"
  "editor-anuncios/scripts/texto_quemado.py:scripts/texto_quemado.py"
  "editor-anuncios/scripts/broll_vps.py:scripts/broll_vps.py"
  "editor-anuncios/scripts/referencia_meta.py:scripts/referencia_meta.py"
  "editor-anuncios/scripts/consultas_desde_texto.py:scripts/consultas_desde_texto.py"
  "editor-anuncios/scripts/original_reutilizable.py:scripts/original_reutilizable.py"
  "editor-anuncios/scripts/transcribir_vps.py:scripts/transcribir_vps.py"
  "editor-anuncios/scripts/fabrica_broll.py:scripts/fabrica_broll.py"
  "editor-anuncios/scripts/autoprueba_broll.py:scripts/autoprueba_broll.py"
  "editor-anuncios/scripts/autoprueba_drive.py:scripts/autoprueba_drive.py"
  "editor-anuncios/scripts/autoprueba_broll_vps.py:scripts/autoprueba_broll_vps.py"
  "editor-anuncios/scripts/autoprueba_referencia.py:scripts/autoprueba_referencia.py"
  "editor-anuncios/scripts/autoprueba_consultas.py:scripts/autoprueba_consultas.py"
  "editor-anuncios/scripts/autoprueba_fabrica_broll.py:scripts/autoprueba_fabrica_broll.py"
  "editor-anuncios/requirements.txt:requirements.txt"
  "editor-anuncios/vps/requirements-vps.txt:vps/requirements-vps.txt"
  "editor-anuncios/vps/instalar-vps.sh:vps/instalar-vps.sh"
  "editor-anuncios/vps/correr_broll.sh:vps/correr_broll.sh"
  "editor-anuncios/vps/correr_fabrica.sh:vps/correr_fabrica.sh"
  "editor-anuncios/vps/broll-tiktok.service:vps/broll-tiktok.service"
  "editor-anuncios/vps/broll-tiktok.timer:vps/broll-tiktok.timer"
  "editor-anuncios/vps/fabrica-broll.service:vps/fabrica-broll.service"
  "editor-anuncios/vps/fabrica-broll.timer:vps/fabrica-broll.timer"
  "editor-anuncios/vps/ofertas.json:vps/ofertas.json"
  "editor-anuncios/VERSION:VERSION"
)

echo "Commit: $(git rev-parse --short "$REF") ($(git log -1 --format=%s "$REF" | cut -c1-70))"
if [ "$SIMULAR" -eq 0 ]; then
  "${SSH[@]}" "install -d -m 0755 -o root -g root $DESTINO $DESTINO/scripts $DESTINO/vps"
fi

CAMBIAN=()
for par in "${ARCHIVOS[@]}"; do
  origen="${par%%:*}"
  destino="${par#*:}"
  if ! git cat-file -e "$REF:$origen" 2>/dev/null; then
    echo "  FALTA en el commit: $origen" >&2
    exit 1
  fi
  local_sha="$(git show "$REF:$origen" | sha256sum | cut -d' ' -f1)"
  remoto_sha="$("${SSH[@]}" "sha256sum $DESTINO/$destino 2>/dev/null | cut -d' ' -f1" || true)"
  if [ "$local_sha" = "$remoto_sha" ]; then
    echo "  =  $destino"
    continue
  fi
  CAMBIAN+=("$par:$local_sha")
  if [ -n "$remoto_sha" ]; then echo "  ~  $destino  (cambia)"; else echo "  +  $destino  (nuevo)"; fi
done

if [ "${#CAMBIAN[@]}" -eq 0 ]; then
  echo "Nada cambio en el servidor."
elif [ "$SIMULAR" -eq 1 ]; then
  echo "Simulacion: ${#CAMBIAN[@]} archivo(s) se subirian."
else
  # 1) todo llega como .nuevo y se comprueba; 2) solo si TODO llego bien, se pone en su sitio de una vez
  for item in "${CAMBIAN[@]}"; do
    origen="${item%%:*}"; resto="${item#*:}"; destino="${resto%%:*}"; sha="${resto#*:}"
    git show "$REF:$origen" | "${SSH[@]}" "cat > $DESTINO/$destino.nuevo"
    llego="$("${SSH[@]}" "sha256sum $DESTINO/$destino.nuevo | cut -d' ' -f1")"
    if [ "$llego" != "$sha" ]; then
      echo "  ERROR: $destino llego distinto (esperaba ${sha:0:12}, llego ${llego:0:12}). No se puso nada en su sitio." >&2
      exit 1
    fi
  done
  movimientos=""
  for item in "${CAMBIAN[@]}"; do
    resto="${item#*:}"; destino="${resto%%:*}"
    movimientos="$movimientos mv -f $DESTINO/$destino.nuevo $DESTINO/$destino;"
  done
  "${SSH[@]}" "set -e; $movimientos chown root:root $DESTINO/scripts/*.py $DESTINO/vps/*; chmod 0644 $DESTINO/scripts/*.py $DESTINO/vps/*"
  echo "Subidos y comprobados: ${#CAMBIAN[@]} archivo(s)."
fi

if [ "$SIMULAR" -eq 0 ] && [ "$INSTALAR" -eq 1 ]; then
  echo
  "${SSH[@]}" "bash $DESTINO/vps/instalar-vps.sh ${PASAR[*]:-}"
fi
