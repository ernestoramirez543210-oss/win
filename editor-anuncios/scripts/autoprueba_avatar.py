"""Autoprueba del anuncio de AVATAR con IA (avatar_comun.py y avatar_auto.py) SIN internet, sin APIMart y sin Whisper: todo lo que cuesta o tarda se reemplaza por un doble.

Lo que SI corre de verdad: las reglas, el tope de gasto, los reintentos, el plan, unir_clips.py, mezclar_audio.py, aplicar_receta.py, validar_videodata.py, las compuertas de calidad y ffmpeg
(con clips y un render de mentira de unos segundos). Lo que NO se prueba aqui (y se dice): que APIMart haga un buen video de una persona, que Whisper oiga bien, que Remotion renderice.

  python scripts/autoprueba_avatar.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apimart as A  # noqa: E402
import apimart_chat as C  # noqa: E402
import avatar_auto as AV  # noqa: E402
import avatar_comun as AC  # noqa: E402
import gancho_ia as G  # noqa: E402
from _comun import ANUNCIOS, PUBLIC, RAIZ, ejecutable  # noqa: E402

PRODUCTO = f"_autoprueba_av_{os.getpid()}"
AD = "AD4"
TOTAL = 0
FALLAS: list[str] = []


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    global TOTAL
    TOTAL += 1
    if cond:
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"   → {detalle}" if detalle else ""))


def seccion(titulo: str) -> None:
    print(f"\n{titulo}")


# ------------------------------------------------------------------ materiales de mentira
FRASES = ["¿Quieres aprender a hacer moldes sin gastar de más? Hoy te cuento cómo empezar.",
          "Muchas personas se rinden porque no saben por dónde empezar con sus proyectos.",
          "Con este ebook sigues cada paso con fotos claras y materiales sencillos de conseguir.",
          "Trae plantillas listas para imprimir y una guía para cada proyecto del libro.",
          "Pídelo hoy y empieza tu primer proyecto este mismo fin de semana en casa.",
          "Escríbenos por WhatsApp y te enviamos el ebook completo ahora mismo."]
ROLES = ["gancho", "problema", "giro", "prueba", "oferta", "llamada"]
LUGARES = ["kitchen counter next to the window", "living room sofa with a blue blanket", "balcony table surrounded by green plants", "bedroom desk with a small lamp"]


def ff(*args: str) -> None:
    r = subprocess.run([ejecutable("ffmpeg"), "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr[-300:])


def crear_png(ruta: Path, ancho: int = 360, alto: int = 640) -> None:
    from PIL import Image

    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", (ancho, alto), os.urandom(ancho * alto * 3)).save(ruta)


def crear_jpg(ruta: Path) -> None:
    from PIL import Image

    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", (400, 500), os.urandom(400 * 500 * 3)).save(ruta, quality=70)


def crear_mp4(ruta: Path, segundos: float = 2.0) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", f"testsrc2=size=270x480:rate=30:duration={segundos}", "-f", "lavfi", "-i", f"sine=frequency=330:duration={segundos}", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
       "-shortest", str(ruta))


def crear_final(ruta: Path, segundos: float) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", f"color=c=0x6080a0:s=1080x1920:r=30:d={segundos}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={segundos}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", str(ruta))


def ficha_avatar(**cambios) -> dict:
    j = {"nombre": "lucia", "genero": "mujer", "fotos": ["foto1.jpg"], "aspecto": "woman in her mid-30s with long dark hair and warm brown eyes",
         "voz": "a female voice in her mid-30s, medium pitch, calm and confident, warm", "acento": "Spanish with a clear, neutral Colombian accent",
         "casa_base": "kitchen with a wooden counter, a stove on the left and a window with natural light", "autorizado": True}
    j.update(cambios)
    return j


class Doble:
    """Los dobles de APIMart (imagenes, videos, texto y vision), de Whisper y del render. Cada llamada queda anotada."""

    def __init__(self, costos: Path):
        self.costos = costos
        self.llamadas: list[list[str]] = []
        self.textos: list[dict] = []
        self.falla: dict[str, int] = {}
        self.informar_costo = True
        self.veredictos: list = []
        self.oido: dict[str, list[str]] = {}
        self.escenas_malas = 0
        self.vision_cae = False
        self.factor_costo = 1.0
        self.n = 0

    def anotar(self, fila: dict) -> None:
        with self.costos.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")

    def apimart(self, argumentos: list[str], tiempo: int):
        self.llamadas.append(list(argumentos))
        cmd = argumentos[0]
        salida = Path(argumentos[argumentos.index("--salida") + 1])
        clave = f"{cmd}:{salida.stem}"
        if self.falla.get(clave, 0) > 0:
            self.falla[clave] -= 1
            return False, "ERROR: APIMart dijo que no", None, ""
        (crear_png if cmd == "imagen" else crear_mp4)(salida)
        costo = round((AC.COSTO_IMAGEN if cmd == "imagen" else AC.COSTO_CLIP) * self.factor_costo, 6) if self.informar_costo else None
        self.n += 1
        self.anotar({"tipo": "imagen" if cmd == "imagen" else "video", "costo": costo, "producto": PRODUCTO, "anuncio": AD, "etiqueta": argumentos[argumentos.index("--etiqueta") + 1]})
        return True, "", costo, f"tarea{self.n}"

    def completar(self, sistema, usuario, *, imagenes=None, modelos=None, max_tokens=1800, temperatura=0.4, json_modo=True, etiqueta="", producto=None, anuncio=None, anotar=True):
        self.textos.append({"etiqueta": etiqueta, "imagenes": [str(p) for p in (imagenes or [])], "usuario": usuario, "sistema": sistema})
        if etiqueta == "avatar-escenas":
            lineas = json.loads(usuario.split("\n\nYour previous")[0])["lineas"]
            if self.escenas_malas > 0:
                self.escenas_malas -= 1
                datos = {"ropa": "x", "clips": []}
            else:
                datos = {"ropa": "a plain sage green cotton blouse and light blue jeans",
                         "clips": [{"escena": f"Three-quarter angle, medium shot, waist up, at the {LUGARES[i % len(LUGARES)]}, she is arranging small paper templates and a pair of scissors while talking, a warm smile toward the camera, "
                                              f"clip number {i}", "accion": "She walks into the frame holding a stack of printed templates", "usa_producto": i == 1} for i in range(len(lineas))]}
            costo = 0.003
        else:
            if self.vision_cae:
                raise C.ErrorChat("ningun modelo respondio")
            datos = self.veredictos.pop(0) if self.veredictos else {"misma_persona": True, "ropa_igual_que_el_ancla": True, "texto_o_logos_visibles": False, "una_sola_persona": True,
                                                                    "manos_o_cara_deformes": False, "producto_deformado": None, "motivo": ""}
            costo = 0.0006
        self.anotar({"tipo": "texto", "costo": None, "costo_estimado": costo, "producto": producto, "anuncio": anuncio, "etiqueta": etiqueta})
        return C.Respuesta(texto=json.dumps(datos), datos=datos, modelo="modelo-de-mentira", tokens_entrada=900, tokens_salida=300, costo_estimado=costo)

    def transcribir(self, ruta: Path, trabajo: Path):
        if ruta.name in self.oido:
            return self.oido[ruta.name]
        n = int(ruta.stem[:2])
        return AC.partir_en_clips(FRASES)[n - 1].split()


def armar_mundo(tmp: Path, receta_extra: dict | None = None, avatar: bool = True, con_guion: bool = True) -> dict:
    """El producto, el pedido y (si se pide) el guion y el avatar; todo en las carpetas de verdad pero con un nombre que no estorba. En Windows, justo despues de borrar, una carpeta puede seguir
    «pendiente de borrar» un instante (antivirus, indexador) y el mkdir revienta con FileExistsError: se repite todo el armado unos segundos."""
    for _ in range(40):
        try:
            return _armar_mundo(tmp, receta_extra, avatar, con_guion)
        except (FileExistsError, PermissionError):
            time.sleep(0.5)
    return _armar_mundo(tmp, receta_extra, avatar, con_guion)


def _armar_mundo(tmp: Path, receta_extra: dict | None, avatar: bool, con_guion: bool) -> dict:
    d_prod = PUBLIC / "productos" / PRODUCTO
    base = ANUNCIOS / PRODUCTO / "ia" / AD
    for d in (d_prod, ANUNCIOS / PRODUCTO):
        for _ in range(20):
            shutil.rmtree(d, ignore_errors=True)
            if not d.exists():
                break
            time.sleep(0.5)
    A.COSTOS.unlink(missing_ok=True)
    crear_jpg(d_prod / "portada.jpg")
    crear_jpg(d_prod / "paginas" / "p001.jpg")
    (d_prod / "producto.json").write_text(json.dumps({"nombre": "Ebook Moldes Faciles", "que_es": "Un ebook de moldes para manualidades", "para_quien": "Mujeres que quieren emprender", "promesa_principal":
                                                      "Aprende a hacer moldes paso a paso", "descripcion_ia": "A white A5 ebook with a blue cover lying on a wooden table", "paginas_foto_producto": ["p001.jpg"]}), encoding="utf-8")
    (d_prod / "referencia").mkdir(parents=True)
    (d_prod / "referencia" / f"{AD}_referencia.json").write_text(json.dumps({"anunciante": "Marca Ajena Brasil"}), encoding="utf-8")
    if avatar:
        crear_jpg(d_prod / "avatares" / "lucia" / "foto1.jpg")
        (d_prod / "avatares" / "lucia" / "avatar.json").write_text(json.dumps(ficha_avatar()), encoding="utf-8")
    base.mkdir(parents=True)
    receta = {"tipoAnuncio": "ia", "duracionMin": 4, "duracionMax": 10, "idioma": {"variante": "colombiano"}, "aprobacion": {"plan": "automatico", "topeUsd": 0.30},
              "textosGrandes": {"min": 5, "max": 7}, "cta": {"texto": "Pídelo ahora", "subtexto": "", "segundos": 1.5}, "subtitulos": {"activos": True}}
    receta.update(receta_extra or {})
    (base / "receta.json").write_text(json.dumps(receta), encoding="utf-8")
    (base / "orden.json").write_text(json.dumps({"proyecto": {"id": "mpruebaprueba01", "nombre": "Moldes", "slug": PRODUCTO}, "anuncio": {"id": AD, "tipo": "ia"}, "oferta": {"promesa": "Moldes paso a paso"}}), encoding="utf-8")
    if con_guion:
        (base / "guion.json").write_text(json.dumps({"idioma": "es", "frases": [{"texto": t, "rol": r, "visual": {"en": "a woman talking to the camera", "etiquetas": ["avatar", "moldes"], "producto": False}, "textoGrande":
                                                                           ("MOLDES FÁCILES" if r in ("gancho", "oferta") else "")} for t, r in zip(FRASES, ROLES)], "cta": {"texto": "Pídelo ahora", "subtexto": ""}}, ensure_ascii=False), encoding="utf-8")
        (base / "guion.txt").write_text(" ".join(FRASES) + "\n", encoding="utf-8")
    return {"d_prod": d_prod, "base": base}


def args(**kw) -> argparse.Namespace:
    d = dict(producto=PRODUCTO, anuncio=AD, tope_usd=0.30, avatar=None, modelo=None, dry_run=False, rehacer=False, avatar_desde=None, sin_revision_visual=False, sin_render=False, concurrencia=1,
             musica=None, sin_historial=True, cta=None, subcta=None)
    d.update(kw)
    return argparse.Namespace(**d)


def ejecutar(fn, *a):
    """(codigo, mensaje de error): corre una etapa como lo hace main()."""
    try:
        return fn(*a), ""
    except AC.ErrorAvatar as e:
        return e.codigo, e.mensaje


# ------------------------------------------------------------------ 1. las reglas puras
def puras() -> None:
    seccion("avatar_comun: costo, largo y clips")
    comprobar(AC.COSTO_IMAGEN == G.COSTO_IMAGEN and AC.COSTO_CLIP == G.COSTO_CLIP, "los precios son los mismos que usa gancho_ia.py (los medidos con el saldo de APIMart)")
    comprobar(abs(AC.costo_estimado(3) - 0.2903) < 1e-9 and abs(AC.costo_estimado(10) - 0.921) < 1e-9, "3 clips ≈ US$ 0,29 y 10 clips ≈ US$ 0,92 (el «≈ US$ 1» del dash)")
    comprobar(abs(AC.costo_estimado(3, reintentos_clip=1) - (0.2903 + 0.0816)) < 1e-9, "repetir un clip suma US$ 0,08")
    c = AC.clips_que_caben(25, 60, 0.30)
    comprobar(not c["cabe"] and c["n"] == 0 and c["n_min"] == 4 and abs(c["costo_minimo"] - 0.3804) < 1e-9 and c["n_dinero"] == 3, "la receta de fabrica (25 a 60 s) NO cabe en US$ 0,30: el minimo son 4 clips ≈ US$ 0,38", str(c))
    c = AC.clips_que_caben(20, 25, 0.30)
    comprobar(c["cabe"] and c["n"] == 3 and c["duracion"] == 22.2, "una receta de 20 a 25 s SI cabe en US$ 0,30: 3 clips de ≈ 22 s", str(c))
    c = AC.clips_que_caben(20.2, 25, 0.30)
    comprobar(c["n_min"] == 3 and c["cabe"], "el redondeo no suma un clip ((20,2 + 2) / 7,4 = 3,0000000000000004)", str(c))
    c = AC.clips_que_caben(22, 40, 0.30)
    mensaje = AC.mensaje_de_costo(c, 0.30)
    comprobar(c["n_min"] == 4 and not c["cabe"] and c["ventana_ok"] and AC.duracion_minima_segura(3) == 20 and "minima de 20 s o menos" in mensaje,
              "un minimo de 22 s ya pide 4 clips (3 clips pueden dar 19 a 22 s y la compuerta de duracion tolera 0,5 s: en el ensayo real dieron 19,4) y el consejo es bajar el minimo a 20 s", mensaje)
    for dmin, dmax in ((15, 20), (22, 25)):
        c = AC.clips_que_caben(dmin, dmax, 1.00)
        mensaje = AC.mensaje_de_costo(c, 1.00)
        comprobar(not c["ventana_ok"] and not c["cabe"] and c["n"] == 0 and "Ensancha la receta" in mensaje and f"{dmin} a {dmax} s" in mensaje,
                  f"una receta tan angosta como {dmin} a {dmax} s no la cumple ningun numero de clips (se pasaria o no llegaria): no cabe aunque sobre plata, y el mensaje pide ensancharla", mensaje)
    c = AC.clips_que_caben(20, 25, 1.00)
    comprobar(c["ventana_ok"] and c["cabe"] and c["n"] == 3, "20 a 25 s si la cumplen 3 clips", str(c))
    c = AC.clips_que_caben(25, 60, 1.00)
    comprobar(c["cabe"] and c["n"] == 6 and c["n_dinero"] == 10, "con US$ 1,00 la receta de fabrica cabe: el objetivo son 6 clips (≈ 44 s)", str(c))
    c = AC.clips_que_caben(30, 40, 0.5)
    comprobar(c["cabe"] and c["n"] == 5 and abs(c["costo"] - 0.4705) < 1e-9, "30 a 40 s con US$ 0,50: 5 clips")
    c = AC.clips_que_caben(25, 60, None)
    comprobar(c["n"] == c["n_objetivo"], "sin tope el numero de clips es el objetivo de la receta")
    c = AC.clips_que_caben(15, 25, 0.15)
    mensaje = AC.mensaje_de_costo(c, 0.15)
    comprobar(not c["cabe"] and "Ni dos clips caben" in mensaje and "US$ 0.29" in mensaje, "si ni dos clips caben, el mensaje lo dice con las cifras", mensaje)
    mensaje = AC.mensaje_de_costo(AC.clips_que_caben(25, 60, 0.30), 0.30, "el tope por anuncio")
    comprobar("4 clips" in mensaje and "US$ 0.38" in mensaje and "US$ 0.30" in mensaje and "3 clips" in mensaje and "sube el tope" in mensaje, "el mensaje de costo trae los clips, el costo, el tope y que hacer", mensaje)

    seccion("avatar_comun: la ficha del avatar")
    comprobar(AC.problemas_de_ficha(ficha_avatar(), ["foto1.jpg"]) == [], "una ficha completa y autorizada no tiene problemas")
    comprobar(any("autorizado" in p for p in AC.problemas_de_ficha(ficha_avatar(autorizado=False), ["foto1.jpg"])), "sin «autorizado»: true no se usa (solo fotos de quien autorizo su cara y su voz)")
    comprobar(any("autorizado" in p for p in AC.problemas_de_ficha(ficha_avatar(autorizado="si"), ["foto1.jpg"])), "«autorizado» tiene que ser true de verdad, no un texto")
    comprobar(any("fotos" in p for p in AC.problemas_de_ficha(ficha_avatar(fotos=[]), [])), "sin fotos no sirve")
    comprobar(any("no estan en la carpeta" in p for p in AC.problemas_de_ficha(ficha_avatar(), ["otra.jpg"])), "una foto que la ficha nombra y no esta en la carpeta se dice")
    comprobar(not any("fotos" in p for p in AC.problemas_de_ficha(ficha_avatar(fotos=["../../etc/foto1.jpg"]), ["foto1.jpg"])), "de una ruta con carpetas solo cuenta el nombre del archivo")
    for campo in AC.CAMPOS_EN_INGLES:
        comprobar(any(campo in p for p in AC.problemas_de_ficha(ficha_avatar(**{campo: ""}), ["foto1.jpg"])), f"sin «{campo}» no sirve")
    comprobar(any("aspecto" in p for p in AC.problemas_de_ficha(ficha_avatar(aspecto="apparent age, hair, facial features (in English)"), ["foto1.jpg"])), "el texto de ejemplo de la plantilla en «aspecto» no cuenta")
    comprobar(any("casa_base" in p for p in AC.problemas_de_ficha(ficha_avatar(casa_base="el lugar donde graba, con objetos concretos"), ["foto1.jpg"])), "el texto de ejemplo de la plantilla en «casa_base» no cuenta")
    comprobar(any("genero" in p for p in AC.problemas_de_ficha(ficha_avatar(genero="otro"), ["foto1.jpg"])), "el genero es mujer u hombre")
    comprobar(AC.problemas_de_ficha("no es un diccionario") != [] and AC.problemas_de_ficha(None) != [], "una ficha que no es un objeto JSON no sirve")
    comprobar(AC.pronombres("mujer") == ("woman", "she", "her") and AC.pronombres("hombre") == ("man", "he", "his"), "los pronombres cambian con el genero del avatar")

    seccion("avatar_comun: partir el guion en clips")
    clips = AC.partir_en_clips(FRASES)
    comprobar(len(clips) == 3 and all(AC.PALABRAS_MIN_POR_CLIP <= AC.palabras_de(c) <= AC.PALABRAS_POR_CLIP for c in clips), "las 6 frases del guion de prueba hacen exactamente 3 clips de 25 a 30 palabras", str([AC.palabras_de(c) for c in clips]))
    comprobar(" ".join(clips) == " ".join(FRASES), "no se pierde ni se cambia una sola palabra")
    comprobar(all(c.rstrip().endswith((".", "?", "!")) for c in clips), "ningun clip corta una frase a la mitad (todos terminan en fin de frase)")
    try:
        AC.partir_en_clips(["palabra " * 30 + "fin."])
        largo = False
    except AC.ErrorAvatar as e:
        largo = e.codigo == 1 and "30" in e.mensaje
    comprobar(largo, "una frase de mas de 30 palabras se rechaza (no se parte)")
    corto = AC.partir_en_clips([" ".join(["palabra"] * 26) + " fin.", "tres palabras aqui."])
    comprobar(len(corto) == 1 and AC.palabras_de(corto[0]) == 30, "un ultimo clip muy corto (3 palabras) se une al anterior si juntos no pasan de 30 palabras", str(corto))
    try:
        AC.partir_en_clips([" ".join(["palabra"] * 26) + " fin.", " ".join(["otra"] * 9) + " fin."])
        sin_forma = False
    except AC.ErrorAvatar as e:
        sin_forma = e.codigo == 1 and "27, 10" in e.mensaje and "de 25 a 30" in e.mensaje
    comprobar(sin_forma, "...pero 27 + 10 palabras no se pueden partir en clips de 25 a 30 con frases enteras: se dice con las palabras de cada frase (no se paga un clip de 10)")
    f15 = [" ".join(["uno"] * 14) + " fin."] * 6                     # 6 frases de 15 palabras = 90: 3 clips de 30
    comprobar([AC.palabras_de(c) for c in AC.partir_en_clips(f15)] == [30, 30, 30], "seis frases de 15 hacen 3 clips de 30 (lo mas parejo posible)")
    f13 = [" ".join(["uno"] * 12) + " fin."] * 6                     # 6 frases de 13 = 78: 3 clips de 26
    comprobar([AC.palabras_de(c) for c in AC.partir_en_clips(f13, 3)] == [26, 26, 26], "con n=3: exactamente 3 clips")
    try:
        AC.partir_en_clips(f13, 2)
        dos = False
    except AC.ErrorAvatar as e:
        dos = "exactamente 2 clips" in e.mensaje
    comprobar(dos, "con n=2 y 78 palabras no se puede (2 clips llevan hasta 60): se dice")
    try:
        AC.partir_en_clips(["", "  "])
        vacio = False
    except AC.ErrorAvatar as e:
        vacio = e.codigo == 2
    comprobar(vacio, "un guion sin frases se rechaza")
    comprobar(AC.texto_apto_para_decir("Cuesta 50 pesos") != [] and AC.texto_apto_para_decir("Escribe a https://x.co ya") != [] and AC.texto_apto_para_decir("Cuesta cincuenta pesos") == [],
              "las cifras y los enlaces no se dicen en voz alta; los numeros en letras si")

    seccion("avatar_comun: las escenas que escribe el modelo")
    def escenas(n=3, **cambios):
        o = {"ropa": "a sage green cotton blouse and light blue jeans", "clips": [{"escena": f"Three-quarter angle, medium shot, waist up, at the {LUGARES[i % 4]}, she is arranging small paper templates while talking, warm smile {i}", "accion":
                                                                                  "She walks into the frame holding a stack of templates", "usa_producto": False} for i in range(n)]}
        o.update(cambios)
        return o
    comprobar(AC.validar_escenas(escenas(), 3, True) == [], "un plan de escenas bien hecho pasa")
    comprobar(AC.validar_escenas(escenas(), 4, True) != [], "con otro numero de clips no pasa")
    comprobar(AC.validar_escenas([], 3, True) != [] and AC.validar_escenas(None, 3, True) != [], "algo que no es un objeto no pasa")
    malo = escenas()
    malo["clips"][1]["escena"] = "Cinematic 4K studio lighting shot of a woman at the kitchen counter with bokeh background and many objects"
    comprobar(any("delatan a la IA" in m for m in AC.validar_escenas(malo, 3, True)), "las palabras que delatan a la IA (cinematic, 4K, studio lighting, bokeh) se rechazan")
    malo = escenas()
    malo["clips"][2]["usa_producto"] = True
    comprobar(any("no hay fotos del producto" in m for m in AC.validar_escenas(malo, 3, False)), "mostrar el producto sin tener sus fotos se rechaza (nunca se genera solo con texto)")
    malo = escenas()
    malo["clips"][1]["escena"] = malo["clips"][0]["escena"]
    comprobar(any("casi igual" in m for m in AC.validar_escenas(malo, 3, True)), "dos clips seguidos con la misma escena se rechazan (cada clip cambia de lugar o de encuadre)")
    malo = escenas()
    malo["clips"][0]["escena"] += " with the logo of Marca Ajena Brasil on the wall"
    comprobar(any("Marca Ajena" in m for m in AC.validar_escenas(malo, 3, True, ["Marca Ajena Brasil"])), "un nombre prohibido (el anunciante original) en una escena se rechaza")
    malo = escenas()
    malo["clips"][0]["accion"] = "She opens https://tienda.co"
    comprobar(any("enlace" in m for m in AC.validar_escenas(malo, 3, True)), "un enlace en una escena se rechaza")
    malo = escenas(ropa="")
    comprobar(any("ropa" in m for m in AC.validar_escenas(malo, 3, True)), "sin ropa no pasa")
    malo = escenas()
    malo["clips"][0]["usa_producto"] = "si"
    comprobar(any("usa_producto" in m for m in AC.validar_escenas(malo, 3, True)), "usa_producto tiene que ser true o false")

    seccion("avatar_comun: los prompts (plantillas de references/prompts-ia.md)")
    avatar = {"genero": "mujer", "casa_base": "kitchen with a wooden counter", "voz": "a female voice in her mid-30s, calm", "acento": "Spanish with a Colombian accent"}
    plan = {"ropa": "a sage green blouse"}
    clip1 = {"n": 1, "escena": "Medium shot at the counter, preparing small templates", "usa_producto": False, "accion": "Walking into the frame holding templates", "texto": 'Hola, te cuento "algo" util.'}
    clip2 = {**clip1, "n": 2, "usa_producto": True}
    p1 = AC.prompt_cuadro(clip1, plan, avatar, False, None, False)
    p2 = AC.prompt_cuadro(clip2, plan, avatar, True, "A white ebook with a blue cover", False)
    p2b = AC.prompt_cuadro(clip2, plan, avatar, True, None, True)
    comprobar(p1.startswith("Same woman as the reference photo: keep her face") and "She wears a sage green blouse" in p1 and "Place: kitchen with a wooden counter" in p1, "el cuadro ancla sigue la PLANTILLA 1")
    comprobar("reference photos" in p2 and "the second reference" in p2 and "Product: A white ebook" in p2 and "Place:" not in p2, "los demas cuadros siguen la PLANTILLA 2 (y llevan el producto si el clip lo muestra)")
    comprobar("reproduce the reference cover exactly" in p2b, "si solo hay portada, se pide reproducirla tal cual")
    comprobar(all("No text, no readable brand names" in p and "home video filmed on a phone" in p for p in (p1, p2)) and not AC.PROHIBIDAS_EN_PROMPT.search(p1 + p2), "todos llevan el aspecto de video casero, sin texto ni marcas y sin palabras de IA")
    pv = AC.prompt_clip(clip1, avatar)
    comprobar('says, warmly and naturally, with small hand gestures: "Hola, te cuento \'algo\' util."' in pv, "el clip lleva el dialogo entre comillas y las comillas del texto se cambian para no romperlo")
    comprobar("VOICE: a female voice in her mid-30s, calm. The voice is exactly the same" in pv and "ACCENT: Spanish with a Colombian accent." in pv and "lip-synced dialogue only in Spanish" in pv, "los bloques VOICE, ACCENT y RESTRICTIONS van en el clip")
    comprobar(AC.prompt_clip({**clip2, "texto": "Otro texto."}, avatar).count("VOICE: a female voice in her mid-30s, calm.") == 1 and
              AC.prompt_clip(clip1, avatar).split("VOICE:")[1] == AC.prompt_clip({**clip1, "accion": "Distinta"}, avatar).split("VOICE:")[1], "VOICE y ACCENT son identicos, palabra por palabra, en todos los clips")
    hombre = AC.prompt_cuadro(clip1, plan, {**avatar, "genero": "hombre"}, False, None, False)
    comprobar("Same man as the reference photo" in hombre and "his face" in hombre and "woman" not in hombre and " she " not in hombre.lower(), "con un avatar hombre cambian man/he/his en el cuadro")
    comprobar("he finishes" in AC.prompt_clip(clip1, {**avatar, "genero": "hombre"}).lower() and " she " not in AC.prompt_clip(clip1, {**avatar, "genero": "hombre"}).lower(), "...y en el video")

    seccion("avatar_comun: medir un clip y un cuadro")
    esperado = "Hola soy Lucia y hoy te cuento cómo hacer moldes muy faciles"
    r = AC.revisar_clip(esperado, esperado.split())
    comprobar(r["ok"] is True and r["cobertura"] == 1.0, "un clip que dice todo su texto pasa")
    r = AC.revisar_clip(esperado, "hola soy lucia y hoy te cuento".split())
    comprobar(r["ok"] is False and r["cobertura"] < 0.85, "un clip al que le falta la mitad del texto NO pasa", str(r))
    r = AC.revisar_clip(esperado, ["Hola", "soy", "Lucía", "y", "hoy", "te", "cuento", "como", "hacer", "moldes", "muy", "fáciles"])
    comprobar(r["ok"] is True, "las tildes y las mayusculas no cuentan")
    r = AC.revisar_clip(esperado, "hola soy lucia y hoy te cuento como hacer moldes muy".split())
    comprobar(r["ok"] is False and r["ultima_palabra"] is False, "si la ultima palabra queda cortada NO pasa aunque lo demas se oiga")
    r = AC.revisar_clip(esperado, esperado.split() + "y ademas dime que mas quieres saber hoy amiga".split())
    comprobar(r["ok"] is False and r["sobran"] >= 5, "si el modelo se inventa palabras de mas NO pasa")
    r = AC.revisar_clip(esperado, None)
    comprobar(r["ok"] is None and r["sin_verificar"], "sin transcripcion (Whisper no pudo) queda «sin verificar», no se aprueba ni se rechaza")
    comprobar(AC.cuadro_malo({"misma_persona": True, "ropa_igual_que_el_ancla": True, "texto_o_logos_visibles": False, "una_sola_persona": True, "manos_o_cara_deformes": False}, False) == [], "un cuadro sin problemas pasa")
    comprobar("no parece la misma persona que la foto del avatar" in AC.cuadro_malo({"misma_persona": False}, True), "otra persona se rechaza")
    comprobar(AC.cuadro_malo({"ropa_igual_que_el_ancla": False}, True) == [] and AC.cuadro_malo({"ropa_igual_que_el_ancla": False}, False) != [], "la ropa solo se compara con el ancla en los cuadros que no son el ancla")
    comprobar(len(AC.cuadro_malo({"texto_o_logos_visibles": True, "una_sola_persona": False, "manos_o_cara_deformes": True, "producto_deformado": True}, False)) == 4, "texto, mas de una persona, manos deformes y producto deformado se rechazan")
    comprobar(AC.cuadro_malo({"misma_persona": None, "producto_deformado": None}, True) == [] and AC.cuadro_malo(None, True) == [], "lo que el modelo no pudo saber (null) o una respuesta rara no rechaza: es un aviso, no una garantia")


# ------------------------------------------------------------------ 2. el avatar en disco y planear
def avatar_y_planear(tmp: Path, d: Doble) -> None:
    seccion("leer_avatar e instalar_avatar")
    mundo = armar_mundo(tmp, avatar=False)
    d_prod = mundo["d_prod"]
    c, msg = ejecutar(AV.leer_avatar, d_prod)
    comprobar(c == 2 and "No hay ningun avatar" in msg and "AVATAR" in msg, "sin avatar: faltan insumos (2) y dice donde ponerlo", msg)
    (d_prod / "avatares" / "avatar-ejemplo").mkdir(parents=True)
    (d_prod / "avatares" / "avatar-ejemplo" / "avatar.json").write_text(json.dumps({"_nota": "PLANTILLA", "nombre": "avatar-ejemplo", "fotos": ["foto1.jpg"], "aspecto": "apparent age, hair, facial features (in English)",
                                                                                     "voz": "a female voice in her mid-30s, medium pitch, calm and confident, warm", "acento": "Spanish with a clear, neutral Colombian accent",
                                                                                     "casa_base": "el lugar donde graba, con objetos concretos"}), encoding="utf-8")
    c, msg = ejecutar(AV.leer_avatar, d_prod)
    comprobar(c == 2 and "avatar-ejemplo" in msg and "autorizado" in msg, "la plantilla de ejemplo no cuenta como avatar y dice que le falta", msg)
    origen = tmp / "AVATAR"
    crear_jpg(origen / "foto1.jpg")
    crear_jpg(origen / "foto2.jpg")
    (origen / "avatar.json").write_text(json.dumps(ficha_avatar(fotos=["foto1.jpg", "foto2.jpg"])), encoding="utf-8")
    (origen / "otra.txt").write_text("no debe copiarse", encoding="utf-8")
    destino = AV.instalar_avatar(origen, d_prod)
    comprobar(destino == d_prod / "avatares" / "lucia" and sorted(p.name for p in destino.iterdir()) == ["avatar.json", "foto1.jpg", "foto2.jpg"], "instalar copia solo las fotos de la ficha y la ficha", str(sorted(p.name for p in destino.iterdir())))
    a = AV.leer_avatar(d_prod)
    comprobar(a["nombre"] == "lucia" and len(a["fotos"]) == 2 and a["genero"] == "mujer" and a["fotos"][0].name == "foto1.jpg", "con un avatar listo, lo lee (la plantilla de ejemplo se salta)")
    comprobar(AV.leer_avatar(d_prod, "lucia")["nombre"] == "lucia", "se puede pedir por nombre")
    c, msg = ejecutar(AV.leer_avatar, d_prod, "../x")
    comprobar(c == 1, "un nombre de avatar con rutas se rechaza", msg)
    c, msg = ejecutar(AV.leer_avatar, d_prod, "noexiste")
    comprobar(c == 2, "un avatar que no existe: faltan insumos", msg)
    mala = tmp / "AVATAR2"
    crear_jpg(mala / "foto1.jpg")
    (mala / "avatar.json").write_text(json.dumps(ficha_avatar(autorizado=False)), encoding="utf-8")
    c, msg = ejecutar(AV.instalar_avatar, mala, d_prod)
    comprobar(c == 2 and "autorizado" in msg, "instalar rechaza un avatar sin autorizacion", msg)

    seccion("planear: el costo, el tope y las escenas")
    mundo = armar_mundo(tmp)
    ctx = AV.armar_contexto(args())
    c, _ = ejecutar(AV.planear, ctx, args(dry_run=True))
    comprobar(c == 0 and not d.textos and not (ctx["trabajo"] / "plan.json").exists(), "--dry-run dice el costo y no pide ni escribe nada")
    receta = json.loads((mundo["base"] / "receta.json").read_text())
    receta["aprobacion"]["topeUsd"] = 0
    (mundo["base"] / "receta.json").write_text(json.dumps(receta), encoding="utf-8")
    ctx = AV.armar_contexto(args(tope_usd=None))
    c, _ = ejecutar(AV.planear, ctx, args(tope_usd=None))
    comprobar(c == 3 and not d.textos, "sin OK de gasto (ni --tope-usd ni la receta) no se pide nada: sale con 3", str(c))
    ctx = AV.armar_contexto(args(tope_usd=0.20))
    c, _ = ejecutar(AV.planear, ctx, args(tope_usd=0.20))
    comprobar(c == 3 and not d.textos and not (ctx["trabajo"] / "plan.json").exists(), "con un tope que no alcanza para 3 clips (US$ 0,20) NO se pide nada: sale con 3")
    receta["aprobacion"]["topeUsd"] = 0.30
    (mundo["base"] / "receta.json").write_text(json.dumps(receta), encoding="utf-8")
    ctx = AV.armar_contexto(args(tope_usd=None))
    c, _ = ejecutar(AV.planear, ctx, args(tope_usd=None))
    plan = json.loads((ctx["trabajo"] / "plan.json").read_text(encoding="utf-8")) if (ctx["trabajo"] / "plan.json").is_file() else {}
    comprobar(c == 0 and len(d.textos) == 1 and plan.get("n_clips") == 3 and len(plan.get("clips", [])) == 3, "con el tope de la receta (US$ 0,30) planea 3 clips con UNA sola llamada de texto", f"c={c} textos={len(d.textos)}")
    comprobar([cl["texto"] for cl in plan["clips"]] == AC.partir_en_clips(FRASES) and plan["clips"][0]["palabras"] == AC.palabras_de(plan["clips"][0]["texto"]), "el texto de cada clip es el del guion, sin tocar una palabra")
    comprobar(plan["avatar"] == "lucia" and plan["ropa"] and plan["clips"][1]["usa_producto"] is True and all(cl["escena"] and cl["accion"] for cl in plan["clips"]), "el plan trae la ropa, el avatar y la escena y accion de cada clip")
    pedido = d.textos[0]
    comprobar("Marca Ajena Brasil" in pedido["usuario"] and "Marca Ajena Brasil" not in pedido["sistema"], "el anunciante original viaja como «no mencionar» (dato), no como instruccion")
    comprobar((ctx["trabajo"] / "plan.md").is_file() and "clip" in (ctx["trabajo"] / "plan.md").read_text(encoding="utf-8").lower(), "deja plan.md para leerlo")
    led = AV.leer_ledger(ctx["trabajo"])
    comprobar(len(led["analisis"]) == 1 and led["analisis"][0]["etiqueta"] == "avatar-escenas", "la llamada de texto queda en el ledger")
    c, _ = ejecutar(AV.planear, ctx, args(tope_usd=None))
    comprobar(c == 0 and len(d.textos) == 1, "si el plan ya existe para ese guion, no se vuelve a pedir")
    c, _ = ejecutar(AV.planear, ctx, args(tope_usd=None, rehacer=True))
    comprobar(c == 0 and len(d.textos) == 2, "--rehacer lo vuelve a pedir")

    # un plan que el modelo escribe mal dos veces
    armar_mundo(tmp)
    d.escenas_malas = 2
    n_t = len(d.textos)
    ctx = AV.armar_contexto(args())
    c, msg = ejecutar(AV.planear, ctx, args())
    comprobar(c == 1 and len(d.textos) == n_t + 2 and (ctx["trabajo"] / "plan_rechazado.json").is_file() and not (ctx["trabajo"] / "plan.json").exists(), "si el modelo escribe mal el plan dos veces: error, nada de imagenes y el rechazo queda guardado", msg)
    comprobar("DATOS" in (ctx["trabajo"] / "plan_rechazado.json").read_text(encoding="utf-8"), "...marcado como datos, no instrucciones")
    d.escenas_malas = 1
    n_t = len(d.textos)
    c, _ = ejecutar(AV.planear, ctx, args())
    comprobar(c == 0 and len(d.textos) == n_t + 2, "si el segundo intento sale bien, sigue (y le dice al modelo por que se rechazo el primero)")
    comprobar("rejected because" in d.textos[-1]["usuario"], "...en el segundo pedido va el motivo del rechazo")

    # sin guion, sin avatar, frase demasiado larga, cifras
    armar_mundo(tmp, con_guion=False)
    c, msg = ejecutar(AV.planear, AV.armar_contexto(args()), args())
    comprobar(c == 2 and "guion_auto.py" in msg and "--tipo ia" in msg, "sin guion: faltan insumos y dice el comando para escribirlo", msg)
    armar_mundo(tmp, avatar=False)
    c, msg = ejecutar(AV.planear, AV.armar_contexto(args()), args())
    comprobar(c == 2 and "avatar" in msg.lower(), "sin avatar: faltan insumos y no se pide nada", msg)
    mundo = armar_mundo(tmp)
    g = json.loads((mundo["base"] / "guion.json").read_text(encoding="utf-8"))
    g["frases"][0]["texto"] = "palabra " * 30 + "fin."
    (mundo["base"] / "guion.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    c, msg = ejecutar(AV.planear, AV.armar_contexto(args()), args())
    comprobar(c == 1 and "30" in msg, "una frase de mas de 30 palabras detiene el plan antes de gastar", msg)
    g["frases"][0]["texto"] = "Cuesta 50 pesos y lo recibes hoy mismo en tu casa."
    (mundo["base"] / "guion.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    c, msg = ejecutar(AV.planear, AV.armar_contexto(args()), args())
    comprobar(c == 1 and "cifras" in msg, "una cifra en el guion detiene el plan (se escribe en letras)", msg)
    mundo = armar_mundo(tmp, receta_extra={"tipoAnuncio": "tiktok"})
    c, msg = ejecutar(lambda: AV.armar_contexto(args()))
    comprobar(c == 2 and "no es de avatar" in msg, "un pedido que no es de avatar se rechaza", msg)
    c, msg = ejecutar(lambda: AV.armar_contexto(args(producto="Malo/../x")))
    comprobar(c == 1, "un nombre de producto con rutas se rechaza", msg)
    c, msg = ejecutar(lambda: AV.armar_contexto(args(anuncio="AD99")))
    comprobar(c == 1, "un id de anuncio raro se rechaza", msg)


# ------------------------------------------------------------------ 3. generar
def planeado(tmp: Path, d: Doble, **mundo_kw) -> dict:
    armar_mundo(tmp, **mundo_kw)
    ctx = AV.armar_contexto(args())
    c, msg = ejecutar(AV.planear, ctx, args())
    assert c == 0, msg
    return ctx


def llamadas(d: Doble, cmd: str) -> list[list[str]]:
    return [x for x in d.llamadas if x[0] == cmd]


def valor(argv: list[str], bandera: str) -> str:
    return argv[argv.index(bandera) + 1]


def generar_casos(tmp: Path, d: Doble) -> None:
    seccion("generar: cuadros y clips dentro del tope")
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    c, _ = ejecutar(AV.generar, ctx, args())
    imgs, vids = llamadas(d, "imagen"), llamadas(d, "video")
    comprobar(c == 0 and len(imgs) == 3 and len(vids) == 3, "el camino feliz pide 3 cuadros y 3 clips", f"c={c} img={len(imgs)} vid={len(vids)}")
    orden = [x[0] for x in d.llamadas]
    comprobar(orden == ["imagen", "video", "imagen", "video", "imagen", "video"], "van en orden: cuadro 1, clip 1, cuadro 2, clip 2…", str(orden))
    comprobar([valor(x, "--etiqueta") for x in imgs] == ["avatar01", "avatar02", "avatar03"] and all(valor(x, "--producto") == PRODUCTO and valor(x, "--anuncio") == AD for x in d.llamadas),
              "cada llamada lleva --producto y --anuncio (de ahi sale «Costo por proyecto») y su etiqueta")
    comprobar(valor(vids[0], "--duracion") == "8" and valor(vids[0], "--imagen").endswith("01.png") and valor(vids[2], "--imagen").endswith("03.png"), "cada clip es de 8 s y sale de su cuadro")
    refs1 = [x for i, x in enumerate(imgs[0]) if imgs[0][i - 1] == "--ref"]
    refs2 = [x for i, x in enumerate(imgs[1]) if imgs[1][i - 1] == "--ref"]
    comprobar(len(refs1) == 1 and "foto1" in Path(refs1[0]).name, "el cuadro ancla lleva como referencia la foto del avatar", str(refs1))
    comprobar([Path(r).name for r in refs2][:2] == ["foto1.jpg", "01.png"] and Path(refs2[-1]).name == "p001.jpg", "el cuadro 2 (que muestra el producto) lleva: foto del avatar, cuadro ancla y la pagina del producto, en ese orden", str(refs2))
    refs3 = [x for i, x in enumerate(imgs[2]) if imgs[2][i - 1] == "--ref"]
    comprobar([Path(r).name for r in refs3] == ["foto1.jpg", "01.png"], "el cuadro 3 (sin producto) lleva foto del avatar y ancla, nada del producto", str([Path(r).name for r in refs3]))
    prompt_cuadro1 = Path(valor(imgs[0], "--prompt-archivo")).read_text(encoding="utf-8")
    prompt_clip2 = Path(valor(vids[1], "--prompt-archivo")).read_text(encoding="utf-8")
    plan = json.loads((ctx["trabajo"] / "plan.json").read_text(encoding="utf-8"))
    comprobar("Same woman as the reference photo" in prompt_cuadro1 and "Place: kitchen with a wooden counter" in prompt_cuadro1, "el prompt del cuadro 1 es la plantilla del ancla con la casa del avatar")
    comprobar(plan["clips"][1]["texto"] in prompt_clip2 and "VOICE: a female voice in her mid-30s" in prompt_clip2, "el prompt del clip 2 lleva su texto y la voz del avatar")
    comprobar(len(d.textos) == 3 and all(t["etiqueta"] == "avatar-revision-cuadro" for t in d.textos), "cada cuadro lo revisa el modelo de vision (3 revisiones)")
    comprobar(len(d.textos[0]["imagenes"]) == 2 and len(d.textos[1]["imagenes"]) == 3, "el ancla se revisa contra la foto del avatar; los demas, tambien contra el ancla")
    led = AV.leer_ledger(ctx["trabajo"])
    comprobar(all(ctx["cuadros"].joinpath(f"{i:02d}.png").is_file() and ctx["clips"].joinpath(f"{i:02d}.mp4").is_file() for i in (1, 2, 3)), "quedan los 3 cuadros y los 3 clips en su carpeta")
    comprobar(abs(AV.total_ledger(led)["imagenes"] - 3 * 0.0085) < 1e-6 and abs(AV.total_ledger(led)["clips"] - 3 * 0.0816) < 1e-6, "el ledger suma lo gastado en cuadros y clips")
    comprobar(all((led["clips"][k].get("revision") or {}).get("ok") is True for k in ("01", "02", "03")) and all((led["imagenes"][k].get("revision") or {}).get("ok") is True for k in ("01", "02", "03")),
              "cada clip y cada cuadro quedaron revisados y aprobados")
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(informe["estado"] == "completo" and informe["avisos"] == [], "generacion.json dice «completo» y sin avisos")
    comprobar(0.27 < AV.gastado_en_anuncio(PRODUCTO, AD) < 0.30, "lo gastado en el anuncio (costos.jsonl) queda bajo el tope de US$ 0,30", str(AV.gastado_en_anuncio(PRODUCTO, AD)))

    n_antes = len(d.llamadas)
    c, _ = ejecutar(AV.generar, ctx, args())
    comprobar(c == 0 and len(d.llamadas) == n_antes, "repetir el comando no vuelve a pedir ni a pagar nada")

    seccion("generar: tope de gasto")
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.10))
    comprobar(c == 3 and not d.llamadas, "con un tope que no alcanza para lo que falta no se pide nada (3)", str(c))
    receta = json.loads((ctx["base"] / "receta.json").read_text())
    receta["aprobacion"]["topeUsd"] = 0
    (ctx["base"] / "receta.json").write_text(json.dumps(receta), encoding="utf-8")
    ctx = AV.armar_contexto(args())
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=None))
    comprobar(c == 3 and not d.llamadas, "sin OK de gasto no se pide nada (3)")
    c, _ = ejecutar(AV.generar, ctx, args(dry_run=True))
    comprobar(c == 0 and not d.llamadas, "--dry-run no pide nada")
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.2903))
    comprobar(c == 0 and len(llamadas(d, "video")) == 3 and len(d.textos) == 3, "con US$ 0,29 (3 clips y los textos) sale completo, con sus 3 revisiones de cuadro", f"c={c} videos={len(llamadas(d, 'video'))} revisiones={len(d.textos)}")
    comprobar(sum(1 for x in AV.leer_ledger(ctx["trabajo"])["analisis"] if x["etiqueta"] == "avatar-revision-cuadro") == 3, "...y las revisiones quedan en el ledger")
    # con el tope justo para los pagos, la revision de cuadros le quita plata al ultimo clip: se detiene sin pasarse
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.2703))
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(c == 5 and len(llamadas(d, "video")) == 2 and any("clip 03" in x and "tope" in x for x in informe["avisos"]), "con US$ 0,2703 y revision de cuadros, el clip 3 no cabe: se detiene (5) y lo dice, sin pasarse", f"c={c} {informe['avisos']}")
    comprobar(AV.gastado_en_anuncio(PRODUCTO, AD) <= 0.2703 + 0.0036, "...y lo gastado no pasa del tope (salvo los centavos de la revision que ya estaba en curso)", str(AV.gastado_en_anuncio(PRODUCTO, AD)))
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.2703, sin_revision_visual=True))
    comprobar(c == 0 and len(llamadas(d, "video")) == 3 and not d.textos, "...pero con el tope justo y SIN revision de cuadros sale completo (3 × 0,0901 = 0,2703)")
    # APIMart cobra el doble de lo previsto: despues del segundo clip el tope ya se paso y el tercer cuadro no se pide
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.factor_costo = 2.0
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.30, sin_revision_visual=True))
    d.factor_costo = 1.0
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(c == 5 and len(llamadas(d, "imagen")) == 2 and len(llamadas(d, "video")) == 2 and any("cuadro 03" in x and "tope" in x for x in informe["avisos"]),
              "si APIMart cobra mas que la lista, en cuanto el tope se pasa no se pide nada mas (5)", f"c={c} {informe['avisos']}")

    seccion("generar: reintentos, fallos y revisiones")
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    d.oido = {"02.mp4": "hola amigas hoy".split()}
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.40))
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(c == 0 and len(llamadas(d, "video")) == 3 and any("clip 02" in x and "una persona debe oirlo" in x for x in informe["avisos"]), "orden del dueño: sin --repetir-clip un clip con errores NO se repite y se avisa", f"c={c} videos={len(llamadas(d, 'video'))}")
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.40, repetir_clip=True))
    comprobar(c == 0 and len(llamadas(d, "video")) == 4, "con --repetir-clip un clip al que le faltan palabras se repite UNA vez (4 clips en total)", f"c={c} videos={len(llamadas(d, 'video'))}")
    con_repetir = [x for x in llamadas(d, "video") if "--repetir" in x]
    comprobar(len(con_repetir) == 1 and con_repetir[0][con_repetir[0].index("--salida") + 1].endswith("02_b.mp4"),
              "solo la repeticion lleva --repetir (apimart.py la cuenta: una por anuncio)", str([x[x.index("--salida") + 1] for x in con_repetir]))
    led = AV.leer_ledger(ctx["trabajo"])
    comprobar(led["clips"]["02"]["intentos"] == 2 and abs(led["clips"]["02"]["costo"] - 2 * 0.0816) < 1e-6, "el ledger cuenta los dos intentos del clip 2 y su costo", str(led["clips"]["02"]))
    comprobar(not (ctx["clips"] / "02_b.mp4").exists(), "el reintento reemplaza al clip (no quedan dos archivos)")
    comprobar((led["clips"]["02"]["revision"] or {}).get("ok") is True, "...y quedo revisado y aprobado (el segundo intento SI dice su texto)")
    # el reintento tampoco pasa: se queda con el mejor y se avisa
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.oido = {"02.mp4": "hola amigas hoy".split(), "02_b.mp4": "hola amigas hoy te cuento".split()}
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.40, repetir_clip=True))
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(c == 0 and any("clip 02" in x and "una persona debe oirlo" in x for x in informe["avisos"]), "si el reintento tampoco dice su texto, se queda con el mejor y avisa que una persona debe oirlo", str(informe["avisos"]))
    led = AV.leer_ledger(ctx["trabajo"])
    comprobar((led["clips"]["02"]["revision"] or {}).get("ok") is False and len(llamadas(d, "video")) == 4, "...y el clip queda marcado como «no dice su texto» (el control de calidad lo vera)")
    # sin plata para reintentar: el ultimo clip no dice su texto y el tope ya se uso entero
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.oido = {"03.mp4": "hola amigas".split()}
    c, _ = ejecutar(AV.generar, ctx, args(tope_usd=0.2703, sin_revision_visual=True))
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(c == 0 and len(llamadas(d, "video")) == 3 and any("clip 03" in x and "una persona debe oirlo" in x for x in informe["avisos"]), "sin tope para reintentar el clip 3, no se repite y se avisa que una persona debe oirlo", str(informe["avisos"]))
    d.oido = {}
    # Whisper no puede
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    antes = AV.transcribir_clip
    AV.transcribir_clip = lambda ruta, trabajo: None
    c, _ = ejecutar(AV.generar, ctx, args())
    AV.transcribir_clip = antes
    led = AV.leer_ledger(ctx["trabajo"])
    comprobar(c == 0 and len(llamadas(d, "video")) == 3 and all((led["clips"][k]["revision"] or {}).get("sin_verificar") for k in ("01", "02", "03")), "si Whisper no puede oir, los clips quedan «sin verificar» y no se repiten")
    # el ancla no se parece
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    d.veredictos = [{"misma_persona": False, "texto_o_logos_visibles": False, "una_sola_persona": True, "manos_o_cara_deformes": False}] * 2
    c, _ = ejecutar(AV.generar, ctx, args())
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(c == 5 and len(llamadas(d, "imagen")) == 2 and not llamadas(d, "video") and informe["estado"] == "incompleto" and any("ancla" in x for x in informe["avisos"]),
              "si el cuadro ancla no se parece al avatar dos veces, NO se paga ningun clip (5) y se avisa", f"c={c} img={len(llamadas(d, 'imagen'))} vid={len(llamadas(d, 'video'))}")
    # un cuadro que no es el ancla se rechaza una vez y se rehace
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    d.veredictos = [{"misma_persona": True, "ropa_igual_que_el_ancla": True, "texto_o_logos_visibles": False, "una_sola_persona": True, "manos_o_cara_deformes": False}, {"misma_persona": True,
                    "ropa_igual_que_el_ancla": False, "texto_o_logos_visibles": False, "una_sola_persona": True, "manos_o_cara_deformes": False}]
    c, _ = ejecutar(AV.generar, ctx, args())
    comprobar(c == 0 and len(llamadas(d, "imagen")) == 4 and len(llamadas(d, "video")) == 3, "un cuadro con la ropa cambiada se rehace UNA vez (4 cuadros en total)", f"img={len(llamadas(d, 'imagen'))}")
    # el modelo de vision no contesta: se acepta
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.vision_cae = True
    c, _ = ejecutar(AV.generar, ctx, args())
    d.vision_cae = False
    comprobar(c == 0 and len(llamadas(d, "imagen")) == 3, "si el modelo de vision no contesta, los cuadros se aceptan (es un aviso, no una garantia)")
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.textos.clear()
    c, _ = ejecutar(AV.generar, ctx, args(sin_revision_visual=True))
    comprobar(c == 0 and not d.textos, "--sin-revision-visual no llama al modelo de vision")
    # APIMart falla en el cuadro 2 y se retoma
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.falla = {"imagen:02": 1}
    c, _ = ejecutar(AV.generar, ctx, args())
    informe = json.loads((ctx["trabajo"] / "generacion.json").read_text(encoding="utf-8"))
    comprobar(c == 5 and len(llamadas(d, "imagen")) == 2 and len(llamadas(d, "video")) == 1 and any("cuadro 02 fallo" in x for x in informe["avisos"]), "si APIMart falla en el cuadro 2: se detiene (5), ya pago el 1 y su clip, y lo dice", str(informe["avisos"]))
    gastado = AV.gastado_en_anuncio(PRODUCTO, AD)
    d.llamadas.clear()
    c, _ = ejecutar(AV.generar, ctx, args())
    comprobar(c == 0 and len(llamadas(d, "imagen")) == 2 and len(llamadas(d, "video")) == 2, "al repetir, retoma: NO vuelve a pagar el cuadro 1 ni el clip 1", f"img={len(llamadas(d, 'imagen'))} vid={len(llamadas(d, 'video'))}")
    comprobar(AV.gastado_en_anuncio(PRODUCTO, AD) < gastado + 0.20, "...y el gasto total no se duplica")
    # un clip falla
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.falla = {"video:03": 1}
    c, _ = ejecutar(AV.generar, ctx, args())
    comprobar(c == 5 and (ctx["cuadros"] / "03.png").is_file() and not (ctx["clips"] / "03.mp4").exists(), "si falla el clip 3: 5, queda su cuadro y no hay clip a medias")
    d.falla = {}
    # sin informar el costo: se estima
    ctx = planeado(tmp, d)
    d.llamadas.clear()
    d.informar_costo = False
    c, _ = ejecutar(AV.generar, ctx, args(sin_revision_visual=True))
    d.informar_costo = True
    led = AV.leer_ledger(ctx["trabajo"])
    comprobar(c == 0 and led["imagenes"]["01"]["medido"] is False and abs(led["clips"]["01"]["costo"] - 0.0816) < 1e-9, "si APIMart no informa el costo, se cuenta el de lista y el ledger lo marca como estimado")
    # rehacer: con clips ya pagados se niega sin borrar nada (orden del dueño, 2026-11-10); sin clips pagados, vuelve a generar todo
    n = len(d.llamadas)
    import contextlib
    import io
    oido = io.StringIO()
    with contextlib.redirect_stdout(oido):
        c, _ = ejecutar(AV.generar, ctx, args(rehacer=True, sin_revision_visual=True))
    msg = oido.getvalue()
    comprobar(c == 3 and len(d.llamadas) == n and (ctx["clips"] / "01.mp4").is_file() and "ya se pagaron" in msg, "--rehacer con clips ya pagados no borra ni vuelve a pagar nada", f"c={c} {msg[-160:]}")
    A.COSTOS.unlink(missing_ok=True)
    c, _ = ejecutar(AV.generar, ctx, args(rehacer=True, sin_revision_visual=True))
    led = AV.leer_ledger(ctx["trabajo"])
    comprobar(c == 0 and len(d.llamadas) == n + 6 and led["anteriores_usd"] > 0.25, "--rehacer sin clips pagados lo vuelve a generar todo y deja anotado lo gastado antes", f"llamadas nuevas={len(d.llamadas) - n}")
    disp, origen = AV.disponible_para(args(tope_usd=0.9), {}, 0.1)
    comprobar(abs(disp - 0.40) < 1e-9 and "0.50" in origen, "con --tope-usd 0,9 y US$ 0,10 ya gastados, quedan US$ 0,40 (el tope de 0,50 manda)", f"{disp} {origen}")
    disp, _ = AV.disponible_para(args(tope_usd=None), {"aprobacion": {"topeUsd": 2}}, 0.0)
    comprobar(abs(disp - 0.50) < 1e-9, "una receta con US$ 2 queda en 0,50", str(disp))
    disp, origen = AV.disponible_para(args(tope_usd=0.2), {}, 0.0)
    comprobar(abs(disp - 0.2) < 1e-9 and "--tope-usd" in origen, "un tope menor que 0,50 se respeta", f"{disp} {origen}")
    seis = AC.clips_que_caben(25, 60, 0.57)
    cinco = AC.clips_que_caben(25, 60, min(0.57, AV.TOPE_ANUNCIO_USD))
    comprobar(seis["n"] == 6 and cinco["n"] == 5 and cinco["costo"] <= AV.TOPE_ANUNCIO_USD, "con US$ 0,57 cabrian 6 clips; con el tope de 0,50, 5 (y caben)", f"{seis['n']} {cinco['n']} {cinco['costo']}")
    # sin plan
    armar_mundo(tmp)
    c, msg = ejecutar(AV.generar, AV.armar_contexto(args()), args())
    comprobar(c == 2 and "planear" in msg, "sin plan: faltan insumos y dice que corra planear", msg)


# ------------------------------------------------------------------ 4. montar
def montar_casos(tmp: Path, d: Doble) -> None:
    import montaje_auto as M

    seccion("montar: unir, videoData, receta, render y compuertas")
    avisos_dash: list[dict] = []
    import fabrica

    guardado = {k: getattr(M, k) for k in ("renderizar", "obtener_transcripcion", "medir_sonoridad", "elegir_musica")}
    guardado_f = fabrica.reportar_progreso
    guardado_det = M.et.DetectorTexto.disponible

    def render_falso(ruta_vd, conc, log, mostrar=print):
        vd = json.loads(Path(ruta_vd).read_text(encoding="utf-8"))
        final = Path(ruta_vd).parent / "final" / f"{PRODUCTO}_{AD}.mp4"
        crear_final(final, float(vd["meta"]["duracion"]))
        return final, 1.5

    def transcripcion_falsa(voz, base, idioma, guion=None, mostrar=print):
        dur = float(AV.duracion_seg(voz))
        texto = (base / "guion.txt").read_text(encoding="utf-8").split()
        paso = dur / len(texto)
        return {"palabras": [{"texto": w, "inicio": round(i * paso, 3), "fin": round((i + 0.8) * paso, 3)} for i, w in enumerate(texto)], "alineado_con_guion": True}

    M.renderizar = render_falso
    M.obtener_transcripcion = transcripcion_falsa
    M.medir_sonoridad = lambda ruta: -14.2
    M.elegir_musica = lambda receta, arg, previo: None
    M.et.DetectorTexto.disponible = staticmethod(lambda: False)
    fabrica.reportar_progreso = lambda proyecto, anuncio, mostrar=print, **datos: avisos_dash.append({"proyecto": proyecto, "anuncio": anuncio, **datos}) or True
    os.environ["DASH_URL"], os.environ["DASH_CLAVE_EDITOR"] = "https://dash.example.com", "k" * 40
    salida: list[str] = []
    try:
        ctx = planeado(tmp, d)
        c, _ = ejecutar(AV.generar, ctx, args(sin_revision_visual=True))
        assert c == 0
        avisos_dash.clear()
        informe = AV.montar(ctx, args(), mostrar=salida.append)
        base = ctx["base"]
        vd = json.loads((base / "videoData.json").read_text(encoding="utf-8"))
        comprobar(informe["estado"] == "aprobado", "el montaje completo con clips que dicen su texto queda «aprobado»", f"{informe['estado']} {[k for k, v in informe.get('compuertas', {}).items() if v['ok'] is False]}")
        comprobar((base / "base.mp4").is_file() and (base / "voz_norm.wav").is_file() and (base / "clips.json").is_file(), "unir_clips.py deja base.mp4, voz_norm.wav y clips.json")
        comprobar(len(vd["tomas"]) == 1 and vd["tomas"][0]["src"].endswith("/ia/AD4/base.mp4") and vd["tomas"][0]["inicio"] == 0 and abs(vd["tomas"][0]["duracion"] - vd["meta"]["duracion"]) < 0.01, "videoData.json tiene UNA toma: el video unido, de principio a fin")
        comprobar(vd["meta"]["tipo"] == "ia" and abs(vd["meta"]["duracion"] - AV.duracion_seg(base / "base.mp4")) < 0.01, "es de tipo ia y dura lo que el video unido")
        comprobar(vd["audio"]["voz"]["src"].endswith("audio/voz_norm.wav") and len(vd["palabras"]) >= 5, "la voz es la de los clips y los subtitulos salen de la transcripcion")
        comprobar(len(vd["textosGrandes"]) <= 2 and all(t["posicion"] == "arriba" for t in vd["textosGrandes"]), "a lo mucho 2 textos grandes y arriba (sobre la cara de la persona no)")
        comprobar(vd["producto"] == [] and vd["cta"]["texto"] == "Pídelo ahora", "no se pone la portada encima del avatar y el boton final es el de la receta")
        comprobar(informe["errores_validador"] == [], "el validador no encuentra errores")
        comp = informe["compuertas"]
        comprobar(comp["clips_dicen_su_texto"]["ok"] is True and comp["cuadros_revisados"]["ok"] is True and comp["formato"]["ok"] is True and comp["volumen"]["ok"] is True and comp["tomas_sin_repetir"]["ok"] is True,
                  "las compuertas del avatar pasan: clips, cuadros, formato, volumen y una sola toma")
        comprobar(comp["duracion_en_receta"]["ok"] is True, "la duracion cae dentro de lo que pide la receta", comp["duracion_en_receta"]["detalle"])
        comprobar(comp["subtitulos_iguales_al_guion"]["ok"] is True, "los subtitulos dicen lo mismo que el guion")
        comprobar([a.get("etapa") for a in avisos_dash] == ["plan", "render", "revision"], "le avisa al dash la etapa plan, luego render y luego revision", str([a.get("etapa") for a in avisos_dash]))
        comprobar(len(avisos_dash[0]["plan"]) == 3 and avisos_dash[0].get("guion") and avisos_dash[2].get("video"), "el aviso del plan trae una fila por clip y el guion; el de revision, el video")
        comprobar((base / "informe_calidad.json").is_file() and (base / "evidencia.png").is_file() and (base / "final" / f"{PRODUCTO}_{AD}.mp4").is_file(), "deja informe_calidad.json, la hoja de cuadros y el video en final/")
        comprobar("persona debe mirar" in informe["recordatorio"], "el informe recuerda que una persona debe mirar la cara, la ropa, la voz y el producto")

        # --sin-render
        avisos_dash.clear()
        informe2 = AV.montar(ctx, args(sin_render=True), mostrar=salida.append)
        comprobar(informe2["estado"] == "sin-render" and informe2["final"] is None and [a["etapa"] for a in avisos_dash] == ["plan"], "--sin-render arma el plan y el videoData pero no renderiza (y solo avisa el plan)")

        # un clip que no dice su texto -> con-fallas
        led = AV.leer_ledger(ctx["trabajo"])
        led["clips"]["02"]["revision"] = {"ok": False, "detalle": "dice el 40 % del texto"}
        AV.guardar_ledger(ctx, led)
        informe3 = AV.montar(ctx, args(), mostrar=salida.append)
        comprobar(informe3["estado"] == "con-fallas" and informe3["compuertas"]["clips_dicen_su_texto"]["ok"] is False and "02" in informe3["compuertas"]["clips_dicen_su_texto"]["detalle"],
                  "un clip que no dice su texto deja el anuncio «con-fallas» y dice cual")
        # un cuadro con problemas
        led["clips"]["02"]["revision"] = {"ok": True}
        led["imagenes"]["03"]["revision"] = {"ok": False, "problemas": ["la ropa cambio"]}
        AV.guardar_ledger(ctx, led)
        informe4 = AV.montar(ctx, args(), mostrar=salida.append)
        comprobar(informe4["compuertas"]["cuadros_revisados"]["ok"] is False and informe4["estado"] == "con-fallas", "un cuadro que sigue con problemas deja «con-fallas» y dice cual")
        # historial
        hist = ANUNCIOS / PRODUCTO / "historial.json"
        informe5 = AV.montar(ctx, args(sin_historial=False), mostrar=salida.append)
        comprobar(informe5["estado"] == "con-fallas" and not hist.exists(), "un anuncio con fallas no se anota en el historial")
        led["imagenes"]["03"]["revision"] = {"ok": True}
        AV.guardar_ledger(ctx, led)
        informe6 = AV.montar(ctx, args(sin_historial=False), mostrar=salida.append)
        comprobar(informe6["estado"] == "aprobado" and hist.is_file() and AV.leer_suave(hist, {}), "uno aprobado si se anota en el historial", informe6["estado"])

        # faltan clips
        (ctx["clips"] / "03.mp4").unlink()
        (base / "base.mp4").unlink()
        c, msg = ejecutar(AV.montar, ctx, args())
        comprobar(c == 5 and "Faltan clips" in msg, "si faltan clips no monta: 5 y dice que corra generar", msg)

        # el validador encuentra un error: no se renderiza
        ctx = planeado(tmp, d)
        ejecutar(AV.generar, ctx, args(sin_revision_visual=True))
        validar = ctx["base"]  # noqa: F841
        guardado_run = subprocess.run
        def run_roto(cmd, *a, **k):
            if isinstance(cmd, list) and len(cmd) > 2 and str(cmd[1]).endswith("validar_videodata.py"):
                return subprocess.CompletedProcess(cmd, 1, "ERROR: algo esta mal en la toma 1\n", "")
            return guardado_run(cmd, *a, **k)
        subprocess.run = run_roto
        try:
            c, msg = ejecutar(AV.montar, ctx, args())
        finally:
            subprocess.run = guardado_run
        comprobar(c == 5 and "validador" in msg and not (ctx["base"] / "final" / f"{PRODUCTO}_{AD}.mp4").exists(), "si el validador encuentra errores, no se renderiza (5)", msg)
        informe = AV.leer_suave(ctx["base"] / "informe_calidad.json", {})
        comprobar(informe.get("estado") == "no-apto", "...y el informe dice «no-apto»")
    finally:
        for k, v in guardado.items():
            setattr(M, k, v)
        fabrica.reportar_progreso = guardado_f
        M.et.DetectorTexto.disponible = guardado_det
        os.environ.pop("DASH_URL", None)
        os.environ.pop("DASH_CLAVE_EDITOR", None)


# ------------------------------------------------------------------ 4b. lo que ya lleva el anuncio segun el dash
def dash_casos() -> None:
    """Desde la version 2026.11.10-2 las imagenes y los clips se piden por el dash, que ve lo de TODOS los equipos: el tope disponible y los clips pagados salen de ahi."""
    seccion("lo que ya lleva el anuncio segun el dash")
    guardado = A.ficha_del_dash
    A.COSTOS.unlink(missing_ok=True)
    try:
        A.ficha_del_dash = lambda producto, anuncio: {"gastado_usd": 0.45, "clips_pagados": 3, "repetidos": 0}
        receta = {"aprobacion": {"topeUsd": 0.30}}
        disponible, origen = AV.disponible_para(args(tope_usd=0.30), receta, AV.gastado_en_anuncio(PRODUCTO, AD, preguntar_dash=True))
        comprobar(abs(disponible - 0.05) < 1e-9 and "tope de US$ 0.50" in origen, "si el dash dice que el anuncio ya gasto US$ 0,45 (en otro equipo), solo quedan US$ 0,05 aunque aqui no haya nada", f"{disponible} {origen}")
        comprobar(AV.clips_pagados(PRODUCTO, AD) == 3, "los clips pagados salen del dash cuando este equipo no tiene ninguno anotado")
        A.ficha_del_dash = lambda producto, anuncio: None
        disponible, _ = AV.disponible_para(args(tope_usd=0.30), receta, AV.gastado_en_anuncio(PRODUCTO, AD, preguntar_dash=True))
        comprobar(abs(disponible - 0.30) < 1e-9 and AV.clips_pagados(PRODUCTO, AD) == 0, "si no se puede preguntar al dash, cuenta lo de este equipo")
    finally:
        A.ficha_del_dash = guardado


# ------------------------------------------------------------------ 5. la linea de comandos
def cli_casos(tmp: Path) -> None:
    seccion("linea de comandos")

    def correr(*argv: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        # con el APIMart falso (aunque no conteste) avatar_auto.py no le pregunta al dash de verdad cuanto lleva el anuncio, aunque esto corra en una instalacion con .env
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "APIMART_API_KEY": "", "APIMART_BASE_URL": "http://127.0.0.1:9/v1", **(env_extra or {})}
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "avatar_auto.py"), *argv], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=RAIZ, timeout=120)

    r = correr("estimar", "--dur-min", "25", "--dur-max", "60", "--tope-usd", "0.30")
    comprobar(r.returncode == 3 and "NO cabe" in r.stdout and "US$ 0.38" in r.stdout and "4 clips" in r.stdout, "estimar con la receta de fabrica y US$ 0,30: NO cabe, con las cifras (3)", r.stdout[-300:])
    r = correr("estimar", "--dur-min", "20", "--dur-max", "25", "--tope-usd", "0.30")
    comprobar(r.returncode == 0 and "Cabe: 3 clips" in r.stdout, "estimar con 20 a 25 s y US$ 0,30: cabe (0)", r.stdout[-300:])
    r = correr("estimar")
    comprobar(r.returncode == 0 and "10 ·" in r.stdout and "US$ 0.92" in r.stdout, "estimar sin tope muestra la tabla de clips y costos")
    armar_mundo(tmp)
    r = correr("planear", PRODUCTO, AD, "--dry-run")
    comprobar(r.returncode == 0 and "3 clips" in r.stdout and "no se pidio nada" in r.stdout, "planear --dry-run por la linea de comandos: costo y nada mas", r.stdout[-300:] + r.stderr[-200:])
    r = correr("planear", PRODUCTO, "AD99")
    comprobar(r.returncode == 1 and "ERROR" in r.stderr, "un anuncio raro: error (1) en stderr")
    armar_mundo(tmp, receta_extra={"aprobacion": {"plan": "automatico", "topeUsd": 0}})
    r = correr("planear", PRODUCTO, AD)
    comprobar(r.returncode == 3 and "Falta el OK" in r.stdout and "--tope-usd" in r.stdout, "sin OK de gasto: 3 y dice con que comando darlo", r.stdout[-300:])
    armar_mundo(tmp, con_guion=False)
    r = correr("planear", PRODUCTO, AD, "--tope-usd", "0.30")
    comprobar(r.returncode == 2 and "guion_auto.py" in r.stderr, "sin guion: 2 y el mensaje va por stderr")
    armar_mundo(tmp, receta_extra={"tipoAnuncio": "tiktok"})
    r = correr("generar", PRODUCTO, AD, "--tope-usd", "0.30")
    comprobar(r.returncode == 2 and "no es de avatar" in r.stderr, "un pedido tiktok no se hace por aqui (2)")
    comprobar("APIMART_API_KEY" not in (r.stdout + r.stderr), "ninguna salida nombra la llave")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="autoprueba_avatar_"))
    costos_antes = A.COSTOS
    A.COSTOS = tmp / "costos.jsonl"
    d = Doble(A.COSTOS)
    guardados = (G.correr_apimart, C.completar, AV.transcribir_clip)
    G.correr_apimart, C.completar, AV.transcribir_clip = d.apimart, d.completar, d.transcribir
    saldo = G.saldo_apimart
    G.saldo_apimart = lambda: None                      # el saldo de la cuenta de verdad no se toca
    ficha = A.ficha_del_dash
    A.ficha_del_dash = lambda producto, anuncio: None   # ni se le pregunta al dash de verdad cuanto lleva el anuncio
    try:
        puras()
        avatar_y_planear(tmp, d)
        generar_casos(tmp, d)
        montar_casos(tmp, d)
        dash_casos()
        G.correr_apimart, C.completar, AV.transcribir_clip = guardados
        cli_casos(tmp)
    finally:
        G.correr_apimart, C.completar, AV.transcribir_clip = guardados
        G.saldo_apimart = saldo
        A.ficha_del_dash = ficha
        A.COSTOS = costos_antes
        for carpeta in (PUBLIC / "productos" / PRODUCTO, ANUNCIOS / PRODUCTO):
            shutil.rmtree(carpeta, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON FALLAS: {len(FALLAS)} de {TOTAL}")
        for f in FALLAS:
            print(f"  - {f}")
        sys.exit(1)
    print(f"AUTOPRUEBA OK ({TOTAL} comprobaciones)")


if __name__ == "__main__":
    main()
