"""Pruebas SIN red de seguimiento.py y de lo que el radar le agrega (Meta.pagina_anunciante, Meta.pagina_de_anuncio, radar.seguir_ofertas).
Meta, el dash y la planilla estan simulados. Corre con:  python -X utf8 test_seguimiento.py"""
import contextlib
import io
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import dash_estados
import radar
import seguimiento as sg
import sheets

CLAVE = "llave-de-prueba-0123456789abcdef"
bien = 0


def ok(cond, msg):
    global bien
    assert cond, msg
    bien += 1


def igual(a, b, msg):
    ok(a == b, f"{msg}: obtuve {a!r} y esperaba {b!r}")


DIA = 86400
# 2026-10-21 09:30 en Bogota = 14:30 UTC. El calendario de Meta (Pacifico) esta en el 21 y su medianoche es 07:00 UTC.
AHORA = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)
HOY_META = datetime(2026, 10, 21, 7, 0, tzinfo=timezone.utc)
TS_HOY = int(HOY_META.timestamp())


def ts(dias_atras):
    return TS_HOY - dias_atras * DIA


def anuncio(i, dias_atras, col=1, video=False, link="https://tienda.example/p", cta="LEARN_MORE", sig=None, likes=1200):
    return {"archive_id": str(i), "collation_id": f"c{i}", "collation_count": col, "page_id": "9990001", "page_name": "X", "start": ts(dias_atras), "end": TS_HOY,
            "active": True, "body": "b", "text": "t", "link": link, "cta": "", "cta_type": cta, "page_like": likes, "video": video, "sig": sig or f"s{i}"}


NOMBRES = {"9990001": "Pagina X", "1110001": "Lia Crochê", "2220002": "Mundo do Fio", "5555555555": "Manual"}


class MetaFalsa:
    """Un anunciante por id de pagina: lista de anuncios activos. Emula el filtro start_date[max] (cuenta los que empezaron ANTES de la fecha)."""

    def __init__(self, paginas, pag_de_anuncio=None, falla=None, nombres=None):
        self.paginas, self.pag_de_anuncio, self.llamadas, self.falla, self.n_pag = paginas, pag_de_anuncio or {}, [], falla, 0
        self.nombres = nombres or NOMBRES

    def pagina_anunciante(self, pid, country="BR", antes_de=None):
        self.llamadas.append((pid, antes_de))
        if self.falla:
            self.n_pag += 1
            e = self.falla(self.n_pag)
            if e:
                raise e
        ads = self.paginas.get(pid)
        if ads is None:
            return None
        ads = [dict(a, page_name=self.nombres.get(pid, "Pagina X")) for a in ads]
        if antes_de:
            lim = int(datetime.strptime(antes_de, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 7 * 3600
            n = sum(a["collation_count"] for a in ads if a["start"] < lim)
            return {"total": n, "ads": [a for a in ads if a["start"] < lim][:30], "hay_mas": False}
        return {"total": sum(a["collation_count"] for a in ads), "ads": ads[:30], "hay_mas": len(ads) > 30}

    def pagina_de_anuncio(self, ad_id):
        self.llamadas.append(("anuncio", ad_id))
        return self.pag_de_anuncio.get(str(ad_id), "")


def fila(row, anunciante, status, link="", palabra="kw", fecha="10/10/2026"):
    return {"_row": row, "Fecha": fecha, "Palabra clave": palabra, "Anunciante": anunciante, "Link del anuncio": link, "Status": status}


class FakeDash:
    def __init__(self, respuestas, manuales=None, get_resp=None):
        self.respuestas, self.calls, self.gets = list(respuestas), [], []
        self.manuales, self.get_resp = manuales or [], get_resp

    def get(self, url, headers=None, timeout=None):
        self.gets.append({"url": url, "headers": headers})
        if isinstance(self.get_resp, Exception):
            raise self.get_resp
        return self.get_resp or Resp(200, {"ok": True, "ofertas": self.manuales})

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        r = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(r, Exception):
            raise r
        return r


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self.payload = status, payload

    def json(self):
        if self.payload is None:
            raise ValueError("no es JSON")
        return self.payload


def sin_pausa():
    pass


def callar(*a, **k):
    pass


# ---------------------------------------------------------------- ids: los mismos que el dash (valores sacados de functions/_radar.js)
igual(sg.huella("Lia Crochê|2026-09-23|receitas passo a passo em PDF"), "1ea3pad", "huella igual a la de JavaScript")
igual(sg.huella("Sin Enlace SA|2026-10-15|sin enlace"), "7ktvd0", "huella ASCII")
igual(sg.huella("Mundo ñandú 🌟|2026-10-11|x"), "14pn5lm", "huella con eñe, tildes y un emoji (dos unidades UTF-16)")
igual(sg.huella(""), "ztntfp", "huella del texto vacio")
igual(sg.id_oferta(fila(5, "  Sin   Enlace\tSA ", "vigilar", "no visible", "sin enlace", "15/10/2026")), "h7ktvd0", "sin id de anuncio: h + huella, con el nombre limpiado como en el dash")
igual(sg.id_oferta(fila(5, "Mundo ñandú 🌟", "vigilar", "", "x\x01y", "2026-10-11")), "hsrtjth", "un caracter de control en la palabra se vuelve espacio, igual que en el dash")
igual(sg.id_oferta(fila(5, "A", "vigilar", "https://www.facebook.com/ads/library/?id=9999000000318321")), "a9999000000318321", "con id de anuncio: a + id")
igual(sg.page_id_de_enlace("https://www.facebook.com/ads/library/?active_status=all&view_all_page_id=9999000000813676&search_type=page"), "9999000000813676", "id de pagina de un enlace de pagina")
igual(sg.page_id_de_enlace("https://www.facebook.com/ads/library/?id=9999000000318321"), "", "un enlace de anuncio no trae pagina")

# ---------------------------------------------------------------- elegir
filas = [fila(2, "A", "vigilar", "https://www.facebook.com/ads/library/?id=1111111111"), fila(3, "B", "nuevo", "https://www.facebook.com/ads/library/?id=2222222222"),
         fila(4, "C", " Testiando ", "https://www.facebook.com/ads/library/?id=3333333333"), fila(5, "A otra vez", "vigilar", "https://www.facebook.com/ads/library/?id=1111111111"),
         fila(6, "D", "ganadora", "https://www.facebook.com/ads/library/?id=4444444444"), fila(7, "E", "vigilar", "https://www.facebook.com/ads/library/?view_all_page_id=5555555555&search_type=page"),
         fila(8, "F", "descartada", "https://www.facebook.com/ads/library/?id=6666666666")]
sel = sg.elegir(filas)
igual([o["anunciante"] for o in sel], ["A", "C", "E"], "se siguen vigilar y testiando (sin importar mayusculas ni espacios); una fila repetida no cuenta dos veces")
igual([o["estado"] for o in sel], ["vigilar", "testiando", "vigilar"], "estado normalizado")
igual(sel[2]["page_id"], "5555555555", "la pagina sale del enlace cuando la fila la trae")
igual(sg.indice_nombres({"123456": {"name": "Lia Crochê"}, "223456": {"name": "lia croche"}, "abc": {"name": "mal"}, "323456": {"name": "Otra"}}),
      {"lia croche": ["123456", "223456"], "otra": ["323456"]}, "indice de nombres sin tildes; id de pagina invalido descartado; nombre repetido lista las dos paginas")

# ---------------------------------------------------------------- foto_de
ads = [anuncio(1, 90, col=3, video=True, link="https://wa.me/55119999", likes=800), anuncio(2, 45, video=True), anuncio(3, 10, link="https://api.whatsapp.com/send?x=1"),
       anuncio(4, 3, cta="WHATSAPP_MESSAGE"), anuncio(5, 0, sig="s1")]
meta = MetaFalsa({"9990001": ads})
principal = meta.pagina_anunciante("9990001")
igual(principal["total"], 7, "el total suma las versiones (collation_count) de cada resultado")
f, ids = sg.foto_de(principal, {0: 6, 7: 5, 30: 3, 60: 3}, None, AHORA)
igual(f["d"], "2026-10-21", "el dia sale del end_date de los anuncios activos (calendario de Meta)")
igual((f["a"], f["s0"], f["s7"], f["s30"], f["s60"]), (7, 6, 5, 3, 3), "total y sobrevivientes por edad")
igual(f["p"], 4, "piezas distintas: el anuncio 5 repite la firma del 1")
igual(f["vd"], 50, "2 de 4 piezas son video")
igual(f["wa"], 60, "3 de 5 anuncios mandan a WhatsApp (por enlace o por boton)")
igual((f["v"], f["lk"], f["vis"], f["top"]), (3, 1200, 5, "1"), "version mas repetida, seguidores, visibles y anuncio estrella")
ok("k" not in f and "nv" not in f, "sin foto anterior no hay apagados ni nuevos")
igual(ids, ["1", "2", "3", "4", "5"], "ids de la pagina 1")
# un corte que Meta contradice (mayor que el anterior) se recorta: la curva nunca sube con la edad
f2, _ = sg.foto_de(principal, {0: 6, 7: 2, 30: 4, 60: 1}, None, AHORA)
igual((f2["s0"], f2["s7"], f2["s30"], f2["s60"]), (6, 2, 2, 1), "los sobrevivientes nunca suben con la edad")
f3, _ = sg.foto_de(principal, {0: 99}, None, AHORA)
igual(f3["s0"], 7, "ni pasan del total")
ok("s7" not in f3 and "s30" not in f3, "un corte que no llego no se inventa")
# con foto anterior
f4, _ = sg.foto_de(principal, {}, {"ids": ["1", "2", "9", "10"], "d": "2026-10-20"}, AHORA)
igual((f4["k"], f4["nv"], f4["pd"]), (2, 3, "2026-10-20"), "apagados = de la foto anterior que ya no estan; nuevos = de hoy que antes no estaban")
# sin total de Meta: si la pagina trae todo, es lo que se ve; si no, no hay foto
sin_total = {"total": None, "ads": ads, "hay_mas": False}
igual(sg.foto_de(sin_total, {}, None, AHORA)[0]["a"], 7, "sin count y sin mas paginas, el total es lo que se ve")
igual(sg.foto_de({"total": None, "ads": ads, "hay_mas": True}, {}, None, AHORA), None, "sin count y con mas paginas no se inventa el total")
fz, _ = sg.foto_de({"total": 0, "ads": [], "hay_mas": False}, {}, None, AHORA)
igual((fz["a"], fz["s0"], fz["s7"], fz["s30"], fz["s60"], fz["d"]), (0, 0, 0, 0, 0, "2026-10-21"), "una pagina sin anuncios: todo en cero y el dia sale de la hora (Bogota menos 2 h)")
igual(sg.dia_de_meta([], datetime(2026, 10, 21, 5, 30, tzinfo=timezone.utc)), "2026-10-20", "sin anuncios, a las 05:30 UTC el calendario del Pacifico aun esta en el dia anterior")
igual(sg.dia_bogota(datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc)), "2026-10-21", "las 3 a. m. UTC son las 10 p. m. del 21 en Bogota")

# ---------------------------------------------------------------- tomar_foto: 5 peticiones con las fechas de corte correctas
m = MetaFalsa({"9990001": ads})
f, ids = sg.tomar_foto(m, "9990001", None, sin_pausa, AHORA)
igual((f["pn"], f["pg"], f["hs"]), ("Pagina X", "9990001", "tienda.example"), "la foto dice que pagina se midio: su nombre, su id y el destino al que mas apuntan sus anuncios (sin contar WhatsApp)")
igual(m.llamadas, [("9990001", None), ("9990001", "2026-10-21"), ("9990001", "2026-10-14"), ("9990001", "2026-09-21"), ("9990001", "2026-08-22")], "pagina 1 sin filtro y los cortes de 0, 7, 30 y 60 dias")
igual((f["a"], f["s0"], f["s7"], f["s30"], f["s60"]), (7, 6, 5, 4, 3), "los totales por corte salen del filtro de la Biblioteca")
m = MetaFalsa({"9990001": []})
f, _ = sg.tomar_foto(m, "9990001", None, sin_pausa, AHORA)
igual(len(m.llamadas), 1, "una pagina sin anuncios no pide los cortes")
try:
    sg.tomar_foto(MetaFalsa({}), "404", None, sin_pausa, AHORA)
    ok(False, "una pagina sin resultados debe fallar")
except sg.SinDatos:
    ok(True, "")

# ---------------------------------------------------------------- correr: una pasada completa
LIMITE, RED = radar.RateLimited, (requests.RequestException,)
tmp = Path(tempfile.mkdtemp())
ruta = tmp / "seguimiento.json"
PAG_ANUNCIO = {"2222222222": "2220002"}
PAGINAS = {"1110001": [anuncio(i, 50 - i) for i in range(1, 11)], "2220002": [anuncio(100 + i, 5 + i) for i in range(1, 9)], "5555555555": [anuncio(300 + i, 20) for i in range(1, 4)]}
filas = [fila(2, "Lia Crochê", "vigilar", "https://www.facebook.com/ads/library/?id=1111111111"), fila(3, "Mundo", "testiando", "https://www.facebook.com/ads/library/?id=2222222222"),
         fila(4, "Otra", "nuevo", "https://www.facebook.com/ads/library/?id=3333333333"), fila(5, "Manual", "vigilar", "https://www.facebook.com/ads/library/?view_all_page_id=5555555555&search_type=page")]
nombres = sg.indice_nombres({"1110001": {"name": "Lia Crochê"}})
meta = MetaFalsa(PAGINAS, pag_de_anuncio={"2222222222": "2220002"})
dash = FakeDash([Resp(200, {"ok": True, "guardadas": 3, "rechazadas": 0})])
codigo, r = sg.correr(meta, filas, nombres, CLAVE, LIMITE, RED, ruta=ruta, ahora=AHORA, pausa=sin_pausa, session=dash, url="https://dash.test/api/radar-seguimiento", log=callar)
igual(codigo, 0, "todo bien")
igual((r["seguidas"], r["fotos"], r["ya"], r["sinPagina"], r["errores"], r["limitado"], r["enviadas"]), (3, 3, 0, 0, 0, False, 3), "tres ofertas seguidas, tres fotos, tres enviadas")
igual(len(dash.calls), 1, "un solo envio (menos de 25 fotos)")
env = dash.calls[0]
igual(env["headers"]["x-clave-radar"], CLAVE, "la llave viaja en la cabecera")
ids_enviados = {f["id"] for f in env["json"]["fotos"]}
ok({"a1111111111", "a2222222222"} <= ids_enviados and len(ids_enviados) == 3, "van las tres ofertas seguidas (la de enlace de pagina lleva id h...)")
ok(all(f["a"] > 0 and "d" in f and "t" in f for f in env["json"]["fotos"]), "cada foto trae total, dia y hora")
igual(env["json"]["corrida"]["seguidas"], 3, "el aviso de la corrida viaja con las fotos")
llamadas_pag = [c for c in meta.llamadas if c[0] != "anuncio"]
ok(("1110001", None) in llamadas_pag and ("2220002", None) in llamadas_pag and ("5555555555", None) in llamadas_pag, "se pidio cada pagina")
igual([c for c in meta.llamadas if c[0] == "anuncio"], [("anuncio", "2222222222")], "solo la oferta sin pagina conocida gasto una peticion extra para hallarla (la de Lia sale de state.json, la manual del enlace)")
datos = json.loads(ruta.read_text(encoding="utf-8"))
igual(datos["ofertas"]["a1111111111"]["pid"], "1110001", "la pagina resuelta queda guardada")
igual(datos["ofertas"]["a1111111111"]["pend"], [], "lo enviado ya no esta pendiente")
igual(datos["ofertas"]["a2222222222"]["pid"], "2220002", "la pagina hallada por el anuncio queda guardada para no volver a buscarla")

# la segunda pasada del mismo dia no repite nada
meta2 = MetaFalsa(PAGINAS, PAG_ANUNCIO)
dash2 = FakeDash([Resp(200, {"ok": True, "guardadas": 0, "rechazadas": 0})])
codigo, r = sg.correr(meta2, filas, nombres, CLAVE, LIMITE, RED, ruta=ruta, ahora=AHORA + timedelta(hours=7), pausa=sin_pausa, session=dash2, log=callar)
igual((codigo, r["fotos"], r["ya"], len(meta2.llamadas)), (0, 0, 3, 0), "una oferta con foto de hoy no se vuelve a fotografiar ni gasta peticiones")
igual(dash2.calls[0]["json"]["fotos"], [], "sin fotos nuevas solo avisa de la corrida")

# al dia siguiente: apagados y nuevos contra la foto anterior; la foto de ayer se conserva
PAGINAS["1110001"] = [a for a in PAGINAS["1110001"] if a["archive_id"] not in ("1", "2")] + [anuncio(50, 0)]
for lst in PAGINAS.values():
    for a in lst:
        a["end"] = TS_HOY + DIA
AHORA2 = AHORA + timedelta(days=1)
meta3 = MetaFalsa(PAGINAS, PAG_ANUNCIO)
dash3 = FakeDash([Resp(200, {"ok": True, "guardadas": 3, "rechazadas": 0})])
codigo, r = sg.correr(meta3, filas, nombres, CLAVE, LIMITE, RED, ruta=ruta, ahora=AHORA2, pausa=sin_pausa, session=dash3, log=callar)
f_lia = next(f for f in dash3.calls[0]["json"]["fotos"] if f["id"] == "a1111111111")
igual((f_lia["d"], f_lia["k"], f_lia["nv"], f_lia["pd"]), ("2026-10-22", 2, 1, "2026-10-21"), "un dia despues: 2 apagados, 1 nuevo, contra la foto de ayer")
datos = json.loads(ruta.read_text(encoding="utf-8"))
igual([f["d"] for f in datos["ofertas"]["a1111111111"]["fotos"]], ["2026-10-21", "2026-10-22"], "el respaldo guarda una foto por dia")

# el dash no contesta: las fotos esperan y se reenvian
ruta2 = tmp / "otro.json"
dash_caido = FakeDash([requests.ConnectionError("caido")])
codigo, r = sg.correr(MetaFalsa(PAGINAS, PAG_ANUNCIO), filas, nombres, CLAVE, LIMITE, RED, ruta=ruta2, ahora=AHORA, pausa=sin_pausa, session=dash_caido, log=callar)
igual(codigo, 1, "si el dash no recibe las fotos el codigo es 1")
ok("el dash no respondio" in r["error"], "el error dice que el dash no respondio")
datos = json.loads(ruta2.read_text(encoding="utf-8"))
ok(all(len(v["pend"]) == 1 for v in datos["ofertas"].values()), "las fotos quedan pendientes")
dash_vuelve = FakeDash([Resp(200, {"ok": True, "guardadas": 3, "rechazadas": 0})])
codigo, r = sg.correr(MetaFalsa(PAGINAS, PAG_ANUNCIO), filas, nombres, CLAVE, LIMITE, RED, ruta=ruta2, ahora=AHORA + timedelta(hours=7), pausa=sin_pausa, session=dash_vuelve, log=callar)
igual((codigo, r["fotos"], len(dash_vuelve.calls[0]["json"]["fotos"])), (0, 0, 3), "a la siguiente pasada se reenvian las 3 sin volver a fotografiar")
igual(all(v["pend"] == [] for v in json.loads(ruta2.read_text(encoding="utf-8"))["ofertas"].values()), True, "y ya no quedan pendientes")

# sin llave: se guarda y no se envia
ruta3 = tmp / "sinllave.json"
dash_no = FakeDash([Resp(200, {"ok": True})])
codigo, r = sg.correr(MetaFalsa(PAGINAS, PAG_ANUNCIO), filas, nombres, "", LIMITE, RED, ruta=ruta3, ahora=AHORA, pausa=sin_pausa, session=dash_no, log=callar)
igual((codigo, len(dash_no.calls), ruta3.exists()), (0, 0, True), "sin dash.key no se envia nada pero las fotos se guardan")

# el dash rechaza algunas por formato: no se reintentan para siempre
ruta4 = tmp / "rechazo.json"
dash_rech = FakeDash([Resp(200, {"ok": True, "guardadas": 1, "rechazadas": 2})])
codigo, r = sg.correr(MetaFalsa(PAGINAS, PAG_ANUNCIO), filas, nombres, CLAVE, LIMITE, RED, ruta=ruta4, ahora=AHORA, pausa=sin_pausa, session=dash_rech, log=callar)
igual(all(v["pend"] == [] for v in json.loads(ruta4.read_text(encoding="utf-8"))["ofertas"].values()), True, "lo rechazado por formato no queda pendiente")

# el dash rechaza la llave
for status, trozo in ((401, "rechazo la llave"), (503, "CLAVE_RADAR no esta puesta"), (404, "todavia no tiene"), (500, "contesto 500")):
    try:
        sg.enviar(CLAVE, [], {"t": "x"}, FakeDash([Resp(status, {"ok": False})]))
        ok(False, f"el estado {status} debe ser un error")
    except dash_estados.DashError as e:
        ok(trozo in str(e), f"mensaje del estado {status}: {e}")
# lotes de 25
d = FakeDash([Resp(200, {"ok": True, "guardadas": 25, "rechazadas": 0}), Resp(200, {"ok": True, "guardadas": 5, "rechazadas": 0})])
g, rj = sg.enviar(CLAVE, [{"id": "a1", "d": "x"}] * 30, {"t": "x"}, d)
igual((g, rj, [len(c["json"]["fotos"]) for c in d.calls], ["corrida" in c["json"] for c in d.calls]), (30, 0, [25, 5], [True, False]), "30 fotos van en dos envios y el aviso solo en el primero")

# Meta limita la IP a la mitad: se corta, se envia lo tomado y el codigo es 3
ruta5 = tmp / "limite.json"
meta_lim = MetaFalsa(PAGINAS, PAG_ANUNCIO, falla=lambda n: LIMITE("HTTP 429") if n > 5 else None)
dash_l = FakeDash([Resp(200, {"ok": True, "guardadas": 1, "rechazadas": 0})])
codigo, r = sg.correr(meta_lim, filas, nombres, CLAVE, LIMITE, RED, ruta=ruta5, ahora=AHORA, pausa=sin_pausa, session=dash_l, log=callar)
igual((codigo, r["limitado"], r["fotos"], len(dash_l.calls[0]["json"]["fotos"])), (3, True, 1, 1), "Meta limita en la segunda oferta: queda 1 foto, se envia y el codigo es 3")
# la siguiente pasada completa las que faltan sin repetir la primera
meta_ok = MetaFalsa(PAGINAS, PAG_ANUNCIO)
dash_l2 = FakeDash([Resp(200, {"ok": True, "guardadas": 2, "rechazadas": 0})])
codigo, r = sg.correr(meta_ok, filas, nombres, CLAVE, LIMITE, RED, ruta=ruta5, ahora=AHORA + timedelta(hours=7), pausa=sin_pausa, session=dash_l2, log=callar)
igual((codigo, r["fotos"], r["ya"]), (0, 2, 1), "la pasada siguiente fotografia las 2 que faltaban y salta la que ya estaba")

# fallos de red: una oferta se salta, tres seguidos cortan
ruta6 = tmp / "red.json"
meta_red = MetaFalsa(PAGINAS, PAG_ANUNCIO, falla=lambda n: requests.ReadTimeout("lento") if n <= 2 else None)
dash_r = FakeDash([Resp(200, {"ok": True, "guardadas": 1, "rechazadas": 0})])
codigo, r = sg.correr(meta_red, filas, nombres, CLAVE, LIMITE, RED, ruta=ruta6, ahora=AHORA, pausa=sin_pausa, session=dash_r, log=callar)
igual((codigo, r["errores"], r["fotos"], r["limitado"]), (1, 2, 1, False), "dos fallos de red seguidos saltan esas ofertas y sigue con la tercera")
meta_red3 = MetaFalsa(PAGINAS, PAG_ANUNCIO, falla=lambda n: requests.ConnectionError("x"))
codigo, r = sg.correr(meta_red3, filas + [fila(9, "Cuarta", "vigilar", "https://www.facebook.com/ads/library/?view_all_page_id=5555555555")], nombres, CLAVE, LIMITE, RED, ruta=tmp / "red3.json", ahora=AHORA,
                      pausa=sin_pausa, session=FakeDash([Resp(200, {"ok": True})]), log=callar)
igual((r["errores"], "3 veces seguidas" in r["error"]), (3, True), "tres fallos de red seguidos cortan la corrida")

# una oferta sin forma de saber su pagina
ruta7 = tmp / "sinpag.json"
codigo, r = sg.correr(MetaFalsa({}), [fila(2, "Desconocida", "vigilar", "no visible")], {}, CLAVE, LIMITE, RED, ruta=ruta7, ahora=AHORA, pausa=sin_pausa, session=FakeDash([Resp(200, {"ok": True})]), log=callar)
igual((r["sinPagina"], r["fotos"], codigo), (1, 0, 0), "sin enlace ni anuncio no se puede seguir: se cuenta y no es un error")
igual(r["sinPaginaIds"], [sg.id_oferta(fila(2, "Desconocida", "vigilar", "no visible"))], "y el aviso dice cual oferta no se pudo ubicar")

# una fila manual cuyo nombre es el de la MARCA (Arco Reabilitacion) y cuyo anuncio es de la pagina de un doctor que la vende: la resolucion es exacta (la pagina pegada al anuncio), asi que SE sigue,
# y la foto dice de que pagina son los numeros para que el dash lo muestre
PAG_MARCA = {"9990009": [anuncio(700 + i, 40 - i, link="https://arcoalianca.com.br/oferta") for i in range(1, 10)]}
meta_aj = MetaFalsa(PAG_MARCA, pag_de_anuncio={"7777777777": "9990009"}, nombres={"9990009": "Dr. Mauro Choi - Ortopedista"})
filas_aj = [fila(2, "Arco Reabilitação", "vigilar", "https://www.facebook.com/ads/library/?id=7777777777")]
ruta9 = tmp / "marca.json"
dash_aj = FakeDash([Resp(200, {"ok": True, "guardadas": 1, "rechazadas": 0})])
codigo, r = sg.correr(meta_aj, filas_aj, {}, CLAVE, LIMITE, RED, ruta=ruta9, ahora=AHORA, pausa=sin_pausa, session=dash_aj, log=callar)
igual((r["fotos"], r["sinPagina"]), (1, 0), "una pagina con otro nombre que el de la planilla se sigue si se resolvio por el anuncio")
fm = dash_aj.calls[0]["json"]["fotos"][0]
igual((fm["pn"], fm["pg"], fm["hs"]), ("Dr. Mauro Choi - Ortopedista", "9990009", "arcoalianca.com.br"), "y la foto dice la pagina, su id y su destino: asi el dash muestra de quien son los numeros")

# SIMULACION: pide a Meta pero no guarda ni envia
ruta8 = tmp / "simula.json"
dash_s = FakeDash([Resp(200, {"ok": True})])
codigo, r = sg.correr(MetaFalsa(PAGINAS, PAG_ANUNCIO), filas, nombres, CLAVE, LIMITE, RED, ruta=ruta8, simular=True, ahora=AHORA, pausa=sin_pausa, session=dash_s, log=callar)
igual((r["fotos"], ruta8.exists(), len(dash_s.calls)), (3, False, 0), "en simulacion no se escribe el respaldo ni se envia")

# ---------------------------------------------------------------- las ofertas que el equipo agrego a mano (viven en el dash, no en la planilla)
AD_M1, AD_M2 = "5555500001", "5555500002"
def manual(id_, estado, *enlaces, nombre="Moldes de fieltro"):
    return {"id": id_, "anunciante": nombre, "estado": estado, "enlaces": [{"url": u, "tipo": t} for u, t in enlaces]}
ENL_AD = lambda n: ("https://www.facebook.com/ads/library/?id=" + n, "anuncio")
ENL_PAG = ("https://www.facebook.com/ads/library/?active_status=active&search_type=page&view_all_page_id=7770007", "anunciante")
ENL_WEB = ("https://tienda.com/p?id=1234567", "pagina")
ENL_TT = ("https://www.tiktok.com/@x/video/1", "red")

# traer_manuales: lo bien formado, y cada fallo con su motivo
d_ok = FakeDash([], manuales=[manual("mabcd1234", "vigilar", ENL_AD(AD_M1), ENL_WEB), manual("mzzzzzzzz", "nuevo", ENL_WEB)])
tm = sg.traer_manuales(CLAVE, d_ok, "https://dash.test/api/radar-manual")
igual([(m["id"], m["estado"], [e["tipo"] for e in m["enlaces"]]) for m in tm], [("mabcd1234", "vigilar", ["anuncio", "pagina"]), ("mzzzzzzzz", "nuevo", ["pagina"])], "traer_manuales devuelve id, estado y el tipo de cada enlace")
igual((d_ok.gets[0]["url"], d_ok.gets[0]["headers"]["x-clave-radar"]), ("https://dash.test/api/radar-manual", CLAVE), "es un GET con la llave del radar en la cabecera")
sucio = FakeDash([], manuales=[manual("mabcd1234", "vigilar", ENL_WEB), {"id": "a123", "estado": "vigilar", "enlaces": []}, {"id": "mabcd1235", "estado": "rara", "enlaces": []}, "texto", None,
                               {"id": "mabcd1236", "anunciante": "X", "estado": "vigilar", "enlaces": [{"url": "x" * 500, "tipo": "pagina"}, {"url": "https://a.com", "tipo": "otro"}, "no", {"url": 5, "tipo": "pagina"}]}])
tm = sg.traer_manuales(CLAVE, sucio)
igual([m["id"] for m in tm], ["mabcd1234", "mabcd1236"], "se descartan los ids que no son de una oferta a mano, los estados desconocidos y lo que no es un objeto")
igual(tm[1]["enlaces"], [], "y los enlaces que no son bien formados (tipo raro, muy largos, no son objeto)")
for resp, trozo in ((Resp(401, {"ok": False}), "rechazo la llave"), (Resp(503, {"ok": False}), "CLAVE_RADAR no esta puesta"), (Resp(404, {}), "todavia no tiene la lectura"), (Resp(500, {}), "contesto 500"),
                    (Resp(200, None), "no contesto JSON"), (Resp(200, {"ok": False}), "inesperada"), (Resp(200, {"ok": True}), "inesperada"), (requests.ConnectionError("x"), "no respondio")):
    try:
        sg.traer_manuales(CLAVE, FakeDash([], get_resp=resp))
        ok(False, f"debia fallar: {trozo}")
    except dash_estados.DashError as e:
        ok(trozo in str(e), f"traer_manuales: {trozo} -> {e}")

# elegir_manuales: solo las vigilar/testiando, con sus anuncios y su pagina
em = sg.elegir_manuales([manual("maaaaaaaa", "vigilar", ENL_AD(AD_M1), ENL_AD(AD_M2), ENL_WEB), manual("mbbbbbbbb", "testiando", ENL_PAG, ENL_TT), manual("mcccccccc", "nuevo", ENL_AD(AD_M1)),
                         manual("mdddddddd", "descartada", ENL_PAG), manual("meeeeeeee", "vigilar", ENL_WEB, ENL_TT)])
igual([m["id"] for m in em], ["maaaaaaaa", "mbbbbbbbb", "meeeeeeee"], "solo se siguen las que estan en Vigilar o Testiando en el dash")
igual((em[0]["ad_ids"], em[0]["ad_id"], em[0]["page_id"], em[0]["manual"]), ([AD_M1, AD_M2], AD_M1, "", True), "con enlaces de anuncios guarda todos (se prueban en orden) y marca que es a mano")
igual((em[1]["page_id"], em[1]["ad_ids"]), ("7770007", []), "con el enlace de la pagina del anunciante ya sabe su pagina")
igual((em[2]["ad_ids"], em[2]["page_id"]), ([], ""), "solo con una pagina de ventas y un video no hay como ubicarla")

# correr: se suman a las de la planilla y se ubican igual
MANUALES_PAGINAS = {"6660006": [anuncio(800 + i, 30 - i) for i in range(1, 10)], "7770007": [anuncio(900 + i, 20 - i) for i in range(1, 6)], "1110001": PAGINAS["1110001"]}
NOMBRES_M = {"6660006": "Moleque Arteiro", "7770007": "Otra Pagina", "1110001": "Lia Crochê"}
manuales_dash = [manual("maaaaaaaa", "vigilar", ENL_AD(AD_M1), ENL_WEB), manual("mbbbbbbbb", "testiando", ENL_PAG), manual("mcccccccc", "nuevo", ENL_AD(AD_M1)), manual("meeeeeeee", "vigilar", ENL_WEB, ENL_TT),
                 manual("mfffffffff", "vigilar", ENL_AD("5555500009"), ENL_AD(AD_M2), nombre="Lia Crochê")]
manuales_dash[4]["id"] = "mffffffff"
meta_m = MetaFalsa(MANUALES_PAGINAS, pag_de_anuncio={AD_M1: "6660006", AD_M2: "6660006"}, nombres=NOMBRES_M)
dash_m = FakeDash([Resp(200, {"ok": True, "guardadas": 3, "rechazadas": 0})], manuales=manuales_dash)
filas_m = [fila(2, "Lia Crochê", "vigilar", "https://www.facebook.com/ads/library/?id=1111111111")]
ruta_m = tmp / "manuales.json"
codigo, r = sg.correr(meta_m, filas_m, nombres, CLAVE, LIMITE, RED, ruta=ruta_m, ahora=AHORA, pausa=sin_pausa, session=dash_m, log=callar)
igual((r["seguidas"], r["fotos"], r["sinPagina"], r["sinPaginaIds"]), (5, 4, 1, ["meeeeeeee"]), "5 seguidas (1 de la planilla y 4 a mano en Vigilar o Testiando): 4 fotos y la que solo trae pagina de ventas y un video queda como sin ubicar")
enviados = {f["id"]: f for f in dash_m.calls[0]["json"]["fotos"]}
ok({"maaaaaaaa", "mbbbbbbbb", "mffffffff"} <= set(enviados) and "mcccccccc" not in enviados and "meeeeeeee" not in enviados, "se mandan las fotos de las a mano que se siguen, con su id «m…»; la que esta en Nueva no se sigue")
igual((enviados["maaaaaaaa"]["pg"], enviados["mbbbbbbbb"]["pg"], enviados["mffffffff"]["pg"]), ("6660006", "7770007", "6660006"), "cada una se ubica: por el anuncio, por la pagina del anunciante y, si el primer anuncio ya no existe, por el segundo")
igual([c for c in meta_m.llamadas if c[0] == "anuncio"], [("anuncio", AD_M1), ("anuncio", "5555500009"), ("anuncio", AD_M2)], "se piden las paginas de los anuncios en orden y se para al primero que sirve (la de pagina ya sabia la suya)")
datos_m = json.loads(ruta_m.read_text(encoding="utf-8"))
ok("meeeeeeee" not in datos_m["ofertas"] and "mffffffff" in datos_m["ofertas"], "en el respaldo solo quedan las que tuvieron foto")
igual(datos_m["ofertas"]["mffffffff"]["pid"], "6660006", "y la pagina hallada queda guardada: la siguiente pasada no vuelve a pedirla")
# el nombre de una oferta a mano NO se usa para ubicarla (lo puso el equipo): aunque coincida con una pagina que el radar ya vio
meta_n = MetaFalsa({"1110001": PAGINAS["1110001"]}, nombres=NOMBRES_M)
codigo, r = sg.correr(meta_n, [], nombres, CLAVE, LIMITE, RED, ruta=tmp / "nombre.json", ahora=AHORA, pausa=sin_pausa, session=FakeDash([Resp(200, {"ok": True, "guardadas": 0, "rechazadas": 0})], manuales=[manual("mnnnnnnnn", "vigilar", ENL_WEB, nombre="Lia Crochê")]), log=callar)
igual((r["fotos"], r["sinPagina"]), (0, 1), "una oferta a mano llamada «Lia Crochê» no se ubica por el nombre de esa pagina: sin enlace de Meta queda sin ubicar")

# si el dash no da las ofertas a mano, se sigue con las de la planilla y se dice
for get_resp in (Resp(404, {}), requests.ConnectionError("caido")):
    dash_f = FakeDash([Resp(200, {"ok": True, "guardadas": 1, "rechazadas": 0})], get_resp=get_resp)
    codigo, r = sg.correr(MetaFalsa(PAGINAS, PAG_ANUNCIO), filas_m, nombres, CLAVE, LIMITE, RED, ruta=tmp / "sinman.json", ahora=AHORA, pausa=sin_pausa, session=dash_f, log=callar)
    ok(r["fotos"] == 1 and "ofertas a mano" in r["error"] and codigo == 1, "si el dash no da las ofertas a mano se siguen las de la planilla, el aviso lo dice y el codigo es 1")
    (tmp / "sinman.json").unlink()
dash_sl = FakeDash([Resp(200, {"ok": True})], manuales=manuales_dash)
sg.correr(MetaFalsa(PAGINAS, PAG_ANUNCIO), filas_m, nombres, "", LIMITE, RED, ruta=tmp / "sinllave2.json", ahora=AHORA, pausa=sin_pausa, session=dash_sl, log=callar)
igual(dash_sl.gets, [], "sin dash.key no se pide nada al dash (ni las ofertas a mano)")

# ---------------------------------------------------------------- lo que el radar le agrega: Meta.pagina_anunciante y pagina_de_anuncio (HTML simulado)
def html_biblioteca(count, ads, hay_mas=False):
    conn = {"count": count, "edges": [{"node": {"collated_results": [a]}} for a in ads], "page_info": {"end_cursor": "c" if hay_mas else "", "has_next_page": hay_mas}}
    return '<html><script>x={"search_results_connection":' + json.dumps(conn) + ',"otro":1}</script></html>'


def crudo(i, ini, fin=None, col=1):
    return {"ad_archive_id": str(i), "collation_id": f"c{i}", "collation_count": col, "page_id": "9990001", "page_name": "Pag", "start_date": ini, "end_date": fin or ini + 100, "is_active": True,
            "snapshot": {"body": {"text": "hola"}, "link_url": "https://wa.me/1", "cta_type": "WHATSAPP_MESSAGE", "page_like_count": 77}}


class RespHtml:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


mt = radar.Meta()
pedidos = []
mt._open_library = lambda params: (pedidos.append(dict(params)), RespHtml(html_biblioteca(204, [crudo(1, TS_HOY - 5 * DIA, TS_HOY), crudo(2, TS_HOY - 9 * DIA, TS_HOY, col=2)], hay_mas=True)))[1]
res = mt.pagina_anunciante("777", antes_de="2026-09-21")
igual((res["total"], len(res["ads"]), res["hay_mas"], res["ads"][0]["end"], res["ads"][1]["collation_count"]), (204, 2, True, TS_HOY, 2), "pagina_anunciante lee el count, los anuncios, si hay mas y el end_date")
igual((pedidos[0]["view_all_page_id"], pedidos[0]["search_type"], pedidos[0]["start_date[max]"], pedidos[0]["active_status"]), ("777", "page", "2026-09-21", "active"), "pide la pagina del anunciante, solo activos y con el filtro de fecha")
mt.pagina_anunciante("777")
ok("start_date[max]" not in pedidos[1], "sin antes_de no manda el filtro de fecha")
mt._open_library = lambda params: RespHtml("<html>sin resultados</html>")
igual(mt.pagina_anunciante("777"), None, "un HTML sin resultados devuelve None")
mt._open_library = lambda params: RespHtml("", 429)
try:
    mt.pagina_anunciante("777")
    ok(False, "429 debe lanzar RateLimited")
except radar.RateLimited:
    ok(True, "")
# Un anuncio propio con su pagina, rodeado de anuncios de OTRAS paginas que se repiten mas (asi era Moleque Arteiro: el id mas repetido era el de Alibaba.com)
decoy = "".join('{"ad_archive_id":"%d","collation_id":null,"page_id":"22437985072","snapshot":{"page_id":"22437985072"}},' % (900 + i) for i in range(12))
propio = '"deeplink_ad_archive":{"ad_archive_id":"9999000000821469","collation_id":null,"collation_count":null,"page_id":"999900000271790","snapshot":{"branded_content":null,"page_id":"999900000271790"}}'
mt._open_library = lambda params: RespHtml("<html>" + decoy + propio + "</html>")
igual(mt.pagina_de_anuncio(9999000000821469), "999900000271790", "la pagina de un anuncio es la que esta pegada a ESE anuncio, aunque el HTML repita mas el id de otra pagina")
igual(mt.pagina_de_anuncio(9999000000318321), "", "un anuncio que no aparece en la pagina (ya apagado) devuelve vacio: NUNCA se cae al id mas repetido")
mt._open_library = lambda params: RespHtml("nada")
igual(mt.pagina_de_anuncio(1), "", "sin id de pagina devuelve vacio")
igual(radar.normalize({"ad_archive_id": "1", "end_date": 55, "start_date": 5})["end"], 55, "normalize conserva el end_date")

# ---------------------------------------------------------------- radar.seguir_ofertas (planilla y Meta simuladas, SIMULACION: no escribe ni envia)
class PlanillaFalsa:
    def read_dicts(self, tab):
        assert tab == "OFERTAS"
        return filas


orig = (sheets.available, sheets.Sheet, radar.Meta, radar.load_state, dash_estados.leer_clave, radar.load_env)
try:
    sheets.available = lambda: True
    sheets.Sheet = PlanillaFalsa
    radar.Meta = lambda proxy=None, use_cache=False: MetaFalsa(PAGINAS, pag_de_anuncio={"2222222222": "2220002"})
    radar.load_state = lambda: {"seen_pages": {"1110001": {"name": "Lia Crochê"}}}
    dash_estados.leer_clave = lambda: ""
    radar.load_env = lambda: None
    radar.polite = lambda *a, **k: None
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        code = radar.seguir_ofertas(simular=True)
    igual(code, 0, "seguir_ofertas en simulacion sale con 0")
    ok("Seguimiento: 3 ofertas vigiladas | fotos hoy: 3" in salida.getvalue() and "SIMULACION" in salida.getvalue(), "el resumen cuenta las tres ofertas y dice SIMULACION")
    sheets.available = lambda: False
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        code = radar.seguir_ofertas(simular=True)
    igual(code, 1, "sin acceso a la planilla sale con 1")
    sheets.available = lambda: True

    class PlanillaRota:
        def read_dicts(self, tab):
            raise sheets.SheetsError("sin permiso")
    sheets.Sheet = PlanillaRota
    with contextlib.redirect_stdout(io.StringIO()):
        igual(radar.seguir_ofertas(simular=True), 1, "una planilla que no se puede leer sale con 1, sin lanzar")
finally:
    sheets.available, sheets.Sheet, radar.Meta, radar.load_state, dash_estados.leer_clave, radar.load_env = orig

print(f"OK: {bien} comprobaciones de seguimiento.py")
