"""Mide con ffmpeg la voz y la musica, deja la voz a -14 LUFS y baja la musica hasta quedar
unos 15 dB por debajo de la voz.

Uso:
  python scripts/mezclar_audio.py --voz <voz.mp3|wav> --salida <carpeta> [--musica <pista.mp3>]
                                  [--voz-lufs -14] [--musica-db -15]

Salida en <carpeta>:
  voz_norm.wav         la voz a -14 LUFS            -> audio.voz.src
  musica_ajustada.mp3  la musica ya a (voz - 15) dB -> audio.musica.src con "volumen": 1
  mezcla.json          lo medido antes y despues

La musica queda ya al nivel correcto dentro del archivo: en videoData.json va con volumen 1
(los fundidos de entrada y salida los aplica el video, no este script).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola, correr, ejecutable, escribir_json, salir  # noqa: E402


def medir(ruta: Path, objetivo: float = -14.0) -> dict:
    """Primera pasada de loudnorm: devuelve lo medido (LUFS integrados, pico real, rango)."""
    r = correr(
        [
            ejecutable("ffmpeg"), "-hide_banner", "-nostats", "-i", str(ruta),
            "-af", f"loudnorm=I={objetivo}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
        ]
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        salir(f"No pude medir el volumen de {ruta}: {r.stderr.strip()[-300:]}")
    datos = json.loads(m.group(0))
    try:
        return {k: float(v) for k, v in datos.items() if k != "normalization_type"}
    except ValueError:
        salir(f"{ruta.name} parece estar en silencio total (no se puede medir el volumen).")


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voz", required=True)
    ap.add_argument("--musica")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--voz-lufs", type=float, default=-14.0)
    ap.add_argument("--musica-db", type=float, default=-15.0, help="dB de la musica respecto a la voz (por defecto -15)")
    a = ap.parse_args()

    voz, salida = Path(a.voz), Path(a.salida)
    if not voz.is_file():
        salir(f"No existe la voz: {voz}")
    salida.mkdir(parents=True, exist_ok=True)

    # ---- voz: ganancia + limitador a -1 dBFS, ajustada hasta caer en el objetivo.
    # loudnorm lineal no alcanza -14 LUFS en voces con picos altos (se queda corto para no pasar
    # el tope de pico); con un limitador la voz llega al nivel pedido sin saturar.
    antes = medir(voz, a.voz_lufs)
    voz_norm = salida / "voz_norm.wav"
    ganancia_voz = a.voz_lufs - antes["input_i"]
    for _ in range(5):
        r = correr(
            [
                ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(voz),
                "-af", f"volume={ganancia_voz:.2f}dB,alimiter=limit=0.89:level=0:attack=3:release=40",
                "-ar", "48000", "-c:a", "pcm_s16le", str(voz_norm),
            ]
        )
        if r.returncode != 0:
            salir(f"ffmpeg fallo normalizando la voz: {r.stderr.strip()[:300]}")
        despues = medir(voz_norm, a.voz_lufs)
        falta = a.voz_lufs - despues["input_i"]
        if abs(falta) <= 0.3:
            break
        ganancia_voz += falta
    if ganancia_voz > 14:
        print(f"AVISO: hubo que subir la voz {ganancia_voz:.0f} dB; la grabacion esta muy baja y puede traer ruido de fondo.", file=sys.stderr)
    resultado = {
        "voz": {
            "lufs_antes": round(antes["input_i"], 1),
            "lufs_despues": round(despues["input_i"], 1),
            "pico_real_dbtp": round(despues["input_tp"], 1),
            "archivo": str(voz_norm),
        },
    }

    # ---- musica: ganancia fija para quedar (voz + musica_db) LUFS
    if a.musica:
        musica = Path(a.musica)
        if not musica.is_file():
            salir(f"No existe la musica: {musica}")
        m_antes = medir(musica)
        objetivo_musica = despues["input_i"] + a.musica_db
        ganancia = objetivo_musica - m_antes["input_i"]
        musica_aj = salida / "musica_ajustada.mp3"
        r = correr(
            [
                ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(musica),
                "-af", f"volume={ganancia:.2f}dB,alimiter=limit=0.89:level=0", "-ar", "48000", "-c:a", "libmp3lame", "-q:a", "2", str(musica_aj),
            ]
        )
        if r.returncode != 0:
            salir(f"ffmpeg fallo ajustando la musica: {r.stderr.strip()[:300]}")
        m_despues = medir(musica_aj)
        resultado["musica"] = {
            "lufs_antes": round(m_antes["input_i"], 1),
            "ganancia_db": round(ganancia, 1),
            "lufs_despues": round(m_despues["input_i"], 1),
            "db_bajo_la_voz": round(despues["input_i"] - m_despues["input_i"], 1),
            "archivo": str(musica_aj),
        }

    escribir_json(salida / "mezcla.json", resultado)
    print(f"Voz: {resultado['voz']['lufs_antes']} -> {resultado['voz']['lufs_despues']} LUFS  ({voz_norm})")
    if "musica" in resultado:
        m = resultado["musica"]
        print(f"Musica: {m['lufs_antes']} -> {m['lufs_despues']} LUFS (ganancia {m['ganancia_db']} dB, queda {m['db_bajo_la_voz']} dB bajo la voz)  ({m['archivo']})")


if __name__ == "__main__":
    main()
