"""Autoprueba de scripts/drive_api.py SIN internet y sin tocar el Drive de nadie: un Google Drive FALSO en esta misma maquina
(carpetas y archivos en memoria, subida reanudable por trozos, renovacion del permiso) al que se le pueden provocar fallas.

Comprueba: enlaces de carpeta, credenciales, la renovacion del permiso (y que un permiso revocado se explique), hallar/crear la subcarpeta B-rolls,
listar con paginas, escribir y leer archivos de texto, la subida de un video (por trozos, y retomada despues de un corte, de un error 500, de
una sesion perdida, de un guardado a medias), que NUNCA se de por subido algo que Drive guardo distinto (tamano o MD5), la cerca que impide
escribir fuera de las carpetas declaradas, que no exista ninguna funcion de borrar, y que ninguna llave salga en pantalla ni en un error.

Uso:  python scripts/autoprueba_drive.py        Termina con "AUTOPRUEBA OK" si todo va bien. Corre esto despues de tocar drive_api.py.
Este mismo archivo sirve el Drive falso a autoprueba_broll_vps.py (iniciar_drive_falso).
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola  # noqa: E402

MIME_CARPETA = "application/vnd.google-apps.folder"
FALLAS: list[str] = []


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"  ->  {detalle}" if detalle else ""))


# ------------------------------------------------------------------ el Drive falso
class EstadoDrive:
    """Todo lo que 'hay' en el Drive falso y las fallas que se le piden."""

    def __init__(self):
        self.cred = {"client_id": "cliente-de-prueba.apps.googleusercontent.com", "client_secret": "SECRETO-DE-PRUEBA-0123456789",
                     "refresh_token": "REFRESCO-DE-PRUEBA-0123456789"}
        self.reiniciar()

    def reiniciar(self):
        self.archivos: dict[str, dict] = {}
        self.sesiones: dict[str, dict] = {}
        self.tokens_validos: set[str] = set()
        self.n_tokens = 0
        self.n_ids = 0
        self.log: list[tuple[str, str, dict]] = []
        self.fallas: dict[str, int | bool] = {}
        self.solo_lectura: set[str] = set()
        self.tamano_pagina: int | None = None
        self.tiempo = 0

    # ---- datos
    def nuevo_id(self) -> str:
        self.n_ids += 1
        return f"idprueba{self.n_ids:08d}"

    def poner(self, nombre: str, mime: str = "application/octet-stream", padres: list[str] | None = None, contenido: bytes = b"",
              papelera: bool = False, id_fijo: str | None = None) -> dict:
        self.tiempo += 1
        f = {"id": id_fijo or self.nuevo_id(), "name": nombre, "mimeType": mime, "parents": list(padres or []), "content": contenido,
             "trashed": papelera, "createdTime": f"2026-10-20T10:{self.tiempo // 60:02d}:{self.tiempo % 60:02d}.000Z",
             "modifiedTime": f"2026-10-20T10:{self.tiempo // 60:02d}:{self.tiempo % 60:02d}.000Z"}
        self.archivos[f["id"]] = f
        return f

    def hijos(self, padre: str) -> list[dict]:
        return [f for f in self.archivos.values() if padre in f["parents"] and not f["trashed"]]

    def tomar(self, falla: str) -> bool:
        """True si hay que provocar esta falla ahora (las que son un numero se gastan de a una)."""
        v = self.fallas.get(falla)
        if v is True:
            return True
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            self.fallas[falla] = v - 1
            return True
        return False

    def ficha(self, f: dict) -> dict:
        d = {k: f[k] for k in ("id", "name", "mimeType", "parents", "trashed", "createdTime", "modifiedTime")}
        if f["mimeType"] == MIME_CARPETA:
            d["capabilities"] = {"canAddChildren": f["id"] not in self.solo_lectura, "canEdit": True}
        else:
            d["size"] = str(len(f["content"]))
            d["md5Checksum"] = hashlib.md5(f["content"]).hexdigest()
        return d


def _cuerpo_error(codigo: int, razon: str, mensaje: str, estado: str = "") -> dict:
    e = {"code": codigo, "message": mensaje, "errors": [{"domain": "global", "reason": razon, "message": mensaje}]}
    if estado:
        e["status"] = estado
        e["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": razon}]
    return {"error": e}


class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def est(self) -> EstadoDrive:
        return self.server.estado     # type: ignore[attr-defined]

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    # ---- utilidades de respuesta
    def responder(self, codigo: int, cuerpo=None, cabeceras: dict | None = None, crudo: bytes | None = None) -> None:
        datos = crudo if crudo is not None else (json.dumps(cuerpo).encode() if cuerpo is not None else b"")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json" if crudo is None else "application/octet-stream")
        self.send_header("Content-Length", str(len(datos)))
        for k, v in (cabeceras or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(datos)

    def cortar(self) -> None:
        """Cierra la conexion sin contestar (como una red que se cae)."""
        self.close_connection = True
        with contextlib.suppress(OSError):
            self.connection.shutdown(2)
        with contextlib.suppress(OSError):
            self.connection.close()

    def leer_cuerpo(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def parametros(self) -> tuple[str, dict]:
        u = urlparse(self.path)
        return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}

    # ---- el enrutador
    def despachar(self, metodo: str) -> None:
        ruta, p = self.parametros()
        cuerpo = self.leer_cuerpo()
        est = self.est
        est.log.append((metodo, ruta, p))
        if ruta == "/token" and metodo == "POST":
            return self.token(cuerpo)
        if ruta.startswith("/upload/sesion/"):
            return self.trozo(ruta.rsplit("/", 1)[1], cuerpo)
        # --- de aqui en adelante hace falta el permiso de acceso
        if est.tomar("api_apagada"):
            return self.responder(403, _cuerpo_error(403, "SERVICE_DISABLED", "Google Drive API has not been used in project 1 before or it is disabled.", "PERMISSION_DENIED"))
        if est.tomar("scope_malo"):
            return self.responder(403, _cuerpo_error(403, "insufficientPermissions", "Insufficient Permission"))
        if est.tomar("sin_espacio"):
            return self.responder(403, _cuerpo_error(403, "storageQuotaExceeded", "The user's Drive storage quota has been exceeded."))
        if est.tomar("expirar_token"):
            est.tokens_validos.clear()
        autor = self.headers.get("Authorization", "")
        if not autor.startswith("Bearer ") or autor[7:] not in est.tokens_validos:
            return self.responder(401, _cuerpo_error(401, "authError", "Invalid Credentials"))
        if est.tomar("limite_tasa"):
            return self.responder(403, _cuerpo_error(403, "rateLimitExceeded", "Rate Limit Exceeded"))
        if est.tomar("http_429"):
            return self.responder(429, _cuerpo_error(429, "rateLimitExceeded", "Too Many Requests"), {"Retry-After": "0"})
        if est.tomar("http_500"):
            return self.responder(500, _cuerpo_error(500, "backendError", "Backend Error"))
        if ruta == "/drive/v3/about" and metodo == "GET":
            return self.responder(200, {"user": {"displayName": "Dueno de Prueba", "emailAddress": "dueno@prueba.example"}})
        if ruta == "/drive/v3/files" and metodo == "GET":
            return self.listar(p)
        if ruta == "/drive/v3/files" and metodo == "POST":
            return self.crear(cuerpo)
        m = re.fullmatch(r"/drive/v3/files/([A-Za-z0-9_-]+)", ruta)
        if m and metodo == "GET":
            return self.ver(m.group(1), p)
        if ruta == "/upload/drive/v3/files" and metodo == "POST":
            return self.subida_nueva(p, cuerpo)
        m = re.fullmatch(r"/upload/drive/v3/files/([A-Za-z0-9_-]+)", ruta)
        if m and metodo == "PATCH":
            return self.reemplazar(m.group(1), cuerpo)
        return self.responder(404, _cuerpo_error(404, "notFound", f"ruta no soportada por el Drive falso: {metodo} {ruta}"))

    do_GET = lambda self: self.despachar("GET")          # noqa: E731
    do_POST = lambda self: self.despachar("POST")        # noqa: E731
    do_PUT = lambda self: self.despachar("PUT")          # noqa: E731
    do_PATCH = lambda self: self.despachar("PATCH")      # noqa: E731

    # ---- el permiso
    def token(self, cuerpo: bytes) -> None:
        est = self.est
        if est.tomar("token_500"):
            return self.responder(500, {"error": "backend_error"})
        form = {k: v[0] for k, v in parse_qs(cuerpo.decode()).items()}
        if est.tomar("revocado") or form.get("refresh_token") != est.cred["refresh_token"]:
            return self.responder(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        if form.get("client_id") != est.cred["client_id"] or form.get("client_secret") != est.cred["client_secret"] or form.get("grant_type") != "refresh_token":
            return self.responder(401, {"error": "invalid_client", "error_description": "The OAuth client was not found."})
        est.n_tokens += 1
        tok = f"ya29.token-de-prueba-{est.n_tokens}"
        est.tokens_validos.add(tok)
        self.responder(200, {"access_token": tok, "expires_in": 3599, "scope": "https://www.googleapis.com/auth/drive", "token_type": "Bearer"})

    # ---- archivos
    def ver(self, fid: str, p: dict) -> None:
        f = self.est.archivos.get(fid)
        if not f:
            return self.responder(404, _cuerpo_error(404, "notFound", f"File not found: {fid}."))
        if p.get("alt") == "media":
            return self.responder(200, crudo=f["content"])
        self.responder(200, self.est.ficha(f))

    def listar(self, p: dict) -> None:
        q = p.get("q", "")
        padre = re.search(r"'([^']+)' in parents", q)
        nombre = re.search(r"name = '((?:[^'\\]|\\.)*)'", q)
        es = re.search(r"mimeType = '([^']+)'", q)
        no_es = re.search(r"mimeType != '([^']+)'", q)
        sin_papelera = "trashed = false" in q
        res = []
        for f in self.est.archivos.values():
            if padre and padre.group(1) not in f["parents"]:
                continue
            if nombre and f["name"] != re.sub(r"\\(.)", r"\1", nombre.group(1)):
                continue
            if es and f["mimeType"] != es.group(1):
                continue
            if no_es and f["mimeType"] == no_es.group(1):
                continue
            if sin_papelera and f["trashed"]:
                continue
            res.append(f)
        if p.get("orderBy") == "modifiedTime desc":
            res.sort(key=lambda f: f["modifiedTime"], reverse=True)
        else:
            res.sort(key=lambda f: f["createdTime"])
        tam = self.est.tamano_pagina or int(p.get("pageSize", "100"))
        desde = int(p.get("pageToken", "0") or 0)
        trozo = res[desde:desde + tam]
        salida = {"files": [self.est.ficha(f) for f in trozo]}
        if desde + tam < len(res):
            salida["nextPageToken"] = str(desde + tam)
        self.responder(200, salida)

    def crear(self, cuerpo: bytes) -> None:
        meta = json.loads(cuerpo.decode())
        for padre in meta.get("parents", []):
            if padre not in self.est.archivos:
                return self.responder(404, _cuerpo_error(404, "notFound", f"File not found: {padre}."))
        if self.est.tomar("corta_sin_crear"):
            return self.cortar()
        f = self.est.poner(meta["name"], meta.get("mimeType", "application/octet-stream"), meta.get("parents"))
        if self.est.tomar("crea_y_corta"):          # Drive LO CREA, pero la respuesta no llega (como una red que se cae justo despues)
            return self.cortar()
        self.responder(200, self.est.ficha(f))

    def reemplazar(self, fid: str, cuerpo: bytes) -> None:
        f = self.est.archivos.get(fid)
        if not f:
            return self.responder(404, _cuerpo_error(404, "notFound", f"File not found: {fid}."))
        f["content"] = cuerpo
        self.est.tiempo += 1
        f["modifiedTime"] = f"2026-10-20T11:{self.est.tiempo // 60:02d}:{self.est.tiempo % 60:02d}.000Z"
        self.responder(200, self.est.ficha(f))

    # ---- subidas
    def subida_nueva(self, p: dict, cuerpo: bytes) -> None:
        est = self.est
        tipo = p.get("uploadType")
        if tipo == "multipart":
            m = re.search(r'boundary=([^\s;]+)', self.headers.get("Content-Type", ""))
            partes = cuerpo.split(("--" + m.group(1)).encode()) if m else []
            if len(partes) < 3:
                return self.responder(400, _cuerpo_error(400, "badRequest", "multipart mal formado"))
            cab1, _, js = partes[1].partition(b"\r\n\r\n")
            _, _, datos = partes[2].partition(b"\r\n\r\n")
            meta = json.loads(js.strip().decode())
            for padre in meta.get("parents", []):
                if padre not in est.archivos:
                    return self.responder(404, _cuerpo_error(404, "notFound", f"File not found: {padre}."))
            if est.tomar("corta_sin_crear"):
                return self.cortar()
            f = est.poner(meta["name"], "text/plain", meta.get("parents"), datos[:-2] if datos.endswith(b"\r\n") else datos)
            if est.tomar("crea_y_corta"):
                return self.cortar()
            return self.responder(200, est.ficha(f))
        if tipo == "resumable":
            meta = json.loads(cuerpo.decode())
            for padre in meta.get("parents", []):
                if padre not in est.archivos:
                    return self.responder(404, _cuerpo_error(404, "notFound", f"File not found: {padre}."))
            sid = f"s{len(est.sesiones) + 1}"
            est.sesiones[sid] = {"meta": meta, "total": int(self.headers.get("X-Upload-Content-Length") or 0), "datos": bytearray(),
                                 "trozos": 0, "terminada": None}
            destino = "https://evil.example.com/robar" if est.tomar("ubicacion_ajena") else f"{self.base}/upload/sesion/{sid}"
            return self.responder(200, {}, {"Location": destino})
        self.responder(400, _cuerpo_error(400, "badRequest", "uploadType desconocido"))

    def trozo(self, sid: str, cuerpo: bytes) -> None:
        est = self.est
        s = est.sesiones.get(sid)
        if s is None or s.get("perdida"):
            return self.responder(404, _cuerpo_error(404, "notFound", "Upload session not found"))
        cr = self.headers.get("Content-Range", "")
        if cr.startswith("bytes */"):          # pregunta "cuanto tienes?"
            if s["terminada"]:
                return self.responder(200, s["terminada"])
            cab = {"Range": f"bytes=0-{len(s['datos']) - 1}"} if s["datos"] else {}
            return self.responder(308, None, cab)
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", cr)
        if not m:
            return self.responder(400, _cuerpo_error(400, "badRequest", "Content-Range mal formado"))
        a, b, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if est.tomar("sesion_perdida"):
            s["perdida"] = True
            return self.responder(404, _cuerpo_error(404, "notFound", "Upload session not found"))
        if est.tomar("chunk_500_sin_guardar"):
            return self.responder(500, _cuerpo_error(500, "backendError", "Backend Error"))
        if est.tomar("corte_sin_guardar"):
            return self.cortar()
        if a != len(s["datos"]):               # el cliente manda desde otro punto: se le dice hasta donde hay
            cab = {"Range": f"bytes=0-{len(s['datos']) - 1}"} if s["datos"] else {}
            return self.responder(308, None, cab)
        trozo = cuerpo
        if est.fallas.get("parcial") and b + 1 < total and len(cuerpo) >= 2 * 262144:
            trozo = cuerpo[:(len(cuerpo) // 2 // 262144) * 262144]       # Drive solo alcanzo a guardar la primera parte (multiplo de 256 KiB)
        s["datos"] += trozo
        s["trozos"] += 1
        if est.tomar("chunk_500_guardando"):
            return self.responder(500, _cuerpo_error(500, "backendError", "Backend Error"))
        if est.tomar("corte_guardando"):
            return self.cortar()
        if len(s["datos"]) >= total:
            contenido = bytes(s["datos"])
            f = est.poner(s["meta"]["name"], s["meta"].get("mimeType", "video/mp4"), s["meta"].get("parents"), contenido)
            ficha = est.ficha(f)
            if est.fallas.get("md5_malo"):
                ficha["md5Checksum"] = "0" * 32
            if est.fallas.get("tamano_malo"):
                ficha["size"] = str(len(contenido) - 1)
            if est.fallas.get("sin_tamano_en_respuesta"):
                ficha.pop("size", None)
                ficha.pop("md5Checksum", None)
            s["terminada"] = ficha
            return self.responder(200, ficha)
        self.responder(308, None, {"Range": f"bytes=0-{len(s['datos']) - 1}"})


def iniciar_drive_falso() -> tuple[ThreadingHTTPServer, EstadoDrive]:
    """Arranca el Drive falso y deja BROLL_DRIVE_URL apuntando a el (hay que importar drive_api DESPUES de llamar esto)."""
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    estado = EstadoDrive()
    servidor.estado = estado         # type: ignore[attr-defined]
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    os.environ["BROLL_DRIVE_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    return servidor, estado


# ------------------------------------------------------------------ pruebas
def nuevo_drive(d, est: EstadoDrive, **kw):
    """Un cliente nuevo contra el Drive falso, sin esperas reales."""
    kw.setdefault("dormir", lambda s: None)
    kw.setdefault("registrar", lambda *a, **k: None)
    return d.Drive(dict(est.cred), **kw)


def archivo_de(tmp: Path, nombre: str, tam: int) -> Path:
    ruta = tmp / nombre
    ruta.write_bytes(os.urandom(tam))
    return ruta


def pruebas(d, est: EstadoDrive, tmp: Path) -> None:
    print("Enlaces de carpeta")
    ID = "ID_DE_TU_CARPETA_DE_DRIVE_01"
    for texto, esperado in ((ID, ID),
                            (f"https://drive.google.com/drive/folders/{ID}", ID),
                            (f"https://drive.google.com/drive/folders/{ID}?usp=sharing", ID),
                            (f"https://drive.google.com/drive/u/0/folders/{ID}?resourcekey=0-abc", ID),
                            (f"https://drive.google.com/open?id={ID}", ID),
                            (f"  https://drive.google.com/drive/folders/{ID}  ", ID),
                            (f"https://evil.example.com/drive/folders/{ID}", None),
                            ("https://drive.google.com/drive/folders/corto", None),
                            ("https://drive.google.com/file/d/abc/view", None),
                            ("javascript:alert(1)", None), ("", None), ("no es un enlace", None), ("abc", None)):
        comprobar(d.id_de_carpeta(texto) == esperado, f"id_de_carpeta({texto[:48]!r})", str(d.id_de_carpeta(texto)))

    print("Credenciales")
    ruta = tmp / "google.json"
    try:
        d.cargar_credenciales(ruta)
        comprobar(False, "sin archivo de credenciales: se explica")
    except d.ErrorPermiso as e:
        comprobar("Setup-DriveBroll" in str(e) and str(ruta) in str(e), "sin archivo de credenciales: dice que corra Setup-DriveBroll.ps1", str(e))
    for contenido, nombre in (("{no es json", "JSON roto"), (json.dumps({"client_id": "a", "client_secret": "b"}), "sin refresh_token"),
                              (json.dumps({"client_id": "a b", "client_secret": "b", "refresh_token": "c"}), "con espacios")):
        ruta.write_text(contenido, encoding="utf-8")
        try:
            d.cargar_credenciales(ruta)
            comprobar(False, f"credenciales {nombre}: se rechazan")
        except d.ErrorPermiso as e:
            comprobar("a b" not in str(e) and "SECRETO" not in str(e), f"credenciales {nombre}: se rechazan sin repetir su contenido", str(e))
    ruta.write_text(json.dumps({**est.cred, "scope": d.ALCANCE}), encoding="utf-8")
    c = d.cargar_credenciales(ruta)
    comprobar(c["refresh_token"] == est.cred["refresh_token"] and c["scope"] == d.ALCANCE, "credenciales buenas se leen")

    print("Permiso de acceso")
    est.reiniciar()
    cli = nuevo_drive(d, est)
    raiz = est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    cli.carpeta(ID)
    cli.carpeta(ID)
    comprobar(est.n_tokens == 1, "el permiso se renueva una sola vez y se reutiliza", f"{est.n_tokens} renovaciones")
    est.fallas["expirar_token"] = 1
    cli.carpeta(ID)
    comprobar(est.n_tokens == 2, "si Drive dice 401 se renueva el permiso y se repite la llamada", f"{est.n_tokens}")
    est.reiniciar()
    est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    est.fallas["token_500"] = 2
    comprobar(nuevo_drive(d, est).carpeta(ID)["name"] == "OFERTA", "si Google falla (500) al renovar se reintenta")
    est.fallas["revocado"] = True
    try:
        nuevo_drive(d, est).carpeta(ID)
        comprobar(False, "permiso revocado: se explica")
    except d.ErrorPermiso as e:
        comprobar("Setup-DriveBroll" in str(e) and "REFRESCO" not in str(e) and "SECRETO" not in str(e), "permiso revocado: error de permiso que dice que hacer y no repite llaves", str(e))
    est.reiniciar()
    mala = dict(est.cred, refresh_token="OTRO-REFRESCO")
    try:
        d.Drive(mala, dormir=lambda s: None).carpeta(ID)
        comprobar(False, "refresh_token equivocado: se explica")
    except d.ErrorPermiso as e:
        comprobar("OTRO-REFRESCO" not in str(e), "refresh_token equivocado: error de permiso sin repetir la llave", str(e))

    print("Cuenta y carpeta de la oferta")
    est.reiniciar()
    cli = nuevo_drive(d, est)
    est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    comprobar(cli.cuenta() == "dueno@prueba.example", "dice con que cuenta de Google esta")
    inf = cli.carpeta(ID)
    comprobar(inf["name"] == "OFERTA" and inf["escribible"], "una carpeta normal: nombre y se puede escribir")
    est.solo_lectura.add(ID)
    comprobar(cli.carpeta(ID)["escribible"] is False, "una carpeta de solo lectura se reconoce")
    est.solo_lectura.clear()
    for malo, texto in ((est.poner("video.mp4", "video/mp4", [ID]).get("id"), "no es una carpeta"), ):
        try:
            cli.carpeta(malo)
            comprobar(False, f"un archivo que {texto} se rechaza")
        except d.ErrorDrive as e:
            comprobar(texto in str(e), f"un archivo que {texto} se rechaza", str(e))
    est.poner("VIEJA", MIME_CARPETA, ["raiz"], papelera=True, id_fijo="idpapelera000001")
    try:
        cli.carpeta("idpapelera000001")
        comprobar(False, "carpeta en la papelera se rechaza")
    except d.ErrorDrive as e:
        comprobar("papelera" in str(e), "carpeta en la papelera se rechaza", str(e))
    try:
        cli.carpeta("idquenoexiste0001")
        comprobar(False, "carpeta inexistente se explica")
    except d.ErrorDrive as e:
        comprobar("no encuentra" in str(e) and "compartida" in str(e), "carpeta inexistente: dice que puede ser que no este compartida", str(e))
    try:
        cli.carpeta("corto")
        comprobar(False, "id invalido se rechaza antes de llamar")
    except d.ErrorDrive:
        comprobar(True, "id invalido se rechaza antes de llamar")

    print("La cerca y las funciones que NO existen")
    try:
        cli.crear_carpeta(ID, "B-rolls")
        comprobar(False, "escribir sin declarar la carpeta: se niega")
    except d.ErrorDrive as e:
        comprobar("permitir" in str(e), "escribir sin declarar la carpeta: se niega", str(e))
    cli.permitir(ID)
    cli.crear_carpeta(ID, "B-rolls")
    try:
        cli.guardar_texto("idotra0000000001", "x.txt", b"x")
        comprobar(False, "guardar_texto fuera de la cerca se niega")
    except d.ErrorDrive:
        comprobar(True, "guardar_texto fuera de la cerca se niega")
    try:
        cli.subir_archivo(archivo_de(tmp, "fuera.mp4", 100), "idotra0000000001", "fuera.mp4")
        comprobar(False, "subir_archivo fuera de la cerca se niega")
    except d.ErrorDrive:
        comprobar(True, "subir_archivo fuera de la cerca se niega")
    publicos = [n for n in dir(d.Drive) if not n.startswith("_")]
    peligrosos = [n for n in dir(d.Drive) if re.search(r"borr|elimin|delete|remove|trash|papelera|move|mover|compart|share|permiso_de|vaciar|destroy", n, re.I) and n != "permitir"]
    comprobar(not peligrosos, "Drive no tiene ninguna funcion de borrar, mover ni compartir", str(peligrosos))
    comprobar(not any(m in {"DELETE"} for m, _, _ in est.log), "ninguna llamada fue DELETE")
    comprobar(set(publicos) >= {"subir_archivo", "guardar_texto", "leer_texto", "listar_hijos", "hallar_o_crear_carpeta", "carpeta", "permitir"}, "la superficie publica es la esperada", str(publicos))

    print("Subcarpeta B-rolls")
    est.reiniciar()
    cli = nuevo_drive(d, est)
    est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    cli.permitir(ID)
    comprobar(cli.hallar_carpeta(ID, "B-rolls") == (None, 0), "si no existe, hallar dice que no")
    b1 = cli.hallar_o_crear_carpeta(ID, "B-rolls")
    b2 = cli.hallar_o_crear_carpeta(ID, "B-rolls")
    comprobar(b1 == b2 and len([f for f in est.archivos.values() if f["name"] == "B-rolls"]) == 1, "se crea una vez y la segunda vez se encuentra", f"{b1} {b2}")
    comprobar(est.archivos[b1]["mimeType"] == MIME_CARPETA and est.archivos[b1]["parents"] == [ID], "queda dentro de la carpeta de la oferta y es una carpeta")
    est.poner("B-rolls", MIME_CARPETA, [ID])       # otra con el mismo nombre (alguien la creo a mano)
    avisos: list[str] = []
    cli2 = nuevo_drive(d, est, registrar=avisos.append)
    cli2.permitir(ID)
    comprobar(cli2.hallar_o_crear_carpeta(ID, "B-rolls") == b1 and any("2 carpetas" in a for a in avisos), "si hay dos B-rolls usa la mas antigua y avisa", str(avisos))
    est.poner("B-rolls", MIME_CARPETA, [ID], papelera=True)
    comprobar(cli2.hallar_carpeta(ID, "B-rolls")[1] == 2, "una B-rolls en la papelera no cuenta")
    comprobar(cli2.hallar_carpeta(ID, "Alo'ha")[0] is None and any("Alo\\'ha" in p.get("q", "") for _, _, p in est.log), "las comillas simples de un nombre se escapan en la consulta")

    print("Listar con paginas")
    est.reiniciar()
    cli = nuevo_drive(d, est)
    est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    for i in range(7):
        est.poner(f"tt_{i}.mp4", "video/mp4", [ID], bytes([i]) * (i + 1))
    est.poner("borrado.mp4", "video/mp4", [ID], b"x", papelera=True)
    est.poner("otra-carpeta.mp4", "video/mp4", ["otro"], b"x")
    est.tamano_pagina = 3
    hijos = cli.listar_hijos(ID)
    comprobar([h["name"] for h in hijos] == [f"tt_{i}.mp4" for i in range(7)], "lista las 7 en 3 paginas, sin papelera ni lo de otra carpeta", str([h["name"] for h in hijos]))
    comprobar(hijos[2]["size"] == "3" and hijos[2]["md5Checksum"] == hashlib.md5(b"\x02\x02\x02").hexdigest(), "trae tamano y MD5")
    est.tamano_pagina = None

    print("Archivos de texto")
    est.reiniciar()
    cli = nuevo_drive(d, est)
    est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    cli.permitir(ID)
    B = cli.hallar_o_crear_carpeta(ID, "B-rolls")
    cli.permitir(B)
    comprobar(cli.leer_texto(B, "fuentes.csv") is None, "leer un archivo que no existe da None")
    csv1 = (chr(0xFEFF) + "id,archivo\n1,tt_1.mp4\n2,tt_2 con acentos " + chr(0xF1) + ".mp4\n").encode("utf-8")
    cli.guardar_texto(B, "fuentes.csv", csv1, "text/csv")
    comprobar(cli.leer_texto(B, "fuentes.csv") == csv1, "guardar y leer devuelve los mismos bytes (BOM y acentos incluidos)")
    csv2 = csv1 + "3,tt_3.mp4\n".encode()
    n_antes = len(est.hijos(B))
    cli.guardar_texto(B, "fuentes.csv", csv2, "text/csv")
    comprobar(cli.leer_texto(B, "fuentes.csv") == csv2 and len(est.hijos(B)) == n_antes, "guardar de nuevo REEMPLAZA el contenido, no crea otro archivo")
    otra = est.poner("fuentes.csv", "text/csv", [B], b"copia reciente")
    otra["modifiedTime"] = "2026-10-21T00:00:00.000Z"
    comprobar(cli.leer_texto(B, "fuentes.csv") == b"copia reciente", "si hay dos con el mismo nombre se lee el mas reciente")
    leeme = ("Hola " + chr(0xF1) + "\n").encode("utf-8")
    cli.guardar_texto(B, "LEEME.txt", leeme)
    comprobar(cli.leer_texto(B, "LEEME.txt") == leeme, "un LEEME.txt nuevo se crea completo")
    comprobar(not est.archivos[[f["id"] for f in est.hijos(B) if f["name"] == "LEEME.txt"][0]]["mimeType"].endswith("folder"), "y no es una carpeta")

    print("Crear sin duplicar cuando se pierde la respuesta")
    est.reiniciar()
    cli = nuevo_drive(d, est)
    est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    cli.permitir(ID)
    est.fallas["crea_y_corta"] = 1
    nueva = cli.crear_carpeta(ID, "REFERENCIA")
    copias = [f for f in est.hijos(ID) if f["name"] == "REFERENCIA"]
    comprobar(len(copias) == 1 and copias[0]["id"] == nueva, "una carpeta que Drive creo pero cuya respuesta se perdio NO se duplica: se usa la que ya quedo", f"{len(copias)} copia(s)")
    cli.permitir(nueva)
    est.fallas["crea_y_corta"] = 1
    r = cli.guardar_texto(nueva, "REF3_analisis.json", b'{"idioma": "pt"}', "application/json")
    copias = [f for f in est.hijos(nueva) if f["name"] == "REF3_analisis.json"]
    comprobar(len(copias) == 1 and copias[0]["content"] == b'{"idioma": "pt"}' and r["id"] == copias[0]["id"], "y un archivo de texto tampoco (el caso real del 2026-10-21)", f"{len(copias)} copia(s)")
    est.fallas["http_500"] = 1
    cli.guardar_texto(nueva, "otro.txt", b"hola")
    comprobar(len([f for f in est.hijos(nueva) if f["name"] == "otro.txt"]) == 1, "si Drive contesta 500 SIN crear nada, se repite y queda una sola copia")
    est.fallas["http_429"] = 2
    cli.crear_carpeta(ID, "Otra")
    comprobar(len([f for f in est.hijos(ID) if f["name"] == "Otra"]) == 1, "y con «demasiadas llamadas» (429) dos veces, tambien una sola carpeta")
    est.tokens_validos.clear()           # el permiso que el cliente cree vigente ya no lo es: la primera peticion es el POST de crear
    tokens_antes = est.n_tokens
    otra = cli.crear_carpeta(ID, "Tras401")
    comprobar(est.n_tokens == tokens_antes + 1 and [f["id"] for f in est.hijos(ID) if f["name"] == "Tras401"] == [otra], "con el permiso vencido (401) en la peticion de crear: se renueva y se crea UNA vez")
    est.fallas.clear()
    est.fallas["corta_sin_crear"] = True
    n_antes, llamadas = len(est.archivos), len(est.log)
    try:
        cli.guardar_texto(nueva, "nunca.txt", b"x")
        comprobar(False, "si Drive nunca contesta ni crea, termina con error")
    except d.ErrorDrive as e:
        comprobar(len(est.archivos) == n_antes and len(est.log) - llamadas < 40 and not isinstance(e, d.ErrorPermiso),
                  "si Drive nunca contesta ni crea, termina con un error normal tras unos pocos intentos (no queda nada a medias)", str(e))
    est.fallas.clear()
    comprobar(not any(m == "DELETE" for m, _, _ in est.log), "y en todo esto no hubo ni un DELETE")

    print("Subida de un video")
    d.TROZO = 262144 * 2              # 512 KiB: asi un archivo de ~1,3 MB va en 3 trozos
    est.reiniciar()
    cli = nuevo_drive(d, est)
    est.poner("OFERTA", MIME_CARPETA, ["raiz"], id_fijo=ID)
    cli.permitir(ID)
    B = cli.hallar_o_crear_carpeta(ID, "B-rolls")
    cli.permitir(B)
    chico = archivo_de(tmp, "chico.mp4", 40_000)
    meta = cli.subir_archivo(chico, B, "tt_chico.mp4")
    sube = est.archivos[meta["id"]]
    comprobar(sube["content"] == chico.read_bytes() and sube["parents"] == [B] and sube["name"] == "tt_chico.mp4" and sube["mimeType"] == "video/mp4",
              "un archivo chico llega igual, con su nombre, su tipo y en B-rolls")
    grande = archivo_de(tmp, "grande.mp4", 1_300_000)
    est.log.clear()
    meta = cli.subir_archivo(grande, B, "tt_grande.mp4")
    trozos = [x for x in est.log if x[0] == "PUT"]
    comprobar(est.archivos[meta["id"]]["content"] == grande.read_bytes() and len(trozos) == 3, "uno grande va en 3 trozos y llega igual", f"{len(trozos)} trozos")
    comprobar(cli.bytes_subidos == 40_000 + 1_300_000, "cuenta los bytes subidos")

    def con_falla(nombre_falla, valor, nombre_prueba, esperar_pedidos=None, tam=1_300_000, **kw):
        est.fallas.clear()
        est.fallas[nombre_falla] = valor
        for k, v in kw.items():
            est.fallas[k] = v
        antes = len([f for f in est.archivos.values() if f["name"] == "reanuda.mp4"])
        ruta_ = archivo_de(tmp, "reanuda.mp4", tam)
        c = nuevo_drive(d, est)
        c.permitir(B)
        est.log.clear()
        m = c.subir_archivo(ruta_, B, "reanuda.mp4")
        copias = [f for f in est.archivos.values() if f["name"] == "reanuda.mp4"]
        comprobar(est.archivos[m["id"]]["content"] == ruta_.read_bytes() and len(copias) == antes + 1, nombre_prueba, f"{len(copias) - antes} copia(s)")
        est.fallas.clear()
        return c

    con_falla("chunk_500_sin_guardar", 1, "un error 500 en un trozo (que no se guardo): sigue donde iba y no duplica")
    con_falla("chunk_500_guardando", 1, "un 500 DESPUES de guardar el trozo: pregunta cuanto tiene Drive y no lo manda dos veces")
    con_falla("corte_sin_guardar", 1, "se corta la conexion antes de guardar: retoma")
    con_falla("corte_guardando", 1, "se corta la conexion despues de guardar (la respuesta se perdio): retoma sin duplicar")
    con_falla("parcial", True, "Drive guarda solo la mitad de un trozo: el siguiente sale desde donde Drive dice", tam=2_000_000)
    c = con_falla("sesion_perdida", 1, "Drive olvida la sesion de subida: abre otra y sube el archivo completo")
    sesiones = [x for x in est.log if x[0] == "POST" and x[1] == "/upload/drive/v3/files"]
    comprobar(len(sesiones) == 2, "y fueron dos sesiones (una perdida y una buena)", str([(x[0], x[1]) for x in est.log]))
    con_falla("http_500", 2, "el 500 al abrir la sesion se reintenta")
    con_falla("limite_tasa", 2, "el limite de llamadas (403 rateLimitExceeded) se espera y se reintenta")
    con_falla("http_429", 2, "un 429 se espera y se reintenta")
    con_falla("sin_tamano_en_respuesta", True, "si Drive no dice el tamano en la respuesta, se le pregunta aparte y se comprueba", tam=50_000)

    est.fallas.clear()
    for falla, texto in (("md5_malo", "MD5"), ("tamano_malo", "tamano")):
        est.fallas[falla] = True
        try:
            cli.subir_archivo(archivo_de(tmp, "mal.mp4", 30_000), B, f"mal_{falla}.mp4")
            comprobar(False, f"Drive guarda distinto ({texto}): NO se da por subido")
        except d.ErrorDrive as e:
            comprobar(texto in str(e) and "no se da por subido" in str(e), f"Drive guarda distinto ({texto}): NO se da por subido", str(e))
        est.fallas.clear()
    try:
        cli.subir_archivo(archivo_de(tmp, "vacio.mp4", 0), B, "vacio.mp4")
        comprobar(False, "un archivo vacio se rechaza")
    except d.ErrorDrive as e:
        comprobar("vacio" in str(e), "un archivo vacio se rechaza", str(e))
    est.fallas["ubicacion_ajena"] = True
    try:
        cli.subir_archivo(archivo_de(tmp, "ajena.mp4", 30_000), B, "ajena.mp4")
        comprobar(False, "una direccion de subida de otro sitio se rechaza")
    except d.ErrorDrive as e:
        comprobar("direccion de subida" in str(e), "una direccion de subida de otro sitio se rechaza (nada se manda a un tercero)", str(e))
    est.fallas.clear()
    est.fallas["sesion_perdida"] = 9
    try:
        cli.subir_archivo(archivo_de(tmp, "siempre.mp4", 700_000), B, "siempre.mp4")
        comprobar(False, "si la sesion se pierde siempre, termina con un error claro")
    except d.ErrorDrive as e:
        comprobar("no pude terminar de subir" in str(e), "si la sesion se pierde siempre, termina con un error claro", str(e))
    est.fallas.clear()
    d.TROZO = 8 * 1024 * 1024

    print("Errores que se explican")
    est.fallas["api_apagada"] = True
    try:
        nuevo_drive(d, est).carpeta(ID)
        comprobar(False, "API de Drive apagada: se explica")
    except d.ErrorPermiso as e:
        comprobar("APAGADA" in str(e) and "console.cloud.google.com" in str(e), "API de Drive apagada: error de permiso con el enlace para encenderla", str(e))
    est.fallas.clear()
    est.fallas["scope_malo"] = True
    try:
        nuevo_drive(d, est).carpeta(ID)
        comprobar(False, "permiso insuficiente: se explica")
    except d.ErrorPermiso as e:
        comprobar("Drive completo" in str(e), "permiso insuficiente: dice que hace falta el de Drive completo", str(e))
    est.fallas.clear()
    est.fallas["sin_espacio"] = True
    try:
        c = nuevo_drive(d, est)
        c.permitir(B)
        c.subir_archivo(archivo_de(tmp, "lleno.mp4", 30_000), B, "lleno.mp4")
        comprobar(False, "Drive sin espacio: se explica")
    except d.ErrorPermiso as e:
        comprobar("espacio" in str(e), "Drive sin espacio: error de permiso (no se reintenta)", str(e))
    est.fallas.clear()
    est.fallas["http_500"] = 99
    try:
        nuevo_drive(d, est).carpeta(ID)
        comprobar(False, "Drive caido: termina con error")
    except d.ErrorDrive as e:
        comprobar("500" in str(e) and not isinstance(e, d.ErrorPermiso), "Drive caido (500 siempre): error normal tras los reintentos, no de permiso", str(e))
    est.fallas.clear()

    print("probar (linea de comandos)")
    est.reiniciar()
    est.poner("OFERTA Dinamicas", MIME_CARPETA, ["raiz"], id_fijo=ID)
    brolls = est.poner("B-rolls", MIME_CARPETA, [ID])
    est.poner("tt_1.mp4", "video/mp4", [brolls["id"]], b"x")
    cred_ruta = tmp / "secretos-google.json"
    cred_ruta.write_text(json.dumps(est.cred), encoding="utf-8")
    d.RUTA_CREDENCIALES = cred_ruta

    def correr(args):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            cod = d.probar(args)
        return cod, salida.getvalue()

    cod, txt = correr([f"https://drive.google.com/drive/folders/{ID}"])
    comprobar(cod == 0 and "dueno@prueba.example" in txt and "OFERTA Dinamicas" in txt and "1 archivo" in txt and "se puede escribir" in txt, "probar: cuenta, carpeta, B-rolls con su cantidad", txt)
    comprobar("SECRETO" not in txt and "REFRESCO" not in txt and "ya29" not in txt, "probar no imprime ninguna llave ni token")
    cod, txt = correr(["https://example.com/x"])
    comprobar(cod == 1 and "no reconozco" in txt, "probar: un enlace que no es de Drive se dice")
    est.fallas["revocado"] = True
    cod, txt = correr([ID])
    comprobar(cod == 4 and "SIN PERMISO" in txt, "probar: permiso revocado sale con codigo 4")
    est.fallas.clear()
    d.RUTA_CREDENCIALES = tmp / "no-existe.json"
    cod, txt = correr([ID])
    comprobar(cod == 4 and "Setup-DriveBroll" in txt, "probar: sin credenciales sale con codigo 4 y dice que hacer")


def main() -> None:
    configurar_consola()
    servidor, est = iniciar_drive_falso()
    os.environ["BROLL_DRIVE_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    d = importlib.import_module("drive_api")
    d = importlib.reload(d)
    comprobar(d.URL_API.startswith("http://127.0.0.1:") and d.URL_TOKEN.startswith("http://127.0.0.1:"), "la autoprueba habla con el Drive FALSO")
    os.environ["BROLL_DRIVE_URL"] = "https://evil.example.com"
    otro = importlib.reload(d)
    comprobar(otro.URL_API == "https://www.googleapis.com/drive/v3" and otro.URL_TOKEN == "https://oauth2.googleapis.com/token",
              "una direccion que no es de esta maquina se ignora (las llaves no pueden ir a otro lado)")
    os.environ["BROLL_DRIVE_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    d = importlib.reload(otro)
    with tempfile.TemporaryDirectory(prefix="autoprueba_drive_") as t:
        pruebas(d, est, Path(t))
    servidor.shutdown()
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON {len(FALLAS)} FALLA(S):")
        for f in FALLAS:
            print("  -", f)
        sys.exit(1)
    print("AUTOPRUEBA OK")


if __name__ == "__main__":
    main()
