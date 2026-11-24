"""Trae b-roll de TikTok para un producto del editor de anuncios.

Busca videos (o recibe enlaces), baja cada uno SIN marca de agua, comprueba que sirva (vertical, duracion
razonable, se abre) y lo deja en dos sitios:
  - public/productos/<producto>/broll-tiktok/    donde el editor lo indexa y lo usa (scripts/indexar_broll.py)
  - <carpeta de Drive de la oferta>/B-rolls/     para que el equipo lo vea y lo reuse (Drive para escritorio lo sube solo)
Cada video queda anotado en fuentes.csv (enlace, autor, vistas, consulta) en las dos carpetas. Lo ya bajado no se
vuelve a bajar, aunque alguien borre el archivo (asi un video que el equipo descarto no vuelve a aparecer).

Uso:
  python scripts/broll_tiktok.py traer <producto> [fuentes] [opciones]
  python scripts/broll_tiktok.py estado <producto>
  python scripts/broll_tiktok.py recoger <producto> [--drive "<carpeta de la oferta en este PC>"] [--simular]
      trae al editor lo que el SERVIDOR dejo en Drive > carpeta de la oferta > B-rolls (el servidor corre solo, ver vps/LEEME.md)

Fuentes (se pueden combinar; si no pasas ninguna se usan las de public/productos/<producto>/broll.json):
  --consulta "texto"   busqueda por palabra (repetible)             [gratis: la lista publica de TikTok, sin llave]
  --hashtag nombre     busqueda por hashtag, sin # (repetible)      [igual: se busca esa palabra]
  --cuenta usuario     los ultimos ~10 videos de una cuenta (repetible) [sin llave y sin sesion]
  --enlace URL         un video que ya tienes (repetible)
  --enlaces archivo    un enlace por linea (lo que deja Claude despues de buscar en TikTok con el Chrome del dueno,
                       o lo que pega el equipo). Lo que sigue a un # en la linea es un comentario.

Como se busca por palabra SIN pagar (descubierto el 2026-10-21): la busqueda normal de TikTok solo contesta a quien tiene la sesion iniciada
(sin sesion devuelve una respuesta vacia; probado el 2026-10-19), pero TikTok SI le muestra a cualquier visitante, en sus paginas
tiktok.com/discover/<palabra>, una lista de videos por palabra (16 por pagina, hasta ~100) y las palabras relacionadas. Esa lista publica
(api/seo/kap/video_list y related_keywords) es la fuente por defecto (--proveedor descubrir): sin cuenta, sin llave, sin firma y sin captcha;
se piden la pagina y la lista como las pide el navegador, con pausa y pocas peticiones (el robots.txt de TikTok permite /discover). Si TikTok
contesta un reto o algo raro, se explica y se para: no se intenta esquivar nada. Limite: solo contesta para palabras que TikTok tiene en su
lista (frases de busqueda comunes, en cualquier idioma); una frase rara da 'no hay lista' y se sigue con las demas. --relacionadas N sigue las
palabras que TikTok da como relacionadas (todas existen), asi se encuentran mas palabras buenas a partir de una.
El proveedor de pago ScrapeCreators (--proveedor scrapecreators; su llave va en .env como SCRAPECREATORS_API_KEY, UNA linea, y NUNCA se
imprime; 1 credito por pagina) sigue disponible pero ya no hace falta.

Opciones:
  --max 20             videos NUEVOS por corrida
  --min-seg 4 --max-seg 120   duracion aceptada, en segundos (para b-roll sirven los videos largos: traen mas tomas)
  --drive "G:\\Mi unidad\\...\\OFERTA"   carpeta de Drive de la oferta EN ESTE PC; ahi se crea B-rolls
  --sin-drive          no copiar a Drive aunque broll.json traiga la carpeta
  --region CO          pais desde el que busca el proveedor (no filtra por pais: solo cambia desde donde mira)
  --orden relevancia|likes|fecha    --periodo todo|semana|mes|3meses|6meses
  --paginas 2          paginas de resultados por consulta o hashtag (16 videos cada una; 1 pedido cada una)
  --max-creditos 12    tope de pedidos de busqueda en esta corrida (a TikTok, o creditos si el proveedor es de pago)
  --proveedor descubrir|scrapecreators   de donde sale la busqueda por palabra (descubrir = gratis, es lo normal)
  --relacionadas 0     por cada consulta, cuantas palabras relacionadas de TikTok seguir tambien (1 pagina cada una)
  --codec mejor|h264   mejor = la mayor resolucion limpia (casi siempre HEVC 1080p, que Remotion lee bien);
                       h264 = solo H.264 (TikTok lo ofrece hasta 576x1024)
  --simular            muestra que bajaria, sin bajar ni escribir nada (la busqueda SI hace sus pedidos; con ScrapeCreators gasta creditos)

broll.json (opcional, en la carpeta del producto) guarda lo de siempre de esa oferta:
  {"drive": "G:\\\\Mi unidad\\\\MI NEGOCIO\\\\OFERTAS LATAM\\\\DINAMICAS", "consultas": ["..."], "hashtags": [], "cuentas": [],
   "max": 20, "min_seg": 4, "max_seg": 120, "region": "CO", "paginas": 2, "relacionadas": 0, "proveedor": "descubrir"}
Lo que pases por la linea de comandos manda sobre broll.json.

Los videos de TikTok son de sus creadores: este script guarda el autor y el enlace de cada uno para poder pedir
permiso o quitarlo. Solo se usan en anuncios los que se tenga derecho a usar.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import EXT_VIDEO, PUBLIC, RAIZ, configurar_consola, correr, ejecutable, salir  # noqa: E402

# El proveedor de busqueda. Solo para scripts/autoprueba_broll.py se acepta otra direccion, y unicamente si es de
# esta misma maquina: asi la llave nunca puede ir a otro lado por una variable de entorno.
BASE_PROVEEDOR = "https://api.scrapecreators.com"
_BASE_PRUEBA = os.environ.get("BROLL_PROVEEDOR_URL", "")
if _BASE_PRUEBA.startswith(("http://127.0.0.1:", "http://localhost:")):
    BASE_PROVEEDOR = _BASE_PRUEBA.rstrip("/")

# La lista publica de TikTok por palabra (Descubrir). Solo para scripts/autoprueba_broll.py se acepta otra direccion, y unicamente de esta misma maquina.
BASE_TIKTOK = "https://www.tiktok.com"
_BASE_TIKTOK_PRUEBA = os.environ.get("BROLL_TIKTOK_URL", "")
if _BASE_TIKTOK_PRUEBA.startswith(("http://127.0.0.1:", "http://localhost:")):
    BASE_TIKTOK = _BASE_TIKTOK_PRUEBA.rstrip("/")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
UA_PUBLICO = UA + " broll-editor/1.0 (uso interno de un editor de anuncios; pocas peticiones)"     # la lista publica se pide sin disfrazarse
HOSTS_TIKTOK = {"www.tiktok.com", "tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
RE_VIDEO = re.compile(r"^/@([\w.\-]+)/video/(\d{10,25})/?$")
CARPETA_DRIVE = "B-rolls"
CARPETA_LOCAL = "broll-tiktok"
COLUMNAS = ["id", "archivo", "estado", "motivo", "url", "autor", "descripcion", "duracion_s", "ancho", "alto",
            "codec", "vistas", "likes", "fuente", "consulta", "descargado_en", "en_drive",
            # las cuatro siguientes las llena el servidor (scripts/texto_quemado.py): cuanto del video no trae texto quemado
            "texto_pct", "limpio_seg", "tramos_limpios", "calidad",
            # de que trata el video segun TikTok (titulo y descripcion que su lista publica le pone): sirve para emparejarlo con un guion
            "tema"]
PERIODOS = {"todo": "all-time", "semana": "this-week", "mes": "this-month", "3meses": "last-3-months",
            "6meses": "last-6-months"}
ORDENES = {"relevancia": "relevance", "likes": "most-liked", "fecha": "date-posted"}
# Motivos de fallo que no dicen nada del video (conexion, bloqueo, yt-dlp viejo, error raro): no se anotan como descartado.
PASAJEROS = ("TikTok bloqueo", "yt-dlp ya no", "no se pudo bajar", "error inesperado", "TikTok no dio datos")


class ErrorFuente(Exception):
    """Una fuente (cuenta, consulta, proveedor) no pudo darnos videos. No tumba la corrida: se avisa y se sigue."""


class ErrorFuenteEspera(ErrorFuente):
    """La fuente pidio esperar (TikTok limita por IP): vale la pena volver a intentarla al final de la corrida."""


# ------------------------------------------------------------------ llave y .env
def leer_env(nombre: str) -> str:
    """Un valor de las variables de entorno o del archivo .env (NOMBRE=valor). Nunca lo imprime."""
    valor = os.environ.get(nombre, "").strip()
    env = RAIZ / ".env"
    if not valor and env.is_file():
        for linea in env.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea.startswith(nombre + "="):
                valor = linea.split("=", 1)[1].strip().strip('"').strip("'")
    return valor


def llave_proveedor() -> str:
    llave = leer_env("SCRAPECREATORS_API_KEY")
    if not llave:
        raise ErrorFuente(
            "falta la llave del proveedor de busqueda. Se registra en scrapecreators.com (100 busquedas gratis) y se pone en el "
            "archivo .env de esta carpeta, UNA linea: SCRAPECREATORS_API_KEY=tu_llave (sin espacios ni comillas). No la pegues en el chat. "
            "Mientras tanto se puede usar --cuenta o --enlaces.")
    if any(c.isspace() for c in llave):
        raise ErrorFuente("la llave SCRAPECREATORS_API_KEY de .env tiene espacios o saltos de linea: debe ir completa en una sola linea.")
    return llave


# ------------------------------------------------------------------ enlaces
def interpretar_enlace(texto: str) -> dict | None:
    """Un enlace de TikTok -> {'url','autor','id'} (id y autor pueden ser None en los enlaces cortos, que yt-dlp resuelve).
    None si no es de TikTok o no es un video (fotos, perfiles, otras paginas): asi yt-dlp nunca recibe una direccion ajena."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        u = urllib.parse.urlparse(texto)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in HOSTS_TIKTOK:
        return None
    m = RE_VIDEO.match(u.path)
    if m:
        autor, vid = m.group(1), m.group(2)
        return {"url": f"https://www.tiktok.com/@{autor}/video/{vid}", "autor": autor, "id": vid}
    host = (u.hostname or "").lower()
    if host in ("vm.tiktok.com", "vt.tiktok.com") and len(u.path.strip("/")) >= 5:
        return {"url": f"https://{host}{u.path}", "autor": None, "id": None}
    if host in ("www.tiktok.com", "tiktok.com") and re.match(r"^/t/[\w]{5,}/?$", u.path):
        return {"url": f"https://www.tiktok.com{u.path}", "autor": None, "id": None}
    return None


def candidato(**kw) -> dict:
    base = {"id": None, "url": "", "autor": None, "desc": "", "tema": "", "vistas": None, "likes": None, "duracion": None,
            "ancho": None, "alto": None, "fuente": "", "consulta": "", "es_anuncio": False, "es_foto": False}
    base.update(kw)
    return base


def leer_enlaces(origen) -> tuple[list[dict], list[str]]:
    """Lineas con un enlace por video -> (candidatos, lineas que no sirvieron). Acepta comentarios con #."""
    hallados, malos, vistos = [], [], set()
    for linea in origen:
        crudo = linea.split("#", 1)[0].strip()
        if not crudo:
            continue
        e = interpretar_enlace(crudo.split()[0])
        if not e:
            malos.append(crudo[:90])
            continue
        clave = e["id"] or e["url"]
        if clave in vistos:
            continue
        vistos.add(clave)
        hallados.append(candidato(id=e["id"], url=e["url"], autor=e["autor"], fuente="enlace"))
    return hallados, malos


# ------------------------------------------------------------------ fuente: cuenta (pagina de «insertar» de TikTok)
def parsear_embed(html: str, usuario: str) -> list[dict]:
    """La pagina tiktok.com/embed/@usuario trae dentro de su HTML los ultimos ~10 videos de la cuenta."""
    m = re.search(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ErrorFuente(f"@{usuario}: la pagina no trae la lista de videos (cuenta inexistente o privada, o TikTok cambio la pagina)")
    try:
        estado = json.loads(m.group(1))
    except ValueError:
        raise ErrorFuente(f"@{usuario}: la lista de videos no se pudo leer") from None
    lista = _buscar_clave(estado, "videoList")
    if not isinstance(lista, list):
        raise ErrorFuente(f"@{usuario}: la cuenta no tiene videos publicos")
    salida = []
    for v in lista:
        if not isinstance(v, dict) or not str(v.get("id", "")).isdigit() or v.get("privateItem"):
            continue
        autor = str(v.get("authorUniqueId") or usuario)
        if not re.fullmatch(r"[\w.\-]+", autor):
            autor = usuario
        salida.append(candidato(
            id=str(v["id"]), url=f"https://www.tiktok.com/@{autor}/video/{v['id']}", autor=autor,
            desc=str(v.get("desc") or ""), vistas=_entero(v.get("playCount")),
            ancho=_entero(v.get("width")), alto=_entero(v.get("height")), fuente=f"cuenta:@{usuario}"))
    return salida


def _buscar_clave(obj, clave):
    if isinstance(obj, dict):
        if clave in obj:
            return obj[clave]
        for v in obj.values():
            r = _buscar_clave(v, clave)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _buscar_clave(v, clave)
            if r is not None:
                return r
    return None


def _entero(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def fuente_cuenta(usuario: str) -> list[dict]:
    import requests

    usuario = usuario.strip().lstrip("@")
    if not re.fullmatch(r"[\w.\-]{1,40}", usuario):
        raise ErrorFuente(f"'{usuario}' no parece un usuario de TikTok")
    espera = 4
    for intento in range(3):
        try:
            r = requests.get(f"https://www.tiktok.com/embed/@{usuario}", headers={"User-Agent": UA, "Accept-Language": "es-CO,es;q=0.9"}, timeout=40)
        except requests.RequestException as e:
            raise ErrorFuente(f"@{usuario}: sin conexion con TikTok ({e.__class__.__name__})") from None
        if r.status_code == 200:
            return parsear_embed(r.text, usuario)
        if r.status_code in (429, 503) and intento < 2:
            time.sleep(espera)
            espera *= 2
            continue
        break
    if r.status_code in (429, 503):
        raise ErrorFuenteEspera(f"@{usuario}: TikTok pidio esperar ({r.text.strip()[:40]!r}); prueba en unos minutos")
    raise ErrorFuente(f"@{usuario}: TikTok contesto {r.status_code}")


# ------------------------------------------------------------------ fuente: proveedor de busqueda
def extraer_awemes(datos, fuente: str, consulta: str) -> list[dict]:
    """Saca los videos de una respuesta del proveedor. Cada endpoint los pone en otra lista (search_item_list, aweme_list,
    items) y a veces dentro de 'aweme_info'; se recorre todo el JSON y se toma cada objeto que parezca un video."""
    salida, vistos = [], set()

    def visitar(o):
        if isinstance(o, dict):
            if isinstance(o.get("aweme_info"), dict):
                visitar(o["aweme_info"])
                return
            vid = o.get("aweme_id") or (o.get("id") if isinstance(o.get("video"), dict) else None)
            if vid and str(vid).isdigit() and isinstance(o.get("video"), dict):
                if str(vid) not in vistos:
                    vistos.add(str(vid))
                    salida.append(_aweme_a_candidato(o, str(vid), fuente, consulta))
                return
            for v in o.values():
                if isinstance(v, (dict, list)):
                    visitar(v)
        elif isinstance(o, list):
            for v in o:
                visitar(v)

    visitar(datos)
    return salida


def _aweme_a_candidato(o: dict, vid: str, fuente: str, consulta: str) -> dict:
    video = o.get("video") or {}
    autor = (o.get("author") or {}).get("unique_id")
    if autor and not re.fullmatch(r"[\w.\-]+", str(autor)):
        autor = None
    est = o.get("statistics") or {}
    dur = video.get("duration")
    try:
        dur = float(dur)
        dur = dur / 1000 if dur >= 1000 else dur    # TikTok la da en milisegundos
    except (TypeError, ValueError):
        dur = None
    url = f"https://www.tiktok.com/@{autor}/video/{vid}" if autor else ""
    return candidato(
        id=vid, url=url, autor=autor, desc=str(o.get("desc") or ""), vistas=_entero(est.get("play_count")),
        likes=_entero(est.get("digg_count")), duracion=dur, ancho=_entero(video.get("width")), alto=_entero(video.get("height")),
        fuente=fuente, consulta=consulta, es_anuncio=bool(o.get("is_ad")),
        es_foto=bool(o.get("images")) and not (video.get("play_addr") or video.get("bit_rate")))


class Proveedor:
    """Cliente minimo de ScrapeCreators (solo busqueda). Cuenta los pedidos para respetar --max-creditos."""

    def __init__(self, llave: str, tope: int):
        self.llave, self.tope, self.gastados, self.quedan = llave, tope, 0, None

    def pedir(self, ruta: str, params: dict) -> dict:
        import requests

        if self.gastados >= self.tope:
            raise ErrorFuente(f"se llego al tope de {self.tope} pedidos al proveedor en esta corrida (--max-creditos)")
        params = {k: v for k, v in params.items() if v not in (None, "")}
        espera, ultimo = 3, ""
        for intento in range(3):
            try:
                r = requests.get(BASE_PROVEEDOR + ruta, params=params, headers={"x-api-key": self.llave, "Accept": "application/json"}, timeout=90)
            except requests.RequestException as e:
                ultimo = f"sin conexion con el proveedor ({e.__class__.__name__})"
                time.sleep(espera)
                espera *= 2
                continue
            if r.status_code in (401, 403):
                raise ErrorFuente("el proveedor rechazo la llave (SCRAPECREATORS_API_KEY de .env): revisala en su panel")
            if r.status_code == 402:
                raise ErrorFuente("al proveedor se le acabaron los creditos: hay que recargar en su panel")
            if r.status_code == 429 or r.status_code >= 500:
                ultimo = f"el proveedor contesto {r.status_code}"
                time.sleep(espera)
                espera *= 2
                continue
            if r.status_code >= 400:
                raise ErrorFuente(f"el proveedor contesto {r.status_code}: {r.text.strip()[:140]}")
            try:
                j = r.json()
            except ValueError:
                raise ErrorFuente("el proveedor contesto algo que no es JSON") from None
            if isinstance(j, dict) and j.get("success") is False:
                raise ErrorFuente("el proveedor dijo que no pudo: " + str(j.get("message") or j.get("error") or "sin explicacion")[:140])
            self.gastados += 1
            if isinstance(j, dict) and isinstance(j.get("credits_remaining"), (int, float)):
                self.quedan = j["credits_remaining"]
            return j
        raise ErrorFuente(ultimo or "el proveedor no respondio")

    def _paginar(self, ruta: str, params: dict, paginas: int, fuente: str, consulta: str) -> list[dict]:
        """Pide hasta `paginas` paginas siguiendo el cursor que devuelve el proveedor (TikTok puede repetir videos entre paginas)."""
        salida, cursor = [], None
        for _ in range(max(1, paginas)):
            j = self.pedir(ruta, {**params, "cursor": cursor})
            salida += extraer_awemes(j, fuente, consulta)
            siguiente = j.get("cursor") if isinstance(j, dict) else None
            if siguiente in (None, "", 0) or siguiente == cursor:
                break
            cursor = siguiente
        return salida

    def buscar(self, consulta: str, paginas: int, region: str, orden: str, periodo: str) -> list[dict]:
        return self._paginar("/v1/tiktok/search/keyword",
                             {"query": consulta, "region": region, "sort_by": ORDENES.get(orden), "date_posted": PERIODOS.get(periodo)},
                             paginas, f"consulta:{consulta}", consulta)

    def hashtag(self, etiqueta: str, paginas: int, region: str) -> list[dict]:
        etiqueta = etiqueta.lstrip("#")
        return self._paginar("/v1/tiktok/search/hashtag", {"hashtag": etiqueta, "region": region}, paginas, f"hashtag:{etiqueta}", f"#{etiqueta}")


def slug_de(texto: str) -> str:
    """'Dinamicas para mujeres!' -> 'dinamicas-para-mujeres': como TikTok escribe la palabra en sus paginas /discover/<palabra>."""
    t = sin_tildes(texto).replace("'", "").replace(chr(0x2019), "").replace("#", " ")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:80].strip("-")


class Descubrir:
    """Busqueda por palabra SIN llave y SIN pagar: la lista publica de videos que TikTok le muestra a cualquier visitante en sus paginas
    tiktok.com/discover/<palabra> (api/seo/kap/video_list) y sus palabras relacionadas (api/seo/kap/related_keywords). Sin cuenta, sin firma y
    sin captcha: primero se pide la pagina (TikTok da las cookies y el identificador de visitante) y luego la lista, como hace el navegador.
    Se identifica con su propio User-Agent, hace pocas peticiones con pausa y, si TikTok contesta un reto, vacio o algo raro, lo explica y
    PARA: no intenta esquivar nada. Cuenta cada peticion contra `tope` (--max-creditos).
    Limite honesto: solo contesta para palabras que TikTok tiene en su lista (frases de busqueda comunes); una frase rara da 'sin lista'."""

    nombre = "descubrir"
    PAUSA = (1.8, 3.2)

    def __init__(self, tope: int, base: str | None = None):
        self.tope, self.gastados, self.quedan = tope, 0, None
        self.base = base or BASE_TIKTOK
        self.s = None
        self.wid, self.region = "", "US"
        self._ultima = 0.0
        self.sin_lista: set[str] = set()       # palabras que TikTok no tiene: no se vuelven a pedir en esta corrida

    def _pausa(self) -> None:
        espera = random.uniform(*self.PAUSA) - (time.monotonic() - self._ultima)
        if espera > 0:
            time.sleep(espera)
        self._ultima = time.monotonic()

    def _contar(self) -> None:
        if self.gastados >= self.tope:
            raise ErrorFuente(f"se llego al tope de {self.tope} pedidos a TikTok en esta corrida (--max-creditos)")
        self.gastados += 1

    def _sesion(self):
        import requests

        if self.s is not None:
            return self.s
        self._contar()
        self._pausa()
        s = requests.Session()
        s.headers.update({"User-Agent": UA_PUBLICO, "Accept-Language": "es-419,es;q=0.9,en;q=0.8"})
        try:
            r = s.get(f"{self.base}/discover", headers={"Accept": "text/html,application/xhtml+xml"}, timeout=40)
        except requests.RequestException as e:
            raise ErrorFuente(f"sin conexion con TikTok ({e.__class__.__name__})") from None
        if r.status_code in (429, 503):
            raise ErrorFuenteEspera(f"TikTok pidio esperar (HTTP {r.status_code}); prueba en unos minutos")
        if r.status_code != 200:
            raise ErrorFuente(f"TikTok contesto {r.status_code} a la pagina de descubrir")
        m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', r.text, re.S)
        try:
            ctx = json.loads(m.group(1))["__DEFAULT_SCOPE__"]["webapp.app-context"]
            wid = str(ctx.get("wid") or "")
        except (AttributeError, KeyError, ValueError, TypeError):
            wid = ""
        if not wid.isdigit():
            raise ErrorFuente("la pagina de descubrir de TikTok no trae lo que se esperaba (cambio la pagina, o pide comprobar que no eres un robot): no se insiste")
        self.wid, self.region, self.s = wid, str(ctx.get("region") or "US")[:3], s
        return s

    def _pedir(self, ruta: str, slug: str, **extra) -> dict:
        import requests

        s = self._sesion()
        params = {"aid": "1988", "appId": "1233", "app_language": "es", "app_name": "tiktok_web", "browser_language": "es", "browser_name": "Mozilla",
                  "browser_online": "true", "browser_platform": "Win32", "channel": "tiktok_web", "cookie_enabled": "true", "data_collection_enabled": "false",
                  "device_id": self.wid, "device_platform": "web_pc", "focus_state": "true", "from_page": "expansion", "history_len": "2",
                  "is_fullscreen": "false", "is_page_visible": "true", "os": "windows", "priority_region": "", "referer": "", "region": self.region,
                  "root_referer": "", "screen_height": "768", "screen_width": "1366", "trafficType": "0", "tz_name": "America/Bogota",
                  "user_is_login": "false", "webcast_language": "es", "keyword": slug, "WebIdLastTime": str(int(time.time()) - 5), **extra}
        espera, ultimo = 4, ""
        for intento in range(3):
            self._contar()
            self._pausa()
            try:
                r = s.get(f"{self.base}/api/seo/kap/{ruta}/", params=params, timeout=40,
                          headers={"Referer": f"{self.base}/discover/{slug}", "Accept": "application/json"})
            except requests.RequestException as e:
                ultimo = f"sin conexion con TikTok ({e.__class__.__name__})"
                time.sleep(espera)
                espera *= 2
                continue
            if r.status_code in (429, 503):
                ultimo = f"TikTok pidio esperar (HTTP {r.status_code})"
                time.sleep(espera)
                espera *= 2
                continue
            if r.status_code != 200:
                raise ErrorFuente(f"TikTok contesto {r.status_code} a la lista publica")
            try:
                j = r.json()
            except ValueError:
                raise ErrorFuente("TikTok no devolvio la lista (respuesta vacia o que no es JSON: cambio la pagina o pide verificar); no se insiste") from None
            if not isinstance(j, dict):
                raise ErrorFuente("TikTok devolvio la lista en un formato que no se entiende")
            return j
        raise (ErrorFuenteEspera if "esperar" in ultimo else ErrorFuente)(ultimo or "TikTok no respondio")

    @staticmethod
    def _codigo(j: dict):
        c = j.get("status_code", j.get("statusCode"))
        return c if isinstance(c, int) else None

    def _candidato(self, it: dict, tdk: dict, consulta: str) -> dict:
        v = it.get("video") if isinstance(it.get("video"), dict) else {}
        a = it.get("author") if isinstance(it.get("author"), dict) else {}
        est = it.get("stats") if isinstance(it.get("stats"), dict) else {}
        autor = a.get("uniqueId")
        if autor and not re.fullmatch(r"[\w.\-]+", str(autor)):
            autor = None
        dur = v.get("duration")
        try:
            dur = float(dur)
            dur = dur / 1000 if dur >= 1000 else dur
        except (TypeError, ValueError):
            dur = None
        vid = str(it["id"])
        tema = " ".join(str(x) for x in (tdk.get("title"), tdk.get("description"), " ".join(str(k).strip() for k in (tdk.get("keywords") or []))) if x)
        return candidato(
            id=vid, url=f"https://www.tiktok.com/@{autor}/video/{vid}" if autor else "", autor=autor, desc=str(it.get("desc") or ""), tema=tema[:600],
            vistas=_entero(est.get("playCount")), likes=_entero(est.get("diggCount")), duracion=dur, ancho=_entero(v.get("width")), alto=_entero(v.get("height")),
            fuente=f"consulta:{consulta}", consulta=consulta, es_anuncio=False,
            es_foto=bool(it.get("imagePost")) or not (v.get("playAddr") or v.get("downloadAddr")))

    def buscar(self, consulta: str, paginas: int, region: str = "", orden: str = "", periodo: str = "") -> list[dict]:
        """Hasta `paginas` paginas (16 videos cada una) de la lista publica de esa palabra. [] si TikTok no tiene lista para ella.
        (region, orden y periodo no cambian nada aqui: la lista publica es una sola)."""
        slug = slug_de(consulta)
        if not slug:
            raise ErrorFuente(f"\"{consulta}\" no tiene letras ni numeros")
        if slug in self.sin_lista:
            return []
        salida, vistos, offset = [], set(), 0
        for pagina in range(max(1, paginas)):
            try:
                j = self._pedir("video_list", slug, count="16", offset=str(offset), pageType="7")
            except ErrorFuente as e:
                if not salida:
                    raise
                print(f"  AVISO: \"{consulta}\": {e} (se usan los {len(salida)} videos de las paginas anteriores)")
                break
            lista = j.get("videoList") if isinstance(j.get("videoList"), list) else []
            if not lista:
                codigo = self._codigo(j)
                if codigo not in (None, 0, 4):      # 4 = 'TikTok no tiene esa palabra'; cualquier otro codigo es algo que no conocemos
                    raise ErrorFuente(f"TikTok contesto el codigo {codigo} a la lista de \"{consulta}\": {str(j.get('status_msg') or '')[:80]}")
                if pagina == 0:
                    self.sin_lista.add(slug)
                break
            tdk = {str(i.get("itemId")): (i.get("customTdk") if isinstance(i.get("customTdk"), dict) else {})
                   for i in (j.get("seoBizItemInfoList") or []) if isinstance(i, dict)}
            for it in lista:
                vid = str(it.get("id") or "") if isinstance(it, dict) else ""
                if vid.isdigit() and vid not in vistos:
                    vistos.add(vid)
                    salida.append(self._candidato(it, tdk.get(vid) or {}, consulta))
            if not j.get("hasMore"):
                break
            offset += len(lista)
        return salida

    def hashtag(self, etiqueta: str, paginas: int, region: str = "") -> list[dict]:
        etiqueta = etiqueta.lstrip("#")
        g = self.buscar(etiqueta, paginas)
        for c in g:
            c["fuente"], c["consulta"] = f"hashtag:{etiqueta}", f"#{etiqueta}"
        return g

    def relacionadas(self, consulta: str) -> list[str]:
        """Las palabras que TikTok da como relacionadas (todas tienen su propia lista), en el orden en que las da. [] si no hay."""
        slug = slug_de(consulta)
        if not slug or slug in self.sin_lista:
            return []
        j = self._pedir("related_keywords", slug, count="60")
        lista = j.get("relatedKeywords") if isinstance(j.get("relatedKeywords"), list) else []
        palabras, vistas = [], {slug}
        for x in lista:
            p = str(x.get("formattedWord") or "").strip() if isinstance(x, dict) else ""
            k = slug_de(p)
            if p and k and k not in vistas:
                vistas.add(k)
                palabras.append(p)
        return palabras


def crear_proveedor(tope: int, nombre: str = "descubrir"):
    """El proveedor de la busqueda por palabra: 'descubrir' (gratis, lo normal) o 'scrapecreators' (de pago, con llave en .env)."""
    nombre = (nombre or "descubrir").strip().lower()
    if nombre == "scrapecreators":
        return Proveedor(llave_proveedor(), tope)
    if nombre == "descubrir":
        return Descubrir(tope)
    raise ErrorFuente(f"proveedor '{nombre}' desconocido: usa descubrir (gratis) o scrapecreators (de pago)")


_VACIAS = {"para", "como", "pero", "sobre", "entre", "desde", "hasta", "donde", "cuando", "tambien", "porque", "cada", "todo", "toda", "todos", "todas", "este", "esta", "esto",
           "estos", "estas", "tiene", "tienen", "hacer", "mejor", "mejores", "ideas", "idea"}


def palabras_utiles(texto: str) -> set[str]:
    """Las raices (5 letras) de las palabras con contenido de una frase: para saber si una palabra relacionada habla de lo mismo."""
    return {p[:5] for p in re.findall(r"[a-z0-9]+", sin_tildes(texto)) if len(p) >= 4 and p not in _VACIAS}


def relacionada_del_tema(semilla: str, candidata: str) -> bool:
    """Una palabra relacionada de TikTok sirve si comparte con la consulta al menos dos raices (una si la consulta es muy corta)."""
    a, b = palabras_utiles(semilla), palabras_utiles(candidata)
    return len(a & b) >= min(2, max(1, len(a)))


# ------------------------------------------------------------------ fuentes.csv (lo que ya se bajo o se descarto)
def leer_fuentes(carpeta: Path | None) -> dict[str, dict]:
    ruta = carpeta / "fuentes.csv" if carpeta else None
    if not ruta or not ruta.is_file():
        return {}
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        return {fila["id"]: fila for fila in csv.DictReader(f) if fila.get("id")}


COLUMNAS_TEXTO = ("motivo", "url", "autor", "descripcion", "fuente", "consulta", "tema")      # vienen de TikTok o de una persona: nunca se confia en ellas


def _celda_segura(valor) -> str:
    """Un texto que empieza con = + - @ (o tab / retorno) se abre como FORMULA en Google Sheets y Excel: se le antepone un apostrofo.
    La descripcion de un video la escribe cualquiera en TikTok, asi que sin esto un creador podria meter una formula en el csv del equipo."""
    t = "" if valor is None else str(valor)
    return "'" + t if t[:1] in ("=", "+", "-", "@", chr(9), chr(13)) else t


def escribir_fuentes(carpeta: Path, filas: dict[str, dict]) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "fuentes.csv"
    tmp = ruta.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
        w.writeheader()
        for fila in sorted(filas.values(), key=lambda x: (x.get("descargado_en") or "", x.get("id") or "")):
            w.writerow({c: (_celda_segura(fila.get(c, "")) if c in COLUMNAS_TEXTO else fila.get(c, "")) for c in COLUMNAS})
    os.replace(tmp, ruta)


def ids_tenidos(*carpetas: Path | None) -> set[str]:
    """Todo id que ya se bajo o se descarto, segun fuentes.csv y los archivos tt_<id>.* de cada carpeta."""
    tenidos: set[str] = set()
    for c in carpetas:
        if not c or not c.is_dir():
            continue
        tenidos |= set(leer_fuentes(c))
        for p in c.glob("tt_*"):
            m = re.match(r"tt_(\d{10,25})\.", p.name)
            if m:
                tenidos.add(m.group(1))
    return tenidos


# ------------------------------------------------------------------ cola de descarga
def sin_tildes(texto: str) -> str:
    """Minusculas y sin tildes: para comparar palabras sin que 'dinámica' y 'dinamica' sean distintas."""
    return "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower()) if unicodedata.category(c) != "Mn")


def habla_del_tema(c: dict, filtro: list[str]) -> bool:
    """Con un filtro de palabras puesto, un video solo pasa si su descripcion trae alguna (los enlaces dados a mano pasan siempre)."""
    if not filtro or c["fuente"] == "enlace":
        return True
    desc = sin_tildes(c["desc"] + " " + (c.get("tema") or ""))
    return any(sin_tildes(p) in desc for p in filtro)


MOTIVO_CUENTA_ANUNCIANTE = "de la cuenta del anunciante original"


def clave_cuenta(texto: object) -> str:
    """Minusculas, sin tildes y solo letras y numeros: para comparar el nombre de un anunciante con el usuario de una cuenta de TikTok (moleque_arteiro y MoleQue.Arteiro son la misma)."""
    return re.sub(r"[^a-z0-9]+", "", sin_tildes(str(texto or "")))


def es_de_la_cuenta(c: dict, excluir: list[str]) -> bool:
    """¿El video es de la cuenta del propio anunciante cuyo anuncio se esta modelando? Su material promocional NO se baja como b-roll. Se compara el usuario de TikTok del autor con el nombre del
    anunciante sin separadores ni tildes: es la misma cuenta si son iguales, si el usuario CONTIENE el nombre (moleque_arteiro_oficial) o si el usuario es casi todo el nombre (un recorte de al menos
    8 letras que cubre el 70 % o mas). Un nombre de menos de 5 letras no se usa (descartaria cuentas que no son) y una palabra suelta que solo esta DENTRO del nombre (fernanda en dicasdafernanda)
    no cuenta. Lo que la persona puso a mano (un enlace o una cuenta de la lista) pasa siempre: fue su decision."""
    fuente = str(c.get("fuente") or "")
    autor = clave_cuenta(c.get("autor"))
    if not excluir or not autor or fuente == "enlace" or fuente.startswith("cuenta:"):
        return False
    for e in excluir:
        k = clave_cuenta(e)
        if len(k) < 5:
            continue
        if autor == k or k in autor or (autor in k and len(autor) >= 8 and len(autor) * 10 >= len(k) * 7):
            return True
    return False


def armar_cola(grupos: list[list[dict]], tenidos: set[str], min_seg: float, max_seg: float, max_por_grupo: int,
               filtro: list[str] | None = None, excluir_autores: list[str] | None = None) -> tuple[list[dict], dict]:
    """Une las listas de cada fuente en una sola cola: sin repetidos, sin lo ya tenido, sin lo que claramente no sirve, y
    intercalada (uno de cada fuente por turno) para que una consulta o una cuenta no se coma todo el cupo.
    `filtro` = palabras del tema: una cuenta publica de todo (peinados, departamentos...) y asi solo pasan sus videos que hablan de lo que se busca.
    `excluir_autores` = nombres de anunciantes cuya propia cuenta de TikTok no se usa como b-roll (es_de_la_cuenta)."""
    descartes: dict[str, int] = {}
    filtro = [p for p in (filtro or []) if str(p).strip()]
    excluir = [p for p in (excluir_autores or []) if str(p).strip()]

    def descartar(motivo: str):
        descartes[motivo] = descartes.get(motivo, 0) + 1

    vistos: set[str] = set()
    listas = []
    for grupo in grupos:
        utiles = []
        for c in grupo:
            clave = c["id"] or c["url"]
            if not c["url"]:
                descartar("sin enlace")
            elif clave in vistos:
                descartar("repetido")
            elif c["id"] and c["id"] in tenidos:
                descartar("ya lo teniamos")
            elif es_de_la_cuenta(c, excluir):
                descartar(MOTIVO_CUENTA_ANUNCIANTE)
            elif not habla_del_tema(c, filtro):
                descartar("no habla del tema")
            elif c["es_anuncio"]:
                descartar("es un anuncio")
            elif c["es_foto"]:
                descartar("es una foto, no un video")
            elif c["duracion"] is not None and not (min_seg <= c["duracion"] <= max_seg):
                descartar("duracion fuera de rango")
            elif c["ancho"] and c["alto"] and c["alto"] < c["ancho"] * 1.2:
                descartar("no es vertical")
            else:
                vistos.add(clave)
                utiles.append(c)
        # Dentro de una fuente se respeta el orden en que llego (relevancia del proveedor, lo mas reciente de una cuenta,
        # o el orden en que Claude o el equipo pusieron los enlaces).
        listas.append(utiles[:max_por_grupo])

    cola = []
    for turno in range(max((len(l) for l in listas), default=0)):
        for l in listas:
            if turno < len(l):
                cola.append(l[turno])
    return cola, descartes


# ------------------------------------------------------------------ descarga y comprobacion
def sondear(ruta: Path) -> dict | None:
    """Datos del archivo segun ffprobe, o None si no se puede abrir (o si este PC no tiene ffprobe)."""
    if not shutil.which("ffprobe"):
        return None
    r = correr([ejecutable("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)])
    if r.returncode != 0:
        return {"error": (r.stderr or "ffprobe fallo").strip()[:120]}
    try:
        info = json.loads(r.stdout)
    except ValueError:
        return {"error": "ffprobe no devolvio JSON"}
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        return {"error": "el archivo no tiene pista de video"}
    return {"codec": v.get("codec_name", ""), "ancho": int(v.get("width") or 0), "alto": int(v.get("height") or 0),
            "duracion": float(info.get("format", {}).get("duration") or v.get("duration") or 0),
            "audio": any(s.get("codec_type") == "audio" for s in info.get("streams", []))}


def _selector_formato(codec: str):
    """Elige el formato: nunca el de marca de agua, el de mayor resolucion (con codec=h264, solo H.264)."""
    def elegir(ctx):
        formatos = [f for f in ctx.get("formats", [])
                    if f.get("vcodec") not in (None, "none")
                    and f.get("format_id") != "download"
                    and "watermark" not in str(f.get("format_note", "")).lower()]
        if codec == "h264":
            formatos = [f for f in formatos if str(f.get("vcodec", "")).lower().startswith(("h264", "avc"))]
        if not formatos:
            return
        yield max(formatos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    return elegir


class _SilencioYtdlp:
    """yt-dlp escribe sus errores en pantalla aunque se pida silencio; aqui los explicamos nosotros."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class DescargadorYtdlp:
    """Baja videos de TikTok con yt-dlp, uno por uno, reutilizando la misma sesion (TikTok pide resolver un reto la primera vez)."""

    def __init__(self, carpeta: Path, codec: str):
        try:
            import yt_dlp
        except ImportError:
            salir("Falta yt-dlp: corre Instalar-Editor.cmd o  python -m pip install -r requirements.txt")
        self.yt_dlp = yt_dlp
        opciones = {
            "outtmpl": str(carpeta / "%(id)s.%(ext)s"), "format": _selector_formato(codec), "noplaylist": True,
            "quiet": True, "no_warnings": True, "noprogress": True, "logger": _SilencioYtdlp(),
            "socket_timeout": 30, "retries": 3, "fragment_retries": 3, "overwrites": True, "restrictfilenames": True,
        }
        try:
            import curl_cffi  # noqa: F401
            from yt_dlp.networking.impersonate import ImpersonateTarget

            # TikTok contesta 403 al cliente HTTP comun de yt-dlp; con la huella de un navegador real contesta bien.
            opciones["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception:
            pass
        self.carpeta = carpeta
        self.ydl = yt_dlp.YoutubeDL(opciones)

    def cerrar(self) -> None:
        self.ydl.close()

    def bajar(self, url: str, min_seg: float, max_seg: float) -> dict:
        """Baja UN video. Devuelve {'ok': True, 'ruta', 'id', 'autor', ...} o {'ok': False, 'motivo'}. Ante un 403/429 reintenta una vez."""
        res = self._bajar_una_vez(url, min_seg, max_seg)
        if not res["ok"] and res["motivo"].startswith("TikTok bloqueo"):
            time.sleep(random.uniform(8, 12))
            res = self._bajar_una_vez(url, min_seg, max_seg)
        return res

    def _bajar_una_vez(self, url: str, min_seg: float, max_seg: float) -> dict:
        try:
            crudo = self.ydl.extract_info(url, download=False, process=False)
            if not crudo:
                return {"ok": False, "motivo": "TikTok no dio datos del video"}
            if crudo.get("_type") not in (None, "video"):
                return {"ok": False, "motivo": "no es un video (foto o lista)"}
            dur = crudo.get("duration")
            if dur is not None and not (min_seg <= float(dur) <= max_seg):
                return {"ok": False, "id": str(crudo.get("id") or ""), "motivo": f"dura {float(dur):.0f} s (se aceptan {min_seg:g} a {max_seg:g})", "saltar": True}
            info = self.ydl.process_ie_result(crudo, download=True)
        except self.yt_dlp.utils.YoutubeDLError as e:    # DownloadError, ExtractorError (formato no disponible) y demas
            return {"ok": False, "motivo": _explicar_error_ytdlp(str(e))}
        except Exception as e:  # noqa: BLE001 - una falla de un video no debe tumbar la corrida
            return {"ok": False, "motivo": f"error inesperado: {e.__class__.__name__}: {str(e)[:120]}"}
        descargas = (info or {}).get("requested_downloads") or []
        ruta = Path(descargas[0]["filepath"]) if descargas and descargas[0].get("filepath") else None
        if not ruta or not ruta.is_file():
            hallados = sorted(self.carpeta.glob(f"{(info or {}).get('id', '')}.*"))
            ruta = hallados[0] if hallados else None
        if not ruta:
            return {"ok": False, "motivo": "yt-dlp no dejo el archivo"}
        return {"ok": True, "ruta": ruta, "id": str(info.get("id") or ""), "autor": info.get("uploader") or info.get("channel") or None,
                "duracion": info.get("duration"), "ancho": info.get("width"), "alto": info.get("height"),
                "vistas": info.get("view_count"), "likes": info.get("like_count"), "desc": (info.get("description") or info.get("title") or ""),
                "codec": info.get("vcodec")}


def _explicar_error_ytdlp(txt: str) -> str:
    t = txt.lower()
    if "private" in t:
        return "el video es privado"
    if "unavailable" in t or "removed" in t or "not found" in t or "404" in t:
        return "el video ya no esta disponible"
    if "format" in t and "not available" in t:
        return "TikTok solo ofrece la version con marca de agua (o ninguna en ese codec)"
    if "ip address is blocked" in t or "403" in t or "429" in t:
        return "TikTok bloqueo la descarga desde esta conexion (403/429): espera un rato y repite"
    if "unable to extract" in t or "unsupported url" in t:
        return "yt-dlp ya no entiende la pagina de TikTok: actualizalo con  python -m pip install -U yt-dlp"
    return "no se pudo bajar: " + re.sub(r"\s+", " ", txt)[:140]


def comprobar_archivo(ruta: Path, min_seg: float, max_seg: float, permitir_horizontal: bool) -> tuple[bool, str, dict]:
    """El archivo bajado de verdad sirve? -> (ok, motivo, datos tecnicos)."""
    if ruta.stat().st_size < 30_000:
        return False, "el archivo pesa menos de 30 KB", {}
    datos = sondear(ruta)
    if datos is None:
        return True, "", {}    # sin ffprobe en este PC: se confia en lo que dijo yt-dlp
    if "error" in datos:
        return False, f"no se abre: {datos['error']}", {}
    if not (min_seg - 0.5 <= datos["duracion"] <= max_seg + 0.5):
        return False, f"dura {datos['duracion']:.0f} s", datos
    if not permitir_horizontal and datos["alto"] < datos["ancho"]:
        return False, f"es horizontal ({datos['ancho']}x{datos['alto']})", datos
    return True, "", datos


# ------------------------------------------------------------------ carpetas de destino
def carpeta_drive(ruta: str | None) -> Path | None:
    """<carpeta de la oferta>/B-rolls, creada si falta. None (con aviso) si la carpeta de Drive no esta en este PC."""
    if not ruta:
        return None
    if re.match(r"^https?://", ruta.strip()):
        print("  AVISO: --drive / broll.json 'drive' debe ser la RUTA de la carpeta en este PC (por ejemplo G:\\Mi unidad\\...), no el enlace de Drive.")
        return None
    base = Path(ruta.strip().strip('"'))
    if not base.is_dir():
        print(f"  AVISO: no encuentro la carpeta de Drive {base}. Los videos quedan solo en el editor; la proxima corrida los copia.")
        return None
    destino = base / CARPETA_DRIVE
    try:
        destino.mkdir(exist_ok=True)
        leeme = destino / "LEEME.txt"
        if not leeme.exists():
            leeme.write_text(
                "B-rolls de esta oferta, bajados de TikTok con scripts/broll_tiktok.py del editor de anuncios.\n"
                "fuentes.csv trae el enlace, el autor, las vistas y la busqueda de cada video.\n"
                "Son videos de sus creadores: solo se usan en anuncios los que se tenga derecho a usar (permiso o licencia).\n"
                "Si un video no sirve, borralo de aqui: no vuelve a bajarse porque queda anotado en fuentes.csv.\n", encoding="utf-8")
    except OSError as e:
        print(f"  AVISO: no pude escribir en {destino}: {e}")
        return None
    return destino


def copiar_a(destino: Path, origen: Path) -> bool:
    try:
        destino.mkdir(parents=True, exist_ok=True)
        final = destino / origen.name
        if not (final.is_file() and final.stat().st_size == origen.stat().st_size):
            shutil.copy2(origen, final)
        return final.stat().st_size == origen.stat().st_size
    except OSError as e:
        print(f"  AVISO: no pude copiar {origen.name} a {destino}: {e}")
        return False


def leer_config(carpeta_producto: Path | None) -> dict:
    if carpeta_producto and (carpeta_producto / "broll.json").is_file():
        try:
            c = json.loads((carpeta_producto / "broll.json").read_text(encoding="utf-8"))
            return c if isinstance(c, dict) else {}
        except ValueError:
            salir(f"{carpeta_producto / 'broll.json'} no es un JSON valido")
    return {}


def _lista(x) -> list[str]:
    return [str(i).strip() for i in (x if isinstance(x, list) else []) if str(i).strip()]


# ------------------------------------------------------------------ comando: traer
def traer(a, fabrica=DescargadorYtdlp, resumen: dict | None = None) -> int:
    """Busca y baja. `resumen` (opcional) se llena con lo que paso, para quien llame a esta funcion desde otro programa (scripts/broll_vps.py):
    bajados, fallos, problemas (fuentes que no dieron videos) y cortada_por_red (tres fallos seguidos de conexion: TikTok bloqueo o no hay red)."""
    resumen = resumen if resumen is not None else {}
    resumen.update(bajados=0, fallos=0, problemas=[], cortada_por_red=False, candidatos=0, creditos=0, creditos_quedan=None, busquedas={}, de_la_cuenta_del_anunciante=0)
    prod = PUBLIC / "productos" / a.producto
    local = prod / CARPETA_LOCAL if prod.is_dir() else None
    cfg = leer_config(prod if prod.is_dir() else None)

    # Lo que se pasa por la linea de comandos manda: si pidieron fuentes a mano, no se mezclan las de broll.json.
    enlaces_cli, archivo_enlaces = list(a.enlace or []), a.enlaces
    fuentes_a_mano = bool(a.consulta or a.hashtag or a.cuenta or enlaces_cli or archivo_enlaces)
    if fuentes_a_mano:
        consultas, hashtags, cuentas = list(a.consulta or []), list(a.hashtag or []), list(a.cuenta or [])
    else:
        consultas, hashtags, cuentas = _lista(cfg.get("consultas")), _lista(cfg.get("hashtags")), _lista(cfg.get("cuentas"))
    filtro = list(a.filtro_desc or []) or ([] if fuentes_a_mano else _lista(cfg.get("filtro_desc")))
    excluir = [x for x in list(a.excluir_autor or []) + _lista(cfg.get("excluir_autores")) if str(x).strip()]      # es una regla de seguridad (derechos): vale aunque las fuentes se den a mano
    maximo = a.max if a.max is not None else int(cfg.get("max") or 20)
    min_seg = a.min_seg if a.min_seg is not None else float(cfg.get("min_seg") or 4)
    max_seg = a.max_seg if a.max_seg is not None else float(cfg.get("max_seg") or 120)
    region = a.region or cfg.get("region") or ""
    paginas = a.paginas if a.paginas is not None else int(cfg.get("paginas") or 2)
    proveedor_nombre = a.proveedor or str(cfg.get("proveedor") or "descubrir")
    n_relacionadas = a.relacionadas if a.relacionadas is not None else int(cfg.get("relacionadas") or 0)
    ruta_drive = None if a.sin_drive else (a.drive or cfg.get("drive") or "")

    if not (consultas or hashtags or cuentas or enlaces_cli or archivo_enlaces):
        salir("No hay de donde sacar videos: pasa --consulta, --hashtag, --cuenta, --enlace o --enlaces, o llena public/productos/"
              f"{a.producto}/broll.json (consultas, hashtags, cuentas).")
    if local is None and not ruta_drive:
        salir(f"No existe public/productos/{a.producto}. Crea el producto (python scripts/nuevo_producto.py {a.producto}) o indica --drive para dejar los videos solo en Drive.")

    destino_drive = None if a.simular else carpeta_drive(ruta_drive)
    if a.simular and ruta_drive:
        d = Path(str(ruta_drive).strip('"'))
        destino_drive = d / CARPETA_DRIVE if d.is_dir() else None
    destinos = [d for d in (local, destino_drive) if d is not None]
    tenidos = ids_tenidos(*destinos)
    filas = {**leer_fuentes(destino_drive), **leer_fuentes(local)}
    if not a.simular and _reparar_copias_a_drive(local, destino_drive, filas):
        for d in destinos:
            escribir_fuentes(d, filas)
    print(f"Producto: {a.producto} | {len(tenidos)} video(s) ya anotados | cupo de esta corrida: {maximo} nuevos")
    print(f"Destino: {local if local else '(sin carpeta del producto)'}" + (f"  +  {destino_drive}" if destino_drive else "  (sin copia a Drive)"))

    # ---- 1. juntar candidatos de cada fuente
    grupos: list[list[dict]] = []
    problemas: list[str] = []
    if enlaces_cli or archivo_enlaces:
        lineas = list(enlaces_cli)
        if archivo_enlaces:
            p = Path(archivo_enlaces)
            if not p.is_file():
                salir(f"No existe el archivo de enlaces {p}")
            lineas += p.read_text(encoding="utf-8-sig").splitlines()
        hallados, malos = leer_enlaces(lineas)
        grupos.append(hallados)
        print(f"Enlaces: {len(hallados)} de video" + (f" ({len(malos)} lineas que no son un video de TikTok se ignoraron)" if malos else ""))
    esperar_y_reintentar: list[str] = []
    for u in cuentas:
        try:
            g = fuente_cuenta(u)
            grupos.append(g)
            print(f"Cuenta @{u.lstrip('@')}: {len(g)} video(s)")
        except ErrorFuenteEspera as e:
            esperar_y_reintentar.append(u)
            print(f"  AVISO: {e} (se reintenta al final)")
        except ErrorFuente as e:
            problemas.append(str(e))
            print(f"  AVISO: {e}")
        time.sleep(random.uniform(3.0, 6.0))      # la pagina de «insertar» de TikTok limita por IP: con 1-2 s entre cuentas, la tercera ya contesta «ratelimit»
    if esperar_y_reintentar:
        # Lo que TikTok mando a esperar se intenta una vez mas cuando ya paso un rato (el limite es por minutos, no por dias)
        print(f"Esperando un poco para reintentar {len(esperar_y_reintentar)} cuenta(s) que TikTok mando a esperar...")
        time.sleep(random.uniform(50.0, 70.0))
        for u in esperar_y_reintentar:
            try:
                g = fuente_cuenta(u)
                grupos.append(g)
                print(f"Cuenta @{u.lstrip('@')} (segundo intento): {len(g)} video(s)")
            except ErrorFuente as e:
                problemas.append(str(e))
                print(f"  AVISO: {e}")
            time.sleep(random.uniform(4.0, 8.0))
    prov = None
    if consultas or hashtags:
        try:
            prov = crear_proveedor(a.max_creditos, proveedor_nombre)
        except ErrorFuente as e:
            problemas.append(str(e))
            print(f"  AVISO: {e}")
    con_lista = fallo_consulta = 0
    busquedas: dict[str, int] = {}          # cuantos videos mostro TikTok por cada frase (0 = no tiene lista publica para esa frase)
    if prov:
        for q in consultas:
            try:
                g = prov.buscar(q, paginas, region, a.orden, a.periodo)
                grupos.append(g)
                busquedas[q] = len(g)
                con_lista += 1 if g else 0
                print(f"Busqueda \"{q}\": {len(g)} video(s)" + (" (TikTok no tiene lista publica para esa palabra)" if not g and getattr(prov, "nombre", "") == "descubrir" else ""))
            except ErrorFuente as e:
                problemas.append(f"\"{q}\": {e}")
                print(f"  AVISO: \"{q}\": {e}")
                fallo_consulta += 1
                if "tope" in str(e):
                    break
        for h in hashtags:
            try:
                g = prov.hashtag(h, paginas, region)
                grupos.append(g)
                print(f"Hashtag #{h.lstrip('#')}: {len(g)} video(s)")
            except ErrorFuente as e:
                problemas.append(f"#{h}: {e}")
                print(f"  AVISO: #{h}: {e}")
                if "tope" in str(e):
                    break
        if n_relacionadas > 0 and hasattr(prov, "relacionadas"):
            # Las palabras que TikTok da como relacionadas SI tienen lista: se siguen las que hablan de lo mismo (1 pagina cada una).
            consultadas = {slug_de(x) for x in list(consultas) + list(hashtags)}
            sin_pedidos = False         # llegar al tope de pedidos no es un fallo: es el limite que se puso; solo se dejan de seguir palabras
            for q in list(consultas):
                if sin_pedidos:
                    break
                try:
                    elegidas = [p for p in prov.relacionadas(q) if slug_de(p) not in consultadas and relacionada_del_tema(q, p)][:n_relacionadas]
                except ErrorFuente as e:
                    if "tope" in str(e):
                        sin_pedidos = True
                        break
                    problemas.append(f"palabras relacionadas de \"{q}\": {e}")
                    print(f"  AVISO: palabras relacionadas de \"{q}\": {e}")
                    continue
                for p in elegidas:
                    consultadas.add(slug_de(p))
                    try:
                        g = prov.buscar(p, 1, region, a.orden, a.periodo)
                        grupos.append(g)
                        busquedas[p] = len(g)
                        print(f"Relacionada \"{p}\" (de \"{q}\"): {len(g)} video(s)" + (" (TikTok no tiene lista publica para esa palabra)" if not g else ""))
                    except ErrorFuente as e:
                        if "tope" in str(e):
                            sin_pedidos = True
                        else:
                            problemas.append(f"\"{p}\": {e}")
                            print(f"  AVISO: \"{p}\": {e}")
                        break
            if sin_pedidos:
                print("  (se llego al tope de pedidos de esta corrida: no se siguen mas palabras relacionadas)")
        if getattr(prov, "nombre", "") == "descubrir":
            if consultas and not con_lista and not fallo_consulta:
                problemas.append("TikTok no tiene lista publica para ninguna de las palabras buscadas (prueba con frases mas comunes o mas cortas)")
                print(f"  AVISO: {problemas[-1]}")
            print(f"Lista publica de TikTok: {prov.gastados} pedido(s) en esta corrida (gratis)")
        else:
            print(f"Proveedor: {prov.gastados} pedido(s) en esta corrida" + (f"; le quedan {prov.quedan:g} credito(s)" if prov.quedan is not None else ""))
        resumen.update(creditos=prov.gastados, creditos_quedan=prov.quedan)

    por_grupo = max(3, -(-maximo // max(1, len(grupos))) + 2)
    cola, descartes = armar_cola(grupos, tenidos, min_seg, max_seg, por_grupo, filtro, excluir)
    total = sum(len(g) for g in grupos)
    print(f"Candidatos: {total} | en la cola: {len(cola)}" + (" | descartados: " + ", ".join(f"{n} {m}" for m, n in descartes.items()) if descartes else ""))
    resumen.update(problemas=list(problemas), candidatos=total, busquedas=dict(busquedas), de_la_cuenta_del_anunciante=int(descartes.get(MOTIVO_CUENTA_ANUNCIANTE, 0)))
    if not cola:
        print("Nada nuevo que bajar.")
        return 0 if not problemas else 2

    if a.simular:
        print(f"\nSIMULACION: bajaria hasta {maximo} de estos (en este orden); no se baja ni se escribe nada.")
        for i, c in enumerate(cola[:maximo + 5], 1):
            cifras = " ".join(x for x in (f"{c['likes']:,} likes" if c["likes"] is not None else "", f"{c['vistas']:,} vistas" if c["vistas"] is not None else "") if x)
            dur = f"{c['duracion']:.0f}s" if c["duracion"] else "?s"
            print(f"  {i:2d}. @{c['autor'] or '?'} {c['id'] or '(enlace corto)'}  {dur}  {cifras}  [{c['fuente']}]  {c['desc'][:60]!r}")
        return 0

    # ---- 2. bajar
    bajados, fallos, seguidos_pasajeros = 0, 0, 0
    cortada = False
    tmp = Path(tempfile.mkdtemp(prefix="broll_"))
    descargador = None
    intentos_max = maximo * 3
    intentos = 0
    try:
        descargador = fabrica(tmp, a.codec)
        for c in cola:
            if bajados >= maximo or intentos >= intentos_max:
                break
            intentos += 1
            etiqueta = f"@{c['autor'] or '?'} {c['id'] or c['url'][-14:]}"
            res = descargador.bajar(c["url"], min_seg, max_seg)
            vid = res.get("id") or c["id"]
            if res.get("saltar"):
                # La duracion depende de --min-seg y --max-seg, no del video: no se anota, para poder bajarlo con otros limites.
                print(f"  - {etiqueta}: salta ({res['motivo']})")
                continue
            if not res["ok"]:
                fallos += 1
                print(f"  x {etiqueta}: {res['motivo']}")
                if res["motivo"].startswith(PASAJEROS):
                    # Falla de la conexion o de yt-dlp, no del video: no se anota, para volver a intentarlo otro dia.
                    seguidos_pasajeros += 1
                    if seguidos_pasajeros >= 3:
                        print("  Tres fallos seguidos por la conexion: se detiene la corrida (no se anoto nada de lo que fallo).")
                        cortada = True
                        break
                elif vid:
                    seguidos_pasajeros = 0
                    filas[vid] = _fila(vid, "", "descartado", res["motivo"], c, res, None, "")
                time.sleep(random.uniform(2.0, 4.0))
                continue
            seguidos_pasajeros = 0
            if not vid or not str(vid).isdigit():
                fallos += 1
                print(f"  x {etiqueta}: no se pudo leer el id del video")
                res["ruta"].unlink(missing_ok=True)
                continue
            if vid in tenidos:    # un enlace corto que resulto ser un video que ya teniamos
                print(f"  - {etiqueta}: ya lo teniamos")
                res["ruta"].unlink(missing_ok=True)
                continue
            ok, motivo, tec = comprobar_archivo(res["ruta"], min_seg, max_seg, a.permitir_horizontal)
            if not ok:
                fallos += 1
                print(f"  x {etiqueta}: {motivo}")
                filas[vid] = _fila(vid, "", "descartado", motivo, c, res, tec, "")
                res["ruta"].unlink(missing_ok=True)
                continue
            nombre = f"tt_{vid}{res['ruta'].suffix.lower() or '.mp4'}"
            final = None
            if local is not None:
                local.mkdir(parents=True, exist_ok=True)
                final = local / nombre
                shutil.move(str(res["ruta"]), final)
                en_drive = bool(destino_drive and copiar_a(destino_drive, final))
            else:
                final = destino_drive / nombre
                shutil.move(str(res["ruta"]), final)
                en_drive = True
            filas[vid] = _fila(vid, nombre, "bajado", "", c, res, tec, "si" if en_drive else "no")
            tenidos.add(vid)
            bajados += 1
            mb = final.stat().st_size / 1e6
            dim = f"{tec.get('ancho') or res.get('ancho')}x{tec.get('alto') or res.get('alto')}"
            print(f"  + {etiqueta}: {float(tec.get('duracion') or res.get('duracion') or 0):.0f} s, {dim}, {mb:.1f} MB -> {nombre}" + ("" if en_drive or not destino_drive else "  (Drive: pendiente)"))
            time.sleep(random.uniform(1.5, 3.5))
    finally:
        if descargador is not None:
            descargador.cerrar()
        shutil.rmtree(tmp, ignore_errors=True)
        for d in destinos:
            escribir_fuentes(d, filas)
        resumen.update(bajados=bajados, fallos=fallos, cortada_por_red=cortada)

    print(f"\nListo: {bajados} video(s) nuevos, {fallos} que no sirvieron" + (f", {len(problemas)} fuente(s) con problemas" if problemas else "") + ".")
    if bajados and local is not None:
        print(f"Siguiente paso: python scripts/indexar_broll.py {a.producto}   (saca las hojas de contacto; luego Claude las mira una vez)")
    return 0 if bajados or not (fallos or problemas) else 2


def _codec_corto(vcodec) -> str:
    """yt-dlp dice h264, h265, avc1.64001F...: se anota como lo diria ffprobe (h264 / hevc)."""
    v = str(vcodec or "").lower()
    if v.startswith(("h265", "hevc", "hvc1", "hev1")):
        return "hevc"
    if v.startswith(("h264", "avc")):
        return "h264"
    return v if v not in ("none", "") else ""


def _fila(vid: str, archivo: str, estado: str, motivo: str, c: dict, res: dict, tec: dict | None, en_drive: str) -> dict:
    tec = tec or {}
    return {
        "id": vid, "archivo": archivo, "estado": estado, "motivo": motivo,
        "url": c["url"] if c["id"] == vid else (f"https://www.tiktok.com/@{res.get('autor') or c['autor']}/video/{vid}" if (res.get("autor") or c["autor"]) else c["url"]),
        "autor": res.get("autor") or c["autor"] or "", "descripcion": re.sub(r"\s+", " ", (res.get("desc") or c["desc"] or "")).strip()[:200],
        "duracion_s": round(float(tec.get("duracion") or res.get("duracion") or c["duracion"] or 0), 1) or "",
        "ancho": tec.get("ancho") or res.get("ancho") or c["ancho"] or "", "alto": tec.get("alto") or res.get("alto") or c["alto"] or "",
        "codec": tec.get("codec") or _codec_corto(res.get("codec")), "vistas": res.get("vistas") if res.get("vistas") is not None else (c["vistas"] if c["vistas"] is not None else ""),
        "likes": res.get("likes") if res.get("likes") is not None else (c["likes"] if c["likes"] is not None else ""),
        "fuente": c["fuente"], "consulta": c["consulta"], "descargado_en": datetime.now().isoformat(timespec="seconds"), "en_drive": en_drive,
        "tema": re.sub(r"\s+", " ", c.get("tema") or "").strip()[:200],
    }


def _reparar_copias_a_drive(local: Path | None, drive: Path | None, filas: dict[str, dict]) -> int:
    """Lo que quedo sin copiar a Drive en una corrida anterior (Drive no estaba montado) se copia ahora. Devuelve cuantos."""
    if not local or not drive:
        return 0
    n = 0
    for f in filas.values():
        if f.get("estado") == "bajado" and f.get("en_drive") != "si" and f.get("archivo"):
            origen = local / f["archivo"]
            if origen.is_file() and copiar_a(drive, origen):
                f["en_drive"] = "si"
                n += 1
    if n:
        print(f"Copiados a Drive {n} video(s) que habian quedado pendientes.")
    return n


# ------------------------------------------------------------------ comando: recoger
def recoger(a) -> int:
    """Trae al editor lo que el servidor (o otra persona) dejo en <carpeta de la oferta en Drive>/B-rolls: copia los videos que aqui no estan
    a public/productos/<producto>/broll-tiktok/ y suma sus filas a fuentes.csv. Lo que ya se tuvo aqui (aunque el equipo lo haya borrado) no vuelve."""
    prod = PUBLIC / "productos" / a.producto
    if not prod.is_dir():
        salir(f"No existe public/productos/{a.producto}. Crea el producto (python scripts/nuevo_producto.py {a.producto}).")
    ruta = a.drive or leer_config(prod).get("drive") or ""
    if not ruta:
        salir("No se donde esta la carpeta de Drive de la oferta: pasa --drive \"<ruta en este PC>\" o ponla en broll.json (\"drive\").")
    if re.match(r"^https?://", ruta.strip()):
        salir("--drive / broll.json 'drive' debe ser la RUTA de la carpeta en este PC (por ejemplo G:\\Mi unidad\\...), no el enlace de Drive.")
    origen = Path(ruta.strip().strip('"')) / CARPETA_DRIVE
    if not origen.is_dir():
        salir(f"No encuentro {origen}. Revisa que Drive para escritorio este abierto y que la oferta ya tenga su carpeta {CARPETA_DRIVE}.")
    local = prod / CARPETA_LOCAL
    tenidos = ids_tenidos(local)
    filas = leer_fuentes(local)
    delas = leer_fuentes(origen)
    nuevos = ya = 0
    for p in sorted(origen.glob("tt_*")):
        m = re.match(r"tt_(\d{10,25})\.", p.name)
        if not m or p.suffix.lower() not in EXT_VIDEO or not p.is_file():
            continue
        vid = m.group(1)
        if vid in tenidos:
            ya += 1
            continue
        if a.simular:
            print(f"  (simulacion) traeria {p.name}")
            nuevos += 1
            continue
        if not copiar_a(local, p):
            continue
        fila = dict(delas.get(vid) or {"id": vid})
        fila.update(id=vid, archivo=p.name, estado="bajado", en_drive="si")
        filas[vid] = fila
        tenidos.add(vid)
        nuevos += 1
        print(f"  + {p.name} ({p.stat().st_size / 1e6:.1f} MB)")
    if not a.simular:
        for vid, fila in delas.items():        # lo que Drive tiene anotado y aqui no (por ejemplo lo que el servidor descarto)
            filas.setdefault(vid, fila)
        if nuevos or delas:
            escribir_fuentes(local, filas)
    print(f"Listo: {nuevos} video(s) traidos de {origen}; {ya} ya estaban aqui.")
    if nuevos and not a.simular:
        print(f"Siguiente paso: python scripts/indexar_broll.py {a.producto}   (saca las hojas de contacto; luego Claude las mira una vez)")
    return 0


# ------------------------------------------------------------------ comando: estado
def estado(a) -> int:
    prod = PUBLIC / "productos" / a.producto
    if not prod.is_dir():
        salir(f"No existe public/productos/{a.producto}")
    local = prod / CARPETA_LOCAL
    cfg = leer_config(prod)
    filas = leer_fuentes(local)
    videos = [p for p in local.glob("*") if p.suffix.lower() in EXT_VIDEO] if local.is_dir() else []
    bajados = [f for f in filas.values() if f.get("estado") == "bajado"]
    descartados = [f for f in filas.values() if f.get("estado") == "descartado"]
    print(f"{a.producto}: {len(videos)} video(s) en {CARPETA_LOCAL}/ ({len(bajados)} bajados con broll_tiktok.py, {len(descartados)} descartados anotados)")
    pendientes = [f for f in bajados if f.get("en_drive") != "si"]
    ruta = cfg.get("drive")
    if ruta:
        d = Path(str(ruta))
        print(f"Drive de la oferta: {ruta} -> " + ("encontrada" if d.is_dir() else "NO esta en este PC") + f"; {len(pendientes)} video(s) sin copiar")
    else:
        print("Drive de la oferta: sin configurar (pon \"drive\" en broll.json o usa --drive al traer)")
    print("Fuentes guardadas en broll.json: " + (f"{len(_lista(cfg.get('consultas')))} consultas, {len(_lista(cfg.get('hashtags')))} hashtags, {len(_lista(cfg.get('cuentas')))} cuentas" if cfg else "ninguna"))
    llave = leer_env("SCRAPECREATORS_API_KEY")
    print("Busqueda por palabra: gratis con la lista publica de TikTok (--proveedor descubrir)"
          + ("; ademas hay llave de ScrapeCreators en .env (--proveedor scrapecreators)" if llave else ""))
    for f in descartados[-5:]:
        print(f"  descartado {f['id']}: {f.get('motivo', '')}")
    return 0


def probar_proveedor(nombre: str = "descubrir") -> int:
    """Una busqueda de prueba. Con descubrir (gratis) dice si TikTok esta dando su lista publica; con scrapecreators gasta 1 credito
    para saber si la llave sirve y cuantos creditos quedan. No imprime ninguna llave."""
    if (nombre or "descubrir") == "descubrir":
        try:
            prov = Descubrir(3)
            videos = prov.buscar("dinamicas para mujeres", 1)
            rel = prov.relacionadas("dinamicas para mujeres")
        except ErrorFuente as e:
            print(f"La lista publica de TikTok NO esta dando videos: {e}")
            return 2
        if not videos:
            print("La lista publica de TikTok contesto, pero sin videos para la palabra de prueba: puede haber cambiado.")
            return 2
        print(f"La lista publica de TikTok sirve: la busqueda de prueba trajo {len(videos)} video(s) y {len(rel)} palabra(s) relacionada(s) ({prov.gastados} pedidos, gratis).")
        return 0
    try:
        prov = Proveedor(llave_proveedor(), 1)
        videos = prov.buscar("dinamicas para mujeres", 1, "", "relevancia", "todo")
    except ErrorFuente as e:
        print(f"La llave del proveedor NO sirve: {e}")
        return 2
    print(f"La llave del proveedor sirve: la busqueda de prueba trajo {len(videos)} video(s)"
          + (f" y le quedan {prov.quedan:g} credito(s)." if prov.quedan is not None else "."))
    return 0


def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)
    t = sub.add_parser("traer", help="busca y baja b-roll de TikTok")
    t.add_argument("producto")
    t.add_argument("--consulta", action="append")
    t.add_argument("--hashtag", action="append")
    t.add_argument("--cuenta", action="append")
    t.add_argument("--enlace", action="append")
    t.add_argument("--enlaces")
    t.add_argument("--excluir-autor", dest="excluir_autor", action="append", metavar="NOMBRE",
                   help="nombre del anunciante original: no se bajan los videos de busqueda que sean de SU cuenta de TikTok (tambien sirve 'excluir_autores' en broll.json)")
    t.add_argument("--filtro-desc", dest="filtro_desc", action="append",
                   help="palabra del tema (repetible): solo se bajan los videos cuya descripcion traiga alguna (no aplica a --enlace)")
    t.add_argument("--max", type=int)
    t.add_argument("--min-seg", dest="min_seg", type=float)
    t.add_argument("--max-seg", dest="max_seg", type=float)
    t.add_argument("--drive")
    t.add_argument("--sin-drive", action="store_true")
    t.add_argument("--region")
    t.add_argument("--orden", choices=sorted(ORDENES), default="relevancia")
    t.add_argument("--periodo", choices=sorted(PERIODOS), default="todo")
    t.add_argument("--paginas", type=int)
    t.add_argument("--max-creditos", dest="max_creditos", type=int, default=12)
    t.add_argument("--proveedor", choices=("descubrir", "scrapecreators"), help="de donde sale la busqueda por palabra (descubrir = gratis, es lo normal)")
    t.add_argument("--relacionadas", type=int, help="por cada consulta, cuantas palabras relacionadas de TikTok seguir tambien (1 pagina cada una)")
    t.add_argument("--codec", choices=("mejor", "h264"), default="mejor")
    t.add_argument("--permitir-horizontal", action="store_true", dest="permitir_horizontal")
    t.add_argument("--simular", action="store_true")
    e = sub.add_parser("estado", help="que hay y que falta")
    e.add_argument("producto")
    pv = sub.add_parser("proveedor", help="prueba la busqueda por palabra: la lista publica de TikTok (gratis) o la llave de ScrapeCreators (gasta 1 credito)")
    pv.add_argument("cual", nargs="?", choices=("descubrir", "scrapecreators"), default="descubrir")
    r = sub.add_parser("recoger", help="trae al editor lo que el servidor dejo en Drive > B-rolls")
    r.add_argument("producto")
    r.add_argument("--drive", help="carpeta de Drive de la oferta EN ESTE PC (si no esta en broll.json)")
    r.add_argument("--simular", action="store_true")
    return ap


def main() -> None:
    configurar_consola()
    a = construir_parser().parse_args()
    if a.comando == "traer":
        sys.exit(traer(a))
    if a.comando == "proveedor":
        sys.exit(probar_proveedor(a.cual))
    if a.comando == "recoger":
        sys.exit(recoger(a))
    sys.exit(estado(a))


if __name__ == "__main__":
    main()
