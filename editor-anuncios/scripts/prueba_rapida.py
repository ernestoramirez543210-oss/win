"""Prueba de punta a punta del editor con material SINTETICO. No gasta nada de APIMart.

Uso:
  python scripts/prueba_rapida.py [--sin-render] [--conservar] [--modelo small]

Crea un producto de mentira (_prueba) con una voz de Windows, tres videos de prueba, una musica y un
ebook; corre cada paso del flujo (PDF a imagenes, indexar b-roll, transcribir, mezclar audio, validar,
fotogramas, render) y comprueba el video final: 1080x1920, 30 fps, h264 + aac, duracion y volumen.
Al terminar borra todo lo que creo (--conservar lo deja para mirarlo).

Sirve para comprobar una instalacion nueva (por ejemplo en el PC del editor) y despues de tocar el
codigo. --sin-render salta los fotogramas y el render: tarda ~7 minutos con el PC ocupado (la autoprueba del anuncio automatico suma ~4) en vez de ~12.
Termina con codigo 1 si algo falla.
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
from _comun import ANUNCIOS, PUBLIC, RAIZ, configurar_consola, ejecutable, escribir_json, leer_json  # noqa: E402

PROD = "_prueba"
AD = "AD1"
TEXTO = "Descubre las mejores recetas de velas aromáticas caseras. Aprende paso a paso y empieza hoy mismo."
TEXTO_EN = "Discover the best homemade scented candle recipes. Learn step by step and start today."
FALLAS: list[str] = []
OMITIDOS: list[str] = []


def correr(args: list[str]) -> str:
    r = subprocess.run(args, cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"termino con codigo {r.returncode}: {(r.stderr or r.stdout).strip()[-500:]}")
    return r.stdout


def py(*args: str) -> str:
    return correr([sys.executable, *args])


def ff(*args: str) -> None:
    correr([ejecutable("ffmpeg"), "-y", "-loglevel", "error", *args])


def paso(nombre: str, fn) -> bool:
    t0 = time.time()
    try:
        detalle = fn() or ""
        print(f"  PASA    {nombre} ({time.time() - t0:.0f} s) {detalle}".rstrip(), flush=True)
        return True
    except Exception as e:  # noqa: BLE001 - queremos seguir con el resto y resumir al final
        FALLAS.append(nombre)
        print(f"  FALLA   {nombre}: {e}", flush=True)
        return False


def ffprobe_json(ruta: Path) -> dict:
    import json

    return json.loads(correr([ejecutable("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)]))


def lufs(ruta: Path) -> float:
    r = subprocess.run([ejecutable("ffmpeg"), "-hide_banner", "-nostats", "-i", str(ruta), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.findall(r"^\s+I:\s+(-?\d+(?:\.\d+)?) LUFS", r.stderr, re.M)
    if not m:
        raise RuntimeError("no pude medir el volumen")
    return float(m[-1])


# ------------------------------------------------------------------ material de prueba
IDIOMA = {"codigo": "es"}


def crear_voz() -> str:
    """Voz de Windows (SAPI). Si el PC no tiene ninguna voz en espanol, usa la que haya y un texto en
    ingles: la prueba solo necesita audio hablado que Whisper pueda transcribir."""
    carpeta = PUBLIC / "productos" / PROD / "voces"
    carpeta.mkdir(parents=True, exist_ok=True)
    wav = carpeta / f"{AD}.wav"
    consulta = ("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
                "ForEach-Object { $_.VoiceInfo.Culture.Name }")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", consulta], capture_output=True, text=True)
    culturas = r.stdout.split()
    texto = TEXTO
    if not any(c.lower().startswith("es") for c in culturas):
        texto = TEXTO_EN
        IDIOMA["codigo"] = "en"
    script = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like $env:PRUEBA_CULTURA } | Select-Object -First 1; "
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; $s.SetOutputToWaveFile($env:PRUEBA_WAV); $s.Speak($env:PRUEBA_TEXTO); $s.Dispose()"
    )
    entorno = {**os.environ, "PRUEBA_WAV": str(wav), "PRUEBA_TEXTO": texto, "PRUEBA_CULTURA": IDIOMA["codigo"] + "*"}
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], env=entorno, capture_output=True, text=True)
    if r.returncode != 0 or not wav.is_file():
        raise RuntimeError("Windows no pudo generar la voz de prueba: " + (r.stderr or "")[:200])
    ff("-i", str(wav), "-c:a", "libmp3lame", "-q:a", "3", str(carpeta / f"{AD}.mp3"))
    wav.unlink()
    return ""


def crear_material() -> str:
    base = PUBLIC / "productos" / PROD
    broll = base / "broll-tiktok"
    broll.mkdir(parents=True, exist_ok=True)
    for nombre, fuente, w, h in (("a.mp4", "testsrc2", 720, 1280), ("b.mp4", "testsrc", 1280, 720), ("c.mp4", "rgbtestsrc", 720, 1280)):
        ff("-f", "lavfi", "-i", f"{fuente}=size={w}x{h}:rate=30:duration=6", "-f", "lavfi", "-i", "sine=f=440:duration=6",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(broll / nombre))
    musica = PUBLIC / "musica" / "biblioteca" / PROD
    musica.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", "sine=f=220:d=20", "-f", "lavfi", "-i", "sine=f=277:d=20", "-f", "lavfi", "-i", "sine=f=330:d=20",
       "-filter_complex", "amix=inputs=3:normalize=0,volume=0.5", "-c:a", "libmp3lame", "-q:a", "4", str(musica / "loop.mp3"))

    import pymupdf
    from PIL import Image, ImageDraw

    tmp = RAIZ / ".cache" / "prueba"
    tmp.mkdir(parents=True, exist_ok=True)
    foto = tmp / "foto.jpg"
    im = Image.new("RGB", (900, 600), (205, 120, 60))
    d = ImageDraw.Draw(im)
    for i in range(0, 900, 60):
        d.ellipse([i, 200, i + 90, 290], fill=(250, 210, 120))
    im.save(foto)
    doc = pymupdf.open()
    for n in range(1, 6):
        p = doc.new_page(width=595, height=842)
        p.insert_text((50, 80), f"Capitulo {n}: Velas aromaticas", fontsize=26)
        p.insert_textbox(pymupdf.Rect(50, 110, 545, 300), "Receta paso a paso con cera de soja y esencias naturales. " * 4, fontsize=12)
        if n in (2, 4):
            p.insert_image(pymupdf.Rect(50, 320, 545, 650), filename=str(foto))
    doc.save(str(base / "ebook.pdf"))
    return ""


# ------------------------------------------------------------------ pasos del flujo
def paso_sfx() -> str:
    faltan = [n for n in ("hit", "pop", "click") if not (PUBLIC / "sfx" / f"{n}.mp3").is_file()]
    if faltan:
        raise RuntimeError(f"faltan efectos ({', '.join(faltan)}): corre python scripts/crear_sfx.py")
    return ""


def paso_material() -> str:
    crear_voz()
    crear_material()
    return ""


def paso_pdf() -> str:
    py("scripts/pdf_a_imagenes.py", PROD)
    base = PUBLIC / "productos" / PROD
    for f in ("portada.jpg", "paginas/p001.jpg", "paginas/p005.jpg", "ebook.txt", "paginas/_hojas/hoja_01.jpg"):
        if not (base / f).is_file():
            raise RuntimeError(f"falta {f}")
    return "portada, 5 paginas, texto y hoja de contacto"


def paso_broll() -> str:
    py("scripts/indexar_broll.py", PROD)
    idx = leer_json(PUBLIC / "productos" / PROD / "broll-tiktok" / "indice.json")
    if len(idx["videos"]) != 3:
        raise RuntimeError(f"el indice tiene {len(idx['videos'])} videos y deberia tener 3")
    py("scripts/indexar_broll.py", PROD)  # segunda vez: no debe repetir lo ya indexado
    return "3 videos indexados"


def paso_referencia_meta() -> str:
    salida = py("scripts/autoprueba_referencia.py")
    if "AUTOPRUEBA OK" not in salida:
        raise RuntimeError("la autoprueba de referencia_meta.py no termino con AUTOPRUEBA OK")
    return "anuncio de referencia de la Biblioteca de Meta: copy, datos y video (sin internet)"


def paso_gancho_ia() -> str:
    salida = py("scripts/autoprueba_gancho.py")
    if "AUTOPRUEBA OK" not in salida:
        raise RuntimeError("la autoprueba de gancho_ia.py no termino con AUTOPRUEBA OK")
    return "gancho nuevo con IA: analisis, tope de gasto, imagenes y clips con un APIMart de mentira (sin internet)"


def paso_montaje_auto() -> str:
    # emparejar_tomas.py + montaje_auto.py (anuncio automatico, sin tokens) con un motor de CLIP y un detector de texto de mentira, sin internet; y, si los modelos
    # reales de CLIP estan instalados, que reconozcan colores en espanol, ingles y portugues.
    salida = py("scripts/autoprueba_montaje.py")
    if "AUTOPRUEBA OK" not in salida:
        raise RuntimeError("la autoprueba de emparejar_tomas.py y montaje_auto.py no termino con AUTOPRUEBA OK")
    return "emparejar tomas y montaje automatico (sin internet, sin render)"


def paso_guion_voz() -> str:
    # guion_auto.py + voz_auto.py (anuncio automatico, sin tokens) con un APIMart y un dash de mentira, sin internet; incluye el contrato con montaje_auto.py
    salida = py("scripts/autoprueba_guion_voz.py")
    if "AUTOPRUEBA OK" not in salida:
        raise RuntimeError("la autoprueba de guion_auto.py y voz_auto.py no termino con AUTOPRUEBA OK")
    return "guion y voz automaticos (APIMart y dash de mentira, sin internet)"


def paso_musica() -> str:
    salida = py("scripts/autoprueba_musica.py")
    if "AUTOPRUEBA OK" not in salida:
        raise RuntimeError("la autoprueba de crear_musica.py no termino con AUTOPRUEBA OK")
    return "musica de fondo propia: teoria, sonido de cada estilo, linea de comandos y selector del montaje (sin internet)"


def paso_broll_tiktok() -> str:
    # broll_tiktok.py (PC) y, con un Drive y un proveedor de mentira, lo que corre en el servidor: drive_api.py y broll_vps.py
    # y la Fabrica de ofertas en el servidor: frases de busqueda, "sirve el original", transcripcion y la tuberia completa con dash, Meta, Drive y TikTok de mentira
    for script, que in (("autoprueba_broll.py", "broll_tiktok.py"), ("autoprueba_drive.py", "drive_api.py"), ("autoprueba_broll_vps.py", "broll_vps.py"),
                        ("autoprueba_consultas.py", "consultas_desde_texto.py, original_reutilizable.py y transcribir_vps.py"), ("autoprueba_fabrica_broll.py", "fabrica_broll.py")):
        salida = py(f"scripts/{script}")
        if "AUTOPRUEBA OK" not in salida:
            raise RuntimeError(f"la autoprueba de {que} no termino con AUTOPRUEBA OK")
    return "busqueda, descarga y copia a Drive, la corrida del servidor con Drive de mentira y la Fabrica de ofertas de punta a punta (sin internet)"


def paso_transcribir(modelo: str, trabajo: Path) -> str:
    voz = PUBLIC / "productos" / PROD / "voces" / f"{AD}.mp3"
    py("scripts/transcribir.py", str(voz), "--salida", str(trabajo / "transcripcion.json"), "--modelo", modelo, "--idioma", IDIOMA["codigo"])
    t = leer_json(trabajo / "transcripcion.json")
    if len(t["palabras"]) < 8:
        raise RuntimeError(f"solo {len(t['palabras'])} palabras reconocidas: {t['texto']!r}")
    return f"{len(t['palabras'])} palabras"


def paso_mezclar(trabajo: Path) -> str:
    voz = PUBLIC / "productos" / PROD / "voces" / f"{AD}.mp3"
    musica = PUBLIC / "musica" / "biblioteca" / PROD / "loop.mp3"
    py("scripts/mezclar_audio.py", "--voz", str(voz), "--musica", str(musica), "--salida", str(trabajo / "audio"))
    m = leer_json(trabajo / "audio" / "mezcla.json")
    if abs(m["voz"]["lufs_despues"] + 14) > 0.7:
        raise RuntimeError(f"la voz quedo en {m['voz']['lufs_despues']} LUFS y debia ser -14")
    if not 14 <= m["musica"]["db_bajo_la_voz"] <= 16:
        raise RuntimeError(f"la musica quedo {m['musica']['db_bajo_la_voz']} dB bajo la voz y debian ser ~15")
    return f"voz {m['voz']['lufs_despues']} LUFS, musica {m['musica']['db_bajo_la_voz']} dB mas baja"


def armar_videodata(trabajo: Path) -> Path:
    t = leer_json(trabajo / "transcripcion.json")
    dur_voz = t["duracion"]
    dur = round(dur_voz + 0.7, 2)
    rel = f"anuncios/{PROD}/tiktok/{AD}"
    fuentes = ["a.mp4", "b.mp4", "c.mp4", "a.mp4", "b.mp4"]
    tomas, cursor, i = [], 0.0, 0
    while cursor < dur - 0.01:
        largo = round(min(2.0, dur - cursor), 2)
        tomas.append({"src": f"productos/{PROD}/broll-tiktok/{fuentes[i % len(fuentes)]}", "desde": round(0.5 + (i // 3) * 2.2, 2), "inicio": round(cursor, 2), "duracion": largo, "zoom": 1.05})
        cursor += largo
        i += 1
    datos = {
        "meta": {"anuncio": AD, "producto": PROD, "tipo": "tiktok", "fps": 30, "ancho": 1080, "alto": 1920, "duracion": dur},
        "estilo": {"fondo": "#000000", "colorPrimario": "#E11D48", "subtitulos": {"tamano": 82, "maxPalabras": 3}, "textoGrande": {"tamano": 112}},
        "zonaInferiorLibre": 280,
        "tomas": tomas,
        "palabras": [{"texto": p["texto"], "inicio": p["inicio"], "fin": p["fin"]} for p in t["palabras"]],
        "textosGrandes": [
            {"texto": "Velas caseras", "inicio": 0.6, "fin": 1.8, "estilo": "caja"},
            {"texto": "Paso a paso", "inicio": round(dur * 0.45, 2), "fin": round(dur * 0.45 + 1.2, 2), "estilo": "plano", "posicion": "arriba"},
            {"texto": "Empieza hoy", "inicio": round(dur * 0.7, 2), "fin": round(dur * 0.7 + 1.0, 2), "estilo": "resaltado", "color": "#FFE14D"},
        ],
        "producto": [{"src": f"productos/{PROD}/portada.jpg", "inicio": round(dur * 0.2, 2), "duracion": 1.3, "modo": "tarjeta", "giro": -5}],
        "cta": {"texto": "Compra hoy", "subtexto": "Link en la descripción", "inicio": round(dur - 1.6, 2), "duracion": 1.5},
        "audio": {
            "voz": {"src": f"{rel}/audio/voz_norm.wav", "volumen": 1},
            "musica": {"src": f"{rel}/audio/musica_ajustada.mp3", "volumen": 1, "fadeInSeg": 0.5, "fadeOutSeg": 1.0},
            "sfx": [{"src": "sfx/hit.mp3", "t": 0.0, "volumen": 0.6}, {"src": "sfx/pop.mp3", "t": 0.6, "volumen": 0.5}, {"src": "sfx/click.mp3", "t": round(dur - 1.6, 2), "volumen": 0.6}],
        },
    }
    ruta = trabajo / "videoData.json"
    escribir_json(ruta, datos)
    return ruta


def paso_validar(ruta: Path) -> str:
    salida = py("scripts/validar_videodata.py", str(ruta))
    return salida.strip().splitlines()[-1]


def paso_sin_herencia(ruta: Path, trabajo: Path) -> str:
    """Lo que un videoData.json no trae no se hereda de la demo (antes un anuncio sin cta mostraba su
    «Prueba superada»): remotion.mjs datos imprime las props que recibira Remotion, sin renderizar."""
    datos = leer_json(ruta)
    vacios = {"cta": None, "producto": [], "textosGrandes": []}
    for clave in vacios:
        datos.pop(clave, None)
    minimo = trabajo / "videoData_sin_cta.json"
    escribir_json(minimo, datos)
    props = json.loads(correr(["node", "scripts/remotion.mjs", "datos", str(minimo)]))
    minimo.unlink()
    # La clave tiene que LLEGAR vacia: si no llega, Remotion la rellena con la de la demo.
    heredadas = [clave for clave, vacio in vacios.items() if clave not in props or props[clave] != vacio]
    if heredadas:
        raise RuntimeError("no llegan vacias a Remotion (las rellenaria con las de la demo): " + ", ".join(heredadas))
    return "sin cta, producto ni textos grandes: llegan vacios, no los de la demo"


def paso_fotogramas(ruta: Path, trabajo: Path) -> str:
    dur = leer_json(ruta)["meta"]["duracion"]
    correr(["node", "scripts/remotion.mjs", "fotogramas", str(ruta), "--segundos", f"0.8,{dur * 0.5:.1f},{dur - 0.5:.1f}", "--salida", str(trabajo / "pruebas")])
    py("scripts/hoja_fotogramas.py", str(trabajo / "pruebas"), "--columnas", "3")
    if not (trabajo / "pruebas" / "hoja.jpg").is_file():
        raise RuntimeError("no se hizo la hoja de fotogramas")
    return "3 fotogramas y su hoja"


def paso_render(ruta: Path, trabajo: Path) -> str:
    salida = correr(["node", "scripts/remotion.mjs", "render", str(ruta)])
    finales = sorted((trabajo / "final").glob("*.mp4"))
    if not finales:
        raise RuntimeError("el render no dejo ningun mp4 en final/")
    mp4 = finales[-1]
    info = ffprobe_json(mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    esperado = leer_json(ruta)["meta"]["duracion"]
    dur = float(info["format"]["duration"])
    problemas = []
    if (v["width"], v["height"]) != (1080, 1920):
        problemas.append(f"tamano {v['width']}x{v['height']}")
    if v["r_frame_rate"] != "30/1":
        problemas.append(f"fps {v['r_frame_rate']}")
    if v["codec_name"] != "h264":
        problemas.append(f"codec {v['codec_name']}")
    if not a:
        problemas.append("sin audio")
    elif a["codec_name"] != "aac":
        problemas.append(f"audio {a['codec_name']}")
    if abs(dur - esperado) > 0.25:
        problemas.append(f"dura {dur:.2f} s y debia durar {esperado} s")
    sonoridad = lufs(mp4) if a else None
    if sonoridad is not None and not -16.5 <= sonoridad <= -12:
        problemas.append(f"volumen {sonoridad} LUFS (debia estar cerca de -14)")
    if problemas:
        raise RuntimeError("; ".join(problemas))
    return f"{mp4.name}: 1080x1920, 30 fps, {dur:.1f} s, {sonoridad} LUFS ({salida.strip().splitlines()[-1][:60]})"


def limpiar() -> None:
    for p in (PUBLIC / "productos" / PROD, PUBLIC / "musica" / "biblioteca" / PROD, ANUNCIOS / PROD, RAIZ / ".cache" / "prueba"):
        shutil.rmtree(p, ignore_errors=True)


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-render", action="store_true")
    ap.add_argument("--conservar", action="store_true", help="no borrar el material de prueba al terminar")
    ap.add_argument("--modelo", default="small")
    a = ap.parse_args()

    for carpeta in (PUBLIC / "productos" / PROD, ANUNCIOS / PROD):
        if carpeta.exists():
            print(f"Ya existe {carpeta}: bórrala o renómbrala antes de probar (es de una prueba anterior).")
            sys.exit(1)

    t0 = time.time()
    trabajo = ANUNCIOS / PROD / "tiktok" / AD
    trabajo.mkdir(parents=True, exist_ok=True)
    print(f"Prueba rapida del editor ({'sin render' if a.sin_render else 'completa'}). Python: {sys.executable}")
    try:
        if not paso("efectos de sonido presentes", paso_sfx):
            return
        if not paso("material sintetico (voz de Windows, videos, musica, ebook)", paso_material):
            return
        paso("ebook -> portada, paginas y texto", paso_pdf)
        paso("b-roll -> indice y hojas de contacto", paso_broll)
        paso("b-roll de TikTok -> autoprueba de scripts/broll_tiktok.py", paso_broll_tiktok)
        paso("referencia de Meta -> autoprueba de scripts/referencia_meta.py", paso_referencia_meta)
        paso("gancho con IA -> autoprueba de scripts/gancho_ia.py", paso_gancho_ia)
        paso("anuncio automatico -> autoprueba de scripts/emparejar_tomas.py y montaje_auto.py", paso_montaje_auto)
        paso("guion y voz automaticos -> autoprueba de scripts/guion_auto.py y voz_auto.py", paso_guion_voz)
        paso("musica de fondo -> autoprueba de scripts/crear_musica.py", paso_musica)
        if not paso(f"transcribir palabra por palabra (modelo {a.modelo})", lambda: paso_transcribir(a.modelo, trabajo)):
            return
        if not paso("voz a -14 LUFS y musica 15 dB mas baja", lambda: paso_mezclar(trabajo)):
            return
        ruta = armar_videodata(trabajo)
        if not paso("validar videoData.json", lambda: paso_validar(ruta)):
            return
        paso("lo que falta en videoData.json no se hereda de la demo", lambda: paso_sin_herencia(ruta, trabajo))
        if a.sin_render:
            OMITIDOS.extend(["fotogramas de prueba", "render completo"])
        else:
            paso("fotogramas de prueba", lambda: paso_fotogramas(ruta, trabajo))
            paso("render completo y revision del mp4", lambda: paso_render(ruta, trabajo))
    finally:
        if a.conservar:
            print(f"Material de prueba conservado en {PUBLIC / 'productos' / PROD} y {ANUNCIOS / PROD}")
        else:
            limpiar()

    total = time.time() - t0
    print()
    if OMITIDOS:
        print(f"Omitido: {', '.join(OMITIDOS)}")
    if FALLAS:
        print(f"PRUEBA CON FALLAS ({len(FALLAS)}): {', '.join(FALLAS)}  [{total:.0f} s]")
        sys.exit(1)
    print(f"PRUEBA OK  [{total:.0f} s]")


if __name__ == "__main__":
    main()
