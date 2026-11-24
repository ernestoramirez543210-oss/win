"""S4 del anuncio automatico: la VOZ del anuncio, con la voz que el dueño ya usa en los flujos, y la transcripcion con el TEXTO del guion. Sin tokens de Claude.

La voz sale de Fish Audio, la misma que genera los audios de los flujos de WhatsApp. La llave de Fish vive SOLO en el dash: este script no la tiene ni la necesita; le pide el audio
al dash (`POST /api/fabrica-editor {accion:'voz'}` con la misma llave del editor, DASH_URL y DASH_CLAVE_EDITOR de .env) y el dash le contesta con el audio.
Por defecto la voz es la de los flujos: la «voz por defecto» de la página APIs del dash (si no hay, la primera guardada en la página Voces; hoy «Voz estándar (mujer)»). Si la receta pide un
narrador hombre (`voz.genero = hombre`) se usa una voz guardada con Género «Hombre» en la página Voces (la más afín a la oferta si hay varias; si una voz no tiene género, se lee del nombre:
«(hombre)»); si no hay ninguna, se detiene y dice cómo agregarla: nunca inventa una voz.

Uso:
  python scripts/voz_auto.py <producto> <AD> [--voz "Nombre de una voz guardada"] [--genero mujer|hombre] [--velocidad 1.1]
                             [--tope-usd 0.05] [--forzar] [--ensayo] [--minimo-calce 85] [--transcripcion-whisper archivo.json]

Entra:  anuncios/<producto>/tiktok/<AD>/guion.json (o guion.txt)  y  receta.json (voz.genero, voz.nombre, voz.velocidad, duracionMin/Max, aprobacion.topeUsd)
Sale en anuncios/<producto>/tiktok/<AD>/:
  audio/voz_original.ogg      lo que devolvio el dash (Opus)
  audio/voz.mp3               la voz a la velocidad de la receta (atempo)         <- la que busca montaje_auto.py
  audio/voz_norm.wav          la voz a -14 LUFS (mezclar_audio.py) y audio/mezcla.json
  audio/voz_meta.json         de que texto y voz salio el audio (si no cambian, no se vuelve a pagar; cambiar la velocidad solo vuelve a convertir)
  audio/voz_rechazada.mp3     la voz que NO coincidio con el guion (se aparta para que montaje_auto.py no la use)
  transcripcion.json          las palabras DEL GUION con los tiempos que midio Whisper (y la huella del guion, para que el montaje no use la voz de OTRO guion): asi los subtitulos
                              salen con el texto correcto aunque Whisper oiga «ibup» por «ebook». Whisper NO recibe el guion como pista: si no, «oiria» el guion aunque la voz dijera otra cosa

Dinero (regla fija del editor): Fish cobra US$ 15 por millon de bytes de texto (≈ US$ 0,015 un anuncio de 50 s; el doble si Fish no entrega Ogg y el dash tiene que pedir el MP3). El permiso sale de --tope-usd (el OK del dueño para ESTA corrida) o de
aprobacion.topeUsd de la receta menos lo ya gastado en el anuncio. Sin ninguno NO gasta nada: muestra el costo y sale con codigo 2.

Codigos de salida: 0 listo · 1 no se pudo · 2 falta el OK de gasto (no se gasto nada) · 3 listo, pero la voz dura mas o menos de lo que permite la receta.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import RAIZ, configurar_consola, correr, duracion_seg, ejecutable, escribir_json, leer_json, salir, version_editor  # noqa: E402
from anuncio_comun import avisador_de_avance, carpeta_anuncio, carpeta_producto, comprobar_ids, decir, gastado_en_anuncio, huella_guion, leer_receta, numero, plano, tokens, tope_disponible, val  # noqa: E402
from guion_auto import COLECTIVOS, NUMEROS_PALABRA  # noqa: E402            # las mismas palabras de numero que usa el guion (una sola tabla)

PRECIO_FISH_POR_MILLON_BYTES = 15.0        # USD, docs.fish.audio (pricing-and-rate-limits): s2.1-pro y s2-pro; consultado el 2026-10-20
MODELO_VOZ = "fish-s2.1-pro"
MAX_LETRAS = 1500                          # lo mismo que acepta el dash por llamada: un anuncio de 60 s son ~900 letras
HUECO_MAXIMO = 5                           # palabras SEGUIDAS del guion que Whisper puede no oir antes de rechazar la voz (la voz se salto una frase)
SOBRA_SEGUIDA_MAXIMA = 6                   # palabras SEGUIDAS del audio que no estan en el guion (la voz repitio o agrego una frase); Whisper parte «ebook» o «30» en 2 o 3 palabras, de ahi el margen
UMBRAL_ALINEACION = 0.85                   # si Whisper no reconoce al menos el 85 % de las palabras del guion, algo salio mal (la voz no dijo el guion). --minimo-calce lo cambia
# Afinidad entre el tono de una voz (palabras de su nombre) y de que trata la oferta: solo desempata entre varias voces del mismo genero.
AFINIDAD = (
    (r"calid|seren|suav|amab|amig|matern|cercan|tranquil", r"biblic|cristian|oracion|devocion|sermon|\bfe\b|familia|hogar|bebe|mama|maternidad|manualidad|bordado|croche|tejid|cocina|receta|dinamica|mujeres"),
    (r"energic|firm|segur|profesional|motivad|serio|dinamic", r"negocio|dinero|venta|emprend|finanz|marketing|trading|curso|aprend|liderazgo|productividad"),
)


# ------------------------------------------------------------------ eleccion de la voz
def genero_de(nombre: str) -> str:
    """'mujer', 'hombre' o '' segun lo que dice el nombre de la voz guardada (convencion: termina en «(mujer)» o «(hombre)»)."""
    n = plano(nombre)
    if re.search(r"\(hombre\)|\bhombre\b|\bmasculin[ao]\b|\bvaron\b", n):
        return "hombre"
    if re.search(r"\(mujer\)|\bmujer\b|\bfemenin[ao]\b", n):
        return "mujer"
    return ""


def _voces(voces) -> list[dict]:
    """Las voces del dash como [{nombre, genero}] (acepta tambien una lista de nombres). El genero que se eligio en la pestaña Voces manda; si no hay, se lee del nombre."""
    out = []
    for v in voces or []:
        nombre = str(v if isinstance(v, str) else (v or {}).get("nombre") or "").strip()
        if not nombre:
            continue
        g = plano((v.get("genero") if isinstance(v, dict) else "") or "").strip()
        out.append({"nombre": nombre, "genero": g if g in ("mujer", "hombre") else genero_de(nombre)})
    return out


def elegir_voz(voces, genero: str, nombre_pedido: str = "", texto_oferta: str = "", por_defecto: str = "") -> tuple[str | None, str]:
    """(nombre de la voz, motivo). None si no hay ninguna que sirva (el motivo dice que hacer). La receta puede fijar el nombre; si no, se elige por genero:
    MUJER = la voz de los flujos (la «voz por defecto» del dash) sin mirar la oferta; HOMBRE = una voz guardada con Genero «Hombre», la mas afin a la oferta si hay varias."""
    lista = _voces(voces)
    nombres = [v["nombre"] for v in lista]
    if not nombres:
        return None, "El dash no tiene ninguna voz guardada. Agrega una en la página Voces del dash y repite."
    if nombre_pedido:
        for n in nombres:
            if plano(n).strip() == plano(nombre_pedido).strip():
                return n, f"la voz que fija la receta o la linea de comandos: «{n}»"
        return None, f"La voz «{nombre_pedido}» no está entre las guardadas en el dash: {', '.join('«' + n + '»' for n in nombres)}."
    genero = genero if genero in ("mujer", "hombre") else "mujer"
    if genero == "mujer":
        defecto = next((v for v in lista if plano(v["nombre"]).strip() == plano(por_defecto).strip()), None) if por_defecto else None
        if defecto and defecto["genero"] != "hombre":
            return defecto["nombre"], f"la voz de tus flujos (la «voz por defecto» del dash): «{defecto['nombre']}»"
        mujeres = [v["nombre"] for v in lista if v["genero"] == "mujer"]
        if mujeres:
            return mujeres[0], f"la primera voz de mujer guardada: «{mujeres[0]}»"
        return nombres[0], f"ninguna voz dice ser de mujer: se usa la primera guardada, «{nombres[0]}» (la que sale por defecto en los flujos)"
    candidatas = [v["nombre"] for v in lista if v["genero"] == "hombre"]
    if not candidatas:
        return None, ("La receta pide un narrador hombre y el dash no tiene ninguna voz de hombre guardada (las que hay: "
                      + ", ".join("«" + n + "»" for n in nombres) + "). Agrega una en la página Voces del dash con Género «Hombre»: "
                      "las candidatas de la biblioteca de Fish Audio están en .claude/skills/fabrica-ofertas/references/voces-hombre.md. No se inventa una voz.")
    if len(candidatas) == 1:
        return candidatas[0], f"la única voz de hombre guardada: «{candidatas[0]}»"
    oferta = plano(texto_oferta)
    mejor, puntos = candidatas[0], -1
    for n in candidatas:
        p = sum(1 for tono, patron in AFINIDAD if re.search(tono, plano(n)) and re.search(patron, oferta))
        if p > puntos:
            mejor, puntos = n, p
    return mejor, f"la voz de hombre más afín a la oferta entre {len(candidatas)}: «{mejor}»"


# ------------------------------------------------------------------ dinero
def bytes_de(texto: str) -> int:
    return len(texto.encode("utf-8"))


def costo_estimado(texto: str) -> float:
    return bytes_de(texto) * PRECIO_FISH_POR_MILLON_BYTES / 1_000_000


# ------------------------------------------------------------------ el dash
def voces_del_dash() -> tuple[list[dict], str]:
    """(las voces guardadas como [{nombre, genero}], el nombre de la voz de los flujos). El dash no entrega sus ids. Gratis."""
    import fabrica

    j = fabrica.llamar("GET", "?voces=1")
    if not isinstance(j.get("voces"), list):          # un dash viejo no entiende ?voces=1 y contesta la lista de pedidos: no es «ninguna voz guardada»
        salir("El dash todavía no tiene la voz para el editor (falta que se despliegue el cambio de functions/api/fabrica-editor.js): ?voces=1 no devolvió la lista de voces.")
    return _voces([v for v in (j.get("voces") or []) if isinstance(v, dict)]), str(j.get("porDefecto") or "")


def pedir_voz(texto: str, voz: str | None) -> tuple[bytes, str, str, float | None]:
    """(audio, tipo, nombre de la voz usada, costo estimado que dice el dash). La llave de Fish no pasa por aqui: el dash la usa por nosotros."""
    import fabrica
    import requests

    url, llave = fabrica.config_dash()
    cuerpo = {"accion": "voz", "texto": texto}
    if voz:
        cuerpo["voz"] = voz
    try:
        # Sin seguir redirecciones: requests conserva las cabeceras al cambiar de sitio y la llave no debe salir de la direccion del dash.
        # x-version-editor: el dash solo da la voz (que cuesta) a un editor al dia; a una copia vieja le contesta 426.
        r = requests.post(url + "/api/fabrica-editor", json=cuerpo, headers={"x-clave-editor": llave, "x-version-editor": version_editor()}, timeout=180, allow_redirects=False)
    except requests.RequestException as e:
        salir(f"Sin conexion con el dash ({type(e).__name__}). Revisa internet y DASH_URL en .env.")
    tipo = r.headers.get("Content-Type", "")
    if r.status_code == 200 and tipo.startswith("audio/") and r.content:
        try:
            costo = float(r.headers.get("X-Costo-Estimado-Usd", ""))
        except ValueError:
            costo = None
        if costo is not None and not 0 <= costo <= 1:          # una voz de un anuncio no cuesta un dolar: si el numero es absurdo, no se usa
            costo = None
        return r.content, tipo, unquote(r.headers.get("X-Voz", "")) or (voz or ""), costo
    try:
        j = r.json()
    except ValueError:
        j = {}
    detalle = j.get("error") or f"respuesta rara (HTTP {r.status_code})"
    if r.status_code == 401:
        salir("El dash rechazo la llave (DASH_CLAVE_EDITOR de .env). Pidele al dueño que corra scripts\\Setup-ClaveEditor.ps1 otra vez.")
    if r.status_code == 426:
        salir(f"Este editor esta viejo para el dash (426): {detalle} Tienes la version {version_editor() or '?'}; actualizalo: baja el instalador del dash (Herramientas > Editor de anuncios) "
              "y corre Instalar-Editor.cmd. No se gasto nada.")
    if r.status_code == 503:
        salir(f"El dash no puede dar voz todavia: {detalle}")
    if 300 <= r.status_code < 400:
        salir("El dash contesto con una redireccion; no la sigo (la llave no sale de la direccion que pusiste). Revisa DASH_URL en .env.")
    if r.status_code == 400 and "accion" in str(detalle).lower():
        salir("El dash todavia no tiene la voz para el editor (falta que se despliegue el cambio de functions/api/fabrica-editor.js).")
    salir(f"El dash no dio la voz (HTTP {r.status_code}): {detalle}")
    raise SystemExit(1)


# ------------------------------------------------------------------ audio
def convertir_a_mp3(origen: Path, destino: Path, velocidad: float) -> None:
    filtro = [] if abs(velocidad - 1.0) < 0.005 else ["-filter:a", f"atempo={velocidad:.3f}"]
    r = correr([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(origen), *filtro, "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "2", str(destino)])
    if r.returncode != 0 or not destino.is_file():
        salir(f"ffmpeg no pudo convertir la voz: {r.stderr.strip()[:300]}. La voz ya se pagó y quedó guardada en {origen}: si el audio está dañado, repite con --forzar (vuelve a pagar).")


# ------------------------------------------------------------------ transcripcion con el texto del guion
def _plano_token(t: str) -> str:
    return "".join(tokens(t))


# Whisper escribe las cifras en digitos («30», «300») aunque la voz diga «treinta» y el guion las pida en letras: sin esto cada cifra contaba como una palabra de mas en el audio y como una palabra
# que Whisper no oyo en el guion (pedido 103 de RECETAS NAVIDAD: 9 cifras, «17 palabras que no estan en el guion», una voz buena rechazada).
_NUMERO_EN_LETRAS = {w: v for w, v in NUMEROS_PALABRA.items() if w not in COLECTIVOS and v < 1000}          # «mil» y «millones» se oyen en varias palabras: se dejan como estan


def _clave(t: str) -> str:
    """Con que se compara una palabra del guion con una de Whisper: sin tildes ni signos y, si es un numero, por su valor («treinta», «trescientas» y «30», «300» valen lo mismo)."""
    p = _plano_token(t)
    if p.isascii() and p.isdigit() and len(p) <= 9:
        return "#" + str(int(p))
    v = _NUMERO_EN_LETRAS.get(p)
    return p if v is None else "#" + str(v)


def alinear_con_guion(texto: str, whisper: list[dict], duracion: float) -> tuple[list[dict], dict]:
    """Las palabras del GUION con los tiempos que midio Whisper (lista como la de transcribir.py: texto, inicio, fin, prob) y un resumen de que tan bien calzaron.

    Se alinean las palabras (sin tildes ni signos) con difflib: las que Whisper oyo igual heredan su tiempo; las que oyo distinto (un «ebook» que salio «ibup», un «30» que salio
    «treinta», una palabra que no oyo) reciben el tiempo repartido entre sus vecinas ya alineadas, segun el largo de cada palabra. Los subtitulos salen con la ortografia del guion."""
    crudas = [t for t in texto.split() if _plano_token(t)]            # los signos sueltos («—») no suenan: no llevan tiempo
    if not crudas:
        return [], {"palabras": 0, "calzan": 0, "porcentaje": 0.0, "sobran_en_whisper": len(whisper), "inicio_oido": False, "final_oido": False, "fin_ultima_palabra": 0.0, "hueco_maximo": 0, "sobra_seguida": 0}
    gp = [_plano_token(t) for t in crudas]                    # largo de cada palabra: reparte el tiempo de las que Whisper no oyo
    clave_g = [_clave(t) for t in crudas]
    clave_w = [_clave(w.get("texto", "")) for w in whisper]
    anclas: list[tuple[int, int]] = []
    for b in difflib.SequenceMatcher(None, clave_g, clave_w, autojunk=False).get_matching_blocks():
        anclas.extend((b.a + d, b.b + d) for d in range(b.size))
    n = len(crudas)
    tiempos: list[tuple[float, float, float] | None] = [None] * n
    for i, j in anclas:
        w = whisper[j]
        tiempos[i] = (float(w["inicio"]), float(w["fin"]), float(w.get("prob", 0.9)))
    prev_i, prev_j = -1, -1
    for i, j in anclas + [(n, len(whisper))]:
        hueco = list(range(prev_i + 1, i))
        if hueco:
            sin_ancla = whisper[prev_j + 1:j]
            if sin_ancla:
                t0, t1 = float(sin_ancla[0]["inicio"]), float(sin_ancla[-1]["fin"])
            else:
                t0 = float(whisper[prev_j]["fin"]) if prev_j >= 0 else max(0.0, (float(whisper[j]["inicio"]) if j < len(whisper) else duracion) - 0.35 * len(hueco))
                t1 = float(whisper[j]["inicio"]) if j < len(whisper) else duracion
            if t1 - t0 < 0.08 * len(hueco):
                t1 = t0 + 0.08 * len(hueco)
            pesos = [max(1, len(gp[k])) for k in hueco]
            total, cursor = float(sum(pesos)), t0
            for k, p in zip(hueco, pesos):
                paso = (t1 - t0) * p / total
                tiempos[k] = (cursor, cursor + paso, 0.4)
                cursor += paso
        prev_i, prev_j = i, j
    salida: list[dict] = []
    ultimo = 0.0
    for tok, (ini, fin, prob) in zip(crudas, tiempos):          # type: ignore[misc]
        ini = max(ini, ultimo)                                   # nunca hacia atras
        fin = max(fin, ini + 0.02)
        salida.append({"texto": tok, "inicio": round(ini, 3), "fin": round(fin, 3), "prob": round(prob, 3)})
        ultimo = ini
    anclados = {i for i, _ in anclas}
    oidos = {j for _, j in anclas}
    hueco = hueco_max = sobra = sobra_max = 0
    for k in range(n):                                      # el trozo mas largo de palabras seguidas del guion que Whisper no oyo
        hueco = 0 if k in anclados else hueco + 1
        hueco_max = max(hueco_max, hueco)
    for k in range(len(whisper)):                           # y el mas largo de palabras seguidas del audio que no estan en el guion
        sobra = 0 if k in oidos else sobra + 1
        sobra_max = max(sobra_max, sobra)
    resumen = {"palabras": n, "calzan": len(anclas), "porcentaje": round(len(anclas) / n * 100, 1), "sobran_en_whisper": max(0, len(whisper) - len(anclas)),
               "inicio_oido": any(k in anclados for k in range(min(3, n))), "final_oido": any(k in anclados for k in range(max(0, n - 3), n)),
               "fin_ultima_palabra": salida[-1]["fin"] if salida else 0.0, "hueco_maximo": hueco_max, "sobra_seguida": sobra_max}
    return salida, resumen


def problemas_de_calce(resumen: dict, duracion: float, minimo_pct: float, hueco_max: int | None = HUECO_MAXIMO, sobra_max: int | None = SOBRA_SEGUIDA_MAXIMA) -> list[str]:
    """Por que la voz NO se da por buena ([] = buena): no dijo el guion, esta cortada, se salto un trozo seguido, repitio o agrego uno, le sobra texto o le sobra audio al final.
    hueco_max y sobra_max son las palabras SEGUIDAS que se pueden saltar o agregar (None = no se miran)."""
    p: list[str] = []
    if resumen["palabras"] == 0 or resumen["porcentaje"] < minimo_pct:
        p.append(f"Whisper solo reconoció el {resumen['porcentaje']:.0f} % de las palabras del guion (mínimo {minimo_pct:.0f} %)")
    if not resumen["final_oido"]:
        p.append("no se oye el final del guion (ninguna de sus últimas 3 palabras): la voz parece cortada")
    if not resumen["inicio_oido"]:
        p.append("no se oye el comienzo del guion (ninguna de sus primeras 3 palabras)")
    if hueco_max is not None and resumen.get("hueco_maximo", 0) > hueco_max:
        p.append(f"Whisper no oyó un trozo seguido de {resumen['hueco_maximo']} palabras del guion (más de {hueco_max} seguidas): parece que la voz se saltó una frase")
    if sobra_max is not None and resumen.get("sobra_seguida", 0) > sobra_max:
        p.append(f"en el audio hay un trozo seguido de {resumen['sobra_seguida']} palabras que no están en el guion (más de {sobra_max} seguidas): parece que la voz repitió o agregó una frase")
    if resumen["sobran_en_whisper"] > max(8, 0.1 * resumen["palabras"]):
        p.append(f"en el audio hay {resumen['sobran_en_whisper']} palabras que no están en el guion: la voz dijo algo más")
    if resumen["final_oido"] and duracion - resumen["fin_ultima_palabra"] > 4.0:
        p.append(f"quedan {duracion - resumen['fin_ultima_palabra']:.0f} s de audio después de la última palabra del guion")
    return p


def comando_whisper(audio: Path, salida: Path) -> list[str]:
    """El comando que mide los tiempos con Whisper local. SIN `--guion`: darle el texto esperado como pista lo hace «oir» el guion aunque la voz diga otra cosa,
    y entonces comprobar que el audio dijo el guion no serviria de nada."""
    return [sys.executable, str(RAIZ / "scripts" / "transcribir.py"), str(audio), "--salida", str(salida), "--modelo", "small", "--idioma", "es"]


def segmentos_de(frases: list[str], palabras: list[dict]) -> list[dict]:
    """Un segmento por frase del guion (como los de transcribir.py): de la primera a la ultima palabra de la frase."""
    segs, k = [], 0
    for f in frases:
        n = len([t for t in f.split() if _plano_token(t)])
        if n == 0 or k >= len(palabras):
            continue
        trozo = palabras[k:k + n]
        segs.append({"inicio": trozo[0]["inicio"], "fin": trozo[-1]["fin"], "texto": f.strip()})
        k += n
    return segs


# ------------------------------------------------------------------ programa principal
def _texto_de(f) -> str:
    """El texto de una frase del guion, con las mismas claves que lee montaje_auto.py (texto, es, text); si no tiene, vacio (nunca la palabra «None»)."""
    if isinstance(f, dict):
        return next((str(f[k]) for k in ("texto", "es", "text") if f.get(k)), "")
    return "" if f is None else str(f)


def leer_guion(base: Path) -> tuple[str, list[str]]:
    """(texto corrido, frases) del guion: guion.json (lo de guion_auto.py) o, si alguien lo escribio a mano, guion.txt."""
    j = base / "guion.json"
    if j.is_file():
        try:
            g = leer_json(j)
            frases = [re.sub(r"\s+", " ", _texto_de(f)).strip() for f in (g.get("frases") or g.get("escenas") or g.get("scenes") or [])]
            frases = [f for f in frases if f]
            if frases:
                return " ".join(frases), frases
        except (ValueError, AttributeError):
            salir("guion.json no se puede leer (JSON roto). Vuelve a escribirlo: python scripts/guion_auto.py <producto> <AD> --forzar")
    t = base / "guion.txt"
    if t.is_file():
        texto = re.sub(r"\s+", " ", t.read_text(encoding="utf-8", errors="replace")).strip()
        if texto:
            return texto, [texto]
    salir(f"No hay guion para {base.name}. Primero: python scripts/guion_auto.py <producto> {base.name}")
    raise SystemExit(1)


def texto_de_la_oferta_para_voz(base: Path, receta: dict) -> str:
    """Nombre, promesa, vinetas y notas de NUESTRA oferta: solo sirve para desempatar entre varias voces del mismo genero."""
    try:
        orden = leer_json(base / "orden.json") if (base / "orden.json").is_file() else {}
    except ValueError:
        orden = {}
    o = (orden.get("oferta") or {}) if isinstance(orden, dict) else {}
    pr = (orden.get("proyecto") or {}) if isinstance(orden, dict) else {}
    return " ".join(str(x) for x in (pr.get("nombre", ""), o.get("promesa", ""), " ".join(str(v) for v in (o.get("vinetas") or [])), val(receta, "notas", "")))


def calce_para_aviso(porcentaje, a_mano: bool):
    """El % de coincidencia que se le cuenta a /fabrica: el que midio un Whisper de VERDAD. Con un Whisper dado a mano (--transcripcion-whisper, solo para pruebas) nadie oyo el audio: None."""
    return None if a_mano else porcentaje


def nota_de_voz(duracion: float, calce, fuera: bool) -> str:
    """La nota que se le manda a /fabrica cuando la voz esta lista: cuanto dura y, solo si lo midio un Whisper de verdad, en que porcentaje coincide con el guion."""
    return f"Voz lista: dura {duracion:.0f} s" + (f" y coincide con el guion en un {calce} %" if calce is not None else "") + ("; está fuera del largo que pide la receta" if fuera else "") + "."


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("producto")
    ap.add_argument("anuncio")
    ap.add_argument("--tipo", default="tiktok", choices=["tiktok"])
    ap.add_argument("--voz", help="nombre de una voz guardada en el dash (si no, se elige por genero)")
    ap.add_argument("--genero", choices=["mujer", "hombre"], help="narrador (si no, el de la receta: voz.genero)")
    ap.add_argument("--velocidad", type=float, help="velocidad de la voz (si no, la de la receta: voz.velocidad)")
    ap.add_argument("--tope-usd", type=float, help="OK del dueño para el gasto de ESTA corrida (si no, manda aprobacion.topeUsd de la receta)")
    ap.add_argument("--forzar", action="store_true", help="volver a pedir la voz aunque ya haya una del mismo texto")
    ap.add_argument("--ensayo", action="store_true", help="dice que voz usaria y cuanto costaria, sin pedir nada")
    ap.add_argument("--minimo-calce", type=float, help=f"porcentaje minimo de palabras del guion que Whisper debe reconocer en la voz (por defecto {UMBRAL_ALINEACION * 100:.0f}); ponerlo tambien deja de exigir que no falte ni sobre un trozo seguido de mas de {HUECO_MAXIMO} o {SOBRA_SEGUIDA_MAXIMA} palabras. Solo si Whisper oye mal una voz que SI esta bien")
    ap.add_argument("--transcripcion-whisper", help="usa esta transcripcion de Whisper (JSON de transcribir.py) en vez de correr Whisper")
    a = ap.parse_args()
    comprobar_ids(a.producto, a.anuncio)

    base = carpeta_anuncio(a.producto, a.anuncio, a.tipo)
    if not base.is_dir():
        salir(f"No existe {base}. Primero trae el pedido (`python scripts/fabrica.py traer`) y escribe el guion (`python scripts/guion_auto.py {a.producto} {a.anuncio}`).")
    receta = leer_receta(base)
    if str(val(receta, "voz.origen", "tts")) == "grabada":
        salir("La receta pide una locución GRABADA por una persona: pon su audio en public/productos/<producto>/voces/<AD>.mp3. voz_auto.py solo hace voz de IA.")
    locuciones = sorted((carpeta_producto(a.producto) / "voces").glob(f"{a.anuncio}.*"))
    if locuciones:
        salir(f"Ya hay una locución de este anuncio en {locuciones[0]}: montaje_auto.py usaría ESA voz y no la de IA que pides ahora, y los subtítulos y los cortes saldrían de otra. "
              "Si ya no sirve, muévela o bórrala; si es la buena, no hace falta voz_auto.py. No se gastó nada.")
    texto, frases = leer_guion(base)
    sha = huella_guion(frases)
    minimo_calce = UMBRAL_ALINEACION * 100 if a.minimo_calce is None else a.minimo_calce
    if not 0 <= minimo_calce <= 100:
        salir("--minimo-calce es un porcentaje entre 0 y 100.")
    if len(texto) > MAX_LETRAS:
        salir(f"El guion tiene {len(texto)} letras y el dash acepta hasta {MAX_LETRAS} por voz (un anuncio de 60 s son ~900). Escribe uno más corto.")

    genero = a.genero or str(val(receta, "voz.genero", "mujer"))
    nombre_pedido = (a.voz or str(val(receta, "voz.nombre", "") or "")).strip()
    velocidad = numero(a.velocidad if a.velocidad is not None else val(receta, "voz.velocidad", 1.1), 1.1, 0.8, 1.4)
    oferta_txt = texto_de_la_oferta_para_voz(base, receta)
    voces, por_defecto = voces_del_dash()
    voz, motivo = elegir_voz(voces, genero, nombre_pedido, oferta_txt, por_defecto)
    if voz is None:
        salir(motivo)
    estimado = costo_estimado(texto)
    peor = estimado * 2          # si Fish no entrega Ogg, el dash pide el MP3 y se pagan las dos
    decir(f"Voz: {motivo}.\nTexto: {len(texto)} letras ({bytes_de(texto)} bytes) · velocidad {velocidad:g} · costo estimado de Fish ≈ US$ {estimado:.4f} (hasta US$ {peor:.4f} si Fish no entrega Ogg y se pide dos veces; US$ {PRECIO_FISH_POR_MILLON_BYTES:g} por millón de bytes; es una estimación: mira el panel de Fish).")

    audio_dir = base / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    original, mp3, meta = audio_dir / "voz_original.ogg", audio_dir / "voz.mp3", audio_dir / "voz_meta.json"
    huella = hashlib.sha1(f"{texto}|{voz}|{MODELO_VOZ}".encode("utf-8")).hexdigest()     # lo que se PAGA: el texto, la voz y el modelo (la velocidad solo cambia la conversion, que es gratis)
    previa: dict = {}
    if meta.is_file():
        try:
            previa = leer_json(meta) or {}
        except ValueError:
            previa = {}
    reutilizar = bool(previa.get("huella") == huella and original.is_file() and original.stat().st_size > 0 and not a.forzar)

    if a.ensayo:
        decir("Ensayo: " + ("el audio de este texto y esta voz ya está pedido y no se vuelve a pagar." if reutilizar else "esto es lo que se pediría; no se pidió nada ni se gastó nada."))
        return

    if reutilizar:
        decir("El audio de este mismo texto y esta voz ya está pedido (voz_original.ogg): no se vuelve a pagar.")
    else:
        # con --tope-usd (el corredor lo pasa siempre) no hace falta saber lo ya gastado; con el tope de la receta, si: se le pregunta al dash (ve todos los equipos)
        gastado = 0.0 if a.tope_usd is not None else gastado_en_anuncio(a.producto, a.anuncio, preguntar_dash=True)
        disponible, origen = tope_disponible(a.tope_usd, receta, gastado)
        if disponible is None:
            decir(f"Falta el OK del dueño para gastar ≈ US$ {estimado:.4f} en la voz. Cuando lo dé, repite con: --tope-usd {max(0.05, round(estimado * 5, 2)):g}   (no se gastó nada).")
            sys.exit(2)
        if peor > disponible:
            decir(f"El costo estimado (≈ US$ {estimado:.4f}, hasta US$ {peor:.4f} en el peor caso) pasa del tope disponible (US$ {disponible:.4f}, de {origen}). No se gastó nada. Sube el tope con el OK del dueño.")
            sys.exit(2)
        decir(f"Permiso de gasto: {origen}; disponible US$ {disponible:.4f}. Pidiéndole la voz al dash...")
        pedida = None if (not nombre_pedido and genero == "mujer" and plano(voz) == plano(por_defecto)) else voz        # la voz de los flujos la pone el dash por su id
        audio, tipo, usada, costo_dash = pedir_voz(texto, pedida)
        original.write_bytes(audio)
        # Ya se pago: el costo y el archivo se anotan ANTES de convertir. Si lo que sigue falla, la proxima corrida reutiliza este audio y no paga otra vez.
        import apimart as A

        A.anotar({"tipo": "voz", "modelo": MODELO_VOZ, "proveedor": "fishaudio", "task_id": None, "costo": None, "costo_estimado": round(estimado if costo_dash is None else costo_dash, 6), "caracteres": len(texto),
                  "bytes": bytes_de(texto), "voz": usada, "archivo": str(original), "anuncio": a.anuncio, "producto": a.producto, "etiqueta": "voz"})
        previa = {"huella": huella, "guion_sha1": sha, "voz": usada, "modelo": MODELO_VOZ, "caracteres": len(texto), "bytes": bytes_de(texto), "costo_estimado_usd": round(estimado if costo_dash is None else costo_dash, 6),
                  "generado": datetime.now().isoformat(timespec="seconds"), "tipo_de_audio": tipo, "velocidad_mp3": None}
        escribir_json(meta, previa)

    # La voz a la velocidad de la receta (gratis): se rehace si no existe, si cambio la velocidad o si el audio original es mas nuevo.
    if not mp3.is_file() or previa.get("velocidad_mp3") != velocidad or mp3.stat().st_mtime < original.stat().st_mtime:
        convertir_a_mp3(original, mp3, velocidad)
        previa["velocidad_mp3"] = velocidad
        escribir_json(meta, previa)
        decir(f"Voz lista a velocidad {velocidad:g}: {mp3} ({duracion_seg(mp3):.1f} s).")

    duracion = duracion_seg(mp3)

    # transcripcion: Whisper mide los tiempos, el texto es el del guion. Ademas se comprueba que el audio DIGA el guion (sin darle el guion a Whisper como pista).
    trans = base / "transcripcion.json"
    hacer_transcripcion = True
    if trans.is_file() and trans.stat().st_mtime >= mp3.stat().st_mtime and not a.forzar:
        try:
            if (leer_json(trans).get("alineado_con_guion") or {}).get("guion_sha1") == sha:
                decir("transcripcion.json ya está hecha con el texto de este guion.")
                hacer_transcripcion = False
        except ValueError:
            pass
    if hacer_transcripcion:
        if a.transcripcion_whisper:
            decir("OJO: --transcripcion-whisper le da a Whisper por hecho (solo para pruebas): con él cualquier voz puede salir «buena». Queda anotado en transcripcion.json.")
            ruta_w = Path(a.transcripcion_whisper)
            if not ruta_w.is_file():
                salir(f"No existe la transcripción de Whisper: {ruta_w}")
        else:
            ruta_w = audio_dir / "whisper_voz.json"
            decir("Midiendo los tiempos con Whisper local (gratis; tarda más o menos lo que dura la voz)...")
            r = subprocess.run(comando_whisper(mp3, ruta_w), cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or not ruta_w.is_file():
                salir(f"Whisper no pudo transcribir la voz: {(r.stderr or r.stdout).strip()[-300:]}")
        w = leer_json(ruta_w)
        palabras, resumen = alinear_con_guion(texto, [p for p in (w.get("palabras") or []) if str(p.get("texto", "")).strip()], duracion)
        problemas = problemas_de_calce(resumen, duracion, minimo_calce, *((None, None) if a.minimo_calce is not None else (HUECO_MAXIMO, SOBRA_SEGUIDA_MAXIMA)))
        if not palabras or problemas:
            # la voz que no dijo el guion se aparta (con lo que salio de ella) para que ni montaje_auto.py ni nadie la tome por buena
            rechazada = audio_dir / "voz_rechazada.mp3"
            rechazada.unlink(missing_ok=True)
            mp3.replace(rechazada)
            for sobrante in (trans, audio_dir / "voz_norm.wav", audio_dir / "mezcla.json"):
                sobrante.unlink(missing_ok=True)
            salir("El audio no coincide con el guion: " + "; ".join(problemas or ["el guion no tiene palabras"]) + ". No se guarda una transcripción mala (los subtítulos saldrían mal) y la voz quedó aparte como audio/voz_rechazada.mp3. "
                  "Escúchala: si de verdad no dice el guion, vuelve a correr con --forzar (vuelve a pedir y a pagar la voz); si está bien y solo es Whisper el que oye mal, repite con --minimo-calce más bajo (OJO: con ese flag ya no se exige que no falte ni sobre un trozo seguido de palabras: una frase saltada o repetida también pasaría).")
        escribir_json(trans, {"archivo": str(mp3), "modelo": f"{w.get('modelo', 'whisper')}+guion", "idioma": "es", "pista": w.get("pista") or "ninguna", "duracion": round(duracion, 3), "texto": texto, "palabras": palabras,
                              "segmentos": segmentos_de(frases, palabras), "generado": datetime.now().isoformat(timespec="seconds"), "guion_sha1": sha,
                              "alineado_con_guion": {"texto_de": "guion", "tiempos_de": w.get("modelo", "whisper"), "guion_sha1": sha, "whisper_a_mano": bool(a.transcripcion_whisper), **resumen}})
        decir(f"Transcripción: {trans}  ({resumen['palabras']} palabras del guion, {resumen['calzan']} reconocidas por Whisper = {resumen['porcentaje']} %; las demás reciben el tiempo de sus vecinas).")

    # Lo que se le cuenta a /fabrica sale de la transcripcion que quedo guardada (la de esta corrida o la que ya estaba): una sola regla para las dos.
    try:
        guardada = leer_json(trans).get("alineado_con_guion") or {}
    except (OSError, ValueError):
        guardada = {}
    calce = calce_para_aviso(guardada.get("porcentaje"), bool(guardada.get("whisper_a_mano")))

    # -14 LUFS (el mismo paso que hace montaje_auto.py; si ya esta fresco no lo repite). Solo despues de saber que la voz dice el guion.
    norm, mezcla = audio_dir / "voz_norm.wav", audio_dir / "mezcla.json"
    normalizo = not (norm.is_file() and mezcla.is_file() and norm.stat().st_mtime >= mp3.stat().st_mtime)
    if normalizo:
        r = subprocess.run([sys.executable, str(RAIZ / "scripts" / "mezclar_audio.py"), "--voz", str(mp3), "--salida", str(audio_dir)], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            salir(f"mezclar_audio.py fallo: {(r.stderr or r.stdout).strip()[-300:]}")
        decir("  " + (r.stdout.strip().splitlines() or [""])[0])

    dmin, dmax = numero(val(receta, "duracionMin", 25), 25, 10, 120), numero(val(receta, "duracionMax", 60), 60, 15, 180)
    fuera = duracion < dmin * 0.85 or duracion > dmax + 2.0
    decir(f"\nListo. La voz dura {duracion:.1f} s (la receta pide de {dmin:g} a {dmax:g} s)." + ("  AVISO: está fuera de lo que pide la receta; el control de calidad del montaje lo marcará. "
          "Si es muy larga: python scripts/guion_auto.py <producto> <AD> --forzar con un objetivo más corto, o sube voz.velocidad en la receta." if fuera else ""))
    decir(f"Siguiente paso: python scripts/montaje_auto.py {a.producto} {a.anuncio}")
    # Lo ultimo, para que lo de arriba salga aunque el dash tarde: la pagina /fabrica muestra «Voz» en la barra del anuncio (si el anuncio viene de la Fabrica). Solo si esta corrida hizo algo
    # (transcribio o normalizo): repetir este paso sin nada nuevo no avisa, para que la barra no retroceda de «revision» a «voz» (el plan del montaje ya cuenta la voz como hecha).
    if hacer_transcripcion or normalizo:
        avisador_de_avance(base, a.anuncio, decir)(etapa="voz", nota=nota_de_voz(duracion, calce, fuera))
    if fuera:
        sys.exit(3)


if __name__ == "__main__":
    main()
