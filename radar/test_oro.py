"""Pruebas SIN red del oro en espanol y de los links de los anunciantes en espanol (radar.py, 2026-11-05): enlaces de WhatsApp y de la Biblioteca,
el texto de la columna de links, las busquedas guardadas en «Hueco Colombia», las semillas y la rotacion de paises del oro, el filtro, el copy en
espanol, la nota oro, la fila, los links de las filas de antes y una corrida completa con Meta simulado. Tambien el pais de una fila de oro en
seguimiento.py."""
import contextlib
import csv
import io
import json
import tempfile
import time
import types
from datetime import datetime
from pathlib import Path

import radar
import seguimiento

AHORA = time.time()


def anuncio(pid, nombre, dias, cuerpo, link="https://api.whatsapp.com/send?phone=5215512345678&text=Hola%20quiero", cta="WHATSAPP_MESSAGE", i=0):
    return {"archive_id": f"{pid}{i}", "collation_id": f"{pid}c{i}", "collation_count": 1, "page_id": pid, "page_name": nombre, "page_uri": "",
            "start": AHORA - dias * 86400, "end": None, "active": True, "body": cuerpo, "text": cuerpo, "link": link, "cta": "", "cta_type": cta,
            "page_like": 900, "video": False, "video_id": "", "sig": f"{pid}s{i}"}


# ---------------------------------------------------------------- enlaces
assert radar.link_whatsapp("https://api.whatsapp.com/send?phone=5215512345678&text=Hola") == "https://wa.me/5215512345678"
assert radar.link_whatsapp("https://wa.me/573001234567?text=quiero") == "https://wa.me/573001234567"
assert radar.link_whatsapp("https://chat.whatsapp.com/AbCdEf?x=1") == "https://chat.whatsapp.com/AbCdEf", "un grupo queda tal cual (sin parametros)"
assert radar.link_whatsapp("https://api.whatsapp.com/send") == "" and radar.link_whatsapp("https://tienda.com/?phone=5215512345678") == ""
assert radar.destino_de({"ads": [{"link": "https://pagina.com/x?utm=1"}, {"link": "https://wa.me/5491112345678"}], "top_host": "pagina.com"}) == "https://wa.me/5491112345678", \
    "si algun anuncio trae el WhatsApp, ese es el destino"
assert radar.destino_de({"ads": [{"link": "https://pagina.com/x?utm=1"}], "top_host": "pagina.com"}) == "https://pagina.com/x"
assert radar.destino_de({"ads": [{"link": "https://www.instagram.com/x"}], "top_host": "instagram.com"}) == "" and radar.destino_de({"ads": []}) == ""
bib = radar.biblioteca_de_pagina("123", "MX")
assert bib.startswith("https://www.facebook.com/ads/library/?") and "country=MX" in bib and "view_all_page_id=123" in bib and "active_status=active" in bib

g_co = radar.aggregate([anuncio("7", "Recetas Colombia", 45, "Recetario PDF de postres")])["7"]
a_co = radar.anunciante_es(g_co, "CO")
assert a_co == {"nombre": "Recetas Colombia", "pais": "CO", "dias": 45, "page_id": "7", "destino": "https://wa.me/5215512345678", "facebook": "", "whatsapp": True}, a_co
# Los anuncios de WhatsApp de Latinoamerica no traen el numero (api.whatsapp.com/send a secas, medido el 05/11/2026): queda su Facebook, con el boton de WhatsApp
sin_numero = dict(anuncio("9", "Dalux", 158, "Mega curso PDF", link="https://api.whatsapp.com/send"), page_uri="https://www.facebook.com/daluxstore6/")
a_fb = radar.anunciante_es(radar.aggregate([sin_numero])["9"], "CO")
assert a_fb["destino"] == "" and a_fb["facebook"] == "https://www.facebook.com/daluxstore6/" and a_fb["whatsapp"]
assert radar.links_texto([a_fb]).endswith("| Facebook (botón de WhatsApp): https://www.facebook.com/daluxstore6/")
assert radar.facebook_de({"ads": [{"page_uri": "javascript:alert(1)"}, {"page_uri": "https://evil.com/x"}]}) == "", "solo paginas de facebook.com"
txt = radar.links_texto([dict(a_co, etiqueta="Este anunciante: "), a_co, {"nombre": "Sin pagina", "pais": "MX", "dias": 9, "page_id": "", "destino": ""},
                         {"nombre": "Web", "pais": "MX", "dias": 31, "page_id": "8", "destino": "https://web.com/p"}])
lineas = txt.split("\n")
assert len(lineas) == 2, "la misma pagina no se repite y sin pagina no hay enlace: " + txt
assert lineas[0] == f"Este anunciante: Recetas Colombia (CO, 45 días): {radar.biblioteca_de_pagina('7', 'CO')} | WhatsApp: https://wa.me/5215512345678", lineas[0]
assert lineas[1].endswith("| página: https://web.com/p") and lineas[1].startswith("Web (MX, 31 días): ")
assert radar.links_texto([]) == "" and len(radar.links_texto([dict(a_co, page_id=str(i)) for i in range(20)]).split("\n")) == radar.LINKS_MAX

# ---------------------------------------------------------------- busquedas guardadas en «Hueco Colombia»
assert radar.consultas_de_hueco("1 anunciantes, el mayor con 29 dias (validado con IA en 1 ronda(s); busquedas 'guía vida católica', 'catequesis para católicos'; 8 anunciantes revisados)") \
    == ["guía vida católica", "catequesis para católicos"]
assert radar.consultas_de_hueco("0 anunciantes (busqueda 'recetas fit', traduccion automatica; revisar a mano)") == ["recetas fit"]
assert radar.consultas_de_hueco("PENDIENTE validar Colombia a mano") == [] and radar.consultas_de_hueco(None) == []

# ---------------------------------------------------------------- semillas y rotacion del oro
assert radar.consulta_oro("actividades para niños pdf") == "actividades niños pdf"
assert radar.consulta_oro("cuaderno de caligrafia") == "cuaderno caligrafia pdf", "sin palabra de material digital se le agrega pdf"
assert radar.consulta_oro("de la") == ""
# Solo PDF (dueno, 2026-11-06): «canva» y «editables» traen plantillas de Canva o Word; se quitan y se pide PDF
assert [radar.consulta_oro(s) for s in ("plantillas editables", "Plantillas editables Canva", "diseños cristianos canva", "plantillas pdf canva", "canva")] \
    == ["plantillas pdf", "plantillas pdf", "diseños cristianos pdf", "plantillas pdf", ""]
assert ("plantillas pdf", "plantillas") in radar.ORO_SEMILLAS and not any("editable" in s or "canva" in s for s, _n in radar.ORO_SEMILLAS)
assert not any(w in radar.fold(s["query"]).split() for s in radar.semillas_oro([{"es": "plantillas editables canva", "nicho": "x"}], [],
               {"oro_semillas": ["diseños cristianos canva", "plantillas ig iglesia"]}) for w in ("canva", "editable", "editables")), "ninguna busqueda de oro pide Canva"
assert all(1 <= len(radar.consulta_oro(s).split()) <= 3 for s, _n in radar.ORO_SEMILLAS), "consultas de 1 a 3 palabras: con 4 o mas Meta casi no devuelve nada"
sem = radar.semillas_oro([{"es": "recetas pdf", "nicho": "x"}, {"es": "Moldes de costura", "nicho": "costura"}, {"es": "", "nicho": "y"}],
                         [{"es": "receitas", "nicho": "z"}], {"oro_semillas": ["sermones listos pdf"]})
qs = [s["query"] for s in sem]
assert qs[:len(radar.ORO_SEMILLAS)] == [radar.consulta_oro(s) for s, _n in radar.ORO_SEMILLAS], "las fijas primero"
assert qs[len(radar.ORO_SEMILLAS)] == "sermones listos pdf", "luego las que propuso la IA"
assert qs.count("recetas pdf") == 1 and "moldes costura" in qs and "receitas pdf" in qs and len(qs) == len(set(qs))
assert next(s for s in sem if s["query"] == "moldes costura")["nicho"] == "costura"

st = {"keywords": {}}
p1 = radar.pick_oro(sem, st, 1)
assert p1 == [{"pais": "CO", "query": "recetas pdf", "nicho": "recetas", "label": "oro CO: recetas pdf"}] and st["oro_turno"] == 1, (p1, st)
st["keywords"]["oro CO: recetas pdf"] = datetime.now().isoformat(timespec="seconds")
p2 = radar.pick_oro(sem, st, 2)
assert [(o["pais"], o["query"]) for o in p2] == [("MX", "recetas pdf"), ("PE", "actividades niños pdf")] and st["oro_turno"] == 3, \
    "el pais rota y en una misma corrida no se repite la consulta"
st2 = {"keywords": {"oro CO: recetas pdf": datetime.now().isoformat(timespec="seconds")}, "oro_turno": 0}
assert radar.pick_oro(sem, st2, 1)[0]["query"] == "actividades niños pdf", "lo buscado en las ultimas horas cede el turno"
st3 = {"keywords": {f"oro CO: {s['query']}": datetime.now().isoformat(timespec="seconds") for s in sem}, "oro_turno": 0}
assert radar.pick_oro(sem, st3, 1)[0]["pais"] == "MX" and st3["oro_turno"] == 2, "un pais sin semillas libres cede el turno al siguiente"
assert radar.pick_oro(sem, {"keywords": {}}, 0) == [] and radar.pick_oro([], {"keywords": {}}, 3) == []
vieja = "2026-10-01T10:00:00"
st4 = {"keywords": {"oro CO: recetas pdf": vieja}, "oro_turno": 0}
assert radar.pick_oro(sem[:1], st4, 1)[0]["label"] == "oro CO: recetas pdf", "con todo ya buscado vuelve la de hace mas tiempo"

st5 = {"oro_semillas": ["recetas pdf"]}
radar.guardar_semillas_oro(st5, ["Recetas PDF", "sermones para predicar", "", "de la"])
assert st5["oro_semillas"] == ["recetas pdf", "sermones para predicar"], st5
radar.guardar_semillas_oro(st5, [f"tema {i} pdf" for i in range(300)])
assert len(st5["oro_semillas"]) == radar.ORO_SEMILLAS_MAX and st5["oro_semillas"][-1] == "tema 299 pdf"

assert radar.pais_de_fila({"Palabra clave": "oro MX: recetas pdf"}) == "MX" and radar.pais_de_fila({"Palabra clave": "nicho: receitas"}) == "BR"
assert radar.pais_de_fila({}) == "BR" and seguimiento.pais_de("oro PE: moldes pdf") == "PE" and seguimiento.pais_de("croche vestidos") == "BR"

# ---------------------------------------------------------------- filtro, copy en espanol y nota oro
SEEN = {"names": set(), "domains": set(), "ad_ids": set(), "bases": set(), "page_ids": set()}


def grupo(pid="1", nombre="Recetas Ya", dias=200, n=3, cuerpo="Recetario de postres en PDF, recibelo por WhatsApp", **k):
    return radar.aggregate([anuncio(pid, nombre, dias, cuerpo + f" {i}", i=i, **k) for i in range(n)])[pid]


assert radar.filtro_oro(grupo(), SEEN) == (True, "")
assert radar.filtro_oro(grupo(dias=20), SEEN) == (False, "20 dias (<30)")
assert radar.filtro_oro(grupo(link="https://tienda.com/p", cta="LEARN_MORE"), SEEN)[1] == "no vende por WhatsApp"
assert radar.filtro_oro(grupo(n=1), SEEN, min_activos=2)[1] == "1 anuncios activos (<2)"
assert radar.filtro_oro(grupo(cuerpo="Zapatos de cuero con envio gratis"), SEEN)[1].startswith("sus anuncios no hablan de material digital")
assert "servicio local" in radar.filtro_oro(grupo(nombre="Clínica Dental Sonrisas"), SEEN)[1]
assert radar.filtro_oro(grupo(), dict(SEEN, names={"recetas ya"}))[1] == "anunciante ya en la planilla"
assert radar.filtro_oro(grupo(), dict(SEEN, page_ids={"1"}))[1] == "anunciante ya en la planilla"
assert radar.filtro_oro(grupo(), dict(SEEN, ad_ids={"10"}))[1] == "anuncio ya registrado en la planilla"
assert "prohibido por Meta" in radar.filtro_oro(grupo(cuerpo="Guia PDF para bajar de peso"), SEEN)[1]
assert "no se modela" in radar.filtro_oro(grupo(cuerpo="Catalogo de productos PDF para revendedoras"), SEEN)[1]

lan = radar.landing_es(grupo(cuerpo="Recetario PDF de postres por solo $5.000 pesos colombianos"))
assert lan["pdf"] and not lan["course"] and not lan["launch"] and lan["funnel"] == "WhatsApp directo (api.whatsapp.com)" and lan["whatsapp"]
assert lan["precio_texto"] == "$5.000 pesos colombianos" and lan["usd"] is None, lan
assert radar.landing_es(grupo(cuerpo="Pack PDF de plantillas por US$ 7"))["usd"] == 7.0
assert radar.landing_es(grupo(cuerpo="Inscríbete a la clase gratuita en vivo"))["launch"]
assert radar.landing_es(grupo(cuerpo="Mega curso de pasteleria con certificado"))["course"]
assert radar.entregable_es(lan) == "PDF/material digital (el anuncio lo menciona)"
assert radar.nota_oro(grupo(dias=200, n=3), [], lan, True) == (9, "4+1+2+1+1=9")
assert radar.nota_oro(grupo(dias=40, n=1), [a_co], lan, False) == (4, "2+0+2+0+0=4"), "1 anuncio, 40 dias y con competencia en Colombia no llega a 6"
assert radar.nota_oro(grupo(dias=90, n=6), [None], lan, False)[0] == 7, "Colombia sin revisar no da el punto"
# anuncios en video (dueno, 2026-11-11): +1 si la mitad o mas de sus anuncios activos son video; un 5 llega asi a la nota minima del oro (6)
en_video = radar.aggregate([dict(anuncio("9", "Videos Ya", 40, f"Recetario PDF de postres {i}", i=i), video=bool(i % 2 == 0)) for i in range(2)])["9"]
assert radar.nota_oro(en_video, [None], lan, False) == (6, "2+1+2+0+0=5 +1 por anuncios en video = 6"), radar.nota_oro(en_video, [None], lan, False)
assert radar.nota_oro(grupo(dias=40, n=2), [None], lan, False) == (5, "2+1+2+0+0=5"), "sin video, la nota de siempre"
assert radar.nota_oro(dict(grupo(dias=200, n=6), video_activos=6), [], lan, True) == (10, "4+2+2+1+1=10"), "con 10 no pasa de 10"

# ---------------------------------------------------------------- la fila
item = {"pais": "MX", "query": "recetas pdf", "nicho": "recetas", "label": "oro MX: recetas pdf"}
full = grupo()
row = radar.fila_oro(full, item, lan, 9, "4+1+2+1+1=9", "0 anunciantes, el mayor con 0 dias (busqueda 'recetas pdf' en Colombia; la oferta es de Mexico)", True,
                     ("VERDE: modelar", "[verde] se vende en espanol"), "Este anunciante: ...", "05/11/2026")
assert set(radar.CAND_COLS) <= set(row) and set(row) == set(radar.CAND_COLS) | set(radar.flujo.COLUMNAS), sorted(set(row) ^ set(radar.CAND_COLS))
assert row["Palabra clave"] == "oro MX: recetas pdf" and row["Status"] == "nuevo" and row["Link de la página"] == "https://wa.me/5215512345678"
assert row["Justificación"].startswith("[radar.py] ORO en espanol (Mexico): 200 días") and "nota oro 4+1+2+1+1=9" in row["Justificación"]
assert row["Precio origen"] == "$5.000 pesos colombianos (moneda de Mexico)" and row["Ángulo"].startswith("es: Recetario")
assert radar.flujo.COLUMNAS[0] in row and row["Dominio"] == "no visible" and row[radar.LINKS_COL] == "Este anunciante: ..."
assert radar.pais_de_fila(row) == "MX"


# ---------------------------------------------------------------- semaforo: los anunciantes en espanol que ve quedan con sus links
class MetaEs:
    def collect(self, q, pais, pages=1, **k):
        return ([anuncio("55", "Sermones Ya", 120, "sermones listos en pdf")] if pais == "MX" else []), False


viejo_polite = radar.polite
radar.polite = lambda *a, **k: None
try:
    quienes = []
    vistos, completo = radar.demanda_en_espanol(MetaEs(), ["sermones pdf"], None, {}, quienes=quienes)
    assert vistos == [("Sermones Ya", "MX", 120)] and [q["page_id"] for q in quienes] == ["55"] and quienes[0]["destino"] == "https://wa.me/5215512345678"
    ideas = []
    radar.semaforo_de(MetaEs(), grupo(), lan, "x", "x", ["t"], "", "PDF", None, None, {}, False, en_es_propio=[("Recetas Ya", "MX", 200)], ideas=ideas)
    assert ideas == [], "sin IA no hay ideas"
    c, d = radar.semaforo_de(None, grupo(), lan, "x", "x", ["t"], "", "PDF", None, None, {}, False, en_es_propio=[("Recetas Ya", "MX", 200)])
    assert "[verde] se vende en espanol: Recetas Ya (MX, 200 dias)" in d, "una oferta de oro ya prueba la demanda en espanol, sin buscar mas (meta=None no se toca)"
finally:
    radar.polite = viejo_polite


# ---------------------------------------------------------------- links de las filas de antes
class HojaFalsa:
    def __init__(self, filas, con_col=True):
        self.cab = list(radar.SHEET_COLS) + ([radar.LINKS_COL] if con_col else [])
        self.filas, self.escrito = filas, {}

    def headers(self, tab):
        return self.cab

    def read_dicts(self, tab):
        return [dict(f, _row=i + 2) for i, f in enumerate(self.filas)]

    def update_cells(self, tab, cells):
        self.escrito.update(cells)


class MetaCo:
    llamadas = []

    def collect(self, q, pais, pages=1, **k):
        MetaCo.llamadas.append((q, pais))
        if "vacia" in q:
            return [], False
        return [anuncio("70", "Guia Catolica CO", 60, "Guía católica en PDF para catequesis", i=0),
                anuncio("71", "Hotel Campestre", 300, "Retiro católico en el hotel, reserva", link="https://hotel.com/r", cta="LEARN_MORE", i=0)], False


HUECO = "1 anunciantes, el mayor con 29 dias (validado con IA en 1 ronda(s); busquedas 'guía católica', 'catequesis para católicos'; 8 anunciantes revisados)"
filas = [{"Anunciante": "Vieja", "Status": "nuevo", "Hueco Colombia": HUECO},
         {"Anunciante": "Descartada", "Status": "descartada", "Hueco Colombia": HUECO},
         {"Anunciante": "Ya tiene", "Status": "nuevo", "Hueco Colombia": HUECO, radar.LINKS_COL: "algo"},
         {"Anunciante": "Sin busquedas", "Status": "vigilar", "Hueco Colombia": "PENDIENTE validar Colombia a mano"},
         {"Anunciante": "Vacia", "Status": "testiando", "Hueco Colombia": "0 anunciantes (busqueda 'consulta vacia'; revisar a mano)"},
         {"Anunciante": "Reciente", "Status": "nuevo", "Hueco Colombia": HUECO}]
radar.polite = lambda *a, **k: None
try:
    hoja = HojaFalsa(filas)
    args = types.SimpleNamespace(simular_sheets=False)
    assert radar.links_pendientes(hoja, MetaCo(), args, "05/11/2026", 2) == 2
    col = radar.sheets.col_letter(hoja.cab.index(radar.LINKS_COL))
    assert set(hoja.escrito) == {f"{col}7", f"{col}6"}, "las mas recientes primero, saltando descartadas, las que ya tienen links y las que no guardaron busquedas"
    assert hoja.escrito[f"{col}6"] == "ninguno en Colombia con 'consulta vacia' [busqueda de nuevo 05/11/2026]"
    t7 = hoja.escrito[f"{col}7"]
    assert t7.startswith("Guia Catolica CO (CO, 60 días): ") and "view_all_page_id=70" in t7 and "Hotel" not in t7, "el hotel no vende material digital"
    assert t7.endswith("[busqueda de nuevo 05/11/2026: anunciantes en Colombia con 'guía católica', 'catequesis para católicos'; no todos venden la misma oferta]"), t7
    assert MetaCo.llamadas.count(("guía católica", "CO")) == 1, "una busqueda repetida sale de la memoria de la corrida"
    assert radar.links_pendientes(HojaFalsa(filas, con_col=False), MetaCo(), args, "05/11/2026", 2) == 0, "sin la columna no hace nada"
    hoja2 = HojaFalsa(filas)
    assert radar.links_pendientes(hoja2, MetaCo(), types.SimpleNamespace(simular_sheets=True), "05/11/2026", 9) == 3 and hoja2.escrito == {}, "en simulacion no escribe"
finally:
    radar.polite = viejo_polite


# ---------------------------------------------------------------- una corrida completa con Meta simulado (oro en Mexico + Brasil sin nada)
ORO = {"10": [anuncio("10", "Recetas Ya", 200, f"Recetario de postres en PDF, más de 300 recetas {i}", i=i) for i in range(3)],
       "11": [anuncio("11", "Otro Zap", 40, "Recetas en PDF por WhatsApp", link="https://wa.me/5215599999999")],
       "12": [anuncio("12", "Zapatos MX", 300, "Recetas de zapatos con envio gratis", i=0)]}
CO = [anuncio("20", "Recetas Colombia", 45, "Recetas en PDF para emprender")]
# para la corrida en Colombia: un anunciante de 300 dias que vende por WhatsApp pero es una masterclass en vivo (no sirve: no sale en los links)
ORO["21"] = [anuncio("21", "Clase en Vivo CO", 300, "Recetas PDF de regalo en la masterclass gratuita", i=i) for i in range(2)]
ORO["22"] = [dict(anuncio("22", "Postres CO", 120, f"Recetario PDF de postres con 200 recetas {i}", link="https://api.whatsapp.com/send", i=i),
                  page_uri="https://www.facebook.com/postresco/") for i in range(6)]
# fin de ano: una oferta de temporada de 20 dias (el oro normal pide 30; el de temporada, --dias-temporada)
ORO["30"] = [anuncio("30", "Rituales Ya", 20, f"Rituales de año nuevo en PDF: abundancia, amor y salud, recíbelos por WhatsApp {i}", i=i) for i in range(6)]


class MetaOro:
    llamadas, co_oro = [], False

    def __init__(self, *a, **k):
        self.lsd = "x"

    def bootstrap(self, country):
        return "lsd", "doc"

    def collect(self, q, country, pages=1, page_id=None, **k):
        MetaOro.llamadas.append((q, country, page_id))
        if page_id:
            return ORO.get(page_id, []), True
        if "rituales" in q and country != "CO":
            return ORO["30"], False
        if country == "BR":
            return [], False
        if country == "CO":
            return (ORO["21"] + ORO["22"] + CO) if q == "recetas pdf" and MetaOro.co_oro else CO, False
        return ORO["10"] + ORO["11"] + ORO["12"], False

    def inspect_landing(self, link):
        return ""

    def pagina_anunciante(self, pid, country="BR", antes_de=None):
        MetaOro.llamadas.append(("pagina", pid, country))
        return {"total": 3, "ads": [{}], "hay_mas": False} if (pid, country) in (("11", "CO"), ("11", "PE")) else {"total": 0, "ads": [], "hay_mas": False}

    def first_page(self, q, country, page_id=None, extra=None):
        MetaOro.llamadas.append(("frase", q, country))
        return None


def ia_falsa(prompt, *a, **k):
    """Gemini simulado: producto digital si; ninguno de Colombia vende lo mismo; el semaforo contesta una LISTA con un objeto (como el 05/11 con EduCursos Pao)."""
    PREGUNTAS.append(prompt)
    if '"veredicto"' in prompt:           # mismo angulo ESTRICTO: nadie vende lo mismo
        return {"anunciantes": []}
    if '"claves"' in prompt:              # el nucleo de la oferta
        return {"producto": "recetario de postres", "producto_pt": "receitas de sobremesas", "angulo": "Postres para vender",
                "claves": [["receta", "recetas", "recetario"]], "claves_pt": [["receita"]], "busquedas_es": ["recetario postres", "recetas postres"],
                "busquedas_pt": ["receitas sobremesas"], "vecinos": ["postres en vaso"]}
    if '"modelable"' in prompt:
        return {"tipo": "pdf_digital", "modelable": True, "motivo": "recetario"}
    if "jefe de produccion" in prompt:
        return [{"esfuerzo": "bajo", "horas": 5, "como": "con IA", "encaje": "alto", "por_que": "gusta", "busquedas_es": ["postres en vaso pdf"]}]
    raise AssertionError("pregunta inesperada: " + prompt[:80])


PREGUNTAS = []


def corrida(tmp, st, con_ia=False, rescate=0, temporadas="", **extra):
    tmp = Path(tmp)
    (tmp / "out").mkdir(exist_ok=True)
    (tmp / "palabras.csv").write_text("pt,es,nicho,mercado\nreceitas faceis,,receitas,Brasil\n", encoding="utf-8")
    (tmp / "angulos.csv").write_text("angulo,tipo,origen\n", encoding="utf-8")
    (tmp / "temporadas.csv").write_text("palabra,es,nicho,temporada,prioridad,desde,hasta\n" + temporadas, encoding="utf-8")
    (tmp / "nichos.csv").write_text("palabra,es,nicho,origen\n", encoding="utf-8")
    (tmp / "state.json").write_text(json.dumps(st), encoding="utf-8")
    args = types.SimpleNamespace(
        simular_sheets=False, sin_sheets=True, tope_diario=0, modo="auto", min_nota=7, max_kw=2, temporadas=str(tmp / "temporadas.csv"), sin_ia=True, revisar_max=0,
        pages_co=1, pages=1, nota_sin_colombia=7, min_activos=8, keywords=str(tmp / "palabras.csv"), angulos=str(tmp / "angulos.csv"), max_temporada=0,
        max_seguidores=500_000, max_ofertas=6, max_adv=8, exigir_colombia=False, dias_temporada=14, cache=False, foco=str(tmp / "no-foco.csv"), max_foco=0,
        nichos=str(tmp / "nichos.csv"), max_nichos=0, min_promesas=0, max_oro=1,
        min_anuncios_oferta=0)       # la oferta principal de la pagina se prueba aparte (test_oferta.py)
    args.sin_ia, args.rescate_max = not con_ia, rescate
    vars(args).update(extra)
    MetaOro.llamadas = []
    viejo = (radar.Meta, radar.OUT, radar.STATE_FILE, radar.load_env, radar.polite, radar.ia.available, radar.ia.USO_FILE, radar.ia.ENV_FILE, radar.ia.ask_json)
    radar.Meta, radar.OUT, radar.STATE_FILE = MetaOro, tmp / "out", tmp / "state.json"
    radar.load_env, radar.polite, radar.ia.available = (lambda *a, **k: None), (lambda *a, **k: None), (lambda: con_ia)
    if con_ia:
        radar.ia.ask_json = ia_falsa
    radar.ia.USO_FILE, radar.ia.ENV_FILE = tmp / "ia_uso.json", tmp / "no-existe.env"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = radar.run(args)
    finally:
        (radar.Meta, radar.OUT, radar.STATE_FILE, radar.load_env, radar.polite, radar.ia.available, radar.ia.USO_FILE, radar.ia.ENV_FILE, radar.ia.ask_json) = viejo
    assert code == 0, buf.getvalue()
    filas = []
    for f in (tmp / "out").glob("candidatos_*.csv"):
        with open(f, encoding="utf-8-sig", newline="") as fh:
            filas += list(csv.DictReader(fh))
    return buf.getvalue(), filas, json.loads((tmp / "state.json").read_text(encoding="utf-8"))


with tempfile.TemporaryDirectory() as tmp:
    salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 1})
    assert "Oro en espanol (1 busquedas" in salida and "[MX] oro MX: recetas pdf" in salida, salida
    assert MetaOro.llamadas[0] == ("recetas pdf", "MX", None), "el oro va primero, en su pais"
    assert [f["Anunciante"] for f in filas] == ["Recetas Ya"], salida
    f = filas[0]
    assert f["Palabra clave"] == "oro MX: recetas pdf" and f["Nota"] == "8" and f["Link de la página"] == "https://wa.me/5215512345678", f
    assert f["Hueco Colombia"].startswith("1 anunciantes, el mayor con 45 dias (1 anunciantes de la busqueda, sin comparar con IA; busqueda 'recetas pdf' en Colombia; "
                                          "la oferta es de Mexico)"), f["Hueco Colombia"]
    links = f[radar.LINKS_COL].split("\n")
    assert links[0].startswith("Este anunciante: Recetas Ya (MX, 200 días): ") and "country=MX" in links[0] and links[0].endswith("WhatsApp: https://wa.me/5215512345678")
    assert links[1].startswith("Recetas Colombia (CO, 45 días): ") and "view_all_page_id=20" in links[1], links
    assert "[verde] se vende en espanol: Recetas Ya (MX, 200 dias)" in f["Semáforo (detalle)"] and "[verde] embudo por WhatsApp" in f["Semáforo (detalle)"]
    assert "[verde] entrega volumen: 300 piezas" in f["Semáforo (detalle)"], f["Semáforo (detalle)"]
    assert st["seen_pages"]["10"]["decision"] == "candidato no escrito 8" and st["seen_pages"]["11"]["decision"].startswith("nota oro 2+0+2+1+0=5"), st["seen_pages"]
    assert "12" not in st["seen_pages"], "el que no vende material digital ni se abre"
    assert st["oro_turno"] == 2 and "oro MX: recetas pdf" in st["keywords"] and st["etiquetas"]["oro MX: recetas pdf"]["candidatos"] == 1
    assert st["modos"]["oro"]["busquedas"] == 1 and st["modos"]["oro"]["candidatos"] == 1
    assert ("", "MX", "10") in MetaOro.llamadas and ("recetas pdf", "CO", None) in MetaOro.llamadas, "al anunciante se le miran todos sus anuncios en su pais"
    salida2, filas2, st2 = corrida(tmp, st)
    assert "[PE] oro PE: recetas pdf" in salida2 and not [x for x in filas2 if x["Anunciante"] != "Recetas Ya"], "la siguiente corrida va al siguiente pais y no repite al ya registrado"
with tempfile.TemporaryDirectory() as tmp:
    MetaOro.co_oro = True
    try:
        salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 0})
    finally:
        MetaOro.co_oro = False
    assert "[CO] oro CO: recetas pdf" in salida and [f["Anunciante"] for f in filas] == ["Postres CO"], salida
    f = filas[0]
    assert st["seen_pages"]["21"]["decision"].startswith("lanzamiento o clases en vivo") and f["Link de la página"] == "https://www.facebook.com/postresco/"
    assert f["Hueco Colombia"].startswith("2 anunciantes, el mayor con 120 dias (incluye a este anunciante: la oferta ya se vende en Colombia"), f["Hueco Colombia"]
    links = f[radar.LINKS_COL].split(chr(10))
    assert len(links) == 2 and links[0].startswith("Este anunciante: Postres CO (CO, 120 días)") and links[1].startswith("Recetas Colombia (CO, 45 días)"), \
        "la masterclass no sale en los links ni cuenta como competidor: " + str(links)
    assert "nota oro 3+2+2+1+0=8" in f["Justificación"], f["Justificación"]
with tempfile.TemporaryDirectory() as tmp:
    salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 1}, con_ia=True)
    f = filas[0]
    assert f["Anunciante"] == "Recetas Ya" and f["Nota"] == "9", (salida, f["Justificación"])
    assert f["Hueco Colombia"].startswith("0 anunciantes (mismo angulo estricto: 'recetario de postres', 0 de 1 que hablan del producto; "
                                          "busquedas 'recetario postres' en Colombia"), f["Hueco Colombia"]
    assert f[radar.LINKS_COL].endswith("[mismo angulo estricto: 'recetario de postres' " + f["Fecha"][:10] + "]"), f[radar.LINKS_COL]
    assert len(f[radar.LINKS_COL].split(chr(10))) == 2, "la IA dijo que nadie en Colombia vende lo mismo: solo queda el propio anunciante (y la nota)"
    assert "[verde] esfuerzo bajo (~5 h): con IA" in f["Semáforo (detalle)"] and "[verde] encaje en Colombia alto" in f["Semáforo (detalle)"],         "una respuesta en lista con un objeto vale: " + f["Semáforo (detalle)"]
    assert "Producto de referencia (Mexico)" in next(p for p in PREGUNTAS if '"veredicto"' in p)
    assert "de bajo ticket de Mexico (ya en espanol)" in next(p for p in PREGUNTAS if "jefe de produccion" in p)
    assert "Un anunciante de Mexico publica estos anuncios (espanol)" in next(p for p in PREGUNTAS if '"modelable"' in p)
    assert st["oro_semillas"] == ["postres en vaso pdf"], "lo que propone la IA alimenta las busquedas de oro"

# a UN punto del minimo: se miran sus paises antes de descartarlo («Otro Zap», nota oro 5 con minimo 6, tiene la misma pagina en CO y PE)
with tempfile.TemporaryDirectory() as tmp:
    salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 1}, rescate=1)
    assert "Rescate por paises de Otro Zap (nota 5, minimo 6): MX, CO, PE -> +2" in salida, salida
    zap = next(f for f in filas if f["Anunciante"] == "Otro Zap")
    assert zap["Nota"] == "7" and "nota oro 2+0+2+1+0=5 +2 por venderse en 3 paises = 7" in zap["Justificación"], zap["Justificación"]
    assert zap[radar.PAISES_COLS[0]] == "MX, CO, PE" and zap[radar.PAISES_COLS[1]].startswith("CO: este anunciante (3 anuncios activos): "), zap
    assert next(f for f in filas if f["Anunciante"] == "Recetas Ya")[radar.PAISES_COLS[0]] == "", "a las que pasaron solas se les miran los paises despues"
    assert not [x for x in MetaOro.llamadas if x[0] == "pagina" and x[1] == "10"], "la que paso sola no gasta peticiones en el rescate"
with tempfile.TemporaryDirectory() as tmp:
    viejo_pa = MetaOro.pagina_anunciante
    MetaOro.pagina_anunciante = lambda self, pid, country="BR", antes_de=None: {"total": 0, "ads": [], "hay_mas": False}
    try:
        salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 1}, rescate=1)
    finally:
        MetaOro.pagina_anunciante = viejo_pa
    assert [f["Anunciante"] for f in filas] == ["Recetas Ya"] and st["seen_pages"]["11"]["decision"] == "nota oro 2+0+2+1+0=5 (<6); se vende solo en MX",         st["seen_pages"]["11"]
with tempfile.TemporaryDirectory() as tmp:
    salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 1}, rescate=0)
    assert "Rescate" not in salida and [f["Anunciante"] for f in filas] == ["Recetas Ya"], "sin --rescate-max no se rescata"

# el semaforo dice el precio en moneda local en vez de «precio no visible»
c, d = radar.semaforo(grupo(), {**lan, "precio_texto": "$10.000"}, "x", None, [("A", "CO", 40)], None)
assert "[amarillo] precio en moneda local: $10.000 (sin pasar a US$)" in d and "precio no visible" not in d, d

# ---------------------------------------------------------------- el precio que mas se repite (caso «Aprende para Emprender», VE, 05/11/2026)
precio = lambda t, c=1: {"text": t, "body": t, "collation_count": c}
assert radar.precio_principal({"ads": [precio("Guia de Marquesas en PDF por solo 1 USD")] * 9 + [precio("Oferta especial: solo $7 USD")] * 4
                                       + [precio("solo $1")] * 4}) == ("1 USD", 1.0), "13 anuncios de la guia a 1 USD le ganan a 4 de otro producto a 7"
assert radar.precio_principal({"ads": [precio("Pack $7 USD", c=20), precio("Guia 1 USD")]}) == ("$7 USD", 7.0), "cada anuncio cuenta sus copias"
assert radar.precio_principal({"ads": [precio("Recetario $49 MXN"), precio("Recetario $49 MXN"), precio("otro $99")]}) == ("$49 MXN", None)
assert radar.precio_principal({"ads": [precio("sin precio")]}) == ("", None)
assert radar.precios_es("Por solo $5.000 pesos colombianos y US$ 3") == [("US$ 3", 3.0), ("$5.000 pesos colombianos", None)]
assert radar.precios_es("Precio: 7 dólares") == [("7 dólares", 7.0)]
assert radar.landing_es(grupo(cuerpo="Guia PDF por solo 1 USD"))["usd"] == 1.0

# ---------------------------------------------------------------- el nicho de la semilla solo si el anunciante es de ese tema
it = {"pais": "AR", "query": "actividades niños pdf", "nicho": "educacion infantil", "label": "oro AR: actividades niños pdf"}
assert radar.con_nicho_propio(grupo(cuerpo="Cuadernillo de actividades para niños en PDF"), it)["nicho"] == "educacion infantil"
assert radar.con_nicho_propio(grupo(cuerpo="Biblioteca digital de enfermeria en PDF"), it)["nicho"] == "por clasificar (salio con 'actividades niños pdf')", \
    "una biblioteca de enfermeria que salio con «actividades niños» no es educacion infantil"


# ---------------------------------------------------------------- la frase del mismo anuncio
CUERPO_900 = "🍰 APRENDE A PREPARAR MARQUESAS INDUSTRIALES DE AREQUIPE 🍰 Escríbenos por WhatsApp. La guía en PDF trae 25 recetas"
assert radar.frase_distintiva(CUERPO_900) == "APRENDE A PREPARAR MARQUESAS INDUSTRIALES DE AREQUIPE", radar.frase_distintiva(CUERPO_900)
assert radar.frase_distintiva("Escríbenos por WhatsApp para recibir tu guía hoy mismo") == "", "una frase que llama a escribir sale en cualquier anuncio"
assert radar.frase_distintiva("Hola 👋 compra ya") == "" and radar.frase_distintiva(None) == ""
assert radar.frase_del_anunciante([anuncio("1", "X", 40, "Hola", i=0), dict(anuncio("1", "X", 40, CUERPO_900, i=1), collation_count=5)])     == "APRENDE A PREPARAR MARQUESAS INDUSTRIALES DE AREQUIPE", "la del anuncio que mas escala"
assert radar.frase_del_anunciante([]) == ""

# ---------------------------------------------------------------- en que paises se vende cada oferta
assert radar.consultas_sin_ia({"Palabra clave": "oro MX: recetas pdf", "Hueco Colombia": "0 anunciantes (misma oferta segun la IA, de 3 anunciantes; busqueda 'recetas pdf' en Colombia)"}) \
    == {"es": "recetas pdf", "pt": ""}
assert radar.consultas_sin_ia({"Palabra clave": "nicho: receitas de bolo", "Hueco Colombia": HUECO}) == {"es": "guía católica pdf", "pt": "receitas de bolo"}
assert radar.consultas_sin_ia({"Palabra clave": "combo: planner de limpeza PDF + pronto para montar", "Hueco Colombia": ""}) == {"es": "", "pt": "planner de limpeza"}


NUCLEO_POSTRES = {"producto": "recetario de postres", "producto_pt": "receitas de sobremesas", "angulo": "Postres para vender desde casa",
                  "claves": [["postre", "postres"]], "claves_pt": [["sobremesa", "sobremesas"]], "busquedas_es": ["recetario postres"],
                  "busquedas_pt": ["receitas sobremesas"], "vecinos": []}


def ia_paises(prompt, *a, **k):
    PREGUNTAS.append(prompt)
    if '"veredicto"' in prompt:           # la IA dice que venden lo mismo los que se llaman «Igual ...»; los demas, algo vecino
        nombres = re.findall(r"^\[(\d+)\] (.*?):", prompt, re.M)
        ver = lambda n: "distinto" if "Pasteleria" in n else "igual" if n.startswith("Igual") else "vecino"      # un servicio nunca es «igual»
        return {"anunciantes": [{"i": int(i), "vende": "recetario de postres" if ver(n) == "igual" else "otra cosa", "veredicto": ver(n)} for i, n in nombres]}
    if '"claves"' in prompt:
        return dict(NUCLEO_POSTRES)
    raise AssertionError("pregunta inesperada: " + prompt[:80])


class MetaPaises:
    llamadas = []

    def pagina_de_anuncio(self, ad_id):
        return {"111": "900", "222": ""}.get(str(ad_id), "")

    def pagina_anunciante(self, pid, country="BR", antes_de=None):
        MetaPaises.llamadas.append(("pagina", pid, country))
        return {"total": 12, "ads": [{}], "hay_mas": False} if (pid, country) == ("900", "MX") else {"total": 0, "ads": [], "hay_mas": False}

    def collect(self, q, pais, pages=1, **k):
        if k.get("page_id"):                      # los anuncios de la pagina en su pais: de ahi sale la frase del mismo anuncio
            MetaPaises.llamadas.append(("anuncios", k["page_id"], pais))
            return [anuncio(k["page_id"], "Marquesas VE", 200, CUERPO_900, i=i) for i in range(3)], True
        MetaPaises.llamadas.append(("busca", q, pais))
        por_pais = {"CO": [anuncio("31", "Igual Colombia", 80, "Recetario de postres en PDF", i=i) for i in range(2)]
                          + [anuncio("32", "Otro Colombia", 200, "Recetario de postres para diabeticos PDF")]
                          # una pasteleria de 300 dias que la IA (simulada) daria por igual: vende el servicio, no material digital, y no cuenta
                          + [anuncio("35", "Igual Pasteleria", 300, "Postres artesanales de nuestra pastelería, pedidos a domicilio")],
                    "BR": [anuncio("33", "Igual Brasil", 150, "Receitas de sobremesas em PDF", link="https://loja.com.br/x?utm=1", cta="LEARN_MORE")],
                    "PE": [anuncio("34", "Igual Peru Nuevo", 10, "Recetario de postres PDF")]}       # 10 dias: no cuenta
        return por_pais.get(pais, []), False

    def first_page(self, q, country, page_id=None, extra=None):
        assert extra == {"search_type": "keyword_exact_phrase"} and q.startswith('"') and q.endswith('"'), (q, extra)
        MetaPaises.llamadas.append(("frase", q, country))
        por_pais = {"GT": [anuncio("900", "Marquesas GT", 200, CUERPO_900)],                 # la misma pagina corre el mismo anuncio en Guatemala
                    "CL": [anuncio("41", "Clon Chile", 45, CUERPO_900)],                     # otra pagina (una fanpage por pais) con 45 dias
                    "EC": [anuncio("42", "Clon Nuevo", 7, CUERPO_900)]}                      # otra pagina con 7 dias: todavia no prueba nada
        return por_pais.get(country, []), None, False


import re  # noqa: E402

viejo_ask, viejo_polite = radar.ia.ask_json, radar.polite
radar.ia.ask_json, radar.polite = ia_paises, (lambda *a, **k: None)
try:
    fila = {"Palabra clave": "receitas de sobremesa", "Anunciante": "Doces da Ana", "Nicho": "recetas", "Ángulo": "Postres para vender desde casa",
            "Entregable": "PDF", "Hueco Colombia": "", "Link del anuncio": "https://www.facebook.com/ads/library/?id=111", "Status": "nuevo",
            "Nota": "7", "Justificación": "[radar.py] 2+2+1+1+1=7."}
    paises, lineas = radar.paises_de_oferta(MetaPaises(), fila, "900", NUCLEO_POSTRES, True)
    assert paises == ["BR", "CO", "MX"], (paises, lineas)
    assert lineas[0].startswith("CO: mismo ángulo — Igual Colombia · recetario de postres, 80 días: ") and "view_all_page_id=31" in lineas[0] and lineas[0].endswith("WhatsApp: https://wa.me/5215512345678")
    assert lineas[1] == f"MX: este anunciante (12 anuncios activos): {radar.biblioteca_de_pagina('900', 'MX')}"
    assert ("busca", "recetario postres", "MX") not in MetaPaises.llamadas, "donde ya esta el mismo anunciante no hace falta buscar el angulo"
    assert ("busca", "receitas sobremesas", "BR") not in MetaPaises.llamadas, "Brasil es el pais de la oferta: no se busca alli"
    assert "Producto de referencia (Brasil)" in PREGUNTAS[-1]
    fila_mx = dict(fila, **{"Palabra clave": "oro MX: recetas pdf"})
    paises, lineas = radar.paises_de_oferta(MetaPaises(), fila_mx, "900", NUCLEO_POSTRES, True)
    assert paises == ["MX", "BR", "CO"] and lineas[0].startswith("BR: mismo ángulo — Igual Brasil · recetario de postres, 150 días: ") and lineas[0].endswith("| página: https://loja.com.br/x"), lineas
    paises, lineas = radar.paises_de_oferta(MetaPaises(), fila, "900", None, False)
    assert paises == ["BR", "MX"] and len(lineas) == 1, "sin IA solo cuenta el mismo anunciante"

    class HojaPaises(HojaFalsa):
        def __init__(self, filas):
            super().__init__(filas)
            self.cab += radar.PAISES_COLS

    filas = [dict(fila, Anunciante="Vieja"), dict(fila, Status="descartada"), dict(fila, **{radar.PAISES_COLS[0]: "BR"}),
             dict(fila, **{"Link del anuncio": "no visible"}), dict(fila, **{"Anunciante": "Sin pagina", "Link del anuncio": "https://www.facebook.com/ads/library/?id=222"}),
             dict(fila)]
    hoja = HojaPaises(filas)
    assert radar.paises_pendientes(hoja, MetaPaises(), types.SimpleNamespace(simular_sheets=False), "06/11/2026", 2, True) == 2
    c0, c1 = (radar.sheets.col_letter(hoja.cab.index(c)) for c in radar.PAISES_COLS)
    cn, cj = (radar.sheets.col_letter(hoja.cab.index(c)) for c in ("Nota", "Justificación"))
    assert set(hoja.escrito) == {f"{c}{n}" for c in (c0, c1, cn, cj) for n in (6, 7)}, sorted(hoja.escrito)
    assert (hoja.escrito[f"{cn}7"], hoja.escrito[f"{cn}6"]) == (9, 8), "2 paises mas: +2; 1 pais mas: +1"
    assert hoja.escrito[f"{cj}7"].endswith("| Paises 06/11/2026: se vende en 3 paises (BR, CO, MX): +2, nota 7 -> 9."), hoja.escrito[f"{cj}7"]
    assert radar.bono_paises(0) == 0 and radar.bono_paises(1) == 1 and radar.bono_paises(3) == 2 and radar.bono_paises(4) == 3 and radar.bono_paises(9) == 3
    hoja5 = HojaPaises([dict(fila, Nota="", **{"Justificación": "pegada a mano"})])
    radar.paises_pendientes(hoja5, MetaPaises(), types.SimpleNamespace(simular_sheets=False), "06/11/2026", 1, True)
    assert set(hoja5.escrito) == {f"{c0}2", f"{c1}2"}, "una fila sin nota (pegada a mano) no recibe nota inventada"
    assert hoja.escrito[f"{c0}7"] == "BR, CO, MX" and hoja.escrito[f"{c1}7"].endswith(
        "[revisado 06/11/2026: misma pagina en 5 paises; mismo angulo estricto: 'recetario de postres' con 'recetario postres' (CO, PE, AR, VE)]"), hoja.escrito[f"{c1}7"]
    assert not [x for x in MetaPaises.llamadas if x[0] == "frase"], "una oferta de Brasil (en portugues) no busca la frase en los paises hispanos"
    assert ("anuncios", "900", "BR") in MetaPaises.llamadas, "con IA se piden los anuncios de la pagina en su pais: de ahi sale el producto exacto"
    assert hoja.escrito[f"{c0}6"] == "BR, CO" and "no halle la pagina del anunciante" in hoja.escrito[f"{c1}6"], "sin pagina igual se mira el angulo"
    hoja2 = HojaPaises([dict(fila)])
    radar.paises_pendientes(hoja2, MetaPaises(), types.SimpleNamespace(simular_sheets=False), "06/11/2026", 2, False)
    assert hoja2.escrito[f"{c0}2"] == "BR, MX" and hoja2.escrito[f"{c1}2"].endswith("angulo sin comparar (sin IA)]")
    # una oferta de oro (VE): el MISMO ANUNCIO en otros paises, con otra pagina (una fanpage por pais) o con la misma
    MetaPaises.llamadas = []
    hoja4 = HojaPaises([dict(fila, **{"Palabra clave": "oro VE: recetas pdf"})])
    radar.paises_pendientes(hoja4, MetaPaises(), types.SimpleNamespace(simular_sheets=False), "06/11/2026", 1, True)
    assert hoja4.escrito[f"{c0}2"] == "VE, BR, CO, MX, CL, GT", hoja4.escrito
    cn, cj = (radar.sheets.col_letter(hoja4.cab.index(c)) for c in ("Nota", "Justificación"))
    assert hoja4.escrito[f"{cn}2"] == 10 and hoja4.escrito[f"{cj}2"] == (
        "[radar.py] 2+2+1+1+1=7. | Paises 06/11/2026: se vende en 6 paises (VE, BR, CO, MX, CL, GT): +3, nota 7 -> 10."), "5 paises mas: +3, con tope de 10"
    det = hoja4.escrito[f"{c1}2"].split(chr(10))
    assert det[3].startswith("CL: mismo anuncio — Clon Chile, 45 días: ") and "view_all_page_id=41" in det[3] and "country=CL" in det[3], det
    assert det[4] == f"GT: este anunciante, mismo anuncio: {radar.biblioteca_de_pagina('900', 'GT')}", det
    assert det[2].startswith("MX: este anunciante (12 anuncios activos)") and det[0].startswith("BR: mismo ángulo") and det[1].startswith("CO: mismo ángulo"), det
    assert det[-1].startswith("[revisado 06/11/2026: mismo anuncio («APRENDE A PREPARAR MARQUESAS INDUSTRIALES DE AREQUIPE») en 9 paises; misma pagina en 5 paises;"), det[-1]
    frases = [x for x in MetaPaises.llamadas if x[0] == "frase"]
    assert [x[2] for x in frases] == ["CO", "MX", "PE", "AR", "CL", "EC", "GT", "BO", "DO"], "el mismo anuncio se busca en los 9 paises hispanos (no en Brasil ni en el propio)"
    assert ("pagina", "900", "GT") not in MetaPaises.llamadas and ("pagina", "900", "CL") not in MetaPaises.llamadas,         "la pagina y el angulo solo se miran en los 6 paises principales"
    assert ("anuncios", "900", "VE") in MetaPaises.llamadas, "la frase sale de los anuncios de la pagina en su pais"

    MetaPaises.pagina_anunciante = lambda self, pid, country="BR", antes_de=None: None
    MetaPaises.first_page = lambda self, *a, **k: None           # la Biblioteca no trae resultados para la frase en ningun pais
    hoja3 = HojaPaises([dict(fila, **{"Hueco Colombia": "", "Palabra clave": "oro PE: x"})])
    radar.paises_pendientes(hoja3, MetaPaises(), types.SimpleNamespace(simular_sheets=False), "06/11/2026", 1, False)
    assert hoja3.escrito[f"{c0}2"] == "PE" and hoja3.escrito[f"{c1}2"].startswith("solo en PE [revisado 06/11/2026"), hoja3.escrito
    assert radar.paises_pendientes(HojaFalsa([dict(fila)]), MetaPaises(), types.SimpleNamespace(simular_sheets=False), "06/11/2026", 2, True) == 0, "sin las columnas no hace nada"

    # ------------------------------------------------------------ mismo angulo ESTRICTO (11/11/2026): piezas sueltas
    assert [radar.raiz(w) for w in ("rellenos", "Relleno", "recheios", "bolos", "flores", "pan", "crochet")] == \
        ["rellen", "rellen", "rechei", "bolo", "flor", "pan", "crochet"]
    g_cro = {"pieces": [{"text": "Patrones de PLANTAS en crochet, PDF"}, {"text": "Amigurumis"}]}
    assert radar.habla_del_nucleo(g_cro, [["crochet", "ganchillo"], ["planta", "suculenta"]])
    assert not radar.habla_del_nucleo({"pieces": [{"text": "Patrones de amigurumi en crochet"}, {"text": "plantas de interior"}]},
                                      [["crochet"], ["planta"]]), "las dos palabras tienen que estar en el MISMO anuncio"
    assert not radar.habla_del_nucleo({"pieces": [{"text": "Hojaldres con rellenos de crema"}]}, [["torta"], ["relleno"]])
    assert radar.habla_del_nucleo({"pieces": []}, []), "sin claves no se filtra"
    assert radar.grupos_que_dice({"pieces": [{"text": "Guia del Santo Rosario"}, {"text": "rosario meditado"}]}, [["rosario"], ["meditado"]]) == 2
    assert radar.grupos_que_dice({"pieces": [{"text": "Guia del Santo Rosario"}]}, [["rosario"], ["meditado"]]) == 1, "dice una parte: igual se juzga"
    assert radar.grupos_que_dice({"pieces": [{"text": "Tortas por encargo"}]}, [["relleno"]]) == 0
    assert radar.senal_digital({"pieces": [{"text": "Recetario de rellenos sin fuego"}], "ads": [{"link": "https://go.hotmart.com/O1"}]}), \
        "un checkout de Hotmart prueba que es digital aunque el texto no diga PDF (caso «Rellenos Sin Fuego», 11/11/2026)"
    assert not radar.senal_digital({"pieces": [{"text": "Tortas por encargo"}], "ads": [{"link": "https://mitienda.com"}]})
    assert radar.nombre_con_vende("Ana (oficial)", "recetario: 54 rellenos | bombones") == "Ana oficial · recetario 54 rellenos bombones"
    assert "|" not in radar.nombre_con_vende("Ana", "a | b: c [d]") and ":" not in radar.nombre_con_vende("Ana", "a | b: c [d]")
    assert radar.nombre_con_vende("Ana", "") == "Ana" and len(radar.nombre_con_vende("N" * 50, "v" * 50)) <= 58
    assert radar.co_puntos_de({"Hueco Colombia": "0 anunciantes (validado...)", "Palabra clave": "nicho: x"}) == 3
    assert radar.co_puntos_de({"Hueco Colombia": "2 anunciantes, el mayor con 45 dias (...)", "Palabra clave": "x"}) == 1
    assert radar.co_puntos_de({"Hueco Colombia": "0 anunciantes (busquedas 'a' sin resultados: hueco NO confirmado)", "Palabra clave": "x"}) == 2
    assert radar.co_puntos_de({"Hueco Colombia": "1 anunciantes, el mayor con 9 dias (incluye a este anunciante)", "Palabra clave": "oro CO: x"}) == 1
    assert radar.co_puntos_de({"Hueco Colombia": "SATURADO a mano", "Palabra clave": "x"}) is None
    assert radar.bono_viejo("[radar.py] 3+2+1+2+1=9. | Paises 11/11/2026: se vende en 6 paises (BR, CO, MX, PE, AR, VE): +3, nota 9 -> 10.") == 1, "el tope de 10 se comio 2"
    assert radar.bono_viejo("nota oro 2+0+2+1+0=5 +2 por venderse en 3 paises = 7") == 2 and radar.bono_viejo("[radar.py] 3+2+1+2+1=9.") == 0
    assert radar.semaforo_con_espanol("ROJO: no modelar", "[rojo] el anuncio registrado se apago", []) is None

    # ------------------------------------------------------------ las filas de antes se rehacen (caso Karen Confeiteira)
    class HojaAfines(HojaFalsa):
        def __init__(self, filas):
            super().__init__(filas)
            self.cab = ["Fecha", "Palabra clave", "Anunciante", "Nicho", "Ángulo", "Entregable", "Hueco Colombia", "Link del anuncio", "Nota",
                        "Justificación", "Status", "ID de la página", "Semáforo", "Semáforo (detalle)", radar.LINKS_COL] + radar.PAISES_COLS

    SEM = ("[verde] se vende en espanol: EduCursos Pao (CO, 521 dias), Hotmart afiliados (MX, 519 dias); [amarillo] 0 anuncios nuevos en 14 dias; "
           "[verde] embudo por WhatsApp; [verde] esfuerzo bajo (~4 h): con IA; [amarillo] precio no visible; [verde] encaje en Colombia alto: gusta; "
           "[verde] coleccion grande [semaforo 06/11/2026]")
    karen = {"Fecha": "11/11/2026", "Palabra clave": "nicho: apostila receitas confeitaria", "Anunciante": "Karen Confeiteira", "Nicho": "reposteria",
             "Ángulo": "Aprenda a preparar rellenos profesionales", "Entregable": "PDF", "Status": "nuevo", "ID de la página": "900",
             "Hueco Colombia": "0 anunciantes (validado con IA en 1 ronda(s); busquedas 'recetas rellenos', 'rellenos para tortas'; 8 anunciantes revisados)",
             "Link del anuncio": "https://www.facebook.com/ads/library/?id=111", "Nota": "10",
             "Justificación": "[radar.py] 3+2+1+2+1=9. algo | Paises 11/11/2026: se vende en 6 paises (BR, CO, MX, PE, AR, VE): +3, nota 9 -> 10.",
             "Semáforo": "AMARILLO: prueba barata antes de modelar", "Semáforo (detalle)": SEM, radar.LINKS_COL: "Cocina Creativa (MX, 243 días): https://x",
             radar.PAISES_COLS[0]: "BR, CO, MX, PE, AR, VE",
             radar.PAISES_COLS[1]: "CO: mismo ángulo — Click Emprende Fácil, 200 días: https://x\nMX: este anunciante (12 anuncios activos): https://y\n"
                                   "PE: mismo ángulo — Click Emprende Fácil, 200 días: https://x\n[revisado 11/11/2026: misma pagina en 5 paises; mismo angulo con 'r' (es)]"}
    oro_ve = dict(karen, **{"Palabra clave": "oro VE: recetas pdf", "Anunciante": "Marquesas", "Nota": "8", "Justificación": "[radar.py] ORO en espanol (Venezuela): x",
                            "Hueco Colombia": "1 anunciantes, el mayor con 45 dias (misma oferta segun la IA; la oferta es de Venezuela)",
                            radar.PAISES_COLS[0]: "", radar.PAISES_COLS[1]: "", radar.LINKS_COL: "Este anunciante: Marquesas (VE, 200 días): https://z\nOtro (CO, 40 días): https://w"})
    hecha = dict(karen, **{radar.LINKS_COL: "x\n[mismo angulo estricto: 'algo' 10/11/2026]"})
    hoja_a = HojaAfines([dict(karen, Status="descartada"), oro_ve, hecha, karen])
    MetaPaises.llamadas = []
    assert radar.afines_pendientes(hoja_a, MetaPaises(), types.SimpleNamespace(simular_sheets=False), "11/11/2026", 5, True) == 2
    col = lambda n, f: f"{radar.sheets.col_letter(hoja_a.cab.index(n))}{f}"
    esc = hoja_a.escrito
    assert not any(k.endswith(("2", "4")) and k[-2:] not in ("12", "14") for k in esc), "ni la descartada ni la que ya tiene la marca: " + str(sorted(esc))
    # Karen (fila 5): Colombia, paises, nota, semaforo y links con los que venden LO MISMO
    assert esc[col("Hueco Colombia", 5)].startswith("1 anunciantes, el mayor con 80 dias (mismo angulo estricto: 'recetario de postres'; busquedas 'recetario postres'; "), \
        esc[col("Hueco Colombia", 5)]
    assert "venden algo vecino: Otro Colombia (otra cosa)" in esc[col("Hueco Colombia", 5)] and esc[col("Hueco Colombia", 5)].endswith("[afines 11/11/2026]")
    assert esc[col(radar.PAISES_COLS[0], 5)] == "BR, CO, MX", esc[col(radar.PAISES_COLS[0], 5)]
    det = esc[col(radar.PAISES_COLS[1], 5)].split(chr(10))
    assert det[0].startswith("CO: mismo ángulo — Igual Colombia · recetario de postres, 80 días: ") and det[1] == "MX: este anunciante (12 anuncios activos): https://y", det
    assert det[2] == "[revisado 11/11/2026: mismo angulo estricto: 'recetario de postres' con 'recetario postres' (CO, PE, AR, VE); mismo anuncio y misma pagina del 11/11/2026]", det
    assert esc[col("Nota", 5)] == 9, "Colombia 3 -> 1 (-2) y bono de paises +1 -> +2 (+1): 10 -> 9"
    assert esc[col("Justificación", 5)].endswith("| Afines 11/11/2026 (mismo angulo estricto: 'recetario de postres'): Colombia 3 -> 1 pts; "
                                                 "paises BR, CO, MX, PE, AR, VE -> BR, CO, MX; bono de paises +1 -> +2; nota 10 -> 9."), esc[col("Justificación", 5)]
    assert esc[col("Semáforo (detalle)", 5)].startswith("[verde] se vende en espanol: Igual Colombia · recetario de postres (CO, 80 dias); [amarillo] 0 anuncios"), \
        esc[col("Semáforo (detalle)", 5)]
    assert esc[col("Semáforo (detalle)", 5)].endswith("[verde] coleccion grande [semaforo 06/11/2026]") and col("Semáforo", 5) not in esc, "el color no cambia: no se escribe"
    links = esc[col(radar.LINKS_COL, 5)].split(chr(10))
    assert links[0].startswith("Igual Colombia · recetario de postres (CO, 80 días)") and len(links) == 2, "la pasteleria (un servicio) no es igual: " + str(links)
    assert links[-1] == "[mismo angulo estricto: 'recetario de postres' 11/11/2026]" and not any("Cocina Creativa" in l for l in links), links
    # la de oro (fila 3): el propio anunciante sigue primero en los links; sin paises todavia (los hace paises_pendientes) y sin tocar su semaforo
    links_ve = esc[col(radar.LINKS_COL, 3)].split(chr(10))
    assert links_ve[0] == "Este anunciante: Marquesas (VE, 200 días): https://z" and links_ve[1].startswith("Igual Colombia"), links_ve
    assert col(radar.PAISES_COLS[0], 3) not in esc and col("Semáforo (detalle)", 3) not in esc and col("Nota", 3) not in esc, "oro: 0 -> 0 pts, sin cambio de nota"
    assert ("anuncios", "900", "VE") in MetaPaises.llamadas and ("anuncios", "900", "BR") in MetaPaises.llamadas, "cada fila se mira en su pais"
    assert radar.afines_pendientes(hoja_a, MetaPaises(), types.SimpleNamespace(simular_sheets=False), "11/11/2026", 5, False) == 0, "sin IA no hace nada"
finally:
    radar.ia.ask_json, radar.polite = viejo_ask, viejo_polite

# ---------------------------------------------------------------- sacar de OFERTAS lo que no sirve (06/11/2026), sin perderlo
class HojaSacar:
    """Planilla en memoria: pestanas = {nombre: [encabezados, fila, fila...]}."""
    def __init__(self, filas, perder_copia=False):
        cab = ["Fecha", "Anunciante", "Link del anuncio", "Status", radar.LINKS_COL]
        self.tabs, self.perder, self.borradas = {"OFERTAS": [cab] + [[f.get(c, "") for c in cab] for f in filas]}, perder_copia, []

    def headers(self, tab):
        return list(self.tabs[tab][0])

    def read_dicts(self, tab):
        if tab not in self.tabs:
            raise radar.sheets.SheetsError("Google Sheets 400: Unable to parse range")
        cab = self.tabs[tab][0]
        return [dict(zip(cab, r), _row=i + 2) for i, r in enumerate(self.tabs[tab][1:])]

    def ensure_tab(self, tab, cab):
        if tab in self.tabs:
            return False
        self.tabs[tab] = [list(cab)]
        return True

    def asegurar_columnas(self, tab, nombres):
        self.tabs[tab][0] += list(nombres)
        for r in self.tabs[tab][1:]:
            r += [""] * len(nombres)

    def append_dicts(self, tab, filas):
        if not self.perder:
            self.tabs[tab] += [[f.get(c, "") for c in self.tabs[tab][0]] for f in filas]

    def borrar_filas(self, tab, filas):
        self.borradas += sorted(filas, reverse=True)
        for f in sorted(filas, reverse=True):
            del self.tabs[tab][f - 1]


link = lambda i: f"https://www.facebook.com/ads/library/?id={i}"
filas_of = [{"Anunciante": "Buena", "Link del anuncio": link(11111), "Status": "nuevo"},
            {"Anunciante": "Plantillas Canva", "Link del anuncio": link(22222), "Status": "nuevo", radar.LINKS_COL: "x"},
            {"Anunciante": "Programas PC", "Link del anuncio": link(33333), "Status": "nuevo"},
            {"Anunciante": "Otra", "Link del anuncio": link(44444), "Status": "vigilar"}]
pedidas = [{"ad_id": "22222", "anunciante": "plantillas canva", "motivo": "plantillas de Canva que se compran en una pagina"},
           {"ad_id": "33333", "anunciante": "Programas PC", "motivo": "pack de programas: no se hace en PDF"},
           {"ad_id": "44444", "anunciante": "Nombre Equivocado", "motivo": "x"},
           {"ad_id": "99999", "anunciante": "No Existe", "motivo": "x"}]
h = HojaSacar(filas_of)
hechas, problemas = radar.sacar_ofertas(h, pedidas, "06/11/2026", simular=True)
assert hechas == ["Plantillas Canva", "Programas PC"] and len(problemas) == 2 and len(h.tabs["OFERTAS"]) == 5 and radar.FUERA_TAB not in h.tabs, "simular no toca nada"
hechas, problemas = radar.sacar_ofertas(h, pedidas, "06/11/2026")
assert hechas == ["Plantillas Canva", "Programas PC"], hechas
assert problemas == ["Nombre Equivocado: el anuncio no es de ese anunciante", "No Existe: no esta en OFERTAS"], problemas
assert [r["Anunciante"] for r in h.read_dicts("OFERTAS")] == ["Buena", "Otra"] and h.borradas == [4, 3], "se borran de abajo hacia arriba"
fuera = h.read_dicts(radar.FUERA_TAB)
assert [r["Anunciante"] for r in fuera] == ["Plantillas Canva", "Programas PC"] and fuera[0][radar.LINKS_COL] == "x", "la fila se copia entera"
assert fuera[0]["Por qué salió"] == "plantillas de Canva que se compran en una pagina" and fuera[1]["Fecha de salida"] == "06/11/2026"
# si la copia no llega a FUERA, no se borra nada
h2 = HojaSacar(filas_of, perder_copia=True)
try:
    radar.sacar_ofertas(h2, pedidas[:1], "06/11/2026")
    raise AssertionError("sin copia no se borra")
except radar.sheets.SheetsError as e:
    assert "no borro nada" in str(e) and len(h2.tabs["OFERTAS"]) == 5 and not h2.borradas
# lo que salio sigue contando para no repetir
assert radar.leer_fuera(HojaSacar([])) == [] and len(radar.leer_fuera(h)) == 2
seen = radar.read_seen([])
for r in radar.leer_fuera(h):
    radar.add_seen(seen, r)
assert "22222" in seen["ad_ids"] and "programas pc" in seen["names"]

# ---------------------------------------------------------------- lo que el radar ya no mete (regla del dueno, 06/11/2026)
canva = lambda cta, link_: radar.aggregate([anuncio("7", "Artes Prontas", 90, f"Mais de 500 artes editaveis no Canva {i}", link=link_, cta=cta, i=i)
                                             for i in range(4)])["7"]
assert radar.plantilla_sin_whatsapp(canva("LEARN_MORE", "https://artes.com.br/checkout")), "plantillas de Canva que se compran en una pagina: fuera"
assert not radar.plantilla_sin_whatsapp(canva("WHATSAPP_MESSAGE", "https://api.whatsapp.com/send")), "por WhatsApp si sirven: ahi se sacan los entregables"
assert not radar.plantilla_sin_whatsapp(grupo(cuerpo="Recetario PDF de postres", link="https://x.com/p", cta="LEARN_MORE")), "un PDF que no es plantilla no cae"
assert "no se modela" in radar.filtro_oro(grupo(cuerpo="Mega pack de programas para PC, instaladores y drivers en PDF"), SEEN)[1]
assert radar.NO_MODELABLE.search(radar.fold("Pacote de programas para PC com ativador e drivers"))
pd_prompt = []
viejo_ask = radar.ia.ask_json
radar.ia.ask_json = lambda prompt, *a, **k: pd_prompt.append(prompt) or {"tipo": "pdf_digital", "modelable": True, "motivo": "x"}
try:
    radar.ia.producto_digital("plantillas", ["Artes no Canva"], "", embudo="Pagina de producto + Kiwify")
finally:
    radar.ia.ask_json = viejo_ask
assert "Como se compra: Pagina de producto + Kiwify" in pd_prompt[0] and "solo son modelables si se compran por WhatsApp" in pd_prompt[0]

# ---------------------------------------------------------------- fin de ano (dueno, 2026-11-11: «ofertas que se vendan muy bien antes de que termine el ano»)
from datetime import date, timedelta  # noqa: E402

H = date(2026, 11, 11)
assert radar.dias_para_vender("oro CO: novena de aguinaldos", H) == ("Novena de aguinaldos", 35)
assert radar.dias_para_vender("temporada: simpatias de ano novo", H) == ("Ano Nuevo", 50)
assert radar.dias_para_vender("oro MX: agüeros de año nuevo", H) == ("Ano Nuevo", 50)
assert radar.dias_para_vender("oro PE: planner 2027 pdf", H) == ("Comienzo de ano", 65)
assert radar.dias_para_vender("temporada: jejum de daniel PDF", H) == ("Comienzo de ano", 65)
assert radar.dias_para_vender("temporada: panetone caseiro", H)[0] == "Navidad" and radar.dias_para_vender("calendario de adviento", H)[0] == "Navidad"
assert radar.dias_para_vender("receitas fit", H) is None and radar.dias_para_vender("novena a san judas", H) is None
# descanso corto en temporada: las de Navidad se agotaron el 2-4/11 y con 21 dias volvian hacia el 25
assert radar.dias_de_descanso("temporada: natal PDF", H) == radar.DESCANSO_TEMPORADA_DIAS == 3
assert radar.dias_de_descanso("temporada: halloween PDF", H) == radar.DESCANSO_DIAS, "Halloween ya paso: la proxima esta a casi un ano"
assert radar.dias_de_descanso("nicho: receitas fit", H) == radar.DESCANSO_DIAS and radar.dias_de_descanso("temporada: natal PDF", date(2026, 7, 1)) == radar.DESCANSO_DIAS
hace5 = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")
st_a = {"etiquetas": {lab: {"busquedas": 9, "candidatos": 1, "seguidas_sin": 2, "nuevos_ultima": 0, "ultima": hace5}
                      for lab in ("temporada: natal PDF", "nicho: receitas fit", "oro CO: rituales de año nuevo")}}
viejo_dd = radar.dias_de_descanso
radar.dias_de_descanso = lambda lab, hoy=None: viejo_dd(lab, H)
try:
    assert not radar.busqueda_agotada("temporada: natal PDF", st_a) and not radar.busqueda_agotada("oro CO: rituales de año nuevo", st_a), \
        "en temporada una busqueda agotada vuelve a los 3 dias"
    assert radar.busqueda_agotada("nicho: receitas fit", st_a), "lo demas sigue descansando 21 dias"
finally:
    radar.dias_de_descanso = viejo_dd

# oro de temporada: la columna «es» de temporadas.csv con la ventana abierta, tal cual, por prioridad, con los dias de temporada
filas_t = radar.read_seasons(radar.HERE / "temporadas.csv")
sem_t = radar.semillas_oro_temporada(filas_t, datetime(2026, 11, 11, 12))
qs_t = [s["query"] for s in sem_t]
assert qs_t[0] == "novena de aguinaldos" and "rituales de año nuevo" in qs_t and "agüeros de año nuevo" in qs_t, qs_t
assert "ayuno de daniel" not in qs_t, "su ventana abre el 1 de diciembre"
assert qs_t.index("anchetas navideñas") < qs_t.index("bordado navideño pdf"), "prioridad 1 antes que 2"
assert all(s["min_days"] == radar.SEASON_MIN_DAYS for s in sem_t) and len(qs_t) == len(set(qs_t)) and all(len(q.split()) <= 4 for q in qs_t)
assert all(radar.dias_para_vender(q, H) for q in qs_t), "toda consulta de temporada nombra su fecha (descanso corto y semaforo con los dias que quedan)"
assert radar.semillas_oro_temporada(filas_t, datetime(2026, 3, 1, 12)) == [], "fuera de temporada no hay"
assert radar.semillas_oro_temporada([{"es": "  Rituales  de AÑO nuevo ", "nicho": "", "prioridad": 1, "desde": "01-01", "hasta": "12-31"}])[0]["query"] \
    == "rituales de año nuevo"
p_t = radar.pick_oro(sem_t, {"keywords": {}}, 1)
assert p_t[0]["label"] == "oro CO: novena de aguinaldos" and p_t[0]["min_days"] == radar.SEASON_MIN_DAYS, p_t
assert "min_days" not in radar.pick_oro(sem, {"keywords": {}}, 1)[0], "el oro normal sigue pidiendo 30 dias"
assert radar.filtro_oro(grupo(dias=20), SEEN, min_dias=14) == (True, "") and radar.filtro_oro(grupo(dias=10), SEEN, min_dias=14)[1] == "10 dias (<14)"

# corrida: el oro de temporada va primero, con 14 dias registra una oferta de 20; el oro normal no repite su consulta
with tempfile.TemporaryDirectory() as tmp:
    salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 1},
                                temporadas="simpatias de ano novo,rituales de año nuevo,año nuevo rituales,ano novo,1,01-01,12-31\n",
                                max_oro_temporada=1, max_oro=0)
    assert "Oro de temporada en espanol (1 busquedas; ofertas de WhatsApp con 14+ dias" in salida and "oro MX: rituales de año nuevo" in salida, salida
    assert "Oro en espanol (" not in salida, "con --max-oro 0 no hay oro normal"
    assert MetaOro.llamadas[0][:2] == ("rituales de año nuevo", "MX"), MetaOro.llamadas[:3]
    assert [f["Anunciante"] for f in filas] == ["Rituales Ya"], salida
    assert filas[0]["Palabra clave"] == "oro MX: rituales de año nuevo" and "Ano Nuevo en" in filas[0]["Semáforo (detalle)"], filas[0]
    assert st["oro_turno"] == 2
with tempfile.TemporaryDirectory() as tmp:
    salida, filas, st = corrida(tmp, {"keywords": {}, "seen_pages": {}, "oro_turno": 1}, max_oro_temporada=1, max_oro=1)
    assert "Oro de temporada en espanol (0 busquedas" in salida and "ninguna libre" in salida and "[MX] oro MX: recetas pdf" in salida, \
        "sin filas de temporada abiertas el oro normal sigue igual"

print("TODO OK (oro en espanol y links: enlaces, columna de links, busquedas guardadas, semillas, rotacion, filtro, copy, nota, fila, filas de antes, "
      "corrida completa, pais en seguimiento, precio que mas se repite, nicho propio y paises que venden)")
