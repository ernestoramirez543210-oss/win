"""Transcribe un audio o un video palabra por palabra y guarda un JSON con el texto y el
inicio y fin (en segundos) de cada palabra. De ahi salen los subtitulos sincronizados.

Uso:
  python scripts/transcribir.py <audio_o_video> [--salida ruta.json] [--modelo small]
                                [--idioma es|pt|en|auto] [--guion guion.txt]
  python scripts/transcribir.py --solo-descargar [--modelo small]     (solo baja el modelo)

  --salida  Por defecto: anuncios/_transcripciones/<nombre>.json
  --modelo  tiny | base | small (por defecto) | medium. En este PC (2 nucleos, sin GPU)
            small tarda alrededor de la mitad de la duracion del audio; medium es mas
            exacto pero mucho mas lento. El primer uso de cada modelo lo descarga una vez
            (small ~0,5 GB, medium ~1,5 GB) a la carpeta modelos/.
  --guion   Texto del guion: se le pasa a Whisper como pista para que acierte nombres de
            productos y palabras raras. OJO: si la voz dice justo lo que dice la pista, Whisper
            se salta esa parte (le pasa con el guion entero: se come los primeros ~20 s y escribe
            "www.mooji.com"; en otras voces tuerce palabras: "ndaNavidad"). El montaje automatico YA NO
            la usa (corrige despues con el guion: corregir_con_guion). Si se usa a mano, se comprueba
            cuanto quedo escrito del guion y de su arranque
            (lo que va de pista) y, si el menor de los dos es menos del 80 %, se repite SIN pista y
            se queda la que cubre mas.

Salida (JSON): archivo, modelo, idioma, duracion, texto, palabras[{texto, inicio, fin, prob}],
segmentos[{inicio, fin, texto}], pista ("ninguna" | "guion" | "descartada": como se uso el guion) y, con guion, cobertura_guion (0 a 1: el menor entre el guion entero y su arranque).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, RAIZ, configurar_consola, ejecutable, escribir_json, salir  # noqa: E402

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def cargar_audio(ruta: Path):
    """Decodifica con ffmpeg a 16 kHz mono (lo que espera Whisper). Se hace aqui y no con el
    decodificador interno de faster-whisper porque este depende de PyAV, que ya cambio su API
    una vez y rompio la transcripcion; ffmpeg lee cualquier audio o video."""
    import numpy as np

    comando = [
        ejecutable("ffmpeg"), "-nostdin", "-loglevel", "error", "-i", str(ruta),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
    ]
    r = subprocess.run(comando, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        salir(f"ffmpeg no pudo leer el audio de {ruta}: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


COBERTURA_MINIMA = 0.8      # menos de esto del guion escrito = la pista se comio parte de la voz
PISTA_MAX = 600             # caracteres del guion que se le pasan a Whisper como pista


def _plano(texto: str) -> str:
    """El texto en minusculas y sin tildes."""
    plano = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in plano if unicodedata.category(c) != "Mn")


def _fichas(texto: str) -> list[str]:
    """Las palabras de un texto en minusculas y sin tildes ni signos (para comparar el guion con lo que Whisper escribio)."""
    return re.findall(r"[a-z0-9]+", _plano(texto))


def cobertura_guion(guion: str, palabras: list[dict]) -> float:
    """Que parte del guion aparece, en orden, en lo que Whisper escribio (de 0 a 1). Un guion vacio cuenta como cubierto."""
    g = _fichas(guion)
    if not g:
        return 1.0
    t = _fichas(" ".join(str(p.get("texto", "")) for p in palabras))
    if not t:
        return 0.0
    return sum(b.size for b in SequenceMatcher(None, g, t, autojunk=False).get_matching_blocks()) / len(g)


def cobertura_con_pista(guion: str, palabras: list[dict]) -> float:
    """El menor entre lo escrito del guion entero y lo escrito de su arranque (los PISTA_MAX caracteres que van de pista, que es lo unico que la pista se puede comer: con un guion
    larguisimo, lo comido seria una parte chica del total y la cobertura total enganaria)."""
    cabeza = guion[:PISTA_MAX]
    if len(guion) > PISTA_MAX and not guion[PISTA_MAX].isspace() and len(cabeza.split()) > 1:
        cabeza = cabeza.rsplit(None, 1)[0]      # sin la ultima palabra, que quedo cortada
    return min(cobertura_guion(guion, palabras), cobertura_guion(cabeza, palabras))


def corregir_con_guion(palabras: list[dict], guion: str, similitud_minima: float = 0.6) -> tuple[list[dict], int]:
    """Arregla con el guion las palabras que Whisper escucho mal ('nomo' por 'gnomo', 'Fiel Tronavidad' por 'fieltro navidad'): la voz LEE el guion, asi que lo escrito ahi manda.
    Se alinean las palabras de Whisper con las del guion y se cambia donde coinciden:
      - una a una (iguales sin tildes, o distintas pero parecidas: similitud >= 0.6);
      - un grupo de palabras por otro grupo de distinto largo (hasta 3 y 3) cuando, juntas, se parecen: 'de ciclado' por 'reciclado', 'de a' por 'vea', 'fieltronavidad' por 'fieltro navidad'
        (la nueva palabra reparte entre ellas el tiempo de las viejas, en proporcion a su largo; si el bloque esta en un borde del texto se pide mas parecido, 0.75, porque no hay palabras iguales que lo
        confirmen a ambos lados);
      - una palabra repetida pegada a si misma ('para para'), que el guion no tiene.
    Donde faltan, sobran o no se parecen, se deja lo que oyo Whisper. Los signos pegados a cada palabra y los tiempos de las demas no se tocan. Devuelve (palabras nuevas, cuantas palabras de
    Whisper se cambiaron); no modifica la lista que recibe."""
    g = re.findall(r"\w+", guion or "")
    nucleo = lambda texto: re.match(r"^(\W*)(.*?)(\W*)$", str(texto), re.S).groups()  # noqa: E731    (signos del principio, palabra, signos del final)
    salida = [dict(p) for p in palabras]
    idx = [i for i, p in enumerate(salida) if _plano(nucleo(p.get("texto", ""))[1]).strip()]
    wk = ["".join(_fichas(nucleo(salida[i].get("texto", ""))[1])) for i in idx]
    gk = ["".join(_fichas(x)) for x in g]
    ops = SequenceMatcher(None, wk, gk, autojunk=False).get_opcodes()
    cambios = 0
    bloques: list[tuple[int, int, list[str]]] = []      # (primera, ultima, palabras nuevas): se aplican al final, de atras hacia adelante, para no mover los indices
    for n, (op, i1, i2, j1, j2) in enumerate(ops):
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                if op == "replace" and SequenceMatcher(None, wk[i1 + k], gk[j1 + k]).ratio() < similitud_minima:
                    continue
                fila = salida[idx[i1 + k]]
                antes, palabra, despues = nucleo(fila.get("texto", ""))
                if palabra != g[j1 + k]:
                    fila["texto"] = antes + g[j1 + k] + despues
                    cambios += 1
        elif op == "replace" and max(i2 - i1, j2 - j1) <= 3:
            borde = n == 0 or n == len(ops) - 1
            if SequenceMatcher(None, "".join(wk[i1:i2]), "".join(gk[j1:j2])).ratio() >= (0.75 if borde else similitud_minima):
                bloques.append((idx[i1], idx[i2 - 1], g[j1:j2]))
        elif op == "delete" and i2 - i1 == 1 and ((i1 > 0 and wk[i1 - 1] == wk[i1]) or (i2 < len(wk) and wk[i2] == wk[i1])):
            bloques.append((idx[i1], idx[i1], []))
    for desde, hasta, nuevas in reversed(bloques):
        viejas = salida[desde:hasta + 1]
        cambios += len(viejas)
        if not nuevas:
            del salida[desde:hasta + 1]
            continue
        antes, _, _ = nucleo(viejas[0].get("texto", ""))
        _, _, despues = nucleo(viejas[-1].get("texto", ""))
        t0, t1 = float(viejas[0].get("inicio", 0.0)), float(viejas[-1].get("fin", 0.0))
        pesos = [max(1, len(x)) for x in nuevas]
        acumulado, nuevos = 0, []
        for k, palabra in enumerate(nuevas):
            a = t0 + (t1 - t0) * acumulado / sum(pesos)
            acumulado += pesos[k]
            b = t0 + (t1 - t0) * acumulado / sum(pesos)
            fila = dict(viejas[0])
            fila.update({"texto": (antes if k == 0 else "") + palabra + (despues if k == len(nuevas) - 1 else ""), "inicio": round(a, 3), "fin": round(b, 3)})
            nuevos.append(fila)
        salida[desde:hasta + 1] = nuevos
    return salida, cambios


def _nucleo(texto) -> tuple[str, str, str]:
    """(signos del principio, palabra, signos del final) de una palabra de Whisper."""
    return re.match(r"^(\W*)(.*?)(\W*)$", str(texto), re.S).groups()


def diferencias_con_guion(palabras: list[dict], guion: str) -> list[dict]:
    """Donde las palabras que se van a mostrar NO dicen lo mismo que el guion: [{"dicen": [...], "guion": [...]}], vacio si son iguales. No cuentan las tildes, las mayusculas ni los signos, ni una cifra
    en lugar de su nombre ('25' por 'veinticinco'). Sirve para parar ANTES de renderizar (15 a 20 minutos por video) un anuncio con subtitulos mal escritos o con una voz que no dice el guion."""
    g = re.findall(r"\w+", guion or "")
    w = []
    for p in palabras:
        nucleo = _nucleo(p.get("texto", ""))[1]
        k = "".join(_fichas(nucleo))
        if k:
            w.append((k, nucleo))
    wk = [k for k, _ in w]
    gk = ["".join(_fichas(x)) for x in g]
    salida = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, wk, gk, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op == "replace" and (all(x.isdigit() for x in wk[i1:i2]) or all(x.isdigit() for x in gk[j1:j2])):
            continue
        salida.append({"dicen": [texto for _, texto in w[i1:i2]], "guion": g[j1:j2]})
    return salida


def oir(modelo, audio, idioma: str, pista: str | None):
    """Una pasada de Whisper: (palabras, segmentos, info)."""
    segmentos_it, info = modelo.transcribe(
        audio,
        language=None if idioma == "auto" else idioma,      # auto = que Whisper detecte el idioma (anuncios de referencia en portugues, ingles...)
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
        initial_prompt=pista,
    )
    palabras, segmentos = [], []
    for seg in segmentos_it:
        segmentos.append({"inicio": round(seg.start, 3), "fin": round(seg.end, 3), "texto": seg.text.strip()})
        for w in seg.words or []:
            texto = w.word.strip()
            if not texto:
                continue
            palabras.append(
                {
                    "texto": texto,
                    "inicio": round(w.start, 3),
                    "fin": round(w.end, 3),
                    "prob": round(float(w.probability), 3),
                }
            )
    return palabras, segmentos, info


def oir_con_pista(modelo, audio, idioma: str, guion: str | None, aviso=print):
    """Whisper con el guion como pista, SALVO que la pista le haga saltarse la voz: si queda escrito menos del COBERTURA_MINIMA del guion, repite sin pista y se queda con la que cubre mas.
    Devuelve (palabras, segmentos, info, pista, cobertura); pista es "ninguna" sin guion, "guion" si se uso y "descartada" si se repitio sin ella."""
    if not guion:
        palabras, segmentos, info = oir(modelo, audio, idioma, None)
        return palabras, segmentos, info, "ninguna", None
    palabras, segmentos, info = oir(modelo, audio, idioma, guion[:PISTA_MAX])
    cobertura = cobertura_con_pista(guion, palabras)
    if cobertura >= COBERTURA_MINIMA:
        return palabras, segmentos, info, "guion", cobertura
    aviso(f"La pista del guion hizo que Whisper se saltara parte de la voz (solo quedo escrito el {cobertura:.0%} del guion): repito sin pista...")
    palabras2, segmentos2, info2 = oir(modelo, audio, idioma, None)
    cobertura2 = cobertura_con_pista(guion, palabras2)
    if cobertura2 >= cobertura:
        return palabras2, segmentos2, info2, "descartada", cobertura2
    return palabras, segmentos, info, "guion", cobertura


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", nargs="?", help="audio o video a transcribir")
    ap.add_argument("--salida")
    ap.add_argument("--modelo", default="small")
    ap.add_argument("--idioma", default="es")
    ap.add_argument("--guion", help="archivo .txt con el guion, como pista para Whisper")
    ap.add_argument("--solo-descargar", action="store_true", help="solo descarga el modelo y termina (lo usa el instalador)")
    a = ap.parse_args()

    if a.solo_descargar:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            salir("Falta faster-whisper. Corre: python -m pip install -r requirements.txt")
        print(f"Descargando el modelo '{a.modelo}' (una sola vez, queda en modelos/)...", flush=True)
        WhisperModel(a.modelo, device="cpu", compute_type="int8", download_root=str(RAIZ / "modelos"))
        print(f"Modelo '{a.modelo}' listo.")
        return

    if not a.entrada:
        salir("Falta el audio o video a transcribir (o usa --solo-descargar).")
    entrada = Path(a.entrada)
    if not entrada.is_file():
        salir(f"No existe el archivo: {entrada}")
    salida = Path(a.salida) if a.salida else ANUNCIOS / "_transcripciones" / f"{entrada.stem}.json"

    guion = None
    if a.guion:
        g = Path(a.guion)
        if not g.is_file():
            salir(f"No existe el guion: {g}")
        guion = g.read_text(encoding="utf-8", errors="replace").strip() or None

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        salir("Falta faster-whisper. Corre: python -m pip install -r requirements.txt")

    inicio = time.time()
    print(f"Cargando modelo '{a.modelo}' (la primera vez lo descarga)...", flush=True)
    modelo = WhisperModel(
        a.modelo,
        device="cpu",
        compute_type="int8",
        cpu_threads=os.cpu_count() or 4,
        download_root=str(RAIZ / "modelos"),
    )
    print("Transcribiendo...", flush=True)
    palabras, segmentos, info, pista, cobertura = oir_con_pista(modelo, cargar_audio(entrada), a.idioma, guion, aviso=lambda m: print(m, flush=True))

    resultado = {
        "archivo": str(entrada),
        "modelo": a.modelo,
        "idioma": info.language,
        "duracion": round(float(info.duration), 3),
        "texto": " ".join(s["texto"] for s in segmentos).strip(),
        "palabras": palabras,
        "segmentos": segmentos,
        "generado": datetime.now().isoformat(timespec="seconds"),
    }
    resultado["pista"] = pista            # su presencia dice que la hizo la version que comprueba la pista (las viejas no la traen)
    if cobertura is not None:
        resultado["cobertura_guion"] = round(cobertura, 3)
    escribir_json(salida, resultado)

    dudosas = [p["texto"] for p in palabras if p["prob"] < 0.5]
    print(f"Listo en {time.time() - inicio:.0f} s: {len(palabras)} palabras, audio de {resultado['duracion']:.1f} s")
    if cobertura is not None:
        print(f"Guion escrito: {cobertura:.0%}" + (" (con la pista descartada)" if pista == "descartada" else ""))
    print(f"JSON: {salida}")
    print(f"Texto: {resultado['texto'][:400]}")
    if dudosas:
        print(f"Revisa estas palabras (poca confianza): {', '.join(dudosas[:15])}")


if __name__ == "__main__":
    main()
