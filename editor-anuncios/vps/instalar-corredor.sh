#!/bin/bash
# Instala o actualiza el CORREDOR DE ANUNCIOS en el VPS (/opt/anuncios). IDEMPOTENTE: se puede volver a correr sin romper nada.
# Lo corre ROOT, siempre desde desplegar-corredor.sh (que ya subio los archivos a /opt/anuncios/.despliegue/<cambio>/ y comprobo su SHA-256 contra git):
#
#   instalar-corredor.sh --aplicar <cambio> [--esperar MIN] [--sin-sistema]
#   instalar-corredor.sh --estado
#
# Que hace con --aplicar (en este orden):
#   1. comprueba el paquete (SHA-256 de cada archivo contra el manifiesto) y crea las carpetas de /opt/anuncios con sus duenos
#   2. toma el candado de despliegue y el del corredor (si el corredor esta trabajando un anuncio, NO cambia nada: espera con --esperar o se rinde)
#   3. pone cada archivo en su sitio: guarda antes <archivo>.antes-<cambio>, lo copia como .nuevo y lo mueve (todo o nada: si algo falla despues, lo devuelve)
#   4. (salvo --sin-sistema) por apt, las pocas librerias que pide el navegador de Remotion; Remotion con npm ci (como el usuario broll, sin ejecutar scripts de
#      paquetes, y despues de root y de solo lectura) y su navegador; los modelos de CLIP (SHA-256 fijo); el modelo de Whisper (enlace duro al del b-roll, 0 bytes mas);
#      los efectos de sonido (se fabrican con un entorno temporal que se borra); las unidades de systemd (el reloj NO se enciende)
#   5. verifica que todo importa y abre; si algo falla, devuelve los archivos a como estaban
# Lo que NO hace: no pone llaves (la del dash la pone el dueno con scripts\Setup-ClaveEditorVps.ps1; la de APIMart ya no se usa), no enciende el reloj, no toca el radar, el runner, Hermes ni
# el b-roll de TikTok (comparte el usuario broll, su Python, su modelo de Whisper y su permiso de Drive, solo en lectura).
set -euo pipefail
umask 022

BASE=/opt/anuncios
BROLL=/opt/broll-tiktok
PY="$BROLL/venv/bin/python"
USUARIO=broll
PAQUETES_APT="libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libxcomposite1 libxdamage1 fonts-liberation fonts-noto-color-emoji"
REV_WHISPER=536b0662742c02347bc0e980a01041f333bce120      # el mismo commit de Systran/faster-whisper-small que baja instalar-vps.sh del b-roll
SFX="whoosh whoosh_corto pop pop_suave click ding hit kaching click_camara"

decir() { echo "[$(date +%H:%M:%S)] $*"; }
fallar() { echo "ERROR: $*" >&2; exit 1; }

# ------------------------------------------------------------------ --estado (solo lectura, sin llaves)
estado() {
  echo "=== Corredor de anuncios: instalacion ==="
  [ -d "$BASE" ] || { echo "No esta instalado (no existe $BASE)."; return 0; }
  echo "version:        $(cat "$BASE/VERSION" 2>/dev/null || echo '?')   ($(cat "$BASE/.instalado" 2>/dev/null | tr '\n' ' '))"
  echo "node_modules:   $( [ -d "$BASE/node_modules" ] && du -sh "$BASE/node_modules" | cut -f1 || echo 'FALTA')   navegador de Remotion: $( [ -n "$(find "$BASE/node_modules/.remotion" -maxdepth 3 -name 'chrome-headless-shell*' 2>/dev/null | head -1)" ] && echo ok || echo 'FALTA')"
  echo "modelos CLIP:   $("$PY" "$BASE/scripts/clip_local.py" --estado 2>/dev/null | tr -s ' \n' ' ' || echo 'FALTA')"
  echo "whisper:        $( [ -e "$BASE/modelos/models--Systran--faster-whisper-small/snapshots/$REV_WHISPER/model.bin" ] && echo ok || echo 'FALTA')"
  echo "efectos sonido: $(ls "$BASE/public/sfx"/*.mp3 2>/dev/null | wc -l) archivos mp3"
  echo "llave APIMart:  ya no se usa (lo que cuesta pasa por el dash con la llave del editor)$( [ -e "$BASE/secretos/apimart.key" ] && echo ' -> sobra secretos/apimart.key: la borra el dueno')"
  local habilitado activo
  habilitado="$(systemctl is-enabled anuncios-corredor.timer 2>/dev/null || true)"; activo="$(systemctl is-active anuncios-corredor.timer 2>/dev/null || true)"
  echo "reloj:          ${habilitado:-no instalado} / ${activo:-?}"
  echo "disco /opt/anuncios: $(du -sh "$BASE" 2>/dev/null | cut -f1)"
}

# ------------------------------------------------------------------ argumentos
MODO=""; CAMBIO=""; ESPERAR=0; SINSIS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --aplicar) MODO=aplicar; CAMBIO="${2:?falta el nombre del cambio despues de --aplicar}"; shift ;;
    --esperar) ESPERAR="${2:?falta el numero de minutos}"; shift ;;
    --sin-sistema) SINSIS=1 ;;
    --estado) MODO=estado ;;
    *) echo "Opcion desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done
[ "$(id -u)" -eq 0 ] || fallar "Corre esto como root (lo hace desplegar-corredor.sh)."
if [ "$MODO" = estado ]; then estado; exit 0; fi
[ "$MODO" = aplicar ] || { sed -n '2,20p' "$0"; exit 2; }
[[ "$CAMBIO" =~ ^[A-Za-z0-9._-]{1,60}$ ]] || fallar "El nombre del cambio solo puede llevar letras, numeros, punto, guion y guion bajo (hasta 60)."
[[ "$ESPERAR" =~ ^[0-9]{1,3}$ ]] || fallar "--esperar lleva un numero de minutos."
STAGE="$BASE/.despliegue/$CAMBIO"
[ -d "$STAGE" ] || fallar "No existe el paquete $STAGE (primero desplegar-corredor.sh lo sube)."
id -u "$USUARIO" >/dev/null 2>&1 || fallar "No existe el usuario $USUARIO (lo crea instalar-vps.sh del b-roll)."
[ -x "$PY" ] || fallar "Falta el Python del b-roll ($PY): corre primero instalar-vps.sh."

# ------------------------------------------------------------------ 1. carpetas (sin tocar duenos de lo que ya existe)
carpetas() {
  mkdir -p "$BASE"; chown root:root "$BASE"; chmod 0755 "$BASE"
  local d
  for d in public public/productos public/musica public/musica/biblioteca public/sfx anuncios .cache logs estado drive "drive/OFERTAS LATAM"; do
    if [ ! -d "$BASE/$d" ]; then mkdir -p "$BASE/$d"; chown "$USUARIO:$USUARIO" "$BASE/$d"; chmod 0750 "$BASE/$d"; fi
  done
  # los secretos: solo root (desde la version 2026.11.10-2 el corredor no guarda aqui ninguna llave: la de APIMart ya no se usa y la del dash es la del b-roll)
  if [ ! -d "$BASE/secretos" ]; then mkdir -p "$BASE/secretos"; chown root:root "$BASE/secretos"; chmod 0700 "$BASE/secretos"; fi
  # el libro de costos de APIMart vive en la raiz del editor (apimart.py lo abre para agregar)
  [ -e "$BASE/costos.jsonl" ] || install -m 0600 -o "$USUARIO" -g "$USUARIO" /dev/null "$BASE/costos.jsonl"
  [ -e "$BASE/estado/.candado-corredor" ] || install -m 0600 -o "$USUARIO" -g "$USUARIO" /dev/null "$BASE/estado/.candado-corredor"
  mkdir -p "$BASE/.despliegue"; chmod 0700 "$BASE/.despliegue"
}
carpetas
mkdir -p "$BASE/logs"
exec > >(tee -a "$BASE/logs/instalacion.log") 2>&1
decir "=== Instalando el corredor de anuncios ($CAMBIO) ==="

# ------------------------------------------------------------------ 1b. el paquete: SHA-256 de cada archivo contra el manifiesto
verificar_paquete() {
  [ -f "$STAGE/MANIFIESTO" ] || fallar "Falta $STAGE/MANIFIESTO."
  local sha modo ruta n=0
  while read -r sha modo ruta || [ -n "${sha:-}" ]; do
    [ -n "${sha:-}" ] || continue
    case "$ruta" in ''|*..*|/*|*[!A-Za-z0-9._/-]*) fallar "Ruta no permitida en el manifiesto: '$ruta'" ;; esac
    [ "$modo" = 644 ] || [ "$modo" = 755 ] || fallar "Modo no permitido para $ruta: $modo"
    [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || fallar "SHA-256 mal formado para $ruta"
    [ -f "$STAGE/$ruta" ] || fallar "Falta en el paquete: $ruta"
    [ "$(sha256sum "$STAGE/$ruta" | cut -d' ' -f1)" = "$sha" ] || fallar "El SHA-256 de $ruta no coincide con el del commit. No se aplica nada."
    n=$((n + 1))
  done < "$STAGE/MANIFIESTO"
  decir "Paquete comprobado: $n archivo(s) iguales a los del commit."
}
verificar_paquete

# ------------------------------------------------------------------ 2. candados
exec 8>"$BASE/.despliegue/.candado"
flock -n 8 || fallar "Ya hay otro despliegue o instalacion en curso."
exec 9<>"$BASE/estado/.candado-corredor"
if [ "$ESPERAR" -gt 0 ]; then
  decir "Si el corredor esta trabajando un anuncio, espero hasta $ESPERAR min a que termine..."
  flock -w $((ESPERAR * 60)) 9 || fallar "El corredor siguio ocupado $ESPERAR min. No se cambio nada. (corredor-ctl pausar y vuelve a intentar)"
else
  flock -n 9 || fallar "El corredor esta trabajando un anuncio ahora mismo: no cambio sus archivos a mitad de camino. Espera, o usa --esperar MIN, o corredor-ctl pausar."
fi

# ------------------------------------------------------------------ 3. archivos en su sitio (todo o nada)
APLICANDO=0
revertir() {
  set +e          # devolver todo lo que se pueda, aunque un paso de la marcha atras falle
  decir "ALGO FALLO: devuelvo los archivos a como estaban."
  local tipo rel
  if [ -f "$STAGE/.aplicados" ]; then
    while read -r tipo rel; do
      [ -n "${rel:-}" ] || continue
      if [ "$tipo" = M ] && [ -f "$BASE/$rel.antes-$CAMBIO" ]; then mv -f "$BASE/$rel.antes-$CAMBIO" "$BASE/$rel"
      elif [ "$tipo" = N ]; then rm -f "$BASE/$rel"; fi
    done < "$STAGE/.aplicados"
  fi
  if [ -d "$BASE/node_modules.antes-$CAMBIO" ]; then rm -rf "$BASE/node_modules"; mv "$BASE/node_modules.antes-$CAMBIO" "$BASE/node_modules"; fi
  systemctl daemon-reload || true
  decir "Devuelto. El paquete queda en $STAGE para ver que paso."
}
al_salir() { local rc=$?; if [ "$rc" -ne 0 ] && [ "$APLICANDO" = 1 ]; then revertir; fi; }
trap al_salir EXIT

aplicar_archivos() {
  APLICANDO=1
  : > "$STAGE/.aplicados"
  local sha modo ruta dest
  while read -r sha modo ruta || [ -n "${sha:-}" ]; do
    [ -n "${sha:-}" ] || continue
    dest="$BASE/$ruta"
    mkdir -p "$(dirname "$dest")"
    if [ -e "$dest" ]; then
      if cmp -s "$STAGE/$ruta" "$dest" && [ "$(stat -c %a "$dest")" = "$modo" ]; then continue; fi
      [ ! -e "$dest.antes-$CAMBIO" ] || fallar "Ya existe $dest.antes-$CAMBIO (otro despliegue con el mismo nombre). Usa otro --cambio."
      cp -p "$dest" "$dest.antes-$CAMBIO"
      echo "M $ruta" >> "$STAGE/.aplicados"
    else
      echo "N $ruta" >> "$STAGE/.aplicados"
    fi
    install -m "$modo" -o root -g root "$STAGE/$ruta" "$dest.nuevo"
    mv -f "$dest.nuevo" "$dest"
  done < "$STAGE/MANIFIESTO"
  decir "Archivos puestos en su sitio: $(grep -c '^M ' "$STAGE/.aplicados" || true) cambiados (con copia .antes-$CAMBIO), $(grep -c '^N ' "$STAGE/.aplicados" || true) nuevos."
}
aplicar_archivos

# ------------------------------------------------------------------ 4. sistema
paquetes_apt() {
  local p faltan=""
  for p in $PAQUETES_APT; do dpkg -s "$p" >/dev/null 2>&1 || faltan="$faltan $p"; done
  if [ -z "$faltan" ]; then decir "Librerias del navegador: ya estaban"; return 0; fi
  decir "Instalando por apt:$faltan"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1
  # -o DPkg::Lock::Timeout: si el servidor esta haciendo sus actualizaciones automaticas, espera hasta 5 min en vez de fallar
  if ! apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends $faltan; then
    decir "apt no pudo: actualizo la lista de paquetes y lo intento una vez mas"
    apt-get -o DPkg::Lock::Timeout=300 update -qq
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends $faltan
  fi
}

remotion() {
  local lock_sha viejo B
  lock_sha="$(sha256sum "$BASE/package-lock.json" | cut -d' ' -f1)"
  viejo="$(cat "$BASE/.node_modules.sha" 2>/dev/null || true)"
  if [ -d "$BASE/node_modules" ] && [ "$viejo" = "$lock_sha" ] && [ -n "$(find "$BASE/node_modules/.remotion" -maxdepth 3 -name 'chrome-headless-shell*' 2>/dev/null | head -1)" ]; then
    decir "Remotion y su navegador: ya estaban (mismo package-lock.json)"; return 0
  fi
  decir "Instalando Remotion (npm ci, como $USUARIO, sin scripts de paquetes) y su navegador..."
  B="$BASE/.build"
  rm -rf "$B"
  install -d -m 0750 -o "$USUARIO" -g "$USUARIO" "$B" "$B/home" "$B/npm-cache"
  install -m 0640 -o "$USUARIO" -g "$USUARIO" "$BASE/package.json" "$B/package.json"
  install -m 0640 -o "$USUARIO" -g "$USUARIO" "$BASE/package-lock.json" "$B/package-lock.json"
  runuser -u "$USUARIO" -- nice -n 15 env HOME="$B/home" npm_config_cache="$B/npm-cache" npm_config_update_notifier=false npm_config_fund=false npm_config_audit=false \
    bash -c 'cd "$1" && npm ci --omit=dev --ignore-scripts' _ "$B"
  decir "Bajando el navegador de Remotion (Chrome Headless Shell)..."
  runuser -u "$USUARIO" -- nice -n 15 env HOME="$B/home" \
    bash -c 'cd "$1" && node --input-type=module -e "import {ensureBrowser} from \"@remotion/renderer\"; await ensureBrowser(); console.log(\"navegador listo\")"' _ "$B"
  # a su sitio: root, solo lectura (el servicio no puede reescribir el codigo que corre)
  if [ -d "$BASE/node_modules" ]; then mv "$BASE/node_modules" "$BASE/node_modules.antes-$CAMBIO"; fi
  mv "$B/node_modules" "$BASE/node_modules"
  chown -R root:root "$BASE/node_modules"
  chmod -R go-w "$BASE/node_modules"
  echo "$lock_sha" > "$BASE/.node_modules.sha"
  rm -rf "$B"
}

cache_de_webpack() {
  # Remotion arma su paquete con webpack, que quiere guardar una cache en node_modules/.cache; node_modules es de root y de solo lectura (a proposito), asi que ese camino apunta a una carpeta de
  # datos del usuario broll. Sin esto webpack avisa «EROFS» en cada render y no guarda cache.
  mkdir -p "$BASE/.cache/webpack"; chown "$USUARIO:$USUARIO" "$BASE/.cache/webpack"; chmod 0750 "$BASE/.cache/webpack"
  if [ ! -L "$BASE/node_modules/.cache" ]; then
    if [ -d "$BASE/node_modules/.cache" ]; then rm -rf "$BASE/node_modules/.cache"; fi
    ln -s "$BASE/.cache/webpack" "$BASE/node_modules/.cache"
  fi
}

modelos() {
  if "$PY" "$BASE/scripts/clip_local.py" --estado >/dev/null 2>&1; then decir "Modelos de CLIP: ya estaban"
  else decir "Bajando los modelos de CLIP (~315 MB, SHA-256 fijo)..."; "$PY" "$BASE/scripts/clip_local.py" --descargar; fi
  # Whisper: el MISMO modelo del b-roll (enlaces duros: 0 bytes mas y no dependen de que el b-roll mantenga su carpeta). Con el formato de cache de Hugging Face
  # que lee transcribir.py (WhisperModel('small', download_root=modelos)) sin bajar nada.
  local hf="$BASE/modelos/models--Systran--faster-whisper-small" f
  mkdir -p "$hf/snapshots/$REV_WHISPER" "$hf/refs"
  printf '%s' "$REV_WHISPER" > "$hf/refs/main"
  for f in config.json model.bin tokenizer.json vocabulary.txt; do
    [ -f "$BROLL/modelos/faster-whisper-small/$f" ] || fallar "Falta $BROLL/modelos/faster-whisper-small/$f (lo baja instalar-vps.sh del b-roll)."
    if [ ! -e "$hf/snapshots/$REV_WHISPER/$f" ]; then
      ln "$BROLL/modelos/faster-whisper-small/$f" "$hf/snapshots/$REV_WHISPER/$f" 2>/dev/null || cp -p "$BROLL/modelos/faster-whisper-small/$f" "$hf/snapshots/$REV_WHISPER/$f"
    fi
  done
  # clip_local.py baja a archivos temporales de modo 0600: como root los dejaria ilegibles para broll (paso en el primer despliegue: «system error number 13»). Siempre se corrige.
  chmod -R a+rX,go-w "$BASE/modelos"
}

efectos_de_sonido() {
  local n falta=0
  for n in $SFX; do [ -s "$BASE/public/sfx/$n.mp3" ] || falta=1; done
  if [ "$falta" -eq 0 ]; then decir "Efectos de sonido: ya estaban"; return 0; fi
  decir "Fabricando los efectos de sonido (entorno temporal con numpy y scipy, que se borra)..."
  local V="$BASE/.build-sfx"
  rm -rf "$V"; install -d -m 0750 -o "$USUARIO" -g "$USUARIO" "$V"
  runuser -u "$USUARIO" -- nice -n 15 env HOME="$V" \
    bash -c 'cd "$1" && python3 -m venv v && ./v/bin/pip install --quiet --disable-pip-version-check --only-binary=:all: numpy==2.5.4 scipy==1.18.1 && ./v/bin/python "$2/scripts/crear_sfx.py" --carpeta "$2/public/sfx"' _ "$V" "$BASE"
  rm -rf "$V"
  for n in $SFX; do [ -s "$BASE/public/sfx/$n.mp3" ] || fallar "No se fabrico el efecto $n.mp3"; done
}

unidades() {
  install -m 0755 -o root -g root "$BASE/vps/correr_corredor.sh" "$BASE/correr_corredor.sh"
  install -m 0755 -o root -g root "$BASE/vps/corredor-ctl" "$BASE/corredor-ctl"
  install -m 0644 -o root -g root "$BASE/vps/corredor.json" "$BASE/corredor.json"
  install -m 0644 -o root -g root "$BASE/vps/anuncios-corredor.service" /etc/systemd/system/anuncios-corredor.service
  install -m 0644 -o root -g root "$BASE/vps/anuncios-corredor.timer" /etc/systemd/system/anuncios-corredor.timer
  systemctl daemon-reload
  # El reloj NO se enciende aqui: lo enciende el dueno (corredor-ctl encender). Si ya estaba encendido, se reinicia por si cambio la hora.
  if systemctl is-enabled --quiet anuncios-corredor.timer 2>/dev/null; then systemctl try-restart anuncios-corredor.timer; fi
}

verificar() {
  decir "Verificando..."
  runuser -u "$USUARIO" -- env HOME="$BASE/estado" PYTHONDONTWRITEBYTECODE=1 "$PY" -c \
    "import numpy, PIL, onnxruntime, tokenizers, cv2, requests, faster_whisper; from rapidocr_onnxruntime import RapidOCR; print('python: modulos ok')" || return 1
  local f
  for f in "$BASE"/scripts/*.py; do
    "$PY" -B -c "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')" "$f" || { echo "No compila: $f" >&2; return 1; }
  done
  decir "Todos los scripts compilan"
  runuser -u "$USUARIO" -- bash -c 'cd "$1" && node --input-type=module -e "import(\"@remotion/renderer\").then(() => console.log(\"remotion: modulo ok\"))"' _ "$BASE" || return 1
  local nav; nav="$(find "$BASE/node_modules/.remotion" -maxdepth 6 -type f -name 'chrome-headless-shell' 2>/dev/null | head -1)"
  [ -n "$nav" ] || { echo "No encuentro el navegador de Remotion" >&2; return 1; }
  # ldd puede ejecutar el programa que examina: se lo pido a broll, no a root
  local faltan_lib; faltan_lib="$(runuser -u "$USUARIO" -- ldd "$nav" 2>&1 | grep 'not found' | tr -s ' \t' ' ' | tr '\n' ';' || true)"
  if [ -n "$faltan_lib" ]; then echo "Al navegador le faltan librerias: $faltan_lib" >&2; return 1; fi
  decir "El navegador de Remotion tiene todas sus librerias"
  systemd-analyze verify /etc/systemd/system/anuncios-corredor.service /etc/systemd/system/anuncios-corredor.timer 2>&1 | grep -v 'apimart.key' | head -8 || true
  "$PY" "$BASE/scripts/clip_local.py" --estado >/dev/null || return 1
  [ -e "$BASE/modelos/models--Systran--faster-whisper-small/snapshots/$REV_WHISPER/model.bin" ] || return 1
  # Que los modelos se ABRAN de verdad, como el usuario broll y sin internet (no basta con que existan): CLIP (imagen y texto), Whisper y el detector de texto
  runuser -u "$USUARIO" -- env HOME="$BASE/estado" PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 "$PY" -c "
import sys
sys.path.insert(0, '$BASE/scripts')
import clip_local
m = clip_local.MotorClip()
print('clip: motor abierto,', m.textos(['una mano cose fieltro']).shape, 'vector de texto')
from faster_whisper import WhisperModel
WhisperModel('small', device='cpu', compute_type='int8', download_root='$BASE/modelos')
print('whisper: modelo abierto sin internet')
from rapidocr_onnxruntime import RapidOCR
RapidOCR()
print('detector de texto: abierto')
" || return 1
  decir "Modelos ok (se abren como broll y sin internet)"
  return 0
}

if [ "$SINSIS" -eq 0 ]; then
  paquetes_apt
  remotion
  cache_de_webpack
  modelos
  efectos_de_sonido
fi
unidades
if ! verificar; then fallar "La verificacion fallo."; fi

# ------------------------------------------------------------------ 5. listo
printf '%s %s %s\n' "$(cat "$BASE/VERSION" 2>/dev/null || echo '?')" "$CAMBIO" "$(date -Is)" > "$BASE/.instalado"
printf '%s %s %s\n' "$(date -Is)" "$CAMBIO" "$(cat "$BASE/VERSION" 2>/dev/null || echo '?')" >> "$BASE/logs/despliegues.log"
APLICANDO=0
rm -rf "$STAGE"
# el node_modules anterior (~1 GB) ya no hace falta: si hiciera falta volver atras, se reconstruye con npm ci
rm -rf "$BASE"/node_modules.antes-*
decir "=== Listo ($CAMBIO). Las copias de lo reemplazado quedan como *.antes-$CAMBIO ==="
estado
