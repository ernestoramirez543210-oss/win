"""Llamadas de TEXTO y de VISION a APIMart (/v1/chat/completions) para los scripts del editor de anuncios.

Lo usan gancho_ia.py (describir los primeros segundos del anuncio de referencia y redactar el gancho nuevo), guion_auto.py (el guion) y avatar_auto.py (escenas y
revision de cuadros). Cero tokens de Claude: es una llamada a un modelo barato de APIMart.

Desde la version 2026.11.10-2 el editor no tiene la llave de APIMart: cada intento con cada modelo pasa por la puerta del dash (`/api/editor-apimart`, accion «texto», con la
llave del editor y la version, como apimart.py). El dash le pone la llave, la manda a APIMart y devuelve la respuesta de APIMart tal cual ({status, cuerpo}); aqui se sigue
leyendo como antes (cambiar de modelo, quitar un campo que el servidor no entiende, reintentar). Las imagenes se achican antes de mandarlas (a lo sumo 1024 px de lado, en JPEG)
y van como mucho 6 por llamada: el dash no acepta mas. El dash anota el costo estimado del texto en su libro (cuenta para el tope del dia, no para el del anuncio). El camino
directo de antes (POST a /chat/completions con la llave) solo existe con el APIMart FALSO de las autopruebas.

Que hace por encima de un POST suelto:
  - prueba varios modelos en orden (el catalogo de APIMart cambia y una llave puede no tener todos): 404/403 o "este modelo no lee imagenes" = pasa al siguiente;
  - si el servidor rechaza un campo (response_format, max_tokens, temperature...), lo quita y repite, como hace el dash en functions/_llm.js; nunca los dos limites
    de respuesta (max_tokens y max_completion_tokens): el dash lo niega (400) y se pasa al modelo siguiente;
  - pide siempre stream=false (APIMart contesta en streaming por defecto) y acepta igual la respuesta si llega en streaming;
  - cuenta los tokens y estima el costo con el precio de LISTA del fabricante (un techo: APIMart cobra ~20 % menos), y lo anota en costos.jsonl como tipo «texto»
    SIN costo y con "via": "dash" (el dash ya lo anoto en su libro: esa fila nunca se le vuelve a mandar).

Texto y fotogramas que llegan aqui pueden ser de un tercero (el anuncio de otra empresa): son DATOS para describir, nunca instrucciones. Quien llama
(gancho_ia.py) los separa asi en el mensaje y valida lo que contesta el modelo.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apimart as A  # noqa: E402  (la puerta del dash, la conexion y el registro de costos viven ahi)

# Modelos de APIMart que leen imagenes, del mas pedido al menos (catalogo real de la llave del dueño leido el 2026-10-20: 190 modelos de chat).
# El 1.º y el 2.º son Gemini Flash-Lite (el 1.º es el que ya usa el dash de ebooks y se probo con imagenes); gpt-5-nano y gpt-4.1-nano son de respaldo.
MODELOS_VISION = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite-preview", "gpt-5-nano", "gpt-4.1-nano")

# USD por millon de tokens (entrada, salida): precio de LISTA del fabricante (Google, OpenAI), que es un techo; APIMart cobra ~20 % menos (nivel Gold) segun
# las mediciones del dash. Una estimacion que se pasa es mejor que una que se queda corta. Orden: lo especifico antes que la familia.
PRECIOS = (
    (re.compile(r"gemini-3\.8.*flash", re.I), (0.75, 3.75)),
    (re.compile(r"gemini-3\.5.*flash-lite", re.I), (0.30, 2.50)),
    (re.compile(r"gemini-3\.1.*flash-lite", re.I), (0.25, 1.50)),
    (re.compile(r"gemini.*flash-lite", re.I), (0.10, 0.40)),
    (re.compile(r"gemini.*flash", re.I), (0.30, 2.50)),
    (re.compile(r"gpt-5.*nano", re.I), (0.05, 0.40)),
    (re.compile(r"gpt-5.*mini", re.I), (0.25, 2.00)),
    (re.compile(r"gpt-4\.1-nano", re.I), (0.10, 0.40)),
    (re.compile(r"gpt-4o-mini|gpt-4\.1-mini", re.I), (0.15, 0.60)),
)
PRECIO_DESCONOCIDO = (1.0, 5.0)          # un modelo que no figura: se supone caro, para no subestimar
PAUSA = 1.0 if A.BASE == A.BASE_REAL else 0.0     # con el servidor falso de las autopruebas no se espera entre reintentos

# Campos opcionales que se le quitan al pedido si el servidor dice que no los entiende (en este orden cuando el error no nombra ninguno).
CAMPOS_OPCIONALES = ("response_format", "max_tokens", "temperature", "max_completion_tokens")
# Lo que acepta la accion «texto» del dash (functions/_editor-apimart.js): hasta 6 imagenes, cada data URI hasta 400.000 letras (~300 KB) y max_tokens hasta 6.000.
MAX_IMAGENES = 6
LADO_MAXIMO = 1024
BYTES_MAXIMOS_IMAGEN = 290_000
MAX_TOKENS_DASH = 6000


class ErrorChat(Exception):
    """No se pudo obtener respuesta de ningun modelo (el mensaje dice por que y no lleva la llave).
    grave=True = es un problema de la cuenta o del pedido (clave, saldo, tamaño): cambiar de modelo no lo arregla."""

    def __init__(self, mensaje: str, grave: bool = False):
        super().__init__(mensaje)
        self.grave = grave


@dataclass
class Respuesta:
    texto: str
    datos: object | None            # el JSON ya leido (None si no se pidio JSON o no se pudo leer)
    modelo: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    costo_estimado: float = 0.0
    precio_conocido: bool = True
    intentos: list[str] = field(default_factory=list)   # que paso con los modelos que se probaron antes


class _SiguienteModelo(Exception):
    pass


def precio_de(modelo: str) -> tuple[float, float, bool]:
    for patron, (entrada, salida) in PRECIOS:
        if patron.search(modelo or ""):
            return entrada, salida, True
    return PRECIO_DESCONOCIDO[0], PRECIO_DESCONOCIDO[1], False


def costo_de(modelo: str, entrada: int, salida: int) -> tuple[float, bool]:
    pe, ps, conocido = precio_de(modelo)
    return (entrada * pe + salida * ps) / 1_000_000, conocido


# ------------------------------------------------------------------ lectura de lo que contesta el modelo
def json_de_texto(texto: str | None):
    """El primer objeto JSON dentro del texto del modelo (a veces viene dentro de ```json o con una explicacion antes/despues). None si no hay."""
    t = (texto or "").strip()
    cerca = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if cerca:
        t = cerca.group(1).strip()
    i = t.find("{")
    if i == -1:
        return None
    resto = t[i:]
    for candidato in (resto, re.sub(r",\s*([}\]])", r"\1", resto)):       # el segundo intento quita las comas finales
        try:
            obj, _ = json.JSONDecoder().raw_decode(candidato)
            return obj
        except ValueError:
            continue
    return None


def _desde_sse(crudo: str) -> dict | None:
    """Si el servidor contesta en streaming (lineas «data: {...}») aunque se le pidio stream=false, junta los trozos de texto."""
    trozos, uso = [], {}
    for linea in crudo.splitlines():
        linea = linea.strip()
        if not linea.startswith("data:"):
            continue
        carga = linea[5:].strip()
        if carga == "[DONE]":
            continue
        try:
            j = json.loads(carga)
        except ValueError:
            continue
        for c in j.get("choices") or []:
            parte = (c.get("delta") or {}).get("content") or (c.get("message") or {}).get("content")
            if isinstance(parte, str):
                trozos.append(parte)
        uso = j.get("usage") or uso
    if not trozos:
        return None
    return {"choices": [{"message": {"content": "".join(trozos)}, "finish_reason": "stop"}], "usage": uso}


def _contenido(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c if isinstance(c, str) else ""


def _mensaje_de_error(r) -> str:
    try:
        err = r.json().get("error")
        if isinstance(err, dict):
            return str(err.get("message") or "")[:300]
        if isinstance(err, str):
            return err[:300]
    except (ValueError, AttributeError):
        pass
    return (r.text or "")[:300]


def imagen_a_data_uri(ruta: Path) -> str:
    tipo = "image/png" if ruta.suffix.lower() == ".png" else "image/webp" if ruta.suffix.lower() == ".webp" else "image/jpeg"
    return f"data:{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode('ascii')}"


def _lados(ruta: Path) -> tuple[int, int] | None:
    """(ancho, alto) de una imagen: con Pillow si esta, si no con ffprobe; None si no se pudo medir."""
    try:
        from PIL import Image

        with Image.open(ruta) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:  # noqa: BLE001 - sin Pillow o un formato que no lee: se prueba con ffprobe
        pass
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(ruta)], capture_output=True, text=True, timeout=60)
        s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        return int(s["width"]), int(s["height"])
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def _achicar(ruta: Path, lado: int, calidad: int) -> bytes | None:
    """La imagen en JPEG con a lo sumo `lado` px en su lado mas largo (nunca la agranda), con ffmpeg. None si no se pudo."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    carpeta = Path(tempfile.mkdtemp(prefix="achicar_"))
    try:
        destino = carpeta / "img.jpg"
        r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(ruta), "-vf", f"scale='min({lado},iw)':'min({lado},ih)':force_original_aspect_ratio=decrease",
                            "-frames:v", "1", "-q:v", str(calidad), str(destino)], capture_output=True, text=True, timeout=120)
        return destino.read_bytes() if r.returncode == 0 and destino.is_file() and destino.stat().st_size > 0 else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def imagen_para_enviar(ruta: Path) -> str:
    """La imagen como data URI, lista para el modelo: si ya es chica (PNG, JPEG o WEBP de hasta 1024 px de lado y ~290 KB) va tal cual; si no, se achica a JPEG de 1024 px
    de lado con ffmpeg (el dash no acepta mas de ~300 KB por imagen, y un modelo de vision barato no ve mas detalle que eso)."""
    ruta = Path(ruta)
    datos = ruta.read_bytes()
    lados = _lados(ruta)
    if A.tipo_de_imagen(datos) and lados and max(lados) <= LADO_MAXIMO and len(datos) <= BYTES_MAXIMOS_IMAGEN:
        return imagen_a_data_uri(ruta)
    for lado, calidad in ((LADO_MAXIMO, 4), (LADO_MAXIMO, 9), (768, 12)):
        jpg = _achicar(ruta, lado, calidad)
        if jpg and len(jpg) <= BYTES_MAXIMOS_IMAGEN:
            return "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")
    raise ErrorChat(f"no pude achicar la imagen {ruta.name} para mandarla (hace falta ffmpeg y una imagen que ffmpeg lea): usa una de menos de 1024 px y 290 KB.", grave=True)


class _RespuestaDash:
    """Lo que el dash devolvio de APIMart ({status, cuerpo}) con la forma de una respuesta de requests (status_code, text, json()): asi se lee igual que antes."""

    def __init__(self, status: int, cuerpo):
        self.status_code = int(status)
        if isinstance(cuerpo, (dict, list)):
            self._json = cuerpo
            self.text = json.dumps(cuerpo, ensure_ascii=False)
        else:
            self._json = None
            self.text = "" if cuerpo is None else str(cuerpo)

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


def _transporte_directo(llave: str):
    """POST a /chat/completions del APIMart FALSO de las autopruebas (solo ahi existe el camino directo)."""
    import requests

    def enviar(cuerpo: dict):
        return requests.post(A.BASE + "/chat/completions", headers={"Authorization": f"Bearer {llave}", "Content-Type": "application/json"}, json=cuerpo, timeout=120,
                             allow_redirects=False)
    return enviar


def _transporte_dash(sistema: str, usuario: str, uris: list[str], *, producto, anuncio, etiqueta: str, max_tokens: int, temperatura: float, json_modo: bool):
    """Cada intento con un modelo, por la puerta del dash (accion «texto»). Los campos que se quitaron del pedido van en `quitar`; el dash contesta {status, cuerpo} de APIMart."""
    import requests

    def enviar(cuerpo: dict):
        quitar = [c for c in CAMPOS_OPCIONALES if c not in cuerpo and (c != "response_format" or json_modo)]
        pedido = {"accion": "texto", "producto": (producto or "").strip(), "anuncio": (anuncio or "").strip(), "etiqueta": A.etiqueta_para_dash(etiqueta), "modelo": cuerpo["model"], "sistema": sistema,
                  "usuario": usuario, "imagenes": uris, "max_tokens": min(int(max_tokens), MAX_TOKENS_DASH), "temperatura": temperatura, "json_modo": bool(json_modo), "quitar": quitar}
        try:
            j = A.pedir_dash("POST", cuerpo=pedido, tiempo=150, texto=True)
        except A.ErrorDash as e:
            if e.status == 0:
                raise requests.ConnectionError(str(e)) from None           # sin conexion: se reintenta como antes
            if e.status in (500, 502, 504):
                return _RespuestaDash(e.status, {"error": {"message": str(e)}})          # el dash no llego a APIMart: se reintenta como un 5xx de APIMart
            if e.status == 400 and re.search(r"modelo|l[ií]mites de respuesta", str(e), re.I):
                # un modelo que el dash no permite, o uno que no acepta ni max_tokens ni max_completion_tokens (el dash no deja quitar los dos: la respuesta
                # quedaria sin techo): se prueba el siguiente
                return _RespuestaDash(404, {"error": {"message": str(e)}})
            raise ErrorChat(str(e), grave=True) from None                    # llave, version, tope del dia, editor sin conectar...: cambiar de modelo no lo arregla
        if not isinstance(j.get("status"), int):
            return _RespuestaDash(502, {"error": {"message": "el dash contesto sin el status de APIMart"}})
        return _RespuestaDash(j["status"], j.get("cuerpo"))
    return enviar


# ------------------------------------------------------------------ una llamada
def _un_modelo(enviar, llave: str, modelo: str, mensajes: list[dict], *, max_tokens: int, temperatura: float, json_modo: bool, por_dash: bool = False) -> tuple[str, int, int, str]:
    """Pide al modelo y devuelve (texto, tokens_entrada, tokens_salida, finish_reason). Lanza _SiguienteModelo si ESTE modelo no sirve, ErrorChat si ninguno servira.
    `enviar(cuerpo)` manda el pedido (directo al APIMart falso, o por el dash) y devuelve algo con status_code, text y json(); `llave` es lo que nunca debe salir en un mensaje."""
    import requests

    cuerpo = {"model": modelo, "messages": mensajes, "stream": False, "max_tokens": max_tokens, "max_completion_tokens": max_tokens, "temperature": temperatura}
    if json_modo:
        cuerpo["response_format"] = {"type": "json_object"}
    espera, quitados = 0, 0
    for intento in range(8):                       # tope duro: campos que se quitan + reintentos por red/limite
        try:
            r = enviar(cuerpo)
        except requests.RequestException as e:
            if espera >= 2:
                raise _SiguienteModelo(f"sin conexion con APIMart ({type(e).__name__})") from None
            espera += 1
            time.sleep(PAUSA * 2**espera)
            continue
        if r.status_code == 200:
            break
        detalle = _mensaje_de_error(r).replace(llave, "****")
        bajo = detalle.lower()
        if r.status_code == 401:
            raise ErrorChat("APIMart rechazo la llave del dash (401): avisale al dueño." if por_dash else "APIMart rechazo la clave (401).", grave=True)
        if r.status_code == 402:
            raise ErrorChat("saldo insuficiente en APIMart (402): avisale al dueño (la cuenta es la del dash)." if por_dash else "saldo insuficiente en APIMart (402).", grave=True)
        if r.status_code == 413:
            raise ErrorChat("el pedido es demasiado grande para APIMart (413): usa menos fotogramas.", grave=True)
        if r.status_code in (429, 500, 502, 503, 504):
            if espera >= 2:
                raise _SiguienteModelo(f"HTTP {r.status_code}: {detalle}")
            espera += 1
            time.sleep(PAUSA * 3 * 2**espera)
            continue
        if r.status_code in (400, 403) and re.search(r"insufficient|balance|quota|credit", bajo):
            raise ErrorChat(f"APIMart dice que falta saldo o cuota ({r.status_code}): {detalle[:160]}", grave=True)
        if r.status_code in (403, 404) or (r.status_code == 400 and re.search(r"model|vision|image|multimodal|modalit", bajo) and not any(c in bajo for c in CAMPOS_OPCIONALES)):
            raise _SiguienteModelo(f"HTTP {r.status_code}: {detalle or 'sin detalle'}")
        if r.status_code == 400 and quitados < 4:
            # el servidor no entiende un campo: se quita el que el error nombra, o el siguiente de la lista, y se repite (un 400 no se cobra)
            campo = next((c for c in CAMPOS_OPCIONALES if c in cuerpo and c in bajo), None) or next((c for c in CAMPOS_OPCIONALES if c in cuerpo), None)
            if campo:
                cuerpo.pop(campo)
                quitados += 1
                continue
        raise _SiguienteModelo(f"HTTP {r.status_code}: {detalle or 'sin detalle'}")
    else:
        raise _SiguienteModelo("APIMart no contesto despues de varios intentos")

    crudo = r.text
    try:
        resp = r.json()
    except ValueError:
        resp = _desde_sse(crudo)
    if not isinstance(resp, dict):
        raise _SiguienteModelo("la respuesta no se pudo leer: " + crudo[:120].replace(llave, "****"))
    j = resp if resp.get("choices") else (resp.get("data") if isinstance(resp.get("data"), dict) else resp)
    eleccion = (j.get("choices") or [{}])[0] or {}
    texto = _contenido(eleccion.get("message") or {}).strip()
    uso = j.get("usage") or resp.get("usage") or {}
    entrada = int(uso.get("prompt_tokens") or uso.get("input_tokens") or 0)
    salida = int(uso.get("completion_tokens") or uso.get("output_tokens") or 0)
    if uso.get("total_tokens"):       # los tokens de razonamiento se cobran como salida: si el servidor no los suma en completion_tokens, salen de total - entrada
        salida = max(salida, int(uso["total_tokens"]) - entrada)
    if not texto:
        motivo = resp.get("error") or resp.get("message") or ""
        raise _SiguienteModelo(f"el modelo no devolvio texto (finish_reason={eleccion.get('finish_reason')}){' · ' + str(motivo)[:160].replace(llave, '****') if motivo else ''}")
    return texto, entrada, salida, str(eleccion.get("finish_reason") or "")


def completar(sistema: str, usuario: str, *, imagenes: list[Path] | None = None, modelos: tuple[str, ...] | list[str] | None = None,
              max_tokens: int = 1800, temperatura: float = 0.4, json_modo: bool = True,
              etiqueta: str = "", producto: str | None = None, anuncio: str | None = None, anotar: bool = True) -> Respuesta:
    """Una pregunta (con fotogramas si se dan) a APIMart. Prueba los modelos en orden y devuelve la primera respuesta buena.
    Con json_modo=True `datos` trae el JSON ya leido (o None si el modelo no devolvio uno valido: quien llama decide si repetir)."""
    imagenes = [Path(p) for p in (imagenes or [])]
    if len(imagenes) > MAX_IMAGENES:
        raise ErrorChat(f"se pidieron {len(imagenes)} imagenes y van como mucho {MAX_IMAGENES} por llamada (el dash no acepta mas): usa menos fotogramas.", grave=True)
    if A.modo_directo():
        llave = A.cargar_llave()
        uris = [imagen_para_enviar(p) for p in imagenes]
        enviar, via = _transporte_directo(llave), {}
    else:
        try:
            _, llave = A.conexion()
        except A.ErrorDash as e:
            raise ErrorChat(str(e), grave=True) from None
        problema = A.problema_de_producto_y_anuncio(producto, anuncio)      # el dash anota cada pregunta en un producto y un anuncio: sin ellos la niega
        if problema:
            raise ErrorChat("no pregunto nada: " + problema, grave=True)
        uris = [imagen_para_enviar(p) for p in imagenes]
        enviar = _transporte_dash(sistema, usuario, uris, producto=producto, anuncio=anuncio, etiqueta=etiqueta, max_tokens=max_tokens, temperatura=temperatura, json_modo=json_modo)
        via = {"via": "dash"}
    contenido: list[dict] = [{"type": "text", "text": usuario}]
    for uri in uris:
        contenido.append({"type": "image_url", "image_url": {"url": uri}})
    mensajes = [{"role": "system", "content": sistema}, {"role": "user", "content": contenido if uris else usuario}]
    historia: list[str] = []
    for modelo in (modelos or MODELOS_VISION):
        try:
            texto, entrada, salida, razon = _un_modelo(enviar, llave, modelo, mensajes, max_tokens=max_tokens, temperatura=temperatura, json_modo=json_modo, por_dash=bool(via))
        except _SiguienteModelo as e:
            historia.append(f"{modelo}: {e}")
            continue
        costo, conocido = costo_de(modelo, entrada, salida)
        if anotar:
            A.anotar({"tipo": "texto", "modelo": modelo, "task_id": None, "costo": None, "costo_estimado": round(costo, 6), "tokens_entrada": entrada, "tokens_salida": salida,
                      "imagenes": len(imagenes), "archivo": None, "anuncio": anuncio, "producto": producto, "etiqueta": etiqueta or None, **via})
        return Respuesta(texto=texto, datos=json_de_texto(texto) if json_modo else None, modelo=modelo, tokens_entrada=entrada, tokens_salida=salida,
                         costo_estimado=costo, precio_conocido=conocido, intentos=historia)
    raise ErrorChat("ningun modelo respondio: " + " | ".join(historia))


# ------------------------------------------------------------------ catalogo de la llave (gratis)
def catalogo_chat() -> dict[str, dict]:
    """{id: {tags, categoria}} de los modelos que ve la llave (GET /v1/models, no cuesta). Vacio si no se pudo leer.
    Por el dash no hay catalogo que leer (el editor no tiene la llave y el dash solo deja usar los modelos de MODELOS_VISION): devuelve {} sin llamar a nadie."""
    import requests

    if not A.modo_directo():
        return {}
    llave = A.cargar_llave()
    try:
        r = requests.get(A.BASE + "/models", params={"expand": "category"}, headers={"Authorization": f"Bearer {llave}"}, timeout=60, allow_redirects=False)
        lista = r.json()
        lista = lista if isinstance(lista, list) else lista.get("data", [])
    except (requests.RequestException, ValueError, AttributeError):
        return {}
    return {m["id"]: {"tags": m.get("capability_tags") or [], "categoria": m.get("category")} for m in lista if isinstance(m, dict) and isinstance(m.get("id"), str)}
