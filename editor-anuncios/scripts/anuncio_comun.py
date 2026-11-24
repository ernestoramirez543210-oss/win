"""Piezas que comparten guion_auto.py y voz_auto.py (etapas S3 y S4 del anuncio automatico, sin tokens de Claude).

Aqui vive lo que las dos necesitan igual: donde esta cada archivo de un anuncio, la receta con sus valores de fabrica, la regla de dinero del editor
(nunca se gasta sin mostrar el costo y tener el OK del dueño: o lo da `--tope-usd` para ESA corrida, o lo trae la receta en `aprobacion.topeUsd`)
y el aviso de avance a la pagina /fabrica (el guion y la voz salen en la barra de cada anuncio, igual que el plan y el render del montaje).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, PUBLIC, leer_json, salir  # noqa: E402

# AD1..AD12 (los anuncios de antes) o 101..130 (los nuevos: el dueño los numera por oferta desde el 101, como en Meta; desde 2026-10-22)
ID_ANUNCIO = re.compile(r"^(AD([1-9]|1[0-2])|1(0[1-9]|[12][0-9]|30))\Z")
NOMBRE_PRODUCTO = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,40}\Z")     # puede empezar por «_»: asi se llaman los productos de las autopruebas. \Z y no $: "AD1\n" no es AD1

# Los valores de fabrica de la receta que usan estos scripts: copia de RECETA_DEFECTO de functions/_fabrica.js (si alla cambia un valor, se cambia aqui).
RECETA_FABRICA = {
    "tipoAnuncio": "tiktok", "duracionMin": 25, "duracionMax": 60, "material": "propio",
    "idioma": {"variante": "neutro"}, "guion": {"origen": "traducir-referencia"},
    "voz": {"origen": "tts", "genero": "mujer", "nombre": "", "velocidad": 1.1},
    "textosGrandes": {"min": 5, "max": 7}, "cta": {"texto": "", "subtexto": "", "segundos": 2.2},
    "aprobacion": {"plan": "mostrar", "topeUsd": 0}, "notas": "",
}


def plano(texto) -> str:
    """Minusculas, sin tildes y sin caracteres invisibles: para comparar textos sin que importen los acentos, las mayusculas, las letras de ancho completo ni un
    caracter de ancho cero metido a mitad de una palabra (que partiria «garantia» en dos para una regla de busqueda)."""
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) not in ("Mn", "Cf"))


def tokens(texto) -> list[str]:
    """Las palabras de un texto, en minusculas y sin tildes ni signos."""
    return re.findall(r"[a-z0-9]+", plano(texto))


def decir(mensaje: str = "") -> None:
    print(mensaje, flush=True)


def huella_guion(frases) -> str:
    """Huella del TEXTO que se dice en voz alta (las frases del guion, sin lo visual): sirve para saber si una voz o una transcripcion son de ESTE guion.
    Acepta frases como texto o como diccionarios con `texto` (la forma de guion.json). Cambiar solo lo visual (descripciones, textos grandes, boton) no la cambia."""
    limpias = [re.sub(r"\s+", " ", str(f.get("texto") if isinstance(f, dict) else f)).strip() for f in (frases or [])]
    return hashlib.sha1("\n".join(f for f in limpias if f).encode("utf-8")).hexdigest()


def comprobar_ids(producto: str, anuncio: str) -> None:
    if not NOMBRE_PRODUCTO.match(producto or ""):
        salir(f"Producto no valido: {producto!r} (minusculas, numeros, guion y guion bajo).")
    if not ID_ANUNCIO.match(anuncio or ""):
        salir(f"Anuncio no valido: {anuncio!r} (es AD1 a AD12).")


def carpeta_anuncio(producto: str, anuncio: str, tipo: str = "tiktok") -> Path:
    return ANUNCIOS / producto / tipo / anuncio


def carpeta_producto(producto: str) -> Path:
    return PUBLIC / "productos" / producto


def carpeta_referencia(producto: str) -> Path:
    return carpeta_producto(producto) / "referencia"


def _por_ruta(datos, ruta: str):
    cur = datos
    for p in ruta.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None, False
        cur = cur[p]
    return cur, True


def val(receta: dict, ruta: str, defecto=None):
    """receta['a']['b'] con la ruta «a.b»; el valor de fabrica si falta."""
    v, hay = _por_ruta(receta, ruta)
    if hay:
        return v
    v, hay = _por_ruta(RECETA_FABRICA, ruta)
    return v if hay else defecto


def leer_receta(base: Path) -> dict:
    """receta.json de la carpeta del anuncio (la deja `fabrica.py traer`); sin ella, valores de fabrica."""
    ruta = base / "receta.json"
    if not ruta.is_file():
        return json.loads(json.dumps(RECETA_FABRICA))
    try:
        r = leer_json(ruta)
    except ValueError:
        salir(f"receta.json de {base.name} no se puede leer (JSON roto).")
    return r if isinstance(r, dict) else json.loads(json.dumps(RECETA_FABRICA))


def numero(v, defecto: float, minimo: float, maximo: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return defecto
    return min(maximo, max(minimo, n))


# ------------------------------------------------------------------ dinero
def gastado_en_anuncio(producto: str, anuncio: str, preguntar_dash: bool = False) -> float:
    """Lo ya gastado (o estimado) en ESTE anuncio, sumando costos.jsonl: el costo real si hay y, si no, el estimado (texto y voz no traen costo).

    preguntar_dash=True (avatar_auto.py y voz_auto.py, antes de gastar): las imagenes y los clips los cuenta el dash, que ve lo de TODOS los equipos (su tope por anuncio es el
    que manda); se toma lo mayor entre lo que dice el dash y lo anotado aqui (el dash puede tardar un momento en ver lo recien pagado) y se suma lo que el dash no cuenta en el
    anuncio (texto y voz, estimados aqui). Si el dash no contesta, o con el APIMart falso de las autopruebas, se usa solo costos.jsonl de este equipo.
    El corredor del servidor lo llama sin preguntar (solo cuenta lo que gasta el, y lo mira antes y despues de cada etapa)."""
    import apimart as A

    imagenes_y_clips, lo_demas = 0.0, 0.0
    ruta = A.COSTOS
    if Path(ruta).is_file():
        for linea in Path(ruta).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                f = json.loads(linea)
            except ValueError:
                continue
            if not isinstance(f, dict) or f.get("producto") != producto or f.get("anuncio") != anuncio:
                continue
            c = f.get("costo")
            if c is None:
                c = f.get("costo_estimado")
            try:
                c = max(0.0, float(c or 0))         # un costo negativo (un archivo tocado a mano) no baja lo gastado ni sube el tope
            except (TypeError, ValueError):
                continue
            if f.get("tipo") in ("imagen", "video"):
                imagenes_y_clips += c
            else:
                lo_demas += c
    if preguntar_dash:
        ficha = A.ficha_del_dash(producto, anuncio)
        if ficha is not None:
            return max(imagenes_y_clips, ficha["gastado_usd"]) + lo_demas
        if not A.modo_directo():
            decir("  (no pude preguntarle al dash cuanto lleva este anuncio: cuento solo lo anotado en este equipo; el tope lo aplica el dash igual)")
    return imagenes_y_clips + lo_demas


def tope_disponible(tope_usd: float | None, receta: dict, gastado: float) -> tuple[float | None, str]:
    """(dolares que se pueden gastar ahora, de donde sale el permiso). None = nadie dio el OK: no se gasta nada.
    --tope-usd es el OK del dueño para ESTA corrida; si no se pasa, manda aprobacion.topeUsd de la receta (instruccion del dueño por anuncio) menos lo ya gastado."""
    if tope_usd is not None:
        return max(0.0, float(tope_usd)), "--tope-usd (el OK del dueño para esta corrida)"
    de_receta = numero(val(receta, "aprobacion.topeUsd", 0), 0.0, 0.0, 20.0)
    if de_receta > 0:
        return max(0.0, de_receta - gastado), f"la receta (aprobacion.topeUsd = US$ {de_receta:g}, menos lo ya gastado en este anuncio: US$ {gastado:.3f})"
    return None, ""


# ------------------------------------------------------------------ avance en la pagina /fabrica
def avisador_de_avance(base: Path, anuncio: str, mostrar=decir):
    """Una funcion `avisar(**datos)` que le cuenta al dash como va este anuncio (la pagina /fabrica pinta la etapa, la barra y el guion), o una que no hace nada.
    Misma regla que el montaje (montaje_auto.reporte_de_fabrica): solo avisa si el anuncio viene de la Fabrica (su orden.json lleva el proyecto) y este editor tiene DASH_URL y DASH_CLAVE_EDITOR.
    De buena fe: un fallo hablando con el dash se dice en una linea y NUNCA frena al guion ni a la voz (que ya estan hechos y, la voz, pagados)."""
    try:
        proyecto = str(leer_json(base / "orden.json")["proyecto"]["id"])
        import fabrica

        hay_url = os.environ.get("DASH_URL", "").strip() or fabrica.leer_env("DASH_URL")
        hay_llave = os.environ.get("DASH_CLAVE_EDITOR", "").strip() or fabrica.leer_env("DASH_CLAVE_EDITOR")
    except Exception:  # noqa: BLE001 - un orden.json roto o hostil (hasta uno que revienta el analizador de JSON) solo significa «no avisar»: el aviso nunca cambia el resultado de quien lo pide
        return lambda **datos: False
    if not (hay_url and hay_llave):
        return lambda **datos: False

    def avisar(**datos) -> bool:
        try:
            return bool(fabrica.reportar_progreso(proyecto, anuncio, mostrar=mostrar, **datos))
        except Exception as e:  # noqa: BLE001 - de buena fe: nada de esto debe tumbar a quien llama
            mostrar(f"  (aviso: no pude avisar el avance al dash: {e})")
            return False
    return avisar
