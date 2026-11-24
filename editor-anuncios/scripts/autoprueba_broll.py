"""Autoprueba de scripts/broll_tiktok.py SIN internet y sin gastar nada: un proveedor de busqueda FALSO en esta misma
maquina y un descargador FALSO que entrega videos de mentira hechos con ffmpeg.

Comprueba: enlaces buenos y malos, la lista de una cuenta, las respuestas del proveedor de pago (paginas, repetidos, anuncios, fotos) y de la lista
publica de TikTok que se usa gratis (un TikTok falso: sesion de visitante, paginas, palabras que no existen, esperas, tope, palabras relacionadas),
la cola intercalada, fuentes.csv, los videos que no sirven (horizontal, corto, roto, privado), lo que se reintenta y lo que no,
la copia a Drive (incluida la carpeta que no esta montada y se repara despues), --simular y que la llave nunca se imprime.

Uso:  python scripts/autoprueba_broll.py [--vivo URL_DE_UN_VIDEO_DE_TIKTOK [--solo-vivo]]
Corre esto despues de tocar broll_tiktok.py. Si todo va bien termina con "AUTOPRUEBA OK".
Con --vivo ademas baja ESE video de verdad (necesita internet), sin tocar ningun producto: comprueba que yt-dlp, la huella de
navegador y la eleccion de formato siguen funcionando contra TikTok. --solo-vivo se salta todo lo demas (sirve en un servidor sin ffmpeg).
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola, ejecutable  # noqa: E402

LLAVE = "llave-de-prueba-0123456789"
FALLAS: list[str] = []
VISTO: list[tuple[str, dict]] = []     # lo que llego al proveedor falso: (ruta, parametros)
MODO = {"estado": 200, "pendientes_429": 0, "sin_exito": False}
VISTO_TT: list[tuple[str, dict, dict]] = []     # lo que llego al TikTok falso: (ruta, parametros, cabeceras)
MODO_TT = {"sin_wid": False, "pendientes_429": 0, "siempre_429": False}


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"  ->  {detalle}" if detalle else ""))


# ------------------------------------------------------------------ proveedor falso (misma forma que ScrapeCreators)
def aweme(vid, autor, dur_ms, ancho, alto, vistas, likes, desc="", anuncio=False, foto=False):
    video = {"duration": dur_ms, "width": ancho, "height": alto, "play_addr": {"width": ancho, "height": alto}, "bit_rate": [{"play_addr": {}}]}
    cuerpo = {"aweme_id": vid, "desc": desc, "region": "CO", "statistics": {"aweme_id": vid, "play_count": vistas, "digg_count": likes},
              "video": video, "author": {"unique_id": autor, "nickname": autor.upper()}, "is_ad": anuncio, "url": f"https://www.tiktok.com/@{autor}/video/{vid}"}
    if foto:
        cuerpo["video"] = {"width": 0, "height": 0}
        cuerpo["images"] = [{"url": "x"}]
    return cuerpo


V1 = "9999000000000090001"
V2 = "9999000000000090002"
V3 = "9999000000000090003"
V4 = "9999000000000090004"
V5 = "9999000000000090005"
V6 = "9999000000000090006"
V7 = "9999000000000090007"
PAGINA1 = [aweme(V1, "ana_crochet", 22874, 576, 1024, 1200000, 85000, "tejiendo crochet"),
           aweme(V2, "luz.manos", 15000, 720, 1280, 90000, 4000, "manos tejiendo"),
           aweme(V3, "tienda", 30000, 576, 1024, 5, 1, "oferta", anuncio=True),
           aweme(V4, "paisaje", 20000, 1920, 1080, 500, 20, "horizontal"),
           aweme(V5, "fotos", 0, 0, 0, 10, 1, "foto", foto=True)]
PAGINA2 = [aweme(V2, "luz.manos", 15000, 720, 1280, 90000, 4000, "repetido de la pagina 1"),
           aweme(V6, "tejedora", 40000, 1080, 1920, 7000, 300, "segunda pagina")]


class Falso(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, codigo, cuerpo):
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        u = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        VISTO.append((u.path, params))
        if self.headers.get("x-api-key") != LLAVE:
            return self._json(401, {"success": False, "error": "llave mala"})
        if MODO["estado"] != 200:
            return self._json(MODO["estado"], {"success": False, "error": "modo de prueba"})
        if MODO["sin_exito"]:
            return self._json(200, {"success": False, "message": "cuota de prueba agotada"})
        if MODO["pendientes_429"] > 0:
            MODO["pendientes_429"] -= 1
            return self._json(429, {"success": False})
        if u.path == "/v1/tiktok/search/keyword":
            if "cursor" not in params:
                return self._json(200, {"success": True, "credits_remaining": 97, "credits_charged": 1, "cursor": 12, "search_item_list": PAGINA1})
            return self._json(200, {"success": True, "credits_remaining": 96, "credits_charged": 1, "cursor": 0,
                                    "search_item_list": [{"aweme_info": p} for p in PAGINA2]})
        if u.path == "/v1/tiktok/search/hashtag":
            return self._json(200, {"success": True, "credits_remaining": 95, "credits_charged": 1, "cursor": 0,
                                    "aweme_list": [aweme(V7, "hashtagera", 12000, 576, 1024, 800, 50, "por hashtag")]})
        self._json(404, {"success": False})


# ------------------------------------------------------------------ TikTok falso: /discover y la lista publica (api/seo/kap)
def kap(vid, autor, dur, ancho, alto, vistas=1000, likes=10, desc="", reproducible=True, foto=False):
    v = {"duration": dur, "width": ancho, "height": alto, "playAddr": "https://cdn.falso/x.mp4" if reproducible else "", "downloadAddr": ""}
    cuerpo = {"id": vid, "desc": desc, "author": {"uniqueId": autor, "nickname": autor}, "stats": {"playCount": vistas, "diggCount": likes}, "video": v}
    if foto:
        cuerpo["imagePost"] = {"images": [{}]}
    return cuerpo


def tdk(vid, titulo, descripcion="", claves=()):
    return {"itemId": vid, "customTdk": {"title": titulo, "description": descripcion, "keywords": list(claves)}}


K1, K2, K3, K4, K5, K6, K7, K8, K9, K10, K11 = (f"99990000000100000{n:02d}" for n in range(1, 12))
KAP_CROCHET = [
    ([kap(K1, "ana_crochet", 22, 576, 1024, 1200000, 85000, "mi primer amigurumi"), kap(K2, "luz.manos", 15, 720, 1280, 90000, 4000, "TEJIENDO sin parar"),
      kap(K3, "fotos", 0, 0, 0, 10, 1, "carrusel de fotos", reproducible=False, foto=True), kap(K4, "paisaje", 20, 1920, 1080, 500, 20, "horizontal"),
      kap(K5, "largo", 300, 576, 1024, 800, 30, "muy largo")],
     [tdk(K1, "Amigurumi paso a paso", "Aprende a tejer un amigurumi", ["crochet", "amigurumi"]), tdk(K2, "Tejer sin parar")], True),
    ([kap(K2, "luz.manos", 15, 720, 1280, 90000, 4000, "TEJIENDO sin parar"), kap(K6, "tejedora", 40, 1080, 1920, 7000, 300, "segunda pagina")], [tdk(K6, "Puntada baja para principiantes")], False),
]
KAP_PRINCIPIANTES = [([kap(K7, "fiesta_ana", 12, 576, 1024, 5000, 200, "primeros puntos"), kap(K8, "tejedora2", 18, 576, 1024, 300, 9, "ovillo y aguja")],
                      [tdk(K7, "Crochet para principiantes: primeros puntos")], False)]
KAP_GLOBOS = [([kap(K9, "globero", 18, 576, 1024, 300, 9, "inflar globos")], [tdk(K9, "Dinamica con globos para reuniones")], False)]
KAP_MUJERES = [([kap(K10, "reunion_ana", 25, 576, 1024, 9000, 300, "dinamica de mujeres"), kap(K11, "reunion_bea", 30, 576, 1024, 800, 20, "juego de mujeres")], [], False)]
KAP_LISTAS = {"crochet": KAP_CROCHET, "crochet-para-principiantes": KAP_PRINCIPIANTES, "dinamica-con-globos": KAP_GLOBOS, "dinamicas-para-mujeres": KAP_MUJERES}
KAP_RELACIONADAS = {"crochet": ["crochet para principiantes", "CROCHET", "dinamica con globos", "bolsos de crochet", "manualidades navidenas"],
                    "dinamicas-para-mujeres": ["dinamicas para mujeres cristianas", "juegos para mujeres"]}


class FalsoTikTok(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, codigo, datos: bytes, tipo="application/json", extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(datos)

    def _json(self, cuerpo, codigo=200):
        self._enviar(codigo, json.dumps(cuerpo).encode())

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        u = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        VISTO_TT.append((u.path, params, {k.lower(): v for k, v in self.headers.items()}))
        if u.path == "/discover":
            if MODO_TT["siempre_429"]:
                return self._enviar(429, b"slow down", "text/plain")
            ctx = {} if MODO_TT["sin_wid"] else {"wid": "9999000000000110001", "region": "US"}
            html = '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">' + json.dumps({"__DEFAULT_SCOPE__": {"webapp.app-context": ctx}}) + "</script></html>"
            return self._enviar(200, html.encode(), "text/html", {"Set-Cookie": "ttwid=visitante123; Path=/"})
        if not u.path.startswith("/api/seo/kap/"):
            return self._enviar(404, b"no", "text/plain")
        if "ttwid=visitante123" not in self.headers.get("Cookie", ""):
            return self._json({"log_pb": {}, "status_code": 10000, "status_msg": "sin cookie de visitante"})
        if MODO_TT["siempre_429"]:
            return self._enviar(429, b"slow down", "text/plain")
        if MODO_TT["pendientes_429"] > 0:
            MODO_TT["pendientes_429"] -= 1
            return self._enviar(429, b"slow down", "text/plain")
        k = params.get("keyword", "")
        if u.path.endswith("/video_list/"):
            if k == "vacia":
                return self._enviar(200, b"", "application/json")
            if k == "rara":
                return self._json({"log_pb": {}, "status_code": 10000, "status_msg": "algo que no conocemos"})
            paginas = KAP_LISTAS.get(k)
            if not paginas:
                return self._json({"log_pb": {}, "status_code": 4, "status_msg": "El servidor no esta disponible actualmente."})
            desplazamiento, n = int(params.get("offset", "0")), 0
            for i, (videos, tdks, mas) in enumerate(paginas):
                if desplazamiento <= n:
                    return self._json({"statusCode": 0, "status_code": 0, "hasMore": mas, "cacheSessionId": "", "videoList": videos, "seoBizItemInfoList": tdks})
                n += len(videos)
            return self._json({"statusCode": 0, "status_code": 0, "hasMore": False, "videoList": [], "seoBizItemInfoList": []})
        if u.path.endswith("/related_keywords/"):
            lista = KAP_RELACIONADAS.get(k)
            if lista is None:
                return self._json({"log_pb": {}, "status_code": 10003, "status_msg": ""})
            return self._json({"statusCode": 0, "status_code": 0, "relatedKeywords": [{"formattedWord": w, "uniqueWord": w.lower().replace(" ", "-"), "pageType": 7} for w in lista]})
        self._enviar(404, b"no", "text/plain")


# ------------------------------------------------------------------ videos de mentira y descargador falso
def hacer_mp4(ruta: Path, ancho: int, alto: int, segundos: int) -> None:
    r = subprocess.run([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={ancho}x{alto}:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(segundos), "-pix_fmt", "yuv420p", "-c:v", "libx264",
                        "-preset", "ultrafast", "-b:v", "400k", "-c:a", "aac", str(ruta)], capture_output=True, text=True)
    if r.returncode != 0 or not ruta.is_file():
        print("ERROR: ffmpeg no pudo crear el video de prueba:", r.stderr[:200])
        sys.exit(1)


class DescargadorFalso:
    """Se comporta como DescargadorYtdlp, pero cada id tiene un destino fijo en COMPORTAMIENTO."""

    COMPORTAMIENTO: dict[str, str] = {}
    MATERIAL: dict[str, Path] = {}
    LLAMADAS: list[str] = []

    def __init__(self, carpeta: Path, codec: str):
        self.carpeta = carpeta

    def cerrar(self):
        pass

    def bajar(self, url, min_seg, max_seg):
        m = re.search(r"/video/(\d+)", url)
        vid = m.group(1) if m else url
        DescargadorFalso.LLAMADAS.append(vid)
        modo = self.COMPORTAMIENTO.get(vid, "vertical")
        if modo == "privado":
            return {"ok": False, "motivo": "el video es privado"}
        if modo == "bloqueo":
            return {"ok": False, "motivo": "TikTok bloqueo la descarga desde esta conexion (403/429): espera un rato y repite"}
        if modo == "corto":
            return {"ok": False, "id": vid, "motivo": "dura 2 s (se aceptan 4 a 60)", "saltar": True}
        destino = self.carpeta / f"{vid}.mp4"
        if modo == "roto":
            destino.write_bytes(os.urandom(60_000))
        else:
            shutil.copy2(self.MATERIAL["horizontal" if modo == "horizontal" else "vertical"], destino)
        return {"ok": True, "ruta": destino, "id": vid, "autor": "autor_" + vid[-2:], "duracion": 5.0, "ancho": 270, "alto": 480,
                "vistas": 1234, "likes": 56, "desc": "video de prueba con acentos: dinámica ñandú", "codec": "h264"}


# ------------------------------------------------------------------ pruebas
def pruebas_puras(b) -> None:
    print("Enlaces")
    e = b.interpretar_enlace("https://www.tiktok.com/@maestra_bel/video/9999000000000120085?is_from_webapp=1&sender_device=pc")
    comprobar(e == {"url": "https://www.tiktok.com/@maestra_bel/video/9999000000000120085", "autor": "maestra_bel", "id": "9999000000000120085"}, "un enlace con parametros se limpia", str(e))
    comprobar((b.interpretar_enlace("https://www.tiktok.com/@9999000000000133206/video/9999000000000142884") or {}).get("autor") == "9999000000000133206", "usuario solo con numeros")
    c = b.interpretar_enlace("https://vm.tiktok.com/ZMabc12345/")
    comprobar(c is not None and c["id"] is None, "enlace corto de vm.tiktok.com se acepta sin id")
    for malo in ("https://www.tiktok.com/@a/photo/9999000000000120085", "https://evil.com/@a/video/9999000000000120085",
                 "http://www.tiktok.com.evil.com/@a/video/9999000000000120085", "javascript:alert(1)", "file:///etc/passwd",
                 "https://www.tiktok.com/@a/video/12", "https://www.tiktok.com/@a", "", "no es un enlace"):
        comprobar(b.interpretar_enlace(malo) is None, f"se rechaza {malo[:50]!r}")
    cands, malos = b.leer_enlaces(["# comentario", "", "https://www.tiktok.com/@a/video/9999000000000150009  # el bueno",
                                   "https://www.tiktok.com/@a/video/9999000000000150009?x=1", "https://otra.com/x", "https://www.tiktok.com/@b/video/9999000000000150010"])
    comprobar([c["id"] for c in cands] == ["9999000000000150009", "9999000000000150010"] and len(malos) == 1, "leer_enlaces: comentarios, repetidos y ajenos", f"{[c['id'] for c in cands]} {malos}")

    print("Cuenta (pagina de insertar)")
    html = ('<!doctype html><html><body><script id="__FRONTITY_CONNECT_STATE__" type="application/json">'
            + json.dumps({"source": {"data": {"/embed/@pruebas": {"videoList": [
                {"id": "9999000000000165583", "desc": "uno", "height": 1280, "width": 720, "playCount": 1300000, "authorUniqueId": "pruebas", "privateItem": False, "playAddr": "https://x"},
                {"id": "9999000000000176031", "desc": "privado", "height": 1280, "width": 720, "playCount": 5, "authorUniqueId": "pruebas", "privateItem": True},
                {"id": "no-es-numero", "desc": "malo"}]}}}}) + "</script></body></html>")
    v = b.parsear_embed(html, "pruebas")
    comprobar(len(v) == 1 and v[0]["id"] == "9999000000000165583" and v[0]["url"].endswith("/@pruebas/video/9999000000000165583") and v[0]["vistas"] == 1300000,
              "parsear_embed: toma el video publico y deja el privado y el malo", str(v))
    for roto in ("<html></html>", '<script id="__FRONTITY_CONNECT_STATE__">{no json</script>', '<script id="__FRONTITY_CONNECT_STATE__">{"a":1}</script>'):
        try:
            b.parsear_embed(roto, "x")
            comprobar(False, "parsear_embed rechaza paginas sin lista")
        except b.ErrorFuente:
            comprobar(True, "parsear_embed rechaza paginas sin lista")

    print("Respuestas del proveedor")
    directo = b.extraer_awemes({"search_item_list": PAGINA1}, "consulta:x", "x")
    comprobar([c["id"] for c in directo] == [V1, V2, V3, V4, V5], "forma directa (search_item_list)", str([c["id"] for c in directo]))
    envuelto = b.extraer_awemes({"search_item_list": [{"aweme_info": p} for p in PAGINA2]}, "consulta:x", "x")
    comprobar([c["id"] for c in envuelto] == [V2, V6], "forma con aweme_info")
    top = b.extraer_awemes({"items": [{"id": V6, "desc": "top", "video": {"duration": 9000, "width": 576, "height": 1024}, "author": {"unique_id": "tej"}, "statistics": {"play_count": 3}}]}, "consulta:x", "x")
    comprobar(len(top) == 1 and top[0]["duracion"] == 9.0, "forma de search/top (id en vez de aweme_id)")
    c1 = directo[0]
    comprobar(abs(c1["duracion"] - 22.874) < 1e-6 and c1["likes"] == 85000 and c1["vistas"] == 1200000 and c1["autor"] == "ana_crochet"
              and c1["url"] == f"https://www.tiktok.com/@ana_crochet/video/{V1}", "campos del video (duracion de ms a s, likes, vistas, autor, enlace)", str(c1))
    comprobar(directo[2]["es_anuncio"] and directo[4]["es_foto"] and not directo[0]["es_foto"], "marca anuncios y fotos")

    print("Cola de descarga")
    def cand(i, **kw):
        return b.candidato(id=str(9999000000000180000 + i), url=f"https://www.tiktok.com/@a/video/{9999000000000180000 + i}", autor="a", **kw)
    g1 = [cand(1), cand(2), cand(3), cand(4)]
    g2 = [cand(5), cand(1)]
    cola, desc = b.armar_cola([g1, g2], {str(9999000000000180002)}, 4, 60, 10)
    ids = [int(c["id"]) - 9999000000000180000 for c in cola]
    comprobar(ids == [1, 5, 3, 4], "intercala una de cada fuente, sin repetidos ni lo ya tenido", str(ids))
    comprobar(desc.get("repetido") == 1 and desc.get("ya lo teniamos") == 1, "cuenta los descartes", str(desc))
    cola, desc = b.armar_cola([[cand(1, duracion=2), cand(2, duracion=90), cand(3, duracion=20), cand(4, duracion=20, ancho=1920, alto=1080),
                                cand(5, es_anuncio=True), cand(6, es_foto=True), cand(7, duracion=20, ancho=720, alto=1280)]], set(), 4, 60, 10)
    comprobar([int(c["id"]) - 9999000000000180000 for c in cola] == [3, 7], "descarta corto, largo, horizontal, anuncio y foto", str(desc))
    cola, _ = b.armar_cola([[cand(i) for i in range(1, 9)]], set(), 4, 60, 3)
    comprobar(len(cola) == 3, "un tope por fuente")
    cola, desc = b.armar_cola([[cand(1, desc="Din" + chr(0xE1) + "mica de MUJERES en la iglesia"), cand(2, desc="ondas super naturales"), cand(3, desc=""),
                                cand(4, desc="otra cosa", fuente="enlace"), cand(5, desc="un juego divertido")]], set(), 4, 60, 10, ["dinamica", "JUEGO"])
    comprobar([int(c["id"]) - 9999000000000180000 for c in cola] == [1, 4, 5] and desc.get("no habla del tema") == 2,
              "con filtro de palabras solo pasan los videos que hablan del tema (sin tildes ni mayusculas); un enlace dado a mano pasa siempre", str(desc))
    cola, _ = b.armar_cola([[cand(1, desc="nada que ver")]], set(), 4, 60, 10, [])
    comprobar(len(cola) == 1, "sin filtro no se descarta por tema")
    # la cuenta del propio anunciante original NO se usa como b-roll (su material promocional)
    comprobar(b.clave_cuenta("MoleQue.Arteiro_") == "molequearteiro" and b.clave_cuenta("Moleque Arteiro") == "molequearteiro" and b.clave_cuenta(None) == "" and b.clave_cuenta("Ñandú 2") == "nandu2",
              "clave_cuenta: minusculas, sin tildes y solo letras y numeros")

    def de(autor, fuente="consulta:x", i=1):
        return b.candidato(id=str(9999000000000180000 + i), url=f"https://www.tiktok.com/@{autor}/video/{9999000000000180000 + i}", autor=autor, fuente=fuente, duracion=20, ancho=720, alto=1280)

    casos = [("molequearteiro", True), ("moleque_arteiro", True), ("MoleQue.Arteiro", True), ("molequearteiro.oficial", True), ("oficial_molequearteiro", True), ("molequearte", True),
             ("arteiro", False), ("moleque", False), ("yunesans", False), ("tejedora", False)]
    mal = [(a, esperado) for a, esperado in casos if b.es_de_la_cuenta(de(a), ["Moleque Arteiro"]) != esperado]
    comprobar(not mal, "es_de_la_cuenta: la misma cuenta (con separadores, mayusculas, «oficial» delante o detras o un recorte de 8 letras o mas que cubre el 70 %) si; una palabra suelta del nombre u otra cuenta no", str(mal))
    comprobar(not b.es_de_la_cuenta(de("fernanda"), ["Dicas da Fernanda"]) and b.es_de_la_cuenta(de("dicasdafernanda"), ["Dicas da Fernanda"]), "un nombre de pila que solo esta DENTRO del nombre del anunciante no cuenta (fernanda vs dicasdafernanda)")
    comprobar(not b.es_de_la_cuenta(de("ana_crochet"), ["Ana"]) and not b.es_de_la_cuenta(de("fiesta_ana"), ["Ana"]) and not b.es_de_la_cuenta(de("ana_crochet"), ["", "  ", "x"]) and not b.es_de_la_cuenta(de("ana_crochet"), []) and not b.es_de_la_cuenta(de(None), ["Ana Crochet"]),
              "un nombre de menos de 5 letras, vacio o sin lista no descarta a nadie, y un video sin autor tampoco")
    comprobar(not b.es_de_la_cuenta(de("ana_crochet", fuente="enlace"), ["Ana Crochet"]) and not b.es_de_la_cuenta(de("ana_crochet", fuente="cuenta:@ana_crochet"), ["Ana Crochet"]) and b.es_de_la_cuenta(de("ana_crochet", fuente="consulta:crochet"), ["Ana Crochet"]),
              "lo que la persona puso a mano (un enlace o una cuenta de la lista) pasa siempre; lo que salio de una busqueda por palabra, no")
    cola, desc = b.armar_cola([[de("ana_crochet", i=1), de("luz.manos", i=2), de("Ana_Crochet", "enlace", i=3), de("ana_crochet", "cuenta:@ana_crochet", i=4), de("moleque_arteiro_oficial", i=5), de("arteiro", i=6)]],
                              set(), 4, 60, 10, [], ["Ana Crochet", "Moleque Arteiro"])
    comprobar([int(c["id"]) - 9999000000000180000 for c in cola] == [2, 3, 4, 6] and desc.get(b.MOTIVO_CUENTA_ANUNCIANTE) == 2, "armar_cola con excluir_autores: sale lo de las cuentas de los anunciantes (2) y lo demas pasa; las cuentas dadas a mano tambien", str(desc))
    cola, desc = b.armar_cola([[de("ana_crochet", i=1)]], set(), 4, 60, 10)
    comprobar(len(cola) == 1 and not desc, "sin la lista (como antes) no se descarta a nadie")
    comprobar(b._celda_segura("=SUM(A1)") == "'=SUM(A1)" and b._celda_segura("@x") == "'@x" and b._celda_segura("-1+1") == "'-1+1" and b._celda_segura("+54") == "'+54"
              and b._celda_segura("normal") == "normal" and b._celda_segura("'ya") == "'ya" and b._celda_segura(None) == "", "las celdas de texto que empiezan con = + - @ se guardan con apostrofo")

    print("Eleccion de formato")
    formatos = [
        {"format_id": "audio", "vcodec": "none", "format_note": ""},
        {"format_id": "download", "vcodec": "h264", "format_note": "Untested, watermarked", "height": 1920, "tbr": 9999},
        {"format_id": "h264_540p", "vcodec": "h264", "height": 1024, "tbr": 562},
        {"format_id": "bytevc1_720p", "vcodec": "h265", "height": 1280, "tbr": 629},
        {"format_id": "bytevc1_1080p", "vcodec": "h265", "height": 1920, "tbr": 1469},
    ]
    elegido = list(b._selector_formato("mejor")({"formats": formatos}))
    comprobar(len(elegido) == 1 and elegido[0]["format_id"] == "bytevc1_1080p", "mejor: la mayor resolucion, nunca la de marca de agua")
    elegido = list(b._selector_formato("h264")({"formats": formatos}))
    comprobar(len(elegido) == 1 and elegido[0]["format_id"] == "h264_540p", "h264: solo H.264")
    comprobar(list(b._selector_formato("mejor")({"formats": [formatos[1]]})) == [], "si solo hay marca de agua no se baja nada")
    comprobar(b._codec_corto("h265") == "hevc" and b._codec_corto("avc1.64001F") == "h264" and b._codec_corto(None) == "", "nombres de codec")

    print("fuentes.csv")
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "broll"
        filas = {"1": {"id": "1", "archivo": "tt_1.mp4", "estado": "bajado", "descripcion": "dinámica, con \"comillas\" y ñ", "descargado_en": "2026-10-19T10:00:00"},
                 "2": {"id": "2", "estado": "descartado", "motivo": "es horizontal", "descargado_en": "2026-10-19T09:00:00"}}
        b.escribir_fuentes(d, filas)
        crudo = (d / "fuentes.csv").read_bytes()
        comprobar(crudo.startswith(b"\xef\xbb\xbf"), "el csv lleva BOM (Excel y Sheets lo leen con acentos)")
        leidas = b.leer_fuentes(d)
        comprobar(leidas["1"]["descripcion"] == filas["1"]["descripcion"] and leidas["2"]["motivo"] == "es horizontal", "ida y vuelta con acentos, comas y comillas")
        (d / "tt_9999000000000190009.mp4").write_bytes(b"x")
        comprobar(b.ids_tenidos(d) == {"1", "2", "9999000000000190009"}, "ids tenidos = csv + archivos tt_<id>")
        comprobar(b.ids_tenidos(None, Path(t) / "no-existe") == set(), "carpetas inexistentes no dan error")


def pruebas_proveedor(b) -> None:
    print("Proveedor de busqueda (falso)")
    VISTO.clear()
    MODO.update(estado=200, pendientes_429=0)
    p = b.Proveedor(LLAVE, 5)
    r = p.buscar("crochet", 2, "CO", "relevancia", "todo")
    comprobar([c["id"] for c in r] == [V1, V2, V3, V4, V5, V2, V6], "dos paginas siguiendo el cursor", str([c["id"] for c in r]))
    rutas = [(u, q) for u, q in VISTO if u.endswith("/keyword")]
    comprobar(len(rutas) == 2 and "cursor" not in rutas[0][1] and rutas[1][1].get("cursor") == "12", "la segunda pagina pide el cursor que dio la primera", str(rutas))
    comprobar(rutas[0][1].get("query") == "crochet" and rutas[0][1].get("region") == "CO" and rutas[0][1].get("sort_by") == "relevance" and rutas[0][1].get("date_posted") == "all-time",
              "manda consulta, region, orden y periodo", str(rutas[0][1]))
    comprobar(p.gastados == 2 and p.quedan == 96, "cuenta los pedidos y guarda los creditos que quedan", f"{p.gastados} {p.quedan}")
    h = p.hashtag("#crochet", 1, "")
    comprobar([c["id"] for c in h] == [V7] and h[0]["consulta"] == "#crochet" and "region" not in [q for u, q in VISTO if u.endswith("/hashtag")][0], "hashtag (sin region si no se pide)")
    p2 = b.Proveedor(LLAVE, 1)
    p2.pedir("/v1/tiktok/search/hashtag", {"hashtag": "x"})
    try:
        p2.pedir("/v1/tiktok/search/hashtag", {"hashtag": "x"})
        comprobar(False, "respeta el tope de pedidos")
    except b.ErrorFuente as e:
        comprobar("tope" in str(e), "respeta el tope de pedidos")
    for estado, trozo in ((402, "creditos"), (403, "rechazo la llave")):
        MODO["estado"] = estado
        try:
            b.Proveedor(LLAVE, 3).pedir("/v1/tiktok/search/keyword", {"query": "x"})
            comprobar(False, f"error {estado} explicado")
        except b.ErrorFuente as e:
            comprobar(trozo in str(e), f"error {estado} explicado en palabras", str(e))
    MODO["estado"] = 200
    try:
        b.Proveedor("llave-equivocada", 3).pedir("/v1/tiktok/search/keyword", {"query": "x"})
        comprobar(False, "llave mala (401)")
    except b.ErrorFuente as e:
        comprobar("rechazo la llave" in str(e) and "llave-equivocada" not in str(e), "llave mala (401): se explica y no se imprime la llave", str(e))
    MODO["estado"] = 200
    MODO["sin_exito"] = True
    gastados_antes = b.Proveedor(LLAVE, 3)
    try:
        gastados_antes.pedir("/v1/tiktok/search/keyword", {"query": "x"})
        comprobar(False, "un 200 con success=false se explica")
    except b.ErrorFuente as e:
        comprobar("no pudo" in str(e) and "cuota de prueba" in str(e) and gastados_antes.gastados == 0, "un 200 con success=false se explica y no cuenta como pedido gastado", str(e))
    MODO["sin_exito"] = False
    MODO["pendientes_429"] = 1
    b.time.sleep = lambda s: None
    ok = b.Proveedor(LLAVE, 3).pedir("/v1/tiktok/search/keyword", {"query": "x"})
    comprobar(bool(ok.get("success")), "un 429 se reintenta y sale bien")


def pruebas_descubrir(b) -> None:
    print("Lista publica de TikTok (falsa): palabras, paginas y errores")
    b.Descubrir.PAUSA = (0.0, 0.0)
    b.time.sleep = lambda s: None
    comprobar(b.slug_de("Din\u00e1micas para mujeres!") == "dinamicas-para-mujeres" and b.slug_de("#Crochet  Amigurumi") == "crochet-amigurumi"
              and b.slug_de("women's group") == "womens-group" and b.slug_de("  ...  ") == "" and len(b.slug_de("a " * 100)) <= 80,
              "slug_de: tildes, signos, apostrofes, hashtags y vacios", b.slug_de("Din\u00e1micas para mujeres!"))
    VISTO_TT.clear()
    d = b.Descubrir(20)
    r = d.buscar("Crochet", 3)
    comprobar([c["id"] for c in r] == [K1, K2, K3, K4, K5, K6], "dos paginas siguiendo offset/hasMore, sin repetir el video que sale en las dos", str([c["id"] for c in r]))
    pedidos = [(u, q) for u, q, h in VISTO_TT]
    comprobar(pedidos[0][0] == "/discover" and [u for u, q in pedidos[1:]] == ["/api/seo/kap/video_list/", "/api/seo/kap/video_list/"] and d.gastados == 3,
              "pide la pagina de visitante una vez y luego la lista (2 paginas): 3 pedidos en total", str(pedidos))
    lista1, lista2 = pedidos[1][1], pedidos[2][1]
    comprobar(lista1["keyword"] == "crochet" and lista1["count"] == "16" and lista1["offset"] == "0" and lista1["pageType"] == "7" and lista2["offset"] == "5"
              and lista1["device_id"] == "9999000000000110001" and lista1["user_is_login"] == "false" and "msToken" not in lista1 and "X-Bogus" not in lista1,
              "la segunda pagina sigue por donde iba (offset=5); usa el identificador de visitante que dio TikTok; no manda ninguna firma ni sesion", str(lista1) + str(lista2))
    h = VISTO_TT[1][2]
    comprobar("broll-editor" in h.get("user-agent", "") and "ttwid=visitante123" in h.get("cookie", "") and h.get("referer", "").endswith("/discover/crochet"),
              "se identifica con su propio User-Agent, devuelve las cookies de visitante y manda el Referer de la pagina de la palabra", str(h))
    c1, c2, c3, c4, c5 = r[0], r[1], r[2], r[3], r[4]
    comprobar(c1["url"] == f"https://www.tiktok.com/@ana_crochet/video/{K1}" and c1["autor"] == "ana_crochet" and c1["vistas"] == 1200000 and c1["likes"] == 85000
              and c1["duracion"] == 22.0 and (c1["ancho"], c1["alto"]) == (576, 1024) and c1["fuente"] == "consulta:Crochet" and not c1["es_anuncio"] and not c1["es_foto"],
              "el video se lee completo: enlace, autor, cifras, duracion, medidas y de que busqueda salio", str(c1))
    comprobar("Amigurumi paso a paso" in c1["tema"] and "amigurumi" in c1["tema"] and c2["tema"] == "Tejer sin parar" and r[5]["tema"] == "Puntada baja para principiantes",
              "el titulo y la descripcion que TikTok le pone al video quedan en «tema»", str([c["tema"] for c in r]))
    comprobar(c3["es_foto"] is True and c4["ancho"] == 1920 and c5["duracion"] == 300.0, "las fotos y lo horizontal o muy largo se ven desde el candidato (la cola los descarta)")
    cola, descartes = b.armar_cola([r], set(), 4, 120, 10, [])
    comprobar([c["id"] for c in cola] == [K1, K2, K6] and descartes == {"es una foto, no un video": 1, "no es vertical": 1, "duracion fuera de rango": 1},
              "la cola deja solo los verticales de 4 a 120 s y que son video", str(descartes) + str([c["id"] for c in cola]))
    cola2, _ = b.armar_cola([r], set(), 4, 120, 10, ["amigurumi", "puntada"])
    comprobar([c["id"] for c in cola2] == [K1, K6], "el filtro de tema tambien mira el titulo de TikTok (el K6 no dice 'puntada' en su descripcion, si en su titulo)", str([c["id"] for c in cola2]))
    n = d.gastados
    comprobar(d.buscar("palabra que no existe", 2) == [] and "palabra-que-no-existe" in d.sin_lista and d.gastados == n + 1, "una palabra que TikTok no tiene da [] (no es un error) y cuesta 1 pedido")
    d.buscar("palabra que no existe", 2)
    comprobar(d.gastados == n + 1, "y no se vuelve a pedir en la misma corrida")
    try:
        d.buscar("rara", 1)
        comprobar(False, "un codigo que no conocemos para la lista se explica")
    except b.ErrorFuente as e:
        comprobar("10000" in str(e) and "algo que no conocemos" in str(e), "un codigo que no conocemos para la lista se explica y se para", str(e))
    try:
        b.Descubrir(20).buscar("vacia", 1)
        comprobar(False, "una respuesta vacia se explica")
    except b.ErrorFuente as e:
        comprobar("no devolvio la lista" in str(e) and "no se insiste" in str(e), "una respuesta vacia (la forma en que TikTok bloquea) se explica y NO se insiste", str(e))
    try:
        b.Descubrir(20).buscar("...", 1)
        comprobar(False, "una consulta sin letras se explica")
    except b.ErrorFuente as e:
        comprobar("no tiene letras" in str(e), "una consulta sin letras se explica")
    chico = b.Descubrir(2)
    chico.buscar("crochet", 1)
    try:
        chico._pedir("video_list", "crochet", count="16", offset="0", pageType="7")
        comprobar(False, "respeta el tope de pedidos")
    except b.ErrorFuente as e:
        comprobar("tope de 2 pedidos a TikTok" in str(e), "respeta el tope de pedidos (la pagina de visitante tambien cuenta)", str(e))
    parcial = io.StringIO()
    with contextlib.redirect_stdout(parcial):
        medio = b.Descubrir(2).buscar("crochet", 3)       # pagina de visitante + 1 lista = 2 pedidos: la 2.a pagina ya no cabe
    comprobar([c["id"] for c in medio] == [K1, K2, K3, K4, K5] and "se usan los 5 videos de las paginas anteriores" in parcial.getvalue(),
              "si el tope llega a mitad de camino se quedan los videos de las paginas que ya se tenian y se avisa", parcial.getvalue())
    MODO_TT["pendientes_429"] = 1
    comprobar(len(b.Descubrir(20).buscar("crochet", 1)) == 5, "un 429 se espera y se reintenta una vez")
    MODO_TT["siempre_429"] = True
    try:
        b.Descubrir(20).buscar("crochet", 1)
        comprobar(False, "si TikTok siempre pide esperar, se explica")
    except b.ErrorFuenteEspera as e:
        comprobar("esperar" in str(e), "si TikTok pide esperar a cada rato (429) lo dice como «espera» y no insiste", str(e))
    MODO_TT["siempre_429"] = False
    MODO_TT["sin_wid"] = True
    try:
        b.Descubrir(20).buscar("crochet", 1)
        comprobar(False, "una pagina de visitante sin identificador se explica")
    except b.ErrorFuente as e:
        comprobar("no trae lo que se esperaba" in str(e) and "no se insiste" in str(e), "si la pagina de visitante no trae lo esperado (cambio, o pide comprobar que no eres un robot) se explica y no se insiste", str(e))
    MODO_TT["sin_wid"] = False
    comprobar(b.Descubrir(20).relacionadas("crochet") == ["crochet para principiantes", "dinamica con globos", "bolsos de crochet", "manualidades navidenas"]
              and b.Descubrir(20).relacionadas("palabra que no existe") == [], "relacionadas: las que da TikTok sin repetir la misma palabra; [] si la palabra no tiene lista")
    comprobar(b.relacionada_del_tema("dinamicas para reuniones de mujeres", "dinamica para reunion de mujeres") and not b.relacionada_del_tema("dinamicas para reuniones de mujeres", "invitaciones para bodas")
              and b.relacionada_del_tema("crochet", "crochet para principiantes") and not b.relacionada_del_tema("crochet", "manualidades navidenas"),
              "una palabra relacionada solo se sigue si habla de lo mismo (comparte raices con la consulta)")
    g = b.Descubrir(20).hashtag("#Crochet", 1)
    comprobar(g and g[0]["fuente"] == "hashtag:Crochet" and g[0]["consulta"] == "#Crochet", "un hashtag se busca como palabra y queda anotado como hashtag")
    comprobar(isinstance(b.crear_proveedor(5), b.Descubrir) and isinstance(b.crear_proveedor(5, "DESCUBRIR"), b.Descubrir), "el proveedor normal es la lista publica (gratis)")
    try:
        b.crear_proveedor(5, "inventado")
        comprobar(False, "proveedor desconocido")
    except b.ErrorFuente as e:
        comprobar("desconocido" in str(e), "un proveedor que no existe se explica")


def correr_traer(b, argumentos: list[str]) -> tuple[int, str]:
    a = b.construir_parser().parse_args(["traer", *argumentos])
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
        try:
            codigo = b.traer(a, fabrica=DescargadorFalso)
        except SystemExit as e:
            codigo = int(e.code or 0)
    return codigo, salida.getvalue()


def correr_traer_con(b, argumentos: list[str], fabrica) -> tuple[int, str]:
    a = b.construir_parser().parse_args(["traer", *argumentos])
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
        try:
            codigo = b.traer(a, fabrica=fabrica)
        except SystemExit as e:
            codigo = int(e.code or 0)
    return codigo, salida.getvalue()


def pruebas_traer(b, tmp: Path) -> None:
    print("Traer: punta a punta con videos de mentira")
    material = tmp / "material"
    material.mkdir()
    hacer_mp4(material / "vertical.mp4", 270, 480, 5)
    hacer_mp4(material / "horizontal.mp4", 480, 270, 5)
    DescargadorFalso.MATERIAL = {"vertical": material / "vertical.mp4", "horizontal": material / "horizontal.mp4"}
    (tmp / "temp").mkdir()
    tempfile.tempdir = str(tmp / "temp")       # asi se ve si traer() deja carpetas temporales sin borrar
    b.PUBLIC = tmp / "public"
    b.RAIZ = tmp                               # aqui no hay .env: la autoprueba nunca lee la llave de verdad
    b.time.sleep = lambda s: None
    for n in ("prueba", "prueba2", "prueba3", "prueba4"):
        (b.PUBLIC / "productos" / n).mkdir(parents=True)
    # (prueba5 la crea la prueba de recoger)
    drive = tmp / "drive" / "OFERTA"
    drive.mkdir(parents=True)
    local = b.PUBLIC / "productos" / "prueba" / "broll-tiktok"
    en_drive = drive / "B-rolls"
    ids = [f"999900000200000{n:04d}" for n in range(11, 18)]       # 7 videos
    ok1, ok2, horizontal, privado, bloqueo, corto, roto = ids
    DescargadorFalso.COMPORTAMIENTO = {horizontal: "horizontal", privado: "privado", bloqueo: "bloqueo", corto: "corto", roto: "roto"}
    enlaces = []
    for i in ids:
        enlaces += ["--enlace", f"https://www.tiktok.com/@x/video/{i}"]

    codigo, txt = correr_traer(b, ["prueba", *enlaces, "--drive", str(drive), "--max", "5"])
    comprobar(sorted(p.name for p in local.glob("*.mp4")) == [f"tt_{ok1}.mp4", f"tt_{ok2}.mp4"], "baja los dos videos buenos al editor", txt[-400:])
    comprobar(sorted(p.name for p in en_drive.glob("*.mp4")) == [f"tt_{ok1}.mp4", f"tt_{ok2}.mp4"], "y los copia a Drive/B-rolls")
    comprobar((en_drive / "LEEME.txt").is_file(), "B-rolls trae su LEEME con el aviso de derechos")
    filas = b.leer_fuentes(local)
    estados = {i: filas[i]["estado"] for i in filas}
    comprobar(estados.get(ok1) == "bajado" and estados.get(horizontal) == "descartado" and estados.get(privado) == "descartado"
              and estados.get(roto) == "descartado" and bloqueo not in estados and corto not in estados,
              "anota los buenos y los descartados; lo que fallo por la conexion o por la duracion NO se anota", str(estados))
    comprobar("horizontal" in filas[horizontal]["motivo"] and "no se abre" in filas[roto]["motivo"] and "privado" in filas[privado]["motivo"], "cada descarte dice por que", str({k: v["motivo"] for k, v in filas.items()}))
    comprobar("salta" in txt and "dura 2 s" in txt, "lo que se salta por la duracion se dice en pantalla")
    comprobar(filas[ok1]["en_drive"] == "si" and filas[ok1]["codec"] == "h264" and "dinámica ñandú" in filas[ok1]["descripcion"] and str(filas[ok1]["ancho"]) == "270",
              "la fila trae codec, descripcion con acentos y en_drive=si", str(filas[ok1]))
    comprobar(b.leer_fuentes(en_drive).keys() == filas.keys(), "fuentes.csv es igual en el editor y en Drive")
    comprobar(not list((tmp / "temp").glob("broll_*")) and codigo == 0, "sin carpetas temporales sobrantes y salida 0", f"codigo {codigo}")

    DescargadorFalso.LLAMADAS.clear()
    codigo, txt = correr_traer(b, ["prueba", *enlaces, "--drive", str(drive)])
    comprobar(sorted(DescargadorFalso.LLAMADAS) == sorted([corto, bloqueo]), "segunda corrida: solo vuelve a mirar lo que fallo por la conexion o se salto por la duracion", str(DescargadorFalso.LLAMADAS))
    comprobar(sorted(p.name for p in local.glob("*.mp4")) == [f"tt_{ok1}.mp4", f"tt_{ok2}.mp4"], "no baja dos veces lo mismo")
    DescargadorFalso.COMPORTAMIENTO[bloqueo] = "vertical"
    codigo, txt = correr_traer(b, ["prueba", *enlaces, "--drive", str(drive)])
    comprobar((local / f"tt_{bloqueo}.mp4").is_file() and (en_drive / f"tt_{bloqueo}.mp4").is_file(), "cuando la conexion vuelve, baja el que faltaba")

    # Alguien borra un video malo de Drive: no debe volver a bajarse.
    (en_drive / f"tt_{ok2}.mp4").unlink()
    (local / f"tt_{ok2}.mp4").unlink()
    DescargadorFalso.LLAMADAS.clear()
    correr_traer(b, ["prueba", *enlaces, "--drive", str(drive)])
    comprobar(ok2 not in DescargadorFalso.LLAMADAS and not (local / f"tt_{ok2}.mp4").exists(), "lo que el equipo borro no vuelve a bajarse")

    print("Traer: si el descargador no se puede crear, no queda basura")

    def fabrica_rota(carpeta, codec):
        raise SystemExit(1)
    antes = set((tmp / "temp").glob("broll_*"))
    correr_traer_con(b, ["prueba2", "--enlace", "https://www.tiktok.com/@x/video/9999000000000096666", "--sin-drive"], fabrica_rota)
    comprobar(set((tmp / "temp").glob("broll_*")) == antes, "falta yt-dlp: no queda la carpeta temporal")

    print("Traer: Drive que no esta montado y se repara despues")
    nuevo = "9999000000000099999"
    codigo, txt = correr_traer(b, ["prueba2", "--enlace", f"https://www.tiktok.com/@x/video/{nuevo}", "--drive", str(tmp / "no-hay-drive")])
    f2 = b.leer_fuentes(b.PUBLIC / "productos" / "prueba2" / "broll-tiktok")
    comprobar("AVISO" in txt and f2[nuevo]["en_drive"] == "no" and (b.PUBLIC / "productos" / "prueba2" / "broll-tiktok" / f"tt_{nuevo}.mp4").is_file(),
              "sin Drive avisa, baja igual y anota en_drive=no", txt[-300:])
    drive2 = tmp / "drive" / "OFERTA2"
    drive2.mkdir()
    otro = "9999000000000098888"
    correr_traer(b, ["prueba2", "--enlace", f"https://www.tiktok.com/@x/video/{otro}", "--drive", str(drive2)])
    comprobar((drive2 / "B-rolls" / f"tt_{nuevo}.mp4").is_file() and (drive2 / "B-rolls" / f"tt_{otro}.mp4").is_file(), "con Drive de vuelta copia tambien el que habia quedado pendiente")
    comprobar(b.leer_fuentes(b.PUBLIC / "productos" / "prueba2" / "broll-tiktok")[nuevo]["en_drive"] == "si", "y lo anota como copiado")
    codigo, txt = correr_traer(b, ["prueba2", "--enlace", f"https://www.tiktok.com/@x/video/{otro}", "--drive", "https://drive.google.com/drive/folders/abc"])
    comprobar("RUTA de la carpeta" in txt, "un enlace de Drive en vez de la ruta se explica")

    print("Traer: --simular no escribe nada")
    antes = sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*"))
    codigo, txt = correr_traer(b, ["prueba3", "--enlace", "https://www.tiktok.com/@x/video/9999000000000097777", "--drive", str(drive), "--simular"])
    despues = sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*"))
    comprobar(antes == despues and "SIMULACION" in txt, "la simulacion lista la cola y no toca ningun archivo", txt[-200:])

    print("Traer: por el proveedor de PAGO (ScrapeCreators) y broll.json")
    VISTO.clear()
    MODO.update(estado=200, pendientes_429=0)
    DescargadorFalso.COMPORTAMIENTO = {}
    os.environ["SCRAPECREATORS_API_KEY"] = LLAVE
    cfg = {"consultas": ["crochet"], "hashtags": ["crochet"], "max": 10, "paginas": 2, "min_seg": 4, "max_seg": 60, "proveedor": "scrapecreators"}
    (b.PUBLIC / "productos" / "prueba3" / "broll.json").write_text(json.dumps(cfg), encoding="utf-8")
    codigo, txt = correr_traer(b, ["prueba3", "--sin-drive"])
    l3 = b.PUBLIC / "productos" / "prueba3" / "broll-tiktok"
    bajados = sorted(p.stem.replace("tt_", "") for p in l3.glob("*.mp4"))
    comprobar(bajados == sorted([V1, V2, V6, V7]), "busca por consulta y hashtag de broll.json, sin anuncios, fotos, horizontales ni repetidos", str(bajados) + txt[-500:])
    comprobar(LLAVE not in txt, "la llave nunca sale en pantalla")
    comprobar("le quedan" in txt and "credito" in txt, "dice cuantos creditos le quedan al proveedor")
    VISTO.clear()
    correr_traer(b, ["prueba3", "--cuenta", "no_existe_ninguna", "--sin-drive", "--simular"])
    comprobar(not VISTO, "si pasas --cuenta a mano no se mezclan las consultas de broll.json (no gasta creditos)")
    codigo, txt = correr_traer(b, ["prueba4", "--consulta", "crochet", "--proveedor", "scrapecreators", "--filtro-desc", "TEJIENDO", "--sin-drive", "--simular"])
    comprobar(V1 in txt and V2 in txt and V6 not in txt and "no habla del tema" in txt, "--filtro-desc deja solo los videos cuya descripcion habla del tema", txt[-500:])
    codigo, txt = correr_traer(b, ["prueba4", "--consulta", "crochet", "--proveedor", "scrapecreators", "--excluir-autor", "Ana Crochet", "--sin-drive", "--simular"])
    comprobar(V1 not in txt and V2 in txt and "1 de la cuenta del anunciante original" in txt, "--excluir-autor: no baja el video de la cuenta del anunciante (V1, de ana_crochet) y lo dice; los demas siguen", txt[-600:])
    (b.PUBLIC / "productos" / "prueba4" / "broll.json").write_text(json.dumps({"excluir_autores": ["ana crochet"]}), encoding="utf-8")
    try:
        codigo, txt = correr_traer(b, ["prueba4", "--consulta", "crochet", "--proveedor", "scrapecreators", "--sin-drive", "--simular"])
    finally:
        (b.PUBLIC / "productos" / "prueba4" / "broll.json").unlink()
    comprobar(V1 not in txt and V2 in txt and "de la cuenta del anunciante original" in txt, "'excluir_autores' de broll.json tambien vale, aunque las fuentes se den por la linea de comandos (es una regla de derechos)", txt[-600:])
    resumen_ex: dict = {}
    (b.PUBLIC / "productos" / "prueba_ex").mkdir(parents=True, exist_ok=True)
    a_ex = b.construir_parser().parse_args(["traer", "prueba_ex", "--consulta", "crochet", "--proveedor", "scrapecreators", "--excluir-autor", "Ana Crochet", "--sin-drive", "--max", "2"])
    with contextlib.redirect_stdout(io.StringIO()):
        b.traer(a_ex, fabrica=DescargadorFalso, resumen=resumen_ex)
    comprobar(resumen_ex.get("de_la_cuenta_del_anunciante") == 1 and V1 not in {p.stem.replace("tt_", "") for p in (b.PUBLIC / "productos" / "prueba_ex" / "broll-tiktok").glob("*.mp4")},
              "traer() cuenta en su resumen los videos que dejo de lado por ser de la cuenta del anunciante, y no bajo ese", str(resumen_ex))
    resumen: dict = {}
    a = b.construir_parser().parse_args(["traer", "prueba4", "--consulta", "crochet", "--proveedor", "scrapecreators", "--sin-drive", "--max", "2"])
    with contextlib.redirect_stdout(io.StringIO()):
        b.traer(a, fabrica=DescargadorFalso, resumen=resumen)
    comprobar(resumen.get("bajados") == 2 and resumen.get("creditos") == 2 and resumen.get("cortada_por_red") is False and resumen.get("fallos") == 0 and resumen.get("creditos_quedan") == 96,
              "traer() deja un resumen (bajados, creditos, fallos, cortada_por_red) para quien lo llame desde otro programa", str(resumen))
    ab_bloqueo = {i: "bloqueo" for i in (V1, V2, V6)}
    DescargadorFalso.COMPORTAMIENTO = ab_bloqueo
    resumen = {}
    a = b.construir_parser().parse_args(["traer", "prueba2", "--consulta", "crochet", "--proveedor", "scrapecreators", "--sin-drive", "--max", "9", "--paginas", "2"])
    with contextlib.redirect_stdout(io.StringIO()):
        b.traer(a, fabrica=DescargadorFalso, resumen=resumen)
    comprobar(resumen.get("cortada_por_red") is True and resumen.get("bajados") == 0, "tres fallos seguidos por la conexion: el resumen dice cortada_por_red", str(resumen))
    DescargadorFalso.COMPORTAMIENTO = {}
    # el comando proveedor: una busqueda de prueba que dice si la llave sirve
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        cod_ok = b.probar_proveedor("scrapecreators")
    comprobar(cod_ok == 0 and "sirve" in salida.getvalue() and LLAVE not in salida.getvalue(), "proveedor: con llave buena dice que sirve y cuantos creditos quedan, sin imprimirla", salida.getvalue())
    MODO["estado"] = 403
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        cod_mal = b.probar_proveedor("scrapecreators")
    MODO["estado"] = 200
    comprobar(cod_mal == 2 and "NO sirve" in salida.getvalue(), "proveedor: con llave rechazada dice que no sirve", salida.getvalue())
    os.environ.pop("SCRAPECREATORS_API_KEY")
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        cod_sin = b.probar_proveedor("scrapecreators")
    comprobar(cod_sin == 2 and "falta la llave" in salida.getvalue(), "proveedor: sin llave explica que falta")
    os.environ["SCRAPECREATORS_API_KEY"] = LLAVE
    os.environ.pop("SCRAPECREATORS_API_KEY")
    (b.PUBLIC / "productos" / "prueba3" / "broll-tiktok" / "fuentes.csv").unlink()
    codigo, txt = correr_traer(b, ["prueba3", "--consulta", "otra cosa", "--proveedor", "scrapecreators", "--sin-drive"])
    comprobar("falta la llave" in txt and "SCRAPECREATORS_API_KEY" in txt and codigo == 2, "sin llave explica que hacer y no se cae", txt[-300:])
    codigo, txt = correr_traer(b, ["prueba3", "--drive", str(drive)])
    comprobar(codigo == 2 and "falta la llave" in txt and "Nada nuevo" in txt, "con broll.json y sin llave avisa y no se cae", txt[-300:])
    print("Traer: busqueda GRATIS por la lista publica de TikTok (lo normal)")
    VISTO_TT.clear()
    DescargadorFalso.COMPORTAMIENTO = {}
    DescargadorFalso.LLAMADAS.clear()
    for n_ in ("prueba6", "prueba7"):
        (b.PUBLIC / "productos" / n_).mkdir(parents=True)
    codigo, txt = correr_traer(b, ["prueba6", "--consulta", "crochet", "--consulta", "algo que no existe", "--relacionadas", "2", "--sin-drive", "--max", "9", "--paginas", "2"])
    l6 = b.PUBLIC / "productos" / "prueba6" / "broll-tiktok"
    bajados6 = sorted(p.stem.replace("tt_", "") for p in l6.glob("*.mp4"))
    comprobar(bajados6 == sorted([K1, K2, K6, K7, K8]) and codigo == 0, "sin llave ni nada: busca por la lista publica, sigue las palabras relacionadas que hablan de lo mismo y baja los videos buenos", f"{bajados6}\n{txt[-900:]}")
    comprobar("Busqueda \"crochet\": 6 video(s)" in txt and "Busqueda \"algo que no existe\": 0 video(s) (TikTok no tiene lista publica para esa palabra)" in txt
              and "Relacionada \"crochet para principiantes\" (de \"crochet\"): 2 video(s)" in txt and "Relacionada \"bolsos de crochet\" (de \"crochet\"): 0 video(s) (TikTok no tiene" in txt
              and "dinamica con globos" not in txt and "manualidades navidenas" not in txt,
              "dice cuantos videos dio cada palabra, avisa cuando TikTok no tiene lista para una (sin llamarlo error) y NO sigue las relacionadas que hablan de otra cosa", txt[-900:])
    comprobar("Lista publica de TikTok:" in txt and "(gratis)" in txt and "llave" not in txt.lower() and "credito" not in txt.lower(), "dice que fue gratis y no pide ninguna llave", txt[-500:])
    f6 = b.leer_fuentes(l6)
    comprobar(f6[K1]["consulta"] == "crochet" and f6[K1]["tema"].startswith("Amigurumi paso a paso") and f6[K1]["fuente"] == "consulta:crochet", "fuentes.csv guarda de que busqueda salio cada video y de que trata segun TikTok", str(f6.get(K1)))
    comprobar(f6[K7]["consulta"] == "crochet para principiantes" and "primeros puntos" in f6[K7]["tema"], "los que salen de una palabra relacionada anotan esa palabra", str(f6.get(K7)))
    comprobar(not any(q.get("keyword") == "dinamica-con-globos" for u, q, h in VISTO_TT), "no se pidio la lista de la relacionada que habla de otra cosa", str([q.get("keyword") for u, q, h in VISTO_TT]))
    resumen = {}
    a = b.construir_parser().parse_args(["traer", "prueba7", "--consulta", "crochet", "--sin-drive", "--max", "2", "--paginas", "1"])
    with contextlib.redirect_stdout(io.StringIO()):
        b.traer(a, fabrica=DescargadorFalso, resumen=resumen)
    comprobar(resumen.get("bajados") == 2 and resumen.get("creditos") == 2 and resumen.get("creditos_quedan") is None,
              "traer() cuenta los pedidos a TikTok como 'creditos' del resumen (la pagina de visitante + 1 lista) y no inventa saldo", str(resumen))
    (b.PUBLIC / "productos" / "prueba7" / "broll.json").write_text(json.dumps({"consultas": ["crochet"], "paginas": 1, "min_seg": 4, "max_seg": 60, "proveedor": "descubrir", "relacionadas": 1}), encoding="utf-8")
    VISTO_TT.clear()
    codigo, txt = correr_traer(b, ["prueba7", "--sin-drive", "--simular"])
    comprobar("Relacionada \"crochet para principiantes\" (de \"crochet\")" in txt and sum(1 for u, q, h in VISTO_TT if u.endswith("/related_keywords/")) == 1,
              "broll.json con proveedor y relacionadas funciona: pide 1 palabra relacionada por consulta", txt[-400:])
    VISTO_TT.clear()
    codigo, txt = correr_traer(b, ["prueba7", "--consulta", "crochet", "--sin-drive", "--simular", "--max-creditos", "2", "--paginas", "3"])
    comprobar("tope de 2 pedidos a TikTok" in txt and "se usan los 5 videos de las paginas anteriores" in txt and codigo == 0, "con un tope chico se queda con lo que ya tenia, lo dice y no se cae", txt[-400:])
    codigo, txt = correr_traer(b, ["prueba7", "--consulta", "algo que no existe", "--consulta", "otra que tampoco", "--sin-drive", "--simular"])
    comprobar(codigo == 2 and "no tiene lista publica para ninguna de las palabras buscadas" in txt, "si NINGUNA palabra tiene lista lo dice como problema de la corrida (salida 2)", txt[-300:])
    MODO_TT["siempre_429"] = True
    codigo, txt = correr_traer(b, ["prueba7", "--consulta", "crochet", "--sin-drive", "--simular"])
    MODO_TT["siempre_429"] = False
    comprobar("esperar" in txt and codigo == 2, "si TikTok pide esperar, la corrida no se cae: avisa y sale con 2", txt[-300:])
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        cod_gratis = b.probar_proveedor()
    comprobar(cod_gratis == 0 and "sirve" in salida.getvalue() and "gratis" in salida.getvalue() and "llave" not in salida.getvalue().lower(),
              "el comando proveedor (sin decir cual) prueba la lista publica y no gasta nada", salida.getvalue())
    MODO_TT["siempre_429"] = True
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        cod_mal = b.probar_proveedor()
    MODO_TT["siempre_429"] = False
    comprobar(cod_mal == 2 and "NO esta dando" in salida.getvalue(), "y si TikTok no da la lista dice que NO", salida.getvalue())

    codigo, txt = correr_traer(b, ["no-existe", "--enlace", f"https://www.tiktok.com/@x/video/{ok1}"])
    comprobar(codigo == 1 and "No existe public/productos/no-existe" in txt, "producto inexistente sin --drive: se explica")
    codigo, txt = correr_traer(b, ["prueba2"])
    comprobar(codigo == 1 and "No hay de donde sacar videos" in txt, "sin fuentes ni broll.json se explica", txt[-200:])

    print("Cuentas que TikTok manda a esperar")
    intentos: dict[str, int] = {}
    original_cuenta = b.fuente_cuenta

    def cuenta_con_limite(usuario: str):
        intentos[usuario] = intentos.get(usuario, 0) + 1
        if usuario == "limitada" and intentos[usuario] == 1:
            raise b.ErrorFuenteEspera("@limitada: TikTok pidio esperar ('ratelimit triggered'); prueba en unos minutos")
        if usuario == "rota":
            raise b.ErrorFuente("@rota: la pagina no trae la lista de videos")
        return [b.candidato(id="9999000000000210001", url="https://www.tiktok.com/@" + usuario + "/video/9999000000000210001", autor=usuario, fuente="cuenta:@" + usuario, duracion=20, ancho=720, alto=1280)]
    b.fuente_cuenta = cuenta_con_limite
    try:
        codigo, txt = correr_traer(b, ["prueba4", "--cuenta", "buena", "--cuenta", "limitada", "--cuenta", "rota", "--sin-drive", "--simular"])
    finally:
        b.fuente_cuenta = original_cuenta
    comprobar(intentos.get("limitada") == 2 and intentos.get("buena") == 1 and intentos.get("rota") == 1, "una cuenta que TikTok manda a esperar se reintenta UNA vez al final; una rota no se reintenta", str(intentos))
    comprobar("se reintenta al final" in txt and "segundo intento" in txt and "la pagina no trae la lista" in txt, "y se dice en pantalla", txt[-500:])

    print("Recoger: lo que el servidor dejo en Drive llega al editor")

    def correr_recoger(args: list[str]) -> tuple[int, str]:
        a_ = b.construir_parser().parse_args(["recoger", *args])
        sal = io.StringIO()
        with contextlib.redirect_stdout(sal), contextlib.redirect_stderr(sal):
            try:
                cod = b.recoger(a_)
            except SystemExit as e:
                cod = int(e.code or 0)
        return cod, sal.getvalue()

    drive3 = tmp / "drive" / "OFERTA3"
    (drive3 / "B-rolls").mkdir(parents=True)
    ids3 = ["9999000000000220001", "9999000000000220002", "9999000000000220003"]
    for i in ids3:
        shutil.copy2(material / "vertical.mp4", drive3 / "B-rolls" / f"tt_{i}.mp4")
    (drive3 / "B-rolls" / "otra-cosa.mp4").write_bytes(b"x")
    b.escribir_fuentes(drive3 / "B-rolls", {
        ids3[0]: {"id": ids3[0], "archivo": f"tt_{ids3[0]}.mp4", "estado": "bajado", "autor": "ana", "calidad": "limpio", "tramos_limpios": "0-5", "en_drive": "si", "descargado_en": "2026-10-20T05:00:00"},
        "9999000000000220099": {"id": "9999000000000220099", "estado": "descartado", "motivo": "texto quemado", "descargado_en": "2026-10-20T05:00:01"}})
    p5 = b.PUBLIC / "productos" / "prueba5"
    (p5 / "broll-tiktok").mkdir(parents=True)
    b.escribir_fuentes(p5 / "broll-tiktok", {ids3[1]: {"id": ids3[1], "archivo": f"tt_{ids3[1]}.mp4", "estado": "bajado", "en_drive": "si"}})    # el editor ya lo tuvo y el equipo borro el archivo
    cod, txt = correr_recoger(["prueba5", "--drive", str(drive3), "--simular"])
    comprobar(cod == 0 and not list((p5 / "broll-tiktok").glob("tt_*.mp4")) and "traeria" in txt, "recoger --simular dice que traeria pero no copia nada", txt)
    cod, txt = correr_recoger(["prueba5", "--drive", str(drive3)])
    nombres5 = sorted(p.name for p in (p5 / "broll-tiktok").glob("tt_*.mp4"))
    comprobar(cod == 0 and nombres5 == [f"tt_{ids3[0]}.mp4", f"tt_{ids3[2]}.mp4"], "recoger trae lo que falta y NO lo que el editor ya tuvo (aunque el equipo borrara el archivo)", f"{nombres5}\n{txt}")
    f5 = b.leer_fuentes(p5 / "broll-tiktok")
    comprobar(f5[ids3[0]]["autor"] == "ana" and f5[ids3[0]]["calidad"] == "limpio" and f5[ids3[0]]["tramos_limpios"] == "0-5" and f5[ids3[2]]["archivo"] == f"tt_{ids3[2]}.mp4"
              and f5["9999000000000220099"]["estado"] == "descartado" and f5[ids3[1]]["en_drive"] == "si", "y suma las filas de Drive a fuentes.csv (calidad y tramos limpios incluidos; lo descartado tambien)", str(f5))
    comprobar(not (p5 / "broll-tiktok" / "otra-cosa.mp4").exists(), "un video que no es de TikTok (otra-cosa.mp4) no se toca")
    cod, txt = correr_recoger(["prueba5", "--drive", str(drive3)])
    comprobar(cod == 0 and "0 video(s) traidos" in txt and "Siguiente paso" not in txt, "una segunda vez no trae nada")
    cod, txt = correr_recoger(["prueba5", "--drive", str(tmp / "no-hay-drive")])
    comprobar(cod == 1 and "No encuentro" in txt, "sin la carpeta B-rolls se explica")
    cod, txt = correr_recoger(["prueba5", "--drive", "https://drive.google.com/drive/folders/abc"])
    comprobar(cod == 1 and "RUTA de la carpeta" in txt, "un enlace de Drive en vez de la ruta se explica")
    cod, txt = correr_recoger(["prueba5"])
    comprobar(cod == 1 and "No se donde esta la carpeta de Drive" in txt, "sin --drive ni broll.json se explica")
    cod, txt = correr_recoger(["no-existe-nada", "--drive", str(drive3)])
    comprobar(cod == 1 and "No existe public/productos/no-existe-nada" in txt, "producto inexistente se explica")

    print("Estado")
    a = b.construir_parser().parse_args(["estado", "prueba"])
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        b.estado(a)
    comprobar("bajados con broll_tiktok.py" in salida.getvalue() and "Busqueda por palabra: gratis" in salida.getvalue(), "estado resume lo que hay y dice que la busqueda por palabra es gratis")


def prueba_vivo(b, url: str) -> None:
    print(f"VIVO: bajando de verdad {url}")
    e = b.interpretar_enlace(url)
    comprobar(e is not None, "el enlace es de un video de TikTok")
    if not e:
        return
    with tempfile.TemporaryDirectory() as t:
        d = b.DescargadorYtdlp(Path(t), "mejor")
        try:
            r = d.bajar(e["url"], 1, 600)
        finally:
            d.cerrar()
        comprobar(r.get("ok"), "yt-dlp baja el video", str(r.get("motivo")))
        if r.get("ok"):
            ok, motivo, tec = b.comprobar_archivo(r["ruta"], 1, 600, True)
            comprobar(ok, "el archivo se abre con ffprobe", motivo)
            ancho, alto = tec.get("ancho") or r.get("ancho"), tec.get("alto") or r.get("alto")
            print(f"         {tec.get('codec') or r.get('codec')} {ancho}x{alto} {float(tec.get('duracion') or r.get('duracion') or 0):.0f} s, {r['ruta'].stat().st_size / 1e6:.1f} MB, autor @{r.get('autor')}")
            comprobar((alto or 0) >= 1024, "viene en alta resolucion (sin marca de agua)")


def main() -> None:
    configurar_consola()
    vivo = None
    if "--vivo" in sys.argv:
        i = sys.argv.index("--vivo")
        vivo = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        if not vivo:
            print("Uso: python scripts/autoprueba_broll.py --vivo https://www.tiktok.com/@usuario/video/123...")
            sys.exit(1)
    solo_vivo = "--solo-vivo" in sys.argv
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Falso)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    os.environ["BROLL_PROVEEDOR_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    servidor_tt = ThreadingHTTPServer(("127.0.0.1", 0), FalsoTikTok)
    threading.Thread(target=servidor_tt.serve_forever, daemon=True).start()
    os.environ["BROLL_TIKTOK_URL"] = f"http://127.0.0.1:{servidor_tt.server_port}"
    os.environ.pop("SCRAPECREATORS_API_KEY", None)
    b = importlib.import_module("broll_tiktok")
    comprobar(b.BASE_PROVEEDOR.startswith("http://127.0.0.1:") and b.BASE_TIKTOK.startswith("http://127.0.0.1:"), "la autoprueba habla con el proveedor y con el TikTok FALSOS")
    os.environ["BROLL_PROVEEDOR_URL"] = "https://evil.example.com"
    os.environ["BROLL_TIKTOK_URL"] = "https://evil.example.com"
    otro = importlib.reload(b)
    comprobar(otro.BASE_PROVEEDOR == "https://api.scrapecreators.com" and otro.BASE_TIKTOK == "https://www.tiktok.com", "una direccion que no es de esta maquina se ignora (la llave y las cookies no pueden ir a otro lado)")
    os.environ["BROLL_PROVEEDOR_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    os.environ["BROLL_TIKTOK_URL"] = f"http://127.0.0.1:{servidor_tt.server_port}"
    b = importlib.reload(b)

    if not solo_vivo:
        pruebas_puras(b)
        pruebas_proveedor(b)
        pruebas_descubrir(b)
        with tempfile.TemporaryDirectory(prefix="autoprueba_broll_") as t:
            pruebas_traer(b, Path(t))
    servidor.shutdown()
    servidor_tt.shutdown()
    if vivo:
        importlib.reload(b)
        prueba_vivo(b, vivo)
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON {len(FALLAS)} FALLA(S):")
        for f in FALLAS:
            print("  -", f)
        sys.exit(1)
    print("AUTOPRUEBA OK")


if __name__ == "__main__":
    main()
