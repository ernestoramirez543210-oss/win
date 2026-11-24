"""Revisa que el editor de anuncios este completo y dice que falta y como arreglarlo.

Uso:
  python scripts/verificar_instalacion.py [--saldo]

Comprueba: Node, Python 3.12, ffmpeg, librerias de Python, dependencias de Remotion (misma
version todas), navegador de Remotion, modelo de Whisper, modelos de CLIP, efectos de sonido,
musica, la conexion con el dash en .env (DASH_URL y DASH_CLAVE_EDITOR, sin mostrar la llave: por ahi pasan
APIMart y la voz; el editor ya no usa la llave de APIMart) y los productos. --saldo ademas le pregunta al dash
cuanto queda del tope del dia (es la prueba de que la conexion funciona; no gasta nada).
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import EXT_VIDEO, PUBLIC, RAIZ, configurar_consola, correr  # noqa: E402

FALTAS: list[str] = []


def ok(txt: str) -> None:
    print(f"  ok     {txt}")


def falta(txt: str, arreglo: str) -> None:
    FALTAS.append(f"{txt} -> {arreglo}")
    print(f"  FALTA  {txt}")


def version(cmd: list[str]) -> str | None:
    try:
        r = correr(cmd)
    except FileNotFoundError:
        return None
    return (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 and (r.stdout or r.stderr).strip() else None


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saldo", action="store_true")
    a = ap.parse_args()

    print("Instalacion")
    manifiesto = RAIZ / ".instalado.json"
    version_fuente = (RAIZ / "VERSION").read_text(encoding="utf-8").strip() if (RAIZ / "VERSION").is_file() else "?"
    if manifiesto.is_file():
        m = json.loads(manifiesto.read_text(encoding="utf-8"))
        ok(f"version {m.get('version', '?')} instalada el {str(m.get('actualizado', '?'))[:10]} (la carpeta trae la {version_fuente})")
    else:
        print(f"  --     sin .instalado.json (no se instalo con Instalar-Editor.ps1); version de la carpeta: {version_fuente}")
    if (RAIZ / ".venv" / "Scripts" / "python.exe").is_file():
        ok("entorno de Python propio (.venv)")
    else:
        print("  --     sin .venv: se usa el Python del sistema (Instalar-Editor.ps1 lo crea y aisla las librerias)")

    print("Programas")
    v = version(["node", "-v"])
    ok(f"Node {v}") if v else falta("Node.js", "winget install OpenJS.NodeJS.LTS y reiniciar la app")
    py = sys.version_info
    ok(f"Python {py.major}.{py.minor}.{py.micro}") if (py.major, py.minor) == (3, 12) else falta(f"Python 3.12 (esta corriendo {py.major}.{py.minor})", "instalar Python 3.12; las librerias de IA fallan con las versiones nuevas")
    for prog in ("ffmpeg", "ffprobe"):
        v = version([prog, "-version"])
        ok(v[:40]) if v else falta(prog, "winget install Gyan.FFmpeg y reiniciar la app")

    print("Librerias de Python")
    for paquete in ("faster-whisper", "pymupdf", "rembg", "pillow", "numpy", "scipy", "requests", "yt-dlp", "curl_cffi", "onnxruntime", "tokenizers", "rapidocr-onnxruntime"):
        try:
            ok(f"{paquete} {md.version(paquete)}")
        except md.PackageNotFoundError:
            falta(paquete, "volver a correr Instalar-Editor.cmd (o: python -m pip install -r requirements.txt)")

    print("Remotion")
    pkg = json.loads((RAIZ / "package.json").read_text(encoding="utf-8"))
    esperado = pkg["dependencies"]["remotion"]
    instaladas = set()
    for nombre in ("remotion", "@remotion/cli", "@remotion/bundler", "@remotion/renderer", "@remotion/tailwind-v4"):
        ruta = RAIZ / "node_modules" / nombre / "package.json"
        if ruta.is_file():
            instaladas.add(json.loads(ruta.read_text(encoding="utf-8"))["version"])
        else:
            instaladas.add(None)
    if instaladas == {esperado}:
        ok(f"remotion y sus paquetes @remotion/* en {esperado}")
    else:
        falta(f"paquetes de Remotion incompletos o con versiones distintas ({instaladas})", "en la carpeta del proyecto: npm install")
    navegador = list((RAIZ / "node_modules" / ".remotion").glob("**/chrome-headless-shell*")) if (RAIZ / "node_modules" / ".remotion").is_dir() else []
    ok("navegador de Remotion (Chrome Headless Shell)") if navegador else falta("navegador de Remotion", "node scripts/remotion.mjs navegador")
    if (RAIZ / "node_modules" / ".bin").is_dir():
        r = subprocess.run(["npx.cmd" if sys.platform == "win32" else "npx", "tsc", "--noEmit"], capture_output=True, text=True, cwd=RAIZ, shell=False)
        ok("el proyecto compila (tsc sin errores)") if r.returncode == 0 else falta("el proyecto no compila", f"revisar: {(r.stdout or r.stderr)[:200]}")

    print("Modelo de transcripcion")
    modelos = list((RAIZ / "modelos").glob("models--Systran--faster-whisper-*")) if (RAIZ / "modelos").is_dir() else []
    ok("modelo(s): " + ", ".join(m.name.split("faster-whisper-")[1] for m in modelos)) if modelos else falta("modelo de Whisper", "se descarga solo la primera vez que corras scripts/transcribir.py")

    print("Modelos de CLIP (anuncio automatico)")
    import clip_local

    estado_clip = clip_local.estado()
    malos = [k for k, v in estado_clip.items() if v != "ok"]
    ok(f"modelos de CLIP completos ({clip_local.CARPETA})") if not malos else falta(f"modelos de CLIP ({', '.join(malos)})", "python scripts/emparejar_tomas.py --solo-descargar (~300 MB, una sola vez)")

    print("Audio")
    sfx = sorted((PUBLIC / "sfx").glob("*.mp3"))
    esperados = {"whoosh", "whoosh_corto", "pop", "pop_suave", "click", "ding", "hit", "kaching", "click_camara"}
    faltan_sfx = esperados - {p.stem for p in sfx}
    ok(f"{len(sfx)} efectos de sonido") if not faltan_sfx else falta(f"efectos {sorted(faltan_sfx)}", "python scripts/crear_sfx.py")
    musica = [p for p in (PUBLIC / "musica" / "biblioteca").rglob("*.mp3")]
    ok(f"{len(musica)} pistas de musica en la biblioteca") if musica else falta("musica en public/musica/biblioteca/", "corre `python scripts/crear_musica.py` (baja la biblioteca musical de Drive si este PC la tiene y crea pistas propias, sin licencia de terceros), o copia ahi mp3 libres de derechos")

    print("Conexion con el dash (APIMart y la voz pasan por ahi)")
    import apimart as A

    env = RAIZ / ".env"
    if A.config_dash():
        url, llave_dash = A.config_dash()
        ok(f".env con DASH_URL ({url}) y DASH_CLAVE_EDITOR ({len(llave_dash)} caracteres)")
        if a.saldo:
            r = correr([sys.executable, str(RAIZ / "scripts" / "apimart.py"), "saldo"])
            print("\n".join("         " + l for l in ((r.stdout or "") + (r.stderr or "")).strip().splitlines()))
            if r.returncode != 0:
                FALTAS.append("el dash no acepta esta conexion -> lee el mensaje de arriba (si dice que la llave no sirve: baja otra vez la conexion del dash y doble clic en Conectar-Editor.cmd)")
    else:
        falta("conexion con el dash (DASH_URL y DASH_CLAVE_EDITOR en .env)", A.sin_conexion(gasto=False))
    if A.leer_env("APIMART_API_KEY"):
        print("  ojo    .env todavia trae APIMART_API_KEY: ya no se usa (todo pasa por el dash). Borra esa linea (o corre Conectar-Editor.cmd con la conexion del dash, que la quita).")

    print("B-roll de TikTok")
    llave_busqueda = ""
    if env.is_file():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.strip().startswith("SCRAPECREATORS_API_KEY="):
                llave_busqueda = linea.split("=", 1)[1].strip().strip("\"'")
    if llave_busqueda:
        ok(f".env con SCRAPECREATORS_API_KEY ({len(llave_busqueda)} caracteres): la busqueda por palabra esta activa")
    else:
        print("  --     sin SCRAPECREATORS_API_KEY en .env: la busqueda por palabra no esta activa (broll_tiktok.py sigue sirviendo con --cuenta y --enlaces)")

    print("Productos")
    productos = [p for p in sorted((PUBLIC / "productos").iterdir()) if p.is_dir() and p.name != "producto-ejemplo"]
    if not productos:
        print("  (ninguno todavia: copia producto-ejemplo o usa scripts/nuevo_producto.py)")
    for p in productos:
        videos = [v for v in (p / "broll-tiktok").glob("*") if v.suffix.lower() in EXT_VIDEO] if (p / "broll-tiktok").is_dir() else []
        voces = list((p / "voces").glob("*.mp3")) if (p / "voces").is_dir() else []
        guiones = list((p / "guiones").glob("*.txt")) if (p / "guiones").is_dir() else []
        avatares = [d for d in (p / "avatares").iterdir() if d.is_dir()] if (p / "avatares").is_dir() else []
        print(f"  {p.name}: ebook={'si' if (p / 'ebook.pdf').is_file() else 'NO'}, ficha={'si' if (p / 'producto.json').is_file() else 'NO'}, {len(videos)} videos de b-roll, {len(voces)} voces, {len(guiones)} guiones, {len(avatares)} avatar(es)")

    print()
    if FALTAS:
        print(f"FALTAN {len(FALTAS)} cosa(s):")
        for f in FALTAS:
            print(f"  - {f}")
        sys.exit(1)
    print("Todo en orden.")


if __name__ == "__main__":
    main()
