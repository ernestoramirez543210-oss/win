"""
flujo.py - Los dos IDs que hacen falta para abrir el FLUJO DE WHATSAPP de una oferta desde el dash: el ID de la pagina del anunciante y el ID del video de su anuncio.

Pedido del dueno (2026-10-22): un boton en la ficha de cada oferta que lo lleve al flujo de WhatsApp del anunciante, directo desde el anuncio. Lo que hacia a mano: buscar la pagina en la
Biblioteca de anuncios, tomar el ID del video del anuncio y armar  facebook.com/permalink.php?story_fbid={ID_VIDEO}&id={ID_PAGINA}  (esa es la PUBLICACION del anuncio en la pagina del
anunciante, y ahi esta su boton «Enviar mensaje de WhatsApp»). El dash arma ese enlace solo; este modulo consigue los dos IDs y los deja en dos columnas de OFERTAS: «ID de la página» e
«ID del video».

De donde sale el ID del video: la Biblioteca no lo da como un campo, pero la direccion de cada video (video_hd_url / video_sd_url) lleva un parametro `efg` que es un JSON en base64 con
`xpv_asset_id` = el ID del video en Facebook. Medido el 2026-10-22 con las 1.888 direcciones de video de 956 anuncios guardados en debug/cache: 1.880 lo traen (el 99,6 %).

Dos caminos:
  - Una oferta NUEVA: radar.py ya tiene en la mano los anuncios del anunciante cuando la registra, asi que saca los dos IDs sin ninguna peticion mas (ids_de_grupo) y los escribe con la fila.
  - Las que YA estan en la planilla (y las que el dueno pegue a mano): `radar.py --rellenar-flujo` (radar-flujo.timer, una vez al dia) busca las filas sin ID, halla la pagina (por el enlace
    de la fila, por la pagina del anuncio en la Biblioteca o, si el anuncio ya se apago, por el nombre del anunciante que el radar ya vio) y lee los anuncios de esa pagina. Cuesta 1 o 2 peticiones al HTML de la Biblioteca por fila, con un tope por corrida
    (MAXIMO_POR_CORRIDA); si Meta limita la IP se corta y al dia siguiente sigue.

Reglas: solo ESCRIBE en las dos columnas nuevas y solo en celdas VACIAS (nunca pisa lo que el dueno puso a mano); la fila se vuelve a comprobar justo antes de escribir (si alguien inserto o
borro filas mientras tanto se busca otra vez por el ID del anuncio); lo que no se pudo hallar queda anotado en flujo.json y no se reintenta hasta 7 dias despues. No usa Gemini ni tokens.
"""
import base64
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import publicacion
import sheets
from archivos import escribir_atomico

HERE = Path(os.environ.get("RADAR_DATOS") or Path(__file__).resolve().parent)   # carpeta de los DATOS (en el VPS /opt/radar-de-ofertas; el codigo vive aparte)
RUTA = HERE / "flujo.json"
RUTA_ANTES = HERE / "flujo_antes.json"     # los valores que la busqueda reemplazo en la planilla (para deshacer)
COLUMNA_ORIGEN = "Origen del flujo"          # «google» (hallado por la busqueda) o «manual» (lo puso el dueno): una fila con cualquiera de los dos no se vuelve a tocar
ORIGENES_FIJOS = ("google", "manual")
BOGOTA = timezone(timedelta(hours=-5))          # Colombia no tiene horario de verano
COLUMNAS = ("ID de la página", "ID del video")  # los encabezados nuevos de OFERTAS (el dash los lee sin tildes ni mayusculas)
MAXIMO_POR_CORRIDA = 12                         # filas que se intentan por corrida (1 a 3 peticiones a Meta cada una: la pagina, otros paises y, si hace falta, el anuncio suelto)
ESPERAS_HORAS = (6, 24, 72, 168)               # lo que la BUSQUEDA no hallo se reintenta a las 6 h, a las 24 h, a los 3 dias y despues cada 7 dias (cada intento gasta busquedas de Google)
REINTENTO_DIAS = 7                              # lo que no se hallo (anuncio sin video, pagina que no se ubica) no se vuelve a intentar antes
RED_FALLOS_MAX = 3                              # fallos de red seguidos tras los cuales se corta la corrida
ESTADOS_ORDEN = ("vigilar", "testiando", "ganadora", "nuevo", "", "no testeada", "descartada")   # las que el dueno mira primero se llenan primero
WHATSAPP_HOSTS = {"wa.me", "api.whatsapp.com", "whatsapp.com", "chat.whatsapp.com"}              # igual que en radar.py (test_flujo.py lo comprueba)


class SinDatos(Exception):
    """Meta contesto pero la pagina no trae resultados utilizables (otra estructura, un muro)."""


# ---- el ID del video ----
def _id(v):
    s = str(v or "").strip()
    return s if re.fullmatch(r"\d{5,25}", s) else ""


def video_id_de_url(url):
    """El ID del VIDEO que lleva dentro la direccion de un video de Facebook: el parametro `efg` es un JSON en base64 con `xpv_asset_id`. '' si no lo trae o no se entiende."""
    try:
        q = parse_qs(urlparse(str(url or "")).query).get("efg", [""])[0]
        if not q:
            return ""
        datos = json.loads(base64.urlsafe_b64decode(q + "=" * (-len(q) % 4)).decode("utf-8", "replace"))
        return _id(datos.get("xpv_asset_id"))
    except (ValueError, TypeError, AttributeError):
        return ""


def video_id_de_snapshot(snap):
    """El ID del primer video del anuncio (un video suelto o el de una tarjeta de carrusel); '' si el anuncio no trae video o la direccion no lo dice. Nunca lanza."""
    snap = snap if isinstance(snap, dict) else {}
    try:
        piezas = list(snap.get("videos") or []) + list(snap.get("cards") or [])
    except TypeError:
        return ""
    for p in piezas:
        if not isinstance(p, dict):
            continue
        for campo in ("video_hd_url", "video_sd_url"):
            v = video_id_de_url(p.get(campo))
            if v:
                return v
    return ""


def _host(url):
    if not url:
        return ""
    try:
        h = urlparse(str(url) if "//" in str(url) else "//" + str(url)).netloc.lower()
    except ValueError:                  # una direccion rota de un tercero («https://[...») no tumba nada: no es de WhatsApp
        return ""
    return h[4:] if h.startswith("www.") else h


def vende_por_whatsapp(a):
    """El anuncio manda a WhatsApp: su boton es de WhatsApp o su destino es un enlace de WhatsApp (la misma regla de radar.py)."""
    return "WHATSAPP" in str(a.get("cta_type") or "").upper() or _host(a.get("link")) in WHATSAPP_HOSTS


def anuncios_de_pagina(meta, pid, pausa=None):
    """Los anuncios activos de una pagina en la Biblioteca: primero en Brasil (lo de siempre) y, si alli no tiene NINGUNO, en todos los paises.
    Las ofertas de oro en espanol (Argentina, Mexico, Colombia, Peru, Venezuela) no corren en Brasil: con solo country=BR la Biblioteca devolvia 0 anuncios, el pedido
    de «Buscar el WhatsApp» salia «sin pagina» y la busqueda de cada hora no hallaba nunca su publicacion. Medido el 2026-11-11 con Bienestar Integral Digital
    (oro Argentina): 0 anuncios en BR y 24, todos con boton de WhatsApp, en ALL. Una pagina de Brasil sigue costando UNA peticion; solo las que no tienen
    anuncios en Brasil piden una segunda (con su pausa). Devuelve lo mismo que Meta.pagina_anunciante ({total, ads, hay_mas} o None)."""
    res = meta.pagina_anunciante(pid)
    if res and res.get("ads"):
        return res
    if pausa:
        pausa()
    todos = meta.pagina_anunciante(pid, country="ALL")
    return todos if todos and todos.get("ads") else res


MAX_LLAVES_ATRAS = 300           # objetos que se prueban hacia atras desde el ID del anuncio en el HTML de su pagina (el suyo suele ser el primero: su «ad_archive_id» va al principio)
MAX_ATRAS = 40000                # y a cuantos caracteres como mucho del ID
MAX_APARICIONES = 5             # apariciones del ID que se prueban (el HTML real lo trae una vez; un HTML raro con cientos no se vuelve lento)


def objeto_de_anuncio(html, ad_id):
    """El objeto JSON del anuncio `ad_id` dentro del HTML de SU pagina en la Biblioteca (facebook.com/ads/library/?id=<ad_id>), con su `snapshot` (texto, videos, boton), o None.
    Medido el 2026-11-13 con el de Mentora (999900000302813): el HTML (460 KB) trae el ID una vez, dentro del objeto del anuncio, y su `search_results_connection` viene vacio
    (por eso no sirve `connection_ads`). Un anuncio apagado no trae su objeto (radar.Meta.pagina_de_anuncio). Nunca lanza."""
    ad = _id(ad_id)
    if not ad or not isinstance(html, str):
        return None
    dec = json.JSONDecoder()
    for n, m in enumerate(re.finditer(r'"ad_archive_id"\s*:\s*"%s"' % ad, html)):
        if n >= MAX_APARICIONES:
            break
        j, intentos = m.start(), 0
        while intentos < MAX_LLAVES_ATRAS:
            j = html.rfind("{", 0, j)
            if j < 0 or m.start() - j > MAX_ATRAS:
                break
            intentos += 1
            try:
                o, _fin = dec.raw_decode(html, j)
            except (ValueError, RecursionError):            # RecursionError: un JSON anidado miles de niveles
                continue
            if isinstance(o, dict) and str(o.get("ad_archive_id") or "") == ad and isinstance(o.get("snapshot"), dict):
                return o
    return None


def anuncio_de_oferta(ads, ad_id):
    """El anuncio de la oferta (`ad_id`) entre los anuncios ya leidos de su pagina, o None."""
    ad = _id(ad_id)
    return next((a for a in (ads or []) if isinstance(a, dict) and str(a.get("archive_id") or "") == ad), None) if ad else None


def anuncio_propio(meta, pid, ad_id, ads, pausa=None):
    """El anuncio de UNA oferta (el de «Link del anuncio» de su fila, o el que manda la ficha del dash) para buscar SOLO su publicacion: un anunciante con varias ofertas tiene
    varios angulos en la misma pagina y la publicacion del mas repetido puede ser la de otra oferta (2026-11-13, Mentora: la oferta de «cuerpo astral» recibia la de filosofia).
    Primero entre los anuncios ya leidos de la pagina (sin peticion); si no esta (la Biblioteca solo da la primera tanda, ~30, y agrupa las copias bajo uno), leido de SU pagina
    en la Biblioteca (1 peticion, si `meta` sabe: radar.Meta.anuncio); ese solo vale si es de esa pagina (la pedida o la de sus anuncios). None si no se halla (apagado): entonces
    NO se busca la de otra oferta."""
    a = anuncio_de_oferta(ads, ad_id)
    if a is not None:
        return a if a.get("active", True) else None
    leer = getattr(meta, "anuncio", None)
    if not _id(ad_id) or not callable(leer):
        return None
    if pausa:
        pausa()
    a = leer(_id(ad_id))
    if not isinstance(a, dict) or not a.get("active", True):
        return None
    # La pagina de la oferta puede venir con su ID interno o con el PUBLICO (la busqueda de la planilla escribe el publico en «ID de la página», 6158… en vez de 9376…)
    paginas = {str(pid)} | {str(x.get("page_id")) for x in (ads or []) if isinstance(x, dict) and x.get("page_id")}
    suyas = {str(a.get("page_id") or ""), publicacion.datos_de_pagina(a.get("page_uri"), a.get("page_id"))[0]}
    return a if paginas & (suyas - {""}) else None


def elegir_anuncio_flujo(ads, preferido=""):
    """(id de la pagina, id del video, id del anuncio) del anuncio cuya publicacion se abre desde el dash, o ('', '', '') si ninguno tiene video.
    El boton es para llegar al WhatsApp del anunciante, asi que se elige ENTRE LOS ANUNCIOS QUE MANDAN A WHATSAPP (y tienen video) y solo si no hay ninguno, entre todos los que tienen video.
    Dentro del grupo elegido: el anuncio de la fila (`preferido`, el de «Link del anuncio») si esta en el, y si no el mas viejo (el que mas lleva en el aire)."""
    validos = [a for a in (ads or []) if a.get("video_id") and _id(a.get("page_id")) and a.get("active", True)]
    for grupo in ([a for a in validos if vende_por_whatsapp(a)], validos):
        if not grupo:
            continue
        a = next((x for x in grupo if preferido and str(x.get("archive_id")) == str(preferido)), None) or min(grupo, key=lambda x: x.get("start") or float("inf"))
        return a["page_id"], a["video_id"], str(a.get("archive_id") or "")
    return "", "", ""


def ids_de_grupo(grupo, preferido=""):
    """(id de la pagina, id del video) para la fila de OFERTAS de un anunciante que radar.py ya agrupo. Sin video el segundo queda vacio (la pagina se escribe igual).
    Nunca lanza: un dato raro de Meta no puede impedir que se registre una oferta."""
    try:
        pid, vid, _ad = elegir_anuncio_flujo((grupo or {}).get("ads"), preferido)
        return pid or _id((grupo or {}).get("page_id")), vid
    except Exception:
        return "", ""


# ---- las filas de la planilla ----
def _celdas(fila):
    return {sheets.fold(k): v for k, v in fila.items() if not str(k).startswith("_")}


def ad_id_de(link):
    m = re.search(r"[?&]id=(\d{5,25})\b", str(link or ""))
    return m.group(1) if m else ""


def page_id_de_enlace(link):
    """El ID de pagina de un enlace de la Biblioteca que ya lo trae (view_all_page_id), como el de las filas pegadas a mano."""
    m = re.search(r"[?&]view_all_page_id=(\d{5,25})\b", str(link or ""))
    return m.group(1) if m else ""


def columnas_de(encabezados):
    """(indice de «ID de la página», indice de «ID del video») en la fila de encabezados, o None si falta alguna."""
    f = [sheets.fold(h) for h in encabezados]
    try:
        return tuple(f.index(sheets.fold(n)) for n in COLUMNAS)
    except ValueError:
        return None


def candidatas(filas, memo, hoy):
    """(filas por completar, cuantas se saltaron por haberse intentado hace poco). Una fila entra si le falta el ID del video o el de la pagina Y se puede partir de algo
    (el ID del anuncio, el enlace de la pagina o el ID de la pagina ya escrito). Una celda con CUALQUIER cosa escrita no se toca. Ordenadas: primero las que el dueno mira."""
    out, recientes = [], 0
    cp, cv = sheets.fold(COLUMNAS[0]), sheets.fold(COLUMNAS[1])
    for f in filas:
        c = _celdas(f)
        link = str(c.get("link del anuncio") or "")
        ad_id, de_enlace = ad_id_de(link), page_id_de_enlace(link)
        pagina_celda, video_celda = str(c.get(cp) or "").strip(), str(c.get(cv) or "").strip()
        falta_pagina, falta_video = not pagina_celda, not video_celda
        if not (falta_pagina or falta_video):
            continue
        pagina = _id(pagina_celda) or de_enlace
        if not (ad_id or pagina):
            continue
        clave = ad_id or "p" + pagina
        if _reciente(memo, clave, hoy):
            recientes += 1
            continue
        out.append({"fila": f.get("_row"), "ad_id": ad_id, "de_enlace": de_enlace, "clave": clave, "anunciante": str(c.get("anunciante") or "").strip()[:60], "nombre": sheets.fold(c.get("anunciante")).strip(),
                    "estado": sheets.fold(c.get("status") or c.get("estado")).strip(), "pagina": pagina, "falta_pagina": falta_pagina, "falta_video": falta_video})
    orden = {e: i for i, e in enumerate(ESTADOS_ORDEN)}
    out.sort(key=lambda o: (orden.get(o["estado"], len(ESTADOS_ORDEN)), -(o["fila"] or 0)))
    return out, recientes


# ---- lo que no se hallo, anotado ----
def cargar(ruta=RUTA):
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("filas"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "filas": {}}


def guardar(datos, ruta=RUTA):
    escribir_atomico(ruta, json.dumps(datos, ensure_ascii=False, separators=(",", ":")))


def _reciente(memo, clave, hoy):
    e = (memo or {}).get(clave)
    try:
        return bool(e) and (date.fromisoformat(hoy) - date.fromisoformat(e["t"])).days < REINTENTO_DIAS
    except (KeyError, ValueError, TypeError):
        return False


def _nota(motivo, ahora, previo=None):
    """Lo que se anota en flujo.json cuando la busqueda no hallo: fecha, hora exacta (UTC), cuantas veces van y el motivo."""
    try:
        n = (int(previo.get("n") or 1) if isinstance(previo, dict) else 0) + 1
    except (TypeError, ValueError):
        n = 1
    return {"t": ahora.astimezone(BOGOTA).date().isoformat(), "h": ahora.astimezone(timezone.utc).isoformat(timespec="seconds"), "n": n, "motivo": motivo}


def _reciente_busqueda(memo, clave, hoy, ahora):
    """True si lo que la busqueda no hallo se intento hace menos de lo que toca esperar (ESPERAS_HORAS[n-1] horas). Lo anotado antes de existir la hora cuenta como un intento hecho a mediodia de su fecha."""
    e = (memo or {}).get(clave)
    if not isinstance(e, dict):
        return False
    try:
        if e.get("h"):
            cuando, n = datetime.fromisoformat(e["h"]), int(e.get("n") or 1)
        else:
            cuando, n = datetime.fromisoformat(e["t"] + "T12:00:00-05:00"), 1
        if cuando.tzinfo is None:
            cuando = cuando.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        return False
    espera = ESPERAS_HORAS[min(max(n, 1), len(ESPERAS_HORAS)) - 1]
    return (ahora - cuando) < timedelta(hours=espera)


def _viejo(e, hoy, dias):
    try:
        return (date.fromisoformat(hoy) - date.fromisoformat(e["t"])).days >= dias
    except (KeyError, ValueError, TypeError):
        return True


def _podar(memo, hoy, dias=60):
    """Saca de flujo.json lo anotado hace 60 dias o mas (o mal formado): una oferta que cambio de anuncios merece otro intento."""
    for k in [k for k, e in memo.items() if _viejo(e, hoy, dias)]:
        del memo[k]


# ---- la corrida ----
def indice_nombres(seen_pages):
    """{nombre sin tildes: [id de pagina]} de lo que el radar ya vio (state.json). Es el RESPALDO para hallar la pagina de una oferta cuyo anuncio ya se apago (la Biblioteca no devuelve
    la pagina de un anuncio apagado): el radar escribio en «Anunciante» el mismo nombre que guardo ahi, asi que sirve cuando el nombre es unico."""
    idx = {}
    for pid, e in (seen_pages or {}).items():
        n = sheets.fold(e.get("name")).strip() if isinstance(e, dict) else ""
        if n and re.fullmatch(r"\d{5,25}", str(pid)):
            idx.setdefault(n, []).append(str(pid))
    return idx


def _resolver_pagina(o, meta, pausa, de_anuncio, nombres):
    """El ID de la pagina de la fila, en este orden: el que ya tiene (celda o enlace); el de la pagina del anuncio en la Biblioteca (1 peticion, una sola vez por anuncio en la corrida);
    y, si el anuncio ya se apago y la Biblioteca no lo ubica, el del anunciante con ese mismo nombre que el radar ya vio (solo si es UNICO: con dos paginas del mismo nombre no se adivina)."""
    if o["pagina"]:
        return o["pagina"]
    if o["ad_id"]:
        if o["ad_id"] not in de_anuncio:
            pausa()
            de_anuncio[o["ad_id"]] = _id(meta.pagina_de_anuncio(o["ad_id"]))
        if de_anuncio[o["ad_id"]]:
            return de_anuncio[o["ad_id"]]
    cands = nombres.get(o["nombre"], []) if o["nombre"] else []
    return cands[0] if len(cands) == 1 else ""


def _es_la_fila(fila, h):
    """La fila que se leyo al empezar sigue siendo esa (alguien pudo insertar o borrar filas mientras se hablaba con Meta)."""
    if not fila:
        return False
    c = _celdas(fila)
    link = str(c.get("link del anuncio") or "")
    if h["ad_id"]:
        return ad_id_de(link) == h["ad_id"]
    return not ad_id_de(link) and page_id_de_enlace(link) == h["de_enlace"] and str(c.get("anunciante") or "").strip()[:60] == h["anunciante"]


def _escribir(hoja, hallados, cols, r):
    """Escribe SOLO en celdas que siguen vacias, tras volver a leer la planilla. Devuelve las claves que quedaron completas."""
    frescas = hoja.read_dicts("OFERTAS")
    por_fila = {f.get("_row"): f for f in frescas}
    cp, cv = sheets.fold(COLUMNAS[0]), sheets.fold(COLUMNAS[1])
    celdas, listas = {}, []
    for h in hallados:
        f = por_fila.get(h["fila"])
        if not _es_la_fila(f, h):
            f = next((x for x in frescas if _es_la_fila(x, h)), None)       # la fila se movio: se busca por el ID del anuncio
            if f is None:
                r["movidas"] += 1
                continue
        c = _celdas(f)
        if h["pagina_nueva"] and not str(c.get(cp) or "").strip():
            celdas[f"{sheets.col_letter(cols[0])}{f['_row']}"] = h["pagina_nueva"]
        if h["video_nuevo"] and not str(c.get(cv) or "").strip():
            celdas[f"{sheets.col_letter(cols[1])}{f['_row']}"] = h["video_nuevo"]
        if h["video_nuevo"]:
            listas.append(h["clave"])          # con video la fila quedo completa: ya no hay nada que recordar (sin video se conserva la nota de «sin_video»)
    if celdas:
        hoja.update_cells("OFERTAS", celdas)
    r["escritas"] = len(celdas)
    return listas


def correr(meta, hoja, filas, maximo, limitado, red, simular=False, ahora=None, pausa=None, ruta=None, log=print, nombres=None):
    """Completa los dos IDs de las filas de OFERTAS que no los tienen. `meta` = radar.Meta (pagina_de_anuncio, pagina_anunciante), `hoja` = sheets.Sheet, `filas` = hoja.read_dicts("OFERTAS"),
    `limitado` = la excepcion con la que Meta avisa que limito la IP, `red` = tupla de excepciones de red, `nombres` = indice_nombres(state.json["seen_pages"]) (respaldo). Devuelve (codigo, resumen): 0 bien, 1 algo fallo, 3 Meta limito la IP.
    Con simular=True hace las consultas a Meta pero solo muestra lo que escribiria: no toca la planilla ni flujo.json."""
    ahora = ahora or datetime.now(timezone.utc)
    hoy = ahora.astimezone(BOGOTA).date().isoformat()
    pausa = pausa or (lambda: None)
    ruta = ruta or RUTA                      # (se lee aqui y no como valor por defecto: asi una prueba puede apuntarla a una carpeta temporal)
    r = {"filas": len(filas), "porCompletar": 0, "recientes": 0, "intentadas": 0, "halladas": 0, "sinVideo": 0, "sinPagina": 0, "errores": 0, "escritas": 0, "movidas": 0, "limitado": False}
    cols = columnas_de(hoja.headers("OFERTAS"))
    if cols is None:
        log("!! A OFERTAS le faltan las columnas «ID de la página» e «ID del video»: corre una vez  radar.py --preparar-flujo")
        return 1, r
    memo = cargar(ruta)
    cand, r["recientes"] = candidatas(filas, memo["filas"], hoy)
    r["porCompletar"] = len(cand)
    hallados, por_pagina, de_anuncio, seguidos = [], {}, {}, 0
    for o in cand[:max(0, maximo)]:
        r["intentadas"] += 1
        try:
            pid = _resolver_pagina(o, meta, pausa, de_anuncio, nombres or {})
            if not pid:
                r["sinPagina"] += 1
                memo["filas"][o["clave"]] = {"t": hoy, "motivo": "sin_pagina"}
                seguidos = 0
                continue
            vid = ""
            if o["falta_video"]:
                if pid not in por_pagina:
                    pausa()
                    por_pagina[pid] = anuncios_de_pagina(meta, pid, pausa)
                res = por_pagina[pid]
                if res is None:
                    raise SinDatos("la Biblioteca no trajo resultados para la pagina " + pid)
                _p, vid, _ad = elegir_anuncio_flujo(res.get("ads"), o["ad_id"])
                if not vid:
                    r["sinVideo"] += 1
                    memo["filas"][o["clave"]] = {"t": hoy, "motivo": "sin_video"}
            seguidos = 0
            if vid:
                r["halladas"] += 1
            if (o["falta_pagina"] and pid) or vid:
                hallados.append({**o, "pagina_nueva": pid if o["falta_pagina"] else "", "video_nuevo": vid})
        except limitado as e:
            r["limitado"] = True
            log(f"   Meta limito la IP ({str(e)[:70]}): corto aqui y sigo en la proxima corrida.")
            break
        except (SinDatos,) + tuple(red) as e:
            r["errores"] += 1
            seguidos += 1
            log(f"   Fila {o['fila']} ({o['anunciante'][:28]}): {type(e).__name__}: {str(e)[:90]}")
            if seguidos >= RED_FALLOS_MAX:
                log("   Meta no contesta varias veces seguidas: corto aqui y sigo en la proxima corrida.")
                break
    for h in hallados:
        log(f"   {'[simulacion] escribiria' if simular else 'Hallado'} fila {h['fila']} ({h['anunciante'][:28]}): pagina {h['pagina_nueva'] or '(ya estaba)'} | video {h['video_nuevo'] or '(no hallado)'}")
    if hallados and not simular:
        try:
            for clave in _escribir(hoja, hallados, cols, r):
                memo["filas"].pop(clave, None)
        except Exception as e:                           # no se pudo escribir: nada queda anotado como hecho y la proxima corrida lo repite
            r["errores"] += 1
            log(f"!! No pude escribir los IDs en la planilla ({type(e).__name__}: {str(e)[:120]})")
    if not simular:
        _podar(memo["filas"], hoy)
        try:
            guardar(memo, ruta)
        except OSError as e:
            log(f"!! No pude guardar {Path(ruta).name} ({e}): se reintentara lo que no se hallo.")
    return (3 if r["limitado"] else 1 if r["errores"] else 0), r


# ---- la busqueda de la publicacion real (metodo del dueno, ver publicacion.py) ----
def col_origen(encabezados):
    """Indice de la columna «Origen del flujo», o None si falta (corre una vez  radar.py --preparar-flujo)."""
    f = [sheets.fold(h) for h in encabezados]
    return f.index(sheets.fold(COLUMNA_ORIGEN)) if sheets.fold(COLUMNA_ORIGEN) in f else None


def candidatas_busqueda(filas, memo, hoy, ahora=None):
    """(filas por buscar, cuantas se saltaron por haberse buscado hace poco). Entra toda fila de la que se pueda partir (ID del anuncio, enlace de la pagina o ID de la pagina escrito) y que NO tenga
    «google» ni «manual» en «Origen del flujo»: los IDs que dejo el radar antes de esta busqueda (el archivo de video del anuncio) no abren la publicacion y se reemplazan. Ordenadas como `candidatas`."""
    ahora = ahora or datetime.now(timezone.utc)
    out, recientes = [], 0
    co, cp, cv = sheets.fold(COLUMNA_ORIGEN), sheets.fold(COLUMNAS[0]), sheets.fold(COLUMNAS[1])
    for f in filas:
        c = _celdas(f)
        if sheets.fold(c.get(co)).strip() in ORIGENES_FIJOS:
            continue
        link = str(c.get("link del anuncio") or "")
        ad_id, de_enlace = ad_id_de(link), page_id_de_enlace(link)
        pagina = _id(str(c.get(cp) or "").strip()) or de_enlace
        if not (ad_id or pagina):
            continue
        clave = "b" + (ad_id or "p" + pagina)
        if _reciente_busqueda(memo, clave, hoy, ahora):
            recientes += 1
            continue
        out.append({"fila": f.get("_row"), "ad_id": ad_id, "de_enlace": de_enlace, "clave": clave, "anunciante": str(c.get("anunciante") or "").strip()[:60], "nombre": sheets.fold(c.get("anunciante")).strip(),
                    "estado": sheets.fold(c.get("status") or c.get("estado")).strip(), "pagina": pagina})
    orden = {e: i for i, e in enumerate(ESTADOS_ORDEN)}
    out.sort(key=lambda o: (orden.get(o["estado"], len(ESTADOS_ORDEN)), -(o["fila"] or 0)))
    return out, recientes


def _respaldar(antes, ruta):
    """Guarda en flujo_antes.json los valores que se van a reemplazar (para poder deshacer). Si no se puede guardar, no se escribe nada."""
    try:
        previo = json.loads(Path(ruta).read_text(encoding="utf-8"))
        previo = previo if isinstance(previo, list) else []
    except (OSError, ValueError):
        previo = []
    escribir_atomico(ruta, json.dumps(previo + antes, ensure_ascii=False, separators=(",", ":")))


def _escribir_busqueda(hoja, hallados, cols, c_origen, r, hoy, ruta_antes):
    """Reemplaza «ID de la página» e «ID del video» y marca «Origen del flujo» = google, tras volver a leer la planilla (si alguien la fijo a «manual» mientras tanto, no se toca) y tras
    respaldar lo que habia. Devuelve las claves que quedaron hechas."""
    frescas = hoja.read_dicts("OFERTAS")
    por_fila = {f.get("_row"): f for f in frescas}
    co, cp, cv = sheets.fold(COLUMNA_ORIGEN), sheets.fold(COLUMNAS[0]), sheets.fold(COLUMNAS[1])
    celdas, antes, hechas = {}, [], []
    for h in hallados:
        f = por_fila.get(h["fila"])
        if not _es_la_fila(f, h):
            f = next((x for x in frescas if _es_la_fila(x, h)), None)
            if f is None:
                r["movidas"] += 1
                continue
        c = _celdas(f)
        if sheets.fold(c.get(co)).strip() in ORIGENES_FIJOS:
            r["omitidas"] += 1
            continue
        antes.append({"fecha": hoy, "fila": f["_row"], "anuncio": h["ad_id"], "pagina": str(c.get(cp) or ""), "video": str(c.get(cv) or ""), "origen": str(c.get(co) or "")})
        celdas[f"{sheets.col_letter(cols[0])}{f['_row']}"] = h["pagina_nueva"]
        celdas[f"{sheets.col_letter(cols[1])}{f['_row']}"] = h["video_nuevo"]
        celdas[f"{sheets.col_letter(c_origen)}{f['_row']}"] = "google"
        hechas.append(h["clave"])
    if celdas:
        _respaldar(antes, ruta_antes)
        hoja.update_cells("OFERTAS", celdas)
    r["escritas"] = len(celdas)
    return hechas


def correr_busqueda(meta, hoja, filas, maximo, limitado, red, buscar, simular=False, ahora=None, pausa=None, ruta=None, ruta_antes=None, log=print, nombres=None):
    """Halla la PUBLICACION real de cada oferta (publicacion.py) y escribe su numero y el ID publico de la pagina, marcando la fila como «google». `buscar(consulta, pagina)` = busqueda en Google (publicacion.serper).
    Por fila: la Biblioteca (la pagina y sus anuncios: 1 o 2 peticiones) y 1 o 2 busquedas. Devuelve (codigo, resumen): 0 bien, 1 algo fallo, 3 Meta limito la IP. Con simular=True hace las consultas
    pero solo muestra lo que escribiria (las busquedas gastan creditos igual)."""
    ahora = ahora or datetime.now(timezone.utc)
    hoy = ahora.astimezone(BOGOTA).date().isoformat()
    pausa = pausa or (lambda: None)
    ruta, ruta_antes = ruta or RUTA, ruta_antes or RUTA_ANTES
    r = {"filas": len(filas), "porBuscar": 0, "recientes": 0, "intentadas": 0, "halladas": 0, "sinPublicacion": 0, "sinAnuncio": 0, "sinPagina": 0, "errores": 0, "escritas": 0, "movidas": 0, "omitidas": 0,
         "limitado": False, "busquedaCortada": False}
    encabezados = hoja.headers("OFERTAS")
    cols, c_origen = columnas_de(encabezados), col_origen(encabezados)
    if cols is None or c_origen is None:
        log("!! A OFERTAS le faltan columnas («ID de la página», «ID del video» y «Origen del flujo»): corre una vez  radar.py --preparar-flujo")
        return 1, r
    memo = cargar(ruta)
    cand, r["recientes"] = candidatas_busqueda(filas, memo["filas"], hoy, ahora)
    r["porBuscar"] = len(cand)
    hallados, por_pagina, de_anuncio, seguidos = [], {}, {}, 0
    for o in cand[:max(0, maximo)]:
        r["intentadas"] += 1
        try:
            pid = _resolver_pagina(o, meta, pausa, de_anuncio, nombres or {})
            if not pid:
                r["sinPagina"] += 1
                memo["filas"][o["clave"]] = _nota("sin_pagina", ahora, memo["filas"].get(o["clave"]))
                seguidos = 0
                continue
            if pid not in por_pagina:
                pausa()
                por_pagina[pid] = anuncios_de_pagina(meta, pid, pausa)
            res = por_pagina[pid]
            if res is None:
                raise SinDatos("la Biblioteca no trajo resultados para la pagina " + pid)
            ads = res.get("ads") or []
            # La publicacion de ESTA oferta: la del anuncio de su fila («Link del anuncio»), no la del angulo mas repetido de la pagina, que en un anunciante con varias ofertas
            # puede ser otra (2026-11-13: Info Cursos Brasil, filas 11 y 29, y Profª Karina Tarefinhas, filas 13 y 22, quedaron con la MISMA publicacion para dos ofertas distintas).
            # Sin anuncio en la fila (una fila pegada solo con la pagina) se sigue buscando por la pagina, como antes.
            propio = anuncio_propio(meta, pid, o["ad_id"], ads, pausa) if o["ad_id"] else None
            if o["ad_id"] and propio is None:
                r["sinAnuncio"] += 1
                memo["filas"][o["clave"]] = _nota("sin_anuncio", ahora, memo["filas"].get(o["clave"]))
                seguidos = 0
                log(f"   Sin anuncio: fila {o['fila']} ({o['anunciante'][:28]}): su anuncio {o['ad_id']} ya no esta activo en la Biblioteca; no se busca la publicacion de otra oferta de la pagina")
                continue
            base = ([propio] if propio else []) + ads
            pub, vanity = publicacion.datos_de_pagina(next((a.get("page_uri") for a in base if a.get("page_uri")), ""), pid)
            nombre = next((a.get("page_name") for a in base if a.get("page_name")), o["anunciante"])
            textos = publicacion.angulos([propio], lambda a: True) if propio else publicacion.angulos(ads, vende_por_whatsapp)
            if not textos:                   # sin texto no hay con que comparar: no se gastan busquedas de pago para un «sin publicacion» seguro
                r["sinPublicacion"] += 1
                memo["filas"][o["clave"]] = _nota("sin_publicacion", ahora, memo["filas"].get(o["clave"]))
                seguidos = 0
                log(f"   Sin texto: fila {o['fila']} ({o['anunciante'][:28]}): su anuncio no trae texto para buscar su publicacion")
                continue
            diag = {}
            hit = publicacion.hallar(buscar, pub, vanity, nombre, textos, diag=diag, ajenos=publicacion.textos_ajenos(ads, propio) if propio else None)
            seguidos = 0
            if hit:
                r["halladas"] += 1
                hallados.append({**o, "pagina_nueva": pub or pid, "video_nuevo": hit["numero"], "hit": hit})
            else:
                r["sinPublicacion"] += 1
                memo["filas"][o["clave"]] = _nota("sin_publicacion", ahora, memo["filas"].get(o["clave"]))
                log(f"   Sin publicacion: fila {o['fila']} ({o['anunciante'][:28]}), pagina {pub or pid}{' (' + vanity + ')' if vanity else ''}: {diag.get('resultados', 0)} resultados de Google, "
                    f"{diag.get('con_numero', 0)} con numero, {diag.get('de_la_pagina', 0)} de la pagina, mejor parecido {diag.get('mejor', 0):.2f}")
        except limitado as e:
            r["limitado"] = True
            log(f"   Meta limito la IP ({str(e)[:70]}): corto aqui y sigo en la proxima corrida.")
            break
        except (publicacion.SinCredito, publicacion.Limitada) as e:
            r["busquedaCortada"] = True
            r["errores"] += 1
            log(f"!! La busqueda en Google se corto: {str(e)[:120]}. Sigo en la proxima corrida.")
            break
        except (SinDatos, publicacion.BusquedaError) + tuple(red) as e:
            r["errores"] += 1
            seguidos += 1
            log(f"   Fila {o['fila']} ({o['anunciante'][:28]}): {type(e).__name__}: {str(e)[:90]}")
            if seguidos >= RED_FALLOS_MAX:
                log("   Fallos seguidos (Meta o la busqueda): corto aqui y sigo en la proxima corrida.")
                break
    for h in hallados:
        x = h["hit"]
        log(f"   {'[simulacion] escribiria' if simular else 'Hallada'} fila {h['fila']} ({h['anunciante'][:28]}): pagina {h['pagina_nueva']} | publicacion {h['video_nuevo']} "
            f"({x['tipo']}, parecido {x['puntaje']}, angulo {x['angulo'] + 1}) «{x['titulo'][:50]}»"
            + (" EMPATE PERDONADO (otra version del mismo anuncio): revisar a mano que sea la suya" if x.get("perdonado") else ""))
    if hallados and not simular:
        try:
            for clave in _escribir_busqueda(hoja, hallados, cols, c_origen, r, hoy, ruta_antes):
                memo["filas"].pop(clave, None)
        except Exception as e:                           # no se pudo respaldar o escribir: nada queda anotado como hecho y la proxima corrida lo repite
            r["errores"] += 1
            log(f"!! No pude escribir en la planilla ({type(e).__name__}: {str(e)[:120]})")
    if not simular:
        _podar(memo["filas"], hoy)
        try:
            guardar(memo, ruta)
        except OSError as e:
            log(f"!! No pude guardar {Path(ruta).name} ({e}): se reintentara lo que no se hallo.")
    return (3 if r["limitado"] else 1 if r["errores"] else 0), r
