"""Autoprueba de la puerta de Drive del equipo de video (scripts/puerta_editor.py). Sin internet, sin llaves y sin tocar Drive: usa un Drive de mentira.

  python scripts/autoprueba_puerta.py

Lo de las carpetas y los archivos (O_NOFOLLOW, dir_fd, propietarios) solo existe en Linux: en Windows corren las comprobaciones de configuracion, del empaquetado y de los archivos del
servidor, y las demas se saltan (se dice cuales). En el servidor corren todas:
  /opt/anuncios/corredor-ctl ejecutar -- /opt/broll-tiktok/venv/bin/python -I -B /opt/anuncios/scripts/autoprueba_puerta.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puerta_editor as pe  # noqa: E402

LINUX = hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.name == "posix" and os.open in os.supports_dir_fd
AQUI = Path(__file__).resolve().parent
VPS = AQUI.parent / "vps"
bien = mal = saltadas = 0


def comprobar(nombre: str, condicion: bool, detalle: object = "") -> None:
    global bien, mal
    if condicion:
        bien += 1
    else:
        mal += 1
        print(f"  FALLO: {nombre}" + (f"  -> {detalle}" if detalle != "" else ""))


def saltar(motivo: str, n: int = 1) -> None:
    global saltadas
    saltadas += n
    print(f"  (se salta: {motivo})")


# ------------------------------------------------------------------ un Drive de mentira
class Resp:
    def __init__(self, contenido: bytes, estado: int = 200):
        self.status_code, self._c, self.headers = estado, contenido, {}

    def iter_content(self, n):
        for i in range(0, len(self._c), n):
            yield self._c[i:i + n]

    def json(self):
        return {}


class DriveFalso:
    def __init__(self, id_ofertas: str):
        import drive_api as da

        self.da = da
        self.cercado: set[str] = set()
        self.nodos: dict[str, dict] = {}
        self.n = 0
        self.subidas: list[dict] = []
        self.creaciones: list[tuple[str, str]] = []
        self.error_al_subir: Exception | None = None
        self.golden = self._nodo("MI NEGOCIO", None, carpeta=True)
        self.nodos[id_ofertas] = {"id": id_ofertas, "name": "OFERTAS LATAM", "mimeType": da.MIME_CARPETA, "parents": [self.golden], "contenido": b"", "trashed": False}
        self.id_ofertas = id_ofertas
        uno = self._nodo("OFERTA UNO", id_ofertas, carpeta=True)
        brolls = self._nodo("B-rolls", uno, carpeta=True)
        self._nodo("tt_1234567890123.mp4", brolls, contenido=b"video-falso-1" * 100)
        self._nodo("tt_1234567890124.mp4", brolls, contenido=b"video-falso-2" * 100)
        self._nodo("fuentes.csv", brolls, contenido=b"id,autor\n1,x\n")
        ref = self._nodo("REFERENCIA", uno, carpeta=True)
        self._nodo("REF1_referencia.mp4", ref, contenido=b"ref" * 100)
        self._nodo("OFERTA DOS", id_ofertas, carpeta=True)
        musica = self._nodo("BIBLIOTECA MUSICAL", self.golden, carpeta=True)
        cal = self._nodo("calida", musica, carpeta=True)
        self._nodo("pista1.mp3", cal, contenido=b"mp3" * 50)
        self.oferta_uno = uno

    def _nodo(self, nombre: str, padre: str | None, *, carpeta: bool = False, contenido: bytes = b"") -> str:
        self.n += 1
        i = f"id{self.n:028d}"
        self.nodos[i] = {"id": i, "name": nombre, "mimeType": self.da.MIME_CARPETA if carpeta else "video/mp4", "parents": [padre] if padre else [], "contenido": contenido, "trashed": False}
        return i

    def _vista(self, n: dict) -> dict:
        v = {"id": n["id"], "name": n["name"], "mimeType": n["mimeType"], "parents": n["parents"], "trashed": False}
        if n["mimeType"] != self.da.MIME_CARPETA:
            import hashlib

            v["size"] = str(len(n["contenido"]))
            v["md5Checksum"] = hashlib.md5(n["contenido"]).hexdigest()
        return v

    # ---- la misma «cerca» de drive_api.Drive
    def permitir(self, i: str) -> None:
        self.cercado.add(i)

    def _exigir(self, i: str) -> None:
        if i not in self.cercado:
            raise self.da.ErrorDrive("BUG: se intento escribir en una carpeta que no se declaro con permitir(): no se hace")

    def listar_hijos(self, i: str) -> list[dict]:
        return [self._vista(n) for n in self.nodos.values() if i in n["parents"]]

    def hallar_carpeta(self, padre: str, nombre: str):
        c = [n["id"] for n in self.nodos.values() if padre in n["parents"] and n["name"] == nombre and n["mimeType"] == self.da.MIME_CARPETA]
        return (c[0] if c else None), len(c)

    def hallar_o_crear_carpeta(self, padre: str, nombre: str) -> str:
        i, _ = self.hallar_carpeta(padre, nombre)
        if i:
            return i
        self._exigir(padre)
        self.creaciones.append((padre, nombre))
        return self._nodo(nombre, padre, carpeta=True)

    def carpeta(self, i: str) -> dict:
        n = self.nodos[i]
        return {"id": i, "name": n["name"], "escribible": True}

    def datos_de(self, i: str) -> dict:
        return self._vista(self.nodos[i])

    def subir_archivo(self, ruta, carpeta_id: str, nombre: str, mime: str = "video/mp4") -> dict:
        self._exigir(carpeta_id)
        if self.error_al_subir:
            raise self.error_al_subir
        datos = Path(ruta).read_bytes()
        i = self._nodo(nombre, carpeta_id, contenido=datos)
        self.subidas.append({"id": i, "nombre": nombre, "carpeta": carpeta_id, "ruta": str(ruta), "tam": len(datos)})
        return {"id": i, "name": nombre}

    def _pedir(self, metodo: str, url: str, **kw):
        m = re.search(r"/files/([A-Za-z0-9_-]+)", url)
        n = self.nodos.get(m.group(1)) if m else None
        if not n:
            return Resp(b"", 404)
        return Resp(n["contenido"], 200)

    @staticmethod
    def _explicar(r, que):
        import drive_api as da

        return da.ErrorDrive(f"falso: {que}")


# ------------------------------------------------------------------ preparacion
def armar(tmp: Path, **limites):
    raiz = tmp / "srv"
    cfg_ruta = tmp / "editor-puerta.json"
    id_o = "ID_DE_TU_CARPETA_OFERTAS_LATAM"
    cfg_ruta.write_text(json.dumps({"raiz": str(raiz), "personas": ["tester"], "carpeta_ofertas": "https://drive.google.com/drive/folders/" + id_o,
                                    "carpeta_respaldos": "EDITOR", "limites": limites}), encoding="utf-8")
    cfg = pe.cargar_config(cfg_ruta)
    spool = raiz / "personas" / "tester"
    for s in pe.SUBCARPETAS:
        (spool / s).mkdir(parents=True)
    (raiz / "estado" / "trabajo").mkdir(parents=True)
    drive = DriveFalso(id_o)
    log: list[str] = []
    uid = os.getuid() if hasattr(os, "getuid") else 0
    puerta = pe.Puerta(cfg, drive, registrar=log.append, uid_raiz=uid, uid_de=lambda p: uid, trabajo=raiz / "estado" / "trabajo")
    return cfg, drive, puerta, spool, log


def peticion(spool: Path, tipo: str, **datos) -> str:
    id_ = pe._nuevo_id()
    p = spool / "cola" / f"{id_}.json"
    p.write_text(json.dumps({"version": 1, "id": id_, "tipo": tipo, **datos}), encoding="utf-8")
    os.chmod(p, 0o660)
    return id_


def resultado(spool: Path, id_: str) -> dict:
    r = spool / "resultados" / f"{id_}.json"
    return json.loads(r.read_text(encoding="utf-8")) if r.is_file() else {}


def hacer_video(destino: Path, segundos: float = 2.0) -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=72x128:d={segundos}", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-shortest",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(destino)], capture_output=True, timeout=120)
        return r.returncode == 0 and destino.is_file()
    except (OSError, subprocess.SubprocessError):
        return False


def con_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


# ================================================================== pruebas
def pruebas_config() -> None:
    print("configuracion")
    with tempfile.TemporaryDirectory() as t:
        base = {"raiz": "/srv/editor", "personas": ["beto"], "carpeta_ofertas": "x", "carpeta_respaldos": "EDITOR"}

        def cfg(**cambios):
            d = {**base, **cambios}
            d = {k: v for k, v in d.items() if v is not None}
            f = Path(t) / "c.json"
            f.write_text(json.dumps(d), encoding="utf-8")
            try:
                return pe.cargar_config(f)
            except pe.ErrorPuerta as e:
                return e

        comprobar("una configuracion buena carga", isinstance(cfg(), dict))
        comprobar("sin raiz se rechaza", isinstance(cfg(raiz=None), pe.ErrorPuerta))
        comprobar("raiz relativa se rechaza", isinstance(cfg(raiz="srv/editor"), pe.ErrorPuerta))
        comprobar("raiz con .. se rechaza", isinstance(cfg(raiz="/srv/../etc"), pe.ErrorPuerta))
        comprobar("personas vacia se rechaza", isinstance(cfg(personas=[]), pe.ErrorPuerta))
        comprobar("personas con mayusculas o simbolos se rechaza", isinstance(cfg(personas=["Beto"]), pe.ErrorPuerta) and isinstance(cfg(personas=["a;b"]), pe.ErrorPuerta) and isinstance(cfg(personas=["../x"]), pe.ErrorPuerta))
        comprobar("personas repetidas se rechaza", isinstance(cfg(personas=["beto", "beto"]), pe.ErrorPuerta))
        comprobar("un limite desconocido o no positivo se rechaza", isinstance(cfg(limites={"video_mb": 0}), pe.ErrorPuerta) and isinstance(cfg(limites={"raro": 5}), pe.ErrorPuerta))
        comprobar("los limites por defecto se completan", cfg(limites={"video_mb": 10})["limites"]["pedidos_por_hora"] == 30)
        comprobar("el nombre de la carpeta de respaldos no puede traer rutas, ni minusculas, ni quedar vacio", all(isinstance(cfg(carpeta_respaldos=r), pe.ErrorPuerta) for r in ("../EDITOR", "editor", "EDITOR/x", "", "A", "https://drive.google.com/drive/folders/ID_DE_TU_CARPETA_DE_DRIVE_02")) and isinstance(cfg(), dict))
        f = Path(t) / "mala.json"
        f.write_text("no es json", encoding="utf-8")
        try:
            pe.cargar_config(f)
            comprobar("un JSON roto se rechaza", False)
        except pe.ErrorPuerta:
            comprobar("un JSON roto se rechaza", True)
    real = pe.cargar_config(VPS / "editor-puerta.json")
    comprobar("editor-puerta.json del repo es valido y trae a beto", "beto" in real["personas"] and real["raiz"] == "/srv/editor", real)
    comprobar("la carpeta de respaldos del repo es EDITOR (dentro de MI NEGOCIO) y la de ofertas la de OFERTAS LATAM", real["carpeta_respaldos"] == "EDITOR" and "ID_DE_TU_CARPETA_OFERTAS_LATAM" in real["carpeta_ofertas"])



def pruebas_nombres() -> None:
    print("nombres de archivo y de oferta")
    buenos = ["video.mp4", "AD1 final (v2).mp4", "mi-video_2026.MP4", "respaldo_20261029-1530.zip", "a.mov"]
    malos = ["", "../x.mp4", "a/b.mp4", "a\\b.mp4", ".oculto.mp4", "-rf.mp4", "x.exe", "x.mp4.exe", "a..b.mp4", "x" * 200 + ".mp4", "con\nsalto.mp4", "con;punto.mp4", "tildeñ.mp4", "x.mp4 ", "$(x).mp4"]
    comprobar("nombres buenos aceptados", all(pe._bien_nombre(n) == n for n in buenos), [n for n in buenos if pe._bien_nombre(n) != n])
    comprobar("nombres malos rechazados", all(pe._bien_nombre(n) is None for n in malos), [n for n in malos if pe._bien_nombre(n) is not None])
    comprobar("el id de peticion tiene la forma esperada", all(pe.RE_ID.match(pe._nuevo_id()) for _ in range(20)) and len({pe._nuevo_id() for _ in range(50)}) == 50)
    comprobar("una oferta con barras o saltos se rechaza", not pe.RE_OFERTA.match("a/b") and not pe.RE_OFERTA.match("a\\b") and not pe.RE_OFERTA.match("a\nb") and pe.RE_OFERTA.match("VIVENDO DE ARTESANATOS"))


def pruebas_zip() -> None:
    print("empaquetado del respaldo (lado de la persona)")
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "proyecto"
        (p / "node_modules" / "x").mkdir(parents=True)
        (p / "node_modules" / "x" / "a.js").write_text("x")
        (p / "anuncios" / "uno").mkdir(parents=True)
        (p / "anuncios" / "uno" / "videoData.json").write_text("{}")
        (p / "public" / "productos" / "z" / "broll-tiktok").mkdir(parents=True)
        (p / "public" / "productos" / "z" / "broll-tiktok" / "tt_1.mp4").write_bytes(b"x")
        (p / "public" / "productos" / "z" / "guiones").mkdir(parents=True)
        (p / "public" / "productos" / "z" / "guiones" / "g.txt").write_text("guion")
        (p / "grande.mp4").write_bytes(b"0" * (pe.MAX_ARCHIVO_RESPALDO + 10))
        (p / "x.parte").write_text("x")
        (p / "scripts").mkdir()
        (p / "scripts" / "a.py").write_text("print(1)")
        try:
            os.symlink("/etc/passwd", p / "enlace.txt")
            os.symlink(str(p / "scripts"), p / "enlace_dir")
            enlaces = True
        except (OSError, NotImplementedError, AttributeError):
            enlaces = False
        incluir, fuera = pe._lista_para_zip(p)
        rel = sorted(r for _, r in incluir)
        comprobar("el respaldo trae lo que es trabajo (videoData, guion, scripts)", rel == ["anuncios/uno/videoData.json", "public/productos/z/guiones/g.txt", "scripts/a.py"], rel)
        comprobar("deja fuera node_modules, b-rolls, archivos pesados y temporales", "node_modules/" in fuera and any("Drive" in k for k in fuera) and any("30 MB" in k for k in fuera), fuera)
        if enlaces:
            comprobar("no sigue enlaces simbolicos (ni a archivos ni a carpetas)", all("enlace" not in r for r in rel), rel)
        else:
            saltar("este sistema no deja crear enlaces simbolicos", 1)


def pruebas_archivos_del_servidor() -> None:
    print("archivos del servidor (unidad, vigilante, instalador, manifiesto)")
    svc = (VPS / "editor-puerta.service").read_text(encoding="utf-8")
    pth = (VPS / "editor-puerta.path").read_text(encoding="utf-8")
    comprobar("la unidad corre con el usuario puerta, sin privilegios", "User=puerta" in svc and "NoNewPrivileges=true" in svc and "CapabilityBoundingSet=\n" in svc + "\n")
    comprobar("la unidad recibe el permiso de Drive por LoadCredential (no por una ruta que la persona pueda leer)", "LoadCredential=google:/opt/broll-tiktok/secretos/google.json" in svc)
    comprobar("la unidad solo escribe en /srv/editor", re.findall(r"^ReadWritePaths=(.*)$", svc, re.M) == ["/srv/editor"], re.findall(r"^ReadWritePaths=(.*)$", svc, re.M))
    comprobar("la unidad esta aislada del sistema y de los otros servicios", all(x in svc for x in ("ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "/opt/radar-de-ofertas", "/opt/actions-runner", "/run/docker.sock", "/opt/anuncios/secretos")))
    comprobar("la unidad corre el script con Python aislado (-I) y el modo servir", "puerta_editor.py servir" in svc and "python -I" in svc)
    comprobar("el vigilante es una plantilla por persona (un comodin en la mitad de la ruta no despierta a systemd) y arranca la unidad de ESA persona", "PathExistsGlob=/srv/editor/personas/%i/cola/*.json" in pth and "personas/*/cola" not in pth.split("[Path]")[1] and "Unit=editor-puerta@%i.service" in pth)
    comprobar("la unidad es una plantilla por persona: el limite de arranques de una no frena a las demas, y solo ve el grupo de ESA persona", "SupplementaryGroups=ed-%i" in svc and "servir --persona %i" in svc and "StartLimitBurst" in svc)
    comprobar("la unidad no puede ver la carpeta de secretos del b-roll (el permiso llega por LoadCredential)", "/opt/broll-tiktok/secretos" in re.findall(r"^InaccessiblePaths=(.*)$", svc, re.M)[0])
    inst = (VPS / "instalar-editor-vps.sh").read_text(encoding="utf-8")
    comprobar("el instalador no usa usermod -U (dejaria la contrasena vacia) ni da sudo", "usermod -U" not in inst.replace("nunca se usa usermod -U", "") and "sudoers" not in inst and "NOPASSWD" not in inst)
    # La auditoria del 2026-10-29 encontro que el instalador (root) escribia dentro de la casa de la persona siguiendo enlaces simbolicos: nada de root dentro de ~ (todo va por como_usuario)
    peligro = re.compile(r"\b(install|chown|chmod|cp|rm|tee|ln|tar|touch|mkdir)\b|>\s*\"?\$\{?(home|ws)\b|sed -i")
    sospechosas = [l.strip() for l in inst.splitlines() if not l.strip().startswith("#") and re.search(r"\$\{?(home|ws)\b", l) and peligro.search(l.split("  #")[0]) and 'mv -T "$stage" "$ws"' not in l]
    comprobar("el instalador no deja que root escriba dentro de la casa de la persona (solo el renombrado final del espacio de trabajo)", not sospechosas, sospechosas)
    comprobar("el instalador valida la llave sin escribir archivos junto a ella", ".revisar" not in inst and "ssh-keygen -l -f /dev/stdin" in inst)
    comprobar("el espacio de trabajo se arma en una carpeta de root y se mueve entero (mv -T)", 'mv -T "$stage" "$ws"' in inst and "$BASE/.despliegue/espacio-" in inst)
    comprobar("--quitar-acceso tambien quita los trabajos de at, las unidades systemd --user y el autostart de la persona (hechos como ella), y corta su sesion de systemd", all(x in inst for x in ("atrm", 'rm -rf "$HOME/.config/systemd/user"', "terminate-user", 'systemctl stop "user@$(id -u "$p").service"', "crontab -r -u")))
    comprobar("el instalador no lee una llave que sea un enlace", '[ ! -L "$f" ]' in inst)
    comprobar("el instalador limita la llave (sin reenvio de puertos, agente ni X11)","no-port-forwarding,no-agent-forwarding,no-X11-forwarding" in inst)
    comprobar("el instalador no toca la configuracion de SSH global ni el firewall", "sshd_config" not in inst.replace("# ", "") and "ufw" not in inst and "iptables" not in inst)
    comprobar("el instalador comprueba que la persona no lee las llaves", "google.json" in inst and "apimart.key" in inst and "sudo -n true" in inst)
    for f in ("instalar-editor-vps.sh", "pesado"):
        contenido = (VPS / f).read_bytes()
        comprobar(f"{f} no tiene CRLF (en el servidor fallaria)", b"\r" not in contenido)
        sh = shutil.which("bash")
        if sh:
            r = subprocess.run([sh, "-n", str(VPS / f)], capture_output=True, text=True)
            comprobar(f"{f}: bash -n sin errores", r.returncode == 0, r.stderr[:200])
    guia = (VPS / "CLAUDE-VPS.md").read_text(encoding="utf-8")
    comprobar("la guia del editor en el servidor nombra todos los comandos", all(x in guia for x in ("puerta traer", "puerta entregar", "puerta respaldar", "puerta estado", "pesado", "OFERTAS_LATAM_DIR", "ANUNCIOS")))
    if not (VPS / "corredor-archivos.txt").is_file():
        saltar("el manifiesto del despliegue (corredor-archivos.txt) vive en el repo, no en el servidor", 2)
        return
    lista = (VPS / "corredor-archivos.txt").read_text(encoding="utf-8")
    nombres = [l.split()[0] for l in lista.splitlines() if l.strip() and not l.startswith("#")]
    comprobar("el despliegue sube la puerta, la unidad, el vigilante, la configuracion, el instalador y la guia", all(x in nombres for x in (
        "scripts/puerta_editor.py", "vps/instalar-editor-vps.sh", "vps/pesado", "vps/editor-puerta.service", "vps/editor-puerta.path", "vps/editor-puerta.json", "vps/CLAUDE-VPS.md")), [x for x in nombres if "puerta" in x])
    faltan = [l.split()[0] for l in lista.splitlines() if l.strip() and not l.startswith("#") and not (AQUI.parent / l.split()[0]).is_file()]
    comprobar("todo lo que lista el manifiesto existe en el repo", not faltan, faltan)


def pruebas_servidor() -> None:
    if not LINUX:
        saltar("el servidor de la puerta usa O_NOFOLLOW y dir_fd (solo Linux): corre en el servidor", 40)
        return
    print("servidor de la puerta: traer")
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t))
        a = peticion(spool, "traer", oferta="oferta uno")
        puerta.servir()
        r = resultado(spool, a)
        comprobar("traer una oferta existente sale bien (el nombre se busca sin importar mayusculas ni tildes)", r.get("ok") is True, r)
        local = Path(t) / "srv" / "drive" / "OFERTAS LATAM" / "OFERTA UNO"
        comprobar("baja los b-rolls y la referencia al espejo", (local / "B-rolls" / "tt_1234567890123.mp4").is_file() and (local / "REFERENCIA" / "REF1_referencia.mp4").is_file() and (local / "B-rolls" / "fuentes.csv").is_file())
        comprobar("el contenido bajado es igual al de Drive", (local / "B-rolls" / "tt_1234567890123.mp4").read_bytes() == b"video-falso-1" * 100)
        comprobar("la peticion paso a procesadas y no queda en la cola", not list((spool / "cola").glob("*.json")) and (spool / "procesadas" / f"{a}.json").is_file())
        comprobar("traer no escribio nada en Drive", not drive.subidas and not drive.creaciones, (drive.subidas, drive.creaciones))
        b = peticion(spool, "traer", oferta="OFERTA QUE NO EXISTE")
        puerta.servir()
        rb = resultado(spool, b)
        comprobar("una oferta que no existe se rechaza y dice cuales hay", rb.get("ok") is False and "OFERTA UNO" in rb.get("mensaje", "") and "OFERTA DOS" in rb.get("mensaje", ""), rb)
        c = peticion(spool, "traer", musica=True)
        puerta.servir()
        rc = resultado(spool, c)
        comprobar("traer la musica sale bien y la deja en drive/musica", rc.get("ok") is True and (Path(t) / "srv" / "drive" / "musica" / "calida" / "pista1.mp3").is_file(), rc)

    print("servidor de la puerta: peticiones malas")
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t))
        secreto = Path(t) / "secreto.json"
        secreto.write_text('{"refresh_token": "NO-DEBE-SALIR"}')
        # a) un enlace simbolico como peticion
        id_ = pe._nuevo_id()
        os.symlink(secreto, spool / "cola" / f"{id_}.json")
        # b) campos que no se conocen, version mala, id que no coincide, tipo raro, JSON roto, nombre raro
        p1 = peticion(spool, "traer", oferta="OFERTA UNO", comando="rm -rf /")
        id2 = pe._nuevo_id()
        (spool / "cola" / f"{id2}.json").write_text(json.dumps({"version": 2, "id": id2, "tipo": "traer", "oferta": "OFERTA UNO"}))
        id3 = pe._nuevo_id()
        (spool / "cola" / f"{id3}.json").write_text(json.dumps({"version": 1, "id": "otra", "tipo": "traer", "oferta": "OFERTA UNO"}))
        id4 = pe._nuevo_id()
        (spool / "cola" / f"{id4}.json").write_text(json.dumps({"version": 1, "id": id4, "tipo": "borrar", "oferta": "OFERTA UNO"}))
        id5 = pe._nuevo_id()
        (spool / "cola" / f"{id5}.json").write_text("{esto no es json")
        p6 = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="../../etc/passwd.mp4")
        p7 = peticion(spool, "traer", oferta="OFERTA UNO", musica=True)
        p8 = peticion(spool, "traer")
        (spool / "cola" / "raro.json").write_text("{}")
        (spool / "cola" / ".oculto.json").write_text("{}")
        puerta.servir()
        for nombre, i in (("un enlace simbolico", id_), ("campos desconocidos", p1), ("version distinta", id2), ("id que no coincide", id3), ("tipo desconocido", id4), ("JSON roto", id5),
                          ("nombre con ..", p6), ("oferta y musica a la vez", p7), ("traer sin decir que", p8)):
            r = resultado(spool, i)
            comprobar(f"se rechaza: {nombre}", r.get("ok") is False and r.get("mensaje"), r)
        comprobar("el enlace simbolico a un secreto no se leyo ni se copio a ningun lado", "NO-DEBE-SALIR" not in json.dumps(resultado(spool, id_)) and not drive.subidas)
        comprobar("nada de eso toco Drive", not drive.subidas and not drive.creaciones)
        comprobar("un nombre que no es de peticion se aparta de la cola (y los ocultos no se tocan)", not (spool / "cola" / "raro.json").exists() and (spool / "cola" / ".oculto.json").exists())
        comprobar("la cola quedo vacia de peticiones", not [x for x in os.listdir(spool / "cola") if not x.startswith(".")])

    print("servidor de la puerta: de quien es la peticion")
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t))
        puerta.uid_de = lambda p: os.getuid() + 12345         # la persona «de verdad» tendria otro numero de usuario: lo escrito por otro no vale
        a = peticion(spool, "traer", oferta="OFERTA UNO")
        puerta.servir()
        ra = resultado(spool, a)
        comprobar("una peticion escrita por otro usuario se rechaza", ra.get("ok") is False and "no es de esa persona" in ra.get("mensaje", ""), ra)
        puerta.uid_de = lambda p: os.getuid()
        puerta.uid_raiz = os.getuid() + 1                      # las carpetas tienen que ser de root: si no lo son, no se usan
        b = peticion(spool, "traer", oferta="OFERTA UNO")
        puerta.servir()
        comprobar("si las carpetas de la persona no son de root, no se atiende nada", not resultado(spool, b) and any("no es de root" in x for x in log), log[-3:])

    print("servidor de la puerta: entregar")
    if not con_ffprobe():
        saltar("no hay ffmpeg/ffprobe aqui para fabricar y revisar videos de prueba", 14)
    else:
        with tempfile.TemporaryDirectory() as t:
            cfg, drive, puerta, spool, log = armar(Path(t))
            video = Path(t) / "v.mp4"
            comprobar("(prueba) se fabrico un video de verdad", hacer_video(video))
            shutil.copy(video, spool / "entregas" / "anuncio final.mp4")
            a = peticion(spool, "entregar", oferta="oferta uno", archivo="anuncio final.mp4")
            puerta.servir()
            r = resultado(spool, a)
            comprobar("entregar un video bueno sale bien", r.get("ok") is True and "ANUNCIOS" in r.get("mensaje", "") and r["detalle"].get("enlace", "").startswith("https://drive.google.com/file/d/"), r)
            comprobar("lo subio UNA vez, a la carpeta ANUNCIOS de la oferta, con su tamano exacto", len(drive.subidas) == 1 and drive.nodos[drive.subidas[0]["carpeta"]]["name"] == "ANUNCIOS" and drive.nodos[drive.nodos[drive.subidas[0]["carpeta"]]["parents"][0]]["name"] == "OFERTA UNO" and drive.subidas[0]["tam"] == video.stat().st_size, drive.subidas)
            comprobar("lo que se subio sale de la copia privada del servicio, NO de la carpeta de la persona", str(Path(t) / "srv" / "estado" / "trabajo") in drive.subidas[0]["ruta"] and "entregas" not in drive.subidas[0]["ruta"], drive.subidas[0]["ruta"])
            comprobar("la copia privada y el archivo de entregas se limpiaron", not list((Path(t) / "srv" / "estado" / "trabajo").iterdir()) and not (spool / "entregas" / "anuncio final.mp4").exists())
            comprobar("lo unico que se creo en Drive fue ANUNCIOS", drive.creaciones == [(drive.oferta_uno, "ANUNCIOS")], drive.creaciones)
            # el mismo video otra vez: no se duplica
            shutil.copy(video, spool / "entregas" / "anuncio final.mp4")
            b = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="anuncio final.mp4")
            puerta.servir()
            comprobar("el mismo video otra vez no se vuelve a subir", resultado(spool, b).get("ok") is True and len(drive.subidas) == 1 and "ya estaba" in resultado(spool, b).get("mensaje", ""), resultado(spool, b))
            # otro video con el mismo nombre: _v2, nunca pisa
            otro = Path(t) / "v2.mp4"
            hacer_video(otro, 3.0)
            shutil.copy(otro, spool / "entregas" / "anuncio final.mp4")
            c = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="anuncio final.mp4")
            puerta.servir()
            rc = resultado(spool, c)
            comprobar("otro video con el mismo nombre se sube como _v2 (no pisa)", rc.get("ok") is True and len(drive.subidas) == 2 and drive.subidas[1]["nombre"] == "anuncio final_v2.mp4", (rc, drive.subidas))
            # no es un video
            (spool / "entregas" / "falso.mp4").write_bytes(b"esto no es un video" * 100)
            d = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="falso.mp4")
            puerta.servir()
            comprobar("un archivo que no es video se rechaza y no se sube", resultado(spool, d).get("ok") is False and len(drive.subidas) == 2, resultado(spool, d))
            # un enlace simbolico a un secreto
            secreto = Path(t) / "google.json"
            secreto.write_text('{"refresh_token": "NO-DEBE-SALIR"}')
            os.symlink(secreto, spool / "entregas" / "enlace.mp4")
            e = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="enlace.mp4")
            puerta.servir()
            comprobar("un enlace simbolico en entregas se rechaza (no se sigue) y no se sube nada", resultado(spool, e).get("ok") is False and len(drive.subidas) == 2, resultado(spool, e))
            # oferta que no existe: no se sube
            shutil.copy(video, spool / "entregas" / "huerfano.mp4")
            f = peticion(spool, "entregar", oferta="NO EXISTE", archivo="huerfano.mp4")
            puerta.servir()
            comprobar("una oferta que no existe: NO se sube a ningun lado", resultado(spool, f).get("ok") is False and len(drive.subidas) == 2 and drive.creaciones == [(drive.oferta_uno, "ANUNCIOS")], (resultado(spool, f), drive.creaciones))
            # tope de tamano
            puerta.cfg["limites"]["video_mb"] = 0.0001
            shutil.copy(video, spool / "entregas" / "grande.mp4")
            g = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="grande.mp4")
            puerta.servir()
            comprobar("un video por encima del tope se rechaza", resultado(spool, g).get("ok") is False and len(drive.subidas) == 2, resultado(spool, g))
            puerta.cfg["limites"]["video_mb"] = 1500
            # duracion
            puerta.cfg["limites"]["duracion_max_s"] = 1
            shutil.copy(video, spool / "entregas" / "largo.mp4")
            h = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="largo.mp4")
            puerta.servir()
            comprobar("un video mas largo que el tope de duracion se rechaza", resultado(spool, h).get("ok") is False and len(drive.subidas) == 2, resultado(spool, h))
            puerta.cfg["limites"]["duracion_max_s"] = 600
            # Drive falla
            drive.error_al_subir = pe.ErrorPuerta("simulado")
            shutil.copy(video, spool / "entregas" / "falla.mp4")
            i = peticion(spool, "entregar", oferta="OFERTA UNO", archivo="falla.mp4")
            puerta.servir()
            ri = resultado(spool, i)
            comprobar("si Drive falla, la persona recibe el motivo y la copia privada no queda", ri.get("ok") is False and "simulado" in ri.get("mensaje", "") and not list((Path(t) / "srv" / "estado" / "trabajo").iterdir()), ri)
            drive.error_al_subir = None

    print("servidor de la puerta: respaldar")
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t))
        z = Path(t) / "r.zip"
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr("a.txt", "hola")
        shutil.copy(z, spool / "entregas" / "proyecto_20261029-1530.zip")
        a = peticion(spool, "respaldar", archivo="proyecto_20261029-1530.zip")
        puerta.servir()
        r = resultado(spool, a)
        comprobar("un respaldo bueno sale bien", r.get("ok") is True, r)
        sub = drive.subidas[0] if drive.subidas else {}
        padre = drive.nodos.get(sub.get("carpeta"), {})
        abuelo = drive.nodos.get((padre.get("parents") or [""])[0], {})
        bisabuelo = drive.nodos.get((abuelo.get("parents") or [""])[0], {})
        comprobar("queda en MI NEGOCIO > EDITOR > tester", padre.get("name") == "tester" and abuelo.get("name") == "EDITOR" and bisabuelo.get("name") == "MI NEGOCIO", (padre.get("name"), abuelo.get("name"), bisabuelo.get("name")))
        comprobar("solo se crearon EDITOR y la carpeta de la persona (y EDITOR dentro de MI NEGOCIO, no dentro de una oferta)", [n for _, n in drive.creaciones] == ["EDITOR", "tester"] and drive.creaciones[0][0] == drive.golden, drive.creaciones)
        comprobar("el archivo de entregas se quito (y la copia privada tambien)", not (spool / "entregas" / "proyecto_20261029-1530.zip").exists() and not list((Path(t) / "srv" / "estado" / "trabajo").iterdir()))
        (spool / "entregas" / "mentira.zip").write_bytes(b"no soy un zip" * 50)
        b = peticion(spool, "respaldar", archivo="mentira.zip")
        puerta.servir()
        comprobar("un archivo que no es zip se rechaza (y no queda en entregas)", resultado(spool, b).get("ok") is False and len(drive.subidas) == 1 and not (spool / "entregas" / "mentira.zip").exists(), resultado(spool, b))
        c = peticion(spool, "respaldar", archivo="video.mp4")
        puerta.servir()
        comprobar("respaldar pide un .zip: un .mp4 se rechaza", resultado(spool, c).get("ok") is False)
        puerta.cfg["limites"]["respaldo_mb"] = 0.00001
        shutil.copy(z, spool / "entregas" / "otro.zip")
        d = peticion(spool, "respaldar", archivo="otro.zip")
        puerta.servir()
        comprobar("un respaldo por encima del tope se rechaza", resultado(spool, d).get("ok") is False and len(drive.subidas) == 1, resultado(spool, d))

    print("servidor de la puerta: topes y limpieza")
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t), pedidos_por_hora=3)
        ids = [peticion(spool, "traer", oferta="OFERTA DOS") for _ in range(6)]
        time.sleep(0.01)
        puerta.servir()
        oks = [resultado(spool, i).get("ok") for i in ids]
        rechazadas = [resultado(spool, i).get("mensaje", "") for i, ok in zip(ids, oks) if not ok]      # el orden dentro del mismo segundo es al azar: se cuentan, no se miran por posicion
        comprobar("el registro del ritmo vive en el estado de la puerta, no en la carpeta de la persona", (Path(t) / "srv" / "estado" / "ritmo-tester.json").is_file())
        for x in (spool / "procesadas").iterdir():          # la persona borra su carpeta de procesadas para empezar de cero: no sirve
            x.unlink()
        extra = peticion(spool, "traer", oferta="OFERTA DOS")
        puerta.servir()
        comprobar("borrar lo procesado no reinicia el tope de peticiones por hora", resultado(spool, extra).get("ok") is False and "tope" in resultado(spool, extra).get("mensaje", ""), resultado(spool, extra))
        comprobar("pasadas las peticiones por hora, el resto se rechaza con un mensaje claro", oks.count(True) == 3 and len(rechazadas) == 3 and all("tope" in m for m in rechazadas), (oks, rechazadas[:1]))
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t), disco_libre_min_gb=10 ** 9)
        a = peticion(spool, "traer", oferta="OFERTA UNO")
        puerta.servir()
        comprobar("sin disco suficiente no baja nada", resultado(spool, a).get("ok") is False and "libres" in resultado(spool, a).get("mensaje", ""), resultado(spool, a))
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t), dias_guardar=1)
        viejo = spool / "resultados" / "20200101T000000-aaaaaa.json"
        viejo.write_text("{}")
        os.utime(viejo, (time.time() - 5 * 86400,) * 2)
        reciente = spool / "resultados" / "20261029T000000-bbbbbb.json"
        reciente.write_text("{}")
        puerta.servir()
        comprobar("lo viejo se borra y lo reciente se queda", not viejo.exists() and reciente.exists())

    print("de punta a punta: la persona pide y el servidor contesta")
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t))
        parar = threading.Event()

        def servidor():
            while not parar.is_set():
                if any(not x.startswith(".") for x in os.listdir(spool / "cola")):
                    puerta.servir()
                time.sleep(0.2)

        hilo = threading.Thread(target=servidor, daemon=True)
        hilo.start()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = pe.pedir(cfg, "tester", "traer", {"oferta": "OFERTA UNO"}, 30, como_json=True)
                rc2 = pe.pedir(cfg, "tester", "traer", {"oferta": "NO EXISTE"}, 30, como_json=True)
            comprobar("pedir() deja la peticion, espera y devuelve 0 si salio bien", rc == 0)
            comprobar("pedir() devuelve 1 si el servidor dijo que no", rc2 == 1)
        finally:
            parar.set()
            hilo.join(5)
    with tempfile.TemporaryDirectory() as t:
        cfg, drive, puerta, spool, log = armar(Path(t))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = pe.pedir(cfg, "tester", "traer", {"oferta": "OFERTA UNO"}, 1, como_json=True)
        comprobar("pedir() devuelve 3 si nadie contesta a tiempo (y la peticion queda en la cola)", rc == 3 and len([x for x in os.listdir(spool / "cola") if not x.startswith(".")]) == 1)


def main() -> int:
    print(f"Autoprueba de la puerta de Drive del equipo de video ({'Linux: completa' if LINUX else 'sin Linux: parcial'})")
    pruebas_config()
    pruebas_nombres()
    pruebas_zip()
    pruebas_archivos_del_servidor()
    pruebas_servidor()
    print(f"\n{bien} comprobaciones bien, {mal} con fallo, {saltadas} saltadas" + ("" if mal == 0 else "  <-- HAY FALLOS"))
    return 0 if mal == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
