"""Pruebas SIN red de pedidos_flujo.py (los pedidos de «Buscar el WhatsApp» del dash). El dash, Meta y Google estan simulados. Corre con:  python -X utf8 test_pedidos.py"""
import requests

import flujo
import pedidos_flujo
import publicacion
import radar

bien = 0


def ok(cond, msg):
    global bien
    assert cond, msg
    bien += 1


def igual(a, b, msg):
    ok(a == b, f"{msg}: obtuve {a!r} y esperaba {b!r}")


PUB, PAG = "61579375388957", "999900000468472"
TEXTO = "Praticidade total para o seu atelie! Conheca o pacote com 1.000 adesivos termocolantes para panos de prato, pecas lindas aplicadas em segundos."


def ad(archive, texto, cuenta, pid=PAG, uri=f"https://www.facebook.com/{PUB}/", nombre="Pinturas da Rosa"):
    return {"archive_id": str(archive), "page_id": pid, "text": texto, "body": texto, "collation_count": cuenta, "cta_type": "WHATSAPP_MESSAGE", "link": "https://api.whatsapp.com/send",
            "active": True, "sig": "s" + str(archive), "page_uri": uri, "page_name": nombre, "start": 1000, "video_id": "9"}


class Resp:
    def __init__(self, codigo, cuerpo=None):
        self.status_code, self._c = codigo, cuerpo

    def json(self):
        if self._c is None:
            raise ValueError("no es JSON")
        return self._c


class Dash:
    """Un dash falso: GET devuelve los pendientes, POST guarda lo que el radar manda."""
    def __init__(self, pendientes, codigo=200, falla_post=None):
        self.pend, self.codigo, self.posts, self.cabeceras, self.falla_post = pendientes, codigo, [], [], falla_post

    def get(self, url, headers=None, timeout=None):
        self.cabeceras.append(headers)
        if self.codigo != 200:
            return Resp(self.codigo, {})
        return Resp(200, {"ok": True, "pedidos": [p if isinstance(p, dict) else {"pagina": p} for p in self.pend]})

    def post(self, url, json=None, headers=None, timeout=None):
        self.cabeceras.append(headers)
        if self.falla_post:
            raise self.falla_post
        self.posts.append(json)
        return Resp(200, {"ok": True})


class Meta:
    def __init__(self, paginas, falla=None):
        self.paginas, self.falla, self.llamadas = paginas, falla or {}, []

    def pagina_anunciante(self, pid, country="BR", antes_de=None):
        self.llamadas.append(pid)
        if pid in self.falla:
            raise self.falla[pid]
        lista = self.paginas.get(pid)
        return None if lista is None else {"total": len(lista), "ads": lista, "hay_mas": False}


def buscador(resultados, llamadas=None):
    def buscar(q, pagina=1):
        if llamadas is not None:
            llamadas.append(q)
        return resultados
    return buscar


R_TOP = {"title": "1.000 Termocolantes para Panos de Prato", "link": f"https://www.facebook.com/{PUB}/videos/1000-termocolantes-para-panos-de-prato/9999000000472319/", "snippet": "adesivos termocolantes para panos de prato"}
RED = (requests.RequestException,)
log = []

# --- halla la publicacion
dash = Dash([PAG])
codigo, r = pedidos_flujo.atender(Meta({PAG: [ad(1, TEXTO, 6)]}), buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual(codigo, 0, "una pasada normal sale bien")
igual(dash.posts, [{"pagina": PAG, "estado": "hallado", "publicacion": "9999000000472319", "paginaPublica": PUB, "nombre": "Pinturas da Rosa"}], "deja la publicacion hallada con el ID publico de la pagina")
igual([r["pendientes"], r["atendidas"], r["halladas"]], [1, 1, 1], "resumen")
ok(all(h and h.get("x-clave-radar") == "llave-x" for h in dash.cabeceras), "la llave viaja en la cabecera x-clave-radar")
ok("llave-x" not in " ".join(log), "la llave no se imprime")

# --- Google no la tiene
dash = Dash([PAG])
codigo, r = pedidos_flujo.atender(Meta({PAG: [ad(1, TEXTO, 6)]}), buscador([]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0]["nombre"], r["sinPublicacion"], codigo], ["sin_publicacion", "Pinturas da Rosa", 1, 0], "sin resultados: sin_publicacion con el nombre de la pagina (no es un error)")

# --- la Biblioteca no trae anuncios (pagina sin anuncios activos o inexistente)
dash = Dash(["999900000485555", "999900000496666"])
codigo, r = pedidos_flujo.atender(Meta({"999900000485555": [], "999900000496666": None}), buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([p["estado"] for p in dash.posts], ["sin_pagina", "sin_pagina"], "sin anuncios en la Biblioteca: sin_pagina")

# --- oferta de oro en espanol: sin anuncios en Brasil y con anuncios en los demas paises (2026-11-11: Bienestar Integral Digital, oro Argentina, 0 en BR y 24 en ALL).
# Antes salia «sin_pagina» y el boton de la ficha del dash nunca hallaba su WhatsApp.
class MetaPais(Meta):
    def __init__(self, por_pais):
        super().__init__({})
        self.por_pais = por_pais

    def pagina_anunciante(self, pid, country="BR", antes_de=None):
        self.llamadas.append((pid, country))
        lista = self.por_pais.get((pid, country))
        return None if lista is None else {"total": len(lista), "ads": lista, "hay_mas": False}


dash, pausas = Dash([PAG]), []
meta = MetaPais({(PAG, "BR"): [], (PAG, "ALL"): [ad(1, TEXTO, 6)]})
codigo, r = pedidos_flujo.atender(meta, buscador([R_TOP]), "llave-x", radar.RateLimited, RED, pausa=lambda: pausas.append(1), log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0].get("publicacion"), dash.posts[0].get("nombre")], ["hallado", "9999000000472319", "Pinturas da Rosa"], "oro en espanol: sin anuncios en Brasil se miran todos los paises y se halla su publicacion")
igual(meta.llamadas, [(PAG, "BR"), (PAG, "ALL")], "primero Brasil y despues todos los paises")
igual(len(pausas), 2, "con su pausa tambien antes de la segunda peticion")
dash = Dash([PAG])
meta = MetaPais({(PAG, "BR"): None, (PAG, "ALL"): [ad(1, TEXTO, 6)]})
pedidos_flujo.atender(meta, buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual(dash.posts[0]["estado"], "hallado", "tambien si Brasil no trae ni la conexion (None)")
dash = Dash([PAG])
meta = MetaPais({(PAG, "BR"): [ad(1, TEXTO, 6)], (PAG, "ALL"): [ad(2, TEXTO, 6)]})
pedidos_flujo.atender(meta, buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual(meta.llamadas, [(PAG, "BR")], "una pagina con anuncios en Brasil cuesta una sola peticion, como siempre")
dash = Dash([PAG])
meta = MetaPais({(PAG, "BR"): [], (PAG, "ALL"): []})
pedidos_flujo.atender(meta, buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual(dash.posts[0]["estado"], "sin_pagina", "sin anuncios en ningun pais: sin_pagina, como antes")
igual(flujo.anuncios_de_pagina(MetaPais({(PAG, "BR"): None, (PAG, "ALL"): None}), PAG), None, "si ningun pais trae la conexion devuelve None (lo que el llamador ya sabe tratar)")

# --- Meta limita la IP: se corta y el pedido sigue pendiente (no se manda resultado)
dash = Dash([PAG, "999900000485555"])
meta = Meta({}, falla={PAG: radar.RateLimited("HTTP 429")})
codigo, r = pedidos_flujo.atender(meta, buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([codigo, dash.posts, meta.llamadas, r["limitado"]], [3, [], [PAG], True], "Meta limito: codigo 3, no se manda nada y no se sigue con los demas")

# --- Google sin credito: se le dice al equipo y se corta
dash = Dash([PAG, "999900000485555"])


def sin_credito(q, pagina=1):
    raise publicacion.SinCredito("sin creditos")


codigo, r = pedidos_flujo.atender(Meta({PAG: [ad(1, TEXTO, 6)]}), sin_credito, "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([codigo, len(dash.posts), dash.posts[0]["estado"]], [1, 1, "error"], "sin credito: un error claro para el equipo y se corta")
ok("Google" in dash.posts[0]["mensaje"], "el mensaje dice que la busqueda en Google no esta disponible")

# --- error de red con Meta: error para ese pedido y se sigue con el siguiente
dash = Dash(["999900000485555", PAG])
meta = Meta({PAG: [ad(1, TEXTO, 6)]}, falla={"999900000485555": requests.ConnectionError("x")})
codigo, r = pedidos_flujo.atender(meta, buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([p["estado"] for p in dash.posts], ["error", "hallado"], "un fallo de red en un pedido no frena el siguiente")
igual(codigo, 1, "y la pasada sale con 1")

# --- tope por pasada y datos raros
dash = Dash([str(100000000000 + i) for i in range(9)])
codigo, r = pedidos_flujo.atender(Meta({}), buscador([]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash, maximo=3)
igual([len(dash.posts), r["pendientes"]], [3, 9], "atiende como mucho `maximo` por pasada")
d2 = Dash([])
d2.get = lambda url, headers=None, timeout=None: Resp(200, {"ok": True, "pedidos": [{"pagina": "abc"}, {"pagina": "999900000502345"}, "basura", {"pagina": None}]})
igual(pedidos_flujo.traer_pendientes("k", d2), ["999900000502345"], "solo se aceptan IDs de solo digitos")

# --- el dash no contesta bien
for codigo_http, texto in [(401, "llave"), (503, "CLAVE_RADAR"), (500, "500")]:
    try:
        pedidos_flujo.traer_pendientes("k", Dash([], codigo=codigo_http))
        ok(False, f"HTTP {codigo_http} debia fallar")
    except pedidos_flujo.PedidosError as e:
        ok(texto in str(e), f"HTTP {codigo_http} se explica: {e}")


class Caido:
    def get(self, *a, **k):
        raise requests.ConnectionError("x")
    post = get


try:
    pedidos_flujo.traer_pendientes("k", Caido())
    ok(False, "un dash caido debia fallar")
except pedidos_flujo.PedidosError as e:
    ok("no respondio" in str(e), "un dash caido se avisa")

# --- no se pudo dejar el resultado: se corta y se cuenta como error
dash = Dash([PAG, "999900000485555"], falla_post=requests.ConnectionError("x"))
codigo, r = pedidos_flujo.atender(Meta({PAG: [ad(1, TEXTO, 6)]}), buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([codigo, r["atendidas"], r["errores"]], [1, 0, 1], "si no puede dejar el resultado, no lo da por atendido y corta")

# ====== POR ANUNCIO (2026-11-13): dos ofertas del MISMO anunciante. El pedido de la ficha trae el anuncio de su oferta y se busca SOLO su publicacion.
# Caso real: Mentora (pagina 999900000431570), oferta de «cuerpo astral» (anuncio 999900000302813); la de filosofia es la mas repetida y Google solo tenia esa.
MENTA, PUB_M, AD_ASTRAL, AD_FILO = "999900000431570", "61586031826854", "999900000302813", "9999000000519629"
T_ASTRAL = "¿Y si pudieras experimentar una salida del cuerpo mientras te quedas dormido? Existe un estado de concentración y conocimientos para lograrlo con una guía práctica."
T_FILO = "¿Siempre quisiste entender filosofía y psicoanálisis, pero sentías que era demasiado difícil? Platón, Aristóteles, Nietzsche, Freud, Lacan y más en una guía visual."
R_FILO = {"title": "¿Siempre quisiste entender filosofía y psicoanálisis, pero ...", "link": f"https://www.facebook.com/{PUB_M}/posts/siempre-quisiste/999900000000521060/", "snippet": "Platón, Aristóteles"}
R_ASTRAL = {"title": "¿Y si pudieras experimentar una salida del cuerpo mientras te quedas dormido?", "link": f"https://www.facebook.com/{PUB_M}/videos/salida/999900000000531060/", "snippet": ""}
ADS_M = [ad(AD_FILO, T_FILO, 4, pid=MENTA, uri=f"https://www.facebook.com/{PUB_M}/", nombre="Mentora"), ad(AD_ASTRAL, T_ASTRAL, 1, pid=MENTA, uri=f"https://www.facebook.com/{PUB_M}/", nombre="Mentora")]

dash, consultas = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}, {"pagina": MENTA, "anuncio": AD_FILO}, {"pagina": MENTA}]), []
codigo, r = pedidos_flujo.atender(Meta({MENTA: ADS_M}), buscador([R_FILO], consultas), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([(p["estado"], p.get("anuncio"), p.get("publicacion")) for p in dash.posts], [("sin_publicacion", AD_ASTRAL, None), ("hallado", AD_FILO, "999900000000521060"), ("hallado", None, "999900000000521060")],
      "la oferta de «cuerpo astral» NO recibe la publicacion de filosofia; la de filosofia si recibe la suya; el pedido de la pagina sola sigue como antes")
ok("solo su anuncio" in dash.posts[0]["mensaje"], "el mensaje dice que se busco solo con su anuncio")
igual([r["halladas"], r["sinPublicacion"], codigo], [2, 1, 0], "resumen")
dash = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}])
pedidos_flujo.atender(Meta({MENTA: ADS_M}), buscador([R_FILO, R_ASTRAL]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0]["publicacion"], dash.posts[0]["anuncio"], dash.posts[0]["paginaPublica"]], ["hallado", "999900000000531060", AD_ASTRAL, PUB_M],
      "si Google tiene la de SU anuncio, la oferta de «cuerpo astral» recibe la suya (con su anuncio en la respuesta)")
# su anuncio ya no esta activo (y Meta no sabe leerlo suelto): «sin_anuncio», sin gastar ni una busqueda en Google
dash, consultas = Dash([{"pagina": MENTA, "anuncio": "1111111111111"}]), []
codigo, r = pedidos_flujo.atender(Meta({MENTA: ADS_M}), buscador([R_FILO], consultas), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0]["anuncio"], consultas, r["sinAnuncio"]], ["sin_anuncio", "1111111111111", [], 1], "anuncio apagado: sin_anuncio y ninguna busqueda en Google")

# ====== otras VERSIONES del MISMO anuncio (2026-11-19). Caso real (dueno, con captura): Loja Artesanato (pagina 999900000548972, ID publico 61584502316540,
# anuncio 99990000000553816). Sus otros anuncios son la misma oferta (una copia con otros saltos de linea y otra version del texto) y empataban: «sin publicacion».
RIC, PUB_R, AD_R = "999900000548972", "61584502316540", "99990000000553816"
T_R = "🪚 +2.000 PROJETOS DE MARCENARIA 🔨📐\n\nTenha acesso a mais de 2.000 projetos prontos para você usar e aplicar na sua marcenaria! 🚀\n\n📚 Projetos para diferentes tipos de móveis"
T_R_V2 = "📚 Tenha acesso a 2.000 projetos de marcenaria, com modelos prontos para criar móveis de diversos estilos.\n\n✅ Projetos detalhados e fáceis de seguir"
R_R1 = {"title": "+2.000 PROJETOS DE MARCENARIA 🔨📐 Tenha acesso a ...",
        "link": f"https://www.facebook.com/{PUB_R}/posts/-2000-projetos-de-marcenaria-tenha-acesso-a-mais-de-2000-projetos-prontos-para-v/999900000000560077/"}
URI_R = f"https://www.facebook.com/{PUB_R}/"
ADS_R = [ad(AD_R, T_R, 9, pid=RIC, uri=URI_R, nombre="Loja Artesanato"), ad(2, T_R.replace("\n\n", "\n"), 3, pid=RIC, uri=URI_R, nombre="Loja Artesanato"),
         ad(3, T_R_V2, 2, pid=RIC, uri=URI_R, nombre="Loja Artesanato")]
dash = Dash([{"pagina": RIC, "anuncio": AD_R}])
codigo, r = pedidos_flujo.atender(Meta({RIC: ADS_R}), buscador([R_R1]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0].get("publicacion"), dash.posts[0].get("paginaPublica"), dash.posts[0].get("anuncio"), codigo],
      ["hallado", "999900000000560077", PUB_R, AD_R, 0], "Loja Artesanato: «Buscar su WhatsApp» halla SU publicacion aunque la pagina tenga otras versiones del mismo anuncio")
ok("EMPATE PERDONADO" in log[-1] and "999900000000560077" in log[-1], "el log del pedido la marca para revisarla a mano")


class MetaUno(Meta):
    def __init__(self, paginas, anuncios):
        super().__init__(paginas)
        self.anuncios = anuncios

    def anuncio(self, ad_id):
        self.llamadas.append(("un_anuncio", ad_id))
        return self.anuncios.get(ad_id)


# su anuncio no salio en la primera tanda de la pagina: se lee de su propia pagina y se busca con su texto
dash, pausas = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}]), []
meta = MetaUno({MENTA: [ADS_M[0]]}, {AD_ASTRAL: ADS_M[1]})
pedidos_flujo.atender(meta, buscador([R_FILO, R_ASTRAL]), "llave-x", radar.RateLimited, RED, pausa=lambda: pausas.append(1), log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0]["publicacion"], meta.llamadas], ["hallado", "999900000000531060", [MENTA, ("un_anuncio", AD_ASTRAL)]], "fuera de la primera tanda: se lee el anuncio suelto y se halla SU publicacion")
igual(len(pausas), 2, "con su pausa antes de esa segunda peticion")
# Meta limita la IP al leer el anuncio suelto: se corta y el pedido sigue pendiente
class MetaUnoLimitada(MetaUno):
    def anuncio(self, ad_id):
        raise radar.RateLimited("HTTP 429")


dash = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}])
codigo, r = pedidos_flujo.atender(MetaUnoLimitada({MENTA: [ADS_M[0]]}, {}), buscador([R_ASTRAL]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([codigo, dash.posts], [3, []], "Meta limito al leer el anuncio suelto: codigo 3 y no se manda nada")
# su anuncio no manda a WhatsApp, aunque otros de la pagina si: se dice de SU anuncio
landing = dict(ADS_M[1], cta_type="LEARN_MORE", link="https://www.mentora.example/astral")
dash = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}])
pedidos_flujo.atender(Meta({MENTA: [ADS_M[0], landing]}), buscador([R_ASTRAL]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0]["mensaje"], dash.posts[0]["anuncio"]], ["sin_whatsapp", "www.mentora.example", AD_ASTRAL], "su anuncio va a una landing: sin_whatsapp con su destino (no la publicacion de otra oferta)")
# los errores de un pedido por anuncio tambien llevan su anuncio (si no, el dash no los guarda donde la ficha los mira)
dash = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}])
pedidos_flujo.atender(Meta({MENTA: ADS_M}), sin_credito, "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0].get("anuncio")], ["error", AD_ASTRAL], "sin credito en Google: el error va con su anuncio")
dash = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}])
pedidos_flujo.atender(Meta({}, falla={MENTA: requests.ConnectionError("x")}), buscador([]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0].get("anuncio")], ["error", AD_ASTRAL], "un fallo de red: el error va con su anuncio")
# lo que manda el dash
d3 = Dash([])
d3.get = lambda url, headers=None, timeout=None: Resp(200, {"ok": True, "pedidos": [{"pagina": MENTA, "anuncio": AD_ASTRAL}, {"pagina": MENTA}, {"pagina": MENTA, "anuncio": "abc"},
                                                                                      {"pagina": MENTA, "anuncio": "12"}, {"pagina": MENTA, "modo": "es", "anuncio": AD_ASTRAL, "palabras": "x"}, {"pagina": MENTA, "anuncio": 999900000302813}]})
igual([(p["pagina"], p["anuncio"]) for p in pedidos_flujo.traer_pedidos("k", d3)], [(MENTA, AD_ASTRAL), (MENTA, ""), (MENTA, AD_ASTRAL)],
      "el anuncio llega solo con digitos; un anuncio raro (o en un pedido en espanol) no se atiende como si fuera de la pagina")

# endurecido tras la revision de seguridad (13-nov): datos raros de terceros no tumban la pasada ni gastan busquedas de pago
dash, consultas = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}]), []
pedidos_flujo.atender(Meta({MENTA: [ADS_M[0], dict(ADS_M[1], text="", body="")]}), buscador([R_ASTRAL], consultas), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], consultas], ["sin_publicacion", []], "su anuncio sin texto: sin_publicacion sin gastar busquedas en Google")
dash, consultas = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}]), []
pedidos_flujo.atender(MetaUno({MENTA: [ADS_M[0]]}, {AD_ASTRAL: dict(ADS_M[1], active=False)}), buscador([R_ASTRAL], consultas), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], consultas], ["sin_anuncio", []], "su anuncio leido suelto pero apagado: sin_anuncio, sin busquedas")
dash = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}])
pedidos_flujo.atender(Meta({MENTA: [ADS_M[0], dict(ADS_M[1], cta_type="LEARN_MORE", link="https://[roto")]}), buscador([]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([dash.posts[0]["estado"], dash.posts[0]["mensaje"]], ["sin_whatsapp", ""], "un destino con una direccion rota no tumba el pedido")


class MetaRara(MetaUno):
    def __init__(self, paginas, excepcion):
        super().__init__(paginas, {})
        self.excepcion = excepcion

    def anuncio(self, ad_id):
        raise self.excepcion


for exc in (flujo.SinDatos("la Biblioteca no trajo la pagina del anuncio"), KeyError("x")):
    dash = Dash([{"pagina": MENTA, "anuncio": AD_ASTRAL}, {"pagina": PAG}])
    codigo, r = pedidos_flujo.atender(MetaRara({MENTA: [ADS_M[0]], PAG: [ad(1, TEXTO, 6)]}, exc), buscador([R_TOP]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
    igual([(p["estado"], p.get("anuncio")) for p in dash.posts], [("error", AD_ASTRAL), ("hallado", None)], f"{type(exc).__name__} al leer el anuncio: ese pedido contesta error (con su anuncio) y el siguiente se atiende")
    igual(codigo, 1, "y la pasada sale con 1")

# textos de plantilla en la misma pagina: el pedido de una oferta no recibe la publicacion de la otra aunque pase el umbral
T_X = "Curso completo de crochet para principiantes con certificado y acceso de por vida. Aprende desde cero."
T_Y = "Curso completo de bordado para principiantes con certificado y acceso de por vida. Aprende desde cero."
R_Y = {"title": "Curso completo de bordado para principiantes con certificado", "link": f"https://www.facebook.com/{PUB_M}/videos/curso-bordado/9999000000573333/", "snippet": ""}
ADS_P = [ad(5555555555551, T_Y, 4, pid=MENTA, uri=f"https://www.facebook.com/{PUB_M}/", nombre="Cursos"), ad(5555555555552, T_X, 1, pid=MENTA, uri=f"https://www.facebook.com/{PUB_M}/", nombre="Cursos")]
dash = Dash([{"pagina": MENTA, "anuncio": "5555555555552"}, {"pagina": MENTA, "anuncio": "5555555555551"}])
pedidos_flujo.atender(Meta({MENTA: ADS_P}), buscador([R_Y]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
igual([(p["estado"], p.get("publicacion")) for p in dash.posts], [("sin_publicacion", None), ("hallado", "9999000000573333")], "plantilla: la de crochet no recibe la de bordado; la de bordado si recibe la suya")
ok("se parecian mas a otra oferta" in dash.posts[0]["mensaje"], "y el mensaje lo explica")

print(f"OK: {bien} comprobaciones de pedidos_flujo.py")


# ====== «Buscar en espanol»: paginas con anuncios en espanol que mandan a WhatsApp
import buscar_es


def ad_es(pid, nombre, texto, cuenta=2, cta="WHATSAPP_MESSAGE", link="https://api.whatsapp.com/send"):
    return {"archive_id": "1" + pid, "page_id": pid, "page_name": nombre, "text": texto, "body": texto, "collation_count": cuenta, "cta_type": cta, "link": link, "active": True}


igual(buscar_es.es_espanol("Aprende a hacer sólidos geométricos con tu hijo, descarga las actividades ahora"), True, "texto en espanol")
igual(buscar_es.es_espanol("Você recebe atividades prontas para ensinar muito mais fácil"), False, "texto en portugues")
igual(buscar_es.limpiar_palabras("sólidos geométricos; site:x.com -foo OR bar"), "sólidos geométricos site x com foo", "solo palabras y como mucho 6")


class MetaEs(Meta):
    def __init__(self, paginas, resultados):
        super().__init__(paginas)
        self.resultados, self.consultas = resultados, []

    def first_page(self, q, country, page_id=None, extra=None):
        self.consultas.append((q, country))
        return (self.resultados.get(q, []), None, False)


ANUN = [ad(1, "Ensinar sólidos geométricos ficou muito mais fácil e divertido com o kit", 22, pid="999900000584453", nombre="Prof. Marta Dias")]
ANUN[0]["cta_type"] = "LEARN_MORE"
ANUN[0]["link"] = "https://auladivertida.online/"
RES = {"sólidos geométricos": [
    ad_es("999900000371111", "Maestra Ana", "Aprende sólidos geométricos con tu hijo, descarga las actividades y escríbenos por WhatsApp", 5),
    ad_es("999900000382222", "Profe Luis", "Kit de sólidos geométricos para tu clase, escríbenos ahora", 2),
    ad_es("999900000393333", "Professora Bia", "Você recebe atividades de sólidos geométricos muito prontas", 9),
    ad_es("999900000404444", "Tienda Web", "Sólidos geométricos para tu aula con envío a todo el país", 4, cta="LEARN_MORE", link="https://tienda.example/"),
    ad_es("999900000485551", "Ambienta Home", "Juego de cubrecama para tu habitación, escríbenos por WhatsApp", 2),
    ad_es("999900000584453", "Prof. Marta Dias", "Kit con tu hijo", 3)]}
dash = Dash([])
dash.get = lambda url, headers=None, timeout=None: Resp(200, {"ok": True, "pedidos": [{"pagina": "999900000584453", "modo": "es", "palabras": "sólidos geométricos"}]})
meta = MetaEs({"999900000584453": ANUN}, RES)
codigo, r = pedidos_flujo.atender(meta, buscador([]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash)
c = dash.posts[0]
igual([c["estado"], c["modo"], [x["pagina"] for x in c["candidatas"]]], ["candidatas", "es", ["999900000371111", "999900000382222"]], "solo paginas en espanol con boton de WhatsApp, de mas a menos anuncios (sin la de portugues, la de landing ni la propia)")
igual(meta.consultas, [("sólidos geométricos", "ALL")], "con palabras escritas se busca una sola vez, en todos los paises")
igual(r["candidatas"], 1, "resumen")

# sin palabras: se arman con el anuncio mas repetido (la primera suele ser un verbo en portugues)
dash2 = Dash([])
dash2.get = lambda url, headers=None, timeout=None: Resp(200, {"ok": True, "pedidos": [{"pagina": "999900000584453", "modo": "es", "palabras": ""}]})
meta2 = MetaEs({"999900000584453": ANUN}, RES)
pedidos_flujo.atender(meta2, buscador([]), "llave-x", radar.RateLimited, RED, log=log.append, session=dash2, traduccion=(radar.query_words, radar.GLOSS))
igual([q for q, _ in meta2.consultas], ["Ensinar sólidos geométricos", "sólidos geométricos"], "dos consultas automaticas")
igual(len(dash2.posts[0]["candidatas"]), 2, "y halla las mismas")
print(f"OK: {bien} comprobaciones de pedidos_flujo.py (con la busqueda en espanol)")
