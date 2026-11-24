"""Revisa un videoData.json ANTES de renderizar: archivos que existen, tomas sin huecos ni
repeticiones, tramos que no se pasan del final del video, textos grandes, zona libre, audio.

Uso:
  python scripts/validar_videodata.py <videoData.json>

Sale con codigo 1 si hay ERRORES (no se debe renderizar). Las ADVERTENCIAS no bloquean.
Las reglas vienen de CLAUDE.md y del flujo de la skill editor-video.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, PUBLIC, RAIZ, configurar_consola, duracion_seg, leer_json, salir  # noqa: E402

ERRORES: list[str] = []
AVISOS: list[str] = []
_dur_cache: dict[str, float] = {}


def error(msg: str) -> None:
    ERRORES.append(msg)


def aviso(msg: str) -> None:
    AVISOS.append(msg)


def resolver(rel: str) -> Path | None:
    """Igual que scripts/remotion.mjs: primero public/, luego la raiz del proyecto (anuncios/...)."""
    limpio = rel.replace("\\", "/").lstrip("/")
    for base in (PUBLIC, RAIZ):
        ruta = (base / limpio).resolve()
        if RAIZ.resolve() in ruta.parents and ruta.is_file():
            return ruta
    return None


def duracion_de(rel: str) -> float | None:
    ruta = resolver(rel)
    if not ruta:
        return None
    if rel not in _dur_cache:
        _dur_cache[rel] = duracion_seg(ruta)
    return _dur_cache[rel]


def revisar(datos: dict, previo: dict | None) -> None:
    meta = datos.get("meta", {})
    for campo in ("anuncio", "producto", "tipo", "fps", "ancho", "alto", "duracion"):
        if campo not in meta:
            error(f"meta.{campo} falta")
    if ERRORES:
        return
    dur = float(meta["duracion"])
    if (meta["ancho"], meta["alto"]) != (1080, 1920):
        aviso(f"El formato es {meta['ancho']}x{meta['alto']}; el estandar de los anuncios es 1080x1920 (9:16).")
    if meta["fps"] != 30:
        aviso(f"fps={meta['fps']}; el estandar es 30.")
    if datos.get("zonaInferiorLibre", 0) < 280:
        error("zonaInferiorLibre debe ser al menos 280 px (ahi va la interfaz de la red social).")

    # ---- archivos
    rutas = [t.get("src") for t in datos.get("tomas", [])] + [p.get("src") for p in datos.get("producto", [])]
    audio = datos.get("audio", {})
    for clave in ("voz", "musica"):
        if audio.get(clave, {}).get("src"):
            rutas.append(audio[clave]["src"])
    rutas += [s.get("src") for s in audio.get("sfx", [])]
    for rel in sorted({r for r in rutas if r}):
        if not resolver(rel):
            error(f"No existe el archivo: {rel} (las rutas son relativas a public/)")

    # ---- tomas
    tomas = sorted(datos.get("tomas", []), key=lambda t: t.get("inicio", 0))
    if not tomas:
        error("No hay tomas de video.")
    cursor = 0.0
    for i, t in enumerate(tomas, 1):
        ini, d = float(t["inicio"]), float(t["duracion"])
        vel = float(t.get("velocidad", 1) or 1)
        if d <= 0:
            error(f"Toma {i}: duracion {d} no valida.")
        if ini - cursor > 0.05:
            error(f"Hueco de {ini - cursor:.2f} s antes de la toma {i} (en {cursor:.2f} s): se veria negro.")
        if ini < cursor - 0.05:
            aviso(f"La toma {i} se solapa {cursor - ini:.2f} s con la anterior.")
        cursor = max(cursor, ini + d)
        if meta["tipo"] == "tiktok" and not (0.8 <= d <= 3.5):
            aviso(f"Toma {i} dura {d:.1f} s; en estilo TikTok los cortes van cada 1 a 3 s.")
        origen = duracion_de(t["src"])
        if origen is not None and float(t["desde"]) + d * vel > origen + 0.05:
            error(f"Toma {i}: pide el segundo {float(t['desde']) + d * vel:.1f} de {t['src']}, que solo dura {origen:.1f} s.")
    if tomas and cursor < dur - 0.1:
        error(f"Las tomas terminan en {cursor:.2f} s pero el anuncio dura {dur:.2f} s: el final quedaria negro.")

    # ---- transiciones (las pone aplicar_receta.py; el campo es toma.transicion = {tipo, duracion})
    for i, t in enumerate(tomas, 1):
        tr = t.get("transicion")
        if tr is None:
            continue
        if i == 1:
            aviso("La primera toma no tiene a donde entrar: su transicion se ignora.")
            continue
        if not isinstance(tr, dict) or tr.get("tipo") not in ("fundido", "deslizar", "zoom", "flash"):
            error(f"Toma {i}: transicion no valida ({tr!r}). Tipos: fundido, deslizar, zoom, flash.")
            continue
        td = float(tr.get("duracion", 0.25))
        tope = min(float(t["duracion"]), float(tomas[i - 2]["duracion"])) / 2
        if td <= 0:
            error(f"Toma {i}: la duracion de la transicion debe ser mayor que 0.")
        elif td > tope + 0.01:
            aviso(f"Toma {i}: la transicion dura {td:.2f} s y se recortara a {tope:.2f} s (la mitad de la toma mas corta de las dos).")

    # ---- tramos repetidos dentro del anuncio
    if meta["tipo"] == "tiktok":
        usados: dict[str, list[tuple[float, float, int]]] = {}
        for i, t in enumerate(tomas, 1):
            a, b = float(t["desde"]), float(t["desde"]) + float(t["duracion"]) * float(t.get("velocidad", 1) or 1)
            for (c, d, j) in usados.get(t["src"], []):
                if a < d and c < b:
                    error(f"Las tomas {j} y {i} repiten el mismo tramo de {t['src']} ({max(a, c):.1f}-{min(b, d):.1f} s).")
            usados.setdefault(t["src"], []).append((a, b, i))
        if previo:
            previas = {(x["src"], round(float(x["desde"]), 1)) for x in previo.get("tomas_usadas", [])}
            repetidas = [t["src"] for t in tomas if (t["src"], round(float(t["desde"]), 1)) in previas]
            if repetidas:
                aviso(f"{len(repetidas)} toma(s) ya se usaron en el anuncio anterior ({previo.get('anuncio')}): evita repetir tramos entre anuncios seguidos.")

    # ---- textos grandes
    textos = sorted(datos.get("textosGrandes", []), key=lambda t: t["inicio"])
    if not 5 <= len(textos) <= 7:
        aviso(f"Hay {len(textos)} textos grandes; el estandar es de 5 a 7 por anuncio.")
    for i, t in enumerate(textos, 1):
        if t["fin"] <= t["inicio"]:
            error(f"Texto grande {i} ('{t['texto']}'): fin <= inicio.")
        if t["fin"] > dur + 0.05:
            error(f"Texto grande {i} termina en {t['fin']} s, despues del final del anuncio ({dur} s).")
        if i < len(textos) and textos[i]["inicio"] < t["fin"]:
            aviso(f"Los textos grandes {i} y {i + 1} se solapan.")

    # ---- palabras y llamada a la accion
    palabras = datos.get("palabras", [])
    if not palabras:
        aviso("No hay palabras: el anuncio saldria sin subtitulos.")
    for a_, b_ in zip(palabras, palabras[1:]):
        if b_["inicio"] < a_["inicio"] - 0.01:
            error(f"Las palabras no estan en orden de tiempo cerca de '{b_['texto']}'.")
            break
    cta = datos.get("cta")
    if not cta:
        aviso("No hay llamada a la accion final.")
    else:
        if cta["inicio"] + cta["duracion"] > dur + 0.05:
            error("La llamada a la accion se pasa del final del anuncio.")
        for t in textos:
            if t["inicio"] < cta["inicio"] + cta["duracion"] and cta["inicio"] < t["fin"]:
                aviso("Un texto grande coincide con la llamada a la accion.")
                break

    # ---- audio
    voz = audio.get("voz")
    if not voz:
        error("Falta audio.voz (la locucion).")
    else:
        d_voz = duracion_de(voz["src"])
        if d_voz is not None and abs(d_voz - dur) > 2.5:
            aviso(f"La voz dura {d_voz:.1f} s y el anuncio {dur:.1f} s: revisa la duracion.")
    musica = audio.get("musica")
    if musica and "musica/biblioteca/" not in musica["src"].replace("\\", "/") and "musica_ajustada" not in musica["src"]:
        aviso("La musica no viene de public/musica/biblioteca/: usa solo musica de esa biblioteca.")
    if musica and "musica_ajustada" not in musica["src"] and float(musica.get("volumen", 0.2)) > 0.6:
        aviso(f"audio.musica.volumen={musica.get('volumen')} es alto: corre mezclar_audio.py para dejarla ~15 dB bajo la voz.")
    sfx = audio.get("sfx", [])
    if len(sfx) > len(tomas) + len(textos) + 3:
        aviso(f"{len(sfx)} efectos de sonido son muchos: van solo en transiciones, textos grandes y la llamada a la accion.")


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videodata")
    a = ap.parse_args()
    ruta = Path(a.videodata)
    if not ruta.is_file():
        salir(f"No existe {ruta}")
    datos = leer_json(ruta)

    previo = None
    try:
        hist = ANUNCIOS / datos["meta"]["producto"] / "historial.json"
        if hist.is_file():
            anuncios = [x for x in leer_json(hist).get("anuncios", []) if x.get("anuncio") != datos["meta"]["anuncio"]]
            previo = anuncios[-1] if anuncios else None
    except (KeyError, TypeError):
        pass

    revisar(datos, previo)
    for m in ERRORES:
        print(f"ERROR: {m}")
    for m in AVISOS:
        print(f"AVISO: {m}")
    if ERRORES:
        print(f"\nNO RENDERIZAR: {len(ERRORES)} error(es), {len(AVISOS)} aviso(s).")
        sys.exit(1)
    print(f"OK: sin errores ({len(AVISOS)} aviso(s)).")


if __name__ == "__main__":
    main()
