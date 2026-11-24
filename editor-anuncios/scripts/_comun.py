"""Utilidades compartidas por los scripts del editor de anuncios."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

RAIZ = Path(__file__).resolve().parents[1]
PUBLIC = RAIZ / "public"
ANUNCIOS = RAIZ / "anuncios"
EXT_VIDEO = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

# antes de empezar, avatar_auto.py (para no dejar un anuncio a medias).
TOPE_ANUNCIO_USD = 0.50
TECHO_ANUNCIO_USD = 1.00            # ni con --tope-anuncio se pasa de esto
HOSTS_DASH = ("tu_proyecto.pages.dev",)


def es_local(url: str) -> bool:
    """http a esta misma maquina (los servidores falsos de las autopruebas). Se mira la direccion ya separada:
    «http://127.0.0.1:1@otro.com/» empieza igual pero va a otro.com."""
    try:
        p = urlsplit(str(url))
        p.port  # noqa: B018 - lanza ValueError si el puerto no es un numero
    except ValueError:
        return False
    return p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost") and "@" not in p.netloc


def direccion_dash_valida(url: str) -> bool:
    """La llave del dash (DASH_CLAVE_EDITOR) solo viaja al dash de verdad por https, o a esta maquina en las pruebas: ni por http, ni con usuario
    o puerto, ni a otro sitio aunque sea https (unas «dos lineas del .env» cambiadas en un mensaje no pueden mandarla a otro lado)."""
    if es_local(url):
        return True
    try:
        p = urlsplit(str(url))
        puerto = p.port
    except ValueError:
        return False
    return (p.scheme == "https" and (p.hostname or "") in HOSTS_DASH and "@" not in p.netloc and puerto is None
            and p.path in ("", "/") and not p.query and not p.fragment)


def version_editor(raiz: Path | None = None) -> str:
    """La version instalada (el archivo VERSION de la carpeta del editor, p. ej. 2026.11.10-2) o '' si falta o esta rara. Va en la cabecera x-version-editor de lo que se
    le pide al dash (apimart.py, voz_auto.py): el dash niega con 426 las copias viejas, para que nadie siga gastando con reglas de antes."""
    try:
        v = ((raiz or RAIZ) / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return v if re.fullmatch(r"[0-9A-Za-z._-]{1,40}", v) else ""


def asegurar_entorno() -> None:
    """Si el proyecto tiene su propio entorno de Python (.venv, lo crea Instalar-Editor.ps1), vuelve a
    lanzar el script con ese Python. Asi da igual cual `python` encuentre la consola: las librerias de IA
    siempre salen del entorno del editor y nunca se mezclan con las de otros proyectos del PC.
    EDITOR_SIN_VENV=1 lo desactiva."""
    if os.environ.get("EDITOR_SIN_VENV"):
        return
    venv_py = RAIZ / ".venv" / "Scripts" / "python.exe"
    if not venv_py.is_file() or not sys.argv or not sys.argv[0] or not Path(sys.argv[0]).is_file():
        return
    try:
        if Path(sys.prefix).resolve() == (RAIZ / ".venv").resolve():
            return
    except OSError:
        return
    sys.exit(subprocess.call([str(venv_py), *sys.argv]))


asegurar_entorno()


def configurar_consola() -> None:
    """La consola de Windows usa cp1252: sin esto, los acentos revientan los print()."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def salir(mensaje: str, codigo: int = 1) -> None:
    print(f"ERROR: {mensaje}", file=sys.stderr)
    sys.exit(codigo)


def producto_dir(producto: str) -> Path:
    carpeta = PUBLIC / "productos" / producto
    if not carpeta.is_dir():
        salir(f"No existe la carpeta del producto: {carpeta}")
    return carpeta


def leer_json(ruta: Path | str):
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def escribir_json(ruta: Path | str, datos) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ejecutable(nombre: str) -> str:
    camino = shutil.which(nombre)
    if not camino:
        salir(f"No encuentro '{nombre}' en el PATH. Cierra y abre la app de Claude (o reinicia el PC) y repite.")
    return camino


def correr(comando: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(comando, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)


def sonda(ruta: Path | str) -> dict:
    """ffprobe en JSON: duracion, flujos de video y audio."""
    r = correr([ejecutable("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)])
    if r.returncode != 0:
        salir(f"ffprobe no pudo leer {ruta}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def duracion_seg(ruta: Path | str) -> float:
    info = sonda(ruta)
    return float(info.get("format", {}).get("duration") or 0)


def formato_tiempo(seg: float) -> str:
    seg = max(0.0, seg)
    return f"{int(seg // 60)}:{int(seg % 60):02d}"


# Largo maximo (letras) del texto grande del gancho. Cabe en 4 renglones de la caja de Remotion a 112 px (medido con «sorprende a todos con trescientas recetas», 41 letras);
# un texto mas largo se corta por palabras completas (recortar_por_palabras). Lo usan gancho_ia.py (al guardar el plan) y montaje_auto.py (al armar el videoData).
MAX_LETRAS_TEXTO_GANCHO = 44

# Palabras sueltas con las que una frase no puede terminar («cenas completas en» no dice nada).
_PALABRITAS_FINALES = frozenset("a al ante con contra de del e el en entre hacia hasta la las lo los mas más mi mis ni o para por que se segun según si sin sobre su sus tu tus u un una unos unas y".split())


def recortar_por_palabras(texto: str, maximo: int) -> str:
    """El texto con a lo sumo `maximo` letras, SIN partir nunca una palabra («CENAS COMPLETAS EN TREINTA MIN» salio asi de cortar a los 30 letras un «…TREINTA MINUTOS»).
    Si no cabe entero se queda con las palabras que caben y quita del final las palabritas sueltas («con», «de», «en»…) y la coma que quede, para no terminar a medias; si queda un
    «¿» o «¡» abierto sin su cierre, lo quita. Una sola palabra mas larga que el maximo se deja entera: quien la muestra (TextoGrandeVista) achica la letra para que quepa."""
    limpio = " ".join(str(texto or "").split())
    if len(limpio) <= maximo:
        return limpio
    palabras = limpio.split(" ")
    entran: list[str] = []
    for p in palabras:
        if len(" ".join(entran + [p])) > maximo:
            break
        entran.append(p)
    if not entran:
        return palabras[0]
    while len(entran) > 1 and entran[-1].strip(".,;:!?¡¿\"'()-–—").lower() in _PALABRITAS_FINALES:
        entran.pop()
    corto = " ".join(entran).rstrip(" ,;:-–—")
    if corto[:1] in ("¿", "¡") and not any(c in corto for c in ("?", "!")):
        corto = corto[1:]
    return corto
