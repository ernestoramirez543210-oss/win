"""Montaje AUTOMATICO de un anuncio estilo TikTok: de una voz con su transcripcion y el b-roll del producto a un mp4 ya revisado. Sin LLM:
reglas fijas + CLIP local (S6 y S7 de mejora/anuncio-automatico.md). Nadie mira fotogramas ni escribe planes; es lo que antes hacia Claude a ojo.

Uso:
  python scripts/montaje_auto.py <producto> <AD> [--voz archivo] [--guion guion.json] [--receta receta.json] [--sin-render] [--concurrencia N]
                                 [--musica archivo.mp3] [--sin-historial] [--cta "texto"] [--subcta "texto"]
Entradas (se buscan solas):
  - la voz: public/productos/<p>/voces/<AD>.mp3|wav|m4a, o anuncios/<p>/tiktok/<AD>/audio/voz.*
  - la transcripcion palabra por palabra: anuncios/<p>/tiktok/<AD>/transcripcion.json (si no esta, Whisper local la saca)
  - el b-roll: public/productos/<p>/broll-tiktok/ (lo que falte por analizar, emparejar_tomas.py lo analiza antes)
  - opcional guion.json (frases con `visual.en`, `textoGrande`, `rol`): si esta, manda sobre las reglas de texto; sin el, todo sale de la transcripcion
  - la receta del pedido (receta.json en la carpeta del anuncio; si no esta, los valores de fabrica), producto.json, portada.jpg y paginas/ del ebook, historial.json
Que hace: parte la voz en frases y cortes (ritmo de la receta) → escoge una toma distinta del b-roll para cada corte (emparejar_tomas.py: CLIP, sin repetir,
sin las del anuncio anterior, sin texto quemado, sin cortes de escena adentro) → textos grandes en cifras, beneficios, garantia y gancho → portada y paginas del
ebook cuando se nombra el producto → boton final → videoData.json → aplicar_receta.py → validar_videodata.py → render (remotion.mjs) → control de calidad.
Deja en anuncios/<p>/tiktok/<AD>/: videoData.json, plan.md, evidencia.png (la toma elegida para cada frase: se juzga en 10 segundos), informe_calidad.json y final/<p>_<AD>.mp4.
El control de calidad mira: duracion dentro de la receta, -14 LUFS, cuadros negros, franjas negras arriba y abajo, ninguna toma repetida, subtitulos presentes y nada en los 280 px de abajo.
Sale con 0 si todo paso, 2 si el video quedo pero algo no paso el control, 1 si no se pudo armar.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import io
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, MAX_LETRAS_TEXTO_GANCHO, PUBLIC, RAIZ, configurar_consola, ejecutable, escribir_json, leer_json, recortar_por_palabras, salir  # noqa: E402
import emparejar_tomas as et  # noqa: E402
from anuncio_comun import huella_guion  # noqa: E402
import transcribir as trn  # noqa: E402

# La receta de fabrica: espejo de RECETA_DEFECTO en functions/_fabrica.js (si alla cambia un valor, aqui tambien).
RECETA_DEFECTO = {
    "tipoAnuncio": "tiktok", "duracionMin": 25, "duracionMax": 60, "material": "propio",
    "idioma": {"variante": "neutro"}, "guion": {"origen": "traducir-referencia"}, "voz": {"origen": "tts", "velocidad": 1.1},
    "subtitulos": {"activos": True, "palabrasPorVez": 3, "tamano": 82, "mayusculas": True, "color": "#FFFFFF", "colorActivo": "#FFE14D"},
    "gancho": {"modo": "nuevo-modelado", "segundos": 6, "textoGrande": True, "sonido": "hit", "instruccion": ""},
    "ritmo": {"corteMin": 1, "corteMax": 3, "maxSegPorVideoAjeno": 5},
    "transiciones": {"activas": True, "tipos": ["zoom", "fundido", "deslizar"], "frecuencia": "cambio-de-idea", "duracionMs": 250},
    "sonidos": {"activos": True, "enTransiciones": True, "enTextosGrandes": True, "enCta": True, "lista": ["whoosh_corto", "pop", "hit", "click"], "volumen": 0.6, "maximo": 14},
    "musica": {"estilo": "segun-oferta"},
    "textosGrandes": {"min": 5, "max": 7}, "producto": {"mostrar": True, "paginas": 2}, "cta": {"texto": "", "subtexto": "", "segundos": 2.2},
    "aprobacion": {"plan": "mostrar", "topeUsd": 0}, "notas": "",
}
PALETA = ["#E11D48", "#2563EB", "#16A34A", "#D97706", "#7C3AED", "#DB2777"]       # colorPrimario: se rota para no repetir el del anuncio anterior
ZONA_INFERIOR = 280
COLA_SEG = 0.5                  # lo que dura el anuncio despues de la ultima palabra
ESTILOS_TEXTO = [("caja", "centro"), ("plano", "arriba"), ("resaltado", "centro")]

# palabras que mandan en un anuncio (sin acentos, en espanol y portugues): cifras, garantia, oferta, beneficio, producto, llamada
NUMEROS = set("un uno una dos tres cuatro cinco seis siete ocho nueve diez once doce quince veinte treinta cuarenta cincuenta sesenta cien ciento mil millon "
              "dois tres quatro seis sete oito nove dez vinte trinta quarenta cinquenta".split())
GARANTIA = set("garantia garantizado garantizada reembolso devolucion devolvemos devolvo reembolsamos riesgo".split())
OFERTA = set("gratis gratuito gratuita bono bonos bonus regalo descuento oferta promocion exclusivo limitado ultimos ultimas hoy ahora desconto brinde "
             "gratuitamente promocao exclusiva limitada hoje agora".split())
BENEFICIO = set("aprende aprenderas descubre descubriras logra lograras consigue ahorra gana ganar mejora facil facilmente rapido rapida simple sencillo paso pasos "
                "casa minutos resultados transforma aprenda descubra faca facil rapido passo ganhe aprenda".split())
LLAMADA = set("compra comprar escribenos escribe toca link enlace pidelo pide obten llevate accede descarga aprovecha reserva inscribete compre clique garanta "
              "haz clic click".split())
PRODUCTO_PAL = set("ebook libro guia recetario manual pdf curso metodo kit paquete coleccion moldes plantilla plantillas recetas dinamicas paginas capitulos livro "
                   "receitas".split())
PARADA = set("de la que el en y a los se del las un por con no una su para es al lo como mas pero sus le ya o este si porque esta son entre cuando muy sin sobre "
             "tambien me hasta hay donde quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos e esto antes algunos que unos yo otro otras otra "
             "el tanto esa estos mucho quienes nada muchos cual poco ella estar estas algunas algo nosotros mi mis tu te ti tus ellas del al "
             "do da dos das os as um uma para com nao mais seu sua seus suas na no nas nos em por ao aos que e ou se".split())
FIN_ORACION = (".", "!", "?", "…", ":", ";")
RECORDATORIO = ("Mira evidencia.png y revision_tomas_*.png (tres fotogramas de cada toma: lo que aparece a mitad de una toma solo se ve ahi): el automatico NO detecta ninos, personajes con licencia, "
                "logos sin letras ni subtitulos grandes de otros, y el b-roll de TikTok es de terceros (derechos: lo decide el dueno).")


def como_marcar(producto: str, ad: str) -> str:
    """Que hacer si en evidencia.png o revision_tomas_*.png se ve un nino, un logo o algo que no va (la marca vale para todos los anuncios de ese producto)."""
    return (f"Si en evidencia.png o revision_tomas_*.png ves un nino, un logo o algo que no va en la toma N: `python scripts/emparejar_tomas.py {producto} --marcar-toma {ad} N --motivo \"lo que se ve\"` "
            "y vuelve a montar: esa toma no se vuelve a usar nunca.")


def combinar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = combinar(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def val(receta: dict, ruta: str, defecto=None):
    cur = receta
    for p in ruta.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return defecto
        cur = cur[p]
    return cur


def plano(w: str) -> str:
    """Una palabra sin acentos, sin signos y en minusculas (para compararla con las listas)."""
    return re.sub(r"[^0-9a-z]", "", et.plegar(w))


def limpio_texto(w: str) -> str:
    """La palabra tal como se mostrara en pantalla: sin signos de abrir y cerrar ni puntuacion pegada (se conservan $, % y los puntos dentro de una cifra)."""
    return re.sub(r"^[¿¡\"'(\[]+|[.,;:!?\"')\]…]+$", "", w.strip())


# --------------------------------------------------------------------------------------------------------------------------------------------
# frases y cortes
def partir_frases(palabras: list[dict], pausa: float = 0.7, max_palabras: int = 24) -> list[dict]:
    """La voz en frases: se cierra en un punto, una pregunta o una pausa larga; una frase muy larga se parte en comas o pausas cortas.
    Devuelve [{i0, i1 (incluida), texto, t0, t1}] con los tiempos de la primera y la ultima palabra."""
    if not palabras:
        return []
    grupos: list[tuple[int, int]] = []
    ini = 0
    for i, w in enumerate(palabras):
        sig = palabras[i + 1] if i + 1 < len(palabras) else None
        fin = str(w["texto"]).rstrip().endswith(FIN_ORACION) or sig is None or (float(sig["inicio"]) - float(w["fin"]) >= pausa)
        if fin:
            grupos.append((ini, i))
            ini = i + 1
    final: list[tuple[int, int]] = []
    for a, b in grupos:
        if b - a + 1 <= max_palabras:
            final.append((a, b))
            continue
        # una frase larga se parte en trozos iguales (de `max_palabras` o menos), cada corte donde haya una coma o una pausa corta cerca del lugar ideal
        resto = a
        while b - resto + 1 > max_palabras:
            quedan = b - resto + 1
            trozos = -(-quedan // max_palabras)                         # cuantos trozos hacen falta (redondeo hacia arriba)
            ideal = resto + -(-quedan // trozos) - 1                    # donde termina el primero si todos fueran iguales
            tope = min(b - 2, resto + max_palabras - 1)
            candidatos = [j for j in range(resto + 3, tope + 1) if str(palabras[j]["texto"]).rstrip().endswith(",") or (float(palabras[j + 1]["inicio"]) - float(palabras[j]["fin"]) >= 0.25)]
            corte = min(candidatos, key=lambda j: abs(j - ideal)) if candidatos else ideal
            final.append((resto, corte))
            resto = corte + 1
        final.append((resto, b))
    return [{"i0": a, "i1": b, "texto": " ".join(str(palabras[k]["texto"]).strip() for k in range(a, b + 1)), "t0": float(palabras[a]["inicio"]), "t1": float(palabras[b]["fin"])}
            for a, b in final]


def tokens_de(texto: str) -> list[str]:
    return [t for t in (plano(x) for x in str(texto).split()) if t]


def indices_por_tiempo(palabras: list[dict], inicio, fin) -> tuple[int, int] | None:
    """(primera, ultima) palabra que cae entre `inicio` y `fin` segundos, o None si no hay tiempos validos."""
    try:
        ini, fi = float(inicio), float(fin)
    except (TypeError, ValueError):
        return None
    if not fi > ini:
        return None
    i0 = next((i for i, p in enumerate(palabras) if float(p["fin"]) > ini + 0.02), None)
    i1 = next((i for i in range(len(palabras) - 1, -1, -1) if float(palabras[i]["inicio"]) < fi - 0.02), None)
    return (i0, i1) if i0 is not None and i1 is not None and i1 >= i0 else None


def alinear_guion(frases_guion: list[dict], palabras: list[dict]) -> list[dict]:
    """Pone a cada frase del guion el tiempo en que se dijo (con las palabras de la transcripcion). Si el guion trae `inicio` y `fin` se respetan.
    Devuelve [{i0, i1, texto, t0, t1, guion: <la frase del guion>}] en el orden del guion."""
    pw = [plano(p["texto"]) for p in palabras]
    plano_g: list[str] = []
    dueno: list[int] = []
    for k, f in enumerate(frases_guion):
        for t in tokens_de(f["texto"]):
            plano_g.append(t)
            dueno.append(k)
    pares = difflib.SequenceMatcher(None, plano_g, pw, autojunk=False).get_matching_blocks()
    primera: dict[int, int] = {}
    ultima: dict[int, int] = {}
    for a, b, n in pares:
        for d in range(n):
            k = dueno[a + d]
            primera.setdefault(k, b + d)
            ultima[k] = b + d
    salida: list[dict] = []
    n_pal = len(palabras)
    total_tokens = max(1, len(plano_g))
    acumulado = 0
    for k, f in enumerate(frases_guion):
        n_tok = max(1, len(tokens_de(f["texto"])))
        explicito = indices_por_tiempo(palabras, f.get("inicio"), f.get("fin"))
        if explicito:                                       # el guion trae sus propios tiempos (la etapa de voz): mandan sobre lo que se deduce del texto
            i0, i1 = explicito
        elif k in primera:
            i0, i1 = primera[k], ultima[k]
        else:                                               # sin coincidencias: se reparte por cantidad de palabras
            i0 = min(n_pal - 1, int(acumulado / total_tokens * n_pal))
            i1 = min(n_pal - 1, max(i0, int((acumulado + n_tok) / total_tokens * n_pal) - 1))
        acumulado += n_tok
        salida.append({"i0": i0, "i1": i1, "texto": f["texto"], "t0": float(palabras[i0]["inicio"]), "t1": float(palabras[i1]["fin"]), "guion": f})
    # que no se crucen ni dejen huecos de palabras: cada frase empieza donde termino la anterior
    for k in range(1, len(salida)):
        if salida[k]["i0"] <= salida[k - 1]["i1"]:
            salida[k]["i0"] = min(salida[k - 1]["i1"] + 1, n_pal - 1)
            salida[k]["t0"] = float(palabras[salida[k]["i0"]]["inicio"])
        if salida[k]["i1"] < salida[k]["i0"]:
            salida[k]["i1"] = salida[k]["i0"]
            salida[k]["t1"] = float(palabras[salida[k]["i1"]]["fin"])
    return salida


def tramos_de_frase(frases: list[dict], palabras: list[dict], dur: float) -> list[tuple[float, float]]:
    """Cada frase ocupa de donde termina la anterior a donde empieza la siguiente (punto medio de la pausa): tapan todo el anuncio de 0 a `dur`."""
    limites = [0.0]
    for a, b in zip(frases, frases[1:]):
        fin_a = float(palabras[a["i1"]]["fin"])
        ini_b = float(palabras[b["i0"]]["inicio"])
        limites.append(round((fin_a + ini_b) / 2, 3) if ini_b >= fin_a else round(ini_b, 3))
    limites.append(round(dur, 3))
    for k in range(1, len(limites)):                        # nunca hacia atras (palabras de Whisper que se montan), y todo dentro del anuncio
        limites[k] = round(min(max(limites[k], limites[k - 1] + 0.05), dur), 3)
    return [(limites[k], max(limites[k + 1], limites[k])) for k in range(len(frases))]


def hacer_cortes(frases: list[dict], palabras: list[dict], dur: float, corte_min: float, corte_max: float) -> list[dict]:
    """Los cortes del video: [{t0, t1, frase}] contiguos de 0 a `dur`, cada uno entre corte_min y corte_max segundos y cortando en pausas entre palabras."""
    objetivo = (corte_min + corte_max) / 2
    spans = tramos_de_frase(frases, palabras, dur)
    cortes: list[dict] = []
    for fi, (a, b) in enumerate(spans):
        d = b - a
        n = max(1, round(d / objetivo))
        while n > 1 and d / n < corte_min * 0.9:
            n -= 1
        while d / n > corte_max:
            n += 1
        # los huecos entre palabras de esta frase: ahi se puede cortar sin partir una palabra
        f = frases[fi]
        huecos = []
        for k in range(f["i0"], f["i1"]):
            ini, fin = float(palabras[k]["fin"]), float(palabras[k + 1]["inicio"])
            huecos.append((ini + fin) / 2)
        limites = [a]
        for j in range(1, n):
            ideal = a + d * j / n
            validos = [h for h in huecos if h > limites[-1] + corte_min * 0.8 and h < b - corte_min * 0.8 and abs(h - ideal) <= 0.45 * d / n]
            limites.append(round(min(validos, key=lambda h: abs(h - ideal)) if validos else ideal, 3))
        limites.append(b)
        for x, y in zip(limites, limites[1:]):
            cortes.append({"t0": round(x, 3), "t1": round(y, 3), "frase": fi})
    # un corte demasiado corto se pega al de al lado
    cambio = True
    while cambio and len(cortes) > 1:
        cambio = False
        for k, c in enumerate(cortes):
            if c["t1"] - c["t0"] >= corte_min * 0.75:
                continue
            vecino = k + 1 if k + 1 < len(cortes) else k - 1
            otro = cortes[vecino]
            if (c["t1"] - c["t0"]) + (otro["t1"] - otro["t0"]) > corte_max * 1.25:
                continue
            nuevo = {"t0": min(c["t0"], otro["t0"]), "t1": max(c["t1"], otro["t1"]), "frase": c["frase"] if (c["t1"] - c["t0"]) >= (otro["t1"] - otro["t0"]) else otro["frase"]}
            lo = min(k, vecino)
            cortes[lo:lo + 2] = [nuevo]
            cambio = True
            break
    return cortes


# --------------------------------------------------------------------------------------------------------------------------------------------
# guion (opcional) y consultas de CLIP
def leer_guion(ruta: Path | None) -> dict | None:
    """guion.json de la etapa S3. Formato que se lee (todo lo que no este se ignora):
    {"frases": [{"texto": "...", "rol": "gancho|problema|giro|prueba|oferta|llamada", "visual": {"en": "close-up of ...", "etiquetas": ["manos"], "producto": true},
                 "textoGrande": "30 DINÁMICAS", "inicio": 0.0, "fin": 2.1}], "cta": {"texto": "...", "subtexto": "..."}}"""
    if not ruta or not ruta.is_file():
        return None
    try:
        g = leer_json(ruta)
    except ValueError:
        return None
    crudas = g.get("frases") or g.get("escenas") or g.get("scenes") or []
    frases = []
    for f in crudas:
        if isinstance(f, str):
            f = {"texto": f}
        texto = f.get("texto") or f.get("es") or f.get("text") or ""
        if not str(texto).strip():
            continue
        v = f.get("visual") if isinstance(f.get("visual"), dict) else {}
        frases.append({
            "texto": str(texto).strip(), "rol": f.get("rol") or f.get("role") or "",
            "visual_en": str(v.get("en") or f.get("visual_en") or f.get("visualEn") or (f.get("visual") if isinstance(f.get("visual"), str) else "") or "").strip(),
            "etiquetas": [str(x) for x in (v.get("etiquetas") or f.get("etiquetas") or [])],
            "texto_grande": str(f.get("textoGrande") or f.get("texto_grande") or "").strip(),
            "producto": bool(v.get("producto") if "producto" in v else f.get("mostrarProducto", f.get("producto", False))),
            "inicio": f.get("inicio"), "fin": f.get("fin"),
        })
    return {"frases": frases, "cta": g.get("cta") if isinstance(g.get("cta"), dict) else {}}


def normalizar_filas(m):
    import numpy as np

    return m / np.maximum(np.linalg.norm(m, axis=-1, keepdims=True), 1e-8)


NEGADOS = re.compile(r"\b(?:ni|sin)\s+(?:(?:un|una|el|la|los|las|sola|solo)\s+){0,2}\w+(?:\s+(?:de|del)\s+\w+)?", re.IGNORECASE)


def sin_negados(texto: str) -> str:
    """El texto sin lo que NIEGA («ni aguja, ni hilo, ni maquina de coser», «sin coser», «ni una sola puntada»). CLIP busca las palabras, no el sentido: para «ni aguja» ensenaba una mano cosiendo con aguja
    (en FIELTRO, el gancho «no llevan ni una sola puntada» y el «todo sin coser» salieron sobre alguien cosiendo, justo lo contrario de lo que promete el anuncio). Quedan las demas palabras de la
    frase; si todo era negacion, devuelve vacio."""
    limpio = NEGADOS.sub(" ", texto)
    limpio = re.sub(r"\s+([,.;:!?])", r"\1", limpio)
    limpio = re.sub(r"([,;:])\s*(?=[,.;:!?])", "", limpio)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip(" ,;:")
    return limpio if re.search(r"\w", limpio) else ""


def construir_consultas(motor, frases: list[dict], cortes: list[dict], palabras: list[dict], temas: list[str]) -> list[dict]:
    """Un vector de CLIP por corte: la frase entera (lo que se dice), el pedazo que cae en ese corte, la descripcion visual en ingles del guion si hay,
    y un poco del tema de la oferta (para que una frase abstracta como «pero espera, hay mas» siga con tomas del tema)."""
    import numpy as np

    textos_frase = [sin_negados(f["texto"]) or (temas[0] if temas else f["texto"]) for f in frases]          # lo que la frase niega no se busca: si toda era negacion, se busca el tema de la oferta
    visuales = [(f.get("guion") or {}).get("visual_en", "") for f in frases]
    fragmentos = []
    for c in cortes:
        pal = [str(p["texto"]).strip() for p in palabras if c["t0"] - 1e-6 <= float(p["inicio"]) < c["t1"]]
        fragmentos.append(sin_negados(" ".join(pal)) or textos_frase[c["frase"]])
    todos = list(dict.fromkeys(textos_frase + [v for v in visuales if v] + fragmentos + temas))
    vec = dict(zip(todos, motor.textos(todos)))
    vf = normalizar_filas(np.stack([vec[t] for t in textos_frase]))
    tema = vf.mean(axis=0)
    if temas:
        tema = tema + 0.5 * np.stack([vec[t] for t in temas]).mean(axis=0)
    tema = tema / max(float(np.linalg.norm(tema)), 1e-8)
    consultas = []
    for k, c in enumerate(cortes):
        fi = c["frase"]
        if visuales[fi]:
            q = 0.60 * vec[visuales[fi]] + 0.20 * vf[fi] + 0.20 * tema
        else:
            q = 0.55 * vf[fi] + 0.25 * vec[fragmentos[k]] + 0.20 * tema
        q = q / max(float(np.linalg.norm(q)), 1e-8)
        consultas.append({"vec": q, "dur": round(c["t1"] - c["t0"], 3), "gancho": k == 0, "texto": fragmentos[k], "frase": fi})
    return consultas


# --------------------------------------------------------------------------------------------------------------------------------------------
# gancho nuevo con IA (etapa S5b: scripts/gancho_ia.py deja anuncios/<p>/<tipo>/<AD>/gancho/gancho.json)
def ruta_media(rel: str) -> Path:
    """La ruta de un archivo de videoData: relativa a public/ o, si no esta ahi, a la raiz del editor (anuncios/...)."""
    p = PUBLIC / rel
    return p if p.is_file() else RAIZ / rel


def leer_gancho(base: Path, receta: dict) -> dict | None:
    """El gancho hecho con IA, si la receta lo pide (`gancho.modo` = nuevo-modelado) y esta completo o parcial: hasta 3 tomas nuestras (imagenes quietas o clips de IA), la frase y el
    texto grande. Formato (version 1) en la skill editor-video, «Gancho nuevo con IA». Si falta algo, el montaje sigue con b-roll como siempre."""
    ruta = base / "gancho" / "gancho.json"
    if not ruta.is_file() or val(receta, "gancho.modo", "nuevo-modelado") != "nuevo-modelado":
        return None
    try:
        g = leer_json(ruta)
    except ValueError:
        return None
    if g.get("estado") not in ("completo", "parcial"):
        return None
    tomas = []
    for t in sorted((x for x in (g.get("tomas") or []) if isinstance(x, dict) and x.get("src")), key=lambda x: x.get("orden", 0))[:3]:
        archivo = ruta_media(str(t["src"]))
        if archivo.is_file():
            from _comun import duracion_seg

            tomas.append({**t, "_dur_archivo": duracion_seg(archivo)})
    if not tomas:
        return None
    return {"tomas": tomas, "frase": str(g.get("frase") or ""), "texto_grande": str(g.get("texto_grande") or "").strip()}


def tomas_del_gancho(gancho: dict, cortes: list[dict]) -> list[dict]:
    """Reparte las tomas del gancho en los primeros cortes, en orden. Una imagen quieta (ya convertida a video) cubre UN corte; un clip de IA cubre los cortes seguidos que haga falta hasta
    llenar su `duracion_sugerida` (cada corte toma el tramo que sigue del mismo clip, sin repetir): pagar un clip de 6 s para ver solo el primer corte seria tirar el resto. Si el archivo dura
    menos que el corte se le baja la velocidad hasta un 25 %; si dura mucho menos, se salta (ese corte lo cubre el b-roll). Devuelve las elecciones ya hechas, con la forma de `emparejar`."""
    fijas: list[dict] = []
    k = 0                                                       # el corte que sigue
    for t in gancho["tomas"]:
        if k >= len(cortes):
            break
        base, archivo = float(t.get("desde") or 0.0), float(t["_dur_archivo"])
        sugerida = float(t.get("duracion_sugerida") or 0.0)
        es_clip = str(t.get("tipo") or "") == "clip"
        usado = 0.0                                             # segundos del archivo que ya se repartieron en cortes
        while k < len(cortes):
            necesita = round(cortes[k]["t1"] - cortes[k]["t0"], 3)
            desde = base + usado
            disponible = archivo - desde
            if disponible >= necesita - 0.05:
                vel, gasta = 1.0, necesita
            elif disponible >= 0.75 * necesita:
                vel, gasta = round(disponible / necesita, 3), disponible        # un poco mas lenta para que alcance
            else:
                break
            e = {"ruta": str(t["src"]).replace("\\", "/"), "video": Path(str(t["src"])).name, "desde": round(desde, 3), "dur": necesita, "z": 0.0, "sim": 0.0, "puntaje": 0.0,
                 "calidad": "gancho IA", "que_se_ve": str(t.get("que_muestra") or "gancho nuevo con IA")[:80], "relajada": 0, "zoom": float(t.get("zoom") or 1.0),
                 "ajuste": str(t.get("ajuste") or "cover"), "velocidad": vel, "origen": "gancho-ia"}
            if usado > 0.0:
                e["continuacion"] = True                        # tramo SEGUIDO del mismo clip: no es un corte de verdad, y la receta no le pone transicion
            fijas.append(e)
            usado += gasta
            k += 1
            if not es_clip or usado >= sugerida - 0.5:
                break
        if usado == 0.0:
            break                                               # ni el primer corte cupo: el resto lo cubre el b-roll
    return fijas


# --------------------------------------------------------------------------------------------------------------------------------------------
# textos grandes
def ancla_de(planos: list[str]) -> tuple[int, str, float]:
    """La palabra de la frase que mas vende: una cifra, la garantia, algo gratis, un beneficio, el producto. (indice, motivo, puntos); -1 si no hay."""
    mejor = (-1, "", 0.0)
    for i, t in enumerate(planos):
        if re.search(r"\d", t) or t in NUMEROS:
            c = (i, "cifra", 5.0)
        elif t in GARANTIA:
            c = (i, "garantia", 4.0)
        elif t in OFERTA:
            c = (i, "oferta", 3.0)
        elif t in BENEFICIO:
            c = (i, "beneficio", 2.5)
        elif t in PRODUCTO_PAL:
            c = (i, "producto", 1.5)
        else:
            continue
        if c[2] > mejor[2]:
            mejor = c
    return mejor


def trozo_clave(originales: list[str], planos: list[str], i: int, maximo: int = 3) -> tuple[int, int]:
    """El pedazo corto (hasta `maximo` palabras) de la frase que se pone en grande, desde la palabra `i`: sigue mientras haya palabras con contenido
    (deja pasar una palabrita como «de» o «a» en medio si despues viene contenido) y no termina en palabrita. Devuelve (desde, hasta) incluidos."""
    n = len(planos)
    j = i
    contenido = 1
    while j + 1 < n and contenido < maximo:
        sig = planos[j + 1]
        if sig in PARADA:
            if j + 2 < n and planos[j + 2] not in PARADA and contenido + 1 < maximo + 1 and (j + 2 - i + 1) <= maximo + 1:
                j += 2
                contenido += 1
                continue
            break
        j += 1
        contenido += 1
    while j > i and planos[j] in PARADA:
        j -= 1
    if j == i and i > 0 and planos[i] not in NUMEROS and not re.search(r"\d", planos[i]) and planos[i - 1] not in PARADA:
        return i - 1, j                                   # una sola palabra al final: se le suma la anterior
    return i, j


def _misma_palabra(a: str, b: str) -> bool:
    """Dos palabras planas que son la misma, o una el plural/derivado corto de la otra («receta»/«recetas»). Las cifras solo coinciden exactas: «30» no es «300»."""
    if a == b:
        return True
    if a.isdigit() or b.isdigit() or min(len(a), len(b)) < 4:
        return False
    return (a.startswith(b) or b.startswith(a)) and abs(len(a) - len(b)) <= 3


def texto_grande_por_frase(frases: list[dict], palabras: list[dict]) -> list[str]:
    """El `textoGrande` que el guion trae para cada frase, puesto en la frase donde la voz de verdad dice sus palabras (lista de un texto por frase, «» si no hay).
    El modelo que escribe el guion a veces pone el texto grande una frase ANTES de la que lo dice (el 102 de RECETAS NAVIDAD: «300 RECETAS» salia mientras la voz decia «Pero puedes cambiar eso
    hoy mismo…» y «CENAS EN 30 MINUTOS» una frase antes de nombrar los 30 minutos). Un texto solo se mueve a la frase de al lado cuando casi nada de el se dice en la suya (menos de la mitad de sus
    palabras con contenido) y al menos tres cuartas partes se dicen en esa vecina; un texto que resume («RECETAS NAVIDAD») o cuyas palabras no se dicen en ningun lado se queda donde lo puso el guion."""
    n = len(frases)
    dichas = [{plano(palabras[k]["texto"]) for k in range(f["i0"], f["i1"] + 1)} - {""} for f in frases]

    def cuanto_se_dice(claves: list[str], k: int) -> float:
        return sum(any(_misma_palabra(c, w) for w in dichas[k]) for c in claves) / len(claves)

    propios: list[list[str]] = [[] for _ in frases]                     # los que ya estaban en la frase que los dice (o no se pudieron comparar)
    movidos: list[list[str]] = [[] for _ in frases]
    for fi, f in enumerate(frases):
        texto = str((f.get("guion") or {}).get("texto_grande") or "").strip()
        if not texto:
            continue
        claves = [t for t in tokens_de(texto) if t not in PARADA]
        destino = fi
        if claves and cuanto_se_dice(claves, fi) < 0.5:
            mejor, k = max(((cuanto_se_dice(claves, v), v) for v in (fi - 1, fi + 1) if 0 <= v < n), default=(0.0, fi))
            if mejor >= 0.75:
                destino = k
        (propios if destino == fi else movidos)[destino].append(texto)
    return [(propios[k] or movidos[k] or [""])[0] for k in range(n)]


def candidatos_texto(frases: list[dict], palabras: list[dict]) -> list[dict]:
    """Un candidato de texto grande por frase: {frase, texto, t0, t1, puntos, motivo}. Si el guion trae `textoGrande` para la frase, ese texto manda
    (puesto en la frase donde la voz lo dice: ver `texto_grande_por_frase`)."""
    out = []
    de_guion = texto_grande_por_frase(frases, palabras)
    for fi, f in enumerate(frases):
        idx = list(range(f["i0"], f["i1"] + 1))
        if de_guion[fi]:
            out.append({"frase": fi, "texto": de_guion[fi], "t0": float(palabras[idx[0]]["inicio"]), "t1": float(palabras[idx[-1]]["fin"]),
                        "puntos": 9.0, "motivo": "guion"})
            continue
        if fi == 0:                                        # el gancho: el texto tiene que salir en los primeros segundos, no al final de la frase
            primeros = [k for k in idx if float(palabras[k]["inicio"]) <= 3.2]
            if len(primeros) >= 2:
                idx = primeros
        originales = [str(palabras[k]["texto"]) for k in idx]
        planos = [plano(x) for x in originales]
        i, motivo, puntos = ancla_de(planos)
        if i < 0:
            cont = [k for k, t in enumerate(planos) if t and t not in PARADA]
            if len(cont) < 2:
                continue
            # la racha mas larga de palabras con contenido
            mejor, actual = (cont[0], cont[0]), (cont[0], cont[0])
            for a, b in zip(cont, cont[1:]):
                actual = (actual[0], b) if b == actual[1] + 1 else (b, b)
                if actual[1] - actual[0] > mejor[1] - mejor[0]:
                    mejor = actual
            i, motivo, puntos = mejor[0], "tema", 0.5
        a, b = trozo_clave(originales, planos, i, 4 if motivo == "cifra" else 3)
        texto = limpio_texto(" ".join(originales[a:b + 1]))
        if len(texto) < 3 or len(texto) > 30:
            continue
        largo = f["i1"] - f["i0"] + 1
        puntos += 0.3 if 4 <= largo <= 14 else -0.3
        if fi == 0:
            puntos += 2.0                                  # la primera frase es el gancho
        out.append({"frase": fi, "texto": texto, "t0": float(palabras[idx[a]]["inicio"]), "t1": float(palabras[idx[b]]["fin"]), "puntos": puntos, "motivo": motivo})
    return out


def elegir_textos_grandes(frases: list[dict], palabras: list[dict], dur: float, receta: dict, cta_inicio: float, color_primario: str, texto_gancho: str = "") -> list[dict]:
    """De 5 a 7 textos grandes (lo que pide la receta): el gancho al inicio y las frases que mas venden, separados al menos 3 segundos y sin tocar el boton final.
    `texto_gancho` (el `texto_grande` de gancho/gancho.json) manda sobre lo que las reglas sacarian de la primera frase."""
    tmin, tmax = int(val(receta, "textosGrandes.min", 5)), int(val(receta, "textosGrandes.max", 7))
    objetivo = max(tmin, min(tmax, round(dur / 8)))
    cands = candidatos_texto(frases, palabras)
    if texto_gancho and palabras:
        t0 = float(palabras[0]["inicio"])
        # por palabras completas, nunca a mitad de una (con [:30] «…treinta minutos» salio «TREINTA MIN»); si es largo, Remotion achica la letra para que quepa
        cands = [{"frase": 0, "texto": recortar_por_palabras(texto_gancho, MAX_LETRAS_TEXTO_GANCHO), "t0": t0, "t1": t0 + 1.5, "puntos": 10.0, "motivo": "guion"}] + [c for c in cands if c["t0"] > t0 + 3.0 or c["frase"] != 0]
    elegidos: list[dict] = []

    def cabe(c: dict, sep: float) -> bool:
        ini = max(0.0, c["t0"] - 0.05)
        if ini + 1.1 > cta_inicio - 0.1 or ini + 1.1 > dur - 0.1:
            return False
        separacion = min(sep, 2.2) if c.get("motivo") == "guion" else sep         # el texto que pide el guion no se descarta por 0,8 s de diferencia
        return all(abs(ini - e["t0"]) >= separacion for e in elegidos)

    hook_pedido = bool(val(receta, "gancho.textoGrande", True)) and val(receta, "gancho.modo", "nuevo-modelado") != "sin-gancho"
    if hook_pedido:
        for c in cands:
            if c["t0"] <= 3.5:
                elegidos.append(c)
                break
    for sep in (3.0, 2.2):
        for c in sorted(cands, key=lambda c: (-c["puntos"], c["t0"])):
            if len(elegidos) >= objetivo:
                break
            if c in elegidos or not cabe(c, sep):
                continue
            elegidos.append(c)
        if len(elegidos) >= tmin:
            break
    elegidos.sort(key=lambda c: c["t0"])
    textos = []
    for k, c in enumerate(elegidos):
        ini = round(max(0.0, c["t0"] - 0.05), 2)
        largo = min(2.4, max(1.2, (c["t1"] - c["t0"]) + 0.5))
        fin = round(min(ini + largo, cta_inicio - 0.1, dur - 0.1), 2)
        estilo, posicion = ESTILOS_TEXTO[k % len(ESTILOS_TEXTO)]
        item = {"texto": c["texto"], "inicio": ini, "fin": fin, "estilo": estilo, "posicion": posicion}
        item["color"] = "#FFE14D" if estilo == "resaltado" else color_primario
        textos.append(item)
    return textos


# --------------------------------------------------------------------------------------------------------------------------------------------
# producto (portada y paginas del ebook) y boton final
def menciones_producto(palabras: list[dict], nombre: str) -> list[float]:
    """Segundos en que se nombra el producto (su nombre, ebook, guia, recetario, curso...)."""
    propios = {plano(t) for t in str(nombre).split() if plano(t) and plano(t) not in PARADA and len(plano(t)) > 3}
    claves = PRODUCTO_PAL | propios
    return [float(w["inicio"]) for w in palabras if plano(w["texto"]) in claves]


def paginas_disponibles(carpeta: Path, ficha: dict, cuantas: int) -> list[str]:
    """Las paginas del ebook que se muestran: las que dice producto.json (`mejores_paginas_ebook`) o, si no, repartidas entre las que hay."""
    todas = sorted((carpeta / "paginas").glob("p*.jpg")) if (carpeta / "paginas").is_dir() else []
    if not todas or cuantas <= 0:
        return []
    elegidas: list[Path] = []
    for x in ficha.get("mejores_paginas_ebook") or []:
        cand = carpeta / "paginas" / (f"p{int(x):03d}.jpg" if str(x).isdigit() else str(x))
        if cand.is_file() and cand not in elegidas:
            elegidas.append(cand)
    if len(elegidas) < cuantas:
        saltos = [todas[min(len(todas) - 1, int(len(todas) * f))] for f in (0.25, 0.5, 0.75)]
        for s in saltos:
            if s not in elegidas:
                elegidas.append(s)
    return [f"productos/{carpeta.name}/paginas/{p.name}" for p in elegidas[:cuantas]]


def lugares_producto(palabras: list[dict], dur: float, textos: list[dict], cta: dict | None, receta: dict, carpeta: Path, ficha: dict) -> list[dict]:
    """Cuando mostrar la portada y las paginas: al nombrar el producto; si nunca se nombra, a mitad del anuncio. Nunca encima de un texto grande ni del boton final. Si producto.json trae `no_mostrar_portada`, solo paginas."""
    sin_portada = bool(ficha.get("no_mostrar_portada"))          # la portada promete mas de lo que trae (FIELTRO: «20 Proyectos» y el ebook trae 14 adornos): se muestran paginas de adentro, una mas para que el producto salga el mismo tiempo
    if not val(receta, "producto.mostrar", True) or (not sin_portada and not (carpeta / "portada.jpg").is_file()):
        return []
    n_pag = int(val(receta, "producto.paginas", 2)) + (1 if sin_portada else 0)
    imagenes = ([] if sin_portada else [(f"productos/{carpeta.name}/portada.jpg", 1.7, -5)]) + [(p, 1.4, 4 if k % 2 == 0 else -3) for k, p in enumerate(paginas_disponibles(carpeta, ficha, n_pag))]
    if not imagenes:
        return []
    mencion = menciones_producto(palabras, ficha.get("nombre", ""))
    deseados: list[float] = []
    for k in range(len(imagenes)):
        if k < len(mencion) and (not deseados or mencion[k] - deseados[-1] >= 3.5):
            deseados.append(mencion[k])
        else:
            base = [0.34, 0.50, 0.64, 0.76, 0.86][min(k, 4)] * dur
            deseados.append(max(base, (deseados[-1] + 3.5) if deseados else 0.0))
    ocupado = [(t["inicio"], t["fin"]) for t in textos] + ([(cta["inicio"], cta["inicio"] + cta["duracion"])] if cta else [])
    out: list[dict] = []
    for (src, largo, giro), t in zip(imagenes, deseados):
        ini = max(0.4, t)
        for _ in range(6):
            choque = [b for a, b in ocupado if ini < b + 0.15 and ini + largo > a - 0.15]
            if not choque:
                break
            ini = max(choque) + 0.2
        if ini + largo > (cta["inicio"] if cta else dur) - 0.1 or ini + largo > dur:
            continue
        out.append({"src": src, "inicio": round(ini, 2), "duracion": largo, "modo": "tarjeta", "posicion": "centro", "giro": giro})
        ocupado.append((ini, ini + largo))
    return out


def boton_final(receta: dict, guion: dict | None, dur: float, texto_arg: str, sub_arg: str) -> dict:
    """El boton de la llamada a la accion: lo que pide la receta o el guion; si nadie dijo nada, «Pídelo ahora» (nada de promesas que no se verificaron)."""
    seg = float(val(receta, "cta.segundos", 2.2))
    g = (guion or {}).get("cta") or {}
    texto = texto_arg or val(receta, "cta.texto", "") or g.get("texto") or "Pídelo ahora"
    sub = sub_arg or val(receta, "cta.subtexto", "") or g.get("subtexto") or ""
    cta = {"texto": str(texto), "inicio": round(max(0.0, dur - seg), 2), "duracion": seg}
    if sub:
        cta["subtexto"] = str(sub)
    return cta


# --------------------------------------------------------------------------------------------------------------------------------------------
# videoData.json
def color_primario(historial_previo: dict | None) -> str:
    anterior = ((historial_previo or {}).get("colores") or {}).get("primario")
    for c in PALETA:
        if c != anterior:
            return c
    return PALETA[0]


def armar_videodata(producto: str, ad: str, dur: float, palabras: list[dict], cortes: list[dict], elegidas: list[dict], textos: list[dict], prod: list[dict],
                    cta: dict, receta: dict, voz_src: str, musica: dict | None, color: str) -> dict:
    tomas = []
    for k, (c, e) in enumerate(zip(cortes, elegidas)):
        toma = {"src": e["ruta"], "desde": e["desde"], "inicio": round(c["t0"], 3), "duracion": round(c["t1"] - c["t0"], 3), "ajuste": e.get("ajuste", "cover"), "posX": 0.5,
                "zoom": e.get("zoom", 1.05), "nota": (f"gancho IA · {e.get('que_se_ve', '')[:50]}" if e.get("origen") == "gancho-ia" else f"{e.get('que_se_ve', '')[:60]} · z={e['z']:+.1f}")}
        if e.get("velocidad") and abs(float(e["velocidad"]) - 1.0) > 1e-6:
            toma["velocidad"] = e["velocidad"]
        if e.get("continuacion"):
            toma["continuacion"] = True
        tomas.append(toma)
    sub = val(receta, "subtitulos", {})
    audio: dict = {"voz": {"src": voz_src, "volumen": 1}, "sfx": []}
    if musica:
        audio["musica"] = musica
    return {
        "meta": {"anuncio": ad, "producto": producto, "tipo": "tiktok", "fps": 30, "ancho": 1080, "alto": 1920, "duracion": round(dur, 2)},
        "estilo": {"fondo": "#000000", "colorPrimario": color,
                   "subtitulos": {"tamano": int(sub.get("tamano", 82)), "color": sub.get("color", "#FFFFFF"), "colorActivo": sub.get("colorActivo", "#FFE14D"), "contorno": "#000000",
                                  "maxPalabras": int(sub.get("palabrasPorVez", 3)), "mayusculas": bool(sub.get("mayusculas", True))},
                   "textoGrande": {"tamano": 112}},
        "zonaInferiorLibre": ZONA_INFERIOR,
        "tomas": tomas,
        "palabras": [{"texto": str(p["texto"]).strip(), "inicio": p["inicio"], "fin": p["fin"]} for p in palabras],
        "textosGrandes": textos,
        "producto": prod,
        "cta": cta,
        "audio": audio,
    }


# --------------------------------------------------------------------------------------------------------------------------------------------
# entradas: voz, transcripcion, mezcla, musica, historial
def buscar_voz(producto: str, ad: str, base: Path, arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.is_file():
            salir(f"No existe la voz: {p}")
        return p
    for ext in (".mp3", ".wav", ".m4a", ".ogg", ".flac"):
        for c in (PUBLIC / "productos" / producto / "voces" / f"{ad}{ext}", base / "audio" / f"voz{ext}"):
            if c.is_file():
                return c
    salir(f"No encuentro la voz de {ad}. Pon la locucion en public/productos/{producto}/voces/{ad}.mp3 (o pasa --voz archivo).")
    raise SystemExit(1)


def transcripcion_sin_comprobar(t: dict, guion: Path | None) -> bool:
    """Con un guion al lado, solo se fia una transcripcion hecha SIN pista (`pista` = ninguna) o con la pista descartada (descartada). Las que no traen la clave (anteriores) o se hicieron CON el guion de pista (guion)
    pudieron saltarse parte de la voz o torcer palabras (el AD1 de FIELTRO perdio 20 s y escribio «www.mooji.com»; el AD2 salio con «ndaNavidad») y se repiten."""
    if t.get("alineado_con_guion"):          # voz_auto.py: las palabras SON las del guion de la cadena (Whisper solo puso los tiempos); no son de la pista de Whisper
        return False
    return t.get("pista") not in ("ninguna", "descartada") and bool(guion) and guion.is_file()


def obtener_transcripcion(voz: Path, base: Path, idioma: str, guion: Path | None = None, mostrar=print) -> dict:
    """transcripcion.json de la voz: la que ya esta (si es mas nueva que la voz y es de fiar) o la que saca Whisper local (gratis; tarda como la duracion del audio), SIN el guion de pista (se come o tuerce la voz).
    Con un guion de texto, las palabras que Whisper oyo mal se corrigen con el (solo en memoria: el json queda como lo dio Whisper)."""
    ruta = base / "transcripcion.json"
    t = None
    if ruta.is_file() and ruta.stat().st_mtime >= voz.stat().st_mtime:
        t = leer_json(ruta)
        if not t.get("palabras") or not es_de_esta_voz(t, voz):          # la de otra voz (una locucion vieja que el montaje prefiere) no se reutiliza aunque sea mas nueva
            t = None
        elif transcripcion_sin_comprobar(t, guion):
            mostrar("La transcripcion que habia pudo hacerse con el guion de pista (Whisper se salta o tuerce parte de la voz): la repito sin pista.")
            t = None
    if t is None:
        mostrar(f"Transcribiendo la voz con Whisper local ({voz.name})...")
        cmd = [sys.executable, str(RAIZ / "scripts" / "transcribir.py"), str(voz), "--salida", str(ruta), "--modelo", "small", "--idioma", idioma]
        r = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace", env={**os.environ, "EDITOR_SIN_VENV": os.environ.get("EDITOR_SIN_VENV", "")})
        if r.returncode != 0 or not ruta.is_file():
            salir(f"No pude transcribir la voz: {(r.stderr or r.stdout).strip()[-300:]}")
        t = leer_json(ruta)
    if guion and guion.is_file() and not t.get("alineado_con_guion"):          # voz_auto.py ya dejo las palabras DEL guion de la cadena: no se «corrigen» con otro texto (un guiones/<AD>.txt viejo)
        palabras, n = trn.corregir_con_guion(t.get("palabras") or [], guion.read_text(encoding="utf-8", errors="replace"))
        if n:
            mostrar(f"Subtitulos corregidos con el guion: {n} palabra(s) que Whisper oyo mal.")
            t = {**t, "palabras": palabras}
    return t


def mezclar(voz: Path, musica: Path | None, base: Path, mostrar=print) -> dict:
    """Voz a -14 LUFS (y la musica 15 dB mas baja) con mezclar_audio.py. Devuelve {voz, musica, mezcla} con rutas relativas a la raiz del proyecto."""
    salida = base / "audio"
    voz_norm, musica_aj, mezcla = salida / "voz_norm.wav", salida / "musica_ajustada.mp3", salida / "mezcla.json"
    fresca = (voz_norm.is_file() and mezcla.is_file() and voz_norm.stat().st_mtime >= voz.stat().st_mtime
              and (musica is None or (musica_aj.is_file() and musica_aj.stat().st_mtime >= musica.stat().st_mtime)))
    if not fresca:
        cmd = [sys.executable, str(RAIZ / "scripts" / "mezclar_audio.py"), "--voz", str(voz), "--salida", str(salida)] + (["--musica", str(musica)] if musica else [])
        r = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            salir(f"mezclar_audio.py fallo: {(r.stderr or r.stdout).strip()[-300:]}")
    rel = lambda p: p.resolve().relative_to(RAIZ.resolve()).as_posix()  # noqa: E731
    return {"voz": rel(voz_norm), "musica": rel(musica_aj) if musica else None, "mezcla": leer_json(mezcla)}


def elegir_musica(receta: dict, arg: str | None, previo: dict | None) -> Path | None:
    """La musica de public/musica/biblioteca/: la que se pide, la del estilo de la receta o la primera que no uso el anuncio anterior. Sin musica si la receta o la biblioteca no la dan."""
    if arg:
        p = Path(arg)
        return p if p.is_file() else None
    estilo = val(receta, "musica.estilo", "segun-oferta")
    if estilo == "sin-musica":
        return None
    biblio = PUBLIC / "musica" / "biblioteca"
    pistas = sorted(p for p in biblio.rglob("*.mp3") if p.is_file() and "_prueba" not in p.parts) if biblio.is_dir() else []
    if not pistas:
        return None
    if estilo != "segun-oferta":
        por_estilo = [p for p in pistas if et.plegar(estilo) in et.plegar(str(p.relative_to(biblio)))]
        pistas = por_estilo or pistas
    anterior = Path(str((previo or {}).get("musica") or "")).name
    return next((p for p in pistas if p.name != anterior), pistas[0])


def historial_previo(producto: str, ad: str) -> dict | None:
    ruta = ANUNCIOS / producto / "historial.json"
    if not ruta.is_file():
        return None
    try:
        previos = [x for x in leer_json(ruta).get("anuncios", []) if x.get("anuncio") != ad]
    except ValueError:
        return None
    return previos[-1] if previos else None


def tomas_de_anuncios_previos(producto: str, ad: str) -> list[tuple]:
    """Los tramos de b-roll (video, desde, hasta) que usaron TODOS los otros anuncios de este producto, no solo el ultimo: en FIELTRO el AD3 solo evitaba lo del AD2 y repitio 14 de sus 18 tomas del AD1."""
    ruta = ANUNCIOS / producto / "historial.json"
    if not ruta.is_file():
        return []
    try:
        previos = [x for x in leer_json(ruta).get("anuncios", []) if x.get("anuncio") != ad]
    except ValueError:
        return []
    return [(t["src"], t["desde"], t["hasta"]) for x in previos for t in x.get("tomas_usadas", []) if all(k in t for k in ("src", "desde", "hasta"))]


# --------------------------------------------------------------------------------------------------------------------------------------------
# evidencia: la toma elegida para cada frase, en una imagen
def cuadro_de(video: Path, t: float):
    """Un fotograma del video en el segundo t (PIL), o None."""
    from PIL import Image

    r = subprocess.run([ejecutable("ffmpeg"), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg",
                        "-q:v", "3", "-"], capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        return None
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def fuente(tam: int):
    from PIL import ImageFont

    for ruta in (r"C:\Windows\Fonts\arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(ruta, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def evidencia_png(ruta: Path, producto: str, ad: str, cortes: list[dict], elegidas: list[dict], frases: list[dict], palabras: list[dict], hook: str, columnas: int = 8) -> None:
    """Una imagen con la toma elegida para cada corte (la franja de 9:16 que se vera), debajo lo que se dice en ese corte; arriba a la izquierda el numero de toma, la frase (F1, F2...
    una frase tiene varios cortes) y el segundo; abajo a la derecha el parecido (z). Borde verde = tramo bueno, ambar = medio, rojo = sin toma. Una persona la juzga en unos segundos:
    lo que se dice contra lo que se ve."""
    from PIL import Image, ImageDraw

    ancho, alto = 150, 267
    celda_w, celda_h = ancho + 12, alto + 78
    filas = (len(cortes) + columnas - 1) // columnas
    hoja = Image.new("RGB", (columnas * celda_w + 12, 64 + filas * celda_h + 8), (24, 24, 28))
    d = ImageDraw.Draw(hoja)
    d.text((14, 10), f"{producto} {ad} - {len(cortes)} tomas, {len(frases)} frases", fill=(255, 225, 77), font=fuente(24))
    d.text((14, 38), f"Gancho: {hook[:110]}", fill=(210, 210, 215), font=fuente(15))
    f_chica, f_tiempo = fuente(13), fuente(14)
    for k, (c, e) in enumerate(zip(cortes, elegidas)):
        x, y = 12 + (k % columnas) * celda_w, 64 + (k // columnas) * celda_h
        borde = (80, 200, 120) if e and e.get("calidad") != "media" else (240, 170, 40)
        if e is None:
            borde = (220, 60, 60)
        d.rectangle([x - 3, y - 3, x + ancho + 3, y + alto + 3], fill=borde)
        im = None
        if e:
            im = cuadro_de(ruta_media(e["ruta"]), e["desde"] + e["dur"] / 2)
        if im is not None:
            x0, y0, rw, rh = et.region_916(*im.size)
            im = im.crop((x0, y0, x0 + rw, y0 + rh)).resize((ancho, alto))
            hoja.paste(im, (x, y))
        else:
            d.rectangle([x, y, x + ancho, y + alto], fill=(60, 30, 30))
            d.text((x + 10, y + 10), "sin toma", fill=(255, 255, 255), font=f_chica)
        d.rectangle([x, y, x + 112, y + 20], fill=(0, 0, 0))
        d.text((x + 4, y + 2), f"#{k + 1} F{c['frase'] + 1} {c['t0']:.1f}s", fill=(255, 225, 77), font=f_tiempo)
        if e:
            d.rectangle([x + ancho - 62, y + alto - 20, x + ancho, y + alto], fill=(0, 0, 0))
            d.text((x + ancho - 58, y + alto - 18), f"z{e['z']:+.1f}", fill=(255, 255, 255), font=f_chica)
        pal = " ".join(str(p["texto"]).strip() for p in palabras if c["t0"] - 1e-6 <= float(p["inicio"]) < c["t1"])
        renglones, actual = [], ""
        for w in pal.split():
            if len(actual) + len(w) + 1 > 21:
                renglones.append(actual)
                actual = w
            else:
                actual = (actual + " " + w).strip()
        renglones.append(actual)
        for j, linea in enumerate(renglones[:4]):
            d.text((x, y + alto + 6 + j * 15), linea, fill=(235, 235, 240), font=f_chica)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(ruta, optimize=True)


def hoja_de_revision(base: Path, producto: str, ad: str, cortes: list[dict], elegidas: list[dict], por_hoja: int = 12) -> list[Path]:
    """Las hojas `revision_tomas_N.png`: de CADA toma, tres fotogramas (a 10 %, 50 % y 90 % de lo que dura) con su numero y el video de donde sale. evidencia.png solo muestra el fotograma del medio y
    un nino, un logo o un subtitulo ajeno que aparece a mitad de una toma no se ve (en FIELTRO se asomaba un nino hacia los 4,8 s de un clip). Hasta `por_hoja` tomas por hoja; borra las de una
    corrida anterior."""
    from PIL import Image, ImageDraw

    for vieja in base.glob("revision_tomas_*.png"):
        vieja.unlink()
    ancho, alto, por_fila = 118, 210, 3
    celda_w, celda_h = 3 * ancho + 22, alto + 36
    f_chica = fuente(13)
    hojas: list[Path] = []
    for n, ini in enumerate(range(0, len(cortes), por_hoja), 1):
        trozo = list(range(ini, min(ini + por_hoja, len(cortes))))
        filas = (len(trozo) + por_fila - 1) // por_fila
        hoja = Image.new("RGB", (por_fila * celda_w + 10, 34 + filas * celda_h + 6), (24, 24, 28))
        d = ImageDraw.Draw(hoja)
        d.text((10, 8), f"{producto} {ad} - inicio, mitad y final de las tomas {trozo[0] + 1} a {trozo[-1] + 1}", fill=(255, 225, 77), font=fuente(18))
        for pos, k in enumerate(trozo):
            e = elegidas[k]
            x0, y0 = 10 + (pos % por_fila) * celda_w, 34 + (pos // por_fila) * celda_h
            d.text((x0, y0), f"#{k + 1} " + (f"{str(e['video'])[-22:]} {e['desde']:.1f}s +{e['dur']:.1f}" if e else "sin toma"), fill=(255, 225, 77), font=f_chica)
            for j, fraccion in enumerate((0.1, 0.5, 0.9)):
                x = x0 + j * (ancho + 4)
                im = cuadro_de(ruta_media(e["ruta"]), e["desde"] + e["dur"] * fraccion) if e else None
                if im is None:
                    d.rectangle([x, y0 + 18, x + ancho, y0 + 18 + alto], fill=(60, 30, 30))
                    continue
                rx, ry, rw, rh = et.region_916(*im.size)
                hoja.paste(im.crop((rx, ry, rx + rw, ry + rh)).resize((ancho, alto)), (x, y0 + 18))
        ruta = base / f"revision_tomas_{n}.png"
        hoja.save(ruta, optimize=True)
        hojas.append(ruta)
    return hojas


def resumen_broll(cat, dur: float, tope: float, cantidad: int = 1) -> dict:
    """Cuanto b-roll limpio hay frente a lo que piden los anuncios. Con el tope de segundos por video de otras personas, un anuncio de `dur` s necesita al menos ceil(dur / tope) videos distintos, y los
    `cantidad` anuncios de la receta, ese numero por cada uno si no quieren repetir videos (en FIELTRO: 11 videos limpios para 3 anuncios que pedian 27: los tres se parecieron y AD2 repitio 5 tomas
    de AD1). Devuelve {videos, segundos, por_anuncio, cantidad, faltan}."""
    utiles = [v for v in cat.videos if float(v.estricto.sum()) * v.cada >= 1.5]
    por_anuncio = max(1, math.ceil(dur / tope)) if tope and tope > 0 else 1
    cantidad = max(1, int(cantidad))
    return {"videos": len(utiles), "segundos": round(cat.segundos_limpios()), "por_anuncio": por_anuncio, "cantidad": cantidad, "faltan": max(0, cantidad * por_anuncio - len(utiles))}


# --------------------------------------------------------------------------------------------------------------------------------------------
# render y control de calidad
def ram_libre_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("largo", ctypes.c_ulong), ("carga", ctypes.c_ulong), ("total_fisica", ctypes.c_ulonglong), ("libre_fisica", ctypes.c_ulonglong),
                            ("total_pagina", ctypes.c_ulonglong), ("libre_pagina", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong), ("libre_virtual", ctypes.c_ulonglong),
                            ("libre_ext", ctypes.c_ulonglong)]
            m = MEM()
            m.largo = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.libre_fisica / 1073741824
        for linea in open("/proc/meminfo", encoding="utf-8"):
            if linea.startswith("MemAvailable:"):
                return int(linea.split()[1]) / 1048576
    except Exception:  # noqa: BLE001
        pass
    return 4.0


def estimar_render_min(dur: float, concurrencia: int) -> tuple[int, int]:
    """Cuanto tarda el render. Si en este PC ya se midio uno con los mismos procesos (.cache/render_medido.json), se usa lo medido; si no, una banda ancha para el PC del dueno
    (2 nucleos, sin GPU): de 7 a 20 minutos por minuto de anuncio (de 10 a 26 con un solo proceso) mas el empaquetado de Remotion; depende de cuanto este usando el PC."""
    ruta = RAIZ / ".cache" / "render_medido.json"
    try:
        m = leer_json(ruta) if ruta.is_file() else {}
        if m.get("concurrencia") == concurrencia and float(m.get("seg_por_seg", 0)) > 0:
            t = float(m["seg_por_seg"]) * dur / 60
            return max(1, round(t * 0.8)), max(2, round(t * 1.25))
    except (ValueError, OSError, TypeError):
        pass
    por_min = (7.0, 20.0) if concurrencia >= 2 else (10.0, 26.0)       # el 2026-10-20 con el PC casi sin memoria libre: 21,6 min para 50 s (26 min por minuto de anuncio)
    return round(dur / 60 * por_min[0] + 1.5), round(dur / 60 * por_min[1] + 3)


def anotar_render_medido(dur: float, concurrencia: int, seg: float) -> None:
    try:
        escribir_json(RAIZ / ".cache" / "render_medido.json", {"concurrencia": concurrencia, "seg_por_seg": round(seg / max(dur, 1.0), 2), "medido": datetime.now().isoformat(timespec="seconds")})
    except OSError:
        pass


def renderizar(ruta_vd: Path, concurrencia: int, log: Path, mostrar=print) -> tuple[Path, float]:
    """Renderiza con remotion.mjs (nunca pisa un video entregado). Devuelve (mp4, segundos)."""
    cmd = [ejecutable("node"), str(RAIZ / "scripts" / "remotion.mjs"), "render", str(ruta_vd), "--concurrencia", str(concurrencia)]
    t0 = time.time()
    salida: list[str] = []
    proc = subprocess.Popen(cmd, cwd=RAIZ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    ultimo = -10
    for linea in proc.stdout:  # type: ignore[union-attr]
        salida.append(linea)
        m = re.match(r"\s*(\d+) %", linea)
        if m and int(m.group(1)) >= ultimo + 10:
            ultimo = int(m.group(1))
            mostrar(f"    render {ultimo} % ({time.time() - t0:.0f} s)")
    proc.wait()
    texto = "".join(salida)
    log.write_text(texto, encoding="utf-8")
    if proc.returncode != 0:
        salir(f"El render fallo (codigo {proc.returncode}): {texto.strip()[-400:]}. Si dice «timed out»/memoria, repite con --concurrencia 1 y cierra otros programas.")
    m = re.search(r"LISTO en \d+ s -> (.+?) \(", texto)
    if not m:
        salir("El render termino pero no dijo donde dejo el video (mira " + str(log) + ")")
    return Path(m.group(1)), time.time() - t0


def medir_sonoridad(ruta: Path) -> float | None:
    r = subprocess.run([ejecutable("ffmpeg"), "-hide_banner", "-nostats", "-i", str(ruta), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.findall(r"^\s+I:\s+(-?\d+(?:\.\d+)?) LUFS", r.stderr, re.M)
    return float(m[-1]) if m else None


def cuadros_negros(ruta: Path) -> list[tuple[float, float]]:
    r = subprocess.run([ejecutable("ffmpeg"), "-hide_banner", "-nostats", "-i", str(ruta), "-vf", "blackdetect=d=0.15:pic_th=0.98:pix_th=0.10", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:(\d+(?:\.\d+)?) black_end:(\d+(?:\.\d+)?)", r.stderr)]


def tomas_repetidas(tomas: list[dict]) -> list[tuple[int, int]]:
    """Pares de tomas (numeradas desde 1) que usan el mismo tramo del mismo video."""
    out = []
    for i in range(len(tomas)):
        for j in range(i + 1, len(tomas)):
            a, b = tomas[i], tomas[j]
            if a["src"] != b["src"]:
                continue
            a0, a1 = float(a["desde"]), float(a["desde"]) + float(a["duracion"]) * float(a.get("velocidad", 1) or 1)
            b0, b1 = float(b["desde"]), float(b["desde"]) + float(b["duracion"]) * float(b.get("velocidad", 1) or 1)
            if a0 < b1 - 0.05 and b0 < a1 - 0.05:
                out.append((i + 1, j + 1))
    return out


def tiempos_de_muestra(vd: dict, n_sub: int = 8) -> tuple[list[float], list[float]]:
    """Segundos del video final donde mirar: (con subtitulo y sin texto grande, con texto grande o boton final)."""
    ocultos = [(t["inicio"], t["fin"]) for t in vd.get("textosGrandes", [])]
    pal = vd.get("palabras") or []
    libres = [(float(p["inicio"]) + float(p["fin"])) / 2 for p in pal if not any(a - 0.2 <= (float(p["inicio"]) + float(p["fin"])) / 2 <= b + 0.2 for a, b in ocultos)]
    if cta := vd.get("cta"):
        libres = [t for t in libres if t < cta["inicio"] - 0.2]
    paso = max(1, len(libres) // n_sub)
    subs = libres[::paso][:n_sub]
    extra = [(t["inicio"] + t["fin"]) / 2 for t in vd.get("textosGrandes", [])[:3]]
    if cta := vd.get("cta"):
        extra.append(cta["inicio"] + cta["duracion"] / 2)
    return subs, extra


def segundos_por_video_ajeno(tomas: list[dict]) -> dict[str, dict]:
    """De cada video de otra persona (de TikTok) que usa el anuncio: los segundos que se usan EN TOTAL del video de origen y los tramos. Sale del videoData (lo que de verdad se renderiza)."""
    uso: dict[str, dict] = {}
    for t in tomas:
        nombre = Path(str(t.get("src", ""))).name
        if not et.es_ajeno(nombre):
            continue
        seg = float(t.get("duracion", 0)) * float(t.get("velocidad") or 1.0)
        d0 = float(t.get("desde", 0))
        u = uso.setdefault(nombre, {"segundos": 0.0, "tramos": []})
        u["segundos"] += seg
        u["tramos"].append([round(d0, 1), round(d0 + seg, 1)])
    return uso


def fuentes_ajenas(carpeta_broll: Path, tomas: list[dict]) -> list[dict]:
    """Los videos de otras personas que usa un anuncio, con su autor y su enlace (de fuentes.csv) y los segundos usados: para saber de donde salio cada clip y retirarlo rapido si alguien reclama."""
    import csv

    datos: dict[str, dict] = {}
    ruta = carpeta_broll / "fuentes.csv"
    if ruta.is_file():
        try:
            with open(ruta, encoding="utf-8-sig", newline="") as f:
                for fila in csv.DictReader(f):
                    if fila.get("archivo"):
                        datos[str(fila["archivo"])] = fila
        except (OSError, csv.Error):
            pass
    out = []
    for nombre, u in sorted(segundos_por_video_ajeno(tomas).items(), key=lambda kv: -kv[1]["segundos"]):
        f = datos.get(nombre, {})
        out.append({"archivo": nombre, "autor": str(f.get("autor") or "").lstrip("'"), "enlace": str(f.get("url") or "").lstrip("'"), "segundos": round(u["segundos"], 1), "tramos": u["tramos"]})
    return out


def control_calidad(mp4: Path | None, vd: dict, receta: dict, detector, elegidas: list[dict | None], mostrar=print, guion_txt: str | None = None, broll: dict | None = None) -> dict:
    """Las compuertas del anuncio. Cada una: {ok, detalle}. `ok` None = no se pudo verificar (falta el detector de texto o no hubo render)."""
    import texto_quemado as tq

    c: dict[str, dict] = {}
    dur_vd = float(vd["meta"]["duracion"])
    mn, mx = float(val(receta, "duracionMin", 25)), float(val(receta, "duracionMax", 60))
    c["tomas_sin_repetir"] = {"ok": not tomas_repetidas(vd["tomas"]), "detalle": f"{len(vd['tomas'])} tomas" if not tomas_repetidas(vd["tomas"]) else f"repetidas: {tomas_repetidas(vd['tomas'])[:5]}"}
    c["tomas_con_material"] = {"ok": all(e is not None for e in elegidas), "detalle": "todas las tomas salieron del b-roll limpio" if all(e is not None for e in elegidas) else "faltan tomas"}
    # la regla del dueño: de un mismo video de otra persona (de TikTok) se usa muy poco (se mide en el videoData, lo que de verdad se renderiza)
    cap = float(val(receta, "ritmo.maxSegPorVideoAjeno", 5) or 0)
    tope = max(cap, float(val(receta, "ritmo.corteMax", 3))) if cap else 0.0          # igual que en montar(): un tope menor que el corte mas largo se sube a ese corte
    if tope > 0:
        uso = segundos_por_video_ajeno(vd["tomas"])
        pasan = sorted(n for n, u in uso.items() if u["segundos"] > tope + 0.06)
        masuno = max((u["segundos"] for u in uso.values()), default=0.0)
        if pasan:
            detalle = f"pasan del tope de {tope:g} s: {pasan[:4]}"
        elif uso:
            detalle = f"ningun video de TikTok pasa de {tope:g} s (el que mas: {masuno:.1f} s; {len(uso)} videos de otras personas)"
        else:
            detalle = "el anuncio no usa videos de otras personas"
        c["tope_por_video_ajeno"] = {"ok": not pasan, "detalle": detalle}
    junto_texto = [k + 1 for k, e in enumerate(elegidas) if e and e.get("relajada") == 1]
    repetidas_previo = [k + 1 for k, e in enumerate(elegidas) if e and e.get("relajada") == 2]
    c["tomas_limpias"] = {"ok": True, "detalle": "todas con fotogramas sin texto quemado ni cuadros negros" + (f"; las tomas {junto_texto} quedan pegadas a un fotograma con texto (faltaba material)" if junto_texto else "")}
    if repetidas_previo:
        c["tomas_nuevas"] = {"ok": True, "detalle": f"las tomas {repetidas_previo} repiten tramos de anuncios anteriores de esta oferta (no alcanzaba el b-roll nuevo): trae mas videos"}
    c["duracion_en_receta"] = {"ok": mn - 0.5 <= dur_vd <= mx + 0.5, "detalle": f"{dur_vd:.1f} s; la receta pide de {mn:.0f} a {mx:.0f} s"}
    c["subtitulos_activos"] = {"ok": bool(vd.get("palabras")) or not val(receta, "subtitulos.activos", True), "detalle": f"{len(vd.get('palabras') or [])} palabras"}
    if guion_txt and vd.get("palabras"):
        dif = trn.diferencias_con_guion(vd["palabras"], guion_txt)
        c["subtitulos_iguales_al_guion"] = {"ok": not dif, "detalle": "los subtitulos dicen lo mismo que el guion" if not dif else
                                             f"{len(dif)} diferencia(s) con el guion: " + "; ".join(f"«{' '.join(d['dicen'])}» por «{' '.join(d['guion'])}»" for d in dif[:4])}
    if broll:
        c["broll_variedad"] = {"ok": True, "detalle": (f"{broll['videos']} videos limpios para {broll['cantidad']} anuncio(s) de {dur_vd:.0f} s: alcanza" if not broll["faltan"] else
                                f"poco b-roll: {broll['videos']} videos limpios y para {broll['cantidad']} anuncio(s) que no se parezcan hacen falta unos {broll['cantidad'] * broll['por_anuncio']}: trae mas")}
    if mp4 is None:
        for k in ("formato", "volumen", "sin_cuadros_negros", "sin_franjas_negras", "subtitulos_visibles", "zona_inferior_libre"):
            c[k] = {"ok": None, "detalle": "sin render: no se verifica"}
        return c
    from _comun import sonda

    info = sonda(mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur_mp4 = float(info["format"]["duration"])
    fallas = []
    if (v["width"], v["height"]) != (1080, 1920):
        fallas.append(f"tamano {v['width']}x{v['height']}")
    if v.get("r_frame_rate") != "30/1":
        fallas.append(f"fps {v.get('r_frame_rate')}")
    if v.get("codec_name") != "h264":
        fallas.append(f"codec {v.get('codec_name')}")
    if not a:
        fallas.append("sin audio")
    if abs(dur_mp4 - dur_vd) > 0.3:
        fallas.append(f"dura {dur_mp4:.2f} s y debia {dur_vd:.2f} s")
    c["formato"] = {"ok": not fallas, "detalle": f"1080x1920, 30 fps, h264, {dur_mp4:.1f} s" if not fallas else "; ".join(fallas)}
    c["duracion_en_receta"] = {"ok": mn - 0.5 <= dur_mp4 <= mx + 0.5, "detalle": f"{dur_mp4:.1f} s; la receta pide de {mn:.0f} a {mx:.0f} s"}
    lufs = medir_sonoridad(mp4) if a else None
    c["volumen"] = {"ok": lufs is not None and -15.5 <= lufs <= -12.5, "detalle": f"{lufs} LUFS (objetivo -14)" if lufs is not None else "no se pudo medir", "lufs": lufs}
    negros = cuadros_negros(mp4)
    c["sin_cuadros_negros"] = {"ok": not negros, "detalle": "ninguno" if not negros else f"negro en {[(round(x, 1), round(y, 1)) for x, y in negros[:5]]}"}
    # franjas negras arriba y abajo (un clip de TikTok exportado asi): no son un cuadro negro, el control de arriba no las ve. El catalogo ya no usa esos tramos; esto es la segunda red
    franjas = et.franjas_negras(mp4, min_muestras=2)
    c["sin_franjas_negras"] = {"ok": not franjas, "detalle": "ninguna" if not franjas else "franjas negras arriba y abajo en los segundos " + ", ".join(f"{a:.1f}-{b:.1f}" for a, b in franjas[:5])
                               + ": mira la toma de ese momento en evidencia.png y marca el tramo del video con `emparejar_tomas.py --marcar-toma`"}
    if detector is None:
        for k in ("subtitulos_visibles", "zona_inferior_libre"):
            c[k] = {"ok": None, "detalle": "sin detector de texto (rapidocr-onnxruntime): solo se verifica el videoData, no los pixeles"}
        return c
    # pixeles: 8 cuadros con subtitulo, mas los de textos grandes y el boton, pasados por el detector de texto
    subs, extra = tiempos_de_muestra(vd)
    con_subtitulo = 0
    en_zona: list[float] = []
    revisados = 0
    for t in subs + extra:
        im = cuadro_de(mp4, t)
        if im is None:
            continue
        revisados += 1
        alto = im.height
        cajas = [cj for cj in detector.cajas(im) if tq.caja_valida(cj, im.width, alto, tq.PARAMETROS)]
        centros = [sum(p[1] for p in cj) / 4 for cj in cajas]
        if t in subs and any((alto - ZONA_INFERIOR - 300) <= y <= (alto - ZONA_INFERIOR) for y in centros):
            con_subtitulo += 1
        if any(y >= alto - ZONA_INFERIOR + 10 for y in centros):
            en_zona.append(round(t, 1))
    c["subtitulos_visibles"] = {"ok": (con_subtitulo >= max(1, round(0.6 * len(subs)))) if subs else not val(receta, "subtitulos.activos", True),
                                "detalle": f"se leen en {con_subtitulo} de {len(subs)} cuadros revisados"}
    c["zona_inferior_libre"] = {"ok": not en_zona, "detalle": f"sin texto en los {ZONA_INFERIOR} px de abajo ({revisados} cuadros revisados)" if not en_zona else f"hay texto abajo en los segundos {en_zona}"}
    return c


def plan_md(producto: str, ad: str, cortes: list[dict], elegidas: list[dict], frases: list[dict], palabras: list[dict], textos: list[dict], prod: list[dict], cta: dict,
            fuentes: list[dict] | None = None, tope: float = 0.0) -> str:
    def celda(x: object) -> str:
        return re.sub(r"\s+", " ", str(x)).replace("|", "/").strip()

    def cuando(t: float) -> str:
        return f"{int(t // 60)}:{t % 60:04.1f}"
    filas = ["| Tiempo | Lo que se dice | Toma (video · desde) | Parecido | Texto grande | Producto |", "|---|---|---|---|---|---|"]
    for k, (c, e) in enumerate(zip(cortes, elegidas)):
        pal = " ".join(str(p["texto"]).strip() for p in palabras if c["t0"] - 1e-6 <= float(p["inicio"]) < c["t1"])
        tg = next((t["texto"] for t in textos if c["t0"] - 0.05 <= t["inicio"] < c["t1"]), "")
        pr = next((Path(p["src"]).name for p in prod if c["t0"] - 0.05 <= p["inicio"] < c["t1"]), "")
        toma = f"{e['video'][-18:]} · {e['desde']:.1f} s ({e['calidad']})" if e else "(sin toma)"
        filas.append(f"| {cuando(c['t0'])}–{cuando(c['t1'])} | {pal[:70]} | {toma} | z={e['z']:+.1f} | {tg} | {pr} |" if e else f"| {cuando(c['t0'])} | {pal[:70]} | {toma} | | {tg} | {pr} |")
    ajenos = ""
    if fuentes:
        ajenos = ("\n## Videos de otras personas que usa este anuncio\n\n"
                  + (f"Tope: {tope:g} s de cada video, en total. " if tope else "")
                  + "Si alguien reclama uno, se cambia esa toma (`--marcar-toma`) o se retira el anuncio. La lista tambien queda en `fuentes_ajenas.json`.\n\n"
                  + "| Video | Autor | Enlace | Segundos | Tramos usados (s) |\n|---|---|---|---|---|\n"
                  + "\n".join(f"| {celda(f['archivo'])} | {celda(f['autor'])} | {celda(f['enlace'])} | {f['segundos']:g} | {celda(', '.join(f'{a:g}-{b:g}' for a, b in f['tramos']))} |" for f in fuentes) + "\n")
    return (f"# Plan automatico de {producto} {ad}\n\nHecho por `montaje_auto.py` (CLIP local y reglas fijas, sin LLM). La imagen `evidencia.png` muestra la toma de cada corte y `revision_tomas_*.png` el inicio, la mitad y el final de cada una.\n\n"
            + "\n".join(filas) + f"\n\nBoton final: «{cta['texto']}» a los {cuando(cta['inicio'])}.\n" + ajenos + f"\n{RECORDATORIO}\n{como_marcar(producto, ad)}\n")


# --------------------------------------------------------------------------------------------------------------------------------------------
def calce_del_guion(frases_guion: list[dict], palabras: list[dict]) -> float:
    """Que parte (de 0 a 1) de las palabras del guion aparece, en orden, en la transcripcion de la voz."""
    plano_g = [t for f in frases_guion for t in tokens_de(f["texto"])]
    if not plano_g:
        return 1.0
    pw = [plano(p["texto"]) for p in palabras]
    return sum(n for _, _, n in difflib.SequenceMatcher(None, plano_g, pw, autojunk=False).get_matching_blocks()) / len(plano_g)


def es_de_esta_voz(transcripcion: dict, voz: Path) -> bool:
    """La transcripcion se hizo sobre ESTE audio (su campo `archivo`). Sin ese dato (las de antes no lo traian) o con solo un nombre suelto, sin carpeta, se acepta."""
    a = transcripcion.get("archivo")
    if not a:
        return True
    ruta = Path(str(a))
    if not ruta.is_absolute():          # un nombre suelto («voz.mp3») no dice de que carpeta: no se puede comparar y se acepta (transcribir.py y voz_auto.py escriben la ruta completa)
        return True
    try:
        return ruta.resolve() == voz.resolve()
    except OSError:
        return False


def voz_de_otro_guion(transcripcion: dict, guion: dict | None) -> str:
    """Por que la voz y su transcripcion NO sirven para este guion (vacio si sirven). Solo mira cuando voz_auto.py dejo la huella del texto con el que se pidio la voz
    (transcripcion.alineado_con_guion.guion_sha1): si el texto del guion cambio despues, la voz dice OTRA cosa y los subtitulos y los cortes saldrian desfasados."""
    ya = (transcripcion.get("alineado_con_guion") or {}) if isinstance(transcripcion, dict) else {}
    if not (guion and guion.get("frases")):
        return ""
    if not ya.get("guion_sha1"):
        # una voz que no pidio voz_auto.py (una locucion grabada, o la que transcribio este mismo montaje): no hay huella, pero si casi nada del guion esta en la voz, no es la voz de este guion
        pals = [p for p in (transcripcion.get("palabras") or []) if str(p.get("texto", "")).strip()] if isinstance(transcripcion, dict) else []
        calce = calce_del_guion(guion["frases"], pals) if pals else 1.0          # sin palabras no hay con que comparar (montar() ya se detiene si la transcripcion es muy corta)
        if calce < 0.5:
            return (f"La voz no dice este guion: solo el {calce * 100:.0f} % de las palabras de guion.json están en la voz. No monto con una voz que dice otra cosa. "
                    "Pide la voz de este guion (python scripts/voz_auto.py <producto> <AD> --forzar) o corrige guion.json (si la voz es una locución grabada por una persona, borra guion.json y el montaje saca todo de la voz).")
        return ""
    if ya["guion_sha1"] == huella_guion(guion["frases"]):
        return ""
    return ("La voz y la transcripción son de OTRO guion: el texto de guion.json cambió después de pedir la voz. No monto con una voz que dice otra cosa. "
            "Pide la voz de este guion: python scripts/voz_auto.py <producto> <AD> --forzar (o devuelve guion.json a como estaba).")


def texto_del_guion(carpeta_prod: Path, base: Path, ad: str) -> str | None:
    """El guion que se le muestra al equipo en la pagina de la Fabrica: lo que va a decir la voz de la cadena (las frases de guion.json, que una persona pudo corregir antes de pedir la voz: voz_auto.py
    lee ese archivo primero), o guion.txt, o, si no hay cadena, el guion de texto de una corrida a mano (public/productos/<p>/guiones/<AD>.txt). Sin ninguno, None."""
    g = leer_guion(base / "guion.json")
    if g and g["frases"]:
        return " ".join(f["texto"] for f in g["frases"]) + "\n"
    for ruta in (base / "guion.txt", carpeta_prod / "guiones" / f"{ad}.txt"):
        if ruta.is_file():
            return ruta.read_text(encoding="utf-8", errors="replace")
    return None


def filas_del_plan(cortes: list[dict], elegidas: list[dict], palabras: list[dict], textos: list[dict], prod: list[dict]) -> list[dict]:
    """Una fila por corte para la pagina de la Fabrica: cuando empieza, lo que se dice, la toma elegida y el texto grande (o la pagina del producto que se muestra ahi)."""
    filas = []
    for c, e in zip(cortes, elegidas):
        dicho = " ".join(str(p["texto"]).strip() for p in palabras if c["t0"] - 1e-6 <= float(p["inicio"]) < c["t1"])
        grande = next((t["texto"] for t in textos if c["t0"] - 0.05 <= t["inicio"] < c["t1"]), "")
        pagina = next((Path(p["src"]).name for p in prod if c["t0"] - 0.05 <= p["inicio"] < c["t1"]), "")
        toma = f"{str(e['video'])[-22:]} · {e['desde']:.1f} s" if e else "(sin toma)"
        filas.append({"t": f"{int(c['t0'] // 60)}:{int(c['t0'] % 60):02d}", "frase": dicho[:160], "toma": toma, "texto": grande or (f"[{pagina}]" if pagina else "")})
    return filas


def reporte_de_fabrica(producto: str, ad: str, mostrar=print):
    """Una funcion `rep(**datos)` que le avisa al dash como va este anuncio (etapa, guion, plan por tiempo, imagen y video de vista previa), o una que no hace nada. Solo avisa si el anuncio viene
    de la Fabrica (su orden.json lleva el proyecto) y este editor tiene DASH_URL y DASH_CLAVE_EDITOR en .env. Es de buena fe: no imprime nada si no hay a quien avisar y un fallo nunca tumba el montaje."""
    try:
        proyecto = str(leer_json(ANUNCIOS / producto / "tiktok" / ad / "orden.json")["proyecto"]["id"])
        import fabrica

        hay_url = os.environ.get("DASH_URL", "").strip() or fabrica.leer_env("DASH_URL")
        hay_llave = os.environ.get("DASH_CLAVE_EDITOR", "").strip() or fabrica.leer_env("DASH_CLAVE_EDITOR")
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return lambda **datos: False
    if not (hay_url and hay_llave):
        return lambda **datos: False

    def rep(**datos) -> bool:
        return fabrica.reportar_progreso(proyecto, ad, mostrar=mostrar, **datos)
    return rep


def montar(producto: str, ad: str, voz_arg: str | None = None, guion_arg: str | None = None, receta_arg: str | None = None, sin_render: bool = False,
           concurrencia: int | None = None, musica_arg: str | None = None, sin_historial: bool = False, cta_texto: str = "", cta_sub: str = "",
           motor=None, detector=None, mostrar=print, reportar=None) -> dict:
    """Todo el montaje. Devuelve el informe de calidad (tambien lo deja en informe_calidad.json). `motor` y `detector` se pueden inyectar para probar sin los modelos."""
    t_ini = time.time()
    tiempos: dict[str, float] = {}
    rep_dash = reportar if reportar is not None else reporte_de_fabrica(producto, ad, mostrar)

    def avisar(**datos) -> None:
        """Avisarle al dash como va el anuncio: de buena fe, un fallo ahi (o una funcion de aviso que se cae) nunca frena el montaje."""
        try:
            rep_dash(**datos)
        except Exception as e:  # noqa: BLE001
            mostrar(f"  (aviso: no pude avisar el progreso: {e})")

    base = ANUNCIOS / producto / "tiktok" / ad
    carpeta_prod = PUBLIC / "productos" / producto
    if not carpeta_prod.is_dir():
        salir(f"No existe el producto {producto} (public/productos/{producto})")
    base.mkdir(parents=True, exist_ok=True)
    (base / "final").mkdir(exist_ok=True)

    receta = combinar(RECETA_DEFECTO, leer_json(Path(receta_arg or base / "receta.json")) if Path(receta_arg or base / "receta.json").is_file() else {})
    if val(receta, "tipoAnuncio", "tiktok") == "ia":
        salir("Este pedido es de tipo IA (avatar con APIMart): el montaje automatico solo arma anuncios TikTok (locucion + b-roll). Sigue el flujo «Anuncios con IA» de la skill editor-video.")
    ficha = leer_json(carpeta_prod / "producto.json") if (carpeta_prod / "producto.json").is_file() else {}
    ficha = {} if str(ficha.get("_nota", "")).startswith("PLANTILLA") and not ficha.get("nombre") else ficha
    previo = historial_previo(producto, ad)
    corte_min, corte_max = float(val(receta, "ritmo.corteMin", 1)), float(val(receta, "ritmo.corteMax", 3))
    tope_ajeno = float(val(receta, "ritmo.maxSegPorVideoAjeno", 5) or 0)       # segundos EN TOTAL que se usan de un mismo video de otra persona (de TikTok); 0 = sin tope
    if 0 < tope_ajeno < corte_max:
        mostrar(f"AVISO: el tope de {tope_ajeno:g} s por video de otra persona es menor que el corte mas largo ({corte_max:g} s): se sube a {corte_max:g} s para que un corte quepa.")
        tope_ajeno = corte_max

    # 1. voz, transcripcion y audio
    t0 = time.time()
    voz = buscar_voz(producto, ad, base, voz_arg)
    pista = carpeta_prod / "guiones" / f"{ad}.txt"                  # el guion de texto, si lo hay, corrige despues los nombres que Whisper oye mal (de pista se come o tuerce la voz)
    transcripcion = obtener_transcripcion(voz, base, "es", pista if pista.is_file() else None, mostrar)
    palabras = [p for p in transcripcion["palabras"] if str(p.get("texto", "")).strip()]
    if len(palabras) < 5:
        salir(f"La transcripcion tiene solo {len(palabras)} palabras: no alcanza para armar un anuncio.")
    guion = leer_guion(Path(guion_arg) if guion_arg else base / "guion.json")
    otro = voz_de_otro_guion(transcripcion, guion)
    if otro:
        salir(otro)
    guion_txt = texto_del_guion(carpeta_prod, base, ad)
    dif_guion = trn.diferencias_con_guion(palabras, guion_txt) if guion_txt else []
    if dif_guion:
        mostrar(f"OJO: los subtitulos no dicen lo mismo que el guion ({len(dif_guion)} diferencia(s)): " + "; ".join(f"«{' '.join(d['dicen'])}» por «{' '.join(d['guion'])}»" for d in dif_guion[:4])
                + ". Corrige el guion o vuelve a pedir la voz ANTES de renderizar (cada render son 15 a 20 minutos).")
    musica = elegir_musica(receta, musica_arg, previo)
    audio = mezclar(voz, musica, base, mostrar)
    from _comun import duracion_seg

    dur = round(duracion_seg(base / "audio" / "voz_norm.wav") + COLA_SEG, 2)
    tiempos["voz_y_audio"] = round(time.time() - t0, 1)
    mostrar(f"Voz: {voz.name}, {len(palabras)} palabras, el anuncio durara {dur:.1f} s" + (f" · musica {musica.name}" if musica else " · sin musica (la biblioteca esta vacia o la receta no la quiere)"))

    # 2. b-roll analizado (CLIP + detector de texto), si falta algo
    t0 = time.time()
    if motor is None:
        import clip_local

        try:
            motor = clip_local.MotorClip()
        except clip_local.FaltanModelos as e:
            salir(str(e))
    cat = et.Catalogo(producto)
    if cat.faltan or not cat.videos:
        mostrar(f"Analizando {len(cat.faltan) or 'el'} video(s) del b-roll antes de montar...")
        et.indexar(producto, motor=motor, detector=detector, mostrar=mostrar)
        cat = et.Catalogo(producto)
    if not cat.videos:
        salir(f"No hay b-roll analizado en public/productos/{producto}/broll-tiktok/")
    tiempos["broll"] = round(time.time() - t0, 1)
    n_ajenos = sum(1 for v in cat.videos if et.es_ajeno(v.nombre))
    mostrar(f"B-roll: {len(cat.videos)} videos, {cat.segundos_limpios():.0f} s de fotogramas limpios"
            + (f" · {n_ajenos} son de otras personas (TikTok): maximo {tope_ajeno:g} s de cada uno" if tope_ajeno and n_ajenos else ""))
    broll_res = resumen_broll(cat, dur, tope_ajeno, int(val(receta, "cantidad", 1) or 1))
    if broll_res["faltan"]:
        mostrar(f"OJO: poco b-roll limpio: {broll_res['videos']} videos ({broll_res['segundos']} s). Con {tope_ajeno:g} s por video, cada anuncio de {dur:.0f} s necesita {broll_res['por_anuncio']} videos distintos; "
                f"para {broll_res['cantidad']} anuncio(s) que no se parezcan faltan unos {broll_res['faltan']}: trae mas b-roll.")

    # 3. frases, cortes y emparejamiento
    t0 = time.time()
    if guion and guion["frases"]:
        frases = alinear_guion(guion["frases"], palabras)
        mostrar(f"Guion: {len(frases)} frases (con descripcion visual: {sum(1 for f in guion['frases'] if f['visual_en'])})")
    else:
        frases = partir_frases(palabras)
        mostrar(f"Sin guion.json: {len(frases)} frases sacadas de la transcripcion")
    cortes = hacer_cortes(frases, palabras, dur, corte_min, corte_max)
    temas = []
    broll_json = carpeta_prod / "broll.json"
    if broll_json.is_file():
        try:
            temas += [str(x) for x in (leer_json(broll_json).get("consultas") or [])][:6]
        except ValueError:
            pass
    temas += [str(ficha[k]) for k in ("que_es", "promesa_principal") if ficha.get(k)]
    consultas = construir_consultas(motor, frases, cortes, palabras, temas)
    evitar = tomas_de_anuncios_previos(producto, ad)
    gancho = leer_gancho(base, receta)
    fijas = tomas_del_gancho(gancho, cortes[:-1]) if gancho else []          # el gancho nuevo con IA cubre los primeros cortes; los demas se emparejan con el b-roll
    resto = consultas[len(fijas):]
    for q in resto[:1]:
        q["gancho"] = not fijas                                                # el gancho del b-roll solo pesa si no hay gancho con IA
    elegidas = fijas + (et.emparejar(cat, resto, evitar, {"tope_seg_video": tope_ajeno}) if resto else [])
    if fijas:
        mostrar(f"Gancho con IA (gancho/gancho.json): los primeros {len(fijas)} cortes son del gancho (imagenes y clips nuestros), no del b-roll")
    faltan = [k + 1 for k, e in enumerate(elegidas) if e is None]
    tiempos["emparejar"] = round(time.time() - t0, 1)
    mostrar(f"{len(cortes)} cortes de {corte_min:g} a {corte_max:g} s; emparejados {len(cortes) - len(faltan)}" + (f"; SIN TOMA: cortes {faltan}" if faltan else ""))

    informe: dict = {"producto": producto, "anuncio": ad, "generado": datetime.now().isoformat(timespec="seconds"), "motor": getattr(motor, "version", "?"),
                     "entradas": {"voz": voz.name, "guion": bool(guion), "gancho_ia": len(fijas), "receta": "receta.json" if (base / "receta.json").is_file() else "valores de fabrica"}}
    if faltan:
        necesarios = math.ceil(sum(float(c["t1"]) - float(c["t0"]) for c in cortes) / tope_ajeno) if tope_ajeno else 0
        pista = (f" (con el tope de {tope_ajeno:g} s por video de otra persona hacen falta unos {necesarios} videos distintos y hay {n_ajenos} de TikTok)" if tope_ajeno and n_ajenos else "")
        informe.update({"estado": "sin-material", "compuertas": {"tomas_con_material": {"ok": False, "detalle": f"faltan tomas limpias para los cortes {faltan}: trae mas b-roll{pista}"}},
                        "tiempos": tiempos})
        escribir_json(base / "informe_calidad.json", informe)
        salir(f"No alcanza el b-roll limpio para los cortes {faltan}. Trae mas videos (broll_tiktok.py) y repite.{pista}")

    # 4. textos grandes, producto, boton final
    cta = boton_final(receta, guion, dur, cta_texto, cta_sub)
    color = color_primario(previo)
    textos = elegir_textos_grandes(frases, palabras, dur, receta, cta["inicio"], color,
                                   gancho["texto_grande"] if (gancho and fijas and val(receta, "gancho.textoGrande", True)) else "")
    prod = lugares_producto(palabras, dur, textos, cta, receta, carpeta_prod, ficha)
    mus = None
    if musica and audio["musica"]:
        mus = {"src": audio["musica"], "volumen": 1, "fadeInSeg": 0.6, "fadeOutSeg": 1.5}
    vd = armar_videodata(producto, ad, dur, palabras, cortes, elegidas, textos, prod, cta, receta, audio["voz"], mus, color)
    ruta_vd = base / "videoData.json"
    escribir_json(ruta_vd, vd)

    # 5. la receta y el validador (los mismos scripts de siempre)
    avisos: list[str] = []
    # la receta que se aplica queda escrita junto al anuncio (receta_aplicada.json): la del pedido sobre los valores de fabrica; asi consta lo que se uso
    escribir_json(base / "receta_aplicada.json", receta)
    r = subprocess.run([sys.executable, str(RAIZ / "scripts" / "aplicar_receta.py"), str(base), "--receta", str(base / "receta_aplicada.json")], cwd=RAIZ,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        salir(f"aplicar_receta.py fallo: {(r.stderr or r.stdout).strip()[-300:]}")
    avisos += [x.split("AVISO:", 1)[1].strip() for x in r.stdout.splitlines() if "AVISO:" in x]
    r = subprocess.run([sys.executable, str(RAIZ / "scripts" / "validar_videodata.py"), str(ruta_vd)], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
    errores = [x[6:].strip() for x in r.stdout.splitlines() if x.startswith("ERROR:")]
    avisos_validador = [x[6:].strip() for x in r.stdout.splitlines() if x.startswith("AVISO:")]
    vd = leer_json(ruta_vd)
    informe.update({"avisos_receta": avisos, "avisos_validador": avisos_validador, "errores_validador": errores})
    mostrar(f"Receta aplicada ({len(avisos)} aviso(s)); validador: " + ("OK" if not errores else f"{len(errores)} ERROR(ES): {errores[:3]}"))
    if errores:
        informe.update({"estado": "no-apto", "tiempos": tiempos})
        escribir_json(base / "informe_calidad.json", informe)
        salir("El validador encontro errores en videoData.json; no se renderiza: " + "; ".join(errores[:3]))

    # 6. evidencia y plan
    t0 = time.time()
    evidencia_png(base / "evidencia.png", producto, ad, cortes, elegidas, frases, palabras, frases[0]["texto"])
    hojas_revision = hoja_de_revision(base, producto, ad, cortes, elegidas)
    fuentes = fuentes_ajenas(cat.carpeta, vd["tomas"])
    escribir_json(base / "fuentes_ajenas.json", {"anuncio": ad, "tope_seg_por_video": tope_ajeno, "videos": fuentes})
    informe["fuentes_ajenas"] = fuentes
    (base / "plan.md").write_text(plan_md(producto, ad, cortes, elegidas, frases, palabras, vd.get("textosGrandes", []), vd.get("producto", []), vd["cta"], fuentes, tope_ajeno), encoding="utf-8")
    tiempos["evidencia"] = round(time.time() - t0, 1)
    informe["emparejamiento"] = [{"corte": k + 1, "t0": c["t0"], "t1": c["t1"], "frase": frases[c["frase"]]["texto"][:80], "toma": {kk: e[kk] for kk in ("video", "desde", "dur", "z", "sim", "calidad", "relajada")}}
                                 for k, (c, e) in enumerate(zip(cortes, elegidas))]
    mostrar(f"Evidencia: {base / 'evidencia.png'} · revision de tomas (3 fotogramas de cada una): {len(hojas_revision)} hoja(s) revision_tomas_*.png")
    # el dash muestra el plan (guion, una fila por corte y la imagen con las tomas) para que alguien lo vea y diga OK
    espera_ok = sin_render and val(receta, "aprobacion.plan", "mostrar") != "automatico"
    avisar(etapa="plan", nota=f"Plan de {len(cortes)} cortes para {dur:.0f} s" + (": espero el OK para renderizar" if espera_ok else ""), guion=texto_del_guion(carpeta_prod, base, ad),
           plan=filas_del_plan(cortes, elegidas, palabras, vd.get("textosGrandes", []), vd.get("producto", [])), vista=base / "evidencia.png")

    # 7. render y control de calidad
    motor = None                                              # se sueltan los modelos de CLIP antes del render: Remotion necesita esa memoria (en un PC de 16 GB con el navegador abierto sobra poca)
    import gc

    gc.collect()
    mp4 = None
    if not sin_render:
        # con poca RAM libre Remotion falla a media corrida («timed out ... headless browser»): 2 procesos solo si sobra memoria (cada uno, ~0,6 GB mas el navegador)
        conc = concurrencia or (1 if ram_libre_gb() < 4.5 else max(1, (os.cpu_count() or 2) // 2))
        lo, hi = estimar_render_min(dur, conc)
        mostrar(f"Renderizando {dur:.0f} s de video con {conc} proceso(s): en este PC tarda unos {lo} a {hi} minutos (RAM libre {ram_libre_gb():.1f} GB). No abras programas pesados.")
        avisar(etapa="render", pct=60, nota=f"Renderizando {dur:.0f} s de video: unos {lo} a {hi} min")
        mp4, seg = renderizar(ruta_vd, conc, base / "render.log", mostrar)
        tiempos["render"] = round(seg, 1)
        anotar_render_medido(dur, conc, seg)
        mostrar(f"Render listo en {seg / 60:.1f} min: {mp4}")
    t0 = time.time()
    if detector is None and et.DetectorTexto.disponible():
        detector = et.DetectorTexto()
    compuertas = control_calidad(mp4, vd, receta, detector, elegidas, mostrar, guion_txt=guion_txt, broll=broll_res)
    tiempos["control"] = round(time.time() - t0, 1)
    tiempos["total"] = round(time.time() - t_ini, 1)
    fallas = [k for k, v in compuertas.items() if v["ok"] is False]
    sin_verificar = [k for k, v in compuertas.items() if v["ok"] is None]
    informe.update({"recordatorio": RECORDATORIO, "como_marcar": como_marcar(producto, ad), "estado": "sin-render" if sin_render else ("aprobado" if not fallas else "con-fallas"),
                    "final": str(mp4) if mp4 else None, "duracion_s": dur,
                    "tomas": len(vd["tomas"]), "textos_grandes": len(vd.get("textosGrandes", [])), "compuertas": compuertas, "sin_verificar": sin_verificar, "tiempos": tiempos,
                    "ram_pico_mb": round(et.memoria_pico_mb())})
    escribir_json(base / "informe_calidad.json", informe)
    if mp4:
        avisar(etapa="revision", nota="Aprobado por el control de calidad" if not fallas else "El video quedo, pero fallo: " + ", ".join(fallas), video=mp4)
    for k, v in compuertas.items():
        mostrar(f"  {'PASA ' if v['ok'] else ('FALLA' if v['ok'] is False else 'sin verificar')} {k}: {v['detalle']}")
    mostrar(f"  OJO: {RECORDATORIO}")
    mostrar(f"       {como_marcar(producto, ad)}")
    if mp4 and not fallas and not sin_historial:
        subprocess.run([sys.executable, str(RAIZ / "scripts" / "anotar_historial.py"), str(ruta_vd), "--gancho", frases[0]["texto"][:120], "--estilo", "automatico (CLIP local, sin LLM)",
                        "--final", str(mp4)], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return informe


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("producto")
    ap.add_argument("anuncio", help="el ID del anuncio: AD1, AD2...")
    ap.add_argument("--voz")
    ap.add_argument("--guion")
    ap.add_argument("--receta")
    ap.add_argument("--sin-render", action="store_true", help="arma el plan, el videoData y la evidencia pero no renderiza (tarda segundos)")
    ap.add_argument("--concurrencia", type=int)
    ap.add_argument("--musica")
    ap.add_argument("--sin-historial", action="store_true")
    ap.add_argument("--cta")
    ap.add_argument("--subcta")
    a = ap.parse_args()
    informe = montar(a.producto, a.anuncio, a.voz, a.guion, a.receta, a.sin_render, a.concurrencia, a.musica, a.sin_historial, a.cta or "", a.subcta or "")
    estado = informe.get("estado")
    print(f"\nEstado: {estado}. Informe: anuncios/{a.producto}/tiktok/{a.anuncio}/informe_calidad.json")
    sys.exit(0 if estado in ("aprobado", "sin-render") else 2)


if __name__ == "__main__":
    main()
