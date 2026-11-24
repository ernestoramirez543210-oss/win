"""Une los clips de un anuncio con IA: recorta el silencio del inicio y del final de cada clip,
los pasa a 1080x1920 a 30 fps, los junta en orden y deja la voz a -14 LUFS.

Uso:
  python scripts/unir_clips.py --clips clips/01.mp4 clips/02.mp4 ... --salida <carpeta_del_anuncio>
                               [--voz-lufs -14] [--umbral-db -38] [--margen 0.12]

Salida en <carpeta_del_anuncio>:
  base.mp4     video unido (con el audio de los clips)   -> una sola toma en videoData.json (va silenciada)
  voz.wav      el audio unido y normalizado a -14 LUFS    -> audio.voz.src
  clips.json   donde cae cada clip en la linea de tiempo del anuncio (para poner textos y producto)

Despues: transcribir.py sobre voz.wav -> palabras para los subtitulos; y se sigue el flujo normal.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import RAIZ, configurar_consola, correr, duracion_seg, ejecutable, escribir_json, salir  # noqa: E402


def silencios(ruta: Path, umbral_db: float, minimo: float = 0.12) -> list[tuple[float, float | None]]:
    r = correr([ejecutable("ffmpeg"), "-hide_banner", "-nostats", "-i", str(ruta), "-af", f"silencedetect=noise={umbral_db}dB:d={minimo}", "-f", "null", "-"])
    inicios = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    fines = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [(s, fines[i] if i < len(fines) else None) for i, s in enumerate(inicios)]


def limites_con_voz(ruta: Path, umbral_db: float, margen: float) -> tuple[float, float]:
    """Segundo donde empieza y donde termina la voz, dejando un margen para no comerse palabras."""
    dur = duracion_seg(ruta)
    ini, fin = 0.0, dur
    for s, e in silencios(ruta, umbral_db):
        if s <= 0.05 and e is not None:
            ini = max(0.0, e - margen)
        elif e is None or e >= dur - 0.05:
            fin = min(dur, s + margen)
    if fin - ini < 0.5:
        return 0.0, dur
    return ini, fin


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", nargs="+", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--voz-lufs", type=float, default=-14.0)
    ap.add_argument("--umbral-db", type=float, default=-38.0, help="por debajo de esto se considera silencio")
    ap.add_argument("--margen", type=float, default=0.12, help="segundos que se dejan antes de la primera palabra y despues de la ultima")
    a = ap.parse_args()

    clips = [Path(c) for c in a.clips]
    for c in clips:
        if not c.is_file():
            salir(f"No existe el clip {c}")
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)

    ffmpeg = ejecutable("ffmpeg")
    linea, cursor, tramos = [], 0.0, []
    with tempfile.TemporaryDirectory(prefix="unir_", dir=RAIZ / ".cache" if (RAIZ / ".cache").is_dir() else None) as tmp:
        lista = Path(tmp) / "lista.txt"
        for i, c in enumerate(clips, 1):
            ini, fin = limites_con_voz(c, a.umbral_db, a.margen)
            pieza = Path(tmp) / f"p{i:03d}.mp4"
            r = correr(
                [
                    ffmpeg, "-y", "-loglevel", "error", "-ss", f"{ini:.3f}", "-to", f"{fin:.3f}", "-i", str(c),
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
                    "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
                    "-af", "aresample=48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(pieza),
                ]
            )
            if r.returncode != 0:
                salir(f"ffmpeg fallo con {c.name}: {r.stderr.strip()[:300]}")
            dur = duracion_seg(pieza)
            tramos.append({"clip": c.name, "recorte_inicio": round(ini, 2), "recorte_fin": round(fin, 2), "inicio": round(cursor, 3), "fin": round(cursor + dur, 3)})
            cursor += dur
            linea.append(f"file '{pieza.as_posix()}'")
            print(f"  {c.name}: voz de {ini:.2f} a {fin:.2f} s -> {dur:.2f} s", flush=True)
        lista.write_text("\n".join(linea) + "\n", encoding="utf-8")

        base = salida / "base.mp4"
        r = correr([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(base)])
        if r.returncode != 0:
            salir(f"ffmpeg no pudo unir los clips: {r.stderr.strip()[:300]}")

    # Voz: el audio del video unido, normalizado a -14 LUFS en dos pasadas.
    sys.argv = ["mezclar_audio.py", "--voz", str(base), "--salida", str(salida), "--voz-lufs", str(a.voz_lufs)]
    from mezclar_audio import main as mezclar

    mezclar()
    escribir_json(salida / "clips.json", {"duracion": round(cursor, 3), "clips": tramos})
    print(f"LISTO: {base} ({cursor:.2f} s), {salida / 'voz_norm.wav'} y {salida / 'clips.json'}")
    print("Nota: la voz queda en voz_norm.wav; usa ese archivo en audio.voz.src y base.mp4 como unica toma (va silenciada).")


if __name__ == "__main__":
    main()
