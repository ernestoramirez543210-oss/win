#!/bin/bash
# Trae al repo las mejoras que el equipo de video publico DESDE EL SERVIDOR con `puerta publicar` (puerta de mejoras) y que todavia no estan en git, para que git siga siendo la fuente de verdad y el
# proximo desplegar-corredor.sh no las pise. Se corre desde el PC (Git Bash), en el repo del dash:
#
#   bash editor-anuncios/vps/traer-promovidas.sh [--simular]
#
# Que hace: lee /opt/anuncios/.versiones/promovidas.json del servidor, baja cada archivo (comprobando su SHA-256), lo deja en editor-anuncios/<ruta> y, si es un archivo nuevo (un componente
# o un programa que no estaba en vps/corredor-archivos.txt), lo agrega a esa lista. NO hace commit: despues revisa con `git diff` / `git status`, haz commit y despliega (el despliegue vuelve a
# comprobar y retira del servidor lo que ya esta en git).
# Entra con la misma llave que el radar (~/.ssh/radar_vps; otra con CORREDOR_VPS_LLAVE).
set -euo pipefail

VPS="${CORREDOR_VPS:-root@203.0.113.10}"
LLAVE="${CORREDOR_VPS_LLAVE:-$HOME/.ssh/radar_vps}"
DESTINO=/opt/anuncios
SIMULAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --simular) SIMULAR=1 ;;
    *) echo "Opcion desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done

RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"
[ -f "$LLAVE" ] || { echo "Falta la llave SSH del servidor: $LLAVE" >&2; exit 1; }
SSH=(ssh -i "$LLAVE" -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=yes "$VPS")
LISTA_GIT="editor-anuncios/vps/corredor-archivos.txt"

PROMOVIDAS="$("${SSH[@]}" "cat $DESTINO/.versiones/promovidas.json 2>/dev/null || true")"
[ -n "$PROMOVIDAS" ] || { echo "No hay mejoras publicadas desde el servidor pendientes de git."; exit 0; }
# destino <TAB> ruta-en-el-repo <TAB> sha <TAB> quien
LINEAS="$(printf '%s' "$PROMOVIDAS" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{for(const [k,v] of Object.entries(JSON.parse(s)))console.log([k,v.repo,v.sha,v.persona].join("\t"))})')"
[ -n "$LINEAS" ] || { echo "No hay mejoras publicadas desde el servidor pendientes de git."; exit 0; }

NUEVAS=0
while IFS=$'\t' read -r dest repo sha quien; do
  [ -n "$dest" ] || continue
  for v in "$dest" "$repo"; do
    case "$v" in ''|*..*|/*|*[!A-Za-z0-9._/-]*) echo "Ruta no permitida en promovidas.json: '$v'" >&2; exit 1 ;; esac
  done
  [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || { echo "SHA mal hecho para $dest" >&2; exit 1; }
  local_ruta="editor-anuncios/$repo"
  if [ -f "$local_ruta" ] && [ "$(tr -d '\r' < "$local_ruta" | sha256sum | cut -d' ' -f1)" = "$sha" ]; then
    echo "  =  $repo (ya esta igual en el repo)"
    continue
  fi
  estado="~  (cambia)"; [ -f "$local_ruta" ] || estado="+  (nuevo)"
  echo "  ${estado%% *}  $repo   <- $quien   $estado"
  [ "$SIMULAR" -eq 0 ] || continue
  tmp="$(mktemp)"
  "${SSH[@]}" "cat $DESTINO/$dest" > "$tmp" < /dev/null          # (sin esto ssh se come la lista que lee el bucle)
  if [ "$(sha256sum "$tmp" | cut -d' ' -f1)" != "$sha" ]; then
    rm -f "$tmp"
    echo "  ERROR: $dest cambio en el servidor mientras lo bajaba (o ya no es el que se publico). No se toco nada de ese archivo." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$local_ruta")"
  cat "$tmp" > "$local_ruta"
  rm -f "$tmp"
  # un archivo que viaja por el corredor y todavia no esta en la lista: se agrega (los de vps/espacio/* ya estan)
  if ! git show "HEAD:$LISTA_GIT" | tr -d '\r' | awk -v d="$dest" '$2 == d {f=1} END {exit !f}' && ! tr -d '\r' < "$LISTA_GIT" | awk -v d="$dest" '$2 == d {f=1} END {exit !f}'; then
    printf '%-40s %-40s 644\n' "$repo" "$dest" >> "$LISTA_GIT"
    NUEVAS=$((NUEVAS + 1))
    echo "     (agregado a $LISTA_GIT)"
  fi
done <<< "$LINEAS"

if [ "$SIMULAR" -eq 1 ]; then echo "Simulacion: no se toco el repo."; exit 0; fi
echo
echo "Listo. Revisa con:  git status ; git diff"
[ "$NUEVAS" -eq 0 ] || echo "Archivos nuevos que viajaran por el corredor: agrega tambien a DENEGADAS (functions/api/equipo.js) y a equipo-kit/zonas.json los que sean de CODIGO (.py/.mjs); las pruebas (scripts/probar-equipo.mjs) te lo exigen."
echo "Despues: commit y  bash editor-anuncios/vps/desplegar-corredor.sh"
