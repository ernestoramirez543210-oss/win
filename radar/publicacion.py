"""
publicacion.py - Halla la PUBLICACION real de la pagina de un anunciante: el numero que abre el boton «Flujo de WhatsApp» del dash.

Metodo del dueno (2026-10-29, con capturas): (1) el ID PUBLICO de la pagina (el de `page_profile_uri` en la Biblioteca; en las paginas nuevas es 6157..., distinto del `page_id` del anuncio);
(2) lo busca en Google y salen las publicaciones reales de esa pagina; (3) abre la del ANGULO que mas se repite en la Biblioteca y toma el numero de su direccion (facebook.com/reel/<ID>);
(4) arma facebook.com/permalink.php?story_fbid=<ID de la publicacion>&id=<ID publico de la pagina>, que abre la publicacion con su boton de WhatsApp.
El numero que antes guardaba el radar (`xpv_asset_id`, el archivo de video DENTRO del anuncio) no es una publicacion de la pagina: Facebook contesta «Sorry, something went wrong» (verificado el 2026-10-29).

Aqui solo esta la parte sin red (sacar el numero de una direccion, elegir la publicacion que coincide con el angulo) y el cliente de la API de busqueda. Google cerro su API de busqueda a cuentas nuevas
(enero de 2026), asi que se usa Serper (resultados de Google, de pago por uso: 2.500 busquedas gratis al abrir la cuenta). La llave va en serper.key (o en la variable SERPER_API_KEY); Setup-ClaveBusqueda.ps1
la pone en el servidor sin pasarla por el chat. Nada de esto usa Gemini ni tokens de Claude.

Reglas: una publicacion solo se acepta si su titulo se parece al texto de uno de los anuncios mas repetidos de la pagina (UMBRAL) y si es de la pagina (su direccion lleva el ID publico o el nombre corto
de la pagina, o su titulo dice el nombre). La consulta es  site:facebook.com <ID publico> <palabras del anuncio>  (probado con Serper el 2026-10-29: halla facebook.com/<ID>/videos/<titulo>/<numero>).
Las direcciones `/posts/<texto>` y `/posts/pfbid...` no traen un numero y las envueltas en google.com/goto tampoco: se ignoran (el dash solo arma enlaces con digitos).
"""
import math
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

import sheets

HERE = Path(os.environ.get("RADAR_DATOS") or Path(__file__).resolve().parent)   # carpeta de los DATOS (en el VPS /opt/radar-de-ofertas; el codigo vive aparte)
SERPER_URL = "https://google.serper.dev/search"
ARCHIVO_LLAVE = HERE / "serper.key"
UMBRAL = 0.5            # parte de las palabras del titulo de la publicacion que tienen que estar en el texto del anuncio (con 0.4 se colaba un titulo que solo compartia dos palabras sueltas)
MIN_COMUNES = 2         # y al menos dos palabras en comun (un titulo de una sola palabra no basta)
MAX_ANGULOS = 3         # cuantos textos de anuncio se prueban, del mas repetido al menos
MIN_TITULO_COMPLETO = 4  # un empate con otro anuncio de la pagina solo se perdona si el anuncio propio explica el titulo entero y este tiene 4+ palabras (ver de_otra_oferta)
TIPOS = {"reel": 3, "video": 3, "post": 2, "foto": 1}   # a igual parecido gana el video/reel: es donde esta el boton de WhatsApp de un anuncio de video

VACIAS = {"para", "como", "esse", "essa", "esta", "este", "isso", "voce", "voces", "mais", "muito", "pelo", "pela", "seus", "suas", "nossa", "nosso", "nossas", "nossos", "tambem", "quando", "porque",
          "sobre", "entre", "desde", "hasta", "todo", "toda", "todos", "todas", "tiene", "tienes", "puedes", "pode", "podem", "quer", "aqui", "agora", "ahora", "facebook", "reels", "video", "videos"}


class BusquedaError(Exception):
    """La API de busqueda fallo (red, respuesta rara)."""


class SinCredito(BusquedaError):
    """La llave no sirve o no quedan busquedas: no tiene sentido seguir en esta corrida."""


class Limitada(BusquedaError):
    """La API pidio esperar (HTTP 429): se corta la corrida y sigue la proxima."""


def cargar_llave(ruta=None):
    """La llave de Serper (variable SERPER_API_KEY o archivo serper.key); '' si no hay o no tiene forma de llave. Nunca se imprime."""
    v = (os.environ.get("SERPER_API_KEY") or "").strip()
    if not v:
        try:
            v = Path(ruta or ARCHIVO_LLAVE).read_text(encoding="utf-8-sig").strip()
        except OSError:
            v = ""
    return v if re.fullmatch(r"[A-Za-z0-9_.-]{16,128}", v) else ""


# ---- la pagina ----
def datos_de_pagina(page_uri, page_id):
    """(ID publico de la pagina, nombre corto de su direccion). El ID publico sale de `page_profile_uri` (facebook.com/6157.../, /people/Nombre/6157.../, profile.php?id=..., /p/Nombre-6157...);
    si la direccion es un nombre corto (facebook.com/tienda) el ID es el `page_id` del anuncio y el nombre corto se devuelve aparte. ('', '') si no hay nada utilizable."""
    pid = str(page_id or "") if re.fullmatch(r"\d{5,25}", str(page_id or "")) else ""
    try:
        u = urlparse(str(page_uri or ""))
    except ValueError:
        return pid, ""
    if not re.search(r"(^|\.)facebook\.com$", (u.hostname or "").lower()):
        return pid, ""
    q = parse_qs(u.query).get("id", [""])[0]
    if re.fullmatch(r"\d{5,25}", q):
        return q, ""
    segs = [s for s in u.path.split("/") if s]
    for s in reversed(segs):
        m = re.search(r"(?:^|-)(\d{10,25})$", s)
        if m:
            return m.group(1), ""
    if segs and segs[0].lower() not in ("people", "pages", "profile.php", "p", "pg") and re.fullmatch(r"[A-Za-z0-9._-]{3,80}", segs[0]):
        return pid, segs[0]
    return pid, ""


def clave_texto(texto):
    """La clave para saber si dos textos de anuncio son EL MISMO: sus palabras, sin tildes, emojis, signos ni saltos de linea (los primeros 80 caracteres).
    La Biblioteca trae copias del mismo anuncio que solo cambian en los saltos de linea (2026-11-19, Loja Artesanato: «📐\\n\\nTenha» y «📐\\nTenha»)."""
    t = sheets.fold(texto)
    return " ".join(re.findall(r"[a-z0-9]+", t)) or t.strip()      # TODAS sus palabras; un texto sin letras latinas: su propio texto (si no, todos serian «el mismo»)


def mismo_texto(a, b):
    """Dos textos de anuncio son EL MISMO anuncio: las mismas palabras (aunque cambien los saltos de linea, los emojis o los signos), o casi: largos parecidos (el corto
    tiene el 80 % o mas de las palabras del largo) y el mismo comienzo en el 90 % o mas del corto. La Biblioteca trae copias del mismo anuncio que solo cambian al final
    (2026-11-19, Loja Artesanato: 60 de 66 palabras iguales desde el principio). Dos ofertas que solo comparten la entrada (una plantilla: «ULTIMAS VAGAS…», aunque
    pase de 80 letras) NO son el mismo: siguen contando como otro anuncio (revision de seguridad del 2026-11-19)."""
    if clave_texto(a) == clave_texto(b):
        return True
    # Lo que main juntaba (los mismos primeros 80 caracteres) sigue junto si comparten el 85 % de las palabras: versiones con «ULTIMAS 10 VAGAS» metido en medio o la
    # misma cabecera larga de emojis (revision de cambios, 2.a ronda: sin esto la primera pasada perdia publicaciones que main hallaba). Dos cursos con la misma
    # entrada larga comparten bastante menos (43 % en la prueba) y siguen aparte.
    if sheets.fold(str(a or "").strip())[:80] == sheets.fold(str(b or "").strip())[:80]:
        pa, pb = _palabras(a), _palabras(b)
        if pa and len(pa & pb) >= 0.85 * len(pa | pb):
            return True
    fa, fb = _fichas(a), _fichas(b)
    corto, largo = sorted((len(fa), len(fb)))
    if not corto or corto < 0.8 * largo:
        return False
    iguales = next((i for i, (x, y) in enumerate(zip(fa, fb)) if x != y), corto)
    return iguales >= math.ceil(0.9 * corto)


def angulos(ads, es_whatsapp):
    """Los textos de los anuncios activos de la pagina, del ANGULO MAS REPETIDO al menos (la Biblioteca junta los anuncios con el mismo contenido: `collation_count`). Primero los que mandan a WhatsApp
    (el boton es para llegar a su WhatsApp); solo si no hay ninguno, todos. Hasta MAX_ANGULOS textos distintos."""
    activos = [a for a in (ads or []) if isinstance(a, dict) and a.get("active", True)]
    base = [a for a in activos if es_whatsapp(a)] or activos
    base.sort(key=lambda a: (-int(a.get("collation_count") or 1), -len(str(a.get("text") or a.get("body") or ""))))
    vistos, out = set(), []
    for a in base:
        t = str(a.get("text") or a.get("body") or "").strip()
        k = sheets.fold(t)[:80]                    # como siempre (la busqueda por pagina no cambia; las copias del anuncio propio se juntan en textos_ajenos)
        if t and k not in vistos:
            vistos.add(k)
            out.append(t)
        if len(out) >= MAX_ANGULOS:
            break
    return out


# ---- el numero de una direccion de Facebook ----
_PATRONES = (
    ("reel", re.compile(r"/reel/(\d{8,25})")),
    ("video", re.compile(r"/videos/(?:[^/?#]+/)?(\d{8,25})")),
    ("video", re.compile(r"/watch/?\?(?:[^#]*&)?v=(\d{8,25})")),
    ("post", re.compile(r"/posts/(?:[^/?#]+/)?(\d{8,25})")),
    ("post", re.compile(r"[?&]story_fbid=(\d{8,25})")),
    ("foto", re.compile(r"[?&]fbid=(\d{8,25})")),
)


def publicacion_de_enlace(link):
    """(tipo, numero) de una direccion de Facebook que apunta a una publicacion con numero (reel, video, post, foto), o None. Solo facebook.com; nada mas de la direccion se conserva."""
    try:
        u = urlparse(str(link or ""))
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not re.search(r"(^|\.)facebook\.com$", (u.hostname or "").lower()):
        return None
    resto = u.path + ("?" + u.query if u.query else "")
    for tipo, pat in _PATRONES:
        m = pat.search(resto)
        if m:
            return tipo, m.group(1)
    return None


# ---- elegir la publicacion que coincide con el angulo ----
def _palabras(texto):
    t = sheets.fold(texto)
    t = re.sub(r"(?<=\d)[.,](?=\d{3}(?!\d))", "", t)                 # «1.000» y «1,000» son 1000
    return {p for p in re.findall(r"[a-z0-9]+", t) if p not in VACIAS and (len(p) >= 4 or (p.isdigit() and len(p) >= 2))}


def _palabras_titulo(resultado):
    """Las palabras con que se compara una publicacion: las de su TITULO (o tambien las de su descripcion si el titulo trae menos de 3)."""
    r = resultado or {}
    pal = _palabras(r.get("title"))
    if len(pal) < 3:
        pal |= _palabras(r.get("snippet"))
    return pal


def puntaje(resultado, texto_anuncio):
    """(parecido 0..1, palabras en comun): que parte de las palabras del TITULO de la publicacion (o de su descripcion si el titulo es muy corto) esta en el texto del anuncio."""
    pal = _palabras_titulo(resultado)
    if not pal:
        return 0.0, 0
    comunes = pal & _palabras(texto_anuncio)
    return len(comunes) / len(pal), len(comunes)


def _fichas(texto):
    """Las palabras de un texto EN ORDEN (todas, tambien las cortas; sin tildes, emojis, signos ni saltos de linea; «2.000» = 2000): para comparar como EMPIEZAN dos textos."""
    return re.findall(r"[a-z0-9]+", re.sub(r"(?<=\d)[.,](?=\d{3}(?!\d))", "", sheets.fold(texto)))


def comienzo_en_direccion(resultado):
    """El comienzo del texto de la publicacion que Facebook pone en su direccion (facebook.com/<pagina>/posts/<texto>/<numero> y /videos/<texto>/<numero>), en orden y SIN
    su ultima palabra, que suele venir cortada («…prontos-para-v»). [] si la direccion no trae texto (un /reel/<numero>, un /videos/<numero>)."""
    try:
        segs = [s for s in urlparse(str((resultado or {}).get("link") or "")).path.split("/") if s]
    except ValueError:
        return []
    for i, s in enumerate(segs[:-1]):
        if s.lower() in ("posts", "videos") and not segs[i + 1].isdigit():
            return _fichas(unquote(segs[i + 1]).replace("-", " "))[:-1]
    return []


def de_otra_oferta(resultado, texto_anuncio, ajenos, perdonar=False, en_su_direccion=False):
    """La publicacion puede ser de OTRA oferta de la pagina: algun texto ajeno explica tantas palabras de su titulo como el anuncio propio, o mas (con textos de plantilla,
    «Curso completo de X con certificado» / «… de Y …», el titulo de la otra oferta tambien pasa el umbral con el texto propio). Las copias del texto propio
    (`mismo_texto`) no cuentan.
    Con `perdonar` (solo la SEGUNDA pasada de hallar, cuando la regla de siempre no hallo nada; 2026-11-19) un empate se perdona si hay prueba EN ORDEN de que es la
    publicacion del anuncio propio: (1) el propio explica el titulo ENTERO (MIN_TITULO_COMPLETO palabras o mas); (2) la direccion lleva el ID publico o el nombre corto
    de la pagina (`en_su_direccion`: el nombre en el texto lo puede copiar una pagina clon); (3) el texto de la direccion (`comienzo_en_direccion`) va MAS ALLA del
    titulo y el texto propio EMPIEZA exactamente asi; y (4) el ajeno NO empieza asi. Entonces el ajeno es otra version del mismo anuncio (Loja Artesanato, 12 versiones
    de «+2.000 projetos de marcenaria»: Google traia la suya con parecido 1,0 y sus otras versiones empataban). Sin texto en la direccion, o si el ajeno empieza igual
    (dos ofertas con la misma frase de entrada: «…Aprenda a fazer» bolos / doces; revisiones del 2026-11-19), sigue tumbandola. La descripcion de Google no se usa: puede
    ser relleno («21 mil curtidas · 3 dias atrás»). Un empate con el titulo a MEDIAS nunca se perdona (Widom: su anuncio de ASB empataba con «Curso Tecnico em Seguranca
    do Trabalho», otro curso)."""
    pal = _palabras_titulo(resultado)
    propias = pal & _palabras(texto_anuncio)
    comienzo = []
    if perdonar and en_su_direccion and propias == pal and len(pal) >= MIN_TITULO_COMPLETO:
        c = comienzo_en_direccion(resultado)
        if len(c) > len(_fichas((resultado or {}).get("title"))) and _fichas(texto_anuncio)[:len(c)] == c:
            comienzo = c
    for x in ajenos or []:
        if mismo_texto(x, texto_anuncio):
            continue
        if len(pal & _palabras(x)) < len(propias):
            continue
        if comienzo and _fichas(x)[:len(comienzo)] != comienzo:
            continue
        return True
    return False


def _en_su_direccion(r, pub, vanity):
    """La direccion del resultado ES de la pagina: su PRIMER tramo es el ID publico o el nombre corto (facebook.com/<ID>/posts/..., tambien /pg/<nombre>/...), o es
    permalink.php / story.php con id=<ID>. «/<ID>/» mas adentro o en la consulta (?ref=/<ID>/) no cuenta (revision de seguridad, 2026-11-19)."""
    try:
        u = urlparse(str((r or {}).get("link") or ""))
    except ValueError:
        return False
    segs = [s for s in u.path.split("/") if s]
    if segs and segs[0].lower() == "pg":
        segs = segs[1:]
    if not segs:
        return False
    primero = segs[0].lower()
    if (pub and primero == pub) or (vanity and primero == vanity.lower()):
        return True
    return bool(pub) and primero in ("permalink.php", "story.php") and parse_qs(u.query).get("id", [""])[0] == pub


def _es_de_la_pagina(r, pub, vanity, nombre):
    """El resultado es de ESTA pagina: su direccion lleva el ID publico o el nombre corto de la pagina (facebook.com/<ID>/videos/...), o su titulo/texto dice el nombre de la pagina.
    Un reel suelto (facebook.com/reel/<numero>) sin ninguna de las dos cosas no se acepta: la busqueda puede traer reels de otras paginas que solo comparten palabras."""
    if _en_su_direccion(r, pub, vanity):
        return True
    n = sheets.fold(nombre).strip()
    return len(n) >= 4 and n in sheets.fold(" ".join(str(r.get(k) or "") for k in ("title", "snippet", "displayedLink")))


def palabras_clave(textos, n=2):
    """Las `n` palabras que mas se repiten entre los textos de los anuncios de la pagina (el tema de la oferta: «termocolantes», «adesivos»...), para acotar la busqueda. Solo palabras de 6 letras o mas."""
    cuenta, largo = {}, {}
    for t in textos or []:
        for p in {p for p in _palabras(t) if len(p) >= 6 and not p.isdigit()}:
            cuenta[p] = cuenta.get(p, 0) + 1
            largo[p] = len(p)
    return [p for p, _ in sorted(cuenta.items(), key=lambda kv: (-kv[1], -largo[kv[0]], kv[0]))[:max(0, n)]]


def consultas(pub, vanity, claves):
    """[(consulta, pagina de resultados)] que se prueban en orden y se paran al hallar la publicacion: primero la acotada con las palabras del anuncio, luego la general de la pagina (paginas 1 y 2).
    Es la busqueda del dueno en Google (el ID publico de la pagina), pero con site:facebook.com, que es lo que hace que salgan sus publicaciones (con solo el ID salen tiendas cualquiera)."""
    base = f"site:facebook.com/{vanity}" if vanity else (f"site:facebook.com {pub}" if pub else "")
    if not base:
        return []
    qs = [(base + " " + " ".join(claves), 1)] if claves else []
    return qs + [(base, 1), (base, 2)]


def textos_ajenos(ads, propio):
    """Los textos de los OTROS anuncios activos de la pagina (las demas ofertas del anunciante), sin las copias del anuncio propio (mismo texto, aunque cambien los saltos
    de linea, los emojis o el final: `mismo_texto`). Para `hallar(..., ajenos=)`."""
    suyo = str((propio or {}).get("text") or (propio or {}).get("body") or "").strip()
    out = []
    for a in ads or []:
        if not isinstance(a, dict) or not a.get("active", True):
            continue
        t = str(a.get("text") or a.get("body") or "").strip()
        if t and not mismo_texto(t, suyo) and not any(mismo_texto(t, x) for x in out):
            out.append(t)
    return out


def hallar(buscar, pub, vanity, nombre, textos, diag=None, ajenos=None):
    """La publicacion de la pagina cuyo titulo coincide con el angulo mas repetido (o con el siguiente, hasta MAX_ANGULOS), o None.
    `buscar(consulta, pagina)` devuelve la lista de resultados de Google (cada uno con title, link, snippet). Devuelve {numero, tipo, enlace, puntaje, titulo, consulta, angulo} (angulo = 0 si fue el mas repetido).
    Hasta 3 busquedas por oferta. Si `diag` es un dict, se llena con lo visto (resultados, con_numero, de_la_pagina, mejor parecido) para explicar por que no se hallo nada.
    Lanza BusquedaError (SinCredito, Limitada) si la API de busqueda falla.
    `ajenos` (busqueda POR ANUNCIO, 2026-11-13): los textos de los OTROS anuncios de la pagina. Una publicacion que se parece igual o mas a uno de ellos que al anuncio buscado,
    por palabras del titulo que el anuncio buscado no tiene, no se acepta (`de_otra_oferta`): con textos de plantilla («Curso completo de X con certificado» / «… de Y …»)
    el titulo de la otra oferta tambien pasa el umbral.
    DOS PASADAS con los MISMOS resultados de Google (2026-11-19, revision de cambios): primero la regla de siempre; si no halla nada y hay `ajenos`, una segunda pasada que
    perdona el empate con otra version del MISMO anuncio cuando hay prueba en orden (ver de_otra_oferta). La primera pasada es la regla de siempre (solo que las copias casi iguales del anuncio propio, `mismo_texto`, ya no empatan) y la segunda no
    gasta busquedas (la primera, sin hallazgo, ya hizo todas). Lo hallado en la segunda lleva "perdonado": True (y diag["perdonado"] = 1) para revisarlo a mano en los logs."""
    vistos = {}

    def buscar_una_vez(q, pagina):
        if (q, pagina) not in vistos:
            vistos[(q, pagina)] = buscar(q, pagina)
        return vistos[(q, pagina)]

    hit = _hallar(buscar_una_vez, pub, vanity, nombre, textos, diag, ajenos, perdonar=False)
    if hit is None and ajenos:
        hit = _hallar(buscar_una_vez, pub, vanity, nombre, textos, {}, ajenos, perdonar=True)
        if hit is not None:
            hit["perdonado"] = True
            if diag is not None:
                diag["perdonado"] = 1
    return hit


def _hallar(buscar, pub, vanity, nombre, textos, diag, ajenos, perdonar):
    """Una pasada de hallar() (ver alli)."""
    visto = diag if diag is not None else {}
    visto.update({"resultados": 0, "con_numero": 0, "de_la_pagina": 0, "mejor": 0.0})
    de_texto = None   # mejor publicacion que NO es video/reel (un post suele llevar el boton a la landing): solo vale si ningun video/reel coincide en las demas busquedas
    for q, pagina in consultas(pub, vanity, palabras_clave(textos)):
        cands = []
        for pos, r in enumerate(buscar(q, pagina) or []):
            visto["resultados"] += 1
            pu = publicacion_de_enlace(r.get("link")) if isinstance(r, dict) else None
            if pu:
                visto["con_numero"] += 1
            if pu and _es_de_la_pagina(r, pub, vanity, nombre):
                visto["de_la_pagina"] += 1
                cands.append((pos, pu, r))
        for i, texto in enumerate((textos or [])[:MAX_ANGULOS]):
            mejor = None
            for pos, (tipo, numero), r in cands:
                p, comunes = puntaje(r, texto)
                visto["mejor"] = max(visto["mejor"], round(p, 2))
                if p >= UMBRAL and comunes >= MIN_COMUNES:
                    if ajenos and de_otra_oferta(r, texto, ajenos, perdonar=perdonar, en_su_direccion=_en_su_direccion(r, pub, vanity)):
                        visto["de_otra_oferta"] = visto.get("de_otra_oferta", 0) + 1
                        continue
                    clave = (round(p, 3), TIPOS.get(tipo, 0), -pos)
                    if mejor is None or clave > mejor[0]:
                        mejor = (clave, {"numero": numero, "tipo": tipo, "enlace": str(r.get("link") or "")[:200], "puntaje": round(p, 2),
                                         "titulo": str(r.get("title") or "")[:90], "consulta": q, "angulo": i})
            if mejor:
                if mejor[1]["tipo"] in ("reel", "video"):
                    if de_texto is None or i <= de_texto["angulo"]:
                        return mejor[1]
                    break
                if de_texto is None:
                    de_texto = mejor[1]
                break
    return de_texto


# ---- la API de busqueda ----
def serper(consulta, llave, pagina=1, gl="br", hl="pt", timeout=30, post=None):
    """Una busqueda en Google por Serper: lista de resultados organicos [{title, link, snippet, ...}]. Como Brasil y en portugues (las ofertas del radar son de ahi; probado el 2026-10-29: con gl=co salia
    otro orden y a veces faltaba la publicacion). Algunos resultados vienen envueltos en google.com/goto?url=<token>: no se puede sacar la direccion de ahi y se ignoran.
    `post` solo lo cambian las pruebas. La llave nunca va en un mensaje de error."""
    post = post or requests.post
    cuerpo = {"q": consulta, "gl": gl, "hl": hl, "num": 10}
    if pagina and pagina > 1:
        cuerpo["page"] = int(pagina)
    r = post(SERPER_URL, headers={"X-API-KEY": llave, "Content-Type": "application/json"}, json=cuerpo, timeout=timeout)
    codigo = r.status_code
    if codigo in (401, 403):
        raise SinCredito(f"Serper dijo HTTP {codigo}: la llave no sirve o no tiene permiso")
    if codigo == 400 and "credit" in (r.text or "").lower():
        raise SinCredito("Serper dijo que no quedan busquedas (creditos)")
    if codigo == 429:
        raise Limitada("Serper pidio esperar (HTTP 429)")
    if codigo >= 400:
        raise BusquedaError(f"Serper dijo HTTP {codigo}")
    try:
        d = r.json()
    except ValueError:
        raise BusquedaError("Serper no devolvio JSON")
    organicos = d.get("organic") if isinstance(d, dict) else None
    return [x for x in (organicos or []) if isinstance(x, dict)]
