"""Prueba del servidor SIN gastar nada: arma un anuncio de mentira de punta a punta con el b-roll que se le indique y una voz ya grabada, y mide cuanto tarda cada parte.

No llama a APIMart, ni a Fish, ni al dash, ni a Drive: usa solo lo que corre en el propio equipo (Whisper, CLIP, el detector de texto, Remotion y ffmpeg), las mismas
piezas que usa el corredor de anuncios. Sirve para (1) comprobar que una instalacion nueva del servidor funciona, (2) medir cuanto tarda un anuncio de verdad ANTES de
prometer tiempos y (3) comprobar el efecto en el resto del servidor (se corre dentro de una unidad de systemd con los mismos limites que la del corredor).

Uso:
  python scripts/prueba_servidor.py --voz voz.mp3 --broll carpeta_con_videos [--guion texto.txt] [--sin-render] [--concurrencia 1] [--max-videos 20]
                                    [--producto _medicion] [--anuncio AD1] [--conservar]

Que hace:
  1. crea el producto de prueba public/productos/<producto>/ (los videos del b-roll con enlace duro si se puede, una portada y dos paginas de mentira, una ficha)
  2. pone la voz como public/productos/<producto>/voces/<anuncio>.<extension>
  3. corre montaje_auto.py (Whisper local, analisis del b-roll con CLIP y el detector de texto, plan, render con Remotion y las 10 compuertas de calidad)
  4. imprime lo que tardo cada parte (transcribir, analizar el b-roll, emparejar, plan, render, control), la memoria maxima y las propiedades del mp4
  5. borra lo que creo (salvo --conservar). Si el producto o el anuncio ya existian, NO los toca y se detiene.
Sale con 0 si el anuncio paso las 10 compuertas, 2 si el video quedo pero alguna fallo, 1 si algo no se pudo hacer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, PUBLIC, RAIZ, configurar_consola, ejecutable, leer_json, salir  # noqa: E402

PRODUCTO = "_medicion"
EXT_VOZ = {".mp3", ".wav", ".m4a", ".ogg"}


def crear_portada_y_paginas(carpeta: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    (carpeta / "paginas").mkdir(parents=True, exist_ok=True)
    fuente = ImageFont.load_default()
    for nombre, color, texto in (("portada.jpg", (190, 60, 80), "PORTADA DE PRUEBA"), ("paginas/p001.jpg", (245, 245, 235), "Pagina 1 de prueba"), ("paginas/p002.jpg", (235, 245, 245), "Pagina 2 de prueba")):
        im = Image.new("RGB", (1080, 1528), color)
        d = ImageDraw.Draw(im)
        d.rectangle([60, 60, 1020, 1468], outline=(30, 30, 30), width=6)
        d.text((120, 200), texto, fill=(20, 20, 20), font=fuente)
        im.save(carpeta / nombre, quality=88)


def enlazar_o_copiar(origen: Path, destino: Path) -> str:
    try:
        os.link(origen, destino)
        return "enlace"
    except OSError:
        shutil.copy2(origen, destino)
        return "copia"


def preparar(producto: str, anuncio: str, voz: Path, broll: Path, guion: Path | None, max_videos: int) -> tuple[Path, Path]:
    carpeta = PUBLIC / "productos" / producto
    base = ANUNCIOS / producto / "tiktok" / anuncio
    for ruta in (carpeta, ANUNCIOS / producto):
        if ruta.exists():
            salir(f"Ya existe {ruta}: no la toco (es de otra prueba o de un producto de verdad). Borrala a mano o usa --producto con otro nombre.")
    videos = sorted(p for p in broll.glob("*.mp4") if p.is_file())[:max_videos]
    if not videos:
        salir(f"No hay videos .mp4 en {broll}")
    (carpeta / "broll-tiktok").mkdir(parents=True)
    modos = {"enlace": 0, "copia": 0}
    for v in videos:
        modos[enlazar_o_copiar(v, carpeta / "broll-tiktok" / v.name)] += 1
    (carpeta / "voces").mkdir()
    shutil.copy2(voz, carpeta / "voces" / f"{anuncio}{voz.suffix.lower()}")
    if guion:
        (carpeta / "guiones").mkdir()
        shutil.copy2(guion, carpeta / "guiones" / f"{anuncio}.txt")
    crear_portada_y_paginas(carpeta)
    (carpeta / "producto.json").write_text(json.dumps({"nombre": "Producto de prueba", "que_es": "un ebook de manualidades con fieltro", "promesa_principal": "figuras de fieltro listas para cortar y coser"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    base.mkdir(parents=True)
    print(f"Producto de prueba listo: {len(videos)} video(s) de b-roll ({modos['enlace']} con enlace duro, {modos['copia']} copiados), voz {voz.name}", flush=True)
    return carpeta, base


def correr_montaje(producto: str, anuncio: str, concurrencia: int, sin_render: bool) -> tuple[int, float]:
    cmd = [sys.executable, "-u", str(RAIZ / "scripts" / "montaje_auto.py"), producto, anuncio, "--concurrencia", str(concurrencia), "--sin-historial"] + (["--sin-render"] if sin_render else [])
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=RAIZ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for linea in proc.stdout:  # type: ignore[union-attr]
        print(f"  [+{int(time.time() - t0) // 60:02d}:{int(time.time() - t0) % 60:02d}] {linea.rstrip()[:240]}", flush=True)
    proc.wait()
    return proc.returncode, time.time() - t0


def propiedades_mp4(ruta: Path) -> dict:
    r = subprocess.run([ejecutable("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"error": (r.stderr or "").strip()[:200]}
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), {})
    return {"tamano": f"{v.get('width')}x{v.get('height')}", "fps": v.get("r_frame_rate"), "codec": v.get("codec_name"), "audio": a.get("codec_name"),
            "segundos": round(float(j["format"].get("duration") or 0), 2), "mb": round(int(j["format"].get("size") or 0) / 1048576, 1)}


def memoria_maxima_mb() -> float:
    """Lo mas que ocupo en memoria el proceso hijo mas grande (Linux); 0 donde no se puede medir."""
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    except (ImportError, OSError):
        return 0.0


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voz", required=True, help="una voz grabada (mp3, wav, m4a) de unos 25 a 60 segundos, en espanol")
    ap.add_argument("--broll", required=True, help="una carpeta con videos .mp4 verticales (el b-roll)")
    ap.add_argument("--guion", help="el texto de la voz (corrige los nombres que Whisper oye mal y activa la compuerta de subtitulos iguales al guion)")
    ap.add_argument("--producto", default=PRODUCTO)
    ap.add_argument("--anuncio", default="AD1")
    ap.add_argument("--concurrencia", type=int, default=1, help="procesos de Remotion (por defecto 1: el corredor usa 1)")
    ap.add_argument("--max-videos", type=int, default=20)
    ap.add_argument("--sin-render", action="store_true", help="arma el plan pero no renderiza (para probar rapido)")
    ap.add_argument("--conservar", action="store_true", help="no borrar lo creado al terminar (para mirar el video y la evidencia)")
    a = ap.parse_args()

    voz, broll = Path(a.voz), Path(a.broll)
    if not voz.is_file() or voz.suffix.lower() not in EXT_VOZ:
        salir(f"--voz debe ser un archivo de audio ({', '.join(sorted(EXT_VOZ))}): {voz}")
    if not broll.is_dir():
        salir(f"--broll debe ser una carpeta con videos: {broll}")
    if not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,40}", a.producto) or not re.fullmatch(r"AD([1-9]|1[0-2])|1(0[1-9]|[12][0-9]|30)", a.anuncio):
        salir("Producto o anuncio no validos.")
    guion = Path(a.guion) if a.guion else None
    if guion and not guion.is_file():
        salir(f"No existe el guion: {guion}")

    print(f"Prueba del servidor ({'sin render' if a.sin_render else 'completa'}, {a.concurrencia} proceso(s) de Remotion). Python: {sys.executable}  CPU: {os.cpu_count()}", flush=True)
    t_ini = time.time()
    carpeta, base = preparar(a.producto, a.anuncio, voz, broll, guion, a.max_videos)
    codigo = 1
    try:
        rc, seg = correr_montaje(a.producto, a.anuncio, a.concurrencia, a.sin_render)
        informe_ruta = base / "informe_calidad.json"
        informe = leer_json(informe_ruta) if informe_ruta.is_file() else {}
        print("\n=== RESULTADO ===", flush=True)
        print(f"montaje_auto.py salio con codigo {rc} en {seg / 60:.1f} min; estado: {informe.get('estado', '?')}")
        t = informe.get("tiempos") or {}
        nombres = {"voz_y_audio": "transcribir la voz y mezclar el audio", "broll": "analizar el b-roll (CLIP + detector de texto)", "emparejar": "emparejar frases con tomas", "evidencia": "plan y hojas de revision",
                   "render": "render con Remotion", "control": "control de calidad", "total": "TOTAL del montaje"}
        for k, etiqueta in nombres.items():
            if k in t:
                print(f"  {etiqueta:<46} {float(t[k]):8.1f} s  ({float(t[k]) / 60:5.1f} min)")
        print(f"  duracion del anuncio: {informe.get('duracion_s', '?')} s   tomas: {informe.get('tomas', '?')}   textos grandes: {informe.get('textos_grandes', '?')}")
        print(f"  memoria maxima del proceso: {informe.get('ram_pico_mb', '?')} MB (el montaje)  /  {memoria_maxima_mb():.0f} MB (el proceso hijo mas grande, incluido Remotion/Chrome)")
        comp = informe.get("compuertas") or {}
        fallan = [k for k, v in comp.items() if v.get("ok") is False]
        sin = [k for k, v in comp.items() if v.get("ok") is None]
        print(f"  compuertas: {sum(1 for v in comp.values() if v.get('ok'))} pasan, {len(fallan)} fallan{(': ' + ', '.join(fallan)) if fallan else ''}, {len(sin)} sin verificar{(': ' + ', '.join(sin)) if sin else ''}")
        if informe.get("final"):
            print(f"  video: {informe['final']}  ->  {propiedades_mp4(Path(informe['final']))}")
        print(f"  tiempo total de la prueba (con preparar): {(time.time() - t_ini) / 60:.1f} min")
        codigo = 0 if rc == 0 else (2 if rc == 2 else 1)
    finally:
        if a.conservar:
            print(f"\nConservado: {carpeta} y {ANUNCIOS / a.producto} (borralos a mano cuando termines).")
        else:
            shutil.rmtree(carpeta, ignore_errors=True)
            shutil.rmtree(ANUNCIOS / a.producto, ignore_errors=True)
            print("\nBorrado lo creado por la prueba.")
    sys.exit(codigo)


if __name__ == "__main__":
    main()
