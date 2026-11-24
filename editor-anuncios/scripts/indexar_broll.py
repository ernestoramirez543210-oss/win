"""Prepara el indice de los videos de stock (b-roll) de un producto.

Uso:
  python scripts/indexar_broll.py <producto> [--cada 2] [--todo]

Lee public/productos/<producto>/broll-tiktok/. Por cada video NUEVO o CAMBIADO:
  1. anota sus datos tecnicos (duracion, tamano, si trae audio, vertical u horizontal),
  2. saca un fotograma cada N segundos (maximo 24 por video) y arma una hoja de contacto
     con la hora de cada fotograma,
  3. lo deja en indice.json con revisado=false y tramos=[].
Despues Claude mira cada hoja una sola vez y llena `tramos` (que se ve entre que segundos).
Los videos ya revisados no se tocan: se revisa una sola vez por video. --todo fuerza revisar todos.

Salida:
  broll-tiktok/indice.json         una entrada por video; los `tramos` los escribe Claude:
        {"desde": 0.0, "hasta": 3.5, "que_se_ve": "manos vertiendo cera", "etiquetas": ["manos","proceso"], "calidad": "buena"}
  broll-tiktok/_hojas/<video>.jpg  hojas de contacto (carpeta de trabajo; se puede borrar)
"""
from __future__ import annotations

import argparse
import math
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import (  # noqa: E402
    EXT_VIDEO,
    configurar_consola,
    correr,
    ejecutable,
    escribir_json,
    formato_tiempo,
    leer_json,
    producto_dir,
    salir,
    sonda,
)

MAX_FOTOGRAMAS = 24


def datos_tecnicos(ruta: Path) -> dict:
    info = sonda(ruta)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        salir(f"{ruta.name} no tiene pista de video")
    audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    ancho, alto = int(video.get("width", 0)), int(video.get("height", 0))
    fps_txt = video.get("avg_frame_rate", "0/1")
    try:
        num, den = fps_txt.split("/")
        fps = round(float(num) / float(den), 2) if float(den) else 0
    except ValueError:
        fps = 0
    return {
        "duracion": round(float(info["format"].get("duration") or video.get("duration") or 0), 2),
        "ancho": ancho,
        "alto": alto,
        "fps": fps,
        "orientacion": "vertical" if alto > ancho else ("horizontal" if ancho > alto else "cuadrado"),
        "tiene_audio": audio,
    }


def hoja_de_contacto(ruta: Path, destino: Path, cada: float) -> tuple[int, float]:
    from PIL import Image, ImageDraw, ImageFont

    duracion = float(sonda(ruta)["format"].get("duration") or 0)
    cada = max(cada, math.ceil(duracion / MAX_FOTOGRAMAS * 10) / 10) if duracion else cada
    with tempfile.TemporaryDirectory(prefix="broll_") as tmp:
        r = correr(
            [
                ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(ruta),
                "-vf", f"fps=1/{cada},scale=360:-2", "-q:v", "4", f"{tmp}/f_%03d.jpg",
            ]
        )
        if r.returncode != 0:
            salir(f"ffmpeg no pudo sacar fotogramas de {ruta.name}: {r.stderr.strip()[:300]}")
        fotos = sorted(Path(tmp).glob("f_*.jpg"))
        if not fotos:  # video mas corto que el intervalo
            r = correr([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(ruta), "-frames:v", "1", "-vf", "scale=360:-2", f"{tmp}/f_001.jpg"])
            fotos = sorted(Path(tmp).glob("f_*.jpg"))
        imgs = [Image.open(f).convert("RGB") for f in fotos]
        ancho_celda, alto_celda = max(i.width for i in imgs), max(i.height for i in imgs)
        cols = 4
        filas = (len(imgs) + cols - 1) // cols
        hoja = Image.new("RGB", (cols * (ancho_celda + 8) + 8, filas * (alto_celda + 8) + 8), (25, 25, 25))
        d = ImageDraw.Draw(hoja)
        try:
            fuente = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 24)
        except OSError:
            fuente = ImageFont.load_default()
        for i, im in enumerate(imgs):
            x, y = 8 + (i % cols) * (ancho_celda + 8), 8 + (i // cols) * (alto_celda + 8)
            hoja.paste(im, (x, y))
            d.rectangle([x, y, x + 78, y + 32], fill=(0, 0, 0))
            d.text((x + 6, y + 3), formato_tiempo(i * cada), fill=(255, 225, 77), font=fuente)
        destino.parent.mkdir(parents=True, exist_ok=True)
        hoja.save(destino, quality=80)
    return len(imgs), cada


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("producto")
    ap.add_argument("--cada", type=float, default=2.0, help="segundos entre fotogramas (por defecto 2)")
    ap.add_argument("--todo", action="store_true", help="volver a indexar tambien los ya revisados")
    a = ap.parse_args()

    carpeta = producto_dir(a.producto) / "broll-tiktok"
    if not carpeta.is_dir():
        salir(f"No existe {carpeta}")
    videos = sorted(p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in EXT_VIDEO)
    if not videos:
        salir(f"No hay videos en {carpeta}. Sube ahi los videos de TikTok o de stock del producto.")

    ruta_indice = carpeta / "indice.json"
    indice = leer_json(ruta_indice) if ruta_indice.is_file() else {}
    indice.setdefault("producto", a.producto)
    entradas = indice.setdefault("videos", {})

    nuevos, saltados = [], 0
    for v in videos:
        st = v.stat()
        huella = {"tam": st.st_size, "mtime": int(st.st_mtime)}
        previo = entradas.get(v.name)
        if previo and previo.get("tam") == huella["tam"] and previo.get("mtime") == huella["mtime"] and not a.todo:
            saltados += 1
            continue
        print(f"Revisando {v.name}...", flush=True)
        tecnicos = datos_tecnicos(v)
        hoja = carpeta / "_hojas" / f"{v.stem}.jpg"
        n_fotos, cada = hoja_de_contacto(v, hoja, a.cada)
        entradas[v.name] = {
            **huella,
            **tecnicos,
            "hoja": f"_hojas/{v.stem}.jpg",
            "fotogramas_cada": cada,
            "revisado": False,
            "tramos": previo.get("tramos", []) if previo and not a.todo else [],
            **({"marcas": previo["marcas"]} if previo and previo.get("marcas") else {}),        # lo que una persona marco (un nino, un logo) no se pierde al reindexar
        }
        nuevos.append((v.name, n_fotos, cada))

    # Si borraron un video, sale del indice.
    existentes = {v.name for v in videos}
    for nombre in [n for n in entradas if n not in existentes]:
        del entradas[nombre]
        (carpeta / "_hojas" / f"{Path(nombre).stem}.jpg").unlink(missing_ok=True)

    indice["actualizado"] = datetime.now().isoformat(timespec="seconds")
    escribir_json(ruta_indice, indice)

    pendientes = [n for n, e in entradas.items() if not e.get("revisado")]
    print(f"{len(videos)} videos en total: {len(nuevos)} nuevos, {saltados} ya indexados.")
    for nombre, n_fotos, cada in nuevos:
        e = entradas[nombre]
        print(f"  NUEVO {nombre}: {e['duracion']} s, {e['ancho']}x{e['alto']} ({e['orientacion']}), audio={'si' if e['tiene_audio'] else 'no'}, {n_fotos} fotogramas cada {cada} s -> {e['hoja']}")
    if pendientes:
        print(f"Falta describir los tramos de {len(pendientes)} video(s): {', '.join(pendientes)}")
        print("Claude: mira cada hoja de contacto una sola vez y llena `tramos` y revisado=true en indice.json.")
    else:
        print("Todos los videos estan revisados.")


if __name__ == "__main__":
    main()
