"""archivos.py - escritura atomica y convivencia segura en la carpeta del radar.

* escribir_atomico: si el proceso muere a mitad (tiempo agotado, disco lleno, reinicio), el archivo queda como estaba y
  nunca a medias. Se escribe a un temporal propio (nace con permisos 0600 y nombre unico), se fuerza a disco y se reemplaza
  de una sola vez. El modo final nunca es mas abierto que 0600: lo que aqui se escribe (token de Google, estado, ajustes)
  es de este usuario y de nadie mas; un archivo que alguna vez quedo en 0644 se corrige solo al reescribirse.
* candado: una sola cosa a la vez en la carpeta (el mismo `.candado` que toman los .sh con flock), tambien para quien
  corra los programas a mano.
* es_root: ni el radar ni la revision deben correr como root: dejarian archivos de root que despues el servicio (usuario
  radar) no puede escribir y todo fallaria sin avisar.
"""
import contextlib
import os
import stat
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:                      # Windows: el candado solo existe en el servidor
    fcntl = None


class CandadoOcupado(Exception):
    pass


def escribir_atomico(path, texto, encoding="utf-8"):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode) & 0o600)      # conserva el modo del archivo, sin abrirlo mas
        except FileNotFoundError:
            pass                                                          # archivo nuevo: queda con el 0600 de mkstemp
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def limpiar_temporales(carpeta, horas=24):
    """Borra los temporales de escrituras atomicas que un corte dejo a medias (nombre.xxxx.tmp) con mas de `horas`."""
    corte, n = time.time() - horas * 3600, 0
    try:
        for p in Path(carpeta).glob("*.tmp"):
            try:
                if p.is_file() and p.stat().st_mtime < corte:
                    p.unlink()
                    n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n


def es_root():
    """True si corre como root sin permiso expreso (RADAR_PERMITIR_ROOT=1, solo para quien sabe lo que hace)."""
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        return False
    return not (os.environ.get("RADAR_PERMITIR_ROOT") or os.environ.get("REVISION_PERMITIR_ROOT"))


def _heredado(carpeta):
    """True si quien nos lanzo (correr_radar.sh / correr_revision.sh) ya tiene el candado: el descriptor 9 que heredamos es
    el de `.candado` y el flock se sostiene desde ese mismo descriptor (volver a pedirlo desde otro lo bloquearia contra
    uno mismo). No se fia de una variable de entorno: se comprueba el candado de verdad."""
    try:
        a, b = os.fstat(9), os.stat(Path(carpeta) / ".candado")
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            return False
        fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)         # mismo descriptor de archivo: ya es nuestro (o queda tomado)
        return True
    except OSError:
        return False


@contextlib.contextmanager
def candado(carpeta, espera_s=0):
    """Toma el candado de la carpeta (flock sobre `.candado`, el mismo que usan correr_radar.sh y correr_revision.sh), para
    que el radar y la revision no se pisen tampoco cuando alguien los corre a mano. Si el .sh que nos lanzo ya lo tiene,
    se reconoce (ver _heredado). Espera hasta `espera_s` segundos; si no lo consigue lanza CandadoOcupado."""
    if fcntl is None or _heredado(carpeta):
        yield
        return
    fd = os.open(str(Path(carpeta) / ".candado"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fin = time.monotonic() + espera_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= fin:
                    raise CandadoOcupado("otra corrida del radar o de la revision esta usando la carpeta")
                time.sleep(2)
        yield
    finally:
        os.close(fd)                                   # cerrar el descriptor suelta el candado
