"""Revisa cada prompt de imagen y de clip ANTES de pagarlo (orden del dueño, 2026-11-10: cada anuncio por menos de US$ 0,50 y nunca se regenera nada).

Un clip sale mal casi siempre porque el prompt no fue especifico. Esta revision no depende del cuidado de quien escribe el prompt: es una regla del programa.
  - apimart.py la corre en `imagen` y `video` antes de subir o pedir nada (tambien con --dry-run): si el prompt no cumple, sale con la lista de lo que falta y no gasta nada.
  - gancho_ia.py y avatar_auto.py la corren sobre los prompts que arman: lo que falla se le devuelve al modelo barato para que reescriba, y el anuncio entero se revisa antes del
    primer pago (no se paga el cuadro 1 de un anuncio cuyo clip 3 no cumple).
  - el dash repite las mismas reglas antes de cobrar (functions/_revisar-prompts.js): un PC con un apimart.py viejo o editado a mano no puede saltarselas.
Las dos copias (esta y la del dash) se prueban con los mismos casos: casos_revisar_prompts.json (autoprueba_revisar_prompts.py y scripts/probar-revisar-prompts.mjs).

Las reglas son las de .claude/skills/editor-video/references/prompts-ia.md y de preferencias.md (sin texto en la imagen, sin dialogo en el b-roll, manos quietas):
  IMAGEN (cuadro): lugar, angulo de camara, encuadre, accion en curso con objetos concretos y expresion. Si no sale ninguna cara («Nobody's face is visible») no hace falta la
          expresion; si no sale nadie («No people in the shot») bastan lugar, angulo y encuadre. Lleva «No text…»; si muestra el producto, va con sus fotos de referencia; si nombra
          la foto de referencia («the second reference»), esa foto va.
  CLIP CON DIALOGO (plantillas 3 y 4): de 25 a 30 palabras por cada 8 s (de 12 a 15 por toma en la de dos tomas), que terminen en fin de frase, sin cifras ni simbolos, con los
          bloques fijos UGC LOOK, STABLE OBJECT CONSISTENCY, VOICE, ACCENT, PACE y RESTRICTIONS, y VOICE y ACCENT identicos en todos los clips del anuncio.
  CLIP SIN DIALOGO (plantilla 5): CAMERA, MICRO-ACTION, STABLE OBJECT CONSISTENCY y RESTRICTIONS, que diga que va en silencio.
  TODOS: en ingles (solo el dialogo, entre comillas, en español); sin cinematic, 4K, studio lighting, bokeh ni parecidas; sin pedir texto legible ni palabras entre comillas fuera
          del dialogo (el modelo las dibuja como subtitulos); sin manos escribiendo (la IA las deforma: la mano queda quieta); con «No text» (imagen) o «no captions» (clip).
Es determinista: listas de palabras y conteos, sin modelos ni red. Uso a mano (no gasta nada; sale con 0 si cumple y con 1 si no):
  python scripts/revisar_prompts.py imagen cuadros/02.txt [--ref foto.jpg --ref cuadros/01.png ...] [--producto p]
  python scripts/revisar_prompts.py video clips/02.txt [--duracion 8] [--carpeta clips]
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import unicodedata
from pathlib import Path

REGLAS_VERSION = 1
# El prompt mas largo que se revisa (el dash no acepta mas para una imagen o un clip: LIMITES.prompt de functions/_editor-apimart.js). Las plantillas llegan a ~1.500 letras.
MAX_LETRAS_PROMPT = 4000
PLANTILLAS = ".claude/skills/editor-video/references/prompts-ia.md"

# ------------------------------------------------------------------ vocabulario (el dash tiene las mismas listas: functions/_revisar-prompts.js)
PALABRAS_IA = re.compile(r"\b(cinemat\w*|4k|8k|uhd|studio[- ]light\w*|bokeh|shallow depth of field|hdr|dslr|film grain|photoshoot|professional photo\w*)\b", re.I)
NEGACION = re.compile(r"\b(no|not|without|never|zero|avoid|nothing|none|nor|don't|doesn't|don’t|doesn’t)\b", re.I)
CORTES = ",.;:!?\n()[]"
VENTANA_CLAUSULA = 160
LUGAR = re.compile(r"\b(kitchen|living[- ]room|bedroom|dining[- ]room|dining table|table|tabletop|countertop|counter|desk|workbench|sofa|couch|armchair|bed|garden|backyard|yard|"
                   r"patio|balcony|porch|terrace|rooftop|workshop|office|bathroom|hallway|corridor|doorway|entrance|windowsill|laundry|garage|stove|sink|fridge|"
                   r"refrigerator|oven|pantry|market|store|shop|street|park|car|classroom|school|church|cafe|restaurant|bakery|salon|gym|floor|rug|carpet|shelf|bookshelf|"
                   r"nursery|crib|playroom|stairs|staircase|farm|field|beach|apartment|house|room)\b", re.I)
# «Place: algo de dos palabras o mas»; la primera palabra acotada a 60 letras (sin eso, «place:place:place:…» sin espacios crece al cuadrado)
LUGAR_ETIQUETA = re.compile(r"\bplace\s*:\s*[^\s.]{1,60}[ \t]+[^\s.]", re.I)
ANGULO = re.compile(r"\b(three[- ]quarters?|3/4|side (view|angle|profile)|from the side|in profile|profile view|low[- ]angle|high[- ]angle|eye[- ]level|overhead|top[- ]down|"
                    r"from above|from below|top view|flat[- ]lay|aerial view|bird'?s[- ]eye|over[- ]the[- ]shoulder|front view|frontal|straight[- ]on|head[- ]on|from behind|rear view|dutch angle|angle|angled|"
                    r"point of view|pov|first[- ]person)\b", re.I)
ENCUADRE = re.compile(r"\b(extreme close[- ]?up|medium close[- ]?up|close[- ]?up|closeup|close shot|medium shot|mid[- ]shot|medium[- ]wide( shot)?|wide shot|full shot|full[- ]body|"
                      r"long shot|waist[- ]up|chest[- ]up|shoulders[- ]up|head[- ]and[- ]shoulders|knees?[- ]up|half[- ]body|tight shot|macro( shot)?|detail shot|insert shot|"
                      r"establishing shot|two[- ]shot|framed from (the )?(waist|chest|knees|shoulders)|from the (waist|chest|knees) up)\b", re.I)
EXPRESION = re.compile(r"\b(smil\w*|grin|grins|grinning|laugh\w*|chuckl\w*|giggl\w*|focused|concentrat\w*|surprised|amazed|astonished|curious|excited|proud|relaxed|calm|serious|"
                       r"frown\w*|worried|tired|exhausted|frustrated|annoyed|happy|joyful|cheerful|amused|confident|thoughtful|satisfied|relieved|delighted|pleased|determined|"
                       r"skeptical|sceptical|shy|embarrassed|nervous|hopeful|tender|playful|enthusiastic|expression|eyebrows?|wink\w*|smirk\w*|sad|upset|stressed|overwhelmed)\b", re.I)
SIN_PERSONAS = re.compile(r"\b(no (people|person|humans?|one)|nobody (is )?in (the )?(shot|frame|scene|picture)|without (people|anyone|any person|a person|humans))\b", re.I)
SIN_CARA = re.compile(r"(\bnobody['’]?s face is visible|\bno faces?\b|\bfaces? (is |are )?not visible|\bface (is )?out of (the )?frame|\bhands only\b|"
                      r"\bonly (her|his|their|the) hands\b|\bjust (her|his|their|the) hands\b|\bwithout (showing )?(her|his|their|a|any) face\b)", re.I)
PRODUCTO = re.compile(r"(\bproduct\s*:|\b(e-?books?|the product|our product|the guide|the booklet|the pdf|book cover|the cover|its cover|printed pages)\b)", re.I)
REFERENCIA = re.compile(r"\b(reference (photos?|images?|pictures?|cover)|(first|second|third|fourth) reference)\b", re.I)
NO_TEXT_IMAGEN = re.compile(r"\bno (readable )?(text|words|lettering)\b", re.I)
NO_TEXT_CLIP = re.compile(r"\bno (captions|subtitles|on-screen text|text)\b", re.I)
PIDE_TEXTO = re.compile(r"\b(text (that )?(says|reads|saying|reading)|(that|which) (says|reads)|with the (words?|text|phrase|title|name)|the words|lettering|captions?|subtitles?|"
                        r"headline|title text|on-screen text|text overlay|speech bubble|signs? (that|reading|with)|labels? (that|reading|with)|written|handwritten|handwriting|"
                        r"typography|fonts?|spell(s|ing)? out|brand names?|logos?|watermarks?|price tags?|readable)\b", re.I)
# «writing desk(s)», «writing-desk», «writing bureau(s)» o «writing table» son un mueble, no manos escribiendo (y en COMPUESTOS no cuentan como accion); «writing tables of …» si es escribir. Los espacios van
# explicitos (espacio, tabulador, salto de linea, espacio duro y guion): \s no cubre lo mismo en Python y en el dash (U+FEFF, U+0085...)
ESCRIBE = re.compile(r"\b(writ(e|es|ing)(?![ \t\n\u00a0-]+(?:desks?|bureaus?|table)\b)|handwrit\w*|typ(ing|es on)|texting|scribbl\w*|calligraph\w*|signing|draw(s|ing)? (letters|words|a letter|the letters))\b", re.I)
SILENCIO = re.compile(r"(absolute silence|zero speech|no speech|no dialogue|no talking|nobody speaks|no one speaks|lips stay closed|mouths? stays? closed|mouth never moves)", re.I)
COMPUESTOS = re.compile(r"\b(cutting board|chopping board|baking (sheet|tray|dish|paper|pan)|frying pan|mixing bowl|measuring (cups?|spoons?|tape)|rolling pin|"
                        r"sewing (machine|kit|box|room)|ironing board|dining (table|room|chair)|living room|washing machine|serving (bowl|plate|tray|spoon)|knitting needles?|"
                        r"wrapping paper|cooling rack|drawing (pad|book)|painting (brush|set)|ceiling (fan|light)|reading glasses|craft room|writing[ \t\n\u00a0-]+(desks?|bureaus?|table))\b", re.I)
ETIQUETA = re.compile(r"(UGC LOOK|STABLE OBJECT CONSISTENCY|VOICE|ACCENT LOCK|ACCENT|PACE|RESTRICTIONS|CAMERA|MICRO-ACTION)\s*:")
SHOT_1 = re.compile(r"\bSHOT 1\b")
SHOT_2 = re.compile(r"\bSHOT 2\b")
# las comillas dobles del dialogo y su cierre (se buscan en una pasada en dialogos(): un regex con miles de “ sin cerrar crecia al cuadrado)
PARES_COMILLAS = {'"': '"', "“": "”", "«": "»"}
SIMPLES = re.compile(r"(?:^|[^A-Za-z0-9])['‘]([^'’\n]{1,120})['’](?![A-Za-z0-9])")
# Una palabra = letras (categoria Unicode L, como \p{L} del dash). [^\W\d_] solo deja letras despues de _sin_numeros(): sin eso, ⅓, ², ⑩ o Ⅻ (numericos que no son \d) contaban
# como letras aqui y no en el dash.
PALABRA = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
# Cifra en el dialogo: todo caracter numerico (categoria Unicode N: 0-9, ½, ⅓, ², ⑩, Ⅻ, １…; el dash usa \p{N}) y estos simbolos.
SIMBOLOS_CIFRA = frozenset("%$#@&+=€£/\\_*")
TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?")
IDIOMA = re.compile(r"[a-záéíóúüñ]+")

INGLES = frozenset("the and with her his their of on in at is are she he while from into to it its this that holding same as".split())
ESPANOL = frozenset("el la los las de del que con una un para por en su sus está esta mientras sobre y se al lo como muy pero mujer hombre cocina mesa manos".split())
NO_ACCION = frozenset("""doing posing wearing framing lighting something nothing anything everything morning evening ceiling clothing building during according standing sitting
lying leaning kneeling smiling looking facing being having feeling waiting thinking talking speaking saying glancing blinking breathing shining bring string spring thing king
sing wing swing sling sting icing stuffing dressing frosting topping seasoning amazing interesting charming stunning relaxing loving caring living dining pudding wedding railing
flooring padding bedding packaging matching missing going coming listening watching""".split())
NO_ACCION_3P = frozenset("wears looks smiles stands sits has is was does seems appears faces poses keeps gets says talks speaks lies leans waits thinks feels".split())
SUJETOS = frozenset("she he they hands hand fingers woman man person mom mother".split())
FUNCION = frozenset("""the a an and or but with her his their its our your my me him them of on in into onto at to from for by over under up down out off around through across
along about above below behind beside next near between while as then than very so too just still also again some any more most much many this that these those it there here now
away together both each every one two three four five few several little small big large new fresh old same other another own such what which who whom whose where when how all is
are was be been being has have had do does did will would can could should may might must not no slowly carefully gently quickly happily calmly softly lightly slightly firmly
proudly neatly closely briefly really nearly barely simply warmly naturally casually right left""".split())
VAGO = frozenset("something anything everything nothing stuff things thing items item objects object whatever someone somebody".split())
NEGACION_TOKENS = frozenset("no not without never zero avoid nothing none nor don't doesn't".split())
# Palabras que solo son un objeto despues de her/his/their: «stretching her back» es una accion; «stepping back» no tiene objeto.
SOLO_TRAS_POSESIVO = frozenset(["back"])
POSESIVOS = frozenset("her his their".split())
BLOQUES_DIALOGO = ("UGC LOOK", "STABLE OBJECT CONSISTENCY", "VOICE", "ACCENT", "PACE", "RESTRICTIONS")
BLOQUES_SILENCIO = ("CAMERA", "MICRO-ACTION", "STABLE OBJECT CONSISTENCY", "RESTRICTIONS")
BLOQUES_VOZ = ("VOICE", "ACCENT", "ACCENT LOCK")


# ------------------------------------------------------------------ utilidades (las mismas en el dash)
def _nfc(texto) -> str:
    return unicodedata.normalize("NFC", str(texto or "")).replace("\r\n", "\n").replace("\r", "\n")


def _inicios_de_clausula(texto: str) -> list[int]:
    """inicio[i] = donde empieza la clausula que contiene la posicion i (justo despues de la ultima coma, punto, dos puntos, salto o parentesis antes de i). Una pasada."""
    inicio = [0] * (len(texto) + 1)
    desde = 0
    for i, c in enumerate(texto):
        inicio[i] = desde
        if c in CORTES:
            desde = i + 1
    inicio[len(texto)] = desde
    return inicio


def _sin_negar(regex: re.Pattern, texto: str) -> list[str]:
    """Lo que encuentra `regex` en `texto` y NO esta negado en su clausula («no text», «without logos», «no captions or on-screen text»). La negacion se busca en lo que va de la
    clausula, hasta VENTANA_CLAUSULA letras atras: asi un prompt enorme sin comas no cuesta tiempo cuadratico (el dash tiene ~10 ms de CPU por pedido)."""
    inicios = _inicios_de_clausula(texto)
    salida = []
    for m in regex.finditer(texto):
        desde = max(inicios[m.start()], m.start() - VENTANA_CLAUSULA)
        if not NEGACION.search(texto[desde:m.start()]):
            salida.append(m.group(0))
    return salida


def _es_numero(c: str) -> bool:
    return unicodedata.category(c)[0] == "N"


def _sin_numeros(t: str) -> str:
    """t con cada caracter numerico (categoria N) cambiado por un espacio (mismas posiciones)."""
    return "".join(" " if _es_numero(c) else c for c in t)


def _palabras(texto: str) -> int:
    return len(PALABRA.findall(_sin_numeros(_nfc(texto))))


def _cifras(texto: str) -> set[str]:
    """Los caracteres de `texto` que el dialogo no puede llevar: los numericos (categoria N) y SIMBOLOS_CIFRA."""
    return {c for c in texto if c in SIMBOLOS_CIFRA or _es_numero(c)}


def palabras(texto: str) -> int:
    """Cuantas palabras cuenta la revision en un dialogo (solo las que tienen letras). avatar_comun.py parte el guion con esta misma cuenta."""
    return _palabras(texto)


def _junta(texto: str) -> str:
    return " ".join(str(texto).split())


def _unicos(xs: list[str], n: int = 3) -> str:
    vistos: list[str] = []
    for x in xs:
        x = _junta(x).lower()
        if x not in vistos:
            vistos.append(x)
    return ", ".join("«" + x + "»" for x in vistos[:n])


def dialogos(texto: str) -> tuple[list[tuple[int, int, str]], bool]:
    """([(inicio, fin, lo de adentro)], comillas_bien_cerradas) de lo que va entre comillas dobles: "…", “…” o «…»."""
    t = _nfc(texto)
    cerradas = t.count('"') % 2 == 0 and t.count("“") == t.count("”") and t.count("«") == t.count("»")
    return _entre_comillas(t), cerradas


def _entre_comillas(t: str) -> list[tuple[int, int, str]]:
    """[(inicio, fin, lo de adentro)] de lo que va entre comillas dobles, de izquierda a derecha y sin anidar: lo mismo que re.finditer(r'"([^"]*)"|“([^”]*)”|«([^»]*)»'), pero
    lineal (si una comilla de un tipo no tiene cierre, ninguna de ese tipo mas adelante lo tiene: se deja de buscar ese tipo)."""
    segs: list[tuple[int, int, str]] = []
    sin_cierre: set[str] = set()
    i = 0
    while i < len(t):
        c = t[i]
        cierre = PARES_COMILLAS.get(c)
        if cierre is not None and c not in sin_cierre:
            j = t.find(cierre, i + 1)
            if j != -1:
                segs.append((i, j + 1, t[i + 1:j]))
                i = j + 1
                continue
            sin_cierre.add(c)
        i += 1
    return segs


def _sin_dialogo(t: str, segs: list[tuple[int, int, str]]) -> str:
    """El prompt (ya en NFC) con lo que va entre comillas dobles cambiado por espacios (mismas posiciones). Una pasada."""
    if not segs:
        return t
    partes, p = [], 0
    for a, b, _x in segs:
        partes += [t[p:a], " " * (b - a)]
        p = b
    partes.append(t[p:])
    return "".join(partes)


def fuera_del_dialogo(texto: str) -> str:
    t = _nfc(texto)
    return _sin_dialogo(t, dialogos(t)[0])


def bloques(texto: str) -> dict[str, str]:
    return _bloques_de(fuera_del_dialogo(texto))


def _bloques_de(t: str) -> dict[str, str]:
    """Los bloques con etiqueta (VOICE:, ACCENT:, RESTRICTIONS:…) y su contenido, de un texto ya sin dialogo. Si una etiqueta se repite, vale la primera."""
    marcas = [m for m in ETIQUETA.finditer(t) if m.start() == 0 or not re.match(r"[A-Za-z-]", t[m.start() - 1])]
    salida: dict[str, str] = {}
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(t)
        if m.group(1) not in salida:
            salida[m.group(1)] = _junta(t[m.end():fin])
    return salida


def bloques_voz(texto: str) -> dict[str, str]:
    """VOICE, ACCENT y ACCENT LOCK (los que tenga) de un prompt de clip con dialogo: tienen que ir identicos, palabra por palabra, en todos los clips del anuncio."""
    return _voz_de(bloques(texto))


def _voz_de(b: dict[str, str]) -> dict[str, str]:
    return {k: b[k] for k in BLOQUES_VOZ if k in b}


def rango_palabras(duracion: float, minimo: int = 25, maximo: int = 30) -> tuple[int, int]:
    """Las palabras que caben en un clip: de 25 a 30 por cada 8 s (prompts-ia.md, regla 1). 6 s: 19 a 22; 8 s: 25 a 30; 10 s: 32 a 37."""
    d = float(duracion)
    return math.ceil(minimo * d / 8 - 1e-9), math.floor(maximo * d / 8 + 1e-9)


def _es_objeto(tokens: list[str], j: int) -> bool:
    x = tokens[j]
    return len(x) >= 3 and x not in FUNCION and x not in VAGO and (x not in SOLO_TRAS_POSESIVO or tokens[j - 1] in POSESIVOS)


def _hay_accion(texto: str) -> bool:
    """Un verbo de accion (en -ing, o en presente despues de she/he/hands…) seguido, en la misma clausula, de un objeto concreto: «pouring milk into a mug», «she slices bread»."""
    t = COMPUESTOS.sub(lambda m: re.split(r"[ \t\n\u00a0-]+", m.group(0))[-1], texto.lower())
    for clausula in re.split(r"[,.;:!?\n()\[\]]", t):
        tokens = TOKEN.findall(clausula)
        negada = False                     # ya aparecio un «no», «not», «without»… antes en la clausula
        for i, tk in enumerate(tokens):
            es_verbo = (len(tk) >= 5 and tk.endswith("ing") and tk not in NO_ACCION) or \
                       (i > 0 and tokens[i - 1] in SUJETOS and len(tk) >= 4 and tk.endswith("s") and tk not in NO_ACCION_3P and tk not in FUNCION)
            if es_verbo and not negada and any(_es_objeto(tokens, j) for j in range(i + 1, min(i + 9, len(tokens)))):
                return True
            negada = negada or tk in NEGACION_TOKENS
    return False


def _comunes(fuera: str, cuerpo: str, problemas: list[tuple[str, str]]) -> None:
    """Las reglas de imagen y de clip: idioma, palabras de IA, texto pedido, manos escribiendo y comillas simples fuera del dialogo. `fuera` = sin dialogo; `cuerpo` = todo."""
    tokens = IDIOMA.findall(fuera.lower())
    es = sum(1 for x in tokens if x in ESPANOL)
    en = sum(1 for x in tokens if x in INGLES)
    if es >= 6 and es > en:
        problemas.append(("idioma", "parece escrito en español: el prompt va en INGLÉS (solo el diálogo, entre comillas, va en español)"))
    ia = [m.group(0) for m in PALABRAS_IA.finditer(cuerpo)]
    if ia:
        problemas.append(("palabras_ia", f"usa palabras que delatan la IA ({_unicos(ia)}): quítalas; el aspecto de video casero grabado con celular ya va en la plantilla"))
    simples = [m.group(1) for m in SIMPLES.finditer(fuera)]
    if simples:
        problemas.append(("comillas", f"tiene palabras entre comillas fuera del diálogo ({_unicos(simples)}): el modelo las dibuja como subtítulos; quita las comillas"))
    pide = _sin_negar(PIDE_TEXTO, fuera)
    if pide:
        problemas.append(("texto", f"pide texto legible ({_unicos(pide)}): la IA dibuja mal las letras y se ve falso; nada de letreros, etiquetas, títulos, logos ni palabras escritas"))
    escribe = _sin_negar(ESCRIBE, fuera)
    if escribe:
        problemas.append(("escribir", f"pide manos escribiendo ({_unicos(escribe)}): la IA deforma las manos que escriben; deja la mano quieta (por ejemplo «holding a pen still above the "
                                      "open notebook»)"))


# ------------------------------------------------------------------ imagen
def fotos_de_producto(producto_dir: Path | None) -> set[str] | None:
    """Los nombres (sin extension) de las fotos del producto en public/productos/<p>/: portada y las paginas de paginas/. None si la carpeta no existe."""
    if producto_dir is None or not Path(producto_dir).is_dir():
        return None
    d = Path(producto_dir)
    nombres = {p.stem.lower() for p in d.glob("portada.*")}
    if (d / "paginas").is_dir():
        nombres |= {p.stem.lower() for p in (d / "paginas").iterdir() if p.is_file()}
    return nombres


def es_foto_de_producto(ref: str, producto_dir: Path | None, nombres: set[str] | None) -> bool | None:
    """True si la referencia es una foto del producto: un archivo dentro de public/productos/<p>/ (fuera de avatares/), o la copia achicada que hacen gancho_ia.py y avatar_auto.py
    (ref_<nombre>.jpg). None si no se puede saber (una direccion https, o el producto no esta en este equipo)."""
    if nombres is None or str(ref).startswith(("https://", "http://")):
        return None
    p = Path(ref)
    try:
        dentro = p.resolve().relative_to(Path(producto_dir).resolve())
        if dentro.parts and dentro.parts[0].lower() != "avatares":
            return True
    except (ValueError, OSError):
        pass
    tallo = p.stem.lower()
    return tallo.startswith("ref_") and tallo[4:] in nombres


def _largo(cuerpo: str) -> tuple[str, str]:
    return ("largo", f"el prompt tiene {len(cuerpo)} letras y el máximo es {MAX_LETRAS_PROMPT} (las plantillas llegan a unas 1.500): acórtalo")


def revisar_imagen(prompt: str, refs: list[str] | int = 0, producto_dir: Path | None = None) -> list[tuple[str, str]]:
    """[(codigo, que falta)] del prompt de un cuadro; [] = cumple. `refs`: las referencias (rutas o direcciones) o cuantas son. Con `producto_dir` (public/productos/<p>) y rutas
    locales se comprueba ademas que, si sale el producto, una de las referencias sea una foto del producto (en el dash solo se cuenta que haya referencias)."""
    cuerpo = _nfc(prompt)
    problemas: list[tuple[str, str]] = []
    if not cuerpo.strip():
        return [("vacio", "el prompt esta vacio")]
    if len(cuerpo) > MAX_LETRAS_PROMPT:
        return [_largo(cuerpo)]
    segs, cerradas = dialogos(cuerpo)
    fuera = _sin_dialogo(cuerpo, segs)
    if segs or not cerradas:
        problemas.append(("comillas", "tiene texto entre comillas: en una imagen el modelo lo dibuja como letras; describe la escena sin comillas"))
    _comunes(fuera, cuerpo, problemas)
    if not NO_TEXT_IMAGEN.search(fuera):
        problemas.append(("sin_no_text", "le falta la frase de la plantilla «No text, no readable brand names, no logos, no watermark.»"))
    sin_personas = bool(SIN_PERSONAS.search(fuera))
    sin_cara = sin_personas or bool(SIN_CARA.search(fuera))
    if not (LUGAR_ETIQUETA.search(fuera) or _sin_negar(LUGAR, fuera)):
        problemas.append(("lugar", "no dice el LUGAR (dónde es: «in her small kitchen, next to the stove», o «Place: …» con objetos concretos)"))
    if not _sin_negar(ANGULO, fuera):
        problemas.append(("angulo", "no dice el ÁNGULO de cámara («three-quarter angle», «side view», «low angle», «from above», «eye level»)"))
    if not _sin_negar(ENCUADRE, fuera):
        problemas.append(("encuadre", "no dice el ENCUADRE («medium shot», «waist up», «close-up», «full body»)"))
    if not sin_personas and not _hay_accion(fuera):
        problemas.append(("accion", "no dice la ACCIÓN en curso con objetos concretos (qué está haciendo y con qué: «pouring hot chocolate into a red mug», «gluing felt pieces "
                                    "onto a paper template»); nunca posando"))
    if not sin_cara and not _sin_negar(EXPRESION, fuera):
        problemas.append(("expresion", "no dice la EXPRESIÓN de la cara («smiling at the camera», «focused», «surprised»). Si no sale ninguna cara, escribe «Nobody's face is "
                                       "visible»; si no sale nadie, «No people in the shot»"))
    n_refs = refs if isinstance(refs, int) else len(refs)
    pide_refs = 0
    for m in REFERENCIA.finditer(fuera):
        frase = m.group(0).lower()
        pide_refs = max(pide_refs, 4 if "fourth" in frase else 3 if "third" in frase else 2 if ("second" in frase or "photos" in frase or "images" in frase
                                                                                                 or "pictures" in frase) else 1)
    if pide_refs > n_refs:
        problemas.append(("referencias", f"nombra {pide_refs} foto(s) de referencia y van {n_refs}: pasa cada foto con --ref, en el orden de la plantilla (1.ª el avatar, 2.ª el cuadro "
                                         "ancla, después las del producto)"))
    if _sin_negar(PRODUCTO, fuera):
        if n_refs == 0:
            problemas.append(("producto_sin_ref", "muestra el producto pero no va ninguna foto de referencia: nunca se genera solo con texto (pasa con --ref la portada o las páginas "
                                                  "que producto.json marca como fotos del producto)"))
        elif not isinstance(refs, int) and producto_dir is not None:
            nombres = fotos_de_producto(producto_dir)
            vistas = [es_foto_de_producto(r, producto_dir, nombres) for r in refs]
            if vistas and all(v is False for v in vistas):
                problemas.append(("producto_sin_ref", "muestra el producto pero ninguna referencia es una foto del producto (la portada o una página de paginas/ de "
                                                      f"public/productos/{Path(producto_dir).name}/): nunca se genera solo con texto"))
    return problemas


# ------------------------------------------------------------------ clip
def revisar_video(prompt: str, duracion: float = 8, otros: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """[(codigo, que falta)] del prompt de un clip; [] = cumple. `otros`: [(nombre, prompt)] de los otros clips del mismo anuncio, para que VOICE y ACCENT sean identicos."""
    cuerpo = _nfc(prompt)
    problemas: list[tuple[str, str]] = []
    if not cuerpo.strip():
        return [("vacio", "el prompt esta vacio")]
    if len(cuerpo) > MAX_LETRAS_PROMPT:
        return [_largo(cuerpo)]
    segs, cerradas = dialogos(cuerpo)
    fuera = _sin_dialogo(cuerpo, segs)
    if not cerradas:
        problemas.append(("comillas", "tiene comillas sin cerrar: el diálogo va entero entre comillas dobles y no hay más comillas en el prompt"))
    _comunes(fuera, cuerpo, problemas)
    if not NO_TEXT_CLIP.search(fuera):
        problemas.append(("sin_no_captions", "le falta «no captions or on-screen text» en RESTRICTIONS (sin eso el modelo puede poner subtítulos)"))
    b = _bloques_de(fuera)
    if segs:
        dos_tomas = bool(SHOT_1.search(fuera) and SHOT_2.search(fuera))
        esperados = 2 if dos_tomas else 1
        if len(segs) != esperados:
            problemas.append(("comillas", f"tiene {len(segs)} textos entre comillas y la plantilla lleva {esperados} (el diálogo"
                                          f"{', uno por toma' if dos_tomas else ''}): el modelo dibuja como subtítulo lo que va entre comillas fuera del diálogo"))
        textos = [s[2] for s in segs]
        total = sum(_palabras(x) for x in textos)
        lo, hi = rango_palabras(duracion)
        if dos_tomas:
            lo_t, hi_t = rango_palabras(duracion, 12, 15)
            for i, x in enumerate(textos[:2], 1):
                n = _palabras(x)
                if not lo_t <= n <= hi_t:
                    problemas.append(("dialogo_largo", f"la toma {i} dice {n} palabras y cada toma de un clip de {duracion:g} s lleva de {lo_t} a {hi_t}"))
        if not lo <= total <= hi:
            problemas.append(("dialogo_largo", f"el diálogo tiene {total} palabras y un clip de {duracion:g} s lleva de {lo} a {hi} (con menos sobra tiempo y el modelo inventa o "
                                               "se queda callado; con más corta la frase)"))
        final = textos[-1].strip().rstrip("'’»\"” ").rstrip()
        if not final or final[-1] not in ".!?…":
            cola = " ".join(textos[-1].split()[-4:])
            problemas.append(("dialogo_fin", f"el diálogo no termina en fin de frase («…{cola}»): cada clip termina con punto, ? o ! y nunca corta una frase a la mitad"))
        cifras = sorted(set().union(*(_cifras(x) for x in textos)))
        if cifras:
            problemas.append(("dialogo_cifras", f"el diálogo tiene cifras o símbolos ({' '.join(cifras)}): escríbelos en letras («cincuenta por ciento», «veinte mil pesos»)"))
        faltan = [k for k in BLOQUES_DIALOGO if not b.get(k)]
        if faltan:
            problemas.append(("bloques", f"le faltan los bloques fijos {', '.join(faltan)} (BLOQUES FIJOS de {PLANTILLAS}: van al final de todo clip con diálogo)"))
        for k in ("VOICE", "ACCENT"):
            if b.get(k) and _palabras(b[k]) < 3:
                problemas.append(("bloques", f"el bloque {k} está casi vacío: describe la voz y el acento del avatar (sale de avatar.json y va igual en todos los clips)"))
        if not re.search(r"\bSpanish\b", fuera):
            problemas.append(("bloques", "no dice que el diálogo es en español (la plantilla: «(Spanish, lip-synced)» y «lip-synced dialogue only in Spanish»)"))
        mia = _voz_de(b)
        for nombre, otro in otros or []:
            suya = bloques_voz(otro)
            if not dialogos(otro)[0] or not suya:
                continue
            distintos = [k for k in BLOQUES_VOZ if mia.get(k, "") != suya.get(k, "")]
            if distintos:
                problemas.append(("voz_distinta", f"{' y '.join(distintos)} no {'es idéntico' if len(distintos) == 1 else 'son idénticos'} al de {nombre}: van palabra por palabra "
                                                  "iguales en todos los clips del anuncio (si cambian, cambia la voz o el acento a mitad del anuncio)"))
                break
    else:
        faltan = [k for k in BLOQUES_SILENCIO if not b.get(k)]
        if faltan:
            problemas.append(("bloques", f"un clip sin diálogo sigue la PLANTILLA 5 de {PLANTILLAS}: le faltan {', '.join(faltan)}"))
        if not SILENCIO.search(fuera):
            problemas.append(("silencio", "un clip sin diálogo dice que va en silencio («RESTRICTIONS: absolute silence for the entire clip: zero speech, zero voice-over…»); si "
                                          "lleva diálogo, va entre comillas dobles"))
        if b.get("MICRO-ACTION") is not None and _palabras(b["MICRO-ACTION"]) < 3:
            problemas.append(("bloques", "MICRO-ACTION está casi vacío: una sola acción lenta y concreta («slowly pours the warm milk into the mug»)"))
    return problemas


def mensaje(problemas: list[tuple[str, str]], que: str = "el prompt") -> str:
    """El texto para la persona (o para el modelo que reescribe): que le falta a `que`, una linea por problema."""
    return (f"{que} no cumple la plantilla de {PLANTILLAS} (revision automatica, antes de pagar):\n" + "\n".join(f"  - {m}" for _c, m in problemas))


SUFIJO_PROMPT = "_prompt.txt"
VUELTA_DE_CLIP = re.compile(r"_(?:v(\d+)|(b))$")


def clip_y_vuelta(nombre: str) -> tuple[str, int]:
    """El clip y su vuelta, por el nombre (con o sin extension o «_prompt.txt»): «03_v2» -> («03», 2); «03_b» (el reintento de avatar_auto) -> («03», 1); «03» -> («03», 0).
    Como baseArchivo del dash (functions/_editor-apimart.js): las tres son el mismo clip."""
    n = Path(str(nombre)).name.lower()
    if n.endswith(SUFIJO_PROMPT):
        n = n[:-len(SUFIJO_PROMPT)]
    else:
        n = Path(n).stem
    m = VUELTA_DE_CLIP.search(n)
    if not m:
        return n, 0
    return n[:m.start()], (int(m.group(1)) if m.group(1) else 1)


def prompts_hermanos(carpeta: Path, excepto: Path | None = None) -> list[tuple[str, str]]:
    """Los prompts de los OTROS clips ya generados en `carpeta` (apimart.py guarda <clip>_prompt.txt junto a cada clip), sin el renglon de parametros. Igual que el dash:
    «03_v2» y «03_b» son el mismo clip que «03»; de cada clip vale su vuelta mas nueva (la de numero mas alto; si empatan, el archivo mas reciente), y el clip `excepto`
    (el que se esta pidiendo) no se compara con NINGUNA de sus vueltas: al repetir un clip, con el mismo nombre o como «03_v2», se corrige su voz."""
    salida: list[tuple[str, str]] = []
    if not Path(carpeta).is_dir():
        return salida
    propio = clip_y_vuelta(Path(excepto).name)[0] if excepto is not None else None
    elegido: dict[str, tuple[tuple[int, float], Path]] = {}
    for p in sorted(Path(carpeta).glob("*" + SUFIJO_PROMPT)):
        clip, vuelta = clip_y_vuelta(p.name)
        if clip == propio:
            continue
        try:
            orden = (vuelta, p.stat().st_mtime)
        except OSError:
            continue
        if clip not in elegido or orden > elegido[clip][0]:
            elegido[clip] = (orden, p)
    for _orden, p in sorted(elegido.values(), key=lambda x: x[1].name):
        try:
            texto = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        salida.append((p.name, texto.split("\n\n# parametros:")[0]))
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="tipo", required=True)
    p = sub.add_parser("imagen")
    p.add_argument("archivo")
    p.add_argument("--ref", action="append", default=[])
    p.add_argument("--producto")
    p = sub.add_parser("video")
    p.add_argument("archivo")
    p.add_argument("--duracion", type=int, default=8)
    p.add_argument("--carpeta", help="la carpeta de los clips del anuncio: VOICE y ACCENT se comparan con los <clip>_prompt.txt que ya hay ahi")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    texto = Path(a.archivo).read_text(encoding="utf-8")
    if a.tipo == "imagen":
        raiz = Path(__file__).resolve().parents[1]
        problemas = revisar_imagen(texto, a.ref, (raiz / "public" / "productos" / a.producto) if a.producto else None)
    else:
        problemas = revisar_video(texto, a.duracion, prompts_hermanos(Path(a.carpeta), None) if a.carpeta else None)
    if problemas:
        print(mensaje(problemas, Path(a.archivo).name))
        return 1
    print(f"{Path(a.archivo).name}: cumple la plantilla.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
