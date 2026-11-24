"""Pruebas SIN red de publicacion.py y de flujo.correr_busqueda (la busqueda de la publicacion real para el «Flujo de WhatsApp»).
Google (Serper), Meta y la planilla estan simulados. Corre con:  python -X utf8 test_publicacion.py"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import flujo
import publicacion
import radar
import sheets

bien = 0


def ok(cond, msg):
    global bien
    assert cond, msg
    bien += 1


def igual(a, b, msg):
    ok(a == b, f"{msg}: obtuve {a!r} y esperaba {b!r}")


# ---------------------------------------------------------------- la pagina: ID publico y nombre corto
PUB, VIEJO = "61579375388957", "999900000468472"
igual(publicacion.datos_de_pagina(f"https://www.facebook.com/{PUB}/", VIEJO), (PUB, ""), "pagina nueva: el ID publico sale de la direccion, no del page_id del anuncio")
igual(publicacion.datos_de_pagina(f"https://www.facebook.com/people/Pinturas-da-Rosa/{PUB}/", VIEJO), (PUB, ""), "formato /people/Nombre/ID/")
igual(publicacion.datos_de_pagina(f"https://www.facebook.com/profile.php?id={PUB}", VIEJO), (PUB, ""), "formato profile.php?id=")
igual(publicacion.datos_de_pagina(f"https://www.facebook.com/p/Pinturas-da-Rosa-{PUB}/", VIEJO), (PUB, ""), "formato /p/Nombre-ID/")
igual(publicacion.datos_de_pagina("https://www.facebook.com/liacrochet/", "9999000000335632"), ("9999000000335632", "liacrochet"), "pagina con nombre corto: ID = page_id y el nombre corto aparte")
igual(publicacion.datos_de_pagina("", "999900000502345"), ("999900000502345", ""), "sin direccion: el page_id")
igual(publicacion.datos_de_pagina("https://evil.example/" + PUB + "/", VIEJO), (VIEJO, ""), "una direccion que no es de Facebook no se usa")
igual(publicacion.datos_de_pagina("https://www.facebook.com.evil.example/" + PUB + "/", VIEJO), (VIEJO, ""), "ni un dominio que solo empieza como Facebook")
igual(publicacion.datos_de_pagina("", ""), ("", ""), "sin nada: nada")
igual(publicacion.datos_de_pagina(None, "abc"), ("", ""), "un page_id que no son digitos no vale")

# ---------------------------------------------------------------- el numero de una direccion de Facebook
for enlace, esperado in [
    ("https://www.facebook.com/reel/9999000000472319", ("reel", "9999000000472319")),
    ("https://www.facebook.com/reel/9999000000472319/?mibextid=abc", ("reel", "9999000000472319")),
    (f"https://www.facebook.com/{PUB}/videos/9999000000595943/", ("video", "9999000000595943")),
    (f"https://www.facebook.com/lia/videos/mi-titulo-de-video/9999000000595943/", ("video", "9999000000595943")),
    ("https://www.facebook.com/watch/?v=9999000000595943", ("video", "9999000000595943")),
    ("https://www.facebook.com/watch?ref=x&v=9999000000595943", ("video", "9999000000595943")),
    ("https://m.facebook.com/lia/posts/9999000000595943", ("post", "9999000000595943")),
    ("https://www.facebook.com/permalink.php?story_fbid=9999000000472319&id=" + PUB, ("post", "9999000000472319")),
    ("https://www.facebook.com/photo.php?fbid=9999000000595943&set=a.1", ("foto", "9999000000595943")),
    (f"https://www.facebook.com/{PUB}/posts/pfbid02abcdefghijklmnopqrstuvwxyz1234567890", None),
    (f"https://www.facebook.com/{PUB}/posts/praticidade-total-para-o-seu-atelie", None),
    (f"https://www.facebook.com/{PUB}/posts/ensinar-s%C3%B3lidos-geom%C3%A9tricos-ficou-muito/999900000000608760/", ("post", "999900000000608760")),
    ("https://www.facebook.com/people/Pinturas-da-Rosa/" + PUB + "/", None),
    ("https://www.facebook.com.evil.example/reel/9999000000472319", None),
    ("https://evil.example/reel/9999000000472319", None),
    ("javascript:alert(1)//facebook.com/reel/9999000000472319", None),
    ("https://www.facebook.com/reel/123", None),                  # un numero demasiado corto no es una publicacion
    ("", None), (None, None),
]:
    igual(publicacion.publicacion_de_enlace(enlace), esperado, f"numero de {enlace!r}")

# ---------------------------------------------------------------- parecido entre el titulo de la publicacion y el texto del anuncio
TEXTO_TOP = ("Praticidade total para o seu ateliê! Conheça o pacote com 1.000 adesivos termocolantes para panos de prato. É só posicionar o ferro de passar e pronto: "
             "peças lindas aplicadas em segundos, sem precisar costurar cada detalhe.")
TEXTO_NATAL = "Aumente seu faturamento nas festas de fim de ano. Conheça o pacote com 1.000 adesivos termocolantes de Natal e transforme peças simples em presentes."
TEXTO_FLORAL = "Quer dar vida às suas criações? Adesivos florais lindos para panos de prato, com cores vivas."


def res(titulo, numero, snippet="", tipo="video", extra=None):
    """Un resultado de Google. `video` = facebook.com/<ID publico>/videos/<titulo>/<numero> (la forma que trae Serper para las publicaciones de la pagina); `reel` = facebook.com/reel/<numero> (sin la pagina)."""
    link = f"https://www.facebook.com/reel/{numero}/" if tipo == "reel" else f"https://www.facebook.com/{PUB}/videos/titulo-de-la-publicacion/{numero}/"
    d = {"title": titulo, "link": link, "snippet": snippet}
    d.update(extra or {})
    return d


R_TOP = res("1.000 Termocolantes para Panos de Prato", "9999000000472319", "Economize tempo e encante seus clientes com 1.000 moldes e adesivos termocolantes exclusivos.")
R_FLORAL = res("Essas artes de adesivos estão na nossa coleção Floral!", "9999000000611111", "Video Transcript: E hoje eu decidi estar trazendo pra vocês esse floral maravilhoso")
R_GOSTOU = res("Gostou? Me conta aquiii | Pinturas da Rosa", "9999000000622222", "This video showcases the artistic process of creating custom paper tumblers.")
R_PAGINA = {"title": "Pinturas da Rosa - Facebook", "link": f"https://www.facebook.com/people/Pinturas-da-Rosa/{PUB}/", "snippet": "5,7 mil seguidores"}
p, n = publicacion.puntaje(R_TOP, TEXTO_TOP)
ok(p >= 0.99 and n >= 4, f"el titulo de la publicacion del angulo principal coincide casi por completo (1.000 = 1000): {p} {n}")
p2, n2 = publicacion.puntaje(R_FLORAL, TEXTO_TOP)
ok(p2 < publicacion.UMBRAL, f"una publicacion de otro tema no pasa el umbral: {p2} {n2}")
igual(publicacion.puntaje({}, TEXTO_TOP), (0.0, 0), "un resultado vacio no coincide con nada")
igual(publicacion.puntaje(R_TOP, ""), (0.0, 0), "un anuncio sin texto tampoco")
p3, n3 = publicacion.puntaje({"title": "Panos", "snippet": ""}, TEXTO_TOP)
ok(n3 < publicacion.MIN_COMUNES, "un titulo de una sola palabra no basta (se pide un minimo de palabras en comun)")

# ---------------------------------------------------------------- angulos: el mas repetido primero, WhatsApp primero
def ad(archive, texto, cuenta=1, cta="WHATSAPP_MESSAGE", activo=True, link="https://api.whatsapp.com/send", **extra):
    d = {"archive_id": str(archive), "page_id": VIEJO, "text": texto, "body": texto, "collation_count": cuenta, "cta_type": cta, "link": link, "active": activo, "sig": "s" + str(archive),
         "page_uri": f"https://www.facebook.com/{PUB}/", "page_name": "Pinturas da Rosa", "start": 1000, "video_id": "999"}
    d.update(extra)
    return d


ADS = [ad(1, TEXTO_NATAL, 2), ad(2, TEXTO_TOP, 6), ad(3, TEXTO_FLORAL, 9, cta="LEARN_MORE", link="https://tienda.example/p"), ad(4, "texto de un anuncio apagado", 20, activo=False)]
igual(publicacion.angulos(ADS, flujo.vende_por_whatsapp), [TEXTO_TOP, TEXTO_NATAL], "los anuncios de WhatsApp primero y el mas repetido antes (el de 9 que no es de WhatsApp y el apagado no cuentan)")
igual(publicacion.angulos([ad(3, TEXTO_FLORAL, 9, cta="LEARN_MORE", link="https://tienda.example/p")], flujo.vende_por_whatsapp), [TEXTO_FLORAL], "sin anuncios de WhatsApp se usan todos los activos")
igual(publicacion.angulos(None, flujo.vende_por_whatsapp), [], "sin anuncios: nada")
igual(len(publicacion.angulos([ad(i, f"angulo numero {i} distinto del resto en todo", i) for i in range(1, 9)], flujo.vende_por_whatsapp)), publicacion.MAX_ANGULOS, "como mucho MAX_ANGULOS textos")
igual(publicacion.angulos([ad(1, TEXTO_TOP, 3), ad(2, TEXTO_TOP, 5)], flujo.vende_por_whatsapp), [TEXTO_TOP], "el mismo texto no se repite")


# ---------------------------------------------------------------- hallar: el metodo del dueno con los resultados de su captura
def buscador(resultados, llamadas=None):
    """Un `buscar(consulta, pagina)` falso. `resultados` = una lista (la misma para toda consulta) o un dict {comienzo de la consulta: lista}."""
    def buscar(q, pagina=1):
        if llamadas is not None:
            llamadas.append((q, pagina))
        if isinstance(resultados, dict):
            return next((v for k, v in resultados.items() if q.startswith(k)), [])
        return resultados
    return buscar


# palabras_clave y consultas: la busqueda acotada primero, luego la general (paginas 1 y 2)
igual(publicacion.palabras_clave([TEXTO_TOP, TEXTO_NATAL]), ["termocolantes", "adesivos"], "las palabras que mas se repiten entre los anuncios (de 6 letras o mas, sin numeros) son el tema de la oferta")
igual(publicacion.palabras_clave([]), [], "sin anuncios: sin palabras")
igual(publicacion.palabras_clave([TEXTO_TOP], 0), [], "n=0: ninguna")
igual(publicacion.consultas(PUB, "", ["termocolantes", "adesivos"]), [(f"site:facebook.com {PUB} termocolantes adesivos", 1), (f"site:facebook.com {PUB}", 1), (f"site:facebook.com {PUB}", 2)], "con ID publico: la acotada, la general y la pagina 2")
igual(publicacion.consultas(PUB, "", []), [(f"site:facebook.com {PUB}", 1), (f"site:facebook.com {PUB}", 2)], "sin palabras: solo las generales")
igual(publicacion.consultas("999900000502345", "lia", ["x"])[0], ("site:facebook.com/lia x", 1), "con nombre corto se busca por el nombre corto")
igual(publicacion.consultas("", "", ["x"]), [], "sin ID ni nombre corto: sin consultas")
QA = publicacion.consultas(PUB, "", publicacion.palabras_clave(publicacion.angulos(ADS, flujo.vende_por_whatsapp)))[0][0]

llam = []
hit = publicacion.hallar(buscador([R_PAGINA, R_FLORAL, R_TOP, R_GOSTOU], llam), PUB, "", "Pinturas da Rosa", publicacion.angulos(ADS, flujo.vende_por_whatsapp))
igual((hit or {}).get("numero"), "9999000000472319", "halla la publicacion del angulo mas repetido (la de «1.000 termocolantes»), no la floral ni la de «gostou»")
igual([(hit or {}).get("tipo"), (hit or {}).get("angulo"), (hit or {}).get("consulta")], ["video", 0, QA], "es un video, del angulo 1 y salio de la busqueda acotada con las palabras del anuncio")
igual(llam, [(QA, 1)], "se hace UNA sola busqueda y se para al hallarla")
llam4 = []
publicacion.hallar(buscador([], llam4), PUB, "", "Pinturas da Rosa", [TEXTO_TOP])
igual([p for _, p in llam4], [1, 1, 2], "sin resultados se prueban las tres busquedas (acotada, general, pagina 2) y no mas")
igual(publicacion.hallar(buscador([R_FLORAL, R_GOSTOU, R_PAGINA]), PUB, "", "Pinturas da Rosa", [TEXTO_TOP]), None, "si ninguna publicacion coincide con el angulo no se inventa una")
igual(publicacion.hallar(buscador([]), PUB, "", "Pinturas da Rosa", [TEXTO_TOP]), None, "una busqueda sin resultados: nada")
dg = {}
publicacion.hallar(buscador([R_FLORAL, R_GOSTOU, R_PAGINA, {"title": "x", "link": "https://tienda.example/a"}]), PUB, "", "Pinturas da Rosa", [TEXTO_TOP], diag=dg)
igual([dg["resultados"], dg["con_numero"], dg["de_la_pagina"]], [12, 6, 6], "el diagnostico cuenta resultados (4 por consulta x 3 consultas), los que traen numero y los de la pagina")
ok(0 < dg["mejor"] < publicacion.UMBRAL, f"y el mejor parecido que se vio, que explica por que no se hallo nada: {dg['mejor']}")
igual(publicacion.hallar(buscador([R_TOP]), PUB, "", "Pinturas da Rosa", []), None, "sin texto de anuncios no hay con que comparar: nada")
# un reel suelto (facebook.com/reel/<numero>) sin la pagina en la direccion ni su nombre en el texto no se acepta: puede ser de otra pagina
reel_suelto = res("1.000 Termocolantes para Panos de Prato", "9999000000638888", tipo="reel")
igual(publicacion.hallar(buscador([reel_suelto]), PUB, "", "Pinturas da Rosa", [TEXTO_TOP]), None, "un reel sin evidencia de que es de la pagina no se acepta")
reel_con_nombre = res("1.000 Termocolantes para Panos de Prato | Pinturas da Rosa", "9999000000638888", tipo="reel")
igual((publicacion.hallar(buscador([reel_con_nombre]), PUB, "", "Pinturas da Rosa", [TEXTO_TOP]) or {}).get("numero"), "9999000000638888", "pero si el titulo dice el nombre de la pagina, si")
h2 = publicacion.hallar(buscador([R_FLORAL, R_TOP]), PUB, "", "Pinturas da Rosa", ["Aprenda crochet y amigurumi paso a paso para principiantes", TEXTO_NATAL])
igual((h2 or {}).get("numero"), "9999000000472319", "si el angulo principal no esta, se prueba el siguiente (el de Natal coincide con «1.000 ... termocolantes»)")
igual((h2 or {}).get("angulo"), 1, "y se anota que fue el segundo angulo")
# a igual parecido gana el video sobre el post con numero
post_num = {"title": "1.000 Termocolantes para Panos de Prato", "link": f"https://www.facebook.com/{PUB}/posts/9999000000573333", "snippet": ""}
igual(publicacion.hallar(buscador([post_num, R_TOP]), PUB, "", "Pinturas da Rosa", [TEXTO_TOP])["numero"], "9999000000472319", "a igual parecido gana el video sobre el post con numero (aunque el post salga primero)")
# un post normal no basta si mas abajo (otra busqueda) hay un video/reel igual de parecido: el boton de WhatsApp esta en el video
llam3 = []
h4 = publicacion.hallar(buscador({"site:facebook.com 6": [post_num]}, llam3), PUB, "", "Pinturas da Rosa", [TEXTO_TOP])
igual((h4 or {}).get("numero"), "9999000000573333", "si solo hay un post, se usa el post")
igual(len(llam3), 3, "pero antes se prueban las otras busquedas por si hay un video")
class Dos:
    def __init__(self): self.n = 0
    def __call__(self, q, pagina=1):
        self.n += 1
        return [post_num] if self.n == 1 else ([R_TOP] if self.n == 3 else [])
igual((publicacion.hallar(Dos(), PUB, "", "Pinturas da Rosa", [TEXTO_TOP]) or {}).get("numero"), "9999000000472319", "el video hallado en la tercera busqueda gana al post de la primera")
# una publicacion con pfbid o con texto en vez de numero no se puede usar
pf = {"title": "1.000 Termocolantes para Panos de Prato", "link": f"https://www.facebook.com/{PUB}/posts/pfbid02abcdefghijklmnopqrstuvwxyz1234567890", "snippet": ""}
igual(publicacion.hallar(buscador([pf]), PUB, "", "Pinturas da Rosa", [TEXTO_TOP]), None, "una publicacion /posts/pfbid... no trae numero: se ignora")
# pagina con nombre corto: solo cuenta lo que el enlace o el texto atribuyen a esa pagina
ajena = {"title": "1.000 Termocolantes para Panos de Prato", "link": "https://www.facebook.com/otrapagina/videos/titulo/9999000000644444/", "snippet": "texto de otra pagina cualquiera"}
propia = {"title": "1.000 Termocolantes para Panos de Prato", "link": "https://www.facebook.com/pinturasdasueli/videos/9999000000655555/", "snippet": ""}
llam2 = []
h3 = publicacion.hallar(buscador([ajena, propia], llam2), "999900000502345", "pinturasdasueli", "Pinturas da Rosa", [TEXTO_TOP])
igual((h3 or {}).get("numero"), "9999000000655555", "con nombre corto solo cuenta lo que el enlace o el texto atribuyen a esa pagina (la publicacion «ajena» se descarta)")
ok(len(llam2) == 1 and llam2[0][0].startswith("site:facebook.com/pinturasdasueli "), "se busca por el nombre corto y se para en la primera busqueda que halla")
nombre_en_texto = res("1.000 Termocolantes para Panos de Prato | Pinturas da Rosa", "9999000000666666")
h4 = publicacion.hallar(buscador([nombre_en_texto]), "", "pinturasdasueli", "Pinturas da Rosa", [TEXTO_TOP])
igual((h4 or {}).get("numero"), "9999000000666666", "tambien vale que el titulo lleve el nombre de la pagina")
llam3 = []
publicacion.hallar(buscador([], llam3), "999900000502345", "pinturasdasueli", "X", [TEXTO_TOP])
ok(all(q.startswith("site:facebook.com/pinturasdasueli") for q, _ in llam3) and [p for _, p in llam3] == [1, 1, 2], "con nombre corto las tres busquedas son por el nombre corto")
for malo in ({"link": None}, {"title": None, "link": "https://www.facebook.com/reel/9999000000472319"}, "no es un dict", None):
    publicacion.hallar(buscador([malo]), PUB, "", "X", [TEXTO_TOP])
    ok(True, f"un resultado raro no rompe la busqueda: {malo!r}")

# Lo que devolvio Serper de verdad el 2026-10-29 para «site:facebook.com 61579375388957 termocolantes» (gl=br, hl=pt): 4 resultados directos de facebook.com y 6 envueltos en google.com/goto
GOTO = "https://www.google.com/goto?url=CAESogEB6zswFYK57twK4rCZ1uIJr0oP0-uVSZF_HU8J4cF6"
REAL = [
    {"title": "Lucre no Natal com Termocolantes", "link": f"https://www.facebook.com/{PUB}/videos/lucre-no-natal-com-termocolantes/9999000000670590/", "snippet": "O Natal está chegando e suas clientes vão amar essa novidade!", "position": 1},
    {"title": "Lucre no Natal com Termocolantes", "link": f"https://www.facebook.com/{PUB}/videos/lucre-no-natal-com-termocolantes/9999000000685796/", "snippet": "O Natal está chegando", "position": 2},
    {"title": "Lucre no Natal com Termocolantes", "link": f"https://www.facebook.com/{PUB}/videos/lucre-no-natal-com-termocolantes/999900000691470/", "snippet": "O Natal está chegando", "position": 3},
    {"title": "1.000 Termocolantes para Panos de Prato", "link": f"https://www.facebook.com/{PUB}/videos/1000-termocolantes-para-panos-de-prato/9999000000472319/", "snippet": "Economize tempo e encante seus clientes com 1.000 moldes e adesivos termocolantes exclusivos.", "position": 4},
    {"title": "Aplique com o Ferro: 1.000 Termocolantes", "link": GOTO, "snippet": "x", "position": 5},
    {"title": "Lucre no Natal com Termocolantes | Pinturas da Rosa | Facebook", "link": GOTO, "snippet": "x", "position": 6},
    {"title": "O Natal está chegando e suas clientes vão amar e", "link": GOTO, "snippet": "x", "position": 7},
    {"title": "abe aquele toque de carinho e delicadeza que transforma", "link": GOTO, "snippet": "x", "position": 8},
]
h5 = publicacion.hallar(buscador(REAL), PUB, "", "Pinturas da Rosa", publicacion.angulos(ADS, flujo.vende_por_whatsapp))
igual([(h5 or {}).get("numero"), (h5 or {}).get("tipo"), (h5 or {}).get("angulo")], ["9999000000472319", "video", 0], "con los resultados REALES de Serper sale la publicacion que escogio el dueno (9999000000472319), no las de Natal ni las envueltas en google.com/goto")
igual(publicacion.publicacion_de_enlace(GOTO), None, "un enlace envuelto en google.com/goto no es una publicacion")


# ---------------------------------------------------------------- la API de busqueda (Serper), con respuestas simuladas
class Resp:
    def __init__(self, codigo, cuerpo=None, texto=""):
        self.status_code, self._cuerpo, self.text = codigo, cuerpo, texto

    def json(self):
        if self._cuerpo is None:
            raise ValueError("no es JSON")
        return self._cuerpo


class Post:
    def __init__(self, resp):
        self.resp, self.llamadas = resp, []

    def __call__(self, url, **kw):
        self.llamadas.append((url, kw))
        return self.resp


LLAVE = "abcdef0123456789ABCDEF"
post = Post(Resp(200, {"organic": [{"title": "a", "link": "https://x"}, "basura", {"title": "b"}]}))
igual(publicacion.serper(PUB, LLAVE, post=post), [{"title": "a", "link": "https://x"}, {"title": "b"}], "devuelve solo los resultados que son diccionarios")
url, kw = post.llamadas[0]
igual([url, kw["headers"]["X-API-KEY"], kw["json"]["q"], kw["timeout"]], [publicacion.SERPER_URL, LLAVE, PUB, 30], "la llave va en la cabecera, la consulta en el cuerpo y hay un limite de tiempo")
ok(LLAVE not in url and LLAVE not in json.dumps(kw["json"]), "la llave no va en la direccion ni en el cuerpo")
igual([kw["json"]["gl"], kw["json"]["hl"], "page" in kw["json"]], ["br", "pt", False], "se busca como Brasil y en portugues, y la pagina 1 no manda «page»")
post2 = Post(Resp(200, {"organic": []}))
publicacion.serper(PUB, LLAVE, pagina=2, post=post2)
igual(post2.llamadas[0][1]["json"].get("page"), 2, "la pagina 2 de resultados manda page=2")
igual(publicacion.serper(PUB, LLAVE, post=Post(Resp(200, {}))), [], "una respuesta sin «organic» es una lista vacia")
for codigo, texto, clase in [(401, "", publicacion.SinCredito), (403, "", publicacion.SinCredito), (400, "Not enough credits", publicacion.SinCredito), (429, "", publicacion.Limitada),
                             (400, "otra cosa", publicacion.BusquedaError), (500, "", publicacion.BusquedaError)]:
    try:
        publicacion.serper(PUB, LLAVE, post=Post(Resp(codigo, {}, texto)))
        ok(False, f"HTTP {codigo} debia fallar")
    except publicacion.BusquedaError as e:
        ok(type(e) is clase, f"HTTP {codigo} {texto!r} debia ser {clase.__name__} y fue {type(e).__name__}")
        ok(LLAVE not in str(e), "el mensaje de error no lleva la llave")
try:
    publicacion.serper(PUB, LLAVE, post=Post(Resp(200, None)))
    ok(False, "una respuesta que no es JSON debia fallar")
except publicacion.BusquedaError as e:
    ok("JSON" in str(e), "una respuesta que no es JSON se avisa")

with tempfile.TemporaryDirectory() as tmp:
    llave_ruta = Path(tmp) / "serper.key"
    anterior = os.environ.pop("SERPER_API_KEY", None)
    try:
        igual(publicacion.cargar_llave(llave_ruta), "", "sin archivo ni variable: sin llave")
        llave_ruta.write_text("﻿" + LLAVE + "\r\n", encoding="utf-8")
        igual(publicacion.cargar_llave(llave_ruta), LLAVE, "el archivo con marca BOM y salto de linea se lee limpio")
        llave_ruta.write_text("esto no es una llave porque tiene espacios", encoding="utf-8")
        igual(publicacion.cargar_llave(llave_ruta), "", "un contenido que no parece una llave no se usa")
        os.environ["SERPER_API_KEY"] = "  " + LLAVE + " "
        igual(publicacion.cargar_llave(llave_ruta), LLAVE, "la variable de entorno gana y se recorta")
    finally:
        os.environ.pop("SERPER_API_KEY", None)
        if anterior is not None:
            os.environ["SERPER_API_KEY"] = anterior


# ---------------------------------------------------------------- correr_busqueda: la planilla y Meta simulados
CAB = ["Fecha", "Palabra clave", "Anunciante", "Link del anuncio", "Link de la página", "Status", "ID de la página", "ID del video", "Origen del flujo"]


def a1(texto):
    letras = "".join(c for c in texto if c.isalpha())
    col = 0
    for c in letras:
        col = col * 26 + (ord(c.upper()) - 64)
    return col - 1, int("".join(c for c in texto if c.isdigit()))


def fila(anunciante, ad_id, pagina="", video="", origen="", status="nuevo"):
    return {"Fecha": "21/10/2026", "Palabra clave": "x", "Anunciante": anunciante, "Link del anuncio": f"https://www.facebook.com/ads/library/?id={ad_id}", "Link de la página": "no visible",
            "Status": status, "ID de la página": pagina, "ID del video": video, "Origen del flujo": origen}


class Hoja:
    def __init__(self, filas, cabecera=CAB, antes_de_releer=None):
        self.cabecera, self.filas, self.escrituras, self.lecturas, self.antes_de_releer = list(cabecera), [dict(f) for f in filas], [], 0, antes_de_releer

    def headers(self, tab):
        return list(self.cabecera)

    def read_dicts(self, tab):
        self.lecturas += 1
        if self.lecturas == 2 and self.antes_de_releer:
            self.antes_de_releer(self)
        return [dict(f, _row=i + 2) for i, f in enumerate(self.filas)]

    def update_cells(self, tab, cells):
        self.escrituras.append(dict(cells))
        for celda, v in cells.items():
            col, nfila = a1(celda)
            self.filas[nfila - 2][self.cabecera[col]] = v


class Meta:
    def __init__(self, paginas, de_anuncio=None, falla=None):
        self.paginas, self.de_anuncio, self.falla, self.llamadas = paginas, de_anuncio or {}, falla or {}, []

    def pagina_de_anuncio(self, ad_id):
        self.llamadas.append(("anuncio", ad_id))
        return self.de_anuncio.get(ad_id, "")

    def pagina_anunciante(self, pid, country="BR", antes_de=None):
        self.llamadas.append(("pagina", pid))
        if pid in self.falla:
            raise self.falla[pid]
        lista = self.paginas.get(pid)
        return None if lista is None else {"total": len(lista), "ads": lista, "hay_mas": False}


AHORA = datetime(2026, 10, 29, 16, 0, tzinfo=timezone.utc)
RED = (requests.RequestException,)
# tres paginas: SUELI (hay publicacion), SINPUB (Google no la tiene), OTRA (sin ninguna coincidencia)
SUELI, SINPUB, OTRA = VIEJO, "999900000382222", "999900000393333"
# El anuncio de cada fila («Link del anuncio») es uno de los anuncios de su pagina, como en la planilla real (2026-11-13: la busqueda usa SOLO el texto de ese anuncio).
# Las filas de Pinturas da Rosa son copias de su anuncio de los termocolantes (el angulo de la captura del dueno).
SUELI_IDS = ["9999000000704666", "9999000000705000"] + [str(9999000000704000 + i) for i in range(6)]
PAGINAS = {SUELI: ADS + [ad(i, TEXTO_TOP, 1) for i in SUELI_IDS], SINPUB: [ad(9999000000710001, "texto sobre un curso de crochet para principiantes", 4, page_id=SINPUB, page_uri="", page_name="Sin Pub")],
           OTRA: [ad(9999000000710002, "metodo de bordado a mano paso a paso", 3, page_id=OTRA, page_uri="https://www.facebook.com/tiendaotra/", page_name="Otra Tienda")]}
RESULTADOS = {f"site:facebook.com {PUB}": [R_PAGINA, R_FLORAL, R_TOP], "site:facebook.com/tiendaotra": [res("Receta de pastel de chocolate", "9999000000727777")], f"site:facebook.com {SINPUB}": []}


def correr(hoja, meta, tmp, maximo=12, simular=False, buscar=None, ahora=AHORA):
    log, llamadas = [], []
    base = buscador(RESULTADOS, llamadas)
    codigo, r = flujo.correr_busqueda(meta, hoja, hoja.read_dicts("OFERTAS"), maximo, radar.RateLimited, RED, buscar or base, simular=simular, ahora=ahora,
                                      ruta=Path(tmp) / "flujo.json", ruta_antes=Path(tmp) / "flujo_antes.json", log=log.append)
    return codigo, r, log, llamadas


def celdas(hoja):
    return [(f["Anunciante"], f["ID de la página"], f["ID del video"], f["Origen del flujo"]) for f in hoja.filas]


with tempfile.TemporaryDirectory() as tmp:
    hoja = Hoja([fila("Pinturas da Rosa", 9999000000704666, VIEJO, "9999000000738404"),         # IDs viejos del radar: se reemplazan
                 fila("Ya fijada a mano", 9999000000704667, "1", "2", "manual"),                  # manual: no se toca ni se busca
                 fila("Ya hallada", 9999000000704668, "3", "4", "google"),                        # google: ya esta hecha
                 fila("Sin publicacion", 9999000000710001, SINPUB, "9999999999"),
                 fila("Otra Tienda", 9999000000710002, OTRA, "8888888888")])
    meta = Meta(PAGINAS, de_anuncio={"9999000000704666": SUELI, "9999000000710001": SINPUB, "9999000000710002": OTRA})
    codigo, r, log, llamadas = correr(hoja, meta, tmp)
    igual(codigo, 0, "una corrida normal sale bien: " + " | ".join(log))
    igual(celdas(hoja)[0], ("Pinturas da Rosa", PUB, "9999000000472319", "google"), "la oferta de la captura: ID publico de la pagina, numero de la publicacion y origen «google»")
    igual(celdas(hoja)[1:3], [("Ya fijada a mano", "1", "2", "manual"), ("Ya hallada", "3", "4", "google")], "las filas «manual» y «google» no se tocan")
    igual(celdas(hoja)[3:], [("Sin publicacion", SINPUB, "9999999999", ""), ("Otra Tienda", OTRA, "8888888888", "")], "sin publicacion hallada, los IDs que habia se quedan como estaban (nada se borra)")
    igual([r["porBuscar"], r["intentadas"], r["halladas"], r["sinPublicacion"], r["escritas"], r["errores"]], [3, 3, 1, 2, 3, 0], "resumen: 3 por buscar, 1 hallada, 2 sin publicacion, 3 celdas escritas")
    ok(not any(c[1] in ("1", "3") for c in meta.llamadas if c[0] == "pagina"), "no se pidio nada a Meta para las filas «manual» y «google»")
    igual(len(hoja.escrituras), 1, "una sola escritura a la planilla")
    antes = json.loads((Path(tmp) / "flujo_antes.json").read_text(encoding="utf-8"))
    igual([(a["fila"], a["pagina"], a["video"], a["origen"]) for a in antes], [(2, VIEJO, "9999000000738404", "")], "lo que se reemplazo queda respaldado para poder deshacer")
    memo = json.loads((Path(tmp) / "flujo.json").read_text(encoding="utf-8"))["filas"]
    igual(sorted(k for k, v in memo.items() if v["motivo"] == "sin_publicacion"), ["b9999000000710001", "b9999000000710002"], "lo no hallado se anota para no repetirlo cada dia")
    # reintentos por horas: lo no hallado espera 6 h el primer reintento, 24 h el segundo, 72 h el tercero y despues 7 dias
    igual([v["n"] for v in memo.values() if v["motivo"] == "sin_publicacion"], [1, 1], "cada intento fallido anota su numero")
    meta2 = Meta(PAGINAS, de_anuncio={})
    codigo, r, log, llamadas = correr(hoja, meta2, tmp, ahora=AHORA + timedelta(hours=5))
    igual([r["porBuscar"], r["recientes"], meta2.llamadas, llamadas], [0, 2, [], []], "a las 5 horas no se repite ni la Biblioteca ni la busqueda")
    SIN = {"9999000000710001": SINPUB, "9999000000710002": OTRA}
    codigo, r, log, llamadas = correr(hoja, Meta(PAGINAS, de_anuncio=SIN), tmp, ahora=AHORA + timedelta(hours=7))
    igual([r["porBuscar"], r["recientes"]], [2, 0], "a las 7 horas se reintenta lo que no se hallo")
    memo = json.loads((Path(tmp) / "flujo.json").read_text(encoding="utf-8"))["filas"]
    igual(sorted(v["n"] for v in memo.values()), [2, 2], "y el segundo intento fallido sube la cuenta a 2")
    codigo, r, log, llamadas = correr(hoja, Meta(PAGINAS, de_anuncio=SIN), tmp, ahora=AHORA + timedelta(hours=7 + 20))
    igual([r["porBuscar"], r["recientes"]], [0, 2], "con 2 intentos fallidos espera 24 h: a las 20 h no")
    codigo, r, log, llamadas = correr(hoja, Meta(PAGINAS, de_anuncio=SIN), tmp, ahora=AHORA + timedelta(hours=7 + 25))
    igual([r["porBuscar"], r["recientes"]], [2, 0], "a las 25 h si")
    # una nota vieja (sin hora, de antes de este cambio) cuenta como un intento hecho a mediodia de su fecha
    viejo = {"b9999000000710001": {"t": "2026-10-29", "motivo": "sin_publicacion"}}
    ref = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)      # 15:00 en Bogota: pasaron 3 h
    igual([flujo._reciente_busqueda(viejo, "b9999000000710001", "2026-10-29", ref), flujo._reciente_busqueda(viejo, "b9999000000710001", "2026-10-30", ref + timedelta(hours=4))], [True, False], "la nota vieja se reintenta pasadas 6 h")
    igual([flujo._reciente_busqueda({"x": "basura"}, "x", "", ref), flujo._reciente_busqueda({}, "x", "", ref), flujo._reciente_busqueda({"x": {"t": "ayer"}}, "x", "", ref)], [False, False, False], "notas rotas o ausentes no frenan")

with tempfile.TemporaryDirectory() as tmp:        # simulacion: consulta pero no escribe nada
    hoja = Hoja([fila("Pinturas da Rosa", 9999000000704666, VIEJO, "9999000000738404")])
    codigo, r, log, llamadas = correr(hoja, Meta(PAGINAS, de_anuncio={"9999000000704666": SUELI}), tmp, simular=True)
    igual([celdas(hoja)[0][3], hoja.escrituras, r["halladas"]], ["", [], 1], "simular halla pero no escribe")
    ok(any("[simulacion] escribiria" in x and "9999000000472319" in x for x in log), "y muestra lo que escribiria")
    ok(not (Path(tmp) / "flujo.json").exists() and not (Path(tmp) / "flujo_antes.json").exists(), "sin tocar flujo.json ni el respaldo")

with tempfile.TemporaryDirectory() as tmp:        # alguien fija la fila como «manual» mientras la corrida habla con Google: no se pisa
    def fijar(h):
        h.filas[0]["Origen del flujo"] = "manual"
        h.filas[0]["ID del video"] = "77777777777"
    hoja = Hoja([fila("Pinturas da Rosa", 9999000000704666, VIEJO, "9999000000738404")], antes_de_releer=fijar)
    codigo, r, log, llamadas = correr(hoja, Meta(PAGINAS, de_anuncio={"9999000000704666": SUELI}), tmp)
    igual([celdas(hoja)[0], r["omitidas"], hoja.escrituras], [("Pinturas da Rosa", VIEJO, "77777777777", "manual"), 1, []], "una fila que el dueno fijo a mano mientras tanto no se pisa")

with tempfile.TemporaryDirectory() as tmp:        # alguien inserta una fila arriba: la oferta se busca por el ID de su anuncio
    def insertar(h):
        h.filas.insert(0, fila("Fila nueva de arriba", 9999000000743456))
    hoja = Hoja([fila("Pinturas da Rosa", 9999000000704666, VIEJO, "9999000000738404")], antes_de_releer=insertar)
    codigo, r, log, llamadas = correr(hoja, Meta(PAGINAS, de_anuncio={"9999000000704666": SUELI}), tmp)
    igual([celdas(hoja)[0][3], celdas(hoja)[1]], ["", ("Pinturas da Rosa", PUB, "9999000000472319", "google")], "si se movio la fila, se escribe en la que ahora es la suya (no en la que ocupa su lugar)")

with tempfile.TemporaryDirectory() as tmp:        # la busqueda se queda sin credito a mitad: lo ya hallado se escribe y se corta
    ADS2 = {SUELI: PAGINAS[SUELI], "999900000404444": [ad(9999000000704999, TEXTO_TOP, 3, page_id="999900000404444", page_uri="https://www.facebook.com/999900000404444/")]}
    hoja = Hoja([fila("Pinturas da Rosa", 9999000000704666), fila("Otra con credito agotado", 9999000000704999), fila("Tercera", 9999000000705000)])
    pedidas = []

    def buscar_cortada(q, pagina=1):
        pedidas.append(q)
        if PUB in q:
            return [R_TOP]
        raise publicacion.SinCredito("Serper dijo que no quedan busquedas (creditos)")

    meta = Meta(ADS2, de_anuncio={"9999000000704666": SUELI, "9999000000704999": "999900000404444", "9999000000705000": SUELI})
    codigo, r, log, llamadas = correr(hoja, meta, tmp, buscar=buscar_cortada)
    igual([codigo, r["busquedaCortada"], r["halladas"]], [1, True, 1], "sin credito: se corta la corrida con codigo 1")
    ok(any("se corto" in x for x in log), "y se avisa en el log")
    ok(celdas(hoja)[0][3] == "google" or celdas(hoja)[2][3] == "google", "lo que ya se habia hallado antes de cortar si se escribe")
    ok(all(c[3] == "" for c in celdas(hoja)[1:2]), "la fila que fallo no se toca")

with tempfile.TemporaryDirectory() as tmp:        # Meta limita la IP: codigo 3 y se corta
    hoja = Hoja([fila("Pinturas da Rosa", 9999000000704666)])
    meta = Meta(PAGINAS, de_anuncio={"9999000000704666": SUELI}, falla={SUELI: radar.RateLimited("HTTP 429")})
    codigo, r, log, llamadas = correr(hoja, meta, tmp)
    igual([codigo, r["limitado"], hoja.escrituras], [3, True, []], "Meta limito la IP: codigo 3 y no se escribe nada")

with tempfile.TemporaryDirectory() as tmp:        # tres fallos seguidos de red (Meta o Google) cortan la corrida
    hoja = Hoja([fila(f"Oferta {i}", 9999000000704000 + i) for i in range(6)])
    meta = Meta({SUELI: PAGINAS[SUELI]}, de_anuncio={str(9999000000704000 + i): SUELI for i in range(6)})

    def buscar_rota(q, pagina=1):
        raise requests.ConnectionError("sin red de prueba")
    codigo, r, log, llamadas = correr(hoja, meta, tmp, buscar=buscar_rota)
    igual([codigo, r["errores"], r["intentadas"]], [1, flujo.RED_FALLOS_MAX, flujo.RED_FALLOS_MAX], "tres fallos seguidos: se corta (no se insiste con las otras tres)")

with tempfile.TemporaryDirectory() as tmp:        # a la planilla le falta la columna «Origen del flujo»
    hoja = Hoja([fila("Pinturas da Rosa", 9999000000704666)], cabecera=CAB[:-1])
    codigo, r, log, llamadas = correr(hoja, Meta(PAGINAS), tmp)
    igual([codigo, hoja.escrituras], [1, []], "sin la columna de origen no se hace nada")
    ok(any("--preparar-flujo" in x for x in log), "y se dice que hay que correr --preparar-flujo")

with tempfile.TemporaryDirectory() as tmp:        # el maximo por corrida y el orden: primero lo que el dueno mira
    hoja = Hoja([fila("Descartada", 9999000000704001, status="descartada"), fila("Vigilar", 9999000000704002, status="vigilar"), fila("Nuevo", 9999000000704003, status="nuevo")])
    meta = Meta(PAGINAS, de_anuncio={"9999000000704001": SUELI, "9999000000704002": SUELI, "9999000000704003": SUELI})
    codigo, r, log, llamadas = correr(hoja, meta, tmp, maximo=1)
    igual([r["intentadas"], celdas(hoja)[1][3], celdas(hoja)[0][3]], [1, "google", ""], "con maximo 1 se hace primero la de estado «vigilar»")

# ---------------------------------------------------------------- DOS ofertas del MISMO anunciante (2026-11-13)
# Mentora tiene varias ofertas en la misma pagina («cuerpo astral», filosofia...). Con los angulos de TODA la pagina, hallar() daba la publicacion del angulo que
# Google tuviera, aunque fuera de otra oferta. Con SOLO el texto del anuncio de la oferta, o halla la suya o no halla nada.
TEXTO_ASTRAL = "¿Y si pudieras experimentar una salida del cuerpo mientras te quedas dormido? Existe un estado de concentración y conocimientos para lograrlo con una guía práctica."
TEXTO_FILO = "¿Siempre quisiste entender filosofía y psicoanálisis, pero sentías que era demasiado difícil? Platón, Aristóteles, Nietzsche, Freud, Lacan y más en una guía visual."
PUB_M = "61586031826854"
R_FILO = {"title": "¿Siempre quisiste entender filosofía y psicoanálisis, pero ...", "link": f"https://www.facebook.com/{PUB_M}/posts/siempre-quisiste-entender-filosofia/999900000000521060/", "snippet": "Platón, Aristóteles, Nietzsche, Freud, Lacan"}
R_ASTRAL = {"title": "¿Y si pudieras experimentar una salida del cuerpo mientras te quedas dormido?", "link": f"https://www.facebook.com/{PUB_M}/videos/salida-del-cuerpo/999900000000531060/", "snippet": "concentración y conocimientos"}
ok(publicacion.hallar(buscador([R_FILO]), PUB_M, "", "Mentora", [TEXTO_FILO, TEXTO_ASTRAL]) is not None, "con los angulos de toda la pagina, la publicacion de filosofia pasa (era el error: la oferta de «cuerpo astral» recibia esa)")
igual(publicacion.hallar(buscador([R_FILO]), PUB_M, "", "Mentora", [TEXTO_ASTRAL]), None, "con SOLO el texto del anuncio de «cuerpo astral», la de filosofia no se acepta: no hay publicacion")
h_astral = publicacion.hallar(buscador([R_FILO, R_ASTRAL]), PUB_M, "", "Mentora", [TEXTO_ASTRAL])
igual(h_astral and (h_astral["numero"], h_astral["angulo"]), ("999900000000531060", 0), "y si Google trae las dos, se queda con la de SU anuncio")
igual(publicacion.angulos([ad(999900000302813, TEXTO_ASTRAL, 1)], lambda a: True), [TEXTO_ASTRAL], "el angulo de un solo anuncio es su texto")
igual(publicacion.angulos([ad(999900000302813, "", 1)], lambda a: True), [], "un anuncio sin texto no da angulo (y hallar no acepta nada)")

# la busqueda de la planilla (correr_busqueda): cada fila con la publicacion de SU anuncio
MENTA = "999900000431570"
ADS_M = [ad(9999000000519629, TEXTO_FILO, 4, page_id=MENTA, page_uri=f"https://www.facebook.com/{PUB_M}/", page_name="Mentora"),
         ad(999900000302813, TEXTO_ASTRAL, 1, page_id=MENTA, page_uri=f"https://www.facebook.com/{PUB_M}/", page_name="Mentora")]
RES_M = {f"site:facebook.com {PUB_M}": [R_FILO]}          # Google solo tiene la de filosofia


class MetaAnuncio(Meta):
    """Una Meta que ademas lee UN anuncio de su propia pagina en la Biblioteca (radar.Meta.anuncio)."""
    def __init__(self, paginas, anuncios, **k):
        super().__init__(paginas, **k)
        self.anuncios = anuncios

    def anuncio(self, ad_id):
        self.llamadas.append(("un_anuncio", ad_id))
        return self.anuncios.get(ad_id)


with tempfile.TemporaryDirectory() as tmp:
    hoja = Hoja([fila("Mentora", 9999000000519629, MENTA, "1"), fila("Mentora", 999900000302813, MENTA, "2")])
    llam = []
    codigo, r, log, _ = correr(hoja, Meta({MENTA: ADS_M}), tmp, buscar=buscador(RES_M, llam))
    igual(celdas(hoja), [("Mentora", PUB_M, "999900000000521060", "google"), ("Mentora", MENTA, "2", "")],
          "dos ofertas de la misma pagina: la de filosofia recibe SU publicacion y la de «cuerpo astral» NO recibe la de filosofia (se queda como estaba)")
    igual([r["halladas"], r["sinPublicacion"], r["sinAnuncio"]], [1, 1, 0], "resumen: 1 hallada, 1 sin publicacion")
    memo = json.loads((Path(tmp) / "flujo.json").read_text(encoding="utf-8"))["filas"]
    igual({k: v["motivo"] for k, v in memo.items()}, {"b999900000302813": "sin_publicacion"}, "la de «cuerpo astral» queda anotada para reintentar")
with tempfile.TemporaryDirectory() as tmp:
    hoja = Hoja([fila("Mentora", 999900000302813, MENTA, "2")])
    codigo, r, log, _ = correr(hoja, Meta({MENTA: ADS_M}), tmp, buscar=buscador({f"site:facebook.com {PUB_M}": [R_FILO, R_ASTRAL]}))
    igual(celdas(hoja)[0], ("Mentora", PUB_M, "999900000000531060", "google"), "cuando Google tiene la de SU anuncio, la fila de «cuerpo astral» recibe la suya")
with tempfile.TemporaryDirectory() as tmp:        # su anuncio ya no esta activo: no se busca nada (ni se gasta Google) y no se toma la de otra oferta
    hoja = Hoja([fila("Mentora", 9999000000611111, MENTA, "2")])
    llam = []
    codigo, r, log, _ = correr(hoja, Meta({MENTA: ADS_M}), tmp, buscar=buscador(RES_M, llam))
    igual([celdas(hoja)[0], r["sinAnuncio"], llam, codigo], [("Mentora", MENTA, "2", ""), 1, [], 0], "anuncio apagado: sin_anuncio, sin busquedas en Google y la fila no se toca")
    memo = json.loads((Path(tmp) / "flujo.json").read_text(encoding="utf-8"))["filas"]
    igual(memo["b9999000000611111"]["motivo"], "sin_anuncio", "y queda anotado para reintentar con las mismas esperas")
    ok(any("Sin anuncio" in x for x in log), "y lo dice el log")
with tempfile.TemporaryDirectory() as tmp:        # su anuncio no salio en la primera tanda de la pagina: se lee de su propia pagina (1 peticion) y se busca con su texto
    hoja = Hoja([fila("Mentora", 999900000302813, MENTA, "2")])
    solo_filo = [ADS_M[0]]
    meta = MetaAnuncio({MENTA: solo_filo}, {"999900000302813": ADS_M[1]})
    codigo, r, log, _ = correr(hoja, meta, tmp, buscar=buscador({f"site:facebook.com {PUB_M}": [R_FILO, R_ASTRAL]}))
    igual([celdas(hoja)[0][2], ("un_anuncio", "999900000302813") in meta.llamadas], ["999900000000531060", True], "anuncio fuera de la primera tanda: se lee de su pagina y se halla SU publicacion")
    meta = MetaAnuncio({MENTA: solo_filo}, {"999900000302813": dict(ADS_M[1], page_id="999999999999999")})
    hoja = Hoja([fila("Mentora", 999900000302813, MENTA, "2")])
    codigo, r, log, _ = correr(hoja, meta, tmp, buscar=buscador({f"site:facebook.com {PUB_M}": [R_FILO, R_ASTRAL]}))
    igual([celdas(hoja)[0][3], r["sinAnuncio"]], ["", 1], "si ese anuncio es de OTRA pagina no se usa")
with tempfile.TemporaryDirectory() as tmp:        # una fila SIN anuncio (solo el enlace de la pagina) se sigue buscando por la pagina, como antes
    hoja = Hoja([dict(fila("Pinturas da Rosa", 1), **{"Link del anuncio": f"https://www.facebook.com/ads/library/?active_status=active&view_all_page_id={SUELI}", "ID de la página": ""})])
    codigo, r, log, _ = correr(hoja, Meta(PAGINAS), tmp)
    igual(celdas(hoja)[0][1:], (PUB, "9999000000472319", "google"), "fila sin anuncio: la publicacion del angulo mas repetido de la pagina, como antes")

with tempfile.TemporaryDirectory() as tmp:        # el anuncio de la fila sin texto: no se gastan busquedas para un «sin publicacion» seguro
    hoja = Hoja([fila("Mentora", 999900000302813, MENTA, "2")])
    llam = []
    codigo, r, log, _ = correr(hoja, Meta({MENTA: [ADS_M[0], dict(ADS_M[1], text="", body="")]}), tmp, buscar=buscador(RES_M, llam))
    igual([celdas(hoja)[0][3], r["sinPublicacion"], llam], ["", 1, []], "anuncio sin texto: sin publicacion, sin busquedas en Google y la fila no se toca")

# textos de plantilla (revision del 13-nov): «Curso completo de X con certificado» y «… de Y …». El titulo de la otra oferta tambien pasa el umbral con el texto propio;
# con `ajenos` (los textos de las demas ofertas de la pagina) se rechaza la que se parece igual o mas a otra oferta.
T_X = "Curso completo de crochet para principiantes con certificado y acceso de por vida. Aprende desde cero."
T_Y = "Curso completo de bordado para principiantes con certificado y acceso de por vida. Aprende desde cero."
R_Y = {"title": "Curso completo de bordado para principiantes con certificado", "link": f"https://www.facebook.com/{PUB_M}/videos/curso-bordado/9999000000573333/", "snippet": ""}
R_X = {"title": "Curso completo de crochet para principiantes con certificado", "link": f"https://www.facebook.com/{PUB_M}/videos/curso-crochet/9999000000644444/", "snippet": ""}
ok(publicacion.hallar(buscador([R_Y]), PUB_M, "", "Mentora", [T_X]) is not None, "sin ajenos, la publicacion de bordado pasa para el anuncio de crochet (el riesgo de los textos de plantilla)")
d_aj = {}
igual(publicacion.hallar(buscador([R_Y]), PUB_M, "", "Mentora", [T_X], diag=d_aj, ajenos=[T_Y]), None, "con ajenos, la de bordado no se acepta para el de crochet")
ok(d_aj.get("de_otra_oferta", 0) >= 1, "y el diagnostico lo cuenta")
h_x = publicacion.hallar(buscador([R_Y, R_X]), PUB_M, "", "Mentora", [T_X], ajenos=[T_Y])
igual(h_x and h_x["numero"], "9999000000644444", "si Google trae las dos, se queda con la de crochet")
igual(publicacion.textos_ajenos([ad(1, T_X, 2), ad(2, T_X, 1), ad(3, T_Y, 4), ad(4, "apagado", 9, activo=False)], ad(2, T_X, 1)), [T_Y], "ajenos: las demas ofertas activas, sin las copias del anuncio propio")
ok(publicacion.de_otra_oferta(R_Y, T_X, [T_Y]), "plantilla: el titulo dice «bordado», que solo tiene el otro anuncio: es de otra oferta")
ok(not publicacion.de_otra_oferta(R_X, T_X, [T_Y]), "y la de crochet no es de otra oferta para el anuncio de crochet")

# ---------------------------------------------------------------- otras VERSIONES del MISMO anuncio (2026-11-19)
# Caso real (dueno, con captura): Loja Artesanato (pagina 999900000548972, ID publico 61584502316540; anuncio 99990000000553816). Google traia su publicacion con
# parecido 1,0, pero sus otros anuncios son la MISMA oferta (una copia con otros saltos de linea y otra version del texto): empataban con el anuncio propio y la regla
# de «otra oferta» la tumbaba («4 se parecian mas a otra oferta» -> sin publicacion; el dueno no veia el boton). Textos y resultados de Google tal como llegaron
# (el de la segunda version, hasta donde se vio). El enlace armado con 999900000000560077 abre la publicacion con su boton de WhatsApp (visto con sesion el 2026-11-19).
PUB_R = "61584502316540"
T_R = ("🪚 +2.000 PROJETOS DE MARCENARIA 🔨📐\n\nTenha acesso a mais de 2.000 projetos prontos para você usar e aplicar na sua marcenaria! 🚀\n\n📚 Projetos para diferentes "
       "tipos de móveis\n🛠️ Ideias práticas para colocar em execução\n💰 Use para criar móveis para seus clientes ou para o seu próprio dia a dia")
T_R_COPIA = T_R.replace("\n\n", "\n")
T_R_V2 = "📚 Tenha acesso a 2.000 projetos de marcenaria, com modelos prontos para criar móveis de diversos estilos.\n\n✅ Projetos detalhados e fáceis de seguir\n✅ Móveis par"
R_R1 = {"title": "+2.000 PROJETOS DE MARCENARIA 🔨📐 Tenha acesso a ...",
        "link": f"https://www.facebook.com/{PUB_R}/posts/-2000-projetos-de-marcenaria-tenha-acesso-a-mais-de-2000-projetos-prontos-para-v/999900000000560077/"}
R_R2 = {"title": "📚 Tenha acesso a 2.000 projetos de marcenaria, com ...",
        "link": f"https://www.facebook.com/{PUB_R}/posts/-tenha-acesso-a-2000-projetos-de-marcenaria-com-modelos-prontos-para-criar-m%C3%B3vei/999900000000750077"}
igual(publicacion.clave_texto(T_R), publicacion.clave_texto(T_R_COPIA), "la copia con otros saltos de linea tiene la misma clave")
ok(publicacion.clave_texto(T_R) != publicacion.clave_texto(T_R_V2), "otra version del texto, no")
ok(publicacion.clave_texto("🔥🔥🔥") != publicacion.clave_texto("✨✨✨") and publicacion.clave_texto("🔥🔥🔥"), "textos sin letras latinas: cada uno su clave (no todos «el mismo»)")
ADS_R = [ad(99990000000553816, T_R, 9), ad(2, T_R_COPIA, 3), ad(3, T_R_V2, 2)]
igual(publicacion.textos_ajenos(ADS_R, ADS_R[0]), [T_R_V2], "ajenos: la copia con otros saltos de linea no cuenta como otro anuncio")
igual(len(publicacion.angulos(ADS_R, lambda a: True)), 3, "angulos (busqueda por pagina): como siempre, la clave de los primeros 80 caracteres")
CAB = "🔥🔥🔥 ATENÇÃO 🔥🔥🔥 ÚLTIMAS VAGAS 🔥 OFERTA RELÂMPAGO 🔥 SÓ HOJE 🔥 "
V1 = CAB + "Aprenda crochê do zero com 50 aulas em vídeo, apostilas em PDF e suporte no WhatsApp para tirar suas dúvidas."
V2 = CAB + "Aprenda crochê do zero com 50 aulas em vídeo, apostilas em PDF, certificado e suporte no WhatsApp para tirar suas dúvidas."
ok(publicacion.mismo_texto(V1, V2), "dos versiones con la misma cabecera larga y casi las mismas palabras siguen siendo el mismo texto (como en main)")
igual(publicacion.comienzo_en_direccion(R_R1), ["2000", "projetos", "de", "marcenaria", "tenha", "acesso", "a", "mais", "de", "2000", "projetos", "prontos", "para"],
      "el comienzo de la publicacion que trae su direccion, en orden y sin la ultima palabra (viene cortada: «para-v»)")
igual(publicacion.comienzo_en_direccion({"link": f"https://www.facebook.com/{PUB_R}/videos/9999000000743456/"}), [], "un /videos/<numero> no trae texto")
ok(not publicacion.de_otra_oferta(R_R1, T_R, [T_R_COPIA, T_R_V2], perdonar=True, en_su_direccion=True),
   "segunda pasada: su texto EMPIEZA como la direccion y la otra version no: es la suya (la copia con otros saltos de linea ni cuenta)")
ok(publicacion.de_otra_oferta(R_R1, T_R, [T_R_V2], en_su_direccion=True), "primera pasada (la regla de siempre): el empate la tumba")
ok(publicacion.de_otra_oferta(R_R1, T_R, [T_R_V2], perdonar=True), "sin el ID de la pagina en la direccion (podria ser un clon) el empate no se perdona")
d_r = {}
h_r = publicacion.hallar(buscador({f"site:facebook.com {PUB_R} ": [R_R1], f"site:facebook.com {PUB_R}": [R_R2, R_R1]}), PUB_R, "", "Loja Artesanato", [T_R], diag=d_r,
                         ajenos=[T_R_COPIA, T_R_V2])
igual(h_r and (h_r["numero"], h_r["tipo"], h_r["puntaje"], h_r.get("perdonado")), ("999900000000560077", "post", 1.0, True), "Loja Artesanato: con sus otras versiones como ajenos se halla SU publicacion (antes: ninguna), marcada como empate perdonado")
igual(d_r.get("perdonado"), 1, "y el diagnostico lo dice (para revisarla a mano en los logs)")
h_r2 = publicacion.hallar(buscador([R_R2, R_R1]), PUB_R, "", "Loja Artesanato", [T_R], ajenos=[T_R_V2])
igual(h_r2 and h_r2["numero"], "999900000000560077", "con Google en el otro orden (primero la de la otra version) tambien recibe la SUYA, no la de la otra version")
llam_r = []
publicacion.hallar(buscador([R_R2, R_R1], llam_r), PUB_R, "", "Loja Artesanato", [T_R], ajenos=[T_R_V2])
igual(len(llam_r), 3, "la segunda pasada no gasta busquedas: las mismas 3 de siempre")
ok(publicacion.hallar(buscador([R_R1]), PUB_R, "", "Loja Artesanato", [T_R], ajenos=[T_R_V2, T_X]) is not None, "un ajeno sin nada que ver no cambia nada")
# empate con un ajeno que tiene UNA palabra del titulo que el propio no tiene: sigue siendo de otra oferta (igual que antes)
R_GEN = {"title": "Curso completo de bordado con certificado", "link": f"https://www.facebook.com/{PUB_M}/videos/x/9999000000655555/"}
ok(publicacion.de_otra_oferta(R_GEN, "Curso completo con certificado y acceso de por vida", ["Curso de bordado con certificado"]),
   "empate en cantidad (3 y 3), pero el otro explica «bordado» y el propio no: de otra oferta")
ok(publicacion.de_otra_oferta(R_GEN, "Curso completo con certificado y acceso de por vida", ["Curso completo de bordado, sin certificado"]),
   "el otro explica mas palabras (4 contra 3): de otra oferta")

# Caso real del mismo dia (Widom, fila 319): su anuncio es del curso de ASB y una copia suya (sin el prefijo {{product.brand}}...) explica las MISMAS 3 de 5 palabras de
# «Chegou o Curso Tecnico em Seguranca do Trabalho EaD», que es OTRO curso. Un empate con el titulo a medias sigue tumbandola (con «solo si explica una palabra que el
# propio no tiene» se colaba: medido con los resultados reales de Google).
T_W = ("{{product.brand}} {{product.name}} {{product.description}} widom.com.br ÚLTIMAS VAGAS! \n\n👩‍⚕️ Você já trabalha como ASB em consultórios, ajuda nos "
       "atendimentos, organiza tudo... mas ainda não tem a sua certificação profissional? O Curso Widom ASB foi feito pra quem já trabalha na área: mais segurança no "
       "mercado de trabalho.")
T_W_COPIA = T_W.split("widom.com.br ", 1)[1]
R_W = {"title": "Chegou o Curso Técnico em Segurança do Trabalho EaD ...", "link": "https://www.facebook.com/9999000000765327/posts/chegou/9999000000771197/"}
igual(publicacion.puntaje(R_W, T_W), (0.6, 3), "Widom: su anuncio de ASB explica 3 de las 5 palabras del titulo del otro curso (pasa el umbral)")
ok(publicacion.de_otra_oferta(R_W, T_W, [T_W_COPIA]), "y una copia suya empata con el titulo A MEDIAS: puede ser de otra oferta (no se perdona)")
igual(publicacion.hallar(buscador([R_W]), "9999000000765327", "", "Widom", [T_W], ajenos=[T_W_COPIA]), None, "Widom: sigue sin publicacion (no recibe la del curso de Seguranca do Trabalho)")
# titulo corto y generico de otra oferta (3 palabras, las 3 en los dos textos): tampoco se perdona el empate
R_CORTO = {"title": "Curso completo certificado", "link": f"https://www.facebook.com/{PUB_M}/videos/x/9999000000666666/"}
ok(publicacion.de_otra_oferta(R_CORTO, T_X, [T_Y]), "titulo de 3 palabras explicado entero por los dos: empate sin perdon (podria ser de la otra)")

# Revision de seguridad (2026-11-19): dos ofertas que EMPIEZAN con la misma frase. Google corta el titulo ahi y las dos publicaciones salen con el MISMO titulo: el texto
# que sigue (en la direccion o en la descripcion) es lo que dice de cual es cada una.
T_A = "🔥 Curso Online de Confeitaria Profissional 🔥 Aprenda a fazer bolos decorados do zero, com certificado e acesso vitalício."
T_B = "🔥 Curso Online de Confeitaria Profissional 🔥 Aprenda a fazer doces finos para vender, com certificado e acesso vitalício."
TIT_AB = "🔥 Curso Online de Confeitaria Profissional 🔥 Aprenda a ..."
R_B = {"title": TIT_AB, "link": f"https://www.facebook.com/{PUB_M}/videos/curso-online-de-confeitaria-profissional-aprenda-a-fazer-doces-finos/9999000000727777/"}
R_A = {"title": TIT_AB, "link": f"https://www.facebook.com/{PUB_M}/posts/curso-online-de-confeitaria-profissional-aprenda-a-fazer-bolos-decorados/9999000000638888/"}
igual(publicacion.hallar(buscador([R_B]), PUB_M, "", "Doceria", [T_A], ajenos=[T_B]), None, "la publicacion de B (dice «doces finos») no se le da a la oferta A, aunque el titulo cortado empate")
h_ab = publicacion.hallar(buscador([R_B, R_A]), PUB_M, "", "Doceria", [T_A], ajenos=[T_B])
igual(h_ab and h_ab["numero"], "9999000000638888", "si Google trae las dos, A recibe la SUYA (dice «bolos decorados»), aunque la de B sea video")
h_ba = publicacion.hallar(buscador([R_B, R_A]), PUB_M, "", "Doceria", [T_B], ajenos=[T_A])
igual(h_ba and h_ba["numero"], "9999000000727777", "y B la suya")
igual(h_ab and h_ab.get("perdonado"), True, "(lo de A sale en la segunda pasada: empate perdonado)")
R_A_CORTO = {"title": TIT_AB, "link": f"https://www.facebook.com/{PUB_M}/posts/curso-online-de-confeitaria-profissional-aprenda-a-fazer-bo/9999000000638888/"}
igual(publicacion.hallar(buscador([R_A_CORTO]), PUB_M, "", "Doceria", [T_A], ajenos=[T_B]), None, "si la direccion se corta antes de lo que las distingue («…fazer-bo»), las dos empiezan igual: se descarta")
R_B_RELLENO = {"title": TIT_AB, "snippet": "21 mil curtidas · 1,2 mil comentários · 3 dias atrás", "link": f"https://www.facebook.com/{PUB_M}/videos/9999000000727777/"}
igual(publicacion.hallar(buscador([R_B_RELLENO]), PUB_M, "", "Doceria", [T_A], ajenos=[T_B]), None, "una descripcion de relleno no cuenta como prueba: se descarta")
R_A_POST = {"title": "Aprenda a fazer bolos decorados do zero", "link": f"https://www.facebook.com/{PUB_M}/posts/aprenda-a-fazer-bolos-decorados-do-zero/9999999999999999/"}
h_post = publicacion.hallar(buscador([R_B, R_A_POST]), PUB_M, "", "Doceria", [T_A], ajenos=[T_B])
igual(h_post and (h_post["numero"], h_post.get("perdonado")), ("9999999999999999", None), "lo que la regla de siempre ya hallaba (el post de A) no cambia: el video de B no le gana")
R_B_SOLO = {"title": TIT_AB, "link": f"https://www.facebook.com/{PUB_M}/videos/9999000000727777/"}
igual(publicacion.hallar(buscador([R_B_SOLO]), PUB_M, "", "Doceria", [T_A], ajenos=[T_B]), None, "con solo el titulo cortado (sin descripcion ni texto en la direccion) no hay como saber de cual es: se descarta")
# una pagina CLON que copia el texto y pone el nombre del anunciante en la descripcion: la direccion trae OTRO ID; con otras versiones del anuncio, el empate no se perdona
R_CLON = {"title": R_R1["title"], "snippet": "Loja Artesanato. Tenha acesso a mais de 2.000 projetos prontos",
          "link": "https://www.facebook.com/99990000000789999/posts/-2000-projetos-de-marcenaria-tenha-acesso-a-mais-de-2000-projetos-prontos-para-v/999900000000799999/"}
igual(publicacion.hallar(buscador([R_CLON]), PUB_R, "", "Loja Artesanato", [T_R], ajenos=[T_R_V2]), None, "Ricardo con una pagina clon: no recibe la del clon")

# Revision de seguridad, ronda 2 (2026-11-19). (a) La copia real de Ricardo NO es identica: 60 de sus 66 palabras iguales desde el principio (cambia el final).
# (b) Dos ofertas que comparten una entrada LARGA (mas de 80 letras) no son «el mismo texto». (c) «/<ID>/» en la consulta de la direccion no la hace de la pagina.
T_R_FINAL = T_R.rsplit(" ", 3)[0] + " Garanta já o seu acesso"
ok(publicacion.mismo_texto(T_R, T_R_FINAL), "una copia que solo cambia el final es el mismo texto")
ok(not publicacion.mismo_texto(T_R, T_R_V2), "otra version del texto no")
ENTRADA = "ÚLTIMAS VAGAS NO VALOR PROMOCIONAL! Matrículas abertas por tempo limitado, com certificado reconhecido e aulas 100% online. "
T_C1 = ENTRADA + "Curso de Auxiliar de Saúde Bucal: aprenda a atender em consultórios odontológicos, organizar instrumentais e ajudar nos procedimentos do dia a dia."
T_C2 = ENTRADA + "Curso de Body Piercer Profissional: aprenda as técnicas de perfuração com segurança, biossegurança e atendimento para abrir o seu estúdio."
ok(len(publicacion.clave_texto(ENTRADA)) > 80 and not publicacion.mismo_texto(T_C1, T_C2), "dos cursos con la misma entrada larga NO son el mismo texto")
igual(publicacion.textos_ajenos([ad(1, T_C1, 3), ad(2, T_C2, 2), ad(3, T_R_FINAL, 1)], ad(1, T_C1, 3)), [T_C2, T_R_FINAL], "el otro curso sigue contando como otro anuncio")
igual(publicacion.textos_ajenos(ADS_R + [ad(4, T_R_FINAL, 1)], ADS_R[0]), [T_R_V2], "y la copia de Ricardo con otro final no cuenta como otro anuncio")
ok(not publicacion.mismo_texto("Clique e saiba mais", "Clique e saiba mais sobre o curso completo de crochê com certificado e acesso vitalício"), "un texto corto que es el comienzo de uno largo no es el mismo")
igual([publicacion._en_su_direccion({"link": f"https://www.facebook.com/{PUB_R}/posts/x/1234567890123/"}, PUB_R, ""),
       publicacion._en_su_direccion({"link": f"https://www.facebook.com/99990000000789999/posts/x/1234567890123/?ref=/{PUB_R}/"}, PUB_R, ""),
       publicacion._en_su_direccion({"link": f"https://www.facebook.com/permalink.php?story_fbid=1234567890123&id={PUB_R}"}, PUB_R, ""),
       publicacion._en_su_direccion({"link": "https://www.facebook.com/pg/tienda/posts/1234567890123/"}, "", "Tienda")],
      [True, False, True, True], "la pagina se mira en el PRIMER tramo de la direccion (o id= de permalink.php), no en la consulta")

with tempfile.TemporaryDirectory() as tmp:        # la busqueda de la planilla (cada 2 h): la fila de Ricardo recibe su publicacion
    PAG_R = "999900000548972"
    ads_r = [dict(a, page_id=PAG_R, page_uri=f"https://www.facebook.com/{PUB_R}/", page_name="Loja Artesanato") for a in ADS_R]
    hoja = Hoja([fila("Loja Artesanato", 99990000000553816, PAG_R, "9999000000802573")])
    codigo, r, log, _ = correr(hoja, Meta({PAG_R: ads_r}), tmp, buscar=buscador({f"site:facebook.com {PUB_R}": [R_R1, R_R2]}))
    igual([celdas(hoja)[0], r["halladas"], codigo], [("Loja Artesanato", PUB_R, "999900000000560077", "google"), 1, 0],
          "planilla: la fila de Loja Artesanato recibe su publicacion (y el dash le pone el boton de WhatsApp)")
    ok(any("EMPATE PERDONADO" in x and "999900000000560077" in x for x in log), "y el log de la corrida la marca para revisarla a mano")

# la pagina de la oferta con su ID PUBLICO (la planilla lo escribe asi tras la busqueda): el anuncio leido suelto, con su pagina interna, se acepta igual
m_pub = MetaAnuncio({}, {"999900000302813": ADS_M[1]})
igual(flujo.anuncio_propio(m_pub, PUB_M, "999900000302813", [], None) is ADS_M[1], True, "pagina con su ID publico: el anuncio de su pagina interna se acepta (por su page_profile_uri)")

# flujo.anuncio_propio y flujo.objeto_de_anuncio (sin red)
pausas = []
igual(flujo.anuncio_propio(Meta({}), MENTA, "999900000302813", ADS_M, lambda: pausas.append(1)) is ADS_M[1], True, "el anuncio de la oferta se toma de los de la pagina")
igual(pausas, [], "sin peticion ni pausa")
m_a = MetaAnuncio({}, {"999900000302813": ADS_M[1]})
igual(flujo.anuncio_propio(m_a, MENTA, "999900000302813", [], lambda: pausas.append(1)) is ADS_M[1], True, "si no esta entre los de la pagina, se lee de su propia pagina")
igual([pausas, m_a.llamadas], [[1], [("un_anuncio", "999900000302813")]], "con su pausa y una sola peticion")
igual(flujo.anuncio_propio(Meta({}), MENTA, "999900000302813", [], None), None, "una Meta que no sabe leer un anuncio suelto: None (no se inventa)")
igual(flujo.anuncio_propio(MetaAnuncio({}, {"999900000302813": dict(ADS_M[1], page_id="1234567")}), MENTA, "999900000302813", [], None), None, "un anuncio de otra pagina: None")
igual(flujo.anuncio_propio(MetaAnuncio({}, {}), MENTA, "abc", ADS_M, None), None, "un ID raro: None")

# el parseo de radar.normalize trae la direccion publica de la pagina
anuncio_meta = {"ad_archive_id": "1", "page_id": VIEJO, "collation_count": 3, "is_active": True,
                "snapshot": {"page_profile_uri": f"https://www.facebook.com/{PUB}/", "page_name": "Pinturas da Rosa", "cta_type": "WHATSAPP_MESSAGE", "link_url": "https://api.whatsapp.com/send",
                             "body": {"text": TEXTO_TOP}, "display_format": "VIDEO", "videos": []}}
n = radar.normalize(anuncio_meta)
igual([n["page_uri"], n["collation_count"], n["page_id"]], [f"https://www.facebook.com/{PUB}/", 3, VIEJO], "radar.normalize guarda la direccion publica de la pagina (page_profile_uri)")
igual(radar.normalize({"ad_archive_id": "2", "snapshot": {}})["page_uri"], "", "sin page_profile_uri queda vacia")
igual(publicacion.datos_de_pagina(n["page_uri"], n["page_id"]), (PUB, ""), "y de ahi sale el ID publico")

print(f"OK: {bien} comprobaciones de publicacion.py y de la busqueda de publicaciones")
