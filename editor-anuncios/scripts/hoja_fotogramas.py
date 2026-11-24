"""Junta los fotogramas de prueba de un anuncio en UNA sola imagen (con su segundo), para
revisarlos de un vistazo antes del render completo.

Uso:
  python scripts/hoja_fotogramas.py <carpeta_pruebas> [--columnas 4] [--ancho 300]

Lee los f_*.jpg de la carpeta (los que saca `node scripts/remotion.mjs fotogramas`; el numero
del archivo es el cuadro y los segundos salen de --fps, por defecto 30) y escribe hoja.jpg.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola, salir  # noqa: E402


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta")
    ap.add_argument("--columnas", type=int, default=4)
    ap.add_argument("--ancho", type=int, default=300, help="ancho de cada fotograma en la hoja")
    ap.add_argument("--fps", type=float, default=30.0)
    a = ap.parse_args()

    from PIL import Image, ImageDraw, ImageFont

    carpeta = Path(a.carpeta)
    fotos = sorted(carpeta.glob("f_*.jpg"))
    if not fotos:
        salir(f"No hay fotogramas f_*.jpg en {carpeta}")
    imgs = []
    for f in fotos:
        im = Image.open(f).convert("RGB")
        im.thumbnail((a.ancho, a.ancho * 3))
        imgs.append((int(f.stem.split("_")[1]), im))
    celda_alto = max(im.height for _, im in imgs)
    filas = (len(imgs) + a.columnas - 1) // a.columnas
    hoja = Image.new("RGB", (a.columnas * (a.ancho + 8) + 8, filas * (celda_alto + 8) + 8), (25, 25, 25))
    d = ImageDraw.Draw(hoja)
    try:
        fuente = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 22)
    except OSError:
        fuente = ImageFont.load_default()
    for i, (cuadro, im) in enumerate(imgs):
        x, y = 8 + (i % a.columnas) * (a.ancho + 8), 8 + (i // a.columnas) * (celda_alto + 8)
        hoja.paste(im, (x, y))
        d.rectangle([x, y, x + 96, y + 30], fill=(0, 0, 0))
        d.text((x + 6, y + 3), f"{cuadro / a.fps:.1f} s", fill=(255, 225, 77), font=fuente)
    salida = carpeta / "hoja.jpg"
    hoja.save(salida, quality=82)
    print(f"LISTO: {salida} ({len(imgs)} fotogramas)")


if __name__ == "__main__":
    main()
