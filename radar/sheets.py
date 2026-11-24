"""
sheets.py - acceso minimo a Google Sheets (API v4) usando solo `requests`.

Sin librerias de Google. Usa el archivo client_secret*.json de esta carpeta (lo lee el programa,
nunca se imprime) y guarda el permiso en token_sheets.json. Una sola vez:

    python radar.py --autorizar-sheets     (abre el navegador; tu aceptas el acceso a la planilla)

Permiso pedido: https://www.googleapis.com/auth/spreadsheets (leer y escribir celdas de tus planillas).
Para revocarlo: https://myaccount.google.com/permissions  (y borra token_sheets.json).
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import unicodedata
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from archivos import escribir_atomico

HERE = Path(os.environ.get("RADAR_DATOS") or Path(__file__).resolve().parent)   # carpeta de los DATOS (en el VPS /opt/radar-de-ofertas; el codigo vive aparte)
TOKEN_FILE = HERE / "token_sheets.json"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets"
DEFAULT_SHEET_ID = ""   # usa la variable SHEET_ID con el ID de TU planilla (ver radar/LEEME.md)


class SheetsError(Exception):
    pass


def available():
    """True si ya se autorizo el acceso (existe token_sheets.json)."""
    return TOKEN_FILE.exists()


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "").lower().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _client():
    files = sorted(HERE.glob("client_secret*.json"))
    if not files:
        raise SheetsError("No encuentro client_secret*.json en la carpeta del radar.")
    cfg = json.loads(files[0].read_text(encoding="utf-8"))
    c = cfg.get("installed") or cfg.get("web")
    if not c or "client_id" not in c:
        raise SheetsError("client_secret*.json no tiene el formato esperado (clave 'installed' o 'web').")
    return c["client_id"], c.get("client_secret", "")


def authorize(timeout=300):
    """Flujo OAuth de escritorio (loopback + PKCE). El usuario acepta en el navegador."""
    cid, secret = _client()
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h3>Listo. Puedes cerrar esta pestana y volver a la terminal.</h3>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    srv.timeout = 3
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent"})
    print("Abriendo el navegador para que autorices el acceso a la planilla.\n"
          f"Si no se abre solo, copia este enlace en el navegador:\n{url}\n", flush=True)
    webbrowser.open(url)
    deadline = time.time() + timeout
    while not got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not got:
        raise SheetsError(f"No recibi la autorizacion en {timeout}s. Vuelve a correr --autorizar-sheets.")
    if got.get("error"):
        raise SheetsError("Google devolvio: " + got["error"])
    if got.get("state") != state:
        raise SheetsError("La respuesta de Google no coincide con la solicitud (state). Intenta de nuevo.")
    r = requests.post(TOKEN_URL, data={
        "code": got["code"], "client_id": cid, "client_secret": secret, "code_verifier": verifier,
        "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=30)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "refresh_token" not in j:
        raise SheetsError("Google no entrego el permiso: " + str(j.get("error_description") or j.get("error") or r.status_code))
    escribir_atomico(TOKEN_FILE, json.dumps({
        "refresh_token": j["refresh_token"], "access_token": j.get("access_token"),
        "expires_at": time.time() + int(j.get("expires_in", 3600)), "scope": j.get("scope", SCOPE)}))
    print("Acceso autorizado y guardado en token_sheets.json (no lo compartas).")


def _access_token():
    if not TOKEN_FILE.exists():
        raise SheetsError("Falta autorizar el acceso: python radar.py --autorizar-sheets")
    t = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    if t.get("access_token") and t.get("expires_at", 0) > time.time() + 60:
        return t["access_token"]
    cid, secret = _client()
    r = requests.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret,
                                       "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        try:
            why = r.json().get("error", r.status_code)
        except ValueError:
            why = r.status_code
        raise SheetsError(f"No pude renovar el acceso a Google ({why}). Vuelve a correr --autorizar-sheets.")
    j = r.json()
    t["access_token"] = j["access_token"]
    t["expires_at"] = time.time() + int(j.get("expires_in", 3600))
    escribir_atomico(TOKEN_FILE, json.dumps(t))      # un corte a mitad no puede dejar sin el permiso de renovacion
    return t["access_token"]


class Sheet:
    def __init__(self, sheet_id=None, session=None):
        self.id = sheet_id or os.environ.get("SHEET_ID") or DEFAULT_SHEET_ID
        self.http = session or requests.Session()
        self._headers = {}

    def _req(self, method, path, **kw):
        for espera in (2, 6, 12, None):                  # 429 y 5xx de Google se reintentan con espera
            r = self.http.request(method, f"{API}/{self.id}{path}", timeout=45,
                                  headers={"Authorization": "Bearer " + _access_token()}, **kw)
            if (r.status_code == 429 or r.status_code >= 500) and espera is not None:
                time.sleep(espera)
                continue
            break
        if r.status_code >= 400:
            try:
                msg = r.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                msg = r.text[:200]
            raise SheetsError(f"Google Sheets {r.status_code}: {msg[:300]}")
        return r.json()

    @staticmethod
    def _a1(tab, cells=""):
        return urllib.parse.quote("'" + tab.replace("'", "''") + "'" + (f"!{cells}" if cells else ""), safe="")

    def read(self, tab):
        """Todas las filas de la pestana (rellenadas al ancho del encabezado)."""
        vals = self._req("GET", f"/values/{self._a1(tab)}", params={"majorDimension": "ROWS"}).get("values", [])
        width = max((len(r) for r in vals), default=0)
        return [r + [""] * (width - len(r)) for r in vals]

    def read_dicts(self, tab):
        """Filas como dicts {encabezado: valor}; '_row' es el numero de fila en la planilla."""
        vals = self.read(tab)
        if not vals:
            return []
        head = vals[0]
        self._headers[tab] = head
        return [dict(zip(head, r), _row=i + 2) for i, r in enumerate(vals[1:]) if any(str(c).strip() for c in r)]

    def headers(self, tab):
        if tab not in self._headers:
            vals = self._req("GET", f"/values/{self._a1(tab, '1:1')}").get("values", [[]])
            self._headers[tab] = vals[0] if vals else []
        return self._headers[tab]

    def check_headers(self, tab, required):
        """Falla (sin escribir nada) si a la pestana le falta alguno de los encabezados esperados."""
        have = {fold(h) for h in self.headers(tab)}
        missing = [c for c in required if fold(c) not in have]
        if missing:
            raise SheetsError(f"A la pestana {tab} le faltan los encabezados: {', '.join(missing)}")

    def set_dropdown(self, tab, header, options):
        """Menu desplegable en la columna `header` (desde la fila 2 hasta el final de la cuadricula).
        No estricto: avisa si el valor no esta en la lista pero no lo rechaza, y no cambia ningun valor."""
        head = [fold(h) for h in self.headers(tab)]
        if fold(header) not in head:
            raise SheetsError(f"La pestana {tab} no tiene la columna {header}")
        col = head.index(fold(header))
        meta = self._req("GET", "", params={"fields": "sheets.properties(sheetId,title,gridProperties.rowCount)"})
        props = next((s["properties"] for s in meta.get("sheets", []) if s["properties"]["title"] == tab), None)
        if not props:
            raise SheetsError(f"No existe la pestana {tab}")
        self._req("POST", ":batchUpdate", json={"requests": [{"setDataValidation": {
            "range": {"sheetId": props["sheetId"], "startRowIndex": 1,
                      "endRowIndex": props.get("gridProperties", {}).get("rowCount", 1000),
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": o} for o in options]},
                     "showCustomUi": True, "strict": False}}}]})

    def asegurar_columnas(self, tab, nombres):
        """Agrega AL FINAL de la fila de encabezados las columnas de `nombres` que falten (no cambia ni mueve nada de lo que ya existe) y deja todas ellas en formato «Texto sin formato»
        desde la fila 2: Google Sheets guarda solo 15 cifras de un numero, asi que un ID de 16 o 17 digitos pegado a mano en una columna normal pierde digitos sin avisar.
        Devuelve la lista de nombres que agrego."""
        head = self.headers(tab)
        tiene = {fold(h) for h in head}
        nuevos = [n for n in nombres if fold(n) not in tiene]
        meta = self._req("GET", "", params={"fields": "sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))"})
        props = next((s["properties"] for s in meta.get("sheets", []) if s["properties"]["title"] == tab), None)
        if not props:
            raise SheetsError(f"No existe la pestana {tab}")
        grid = props.get("gridProperties", {})
        necesarias = len(head) + len(nuevos)
        if necesarias > grid.get("columnCount", necesarias):          # la cuadricula no tiene sitio: values.update no la amplia
            self._req("POST", ":batchUpdate", json={"requests": [{"appendDimension": {
                "sheetId": props["sheetId"], "dimension": "COLUMNS", "length": necesarias - grid["columnCount"]}}]})
        if nuevos:
            self._req("PUT", f"/values/{self._a1(tab, col_letter(len(head)) + '1')}", params={"valueInputOption": "RAW"}, json={"values": [nuevos]})
            self._headers.pop(tab, None)
        orden = [fold(h) for h in self.headers(tab)]
        self._req("POST", ":batchUpdate", json={"requests": [{"repeatCell": {
            "range": {"sheetId": props["sheetId"], "startRowIndex": 1, "endRowIndex": grid.get("rowCount", 1000),
                      "startColumnIndex": orden.index(fold(n)), "endColumnIndex": orden.index(fold(n)) + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}}, "fields": "userEnteredFormat.numberFormat"}} for n in nombres]})
        return nuevos

    def append_dict(self, tab, row):
        """Agrega UNA fila al final de la pestana, ubicando cada valor por el nombre de su columna.
        No toca filas existentes (RAW: nada se interpreta como formula)."""
        head = self.headers(tab)
        by_name = {fold(k): v for k, v in row.items()}
        values = [[by_name.get(fold(h), "") for h in head]]
        self._req("POST", f"/values/{self._a1(tab, 'A1')}:append",
                  params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, json={"values": values})

    def append_dicts(self, tab, rows):
        """Agrega VARIAS filas al final en una sola peticion (una sola llamada a la API, no una por fila)."""
        if not rows:
            return
        head = self.headers(tab)
        values = []
        for row in rows:
            by_name = {fold(k): v for k, v in row.items()}
            values.append([by_name.get(fold(h), "") for h in head])
        self._req("POST", f"/values/{self._a1(tab, 'A1')}:append",
                  params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, json={"values": values})

    def borrar_filas(self, tab, filas):
        """Borra esas filas (numeros de fila de la planilla, 2 en adelante) de la pestana, en UNA peticion, de abajo hacia arriba para que los numeros no
        se corran mientras se borra. Nunca la fila 1 (encabezados)."""
        filas = sorted({int(f) for f in filas}, reverse=True)
        if not filas:
            return
        if filas[-1] < 2:
            raise SheetsError("no se borra la fila de encabezados")
        meta = self._req("GET", "", params={"fields": "sheets.properties(sheetId,title)"})
        props = next((s["properties"] for s in meta.get("sheets", []) if s["properties"]["title"] == tab), None)
        if not props:
            raise SheetsError(f"No existe la pestana {tab}")
        self._req("POST", ":batchUpdate", json={"requests": [{"deleteDimension": {"range": {
            "sheetId": props["sheetId"], "dimension": "ROWS", "startIndex": f - 1, "endIndex": f}}} for f in filas]})

    def ensure_tab(self, tab, headers):
        """Crea la pestana con su encabezado si no existe. No toca ninguna pestana existente. True si la creo."""
        meta = self._req("GET", "", params={"fields": "sheets.properties.title"})
        if any(s["properties"]["title"] == tab for s in meta.get("sheets", [])):
            return False
        self._req("POST", ":batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": tab}}}]})
        self._req("PUT", f"/values/{self._a1(tab, 'A1')}", params={"valueInputOption": "RAW"},
                  json={"values": [headers]})
        self._headers.pop(tab, None)
        return True

    def protection(self, tab, headers):
        """{encabezado: (protegida, editores)} para cada columna pedida. Una columna esta protegida solo si TODA ella (desde
        la fila 1, que lleva el nombre, hasta el final) cae bajo un rango protegido que no sea solo un aviso y que no tenga
        excepciones sobre esa columna; una hoja entera protegida tambien cuenta. `editores` junta a quienes pueden
        escribir ahi: correos, y "@grupo:..." / "@dominio" si la proteccion se abre a un grupo o a todo el dominio.
        Si algun encabezado aparece mas de una vez (por mayusculas, tildes o espacios) lanza SheetsError: leer y proteger
        no se pondrian de acuerdo sobre cual es la columna."""
        head = [fold(h) for h in self.headers(tab)]
        cols = {}
        for h in headers:
            n = head.count(fold(h))
            if n != 1:
                raise SheetsError(f"La pestana {tab} tiene {n} columnas '{h}' (debe tener exactamente una)")
            cols[h] = head.index(fold(h))
        meta = self._req("GET", "", params={"fields": "sheets(properties(sheetId,title),"
                                                      "protectedRanges(range,unprotectedRanges,warningOnly,editors))"})
        hoja = next((s for s in meta.get("sheets", []) if s.get("properties", {}).get("title") == tab), None)
        if hoja is None:
            raise SheetsError(f"No existe la pestana {tab}")
        out = {}
        for h, col in cols.items():
            cubierta, eds = False, set()
            for pr in hoja.get("protectedRanges", []):
                if pr.get("warningOnly") or not _cubre_columna(pr.get("range", {}), col):
                    continue
                if any(_toca_columna(u, col) for u in pr.get("unprotectedRanges", [])):
                    continue                                   # "protegida excepto ..." sobre esta columna: no cuenta
                cubierta = True
                ed = pr.get("editors") or {}
                eds.update(str(u) for u in ed.get("users", []))
                eds.update("@grupo:" + str(g) for g in ed.get("groups", []))
                if ed.get("domainUsersCanEdit"):
                    eds.add("@dominio")
            out[h] = (cubierta, sorted(eds))
        return out

    def column_protection(self, tab, header):
        """(protegida, editores) de una sola columna (ver `protection`)."""
        return self.protection(tab, [header])[header]

    def update_cells(self, tab, cells):
        """cells = {'F5': 3, 'H5': '11/10/2026'}. Escribe solo esas celdas."""
        if not cells:
            return
        data = [{"range": "'" + tab.replace("'", "''") + "'!" + a1, "values": [[v]]} for a1, v in cells.items()]
        self._req("POST", "/values:batchUpdate", json={"valueInputOption": "RAW", "data": data})


_SIN_FIN = 10 ** 9


def _cubre_columna(r, col):
    """El rango protegido cubre la columna `col` (base 0) de la fila 1 al final de la hoja (la API omite los ceros)."""
    return (r.get("startRowIndex", 0) == 0 and "endRowIndex" not in r
            and r.get("startColumnIndex", 0) <= col < r.get("endColumnIndex", _SIN_FIN))


def _toca_columna(r, col):
    """El rango (una excepcion) toca la columna `col` en alguna fila."""
    return r.get("startColumnIndex", 0) <= col < r.get("endColumnIndex", _SIN_FIN)


def col_letter(idx):
    """Indice base 0 -> letra de columna (0 -> A, 26 -> AA)."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s
