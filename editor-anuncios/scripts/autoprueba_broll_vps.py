"""Autoprueba de scripts/broll_vps.py (la corrida del b-roll en el VPS) SIN internet y sin tocar ni TikTok ni el Drive de nadie:
un Drive FALSO (autoprueba_drive.py), un proveedor de busqueda de pago FALSO, un TikTok FALSO con su lista publica gratis y un descargador FALSO (autoprueba_broll.py)
y un detector de texto FALSO.

Comprueba: la validacion de ofertas.json, una corrida completa (baja -> revisa el texto -> sube a Drive -> deja fuentes.csv en las dos partes),
que lo que ya estaba en Drive o lo que borro el equipo no vuelve, que lo descartado por texto nunca llega a Drive, que una subida que falla
se retoma sin volver a bajar, los topes, el permiso revocado (no baja nada), la carpeta de solo lectura, que un fuentes.csv de Drive con un
nombre de archivo raro nunca saca archivos del servidor, la limpieza de videos viejos, --simular y --sin-drive, y el estado que queda escrito.

Uso:  python scripts/autoprueba_broll_vps.py      Termina con "AUTOPRUEBA OK" si todo va bien. Corre esto despues de tocar broll_vps.py.
Necesita ffmpeg (para los videos de mentira).
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola  # noqa: E402
import autoprueba_broll as ab  # noqa: E402
import autoprueba_drive as ad  # noqa: E402

FALLAS: list[str] = []
MIME_CARPETA = ad.MIME_CARPETA


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"  ->  {detalle}" if detalle else ""))


def enlace(id_: str) -> str:
    return f"https://drive.google.com/drive/folders/{id_}?usp=sharing"


def config_basica(**cambios) -> dict:
    cfg = {"tope_videos_corrida": 30, "tope_creditos_corrida": 20, "minutos_max": 50, "conservar_dias": 14,
           "texto_quemado": {"activo": True, "modo": "descartar", "min_limpio_seg": 5},
           "ofertas": [{"producto": "prueba", "nombre": "Oferta de prueba", "drive": enlace("idprueba0000ofer1"), "consultas": ["crochet"], "max": 10, "paginas": 2,
                        "min_seg": 4, "max_seg": 60, "proveedor": "scrapecreators"}]}
    cfg.update(cambios)
    return cfg


def sacar_id(ruta: Path) -> str:
    m = re.search(r"tt_(\d+)", ruta.name)
    return m.group(1) if m else ""


def pruebas_config(br) -> None:
    print("Validacion de ofertas.json")
    with tempfile.TemporaryDirectory() as t:
        ruta = Path(t) / "ofertas.json"

        def cargar(cfg):
            ruta.write_text(json.dumps(cfg), encoding="utf-8")
            return br.cargar_config(ruta)

        c = cargar(config_basica())
        o = c["ofertas"][0]
        comprobar(o["activa"] is True and o["hashtags"] == [] and o["orden"] == "relevancia" and c["conservar_dias"] == 14 and c["texto_quemado"]["modo"] == "descartar", "los valores que faltan toman su valor por defecto")
        comprobar(cargar({**config_basica(), "_nota": "texto"})["tope_videos_corrida"] == 30, "las claves con guion bajo (notas) se ignoran")
        malas = [
            ({"ofertas": []}, "al menos una oferta"),
            ({"ofertas": [{"producto": "Mal Nombre", "drive": enlace("idprueba0000ofer1"), "consultas": ["x"]}]}, "'producto' debe ser"),
            ({"ofertas": [{"producto": "a", "drive": "https://example.com/x", "consultas": ["x"]}]}, "'drive' debe ser el enlace"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1")}]}, "ninguna fuente"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1"), "consultas": "no es lista"}]}, "'consultas' debe ser una lista"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1"), "consultas": ["x"], "max": 0}]}, "'max'"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1"), "consultas": ["x"], "orden": "inventado"}]}, "'orden'"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1"), "consultas": ["x"]}, {"producto": "a", "drive": enlace("idprueba0000ofer1"), "cuentas": ["y"]}]}, "repetido"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1"), "consultas": ["x"], "relacionadas": 11}]}, "'relacionadas'"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1"), "consultas": ["x"], "proveedor": "inventado"}]}, "'proveedor' debe ser descubrir"),
            ({"ofertas": [{"producto": "a", "drive": enlace("idprueba0000ofer1"), "consultas": ["x"], "excluir_autores": "Moleque Arteiro"}]}, "'excluir_autores' debe ser una lista de textos"),
            ({"tope_videos_corrida": 0}, "tope_videos_corrida"),
            ({"texto_quemado": {"modo": "borrar"}}, "'modo'"),
        ]
        for cambio, trozo in malas:
            try:
                cargar({**config_basica(), **cambio})
                comprobar(False, f"se rechaza: {trozo}")
            except br.ErrorConfig as e:
                comprobar(trozo in str(e), f"se rechaza y dice por que: {trozo}", str(e)[:200])
        try:
            ruta.write_text("{roto", encoding="utf-8")
            br.cargar_config(ruta)
            comprobar(False, "un JSON roto se explica")
        except br.ErrorConfig as e:
            comprobar("no es un JSON valido" in str(e), "un JSON roto se explica")
        try:
            br.cargar_config(Path(t) / "no-existe.json")
            comprobar(False, "sin archivo se explica")
        except br.ErrorConfig as e:
            comprobar("no encuentro" in str(e), "sin archivo se explica")
        cinco = cargar({**config_basica(), "ofertas": [{**config_basica()["ofertas"][0], "orden": ["relevancia", "likes", "fecha"]}]})["ofertas"][0]
        comprobar([br.elegir(cinco["orden"], n) for n in range(5)] == ["relevancia", "likes", "fecha", "relevancia", "likes"] and br.elegir("fecha", 7) == "fecha", "una lista de orden rota de una corrida a otra")
        a = br.argumentos_de_traer({**br.DEFECTOS_OFERTA, "producto": "p", "consultas": ["uno"], "cuentas": ["ana"], "filtro_desc": ["juego"], "hashtags": ["h"], "region": "CO"}, 5, 0, 0, False)
        comprobar(not any(x.startswith(("--consulta", "--hashtag")) for x in a) and "--cuenta=ana" in a and "--filtro-desc=juego" in a and "--sin-drive" in a and a[a.index("--max") + 1] == "5",
                  "sin pedidos disponibles no se hacen busquedas por palabra, pero si las cuentas, con el filtro y sin Drive", str(a))
        a = br.argumentos_de_traer({**br.DEFECTOS_OFERTA, "producto": "p", "consultas": ["uno"], "cuentas": ["ana"], "excluir_autores": ["Moleque Arteiro", "Otra Cuenta"]}, 5, 0, 0, False)
        comprobar([x.split("=", 1)[1] for x in a if x.startswith("--excluir-autor=")] == ["Moleque Arteiro", "Otra Cuenta"] and br.DEFECTOS_OFERTA["excluir_autores"] == [],
                  "las cuentas excluidas de la oferta viajan a traer (una opcion por nombre) y por omision no hay ninguna", str(a))
        # el nombre de un anunciante o una frase vienen de texto ajeno: uno que empiece con «-» llega a traer como VALOR (antes argparse lo tomaba por otra opcion y esa oferta no bajaba nada)
        a = br.argumentos_de_traer({**br.DEFECTOS_OFERTA, "producto": "p", "consultas": ["-raro", "--max"], "hashtags": ["-h"], "cuentas": ["-ana"], "filtro_desc": ["-x"],
                                    "excluir_autores": ["--max", "-Moleque Arteiro"]}, 5, 20, 0, False)
        try:
            leido = br.bt.construir_parser().parse_args(a)
        except SystemExit:                      # argparse sale con 2 si toma un valor por otra opcion: que eso sea una falla de la prueba y no la corte
            leido = None
        comprobar(leido is not None and leido.excluir_autor == ["--max", "-Moleque Arteiro"] and leido.consulta == ["-raro", "--max"] and leido.hashtag == ["-h"] and leido.cuenta == ["-ana"] and leido.filtro_desc == ["-x"] and leido.max == 5,
                  "un texto que empieza con «-» (nombre de anunciante, frase, cuenta) llega a traer como valor y no como otra opcion", str(a))
        a = br.argumentos_de_traer({**br.DEFECTOS_OFERTA, "producto": "p", "consultas": ["uno"], "relacionadas": 2}, 5, 20, 0, False)
        comprobar(a[a.index("--proveedor") + 1] == "descubrir" and a[a.index("--relacionadas") + 1] == "2" and "--consulta=uno" in a and a[a.index("--max-creditos") + 1] == "14",
                  "con pedidos disponibles: la busqueda por palabra usa la lista publica (descubrir), sigue las relacionadas pedidas y respeta el tope de la oferta", str(a))
        sola = cargar({"ofertas": [{"producto": "x", "drive": enlace("idprueba0000ofer1"), "consultas": ["uno"]}]})["ofertas"][0]
        comprobar(sola["proveedor"] == "descubrir" and sola["relacionadas"] == 0 and sola["max_creditos"] == 14, "una oferta que no dice proveedor usa la lista publica de TikTok, sin relacionadas, con tope de 14 pedidos")


def pruebas_texto(tq, tmp: Path) -> None:
    print("Detector de texto quemado (texto_quemado.py)")
    P = tq.PARAMETROS
    W, H = 720, 1280

    def caja(x, y, w, h):
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    comprobar(tq.caja_valida(caja(40, 1000, 640, 60), W, H, P), "un renglon de letras (ancho y bajito) cuenta como texto")
    comprobar(not tq.caja_valida(caja(0, 220, 250, 170), W, H, P), "una mancha casi cuadrada (un cartel de la escena, un adorno) no cuenta")
    comprobar(not tq.caja_valida(caja(40, 1000, 640, 8), W, H, P), "una linea muy delgada no cuenta (menos de 1,5% del alto)")
    comprobar(not tq.caja_valida(caja(40, 1000, 90, 30), W, H, P), "una palabra chiquita del fondo no cuenta (menos de 20% del ancho)")
    comprobar(not tq.caja_valida([(0, 0), (0, 0), (0, 0), (0, 0)], W, H, P), "una caja vacia no cuenta")
    comprobar(tq.hay_texto([caja(40, 1000, 640, 60)], W, H, P) and not tq.hay_texto([], W, H, P), "un cuadro con una caja valida tiene texto; sin cajas no")

    r = tq.resumir([False] * 10, 1.0, 10.0)
    comprobar(r["calidad"] == "limpio" and r["limpio_seg"] == 10.0 and r["tramos_limpios"] == [[0.0, 10.0]] and r["texto_pct"] == 0, "todo sin texto = limpio, un solo tramo")
    r = tq.resumir([True] * 10, 1.0, 10.0)
    comprobar(r["calidad"] == "malo" and r["limpio_seg"] == 0.0 and r["tramos_limpios"] == [] and r["texto_pct"] == 100, "todo con texto = malo")
    r = tq.resumir([False] * 4 + [True] * 4 + [False] * 4, 1.0, 12.0)
    comprobar(r["calidad"] == "medio" and r["tramos_limpios"] == [[0.0, 4.0], [8.0, 12.0]] and r["limpio_seg"] == 8.0 and r["texto_pct"] == 33, "texto en el medio: dos tramos limpios y calidad media", str(r))
    r = tq.resumir([False, False, True, False, False, True, False, False, True, True], 1.0, 10.0)
    comprobar(r["tramos_limpios"] == [] and r["calidad"] == "malo", "tramos limpios de menos de 3 s no sirven de toma", str(r))
    r = tq.resumir([False, True, True, True, True, True, True, True, True, True], 1.0, 10.0)
    comprobar(r["limpio_seg"] == 0.0 and r["calidad"] == "malo", "un solo cuadro limpio suelto no cuenta")
    r = tq.resumir([False, False, False, True, True, False, False, False], 2.5, 20.0)
    comprobar(r["tramos_limpios"] == [[0.0, 7.5], [12.5, 20.0]] and r["limpio_seg"] == 15.0 and r["texto_pct"] == 25, "con cuadros cada 2,5 s los tramos se miden en segundos reales", str(r))
    r = tq.resumir([False] * 3, 5.0, 12.0)
    comprobar(r["tramos_limpios"] == [[0.0, 12.0]] and r["limpio_seg"] == 12.0, "el ultimo tramo no pasa de la duracion del video", str(r))
    comprobar(tq.resumir([], 1.0, 10.0)["calidad"] == "malo", "sin cuadros no hay nada que afirmar (malo)")
    comprobar(tq.tramos_a_texto([[0.0, 16.0], [44.5, 64.0]]) == "0-16;44.5-64" and tq.tramos_a_texto([]) == "", "los tramos van a fuentes.csv como 0-16;44.5-64")

    # --- analizar() con un video de mentira y un detector de mentira (sin el modelo)
    ruta = tmp / "doce.mp4"
    ab.hacer_mp4(ruta, 270, 480, 12)

    def motor_falso(img: str) -> list:
        n = int(Path(img).stem[1:])          # c0001.jpg -> 1
        return [caja(40, 1000, 640, 60)] if 5 <= n <= 8 else [caja(0, 220, 250, 170)]
    detalle: list = []
    r = tq.analizar(ruta, motor=motor_falso, detalle=detalle)
    comprobar(r["muestras"] == 12 and r["tramos_limpios"] == [[0.0, 4.0], [8.0, 12.0]] and r["calidad"] == "medio" and [d["con_texto"] for d in detalle] == [False] * 4 + [True] * 4 + [False] * 4,
              "analizar(): saca un cuadro por segundo, aplica el detector y devuelve los tramos limpios", str(r))
    comprobar(not list(Path(tempfile.gettempdir()).glob("texto_*")), "y no deja carpetas temporales")

    def motor_roto(img: str) -> list:
        raise RuntimeError("el modelo se cayo")
    try:
        tq.analizar(ruta, motor=motor_roto)
        comprobar(False, "un fallo del detector en un cuadro se explica")
    except tq.ErrorTexto as e:
        comprobar("RuntimeError" in str(e) and not isinstance(e, tq.FaltaDetector), "un fallo del detector en un cuadro es un ErrorTexto de ese video (no tumba la corrida)", str(e))
    mal = tmp / "no-es-video.mp4"
    mal.write_bytes(b"esto no es un video" * 100)
    try:
        tq.analizar(mal, motor=motor_falso)
        comprobar(False, "un archivo que no es video se explica")
    except tq.ErrorTexto as e:
        comprobar(not isinstance(e, tq.FaltaDetector), "un archivo que no es video: ErrorTexto de ese video", str(e))
    original = tq.shutil.which
    tq.shutil.which = lambda nombre, *a, **k: None
    try:
        tq.analizar(ruta, motor=motor_falso)
        comprobar(False, "sin ffmpeg se explica")
    except tq.FaltaDetector as e:
        comprobar("ffmpeg" in str(e), "sin ffmpeg: FaltaDetector (no sirve probar con otro video)", str(e))
    finally:
        tq.shutil.which = original

    # --- el detector REAL (RapidOCR): solo donde esta instalado (en el servidor); en el PC del editor se salta
    if not tq.disponible():
        print("  (el detector real no esta instalado en este equipo: se prueba en el servidor)")
        return
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  (falta Pillow: se salta la prueba con el detector real)")
        return

    def imagen(con_texto: bool):
        im = Image.new("RGB", (540, 960), (90, 140, 200))
        d = ImageDraw.Draw(im)
        for y in range(0, 960, 48):
            d.rectangle([0, y, 540, y + 24], fill=(60 + (y % 150), 110, 170))
        if con_texto:
            fuente = ImageFont.load_default(size=64)
            d.text((30, 600), "HOLA AMIGAS", font=fuente, fill=(255, 255, 0), stroke_width=4, stroke_fill=(0, 0, 0))
            d.text((30, 700), "MIRA ESTO YA", font=fuente, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
        return im
    resultados = {}
    for nombre, con in (("sin_texto", False), ("con_texto", True)):
        imagen(con).save(tmp / f"{nombre}.png")
        subprocess.run([ab.ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-loop", "1", "-i", str(tmp / f"{nombre}.png"), "-t", "8", "-r", "10",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(tmp / f"{nombre}.mp4")], check=True)
        resultados[nombre] = tq.analizar(tmp / f"{nombre}.mp4")
    comprobar(resultados["sin_texto"]["calidad"] == "limpio" and resultados["sin_texto"]["texto_pct"] <= 15, "DETECTOR REAL: un video de rayas sin letras sale limpio", str(resultados["sin_texto"]))
    comprobar(resultados["con_texto"]["calidad"] == "malo" and resultados["con_texto"]["texto_pct"] >= 85, "DETECTOR REAL: un video con letras amarillas con contorno sale malo", str(resultados["con_texto"]))


def pruebas_corrida(br, bt, da, tmp: Path, estado_drive, provider_visto: list, nueva_llave) -> None:
    print("Corrida completa contra el Drive falso")
    material = tmp / "material"
    material.mkdir()
    ab.hacer_mp4(material / "vertical.mp4", 270, 480, 5)
    ab.hacer_mp4(material / "horizontal.mp4", 480, 270, 5)
    ab.DescargadorFalso.MATERIAL = {"vertical": material / "vertical.mp4", "horizontal": material / "horizontal.mp4"}
    ab.DescargadorFalso.COMPORTAMIENTO = {}
    bt.PUBLIC = tmp / "public"
    bt.RAIZ = tmp
    time.sleep = lambda s: None
    (tmp / "estado").mkdir()
    est = estado_drive
    cred = tmp / "google.json"
    cred.write_text(json.dumps(est.cred), encoding="utf-8")
    cfg_ruta = tmp / "ofertas.json"
    ID_OFERTA = "idprueba0000ofer1"

    def montar_drive(con_ledger: bool = True):
        est.reiniciar()
        est.poner("DINAMICAS", MIME_CARPETA, ["raiz"], id_fijo=ID_OFERTA)
        if con_ledger:
            carpeta = est.poner("B-rolls", MIME_CARPETA, [ID_OFERTA])
            # lo que el PC ya habia bajado a mano: V6 esta en Drive; V4 (horizontal) se descarto; el id 9999000000000099001 lo borro el equipo
            filas = {
                ab.V6: {"id": ab.V6, "archivo": f"tt_{ab.V6}.mp4", "estado": "bajado", "url": f"https://www.tiktok.com/@tejedora/video/{ab.V6}", "en_drive": "si", "descargado_en": "2026-10-19T10:00:00"},
                "9999000000000099001": {"id": "9999000000000099001", "archivo": "tt_9999000000000099001.mp4", "estado": "bajado", "en_drive": "si", "descargado_en": "2026-10-19T10:01:00"},
            }
            with tempfile.TemporaryDirectory() as t2:
                bt.escribir_fuentes(Path(t2), filas)
                est.poner("fuentes.csv", "text/csv", [carpeta["id"]], (Path(t2) / "fuentes.csv").read_bytes())
            est.poner(f"tt_{ab.V6}.mp4", "video/mp4", [carpeta["id"]], b"x" * 1000)
            return carpeta["id"]
        return None

    def correr(args_extra=None, cfg=None, **kw):
        cfg_ruta.write_text(json.dumps(cfg or config_basica()), encoding="utf-8")
        args = br.construir_parser().parse_args(["correr", "--config", str(cfg_ruta), *(args_extra or [])])
        salida = io.StringIO()
        registro: list[str] = []
        with contextlib.redirect_stdout(salida):
            codigo = br.correr(args, fabrica=ab.DescargadorFalso, analizar=kw.pop("analizar", texto_falso({})), ruta_credenciales=cred, log=registro.append, **kw)
        return codigo, "\n".join(registro) + "\n" + salida.getvalue()

    def texto_falso(mapa: dict):
        def analizar(ruta, duracion=None, parametros=None, detalle=None):
            vid = sacar_id(Path(ruta))
            return mapa.get(vid, {"muestras": 5, "intervalo": 1.0, "texto_pct": 0, "limpio_seg": 5.0, "tramos_limpios": [[0.0, 5.0]], "calidad": "limpio"})
        return analizar

    MAPA = {ab.V2: {"muestras": 5, "intervalo": 1.0, "texto_pct": 80, "limpio_seg": 1.0, "tramos_limpios": [], "calidad": "malo"},
            ab.V1: {"muestras": 5, "intervalo": 1.0, "texto_pct": 40, "limpio_seg": 3.0, "tramos_limpios": [[0.0, 3.0]], "calidad": "medio"}}
    MAPA[ab.V1]["limpio_seg"] = 6.5       # medio: tiene tramos limpios pero tambien texto, y supera el minimo (5 s)
    MAPA[ab.V1]["tramos_limpios"] = [[0.0, 6.5]]

    # ------------------------------------------------------------ corrida 1
    brolls = montar_drive()
    ab.DescargadorFalso.LLAMADAS.clear()
    provider_visto.clear()
    codigo, txt = correr(analizar=texto_falso(MAPA))
    local = bt.PUBLIC / "productos" / "prueba" / "broll-tiktok"
    nombres_drive = sorted(f["name"] for f in est.hijos(brolls))
    comprobar(codigo == 0, "la corrida termina bien (codigo 0)", f"{codigo}\n{txt[-900:]}")
    comprobar(sorted(ab.DescargadorFalso.LLAMADAS) == sorted([ab.V1, ab.V2]), "baja solo lo nuevo: V6 ya estaba en Drive (lo bajo el PC) y no se vuelve a bajar", str(ab.DescargadorFalso.LLAMADAS))
    comprobar(f"tt_{ab.V1}.mp4" in nombres_drive and f"tt_{ab.V2}.mp4" not in nombres_drive, "sube a Drive el bueno (V1) y NO el descartado por texto (V2)", str(nombres_drive))
    v1_drive = [f for f in est.hijos(brolls) if f["name"] == f"tt_{ab.V1}.mp4"][0]
    comprobar(v1_drive["parents"] == [brolls] and v1_drive["content"] == (local / f"tt_{ab.V1}.mp4").read_bytes(), "el video quedo dentro de B-rolls con el mismo contenido")
    comprobar((local / "_descartados" / f"tt_{ab.V2}.mp4").is_file() and not (local / f"tt_{ab.V2}.mp4").exists(), "el descartado queda guardado en el servidor (_descartados), fuera de Drive")
    filas = bt.leer_fuentes(local)
    comprobar(filas[ab.V2]["estado"] == "descartado" and "texto quemado" in filas[ab.V2]["motivo"] and filas[ab.V2]["calidad"] == "malo", "el registro anota por que se descarto", str(filas.get(ab.V2)))
    comprobar(filas[ab.V1]["en_drive"] == "si" and filas[ab.V1]["calidad"] == "medio" and filas[ab.V1]["tramos_limpios"] == "0-6.5" and filas[ab.V1]["limpio_seg"] == "6.5", "el registro anota la calidad, los tramos limpios y que ya esta en Drive", str(filas.get(ab.V1)))
    csv_drive = br.leer_csv_bytes([f for f in est.hijos(brolls) if f["name"] == "fuentes.csv"][0]["content"])
    comprobar({ab.V1, ab.V2, ab.V6, "9999000000000099001"} <= set(csv_drive) and csv_drive[ab.V1]["en_drive"] == "si", "fuentes.csv de Drive tiene lo de antes MAS lo nuevo", str(sorted(csv_drive)))
    comprobar(len([f for f in est.hijos(brolls) if f["name"] == "fuentes.csv"]) == 1, "y es el mismo archivo (se reemplaza, no se duplica)")
    comprobar(not any(f["name"] == "LEEME.txt" for f in est.hijos(brolls)), "si B-rolls ya existia no se le agrega un LEEME.txt (si el equipo lo borro, no vuelve)")
    ult = json.loads((tmp / "estado" / "ultima_corrida.json").read_text(encoding="utf-8"))
    comprobar(ult["codigo"] == 0 and ult["videos"] == 2 and ult["subidos"] == 1 and ult["descartados"] == 1 and ult["ofertas"][0]["producto"] == "prueba", "escribe estado/ultima_corrida.json", str(ult)[:300])
    hist = (tmp / "estado" / "historial.jsonl").read_text(encoding="utf-8").strip().splitlines()
    comprobar(len(hist) == 1 and json.loads(hist[0])["subidos"] == 1, "y una linea en historial.jsonl")
    comprobar(json.loads((tmp / "estado" / "rotacion.json").read_text(encoding="utf-8")) == {"prueba": 1}, "y recuerda en que vuelta va (rotacion.json)")
    comprobar("ya91" not in txt and ab.LLAVE not in txt and est.cred["client_secret"] not in txt and est.cred["refresh_token"] not in txt and "ya29" not in txt,
              "ninguna llave ni token sale en el registro")
    comprobar(not list((tmp / "estado").glob("*.nuevo")) and not list(local.glob("*.tmp")), "sin archivos temporales sobrantes")
    llamadas_post = [x for x in est.log if x[0] == "DELETE"]
    comprobar(not llamadas_post, "durante la corrida no hubo ni una llamada de borrar a Drive")

    # ------------------------------------------------------------ corrida 2: nada nuevo; lo que el equipo borra no vuelve
    est.archivos[v1_drive["id"]]["trashed"] = True       # el equipo manda V1 a la papelera
    ab.DescargadorFalso.LLAMADAS.clear()
    n_llamadas = len(est.log)
    codigo, txt = correr(analizar=texto_falso(MAPA))
    comprobar(codigo == 0 and ab.DescargadorFalso.LLAMADAS == [], "segunda corrida: no baja nada (todo esta en el registro)", f"{ab.DescargadorFalso.LLAMADAS}\n{txt[-500:]}")
    comprobar(not any(f["name"] == f"tt_{ab.V1}.mp4" and not f["trashed"] for f in est.archivos.values()), "lo que el equipo mando a la papelera NO se vuelve a subir")
    ordenes = [p.get("sort_by") for u, p in provider_visto if u.endswith("/keyword") and "cursor" not in p]
    comprobar(len(ordenes) >= 1, "pidio busquedas al proveedor", str(provider_visto[:3]))
    comprobar(json.loads((tmp / "estado" / "rotacion.json").read_text(encoding="utf-8")) == {"prueba": 2}, "la vuelta sube a 2")

    # ------------------------------------------------------------ una subida que falla se retoma sin volver a bajar
    print("Subida que falla y se retoma")
    brolls = montar_drive()
    shutil_rm = __import__("shutil").rmtree
    shutil_rm(bt.PUBLIC / "productos" / "prueba", ignore_errors=True)
    (tmp / "estado" / "rotacion.json").unlink(missing_ok=True)
    ab.DescargadorFalso.LLAMADAS.clear()
    est.fallas["http_500"] = 0
    # el 500 empieza cuando ya paso la lectura de carpetas: se provoca al abrir la sesion de subida
    original = ad.Manejador.subida_nueva

    def subida_rota(self, p, cuerpo):
        if est.fallas.get("subida_rota") and p.get("uploadType") == "resumable":
            return self.responder(500, ad._cuerpo_error(500, "backendError", "Backend Error"))
        return original(self, p, cuerpo)
    ad.Manejador.subida_nueva = subida_rota
    est.fallas["subida_rota"] = True
    codigo, txt = correr(analizar=texto_falso({}))
    local = bt.PUBLIC / "productos" / "prueba" / "broll-tiktok"
    filas = bt.leer_fuentes(local)
    comprobar(codigo == 2 and filas[ab.V1]["en_drive"] != "si" and (local / f"tt_{ab.V1}.mp4").is_file(), "si Drive falla al subir: el video se queda en el servidor, sin marcar, y la salida es 2", f"{codigo}\n{txt[-700:]}")
    comprobar("Tres fallos seguidos subiendo" in txt or "sin subir todavia" in txt, "y lo dice en el registro")
    est.fallas["subida_rota"] = False
    ab.DescargadorFalso.LLAMADAS.clear()
    codigo, txt = correr(analizar=texto_falso({}))
    nombres = sorted(f["name"] for f in est.hijos(brolls))
    comprobar(f"tt_{ab.V1}.mp4" in nombres and ab.V1 not in ab.DescargadorFalso.LLAMADAS, "la corrida siguiente lo sube SIN volver a bajarlo", f"{nombres} {ab.DescargadorFalso.LLAMADAS}")
    ad.Manejador.subida_nueva = original
    est.fallas.clear()

    # ------------------------------------------------------------ si la subida llego a Drive pero el programa murio antes de anotarlo
    brolls = montar_drive()
    shutil_rm(bt.PUBLIC / "productos" / "prueba", ignore_errors=True)
    codigo, txt = correr(analizar=texto_falso({}))
    local = bt.PUBLIC / "productos" / "prueba" / "broll-tiktok"
    filas = bt.leer_fuentes(local)
    for f in filas.values():
        if f["id"] == ab.V1:
            f["en_drive"] = "no"       # como si el programa hubiera muerto justo despues de subir
    bt.escribir_fuentes(local, filas)
    n_antes = len([f for f in est.hijos(brolls) if f["name"] == f"tt_{ab.V1}.mp4"])
    (local / f"tt_{ab.V1}.mp4").touch()
    for f in est.hijos(brolls):
        if f["name"] == "fuentes.csv":
            f["trashed"] = True         # y el registro de Drive se perdio: solo queda comparar nombre y tamano
    codigo, txt = correr(analizar=texto_falso({}))
    n_despues = len([f for f in est.hijos(brolls) if f["name"] == f"tt_{ab.V1}.mp4"])
    comprobar(n_antes == 1 and n_despues == 1 and bt.leer_fuentes(local)[ab.V1]["en_drive"] == "si", "si el video ya estaba en Drive (misma medida) solo se marca, no se sube dos veces", f"{n_antes}->{n_despues}")

    # ------------------------------------------------------------ permiso revocado
    print("Permiso, carpetas y topes")
    montar_drive()
    shutil_rm(bt.PUBLIC / "productos" / "prueba", ignore_errors=True)
    ab.DescargadorFalso.LLAMADAS.clear()
    est.fallas["revocado"] = True
    codigo, txt = correr()
    comprobar(codigo == 4 and "SIN PERMISO" in txt and ab.DescargadorFalso.LLAMADAS == [], "permiso revocado: sale con 4 y NO baja nada de TikTok", f"{codigo} {ab.DescargadorFalso.LLAMADAS}\n{txt[-400:]}")
    ult = json.loads((tmp / "estado" / "ultima_corrida.json").read_text(encoding="utf-8"))
    comprobar(ult["codigo"] == 4 and ult["sin_permiso_de_drive"] is True, "y queda anotado en el estado")
    est.fallas.clear()

    # carpeta de solo lectura: esa oferta falla, la otra sigue
    est.reiniciar()
    est.poner("SOLO LECTURA", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000solo")
    est.poner("OK", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer2")
    est.solo_lectura.add("idprueba0000solo")
    dos = config_basica()
    dos["ofertas"] = [{**dos["ofertas"][0], "producto": "solo", "drive": enlace("idprueba0000solo")}, {**dos["ofertas"][0], "producto": "buena", "drive": enlace("idprueba0000ofer2")}]
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    ab.DescargadorFalso.LLAMADAS.clear()
    codigo, txt = correr(cfg=dos)
    r = {x["producto"]: x for x in json.loads((tmp / "estado" / "ultima_corrida.json").read_text(encoding="utf-8"))["ofertas"]}
    comprobar(codigo == 2 and r["solo"]["estado"] == "fallo" and "solo lectura" in txt and r["buena"]["subidos"] >= 1, "carpeta de solo lectura: esa oferta falla con un mensaje claro y la otra sigue y sube", f"{codigo}\n{txt[-600:]}")

    # topes
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")
    est.poner("B", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer2")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    dos = config_basica(tope_videos_corrida=2)
    dos["ofertas"] = [{**dos["ofertas"][0], "producto": "uno", "max": 10}, {**dos["ofertas"][0], "producto": "dos", "drive": enlace("idprueba0000ofer2"), "max": 10}]
    ab.DescargadorFalso.LLAMADAS.clear()
    codigo, txt = correr(cfg=dos)
    r = {x["producto"]: x for x in json.loads((tmp / "estado" / "ultima_corrida.json").read_text(encoding="utf-8"))["ofertas"]}
    comprobar(len(ab.DescargadorFalso.LLAMADAS) == 2 and r["uno"]["bajados"] == 2 and r["dos"]["estado"] == "saltada", "el tope de la corrida (2 videos) se respeta entre ofertas: la segunda espera", f"{ab.DescargadorFalso.LLAMADAS} {r}")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")
    ab.DescargadorFalso.LLAMADAS.clear()
    codigo, txt = correr(["--max-por-oferta", "1"])
    comprobar(len(ab.DescargadorFalso.LLAMADAS) == 1, "--max-por-oferta manda sobre el 'max' de la oferta", str(ab.DescargadorFalso.LLAMADAS))

    # ------------------------------------------------------------ un fuentes.csv de Drive con nombres raros nunca saca archivos del servidor
    print("Seguridad del registro compartido")
    est.reiniciar()
    brolls = est.poner("B-rolls", MIME_CARPETA, ["raiz"])["id"]
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")
    est.archivos[brolls]["parents"] = ["idprueba0000ofer1"]
    secreto = tmp / "secretos"
    secreto.mkdir(exist_ok=True)
    (secreto / "google.json").write_text("NO-SUBIR-ESTO", encoding="utf-8")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    local = bt.PUBLIC / "productos" / "prueba" / "broll-tiktok"
    local.mkdir(parents=True)
    (tmp / "afuera.mp4").write_bytes(b"x" * 5000)
    ataque = {"9999000000000180001": {"id": "9999000000000180001", "archivo": "../../secretos/google.json", "estado": "bajado", "en_drive": "no", "descargado_en": "2026-10-20T01:00:00"},
              "9999000000000180002": {"id": "9999000000000180002", "archivo": "../../afuera.mp4", "estado": "bajado", "en_drive": "no", "descargado_en": "2026-10-20T01:00:01"},
              "9999000000000180003": {"id": "9999000000000180003", "archivo": "/etc/passwd", "estado": "bajado", "en_drive": "no", "descargado_en": "2026-10-20T01:00:02"}}
    with tempfile.TemporaryDirectory() as t2:
        bt.escribir_fuentes(Path(t2), ataque)
        est.poner("fuentes.csv", "text/csv", [brolls], (Path(t2) / "fuentes.csv").read_bytes())
    codigo, txt = correr()
    subidos = [f["name"] for f in est.archivos.values() if f["mimeType"] != MIME_CARPETA]
    comprobar(not any("google" in n or "afuera" in n or "passwd" in n for n in subidos) and (secreto / "google.json").read_text(encoding="utf-8") == "NO-SUBIR-ESTO", "un fuentes.csv de Drive con rutas raras ('../../secretos/google.json') no saca archivos del servidor", str(subidos))
    # una descripcion de TikTok que empieza con = se guarda como texto, no como formula
    filas = bt.leer_fuentes(local)
    peligrosa = {"id": "9999000000000190001", "archivo": "", "estado": "descartado", "descripcion": "=IMPORTXML(\"http://malo.example\",\"//a\")", "autor": "+cmd", "motivo": "@x"}
    filas[peligrosa["id"]] = peligrosa
    bt.escribir_fuentes(local, filas)
    crudo = (local / "fuentes.csv").read_text(encoding="utf-8-sig")
    comprobar("'=IMPORTXML" in crudo and "'+cmd" in crudo and "'@x" in crudo, "en fuentes.csv un texto que empieza con = + @ se guarda con apostrofo (no se ejecuta como formula en Sheets o Excel)")
    otra_vez = bt.leer_fuentes(local)
    bt.escribir_fuentes(local, otra_vez)
    comprobar(((local / "fuentes.csv").read_text(encoding="utf-8-sig")).count("''") == 0, "y escribirlo otra vez no apila apostrofos")

    # ------------------------------------------------------------ limpieza de lo viejo
    print("Limpieza y modos")
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    local = bt.PUBLIC / "productos" / "prueba" / "broll-tiktok"
    (local / "_descartados").mkdir(parents=True)
    viejo, nuevo = local / "tt_9999000000000230001.mp4", local / "tt_9999000000000230002.mp4"
    desc_viejo, desc_nuevo = local / "_descartados" / "tt_9999000000000230003.mp4", local / "_descartados" / "tt_9999000000000230004.mp4"
    for p in (viejo, nuevo, desc_viejo, desc_nuevo):
        p.write_bytes(b"x" * 2000)
    hace = time.time() - 40 * 86400
    os.utime(viejo, (hace, hace))
    os.utime(desc_viejo, (hace, hace))
    filas = {"9999000000000230001": {"id": "9999000000000230001", "archivo": viejo.name, "estado": "bajado", "en_drive": "si"},
             "9999000000000230002": {"id": "9999000000000230002", "archivo": nuevo.name, "estado": "bajado", "en_drive": "si"}}
    bt.escribir_fuentes(local, filas)
    os.utime(nuevo, (time.time() - 3 * 86400,) * 2)
    br.limpiar(local, bt.leer_fuentes(local), 14, lambda *a: None)
    comprobar(not viejo.exists() and nuevo.exists(), "borra del servidor el video ya subido con mas de 14 dias y deja el reciente")
    comprobar(not desc_viejo.exists() and desc_nuevo.exists(), "borra los descartados con mas de 30 dias y deja los recientes")
    comprobar("9999000000000230001" in bt.leer_fuentes(local), "pero el registro conserva el id (no se vuelve a bajar)")
    sin_subir_aun = local / "tt_9999000000000230009.mp4"
    sin_subir_aun.write_bytes(b"x")
    os.utime(sin_subir_aun, (hace, hace))
    br.limpiar(local, {"9999000000000230009": {"id": "9999000000000230009", "archivo": sin_subir_aun.name, "estado": "bajado", "en_drive": "no"}}, 14, lambda *a: None)
    comprobar(sin_subir_aun.exists(), "un video viejo que NO se ha subido nunca se queda")

    # --simular y --sin-drive y --sin-texto
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    ab.DescargadorFalso.LLAMADAS.clear()
    n = len(est.archivos)
    codigo, txt = correr(["--simular"])
    comprobar(codigo == 0 and ab.DescargadorFalso.LLAMADAS == [] and len(est.archivos) == n and not any(m != "GET" and u != "/token" for m, u, _ in est.log), "--simular no baja, no crea carpetas en Drive y no escribe nada en Drive", f"{codigo}\n{txt[-400:]}")
    comprobar("SIMULACION" in txt and "se crearia" in txt, "y lo dice")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.log.clear()
    ab.DescargadorFalso.LLAMADAS.clear()
    codigo, txt = correr(["--sin-drive"])
    local = bt.PUBLIC / "productos" / "prueba" / "broll-tiktok"
    comprobar(len(ab.DescargadorFalso.LLAMADAS) >= 1 and not est.log and sorted(p.name for p in local.glob("tt_*.mp4")), "--sin-drive baja y revisa pero no habla con Drive", f"{len(est.log)} llamadas")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")
    codigo, txt = correr(["--sin-texto"], analizar=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debia revisar el texto")))
    comprobar(codigo == 0 and any(f["name"].startswith("tt_") for f in est.archivos.values()), "--sin-texto no llama al detector de texto y sube todo")
    nuevas = [f for f in est.archivos.values() if f["name"] == "B-rolls"]
    leeme = [f for f in est.archivos.values() if f["name"] == "LEEME.txt"]
    comprobar(len(nuevas) == 1 and len(leeme) == 1 and leeme[0]["parents"] == [nuevas[0]["id"]] and b"derecho" in leeme[0]["content"] and "Se creo la carpeta B-rolls" in txt,
              "una carpeta B-rolls nueva se crea dentro de la de la oferta y trae su LEEME.txt con el aviso de derechos", str([(f["name"], f["parents"]) for f in est.archivos.values()]))

    # detector de texto ausente: se sube todo tal cual y se avisa una vez
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")

    def sin_detector(ruta, duracion=None, parametros=None, detalle=None):
        raise br.tq.FaltaDetector("falta el paquete rapidocr-onnxruntime en este equipo")
    codigo, txt = correr(analizar=sin_detector)
    comprobar(txt.count("Sin el detector de texto") == 1 and sum(1 for f in est.archivos.values() if f["name"].startswith("tt_")) >= 2, "sin el detector de texto: avisa UNA vez y sube los videos sin revisar", txt[-500:])

    # oferta apagada y --solo
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo="idprueba0000ofer1")
    ab.DescargadorFalso.LLAMADAS.clear()
    apagada = config_basica()
    apagada["ofertas"][0]["activa"] = False
    codigo, txt = correr(cfg=apagada)
    comprobar(codigo == 0 and ab.DescargadorFalso.LLAMADAS == [] and "0 oferta(s)" in txt, "una oferta con activa=false no se toca")
    codigo, txt = correr(["--solo", "inexistente"])
    comprobar(codigo == 1 and "no estan en ofertas.json" in txt, "--solo con una oferta que no existe se rechaza")

    # ------------------------------------------------------------ la lista publica de TikTok (gratis): sin llave de nadie
    print("Corrida con la lista publica de TikTok (gratis, sin llave)")
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo=ID_OFERTA)
    libre = config_basica()
    del libre["ofertas"][0]["proveedor"]
    libre["ofertas"][0].update(relacionadas=1)
    llave = os.environ.pop("SCRAPECREATORS_API_KEY", None)
    ab.VISTO_TT.clear()
    ab.VISTO.clear()
    ab.DescargadorFalso.LLAMADAS.clear()
    try:
        codigo, txt = correr(cfg=libre)
    finally:
        if llave:
            os.environ["SCRAPECREATORS_API_KEY"] = llave
    subidos = sorted(f["name"] for f in est.archivos.values() if f["name"].startswith("tt_"))
    comprobar(codigo == 0 and subidos == sorted(f"tt_{i}.mp4" for i in (ab.K1, ab.K2, ab.K6, ab.K7, ab.K8)), "sin ninguna llave: busca por la lista publica, sigue una palabra relacionada y sube los videos buenos a Drive", f"{codigo} {subidos}\n{txt[-700:]}")
    comprobar(not ab.VISTO and len(ab.VISTO_TT) == 5, "no toco al proveedor de pago y hizo 5 pedidos a TikTok (pagina de visitante, 2 paginas, palabras relacionadas, 1 pagina de la relacionada)", f"{len(ab.VISTO)} {[u for u, q, h in ab.VISTO_TT]}")
    brolls_n = [f for f in est.archivos.values() if f["name"] == "B-rolls"][0]["id"]
    csv_n = br.leer_csv_bytes([f for f in est.archivos.values() if f["name"] == "fuentes.csv" and f["parents"] == [brolls_n]][0]["content"])
    comprobar(csv_n[ab.K1]["tema"].startswith("Amigurumi paso a paso") and csv_n[ab.K7]["consulta"] == "crochet para principiantes" and csv_n[ab.K1]["en_drive"] == "si",
              "fuentes.csv en Drive guarda de que busqueda salio cada video y de que trata segun TikTok", str(csv_n.get(ab.K1)))
    ult = json.loads((tmp / "estado" / "ultima_corrida.json").read_text(encoding="utf-8"))
    bq = ult["ofertas"][0]["busquedas"]
    comprobar(bq.get("crochet", 0) >= 1 and "crochet para principiantes" in bq, "y guarda cuantos videos mostro TikTok por cada frase (y por la palabra relacionada que siguio)", str(bq))
    comprobar(ult["creditos"] == 5 and "pedido(s) de busqueda" in txt and "llave" not in txt.lower(), "el registro cuenta los 5 pedidos, los llama pedidos (no creditos) y no menciona ninguna llave", txt[-400:])
    # la misma corrida, pero la oferta dice que el anunciante original es «Ana Crochet»: su cuenta (ana_crochet, el video K1) no se baja ni se sube como b-roll
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo=ID_OFERTA)
    con_exclusion = json.loads(json.dumps(libre))
    con_exclusion["ofertas"][0]["excluir_autores"] = ["Ana Crochet"]
    ab.VISTO_TT.clear()
    ab.DescargadorFalso.LLAMADAS.clear()
    llave = os.environ.pop("SCRAPECREATORS_API_KEY", None)
    try:
        codigo, txt = correr(cfg=con_exclusion)
    finally:
        if llave:
            os.environ["SCRAPECREATORS_API_KEY"] = llave
    subidos_ex = sorted(f["name"] for f in est.archivos.values() if f["name"].startswith("tt_"))
    ult_ex = json.loads((tmp / "estado" / "ultima_corrida.json").read_text(encoding="utf-8"))
    comprobar(codigo == 0 and subidos_ex == sorted(f"tt_{i}.mp4" for i in (ab.K2, ab.K6, ab.K7, ab.K8)) and ult_ex["ofertas"][0]["de_la_cuenta_del_anunciante"] == 1 and "1 de la cuenta del anunciante original" in txt,
              "con 'excluir_autores': no baja ni sube el video de la cuenta del anunciante (K1), los demas si, y el resultado de la corrida lo cuenta", f"{codigo} {subidos_ex}\n{txt[-600:]}")
    comprobar(not any(ab.K1 in str(x) for x in ab.DescargadorFalso.LLAMADAS), "y a TikTok ni siquiera le pidio ese video", str(ab.DescargadorFalso.LLAMADAS))
    ab.MODO_TT["siempre_429"] = True
    shutil_rm(bt.PUBLIC / "productos", ignore_errors=True)
    est.reiniciar()
    est.poner("A", MIME_CARPETA, ["raiz"], id_fijo=ID_OFERTA)
    try:
        codigo, txt = correr(cfg=libre)
    finally:
        ab.MODO_TT["siempre_429"] = False
    comprobar(codigo == 2 and "esperar" in txt and not any(f["name"].startswith("tt_") for f in est.archivos.values()), "si TikTok pide esperar: la corrida termina con 2, lo dice y no se cae", f"{codigo}\n{txt[-500:]}")

    # estado
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        br.estado_corrida()
    comprobar("Ultima corrida" in salida.getvalue(), "el comando estado resume la ultima corrida")


def main() -> None:
    configurar_consola()
    srv_drive, estado_drive = ad.iniciar_drive_falso()
    srv_proveedor = ThreadingHTTPServer(("127.0.0.1", 0), ab.Falso)
    threading.Thread(target=srv_proveedor.serve_forever, daemon=True).start()
    os.environ["BROLL_PROVEEDOR_URL"] = f"http://127.0.0.1:{srv_proveedor.server_port}"
    srv_tiktok = ThreadingHTTPServer(("127.0.0.1", 0), ab.FalsoTikTok)
    threading.Thread(target=srv_tiktok.serve_forever, daemon=True).start()
    os.environ["BROLL_TIKTOK_URL"] = f"http://127.0.0.1:{srv_tiktok.server_port}"
    os.environ["SCRAPECREATORS_API_KEY"] = ab.LLAVE
    for nombre in ("drive_api", "broll_tiktok", "broll_vps"):
        sys.modules.pop(nombre, None)
    da = importlib.import_module("drive_api")
    bt = importlib.import_module("broll_tiktok")
    br = importlib.import_module("broll_vps")
    comprobar(da.URL_API.startswith("http://127.0.0.1:") and bt.BASE_PROVEEDOR.startswith("http://127.0.0.1:") and bt.BASE_TIKTOK.startswith("http://127.0.0.1:"), "la autoprueba habla con el Drive, el proveedor y el TikTok FALSOS")
    bt.Descubrir.PAUSA = (0.0, 0.0)

    print("Versiones del servidor = versiones del editor")
    base = Path(__file__).resolve().parents[1]
    def pins(ruta: Path) -> dict:
        if not ruta.is_file():
            return {}
        return {m.group(1).lower().replace("_", "-"): m.group(2) for m in re.finditer(r"^\s*([A-Za-z0-9_.\-]+)==([\w.]+)", ruta.read_text(encoding="utf-8"), re.M)}
    vps, editor = pins(base / "vps" / "requirements-vps.txt"), pins(base / "requirements.txt")
    if vps and editor:
        comunes = sorted(set(vps) & set(editor))
        comprobar(len(comunes) >= 4 and all(editor[k] == vps[k] for k in comunes), "requirements-vps.txt usa las mismas versiones que requirements.txt en lo que tienen en comun", f"{[(k, vps[k], editor[k]) for k in comunes]}")

    pruebas_config(br)
    with tempfile.TemporaryDirectory(prefix="autoprueba_broll_vps_") as t:
        pruebas_texto(br.tq, Path(t))
    with tempfile.TemporaryDirectory(prefix="autoprueba_broll_vps_") as t:
        pruebas_corrida(br, bt, da, Path(t), estado_drive, ab.VISTO, None)
    srv_drive.shutdown()
    srv_proveedor.shutdown()
    srv_tiktok.shutdown()
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON {len(FALLAS)} FALLA(S):")
        for f in FALLAS:
            print("  -", f)
        sys.exit(1)
    print("AUTOPRUEBA OK")


if __name__ == "__main__":
    main()
