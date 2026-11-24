"""¿Se puede usar el anuncio ORIGINAL (el de referencia) como base del nuestro, cambiando solo el inicio? Lo decide con reglas fijas, sin modelo de lenguaje.

Pedido del dueno (2026-10-21): «si se puede usar el original se usa y solo se cambia el inicio; si no, lo creamos todo». Se considera que SIRVE cuando:
  1. el audio esta en español (el idioma que detecto Whisper, con confianza suficiente) y se oye una voz que cuenta algo (no solo musica), y
  2. al menos el 70 % del video NO trae texto escrito encima (el detector de texto quemado de texto_quemado.py).
Ademas se avisa, sin descartar, de lo que conviene mirar: que no sea vertical, que dure demasiado o muy poco, o que la voz diga un telefono.

Aunque sirva, solo se dice «usar el original» si la receta de la oferta lo permite (material = referencia): usar cortes del anuncio de otra persona en un
anuncio pago tiene riesgo de derechos y es decision del dueno. Si sirve pero la receta dice «solo material propio», queda anotado que SIRVE y que NO se usa por eso.

Funciones puras (sin red ni archivos): se prueban con autoprueba_consultas.py.
"""
from __future__ import annotations

import re

PARAMETROS = {"prob_idioma_min": 0.6, "texto_max_pct": 30, "palabras_min": 15, "dur_min": 10.0, "dur_max": 180.0, "vertical_min": 1.3}
IDIOMAS = {"es": "español", "pt": "portugués", "en": "inglés", "fr": "francés", "it": "italiano", "de": "alemán", "ca": "catalán", "gl": "gallego"}
_TELEFONO = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{6,}\d)(?!\d)")


def nombre_idioma(codigo: str) -> str:
    codigo = (codigo or "").strip().lower()
    return IDIOMAS.get(codigo, f"idioma «{codigo}»" if codigo else "idioma desconocido")


def menciona_telefono(texto: str) -> bool:
    """Una secuencia de 8 o mas cifras (con espacios, guiones o parentesis) en lo que dice la voz."""
    return any(len(re.sub(r"\D", "", m.group(0))) >= 8 for m in _TELEFONO.finditer(texto or ""))


def evaluar(ref: dict, parametros: dict | None = None) -> dict:
    """Mira un anuncio de referencia ya analizado y dice si sirve de base. `ref`: ad_id, idioma, prob_idioma, palabras, duracion, ancho, alto, texto_pct,
    limpio_seg, transcripcion (todos opcionales: lo que falta cuenta como «no se pudo comprobar»).
    Devuelve {'ad_id', 'sirve', 'motivos_no': [...], 'avisos': [...], 'idioma', 'texto_pct', 'limpio_seg', ...}."""
    p = {**PARAMETROS, **(parametros or {})}
    no, avisos = [], []

    idioma = str(ref.get("idioma") or "").lower()
    prob = ref.get("prob_idioma")
    if not idioma:
        no.append("no se pudo saber en qué idioma habla")
    elif idioma != "es":
        no.append(f"el audio está en {nombre_idioma(idioma)}, no en español")
    elif prob is not None and float(prob) < float(p["prob_idioma_min"]):
        no.append(f"no se está seguro de que el audio sea español (confianza {float(prob):.0%})")

    palabras = ref.get("palabras")
    if palabras is None:
        no.append("no se pudo transcribir el audio")
    elif int(palabras) < int(p["palabras_min"]):
        no.append(f"casi no se oye una voz que cuente algo (solo {int(palabras)} palabras en el audio)")

    texto = ref.get("texto_pct")
    if texto is None:
        no.append("no se pudo medir cuánto texto trae escrito encima")
    elif float(texto) > float(p["texto_max_pct"]):
        no.append(f"trae texto escrito encima en el {float(texto):.0f} % del video (se pide como máximo {float(p['texto_max_pct']):.0f} %)")

    dur = ref.get("duracion")
    if dur is not None:
        if float(dur) > float(p["dur_max"]):
            avisos.append(f"dura {float(dur):.0f} s: es más largo que un anuncio normal")
        elif float(dur) < float(p["dur_min"]):
            avisos.append(f"dura solo {float(dur):.0f} s")
    ancho, alto = ref.get("ancho"), ref.get("alto")
    if ancho and alto and float(alto) / float(ancho) < float(p["vertical_min"]):
        avisos.append("no es vertical (9:16): habría que recortarlo o ponerle bandas")
    if menciona_telefono(str(ref.get("transcripcion") or "")):
        avisos.append("la voz dice un número de teléfono (habría que cortar esa parte)")

    return {"ad_id": str(ref.get("ad_id") or ""), "n": ref.get("n"), "sirve": not no, "motivos_no": no, "avisos": avisos, "idioma": idioma, "idioma_nombre": nombre_idioma(idioma) if idioma else "",
            "prob_idioma": prob, "palabras": palabras, "texto_pct": texto, "limpio_seg": ref.get("limpio_seg"), "duracion": dur}


def elegir_mejor(evaluaciones: list[dict]) -> dict | None:
    """El que sirve y trae menos texto encima (a igualdad, el que tiene mas segundos limpios y el primero en la lista). None si ninguno sirve."""
    buenos = [e for e in evaluaciones if e.get("sirve")]
    if not buenos:
        return None
    return sorted(buenos, key=lambda e: (float(e.get("texto_pct") or 0), -float(e.get("limpio_seg") or 0), e.get("n") or 99))[0]


def _nombre(e: dict) -> str:
    return f"anuncio REF{e['n']} ({e['ad_id']})" if e.get("n") else f"anuncio {e['ad_id']}"


def decidir(evaluaciones: list[dict], receta_material: str = "propio", gancho: dict | None = None) -> dict:
    """La decision sobre la oferta: {'sirve_tecnicamente', 'usar_original', 'mejor' (ad_id), 'receta_material', 'frase' (para el informe), 'reemplazar_inicio_seg'}."""
    gancho = gancho or {}
    mejor = elegir_mejor(evaluaciones)
    permite = receta_material == "referencia"
    seg = gancho.get("segundos")
    modo = gancho.get("modo") or ""
    base = {"sirve_tecnicamente": mejor is not None, "usar_original": bool(mejor and permite), "mejor": mejor["ad_id"] if mejor else None, "receta_material": receta_material,
            "reemplazar_inicio_seg": int(seg) if isinstance(seg, (int, float)) and modo != "sin-gancho" else None}
    if not evaluaciones:
        base["frase"] = "No se pudo estudiar ningún anuncio original, así que no se puede decir si sirve: se crea todo con material nuestro."
    elif mejor is None:
        razones = []
        for e in evaluaciones:
            razones += [m for m in e["motivos_no"] if m not in razones]
        base["frase"] = "Ningún anuncio original sirve para usarlo tal cual (" + "; ".join(razones[:3]) + "): se crea todo con material nuestro."
    elif not permite:
        base["frase"] = (f"El {_nombre(mejor)} sirve de base (español y poco texto encima), pero la receta de esta oferta dice «solo material propio»: "
                         "no se usan cortes del original a menos que el dueño lo cambie a «también cortes del anuncio de referencia».")
    else:
        inicio = f" y solo se rehace el inicio ({base['reemplazar_inicio_seg']} s)" if base["reemplazar_inicio_seg"] else ""
        base["frase"] = f"Se puede usar el {_nombre(mejor)} como base{inicio}: está en español y trae poco texto encima."
    return base
