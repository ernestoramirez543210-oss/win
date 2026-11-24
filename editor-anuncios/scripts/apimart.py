"""Cliente de APIMart (imagenes y clips de video con IA) del editor de anuncios: TODO pasa por el dash.

Desde la version 2026.11.10-2 (orden del dueño, 2026-11-10) el editor NO tiene la llave de APIMart. Cada imagen, clip, subida de una referencia, consulta de un trabajo y
llamada de texto (apimart_chat.py) va a la puerta del dash `/api/editor-apimart` con la llave del editor (cabecera x-clave-editor = DASH_CLAVE_EDITOR de .env) y la version
instalada (cabecera x-version-editor = el archivo VERSION). El dash pone la llave de APIMart y aplica el tope por anuncio (US$ 0,50; hasta US$ 1 con motivo), la regla de no
repetir clips, el tope del dia y el libro de «Costo por proyecto». Desde aqui NO se puede llamar a APIMart directo: el camino directo de antes solo existe para las autopruebas,
con un APIMart FALSO en esta maquina (APIMART_BASE_URL=http://127.0.0.1:...); con cualquier otra direccion se ignora.

Comandos:
  saldo [--producto p --anuncio AD]      lo que queda del tope del dia del editor y la version minima (con anuncio: lo que lleva gastado). El saldo de la cuenta solo lo ve el dueño
  imagen   --prompt-archivo p.txt [--ref foto.jpg --ref cuadro01.png ...] --salida cuadros/02.png --producto p --anuncio AD
  video    --imagen cuadros/02.png --prompt-archivo v.txt --salida clips/02.mp4 --producto p --anuncio AD
  estado   <task_id>                     consulta un trabajo pedido desde ESTE equipo (su ticket queda en anuncios/_apimart/tickets.json)
  subir    foto.jpg --producto p --anuncio AD   sube una imagen por el dash y da su URL publica (vale unas 70 h)
  costos   [--anuncio AD4]               suma lo gastado por anuncio segun costos.jsonl de este equipo
  enviar-costos [--dry-run]              manda al dash los costos VIEJOS que no le llegaron (lo hecho por el dash ya esta alla y no se manda)
  voz                                    ya no se usa: la voz de los anuncios sale de voz_auto.py (la de los flujos, Fish Audio, por el dash)

Opciones de imagen y video: --producto <oferta> y --anuncio <ID del video> (obligatorias: de ahi sale «Costo por proyecto»), --etiqueta cuadro02, --dry-run (muestra lo que
pediria, sin pedir ni gastar nada), --tope-anuncio X --motivo-tope "por que" (sube el tope de ESE anuncio, hasta US$ 1; lo decide el dash) y, en video, --repetir "motivo"
(repetir un clip ya pagado: solo si una persona lo pidio, uno por anuncio). Lo que el dash niega (tope del anuncio 402, clip repetido 409, tope del dia 429, editor viejo 426)
se muestra tal cual, sale con error y no se gasta nada.

ANTES de subir o pedir nada (tambien con --dry-run), cada prompt pasa la revision de la plantilla (revisar_prompts.py, regla del dueño del 2026-11-10: nunca se regenera nada, asi que
el prompt tiene que ser especifico a la primera): el cuadro dice lugar, angulo, encuadre, accion con objetos concretos y expresion, sin texto legible ni manos escribiendo, y si sale
el producto va con su foto; el clip con dialogo lleva de 25 a 30 palabras por cada 8 s, termina en fin de frase y repite VOICE y ACCENT identicos a los otros clips de la carpeta
(<clip>_prompt.txt); el mudo dice que va en silencio; el prompt tiene hasta 4.000 letras. Si no cumple, sale con la lista de lo que falta y no gasta nada. El dash repite la revision
antes de cobrar y, para imagenes y clips, pide un editor 2026.11.10-3 o mas nuevo (426 a uno anterior, sin gastar).

Sin DASH_URL y DASH_CLAVE_EDITOR en .env el editor no esta conectado y no genera nada. Se conecta bajando la «conexion» en la pagina Editor de anuncios del dash y con doble
clic en Conectar-Editor.cmd (o volviendo a correr Instalar-Editor.cmd).

Modelos (fijados con el dueño; el dash los fija): imagen gpt-image-2 (1k, 9:16, hasta 15 referencias); video grok-imagine-1.5-video-ext (imagen a video, 9:16, 6 a 15 s,
480p o 720p, con voz y labios generados por el modelo).

Los trabajos son asincronos: se piden, se consultan hasta que terminan y el resultado se baja enseguida directo del enlace de APIMart (no lleva llave y caduca).
Cada generacion queda en costos.jsonl con el costo final que dio el dash y "via": "dash"; esas filas NUNCA se mandan a /api/editor-costos (el dash ya las anoto).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import (ANUNCIOS, PUBLIC, RAIZ, configurar_consola, correr, direccion_dash_valida, ejecutable, es_local, escribir_json, leer_json,  # noqa: E402
                    salir, version_editor)
import revisar_prompts as RP  # noqa: E402

BASE = BASE_REAL = "https://api.apimart.ai/v1"
# SOLO para las autopruebas: un APIMart falso en esta misma maquina. Cualquier otra direccion se ignora (y entonces todo va por el dash), para que nadie pueda llamar a APIMart
# directo con una variable de entorno.
_BASE_PRUEBA = os.environ.get("APIMART_BASE_URL", "")
if es_local(_BASE_PRUEBA):
    BASE = _BASE_PRUEBA.rstrip("/")
MODELOS = {"imagen": "gpt-image-2", "video": "grok-imagine-1.5-video-ext", "voz": "gpt-4o-mini-tts"}
COSTOS = RAIZ / "costos.jsonl"
SUBIDAS = ANUNCIOS / "_apimart" / "subidas.json"
TICKETS = ANUNCIOS / "_apimart" / "tickets.json"
# Con el APIMart falso, el registro de costos, de subidas y de tickets va a la carpeta temporal de la autoprueba (APIMART_PRUEBA_DIR): asi nunca toca los de verdad.
_PRUEBA_DIR = os.environ.get("APIMART_PRUEBA_DIR", "")
if BASE != BASE_REAL and _PRUEBA_DIR:
    COSTOS = Path(_PRUEBA_DIR) / "costos.jsonl"
    SUBIDAS = Path(_PRUEBA_DIR) / "subidas.json"
    TICKETS = Path(_PRUEBA_DIR) / "tickets.json"
PUERTA = "/api/editor-apimart"
DASH_OFICIAL = "https://tu_proyecto.pages.dev"
VIGENCIA_SUBIDA_SEG = 70 * 3600       # APIMart borra las subidas a las 72 h
VIGENCIA_TICKET_SEG = 3 * 86400       # el dash firma los tickets por 3 dias
MAX_SUBIDA_BYTES = 10 * 1024 * 1024   # lo que el dash acepta por subida
REINTENTOS_503 = 2                    # el dash dice «reintentar» cuando no pudo anotar la reserva: no pidio nada, se puede repetir
ESPERA_503_SEG = 2.0
# Lo que la puerta del dash acepta (functions/_editor-apimart.js): se mira ANTES de pedir, para decirlo claro y sin gastar nada.
PRODUCTO_DASH = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")          # la oferta: minusculas, numeros, - y _, hasta 41 (como el dash y NOMBRE_PRODUCTO de fabrica.py)
ANUNCIO_DASH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,15}")      # el ID del video: hasta 16
ARCHIVO_DASH = re.compile(r"[^\W_][\w .()-]{0,79}")              # el nombre del archivo de salida: empieza por letra o numero; letras, numeros, espacio y . _ ( ) -; hasta 80
ETIQUETA_MAX = 40
# Lo que el dash estima antes de pedir (informativo: el tope lo aplica el dash con su propia tabla)
PRECIO_IMAGEN_USD = 0.0085
PRECIO_VIDEO_SEG_USD = {"480p": 0.0102, "720p": 0.0191}
NO_HAY_LLAVE = ("Este editor ya no llama a APIMart directo ni tiene su llave: todo pasa por el dash (`python scripts/apimart.py saldo` dice si esta conectado). "
                "No se gasto nada.")


def modo_directo() -> bool:
    """True SOLO con el APIMart FALSO de las autopruebas (APIMART_BASE_URL a esta maquina). En uso real no hay forma de llamar a APIMart directo: todo va por el dash."""
    return BASE != BASE_REAL and es_local(BASE)


# ------------------------------------------------------------------ camino DIRECTO (solo autopruebas, con el APIMart falso)
def cargar_llave() -> str:
    """La llave del APIMart FALSO de las autopruebas (APIMART_API_KEY del entorno). En uso real se niega: el editor ya no tiene llave de APIMart."""
    if not modo_directo():
        salir(NO_HAY_LLAVE)
    llave = os.environ.get("APIMART_API_KEY", "").strip()
    if not llave:
        salir("Falta APIMART_API_KEY en el entorno (solo la usan las autopruebas, con el APIMart falso).")
    if any(c.isspace() for c in llave):
        salir("APIMART_API_KEY tiene espacios o saltos de linea.")
    return llave


def pedir(metodo: str, ruta: str, llave: str, *, reintentos: int = 0, timeout: int = 120, **kw):
    """Una llamada DIRECTA a APIMart. Solo existe para el APIMart falso de las autopruebas: en uso real se niega ANTES de abrir ninguna conexion."""
    if not modo_directo():
        salir(NO_HAY_LLAVE)
    import requests

    ultimo = None
    for intento in range(reintentos + 1):
        try:
            r = requests.request(metodo, BASE + ruta, headers={"Authorization": f"Bearer {llave}"}, timeout=timeout, allow_redirects=False, **kw)
        except requests.RequestException as e:
            ultimo = f"sin conexion con APIMart: {type(e).__name__}"
            time.sleep(2**intento)
            continue
        if r.status_code in (429, 500, 502, 503) and intento < reintentos:
            ultimo = f"HTTP {r.status_code}"
            time.sleep(2**intento)
            continue
        if r.status_code >= 400:
            explicar_error(r)
        return r
    salir(f"APIMart no respondio ({ultimo}).")


def explicar_error(r) -> None:
    try:
        err = r.json().get("error", {})
        detalle = err.get("message") or r.text[:200]
    except (ValueError, AttributeError):
        detalle = r.text[:200]
    claves = {401: "la clave fue rechazada.", 402: "saldo insuficiente.", 413: "el archivo o el texto es demasiado grande.", 429: "demasiadas peticiones; espera un momento."}
    salir(f"APIMart contesto {r.status_code}: {claves.get(r.status_code, detalle)} ({detalle})")


# ------------------------------------------------------------------ el dash: conexion y puerta
def leer_env(nombre: str) -> str:
    """Un valor del archivo .env (NOMBRE=valor) o ''. Nunca lo imprime."""
    env = RAIZ / ".env"
    if not env.is_file():
        return ""
    for linea in env.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip().lstrip("﻿")
        if linea.startswith(nombre + "="):
            return linea.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _valores_dash() -> tuple[str, str]:
    """(DASH_URL, DASH_CLAVE_EDITOR) tal como estan: variables de entorno primero (el corredor del servidor) y luego .env. Con el APIMart falso de las autopruebas NO se lee .env."""
    prueba = BASE != BASE_REAL
    url = os.environ.get("DASH_URL", "").strip() or ("" if prueba else leer_env("DASH_URL"))
    llave = os.environ.get("DASH_CLAVE_EDITOR", "").strip() or ("" if prueba else leer_env("DASH_CLAVE_EDITOR"))
    return url.rstrip("/"), llave


def config_dash() -> tuple[str, str] | None:
    """(direccion, llave) del dash si estan bien puestas; si no, None.
    La llave solo viaja por https al dash de verdad (o a 127.0.0.1 en las pruebas): una direccion mal escrita no puede mandarla en claro ni a otro lado."""
    url, llave = _valores_dash()
    if not url or not llave or any(c.isspace() for c in llave):
        return None
    if not direccion_dash_valida(url):
        return None
    return url, llave


def sin_conexion(gasto: bool = True) -> str:
    """Por que este editor no esta conectado al dash y como se conecta (sin mostrar la llave)."""
    url, llave = _valores_dash()
    if url and not direccion_dash_valida(url):
        detalle = f"DASH_URL de .env no es la direccion del dash: debe ser exactamente {DASH_OFICIAL}"
    elif llave and any(c.isspace() for c in llave):
        detalle = "DASH_CLAVE_EDITOR de .env tiene espacios o saltos de linea: debe ir entera, en una sola linea"
    else:
        detalle = "faltan DASH_URL y DASH_CLAVE_EDITOR en el archivo .env"
    return (f"Este editor no esta conectado al dash ({detalle}). Para conectarlo: en el dash, pagina «Editor de anuncios», boton «Descargar conexion»; despues doble clic "
            "en Conectar-Editor.cmd (en la carpeta del editor) o vuelve a correr Instalar-Editor.cmd. No pegues esas lineas en el chat." + (" No se gasto nada." if gasto else ""))


class ErrorDash(Exception):
    """El dash dijo que no (o no contesto). `status`: el HTTP del dash (0 = sin conexion, -1 = editor sin conectar). El mensaje nunca lleva la llave."""

    def __init__(self, mensaje: str, status: int = 0, datos: dict | None = None):
        super().__init__(mensaje)
        self.status = status
        self.datos = datos if isinstance(datos, dict) else {}


def conexion() -> tuple[str, str]:
    cfg = config_dash()
    if not cfg:
        raise ErrorDash(sin_conexion(), -1)
    return cfg


def _ocultar(texto: str, llave: str) -> str:
    return texto.replace(llave, "****") if llave else texto


def mensaje_de_error_dash(status: int, j: dict) -> str:
    """Lo que el dash contesto, TAL CUAL (su campo error), y que hacer."""
    error = " ".join(str(j.get("error") or "").split())[:600] or "sin detalle"
    if status == 401:
        return (f"El dash no acepto el pedido (401): {error}. Si dice que la llave no sirve, vuelve a conectar el editor: en el dash, pagina «Editor de anuncios», "
                "«Descargar conexion», y doble clic en Conectar-Editor.cmd.")
    if status == 426:
        minima = str(j.get("versionMinima") or "una mas nueva")
        return (f"Este editor esta viejo para el dash (426): {error} Tienes la version {version_editor(RAIZ) or '?'} y el dash pide {minima}. Actualizalo: baja el instalador "
                "del dash (Herramientas > Editor de anuncios) y corre Instalar-Editor.cmd. No se gasto nada.")
    if status in (402, 409, 429):
        return f"El dash no lo deja ({status}): {error} No se gasto nada."
    if status == 503:
        return f"El dash no puede pedirle a APIMart ahora (503): {error}" + (" No se gasto nada; repite en un rato." if j.get("reintentar") else "")
    if status == 400 and isinstance(j.get("revision"), list) and j["revision"]:
        lineas = [" ".join(str(x.get("mensaje") or "").split())[:300] for x in j["revision"][:12] if isinstance(x, dict)]
        return ("El dash rechazo el prompt (400): no cumple la plantilla de references/prompts-ia.md (revision automatica, antes de pagar). No se gasto nada.\n"
                + "\n".join("  - " + x for x in lineas if x))
    if status in (400, 405, 413):
        return f"El dash rechazo el pedido ({status}): {error}"
    return f"El dash contesto HTTP {status}: {error}"


def pedir_dash(metodo: str, *, consulta: dict | None = None, cuerpo: dict | None = None, datos: bytes | None = None, tipo: str = "application/octet-stream",
               tiempo: int = 90, texto: bool = False) -> dict:
    """Una llamada a la puerta del dash (/api/editor-apimart) con la llave del editor y la version instalada. Devuelve el JSON del dash o lanza ErrorDash.
    Sin seguir redirecciones (requests conservaria la cabecera con la llave al cambiar de sitio). Un 503 con «reintentar» (el dash no anoto la reserva y no pidio nada) se repite
    2 veces, con 2 s de espera. texto=True: la respuesta de la accion «texto» ({status, cuerpo} de APIMart) se devuelve aunque APIMart haya dicho que no."""
    import requests

    url, llave = conexion()
    cabeceras = {"x-clave-editor": llave, "x-version-editor": version_editor(RAIZ), "Accept": "application/json"}
    for intento in range(REINTENTOS_503 + 1):
        try:
            if datos is not None:
                r = requests.request(metodo, url + PUERTA, params=consulta, data=datos, headers={**cabeceras, "Content-Type": tipo}, timeout=tiempo, allow_redirects=False)
            else:
                r = requests.request(metodo, url + PUERTA, params=consulta, json=cuerpo, headers=cabeceras, timeout=tiempo, allow_redirects=False)
        except requests.RequestException as e:
            raise ErrorDash(f"Sin conexion con el dash ({type(e).__name__}). Revisa internet y DASH_URL en .env.", 0) from None
        if 300 <= r.status_code < 400:
            raise ErrorDash(f"El dash contesto con una redireccion y no la sigo: la llave del editor no sale de la direccion de DASH_URL en .env (debe ser {DASH_OFICIAL}).", r.status_code)
        try:
            j = r.json()
        except ValueError:
            j = None
        j = j if isinstance(j, dict) else {}
        if texto and isinstance(j.get("status"), int) and "cuerpo" in j:
            return j
        if r.status_code == 503 and j.get("reintentar") is True and intento < REINTENTOS_503:
            time.sleep(ESPERA_503_SEG)
            continue
        if r.status_code >= 400 or not j or j.get("ok") is False:
            raise ErrorDash(_ocultar(mensaje_de_error_dash(r.status_code, j), llave), r.status_code, j)
        return j
    raise ErrorDash("El dash no contesto.", 0)       # no se llega: el ultimo intento siempre devuelve o lanza


def estado_dash(producto: str | None = None, anuncio: str | None = None) -> dict:
    """GET ?estado=1: tope del dia, lo gastado hoy, la version minima y (con producto y anuncio) lo que lleva ese anuncio. Gratis; nunca el saldo de la cuenta."""
    consulta = {"estado": "1"}
    if producto and anuncio:
        consulta.update(producto=producto, anuncio=anuncio)
    return pedir_dash("GET", consulta=consulta, tiempo=30)


def _num(v) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) and n > 0 else 0.0


def _tupla_version(v) -> tuple[int, ...] | None:
    """2026.11.10-3 -> (2026, 11, 10, 3), como versionAlDia del dash; None si no tiene esa forma."""
    m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})-(\d{1,4})", str(v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def ficha_del_dash(producto: str, anuncio: str) -> dict | None:
    """Lo que el dash lleva de ESTE anuncio: {"gastado_usd", "clips_pagados", "repetidos"}; None si no se pudo preguntar (autopruebas con el APIMart falso, editor sin
    conectar o el dash no contesto). El gasto es el que usa el tope del dash: ya incluye lo que tenia de antes de la puerta (legadoUsd, que viene aparte solo para mostrarlo).
    Un anuncio que todavia no pidio nada por el dash viene con gastadoUsd 0 y legadoSinCalcular (legadoUsd null): el dash no recorre el libro para el estado y suma lo de antes
    en el primer pedido; quien llama toma lo mayor entre esto y lo anotado en este equipo (anuncio_comun.gastado_en_anuncio)."""
    if modo_directo() or not config_dash():
        return None
    try:
        j = estado_dash(producto, anuncio)
    except ErrorDash:
        return None
    an = j.get("anuncio") if isinstance(j.get("anuncio"), dict) else {}
    clips = an.get("clipsPagados") if isinstance(an.get("clipsPagados"), list) else []
    try:
        repetidos = max(0, int(an.get("repetidos") or 0))
    except (TypeError, ValueError):
        repetidos = 0
    return {"gastado_usd": round(_num(an.get("gastadoUsd")), 6), "clips_pagados": len(clips), "repetidos": repetidos}


# ------------------------------------------------------------------ utilidades
def leer_texto(directo: str | None, archivo: str | None, nombre: str) -> str:
    if archivo:
        p = Path(archivo)
        if not p.is_file():
            salir(f"No existe {archivo}")
        texto = p.read_text(encoding="utf-8").strip()
    else:
        texto = (directo or "").strip()
    if not texto:
        salir(f"Falta {nombre}: usa --{nombre} o --{nombre}-archivo")
    return texto


def a_data_uri(ruta: Path) -> str:
    import base64

    tipo = mimetypes.guess_type(ruta.name)[0] or "image/png"
    return f"data:{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode('ascii')}"


def tipo_de_imagen(datos: bytes) -> str | None:
    """El tipo de una imagen por sus primeros bytes (como lo mira el dash): PNG, JPEG o WEBP; None si es otra cosa."""
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if datos[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


def sha1(ruta: Path) -> str:
    return hashlib.sha1(ruta.read_bytes()).hexdigest()


def buscar_urls(obj) -> list[str]:
    """Todas las URL http(s) dentro de un resultado, en orden (los videos no documentan su forma exacta)."""
    if isinstance(obj, str):
        return [obj] if obj.startswith("http") else []
    if isinstance(obj, dict):
        return [u for v in obj.values() for u in buscar_urls(v)]
    if isinstance(obj, list):
        return [u for v in obj for u in buscar_urls(v)]
    return []


def texto_de_error(err) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("mensaje") or json.dumps(err, ensure_ascii=False))[:300]
    return str(err or "sin detalle")[:300]


def anotar(registro: dict) -> None:
    """Anota una generacion en costos.jsonl. Lo hecho POR EL DASH (via = dash) ya esta en su libro: solo se anota aqui, nunca se manda a /api/editor-costos."""
    registro = {"fecha": datetime.now().isoformat(timespec="seconds"), **registro}
    try:
        with COSTOS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError as e:
        # lo generado ya esta pagado y el resultado ya se bajo: un registro local que no se puede escribir no lo tumba (quien llama borraria y volveria a pagar)
        print(f"  (aviso: no pude anotar este costo en {COSTOS.name}: {type(e).__name__}" + ("; el dash si lo tiene anotado" if registro.get("via") == "dash" else "") + ")", flush=True)
    if registro.get("via") == "dash":
        return
    r = enviar_al_dash([registro])
    if not r["ok"]:
        try:
            with ruta_pendientes().open("a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            queda = "Queda pendiente: se manda solo en la proxima generacion o con `apimart.py enviar-costos`."
        except OSError as e:
            queda = f"Tampoco se pudo dejar en la cola ({type(e).__name__}): sigue en costos.jsonl; mandalo con `apimart.py enviar-costos`."
        print(f"  (aviso: el dash no recibio este costo: {r['mensaje']}. {queda})", flush=True)


# ------------------------------------------------------------------ costos VIEJOS al dash (/api/editor-costos, pagina «Costo por proyecto»)
def entrada_para_dash(r: dict) -> dict:
    """Un renglon de costos.jsonl como lo espera el dash: la hora en UTC con Z (el renglon trae la hora del PC, sin zona)."""
    fecha = str(r.get("fecha") or "")
    try:
        fecha = datetime.fromisoformat(fecha).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except ValueError:
        pass  # el dash la lee o la rechaza
    return {**{k: r.get(k) for k in ("tipo", "modelo", "costo", "anuncio", "producto", "etiqueta", "task_id")}, "fecha": fecha}


def para_editor_costos(registros: list[dict]) -> list[dict]:
    """Lo que SI se manda a /api/editor-costos: renglones con costo que NO hizo el dash (lo hecho por el dash ya esta en su libro y contaria doble)."""
    return [r for r in registros if isinstance(r, dict) and r.get("via") != "dash" and r.get("costo") not in (None, 0, 0.0)]


def enviar_al_dash(registros: list[dict]) -> dict:
    """Manda costos al dash, de 40 en 40. NUNCA lanza: lo que ya se pago no se pierde por un aviso que falle.
    Devuelve {ok, mensaje, configurado, enviadas, nuevas, repetidas, rechazadas}. La voz no trae costo y lo hecho por el dash ya esta alla: no se mandan."""
    res = {"ok": True, "mensaje": "", "configurado": False, "enviadas": 0, "nuevas": 0, "repetidas": 0, "rechazadas": 0}
    cfg = config_dash()
    if not cfg:
        return res
    res["configurado"] = True
    url, llave = cfg
    con_costo = para_editor_costos(registros)
    try:
        import requests

        for i in range(0, len(con_costo), 40):
            lote = con_costo[i:i + 40]
            # Sin seguir redirecciones: requests conserva las cabeceras personalizadas al cambiar de sitio y la llave del editor no debe salir de la direccion que puso el dueño.
            r = requests.post(url + "/api/editor-costos", json={"entradas": [entrada_para_dash(x) for x in lote]}, headers={"x-clave-editor": llave}, timeout=15, allow_redirects=False)
            if 300 <= r.status_code < 400:
                return {**res, "ok": False, "mensaje": "el dash contesto con una redireccion; no la sigo (la llave no sale de la direccion de DASH_URL en .env)"}
            if r.status_code == 401:
                return {**res, "ok": False, "mensaje": "el dash rechazo la llave (DASH_CLAVE_EDITOR de .env)"}
            if r.status_code == 503:
                return {**res, "ok": False, "mensaje": "al dash todavia le falta la llave del editor"}
            if r.status_code >= 400:
                return {**res, "ok": False, "mensaje": f"el dash contesto HTTP {r.status_code}"}
            j = r.json()
            res["enviadas"] += len(lote)
            res["nuevas"] += int(j.get("nuevas") or 0)
            res["repetidas"] += int(j.get("repetidas") or 0)
            res["rechazadas"] += len(j.get("rechazadas") or [])
            if j.get("fallidas"):
                return {**res, "ok": False, "mensaje": "el dash no pudo guardar todo (tope diario de escrituras); se reintenta con enviar-costos"}
    except Exception as e:  # noqa: BLE001 - sin internet, DNS, tiempo agotado...: da igual, el costo ya esta en costos.jsonl
        return {**res, "ok": False, "mensaje": f"sin conexion con el dash ({type(e).__name__})"}
    return res


def ruta_pendientes() -> Path:
    """Costos ya pagados (de antes de pasar por el dash) que el dash no alcanzo a recibir. Va junto a costos.jsonl; en el servidor, en estado/, donde el corredor si puede escribir."""
    estado = COSTOS.parent / "estado"
    return (estado if estado.is_dir() else COSTOS.parent) / "costos_pendientes.jsonl"


def reenviar_pendientes() -> None:
    """Manda lo que quedo sin avisar. El dash reconoce lo repetido por su tarea, asi que mandar dos veces no cuenta doble."""
    ruta = ruta_pendientes()
    if not ruta.is_file():
        return
    filas = []
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if linea.strip():
                filas.append(json.loads(linea))
        except ValueError:
            continue  # un renglon cortado (un apagon a mitad de escribir) no frena las generaciones: ese costo sigue en costos.jsonl
    if enviar_al_dash(filas)["ok"]:
        ruta.unlink(missing_ok=True)
        print(f"  (se mandaron al dash {len(para_editor_costos(filas))} costos que habian quedado pendientes.)", flush=True)


# ------------------------------------------------------------------ subidas (URL publicas de las imagenes locales) y tickets
def leer_subidas() -> dict:
    try:
        d = leer_json(SUBIDAS) if SUBIDAS.is_file() else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def guardar_url(ruta: Path, url: str, expira: float) -> None:
    datos = leer_subidas()
    datos[str(ruta.resolve())] = {"sha1": sha1(ruta), "url": url, "expira": expira}
    escribir_json(SUBIDAS, datos)


def url_vigente(ruta: Path) -> str | None:
    d = leer_subidas().get(str(ruta.resolve()))
    if isinstance(d, dict) and d.get("sha1") == sha1(ruta) and float(d.get("expira") or 0) > time.time() + 600:
        url = d.get("url")
        if not modo_directo() and not str(url or "").startswith("https://"):
            return None             # a la puerta del dash solo van direcciones https: esta se vuelve a subir
        return url
    return None


def leer_tickets() -> dict:
    try:
        d = leer_json(TICKETS) if TICKETS.is_file() else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def guardar_ticket(task_id: str, ticket: str, datos: dict) -> None:
    """El ticket firmado del dash es lo unico que deja consultar un trabajo: se guarda por tarea (los vencidos se van solos)."""
    ahora = time.time()
    d = {k: v for k, v in leer_tickets().items() if isinstance(v, dict) and float(v.get("creado") or 0) > ahora - VIGENCIA_TICKET_SEG - 86400}
    d[task_id] = {"ticket": ticket, "creado": ahora, **datos}
    escribir_json(TICKETS, d)


def subir(ruta: Path, producto: str | None = None, anuncio: str | None = None, llave: str | None = None) -> str:
    """La URL publica de una imagen local (la sube si no hay una vigente para ESE contenido). Por el dash: los bytes tal cual (PNG, JPEG o WEBP, hasta 10 MB)."""
    previa = url_vigente(ruta)
    if previa:
        return previa
    if modo_directo():
        with ruta.open("rb") as f:
            r = pedir("POST", "/uploads/images", llave or cargar_llave(), files={"file": (ruta.name, f, mimetypes.guess_type(ruta.name)[0] or "image/png")})
        url = r.json().get("url")
        if not url:
            salir(f"APIMart no devolvio la URL de la imagen subida: {r.text[:200]}")
        guardar_url(ruta, url, time.time() + VIGENCIA_SUBIDA_SEG)
        return url
    datos = ruta.read_bytes()
    tipo = tipo_de_imagen(datos)
    if not tipo:
        raise ErrorDash(f"{ruta.name} no es PNG, JPEG ni WEBP: el dash solo sube esas. Conviertela (por ejemplo a .jpg) y repite. No se gasto nada.", 400)
    if len(datos) > MAX_SUBIDA_BYTES:
        raise ErrorDash(f"{ruta.name} pesa {len(datos) / 1048576:.1f} MB y el dash acepta hasta 10 MB: achicala y repite. No se gasto nada.", 413)
    problema = problema_de_producto_y_anuncio(producto, anuncio)
    if problema:
        raise ErrorDash(f"No subo nada: {problema} No se gasto nada.", 400)
    j = pedir_dash("POST", consulta={"subir": "1", "producto": producto.strip(), "anuncio": anuncio.strip()}, datos=datos, tipo=tipo, tiempo=180)
    url = j.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ErrorDash("El dash no devolvio la direccion de la imagen subida.", 502)
    expira = _num(j.get("expira")) or time.time() + VIGENCIA_SUBIDA_SEG
    guardar_url(ruta, url, min(expira, time.time() + VIGENCIA_SUBIDA_SEG))
    return url


def descargar(url: str, destino: Path, fila_si_falla: dict | None = None) -> None:
    """Baja el resultado directo del enlace de APIMart: SIN la llave del editor ni ninguna otra cabecera propia.
    Si la bajada falla (red, enlace vencido), lo generado YA se pago: con `fila_si_falla` se anota el costo igual y se dice como volver a pedir el enlace."""
    import requests

    if not str(url).startswith(("https://", "http://")):
        salir(f"El resultado no trae un enlace para bajarlo: {str(url)[:120]}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")      # se baja aparte: si falla a la mitad, el archivo que ya estaba no se pierde
    try:
        with requests.get(url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with parcial.open("wb") as f:
                for trozo in r.iter_content(1 << 20):
                    f.write(trozo)
        os.replace(parcial, destino)
    except (requests.RequestException, OSError) as e:
        try:
            parcial.unlink()
        except OSError:
            pass
        if fila_si_falla is None:
            raise
        anotar({**fila_si_falla, "descarga_fallida": True})
        tarea = fila_si_falla.get("task_id") or "?"
        salir(f"Se genero (y se pago) la tarea {tarea}, pero no pude bajar el resultado: {type(e).__name__}. No la pidas otra vez: "
              f"`python scripts/apimart.py estado {tarea}` muestra el enlace (dura unas horas) para bajarlo a mano.")


def esperar_directo(llave: str, task_id: str, cada: int, maximo: int) -> dict:
    t0 = time.time()
    ultimo = None
    while True:
        r = pedir("GET", f"/tasks/{task_id}", llave, reintentos=3, params={"language": "es"})
        d = r.json().get("data", {})
        estado = d.get("status")
        if estado == "completed":
            return d
        if estado in ("failed", "cancelled"):
            salir(f"El trabajo {task_id} termino como '{estado}': {texto_de_error(d.get('error'))}")
        avance = d.get("progress")
        if (estado, avance) != ultimo:
            print(f"  {estado} {avance if avance is not None else ''} %".replace("  %", ""), flush=True)
            ultimo = (estado, avance)
        if time.time() - t0 > maximo:
            salir(f"El trabajo {task_id} sigue en '{estado}' despues de {maximo} s. No se pierde: consulta luego con `estado {task_id}`.")
        time.sleep(cada)


def esperar_dash(task_id: str, ticket: str, cada: int, maximo: int) -> dict:
    """Consulta el trabajo por el dash (con su ticket) hasta que termine. Si el dash no contesta un rato, insiste; el trabajo nunca se pierde."""
    t0 = time.time()
    ultimo = None
    fallos = 0
    while True:
        try:
            d = pedir_dash("POST", cuerpo={"accion": "tarea", "ticket": ticket}, tiempo=60)
            fallos = 0
        except ErrorDash as e:
            if (e.status == 0 or e.status >= 500) and fallos < 3:
                fallos += 1
                print(f"  (el dash no contesto la consulta: {e} Reintento en {cada} s.)", flush=True)
                time.sleep(cada)
                continue
            salir(f"{e} El trabajo {task_id} no se pierde: consultalo luego con `python scripts/apimart.py estado {task_id}`.")
        estado = str(d.get("estado") or "")
        if estado == "completed":
            return d
        if estado in ("failed", "cancelled"):
            salir(f"El trabajo {task_id} termino como '{estado}': {texto_de_error(d.get('error'))}")
        avance = d.get("avance")
        if (estado, avance) != ultimo:
            print(f"  {estado or '?'} {avance if avance is not None else ''} %".replace("  %", ""), flush=True)
            ultimo = (estado, avance)
        if time.time() - t0 > maximo:
            salir(f"El trabajo {task_id} sigue en '{estado}' despues de {maximo} s. No se pierde: consultalo luego con `python scripts/apimart.py estado {task_id}`.")
        time.sleep(cada)


def contexto(a) -> dict:
    return {"anuncio": a.anuncio, "producto": a.producto, "etiqueta": a.etiqueta}


def comprobar_modelo(a, tipo: str) -> str:
    modelo = a.modelo or MODELOS[tipo]
    if modelo == MODELOS[tipo]:
        return modelo
    if not modo_directo():
        salir(f"Por el dash el modelo de {tipo} es fijo ({MODELOS[tipo]}): otro modelo lo decide el dueño en el dash. No se gasto nada.")
    if not a.cambio_de_modelo_aprobado:
        salir(f"El modelo de {tipo} esta fijado en {MODELOS[tipo]}. Para usar '{modelo}' el dueño debe aprobarlo y se agrega --cambio-de-modelo-aprobado.")
    return modelo


def validar_tope(a) -> None:
    """--tope-anuncio: un numero mayor que 0 (el dash decide hasta cuanto: US$ 1 como mucho, y con --motivo-tope si pasa de US$ 0,50). nan o inf no viajan en JSON."""
    if getattr(a, "tope_anuncio", None) is None:
        return
    if not (math.isfinite(a.tope_anuncio) and a.tope_anuncio > 0):
        salir("--tope-anuncio debe ser un numero mayor que 0 (el dash acepta hasta US$ 1, y con --motivo-tope si pasa de US$ 0,50). No se gasto nada.")


def cuerpo_comun(a, accion: str) -> dict:
    """Lo que el dash exige en cada pedido que cuesta: para que oferta y que anuncio es (de ahi sale «Costo por proyecto»), y el tope si se sube."""
    cuerpo = {"accion": accion, "producto": (a.producto or "").strip(), "anuncio": (a.anuncio or "").strip(), "etiqueta": etiqueta_para_dash(a.etiqueta),
              "archivo": Path(a.salida).name}
    if a.tope_anuncio is not None:
        cuerpo["topeAnuncio"] = a.tope_anuncio
    if (a.motivo_tope or "").strip():
        cuerpo["motivoTope"] = a.motivo_tope.strip()[:200]
    return cuerpo


def etiqueta_para_dash(etiqueta) -> str:
    """La etiqueta (para que es, dentro del anuncio) como la guarda el dash: sin saltos ni «|», en una linea y de hasta 40 letras."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f|]+", " ", str(etiqueta or "")).split())[:ETIQUETA_MAX].strip()


def problema_de_producto_y_anuncio(producto, anuncio) -> str | None:
    """Por que el dash no aceptaria este producto o este anuncio (None = estan bien). No llama a nadie."""
    p, a = (producto or "").strip(), (anuncio or "").strip()
    if not p or not a:
        return "falta decir para que es este gasto. Usa --producto <oferta> y --anuncio <ID del video>, para que quede en «Costo por proyecto» del dash."
    if not PRODUCTO_DASH.fullmatch(p):
        return (f"el dash solo acepta el producto en minusculas (letras, numeros, - y _), empezando por letra o numero y de hasta 41 letras; «{p[:60]}» no sirve "
                "(si el producto se llama asi, avisale al dueño).")
    if not ANUNCIO_DASH.fullmatch(a):
        return f"el dash solo acepta el ID del anuncio con letras, numeros, - y _, empezando por letra o numero y de hasta 16; «{a[:40]}» no sirve."
    return None


def exigir_producto_y_anuncio(a) -> None:
    problema = problema_de_producto_y_anuncio(a.producto, a.anuncio)
    if problema:
        salir("No genero nada: " + problema)


def exigir_formato_dash(a, tipo: str) -> None:
    """Lo que el dash niega con 400 de una imagen o un clip, dicho antes de subir nada: el nombre de la salida, 9:16, 1k (imagen) o 480p/720p (clip)."""
    nombre = Path(a.salida).name
    if not ARCHIVO_DASH.fullmatch(nombre):
        salir(f"No genero nada: el dash pide que el archivo de salida se llame con letras, numeros, espacios y . _ ( ) -, empezando por letra o numero y de hasta 80; "
              f"«{nombre[:90]}» no sirve. Cambia --salida.")
    if a.tamano != "9:16":
        salir("No genero nada: por el dash las imagenes y los clips van en 9:16 (--tamano 9:16).")
    if tipo == "imagen" and a.resolucion != "1k":
        salir("No genero nada: por el dash la imagen va en 1k (--resolucion 1k): el precio estimado es para eso.")
    if tipo == "video" and a.resolucion not in PRECIO_VIDEO_SEG_USD:
        salir("No genero nada: el clip va en 480p o 720p (--resolucion).")


def revisar_o_salir(problemas: list, que: str) -> None:
    """La revision de la plantilla (revisar_prompts.py): si algo no cumple, sale con la lista y sin gastar nada."""
    if problemas:
        salir("No genero nada: " + RP.mensaje(problemas, que) + "\nCorrige el prompt y repite. No se gasto nada.")


def carpeta_del_producto(producto) -> Path | None:
    """public/productos/<producto> si el nombre es valido (para saber cuales referencias son fotos del producto)."""
    p = (producto or "").strip()
    return (PUBLIC / "productos" / p) if PRODUCTO_DASH.fullmatch(p) else None


def preparar_pedido_dash(a) -> None:
    """Antes de pedir algo que cuesta: el editor conectado (si no, dice como) y lo pendiente de antes, mandado."""
    exigir_producto_y_anuncio(a)
    if not config_dash():
        salir(sin_conexion())
    reenviar_pendientes()


def pedir_generacion(cuerpo: dict, tipo: str, salida: Path) -> tuple[str, str, float | None]:
    """Pide al dash una imagen o un clip y guarda su ticket. Devuelve (tarea, ticket, costo estimado del dash)."""
    j = pedir_dash("POST", cuerpo=cuerpo, tiempo=120)
    tarea = j.get("tarea") if isinstance(j.get("tarea"), dict) else {}
    task_id, ticket = str(tarea.get("id") or ""), str(tarea.get("ticket") or "")
    if not task_id or not ticket or len(task_id) > 200 or any(c.isspace() for c in task_id):
        salir(f"El dash acepto el pedido pero no devolvio la tarea con su ticket: {json.dumps(j, ensure_ascii=False)[:200]}")
    estimado = _num(j.get("estimadoUsd")) or None
    guardar_ticket(task_id, ticket, {"tipo": tipo, "producto": cuerpo.get("producto"), "anuncio": cuerpo.get("anuncio"), "etiqueta": cuerpo.get("etiqueta"), "archivo": str(salida),
                                     "estimado": estimado})
    return task_id, ticket, estimado


def costo_final(d: dict, estimado: float | None) -> tuple[float | None, bool]:
    """(costo, es_estimado): el costo real que dio el dash al terminar; si no lo trae, el estimado (es lo que queda en su libro)."""
    try:
        c = float(d.get("costoUsd"))
        if math.isfinite(c) and c >= 0:
            return round(c, 6), False
    except (TypeError, ValueError):
        pass
    return (round(estimado, 6) if estimado is not None else None), True


def mostrar_dry_run(titulo: str, ruta: str, payload: dict) -> None:
    recortado = json.loads(json.dumps(payload))
    for k, v in recortado.items():
        if isinstance(v, list):
            recortado[k] = [(x[:60] + "...(base64)") if isinstance(x, str) and x.startswith("data:") else x for x in v]
    print(f"[DRY-RUN] {titulo}: POST {BASE}{ruta}\n{json.dumps(recortado, ensure_ascii=False, indent=2)}\nNo se envio nada ni se gasto nada.")


def mostrar_dry_run_dash(titulo: str, cuerpo: dict, estimado: float, notas: list[str]) -> None:
    cfg = config_dash()
    destino = (cfg[0] + PUERTA) if cfg else "(el dash: este editor todavia no esta conectado)"
    print(f"[DRY-RUN] {titulo}: POST {destino}\n{json.dumps(cuerpo, ensure_ascii=False, indent=2)}" + "".join(f"\n{n}" for n in notas)
          + f"\nCosto estimado: US$ {estimado:.4f} (lo cuenta el dash contra el tope del anuncio). No se envio nada ni se gasto nada.")


# ------------------------------------------------------------------ comandos
def cmd_saldo(a) -> None:
    if modo_directo():
        d = pedir("GET", "/user/balance", cargar_llave(), reintentos=2).json()
        print(f"Saldo restante: {d.get('remain_balance')}  (creditos: {d.get('remain_credits')})")
        print(f"Gastado:        {d.get('used_balance')}  (creditos: {d.get('used_credits')})")
        print("Unidad segun la configuracion de APIMart (normalmente USD).")
        return
    if not config_dash():
        salir(sin_conexion(gasto=False))
    if a.producto or a.anuncio:
        problema = problema_de_producto_y_anuncio(a.producto, a.anuncio)
        if problema:
            salir("No pregunto: " + problema)
    j = estado_dash(a.producto, a.anuncio)
    tope, hoy = _num(j.get("topeDiaUsd")), _num(j.get("gastadoHoyUsd"))
    print(f"Conectado al dash ({config_dash()[0]}).")
    print(f"Tope del dia del editor: US$ {tope:.2f} · gastado hoy: US$ {hoy:.2f} · queda: US$ {max(0.0, tope - hoy):.2f}  (todo lo que el editor gasta en APIMart; el dia es UTC; "
          "lo recien gastado puede tardar un minuto en sumarse)")
    print(f"Tope por anuncio: US$ {_num(j.get('topeAnuncioUsd')):.2f} (hasta US$ {_num(j.get('techoAnuncioUsd')):.2f} con --tope-anuncio y --motivo-tope)")
    minima, mia = str(j.get("versionMinima") or "?"), version_editor(RAIZ) or "?"
    # imagenes y clips piden una version mas nueva que el resto (la que revisa los prompts antes de pagar): un editor puede estar conectado y aun asi recibir 426 al pedirlas
    minima_iv = str(j.get("versionMinimaImagenVideo") or "")[:40]
    print(f"Version del editor: {mia} (el dash pide como minimo {minima}" + (f"; para imagenes y clips, {minima_iv}" if minima_iv and minima_iv != minima else "") + ")")
    t_iv, t_mia = _tupla_version(minima_iv), _tupla_version(mia)
    if t_iv is not None and (t_mia is None or t_mia < t_iv):
        print(f"  OJO: con la version {mia} el dash NO deja pedir imagenes ni clips (426, sin gastar). Actualiza el editor: baja el instalador del dash (Herramientas > "
              "Editor de anuncios) y corre Instalar-Editor.cmd.")
    modelos = j.get("modelos") if isinstance(j.get("modelos"), dict) else {}
    if modelos:
        print("Modelos: " + "; ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in modelos.items()))
    an = j.get("anuncio") if isinstance(j.get("anuncio"), dict) else None
    if a.producto and a.anuncio:
        if an is None:
            print(f"Anuncio {a.anuncio} de {a.producto}: el dash no tiene nada gastado todavia.")
        else:
            clips = an.get("clipsPagados") if isinstance(an.get("clipsPagados"), list) else []
            if an.get("legadoSinCalcular") or an.get("legadoUsd") is None:
                # el anuncio no ha pedido nada por el dash: lo que el libro tenga de antes se suma en su primer pedido (el estado no recorre el libro entero)
                previo = "lo de antes de la puerta todavia no esta sumado: el dash lo suma en el primer pedido de este anuncio"
            else:
                previo = f"incluye US$ {_num(an.get('legadoUsd')):.3f} de antes de la puerta"
            print(f"Anuncio {a.anuncio} de {a.producto}: gastado US$ {_num(an.get('gastadoUsd')):.3f} ({previo}) · "
                  f"{len(clips)} clip(s) pagado(s) · {an.get('repetidos') or 0} repetido(s)")
    print("El saldo de la cuenta de APIMart solo lo ve el dueño (en el dash): el editor ya no tiene la llave.")


def cmd_subir(a) -> None:
    ruta = Path(a.archivo)
    if not ruta.is_file():
        salir(f"No existe {ruta}")
    if not modo_directo():
        exigir_producto_y_anuncio(a)
        if not config_dash():
            salir(sin_conexion(gasto=False))
    print(subir(ruta, a.producto, a.anuncio))


def cmd_imagen(a) -> None:
    validar_tope(a)
    modelo = comprobar_modelo(a, "imagen")
    prompt = leer_texto(a.prompt, a.prompt_archivo, "prompt")
    refs: list[str | Path] = []
    for r in a.ref or []:
        if r.startswith(("https://", "http://")):
            refs.append(r)
        else:
            p = Path(r)
            if not p.is_file():
                salir(f"No existe la imagen de referencia: {r}")
            refs.append(p)
    if len(refs) > 15:
        salir("gpt-image-2 acepta hasta 15 imagenes de referencia.")
    revisar_o_salir(RP.revisar_imagen(prompt, [str(r) for r in refs], carpeta_del_producto(a.producto)), "el prompt de la imagen")
    salida = Path(a.salida)
    if modo_directo():
        return imagen_directo(a, modelo, prompt, refs, salida)
    exigir_formato_dash(a, "imagen")
    if any(isinstance(r, str) and not r.startswith("https://") for r in refs):
        salir("No genero nada: por el dash las referencias por direccion van en https (o pasa el archivo y se sube solo).")

    cuerpo = {**cuerpo_comun(a, "imagen"), "prompt": prompt, "tamano": a.tamano, "resolucion": a.resolucion}
    if a.dry_run:
        demo = [r if isinstance(r, str) else (url_vigente(r) or f"(se subiria {r.name} por el dash)") for r in refs]
        return mostrar_dry_run_dash("imagen", {**cuerpo, "imagenes": demo}, PRECIO_IMAGEN_USD, [])
    preparar_pedido_dash(a)
    cuerpo["imagenes"] = [r if isinstance(r, str) else subir(r, a.producto, a.anuncio) for r in refs]
    task_id, ticket, estimado = pedir_generacion(cuerpo, "imagen", salida)
    print(f"Imagen pedida por el dash: {task_id} (estimado US$ {estimado or PRECIO_IMAGEN_USD:.4f})", flush=True)
    d = esperar_dash(task_id, ticket, cada=4, maximo=600)
    url = (buscar_urls(d.get("urls")) or [None])[0]
    if not url:
        salir(f"El trabajo {task_id} termino pero no trajo la imagen: {json.dumps(d, ensure_ascii=False)[:300]}")
    costo, es_estimado = costo_final(d, estimado)
    fila = {"tipo": "imagen", "modelo": MODELOS["imagen"], "task_id": task_id, "costo": costo, **({"costo_es_estimado": True} if es_estimado else {}), "archivo": str(salida),
            **contexto(a), "via": "dash"}
    descargar(url, salida, fila_si_falla=fila)
    guardar_url(salida, url, time.time() + 23 * 3600)
    with (salida.parent / "prompts.md").open("a", encoding="utf-8") as f:
        f.write(f"## {salida.stem}\n\n{prompt}\n\nreferencias: {', '.join(a.ref or []) or '(ninguna)'}\n\n")
    anotar(fila)
    print(f"LISTO: {salida}  | costo {costo} | tarea {task_id}")


def imagen_directo(a, modelo: str, prompt: str, refs: list, salida: Path) -> None:
    """La imagen con el APIMart FALSO de las autopruebas (el camino de antes)."""
    payload = {"model": modelo, "prompt": prompt, "n": 1, "size": a.tamano, "resolution": a.resolucion}
    if refs:
        payload["image_urls"] = [r if isinstance(r, str) else a_data_uri(r) for r in refs]
    if a.dry_run:
        return mostrar_dry_run("imagen", "/images/generations", payload)
    llave = cargar_llave()
    r = pedir("POST", "/images/generations", llave, json=payload)
    task_id = r.json()["data"][0]["task_id"]
    print(f"Imagen enviada: {task_id}", flush=True)
    d = esperar_directo(llave, task_id, cada=4, maximo=600)
    imagenes = d.get("result", {}).get("images", [])
    url = (buscar_urls(imagenes) or [None])[0]
    if not url:
        salir(f"El trabajo {task_id} termino pero no trajo la imagen: {json.dumps(d)[:300]}")
    descargar(url, salida)
    expira = (imagenes[0].get("expires_at") if imagenes and isinstance(imagenes[0], dict) else None) or time.time() + 23 * 3600
    guardar_url(salida, url, float(expira))
    with (salida.parent / "prompts.md").open("a", encoding="utf-8") as f:
        f.write(f"## {salida.stem}\n\n{prompt}\n\nreferencias: {', '.join(a.ref or []) or '(ninguna)'}\n\n")
    anotar({"tipo": "imagen", "modelo": modelo, "task_id": task_id, "costo": d.get("cost"), "creditos": d.get("credits_cost"), "archivo": str(salida), **contexto(a)})
    print(f"LISTO: {salida}  | costo {d.get('cost')} | tarea {task_id}")


def cmd_video(a) -> None:
    validar_tope(a)
    modelo = comprobar_modelo(a, "video")
    prompt = leer_texto(a.prompt, a.prompt_archivo, "prompt")
    if not a.imagen:
        salir("Falta --imagen: el video se genera a partir de un cuadro (imagen a video).")
    if not 6 <= a.duracion <= 15:
        salir("grok-imagine admite duraciones de 6 a 15 segundos.")
    es_url = a.imagen.startswith(("https://", "http://"))
    ruta_img = None if es_url else Path(a.imagen)
    if ruta_img is not None and not ruta_img.is_file():
        salir(f"No existe la imagen: {a.imagen}")
    salida = Path(a.salida)
    # VOICE y ACCENT identicos a los de los otros clips ya generados en la misma carpeta (apimart.py deja <clip>_prompt.txt junto a cada uno); el dash compara con todo el anuncio
    revisar_o_salir(RP.revisar_video(prompt, a.duracion, RP.prompts_hermanos(salida.parent, salida.parent / f"{salida.stem}_prompt.txt")), "el prompt del clip")
    if modo_directo():
        return video_directo(a, modelo, prompt, es_url, ruta_img, salida)
    exigir_formato_dash(a, "video")
    if es_url and not a.imagen.startswith("https://"):
        salir("No genero nada: por el dash el cuadro inicial por direccion va en https (o pasa el archivo y se sube solo).")

    estimado = a.duracion * PRECIO_VIDEO_SEG_USD.get(str(a.resolucion).lower(), max(PRECIO_VIDEO_SEG_USD.values()))
    cuerpo = {**cuerpo_comun(a, "video"), "prompt": prompt, "duracion": int(a.duracion), "resolucion": a.resolucion, "tamano": a.tamano}
    if (a.repetir or "").strip():
        cuerpo["repetir"] = a.repetir.strip()[:200]
    if a.dry_run:
        demo = a.imagen if es_url else (url_vigente(ruta_img) or f"(se subiria {ruta_img.name} por el dash)")
        return mostrar_dry_run_dash("video", {**cuerpo, "imagen": demo}, estimado, [])
    preparar_pedido_dash(a)
    cuerpo["imagen"] = a.imagen if es_url else subir(ruta_img, a.producto, a.anuncio)
    task_id, ticket, est_dash = pedir_generacion(cuerpo, "video", salida)
    print(f"Video pedido por el dash: {task_id} (tarda varios minutos; estimado US$ {est_dash or estimado:.4f})", flush=True)
    d = esperar_dash(task_id, ticket, cada=10, maximo=1500)
    url = (buscar_urls(d.get("urls")) or [None])[0]
    if not url:
        salir(f"El trabajo {task_id} termino pero no trajo el video: {json.dumps(d, ensure_ascii=False)[:300]}")
    costo, es_estimado = costo_final(d, est_dash)
    fila = {"tipo": "video", "modelo": MODELOS["video"], "task_id": task_id, "costo": costo, **({"costo_es_estimado": True} if es_estimado else {}), "archivo": str(salida),
            **contexto(a), "via": "dash", **({"repetido": a.repetir.strip()} if (a.repetir or "").strip() else {})}
    descargar(url, salida, fila_si_falla=fila)
    parametros = {k: v for k, v in cuerpo.items() if k not in ("prompt", "accion")}
    (salida.parent / f"{salida.stem}_prompt.txt").write_text(f"{prompt}\n\n# parametros: {json.dumps(parametros, ensure_ascii=False)}\n", encoding="utf-8")
    anotar(fila)
    print(f"LISTO: {salida}  | costo {costo} | tarea {task_id}")


def video_directo(a, modelo: str, prompt: str, es_url: bool, ruta_img: Path | None, salida: Path) -> None:
    """El clip con el APIMart FALSO de las autopruebas (el camino de antes)."""
    if a.dry_run:
        url_demo = a.imagen if es_url else (url_vigente(ruta_img) or f"(se subiria {ruta_img.name} a APIMart y se usaria su URL publica)")
        payload = {"model": modelo, "prompt": prompt, "size": a.tamano, "duration": int(a.duracion), "resolution": a.resolucion, "image_urls": [url_demo]}
        return mostrar_dry_run("video", "/videos/generations", payload)
    llave = cargar_llave()
    url_imagen = a.imagen if es_url else subir(ruta_img, llave=llave)
    payload = {"model": modelo, "prompt": prompt, "size": a.tamano, "duration": int(a.duracion), "resolution": a.resolucion, "image_urls": [url_imagen]}
    r = pedir("POST", "/videos/generations", llave, json=payload)
    task_id = r.json()["data"][0]["task_id"]
    print(f"Video enviado: {task_id} (tarda varios minutos)", flush=True)
    d = esperar_directo(llave, task_id, cada=10, maximo=1500)
    url = (buscar_urls(d.get("result", {}).get("videos", [])) or buscar_urls(d.get("result", {})) or [None])[0]
    if not url:
        salir(f"El trabajo {task_id} termino pero no trajo el video: {json.dumps(d)[:300]}")
    descargar(url, salida)
    (salida.parent / f"{salida.stem}_prompt.txt").write_text(f"{prompt}\n\n# parametros: {json.dumps({k: v for k, v in payload.items() if k != 'prompt'})}\n", encoding="utf-8")
    anotar({"tipo": "video", "modelo": modelo, "task_id": task_id, "costo": d.get("cost"), "creditos": d.get("credits_cost"), "archivo": str(salida), **contexto(a),
            **({"repetido": a.repetir.strip()} if (a.repetir or "").strip() else {})})
    print(f"LISTO: {salida}  | costo {d.get('cost')} | tarea {task_id}")


def cmd_voz(a) -> None:
    """La voz de APIMart (gpt-4o-mini-tts) era solo para pruebas y necesitaba la llave: ya no existe en uso real. Queda solo con el APIMart falso de las autopruebas."""
    if not modo_directo():
        salir("apimart.py voz ya no funciona: el editor ya no tiene la llave de APIMart (todo pasa por el dash) y el dash no da la voz de APIMart. La voz de los anuncios sale de "
              "`python scripts/voz_auto.py <producto> <AD>` (la de los flujos del dueño, Fish Audio, por el dash). No se gasto nada.")
    modelo = comprobar_modelo(a, "voz")
    texto = leer_texto(a.texto, a.texto_archivo, "texto")
    if len(texto) > 4096:
        salir(f"El texto tiene {len(texto)} caracteres y el limite es 4096. Divide el guion en dos partes.")
    payload = {"model": modelo, "input": texto, "voice": a.voz, "response_format": "wav", "speed": a.velocidad}
    if a.instrucciones.strip():
        payload["instructions"] = a.instrucciones.strip()[:600]
    if a.dry_run:
        return mostrar_dry_run("voz", "/audio/speech", payload)
    llave = cargar_llave()
    salida = Path(a.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    r = pedir("POST", "/audio/speech", llave, json=payload, timeout=300)
    wav = salida.with_suffix(".tmp.wav")
    wav.write_bytes(r.content)
    if salida.suffix.lower() == ".wav":
        wav.replace(salida)
    else:
        c = correr([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libmp3lame", "-q:a", "2", str(salida)])
        wav.unlink(missing_ok=True)
        if c.returncode != 0:
            salir(f"ffmpeg no pudo convertir la voz: {c.stderr.strip()[:300]}")
    anotar({"tipo": "voz", "modelo": modelo, "task_id": None, "costo": None, "caracteres": len(texto), "voz": a.voz, "con_instrucciones": bool(a.instrucciones.strip()),
            "archivo": str(salida), **contexto(a)})
    print(f"LISTO: {salida} ({len(texto)} caracteres; APIMart no informa el costo de la voz)")


def cmd_estado(a) -> None:
    if modo_directo():
        d = pedir("GET", f"/tasks/{a.task_id}", cargar_llave(), reintentos=2, params={"language": "es"}).json().get("data", {})
        print(json.dumps({k: d.get(k) for k in ("id", "status", "progress", "cost", "credits_cost", "estimated_time", "actual_time", "error")}, ensure_ascii=False))
        for u in buscar_urls(d.get("result", {})):
            print(f"resultado: {u}")
        return
    t = leer_tickets().get(a.task_id)
    if not isinstance(t, dict) or not t.get("ticket"):
        salir(f"No tengo el ticket de la tarea {a.task_id} en este equipo: solo se consulta desde el equipo que la pidio (su ticket queda en anuncios/_apimart/tickets.json).")
    if not config_dash():
        salir(sin_conexion(gasto=False))
    d = pedir_dash("POST", cuerpo={"accion": "tarea", "ticket": t["ticket"]}, tiempo=60)
    print(json.dumps({"id": a.task_id, "estado": d.get("estado"), "avance": d.get("avance"), "costoUsd": d.get("costoUsd"), "error": d.get("error"),
                      "producto": t.get("producto"), "anuncio": t.get("anuncio"), "archivo": t.get("archivo")}, ensure_ascii=False))
    for u in buscar_urls(d.get("urls")):
        print(f"resultado: {u}")


def cmd_costos(a) -> None:
    if not COSTOS.is_file():
        print("Todavia no hay costos anotados (costos.jsonl no existe).")
        return
    filas = []
    for linea in COSTOS.read_text(encoding="utf-8").splitlines():
        try:
            if linea.strip():
                filas.append(json.loads(linea))
        except ValueError:
            continue
    if a.anuncio:
        filas = [f for f in filas if f.get("anuncio") == a.anuncio]
    grupos: dict[tuple, dict] = {}
    for f in filas:
        g = grupos.setdefault((f.get("producto") or "-", f.get("anuncio") or "-"), {"imagen": 0, "video": 0, "voz": 0, "texto": 0, "usd": 0.0, "texto_usd": 0.0, "sin_costo": 0})
        tipo = str(f.get("tipo") or "?")
        g[tipo] = g.get(tipo, 0) + 1
        if tipo == "texto":      # llamadas de texto/vision: se estiman por tokens y NO entran en el total (el dash anota su propio estimado)
            g["texto_usd"] += float(f.get("costo_estimado") or 0)
        elif f.get("costo") is None:
            g["sin_costo"] += 1
        else:
            g["usd"] += float(f["costo"])
    total = 0.0
    for (producto, anuncio), g in sorted(grupos.items()):
        total += g["usd"]
        nota = f" (+{g['sin_costo']} sin costo informado)" if g["sin_costo"] else ""
        texto = f", {g['texto']} textos (≈ {g['texto_usd']:.4f} estimado, aparte)" if g["texto"] else ""
        print(f"{producto} / {anuncio}: {g['imagen']} imagenes, {g['video']} clips, {g['voz']} voces{texto} -> {g['usd']:.3f}{nota}")
    print(f"TOTAL: {total:.3f}")


def cmd_enviar_costos(a) -> None:
    if not COSTOS.is_file():
        print("Todavia no hay costos anotados (costos.jsonl no existe).")
        return
    filas = []
    for linea in COSTOS.read_text(encoding="utf-8").splitlines():
        try:
            if linea.strip():
                filas.append(json.loads(linea))
        except ValueError:
            continue
    cfg = config_dash()
    if not cfg:
        salir(sin_conexion(gasto=False) + " Mientras tanto el dueño puede importar costos.jsonl a mano en la pagina «Costo por proyecto».")
    por_dash = sum(1 for f in filas if isinstance(f, dict) and f.get("via") == "dash")
    con_costo = para_editor_costos(filas)
    nota_dash = f"; {por_dash} los hizo el dash y ya estan alla" if por_dash else ""
    if a.dry_run:
        print(f"[DRY-RUN] se mandarian {len(con_costo)} costos (de {len(filas)} renglones{nota_dash}; la voz no trae costo) a {cfg[0]}. No se envio nada.")
        return
    r = enviar_al_dash(filas)
    if not r["ok"]:
        salir(f"No se pudieron mandar los costos: {r['mensaje']}.")
    print(f"Enviados {r['enviadas']} costos al dash: {r['nuevas']} nuevos, {r['repetidas']} que ya estaban" + (f", {r['rechazadas']} rechazados" if r["rechazadas"] else "")
          + (f" ({por_dash} hechos por el dash no se mandan: ya estan alla)" if por_dash else "") + ".")


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def comunes(p):
        p.add_argument("--anuncio")
        p.add_argument("--producto")
        p.add_argument("--etiqueta")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--modelo")
        p.add_argument("--cambio-de-modelo-aprobado", action="store_true")
        p.add_argument("--tope-anuncio", type=float, help="sube el tope de ESTE anuncio (normal US$ 0,50; nunca mas de US$ 1; lo decide el dash)")
        p.add_argument("--motivo-tope", help="por que se sube el tope del anuncio (el dash lo exige si pasa de US$ 0,50 y lo deja anotado)")
        p.add_argument("--repetir", help="motivo para repetir un clip ya pagado, solo si una persona lo pidio (uno por anuncio; lo cuenta el dash)")

    p = sub.add_parser("saldo")
    p.add_argument("--producto")
    p.add_argument("--anuncio")
    p.set_defaults(f=cmd_saldo)

    p = sub.add_parser("imagen")
    comunes(p)
    p.add_argument("--prompt")
    p.add_argument("--prompt-archivo")
    p.add_argument("--ref", action="append", help="imagen de referencia (archivo o URL); se puede repetir, hasta 15")
    p.add_argument("--tamano", default="9:16")
    p.add_argument("--resolucion", default="1k")
    p.add_argument("--salida", required=True)
    p.set_defaults(f=cmd_imagen)

    p = sub.add_parser("video")
    comunes(p)
    p.add_argument("--imagen", help="cuadro inicial: archivo local (se sube) o URL publica")
    p.add_argument("--prompt")
    p.add_argument("--prompt-archivo")
    p.add_argument("--tamano", default="9:16")
    p.add_argument("--duracion", type=int, default=8)
    p.add_argument("--resolucion", default="480p")
    p.add_argument("--salida", required=True)
    p.set_defaults(f=cmd_video)

    p = sub.add_parser("voz", help="ya no se usa (solo autopruebas): la voz sale de voz_auto.py")
    comunes(p)
    p.add_argument("--texto")
    p.add_argument("--texto-archivo")
    p.add_argument("--voz", default="nova")
    p.add_argument("--velocidad", type=float, default=1.0)
    p.add_argument("--instrucciones", default="")
    p.add_argument("--salida", required=True, help=".mp3 o .wav")
    p.set_defaults(f=cmd_voz)

    p = sub.add_parser("estado")
    p.add_argument("task_id")
    p.set_defaults(f=cmd_estado)

    p = sub.add_parser("subir")
    p.add_argument("archivo")
    p.add_argument("--producto")
    p.add_argument("--anuncio")
    p.set_defaults(f=cmd_subir)

    p = sub.add_parser("costos")
    p.add_argument("--anuncio")
    p.set_defaults(f=cmd_costos)

    p = sub.add_parser("enviar-costos")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(f=cmd_enviar_costos)

    a = ap.parse_args()
    try:
        a.f(a)
    except ErrorDash as e:
        salir(str(e))


if __name__ == "__main__":
    main()
