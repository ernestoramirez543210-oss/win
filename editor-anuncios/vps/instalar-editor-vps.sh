#!/bin/bash
# Instala o actualiza el ESPACIO DEL EDITOR en el VPS: la cuenta de cada persona del equipo de video (sin permisos de administrador, sin la llave de Drive), su espacio de trabajo,
# sus topes de memoria y procesador, y la PUERTA de Drive (editor-puerta@.service/.path + scripts/puerta_editor.py). IDEMPOTENTE: se puede volver a correr sin romper nada.
# Lo corre ROOT, en el servidor, DESPUES de desplegar-corredor.sh (que sube los archivos a /opt/anuncios con SHA-256 y copias):
#
#   /opt/anuncios/vps/instalar-editor-vps.sh --aplicar [--persona NOMBRE --clave-publica ARCHIVO]
#   /opt/anuncios/vps/instalar-editor-vps.sh --dar-acceso NOMBRE ARCHIVO     (pone o cambia la llave publica de esa persona; una sola llave por persona)
#   /opt/anuncios/vps/instalar-editor-vps.sh --quitar-acceso NOMBRE          (quita su llave, bloquea la cuenta y corta sus sesiones; no borra sus datos)
#   /opt/anuncios/vps/instalar-editor-vps.sh --estado                        (solo lectura)
#
# Que hace con --aplicar:
#   1. usuarios y grupos: `puerta` (el unico que recibe el permiso de Drive, sin sesion), `editores` y, por persona, su usuario y el grupo ed-<persona>
#   2. /srv/editor: la carpeta de peticiones de cada persona (de root: ella no puede moverla), el espejo de Drive (de solo lectura para ella) y el trabajo privado de la puerta
#   3. topes de la cuenta de cada persona (systemd): memoria 3,5 a 4 GB, 1,5 nucleos, peso bajo de procesador
#   4. la puerta: el archivo de configuracion, la unidad (una instancia por persona) y su vigilante de peticiones (editor-puerta@<persona>.path)
#      y la puerta de MEJORAS (editor-promover@<persona>.service/.path + scripts/editor_promover.py): lo que la persona publica con `puerta publicar` se revisa, se prueba en una caja y llega al editor del dash
#   5. envoltorios en /usr/local/bin: `puerta`, `pesado` y `claude` (copia de la version que ya esta en el servidor, para no bajar nada de internet)
#   6. el espacio de trabajo de cada persona (~/editor-anuncios: scripts, Remotion con su navegador y modelos), solo la PRIMERA vez (despues no pisa lo suyo)
#   7. su llave publica (solo si se la das): acepta UNA llave publica por persona y la limita (sin reenvio de puertos, de agente ni X11)
# Lo que NO hace: no da sudo ni ninguna llave, no toca SSH global ni el firewall, no enciende nada que gaste dinero, no toca el radar, el runner, el corredor ni el b-roll.
#
# REGLA DE ESTE ARCHIVO (la auditoria del 2026-10-29 encontro la falla): este script corre como ROOT y la persona controla todo lo que hay en su casa (puede cambiar una carpeta por un
# enlace simbolico a /root/.ssh). Por eso NINGUNA operacion de root toca rutas dentro de la casa de la persona: todo lo de la casa se hace COMO LA PERSONA (como_usuario) y el espacio de
# trabajo se arma en una carpeta de root y se mueve entero con un solo renombrado (mv -T), que no sigue enlaces y falla si el destino ya existe.
set -euo pipefail
umask 022

BASE=/opt/anuncios
BROLL=/opt/broll-tiktok
PY="$BROLL/venv/bin/python"
CFG="$BASE/vps/editor-puerta.json"
CLAUDE_ORIGEN="${CLAUDE_ORIGEN:-/opt/claude-code/claude}"   # una copia de Claude Code que ya tengas en el servidor; si no existe, se avisa y se sigue
CLAUDE_DEST=/opt/editor-claude/claude
RAIZ=""      # sale de la configuracion

decir() { echo "[$(date +%H:%M:%S)] $*"; }
fallar() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fallar "Corre esto como root."
[ -f "$CFG" ] || fallar "Falta $CFG (lo sube desplegar-corredor.sh)."
[ -x "$PY" ] || fallar "Falta el Python del b-roll ($PY)."

# ---- la configuracion (se lee con Python: es JSON)
leer_cfg() { "$PY" -I -c 'import json,sys; c=json.load(open(sys.argv[1],encoding="utf-8")); v=c[sys.argv[2]]; print("\n".join(v) if isinstance(v,list) else v)' "$CFG" "$1"; }
RAIZ="$(leer_cfg raiz)"
mapfile -t PERSONAS < <(leer_cfg personas)
[[ "$RAIZ" =~ ^/[A-Za-z0-9/_-]{3,40}$ ]] || fallar "raiz de la configuracion no valida: $RAIZ"
for p in "${PERSONAS[@]}"; do [[ "$p" =~ ^[a-z][a-z0-9_-]{1,30}$ ]] || fallar "nombre de persona no valido en la configuracion: $p"; done

es_persona() { local x; for x in "${PERSONAS[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }
home_de() { getent passwd "$1" | cut -d: -f6; }
# Corre un comando COMO la persona, con un entorno limpio (sin nada del entorno de root). Todo lo que toque su casa pasa por aqui.
como_usuario() { local p="$1"; shift; runuser -u "$p" -- env -i HOME="$(home_de "$p")" USER="$p" LOGNAME="$p" PATH=/usr/local/bin:/usr/bin:/bin "$@"; }

# ------------------------------------------------------------------ --estado
estado() {
  echo "=== Espacio del editor ==="
  echo "puerta (usuario):   $(id -un puerta 2>/dev/null || echo 'FALTA')   vigilantes: $(for p in "${PERSONAS[@]}"; do printf '%s=%s ' "$p" "$(systemctl is-active "editor-puerta@$p.path" 2>/dev/null || echo '?')"; done)  mejoras: $(for p in "${PERSONAS[@]}"; do printf '%s=%s ' "$p" "$(systemctl is-active "editor-promover@$p.path" 2>/dev/null || echo '?')"; done)"
  local p home
  for p in "${PERSONAS[@]}"; do
    if ! id "$p" >/dev/null 2>&1; then echo "$p: la cuenta NO existe"; continue; fi
    home="$(home_de "$p")"
    echo "$p: cuenta ok | llave: $(como_usuario "$p" test -s "$home/.ssh/authorized_keys" && echo puesta || echo 'FALTA') | espacio: $(como_usuario "$p" test -f "$home/editor-anuncios/.espacio-instalado" && echo instalado || echo 'FALTA') | vence: $(chage -l "$p" | sed -n 's/^Account expires[^:]*: //p') | disco: $(du -xsh "$home" 2>/dev/null | cut -f1) | sesiones: $(who | grep -c "^$p " || true) | ultima corrida de la puerta: $(systemctl show "editor-puerta@$p.service" -p ExecMainExitTimestamp --value 2>/dev/null | cut -c1-30)"
    ls "$RAIZ/personas/$p" 2>/dev/null | tr '\n' ' ' | sed "s/^/   carpetas: /"; echo
  done
  echo "espejo de Drive: $(du -xsh "$RAIZ/drive" 2>/dev/null | cut -f1)   disco libre: $(df -h / | tail -1 | tr -s ' ' | cut -d' ' -f4)   memoria disponible: $(awk '/^MemAvailable:/ {print int($2/1024)" MB"}' /proc/meminfo)"
  echo "claude: $( [ -x "$CLAUDE_DEST" ] && "$CLAUDE_DEST" --version 2>/dev/null | head -1 || echo FALTA)"
}

# ------------------------------------------------------------------ --quitar-acceso / --dar-acceso
validar_clave() {
  # Imprime la llave publica ya limpia (una linea). No escribe NINGUN archivo (ni junto al de la llave): la huella se calcula leyendo de la entrada estandar.
  local f="$1" linea bits
  [ -f "$f" ] && [ ! -L "$f" ] || fallar "No existe el archivo de la llave (o es un enlace): $f. Usa un archivo normal que hayas guardado tu."
  [ "$(grep -cv '^[[:space:]]*$' "$f")" = 1 ] || fallar "El archivo de la llave tiene que tener UNA sola linea (la llave PUBLICA: empieza por ssh-ed25519)."
  linea="$(grep -v '^[[:space:]]*$' "$f" | tr -d '\r')"
  case "$linea" in
    ssh-ed25519\ *|sk-ssh-ed25519@openssh.com\ *|ecdsa-sha2-nistp256\ *|ecdsa-sha2-nistp384\ *|ssh-rsa\ *) ;;
    *) fallar "Eso no parece una llave publica de SSH (tiene que empezar por ssh-ed25519, ecdsa-sha2-... o ssh-rsa). Si empieza por -----BEGIN es la llave PRIVADA: no la compartas con nadie, borrala del chat y genera otra." ;;
  esac
  case "$linea" in *PRIVATE*) fallar "Eso es una llave PRIVADA." ;; esac
  bits="$(printf '%s\n' "$linea" | ssh-keygen -l -f /dev/stdin 2>/dev/null)" || fallar "ssh-keygen no entiende esa llave publica."
  case "$linea" in ssh-rsa\ *) [ "$(echo "$bits" | cut -d' ' -f1)" -ge 3072 ] || fallar "Una llave RSA tiene que ser de 3072 bits o mas." ;; esac
  printf '%s' "$linea"
}

dar_acceso() {
  local p="$1" f="$2" linea
  es_persona "$p" || fallar "$p no esta en la lista de personas de $CFG (se suma en el repo y se despliega)."
  id "$p" >/dev/null 2>&1 || fallar "La cuenta $p no existe: corre primero --aplicar."
  linea="$(validar_clave "$f")"
  # Se escribe COMO la persona: si ~/.ssh fuera un enlace a otro lado, la persona solo podria escribir donde ya puede escribir
  printf 'no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc %s\n' "$linea" | como_usuario "$p" bash -c '
    set -e; umask 077; d="$HOME/.ssh"
    [ ! -L "$d" ] || rm -f "$d"
    mkdir -p "$d"; chmod 700 "$d"
    rm -f "$d/authorized_keys.nuevo"
    cat > "$d/authorized_keys.nuevo"; chmod 600 "$d/authorized_keys.nuevo"
    mv -f "$d/authorized_keys.nuevo" "$d/authorized_keys"'
  chage -E -1 "$p"            # que la cuenta no este vencida (la deja vencida --quitar-acceso); nunca se usa usermod -U: dejaria la contrasena vacia
  decir "Llave de $p puesta ($(printf '%s\n' "$linea" | ssh-keygen -l -f /dev/stdin | cut -d' ' -f1-2)). Entra con:  ssh -i <su llave privada> $p@$(hostname -I | cut -d' ' -f1)"
}

quitar_acceso() {
  local p="$1"
  es_persona "$p" || fallar "$p no esta en la lista de personas."
  id "$p" >/dev/null 2>&1 || fallar "La cuenta $p no existe."
  chage -E 0 "$p"             # cuenta vencida PRIMERO: ni con una llave que se colara entra
  como_usuario "$p" bash -c 'rm -f "$HOME/.ssh/authorized_keys" "$HOME/.ssh/authorized_keys2"' || true     # como la persona (no como root): su casa no es de fiar
  loginctl disable-linger "$p" 2>/dev/null || true
  crontab -r -u "$p" 2>/dev/null || true
  # trabajos de at (si el servidor tiene at: hoy no lo tiene, pero se instala sin avisar): atd los correria como la persona aunque su cuenta este vencida
  if command -v atq >/dev/null 2>&1; then for j in $(atq 2>/dev/null | awk -v u="$p" '$NF == u {print $1}'); do atrm "$j" 2>/dev/null || true; done; fi
  loginctl terminate-user "$p" 2>/dev/null || true
  systemctl stop "user@$(id -u "$p").service" 2>/dev/null || true
  pkill -KILL -u "$p" 2>/dev/null || true
  sleep 1; pkill -KILL -u "$p" 2>/dev/null || true
  # unidades y arranques propios de la persona (systemd --user, autostart): como ella, porque su casa no es de fiar. Si se vuelve a dar acceso, no reviven solos
  como_usuario "$p" bash -c 'rm -rf "$HOME/.config/systemd/user" "$HOME/.config/autostart"' || true
  decir "Acceso de $p CERRADO: cuenta vencida, llave quitada, sesiones cortadas y sus trabajos programados quitados (cron, at, systemd --user). Sus datos siguen en $(home_de "$p") y en $RAIZ/personas/$p; lo que dejo en su cola de la puerta se procesa igual. Para volver a abrirlo: --dar-acceso $p <archivo con su llave publica>."
}

# ------------------------------------------------------------------ argumentos
MODO=""; PERSONA_ARG=""; CLAVE_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --aplicar) MODO=aplicar ;;
    --estado) MODO=estado ;;
    --persona) PERSONA_ARG="${2:?falta el nombre}"; shift ;;
    --clave-publica) CLAVE_ARG="${2:?falta el archivo}"; shift ;;
    --dar-acceso) MODO=dar; PERSONA_ARG="${2:?falta el nombre}"; CLAVE_ARG="${3:?falta el archivo con la llave publica}"; shift 2 ;;
    --quitar-acceso) MODO=quitar; PERSONA_ARG="${2:?falta el nombre}"; shift ;;
    *) echo "Opcion desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$MODO" in
  estado) estado; exit 0 ;;
  dar) dar_acceso "$PERSONA_ARG" "$CLAVE_ARG"; exit 0 ;;
  quitar) quitar_acceso "$PERSONA_ARG"; exit 0 ;;
  aplicar) ;;
  *) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
if [ -n "$PERSONA_ARG" ] || [ -n "$CLAVE_ARG" ]; then
  [ -n "$PERSONA_ARG" ] && [ -n "$CLAVE_ARG" ] || fallar "--persona y --clave-publica van juntas."
  es_persona "$PERSONA_ARG" || fallar "$PERSONA_ARG no esta en la lista de personas de $CFG."
  validar_clave "$CLAVE_ARG" >/dev/null
fi
for f in scripts/puerta_editor.py scripts/editor_promover.py scripts/revisar_src.mjs vps/promover-fixture.json vps/editor-promover.service vps/editor-promover.path vps/editor-puerta.service vps/editor-puerta.path vps/pesado vps/CLAUDE-VPS.md scripts/espejo_drive.py scripts/drive_api.py; do
  [ -f "$BASE/$f" ] || fallar "Falta $BASE/$f (corre primero desplegar-corredor.sh)."
done
mkdir -p "$BASE/logs"
exec > >(tee -a "$BASE/logs/instalacion-editor.log") 2>&1
decir "=== Instalando el espacio del editor (personas: ${PERSONAS[*]}) ==="

# ------------------------------------------------------------------ 1. usuarios y grupos
if ! id puerta >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir "$RAIZ/estado" --shell /usr/sbin/nologin --user-group puerta
  decir "Usuario del sistema puerta: creado"
fi
getent group editores >/dev/null || groupadd editores
for p in "${PERSONAS[@]}"; do
  getent group "ed-$p" >/dev/null || groupadd "ed-$p"
  if ! id "$p" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --user-group --groups "editores,ed-$p" --comment "Equipo de video - $p" "$p"
    chage -E 0 "$p"      # nace con la cuenta vencida: no entra nadie hasta que el dueno le ponga su llave (--dar-acceso)
    decir "Cuenta $p: creada, SIN acceso todavia (vencida y sin llave)"
  else
    usermod -a -G "editores,ed-$p" "$p"
  fi
  [ "$(stat -c %a "$(home_de "$p")")" = "750" ] || chmod 0750 "$(home_de "$p")"       # /home/<p> la crea root: la persona no puede cambiarla por un enlace (/home no es suyo)
done

# ------------------------------------------------------------------ 2. carpetas de /srv/editor
install -d -m 0755 -o root -g root "$RAIZ" "$RAIZ/personas"
install -d -m 2750 -o puerta -g editores "$RAIZ/drive" "$RAIZ/drive/OFERTAS LATAM"
install -d -m 0700 -o puerta -g puerta "$RAIZ/estado" "$RAIZ/estado/trabajo"
# la puerta de mejoras: .promover (de root; la caja de pruebas necesita poder entrar a su carpeta de trabajo) y .versiones (copias de lo reemplazado, solo root)
install -d -m 0755 -o root -g root "$BASE/.promover"
install -d -m 0700 -o root -g root "$BASE/.versiones"
for p in "${PERSONAS[@]}"; do
  install -d -m 0755 -o root -g root "$RAIZ/personas/$p"
  for s in cola entregas resultados procesadas mejoras; do install -d -m 2770 -o root -g "ed-$p" "$RAIZ/personas/$p/$s"; done
done
decir "Carpetas de $RAIZ: listas"

# ------------------------------------------------------------------ 3. topes de la cuenta de cada persona
for p in "${PERSONAS[@]}"; do
  uid="$(id -u "$p")"
  d="/etc/systemd/system/user-$uid.slice.d"
  install -d -m 0755 "$d"
  cat > "$d/50-editor.conf" <<EOF
# Topes de la cuenta de $p (los pone instalar-editor-vps.sh): el servidor es chico (2 nucleos, 8 GB) y lo comparten el radar, el runner y el corredor de anuncios
[Slice]
CPUWeight=50
CPUQuota=150%
MemoryHigh=3500M
MemoryMax=4G
MemorySwapMax=0
TasksMax=800
IOWeight=50
EOF
done
systemctl daemon-reload
decir "Topes de memoria y procesador por cuenta: puestos"

# ------------------------------------------------------------------ 4. la puerta (una instancia por persona: el limite de arranques de una no frena a las demas)
install -m 0644 -o root -g root "$BASE/vps/editor-puerta.service" /etc/systemd/system/editor-puerta@.service
install -m 0644 -o root -g root "$BASE/vps/editor-puerta.path" /etc/systemd/system/editor-puerta@.path
# lo de las primeras versiones (una sola unidad y un vigilante con comodin que no despertaba a nadie)
if [ -e /etc/systemd/system/editor-puerta.path ] || [ -e /etc/systemd/system/editor-puerta.service ] || [ -d /etc/systemd/system/editor-puerta.service.d ]; then
  systemctl disable --now editor-puerta.path >/dev/null 2>&1 || true
  rm -rf /etc/systemd/system/editor-puerta.path /etc/systemd/system/editor-puerta.service /etc/systemd/system/editor-puerta.service.d
fi
install -m 0644 -o root -g root "$BASE/vps/editor-promover.service" /etc/systemd/system/editor-promover@.service
install -m 0644 -o root -g root "$BASE/vps/editor-promover.path" /etc/systemd/system/editor-promover@.path
systemctl daemon-reload
for p in "${PERSONAS[@]}"; do
  systemctl enable "editor-puerta@$p.path" >/dev/null 2>&1 || true
  systemctl restart "editor-puerta@$p.path"
  systemctl enable "editor-promover@$p.path" >/dev/null 2>&1 || true
  systemctl restart "editor-promover@$p.path"
done
decir "Puerta de Drive: unidades y vigilantes puestos ($(for p in "${PERSONAS[@]}"; do printf '%s=%s ' "$p" "$(systemctl is-active "editor-puerta@$p.path")"; done))"

# ------------------------------------------------------------------ 5. envoltorios
cat > /usr/local/bin/puerta <<'EOF'
#!/bin/bash
# Puerta de Drive del equipo de video: puerta traer|entregar|respaldar|estado  (ver /opt/anuncios/scripts/puerta_editor.py)
exec /opt/broll-tiktok/venv/bin/python -I -B -X utf8 /opt/anuncios/scripts/puerta_editor.py "$@"
EOF
chmod 0755 /usr/local/bin/puerta; chown root:root /usr/local/bin/puerta
install -m 0755 -o root -g root "$BASE/vps/pesado" /usr/local/bin/pesado
install -d -m 0755 -o root -g root /opt/editor-claude
if [ ! -x "$CLAUDE_DEST" ]; then
  if [ -x "$CLAUDE_ORIGEN" ]; then
    install -m 0755 -o root -g root "$CLAUDE_ORIGEN" "$CLAUDE_DEST.nuevo"; mv -f "$CLAUDE_DEST.nuevo" "$CLAUDE_DEST"
    decir "Claude Code copiado de $CLAUDE_ORIGEN ($("$CLAUDE_DEST" --version | head -1))"
  else
    decir "AVISO: no hay copia de Claude Code ($CLAUDE_ORIGEN no existe): la app de escritorio lo instala sola al conectar, o instalalo con el instalador oficial como la persona."
  fi
fi
[ ! -x "$CLAUDE_DEST" ] || ln -sf "$CLAUDE_DEST" /usr/local/bin/claude

# ------------------------------------------------------------------ 6. la casa y el espacio de trabajo de cada persona
bloque_perfil() {
  cat <<'EOF'
# >>> editor-vps (lo pone instalar-editor-vps.sh; no lo borres)
export PATH="$HOME/bin:$PATH"
export OFERTAS_LATAM_DIR="/srv/editor/drive/OFERTAS LATAM"
export HF_HUB_OFFLINE=1
export OFFTHREADVIDEO_CACHE_MB=384
export EDITOR_SIN_VENV=1
export TZ=America/Bogota
# <<< editor-vps
EOF
}
casa() {
  # TODO esto corre COMO LA PERSONA (ver la regla del encabezado). El bloque del perfil va al PRINCIPIO de .bashrc y .profile: el .bashrc de Ubuntu sale pronto si la shell no es
  # interactiva, y asi tambien lo ven las sesiones que abre la app por ssh. ~/.claude/settings.json lleva las mismas variables porque la app NO lee el perfil de la shell.
  local p="$1"
  como_usuario "$p" BLOQUE="$(bloque_perfil)" GUIA="$BASE/vps/CLAUDE-VPS.md" RAIZ_ED="$RAIZ" bash -s <<'EOS'
set -e
umask 027
# si la persona cambio una de sus carpetas por un enlace (a proposito o por error), se quita el enlace y se hace la carpeta de verdad: nunca se escribe a traves de un enlace
for d in bin trabajos proyectos .claude; do [ ! -L "$HOME/$d" ] || rm -f "$HOME/$d"; done
mkdir -p "$HOME/bin" "$HOME/trabajos" "$HOME/proyectos" "$HOME/.claude"
rm -f "$HOME/bin/python.nuevo" "$HOME/bin/python3.nuevo" "$HOME/CLAUDE.md.nuevo" "$HOME/.claude/settings.json.nuevo" "$HOME/.bashrc.nuevo" "$HOME/.profile.nuevo"
printf '#!/bin/bash\nexec /opt/broll-tiktok/venv/bin/python "$@"\n' > "$HOME/bin/python.nuevo"
chmod 755 "$HOME/bin/python.nuevo"; cp "$HOME/bin/python.nuevo" "$HOME/bin/python3.nuevo"
mv -f "$HOME/bin/python.nuevo" "$HOME/bin/python"; mv -f "$HOME/bin/python3.nuevo" "$HOME/bin/python3"
for f in "$HOME/.bashrc" "$HOME/.profile"; do
  [ ! -L "$f" ] || rm -f "$f"
  [ -e "$f" ] || : > "$f"
  sed -i '/^# >>> editor-vps/,/^# <<< editor-vps/d' "$f"
  { printf '%s\n' "$BLOQUE"; cat "$f"; } > "$f.nuevo"
  cat "$f.nuevo" > "$f"; rm -f "$f.nuevo"
done
cat "$GUIA" > "$HOME/CLAUDE.md.nuevo"; mv -f "$HOME/CLAUDE.md.nuevo" "$HOME/CLAUDE.md"
if [ ! -e "$HOME/.claude/settings.json" ]; then
  cat > "$HOME/.claude/settings.json.nuevo" <<EOF
{
  "env": {
    "OFERTAS_LATAM_DIR": "$RAIZ_ED/drive/OFERTAS LATAM",
    "HF_HUB_OFFLINE": "1",
    "OFFTHREADVIDEO_CACHE_MB": "384",
    "EDITOR_SIN_VENV": "1",
    "TZ": "America/Bogota",
    "PATH": "$HOME/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  }
}
EOF
  mv -f "$HOME/.claude/settings.json.nuevo" "$HOME/.claude/settings.json"
fi
EOS
}
espacio() {
  local p="$1" home ws stage
  home="$(home_de "$p")"; ws="$home/editor-anuncios"
  casa "$p"
  if [ -e "$ws" ] || [ -L "$ws" ]; then decir "Espacio de $p: ya estaba (no toco lo suyo)"; return 0; fi
  decir "Armando el espacio de $p (copia del editor que ya corre en el servidor; unos minutos)..."
  # Se arma en una carpeta de ROOT a la que la persona no llega, se le pasa el dueno ahi, y se mueve entera con un renombrado: root nunca escribe dentro de su casa
  stage="$BASE/.despliegue/espacio-$p"
  rm -rf "$stage"; install -d -m 0700 -o root -g root "$stage"
  ( cd "$BASE" && tar -cf - --exclude='*.antes-*' --exclude='__pycache__' --exclude='*.nuevo' scripts src package.json package-lock.json remotion.config.ts tsconfig.json VERSION requirements.txt \
      public/sfx public/productos/LEEME.txt public/productos/producto-ejemplo ) | tar -C "$stage" -xf - --no-same-owner
  if [ -d "$BASE/vps/espacio" ]; then ( cd "$BASE/vps/espacio" && tar -cf - . ) | tar -C "$stage" -xf - --no-same-owner; fi
  # Remotion: copia propia de node_modules (con su navegador): asi la persona puede renderizar y actualizar la cache de webpack sin tocar lo de root
  cp -a "$BASE/node_modules" "$stage/node_modules"
  rm -rf "$stage/node_modules/.cache"
  ln -sfn "$BASE/modelos" "$stage/modelos"
  mkdir -p "$stage/anuncios" "$stage/public/musica" "$stage/public/productos"
  ln -sfn "$RAIZ/drive/musica" "$stage/public/musica/biblioteca"
  printf '%s %s\n' "$(cat "$BASE/VERSION")" "$(date -Is)" > "$stage/.espacio-instalado"
  chown -R "$p:$p" "$stage"          # (chown -R no sigue enlaces simbolicos: modelos y biblioteca quedan como enlaces)
  chmod -R go-rwx "$stage"
  # /opt y /home son el mismo sistema de archivos (comprobado), asi que esto es un rename atomico: no sigue enlaces y falla si el destino ya existe y no esta vacio
  if [ -e "$ws" ] || [ -L "$ws" ]; then
    rm -rf "$stage"
    fallar "El espacio de $p aparecio mientras se armaba (alguien lo creo): no se toca. Corre --aplicar otra vez."
  fi
  [ "$(stat -c %d "$BASE")" = "$(stat -c %d "$home")" ] || fallar "$BASE y $home estan en sistemas de archivos distintos: el espacio no se puede mover con un solo renombrado."
  mv -T "$stage" "$ws"
  # lo que habia en produccion cuando ella copio su espacio: la base para saber despues si un archivo lo mejoro ella o cambio produccion (puerta publicar)
  "$PY" -I -B -X utf8 "$BASE/scripts/editor_promover.py" base-inicial --persona "$p" || decir "AVISO: no pude escribir la base inicial de $p (puerta publicar lo pedira archivo por archivo)"
  decir "Espacio de $p: listo ($(du -xsh "$ws" | cut -f1))"
}
for p in "${PERSONAS[@]}"; do espacio "$p"; done

# ------------------------------------------------------------------ 7. su llave (si se dio)
if [ -n "$PERSONA_ARG" ]; then dar_acceso "$PERSONA_ARG" "$CLAVE_ARG"; fi

# ------------------------------------------------------------------ verificacion
decir "Verificando..."
fallos=0
ok() { echo "  OK    $*"; }
mal() { echo "  FALLA $*"; fallos=$((fallos + 1)); }
systemd-analyze verify /etc/systemd/system/editor-promover@.service "/etc/systemd/system/editor-promover@${PERSONAS[0]}.path" /etc/systemd/system/editor-puerta@.service "/etc/systemd/system/editor-puerta@${PERSONAS[0]}.service" "/etc/systemd/system/editor-puerta@${PERSONAS[0]}.path" 2>&1 | grep -v 'google.json' | head -8 || true
for p in "${PERSONAS[@]}"; do [ "$(systemctl is-active "editor-puerta@$p.path")" = "active" ] && ok "el vigilante de peticiones de $p esta activo" || mal "editor-puerta@$p.path no esta activo"; done
"$PY" -I -B -c "import ast,sys; ast.parse(open('$BASE/scripts/puerta_editor.py',encoding='utf-8').read())" && ok "puerta_editor.py compila" || mal "puerta_editor.py no compila"
"$PY" -I -B -c "import ast,sys; ast.parse(open('$BASE/scripts/editor_promover.py',encoding='utf-8').read())" && ok "editor_promover.py compila" || mal "editor_promover.py no compila"
for p in "${PERSONAS[@]}"; do [ "$(systemctl is-active "editor-promover@$p.path")" = "active" ] && ok "el vigilante de mejoras de $p esta activo" || mal "editor-promover@$p.path no esta activo"; done
for p in "${PERSONAS[@]}"; do
  # lo que la persona NO debe poder: leer las llaves, entrar a otros servicios, usar sudo
  for ruta in /opt/broll-tiktok/secretos/google.json /opt/broll-tiktok/secretos/dash.env /opt/anuncios/secretos/apimart.key /opt/radar-de-ofertas /opt/actions-runner /root /home/radar /home/ghrunner /opt/anuncios/estado /opt/anuncios/drive /opt/anuncios/costos.jsonl /opt/broll-tiktok/estado; do
    if runuser -u "$p" -- test -r "$ruta" 2>/dev/null; then mal "$p PUEDE leer $ruta"; fi
  done
  ok "$p no lee llaves ni carpetas de otros servicios"
  runuser -u "$p" -- sudo -n true 2>/dev/null && mal "$p tiene sudo" || ok "$p no tiene sudo"
  runuser -u "$p" -- test -w "$RAIZ/drive" && mal "$p puede escribir en el espejo de Drive" || ok "$p no escribe en el espejo de Drive"
  runuser -u "$p" -- test -w "$RAIZ/personas/$p/cola" && ok "$p escribe en su cola" || mal "$p no puede escribir en su cola"
  runuser -u "$p" -- test -w "$RAIZ/personas/$p" && mal "$p puede mover su carpeta de peticiones" || ok "$p no puede mover su carpeta de peticiones"
  runuser -u "$p" -- test -w "$BASE/src" && mal "$p puede escribir en el editor de produccion" || ok "$p no escribe en el editor de produccion (solo lo cambia la puerta de mejoras)"
  runuser -u "$p" -- test -w "$RAIZ/personas/$p/mejoras" && ok "$p escribe en su carpeta de mejoras" || mal "$p no puede escribir en su carpeta de mejoras"
  runuser -u "$p" -- test -r "$BASE/.versiones" && mal "$p puede leer las copias de versiones" || ok "$p no lee las copias de versiones"
  runuser -u "$p" -- test -w "$RAIZ/estado" && mal "$p puede escribir en el estado de la puerta" || ok "$p no escribe en el estado de la puerta"
  como_usuario "$p" bash -c 'source ~/.profile; python --version >/dev/null && node -v >/dev/null && ffmpeg -version >/dev/null && echo ok' >/dev/null 2>&1 && ok "$p: python, node y ffmpeg responden" || mal "$p: falta python, node o ffmpeg en su entorno"
  [ ! -x "$CLAUDE_DEST" ] || { como_usuario "$p" "$CLAUDE_DEST" --version >/dev/null 2>&1 && ok "$p: claude responde" || mal "$p: claude no responde"; }
  [ "$(stat -c %U "$BASE/modelos")" = "root" ] && ok "los modelos del servidor siguen siendo de root (el espacio de $p solo los enlaza)" || mal "$BASE/modelos cambio de dueno"
done
[ "$fallos" -eq 0 ] || fallar "La verificacion encontro $fallos problema(s): arreglalos antes de darle acceso a nadie."
decir "=== Listo. Acceso: --dar-acceso <persona> <archivo con su llave publica>. ==="
estado
