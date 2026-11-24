"""Detector de TEXTO QUEMADO en un video, para saber cuanto trae "limpio" y sirve de b-roll.

Un video de TikTok casi siempre trae subtitulos, titulos o logos puestos por su creador; encima de un anuncio nuestro eso estorba. Aqui se sacan
unos cuadros del video (uno cada pocos segundos), el detector de texto de RapidOCR (modelo PP-OCR sobre onnxruntime: gratis, corre en el servidor, no
usa internet ni Claude) marca las cajas de texto de cada cuadro, y se saca:
  - texto_pct       % de los cuadros que traen texto
  - tramos_limpios  los tramos de tiempo SIN texto, de al menos 3 segundos (por ejemplo "0-16;44-64")
  - limpio_seg      los segundos que suman esos tramos
  - calidad         limpio (casi todo el video sin texto) / medio (tiene tramos limpios pero tambien texto) / malo (menos de min_limpio_seg limpios)

Por que el detector y no un OCR que lea las letras (se probo con tesseract): los subtitulos de TikTok usan letras de adorno (contorno, sombra, cursivas,
amarillo sobre fotos) que un OCR casi no lee (solo encontraba el 19% de los cuadros con texto); un detector de cajas las encuentra casi todas.
Se calibro con 12 videos de Dinamicas que Claude habia revisado a mano (328 cuadros): encuentra el 89% de los cuadros con texto y el 87% de lo que marca es
texto de verdad. Una caja solo cuenta si es ancha (proporcion ancho/alto de 2 o mas: una linea de letras, no una mancha), de al menos 20% del ancho del
cuadro y 1,5% de su alto: asi no cuentan los adornos redondos ni las letras sueltas del fondo. Limites: NO distingue un subtitulo puesto encima de un cartel
que sale en la escena (los dos cuentan como texto), y NO ve rostros, ninos ni logos sin letras: eso lo sigue juzgando una persona mirando la hoja de contacto.

Uso:  python scripts/texto_quemado.py video.mp4 [--calibrar]     (--calibrar imprime los datos de cada cuadro, para ajustar los umbrales)
Necesita ffmpeg y el paquete rapidocr-onnxruntime (esta en vps/requirements-vps.txt; en el PC del editor no se instala: alla no hace falta).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola  # noqa: E402

PARAMETROS = {
    "max_muestras": 30,      # cuadros por video como maximo (un video largo se mira cada duracion/30 segundos)
    "intervalo_min": 1.0,    # nunca mas de un cuadro por segundo
    "ancho": 720,            # los cuadros se reducen a este ancho antes de analizarlos
    "alto_min": 0.015,       # alto minimo de una caja de texto respecto al alto del cuadro
    "ancho_min": 0.2,        # ancho minimo de una caja de texto respecto al ancho del cuadro
    "razon_min": 2.0,        # proporcion ancho/alto minima de la caja: una linea de letras es ancha, una mancha o un adorno redondo no
    "cajas_min": 1,          # cajas validas para dar el cuadro por "con texto"
    "tramo_min_seg": 3.0,    # un tramo limpio mas corto que esto no sirve de toma
    "min_limpio_seg": 5.0,   # menos segundos limpios que esto = malo
    "limpio_frac": 0.8,      # tantos segundos limpios sobre la duracion = limpio
}
_MOTOR = None


class ErrorTexto(Exception):
    """No se pudo analizar este video (no se abre, o el detector fallo con el)."""


class FaltaDetector(ErrorTexto):
    """Falta ffmpeg o el paquete del detector en este equipo: no sirve intentar con otro video."""


def disponible() -> bool:
    return bool(importlib.util.find_spec("rapidocr_onnxruntime") and shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _motor_rapidocr():
    """El detector de RapidOCR (se carga una sola vez por programa: tarda ~1 s). Devuelve una funcion ruta_de_imagen -> lista de cajas."""
    global _MOTOR
    if _MOTOR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            raise FaltaDetector("falta el paquete rapidocr-onnxruntime en este equipo") from None
        motor = RapidOCR()

        def cajas(ruta: str) -> list:
            res, _ = motor(ruta, use_det=True, use_cls=False, use_rec=False)
            return [[(float(x), float(y)) for x, y in caja] for caja in (res or [])]
        _MOTOR = cajas
    return _MOTOR


def caja_valida(caja, ancho_cuadro: float, alto_cuadro: float, p: dict) -> bool:
    """Una caja (4 puntos) cuenta como linea de texto si es ancha, grande y con forma de renglon."""
    xs, ys = [pt[0] for pt in caja], [pt[1] for pt in caja]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if h <= 0 or w <= 0:
        return False
    return h >= p["alto_min"] * alto_cuadro and w >= p["ancho_min"] * ancho_cuadro and w / h >= p["razon_min"]


def hay_texto(cajas: list, ancho_cuadro: float, alto_cuadro: float, p: dict) -> bool:
    return sum(1 for c in cajas if caja_valida(c, ancho_cuadro, alto_cuadro, p)) >= p["cajas_min"]


def resumir(con_texto: list[bool], intervalo: float, duracion: float, p: dict | None = None) -> dict:
    """De 'este cuadro tiene texto / no' a los tramos limpios y la calidad. Funcion pura (se prueba sin video)."""
    p = {**PARAMETROS, **(p or {})}
    n = len(con_texto)
    if n == 0 or duracion <= 0:
        return {"muestras": 0, "intervalo": intervalo, "texto_pct": 0, "limpio_seg": 0.0, "tramos_limpios": [], "calidad": "malo"}
    tramos, i = [], 0
    while i < n:
        if con_texto[i]:
            i += 1
            continue
        j = i
        while j < n and not con_texto[j]:
            j += 1
        desde, hasta = i * intervalo, min(j * intervalo, duracion)
        if hasta - desde >= p["tramo_min_seg"] - 1e-9:
            tramos.append([round(desde, 1), round(hasta, 1)])
        i = j
    limpio = round(sum(b - a for a, b in tramos), 1)
    if limpio >= p["limpio_frac"] * duracion:
        calidad = "limpio"
    elif limpio < p["min_limpio_seg"]:
        calidad = "malo"
    else:
        calidad = "medio"
    return {"muestras": n, "intervalo": round(intervalo, 2), "texto_pct": round(100 * sum(con_texto) / n), "limpio_seg": limpio,
            "tramos_limpios": tramos, "calidad": calidad}


def tramos_a_texto(tramos: list[list[float]]) -> str:
    """[[0, 16], [44.5, 64]] -> '0-16;44.5-64' (asi va en fuentes.csv)."""
    return ";".join(f"{a:g}-{b:g}" for a, b in tramos)


def _duracion(ruta: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(ruta)],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise ErrorTexto(f"ffprobe no pudo leer la duracion de {ruta.name}") from None


def _tamano_imagen(ruta: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(ruta) as im:
        return im.size


def analizar(ruta: Path | str, duracion: float | None = None, parametros: dict | None = None, detalle: list | None = None, motor=None) -> dict:
    """Mira el video y devuelve lo de resumir(). Si pasas una lista en `detalle`, le agrega los datos de cada cuadro.
    `motor` (opcional, para pruebas) es una funcion ruta_de_imagen -> lista de cajas; por defecto el detector de RapidOCR."""
    p = {**PARAMETROS, **(parametros or {})}
    ruta = Path(ruta)
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise FaltaDetector("faltan ffmpeg o ffprobe en este equipo")
    motor = motor or _motor_rapidocr()
    duracion = duracion or _duracion(ruta)
    intervalo = max(p["intervalo_min"], duracion / p["max_muestras"])
    con_texto: list[bool] = []
    with tempfile.TemporaryDirectory(prefix="texto_") as t:
        cuadros = Path(t)
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(ruta), "-vf", f"fps=1/{intervalo:.3f},scale={p['ancho']}:-2",
                            "-q:v", "4", "-frames:v", str(p["max_muestras"] + 2), str(cuadros / "c%04d.jpg")],
                           capture_output=True, text=True, timeout=300)
        imagenes = sorted(cuadros.glob("c*.jpg"))
        if r.returncode != 0 or not imagenes:
            raise ErrorTexto(f"ffmpeg no pudo sacar cuadros de {ruta.name}: {(r.stderr or '').strip()[:120]}")
        for img in imagenes:
            try:
                ancho, alto = _tamano_imagen(img)
                cajas = motor(str(img))
            except ErrorTexto:
                raise
            except Exception as e:  # noqa: BLE001 - un cuadro raro no debe tumbar la corrida: se cuenta como error de este video
                raise ErrorTexto(f"el detector fallo en un cuadro de {ruta.name}: {e.__class__.__name__}: {str(e)[:100]}") from None
            hay = hay_texto(cajas, ancho, alto, p)
            con_texto.append(hay)
            if detalle is not None:
                detalle.append({"cuadro": len(con_texto) - 1, "t": round((len(con_texto) - 1) * intervalo, 1), "con_texto": hay, "cajas": len(cajas),
                                "validas": sum(1 for c in cajas if caja_valida(c, ancho, alto, p))})
    return resumir(con_texto, intervalo, duracion, p)


def main() -> None:
    configurar_consola()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    detalle: list | None = [] if "--calibrar" in sys.argv else None
    try:
        res = analizar(args[0], detalle=detalle)
    except ErrorTexto as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if detalle is not None:
        for d in detalle:
            print(f"  t={d['t']:6.1f}s  {'TEXTO' if d['con_texto'] else '  -  '}  cajas {d['cajas']} (validas {d['validas']})")
    print(json.dumps({**res, "tramos_limpios": tramos_a_texto(res["tramos_limpios"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
