"""Saca del ebook de un producto la portada, todas las paginas como imagenes y el texto.

Uso:
  python scripts/pdf_a_imagenes.py <producto> [--ancho 1400] [--pdf otra_ruta.pdf]

Entrada:  public/productos/<producto>/ebook.pdf
Salida (dentro de la carpeta del producto):
  portada.jpg                  primera pagina, buena resolucion
  paginas/p001.jpg ...         todas las paginas, sin recortar
  paginas/_hojas/hoja_01.jpg   12 paginas por hoja, cada una con su numero: sirve para ojear el
                               ebook entero con pocas imagenes y elegir las mejores paginas
  paginas/_resumen.json        por pagina: palabras, imagenes y que porcentaje del area cubren
                               (las paginas con mas foto son las candidatas a "foto del producto")
  ebook.txt                    texto completo con marcas '=== pagina N ==='
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola, escribir_json, producto_dir, salir  # noqa: E402


def cargar_fuente(tam: int):
    from PIL import ImageFont

    for ruta in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            return ImageFont.truetype(ruta, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def hojas_de_contacto(paginas: list[Path], destino: Path, por_hoja: int = 12, cols: int = 4, ancho_celda: int = 330) -> int:
    from PIL import Image, ImageDraw

    destino.mkdir(parents=True, exist_ok=True)
    fuente = cargar_fuente(26)
    total = 0
    for h in range(0, len(paginas), por_hoja):
        celdas = []
        for ruta in paginas[h : h + por_hoja]:
            im = Image.open(ruta).convert("RGB")
            im.thumbnail((ancho_celda, ancho_celda * 2))
            celdas.append((ruta, im))
        alto_celda = max(im.height for _, im in celdas)
        filas = (len(celdas) + cols - 1) // cols
        hoja = Image.new("RGB", (cols * (ancho_celda + 10) + 10, filas * (alto_celda + 10) + 10), (30, 30, 30))
        d = ImageDraw.Draw(hoja)
        for i, (ruta, im) in enumerate(celdas):
            x = 10 + (i % cols) * (ancho_celda + 10)
            y = 10 + (i // cols) * (alto_celda + 10)
            hoja.paste(im, (x, y))
            # El numero va abajo a la izquierda: arriba taparia el titulo de la pagina.
            yb = y + im.height - 36
            d.rectangle([x, yb, x + 70, y + im.height], fill=(0, 0, 0))
            d.text((x + 8, yb + 3), str(int(ruta.stem[1:])), fill=(255, 225, 77), font=fuente)
        hoja.save(destino / f"hoja_{h // por_hoja + 1:02d}.jpg", quality=80)
        total += 1
    return total


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("producto")
    ap.add_argument("--ancho", type=int, default=1400, help="ancho en px de cada pagina (por defecto 1400)")
    ap.add_argument("--pdf", help="ruta de otro PDF (por defecto ebook.pdf del producto)")
    a = ap.parse_args()

    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # nombre antiguo del paquete
        except ImportError:
            salir("Falta pymupdf. Corre: python -m pip install -r requirements.txt")

    carpeta = producto_dir(a.producto)
    pdf = Path(a.pdf) if a.pdf else carpeta / "ebook.pdf"
    if not pdf.is_file():
        salir(f"No encuentro {pdf}. Sube el ebook como ebook.pdf a la carpeta del producto.")

    paginas_dir = carpeta / "paginas"
    if paginas_dir.exists():
        for vieja in paginas_dir.glob("p???.jpg"):
            vieja.unlink()
        shutil.rmtree(paginas_dir / "_hojas", ignore_errors=True)
    paginas_dir.mkdir(exist_ok=True)

    doc = pymupdf.open(str(pdf))
    n = doc.page_count
    print(f"{pdf.name}: {n} paginas. Sacando imagenes y texto...", flush=True)

    textos, resumen, rutas = [], [], []
    for i, pagina in enumerate(doc, 1):
        zoom = min(4.0, a.ancho / pagina.rect.width)
        pix = pagina.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        ruta = paginas_dir / f"p{i:03d}.jpg"
        pix.save(str(ruta), jpg_quality=88)
        rutas.append(ruta)

        texto = pagina.get_text().strip()
        textos.append(f"=== pagina {i} ===\n{texto}\n")

        area_pagina = pagina.rect.width * pagina.rect.height
        area_img, n_img = 0.0, 0
        for info in pagina.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            area_img += max(0.0, x1 - x0) * max(0.0, y1 - y0)
            n_img += 1
        resumen.append(
            {
                "pagina": i,
                "palabras": len(texto.split()),
                "imagenes": n_img,
                "area_foto_pct": round(100 * min(area_img, area_pagina) / area_pagina) if area_pagina else 0,
            }
        )

    # Portada en mejor resolucion que las paginas internas.
    primera = doc[0]
    zoom = min(4.0, a.ancho * 1.3 / primera.rect.width)
    primera.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(carpeta / "portada.jpg"), jpg_quality=92)

    (carpeta / "ebook.txt").write_text("\n".join(textos), encoding="utf-8")
    escribir_json(paginas_dir / "_resumen.json", resumen)
    hojas = hojas_de_contacto(rutas, paginas_dir / "_hojas")

    palabras = sum(r["palabras"] for r in resumen)
    con_foto = [r["pagina"] for r in resumen if r["area_foto_pct"] >= 20]
    print(f"LISTO: portada.jpg, {n} paginas en paginas/, {hojas} hojas de contacto en paginas/_hojas/, ebook.txt ({palabras} palabras)")
    if con_foto:
        print(f"Paginas con foto (>=20 % del area): {', '.join(map(str, con_foto[:40]))}")
    else:
        print("Ninguna pagina tiene fotos de buen tamano: si el producto no se ve bien, sube 3 o 4 fotos reales a la carpeta del producto.")


if __name__ == "__main__":
    main()
