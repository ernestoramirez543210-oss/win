"""Pruebas SIN red de flujo.py y de lo que el radar le agrega (normalize, write_offer, Sheet.asegurar_columnas, preparar_flujo, rellenar_flujos).
Meta y la planilla estan simulados. Corre con:  python -X utf8 test_flujo.py"""
import base64
import contextlib
import io
import json
import tempfile
import types
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import flujo
import radar
import sheets

bien = 0


def ok(cond, msg):
    global bien
    assert cond, msg
    bien += 1


def igual(a, b, msg):
    ok(a == b, f"{msg}: obtuve {a!r} y esperaba {b!r}")


# ---------------------------------------------------------------- el ID del video, de la direccion del video
# Un `efg` REAL de la Biblioteca (debug/cache, anuncio 9999000000321374 de la pagina 9999000000335632): JSON en base64 con xpv_asset_id.
# (plantilla) armado con la misma forma que el real, a partir del numero
EFG_REAL = base64.urlsafe_b64encode(json.dumps({"vencode_tag": "xpv_progressive.VI_USECASE_PRODUCT_TYPE..C3.720.progressive_h264-basic-gen2_720p", "xpv_asset_id": 9999000000346933, "asset_age_days": 30, "vi_usecase_id": 12374, "duration_s": 28, "urlgen_source": "www"}, separators=(",", ":")).encode()).decode().rstrip("=")


def efg(valor, **extra):
    d = {"vencode_tag": "xpv_progressive.VI_USECASE_PRODUCT_TYPE..C3.360.sve_sd", "xpv_asset_id": valor, "asset_age_days": 30, **extra}
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def url_video(valor, relleno=""):
    return f"https://video-bog2-1.xx.fbcdn.net/o1/v/t2/f2/m366/AQPQhRfqnRXwuDLBnytbLyjq7CcPT1Ih5RH8Q13uVhakJM_ypGt.mp4?_nc_cat=1&ccb=9-4{relleno}&efg={efg(valor)}&_nc_sid=5e9851"


igual(flujo.video_id_de_url(f"https://video-bog2-1.xx.fbcdn.net/o1/v/t2/f2/m366/AQP.mp4?_nc_cat=1&efg={EFG_REAL}&ccb=9-4"), "9999000000346933", "un efg real de la Biblioteca da el id del video")
igual(flujo.video_id_de_url(url_video(9999000000352404)), "9999000000352404", "el id (numero en el JSON) sale como texto")
igual(flujo.video_id_de_url(url_video("99990000000363636")), "99990000000363636", "un id de 17 digitos no pierde digitos (viene como texto)")
con_relleno = base64.urlsafe_b64encode(json.dumps({"xpv_asset_id": 4242424242}).encode()).decode()
ok(con_relleno.endswith("="), "(el caso de prueba lleva relleno =)")
igual(flujo.video_id_de_url("https://v.example/x.mp4?efg=" + urllib.parse.quote(con_relleno)), "4242424242", "un efg con relleno (=) en la direccion tambien se lee")
for malo, por_que in [("https://v.example/x.mp4?a=1", "sin efg"), ("", "vacia"), (None, "None"), ("https://v.example/x.mp4?efg=", "efg vacio"), ("https://v.example/x.mp4?efg=!!!", "efg que no es base64"),
                      ("https://v.example/x.mp4?efg=" + base64.urlsafe_b64encode(b"esto no es json").decode(), "base64 de algo que no es JSON"),
                      ("https://v.example/x.mp4?efg=" + base64.urlsafe_b64encode(b"[1,2]").decode(), "JSON que es una lista"),
                      ("https://v.example/x.mp4?efg=" + base64.urlsafe_b64encode(b'{"xpv_asset_id": null}').decode(), "id nulo"),
                      (url_video("abc"), "id con letras"), (url_video(123), "id de 3 digitos"), (url_video("1" * 26), "id de 26 digitos"), (url_video("12345; DROP TABLE"), "id con basura"),
                      ("http://[::1", "direccion mal formada"), (12345, "ni siquiera es texto")]:
    igual(flujo.video_id_de_url(malo), "", f"video_id_de_url: {por_que} -> vacio")

# del snapshot de un anuncio
igual(flujo.video_id_de_snapshot({"videos": [{"video_hd_url": url_video(111111), "video_sd_url": url_video(222222)}]}), "111111", "el video HD manda")
igual(flujo.video_id_de_snapshot({"videos": [{"video_hd_url": "https://v.example/sin-efg.mp4", "video_sd_url": url_video(222222)}]}), "222222", "si el HD no trae el id se prueba el SD")
igual(flujo.video_id_de_snapshot({"videos": [], "cards": [{"video_hd_url": None}, {"video_sd_url": url_video(333333)}]}), "333333", "un carrusel: el video de la primera tarjeta que lo tenga")
igual(flujo.video_id_de_snapshot({"videos": [{"video_hd_url": url_video(111111)}], "cards": [{"video_hd_url": url_video(333333)}]}), "111111", "el video suelto gana sobre el de las tarjetas")
igual(flujo.video_id_de_snapshot({"images": [{"original_image_url": "https://i.example/a.jpg"}], "videos": []}), "", "un anuncio de imagen no tiene id de video")
igual([flujo.video_id_de_snapshot(x) for x in (None, [], "texto", 5, {}, {"videos": None, "cards": None}, {"videos": "no es lista"}, {"videos": [None, 5, "x", [1]]}, {"videos": {"a": 1}})], [""] * 9,
      "un snapshot raro (None, lista, texto, numero, tipos cambiados) da vacio y nunca lanza")

# lo normaliza radar.normalize
ad = {"ad_archive_id": "10", "page_id": "999000111", "page_name": "Zap", "start_date": 1700000000, "is_active": True,
      "snapshot": {"body": {"text": "oi"}, "link_url": "https://api.whatsapp.com/send", "cta_type": "WHATSAPP_MESSAGE", "display_format": "VIDEO", "videos": [{"video_hd_url": url_video(9999000000346933)}]}}
n = radar.normalize(ad)
igual([n["video_id"], n["page_id"], n["archive_id"], n["video"]], ["9999000000346933", "999000111", "10", True], "radar.normalize trae el id del video junto con los de siempre")
igual(radar.normalize({"ad_archive_id": "11", "page_id": "1", "snapshot": {"body": {"text": "oi"}, "images": [{}]}})["video_id"], "", "un anuncio sin video: video_id vacio")
igual(radar.normalize({"ad_archive_id": "12", "page_id": "1", "snapshot": None})["video_id"], "", "un anuncio sin snapshot no rompe normalize")
igual(flujo.WHATSAPP_HOSTS, radar.WHATSAPP_HOSTS, "flujo.py y radar.py cuentan los mismos destinos como WhatsApp")


# ---------------------------------------------------------------- cual anuncio se abre
def anuncio(archive, page, video=None, cta="LEARN_MORE", start=1000, link="https://tienda.example/p", active=True):
    return {"archive_id": str(archive), "page_id": str(page), "video_id": video or "", "cta_type": cta, "start": start, "link": link, "active": active}


PAG = "9999000000335632"
ads = [anuncio(1, PAG, "7001", start=900), anuncio(2, PAG, "7002", cta="WHATSAPP_MESSAGE", start=500), anuncio(3, PAG, "7003", cta="WHATSAPP_MESSAGE", start=300), anuncio(4, PAG, None, cta="WHATSAPP_MESSAGE", start=100)]
igual(flujo.elegir_anuncio_flujo(ads, "2"), (PAG, "7002", "2"), "si el anuncio de la fila es de WhatsApp y tiene video, ese (aunque haya uno de WhatsApp mas viejo)")
igual(flujo.elegir_anuncio_flujo(ads, "1"), (PAG, "7003", "3"), "si el de la fila NO es de WhatsApp pero el anunciante tiene anuncios de WhatsApp con video: el de WhatsApp mas viejo (el boton es para llegar al WhatsApp)")
igual(flujo.elegir_anuncio_flujo(ads, "4"), (PAG, "7003", "3"), "el anuncio de la fila es de WhatsApp pero sin video: el de WhatsApp con video mas viejo")
igual(flujo.elegir_anuncio_flujo(ads), (PAG, "7003", "3"), "sin anuncio preferido: de WhatsApp y el mas viejo")
igual(flujo.elegir_anuncio_flujo(ads, "999"), (PAG, "7003", "3"), "un anuncio preferido que no esta en la lista no estorba")
igual(flujo.elegir_anuncio_flujo([anuncio(1, PAG, "7001", start=900), anuncio(5, PAG, "7005", start=100)]), (PAG, "7005", "5"), "sin ninguno de WhatsApp: el mas viejo con video")
igual(flujo.elegir_anuncio_flujo([anuncio(1, PAG, "7001", start=900), anuncio(5, PAG, "7005", start=100)], "1"), (PAG, "7001", "1"), "sin ninguno de WhatsApp: el de la fila si tiene video")
igual(flujo.elegir_anuncio_flujo([anuncio(6, PAG, "7006", link="https://api.whatsapp.com/send", start=800), anuncio(7, PAG, "7007", start=100)]), (PAG, "7006", "6"), "un destino wa.me/api.whatsapp.com cuenta como WhatsApp aunque el boton no lo diga")
igual(flujo.elegir_anuncio_flujo([anuncio(8, PAG, "7008", active=False)]), ("", "", ""), "un anuncio apagado no se elige")
igual(flujo.elegir_anuncio_flujo([anuncio(9, PAG, None)]), ("", "", ""), "sin ningun video: nada")
igual(flujo.elegir_anuncio_flujo([anuncio(9, "abc", "7009")]), ("", "", ""), "una pagina que no es un id no se elige")
igual([flujo.elegir_anuncio_flujo(x) for x in (None, [])], [("", "", "")] * 2, "sin anuncios: nada")
igual(flujo.elegir_anuncio_flujo([{"archive_id": "1", "page_id": PAG, "video_id": "7001", "start": None}]), (PAG, "7001", "1"), "un anuncio sin fecha de inicio tambien sirve")
igual(flujo.ids_de_grupo({"page_id": PAG, "ads": ads}, "2"), (PAG, "7002"), "ids_de_grupo: la pagina y el video del anuncio de la fila")
igual(flujo.ids_de_grupo({"page_id": PAG, "ads": [anuncio(9, PAG, None)]}), (PAG, ""), "sin video: la pagina se escribe igual y el video queda vacio")
igual([flujo.ids_de_grupo(x) for x in (None, {}, {"ads": None}, {"ads": [None, 5]}, {"ads": "no"})], [("", "")] * 5, "un grupo raro da vacios y nunca lanza (no puede frenar el registro de una oferta)")

# ---------------------------------------------------------------- write_offer: los dos ids van con la fila, solo si los hay
class HojaApend:
    def __init__(self):
        self.filas = []

    def append_dict(self, tab, row):
        self.filas.append((tab, dict(row)))


fila_base = {c: "x" for c in radar.SHEET_COLS}
h = HojaApend()
radar.write_offer(h, {**fila_base, flujo.COLUMNAS[0]: PAG, flujo.COLUMNAS[1]: "7001", "Señales winner": "no va"}, types.SimpleNamespace(simular_sheets=False))
igual(sorted(h.filas[0][1]), sorted(radar.SHEET_COLS + list(flujo.COLUMNAS)), "write_offer escribe las columnas de siempre MAS las dos de ID (y nada de las extra del CSV)")
igual([h.filas[0][1][flujo.COLUMNAS[0]], h.filas[0][1][flujo.COLUMNAS[1]]], [PAG, "7001"], "con los valores")
h = HojaApend()
radar.write_offer(h, {**fila_base, flujo.COLUMNAS[0]: PAG, flujo.COLUMNAS[1]: ""}, types.SimpleNamespace(simular_sheets=False))
igual(sorted(h.filas[0][1]), sorted(radar.SHEET_COLS + [flujo.COLUMNAS[0]]), "sin video solo va la pagina (una celda vacia no se manda)")
h = HojaApend()
radar.write_offer(h, dict(fila_base), types.SimpleNamespace(simular_sheets=False))
igual(sorted(h.filas[0][1]), sorted(radar.SHEET_COLS), "una fila sin los ids se escribe como siempre")
h = HojaApend()
with contextlib.redirect_stdout(io.StringIO()):
    radar.write_offer(h, {**fila_base, flujo.COLUMNAS[1]: "7001"}, types.SimpleNamespace(simular_sheets=True))
igual(h.filas, [], "en simulacion no escribe nada")
try:
    radar.write_offer(HojaApend(), {k: v for k, v in fila_base.items() if k != "Nota"}, types.SimpleNamespace(simular_sheets=False))
    ok(False, "a una fila sin una columna obligatoria hay que gritarle (KeyError), como antes")
except KeyError:
    ok(True, "")
igual(sheets.fold(flujo.COLUMNAS[0]), "id de la pagina", "el encabezado nuevo se lee sin tilde (el dash lo busca asi)")
igual(sheets.fold(flujo.COLUMNAS[1]), "id del video", "el otro encabezado tambien")


# ---------------------------------------------------------------- una planilla y un Meta simulados
CAB = ["Fecha", "Palabra clave", "Anunciante", "Link del anuncio", "Link de la página", "Status", "ID de la página", "ID del video"]


def fila(anunciante, ad=None, status="nuevo", pagina="", video="", link=None):
    return {"Fecha": "21/10/2026", "Palabra clave": "x", "Anunciante": anunciante, "Link del anuncio": link or (f"https://www.facebook.com/ads/library/?id={ad}" if ad else "no visible"),
            "Link de la página": "no visible", "Status": status, "ID de la página": pagina, "ID del video": video}


def a1(texto):
    letras = "".join(c for c in texto if c.isalpha())
    col = 0
    for c in letras:
        col = col * 26 + (ord(c.upper()) - 64)
    return col - 1, int("".join(c for c in texto if c.isdigit()))


class HojaFalsa:
    """La pestana OFERTAS en memoria. read_dicts devuelve copias con _row (fila 2 = la primera); update_cells escribe de verdad ('S5' = columna S, fila 5), asi lo que la corrida escribe se ve despues.
    `antes_de_releer(hoja)` se llama justo antes de la SEGUNDA lectura (la de antes de escribir): sirve para mover filas o escribir una celda como lo haria el dueno mientras tanto."""

    def __init__(self, cabecera, filas, antes_de_releer=None):
        self.cabecera, self.filas, self.escrituras, self.lecturas = list(cabecera), [dict(f) for f in filas], [], 0
        self.antes_de_releer, self.falla_al_escribir = antes_de_releer, False

    def headers(self, tab):
        return list(self.cabecera)

    def read_dicts(self, tab):
        self.lecturas += 1
        if self.lecturas == 2 and self.antes_de_releer:
            self.antes_de_releer(self)
        return [dict(f, _row=i + 2) for i, f in enumerate(self.filas)]

    def update_cells(self, tab, cells):
        if self.falla_al_escribir:
            raise sheets.SheetsError("Google Sheets 500: de prueba")
        self.escrituras.append(dict(cells))
        for celda, v in cells.items():
            col, nfila = a1(celda)
            self.filas[nfila - 2][self.cabecera[col]] = v


class MetaFalsa:
    def __init__(self, paginas=None, de_anuncio=None, falla_pagina=None):
        self.paginas, self.de_anuncio, self.falla_pagina, self.llamadas = paginas or {}, de_anuncio or {}, falla_pagina or {}, []

    def pagina_de_anuncio(self, ad_id):
        self.llamadas.append(("anuncio", ad_id))
        return self.de_anuncio.get(ad_id, "")

    def pagina_anunciante(self, pid, country="BR", antes_de=None):
        self.llamadas.append(("pagina", pid))
        if pid in self.falla_pagina:
            raise self.falla_pagina[pid]
        lista = self.paginas.get(pid)
        return None if lista is None else {"total": len(lista), "ads": lista, "hay_mas": False}


AHORA = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)      # 09:00 en Bogota
RED = (requests.RequestException,)


def correr(hoja, meta, tmp, maximo=12, ahora=AHORA, simular=False, nombres=None):
    log = []
    codigo, r = flujo.correr(meta, hoja, hoja.read_dicts("OFERTAS"), maximo, radar.RateLimited, RED, simular=simular, ahora=ahora, ruta=Path(tmp) / "flujo.json", log=log.append, nombres=nombres)
    return codigo, r, log


def celdas(hoja):
    return [(f["Anunciante"], f["ID de la página"], f["ID del video"]) for f in hoja.filas]


P1, P2, P3 = "999900000371111", "999900000382222", "999900000393333"
PAGINAS = {P1: [anuncio("100001", P1, "500001", cta="WHATSAPP_MESSAGE", start=100), anuncio("100002", P1, "500002", start=50)],
           P2: [anuncio("200001", P2, "600001", cta="WHATSAPP_MESSAGE", start=100)],
           P3: [anuncio("300001", P3, None, start=100)]}                   # una pagina cuyos anuncios no traen video

with tempfile.TemporaryDirectory() as tmp:
    # ---- camino feliz: tres filas, una pagina compartida y una que se halla por el enlace
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001"), fila("Ministerio", ad="200001"), fila("Lia de nuevo", ad="100002"),
                           fila("Por enlace", link="https://www.facebook.com/ads/library/?active_status=active&view_all_page_id=" + P2 + "&search_type=page")])
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2, "100002": P1})
    codigo, r, log = correr(hoja, meta, tmp)
    igual(codigo, 0, "camino feliz: codigo 0")
    igual(celdas(hoja), [("Lia", P1, "500001"), ("Ministerio", P2, "600001"), ("Lia de nuevo", P1, "500001"), ("Por enlace", P2, "600001")],
          "cada fila recibe su pagina y el video de un anuncio de WhatsApp (el de su fila si lo es; si no, el de WhatsApp mas viejo del anunciante)")
    igual([type(v).__name__ for f in hoja.filas for v in (f["ID de la página"], f["ID del video"])], ["str"] * 8, "los ids se escriben como TEXTO")
    igual(sorted(c for e in hoja.escrituras for c in e), sorted(["G2", "H2", "G3", "H3", "G4", "H4", "G5", "H5"]), "solo se escriben celdas de las dos columnas nuevas (G y H)")
    igual(len(hoja.escrituras), 1, "una sola escritura a la planilla para todas las filas")
    igual([m for m in meta.llamadas if m[0] == "pagina"], [("pagina", P2), ("pagina", P1)], "cada pagina se pide UNA vez aunque tenga varias filas (las filas de abajo, las mas nuevas, van primero)")
    igual([m for m in meta.llamadas if m[0] == "anuncio"], [("anuncio", "100002"), ("anuncio", "200001"), ("anuncio", "100001")], "la pagina de un anuncio se pide a la Biblioteca solo si la fila no la trae; la del enlace no gasta peticion")
    igual([r["porCompletar"], r["intentadas"], r["halladas"], r["escritas"], r["errores"], r["limitado"]], [4, 4, 4, 8, 0, False], "resumen del camino feliz")
    igual(flujo.cargar(Path(tmp) / "flujo.json")["filas"], {}, "no queda nada anotado como pendiente")
    # una segunda pasada no tiene nada que hacer y no pide nada
    meta2 = MetaFalsa(PAGINAS)
    codigo, r, log = correr(hoja, meta2, tmp)
    igual([codigo, meta2.llamadas, r["porCompletar"]], [0, [], 0], "con todo completo no se pide nada a Meta")

with tempfile.TemporaryDirectory() as tmp:
    # ---- lo que el dueno puso a mano NO se toca
    hoja = HojaFalsa(CAB, [fila("Con video malo", ad="100001", video="2.42E+15"), fila("Con pagina puesta", ad="200001", pagina="999999999"), fila("Completa", ad="100002", pagina="888888888", video="777777777"),
                           fila("Sin nada de donde partir", ad=None, link="no visible")])
    meta = MetaFalsa({**PAGINAS, "999999999": [anuncio("200001", "999999999", "600001", cta="WHATSAPP_MESSAGE", start=100)]}, de_anuncio={"100001": P1, "200001": P2})
    codigo, r, log = correr(hoja, meta, tmp)
    igual(celdas(hoja), [("Con video malo", P1, "2.42E+15"), ("Con pagina puesta", "999999999", "600001"), ("Completa", "888888888", "777777777"), ("Sin nada de donde partir", "", "")],
          "un video que alguien escribio (aunque este mal) no se pisa; una pagina escrita tampoco; una fila completa o sin nada de donde partir no se toca")
    igual([c for c in meta.llamadas if c[0] == "pagina"], [("pagina", "999999999")], "la fila con pagina puesta usa ESA pagina para buscar el video; la que solo le faltaba la pagina no pide la lista de anuncios")
    igual([c for c in meta.llamadas if c[0] == "anuncio"], [("anuncio", "100001")], "y la pagina de un anuncio se pide solo a la fila que no la tenia")

with tempfile.TemporaryDirectory() as tmp:
    # ---- una pagina que Meta no conoce, otra sin video, y Meta que a veces falla
    hoja = HojaFalsa(CAB, [fila("Sin video", ad="300001", status="vigilar"), fila("Meta sin resultados", ad="400001", status="nuevo"), fila("Anuncio sin pagina", ad="55555", status="nuevo")])
    meta = MetaFalsa({P3: PAGINAS[P3]}, de_anuncio={"300001": P3, "400001": "999900000404444"})       # 4001 -> una pagina de la que Meta no trae resultados; 5555 -> Meta no sabe de que pagina es
    codigo, r, log = correr(hoja, meta, tmp)
    igual(celdas(hoja), [("Sin video", P3, ""), ("Meta sin resultados", "", ""), ("Anuncio sin pagina", "", "")], "sin video: se escribe la pagina y el video queda vacio; lo que Meta no trae queda como estaba")
    igual([codigo, r["sinVideo"], r["sinPagina"], r["errores"], r["halladas"]], [1, 1, 1, 1, 0], "una pagina sin resultados cuenta como error (codigo 1); sin video y sin pagina se cuentan aparte")
    memo = flujo.cargar(Path(tmp) / "flujo.json")["filas"]
    igual(memo, {"300001": {"t": "2026-10-22", "motivo": "sin_video"}, "55555": {"t": "2026-10-22", "motivo": "sin_pagina"}}, "se anota lo que no se hallo (NO lo que fallo por Meta: eso se reintenta ya)")
    # a los 3 dias no vuelve a pedir lo anotado; la pagina sin resultados SI se reintenta
    meta3 = MetaFalsa({P3: PAGINAS[P3]}, de_anuncio={"300001": P3, "400001": "999900000404444"})
    codigo, r, log = correr(hoja, meta3, tmp, ahora=AHORA + timedelta(days=3))
    igual([c for c in meta3.llamadas if c[1] in ("300001", P3, "55555")], [], "3 dias despues no se vuelve a pedir lo que se anoto como «sin video» ni «sin pagina»")
    ok(("pagina", "999900000404444") in meta3.llamadas, "pero la pagina que fallo por Meta se reintenta")
    igual(r["recientes"], 2, "y se cuenta lo que se salto por haberse intentado hace poco")
    # a los 8 dias si
    meta4 = MetaFalsa({P3: PAGINAS[P3], P1: PAGINAS[P1]}, de_anuncio={"300001": P3, "400001": "999900000404444", "55555": P1})
    codigo, r, log = correr(hoja, meta4, tmp, ahora=AHORA + timedelta(days=8))
    ok(("pagina", P3) in meta4.llamadas and ("anuncio", "55555") in meta4.llamadas, "8 dias despues se reintenta todo")
    igual(celdas(hoja)[2], ("Anuncio sin pagina", P1, "500001"), "y ahora la fila que no tenia pagina recibe su pagina y su video")

with tempfile.TemporaryDirectory() as tmp:
    # ---- Meta limita la IP a mitad de camino: lo hecho se guarda, lo demas queda para manana
    hoja = HojaFalsa(CAB, [fila("Tercera", ad="300001", status="nuevo"), fila("Segunda", ad="100001", status="nuevo"), fila("Primera", ad="200001", status="vigilar")])     # se atienden de abajo hacia arriba: Primera, Segunda (limite) y Tercera
    meta = MetaFalsa(PAGINAS, de_anuncio={"200001": P2, "100001": P1, "300001": P3}, falla_pagina={P1: radar.RateLimited("HTTP 429 al abrir la Biblioteca")})
    codigo, r, log = correr(hoja, meta, tmp)
    igual([codigo, r["limitado"]], [3, True], "Meta limito la IP: codigo 3")
    igual(celdas(hoja), [("Tercera", "", ""), ("Segunda", "", ""), ("Primera", P2, "600001")], "lo que se alcanzo a hallar se escribe y se corta ahi")
    ok(not any(c[1] == "300001" for c in meta.llamadas), "no se sigue pidiendo despues del limite")
    igual(flujo.cargar(Path(tmp) / "flujo.json")["filas"], {}, "lo que quedo sin intentar no se anota como hallado ni como fallido")

with tempfile.TemporaryDirectory() as tmp:
    # ---- el anuncio ya se apago (la Biblioteca no ubica su pagina): respaldo por el nombre del anunciante que el radar ya vio, solo si es unico
    NOMBRES = flujo.indice_nombres({P1: {"name": "Lia Crochê"}, P2: {"name": "Ministério"}, P3: {"name": "Dos Paginas"}, "999900000404444": {"name": "dos paginas"}})
    largo = "Anunciante con un nombre larguisimo " * 3                 # mas de 60 letras: lo que se muestra se recorta, lo que se compara no
    NOMBRES.update(flujo.indice_nombres({"999900000417777": {"name": largo}}))
    hoja = HojaFalsa(CAB, [fila("Lia Crochê", ad="100009"), fila("Dos Páginas", ad="100008"), fila("Nadie con ese nombre", ad="100007"), fila(largo, ad="100006")])
    PAGINAS_2 = {**PAGINAS, "999900000417777": [anuncio("100006", "999900000417777", "800001", cta="WHATSAPP_MESSAGE", start=100)]}
    meta = MetaFalsa(PAGINAS_2)                                         # pagina_de_anuncio no sabe de ninguno de estos (anuncios apagados)
    codigo, r, log = correr(hoja, meta, tmp, nombres=NOMBRES)
    igual(celdas(hoja), [("Lia Crochê", P1, "500001"), ("Dos Páginas", "", ""), ("Nadie con ese nombre", "", ""), (largo, "999900000417777", "800001")],
          "con el anuncio apagado se halla la pagina por el nombre si es unico (aunque el nombre pase de 60 letras); con dos paginas del mismo nombre o sin ninguna no se adivina")
    igual([m for m in meta.llamadas if m[0] == "anuncio"], [("anuncio", "100006"), ("anuncio", "100007"), ("anuncio", "100008"), ("anuncio", "100009")], "primero se le pregunta a la Biblioteca por la pagina del anuncio (lo exacto) y despues se cae al nombre")
    igual([r["sinPagina"], r["halladas"]], [2, 2], "las dos sin pagina se cuentan y se anotan")
    igual(sorted(flujo.cargar(Path(tmp) / "flujo.json")["filas"]), ["100007", "100008"], "y se anotan para no insistir")
    # lo exacto gana sobre el nombre: si la Biblioteca sabe la pagina del anuncio, el nombre no se usa
    hoja = HojaFalsa(CAB, [fila("Lia Crochê", ad="100001")])
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P2})
    codigo, r, log = correr(hoja, meta, tmp, nombres=NOMBRES)
    igual(celdas(hoja)[0][1], P2, "si la Biblioteca ubica la pagina del anuncio, esa gana sobre el nombre")
    # sin indice de nombres todo sigue como antes
    hoja = HojaFalsa(CAB, [fila("Lia Crochê", ad="100009")])
    codigo, r, log = correr(hoja, MetaFalsa(PAGINAS), tmp)
    igual([r["sinPagina"], celdas(hoja)], [1, [("Lia Crochê", "", "")]], "sin indice de nombres un anuncio apagado queda sin pagina")

igual(flujo.indice_nombres({"111111111": {"name": "Lia Crochê"}, "222222222": {"name": "lia croche "}, "333333333": {"name": "Única"}, "abc": {"name": "No es id"}, "444444444": "basura", "555555555": {}, "666666666": None}),
      {"lia croche": ["111111111", "222222222"], "unica": ["333333333"]}, "indice_nombres: junta por nombre sin tildes ni mayusculas, descarta lo que no es un id de pagina y lo que no tiene nombre")
igual([flujo.indice_nombres(x) for x in (None, {}, [])], [{}, {}, {}], "indice_nombres con nada da un indice vacio")

with tempfile.TemporaryDirectory() as tmp:
    # ---- tres fallos de red seguidos cortan la corrida
    hoja = HojaFalsa(CAB, [fila(f"F{i}", ad=f"9000{i}") for i in range(6)])
    meta = MetaFalsa({}, de_anuncio={f"9000{i}": f"5{i}5555555555{i}" for i in range(6)}, falla_pagina={f"5{i}5555555555{i}": requests.exceptions.ReadTimeout("Read timed out") for i in range(6)})
    codigo, r, log = correr(hoja, meta, tmp)
    igual([codigo, r["errores"], r["intentadas"]], [1, 3, 3], "tres fallos de red seguidos: se corta (no espera 45 s por cada una de las demas) y el codigo es 1")
    ok(any("Meta no contesta varias veces seguidas" in l for l in log), "y lo dice")
    igual(hoja.escrituras, [], "sin nada hallado no se escribe")

with tempfile.TemporaryDirectory() as tmp:
    # ---- el tope por corrida y el orden: primero lo que el dueno mira
    estados = ["descartada", "nuevo", "vigilar", "no testeada", "testiando"]
    hoja = HojaFalsa(CAB, [fila(f"E{i}", ad=f"8000{i}", status=e) for i, e in enumerate(estados)])
    meta = MetaFalsa({}, de_anuncio={f"8000{i}": f"6{i}6666666666{i}" for i in range(5)})
    codigo, r, log = correr(hoja, meta, tmp, maximo=2)
    igual([c[1] for c in meta.llamadas if c[0] == "anuncio"], ["80002", "80004"], "con tope 2 entran primero «vigilar» y «testiando» (no «descartada»)")
    igual(r["intentadas"], 2, "se respeta el tope")
    igual(r["porCompletar"], 5, "y se cuenta cuantas faltaban")

with tempfile.TemporaryDirectory() as tmp:
    # ---- alguien inserta una fila mientras se hablaba con Meta: se busca por el ID del anuncio, no por el numero de fila
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001"), fila("Ministerio", ad="200001")], antes_de_releer=lambda h: h.filas.insert(0, fila("Intrusa", ad="1112223")))
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2})
    codigo, r, log = correr(hoja, meta, tmp)
    igual(celdas(hoja), [("Intrusa", "", ""), ("Lia", P1, "500001"), ("Ministerio", P2, "600001")], "con una fila insertada arriba, cada ID cae en SU fila y la intrusa queda vacia")
    # una fila borrada
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001"), fila("Ministerio", ad="200001")], antes_de_releer=lambda h: h.filas.pop(0))
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2})
    codigo, r, log = correr(hoja, meta, tmp)
    igual(celdas(hoja), [("Ministerio", P2, "600001")], "si borraron la fila de Lia, sus ids no caen en otra")
    igual(r["movidas"], 1, "y se cuenta como fila que se movio")
    # el dueno escribio la pagina mientras tanto
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001")], antes_de_releer=lambda h: h.filas[0].update({"ID de la página": "999999999"}))
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1})
    codigo, r, log = correr(hoja, meta, tmp)
    igual(celdas(hoja), [("Lia", "999999999", "500001")], "si el dueno puso la pagina mientras tanto, se queda la suya (el video, que seguia vacio, si se llena)")
    # el dueno escribio el video mientras tanto
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001")], antes_de_releer=lambda h: h.filas[0].update({"ID del video": "123456789"}))
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1})
    codigo, r, log = correr(hoja, meta, tmp)
    igual(celdas(hoja), [("Lia", P1, "123456789")], "si el dueno puso el video mientras tanto, se queda el suyo (la pagina, que seguia vacia, si se llena)")

with tempfile.TemporaryDirectory() as tmp:
    # ---- sin las columnas en la planilla, o una planilla que no deja escribir
    hoja = HojaFalsa(CAB[:-2], [fila("Lia", ad="100001")])
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1})
    codigo, r, log = correr(hoja, meta, tmp)
    igual([codigo, meta.llamadas, hoja.escrituras], [1, [], []], "sin las columnas: codigo 1, no pide nada a Meta y no escribe")
    ok(any("--preparar-flujo" in l for l in log), "y dice el comando que las crea")
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001"), fila("Mundo", ad="200001")])
    hoja.falla_al_escribir = True
    codigo, r, log = correr(hoja, MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2}), tmp)
    igual([codigo, r["errores"], r["escritas"]], [1, 1, 0], "si la planilla rechaza la escritura: codigo 1 y nada escrito")
    ok(any("No pude escribir los IDs" in l for l in log), "y lo dice")
    hoja.falla_al_escribir = False
    codigo, r, log = correr(hoja, MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2}), tmp)
    igual([codigo, celdas(hoja)], [0, [("Lia", P1, "500001"), ("Mundo", P2, "600001")]], "y la siguiente corrida lo repite y lo logra")

with tempfile.TemporaryDirectory() as tmp:
    # ---- simulacion: pregunta a Meta pero no escribe nada ni deja archivo
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001")])
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1})
    codigo, r, log = correr(hoja, meta, tmp, simular=True)
    igual([hoja.escrituras, celdas(hoja), (Path(tmp) / "flujo.json").exists()], [[], [("Lia", "", "")], False], "simular no toca la planilla ni crea flujo.json")
    ok(any("[simulacion] escribiria" in l and "500001" in l for l in log) and meta.llamadas, "pero si consulta a Meta y muestra lo que escribiria")

with tempfile.TemporaryDirectory() as tmp:
    # ---- al terminar se poda lo anotado hace 60 dias o mas
    ruta = Path(tmp) / "flujo.json"
    flujo.guardar({"version": 1, "filas": {"viejo": {"t": "2026-07-01", "motivo": "sin_video"}, "reciente": {"t": "2026-10-20", "motivo": "sin_video"}}}, ruta)
    codigo, r, log = correr(HojaFalsa(CAB, [fila("Lia", ad="100001")]), MetaFalsa(PAGINAS, de_anuncio={"100001": P1}), tmp)
    igual(sorted(flujo.cargar(ruta)["filas"]), ["reciente"], "al terminar se poda lo anotado hace 60 dias o mas y se conserva lo reciente")

# ---------------------------------------------------------------- candidatas
HOY = "2026-10-22"
fs = [dict(fila("A completa", ad="10001", pagina=P1, video="500001"), _row=2), dict(fila("B sin ids", ad="20002"), _row=3), dict(fila("C video malo", ad="30003", video="2.4E+15"), _row=4),
      dict(fila("D sin nada"), _row=5), dict(fila("E por enlace", link="https://www.facebook.com/ads/library/?view_all_page_id=" + P2), _row=6), dict(fila("F reciente", ad="60006"), _row=7),
      dict(fila("G pagina puesta sin ad", pagina=P3), _row=8)]
cands, recientes = flujo.candidatas(fs, {"60006": {"t": "2026-10-20", "motivo": "sin_video"}}, HOY)
igual([c["anunciante"][0] for c in cands], ["G", "E", "C", "B"], "candidatas: sin las completas, sin las que no tienen de donde partir y sin las anotadas hace poco; la de abajo primero")
igual(recientes, 1, "se cuentan las anotadas hace poco")
igual([(c["falta_pagina"], c["falta_video"]) for c in cands], [(False, True), (True, True), (True, False), (True, True)], "que le falta a cada una (el video malo cuenta como puesto: no se pisa)")
igual([c["pagina"] for c in cands], [P3, P2, "", ""], "la pagina de partida: la de la celda o la del enlace de la Biblioteca")
cands, recientes = flujo.candidatas(fs, {"60006": {"t": "2026-10-15", "motivo": "sin_video"}}, HOY)
ok(any(c["anunciante"].startswith("F") for c in cands) and recientes == 0, "una nota de hace 7 dias o mas ya no frena")
cands, _ = flujo.candidatas([dict(fila("X", ad="10001", status=e), _row=i + 2) for i, e in enumerate(["descartada", "", "nuevo", "ganadora", "no testeada", "testiando", "vigilar", "algo raro"])], {}, HOY)
igual([c["estado"] for c in cands], ["vigilar", "testiando", "ganadora", "nuevo", "", "no testeada", "descartada", "algo raro"], "orden por estado: vigilar, testiando, ganadora, nuevo, sin estado, no testeada, descartada, lo raro al final")
igual(flujo.columnas_de(CAB), (6, 7), "las columnas se hallan por nombre (sin importar tildes ni mayusculas)")
igual(flujo.columnas_de(["id DE LA PAGINA", "x", "Id Del Video"]), (0, 2), "con otras mayusculas y sin tilde tambien")
igual(flujo.columnas_de(CAB[:-1]), None, "si falta una, None")


# ---------------------------------------------------------------- flujo.json
with tempfile.TemporaryDirectory() as tmp:
    ruta = Path(tmp) / "flujo.json"
    igual(flujo.cargar(ruta), {"version": 1, "filas": {}}, "sin archivo: vacio")
    ruta.write_text("esto no es json", encoding="utf-8")
    igual(flujo.cargar(ruta), {"version": 1, "filas": {}}, "con un archivo dañado: vacio (se reintenta todo, no se rompe)")
    ruta.write_text('["lista"]', encoding="utf-8")
    igual(flujo.cargar(ruta), {"version": 1, "filas": {}}, "con un archivo que no es un objeto: vacio")
    memo = {"a": {"t": "2026-08-01", "motivo": "x"}, "b": {"t": "2026-10-10", "motivo": "x"}, "c": {"motivo": "sin fecha"}, "d": "basura"}
    flujo._podar(memo, HOY)
    igual(sorted(memo), ["b"], "se poda lo de hace 60 dias o mas y lo mal formado")
    igual([flujo._reciente({"x": {"t": "2026-10-16"}}, "x", HOY), flujo._reciente({"x": {"t": "2026-10-15"}}, "x", HOY), flujo._reciente({}, "x", HOY), flujo._reciente({"x": {"t": "ayer"}}, "x", HOY)],
          [True, False, False, False], "reciente = menos de 7 dias (6 si, 7 no); sin nota o con fecha mala no frena")


# ---------------------------------------------------------------- Sheet.asegurar_columnas (API de Google simulada)
class SheetFalsa(sheets.Sheet):
    def __init__(self, cabecera, columnas_grid=None, tab="OFERTAS", filas=500):
        super().__init__(sheet_id="x" * 30, session=object())
        self.cabecera, self.col_grid, self.tab, self.n_filas, self.pedidos = list(cabecera), len(cabecera) if columnas_grid is None else columnas_grid, tab, filas, []

    def _req(self, method, path, **kw):
        self.pedidos.append((method, urllib.parse.unquote(path), kw))
        if method == "GET" and path == "":
            return {"sheets": [{"properties": {"sheetId": 77, "title": self.tab, "gridProperties": {"rowCount": self.n_filas, "columnCount": self.col_grid}}}]}
        if method == "GET" and path.startswith("/values/"):
            return {"values": [list(self.cabecera)]}
        if method == "PUT":
            self.cabecera.extend(kw["json"]["values"][0])
            return {}
        if method == "POST" and path == ":batchUpdate":
            for rq in kw["json"]["requests"]:
                if "appendDimension" in rq:
                    self.col_grid += rq["appendDimension"]["length"]
            return {}
        raise AssertionError((method, path))


def formatos(s):
    out = []
    for m, p, kw in s.pedidos:
        for rq in (kw.get("json") or {}).get("requests", []):
            if "repeatCell" in rq:
                rg = rq["repeatCell"]["range"]
                out.append((rg["startColumnIndex"], rg["endColumnIndex"], rg["startRowIndex"], rg["endRowIndex"], rq["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]["type"], rq["repeatCell"]["range"]["sheetId"]))
    return out


s = SheetFalsa(CAB[:-2])         # 6 columnas y una cuadricula de 6: no hay sitio para las dos nuevas
nuevas = s.asegurar_columnas("OFERTAS", flujo.COLUMNAS)
igual(nuevas, list(flujo.COLUMNAS), "agrega las dos columnas que faltaban")
igual(s.cabecera, CAB, "al FINAL de los encabezados, sin mover ni cambiar ninguno")
igual([p for m, p, kw in s.pedidos if m == "PUT"], ["/values/'OFERTAS'!G1"], "los escribe en la primera celda libre de la fila 1 (G1)")
ok(any(m == "POST" and any("appendDimension" in rq for rq in kw["json"]["requests"]) for m, p, kw in s.pedidos), "amplia la cuadricula cuando no hay sitio (values.update no lo hace)")
igual(s.col_grid, 8, "con exactamente las 2 columnas que faltaban")
igual(formatos(s), [(6, 7, 1, 500, "TEXT", 77), (7, 8, 1, 500, "TEXT", 77)], "las deja en formato TEXTO desde la fila 2, cada una en su columna")
ok(all(rq.get("repeatCell") or rq.get("appendDimension") for m, p, kw in s.pedidos if m == "POST" for rq in kw["json"]["requests"]), "no se manda ninguna otra clase de cambio (borrar, mover, insertar)")
s = SheetFalsa(CAB[:-2], columnas_grid=26)
s.asegurar_columnas("OFERTAS", flujo.COLUMNAS)
ok(not any(m == "POST" and any("appendDimension" in rq for rq in kw["json"]["requests"]) for m, p, kw in s.pedidos), "si la cuadricula ya tiene sitio no la amplia")
s = SheetFalsa(CAB)                # ya estan las dos
igual(s.asegurar_columnas("OFERTAS", flujo.COLUMNAS), [], "con las dos columnas ya puestas no agrega nada")
ok(not any(m == "PUT" for m, p, kw in s.pedidos), "ni escribe encabezados")
igual(formatos(s), [(6, 7, 1, 500, "TEXT", 77), (7, 8, 1, 500, "TEXT", 77)], "pero si se asegura el formato texto (idempotente)")
s = SheetFalsa(CAB[:-1])           # solo falta el video
igual([s.asegurar_columnas("OFERTAS", flujo.COLUMNAS), s.cabecera[-1]], [["ID del video"], "ID del video"], "si solo falta una, agrega solo esa")
try:
    SheetFalsa(CAB, tab="OTRA").asegurar_columnas("OFERTAS", flujo.COLUMNAS)
    ok(False, "una pestana que no existe debe fallar")
except sheets.SheetsError as e:
    ok("OFERTAS" in str(e), "y el error nombra la pestana")


# ---------------------------------------------------------------- los dos comandos de radar.py (todo simulado)
class HojaCmd(HojaFalsa):
    pass


def con_simulados(hoja, meta, tmp, f):
    viejo = (radar.sheets.available, radar.sheets.Sheet, radar.Meta, radar.load_env, radar.polite, flujo.RUTA, radar.load_state)
    radar.sheets.available, radar.sheets.Sheet, radar.Meta = (lambda: True), (lambda *a, **k: hoja), (lambda *a, **k: meta)
    radar.load_env, radar.polite, flujo.RUTA = (lambda *a, **k: None), (lambda *a, **k: None), Path(tmp) / "flujo.json"
    radar.load_state = lambda: {"seen_pages": {"999900000428888": {"name": "Mundo"}}}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            codigo = f()
    finally:
        radar.sheets.available, radar.sheets.Sheet, radar.Meta, radar.load_env, radar.polite, flujo.RUTA, radar.load_state = viejo
    return codigo, buf.getvalue()


with tempfile.TemporaryDirectory() as tmp:
    hoja = HojaFalsa(CAB, [fila("Lia", ad="100001"), fila("Mundo", ad="200001")])
    meta = MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2})
    codigo, texto = con_simulados(hoja, meta, tmp, lambda: radar.rellenar_flujos(True))
    ok(codigo == 0 and "Flujo de WhatsApp: 2 filas | por completar: 2" in texto and "SIMULACION" in texto and hoja.escrituras == [], "rellenar_flujos(simular): resume, dice SIMULACION y no escribe: " + texto[-200:])
    codigo, texto = con_simulados(hoja, MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2}), tmp, lambda: radar.rellenar_flujos(False))
    ok(codigo == 0 and "IDs hallados: 2" in texto and "escritas: 4" in texto and celdas(hoja) == [("Lia", P1, "500001"), ("Mundo", P2, "600001")], "rellenar_flujos de verdad: escribe y resume: " + texto[-200:])
    codigo, texto = con_simulados(hoja, MetaFalsa(PAGINAS), tmp, lambda: radar.rellenar_flujos(False, 0))
    ok(codigo == 0 and "intentadas: 0" in texto, "con tope 0 no intenta ninguna: " + texto[-160:])
    hoja3 = HojaFalsa(CAB, [fila("A", ad="100001"), fila("B", ad="200001")])
    codigo, texto = con_simulados(hoja3, MetaFalsa(PAGINAS, de_anuncio={"100001": P1, "200001": P2}), tmp, lambda: radar.rellenar_flujos(False, 1))
    ok(codigo == 0 and "por completar: 2" in texto and "intentadas: 1" in texto and "IDs hallados: 1" in texto, "con tope 1 y dos filas pendientes intenta solo una: " + texto[-220:])
    hoja4 = HojaFalsa(CAB, [fila("Mundo", ad="900001")])             # el anuncio ya no esta en la Biblioteca, pero el radar vio una pagina llamada «Mundo» (state.json de la prueba)
    codigo, texto = con_simulados(hoja4, MetaFalsa({"999900000428888": [anuncio("900001", "999900000428888", "990001", cta="WHATSAPP_MESSAGE", start=100)]}), tmp, lambda: radar.rellenar_flujos(False))
    ok(codigo == 0 and "IDs hallados: 1" in texto and celdas(hoja4) == [("Mundo", "999900000428888", "990001")], "rellenar_flujos usa el respaldo por nombre con el state.json del radar: " + texto[-200:])
    hoja2 = HojaFalsa(CAB, [fila(f"F{i}", ad=f"7000{i}") for i in range(4)])
    codigo, texto = con_simulados(hoja2, MetaFalsa({}, de_anuncio={f"7000{i}": P1 for i in range(4)}, falla_pagina={P1: radar.RateLimited("HTTP 429")}), tmp, lambda: radar.rellenar_flujos(False))
    ok(codigo == 3 and "META LIMITO LA IP" in texto, "si Meta limita, el comando sale con 3 y lo dice: " + texto[-200:])

    class SheetPreparar:
        def __init__(self, *a, **k):
            pass

        def asegurar_columnas(self, tab, nombres):
            self.llamado = (tab, tuple(nombres))
            return list(nombres)

    viejo = (radar.sheets.available, radar.sheets.Sheet)
    radar.sheets.available, radar.sheets.Sheet = (lambda: True), SheetPreparar
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            radar.preparar_flujo()
        ok("agregue las columnas ID de la página y ID del video" in buf.getvalue() and "Texto sin formato" in buf.getvalue(), "preparar_flujo cuenta lo que hizo: " + buf.getvalue())
    finally:
        radar.sheets.available, radar.sheets.Sheet = viejo


# ---------------------------------------------------------------- una corrida completa del radar: la oferta NUEVA se escribe con sus dos ids (Meta, IA y planilla simulados; la nota minima baja a 6: se prueba el camino de los ids, no la rubrica)
import time

PID = "123456789"


def anuncios_de(pid, nombre, n=8, con_video=lambda i: True, cta=lambda i: "WHATSAPP_MESSAGE", dias=70, link="https://api.whatsapp.com/send?phone=5511"):
    """n piezas activas con PDF en el copy (pasan los filtros y la nota); la pieza i = n-1 es la MAS VIEJA. El video de la pieza i es 70000<pid><i>."""
    ahora = time.time()
    return [{"archive_id": f"{pid}{i}", "collation_id": f"{pid}c{i}", "collation_count": 1, "page_id": pid, "page_name": nombre, "start": ahora - (dias + i) * 86400, "active": True,
             "body": f"Material em PDF croche bordado receitas {i}", "text": f"Material em PDF croche bordado receitas {i}", "link": link, "cta": "",
             "cta_type": cta(i), "page_like": 1200, "video": con_video(i), "video_id": f"70000{pid}{i}" if con_video(i) else "", "sig": f"{pid}s{i}"} for i in range(n)]


class MetaDeAnuncios:
    ads = []

    def __init__(self, *a, **k):
        self.lsd = "x"

    def bootstrap(self, country):
        return "lsd", "doc"

    def collect(self, q, country, pages=1, page_id=None, **k):
        return (list(MetaDeAnuncios.ads), True) if country == "BR" else ([], False)

    def inspect_landing(self, link):
        return ""


class PlanillaDeOfertas:
    escritas = []

    def check_headers(self, tab, required):
        pass

    def read_dicts(self, tab):
        return []

    def headers(self, tab):
        return ["Palabra clave", "Búsquedas", "Ofertas registradas", "Última búsqueda"] if tab == "PALABRAS-CLAVE" else radar.SHEET_COLS + list(flujo.COLUMNAS) + radar.SEMAFORO_COLS

    def append_dict(self, tab, row):
        PlanillaDeOfertas.escritas.append((tab, dict(row)))

    def asegurar_columnas(self, tab, nombres):          # la planilla falsa ya trae las columnas del semaforo (ver headers)
        return []

    def update_cells(self, tab, cells):
        pass


def corrida_con_planilla(tmp, ads):
    """Corre radar._correr con una planilla conectada y devuelve las filas que se agregaron a OFERTAS."""
    tmp = Path(tmp)
    (tmp / "out").mkdir(exist_ok=True)
    (tmp / "palabras.csv").write_text("pt,es,nicho,mercado\ncroche vestidos,,croche,Brasil\n", encoding="utf-8")
    (tmp / "angulos.csv").write_text("angulo,tipo,origen\nrenda extra em casa,ingreso extra,semilla\n", encoding="utf-8")
    (tmp / "temporadas.csv").write_text("palabra,es,nicho,temporada,prioridad,desde,hasta\n", encoding="utf-8")
    args = types.SimpleNamespace(simular_sheets=False, sin_sheets=False, tope_diario=0, modo="palabra", min_nota=6, max_kw=1, temporadas=str(tmp / "temporadas.csv"), sin_ia=True, revisar_max=0,
                                 pages_co=1, pages=1, nota_sin_colombia=6, min_activos=8, keywords=str(tmp / "palabras.csv"), angulos=str(tmp / "angulos.csv"), max_temporada=0,
                                 max_seguidores=500_000, max_ofertas=3, max_adv=8, exigir_colombia=False, dias_temporada=14, cache=False, foco=str(tmp / "foco.csv"), max_foco=0,
                                 min_anuncios_oferta=0)       # la oferta principal de la pagina se prueba aparte (test_oferta.py)
    MetaDeAnuncios.ads, PlanillaDeOfertas.escritas = ads, []
    viejo = (radar.Meta, radar.OUT, radar.STATE_FILE, radar.load_env, radar.polite, radar.ia.available, radar.sheets.available, radar.sheets.Sheet, radar.ia.USO_FILE, radar.ia.ENV_FILE)
    radar.Meta, radar.OUT, radar.STATE_FILE = MetaDeAnuncios, tmp / "out", tmp / "state.json"
    radar.load_env, radar.polite, radar.ia.available = (lambda *a, **k: None), (lambda *a, **k: None), (lambda: False)
    radar.sheets.available, radar.sheets.Sheet = (lambda: True), PlanillaDeOfertas
    radar.ia.USO_FILE, radar.ia.ENV_FILE = tmp / "ia_uso.json", tmp / "no-existe.env"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            radar._correr(args)
    finally:
        (radar.Meta, radar.OUT, radar.STATE_FILE, radar.load_env, radar.polite, radar.ia.available, radar.sheets.available, radar.sheets.Sheet, radar.ia.USO_FILE, radar.ia.ENV_FILE) = viejo
    descartes = "".join(f.read_text(encoding="utf-8-sig") for f in (tmp / "out").glob("descartes_*.csv"))
    return [r for t, r in PlanillaDeOfertas.escritas if t == "OFERTAS"], buf.getvalue() + "\nDESCARTES:\n" + descartes


with tempfile.TemporaryDirectory() as tmp:
    filas_nuevas, texto = corrida_con_planilla(tmp, anuncios_de(PID, "Tienda con video"))
    ok(len(filas_nuevas) == 1, "(la corrida de prueba registra la oferta en la planilla) " + texto[-400:])
    f = filas_nuevas[0]
    igual(f["Link del anuncio"], f"https://www.facebook.com/ads/library/?id={PID}7", "el «Link del anuncio» es el del anuncio mas viejo")
    igual([f[flujo.COLUMNAS[0]], f[flujo.COLUMNAS[1]]], [PID, f"70000{PID}7"], "la oferta nueva se escribe con el ID de la pagina y el ID del video DEL MISMO anuncio, sin ninguna peticion mas")
    igual(sorted(f), sorted(radar.SHEET_COLS + list(flujo.COLUMNAS) + radar.SEMAFORO_COLS + [radar.LINKS_COL]),
          "y con las columnas de siempre, las del semaforo y la de los links en espanol (nada de las demas extra del CSV)")

with tempfile.TemporaryDirectory() as tmp:
    # el anuncio mas viejo es de imagen: se usa el video del anuncio de WhatsApp mas viejo que si lo tiene
    filas_nuevas, texto = corrida_con_planilla(tmp, anuncios_de(PID, "Tienda con imagen vieja", con_video=lambda i: i != 7, cta=lambda i: "WHATSAPP_MESSAGE" if i in (3, 5) else "LEARN_MORE", link="https://tienda.example.com.br/oferta"))
    f = filas_nuevas[0]
    igual(f["Link del anuncio"], f"https://www.facebook.com/ads/library/?id={PID}7", "el anuncio de la fila sigue siendo el mas viejo (de imagen)")
    igual([f[flujo.COLUMNAS[0]], f[flujo.COLUMNAS[1]]], [PID, f"70000{PID}5"], "pero el video sale del anuncio de WhatsApp mas viejo que tiene video")

with tempfile.TemporaryDirectory() as tmp:
    # ningun anuncio con video: se escribe la pagina y NO se manda un video vacio
    filas_nuevas, texto = corrida_con_planilla(tmp, anuncios_de(PID, "Tienda solo imagenes", con_video=lambda i: False))
    f = filas_nuevas[0]
    igual([f.get(flujo.COLUMNAS[0]), flujo.COLUMNAS[1] in f], [PID, False], "sin video: va la pagina y la celda del video no se manda")

# ---- el anuncio de UNA oferta leido de su propia pagina en la Biblioteca (2026-11-13, «Buscar el WhatsApp» por anuncio)
# El HTML real (Mentora, 999900000302813) trae el ID una vez, dentro del objeto del anuncio; su search_results_connection viene vacio. Aqui, un HTML con la misma forma.
import radar
OBJ = {"ad_archive_id": "999900000302813", "page_id": "999900000431570", "is_active": True, "collation_count": None, "collation_id": None, "page_name": "Mentora",
       "snapshot": {"body": {"text": "¿Y si pudieras experimentar una salida del cuerpo? {no es JSON} \"comillas\""}, "cta_type": "WHATSAPP_MESSAGE", "link_url": "https://api.whatsapp.com/send",
                    "page_profile_uri": "https://www.facebook.com/61586031826854/", "display_format": "VIDEO", "videos": [{"video_hd_url": url_video("9999000000448153")}], "cards": []}}
HTML = ('<html><script>{"require":[["x",{"otro":{"ad_archive_id":"1111111111111","snapshot":{}}}]]}</script><script type="application/json">{"data":{"x":{"search_results_connection":{"edges":[]}},'
        '"ad":' + json.dumps(OBJ, ensure_ascii=False, separators=(",", ":")) + ',"mas":{"a":[1,2,{"b":"}"}]}}}</script></html>')
o = flujo.objeto_de_anuncio(HTML, "999900000302813")
igual([o and o["ad_archive_id"], o and o["page_id"]], ["999900000302813", "999900000431570"], "saca el objeto del anuncio del HTML de su pagina (con llaves dentro de los textos)")
n = radar.normalize(o)
igual([n["archive_id"], n["page_id"], n["video_id"], n["cta_type"], flujo.vende_por_whatsapp(n), n["page_uri"]],
      ["999900000302813", "999900000431570", "9999000000448153", "WHATSAPP_MESSAGE", True, "https://www.facebook.com/61586031826854/"], "y normalizado sirve como los de la pagina (texto, boton, video)")
ok("salida del cuerpo" in n["text"], "con su texto")
igual(flujo.objeto_de_anuncio(HTML, "1111111111111")["snapshot"], {}, "cada ID da SU objeto (el otro anuncio del HTML no se confunde con el de la oferta)")
igual((flujo.objeto_de_anuncio(json.dumps({"x": OBJ}), "999900000302813") or {}).get("page_id"), "999900000431570", "tambien con espacios despues de los dos puntos")
igual(flujo.objeto_de_anuncio(HTML, "2222222222222"), None, "un anuncio que no esta en el HTML (apagado): None")
igual(flujo.objeto_de_anuncio('{"ad_archive_id":"999900000302813","page_id":"1"}', "999900000302813"), None, "sin snapshot no sirve")
igual(flujo.objeto_de_anuncio('<x>"ad_archive_id":"999900000302813" roto {', "999900000302813"), None, "un HTML roto: None, sin lanzar")
igual([flujo.objeto_de_anuncio(HTML, "abc"), flujo.objeto_de_anuncio(None, "999900000302813"), flujo.objeto_de_anuncio(HTML, "88903543073281.")], [None, None, None], "datos raros: None")
lejos = '{"ad_archive_id":"999900000302813","snapshot":{"body":{"text":"' + "x" * (flujo.MAX_ATRAS + 10) + '"}},"z":1}'
igual(flujo.objeto_de_anuncio(lejos, "999900000302813") is not None, True, "el ID al principio de un objeto grande se halla (el objeto empieza justo antes)")
igual(flujo.objeto_de_anuncio('{"snapshot":{},"x":"' + "y" * (flujo.MAX_ATRAS + 10) + '","ad_archive_id":"999900000302813"}', "999900000302813"), None, "no se busca mas atras de MAX_ATRAS caracteres")

# endurecido tras la revision de seguridad (13-nov)
hondo = '{"ad_archive_id":"999900000302813","snapshot":' + "[" * 6000 + "]" * 6000 + '}'
igual(flujo.objeto_de_anuncio(hondo, "999900000302813"), None, "un JSON anidado miles de niveles no lanza (RecursionError atrapado)")
señuelos = ('"ad_archive_id":"999900000302813",' * 6) + '{"ad_archive_id":"999900000302813","snapshot":{}}'
igual(flujo.objeto_de_anuncio(señuelos, "999900000302813"), None, "como mucho MAX_APARICIONES apariciones del ID se prueban")


class RespF:
    def __init__(self, codigo, texto):
        self.status_code, self.text = codigo, texto

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def meta_con(codigo, texto):
    m = radar.Meta.__new__(radar.Meta)
    m._open_library = lambda params: RespF(codigo, texto)
    return m


igual(meta_con(200, HTML).anuncio("999900000302813")["video_id"], "9999000000448153", "radar.Meta.anuncio: el anuncio de su pagina, normalizado")
igual(meta_con(200, '<html><script>{"search_results_connection":{"edges":[]}}</script></html>').anuncio("999900000302813"), None, "Meta.anuncio: una pagina de la Biblioteca sin ese anuncio (apagado): None")
for nombre, codigo, texto, exc in [("muro de login", 200, "<html>Inicia sesion</html>", flujo.SinDatos), ("429", 429, "", radar.RateLimited),
                                   ("body como texto", 200, '{"ad_archive_id":"999900000302813","snapshot":{"body":"texto suelto","cards":["x"]}}', flujo.SinDatos)]:
    try:
        meta_con(codigo, texto).anuncio("999900000302813")
        ok(False, f"Meta.anuncio con {nombre} debia lanzar {exc.__name__}")
    except exc:
        ok(True, "")

print(f"OK: {bien} comprobaciones de flujo.py")
