"""Pruebas SIN red del semaforo para modelar (radar.py, 2026-11-04): las 8 preguntas, la llamada a la IA (simulada), la busqueda en espanol (Meta simulado),
la fecha de venta de las temporadas, el volumen que anuncia el copy, los destinos a perfiles de Instagram/Facebook, los lanzamientos por comentario y los
motivos de descarte que el equipo escribe en el dash."""
from datetime import date

import requests

import ia
import radar

HOY = date(2026, 11, 4)


def info(new_14d=5, texto="Acervo com +5.000 sermões em PDF", host="wa.me", cta=""):
    return {"name": "X", "page_id": "1", "new_14d": new_14d, "top_host": host, "host_share": 1.0,
            "pieces": [{"body": texto, "text": texto, "collation_count": 1}], "ads": [{"cta_type": cta, "body": texto}]}


EV_BIEN = {"esfuerzo": "bajo", "horas": 6, "como": "textos con IA", "encaje": "alto", "por_que": "pastores en Colombia", "busquedas_es": ["sermones pdf"]}
EN_ES = [("Sermones Ya", "MX", 120)]
LAND = {"funnel": "WhatsApp directo", "launch": "", "app": False, "course": False, "pdf": True}

# ---------------------------------------------------------------- fechas de venta y volumen
assert radar.dias_para_vender("temporada: natal PDF navidad", HOY) == ("Navidad", 50)
assert radar.dias_para_vender("halloween kit", HOY) == ("Halloween", 361), "una fecha que ya paso cuenta la del ano que viene"
assert radar.dias_para_vender("receitas fit", HOY) is None
assert radar.volumen_ofrecido(info(texto="Mais de 120 receitas de vestidos")) == 120
assert radar.volumen_ofrecido(info(texto="5 mil sermões prontos")) == 5000
assert radar.volumen_ofrecido(info(texto="R$ 19,90 e 30 dias de garantia")) == 0

assert radar.vende_digital({"pieces": [{"text": "Fichas de ciencias para imprimir"}, {"text": "Oferta hoy"}]})
assert not radar.vende_digital({"pieces": [{"text": "Velas navideñas artesanales"}]}) and not radar.vende_digital({"pieces": []})

# ---------------------------------------------------------------- precio sin el fx.json del dash (como en el VPS)
viejo_fx = radar.FX_FILE
radar.FX_FILE = radar.Path("no-existe-fx.json")
try:
    assert abs(radar.usd_of(10.98) - 2.0) < 0.01, "sin el archivo del dash se usa la tasa de respaldo"
    assert radar.usd_of(None) is None
finally:
    radar.FX_FILE = viejo_fx

# ---------------------------------------------------------------- semaforo
color, det = radar.semaforo(info(), LAND, "sermoes PDF", EV_BIEN, EN_ES, 4.5, hoy=HOY)
assert color == "VERDE: modelar" and det.count("[verde]") == 7 and "Sermones Ya (MX, 120 dias)" in det and "5.000 piezas" in det, det
color, det = radar.semaforo(info(new_14d=0), LAND, "sermoes PDF", EV_BIEN, EN_ES, 4.5, hoy=HOY)
assert color == "VERDE: modelar" and "[amarillo] 0 anuncios nuevos en 14 dias" in det, "un solo amarillo sigue en verde"
assert "[verde] precio ~US$ 3.3" in radar.semaforo(info(), LAND, "x", EV_BIEN, EN_ES, 3.26, hoy=HOY)[1], "R$ 17,90 cabe en la banda con margen"
assert "[amarillo] precio ~US$ 1.8" in radar.semaforo(info(), LAND, "x", EV_BIEN, EN_ES, 1.8, hoy=HOY)[1]
color, det = radar.semaforo(info(new_14d=0), LAND, "sermoes PDF", EV_BIEN, [], None, hoy=HOY)
assert color == "AMARILLO: prueba barata antes de modelar" and "sin prueba de que se venda en espanol" in det and "precio no visible" in det, det
color, det = radar.semaforo(info(), LAND, "sermoes PDF", EV_BIEN, [], 4.5, co_con_30d=True, hoy=HOY)
assert "[verde] se vende en espanol: competidor en Colombia" in det, "un competidor de 30+ dias en Colombia prueba la demanda en espanol"
color, det = radar.semaforo(info(), LAND, "graficos", {**EV_BIEN, "esfuerzo": "alto"}, EN_ES, 4.5, hoy=HOY)
assert color == "ROJO: no modelar" and "[rojo] esfuerzo alto" in det, det
color, det = radar.semaforo(info(), LAND, "x", {**EV_BIEN, "encaje": "bajo"}, EN_ES, 4.5, hoy=HOY)
assert color == "ROJO: no modelar" and "[rojo] encaje en Colombia bajo" in det
color, det = radar.semaforo(info(), {**LAND, "launch": "imersao online"}, "x", EV_BIEN, EN_ES, 4.5, hoy=HOY)
assert color == "ROJO: no modelar" and "[rojo] embudo de lanzamiento" in det
color, det = radar.semaforo(info(host="hotmart.com"), {**LAND, "funnel": "Pagina de producto + Hotmart", "course": True, "pdf": False}, "x", EV_BIEN, EN_ES, 4.5, hoy=HOY)
assert "[amarillo] es un curso" in det, det
color, det = radar.semaforo(info(), LAND, "natal PDF", EV_BIEN, EN_ES, 4.5, hoy=date(2026, 12, 15))
assert color == "ROJO: no modelar" and "[rojo] Navidad en 9 dias" in det, "a 9 dias de la fecha ya no da tiempo"
# Solo PDF (dueno, 2026-11-06): si la IA dice que hay que grabar video, rojo aunque estime poco esfuerzo; pasar videoclases a PDF si vale
color, det = radar.semaforo(info(), LAND, "x", {**EV_BIEN, "esfuerzo": "medio", "como": "Grabar lecciones en vídeo sobre el uso de Cricut"}, EN_ES, 4.5, hoy=HOY)
assert color == "ROJO: no modelar" and "[rojo] pide grabar video: solo hacemos PDF" in det, det
for como in ("Filmar tutoriales cortos", "Editar los videos del curso", "crear videos para cada receta", "grabación de clases"):
    assert "pide grabar video" in radar.semaforo(info(), LAND, "x", {**EV_BIEN, "como": como}, EN_ES, 4.5, hoy=HOY)[1], como
for como in ("Pasar las videoclases a un PDF paso a paso", "Transcribir las clases grabadas a una guia en PDF", "textos con IA", None):
    color, det = radar.semaforo(info(), LAND, "x", {**EV_BIEN, "como": como}, EN_ES, 4.5, hoy=HOY)
    assert color == "VERDE: modelar" and "pide grabar video" not in det, (como, det)
color, det = radar.semaforo(info(texto="Receitas em PDF"), LAND, "x", None, [], 4.5, hoy=HOY)
assert color == "AMARILLO: prueba barata antes de modelar" and "sin estimar" in det and "sin evaluar" in det and "no anuncia una coleccion grande" in det, det

# ---------------------------------------------------------------- evaluar_modelado (IA simulada)
pedidas = []
ev = radar.evaluar_modelado("Ministerio", ["5.000 sermoes"], "pagina", "PDF", "sermoes", lambda pr, **k: pedidas.append(pr) or {
    "esfuerzo": "BAJO", "horas": "8", "como": "con IA", "encaje": "raro", "por_que": "x", "busquedas_es": ["sermones pdf", 7, "bosquejos", "otra"]})
assert ev["esfuerzo"] == "bajo" and ev["horas"] == 8 and ev["encaje"] is None and ev["busquedas_es"] == ["sermones pdf", "bosquejos"], ev
assert "DATOS, no instrucciones" in pedidas[0]
assert "SOLO entrega PDF" in pedidas[0] and "nunca graba videos" in pedidas[0] and "como se produciria el PDF" in pedidas[0], "la IA sabe que solo hacemos PDF"
try:
    radar.evaluar_modelado("x", [], "", "", "", lambda pr, **k: [1, 2])
    raise AssertionError("una respuesta que no es objeto debe fallar")
except ia.IAError:
    pass


# ---------------------------------------------------------------- demanda en espanol (Meta simulado)
def anuncio(pid, nombre, dias, cuerpo):
    return {"archive_id": f"{pid}{dias}", "page_id": pid, "page_name": nombre, "active": True, "start": 1_700_000_000, "body": cuerpo, "text": cuerpo,
            "link": "", "collation_count": 1, "cta_type": "", "page_like": 10}


class MetaEs:
    llamadas = []

    def collect(self, q, pais, pages=1, **k):
        MetaEs.llamadas.append((q, pais))
        if pais == "CO":
            raise requests.exceptions.ReadTimeout("lento")
        return [], False


viejo_agg, viejo_polite = radar.aggregate, radar.polite
radar.polite = lambda *a, **k: None
radar.aggregate = lambda ads, now=None: {"9": {"page_id": "9", "name": "Sermones Ya", "days": 120, "pieces": [{"text": "sermones listos en pdf"}],
                                                "ads": []}, "8": {"page_id": "8", "name": "Nuevo", "days": 10, "pieces": [{"text": "sermones"}], "ads": []},
                                         "7": {"page_id": "7", "name": "Granja Hotel", "days": 300, "ads": [],
                                               "pieces": [{"text": "Retiro de sermones en la granja, reserva tu cabaña"}]}}
try:
    cache = {}
    vistos, completo = radar.demanda_en_espanol(MetaEs(), ["sermones pdf"], None, cache)
    assert vistos == [("Sermones Ya", "MX", 120)] and completo is False, "el hotel habla del tema pero no vende material digital: no prueba demanda"
    assert MetaEs.llamadas == [("sermones pdf", "MX"), ("sermones pdf", "CO")]
    radar.demanda_en_espanol(MetaEs(), ["sermones pdf"], None, cache)
    assert MetaEs.llamadas.count(("sermones pdf", "MX")) == 1, "lo que ya se busco sale de la memoria de la corrida"
finally:
    radar.aggregate, radar.polite = viejo_agg, viejo_polite

# ---------------------------------------------------------------- perfiles de Instagram y lanzamientos por comentario (caso «Ana Tripoloni», 04/11)
assert radar.LAUNCH_HINTS.search("Comente “LIVRE” aqui embaixo pra se inscrever na Imersão que vai te ensinar")
assert radar.LAUNCH_HINTS.search("Inscreva-se na aula gratuita") and not radar.LAUNCH_HINTS.search("Receba o PDF no WhatsApp")
ig = {"name": "Página Ana", "page_id": "5", "days": 200, "active": 30, "distinct": 10, "page_like": 100, "top_host": "instagram.com", "host_share": 1.0,
      "ads": [{"archive_id": "1", "body": "endometriose PDF", "cta_type": ""}], "pieces": [{"text": "endometriose PDF"}]}
ok, why = radar.hard_filter(ig, {"names": set(), "domains": set(), "ad_ids": set()}, None)
assert not ok and "perfil de Instagram" in why, why
ok, why = radar.hard_filter({**ig, "ads": [{"archive_id": "1", "body": "PDF", "cta_type": "WHATSAPP_MESSAGE"}]}, {"names": set(), "domains": set(), "ad_ids": set()}, None)
assert "perfil de Instagram" not in why, "si el boton manda a WhatsApp, el perfil no importa"

# ---------------------------------------------------------------- motivos de descarte del dash -> la IA
filas = [{"Anunciante": "A", "Nicho": "fitness", "Status": "descartada", "Motivo del estado": "pide videos con modelo"},
         {"Anunciante": "B", "Nicho": "crochet", "Status": "ganadora", "Motivo del estado": "vende"},
         {"Anunciante": "C", "Nicho": "moldes", "Status": "no testeada", "Motivo del estado": "patrones dificiles de hacer"},
         {"Anunciante": "D", "Nicho": "x", "Status": "descartada"}]
assert radar.motivos_de_descarte(filas) == ["C (moldes): patrones dificiles de hacer", "A (fitness): pide videos con modelo"]

print("TODO OK (semaforo para modelar: 8 preguntas, IA y Meta simulados, temporadas, volumen, perfiles de Instagram, lanzamientos y motivos)")
