"""Espejo local de Drive para el corredor de anuncios: lo que en el PC hace «Google Drive para escritorio» (la carpeta G:\\Mi unidad\\MI NEGOCIO\\OFERTAS LATAM), pero en el servidor y con la API.

Los scripts del editor (fabrica.py traer / entregar, broll_tiktok.py recoger) ya saben trabajar contra esa carpeta; el corredor solo necesita dos movimientos, y los dos son estrechos:

  BAJAR  (Drive -> disco, SOLO LECTURA): de la carpeta de una oferta trae  B-rolls/  (los videos de TikTok y su fuentes.csv)  y  REFERENCIA/  (la vigente: la de numero mas alto), y de
         BIBLIOTECA MUSICAL/ las pistas de fondo. Lo que ya esta con el mismo tamano no se vuelve a bajar; se baja a un .parte y se mueve cuando tamano y MD5 coinciden; una conexion que se corta se
         retoma donde iba. De B-rolls/ se quita del disco lo que el equipo borro de Drive (borrar un video ahi es como el equipo dice «este no sirve»).
  SUBIR  (disco -> Drive, SOLO CREAR): deja el video terminado en  <oferta>/ANUNCIOS/ . Nunca pisa un archivo que ya esta (si hay otro con el mismo nombre y distinto contenido, sube el nuevo como _v2,
         _v3...), nunca borra, mueve ni comparte nada en Drive, y comprueba que Drive lo guardo con el mismo tamano y MD5.

Usa drive_api.Drive (la misma «cerca» de escritura: solo se escribe en las carpetas que se declaran con permitir(); aqui solo ANUNCIOS y, un instante, la carpeta de la oferta para crearla).
El permiso de Google vive en el servidor (secretos/google.json) y este modulo nunca lo imprime. Los nombres de archivo que vienen de Drive son texto de terceros: se limpian antes de tocar el disco.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drive_api as da  # noqa: E402

CARPETA_BROLLS = da.CARPETA_BROLLS          # «B-rolls»
CARPETA_ANUNCIOS = "ANUNCIOS"
CARPETA_MUSICA = "BIBLIOTECA MUSICAL"
CARPETA_AVATAR = "AVATAR"                     # MI NEGOCIO › OFERTAS LATAM › <OFERTA> › AVATAR: de 1 a 3 fotos autorizadas del avatar y su avatar.json (solo se LEE)
EXT_FOTO = {".jpg", ".jpeg", ".png", ".webp"}
EXT_AVATAR = EXT_FOTO | {".json"}
EXT_ENTREGABLES = {".pdf"} | EXT_FOTO          # los PDF ya hechos de una oferta y, si la carpeta lo trae, el mockup (una imagen)
MAX_FICHA = 64 * 1024                         # avatar.json pesa unos cientos de bytes
RE_REFERENCIA = re.compile(r"^REFERENCIA(?: (\d{1,2}))?$")
EXT_VIDEO = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
EXT_BAJAR = EXT_VIDEO | {".csv", ".txt", ".json", ".md", ".jpg", ".jpeg", ".png", ".mp3", ".wav", ".m4a"}
MAX_ARCHIVO = 400 * 1024 * 1024             # un archivo de mas de 400 MB no se baja (un b-roll de TikTok pesa 1 a 15 MB)
MAX_ENTREGABLES = 400 * 1024 * 1024         # todos los PDF ya hechos de una oferta juntos (ocho ebooks pesan 50 a 80 MB)
MAX_REINTENTOS = 6
ESPERAS = (2, 5, 12, 30, 60, 60)


class ErrorEspejo(Exception):
    """El espejo no pudo hacer lo que se le pidio. El mensaje esta escrito para una persona."""


def clave_nombre(texto: object) -> str:
    """Mayusculas, sin acentos y solo letras y numeros: como se comparan los nombres de las carpetas de las ofertas (igual que fabrica_broll.clave_nombre y fabrica.py)."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"[^A-Z0-9]+", "", t)


def nombre_seguro(nombre: object) -> str | None:
    """Un nombre de archivo o carpeta de Drive para usarlo en el disco, o None si no sirve: sin separadores de ruta, sin «..», sin caracteres de control, sin empezar por punto."""
    n = unicodedata.normalize("NFC", str(nombre or "")).strip()
    if not n or n in (".", "..") or n.startswith(".") or len(n.encode("utf-8")) > 200:
        return None
    if re.search(r"[\x00-\x1f\x7f/\\:*?\"<>|]", n) or ".." in n:
        return None
    return n


def enlace_archivo(archivo_id: str) -> str:
    return f"https://drive.google.com/file/d/{archivo_id}/view"


def enlace_carpeta(carpeta_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{carpeta_id}"


def _es_carpeta(f: dict) -> bool:
    return f.get("mimeType") == da.MIME_CARPETA


def _es_documento_de_google(f: dict) -> bool:
    return str(f.get("mimeType") or "").startswith("application/vnd.google-apps")


class Espejo:
    """El espejo de una carpeta OFERTAS LATAM de Drive en `raiz_local`.
    drive      un drive_api.Drive (o uno de mentira en las pruebas)
    raiz_id    el id de la carpeta OFERTAS LATAM
    raiz_local donde vive el espejo (la carpeta que se le da a los scripts del editor como OFERTAS_LATAM_DIR)"""

    def __init__(self, drive, raiz_id: str, raiz_local: Path, registrar=print, dormir=time.sleep, reloj=time.monotonic):
        self.drive = drive
        self.raiz_id = raiz_id
        self.raiz_local = Path(raiz_local)
        self.registrar = registrar
        self.dormir = dormir
        self.reloj = reloj
        self.bytes_bajados = 0
        self.bytes_subidos = 0

    # ------------------------------------------------------------------ hallar
    def hallar_oferta(self, nombre: str, slug: str) -> dict | None:
        """La carpeta de la oferta dentro de OFERTAS LATAM, buscada por nombre normalizado (el del proyecto o el del slug). {id, name}, o None si no existe (la crea la vigilancia de la Fabrica)."""
        buscadas = {c for c in (clave_nombre(nombre), clave_nombre(slug)) if c}
        for f in self.drive.listar_hijos(self.raiz_id):
            if _es_carpeta(f) and clave_nombre(f.get("name")) in buscadas and nombre_seguro(f.get("name")):
                return {"id": f["id"], "name": f["name"]}
        return None

    def subcarpeta(self, oferta: dict, nombre: str) -> str | None:
        return self.drive.hallar_carpeta(oferta["id"], nombre)[0]

    def contar_brolls(self, oferta: dict) -> int:
        """Cuantos videos de b-roll (tt_*.mp4) tiene la oferta en Drive, sin bajar nada."""
        b = self.subcarpeta(oferta, CARPETA_BROLLS)
        if not b:
            return 0
        return sum(1 for f in self.drive.listar_hijos(b) if not _es_carpeta(f) and re.match(r"^tt_\d{10,25}\.", str(f.get("name") or "")) and Path(str(f.get("name"))).suffix.lower() in EXT_VIDEO)

    def leer_avatar(self, oferta: dict) -> dict:
        """Lo que hay en AVATAR/ de la oferta SIN bajar las fotos: {existe, fotos: [nombres], ficha: dict | None}. Solo avatar.json se lee (pesa unos bytes), a un archivo temporal que se borra.
        Una ficha que no es un objeto JSON, o pesa demasiado, queda como `ficha` = {"_error": ...} (el que decide ve que no sirve)."""
        carpeta = self.subcarpeta(oferta, CARPETA_AVATAR)
        if not carpeta:
            return {"existe": False, "fotos": [], "ficha": None}
        archivos = [f for f in self.drive.listar_hijos(carpeta) if not _es_carpeta(f) and not _es_documento_de_google(f)]
        fotos = sorted({n for n in (nombre_seguro(f.get("name")) for f in archivos) if n and Path(n).suffix.lower() in EXT_FOTO})
        ficha = None
        jf = next((f for f in archivos if f.get("name") == "avatar.json"), None)
        if jf is not None:
            try:
                if int(jf.get("size") or 0) > MAX_FICHA:
                    ficha = {"_error": "avatar.json pesa demasiado"}
                else:
                    tmp = self.raiz_local / ".tmp" / "avatar.json"
                    tmp.unlink(missing_ok=True)
                    self._bajar_archivo(jf, tmp, None)
                    try:
                        ficha = json.loads(tmp.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        ficha = {"_error": "avatar.json no se pudo leer"}
                    finally:
                        tmp.unlink(missing_ok=True)
            except ErrorEspejo as e:
                ficha = {"_error": str(e)[:100]}
        return {"existe": True, "fotos": fotos, "ficha": ficha}

    def referencia_vigente(self, oferta: dict) -> tuple[str, str] | None:
        """(id, nombre) de la carpeta REFERENCIA de numero mas alto (REFERENCIA, REFERENCIA 2...), o None."""
        mejor = None
        for f in self.drive.listar_hijos(oferta["id"]):
            m = RE_REFERENCIA.match(str(f.get("name") or ""))
            if _es_carpeta(f) and m and (mejor is None or int(m.group(1) or 1) > mejor[0]):
                mejor = (int(m.group(1) or 1), f["id"], f["name"])
        return (mejor[1], mejor[2]) if mejor else None

    # ------------------------------------------------------------------ bajar
    def _bajar_archivo(self, f: dict, destino: Path, plazo: float | None) -> str:
        """Baja UN archivo a `destino`. 'bajado' o 'ya estaba'. Retoma un .parte; comprueba tamano y MD5; ante un corte de red espera y sigue donde iba."""
        import requests

        try:
            esperado = int(f["size"])
        except (KeyError, TypeError, ValueError):
            raise ErrorEspejo(f"Drive no dijo cuanto pesa {f.get('name')}: no se baja") from None
        if esperado <= 0 or esperado > MAX_ARCHIVO:
            raise ErrorEspejo(f"{f.get('name')} pesa {esperado} bytes (el tope es {MAX_ARCHIVO}): no se baja")
        if destino.is_file() and destino.stat().st_size == esperado:
            return "ya estaba"
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_name(destino.name + ".parte")
        pos = parcial.stat().st_size if parcial.is_file() else 0
        if pos > esperado:
            parcial.unlink()
            pos = 0
        fallos = 0
        while True:
            if plazo is not None and self.reloj() > plazo:
                raise ErrorEspejo("se acabo el tiempo para bajar de Drive")
            if pos >= esperado:
                break
            try:
                cab = {"Range": f"bytes={pos}-"} if pos else {}
                r = self.drive._pedir("GET", f"{da.URL_API}/files/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"}, headers=cab, stream=True, timeout=(15, 120))
                if r.status_code == 416:
                    break
                if r.status_code not in (200, 206):
                    raise self.drive._explicar(r, f"bajar {f.get('name')}")
                if r.status_code == 200 and pos:
                    pos = 0                         # el servidor no entendio el rango: se empieza de cero
                with parcial.open("ab" if pos else "wb") as salida:
                    for trozo in r.iter_content(1 << 20):
                        if not trozo:
                            continue
                        salida.write(trozo)
                        pos += len(trozo)
                        if plazo is not None and self.reloj() > plazo:
                            raise ErrorEspejo("se acabo el tiempo para bajar de Drive")
                fallos = 0
            except (requests.RequestException, da.ErrorDrive) as e:
                if isinstance(e, da.ErrorPermiso):
                    raise
                fallos += 1
                if fallos > MAX_REINTENTOS:
                    raise ErrorEspejo(f"la bajada de {f.get('name')} se corta una y otra vez ({e.__class__.__name__})") from None
                self.dormir(ESPERAS[min(fallos - 1, len(ESPERAS) - 1)])
                pos = parcial.stat().st_size if parcial.is_file() else 0
        tam = parcial.stat().st_size if parcial.is_file() else 0
        if tam != esperado:
            parcial.unlink(missing_ok=True)
            raise ErrorEspejo(f"{f.get('name')} llego con {tam} bytes y Drive dice {esperado}: no se guarda")
        if f.get("md5Checksum") and da._md5(parcial).lower() != str(f["md5Checksum"]).lower():
            parcial.unlink(missing_ok=True)
            raise ErrorEspejo(f"{f.get('name')} llego con otro contenido (el MD5 no coincide): no se guarda")
        os.replace(parcial, destino)
        self.bytes_bajados += esperado
        return "bajado"

    def bajar_carpeta(self, carpeta_id: str, destino: Path, *, ext: set[str] = EXT_BAJAR, tope_bytes: int = 3 * 1024 ** 3, plazo: float | None = None, quitar_sobrantes: bool = False) -> dict:
        """Espeja los ARCHIVOS de una carpeta de Drive (sin entrar en sus subcarpetas) en `destino`. Con quitar_sobrantes, borra del DISCO lo que ya no esta en Drive (nunca toca Drive)."""
        archivos = [f for f in self.drive.listar_hijos(carpeta_id) if not _es_carpeta(f) and not _es_documento_de_google(f)]
        destino.mkdir(parents=True, exist_ok=True)
        ahora: dict[str, dict] = {}
        for f in archivos:
            n = nombre_seguro(f.get("name"))
            if n is None or Path(n).suffix.lower() not in ext:
                continue
            if n in ahora:
                self.registrar(f"  AVISO: hay dos archivos llamados {n} en la carpeta de Drive; se usa el mas antiguo")
                continue
            ahora[n] = f
        res = {"bajados": 0, "ya_estaban": 0, "bytes": 0, "quitados": 0, "fallos": [], "nombres": sorted(ahora)}
        total = 0
        for n, f in sorted(ahora.items()):
            if total + int(f.get("size") or 0) > tope_bytes:
                res["fallos"].append(f"{n}: pasaria del tope de {tope_bytes // 1048576} MB de esta corrida")
                continue
            try:
                que = self._bajar_archivo(f, destino / n, plazo)
            except da.ErrorPermiso:
                raise
            except (ErrorEspejo, da.ErrorDrive) as e:
                res["fallos"].append(f"{n}: {e}")
                if "se acabo el tiempo" in str(e):
                    break
                continue
            total += int(f.get("size") or 0)
            if que == "bajado":
                res["bajados"] += 1
                res["bytes"] += int(f["size"])
            else:
                res["ya_estaban"] += 1
        if quitar_sobrantes and not res["fallos"]:
            for p in destino.iterdir():
                if p.is_file() and not p.name.endswith(".parte") and p.name not in ahora:
                    p.unlink()
                    res["quitados"] += 1
                    self.registrar(f"  quitado del disco {p.name}: ya no esta en Drive (el equipo lo borro)")
        return res

    def carpeta_de_entregables(self, oferta: dict, carpeta_id: str) -> dict | None:
        """La carpeta de Drive con los PDF que ya vienen hechos (el enlace que el equipo pego en la Fabrica), SOLO si es la carpeta de la oferta o una de sus subcarpetas directas: {id, name}, o None.
        El enlace lo escribe una persona del equipo en el dash; sin esta cerca podria apuntar a cualquier carpeta que el permiso de Google del servidor pueda leer (y sus paginas acabarian en un anuncio)."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,80}", str(carpeta_id or "")):
            return None
        if carpeta_id == oferta["id"]:
            return {"id": oferta["id"], "name": oferta["name"]}
        for f in self.drive.listar_hijos(oferta["id"]):
            if _es_carpeta(f) and f.get("id") == carpeta_id:
                return {"id": f["id"], "name": str(f.get("name") or "")}
        return None

    def bajar_entregables(self, carpeta_id: str, destino: Path, *, plazo: float | None = None) -> dict:
        """Los entregables ya hechos de la oferta: SOLO LECTURA, los .pdf y las imagenes (el mockup), sin entrar en subcarpetas; lo que el equipo quito de Drive se quita tambien del disco."""
        return self.bajar_carpeta(carpeta_id, destino, ext=EXT_ENTREGABLES, tope_bytes=MAX_ENTREGABLES, plazo=plazo, quitar_sobrantes=True)

    def bajar_oferta(self, oferta: dict, *, plazo: float | None = None, con_avatar: bool = False, con_brolls: bool = True) -> dict:
        """B-rolls/ y la REFERENCIA vigente de la oferta (y AVATAR/ si se pide: solo fotos y avatar.json), en  <raiz_local>/<nombre de la carpeta>/ . Un anuncio de avatar no usa b-roll: con
        `con_brolls=False` no se baja (ni se espera a que Drive deje bajarlos). Devuelve un resumen con la ruta local de la oferta."""
        nombre = nombre_seguro(oferta.get("name"))
        if nombre is None:
            raise ErrorEspejo("el nombre de la carpeta de la oferta en Drive no sirve para el disco")
        local = self.raiz_local / nombre
        local.mkdir(parents=True, exist_ok=True)
        resumen: dict = {"oferta": nombre, "local": str(local), "carpetas": {}}
        b = self.subcarpeta(oferta, CARPETA_BROLLS) if con_brolls else None
        if b:
            resumen["carpetas"][CARPETA_BROLLS] = self.bajar_carpeta(b, local / CARPETA_BROLLS, ext=EXT_VIDEO | {".csv", ".txt"}, plazo=plazo, quitar_sobrantes=True)
        ref = self.referencia_vigente(oferta)
        if ref:
            n = nombre_seguro(ref[1]) or "REFERENCIA"
            resumen["carpetas"][n] = self.bajar_carpeta(ref[0], local / n, ext={".mp4", ".mov", ".txt", ".json", ".jpg"}, plazo=plazo)
        if con_avatar:
            a = self.subcarpeta(oferta, CARPETA_AVATAR)
            if a:           # quitar_sobrantes: si el equipo saca una foto de Drive (retira la autorizacion), tambien se va de aqui
                resumen["carpetas"][CARPETA_AVATAR] = self.bajar_carpeta(a, local / CARPETA_AVATAR, ext=EXT_AVATAR, plazo=plazo, quitar_sobrantes=True)
        resumen["bytes"] = sum(c["bytes"] for c in resumen["carpetas"].values())
        resumen["fallos"] = [f"{k}: {x}" for k, c in resumen["carpetas"].items() for x in c["fallos"]]
        return resumen

    def bajar_musica(self, destino: Path, *, plazo: float | None = None) -> dict:
        """Las pistas de MI NEGOCIO > BIBLIOTECA MUSICAL > <estilo>/*.mp3 en `destino` (public/musica/biblioteca). Nunca quita nada del disco. Sin biblioteca en Drive: sin musica."""
        golden = (self.drive.datos_de(self.raiz_id).get("parents") or [None])[0]
        if not golden:
            return {"estilos": 0, "bajados": 0, "nota": "no se pudo ubicar MI NEGOCIO"}
        musica = self.drive.hallar_carpeta(golden, CARPETA_MUSICA)[0]
        if not musica:
            return {"estilos": 0, "bajados": 0, "nota": "no hay BIBLIOTECA MUSICAL en Drive"}
        res = {"estilos": 0, "bajados": 0, "ya_estaban": 0, "fallos": []}
        for f in self.drive.listar_hijos(musica):
            n = nombre_seguro(f.get("name"))
            if not _es_carpeta(f) or n is None:
                continue
            r = self.bajar_carpeta(f["id"], destino / n, ext={".mp3"}, plazo=plazo)
            res["estilos"] += 1
            res["bajados"] += r["bajados"]
            res["ya_estaban"] += r["ya_estaban"]
            res["fallos"] += [f"{n}/{x}" for x in r["fallos"]]
        return res

    # ------------------------------------------------------------------ subir
    def subir_anuncio(self, oferta: dict, archivo: Path, *, mime: str = "video/mp4", carpeta: str = CARPETA_ANUNCIOS) -> dict:
        """Deja `archivo` en  <oferta>/ANUNCIOS/ . Si ya esta (mismo nombre, mismo tamano y MD5) no hace nada; si hay otro con el mismo nombre y distinto contenido, sube este como _v2, _v3...
        Devuelve {id, name, size, md5, enlace, carpeta, estado: 'subido'|'ya estaba'}. Falla (ErrorEspejo/ErrorDrive) si Drive no lo guardo igual."""
        archivo = Path(archivo)
        nombre = nombre_seguro(archivo.name)
        if nombre is None or not archivo.is_file() or archivo.stat().st_size <= 0:
            raise ErrorEspejo(f"no hay un video para subir: {archivo.name}")
        # `carpeta`: ANUNCIOS (los del corredor) o, para las variaciones hechas por el servidor (variaciones_vps.py), la de corredor.json › variaciones.carpeta
        if nombre_seguro(carpeta) is None:
            raise ErrorEspejo("el nombre de la carpeta de destino no es valido")
        self.drive.permitir(oferta["id"])
        try:
            anuncios_id = self.drive.hallar_o_crear_carpeta(oferta["id"], carpeta)       # la unica escritura en la carpeta de la oferta: crear ANUNCIOS (o la de las variaciones) si falta
        finally:
            self.drive.cercado.discard(oferta["id"])
        self.drive.permitir(anuncios_id)
        try:
            tam, md5 = archivo.stat().st_size, da._md5(archivo).lower()
            hijos = [f for f in self.drive.listar_hijos(anuncios_id) if not _es_carpeta(f)]
            por_nombre = {str(f.get("name")): f for f in hijos}
            candidato, n = nombre, 1
            while candidato in por_nombre:
                f = por_nombre[candidato]
                if str(f.get("size")) == str(tam) and str(f.get("md5Checksum") or md5).lower() == md5:
                    return {"id": f["id"], "name": candidato, "size": tam, "md5": md5, "enlace": enlace_archivo(f["id"]), "carpeta": enlace_carpeta(anuncios_id), "estado": "ya estaba"}
                n += 1
                base, ext = os.path.splitext(nombre)
                candidato = f"{base}_v{n}{ext}"
            meta = self.drive.subir_archivo(archivo, anuncios_id, candidato, mime)
            # comprobacion final con otra pregunta a Drive: esta en ANUNCIOS y con el tamano exacto
            datos = self.drive.datos_de(str(meta["id"]))
            if anuncios_id not in (datos.get("parents") or []) or str(datos.get("size")) != str(tam) or datos.get("trashed"):
                raise ErrorEspejo(f"Drive no confirma {candidato} dentro de ANUNCIOS con el tamano exacto: no se da por entregado")
            self.bytes_subidos += tam
            return {"id": meta["id"], "name": candidato, "size": tam, "md5": md5, "enlace": enlace_archivo(str(meta["id"])), "carpeta": enlace_carpeta(anuncios_id), "estado": "subido"}
        finally:
            self.drive.cercado.discard(anuncios_id)
