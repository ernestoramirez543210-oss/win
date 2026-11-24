"""Autoprueba de scripts/referencia_meta.py SIN internet y sin tocar a Meta: un Meta FALSO en esta misma maquina (con el reto de /__rd_verify,
la pagina con el anuncio incrustado y el archivo de video) que se puede poner a fallar.

Comprueba: enlaces, el anuncio suelto dentro del HTML (aunque haya objetos antes de su numero) y entre los resultados de la busqueda, el copy,
que los datos guardados no traigan enlaces firmados, el reto de Meta (resuelto, repetido y no resuelto), el video (HD y SD, de un carrusel),
la imagen de un anuncio sin video, y que NO se baje nada de un dominio que no sea fbcdn.net, ni con redireccion, ni de otro tipo, ni gigante,
ni diminuto, ni roto (sin dejar archivos a medias).

Tambien comprueba la lista de anuncios del MISMO anunciante (solo los de su page_id, un anuncio por creativo) y que se elijan los mas antiguos con video:
primero los que siguen activos, luego los apagados, nunca los de imagen ni los de otro anunciante.

Uso:  python scripts/autoprueba_referencia.py [--vivo <enlace de un anuncio de la Biblioteca>]
Termina con "AUTOPRUEBA OK" si todo va bien. Con --vivo ademas lee ESE anuncio de verdad (una pagina de la Biblioteca; necesita internet).
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola, ejecutable  # noqa: E402

FALLAS: list[str] = []
SERVIDOR: list = []      # el servidor falso, para armar enlaces de video que apunten a el
MODO = {"reto": True, "verify_429": 0, "limitado": False, "pais_chico": False, "pagina_vacia": False, "pagina_hay_mas": False}
VISTO: list[str] = []
MEDIA: dict[str, tuple[int, str, bytes, dict]] = {}


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"  ->  {detalle}" if detalle else ""))


# ------------------------------------------------------------------ anuncios de mentira (misma forma que la Biblioteca)
V_OK, V_IMG, V_CARR, V_RESULT, V_TIPO, V_REDIR, V_GRANDE, V_CHICO, V_ROTO, V_403, V_AJENO, V_REDOK, V_BUCLE = (f"{n:02d}00000000000001" for n in range(1, 14))
NO_EXISTE = "99999999999999"
L_IDS = [f"15000000000000{n:02d}" for n in range(1, 9)]
(L1, L2, L3, L4, L5, L6, L7, L8) = L_IDS
PREFIJO = V_OK + "7"   # un id que empieza igual que otro (no debe confundirse)


def anuncio(ad_id: str, snap: dict, **extra) -> dict:
    base = {"categories": ["UNKNOWN"], "fev_info": {"x": 1}, "ad_archive_id": ad_id, "ad_id": None, "is_active": True, "start_date": 1750000000, "end_date": None,
            "page_id": "555000111", "page_name": "Tienda de Prueba", "publisher_platform": ["FACEBOOK", "INSTAGRAM"], "collation_count": 3, "snapshot": snap}
    base.update(extra)
    return base


def snap_video(host: str, ruta: str, texto: str = "¿Ya intentaste aprender y no pudiste? No fue tu culpa.\n\nAprende bien, por solo $9,90.") -> dict:
    return {"body": {"text": texto}, "title": "Escríbenos", "link_description": None, "caption": "wa.me", "cta_text": "Enviar mensaje de WhatsApp", "cta_type": "WHATSAPP_MESSAGE",
            "display_format": "VIDEO", "link_url": "https://api.whatsapp.com/send", "page_name": "Tienda de Prueba", "cards": [], "images": [],
            "videos": [{"video_hd_url": f"{host}/media/{ruta}.mp4?_nc_sig=SECRETO", "video_sd_url": f"{host}/media/{ruta}_sd.mp4?_nc_sig=SECRETO",
                        "video_preview_image_url": f"{host}/media/foto.jpg"}]}


def compacto(obj) -> str:
    """JSON como lo escribe Meta: sin espacios, con los caracteres no ASCII escapados."""
    return json.dumps(obj, separators=(",", ":"))


def anuncios_de_la_lista(host: str) -> list[dict]:
    """El anunciante 555000111 con 6 anuncios (L1 y L2 son el mismo creativo; L4 esta apagado y es el mas viejo de los que tienen video; L5 es de imagen),
    mas uno de OTRO anunciante (L7)."""
    imagen = {"body": {"text": "Solo imagen"}, "display_format": "IMAGE", "videos": [], "cards": [], "images": [{"original_image_url": f"{host}/media/foto.jpg"}], "page_name": "Tienda de Prueba"}
    ajeno = dict(snap_video(host, "ok", "Texto Z: de otra tienda"), page_name="Otra Tienda")
    return [
        anuncio(L1, snap_video(host, "ok", "Texto A: el primero de todos"), start_date=1700000000, collation_id="gA"),
        anuncio(L2, snap_video(host, "ok", "Texto A: el primero de todos"), start_date=1700000100, collation_id="gB"),
        anuncio(L3, snap_video(host, "ok", "Texto B: el segundo creativo"), start_date=1710000000),
        anuncio(L4, snap_video(host, "ok", "Texto C: el viejo apagado"), start_date=1690000000, is_active=False),
        anuncio(L5, imagen, start_date=1695000000),
        anuncio(L6, snap_video(host, "ok", "Texto D: el cuarto creativo"), start_date=1730000000),
        anuncio(L7, ajeno, start_date=1600000000, page_id="777000999", page_name="Otra Tienda"),
    ]


def html_con(objetos: list[dict], como: str, hay_mas: bool = False) -> str:
    """HTML con el anuncio incrustado: 'suelto' (en un objeto grande, con relleno) o 'resultados' (dentro de search_results_connection)."""
    relleno = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"define": [["X", [], {"a": {"b": 1}}, 1]]}}]]], "otro": {"fev": {"k": [1, 2, {"z": None}]}}}
    if como == "resultados":
        con = {"edges": [{"node": {"collated_results": objetos}}], "page_info": {"has_next_page": hay_mas, "end_cursor": None}}
        cuerpo = f'<script>{compacto(relleno)}</script><script>{{"ad_library_main":{{"search_results_connection":{compacto(con)}}}}}</script>'
    else:
        cuerpo = f"<script>{compacto(relleno)}</script><script>{compacto({'x': {'detalle': objetos}})}</script>"
    return f"<!doctype html><html><head><title>Biblioteca de anuncios</title></head><body>{cuerpo}</body></html>"


class MetaFalso(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, codigo: int, tipo: str, cuerpo: bytes, cab: dict | None = None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str((cab or {}).get("_largo", len(cuerpo))))
        for k, v in (cab or {}).items():
            if not k.startswith("_"):
                self.send_header(k, v)
        self.end_headers()
        if (cab or {}).get("_sin_cuerpo"):
            return
        self.wfile.write(cuerpo)

    def do_POST(self):
        VISTO.append("POST " + self.path)
        if self.path.startswith("/__rd_verify"):
            if MODO["verify_429"] > 0:
                MODO["verify_429"] -= 1
                return self._enviar(429, "text/plain", b"espera")
            return self._enviar(200, "text/plain", b"ok", {"Set-Cookie": "rd_challenge=ok; Path=/"})
        self._enviar(404, "text/plain", b"no")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        VISTO.append("GET " + u.path + ("?" + ",".join(sorted(q)) if q else ""))
        host = f"http://127.0.0.1:{self.server.server_port}"
        if u.path == "/ads/library":
            if MODO["limitado"]:
                return self._enviar(403, "text/html", b"<html>Bloqueado</html>")
            if MODO["reto"] and "rd_challenge=ok" not in (self.headers.get("Cookie") or ""):
                return self._enviar(403, "text/html", b"<html><script>fetch('/__rd_verify_abc123?challenge=1',{method:'POST'}).then(()=>location.reload())</script></html>")
            pid = (q.get("view_all_page_id") or [""])[0]
            if pid:       # la pagina de un anunciante («Ver todos los anuncios»): sus anuncios, en cualquier pais
                lista_pagina = [] if MODO["pagina_vacia"] else [a for a in anuncios_de_la_lista(host) if a["page_id"] == pid]
                return self._enviar(200, "text/html", html_con(lista_pagina, "resultados", hay_mas=MODO["pagina_hay_mas"]).encode())
            ad_id = (q.get("id") or [""])[0]
            con_pais = "country" in q
            if ad_id in L_IDS:      # la lista de un anunciante: con pais trae sus anuncios; L1 dice ademas que hay mas paginas; L8 esta suelto y NO entre los resultados
                lista = anuncios_de_la_lista(host)
                if ad_id == L8:
                    suelto = anuncio(L8, snap_video(host, "ok", "Texto E: suelto"), start_date=1705000000)
                    return self._enviar(200, "text/html", (html_con([suelto], "suelto") + html_con(lista[:2], "resultados")).encode())
                if con_pais:
                    if MODO["pais_chico"]:      # con el pais de un solo mercado solo se ve el anuncio pedido (un anunciante de LATAM con pais BR)
                        return self._enviar(200, "text/html", html_con([a for a in lista if a["ad_archive_id"] == ad_id], "resultados", hay_mas=True).encode())
                    return self._enviar(200, "text/html", html_con(lista, "resultados", hay_mas=(ad_id == L1)).encode())
                return self._enviar(200, "text/html", html_con([a for a in lista if a["ad_archive_id"] == ad_id], "suelto").encode())
            if ad_id == V_OK:
                return self._enviar(200, "text/html; charset=utf-8", html_con([anuncio(ad_id, snap_video(host, "ok"))], "suelto").encode())
            if ad_id == V_IMG:
                s = {"body": {"text": "Solo imagen"}, "title": None, "cta_text": "Comprar", "display_format": "IMAGE", "cards": [], "videos": [],
                     "images": [{"original_image_url": f"{host}/media/foto.jpg", "resized_image_url": None}], "link_url": "https://tienda.example/x", "page_name": "Foto"}
                return self._enviar(200, "text/html", html_con([anuncio(ad_id, s)], "suelto").encode())
            if ad_id == V_CARR:
                s = {"body": {"text": "Carrusel"}, "display_format": "CAROUSEL", "videos": [], "images": [], "cta_text": "Saber mas",
                     "cards": [{"title": "Uno", "body": "Primera", "original_image_url": f"{host}/media/foto.jpg"},
                               {"title": "Dos", "body": "Segunda", "video_hd_url": f"{host}/media/ok.mp4", "video_sd_url": None}]}
                return self._enviar(200, "text/html", html_con([anuncio(ad_id, s)], "suelto").encode())
            if ad_id == V_RESULT:      # solo sale entre los resultados y solo si se pide con pais
                if con_pais:
                    return self._enviar(200, "text/html", html_con([anuncio("2" + ad_id, snap_video(host, "ok")), anuncio(ad_id, snap_video(host, "ok"))], "resultados").encode())
                return self._enviar(200, "text/html", html_con([], "suelto").encode())
            if ad_id in (V_TIPO, V_REDIR, V_GRANDE, V_CHICO, V_ROTO, V_403, V_AJENO, V_REDOK, V_BUCLE):
                ruta = {V_TIPO: "tipo", V_REDIR: "redir", V_GRANDE: "grande", V_CHICO: "chico", V_ROTO: "roto", V_403: "denegado", V_AJENO: "ok", V_REDOK: "redok", V_BUCLE: "bucle"}[ad_id]
                s = snap_video(host if ad_id != V_AJENO else "https://evil.example.com", ruta)
                return self._enviar(200, "text/html", html_con([anuncio(ad_id, s)], "suelto").encode())
            return self._enviar(200, "text/html", html_con([], "suelto").encode())
        if u.path.startswith("/media/"):
            nombre = u.path[len("/media/"):].split(".")[0].replace("_sd", "")
            codigo, tipo, cuerpo, cab = MEDIA.get(nombre, (404, "text/plain", b"no", {}))
            return self._enviar(codigo, tipo, cuerpo, cab)
        self._enviar(404, "text/plain", b"no")


def hacer_mp4(ruta: Path, ancho: int = 270, alto: int = 480, seg: int = 4) -> None:
    r = subprocess.run([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={ancho}x{alto}:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
                        "-t", str(seg), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "300k", "-c:a", "aac", str(ruta)], capture_output=True, text=True)
    if r.returncode != 0 or not ruta.is_file():
        print("ERROR: ffmpeg no pudo crear el video de prueba:", r.stderr[:200])
        sys.exit(1)


def preparar_media(tmp: Path) -> None:
    video = tmp / "v.mp4"
    hacer_mp4(video)
    datos = video.read_bytes()
    MEDIA["ok"] = (200, "video/mp4", datos, {})
    MEDIA["tipo"] = (200, "text/html", b"<html>no soy un video</html>" * 2000, {})
    MEDIA["redir"] = (302, "text/plain", b"", {"Location": "https://evil.example.com/robado.mp4"})
    MEDIA["redok"] = (302, "text/plain", b"", {"Location": "/media/ok.mp4"})        # Meta redirige entre sus propios servidores de video (lo midio la prueba en vivo)
    MEDIA["bucle"] = (302, "text/plain", b"", {"Location": "/media/bucle.mp4"})
    MEDIA["grande"] = (200, "video/mp4", b"x" * 10, {"_largo": 400_000_000, "_sin_cuerpo": True})
    MEDIA["chico"] = (200, "video/mp4", b"\x00" * 5000, {})
    MEDIA["roto"] = (200, "video/mp4", os.urandom(80_000), {})
    MEDIA["denegado"] = (403, "text/plain", b"caducado", {})
    MEDIA["foto"] = (200, "image/jpeg", b"\xff\xd8\xff\xe0" + os.urandom(40_000), {})


def correr_cli(r, argumentos: list[str]) -> tuple[int, str]:
    salida = io.StringIO()
    sys.argv = ["referencia_meta.py", *argumentos]
    with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
        try:
            r.main()
            codigo = 0
        except SystemExit as e:
            codigo = int(e.code or 0)
    return codigo, salida.getvalue()


# ------------------------------------------------------------------ pruebas
def pruebas_puras(r) -> None:
    print("Enlaces")
    comprobar(r.id_de("https://www.facebook.com/ads/library/?id=9999000000247761") == "9999000000247761", "enlace de la Biblioteca")
    comprobar(r.id_de("https://www.facebook.com/ads/library/?active_status=all&country=BR&id=9999000000247761&media_type=all") == "9999000000247761", "enlace con mas parametros")
    comprobar(r.id_de(" 9999000000247761 ") == "9999000000247761", "el numero solo")
    for malo in ("https://evil.com/ads/library/?id=9999000000247761", "https://www.facebook.com/profile.php?id=9999000000247761", "https://facebook.com.evil.com/ads/library/?id=9999000000247761",
                 "https://www.facebook.com/ads/library/?q=hola", "javascript:alert(1)", "12345", "", "no es un enlace"):
        comprobar(r.id_de(malo) is None, f"se rechaza {malo[:55]!r}")

    print("Dominios permitidos para bajar archivos")
    for url, esperado in (("https://video.feoh13-1.fna.fbcdn.net/o1/v/x.mp4?oh=1", True), ("https://fbcdn.net/x", True), ("https://evil.example.com/x.mp4", False),
                          ("https://fbcdn.net.evil.com/x.mp4", False), ("http://video.fna.fbcdn.net/x.mp4", False), ("https://user@video.fna.fbcdn.net/x.mp4", False),
                          ("https://127.0.0.1/x.mp4", False), ("ftp://video.fna.fbcdn.net/x", False), ("", False)):
        comprobar(r.host_permitido(url) is esperado, f"host_permitido({url[:50]!r}) = {esperado}")

    print("Buscar el anuncio dentro del HTML")
    host = "https://video.fna.fbcdn.net"
    ad = anuncio(V_OK, snap_video(host, "ok"))
    h1 = html_con([ad], "suelto")
    hallado = r.buscar_anuncio(h1, V_OK)
    comprobar(hallado is not None and hallado["snapshot"]["page_name"] == "Tienda de Prueba", "anuncio suelto, con objetos anidados ANTES de su numero")
    h2 = html_con([anuncio("2" + V_OK, snap_video(host, "ok")), ad], "resultados")
    comprobar((r.buscar_anuncio(h2, V_OK) or {}).get("ad_archive_id") == V_OK, "entre los resultados de la busqueda (y no confunde un numero que empieza igual)")
    comprobar(r.buscar_anuncio(html_con([anuncio(PREFIJO, snap_video(host, "ok"))], "suelto"), V_OK) is None, "un numero que solo empieza igual no se confunde")
    comprobar(r.buscar_anuncio(h1, NO_EXISTE) is None and r.buscar_anuncio("<html></html>", V_OK) is None and r.buscar_anuncio('{"ad_archive_id":"' + V_OK + '"', V_OK) is None, "si no esta (o el JSON esta roto) devuelve None")

    print("Copy y datos")
    c = r.copy_de(hallado)
    comprobar("No fue tu culpa" in c and "Boton: Enviar mensaje de WhatsApp" in c and "Titulo: Escríbenos" in c and "wa.me" not in c, "copy: texto, titulo y boton (sin el dominio del pie)", c)
    carr = anuncio("3" + V_OK, {"body": {"text": "Intro"}, "cta_text": "Ver", "cards": [{"title": "A", "body": "uno"}, {"title": "B", "body": "dos"}, {"title": "", "body": "Intro"}]})
    cc = r.copy_de(carr)
    comprobar("Tarjeta 1: A | uno" in cc and "Tarjeta 2: B | dos" in cc and cc.count("Intro") == 1, "copy de un carrusel: una linea por tarjeta, sin repetir el texto principal", cc)
    d = r.datos_de(hallado, V_OK)
    crudo = json.dumps(d)
    comprobar(d["anunciante"] == "Tienda de Prueba" and d["formato"] == "VIDEO" and d["dias_en_aire"] and d["dias_en_aire"] > 100 and d["destino"] == "api.whatsapp.com" and d["tiene_video"] and d["variantes"] == 3, "datos: anunciante, formato, dias en el aire, destino, variantes", crudo[:300])
    comprobar("SECRETO" not in crudo and "http" not in crudo and "fbcdn" not in crudo, "los datos guardados no llevan enlaces firmados")
    comprobar(r.elegir_video(hallado) == (f"{host}/media/ok.mp4?_nc_sig=SECRETO", "hd") and r.elegir_video(hallado, sd=True)[1] == "sd", "elige HD, o SD con --sd")
    comprobar(r.elegir_video(anuncio("4" + V_OK, {"videos": [], "cards": []})) is None, "sin video devuelve None")


def pruebas_cliente(r) -> None:
    print("El reto de Meta")
    VISTO.clear()
    MODO.update(reto=True, verify_429=0, limitado=False)
    html = r.ClienteMeta().pagina(V_OK)
    comprobar(V_OK in html and VISTO[:3] == ["GET /ads/library?id", "POST /__rd_verify_abc123?challenge=1", "GET /ads/library?id"], "403 con reto -> POST de verificacion -> se vuelve a pedir", str(VISTO))
    r.time.sleep = lambda s: None
    VISTO.clear()
    MODO["verify_429"] = 2
    html = r.ClienteMeta().pagina(V_OK)
    comprobar(V_OK in html and sum(1 for v in VISTO if v.startswith("POST")) == 3, "si la verificacion contesta 429 se espera y se repite", str(VISTO))
    MODO["limitado"] = True
    try:
        r.ClienteMeta().pagina(V_OK)
        comprobar(False, "Meta que limita la conexion se explica")
    except r.ErrorMeta as e:
        comprobar("limito esta conexion" in str(e) and "403" in str(e), "Meta que limita la conexion se explica", str(e))
    MODO.update(limitado=False, verify_429=0)

    print("Leer un anuncio")
    ad, ad_id = r.leer(f"https://www.facebook.com/ads/library/?id={V_OK}")
    comprobar(ad_id == V_OK and ad["snapshot"]["page_name"] == "Tienda de Prueba", "leer por enlace")
    VISTO.clear()
    ad, _ = r.leer(V_RESULT)
    comprobar(ad["ad_archive_id"] == V_RESULT and any("country" in v for v in VISTO), "si la pagina suelta no trae el anuncio, una segunda mirada con pais lo halla", str(VISTO))
    for malo, trozo in ((NO_EXISTE, "no aparece"), ("https://evil.com/x", "no es un enlace")):
        try:
            r.leer(malo)
            comprobar(False, f"error claro para {malo[:30]!r}")
        except r.ErrorMeta as e:
            comprobar(trozo in str(e), f"error claro para {malo[:30]!r}", str(e))
    comprobar(not any("evil" in v for v in VISTO), "un enlace ajeno ni siquiera se pide")


def pruebas_traer(r, tmp: Path) -> None:
    print("Traer la referencia (copy + datos + video)")
    MODO.update(reto=True, verify_429=0, limitado=False)
    dest = tmp / "referencia"
    res = r.traer_referencia(V_OK, dest, "AD1")
    comprobar((dest / "AD1_referencia.mp4").is_file() and (dest / "AD1_copy.txt").is_file() and (dest / "AD1_referencia.json").is_file(), "deja video, copy y datos", str(res))
    comprobar(res["video"] == "AD1_referencia.mp4" and res["copy_caracteres"] > 40 and res["anunciante"] == "Tienda de Prueba", "el resumen lo dice")
    j = json.loads((dest / "AD1_referencia.json").read_text(encoding="utf-8"))
    comprobar(j.get("video_calidad") == "hd" and j.get("video_duracion", 0) >= 3.5 and j.get("video_alto") == 480 and j.get("video_audio") is True, "los datos traen la calidad y lo medido con ffprobe", str(j))
    comprobar("SECRETO" not in (dest / "AD1_referencia.json").read_text(encoding="utf-8"), "ningun enlace firmado queda guardado")
    comprobar("No fue tu culpa" in (dest / "AD1_copy.txt").read_text(encoding="utf-8"), "el copy trae el texto del anuncio")
    comprobar(not list(dest.glob("*.parte")), "sin archivos a medias")

    res = r.traer_referencia(V_OK, dest, "AD2", sd=True)
    comprobar(json.loads((dest / "AD2_referencia.json").read_text(encoding="utf-8")).get("video_calidad") == "sd", "con --sd baja el de menor calidad")
    res = r.traer_referencia(V_OK, dest, "AD3", sin_video=True)
    comprobar(res["video"] is None and not (dest / "AD3_referencia.mp4").exists() and (dest / "AD3_copy.txt").is_file(), "con --sin-video solo el copy y los datos")

    res = r.traer_referencia(V_IMG, dest, "AD4")
    comprobar(res["video"] is None and res.get("imagen") == "AD4_referencia.jpg" and (dest / "AD4_referencia.jpg").is_file() and "no tiene video" in res["motivo_sin_video"], "anuncio de imagen: baja la imagen y avisa que no hay video", str(res))
    res = r.traer_referencia(V_CARR, dest, "AD5")
    comprobar(res["video"] == "AD5_referencia.mp4" and "Tarjeta 2: Dos | Segunda" in (dest / "AD5_copy.txt").read_text(encoding="utf-8"), "carrusel: toma el video de la tarjeta que lo trae", str(res))

    print("Archivos que NO se deben bajar")
    res = r.traer_referencia(V_REDOK, dest, "AD13")
    comprobar(res["video"] == "AD13_referencia.mp4", "una redireccion entre servidores de Meta SI se sigue", str(res))
    for ad_id, nombre, trozo in ((V_TIPO, "AD6", "no es video"), (V_REDIR, "AD7", "redirige a un dominio"), (V_BUCLE, "AD14", "redirige demasiadas veces"), (V_GRANDE, "AD8", "tope"), (V_CHICO, "AD9", "chico"), (V_ROTO, "AD10", "no se abre"),
                                 (V_403, "AD11", "HTTP 403"), (V_AJENO, "AD12", "dominio de Meta")):
        res = r.traer_referencia(ad_id, dest, nombre)
        comprobar(res["video"] is None and trozo in res["motivo_sin_video"] and not (dest / f"{nombre}_referencia.mp4").exists() and (dest / f"{nombre}_copy.txt").is_file(),
                  f"{trozo}: no se guarda el video, queda el copy y se explica", str(res))
    comprobar(not list(dest.glob("*.parte")), "ningun archivo a medias despues de los fallos")

    print("Linea de comandos")
    r.PUBLIC = tmp / "public"
    (r.PUBLIC / "productos" / "prueba").mkdir(parents=True)

    def correr(argumentos: list[str]) -> tuple[int, str]:
        return correr_cli(r, argumentos)

    codigo, txt = correr(["traer", V_OK, "--producto", "prueba", "--anuncio", "AD1"])
    comprobar(codigo == 0 and (r.PUBLIC / "productos" / "prueba" / "referencia" / "AD1_referencia.mp4").is_file() and "copy de" in txt, "traer: sale bien y deja los archivos en referencia/", txt[-200:])
    codigo, txt = correr(["traer", V_TIPO, "--producto", "prueba", "--anuncio", "AD2"])
    comprobar(codigo == 2 and "SIN video" in txt and "AD2_referencia.mp4" in txt, "sin video: sale con codigo 2 y dice donde ponerlo a mano", txt[-250:])
    codigo, txt = correr(["leer", V_OK])
    comprobar(codigo == 0 and "No fue tu culpa" in txt and "Tienda de Prueba" in txt, "leer: imprime el copy y los datos")
    for argumentos, trozo in ((["traer", V_OK, "--producto", "Mal Nombre", "--anuncio", "AD1"], "no validos"), (["traer", V_OK, "--producto", "prueba", "--anuncio", "AD99"], "no validos"),
                              (["traer", V_OK, "--producto", "no-existe", "--anuncio", "AD1"], "No existe public/productos/no-existe"), (["traer", "https://evil.com/x", "--producto", "prueba", "--anuncio", "AD1"], "no es un enlace")):
        codigo, txt = correr(argumentos)
        comprobar(codigo == 1 and trozo in txt, f"se rechaza {argumentos[1][:28]!r} {argumentos[3:6]}", txt[-150:])


def pruebas_anunciante(r, tmp: Path) -> None:
    print("Los anuncios del mismo anunciante y los mas antiguos")
    MODO.update(reto=True, verify_429=0, limitado=False)
    host = "https://video.fna.fbcdn.net"
    lista = anuncios_de_la_lista(host)
    html = html_con(lista, "resultados", hay_mas=True)
    ads, mas = r.anuncios_embebidos(html)
    comprobar(len(ads) == 7 and mas is True and [a["ad_archive_id"] for a in ads] == L_IDS[:7], "anuncios_embebidos: los 7 de la pagina, en orden, y dice que hay mas paginas")
    comprobar(r.anuncios_embebidos("<html></html>") == ([], False) and len(r.anuncios_embebidos(html_con(lista + lista, "resultados"))[0]) == 7, "sin resultados da [] y un anuncio repetido cuenta una vez")

    VISTO.clear()
    info = r.anuncios_del_anunciante(f"https://www.facebook.com/ads/library/?id={L3}", pais="BR")
    ids = [a["ad_archive_id"] for a in info["anuncios"]]
    comprobar(info["anunciante"] == "Tienda de Prueba" and info["page_id"] == "555000111" and info["referencia_id"] == L3 and ids == [L1, L2, L3, L4, L5, L6] and info["hay_mas"] is False,
              "anuncios_del_anunciante: solo los de SU page_id (L7 es de otra tienda), con su nombre y sin mas paginas", str((info["anunciante"], info["page_id"], ids, info["hay_mas"])))
    comprobar(sum(1 for v in VISTO if v.startswith("GET /ads/library")) == 3 and any("country" in v and "id" in v.split("?")[1].split(",") for v in VISTO) and any("view_all_page_id" in v for v in VISTO),
              "pide la pagina del anuncio (con pais) y la de su anunciante en todos los paises (3 GET: el reto y las dos paginas)", str(VISTO))
    comprobar(r.anuncios_del_anunciante(L1)["hay_mas"] is True, "si Meta dice que hay mas paginas, lo avisa (no las lee)")
    info7 = r.anuncios_del_anunciante(L7)
    comprobar([a["ad_archive_id"] for a in info7["anuncios"]] == [L7] and info7["anunciante"] == "Otra Tienda", "si se pide un anuncio de la otra tienda, salen los de ELLA y ninguno de la primera")
    info8 = r.anuncios_del_anunciante(L8)
    comprobar([a["ad_archive_id"] for a in info8["anuncios"]] == [L8, L1, L2, L3, L4, L5, L6], "si el anuncio pedido no esta entre los resultados, se agrega primero (L8) y la pagina del anunciante en todos los paises trae el resto (L1 a L6)",
              str([a["ad_archive_id"] for a in info8["anuncios"]]))
    for malo in ("https://evil.com/ads/library/?id=" + L3, "no es un enlace", NO_EXISTE):
        try:
            r.anuncios_del_anunciante(malo)
            comprobar(False, f"se rechaza {malo[:40]!r}")
        except r.ErrorMeta as e:
            comprobar(True, f"se rechaza {malo[:40]!r}: {str(e)[:50]}")

    ids_de = lambda lst: [a["ad_archive_id"] for a in lst]
    elegidos = r.elegir_mas_antiguos(info["anuncios"], 3)
    comprobar(ids_de(elegidos) == [L1, L3, L6], "elegir_mas_antiguos(3): los que siguen activos por fecha (L1, L3, L6); L2 es el mismo creativo que L1, L5 es de imagen y L4 esta apagado", str(ids_de(elegidos)))
    comprobar(ids_de(r.elegir_mas_antiguos(info["anuncios"], 4)) == [L1, L3, L6, L4], "si faltan activos se completa con el apagado mas viejo (L4)")
    comprobar(ids_de(r.elegir_mas_antiguos(info["anuncios"], 9)) == [L1, L3, L6, L4], "si hay menos de los pedidos devuelve los que hay (sin repetir creativos)")
    comprobar(ids_de(r.elegir_mas_antiguos(info["anuncios"], 5, solo_video=False)) == [L5, L1, L3, L6, L4], "con solo_video=False entran tambien los de imagen", str(ids_de(r.elegir_mas_antiguos(info["anuncios"], 5, solo_video=False))))
    muchos = [anuncio(f"16000000000{i:03d}", snap_video(host, "ok", f"Texto distinto numero {i}"), start_date=1700000000 + i) for i in range(30)]
    comprobar(r.elegir_mas_antiguos([], 3) == [] and len(r.elegir_mas_antiguos(info["anuncios"], 0)) == 1 and len(r.elegir_mas_antiguos(muchos, 99)) == 10 and len(r.elegir_mas_antiguos(muchos, 4)) == 4,
              "sin anuncios da [], y n se acota entre 1 y 10")
    comprobar(ids_de(r.elegir_mas_antiguos(muchos, 2)) == ["16000000000000", "16000000000001"], "entre muchos, los de fecha de inicio mas vieja")
    sin_fecha = dict(lista[0])
    sin_fecha.pop("start_date")
    comprobar(r.elegir_mas_antiguos([sin_fecha], 3) == [], "un anuncio sin fecha de inicio no se puede ordenar: no se elige")

    corto = r.resumen_corto(elegidos[0])
    crudo = json.dumps(corto)
    comprobar(corto["ad_id"] == L1 and corto["inicio"] == "2023-11-14" and corto["tiene_video"] is True and corto["copy_inicio"].startswith("Texto A") and "SECRETO" not in crudo and "http" not in crudo,
              "resumen_corto: datos utiles y ningun enlace firmado", crudo[:300])

    # guardar_referencia con un anuncio ya leido (sin volver a pedir su pagina)
    destino = tmp / "ref_lista"
    VISTO.clear()
    host_real = f"http://127.0.0.1:{SERVIDOR[0].server_port}"
    ad_local = anuncios_de_la_lista(host_real)[0]
    res = r.guardar_referencia(ad_local, L1, destino, "REF1")
    comprobar(res["video"] == "REF1_referencia.mp4" and (destino / "REF1_referencia.mp4").is_file() and (destino / "REF1_copy.txt").is_file() and (destino / "REF1_referencia.json").is_file(),
              "guardar_referencia: deja el video, el copy y los datos con el nombre pedido", str(res))
    comprobar(not any(v.startswith("GET /ads/library") for v in VISTO) and "SECRETO" not in (destino / "REF1_referencia.json").read_text(encoding="utf-8"),
              "sin pedir la pagina otra vez y sin enlaces firmados en los datos", str(VISTO))

    codigo, txt = correr_cli(r, ["anunciante", L3, "--n", "3"])
    comprobar(codigo == 0 and "Anunciante: Tienda de Prueba" in txt and "6 anuncio(s)" in txt and "5 con video" in txt and L1 in txt and L3 in txt and L6 in txt and L4 not in txt and "no hay mas paginas" in txt,
              "linea de comandos 'anunciante': cuenta los anuncios y lista los 3 mas antiguos", txt[-600:])
    codigo, txt = correr_cli(r, ["anunciante", "https://evil.com/x"])
    comprobar(codigo == 1 and "no es un enlace" in txt, "'anunciante' con un enlace ajeno: error claro y salida 1", txt[-200:])


def pruebas_pagina(r, tmp: Path) -> None:
    print("La pagina de un anunciante (con los filtros del dueño) y el problema del pais")
    MODO.update(reto=True, verify_429=0, limitado=False, pais_chico=False, pagina_vacia=False, pagina_hay_mas=False)
    url = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&is_targeted_country=false&media_type=all&search_type=page"
           "&sort_data[mode]=total_impressions&sort_data[direction]=desc&start_date[min]&start_date[max]=2026-09-16&view_all_page_id=555000111")
    p = r.pagina_de(url)
    comprobar(p == {"page_id": "555000111", "filtros": {"active_status": "active", "ad_type": "all", "country": "ALL", "is_targeted_country": "false", "media_type": "all",
                                                        "sort_data[mode]": "total_impressions", "sort_data[direction]": "desc", "start_date[max]": "2026-09-16"}},
              "pagina_de: saca el numero de la pagina y SOLO los filtros conocidos (el start_date[min] vacio se descarta)", str(p))
    comprobar(r.es_enlace_pagina(url) and r.es_enlace_meta(url) and not r.es_enlace_biblioteca(url) and r.es_enlace_meta(f"https://www.facebook.com/ads/library/?id={L3}") and not r.es_enlace_pagina(f"https://www.facebook.com/ads/library/?id={L3}"),
              "es_enlace_meta acepta el de un anuncio y el de una pagina; es_enlace_biblioteca (el que baja fabrica.py traer) sigue siendo solo el de un anuncio")
    raro = ("https://www.facebook.com/ads/library/?view_all_page_id=555000111&country=ZZZ9&sort_data[mode]=a;b&media_type=<script>&active_status=all&start_date[max]=ayer&evil=1&search_type=otro")
    pr = r.pagina_de(raro)
    comprobar(pr == {"page_id": "555000111", "filtros": {"active_status": "all"}}, "pagina_de: un filtro con un valor raro o desconocido NO viaja a Meta (solo active_status=all queda)", str(pr))
    for malo in ("https://evil.com/ads/library/?view_all_page_id=555000111", "https://www.facebook.com/otra/cosa?view_all_page_id=555000111", "https://www.facebook.com/ads/library/?view_all_page_id=abc",
                 "https://www.facebook.com/ads/library/?search_type=page", "ftp://www.facebook.com/ads/library/?view_all_page_id=555000111", "", "no es un enlace"):
        comprobar(r.pagina_de(malo) is None, f"pagina_de rechaza {malo[:55]!r}")

    VISTO.clear()
    info = r.anuncios_del_anunciante(url)
    ids = [a["ad_archive_id"] for a in info["anuncios"]]
    comprobar(info["anunciante"] == "Tienda de Prueba" and info["page_id"] == "555000111" and info["referencia_id"] == "" and ids == [L1, L2, L3, L4, L5, L6] and info["hay_mas"] is False,
              "con el enlace de la PAGINA: salen solo los anuncios de ese anunciante, sin anuncio de referencia", str((info["anunciante"], info["page_id"], ids)))
    pedida = [v for v in VISTO if v.startswith("GET /ads/library")][-1]
    comprobar(sum(1 for v in VISTO if v.startswith("GET /ads/library")) == 2 and "search_type" in pedida and "view_all_page_id" in pedida and "start_date[max]" in pedida and "sort_data[mode]" in pedida and pedida.split("?")[1].split(",").count("id") == 0,
              "y se pide la pagina de ese anunciante con SUS filtros (orden y fecha), sin id de anuncio (2 GET: el reto y la pagina)", str(VISTO))
    comprobar(info["filtros"]["start_date[max]"] == "2026-09-16" and info["filtros"]["country"] == "ALL", "la lista dice con que filtros se leyo")
    elegidos = r.elegir_mas_antiguos(info["anuncios"], 3)
    comprobar([a["ad_archive_id"] for a in elegidos] == [L1, L3, L6], "de esa lista se eligen los mas antiguos con video, como siempre", str([a["ad_archive_id"] for a in elegidos]))
    MODO["pagina_hay_mas"] = True
    comprobar(r.anuncios_del_anunciante(url)["hay_mas"] is True, "si Meta dice que hay mas paginas, lo avisa")
    MODO["pagina_hay_mas"] = False

    # el problema real: con el pais de un solo mercado solo se ve el anuncio pedido (un anunciante de LATAM salio con 1 anuncio de 52)
    MODO["pais_chico"] = True
    VISTO.clear()
    info = r.anuncios_del_anunciante(f"https://www.facebook.com/ads/library/?id={L3}", pais="BR")
    ids = [a["ad_archive_id"] for a in info["anuncios"]]
    comprobar(ids == [L1, L2, L3, L4, L5, L6] and info.get("amplia") is True and info["referencia_id"] == L3 and info["page_id"] == "555000111" and info["filtros"]["country"] == "ALL",
              "con el pais chico (la pagina del anuncio solo muestra el pedido) se usa la pagina del anunciante en todos los paises", str((ids, info.get("amplia"))))
    comprobar(any("view_all_page_id" in v and "sort_data[mode]" in v for v in VISTO), "y esa pagina se pide ordenada por impresiones", str(VISTO))
    info = r.anuncios_del_anunciante(f"https://www.facebook.com/ads/library/?id={L8}", pais="BR")
    comprobar([a["ad_archive_id"] for a in info["anuncios"]][:1] == [L8], "si el anuncio pedido no sale en la pagina del anunciante, se deja primero")

    # si la pagina del anunciante no trae nada o falla, se queda con lo que dio la del anuncio (sin error)
    MODO["pagina_vacia"] = True
    info = r.anuncios_del_anunciante(f"https://www.facebook.com/ads/library/?id={L3}", pais="BR")
    comprobar([a["ad_archive_id"] for a in info["anuncios"]] == [L3] and not info.get("amplia"), "si la pagina del anunciante no trae nada, se queda con el anuncio pedido (sin error)", str([a["ad_archive_id"] for a in info["anuncios"]]))
    try:
        r.anuncios_del_anunciante(url)
        comprobar(False, "un enlace de pagina sin anuncios da error claro")
    except r.ErrorMeta as e:
        comprobar("no trae ningun anuncio" in str(e), "un enlace de pagina sin anuncios da error claro", str(e)[:100])
    MODO.update(pais_chico=False, pagina_vacia=False)
    MODO["limitado"] = True
    try:
        r.anuncios_del_anunciante(url)
        comprobar(False, "si Meta limita la conexion, la pagina del anunciante lo dice")
    except r.ErrorMeta as e:
        comprobar("limito" in str(e), "si Meta limita la conexion, la pagina del anunciante lo dice", str(e)[:100])
    MODO["limitado"] = False

    codigo, txt = correr_cli(r, ["anunciante", url, "--n", "3"])
    comprobar(codigo == 0 and "Anunciante: Tienda de Prueba" in txt and "6 anuncio(s)" in txt and "5 con video" in txt and L1 in txt and L3 in txt and L6 in txt and L4 not in txt,
              "linea de comandos 'anunciante' con el enlace de una pagina: lista y elige los 3 mas antiguos", txt[-500:])
    codigo, txt = correr_cli(r, ["leer", url])
    comprobar(codigo == 1 and "no es un enlace" in txt, "'leer' con el enlace de una pagina (sin un anuncio concreto) da un error claro", txt[-200:])


def prueba_vivo(r, enlace: str) -> None:
    print(f"VIVO: leyendo de verdad {enlace}")
    try:
        ad, ad_id = r.leer(enlace)
    except r.ErrorMeta as e:
        comprobar(False, "la Biblioteca entrega el anuncio", str(e))
        return
    d = r.datos_de(ad, ad_id)
    comprobar(bool(r.copy_de(ad)), "el anuncio trae copy", d["formato"])
    comprobar(not d["tiene_video"] or r.elegir_video(ad) is not None, "si tiene video, hay un enlace HD o SD")
    print(f"         {d['anunciante']} | {d['formato']} | {d['dias_en_aire']} dias | {len(r.copy_de(ad))} caracteres de copy | video: {'si' if d['tiene_video'] else 'no'}")


def main() -> None:
    configurar_consola()
    vivo = None
    if "--vivo" in sys.argv:
        i = sys.argv.index("--vivo")
        vivo = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        if not vivo:
            print("Uso: python scripts/autoprueba_referencia.py --vivo https://www.facebook.com/ads/library/?id=…")
            sys.exit(1)
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), MetaFalso)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    os.environ["REFERENCIA_META_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    r = importlib.import_module("referencia_meta")
    comprobar(r.EN_PRUEBA and r.ADLIB.startswith("http://127.0.0.1:"), "la autoprueba habla con el Meta FALSO")
    os.environ["REFERENCIA_META_URL"] = "https://evil.example.com"
    comprobar(importlib.reload(r).ADLIB == "https://www.facebook.com/ads/library" and not r.EN_PRUEBA, "una direccion que no es de esta maquina se ignora")
    os.environ["REFERENCIA_META_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    r = importlib.reload(r)
    with tempfile.TemporaryDirectory(prefix="autoprueba_ref_") as t:
        tmp = Path(t)
        preparar_media(tmp)
        pruebas_puras(r)
        pruebas_cliente(r)
        pruebas_traer(r, tmp)
        SERVIDOR[:] = [servidor]
        pruebas_anunciante(r, tmp)
        pruebas_pagina(r, tmp)
    servidor.shutdown()
    if vivo:
        os.environ.pop("REFERENCIA_META_URL", None)
        prueba_vivo(importlib.reload(r), vivo)
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON {len(FALLAS)} FALLA(S):")
        for f in FALLAS:
            print("  -", f)
        sys.exit(1)
    print("AUTOPRUEBA OK")


if __name__ == "__main__":
    main()
