"""Fabrica musica de fondo PROPIA en public/musica/biblioteca/<estilo>/ por sintesis, sin descargar nada de internet: las melodias y los sonidos
los genera este codigo, asi que no tienen licencia de terceros (igual que los efectos de crear_sfx.py) y se pueden usar en anuncios sin avisar a nadie.

Uso:
  python scripts/crear_musica.py [--estilos calida,energica,emotiva,navidad] [--cuantas 3] [--segundos 100] [--semilla 1]
                                 [--carpeta public/musica/biblioteca] [--sobrescribir] [--analizar]
                                 [--drive <ruta>] [--sin-drive] [--sincronizar]

Estilos (cada uno va a su subcarpeta; la receta `musica.estilo` busca el nombre del estilo en la ruta de la pista):
  calida    84-92 BPM, tonalidad mayor: colchon suave, arpegio de pulsacion lenta, campanitas y un shaker muy bajo. Para ofertas de casa, familia, manualidades.
  energica  112-124 BPM: pulso con bombo, palmada y hi-hat, bajo en corcheas y un arpegio brillante. Para ofertas de resultados rapidos, dinero, ofertas.
  emotiva   66-76 BPM, tonalidad menor o mayor: colchon amplio, arpegio lento tipo piano y campanas escasas. Para historias, salud, fe, familia.
  navidad   92-104 BPM, mayor: caja de musica, cascabeles y colchon calido (sin ninguna cancion conocida: la melodia sale de reglas). Para ofertas de fin de ano.

Cada pista dura `--segundos` (100 por defecto: un anuncio dura hasta 60 s) con 1 s de entrada y 3 s de salida suaves, estereo, 48 kHz, mp3. Lo que sale depende solo de la
semilla: la misma semilla da la misma pista. Lo que se crea queda anotado en LICENCIAS.txt de la biblioteca. Si una pista no te gusta, borrala y corre otra vez con otra
`--semilla`, o suelta un mp3 tuyo en la biblioteca (anotalo en LICENCIAS.txt).

Honestamente: es musica sencilla, pensada para ir ~15 dB por debajo de la voz (mezclar_audio.py la baja sola); no reemplaza a una pista hecha por un musico.

Biblioteca compartida en Drive (pedido del dueno, 2026-10-21: "recuerda poner la musica en el drive para ir armando una biblioteca musical que sirva para proximas ofertas"):
con la biblioteca de siempre (public/musica/biblioteca) el script busca MI NEGOCIO > BIBLIOTECA MUSICAL en el Drive de escritorio de este PC (o la ruta de MUSICA_DRIVE_DIR en .env), baja
de ahi las pistas que faltan aqui, crea las que falten y sube a Drive las que falten alla (las suyas y las de otras personas: un mp3 que alguien suelte en una subcarpeta de Drive llega
a todos los editores). Nunca pisa ni borra: si una pista esta en los dos lados con otro tamano, la deja como esta y avisa. `--sincronizar` hace solo ese ir y venir, sin crear pistas.
Con otra --carpeta no toca Drive salvo que pases --drive <ruta> (asi una prueba nunca toca la biblioteca de verdad). Para quitar una pista de verdad, borrala en los dos sitios.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import wave
from datetime import datetime
from functools import lru_cache
from pathlib import Path

# _comun primero: si el proyecto tiene su .venv, vuelve a lanzar este script con ese Python antes de intentar importar numpy y scipy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import PUBLIC, RAIZ, configurar_consola, correr, ejecutable, salir  # noqa: E402

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

SR = 48000
ESTILOS = ("calida", "energica", "emotiva", "navidad")
PC = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}
MAYOR = [0, 2, 4, 5, 7, 9, 11]
MENOR = [0, 2, 3, 5, 7, 8, 10]
TRIADA = {"M": [0, 4, 7], "m": [0, 3, 7]}

# progresiones como (semitonos sobre la tonica, tipo de acorde)
PROG_MAYOR = [
    [(0, "M"), (7, "M"), (9, "m"), (5, "M")],        # I V vi IV
    [(0, "M"), (9, "m"), (5, "M"), (7, "M")],        # I vi IV V
    [(5, "M"), (0, "M"), (7, "M"), (9, "m")],        # IV I V vi
    [(9, "m"), (5, "M"), (0, "M"), (7, "M")],        # vi IV I V
]
PROG_MENOR = [
    [(0, "m"), (8, "M"), (3, "M"), (10, "M")],       # i VI III VII
    [(0, "m"), (5, "m"), (8, "M"), (7, "M")],        # i iv VI V
    [(0, "m"), (10, "M"), (8, "M"), (10, "M")],      # i VII VI VII
]

PERFILES = {
    "calida": {"bpm": (84, 92), "tonicas": ["C", "F", "G", "D", "Bb"], "modos": ["mayor"], "cpa": 2, "swing": 0.035, "brillo_pad": 1500,
               "mezcla": {"pad": 0.68, "arpegio": 0.62, "campana": 0.42, "bajo": 0.4, "shaker": 0.16}},
    "energica": {"bpm": (112, 124), "tonicas": ["G", "D", "A", "E", "C"], "modos": ["mayor"], "cpa": 1, "swing": 0.0, "brillo_pad": 3200,
                 "mezcla": {"pad": 0.5, "arpegio": 0.62, "campana": 0.26, "bajo": 0.55, "bombo": 0.7, "palmada": 0.48, "hat": 0.34}},
    "emotiva": {"bpm": (66, 76), "tonicas": ["A", "E", "D", "C", "F#"], "modos": ["menor", "mayor"], "cpa": 2, "swing": 0.0, "brillo_pad": 1700,
                "mezcla": {"pad": 0.8, "arpegio": 0.62, "campana": 0.32, "bajo": 0.38}},
    "navidad": {"bpm": (92, 104), "tonicas": ["C", "G", "F", "D"], "modos": ["mayor"], "cpa": 2, "swing": 0.02, "brillo_pad": 1900,
                "mezcla": {"pad": 0.58, "arpegio": 0.42, "campana": 0.66, "bajo": 0.34, "cascabel": 0.3}},
}


# ---------------------------------------------------------------- piezas de sintesis
def hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def tiempo(d: float) -> np.ndarray:
    return np.arange(int(SR * d)) / SR


def ruido(d: float, semilla: int) -> np.ndarray:
    return np.random.default_rng(semilla).standard_normal(int(SR * d))


def pasabajos(x: np.ndarray, f: float, orden: int = 2) -> np.ndarray:
    return signal.sosfilt(signal.butter(orden, min(f, SR / 2 * 0.95), btype="low", fs=SR, output="sos"), x)


def pasaaltos(x: np.ndarray, f: float, orden: int = 2) -> np.ndarray:
    return signal.sosfilt(signal.butter(orden, f, btype="high", fs=SR, output="sos"), x)


def pasabanda(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return signal.sosfilt(signal.butter(2, [lo, min(hi, SR / 2 * 0.95)], btype="band", fs=SR, output="sos"), x)


def cierra(y: np.ndarray, ms: float = 6.0) -> np.ndarray:
    """Un fundido de unos milisegundos al final de un sonido que se corta."""
    y = np.array(y, dtype=np.float32)
    k = min(len(y), max(1, int(SR * ms / 1000)))
    y[-k:] *= np.linspace(1.0, 0.0, k, dtype=np.float32)
    return y


def envolvente(n: int, ataque: float, soltar: float) -> np.ndarray:
    """Subida y bajada suaves (coseno) de una nota de n muestras."""
    a = max(1, min(n // 2, int(SR * ataque)))
    r = max(1, min(n // 2, int(SR * soltar)))
    env = np.ones(n)
    env[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    env[n - r:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)
    return env


@lru_cache(maxsize=256)
def tabla_onda(f: float, caida: float) -> np.ndarray:
    """Un ciclo de una onda rica en armonicos (cuerdas/colchon), limitada en banda para esa nota: amplitud 1/k^caida hasta ~7 kHz."""
    n = 4096
    fase = np.arange(n + 1) / n
    y = np.zeros(n + 1)
    for k in range(1, max(2, min(40, int(7000.0 / max(f, 30.0))))):
        y += np.sin(2 * np.pi * k * fase) / (k ** caida)
    return y / np.max(np.abs(y))


def osc(f: float, n: int, caida: float, desafine_cents: float = 0.0, fase0: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    fase = (f * 2.0 ** (desafine_cents / 1200.0) * t + fase0) % 1.0
    return np.interp(fase, np.linspace(0.0, 1.0, 4097), tabla_onda(round(f, 2), caida))


@lru_cache(maxsize=512)
def nota_pad(m: int, d: float) -> tuple[np.ndarray, np.ndarray]:
    """Nota de colchon: dos voces ligeramente desafinadas que se reparten distinto entre los dos oidos (ensancha sin perderse en mono), con subida lenta y bajada larga."""
    n = int(SR * (d + 1.6))
    f = hz(m)
    v1, v2, sub = osc(f, n, 1.35, -4, 0.0), osc(f, n, 1.35, 4, 0.37), osc(f / 2.0, n, 1.8, 0, 0.5)
    izq = v1 + 0.55 * v2 + 0.08 * sub
    der = 0.55 * v1 + v2 + 0.08 * sub
    env = envolvente(n, 0.9, 1.6)
    return (izq * env).astype(np.float32), (der * env).astype(np.float32)


@lru_cache(maxsize=512)
def nota_pluck(m: int, tau: float) -> np.ndarray:
    """Pulsacion suave (arpa / piano electrico): armonicos que se apagan mas rapido cuanto mas agudos, y un 'toque' corto de ruido al empezar."""
    f = hz(m)
    d = min(3.0, 5.0 * tau)
    t = tiempo(d)
    y = np.zeros_like(t)
    for k in range(1, 9):
        if f * k > 9000:
            break
        y += (1.0 / k) * np.sin(2 * np.pi * k * f * t) * np.exp(-t / (tau / k ** 0.75))
    toque = pasabajos(ruido(0.02, int(m) + 7), 2500) * np.exp(-tiempo(0.02) / 0.004) * 0.25
    y[: len(toque)] += toque
    y[: int(SR * 0.002)] *= np.linspace(0, 1, int(SR * 0.002))
    return cierra(y)


@lru_cache(maxsize=256)
def nota_campana(m: int, tau: float) -> np.ndarray:
    """Caja de musica / celesta: parciales casi armonicos con una pizca de inarmonico y cada uno con su caida."""
    f = hz(m)
    d = min(4.0, 6.0 * tau)
    t = tiempo(d)
    y = np.zeros_like(t)
    for rel, amp, dec in ((1.0, 1.0, 1.0), (2.0, 0.42, 0.62), (2.76, 0.22, 0.36), (4.07, 0.12, 0.24), (5.4, 0.06, 0.16)):
        if f * rel < 11000:
            y += amp * np.sin(2 * np.pi * f * rel * t) * np.exp(-t / (tau * dec))
    y[: int(SR * 0.0015)] *= np.linspace(0, 1, int(SR * 0.0015))
    return cierra(y)


@lru_cache(maxsize=128)
def nota_bajo(m: int, d: float) -> np.ndarray:
    f = hz(m)
    n = int(SR * (d + 0.08))
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * f * t) + 0.28 * np.sin(2 * np.pi * 2 * f * t) + 0.08 * np.sin(2 * np.pi * 3 * f * t)
    y = pasabajos(y, 520) * envolvente(n, 0.015, 0.09)
    return y.astype(np.float32)


def bombo() -> np.ndarray:
    t = tiempo(0.6)
    f = 45.0 + 85.0 * np.exp(-t / 0.035)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.15)
    clic = pasaaltos(ruido(0.01, 11), 1800) * np.exp(-tiempo(0.01) / 0.002) * 0.3
    y[: len(clic)] += clic
    return cierra(np.tanh(1.6 * y))


def palmada() -> np.ndarray:
    d = 0.22
    t = tiempo(d)
    base = pasabanda(ruido(d, 12), 900, 3200)
    env = np.zeros_like(t)
    for en, peso in ((0.0, 1.0), (0.011, 0.8), (0.022, 0.65)):
        i = int(SR * en)
        env[i:] += peso * np.exp(-(t[: len(t) - i]) / 0.006)
    env += 0.5 * np.exp(-t / 0.07)
    return cierra(base * env * 0.7)


def hat() -> np.ndarray:
    t = tiempo(0.09)
    return cierra(pasaaltos(ruido(0.09, 13), 7000) * np.exp(-t / 0.022) * 0.6)


def shaker() -> np.ndarray:
    t = tiempo(0.12)
    y = pasabanda(ruido(0.12, 14), 4200, 9500) * np.exp(-t / 0.05)
    y *= np.minimum(1.0, t / 0.012)
    return cierra(y * 0.6)


def cascabel() -> np.ndarray:
    t = tiempo(0.45)
    y = np.zeros_like(t)
    for i, f in enumerate((2793.0, 3520.0, 4186.0, 5274.0, 6272.0)):
        y += (0.5 / (1 + 0.15 * i)) * np.sin(2 * np.pi * f * t + i) * np.exp(-t / (0.16 - 0.015 * i))
    y += 0.5 * pasabanda(ruido(0.45, 15), 5000, 11000) * np.exp(-t / 0.05)
    y[: int(SR * 0.002)] *= np.linspace(0, 1, int(SR * 0.002))
    return cierra(y * 0.45, 12.0)


def respuesta_sala(semilla: int, seg: float = 1.9, tau: float = 0.55) -> np.ndarray:
    """Reverberacion sintetica: ruido que se apaga y se vuelve mas oscuro con el tiempo."""
    t = tiempo(seg)
    r = ruido(seg, semilla) * np.exp(-t / tau)
    r = 0.55 * pasabajos(r, 7000) + 0.45 * pasabajos(r, 2200)
    r[: int(SR * 0.012)] *= np.linspace(0, 1, int(SR * 0.012))
    return r / np.sqrt(np.sum(r ** 2))


# ---------------------------------------------------------------- composicion (solo teoria: sin sonido)
def voz_acorde(raiz_pc: int, tipo: str, previo: list[int] | None) -> list[int]:
    """Las notas del colchon para un acorde: cada nota del acorde en la octava mas cercana a la anterior (se mueve poco entre acordes) dentro de 58..80."""
    clases = [(raiz_pc + i) % 12 for i in TRIADA[tipo]]
    centro = 69 if not previo else sum(previo) / len(previo)
    voces = []
    for c in clases:
        opciones = [m for m in range(58, 81) if m % 12 == c]
        voces.append(min(opciones, key=lambda m: abs(m - centro)))
    return sorted(voces)


def componer(estilo: str, semilla: int, segundos: float) -> dict:
    """El plan de una pista: tonalidad, tempo y la lista de notas (instrumento, inicio, duracion, nota MIDI, fuerza, pan). No genera sonido."""
    rng = np.random.default_rng(semilla)
    P = PERFILES[estilo]
    bpm = int(rng.integers(P["bpm"][0], P["bpm"][1] + 1))
    beat = 60.0 / bpm
    compas = 4 * beat
    tonica = str(rng.choice(P["tonicas"]))
    modo = str(rng.choice(P["modos"]))
    escala = MAYOR if modo == "mayor" else MENOR
    progs = PROG_MAYOR if modo == "mayor" else PROG_MENOR
    prog = progs[int(rng.integers(len(progs)))]
    cpa = P["cpa"]
    n_comp = int(np.ceil((segundos + 1.0) / compas)) + 1
    t0 = PC[tonica]
    mezcla = P["mezcla"]
    notas: list[dict] = []

    def nota(inst, inicio, dur, m, vel, pan=0.0):
        notas.append({"inst": inst, "t": round(float(inicio), 4), "dur": round(float(dur), 4), "m": int(m), "vel": float(vel), "pan": float(pan)})

    acordes = []
    previo = None
    for c in range(n_comp):
        gr, tipo = prog[(c // cpa) % len(prog)]
        raiz = (t0 + gr) % 12
        if c % cpa == 0:
            previo = voz_acorde(raiz, tipo, previo)
        acordes.append({"raiz": raiz, "tipo": tipo, "voces": previo})
    en_escala = {(t0 + g) % 12 for g in escala}

    # el colchon: una nota larga por voz y por cambio de acorde, durante toda la pista
    for c in range(0, n_comp, cpa):
        for m in acordes[c]["voces"]:
            nota("pad", c * compas, cpa * compas + 0.15, m, mezcla["pad"] * (0.9 + 0.2 * rng.random()), pan=0.0)

    # que capas suenan en cada compas: entrada pausada (el colchon solo), las demas capas despues, y un final tranquilo
    ultimo = int((segundos - 3.0) / compas)
    def activo(c: int, capa: str) -> bool:
        if c >= ultimo - 1 and capa != "pad":
            return False
        if capa in ("bajo", "arpegio", "shaker", "cascabel"):
            return c >= 2
        if capa in ("bombo", "palmada", "hat"):
            if c < 4:
                return False
            return not ((c // 4) % 4 == 3 and c % 4 >= 2)                         # el ritmo respira: se calla los 2 ultimos compases de cada 16
        if capa == "campana":
            return c >= 4 and (c // cpa) % 2 == 1
        return True

    for c in range(n_comp):
        a = acordes[c]
        base = c * compas
        tonos = [m for m in a["voces"]] + [a["voces"][0] + 12]
        # el bajo: la raiz del acorde, siempre en 36..47
        raiz_bajo = 36 + a["raiz"]
        if "bajo" in mezcla and activo(c, "bajo"):
            if estilo == "energica":
                for i in range(8):
                    nota("bajo", base + i * beat / 2, beat / 2 * 0.85, raiz_bajo + (12 if i % 4 == 3 else 0), mezcla["bajo"] * (1.0 if i % 2 == 0 else 0.75))
            elif estilo == "navidad":
                nota("bajo", base, beat * 1.9, raiz_bajo, mezcla["bajo"])
                nota("bajo", base + 2 * beat, beat * 1.9, raiz_bajo + 7, mezcla["bajo"] * 0.8)
            else:
                nota("bajo", base, compas * 0.92, raiz_bajo, mezcla["bajo"])
                if estilo == "calida":
                    nota("bajo", base + 2.5 * beat, beat * 1.2, raiz_bajo + 7, mezcla["bajo"] * 0.6)
        # el arpegio sobre las notas del acorde
        if "arpegio" in mezcla and activo(c, "arpegio"):
            sub = {"calida": [0, 1, 2, 3, 2, 1, 2, 1], "energica": [None, 0, None, 2, None, 1, None, 3], "emotiva": [0, 2, 1, 3], "navidad": [0, 1, 2, 3, 2, 1, 2, 1]}[estilo]
            paso = compas / len(sub)
            for i, idx in enumerate(sub):
                if idx is None:
                    continue
                m = tonos[idx % len(tonos)] + (12 if estilo in ("energica", "navidad") else 0)
                while m < 60:
                    m += 12
                while m > 84:
                    m -= 12
                retraso = P["swing"] * beat if i % 2 == 1 else 0.0
                nota("arpegio", base + i * paso + retraso, paso, m, mezcla["arpegio"] * (1.0 if i % (len(sub) // 2 or 1) == 0 else 0.7) * (0.9 + 0.2 * rng.random()), pan=-0.35 + 0.7 * (i % 4) / 3)
        # percusion
        if estilo == "energica":
            if activo(c, "bombo"):
                for b in range(4):
                    nota("bombo", base + b * beat, 0.3, 0, mezcla["bombo"])
            if activo(c, "palmada"):
                for b in (1, 3):
                    nota("palmada", base + b * beat, 0.2, 0, mezcla["palmada"])
            if activo(c, "hat"):
                for i in range(8):
                    nota("hat", base + i * beat / 2 + (beat / 2 * 0.0), 0.08, 0, mezcla["hat"] * (1.0 if i % 2 else 0.55), pan=0.25)
        if "shaker" in mezcla and activo(c, "shaker"):
            for i in range(8):
                desf = beat / 2 * P["swing"] * 2 if i % 2 else 0.0
                nota("shaker", base + i * beat / 2 + desf, 0.1, 0, mezcla["shaker"] * (1.0 if i % 2 else 0.6), pan=0.3)
        if "cascabel" in mezcla and activo(c, "cascabel"):
            for b in range(4):
                nota("cascabel", base + b * beat, 0.4, 0, mezcla["cascabel"] * (1.0 if b % 2 == 0 else 0.7), pan=0.4 if b % 2 == 0 else -0.4)
                if b % 2 == 1:
                    nota("cascabel", base + b * beat + beat / 2, 0.3, 0, mezcla["cascabel"] * 0.45, pan=0.2)

    # la melodia (campanitas): frases de dos compases con notas de la escala, apoyadas en el acorde, que se repiten con pequenas variaciones
    plantillas = {"calida": [[(0, 1.5), (1.5, 0.5), (2, 1.0), (3, 1.0)], [(0, 1.0), (1, 1.0), (2, 2.0)]], "energica": [[(0, 0.5), (0.5, 0.5), (1.5, 0.5), (2, 1.0), (3, 0.5)], [(0, 1.0), (1, 0.5), (1.5, 0.5), (2, 2.0)]],
                  "emotiva": [[(0, 2.0), (2, 1.0), (3, 1.0)], [(0, 1.0), (1.5, 1.5), (3, 1.0)]], "navidad": [[(0, 1.0), (1, 0.5), (1.5, 0.5), (2, 1.0), (3, 1.0)], [(0, 0.5), (0.5, 0.5), (1, 1.0), (2, 2.0)]]}[estilo]
    if "campana" in mezcla:
        lista_escala = [m for m in range(66, 90) if m % 12 in en_escala]
        fraseA = plantillas[int(rng.integers(len(plantillas)))]
        fraseB = plantillas[int(rng.integers(len(plantillas)))]
        pos = int(rng.integers(len(lista_escala) // 2, len(lista_escala) - 3))
        for c in range(0, n_comp - 1, 2):
            if not activo(c, "campana") and not activo(c + 1, "campana"):
                continue
            if c >= ultimo - 1:
                break
            a = acordes[c]
            clases_acorde = {(a["raiz"] + i) % 12 for i in TRIADA[a["tipo"]]}
            frase = fraseA if (c // 2) % 2 == 0 else fraseB
            for (ini, dur) in frase:
                if rng.random() < 0.12:
                    continue
                paso = int(rng.choice([-2, -1, -1, 0, 1, 1, 2]))
                pos = int(np.clip(pos + paso, 2, len(lista_escala) - 3))
                if ini in (0.0, 2.0):           # en los tiempos fuertes cae en una nota del acorde
                    candidatos = [i for i in range(max(0, pos - 2), min(len(lista_escala), pos + 3)) if lista_escala[i] % 12 in clases_acorde]
                    if candidatos:
                        pos = min(candidatos, key=lambda i: abs(i - pos))
                nota("campana", c * compas + ini * beat, dur * beat, lista_escala[pos], mezcla["campana"] * (0.85 + 0.3 * rng.random()), pan=-0.5 + rng.random())
    notas = [e for e in notas if e["t"] < segundos + 1.0]
    return {"estilo": estilo, "semilla": semilla, "bpm": bpm, "tonica": tonica, "modo": modo, "compases": n_comp, "segundos": segundos, "compas_s": round(compas, 4), "cpa": cpa,
            "acordes": [[a["raiz"], a["tipo"]] for a in acordes], "escala_pcs": sorted(en_escala), "notas": notas}


# ---------------------------------------------------------------- de las notas al sonido
def renderizar(plan: dict) -> tuple[np.ndarray, np.ndarray]:
    seg = plan["segundos"]
    n = int(SR * (seg + 3.0))
    secos = [np.zeros(n), np.zeros(n)]          # lo que no lleva sala (bajo, percusion)
    humedo = [np.zeros(n), np.zeros(n)]         # lo que va a la sala (colchon, arpegio, campanas, cascabeles)

    def poner(bus, x, en, pan=0.0, ganancia=1.0):
        i = int(round(SR * en))
        if i >= n:
            return
        th = (pan + 1.0) * np.pi / 4.0
        gl, gr = np.cos(th) * ganancia, np.sin(th) * ganancia
        fin = min(n, i + len(x))
        bus[0][i:fin] += x[: fin - i] * gl
        bus[1][i:fin] += x[: fin - i] * gr

    P = PERFILES[plan["estilo"]]
    for e in plan["notas"]:
        i, t, d, m, v, pan = e["inst"], e["t"], e["dur"], e["m"], e["vel"], e["pan"]
        if t >= seg + 1.0:
            continue
        if i == "pad":
            l, r = nota_pad(m, round(d, 2))
            ini = int(round(SR * t))
            if ini < n:
                fin = min(n, ini + len(l))
                humedo[0][ini:fin] += l[: fin - ini] * v * 0.5
                humedo[1][ini:fin] += r[: fin - ini] * v * 0.5
        elif i == "arpegio":
            tau = {"calida": 0.5, "energica": 0.22, "emotiva": 0.9, "navidad": 0.55}[plan["estilo"]]
            poner(humedo, nota_pluck(m, tau), t, pan, 0.42 * v)
        elif i == "campana":
            poner(humedo, nota_campana(m, 0.8 if plan["estilo"] != "energica" else 0.45), t, pan, 0.4 * v)
        elif i == "bajo":
            poner(secos, nota_bajo(m, round(d, 2)), t, 0.0, 0.62 * v)
        elif i == "bombo":
            poner(secos, bombo(), t, 0.0, 0.9 * v)
        elif i == "palmada":
            poner(humedo, palmada(), t, 0.0, 0.5 * v)
        elif i == "hat":
            poner(secos, hat(), t, pan, 0.5 * v)
        elif i == "shaker":
            poner(secos, shaker(), t, pan, 0.55 * v)
        elif i == "cascabel":
            poner(humedo, cascabel(), t, pan, 0.55 * v)
    # el colchon se oscurece un poco (cada estilo, su brillo) y todo lo humedo pasa por la sala
    for c in (0, 1):
        humedo[c] = pasabajos(humedo[c], P["brillo_pad"] * 2.6)
    comun = respuesta_sala(101)
    salas = [0.7 * comun + 0.7 * respuesta_sala(202), 0.7 * comun + 0.7 * respuesta_sala(303)]
    mezcla = []
    for c in (0, 1):
        sala = signal.fftconvolve(humedo[c], salas[c])[:n]
        mezcla.append(secos[c] + 0.78 * humedo[c] + 0.5 * sala)
    izq, der = mezcla
    # master: sin graves que no se oyen, saturacion suave (no se pasa de 0 dBFS), pico a -3 dBFS, entrada de 1 s y salida de 3 s
    final = int(SR * seg)
    izq, der = pasaaltos(izq, 45)[:final], pasaaltos(der, 45)[:final]
    izq, der = izq + 0.55 * pasaaltos(izq, 2500), der + 0.55 * pasaaltos(der, 2500)
    casi_pico = max(np.percentile(np.abs(izq), 99.9), np.percentile(np.abs(der), 99.9)) or 1.0
    a, b = int(SR * 1.0), int(SR * 3.0)
    out = []
    for x in (izq, der):
        x = np.tanh(0.55 * x / casi_pico)                                   # el 99,9 % de las muestras queda bajo ~0,5: la saturacion solo toca algun pico suelto
        x[:a] *= np.linspace(0, 1, a) ** 1.5                                # entrada de 1 s
        x[-b:] *= np.linspace(1, 0, b) ** 1.5                               # salida de 3 s
        out.append(x)
    pico = max(np.max(np.abs(out[0])), np.max(np.abs(out[1]))) or 1.0
    k = 10 ** (-3.0 / 20) / pico                                            # el pico, ya con los fundidos, queda a -3 dBFS
    return out[0] * k, out[1] * k


def guardar_mp3(izq: np.ndarray, der: np.ndarray, destino: Path) -> None:
    wav = destino.with_suffix(".tmp.wav")
    datos = (np.clip(np.stack([izq, der], axis=1), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(datos.tobytes())
    r = correr([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(wav), "-ar", "48000", "-c:a", "libmp3lame", "-q:a", "2", str(destino)])
    wav.unlink(missing_ok=True)
    if r.returncode != 0:
        salir(f"ffmpeg fallo con {destino.name}: {r.stderr.strip()[:300]}")


def semilla_de(base: int, estilo: str, k: int) -> int:
    return base * 1000 + ESTILOS.index(estilo) * 100 + k


ENCABEZADO_LICENCIAS = ("LICENCIAS DE LA MUSICA DE FONDO\n\nLas pistas marcadas «propia» las sintetiza scripts/crear_musica.py: las notas y los sonidos los genera el codigo, "
                        "sin ninguna cancion, muestra ni sonido de terceros. No tienen licencia que cumplir ni hay que dar credito. Los mp3 que suelte una persona aqui se anotan "
                        "debajo con su fuente y su licencia.\n\n")


def anotar(carpeta: Path, filas: list[dict]) -> None:
    """LICENCIAS.txt de la biblioteca: que pistas son propias, con que semilla se hicieron."""
    ruta = carpeta / "LICENCIAS.txt"
    texto = ruta.read_text(encoding="utf-8") if ruta.is_file() else ENCABEZADO_LICENCIAS
    for f in filas:
        linea = f"propia | {f['archivo']} | {f['estilo']} | {f['bpm']} BPM, {f['tonica']} {f['modo']} | semilla {f['semilla']} | {f['segundos']:g} s | {datetime.now():%Y-%m-%d}\n"
        if f["archivo"] not in texto:
            texto += linea
    ruta.write_text(texto, encoding="utf-8")


def medir_pista(ruta: Path) -> dict:
    """Pico y sonoridad de una pista (ffmpeg): para ver que no se pasa ni queda muy baja."""
    r = correr([ejecutable("ffmpeg"), "-hide_banner", "-nostats", "-i", str(ruta), "-af", "ebur128=peak=true", "-f", "null", "-"])
    txt = r.stderr or ""
    lufs = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", txt)
    pico = re.findall(r"Peak:\s+(-?\d+(?:\.\d+)?) dBFS", txt)
    return {"lufs": float(lufs[-1]) if lufs else None, "pico_db": float(pico[-1]) if pico else None}


# --------------------------------------------------------------------------------------------------------------------------------------------
# La biblioteca musical compartida en Drive: MI NEGOCIO > BIBLIOTECA MUSICAL (una subcarpeta por estilo, igual que public/musica/biblioteca)
CARPETA_DRIVE = "BIBLIOTECA MUSICAL"
LEEME_DRIVE = (
    "BIBLIOTECA MUSICAL de MI NEGOCIO\n"
    "================================\n"
    "Aqui vive la musica de fondo de los anuncios. Cada subcarpeta es un estilo:\n"
    "  calida    casa, familia, manualidades\n"
    "  energica  resultados rapidos, dinero, ofertas\n"
    "  emotiva   historias, salud, fe\n"
    "  navidad   ofertas de fin de ano\n"
    "El editor de anuncios usa estas pistas (las copia a public/musica/biblioteca de su PC) y las baja ~15 dB por debajo de la voz.\n\n"
    "- Las pistas calida_01, energica_01... las crea scripts/crear_musica.py por codigo: no hay ninguna cancion ni muestra de terceros, asi que no tienen licencia que cumplir. Estan anotadas en LICENCIAS.txt.\n"
    "- Para sumar musica: suelta tus mp3 en la subcarpeta del estilo (o en public/musica/biblioteca del editor) y corre `python scripts/crear_musica.py --sincronizar`: sube lo que falte aqui y baja lo que falte alla.\n"
    "  Suma solo musica que tengas derecho a usar en anuncios pagos y anota su fuente y su licencia en LICENCIAS.txt.\n"
    "- Nada se borra ni se pisa solo. Para quitar una pista, borrala en los dos sitios (aqui y en la carpeta del editor); si no, vuelve a copiarse.\n"
)


def leer_env(nombre: str) -> str:
    """Un valor de las variables de entorno o del archivo .env (NOMBRE=valor). Nunca lo imprime."""
    valor = os.environ.get(nombre, "").strip()
    env = RAIZ / ".env"
    if not valor and env.is_file():
        for linea in env.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea.startswith(nombre + "="):
                valor = linea.split("=", 1)[1].strip().strip('"').strip("'")
    return valor


def es_la_biblioteca_real(carpeta: Path) -> bool:
    """True si `carpeta` es public/musica/biblioteca de esta instalacion (la que usa el montaje). Solo con esa se busca Drive sola: asi una prueba o una carpeta de ensayo nunca toca la de Drive."""
    return Path(carpeta).resolve() == (PUBLIC / "musica" / "biblioteca").resolve()


def biblioteca_en_drive(dada: str = "", crear: bool = False) -> Path | None:
    """MI NEGOCIO > BIBLIOTECA MUSICAL en el Drive de escritorio de ESTE PC. Manda la ruta que se pase; si no, MUSICA_DRIVE_DIR (variable o .env); si no, se miran las unidades de Windows.
    Con crear=True la carpeta se crea si no existe (en las unidades, solo si ya existe MI NEGOCIO). None si este PC no tiene Drive o no se pudo crear."""
    dada = (dada or leer_env("MUSICA_DRIVE_DIR")).strip()
    if dada:
        ruta = Path(dada)
        if not ruta.is_dir() and crear:
            try:
                ruta.mkdir(parents=True, exist_ok=True)
            except OSError:
                return None
        return ruta if ruta.is_dir() else None
    if os.name != "nt":
        return None
    for letra in "GHIJKLMNOPQRSTUVWXYZDEF":
        for mi_unidad in ("Mi unidad", "My Drive"):
            golden = Path(f"{letra}:/") / mi_unidad / "MI NEGOCIO"
            if not golden.is_dir():
                continue
            ruta = golden / CARPETA_DRIVE
            if not ruta.is_dir() and crear:
                try:
                    ruta.mkdir(exist_ok=True)
                except OSError:
                    continue
            if ruta.is_dir():
                return ruta
    return None


def nombres(biblioteca: Path) -> list[str]:
    """Los mp3 de una biblioteca como rutas relativas (calida/calida_01.mp3), sin las carpetas _prueba."""
    if not biblioteca.is_dir():
        return []
    return sorted(p.relative_to(biblioteca).as_posix() for p in biblioteca.rglob("*.mp3")
                  if p.is_file() and not any(parte.startswith("_prueba") for parte in p.relative_to(biblioteca).parts))


def copiar_faltantes(origen: Path, destino: Path) -> dict[str, list[str]]:
    """Copia a `destino` las pistas de `origen` que alli no estan (misma ruta dentro de la biblioteca). Nunca pisa ni borra: una pista que ya esta con el mismo tamano cuenta como igual, y con otro
    tamano se deja como esta y se avisa. Se copia a un .tmp y se renombra al final (Drive no sube los .tmp), para que nunca quede una pista a medias."""
    r: dict[str, list[str]] = {"copiadas": [], "iguales": [], "distintas": [], "fallidas": []}
    for rel in nombres(origen):
        fuente, meta = origen / rel, destino / rel
        parcial = meta.with_name(meta.name + ".tmp")
        try:
            if meta.is_file():
                r["iguales" if meta.stat().st_size == fuente.stat().st_size else "distintas"].append(rel)
                continue
            meta.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fuente, parcial)
            os.replace(parcial, meta)
            r["copiadas"].append(rel)
        except OSError as e:
            r["fallidas"].append(f"{rel} ({type(e).__name__}: {str(e)[:80]})")
            try:
                parcial.unlink(missing_ok=True)
            except OSError:
                pass
    return r


def fusionar_licencias(origen: Path, destino: Path, archivos: list[str]) -> None:
    """Pasa a LICENCIAS.txt de `destino` la linea que `origen` tiene de cada pista de `archivos` (o una linea «sin anotar» si no la tiene), sin repetir las que ya estan."""
    ruta_o, ruta_d = origen / "LICENCIAS.txt", destino / "LICENCIAS.txt"
    lineas_o = ruta_o.read_text(encoding="utf-8").splitlines() if ruta_o.is_file() else []
    texto = ruta_d.read_text(encoding="utf-8") if ruta_d.is_file() else ENCABEZADO_LICENCIAS
    nuevo = texto
    for a in archivos:
        if a in nuevo:
            continue
        linea = next((fila for fila in lineas_o if f"| {a} |" in fila), None) or f"sin anotar | {a} | {datetime.now():%Y-%m-%d} | origen y licencia: de quien la puso en la biblioteca (anotalos aqui si los sabes)"
        nuevo += ("" if nuevo.endswith("\n") else "\n") + linea + "\n"
    if nuevo != texto:
        ruta_d.parent.mkdir(parents=True, exist_ok=True)
        ruta_d.write_text(nuevo, encoding="utf-8")


def dejar_leeme(drive: Path) -> None:
    """LEEME.txt de la biblioteca de Drive: se escribe una vez, si no esta."""
    ruta = drive / "LEEME.txt"
    if not ruta.is_file():
        try:
            ruta.write_text(LEEME_DRIVE, encoding="utf-8")
        except OSError as e:
            print(f"  AVISO: no pude escribir LEEME.txt en Drive ({type(e).__name__})")


def resumen(titulo: str, r: dict[str, list[str]]) -> None:
    print(f"  {titulo}: {len(r['copiadas'])} pista(s) copiada(s), {len(r['iguales'])} ya estaban")
    for rel in r["copiadas"]:
        print(f"    + {rel}")
    for rel in r["distintas"]:
        print(f"  AVISO: {rel} ya esta al otro lado con otro tamano: no se toco (si quieres cambiarla, borra una de las dos y vuelve a correr)")
    for rel in r["fallidas"]:
        print(f"  AVISO: no se pudo copiar {rel}")


def drive_de(a: argparse.Namespace, carpeta: Path) -> Path | None:
    """La biblioteca de Drive que toca usar, o None. Con la biblioteca de siempre se busca sola; con otra --carpeta solo si se pasa --drive."""
    if a.drive:
        ruta = biblioteca_en_drive(a.drive, crear=True)
        if ruta is None:
            salir(f"No pude abrir ni crear la carpeta de Drive que pasaste en --drive: {a.drive}")
        return ruta
    if es_la_biblioteca_real(carpeta):
        ruta = biblioteca_en_drive("", crear=True)
        if ruta is None:
            if a.sincronizar:
                salir("No encontre la biblioteca musical en Drive (MI NEGOCIO > BIBLIOTECA MUSICAL) en este PC. Si tu Drive de escritorio esta en otra ruta, pon MUSICA_DRIVE_DIR=<ruta> en .env.")
            print("  (Drive no esta en este PC: la musica queda solo aqui; con MUSICA_DRIVE_DIR en .env se le dice donde esta)")
        return ruta
    if a.sincronizar:
        salir("--sincronizar con otra --carpeta pide tambien --drive <ruta>.")
    return None


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--estilos", default=",".join(ESTILOS), help="lista separada por comas: " + ", ".join(ESTILOS))
    ap.add_argument("--cuantas", type=int, default=3, help="cuantas pistas de cada estilo (3 por defecto)")
    ap.add_argument("--segundos", type=float, default=100.0, help="duracion de cada pista (100 por defecto)")
    ap.add_argument("--semilla", type=int, default=1, help="la misma semilla da las mismas pistas; cambiala para tener otras")
    ap.add_argument("--carpeta", default=str(PUBLIC / "musica" / "biblioteca"))
    ap.add_argument("--sobrescribir", action="store_true", help="rehace las pistas que ya existen (por defecto salta las que ya estan)")
    ap.add_argument("--analizar", action="store_true", help="mide el pico y la sonoridad de cada pista nueva")
    ap.add_argument("--drive", default="", help="la biblioteca musical en Drive (MI NEGOCIO > BIBLIOTECA MUSICAL). Con la biblioteca de siempre la busca sola (MUSICA_DRIVE_DIR en .env o el Drive de escritorio)")
    ap.add_argument("--sin-drive", action="store_true", help="no sube nada a Drive ni baja nada de ahi")
    ap.add_argument("--sincronizar", action="store_true", help="no crea pistas: baja de Drive las que faltan aqui y sube las que faltan alla")
    a = ap.parse_args()

    estilos = [e.strip() for e in a.estilos.split(",") if e.strip()]
    for e in estilos:
        if e not in ESTILOS:
            salir(f"Estilo desconocido: {e}. Opciones: {', '.join(ESTILOS)}")
    if not 5 <= a.segundos <= 600 or not 1 <= a.cuantas <= 20:
        salir("--segundos va de 5 a 600 y --cuantas de 1 a 20.")
    carpeta = Path(a.carpeta)
    if a.sin_drive and (a.sincronizar or a.drive):
        salir("--sin-drive no va junto con --sincronizar ni con --drive.")
    drive = None if a.sin_drive else drive_de(a, carpeta)
    abajo = arriba = {"fallidas": []}
    if drive is not None:
        print(f"Drive: {drive}")
        abajo = copiar_faltantes(drive, carpeta)                            # primero lo que ya hay en Drive: un PC nuevo no rehace lo que existe
        resumen("de Drive aqui", abajo)
        fusionar_licencias(drive, carpeta, nombres(drive))
    hechas: list[dict] = []
    for estilo in ([] if a.sincronizar else estilos):
        destino_dir = carpeta / estilo
        destino_dir.mkdir(parents=True, exist_ok=True)
        for k in range(1, a.cuantas + 1):
            destino = destino_dir / f"{estilo}_{k:02d}.mp3"
            if destino.exists() and not a.sobrescribir:
                print(f"  = {estilo}/{destino.name}  ya estaba")
                continue
            plan = componer(estilo, semilla_de(a.semilla, estilo, k), a.segundos)
            izq, der = renderizar(plan)
            guardar_mp3(izq, der, destino)
            fila = {"archivo": f"{estilo}/{destino.name}", "estilo": estilo, "bpm": plan["bpm"], "tonica": plan["tonica"], "modo": plan["modo"], "semilla": plan["semilla"], "segundos": a.segundos}
            hechas.append(fila)
            extra = ""
            if a.analizar:
                m = medir_pista(destino)
                extra = f"  | {m['lufs']} LUFS, pico {m['pico_db']} dBFS"
            print(f"  + {fila['archivo']}  {plan['bpm']} BPM, {plan['tonica']} {plan['modo']}, {a.segundos:g} s, {destino.stat().st_size / 1e6:.1f} MB{extra}")
    if hechas:
        anotar(carpeta, hechas)
    if a.sincronizar:
        print("Solo se sincroniza: no se crea ninguna pista.")
    else:
        print(f"LISTO: {len(hechas)} pista(s) nueva(s) en {carpeta}")
    if drive is not None:
        arriba = copiar_faltantes(carpeta, drive)                           # y al final lo nuevo (y lo que alguien soltara aqui) sube a Drive
        resumen("de aqui a Drive", arriba)
        fusionar_licencias(carpeta, drive, nombres(carpeta))
        dejar_leeme(drive)
        if a.sincronizar:
            print(f"LISTO: la biblioteca de {carpeta} y la de Drive quedaron al dia")
    if a.sincronizar and (abajo["fallidas"] or arriba["fallidas"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
