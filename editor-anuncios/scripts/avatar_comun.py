"""Reglas del anuncio de AVATAR con IA que no tocan red ni disco (lo comparten avatar_auto.py y el corredor del servidor, que las usa para decidir SI un pedido cabe en el tope antes de gastar).

Un anuncio de avatar es una persona (nuestro avatar) hablando a camara en clips de 8 s: cada clip se hace de un cuadro (imagen) y un video con voz y labios generados por el modelo
(references/prompts-ia.md). Aqui viven:
  - el costo y el largo: cuantos clips caben en la receta y en el tope de gasto, y cuanto cuesta (el dash dice «≈ US$ 1 por anuncio»; con el tope de US$ 0,30 solo caben 3 clips);
  - la ficha del avatar (avatar.json): lo que tiene que traer para que se pueda usar (y que su dueño autorizo su cara y su voz);
  - partir el guion en clips (frases enteras, de 25 a 30 palabras cada uno: la regla de prompts-ia.md que revisar_prompts.py exige antes de pagar);
  - validar las escenas que escribe el modelo barato y armar los prompts finales con las plantillas del editor;
  - medir si un clip dice lo que debia (Whisper contra el texto del clip) y si un cuadro sirve (la respuesta del modelo de vision).
Nada de esto gasta ni escribe: son funciones puras, probadas en autoprueba_avatar.py.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import unicodedata
from pathlib import Path

import revisar_prompts as RP

# Precios medidos con el saldo de APIMart (gancho_ia.py y la skill editor-video los repiten; autoprueba_avatar.py comprueba que coincidan)
COSTO_IMAGEN = 0.0085          # gpt-image-2 1k 9:16
COSTO_CLIP = 0.0816            # grok-imagine-1.5-video-ext, 8 s, 480p, con voz y labios
COSTO_CUADRO_Y_CLIP = round(COSTO_IMAGEN + COSTO_CLIP, 4)
RESERVA_TEXTO = 0.02           # el guion, las escenas y la revision de cada cuadro con el modelo barato de texto y vision (≈ 0,003 + 0,003 + 0,001 por cuadro, con aire)
SEG_POR_CLIP = 7.4             # lo que dura la voz de un clip de 8 s despues de recortarle los silencios de los extremos (unir_clips.py); los clips reales dan entre ~6,5 y 8 s
MARGEN_DURACION = 2.0          # segundos de aire sobre el minimo de la receta: la compuerta de duracion solo tolera 0,5 s y un anuncio de 3 clips puede dar 19,4 s en vez de 22,2 (ensayo del 2026-10-23)
PALABRAS_POR_CLIP = 30         # la skill pide de 25 a 30 palabras por clip de 8 s y que nunca se corte una frase a la mitad; revisar_prompts.py lo exige antes de pagar el clip
PALABRAS_MIN_POR_CLIP = 25     # (hasta el 2026-11-10 el avatar usaba de 20 a 28: con menos de 25 sobra tiempo y el modelo inventa palabras o se queda callado)
PALABRAS_IDEAL_CLIP = 27.5
COBERTURA_MINIMA = 0.85        # lo que debe coincidir lo que oye Whisper con el texto del clip
EXT_FOTO = (".jpg", ".jpeg", ".png", ".webp")
CARPETA_AVATAR = "AVATAR"      # en Drive: MI NEGOCIO › OFERTAS LATAM › <OFERTA> › AVATAR (fotos + avatar.json)
CAMPOS_EN_INGLES = ("aspecto", "voz", "acento", "casa_base")
NOMBRE_AVATAR = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,47}\Z")
PROHIBIDAS_EN_PROMPT = re.compile(r"\b(cinematic|4k|8k|studio lighting|bokeh|photoshoot|professional photo|dslr|hdr|film grain)\b", re.I)
RE_ENLACE = re.compile(r"https?:|www\.|@\w|\b\d{7,}\b", re.I)


class ErrorAvatar(Exception):
    """Un problema que se explica solo: `codigo` es el de salida de avatar_auto.py (1 error, 2 faltan insumos, 3 espera el OK de gasto, 5 incompleto o no apto)."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


# ------------------------------------------------------------------ cuanto cuesta y cuantos clips caben
def costo_estimado(n_clips: int, reintentos_clip: int = 0, reintentos_cuadro: int = 0) -> float:
    """US$ de un anuncio de n clips: cada uno con su cuadro, mas la reserva de textos, mas lo que se repita."""
    return round(n_clips * COSTO_CUADRO_Y_CLIP + RESERVA_TEXTO + reintentos_clip * COSTO_CLIP + reintentos_cuadro * COSTO_IMAGEN, 4)


def duracion_estimada(n_clips: int) -> float:
    return round(n_clips * SEG_POR_CLIP, 1)


def duracion_minima_segura(n_clips: int) -> int:
    """La duracion minima de receta (segundos) con la que n clips cumplen casi seguro: n clips tipicos menos el margen. 3 clips -> 20 s."""
    return int(math.floor(n_clips * SEG_POR_CLIP - MARGEN_DURACION + 1e-9))


def clips_que_caben(dur_min: float, dur_max: float, disponible: float | None) -> dict:
    """Cuantos clips tiene que tener el anuncio: los que cubren la duracion MINIMA de la receta como piso, los de su punto medio como meta, nunca mas de lo que cabe en `disponible` (US$) ni de la
    duracion maxima. `cabe` = que el piso entre en el dinero. Si no cabe, `n` = 0 y `costo_minimo` dice cuanto costaria el anuncio mas corto que acepta la receta."""
    dur_min, dur_max = float(dur_min), max(float(dur_min), float(dur_max))
    n_min = max(2, math.ceil((dur_min + MARGEN_DURACION) / SEG_POR_CLIP - 1e-9))        # el 1e-9: (20,2 + 2) / 7,4 da 3,0000000000000004 y saldria un clip de mas
    n_dur_max = max(n_min, int(dur_max / SEG_POR_CLIP + 1e-9))
    ventana_ok = n_min <= int((dur_max + 0.5) / SEG_POR_CLIP + 1e-9)          # con n_min clips el anuncio no se pasa del maximo de la receta (la compuerta tolera 0,5 s); una receta muy angosta no la cumple ningun numero de clips
    n_obj = min(max(round(((dur_min + dur_max) / 2) / SEG_POR_CLIP), n_min), n_dur_max)
    n_dinero = n_dur_max if disponible is None else max(0, int((float(disponible) - RESERVA_TEXTO) // COSTO_CUADRO_Y_CLIP))
    n = min(n_obj, n_dinero)
    cabe = n >= n_min and ventana_ok
    return {"n_min": n_min, "n_max_dur": n_dur_max, "n_objetivo": n_obj, "n_dinero": n_dinero, "n": n if cabe else 0, "cabe": cabe, "ventana_ok": ventana_ok, "dur_min": dur_min, "dur_max": dur_max,
            "costo_minimo": costo_estimado(n_min), "costo": costo_estimado(n if cabe else n_min), "duracion": duracion_estimada(n if cabe else n_min)}


def mensaje_de_costo(c: dict, disponible: float | None, origen: str = "el tope") -> str:
    """La frase para el dueño cuando el anuncio mas corto que acepta la receta no cabe en el tope (o la receta es tan angosta que ningun numero de clips la cumple), con las cifras."""
    if not c.get("ventana_ok", True):
        return (f"la receta pide de {c['dur_min']:g} a {c['dur_max']:g} s y un clip de 8 s dura ≈ {SEG_POR_CLIP:g} s: con {c['n_min']} clips (≈ {duracion_estimada(c['n_min']):g} s) el anuncio se pasaria del maximo y con "
                f"{c['n_min'] - 1} no llegaria al minimo con margen. Ensancha la receta (por ejemplo de {c['dur_min']:g} a {c['dur_min'] + 15:g} s).")
    base = (f"un anuncio de avatar de {c['n_min']} clips de 8 s (≈ {duracion_estimada(c['n_min']):g} s, lo minimo que pide la receta) costaria ≈ US$ {c['costo_minimo']:.2f} "
            f"(cada clip con su cuadro: US$ {COSTO_CUADRO_Y_CLIP:.2f}) y {origen} es US$ {float(disponible or 0):.2f}.")
    if c["n_dinero"] >= 2:
        return base + (f" Solo caben {c['n_dinero']} clips (≈ {duracion_estimada(c['n_dinero']):g} s): pide en la receta una duracion minima de {duracion_minima_segura(c['n_dinero'])} s o menos, "
                       "o sube el tope de gasto de esta oferta.")
    return base + f" Ni dos clips caben (cuestan ≈ US$ {costo_estimado(2):.2f}): sube el tope de gasto de esta oferta."


# ------------------------------------------------------------------ la ficha del avatar (avatar.json)
def _empieza_con(texto: str, *prefijos: str) -> bool:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c)).strip()
    return any(t.startswith(p) for p in prefijos)


def problemas_de_ficha(j, fotos_en_disco: list[str] | None = None) -> list[str]:
    """Lo que le falta a un avatar.json para poder usarse. Lista vacia = listo. `fotos_en_disco` (nombres) se usa para comprobar que las fotos de `fotos` existen."""
    if not isinstance(j, dict):
        return ["avatar.json no se pudo leer (tiene que ser un JSON con la ficha)"]
    p: list[str] = []
    if j.get("autorizado") is not True:
        p.append("falta «autorizado»: true (solo se usan fotos de personas que autorizaron su cara y su voz en anuncios)")
    fotos = [Path(x).name for x in (j.get("fotos") or []) if isinstance(x, str) and Path(x).suffix.lower() in EXT_FOTO]
    if not fotos:
        p.append("«fotos» esta vacio (de 1 a 3 fotos de frente, .jpg, .png o .webp)")
    elif fotos_en_disco is not None:
        faltan = [f for f in fotos if f not in fotos_en_disco]
        if faltan:
            p.append(f"faltan {len(faltan)} de las {len(fotos)} fotos que nombra avatar.json (no estan en la carpeta AVATAR)")
    for campo in CAMPOS_EN_INGLES:
        v = str(j.get(campo) or "").strip()
        if len(v) < 8:
            p.append(f"«{campo}» esta vacio (va en ingles; se copia identico en todos los prompts)")
    if _empieza_con(str(j.get("aspecto") or ""), "apparent age, hair"):
        p.append("«aspecto» sigue siendo el texto de ejemplo")
    if _empieza_con(str(j.get("casa_base") or ""), "el lugar donde graba"):
        p.append("«casa_base» sigue siendo el texto de ejemplo")
    if str(j.get("genero") or "mujer") not in ("mujer", "hombre"):
        p.append("«genero» es mujer u hombre")
    return p


def pronombres(genero: str) -> tuple[str, str, str]:
    """(persona, ella/el, su) en ingles: las plantillas del editor estan escritas para mujer; con un avatar hombre se cambia woman/she/her por man/he/his."""
    return ("man", "he", "his") if genero == "hombre" else ("woman", "she", "her")


# ------------------------------------------------------------------ partir el guion en clips
def palabras_de(texto: str) -> int:
    """Las palabras como las cuenta la revision de los prompts (revisar_prompts.py: solo las que tienen letras; una raya «—» suelta no cuenta), para que un clip de 25 aqui no
    sea uno de 24 alla."""
    return RP.palabras(texto)


def partir_en_clips(frases: list[str], n: int | None = None) -> list[str]:
    """Agrupa FRASES ENTERAS, en orden, en clips de 25 a 30 palabras (la skill: de 25 a 30 por clip de 8 s y nunca se corta una frase a la mitad). Entre las formas posibles elige la
    de clips mas parejos (cerca de 27,5 palabras); con `n`, exactamente n clips. Si no hay ninguna forma, ErrorAvatar con las palabras de cada frase: hay que reescribir el guion
    (revisar_prompts.py no deja pagar un clip fuera de ese rango)."""
    limpias = [re.sub(r"\s+", " ", str(f)).strip() for f in frases if str(f).strip()]
    if not limpias:
        raise ErrorAvatar(2, "El guion no tiene frases.")
    cuentas = [palabras_de(f) for f in limpias]
    for f, k in zip(limpias, cuentas):
        if k > PALABRAS_POR_CLIP:
            raise ErrorAvatar(1, f"Una frase del guion tiene {k} palabras y un clip de 8 s admite {PALABRAS_POR_CLIP}: «{f[:70]}…». Acortala (el guion de avatar pide frases de hasta 22 palabras).")
    # mejor[i][c] = (desvio, donde empieza el ultimo clip) para las primeras i frases en c clips. Determinista: ante empate gana la primera forma encontrada.
    total = len(limpias)
    max_clips = total if n is None else int(n)
    infinito = float("inf")
    mejor = [[(infinito, -1)] * (max_clips + 1) for _ in range(total + 1)]
    mejor[0][0] = (0.0, -1)
    for i in range(1, total + 1):
        suma = 0
        for j in range(i - 1, -1, -1):
            suma += cuentas[j]
            if suma > PALABRAS_POR_CLIP:
                break
            if suma < PALABRAS_MIN_POR_CLIP:
                continue
            for c in range(1, max_clips + 1):
                previo = mejor[j][c - 1][0]
                if previo < infinito and previo + (suma - PALABRAS_IDEAL_CLIP) ** 2 < mejor[i][c][0] - 1e-9:
                    mejor[i][c] = (previo + (suma - PALABRAS_IDEAL_CLIP) ** 2, j)
    candidatos = [c for c in range(1, max_clips + 1) if mejor[total][c][0] < infinito and (n is None or c == n)]
    if not candidatos:
        lista = ", ".join(str(k) for k in cuentas)
        que = f"exactamente {n} clips" if n is not None else "clips"
        raise ErrorAvatar(1, f"El guion no se puede partir en {que} de {PALABRAS_MIN_POR_CLIP} a {PALABRAS_POR_CLIP} palabras con frases enteras (sus frases tienen {lista} palabras, "
                             f"{sum(cuentas)} en total). Cada clip de 8 s lleva de {PALABRAS_MIN_POR_CLIP} a {PALABRAS_POR_CLIP} palabras y nunca corta una frase: reescribe el guion "
                             "(`guion_auto.py <producto> <AD> --tipo ia --clips N`) o junta o parte frases a mano en guion.json.")
    c = min(candidatos, key=lambda k: (mejor[total][k][0], k))
    grupos: list[list[str]] = []
    i = total
    while c > 0:
        j = mejor[i][c][1]
        grupos.insert(0, limpias[j:i])
        i, c = j, c - 1
    return [" ".join(g) for g in grupos]


def texto_apto_para_decir(texto: str) -> list[str]:
    """Problemas de un texto que dira el avatar: cifras (se escriben en letras: el modelo de voz las lee mal), enlaces o comillas dobles dentro del dialogo."""
    p: list[str] = []
    if re.search(r"\d", texto):
        p.append("tiene cifras: escribelas en letras («cincuenta por ciento», «veinte mil pesos»)")
    if RE_ENLACE.search(texto):
        p.append("tiene un enlace, un usuario o un telefono: no se dice en voz alta")
    return p


# ------------------------------------------------------------------ las escenas que escribe el modelo barato
def _norm(texto: str) -> str:
    return re.sub(r"\s+", " ", str(texto)).strip()


def validar_escenas(obj, n: int, hay_producto: bool, prohibidos: list[str] | None = None) -> list[str]:
    """Motivos por los que el plan de escenas del modelo no sirve (lista vacia = sirve). Las reglas de references/prompts-ia.md que se pueden comprobar con codigo."""
    if not isinstance(obj, dict):
        return ["la respuesta no es un objeto JSON"]
    m: list[str] = []
    ropa = obj.get("ropa")
    if not isinstance(ropa, str) or not 8 <= len(ropa.strip()) <= 220:
        m.append("«ropa» tiene que ser una descripcion de una prenda o dos, en ingles (8 a 220 letras)")
    clips = obj.get("clips")
    if not isinstance(clips, list) or len(clips) != n:
        return m + [f"«clips» tiene que traer exactamente {n} elementos, uno por linea recibida, en el mismo orden"]
    previas: list[str] = []
    for i, c in enumerate(clips, 1):
        if not isinstance(c, dict):
            m.append(f"clip {i}: no es un objeto")
            continue
        escena, accion = c.get("escena"), c.get("accion")
        if not isinstance(escena, str) or not 12 <= len(escena.split()) <= 90:
            m.append(f"clip {i}: «escena» tiene que ser un texto en ingles de 15 a 70 palabras")
            escena = escena if isinstance(escena, str) else ""
        if not isinstance(accion, str) or not 3 <= len(accion.split()) <= 30:
            m.append(f"clip {i}: «accion» tiene que ser una accion corta en ingles (3 a 25 palabras)")
            accion = accion if isinstance(accion, str) else ""
        if not isinstance(c.get("usa_producto"), bool):
            m.append(f"clip {i}: «usa_producto» es true o false")
        elif c["usa_producto"] and not hay_producto:
            m.append(f"clip {i}: «usa_producto» es true pero no hay fotos del producto (nunca se genera solo con texto)")
        for campo, texto in (("escena", escena), ("accion", accion)):
            if PROHIBIDAS_EN_PROMPT.search(texto):
                m.append(f"clip {i}: «{campo}» usa palabras que delatan a la IA (cinematic, 4K, studio lighting, bokeh): el aspecto de video casero lo pone el codigo")
            if RE_ENLACE.search(texto):
                m.append(f"clip {i}: «{campo}» trae un enlace, un usuario o un telefono")
            for nombre in prohibidos or []:
                if nombre and nombre.lower() in texto.lower():
                    m.append(f"clip {i}: «{campo}» nombra a «{nombre}», que no se menciona")
        if previas and escena and difflib.SequenceMatcher(None, _norm(previas[-1]).lower(), _norm(escena).lower()).ratio() > 0.92:
            m.append(f"clip {i}: la escena es casi igual a la del clip anterior (cada clip cambia de lugar o de encuadre)")
        previas.append(escena)
    if isinstance(ropa, str) and PROHIBIDAS_EN_PROMPT.search(ropa):
        m.append("«ropa» usa palabras que delatan a la IA")
    return m


# ------------------------------------------------------------------ los prompts finales (plantillas 1, 2 y 3 de references/prompts-ia.md)
ESTILO_CUADRO = ("Candid still from a home video filmed on a phone, real UGC look, {sujeto} is in the middle of doing something, not posing, natural light, real skin texture, "
                 "no beauty filter, slightly imperfect framing.")
SIN_TEXTO = "No text, no readable brand names, no logos, no watermark."


def prompt_cuadro(clip: dict, plan: dict, avatar: dict, hay_ancla: bool, producto: str | None, solo_portada: bool) -> str:
    """El prompt del cuadro de un clip. clip: {n, escena, usa_producto}; plan: {ropa}; avatar: {genero, casa_base}; producto: `descripcion_ia` (o None)."""
    persona, ella, su = pronombres(avatar.get("genero", "mujer"))
    if clip["n"] == 1 or not hay_ancla:           # PLANTILLA 1: el cuadro ancla (referencia: la foto del avatar)
        partes = [f"Same {persona} as the reference photo: keep {su} face, skin tone, eyes, hair and body exactly the same.",
                  f"{ella.capitalize()} wears {_norm(plan['ropa']).rstrip('. ')}. Place: {_norm(avatar['casa_base']).rstrip('. ')}.",
                  f"Scene: {_norm(clip['escena']).rstrip('. ')}."]
    else:                                         # PLANTILLA 2: los demas (referencias: foto del avatar + cuadro ancla + paginas del producto)
        partes = [f"Same {persona} as the reference photos: keep {su} face, skin tone, eyes, hair and body exactly the same, wearing exactly the same outfit as in the second reference, same hairstyle, same home.",
                  f"Scene: {_norm(clip['escena']).rstrip('. ')}."]
    if clip.get("usa_producto") and (solo_portada or producto):
        partes.append("The ebook cover is shown on a phone screen: reproduce the reference cover exactly as it is." if solo_portada else f"Product: {_norm(producto or '').rstrip('. ')}.")
    partes += [ESTILO_CUADRO.format(sujeto=ella), SIN_TEXTO]
    return " ".join(partes)


def prompt_clip(clip: dict, avatar: dict) -> str:
    """PLANTILLA 3: video de una toma a partir del cuadro, con el dialogo entre comillas y los bloques fijos (VOICE y ACCENT identicos en todos los clips)."""
    _, ella, su = pronombres(avatar.get("genero", "mujer"))
    dialogo = _norm(clip["texto"]).replace('"', "'")
    return (f"(00:00-00:01) {_norm(clip['accion']).rstrip('. ')}. {ella.capitalize()} looks into the lens and says, warmly and naturally, with small hand gestures: \"{dialogo}\" (Spanish, lip-synced). "
            f"{ella.capitalize()} finishes about half a second before the end and holds a soft smile.\n"
            f"UGC LOOK: real home video filmed on a phone, slight natural handheld sway, natural light, real skin texture, no beauty filter, natural blinks and breathing, {ella} keeps doing the action "
            f"with {su} hands while {ella} talks and glances between {su} hands and the camera.\n"
            f"STABLE OBJECT CONSISTENCY: whatever product or object is being held or shown must keep its shape, color and content 100% consistent throughout the clip, never morphing or deforming. "
            f"This only protects the object: {su} body movement, gestures and speech continue as described.\n"
            f"VOICE: {_norm(avatar['voz']).rstrip('. ')}. The voice is exactly the same in the whole clip.\n"
            f"ACCENT: {_norm(avatar['acento']).rstrip('. ')}.\n"
            f"PACE: natural, slightly brisk conversational pace, {ella} starts speaking right away; after the last word {ella} keeps doing the action silently with {su} mouth closed.\n"
            f"RESTRICTIONS: lip-synced dialogue only in Spanish, natural room tone, no background music, no captions or on-screen text, no extra words beyond the quoted lines.")


# ------------------------------------------------------------------ medir un clip y un cuadro
def _palabras_planas(texto: str) -> list[str]:
    t = unicodedata.normalize("NFKD", str(texto).lower())
    t = "".join(c for c in t if unicodedata.category(c) not in ("Mn", "Cf"))
    return re.findall(r"[a-z0-9]+", t)


def revisar_clip(texto_esperado: str, palabras_oidas: list[str] | None) -> dict:
    """Compara lo que Whisper oyo en el clip con el texto que debia decir. ok = coincide al menos el 85 %, no sobran muchas palabras y la ultima palabra no quedo cortada.
    Sin palabras oidas (Whisper no pudo) = «sin_verificar»: no se rechaza ni se aprueba."""
    esperado = _palabras_planas(texto_esperado)
    if palabras_oidas is None:
        return {"ok": None, "sin_verificar": True, "cobertura": None, "sobran": None, "ultima_palabra": None, "detalle": "no se pudo transcribir el clip"}
    oido = _palabras_planas(" ".join(palabras_oidas))
    sm = difflib.SequenceMatcher(None, esperado, oido, autojunk=False)
    iguales = sum(b.size for b in sm.get_matching_blocks())
    cobertura = iguales / len(esperado) if esperado else 0.0
    sobran = max(0, len(oido) - iguales)
    ultima = bool(esperado) and esperado[-1] in oido[-3:]
    ok = cobertura >= COBERTURA_MINIMA and sobran <= max(4, int(0.2 * len(esperado))) and ultima
    detalle = f"dice el {cobertura * 100:.0f} % del texto" + (f", sobran {sobran} palabras" if sobran else "") + ("" if ultima else ", la ultima palabra no se oye")
    return {"ok": ok, "sin_verificar": False, "cobertura": round(cobertura, 3), "sobran": sobran, "ultima_palabra": ultima, "detalle": detalle}


def cuadro_malo(v, es_ancla: bool) -> list[str]:
    """Por que un cuadro no sirve segun el modelo de vision (lista vacia = sirve o no se pudo saber). Es un aviso barato, no una garantia: la cara y la ropa las juzga al final una persona."""
    if not isinstance(v, dict):
        return []
    m: list[str] = []
    if v.get("misma_persona") is False:
        m.append("no parece la misma persona que la foto del avatar")
    if not es_ancla and v.get("ropa_igual_que_el_ancla") is False:
        m.append("la ropa cambio respecto al cuadro ancla")
    if v.get("texto_o_logos_visibles") is True:
        m.append("se ve texto o un logo")
    if v.get("una_sola_persona") is False:
        m.append("aparece mas de una persona")
    if v.get("manos_o_cara_deformes") is True:
        m.append("manos o cara deformes")
    if v.get("producto_deformado") is True:
        m.append("el producto sale deformado")
    return m


def resumen_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
