"""Corredor de anuncios del VPS: toma los pedidos de la Fabrica y produce el anuncio terminado SIN el PC de nadie y sin gastar tokens de Claude.

Pedido del dueno (2026-10-23): «usemos todo desde el VPS» (el anuncio completo corre en el servidor). Cada pasada (la lanza anuncios-corredor.timer o `corredor-ctl correr`):
  1. lee la cola de la Fabrica en el dash. Solo toma pedidos en estado «pedido» (nadie los ha tocado) de tipo locucion + videos, SIN correcciones del equipo (esas las hace una persona con
     Claudia), de una oferta cuyos anuncios una persona mando arrancar en /fabrica («Crear toda la oferta»: el campo «arranque» del pedido; corredor.json: exigir_arranque), con ebooks listos,
     un video de referencia, la carpeta de la oferta en Drive con b-rolls y un OK de gasto en la receta (aprobacion.topeUsd, el interruptor del dueno).
     Lo que ya era suyo y quedo a medias, lo retoma (aunque quiten la orden de arranque: lo ya tomado sigue).
  2. por cada pedido que le toca, una etapa tras otra (cada una con tiempo maximo y anotada en estado/pedidos/<proyecto>_<AD>.json, asi una pasada cortada se retoma donde iba):
       espejo     baja de Drive B-rolls/ y REFERENCIA/ de la oferta (y la musica)          (espejo_drive.py, solo lectura)
       traer      fabrica.py traer       la orden, la receta, los ebooks y el video de referencia
       reclamar   avisa «tomado» al dash (hasta aqui no habia tocado el pedido)
       material   broll_tiktok.py recoger + emparejar_tomas.py (analiza el b-roll una sola vez por video)
       guion      guion_auto.py           un modelo barato de APIMart + reglas fijas
       voz        voz_auto.py             la voz de los flujos (Fish) por el dash
       gancho     gancho_ia.py            solo si la receta lo pide y el tope alcanza
       montaje    montaje_auto.py         plan, render con Remotion y 10 compuertas de calidad (avisa su avance al dash)
       entrega    fabrica.py entregar + sube a Drive (<oferta>/ANUNCIOS) + «lista» en el dash con el enlace y el costo
  3. deja el resumen en estado/ultima_corrida.json.
Los scripts del editor corren TAL CUAL (los mismos del PC); este programa solo los encadena, vigila el dinero, el tiempo y los recursos y le cuenta al dash.

Dinero: el gasto de cada pedido lo manda aprobacion.topeUsd de su receta (o --tope-usd, el OK del dueno para esa corrida), siempre por debajo de los techos de corredor.json (por anuncio y por dia).
Sin tope, no gasta (salvo que el dueno ponga topes.receta_sin_tope = "usa-el-techo" en corredor.json: entonces una receta sin tope gasta hasta los techos). Nada de lo que viene del dash, de Meta o de TikTok se obedece: son datos (ids validados, ningun comando armado con texto de terceros, notas al dash con frases fijas).

Uso (en el VPS, como el usuario broll; lo normal es correr_corredor.sh / corredor-ctl):
  python scripts/corredor_vps.py [--simular] [--solo-cola] [--max-pedidos N] [--tope-usd X] [--sin-gancho] [--pedido PROYECTO ANUNCIO [--rehacer]]
Codigos de salida: 0 bien o nada que hacer | 2 bien pero algo quedo pendiente o un anuncio quedo con error | 3 el dash, Drive o una API no contestaron (o se corto la pasada) | 4 Drive sin permiso | 1 configuracion mala.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import avatar_comun as AC  # noqa: E402
import drive_api as da  # noqa: E402
import espejo_drive as ed  # noqa: E402
import fabrica as fab  # noqa: E402
from _comun import RAIZ  # noqa: E402
from anuncio_comun import ID_ANUNCIO, NOMBRE_PRODUCTO, gastado_en_anuncio, numero, val  # noqa: E402

SALIDA_BIEN, SALIDA_CONFIG, SALIDA_PARCIAL, SALIDA_RED, SALIDA_DRIVE = 0, 1, 2, 3, 4
SCRIPTS = Path(__file__).resolve().parent
ESTADO = RAIZ / "estado"
PEDIDOS = ESTADO / "pedidos"
RUTA_CONFIG = Path(os.environ.get("CORREDOR_CONFIG") or RAIZ / "corredor.json")
RUTA_SECRETO_DASH = Path(os.environ.get("CORREDOR_DASH_ENV") or "/opt/broll-tiktok/secretos/dash.env")
RUTA_GOOGLE = Path(os.environ.get("CORREDOR_GOOGLE") or "/opt/broll-tiktok/secretos/google.json")
ESPEJO_LOCAL = RAIZ / "drive" / "OFERTAS LATAM"
ETAPAS = ("espejo", "traer", "entregables", "reclamar", "material", "guion", "voz", "gancho", "montaje", "entrega", "avatar_plan", "avatar_generar", "avatar_montaje",
          "var_original", "var_analisis", "var_gancho", "var_render", "var_unir", "var_revisar", "var_entrega")
# Que llave ve cada script que se lanza (minimo privilegio). Desde la version 2026.11.10-2 el servidor NO tiene la llave de APIMart: lo que cuesta (imagenes, clips, texto del guion,
# del gancho y de las escenas del avatar) lo pide apimart.py / apimart_chat.py por la puerta del dash con la llave del editor, y el dash aplica el tope por anuncio, el del dia y el
# libro de gasto. El guion, el gancho y el avatar (plan y generar) ven la llave del dash; el b-roll (ffmpeg, onnx, OCR) no ve ninguna, y la entrega tampoco (el aviso de «lista» lo
# manda este programa, en su propio proceso). Una etapa que no este aqui no ve ninguna.
LLAVES_POR_ETAPA = {"traer": ("dash",), "entregables": (), "material": (), "guion": ("dash",), "voz": ("dash",), "gancho": ("dash",), "montaje": ("dash",), "entrega": (),
                    "avatar_plan": ("dash",), "avatar_generar": ("dash",), "avatar_montaje": ("dash",),
                    # variaciones hechas por el servidor (variaciones_vps.py): solo la etapa del inicio nuevo (voz, imagenes o clip) gasta y ve la llave del dash
                    "var_original": (), "var_analisis": (), "var_gancho": ("dash",), "var_render": (), "var_unir": (), "var_revisar": (), "var_entrega": (), "indice": ()}
VARIABLE_DE_LLAVE = {"dash": "DASH_CLAVE_EDITOR"}
# Lo que NINGUN script lanzado ve, aunque el entorno de este programa lo traiga (una unidad de systemd vieja con LoadCredential= o una variable que quedo puesta a mano): la llave de
# APIMart ya no se usa en el servidor y la carpeta de credenciales de systemd era donde llegaba.
NUNCA_A_LOS_HIJOS = ("APIMART_API_KEY", "CREDENTIALS_DIRECTORY")
NOTA_TOMADO = "El servidor (VPS) tomó el pedido y lo está haciendo solo; no lo tomes en el PC salvo que lo pidas a propósito."

DEFECTOS = {
    "carpeta_ofertas": "", "biblioteca_musical": "", "tipos_permitidos": ["tiktok"], "ofertas_permitidas": "todas", "pedidos_por_corrida": 2,
    "topes": {"usd_por_anuncio": 0.30, "usd_por_dia": 1.00, "receta_sin_tope": "no-gasta"}, "recursos": {"ram_disponible_min_gb": 4.5, "carga_5min_max": 1.5, "disco_libre_min_gb": 10.0},
    "tiempos_max_min": {"espejo": 20, "traer": 8, "entregables": 20, "material": 60, "guion": 12, "voz": 15, "gancho": 20, "montaje": 90, "entrega": 20, "avatar_plan": 15, "avatar_generar": 120, "avatar_montaje": 60,
                        "indice": 15, "var_original": 25, "var_analisis": 15, "var_gancho": 45, "var_render": 30, "var_unir": 40, "var_revisar": 20, "var_entrega": 20},
    "ventana_silencio": {"desde": "", "hasta": ""}, "exigir_arranque": True,
    # Variaciones hechas por el servidor (2026-11-13, /tablero › Variaciones › «Hacerla en el servidor»; variaciones_vps.py). APAGADAS: el dueño decide activo, tope, modo y si Beto la revisa.
    # Modo por defecto «fish» (voz de los flujos + imagenes): el «clip» pone palabras nuevas en la boca de la persona del original y solo se usa con las ofertas de `clip_ofertas`.
    "variaciones": {"activo": False, "tope_usd": 0.0, "modo": "fish", "voz": "", "imagenes": 2, "revision_beto": True, "carpeta": "VARIACIONES DEL SERVIDOR", "indice_horas": 12,
                    "corte_min_seg": 4.0, "corte_max_seg": 10.0, "clip_ofertas": []}, "gancho_ia": "segun-receta", "dias_guardar_trabajo": 14, "min_brolls": 6, "pasada_max_min": 170, "minutos_para_empezar_pedido": 45, "intentos_por_pedido": 2,
}


class ErrorConfig(Exception):
    """Falta o esta mal algo que pone una persona (llave del dash, permiso de Drive, corredor.json)."""


class ErrorDash(Exception):
    def __init__(self, mensaje: str, config: bool = False):
        super().__init__(mensaje)
        self.config = config


class FallaDeEtapa(Exception):
    """Una etapa no pudo. `espera` = no es culpa del pedido (falta algo que otro puede traer): se deja como estaba y se vuelve a mirar en otra pasada."""

    def __init__(self, motivo: str, espera: bool = False):
        super().__init__(motivo)
        self.motivo = motivo
        self.espera = espera


class Pausado(Exception):
    pass


class Cortado(Exception):
    pass


class Silencio(Exception):
    """Llego la ventana de silencio (corredor.json: ventana_silencio): se detiene entre etapas y sigue cuando termine."""


# ------------------------------------------------------------------ configuracion
def cargar_config(ruta: Path | None = None) -> dict:
    """corredor.json con sus valores por defecto, validado. Lanza ErrorConfig si algo no sirve (una clave que no se conoce tambien: casi siempre es un error de escritura)."""
    ruta = ruta or RUTA_CONFIG
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErrorConfig(f"no puedo leer {ruta} ({e.__class__.__name__})") from None
    if not isinstance(crudo, dict):
        raise ErrorConfig(f"{ruta} debe ser un objeto JSON")
    c = json.loads(json.dumps(DEFECTOS))
    errores = []
    for k, v in crudo.items():
        if k.startswith("_"):
            continue
        if k not in c:
            errores.append(f"clave desconocida: '{k}'")
        elif isinstance(c[k], dict):
            if not isinstance(v, dict):
                errores.append(f"'{k}' debe ser un objeto")
                continue
            for k2, v2 in v.items():
                if k2.startswith("_"):          # una nota dentro de un bloque (variaciones._nota) no es configuracion
                    continue
                if k2 not in c[k]:
                    errores.append(f"clave desconocida: '{k}.{k2}'")
                else:
                    c[k][k2] = v2
        else:
            c[k] = v

    def num(ruta_clave: str, valor, minimo: float, maximo: float, entero: bool = False) -> None:
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not (minimo <= valor <= maximo) or (entero and int(valor) != valor):
            errores.append(f"'{ruta_clave}' debe ser un numero{' entero' if entero else ''} entre {minimo:g} y {maximo:g}")

    if not da.id_de_carpeta(str(c["carpeta_ofertas"])):
        errores.append("'carpeta_ofertas' debe ser el enlace de la carpeta OFERTAS LATAM de Drive")
    if c["biblioteca_musical"] and not da.id_de_carpeta(str(c["biblioteca_musical"])):
        errores.append("'biblioteca_musical' debe ser el enlace de la carpeta BIBLIOTECA MUSICAL de Drive (o ir vacia)")
    if not isinstance(c["tipos_permitidos"], list) or not c["tipos_permitidos"] or any(t not in ("tiktok", "ia") for t in c["tipos_permitidos"]) or len(set(c["tipos_permitidos"])) != len(c["tipos_permitidos"]):
        errores.append("'tipos_permitidos' es una lista con \"tiktok\" (locucion + videos) y/o \"ia\" (avatar con IA), sin repetir")
    if c["ofertas_permitidas"] != "todas" and (not isinstance(c["ofertas_permitidas"], list) or any(not isinstance(s, str) or not NOMBRE_PRODUCTO.match(s) for s in c["ofertas_permitidas"])):
        errores.append("'ofertas_permitidas' debe ser \"todas\" o una lista de nombres cortos de oferta")
    num("pedidos_por_corrida", c["pedidos_por_corrida"], 1, 5, True)
    num("topes.usd_por_anuncio", c["topes"]["usd_por_anuncio"], 0, 2)
    num("topes.usd_por_dia", c["topes"]["usd_por_dia"], 0, 10)
    if c["topes"]["receta_sin_tope"] not in ("no-gasta", "usa-el-techo"):
        errores.append("'topes.receta_sin_tope' es \"no-gasta\" (sin tope en la receta no gasta) o \"usa-el-techo\" (gasta hasta los techos de arriba)")
    num("recursos.ram_disponible_min_gb", c["recursos"]["ram_disponible_min_gb"], 0.5, 6)
    num("recursos.carga_5min_max", c["recursos"]["carga_5min_max"], 0.2, 4)
    num("recursos.disco_libre_min_gb", c["recursos"]["disco_libre_min_gb"], 1, 80)
    for etapa, minutos in c["tiempos_max_min"].items():
        num(f"tiempos_max_min.{etapa}", minutos, 1, 240, True)
    vs = c["ventana_silencio"]
    if (any(not isinstance(vs.get(k), str) for k in ("desde", "hasta")) or bool(vs["desde"]) != bool(vs["hasta"]) or any(vs[k] and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", vs[k]) for k in ("desde", "hasta"))
            or (vs["desde"] and vs["desde"] == vs["hasta"])):
        errores.append("'ventana_silencio' lleva \"desde\" y \"hasta\" como HH:MM (hora de Bogota) y distintas, o las dos vacias para no tener ventana")
    if not isinstance(c["exigir_arranque"], bool):
        errores.append("'exigir_arranque' es true o false")
    if c["gancho_ia"] not in ("segun-receta", "nunca"):
        errores.append("'gancho_ia' es \"segun-receta\" o \"nunca\"")
    num("dias_guardar_trabajo", c["dias_guardar_trabajo"], 1, 90, True)
    num("min_brolls", c["min_brolls"], 1, 50, True)
    num("pasada_max_min", c["pasada_max_min"], 10, 175, True)
    num("minutos_para_empezar_pedido", c["minutos_para_empezar_pedido"], 5, 120, True)
    num("intentos_por_pedido", c["intentos_por_pedido"], 1, 5, True)
    import variaciones_vps as VV

    VV.validar_config(c["variaciones"], errores, num)
    if errores:
        raise ErrorConfig(f"{ruta}: " + "; ".join(errores))
    return c


def leer_secreto_dash(ruta: Path | None = None) -> tuple[str, str]:
    """(direccion, llave) del dash desde secretos/dash.env (la pone scripts\\Setup-ClaveEditorVps.ps1). Nunca imprime la llave."""
    ruta = ruta or RUTA_SECRETO_DASH
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
    if any(ch.isspace() for ch in llave) or len(llave) < 16:
        raise ErrorConfig(f"DASH_CLAVE_EDITOR de {ruta} esta incompleta (debe ir entera, en una sola linea)")
    if not (url.startswith("https://") or url.startswith(("http://127.0.0.1:", "http://localhost:"))):
        raise ErrorConfig("DASH_URL debe empezar con https:// (la llave no se manda por una direccion en claro)")
    return url, llave


# ------------------------------------------------------------------ registro (con las llaves tapadas)
PATRONES_SECRETOS = [
    (re.compile(r"(ya29\.|1//0)[A-Za-z0-9._-]+"), r"\1***"),
    (re.compile(r"(DASH_CLAVE_EDITOR=|APIMART_API_KEY=)\S+"), r"\1***"),
    (re.compile(r"(x-clave-editor['\": ]+)[^\s'\",}]+", re.I), r"\1***"),
    (re.compile(r"(Bearer )[A-Za-z0-9._~+/-]+=*"), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-***"),
]


class Registro:
    """Escribe lineas con la hora y SIN llaves: tapa las que se le dieron (valores exactos) y las formas conocidas de una llave."""

    def __init__(self, secretos=(), emitir=print, ahora=datetime.now):
        self.secretos = [s for s in secretos if s and len(s) >= 8]
        self.emitir = emitir
        self.ahora = ahora
        self._cerrojo = threading.Lock()

    def agregar_secreto(self, s: str) -> None:
        if s and len(s) >= 8 and s not in self.secretos:
            self.secretos.append(s)

    def limpiar(self, texto: str) -> str:
        t = str(texto)
        for s in self.secretos:
            t = t.replace(s, "***")
        for patron, cambio in PATRONES_SECRETOS:
            t = patron.sub(cambio, t)
        return t

    def __call__(self, texto: str = "") -> None:
        with self._cerrojo:
            for linea in str(texto).splitlines() or [""]:
                self.emitir(f"[{self.ahora().strftime('%H:%M:%S')}] {self.limpiar(linea)[:600]}")
                sys.stdout.flush()


# ------------------------------------------------------------------ procesos hijos y recursos del equipo
@dataclass
class Corrida:
    codigo: int | None             # None = se agoto el tiempo
    tiempo_agotado: bool = False
    cortado: bool = False
    ultimas: list = field(default_factory=list)


def correr_proceso(cmd: list[str], env: dict, timeout_s: float, salida, cortar=lambda: False, cwd: Path | None = None) -> Corrida:
    """Corre un comando (sin shell), pasa cada linea de lo que imprime a `salida` y lo mata (a el y a sus hijos) si pasa del tiempo o si se pide cortar."""
    p = subprocess.Popen(cmd, cwd=str(cwd or RAIZ), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         bufsize=1, start_new_session=hasattr(os, "killpg"))
    ultimas: collections.deque = collections.deque(maxlen=40)

    def leer() -> None:
        for linea in p.stdout:  # type: ignore[union-attr]
            linea = linea.rstrip("\n")
            ultimas.append(linea)
            salida("    " + linea[:400])

    hilo = threading.Thread(target=leer, daemon=True)
    hilo.start()
    t0 = time.monotonic()
    agotado = cortado = False
    while True:
        try:
            p.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            pass
        if cortar():
            cortado = True
        elif time.monotonic() - t0 > timeout_s:
            agotado = True
        if agotado or cortado:
            _matar(p)
            break
    hilo.join(10)
    return Corrida(None if agotado else p.returncode, agotado, cortado, list(ultimas))


def _matar(p: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(p.pid, signal.SIGTERM)
        else:
            p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if hasattr(os, "killpg"):
                os.killpg(p.pid, signal.SIGKILL)
            else:
                p.kill()
            p.wait(timeout=10)
    except (ProcessLookupError, PermissionError):
        pass


def duracion_o_cero(ruta: Path) -> float:
    """Cuanto dura un audio (ffprobe), o 0 si no se puede saber: aqui un fallo no debe tumbar nada (a diferencia de _comun.duracion_seg, que sale del programa)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(ruta)], capture_output=True, text=True, timeout=60)
        return max(0.0, float(r.stdout.strip() or 0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def leer_recursos(ruta_disco: Path | None = None) -> dict:
    """{ram_gb (disponible), carga5, disco_gb (libre)} del servidor. Lo que no se pueda leer queda en None (y no frena nada)."""
    r: dict = {"ram_gb": None, "carga5": None, "disco_gb": None}
    try:
        for linea in open("/proc/meminfo", encoding="utf-8"):
            if linea.startswith("MemAvailable:"):
                r["ram_gb"] = int(linea.split()[1]) / 1048576
                break
        r["carga5"] = float(open("/proc/loadavg", encoding="utf-8").read().split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        st = os.statvfs(str(ruta_disco or RAIZ))
        r["disco_gb"] = st.f_bavail * st.f_frsize / 1073741824
    except (OSError, AttributeError):
        pass
    return r


class Dependencias:
    """Lo que se cambia en las pruebas (dash, Drive, procesos, hora, recursos)."""

    def __init__(self, **cambios):
        self.llamar_dash = None            # callable(metodo, ruta, cuerpo=None, **kw) -> dict; por defecto fabrica.llamar
        self.fabricar_drive = None         # callable(registrar) -> Drive; por defecto drive_api.Drive con secretos/google.json
        self.correr = correr_proceso
        self.recursos = leer_recursos
        self.ahora = datetime.now
        self.reloj = time.monotonic
        self.dormir = time.sleep
        self.en_pausa = lambda: (ESTADO / "PAUSA").exists()
        self.llamar_variaciones = None     # callable(metodo, sufijo, cuerpo) -> dict; por defecto /api/variacion-editor con la llave del editor (originales.DashVariaciones)
        for k, v in cambios.items():
            setattr(self, k, v)


# ------------------------------------------------------------------ estado de cada pedido (en disco, para retomar)
def ruta_estado(proyecto: str, anuncio: str, base: Path | None = None) -> Path:
    return (base or PEDIDOS) / f"{proyecto}_{anuncio}.json"


def leer_estado(proyecto: str, anuncio: str, base: Path | None = None) -> dict | None:
    try:
        d = json.loads(ruta_estado(proyecto, anuncio, base).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("proyecto") == proyecto and d.get("anuncio") == anuncio else None
    except (OSError, ValueError):
        return None


MAX_PDF_ENTREGABLES = 8          # un principal y hasta siete bonos: lo mismo que acepta el dash (pdfs: principal + bono-0..6)


def titulo_de_pdf(nombre: str) -> str:
    """«20_Desayunos_Navidenos.pdf» -> «20 Desayunos Navidenos»: el nombre del archivo sin extension y con espacios, en una sola linea (es texto de terceros: llega al guion como dato)."""
    t = re.sub(r"[_\-]+", " ", Path(str(nombre)).stem)
    t = re.sub(r"[^\w .,&()+']", "", t)
    return re.sub(r"\s+", " ", t).strip()[:120] or "Ebook"


def elegir_principal(pdfs: list[dict]) -> dict:
    """El PDF del que se saca la portada: el que dice «principal», «ebook» o «libro» en su nombre; si ninguno, el mas pesado (el de mas contenido). A igualdad, el de nombre mas alto."""
    claros = [x for x in pdfs if re.search(r"principal|ebook|libro", x["archivo"], re.I)]
    return max(claros or pdfs, key=lambda x: (x["bytes"], x["archivo"]))


def elegir_mockup(imagenes: list[dict]) -> dict | None:
    """El mockup de la oferta entre las imagenes de la carpeta de entregables: la que dice «mockup», «portada», «cover» o «caratula» en su nombre; si ninguna, la UNICA imagen que haya; si hay
    varias sin un nombre claro no se adivina (ninguna: la portada sale del PDF principal). A igualdad, la mas pesada."""
    claras = [x for x in imagenes if re.search(r"mockup|portada|cover|car[aá]tula", x["archivo"], re.I)]
    if claras:
        return max(claras, key=lambda x: (x["bytes"], x["archivo"]))
    return imagenes[0] if len(imagenes) == 1 else None


# El mockup es un archivo de un tercero: se convierte en un JPG de a lo mucho 2000 px en un proceso aparte (python -I, sin llaves y con tiempo maximo, como todas las etapas), con el fondo transparente en blanco.
CODIGO_MOCKUP_A_JPG = (
    "import os, sys\n"
    "from PIL import Image\n"
    "im = Image.open(sys.argv[1]); im.load()\n"
    "if im.mode in ('RGBA', 'LA', 'P'):\n"
    "    im = im.convert('RGBA'); f = Image.new('RGB', im.size, (255, 255, 255)); f.paste(im, mask=im.split()[-1]); im = f\n"
    "else:\n"
    "    im = im.convert('RGB')\n"
    "im.thumbnail((2000, 2000))\n"
    "im.save(sys.argv[2] + '.tmp', 'JPEG', quality=92)\n"
    "os.replace(sys.argv[2] + '.tmp', sys.argv[2])\n"
)


def guardar_json(ruta: Path, datos, ahora: datetime | None = None) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


def estado_nuevo(p: dict, ahora: datetime, corrida: str) -> dict:
    t = ahora.isoformat(timespec="seconds")
    return {"proyecto": p["proyecto"], "anuncio": p["anuncio"], "slug": p["slug"], "nombre": p.get("nombre", ""), "estado": "en-curso", "etapa": "", "etapas": {}, "intentos": 0,
            "gasto_usd": 0.0, "nota": "", "entrega": {}, "creado": t, "actualizado": t, "corrida": corrida}


@dataclass
class Evaluacion:
    tipo: str                       # 'listo' | 'espera' | 'no'
    motivo: str = ""
    orden: dict = field(default_factory=dict)
    oferta: dict | None = None
    receta: dict = field(default_factory=dict)
    tope: float = 0.0
    propio: bool = False            # ya era del corredor (se retoma)
    extra: dict = field(default_factory=dict)       # lo que se supo al evaluar: {"tipo": "tiktok" | "ia", "clips": N, "costo": US$} (los de avatar)
    aviso: str = ""                 # si no es vacio, UNA nota al dash (estado «error») con las cifras: hace falta una persona (el avatar, o un tope mas alto)


class Dash:
    """El dash visto por el corredor: las mismas llamadas que hace el editor en el PC (fabrica.llamar), con sus errores explicados."""

    def __init__(self, llamar=None):
        self._llamar = llamar or fab.llamar

    def _pedir(self, metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
        try:
            return self._llamar(metodo, ruta, cuerpo, fatal=False) if cuerpo is not None else self._llamar(metodo, ruta, fatal=False)
        except fab.ErrorDash as e:
            m = str(e)
            raise ErrorDash(m, config=m.startswith(("El dash rechazo la llave", "Al dash todavia le falta", "faltan DASH_URL", "Faltan DASH_URL"))) from None

    def cola(self) -> list[dict]:
        out = []
        for p in self._pedir("GET", "").get("pedidos") or []:
            if isinstance(p, dict) and fab.ID_PROYECTO.match(str(p.get("proyecto") or "")) and ID_ANUNCIO.match(str(p.get("anuncio") or "")) and NOMBRE_PRODUCTO.match(str(p.get("slug") or "")):
                arranque = str(p.get("arranque") or "")        # la hora (ISO) en que una persona mando arrancar la oferta («Crear toda la oferta»), o ''; un dash viejo no lo trae
                out.append({"proyecto": p["proyecto"], "anuncio": p["anuncio"], "nombre": str(p.get("nombre") or "")[:100], "slug": p["slug"], "tipo": str(p.get("tipo") or ""),
                            "estado": str(p.get("estado") or ""), "paises": [str(x)[:30] for x in (p.get("paises") or []) if isinstance(x, str)][:6],
                            "arranque": arranque if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]{4,20}Z?", arranque) else ""})
        return out

    def orden(self, proyecto: str, anuncio: str) -> dict:
        o = self._pedir("GET", f"?proyecto={proyecto}&anuncio={anuncio}").get("orden")
        if not isinstance(o, dict) or not isinstance(o.get("datos"), dict):
            raise ErrorDash("el dash mando una orden que no se entiende")
        return o

    def avisar(self, proyecto: str, anuncio: str, estado: str, nota: str = "", archivo: str = "", drive: str = "", costo: float | None = None) -> dict:
        cuerpo: dict = {"accion": "avisar", "proyecto": proyecto, "anuncio": anuncio, "estado": estado}
        if nota:
            cuerpo["nota"] = nota[:390]
        if archivo:
            cuerpo["archivo"] = archivo
        if drive:
            cuerpo["drive"] = drive
        if costo is not None:
            cuerpo["costoUsd"] = round(float(costo), 4)
        return self._pedir("POST", "", cuerpo)


# ------------------------------------------------------------------ el corredor
class Corredor:
    def __init__(self, cfg: dict, args, deps: Dependencias, log: Registro, raiz: Path | None = None):
        self.cfg, self.args, self.deps, self.log = cfg, args, deps, log
        self.raiz = Path(raiz or RAIZ)
        self.estado_dir = self.raiz / "estado"
        self.anuncios_dir = self.raiz / "anuncios"
        self.public_dir = self.raiz / "public"
        self.dash = Dash(deps.llamar_dash)
        self._drive = None
        self._espejo: ed.Espejo | None = None
        self.cortar = False
        self.resultados: list[dict] = []
        self.corrida = deps.ahora().strftime("%Y%m%d-%H%M%S")
        self.inicio = deps.reloj()
        self.env_hijos: dict = dict(os.environ)

    # ---- utilidades
    def pausa(self) -> bool:
        return bool(self.deps.en_pausa())

    def minutos_restantes(self) -> float:
        return self.cfg["pasada_max_min"] - (self.deps.reloj() - self.inicio) / 60

    def drive(self):
        if self._drive is None:
            self._drive = self.deps.fabricar_drive(self.log) if self.deps.fabricar_drive else da.Drive(da.cargar_credenciales(RUTA_GOOGLE), registrar=self.log)
        return self._drive

    def espejo(self) -> ed.Espejo:
        if self._espejo is None:
            raiz_id = da.id_de_carpeta(str(self.cfg["carpeta_ofertas"]))
            self._espejo = ed.Espejo(self.drive(), raiz_id, self.raiz / "drive" / "OFERTAS LATAM", registrar=self.log, dormir=self.deps.dormir, reloj=self.deps.reloj)
        return self._espejo

    def gasto_hoy(self) -> float:
        try:
            return float(json.loads((self.estado_dir / "gasto.json").read_text(encoding="utf-8")).get(self.deps.ahora().strftime("%Y-%m-%d"), 0.0))
        except (OSError, ValueError, TypeError):
            return 0.0

    def sumar_gasto(self, delta: float) -> None:
        if delta <= 0:
            return
        ruta = self.estado_dir / "gasto.json"
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        hoy = self.deps.ahora().strftime("%Y-%m-%d")
        d = {k: v for k, v in d.items() if k >= (self.deps.ahora().strftime("%Y-%m-01"))}      # solo se guarda el mes
        d[hoy] = round(float(d.get(hoy, 0.0)) + delta, 6)
        guardar_json(ruta, d)

    def tope_del_pedido(self, receta: dict) -> float:
        techo = float(self.cfg["topes"]["usd_por_anuncio"])
        if getattr(self.args, "tope_usd", None) is not None:
            base = max(0.0, float(self.args.tope_usd))
        else:
            base = numero(val(receta, "aprobacion.topeUsd", 0), 0.0, 0.0, 20.0)
            if base <= 0 and self.cfg["topes"]["receta_sin_tope"] == "usa-el-techo":          # el dueno no pone tope por oferta (2026-10-28): manda el techo del servidor
                base = techo
        return round(min(base, techo), 4)

    def disponible(self, ctx: dict) -> float:
        """Cuanto se puede gastar AHORA en este pedido: lo que falta de su tope y de lo que queda del dia."""
        return round(max(0.0, min(ctx["tope"] - ctx["estado"]["gasto_usd"], float(self.cfg["topes"]["usd_por_dia"]) - self.gasto_hoy())), 4)

    def en_silencio(self, dentro_de_min: int = 0) -> bool:
        """Esta (o estara dentro de `dentro_de_min` minutos) en la ventana de silencio? Sin ventana configurada, nunca. La hora es la de Bogota (la unidad trae TZ=America/Bogota)."""
        v = self.cfg["ventana_silencio"]
        if not v["desde"]:
            return False
        t = self.deps.ahora() + timedelta(minutes=dentro_de_min)
        m = t.hour * 60 + t.minute
        (ha, ma), (hb, mb) = (int(x) for x in v["desde"].split(":")), (int(x) for x in v["hasta"].split(":"))
        a, b = ha * 60 + ma, hb * 60 + mb
        return a <= m < b if a < b else (m >= a or m < b)

    # ---- elegir y evaluar
    def candidatos(self, cola: list[dict]) -> list[dict]:
        propios = []
        nuevos = []
        for p in cola:
            local = leer_estado(p["proyecto"], p["anuncio"], self.estado_dir / "pedidos")
            if local and local.get("estado") == "en-curso" and p["estado"] in ("tomado", "en-edicion"):
                propios.append(p)
            elif p["estado"] == "pedido":
                nuevos.append(p)
        return propios + nuevos

    def evaluar_avatar(self, p: dict, receta: dict, tope: float, propio: bool, esp, oferta: dict | None) -> Evaluacion | None:
        """La compuerta propia de un pedido de AVATAR, ANTES de reclamarlo: el avatar de la oferta (fotos autorizadas + avatar.json en Drive) esta listo y el anuncio mas corto que acepta la receta cabe en el
        tope. None = pasa (y deja en `self._extra_avatar` lo que se supo). Si no pasa, el dash recibe UNA nota con el motivo y las cifras: no hay nada que el servidor pueda hacer solo."""
        nota = "El servidor no pudo hacer este anuncio de avatar solo: "
        if esp is not None and oferta is not None:
            av = esp.leer_avatar(oferta)
            if not av["existe"]:
                return Evaluacion("no", "la oferta no tiene la carpeta AVATAR en Drive (fotos autorizadas del avatar y su avatar.json)",
                                  aviso=nota + "la carpeta de la oferta en Drive no tiene la carpeta AVATAR (de 1 a 3 fotos autorizadas del avatar y su avatar.json). Ponla y vuelve a pedir el anuncio.")
            problemas = AC.problemas_de_ficha(av["ficha"], av["fotos"])
            if problemas:
                return Evaluacion("no", "el avatar de la oferta no esta listo: " + "; ".join(problemas),
                                  aviso=(nota + "el avatar de la oferta no esta listo: " + "; ".join(problemas) + ". Corrigelo en la carpeta AVATAR de Drive y vuelve a pedir el anuncio.")[:380])
        local = leer_estado(p["proyecto"], p["anuncio"], self.estado_dir / "pedidos")
        if propio and local and local.get("estado") in ("en-curso", "esperando") and not (self.args.pedido and self.args.rehacer):
            ya = int((local.get("avatar") or {}).get("clips") or 0)
            if ya:                           # lo retoma: ya paso la compuerta de costo al empezar; no se le cambia la regla a la mitad (cada etapa respeta el tope por su cuenta)
                self._extra_avatar = {"clips": ya, "costo": AC.costo_estimado(ya), "duracion": AC.duracion_estimada(ya)}
                return None
        dmin = numero(val(receta, "duracionMin", 25), 25, 10, 120)
        dmax = max(dmin, numero(val(receta, "duracionMax", 60), 60, 15, 180))
        c = AC.clips_que_caben(dmin, dmax, tope)           # contra el tope de ESTE anuncio (el menor entre el OK de la oferta y el techo del servidor): si no cabe, lo decide una persona
        if not c["cabe"]:
            texto = AC.mensaje_de_costo(c, tope, "el tope de este anuncio")
            return Evaluacion("no", "avatar: " + texto, aviso=(nota + texto)[:380])
        dia = round(max(0.0, float(self.cfg["topes"]["usd_por_dia"]) - self.gasto_hoy()), 4)
        c = AC.clips_que_caben(dmin, dmax, min(tope, dia))      # y contra lo que queda HOY: si no alcanza, manana si (no es cosa de una persona)
        if not c["cabe"]:
            return Evaluacion("espera", f"hoy queda US$ {dia:.2f} del tope diario (corredor.json) y el avatar mas corto que acepta la receta cuesta ≈ US$ {c['costo_minimo']:.2f}: espero al dia siguiente")
        self._extra_avatar = {"clips": c["n"], "costo": c["costo"], "duracion": c["duracion"]}
        return None

    def evaluar(self, p: dict, propio: bool = False, con_drive: bool = True) -> Evaluacion:
        cfg = self.cfg
        self._extra_avatar = {}
        if p.get("tipo") and p["tipo"] not in cfg["tipos_permitidos"]:
            return Evaluacion("no", "es de avatar con IA: lo hace una persona con Claudia (corredor.json no permite «ia»)" if p["tipo"] == "ia" else f"es de tipo «{p['tipo']}»: el servidor no lo hace")
        if cfg["ofertas_permitidas"] != "todas" and p["slug"] not in cfg["ofertas_permitidas"]:
            return Evaluacion("no", "la oferta no esta en la lista de ofertas permitidas del corredor")
        if cfg["exigir_arranque"] and not propio and not p.get("arranque"):
            return Evaluacion("no", "nadie ha mandado arrancar esta oferta (falta la orden «Crear toda la oferta» de /fabrica; corredor.json: exigir_arranque)")
        local = leer_estado(p["proyecto"], p["anuncio"], self.estado_dir / "pedidos")
        if local and local.get("estado") == "entregado" and not (self.args.pedido and self.args.rehacer):
            return Evaluacion("no", "ya lo entregue antes (si hay que rehacerlo: corredor-ctl correr --pedido PROYECTO ANUNCIO --rehacer)")
        if local and local.get("estado") == "error" and not (self.args.pedido and self.args.rehacer) and not propio:
            return Evaluacion("no", f"quedo con error en una pasada anterior ({local.get('nota', '')[:80]}); lo reabre una persona")
        try:
            orden = self.dash.orden(p["proyecto"], p["anuncio"])
        except ErrorDash as e:
            if e.config:
                raise
            return Evaluacion("espera", f"el dash no dio la orden ({e})")
        d = orden["datos"]
        pr, an = d.get("proyecto") or {}, d.get("anuncio") or {}
        if pr.get("id") != p["proyecto"] or an.get("id") != p["anuncio"] or pr.get("slug") != p["slug"]:
            return Evaluacion("no", "la orden que mando el dash no es de este pedido")
        receta = d.get("receta") if isinstance(d.get("receta"), dict) else {}
        tipo = str(an.get("tipo") or "tiktok")
        tipo_receta = str(val(receta, "tipoAnuncio", tipo))
        if tipo_receta != tipo or tipo not in cfg["tipos_permitidos"]:
            return Evaluacion("no", "es de avatar con IA: lo hace una persona con Claudia (corredor.json no permite «ia»)" if "ia" in (tipo, tipo_receta) and "ia" not in cfg["tipos_permitidos"]
                              else "el tipo de anuncio de la receta no es el del pedido")
        if not propio and an.get("estado") != "pedido":
            return Evaluacion("no", f"el dash lo tiene en «{an.get('estado')}»: ya lo trabaja alguien")
        if an.get("correcciones"):
            return Evaluacion("no", "el equipo pidio correcciones: las hace una persona con Claudia (el servidor no las interpreta)")
        prod = d.get("producto") or {}
        hechos_aparte = (prod.get("entregablesDrive") or {}).get("id")          # los ebooks ya vienen hechos (carpeta de Drive): la portada la saca la etapa «entregables» de sus PDF
        if not prod.get("portada") and not hechos_aparte:
            return Evaluacion("espera", "los ebooks todavia no estan listos (falta la portada)")
        ref = d.get("referencia") or {}
        enlace = str(ref.get("linkAnuncio") or "")
        hay_ref = bool(ref.get("videoUrl")) or fab.referencia_meta.es_enlace_biblioteca(enlace)
        tope = self.tope_del_pedido(receta)
        if tope <= 0:
            return Evaluacion("no", "sin OK de gasto: la receta de la oferta no trae tope (aprobacion.topeUsd), no se paso --tope-usd y corredor.json no usa el techo (topes.receta_sin_tope)")
        if float(self.cfg["topes"]["usd_por_dia"]) - self.gasto_hoy() < 0.02:
            return Evaluacion("espera", "se acabo el tope de gasto de hoy (corredor.json: topes.usd_por_dia)")
        oferta = None
        esp = None
        if con_drive:
            try:
                esp = self.espejo()
                oferta = esp.hallar_oferta(pr.get("nombre", ""), p["slug"])
                if oferta is None:
                    return Evaluacion("espera", "la oferta todavia no tiene carpeta en Drive (la crea la vigilancia de la Fabrica en menos de una hora)")
                if tipo == "tiktok":
                    n = esp.contar_brolls(oferta)
                    if n < self.cfg["min_brolls"]:
                        return Evaluacion("espera", f"la carpeta de la oferta tiene {n} video(s) de b-roll y hacen falta al menos {self.cfg['min_brolls']}")
                if not hay_ref:
                    hay_ref = esp.referencia_vigente(oferta) is not None and fab.referencia_meta.es_enlace_pagina(enlace)
            except da.ErrorPermiso:
                raise
            except (da.ErrorDrive, ed.ErrorEspejo) as e:
                return Evaluacion("espera", f"Drive no contesto bien ({str(e)[:100]})")
        if not hay_ref:
            return Evaluacion("espera", "no hay un video de referencia (ni enlace de un anuncio, ni el estudio del servidor en Drive)")
        if tipo == "ia":
            try:
                ev_no = self.evaluar_avatar(p, receta, tope, propio, esp, oferta)
            except da.ErrorPermiso:
                raise
            except (da.ErrorDrive, ed.ErrorEspejo) as e:
                return Evaluacion("espera", f"Drive no contesto bien al mirar el avatar ({str(e)[:100]})")
            if ev_no is not None:
                return ev_no
        return Evaluacion("listo", "", orden, oferta, receta, tope, propio, {"tipo": tipo, **self._extra_avatar})

    def avisar_que_hace_falta_una_persona(self, p: dict, texto: str) -> None:
        """Una nota al dash (estado «error», que es como se pide a una persona) con el motivo y las cifras. El pedido deja de ser «pedido», asi que el corredor no lo vuelve a evaluar hasta que una persona lo
        vuelva a pedir (y entonces se le explica de nuevo): no hay nada que repetir cada hora. Solo lleva frases y cifras nuestras, nada de terceros."""
        try:
            self.dash.avisar(p["proyecto"], p["anuncio"], "error", texto[:380])
        except ErrorDash as e:
            self.log(f"  (no pude dejarle la nota al dash: {str(e)[:80]}; lo intento en la siguiente pasada)")
            return
        self.log("  le deje al dash una nota (estado «error») con los motivos y las cifras: hace falta una persona")

    # ---- la pasada
    def pasada(self) -> int:
        cfg, log = self.cfg, self.log
        log(f"=== Corredor de anuncios {self.corrida} ===")
        if self.pausa():
            log("En pausa (estado/PAUSA): no hago nada.")
            return SALIDA_BIEN
        if self.en_silencio() and not self.args.pedido:
            log(f"Ventana de silencio ({self.cfg['ventana_silencio']['desde']} a {self.cfg['ventana_silencio']['hasta']}, hora de Bogota): no hago nada; ese rato es de los vigilantes de las 2 a. m. Sigo cuando termine.")
            return SALIDA_BIEN
        recursos = self.deps.recursos()
        log(f"Recursos: RAM disponible {recursos['ram_gb'] and round(recursos['ram_gb'], 1)} GB · carga 5 min {recursos['carga5']} · disco libre {recursos['disco_gb'] and round(recursos['disco_gb'])} GB · gasto de hoy US$ {self.gasto_hoy():.3f}")
        if not self.args.pedido and not getattr(self.args, "sin_variaciones", False):
            self.paso_variaciones()
            if getattr(self.args, "variacion", None):
                log("Solo la variación pedida: la cola de la Fábrica queda para la próxima pasada.")
                return SALIDA_BIEN
        try:
            if self.args.pedido:
                p_id, a_id = self.args.pedido
                if not fab.ID_PROYECTO.match(p_id) or not ID_ANUNCIO.match(a_id):
                    raise ErrorConfig("--pedido necesita un proyecto y un anuncio validos")
                o = self.dash.orden(p_id, a_id)
                pr, an = o["datos"].get("proyecto") or {}, o["datos"].get("anuncio") or {}
                cola = [{"proyecto": p_id, "anuncio": a_id, "nombre": str(pr.get("nombre") or "")[:100], "slug": str(pr.get("slug") or ""), "tipo": str(an.get("tipo") or ""), "estado": str(an.get("estado") or ""), "paises": []}]
                if not NOMBRE_PRODUCTO.match(cola[0]["slug"]):
                    raise ErrorConfig("el dash mando un nombre de producto raro")
                candidatos = cola
            else:
                cola = self.dash.cola()
                log(f"Cola de la Fabrica: {len(cola)} pedido(s) abierto(s) ({', '.join(sorted({c['estado'] for c in cola})) or 'ninguno'}).")
                candidatos = self.candidatos(cola)
        except ErrorConfig as e:
            log(f"CONFIGURACION: {e}")
            return SALIDA_CONFIG
        except ErrorDash as e:
            log(f"{'CONFIGURACION' if e.config else 'RED'}: el dash no contesto bien ({e})")
            return SALIDA_CONFIG if e.config else SALIDA_RED
        if self.args.solo_cola:
            for p in cola:
                log(f"  {p['proyecto']} {p['anuncio']:<4} {p['estado']:<10} {p['tipo']:<7} {p['slug']} «{p['nombre'][:40]}»")
            log("Solo mirar la cola: no se evalua ni se toca nada.")
            return SALIDA_BIEN
        elegidos = {(c["proyecto"], c["anuncio"]) for c in candidatos}
        for p in cola:
            if (p["proyecto"], p["anuncio"]) not in elegidos:
                log(f"- {p['proyecto']} {p['anuncio']} ({p['slug']}): lo dejo; esta en «{p['estado']}» (ya lo trabaja alguien, o necesita a una persona)")
        if not candidatos:
            log("Nada que hacer: no hay pedidos nuevos para el servidor.")
            if not self.args.simular:
                self.cerrar([])
            return SALIDA_BIEN

        codigo = SALIDA_BIEN
        hechos = 0
        for p in candidatos:
            if self.cortar:
                codigo = max(codigo, SALIDA_RED) if codigo not in (SALIDA_CONFIG, SALIDA_DRIVE) else codigo
                break
            if self.pausa():
                log("Pausa puesta: no empiezo mas pedidos.")
                break
            if hechos >= (1 if self.args.pedido else min(cfg["pedidos_por_corrida"], self.args.max_pedidos or 99)):
                log("Ya hice los pedidos de esta pasada; los demas, en la siguiente.")
                break
            titulo = f"{p['proyecto']} {p['anuncio']} ({p['slug']})"
            propio = bool((leer_estado(p["proyecto"], p["anuncio"], self.estado_dir / "pedidos") or {}).get("estado") == "en-curso" and p["estado"] in ("tomado", "en-edicion")) or bool(self.args.pedido)
            try:
                ev = self.evaluar(p, propio=propio, con_drive=not self.args.solo_cola)
            except ErrorDash as e:
                log(f"CONFIGURACION: el dash rechazo la llave ({e})")
                return SALIDA_CONFIG
            except da.ErrorPermiso as e:
                log(f"DRIVE SIN PERMISO: {e}")
                return SALIDA_DRIVE
            log(f"- {titulo}: {ev.tipo.upper()}" + (f" · {ev.motivo}" if ev.motivo else ""))
            self.resultados.append({"proyecto": p["proyecto"], "anuncio": p["anuncio"], "slug": p["slug"], "evaluacion": ev.tipo, "motivo": ev.motivo})
            if ev.tipo == "no" and ev.aviso and not self.args.simular:
                self.avisar_que_hace_falta_una_persona(p, ev.aviso)
            if ev.tipo != "listo":
                continue
            if self.args.simular:
                log(f"  (simulacion) lo tomaria con un tope de US$ {ev.tope:.3f}" + (f" (avatar: {ev.extra['clips']} clips de 8 s, ≈ US$ {ev.extra['costo']:.2f})" if ev.extra.get("clips") else "") + "; no se toca nada")
                continue
            if not propio and not self.args.pedido and self.en_silencio(cfg["minutos_para_empezar_pedido"]):
                log(f"  La ventana de silencio ({cfg['ventana_silencio']['desde']} a {cfg['ventana_silencio']['hasta']}) empieza en menos de {cfg['minutos_para_empezar_pedido']} min: no empiezo pedidos nuevos; lo dejo para despues.")
                break
            if self.minutos_restantes() < cfg["minutos_para_empezar_pedido"] and not propio:
                log(f"  Quedan {self.minutos_restantes():.0f} min de esta pasada y un pedido necesita al menos {cfg['minutos_para_empezar_pedido']}: lo dejo para la siguiente.")
                break
            if not self.hay_recursos():
                log("  El servidor esta ocupado (poca memoria libre, mucha carga o poco disco): lo dejo para la siguiente pasada.")
                codigo = max(codigo, SALIDA_PARCIAL)
                break
            try:
                resultado = self.procesar(p, ev)
            except da.ErrorPermiso as e:
                log(f"DRIVE SIN PERMISO: {e}")
                return SALIDA_DRIVE
            hechos += 1
            self.resultados[-1]["resultado"] = resultado
            if resultado == "error":
                codigo = max(codigo, SALIDA_PARCIAL)
            elif resultado in ("pausado", "cortado", "red"):
                codigo = max(codigo, SALIDA_RED if resultado != "pausado" else codigo)
                break
        if not self.args.simular:
            self.limpiar_viejos()
            self.cerrar(self.resultados)
        return codigo

    def paso_variaciones(self) -> None:
        """Las variaciones que pide el dueño desde /tablero (variaciones_vps.py) y el índice de originales. Nunca tumba la pasada: lo que falle queda en el registro y se retoma."""
        try:
            import variaciones_vps as VV

            v = VV.Variaciones(self)
            if self.args.solo_cola:
                for p in v.dash.pendientes():
                    self.log(f"  variación pendiente: {p['numero']} de «{p['oferta']}» (sale de «{p['original']['nombre']}»)")
                return
            r = v.atender()
            self.resultados.append({"variaciones": r})
            if not self.args.simular:
                v.limpiar()
        except Exception as e:  # noqa: BLE001 - lo de las variaciones no frena los anuncios de la Fabrica
            self.log(f"Variaciones: algo falló ({e.__class__.__name__}: {self.log.limpiar(str(e))[:160]}); sigo con la Fábrica y se retoma en la próxima pasada")

    def hay_recursos(self) -> bool:
        r, c = self.deps.recursos(), self.cfg["recursos"]
        if r.get("ram_gb") is not None and r["ram_gb"] < c["ram_disponible_min_gb"]:
            return False
        if r.get("carga5") is not None and r["carga5"] > c["carga_5min_max"]:
            return False
        if r.get("disco_gb") is not None and r["disco_gb"] < c["disco_libre_min_gb"]:
            return False
        return True

    def cerrar(self, resultados: list) -> None:
        try:
            guardar_json(self.estado_dir / "ultima_corrida.json", {"corrida": self.corrida, "fin": self.deps.ahora().isoformat(timespec="seconds"), "minutos": round((self.deps.reloj() - self.inicio) / 60, 1),
                                                                    "gasto_hoy_usd": round(self.gasto_hoy(), 4), "pedidos": resultados})
        except OSError:
            pass

    # ---- un pedido, etapa por etapa
    def procesar(self, p: dict, ev: Evaluacion) -> str:
        log, deps = self.log, self.deps
        estado_dir = self.estado_dir / "pedidos"
        st = leer_estado(p["proyecto"], p["anuncio"], estado_dir)
        if st is None or (self.args.pedido and self.args.rehacer) or st.get("estado") not in ("en-curso", "esperando"):
            st = estado_nuevo(p, deps.ahora(), self.corrida)
        st["estado"], st["corrida"] = "en-curso", self.corrida
        st["gasto_usd"] = round(gastado_en_anuncio(p["slug"], p["anuncio"]), 6)
        tipo = str((ev.extra or {}).get("tipo") or "tiktok")
        if tipo == "ia" and (ev.extra or {}).get("clips"):
            st["avatar"] = {"clips": int(ev.extra["clips"])}          # cuantos clips se calcularon al evaluar: si se retoma, se mantiene
        ctx = {"p": p, "ev": ev, "estado": st, "tope": ev.tope, "oferta": ev.oferta, "local_oferta": None, "t0": deps.reloj(), "reclamado": bool(st["etapas"].get("reclamar", {}).get("ok")),
               "tipo": tipo, "clips": int((st.get("avatar") or {}).get("clips") or 0)}
        self.env_hijos = self.armar_entorno()

        def guardar() -> None:
            st["actualizado"] = deps.ahora().isoformat(timespec="seconds")
            guardar_json(ruta_estado(p["proyecto"], p["anuncio"], estado_dir), st)

        guardar()
        log(f"=== {p['proyecto']} {p['anuncio']} · {p['slug']} · tope de gasto US$ {ev.tope:.3f} (llevo US$ {st['gasto_usd']:.3f}) ===")
        etapas = [("espejo", self.e_espejo), ("traer", self.e_traer), ("entregables", self.e_entregables), ("reclamar", self.e_reclamar), ("material", self.e_material), ("guion", self.e_guion), ("voz", self.e_voz),
                  ("gancho", self.e_gancho), ("montaje", self.e_montaje), ("entrega", self.e_entrega)]
        if tipo == "ia":          # el avatar habla en los clips: no hay b-roll ni voz de los flujos; el guion sale en clips de 8 s y los cuadros y clips los hace avatar_auto.py
            etapas = [("espejo", self.e_espejo), ("traer", self.e_traer), ("entregables", self.e_entregables), ("reclamar", self.e_reclamar), ("guion", self.e_guion), ("avatar_plan", self.e_avatar_plan),
                      ("avatar_generar", self.e_avatar_generar), ("avatar_montaje", self.e_avatar_montaje), ("entrega", self.e_entrega)]
        try:
            for nombre, fn in etapas:
                if self.cortar:
                    raise Cortado()
                if self.pausa():
                    raise Pausado()
                if self.en_silencio() and not self.args.pedido:
                    raise Silencio()
                # el espejo y traer se repiten siempre (son baratos, y asi el espejo y la orden estan al dia); las demas se saltan si ya salieron bien.
                # «entregables» tambien se repite: `traer` vuelve a escribir orden.json y los titulos de los ebooks hay que ponerlos de nuevo (si los PDF y la portada ya estan, no baja ni saca nada)
                if st["etapas"].get(nombre, {}).get("ok") and nombre not in ("espejo", "traer", "entregables"):
                    log(f"- {nombre}: ya estaba hecha")
                    continue
                st["etapa"] = nombre
                guardar()
                t0 = deps.reloj()
                antes = gastado_en_anuncio(p["slug"], p["anuncio"])
                log(f"-- etapa {nombre}")
                try:
                    nota = fn(ctx) or ""
                finally:
                    despues = gastado_en_anuncio(p["slug"], p["anuncio"])
                    self.sumar_gasto(max(0.0, despues - antes))
                    st["gasto_usd"] = round(despues, 6)
                st["etapas"][nombre] = {"ok": True, "t": deps.ahora().isoformat(timespec="seconds"), "seg": round(deps.reloj() - t0, 1), "nota": nota[:200]}
                guardar()
            st["estado"], st["etapa"] = "entregado", "listo"
            guardar()
            log(f"=== {p['proyecto']} {p['anuncio']}: ENTREGADO en {(deps.reloj() - ctx['t0']) / 60:.1f} min · costo US$ {st['gasto_usd']:.3f} ===")
            return "entregado"
        except Pausado:
            st["nota"] = "pausado entre etapas"
            guardar()
            log("PAUSA: me detengo aqui (se retoma al reanudar).")
            return "pausado"
        except Silencio:
            st["nota"] = "ventana de silencio: sigue cuando termine"
            guardar()
            log("SILENCIO: llego la ventana de silencio; me detengo entre etapas y sigo cuando termine (no se pierde lo hecho).")
            return "pausado"
        except Cortado:
            st["nota"] = "pasada cortada (systemd la paro)"
            guardar()
            log("Me pidieron parar: dejo el pedido como esta (se retoma en la siguiente pasada).")
            return "cortado"
        except FallaDeEtapa as e:
            if e.espera and not ctx["reclamado"]:
                st["estado"], st["nota"] = "esperando", e.motivo
                guardar()
                log(f"ESPERA ({st['etapa']}): {e.motivo}. No toqué el pedido en el dash.")
                return "espera"
            return self.fallar(ctx, st["etapa"], e.motivo, guardar)
        except ErrorDash as e:
            if e.config:
                st["nota"] = "el dash rechazo la llave"
                guardar()
                raise
            return self.reintentar_luego(ctx, f"el dash no contesto en la etapa {st['etapa']} ({str(e)[:80]})", guardar)
        except (da.ErrorDrive, ed.ErrorEspejo) as e:
            if isinstance(e, da.ErrorPermiso):
                raise
            return self.reintentar_luego(ctx, f"Drive no contesto bien en la etapa {st['etapa']} ({str(e)[:100]})", guardar)

    def reintentar_luego(self, ctx: dict, motivo: str, guardar) -> str:
        """Un corte de red o de Drive: no es culpa del pedido ni se pierde lo hecho (el video ya renderizado sigue en disco). Se deja como esta y la siguiente pasada lo retoma;
        solo despues de varias pasadas seguidas fallando se da por perdido."""
        st = ctx["estado"]
        st["fallos_de_red"] = int(st.get("fallos_de_red", 0)) + 1
        self.log(f"RED: {motivo} (pasada {st['fallos_de_red']} de 6 con problemas de red)")
        if st["fallos_de_red"] >= 6:
            return self.fallar(ctx, st["etapa"], "6 pasadas seguidas sin poder hablar con el dash o con Drive", guardar)
        st["nota"] = motivo[:300]
        guardar()
        return "red"

    def fallar(self, ctx: dict, etapa: str, motivo: str, guardar) -> str:
        """Una etapa fallo de verdad. Con el pedido ya reclamado se lo dice al dash («error», con una frase fija) y queda anotado; si no se habia reclamado, no se toca el dash."""
        st, p = ctx["estado"], ctx["p"]
        self.log(f"FALLA en la etapa {etapa}: {motivo}")
        st["nota"] = f"{etapa}: {motivo}"[:300]
        st["intentos"] = int(st.get("intentos", 0)) + 1
        st["estado"] = "error"
        guardar()
        if ctx["reclamado"]:
            try:
                self.dash.avisar(p["proyecto"], p["anuncio"], "error", f"El servidor no pudo terminar el anuncio en la etapa «{etapa}». {motivo}"[:380], costo=st["gasto_usd"])
            except ErrorDash as e:
                self.log(f"(no pude avisar el error al dash: {e})")
        return "error"

    # ---- entorno de los hijos: la llave del dash solo viaja por aqui (la de APIMart, nunca)
    def armar_entorno(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONSTARTUP", *NUNCA_A_LOS_HIJOS)}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "OFERTAS_LATAM_DIR": str(self.raiz / "drive" / "OFERTAS LATAM"), "TZ": "America/Bogota"})
        return env

    def py(self, script: str, *args: str) -> list[str]:
        return [sys.executable, "-u", str(SCRIPTS / script), *args]

    def correr_etapa(self, nombre: str, cmd: list[str]) -> Corrida:
        minutos = int(self.cfg["tiempos_max_min"].get(nombre, 30))
        env = dict(self.env_hijos)
        for quien, variable in VARIABLE_DE_LLAVE.items():
            if quien not in LLAVES_POR_ETAPA.get(nombre, ()):
                env.pop(variable, None)
        for variable in NUNCA_A_LOS_HIJOS:
            env.pop(variable, None)
        r = self.deps.correr(cmd, env, minutos * 60, self.log, lambda: self.cortar or False, self.raiz)
        if r.cortado:
            raise Cortado()
        if r.tiempo_agotado:
            raise FallaDeEtapa(f"se paso del tiempo maximo ({minutos} min)")
        return r

    # ---- etapas
    def base_de(self, ctx: dict) -> Path:
        """La carpeta de trabajo del anuncio: anuncios/<oferta>/<tiktok | ia>/<AD>."""
        return self.anuncios_dir / ctx["p"]["slug"] / ctx.get("tipo", "tiktok") / ctx["p"]["anuncio"]

    def e_espejo(self, ctx: dict) -> str:
        p, esp = ctx["p"], self.espejo()
        oferta = ctx["oferta"] or esp.hallar_oferta(ctx["ev"].orden["datos"]["proyecto"].get("nombre", ""), p["slug"])
        if oferta is None:
            raise FallaDeEtapa("la oferta no tiene carpeta en Drive", espera=True)
        ctx["oferta"] = oferta
        plazo = self.deps.reloj() + int(self.cfg["tiempos_max_min"]["espejo"]) * 60
        res = esp.bajar_oferta(oferta, plazo=plazo, con_avatar=ctx.get("tipo") == "ia", con_brolls=ctx.get("tipo") != "ia")
        ctx["local_oferta"] = Path(res["local"])
        if res["fallos"]:
            raise FallaDeEtapa("Drive no dejo bajar todo: " + "; ".join(res["fallos"])[:200], espera=True)
        resumen = ", ".join(f"{k}: {c['bajados']} bajados, {c['ya_estaban']} ya estaban" + (f", {c['quitados']} quitados" if c.get("quitados") else "") for k, c in res["carpetas"].items())
        self.log(f"  espejo de «{res['oferta']}»: {resumen}")
        # la musica: una vez al dia (si Drive no la tiene o falla, el anuncio sale sin musica, como en el PC con la biblioteca vacia)
        marca = self.estado_dir / "musica.json"
        hoy = self.deps.ahora().strftime("%Y-%m-%d")
        try:
            ya = json.loads(marca.read_text(encoding="utf-8")).get("dia") == hoy
        except (OSError, ValueError):
            ya = False
        if not ya:
            try:
                m = esp.bajar_musica(self.raiz / "public" / "musica" / "biblioteca", plazo=self.deps.reloj() + 600)
                self.log(f"  musica: {m.get('bajados', 0)} pista(s) nuevas, {m.get('ya_estaban', 0)} ya estaban" + (f" ({m['nota']})" if m.get("nota") else ""))
                guardar_json(marca, {"dia": hoy})
            except da.ErrorPermiso:
                raise
            except (da.ErrorDrive, ed.ErrorEspejo) as e:
                self.log(f"  (no pude bajar la musica: {str(e)[:100]}; el anuncio sale sin musica si no hay pistas)")
        return resumen

    def e_traer(self, ctx: dict) -> str:
        p = ctx["p"]
        r = self.correr_etapa("traer", self.py("fabrica.py", "traer", p["proyecto"], p["anuncio"], "--sin-avisar"))
        carpeta = self.base_de(ctx)
        if r.codigo != 0 or not (carpeta / "orden.json").is_file() or not (carpeta / "receta.json").is_file():
            raise FallaDeEtapa("fabrica.py traer no pudo dejar la orden y la receta del pedido")
        ref = self.public_dir / "productos" / p["slug"] / "referencia"
        if not ((ref / f"{p['anuncio']}_referencia.mp4").is_file() or (ref / f"{p['anuncio']}_copy.txt").is_file()):
            raise FallaDeEtapa("no hay video ni texto del anuncio de referencia (el estudio del servidor todavia no lo dejo en Drive, o Meta no dejo bajarlo)", espera=True)
        return "orden, receta, ebooks y referencia en su sitio"

    def e_entregables(self, ctx: dict) -> str:
        """Ebooks que YA VIENEN HECHOS: el equipo pego en la Fabrica la carpeta de Drive con los PDF (orden.json › producto.entregablesDrive). Baja esos PDF (solo lectura), saca la portada y las paginas
        del principal con pdf_a_imagenes.py (lo mismo que hace el editor en el PC) y deja en orden.json los titulos de los ebooks: lo unico que el guion puede decir de la oferta sale de ahi y de la promesa.
        Sin enlace en la orden no hace nada (los ebooks vienen del dash). La carpeta tiene que ser la de la oferta o una subcarpeta suya: el servidor no lee otras carpetas por un enlace del dash."""
        p = ctx["p"]
        base = self.base_de(ctx)
        try:
            datos = json.loads((base / "orden.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise FallaDeEtapa("no puedo leer orden.json del pedido") from None
        carpeta_id = str(((datos.get("producto") or {}).get("entregablesDrive") or {}).get("id") or "")
        if not carpeta_id:
            return "los ebooks vienen del dash"
        esp = self.espejo()
        oferta = ctx["oferta"] or esp.hallar_oferta((datos.get("proyecto") or {}).get("nombre", ""), p["slug"])
        if oferta is None:
            raise FallaDeEtapa("la oferta no tiene carpeta en Drive", espera=True)
        ctx["oferta"] = oferta
        carpeta = esp.carpeta_de_entregables(oferta, carpeta_id)
        if carpeta is None:
            raise FallaDeEtapa("la carpeta de entregables que pego el equipo no esta dentro de la carpeta de la oferta en Drive: hay que ponerla ahi (el servidor no lee otras carpetas)", espera=True)
        destino = self.raiz / "drive" / "ENTREGABLES" / p["slug"]
        plazo = self.deps.reloj() + int(self.cfg["tiempos_max_min"]["entregables"]) * 60
        res = esp.bajar_entregables(carpeta["id"], destino, plazo=plazo)
        if res["fallos"]:
            raise FallaDeEtapa("Drive no dejo bajar los PDF: " + "; ".join(res["fallos"])[:200], espera=True)
        nombres_pdf = [n for n in res["nombres"] if Path(n).suffix.lower() == ".pdf"]
        if not nombres_pdf:
            raise FallaDeEtapa(f"la carpeta «{carpeta['name']}» de Drive no tiene PDF", espera=True)
        if len(nombres_pdf) > MAX_PDF_ENTREGABLES:
            raise FallaDeEtapa(f"la carpeta «{carpeta['name']}» tiene {len(nombres_pdf)} PDF y el maximo son {MAX_PDF_ENTREGABLES} (un principal y sus bonos)")
        pdfs = [{"archivo": n, "ruta": destino / n, "bytes": (destino / n).stat().st_size, "titulo": titulo_de_pdf(n)} for n in nombres_pdf]
        principal = elegir_principal(pdfs)
        mockup = elegir_mockup([{"archivo": n, "ruta": destino / n, "bytes": (destino / n).stat().st_size} for n in res["nombres"] if Path(n).suffix.lower() in ed.EXT_FOTO])
        producto = self.public_dir / "productos" / p["slug"]
        marca = producto / "entregables.json"
        firma = [[x["archivo"], x["bytes"]] for x in pdfs] + ([["mockup:" + mockup["archivo"], mockup["bytes"]]] if mockup else [])
        try:
            previo = json.loads(marca.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previo = None
        if not (isinstance(previo, dict) and previo.get("firma") == firma and (producto / "portada.jpg").is_file()):
            # pdf_a_imagenes.py abre el PDF de un tercero con pymupdf: corre en su propio proceso, sin llaves y con tiempo maximo (correr_etapa)
            r = self.correr_etapa("entregables", self.py("pdf_a_imagenes.py", p["slug"], "--pdf", str(principal["ruta"]), "--ancho", "1200"))
            if r.codigo != 0 or not (producto / "portada.jpg").is_file():
                raise FallaDeEtapa("pdf_a_imagenes.py no pudo sacar la portada del PDF principal (falta pymupdf en el servidor, o el PDF esta danado)")
            portada_de = principal["archivo"]
            if mockup:          # el mockup que el equipo puso en la carpeta es mejor portada para un anuncio que la primera pagina de un PDF; la pagina queda en paginas/p001.jpg
                m = self.correr_etapa("entregables", [sys.executable, "-I", "-c", CODIGO_MOCKUP_A_JPG, str(mockup["ruta"]), str(producto / "portada.jpg")])
                if m.codigo == 0:
                    portada_de = "mockup: " + mockup["archivo"]
                else:
                    self.log(f"  AVISO: no pude convertir el mockup «{mockup['archivo']}» (¿esta danado?): la portada sale de la primera pagina de «{principal['archivo']}»")
            previo = {"firma": firma, "principal": principal["archivo"], "portada_de": portada_de,
                      "ebooks": [{"clave": "principal" if x["archivo"] == principal["archivo"] else "bono", "titulo": x["titulo"], "paginas": None} for x in pdfs]}
            guardar_json(marca, previo)
        # los titulos de los ebooks a orden.json (traer lo reescribe en cada pasada: por eso esta etapa se repite)
        of = datos.setdefault("oferta", {})
        if not of.get("ebooks"):
            of["ebooks"] = previo["ebooks"]
        if not of.get("vinetas"):
            of["vinetas"] = [e["titulo"] for e in previo["ebooks"]]
        guardar_json(base / "orden.json", datos)
        return f"{len(pdfs)} PDF de «{carpeta['name']}» ({res['bajados']} bajados, {res['ya_estaban']} ya estaban); portada de «{previo.get('portada_de') or previo['principal']}»"

    def e_reclamar(self, ctx: dict) -> str:
        p = ctx["p"]
        o = self.dash.orden(p["proyecto"], p["anuncio"])          # justo antes de reclamar: sigue en «pedido»?
        estado = (o["datos"].get("anuncio") or {}).get("estado")
        if not ctx["ev"].propio and estado != "pedido":
            raise FallaDeEtapa(f"mientras preparaba el material el pedido paso a «{estado}»: lo dejo para quien lo tomo", espera=True)
        if estado in ("lista", "aprobada", "cancelado"):
            raise FallaDeEtapa(f"el dash lo tiene en «{estado}»: no lo toco", espera=True)
        self.dash.avisar(p["proyecto"], p["anuncio"], "tomado", NOTA_TOMADO)
        ctx["reclamado"] = True
        return "avisado al dash"

    def e_material(self, ctx: dict) -> str:
        p = ctx["p"]
        local = ctx["local_oferta"] or (self.raiz / "drive" / "OFERTAS LATAM" / ctx["oferta"]["name"])
        r = self.correr_etapa("material", self.py("broll_tiktok.py", "recoger", p["slug"], "--drive", str(local)))
        if r.codigo != 0:
            raise FallaDeEtapa("broll_tiktok.py recoger no pudo traer los videos al producto")
        self.quitar_brolls_borrados(p["slug"], local / ed.CARPETA_BROLLS)
        r = self.correr_etapa("material", self.py("emparejar_tomas.py", p["slug"]))
        if r.codigo != 0:
            raise FallaDeEtapa("emparejar_tomas.py no pudo analizar el b-roll")
        return "b-roll recogido y analizado"

    def quitar_brolls_borrados(self, slug: str, espejo_brolls: Path) -> None:
        """Lo que el equipo borro de B-rolls en Drive (ya no esta en el espejo) tampoco se usa: se quita de la carpeta de trabajo del producto. Solo tt_*.mp4 de esa carpeta."""
        trabajo = self.public_dir / "productos" / slug / "broll-tiktok"
        if not trabajo.is_dir() or not espejo_brolls.is_dir():
            return
        hay = {p.name for p in espejo_brolls.iterdir() if p.is_file()}
        for v in trabajo.iterdir():
            if v.is_file() and re.match(r"^tt_\d{10,25}\.", v.name) and v.suffix.lower() in ed.EXT_VIDEO and v.name not in hay:
                v.unlink()
                self.log(f"  quitado del producto {v.name}: ya no esta en B-rolls de Drive")

    def _tope_para(self, ctx: dict, que: str) -> float:
        """Lo que queda por gastar en este pedido. Cada script mira su propio costo contra este numero (y se niega, sin gastar, si no alcanza); aqui solo se corta si ya no queda nada."""
        d = self.disponible(ctx)
        if d < 0.005:
            raise FallaDeEtapa(f"ya no queda tope de gasto para {que} (quedan US$ {d:.3f}: el tope del pedido es US$ {ctx['tope']:.3f}, ya van US$ {ctx['estado']['gasto_usd']:.3f}, y hoy van US$ {self.gasto_hoy():.3f})")
        return d

    def e_guion(self, ctx: dict) -> str:
        p = ctx["p"]
        d = self._tope_para(ctx, "escribir el guion")
        extra = ["--tipo", "ia", "--clips", str(int(ctx["clips"]))] if ctx.get("tipo") == "ia" else []          # el guion de un avatar sale en N clips de 8 s (N lo calculo la compuerta de costo)
        r = self.correr_etapa("guion", self.py("guion_auto.py", p["slug"], p["anuncio"], *extra, "--tope-usd", f"{d:.4f}"))
        base = self.base_de(ctx)
        if r.codigo != 0 or not (base / "guion.json").is_file():
            raise FallaDeEtapa("no salio un guion que pase las reglas" + (" (falta el OK de gasto)" if r.codigo == 2 else ""))
        return "guion escrito"

    def e_voz(self, ctx: dict) -> str:
        p = ctx["p"]
        d = self._tope_para(ctx, "pedir la voz")
        cmd = self.py("voz_auto.py", p["slug"], p["anuncio"], "--tope-usd", f"{d:.4f}")
        r = self.correr_etapa("voz", cmd)
        base = self.base_de(ctx)
        if r.codigo == 3 and (base / "audio" / "voz.mp3").is_file():
            # la voz salio pero dura distinto de lo que pide la receta: se ajusta la velocidad (gratis: no se paga otra voz) y se vuelve a medir
            receta = ctx["ev"].receta
            dmin, dmax = numero(val(receta, "duracionMin", 25), 25, 10, 120), numero(val(receta, "duracionMax", 60), 60, 15, 180)
            vel = numero(val(receta, "voz.velocidad", 1.1), 1.1, 0.8, 1.4)
            dur = duracion_o_cero(base / "audio" / "voz.mp3")
            if dur and dur < dmin * 0.85:
                nueva = max(0.8, round(vel * dur / (dmin * 0.95), 2))
            elif dur and dur > dmax + 2.0:
                nueva = min(1.4, round(vel * dur / (dmax - 1.0), 2))
            else:
                nueva = vel
            if nueva != vel:
                self.log(f"  la voz dura {dur:.1f} s y la receta pide de {dmin:g} a {dmax:g}: la paso de velocidad {vel:g} a {nueva:g} (gratis, no se paga otra voz)")
                r = self.correr_etapa("voz", self.py("voz_auto.py", p["slug"], p["anuncio"], "--tope-usd", f"{d:.4f}", "--velocidad", f"{nueva:g}"))
                if r.codigo == 3:
                    self.log("  sigue fuera de lo que pide la receta: el control de calidad del montaje lo marcara")
                    return "voz lista (duracion fuera de la receta)"
            else:
                return "voz lista (duracion fuera de la receta)"
        if r.codigo not in (0, 3) or not (base / "audio" / "voz_norm.wav").is_file() or not (base / "transcripcion.json").is_file():
            raise FallaDeEtapa("no salio una voz que diga el guion" + (" (falta el OK de gasto)" if r.codigo == 2 else ""))
        return "voz lista"

    def e_gancho(self, ctx: dict) -> str:
        p, receta = ctx["p"], ctx["ev"].receta
        if self.cfg["gancho_ia"] == "nunca" or getattr(self.args, "sin_gancho", False):
            return "sin gancho con IA (apagado a proposito)"
        if str(val(receta, "gancho.modo", "nuevo-modelado")) != "nuevo-modelado":
            return "la receta no pide gancho nuevo"
        d = self.disponible(ctx)
        if d < 0.04:
            self.log(f"  gancho con IA: no alcanza el tope (quedan US$ {d:.3f}); el anuncio sale con el b-roll de principio a fin")
            return "sin gancho con IA (no alcanza el tope)"
        r = self.correr_etapa("gancho", self.py("gancho_ia.py", p["slug"], p["anuncio"], "--tope-usd", f"{d:.4f}"))
        if r.codigo in (0,):
            return "gancho con IA listo"
        self.log(f"  el gancho con IA no salio (codigo {r.codigo}): sigo con el b-roll")
        return f"sin gancho con IA (codigo {r.codigo})"

    def e_montaje(self, ctx: dict) -> str:
        p = ctx["p"]
        r = self.correr_etapa("montaje", self.py("montaje_auto.py", p["slug"], p["anuncio"], "--concurrencia", "1"))
        return self.resultado_del_montaje(ctx, r)

    def e_avatar_plan(self, ctx: dict) -> str:
        p = ctx["p"]
        d = self._tope_para(ctx, "planear las escenas del avatar")
        local = Path(ctx["local_oferta"] or (self.raiz / "drive" / "OFERTAS LATAM" / ctx["oferta"]["name"]))
        r = self.correr_etapa("avatar_plan", self.py("avatar_auto.py", "planear", p["slug"], p["anuncio"], "--tope-usd", f"{d:.4f}", "--avatar-desde", str(local / ed.CARPETA_AVATAR)))
        if r.codigo != 0 or not (self.base_de(ctx) / "avatar" / "plan.json").is_file():
            raise FallaDeEtapa({2: "falta el avatar (ficha, fotos autorizadas) o el guion", 3: "el anuncio de avatar no cabe en el tope de gasto"}.get(r.codigo, "no salio un plan de escenas que pase las reglas"))
        return "plan de escenas del avatar listo"

    def e_avatar_generar(self, ctx: dict) -> str:
        p = ctx["p"]
        d = self.disponible(ctx)
        if d < AC.COSTO_CUADRO_Y_CLIP:
            raise FallaDeEtapa(f"ya no queda tope de gasto para generar los cuadros y clips (quedan US$ {d:.3f}: el tope del pedido es US$ {ctx['tope']:.3f}, ya van US$ {ctx['estado']['gasto_usd']:.3f}, y hoy van US$ {self.gasto_hoy():.3f})")
        r = self.correr_etapa("avatar_generar", self.py("avatar_auto.py", "generar", p["slug"], p["anuncio"], "--tope-usd", f"{d:.4f}"))
        if r.codigo != 0:
            raise FallaDeEtapa({3: "lo que falta generar no cabe en el tope de gasto", 5: "la generacion de cuadros y clips quedo incompleta (APIMart fallo, se acabo el tope o un cuadro no se parece al avatar): mira avatar/generacion.json"}
                               .get(r.codigo, "la generacion de cuadros y clips del avatar fallo"))
        return "cuadros y clips del avatar listos"

    def e_avatar_montaje(self, ctx: dict) -> str:
        p = ctx["p"]
        r = self.correr_etapa("avatar_montaje", self.py("avatar_auto.py", "montar", p["slug"], p["anuncio"], "--concurrencia", "1"))
        return self.resultado_del_montaje(ctx, r)

    def resultado_del_montaje(self, ctx: dict, r: Corrida) -> str:
        base = self.base_de(ctx)
        if r.codigo == 0:
            return "montaje aprobado por las compuertas de calidad"
        fallan = []
        try:
            inf = json.loads((base / "informe_calidad.json").read_text(encoding="utf-8"))
            fallan = [k for k, v in (inf.get("compuertas") or {}).items() if isinstance(v, dict) and v.get("ok") is False]
            if inf.get("estado") == "sin-material":
                raise FallaDeEtapa("no alcanza el b-roll limpio para todos los cortes: trae mas videos (B-rolls en Drive)")
        except (OSError, ValueError):
            pass
        if r.codigo == 2 and fallan:
            raise FallaDeEtapa("el video quedo pero no paso el control de calidad: " + ", ".join(f[:40] for f in fallan[:6]))
        raise FallaDeEtapa(f"el montaje no pudo (codigo {r.codigo})")

    def e_entrega(self, ctx: dict) -> str:
        p, st = ctx["p"], ctx["estado"]
        base = self.base_de(ctx)
        videos = sorted((base / "final").glob("*.mp4"), key=lambda x: x.stat().st_mtime) if (base / "final").is_dir() else []
        if not videos:
            raise FallaDeEtapa("el montaje no dejo ningun video en final/")
        video = videos[-1]
        r = self.correr_etapa("entrega", self.py("fabrica.py", "entregar", p["slug"], p["anuncio"], "--archivo", str(video)))
        local_oferta = ctx["local_oferta"] or (self.raiz / "drive" / "OFERTAS LATAM" / ctx["oferta"]["name"])
        copia = Path(local_oferta) / ed.CARPETA_ANUNCIOS / video.name
        if r.codigo != 0 or not copia.is_file():
            raise FallaDeEtapa("fabrica.py entregar no dejo el video en la carpeta ANUNCIOS del espejo")
        sub = self.espejo().subir_anuncio(ctx["oferta"], copia)
        self.log(f"  Drive: {sub['name']} ({sub['size'] / 1048576:.1f} MB) {sub['estado']} -> {sub['enlace']}")
        gasto = round(gastado_en_anuncio(p["slug"], p["anuncio"]), 4)
        minutos = (self.deps.reloj() - ctx["t0"]) / 60
        st["entrega"] = {"archivo": sub["name"], "enlace": sub["enlace"], "carpeta": sub["carpeta"], "costo_usd": gasto}
        self.dash.avisar(p["proyecto"], p["anuncio"], "lista", f"El servidor lo hizo solo y lo dejó en Drive (ANUNCIOS). Tardó {minutos:.0f} min; costó US$ {gasto:.3f}. Revisa el video antes de aprobarlo.",
                         archivo=sub["name"], drive=sub["enlace"], costo=gasto)
        return f"entregado: {sub['name']}"

    # ---- limpieza
    def limpiar_viejos(self) -> None:
        """Del trabajo de pedidos ya entregados con mas de N dias se borran los archivos pesados (el video final ya esta en Drive); las ordenes, recetas, planes e informes se quedan."""
        limite = self.deps.ahora().timestamp() - self.cfg["dias_guardar_trabajo"] * 86400
        liberado = 0
        carpeta = self.estado_dir / "pedidos"
        if not carpeta.is_dir():
            return
        for f in carpeta.glob("*.json"):
            try:
                st = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if st.get("estado") != "entregado" or st.get("trabajo_limpio") or not NOMBRE_PRODUCTO.match(str(st.get("slug") or "")) or not ID_ANUNCIO.match(str(st.get("anuncio") or "")):
                continue
            try:
                if datetime.fromisoformat(str(st.get("actualizado"))).timestamp() > limite:
                    continue
            except ValueError:
                continue
            for tipo in ("tiktok", "ia"):
                base = self.raiz / "anuncios" / st["slug"] / tipo / st["anuncio"]
                for sub in ("final", "audio", "pruebas", "gancho", "cuadros", "clips"):
                    d = base / sub
                    if d.is_dir():
                        liberado += sum(x.stat().st_size for x in d.rglob("*") if x.is_file())
                        shutil.rmtree(d, ignore_errors=True)
                for suelto in ("base.mp4", "voz_norm.wav"):
                    if (base / suelto).is_file():
                        liberado += (base / suelto).stat().st_size
                        (base / suelto).unlink()
            st["trabajo_limpio"] = True
            guardar_json(f, st)
        if liberado:
            self.log(f"Limpieza: {liberado / 1048576:.0f} MB liberados de anuncios entregados hace mas de {self.cfg['dias_guardar_trabajo']} dias")


# ------------------------------------------------------------------ linea de comandos
def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simular", action="store_true", help="evalua los pedidos y dice cuales tomaria, sin reclamar ni escribir nada")
    ap.add_argument("--solo-cola", action="store_true", help="solo lista la cola de la Fabrica (no evalua ni toca nada)")
    ap.add_argument("--max-pedidos", type=int, help="cuantos pedidos hacer en esta pasada (por defecto, los de corredor.json)")
    ap.add_argument("--tope-usd", type=float, help="OK del dueno para el gasto de CADA pedido de esta corrida (si no, manda aprobacion.topeUsd de la receta); nunca pasa de los techos de corredor.json")
    ap.add_argument("--sin-gancho", action="store_true", help="no hacer el gancho nuevo con IA (solo guion, voz y b-roll)")
    ap.add_argument("--pedido", nargs=2, metavar=("PROYECTO", "ANUNCIO"), help="hacer solo ese pedido (aunque el dash lo tenga tomado, en edicion o con error si es del corredor o con --rehacer)")
    ap.add_argument("--rehacer", action="store_true", help="con --pedido: empezar de cero la cuenta de etapas y de intentos de ese pedido")
    ap.add_argument("--indice", action="store_true", help="armar ya el índice de originales de Drive y mandárselo al dash (normalmente cada variaciones.indice_horas)")
    ap.add_argument("--variacion", metavar="ID", help="hacer solo esa variación pendiente del tablero (con --tope-usd: el OK del dueño para ESA, aunque las variaciones estén apagadas)")
    ap.add_argument("--sin-variaciones", action="store_true", help="no mirar las variaciones del tablero en esta pasada")
    return ap


def main() -> None:
    from _comun import configurar_consola

    configurar_consola()
    a = construir_parser().parse_args()
    if a.rehacer and not a.pedido:
        construir_parser().error("--rehacer necesita --pedido PROYECTO ANUNCIO")
    if a.max_pedidos is not None and not (1 <= a.max_pedidos <= 5):
        construir_parser().error("--max-pedidos es de 1 a 5")
    if a.tope_usd is not None and not (0 <= a.tope_usd <= 2):
        construir_parser().error("--tope-usd es de 0 a 2")
    if a.variacion and (a.pedido or not re.fullmatch(r"[a-z0-9]{8,20}", a.variacion)):
        construir_parser().error("--variacion necesita el id de la variación (letras minúsculas y números) y no va con --pedido")
    log = Registro()
    try:
        cfg = cargar_config()
        url, llave = leer_secreto_dash()
        os.environ["DASH_URL"], os.environ["DASH_CLAVE_EDITOR"] = url, llave
        log.agregar_secreto(llave)
        # La llave de APIMart ya no hace falta (todo lo que cuesta pasa por el dash con la llave del editor). Si una unidad vieja todavia la trae, se tapa en el registro y no
        # la ve ningun script (NUNCA_A_LOS_HIJOS).
        log.agregar_secreto(os.environ.get("APIMART_API_KEY", ""))
    except ErrorConfig as e:
        log(f"CONFIGURACION: {e}")
        sys.exit(SALIDA_CONFIG)
    corredor = Corredor(cfg, a, Dependencias(), log)

    def al_parar(signum, _frame) -> None:
        corredor.cortar = True
        log(f"Me llego una orden de parar (senal {signum}): termino de cerrar lo que hago y salgo.")

    signal.signal(signal.SIGTERM, al_parar)
    signal.signal(signal.SIGINT, al_parar)
    try:
        sys.exit(corredor.pasada())
    except da.ErrorPermiso as e:
        log(f"DRIVE SIN PERMISO: {e}")
        sys.exit(SALIDA_DRIVE)
    except (da.ErrorDrive, ed.ErrorEspejo) as e:
        log(f"DRIVE: {e}")
        sys.exit(SALIDA_RED)


if __name__ == "__main__":
    main()
