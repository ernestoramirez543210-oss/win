"""Autoprueba de scripts/emparejar_tomas.py, scripts/montaje_auto.py y scripts/clip_local.py SIN internet y sin los modelos reales de CLIP:
un motor de mentira que entiende colores (la imagen roja se parece a la palabra «rojo») y un detector de texto de mentira (una barra blanca = texto quemado).

Con videos sinteticos de ffmpeg (4 videos de colores, cada uno con un corte de escena a la mitad, uno con una barra de «texto» en su segunda mitad) comprueba, de punta a punta
y sin render:
  - las frases y los cortes de la voz (contiguos, dentro del ritmo de la receta), el guion alineado a la transcripcion, las cifras y la garantia como textos grandes;
  - el analisis del b-roll (fotogramas, cortes, texto, calidad y tramos), lo que respeta de la revision manual y la comparacion con ella;
  - que cada frase recibe la toma de su color, que ninguna toma se repite ni se solapa, que ninguna cruza un corte ni usa fotogramas con texto, y que se evitan las
    tomas del anuncio anterior;
  - las marcas de una persona (--marcar, --marcar-toma): lo marcado `mala` no se usa nunca, ni cuando falta material, y la marca sobrevive a repetir el analisis;
  - el montaje completo (videoData.json valido, receta aplicada, evidencia.png y plan.md, informe) y las compuertas del control de calidad con un mp4 de mentira;
  - que los scripts de la corrida NO abren la red ni llaman a ningun modelo de lenguaje.
Si los modelos reales de CLIP estan instalados, ademas comprueba que reconocen colores (imagen y texto en espanol e ingles quedan en el mismo espacio).

Uso:  python scripts/autoprueba_montaje.py [--sin-clip-real]
Termina con "AUTOPRUEBA OK" si todo va bien.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, PUBLIC, RAIZ, configurar_consola, ejecutable, escribir_json, leer_json  # noqa: E402

FALLAS: list[str] = []
PROD = "_automontaje"
AD = "AD1"
COLORES = {"rojo": (210, 40, 40), "verde": (40, 180, 70), "azul": (50, 90, 220), "amarillo": (235, 205, 40)}
# lo que dice la voz de mentira: cada frase habla de un color (palabra clave) y trae una cifra, la garantia o un beneficio para los textos grandes
FRASES = [
    "¿Quieres ver el color rojo en tu reunión de mujeres hoy mismo?",
    "Tienes treinta dinámicas de color rojo listas para usar.",
    "Con el color verde todo se ve mejor, aprende paso a paso desde casa.",
    "El verde de la naturaleza trae calma a cada encuentro.",
    "En solo diez minutos preparas todo con el color azul.",
    "El azul del cielo te acompaña y funciona de verdad.",
    "Hoy te llevas el ebook con un bono gratis de color amarillo.",
    "El amarillo del sol alegra tus reuniones.",
    "Tienes treinta días de garantía, si no te gusta te devolvemos tu dinero.",
    "Escríbenos ahora y recibe tu ebook hoy mismo.",
]


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"  ->  {detalle}" if detalle else ""))


def sale(fn, *args, **kw) -> bool:
    """True si fn termina con un mensaje de error de los scripts (SystemExit). El mensaje no se imprime."""
    import contextlib
    import io

    with contextlib.redirect_stderr(io.StringIO()):
        try:
            fn(*args, **kw)
        except SystemExit:
            return True
    return False


# ------------------------------------------------------------------ motor y detector de mentira
class MotorFalso:
    """Imagenes y frases en el mismo espacio de 512 numeros: el color medio de la imagen, o el color de la palabra clave de la frase."""
    version = "falso/1"

    def __init__(self):
        import numpy as np

        rng = np.random.default_rng(7)
        self.np = np
        q, _ = np.linalg.qr(rng.normal(size=(512, 4)))
        self.M = q.T.astype(np.float32)                    # 4 direcciones perpendiculares: un eje por color

    def _vec(self, rgb, clara: float = 0.0):
        """Por el matiz y no por la claridad: el rojo y el rojo oscuro son la misma direccion; dos colores distintos, direcciones perpendiculares; el gris, a medias con todos."""
        import colorsys

        np = self.np
        h, sat, val = colorsys.rgb_to_hsv(*(float(x) / 255.0 for x in rgb))
        c = np.zeros(4, dtype=np.float32)
        if sat < 0.25 or val < 0.05:
            c[:] = 1.0
        else:
            grados = h * 360.0
            c[0 if (grados < 20 or grados >= 330) else 3 if grados < 75 else 1 if grados < 170 else 2] = 1.0       # rojo, amarillo, verde, azul
        v = c / float(np.linalg.norm(c)) @ self.M
        return v / max(float(np.linalg.norm(v)), 1e-6)

    def imagenes(self, ims, lote: int = 16):
        np = self.np
        out = []
        for im in ims:
            a = np.asarray(im.convert("RGB").resize((16, 16)), dtype=np.float32).reshape(-1, 3).mean(axis=0)
            out.append(self._vec(a, 128.0))
        return np.stack(out) if out else np.zeros((0, 512), dtype=np.float32)

    def textos(self, textos):
        np = self.np
        out = []
        for t in textos:
            plano = re.sub(r"[^a-z ]", "", str(t).lower())
            hallado = next((rgb for nombre, rgb in COLORES.items() if nombre in plano), None)
            out.append(self._vec(hallado if hallado else (128, 128, 128), 128.0))
        return np.stack(out) if out else np.zeros((0, 512), dtype=np.float32)


class DetectorFalso:
    """Una barra blanca ancha en la imagen cuenta como texto quemado."""

    def cajas(self, im):
        import numpy as np

        a = np.asarray(im.convert("L"), dtype=np.float32)
        filas = np.where(a.mean(axis=1) > 235)[0]
        if len(filas) < max(4, int(0.015 * a.shape[0])):
            return []
        y0, y1 = float(filas.min()), float(filas.max())
        w = float(a.shape[1])
        return [[(0.05 * w, y0), (0.95 * w, y0), (0.95 * w, y1), (0.05 * w, y1)]]


# ------------------------------------------------------------------ material sintetico
def ff(*args: str) -> None:
    r = subprocess.run([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-nostdin", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg fallo: {r.stderr.strip()[-300:]}")


def color_hex(rgb) -> str:
    return "0x%02X%02X%02X" % rgb


TEXTURA = "drawgrid=w=90:h=90:t=30:c=black@0.45,noise=alls=20:allf=t"      # cuadros gruesos: que el cuadro no sea liso (un cuadro liso se descarta, como uno negro)


def crear_video(destino: Path, c1, c2, barra_en_segunda: bool = False, largo: float = 16.0, tam: str = "270x480") -> None:
    """Dos escenas de `largo` segundos: el color c1 y despues c2 (un corte seco, mucho mas oscuro), con textura para que no sea liso; opcional, una barra blanca en la segunda."""
    f2 = f"[1:v]{TEXTURA},drawbox=x=iw*0.05:y=ih*0.55:w=iw*0.9:h=ih*0.07:color=white:t=fill[b]" if barra_en_segunda else f"[1:v]{TEXTURA}[b]"
    ff("-f", "lavfi", "-i", f"color=c={color_hex(c1)}:s={tam}:r=30:d={largo}", "-f", "lavfi", "-i", f"color=c={color_hex(c2)}:s={tam}:r=30:d={largo}",
       "-filter_complex", f"[0:v]{TEXTURA}[a];{f2};[a][b]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15", str(destino))


def crear_material() -> dict:
    base = PUBLIC / "productos" / PROD
    broll = base / "broll-tiktok"
    broll.mkdir(parents=True, exist_ok=True)
    crear_video(broll / "rojo.mp4", COLORES["rojo"], (95, 10, 10))
    crear_video(broll / "verde.mp4", COLORES["verde"], (15, 90, 30))
    crear_video(broll / "azul.mp4", COLORES["azul"], (20, 40, 120))
    crear_video(broll / "amarillo.mp4", COLORES["amarillo"], (150, 125, 15), barra_en_segunda=True)    # la segunda mitad (8 a 16 s) trae «texto»
    escribir_json(base / "broll.json", {"consultas": ["color rojo", "color verde"], "drive": ""})
    escribir_json(base / "producto.json", {"nombre": "Dinámicas de colores", "que_es": "dinámicas para reuniones", "promesa_principal": "reuniones alegres", "mejores_paginas_ebook": [2]})
    # un ebook de mentira: portada y 3 paginas (basta con imagenes)
    from PIL import Image, ImageDraw

    (base / "paginas").mkdir(exist_ok=True)
    for n, nombre in enumerate(("portada.jpg", "paginas/p001.jpg", "paginas/p002.jpg", "paginas/p003.jpg")):
        im = Image.new("RGB", (600, 850), (240, 240, 235))
        ImageDraw.Draw(im).rectangle([40, 40, 560, 810], outline=(200, 40, 40), width=8)
        im.save(base / nombre)
    # voz: 1 tono por palabra no hace falta; basta un audio con el mismo largo que las palabras
    return {"base": base, "broll": broll}


def hacer_palabras(frases: list[str], seg_por_palabra: float = 0.36, pausa_frase: float = 0.30) -> tuple[list[dict], float]:
    """transcripcion.json de mentira: las palabras de cada frase con sus tiempos (como las daria Whisper, con la puntuacion pegada)."""
    palabras, t = [], 0.45
    for f in frases:
        for w in f.split():
            palabras.append({"texto": w, "inicio": round(t, 3), "fin": round(t + seg_por_palabra * 0.85, 3), "prob": 0.95})
            t += seg_por_palabra
        t += pausa_frase
    return palabras, round(t, 2)


def preparar_anuncio(frases: list[str]) -> tuple[Path, list[dict], float]:
    base = ANUNCIOS / PROD / "tiktok" / AD
    (base / "audio").mkdir(parents=True, exist_ok=True)
    palabras, dur_voz = hacer_palabras(frases)
    escribir_json(base / "transcripcion.json", {"archivo": "voz.mp3", "modelo": "falso", "idioma": "es", "duracion": dur_voz, "texto": " ".join(frases), "palabras": palabras, "segmentos": [], "pista": "ninguna"})
    voces = PUBLIC / "productos" / PROD / "voces"
    voces.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", f"sine=f=330:d={dur_voz}", "-c:a", "libmp3lame", "-q:a", "5", str(voces / f"{AD}.mp3"))
    # que la transcripcion no parezca vieja frente a la voz
    (base / "transcripcion.json").touch()
    return base, palabras, dur_voz


def limpiar() -> None:
    for p in (PUBLIC / "productos" / PROD, ANUNCIOS / PROD, RAIZ / ".cache" / "autoprueba_montaje"):
        shutil.rmtree(p, ignore_errors=True)


# ------------------------------------------------------------------ pruebas
def prueba_frases_y_cortes() -> None:
    import montaje_auto as ma

    print("\n[1] frases, cortes, guion y textos grandes")
    palabras, dur_voz = hacer_palabras(FRASES)
    dur = round(dur_voz + ma.COLA_SEG, 2)
    frases = ma.partir_frases(palabras)
    comprobar(len(frases) == len(FRASES), "una frase por oracion de la voz", f"{len(frases)} frases y habia {len(FRASES)}")
    comprobar(all(frases[k]["i0"] == (frases[k - 1]["i1"] + 1 if k else 0) for k in range(len(frases))), "las frases cubren todas las palabras sin saltos ni cruces")
    # una frase larguisima sin puntos se parte
    largas, _ = hacer_palabras([" ".join(f"palabra{i}," if i % 9 == 0 else f"palabra{i}" for i in range(40))])
    comprobar(len(ma.partir_frases(largas)) >= 2 and all(f["i1"] - f["i0"] + 1 <= 24 for f in ma.partir_frases(largas)), "una frase de 40 palabras sin punto se parte en trozos de 24 o menos")
    cortes = ma.hacer_cortes(frases, palabras, dur, 1.0, 3.0)
    comprobar(abs(cortes[0]["t0"]) < 1e-6 and abs(cortes[-1]["t1"] - dur) < 1e-3, "los cortes empiezan en 0 y terminan al final del anuncio")
    comprobar(all(abs(a["t1"] - b["t0"]) < 1e-6 for a, b in zip(cortes, cortes[1:])), "los cortes son contiguos (sin huecos negros)")
    largos = [round(c["t1"] - c["t0"], 2) for c in cortes]
    comprobar(all(0.75 <= x <= 3.75 for x in largos), "cada corte dura entre 0,75 y 3,75 s (el ritmo 1 a 3 s de la receta)", f"{largos}")
    comprobar(all(0 <= c["frase"] < len(frases) for c in cortes), "cada corte sabe de que frase es")
    # el corte nunca cae en medio de una palabra
    dentro = [c["t0"] for c in cortes[1:] if any(float(p["inicio"]) + 0.02 < c["t0"] < float(p["fin"]) - 0.02 for p in palabras)]
    comprobar(not dentro, "ningun corte parte una palabra a la mitad", f"{dentro[:3]}")

    guion = [{"texto": f, "rol": "", "visual_en": f"a {c} background" if c else "", "etiquetas": [], "texto_grande": "", "producto": False, "inicio": None, "fin": None}
             for f, c in zip(FRASES, ("red", "red", "green", "green", "blue", "blue", "yellow", "yellow", "", ""))]
    al = ma.alinear_guion(guion, palabras)
    comprobar(len(al) == len(FRASES) and all(abs(al[k]["t0"] - frases[k]["t0"]) < 0.05 for k in range(len(FRASES))), "el guion se alinea con la transcripcion y da los mismos tiempos que la voz")
    # guion con palabras cambiadas (la voz no dijo exactamente el guion): se alinea igual
    cambiado = [dict(g, texto=g["texto"].replace("Tienes", "Ahora tienes")) for g in guion]
    al2 = ma.alinear_guion(cambiado, palabras)
    comprobar(all(abs(al2[k]["t0"] - frases[k]["t0"]) < 0.8 for k in range(len(FRASES))), "el guion se alinea aunque la voz diga algo distinto en alguna palabra")

    # guion con tiempos explicitos (los que dara la etapa de voz): mandan sobre lo que se deduce del texto
    g3 = [dict(g) for g in guion]
    g3[2]["inicio"], g3[2]["fin"] = frases[4]["t0"], frases[4]["t1"]        # a proposito en otro lugar: debe ganar el tiempo explicito
    al3 = ma.alinear_guion(g3, palabras)
    comprobar(abs(al3[2]["t0"] - frases[4]["t0"]) < 0.05 and abs(al3[2]["t1"] - frases[4]["t1"]) < 0.05, "si el guion trae inicio y fin, se respetan")
    # casos raros de la voz: muy corta, sin puntuacion ni pausas, y palabras que se montan unas con otras
    pc, dc = hacer_palabras(["Hola mundo bonito.", "Compra hoy mismo."])
    dur_c = round(dc + ma.COLA_SEG, 2)
    cc = ma.hacer_cortes(ma.partir_frases(pc), pc, dur_c, 1.0, 3.0)
    comprobar(abs(cc[0]["t0"]) < 1e-6 and abs(cc[-1]["t1"] - dur_c) < 1e-3 and all(abs(a["t1"] - b["t0"]) < 1e-6 for a, b in zip(cc, cc[1:])) and all(c["t1"] > c["t0"] for c in cc),
              "una voz de 4 segundos da cortes contiguos que tapan todo el anuncio", f"{[(c['t0'], c['t1']) for c in cc]}")
    sin_punt = [{"texto": f"palabra{i}", "inicio": round(0.4 + i * 0.36, 3), "fin": round(0.4 + i * 0.36 + 0.3, 3)} for i in range(60)]
    fr = ma.partir_frases(sin_punt)
    comprobar(fr[0]["i0"] == 0 and fr[-1]["i1"] == 59 and all(f["i1"] - f["i0"] + 1 <= 24 for f in fr) and all(b["i0"] == a["i1"] + 1 for a, b in zip(fr, fr[1:])),
              "60 palabras seguidas, sin puntuacion ni pausas, se parten en frases de 24 o menos sin perder ninguna")
    dur_s = round(0.4 + 60 * 0.36 + ma.COLA_SEG, 2)
    cs = ma.hacer_cortes(fr, sin_punt, dur_s, 1.0, 3.0)
    comprobar(all(0.75 <= round(c["t1"] - c["t0"], 2) <= 3.75 for c in cs) and abs(cs[-1]["t1"] - dur_s) < 1e-3, "sin puntuacion tambien salen cortes de 1 a 3 s hasta el final")
    montadas = [{"texto": f"w{i}.", "inicio": float(i), "fin": i + 1.4} for i in range(8)]       # el fin de cada palabra pasa del inicio de la siguiente
    cm = ma.hacer_cortes(ma.partir_frases(montadas, pausa=0.7), montadas, 9.0, 1.0, 3.0)
    comprobar(all(c["t1"] > c["t0"] for c in cm) and all(abs(a["t1"] - b["t0"]) < 1e-6 for a, b in zip(cm, cm[1:])), "palabras que se montan no dan cortes de largo cero ni hacia atras")

    cta = ma.boton_final(ma.RECETA_DEFECTO, None, dur, "", "")
    textos = ma.elegir_textos_grandes(frases, palabras, dur, ma.RECETA_DEFECTO, cta["inicio"], "#E11D48")
    comprobar(5 <= len(textos) <= 7, "entre 5 y 7 textos grandes (lo que pide la receta)", f"{len(textos)}: {[t['texto'] for t in textos]}")
    comprobar(any(t["inicio"] <= 3.5 for t in textos), "hay un texto grande en el gancho (primeros segundos)")
    comprobar(all(a["fin"] <= b["inicio"] + 1e-6 for a, b in zip(textos, textos[1:])), "los textos grandes no se solapan")
    comprobar(all(t["fin"] <= cta["inicio"] - 0.05 for t in textos), "ningun texto grande toca el boton final")
    todos = " | ".join(t["texto"].lower() for t in textos)
    comprobar("treinta" in todos or "30" in todos, "la cifra (treinta dinamicas) sale en grande", todos)
    comprobar("garant" in todos, "la garantia sale en grande", todos)
    comprobar(all(3 <= len(t["texto"]) <= 30 for t in textos), "los textos grandes son cortos (3 a 30 letras)")
    # con guion que trae su propio texto grande, ese manda
    g2 = [dict(g) for g in guion]
    g2[2]["texto_grande"] = "TODO EN UN SOLO LUGAR"
    textos2 = ma.elegir_textos_grandes(ma.alinear_guion(g2, palabras), palabras, dur, ma.RECETA_DEFECTO, cta["inicio"], "#E11D48")
    comprobar(any(t["texto"] == "TODO EN UN SOLO LUGAR" for t in textos2), "el texto grande que trae el guion manda sobre las reglas")
    # el texto grande del gancho (el que escribe gancho_ia.py) se corta por palabras completas, nunca a mitad de una: con [:30] «cenas completas en treinta minutos» salio «...TREINTA MIN»
    # (el 102 de RECETAS NAVIDAD) y «sorprende a todos con trescientas recetas» salio «...CON TRESCIEN» (el 101)
    from _comun import MAX_LETRAS_TEXTO_GANCHO, recortar_por_palabras
    for dicho in ("cenas completas en treinta minutos", "sorprende a todos con trescientas recetas"):
        tg = ma.elegir_textos_grandes(frases, palabras, dur, ma.RECETA_DEFECTO, cta["inicio"], "#E11D48", texto_gancho=dicho)
        comprobar(tg and tg[0]["texto"] == dicho and tg[0]["inicio"] <= 0.5, f"el texto grande del gancho sale entero y primero: «{dicho}»", str([t["texto"] for t in tg]))
    largo = "descubre cómo preparar cenas completas de navidad con ingredientes sencillos que encuentras en cualquier tienda"
    tg = ma.elegir_textos_grandes(frases, palabras, dur, ma.RECETA_DEFECTO, cta["inicio"], "#E11D48", texto_gancho=largo)
    palabras_orig = largo.split()
    comprobar(tg and len(tg[0]["texto"]) <= MAX_LETRAS_TEXTO_GANCHO and tg[0]["texto"].split() == palabras_orig[:len(tg[0]["texto"].split())],
              "un texto de gancho demasiado largo se corta en palabras completas, las primeras del original", str([t["texto"] for t in tg]))
    corte = lambda t, n: recortar_por_palabras(t, n)  # noqa: E731
    comprobar(corte("cenas completas en treinta minutos", 44) == "cenas completas en treinta minutos" and corte("hola  amigas\n", 44) == "hola amigas", "un texto que cabe queda igual (con los espacios limpios)")
    comprobar(corte("cenas completas en treinta minutos", 30) == "cenas completas en treinta", "a 30 letras se corta antes de «minutos», no a mitad de palabra")
    comprobar(corte("sorprende a todos con trescientas recetas", 33) == "sorprende a todos con trescientas", "a 33 letras: sorprende a todos con trescientas")
    comprobar(corte("sorprende a todos con trescientas recetas", 25) == "sorprende a todos", "se quitan las palabritas del final («con») para no terminar a medias")
    comprobar(corte("electroencefalograma portatil", 10) == "electroencefalograma", "una sola palabra mas larga que el maximo se deja entera (Remotion achica la letra)")
    comprobar(corte("¿Quieres aprender a bordar en una semana?", 26) == "Quieres aprender a bordar", "se cae el «¿» que quedaria abierto sin su cierre", corte("¿Quieres aprender a bordar en una semana?", 26))
    comprobar(corte("¿Quieres bordar?", 20) == "¿Quieres bordar?" and corte("hola, mundo, grande y bonito", 12) == "hola, mundo", "se conservan los signos si el texto cabe; la coma suelta del final se quita", corte("hola, mundo, grande y bonito", 12))
    comprobar(corte("", 30) == "" and corte(None, 30) == "", "texto vacio: queda vacio")
    # el modelo del guion a veces pone el texto grande UNA FRASE ANTES de la que lo dice (el 102 de RECETAS NAVIDAD): el montaje lo pone donde la voz dice sus palabras
    def voz_de_frases(textos_frases: list[str], tg: list[str]) -> tuple[list[dict], list[dict]]:
        pals: list[dict] = []
        fr: list[dict] = []
        t = 0.0
        for txt, grande in zip(textos_frases, tg):
            i0 = len(pals)
            for w in txt.split():
                pals.append({"texto": w, "inicio": round(t, 2), "fin": round(t + 0.35, 2)})
                t += 0.4
            fr.append({"i0": i0, "i1": len(pals) - 1, "texto": txt, "t0": pals[i0]["inicio"], "t1": pals[-1]["fin"], "guion": {"texto_grande": grande}})
            t += 0.6
        return fr, pals

    dicho = ["¿Todavía no sabes qué vas a cocinar para tu familia en esta Navidad?", "Muchas personas pasan horas buscando ideas nuevas.", "Pero puedes cambiar eso hoy mismo con una solución muy práctica.",
             "Tienes acceso completo a 300 Recetas de Navidad para sorprender a todos.", "Además disfrutas cenas navideñas en 30 minutos sin complicaciones.", "Cuentas con 50 recetas navideñas y 60 postres navideños irresistibles."]
    fr_a, pal_a = voz_de_frases(dicho, ["RECETAS NAVIDAD", "", "300 RECETAS", "CENAS EN 30 MINUTOS", "", "60 POSTRES"])
    por_frase = ma.texto_grande_por_frase(fr_a, pal_a)
    comprobar(por_frase == ["RECETAS NAVIDAD", "", "", "300 RECETAS", "CENAS EN 30 MINUTOS", "60 POSTRES"],
              "los textos grandes corridos una frase (el caso del 102) pasan a la frase que los dice; el que resume o ya esta en su sitio no se mueve", str(por_frase))
    cands = {c["frase"]: c for c in ma.candidatos_texto(fr_a, pal_a) if c["motivo"] == "guion"}
    comprobar(cands.get(3, {}).get("texto") == "300 RECETAS" and cands[3]["t0"] == pal_a[fr_a[3]["i0"]]["inicio"] and cands.get(4, {}).get("texto") == "CENAS EN 30 MINUTOS" and 2 not in cands,
              "el candidato de «300 RECETAS» entra con la frase que dice «300 Recetas», no con la anterior", str(cands))
    fr_b, pal_b = voz_de_frases(["Tienes 300 dinámicas listas para usar.", "Hoy te llevas todo en un solo lugar."], ["30 DINÁMICAS", "TODO EN UN SOLO LUGAR"])
    comprobar(ma.texto_grande_por_frase(fr_b, pal_b) == ["30 DINÁMICAS", "TODO EN UN SOLO LUGAR"], "«30» no es «300» y un texto cuyas palabras se dicen en su frase (o no se dicen en ninguna) no se mueve")
    fr_c, pal_c = voz_de_frases(["Tienes treinta dinámicas listas para usar.", "Con el color verde todo se ve mejor."], ["TODO EN COLOR VERDE", "COLOR VERDE"])
    comprobar(ma.texto_grande_por_frase(fr_c, pal_c) == ["", "COLOR VERDE"], "si dos textos caen en la misma frase, gana el que ya estaba en ella",  str(ma.texto_grande_por_frase(fr_c, pal_c)))
    fr_d, pal_d = voz_de_frases(["Escribe ahora mismo.", "Tienes garantía total."], ["GARANTÍA TOTAL", ""])
    comprobar(ma.texto_grande_por_frase(fr_d, pal_d) == ["", "GARANTÍA TOTAL"] and ma.texto_grande_por_frase([], []) == [], "tambien se corrige un texto puesto una frase DESPUES, y sin frases no falla")
    # producto
    carpeta = PUBLIC / "productos" / PROD
    ficha = leer_json(carpeta / "producto.json")
    if (carpeta / "portada.jpg").is_file():
        prod = ma.lugares_producto(palabras, dur, textos, cta, ma.RECETA_DEFECTO, carpeta, ficha)
        comprobar(1 <= len(prod) <= 3 and prod[0]["src"].endswith("portada.jpg"), "la portada va primero y le siguen hasta 2 paginas", f"{[p['src'] for p in prod]}")
        ocupados = [(t["inicio"], t["fin"]) for t in textos] + [(cta["inicio"], cta["inicio"] + cta["duracion"])]
        choque = [p for p in prod if any(p["inicio"] < b and p["inicio"] + p["duracion"] > a for a, b in ocupados)]
        comprobar(not choque, "el producto no queda encima de un texto grande ni del boton")
        sin = ma.lugares_producto(palabras, dur, textos, cta, ma.combinar(ma.RECETA_DEFECTO, {"producto": {"mostrar": False}}), carpeta, ficha)
        comprobar(sin == [], "con producto.mostrar = false no sale el producto")


class WhisperQueSeSalta:
    """Un Whisper de mentira que reproduce el fallo real: lo que la voz dice igual que la pista (initial_prompt) lo da por dicho y NO lo escribe; en su lugar suelta «www.mooji.com».
    `voz` son las palabras que de verdad dice la audio, una cada 0,4 s. Con se_salta=False se porta bien."""

    def __init__(self, voz: list[str], se_salta: bool = True):
        self.voz, self.se_salta, self.pistas = voz, se_salta, []

    def transcribe(self, audio, **kw):
        from types import SimpleNamespace as NS

        import transcribir as trn

        pista = kw.get("initial_prompt")
        self.pistas.append(pista)
        dichas = trn._fichas(pista or "") if self.se_salta else []
        fichas = [trn._fichas(w)[0] if trn._fichas(w) else w for w in self.voz]
        k = 0
        while k < len(dichas) and k < len(fichas) and dichas[k] == fichas[k]:
            k += 1
        t = lambda i: round(0.45 + 0.4 * i, 3)  # noqa: E731
        palabras = [NS(word=" " + w, start=t(i), end=t(i) + 0.34, probability=0.9) for i, w in enumerate(self.voz)]
        salida = []
        if k:
            salida.append(NS(word=" www.mooji.com", start=t(k - 1), end=t(k), probability=0.0))
        salida += palabras[k:]
        segs = [NS(start=g[0].start, end=g[-1].end, text=" ".join(w.word.strip() for w in g), words=g) for g in (salida[i:i + 8] for i in range(0, len(salida), 8))]
        return iter(segs), NS(language="es", duration=t(len(self.voz)))


def prueba_pista_del_guion() -> None:
    import os
    import subprocess as sp
    import tempfile

    import montaje_auto as ma
    import transcribir as trn

    print("\n[1b] la pista del guion (Whisper de mentira que reproduce el fallo) y la correccion con el guion")
    largo = " ".join(FRASES * 4)
    corto = " ".join(FRASES[:4])
    enorme = " ".join(f"Frase{k} numero{k} color{k} dinamica{k} reunion{k}." for k in range(150))     # todas distintas: si se repitieran, el arranque se encontraria mas adelante
    comprobar(len(largo) > 1500 and len(corto) < 400 and len(enorme) > 6000, "los tres guiones de prueba: uno mas largo que la pista (600 caracteres), otro que cabe entero en ella y otro larguisimo", f"{len(largo)}, {len(corto)} y {len(enorme)}")

    # la regla que mide cuanto del guion quedo escrito
    pal = lambda texto: [{"texto": w} for w in texto.split()]  # noqa: E731
    comprobar(trn.cobertura_guion(largo, pal(largo)) == 1.0, "cobertura: el guion entero escrito = 100 %")
    mitad = " ".join(largo.split()[len(largo.split()) // 2:])
    comprobar(0.4 < trn.cobertura_guion(largo, pal(mitad)) < 0.6, "cobertura: la segunda mitad sola = alrededor del 50 %", f"{trn.cobertura_guion(largo, pal(mitad)):.2f}")
    comprobar(trn.cobertura_guion("Escribe fieltro Navidad, ¡hoy!", pal("escribe FIELTRO navidad hoy")) == 1.0, "cobertura: no distingue mayusculas, tildes ni signos")
    comprobar(trn.cobertura_guion("", pal("algo")) == 1.0 and trn.cobertura_guion("algo", []) == 0.0, "cobertura: guion vacio = cubierto; nada escrito = 0")

    for nombre, guion in (("guion largo (se come solo los primeros 600 caracteres)", largo), ("guion corto (cabe entero en la pista: se comeria todo)", corto), ("guion larguisimo (lo comido es una parte chica del total)", enorme)):
        voz = guion.split()
        modelo, avisos = WhisperQueSeSalta(voz), []
        sola = trn.oir(WhisperQueSeSalta(voz), None, "es", guion[:600])[0]
        comprobar(trn.cobertura_con_pista(guion, sola) < trn.COBERTURA_MINIMA, f"{nombre}: el Whisper de mentira reproduce el fallo (con la pista solo deja el {trn.cobertura_con_pista(guion, sola):.0%} del guion y de su arranque)")
        palabras, segmentos, info, pista, cob = trn.oir_con_pista(modelo, None, "es", guion, aviso=avisos.append)
        comprobar(pista == "descartada" and cob >= 0.95, f"{nombre}: lo detecta, repite sin pista y deja el guion completo", f"{pista}, {cob}")
        comprobar(len(modelo.pistas) == 2 and modelo.pistas[0] == guion[:600] and modelo.pistas[1] is None, f"{nombre}: dos pasadas, la primera con la pista y la segunda sin ella", f"{len(modelo.pistas)}")
        comprobar(palabras and palabras[0]["inicio"] < 1.0 and not any("mooji" in p["texto"] for p in palabras), f"{nombre}: la voz empieza en el segundo 0 y no queda el «www.mooji.com»")
        comprobar(len(avisos) == 1 and "saltara" in avisos[0], f"{nombre}: lo avisa una vez", f"{avisos}")

    sola_enorme = trn.oir(WhisperQueSeSalta(enorme.split()), None, "es", enorme[:600])[0]
    comprobar(trn.cobertura_guion(enorme, sola_enorme) >= trn.COBERTURA_MINIMA, "con el guion larguisimo la cobertura TOTAL no alcanza a verlo (por eso se mira tambien el arranque)", f"{trn.cobertura_guion(enorme, sola_enorme):.0%}")
    bien = WhisperQueSeSalta(largo.split(), se_salta=False)
    avisos = []
    palabras, _s, _i, pista, cob = trn.oir_con_pista(bien, None, "es", largo, aviso=avisos.append)
    comprobar(pista == "guion" and cob >= 0.95 and len(bien.pistas) == 1 and not avisos, "un Whisper que se porta bien: una sola pasada, con la pista, sin aviso")
    otra = WhisperQueSeSalta("Esto no tiene nada que ver con el guion de la oferta".split(), se_salta=False)
    p2 = trn.oir_con_pista(otra, None, "es", largo, aviso=lambda m: None)
    comprobar(len(otra.pistas) == 2 and p2[3] == "descartada", "una voz que no dice el guion: repite UNA vez (no en bucle) y lo dice", f"{len(otra.pistas)} pasadas, {p2[3]}")
    sin = WhisperQueSeSalta(largo.split())
    p3 = trn.oir_con_pista(sin, None, "es", None)
    comprobar(sin.pistas == [None] and p3[3] == "ninguna" and p3[4] is None, "sin guion: una sola pasada sin pista y marcada «ninguna»")

    # corregir_con_guion: la voz LEE el guion, asi que lo escrito ahi arregla lo que Whisper oyo mal
    guion_c = "Estos adornos de fieltro no llevan puntadas. Todo paso a paso: un reno, un gnomo y una corona. Escribe fieltro navidad y empieza hoy. Incluye veinticinco moldes."
    oidas, _dur = hacer_palabras(["Estos adornos de fieltro no llevan puntadas.", "Todo pasó a paso: un reno, un nomo y una corona.", "Escrivenos Fiel Tronavidad y empieza hoy.", "Incluye 25 moldes."])
    copia = [dict(p) for p in oidas]
    corr, n = trn.corregir_con_guion(oidas, guion_c)
    texto = " ".join(p["texto"] for p in corr)
    esperado = "Estos adornos de fieltro no llevan puntadas. Todo paso a paso: un reno, un gnomo y una corona. Escribe fieltro navidad y empieza hoy. Incluye 25 moldes."
    comprobar(texto == esperado, "guion: arregla «nomo», «pasó a paso» y «Fiel Tronavidad» con lo que dice el guion, con sus signos y sin tocar la cifra «25»", texto)
    comprobar(n == 5, "guion: cuenta las palabras que cambio (nomo, pasó y las tres del final)", f"{n}")
    comprobar([(p["inicio"], p["fin"]) for p in corr] == [(p["inicio"], p["fin"]) for p in oidas] and oidas == copia, "guion: no toca los tiempos ni modifica la lista que recibe")
    sobra, n_sobra = trn.corregir_con_guion(hacer_palabras(["Todo muy pasó a paso."])[0], "Todo paso a paso.")
    comprobar(n_sobra == 1 and " ".join(p["texto"] for p in sobra) == "Todo muy paso a paso.", "guion: una palabra que sobra se deja como la oyo Whisper (y se corrige lo demas)", f"{n_sobra}: {' '.join(p['texto'] for p in sobra)}")
    otra, n_otra = trn.corregir_con_guion(hacer_palabras(["Esto no tiene nada que ver con la oferta."])[0], guion_c)
    comprobar(n_otra == 0 and " ".join(p["texto"] for p in otra) == "Esto no tiene nada que ver con la oferta.", "guion: una voz que dice otra cosa no se toca", f"{n_otra}")
    impro, n_impro = trn.corregir_con_guion(hacer_palabras(["Todo muy bonito."])[0], "Todo muy lindo.")
    comprobar(n_impro == 0 and " ".join(p["texto"] for p in impro) == "Todo muy bonito.", "guion: dos palabras distintas que no se parecen no se cambian (la voz pudo improvisar)", f"{n_impro}")
    signos, n_signos = trn.corregir_con_guion(hacer_palabras(["¿Tus adornos se mancan? (nomo)"])[0], "¿Tus adornos se manchan? gnomo")
    comprobar(n_signos == 2 and " ".join(p["texto"] for p in signos) == "¿Tus adornos se manchan? (gnomo)", "guion: la palabra corregida conserva los signos que traia pegados (? y parentesis)", f"{n_signos}: {' '.join(p['texto'] for p in signos)}")
    # palabras partidas, pegadas o repetidas (casos reales de las voces de APIMart de FIELTRO: «de ciclado», «se dé a profesional», «para para»)
    oidas_u, _d = hacer_palabras(["Empaques creativos con material de ciclado."])
    unidas, n_unidas = trn.corregir_con_guion(oidas_u, "Empaques creativos con material reciclado.")
    comprobar(" ".join(p["texto"] for p in unidas) == "Empaques creativos con material reciclado." and n_unidas == 2, "guion: «de ciclado» (una palabra que Whisper partio en dos) vuelve a ser «reciclado.»", f"{n_unidas}: {' '.join(p['texto'] for p in unidas)}")
    comprobar(unidas[-1]["inicio"] == oidas_u[4]["inicio"] and unidas[-1]["fin"] == oidas_u[5]["fin"] and len(unidas) == 5, "guion: la palabra unida ocupa el tiempo de las dos que reemplaza")
    oidas_v, _d = hacer_palabras(["Para que todo se dé a profesional."])
    vea, n_vea = trn.corregir_con_guion(oidas_v, "Para que todo se vea profesional.")
    comprobar(" ".join(p["texto"] for p in vea) == "Para que todo se vea profesional." and n_vea == 2, "guion: «dé a» (dos palabras sueltas) vuelve a ser «vea» cuando lo rodean palabras iguales", f"{n_vea}: {' '.join(p['texto'] for p in vea)}")
    borde, n_borde = trn.corregir_con_guion(hacer_palabras(["Dé a profesional hoy."])[0], "Vea profesional hoy.")
    comprobar(n_borde == 0, "guion: en el borde del texto (sin palabras iguales antes) se pide mas parecido y «dé a» no se cambia por «vea»", f"{n_borde}")
    dobles, n_dobles = trn.corregir_con_guion(hacer_palabras(["Una guía de colores para para que todo se vea."])[0], "Una guía de colores para que todo se vea.")
    comprobar(" ".join(p["texto"] for p in dobles) == "Una guía de colores para que todo se vea." and n_dobles == 1, "guion: una palabra repetida pegada a si misma («para para») que el guion no tiene se quita", f"{n_dobles}: {' '.join(p['texto'] for p in dobles)}")
    repite, n_repite = trn.corregir_con_guion(hacer_palabras(["Muy muy bonito."])[0], "Muy muy bonito.")
    comprobar(n_repite == 0 and len(repite) == 3, "guion: si el guion SI repite la palabra, se deja")
    oidas_p, _d = hacer_palabras(["Escribe fieltronavidad y empieza hoy."])
    pegadas, n_pegadas = trn.corregir_con_guion(oidas_p, "Escribe fieltro navidad y empieza hoy.")
    comprobar(" ".join(p["texto"] for p in pegadas) == "Escribe fieltro navidad y empieza hoy." and n_pegadas == 1, "guion: «fieltronavidad» (dos palabras pegadas) se separa en «fieltro navidad»", f"{n_pegadas}: {' '.join(p['texto'] for p in pegadas)}")
    comprobar(pegadas[1]["inicio"] == oidas_p[1]["inicio"] and pegadas[2]["fin"] == oidas_p[1]["fin"] and pegadas[1]["fin"] == pegadas[2]["inicio"], "guion: las dos palabras separadas se reparten el tiempo de la pegada")
    lejos, n_lejos = trn.corregir_con_guion(hacer_palabras(["Escribe ahora hoy."])[0], "Escribe fieltro navidad hoy.")
    comprobar(n_lejos == 0 and len(lejos) == 3, "guion: un grupo de palabras que no se parece al del guion no se cambia", f"{n_lejos}")
    vacio, n_vacio = trn.corregir_con_guion(hacer_palabras(["Hola mundo."])[0], "")
    comprobar(n_vacio == 0 and len(vacio) == 2, "guion: sin guion no cambia nada")
    comprobar(trn.corregir_con_guion([], guion_c) == ([], 0), "guion: sin palabras no falla")

    # diferencias_con_guion: parar ANTES de renderizar lo que no dice el guion (cada render son 15 a 20 minutos)
    ig = hacer_palabras(["Escribe fieltro navidad y empieza hoy."])[0]
    comprobar(trn.diferencias_con_guion(ig, "Escribe fieltro navidad y empieza hoy.") == [], "diferencias: iguales al guion = ninguna")
    comprobar(trn.diferencias_con_guion(hacer_palabras(["Incluye 25 moldes."])[0], "Incluye veinticinco moldes.") == [] and trn.diferencias_con_guion(hacer_palabras(["Incluye veinticinco moldes."])[0], "Incluye 25 moldes.") == [],
              "diferencias: una cifra en lugar de su nombre (y al reves) no cuenta")
    comprobar(trn.diferencias_con_guion(hacer_palabras(["ESCRÍBENOS, fieltro."])[0], "Escribenos fieltro") == [], "diferencias: las tildes, las mayusculas y los signos no cuentan")
    d1 = trn.diferencias_con_guion(hacer_palabras(["Con material de ciclado."])[0], "Con material reciclado.")
    comprobar(d1 == [{"dicen": ["de", "ciclado"], "guion": ["reciclado"]}], "diferencias: «de ciclado» contra «reciclado» es una diferencia", f"{d1}")
    d2 = trn.diferencias_con_guion(hacer_palabras(["Todo sin coser."])[0], "Todo sin coser ni hilo.")
    comprobar(len(d2) == 1 and d2[0]["dicen"] == [] and d2[0]["guion"] == ["ni", "hilo"], "diferencias: lo que el guion dice y la voz se comio se ve", f"{d2}")
    d3 = trn.diferencias_con_guion(hacer_palabras(["Todo muy sin coser."])[0], "Todo sin coser.")
    comprobar(len(d3) == 1 and d3[0]["dicen"] == ["muy"] and d3[0]["guion"] == [], "diferencias: lo que sobra en los subtitulos se ve", f"{d3}")

    # resumen_broll: cuantos videos limpios hay frente a los que piden los anuncios (con el tope de segundos por video de otras personas)
    import numpy as np

    class VideoDeMentira:
        def __init__(self, n):
            self.estricto, self.cada = np.ones(n, dtype=bool), 1.0

    class CatalogoDeMentira:
        def __init__(self, ns):
            self.videos = [VideoDeMentira(n) for n in ns]

        def segundos_limpios(self):
            return float(sum(v.estricto.sum() * v.cada for v in self.videos))

    r = ma.resumen_broll(CatalogoDeMentira([20] * 11), 43.0, 5.0, 3)
    comprobar(r == {"videos": 11, "segundos": 220, "por_anuncio": 9, "cantidad": 3, "faltan": 16}, "b-roll: 11 videos para 3 anuncios de 43 s con 5 s por video = faltan 16 (el caso de FIELTRO)", f"{r}")
    r = ma.resumen_broll(CatalogoDeMentira([20] * 30 + [1]), 43.0, 5.0, 3)
    comprobar(r["videos"] == 30 and r["faltan"] == 0, "b-roll: 30 videos alcanzan para 3 anuncios y un video de 1 s no cuenta", f"{r}")
    r = ma.resumen_broll(CatalogoDeMentira([20] * 5), 43.0, 0, 3)
    comprobar(r["por_anuncio"] == 1 and r["faltan"] == 0, "b-roll: sin tope por video basta 1 video por anuncio", f"{r}")

    # sin_negados: lo que la frase niega no se busca
    # lugares_producto: una portada que promete mas de lo que trae no se muestra
    with tempfile.TemporaryDirectory() as dp:
        carpeta_p = Path(dp) / "prodx"
        (carpeta_p / "paginas").mkdir(parents=True)
        (carpeta_p / "portada.jpg").write_bytes(b"x")
        for k in range(1, 5):
            (carpeta_p / "paginas" / f"p{k:03d}.jpg").write_bytes(b"x")
        pal_p = [{"texto": "ebook", "inicio": 2.0, "fin": 2.4}] + [{"texto": "otra", "inicio": 3.0 + k, "fin": 3.3 + k} for k in range(20)]
        base_f = {"nombre": "Prod X", "mejores_paginas_ebook": [2, 3, 4]}
        con = ma.lugares_producto(pal_p, 30.0, [], None, {}, carpeta_p, base_f)
        sin = ma.lugares_producto(pal_p, 30.0, [], None, {}, carpeta_p, {**base_f, "no_mostrar_portada": True})
        comprobar(len(con) == 3 and con[0]["src"].endswith("portada.jpg"), "producto: por defecto se muestra la portada y 2 paginas", f"{[x['src'].split('/')[-1] for x in con]}")
        comprobar(len(sin) == 3 and not any("portada" in x["src"] for x in sin) and [x["src"].split("/")[-1] for x in sin] == ["p002.jpg", "p003.jpg", "p004.jpg"],
                  "producto: con `no_mostrar_portada` solo salen paginas de adentro (una mas, para que el producto salga el mismo tiempo)", f"{[x['src'].split('/')[-1] for x in sin]}")
        (carpeta_p / "portada.jpg").unlink()
        comprobar(len(ma.lugares_producto(pal_p, 30.0, [], None, {}, carpeta_p, base_f)) == 0 and len(ma.lugares_producto(pal_p, 30.0, [], None, {}, carpeta_p, {**base_f, "no_mostrar_portada": True})) == 3,
                  "producto: sin portada.jpg no se muestra nada por defecto, pero con `no_mostrar_portada` las paginas salen igual")

    # sin_negados: lo que la frase niega no se busca
    comprobar(ma.sin_negados("Ni aguja, ni hilo, ni máquina de coser.") == "", "negados: una frase que solo niega («ni aguja, ni hilo, ni maquina de coser») queda vacia")
    comprobar(ma.sin_negados("Todo sin coser, paso a paso.") == "Todo, paso a paso.", "negados: «sin coser» sale y el resto queda con su puntuacion", ma.sin_negados("Todo sin coser, paso a paso."))
    comprobar(ma.sin_negados("Estos adornos de fieltro no llevan ni una sola puntada.") == "Estos adornos de fieltro no llevan.", "negados: «ni una sola puntada» sale y los adornos de fieltro se quedan", ma.sin_negados("Estos adornos de fieltro no llevan ni una sola puntada."))
    comprobar(ma.sin_negados("Hazlo en la mesa de tu cocina, sin desorden en casa.") == "Hazlo en la mesa de tu cocina, en casa.", "negados: «sin desorden» sale pero no se come lo que sigue («en casa»)", ma.sin_negados("Hazlo en la mesa de tu cocina, sin desorden en casa."))
    comprobar(ma.sin_negados("Solo necesitas fieltro, tijeras y silicón.") == "Solo necesitas fieltro, tijeras y silicón.", "negados: una frase sin negaciones no cambia")
    comprobar(ma.sin_negados("Ningún niño ni nadie. Singular.") == "Ningún niño ni nadie. Singular.".replace(" ni nadie", ""), "negados: «ningun» y «singular» no se confunden con «ni» y «sin»", ma.sin_negados("Ningún niño ni nadie. Singular."))

    # tomas_de_anuncios_previos: se evitan los tramos de TODOS los anuncios anteriores, no solo del ultimo
    prod_h = "_prueba_previos"
    ruta_h = ANUNCIOS / prod_h / "historial.json"
    ruta_h.parent.mkdir(parents=True, exist_ok=True)
    escribir_json(ruta_h, {"producto": prod_h, "anuncios": [
        {"anuncio": "AD1", "tomas_usadas": [{"src": "a.mp4", "desde": 1.0, "hasta": 3.0}]},
        {"anuncio": "AD2", "tomas_usadas": [{"src": "b.mp4", "desde": 5.0, "hasta": 7.0}, {"src": "raro"}]},
    ]})
    try:
        comprobar(sorted(ma.tomas_de_anuncios_previos(prod_h, "AD3")) == [("a.mp4", 1.0, 3.0), ("b.mp4", 5.0, 7.0)], "previos: AD3 evita los tramos de AD1 Y de AD2 (y se salta una toma mal formada)", f"{ma.tomas_de_anuncios_previos(prod_h, 'AD3')}")
        comprobar(ma.tomas_de_anuncios_previos(prod_h, "AD2") == [("a.mp4", 1.0, 3.0)], "previos: AD2 evita solo los de AD1 (los suyos de una corrida anterior no cuentan)")
    finally:
        shutil.rmtree(ANUNCIOS / prod_h, ignore_errors=True)
    comprobar(ma.tomas_de_anuncios_previos("producto_que_no_existe", "AD1") == [], "previos: sin historial no hay nada que evitar")

    # montaje_auto: ya no usa la pista; una transcripcion VIEJA (sin `pista`) con guion al lado se repite sola; las nuevas se respetan; corrige con el guion solo en memoria
    llamadas: list[list[str]] = []
    oidas_texto = " ".join(p["texto"] for p in oidas)
    heard = [{"texto": w, "inicio": 0.4 * i, "fin": 0.4 * i + 0.3, "prob": 0.9} for i, w in enumerate(oidas_texto.split())]

    def falso_run(cmd, **kw):
        from types import SimpleNamespace as NS

        llamadas.append([str(c) for c in cmd])
        escribir_json(Path(cmd[cmd.index("--salida") + 1]), {"palabras": heard, "pista": "ninguna"})
        return NS(returncode=0, stdout="", stderr="")

    def caso(guardada: dict, con_guion: bool = True) -> tuple[list[list[str]], dict, dict]:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            voz, guion_txt = base / "voz.wav", base / "AD1.txt"
            voz.write_bytes(b"RIFF")
            guion_txt.write_text(guion_c, encoding="utf-8")
            escribir_json(base / "transcripcion.json", guardada)
            os.utime(voz, (time.time() - 60, time.time() - 60))
            llamadas.clear()
            viejo, sp.run = sp.run, falso_run
            try:
                t = ma.obtener_transcripcion(voz, base, "es", guion_txt if con_guion else None, mostrar=lambda *a: None)
            finally:
                sp.run = viejo
            return list(llamadas), t, leer_json(base / "transcripcion.json")

    ll, t, disco = caso({"palabras": heard})
    comprobar(len(ll) == 1 and "--guion" not in ll[0], "montaje: una transcripcion vieja (sin `pista`) con guion al lado se repite sola y el comando ya NO lleva --guion", f"{len(ll)} llamadas")
    ll, t, disco = caso({"palabras": heard}, con_guion=False)
    comprobar(not ll, "montaje: sin guion no hay por que dudar de la transcripcion y no se repite")
    ll, t, disco = caso({"palabras": heard, "pista": "guion", "cobertura_guion": 0.97})
    comprobar(len(ll) == 1, "montaje: una transcripcion hecha CON el guion de pista (`pista` = guion) tampoco es de fiar (puede traer palabras torcidas) y se repite", f"{len(ll)} llamadas")
    ll, t, disco = caso({"palabras": heard, "pista": "descartada", "cobertura_guion": 0.97})
    comprobar(not ll, "montaje: una transcripcion con la pista descartada (salio de una pasada sin pista) se usa tal cual")
    ll, t, disco = caso({"palabras": heard, "alineado_con_guion": {"guion_sha1": "x"}})
    comprobar(not ll, "montaje: la transcripcion de voz_auto.py (`alineado_con_guion`, sin la clave `pista`) NO se repite aunque haya un guiones/<AD>.txt al lado", f"{len(ll)} llamadas")
    comprobar(" ".join(p["texto"] for p in t["palabras"]) == oidas_texto, "montaje: y sus palabras (las del guion) no se «corrigen» con otro texto")
    ll, t, disco = caso({"palabras": heard, "pista": "ninguna"})
    comprobar(not ll, "montaje: una transcripcion que ya trae `pista` (hecha por la version nueva) se usa tal cual")
    comprobar(" ".join(p["texto"] for p in t["palabras"]) == esperado, "montaje: las palabras que usa el anuncio salen corregidas con el guion", " ".join(p["texto"] for p in t["palabras"]))
    comprobar(" ".join(p["texto"] for p in disco["palabras"]) == oidas_texto, "montaje: el json de la transcripcion queda como lo dio Whisper (la correccion es solo en memoria)")


def prueba_analisis_y_emparejamiento(motor, detector) -> dict:
    import emparejar_tomas as et
    import numpy as np

    print("\n[2] analisis del b-roll y emparejamiento (motor y detector de mentira)")
    t0 = time.time()
    r = et.indexar(PROD, 1.0, motor=motor, detector=detector, mostrar=lambda *a: None)
    comprobar(r["analizados"] == 4 and r["tramos"] >= 8, "se analizan los 4 videos y salen tramos de cada escena", f"{r}")
    carpeta = PUBLIC / "productos" / PROD / "broll-tiktok"
    indice = leer_json(carpeta / "indice.json")
    e = indice["videos"]["rojo.mp4"]
    comprobar(e.get("revisado") is True and e.get("revisado_por") == "auto-clip" and e["tramos"], "lo automatico queda en `tramos` con revisado_por = auto-clip")
    comprobar((carpeta / "_clip" / "rojo.npz").is_file(), "los vectores quedan en _clip/<video>.npz")
    cortes = e["clip"]["cortes"]
    comprobar(len(cortes) == 1 and abs(cortes[0] - 16.0) < 0.5, "se ve el corte de escena de los 16 s", f"{cortes}")
    tr = e["tramos"]
    comprobar(len(tr) == 2 and abs(tr[0]["hasta"] - 16.0) < 0.6, "el video se parte en dos tramos en el corte", f"{[(t['desde'], t['hasta']) for t in tr]}")
    am = indice["videos"]["amarillo.mp4"]["tramos"]
    comprobar(any(t["calidad"] == "mala" and t["desde"] >= 15 for t in am) and any(t["calidad"] == "buena" and t["hasta"] <= 17 for t in am),
              "el tramo con la barra de texto sale `mala` y el limpio `buena`", f"{[(t['desde'], t['hasta'], t['calidad'], t['motivos']) for t in am]}")
    comprobar(e.get("calidad_video") in ("limpio", "medio") and float(e.get("limpio_seg", 0)) > 10, "cada video trae la calidad del video entero (calidad_video, limpio_seg)",
              f"{e.get('calidad_video')} {e.get('limpio_seg')}")
    comprobar(indice["videos"]["amarillo.mp4"]["limpio_seg"] < indice["videos"]["azul.mp4"]["limpio_seg"] or indice["videos"]["amarillo.mp4"]["calidad_video"] != "malo",
              "el video con la mitad de texto tiene menos segundos limpios que uno sin texto")
    n_rec = et.recalcular(PROD, motor=motor, mostrar=lambda *a: None)
    comprobar(n_rec == 4 and leer_json(carpeta / "indice.json")["videos"]["rojo.mp4"]["tramos"], "--recalcular vuelve a calcular los tramos de los 4 videos sin decodificar video")
    # volver a correr no repite el trabajo
    r2 = et.indexar(PROD, 1.0, motor=motor, detector=detector, mostrar=lambda *a: None)
    comprobar(r2["analizados"] == 0 and r2["al_dia"] == 4, "una segunda corrida no repite lo ya analizado")

    cat = et.Catalogo(PROD)
    comprobar(len(cat.videos) == 4 and not cat.faltan, "el catalogo carga los 4 videos")
    colores = ["rojo", "rojo", "verde", "verde", "azul", "azul", "amarillo", "amarillo"]
    consultas = [{"vec": motor.textos([f"el color {c}"])[0], "dur": 1.8 + 0.1 * (k % 3), "gancho": k == 0, "texto": c} for k, c in enumerate(colores)]
    elegidas = et.emparejar(cat, consultas, [])
    comprobar(all(x is not None for x in elegidas), "cada frase recibe una toma")
    bien = sum(1 for c, x in zip(colores, elegidas) if x and x["video"] == f"{c}.mp4")
    comprobar(bien == len(colores), "cada frase recibe la toma de SU color", f"{bien} de {len(colores)}: {[x['video'] if x else None for x in elegidas]}")
    for i in range(len(elegidas)):
        for j in range(i + 1, len(elegidas)):
            a, b = elegidas[i], elegidas[j]
            if a and b and a["video"] == b["video"]:
                comprobar(a["desde"] + a["dur"] <= b["desde"] + 1e-6 or b["desde"] + b["dur"] <= a["desde"] + 1e-6, f"las tomas {i + 1} y {j + 1} del mismo video no se solapan")
    cruza = [(x["video"], x["desde"]) for x in elegidas if x and x["desde"] + 0.15 < 16.0 < x["desde"] + x["dur"] - 0.15]
    comprobar(not cruza, "ninguna toma cruza el corte de escena de los 16 s", f"{cruza}")
    texto = [(x["video"], x["desde"]) for x in elegidas if x and x["video"] == "amarillo.mp4" and x["desde"] + x["dur"] > 15.0 and x["desde"] < 32.2]
    comprobar(not texto, "ninguna toma usa los fotogramas con texto quemado", f"{texto}")
    # sin repetir las del anuncio anterior
    evitar = [(x["ruta"], x["desde"], x["desde"] + x["dur"]) for x in elegidas if x]
    otra = et.emparejar(cat, consultas, evitar)
    solapa = [1 for x in otra if x and x["relajada"] < 2 for (ruta, a, b) in evitar if x["ruta"] == ruta and x["desde"] < b + 0.3 and x["desde"] + x["dur"] > a - 0.3]
    comprobar(all(x is not None for x in otra) and not solapa, "evitando las tomas del anuncio anterior no repite ninguna (si hay otras)", f"{len(solapa)} solapadas")
    solo = et.emparejar(cat, consultas, evitar + [(f"productos/{PROD}/broll-tiktok/{c}.mp4", 0.0, 30.0) for c in COLORES])        # todo prohibido: solo queda repetir
    comprobar(all(x is not None and x["relajada"] == 2 for x in solo), "si no queda material nuevo, repite antes que dejar un hueco, y lo marca (relajada = 2)")
    # el tope de segundos de un mismo video de otra persona (la regla del dueño: 3 a 5 s de cada uno)
    comprobar(et.es_ajeno("tt_7123.mp4") and et.es_ajeno("TT_9.MP4") and not et.es_ajeno("pin_9.mp4") and not et.es_ajeno("rojo.mp4") and not et.es_ajeno(""),
              "es_ajeno: lo que baja el programa de TikTok (tt_) es de otras personas; Pinterest no se usa y los demas videos tampoco cuentan")
    pide_rojo = [{"vec": motor.textos(["el color rojo"])[0], "dur": 2.0, "gancho": k == 0, "texto": "rojo"} for k in range(6)]
    sin_regla = et.emparejar(cat, pide_rojo, [])
    comprobar(all(x is not None for x in sin_regla) and sum(x["dur"] for x in sin_regla if x["video"] == "rojo.mp4") > 4.0,
              "sin tope, seis tomas del color rojo sacan mas de 4 s de un mismo video (el rojo; la variedad solo reparte las ultimas)", f"{[x['video'] if x else None for x in sin_regla]}")
    comprobar([x["video"] if x else None for x in et.emparejar(cat, pide_rojo, [], {"tope_seg_video": 4.0})] == [x["video"] if x else None for x in sin_regla],
              "el tope solo vale para videos de otras personas: estos de prueba no se llaman tt_, asi que no los toca")
    antes_pref = et.PREFIJOS_AJENOS
    et.PREFIJOS_AJENOS = ("",)                                  # mientras dura esta comprobacion, los 4 videos de prueba hacen de videos de otras personas
    try:
        con_tope = et.emparejar(cat, pide_rojo, [], {"tope_seg_video": 4.0})
        seg_por_video: dict[str, float] = {}
        for x in con_tope:
            if x:
                seg_por_video[x["video"]] = seg_por_video.get(x["video"], 0.0) + x["dur"]
        comprobar(all(x is not None for x in con_tope) and max(seg_por_video.values()) <= 4.0 + 1e-6 and seg_por_video.get("rojo.mp4") == 4.0 and len(seg_por_video) >= 3,
                  "con tope de 4 s ningun video pasa de 4 s en total: el rojo da sus 4 y el resto sale de los otros videos", f"{seg_por_video}")
        comprobar(all(x is None for x in et.emparejar(cat, pide_rojo[:2], [], {"tope_seg_video": 1.0})), "un tope menor que la toma deja sin material (la regla no se salta ni para no dejar un hueco)")
        ya_usado = et.emparejar(cat, pide_rojo, [(x["ruta"], x["desde"], x["desde"] + x["dur"]) for x in con_tope if x], {"tope_seg_video": 4.0})
        suma_dos: dict[str, float] = {}
        for x in ya_usado:
            if x:
                suma_dos[x["video"]] = suma_dos.get(x["video"], 0.0) + x["dur"]
        comprobar(all(v <= 4.0 + 1e-6 for v in suma_dos.values()), "el tope tambien se cumple cuando se evitan las tomas del anuncio anterior (aunque haya que relajar)", f"{suma_dos}")
    finally:
        et.PREFIJOS_AJENOS = antes_pref
    # lo revisado a mano manda: un tramo manual `mala` no se usa
    ruta_i = carpeta / "indice.json"
    ind = leer_json(ruta_i)
    ind["videos"]["rojo.mp4"]["tramos"] = [{"desde": 0.0, "hasta": 8.0, "que_se_ve": "x", "etiquetas": [], "calidad": "mala"},
                                           {"desde": 8.0, "hasta": 16.0, "que_se_ve": "y", "etiquetas": [], "calidad": "buena"}]
    ind["videos"]["rojo.mp4"]["revisado_por"] = "claude"
    escribir_json(ruta_i, ind)
    cat2 = et.Catalogo(PROD)
    otra = et.emparejar(cat2, consultas[:2], [])
    comprobar(all(x and x["video"] != "rojo.mp4" or (x and x["desde"] >= 8.0 - 1e-6) for x in otra), "un tramo que una persona marco `mala` no se usa, aunque sea el mas parecido",
              f"{[(x['video'], x['desde']) if x else None for x in otra]}")
    # y la comparacion con lo manual no revienta y cuenta fotogramas
    cmp_ = et.comparar_con_manual(PROD, mostrar=lambda *a: None)
    comprobar(cmp_["total"] > 0, "la comparacion con lo manual cuenta fotogramas", f"{cmp_['total']}")
    # `pisar_manual` guarda lo viejo
    et.indexar(PROD, 1.0, rehacer=True, solo=["rojo"], motor=motor, detector=detector, pisar_manual=True, mostrar=lambda *a: None)
    ind = leer_json(ruta_i)
    comprobar("tramos_manual" in ind["videos"]["rojo.mp4"] and ind["videos"]["rojo.mp4"]["revisado_por"] == "auto-clip", "--pisar-manual deja lo automatico y guarda lo manual en `tramos_manual`")
    prueba_marcas(motor, detector, consultas, elegidas, evitar)
    print(f"  (analisis + emparejamiento con motor de mentira: {time.time() - t0:.0f} s)")
    return {"elegidas": elegidas}


def prueba_franjas_negras() -> None:
    """Las franjas negras arriba y abajo de un clip (el creador lo exporto asi): no son un cuadro negro y el AD3 de FIELTRO salio con ellas sin que nada avisara."""
    import os
    import random
    import tempfile

    import emparejar_tomas as et

    print("\n[2b] franjas negras arriba y abajo")
    W, H = 108, 192

    def cuadro(fondo: int, arriba: int = 0, abajo: int = 0, ruido: int = 0, centro: int | None = None) -> bytes:
        rnd = random.Random(5)
        filas = []
        for y in range(H):
            if y < arriba or y >= H - abajo:
                v = 0
            elif centro is not None and int(H * 0.45) <= y < int(H * 0.55):
                v = centro
            else:
                v = fondo
            filas.append(bytes(max(0, min(255, v + (rnd.randint(-ruido, ruido) if ruido else 0))) for _ in range(W)))
        return b"".join(filas)
    comprobar(et.cuadro_con_franjas(cuadro(150, 24, 24), W, H), "franjas: un cuadro con 12 % de negro arriba y abajo y la imagen en medio se detecta")
    comprobar(et.cuadro_con_franjas(cuadro(150, 24, 24, ruido=3), W, H), "franjas: y tambien con el ruido de la compresion en el negro")
    comprobar(not et.cuadro_con_franjas(cuadro(150, 24, 0), W, H) and not et.cuadro_con_franjas(cuadro(150, 0, 24), W, H), "franjas: solo arriba o solo abajo no cuenta (puede ser un cielo oscuro)")
    comprobar(not et.cuadro_con_franjas(cuadro(150, 1, 1), W, H), "franjas: una linea fina en el borde no cuenta")
    comprobar(not et.cuadro_con_franjas(cuadro(0), W, H), "franjas: un cuadro entero negro NO es franja (eso lo ve sin_cuadros_negros)")
    comprobar(not et.cuadro_con_franjas(cuadro(150), W, H), "franjas: un cuadro con imagen hasta los bordes no se marca")
    comprobar(not et.cuadro_con_franjas(cuadro(20, ruido=15), W, H), "franjas: una escena oscura con grano (como las de los videos de prueba) no se toma por franjas")
    comprobar(not et.cuadro_con_franjas(cuadro(10, 24, 24, centro=10), W, H), "franjas: con la imagen tambien oscura (un fundido a negro) no se marca")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        clip = d / "barras.mp4"
        # 9 s grises; franjas de 12 % de 0 a 3 s y de 6 a 9 s, y un destello de 0,2 s hacia los 4,5 s
        en = "enable='lt(t,3)+gte(t,6)+between(t,4.5,4.7)'"
        ff("-f", "lavfi", "-i", "color=c=0x9a9a9a:s=270x480:r=30:d=9", "-vf", f"drawbox=x=0:y=0:w=270:h=58:color=black:t=fill:{en},drawbox=x=0:y=422:w=270:h=58:color=black:t=fill:{en}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip))
        todos, firmes = et.franjas_negras(clip, 1), et.franjas_negras(clip, 2)
        comprobar(len(todos) == 3 and abs(todos[0][0]) < 0.3 and 2.7 <= todos[0][1] <= 3.3 and 5.7 <= todos[2][0] <= 6.3 and todos[2][1] >= 8.7, "franjas: un video con franjas de 0 a 3 s y de 6 a 9 s sale con esos tramos (y el destello)", f"{todos}")
        comprobar(len(firmes) == 2 and firmes[0] == todos[0] and firmes[1] == todos[2], "franjas: con `min_muestras=2` el destello de un solo cuadro no cuenta", f"{firmes}")
        carp_clip = d / "_clip"
        r = et.bandas_negras_de(clip, carp_clip)
        guardado = carp_clip / "barras.bandas.json"
        comprobar(r == et.franjas_negras(clip) and guardado.is_file(), "franjas: la respuesta queda guardada en _clip/<video>.bandas.json")
        datos = leer_json(guardado)
        datos["tramos"] = [[1.0, 2.0]]
        escribir_json(guardado, datos)
        comprobar(et.bandas_negras_de(clip, carp_clip) == [(1.0, 2.0)], "franjas: mientras el archivo no cambie se lee de la copia guardada (no se vuelve a medir)")
        os.utime(clip, (clip.stat().st_atime, clip.stat().st_mtime + 5))
        comprobar(et.bandas_negras_de(clip, carp_clip) == r, "franjas: si el archivo cambia (la fecha) se vuelve a medir")
        guardado.write_text("{esto no es json", encoding="utf-8")
        comprobar(et.bandas_negras_de(clip, carp_clip) == r, "franjas: una copia daniada se ignora y se vuelve a medir")
        comprobar(et.bandas_negras_de(d / "no-existe.mp4", carp_clip) == [] and et.franjas_negras(d / "no-existe.mp4") == [], "franjas: un video que no existe (o que ffmpeg no lee) da [] y no revienta")
    for c in COLORES:
        comprobar(et.franjas_negras(PUBLIC / "productos" / PROD / "broll-tiktok" / f"{c}.mp4") == [], f"franjas: el video de prueba {c} (con su rejilla y su grano, hasta la escena oscura) no se marca")


def prueba_marcas(motor, detector, consultas: list[dict], elegidas: list[dict], evitar: list[tuple]) -> None:
    """Las marcas de una persona: lo que el automatico no ve (un nino, un logo) se marca a mano y NUNCA se vuelve a usar."""
    import emparejar_tomas as et

    ruta_i = PUBLIC / "productos" / PROD / "broll-tiktok" / "indice.json"
    silencio = lambda *a: None  # noqa: E731
    # los tramos se escriben como los escribe una persona
    comprobar(et.leer_tramo("12-15,5") == (12.0, 15.5) and et.leer_tramo("3 a 7 s") == (3.0, 7.0) and et.leer_tramo("todo") is None and et.leer_tramo((1, 2)) == (1.0, 2.0),
              "los tramos se escriben como 12-15.5, «3 a 7 s» o «todo»")
    comprobar(sale(et.leer_tramo, "abc") and sale(et.leer_tramo, "9-4"), "un tramo que no se entiende, o que termina antes de empezar, detiene todo con un mensaje")
    comprobar(sale(et.marcar, PROD, "noexiste", "1-2") and sale(et.marcar, PROD, "o", "1-2") and sale(et.marcar, PROD, "rojo", "500-600") and sale(et.marcar, PROD, "rojo", "1-2", "rara"),
              "no se marca un video que no existe, uno ambiguo, un tramo despues del final ni una calidad rara")
    # marcar justo la toma que el automatico habia escogido para la primera frase
    a, b = elegidas[0]["desde"], elegidas[0]["desde"] + elegidas[0]["dur"]
    video = elegidas[0]["video"]
    mensajes: list[str] = []
    m1 = et.marcar(PROD, video.split(".")[0], (a, b), "mala", "se ve un nino", mostrar=mensajes.append)
    marcas = leer_json(ruta_i)["videos"][video].get("marcas") or []
    comprobar(len(marcas) == 1 and marcas[0]["calidad"] == "mala" and marcas[0]["motivo"] == "se ve un nino" and abs(marcas[0]["desde"] - a) < 0.011 and abs(marcas[0]["hasta"] - b) < 0.011,
              "--marcar deja la marca en indice.json (desde, hasta, calidad y motivo)", f"{marcas}")
    comprobar(m1["quedaba_seg"] is not None and m1["queda_seg"] < m1["quedaba_seg"] and "nunca se va a usar" in mensajes[0], "al marcar dice cuantos segundos utilizables le quedan al video",
              f"{m1} {mensajes}")

    def sin_la_marca(cat) -> tuple[list[dict | None], bool]:
        otras = et.emparejar(cat, consultas, [])
        toca = [x for x in otras if x and x["video"] == video and x["desde"] < b and x["desde"] + x["dur"] > a]
        return otras, not toca

    cat = et.Catalogo(PROD)
    vr = next(v for v in cat.videos if v.nombre == video)
    dentro, lejos = (vr.t >= a) & (vr.t <= b), (vr.t < a - 1.0) | (vr.t > b + 1.0)
    comprobar(bool(dentro.any()) and bool(vr.prohibido[dentro].all()) and not vr.prohibido[lejos].any() and int(vr.prohibido.sum()) <= int(dentro.sum()) + 2 and not (vr.limpio & vr.prohibido).any(),
              "la marca prohibe los fotogramas de esa toma (y no los que estan lejos)", f"{vr.t[vr.prohibido]}")
    otras, ok = sin_la_marca(cat)
    comprobar(ok and all(x is not None for x in otras), "ninguna toma cae en lo marcado `mala` y el anuncio no se queda con huecos", f"{[(x['video'], x['desde']) if x else None for x in otras]}")
    # sobrevive a repetir el analisis y a recalcular los tramos
    et.indexar(PROD, 1.0, rehacer=True, solo=[video.split(".")[0]], motor=motor, detector=detector, mostrar=silencio)
    et.recalcular(PROD, motor=motor, mostrar=silencio)
    comprobar(len(leer_json(ruta_i)["videos"][video].get("marcas") or []) == 1 and sin_la_marca(et.Catalogo(PROD))[1], "la marca sobrevive a repetir el analisis (--rehacer) y a --recalcular")
    # `media` solo pesa; `mala` en TODO el b-roll: no hay nada que usar, y NO se usa ni siquiera para no dejar huecos (a diferencia de lo demas)
    et.marcar(PROD, "verde", "0-4", "media", mostrar=silencio)
    vv = next(v for v in et.Catalogo(PROD).videos if v.nombre == "verde.mp4")
    comprobar((vv.blando[:3] >= 0.5).all() and not vv.prohibido.any() and vv.calidad[1] == "media", "una marca `media` solo hace preferir otra toma (no prohibe)")
    et.quitar_marcas(PROD, "verde", "1-2", mostrar=silencio)
    for c in COLORES:
        et.marcar(PROD, c, "todo", "mala", mostrar=silencio)
    todo = et.emparejar(et.Catalogo(PROD), consultas, evitar + [(f"productos/{PROD}/broll-tiktok/{c}.mp4", 0.0, 30.0) for c in COLORES])
    comprobar(all(x is None for x in todo), "con todo marcado `mala` no se usa nada, ni siquiera repitiendo para no dejar huecos")
    # una marca que no se entiende NO se ignora: detiene el montaje (fail-closed)
    ind = leer_json(ruta_i)
    ind["videos"]["azul.mp4"]["marcas"] = [{"desde": "abc", "hasta": 5}]
    escribir_json(ruta_i, ind)
    comprobar(sale(et.Catalogo, PROD), "una marca que no se entiende detiene el montaje (no se ignora en silencio)")
    ind["videos"]["azul.mp4"]["marcas"] = [{"desde": 5, "hasta": 3, "calidad": "mala"}]
    escribir_json(ruta_i, ind)
    comprobar(sale(et.Catalogo, PROD), "una marca que termina antes de empezar tambien detiene el montaje")
    # listar y quitar
    ind = leer_json(ruta_i)
    ind["videos"]["azul.mp4"].pop("marcas")
    escribir_json(ruta_i, ind)
    listado: list[str] = []
    comprobar(et.listar_marcas(PROD, mostrar=listado.append) >= 4 and any("se ve un nino" in x for x in listado), "--marcas lista lo marcado con su motivo", f"{listado[:3]}")
    borradas = sum(et.quitar_marcas(PROD, c, mostrar=silencio) for c in COLORES)
    ind = leer_json(ruta_i)
    comprobar(borradas >= 4 and not any(e.get("marcas") for e in ind["videos"].values()) and et.listar_marcas(PROD, mostrar=silencio) == 0, "--quitar-marca borra las marcas", f"{borradas}")
    cat = et.Catalogo(PROD)
    comprobar(not any(v.prohibido.any() for v in cat.videos), "sin marcas, los videos vuelven a estar completos")
    # franjas negras: un tramo que trae franjas (las trae el video original) se prohibe solo, sin que nadie lo marque, como una marca `mala`
    carpeta_v = PUBLIC / "productos" / PROD / "broll-tiktok"

    def poner_franjas(nombre: str, tramos: list[list[float]]) -> None:
        st = (carpeta_v / nombre).stat()
        escribir_json(carpeta_v / "_clip" / f"{Path(nombre).stem}.bandas.json", {"firma": {"tam": st.st_size, "mtime": int(st.st_mtime), "v": et.BARRAS["version"]}, "tramos": tramos})
    poner_franjas("rojo.mp4", [[2.0, 6.0]])
    vr = next(v for v in et.Catalogo(PROD).videos if v.nombre == "rojo.mp4")
    dentro, fuera = (vr.t >= 2.0) & (vr.t <= 6.0), (vr.t < 1.0) | (vr.t > 7.0)
    comprobar(bool(dentro.any()) and bool(vr.prohibido[dentro].all()) and not vr.prohibido[fuera].any() and not (vr.limpio & vr.prohibido).any() and vr.calidad[3] == "mala",
              "franjas: un tramo con franjas negras prohibe esos fotogramas (y no los que estan lejos)", f"{vr.t[vr.prohibido]}")
    otras = et.emparejar(et.Catalogo(PROD), consultas, [])
    toca = [(x["video"], x["desde"]) for x in otras if x and x["video"] == "rojo.mp4" and x["desde"] < 6.0 and x["desde"] + x["dur"] > 2.0]
    comprobar(not toca and all(x is not None for x in otras), "franjas: ninguna toma cae en un tramo con franjas negras y el anuncio no se queda con huecos", f"{toca}")
    for c in COLORES:
        poner_franjas(f"{c}.mp4", [[0.0, 40.0]])
    comprobar(all(x is None for x in et.emparejar(et.Catalogo(PROD), consultas, evitar + [(f"productos/{PROD}/broll-tiktok/{c}.mp4", 0.0, 30.0) for c in COLORES])),
              "franjas: con todo el b-roll con franjas no se usa nada, ni repitiendo para no dejar huecos")
    for c in COLORES:
        (carpeta_v / "_clip" / f"{c}.bandas.json").unlink(missing_ok=True)
    comprobar(not any(v.prohibido.any() for v in et.Catalogo(PROD).videos), "franjas: sin franjas detectadas los videos vuelven a estar completos")


def prueba_montaje(motor, detector) -> None:
    import montaje_auto as ma

    print("\n[3] montaje completo sin render")
    base, palabras, dur_voz = preparar_anuncio(FRASES)
    informe = ma.montar(PROD, AD, sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=lambda *a: None)
    comprobar(informe["estado"] == "sin-render", "el montaje termina (sin render)", f"{informe.get('estado')}")
    for nombre in ("videoData.json", "plan.md", "evidencia.png", "informe_calidad.json", "receta_aplicada.json"):
        comprobar((base / nombre).is_file(), f"deja {nombre}")
    vd = leer_json(base / "videoData.json")
    comprobar(not informe.get("errores_validador"), "el validador no encuentra errores", f"{informe.get('errores_validador')}")
    comprobar(abs(vd["meta"]["duracion"] - (dur_voz + ma.COLA_SEG)) < 0.2, "la duracion es la de la voz mas la cola", f"{vd['meta']['duracion']}")
    t = sorted(vd["tomas"], key=lambda x: x["inicio"])
    comprobar(abs(t[0]["inicio"]) < 1e-6 and all(abs(t[k]["inicio"] + t[k]["duracion"] - t[k + 1]["inicio"]) < 0.01 for k in range(len(t) - 1)) and abs(t[-1]["inicio"] + t[-1]["duracion"] - vd["meta"]["duracion"]) < 0.05,
              "las tomas tapan todo el anuncio sin huecos")
    comprobar(not ma.tomas_repetidas(vd["tomas"]), "ninguna toma repetida en el videoData")
    comprobar(all(0.0 <= x["desde"] and x["src"].startswith(f"productos/{PROD}/broll-tiktok/") for x in vd["tomas"]), "las rutas de las tomas son relativas a public/")
    comprobar(all("transicion" in x for x in t[1:]) or any("transicion" in x for x in t), "aplicar_receta puso transiciones")
    comprobar(vd["audio"]["voz"]["src"].endswith("voz_norm.wav") and vd["zonaInferiorLibre"] == 280, "la voz va normalizada y la zona inferior libre es de 280 px")
    comprobar(5 <= len(vd["textosGrandes"]) <= 7 and vd["cta"]["inicio"] + vd["cta"]["duracion"] <= vd["meta"]["duracion"] + 0.05, "textos grandes y boton final dentro del anuncio")
    comprobar(len(vd["producto"]) >= 1, "se muestra la portada del ebook", f"{vd['producto']}")
    # cada toma debe ser del color de su frase: el video de origen se llama como el color de la frase que se dice en ese corte
    mal = []
    for x in t:
        dicho = " ".join(p["texto"] for p in vd["palabras"] if x["inicio"] - 1e-6 <= p["inicio"] < x["inicio"] + x["duracion"]).lower()
        color = next((c for c in COLORES if c in dicho), None)
        if color and not x["src"].endswith(f"{color}.mp4"):
            mal.append((round(x["inicio"], 1), color, x["src"].split("/")[-1]))
    comprobar(not mal, "cada corte donde se nombra un color usa el video de ese color", f"{mal[:4]}")
    from PIL import Image

    ev = Image.open(base / "evidencia.png")
    comprobar(ev.width >= 800 and ev.height >= 300, "evidencia.png es una imagen con una miniatura por corte", f"{ev.size}")
    hojas = sorted(base.glob("revision_tomas_*.png"))
    comprobar(len(hojas) == -(-len(vd["tomas"]) // 12) and Image.open(hojas[0]).width >= 1000, "revision_tomas_N.png: una hoja por cada 12 tomas, con el inicio, la mitad y el final de cada una", f"{[h.name for h in hojas]} de {len(vd['tomas'])} tomas")
    bv = informe["compuertas"].get("broll_variedad")
    comprobar(bv is not None and bv["ok"] is True and "videos limpios" in bv["detalle"], "el control de calidad dice cuanto b-roll limpio hay frente a los anuncios que pide la receta", f"{bv}")
    plan = (base / "plan.md").read_text(encoding="utf-8")
    comprobar(re.search(r"\| .*\|.*\|", plan) is not None, "plan.md trae la tabla frase y toma")
    comprobar(f"{PROD} --marcar-toma {AD} N" in plan and "--marcar-toma" in informe.get("como_marcar", ""), "plan.md e informe dicen como marcar una toma que no va (un nino, un logo)")
    # --marcar-toma: la toma #N del anuncio queda marcada con el video y los segundos exactos que uso
    import emparejar_tomas as et

    tk = vd["tomas"][2]
    et.marcar_toma(PROD, AD, [3], "mala", "", mostrar=lambda *a: None)
    mk = (leer_json(PUBLIC / "productos" / PROD / "broll-tiktok" / "indice.json")["videos"][Path(tk["src"]).name].get("marcas") or [{}])[-1]
    comprobar(abs(mk.get("desde", -9) - tk["desde"]) < 0.011 and abs(mk.get("hasta", -9) - (tk["desde"] + tk["duracion"])) < 0.011 and mk.get("calidad") == "mala" and mk.get("motivo") == f"toma 3 de {AD}",
              "--marcar-toma #3 marca el video y los segundos exactos de esa toma", f"{mk} {tk['src']} {tk['desde']} {tk['duracion']}")
    comprobar(sale(et.marcar_toma, PROD, AD, [999]) and sale(et.marcar_toma, PROD, "AD77", [1]), "--marcar-toma rechaza una toma que no existe y un anuncio que no esta montado")
    et.quitar_marcas(PROD, Path(tk["src"]).name, mostrar=lambda *a: None)
    # control de calidad con un mp4 de mentira: 1080x1920, voz a -14 LUFS, un cuadro negro a proposito
    mp4 = base / "final" / "falso.mp4"
    ff("-f", "lavfi", "-i", f"color=c=0x303030:s=1080x1920:r=30:d={vd['meta']['duracion']}", "-f", "lavfi", "-i", f"sine=f=220:d={vd['meta']['duracion']}", "-af", "loudnorm=I=-14:TP=-1.5:LRA=7",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(mp4))
    receta = leer_json(base / "receta_aplicada.json")
    elegidas = [{"relajada": 0, "z": 1.0}] * len(vd["tomas"])
    c = ma.control_calidad(mp4, vd, receta, None, elegidas)
    comprobar(c["formato"]["ok"] and c["sin_cuadros_negros"]["ok"] and c["volumen"]["ok"] and c["duracion_en_receta"]["ok"], "el control pasa un mp4 de 1080x1920 a -14 LUFS sin negros", f"{ {k: v['detalle'] for k, v in c.items() if v['ok'] is False} }")
    comprobar(c["subtitulos_visibles"]["ok"] is None and c["zona_inferior_libre"]["ok"] is None, "sin detector de texto, los pixeles quedan `sin verificar` (no se dan por buenos)")
    negro = base / "final" / "negro.mp4"
    ff("-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30:d={vd['meta']['duracion']}", "-f", "lavfi", "-i", f"sine=f=220:d={vd['meta']['duracion']}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(negro))
    c2 = ma.control_calidad(negro, vd, receta, None, elegidas)
    comprobar(c2["sin_cuadros_negros"]["ok"] is False, "el control detecta un video negro")
    comprobar(c["sin_franjas_negras"]["ok"] is True and c2["sin_franjas_negras"]["ok"] is True, "el control no toma por franjas un video liso ni uno entero negro (ese es de sin_cuadros_negros)", f"{c['sin_franjas_negras']} {c2['sin_franjas_negras']}")
    barras = base / "final" / "barras.mp4"
    ff("-f", "lavfi", "-i", f"color=c=0x9a9a9a:s=1080x1920:r=30:d={vd['meta']['duracion']}", "-f", "lavfi", "-i", f"sine=f=220:d={vd['meta']['duracion']}",
       "-vf", "drawbox=x=0:y=0:w=1080:h=230:color=black:t=fill,drawbox=x=0:y=1690:w=1080:h=230:color=black:t=fill", "-af", "loudnorm=I=-14:TP=-1.5:LRA=7",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(barras))
    c4 = ma.control_calidad(barras, vd, receta, None, elegidas)
    comprobar(c4["sin_franjas_negras"]["ok"] is False and "arriba y abajo en los segundos 0.0-" in c4["sin_franjas_negras"]["detalle"] and "--marcar-toma" in c4["sin_franjas_negras"]["detalle"] and c4["sin_cuadros_negros"]["ok"] is True,
              "el control detecta un video con franjas negras arriba y abajo (12 %), dice en que segundos y como marcar la toma", f"{c4['sin_franjas_negras']} {c4['sin_cuadros_negros']}")
    corto = dict(vd, meta=dict(vd["meta"], duracion=10.0))
    comprobar(ma.control_calidad(None, corto, receta, None, elegidas)["duracion_en_receta"]["ok"] is False, "el control detecta una duracion fuera de la receta (10 s para 25 a 60)")
    comprobar(ma.control_calidad(None, corto, receta, None, elegidas)["sin_franjas_negras"]["ok"] is None, "sin render las franjas negras quedan `sin verificar` (no se dan por buenas)")
    rep = dict(vd, tomas=vd["tomas"] + [dict(vd["tomas"][0])])
    comprobar(ma.control_calidad(None, rep, receta, None, elegidas)["tomas_sin_repetir"]["ok"] is False, "el control detecta una toma repetida")
    # detector falso con un frame de mentira que trae texto abajo: la compuerta de la zona inferior lo ve
    from PIL import Image as _I, ImageDraw as _D

    class DetectorAbajo:
        def cajas(self, im):
            w, h = im.size
            return [[(0.1 * w, h - 150.0), (0.9 * w, h - 150.0), (0.9 * w, h - 100.0), (0.1 * w, h - 100.0)]]
    c3 = ma.control_calidad(mp4, vd, receta, DetectorAbajo(), elegidas)
    comprobar(c3["zona_inferior_libre"]["ok"] is False, "con texto en los 280 px de abajo, la compuerta falla")


def prueba_reporte_fabrica(motor, detector) -> None:
    import os

    import montaje_auto as ma

    print("\n[3b] el avance del anuncio le llega a la Fabrica del dash")
    base = ANUNCIOS / PROD / "tiktok" / AD
    guion = PUBLIC / "productos" / PROD / "guiones" / f"{AD}.txt"
    llamadas: list[dict] = []

    def rep(**datos):
        llamadas.append(datos)
        return True
    informe = ma.montar(PROD, AD, sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=lambda *a: None, reportar=rep)
    comprobar(len(llamadas) == 1 and llamadas[0]["etapa"] == "plan", "sin render avisa UNA vez: la etapa «plan»", f"{[x.get('etapa') for x in llamadas]}")
    d = llamadas[0] if llamadas else {}
    cortes = informe.get("emparejamiento", [])
    plan = d.get("plan") or []
    comprobar(len(plan) == len(cortes) and len(plan) >= 5 and all(set(f) == {"t", "frase", "toma", "texto"} for f in plan), "manda una fila por corte (t, frase, toma, texto)", f"{len(plan)} filas, {len(cortes)} cortes")
    comprobar(plan[0]["t"] == "0:00" and all(re.fullmatch(r"\d+:\d{2}", f["t"]) for f in plan) and all(f["frase"] and f["toma"] and "·" in f["toma"] and " s" in f["toma"] for f in plan), "cada fila dice cuando empieza, lo que se dice y la toma con su segundo")
    comprobar(any(f["texto"] for f in plan), "los textos grandes (o la pagina del producto, entre corchetes) van en su fila")
    comprobar(d.get("vista") == base / "evidencia.png" and (base / "evidencia.png").is_file(), "la imagen de vista previa es la evidencia.png del anuncio")
    comprobar(d.get("guion") is None and "espero el OK" in d.get("nota", ""), "sin archivo de guion no manda guion, y con el plan «mostrar» dice que espera el OK")
    guion.parent.mkdir(parents=True, exist_ok=True)
    guion.write_text("Primera linea del guion de prueba.\nSegunda linea.\n", encoding="utf-8")
    llamadas.clear()
    avisos_g: list[str] = []
    inf_g = ma.montar(PROD, AD, sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=avisos_g.append, reportar=rep)
    comprobar(llamadas and llamadas[0].get("guion") == "Primera linea del guion de prueba.\nSegunda linea.\n", "con public/productos/<p>/guiones/AD1.txt manda el guion tal cual")
    cg = inf_g["compuertas"].get("subtitulos_iguales_al_guion")
    comprobar(cg is not None and cg["ok"] is False and "diferencia" in cg["detalle"], "si el guion de texto no dice lo que dice la voz, el control de calidad lo marca y dice cuantas diferencias hay", f"{cg}")
    comprobar(any("subtitulos no dicen lo mismo que el guion" in x and "ANTES de renderizar" in x for x in avisos_g), "y lo avisa en pantalla ANTES de renderizar, con las primeras diferencias")
    guion.write_text(" ".join(FRASES), encoding="utf-8")
    inf_ok = ma.montar(PROD, AD, sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=lambda *a: None, reportar=rep)
    cg = inf_ok["compuertas"].get("subtitulos_iguales_al_guion")
    comprobar(cg is not None and cg["ok"] is True, "si el guion de texto dice lo mismo que la voz, la compuerta pasa", f"{cg}")
    receta_auto = base / "receta_auto.json"
    receta_auto.write_text(json.dumps({"aprobacion": {"plan": "automatico"}}), encoding="utf-8")
    llamadas.clear()
    ma.montar(PROD, AD, receta_arg=str(receta_auto), sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=lambda *a: None, reportar=rep)
    comprobar(llamadas and "espero el OK" not in llamadas[0].get("nota", ""), "si el dueño dejo seguir sin esperar el OK, la nota no dice que espera")
    receta_auto.unlink(missing_ok=True)
    guion.unlink(missing_ok=True)

    # un aviso que se cae no tumba el montaje
    def rep_malo(**datos):
        raise RuntimeError("se cayo el aviso")
    avisos: list[str] = []
    informe2 = ma.montar(PROD, AD, sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=avisos.append, reportar=rep_malo)
    comprobar(informe2["estado"] == "sin-render" and any("no pude avisar el progreso" in a and "se cayo el aviso" in a for a in avisos), "una funcion de aviso que se cae no frena el montaje: lo dice y sigue")

    # reporte_de_fabrica: solo avisa si el anuncio viene de la Fabrica y hay llave
    orden = base / "orden.json"
    orden.unlink(missing_ok=True)
    viejo = {k: os.environ.get(k) for k in ("DASH_URL", "DASH_CLAVE_EDITOR")}
    try:
        os.environ.update({"DASH_URL": "http://127.0.0.1:1", "DASH_CLAVE_EDITOR": "llave-de-prueba-del-editor-0123456789"})
        mensajes: list[str] = []
        r0 = ma.reporte_de_fabrica(PROD, AD, mensajes.append)
        comprobar(r0(etapa="plan") is False and not mensajes, "un anuncio que NO viene de la Fabrica (sin orden.json) no avisa nada ni imprime nada")
        orden.write_text(json.dumps({"proyecto": {"id": "a1", "slug": PROD}}), encoding="utf-8")
        os.environ.update({"DASH_URL": "", "DASH_CLAVE_EDITOR": ""})
        r1 = ma.reporte_de_fabrica(PROD, AD, mensajes.append)
        comprobar(r1(etapa="plan") is False and not mensajes, "si el editor no tiene DASH_URL y la llave en .env, no avisa nada ni imprime nada")
        os.environ.update({"DASH_URL": "http://127.0.0.1:1", "DASH_CLAVE_EDITOR": "llave-de-prueba-del-editor-0123456789"})
        r2 = ma.reporte_de_fabrica(PROD, AD, mensajes.append)
        ok2 = r2(etapa="plan", nota="x")
        comprobar(ok2 is False and any("no recibio el progreso" in x for x in mensajes), "con la Fabrica y la llave pero sin conexion al dash: devuelve False y lo dice, sin lanzar", f"{mensajes}")
        orden.write_text("{no es json", encoding="utf-8")
        comprobar(ma.reporte_de_fabrica(PROD, AD, mensajes.append)(etapa="plan") is False, "un orden.json roto tampoco tumba nada")
    finally:
        orden.unlink(missing_ok=True)
        for k, v in viejo.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def prueba_tope_ajeno(motor, detector) -> None:
    import emparejar_tomas as et
    import montaje_auto as ma

    print("\n[3a] videos de otras personas: tope de segundos por video y lista de fuentes del anuncio")
    ad = "AD3"
    base = ANUNCIOS / PROD / "tiktok" / ad
    (base / "audio").mkdir(parents=True, exist_ok=True)
    voces = PUBLIC / "productos" / PROD / "voces"
    shutil.copy(voces / f"{AD}.mp3", voces / f"{ad}.mp3")                                                       # la misma voz y la misma transcripcion que AD1
    shutil.copy(ANUNCIOS / PROD / "tiktok" / AD / "transcripcion.json", base / "transcripcion.json")
    carpeta = PUBLIC / "productos" / PROD / "broll-tiktok"
    # fuentes.csv como lo deja broll_tiktok.py: de ahi salen el autor y el enlace de cada clip
    filas = ["id,archivo,estado,motivo,url,autor,descripcion"] + [f"{i},{n}.mp4,bajado,,https://www.tiktok.com/@creador_{n}/video/{i},creador_{n},clip de prueba" for i, n in enumerate(COLORES, 1)]
    (carpeta / "fuentes.csv").write_text("\n".join(filas) + "\n", encoding="utf-8")
    antes_pref = et.PREFIJOS_AJENOS
    et.PREFIJOS_AJENOS = ("",)                                  # los 4 videos de prueba hacen de videos de otras personas
    try:
        # (1) 4 videos x 5 s = 20 s para un anuncio de ~40 s: no alcanza, y el montaje lo dice en vez de pasarse del tope
        receta5 = base / "receta5.json"
        escribir_json(receta5, {"ritmo": {"maxSegPorVideoAjeno": 5}})
        avisos: list[str] = []
        cayo = sale(ma.montar, PROD, ad, receta_arg=str(receta5), sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=avisos.append)
        inf = leer_json(base / "informe_calidad.json")
        det = inf.get("compuertas", {}).get("tomas_con_material", {}).get("detalle", "")
        comprobar(cayo and inf.get("estado") == "sin-material" and "videos distintos" in det and "4 de TikTok" in det,
                  "con 5 s por video y solo 4 videos el montaje dice que no alcanza (cuantos videos haria falta) y no se pasa del tope", f"{inf.get('estado')} {det}")
        comprobar(any("maximo 5 s de cada uno" in a for a in avisos), "el montaje cuenta cuantos videos de otras personas hay y el tope que aplica")
        # (2) con 12 s por video si alcanza: ninguno pasa del tope, y el anuncio deja la lista de donde salio cada clip
        receta12 = base / "receta12.json"
        escribir_json(receta12, {"ritmo": {"maxSegPorVideoAjeno": 12}})
        informe = ma.montar(PROD, ad, receta_arg=str(receta12), sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=lambda *a: None)
        comprobar(informe["estado"] == "sin-render" and not informe.get("errores_validador"), "con 12 s por video el montaje termina y el validador no encuentra errores", f"{informe.get('estado')} {informe.get('errores_validador')}")
        vd = leer_json(base / "videoData.json")
        uso = ma.segundos_por_video_ajeno(vd["tomas"])
        comprobar(uso and all(u["segundos"] <= 12.0 + 0.06 for u in uso.values()), "ningun video de otra persona pasa de 12 s en el videoData que se renderiza", f"{ {k: round(u['segundos'], 1) for k, u in uso.items()} }")
        comprobar(informe["compuertas"]["tope_por_video_ajeno"]["ok"] is True, "el control de calidad da por buena la regla del tope", f"{informe['compuertas'].get('tope_por_video_ajeno')}")
        fuentes = leer_json(base / "fuentes_ajenas.json")
        v0 = fuentes["videos"][0] if fuentes.get("videos") else {}
        comprobar(fuentes.get("tope_seg_por_video") == 12.0 and len(fuentes["videos"]) == len(uso) and v0.get("autor", "").startswith("creador_") and v0.get("enlace", "").startswith("https://www.tiktok.com/@creador_") and v0.get("tramos"),
                  "fuentes_ajenas.json lista cada video de otra persona con su autor, su enlace, los segundos y los tramos usados", f"{fuentes}")
        plan = (base / "plan.md").read_text(encoding="utf-8")
        comprobar("Videos de otras personas que usa este anuncio" in plan and "creador_rojo" in plan and informe.get("fuentes_ajenas") == fuentes["videos"],
                  "plan.md trae la tabla de fuentes (para retirar un clip rapido si alguien reclama) y el informe la repite")
        # (3) un tope menor que el corte mas largo se sube al corte (si no, ningun corte cabria) y lo avisa
        receta2 = base / "receta2.json"
        escribir_json(receta2, {"ritmo": {"maxSegPorVideoAjeno": 2}})
        msgs: list[str] = []
        sale(ma.montar, PROD, ad, receta_arg=str(receta2), sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=msgs.append)
        comprobar(any("se sube a 3 s" in m for m in msgs), "un tope de 2 s con cortes de hasta 3 s se sube a 3 s y se avisa", f"{[m for m in msgs if 'tope' in m]}")
    finally:
        et.PREFIJOS_AJENOS = antes_pref
    # el control de calidad por si solo: mide el videoData (lo que se renderiza), no la intencion
    T = "productos/x/broll-tiktok/"
    vd_x = {"meta": {"duracion": 30.0}, "palabras": [{"texto": "a"}],
            "tomas": [{"src": T + "tt_1.mp4", "desde": 0.0, "inicio": 0.0, "duracion": 3.0}, {"src": T + "tt_1.mp4", "desde": 5.0, "inicio": 3.0, "duracion": 3.0},
                      {"src": T + "tt_2.mp4", "desde": 1.0, "inicio": 6.0, "duracion": 2.0}, {"src": T + "propio.mp4", "desde": 0.0, "inicio": 8.0, "duracion": 3.0},
                      {"src": T + "propio.mp4", "desde": 4.0, "inicio": 11.0, "duracion": 3.0}]}
    el = [{"relajada": 0, "z": 1.0}] * 5
    c5 = ma.control_calidad(None, vd_x, ma.combinar(ma.RECETA_DEFECTO, {}), None, el)
    comprobar(c5["tope_por_video_ajeno"]["ok"] is False and "tt_1.mp4" in c5["tope_por_video_ajeno"]["detalle"] and "tt_2.mp4" not in c5["tope_por_video_ajeno"]["detalle"] and "propio" not in c5["tope_por_video_ajeno"]["detalle"],
              "el control detecta un video de otra persona usado 6 s con tope de 5 (y no acusa a tt_2, que usa 2 s, ni al video propio)", f"{c5['tope_por_video_ajeno']}")
    c8 = ma.control_calidad(None, vd_x, ma.combinar(ma.RECETA_DEFECTO, {"ritmo": {"maxSegPorVideoAjeno": 8}}), None, el)
    comprobar(c8["tope_por_video_ajeno"]["ok"] is True, "con tope de 8 s el mismo anuncio pasa")
    comprobar("tope_por_video_ajeno" not in ma.control_calidad(None, vd_x, ma.combinar(ma.RECETA_DEFECTO, {"ritmo": {"maxSegPorVideoAjeno": 0}}), None, el), "con tope 0 no se mide (sin regla)")
    lento = {"meta": {"duracion": 30.0}, "palabras": [{"texto": "a"}], "tomas": [{"src": T + "tt_1.mp4", "desde": 0.0, "inicio": 0.0, "duracion": 3.0, "velocidad": 2.0}, {"src": T + "tt_1.mp4", "desde": 8.0, "inicio": 3.0, "duracion": 1.0}]}
    comprobar(ma.segundos_por_video_ajeno(lento["tomas"])["tt_1.mp4"]["segundos"] == 7.0, "los segundos usados cuentan la velocidad (3 s al doble de velocidad gastan 6 s del video de origen)")


def prueba_gancho(motor, detector) -> None:
    import montaje_auto as ma

    print("\n[3b] gancho nuevo con IA (gancho/gancho.json de gancho_ia.py)")
    ad = "AD2"
    base = ANUNCIOS / PROD / "tiktok" / ad
    (base / "audio").mkdir(parents=True, exist_ok=True)
    voces = PUBLIC / "productos" / PROD / "voces"
    shutil.copy(voces / f"{AD}.mp3", voces / f"{ad}.mp3")                                                       # la misma voz y la misma transcripcion que AD1
    shutil.copy(ANUNCIOS / PROD / "tiktok" / AD / "transcripcion.json", base / "transcripcion.json")           # (la transcripcion despues de la voz: si no, parece vieja y se llama a Whisper)
    carpeta = base / "gancho"
    carpeta.mkdir(parents=True, exist_ok=True)
    for n, largo in ((1, 4.0), (2, 1.8)):          # imagenes quietas convertidas a video (como las deja gancho_ia.py); la segunda dura menos que su corte
        ff("-f", "lavfi", "-i", f"color=c={color_hex((20, 60, 200))}:s=270x480:r=10:d={largo}", "-vf", TEXTURA, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(carpeta / f"toma_0{n}.mp4"))
    tomas = [{"orden": n, "tipo": "imagen", "src": f"anuncios/{PROD}/tiktok/{ad}/gancho/toma_0{n}.mp4", "desde": 0.0, "ajuste": "cover", "silenciada": True,
              "duracion_archivo": largo, "duracion_sugerida": 3.0, "zoom": 1.06, "que_muestra": "una imagen de prueba"} for n, largo in ((1, 4.0), (2, 1.8))]
    escribir_json(carpeta / "gancho.json", {"version": 1, "producto": PROD, "anuncio": ad, "tipo": "tiktok", "modo": "nuevo-modelado", "estado": "completo", "segundos": 6,
                                            "frase": "¿Quieres ver algo nuevo?", "texto_grande": "¡MIRA ESTO!", "tomas": tomas, "avisos": []})
    receta = ma.combinar(ma.RECETA_DEFECTO, {})
    g = ma.leer_gancho(base, receta)
    comprobar(g is not None and len(g["tomas"]) == 2 and g["texto_grande"] == "¡MIRA ESTO!", "se lee el gancho con IA (2 tomas y su texto grande)")
    comprobar(ma.leer_gancho(base, ma.combinar(ma.RECETA_DEFECTO, {"gancho": {"modo": "sin-gancho"}})) is None, "con gancho.modo = sin-gancho no se usa")
    comprobar(ma.leer_gancho(base, ma.combinar(ma.RECETA_DEFECTO, {"gancho": {"modo": "conservar"}})) is None, "con gancho.modo = conservar tampoco (el material es solo el nuestro)")
    informe = ma.montar(PROD, ad, sin_render=True, sin_historial=True, motor=motor, detector=detector, mostrar=lambda *a: None)
    comprobar(informe["estado"] == "sin-render" and not informe.get("errores_validador"), "el montaje con gancho IA termina y el validador no encuentra errores", f"{informe.get('estado')} {informe.get('errores_validador')}")
    vd = leer_json(base / "videoData.json")
    t = sorted(vd["tomas"], key=lambda x: x["inicio"])
    comprobar(informe["entradas"]["gancho_ia"] == 2 and "/gancho/toma_01" in t[0]["src"] and "/gancho/toma_02" in t[1]["src"] and "/gancho/" not in t[2]["src"],
              "las dos primeras tomas son las del gancho y el resto sale del b-roll", f"{[x['src'].split('/')[-1] for x in t[:3]]}")
    comprobar(abs(t[0]["zoom"] - 1.06) < 1e-6 and t[0]["desde"] == 0.0 and t[0]["ajuste"] == "cover", "la toma del gancho conserva su zoom y su ajuste")
    if t[1]["duracion"] > 1.85:
        comprobar(0.74 <= t[1].get("velocidad", 1.0) < 1.0, "si el archivo dura menos que el corte, se le baja la velocidad para que alcance", f"{t[1]}")
    comprobar(vd["textosGrandes"][0]["texto"] == "¡MIRA ESTO!" and vd["textosGrandes"][0]["inicio"] < 1.0, "el texto grande del gancho manda sobre el que sacarian las reglas")
    comprobar(not ma.tomas_repetidas(vd["tomas"]), "con el gancho IA tampoco hay tomas repetidas")
    # un clip de IA (de varios segundos) cubre los cortes seguidos que haga falta hasta llenar su `duracion_sugerida` (con el tramo que sigue del mismo clip); una imagen cubre uno solo
    cortes = [{"t0": 0.0, "t1": 2.7}, {"t0": 2.7, "t1": 5.0}, {"t0": 5.0, "t1": 7.4}, {"t0": 7.4, "t1": 9.3}, {"t0": 9.3, "t1": 11.0}]
    clip = {"orden": 1, "tipo": "clip", "src": "anuncios/x/tiktok/AD9/gancho/01_clip.mp4", "desde": 0.0, "duracion_sugerida": 6.0, "_dur_archivo": 8.0}
    imagen = {"orden": 2, "tipo": "imagen", "src": "anuncios/x/tiktok/AD9/gancho/toma_02.mp4", "desde": 0.0, "duracion_sugerida": 3.0, "zoom": 1.06, "_dur_archivo": 4.0}
    r = ma.tomas_del_gancho({"tomas": [clip, imagen]}, cortes)
    comprobar([e["video"] for e in r] == ["01_clip.mp4"] * 3 + ["toma_02.mp4"] and [e["desde"] for e in r] == [0.0, 2.7, 5.0, 0.0],
              "un clip de 8 s cubre los 3 primeros cortes con tramos seguidos del mismo clip y la imagen que sigue cubre uno", f"{[(e['video'], e['desde'], e['dur']) for e in r]}")
    comprobar(not ma.tomas_repetidas([{"src": e["ruta"], "desde": e["desde"], "duracion": e["dur"], "velocidad": e["velocidad"]} for e in r]), "los tramos seguidos de un mismo clip no se solapan (no cuentan como repetidos)")
    comprobar([bool(e.get("continuacion")) for e in r] == [False, True, True, False], "los tramos seguidos del mismo clip van marcados `continuacion` (el primero y la imagen que sigue, no)")
    import aplicar_receta as ar

    tomas_ar = [{"inicio": 0.0, "duracion": 2.7}, {"inicio": 2.7, "duracion": 2.3, "continuacion": True}, {"inicio": 5.0, "duracion": 2.4, "continuacion": True},
                {"inicio": 7.4, "duracion": 1.9}, {"inicio": 9.3, "duracion": 1.7}]
    for frecuencia in ("cada-corte", "pocas", "cambio-de-idea"):
        cortes_ar = ar.elegir_cortes({"tomas": tomas_ar, "palabras": [], "textosGrandes": []}, {"transiciones": {"activas": True, "frecuencia": frecuencia}})
        comprobar(not any(i in (1, 2) for i in cortes_ar) and (frecuencia != "cada-corte" or cortes_ar == [3, 4]),
                  f"la receta no pone transicion a los tramos seguidos de un mismo clip (frecuencia {frecuencia})", f"{cortes_ar}")
    r = ma.tomas_del_gancho({"tomas": [imagen, dict(imagen, orden=2, src="anuncios/x/tiktok/AD9/gancho/toma_03.mp4")]}, cortes)
    comprobar([e["video"] for e in r] == ["toma_02.mp4", "toma_03.mp4"], "cada imagen quieta cubre un solo corte")
    comprobar(len(ma.tomas_del_gancho({"tomas": [dict(clip, duracion_sugerida=0.0)]}, cortes)) == 1, "un clip sin `duracion_sugerida` cubre un solo corte (como antes)")
    corto = ma.tomas_del_gancho({"tomas": [dict(clip, _dur_archivo=2.5)]}, cortes)
    comprobar(len(corto) == 1 and 0.74 <= corto[0]["velocidad"] < 1.0, "un clip mas corto que el primer corte se frena un poco y se usa una vez", f"{corto}")
    comprobar(ma.tomas_del_gancho({"tomas": [dict(clip, _dur_archivo=1.2)]}, cortes) == [], "un clip que dura mucho menos que el corte se salta (lo cubre el b-roll)")


def prueba_sin_red_ni_llm() -> None:
    print("\n[4] la corrida no abre la red ni llama a un modelo de lenguaje")
    prohibidos = re.compile(r"^\s*(?:import|from)\s+(requests|urllib|http|socket|httpx|aiohttp|openai|anthropic|google\.generativeai|ftplib|smtplib)\b", re.M)
    for nombre in ("emparejar_tomas.py", "montaje_auto.py"):
        fuente = (RAIZ / "scripts" / nombre).read_text(encoding="utf-8")
        hallados = prohibidos.findall(fuente)
        comprobar(not hallados, f"{nombre} no importa nada de red ni de modelos de lenguaje", f"{hallados}")
    clip = (RAIZ / "scripts" / "clip_local.py").read_text(encoding="utf-8")
    cuerpo = clip.split("def descargar", 1)[1].split("\ndef ", 1)[0]
    comprobar("import requests" in cuerpo and clip.count("import requests") == 1, "clip_local.py solo abre la red dentro de descargar() (una vez, para bajar los modelos)")


def prueba_clip_real() -> None:
    import clip_local

    print("\n[5] CLIP real (si los modelos estan instalados)")
    if not clip_local.modelos_listos():
        print("  (los modelos de CLIP no estan instalados: se salta. Instalalos con `python scripts/emparejar_tomas.py --solo-descargar`)")
        return
    from PIL import Image

    motor = clip_local.MotorClip()
    ims = [Image.new("RGB", (320, 320), c) for c in ((220, 30, 30), (30, 160, 50), (40, 70, 220))]
    v = motor.imagenes(ims)
    comprobar(v.shape == (3, 512) and abs(float((v[0] ** 2).sum()) - 1) < 1e-3, "las imagenes dan vectores de 512 numeros de largo 1")
    for textos, nombre in ((["una imagen roja", "una imagen verde", "una imagen azul"], "en espanol"), (["a red image", "a green image", "a blue image"], "en ingles"),
                           (["uma imagem vermelha", "uma imagem verde", "uma imagem azul"], "en portugues")):
        t = motor.textos(textos)
        aciertos = sum(1 for i in range(3) if int((v @ t[i]).argmax()) == i)
        comprobar(aciertos >= 2, f"CLIP real une cada color con su frase {nombre}", f"{aciertos} de 3")


def main() -> None:
    configurar_consola()
    sin_clip_real = "--sin-clip-real" in sys.argv
    for carpeta in (PUBLIC / "productos" / PROD, ANUNCIOS / PROD):
        if carpeta.exists():
            print(f"Ya existe {carpeta}: borrala (es de una prueba anterior).")
            sys.exit(1)
    t0 = time.time()
    motor, detector = MotorFalso(), DetectorFalso()
    try:
        crear_material()
        prueba_frases_y_cortes()
        prueba_pista_del_guion()
        prueba_analisis_y_emparejamiento(motor, detector)
        prueba_franjas_negras()
        prueba_montaje(motor, detector)
        prueba_reporte_fabrica(motor, detector)
        prueba_tope_ajeno(motor, detector)
        prueba_gancho(motor, detector)
        prueba_sin_red_ni_llm()
        if not sin_clip_real:
            prueba_clip_real()
    finally:
        limpiar()
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON FALLAS ({len(FALLAS)}): {', '.join(FALLAS)}  [{time.time() - t0:.0f} s]")
        sys.exit(1)
    print(f"AUTOPRUEBA OK  [{time.time() - t0:.0f} s]")


if __name__ == "__main__":
    main()
