#!/bin/bash
# Sube al VPS el CORREDOR DE ANUNCIOS DESDE GIT (no desde el arbol de trabajo: en Windows esta en CRLF y en el servidor tiene que ir en LF), comprueba que cada archivo
# llego igual (SHA-256 contra el commit) y despues corre instalar-corredor.sh en el servidor. Se corre desde el PC (Git Bash), en el repo del dash:
#
#   bash editor-anuncios/vps/desplegar-corredor.sh [--ref HEAD] [--cambio etiqueta] [--simular] [--sin-sistema] [--esperar MIN]
#
#   --ref          el commit que se sube (por defecto HEAD: lo que no esta en un commit NO viaja)
#   --cambio       nombre del despliegue; las copias de lo reemplazado quedan como <archivo>.antes-<cambio> (por defecto fecha-hora y commit)
#   --simular      solo dice que archivos cambiarian, no toca el servidor
#   --sin-sistema  pone los archivos pero no instala paquetes, Remotion, modelos ni efectos (sirve para subir solo codigo)
#   --esperar MIN  si el corredor esta trabajando un anuncio, espera hasta MIN minutos a que termine (sin esto, no cambia nada y avisa)
#   --pisar-promovidas  deja que este despliegue reemplace mejoras que el equipo publico desde el servidor (puerta de mejoras) y que aun no estan en git. Antes de eso: bash editor-anuncios/vps/traer-promovidas.sh
#
# La lista de archivos es la de editor-anuncios/vps/corredor-archivos.txt DEL COMMIT que se sube. Todo llega primero a un paquete aparte (/opt/anuncios/.despliegue/<cambio>/), se
# comprueba contra los SHA-256 de git y solo entonces el instalador lo pone en su sitio, guardando antes <archivo>.antes-<cambio>; si algo falla, lo devuelve.
# Entra con la misma llave que el radar (~/.ssh/radar_vps; otra con CORREDOR_VPS_LLAVE). NUNCA sube llaves: la del dash la pone el dueno con scripts\Setup-ClaveEditorVps.ps1 (la de APIMart ya no se usa en el servidor).
set -euo pipefail

VPS="${CORREDOR_VPS:-root@203.0.113.10}"
LLAVE="${CORREDOR_VPS_LLAVE:-$HOME/.ssh/radar_vps}"
DESTINO=/opt/anuncios
REF=HEAD
SIMULAR=0
PISAR=0
CAMBIO=""
PASAR=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?falta el commit despues de --ref}"; shift ;;
    --cambio) CAMBIO="${2:?falta el nombre despues de --cambio}"; shift ;;
    --simular) SIMULAR=1 ;;
    --sin-sistema) PASAR+=("--sin-sistema") ;;
    --pisar-promovidas) PISAR=1 ;;
    --esperar) PASAR+=("--esperar" "${2:?falta el numero de minutos}"); shift ;;
    *) echo "Opcion desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done

RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"
git rev-parse --verify --quiet "$REF^{commit}" >/dev/null || { echo "No existe el commit $REF" >&2; exit 1; }
COMMIT="$(git rev-parse --short=10 "$REF")"
[ -n "$CAMBIO" ] || CAMBIO="$(date +%Y%m%d-%H%M)-$COMMIT"
[[ "$CAMBIO" =~ ^[A-Za-z0-9._-]{1,60}$ ]] || { echo "--cambio solo puede llevar letras, numeros, punto, guion y guion bajo (hasta 60)." >&2; exit 2; }
[ -f "$LLAVE" ] || { echo "Falta la llave SSH del servidor: $LLAVE" >&2; exit 1; }
SSH=(ssh -i "$LLAVE" -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=yes "$VPS")

MANIFIESTO_GIT="editor-anuncios/vps/corredor-archivos.txt"
git cat-file -e "$REF:$MANIFIESTO_GIT" 2>/dev/null || { echo "El commit $COMMIT no trae $MANIFIESTO_GIT" >&2; exit 1; }

echo "Commit: $COMMIT ($(git log -1 --format=%s "$REF" | cut -c1-70))   cambio: $CAMBIO"

# --- la lista: origen destino modo (lo que empieza por # y las lineas vacias no cuentan)
ORIGENES=(); DESTINOS=(); MODOS=(); SHAS=()
while read -r origen destino modo resto; do
  [ -n "${origen:-}" ] || continue
  case "$origen" in \#*) continue ;; esac
  [ -z "${resto:-}" ] || { echo "Linea mal hecha en $MANIFIESTO_GIT (sobra algo): $origen $destino $modo $resto" >&2; exit 1; }
  case "$destino" in ''|*..*|/*|*[!A-Za-z0-9._/-]*) echo "Destino no permitido: '$destino'" >&2; exit 1 ;; esac
  case "$origen" in ''|*..*|/*|*[!A-Za-z0-9._/-]*) echo "Origen no permitido: '$origen'" >&2; exit 1 ;; esac
  [ "$modo" = 644 ] || [ "$modo" = 755 ] || { echo "Modo no permitido para $destino: '$modo' (644 o 755)" >&2; exit 1; }
  git cat-file -e "$REF:editor-anuncios/$origen" 2>/dev/null || { echo "  FALTA en el commit: editor-anuncios/$origen" >&2; exit 1; }
  ORIGENES+=("$origen"); DESTINOS+=("$destino"); MODOS+=("$modo")
  SHAS+=("$(git show "$REF:editor-anuncios/$origen" | sha256sum | cut -d' ' -f1)")
done < <(git show "$REF:$MANIFIESTO_GIT" | tr -d '\r')
N="${#DESTINOS[@]}"
[ "$N" -gt 0 ] || { echo "La lista de archivos esta vacia." >&2; exit 1; }
if [ "$(printf '%s\n' "${DESTINOS[@]}" | sort | uniq -d | wc -l)" -ne 0 ]; then echo "Hay destinos repetidos en la lista." >&2; exit 1; fi

# --- que hay ya en el servidor (UNA sola llamada)
REMOTOS="$(printf '%s\n' "${DESTINOS[@]}" | "${SSH[@]}" "cd $DESTINO 2>/dev/null && xargs -d '\n' sha256sum -- 2>/dev/null || true")"
CAMBIAN=()
REMOTO_SHA=()
for ((i = 0; i < N; i++)); do
  remoto_sha="$(printf '%s\n' "$REMOTOS" | awk -v f="${DESTINOS[$i]}" '$2 == f {print $1}' | head -1)"
  REMOTO_SHA[$i]="$remoto_sha"
  if [ "${SHAS[$i]}" = "$remoto_sha" ]; then
    echo "  =  ${DESTINOS[$i]}"
  else
    CAMBIAN+=("$i")
    if [ -n "$remoto_sha" ]; then echo "  ~  ${DESTINOS[$i]}  (cambia)"; else echo "  +  ${DESTINOS[$i]}  (nuevo)"; fi
  fi
done
echo "${#CAMBIAN[@]} de $N archivo(s) cambian."

# --- mejoras publicadas desde el servidor (puerta de mejoras: `puerta publicar`) que aun no estan en git: este despliegue NO las pisa sin avisar
PROMOVIDAS="$("${SSH[@]}" "cat $DESTINO/.versiones/promovidas.json 2>/dev/null || true")"
if [ -n "$PROMOVIDAS" ]; then
  PROM_LISTA="$(printf '%s' "$PROMOVIDAS" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{for(const [k,v] of Object.entries(JSON.parse(s)))console.log(k+" "+v.sha)}catch(e){console.log("__ERROR__")}})')"
  if printf '%s\n' "$PROM_LISTA" | grep -q '^__ERROR__$'; then echo "promovidas.json del servidor esta danado: no se puede comprobar si se pisarian mejoras del equipo. Revisalo (cat $DESTINO/.versiones/promovidas.json) antes de desplegar." >&2; exit 1; fi
  PISARIAN=()
  for i in "${CAMBIAN[@]}"; do
    prom_sha="$(printf '%s\n' "$PROM_LISTA" | awk -v f="${DESTINOS[$i]}" '$1 == f {print $2}' | head -1)"
    if [ -n "$prom_sha" ] && [ "$prom_sha" = "${REMOTO_SHA[$i]:-}" ]; then PISARIAN+=("${DESTINOS[$i]}"); fi
  done
  if [ "${#PISARIAN[@]}" -gt 0 ]; then
    echo "OJO: este despliegue reemplazaria ${#PISARIAN[@]} archivo(s) que el equipo mejoro desde el servidor y que todavia NO estan en git:" >&2
    printf '   %s\n' "${PISARIAN[@]}" >&2
    if [ "$PISAR" -eq 0 ]; then
      echo "No se hace nada. Primero traelos al repo con:  bash editor-anuncios/vps/traer-promovidas.sh  (revisa, haz commit y vuelve a desplegar). Para perderlos a proposito: --pisar-promovidas." >&2
      exit 1
    fi
    echo "(--pisar-promovidas: se reemplazan; quedan copias en /opt/anuncios/.versiones y <archivo>.antes-<cambio>)" >&2
  fi
fi

if [ "$SIMULAR" -eq 1 ]; then
  echo "Simulacion: no se toco el servidor."
  exit 0
fi

# --- el paquete: archivos tal como estan en git + manifiesto (con los SHA-256 de git, no los de los archivos que viajan)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/MANIFIESTO"
for i in "${CAMBIAN[@]}"; do
  mkdir -p "$TMP/$(dirname "${DESTINOS[$i]}")"
  git show "$REF:editor-anuncios/${ORIGENES[$i]}" > "$TMP/${DESTINOS[$i]}"
  printf '%s %s %s\n' "${SHAS[$i]}" "${MODOS[$i]}" "${DESTINOS[$i]}" >> "$TMP/MANIFIESTO"
done
STAGE="$DESTINO/.despliegue/$CAMBIO"
"${SSH[@]}" "set -e; install -d -m 0755 -o root -g root $DESTINO; install -d -m 0700 -o root -g root $DESTINO/.despliegue; rm -rf $STAGE; install -d -m 0700 -o root -g root $STAGE"
tar -C "$TMP" -cf - . | "${SSH[@]}" "tar -xf - -C $STAGE --no-same-owner --no-same-permissions"
# 1) lo que llego es igual a lo que dice git
"${SSH[@]}" "cd $STAGE && while read -r sha modo ruta; do [ -n \"\$sha\" ] || continue; [ \"\$(sha256sum \"\$ruta\" | cut -d' ' -f1)\" = \"\$sha\" ] || { echo \"  ERROR: \$ruta llego distinto\" >&2; exit 1; }; done < MANIFIESTO" \
  || { echo "El paquete llego distinto a git. No se puso nada en su sitio (queda en $STAGE)." >&2; exit 1; }
echo "Paquete subido y comprobado: ${#CAMBIAN[@]} archivo(s)."

# 2) se instala desde el paquete (el instalador nuevo si cambio; si no, el que ya esta). En una unidad aparte de systemd: si se corta la conexion, la instalacion sigue.
"${SSH[@]}" "INST=$STAGE/vps/instalar-corredor.sh; [ -f \$INST ] || INST=$DESTINO/vps/instalar-corredor.sh; [ -f \$INST ] || { echo 'No hay instalador (ni en el paquete ni en $DESTINO/vps)' >&2; exit 1; }; \
  exec systemd-run --quiet --wait --pipe --collect --unit=anuncios-instalar -p Nice=15 -p IOSchedulingClass=idle -p CPUQuota=100% -p MemoryMax=3G -p RuntimeMaxSec=2h \
  bash \$INST --aplicar $CAMBIO ${PASAR[*]:-} </dev/null"

# 3) las mejoras publicadas desde el servidor que ya son identicas a este commit dejan de ser «aun no en git»
for ((i = 0; i < N; i++)); do printf '%s %s\n' "${DESTINOS[$i]}" "${SHAS[$i]}"; done | "${SSH[@]}" "[ -f $DESTINO/scripts/editor_promover.py ] && /opt/broll-tiktok/venv/bin/python -I -B -X utf8 $DESTINO/scripts/editor_promover.py podar || true"
