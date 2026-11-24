"""Autoprueba de scripts/apimart.py y apimart_chat.py SIN internet, sin gastar y sin ninguna llave de verdad.

Desde la version 2026.11.10-2 el editor pide todo a APIMart POR EL DASH (/api/editor-apimart). Esta prueba levanta en esta maquina:
  - un DASH FALSO que cumple el contrato de la puerta (subir, imagen, video, tarea, texto, estado, y las respuestas 401/402/409/426/429/503-reintentar y una redireccion),
    mas /api/editor-costos (para ver que lo hecho por el dash NUNCA se le vuelve a mandar) y los enlaces de los resultados;
  - un APIMart FALSO para el unico camino directo que queda (el de las autopruebas, con APIMART_BASE_URL a esta maquina).
Las corridas «de verdad» (por el dash) usan una INSTALACION de mentira en una carpeta temporal (copia de apimart.py, apimart_chat.py, _comun.py y VERSION, con su
propio .env): asi nunca leen el .env de esta carpeta ni avisan al dash de verdad, aunque se corra dentro de una instalacion.
En Windows prueba ademas Conectar-Editor.ps1 (pasar conexion-editor.txt a .env) y que el empaquetador nunca lleve ese archivo.

Uso:  python scripts/autoprueba_apimart.py
Corre esto despues de tocar apimart.py, apimart_chat.py, Editor-Comun.ps1 o Conectar-Editor.ps1. Si todo va bien termina con "AUTOPRUEBA OK".
"""
from __future__ import annotations

import base64
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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import RAIZ, configurar_consola  # noqa: E402

CLAVE_DASH = "clave-dash-de-prueba-0123456789"
CLAVE_APIMART_FALSA = "clave-de-prueba"                       # la del APIMart falso (camino directo de las autopruebas)
LLAVE_QUE_NO_DEBE_SALIR = "sk-apimart-que-no-debe-salir-0123456789"   # una llave de APIMart puesta en el entorno en uso real: nadie la manda ni la imprime
VERSION = (RAIZ / "VERSION").read_text(encoding="utf-8").strip()
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c9999000000070001e221bc999900000089454e44ae426082")
TOTAL = 0
# Prompts que CUMPLEN la plantilla (revisar_prompts.py: apimart.py no pide nada con un prompt que no cumple; eso lo prueba autoprueba_revisar_prompts.py).
PROMPT_IMAGEN = ("Three-quarter angle, medium shot, in her small kitchen next to the stove, she is pouring hot chocolate into a red mug, smiling at the camera. Candid still from a "
                 "home video filmed on a phone, natural light. No text, no readable brand names, no logos, no watermark.")
PROMPT_VELA = ("Close-up from above, on the wooden kitchen table, her hands are placing a white candle in a glass jar. Nobody's face is visible. Candid still from a home video "
               "filmed on a phone, natural light. No text, no readable brand names, no logos, no watermark.")
PROMPT_CLIP = ('(00:00-00:01) She is already folding a felt star on the table. She looks into the lens and says, warmly and naturally, with small hand gestures: "Y lo mejor es que '
               'no necesitas experiencia: los moldes vienen listos para imprimir y en una tarde tienes tu arbol lleno de adornos bonitos." (Spanish, lip-synced).\n'
               "UGC LOOK: real home video filmed on a phone, natural light, she keeps doing the action with her hands while she talks.\nSTABLE OBJECT CONSISTENCY: the felt star "
               "keeps its shape.\nVOICE: a warm, friendly female voice in her thirties.\nACCENT: Colombian Spanish with a clear paisa accent from Medellin.\nPACE: natural, "
               "slightly brisk conversational pace.\nRESTRICTIONS: lip-synced dialogue only in Spanish, no background music, no captions or on-screen text.")
# ---- las MISMAS reglas de forma que la puerta de verdad (functions/_editor-apimart.js): un pedido que el dash negaria tambien falla aqui, y queda en RECHAZOS
RECHAZOS: list[dict] = []
RE_PRODUCTO = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
RE_ANUNCIO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,15}")
RE_ARCHIVO = re.compile(r"[^\W_][\w .()-]{0,79}")
RE_DATA_URI = re.compile(r"data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/]{16}")
RE_URL = re.compile(r"https://[^\s\"'<>\\]{1,2040}")
MODELOS_TEXTO = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite-preview", "gpt-5-nano", "gpt-4.1-nano")
CAMPOS_QUITABLES = ("response_format", "max_tokens", "temperature", "max_completion_tokens")


def _texto_corto(v, maximo: int):
    if v is None or v == "":
        return ""
    if not isinstance(v, str):
        return None
    limpio = " ".join(re.sub(r"[\x00-\x1f\x7f|]+", " ", v).split())
    return None if len(limpio) > maximo else limpio


def _numero(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _url_https(u) -> bool:
    return isinstance(u, str) and len(u) <= 2048 and bool(RE_URL.fullmatch(u))


def regla_del_dash(accion: str, consulta: dict, cuerpo: dict, crudo: bytes):
    """(codigo, error) si el dash de verdad negaria este pedido por su forma; None si pasa."""
    if accion == "estado":
        pr, an = consulta.get("producto", ""), consulta.get("anuncio", "")
        if (pr or an) and not (RE_PRODUCTO.fullmatch(pr) and RE_ANUNCIO.fullmatch(an)):
            return 400, "«producto» y «anuncio» van juntos y bien escritos."
        return None
    if accion == "subir":
        if not (RE_PRODUCTO.fullmatch(consulta.get("producto", "")) and RE_ANUNCIO.fullmatch(consulta.get("anuncio", ""))):
            return 400, "Faltan «producto» y «anuncio» (para qué es la imagen)."
        if not crudo:
            return 400, "La imagen llegó vacía."
        if len(crudo) > 10 * 1024 * 1024:
            return 413, "La imagen pesa más de 10 MB: achícala antes."
        if not (crudo[:8] == PNG[:8] or crudo[:3] == b"\xff\xd8\xff" or (crudo[:4] == b"RIFF" and crudo[8:12] == b"WEBP")):
            return 415, "Solo PNG, JPEG o WEBP (se mira por los primeros bytes del archivo)."
        return None
    if accion == "tarea":
        return None if isinstance(cuerpo.get("ticket"), str) and cuerpo["ticket"] else (401, "Ticket no válido")
    if accion not in ("imagen", "video", "texto"):
        return 400, "Accion no reconocida (imagen, video, tarea o texto)"
    pr, an = cuerpo.get("producto"), cuerpo.get("anuncio")
    if not isinstance(pr, str) or not RE_PRODUCTO.fullmatch(pr.strip()):
        return 400, "Falta «producto» (la oferta, en minúsculas: letras, números, - y _; hasta 41)."
    if not isinstance(an, str) or not RE_ANUNCIO.fullmatch(an.strip()):
        return 400, "Falta «anuncio» (el ID del video: letras, números, - y _; hasta 16)."
    if _texto_corto(cuerpo.get("etiqueta"), 40) is None:
        return 400, "«etiqueta» es un texto de hasta 40 letras."
    if accion == "texto":
        if cuerpo.get("modelo") not in MODELOS_TEXTO:
            return 400, "modelo no permitido: usa uno de " + ", ".join(MODELOS_TEXTO) + "."
        sistema = "" if cuerpo.get("sistema") is None else cuerpo.get("sistema")
        if not isinstance(sistema, str) or len(sistema) > 20000:
            return 400, "«sistema» es un texto de hasta 20000 letras."
        usuario = cuerpo.get("usuario")
        if not isinstance(usuario, str) or not usuario.strip() or len(usuario) > 60000:
            return 400, "«usuario» es un texto de 1 a 60000 letras."
        imagenes = [] if cuerpo.get("imagenes") is None else cuerpo.get("imagenes")
        if not isinstance(imagenes, list) or len(imagenes) > 6:
            return 400, "«imagenes» es una lista de hasta 6 fotogramas."
        if not all(isinstance(x, str) and len(x) <= 400000 and RE_DATA_URI.match(x) and re.fullmatch(r"[A-Za-z0-9+/=]*", x[-8:]) for x in imagenes):
            return 400, "Cada fotograma es data:image/jpeg, png o webp en base64, de hasta 400000 caracteres."
        mt = 1800 if cuerpo.get("max_tokens") is None else _numero(cuerpo.get("max_tokens"))
        if mt is None or mt != int(mt) or not 1 <= mt <= 6000:
            return 400, "«max_tokens» va de 1 a 6000."
        te = 0.4 if cuerpo.get("temperatura") is None else _numero(cuerpo.get("temperatura"))
        if te is None or not 0 <= te <= 2:
            return 400, "«temperatura» va de 0 a 2."
        if cuerpo.get("json_modo") is not None and not isinstance(cuerpo.get("json_modo"), bool):
            return 400, "«json_modo» es true o false."
        quitar = [] if cuerpo.get("quitar") is None else cuerpo.get("quitar")
        if not isinstance(quitar, list) or not all(c in CAMPOS_QUITABLES for c in quitar):
            return 400, "«quitar» solo puede nombrar: " + ", ".join(CAMPOS_QUITABLES) + "."
        if "max_tokens" in quitar and "max_completion_tokens" in quitar:
            return 400, ("No se pueden quitar los dos límites de respuesta (max_tokens y max_completion_tokens): sin ninguno, la respuesta y el costo no tienen techo. "
                         "Prueba con otro modelo.")
        return None
    archivo = cuerpo.get("archivo").strip() if isinstance(cuerpo.get("archivo"), str) else ""
    if not RE_ARCHIVO.fullmatch(archivo):
        return 400, "«archivo» es solo el nombre del archivo de salida (sin carpetas), hasta 80 letras."
    prompt = cuerpo.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 12000:
        return 400, "Falta el prompt (o pasa de 12000 letras)."
    tamano = "9:16" if cuerpo.get("tamano") in (None, "") else cuerpo.get("tamano")
    if accion == "imagen":
        resolucion = "1k" if cuerpo.get("resolucion") in (None, "") else cuerpo.get("resolucion")
        if tamano != "9:16" or resolucion != "1k":
            return 400, "La imagen va en 9:16 y 1k (el precio estimado es para eso)."
        refs = cuerpo.get("imagenes")
        if refs is not None and (not isinstance(refs, list) or len(refs) > 15):
            return 400, "«imagenes» es una lista de hasta 15 direcciones https."
        if not all(_url_https(u) for u in (refs or [])):
            return 400, "Cada referencia es una dirección https (súbela antes con ?subir=1): nada de data: ni http."
    else:
        if not _url_https(cuerpo.get("imagen")):
            return 400, "Falta «imagen»: el cuadro inicial como dirección https (súbelo antes con ?subir=1)."
        dur = 8 if cuerpo.get("duracion") in (None, "") else _numero(cuerpo.get("duracion"))
        if dur is None or dur != int(dur) or not 6 <= dur <= 15:
            return 400, "«duracion» va de 6 a 15 segundos (entero)."
        if ("480p" if cuerpo.get("resolucion") in (None, "") else cuerpo.get("resolucion")) not in ("480p", "720p"):
            return 400, "«resolucion» es 480p o 720p."
        if tamano != "9:16":
            return 400, "El clip va en 9:16."
    if cuerpo.get("topeAnuncio") not in (None, ""):
        tope = _numero(cuerpo.get("topeAnuncio"))
        if tope is None or not 0 < tope <= 1:
            return 400, "El tope de un anuncio va de más de 0 a US$ 1,00 como máximo (orden del dueño)."
        if tope > 0.5:
            motivo = _texto_corto(cuerpo.get("motivoTope"), 200)
            if motivo is None:
                return 400, "«motivoTope» es un texto de hasta 200 letras."
            if not motivo:
                return 400, "Para subir el tope del anuncio por encima de US$ 0,50 hace falta «motivoTope» (por qué): queda anotado."
    if _texto_corto(cuerpo.get("repetir"), 200) is None:
        return 400, "«repetir» es el motivo, de hasta 200 letras."
    return None


PUERTO = 0


def _wav_valido() -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 4800)
    return buf.getvalue()


WAV = _wav_valido()

# ------------------------------------------------------------------ lo que ven los servidores falsos
APIMART_VISTO: list[dict] = []     # camino directo (/v1/...)
SONDEOS: dict[str, int] = {}
DASH: list[dict] = []              # cada pedido a /api/editor-apimart: metodo, consulta, clave, version, cuerpo
COSTOS_DASH: list[dict] = []       # lo que llega a /api/editor-costos
ESPIA: list = []                   # a donde llevaria una redireccion: si aqui llega una llave, se filtro
DESCARGAS: list[dict] = []         # las bajadas de resultados: no deben llevar la llave del editor
AUTORIZACIONES: list[str] = []     # toda cabecera Authorization que llego a cualquier ruta
TAREAS: dict[str, dict] = {}
M: dict = {}                       # modo del dash falso (errores a pedido)


def modo_por_defecto() -> None:
    M.clear()
    M.update(minima=VERSION, minima_iv=VERSION, errores=[], reintentar=0, redirige=False, procesando=0, fallar_tarea=False, sin_costo=False, texto=[], descarga_rota=False)


def _tupla(v: str):
    m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})-(\d{1,4})", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


class Falso(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencio
        pass

    def _json(self, codigo: int, cuerpo: dict) -> None:
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _crudo(self, tipo: str, datos: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _cuerpo(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

    def _anotar_auth(self) -> None:
        if self.headers.get("Authorization"):
            AUTORIZACIONES.append(self.headers.get("Authorization"))

    # ---- GET
    def do_GET(self):
        self._anotar_auth()
        if self.path.startswith("/files/"):
            DESCARGAS.append({"ruta": self.path, "clave": self.headers.get("x-clave-editor"), "version": self.headers.get("x-version-editor")})
            if "vencido" in self.path:
                return self._json(404, {"error": "el enlace ya no existe"})
            return self._crudo("application/octet-stream", PNG if self.path.endswith(".png") else b"VIDEO-FALSO" * 50)
        if self.path.startswith("/api/editor-apimart"):
            return self._dash("GET", b"")
        return self._apimart_get()

    def do_POST(self):
        self._anotar_auth()
        cuerpo = self._cuerpo()
        if self.path == "/espia":
            ESPIA.append(self.headers.get("x-clave-editor"))
            return self._json(200, {"ok": True})
        if self.path == "/api/editor-costos":
            entradas = json.loads(cuerpo or b"{}").get("entradas", [])
            COSTOS_DASH.append({"clave": self.headers.get("x-clave-editor"), "entradas": entradas})
            if self.headers.get("x-clave-editor") != CLAVE_DASH:
                return self._json(401, {"ok": False})
            return self._json(200, {"ok": True, "nuevas": len(entradas), "repetidas": 0, "sinCosto": 0, "rechazadas": [], "fallidas": 0})
        if self.path.startswith("/api/editor-apimart"):
            return self._dash("POST", cuerpo)
        return self._apimart_post(cuerpo)

    # ---- el dash falso: la puerta /api/editor-apimart
    def _dash(self, metodo: str, crudo: bytes):
        partes = urlsplit(self.path)
        consulta = {k: v[0] for k, v in parse_qs(partes.query).items()}
        es_json = metodo == "POST" and "subir" not in consulta
        try:
            cuerpo = json.loads(crudo or b"{}") if es_json else {}
        except ValueError:
            cuerpo = {}
        visto = {"metodo": metodo, "consulta": consulta, "clave": self.headers.get("x-clave-editor"), "version": self.headers.get("x-version-editor"),
                 "tipo": self.headers.get("Content-Type"), "cuerpo": cuerpo, "bytes": crudo if not es_json else b""}
        DASH.append(visto)
        if M["redirige"]:
            self.send_response(307)             # 307 conserva el metodo, el cuerpo y las cabeceras: el peor caso para la llave
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/espia")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("x-clave-editor") != CLAVE_DASH:
            return self._json(401, {"ok": False, "error": "La llave del editor no es valida."})
        v, minima = _tupla(self.headers.get("x-version-editor") or ""), _tupla(M["minima"])
        if v is None or v < minima:
            return self._json(426, {"ok": False, "error": "Actualiza el editor de anuncios: esta copia es vieja.", "versionMinima": M["minima"]})
        accion = "estado" if metodo == "GET" else ("subir" if "subir" in consulta else str(cuerpo.get("accion")))
        regla = regla_del_dash(accion, consulta, cuerpo, crudo if not es_json else b"")
        if regla:
            RECHAZOS.append({"accion": accion, "codigo": regla[0], "error": regla[1]})
            return self._json(regla[0], {"ok": False, "error": regla[1]})
        for i, (acc, codigo, resp) in enumerate(M["errores"]):
            if acc == accion:
                M["errores"].pop(i)
                return self._json(codigo, resp)
        if accion in ("imagen", "video", "subir") and M["reintentar"] > 0:
            M["reintentar"] -= 1
            return self._json(503, {"ok": False, "error": "No pude anotar la reserva: no se pidio nada.", "reintentar": True})
        host = f"http://127.0.0.1:{self.server.server_port}"
        if accion == "estado":
            r = {"ok": True, "versionMinima": M["minima"], "versionMinimaImagenVideo": M["minima_iv"], "topeAnuncioUsd": 0.5, "techoAnuncioUsd": 1, "topeDiaUsd": 5, "gastadoHoyUsd": 1.25,
                 "pedidosHoy": 12, "maxPedidosDia": 150, "subidasHoy": 3, "maxSubidasDia": 100,
                 "modelos": {"imagen": "gpt-image-2", "video": "grok-imagine-1.5-video-ext", "texto": ["gemini-3.5-flash-lite", "gpt-5-nano"]}}
            if consulta.get("producto") and consulta.get("anuncio", "").startswith("NUEVO"):
                # como el dash con un anuncio SIN ficha (no ha pedido nada por la puerta): no recorre el libro, asi que el legado viene sin calcular
                r["anuncio"] = {"gastadoUsd": 0, "legadoUsd": None, "legadoSinCalcular": True, "clipsPagados": [], "repetidos": 0}
            elif consulta.get("producto") and consulta.get("anuncio"):
                r["anuncio"] = {"gastadoUsd": 0.15, "legadoUsd": 0.05, "legadoSinCalcular": False, "clipsPagados": ["01.mp4"], "repetidos": 0}     # como el dash: gastadoUsd YA incluye el legado
            return self._json(200, r)
        if accion == "subir":
            n = sum(1 for d in DASH if d["consulta"].get("subir"))
            return self._json(200, {"ok": True, "url": f"https://subidas.apimart-falso.test/s{n}.png", "expira": int(time.time()) + 70 * 3600})
        if accion in ("imagen", "video"):
            n = len(TAREAS) + 1
            tid = f"dash_{'img' if accion == 'imagen' else 'vid'}{n}"
            ticket = base64.urlsafe_b64encode(json.dumps({"v": 1, "k": tid}).encode()).decode().rstrip("=") + ".firma-falsa"
            TAREAS[tid] = {"ticket": ticket, "tipo": accion, "sondeos": 0}
            return self._json(200, {"ok": True, "tarea": {"id": tid, "ticket": ticket}, "estimadoUsd": 0.0085 if accion == "imagen" else 0.0816, "gastadoAnuncioUsd": 0.0, "topeUsd": 0.5})
        if accion == "tarea":
            tid = next((k for k, t in TAREAS.items() if t["ticket"] == cuerpo.get("ticket")), None)
            if tid is None:
                return self._json(401, {"ok": False, "error": "Ticket con firma mala."})
            t = TAREAS[tid]
            t["sondeos"] += 1
            if t["sondeos"] <= M["procesando"]:
                return self._json(200, {"ok": True, "estado": "processing", "avance": 40, "urls": []})
            if M["fallar_tarea"]:
                return self._json(200, {"ok": True, "estado": "failed", "avance": 100, "urls": [], "error": "contenido rechazado por el modelo"})
            url = f"{host}/files/{tid}{'_vencido' if M['descarga_rota'] else ''}." + ("png" if t["tipo"] == "imagen" else "mp4")
            r = {"ok": True, "estado": "completed", "avance": 100, "urls": [url]}
            if not M["sin_costo"]:
                r["costoUsd"] = 0.0085 if t["tipo"] == "imagen" else 0.0816
            return self._json(200, r)
        if accion == "texto":
            if cuerpo.get("modelo") not in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite-preview", "gpt-5-nano", "gpt-4.1-nano"):
                return self._json(400, {"ok": False, "error": "modelo no permitido"})
            if M["texto"]:
                status, resp = M["texto"].pop(0)
                return self._json(200, {"ok": True, "status": status, "cuerpo": json.dumps(resp), "costoEstimadoUsd": 0})
            resp = {"choices": [{"message": {"content": '{"respuesta": "hola"}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
            return self._json(200, {"ok": True, "status": 200, "cuerpo": json.dumps(resp), "costoEstimadoUsd": 0.00022})
        return self._json(400, {"ok": False, "error": "Accion no reconocida"})

    # ---- el APIMart falso (camino directo, solo autopruebas)
    def _apimart_get(self):
        if self.headers.get("Authorization") != f"Bearer {CLAVE_APIMART_FALSA}":
            return self._json(401, {"error": {"code": 401, "message": "llave mala", "type": "authentication_error"}})
        APIMART_VISTO.append({"ruta": self.path.split("?")[0]})
        if self.path.startswith("/v1/user/balance"):
            return self._json(200, {"success": True, "remain_balance": 12.5, "remain_credits": 125, "used_balance": 3.2, "used_credits": 32})
        if self.path.startswith("/v1/tasks/"):
            tid = self.path.split("/v1/tasks/")[1].split("?")[0]
            SONDEOS[tid] = SONDEOS.get(tid, 0) + 1
            host = f"http://127.0.0.1:{self.server.server_port}"
            resultado = {"images": [{"url": [f"{host}/files/x.png"], "expires_at": 4102444800}]} if tid.startswith("task_img") else {"videos": [{"url": [f"{host}/files/x.mp4"]}]}
            return self._json(200, {"code": 200, "data": {"id": tid, "status": "completed", "progress": 100, "cost": 0.05, "credits_cost": 0.5, "result": resultado}})
        if self.path.startswith("/v1/models"):
            return self._json(200, {"data": [{"id": "gemini-3.5-flash-lite"}]})
        self._json(404, {"error": {"code": 404, "message": "no existe", "type": "x"}})

    def _apimart_post(self, cuerpo: bytes):
        if self.headers.get("Authorization") != f"Bearer {CLAVE_APIMART_FALSA}":
            return self._json(401, {"error": {"code": 401, "message": "llave mala", "type": "authentication_error"}})
        if self.path == "/v1/uploads/images":
            APIMART_VISTO.append({"ruta": self.path, "multipart": b"filename=" in cuerpo})
            return self._json(200, {"url": f"http://127.0.0.1:{self.server.server_port}/files/subida.png"})
        datos = json.loads(cuerpo or b"{}")
        APIMART_VISTO.append({"ruta": self.path, **datos})
        if self.path == "/v1/images/generations":
            return self._json(200, {"code": 200, "data": [{"status": "submitted", "task_id": "task_img1"}]})
        if self.path == "/v1/videos/generations":
            return self._json(200, {"code": 200, "data": [{"status": "submitted", "task_id": "task_vid1"}]})
        if self.path == "/v1/audio/speech":
            return self._crudo("audio/wav", WAV)
        if self.path == "/v1/chat/completions":
            return self._json(200, {"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        self._json(404, {"error": {"code": 404, "message": "no existe", "type": "x"}})


# ------------------------------------------------------------------ ayudas
def exigir(condicion: bool, mensaje: str, salida: subprocess.CompletedProcess | str | None = None) -> None:
    global TOTAL
    TOTAL += 1
    if not condicion:
        if isinstance(salida, str):
            detalle = f"\n--- detalle:\n{salida}"
        else:
            detalle = f"\n--- stdout:\n{salida.stdout}\n--- stderr:\n{salida.stderr}" if salida is not None else ""
        print(f"FALLO: {mensaje}{detalle}")
        sys.exit(1)
    print(f"  ok  {mensaje}")


def seccion(titulo: str) -> None:
    print(f"\n{titulo}")


SIN_HERENCIA = ("APIMART_BASE_URL", "APIMART_API_KEY", "APIMART_PRUEBA_DIR", "DASH_URL", "DASH_CLAVE_EDITOR", "EDITOR_EXIGIR_DASH")


def env_limpio(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SIN_HERENCIA}
    env.update({"PYTHONIOENCODING": "utf-8", "EDITOR_SIN_VENV": "1", **(extra or {})})
    return env


def directo(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """apimart.py de esta carpeta con el APIMart FALSO (el unico camino directo que queda)."""
    env = env_limpio({"APIMART_API_KEY": CLAVE_APIMART_FALSA, "APIMART_BASE_URL": f"http://127.0.0.1:{PUERTO}/v1", "APIMART_PRUEBA_DIR": str(TRABAJO / "directo"), **(env_extra or {})})
    return subprocess.run([sys.executable, str(RAIZ / "scripts" / "apimart.py"), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=RAIZ, timeout=180)


def instalacion(nombre: str, env_texto: str | None = None) -> Path:
    """Una instalacion de mentira: copia de los scripts del cliente, el VERSION de verdad y su propio .env (None = sin .env: el editor no esta conectado)."""
    d = TRABAJO / nombre
    (d / "scripts").mkdir(parents=True)
    for s in ("apimart.py", "apimart_chat.py", "_comun.py", "revisar_prompts.py"):
        shutil.copy2(RAIZ / "scripts" / s, d / "scripts" / s)
    shutil.copy2(RAIZ / "VERSION", d / "VERSION")
    if env_texto is not None:
        (d / ".env").write_text(env_texto, encoding="utf-8")
    return d


def env_conectado() -> str:
    return f"SCRAPECREATORS_API_KEY=otra\nDASH_URL=http://127.0.0.1:{PUERTO}\nDASH_CLAVE_EDITOR={CLAVE_DASH}\n"


def por_dash(inst: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """apimart.py de la instalacion de mentira, en USO REAL (sin APIMart falso): todo va por el dash falso que dice su .env."""
    return subprocess.run([sys.executable, str(inst / "scripts" / "apimart.py"), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env_limpio(env_extra), cwd=inst, timeout=180)


def filas(ruta: Path) -> list[dict]:
    return [json.loads(x) for x in ruta.read_text(encoding="utf-8").splitlines() if x.strip()] if ruta.is_file() else []


def acciones(desde: int = 0) -> list[str]:
    return [("estado" if d["metodo"] == "GET" else "subir" if d["consulta"].get("subir") else str(d["cuerpo"].get("accion"))) for d in DASH[desde:]]


def todo_el_texto(*rutas: Path) -> str:
    return "".join(p.read_text(encoding="utf-8", errors="replace") for r in rutas for p in ([r] if r.is_file() else (r.rglob("*") if r.is_dir() else [])) if p.is_file()
                   and p.suffix in (".json", ".jsonl", ".md", ".txt"))


# ------------------------------------------------------------------ 1. el camino directo (solo autopruebas) y su guardia
def pruebas_directo() -> None:
    seccion("1. Camino directo: SOLO con el APIMart falso de las autopruebas")
    trabajo = TRABAJO / "directo"
    trabajo.mkdir()
    (trabajo / "avatar.png").write_bytes(PNG)
    (trabajo / "prompt.txt").write_text(PROMPT_IMAGEN, encoding="utf-8")
    (trabajo / "prompt_clip.txt").write_text(PROMPT_CLIP, encoding="utf-8")
    r = directo("saldo")
    exigir(r.returncode == 0 and "12.5" in r.stdout, "con el APIMart falso, saldo lee el saldo de la cuenta (como antes)", r)
    r = directo("imagen", "--prompt-archivo", str(trabajo / "prompt.txt"), "--ref", str(trabajo / "avatar.png"), "--salida", str(trabajo / "cuadros" / "01.png"), "--anuncio", "ADX", "--producto", "velas")
    enviado = next((v for v in APIMART_VISTO if v["ruta"] == "/v1/images/generations"), {})
    exigir(r.returncode == 0 and (trabajo / "cuadros" / "01.png").read_bytes() == PNG and enviado.get("model") == "gpt-image-2" and enviado["image_urls"][0].startswith("data:image/png;base64,"),
           "imagen directa: envia (la referencia como data URI), espera y descarga", r)
    r = directo("video", "--imagen", str(trabajo / "avatar.png"), "--prompt-archivo", str(trabajo / "prompt_clip.txt"), "--salida", str(trabajo / "clips" / "02.mp4"), "--anuncio", "ADX", "--producto", "velas")
    exigir(r.returncode == 0 and (trabajo / "clips" / "02.mp4").is_file() and any(v["ruta"] == "/v1/uploads/images" and v["multipart"] for v in APIMART_VISTO), "video directo: sube el cuadro (multipart) y descarga", r)
    r = directo("voz", "--texto", "Hola, prueba.", "--salida", str(trabajo / "voz.mp3"), "--anuncio", "ADX", "--producto", "velas")
    exigir(r.returncode == 0 and (trabajo / "voz.mp3").is_file(), "voz directa: solo existe con el APIMart falso (pruebas)", r)
    r = directo("estado", "task_img1")
    exigir(r.returncode == 0 and "completed" in r.stdout, "estado directo consulta un trabajo", r)
    f = filas(trabajo / "costos.jsonl")
    exigir({x["tipo"] for x in f} == {"imagen", "video", "voz"} and not any(x.get("via") for x in f), "en directo, costos.jsonl anota imagen, video y voz sin «via» (no los hizo el dash)")
    r = directo("imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(trabajo / "d.png"), "--anuncio", "ADY", "--producto", "velas", env_extra={"DASH_URL": f"http://127.0.0.1:{PUERTO}", "DASH_CLAVE_EDITOR": CLAVE_DASH})
    exigir(r.returncode == 0 and COSTOS_DASH and COSTOS_DASH[-1]["entradas"][0]["costo"] == 0.05, "en directo, lo que NO hizo el dash si se avisa a /api/editor-costos (como antes)", r)
    exigir(CLAVE_APIMART_FALSA not in (r.stdout + r.stderr) + todo_el_texto(trabajo), "la llave del APIMart falso no aparece en ninguna salida ni archivo")

    # la guardia: en uso real NO hay forma de llamar a APIMart directo
    for k in ("APIMART_BASE_URL", "APIMART_API_KEY", "APIMART_PRUEBA_DIR"):
        os.environ.pop(k, None)
    A = importlib.import_module("apimart")
    n = len(APIMART_VISTO)
    exigir(A.BASE == A.BASE_REAL and not A.modo_directo(), "sin APIMART_BASE_URL local el modo es el real (por el dash)")
    for intento in (lambda: A.pedir("GET", "/user/balance", CLAVE_APIMART_FALSA), A.cargar_llave):
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                intento()
            exigir(False, "en uso real, pedir() y cargar_llave() se niegan")
        except SystemExit:
            exigir("todo pasa por el dash" in err.getvalue(), "en uso real, pedir() y cargar_llave() se niegan y dicen que todo pasa por el dash")
    exigir(len(APIMART_VISTO) == n, "en uso real, pedir() y cargar_llave() se niegan ANTES de abrir ninguna conexion")
    for base, esperado in ((f"http://127.0.0.1:{PUERTO}/v1", True), ("https://api.apimart.ai/v1", False), ("https://otro.test/v1", False), (f"http://127.0.0.1:{PUERTO}@otro.test/v1", False),
                           ("http://10.0.0.5:80/v1", False)):
        A.BASE = base
        exigir(A.modo_directo() is esperado, f"modo directo con APIMART_BASE_URL={base}: {'si' if esperado else 'no'}")
    A.BASE = A.BASE_REAL
    exigir(not A.es_local(f"http://127.0.0.1:{PUERTO}@otro.test/v1") and A.es_local("http://127.0.0.1:5/v1"), "es_local mira la direccion ya separada (127.0.0.1:1@otro.test va a otro.test)")


# ------------------------------------------------------------------ 2. por el dash (uso real)
def pruebas_dash() -> None:
    seccion("2. Uso real: imagenes, clips, subidas, consultas y saldo POR EL DASH")
    inst = instalacion("real", env_conectado())
    t = TRABAJO / "trabajo_real"
    t.mkdir()
    (t / "avatar.png").write_bytes(PNG)
    (t / "prompt.txt").write_text(PROMPT_VELA, encoding="utf-8")
    (t / "prompt_clip.txt").write_text(PROMPT_CLIP, encoding="utf-8")
    costos, tickets, subidas = inst / "costos.jsonl", inst / "anuncios" / "_apimart" / "tickets.json", inst / "anuncios" / "_apimart" / "subidas.json"
    salidas: list[str] = []

    d0 = len(DASH)
    r = por_dash(inst, "saldo", env_extra={"APIMART_API_KEY": LLAVE_QUE_NO_DEBE_SALIR})
    salidas.append(r.stdout + r.stderr)
    exigir(r.returncode == 0 and "Tope del dia del editor: US$ 5.00" in r.stdout and "queda: US$ 3.75" in r.stdout and VERSION in r.stdout and acciones(d0) == ["estado"],
           "saldo pregunta al dash su estado (tope del dia, lo que queda, version minima): ya no el saldo de la cuenta", r)
    r = por_dash(inst, "saldo", "--producto", "velas", "--anuncio", "ADX")
    exigir(r.returncode == 0 and "Anuncio ADX de velas" in r.stdout and "1 clip(s) pagado(s)" in r.stdout and DASH[-1]["consulta"].get("anuncio") == "ADX"
           and "gastado US$ 0.150 (incluye US$ 0.050" in r.stdout and "texto: gemini-3.5-flash-lite, gpt-5-nano" in r.stdout,
           "saldo con --producto y --anuncio dice lo gastado en ese anuncio (el legado ya va incluido, no se suma dos veces)", r)
    r = por_dash(inst, "saldo", "--producto", "velas", "--anuncio", "NUEVO1")
    exigir(r.returncode == 0 and "Anuncio NUEVO1 de velas: gastado US$ 0.000 (lo de antes de la puerta todavia no esta sumado" in r.stdout and "incluye US$" not in r.stdout,
           "saldo de un anuncio sin ficha en el dash (legadoUsd null): no dice «incluye US$ 0.000», dice que lo de antes se suma en el primer pedido", r)
    # imagenes y clips piden una version mas nueva que el resto (versionMinimaImagenVideo del estado): saldo la dice y avisa si este editor no alcanza
    M["minima"] = "2026.01.01-1"
    r = por_dash(inst, "saldo")
    exigir(r.returncode == 0 and f"el dash pide como minimo 2026.01.01-1; para imagenes y clips, {VERSION})" in r.stdout and "OJO" not in r.stdout,
           "saldo dice la version minima general y la de imagenes y clips (al dia: sin aviso)", r)
    M["minima_iv"] = "2099.01.01-1"
    r = por_dash(inst, "saldo")
    exigir(r.returncode == 0 and "para imagenes y clips, 2099.01.01-1" in r.stdout and "NO deja pedir imagenes ni clips" in r.stdout and "Instalar-Editor.cmd" in r.stdout,
           "saldo avisa que este editor no puede pedir imagenes ni clips (426) aunque este conectado", r)
    M["minima"] = M["minima_iv"] = VERSION

    # imagen con una referencia local: se sube por el dash (una sola vez) y la generacion lleva su URL
    d0, n_tareas = len(DASH), len(TAREAS)
    r = por_dash(inst, "imagen", "--prompt-archivo", str(t / "prompt.txt"), "--ref", str(t / "avatar.png"), "--ref", "https://ya.publica.test/foto.jpg", "--salida", str(t / "cuadros" / "01.png"),
                 "--producto", "velas", "--anuncio", "ADX", "--etiqueta", "cuadro01")
    salidas.append(r.stdout + r.stderr)
    exigir(r.returncode == 0 and (t / "cuadros" / "01.png").read_bytes() == PNG, "imagen por el dash: pide, consulta con el ticket y baja el resultado", r)
    exigir(acciones(d0) == ["subir", "imagen", "tarea"], f"imagen: sube la referencia, pide la imagen y consulta la tarea (visto: {acciones(d0)})")
    sub = DASH[d0]
    exigir(sub["bytes"] == PNG and sub["tipo"] == "image/png" and sub["consulta"] == {"subir": "1", "producto": "velas", "anuncio": "ADX"} and sub["metodo"] == "POST",
           "subir: los bytes tal cual, con su Content-Type y ?subir=1&producto=&anuncio=")
    img = DASH[d0 + 1]["cuerpo"]
    exigir(img.get("producto") == "velas" and img.get("anuncio") == "ADX" and img.get("etiqueta") == "cuadro01" and img.get("archivo") == "01.png" and img.get("tamano") == "9:16"
           and img.get("resolucion") == "1k" and "a white candle in a glass jar" in img.get("prompt", "") and img.get("imagenes") == ["https://subidas.apimart-falso.test/s1.png", "https://ya.publica.test/foto.jpg"]
           and "topeAnuncio" not in img and "model" not in img, "imagen: producto, anuncio, etiqueta, archivo, prompt, 9:16, 1k y las referencias como URL (la local subida, la publica tal cual)", )
    tid = list(TAREAS)[-1] if len(TAREAS) > n_tareas else "?"
    exigir(DASH[d0 + 2]["cuerpo"] == {"accion": "tarea", "ticket": TAREAS[tid]["ticket"]}, "la consulta de la tarea lleva SOLO su ticket firmado")
    tk = json.loads(tickets.read_text(encoding="utf-8"))
    exigir(tk.get(tid, {}).get("ticket") == TAREAS[tid]["ticket"] and tk[tid]["producto"] == "velas" and tk[tid]["anuncio"] == "ADX", "el ticket queda guardado por tarea en anuncios/_apimart/tickets.json")
    m = re.search(r"costo\s+([0-9]*\.?[0-9]+)", r.stdout)
    exigir(m and float(m.group(1)) == 0.0085 and f"tarea {tid}" in r.stdout, "la ultima linea dice el costo y la tarea como la leen gancho_ia.py y avatar_auto.py", r)
    fila = filas(costos)[-1]
    exigir(fila["tipo"] == "imagen" and fila["via"] == "dash" and fila["costo"] == 0.0085 and fila["task_id"] == tid and fila["producto"] == "velas" and fila["anuncio"] == "ADX",
           "costos.jsonl: la fila lleva el costo final del dash y \"via\": \"dash\"")
    exigir(all(e.get("task_id") != tid for x in COSTOS_DASH for e in x["entradas"]), "lo hecho por el dash NO se le vuelve a mandar a /api/editor-costos")
    exigir((t / "cuadros" / "prompts.md").is_file() and "a white candle in a glass jar" in (t / "cuadros" / "prompts.md").read_text(encoding="utf-8"), "imagen: guarda el prompt en cuadros/prompts.md")
    exigir(DESCARGAS and all(x["clave"] is None and x["version"] is None for x in DESCARGAS), "el resultado se baja directo, SIN la llave del editor ni la version")

    d0 = len(DASH)
    r = por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN + " Second still.", "--ref", str(t / "avatar.png"), "--salida", str(t / "cuadros" / "02.png"), "--producto", "velas", "--anuncio", "ADX")
    exigir(r.returncode == 0 and acciones(d0) == ["imagen", "tarea"] and DASH[d0]["cuerpo"]["imagenes"] == ["https://subidas.apimart-falso.test/s1.png"],
           "la misma referencia NO se vuelve a subir: se usa la URL guardada en subidas.json (por su sha1)", r)

    # a la puerta solo van direcciones https: el enlace guardado del resultado de 02.png es http (el del dash falso), asi que el cuadro se sube
    d0 = len(DASH)
    r = por_dash(inst, "video", "--imagen", str(t / "cuadros" / "02.png"), "--prompt", PROMPT_CLIP, "--salida", str(t / "clips" / "02.mp4"), "--producto", "velas", "--anuncio", "ADX")
    exigir(r.returncode == 0 and acciones(d0) == ["subir", "video", "tarea"] and str(DASH[d0 + 1]["cuerpo"].get("imagen", "")).startswith("https://subidas.apimart-falso.test/"),
           "un enlace guardado que no es https no se manda al dash: el cuadro se sube y va su direccion https", r)
    sj = json.loads(subidas.read_text(encoding="utf-8"))
    clave = str((t / "cuadros" / "01.png").resolve())
    exigir(isinstance(sj.get(clave), dict) and str(sj[clave].get("url", "")).startswith(f"http://127.0.0.1:{PUERTO}/files/"),
           "el enlace del resultado queda guardado en subidas.json (para no volver a subir ese cuadro)", str(sj.get(clave))[:200])
    sj[clave]["url"] = "https://resultados.apimart-falso.test/01.png"          # como los de APIMart de verdad, que son https
    subidas.write_text(json.dumps(sj), encoding="utf-8")

    # video a partir de la imagen generada: usa la URL del resultado (sin subir), con --repetir y --tope-anuncio
    M["procesando"] = 1
    d0 = len(DASH)
    r = por_dash(inst, "video", "--imagen", str(t / "cuadros" / "01.png"), "--prompt-archivo", str(t / "prompt_clip.txt"), "--salida", str(t / "clips" / "01.mp4"), "--producto", "velas",
                 "--anuncio", "ADX", "--etiqueta", "clip01", "--repetir", "lo pidio Beto: se corta la voz", "--tope-anuncio", "1", "--motivo-tope", "anuncio largo aprobado")
    M["procesando"] = 0
    salidas.append(r.stdout + r.stderr)
    vid = DASH[d0]["cuerpo"] if DASH[d0:] else {}
    exigir(r.returncode == 0 and (t / "clips" / "01.mp4").is_file() and acciones(d0) == ["video", "tarea", "tarea"], "video por el dash: pide, consulta hasta que termina (una vuelta «processing») y baja el clip", r)
    exigir(vid.get("imagen") == "https://resultados.apimart-falso.test/01.png" and vid.get("duracion") == 8 and isinstance(vid.get("duracion"), int) and vid.get("resolucion") == "480p"
           and vid.get("tamano") == "9:16" and vid.get("repetir") == "lo pidio Beto: se corta la voz" and vid.get("topeAnuncio") == 1.0 and vid.get("motivoTope") == "anuncio largo aprobado",
           "video: el cuadro va como la URL del resultado (sin volver a subir), 8 s enteros, 480p, 9:16; --repetir, --tope-anuncio y --motivo-tope viajan al dash", str(vid))
    fila = filas(costos)[-1]
    exigir(fila["tipo"] == "video" and fila["via"] == "dash" and fila["costo"] == 0.0816 and fila.get("repetido"), "costos.jsonl: el clip con su costo del dash, via dash y el motivo de la repeticion")
    exigir((t / "clips" / "01_prompt.txt").is_file(), "video: guarda clips/01_prompt.txt")

    # estado: usa el ticket guardado
    d0 = len(DASH)
    r = por_dash(inst, "estado", tid)
    exigir(r.returncode == 0 and '"estado": "completed"' in r.stdout and acciones(d0) == ["tarea"] and DASH[d0]["cuerpo"]["ticket"] == TAREAS[tid]["ticket"], "estado <tarea> consulta con el ticket guardado", r)
    r = por_dash(inst, "estado", "task_que_no_es_de_aqui")
    exigir(r.returncode != 0 and "No tengo el ticket" in r.stderr and len(DASH) == d0 + 1, "estado de una tarea sin ticket en este equipo: lo dice y no pregunta", r)

    # subir a mano
    (t / "otra.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 200)
    d0 = len(DASH)
    r = por_dash(inst, "subir", str(t / "otra.jpg"), "--producto", "velas", "--anuncio", "ADX")
    exigir(r.returncode == 0 and r.stdout.strip().startswith("https://subidas.apimart-falso.test/") and DASH[d0]["tipo"] == "image/jpeg", "subir a mano: JPEG con su tipo, devuelve la URL", r)
    (t / "texto.png").write_bytes(b"esto no es una imagen")
    r = por_dash(inst, "subir", str(t / "texto.png"), "--producto", "velas", "--anuncio", "ADX")
    exigir(r.returncode != 0 and "PNG, JPEG ni WEBP" in r.stderr and len(DASH) == d0 + 1, "un archivo que no es PNG, JPEG ni WEBP no se manda", r)

    # --dry-run, tope raro, otro modelo, sin producto
    d0 = len(DASH)
    r = por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN, "--ref", str(t / "otra.jpg"), "--salida", str(t / "dry.png"), "--producto", "velas", "--anuncio", "ADX", "--dry-run")
    exigir(r.returncode == 0 and "DRY-RUN" in r.stdout and "/api/editor-apimart" in r.stdout and len(DASH) == d0, "--dry-run muestra lo que pediria al dash y no manda nada", r)
    for raro in ("nan", "inf", "0", "-1"):
        r = por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "r.png"), "--producto", "velas", "--anuncio", "ADX", "--tope-anuncio", raro)
        exigir(r.returncode != 0 and "--tope-anuncio" in r.stderr and len(DASH) == d0, f"--tope-anuncio {raro}: se rechaza aqui, sin pedir nada", r)
    r = por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "r.png"), "--producto", "velas", "--anuncio", "ADX", "--modelo", "otro-modelo", "--cambio-de-modelo-aprobado")
    exigir(r.returncode != 0 and "fijo" in r.stderr and len(DASH) == d0, "por el dash el modelo es fijo: otro modelo no se pide", r)
    r = por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "r.png"))
    exigir(r.returncode != 0 and "--producto" in r.stderr and len(DASH) == d0, "sin --producto y --anuncio no se pide nada", r)

    # la voz de APIMart ya no existe en uso real
    r = por_dash(inst, "voz", "--texto", "hola", "--salida", str(t / "v.mp3"), "--producto", "velas", "--anuncio", "ADX")
    exigir(r.returncode != 0 and "voz_auto.py" in r.stderr and len(DASH) == d0 and not (t / "v.mp3").exists(), "voz: en uso real se niega y dice que la voz sale de voz_auto.py", r)
    r = por_dash(inst, "voz", "--texto", "hola", "--salida", str(t / "v.mp3"), "--dry-run")
    exigir(r.returncode != 0 and "voz_auto.py" in r.stderr, "voz: tambien con --dry-run", r)

    # una llave de APIMart puesta en el entorno, o una APIMART_BASE_URL que no es local, no cambian nada: todo sigue por el dash
    for base in ("https://api.apimart.ai/v1", f"http://127.0.0.1:{PUERTO}@otro.test/v1"):
        d0, a0 = len(DASH), len(APIMART_VISTO)
        r = por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "b.png"), "--producto", "velas", "--anuncio", "ADZ",
                     env_extra={"APIMART_BASE_URL": base, "APIMART_API_KEY": LLAVE_QUE_NO_DEBE_SALIR})
        salidas.append(r.stdout + r.stderr)
        exigir(r.returncode == 0 and acciones(d0) == ["imagen", "tarea"] and len(APIMART_VISTO) == a0, f"con APIMART_BASE_URL={base} y una llave de APIMart en el entorno, igual va por el dash", r)
    exigir(not any(LLAVE_QUE_NO_DEBE_SALIR in a for a in AUTORIZACIONES), "la llave de APIMart del entorno nunca viaja a ningun lado")

    # cabeceras y que la llave no salga
    exigir(DASH and all(d["clave"] == CLAVE_DASH for d in DASH if d["metodo"] in ("GET", "POST")), "cada pedido al dash lleva la llave del editor en x-clave-editor")
    exigir(all(d["version"] == VERSION for d in DASH), f"cada pedido al dash lleva x-version-editor = el archivo VERSION ({VERSION})")
    texto = "\n".join(salidas) + todo_el_texto(costos, tickets, subidas, t)
    exigir(CLAVE_DASH not in texto and LLAVE_QUE_NO_DEBE_SALIR not in texto, "ninguna llave aparece en las salidas, costos.jsonl, tickets.json ni subidas.json")

    # lo hecho por el dash nunca va a /api/editor-costos; lo VIEJO si
    with costos.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"fecha": "2026-11-09T10:00:00", "tipo": "imagen", "costo": 0.0085, "producto": "velas", "anuncio": "AD9", "etiqueta": "vieja", "task_id": "task_viejo"}) + "\n")
    n0 = len(COSTOS_DASH)
    r = por_dash(inst, "enviar-costos")
    mandadas = [e for x in COSTOS_DASH[n0:] for e in x["entradas"]]
    exigir(r.returncode == 0 and [e["task_id"] for e in mandadas] == ["task_viejo"] and "hechos por el dash no se mandan" in r.stdout,
           "enviar-costos manda SOLO lo viejo (sin via): lo hecho por el dash ya esta alla y no se manda", r)
    (inst / "costos_pendientes.jsonl").write_text(json.dumps({"tipo": "video", "costo": 0.0816, "producto": "velas", "anuncio": "AD8", "task_id": "pend_dash", "via": "dash"}) + "\n"
                                                  + json.dumps({"tipo": "video", "costo": 0.0816, "producto": "velas", "anuncio": "AD8", "task_id": "pend_viejo"}) + "\n", encoding="utf-8")
    n0 = len(COSTOS_DASH)
    r = por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "p.png"), "--producto", "velas", "--anuncio", "ADZ")
    mandadas = [e["task_id"] for x in COSTOS_DASH[n0:] for e in x["entradas"]]
    exigir(r.returncode == 0 and mandadas == ["pend_viejo"] and not (inst / "costos_pendientes.jsonl").exists(), f"lo pendiente de antes se manda solo antes de pedir, sin las filas del dash (visto: {mandadas})", r)


# ------------------------------------------------------------------ 3. lo que el dash niega
def pruebas_errores() -> None:
    seccion("3. Lo que el dash niega se dice tal cual y no se gasta")
    inst = instalacion("errores", env_conectado())
    t = TRABAJO / "trabajo_errores"
    t.mkdir()
    (t / "avatar.png").write_bytes(PNG)
    costos = inst / "costos.jsonl"
    img = lambda *mas, **kw: por_dash(inst, "imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "e.png"), "--producto", "velas", "--anuncio", "ADE", *mas, **kw)   # noqa: E731
    vid = lambda *mas: por_dash(inst, "video", "--imagen", "https://ya.publica.test/c.png", "--prompt", PROMPT_CLIP, "--salida", str(t / "e.mp4"), "--producto", "velas", "--anuncio", "ADE", *mas)  # noqa: E731

    for codigo, accion, mensaje, que in ((402, "imagen", "El anuncio ADE ya gasto US$ 0.49: tapa con stock o una imagen con movimiento.", "el tope del anuncio (402)"),
                                         (409, "video", "Ese clip ya se pago: no se repite por un error.", "el clip repetido (409)"),
                                         (429, "imagen", "Se acabo el tope del dia del editor (US$ 5).", "el tope del dia (429)")):
        M["errores"].append((accion, codigo, {"ok": False, "error": mensaje}))
        n_filas, n_tareas = len(filas(costos)), len(TAREAS)
        r = (img if accion == "imagen" else vid)()
        exigir(r.returncode != 0 and mensaje in r.stderr and f"({codigo})" in r.stderr and "No se gasto nada" in r.stderr and len(filas(costos)) == n_filas and len(TAREAS) == n_tareas,
               f"{que}: sale con error, muestra el mensaje del dash TAL CUAL y no anota nada", r)
    M["minima"] = "2099.01.01-1"
    d0 = len(DASH)
    r = img()
    M["minima"] = VERSION
    exigir(r.returncode != 0 and "426" in r.stderr and "Instalar-Editor.cmd" in r.stderr and "2099.01.01-1" in r.stderr and acciones(d0) == ["imagen"], "editor viejo (426): dice que se actualice con Instalar-Editor.cmd y la version minima", r)
    M["errores"].append(("imagen", 401, {"ok": False, "error": "La llave del editor no es valida."}))
    r = img()
    exigir(r.returncode != 0 and "401" in r.stderr and "Conectar-Editor.cmd" in r.stderr, "llave rechazada (401): dice como volver a conectar el editor", r)

    # 503 con «reintentar»: el dash no anoto la reserva y no pidio nada; se repite hasta 2 veces con 2 s de espera
    M["reintentar"] = 2
    d0, t0 = len(DASH), time.time()
    r = img()
    exigir(r.returncode == 0 and acciones(d0) == ["imagen", "imagen", "imagen", "tarea"] and time.time() - t0 >= 3.5, "503 «reintentar» dos veces: espera 2 s, repite y a la tercera sale", r)
    M["reintentar"] = 3
    d0 = len(DASH)
    r = img()
    M["reintentar"] = 0
    exigir(r.returncode != 0 and acciones(d0) == ["imagen", "imagen", "imagen"] and "503" in r.stderr and "repite en un rato" in r.stderr, "503 «reintentar» tres veces: se rinde al tercer intento y lo dice", r)
    M["errores"].append(("imagen", 503, {"ok": False, "error": "Al dash le falta la llave de APIMart."}))
    d0 = len(DASH)
    r = img()
    exigir(r.returncode != 0 and acciones(d0) == ["imagen"] and "le falta la llave de APIMart" in r.stderr, "503 SIN «reintentar» (falta configurar el dash): no se repite", r)

    # la tarea falla en APIMart
    M["fallar_tarea"] = True
    n_filas = len(filas(costos))
    r = img()
    M["fallar_tarea"] = False
    exigir(r.returncode != 0 and "failed" in r.stderr and "contenido rechazado" in r.stderr and len(filas(costos)) == n_filas, "una tarea que falla en APIMart: lo dice y no anota costo", r)
    M["sin_costo"] = True
    r = img()
    M["sin_costo"] = False
    fila = filas(costos)[-1]
    exigir(r.returncode == 0 and fila["costo"] == 0.0085 and fila.get("costo_es_estimado") is True, "si el dash no da el costo final, se anota su estimado y se marca como estimado", r)

    # el resultado se genero (y se pago) pero el enlace no baja: se anota el costo igual y se dice como recuperarlo, sin pedirlo otra vez
    M["descarga_rota"] = True
    d0, n_filas = len(DASH), len(filas(costos))
    r = img()
    M["descarga_rota"] = False
    fila = filas(costos)[-1]
    exigir(r.returncode != 0 and acciones(d0) == ["imagen", "tarea"] and len(filas(costos)) == n_filas + 1 and fila.get("descarga_fallida") is True and fila["costo"] == 0.0085
           and "estado " + fila["task_id"] in r.stderr and "No la pidas otra vez" in r.stderr and "Traceback" not in r.stderr,
           "si el resultado no baja: anota lo pagado (marcado descarga_fallida), dice como sacar el enlace con «estado» y no repite el pedido", r)

    # redireccion: no se sigue
    M["redirige"] = True
    r = img()
    M["redirige"] = False
    exigir(r.returncode != 0 and ESPIA == [] and "redireccion" in r.stderr, "si el dash contesta con una redireccion NO se sigue: la llave no sale de DASH_URL", r)

    # lo que el dash negaria por su forma se dice AQUI, antes de subir o pedir nada (las mismas reglas que functions/_editor-apimart.js)
    f_png, f_mp4 = str(t / "f.png"), str(t / "f.mp4")
    for args, pista, que in (
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", f_png, "--producto", "p" * 42, "--anuncio", "ADE"], "hasta 41", "un producto de 42 letras"),
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", f_png, "--producto", "Velas", "--anuncio", "ADE"], "minusculas", "un producto con mayuscula"),
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", f_png, "--producto", "velas", "--anuncio", "AD 1"], "hasta 16", "un anuncio con espacio"),
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "_f.png"), "--producto", "velas", "--anuncio", "ADE"], "Cambia --salida", "una salida que empieza por _"),
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "f#1.png"), "--producto", "velas", "--anuncio", "ADE"], "Cambia --salida", "una salida con #"),
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", f_png, "--producto", "velas", "--anuncio", "ADE", "--tamano", "1:1"], "9:16", "una imagen cuadrada"),
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", f_png, "--producto", "velas", "--anuncio", "ADE", "--resolucion", "2k"], "1k", "una imagen en 2k"),
            (["imagen", "--prompt", PROMPT_IMAGEN, "--salida", f_png, "--producto", "velas", "--anuncio", "ADE", "--ref", "http://sin-https.test/a.png"], "https", "una referencia http"),
            (["video", "--imagen", "http://sin-https.test/c.png", "--prompt", PROMPT_CLIP, "--salida", f_mp4, "--producto", "velas", "--anuncio", "ADE"], "https", "un cuadro http"),
            (["video", "--imagen", "https://ya.publica.test/c.png", "--prompt", PROMPT_CLIP, "--salida", f_mp4, "--producto", "velas", "--anuncio", "ADE", "--resolucion", "1080p"],
             "480p o 720p", "un clip en 1080p"),
            (["subir", str(t / "avatar.png"), "--producto", "p" * 42, "--anuncio", "ADE"], "hasta 41", "subir con un producto de 42 letras"),
            (["saldo", "--producto", "velas", "--anuncio", "AD/1"], "hasta 16", "saldo con un anuncio raro")):
        d0 = len(DASH)
        r = por_dash(inst, *args)
        exigir(r.returncode != 0 and pista in r.stderr and len(DASH) == d0 and "Traceback" not in r.stderr, f"{que}: lo niega aqui, claro y sin llamar al dash", r)
    d0 = len(DASH)
    r = img("--etiqueta", "linea|con barra\ny salto " * 6)
    et = DASH[d0]["cuerpo"].get("etiqueta", "") if DASH[d0:] else ""
    exigir(r.returncode == 0 and "|" not in et and "\n" not in et and 0 < len(et) <= 40, f"una etiqueta larga o con | y saltos va limpia y de hasta 40 letras, como la guarda el dash ({et!r})", r)

    # sin conexion: sin .env, con una direccion que no es la del dash, o con la llave con espacios
    for nombre, env_texto, que in (("sin_env", None, "sin .env"), ("http", "DASH_URL=http://tu_proyecto.pages.dev\nDASH_CLAVE_EDITOR=" + CLAVE_DASH + "\n", "con una direccion http"),
                                   ("otro_sitio", "DASH_URL=https://dash.ejemplo.test\nDASH_CLAVE_EDITOR=" + CLAVE_DASH + "\n", "con otro sitio")):
        sin = instalacion(nombre, env_texto)
        d0 = len(DASH)
        r = por_dash(sin, "imagen", "--prompt", PROMPT_IMAGEN, "--salida", str(t / "s.png"), "--producto", "velas", "--anuncio", "ADS")
        exigir(r.returncode != 0 and "no esta conectado" in r.stderr and "Conectar-Editor.cmd" in r.stderr and "Descargar conexion" in r.stderr and len(DASH) == d0 and CLAVE_DASH not in r.stderr,
               f"editor sin conectar ({que}): dice como conectarlo (Descargar conexion + Conectar-Editor.cmd) y no pide nada", r)
    r = por_dash(sin, "saldo")
    exigir(r.returncode != 0 and "no esta conectado" in r.stderr, "saldo sin conexion: lo mismo", r)


# ------------------------------------------------------------------ 4. texto y vision por el dash (apimart_chat.py)
PROGRAMA_TEXTO = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import apimart_chat as C
caso, imagenes = sys.argv[2], [p for p in sys.argv[3:]]
try:
    if caso == "catalogo":
        print(json.dumps({"catalogo": C.catalogo_chat()}))
    else:
        modelos = {"modelo": ["modelo-raro", "gemini-3.5-flash-lite"], "dos_limites": ["gemini-3.5-flash-lite", "gpt-5-nano"]}.get(caso, ["gemini-3.5-flash-lite"])
        producto = {"sin_producto": "", "producto_largo": "p" * 42}.get(caso, "velas")
        r = C.completar("SISTEMA", "USUARIO", imagenes=imagenes, modelos=modelos, max_tokens=9000, temperatura=0.3, json_modo=True, etiqueta="prueba", producto=producto, anuncio="ADX")
        print(json.dumps({"modelo": r.modelo, "datos": r.datos, "entrada": r.tokens_entrada, "salida": r.tokens_salida, "intentos": r.intentos}))
except C.ErrorChat as e:
    print(json.dumps({"error": str(e), "grave": e.grave}))
'''


def pruebas_texto() -> None:
    seccion("4. Texto y vision (apimart_chat.py) por el dash")
    inst = instalacion("texto", env_conectado())
    t = TRABAJO / "trabajo_texto"
    t.mkdir()
    prog = t / "texto.py"
    prog.write_text(PROGRAMA_TEXTO, encoding="utf-8")
    try:
        from PIL import Image
    except ImportError:
        Image = None
    grande, chica = t / "grande.png", t / "chica.jpg"
    if Image is not None:
        Image.new("RGB", (2400, 1200), (200, 40, 40)).save(grande)
        Image.new("RGB", (320, 240), (40, 200, 40)).save(chica, quality=80)
    else:
        print("  (sin pillow: las imagenes de la prueba de vision salen de ffmpeg)")
        ff = shutil.which("ffmpeg")
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=2400x1200", "-frames:v", "1", str(grande)], check=True)
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=green:s=320x240", "-frames:v", "1", str(chica)], check=True)

    def correr(caso: str, *imagenes: Path) -> dict:
        r = subprocess.run([sys.executable, str(prog), str(inst / "scripts"), caso, *map(str, imagenes)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env_limpio({"APIMART_API_KEY": LLAVE_QUE_NO_DEBE_SALIR}), cwd=inst, timeout=180)
        try:
            return {**json.loads(r.stdout.strip().splitlines()[-1]), "_salida": r.stdout + r.stderr}
        except (ValueError, IndexError):
            return {"_salida": r.stdout + r.stderr, "_roto": True}

    d0 = len(DASH)
    j = correr("ok", grande, chica)
    exigir(j.get("modelo") == "gemini-3.5-flash-lite" and j.get("datos") == {"respuesta": "hola"} and acciones(d0) == ["texto"], "texto con dos imagenes: UN pedido «texto» al dash y la respuesta se lee igual", j["_salida"])
    c = DASH[d0]["cuerpo"]
    exigir(c["sistema"] == "SISTEMA" and c["usuario"] == "USUARIO" and c["modelo"] == "gemini-3.5-flash-lite" and c["producto"] == "velas" and c["anuncio"] == "ADX" and c["etiqueta"] == "prueba"
           and c["json_modo"] is True and c["temperatura"] == 0.3 and c["quitar"] == [], "texto: sistema, usuario, modelo, producto, anuncio, etiqueta, json_modo, temperatura y quitar vacio")
    exigir(c["max_tokens"] == 6000, "texto: max_tokens se acota a 6.000 (lo que acepta el dash)", str(c["max_tokens"]))
    uris = c["imagenes"]
    exigir(len(uris) == 2 and uris[0].startswith("data:image/jpeg;base64,") and uris[1] == "data:image/jpeg;base64," + base64.b64encode(chica.read_bytes()).decode(),
           "las imagenes van como data URI: la grande convertida a JPEG, la chica tal cual")
    if Image is not None:
        with Image.open(io.BytesIO(base64.b64decode(uris[0].split(",", 1)[1]))) as im:
            exigir(max(im.size) <= 1024 and im.size[0] == 1024, f"la imagen grande (2400x1200) se achica a 1024 px de lado ({im.size})")
    exigir(all(len(u) <= 400_000 for u in uris), "cada imagen queda por debajo de las 400.000 letras que acepta el dash")
    fila = filas(inst / "costos.jsonl")[-1]
    exigir(fila["tipo"] == "texto" and fila["via"] == "dash" and fila["costo"] is None and fila["costo_estimado"] > 0 and fila["imagenes"] == 2, "costos.jsonl: el texto queda con su estimado y via dash")
    exigir(not any(e.get("tipo") == "texto" for x in COSTOS_DASH for e in x["entradas"]), "el texto no se manda a /api/editor-costos (el dash lo anoto)")

    M["texto"] = [(400, {"error": {"message": "Unsupported parameter: 'response_format' is not supported with this model."}})]
    d0 = len(DASH)
    j = correr("quitar")
    exigir(j.get("datos") == {"respuesta": "hola"} and acciones(d0) == ["texto", "texto"] and DASH[d0]["cuerpo"]["quitar"] == [] and DASH[d0 + 1]["cuerpo"]["quitar"] == ["response_format"],
           "si APIMart (por el dash) no entiende un campo, se repite con ese campo en «quitar»", j["_salida"])
    d0 = len(DASH)
    j = correr("modelo")
    exigir(j.get("modelo") == "gemini-3.5-flash-lite" and acciones(d0) == ["texto", "texto"] and "modelo-raro" in " ".join(j.get("intentos", [])), "un modelo que el dash no permite: se prueba el siguiente", j["_salida"])
    # un modelo que no acepta ni max_tokens ni max_completion_tokens: el editor los quita de a uno; el dash niega quitar los dos (400) y el editor pasa al modelo siguiente
    M["texto"] = [(400, {"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with this model."}}),
                  (400, {"error": {"message": "Unsupported parameter: 'max_completion_tokens' is not supported with this model."}})]
    d0, r0 = len(DASH), len(RECHAZOS)
    j = correr("dos_limites")
    M["texto"] = []
    exigir(j.get("modelo") == "gpt-5-nano" and j.get("datos") == {"respuesta": "hola"} and acciones(d0) == ["texto"] * 4
           and [DASH[d0 + i]["cuerpo"]["quitar"] for i in range(4)] == [[], ["max_tokens"], ["max_tokens", "max_completion_tokens"], []]
           and [x["codigo"] for x in RECHAZOS[r0:]] == [400] and "límites de respuesta" in " ".join(j.get("intentos", [])),
           "sin poder quitar los dos limites de respuesta (el dash da 400): el editor pasa al modelo siguiente, que contesta", j["_salida"])
    M["texto"] = [(402, {"error": {"message": "insufficient balance"}})]
    j = correr("saldo")
    exigir(j.get("grave") is True and "dueño" in j.get("error", ""), "APIMart sin saldo (402, por el dash): error grave que manda avisarle al dueño", j["_salida"])
    d0 = len(DASH)
    j = correr("siete", *([chica] * 7))
    exigir(j.get("grave") is True and "6" in j.get("error", "") and len(DASH) == d0, "mas de 6 imagenes: error grave sin pedir nada", j["_salida"])
    for caso, que in (("sin_producto", "sin producto"), ("producto_largo", "con un producto de 42 letras (el dash acepta hasta 41)")):
        d0 = len(DASH)
        j = correr(caso)
        exigir(j.get("grave") is True and "no pregunto nada" in j.get("error", "") and len(DASH) == d0, f"texto {que}: error grave claro y sin llamar al dash", j["_salida"])
    M["minima"] = "2099.01.01-1"
    j = correr("viejo")
    M["minima"] = VERSION
    exigir(j.get("grave") is True and "Instalar-Editor.cmd" in j.get("error", ""), "editor viejo (426) en el texto: error grave que dice como actualizar", j["_salida"])
    d0 = len(DASH)
    j = correr("catalogo")
    exigir(j.get("catalogo") == {} and len(DASH) == d0, "catalogo_chat por el dash: vacio y sin llamar a nadie (no hay llave)", j["_salida"])
    sin = instalacion("texto_sin_conexion", None)
    r = subprocess.run([sys.executable, str(prog), str(sin / "scripts"), "ok"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env_limpio(), cwd=sin, timeout=60)
    j = json.loads(r.stdout.strip().splitlines()[-1])
    exigir(j.get("grave") is True and "no esta conectado" in j.get("error", ""), "texto sin conexion: error grave que dice como conectar", r)
    exigir(not any(LLAVE_QUE_NO_DEBE_SALIR in a for a in AUTORIZACIONES), "la llave de APIMart del entorno tampoco viaja con el texto")


# ------------------------------------------------------------------ 5. lo gastado por anuncio (anuncio_comun.py) y la configuracion
def pruebas_en_proceso() -> None:
    seccion("5. Lo gastado por anuncio y la direccion del dash")
    A = importlib.import_module("apimart")
    AC = importlib.import_module("anuncio_comun")
    guardado = (A.RAIZ, A.COSTOS, A.BASE)
    falso = TRABAJO / "en_proceso"
    falso.mkdir()
    shutil.copy2(RAIZ / "VERSION", falso / "VERSION")
    try:
        A.RAIZ, A.BASE, A.COSTOS = falso, A.BASE_REAL, falso / "costos.jsonl"
        for k in ("DASH_URL", "DASH_CLAVE_EDITOR"):
            os.environ.pop(k, None)
        (falso / ".env").write_text(env_conectado(), encoding="utf-8")
        A.COSTOS.write_text("\n".join(json.dumps(x) for x in (
            {"producto": "velas", "anuncio": "ADG", "tipo": "imagen", "costo": 0.02, "via": "dash"}, {"producto": "velas", "anuncio": "ADG", "tipo": "texto", "costo": None, "costo_estimado": 0.003},
            {"producto": "velas", "anuncio": "ADG", "tipo": "voz", "costo": None, "costo_estimado": 0.01}, {"producto": "velas", "anuncio": "OTRO", "tipo": "video", "costo": 1.0},
            {"producto": "velas", "anuncio": "NUEVO1", "tipo": "video", "costo": 0.08, "via": "dash"}, {"producto": "velas", "anuncio": "NUEVO1", "tipo": "texto", "costo": None, "costo_estimado": 0.002})) + "\n",
            encoding="utf-8")
        d0 = len(DASH)
        local = AC.gastado_en_anuncio("velas", "ADG")
        exigir(abs(local - 0.033) < 1e-9 and len(DASH) == d0, "sin preguntar al dash: suma costos.jsonl de este equipo (como antes) y no llama a nadie")
        con_dash = AC.gastado_en_anuncio("velas", "ADG", preguntar_dash=True)
        exigir(abs(con_dash - (0.15 + 0.013)) < 1e-9 and acciones(d0) == ["estado"] and DASH[-1]["consulta"] == {"estado": "1", "producto": "velas", "anuncio": "ADG"},
               f"preguntando al dash: imagenes y clips segun el dash (0,15, que ya incluye el legado; mayor que lo local) + texto y voz de aqui = 0,163 (dio {con_dash})")
        exigir(A.ficha_del_dash("velas", "ADG") == {"gastado_usd": 0.15, "clips_pagados": 1, "repetidos": 0},
               "ficha_del_dash: gastado (el del dash, sin volver a sumar el legado), clips pagados y repetidos")
        exigir(A.ficha_del_dash("velas", "NUEVO1") == {"gastado_usd": 0.0, "clips_pagados": 0, "repetidos": 0},
               "ficha_del_dash de un anuncio sin ficha en el dash (legadoUsd null, legadoSinCalcular): gastado 0, sin romperse")
        sin_ficha = AC.gastado_en_anuncio("velas", "NUEVO1", preguntar_dash=True)
        exigir(abs(sin_ficha - 0.082) < 1e-9,
               f"anuncio sin ficha en el dash (gastado 0, legado sin calcular): sigue tomando lo mayor entre el dash y lo de aqui = 0,08 + 0,002 de texto (dio {sin_ficha})")
        (falso / ".env").write_text("DASH_URL=http://127.0.0.1:9\nDASH_CLAVE_EDITOR=" + CLAVE_DASH + "\n", encoding="utf-8")
        exigir(abs(AC.gastado_en_anuncio("velas", "ADG", preguntar_dash=True) - 0.033) < 1e-9, "si el dash no contesta, cuenta lo de este equipo (el tope lo aplica el dash igual)")
        A.BASE = f"http://127.0.0.1:{PUERTO}/v1"
        exigir(A.ficha_del_dash("velas", "ADG") is None, "con el APIMart falso de las autopruebas no se pregunta al dash")
        A.BASE = A.BASE_REAL
        # la direccion del dash: solo https al dash de verdad (o esta maquina en pruebas)
        (falso / ".env").write_text("﻿DASH_URL=https://tu_proyecto.pages.dev/\nDASH_CLAVE_EDITOR=llave-del-env-0123456789\n", encoding="utf-8")
        exigir(A.config_dash() == ("https://tu_proyecto.pages.dev", "llave-del-env-0123456789"), "lee DASH_URL y DASH_CLAVE_EDITOR de .env (aunque traiga BOM) y quita la barra final")
        A.BASE = f"http://127.0.0.1:{PUERTO}/v1"
        exigir(A.config_dash() is None, "con el APIMart falso de las autopruebas .env no se lee")
        A.BASE = A.BASE_REAL
        for texto, que in (("DASH_URL=http://tu_proyecto.pages.dev\nDASH_CLAVE_EDITOR=llave-del-env-0123456789\n", "una direccion http"),
                           ("DASH_URL=https://dash.ejemplo.test\nDASH_CLAVE_EDITOR=llave-del-env-0123456789\n", "otro sitio https"),
                           ("DASH_URL=https://tu_proyecto.pages.dev\nDASH_CLAVE_EDITOR=\n", "sin llave")):
            (falso / ".env").write_text(texto, encoding="utf-8")
            exigir(A.config_dash() is None, f"dash: {que} no se usa")
        buenas = ("https://tu_proyecto.pages.dev", "https://tu_proyecto.pages.dev/", "http://127.0.0.1:8790", "http://localhost:9/v1")
        malas = ("http://127.0.0.1:1@otro.test/", "http://127.0.0.1@otro.test", "https://tu_proyecto.pages.dev@otro.test", "https://tu_proyecto.pages.dev.otro.test",
                 "https://otro.test/tu_proyecto.pages.dev", "https://tu_proyecto.pages.dev:8443", "https://u:p@tu_proyecto.pages.dev", "https://tu_proyecto.pages.dev/x",
                 "http://127.0.0.1:abc", "http://127.0.0.2:1", "ftp://127.0.0.1:1", "", "tu_proyecto.pages.dev")
        exigir(all(A.direccion_dash_valida(u) for u in buenas), f"las direcciones buenas pasan (no pasan: {[u for u in buenas if not A.direccion_dash_valida(u)]})")
        exigir(not any(A.direccion_dash_valida(u) for u in malas), f"ninguna direccion que lleve la llave a otro sitio pasa (pasan: {[u for u in malas if A.direccion_dash_valida(u)]})")
    finally:
        A.RAIZ, A.COSTOS, A.BASE = guardado


# ------------------------------------------------------------------ 6. Conectar-Editor.ps1 y el empaquetador (solo Windows)
def pruebas_conectar() -> None:
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if os.name != "nt" or not powershell:
        print("\n6. (omitido: Conectar-Editor.ps1 se prueba en Windows)")
        return
    seccion("6. Conectar-Editor.ps1: la conexion del dash pasa a .env (y nunca viaja en el paquete)")
    A = importlib.import_module("apimart")
    llave_nueva = "llave-nueva-de-prueba-0123456789"
    conexion_ok = f"DASH_URL=https://tu_proyecto.pages.dev\r\nDASH_CLAVE_EDITOR={llave_nueva}\r\n"
    env_viejo = "# mi editor\r\nAPIMART_API_KEY=" "sk-vieja-que-se-va-0123456789\r\nSCRAPECREATORS_API_KEY=abc\r\nDASH_URL=https://viejo.test\r\n"

    def armar(nombre: str):
        d = TRABAJO / nombre
        (d / "editor").mkdir(parents=True)
        (d / "perfil" / "Downloads").mkdir(parents=True)
        for f in ("Conectar-Editor.ps1", "Editor-Comun.ps1"):
            shutil.copy2(RAIZ / f, d / "editor" / f)
        (d / "editor" / ".env").write_bytes(env_viejo.encode("utf-8"))
        return d / "editor", d / "perfil" / "Downloads"

    def conectar(editor: Path, perfil: Path) -> subprocess.CompletedProcess:
        return subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(editor / "Conectar-Editor.ps1")], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env={**os.environ, "USERPROFILE": str(perfil.parent)}, timeout=120)

    editor, bajadas = armar("conectar_ok")
    (bajadas / "conexion-editor.txt").write_text(conexion_ok, encoding="utf-8")
    r = conectar(editor, bajadas)
    env_b = (editor / ".env").read_bytes()
    env_t = env_b.decode("utf-8")
    exigir(r.returncode == 0 and "DASH_URL=https://tu_proyecto.pages.dev\r\n" in env_t and f"DASH_CLAVE_EDITOR={llave_nueva}\r\n" in env_t and "APIMART_API_KEY" not in env_t
           and "SCRAPECREATORS_API_KEY=abc" in env_t and "# mi editor" in env_t and "viejo.test" not in env_t, "pasa las dos lineas a .env, reemplaza la vieja, QUITA APIMART_API_KEY y deja lo demas", r)
    exigir(not env_b.startswith(b"\xef\xbb\xbf"), ".env queda sin BOM (Python lo lee desde la primera linea)")
    exigir(not (bajadas / "conexion-editor.txt").exists(), "borra el archivo bajado (lleva la llave)")
    exigir(llave_nueva not in (r.stdout + r.stderr) and "sk-vieja" not in (r.stdout + r.stderr) and "Se quito la llave de APIMart" in r.stdout, "no muestra ninguna llave y dice que quito la de APIMart", r)
    guardado = (A.RAIZ, A.BASE)
    try:
        A.RAIZ, A.BASE = editor, A.BASE_REAL
        exigir(A.config_dash() == ("https://tu_proyecto.pages.dev", llave_nueva) and A.leer_env("APIMART_API_KEY") == "", "apimart.py lee la conexion que dejo Conectar-Editor.ps1")
    finally:
        A.RAIZ, A.BASE = guardado

    editor, bajadas = armar("conectar_junto")
    (editor / "conexion-editor.txt").write_text(conexion_ok, encoding="utf-8")
    r = conectar(editor, bajadas)
    exigir(r.returncode == 0 and f"DASH_CLAVE_EDITOR={llave_nueva}" in (editor / ".env").read_text(encoding="utf-8") and not (editor / "conexion-editor.txt").exists(),
           "tambien la encuentra junto al editor (o al instalador)", r)

    for nombre, contenido, que in (("con_ruta", conexion_ok.replace("pages.dev\r", "pages.dev/x\r"), "una DASH_URL con ruta"),
                                   ("http", conexion_ok.replace("https://", "http://"), "una DASH_URL http"),
                                   ("otro_sitio", conexion_ok.replace("tu_proyecto.pages.dev", "tu_proyecto.pages.dev.otro.test"), "otro sitio"),
                                   ("puerto", conexion_ok.replace("pages.dev\r", "pages.dev:8443\r"), "un puerto"),
                                   ("espacios", f"DASH_URL=https://tu_proyecto.pages.dev\r\nDASH_CLAVE_EDITOR=llave con espacios 0123456789\r\n", "una llave con espacios"),
                                   ("corta", "DASH_URL=https://tu_proyecto.pages.dev\r\nDASH_CLAVE_EDITOR=corta\r\n", "una llave corta"),
                                   ("tres", conexion_ok + "APIMART_API_KEY=" "sk-colada-0123456789\r\n", "una tercera linea")):
        editor, bajadas = armar(f"conectar_{nombre}")
        (bajadas / "conexion-editor.txt").write_text(contenido, encoding="utf-8")
        r = conectar(editor, bajadas)
        exigir(r.returncode == 1 and (editor / ".env").read_bytes() == env_viejo.encode("utf-8") and (bajadas / "conexion-editor.txt").exists() and "no es una conexion valida" in r.stdout
               and "0123456789" not in (r.stdout + r.stderr), f"{que}: no se importa, no toca .env, no borra el archivo y no muestra la llave", r)
    editor, bajadas = armar("conectar_nada")
    r = conectar(editor, bajadas)
    exigir(r.returncode == 2 and "No encontre conexion-editor.txt" in r.stdout and (editor / ".env").read_bytes() == env_viejo.encode("utf-8"), "sin archivo: dice donde bajarlo y no toca nada", r)

    comun = str(RAIZ / "Editor-Comun.ps1").replace("'", "''")
    r = subprocess.run([powershell, "-NoProfile", "-Command", f". '{comun}'; foreach ($r in @('conexion-editor.txt','conexion-editor (1).txt','scripts/apimart.py','Conectar-Editor.cmd')) "
                        "{ Write-Output ($r + '=' + (Es-Dato-Del-Usuario $r)) }"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    exigir("conexion-editor.txt=True" in r.stdout and "conexion-editor (1).txt=True" in r.stdout and "scripts/apimart.py=False" in r.stdout and "Conectar-Editor.cmd=False" in r.stdout,
           "el empaquetador y el instalador nunca llevan conexion-editor*.txt (y si llevan Conectar-Editor.cmd)", r)


# ------------------------------------------------------------------ principal
def main() -> None:
    global PUERTO, TRABAJO
    configurar_consola()
    modo_por_defecto()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Falso)
    PUERTO = servidor.server_port
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    (RAIZ / ".cache").mkdir(exist_ok=True)
    TRABAJO = Path(tempfile.mkdtemp(prefix="autoprueba_apimart_", dir=RAIZ / ".cache"))
    try:
        pruebas_directo()
        pruebas_dash()
        pruebas_errores()
        pruebas_texto()
        pruebas_en_proceso()
        pruebas_conectar()
        seccion("7. Nada de lo que mando el editor lo habria negado el dash por su forma")
        a_proposito = ("modelo no permitido", "No se pueden quitar los dos límites de respuesta")
        inesperados = [x for x in RECHAZOS if not x["error"].startswith(a_proposito)]
        exigir(not inesperados and any(x["error"].startswith("modelo no permitido") for x in RECHAZOS)
               and sum(1 for x in RECHAZOS if x["error"].startswith(a_proposito[1])) == 1,
               "el dash falso aplica las reglas de forma del de verdad y solo nego lo que se pidio a proposito (el modelo raro y, una vez, quitar los dos limites de respuesta)",
               str(inesperados)[:600])
    finally:
        servidor.shutdown()
        for _ in range(20):            # en Windows el antivirus deja carpetas «pendientes de borrar» un instante
            shutil.rmtree(TRABAJO, ignore_errors=True)
            if not TRABAJO.exists():
                break
            time.sleep(0.5)
    print(f"\nAUTOPRUEBA OK ({TOTAL} comprobaciones)")


TRABAJO = Path(".")

if __name__ == "__main__":
    main()
