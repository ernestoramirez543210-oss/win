"""Transcribe lo que dice un video con faster-whisper, SIN internet y sin gastar tokens de Claude. Es la parte de «escuchar» del servidor (VPS) de la Fabrica:
con lo que dicen los anuncios originales se sabe de que se trata y en que idioma estan.

Diferencias con scripts/transcribir.py (el del editor, que da palabra por palabra para los subtitulos):
  - aqui no hace falta el tiempo de cada palabra, solo las frases con su minuto y el idioma que Whisper detecta (no se le impone uno);
  - el modelo (`small`, ~0,5 GB) se lee de una carpeta fija, /opt/broll-tiktok/modelos/faster-whisper-small, que baja el instalador con su SHA-256;
    el programa no baja nada por su cuenta;
  - solo se transcriben los primeros 4 minutos (un anuncio dura menos; un video largo no deja ocupado el servidor);
  - se quitan las frases que Whisper inventa cuando solo hay musica («Legendas pela comunidade Amara.org», «Gracias por ver»...).

Uso:  python scripts/transcribir_vps.py video.mp4 [--modelo carpeta] [--idioma es]    imprime el idioma, la duracion y el texto
Necesita ffmpeg y el paquete faster-whisper (esta en vps/requirements-vps.txt).
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import RAIZ, configurar_consola  # noqa: E402

MODELO_DIR = RAIZ / "modelos" / "faster-whisper-small"
ARCHIVOS_MODELO = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
MAX_SEGUNDOS = 240
_MODELO = None
_INVENTADAS = re.compile(
    r"amara\.org|legendas? (pela|por|de) |subt[ií]tulos? (realizados|por|de la)|obrigad[oa] por (assistir|ver)|gracias por (ver|mirar)|thanks? for watching|"
    r"inscreva-se|suscr[ií]bete|no olvides suscribirte|\bwww\.|\.com\b", re.I)


class ErrorTranscripcion(Exception):
    """No se pudo transcribir este video (no se abre, o el modelo fallo con el)."""


class FaltaModelo(ErrorTranscripcion):
    """Falta el modelo o el paquete en este equipo: no sirve intentar con otro video."""


def modelo_listo(carpeta: Path | str | None = None) -> bool:
    c = Path(carpeta) if carpeta else MODELO_DIR
    return c.is_dir() and all((c / n).is_file() for n in ARCHIVOS_MODELO)


def disponible(carpeta: Path | str | None = None) -> bool:
    return bool(importlib.util.find_spec("faster_whisper")) and modelo_listo(carpeta) and bool(shutil.which("ffmpeg"))


def _tiene_audio(ruta: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(ruta)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ErrorTranscripcion(f"ffprobe no pudo abrir {ruta.name}: {(r.stderr or '').strip()[:120]}")
    return bool(r.stdout.strip())


def audio_16k(ruta: Path, max_seg: int = MAX_SEGUNDOS):
    """El audio a 16 kHz, mono, como lista de numeros (lo que espera Whisper), hasta `max_seg` segundos."""
    import numpy as np

    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(ruta), "-vn", "-ac", "1", "-ar", "16000", "-t", str(max_seg), "-f", "s16le", "-"],
                       capture_output=True, timeout=600)
    if r.returncode != 0 or not r.stdout:
        raise ErrorTranscripcion(f"ffmpeg no pudo sacar el audio de {ruta.name}: {r.stderr.decode('utf-8', 'replace').strip()[:120]}")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def limpiar_segmentos(segmentos: list[dict]) -> list[dict]:
    """Quita las frases que Whisper inventa sobre la musica y las que se repiten sin parar."""
    salida, repetidas, anterior = [], 0, None
    for s in segmentos:
        texto = re.sub(r"\s+", " ", str(s.get("texto") or "")).strip()
        if not texto or _INVENTADAS.search(texto):
            continue
        clave = re.sub(r"\W+", "", texto.lower())
        repetidas = repetidas + 1 if clave == anterior else 0
        anterior = clave
        if repetidas >= 2:
            continue
        salida.append({"inicio": round(float(s["inicio"]), 2), "fin": round(float(s["fin"]), 2), "texto": texto})
    return salida


def _motor_whisper(carpeta: Path):
    """El motor real: devuelve una funcion (audio, idioma) -> (segmentos, info). El modelo se carga una sola vez por programa."""
    global _MODELO
    if _MODELO is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise FaltaModelo("falta el paquete faster-whisper en este equipo") from None
        if not modelo_listo(carpeta):
            raise FaltaModelo(f"falta el modelo de Whisper en {carpeta} (lo baja vps/instalar-vps.sh)")
        # el modelo se abre desde su carpeta: no toca internet
        _MODELO = WhisperModel(str(carpeta), device="cpu", compute_type="int8", cpu_threads=max(1, min(os.cpu_count() or 2, 4)))

    def motor(audio, idioma):
        segs, info = _MODELO.transcribe(audio, language=idioma, beam_size=5, condition_on_previous_text=False, word_timestamps=False,
                                         vad_filter=True, vad_parameters={"min_silence_duration_ms": 500})
        lista = [{"inicio": s.start, "fin": s.end, "texto": s.text} for s in segs]
        return lista, {"language": info.language, "language_probability": float(info.language_probability), "duration": float(info.duration)}
    return motor


def transcribir(ruta: Path | str, *, carpeta_modelo: Path | str | None = None, idioma: str | None = None, max_seg: int = MAX_SEGUNDOS, motor=None) -> dict:
    """Lo que dice el video: {'idioma', 'prob_idioma', 'duracion', 'analizado_seg', 'recortado', 'sin_audio', 'palabras', 'texto', 'segmentos'}.
    `idioma=None` deja que Whisper lo detecte. `motor` (opcional, para pruebas) es una funcion (audio, idioma) -> (segmentos, info)."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ErrorTranscripcion(f"no existe {ruta}")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise FaltaModelo("faltan ffmpeg o ffprobe en este equipo")
    base = {"modelo": "faster-whisper-small", "idioma": "", "prob_idioma": None, "duracion": 0.0, "analizado_seg": 0.0, "recortado": False, "sin_audio": False,
            "palabras": 0, "texto": "", "segmentos": [], "generado": datetime.now().isoformat(timespec="seconds")}
    if not _tiene_audio(ruta):
        return {**base, "sin_audio": True}
    carpeta = Path(carpeta_modelo) if carpeta_modelo else MODELO_DIR
    motor = motor or _motor_whisper(carpeta)
    audio = audio_16k(ruta, max_seg)
    try:
        segmentos, info = motor(audio, idioma)
    except ErrorTranscripcion:
        raise
    except Exception as e:  # noqa: BLE001 - un fallo raro del modelo con un video no debe tumbar la corrida: se cuenta como error de ese video
        raise ErrorTranscripcion(f"Whisper fallo con {ruta.name}: {e.__class__.__name__}: {str(e)[:100]}") from None
    segmentos = limpiar_segmentos(segmentos)
    texto = " ".join(s["texto"] for s in segmentos).strip()
    dur = float(info.get("duration") or 0.0)
    analizado = round(len(audio) / 16000.0, 2)
    return {**base, "idioma": str(info.get("language") or ""), "prob_idioma": round(float(info.get("language_probability") or 0.0), 3),
            "duracion": round(dur or analizado, 2), "analizado_seg": analizado, "recortado": bool(dur and dur > analizado + 1), "palabras": len(texto.split()),
            "texto": texto, "segmentos": segmentos}


def con_tiempos(res: dict) -> str:
    """El texto en lineas «[0:03] frase», para leerlo (se guarda en Drive junto al video)."""
    lineas = [f"[{int(s['inicio'] // 60)}:{int(s['inicio'] % 60):02d}] {s['texto']}" for s in res.get("segmentos", [])]
    return "\n".join(lineas) + ("\n" if lineas else "")


def main() -> None:
    configurar_consola()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    opciones = {}
    for i, a in enumerate(sys.argv[1:], 1):
        if a in ("--modelo", "--idioma") and i < len(sys.argv) - 1:
            opciones[a[2:]] = sys.argv[i + 1]
    try:
        res = transcribir(args[0], carpeta_modelo=opciones.get("modelo"), idioma=opciones.get("idioma"))
    except ErrorTranscripcion as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Idioma: {res['idioma'] or '?'} ({res['prob_idioma']}) | duracion {res['duracion']} s | {res['palabras']} palabras" + (" | SIN AUDIO" if res["sin_audio"] else ""))
    print(con_tiempos(res) or "(sin texto)")


if __name__ == "__main__":
    main()
