"""Fabrica de ofertas -> b-roll de TikTok en el VPS, SIN el PC del dueno y sin gastar tokens de Claude.

Pedido del dueno (2026-10-21): «cuando una oferta pase a la fabrica de ofertas, que empiece a buscar b-rolls, pero basado en los anuncios a modelar de la oferta
original: se eligen los anuncios mas antiguos de la oferta original, se transcriben, y de ahi se sabe como buscar b-rolls para hacer nuestros propios anuncios.
Si se puede usar el original se usa y solo se cambia el inicio; si no, lo creamos todo». Todo en el servidor y la informacion en Google Drive.

Que hace, por cada oferta nueva de la Fabrica (la ve en la cola de pedidos del dash, solo LEYENDO: nunca toma ni cambia un pedido):
  1. Drive:    halla o crea  MI NEGOCIO > OFERTAS LATAM > <NOMBRE DE LA OFERTA>  con las subcarpetas B-rolls y REFERENCIA.
  2. Anuncios: abre el enlace del anuncio original (el de UN anuncio, o el de la PAGINA de un anunciante con sus filtros: pais, orden por impresiones, fecha) en la
               Biblioteca de Meta, lista los anuncios del MISMO anunciante (de todos los paises) y baja los 3 mas antiguos con video
               (primero los que siguen activos; un solo anuncio por creativo): video, copy y datos, a REFERENCIA. Si despues le cambian el enlace (otro anunciante de la misma
               oferta), la oferta se estudia otra vez: otro anunciante va a su propia carpeta (REFERENCIA 2, 3...) y el mismo sigue en la misma; lo anterior no se borra.
  3. Escucha:  transcribe cada video con Whisper (en el servidor) y mide cuanto texto trae escrito encima (el mismo detector que usa el b-roll).
  4. Decide:   ¿sirve el original como base? (audio en español y al menos 70 % del video sin texto encima; y la receta de la oferta debe permitir usar cortes del original).
  5. Busca:    saca frases de busqueda por reglas fijas (consultas_desde_texto.py: tema, promesa y lo que dicen los anuncios) y baja de TikTok los videos limpios
               a B-rolls con broll_vps.py (lista publica gratis); la oferta queda registrada para que la corrida semanal siga trayendo.
  6. Informa:  deja INFORME.md en la carpeta de la oferta, con lo que se hizo y lo que no se pudo, en palabras del negocio.

Cada paso queda anotado en estado/fabrica/<proyecto>.json y se puede repetir sin hacer dos veces lo ya hecho. Lo que lee de internet (anuncios, textos, nombres de la
oferta) es texto de terceros: solo se cuenta o se guarda, nunca se obedece. La llave del dash vive en secretos/dash.env y nunca se imprime.

Uso (en el VPS, como el usuario broll; el timer de systemd usa correr_fabrica.sh):
  python scripts/fabrica_broll.py vigilar [--solo PROYECTO] [--solo-cola] [--simular] [--sin-drive] [--sin-brolls]
  python scripts/fabrica_broll.py manual --enlace URL --nombre "Nombre" --slug producto [--tema "..."] [--material propio|referencia] [--rehacer] [--sin-drive] [--sin-brolls]
  python scripts/fabrica_broll.py estado

Codigos de salida: 0 bien (o nada que hacer) | 2 bien pero algo quedo pendiente | 3 el dash, Meta o TikTok no contestaron (se reintenta) | 4 Drive sin permiso | 1 configuracion mala.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

try:
    import fcntl
except ImportError:            # Windows: el candado solo existe en el servidor
    fcntl = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broll_tiktok as bt  # noqa: E402
import broll_vps as br  # noqa: E402
import consultas_desde_texto as ct  # noqa: E402
import drive_api as da  # noqa: E402
import original_reutilizable as orig  # noqa: E402
import referencia_meta as rm  # noqa: E402
import texto_quemado as tq  # noqa: E402
import transcribir_vps as tv  # noqa: E402
from _comun import configurar_consola  # noqa: E402

SALIDA_BIEN, SALIDA_CONFIG, SALIDA_PARCIAL, SALIDA_RED, SALIDA_DRIVE = 0, 1, 2, 3, 4
RE_ID_PROYECTO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
RE_ID_ANUNCIO = re.compile(r"^(AD([1-9]|1[0-2])|1(0[1-9]|[12][0-9]|30))\Z")      # AD1..AD12 (los de antes) o 101..130 (los numerados, desde 2026-10-22)
RE_SUBIBLE = re.compile(r"^(REF[1-6]_(referencia\.(mp4|jpg|json)|copy\.txt|transcripcion\.(json|txt)|analisis\.json)|consultas\.json|reutilizable\.json)$")
CARPETA_REFERENCIA = "REFERENCIA"
ARCHIVO_INFORME = "INFORME.md"
DEFECTOS = {"carpeta_ofertas": "", "pais_biblioteca": "BR", "referencias": 3, "max_consultas": 8, "proyectos_por_vuelta": 2, "ofertas_activas_max": 6,
            "videos_por_oferta": 15, "dias_guardar_referencia": 30, "vueltas_max": 3, "horas_entre_vueltas": 2}
PASOS = ("drive", "anuncios", "analisis", "consultas", "decision", "registro", "referencia_en_drive", "brolls", "informe")


_PESO_SALIDA = {SALIDA_BIEN: 0, SALIDA_PARCIAL: 1, SALIDA_RED: 2, SALIDA_CONFIG: 3, SALIDA_DRIVE: 4}


def peor(a: int, b: int) -> int:
    """De dos codigos de salida, el mas grave (no el mayor: 1 es mas grave que 2 y que 3)."""
    return a if _PESO_SALIDA.get(a, 3) >= _PESO_SALIDA.get(b, 3) else b


class ErrorConfig(Exception):
    """Falta o esta mal algo que pone una persona (la llave del dash, la carpeta de Drive de las ofertas)."""


class ErrorDash(Exception):
    """El dash no dio lo que se le pidio. `reintentar` dice si tiene sentido volver a probar mas tarde."""

    def __init__(self, mensaje: str, reintentar: bool = True):
        super().__init__(mensaje)
        self.reintentar = reintentar


# ------------------------------------------------------------------ configuracion y llave del dash
def config_fabrica(cfg: dict) -> dict:
    """El bloque `fabrica` de ofertas.json con sus valores por defecto, validado. Lanza ErrorConfig si algo no sirve."""
    bruto = cfg.get("fabrica") if isinstance(cfg.get("fabrica"), dict) else {}
    c = {**DEFECTOS, **{k: v for k, v in bruto.items() if not k.startswith("_")}}
    errores = []
    for clave, (mn, mx) in {"referencias": (1, 6), "max_consultas": (1, 12), "proyectos_por_vuelta": (1, 5), "ofertas_activas_max": (1, 20), "videos_por_oferta": (1, 50),
                            "dias_guardar_referencia": (1, 365), "vueltas_max": (1, 10), "horas_entre_vueltas": (0, 72)}.items():
        v = c.get(clave)
        if isinstance(v, bool) or not isinstance(v, int) or not (mn <= v <= mx):
            errores.append(f"fabrica: '{clave}' debe ser un numero entero entre {mn} y {mx}")
    if not re.fullmatch(r"[A-Z]{2}", str(c.get("pais_biblioteca"))):
        errores.append("fabrica: 'pais_biblioteca' debe ser un codigo de pais de dos letras (BR)")
    if c.get("carpeta_ofertas") and not da.id_de_carpeta(str(c["carpeta_ofertas"])):
        errores.append("fabrica: 'carpeta_ofertas' debe ser el enlace de la carpeta OFERTAS LATAM de Drive")
    if errores:
        raise ErrorConfig("; ".join(errores))
    return c


def leer_secreto_dash(ruta: Path | None = None) -> tuple[str, str]:
    """(direccion, llave) del dash desde secretos/dash.env (DASH_URL=... y DASH_CLAVE_EDITOR=...; las pone scripts\\Setup-ClaveEditorVps.ps1). Nunca imprime la llave."""
    ruta = ruta or (bt.RAIZ / "secretos" / "dash.env")
    if not ruta.is_file():
        raise ErrorConfig(f"falta {ruta}: la llave del dash la pone el dueno una vez corriendo scripts\\Setup-ClaveEditorVps.ps1 en su PC")
    valores = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            valores[k.strip()] = v.strip().strip('"').strip("'")
    url, llave = valores.get("DASH_URL", "").rstrip("/"), valores.get("DASH_CLAVE_EDITOR", "")
    if not url or not llave:
        raise ErrorConfig(f"{ruta} debe traer DASH_URL y DASH_CLAVE_EDITOR: vuelve a correr Setup-ClaveEditorVps.ps1")
    if any(c.isspace() for c in llave) or len(llave) < 16:
        raise ErrorConfig(f"DASH_CLAVE_EDITOR de {ruta} esta incompleta (debe ir entera, en una sola linea)")
    if not (url.startswith("https://") or url.startswith(("http://127.0.0.1:", "http://localhost:"))):
        raise ErrorConfig("DASH_URL debe empezar con https:// (la llave no se manda por una direccion en claro)")
    return url, llave


# ------------------------------------------------------------------ el dash (solo lectura)
class Dash:
    """La puerta del editor del dash (/api/fabrica-editor), usada SOLO para leer: la cola de pedidos abiertos y la orden de un pedido. Nunca avisa ni cambia nada."""

    def __init__(self, url: str, clave: str, sesion=None):
        import requests

        self.url, self._clave = url.rstrip("/"), clave
        self.http = sesion or requests.Session()

    def _get(self, params: dict | None = None) -> dict:
        import requests

        try:
            # sin seguir redirecciones: requests conserva las cabeceras al cambiar de sitio y la llave no debe salir de la direccion del dash
            r = self.http.get(self.url + "/api/fabrica-editor", params=params or {}, headers={"x-clave-editor": self._clave, "Accept": "application/json"}, timeout=30, allow_redirects=False)
        except requests.RequestException as e:
            raise ErrorDash(f"sin conexion con el dash ({e.__class__.__name__})") from None
        if 300 <= r.status_code < 400:
            raise ErrorDash("el dash contesto con una redireccion; no la sigo (la llave no sale de la direccion configurada): revisa DASH_URL", reintentar=False)
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code == 401:
            raise ErrorDash("el dash rechazo la llave del editor: el dueno corre otra vez scripts\\Setup-ClaveEditorVps.ps1", reintentar=False)
        if r.status_code == 503:
            raise ErrorDash("al dash todavia le falta la llave del editor (se activa con scripts\\Setup-ClaveEditor.ps1)", reintentar=False)
        if r.status_code >= 400 or not isinstance(j, dict) or not j.get("ok"):
            raise ErrorDash(f"el dash contesto {r.status_code}: {str(j.get('error') if isinstance(j, dict) else '')[:120] or 'sin detalle'}")
        return j

    def pedidos(self) -> list[dict]:
        """Los pedidos de anuncio que siguen abiertos (los mas viejos primero), solo los bien formados."""
        salida = []
        for p in self._get().get("pedidos") or []:
            if isinstance(p, dict) and RE_ID_PROYECTO.match(str(p.get("proyecto") or "")) and RE_ID_ANUNCIO.match(str(p.get("anuncio") or "")):
                salida.append({"proyecto": p["proyecto"], "anuncio": p["anuncio"], "nombre": str(p.get("nombre") or "")[:100], "slug": str(p.get("slug") or ""), "estado": str(p.get("estado") or ""),
                               "paises": [str(x)[:30] for x in (p.get("paises") or []) if isinstance(x, str)][:6]})
        return salida

    def orden(self, proyecto: str, anuncio: str) -> dict:
        if not RE_ID_PROYECTO.match(proyecto) or not RE_ID_ANUNCIO.match(anuncio):
            raise ErrorDash("proyecto o anuncio no validos", reintentar=False)
        o = self._get({"proyecto": proyecto, "anuncio": anuncio}).get("orden")
        if not isinstance(o, dict) or not isinstance(o.get("datos"), dict):
            raise ErrorDash("el dash mando una orden que no se entiende")
        return o


def _txt(v, largo: int = 200) -> str:
    """Un texto de terceros para guardarlo: una sola linea, sin caracteres de control, corto."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(v if v is not None else ""))).strip()[:largo]


def _ajeno_de_markdown(md: str) -> dict[str, str]:
    """Lo que la orden trae en «Material ajeno» (entre vallas ~~~): Anunciante, Tema, Que vende, Gancho del anuncio, Idioma original. Es texto de terceros."""
    m = re.search(r"##\s*Material ajeno.*?\n~~~\n(.*?)\n~~~", md or "", re.S)
    out = {}
    if not m:
        return out
    claves = {"anunciante": "anunciante", "tema": "tema", "que vende": "entregable", "gancho del anuncio": "angulo", "idioma original": "idioma_original"}
    for linea in m.group(1).splitlines():
        etiqueta, _, valor = linea.partition(":")
        k = claves.get(ct.sin_acentos(etiqueta).strip().lower())
        if k and valor.strip():
            out[k] = _txt(valor, 400)
    return out


def leer_orden(orden: dict) -> dict:
    """De la orden del dash a lo que usa este programa (todo recortado y limpio)."""
    d = orden.get("datos") or {}
    p, ref, oferta, receta = d.get("proyecto") or {}, d.get("referencia") or {}, d.get("oferta") or {}, d.get("receta") or {}
    enlace = _txt(ref.get("linkAnuncio"), 400)
    gancho = receta.get("gancho") if isinstance(receta.get("gancho"), dict) else {}
    material = receta.get("material") if receta.get("material") in ("propio", "referencia") else "propio"
    info = {"proyecto": _txt(p.get("id"), 48), "nombre": _txt(p.get("nombre"), 100), "slug": _txt(p.get("slug"), 40), "paises": [_txt(x, 30) for x in (p.get("paises") or []) if isinstance(x, str)][:6],
            "link_anuncio": enlace if rm.es_enlace_meta(enlace) else "", "link_pagina": _txt(ref.get("linkPagina"), 300),
            "promesa": _txt(oferta.get("promesa"), 400), "promesa_corta": _txt(oferta.get("promesaCorta"), 200),
            "vinetas": [_txt(x, 200) for x in (oferta.get("vinetas") or []) if isinstance(x, str)][:12], "ebooks": [_txt(e.get("titulo"), 150) for e in (oferta.get("ebooks") or []) if isinstance(e, dict)][:6],
            "material": material, "gancho": {"modo": _txt(gancho.get("modo"), 20), "segundos": gancho.get("segundos") if isinstance(gancho.get("segundos"), int) else None},
            "anunciante": "", "tema": "", "entregable": "", "angulo": "", "idioma_original": ""}
    info.update(_ajeno_de_markdown(str(orden.get("markdown") or "")))
    return info


# ------------------------------------------------------------------ estado de cada oferta
def _escribir_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


def carpeta_estado() -> Path:
    return bt.RAIZ / "estado" / "fabrica"


def leer_estado(proyecto: str) -> dict | None:
    try:
        d = json.loads((carpeta_estado() / f"{proyecto}.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("proyecto") == proyecto else None
    except (OSError, ValueError):
        return None


def estado_nuevo(info: dict, ahora: datetime) -> dict:
    return {"proyecto": info["proyecto"], "slug": info["slug"], "nombre": info["nombre"], "creado": ahora.isoformat(timespec="seconds"), "actualizado": ahora.isoformat(timespec="seconds"),
            "vueltas": 0, "proximo_intento": "", "estado": "en-curso", "info": info, "drive": {}, "pasos": {}, "referencias": [], "anunciante": {}, "consultas": [], "decision": {}, "brolls": {},
            "estudios": [], "cambio_de_estudio": False}


def guardar_estado(st: dict, ahora: datetime) -> None:
    st["actualizado"] = ahora.isoformat(timespec="seconds")
    _escribir_json(carpeta_estado() / f"{st['proyecto']}.json", st)


# ------------------------------------------------------------------ carpetas de Drive
def clave_nombre(texto: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", ct.sin_acentos(texto).upper())


def nombre_carpeta(nombre: str, slug: str) -> str:
    """El nombre de la carpeta de la oferta, como las otras (DINAMICAS, LEY MURPHY): mayusculas, sin acentos, sin simbolos raros, hasta 40 letras."""
    t = re.sub(r"[^A-Z0-9 .&_-]+", " ", ct.sin_acentos(nombre).upper())
    t = re.sub(r"\s+", " ", t).strip(" .-_")[:40].strip(" .-_")
    return t or re.sub(r"[^A-Z0-9]+", " ", slug.upper()).strip() or "OFERTA"


def enlace_carpeta(carpeta_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{carpeta_id}"


# ------------------------------------------------------------------ dependencias (se cambian en las pruebas)
class Dependencias:
    def __init__(self, **cambios):
        self.meta = rm.ClienteMeta
        self.transcribir = tv.transcribir
        self.analizar_texto = tq.analizar
        self.descargador = bt.DescargadorYtdlp
        self.dormir = time.sleep
        self.ahora = datetime.now
        self.correr_brolls = None          # callable(slug, args) -> codigo; por defecto broll_vps.correr en este mismo programa
        self.ruta_config = None            # el ofertas.json que se le pasa a broll_vps (None = el del servidor)
        for k, v in cambios.items():
            setattr(self, k, v)


class Tuberia:
    """Los pasos de UNA oferta. Cada paso es repetible: si ya esta hecho se salta, y un fallo de un paso no impide los demas."""

    def __init__(self, cfg: dict, deps: Dependencias, drive: da.Drive | None, log=print, simular: bool = False, sin_brolls: bool = False, sin_subir: bool = False):
        self.cfg, self.deps, self.drive, self.log = cfg, deps, drive, log
        self.simular, self.sin_brolls, self.sin_subir = simular, sin_brolls, sin_subir

    # ---- util
    def _ref_dir(self, st: dict) -> Path:
        return bt.PUBLIC / "productos" / st["slug"] / "referencia"

    def _paso(self, st: dict, nombre: str, estado: str, nota: str = "") -> None:
        st["pasos"][nombre] = {"estado": estado, "nota": nota[:300], "en": self.deps.ahora().isoformat(timespec="seconds")}
        guardar_estado(st, self.deps.ahora())

    def _hecho(self, st: dict, nombre: str) -> bool:
        return st["pasos"].get(nombre, {}).get("estado") in ("ok", "sin-datos")

    def archivar_estudio(self, st: dict) -> None:
        """Le cambiaron el enlace del anuncio original: la oferta se vuelve a estudiar. Lo estudiado antes se anota en st['estudios'] (con su carpeta de Drive, que NO se toca), se limpia lo
        local del estudio anterior y se reinician los pasos que dependen del enlace. La carpeta de la oferta y sus b-rolls ya bajados se conservan. Llamar ANTES de poner la info nueva."""
        ahora = self.deps.ahora().isoformat(timespec="seconds")
        an = st.get("anunciante") or {}
        if st.get("referencias") or an:
            st.setdefault("estudios", []).append({"n": len(st.get("estudios") or []) + 1, "link": (st.get("info") or {}).get("link_anuncio", ""), "anunciante": _txt(an.get("nombre"), 90),
                                                  "page_id": _txt(an.get("page_id"), 30), "carpeta": st["drive"].get("referencia_nombre") or CARPETA_REFERENCIA,
                                                  "carpeta_id": st["drive"].get("referencia_id", ""), "anuncios": [r.get("ad_id") for r in st.get("referencias") or []], "hasta": ahora})
        carpeta = self._ref_dir(st)
        if carpeta.is_dir():
            for archivo in carpeta.iterdir():
                if archivo.is_file() and RE_SUBIBLE.match(archivo.name):
                    archivo.unlink()
        for paso in ("anuncios", "analisis", "consultas", "decision", "registro", "referencia_en_drive", "brolls", "informe"):
            st["pasos"].pop(paso, None)
        st["referencias"], st["anunciante"], st["consultas"], st["decision"], st["brolls"] = [], {}, [], {}, {}
        st.pop("anclas", None)
        st.pop("filtro_desc", None)
        st["vueltas"], st["proximo_intento"], st["estado"], st["cambio_de_estudio"] = 0, "", "en-curso", True
        self.log("  Le cambiaron el enlace del anuncio original: se vuelve a estudiar (lo anterior no se borra)")

    def _carpeta_del_estudio(self, st: dict, lista: dict) -> None:
        """Tras cambiar el enlace: si el anunciante es el mismo de antes, el estudio sigue en la misma carpeta; si es OTRO, va a su propia carpeta (REFERENCIA 2, 3...) para no mezclar sus
        archivos con los del primero."""
        if not st.get("cambio_de_estudio"):
            return
        previo = (st.get("estudios") or [None])[-1]
        if previo and previo.get("page_id") and previo["page_id"] == _txt(lista.get("page_id"), 30):
            st["estudios"].pop()
            self.log(f"  Es el mismo anunciante de antes: se sigue en la carpeta {st['drive'].get('referencia_nombre') or CARPETA_REFERENCIA}")
        elif previo and self.drive is not None and not self.simular and st["drive"].get("oferta_id"):
            nombre = f"{CARPETA_REFERENCIA} {len(st['estudios']) + 1}"
            nuevo = self.drive.hallar_o_crear_carpeta(st["drive"]["oferta_id"], nombre)
            self.drive.permitir(nuevo)
            st["drive"]["referencia_id"], st["drive"]["referencia_nombre"] = nuevo, nombre
            self.log(f"  Es OTRO anunciante: sus archivos van a la carpeta '{nombre}' (la anterior no se toca)")
        st["cambio_de_estudio"] = False

    # ---- 1. Drive
    def paso_drive(self, st: dict) -> None:
        if self._hecho(st, "drive") and st["drive"].get("oferta_id"):
            return
        if self.drive is None:
            self._paso(st, "drive", "omitido", "sin Drive en esta corrida")
            return
        d = st["drive"]
        padre = da.id_de_carpeta(str(self.cfg["carpeta_ofertas"]))
        if not d.get("oferta_id"):
            if not padre:
                raise ErrorConfig("falta 'carpeta_ofertas' en ofertas.json (el enlace de MI NEGOCIO > OFERTAS LATAM): sin ella no se sabe donde crear la carpeta de la oferta")
            nombre = nombre_carpeta(st["nombre"], st["slug"])
            hijos = [f for f in self.drive.listar_hijos(padre) if f.get("mimeType") == da.MIME_CARPETA]
            iguales = [f for f in hijos if clave_nombre(str(f.get("name"))) == clave_nombre(nombre)]
            if iguales:
                d["oferta_id"], d["nombre_carpeta"], d["creada"] = iguales[0]["id"], iguales[0]["name"], False
                self.log(f"  Drive: ya existia la carpeta '{iguales[0]['name']}' de esta oferta: se usa esa")
            elif self.simular:
                self.log(f"  (simulacion) Drive: se crearia la carpeta '{nombre}' en OFERTAS LATAM, con B-rolls y REFERENCIA")
                self._paso(st, "drive", "omitido", "simulacion")
                return
            else:
                self.drive.permitir(padre)
                try:
                    d["oferta_id"] = self.drive.crear_carpeta(padre, nombre)
                finally:
                    self.drive.cercado.discard(padre)       # en OFERTAS LATAM solo se crea la carpeta de la oferta; no se vuelve a escribir ahi
                d["nombre_carpeta"], d["creada"] = nombre, True
                self.log(f"  Drive: se creo la carpeta '{nombre}' en OFERTAS LATAM")
        info = self.drive.carpeta(d["oferta_id"])
        if not info["escribible"]:
            raise da.ErrorDrive(f"la carpeta '{info['name']}' es de solo lectura para la cuenta que dio el permiso")
        self.drive.permitir(d["oferta_id"])
        if self.simular:
            self._paso(st, "drive", "omitido", "simulacion")
            return
        brolls_id, _ = self.drive.hallar_carpeta(d["oferta_id"], bt.CARPETA_DRIVE)
        if not brolls_id:
            brolls_id = self.drive.crear_carpeta(d["oferta_id"], bt.CARPETA_DRIVE)
            self.drive.permitir(brolls_id)
            self.drive.guardar_texto(brolls_id, "LEEME.txt", br.LEEME_BROLLS.encode("utf-8"))      # el aviso de derechos, solo cuando la carpeta es nueva (como en el b-roll semanal)
        d["brolls_id"] = brolls_id
        d["referencia_id"] = self.drive.hallar_o_crear_carpeta(d["oferta_id"], CARPETA_REFERENCIA)
        self.drive.permitir(d["brolls_id"])
        self.drive.permitir(d["referencia_id"])
        self._paso(st, "drive", "ok")

    def _permitir_conocidas(self, st: dict) -> None:
        """Una corrida nueva empieza con la cerca vacia: se declaran otra vez las carpetas de esta oferta (ya existen)."""
        if self.drive is None:
            return
        for k in ("oferta_id", "brolls_id", "referencia_id"):
            if st["drive"].get(k):
                self.drive.permitir(st["drive"][k])

    # ---- 2. los anuncios originales mas antiguos
    def paso_anuncios(self, st: dict) -> int | None:
        if self._hecho(st, "anuncios"):
            return
        info = st["info"]
        if not info["link_anuncio"]:
            self._paso(st, "anuncios", "sin-datos", "la oferta no trae el enlace del anuncio original de la Biblioteca de Meta")
            return
        es_pagina = rm.id_de(info["link_anuncio"]) is None        # el enlace es el de la pagina de un anunciante, no el de un anuncio suelto
        try:
            cliente = self.deps.meta()
            try:
                lista = rm.anuncios_del_anunciante(info["link_anuncio"], pais=self.cfg["pais_biblioteca"], cliente=cliente)
            except rm.ErrorMeta as e:
                if es_pagina:
                    raise
                self.log(f"  Meta: no se pudo ver la lista del anunciante ({str(e)[:120]}); se prueba solo con el anuncio del enlace")
                ad, ad_id = rm.leer(info["link_anuncio"])
                lista = {"anunciante": _txt((ad.get("snapshot") or {}).get("page_name"), 90), "page_id": "", "referencia_id": ad_id, "anuncios": [ad], "hay_mas": False, "solo_uno": True}
        except rm.ErrorMeta as e:
            self._paso(st, "anuncios", "error", str(e))
            self.log(f"  ERROR en 'anuncios': {e}")
            return SALIDA_RED
        elegidos = rm.elegir_mas_antiguos(lista["anuncios"], int(self.cfg["referencias"]))
        self._carpeta_del_estudio(st, lista)
        st["anunciante"] = {"nombre": _txt(lista["anunciante"], 90), "page_id": _txt(lista["page_id"], 30), "anuncios_vistos": len(lista["anuncios"]), "hay_mas": bool(lista["hay_mas"]),
                            "solo_uno": bool(lista.get("solo_uno")), "pais": self.cfg["pais_biblioteca"], "radar_ad_id": lista["referencia_id"],
                            "tipo_enlace": "pagina" if es_pagina else "anuncio", "filtros": lista.get("filtros") or {}, "amplia": bool(lista.get("amplia"))}
        if not elegidos:
            self._paso(st, "anuncios", "sin-datos", f"de los {len(lista['anuncios'])} anuncios que se ven del anunciante, ninguno tiene video")
            return
        carpeta = self._ref_dir(st)
        refs = []
        for i, ad in enumerate(elegidos, 1):
            ad_id = str(ad.get("ad_archive_id") or "")
            corto = rm.resumen_corto(ad)
            ref = {"n": i, "ad_id": ad_id, **{k: corto[k] for k in ("inicio", "dias_en_aire", "activo", "variantes", "formato")}, "copy_inicio": _txt(corto["copy_inicio"], 140),
                   "video": None, "motivo_sin_video": "", "tecnico": {}, "analisis": None, "error_analisis": ""}
            try:
                # los enlaces de video de Meta caducan en horas: se bajan ahora mismo
                res = rm.guardar_referencia(ad, ad_id, carpeta, f"REF{i}")
                ref["video"], ref["motivo_sin_video"] = res["video"], _txt(res["motivo_sin_video"], 200)
                if res["video"]:
                    datos = json.loads((carpeta / f"REF{i}_referencia.json").read_text(encoding="utf-8"))
                    ref["tecnico"] = {"duracion": datos.get("video_duracion"), "ancho": datos.get("video_ancho"), "alto": datos.get("video_alto"), "audio": datos.get("video_audio")}
                self.log(f"  REF{i}: anuncio {ad_id} ({corto['inicio'] or '?'}) -> " + (f"video {res['video']}" if res["video"] else f"sin video ({ref['motivo_sin_video'][:80]})"))
            except (rm.ErrorMeta, OSError, ValueError) as e:
                ref["motivo_sin_video"] = _txt(str(e), 200)
                self.log(f"  REF{i}: no se pudo guardar el anuncio {ad_id}: {ref['motivo_sin_video']}")
            refs.append(ref)
            self.deps.dormir(2.5)
        st["referencias"] = refs
        self._paso(st, "anuncios", "ok", f"{len(refs)} anuncio(s) de {len(lista['anuncios'])} vistos")

    # ---- 3. escuchar y mirar cada video
    def paso_analisis(self, st: dict) -> None:
        pendientes = [r for r in st["referencias"] if r.get("video") and (not r.get("analisis") or r.get("error_analisis"))]
        if not pendientes:
            if not self._hecho(st, "analisis"):
                self._paso(st, "analisis", "sin-datos" if not st["referencias"] else "ok")
            return
        carpeta = self._ref_dir(st)
        for ref in pendientes:
            ruta = carpeta / ref["video"]
            tx, texto, errores = None, None, []
            try:
                tx = self.deps.transcribir(ruta)
            except tv.ErrorTranscripcion as e:
                errores.append(f"no se pudo transcribir: {e}")
            try:
                texto = self.deps.analizar_texto(ruta, duracion=(ref["tecnico"] or {}).get("duracion"))
            except tq.ErrorTexto as e:
                errores.append(f"no se pudo medir el texto escrito: {e}")
            n = ref["n"]
            if tx is not None:
                _escribir_json(carpeta / f"REF{n}_transcripcion.json", tx)
                (carpeta / f"REF{n}_transcripcion.txt").write_text(tv.con_tiempos(tx), encoding="utf-8")
            a = {"idioma": tx["idioma"] if tx else None, "prob_idioma": tx["prob_idioma"] if tx else None, "palabras": tx["palabras"] if tx else None, "sin_audio": bool(tx and tx["sin_audio"]),
                 "recortado": bool(tx and tx["recortado"]), "texto_inicio": _txt(tx["texto"], 220) if tx else "",
                 "texto_pct": texto["texto_pct"] if texto else None, "limpio_seg": texto["limpio_seg"] if texto else None,
                 "tramos_limpios": tq.tramos_a_texto(texto["tramos_limpios"]) if texto else "", "calidad": texto["calidad"] if texto else None}
            _escribir_json(carpeta / f"REF{n}_analisis.json", a)
            ref["analisis"], ref["error_analisis"] = a, "; ".join(errores)[:300]
            if tx is not None:
                ref["transcripcion"] = tx["texto"][:6000]
            self.log(f"  REF{n}: " + (f"audio en {orig.nombre_idioma(a['idioma'])}, {a['palabras']} palabras" if tx else "sin transcripcion") + (f"; texto escrito en el {a['texto_pct']} % del video" if texto else "; texto sin medir")
                     + (f" [{ref['error_analisis']}]" if errores else ""))
            guardar_estado(st, self.deps.ahora())
        fallaron = [r for r in st["referencias"] if r.get("video") and r["error_analisis"]]
        self._paso(st, "analisis", "error" if fallaron else "ok", "; ".join(f"REF{r['n']}: {r['error_analisis']}" for r in fallaron)[:300])

    # ---- 4. las frases de busqueda
    def fuentes_de_texto(self, st: dict) -> list[dict]:
        info, carpeta = st["info"], self._ref_dir(st)
        f = [{"tipo": "nombre", "texto": info["nombre"]}, {"tipo": "tema", "texto": info["tema"]}, {"tipo": "tema", "texto": info["entregable"]},
             {"tipo": "oferta", "texto": info["promesa"]}, {"tipo": "oferta", "texto": info["promesa_corta"]},
             {"tipo": "copy", "texto": info["angulo"]}, {"tipo": "anunciante", "texto": info["anunciante"]}, {"tipo": "anunciante", "texto": st["anunciante"].get("nombre", "")}]
        f += [{"tipo": "oferta", "texto": v} for v in info["vinetas"]] + [{"tipo": "oferta", "texto": t} for t in info["ebooks"]]
        for ref in st["referencias"]:
            ruta = carpeta / f"REF{ref['n']}_copy.txt"
            if ruta.is_file():
                lineas = [x for x in ruta.read_text(encoding="utf-8", errors="replace").splitlines() if not x.startswith(("Boton:", "Titulo:"))]      # el boton y el titulo son llamadas a la accion
                f.append({"tipo": "copy", "texto": "\n".join(x.removeprefix("Descripcion: ") for x in lineas)[:4000]})
            if ref.get("transcripcion"):
                f.append({"tipo": "transcripcion", "texto": ref["transcripcion"]})
        return [x for x in f if x["texto"].strip()]

    def paso_consultas(self, st: dict) -> None:
        if self._hecho(st, "consultas") and st["consultas"]:
            return
        fuentes = self.fuentes_de_texto(st)
        frases = ct.generar(fuentes, int(self.cfg["max_consultas"]))
        st["consultas"] = [{"consulta": q["consulta"], "tipo": q["tipo"], "puntaje": q["puntaje"], "fuentes": q["fuentes"]} for q in frases]
        st["anclas"] = ct.anclas(fuentes)
        st["filtro_desc"] = sorted({ct.raiz_de_filtro(p) for p in st["anclas"] if ct.raiz_de_filtro(p)})
        if frases:
            self._paso(st, "consultas", "ok", f"{len(frases)} frase(s)")
            self.log("  Frases de busqueda: " + " | ".join(q["consulta"] for q in frases))
        else:
            self._paso(st, "consultas", "sin-datos", "el texto de la oferta y de los anuncios no dio ninguna frase de busqueda")
        _escribir_json(self._ref_dir(st) / "consultas.json", {"frases": st["consultas"], "anclas": st["anclas"], "filtro_descripcion": st["filtro_desc"], "generado": self.deps.ahora().isoformat(timespec="seconds"),
                                                                "nota": "Salen de reglas fijas (consultas_desde_texto.py), sin inteligencia artificial: tema, promesa y lo que dicen los anuncios originales."})

    # ---- 5. ¿sirve el original?
    def paso_decision(self, st: dict) -> None:
        evaluaciones = []
        for ref in st["referencias"]:
            a = ref.get("analisis")
            if not ref.get("video") or not a:
                continue
            t = ref.get("tecnico") or {}
            evaluaciones.append(orig.evaluar({"ad_id": ref["ad_id"], "n": ref["n"], "idioma": a["idioma"], "prob_idioma": a["prob_idioma"], "palabras": None if a["palabras"] is None else a["palabras"],
                                              "duracion": t.get("duracion"), "ancho": t.get("ancho"), "alto": t.get("alto"), "texto_pct": a["texto_pct"], "limpio_seg": a["limpio_seg"],
                                              "transcripcion": ref.get("transcripcion", "")}))
        st["decision"] = {**orig.decidir(evaluaciones, st["info"]["material"], st["info"]["gancho"]), "evaluaciones": evaluaciones}
        _escribir_json(self._ref_dir(st) / "reutilizable.json", {**st["decision"], "receta": {"material": st["info"]["material"], "gancho": st["info"]["gancho"]},
                                                                  "generado": self.deps.ahora().isoformat(timespec="seconds")})
        self._paso(st, "decision", "ok", st["decision"]["frase"])
        self.log("  " + st["decision"]["frase"])

    # ---- 6. dejar la oferta registrada para el b-roll
    def paso_registro(self, st: dict) -> None:
        if not st["consultas"]:
            self._paso(st, "registro", "sin-datos", "sin frases de busqueda no hay nada que registrar")
            return
        if not st["drive"].get("oferta_id"):
            self._paso(st, "registro", "omitido", "sin carpeta de Drive no se registra la oferta")
            return
        anunciante = _txt((st.get("anunciante") or {}).get("nombre"), 90)        # el nombre en la Biblioteca de Meta: su propia cuenta de TikTok no se usa como b-roll
        entrada = {"producto": st["slug"], "nombre": st["nombre"], "activa": True, "drive": enlace_carpeta(st["drive"]["oferta_id"]),
                   "consultas": [q["consulta"] for q in st["consultas"] if q["tipo"] != "plantilla"][:6] + [q["consulta"] for q in st["consultas"] if q["tipo"] == "plantilla"][:2],
                   "hashtags": [], "cuentas": [], "filtro_desc": list(st.get("filtro_desc") or []), "excluir_autores": [anunciante] if anunciante else [], "max": int(self.cfg["videos_por_oferta"]), "min_seg": 4, "max_seg": 120, "region": "", "paginas": 2, "relacionadas": 1,
                   "max_creditos": 14, "proveedor": "descubrir", "origen": "manual" if st["proyecto"].startswith("manual-") else "fabrica", "proyecto": st["proyecto"]}
        if self.simular:
            self._paso(st, "registro", "omitido", "simulacion")
            return
        registrar_oferta(entrada)
        self._paso(st, "registro", "ok")

    # ---- 7. subir lo estudiado a REFERENCIA
    def paso_subir_referencia(self, st: dict) -> None:
        if self.drive is None or self.sin_subir or self.simular or not st["drive"].get("referencia_id"):
            return
        carpeta, rid = self._ref_dir(st), st["drive"]["referencia_id"]
        existentes = {f.get("name"): f for f in self.drive.listar_hijos(rid)}
        subidos = 0
        for ruta in sorted(carpeta.iterdir()) if carpeta.is_dir() else []:
            if not ruta.is_file() or not RE_SUBIBLE.match(ruta.name):
                continue
            ya = existentes.get(ruta.name)
            if ya is not None and str(ya.get("size")) == str(ruta.stat().st_size):
                continue
            if ruta.suffix in (".mp4", ".jpg"):
                if ya is not None:
                    self.log(f"  x {ruta.name}: en Drive ya hay un archivo con ese nombre y OTRO tamano; no se toca")
                    continue
                self.drive.subir_archivo(ruta, rid, ruta.name, "video/mp4" if ruta.suffix == ".mp4" else "image/jpeg")
            else:
                self.drive.guardar_texto(rid, ruta.name, ruta.read_bytes(), "application/json" if ruta.suffix == ".json" else "text/plain")
            subidos += 1
        self._paso(st, "referencia_en_drive", "ok", f"{subidos} archivo(s) nuevo(s) en {st['drive'].get('referencia_nombre') or CARPETA_REFERENCIA}")
        if subidos:
            self.log(f"  REFERENCIA en Drive: {subidos} archivo(s) subido(s)")

    # ---- 8. bajar los b-rolls
    def paso_brolls(self, st: dict) -> int:
        if self.sin_brolls or self.simular:
            self._paso(st, "brolls", "omitido", "no se pidio" if self.sin_brolls else "simulacion")
            return SALIDA_BIEN
        if st["pasos"].get("registro", {}).get("estado") != "ok":
            self._paso(st, "brolls", "sin-datos", "la oferta no quedo registrada (sin frases de busqueda o sin carpeta de Drive)")
            return SALIDA_BIEN
        if st["pasos"].get("brolls", {}).get("estado") == "ok":
            return SALIDA_BIEN                 # ya se bajo en una vuelta anterior; la corrida semanal sigue trayendo mas
        argv = ["correr", "--solo", st["slug"], "--max-por-oferta", str(self.cfg["videos_por_oferta"])]
        if self.deps.ruta_config:
            argv += ["--config", str(self.deps.ruta_config)]
        args = br.construir_parser().parse_args(argv)
        if self.deps.correr_brolls:
            codigo = self.deps.correr_brolls(st["slug"], args)
        else:
            codigo = br.correr(args, fabrica=self.deps.descargador, analizar=self.deps.analizar_texto, drive=self.drive, log=self.log)
        resumen = {}
        try:
            ult = json.loads((bt.RAIZ / "estado" / "ultima_corrida.json").read_text(encoding="utf-8"))
            resumen = next((o for o in ult.get("ofertas", []) if o.get("producto") == st["slug"]), {})
        except (OSError, ValueError):
            pass
        filas = bt.leer_fuentes(bt.PUBLIC / "productos" / st["slug"] / bt.CARPETA_LOCAL)
        por_consulta: dict[str, int] = {}
        for fila in filas.values():
            if fila.get("estado") == "bajado" and fila.get("consulta"):
                por_consulta[fila["consulta"]] = por_consulta.get(fila["consulta"], 0) + 1
        st["brolls"] = {"codigo": codigo, "bajados": int(resumen.get("bajados", 0)), "subidos": int(resumen.get("subidos", 0)), "descartados_texto": int(resumen.get("descartados_texto", 0)),
                        "de_la_cuenta_del_anunciante": int(resumen.get("de_la_cuenta_del_anunciante", 0)),
                        "busquedas": {_txt(k, 60): int(v) for k, v in list((resumen.get("busquedas") or {}).items())[:30]},
                        "problemas": [_txt(p, 160) for p in resumen.get("problemas", [])][:6], "por_consulta": por_consulta, "en": self.deps.ahora().isoformat(timespec="seconds"),
                        "total_en_registro": sum(1 for f in filas.values() if f.get("estado") == "bajado")}
        self._paso(st, "brolls", "ok" if codigo in (SALIDA_BIEN, SALIDA_PARCIAL) else "error", f"salida {codigo}")
        return codigo

    # ---- 9. el informe
    def paso_informe(self, st: dict) -> None:
        texto = informe_md(st, self.deps.ahora())
        _escribir_json(carpeta_estado() / f"{st['proyecto']}.informe.json", {"markdown": texto})
        if self.drive is not None and st["drive"].get("oferta_id") and not self.simular and not self.sin_subir:
            self.drive.guardar_texto(st["drive"]["oferta_id"], ARCHIVO_INFORME, texto.encode("utf-8"), "text/markdown")
            self._paso(st, "informe", "ok", f"{ARCHIVO_INFORME} al dia en Drive")
        else:
            self._paso(st, "informe", "omitido", "sin Drive")

    # ---- todo junto
    def procesar(self, st: dict) -> int:
        """Corre los pasos pendientes de una oferta. Devuelve el codigo de salida de esta oferta. Un paso que falla no impide los siguientes."""
        self._permitir_conocidas(st)
        codigo = SALIDA_BIEN
        pasos = [("drive", self.paso_drive), ("anuncios", self.paso_anuncios), ("analisis", self.paso_analisis), ("consultas", self.paso_consultas), ("decision", self.paso_decision),
                 ("registro", self.paso_registro), ("referencia_en_drive", self.paso_subir_referencia), ("brolls", self.paso_brolls), ("informe", self.paso_informe)]
        for nombre, paso in pasos:
            try:
                c = paso(st)
                if isinstance(c, int):
                    codigo = peor(codigo, c)
                    if c == SALIDA_DRIVE:
                        raise da.ErrorPermiso("Drive no dejo seguir (sin permiso): el dueno vuelve a autorizar con scripts\\Setup-DriveBroll.ps1")
            except da.ErrorPermiso:
                guardar_estado(st, self.deps.ahora())
                raise
            except (da.ErrorDrive, ErrorConfig) as e:
                self._paso(st, nombre, "error", str(e))
                self.log(f"  ERROR en '{nombre}': {e}")
                codigo = peor(codigo, SALIDA_PARCIAL)
            except rm.ErrorMeta as e:
                self._paso(st, nombre, "error", str(e))
                self.log(f"  ERROR en '{nombre}': {e}")
                codigo = peor(codigo, SALIDA_RED)
            except Exception as e:  # noqa: BLE001 - una falla rara en un paso no debe dejar a la oferta sin informe ni a las demas sin atender
                self._paso(st, nombre, "error", f"fallo inesperado: {e.__class__.__name__}: {str(e)[:120]}")
                self.log(f"  ERROR inesperado en '{nombre}': {e.__class__.__name__}: {str(e)[:160]}")
                codigo = peor(codigo, SALIDA_PARCIAL)
        # vueltas: lo que fallo de forma pasajera se reintenta mas tarde; despues de vueltas_max se deja asi y el informe lo dice.
        # Una corrida a medias (--sin-brolls, --simular, sin Drive) no cuenta como vuelta y no cierra la oferta: la proxima vuelta normal la termina.
        if not (self.simular or self.sin_brolls or self.sin_subir or self.drive is None):
            st["vueltas"] += 1
        hay_error = any(p["estado"] == "error" for p in st["pasos"].values())
        hay_omitido = any(p["estado"] == "omitido" for p in st["pasos"].values())
        if hay_error and st["vueltas"] < int(self.cfg["vueltas_max"]):
            st["estado"] = "en-curso"
            st["proximo_intento"] = (self.deps.ahora() + timedelta(hours=int(self.cfg["horas_entre_vueltas"]))).isoformat(timespec="seconds")
        elif hay_error:
            st["estado"], st["proximo_intento"] = "con-pendientes", ""
        elif hay_omitido:
            st["estado"], st["proximo_intento"] = "en-curso", ""
        else:
            st["estado"], st["proximo_intento"] = "completo", ""
        guardar_estado(st, self.deps.ahora())
        return codigo


# ------------------------------------------------------------------ registro de ofertas para broll_vps.py
def archivo_ofertas() -> Path:
    return bt.RAIZ / "estado" / "ofertas_fabrica.json"


def leer_ofertas_registradas() -> list[dict]:
    try:
        d = json.loads(archivo_ofertas().read_text(encoding="utf-8"))
        return [o for o in d.get("ofertas", []) if isinstance(o, dict)] if isinstance(d, dict) else []
    except (OSError, ValueError):
        return []


def registrar_oferta(entrada: dict) -> None:
    """Agrega o reemplaza (por `producto`) la oferta en estado/ofertas_fabrica.json, que broll_vps.py suma a ofertas.json en cada corrida."""
    ofertas = [o for o in leer_ofertas_registradas() if o.get("producto") != entrada["producto"]]
    _escribir_json(archivo_ofertas(), {"nota": "Lo escribe scripts/fabrica_broll.py; broll_vps.py lo suma a ofertas.json. No se edita a mano.", "ofertas": ofertas + [entrada]})


def sincronizar_activas(abiertos: set[str]) -> int:
    """Una oferta de la Fabrica solo sigue trayendo b-roll mientras tenga pedidos abiertos. Devuelve cuantas cambio."""
    ofertas, cambios = leer_ofertas_registradas(), 0
    for o in ofertas:
        if o.get("origen") != "fabrica":
            continue
        activa = o.get("proyecto") in abiertos
        if bool(o.get("activa", True)) != activa:
            o["activa"], cambios = activa, cambios + 1
    if cambios:
        _escribir_json(archivo_ofertas(), {"nota": "Lo escribe scripts/fabrica_broll.py; broll_vps.py lo suma a ofertas.json. No se edita a mano.", "ofertas": ofertas})
    return cambios


# ------------------------------------------------------------------ el informe (en palabras del negocio)
def _md(texto: object, largo: int = 160) -> str:
    return _txt(texto, largo).replace("|", "/").replace("`", "'")


def _corto(texto: object, largo: int = 200) -> str:
    """Un texto recortado en una palabra entera (con «…» si se corto), sin barras ni saltos de linea."""
    t = _md(texto, 2000)
    if len(t) <= largo:
        return t
    return t[:largo].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _fecha_corta(iso: str) -> str:
    return str(iso or "")[:10] or "?"


def _filtros_en_palabras(filtros: dict) -> str:
    """Los filtros de la Biblioteca con los que se leyo la pagina de un anunciante, en palabras del negocio."""
    partes = []
    pais = filtros.get("country")
    if pais:
        partes.append("todos los países" if pais == "ALL" else f"país {pais}")
    estado = {"active": "solo los anuncios activos", "inactive": "solo los apagados", "all": "activos y apagados"}.get(filtros.get("active_status") or "")
    if estado:
        partes.append(estado)
    if filtros.get("sort_data[mode]") == "total_impressions" and (filtros.get("sort_data[direction]") or "desc") == "desc":
        partes.append("los de más impresiones primero")
    if filtros.get("start_date[min]"):
        partes.append(f"que empezaron desde el {filtros['start_date[min]']}")
    if filtros.get("start_date[max]"):
        partes.append(f"que empezaron hasta el {filtros['start_date[max]']}")
    return ", ".join(partes)


def informe_md(st: dict, ahora: datetime) -> str:
    info, d, an = st["info"], st["drive"], st["anunciante"]
    L: list[str] = []
    L.append(f"# {_md(st['nombre'], 100)}: informe de b-rolls")
    L.append(f"_Actualizado el {ahora.strftime('%Y-%m-%d %H:%M')} (hora de Bogotá). Lo escribe el servidor solo cuando una oferta pasa a la Fábrica, sin que nadie intervenga y sin gastar inteligencia artificial._")
    L.append("")
    dec = st.get("decision") or {}
    br_ = st.get("brolls") or {}
    L.append("## En resumen")
    n_ok = sum(1 for r in st["referencias"] if r.get("video"))
    if n_ok:
        L.append(f"- Se estudiaron {n_ok} anuncio{'s' if n_ok != 1 else ''} original{'es' if n_ok != 1 else ''} del anunciante (los más antiguos con video).")
    elif info["link_anuncio"]:
        L.append("- No se pudo estudiar ningún anuncio original (más abajo dice por qué).")
    else:
        L.append("- La oferta no trae el enlace del anuncio original, así que no se estudió ninguno.")
    L.append("- " + dec.get("frase", "Todavía no se decidió si sirve el original."))
    if br_:
        n_baj, n_desc, n_sub = br_.get("bajados", 0), br_.get("descartados_texto", 0), br_.get("subidos", 0)
        un = lambda n, uno, varios: uno if n == 1 else varios                 # «1 se descartó», «2 se descartaron»
        L.append(f"- De TikTok {un(n_baj, 'se bajó', 'se bajaron')} {n_baj} video{un(n_baj, '', 's')} nuevo{un(n_baj, '', 's')}: {n_desc} {un(n_desc, 'se descartó', 'se descartaron')} por traer texto escrito encima "
                 f"y {n_sub} {un(n_sub, 'quedó', 'quedaron')} en la carpeta B-rolls.")
        if br_.get("de_la_cuenta_del_anunciante"):
            n_cta = br_["de_la_cuenta_del_anunciante"]
            L.append(f"- Además se dejaron de lado {n_cta} video{'s' if n_cta != 1 else ''} de TikTok por ser de la cuenta del propio anunciante original ({_md(an.get('nombre') or '?', 60)}): su material promocional no se usa en nuestros anuncios.")
    else:
        L.append("- Todavía no se han bajado b-rolls de TikTok para esta oferta.")
    L.append("")
    L.append("## 1. Los anuncios originales que se estudiaron")
    if not info["link_anuncio"]:
        L.append("Esta oferta no trae el enlace del anuncio original de la Biblioteca de Meta, así que no se pudo elegir cuáles son los más antiguos. Pega ese enlace en la Fábrica para que se haga.")
    elif not st["referencias"]:
        nota = st["pasos"].get("anuncios", {}).get("nota", "")
        L.append("No se pudo estudiar ningún anuncio original" + (f": {_md(nota, 200)}." if nota else ".") + " Se vuelve a intentar sola un par de veces.")
    else:
        L.append(f"Anunciante: **{_md(an.get('nombre') or '?', 90)}**. En la Biblioteca de Meta se ven {an.get('anuncios_vistos', '?')} anuncios suyos" + (" (hay más, que no se leyeron)" if an.get("hay_mas") else "")
                 + (" (solo se pudo leer el anuncio del enlace)" if an.get("solo_uno") else "") + f". Se escogieron los {len(st['referencias'])} más antiguos con video, uno por creativo (primero los que siguen en el aire).")
        if an.get("tipo_enlace") == "pagina":
            L.append(f"Se leyó la página del anunciante que está en la Fábrica, con sus filtros: {_filtros_en_palabras(an.get('filtros') or {}) or 'los que traía el enlace'}.")
        elif an.get("amplia"):
            L.append(f"A partir del anuncio del enlace se halló al anunciante y se leyó su página en la Biblioteca: {_filtros_en_palabras(an.get('filtros') or {})}.")
        L.append("")
        L.append("| # | En el aire desde | Días | Audio | Texto escrito encima | ¿Sirve de base? |")
        L.append("|---|---|---|---|---|---|")
        ev = {e["ad_id"]: e for e in dec.get("evaluaciones", [])}
        for r in st["referencias"]:
            a = r.get("analisis") or {}
            e = ev.get(r["ad_id"])
            if not r.get("video"):
                sirve = "sin video: " + _md(r.get("motivo_sin_video"), 80)
            elif e is None:
                sirve = "no se pudo estudiar"
            elif e["sirve"]:
                sirve = "sí" + (f" (ojo: {_md('; '.join(e['avisos']), 100)})" if e["avisos"] else "")
            else:
                sirve = "no: " + _md("; ".join(e["motivos_no"]), 140)
            L.append(f"| REF{r['n']} | {_fecha_corta(r.get('inicio'))}{'' if r.get('activo') else ' (apagado)'} | {r.get('dias_en_aire') if r.get('dias_en_aire') is not None else '?'} | "
                     f"{orig.nombre_idioma(a.get('idioma') or '') if a.get('idioma') else '?'} | {str(a.get('texto_pct')) + ' % del video' if a.get('texto_pct') is not None else '?'} | {sirve} |")
        L.append("")
        L.append("Lo que dice cada uno al empezar (sirve para rehacer el gancho):")
        for r in st["referencias"]:
            ini = (r.get("analisis") or {}).get("texto_inicio") or r.get("copy_inicio") or ""
            L.append(f"- REF{r['n']}: «{_corto(ini, 200)}»" if ini else f"- REF{r['n']}: (sin texto)")
        L.append("")
        L.append(f"Los videos, el texto de cada anuncio y lo que dicen (transcripción) están en la carpeta **{d.get('referencia_nombre') or CARPETA_REFERENCIA}**.")
    L.append("")
    L.append("## 2. ¿Se puede usar el original?")
    L.append(dec.get("frase", "Todavía no se decidió."))
    if dec.get("evaluaciones"):
        L.append("Se pide que el audio esté en español y que al menos el 70 % del video no tenga texto escrito encima; además la receta de la oferta debe permitir usar cortes del anuncio de referencia (hoy dice: "
                 + ("«también cortes del anuncio de referencia»" if info["material"] == "referencia" else "«solo material propio»") + ").")
    L.append("")
    L.append("## 3. Qué se buscó en TikTok")
    if st["consultas"]:
        por, visto = br_.get("por_consulta", {}), br_.get("busquedas", {})
        for q in st["consultas"]:
            c, n = q["consulta"], por.get(q["consulta"], 0)
            if not br_:
                L.append(f"- «{c}»")
            elif c not in visto:
                L.append(f"- «{c}»: no alcanzó a buscarse en esta corrida (se acabaron los pedidos de búsqueda); queda para la próxima")
            elif visto[c] == 0:
                L.append(f"- «{c}»: TikTok no tiene lista pública para esa frase")
            else:
                L.append(f"- «{c}»: TikTok mostró {visto[c]} videos" + (f"; {n} quedaron en B-rolls" if n else "; ninguno quedó en B-rolls (traían texto encima, no hablaban del tema o ya habían llegado por otra frase)"))
        for k, v in visto.items():
            if k not in {q["consulta"] for q in st["consultas"]}:
                n = por.get(k, 0)
                L.append(f"- «{_md(k, 60)}» (palabra relacionada que sugirió TikTok): " + (f"mostró {v} videos" + (f"; {n} quedaron en B-rolls" if n else "; ninguno quedó") if v else "sin lista pública"))
        L.append("Las frases salen de reglas fijas con el tema, la promesa de la oferta y lo que dicen los anuncios originales (sin inteligencia artificial). Si alguna no te sirve, avísale a Claude para cambiarla.")
        if st.get("filtro_desc"):
            L.append("Para no traer videos de otra cosa, solo pasan los que mencionan " + " o ".join(f"«{_md(p, 20)}…»" for p in st["filtro_desc"]) + " en su título o descripción de TikTok.")
        if an.get("nombre"):
            L.append(f"Tampoco se bajan los videos de la cuenta de TikTok del propio anunciante original ({_md(an['nombre'], 60)}): es su material promocional.")
    else:
        L.append("No se pudo sacar ninguna frase de búsqueda del texto disponible.")
    L.append("")
    L.append("## 4. B-rolls conseguidos")
    if br_:
        L.append(f"- Videos nuevos bajados de TikTok: **{br_.get('bajados', 0)}**; descartados por traer texto escrito encima: {br_.get('descartados_texto', 0)}; subidos a la carpeta B-rolls: **{br_.get('subidos', 0)}**.")
        L.append(f"- En total esta oferta ya tiene {br_.get('total_en_registro', 0)} video(s) buenos en B-rolls. La corrida semanal sigue trayendo más mientras la oferta tenga anuncios por hacer.")
        for p in br_.get("problemas", []):
            L.append(f"- Aviso: {_md(p, 160)}")
    else:
        L.append("Todavía no se han bajado b-rolls para esta oferta.")
    L.append("")
    L.append("## 5. Lo que no se pudo o está pendiente")
    pend = [f"- {nombre}: {_md(p.get('nota'), 200)}" for nombre, p in st["pasos"].items() if p["estado"] == "error"]
    pend += [f"- REF{r['n']}: {_md(r['error_analisis'], 200)}" for r in st["referencias"] if r.get("error_analisis")]
    if st["estado"] == "con-pendientes":
        pend.append("- Ya se reintentó varias veces y se dejó así: hace falta que una persona lo revise.")
    L += pend or ["Nada."]
    L.append("")
    L.append("## Carpetas")
    if d.get("brolls_id"):
        L.append(f"- B-rolls: {enlace_carpeta(d['brolls_id'])}")
    if d.get("referencia_id"):
        L.append(f"- {d.get('referencia_nombre') or CARPETA_REFERENCIA}: {enlace_carpeta(d['referencia_id'])}")
    if st.get("estudios"):
        L.append("")
        L.append("## Estudios anteriores (otro anunciante de la misma oferta)")
        for e in st["estudios"]:
            L.append(f"- {_md(e.get('anunciante') or '?', 90)} ({len(e.get('anuncios') or [])} anuncio(s)): carpeta **{_md(e.get('carpeta'), 40)}**" + (f" {enlace_carpeta(e['carpeta_id'])}" if e.get("carpeta_id") else "")
                     + f", hasta el {_fecha_corta(e.get('hasta'))}. Se conserva tal cual.")
    L.append("")
    L.append("_Los videos de TikTok y los anuncios originales son de sus creadores: solo se usan en anuncios los que se tenga derecho a usar._")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ vigilar la cola de la Fabrica
def vigilar(cfg: dict, deps: Dependencias, dash: Dash, drive: da.Drive | None, log=print, solo: str = "", simular: bool = False, sin_brolls: bool = False, solo_cola: bool = False) -> int:
    fc = config_fabrica(cfg)
    try:
        pedidos = dash.pedidos()
    except ErrorDash as e:
        log(f"ERROR leyendo la cola de la Fabrica: {e}")
        return SALIDA_RED if e.reintentar else SALIDA_CONFIG
    proyectos: dict[str, dict] = {}
    for p in pedidos:
        proyectos.setdefault(p["proyecto"], p)
    if not simular and not solo_cola:
        cambios = sincronizar_activas(set(proyectos))
        if cambios:
            log(f"{cambios} oferta(s) registrada(s) cambiaron de activa a cerrada (o al reves) segun sus pedidos abiertos")
    log(f"Fabrica: {len(pedidos)} pedido(s) abierto(s) de {len(proyectos)} oferta(s)" + (" [SOLO LA COLA: no se hace nada]" if solo_cola else " [SIMULACION]" if simular else ""))
    ahora = deps.ahora()
    cola, hechas = [], []
    for proy, p in proyectos.items():
        if solo and proy != solo:
            continue
        st = leer_estado(proy)
        ya = bool(st and (st["estado"] in ("completo", "con-pendientes") or (st["proximo_intento"] and st["proximo_intento"] > ahora.isoformat(timespec="seconds"))))
        (hechas if ya else cola).append((proy, p, st, None))
    if solo_cola:
        log(f"  {len(cola)} oferta(s) por trabajar" + "".join(f"\n    - {_txt(p['nombre'] or proy, 60)} ({proy})" for proy, p, _, _ in cola))
        return SALIDA_BIEN
    codigo = SALIDA_BIEN
    # Las que ya estaban hechas (o esperando un reintento): si les cambiaron el enlace del anuncio original, se vuelven a estudiar. Mirarlo cuesta una lectura de la orden por oferta.
    for proy, p, st, _ in hechas:
        try:
            orden = dash.orden(proy, p["anuncio"])
        except ErrorDash as e:
            log(f"  {proy}: no pude mirar si le cambiaron el enlace del anuncio original ({e})")
            codigo = peor(codigo, SALIDA_RED if e.reintentar else SALIDA_CONFIG)
            continue
        if leer_orden(orden)["link_anuncio"] != (st.get("info") or {}).get("link_anuncio", ""):
            cola.append((proy, p, st, orden))
    if not cola:
        if codigo == SALIDA_BIEN:
            log("  Nada nuevo que hacer.")
        _anotar_vigilancia(deps, len(pedidos), len(proyectos), 0, codigo)
        return codigo
    hechos = 0
    for proy, p, st, orden in cola[: int(fc["proyectos_por_vuelta"])]:
        log(f"\n=== {p['nombre'] or proy} ({proy}) ===")
        if orden is None:
            try:
                orden = dash.orden(proy, p["anuncio"])
            except ErrorDash as e:
                log(f"  ERROR leyendo la orden: {e}")
                codigo = peor(codigo, SALIDA_RED if e.reintentar else SALIDA_CONFIG)
                continue
        info = leer_orden(orden)
        if info["proyecto"] != proy or not br.RE_PRODUCTO.match(info["slug"]):
            log(f"  La orden no es de este proyecto o trae un nombre de producto raro ({info['slug']!r}): no se hace nada")
            codigo = peor(codigo, SALIDA_PARCIAL)
            continue
        if any(o.get("producto") == info["slug"] and o.get("origen") != "fabrica" for o in cfg.get("ofertas", [])):
            log(f"  El producto '{info['slug']}' ya esta en ofertas.json (se maneja a mano): la Fabrica no lo toca")
            continue
        cambio = bool(st) and (st.get("info") or {}).get("link_anuncio", "") != info["link_anuncio"]
        st = st or estado_nuevo(info, ahora)
        tub = Tuberia(fc, deps, drive, log, simular=simular, sin_brolls=sin_brolls)
        if cambio and simular:
            log("  (simulacion) le cambiaron el enlace del anuncio original: se volveria a estudiar")
            continue                            # sin tocar el estado: la corrida de verdad tiene que ver el cambio
        if cambio:
            tub.archivar_estudio(st)            # antes de poner la info nueva: necesita saber con que enlace se estudio
        st["info"] = info                       # el dash manda: nombre, enlace y receta pueden haber cambiado
        st["nombre"] = info["nombre"] or st["nombre"]
        try:
            codigo = peor(codigo, tub.procesar(st))
        except da.ErrorPermiso as e:
            log(f"SIN PERMISO DE DRIVE: {e}")
            return SALIDA_DRIVE
        hechos += 1
    _anotar_vigilancia(deps, len(pedidos), len(proyectos), hechos, codigo)
    return codigo


def _anotar_vigilancia(deps: Dependencias, pedidos: int, proyectos: int, hechos: int, codigo: int, nota: str = "") -> None:
    try:
        _escribir_json(carpeta_estado() / "_ultima_vigilancia.json", {"en": deps.ahora().isoformat(timespec="seconds"), "pedidos_abiertos": pedidos, "ofertas": proyectos, "procesadas": hechos, "codigo": codigo,
                                                                      **({"nota": nota} if nota else {})})
    except OSError:
        pass


def limpiar_referencias(dias: int, log=print) -> float:
    """Borra del servidor los videos de REFERENCIA de ofertas ya terminadas con mas de `dias` dias (lo demas, que pesa poco, se queda). Devuelve los MB liberados."""
    liberado, ahora = 0, time.time()
    carpeta = carpeta_estado()
    for ruta in carpeta.glob("*.json") if carpeta.is_dir() else []:
        if ruta.name.startswith("_") or ruta.name.endswith(".informe.json"):
            continue
        try:
            st = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if st.get("estado") not in ("completo", "con-pendientes") or not st.get("pasos", {}).get("referencia_en_drive", {}).get("estado") == "ok":
            continue
        slug = str(st.get("slug") or "")
        if not br.RE_PRODUCTO.match(slug):
            continue
        for v in (bt.PUBLIC / "productos" / slug / "referencia").glob("REF[1-6]_referencia.mp4"):
            if v.is_file() and ahora - v.stat().st_mtime > dias * 86400:
                liberado += v.stat().st_size
                v.unlink()
    if liberado:
        log(f"Se borraron del servidor {liberado / 1e6:.0f} MB de videos de referencia viejos que ya estan en Drive.")
    return liberado / 1e6


# ------------------------------------------------------------------ linea de comandos
def info_manual(a: argparse.Namespace) -> dict:
    enlace = a.enlace if rm.es_enlace_meta(a.enlace) else ""
    return {"proyecto": f"manual-{a.slug}"[:48], "nombre": _txt(a.nombre, 100), "slug": a.slug, "paises": [], "link_anuncio": enlace, "link_pagina": "", "promesa": _txt(a.promesa, 400), "promesa_corta": "",
            "vinetas": [], "ebooks": [], "material": a.material, "gancho": {"modo": "nuevo-modelado", "segundos": 6}, "anunciante": "", "tema": _txt(a.tema, 400), "entregable": "", "angulo": "", "idioma_original": ""}


def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)
    for nombre in ("vigilar", "manual"):
        p = sub.add_parser(nombre)
        p.add_argument("--config", help="ofertas.json (por defecto el de /opt/broll-tiktok)")
        p.add_argument("--simular", action="store_true", help="mira y dice que haria; no escribe en Drive ni registra la oferta")
        p.add_argument("--sin-drive", action="store_true", dest="sin_drive", help="no usa Drive (para probar los pasos de analisis)")
        p.add_argument("--sin-brolls", action="store_true", dest="sin_brolls", help="no baja b-rolls de TikTok")
        p.add_argument("--sin-subir", action="store_true", dest="sin_subir", help="no sube nada a Drive (pero si crea las carpetas)")
    sub.choices["vigilar"].add_argument("--solo", default="", help="solo este proyecto de la Fabrica")
    sub.choices["vigilar"].add_argument("--solo-cola", action="store_true", dest="solo_cola", help="solo lee la cola de la Fabrica y dice que haria: no baja, no escribe y no necesita Drive (sirve para probar la llave del dash)")
    m = sub.choices["manual"]
    m.add_argument("--enlace", required=True, help="enlace del anuncio original en la Biblioteca de Meta, o el de la pagina de un anunciante (con sus filtros)")
    m.add_argument("--rehacer", action="store_true", help="vuelve a estudiar la oferta aunque el enlace sea el mismo (el mismo anunciante sigue en la misma carpeta de REFERENCIA)")
    m.add_argument("--nombre", required=True)
    m.add_argument("--slug", required=True, help="nombre corto del producto (minusculas, numeros, guion)")
    m.add_argument("--tema", default="")
    m.add_argument("--promesa", default="")
    m.add_argument("--material", choices=("propio", "referencia"), default="propio")
    sub.add_parser("estado")
    return ap


def estado_fabrica() -> int:
    carpeta = carpeta_estado()
    archivos = sorted(p for p in carpeta.glob("*.json") if not p.name.startswith("_") and not p.name.endswith(".informe.json")) if carpeta.is_dir() else []
    if not archivos:
        print("Todavia no hay ninguna oferta de la Fabrica registrada (estado/fabrica/).")
    for p in archivos:
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pasos = " ".join(f"{k}={v['estado']}" for k, v in st.get("pasos", {}).items())
        print(f"- {st.get('slug')} ({st.get('nombre')}): {st.get('estado')} | vueltas {st.get('vueltas')} | {pasos}")
    try:
        v = json.loads((carpeta / "_ultima_vigilancia.json").read_text(encoding="utf-8"))
        print(f"Ultima vigilancia: {v['en']} | {v['pedidos_abiertos']} pedido(s) abierto(s), {v['procesadas']} oferta(s) trabajada(s) | salida {v['codigo']}" + (f" | {v['nota']}" if v.get("nota") else ""))
    except (OSError, ValueError, KeyError):
        pass
    return 0


def correr(args, *, deps: Dependencias | None = None, dash: Dash | None = None, drive: da.Drive | None = None, ruta_config: Path | None = None, log=print) -> int:
    deps = deps or Dependencias()
    try:
        deps.ruta_config = Path(args.config) if args.config else ruta_config
        cfg = br.cargar_config(deps.ruta_config or bt.RAIZ / "ofertas.json")
        config_fabrica(cfg)
    except (br.ErrorConfig, ErrorConfig) as e:
        log(f"ERROR: {e}")
        return SALIDA_CONFIG
    if args.comando == "vigilar" and dash is None:
        try:
            dash = Dash(*leer_secreto_dash())
        except ErrorConfig as e:
            log(f"La Fabrica no se vigila todavia: {e}")
            _anotar_vigilancia(deps, 0, 0, 0, SALIDA_BIEN, nota="no se vigila: falta la llave del dash (secretos/dash.env)")
            return SALIDA_BIEN
    if drive is None and not args.sin_drive and not getattr(args, "solo_cola", False):
        try:
            drive = da.Drive(da.cargar_credenciales(), registrar=log)
        except da.ErrorPermiso as e:
            log(f"SIN PERMISO DE DRIVE: {e}")
            return SALIDA_DRIVE
        except da.ErrorDrive as e:
            log(f"ERROR hablando con Drive: {e}")
            return SALIDA_RED
    if args.comando == "vigilar":
        codigo = vigilar(cfg, deps, dash, drive, log, solo=args.solo, simular=args.simular, sin_brolls=args.sin_brolls, solo_cola=args.solo_cola)
        if codigo != SALIDA_DRIVE and not args.simular and not args.solo_cola:
            limpiar_referencias(config_fabrica(cfg)["dias_guardar_referencia"], log)
        return codigo
    # manual
    if not br.RE_PRODUCTO.match(args.slug):
        log("ERROR: --slug debe ser minusculas, numeros, guion o guion bajo (hasta 40)")
        return SALIDA_CONFIG
    info = info_manual(args)
    previo = leer_estado(info["proyecto"])
    st = previo or estado_nuevo(info, deps.ahora())
    tub = Tuberia(config_fabrica(cfg), deps, drive, log, simular=args.simular, sin_brolls=args.sin_brolls, sin_subir=args.sin_subir)
    if previo and (args.rehacer or (previo.get("info") or {}).get("link_anuncio", "") != info["link_anuncio"]):
        if args.simular:
            log("(simulacion) se volveria a estudiar la oferta")
            return SALIDA_BIEN                  # sin tocar el estado: la corrida de verdad tiene que ver el cambio
        tub.archivar_estudio(st)
    st["info"], st["nombre"] = info, info["nombre"]
    log(f"=== {info['nombre']} ({info['slug']}) [manual] ===")
    try:
        return tub.procesar(st)
    except da.ErrorPermiso as e:
        log(f"SIN PERMISO DE DRIVE: {e}")
        return SALIDA_DRIVE


def main() -> None:
    configurar_consola()
    args = construir_parser().parse_args()
    if args.comando == "estado":
        sys.exit(estado_fabrica())
    candado = None
    if fcntl is not None:
        (bt.RAIZ / "estado").mkdir(parents=True, exist_ok=True)
        candado = open(bt.RAIZ / "estado" / ".candado-python", "w")
        try:
            fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Ya hay una corrida en curso (b-roll o Fabrica): esta no arranca.")
            sys.exit(0)
    try:
        sys.exit(correr(args))
    except KeyboardInterrupt:
        print("Interrumpido.")
        sys.exit(130)
    finally:
        if candado is not None:
            candado.close()


if __name__ == "__main__":
    main()
