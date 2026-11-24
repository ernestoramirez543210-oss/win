"""Indice de NUESTROS anuncios originales en Drive y el cliente del dash para las variaciones hechas por el servidor (2026-11-13).

Pedido del dueño: «mira también la opción de empezar a editar desde ahí, que coja por ejemplo el original o lo ubique y edite sobre ese mismo la nueva variación o ángulo para que
siempre saquemos anuncios de calidad y lo más económico posible». Cada variación de /tablero › Variaciones dice de qué anuncio nuestro sale (`base`, p. ej. «101» de Sermones). Para
abrirlo (o editarlo en el servidor) hay que saber DÓNDE está ese video en Drive: Beto los guarda como «<oferta> <número> .mp4» en carpetas con fecha dentro de
MI NEGOCIO › OFERTAS LATAM › BRIMIREZZ («5000 sermones 7 oct/5000 sermones 101 .mp4», «12 agosto- KINESIOTAPING/KINESIOTAPING 105 .mp4»), y el corredor deja los suyos en
OFERTAS LATAM › <OFERTA> › ANUNCIOS.

Este modulo:
  - recorre OFERTAS LATAM (solo lectura, hasta 3 niveles por debajo de cada carpeta de primer nivel, sin entrar en B-rolls, REFERENCIA ni AVATAR) y anota cada video .mp4/.mov con su
    id, nombre, carpeta y carpeta de primer nivel (`raiz`). NO interpreta los nombres: eso lo hace el dash (functions/_originales.js), que es quien los muestra y los empareja con
    cada variacion; asi hay una sola regla.
  - le manda la lista al dash (`POST /api/variacion-editor {accion:'indice'}` con la llave del editor) junto con lo que el servidor tiene encendido (si hace variaciones, su tope, la voz).
  - es el cliente del dash para lo demas de las variaciones (pedidos pendientes, avances, resultado).
Nada de lo que viene de Drive o del dash se ejecuta: son datos (nombres acotados y limpiados; ids solo con letras, numeros, - y _).
"""
from __future__ import annotations

import json
import re
import time
import unicodedata

RE_ID_DRIVE = re.compile(r"^[A-Za-z0-9_-]{15,100}$")
RE_ID_VARIACION = re.compile(r"^[a-z0-9]{8,20}$")
MIME_CARPETA = "application/vnd.google-apps.folder"
EXT_VIDEO = (".mp4", ".mov", ".m4v")
SALTAR = re.compile(r"^(b-rolls|referencia(?: \d{1,2})?|avatar|entregables|_anteriores|\.)", re.I)   # carpetas que no son anuncios nuestros
PROFUNDIDAD_MAX = 3            # OFERTAS LATAM / BRIMIREZZ / OK / 1 julio / video.mp4 = 3 niveles bajo la carpeta de primer nivel
MAX_CARPETAS = 900             # una vuelta entera no deberia pasar de ~300 (BRIMIREZZ tiene ~45 carpetas con fecha y OK ~120)
MAX_ARCHIVOS = 6000            # lo mismo que acepta el dash
ETAPAS_VARIACION = ("original", "analisis", "gancho", "render", "unir", "revisar", "entrega")


def limpio(texto, n: int) -> str:
    """Texto de Drive o del dash para guardarlo o mandarlo: sin caracteres de control, en una linea, acotado."""
    t = unicodedata.normalize("NFC", str(texto if texto is not None else ""))
    t = re.sub(r"[\x00-\x1f\x7f]", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:n]


CAJAS_MP4 = (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"pnot")


def es_mp4_de_verdad(ruta) -> bool:
    """Los bytes 4-8 de un MP4/MOV son el nombre de su primera caja (ftyp, moov…). Una lista HLS, un HTML o cualquier otra cosa con nombre de .mp4 no pasa
    (el tipo de Drive lo pone quien sube el archivo: no basta; revisor-seguridad 2026-11-13)."""
    try:
        with open(ruta, "rb") as f:
            cabeza = f.read(12)
    except OSError:
        return False
    return len(cabeza) >= 8 and cabeza[4:8] in CAJAS_MP4


def es_video(nombre: str) -> bool:
    return str(nombre or "").lower().endswith(EXT_VIDEO)


# ------------------------------------------------------------------ recorrer OFERTAS LATAM (solo lectura)
def recorrer(drive, raiz_id: str, registrar=print, reloj=time.monotonic, plazo_s: float = 900.0) -> dict:
    """{generadoEn, raices:[{nombre,id}], archivos:[{id,nombre,carpeta,raiz,mb,fecha}], carpetas, avisos}. `drive` es un drive_api.Drive (o uno de mentira).
    Solo usa listar_hijos (lectura). Para si se pasa de MAX_CARPETAS o del plazo (lo dice en avisos y entrega lo que alcanzo a ver)."""
    if not RE_ID_DRIVE.match(raiz_id or ""):
        raise ValueError("id de OFERTAS LATAM no valido")
    t0 = reloj()
    archivos: list[dict] = []
    raices: list[dict] = []
    avisos: list[str] = []
    visitadas = 0
    cola: list[tuple[str, str, str, int]] = []       # (id de carpeta, nombre de la carpeta, raiz, profundidad)
    for f in drive.listar_hijos(raiz_id):
        if f.get("mimeType") == MIME_CARPETA and RE_ID_DRIVE.match(str(f.get("id") or "")):
            n = limpio(f.get("name"), 120)
            if n and not SALTAR.match(n):
                raices.append({"nombre": n, "id": f["id"]})
                cola.append((f["id"], n, n, 0))
    visitadas += 1
    vistos: set[str] = set()
    while cola:
        if visitadas >= MAX_CARPETAS:
            avisos.append(f"paré en {MAX_CARPETAS} carpetas: el índice puede estar incompleto")
            break
        if reloj() - t0 > plazo_s:
            avisos.append(f"se acabó el tiempo ({plazo_s / 60:.0f} min): el índice puede estar incompleto")
            break
        carpeta_id, carpeta_nombre, raiz, prof = cola.pop(0)
        if carpeta_id in vistos:
            continue
        vistos.add(carpeta_id)
        visitadas += 1
        try:
            hijos = drive.listar_hijos(carpeta_id)
        except Exception as e:  # noqa: BLE001 - una carpeta que no se deja leer no tumba el indice entero (el permiso caido si: lo vuelve a lanzar quien llama)
            if e.__class__.__name__ == "ErrorPermiso":
                raise
            avisos.append(f"no pude leer «{carpeta_nombre[:60]}» ({e.__class__.__name__})")
            continue
        for f in hijos:
            fid = str(f.get("id") or "")
            if not RE_ID_DRIVE.match(fid):
                continue
            nombre = limpio(f.get("name"), 120)
            if not nombre:
                continue
            if f.get("mimeType") == MIME_CARPETA:
                if prof + 1 <= PROFUNDIDAD_MAX and not SALTAR.match(nombre):
                    cola.append((fid, nombre, raiz, prof + 1))
                continue
            if not es_video(nombre) or f.get("trashed") or (f.get("mimeType") and not str(f["mimeType"]).startswith("video/")):   # un .mp4 que Drive no tiene por video (p. ej. una lista HLS) no entra
                continue
            try:
                mb = round(int(f.get("size") or 0) / 1048576, 1)
            except (TypeError, ValueError):
                mb = 0.0
            fecha = str(f.get("createdTime") or f.get("modifiedTime") or "")[:10]
            archivos.append({"id": fid, "nombre": nombre, "carpeta": carpeta_nombre, "raiz": raiz, "mb": mb, "fecha": fecha if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha) else ""})
            if len(archivos) >= MAX_ARCHIVOS:
                avisos.append(f"paré en {MAX_ARCHIVOS} videos: el índice puede estar incompleto")
                cola = []
                break
    registrar(f"  índice de originales: {len(archivos)} video(s) en {visitadas} carpeta(s) de {len(raices)} carpeta(s) de primer nivel ({reloj() - t0:.0f} s)"
              + (f"; avisos: {'; '.join(avisos)}" if avisos else ""))
    return {"generadoEn": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "raices": raices, "archivos": archivos, "carpetas": visitadas, "avisos": avisos}


def ancestros(drive, archivo_id: str, raiz_id: str, maximo: int = 6) -> list[dict] | None:
    """La cadena de carpetas desde el archivo hasta OFERTAS LATAM ([carpeta del archivo, ..., carpeta de primer nivel]), o None si el archivo NO esta dentro de OFERTAS LATAM.
    Sirve para no bajar (ni editar) un archivo de Drive que no sea uno de nuestros anuncios aunque el dash mande su id, y para saber la carpeta de primer nivel donde dejar la variacion."""
    if not RE_ID_DRIVE.match(archivo_id or "") or not RE_ID_DRIVE.match(raiz_id or ""):
        return None
    cadena: list[dict] = []
    actual = drive.datos_de(archivo_id)
    for _ in range(maximo):
        padres = [p for p in (actual.get("parents") or []) if isinstance(p, str)]
        if not padres:
            return None
        padre = padres[0]
        if padre == raiz_id:
            return cadena
        datos = drive.datos_de(padre)
        if datos.get("trashed"):
            return None
        cadena.append({"id": padre, "nombre": limpio(datos.get("name"), 120)})
        actual = datos
    return None


# ------------------------------------------------------------------ el dash (/api/variacion-editor, con la llave del editor)
class ErrorDashVar(Exception):
    def __init__(self, mensaje: str, config: bool = False, codigo: int = 0):
        super().__init__(mensaje)
        self.config = config
        self.codigo = codigo


class DashVariaciones:
    """Las llamadas del servidor a /api/variacion-editor. `llamar(metodo, ruta, cuerpo)` se cambia en las pruebas; por defecto usa DASH_URL y DASH_CLAVE_EDITOR del entorno,
    sin seguir redirecciones (la llave no sale de la direccion del dash) y sin imprimir nunca la llave."""

    RUTA = "/api/variacion-editor"

    def __init__(self, llamar=None, url: str = "", llave: str = ""):
        self._llamar = llamar
        self.url, self.llave = url, llave

    def _pedir(self, metodo: str, sufijo: str = "", cuerpo: dict | None = None, tiempo: int = 60) -> dict:
        if self._llamar is not None:
            return self._llamar(metodo, sufijo, cuerpo)
        import os

        import requests

        url = (self.url or os.environ.get("DASH_URL", "")).rstrip("/")
        llave = self.llave or os.environ.get("DASH_CLAVE_EDITOR", "")
        if not url or not llave:
            raise ErrorDashVar("faltan DASH_URL y DASH_CLAVE_EDITOR", config=True)
        try:
            r = requests.request(metodo, url + self.RUTA + sufijo, json=cuerpo, headers={"x-clave-editor": llave}, timeout=tiempo, allow_redirects=False)
        except requests.RequestException as e:
            raise ErrorDashVar(f"sin conexion con el dash ({e.__class__.__name__})") from None
        if 300 <= r.status_code < 400:
            raise ErrorDashVar("el dash contesto con una redireccion; no la sigo", config=True)
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code == 401:
            raise ErrorDashVar("El dash rechazo la llave del editor", config=True, codigo=401)
        if r.status_code == 503:
            raise ErrorDashVar("Al dash todavia le falta la llave del editor", config=True, codigo=503)
        if r.status_code == 404 and not j:
            raise ErrorDashVar("el dash todavia no tiene /api/variacion-editor (falta desplegarlo)", config=True, codigo=404)
        if r.status_code >= 400 or not isinstance(j, dict) or not j.get("ok"):
            raise ErrorDashVar(f"el dash contesto {r.status_code}: {limpio((j or {}).get('error') if isinstance(j, dict) else '', 160) or 'sin detalle'}", codigo=r.status_code)
        return j

    # -- lo que pide el servidor
    def pendientes(self) -> list[dict]:
        j = self._pedir("GET", "?pendientes=1")
        return [p for p in (pedido_limpio(x) for x in (j.get("pedidos") or [])) if p]

    def uno(self, var_id: str) -> tuple[dict | None, dict]:
        if not RE_ID_VARIACION.match(var_id or ""):
            raise ErrorDashVar("id de variacion no valido")
        j = self._pedir("GET", f"?id={var_id}")
        return pedido_limpio(j.get("pedido")), (j.get("edicion") if isinstance(j.get("edicion"), dict) else {})

    def avance(self, var_id: str, estado: str, **campos) -> dict:
        cuerpo = {"accion": "avance", "id": var_id, "estado": estado}
        for k, v in campos.items():
            if v is not None and v != "":
                cuerpo[k] = v
        if "nota" in cuerpo:
            cuerpo["nota"] = limpio(cuerpo["nota"], 290)
        return self._pedir("POST", "", cuerpo)

    def subir_indice(self, indice: dict, servidor: dict) -> dict:
        cuerpo = {"accion": "indice", "generadoEn": indice["generadoEn"], "raices": indice["raices"][:200], "archivos": indice["archivos"][:MAX_ARCHIVOS], "servidor": servidor}
        return self._pedir("POST", "", cuerpo, tiempo=120)


RE_NUMERO_VAR = re.compile(r"^(1\d{2})((?:\.\d{1,2}){1,2})$")
RE_ARCHIVO_SALIDA = re.compile(r"^[^\x00-\x1f/\\:*?\"<>|]{1,110}\.mp4$")
RE_SLUG = re.compile(r"[^a-z0-9]+")


def slug_de(oferta: str) -> str:
    """El nombre corto de la oferta para el costo por proyecto (como idDe de /api/revivir): «Sermones Biblicos» -> «sermones-biblicos»."""
    t = unicodedata.normalize("NFKD", str(oferta or "")).encode("ascii", "ignore").decode("ascii").lower()
    return RE_SLUG.sub("-", t).strip("-")[:40].strip("-") or "variaciones"


def pedido_limpio(p) -> dict | None:
    """Un pedido del dash, validado. Lo que no sirve devuelve None (y no se toca)."""
    if not isinstance(p, dict):
        return None
    vid = str(p.get("id") or "")
    o = p.get("original") if isinstance(p.get("original"), dict) else {}
    numero = str(p.get("numero") or "")
    archivo = limpio(p.get("archivo"), 115)
    if not RE_ID_VARIACION.match(vid) or not RE_ID_DRIVE.match(str(o.get("id") or "")) or not RE_NUMERO_VAR.match(numero):
        return None
    if not RE_ARCHIVO_SALIDA.match(archivo) or archivo.startswith(".") or ".." in archivo:
        return None
    return {
        "id": vid, "oferta": limpio(p.get("oferta"), 80), "nombre": limpio(p.get("nombre"), 100), "numero": numero, "base": limpio(p.get("base"), 40),
        "gancho": limpio(p.get("gancho"), 200), "nota": limpio(p.get("nota"), 200), "cambio": limpio(p.get("cambio"), 20), "referencia": limpio(p.get("referencia"), 300),
        "original": {"id": str(o["id"]), "nombre": limpio(o.get("nombre"), 120), "carpeta": limpio(o.get("carpeta"), 120), "raiz": limpio(o.get("raiz"), 120)},
        "archivo": archivo,
        # cuantas veces la pidio el dueño: cada aviso lleva este numero y el dash rechaza (409) el de un intento anterior
        "intento": intento_de(p.get("intento")),
    }


def intento_de(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if 0 <= n <= 9999 else 0


def id_de_costos(numero: str) -> str:
    """El anuncio para el costo por proyecto del dash (letras, numeros, - y _): «101.7» -> «101-7»."""
    return numero.replace(".", "-")[:16]


if __name__ == "__main__":
    print(json.dumps({"uso": "modulo: lo usa corredor_vps.py (indice y pedidos) y variacion_auto.py"}, ensure_ascii=False))
