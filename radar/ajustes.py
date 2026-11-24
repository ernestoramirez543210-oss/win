"""
ajustes.py - las perillas del radar que el ciclo de mejora (revision_diaria.py) puede mover.

radar.py las lee al arrancar como valores por defecto de sus opciones; una opcion escrita en la linea de comandos
siempre manda. Solo existen las perillas de KNOBS, siempre dentro de [min, max]. "auto" = el revisor automatico puede
aplicarla solo, y solo dentro de [auto_min, auto_max]: salirse de ese rango (p. ej. apagar las busquedas de temporada)
o tocar una perilla sin "auto" cambia una regla del negocio y espera la aprobacion del dueño.
Cada cambio queda en ajustes_historial.jsonl. Nada de esto toca codigo.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from archivos import escribir_atomico

HERE = Path(os.environ.get("RADAR_DATOS") or Path(__file__).resolve().parent)   # carpeta de los DATOS (en el VPS /opt/radar-de-ofertas; el codigo vive aparte)
FILE = HERE / "ajustes.json"
HISTORY = HERE / "ajustes_historial.jsonl"

KNOBS = {
    # auto_max = 5 (el valor normal): hasta que la simulacion demuestre que el sistema se porta bien con Meta, solo puede
    # BAJAR la carga por su cuenta; subirla a 6 es decision del dueño.
    "max_kw": {"min": 3, "max": 6, "auto": True, "auto_min": 3, "auto_max": 5, "default": 5,
               "desc": "busquedas por corrida"},
    "max_temporada": {"min": 0, "max": 4, "auto": True, "auto_min": 1, "auto_max": 4, "default": 3,
                      "desc": "de esas busquedas, las que van a palabras de temporada"},
    "dias_temporada": {"min": 7, "max": 30, "auto": False, "default": 14,
                       "desc": "dias en el aire que se piden en las busquedas de temporada"},
    "min_activos": {"min": 5, "max": 10, "auto": False, "default": 8,
                    "desc": "anuncios activos minimos para dar por bueno a un anunciante"},
}


try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Bogota")
except Exception:                                        # sin base de zonas horarias (p. ej. Windows): reloj local
    _TZ = None


def ahora():
    """El reloj de todo el ciclo de mejora (un solo punto para poder simular dias en las pruebas). Siempre hora de Bogota,
    aunque alguien corra el programa a mano con otra zona (el dia que se revisa y los topes diarios dependen de ella)."""
    return datetime.now(_TZ).replace(tzinfo=None) if _TZ else datetime.now()


def load(path=None):
    """Valores vigentes: los de ajustes.json (solo si son validos) sobre los de fabrica. Avisa si descarta algo."""
    vals = {k: v["default"] for k, v in KNOBS.items()}
    p = path or FILE
    try:
        saved = json.loads(p.read_text(encoding="utf-8")).get("knobs", {})
        if not isinstance(saved, dict):
            raise ValueError("'knobs' no es un objeto")
    except OSError:
        return vals                                   # no existe todavia: valores de fabrica, sin ruido
    except (ValueError, AttributeError) as e:
        print(f"!! {p.name} no se pudo leer ({str(e)[:60]}): se usan los valores de fabrica.", file=sys.stderr)
        return vals
    for k, v in saved.items():
        if k in KNOBS and isinstance(v, int) and not isinstance(v, bool) and KNOBS[k]["min"] <= v <= KNOBS[k]["max"]:
            vals[k] = v
        else:
            print(f"!! {p.name}: se ignora {k}={v!r} (no valido)", file=sys.stderr)
    return vals


def valid(knob, value):
    """None si el valor se puede poner; si no, el motivo."""
    spec = KNOBS.get(knob)
    if spec is None:
        return f"'{knob}' no es una perilla permitida"
    if not isinstance(value, int) or isinstance(value, bool):
        return f"{knob} debe ser un numero entero"
    if not spec["min"] <= value <= spec["max"]:
        return f"{knob} debe estar entre {spec['min']} y {spec['max']}"
    return None


def save(vals, path=None):
    clean = {k: int(v) for k, v in vals.items() if k in KNOBS and valid(k, int(v)) is None}
    escribir_atomico(path or FILE, json.dumps({"knobs": clean, "actualizado": ahora().isoformat(timespec="seconds")},
                                              ensure_ascii=False, indent=1))


def log(entry, path=None):
    """Una linea por decision (propuesta, aplicacion, rechazo, reversion): el registro de por que cambio todo."""
    entry = dict(entry, ts=ahora().isoformat(timespec="seconds"))
    with open(path or HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


def history(path=None):
    """Las lineas del registro que son objetos; una linea danada o de otro tipo se ignora."""
    out = []
    try:
        # solo "\n" separa lineas: splitlines() tambien corta en U+2028/U+0085, que un nombre de anunciante puede traer
        # dentro del texto de una entrada y dejaria la entrada partida (y por tanto perdida)
        for line in (path or HISTORY).read_text(encoding="utf-8").split("\n"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except OSError:
        pass
    return out
