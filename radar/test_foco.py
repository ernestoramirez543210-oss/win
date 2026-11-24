"""Pruebas SIN red del foco por fechas (radar.py: foco.csv, 'WhatsApp primero'): lectura, ventana de fechas, rotacion, deteccion de WhatsApp y una corrida
completa (radar.run) con Meta y la planilla simulados: que el foco abra la corrida, que la temporada ceda, que los anunciantes que venden por WhatsApp se revisen
primero y que todo se apague solo al pasar la fecha (o con --max-foco 0, o con un foco.csv ausente o ilegible)."""
import contextlib
import csv
import io
import json
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import requests

import radar

HOY = datetime.now().date()
dia = lambda n: (HOY + timedelta(days=n)).isoformat()


def escribir(ruta, texto):
    Path(ruta).write_text(texto, encoding="utf-8")
    return ruta


# ---------------------------------------------------------------- read_foco
with tempfile.TemporaryDirectory() as tmp:
    p = escribir(Path(tmp) / "foco.csv", chr(0xFEFF) + "foco,palabra,es,nicho,prioridad,desde,hasta\n"
        f"whatsapp,bordado PDF WhatsApp,,bordado,1,{dia(-1)},{dia(1)}\n"
        f"whatsapp,PDF WhatsApp,,general,3,{dia(-1)},{dia(1)}\n"
        f",WhatsApp,,general,2,{dia(-1)},{dia(1)}\n"                          # sin nombre de foco: whatsapp
        f"whatsapp,,,general,1,{dia(-1)},{dia(1)}\n"                          # sin palabra: fuera
        f"whatsapp,mala fecha,,general,1,31/12/2026,{dia(1)}\n"               # fecha que no es AAAA-MM-DD: fuera
        f"whatsapp,fecha imposible,,general,1,2026-02-31,{dia(1)}\n"          # fecha que no existe: fuera
        f"whatsapp,al reves,,general,1,{dia(2)},{dia(1)}\n"                   # hasta antes que desde: fuera
        f"whatsapp,sin prioridad,,,,{dia(-1)},{dia(1)}\n"                      # prioridad y nicho vacios: 9 y general
        f"otro,otro foco,,general,1,{dia(-1)},{dia(1)}\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        filas = radar.read_foco(p)
    assert [r["pt"] for r in filas] == ["bordado PDF WhatsApp", "PDF WhatsApp", "WhatsApp", "sin prioridad", "otro foco"], [r["pt"] for r in filas]
    assert buf.getvalue().count("se ignora") == 3 and "fila 6" in buf.getvalue() and "fila 8" in buf.getvalue(), buf.getvalue()   # avisa de las tres mal escritas, con su fila
    por = {r["pt"]: r for r in filas}
    assert por["bordado PDF WhatsApp"]["tema"] == "bordado PDF", "tema = la consulta sin la palabra del foco"
    assert por["PDF WhatsApp"]["tema"] == "PDF" and por["WhatsApp"]["tema"] == "WhatsApp", "si solo queda WhatsApp, el tema es la consulta entera"
    assert [r["foco"] for r in filas] == ["whatsapp", "whatsapp", "whatsapp", "whatsapp", "otro"]
    assert por["sin prioridad"]["prioridad"] == 9 and por["sin prioridad"]["nicho"] == "general"
    assert por["bordado PDF WhatsApp"]["desde"] == HOY - timedelta(days=1) and por["bordado PDF WhatsApp"]["hasta"] == HOY + timedelta(days=1)
    assert radar.read_foco(Path(tmp) / "no-existe.csv") == [], "un archivo ausente es 'sin foco'"
    pausada = escribir(Path(tmp) / "p.csv", f"foco,palabra,es,nicho,prioridad,desde,hasta,estado\nwhatsapp,x,,general,1,{dia(-1)},{dia(1)},pausada\n")
    assert radar.read_foco(pausada) == [], "una fila pausada no cuenta"
    assert all(r["dias"] == 0 for r in filas), "sin la columna dias: 0 (los 30 de siempre)"
    # `dias` (opcional): vacio o 0 = lo de siempre; un numero de 1 a 60 vale para esa fila; cualquier otra cosa deja la fila fuera, con aviso
    con_dias = escribir(Path(tmp) / "d.csv", "foco,palabra,es,nicho,prioridad,desde,hasta,dias\n"
        + "".join(f"whatsapp,{n} PDF WhatsApp,,x,1,{dia(-1)},{dia(1)},{v}\n" for n, v in (("a", "3"), ("b", ""), ("c", "60"), ("d", "61"), ("e", "abc"), ("f", "-2"), ("g", "0"))))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fd = radar.read_foco(con_dias)
    assert [(r["pt"][0], r["dias"]) for r in fd] == [("a", 3), ("b", 0), ("c", 60), ("g", 0)], [(r["pt"], r["dias"]) for r in fd]
    assert buf.getvalue().count("se ignora") == 3 and "'dias'" in buf.getvalue() and "fila 5" in buf.getvalue() and "fila 7" in buf.getvalue(), buf.getvalue()

# ---------------------------------------------------------------- foco_open: los dos extremos entran
fila = lambda d, h, **k: dict({"pt": "x", "tema": "x", "es": "", "nicho": "general", "foco": "whatsapp", "prioridad": 1,
                               "desde": HOY + timedelta(days=d), "hasta": HOY + timedelta(days=h)}, **k)
assert radar.foco_open(fila(0, 0)) and radar.foco_open(fila(-2, 2)), "hoy cuenta aunque sea el primero o el ultimo dia"
assert not radar.foco_open(fila(1, 3)) and not radar.foco_open(fila(-3, -1)), "antes de 'desde' y despues de 'hasta' esta apagado"
domingo = datetime(2026, 10, 18, 23, 0)
r_fin = {"desde": datetime(2026, 10, 16).date(), "hasta": datetime(2026, 10, 18).date()}
assert radar.foco_open(r_fin, domingo) and radar.foco_open(r_fin, domingo.date()), "el domingo a las 11 p. m. todavia es foco (acepta datetime y date)"
assert not radar.foco_open(r_fin, datetime(2026, 10, 19, 0, 1)) and not radar.foco_open(r_fin, datetime(2026, 10, 15, 23, 59)), "el lunes 00:01 ya no"

# ---------------------------------------------------------------- pick_foco: prioridad, rotacion y fechas
filas = [fila(-1, 1, pt="g1", prioridad=3), fila(-1, 1, pt="n1", prioridad=1), fila(-1, 1, pt="n2", prioridad=1), fila(-1, 1, pt="m1", prioridad=2),
         fila(-9, -5, pt="viejo", prioridad=1), fila(2, 5, pt="futuro", prioridad=1)]
hace_1h = datetime.now().isoformat(timespec="seconds")
assert [r["pt"] for r in radar.pick_foco(filas, {"keywords": {}}, 3)] == ["n1", "n2", "m1"], "prioridad 1 primero, luego la 2; las fuera de fecha no entran"
assert radar.pick_foco(filas, {"keywords": {}}, 0) == [] and len(radar.pick_foco(filas, {"keywords": {}}, 99)) == 4
rec = radar.pick_foco(filas, {"keywords": {"foco: n1": hace_1h, "foco: n2": hace_1h}}, 3)
assert [r["pt"] for r in rec] == ["m1", "g1", "n1"], [r["pt"] for r in rec]         # las buscadas hace menos de 24 h ceden el turno (y entran al final si no hay otras)
assert radar.pick_foco(filas, {"keywords": {"temporada: n1": hace_1h}}, 1)[0]["pt"] == "n1", "solo cuenta la etiqueta 'foco: ' (no la de temporada)"

# ---------------------------------------------------------------- vende_por_whatsapp
assert radar.vende_por_whatsapp({"top_host": "api.whatsapp.com", "ads": []}) and radar.vende_por_whatsapp({"top_host": "wa.me", "ads": []})
assert radar.vende_por_whatsapp({"top_host": "tienda.com.br", "ads": [{"cta_type": "LEARN_MORE"}, {"cta_type": "WHATSAPP_MESSAGE"}]}), "el boton de WhatsApp de CUALQUIER anuncio basta"
assert radar.vende_por_whatsapp({"top_host": "tienda.com.br", "ads": [{"cta_type": "whatsapp_message"}]})
assert not radar.vende_por_whatsapp({"top_host": "tienda.com.br", "ads": [{"cta_type": "LEARN_MORE"}, {"cta_type": ""}, {}]})
assert not radar.vende_por_whatsapp({"top_host": "m.me", "ads": [{"cta_type": "MESSAGE_PAGE"}]}), "Messenger no es WhatsApp"
assert not radar.vende_por_whatsapp({}), "sin datos: no"
# la misma regla que usa analyze_landing al abrir la pagina (no pueden separarse)
for host, cta in (("api.whatsapp.com", ""), ("x.com", "WHATSAPP_MESSAGE"), ("x.com", "LEARN_MORE")):
    g = {"top_host": host, "ads": [{"cta_type": cta, "text": "t", "body": "t"}]}
    assert radar.vende_por_whatsapp(g) == radar.analyze_landing(dict(g, top_host=host), "")["whatsapp"], (host, cta)

# ---------------------------------------------------------------- el foco.csv de verdad
real = radar.read_foco(radar.HERE / "foco.csv")
assert len(real) >= 10 and all(r["foco"] == "whatsapp" for r in real), "el foco.csv del repo se lee entero y es de WhatsApp"
assert all(r["desde"] <= r["hasta"] and (r["hasta"] - r["desde"]).days <= 14 for r in real), "un foco es corto: de 'desde' a 'hasta' no pasa de dos semanas (si hace falta mas, se parte en dos)"
assert all(0 <= r["dias"] <= 60 for r in real), "`dias` vacio (0) o de 1 a 60"
assert all(1 <= len(r["pt"].split()) <= 3 for r in real), "consultas de 1 a 3 palabras: con 4 o mas Meta casi no devuelve nada"
assert all(r["tema"] and "whatsapp" not in r["tema"].lower() for r in real if len(r["pt"].split()) > 1), [r["pt"] for r in real if r["tema"].lower() == r["pt"].lower()]
assert len({(r["pt"], r["desde"]) for r in real}) == len(real), "sin consultas repetidas en la misma ventana (una consulta larga se parte en ventanas de 14 dias)"
assert all(radar.niche_stems(r["tema"]) or r["nicho"] == "general" for r in real), "una consulta con nicho tiene raices con que comprobar que el anunciante es de ese nicho"


# ---------------------------------------------------------------- una corrida completa con Meta simulado
def anuncios(pid, nombre, dias, link, cta, n=8):
    """n piezas distintas, activas y con PDF en el copy: lo justo para pasar los filtros y la nota."""
    ahora = time.time()
    return [{"archive_id": f"{pid}{i}", "collation_id": f"{pid}c{i}", "collation_count": 1, "page_id": pid, "page_name": nombre,
             "start": ahora - dias * 86400, "active": True, "body": f"Material em PDF croche bordado receitas {i}", "text": f"Material em PDF croche bordado receitas {i}",
             "link": link, "cta": "", "cta_type": cta, "page_like": 1200, "video": False, "sig": f"{pid}s{i}"} for i in range(n)]


ANUNCIOS = {"1": anuncios("1", "Tienda X", 120, "https://tiendax.example.com.br/oferta", "LEARN_MORE"),          # mejor anunciante por rank, sin WhatsApp
            "2": anuncios("2", "Zap Y", 65, "https://api.whatsapp.com/send?phone=5511", "WHATSAPP_MESSAGE"),     # peor rank, vende por WhatsApp
            "3": anuncios("3", "Zapatos Z", 200, "https://api.whatsapp.com/send?phone=5522", "WHATSAPP_MESSAGE"),   # vende por WhatsApp pero NO es del tema (zapatos): no debe revisarse
            "4": anuncios("4", "Nuevo W", 5, "https://api.whatsapp.com/send?phone=5533", "WHATSAPP_MESSAGE", n=10),  # WhatsApp, del tema, 10 anuncios activos pero solo 5 dias: solo entra con `dias` bajo
            "5": anuncios("5", "Poco V", 5, "https://api.whatsapp.com/send?phone=5544", "WHATSAPP_MESSAGE", n=3)}   # WhatsApp, del tema, 5 dias y solo 3 anuncios activos: ni con `dias` bajo (pide 8+)
for _a in ANUNCIOS["3"]:
    _a["body"] = _a["text"] = "Sapatos femininos com frete gratis " + _a["sig"]
assert radar.winner_rank(radar.aggregate(ANUNCIOS["1"])["1"]) > radar.winner_rank(radar.aggregate(ANUNCIOS["2"])["2"]), "el rank de X es mayor que el de Y (sin foco, X va primero)"


class MetaFalsa:
    registro = {"consultas": [], "evaluados": []}

    def __init__(self, *a, **k):
        self.lsd = "x"

    def bootstrap(self, country):
        return "lsd", "doc"

    def collect(self, q, country, pages=1, page_id=None, **k):
        if country != "BR":
            return [], False                              # Colombia: sin anunciantes (hueco libre)
        if page_id:
            MetaFalsa.registro["evaluados"].append(page_id)
            return ANUNCIOS[page_id], True
        MetaFalsa.registro["consultas"].append(q)
        return [a for lst in ANUNCIOS.values() for a in lst], False       # toda busqueda devuelve a los dos anunciantes

    def inspect_landing(self, link):
        return ""


def _timeout():
    return requests.exceptions.ReadTimeout("HTTPSConnectionPool(host='www.facebook.com', port=443): Read timed out. (read timeout=45)")      # el del 19/10 a las 19:00


class MetaSinRespuesta(MetaFalsa):
    """Meta que a veces no contesta (ReadTimeout de 45 s). cae_adv / cae_q = {page_id o consulta: veces que falla}; todo = toda busqueda falla; bootstrap_cae = ni abre la Biblioteca."""
    cae_adv, cae_q, cae_co, todo, bootstrap_cae = {}, {}, 0, False, False

    def bootstrap(self, country):
        if MetaSinRespuesta.bootstrap_cae:
            raise _timeout()
        return super().bootstrap(country)

    def collect(self, q, country, pages=1, page_id=None, **k):
        if country == "BR" and page_id and MetaSinRespuesta.cae_adv.get(page_id, 0) > 0:
            MetaSinRespuesta.cae_adv[page_id] -= 1
            raise _timeout()
        if country == "BR" and not page_id and (MetaSinRespuesta.todo or MetaSinRespuesta.cae_q.get(q, 0) > 0):
            MetaSinRespuesta.cae_q[q] = MetaSinRespuesta.cae_q.get(q, 0) - 1
            raise _timeout()
        if country == "CO" and MetaSinRespuesta.cae_co > 0:
            MetaSinRespuesta.cae_co -= 1
            raise _timeout()
        return super().collect(q, country, pages, page_id)


def sin_respuesta(adv=None, q=None, co=0, todo=False, bootstrap=False):
    MetaSinRespuesta.cae_adv, MetaSinRespuesta.cae_q, MetaSinRespuesta.cae_co = dict(adv or {}), dict(q or {}), co
    MetaSinRespuesta.todo, MetaSinRespuesta.bootstrap_cae = todo, bootstrap
    return MetaSinRespuesta


def corrida(tmp, foco_texto, max_foco=3, max_ofertas=1, foco_ruta="foco.csv", temporada=True, ia_caida=False, meta=None, presupuesto=None, codigo=0, modo="auto", nichos=None, max_nichos=0, min_promesas=0, max_temporada=3):
    """Corre radar.run con todo simulado y devuelve (lo que imprimio, consultas, orden en que se evaluaron los anunciantes, anunciantes candidatos, estado guardado).
    meta = clase de Meta simulada (por defecto MetaFalsa); presupuesto = None, "agotado" (el tope diario de Gemini ya se gasto antes de empezar) o "mitad" (se agota
    en la primera pregunta); codigo = codigo de salida esperado."""
    tmp = Path(tmp)
    for sub in ("out",):
        (tmp / sub).mkdir(exist_ok=True)
    escribir(tmp / "palabras.csv", "pt,es,nicho,mercado\ncroche vestidos,,croche,Brasil\nbordado riscos,,bordado,Brasil\nreceitas faceis,,receitas,Brasil\n")
    escribir(tmp / "angulos.csv", "angulo,tipo,origen\nrenda extra em casa,ingreso extra,semilla\nmodelos prontos para imprimir,plantillas listas,semilla\nbaixe agora,entrega inmediata,semilla\n")
    escribir(tmp / "temporadas.csv", "palabra,es,nicho,temporada,prioridad,desde,hasta\n" + ("".join(
        f"croche halloween {i},,halloween,halloween,1,01-01,12-31\n" for i in range(4)) if temporada else ""))
    if foco_texto is not None:
        escribir(tmp / foco_ruta, foco_texto)
    if nichos is not None:
        escribir(tmp / "nichos.csv", nichos)
    args = types.SimpleNamespace(
        simular_sheets=False, sin_sheets=True, tope_diario=0, modo=modo, min_nota=7, max_kw=5, temporadas=str(tmp / "temporadas.csv"), sin_ia=not (ia_caida or presupuesto), revisar_max=0,
        pages_co=1, pages=1, nota_sin_colombia=7, min_activos=8, keywords=str(tmp / "palabras.csv"), angulos=str(tmp / "angulos.csv"), max_temporada=max_temporada,
        max_seguidores=500_000, max_ofertas=max_ofertas, max_adv=8, exigir_colombia=False, dias_temporada=14, cache=False,
        foco=str(tmp / foco_ruta), max_foco=max_foco, nichos=str(tmp / "nichos.csv"), max_nichos=max_nichos, min_promesas=min_promesas,
        min_anuncios_oferta=0)       # la oferta principal de la pagina se prueba aparte (test_oferta.py)
    MetaFalsa.registro = {"consultas": [], "evaluados": []}
    viejo = (radar.Meta, radar.OUT, radar.STATE_FILE, radar.load_env, radar.polite, radar.ia.available, radar.ia.ask_json, radar.ia.estado_presupuesto,
             radar.ia.USO_FILE, radar.ia.ENV_FILE)
    radar.Meta, radar.OUT, radar.STATE_FILE = meta or MetaFalsa, tmp / "out", tmp / "state.json"
    radar.load_env, radar.polite, radar.ia.available = (lambda *a, **k: None), (lambda *a, **k: None), (lambda: False)
    radar.ia.USO_FILE, radar.ia.ENV_FILE = tmp / "ia_uso.json", tmp / "no-existe.env"      # nada de esta prueba toca la carpeta real del radar
    if ia_caida:       # la IA esta configurada pero Gemini contesta 403 (tope de gasto): cada pregunta falla
        def caida(*a, **k):
            raise radar.ia.IAError("Gemini 403: Spend cap breached for project: projects/1")
        radar.ia.available, radar.ia.ask_json = (lambda: True), caida
    if presupuesto == "agotado":       # el presupuesto diario ya se gasto: la corrida ni siquiera le pregunta a Gemini
        def prohibido(*a, **k):
            raise AssertionError("con el presupuesto agotado no se le pregunta nada a Gemini")
        radar.ia.available, radar.ia.ask_json = (lambda: True), prohibido
        radar.ia.estado_presupuesto = lambda: {"dia": "2026-10-19", "tope": 0.10, "gastado": 0.1023, "agotado": True}
    if presupuesto == "mitad":         # el tope se gasta justo en la primera pregunta de la corrida
        def sin_presupuesto(*a, **k):
            raise radar.ia.PresupuestoAgotado("Presupuesto diario de Gemini agotado (de prueba)")
        radar.ia.available, radar.ia.ask_json = (lambda: True), sin_presupuesto
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = radar.run(args)
    finally:
        (radar.Meta, radar.OUT, radar.STATE_FILE, radar.load_env, radar.polite, radar.ia.available, radar.ia.ask_json, radar.ia.estado_presupuesto,
         radar.ia.USO_FILE, radar.ia.ENV_FILE) = viejo
    assert code == codigo, (code, buf.getvalue())
    assert buf.getvalue().rstrip().splitlines()[-1].startswith("Gemini: "), buf.getvalue()[-300:]      # la linea de uso de Gemini siempre cierra la corrida
    cands = []
    for f in (tmp / "out").glob("candidatos_*.csv"):
        with open(f, encoding="utf-8-sig", newline="") as fh:
            cands += [r["Anunciante"] for r in csv.DictReader(fh)]
    estado = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
    return buf.getvalue(), list(MetaFalsa.registro["consultas"]), list(MetaFalsa.registro["evaluados"]), cands, estado


NL = chr(10)
TEMAS = ["croche", "bordado", "receitas", "moldes", "costura"]       # palabras que SI aparecen en el copy de los anuncios de prueba (el foco exige que el anunciante hable del tema)
FOCO_ACTIVO = ("foco,palabra,es,nicho,prioridad,desde,hasta" + NL + "".join(
    f"whatsapp,{w} PDF WhatsApp,,general,{1 if i < 2 else 2},{dia(-1)},{dia(1)}" + NL for i, w in enumerate(TEMAS)))
FOCO_VENCIDO = FOCO_ACTIVO.replace(dia(-1), dia(-6)).replace(dia(1), dia(-1))
TODAS = 9        # --max-ofertas alto: la corrida hace sus cinco busquedas; con 1 se detiene al registrar la primera oferta y sirve para ver QUIEN se queda el cupo

with tempfile.TemporaryDirectory() as tmp:
    # a) sin foco vigente: sin busquedas de foco y la temporada con sus 3 busquedas; desde el 2026-11-03 el «WhatsApp primero» vale SIEMPRE, con o sin foco
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_VENCIDO, max_ofertas=1)
    assert "Foco WhatsApp" not in out and "WhatsApp primero" in out, out
    assert not any(c.endswith("PDF WhatsApp") for c in consultas) and evaluados[0] == "2" and cands == ["Zap Y"], (consultas, evaluados, cands)
with tempfile.TemporaryDirectory() as tmp:
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_VENCIDO, max_ofertas=TODAS)
    assert sum(1 for c in consultas if c.startswith("croche halloween")) == 3 and len(consultas) == 5, consultas
    assert est["modos"].get("temporada", {}).get("busquedas") == 3 and "foco" not in est["modos"], est["modos"]

with tempfile.TemporaryDirectory() as tmp:
    # b) con foco vigente y una sola oferta de cupo: se la queda el anunciante de WhatsApp aunque su rank sea menor
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=1)
    assert evaluados[0] == "2" and cands == ["Zap Y"], (evaluados, cands, out)
    assert "3" not in evaluados, "una tienda de WhatsApp que no habla del tema de la busqueda no se revisa (el tema no incluye la palabra WhatsApp)"
    assert "4" not in evaluados and "5" not in evaluados, "sin `dias` la fila pide los 30 de siempre: los de 5 dias no se revisan"
    assert "WhatsApp primero: 1 de 2 anunciantes por revisar con 30+ dias (0 ya revisados antes)" in out, out
    assert consultas == ["croche PDF WhatsApp"], consultas                                                          # la primera busqueda ya lleno el cupo
with tempfile.TemporaryDirectory() as tmp:
    # b1) el que ya se reviso en otra corrida no vuelve, y el log lo dice (asi "0 de 0" no se confunde con "Meta no devolvio nada")
    escribir(Path(tmp) / "state.json", json.dumps({"keywords": {}, "seen_pages": {"1": {
        "name": "Tienda X", "decision": "candidato 8", "date": datetime.now().isoformat(timespec="seconds")}}}))
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=1)
    assert "WhatsApp primero: 1 de 1 anunciantes por revisar con 30+ dias (1 ya revisados antes)" in out, out
    assert evaluados[0] == "2" and cands == ["Zap Y"], (evaluados, cands)
with tempfile.TemporaryDirectory() as tmp:
    # b2) con cupo para todos: el foco abre la corrida (3 de 5 busquedas, por prioridad), la temporada cede a 1 y queda 1 normal; el de WhatsApp se evalua primero
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS)
    assert out.count("Foco WhatsApp hasta el " + dia(1)) == 1 and "3 de 5 busquedas" in out, out
    assert consultas[:3] == ["croche PDF WhatsApp", "bordado PDF WhatsApp", "receitas PDF WhatsApp"], consultas       # prioridad 1 (croche, bordado) y luego la 2 (receitas)
    assert sum(1 for c in consultas if c.startswith("croche halloween")) == 1 and len(consultas) == 5, consultas   # la temporada cede a 1 y siguen siendo 5 busquedas
    assert evaluados[:2] == ["2", "1"] and sorted(cands) == ["Tienda X", "Zap Y"], (evaluados, cands)             # los dos entran al cupo, el de WhatsApp primero
    assert est["modos"]["foco"]["busquedas"] == 3 and "foco: croche PDF WhatsApp" in est["keywords"], est["modos"]
    assert est["modos"]["temporada"]["busquedas"] == 1, est["modos"]

with tempfile.TemporaryDirectory() as tmp:
    # b3) una fila con `dias`: pide esos dias en vez de los 30 (una oferta de temporada nace pocas semanas antes), pero sigue pidiendo 8+ anuncios activos y la nota
    FOCO_DIAS = "foco,palabra,es,nicho,prioridad,desde,hasta,dias" + NL + "".join(
        f"whatsapp,{w} PDF WhatsApp,,general,{1 if i < 2 else 2},{dia(-1)},{dia(1)},3" + NL for i, w in enumerate(TEMAS))
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_DIAS, max_ofertas=TODAS)
    assert "WhatsApp primero: 3 de 4 anunciantes por revisar con 3+ dias (0 ya revisados antes)" in out, out         # Zap Y, Nuevo W y Poco V (WhatsApp) + Tienda X
    assert evaluados[:3].count("2") + evaluados[:3].count("4") + evaluados[:3].count("5") == 3 and evaluados[3] == "1", evaluados[:4]     # los de WhatsApp primero, luego Tienda X
    assert "Nuevo W" in cands and "Zap Y" in cands and "Tienda X" in cands, cands
    assert "Poco V" not in cands, "con 3 anuncios activos no pasa, ni con `dias` bajo"
    assert "3" not in evaluados, evaluados

# b4) la IA se cae a mitad de la corrida (tope de gasto de Gemini, 19/10): el descarte por nota hecho SIN ella es provisional y no se recuerda;
#     los que no dependen de la IA (pocos anuncios activos) si. Sin IA configurada desde el principio, todo se recuerda como siempre.
ANUNCIOS["6"] = anuncios("6", "Medio M", 10, "https://api.whatsapp.com/send?phone=5566", "WHATSAPP_MESSAGE")      # 8 activos, 10 dias: nota 6 (<7)
try:
    for ia_caida in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            out, consultas, evaluados, cands, est = corrida(tmp, FOCO_DIAS, max_ofertas=TODAS, ia_caida=ia_caida)
            vistos = set(est["seen_pages"])
            assert "Medio M" not in cands and "6" in evaluados, (ia_caida, cands, evaluados)
            assert "5" in vistos, (ia_caida, vistos, "Poco V cae por 3 anuncios activos: eso no depende de la IA y se recuerda siempre")
            assert ("6" in vistos) == (not ia_caida), (ia_caida, vistos, "Medio M cae por nota: se recuerda solo si la IA no se cayo")
            assert ("Apago la IA en esta corrida" in out) == ia_caida, out
            if ia_caida:
                assert "IA sin respuesta" in out and "no se recuerdan" in out, out
finally:
    del ANUNCIOS["6"]

#     primera pregunta, la IA se apaga de una vez (no espera a 2 fallos). En los dos casos la IA SE ESPERABA: un descarte por nota hecho sin ella no se recuerda.
ANUNCIOS["6"] = anuncios("6", "Medio M", 10, "https://api.whatsapp.com/send?phone=5566", "WHATSAPP_MESSAGE")
try:
    for modo in ("agotado", "mitad"):
        with tempfile.TemporaryDirectory() as tmp:
            out, consultas, evaluados, cands, est = corrida(tmp, FOCO_DIAS, max_ofertas=TODAS, presupuesto=modo)
            vistos = set(est["seen_pages"])
            assert "Medio M" not in cands and "6" in evaluados, (modo, cands, evaluados)
            assert "5" in vistos and "6" not in vistos, (modo, vistos, "lo que no depende de la IA se recuerda; el descarte por nota sin IA, no")
            if modo == "agotado":
                assert "con glosario (presupuesto diario de Gemini agotado: ~US$ 0.1023 de US$ 0.10 estimados hoy" in out, out
                assert "IA sin respuesta" not in out and "Apago la IA" not in out, out
            else:
                assert out.count("IA sin respuesta") == 1 and "Presupuesto diario de Gemini agotado" in out and "Apago la IA en esta corrida" in out, out
finally:
    del ANUNCIOS["6"]

# m) Meta no contesta (ReadTimeout de 45 s, como el 19/10 a las 19:00): el anunciante o la busqueda se saltan y la corrida sigue; lo saltado no se recuerda (la proxima
#    corrida lo reintenta) y tras 3 fallos seguidos se corta con un aviso (para no esperar 45 s por cada anunciante si Meta esta caida)
with tempfile.TemporaryDirectory() as tmp:
    # m1) un anunciante no contesta: se salta, se sigue con el siguiente, no se le insiste en las otras busquedas de la corrida (cada intento cuesta 45 s) y no queda
    #     recordado en state.json, asi que la proxima corrida lo reintenta
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS, meta=sin_respuesta(adv={"2": 99}))
    assert out.count("Meta no respondio para Zap Y (ReadTimeout): lo salto y sigo con el siguiente.") == 1 and "Traceback" not in out and "corto la corrida" not in out, out
    assert cands == ["Tienda X"] and "1" in est["seen_pages"] and "2" not in est["seen_pages"] and len(consultas) == 5, (cands, list(est["seen_pages"]), consultas)
with tempfile.TemporaryDirectory() as tmp:
    # m2) tres anunciantes DISTINTOS seguidos que no contestan: Meta esta caida, se corta la corrida (sin esperar 45 s por cada uno); sin candidatos el codigo es 3
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_DIAS, max_ofertas=TODAS, meta=sin_respuesta(adv={"2": 99, "4": 99, "5": 99}), codigo=3)
    assert out.count("Meta no respondio para ") == 3 and "!! Meta no respondio 3 veces seguidas (ReadTimeout): corto la corrida" in out, out
    assert cands == [] and not any(k in est["seen_pages"] for k in "245") and len(consultas) == 1, (cands, list(est["seen_pages"]), consultas)
with tempfile.TemporaryDirectory() as tmp:
    # m3) una BUSQUEDA no contesta una vez: no cuenta como hecha (ni en las estadisticas ni en la rotacion de palabras) y las demas siguen
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS, meta=sin_respuesta(q={"croche PDF WhatsApp": 1}))
    assert out.count("Meta no respondio a esta busqueda (ReadTimeout): la salto y sigo con la siguiente.") == 1, out
    assert "croche PDF WhatsApp" not in consultas and len(consultas) == 4, consultas
    assert "foco: croche PDF WhatsApp" not in est["keywords"] and est["modos"]["foco"]["busquedas"] == 2, (list(est["keywords"]), est["modos"])
    assert sorted(cands) == ["Tienda X", "Zap Y"], cands
with tempfile.TemporaryDirectory() as tmp:
    # m4) Meta no contesta NADA: a la tercera busqueda seguida se corta; sin candidatos el codigo es 3 (systemd reintenta 25 minutos despues) y no se guarda nada como hecho
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS, meta=sin_respuesta(todo=True), codigo=3)
    assert out.count("Meta no respondio a esta busqueda (ReadTimeout)") == 3 and "!! Meta no respondio 3 veces seguidas (ReadTimeout): corto la corrida" in out, out
    assert cands == [] and consultas == [] and not est["keywords"] and not est["modos"], (list(est["keywords"]), est["modos"])
with tempfile.TemporaryDirectory() as tmp:
    # m5) Meta ni abre la Biblioteca: no hay nada que buscar y se sale con el codigo 3, sin Traceback
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS, meta=sin_respuesta(bootstrap=True), codigo=3)
    assert "Meta no respondio al abrir la Biblioteca (ReadTimeout): esta corrida no busca nada" in out and "[BR]" not in out and "Traceback" not in out, out
with tempfile.TemporaryDirectory() as tmp:
    # m6) la busqueda de COLOMBIA de un anunciante no contesta: ese anunciante se salta (no se le da por hueco libre ni se recuerda) y el siguiente se evalua normal
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS, meta=sin_respuesta(co=1))
    assert out.count("Meta no respondio para Zap Y (ReadTimeout)") == 1 and "corto la corrida" not in out, out
    assert cands == ["Tienda X"] and "1" in est["seen_pages"] and "2" not in est["seen_pages"], (cands, list(est["seen_pages"]))

with tempfile.TemporaryDirectory() as tmp:
    # m7) una busqueda de COMBO (palabra + promesa) que Meta no contesta no marca ni la palabra ni el angulo como usados: la rotacion no los da por buscados.
    #     (con Meta contestando si quedan marcados)
    out, consultas, evaluados, cands, est = corrida(tmp, None, max_foco=0, max_ofertas=TODAS, temporada=False, modo="combo", meta=sin_respuesta(todo=True), codigo=3)
    assert out.count("Meta no respondio a esta busqueda") == 3 and not est["keywords"], (out, list(est["keywords"]))
with tempfile.TemporaryDirectory() as tmp:
    out, consultas, evaluados, cands, est = corrida(tmp, None, max_foco=0, max_ofertas=TODAS, temporada=False, modo="combo")
    assert "croche vestidos" in est["keywords"] and any(k.startswith("ángulo: ") for k in est["keywords"]), list(est["keywords"])

for _pid, _dias in (("7", 90), ("8", 80), ("9", 70)):          # tres anunciantes de WhatsApp mas, solo para el caso m8
    ANUNCIOS[_pid] = anuncios(_pid, "Zap " + _pid, _dias, f"https://api.whatsapp.com/send?phone=55{_pid}{_pid}", "WHATSAPP_MESSAGE")
try:
    with tempfile.TemporaryDirectory() as tmp:
        # m8) fallo, exito, fallo, exito, fallo dentro de UNA busqueda: la racha de "seguidos" se corta con cada anunciante que si contesta, asi que NO se corta la corrida
        orden = sorted(["2", "7", "8", "9"], key=lambda p: radar.winner_rank(radar.aggregate(ANUNCIOS[p])[p]), reverse=True) + ["1"]      # los de WhatsApp primero, luego Tienda X
        out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS,
                                                        meta=sin_respuesta(adv={orden[0]: 99, orden[2]: 99, orden[4]: 99}))
        assert out.count("Meta no respondio para ") == 3 and "corto la corrida" not in out, out
        assert len(consultas) == 5 and sorted(cands) == sorted(radar.aggregate(ANUNCIOS[p])[p]["name"] for p in (orden[1], orden[3])), (orden, cands, consultas)
finally:
    for _pid in ("7", "8", "9"):
        del ANUNCIOS[_pid]

# v) anuncios en video (dueno, 2026-11-11): entre los de WhatsApp, el que escala con anuncios en video se revisa primero aunque su rank sea menor,
#    y su fila dice cuantos de sus anuncios son video
ANUNCIOS["6"] = [dict(a, video=True) for a in anuncios("6", "Video V", 40, "https://api.whatsapp.com/send?phone=5566", "WHATSAPP_MESSAGE")]
try:
    assert radar.winner_rank(radar.aggregate(ANUNCIOS["6"])["6"]) < radar.winner_rank(radar.aggregate(ANUNCIOS["2"])["2"])
    with tempfile.TemporaryDirectory() as tmp:
        out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_foco=0, max_ofertas=1)
        assert evaluados[0] == "6" and cands == ["Video V"], (evaluados, cands)
        assert "en video: 1" in out, out
        fila = next(csv.DictReader(open(next((Path(tmp) / "out").glob("candidatos_*.csv")), encoding="utf-8-sig")))
        assert fila["Señales winner"].endswith("8 de 8 anuncios activos en video") and "+1 por anuncios en video" in fila["Justificación"], fila
finally:
    del ANUNCIOS["6"]

with tempfile.TemporaryDirectory() as tmp:
    # c) --max-foco 0 apaga las busquedas de foco, aunque el archivo tenga filas vigentes (el «WhatsApp primero» sigue: vale siempre desde el 2026-11-03)
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_foco=0, max_ofertas=1)
    assert "Foco WhatsApp" not in out and not any(c.endswith("PDF WhatsApp") for c in consultas) and evaluados[0] == "2", (consultas, evaluados)

with tempfile.TemporaryDirectory() as tmp:
    # d) sin foco.csv no pasa nada; con un foco.csv ilegible (basura, no texto) tampoco: la corrida sigue
    out, consultas, evaluados, cands, est = corrida(tmp, None, max_ofertas=1)
    assert "Foco WhatsApp" not in out and evaluados[0] == "2" and cands == ["Zap Y"], out
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "foco.csv").write_bytes(bytes([255, 254, 0, 0]) + b" no es texto " + bytes([129, 141, 143]))
    out, consultas, evaluados, cands, est = corrida(tmp, None, max_ofertas=1)
    assert "No pude leer foco.csv" in out and "Foco WhatsApp" not in out and evaluados[0] == "2" and cands == ["Zap Y"], out

with tempfile.TemporaryDirectory() as tmp:
    # e) el ultimo dia (hasta = hoy) todavia es foco; el dia siguiente a 'hasta' ya no
    ultimo = FOCO_ACTIVO.replace(dia(1), dia(0))
    out, _, evaluados, cands, _ = corrida(tmp, ultimo, max_ofertas=1)
    assert "Foco WhatsApp hasta el " + dia(0) in out and cands == ["Zap Y"], out
with tempfile.TemporaryDirectory() as tmp:
    out, consultas_v, evaluados, cands, _ = corrida(tmp, FOCO_ACTIVO.replace(dia(-1), dia(-3)).replace(dia(1), dia(-1)), max_ofertas=1)
    assert "Foco WhatsApp" not in out and not any(c.endswith("PDF WhatsApp") for c in consultas_v), "ayer fue el ultimo dia: hoy ya no hay foco"

with tempfile.TemporaryDirectory() as tmp:
    # f) nichos nuevos (2026-11-03): van despues del foco y la temporada, siempre queda 1 busqueda normal, y --min-promesas da al menos una promesa
    nichos = "palabra,es,nicho,origen" + NL + "croche bolsas,,croche,semilla" + NL + "bordado livre,,bordado,semilla" + NL + "croche tapete,,croche,semilla" + NL
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_foco=1, max_ofertas=TODAS, nichos=nichos, max_nichos=2, min_promesas=1, max_temporada=1)
    assert consultas[0].endswith("PDF WhatsApp") and consultas[1].startswith("croche halloween"), consultas
    assert consultas[2:4] == ["croche bolsas", "bordado livre"], ("un nicho por tema antes de repetir tema", consultas)
    assert len(consultas) == 5 and "Reparto (auto, segun rendimiento): palabra 0, combo 0, angulo 1" in out, out
    assert est["keywords"].get("nicho: croche bolsas") and est["etiquetas"]["nicho: bordado livre"]["busquedas"] == 1, est["etiquetas"]
    assert est["modos"]["nicho"]["busquedas"] == 2, est["modos"]
    # la corrida siguiente estrena el que falta y no repite los de hace un momento
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_foco=1, max_ofertas=TODAS, nichos=nichos, max_nichos=2, min_promesas=1, max_temporada=1)
    assert "croche tapete" in consultas and "croche bolsas" not in consultas and "bordado livre" not in consultas, consultas
with tempfile.TemporaryDirectory() as tmp:
    # h) con nichos en la corrida, las busquedas agotadas (palabras, promesas) no se repiten: su turno lo toman mas nichos nuevos; la cosecha nunca
    #    borra lo que ya estaba en nichos.csv
    hace = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    agotadas = ["croche vestidos", "bordado riscos", "receitas faceis", "ángulo: renda extra em casa", "ángulo: modelos prontos para imprimir",
                "ángulo: baixe agora"]
    escribir(Path(tmp) / "state.json", json.dumps({"keywords": {k: hace for k in agotadas}, "seen_pages": {},
                                                   "etiquetas": {k: {"busquedas": 2, "candidatos": 0} for k in agotadas}}))
    nichos = "palabra,es,nicho,origen" + NL + "".join(f"{t} kit,,{t},semilla" + NL for t in ("croche", "bordado", "receitas", "moldes"))
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_foco=1, max_ofertas=TODAS, nichos=nichos, max_nichos=2, min_promesas=1, max_temporada=1)
    assert consultas[2:] == ["croche kit", "bordado kit", "receitas kit"] and "6 busquedas agotadas descansan" in out, (consultas, out)
    assert [r["pt"] for r in radar.read_nichos(Path(tmp) / "nichos.csv")][:4] == ["croche kit", "bordado kit", "receitas kit", "moldes kit"]
    assert est["etiquetas"]["nicho: croche kit"]["ultima"] and "seguidas_sin" in est["etiquetas"]["nicho: croche kit"], est["etiquetas"]
with tempfile.TemporaryDirectory() as tmp:
    # g) sin --max-nichos (lo de siempre) el archivo no se usa
    out, consultas, evaluados, cands, est = corrida(tmp, FOCO_ACTIVO, max_ofertas=TODAS, nichos="palabra,es,nicho,origen" + NL + "croche bolsas,,croche,semilla" + NL)
    assert "croche bolsas" not in consultas and "Nichos nuevos" not in out, consultas

print("TODO OK (foco por fechas: lectura, ventana, rotacion, WhatsApp primero y corrida completa con Meta simulado)")
