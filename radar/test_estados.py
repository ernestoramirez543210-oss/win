"""Pruebas SIN red de dash_estados.py y de radar.sincronizar_estados (el dash y Google Sheets estan simulados)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

import dash_estados as de
import radar
import seguimiento
import sheets

CLAVE = "llave-de-prueba-0123456789abcdef"
# --sync-estados tambien lee seguimiento.json: en el VPS el de verdad existe, y las pruebas de los estados no deben depender de el (uno que no existe = sin fotos)
_TMP = tempfile.mkdtemp()
radar.RUTA_SEGUIMIENTO = str(Path(_TMP) / "no-existe.json")
# ...y pide el resultado de las pruebas como mucho una vez por hora: con una marca recien puesta, las pruebas de los estados no lo piden (las de las pruebas, al final, si)
_MARCA = Path(_TMP) / "pruebas.marca"
_MARCA.touch()
radar.RUTA_PRUEBAS_MARCA = str(_MARCA)


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self.payload = status, payload

    def json(self):
        if self.payload is None:
            raise ValueError("no es JSON")
        return self.payload


class FakeDash:
    def __init__(self, respuestas):
        self.respuestas, self.calls = list(respuestas), []

    def _sig(self):
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers, None))
        return self._sig()

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        return self._sig()


def dice(fn):
    """Corre fn() y devuelve (resultado, lo que imprimio)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn()
    return r, buf.getvalue()


# ---------------------------------------------------------------- la llave
original_here = de.HERE
with tempfile.TemporaryDirectory() as tmp:
    de.HERE = Path(tmp)
    os.environ.pop("DASH_CLAVE_RADAR", None)
    assert de.leer_clave() == "", "sin archivo ni variable no hay llave"
    (Path(tmp) / "dash.key").write_text("  " + CLAVE + "\n", encoding="utf-8")
    assert de.leer_clave() == CLAVE, "del archivo dash.key (con el salto de linea quitado)"
    # Windows PowerShell 5.1 deja una marca BOM al principio y un CR al final del texto que manda por la entrada estandar: no entran en la llave
    (Path(tmp) / "dash.key").write_bytes(b"\xef\xbb\xbf" + CLAVE.encode() + b"\r\n")
    assert de.leer_clave() == CLAVE, "con BOM al principio y CR LF al final"
    os.environ["DASH_CLAVE_RADAR"] = "otra-llave-de-la-variable"
    assert de.leer_clave() == "otra-llave-de-la-variable", "la variable de entorno manda"
    os.environ.pop("DASH_CLAVE_RADAR", None)
de.HERE = original_here

# ---------------------------------------------------------------- traer_estados
bueno = {"ok": True, "estados": {
    "a9999000000318321": {"estado": "no testeada", "desde": "nuevo", "t": "2026-10-14T20:00:00Z", "fila": 2, "anun": "Lia  Crochê "},
    "h1abc": {"estado": "vigilar", "desde": "", "fila": 4, "anun": "Sin Enlace SA", "motivo": "  la vi\n en el dash  " + "x" * 400},
    "../malo": {"estado": "nuevo", "desde": "vigilar"},                  # id raro: fuera
    "a555": {"estado": "inventado", "desde": "nuevo"},                   # estado que no existe: fuera
    "a666": {"estado": "nuevo", "desde": "raro"},                        # desde que no existe: fuera
    "a777": "texto",                                                       # no es un objeto: fuera
    "a888": {"estado": "ganadora", "desde": "nuevo", "fila": "x", "anun": "y" * 200},   # fila ilegible -> 0; anunciante acotado
}}
f = FakeDash([Resp(200, bueno)])
est = de.traer_estados(CLAVE, f)
assert sorted(est) == sorted(["a9999000000318321", "a888", "h1abc"]), sorted(est)
assert est["a9999000000318321"] == {"estado": "no testeada", "desde": "nuevo", "fila": 2, "anun": "Lia Crochê", "motivo": ""}, est["a9999000000318321"]
assert est["a888"]["fila"] == 0 and len(est["a888"]["anun"]) == 60, est["a888"]
assert est["h1abc"]["motivo"].startswith("la vi en el dash x") and len(est["h1abc"]["motivo"]) == de.MOTIVO_MAX, "motivo en una linea y acotado"
m, url, headers, _ = f.calls[0]
assert m == "GET" and url == de.URL and url.endswith("/api/radar-estado"), (m, url)
assert headers["x-clave-radar"] == CLAVE and CLAVE not in url, "la llave viaja en la cabecera, nunca en la URL"
for status, trozo in ((401, "rechazo la llave"), (503, "CLAVE_RADAR"), (500, "500")):
    try:
        de.traer_estados(CLAVE, FakeDash([Resp(status, {})]))
        assert False, f"{status} deberia fallar"
    except de.DashError as e:
        assert trozo in str(e) and CLAVE not in str(e), (status, str(e))
for malo in (Resp(200, None), Resp(200, {"ok": False, "estados": {}}), Resp(200, {"ok": True}), Resp(200, [1, 2]), requests.ConnectionError("sin red " + CLAVE)):
    try:
        de.traer_estados(CLAVE, FakeDash([malo]))
        assert False, "deberia fallar"
    except de.DashError as e:
        assert CLAVE not in str(e), "el mensaje de error no repite la llave"

# ---------------------------------------------------------------- avisar
f = FakeDash([Resp(200, {"ok": True})])
assert de.avisar(CLAVE, {"aplicados": 1, "ya": 0, "conflictos": 0, "sinFila": 0, "error": ""}, f) is True
m, url, headers, cuerpo = f.calls[0]
assert m == "POST" and cuerpo == {"sync": {"aplicados": 1, "ya": 0, "conflictos": 0, "sinFila": 0, "error": ""}} and headers["x-clave-radar"] == CLAVE
assert de.avisar(CLAVE, {}, FakeDash([requests.ConnectionError("x")])) is False, "avisar nunca lanza"
assert de.avisar(CLAVE, {}, FakeDash([Resp(500, {})])) is False

# ---------------------------------------------------------------- planear
FILAS = [
    {"_row": 2, "Anunciante": "Lia Crochê", "Link del anuncio": "https://www.facebook.com/ads/library/?id=9999000000318321", "Status": "nuevo"},
    {"_row": 3, "Anunciante": "Mundo do Fio", "Link del anuncio": "https://www.facebook.com/ads/library/?id=555500001111", "Status": "vigilar"},
    {"_row": 4, "Anunciante": "Sin Enlace SA", "Link del anuncio": "no visible", "Status": ""},
    {"_row": 5, "Anunciante": "Otro", "Link del anuncio": "https://www.facebook.com/ads/library/?id=777700001111&x=javascript:alert(1)", "Status": "GANADORA "},
    {"_row": 6, "Anunciante": "Duplicado", "Link del anuncio": "https://www.facebook.com/ads/library/?id=9999000000318321", "Status": "nuevo"},   # el mismo anuncio que la fila 2
]
ESTADOS = {
    "a9999000000318321": {"estado": "no testeada", "desde": "nuevo", "fila": 2, "anun": "Lia Crochê"},       # se aplica (solo a la primera fila con ese anuncio)
    "a555500001111": {"estado": "vigilar", "desde": "nuevo", "fila": 3, "anun": "Mundo do Fio"},             # la planilla ya dice vigilar
    "a777700001111": {"estado": "descartada", "desde": "nuevo", "fila": 5, "anun": "Otro"},                  # la planilla dice ganadora (otra cosa): gana la planilla
    "hzzz": {"estado": "vigilar", "desde": "", "fila": 4, "anun": "sin ENLACE sa"},                          # sin id de anuncio: fila + nombre (sin tildes ni mayusculas); vacio -> vigilar
    "hyyy": {"estado": "vigilar", "desde": "", "fila": 4, "anun": "Otro nombre"},                            # el nombre no coincide: no se toca
    "a99999000": {"estado": "vigilar", "desde": "nuevo", "fila": 0, "anun": ""},                             # no hay esa fila
    "hxxx": {"estado": "vigilar", "desde": "", "fila": 4, "anun": ""},                                       # sin nombre para comprobar: no se toca
}
cambios, r = de.planear(FILAS, ESTADOS)
assert [(c["fila"], c["de"], c["a"]) for c in cambios] == [(2, "nuevo", "no testeada"), (4, "", "vigilar")], cambios
assert r == {"aplicados": 2, "ya": 1, "conflictos": 1, "sinFila": 3}, r
assert all(c["fila"] != 6 for c in cambios), "el anuncio repetido solo cuenta en su primera fila (como la pagina)"
# el motivo que el equipo escribe en el dash (2026-11-04): solo si la fila queda en ese estado y el texto cambio
FILAS_M = FILAS + [{"_row": 7, "Anunciante": "Ya Motivada", "Link del anuncio": "https://www.facebook.com/ads/library/?id=888800001111", "Status": "descartada",
                    de.MOTIVO_COL: "no vendio en 5 dias"}]
EST_M = {**ESTADOS, "a9999000000318321": {**ESTADOS["a9999000000318321"], "motivo": "pide videos que no tenemos"},
         "a777700001111": {**ESTADOS["a777700001111"], "motivo": "conflicto: no se escribe"},
         "a888800001111": {"estado": "descartada", "desde": "testiando", "fila": 7, "anun": "Ya Motivada", "motivo": "no vendio en 5 dias"},
         "hzzz": {**ESTADOS["hzzz"], "motivo": "falta revisar el precio"}}
cam_m, _ = de.planear(FILAS_M, EST_M)
mot = de.planear_motivos(FILAS_M, EST_M, cam_m)
assert [(m["fila"], m["motivo"]) for m in mot] == [(2, "pide videos que no tenemos"), (4, "falta revisar el precio")], mot
EST_M["a888800001111"]["motivo"] = "no vendio en 14 dias, ROAS 0,6"
assert (7, "no vendio en 14 dias, ROAS 0,6") in [(m["fila"], m["motivo"]) for m in de.planear_motivos(FILAS_M, EST_M, cam_m)], "un motivo nuevo se reescribe"
# el tope por pasada
muchas = [{"_row": i, "Anunciante": f"A{i}", "Link del anuncio": f"https://www.facebook.com/ads/library/?id={10000 + i}", "Status": "nuevo"} for i in range(2, 90)]
ests = {f"a{10000 + i}": {"estado": "descartada", "desde": "nuevo", "fila": i, "anun": f"A{i}"} for i in range(2, 90)}
cam, rr = de.planear(muchas, ests)
assert len(cam) == de.MAX_CAMBIOS == rr["aplicados"], (len(cam), rr)
cam, rr = de.planear(muchas, ests, max_cambios=5)
assert len(cam) == 5 and rr["aplicados"] == 5

# ---------------------------------------------------------------- radar.sincronizar_estados (de punta a punta, con el dash y Google simulados)
class FakeSheet:
    escrituras = []
    filas = FILAS

    def read_dicts(self, tab):
        assert tab == "OFERTAS"
        return [dict(x) for x in FakeSheet.filas]

    def headers(self, tab):
        return ["Fecha", "Anunciante", "Status", "Link del anuncio"]

    def update_cells(self, tab, cells):
        FakeSheet.escrituras.append((tab, dict(cells)))


avisos = []
real = (de.leer_clave, de.traer_estados, de.avisar, sheets.available, sheets.Sheet)
de.avisar = lambda clave, resumen, **k: avisos.append((clave, dict(resumen)))
sheets.available = lambda: True
sheets.Sheet = FakeSheet
try:
    # a) normal: escribe SOLO la columna Status de las filas que tocaba (C), avisa como le fue y no imprime la llave
    de.leer_clave = lambda: CLAVE
    de.traer_estados = lambda clave, **k: ESTADOS
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0, out
    assert FakeSheet.escrituras == [("OFERTAS", {"C2": "no testeada", "C4": "vigilar"})], FakeSheet.escrituras
    assert avisos[-1][0] == CLAVE and avisos[-1][1] == {"aplicados": 2, "ya": 1, "conflictos": 1, "sinFila": 3, "error": ""}, avisos[-1]
    assert CLAVE not in out and "aplicados: 2" in out and "fila 2" in out, out

    # b) simulacion: no escribe ni avisa
    FakeSheet.escrituras.clear(); avisos.clear()
    code, out = dice(lambda: radar.sincronizar_estados(simular=True))
    assert code == 0 and FakeSheet.escrituras == [] and avisos == [] and "[simulacion] pasaria" in out and "SIMULACION" in out, out

    # c) sin llave: no hace nada ni llama al dash
    llamo = []
    de.leer_clave = lambda: ""
    de.traer_estados = lambda clave, **k: llamo.append(1) or {}
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and llamo == [] and "Sin dash.key" in out and FakeSheet.escrituras == [] and avisos == [], out

    # d) el dash no responde: falla (1), no toca la planilla y no avisa
    def cae(clave, **k):
        raise de.DashError("el dash no respondio (ConnectionError)")
    de.leer_clave = lambda: CLAVE
    de.traer_estados = cae
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 1 and FakeSheet.escrituras == [] and avisos == [] and "No pude leer los estados del dash" in out and CLAVE not in out, out

    # e) la planilla rechaza la escritura: nada aplicado, error avisado al dash, codigo 1
    de.traer_estados = lambda clave, **k: ESTADOS
    class SheetFalla(FakeSheet):
        def update_cells(self, tab, cells):
            raise sheets.SheetsError("Google Sheets 403: sin permiso")
    sheets.Sheet = SheetFalla
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 1 and avisos[-1][1]["aplicados"] == 0 and "403" in avisos[-1][1]["error"], (code, avisos[-1], out)
    sheets.Sheet = FakeSheet

    # f) nada elegido en el dash: no lee la planilla pero SI avisa (es el latido que ve la ficha del dash)
    avisos.clear()
    de.traer_estados = lambda clave, **k: {}
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and FakeSheet.escrituras == [] and avisos[-1][1] == {"aplicados": 0, "ya": 0, "conflictos": 0, "sinFila": 0, "error": ""}, (avisos, out)

    # g) falta autorizar la planilla / la pestana no tiene Status: error avisado, codigo 1
    de.traer_estados = lambda clave, **k: ESTADOS
    sheets.available = lambda: False
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 1 and "autorizar" in avisos[-1][1]["error"], avisos[-1]
    sheets.available = lambda: True
    class SheetSinStatus(FakeSheet):
        def headers(self, tab):
            return ["Fecha", "Anunciante"]
    sheets.Sheet = SheetSinStatus
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 1 and "Status" in avisos[-1][1]["error"], avisos[-1]
finally:
    de.leer_clave, de.traer_estados, de.avisar, sheets.available, sheets.Sheet = real

# ---------------------------------------------------------------- «testiando» (2026-10-16): el dash lo manda a la planilla como los otros cinco estados
assert "testiando" in de.ESTADOS and set(de.ESTADOS) == set(radar.STATUS_VALUES), "el dash y el radar hablan de los mismos seis estados"
bueno_t = {"ok": True, "estados": {
    "a111111": {"estado": "testiando", "desde": "nuevo", "fila": 7, "anun": "Moleque Arteiro"},
    "a222222": {"estado": "ganadora", "desde": "testiando", "fila": 8, "anun": "Otro"},        # la planilla decia testiando y el dueno la paso a ganadora
    "a333333": {"estado": "testiando", "desde": "nuevo", "fila": 9, "anun": "Ya"},            # la planilla ya dice testiando
}}
est_t = de.traer_estados(CLAVE, FakeDash([Resp(200, bueno_t)]))
assert sorted(est_t) == ["a111111", "a222222", "a333333"] and est_t["a111111"]["estado"] == "testiando" and est_t["a222222"]["desde"] == "testiando", est_t
FILAS_T = [
    {"_row": 7, "Anunciante": "Moleque Arteiro", "Link del anuncio": "https://www.facebook.com/ads/library/?id=111111", "Status": "nuevo"},
    {"_row": 8, "Anunciante": "Otro", "Link del anuncio": "https://www.facebook.com/ads/library/?id=222222", "Status": "Testiando "},
    {"_row": 9, "Anunciante": "Ya", "Link del anuncio": "https://www.facebook.com/ads/library/?id=333333", "Status": "TESTIANDO"},
]
cambios_t, r_t = de.planear(FILAS_T, est_t)
assert [(c["fila"], c["de"], c["a"]) for c in cambios_t] == [(7, "nuevo", "testiando"), (8, "testiando", "ganadora")], cambios_t
assert r_t == {"aplicados": 2, "ya": 1, "conflictos": 0, "sinFila": 0}, r_t
# de punta a punta: escribe SOLO la columna Status (C) de esas filas, con las palabras del menu, y avisa como le fue
real_t = (de.leer_clave, de.traer_estados, de.avisar, sheets.available, sheets.Sheet, FakeSheet.filas)
avisos.clear(); FakeSheet.escrituras.clear()
de.leer_clave = lambda: CLAVE
de.traer_estados = lambda clave, **k: est_t
de.avisar = lambda clave, resumen, **k: avisos.append((clave, dict(resumen)))
sheets.available = lambda: True
sheets.Sheet = FakeSheet
FakeSheet.filas = FILAS_T
try:
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0, out
    assert FakeSheet.escrituras == [("OFERTAS", {"C7": "testiando", "C8": "ganadora"})], FakeSheet.escrituras
    assert avisos[-1][1] == {"aplicados": 2, "ya": 1, "conflictos": 0, "sinFila": 0, "error": ""}, avisos[-1]
    assert "nuevo -> testiando" in out and "testiando -> ganadora" in out and CLAVE not in out, out
finally:
    de.leer_clave, de.traer_estados, de.avisar, sheets.available, sheets.Sheet, FakeSheet.filas = real_t

# ---------------------------------------------------------------- lo que dice el seguimiento, a la planilla (2026-11-04)
# Las mismas cifras que la tarjeta del dash («Dia 14 · ▼ -27 %»), sacadas de seguimiento.json: dia, tendencia de los anuncios activos, activos y ultima foto.
def foto(d, a):
    return {"d": d, "a": a, "s7": 0}

FOTOS_A = [foto(f"2026-10-{20 + i}", a) for i, a in enumerate([150, 148, 140, 135, 120, 118, 112, 110])]       # 20 al 27 de octubre
r = seguimiento.resultado(FOTOS_A)
assert r == {"dia": 8, "tendencia": -27, "activos": 110, "foto": seguimiento._dia("2026-10-27")}, r            # 110 contra los 150 de hace 7 dias: -26,7 %
r = seguimiento.resultado([foto("2026-10-25", 10), foto("2026-10-26", 12), foto("2026-10-28", 15)])
assert (r["dia"], r["tendencia"]) == (4, 50), r                                   # sin foto de hace 7 dias: contra la primera, porque ya van 3
assert seguimiento.resultado([foto("2026-10-26", 10), foto("2026-10-28", 20)])["tendencia"] is None, "con 2 dias aun no hay tendencia (igual que el dash)"
assert seguimiento.resultado([foto("2026-10-28", 20)]) == {"dia": 1, "tendencia": None, "activos": 20, "foto": seguimiento._dia("2026-10-28")}
assert seguimiento.resultado([foto("2026-10-20", 0), foto("2026-10-27", 5)])["tendencia"] == 100, "de 0 a algo: +100 (como el dash)"
assert seguimiento.resultado([foto("2026-10-20", 0), foto("2026-10-27", 0)])["tendencia"] == 0
assert seguimiento.resultado([foto("2026-10-20", 8), foto("2026-10-27", 9)])["tendencia"] == 13, "12,5 % sube a 13, como Math.round del dash (round() de Python daria 12)"
# la foto de hace 7 dias exactos o mas, la mas cercana: 1 (100), 3 (80), 10 (60) -> contra la del 3
assert seguimiento.resultado([foto("2026-10-01", 100), foto("2026-10-03", 80), foto("2026-10-10", 60)])["tendencia"] == -25
# dos fotos del mismo dia: gana la ultima; las que no traen un dia real o un total se ignoran
r = seguimiento.resultado([foto("2026-10-20", 10), foto("2026-10-27", 99), foto("2026-10-27", 20), {"d": "2026-02-31", "a": 5}, {"d": "2026-10-28", "a": "x"},
                           {"d": "2026-10-29", "a": True}, {"a": 3}, "texto", None])
assert (r["dia"], r["activos"], r["tendencia"]) == (8, 20, 100), r
assert seguimiento.resultado([]) is None and seguimiento.resultado(None) is None and seguimiento.resultado([{"d": "x", "a": 1}]) is None
assert seguimiento.valores_resultado(seguimiento.resultado(FOTOS_A)) == {"Seguimiento: día": "8", "Seguimiento: anuncios activos": "110",
                                                                       "Seguimiento: última foto": "27/10/2026", "Seguimiento: tendencia %": "-27"}
assert "Seguimiento: tendencia %" not in seguimiento.valores_resultado(seguimiento.resultado([foto("2026-10-28", 20)])), "una tendencia que aun no existe no se escribe"

# planear: una fila se halla por el id que le da el dash; solo las celdas que cambian; nunca borra
FILA_SIN_ENLACE = {"_row": 6, "Fecha": "15/10/2026", "Anunciante": "Sin Enlace", "Palabra clave": "moldes", "Status": "testiando", "Link del anuncio": ""}
ID_SIN_ENLACE = seguimiento.id_oferta(FILA_SIN_ENLACE)
assert ID_SIN_ENLACE.startswith("h")
FILAS_S = [
    {"_row": 2, "Anunciante": "Probada", "Status": "testiando", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900001", "Seguimiento: día": " 8 "},
    {"_row": 3, "Anunciante": "Ya Decidida", "Status": "Descartada", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900002",
     "Seguimiento: día": "1", "Seguimiento: anuncios activos": "20", "Seguimiento: última foto": "28/10/2026"},
    {"_row": 4, "Anunciante": "Probada (repetida)", "Status": "testiando", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900001"},
    {"_row": 5, "Anunciante": "Sin Fotos", "Status": "vigilar", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900003"},
    FILA_SIN_ENLACE,
]
DATOS_S = {"version": 1, "ofertas": {
    "a900001": {"anun": "Probada", "fotos": FOTOS_A},
    "a900002": {"anun": "Ya Decidida", "fotos": [foto("2026-10-28", 20)]},                   # la planilla ya dice lo mismo: nada que escribir
    ID_SIN_ENLACE: {"anun": "Sin Enlace", "fotos": [foto("2026-10-25", 10), foto("2026-10-28", 15)]},
    "a999999": {"anun": "No esta en la planilla", "fotos": FOTOS_A},
}}
pl = seguimiento.planear_resultados(FILAS_S, DATOS_S)
assert [(p["fila"], p["id"]) for p in pl] == [(2, "a900001"), (6, ID_SIN_ENLACE)], pl
assert pl[0]["celdas"] == {"Seguimiento: anuncios activos": "110", "Seguimiento: última foto": "27/10/2026", "Seguimiento: tendencia %": "-27"}, pl[0]   # «8» ya estaba
assert pl[1]["celdas"] == {"Seguimiento: día": "4", "Seguimiento: anuncios activos": "15", "Seguimiento: última foto": "28/10/2026", "Seguimiento: tendencia %": "50"}, pl[1]
assert len(seguimiento.planear_resultados(FILAS_S, DATOS_S, max_filas=1)) == 1, "tope de filas por pasada"
assert seguimiento.planear_resultados(FILAS_S, {"version": 1, "ofertas": {}}) == [] and seguimiento.planear_resultados(FILAS_S, None) == []
# una tendencia que la planilla ya tiene no se borra aunque las fotos de ahora no alcancen para calcularla
FILA_T = [{"_row": 9, "Anunciante": "X", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900009", "Seguimiento: tendencia %": "-10"}]
p9 = seguimiento.planear_resultados(FILA_T, {"ofertas": {"a900009": {"fotos": [foto("2026-10-28", 20)]}}})
assert "Seguimiento: tendencia %" not in p9[0]["celdas"], p9

# de punta a punta: --sync-estados crea las cuatro columnas al final (una vez) y escribe solo esas celdas; la segunda pasada no escribe nada
class SheetSeg:
    head = []
    filas = []
    escrituras = []
    columnas = []

    def read_dicts(self, tab):
        assert tab == "OFERTAS"
        return [dict(x) for x in SheetSeg.filas]

    def headers(self, tab):
        return list(SheetSeg.head)

    def asegurar_columnas(self, tab, nombres):
        nuevos = [n for n in nombres if sheets.fold(n) not in [sheets.fold(h) for h in SheetSeg.head]]
        SheetSeg.head += nuevos
        SheetSeg.columnas.append(list(nuevos))
        return nuevos

    def update_cells(self, tab, cells):
        SheetSeg.escrituras.append((tab, dict(cells)))
        for ref, v in cells.items():                  # lo escrito se lee en la siguiente pasada
            col, fila = "".join(ch for ch in ref if ch.isalpha()), int("".join(ch for ch in ref if ch.isdigit()))
            nombre = SheetSeg.head[[sheets.col_letter(i) for i in range(len(SheetSeg.head))].index(col)]
            next(f for f in SheetSeg.filas if f["_row"] == fila)[nombre] = v


ruta_seg = Path(_TMP) / "seguimiento.json"
ruta_seg.write_text(json.dumps(DATOS_S), encoding="utf-8")
SheetSeg.head = ["Fecha", "Anunciante", "Palabra clave", "Status", "Link del anuncio"]
SheetSeg.filas = [{k: v for k, v in f.items() if not k.startswith("Seguimiento")} for f in FILAS_S]
real_s = (de.leer_clave, de.traer_estados, de.avisar, sheets.available, sheets.Sheet, radar.RUTA_SEGUIMIENTO)
avisos.clear()
de.leer_clave = lambda: CLAVE
de.avisar = lambda clave, resumen, **k: avisos.append((clave, dict(resumen)))
sheets.available = lambda: True
sheets.Sheet = SheetSeg
radar.RUTA_SEGUIMIENTO = str(ruta_seg)
try:
    # a) nada elegido en el dash pero hay fotos: lee la planilla, crea las columnas y escribe el seguimiento (y el aviso del dash sigue sin error)
    de.traer_estados = lambda clave, **k: {}
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0, out
    assert SheetSeg.columnas == [list(seguimiento.RESULTADO_COLS)] and SheetSeg.head[5:] == list(seguimiento.RESULTADO_COLS), SheetSeg.head
    assert len(SheetSeg.escrituras) == 1, SheetSeg.escrituras
    esc = SheetSeg.escrituras[0][1]
    assert esc == {"F2": "8", "G2": "-27", "H2": "110", "I2": "27/10/2026", "F3": "1", "H3": "20", "I3": "28/10/2026",
                   "F6": "4", "G6": "50", "H6": "15", "I6": "28/10/2026"}, esc       # F..I = las cuatro nuevas; la fila repetida (4) y la sin fotos (5) no se tocan
    assert all(ref[0] in "FGHI" for ref in esc), "solo las columnas del seguimiento: nunca Status ni otra"
    assert avisos[-1][1]["error"] == "" and "Seguimiento a la planilla: 3 filas" in out and "fila 2 (Probada)" in out, (avisos, out)
    # b) la segunda pasada: nada cambio, nada se escribe ni se crea
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and len(SheetSeg.escrituras) == 1 and len(SheetSeg.columnas) == 1 and "Seguimiento a la planilla: 0 filas" in out, out
    # c) llega una foto nueva: solo cambian las celdas que cambian
    d2 = json.loads(ruta_seg.read_text(encoding="utf-8"))
    d2["ofertas"]["a900001"]["fotos"].append(foto("2026-10-28", 110))
    ruta_seg.write_text(json.dumps(d2), encoding="utf-8")
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and SheetSeg.escrituras[-1][1] == {"F2": "9", "G2": "-26", "I2": "28/10/2026"}, SheetSeg.escrituras[-1]   # 110 contra los 148 del 21: -25,7 %
    # d) con estados del dash: los dos van en la misma pasada (una lectura de la planilla) y el estado sigue escribiendo solo Status
    ruta_seg.write_text(json.dumps(DATOS_S), encoding="utf-8")
    SheetSeg.filas = [{k: v for k, v in f.items() if not k.startswith("Seguimiento")} for f in FILAS_S]
    SheetSeg.escrituras.clear()
    de.traer_estados = lambda clave, **k: {"a900003": {"estado": "descartada", "desde": "vigilar", "fila": 5, "anun": "Sin Fotos", "motivo": ""}}
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and SheetSeg.escrituras[0] == ("OFERTAS", {"D5": "descartada"}) and len(SheetSeg.escrituras) == 2 and "F2" in SheetSeg.escrituras[1][1], SheetSeg.escrituras
    assert avisos[-1][1]["aplicados"] == 1 and avisos[-1][1]["error"] == "", avisos[-1]
    # e) simulacion: dice lo que escribiria y no escribe ni crea nada
    SheetSeg.filas = [{k: v for k, v in f.items() if not k.startswith("Seguimiento")} for f in FILAS_S]
    SheetSeg.escrituras.clear(); SheetSeg.columnas.clear(); avisos.clear()
    de.traer_estados = lambda clave, **k: {}
    code, out = dice(lambda: radar.sincronizar_estados(simular=True))
    assert code == 0 and SheetSeg.escrituras == [] and SheetSeg.columnas == [] and avisos == [] and "[simulacion] escribiria el seguimiento de la fila 2" in out, out
    # f) la planilla rechaza la escritura del seguimiento: codigo 1 y se dice, pero el aviso de los estados al dash no se ensucia
    class SheetSegFalla(SheetSeg):
        def update_cells(self, tab, cells):
            raise sheets.SheetsError("Google Sheets 403: sin permiso")
    sheets.Sheet = SheetSegFalla
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 1 and "No pude escribir el seguimiento" in out and avisos[-1][1]["error"] == "", (code, avisos, out)
    # g) sin fotos (seguimiento.json no existe): ni lee la planilla si no hay estados
    sheets.Sheet = SheetSeg
    radar.RUTA_SEGUIMIENTO = str(Path(_TMP) / "no-existe.json")
    class SheetNoLeer(SheetSeg):
        def read_dicts(self, tab):
            raise AssertionError("no debia leer la planilla")
    sheets.Sheet = SheetNoLeer
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and "Seguimiento a la planilla" not in out, out
finally:
    de.leer_clave, de.traer_estados, de.avisar, sheets.available, sheets.Sheet, radar.RUTA_SEGUIMIENTO = real_s

# ---------------------------------------------------------------- nuestra prueba de cada oferta, a la planilla (2026-11-04)
# En el dash el dueno une la oferta del radar con una oferta propia; /api/radar-prueba devuelve como le va (gasto, ventas, n.º de ventas, ROAS, costo por venta).
bueno_p = {"ok": True, "hoy": "2026-11-04", "generadoEn": "2026-11-04T10:00:00-05:00", "resultados": {
    "a900001": {"oferta": "ZAPDATA SERMONES\nBIBLICOS", "inicio": "2026-10-28", "dias": 8, "encontrada": True, "gastoUsd": 39.333, "ventasUsd": 47.5, "ventas": 4,
                "roas": 1.21, "cpaUsd": 9.83, "paises": ["colombia", "mexico"]},
    "a900002": {"oferta": "RENOMBRADA", "inicio": "2026-11-01", "dias": 4, "encontrada": False, "gastoUsd": None, "ventasUsd": None, "ventas": None, "roas": None, "cpaUsd": None},
    "a900003": {"oferta": "VIEJA", "inicio": "2026-11-01", "dias": 4, "encontrada": True, "gastoUsd": 10, "ventasUsd": 30, "ventas": None, "roas": 3, "cpaUsd": None},
    "a900004": {"oferta": "X", "inicio": "2026-02-31", "encontrada": True},              # dia que no existe: fuera
    "a900005": {"oferta": "", "inicio": "2026-11-01"},                                    # sin oferta: fuera
    "../malo": {"oferta": "X", "inicio": "2026-11-01"},                                   # id raro: fuera
    "a900006": "texto",                                                                    # no es un objeto: fuera
    "a900007": {"oferta": "Y", "inicio": "2026-11-01", "encontrada": True, "gastoUsd": "12", "ventasUsd": -3, "ventas": True, "roas": float("inf"), "cpaUsd": None},
}}
f = FakeDash([Resp(200, bueno_p)])
res = de.traer_resultados(CLAVE, f)
assert sorted(res) == ["a900001", "a900002", "a900003", "a900007"], sorted(res)
assert res["a900001"] == {"oferta": "ZAPDATA SERMONES BIBLICOS", "inicio": seguimiento._dia("2026-10-28"), "dias": 8, "encontrada": True, "gasto": 39.33, "ventas_usd": 47.5,
                          "ventas": 4, "roas": 1.21, "cpa": 9.83}, res["a900001"]
assert [res["a900007"][k] for k in ("gasto", "ventas_usd", "ventas", "roas")] == [None, None, None, None], "lo que no es un numero sano no se escribe (texto, negativo, booleano, infinito)"
m, url, headers, _ = f.calls[0]
assert m == "GET" and url == de.URL_PRUEBA and url.endswith("/api/radar-prueba") and headers["x-clave-radar"] == CLAVE and CLAVE not in url, (m, url)
for malo in (Resp(401, {}), Resp(503, {}), Resp(502, {"ok": False, "error": "No pude leer el catalogo"}), Resp(200, None), Resp(200, {"ok": True}), Resp(200, {"ok": True, "resultados": []}),
             requests.ConnectionError("sin red " + CLAVE)):
    try:
        de.traer_resultados(CLAVE, FakeDash([malo]))
        assert False, "deberia fallar"
    except de.DashError as e:
        assert CLAVE not in str(e), "el mensaje de error no repite la llave"

# las celdas: texto, dinero con 2 decimales; lo que el dash no sabe no se escribe
assert de.valores_prueba(res["a900001"]) == {"Prueba: oferta del dash": "ZAPDATA SERMONES BIBLICOS", "Prueba: desde": "28/10/2026", "Prueba: días": "8", "Prueba: gasto USD": "39.33",
                                             "Prueba: ventas USD": "47.50", "Prueba: n.º de ventas": "4", "Prueba: ROAS": "1.21", "Prueba: costo por venta USD": "9.83"}
assert de.valores_prueba(res["a900002"]) == {"Prueba: oferta del dash": "RENOMBRADA", "Prueba: desde": "01/11/2026", "Prueba: días": "4"}, "una oferta que el dash ya no halla: sin cifras inventadas"
v3 = de.valores_prueba(res["a900003"])
assert "Prueba: n.º de ventas" not in v3 and "Prueba: costo por venta USD" not in v3 and v3["Prueba: ROAS"] == "3.00", v3

# planear: una fila se halla por el id que le da el dash; solo las celdas que cambian; nunca borra
FILA_PR_H = {"_row": 6, "Fecha": "15/10/2026", "Anunciante": "Sin Enlace", "Palabra clave": "moldes", "Status": "ganadora", "Link del anuncio": ""}
ID_PR_H = seguimiento.id_oferta(FILA_PR_H)
FILAS_P = [
    {"_row": 2, "Anunciante": "Probada", "Status": "testiando", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900001", "Prueba: días": " 8 "},
    {"_row": 3, "Anunciante": "Renombrada", "Status": "descartada", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900002", "Prueba: gasto USD": "55.00"},
    {"_row": 4, "Anunciante": "Probada (repetida)", "Status": "testiando", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900001"},
    {"_row": 5, "Anunciante": "Sin Unir", "Status": "testiando", "Link del anuncio": "https://www.facebook.com/ads/library/?id=900009"},
    FILA_PR_H,
]
RES_P = {"a900001": res["a900001"], "a900002": res["a900002"], ID_PR_H: {**res["a900003"], "oferta": "VIEJA"}, "a999999": res["a900001"]}
pl = de.planear_pruebas(FILAS_P, RES_P)
assert [(p["fila"], p["id"]) for p in pl] == [(2, "a900001"), (3, "a900002"), (6, ID_PR_H)], pl
assert "Prueba: días" not in pl[0]["celdas"] and pl[0]["celdas"]["Prueba: gasto USD"] == "39.33" and len(pl[0]["celdas"]) == 7, pl[0]    # «8» ya estaba
assert pl[1]["celdas"] == {"Prueba: oferta del dash": "RENOMBRADA", "Prueba: desde": "01/11/2026", "Prueba: días": "4"}, "el gasto que ya tenia no se borra aunque el dash ya no lo sepa"
assert len(de.planear_pruebas(FILAS_P, RES_P, max_filas=1)) == 1 and de.planear_pruebas(FILAS_P, {}) == []

# de punta a punta: --sync-estados lo pide como mucho una vez por hora, crea las ocho columnas al final (una vez) y escribe solo esas celdas
class SheetPr(SheetSeg):
    pass


pedidos_p = []
def trae_p(clave, **k):
    pedidos_p.append(clave)
    return RES_P


real_p = (de.leer_clave, de.traer_estados, de.traer_resultados, de.avisar, sheets.available, sheets.Sheet, radar.RUTA_SEGUIMIENTO, radar.RUTA_PRUEBAS_MARCA)
marca_p = Path(_TMP) / "pruebas-e2e.marca"
SheetSeg.head = ["Fecha", "Anunciante", "Palabra clave", "Status", "Link del anuncio"]
SheetSeg.filas = [{k: v for k, v in f.items() if not k.startswith("Prueba")} for f in FILAS_P]
SheetSeg.escrituras, SheetSeg.columnas = [], []
avisos.clear()
de.leer_clave = lambda: CLAVE
de.traer_estados = lambda clave, **k: {}
de.traer_resultados = trae_p
de.avisar = lambda clave, resumen, **k: avisos.append((clave, dict(resumen)))
sheets.available = lambda: True
sheets.Sheet = SheetPr
radar.RUTA_SEGUIMIENTO = str(Path(_TMP) / "no-existe.json")
radar.RUTA_PRUEBAS_MARCA = str(marca_p)
try:
    # a) sin marca (nunca se pidio): lo pide, crea las columnas, escribe y pone la marca
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and pedidos_p == [CLAVE] and marca_p.exists(), (code, pedidos_p, out)
    assert SheetSeg.columnas == [list(de.PRUEBA_COLS)] and SheetSeg.head[5:] == list(de.PRUEBA_COLS), SheetSeg.head
    esc = SheetSeg.escrituras[0][1]
    assert esc["F2"] == "ZAPDATA SERMONES BIBLICOS" and esc["I2"] == "39.33" and esc["L2"] == "1.21" and esc["M2"] == "9.83" and esc["F3"] == "RENOMBRADA" and "I3" not in esc, esc
    assert not any(ref.endswith("4") or ref.endswith("5") for ref in esc), "la fila repetida (4) y la que no esta unida (5) no se tocan"
    assert all(ref[0] in "FGHIJKLM" for ref in esc), "solo las columnas de la prueba: nunca Status ni otra"
    assert "Pruebas a la planilla: 4 ofertas unidas" in out and "la prueba de la fila 2 (Probada)" in out and CLAVE not in out and avisos[-1][1]["error"] == "", (out, avisos)
    # b) otra pasada dentro de la hora: ni lo pide
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and pedidos_p == [CLAVE] and len(SheetSeg.escrituras) == 1 and "Pruebas a la planilla" not in out, out
    # c) paso la hora: lo pide otra vez, y como nada cambio no escribe nada
    os.utime(marca_p, (time.time() - 2 * 3600, time.time() - 2 * 3600))
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and pedidos_p == [CLAVE, CLAVE] and len(SheetSeg.escrituras) == 1 and len(SheetSeg.columnas) == 1 and "4 ofertas unidas en el dash | 0 filas con algo nuevo" in out, out
    # d) el dash no contesta: codigo 1 y se dice; la marca se pone igual (no se insiste cada 5 minutos) y el aviso de los estados no se ensucia
    os.utime(marca_p, (time.time() - 2 * 3600, time.time() - 2 * 3600))
    def cae_p(clave, **k):
        raise de.DashError("el dash contesto 502")
    de.traer_resultados = cae_p
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 1 and "No pude leer el resultado de las pruebas" in out and time.time() - marca_p.stat().st_mtime < 60 and avisos[-1][1]["error"] == "", (code, out)
    # e) simulacion: dice lo que escribiria, no escribe ni crea columnas ni pone la marca
    de.traer_resultados = trae_p
    marca_p.unlink()
    SheetSeg.filas = [{k: v for k, v in f.items() if not k.startswith("Prueba")} for f in FILAS_P]
    SheetSeg.head = ["Fecha", "Anunciante", "Palabra clave", "Status", "Link del anuncio"]
    SheetSeg.escrituras.clear(); SheetSeg.columnas.clear()
    code, out = dice(lambda: radar.sincronizar_estados(simular=True))
    assert code == 0 and SheetSeg.escrituras == [] and SheetSeg.columnas == [] and not marca_p.exists() and "[simulacion] escribiria la prueba de la fila 2" in out, out
    # f) la planilla rechaza la escritura: codigo 1 y se dice
    class SheetPrFalla(SheetPr):
        def update_cells(self, tab, cells):
            raise sheets.SheetsError("Google Sheets 403: sin permiso")
    sheets.Sheet = SheetPrFalla
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 1 and "No pude escribir el resultado de las pruebas" in out, (code, out)
    # g) con estados elegidos en el dash: una sola lectura de la planilla para todo, y el estado sigue escribiendo solo Status
    sheets.Sheet = SheetPr
    marca_p.unlink()
    SheetSeg.escrituras.clear()
    lecturas = []
    class SheetPrCuenta(SheetPr):
        def read_dicts(self, tab):
            lecturas.append(tab)
            return super().read_dicts(tab)
    sheets.Sheet = SheetPrCuenta
    de.traer_estados = lambda clave, **k: {"a900009": {"estado": "descartada", "desde": "testiando", "fila": 5, "anun": "Sin Unir", "motivo": ""}}
    code, out = dice(lambda: radar.sincronizar_estados())
    assert code == 0 and lecturas == ["OFERTAS"] and SheetSeg.escrituras[0] == ("OFERTAS", {"D5": "descartada"}), (lecturas, SheetSeg.escrituras, out)
finally:
    de.leer_clave, de.traer_estados, de.traer_resultados, de.avisar, sheets.available, sheets.Sheet, radar.RUTA_SEGUIMIENTO, radar.RUTA_PRUEBAS_MARCA = real_p

# ---------------------------------------------------------------- la linea de comandos de verdad (sin llave en esta carpeta: no sale a la red)
env = {k: v for k, v in os.environ.items() if k != "DASH_CLAVE_RADAR"}
p = subprocess.run([sys.executable, "-X", "utf8", "radar.py", "--sync-estados"], capture_output=True, text=True, env=env, cwd=str(Path(__file__).resolve().parent), timeout=60)
if not (Path(__file__).resolve().parent / "dash.key").exists():
    assert p.returncode == 0 and "Sin dash.key" in p.stdout, (p.returncode, p.stdout, p.stderr)
h = subprocess.run([sys.executable, "-X", "utf8", "radar.py", "--help"], capture_output=True, text=True, env=env, cwd=str(Path(__file__).resolve().parent), timeout=60)
assert "--sync-estados" in h.stdout, "la opcion --sync-estados existe"

print("TODO OK (estados del dash a la planilla, dash y Google simulados)")
