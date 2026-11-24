"""Cliente minimo de Google Drive (API v3) con solo `requests`, para que el VPS deje los b-roll en la carpeta de cada oferta
(subcarpeta B-rolls) sin necesitar Drive para escritorio. Sin librerias de Google, como sheets.py del radar.

Que SI hace (y nada mas):
  - leer una carpeta que ya existe (su nombre, si se puede escribir en ella) y listar lo que tiene dentro
  - hallar o crear UNA subcarpeta (B-rolls) dentro de la carpeta de la oferta
  - subir un video por subida reanudable (por trozos de 8 MiB; si se corta la conexion sigue donde iba) y comprobar que Drive lo guardo
    con el mismo tamano y el mismo MD5
  - leer y guardar archivos de texto pequenos en la subcarpeta (fuentes.csv, LEEME.txt)
Que NO hace, a proposito: NO existe ninguna funcion para borrar, mover ni compartir, y toda escritura pasa por una "cerca": solo se escribe
en las carpetas que el programa declaro con permitir(). El permiso de Google que se usa es el de Drive completo (los b-roll van a carpetas que
ya existen, y con drive.file solo se ve lo que crea la propia aplicacion), asi que esta cerca es la que limita lo que este codigo puede hacer.

Las credenciales viven en secretos/google.json (client_id, client_secret, refresh_token; las pone Setup-DriveBroll.ps1 con permisos 640 root:broll).
Este modulo las lee, nunca las imprime ni las escribe, y el token de acceso vive solo en memoria.

Uso desde la linea de comandos (en el VPS, como el usuario broll):
  python scripts/drive_api.py probar [carpeta_o_enlace ...]     renueva el permiso y mira las carpetas de ofertas.json (o las que pases)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import RAIZ, configurar_consola  # noqa: E402

URL_API = "https://www.googleapis.com/drive/v3"
URL_SUBIDA = "https://www.googleapis.com/upload/drive/v3"
URL_TOKEN = "https://oauth2.googleapis.com/token"
# Solo para scripts/autoprueba_drive.py se acepta otra direccion, y unicamente si es de esta misma maquina: asi las credenciales
# nunca pueden ir a otro lado por una variable de entorno.
_BASE_PRUEBA = os.environ.get("BROLL_DRIVE_URL", "")
if _BASE_PRUEBA.startswith(("http://127.0.0.1:", "http://localhost:")):
    _b = _BASE_PRUEBA.rstrip("/")
    URL_API, URL_SUBIDA, URL_TOKEN = _b + "/drive/v3", _b + "/upload/drive/v3", _b + "/token"

ALCANCE = "https://www.googleapis.com/auth/drive"
RUTA_CREDENCIALES = RAIZ / "secretos" / "google.json"
MIME_CARPETA = "application/vnd.google-apps.folder"
CARPETA_BROLLS = "B-rolls"
TROZO = 8 * 1024 * 1024            # multiplo de 256 KiB, como exige Google para los trozos que no son el ultimo
ESPERAS = (2, 5, 12, 30, 60)       # segundos entre reintentos de una llamada
RE_ID = re.compile(r"^[A-Za-z0-9_-]{15,100}$")
CAMPOS_ARCHIVO = "id,name,mimeType,size,md5Checksum,modifiedTime,createdTime,parents,trashed"
CAMPOS_DATOS = "id,name,mimeType,size,md5Checksum,parents,trashed"      # los de datos_de (mimeType: las variaciones exigen video/*, 2026-11-13)
ENLACE_ACTIVAR_API = "https://console.cloud.google.com/apis/library/drive.googleapis.com"


class ErrorDrive(Exception):
    """Algo fallo con Drive. El mensaje esta escrito para una persona: dice que paso y que hacer."""


class ErrorPermiso(ErrorDrive):
    """Google no deja seguir (permiso revocado, API apagada, sin espacio): reintentar no sirve, hay que arreglarlo."""


class _SesionPerdida(Exception):
    """La sesion de subida ya no existe en Google (404/410): hay que abrir otra y empezar el archivo de nuevo."""


# ------------------------------------------------------------------ enlaces e ids
def id_de_carpeta(texto: str) -> str | None:
    """El id de una carpeta de Drive a partir de su enlace (o del id suelto). None si no se reconoce."""
    texto = (texto or "").strip()
    if not texto:
        return None
    if RE_ID.match(texto):
        return texto
    try:
        u = urllib.parse.urlparse(texto)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in ("drive.google.com", "docs.google.com"):
        return None
    m = re.search(r"/folders/([A-Za-z0-9_-]{15,100})", u.path)
    if m:
        return m.group(1)
    q = urllib.parse.parse_qs(u.query).get("id")
    if q and RE_ID.match(q[0]):
        return q[0]
    return None


def _escapar_q(texto: str) -> str:
    """Un texto dentro de comillas simples de una consulta de Drive."""
    return texto.replace("\\", "\\\\").replace("'", "\\'")


def _md5(ruta: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


# ------------------------------------------------------------------ credenciales
def cargar_credenciales(ruta: Path | str | None = None) -> dict:
    """secretos/google.json -> {'client_id','client_secret','refresh_token'}. Nunca imprime su contenido."""
    ruta = Path(ruta) if ruta else RUTA_CREDENCIALES
    if not ruta.is_file():
        raise ErrorPermiso(
            f"Falta el permiso de Drive ({ruta}). El dueno lo da una sola vez corriendo scripts\\Setup-DriveBroll.ps1 en su PC "
            "(abre Google, acepta, y deja el permiso en el servidor sin mostrarlo).")
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ErrorPermiso(f"{ruta} no se puede leer como JSON: vuelve a correr Setup-DriveBroll.ps1.") from None
    faltan = [k for k in ("client_id", "client_secret", "refresh_token") if not isinstance(datos.get(k), str) or not datos[k].strip()
              or any(c.isspace() for c in datos[k])]
    if faltan:
        raise ErrorPermiso(f"{ruta} no trae {', '.join(faltan)} completo(s): vuelve a correr Setup-DriveBroll.ps1.")
    return {k: datos[k] for k in ("client_id", "client_secret", "refresh_token")} | {"scope": str(datos.get("scope") or "")}


def _detalle(r) -> dict:
    """De una respuesta de error de Google: estado, razon (si dice cual) y mensaje."""
    razon, mensaje = "", ""
    try:
        j = r.json()
    except ValueError:
        return {"estado": r.status_code, "razon": "", "mensaje": (r.text or "").strip()[:200]}
    e = j.get("error") if isinstance(j, dict) else None
    if isinstance(e, dict):
        razones = [str(x.get("reason", "")) for x in e.get("errors", []) if isinstance(x, dict)]
        razones += [str(d.get("reason", "")) for d in (e.get("details") or []) if isinstance(d, dict)]
        razon = " ".join(x for x in razones if x)
        mensaje = str(e.get("message") or "")[:300]
        if not razon:
            razon = str(e.get("status") or "")
    elif isinstance(e, str):
        razon = e
        mensaje = str(j.get("error_description") or "")[:300]
    return {"estado": r.status_code, "razon": razon, "mensaje": mensaje}


class Drive:
    """Una conexion a Drive con el permiso del dueno. Los reintentos esperan segun ESPERAS; en las pruebas `dormir` es una funcion vacia."""

    def __init__(self, cred: dict, sesion=None, dormir=None, registrar=print):
        import requests

        self.cred = cred
        self.http = sesion or requests.Session()
        self.dormir = dormir or time.sleep
        self.registrar = registrar
        self._token: str | None = None
        self._vence = 0.0
        self.cercado: set[str] = set()
        self.llamadas = 0
        self.bytes_subidos = 0

    # ---- permiso de acceso (se renueva con el refresh_token; vive solo en memoria)
    def _acceso(self, forzar: bool = False) -> str:
        import requests

        if self._token and not forzar and time.time() < self._vence - 60:
            return self._token
        ultimo = ""
        for vez in range(4):
            try:
                r = self.http.post(URL_TOKEN, data={
                    "client_id": self.cred["client_id"], "client_secret": self.cred["client_secret"],
                    "refresh_token": self.cred["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
            except requests.RequestException as e:
                ultimo = f"sin conexion con Google ({e.__class__.__name__})"
                self.dormir(ESPERAS[min(vez, len(ESPERAS) - 1)])
                continue
            if r.status_code == 200:
                try:
                    j = r.json()
                    self._token = str(j["access_token"])
                    self._vence = time.time() + int(j.get("expires_in", 3600))
                except (ValueError, KeyError, TypeError):
                    raise ErrorDrive("Google contesto algo raro al renovar el permiso") from None
                return self._token
            d = _detalle(r)
            if r.status_code in (400, 401) and d["razon"] in ("invalid_grant", "invalid_client", "unauthorized_client", "invalid_request"):
                raise ErrorPermiso(
                    f"Google ya no acepta el permiso guardado en el servidor ({d['razon']}): se revoco, vencio o cambio. "
                    "El dueno lo renueva corriendo scripts\\Setup-DriveBroll.ps1 en su PC.")
            if r.status_code == 429 or r.status_code >= 500:
                ultimo = f"Google contesto {r.status_code} al renovar el permiso"
                self.dormir(ESPERAS[min(vez, len(ESPERAS) - 1)])
                continue
            raise ErrorDrive(f"Google rechazo la renovacion del permiso ({r.status_code} {d['razon']})")
        raise ErrorDrive(ultimo or "no se pudo renovar el permiso de Drive")

    # ---- una llamada con reintentos
    def _pedir(self, metodo: str, url: str, *, auth: bool = True, reintentar: bool = True, **kw):
        """Una llamada. Con reintentar=False se hace UNA sola vez (un 429/5xx se devuelve tal cual y un corte de red lanza ErrorDrive):
        asi lo usan los trozos de una subida, que despues de una falla preguntan cuanto guardo Drive en vez de mandar el trozo a ciegas."""
        import requests

        kw.setdefault("timeout", (15, 120))
        cabeceras = dict(kw.pop("headers", {}) or {})
        renovado = False
        ultimo = ""
        for vez in range(len(ESPERAS) + 1 if reintentar else 2):          # sin reintentos queda una segunda vuelta SOLO para renovar el permiso vencido (401)
            if auth:
                cabeceras["Authorization"] = "Bearer " + self._acceso()
            self.llamadas += 1
            try:
                r = self.http.request(metodo, url, headers=cabeceras, **kw)
            except requests.RequestException as e:
                ultimo = f"sin conexion con Drive ({e.__class__.__name__})"
                if reintentar and vez < len(ESPERAS):
                    self.dormir(ESPERAS[vez])
                    continue
                break
            if r.status_code == 401 and auth and not renovado:
                self._acceso(forzar=True)
                renovado = True
                continue
            limite = r.status_code == 403 and _es_limite_de_tasa(r)
            if reintentar and (r.status_code in (429, 500, 502, 503, 504) or limite):
                ultimo = f"Drive contesto {r.status_code}" + (" (demasiadas llamadas)" if limite or r.status_code == 429 else "")
                if vez < len(ESPERAS):
                    try:
                        espera = float(r.headers.get("Retry-After", "")) if r.headers.get("Retry-After") else ESPERAS[vez]
                    except ValueError:
                        espera = ESPERAS[vez]
                    self.dormir(min(max(espera, 1), 120))
                    continue
                break
            return r
        raise ErrorDrive(ultimo or "Drive no respondio")

    @staticmethod
    def _explicar(r, que: str) -> ErrorDrive:
        d = _detalle(r)
        razon, estado = d["razon"], d["estado"]
        if estado == 403 and any(t in razon for t in ("accessNotConfigured", "SERVICE_DISABLED")):
            return ErrorPermiso(
                "La API de Google Drive esta APAGADA en el proyecto de Google Cloud del radar. El dueno la enciende (un clic, gratis) en "
                f"{ENLACE_ACTIVAR_API} con el proyecto del radar elegido, y se repite.")
        if estado in (401, 403) and any(t in razon for t in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientFilePermissions")):
            return ErrorPermiso(f"Con el permiso que hay no se puede {que}: hace falta el de Drive completo. El dueno corre Setup-DriveBroll.ps1 otra vez.")
        if estado == 403 and "storageQuotaExceeded" in razon:
            return ErrorPermiso("A la cuenta de Drive se le acabo el espacio: no se puede subir mas.")
        if estado == 403 and any(t in razon for t in ("dailyLimitExceeded", "quotaExceeded")):
            return ErrorPermiso("Se acabo la cuota diaria de Google Drive para este proyecto: se reintenta manana.")
        if estado == 404:
            return ErrorDrive(f"Drive no encuentra lo que se pidio al intentar {que} (la carpeta se borro, o no esta compartida con la cuenta que dio el permiso)")
        if estado == 403:
            return ErrorDrive(f"Drive no deja {que} (403 {razon or 'sin motivo'}): la cuenta que dio el permiso no puede editar esa carpeta")
        return ErrorDrive(f"Drive contesto {estado} al intentar {que}" + (f" ({razon})" if razon else "") + (f": {d['mensaje']}" if d["mensaje"] else ""))

    def _json(self, r, que: str) -> dict:
        if r.status_code >= 400:
            raise self._explicar(r, que)
        try:
            j = r.json()
        except ValueError:
            raise ErrorDrive(f"Drive contesto algo que no es JSON al intentar {que}") from None
        return j if isinstance(j, dict) else {}

    # ---- la cerca: donde se puede escribir
    def permitir(self, carpeta_id: str) -> None:
        if not RE_ID.match(carpeta_id or ""):
            raise ErrorDrive("id de carpeta no valido")
        self.cercado.add(carpeta_id)

    def _exigir_cercado(self, carpeta_id: str) -> None:
        if carpeta_id not in self.cercado:
            raise ErrorDrive("BUG: se intento escribir en una carpeta que no se declaro con permitir(): no se hace")

    # ---- lectura
    def cuenta(self) -> str:
        j = self._json(self._pedir("GET", f"{URL_API}/about", params={"fields": "user(displayName,emailAddress)"}), "ver la cuenta")
        u = j.get("user") or {}
        return str(u.get("emailAddress") or u.get("displayName") or "(sin nombre)")

    def carpeta(self, carpeta_id: str) -> dict:
        """Datos de una carpeta que debe existir: nombre, si se puede escribir en ella. Falla con un mensaje claro si no sirve."""
        if not RE_ID.match(carpeta_id or ""):
            raise ErrorDrive("id de carpeta no valido")
        j = self._json(self._pedir("GET", f"{URL_API}/files/{carpeta_id}", params={
            "fields": "id,name,mimeType,trashed,capabilities(canAddChildren,canEdit)", "supportsAllDrives": "true"}), "abrir la carpeta de la oferta")
        if j.get("mimeType") != MIME_CARPETA:
            raise ErrorDrive(f"'{j.get('name', carpeta_id)}' no es una carpeta de Drive")
        if j.get("trashed"):
            raise ErrorDrive(f"la carpeta '{j.get('name')}' esta en la papelera de Drive")
        caps = j.get("capabilities") or {}
        j["escribible"] = bool(caps.get("canAddChildren", True))
        return j

    def datos_de(self, archivo_id: str) -> dict:
        """Nombre, tipo, tamano y MD5 de un archivo que ya esta en Drive (Drive v3 solo da los campos que se le piden: CAMPOS_DATOS)."""
        if not RE_ID.match(archivo_id or ""):
            raise ErrorDrive("id de archivo no valido")
        return self._json(self._pedir("GET", f"{URL_API}/files/{archivo_id}", params={
            "fields": CAMPOS_DATOS, "supportsAllDrives": "true"}), "ver el archivo subido")

    def listar_hijos(self, carpeta_id: str) -> list[dict]:
        """Todo lo que hay dentro de una carpeta (sin la papelera), con tamano y MD5."""
        if not RE_ID.match(carpeta_id or ""):
            raise ErrorDrive("id de carpeta no valido")
        salida, pagina = [], None
        for _ in range(50):
            params = {"q": f"'{carpeta_id}' in parents and trashed = false", "pageSize": "1000", "supportsAllDrives": "true",
                      "includeItemsFromAllDrives": "true", "fields": f"nextPageToken,files({CAMPOS_ARCHIVO})", "orderBy": "createdTime"}
            if pagina:
                params["pageToken"] = pagina
            j = self._json(self._pedir("GET", f"{URL_API}/files", params=params), "listar la carpeta")
            salida += [f for f in j.get("files", []) if isinstance(f, dict)]
            pagina = j.get("nextPageToken")
            if not pagina:
                return salida
        raise ErrorDrive("la carpeta tiene demasiados archivos para listarla")

    def hallar_carpeta(self, padre_id: str, nombre: str) -> tuple[str | None, int]:
        """(id de la subcarpeta `nombre` dentro de `padre_id`, cuantas hay con ese nombre). Si hay varias se usa la mas antigua."""
        if not RE_ID.match(padre_id or ""):
            raise ErrorDrive("id de carpeta no valido")
        q = f"'{padre_id}' in parents and name = '{_escapar_q(nombre)}' and mimeType = '{MIME_CARPETA}' and trashed = false"
        j = self._json(self._pedir("GET", f"{URL_API}/files", params={
            "q": q, "pageSize": "20", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "fields": "files(id,name,createdTime)", "orderBy": "createdTime"}), f"buscar la subcarpeta {nombre}")
        hallados = [f for f in j.get("files", []) if isinstance(f, dict) and f.get("id")]
        return (hallados[0]["id"] if hallados else None), len(hallados)

    # ---- escritura (todas pasan por la cerca)
    def _crear_sin_duplicar(self, hacer, ya_creado):
        """Crea algo con una peticion que NO se puede repetir a ciegas. Si Drive lo crea pero la respuesta se pierde (se corta la red, se acaba el tiempo), repetir la
        peticion lo DUPLICA (paso el 2026-10-21 con un archivo de la Fabrica: REF3_analisis.json quedo dos veces). Por eso se manda UNA sola vez y, si no vuelve una respuesta
        buena, antes de repetir se mira si lo creado ya esta. `hacer()` devuelve la respuesta (o lanza ErrorDrive si no hubo); `ya_creado()` devuelve lo que ya hay con ese
        nombre (o None). Resultado: ('respuesta', r) o ('existente', lo_hallado). Un error de verdad (permiso, no existe) se devuelve tal cual para que quien llama lo explique."""
        ultimo = ""
        for vez in range(len(ESPERAS) + 1):
            r = None
            try:
                r = hacer()
            except ErrorDrive as e:
                ultimo = str(e)
            if r is not None:
                if not (r.status_code in (429, 500, 502, 503, 504) or (r.status_code == 403 and _es_limite_de_tasa(r))):
                    return "respuesta", r
                ultimo = f"Drive contesto {r.status_code}" + (" (demasiadas llamadas)" if r.status_code in (403, 429) else "")
            if vez >= len(ESPERAS):
                break
            self.dormir(ESPERAS[vez])
            hallado = ya_creado()
            if hallado is not None:
                return "existente", hallado
        raise ErrorDrive(ultimo or "Drive no respondio")

    def crear_carpeta(self, padre_id: str, nombre: str) -> str:
        self._exigir_cercado(padre_id)
        cuando, algo = self._crear_sin_duplicar(
            lambda: self._pedir("POST", f"{URL_API}/files", params={"supportsAllDrives": "true", "fields": "id,name"}, reintentar=False,
                                headers={"Content-Type": "application/json; charset=UTF-8"},
                                data=json.dumps({"name": nombre, "mimeType": MIME_CARPETA, "parents": [padre_id]}).encode("utf-8")),
            lambda: self.hallar_carpeta(padre_id, nombre)[0])
        if cuando == "existente":
            return algo
        j = self._json(algo, f"crear la subcarpeta {nombre}")
        if not RE_ID.match(str(j.get("id", ""))):
            raise ErrorDrive("Drive no devolvio el id de la carpeta nueva")
        return j["id"]

    def hallar_o_crear_carpeta(self, padre_id: str, nombre: str) -> str:
        carpeta_id, cuantas = self.hallar_carpeta(padre_id, nombre)
        if carpeta_id:
            if cuantas > 1:
                self.registrar(f"  AVISO: hay {cuantas} carpetas llamadas {nombre} en la oferta; se usa la mas antigua")
            return carpeta_id
        return self.crear_carpeta(padre_id, nombre)

    def leer_texto(self, carpeta_id: str, nombre: str) -> bytes | None:
        """Contenido del archivo `nombre` de la carpeta (el mas reciente si hay varios), o None si no existe."""
        for f in self._hallar_archivos(carpeta_id, nombre):
            r = self._pedir("GET", f"{URL_API}/files/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"})
            if r.status_code >= 400:
                raise self._explicar(r, f"leer {nombre}")
            return r.content
        return None

    def _hallar_archivos(self, carpeta_id: str, nombre: str) -> list[dict]:
        if not RE_ID.match(carpeta_id or ""):
            raise ErrorDrive("id de carpeta no valido")
        q = f"'{carpeta_id}' in parents and name = '{_escapar_q(nombre)}' and mimeType != '{MIME_CARPETA}' and trashed = false"
        j = self._json(self._pedir("GET", f"{URL_API}/files", params={
            "q": q, "pageSize": "20", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "fields": f"files({CAMPOS_ARCHIVO})", "orderBy": "modifiedTime desc"}), f"buscar {nombre}")
        return [f for f in j.get("files", []) if isinstance(f, dict) and f.get("id")]

    def guardar_texto(self, carpeta_id: str, nombre: str, datos: bytes, mime: str = "text/plain") -> dict:
        """Crea el archivo `nombre` en la carpeta, o reemplaza su contenido si ya existe (el nombre y el dueno no cambian)."""
        self._exigir_cercado(carpeta_id)
        existentes = self._hallar_archivos(carpeta_id, nombre)
        if existentes:
            j = self._json(self._pedir("PATCH", f"{URL_SUBIDA}/files/{existentes[0]['id']}", params={
                "uploadType": "media", "supportsAllDrives": "true", "fields": "id,name,size"},
                headers={"Content-Type": mime}, data=datos), f"actualizar {nombre}")
            return j
        frontera = "broll" + hashlib.sha1(datos[:2048] + nombre.encode("utf-8")).hexdigest()[:20]
        cuerpo = (f"--{frontera}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                  + json.dumps({"name": nombre, "parents": [carpeta_id]}).encode("utf-8")
                  + f"\r\n--{frontera}\r\nContent-Type: {mime}\r\n\r\n".encode() + datos + f"\r\n--{frontera}--".encode())
        cuando, algo = self._crear_sin_duplicar(
            lambda: self._pedir("POST", f"{URL_SUBIDA}/files", params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,name,size"}, reintentar=False,
                                headers={"Content-Type": f"multipart/related; boundary={frontera}"}, data=cuerpo),
            lambda: (self._hallar_archivos(carpeta_id, nombre) or [None])[0])
        if cuando == "existente":
            return {"id": algo.get("id"), "name": algo.get("name"), "size": algo.get("size")}
        return self._json(algo, f"crear {nombre}")

    # ---- subida reanudable de un video
    def subir_archivo(self, ruta: Path | str, carpeta_id: str, nombre: str, mime: str = "video/mp4") -> dict:
        """Sube el archivo y comprueba que Drive lo guardo igual (tamano y MD5). Devuelve los datos que dio Drive."""
        self._exigir_cercado(carpeta_id)
        ruta = Path(ruta)
        total = ruta.stat().st_size
        if total <= 0:
            raise ErrorDrive(f"{ruta.name} esta vacio")
        md5_local = _md5(ruta)
        ultimo = ""
        for vez in range(1, 4):
            try:
                sesion = self._abrir_sesion(nombre, carpeta_id, mime, total)
                meta = self._enviar_por_trozos(sesion, ruta, total, mime)
            except _SesionPerdida as e:
                ultimo = str(e)
                self.dormir(3 * vez)
                continue
            if meta.get("size") is None and meta.get("id"):
                # Si Drive no devolvio el tamano en la respuesta de la subida, se le pregunta aparte: nunca se da por subido sin comprobarlo
                meta = {**meta, **self.datos_de(str(meta["id"]))}
            if str(meta.get("size", "")) != str(total):
                raise ErrorDrive(f"Drive guardo {nombre} con otro tamano (mando {total} bytes, quedo {meta.get('size')}): no se da por subido")
            if meta.get("md5Checksum") and str(meta["md5Checksum"]).lower() != md5_local:
                raise ErrorDrive(f"Drive guardo {nombre} con otro contenido (el MD5 no coincide): no se da por subido")
            self.bytes_subidos += total
            return meta
        raise ErrorDrive(f"no pude terminar de subir {nombre}: {ultimo or 'la sesion de subida se perdio'}")

    def _abrir_sesion(self, nombre: str, carpeta_id: str, mime: str, total: int) -> str:
        r = self._pedir("POST", f"{URL_SUBIDA}/files", params={
            "uploadType": "resumable", "supportsAllDrives": "true", "fields": "id,name,size,md5Checksum,parents,mimeType"},
            headers={"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(total)},
            data=json.dumps({"name": nombre, "parents": [carpeta_id], "mimeType": mime}).encode("utf-8"))
        if r.status_code >= 400:
            raise self._explicar(r, f"empezar a subir {nombre}")
        destino = r.headers.get("Location", "")
        if not destino.startswith(URL_SUBIDA.split("/upload/")[0] + "/"):     # en produccion: https://www.googleapis.com/
            raise ErrorDrive("Drive no dio una direccion de subida valida")
        return destino

    def _enviar_por_trozos(self, sesion: str, ruta: Path, total: int, mime: str) -> dict:
        pos, fallos = 0, 0
        with ruta.open("rb") as f:
            while True:
                f.seek(pos)
                datos = f.read(TROZO)
                if not datos:
                    raise ErrorDrive(f"el archivo {ruta.name} cambio mientras se subia")
                fin = pos + len(datos) - 1
                cab = {"Content-Length": str(len(datos)), "Content-Range": f"bytes {pos}-{fin}/{total}", "Content-Type": mime}
                r = None
                try:
                    r = self._pedir("PUT", sesion, auth=False, reintentar=False, headers=cab, data=datos, timeout=(15, 300))
                except ErrorDrive:
                    pass                               # corte de red: se cae abajo a preguntar cuanto guardo Drive
                if r is not None:
                    if r.status_code in (200, 201):
                        try:
                            return r.json()
                        except ValueError:
                            raise ErrorDrive("Drive termino la subida pero contesto algo que no es JSON") from None
                    if r.status_code == 308:
                        pos = _siguiente_byte(r)
                        fallos = 0
                        continue
                    if r.status_code in (404, 410):
                        raise _SesionPerdida(f"Drive olvido la sesion de subida ({r.status_code})")
                    if r.status_code not in (429, 500, 502, 503, 504):
                        raise self._explicar(r, f"subir {ruta.name}")
                # Falla de red o de Drive: se espera, se pregunta cuanto tiene Drive ya, y se sigue desde ahi (nunca a ciegas)
                fallos += 1
                if fallos > 6:
                    raise ErrorDrive(f"la subida de {ruta.name} se corta una y otra vez (red o Drive inestable)")
                self.dormir(ESPERAS[min(fallos - 1, len(ESPERAS) - 1)])
                avance = self._consultar_avance(sesion, total)
                if isinstance(avance, dict):
                    return avance
                pos = avance

    def _consultar_avance(self, sesion: str, total: int):
        """Despues de un corte: cuantos bytes tiene Drive ya (o los datos del archivo, si la subida alcanzo a terminar)."""
        r = self._pedir("PUT", sesion, auth=False, headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"}, data=b"")
        if r.status_code in (200, 201):
            try:
                return r.json()
            except ValueError:
                raise ErrorDrive("Drive termino la subida pero contesto algo que no es JSON") from None
        if r.status_code == 308:
            return _siguiente_byte(r)
        if r.status_code in (404, 410):
            raise _SesionPerdida(f"Drive olvido la sesion de subida ({r.status_code})")
        raise self._explicar(r, "retomar la subida")


def _siguiente_byte(r) -> int:
    """Una respuesta 308 dice 'Range: bytes=0-N' (lo que Drive ya tiene); sin esa cabecera no tiene nada."""
    rango = r.headers.get("Range", "")
    m = re.match(r"bytes=0-(\d+)$", rango.strip())
    return int(m.group(1)) + 1 if m else 0


def _es_limite_de_tasa(r) -> bool:
    return any(t in _detalle(r)["razon"] for t in ("rateLimitExceeded", "userRateLimitExceeded", "sharingRateLimitExceeded"))


# ------------------------------------------------------------------ linea de comandos: probar
def _carpetas_a_probar(argumentos: list[str]) -> list[tuple[str, str]]:
    if argumentos:
        return [(a, a) for a in argumentos]
    cfg = RAIZ / "ofertas.json"
    if not cfg.is_file():
        return []
    try:
        ofertas = json.loads(cfg.read_text(encoding="utf-8")).get("ofertas", [])
    except (OSError, ValueError):
        return []
    return [(str(o.get("producto", "?")), str(o.get("drive", ""))) for o in ofertas if isinstance(o, dict)]


def probar(argumentos: list[str]) -> int:
    """Renueva el permiso, dice con que cuenta de Google esta y mira cada carpeta de oferta. Sin llaves en pantalla. 0 bien, 4 sin permiso."""
    try:
        d = Drive(cargar_credenciales())
        print(f"Permiso renovado. Cuenta de Google: {d.cuenta()}")
        malas = 0
        for nombre, enlace in _carpetas_a_probar(argumentos):
            cid = id_de_carpeta(enlace)
            if not cid:
                print(f"  x {nombre}: no reconozco el enlace de la carpeta")
                malas += 1
                continue
            try:
                c = d.carpeta(cid)
            except ErrorPermiso:
                raise
            except ErrorDrive as e:
                print(f"  x {nombre}: {e}")
                malas += 1
                continue
            sub, _ = d.hallar_carpeta(cid, CARPETA_BROLLS)
            n = len(d.listar_hijos(sub)) if sub else 0
            print(f"  ok {nombre}: carpeta '{c['name']}' ({'se puede escribir' if c['escribible'] else 'SOLO LECTURA'}); "
                  + (f"{CARPETA_BROLLS} ya existe con {n} archivo(s)" if sub else f"{CARPETA_BROLLS} todavia no existe (se crea en la primera corrida)"))
            if not c["escribible"]:
                malas += 1
        return 0 if not malas else 1
    except ErrorPermiso as e:
        print(f"SIN PERMISO: {e}")
        return 4
    except ErrorDrive as e:
        print(f"ERROR: {e}")
        return 1


def main() -> None:
    configurar_consola()
    if len(sys.argv) >= 2 and sys.argv[1] == "probar":
        sys.exit(probar(sys.argv[2:]))
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
