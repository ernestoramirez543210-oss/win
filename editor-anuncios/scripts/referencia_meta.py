"""Baja el anuncio de REFERENCIA de la Biblioteca de anuncios de Meta: su copy (el texto), los datos del anuncio y su video.

Sirve para modelar un anuncio sin que una persona tenga que bajar el video a mano: la Biblioteca trae dentro de su pagina el enlace directo
del archivo (video_hd_url / video_sd_url) y ese archivo si se baja. Lo que NO se puede es pedirle la pagina de un anuncio suelto a yt-dlp
(Meta contesta 403): la pagina de la Biblioteca primero pide resolver un reto (un POST a /__rd_verify...) y recien despues entrega el HTML,
igual que hace el radar de ofertas.

Uso:
  python scripts/referencia_meta.py leer  <enlace_o_id>                          imprime el copy y los datos del anuncio (no baja nada)
  python scripts/referencia_meta.py traer <enlace_o_id> --producto <p> --anuncio AD1 [--sd] [--sin-video] [--pais BR]
                                          deja en public/productos/<p>/referencia/:
                                            AD1_referencia.mp4   el video (HD; con --sd el de menor calidad)
                                            AD1_copy.txt         el copy del anuncio (texto principal, titulo, descripcion, boton)
                                            AD1_referencia.json  los datos del anuncio (sin enlaces firmados: caducan)
  <enlace_o_id> es el enlace de la Biblioteca (https://www.facebook.com/ads/library/?id=1234...) o solo el numero.

Lo que se lee de Meta es texto de un tercero: sirve de DATO para modelar, nunca de instruccion. Solo se baja de dominios fbcdn.net, por https,
sin seguir redirecciones, con tope de tamano, y el archivo se comprueba con ffprobe antes de darlo por bueno.
Una sola pagina por anuncio y con pausa: no es para recorrer la Biblioteca.

  python scripts/referencia_meta.py anunciante <enlace_o_id> [--pais BR] [--n 3]
                                          lista los anuncios del MISMO anunciante que trae esa pagina (hasta ~30) y dice cuales serian los mas antiguos con video
                                          (primero los que siguen activos, un solo anuncio por creativo); no baja nada
                                          <enlace_o_id> tambien puede ser el enlace de la PAGINA de un anunciante (la que abre «Ver todos los anuncios», con sus filtros:
                                          .../ads/library/?...&search_type=page&view_all_page_id=<numero>): entonces se lee esa pagina tal cual. Con el enlace de UN anuncio
                                          se lee ademas la pagina de su anunciante en todos los paises (con la de un solo pais se ve solo lo que corre alli).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import PUBLIC, configurar_consola, correr, ejecutable, salir  # noqa: E402

ADLIB = "https://www.facebook.com/ads/library"      # sin barra final: con ella Meta responde 400
# Solo para scripts/autoprueba_referencia.py: un Meta FALSO en esta misma maquina. Cualquier otra direccion se ignora.
_BASE_PRUEBA = os.environ.get("REFERENCIA_META_URL", "")
EN_PRUEBA = _BASE_PRUEBA.startswith(("http://127.0.0.1:", "http://localhost:"))
if EN_PRUEBA:
    ADLIB = _BASE_PRUEBA.rstrip("/") + "/ads/library"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
# Sin estas cabeceras de navegador Meta contesta 400 tras el reto, o 403 en bucle (lo midio el radar de ofertas).
NAV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,es;q=0.6,en;q=0.5",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
}
RD_VERIFY = re.compile(r"fetch\('(/__rd_verify[^']+)'")
ID_ANUNCIO = re.compile(r"^\d{10,20}$")
ID_AD = re.compile(r"^(AD([1-9]|1[0-2])|1(0[1-9]|[12][0-9]|30))\Z")      # AD1..AD12 (los de antes) o 101..130 (los numerados, desde 2026-10-22)
NOMBRE_PRODUCTO = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MAX_VIDEO_MB = 300
MAX_IMAGEN_MB = 25


class ErrorMeta(Exception):
    """Meta no dio el anuncio (limite, reto que no se resuelve, anuncio inexistente o sin archivo)."""


# ------------------------------------------------------------------ el enlace
def id_de(texto: str) -> str | None:
    """El numero del anuncio a partir de un enlace de la Biblioteca (…/ads/library/?id=123) o del numero solo. None si no es de ahi."""
    t = (texto or "").strip()
    if ID_ANUNCIO.match(t):
        return t
    try:
        u = urlparse(t)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or host not in ("facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"):
        return None
    if not u.path.rstrip("/").endswith("/ads/library"):
        return None
    m = re.search(r"(?:^|&)id=(\d{10,20})(?:&|$)", u.query)
    return m.group(1) if m else None


def es_enlace_biblioteca(texto: str) -> bool:
    return id_de(texto) is not None


# La Biblioteca tambien se abre por anunciante («Ver todos los anuncios»): .../ads/library/?...&search_type=page&view_all_page_id=<numero>. El dueño la usa con sus filtros (solo activos,
# todos los paises, orden por impresiones, hasta una fecha) y esos filtros se respetan, pero SOLO los conocidos y con un valor del tipo esperado: nada mas viaja a Meta.
HOSTS_FACEBOOK = ("facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com")
ID_PAGINA = re.compile(r"^\d{5,25}$")
FILTROS_PAGINA = {
    "active_status": re.compile(r"^(active|inactive|all)$"),
    "ad_type": re.compile(r"^all$"),
    "country": re.compile(r"^(ALL|[A-Z]{2})$"),
    "is_targeted_country": re.compile(r"^(true|false)$"),
    "media_type": re.compile(r"^(all|image|video|meme|image_and_meme|none)$"),
    "sort_data[mode]": re.compile(r"^[a-z_]{3,40}$"),
    "sort_data[direction]": re.compile(r"^(asc|desc)$"),
    "start_date[min]": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "start_date[max]": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}
# Sin filtros propios: los que siguen activos, en todos los paises, los de mas impresiones primero (asi se ve primero lo que de verdad funciona).
FILTROS_PAGINA_BASE = {"active_status": "active", "ad_type": "all", "country": "ALL", "is_targeted_country": "false", "media_type": "all",
                       "sort_data[mode]": "total_impressions", "sort_data[direction]": "desc"}


def pagina_de(texto: str) -> dict | None:
    """Del enlace de la PAGINA de un anunciante en la Biblioteca: {'page_id', 'filtros'} (solo los filtros conocidos del enlace). None si no es un enlace asi."""
    t = (texto or "").strip()
    try:
        u = urlparse(t)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in HOSTS_FACEBOOK or not u.path.rstrip("/").endswith("/ads/library"):
        return None
    q = dict(parse_qsl(u.query, keep_blank_values=True))
    if not ID_PAGINA.match(q.get("view_all_page_id", "")):
        return None
    return {"page_id": q["view_all_page_id"], "filtros": {k: v for k, v in q.items() if k in FILTROS_PAGINA and FILTROS_PAGINA[k].match(v)}}


def es_enlace_pagina(texto: str) -> bool:
    return pagina_de(texto) is not None


def es_enlace_meta(texto: str) -> bool:
    """El enlace de UN anuncio o el de la pagina de un anunciante en la Biblioteca (es_enlace_biblioteca es solo el de un anuncio: es el que sabe bajar fabrica.py traer)."""
    return es_enlace_biblioteca(texto) or es_enlace_pagina(texto)


# ------------------------------------------------------------------ la pagina de la Biblioteca
class ClienteMeta:
    """Un GET de la Biblioteca resolviendo el reto de Meta (como el radar): 403 con un script que hace POST a /__rd_verify_...?challenge=N,
    que deja la cookie rd_challenge, y se vuelve a pedir."""

    def __init__(self):
        import requests

        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, **NAV_HEADERS})
        self.base = _BASE_PRUEBA.rstrip("/") if EN_PRUEBA else "https://www.facebook.com"

    def abrir(self, params: dict):
        r = None
        for _ in range(6):
            r = self.s.get(ADLIB, params=params, timeout=45)
            m = RD_VERIFY.search(r.text) if r.status_code == 403 else None
            if not m:
                return r
            v = self.s.post(self.base + m.group(1), timeout=45, headers={"Referer": r.url, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
            if v.status_code == 429:      # el propio reto se limita: esperar y repetir
                time.sleep(random.uniform(3, 6))
        return r

    def pagina(self, ad_id: str, pais: str = "") -> str:
        """El HTML de la Biblioteca para ese anuncio. Con `pais` (BR, CO…) la pagina trae ademas los anuncios del anunciante."""
        params = {"id": ad_id}
        if pais:
            params.update({"country": pais, "active_status": "all", "ad_type": "all", "media_type": "all"})
        r = self.abrir(params)
        if r.status_code in (403, 429):
            raise ErrorMeta(f"Meta limito esta conexion (HTTP {r.status_code}): espera un rato y repite; si pasa seguido, prueba desde el servidor")
        if r.status_code != 200:
            raise ErrorMeta(f"la Biblioteca de Meta contesto HTTP {r.status_code}")
        return r.text

    def pagina_anunciante(self, page_id: str, filtros: dict | None = None) -> str:
        """El HTML de la Biblioteca con los anuncios de UN anunciante (la pagina de «Ver todos los anuncios»), con los filtros dados o, sin ellos, los de siempre."""
        params = {**FILTROS_PAGINA_BASE, **(filtros or {}), "search_type": "page", "view_all_page_id": page_id}
        r = self.abrir(params)
        if r.status_code in (403, 429):
            raise ErrorMeta(f"Meta limito esta conexion (HTTP {r.status_code}): espera un rato y repite; si pasa seguido, prueba desde el servidor")
        if r.status_code != 200:
            raise ErrorMeta(f"la Biblioteca de Meta contesto HTTP {r.status_code}")
        return r.text


def _decodificar_en(html: str, posicion: int):
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, posicion)
        return obj
    except ValueError:
        return None


def buscar_anuncio(html: str, ad_id: str) -> dict | None:
    """El objeto del anuncio (con su `snapshot`) dentro del HTML. Primero el suelto: la pagina de un anuncio lo trae entero; si no,
    entre los resultados de la busqueda (search_results_connection) que la Biblioteca incrusta."""
    marca = re.compile(r'"ad_archive_id"\s*:\s*"' + re.escape(ad_id) + '"')
    for m in marca.finditer(html):
        # el objeto del anuncio empieza en alguna llave anterior: se prueba hacia atras hasta que decodifique entero y sea de este anuncio
        pos, ini = m.start(), m.start()
        for _ in range(80):
            ini = html.rfind("{", max(0, pos - 4000), ini)
            if ini == -1:
                break
            obj = _decodificar_en(html, ini)
            if isinstance(obj, dict) and str(obj.get("ad_archive_id")) == ad_id and isinstance(obj.get("snapshot"), dict):
                return obj
    for m in re.finditer(r'"search_results_connection":', html):
        con = _decodificar_en(html, m.end())
        if not isinstance(con, dict):
            continue
        for edge in con.get("edges") or []:
            for ad in (edge.get("node") or {}).get("collated_results") or []:
                if isinstance(ad, dict) and str(ad.get("ad_archive_id")) == ad_id and isinstance(ad.get("snapshot"), dict):
                    return ad
    return None


# ------------------------------------------------------------------ lo que se saca del anuncio
def _texto(x) -> str:
    return str(x).strip() if x not in (None, "") else ""


def copy_de(ad: dict) -> str:
    """El copy del anuncio tal como lo ve la gente: texto principal, titulo, descripcion y boton; en un carrusel, el de cada tarjeta."""
    s = ad.get("snapshot") or {}
    partes = []
    cuerpo = _texto((s.get("body") or {}).get("text"))
    if cuerpo:
        partes.append(cuerpo)
    for k, rotulo in (("title", "Titulo"), ("link_description", "Descripcion")):
        v = _texto(s.get(k))
        if v and v not in partes:
            partes.append(f"{rotulo}: {v}")
    tarjetas = s.get("cards") or []
    for i, c in enumerate(tarjetas, 1):
        if not isinstance(c, dict):
            continue
        t = " | ".join(x for x in (_texto(c.get("title")), _texto(c.get("body"))) if x)
        if t and t != cuerpo:
            partes.append(f"Tarjeta {i}: {t}")
    boton = _texto(s.get("cta_text"))
    if boton:
        partes.append(f"Boton: {boton}")
    return "\n\n".join(partes)


def datos_de(ad: dict, ad_id: str) -> dict:
    """Los datos utiles para modelar, SIN enlaces firmados (caducan y no hace falta guardarlos)."""
    s = ad.get("snapshot") or {}
    inicio = ad.get("start_date")
    en_aire, dias = "", None
    try:
        t0 = datetime.fromtimestamp(int(inicio), tz=timezone.utc)
        en_aire, dias = t0.date().isoformat(), (datetime.now(timezone.utc) - t0).days
    except (TypeError, ValueError, OSError):
        pass
    link = _texto(s.get("link_url"))
    return {
        "ad_id": ad_id, "anunciante": _texto(s.get("page_name") or ad.get("page_name")), "page_id": _texto(ad.get("page_id") or s.get("page_id")),
        "formato": _texto(s.get("display_format")), "boton": _texto(s.get("cta_text")), "boton_tipo": _texto(s.get("cta_type")),
        "destino": urlparse(link).netloc if link else "", "activo": ad.get("is_active"), "en_aire_desde": en_aire, "dias_en_aire": dias,
        "variantes": ad.get("collation_count"), "plataformas": ad.get("publisher_platform") or [], "tarjetas": len(s.get("cards") or []),
        "tiene_video": bool(_videos_de(ad)), "obtenido_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _videos_de(ad: dict) -> list[dict]:
    s = ad.get("snapshot") or {}
    fuentes = [v for v in (s.get("videos") or []) if isinstance(v, dict)] + [c for c in (s.get("cards") or []) if isinstance(c, dict)]
    return [v for v in fuentes if v.get("video_hd_url") or v.get("video_sd_url")]


def elegir_video(ad: dict, sd: bool = False) -> tuple[str, str] | None:
    """(enlace, calidad) del primer video del anuncio (en un carrusel, de la primera tarjeta con video)."""
    for v in _videos_de(ad):
        hd, bajo = v.get("video_hd_url"), v.get("video_sd_url")
        if sd and bajo:
            return bajo, "sd"
        if hd:
            return hd, "hd"
        if bajo:
            return bajo, "sd"
    return None


def elegir_imagen(ad: dict) -> str | None:
    s = ad.get("snapshot") or {}
    for i in (s.get("images") or []) + (s.get("cards") or []):
        if isinstance(i, dict):
            u = i.get("original_image_url") or i.get("resized_image_url")
            if u:
                return u
    return None


# ------------------------------------------------------------------ los anuncios del mismo anunciante (para elegir los mas antiguos)
def anuncios_embebidos(html: str) -> tuple[list[dict], bool]:
    """Los anuncios que la pagina incrusta (search_results_connection) y si Meta dice que hay mas paginas. Cuando se pide con `pais`, son los del
    mismo anunciante del anuncio pedido (hasta ~30 por pagina; para ver mas hace falta pedir las paginas siguientes, que esta funcion no hace)."""
    salida, vistos, hay_mas = [], set(), False
    for m in re.finditer(r'"search_results_connection":', html):
        con = _decodificar_en(html, m.end())
        if not isinstance(con, dict):
            continue
        hay_mas = hay_mas or bool((con.get("page_info") or {}).get("has_next_page"))
        for edge in con.get("edges") or []:
            for ad in (edge.get("node") or {}).get("collated_results") or []:
                if isinstance(ad, dict) and isinstance(ad.get("snapshot"), dict):
                    k = str(ad.get("ad_archive_id") or "")
                    if k and k not in vistos:
                        vistos.add(k)
                        salida.append(ad)
    return salida, hay_mas


def _page_id(ad: dict) -> str:
    return _texto(ad.get("page_id") or (ad.get("snapshot") or {}).get("page_id"))


def _inicio(ad: dict) -> int | None:
    try:
        return int(ad.get("start_date"))
    except (TypeError, ValueError):
        return None


def anuncios_de_la_pagina(page_id: str, filtros: dict | None = None, cliente=None) -> dict:
    """Los anuncios de un anunciante segun la pagina de la Biblioteca que se abre con «Ver todos los anuncios», en el orden y con los filtros pedidos (por omision: activos, todos los
    paises, los de mas impresiones primero): {'anunciante','page_id','referencia_id' (vacio),'anuncios','hay_mas','filtros'}. Una sola pagina de la Biblioteca (hasta ~30 anuncios)."""
    if not ID_PAGINA.match(page_id or ""):
        raise ErrorMeta("el numero de pagina del anunciante no es valido")
    cliente = cliente or ClienteMeta()
    html = cliente.pagina_anunciante(page_id, filtros)
    todos, hay_mas = anuncios_embebidos(html)
    mismos = [a for a in todos if _page_id(a) == page_id]
    if not mismos:
        raise ErrorMeta("la pagina de ese anunciante no trae ningun anuncio con esos filtros (o Meta cambio la pagina)")
    nombre = next((n for n in (_texto((a.get("snapshot") or {}).get("page_name") or a.get("page_name")) for a in mismos) if n), "")
    return {"anunciante": nombre, "page_id": page_id, "referencia_id": "", "anuncios": mismos, "hay_mas": hay_mas, "filtros": {**FILTROS_PAGINA_BASE, **(filtros or {})}}


def anuncios_del_anunciante(enlace_o_id: str, pais: str = "BR", cliente=None) -> dict:
    """Los anuncios del anunciante de un enlace de la Biblioteca: {'anunciante','page_id','referencia_id','anuncios':[objetos de Meta],'hay_mas'}.
    - Enlace de la PAGINA de un anunciante: se lee esa pagina tal cual, con los filtros del enlace.
    - Enlace (o numero) de UN anuncio: se abre su pagina con el pais pedido para hallar al anunciante y, si trae mas anuncios su pagina en TODOS los paises (activos, los de mas impresiones
      primero), se usa esa lista: con el pais de un solo mercado solo se ve lo que corre alli (un anunciante de LATAM salio con 1 anuncio de 52).
    Una o dos paginas de la Biblioteca. Lanza ErrorMeta si el anuncio no aparece."""
    ad_id = id_de(enlace_o_id)
    if not ad_id:
        pag = pagina_de(enlace_o_id)
        if pag:
            return anuncios_de_la_pagina(pag["page_id"], pag["filtros"], cliente)
        raise ErrorMeta("eso no es un enlace de la Biblioteca de anuncios de Meta (facebook.com/ads/library/?id=… o la pagina de un anunciante) ni un numero de anuncio")
    cliente = cliente or ClienteMeta()
    html = cliente.pagina(ad_id, pais=pais or "BR")
    ref = buscar_anuncio(html, ad_id)
    if ref is None:
        raise ErrorMeta("el anuncio no aparece en la pagina de la Biblioteca (ya no esta activo, o Meta cambio la pagina)")
    todos, hay_mas = anuncios_embebidos(html)
    page_id = _page_id(ref)
    mismos = [a for a in todos if not page_id or _page_id(a) == page_id]
    if str(ref.get("ad_archive_id")) not in {str(a.get("ad_archive_id")) for a in mismos}:
        mismos.insert(0, ref)
    resultado = {"anunciante": _texto((ref.get("snapshot") or {}).get("page_name") or ref.get("page_name")), "page_id": page_id, "referencia_id": ad_id, "anuncios": mismos, "hay_mas": hay_mas}
    if page_id and ID_PAGINA.match(page_id):
        try:
            grande = anuncios_de_la_pagina(page_id, None, cliente)
        except ErrorMeta:
            grande = None           # la pagina del anunciante no contesto: se queda con lo que dio la del anuncio
        if grande and len(grande["anuncios"]) > len(mismos):
            ids = {str(a.get("ad_archive_id")) for a in grande["anuncios"]}
            resultado.update({"anuncios": grande["anuncios"] if str(ref.get("ad_archive_id")) in ids else [ref] + grande["anuncios"], "hay_mas": grande["hay_mas"],
                              "filtros": grande["filtros"], "amplia": True})
    return resultado


def _firma(ad: dict) -> str:
    """Dos anuncios con el mismo texto (o del mismo grupo de variantes de Meta) son el mismo creativo: se cuenta uno solo."""
    t = re.sub(r"\s+", " ", _texto(((ad.get("snapshot") or {}).get("body") or {}).get("text"))).lower()[:160]
    if t:
        return "t:" + t
    g = _texto(ad.get("collation_id"))
    return "g:" + g if g else "i:" + _texto(ad.get("ad_archive_id"))


def elegir_mas_antiguos(anuncios: list[dict], n: int = 3, solo_video: bool = True) -> list[dict]:
    """Los `n` anuncios mas antiguos (por fecha de inicio) con video y de creativos distintos. Primero los que SIGUEN activos (los que llevan tiempo y todavia se pagan
    son los que funcionan); si no alcanzan, se completa con los ya apagados. Devuelve los objetos de Meta tal cual."""
    n = max(1, min(int(n), 10))
    candidatos = [a for a in anuncios if _inicio(a) is not None and (not solo_video or _videos_de(a))]
    candidatos.sort(key=lambda a: (0 if a.get("is_active", True) else 1, _inicio(a), _texto(a.get("ad_archive_id"))))
    elegidos, firmas = [], set()
    for a in candidatos:
        k = _firma(a)
        if k in firmas:
            continue
        firmas.add(k)
        elegidos.append(a)
        if len(elegidos) >= n:
            break
    return elegidos


def resumen_corto(ad: dict) -> dict:
    """Lo que se anota de un anuncio elegido (sin enlaces firmados)."""
    d = datos_de(ad, _texto(ad.get("ad_archive_id")))
    d["inicio"] = d.pop("en_aire_desde")
    d["copy_inicio"] = re.sub(r"\s+", " ", _texto(((ad.get("snapshot") or {}).get("body") or {}).get("text")))[:140]
    return {k: d[k] for k in ("ad_id", "anunciante", "page_id", "formato", "activo", "inicio", "dias_en_aire", "variantes", "tiene_video", "copy_inicio")}


# ------------------------------------------------------------------ bajar el archivo
def host_permitido(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if EN_PRUEBA and u.scheme == "http" and host in ("127.0.0.1", "localhost"):
        return True
    return u.scheme == "https" and not u.username and not u.password and (host == "fbcdn.net" or host.endswith(".fbcdn.net"))


def bajar(url: str, destino: Path, max_mb: int, tipo: str) -> None:
    """Baja UN archivo de fbcdn.net: las redirecciones se siguen a mano y cada salto debe ser tambien de fbcdn.net por https; con tope de tamano y del
    tipo esperado (video/ o image/). Lanza ErrorMeta si no sirve."""
    import requests

    if not host_permitido(url):
        raise ErrorMeta("el enlace del archivo no es de un dominio de Meta (fbcdn.net) por https: no se baja")
    destino.parent.mkdir(parents=True, exist_ok=True)
    parte = destino.with_name(destino.name + ".parte")
    try:
        actual, total = url, 0
        for _salto in range(5):
            if not host_permitido(actual):
                raise ErrorMeta("el archivo redirige a un dominio que no es de Meta (fbcdn.net) por https: no se baja")
            with requests.get(actual, stream=True, timeout=60, allow_redirects=False, headers={"User-Agent": UA, "Accept": "*/*"}) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    actual = urljoin(actual, r.headers.get("Location", ""))
                    continue
                if r.status_code != 200:
                    raise ErrorMeta(f"Meta no entrego el archivo (HTTP {r.status_code}); el enlace del anuncio caduca en horas: vuelve a leer el anuncio")
                if not r.headers.get("Content-Type", "").lower().startswith((tipo, "application/octet-stream")):
                    raise ErrorMeta(f"el archivo no es {tipo.rstrip('/')} (Content-Type {r.headers.get('Content-Type', '?')[:40]})")
                largo = int(r.headers.get("Content-Length") or 0)
                if largo > max_mb * 1_000_000:
                    raise ErrorMeta(f"el archivo pesa {largo / 1e6:.0f} MB (tope {max_mb} MB)")
                with parte.open("wb") as f:
                    for trozo in r.iter_content(1 << 20):
                        total += len(trozo)
                        if total > max_mb * 1_000_000:
                            raise ErrorMeta(f"el archivo supera el tope de {max_mb} MB")
                        f.write(trozo)
                break
        else:
            raise ErrorMeta("el enlace del archivo redirige demasiadas veces")
        if total < 20_000:
            raise ErrorMeta("el archivo bajado es demasiado chico para ser un video o una imagen")
        os.replace(parte, destino)
    except requests.RequestException as e:
        raise ErrorMeta(f"no se pudo bajar el archivo ({e.__class__.__name__})") from None
    finally:
        parte.unlink(missing_ok=True)


def comprobar_video(ruta: Path) -> dict:
    """ffprobe: que se abra, tenga video y su duracion. Devuelve {'duracion', 'ancho', 'alto', 'audio'} (vacio si no hay ffprobe)."""
    if not shutil.which("ffprobe"):
        return {}
    r = correr([ejecutable("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)])
    if r.returncode != 0:
        raise ErrorMeta("el video bajado no se abre (ffprobe): " + (r.stderr or "").strip()[:100])
    info = json.loads(r.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise ErrorMeta("el archivo bajado no tiene pista de video")
    dur = float(info.get("format", {}).get("duration") or 0)
    if dur < 2:
        raise ErrorMeta(f"el video dura {dur:.1f} s: no parece un anuncio")
    return {"duracion": round(dur, 1), "ancho": int(v.get("width") or 0), "alto": int(v.get("height") or 0),
            "audio": any(s.get("codec_type") == "audio" for s in info.get("streams", []))}


# ------------------------------------------------------------------ lo que usa fabrica.py y la linea de comandos
def leer(enlace_o_id: str, pais: str = "") -> tuple[dict, str]:
    """(anuncio, id) del anuncio de la Biblioteca, o ErrorMeta."""
    ad_id = id_de(enlace_o_id)
    if not ad_id:
        raise ErrorMeta("eso no es un enlace de la Biblioteca de anuncios de Meta (facebook.com/ads/library/?id=…) ni un numero de anuncio")
    cliente = ClienteMeta()
    ad = buscar_anuncio(cliente.pagina(ad_id), ad_id)
    if ad is None and not pais:
        time.sleep(random.uniform(2, 4))       # la pagina sin pais a veces no trae el anuncio: una segunda mirada, con pais
        ad = buscar_anuncio(cliente.pagina(ad_id, pais="BR"), ad_id)
    if ad is None:
        raise ErrorMeta("el anuncio no aparece en la pagina de la Biblioteca (ya no esta activo, o Meta cambio la pagina)")
    return ad, ad_id


def traer_referencia(enlace_o_id: str, carpeta_referencia: Path, anuncio: str, *, sd: bool = False, sin_video: bool = False, pais: str = "") -> dict:
    """Baja el copy, los datos y el video del anuncio a <carpeta_referencia>/<anuncio>_*. Devuelve un resumen. Lanza ErrorMeta si no se pudo leer
    el anuncio; si lo unico que falla es el archivo de video, el copy y los datos quedan y el resumen dice por que."""
    ad, ad_id = leer(enlace_o_id, pais)
    return guardar_referencia(ad, ad_id, carpeta_referencia, anuncio, sd=sd, sin_video=sin_video)


def guardar_referencia(ad: dict, ad_id: str, carpeta_referencia: Path, anuncio: str, *, sd: bool = False, sin_video: bool = False) -> dict:
    """Lo mismo que traer_referencia, pero con un anuncio que ya se leyo (por ejemplo, uno de la lista de su anunciante): no vuelve a pedir su pagina.
    Los enlaces del video caducan en horas: hay que bajarlo enseguida."""
    carpeta_referencia.mkdir(parents=True, exist_ok=True)
    datos = datos_de(ad, ad_id)
    copy = copy_de(ad)
    (carpeta_referencia / f"{anuncio}_copy.txt").write_text(copy + "\n", encoding="utf-8")
    resumen = {"ad_id": ad_id, "anunciante": datos["anunciante"], "formato": datos["formato"], "copy_caracteres": len(copy), "video": None, "motivo_sin_video": ""}
    if not sin_video:
        eleccion = elegir_video(ad, sd)
        if eleccion:
            destino = carpeta_referencia / f"{anuncio}_referencia.mp4"
            try:
                bajar(eleccion[0], destino, MAX_VIDEO_MB, "video/")
                tec = comprobar_video(destino)
            except ErrorMeta as e:
                destino.unlink(missing_ok=True)
                resumen["motivo_sin_video"] = str(e)
            else:
                resumen["video"] = destino.name
                datos.update({"video_calidad": eleccion[1], **{f"video_{k}": v for k, v in tec.items()}})
        else:
            resumen["motivo_sin_video"] = "el anuncio no tiene video (es de imagen o texto)"
            imagen = elegir_imagen(ad)
            if imagen:
                destino = carpeta_referencia / f"{anuncio}_referencia.jpg"
                try:
                    bajar(imagen, destino, MAX_IMAGEN_MB, "image/")
                    resumen["imagen"] = destino.name
                except ErrorMeta as e:
                    resumen["motivo_sin_video"] += f"; tampoco se pudo bajar la imagen ({e})"
    (carpeta_referencia / f"{anuncio}_referencia.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resumen


# ------------------------------------------------------------------ linea de comandos
def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)
    le = sub.add_parser("leer", help="imprime el copy y los datos")
    le.add_argument("anuncio_ref", metavar="enlace_o_id")
    le.add_argument("--pais", default="")
    an = sub.add_parser("anunciante", help="lista los anuncios del mismo anunciante (de un anuncio o de la pagina del anunciante) y dice cuales serian los mas antiguos con video (no baja nada)")
    an.add_argument("anuncio_ref", metavar="enlace_o_id")
    an.add_argument("--pais", default="BR")
    an.add_argument("--n", type=int, default=3)
    tr = sub.add_parser("traer", help="baja copy, datos y video a la carpeta de referencia del producto")
    tr.add_argument("anuncio_ref", metavar="enlace_o_id")
    tr.add_argument("--producto", required=True)
    tr.add_argument("--anuncio", required=True)
    tr.add_argument("--pais", default="")
    tr.add_argument("--sd", action="store_true", help="el video de menor calidad (pesa menos)")
    tr.add_argument("--sin-video", action="store_true", dest="sin_video", help="solo el copy y los datos")
    a = ap.parse_args()
    try:
        if a.comando == "anunciante":
            info = anuncios_del_anunciante(a.anuncio_ref, a.pais)
            con_video = [x for x in info["anuncios"] if _videos_de(x)]
            print(f"Anunciante: {info['anunciante'] or '?'} (page_id {info['page_id'] or '?'}): {len(info['anuncios'])} anuncio(s) en la pagina, {len(con_video)} con video, "
                  + ("hay mas paginas que no se leyeron" if info["hay_mas"] else "no hay mas paginas"))
            for i, ad in enumerate(elegir_mas_antiguos(info["anuncios"], a.n), 1):
                r = resumen_corto(ad)
                print(f"  {i}. {r['ad_id']}  desde {r['inicio'] or '?'} ({r['dias_en_aire'] if r['dias_en_aire'] is not None else '?'} dias, {'activo' if r['activo'] else 'apagado'})  {r['copy_inicio'][:90]!r}")
            return
        if a.comando == "leer":
            ad, ad_id = leer(a.anuncio_ref, a.pais)
            d = datos_de(ad, ad_id)
            print(f"Anuncio {ad_id} de {d['anunciante'] or '?'}: {d['formato'] or '?'}, {'activo' if d['activo'] else 'inactivo'}"
                  + (f", en el aire desde {d['en_aire_desde']} ({d['dias_en_aire']} dias)" if d["en_aire_desde"] else "") + f", boton «{d['boton']}» hacia {d['destino'] or '?'}")
            print("\n" + copy_de(ad))
            return
        if not NOMBRE_PRODUCTO.match(a.producto) or not ID_AD.match(a.anuncio):
            salir("Producto o anuncio no validos (producto en minusculas sin espacios; anuncio AD1 a AD12).")
        carpeta = PUBLIC / "productos" / a.producto / "referencia"
        if not carpeta.parent.is_dir():
            salir(f"No existe public/productos/{a.producto}. Crea el producto primero (python scripts/nuevo_producto.py {a.producto}).")
        res = traer_referencia(a.anuncio_ref, carpeta, a.anuncio, sd=a.sd, sin_video=a.sin_video, pais=a.pais)
    except ErrorMeta as e:
        salir(str(e))
    print(f"Anuncio {res['ad_id']} de {res['anunciante'] or '?'} ({res['formato'] or '?'}): copy de {res['copy_caracteres']} caracteres -> {carpeta / (a.anuncio + '_copy.txt')}")
    if res["video"]:
        print(f"Video -> {carpeta / res['video']}")
    elif not a.sin_video:
        print(f"SIN video: {res['motivo_sin_video']}. Queda el copy; pidele a una persona el video (ponlo en {carpeta} como {a.anuncio}_referencia.mp4).")
        sys.exit(2)


if __name__ == "__main__":
    main()
