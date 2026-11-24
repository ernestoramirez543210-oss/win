"""Autoprueba de las variaciones hechas por el servidor (variacion_auto.py, originales.py, variaciones_vps.py y su paso en corredor_vps.py). Sin internet, sin dash, sin Drive y sin gastar:
el dash, Drive y las etapas son de mentira; la union y las compuertas se prueban con videos sinteticos de ffmpeg (sin Whisper).

  python scripts/autoprueba_variacion.py            todo (≈ 1-3 min: hace videos con ffmpeg)
  python scripts/autoprueba_variacion.py --puras    solo lo que no usa ffmpeg (segundos)
Sale con 0 si todo pasa y con 1 si algo falla (dice que)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import originales as OR  # noqa: E402
import variacion_auto as VA  # noqa: E402

FALLAS: list[str] = []
BIEN = 0


def ok(cond, que: str) -> None:
    global BIEN
    if cond:
        BIEN += 1
    else:
        FALLAS.append(que)
        print(f"  FALLA: {que}", flush=True)


def palabras(texto_tiempos: list[tuple[str, float, float]]) -> list[dict]:
    return [{"texto": t, "inicio": a, "fin": b} for t, a, b in texto_tiempos]


# Lo que oyo Whisper en la 101 de Sermones (medido el 2026-11-13 en el PC): la frase termina en «ti.» a 7,24 s; hay una pausa de 0,64 s en una COMA a 5,30 s.
P101 = palabras([("Si", 0.0, 0.16), ("alguna", 0.16, 0.38), ("vez", 0.38, 0.64), ("te", 0.64, 0.82), ("han", 0.82, 0.9), ("dado", 0.9, 1.08), ("la", 1.08, 1.18), ("oportunidad", 1.18, 1.64),
                 ("de", 1.64, 1.88), ("predicar", 1.88, 2.42), ("y", 2.42, 2.74), ("te", 2.74, 2.84), ("quedaste", 2.84, 3.22), ("bloqueado", 3.22, 3.84), ("sin", 3.84, 4.06), ("saber", 4.06, 4.38),
                 ("por", 4.38, 4.6), ("donde", 4.6, 4.88), ("empezar,", 4.88, 5.3), ("este", 5.94, 6.16), ("video", 6.16, 6.5), ("es", 6.5, 6.86), ("para", 6.86, 7.06), ("ti.", 7.06, 7.24),
                 ("Yo", 7.46, 7.48), ("sé", 7.48, 7.6), ("cómo", 7.6, 7.78), ("se", 7.78, 7.9), ("siente.", 7.9, 8.28), ("Amas", 8.52, 8.78)])
# La 105: la primera frase termina a 2,78 s y la segunda a 8,16 s (Beto cambio las dos en la 105.1).
P105 = palabras([("Hermano", 0.0, 0.5), ("predicador,", 0.5, 1.1), ("usted", 1.1, 1.4), ("no", 1.4, 1.5), ("va", 1.5, 1.7), ("a", 1.7, 1.8), ("creer", 1.8, 2.1), ("lo", 2.1, 2.2), ("que", 2.2, 2.3),
                 ("acabo", 2.3, 2.5), ("de", 2.5, 2.55), ("encuadernar.", 2.55, 2.78), ("Son", 3.12, 3.3), ("los", 3.3, 3.4), ("5", 3.4, 3.6), ("mil", 3.6, 3.8), ("bosquejos", 3.8, 4.4),
                 ("listos", 4.4, 5.0), ("y", 5.0, 5.1), ("organizados", 5.1, 6.0), ("por", 6.0, 6.2), ("temas.", 6.2, 8.16), ("Antes,", 8.3, 8.8), ("yo", 8.8, 9.0)])


def puras() -> None:
    print("-- donde termina la primera frase", flush=True)
    c = VA.elegir_corte(P101, 88.5)
    ok(c is not None and abs(c["corte"] - 7.333) < 0.05 and c["n_frases"] == 1 and not c["por_pausa"], f"101: corte tras «ti.» (~7,33 s), no en la coma de 5,3 s: {c}")
    ok(c and c["frase"].endswith("para ti."), "101: la frase original completa")
    ok(c and abs(c["corte"] * 30 - round(c["corte"] * 30)) < 0.05, "el corte cae en un cuadro (30 fps)")
    c = VA.elegir_corte(P105, 60)
    ok(c is not None and c["n_frases"] == 2 and 8.16 <= c["corte"] <= 8.3, f"105: la 1.ª frase termina antes de 4 s y se suma la 2.ª (como la 105.1 de Beto): {c}")
    c = VA.elegir_corte(P105, 60, minimo=2.0)
    ok(c is not None and c["n_frases"] == 1 and 2.78 <= c["corte"] <= 3.12, f"105 con mínimo 2 s: solo la 1.ª frase: {c}")
    sin_puntos = [{**w, "texto": w["texto"].rstrip(".,")} for w in P101]
    c = VA.elegir_corte(sin_puntos, 88.5)
    ok(c is not None and c["por_pausa"] and 5.3 <= c["corte"] <= 5.94, f"sin puntos de Whisper: la pausa más larga entre 4 y 10 s, marcada «por pausa»: {c}")
    ok(VA.elegir_corte([], 30) is None, "sin palabras: nada (lo decide una persona)")
    larga = palabras([(f"p{i}", i * 0.5, i * 0.5 + 0.45) for i in range(40)])
    ok(VA.elegir_corte(larga, 30) is None, "una frase sin punto ni pausa en 10 s: nada")
    import numpy as np

    hz = 16000
    audio = np.concatenate([np.ones(int(7.30 * hz), np.float32) * 0.3, np.zeros(int(0.05 * hz), np.float32), np.ones(int(2 * hz), np.float32) * 0.3])
    c = VA.elegir_corte(P101, 88.5, audio=audio, hz=hz)
    ok(c is not None and 7.29 <= c["corte"] <= 7.37, f"con el audio, el corte va al silencio real (7,30-7,35 s): {c}")
    print("-- el gancho", flush=True)
    an = {"frase": "frase vieja del original"}

    def falla(gancho: str) -> str:
        try:
            VA.frase_del_gancho({"gancho": gancho}, an, "fish")
            return ""
        except VA.ErrorVariacion as e:
            return e.mensaje

    ok(VA.frase_del_gancho({"gancho": "«Deja de pasar horas frente a la hoja en blanco»"}, an, "fish") == "Deja de pasar horas frente a la hoja en blanco", "el gancho sin comillas")
    ok("no trae gancho" in falla("hola"), "un gancho de 1 palabra no sirve")
    ok("palabras" in falla(" ".join(["palabra"] * 30)), "un gancho de 30 palabras no cabe en el inicio")
    ok("enlace" in falla("Entra ya a www.ejemplo.com para ver más"), "un gancho con enlace no sirve")
    ok("religión o la salud" in falla("¿Eres cristiano y no sabes qué predicar?"), "la regla de Meta de atributos personales")
    ok("religión o la salud" in falla("¿Te duele la rodilla al subir escaleras?"), "la regla de Meta (salud)")
    ok(VA.frase_del_gancho({"gancho": ""}, an, "original") == "frase vieja del original", "con la voz del original se dice la frase original")
    print("-- costo", flush=True)
    e = VA.estimar_costo("Deja de pasar horas frente a la hoja en blanco: tu próximo sermón ya está listo", "fish", 2)
    ok(0.025 < e["total"] < 0.035 and e["imagenes"] == 0.017, f"nuestra voz + 2 imágenes ≈ US$ 0,03: {e}")
    e = VA.estimar_costo("una frase corta de prueba", "clip", 2)
    ok(abs(e["clip"] - 0.0612) < 0.001 and e["total"] < 0.08, f"clip de 6 s del avatar ≈ US$ 0,07: {e}")
    e = VA.estimar_costo(" ".join(["p"] * 26), "clip", 2)
    ok(abs(e["clip"] - 0.0816) < 0.001, f"clip de 8 s si el diálogo es largo: {e}")
    ok(VA.estimar_costo("x y z", "original", 1)["total"] == round(VA.COSTO_TEXTO_MAX + VA.COSTO_IMAGEN, 4), "voz del original: solo tomas e imagen")
    ok(VA.duracion_clip(20) == 6 and VA.duracion_clip(27) == 8 and VA.duracion_clip(10) is None, "clip de 6 s = 19-22 palabras, de 8 s = 25-30")
    print("-- videoData del inicio", flush=True)
    vd = VA.armar_videodata({"anuncio": "101-7", "producto": "sermones-biblicos"}, {"duracion": 4.19, "texto_grande": "DEJA DE PASAR HORAS", "voz": "anuncios/_variaciones/x/gancho/voz_norm.wav",
                                                                                    "palabras": [{"texto": "Deja", "inicio": 0, "fin": 0.3}, {"texto": "tarde", "inicio": 9, "fin": 9.2}],
                                                                                    "tomas": [{"src": "a.mp4"}, {"src": "b.mp4"}]}, Path("."))
    ok(vd["meta"]["duracion"] == 4.2 and abs(sum(t["duracion"] for t in vd["tomas"]) - 4.2) < 1e-6, f"las tomas cubren el inicio entero (4,2 s, al cuadro): {vd['meta']}")
    ok(len(vd["palabras"]) == 1 and vd["zonaInferiorLibre"] == 280 and not vd.get("cta") and "musica" not in vd["audio"], "sin palabras fuera del inicio, zona libre, sin CTA ni música")
    ok(vd["textosGrandes"] and vd["textosGrandes"][0]["fin"] <= 2.6, "el texto grande solo al principio")
    ok(vd["estilo"]["subtitulos"] == VA.SUBTITULOS_NUESTROS, "sin medida de los subtítulos del original, los de siempre")
    print("-- subtítulos quemados del original (puro)", flush=True)
    ok(VA.fichas("¿Sé CÓMO, ti?") == ["se", "como", "ti"] and VA.parecida("versiculos", "versículos".replace("í", "i")) and VA.parecida("versiculos", "versicuIos")
       and not VA.parecida("ti", "te"), "palabras sin tildes ni signos; casi iguales solo las de 4 letras o más")
    viejas, nuevas = ["este", "video", "es", "para", "ti"], ["yo", "se", "como", "se", "siente"]
    ok(VA.palabras_viejas_al_inicio(["ti", "yo", "se", "como"], viejas, nuevas) == 1, "«ti yo sé como» después del corte: 1 palabra de la frase vieja (la 101 de Sermones)")
    ok(VA.palabras_viejas_al_inicio(["para", "ti"], viejas, nuevas) == 2, "un subtítulo que es todo de la frase vieja: se borra entero")
    ok(VA.palabras_viejas_al_inicio(["yo", "se", "como"], viejas, nuevas) == 0, "un subtítulo que ya es de la frase nueva: nada que borrar")
    ok(VA.palabras_viejas_al_inicio(["yo", "se"], ["dije", "yo"], ["yo", "se"]) == 0, "si la palabra es la misma al final de la vieja y al inicio de la nueva, no se borra (ambiguo)")
    oidas = palabras([("este", 6.5, 6.7), ("video", 6.7, 6.9), ("es", 6.9, 7.0), ("para", 7.0, 7.1), ("ti.", 7.1, 7.24), ("Yo", 7.42, 7.5)])
    cajas = [{"texto": "+", "conf": 0.9, "x0": 600, "x1": 800, "y0": 100, "y1": 270}, {"texto": "MAS DE", "conf": 0.95, "x0": 790, "x1": 930, "y0": 520, "y1": 600},
             {"texto": "este video es para", "conf": 0.98, "x0": 254, "x1": 832, "y0": 995, "y1": 1059}]
    ok(VA.caja_del_subtitulo(cajas, oidas, 6.9)["texto"] == "este video es para", "el subtítulo es la línea que dice lo que se oye (no la cruz ni un letrero)")
    ok(VA.caja_del_subtitulo(cajas[:2], oidas, 6.9) is None, "sin una línea que diga lo que se oye, no hay subtítulo")
    muestras = [{"texto": t, "y0": 993, "y1": 1060, "color": "#FFFFFF"} for t in ("si alguna vez te", "este video es para", "tienes versiculos anotados", "todo queda suelto")]
    e = VA.estilo_de_muestras(muestras)
    ok(not e["mayusculas"] and e["color"] == e["colorActivo"] == "#FFFFFF" and e["maxPalabras"] in (3, 4) and 45 <= e["tamano"] <= 60
       and e["margenInferior"] == 1920 - 1060 - 280, f"estilo medido: minúsculas, blanco, sin resalte de color, misma altura: {e}")
    vd2 = VA.armar_videodata({"anuncio": "x"}, {"duracion": 3.0, "voz": "v.wav", "palabras": [], "tomas": [{"src": "a.mp4"}]}, Path("."), e)
    ok(vd2["estilo"]["subtitulos"] == e, "el inicio nuevo usa el estilo medido")
    bien = {"region": [317, 979, 65, 95], "hasta": 0.5}
    ok(VA.filtro_borrar_resto(bien) == ",delogo=x=317:y=979:w=65:h=95:enable='lt(t,0.500)'", f"el borrado va solo en ese rectángulo y ese rato: {VA.filtro_borrar_resto(bien)}")
    for malo, que in (({"region": [0, 979, 65, 95], "hasta": 0.5}, "pegado al borde"), ({"region": [317, 979, 65, 95], "hasta": 9}, "demasiado largo"),
                      ({"region": [317, 979, "65;rm", 95], "hasta": 0.5}, "con texto raro"), ({"region": [1050, 979, 65, 95], "hasta": 0.5}, "fuera del cuadro"), (None, "sin dato")):
        ok(VA.filtro_borrar_resto(malo) == "", f"un borrado {que} no entra al filtro de ffmpeg")
    print("-- lo pagado va atado a su gancho", flush=True)
    h = VA.huella_del_gancho("fish", "Gancho A", 2, False)
    ok(len({h, VA.huella_del_gancho("fish", "Gancho B", 2, False), VA.huella_del_gancho("clip", "Gancho A", 2, False), VA.huella_del_gancho("fish", "Gancho A", 1, False),
            VA.huella_del_gancho("fish", "Gancho A", 2, True), VA.huella_del_gancho("fish", "Gancho A", 2, False, nota="otra nota"),
            VA.huella_del_gancho("fish", "Gancho A", 2, False, original="otro-id")}) == 7 and h == VA.huella_del_gancho("fish", "Gancho A", 2, False),
       "otro gancho, nota, original, modo, número de imágenes o pasar del ensayo a la real cambian la huella (lo hecho no se reusa); el mismo, no")
    tmp_g = Path(tempfile.mkdtemp(prefix="_gancho_")) / "gancho"
    try:
        (tmp_g / "_anteriores").mkdir(parents=True)
        for n in ("01.png", "clip_abc.mp4", "estado.json", "plan.json"):
            (tmp_g / n).write_text("x", encoding="utf-8")
        dest = VA.archivar_gancho(tmp_g, "abc123")
        ok(sorted(x.name for x in tmp_g.iterdir()) == ["_anteriores"] and sorted(x.name for x in dest.iterdir()) == ["01.png", "clip_abc.mp4", "estado.json", "plan.json"]
           and VA.archivar_gancho(tmp_g, "abc123").name != dest.name, "lo del gancho viejo queda aparte en _anteriores/<huella> (no se borra) y otro apartado no lo pisa")
    finally:
        shutil.rmtree(tmp_g.parent, ignore_errors=True)
    print("-- pedidos del dash", flush=True)
    bueno = {"id": "mhx1abc12", "oferta": "Sermones Biblicos", "numero": "101.7", "base": "101", "gancho": "g", "original": {"id": "A" * 20, "nombre": "5000 sermones 101 .mp4"},
             "archivo": "5000 sermones 101.7.mp4"}
    ok(OR.pedido_limpio(bueno) is not None, "un pedido bueno pasa")
    for k, v, que in (("id", "../x", "id raro"), ("numero", "201.1", "número que no es de 101 a 199"), ("numero", "101", "número sin punto (no es variación)"),
                      ("archivo", "../../etc/x.mp4", "archivo con ruta"), ("archivo", "a/b.mp4", "archivo con barra"), ("archivo", ".oculto.mp4", "archivo oculto"),
                      ("archivo", "x.mov", "archivo que no es mp4")):
        ok(OR.pedido_limpio({**bueno, k: v}) is None, f"pedido rechazado: {que}")
    ok(OR.pedido_limpio({**bueno, "original": {"id": "corto"}}) is None, "pedido rechazado: id de Drive raro")
    ok(OR.slug_de("Sermones Bíblicos") == "sermones-biblicos" and OR.id_de_costos("101.2.1") == "101-2-1", "nombres para el costo por proyecto")
    print("-- índice de originales (Drive de mentira)", flush=True)
    drive = DriveFalso()
    ind = OR.recorrer(drive, RAIZ_FALSA, registrar=lambda *_: None)
    nombres = sorted(a["nombre"] for a in ind["archivos"])
    ok(nombres == ["5000 sermones 101 .mp4", "FIELTRO 101.mp4", "KINESIOTAPING 105 .mp4", "muy hondo 103.mp4"], f"solo los videos, sin B-rolls ni REFERENCIA ni la papelera: {nombres}")
    ok({r["nombre"] for r in ind["raices"]} == {"BRIMIREZZ", "FIELTRO"}, "las carpetas de primer nivel")
    a = next(x for x in ind["archivos"] if x["nombre"].startswith("5000"))
    ok(a["raiz"] == "BRIMIREZZ" and a["carpeta"] == "5000 sermones 7 oct" and a["fecha"] == "2026-10-08" and a["mb"] == 129.2, f"datos de cada video: {a}")
    ok(not any("hondisimo" in x["nombre"] for x in ind["archivos"]), "no baja más de 3 niveles")
    ok(drive.escrituras == 0, "el índice solo lee Drive")
    ok(OR.ancestros(drive, "V101" + "x" * 16, RAIZ_FALSA) == [{"id": "S7" + "x" * 18, "nombre": "5000 sermones 7 oct"}, {"id": "B" * 20, "nombre": "BRIMIREZZ"}], "la cadena hasta OFERTAS LATAM")
    ok(OR.ancestros(drive, "FUERA" + "x" * 15, RAIZ_FALSA) is None, "un archivo fuera de OFERTAS LATAM no se acepta")


def _raiz_real() -> str:
    """El id de OFERTAS LATAM que trae corredor.json del repo (el Drive de mentira usa ese mismo id como raiz)."""
    import drive_api as da

    cfg = json.loads((Path(__file__).resolve().parents[1] / "vps" / "corredor.json").read_text(encoding="utf-8"))
    return da.id_de_carpeta(cfg["carpeta_ofertas"])


RAIZ_FALSA = _raiz_real()


class DriveFalso:
    """OFERTAS LATAM de mentira: BRIMIREZZ/{5000 sermones 7 oct, 12 agosto- KINESIOTAPING, OK/1 julio/x/y}, FIELTRO/{ANUNCIOS, B-rolls, REFERENCIA}; y un archivo FUERA."""

    def __init__(self):
        C = "application/vnd.google-apps.folder"
        x = lambda s: s + "x" * (20 - len(s))  # noqa: E731
        R = RAIZ_FALSA
        self.f = {
            R: {"name": "OFERTAS LATAM", "parents": []},
            "B" * 20: {"name": "BRIMIREZZ", "mimeType": C, "parents": [R]},
            x("S7"): {"name": "5000 sermones 7 oct", "mimeType": C, "parents": ["B" * 20]},
            x("V101"): {"name": "5000 sermones 101 .mp4", "size": "135500000", "createdTime": "2026-10-08T14:00:00Z", "parents": [x("S7")]},
            x("DESK"): {"name": "desktop.ini", "size": "352", "parents": [x("S7")]},
            x("K12"): {"name": "12 agosto- KINESIOTAPING", "mimeType": C, "parents": ["B" * 20]},
            x("K105"): {"name": "KINESIOTAPING 105 .mp4", "size": "51303338", "createdTime": "2026-08-12T12:00:00Z", "parents": [x("K12")]},
            x("BORR"): {"name": "KINESIOTAPING 999 .mp4", "size": "10", "trashed": True, "parents": [x("K12")]},
            x("OK"): {"name": "OK", "mimeType": C, "parents": ["B" * 20]},
            x("J1"): {"name": "1 julio", "mimeType": C, "parents": [x("OK")]},
            x("H1"): {"name": "hondo", "mimeType": C, "parents": [x("J1")]},
            x("H1V"): {"name": "muy hondo 103.mp4", "size": "1000000", "parents": [x("H1")]},
            x("H2"): {"name": "mas hondo", "mimeType": C, "parents": [x("H1")]},
            x("H2V"): {"name": "hondisimo 104.mp4", "size": "1000000", "parents": [x("H2")]},
            "F" * 20: {"name": "FIELTRO", "mimeType": C, "parents": [R]},
            x("FA"): {"name": "ANUNCIOS", "mimeType": C, "parents": ["F" * 20]},
            x("FAV"): {"name": "FIELTRO 101.mp4", "size": "20000000", "parents": [x("FA")]},
            x("FB"): {"name": "B-rolls", "mimeType": C, "parents": ["F" * 20]},
            x("FBV"): {"name": "tt_7234567890123.mp4", "size": "2000000", "parents": [x("FB")]},
            x("FR"): {"name": "REFERENCIA", "mimeType": C, "parents": ["F" * 20]},
            x("FRV"): {"name": "REF1_referencia.mp4", "size": "2000000", "parents": [x("FR")]},
            x("FUERA"): {"name": "privado 101.mp4", "size": "100", "parents": [x("OTRA")]},
            x("FALSO"): {"name": "sermones 101.mp4", "size": "100", "mimeType": "application/vnd.apple.mpegurl", "parents": [x("S7")]},   # una lista HLS con nombre de mp4
            x("OTRA"): {"name": "Otra carpeta", "mimeType": C, "parents": [x("RAIZDRIVE")]},
            x("RAIZDRIVE"): {"name": "Mi unidad", "mimeType": C, "parents": []},
        }
        self.escrituras = 0
        self.cercado: set[str] = set()

    def listar_hijos(self, cid):
        return [{"id": k, **v} for k, v in self.f.items() if cid in (v.get("parents") or []) and not v.get("trashed")] + \
               [{"id": k, **v} for k, v in self.f.items() if cid in (v.get("parents") or []) and v.get("trashed")][:0]

    def datos_de(self, fid):
        v = self.f.get(fid)
        if v is None:
            raise OR.ErrorDashVar("no existe")
        import drive_api as da

        d = {"id": fid, **v}
        if "mimeType" not in d and OR.es_video(d["name"]):
            d["mimeType"] = "video/mp4"           # como lo da Drive para un mp4
        campos = set(da.CAMPOS_DATOS.split(","))
        return {k: x for k, x in d.items() if k in campos}      # como Drive v3: solo los campos que pide datos_de (sin mimeType ahi, ningun original pasaria)


# ------------------------------------------------------------------ el paso del corredor (dash, Drive y etapas de mentira)
class DashVarFalso:
    """El dash de mentira. Como el de verdad (functions/api/variacion-editor.js), rechaza con 409 el aviso de otro intento; `rechazar` = {'tomado'} o {'en-curso:render'}
    simula que la fila ya no esta por producir o que el dueño la volvio a pedir a mitad de camino."""

    def __init__(self, pendientes=None, edicion_estado="tomado", rechazar=()):
        self.pend = list(pendientes or [])
        self.avances: list[tuple[str, str, dict]] = []
        self.indices: list[dict] = []
        self.edicion_estado = edicion_estado
        self.rechazar = set(rechazar)

    def __call__(self, metodo, sufijo, cuerpo):
        if metodo == "GET" and sufijo == "?pendientes=1":
            return {"ok": True, "pedidos": self.pend}
        if metodo == "GET" and sufijo.startswith("?id="):
            vid = sufijo[4:]
            p = next((x for x in self.pend if x["id"] == vid), None)
            return {"ok": True, "pedido": p, "edicion": {"estado": self.edicion_estado}}
        if metodo == "POST" and cuerpo.get("accion") == "avance":
            p = next((x for x in self.pend if x["id"] == cuerpo["id"]), {})
            if "intento" in cuerpo and int(cuerpo["intento"]) != int(p.get("intento") or 0):
                raise OR.ErrorDashVar("el dash contesto 409: Ese aviso es de otro intento", codigo=409)
            if cuerpo["estado"] in self.rechazar or f"{cuerpo['estado']}:{cuerpo.get('etapa')}" in self.rechazar:
                raise OR.ErrorDashVar("el dash contesto 409: la variación ya no está por producir", codigo=409)
            self.avances.append((cuerpo["id"], cuerpo["estado"], cuerpo))
            return {"ok": True}
        if metodo == "POST" and cuerpo.get("accion") == "indice":
            self.indices.append(cuerpo)
            return {"ok": True, "total": len(cuerpo["archivos"])}
        raise AssertionError((metodo, sufijo, cuerpo))


class EspejoFalso:
    def __init__(self):
        self.subidas = []
        self.bajadas = []

    contenido = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00video original"      # empieza como un MP4 de verdad (caja ftyp)

    def _bajar_archivo(self, f, destino, plazo):
        Path(destino).parent.mkdir(parents=True, exist_ok=True)
        Path(destino).write_bytes(self.contenido)
        self.bajadas.append(f["id"])
        return "bajado"

    def subir_anuncio(self, oferta, archivo, *, mime="video/mp4", carpeta="ANUNCIOS"):
        self.subidas.append((oferta["id"], Path(archivo).name, carpeta))
        return {"id": "D" * 25, "name": Path(archivo).name, "size": Path(archivo).stat().st_size, "enlace": "https://drive.google.com/file/d/" + "D" * 25 + "/view", "carpeta": "x", "estado": "subido"}


def correr_falso(registro: list, raiz: Path, fallar_en: str | None = None, costo_gancho: float = 0.031):
    import apimart as A

    def correr(cmd, env, timeout_s, salida, cortar=lambda: False, cwd=None):
        import corredor_vps as CV

        sub, carpeta = cmd[3], Path(cmd[4])
        registro.append((sub, "DASH_CLAVE_EDITOR" in env))
        if fallar_en == sub:
            return CV.Corrida(1, ultimas=["algo", "ERROR: el gancho tiene 30 palabras y para el inicio caben 24 (unos 7 segundos): acórtelo"])
        p = json.loads((carpeta / "pedido.json").read_text(encoding="utf-8"))
        if sub == "analizar":
            (carpeta / "analisis.json").write_text(json.dumps({"corte": 7.333}), encoding="utf-8")
        elif sub == "gancho":
            (carpeta / "gancho").mkdir(exist_ok=True)
            (carpeta / "gancho" / "gancho.json").write_text("{}", encoding="utf-8")
            with open(A.COSTOS, "a", encoding="utf-8") as f:
                f.write(json.dumps({"tipo": "imagen", "costo": costo_gancho, "producto": p["producto"], "anuncio": p["anuncio"]}) + "\n")
        elif sub == "render":
            (carpeta / "gancho_render.mp4").write_bytes(b"x")
        elif sub == "unir":
            (carpeta / "final").mkdir(exist_ok=True)
            (carpeta / "final" / p["archivo"]).write_bytes(b"video final")
            (carpeta / "union.json").write_text(json.dumps({"archivo": p["archivo"], "corte": 7.333, "inicio_seg": 4.2}), encoding="utf-8")
        elif sub == "revisar":
            (carpeta / "revision.json").write_text(json.dumps({"ok": True, "compuertas": {"formato": {"ok": True}}}), encoding="utf-8")
        return CV.Corrida(0)

    return correr


def armar_corredor(raiz: Path, dash_var: DashVarFalso, registro: list, cfg_var: dict | None = None, args: dict | None = None, fallar_en: str | None = None, drive=None):
    import corredor_vps as CV

    cfg = CV.cargar_config(Path(__file__).resolve().parents[1] / "vps" / "corredor.json")
    cfg["variaciones"].update(cfg_var or {})
    a = SimpleNamespace(simular=False, solo_cola=False, max_pedidos=None, tope_usd=None, sin_gancho=False, pedido=None, rehacer=False, indice=False, variacion=None, sin_variaciones=False)
    for k, v in (args or {}).items():
        setattr(a, k, v)
    lineas: list[str] = []
    deps = CV.Dependencias(llamar_dash=lambda m, r, c=None, **k: {"ok": True, "pedidos": []}, correr=correr_falso(registro, raiz, fallar_en), recursos=lambda: {"ram_gb": 7, "carga5": 0.1, "disco_gb": 60},
                           en_pausa=lambda: False, ahora=lambda: datetime(2026, 11, 13, 15, 0, 0))
    deps.llamar_variaciones = dash_var
    co = CV.Corredor(cfg, a, deps, CV.Registro(emitir=lineas.append), raiz=raiz)
    co._drive = drive or DriveFalso()
    co._espejo = EspejoFalso()
    return co, lineas


def pedido_bueno(original_id: str = "V101" + "x" * 16) -> dict:
    return {"id": "mhx1abc12", "oferta": "Sermones Biblicos", "nombre": "101.7 · Idea: hoja en blanco", "numero": "101.7", "base": "101", "gancho": "Deja de pasar horas frente a la hoja en blanco",
            "nota": "", "cambio": "inicio", "referencia": "", "paises": ["colombia"], "original": {"id": original_id, "nombre": "5000 sermones 101 .mp4", "carpeta": "5000 sermones 7 oct", "raiz": "BRIMIREZZ"},
            "archivo": "5000 sermones 101.7.mp4", "intento": 2}


def paso_corredor() -> None:
    import apimart as A

    print("-- el paso de las variaciones en el corredor", flush=True)
    base = Path(tempfile.mkdtemp(prefix="_autoprueba_variacion_"))
    costos_antes = A.COSTOS
    llave_antes = os.environ.get("DASH_CLAVE_EDITOR")
    os.environ["DASH_CLAVE_EDITOR"] = "llave-de-mentira-0123456789"          # para ver que SOLO la etapa del gancho la recibe
    try:
        A.COSTOS = base / "costos.jsonl"
        # 1) apagadas: arma el indice, no toma pedidos
        raiz = base / "r1"
        dv = DashVarFalso([pedido_bueno()])
        reg: list = []
        co, lineas = armar_corredor(raiz, dv, reg)
        co.paso_variaciones()
        ok(len(dv.indices) == 1 and dv.indices[0]["servidor"]["activo"] is False and len(dv.indices[0]["archivos"]) == 4, "apagadas: el índice se arma y el dash sabe que están apagadas")
        ok(not dv.avances and not reg, "apagadas: no toma el pedido ni corre ninguna etapa")
        ok(any("APAGADAS" in ln for ln in lineas), "apagadas: lo dice en el registro")
        co2, _ = armar_corredor(raiz, dv, reg)
        co2.paso_variaciones()
        ok(len(dv.indices) == 1, "el índice no se repite antes de indice_horas")
        # 2) encendidas: toma, corre las etapas en orden, sube a Drive y avisa «lista»
        raiz = base / "r2"
        dv = DashVarFalso([pedido_bueno()])
        reg = []
        co, lineas = armar_corredor(raiz, dv, reg, {"activo": True, "tope_usd": 0.10})
        co.paso_variaciones()
        estados = [e for _i, e, _c in dv.avances]
        ok(estados[0] == "tomado" and estados[-1] == "lista" and estados.count("en-curso") == 4, f"encendidas: tomado, 4 avisos de etapa (no 7: cada aviso reescribe la fila) y lista: {estados}")
        ok(all(c.get("intento") == 2 for _i, _e, c in dv.avances), "cada aviso al dash lleva el número de intento del pedido")
        ok([s for s, _k in reg] == ["analizar", "gancho", "render", "unir", "revisar"], f"las etapas de variacion_auto.py en orden: {reg}")
        ok(all(k == (s == "gancho") for s, k in reg), f"solo la etapa del gancho ve la llave del dash: {reg}")
        ok(co._espejo.bajadas == ["V101" + "x" * 16], "bajó el original de Drive")
        ok(co._espejo.subidas == [("B" * 20, "5000 sermones 101.7.mp4", "VARIACIONES DEL SERVIDOR")], f"lo subió a BRIMIREZZ › VARIACIONES DEL SERVIDOR: {co._espejo.subidas}")
        lista = dv.avances[-1][2]
        ok(lista.get("driveId") == "D" * 25 and lista.get("archivo") == "5000 sermones 101.7.mp4" and abs(float(lista.get("costoUsd", 0)) - 0.031) < 1e-6, f"«lista» con el id, el archivo y el costo: {lista}")
        ok(lista.get("marcarLista") is False, "con revision_beto, no la marca Lista (la revisa Beto)")
        ok(abs(co.gasto_hoy() - 0.031) < 1e-6, f"el gasto cuenta en el tope del día del corredor: {co.gasto_hoy()}")
        st = json.loads((raiz / "estado" / "variaciones" / "mhx1abc12.json").read_text(encoding="utf-8"))
        ok(st["estado"] == "entregada" and st["raiz"]["nombre"] == "BRIMIREZZ", "queda anotada como entregada")
        pj = json.loads((raiz / "anuncios" / "_variaciones" / "mhx1abc12" / "pedido.json").read_text(encoding="utf-8"))
        ok(pj["producto"] == "sermones-biblicos" and pj["anuncio"] == "101-7", "el costo va a sermones-biblicos / 101-7 en «Costo por proyecto»")
        # 3) sin revision de Beto: la deja Lista
        raiz = base / "r3"
        dv = DashVarFalso([pedido_bueno()])
        co, _ = armar_corredor(raiz, dv, [], {"activo": True, "tope_usd": 0.10, "revision_beto": False})
        co.paso_variaciones()
        ok(dv.avances[-1][2].get("marcarLista") is True, "sin revision_beto: el servidor la deja Lista")
        # 4) un original FUERA de OFERTAS LATAM: error, no baja nada
        raiz = base / "r4"
        dv = DashVarFalso([pedido_bueno("FUERA" + "x" * 15)])
        reg = []
        co, _ = armar_corredor(raiz, dv, reg, {"activo": True, "tope_usd": 0.10})
        co.paso_variaciones()
        ok(dv.avances[-1][1] == "error" and "OFERTAS LATAM" in dv.avances[-1][2].get("nota", ""), f"original fuera de OFERTAS LATAM: error al dash: {dv.avances[-1]}")
        ok(not co._espejo.bajadas and not reg, "no se bajó nada ni se corrió ninguna etapa")
        # 5) una etapa falla: el motivo (la linea ERROR) llega al dash
        raiz = base / "r5"
        dv = DashVarFalso([pedido_bueno()])
        co, _ = armar_corredor(raiz, dv, [], {"activo": True, "tope_usd": 0.10}, fallar_en="gancho")
        co.paso_variaciones()
        ok(dv.avances[-1][1] == "error" and "30 palabras" in dv.avances[-1][2].get("nota", ""), f"la falla de una etapa llega al dash con su motivo: {dv.avances[-1]}")
        ok(not co._espejo.subidas, "con una falla no se sube nada")
        # 6) cambia el cierre: no la hace
        raiz = base / "r6"
        dv = DashVarFalso([{**pedido_bueno(), "cambio": "inicio-cierre"}])
        reg = []
        co, _ = armar_corredor(raiz, dv, reg, {"activo": True, "tope_usd": 0.10})
        co.paso_variaciones()
        ok(dv.avances and dv.avances[-1][1] == "error" and "Beto" in dv.avances[-1][2].get("nota", "") and not reg, "una variación que cambia el cierre: la hace Beto (no se toca)")
        # 7) simular: no toma ni escribe
        raiz = base / "r7"
        dv = DashVarFalso([pedido_bueno()])
        reg = []
        co, lineas = armar_corredor(raiz, dv, reg, {"activo": True, "tope_usd": 0.10}, {"simular": True})
        co.paso_variaciones()
        ok(not dv.avances and not reg and not dv.indices, "simular: no toma, no corre etapas ni manda el índice")
        # 8) --variacion ID --tope-usd X: el OK del dueño para esa, aunque esten apagadas
        raiz = base / "r8"
        otros = [{**pedido_bueno(), "id": f"otra{k}abc12"} for k in range(3)]
        dv = DashVarFalso(otros + [pedido_bueno()], edicion_estado="pendiente")
        co, _ = armar_corredor(raiz, dv, [], {"activo": False}, {"variacion": "mhx1abc12", "tope_usd": 0.12})
        (raiz / "estado" / "variaciones").mkdir(parents=True, exist_ok=True)       # otra variación quedó a medias en el servidor: la orden a mano NO sigue con esa
        (raiz / "estado" / "variaciones" / "otra0abc12.json").write_text(json.dumps({"id": "otra0abc12", "estado": "en-curso", "etapas": {}}), encoding="utf-8")
        co.paso_variaciones()
        ok(dv.avances and dv.avances[-1][1] == "lista" and {i for i, _e, _c in dv.avances} == {"mhx1abc12"},
           "con --variacion y --tope-usd la hace aunque estén apagadas (aunque haya otras pedidas antes u otra a medias) y no toca ninguna otra")
        dv = DashVarFalso([pedido_bueno()], edicion_estado="tomado")
        co, lineas = armar_corredor(base / "r8b", dv, [], {"activo": False}, {"variacion": "mhx1abc12", "tope_usd": 0.12})
        co.paso_variaciones()
        ok(not dv.avances and any("no está pedida" in ln for ln in lineas), "con --variacion de una que no está pendiente en el dash: no hace nada")
        dv = DashVarFalso([pedido_bueno()])
        co, _ = armar_corredor(base / "r9", dv, [], {"activo": False}, {"variacion": "mhx1abc12"})
        co.paso_variaciones()
        ok(not dv.avances, "con --variacion SIN --tope-usd y apagadas: no la hace")
        # 8b) el dash contesta 409: al tomarla (ya no esta por producir) se salta; a mitad de camino (el dueño la volvio a pedir) se deja sin subir nada
        dv = DashVarFalso([pedido_bueno()], rechazar={"tomado"})
        reg = []
        co, _ = armar_corredor(base / "r10", dv, reg, {"activo": True, "tope_usd": 0.10})
        co.paso_variaciones()
        ok(co.resultados[-1] == {"variaciones": "nada"} and not reg and not co._espejo.bajadas, f"409 al tomarla: la salta sin bajar ni correr nada: {co.resultados}")
        dv = DashVarFalso([pedido_bueno()], rechazar={"en-curso:render"})
        reg = []
        co, _ = armar_corredor(base / "r11", dv, reg, {"activo": True, "tope_usd": 0.10})
        co.paso_variaciones()
        ok(co.resultados[-1] == {"variaciones": "nada"} and [s for s, _k in reg] == ["analizar", "gancho"] and not co._espejo.subidas,
           f"409 a mitad de camino: no sigue ni sube nada: {reg}")
        st = json.loads((base / "r11" / "estado" / "variaciones" / "mhx1abc12.json").read_text(encoding="utf-8"))
        ok(st["estado"] == "abandonada", "y queda anotada como abandonada (no se retoma)")
        # 8c) modo clip: solo con la oferta autorizada en clip_ofertas
        dv = DashVarFalso([pedido_bueno()])
        reg = []
        co, _ = armar_corredor(base / "r12", dv, reg, {"activo": True, "tope_usd": 0.10, "modo": "clip"})
        co.paso_variaciones()
        ok(dv.avances and dv.avances[-1][1] == "error" and "clip_ofertas" in dv.avances[-1][2].get("nota", "") and not reg and not co._espejo.bajadas,
           f"modo clip con una oferta no autorizada: no la toma, error al dash y no corre nada: {dv.avances[-1:]}")
        dv = DashVarFalso([pedido_bueno()])
        reg = []
        co, _ = armar_corredor(base / "r13", dv, reg, {"activo": True, "tope_usd": 0.10, "modo": "clip", "clip_ofertas": ["sermones-biblicos"]})
        co.paso_variaciones()
        ok(dv.avances and dv.avances[-1][1] == "lista", f"modo clip con la oferta en clip_ofertas: la hace: {dv.avances[-1:]}")
        # 8d) un original que es una lista HLS con nombre de mp4, o que está en REFERENCIA: no se baja
        for vid_id, que in (("FALSO" + "x" * 15, "una lista HLS con nombre de mp4"), ("FRV" + "x" * 17, "un video de REFERENCIA (de otro anunciante)")):
            dv = DashVarFalso([pedido_bueno(vid_id)])
            reg = []
            co, _ = armar_corredor(base / f"r14{vid_id[:3]}", dv, reg, {"activo": True, "tope_usd": 0.10})
            co.paso_variaciones()
            ok(dv.avances and dv.avances[-1][1] == "error" and not co._espejo.bajadas and not reg, f"{que}: error, no se baja ni se corre nada: {dv.avances[-1:]}")
        # 8e) lo que bajó de Drive no es un MP4 de verdad: se borra y no se corre nada
        dv = DashVarFalso([pedido_bueno()])
        reg = []
        co, _ = armar_corredor(base / "r15", dv, reg, {"activo": True, "tope_usd": 0.10})
        co._espejo.contenido = b"#EXTM3U\n#EXT-X-VERSION:3\nfile:///etc/passwd\n"
        co.paso_variaciones()
        ok(dv.avances and dv.avances[-1][1] == "error" and "MP4" in dv.avances[-1][2].get("nota", "") and not reg
           and not (base / "r15" / "anuncios" / "_variaciones" / "mhx1abc12" / "original.mp4").exists(), f"una lista HLS con nombre de mp4: se borra, error y no se corre nada: {dv.avances[-1:]}")
        ok(OR.es_mp4_de_verdad.__doc__ and not OR.es_mp4_de_verdad(base / "no-existe.mp4"), "un archivo que no existe no es un MP4")
        ok(OR.pedido_limpio({**pedido_bueno(), "intento": "x"})["intento"] == 0 and OR.pedido_limpio({**pedido_bueno(), "intento": 99999})["intento"] == 0,
           "un intento raro del dash vale 0")
        # 9) configuracion mala
        import corredor_vps as CV

        malo = json.loads((Path(__file__).resolve().parents[1] / "vps" / "corredor.json").read_text(encoding="utf-8"))
        malo["variaciones"] = {**malo["variaciones"], "tope_usd": 3, "modo": "otro", "carpeta": "../x"}
        ruta = base / "malo.json"
        ruta.write_text(json.dumps(malo), encoding="utf-8")
        try:
            CV.cargar_config(ruta)
            ok(False, "corredor.json con variaciones malas debe fallar")
        except CV.ErrorConfig as e:
            ok("tope_usd" in str(e) and "modo" in str(e) and "carpeta" in str(e), f"corredor.json: tope, modo y carpeta malos se rechazan: {str(e)[:200]}")
        cfg = CV.cargar_config(Path(__file__).resolve().parents[1] / "vps" / "corredor.json")
        ok(cfg["variaciones"]["activo"] is False and cfg["variaciones"]["tope_usd"] == 0, "corredor.json del repo trae las variaciones APAGADAS y sin tope")
        ok(CV.LLAVES_POR_ETAPA["var_gancho"] == ("dash",) and all(CV.LLAVES_POR_ETAPA[e] == () for e in ("var_original", "var_analisis", "var_render", "var_unir", "var_revisar", "var_entrega")),
           "solo var_gancho recibe la llave del dash")
    finally:
        A.COSTOS = costos_antes
        if llave_antes is None:
            os.environ.pop("DASH_CLAVE_EDITOR", None)
        else:
            os.environ["DASH_CLAVE_EDITOR"] = llave_antes
        shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------------ union y compuertas con videos sinteticos (ffmpeg, sin Whisper)
def con_ffmpeg() -> None:
    print("-- unir y comparar con videos sintéticos (ffmpeg)", flush=True)
    base = VA.ANUNCIOS / "_variaciones" / f"autoprueba-{os.getpid()}"
    try:
        base.mkdir(parents=True, exist_ok=True)

        def hacer(destino: Path, dur: float, fuente: str, tono: int) -> None:
            r = subprocess.run([VA.ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"{fuente}=size=1080x1920:rate=30:duration={dur}", "-f", "lavfi", "-i",
                                f"sine=frequency={tono}:duration={dur}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(destino)], capture_output=True)
            assert r.returncode == 0, r.stderr[-300:]

        hacer(base / "original.mp4", 14.0, "testsrc2", 440)
        hacer(base / "gancho_render.mp4", 3.0, "smptebars", 660)
        (base / "pedido.json").write_text(json.dumps({"numero": "101.7", "archivo": "prueba 101.7.mp4"}), encoding="utf-8")
        (base / "analisis.json").write_text(json.dumps({"corte": 5.0}), encoding="utf-8")
        final = VA.cmd_unir(base)
        s = VA.sondear(final)
        ok(s["ancho"] == 1080 and s["alto"] == 1920 and abs(s["dur"] - 12.0) < 0.15 and s["audio"], f"la variación dura inicio (3 s) + original desde el corte (9 s): {s}")
        tmp = base / "r"
        tmp.mkdir(exist_ok=True)
        VA._cuadro(base / "original.mp4", 5.0 + 4.0, tmp / "o.png")
        VA._cuadro(final, 3.0 + 4.0, tmp / "f.png")
        p_bien = VA.psnr(tmp / "o.png", tmp / "f.png")
        VA._cuadro(final, 3.0 + 2.0, tmp / "f2.png")
        p_mal = VA.psnr(tmp / "o.png", tmp / "f2.png")
        ok(p_bien is not None and p_bien >= VA.PSNR_MIN, f"el cuadro 4 s después del corte es el mismo del original (PSNR {p_bien})")
        ok(p_mal is not None and p_mal < VA.PSNR_MIN, f"un cuadro corrido 2 s NO pasa (PSNR {p_mal}): la compuerta sí ve un corte mal puesto")
        ok(not VA.negros(final, 4.5), "sin negro en el inicio ni en la unión")
        sil = VA.silencios(final, 2.2, 1.6)
        ok(not sil, f"sin silencio en la unión: {sil}")
        ok(VA.lufs(final, 0.0, 3.0) is not None, "la sonoridad del inicio se puede medir")
        ok(VA.hoja(final, 0.0, 6.0, 0.5, base / "hoja.png") and (base / "hoja.png").stat().st_size > 1000, "la hoja de cuadros del inicio")
        sincronia(base)
        subtitulos_quemados(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def sincronia(base: Path) -> None:
    """Un destello y un pitido a la vez en el segundo 7 del original: en la union (inicio de 3 s, corte en 5) deben caer juntos en el 5.0. Con ffmpeg 6.1 (el del
    servidor) el sonido del inicio traia ~19 ms de relleno y el cuerpo salia 1 cuadro tarde; el corte de los dos al mismo largo lo arreglo (13-nov)."""
    import numpy as np

    print("-- imagen y sonido juntos en la unión (destello y pitido)", flush=True)
    d = base / "sinc"
    d.mkdir(exist_ok=True)
    ffm = VA.ejecutable("ffmpeg")
    r = subprocess.run([ffm, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=14", "-f", "lavfi", "-i",
                        "aevalsrc='if(between(t,7,7.2),0.8*sin(2*PI*1000*t),0.02*sin(2*PI*220*t))':s=48000:d=14",
                        "-vf", "drawbox=x=440:y=860:w=200:h=200:color=gray:t=fill,drawbox=x=440:y=860:w=200:h=200:color=white:t=fill:enable='between(t,7,7.2)'",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(d / "original.mp4")], capture_output=True)
    assert r.returncode == 0, r.stderr[-300:]
    shutil.copy(base / "gancho_render.mp4", d / "gancho_render.mp4")
    (d / "pedido.json").write_text(json.dumps({"numero": "101.7", "archivo": "prueba 101.7.mp4"}), encoding="utf-8")
    (d / "analisis.json").write_text(json.dumps({"corte": 5.0}), encoding="utf-8")
    final = VA.cmd_unir(d)
    r = subprocess.run([ffm, "-loglevel", "error", "-i", str(final), "-vf", "crop=200:200:440:860,scale=8:8,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    claros = np.nonzero(np.frombuffer(r.stdout, dtype=np.uint8).reshape(-1, 64).mean(axis=1) > 230)[0]
    r = subprocess.run([ffm, "-loglevel", "error", "-i", str(final), "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-"], capture_output=True)
    fuertes = np.nonzero(np.abs(np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)) > 8000)[0]
    imagen = claros[0] / 30 if len(claros) else -1.0
    sonido = fuertes[0] / 48000 if len(fuertes) else -1.0
    ok(abs(imagen - 5.0) < 0.017 and abs(sonido - 5.0) < 0.008, f"el destello y el pitido del segundo 7 del original caen juntos en el 5.0 de la unión (imagen {imagen:.3f} s, sonido {sonido:.4f} s)")


# Un original de mentira con subtitulos quemados como los de Beto (minusculas blancas a media pantalla) y el grupo «ti yo se como» cruzando el corte (5 s), como en la 101.
GRUPOS = [(0.0, 1.8, "si alguna vez te"), (2.2, 3.5, "predicar y te quedaste"), (3.5, 4.6, "este video es para"), (4.6, 5.5, "ti yo se como"), (5.5, 7.0, "se siente"),
          (7.0, 9.0, "tienes versiculos anotados"), (9.0, 14.0, "todo queda suelto")]


def subtitulos_quemados(base: Path) -> None:
    print("-- subtítulos quemados: medir, borrar lo viejo y buscar un cuadro limpio (lector de texto)", flush=True)
    if VA.lector() is None:
        print("   (este equipo no tiene rapidocr: se saltan; el servidor y el editor sí lo tienen)")
        return
    from PIL import Image, ImageDraw

    d = base / "subt"
    d.mkdir(exist_ok=True)
    entradas, filtro, previo = [], [], "0:v"
    for k, (a, b, texto) in enumerate(GRUPOS, 1):
        im = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        dib = ImageDraw.Draw(im)
        fuente = VA._fuente(60)
        dib.text(((1080 - dib.textlength(texto, font=fuente)) / 2, 995), texto, font=fuente, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
        im.save(d / f"g{k}.png")
        entradas += ["-i", str(d / f"g{k}.png")]
        filtro.append(f"[{previo}][{k}:v]overlay=0:0:enable='between(t,{a},{b - 0.001})'[o{k}]")
        previo = f"o{k}"
    original = d / "original.mp4"
    r = subprocess.run([VA.ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x2a3b5c:size=1080x1920:rate=30:duration=14", *entradas,
                        "-f", "lavfi", "-i", "sine=frequency=330:duration=14", "-filter_complex", ";".join(filtro), "-map", f"[{previo}]", "-map", f"{len(GRUPOS) + 1}:a",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(original)], capture_output=True, text=True)
    ok(r.returncode == 0, f"original con subtítulos de mentira: {r.stderr[-200:]}")
    oidas = []
    for a, b, texto in GRUPOS:
        if texto == "ti yo se como":        # la frase vieja termina en «ti.» (4,6-4,8 s); la nueva empieza en «yo», despues del corte (5 s)
            oidas += [{"texto": "ti.", "inicio": 4.6, "fin": 4.8}, {"texto": "Yo", "inicio": 5.05, "fin": 5.2}, {"texto": "sé", "inicio": 5.2, "fin": 5.3},
                      {"texto": "cómo", "inicio": 5.3, "fin": 5.45}]
            continue
        ws = texto.split()
        oidas += [{"texto": w, "inicio": round(a + (b - a) * i / len(ws), 3), "fin": round(a + (b - a) * (i + 1) / len(ws), 3)} for i, w in enumerate(ws)]
    subt = VA.medir_subtitulos(original, oidas, 5.0, 14.0, d)
    ok(subt.get("hay") is True and abs(subt["centro"] - 1030) <= 30 and not subt["estilo"]["mayusculas"], f"mide los subtítulos: {[subt.get(k) for k in ('hay', 'centro', 'motivo')]}")
    rs = subt.get("resto") or {}
    ok(rs.get("palabras") == ["ti"] and abs(float(rs.get("hasta") or 0) - 0.5) <= 0.07 and rs.get("todo") is False,
       f"después del corte ve «ti» de la frase vieja por 0,5 s y borra solo esa palabra: {rs}")
    ci = subt.get("cuadro_inicio") or {}
    ok(ci.get("t") is not None and 1.75 <= ci["t"] <= 2.25 and (d / "cuadro_inicio.png").is_file(), f"el cuadro de partida del clip es el del hueco sin subtítulo (1,8-2,2 s): {ci}")
    # unir con el borrado y comprobar que el «ti» ya no se lee y que despues todo queda igual
    (base / "analisis.json").write_text(json.dumps({"corte": 5.0, "subtitulos": subt, "palabras": oidas}), encoding="utf-8")
    shutil.copyfile(original, base / "original.mp4")
    final = VA.cmd_unir(base)
    banda = subt["banda"]
    leidos = []
    for t in (3.0 + 0.5 / 30, 3.0 + 0.3):
        VA.sacar_cuadro(final, t, d / "f.png", banda)
        cajas = [c for c in VA.leer_cuadro(d / "f.png", banda[0]) if VA.es_texto(c)]
        leidos.append(" ".join(VA.fichas(" ".join(c["texto"] for c in cajas))))
    ok(all(x.startswith("yo") for x in leidos), f"tras la unión el subtítulo ya no dice «ti»: {leidos}")
    x, y, w, h = rs["region"]
    recorte = f"crop={w}:{h}:{x}:{y}"
    for t_o, t_f, igual, que in ((5.2, 3.2, False, "mientras se ve «ti», ese rectángulo cambia"), (5.8, 3.8, True, "después, el rectángulo queda igual al original")):
        VA.ff("-y", "-loglevel", "error", "-ss", f"{t_o}", "-i", str(original), "-frames:v", "1", "-vf", recorte, str(d / "ro.png"))
        VA.ff("-y", "-loglevel", "error", "-ss", f"{t_f}", "-i", str(final), "-frames:v", "1", "-vf", recorte, str(d / "rf.png"))
        p = VA.psnr(d / "ro.png", d / "rf.png")
        ok(p is not None and ((p >= VA.PSNR_MIN) == igual), f"{que} (PSNR {p})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--puras", action="store_true")
    a = ap.parse_args()
    puras()
    paso_corredor()
    if not a.puras:
        con_ffmpeg()
    print(f"\n{BIEN} comprobaciones bien, {len(FALLAS)} fallas")
    for f in FALLAS:
        print("  -", f)
    sys.exit(1 if FALLAS else 0)


if __name__ == "__main__":
    main()
