"""Puerta de Drive del equipo de video (el editor) en el servidor (VPS).

El editor trabaja en una cuenta propia del servidor SIN permisos de administrador y SIN la llave de Drive. Esta «puerta» es la unica forma de que lo suyo llegue a Drive (y de que el
material de Drive llegue a el), y funciona asi:

  - La persona corre `puerta traer|entregar|respaldar` (modo CLIENTE, con su propio usuario): el programa deja una peticion (un .json pequeno) en su carpeta de peticiones
    /srv/editor/personas/<persona>/cola y espera la respuesta en .../resultados.
  - systemd ve la peticion y corre `puerta_editor.py servir` (modo SERVIDOR) con un usuario aparte (`puerta`), que es el unico que recibe el permiso de Drive (LoadCredential=).
    Valida la peticion, hace el trabajo contra Drive y deja el resultado. Nunca ejecuta nada de lo que viene en la peticion: solo la lee como datos.

Lo que SI hace el servidor:
  traer      baja de Drive (SOLO LECTURA) la oferta pedida (B-rolls, REFERENCIA vigente, AVATAR) o la biblioteca de musica a /srv/editor/drive, que el editor lee pero no escribe.
  entregar   sube UN video terminado a MI NEGOCIO > OFERTAS LATAM > <oferta> > ANUNCIOS (nunca pisa uno distinto con el mismo nombre: sube _v2, _v3...).
  respaldar  sube UN .zip del proyecto de trabajo a MI NEGOCIO > EDITOR > <persona> (la copia para que nada se pierda si el servidor se limpia).
Lo que NO hace, a proposito: no borra, no mueve, no comparte nada en Drive; no escribe en ninguna otra carpeta de Drive (la «cerca» de drive_api: solo se escribe donde se declaro con
permitir()); no entrega la llave de Drive a nadie.

Defensas contra una peticion mala (la persona dueña de la cuenta es de confianza, pero su cuenta o su Claude podrian equivocarse o ser enganados):
  - la oferta se busca por nombre ENTRE las carpetas reales de OFERTAS LATAM; un nombre que no existe se rechaza (nada se usa como ruta);
  - los archivos de la persona se abren con O_NOFOLLOW (un enlace simbolico se rechaza), se comprueba que son archivos normales, DE ELLA y sin enlaces duros, y se COPIAN a una carpeta
    privada del servicio antes de revisarlos y subirlos: lo que se revisa es exactamente lo que se sube (no se puede cambiar por otro entre una cosa y otra);
  - un video se comprueba con ffprobe (tiene que ser un video de verdad, de duracion razonable); un .zip tiene que ser un zip;
  - topes de tamano, de peticiones por hora y de disco libre; todo lo que sale a Drive lleva nombre limpio.

Uso (en el servidor, como la persona):
  puerta traer <oferta> | --musica        baja el material de la oferta (o la musica) a /srv/editor/drive
  puerta entregar <oferta> <video.mp4>    sube el video terminado a ANUNCIOS de la oferta
  puerta respaldar [carpeta]              empaqueta tu proyecto (sin lo que se puede volver a bajar) y lo sube a MI NEGOCIO > EDITOR > tu nombre
  puerta estado                           lo que hay en tu cola, los ultimos resultados y el disco
  puerta publicar [--simular]             pasa tus mejoras del editor a produccion (revision automatica; ver scripts/editor_promover.py)
  puerta sincronizar [--traer] <archivo>  pone tu base al dia con produccion para ese archivo
Modo servidor (lo lanza systemd):  puerta_editor.py servir
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:                                    # solo Linux (el servidor); en Windows el archivo se puede importar para comprobar la sintaxis
    import pwd
except ImportError:                     # pragma: no cover
    pwd = None

RAIZ_CODIGO = Path(__file__).resolve().parents[1]
RUTA_CONFIG = RAIZ_CODIGO / "vps" / "editor-puerta.json"

RE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
RE_PERSONA = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
RE_ARCHIVO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]{0,118}\.(mp4|mov|zip)$", re.IGNORECASE)
RE_OFERTA = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,120}$")
SUBCARPETAS = ("cola", "entregas", "resultados", "procesadas")
MAX_PETICION = 64 * 1024
TIPOS = ("traer", "entregar", "respaldar")
O_SEGURO = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
O_DIR = getattr(os, "O_DIRECTORY", 0)
MB = 1024 * 1024
LIMITES_POR_DEFECTO = {"video_mb": 1500, "respaldo_mb": 500, "duracion_max_s": 600, "pedidos_por_hora": 30, "disco_libre_min_gb": 10, "dias_guardar": 14}
EXCLUIR_DIRS = {"node_modules", ".cache", ".venv", "__pycache__", "modelos", ".remotion", "out", ".git"}
EXCLUIR_RUTAS = ("/broll-tiktok/", "/_descartados/", "/public/musica/biblioteca/", "/public/sfx/")
EXCLUIR_EXT = {".parte", ".tmp", ".pyc"}
MAX_ARCHIVO_RESPALDO = 30 * MB          # lo mas pesado (videos y audios) se puede volver a hacer o ya esta en Drive: el respaldo guarda el trabajo, no los medios


class ErrorPuerta(Exception):
    """Algo salio mal. El mensaje esta escrito para una persona (o para su Claude): dice que paso y que hacer."""


# ------------------------------------------------------------------ configuracion
def cargar_config(ruta: Path | str | None = None) -> dict:
    ruta = Path(ruta or os.environ.get("PUERTA_CONFIG", "") or RUTA_CONFIG)
    try:
        c = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErrorPuerta(f"No puedo leer la configuracion de la puerta ({ruta}): {e.__class__.__name__}") from None
    if not isinstance(c, dict):
        raise ErrorPuerta("La configuracion de la puerta no es un objeto JSON")
    personas = c.get("personas")
    if not isinstance(personas, list) or not personas or not all(isinstance(p, str) and RE_PERSONA.match(p) for p in personas) or len(set(personas)) != len(personas):
        raise ErrorPuerta("La configuracion de la puerta: 'personas' tiene que ser una lista de nombres de usuario validos y sin repetir")
    raiz = c.get("raiz")
    if not isinstance(raiz, str) or not raiz.startswith("/") or ".." in raiz or len(raiz) < 5:
        raise ErrorPuerta("La configuracion de la puerta: 'raiz' tiene que ser una ruta absoluta (por ejemplo /srv/editor)")
    if not isinstance(c.get("carpeta_ofertas"), str) or not c["carpeta_ofertas"].strip():
        raise ErrorPuerta("La configuracion de la puerta: falta 'carpeta_ofertas'")
    if not isinstance(c.get("carpeta_respaldos"), str) or not re.fullmatch(r"[A-Z][A-Z0-9 _-]{1,40}", c["carpeta_respaldos"]):
        raise ErrorPuerta("La configuracion de la puerta: 'carpeta_respaldos' es el NOMBRE (en mayusculas) de la carpeta que se crea dentro de MI NEGOCIO, por ejemplo EDITOR")
    c["limites"] = {**LIMITES_POR_DEFECTO, **(c.get("limites") if isinstance(c.get("limites"), dict) else {})}
    for k, v in c["limites"].items():
        if k not in LIMITES_POR_DEFECTO or not isinstance(v, (int, float)) or v <= 0:
            raise ErrorPuerta(f"La configuracion de la puerta: el limite '{k}' no es valido")
    return c


def _ahora_texto() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _bien_nombre(texto: str) -> str | None:
    """Un nombre de archivo limpio para Drive y el disco, o None."""
    return texto if isinstance(texto, str) and RE_ARCHIVO.match(texto) and ".." not in texto else None


# ================================================================== CLIENTE (lo corre la persona, sin privilegios)
def _persona_actual(cfg: dict) -> str:
    if pwd is None:
        raise ErrorPuerta("La puerta solo funciona en el servidor Linux")
    nombre = pwd.getpwuid(os.getuid()).pw_name
    if nombre not in cfg["personas"]:
        raise ErrorPuerta(f"El usuario '{nombre}' no esta en la lista de la puerta ({', '.join(cfg['personas'])}).")
    return nombre


def _carpeta_spool(cfg: dict, persona: str) -> Path:
    return Path(cfg["raiz"]) / "personas" / persona


def _nuevo_id() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def _decir(texto: str = "") -> None:
    print(texto, flush=True)


def pedir(cfg: dict, persona: str, tipo: str, datos: dict, espera_s: int, *, como_json: bool = False) -> int:
    """Deja la peticion y espera el resultado. 0 si salio bien, 1 si el servidor dijo que no, 3 si no contesto a tiempo."""
    spool = _carpeta_spool(cfg, persona)
    for s in SUBCARPETAS:
        if not (spool / s).is_dir():
            raise ErrorPuerta(f"No existe {spool / s}: la puerta no esta instalada para {persona} (la instala el dueno con instalar-editor-vps.sh).")
    id_ = _nuevo_id()
    peticion = {"version": 1, "id": id_, "tipo": tipo, **datos}
    tmp = spool / "cola" / f".{id_}.tmp"
    final = spool / "cola" / f"{id_}.json"
    with open(tmp, "x", encoding="utf-8") as f:
        f.write(json.dumps(peticion, ensure_ascii=False))
    os.chmod(tmp, 0o660)
    os.rename(tmp, final)                       # el .json aparece ya completo: systemd arranca al verlo
    resultado_ruta = spool / "resultados" / f"{id_}.json"
    limite = time.monotonic() + espera_s
    ultimo_aviso = time.monotonic()
    if not como_json:
        _decir(f"Peticion {id_} ({tipo}) enviada a la puerta. Espero la respuesta del servidor (hasta {espera_s // 60} min)...")
    while time.monotonic() < limite:
        if resultado_ruta.is_file():
            try:
                res = json.loads(resultado_ruta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(1)
                continue
            if como_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            else:
                _decir(("LISTO: " if res.get("ok") else "NO SE HIZO: ") + str(res.get("mensaje", "")))
                for k, v in (res.get("detalle") or {}).items():
                    _decir(f"  {k}: {v}")
            return 0 if res.get("ok") else 1
        if not como_json and time.monotonic() - ultimo_aviso > 20:
            _decir("  ...sigue trabajando (puedes ver el avance con: puerta estado)")
            ultimo_aviso = time.monotonic()
        time.sleep(2)
    _decir(f"El servidor no contesto en {espera_s // 60} min. La peticion {id_} sigue en la cola: mira `puerta estado` mas tarde (el resultado queda en {spool / 'resultados'}).")
    return 3


def _copiar_a_entregas(cfg: dict, persona: str, origen: Path, nombre: str) -> None:
    destino = _carpeta_spool(cfg, persona) / "entregas" / nombre
    tmp = destino.with_name("." + nombre + ".parte")
    shutil.copyfile(origen, tmp)
    os.chmod(tmp, 0o660)
    os.replace(tmp, destino)


def cmd_traer(cfg: dict, args) -> int:
    persona = _persona_actual(cfg)
    if args.musica:
        return pedir(cfg, persona, "traer", {"musica": True}, args.esperar * 60, como_json=args.json)
    if not args.oferta or not RE_OFERTA.match(args.oferta):
        raise ErrorPuerta("Uso: puerta traer \"NOMBRE DE LA OFERTA\"   (o: puerta traer --musica)")
    return pedir(cfg, persona, "traer", {"oferta": args.oferta}, args.esperar * 60, como_json=args.json)


def cmd_entregar(cfg: dict, args) -> int:
    persona = _persona_actual(cfg)
    origen = Path(args.video).expanduser()
    nombre = _bien_nombre(origen.name)
    if nombre is None or not nombre.lower().endswith((".mp4", ".mov")):
        raise ErrorPuerta("El video tiene que llamarse con letras, numeros, espacios, punto, guion y terminar en .mp4 o .mov (sin tildes ni caracteres raros): renombralo y vuelve a intentar.")
    if origen.is_symlink() or not origen.is_file():
        raise ErrorPuerta(f"No encuentro el video {origen} (tiene que ser un archivo normal, no un enlace).")
    tam = origen.stat().st_size
    if tam <= 0 or tam > cfg["limites"]["video_mb"] * MB:
        raise ErrorPuerta(f"El video pesa {tam // MB} MB; el tope es {int(cfg['limites']['video_mb'])} MB.")
    if not args.oferta or not RE_OFERTA.match(args.oferta):
        raise ErrorPuerta("Uso: puerta entregar \"NOMBRE DE LA OFERTA\" <video.mp4>")
    _decir(f"Copiando {nombre} ({tam // MB} MB) a la cola de entregas...")
    _copiar_a_entregas(cfg, persona, origen, nombre)
    return pedir(cfg, persona, "entregar", {"oferta": args.oferta, "archivo": nombre}, args.esperar * 60, como_json=args.json)


def _lista_para_zip(carpeta: Path):
    """(archivos a incluir, excluidos por motivo). No sigue enlaces simbolicos, y deja fuera lo que se puede volver a bajar o fabricar."""
    incluir: list[tuple[Path, str]] = []
    fuera: dict[str, int] = {}
    carpeta = carpeta.resolve()
    for dirpath, dirnames, filenames in os.walk(carpeta, followlinks=False):
        rel_dir = Path(dirpath).relative_to(carpeta)
        quitar = [d for d in dirnames if d in EXCLUIR_DIRS or (Path(dirpath) / d).is_symlink()]
        for d in quitar:
            fuera[d + "/"] = fuera.get(d + "/", 0) + 1
        dirnames[:] = [d for d in dirnames if d not in quitar]
        for f in filenames:
            p = Path(dirpath) / f
            rel = (rel_dir / f).as_posix()
            if p.is_symlink() or not p.is_file():
                continue
            if any(m in "/" + rel for m in EXCLUIR_RUTAS):
                fuera["videos/archivos que se vuelven a bajar de Drive"] = fuera.get("videos/archivos que se vuelven a bajar de Drive", 0) + 1
                continue
            if p.suffix.lower() in EXCLUIR_EXT or p.stat().st_size > MAX_ARCHIVO_RESPALDO:
                fuera["archivos temporales o de mas de 30 MB (videos y audios pesados)"] = fuera.get("archivos temporales o de mas de 30 MB (videos y audios pesados)", 0) + 1
                continue
            incluir.append((p, rel))
    return incluir, fuera


def cmd_respaldar(cfg: dict, args) -> int:
    persona = _persona_actual(cfg)
    carpeta = Path(args.carpeta or "~/editor-anuncios").expanduser()
    if not carpeta.is_dir() or carpeta.is_symlink():
        raise ErrorPuerta(f"No encuentro la carpeta {carpeta}. Uso: puerta respaldar [carpeta]")
    home = Path(os.path.expanduser("~")).resolve()
    try:
        carpeta.resolve().relative_to(home)
    except ValueError:
        raise ErrorPuerta("Solo se respalda una carpeta dentro de tu casa.") from None
    incluir, fuera = _lista_para_zip(carpeta)
    if not incluir:
        raise ErrorPuerta("No hay nada que respaldar en esa carpeta.")
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", carpeta.name).strip("-.")[:60] or "proyecto"
    nombre = f"{base}_{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    destino = _carpeta_spool(cfg, persona) / "entregas" / nombre
    tmp = destino.with_name("." + nombre + ".parte")
    tope = cfg["limites"]["respaldo_mb"] * MB
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        total = 0
        for p, rel in incluir:
            total += p.stat().st_size
            if total > tope * 3:
                tmp.unlink(missing_ok=True)
                raise ErrorPuerta(f"Lo que hay para respaldar pasa de {int(cfg['limites']['respaldo_mb']) * 3} MB sin comprimir: mueve o borra lo pesado que no sea del proyecto y repite.")
            z.write(p, rel)
    tam = tmp.stat().st_size
    if tam > tope:
        tmp.unlink(missing_ok=True)
        raise ErrorPuerta(f"El respaldo pesa {tam // MB} MB y el tope es {int(cfg['limites']['respaldo_mb'])} MB: saca lo que no sea del proyecto y repite.")
    os.chmod(tmp, 0o660)
    os.replace(tmp, destino)
    _decir(f"Respaldo armado: {nombre} ({tam // MB} MB, {len(incluir)} archivos). Dejado fuera: " + (", ".join(f"{k} ({v})" for k, v in sorted(fuera.items())) or "nada"))
    return pedir(cfg, persona, "respaldar", {"archivo": nombre}, args.esperar * 60, como_json=args.json)


def cmd_estado(cfg: dict, args) -> int:
    persona = _persona_actual(cfg)
    spool = _carpeta_spool(cfg, persona)
    _decir(f"=== Puerta de Drive de {persona} ===")
    try:
        activo = subprocess.run(["systemctl", "is-active", f"editor-puerta@{persona}.path"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        activo = "?"
    _decir(f"vigilante de tus peticiones (editor-puerta@{persona}.path): {activo}")
    for s in ("cola", "entregas"):
        d = spool / s
        n = [p.name for p in sorted(d.iterdir()) if not p.name.startswith(".")] if d.is_dir() else []
        _decir(f"{s}: {len(n)} pendiente(s)" + (": " + ", ".join(n[:6]) if n else ""))
    rs = sorted((spool / "resultados").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5] if (spool / "resultados").is_dir() else []
    if rs:
        _decir("ultimos resultados:")
        for r in rs:
            try:
                j = json.loads(r.read_text(encoding="utf-8"))
                _decir(f"  {r.stem}  {j.get('tipo', '?'):9} {'ok ' if j.get('ok') else 'NO '} {str(j.get('mensaje', ''))[:110]}")
            except (OSError, ValueError):
                _decir(f"  {r.stem}  (no se pudo leer)")
    else:
        _decir("ultimos resultados: ninguno")
    raiz = Path(cfg["raiz"])
    try:
        libre = shutil.disk_usage(raiz).free / 1024 ** 3
        _decir(f"disco libre del servidor: {libre:.0f} GB   (la puerta no trabaja con menos de {int(cfg['limites']['disco_libre_min_gb'])} GB)")
    except OSError:
        pass
    espejo = raiz / "drive" / "OFERTAS LATAM"
    if espejo.is_dir():
        ofertas = sorted(p.name for p in espejo.iterdir() if p.is_dir())
        _decir(f"material de Drive ya traido ({espejo}): " + (", ".join(ofertas) if ofertas else "nada todavia"))
    else:
        _decir(f"material de Drive ya traido: nada todavia (usa `puerta traer \"OFERTA\"`; se lee en {espejo})")
    return 0


# ================================================================== SERVIDOR (lo corre systemd con el usuario `puerta`)
class Puerta:
    """Atiende las peticiones de cada persona. `drive` es un drive_api.Drive (o uno de mentira en las pruebas)."""

    def __init__(self, cfg: dict, drive, *, registrar=print, ffprobe: str = "ffprobe", uid_raiz: int = 0, uid_de=None, reloj=time.time, trabajo: Path | None = None):
        import espejo_drive as ed

        self.cfg = cfg
        self.drive = drive
        self.registrar = registrar
        self.ffprobe = ffprobe
        self.uid_raiz = uid_raiz
        self.uid_de = uid_de or (lambda p: pwd.getpwnam(p).pw_uid)
        self.reloj = reloj
        self.raiz = Path(cfg["raiz"])
        self.trabajo = trabajo or (self.raiz / "estado" / "trabajo")
        self.espejo = ed.Espejo(drive, da_id(cfg["carpeta_ofertas"]), self.raiz / "drive" / "OFERTAS LATAM", registrar=registrar)
        self.ed = ed

    # ---- una pasada: todas las personas, todas las peticiones pendientes
    def servir(self, solo: str | None = None) -> int:
        hechas = 0
        self.limpiar_viejos()
        for persona in ([solo] if solo else self.cfg["personas"]):
            try:
                hechas += self.atender_persona(persona)
            except ErrorPuerta as e:
                self.registrar(f"[{persona}] {e}")
        self.registrar(f"pasada terminada: {hechas} peticion(es) atendida(s)")
        return 0

    def _abrir_dirs(self, persona: str) -> dict[str, int]:
        """Abre las carpetas de la persona SIN seguir enlaces y comprueba que son de root (la persona no puede cambiarlas de sitio)."""
        fds: dict[str, int] = {}
        try:
            base = os.open(str(self.raiz / "personas" / persona), os.O_RDONLY | O_DIR | O_SEGURO)
        except OSError as e:
            raise ErrorPuerta(f"no puedo abrir la carpeta de peticiones de {persona}: {e.strerror}") from None
        try:
            st = os.fstat(base)
            if st.st_uid != self.uid_raiz or st.st_mode & 0o022:
                raise ErrorPuerta(f"la carpeta de {persona} no es de root o es escribible por otros: no la uso")
            for s in SUBCARPETAS:
                try:
                    fd = os.open(s, os.O_RDONLY | O_DIR | O_SEGURO, dir_fd=base)
                except OSError as e:
                    raise ErrorPuerta(f"falta {s} de {persona}: {e.strerror}") from None
                fds[s] = fd
                st = os.fstat(fd)
                if st.st_uid != self.uid_raiz or st.st_mode & 0o002:
                    raise ErrorPuerta(f"{s} de {persona} no es de root o es escribible por cualquiera: no lo uso")
        except Exception:
            for fd in fds.values():
                os.close(fd)
            raise
        finally:
            os.close(base)
        return fds

    def atender_persona(self, persona: str) -> int:
        uid = self.uid_de(persona)
        fds = self._abrir_dirs(persona)
        n = 0
        try:
            nombres = sorted(x for x in os.listdir(fds["cola"]) if not x.startswith("."))
            for nombre in nombres:
                if not nombre.endswith(".json") or not RE_ID.match(nombre[:-5]):
                    self._descartar(fds, nombre, "nombre que no es de una peticion")
                    continue
                self.atender(persona, uid, fds, nombre)
                n += 1
        finally:
            for fd in fds.values():
                os.close(fd)
        return n

    def _descartar(self, fds: dict, nombre: str, motivo: str) -> None:
        self.registrar(f"descarto {nombre} de la cola: {motivo}")
        try:
            os.rename(nombre, f"{int(self.reloj())}-{secrets.token_hex(2)}.descartada", src_dir_fd=fds["cola"], dst_dir_fd=fds["procesadas"])
        except OSError:
            try:
                os.unlink(nombre, dir_fd=fds["cola"])
            except OSError:
                pass

    def atender(self, persona: str, uid: int, fds: dict, nombre: str) -> None:
        id_ = nombre[:-5]
        resultado = {"id": id_, "tipo": "?", "ok": False, "mensaje": "", "detalle": {}, "fin": ""}
        peticion = None
        try:
            peticion = self._leer_peticion(fds["cola"], nombre, uid, id_)
            resultado["tipo"] = peticion["tipo"]
            self._exigir_ritmo(persona)
            if peticion["tipo"] == "traer":
                resultado.update(self._traer(peticion))
            elif peticion["tipo"] == "entregar":
                resultado.update(self._entregar(persona, uid, fds, peticion))
            else:
                resultado.update(self._respaldar(persona, uid, fds, peticion))
            resultado["ok"] = True
        except ErrorPuerta as e:
            resultado["mensaje"] = str(e)
        except Exception as e:                                  # lo inesperado se cuenta en el registro (sin datos) y la persona recibe un mensaje claro
            tipo = e.__class__.__name__
            if self.ed.da.ErrorPermiso in e.__class__.__mro__:
                resultado["mensaje"] = f"Drive no deja trabajar: {e}"
            elif self.ed.da.ErrorDrive in e.__class__.__mro__ or self.ed.ErrorEspejo in e.__class__.__mro__:
                resultado["mensaje"] = str(e)
            else:
                self.registrar(f"[{persona}] error inesperado en {id_}: {tipo}")
                resultado["mensaje"] = f"Error inesperado del servidor ({tipo}). Avisale al dueno con el numero de peticion {id_}."
        resultado["fin"] = _ahora_texto()
        if peticion and peticion["tipo"] in ("entregar", "respaldar"):      # lo que la persona dejo en entregas se quita siempre (si salio mal, lo vuelve a mandar con el comando)
            self._quitar_de_entregas(fds["entregas"], peticion["archivo"])
        try:
            self._escribir_resultado(fds["resultados"], id_, resultado)
        finally:
            try:
                os.rename(nombre, nombre, src_dir_fd=fds["cola"], dst_dir_fd=fds["procesadas"])
            except OSError:
                try:
                    os.unlink(nombre, dir_fd=fds["cola"])
                except OSError:
                    pass
        self.registrar(f"[{persona}] {id_} {resultado['tipo']}: {'ok' if resultado['ok'] else 'NO'} - {resultado['mensaje'][:150]}")

    def _leer_peticion(self, cola_fd: int, nombre: str, uid: int, id_: str) -> dict:
        try:
            fd = os.open(nombre, os.O_RDONLY | O_SEGURO | getattr(os, "O_NONBLOCK", 0), dir_fd=cola_fd)
        except OSError as e:
            raise ErrorPuerta(f"No pude abrir la peticion (¿es un enlace?): {e.strerror}") from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ErrorPuerta("La peticion no es un archivo normal")
            if st.st_uid != uid:
                raise ErrorPuerta("La peticion no es de esa persona")
            if st.st_size > MAX_PETICION:
                raise ErrorPuerta("La peticion es demasiado grande")
            crudo = os.read(fd, MAX_PETICION + 1)
        finally:
            os.close(fd)
        try:
            p = json.loads(crudo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ErrorPuerta("La peticion no es un JSON valido") from None
        if not isinstance(p, dict) or p.get("version") != 1 or p.get("id") != id_ or p.get("tipo") not in TIPOS:
            raise ErrorPuerta("La peticion no tiene la forma esperada (version, id y tipo)")
        permitidas = {"version", "id", "tipo"} | {"traer": {"oferta", "musica"}, "entregar": {"oferta", "archivo"}, "respaldar": {"archivo"}}[p["tipo"]]
        if set(p) - permitidas:
            raise ErrorPuerta("La peticion trae campos que no se conocen: " + ", ".join(sorted(set(p) - permitidas))[:80])
        if p["tipo"] == "traer":
            if bool(p.get("musica")) == bool(p.get("oferta")):
                raise ErrorPuerta("Para traer hay que decir una oferta o --musica (una sola cosa)")
            if p.get("musica") is not None and p.get("musica") is not True:
                raise ErrorPuerta("musica solo puede ser true")
            if p.get("oferta") is not None and not (isinstance(p["oferta"], str) and RE_OFERTA.match(p["oferta"])):
                raise ErrorPuerta("El nombre de la oferta no es valido")
        else:
            if _bien_nombre(p.get("archivo")) is None:
                raise ErrorPuerta("El nombre del archivo no es valido (letras, numeros, espacios, punto y guion; termina en .mp4, .mov o .zip)")
            if p["tipo"] == "entregar":
                if not (isinstance(p.get("oferta"), str) and RE_OFERTA.match(p["oferta"])) or not p["archivo"].lower().endswith((".mp4", ".mov")):
                    raise ErrorPuerta("Para entregar hace falta el nombre de la oferta y un video .mp4 o .mov")
            elif not p["archivo"].lower().endswith(".zip"):
                raise ErrorPuerta("Para respaldar el archivo tiene que ser un .zip")
        return p

    def _exigir_ritmo(self, persona: str) -> None:
        """Tope de peticiones por hora. El registro vive en /srv/editor/estado (de la puerta): la persona no lo puede borrar para empezar de cero."""
        ruta = self.trabajo.parent / f"ritmo-{persona}.json"
        ahora = self.reloj()
        try:
            marcas = [float(x) for x in json.loads(ruta.read_text(encoding="utf-8")) if isinstance(x, (int, float))]
        except (OSError, ValueError, TypeError):
            marcas = []
        marcas = [m for m in marcas if m >= ahora - 3600]
        if len(marcas) >= self.cfg["limites"]["pedidos_por_hora"]:
            raise ErrorPuerta(f"Ya hubo {len(marcas)} peticiones en la ultima hora (el tope es {int(self.cfg['limites']['pedidos_por_hora'])}): espera un rato.")
        marcas.append(ahora)
        tmp = ruta.with_name(ruta.name + ".nuevo")
        tmp.write_text(json.dumps(marcas), encoding="utf-8")
        os.replace(tmp, ruta)

    def _escribir_resultado(self, res_fd: int, id_: str, resultado: dict) -> None:
        tmp = f".{id_}.tmp"
        try:
            os.unlink(tmp, dir_fd=res_fd)
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_SEGURO, 0o640, dir_fd=res_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(resultado, ensure_ascii=False, indent=2))
        os.rename(tmp, f"{id_}.json", src_dir_fd=res_fd, dst_dir_fd=res_fd)

    # ---- trabajo privado: lo que la persona deja se COPIA aqui antes de revisarlo y subirlo
    def _copiar_privado(self, entregas_fd: int, nombre: str, uid: int, tope_bytes: int, destino: Path) -> int:
        try:
            fd = os.open(nombre, os.O_RDONLY | O_SEGURO | getattr(os, "O_NONBLOCK", 0), dir_fd=entregas_fd)
        except OSError:
            raise ErrorPuerta(f"No encuentro {nombre} en tu carpeta de entregas (¿se copio bien? o ¿es un enlace? no se aceptan enlaces).") from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ErrorPuerta(f"{nombre} no es un archivo normal")
            if st.st_uid != uid:
                raise ErrorPuerta(f"{nombre} no es de esa persona")
            if st.st_size <= 0 or st.st_size > tope_bytes:
                raise ErrorPuerta(f"{nombre} pesa {st.st_size // MB} MB y el tope es {tope_bytes // MB} MB")
            destino.parent.mkdir(parents=True, exist_ok=True)
            copiados = 0
            with os.fdopen(fd, "rb", closefd=False) as origen, open(destino, "xb") as sal:
                for trozo in iter(lambda: origen.read(1 * MB), b""):
                    copiados += len(trozo)
                    if copiados > tope_bytes:
                        raise ErrorPuerta(f"{nombre} crecio mientras se copiaba: no se usa")
                    sal.write(trozo)
            return copiados
        finally:
            os.close(fd)

    def _quitar_de_entregas(self, entregas_fd: int, nombre: str) -> None:
        try:
            os.unlink(nombre, dir_fd=entregas_fd)
        except OSError:
            pass

    def _exigir_disco(self, extra_gb: int = 0) -> None:
        """Con `extra_gb` se pide margen para lo que se va a bajar (un traer son hasta 3 carpetas de 3 GB)."""
        libre = shutil.disk_usage(self.raiz).free / 1024 ** 3
        minimo = self.cfg["limites"]["disco_libre_min_gb"] + extra_gb
        if libre < minimo:
            raise ErrorPuerta(f"Al servidor le quedan {libre:.0f} GB libres (hacen falta {int(minimo)}): no trabajo hasta que el dueno haga espacio.")

    def _validar_video(self, ruta: Path) -> dict:
        try:
            r = subprocess.run([self.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)], capture_output=True, text=True, timeout=90,
                               env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"})
            info = json.loads(r.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError):
            raise ErrorPuerta("No pude revisar el video con ffprobe: ¿esta danado?") from None
        if r.returncode != 0:
            raise ErrorPuerta("ffprobe no entiende ese archivo: no es un video valido")
        video = [s for s in info.get("streams", []) if s.get("codec_type") == "video" and s.get("width") and s.get("height")]
        formato = str((info.get("format") or {}).get("format_name", ""))
        if not video or not ("mp4" in formato or "mov" in formato):
            raise ErrorPuerta("Eso no es un video .mp4/.mov con imagen")
        try:
            dur = float((info.get("format") or {}).get("duration") or video[0].get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur <= 0.5 or dur > self.cfg["limites"]["duracion_max_s"]:
            raise ErrorPuerta(f"El video dura {dur:.0f} s; tiene que durar entre 1 s y {int(self.cfg['limites']['duracion_max_s'])} s para entregarse como anuncio")
        return {"duracion_s": round(dur, 1), "ancho": int(video[0]["width"]), "alto": int(video[0]["height"])}

    # ---- las tres cosas que hace
    def _traer(self, p: dict) -> dict:
        self._exigir_disco(extra_gb=9)
        plazo = time.monotonic() + 45 * 60
        if p.get("musica"):
            r = self.espejo.bajar_musica(self.raiz / "drive" / "musica", plazo=plazo)
            if r.get("fallos"):
                raise ErrorPuerta("La musica bajo con problemas: " + "; ".join(r["fallos"][:3]))
            return {"mensaje": f"Musica lista en {self.raiz / 'drive' / 'musica'} ({r.get('estilos', 0)} estilos, {r.get('bajados', 0)} pistas nuevas, {r.get('ya_estaban', 0)} que ya estaban)." + (f" {r['nota']}" if r.get("nota") else ""),
                    "detalle": {"carpeta": str(self.raiz / "drive" / "musica")}}
        oferta = self.espejo.hallar_oferta(p["oferta"], "")
        if not oferta:
            nombres = sorted(str(f.get("name")) for f in self.drive.listar_hijos(self.espejo.raiz_id) if f.get("mimeType") == self.ed.da.MIME_CARPETA)
            raise ErrorPuerta(f"No hay una oferta llamada «{p['oferta']}» en OFERTAS LATAM. Las que hay: " + ", ".join(nombres[:40]))
        r = self.espejo.bajar_oferta(oferta, plazo=plazo, con_avatar=True)
        if r.get("fallos"):
            raise ErrorPuerta("Bajo con problemas (lo que ya llego queda): " + "; ".join(r["fallos"][:3]))
        det = {"carpeta": r["local"], "MB nuevos": round(r["bytes"] / MB, 1)}
        for k, c in r["carpetas"].items():
            det[k] = f"{c['bajados']} nuevos, {c['ya_estaban']} que ya estaban"
        return {"mensaje": f"Material de «{r['oferta']}» listo en {r['local']}. Los scripts del editor lo encuentran solos (OFERTAS_LATAM_DIR).", "detalle": det}

    def _entregar(self, persona: str, uid: int, fds: dict, p: dict) -> dict:
        self._exigir_disco()
        oferta = self.espejo.hallar_oferta(p["oferta"], "")
        if not oferta:
            nombres = sorted(str(f.get("name")) for f in self.drive.listar_hijos(self.espejo.raiz_id) if f.get("mimeType") == self.ed.da.MIME_CARPETA)
            raise ErrorPuerta(f"No hay una oferta llamada «{p['oferta']}» en OFERTAS LATAM: el video NO se subio. Las que hay: " + ", ".join(nombres[:40]))
        privado = self.trabajo / p["id"] / p["archivo"]
        try:
            self._copiar_privado(fds["entregas"], p["archivo"], uid, int(self.cfg["limites"]["video_mb"] * MB), privado)
            info = self._validar_video(privado)
            r = self.espejo.subir_anuncio(oferta, privado, mime="video/quicktime" if privado.suffix.lower() == ".mov" else "video/mp4")
        finally:
            shutil.rmtree(privado.parent, ignore_errors=True)
        return {"mensaje": f"{r['name']} quedo en ANUNCIOS de «{oferta['name']}» ({r['estado']}, {r['size'] // MB} MB, comprobado con el mismo tamano y MD5 en Drive). Enlace: {r['enlace']}",
                "detalle": {"oferta": oferta["name"], "archivo en Drive": r["name"], "enlace": r["enlace"], "carpeta ANUNCIOS": r["carpeta"], "duracion_s": info["duracion_s"], "medidas": f"{info['ancho']}x{info['alto']}"}}

    def _respaldar(self, persona: str, uid: int, fds: dict, p: dict) -> dict:
        self._exigir_disco()
        # MI NEGOCIO es la carpeta donde vive OFERTAS LATAM: ahi se halla o se crea EDITOR (lo unico que se escribe fuera de una oferta), y dentro la carpeta de la persona
        golden = (self.drive.datos_de(self.espejo.raiz_id).get("parents") or [None])[0]
        if not golden:
            raise ErrorPuerta("No pude ubicar la carpeta MI NEGOCIO (la que contiene OFERTAS LATAM) para dejar el respaldo.")
        info = self.drive.carpeta(golden)
        if not info.get("escribible"):
            raise ErrorPuerta("La cuenta que dio el permiso de Drive no puede escribir en MI NEGOCIO.")
        privado = self.trabajo / p["id"] / p["archivo"]
        try:
            self._copiar_privado(fds["entregas"], p["archivo"], uid, int(self.cfg["limites"]["respaldo_mb"] * MB), privado)
            if not zipfile.is_zipfile(privado):
                raise ErrorPuerta("Ese archivo no es un .zip valido")
            self.drive.permitir(golden)
            try:
                editor = self.drive.hallar_o_crear_carpeta(golden, self.cfg["carpeta_respaldos"])
            finally:
                self.drive.cercado.discard(golden)
            self.drive.permitir(editor)
            try:
                suya = self.drive.hallar_o_crear_carpeta(editor, persona)
            finally:
                self.drive.cercado.discard(editor)
            r = self._subir_sin_pisar(suya, privado, p["archivo"], "application/zip")
        finally:
            shutil.rmtree(privado.parent, ignore_errors=True)
        return {"mensaje": f"Respaldo {r['name']} guardado en {info.get('name', 'MI NEGOCIO')} > {self.cfg['carpeta_respaldos']} > {persona} ({max(r['size'] // MB, 1)} MB). Enlace de la carpeta: {r['carpeta']}",
                "detalle": {"archivo en Drive": r["name"], "carpeta": r["carpeta"], "enlace": r["enlace"]}}

    def _subir_sin_pisar(self, carpeta_id: str, ruta: Path, nombre: str, mime: str) -> dict:
        """Sube `ruta` a una carpeta ya validada. Si ya hay uno igual (mismo nombre, tamano y MD5) no hace nada; si hay otro con el mismo nombre, sube _v2, _v3... Comprueba tamano en Drive."""
        da = self.ed.da
        self.drive.permitir(carpeta_id)
        try:
            tam, md5 = ruta.stat().st_size, da._md5(ruta).lower()
            por_nombre = {str(f.get("name")): f for f in self.drive.listar_hijos(carpeta_id) if f.get("mimeType") != da.MIME_CARPETA}
            candidato, n = nombre, 1
            while candidato in por_nombre:
                f = por_nombre[candidato]
                if str(f.get("size")) == str(tam) and str(f.get("md5Checksum") or md5).lower() == md5:
                    return {"id": f["id"], "name": candidato, "size": tam, "enlace": self.ed.enlace_archivo(f["id"]), "carpeta": self.ed.enlace_carpeta(carpeta_id), "estado": "ya estaba"}
                n += 1
                base, ext = os.path.splitext(nombre)
                candidato = f"{base}_v{n}{ext}"
            meta = self.drive.subir_archivo(ruta, carpeta_id, candidato, mime)
            datos = self.drive.datos_de(str(meta["id"]))
            if carpeta_id not in (datos.get("parents") or []) or str(datos.get("size")) != str(tam) or datos.get("trashed"):
                raise ErrorPuerta(f"Drive no confirma {candidato} con el tamano exacto: no se da por guardado")
            return {"id": meta["id"], "name": candidato, "size": tam, "enlace": self.ed.enlace_archivo(str(meta["id"])), "carpeta": self.ed.enlace_carpeta(carpeta_id), "estado": "subido"}
        finally:
            self.drive.cercado.discard(carpeta_id)

    # ---- limpieza
    def limpiar_viejos(self) -> None:
        dias = self.cfg["limites"]["dias_guardar"]
        corte = self.reloj() - dias * 86400
        for persona in self.cfg["personas"]:
            for s in ("resultados", "procesadas"):
                d = self.raiz / "personas" / persona / s
                if not d.is_dir():
                    continue
                for x in d.iterdir():
                    try:
                        if x.is_file() and not x.is_symlink() and x.stat().st_mtime < corte:
                            x.unlink()
                    except OSError:
                        continue
        if self.trabajo.is_dir():
            for x in self.trabajo.iterdir():
                try:
                    if x.stat().st_mtime < self.reloj() - 86400:
                        shutil.rmtree(x, ignore_errors=True)
                except OSError:
                    continue


def da_id(texto: str) -> str:
    import drive_api as da

    i = da.id_de_carpeta(texto)
    if not i:
        raise ErrorPuerta(f"No reconozco el id de carpeta de Drive: {texto[:60]}")
    return i


def cmd_servir(cfg: dict, args) -> int:
    import drive_api as da

    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY", "")
    ruta = Path(cred_dir) / "google" if cred_dir else Path(cfg.get("credenciales", "/opt/broll-tiktok/secretos/google.json"))
    try:
        cred = da.cargar_credenciales(ruta)
        drive = da.Drive(cred)
        solo = getattr(args, "persona", None)
        if solo is not None and solo not in cfg["personas"]:
            raise ErrorPuerta(f"'{solo}' no esta en la lista de personas de la puerta")
        sistema = Puerta(cfg, drive)
        return sistema.servir(solo)
    except da.ErrorPermiso as e:
        print(f"Drive sin permiso: {e}", file=sys.stderr)
        return 4
    except ErrorPuerta as e:
        print(f"Configuracion de la puerta: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="puerta", description="Puerta de Drive del equipo de video")
    ap.add_argument("--esperar", type=int, default=25, help="minutos que espero la respuesta del servidor (por defecto 25)")
    ap.add_argument("--json", action="store_true", help="imprime el resultado como JSON")
    sub = ap.add_subparsers(dest="orden", required=True)
    t = sub.add_parser("traer", help="baja el material de una oferta (o la musica) de Drive")
    t.add_argument("oferta", nargs="?")
    t.add_argument("--musica", action="store_true")
    e = sub.add_parser("entregar", help="sube un video terminado a ANUNCIOS de la oferta")
    e.add_argument("oferta")
    e.add_argument("video")
    r = sub.add_parser("respaldar", help="sube una copia de tu proyecto a EDITORA CLAUDIA > PROYECTOS")
    r.add_argument("carpeta", nargs="?")
    sub.add_parser("estado", help="lo que hay en tu cola y los ultimos resultados")
    pu = sub.add_parser("publicar", help="pasa tus mejoras del editor (parte visual, guias y programas de montaje) a produccion, con revision automatica")
    pu.add_argument("--simular", action="store_true", help="solo dice que subiria")
    pu.add_argument("--solo-seguros", action="store_true", help="publica lo que no tiene conflicto aunque otros archivos si lo tengan")
    pu.add_argument("--esperar", type=int, default=argparse.SUPPRESS)
    sn = sub.add_parser("sincronizar", help="pone tu base al dia con produccion para esos archivos (--traer: copia tambien la version de produccion)")
    sn.add_argument("archivos", nargs="*")
    sn.add_argument("--traer", action="store_true")
    sn.add_argument("--al-dia", action="store_true", help="trae de produccion todo lo que tu no hayas cambiado y produccion si")
    sv = sub.add_parser("servir", help="(lo lanza systemd, una unidad por persona) atiende las peticiones pendientes")
    sv.add_argument("--persona", default=None)
    # --esperar y --json tambien despues del subcomando
    for s in (t, e, r):
        s.add_argument("--esperar", type=int, default=argparse.SUPPRESS)
        s.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        cfg = cargar_config()
        if args.orden in ("publicar", "sincronizar"):
            import editor_promover
            return {"publicar": editor_promover.cmd_publicar, "sincronizar": editor_promover.cmd_sincronizar}[args.orden](cfg, args)
        return {"traer": cmd_traer, "entregar": cmd_entregar, "respaldar": cmd_respaldar, "estado": cmd_estado, "servir": cmd_servir}[args.orden](cfg, args)
    except ErrorPuerta as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
