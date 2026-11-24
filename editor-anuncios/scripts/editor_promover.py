"""Puerta de MEJORAS del editor: lo que Beto mejora en su espacio del servidor llega al editor que usa el dash, con revision automatica y copia de respaldo.

Por que existe: el botón «Crear toda la oferta» del dash usa el editor de /opt/anuncios (de root). El espacio de trabajo de cada persona del equipo de video es una COPIA aparte
(~/editor-anuncios); lo que mejora ahi no llegaba a produccion hasta que alguien lo copiara a mano. Esta puerta lo hace sin darle permisos a nadie:

  - CLIENTE (`puerta publicar`, lo corre la persona sin privilegios): arma un paquete .tar con SOLO los archivos permitidos que cambiaron respecto a produccion y lo deja en
    /srv/editor/personas/<persona>/mejoras. Espera el resultado.
  - SERVIDOR (`editor_promover.py servir`, lo lanza systemd como ROOT al ver un paquete): lee el paquete como DATOS (nunca ejecuta nada suyo ni sigue rutas de la persona), lo clasifica y:
      * la parte visual de Remotion (src/FullVideo.tsx, Root.tsx, types.ts, index.ts y src/components/*.tsx):  AUTOMATICO. Antes de instalar se revisa en una caja sin red, sin llaves y sin permisos
        (usuario `nobody`): revision por arbol sintactico (nada que lea archivos al empaquetar, salga a la red o ejecute texto) y render del anuncio de demostracion, del ejemplo fijo de efectos
        y de las pruebas del paquete con el codigo nuevo. Si todo sale bien se instala con copia de lo reemplazado (.versiones/) y el dash lo usa en el siguiente anuncio.
      * scripts/*.py y *.mjs (corren con las llaves de pago del corredor), src/index.css (Tailwind lo ejecuta al empaquetar) y las guias (CLAUDE.md, skills; las leen otros Claude):
        quedan PENDIENTES con su diferencia: los instala el dueno con `aprobar`.
      * cualquier otra cosa (llaves, puerta, Drive, corredor, paquetes npm, configuracion):  se RECHAZA.
  - Antes de instalar toma el candado del corredor: nunca cambia el editor a mitad de un anuncio.
  - Si producción cambió desde que la persona copió su base (por ejemplo un arreglo desplegado desde git), el archivo se rechaza («conflicto»): no se pisa el trabajo de nadie.
  - `--revertir` devuelve todo a como estaba. `promovidas.json` lista lo que esta en produccion y aun no esta en git (desplegar-corredor.sh no lo pisa; traer-promovidas.sh lo trae al repo).

Uso (servidor, root):  editor_promover.py servir --persona P | estado | aprobar <pendiente> | rechazar <pendiente> | revertir <version>
Uso (persona):         puerta publicar [--simular] [--solo-seguros] | puerta sincronizar [--traer] <archivo>...
"""
from __future__ import annotations

import argparse
import ast
import difflib
import fcntl
import hashlib
import io
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

try:
    import pwd
except ImportError:                     # pragma: no cover  (Windows: solo se puede importar para comprobar la sintaxis)
    pwd = None

BASE = Path("/opt/anuncios")
RAIZ = Path("/srv/editor")
MB = 1024 * 1024
RE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
RE_PERSONA = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
RE_PEND = re.compile(r"^[a-z][a-z0-9_-]{1,30}-[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
O_SEGURO = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
O_DIR = getattr(os, "O_DIRECTORY", 0)

MAX_ARCHIVO = 400 * 1024
MAX_TOTAL = 4 * MB
MAX_ARCHIVOS = 80
MAX_PAQUETE = 6 * MB
MAX_PRUEBAS = 3
PAQUETES_POR_HORA = 6
DISCO_LIBRE_MIN_GB = 5
ESPERA_CANDADO_S = 90 * 60

# ---- que se puede publicar (lista cerrada)
SRC_FIJOS = ("src/FullVideo.tsx", "src/Root.tsx", "src/types.ts", "src/index.ts")
REVISION_FIJOS = ("src/index.css",)        # Tailwind 4 lo procesa en Node al empaquetar (@plugin, @config, @source cargan modulos): no se instala solo
RE_COMPONENTE = re.compile(r"^src/components/[A-Za-z][A-Za-z0-9_]{0,59}\.tsx$")
RE_SCRIPT = re.compile(r"^scripts/([A-Za-z][A-Za-z0-9_]{0,59})\.(py|mjs)$")
# Lo que protege o maneja credenciales, colas, Drive o este mismo mecanismo: NUNCA viaja por aqui (se cambia desde git y desplegar-corredor.sh)
SCRIPTS_DENEGADOS = frozenset({"puerta_editor", "editor_promover", "autoprueba_puerta", "autoprueba_promover", "autoprueba_corredor", "prueba_servidor", "drive_api", "corredor_vps", "espejo_drive", "apimart", "apimart_chat", "revisar_prompts", "autoprueba_revisar_prompts", "revisar_src",
                               "requests", "numpy", "PIL", "faster_whisper", "torch", "clip", "cv2", "pip", "setuptools", "sitecustomize", "usercustomize"})
RE_DOC = re.compile(r"^\.claude/skills/(?:editor-video|fabrica-ofertas)/(?:SKILL\.md|references/[A-Za-z0-9._-]{1,60}\.md)$")
RE_PRUEBA = re.compile(r"^pruebas/[A-Za-z0-9_-]{1,40}\.json$")

# ---- revision del codigo visual (src/**): nada de red, procesos, ejecucion dinamica ni modulos que no sean los del proyecto
PROHIBIDO = [
    (r"\bfetch\s*\(", "fetch (red)"), (r"\bXMLHttpRequest\b", "XMLHttpRequest (red)"), (r"\bWebSocket\b", "WebSocket (red)"), (r"\bEventSource\b", "EventSource (red)"),
    (r"\bsendBeacon\b", "sendBeacon (red)"), (r"https?://", "una direccion de internet"), (r"\beval\s*\(", "eval"), (r"\bnew\s+Function\b", "new Function"),
    (r"\brequire\b", "require (tambien require.context y parecidos: juntan carpetas enteras al empaquetar)"), (r"__webpack\w*|__non_webpack\w*|\bwebpack\w*|\bmodule\s*[.\[]|\bexports\s*[.\[]|\bdefine\s*\(", "internos de webpack, CommonJS o AMD"), (r"\bimport\s*\(", "import() dinamico"), (r"\bprocess\s*\.", "process"), (r"child_process", "child_process"),
    (r"\bimportScripts\b", "importScripts"), (r"\bnew\s+Worker\s*\(", "Worker"), (r"\bdocument\s*\.\s*cookie\b", "cookies"), (r"\b(?:local|session)Storage\b", "almacenamiento del navegador"),
    (r"<\s*(?:iframe|object|embed|script|link|base|img|video|audio|source|form|meta)\b", "una etiqueta HTML que carga cosas de fuera (usa los componentes de remotion con staticFile)"),
    (r"\b(?:srcDoc|srcSet|href|poster|action|formAction|ping)\s*=", "un atributo que carga o enlaza algo de fuera"),
    (r"\burl\s*\(\s*(?![\"']?#)", "url() que no es una referencia interna como url(#filtro) (carga de recursos)"), (r"[\"'`]\s*//\s*[A-Za-z0-9]", "una direccion que empieza por //"), (r"\bimport\.meta\b", "import.meta"),
    (r"\bnew\s+Image\b", "new Image"), (r"\bdocument\s*\.(?!fonts\b|createElement\(\s*[\"']canvas[\"']\s*\))", "document (solo document.fonts y un canvas para medir texto)"),
    (r"\bpostMessage\b", "postMessage"), (r"\bglobalThis\b|\bwindow\s*\[|\bself\s*[.\[]|\bthis\s*\[|\bReflect\b|\bFunction\b|\.constructor\b", "acceso dinamico a objetos globales o a Function"),
]
RE_SRC_ATRIBUTO = re.compile(r"\bsrc\s*=\s*(\{?)\s*([^\s>}]*)")
RE_IMPORT = re.compile(r"""(?:\bfrom\s+|\bimport\s+)["']([^"']+)["']""")
IMPORT_OK = re.compile(r"^(?:\.{1,2}/[A-Za-z0-9_./-]+|react|remotion|@remotion/[a-z-]+|@fontsource/[a-z0-9-]+(?:/[A-Za-z0-9._-]+)?)$")


class ErrorMejora(Exception):
    """Algo salio mal; el mensaje esta escrito para una persona (o su Claude)."""


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _decir(t: str = "") -> None:
    print(t, flush=True)


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_archivo(ruta: Path) -> str | None:
    """SHA-256 de un archivo normal; None si no existe. Un enlace simbolico o algo que no es un archivo normal es un error (nada de esto sigue enlaces)."""
    try:
        st = os.lstat(ruta)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise ErrorMejora(f"{ruta} no es un archivo normal")
    with open(ruta, "rb") as f:
        return sha_bytes(f.read())


# ------------------------------------------------------------------ que archivo es cada cosa
def clasificar(ruta: str) -> tuple[str, str] | tuple[str, None]:
    """(nivel, destino bajo BASE). nivel: auto | revision | doc | prueba | no. Para 'no', el segundo valor es None."""
    if ruta in SRC_FIJOS or RE_COMPONENTE.match(ruta):
        return "auto", ruta
    if ruta in REVISION_FIJOS:
        return "revision", ruta
    m = RE_SCRIPT.match(ruta)
    if m:
        # un programa que se llame como un modulo de la libreria estandar taparia ese modulo en todos los scripts del corredor (scripts/ va primero en su ruta de busqueda)
        return ("no", None) if m.group(1) in SCRIPTS_DENEGADOS or m.group(1) in sys.stdlib_module_names else ("revision", ruta)
    if ruta == "CLAUDE.md" or RE_DOC.match(ruta):
        return "doc", "vps/espacio/" + ruta         # las leen otros Claude (el del dueno, tras traer-promovidas): esperan el visto bueno como los programas
    if RE_PRUEBA.match(ruta):
        return "prueba", None
    return "no", None


def revisar_codigo_visual(ruta: str, datos: bytes) -> list[str]:
    """Problemas de seguridad del codigo de src/ (lista vacia = limpio). Es una barrera de errores y de codigo con malas ideas, no un sandbox: el sandbox es la caja donde se renderiza."""
    problemas: list[str] = []
    try:
        texto = datos.decode("utf-8")
    except UnicodeDecodeError:
        return [f"{ruta}: no es texto UTF-8"]
    if "\x00" in texto:
        return [f"{ruta}: tiene bytes nulos"]
    if ruta.endswith(".css"):
        return [f"{ruta}: un CSS no se instala solo (Tailwind lo ejecuta al empaquetar): pasa por el visto bueno del dueno"]
    # Se revisa el texto ENTERO, comentarios incluidos: quitar comentarios con una regex se puede burlar con una cadena que contenga «//» o «/*»
    for patron, que in PROHIBIDO:
        if re.search(patron, texto):
            problemas.append(f"{ruta}: usa {que}, que un video no necesita y no se permite")
    for esp in RE_IMPORT.findall(texto):
        if not IMPORT_OK.match(esp) or ".." in esp.replace("../", "").replace("./", ""):
            problemas.append(f"{ruta}: importa «{esp}» (solo se permite lo del proyecto, react, remotion, @remotion/* y @fontsource/*)")
        elif esp.startswith("."):
            # lo relativo no puede salir de src/ (../node_modules/..., ../scripts/...) ni traer CSS (salvo el index.css del proyecto desde src/index.ts)
            destino = posixpath.normpath(posixpath.join(posixpath.dirname(ruta), esp))
            if not destino.startswith("src/") or "node_modules" in destino:
                problemas.append(f"{ruta}: importa «{esp}», que sale de src/")
            elif destino.endswith(".css") and destino != "src/index.css":
                problemas.append(f"{ruta}: importa un CSS («{esp}»): no se permite")
    for abre, valor in RE_SRC_ATRIBUTO.findall(texto):
        # src={staticFile(...)} o src={una.ruta.de.props}; nunca un texto escrito ni algo armado
        if not (abre == "{" and (valor.startswith("staticFile(") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.?]*", valor))):
            problemas.append(f"{ruta}: un src solo puede ser staticFile(...) o una propiedad (se vio «{valor[:40]}»)")
    return problemas


# ------------------------------------------------------------------ el paquete
def armar_tar(persona: str, archivos: dict[str, bytes], base: dict[str, str | None]) -> bytes:
    """El paquete: PAQUETE.json primero y despues los archivos (dueno 0, modo 0644, sin fechas)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        meta = json.dumps({"version": 1, "persona": persona, "base": base}, ensure_ascii=False).encode("utf-8")
        for nombre, datos in [("PAQUETE.json", meta), *sorted(archivos.items())]:
            ti = tarfile.TarInfo(nombre)
            ti.size, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = len(datos), 0o644, 0, 0, 0, "", ""
            tf.addfile(ti, io.BytesIO(datos))
    return buf.getvalue()


def leer_tar(datos: bytes, persona: str) -> tuple[dict, dict[str, bytes]]:
    """Lee el paquete COMO DATOS. Devuelve (meta, {ruta: bytes}). Nunca extrae a disco; cualquier cosa rara lo rechaza entero."""
    if len(datos) > MAX_PAQUETE:
        raise ErrorMejora(f"El paquete pesa mas de {MAX_PAQUETE // MB} MB.")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(datos), mode="r:")
        miembros = tf.getmembers()
    except (tarfile.TarError, EOFError, OSError):
        raise ErrorMejora("El paquete no es un .tar valido (sin comprimir).") from None
    if not miembros or miembros[0].name != "PAQUETE.json":
        raise ErrorMejora("El paquete tiene que empezar por PAQUETE.json (usa `puerta publicar`, no lo armes a mano).")
    if len(miembros) - 1 > MAX_ARCHIVOS:
        raise ErrorMejora(f"El paquete trae mas de {MAX_ARCHIVOS} archivos.")
    archivos: dict[str, bytes] = {}
    meta: dict = {}
    total = 0
    for m in miembros:
        if not m.isfile():
            raise ErrorMejora(f"«{m.name}» no es un archivo normal (enlaces, carpetas y archivos especiales no viajan).")
        nombre = m.name
        if m.size > (64 * 1024 if nombre == "PAQUETE.json" else MAX_ARCHIVO):
            raise ErrorMejora(f"«{nombre}» es demasiado grande.")
        if nombre != "PAQUETE.json" and (len(nombre) > 150 or nombre.startswith("/") or "\\" in nombre or "\x00" in nombre or os.path.normpath(nombre) != nombre or nombre.startswith("..")):
            raise ErrorMejora(f"El nombre «{nombre[:80]}» no es valido.")
        if nombre in archivos or (nombre == "PAQUETE.json" and meta):
            raise ErrorMejora(f"«{nombre}» viene repetido.")
        f = tf.extractfile(m)
        cont = f.read() if f else b""
        if len(cont) != m.size:
            raise ErrorMejora(f"«{nombre}» esta incompleto.")
        total += len(cont)
        if total > MAX_TOTAL:
            raise ErrorMejora(f"El paquete suma mas de {MAX_TOTAL // MB} MB.")
        if nombre == "PAQUETE.json":
            try:
                meta = json.loads(cont.decode("utf-8"))
            except ValueError:
                raise ErrorMejora("PAQUETE.json no es JSON valido.") from None
        else:
            archivos[nombre] = cont
    if not isinstance(meta, dict) or meta.get("version") != 1 or meta.get("persona") != persona or not isinstance(meta.get("base"), dict):
        raise ErrorMejora("PAQUETE.json no corresponde a esta persona o esta mal hecho.")
    for k, v in meta["base"].items():
        if not isinstance(k, str) or not (v is None or (isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v))):
            raise ErrorMejora("PAQUETE.json: la base no es valida.")
    return meta, archivos


# ================================================================== SERVIDOR (root)
def _nodo() -> str:
    ruta = os.path.realpath("/usr/local/bin/node")
    if not os.path.isfile(ruta):
        raise ErrorMejora("No encuentro node en el servidor.")
    return ruta


def caja(args: list[str], cwd: Path, escribibles: list[Path], timeout_s: int, nombre: str) -> tuple[int, str]:
    """Corre node en una caja de systemd: usuario nobody, sin red, sin las llaves ni las carpetas de otros servicios, solo escribe donde se le dice, con tope de memoria y tiempo."""
    cmd = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", f"--unit={nombre}", "--uid=nobody", "--gid=nogroup",
           "-p", "PrivateNetwork=yes", "-p", "PrivateTmp=yes", "-p", "ProtectSystem=strict", "-p", "ProtectHome=yes", "-p", "NoNewPrivileges=yes",
           "-p", "ReadWritePaths=" + " ".join(str(p) for p in escribibles),
           "-p", "MemoryMax=2G", "-p", "MemorySwapMax=0", "-p", "CPUQuota=100%", "-p", f"RuntimeMaxSec={timeout_s}", "-p", "Nice=15", "-p", "IOSchedulingClass=idle", "-p", "TasksMax=512",
           "-p", "ProtectProc=invisible", "-p", "PrivateDevices=yes", "-p", "CapabilityBoundingSet=", "-p", "TemporaryFileSystem=/opt:ro",
           "-p", f"BindReadOnlyPaths={os.path.dirname(os.path.dirname(_nodo()))}", "-p", f"BindPaths={cwd.parent}",
           "-p", f"WorkingDirectory={cwd}",
           "-p", "InaccessiblePaths=-/opt/anuncios/secretos -/opt/anuncios/estado -/opt/anuncios/drive -/opt/anuncios/anuncios -/opt/anuncios/.versiones -/opt/anuncios/.promover/pendientes -/opt/anuncios/logs "
                 "-/opt/broll-tiktok/secretos -/opt/broll-tiktok/estado -/root -/home -/srv/editor -/opt/radar-de-ofertas -/opt/actions-runner -/run/docker.sock",
           "-E", "HOME=/tmp", "-E", "OFFTHREADVIDEO_CACHE_MB=384", "-E", "TZ=America/Bogota", "--", _nodo(), *args]
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s + 60, errors="replace")
    except subprocess.TimeoutExpired:
        return 124, "se pasó del tiempo"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def resumen_de_falla(texto: str) -> str:
    """Lo que se le cuenta a la persona de una falla de render: solo lineas de error que nombran archivos de src/, cortas. El log completo queda en el registro de root (nunca devolver el log crudo)."""
    utiles = [re.sub(r"\s+", " ", l)[:200] for l in texto.splitlines() if re.search(r"src/[A-Za-z0-9_./-]+", l) and re.search(r"error|Error|ERROR|Cannot|Can't|Unexpected|not found|Module", l)]
    return "\n".join(utiles[:6]) or "(sin detalle: el dueno puede verlo en el registro del servidor)"


def armar_proyecto(stage: Path, sobrepuestos: dict[str, bytes]) -> Path:
    """Copia de lo que hay en produccion + los archivos nuevos encima. node_modules va por enlaces duros (no gasta disco; los archivos siguen siendo de root: la caja no puede cambiarlos)."""
    os.chmod(stage, 0o755)
    proy = stage / "p"
    proy.mkdir(mode=0o755, parents=True)
    os.chmod(proy, 0o755)
    ignorar = shutil.ignore_patterns("*.antes-*", "__pycache__", "*.nuevo")
    for d in ("scripts", "src"):
        shutil.copytree(BASE / d, proy / d, symlinks=True, ignore=ignorar)
    for f in ("package.json", "tsconfig.json", "remotion.config.ts", "VERSION"):
        if (BASE / f).is_file():
            shutil.copy2(BASE / f, proy / f)
    subprocess.run(["cp", "-al", str(BASE / "node_modules"), str(proy / "node_modules")], check=True)
    cache = proy / "node_modules" / ".cache"
    if cache.is_symlink() or cache.exists():
        cache.unlink() if cache.is_symlink() else shutil.rmtree(cache)
    for d in (cache, proy / ".cache", stage / "salida"):
        d.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.chown(d, "nobody", "nogroup")
    for ruta, datos in sobrepuestos.items():
        dest = proy / ruta
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(datos)
        os.chmod(dest, 0o644)
    for raiz, carpetas, archivos in os.walk(proy / "src"):
        for n in carpetas:
            os.chmod(os.path.join(raiz, n), 0o755)
    return proy


def revisar_arbol(proy: Path, rutas: list[str], id_: str) -> list[str]:
    """Pasa scripts/revisar_src.mjs (revision por arbol sintactico) sobre los archivos de src/ del paquete, en la caja. Si la revision no contesta como se espera, se considera FALLA (nunca se instala a ciegas)."""
    rutas = [x for x in rutas if x.endswith((".ts", ".tsx"))]
    if not rutas:
        return []
    if not (proy / "scripts" / "revisar_src.mjs").is_file():
        return ["falta scripts/revisar_src.mjs en el servidor (la revision del codigo visual no se puede hacer)"]
    rc, texto = caja(["scripts/revisar_src.mjs", *rutas], proy, [], 180, f"editor-promover-arbol-{id_}"[:80])
    linea = next((l for l in texto.splitlines() if l.startswith("RESULTADO_JSON ")), "")
    try:
        res = json.loads(linea[len("RESULTADO_JSON "):])
        problemas = [str(p)[:200] for p in res["problemas"]]
        especificadores = res["imports"]
    except (ValueError, KeyError, TypeError):
        _log(f"REVISION POR ARBOL SIN RESPUESTA {id_} rc={rc}:\n" + "\n".join(texto.strip().splitlines()[-20:]))
        return ["la revision por arbol sintactico no dio una respuesta valida (se rechaza por seguridad)"]
    for archivo, esp in especificadores:
        if not IMPORT_OK.match(esp) or ".." in esp.replace("../", "").replace("./", ""):
            problemas.append(f"{archivo}: importa «{str(esp)[:60]}», que no se permite")
        elif esp.startswith("."):
            destino = posixpath.normpath(posixpath.join(posixpath.dirname(archivo), esp))
            if not destino.startswith("src/") or "node_modules" in destino or (destino.endswith(".css") and destino != "src/index.css"):
                problemas.append(f"{archivo}: importa «{esp}», que sale de src/ o trae un CSS")
    return problemas


def probar_en_caja(stage: Path, sobrepuestos: dict[str, bytes], pruebas: dict[str, bytes], id_: str) -> tuple[bool, str]:
    """Revisa por arbol sintactico y renderiza fotogramas del anuncio de demostracion, del ejemplo fijo de efectos y de las pruebas del paquete con el codigo nuevo. (ok, explicacion)"""
    fijo = BASE / "vps" / "promover-fixture.json"
    if not fijo.is_file():
        return False, "falta vps/promover-fixture.json en el servidor: sin el ejemplo fijo no se puede comprobar que los efectos de siempre sigan funcionando (avisale al dueno)"
    proy = armar_proyecto(stage, sobrepuestos)
    problemas = revisar_arbol(proy, [r for r in sobrepuestos], id_)
    if problemas:
        return False, "La revision del codigo visual encontro:\n" + "\n".join(problemas[:8])
    casos = {"demo": (BASE / "src" / "config" / "videoData.json").read_bytes(), "efectos": fijo.read_bytes()}
    casos.update({f"prueba-{Path(k).stem}": v for k, v in list(pruebas.items())[:MAX_PRUEBAS]})
    for nombre, datos in casos.items():
        (proy / f"caso-{nombre}.json").write_bytes(datos)
        os.chmod(proy / f"caso-{nombre}.json", 0o644)
        salida = stage / "salida" / nombre
        salida.mkdir(mode=0o755)
        os.chmod(salida, 0o755)
        shutil.chown(salida, "nobody", "nogroup")
        rc, texto = caja(["scripts/remotion.mjs", "fotogramas", f"caso-{nombre}.json", "--escala", "0.25", "--salida", str(salida)], proy,
                         [proy / ".cache", proy / "node_modules" / ".cache", stage / "salida"], 600, f"editor-promover-prueba-{id_}-{nombre}"[:80])
        jpgs = sorted(salida.glob("*.jpg"))
        if rc != 0 or "LISTO" not in texto or not jpgs:
            _log(f"FALLA DE RENDER {id_} caso {nombre} rc={rc}:\n" + "\n".join(texto.strip().splitlines()[-40:]))
            return False, f"El caso «{nombre}» no se pudo renderizar con el codigo nuevo:\n{resumen_de_falla(texto)}"
        if max(j.stat().st_size for j in jpgs) < 2000:
            return False, f"El caso «{nombre}» salio en blanco con el codigo nuevo ({len(jpgs)} fotogramas casi vacios)."
    return True, f"{len(casos)} caso(s) renderizados bien ({', '.join(casos)})."


class Candado:
    """El candado del corredor (el mismo de correr_corredor.sh): mientras lo tengo no arranca ninguna pasada. Espera hasta ESPERA_CANDADO_S a que termine la que este en curso."""

    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta or (BASE / "estado" / ".candado-corredor")
        self.fd = -1

    def __enter__(self):
        try:
            self.fd = os.open(self.ruta, os.O_RDONLY | O_SEGURO)
        except FileNotFoundError:
            return self                                   # sin candado = no hay corredor que cuidar
        limite = time.monotonic() + ESPERA_CANDADO_S
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() > limite:
                    raise ErrorMejora("El corredor esta trabajando un anuncio y no termino en 90 minutos: no se instalo nada (se puede volver a intentar).") from None
                time.sleep(15)

    def __exit__(self, *a):
        if self.fd >= 0:
            os.close(self.fd)


def _escribir_atomico(dest: Path, datos: bytes, modo: int = 0o644) -> None:
    if dest.is_symlink() or (dest.exists() and not dest.is_file()):
        raise ErrorMejora(f"{dest} no es un archivo normal: no se toca.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".nuevo")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_SEGURO, modo)
    try:
        os.write(fd, datos)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chown(tmp, 0, 0)                                   # (las pruebas corren sin ser root)
    except PermissionError:
        pass
    os.chmod(tmp, modo)
    os.replace(tmp, dest)


def _cargar_json(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _log(texto: str) -> None:
    (BASE / "logs").mkdir(parents=True, exist_ok=True)
    with open(BASE / "logs" / "promover.log", "a", encoding="utf-8") as f:
        f.write(f"{_ahora()} {texto}\n")


def instalar(archivos: dict[str, bytes], persona: str, id_: str, etiqueta: str, base: dict[str, str | None], origen_repo: dict[str, str]) -> dict:
    """Pone los archivos en produccion (destino -> bytes) con copia de lo reemplazado. Debe llamarse con el Candado tomado. Devuelve el resumen (para el resultado)."""
    carpeta = BASE / ".versiones" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{persona}-{id_}"
    carpeta.mkdir(mode=0o700, parents=True)
    # 1) se vuelve a comprobar la base JUSTO antes de tocar nada (con el candado ya tomado)
    for dest in archivos:
        actual = sha_archivo(BASE / dest)
        if actual != base.get(dest):
            raise ErrorMejora(f"{dest}: produccion cambio mientras se revisaba (conflicto): no se instalo nada.")
    hechos: list[dict] = []
    prom_antes = _cargar_json(BASE / ".versiones" / "promovidas.json", {})
    prom = dict(prom_antes)
    plan = [{"destino": d, "nuevo": sha_archivo(BASE / d) is None, "sha_antes": sha_archivo(BASE / d), "sha_despues": sha_bytes(c)} for d, c in sorted(archivos.items())]
    (carpeta / "manifest.json").write_text(json.dumps({"persona": persona, "id": id_, "etiqueta": etiqueta, "cuando": _ahora(), "estado": "en_curso", "archivos": plan}, ensure_ascii=False, indent=1), encoding="utf-8")
    for h in plan:
        prom[h["destino"]] = {"sha": h["sha_despues"], "repo": origen_repo[h["destino"]], "persona": persona, "id": id_, "version": carpeta.name, "cuando": _ahora()}
    _escribir_atomico(BASE / ".versiones" / "promovidas.json", json.dumps(prom, ensure_ascii=False, indent=1).encode("utf-8"), 0o600)
    try:
        for dest, datos in sorted(archivos.items()):
            antes = sha_archivo(BASE / dest)
            if antes is not None:
                bk = carpeta / "archivos" / dest
                bk.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(BASE / dest, bk)
            _escribir_atomico(BASE / dest, datos)
            hechos.append({"destino": dest, "nuevo": antes is None, "sha_antes": antes, "sha_despues": sha_bytes(datos)})
    except Exception:
        for h in reversed(hechos):                         # algo fallo a medias: se devuelve lo ya puesto
            try:
                if h["nuevo"]:
                    (BASE / h["destino"]).unlink(missing_ok=True)
                else:
                    shutil.copy2(carpeta / "archivos" / h["destino"], BASE / h["destino"])
            except OSError:
                pass
        _escribir_atomico(BASE / ".versiones" / "promovidas.json", json.dumps(prom_antes, ensure_ascii=False, indent=1).encode("utf-8"), 0o600)
        raise
    (carpeta / "manifest.json").write_text(json.dumps({"persona": persona, "id": id_, "etiqueta": etiqueta, "cuando": _ahora(), "estado": "listo", "archivos": hechos}, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"INSTALADO {etiqueta} {persona} {id_}: {', '.join(h['destino'] for h in hechos)} (copia en {carpeta.name})")
    return {"version": carpeta.name, "archivos": [h["destino"] for h in hechos], "base_nueva": {h["destino"]: h["sha_despues"] for h in hechos}}


def _diferencia(dest: str, nuevo: bytes) -> str:
    try:
        viejo = (BASE / dest).read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        viejo = []
    nuevo_l = nuevo.decode("utf-8", errors="replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(viejo, nuevo_l, f"produccion/{dest}", f"propuesta/{dest}", n=3))


def procesar_paquete(persona: str, id_: str, datos: bytes) -> dict:
    """Un paquete a la vez y con el candado del corredor tomado de principio a fin: lo que se prueba es exactamente lo que se instala (ni otro paquete ni un despliegue cambian produccion en medio)."""
    try:
        with Candado():
            return _procesar_paquete(persona, id_, datos)
    except ErrorMejora as e:
        return {"id": id_, "persona": persona, "cuando": _ahora(), "estado": "rechazado", "instalados": [], "pendientes": [], "rechazos": [], "mensaje": str(e)}


def _procesar_paquete(persona: str, id_: str, datos: bytes) -> dict:
    """Todo el trabajo de un paquete. Devuelve el resultado que se le muestra a la persona. No lanza error: lo convierte en un rechazo."""
    resultado: dict = {"id": id_, "persona": persona, "cuando": _ahora(), "estado": "rechazado", "instalados": [], "pendientes": [], "rechazos": [], "mensaje": ""}
    stage = BASE / ".promover" / f"{persona}-{id_}"
    try:
        meta, crudos = leer_tar(datos, persona)
        base: dict = meta["base"]
        auto: dict[str, bytes] = {}
        docs: dict[str, bytes] = {}
        revision: dict[str, bytes] = {}
        pruebas: dict[str, bytes] = {}
        for ruta, cont in sorted(crudos.items()):
            nivel, dest = clasificar(ruta)
            if nivel == "no":
                # `puerta publicar` nunca arma un paquete con esto: si llega, alguien lo armo a mano. Se rechaza el paquete ENTERO.
                resultado["rechazos"].append({"ruta": ruta, "motivo": "este archivo no se puede publicar por aqui (solo la parte visual de Remotion, las guias y los programas de montaje con visto bueno del dueno)"})
                resultado["mensaje"] = "El paquete trae archivos que no se pueden publicar por aqui: no se instalo nada."
                return resultado
            if nivel == "prueba":
                try:
                    j = json.loads(cont.decode("utf-8"))
                    if not isinstance(j, dict) or not isinstance(j.get("meta"), dict):
                        raise ValueError
                except ValueError:
                    resultado["rechazos"].append({"ruta": ruta, "motivo": "una prueba tiene que ser un videoData.json valido (con «meta»)"})
                    continue
                pruebas[ruta] = cont
                continue
            actual = sha_archivo(BASE / dest)
            if actual == sha_bytes(cont):
                continue                                            # ya es lo que esta en produccion
            if base.get(ruta) != actual:
                resultado["rechazos"].append({"ruta": ruta, "motivo": "CONFLICTO: produccion cambio desde que copiaste tu base (alguien desplego un arreglo). Mira la diferencia, pasa a tu archivo lo que cambio alla y corre `puerta sincronizar " + ruta + "`"})
                continue
            (auto if nivel == "auto" else revision)[ruta] = cont       # guias, CSS y programas: visto bueno del dueno
        for ruta, cont in revision.items():
            if ruta.endswith(".py"):
                try:
                    ast.parse(cont.decode("utf-8"), ruta)
                except (SyntaxError, UnicodeDecodeError, ValueError) as e:
                    resultado["rechazos"].append({"ruta": ruta, "motivo": f"no compila: {e}"})
        revision = {k: v for k, v in revision.items() if not any(r["ruta"] == k for r in resultado["rechazos"])}
        for ruta, cont in auto.items():
            for problema in revisar_codigo_visual(ruta, cont):
                resultado["rechazos"].append({"ruta": ruta, "motivo": problema})
        malos = {r["ruta"] for r in resultado["rechazos"]}
        auto = {k: v for k, v in auto.items() if k not in malos}
        if resultado["rechazos"] and (auto or docs) and any("src/" in r["ruta"] for r in resultado["rechazos"]):
            # un archivo visual malo contamina el conjunto (los demas pueden depender de el): no se instala nada visual
            auto = {}
            resultado["rechazos"].append({"ruta": "(conjunto)", "motivo": "como un archivo visual fue rechazado, no se instalo ninguno de la parte visual (dependen unos de otros)"})
        if not (auto or docs or revision):
            resultado["estado"] = "sin_cambios" if not resultado["rechazos"] else "rechazado"
            resultado["mensaje"] = "No hay nada nuevo que instalar." if not resultado["rechazos"] else "No se instalo nada: mira los motivos."
            return resultado
        if auto:
            shutil.rmtree(stage, ignore_errors=True)
            (BASE / ".promover").mkdir(mode=0o755, exist_ok=True)
            stage.mkdir(mode=0o755)
            ok, explicacion = probar_en_caja(stage, auto, pruebas, id_)
            resultado["revision_automatica"] = explicacion
            if not ok:
                resultado["rechazos"].append({"ruta": "(prueba de render)", "motivo": explicacion})
                resultado["mensaje"] = "La prueba de render fallo: no se instalo la parte visual."
                auto = {}
                revision = {}
        # Por orden del dueno (5-nov): programas, guias y CSS de la persona (Beto, experto en edicion) se instalan SIN visto bueno, tras las mismas revisiones; con copia en .versiones para revertir.
        todos = {**auto, **docs, **revision}
        a_instalar = {dest_de(k): v for k, v in todos.items()}
        if a_instalar:
            repo = {dest_de(k): k for k in todos}
            base_dest = {dest_de(k): base.get(k) for k in todos}
            res = instalar(a_instalar, persona, id_, "automatico", base_dest, repo)       # (el candado ya esta tomado por procesar_paquete)
            resultado["instalados"] = sorted(todos)
            resultado["version"] = res["version"]
            resultado["base_nueva"] = {k: res["base_nueva"][dest_de(k)] for k in todos}
        if resultado["instalados"]:
            resultado["estado"] = "instalado"
            resultado["mensaje"] = "Instalado en produccion: el dash ya usa tus mejoras en el siguiente anuncio."
        elif resultado["pendientes"]:
            resultado["estado"] = "pendiente"
            resultado["mensaje"] = "Los programas de montaje esperan el visto bueno del dueno (corren con las llaves de pago, por eso los revisa una persona)."
        else:
            resultado["estado"] = "rechazado"
            resultado["mensaje"] = resultado["mensaje"] or "No se instalo nada: mira los motivos."
    except ErrorMejora as e:
        resultado["estado"] = "rechazado"
        resultado["mensaje"] = str(e)
    except Exception as e:                                          # un error inesperado no puede dejar a la persona sin respuesta
        resultado["estado"] = "rechazado"
        resultado["mensaje"] = f"Error inesperado del servidor ({e.__class__.__name__}): avisale al dueno."
        _log(f"ERROR {persona} {id_}: {e!r}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    _log(f"{resultado['estado'].upper()} {persona} {id_}: instalados={resultado['instalados']} pendientes={resultado['pendientes']} rechazos={[r['ruta'] for r in resultado['rechazos']]}")
    return resultado


def dest_de(ruta: str) -> str:
    nivel, dest = clasificar(ruta)
    assert dest is not None
    return dest


def servir(persona: str) -> int:
    """Atiende los paquetes de UNA persona. Todo lo de su carpeta se abre por descriptores (dir_fd + O_NOFOLLOW): ni un enlace ni un cambio de carpeta la puede desviar."""
    if not RE_PERSONA.match(persona):
        raise ErrorMejora("nombre de persona no valido")
    try:
        uid = pwd.getpwnam(persona).pw_uid
    except KeyError:
        raise ErrorMejora(f"{persona} no existe") from None
    ruta = RAIZ / "personas" / persona / "mejoras"
    dfd = os.open(ruta, os.O_RDONLY | O_DIR | O_SEGURO)
    try:
        for n in os.listdir(dfd):                  # un .tar con nombre raro se quita (si no, el vigilante de systemd despertaria al servicio una y otra vez)
            if n.endswith(".tar") and not RE_ID.match(n[:-4]):
                try:
                    os.unlink(n, dir_fd=dfd)
                except OSError:
                    try:
                        os.rename(n, f"{secrets.token_hex(4)}.malo", src_dir_fd=dfd, dst_dir_fd=dfd)       # (una carpeta no se borra con unlink)
                    except OSError:
                        _log(f"NO PUDE APARTAR {persona}/{n[:60]!r}: el vigilante puede quedar en bucle")
        nombres = sorted(n for n in os.listdir(dfd) if n.endswith(".tar") and RE_ID.match(n[:-4]))
        for nombre in nombres:
            id_ = nombre[:-4]
            datos = b""
            malo = ""
            try:
                fd = os.open(nombre, os.O_RDONLY | os.O_NONBLOCK | O_SEGURO, dir_fd=dfd)      # O_NONBLOCK: un FIFO con nombre .tar no deja esperando
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_uid != uid:
                        malo = "el paquete no es un archivo normal de la persona"
                    elif st.st_size > MAX_PAQUETE:
                        malo = f"el paquete pesa mas de {MAX_PAQUETE // MB} MB"
                    else:
                        datos = os.read(fd, MAX_PAQUETE + 1)
                finally:
                    os.close(fd)
            except OSError as e:
                malo = f"no se pudo abrir el paquete ({e.__class__.__name__})"
            try:
                os.unlink(nombre, dir_fd=dfd)
            except OSError:
                try:
                    os.rename(nombre, f"{id_}.malo", src_dir_fd=dfd, dst_dir_fd=dfd)       # una carpeta llamada X.tar: se aparta para que el vigilante no despierte al servicio en bucle
                except OSError:
                    _log(f"NO PUDE QUITAR {persona}/{nombre}: el vigilante puede quedar en bucle")
            if not malo and not _cuota_ok(persona):
                malo = f"ya hubo {PAQUETES_POR_HORA} paquetes en la ultima hora: espera un rato"
            if not malo and shutil.disk_usage(BASE).free < DISCO_LIBRE_MIN_GB * 1024**3:
                malo = "el servidor tiene poco disco libre"
            if malo:
                res = {"id": id_, "persona": persona, "cuando": _ahora(), "estado": "rechazado", "instalados": [], "pendientes": [], "rechazos": [], "mensaje": malo}
            else:
                res = procesar_paquete(persona, id_, datos)
            _entregar_resultado(dfd, f"{id_}.resultado.json", res)
    finally:
        os.close(dfd)
    return 0


def _cuota_ok(persona: str) -> bool:
    """Cuenta en un archivo de ROOT cuantos paquetes se procesaron en la ultima hora (la persona no puede borrar esta cuenta). True y lo anota si aun hay cupo."""
    ruta = BASE / ".promover" / f"cuota-{persona}.json"
    ahora = time.time()
    marcas = [t for t in _cargar_json(ruta, []) if isinstance(t, (int, float)) and ahora - t < 3600]
    if len(marcas) >= PAQUETES_POR_HORA:
        return False
    marcas.append(ahora)
    try:
        _escribir_atomico(ruta, json.dumps(marcas).encode("utf-8"), 0o600)
    except OSError:
        pass
    return True


def _entregar_resultado(dfd: int, nombre: str, res: dict) -> None:
    """Deja el resultado en la carpeta de la persona (dueno root, grupo de ella por la carpeta, solo lectura para ella). La persona puede haber dejado ahi un enlace con ese nombre:
    se quita (unlink no sigue enlaces) y se crea de nuevo con O_EXCL; si ella gana la carrera, no se escribe (nunca a traves de un enlace)."""
    try:
        os.unlink(nombre, dir_fd=dfd)
    except FileNotFoundError:
        pass
    try:
        fd = os.open(nombre, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_SEGURO, 0o640, dir_fd=dfd)
    except OSError:
        return
    try:
        os.write(fd, json.dumps(res, ensure_ascii=False, indent=1).encode("utf-8"))
    finally:
        os.close(fd)


def escribir_base_inicial(persona: str, desde: float | None) -> int:
    """Escribe /srv/editor/personas/<p>/base-inicial.json (de root; la persona solo lo lee): el SHA-256 de cada archivo publicable COMO ESTABA cuando ella copio su espacio. Asi `puerta publicar`
    distingue «yo lo mejore» (produccion sigue igual a mi base) de «produccion cambio» (conflicto). Con --desde FECHA (cuando ella copio su espacio), un archivo que tenga copias .antes-* hechas
    DESPUES de esa fecha usa la mas vieja de ellas: guarda lo que habia en produccion justo antes del primer despliegue posterior, o sea lo que ella copio."""
    if not RE_PERSONA.match(persona):
        raise ErrorMejora("nombre de persona no valido")
    base: dict[str, str] = {}
    candidatos: dict[str, Path] = {}
    for rel in SRC_FIJOS + REVISION_FIJOS:
        candidatos[rel] = BASE / rel
    for patron, prefijo in (("src/components/*.tsx", ""), ("scripts/*.py", ""), ("scripts/*.mjs", "")):
        for p in (BASE).glob(patron):
            candidatos[p.relative_to(BASE).as_posix()] = p
    esp = BASE / "vps" / "espacio"
    if esp.is_dir():
        for p in [esp / "CLAUDE.md", *esp.glob(".claude/skills/*/SKILL.md"), *esp.glob(".claude/skills/*/references/*.md")]:
            if p.is_file():
                candidatos[p.relative_to(esp).as_posix()] = p
    for rel, ruta in sorted(candidatos.items()):
        nivel, dest = clasificar(rel)
        if nivel in ("no", "prueba") or not ruta.is_file() or ruta.is_symlink():
            continue
        fuente = ruta
        if desde is not None:
            viejas = sorted((q for q in ruta.parent.glob(ruta.name + ".antes-*") if q.is_file() and not q.is_symlink() and q.stat().st_ctime >= desde), key=lambda q: q.stat().st_ctime)
            if viejas:
                fuente = viejas[0]
        base[rel] = sha_bytes(fuente.read_bytes())
    destino = RAIZ / "personas" / persona / "base-inicial.json"
    _escribir_atomico(destino, json.dumps(base, indent=1, sort_keys=True).encode("utf-8"))
    _decir(f"base-inicial de {persona}: {len(base)} archivos ({destino})")
    return 0


# ---- ordenes del dueno (root)
def _pendiente(nombre: str) -> Path:
    if not RE_PEND.match(nombre):
        raise ErrorMejora("nombre de pendiente no valido (mira `editor_promover.py estado`)")
    p = BASE / ".promover" / "pendientes" / nombre
    if not p.is_dir() or p.is_symlink():
        raise ErrorMejora(f"No existe el pendiente {nombre}")
    return p


def aprobar(nombre: str) -> int:
    p = _pendiente(nombre)
    meta = _cargar_json(p / "meta.json", None)
    if not isinstance(meta, dict):
        raise ErrorMejora("meta.json del pendiente esta danado")
    archivos: dict[str, bytes] = {}
    for ruta in meta["base"]:
        nivel, dest = clasificar(ruta)
        if nivel not in ("revision", "doc"):
            raise ErrorMejora(f"{ruta} ya no es un archivo de revision")
        cont = (p / "paquete" / ruta).read_bytes()
        if ruta.endswith(".py"):
            ast.parse(cont.decode("utf-8"), ruta)
        archivos[dest] = cont
    with Candado():
        res = instalar(archivos, meta["persona"], meta["id"], "aprobado", {dest_de(k): v for k, v in meta["base"].items()}, {dest_de(k): k for k in meta["base"]})
    spool = RAIZ / "personas" / meta["persona"] / "mejoras"
    try:
        dfd = os.open(spool, os.O_RDONLY | O_DIR | O_SEGURO)
    except OSError:
        dfd = -1                                             # la persona ya no tiene carpeta: se instala igual
    try:
        if dfd >= 0:
            _entregar_resultado(dfd, f"{meta['id']}.aprobada.resultado.json", {"id": meta["id"], "persona": meta["persona"], "cuando": _ahora(), "estado": "instalado", "instalados": sorted(meta["base"]),
                                                                                    "mensaje": "El dueno aprobo tus programas de montaje: ya estan en produccion.",
                                                                                    "base_nueva": {k: res["base_nueva"][dest_de(k)] for k in meta["base"]}})
    finally:
        if dfd >= 0:
            os.close(dfd)
    shutil.rmtree(p)
    _decir(f"Aprobado e instalado: {', '.join(res['archivos'])} (copia en .versiones/{res['version']}).")
    return 0


def rechazar(nombre: str) -> int:
    shutil.rmtree(_pendiente(nombre))
    _log(f"RECHAZADO a mano {nombre}")
    _decir(f"Pendiente {nombre} descartado.")
    return 0


def revertir(version: str) -> int:
    d = BASE / ".versiones" / version
    man = _cargar_json(d / "manifest.json", None)
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-z][a-z0-9_-]*-[0-9]{8}T[0-9]{6}-[0-9a-f]{6}", version) or not isinstance(man, dict):
        raise ErrorMejora("version no valida (mira `editor_promover.py estado`)")
    prom = _cargar_json(BASE / ".versiones" / "promovidas.json", {})
    with Candado():
        for h in man["archivos"]:
            dest = BASE / h["destino"]
            if sha_archivo(dest) != h["sha_despues"]:
                _decir(f"  OJO {h['destino']}: cambio despues de esa instalacion; no lo toco (revisalo a mano).")
                continue
            if h["nuevo"]:
                dest.unlink(missing_ok=True)
                prom.pop(h["destino"], None)
            else:
                _escribir_atomico(dest, (d / "archivos" / h["destino"]).read_bytes())
                prom.pop(h["destino"], None)
            _decir(f"  devuelto {h['destino']}")
        _escribir_atomico(BASE / ".versiones" / "promovidas.json", json.dumps(prom, ensure_ascii=False, indent=1).encode("utf-8"), 0o600)
    _log(f"REVERTIDO {version}")
    return 0


def podar() -> int:
    """Lee de la entrada estandar lineas «destino sha» (lo que acaba de desplegar git) y quita de promovidas.json lo que ya es identico a git: deja de ser «aun no en git»."""
    en_git = {}
    for linea in sys.stdin.read().splitlines():
        partes = linea.split()
        if len(partes) == 2 and re.fullmatch(r"[0-9a-f]{64}", partes[1]):
            en_git[partes[0]] = partes[1]
    ruta = BASE / ".versiones" / "promovidas.json"
    prom = _cargar_json(ruta, {})
    quitar = [d for d, i in prom.items() if en_git.get(d) == i.get("sha") and sha_archivo(BASE / d) == i.get("sha")]
    for d in quitar:
        prom.pop(d)
    if quitar:
        _escribir_atomico(ruta, json.dumps(prom, ensure_ascii=False, indent=1).encode("utf-8"), 0o600)
    _decir(f"promovidas.json: {len(quitar)} archivo(s) ya estan en git y salen de la lista; quedan {len(prom)}.")
    return 0


def estado() -> int:
    prom = _cargar_json(BASE / ".versiones" / "promovidas.json", {})
    _decir(f"En produccion y aun no en git ({len(prom)}):")
    for dest, i in sorted(prom.items()):
        _decir(f"  {dest}  <- {i.get('persona')} {i.get('id')}  ({i.get('cuando')})")
    pend = BASE / ".promover" / "pendientes"
    lista = sorted(p.name for p in pend.iterdir()) if pend.is_dir() else []
    _decir(f"Pendientes de tu visto bueno ({len(lista)}):")
    for n in lista:
        _decir(f"  {n}   (ver: less {pend / n / 'diferencias.diff'} ; aprobar: editor_promover.py aprobar {n})")
    ver = BASE / ".versiones"
    vs = sorted(p.name for p in ver.iterdir() if p.is_dir()) if ver.is_dir() else []
    _decir(f"Versiones guardadas ({len(vs)}), las ultimas:")
    for n in vs[-5:]:
        _decir(f"  {n}   (devolver: editor_promover.py revertir {n})")
    return 0


# ================================================================== CLIENTE (la persona, sin privilegios)
def _espacio() -> Path:
    return Path.home() / "editor-anuncios"


def _candidatos(ws: Path) -> dict[str, Path]:
    """Archivos de su espacio que se pueden publicar (ruta relativa -> ruta)."""
    r: dict[str, Path] = {}
    for rel in SRC_FIJOS + REVISION_FIJOS + ("CLAUDE.md",):
        if (ws / rel).is_file() and not (ws / rel).is_symlink():
            r[rel] = ws / rel
    for patron in ("src/components/*.tsx", "scripts/*.py", "scripts/*.mjs", ".claude/skills/*/SKILL.md", ".claude/skills/*/references/*.md", "pruebas/*.json"):
        for p in sorted(ws.glob(patron)):
            rel = p.relative_to(ws).as_posix()
            if p.is_file() and not p.is_symlink() and clasificar(rel)[0] != "no":
                r[rel] = p
    return r


def _linea_base(ws: Path) -> dict:
    return _cargar_json(ws / ".base-produccion.json", {})


def _guardar_linea_base(ws: Path, base: dict) -> None:
    tmp = ws / ".base-produccion.json.nuevo"
    tmp.write_text(json.dumps(base, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, ws / ".base-produccion.json")


def _aplicar_resultados(ws: Path, spool: Path) -> None:
    """Lo que el servidor instalo (tambien lo aprobado despues por el dueno) pasa a ser la base de la persona."""
    base = _linea_base(ws)
    cambio = False
    for p in sorted((spool / "mejoras").glob("*resultado.json")):
        res = _cargar_json(p, {})
        for ruta, sha in (res.get("base_nueva") or {}).items():
            if base.get(ruta) != sha:
                base[ruta] = sha
                cambio = True
    if cambio:
        _guardar_linea_base(ws, base)


def cmd_publicar(cfg: dict, args) -> int:
    from puerta_editor import ErrorPuerta, _carpeta_spool, _nuevo_id, _persona_actual
    persona = _persona_actual(cfg)
    ws = _espacio()
    spool = _carpeta_spool(cfg, persona)
    if not ws.is_dir():
        raise ErrorPuerta(f"No encuentro tu espacio de trabajo ({ws}).")
    if not (spool / "mejoras").is_dir():
        raise ErrorPuerta(f"Falta {spool / 'mejoras'}: el dueno tiene que correr instalar-editor-vps.sh --aplicar para activar la puerta de mejoras.")
    _aplicar_resultados(ws, spool)
    base = {**_cargar_json(spool / "base-inicial.json", {}), **_linea_base(ws)}
    suben: dict[str, bytes] = {}
    base_envio: dict[str, str | None] = {}
    iguales = 0
    confirmadas: dict[str, str] = {}
    conflictos: list[str] = []
    sin_tocar: list[str] = []
    for rel, ruta in _candidatos(ws).items():
        nivel, dest = clasificar(rel)
        cont = ruta.read_bytes()
        if nivel == "prueba":
            suben[rel] = cont
            continue
        actual = sha_archivo(BASE / dest)
        if actual == sha_bytes(cont):
            iguales += 1
            confirmadas[rel] = actual
            continue
        if base.get(rel) == sha_bytes(cont):
            sin_tocar.append(rel)                              # tu archivo es el de siempre (no lo cambiaste) y produccion lo actualizo: no es un cambio tuyo ni un conflicto
            continue
        if base.get(rel) != actual:
            conflictos.append(rel)
            continue
        suben[rel] = cont
        base_envio[rel] = actual
    if confirmadas:
        _guardar_linea_base(ws, {**_linea_base(ws), **confirmadas})
    _decir(f"Archivos iguales a produccion: {iguales}.")
    if sin_tocar:
        _decir(f"Archivos que no cambiaste y que produccion ya actualizo ({len(sin_tocar)}): {', '.join(sorted(sin_tocar))}. Para ponerlos al dia en tu espacio:  puerta sincronizar --al-dia")
    for rel in sorted(suben):
        n, _ = clasificar(rel)
        _decir(f"  SUBE   {rel}   ({'automatico' if n == 'auto' else 'prueba del paquete' if n == 'prueba' else 'espera visto bueno del dueno (guia, CSS o programa)'})")
    for rel in conflictos:
        _decir(f"  CONFLICTO {rel}: produccion tiene otra version distinta de la que copiaste.")
        _decir(f"      mira:  diff {BASE / clasificar(rel)[1]} {ws / rel}")
        _decir(f"      si quieres lo de produccion tal cual:  puerta sincronizar --traer {rel};  si ya pasaste a tu archivo lo que cambio alla:  puerta sincronizar {rel}")
    if conflictos and not args.solo_seguros:
        _decir("No publico nada mientras haya conflictos (los archivos se necesitan unos a otros). Resuelvelos, o usa --solo-seguros bajo tu responsabilidad.")
        return 1
    real = {k for k in suben if clasificar(k)[0] != "prueba"}
    if not real:
        _decir("No hay nada nuevo que publicar.")
        return 0
    if args.simular:
        _decir("Simulacion: no se envio nada.")
        return 0
    base_paquete = {k: base_envio.get(k) for k in real}
    tar = armar_tar(persona, suben, base_paquete)
    id_ = _nuevo_id()
    tmp = spool / "mejoras" / f"{id_}.parte"
    with open(tmp, "xb") as f:
        f.write(tar)
    os.chmod(tmp, 0o660)
    os.rename(tmp, spool / "mejoras" / f"{id_}.tar")
    _decir(f"Paquete {id_} enviado ({len(tar) // 1024} KB). El servidor lo revisa y lo prueba (uno o dos minutos; mas si el corredor esta trabajando un anuncio)...")
    res_ruta = spool / "mejoras" / f"{id_}.resultado.json"
    limite = time.monotonic() + args.esperar * 60
    while time.monotonic() < limite:
        if res_ruta.is_file():
            res = _cargar_json(res_ruta, None)
            if res is None:
                time.sleep(1)
                continue
            _aplicar_resultados(ws, spool)
            _decir(f"{res.get('estado', '?').upper()}: {res.get('mensaje', '')}")
            for k in ("revision_automatica",):
                if res.get(k):
                    _decir(f"  prueba: {res[k]}")
            for r in res.get("instalados", []):
                _decir(f"  instalado: {r}")
            for r in res.get("pendientes", []):
                _decir(f"  espera visto bueno del dueno: {r}")
            for r in res.get("rechazos", []):
                _decir(f"  NO: {r['ruta']}: {r['motivo']}")
            return 0 if res.get("estado") in ("instalado", "pendiente", "sin_cambios") else 1
        time.sleep(3)
    _decir(f"El servidor no contesto en {args.esperar} min. El paquete {id_} sigue en la cola: el resultado quedara en {res_ruta}.")
    return 3


def cmd_sincronizar(cfg: dict, args) -> int:
    from puerta_editor import ErrorPuerta, _carpeta_spool, _persona_actual
    persona = _persona_actual(cfg)
    ws = _espacio()
    base = {**_cargar_json(_carpeta_spool(cfg, persona) / "base-inicial.json", {}), **_linea_base(ws)}
    if getattr(args, "al_dia", False):
        hechos = 0
        for rel, mio in _candidatos(ws).items():
            nivel, dest = clasificar(rel)
            if nivel in ("no", "prueba") or dest is None:
                continue
            actual = sha_archivo(BASE / dest)
            if actual is not None and base.get(rel) is not None and base.get(rel) != actual and sha_bytes(mio.read_bytes()) == base[rel]:
                shutil.copyfile(BASE / dest, mio)
                base[rel] = actual
                hechos += 1
                _decir(f"  {rel}: puesto al dia con produccion (tu no lo habias cambiado)")
        _guardar_linea_base(ws, base)
        _decir(f"Listo: {hechos} archivo(s) al dia.")
        return 0
    if not args.archivos:
        raise ErrorPuerta("Dime que archivos (rutas dentro de tu espacio, por ejemplo src/components/TextoGrandeVista.tsx).")
    for rel in args.archivos:
        nivel, dest = clasificar(rel)
        if nivel in ("no", "prueba") or dest is None:
            raise ErrorPuerta(f"{rel} no es un archivo que se publique por aqui.")
        actual = sha_archivo(BASE / dest)
        if args.traer:
            if actual is None:
                raise ErrorPuerta(f"{rel} no existe en produccion.")
            mio = ws / rel
            if mio.exists():
                resp = ws / f".respaldo-sincronizar-{datetime.now().strftime('%Y%m%d-%H%M%S')}" / rel
                resp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(mio, resp)
                _decir(f"  tu version quedo guardada en {resp}")
            mio.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BASE / dest, mio)
            _decir(f"  {rel}: ahora es igual a produccion")
        base[rel] = actual
    _guardar_linea_base(ws, base)
    _decir("Listo: tu base quedo al dia con produccion para esos archivos.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="editor_promover.py", description="Puerta de mejoras del editor (modo servidor/root)")
    sub = ap.add_subparsers(dest="orden", required=True)
    s = sub.add_parser("servir")
    s.add_argument("--persona", required=True)
    sub.add_parser("estado")
    sub.add_parser("podar")
    bi = sub.add_parser("base-inicial")
    bi.add_argument("--persona", required=True)
    bi.add_argument("--desde", default=None, help="fecha ISO en que la persona copio su espacio (p. ej. 2026-10-29T20:22:00+00:00)")
    for n in ("aprobar", "rechazar", "revertir"):
        sub.add_parser(n).add_argument("nombre")
    args = ap.parse_args(argv)
    os.umask(0o022)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Esto lo corre root en el servidor (los clientes usan `puerta publicar`).", file=sys.stderr)
        return 2
    try:
        return {"servir": lambda: servir(args.persona), "estado": estado, "podar": podar, "base-inicial": lambda: escribir_base_inicial(args.persona, datetime.fromisoformat(args.desde).timestamp() if args.desde else None), "aprobar": lambda: aprobar(args.nombre), "rechazar": lambda: rechazar(args.nombre), "revertir": lambda: revertir(args.nombre)}[args.orden]()
    except ErrorMejora as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
