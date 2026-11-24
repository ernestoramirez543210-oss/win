#!/bin/bash
# Prepara el VPS para traer b-roll de TikTok solo. IDEMPOTENTE: se puede volver a correr sin romper nada.
# Lo corre ROOT en el servidor; lo lanza desplegar-vps.sh desde el PC despues de copiar los archivos a /opt/broll-tiktok.
#
#   instalar-vps.sh [--activar]
#
# Que hace:
#   - instala por apt: ffmpeg, python3-venv y las dos librerias que pide el detector de texto (libgl1, libglib2.0-0t64)
#   - crea el usuario `broll` (sin login, sin sudo, sin docker) y las carpetas de /opt/broll-tiktok:
#       codigo, venv y llaves son de root (el servicio solo los LEE); public/, logs/ y estado/ son de broll (ahi escribe)
#   - crea el venv con yt-dlp, curl_cffi, requests, el detector de texto y faster-whisper (versiones fijas de requirements-vps.txt, solo ruedas ya armadas)
#   - baja el modelo de Whisper `small` (4 archivos, ~0,5 GB, de un commit fijo de Hugging Face y con SHA-256 comprobado) a /opt/broll-tiktok/modelos
#   - instala las unidades de systemd: el b-roll semanal (su timer queda APAGADO salvo que pases --activar) y la Fabrica de ofertas (su timer se enciende solo
#     cuando ya esta puesta la llave del dash, secretos/dash.env)
# Lo que NO hace: no pone llaves. El permiso de Drive (secretos/google.json) lo pone el dueno con scripts\Setup-DriveBroll.ps1, la llave del dash para la Fabrica
# (secretos/dash.env) con scripts\Setup-ClaveEditorVps.ps1
# (la busqueda por palabra es GRATIS con la lista publica de TikTok: no necesita llave; scripts\Setup-LlaveScrapeCreators.ps1 solo sirve para el proveedor de pago, opcional).
set -euo pipefail

BASE=/opt/broll-tiktok
ACTIVAR=0
for a in "$@"; do
  case "$a" in
    --activar) ACTIVAR=1 ;;
    *) echo "Opcion desconocida: $a" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "Corre esto como root." >&2; exit 1; }
if [ ! -d "$BASE/scripts" ] || [ ! -f "$BASE/vps/requirements-vps.txt" ]; then
  echo "Faltan los archivos en $BASE (primero corre desplegar-vps.sh desde el PC)." >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive

# --- paquetes del sistema (solo si falta alguno)
paquetes="ffmpeg python3-venv libgl1 libglib2.0-0t64"
faltan=""
for p in $paquetes; do
  dpkg -s "$p" >/dev/null 2>&1 || faltan="$faltan $p"
done
if [ -n "$faltan" ]; then
  echo "Instalando por apt:$faltan"
  apt-get update -qq
  apt-get install -y --no-install-recommends $faltan
else
  echo "Paquetes del sistema: ya estaban ($paquetes)"
fi

# --- usuario y carpetas
if ! id -u broll >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$BASE/estado" --no-create-home --shell /usr/sbin/nologin broll
  echo "Usuario broll creado"
fi
install -d -m 0755 -o root -g root "$BASE" "$BASE/scripts" "$BASE/vps"
install -d -m 0750 -o broll -g broll "$BASE/public" "$BASE/public/productos" "$BASE/logs" "$BASE/estado"
install -d -m 0750 -o root -g broll "$BASE/secretos"
# las llaves ya puestas se quedan donde estan; solo se corrige quien las puede leer (root las escribe, broll las lee)
for f in "$BASE/secretos/google.json" "$BASE/secretos/dash.env" "$BASE/.env"; do
  [ -f "$f" ] && chown root:broll "$f" && chmod 0640 "$f"
done

# --- entorno de Python (solo ruedas ya armadas: instalar no ejecuta codigo de ningun paquete)
if [ ! -x "$BASE/venv/bin/python" ]; then
  python3 -m venv "$BASE/venv"
fi
"$BASE/venv/bin/python" -m pip install --quiet --disable-pip-version-check --only-binary=:all: --no-cache-dir \
  -r "$BASE/vps/requirements-vps.txt"

# --- modelo de Whisper (lo usa scripts/transcribir_vps.py; el programa NO baja nada por su cuenta). Commit fijo y SHA-256 por archivo: lo que llegue distinto no se instala.
MODELO="$BASE/modelos/faster-whisper-small"
REV_MODELO=536b0662742c02347bc0e980a01041f333bce120
declare -A SHA_MODELO=(
  [config.json]=b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828
  [model.bin]=3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671
  [tokenizer.json]=fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab
  [vocabulary.txt]=34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913
)
install -d -m 0755 -o root -g root "$BASE/modelos" "$MODELO"
for f in config.json tokenizer.json vocabulary.txt model.bin; do
  if [ -f "$MODELO/$f" ] && [ "$(sha256sum "$MODELO/$f" | cut -d' ' -f1)" = "${SHA_MODELO[$f]}" ]; then
    continue
  fi
  echo "Bajando $f del modelo de Whisper (Systran/faster-whisper-small, commit ${REV_MODELO:0:8})..."
  rm -f "$MODELO/$f.nuevo"
  curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 20 --max-time 1800 -o "$MODELO/$f.nuevo" \
    "https://huggingface.co/Systran/faster-whisper-small/resolve/$REV_MODELO/$f"
  llego="$(sha256sum "$MODELO/$f.nuevo" | cut -d' ' -f1)"
  if [ "$llego" != "${SHA_MODELO[$f]}" ]; then
    rm -f "$MODELO/$f.nuevo"
    echo "ERROR: $f del modelo llego distinto (esperaba ${SHA_MODELO[$f]:0:12}, llego ${llego:0:12}). No se instalo." >&2
    exit 1
  fi
  chown root:root "$MODELO/$f.nuevo"
  chmod 0644 "$MODELO/$f.nuevo"
  mv -f "$MODELO/$f.nuevo" "$MODELO/$f"
done

# --- archivos de trabajo en su sitio final (los copia desplegar-vps.sh a vps/ y aqui se instalan con su dueno y permisos)
install -m 0755 -o root -g root "$BASE/vps/correr_broll.sh" "$BASE/correr_broll.sh"
install -m 0755 -o root -g root "$BASE/vps/correr_fabrica.sh" "$BASE/correr_fabrica.sh"
install -m 0644 -o root -g root "$BASE/vps/ofertas.json" "$BASE/ofertas.json"
chown -R root:root "$BASE/scripts" "$BASE/vps"
chmod -R go-w "$BASE/scripts" "$BASE/vps"

# --- systemd
install -m 0644 -o root -g root "$BASE/vps/broll-tiktok.service" /etc/systemd/system/broll-tiktok.service
install -m 0644 -o root -g root "$BASE/vps/broll-tiktok.timer" /etc/systemd/system/broll-tiktok.timer
install -m 0644 -o root -g root "$BASE/vps/fabrica-broll.service" /etc/systemd/system/fabrica-broll.service
install -m 0644 -o root -g root "$BASE/vps/fabrica-broll.timer" /etc/systemd/system/fabrica-broll.timer
systemctl daemon-reload
if [ "$ACTIVAR" -eq 1 ]; then
  systemctl enable --now broll-tiktok.timer
elif systemctl is-enabled --quiet broll-tiktok.timer 2>/dev/null; then
  systemctl try-restart broll-tiktok.timer      # por si cambio la hora del timer
fi
# La Fabrica solo se vigila cuando ya esta la llave del dash (la pone el dueno con Setup-ClaveEditorVps.ps1, que tambien lo enciende); sin ella no tiene nada que leer.
if [ -f "$BASE/secretos/dash.env" ]; then
  systemctl enable fabrica-broll.timer >/dev/null 2>&1
  systemctl restart fabrica-broll.timer
elif systemctl is-enabled --quiet fabrica-broll.timer 2>/dev/null; then
  systemctl try-restart fabrica-broll.timer
fi

# --- resumen (sin llaves)
echo
echo "=== Listo ==="
echo "ffmpeg:    $(ffmpeg -version 2>/dev/null | head -1 | cut -c1-60)"
runuser -u broll -- "$BASE/venv/bin/python" -c \
  "import importlib.metadata as m; print('python:    yt-dlp', m.version('yt-dlp'), '| curl_cffi', m.version('curl_cffi'), '| requests', m.version('requests')); print('detector:  rapidocr-onnxruntime', m.version('rapidocr-onnxruntime'), '| onnxruntime', m.version('onnxruntime'), '| opencv', m.version('opencv-python')); print('whisper:   faster-whisper', m.version('faster-whisper'), '| ctranslate2', m.version('ctranslate2'))"
[ -f "$MODELO/model.bin" ] && echo "modelo:    Whisper small instalado ($MODELO)" || echo "modelo:    FALTA el modelo de Whisper"
echo "timer:     $(systemctl is-enabled broll-tiktok.timer 2>/dev/null || true) / $(systemctl is-active broll-tiktok.timer 2>/dev/null || true)   (b-roll semanal)"
echo "fabrica:   $(systemctl is-enabled fabrica-broll.timer 2>/dev/null || true) / $(systemctl is-active fabrica-broll.timer 2>/dev/null || true)   (cada hora)"
[ -f "$BASE/secretos/dash.env" ] && echo "Dash:      llave puesta (secretos/dash.env)" || echo "Dash:      FALTA la llave para vigilar la Fabrica (el dueno corre scripts\\Setup-ClaveEditorVps.ps1)"
[ -f "$BASE/secretos/google.json" ] && echo "Drive:     permiso puesto (secretos/google.json)" || echo "Drive:     FALTA el permiso (el dueno corre scripts\\Setup-DriveBroll.ps1)"
[ -f "$BASE/.env" ] && grep -q '^SCRAPECREATORS_API_KEY=.' "$BASE/.env" && echo "Busqueda:  por palabra GRATIS (lista publica de TikTok); ademas hay llave de ScrapeCreators (.env), de pago y opcional" || echo "Busqueda:  por palabra GRATIS (lista publica de TikTok), cuentas y enlaces"
systemctl list-timers broll-tiktok.timer --no-pager 2>/dev/null | head -3 || true
