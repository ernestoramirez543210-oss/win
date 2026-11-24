"""Fabrica los efectos de sonido del editor en public/sfx/ (mp3 cortos) por sintesis, sin
descargar nada de internet: no tienen licencia de terceros.

Uso:
  python scripts/crear_sfx.py [--solo whoosh,pop] [--carpeta public/sfx]

Efectos: whoosh, whoosh_corto (transiciones), pop, pop_suave (aparicion de textos), click
(llamada a la accion), ding (notificacion), hit (golpe grave para el gancho), kaching (caja
registradora), click_camara (obturador). Cada uno se normaliza a un pico fijo y se recortan
los silencios del final. Si algun efecto no te gusta, pide a Claude uno nuevo o suelta un mp3
tuyo con el mismo nombre en public/sfx/ (y anotalo en LICENCIAS.txt).
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

# _comun primero: si el proyecto tiene su .venv, vuelve a lanzar este script con ese Python
# antes de intentar importar numpy y scipy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import PUBLIC, configurar_consola, correr, ejecutable, salir  # noqa: E402

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

SR = 48000


def tiempo(d: float) -> np.ndarray:
    return np.arange(int(SR * d)) / SR


def ruido(d: float, semilla: int) -> np.ndarray:
    return np.random.default_rng(semilla).standard_normal(int(SR * d))


def pasabanda(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    sos = signal.butter(2, [lo, min(hi, SR / 2 * 0.95)], btype="band", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def pasaaltos(x: np.ndarray, f: float) -> np.ndarray:
    return signal.sosfilt(signal.butter(2, f, btype="high", fs=SR, output="sos"), x)


def pasabajos(x: np.ndarray, f: float) -> np.ndarray:
    return signal.sosfilt(signal.butter(2, f, btype="low", fs=SR, output="sos"), x)


def barrido(x: np.ndarray, f_bajo: float, f_alto: float, ancho: float = 0.7) -> np.ndarray:
    """Ruido por un pasabanda cuyo centro sube y baja (efecto 'paso rapido'). Ventanas Hann
    superpuestas: el empalme entre bloques no se oye."""
    n, bloque = len(x), 2048
    y = np.zeros(n + 2 * bloque)
    ventana = np.hanning(2 * bloque)
    for i in range(0, n, bloque):
        u = i / n
        fc = f_bajo * (f_alto / f_bajo) ** np.sin(np.pi * u)
        seg = x[i : i + 2 * bloque]
        if len(seg) < 2 * bloque:
            seg = np.pad(seg, (0, 2 * bloque - len(seg)))
        y[i : i + 2 * bloque] += pasabanda(seg, fc * (1 - ancho / 2), fc * (1 + ancho / 2)) * ventana
    return y[:n]


def ataque(y: np.ndarray, ms: float) -> np.ndarray:
    k = max(1, int(SR * ms / 1000))
    y = y.copy()
    y[:k] *= np.linspace(0, 1, k)
    return y


def seno_con_caida(t: np.ndarray, f0: float, f1: float, tau_f: float) -> np.ndarray:
    f = f1 + (f0 - f1) * np.exp(-t / tau_f)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def campana(t: np.ndarray, parciales) -> np.ndarray:
    y = np.zeros_like(t)
    for f, amp, tau in parciales:
        y += amp * np.sin(2 * np.pi * f * t) * np.exp(-t / tau)
    return y


def poner(base: np.ndarray, extra: np.ndarray, en: float) -> np.ndarray:
    i = int(SR * en)
    base = base.copy()
    fin = min(len(base), i + len(extra))
    base[i:fin] += extra[: fin - i]
    return base


# ---------------------------------------------------------------- efectos
def fx_whoosh() -> np.ndarray:
    d = 0.9
    t = tiempo(d)
    env = np.sin(np.pi * t / d) ** 2 * np.exp(-t / 0.9) ** 0.2
    return barrido(ruido(d, 1), 350, 3800) * env


def fx_whoosh_corto() -> np.ndarray:
    d = 0.38
    t = tiempo(d)
    return barrido(ruido(d, 2), 600, 5200) * np.sin(np.pi * t / d) ** 2


def fx_pop() -> np.ndarray:
    t = tiempo(0.16)
    y = seno_con_caida(t, 720, 200, 0.018) * np.exp(-t / 0.035)
    return ataque(y, 1.5)


def fx_pop_suave() -> np.ndarray:
    t = tiempo(0.22)
    y = seno_con_caida(t, 420, 160, 0.03) * np.exp(-t / 0.06)
    return ataque(y, 4)


def fx_click() -> np.ndarray:
    t = tiempo(0.08)
    y = pasaaltos(ruido(0.08, 3), 1500) * np.exp(-t / 0.006) * 0.8
    y += 0.7 * np.sin(2 * np.pi * 1800 * t) * np.exp(-t / 0.004)
    return ataque(y, 0.3)


def fx_ding() -> np.ndarray:
    t = tiempo(1.4)
    y = campana(t, [(1318.5, 1.0, 0.9), (2637.0, 0.35, 0.5), (3951.0, 0.15, 0.3)])
    return ataque(y, 3)


def fx_hit() -> np.ndarray:
    t = tiempo(0.8)
    cuerpo = seno_con_caida(t, 150, 38, 0.06) * np.exp(-t / 0.22)
    golpe = pasabajos(ruido(0.8, 4), 700) * np.exp(-t / 0.05) * 0.6
    return np.tanh(1.8 * (cuerpo + golpe))


def fx_kaching() -> np.ndarray:
    t = tiempo(1.2)
    y = np.zeros_like(t)
    ka = pasabanda(ruido(0.09, 5), 1200, 4500) * np.exp(-tiempo(0.09) / 0.02)
    y = poner(y, ka * 0.9, 0.0)
    tc = tiempo(1.0)
    ching = campana(tc, [(2093.0, 1.0, 0.45), (3136.0, 0.55, 0.35), (4186.0, 0.3, 0.25)])
    y = poner(y, ataque(ching, 2), 0.11)
    y = poner(y, ataque(ching, 2) * 0.5, 0.22)
    return y


def fx_click_camara() -> np.ndarray:
    base = np.zeros(int(SR * 0.3))
    for en, tono in ((0.0, 900), (0.085, 650)):
        t = tiempo(0.06)
        tic = pasaaltos(ruido(0.06, 6 + int(en * 100)), 2000) * np.exp(-t / 0.008) + 0.5 * np.sin(2 * np.pi * tono * t) * np.exp(-t / 0.01)
        base = poner(base, ataque(tic, 0.3), en)
    return base


# nombre -> (funcion, pico objetivo en dBFS)
EFECTOS = {
    "whoosh": (fx_whoosh, -4.0),
    "whoosh_corto": (fx_whoosh_corto, -5.0),
    "pop": (fx_pop, -4.0),
    "pop_suave": (fx_pop_suave, -9.0),
    "click": (fx_click, -5.0),
    "ding": (fx_ding, -6.0),
    "hit": (fx_hit, -2.5),
    "kaching": (fx_kaching, -5.0),
    "click_camara": (fx_click_camara, -5.0),
}


def recortar_final(y: np.ndarray, umbral: float = 0.002) -> np.ndarray:
    """Quita el silencio del final y aplica un fundido corto para que no haga 'clic'."""
    idx = np.where(np.abs(y) > umbral)[0]
    if len(idx) == 0:
        return y
    y = y[: min(len(y), idx[-1] + int(SR * 0.02))].copy()
    k = min(len(y), int(SR * 0.008))
    y[-k:] *= np.linspace(1, 0, k)
    return y


def guardar_mp3(y: np.ndarray, destino: Path) -> None:
    wav = destino.with_suffix(".tmp.wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    r = correr([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(wav), "-ar", "48000", "-c:a", "libmp3lame", "-q:a", "2", str(destino)])
    wav.unlink(missing_ok=True)
    if r.returncode != 0:
        salir(f"ffmpeg fallo con {destino.name}: {r.stderr.strip()[:300]}")


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--solo", help="lista separada por comas, p. ej. whoosh,pop")
    ap.add_argument("--carpeta", default=str(PUBLIC / "sfx"))
    a = ap.parse_args()

    destino = Path(a.carpeta)
    destino.mkdir(parents=True, exist_ok=True)
    nombres = [n.strip() for n in a.solo.split(",")] if a.solo else list(EFECTOS)
    for n in nombres:
        if n not in EFECTOS:
            salir(f"Efecto desconocido: {n}. Opciones: {', '.join(EFECTOS)}")

    for nombre in nombres:
        funcion, pico_db = EFECTOS[nombre]
        y = recortar_final(funcion())
        y = y / (np.max(np.abs(y)) or 1.0) * (10 ** (pico_db / 20))
        guardar_mp3(y, destino / f"{nombre}.mp3")
        print(f"  {nombre}.mp3  {len(y) / SR:.2f} s  pico {pico_db} dBFS")
    print(f"LISTO: {len(nombres)} efectos en {destino}")


if __name__ == "__main__":
    main()
