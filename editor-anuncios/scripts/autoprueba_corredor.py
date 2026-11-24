"""Autoprueba del corredor de anuncios del VPS (corredor_vps.py) y de su espejo de Drive (espejo_drive.py), SIN internet y sin gastar nada.

Usa un Drive de mentira (un servidor HTTP en esta misma maquina, el de autoprueba_drive.py con bajadas por rangos y cortes), un dash de mentira y procesos de mentira que
hacen lo que hacen los scripts del editor (dejan los mismos archivos y salen con los mismos codigos), asi se prueba LO QUE DECIDE el corredor: a quien toma, cuando espera, cuanto
puede gastar, como retoma, como pausa, que le cuenta al dash y que nunca hace (borrar en Drive, tocar un pedido ajeno, gastar sin tope, filtrar una llave).

Uso:  python scripts/autoprueba_corredor.py          (termina con AUTOPRUEBA OK, o con la lista de lo que fallo y codigo 1)
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autoprueba_drive as AD  # noqa: E402
from _comun import configurar_consola  # noqa: E402

EDITOR = Path(__file__).resolve().parents[1]
FALLAS: list[str] = []
N_OK = [0]


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    if cond:
        N_OK[0] += 1
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"  ->  {detalle}" if detalle else ""))


# ------------------------------------------------------------------ el Drive de mentira, con bajadas por rangos, cortes y un MD5 que miente
class EstadoPlus(AD.EstadoDrive):
    def reiniciar(self):
        super().reiniciar()
        self.md5_falso: dict[str, str] = {}
        self.peticiones: list[tuple[str, str]] = []

    def ficha(self, f):
        d = super().ficha(f)
        if f["id"] in getattr(self, "md5_falso", {}):
            d["md5Checksum"] = self.md5_falso[f["id"]]
        return d


class ManejadorPlus(AD.Manejador):
    def despachar(self, metodo):
        self.est.peticiones.append((metodo, urlparse(self.path).path))
        return super().despachar(metodo)

    def ver(self, fid, p):
        f = self.est.archivos.get(fid)
        if not f or p.get("alt") != "media":
            return super().ver(fid, p)
        datos = f["content"]
        m = re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range", ""))
        ini = int(m.group(1)) if m else 0
        if ini >= len(datos):
            return self.responder(416, None)
        resto = datos[ini:]
        corta = self.est.tomar("corta_bajada")
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(resto)))
        if m:
            self.send_header("Content-Range", f"bytes {ini}-{len(datos) - 1}/{len(datos)}")
        self.end_headers()
        if corta:
            self.wfile.write(resto[: max(1, len(resto) // 2)])
            self.wfile.flush()
            return self.cortar()
        self.wfile.write(resto)


def iniciar_drive():
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), ManejadorPlus)
    est = EstadoPlus()
    servidor.estado = est
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    os.environ["BROLL_DRIVE_URL"] = f"http://127.0.0.1:{servidor.server_port}"
    return servidor, est


# ------------------------------------------------------------------ el dash de mentira
class DashFalso:
    def __init__(self, fab):
        self.fab = fab
        self.pedidos: list[dict] = []
        self.ordenes: dict[tuple, dict] = {}
        self.llamadas: list[tuple] = []
        self.caido = False
        self.rechaza = False

    def poner(self, proyecto="mpruebaprueba01", anuncio="AD4", slug="fieltro", nombre="FIELTRO", tipo="tiktok", estado="pedido", correcciones=None, portada=True,
              enlace="https://www.facebook.com/ads/library/?id=9999000000247761", video_url="", tope=0.10, receta_extra=None, paises=("Colombia",), arranque="2026-10-23T11:00:00.000Z", entregables=None):
        receta = {"tipoAnuncio": tipo, "duracionMin": 25, "duracionMax": 60, "aprobacion": {"plan": "mostrar", "topeUsd": tope}, "gancho": {"modo": "nuevo-modelado", "segundos": 6}, "voz": {"velocidad": 1.1}}
        receta.update(receta_extra or {})
        datos = {"v": 1, "proyecto": {"id": proyecto, "nombre": nombre, "slug": slug, "paises": list(paises)},
                 "anuncio": {"id": anuncio, "tipo": tipo, "estado": estado, "referencia": {"videoUrl": video_url, "nota": ""}, "correcciones": correcciones or []},
                 "oferta": {"promesa": "Cose 15 figuras de fieltro", "promesaCorta": "", "vinetas": ["15 proyectos"], "ebooks": []},
                 "producto": {"portada": {"nombre": "portada.jpg", "url": "https://x.example/p.jpg"} if portada else None, "imagenes": [], "pdfs": [], "mockup": None,
                              "entregablesDrive": {"id": entregables, "url": "https://drive.google.com/drive/folders/" + entregables} if entregables else None},
                 "referencia": {"linkAnuncio": enlace, "linkPagina": "", "videoUrl": video_url}, "receta": receta}
        self.ordenes[(proyecto, anuncio)] = {"markdown": "# Pedido\n", "datos": datos}
        fila = {"proyecto": proyecto, "anuncio": anuncio, "nombre": nombre, "slug": slug, "tipo": tipo, "estado": estado, "paises": list(paises), "actualizadoEn": "2026-10-23T12:00:00Z", "arranque": arranque}
        self.pedidos = [x for x in self.pedidos if (x["proyecto"], x["anuncio"]) != (proyecto, anuncio)] + [fila]
        return datos

    def estado_de(self, proyecto, anuncio):
        return self.ordenes[(proyecto, anuncio)]["datos"]["anuncio"]["estado"]

    def llamar(self, metodo, ruta, cuerpo=None, tiempo=30, datos=None, fatal=True):
        self.llamadas.append((metodo, ruta, json.loads(json.dumps(cuerpo)) if cuerpo is not None else None))
        if self.caido:
            raise self.fab.ErrorDash("Sin conexion con el dash (ConnectionError). Revisa internet y DASH_URL en .env.")
        if self.rechaza:
            raise self.fab.ErrorDash("El dash rechazo la llave (DASH_CLAVE_EDITOR de .env). Pidele al dueño que corra scripts\\Setup-ClaveEditor.ps1 otra vez.")
        if metodo == "GET" and ruta == "":
            return {"ok": True, "pedidos": [p for p in self.pedidos if p["estado"] in ("pedido", "tomado", "en-edicion", "error")]}
        if metodo == "GET" and ruta.startswith("?proyecto="):
            q = {k: v[0] for k, v in parse_qs(ruta[1:]).items()}
            o = self.ordenes.get((q["proyecto"], q["anuncio"]))
            if not o:
                raise self.fab.ErrorDash("El dash contesto 404: Ese pedido no existe")
            return {"ok": True, "orden": json.loads(json.dumps(o))}
        if metodo == "POST" and isinstance(cuerpo, dict) and cuerpo.get("accion") == "avisar":
            k = (cuerpo["proyecto"], cuerpo["anuncio"])
            o = self.ordenes[k]["datos"]["anuncio"]
            if o["estado"] in ("aprobada", "cancelado"):
                raise self.fab.ErrorDash("El dash contesto 409: Ese pedido esta cerrado")
            o["estado"] = cuerpo["estado"]
            for p in self.pedidos:
                if (p["proyecto"], p["anuncio"]) == k:
                    p["estado"] = cuerpo["estado"]
            return {"ok": True, "anuncio": {"id": cuerpo["anuncio"], "estado": cuerpo["estado"]}}
        return {"ok": True}

    def avisos(self, estado=None):
        return [c[2] for c in self.llamadas if c[0] == "POST" and isinstance(c[2], dict) and c[2].get("accion") == "avisar" and (estado is None or c[2].get("estado") == estado)]


# ------------------------------------------------------------------ los procesos de mentira (hacen lo que hacen los scripts del editor)
class ProcesosFalsos:
    def __init__(self, cv, A, raiz: Path, tiempo: list, mapa_slug: dict):
        self.cv, self.A, self.raiz, self.tiempo, self.mapa = cv, A, raiz, tiempo, mapa_slug
        self.lanzados: list[list[str]] = []
        self.envs: list[dict] = []
        self.codigos: dict[str, int] = {}          # script -> codigo de salida que debe dar
        self.costos = {"guion_auto.py": 0.004, "voz_auto.py": 0.012, "gancho_ia.py": 0.0, "avatar_planear": 0.006, "avatar_generar": 0.27, "avatar_montar": 0.0}
        self.tipo = "tiktok"                       # en que carpeta deja el anuncio: anuncios/<oferta>/<tipo>/<AD>/ (los de avatar, «ia»)
        self.lento: set[str] = set()               # scripts que "se pasan del tiempo"
        self.cortar_en: str | None = None          # script durante el cual llega la orden de parar
        self.sin_referencia = False
        self.sin_video = False
        self.pausar_al_llegar_a: str | None = None
        self.por_hacer = None
        self.orden_json = "{}"                     # lo que `fabrica.py traer` deja en orden.json (las pruebas de entregables ya hechos le ponen la orden del dash)

    def anotar_costo(self, slug, ad, costo, etiqueta):
        if costo:
            with open(self.A.COSTOS, "a", encoding="utf-8") as f:
                f.write(json.dumps({"tipo": etiqueta, "producto": slug, "anuncio": ad, "costo": None, "costo_estimado": costo}) + "\n")

    def __call__(self, cmd, env, timeout_s, salida, cortar=lambda: False, cwd=None):
        self.lanzados.append(list(cmd))
        self.envs.append(dict(env))
        script = Path(cmd[2]).name
        args = cmd[3:]
        self.tiempo[0] += 60
        if script in self.lento:
            self.tiempo[0] += timeout_s + 1
            return self.cv.Corrida(None, True, False, [])
        if self.cortar_en == script:
            return self.cv.Corrida(None, False, True, [])
        if self.pausar_al_llegar_a == script:
            (self.raiz / "estado").mkdir(parents=True, exist_ok=True)
            (self.raiz / "estado" / "PAUSA").write_text("", encoding="utf-8")
        salida("    (proceso de mentira) " + script + " " + " ".join(args)[:100])
        codigo = self.codigos.get(script + " " + args[0], self.codigos.get(script, 0)) if script == "avatar_auto.py" else self.codigos.get(script, 0)
        slug = ad = None
        if script == "fabrica.py" and args[0] == "traer":
            proyecto, ad = args[1], args[2]
            slug = self.mapa[proyecto]
            base = self.raiz / "anuncios" / slug / self.tipo / ad
            base.mkdir(parents=True, exist_ok=True)
            if codigo == 0:
                (base / "orden.json").write_text(self.orden_json, encoding="utf-8")
                (base / "receta.json").write_text("{}", encoding="utf-8")
                if not self.sin_referencia:
                    ref = self.raiz / "public" / "productos" / slug / "referencia"
                    ref.mkdir(parents=True, exist_ok=True)
                    (ref / f"{ad}_referencia.mp4").write_bytes(b"video-de-referencia")
        elif script == "-c":                       # python -I -c <codigo> <origen> <destino>: el mockup de la oferta convertido en portada.jpg
            if codigo == 0:
                Path(args[2]).write_bytes(b"jpg-del-mockup-" + Path(args[1]).name.encode("utf-8"))
        elif script == "pdf_a_imagenes.py":
            slug, pdf = args[0], Path(args[args.index("--pdf") + 1])
            self.pdf_usado = pdf
            if codigo == 0 and pdf.is_file():
                carpeta = self.raiz / "public" / "productos" / slug
                (carpeta / "paginas").mkdir(parents=True, exist_ok=True)
                (carpeta / "portada.jpg").write_bytes(b"portada-de-" + pdf.name.encode("utf-8"))
                (carpeta / "paginas" / "p001.jpg").write_bytes(b"pagina-1")
        elif script == "broll_tiktok.py":
            slug = args[1]
            origen = Path(args[args.index("--drive") + 1]) / "B-rolls"
            destino = self.raiz / "public" / "productos" / slug / "broll-tiktok"
            destino.mkdir(parents=True, exist_ok=True)
            for v in origen.glob("tt_*.mp4"):
                shutil.copy2(v, destino / v.name)
        elif script == "emparejar_tomas.py":
            slug = args[0]
            (self.raiz / "public" / "productos" / slug / "broll-tiktok" / "indice.json").write_text("{}", encoding="utf-8")
        elif script in ("guion_auto.py", "voz_auto.py", "gancho_ia.py", "montaje_auto.py"):
            slug, ad = args[0], args[1]
            base = self.raiz / "anuncios" / slug / self.tipo / ad
            self.anotar_costo(slug, ad, self.costos.get(script, 0) if codigo == 0 else 0, "texto" if script == "guion_auto.py" else "voz")
            if codigo in (0, 3 if script == "voz_auto.py" else 0):
                if script == "guion_auto.py":
                    (base / "guion.json").write_text("{}", encoding="utf-8")
                elif script == "voz_auto.py":
                    (base / "audio").mkdir(exist_ok=True)
                    (base / "audio" / "voz.mp3").write_bytes(b"voz")
                    (base / "audio" / "voz_norm.wav").write_bytes(b"voz")
                    (base / "transcripcion.json").write_text("{}", encoding="utf-8")
            if script == "montaje_auto.py":
                (base / "final").mkdir(exist_ok=True)
                if codigo in (0, 2) and not self.sin_video:
                    (base / "final" / f"{slug}_{ad}.mp4").write_bytes(b"VIDEO-TERMINADO-" + os.urandom(64))
                inf = {"estado": "aprobado" if codigo == 0 else "con-fallas", "compuertas": {"duracion": {"ok": True}, "sin_franjas_negras": {"ok": codigo == 0}}}
                (base / "informe_calidad.json").write_text(json.dumps(inf), encoding="utf-8")
        elif script == "avatar_auto.py":
            accion, slug, ad = args[0], args[1], args[2]
            base = self.raiz / "anuncios" / slug / "ia" / ad
            (base / "avatar").mkdir(parents=True, exist_ok=True)
            self.anotar_costo(slug, ad, self.costos.get("avatar_" + accion, 0) if codigo == 0 else 0, "texto" if accion == "planear" else "video")
            if codigo == 0 and accion == "planear":
                (base / "avatar" / "plan.json").write_text("{}", encoding="utf-8")
            elif codigo == 0 and accion == "generar":
                (base / "avatar" / "generacion.json").write_text("{}", encoding="utf-8")
            elif accion == "montar":
                (base / "final").mkdir(exist_ok=True)
                if codigo in (0, 2) and not self.sin_video:
                    (base / "final" / f"{slug}_{ad}.mp4").write_bytes(b"VIDEO-DE-AVATAR-" + os.urandom(64))
                inf = {"estado": "aprobado" if codigo == 0 else "con-fallas", "compuertas": {"duracion": {"ok": True}, "sin_franjas_negras": {"ok": codigo == 0}}}
                (base / "informe_calidad.json").write_text(json.dumps(inf), encoding="utf-8")
        elif script == "fabrica.py" and args[0] == "entregar":
            slug, ad = args[1], args[2]
            archivo = Path(args[args.index("--archivo") + 1])
            nombre_oferta = self.por_hacer["nombre_carpeta"]
            destino = self.raiz / "drive" / "OFERTAS LATAM" / nombre_oferta / "ANUNCIOS"
            destino.mkdir(parents=True, exist_ok=True)
            if codigo == 0:
                shutil.copyfile(archivo, destino / archivo.name)
        return self.cv.Corrida(codigo, False, False, [])

    def scripts(self) -> list[str]:
        return [Path(c[2]).name + (" " + c[3] if Path(c[2]).name in ("fabrica.py", "avatar_auto.py") else "") for c in self.lanzados]


# ------------------------------------------------------------------ pruebas
def main() -> None:
    configurar_consola()
    servidor, est = iniciar_drive()
    import apimart as A
    import corredor_vps as cv
    import drive_api as da
    import espejo_drive as ed
    import fabrica as fab

    tmp = Path(tempfile.mkdtemp(prefix="autoprueba_corredor_"))
    try:
        pruebas_config_y_registro(cv, tmp)
        pruebas_espejo(da, ed, est, tmp)
        pruebas_proceso_real(cv, tmp)
        pruebas_corredor(cv, da, ed, fab, A, est, tmp)
        if (EDITOR / "vps" / "corredor-archivos.txt").is_file():
            pruebas_archivos(EDITOR)
        else:
            print()
            print("(Sin vps/corredor-archivos.txt: es la copia del servidor. Se salta la parte «Archivos del despliegue y la unidad», que revisa el manifiesto y los archivos del repo; se corre desde el repo.)")
    finally:
        servidor.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON FALLAS ({len(FALLAS)} de {N_OK[0] + len(FALLAS)}): " + "; ".join(FALLAS))
        sys.exit(1)
    print(f"AUTOPRUEBA OK ({N_OK[0]} comprobaciones)")


# ---- configuracion, llaves y registro
def pruebas_config_y_registro(cv, tmp: Path) -> None:
    print("Configuracion (corredor.json)")
    real = cv.cargar_config(EDITOR / "vps" / "corredor.json")
    comprobar(real["topes"]["usd_por_anuncio"] == 0.30 and real["topes"]["usd_por_dia"] == 1.00, "el corredor.json del repo trae los techos que aprobo el dueno (US$ 0,30 por anuncio y US$ 1,00 por dia)")
    comprobar(real["ventana_silencio"] == {"desde": "01:30", "hasta": "03:00"} and cv.DEFECTOS["ventana_silencio"] == {"desde": "", "hasta": ""},
              "el corredor del repo no trabaja de 01:30 a 03:00 hora de Bogota (ese rato es de los vigilantes de las 2 a. m.); sin corredor.json no hay ventana")
    comprobar(real["tipos_permitidos"] == ["tiktok", "ia"] and real["gancho_ia"] == "segun-receta", "anuncios tiktok (locucion + videos) y de avatar con IA («los de avatar tambien hazlos», dueno 2026-10-23); el gancho con IA segun la receta")
    comprobar(cv.DEFECTOS["tipos_permitidos"] == ["tiktok"], "sin corredor.json el defecto sigue siendo solo tiktok (el avatar se enciende a proposito)")
    comprobar(all(real["tiempos_max_min"][k] >= 15 for k in ("avatar_plan", "avatar_generar", "avatar_montaje")) and set(cv.LLAVES_POR_ETAPA) >= set(real["tiempos_max_min"]) - {"espejo"},
              "cada etapa nueva del avatar tiene su tiempo maximo y su fila en la tabla de llaves")
    base = json.loads((EDITOR / "vps" / "corredor.json").read_text(encoding="utf-8"))

    def con(**cambios) -> Path:
        d = json.loads(json.dumps(base))
        for k, v in cambios.items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                d[k].update(v)
            else:
                d[k] = v
        r = tmp / f"cfg_{len(list(tmp.glob('cfg_*')))}.json"
        r.write_text(json.dumps(d), encoding="utf-8")
        return r

    def falla(**cambios) -> str:
        try:
            cv.cargar_config(con(**cambios))
            return ""
        except cv.ErrorConfig as e:
            return str(e)

    comprobar("unknown" not in falla(), "una configuracion buena carga")
    comprobar("clave desconocida" in falla(topesito=1), "una clave que no existe (un error de escritura) se rechaza")
    comprobar("clave desconocida" in falla(topes={"usd_por_semana": 3}), "una clave que no existe dentro de un grupo tambien")
    comprobar(falla(topes={"usd_por_anuncio": 5}) != "", "un tope por anuncio de US$ 5 (pasa del techo posible) se rechaza")
    comprobar(falla(topes={"usd_por_dia": "mucho"}) != "", "un tope que no es numero se rechaza")
    comprobar(cv.DEFECTOS["topes"]["receta_sin_tope"] == "no-gasta", "sin corredor.json el defecto sigue siendo «sin tope en la receta, no gasta» (regla fija del editor)")
    comprobar(real["topes"]["receta_sin_tope"] == "no-gasta", "el corredor.json de la plantilla: una receta sin tope no gasta")
    comprobar(falla(topes={"receta_sin_tope": "no-gasta"}) == "" and falla(topes={"receta_sin_tope": "usa-el-techo"}) == "" and falla(topes={"receta_sin_tope": "siempre"}) != "" and falla(topes={"receta_sin_tope": True}) != "",
              "topes.receta_sin_tope es «no-gasta» o «usa-el-techo»; otra cosa se rechaza")
    comprobar(all(falla(ventana_silencio=v) != "" for v in ({"desde": "25:00", "hasta": "03:00"}, {"desde": "01:30", "hasta": ""}, {"desde": "01:30", "hasta": "01:30"}, {"desde": "1:30", "hasta": "3:00"}, {"desde": 130, "hasta": "03:00"}))
              and falla(ventana_silencio={"desde": "", "hasta": ""}) == "" and falla(ventana_silencio={"desde": "23:00", "hasta": "02:00"}) == "",
              "una ventana de silencio mal escrita (hora 25, una sola punta, desde = hasta, sin cero, un numero) se rechaza; vacia = sin ventana; y puede cruzar la medianoche")
    comprobar(falla(tipos_permitidos=["ia"]) == "" and falla(tipos_permitidos=["tiktok", "ia"]) == "", "el tipo «ia» (avatar) se puede permitir, solo o junto con tiktok")
    comprobar(falla(tipos_permitidos=["video"]) != "" and falla(tipos_permitidos=["tiktok", "tiktok"]) != "" and falla(tipos_permitidos=[]) != "" and falla(tipos_permitidos="ia") != "",
              "un tipo que no existe, uno repetido, una lista vacia o algo que no es lista se rechazan")
    comprobar(real["recursos"]["ram_disponible_min_gb"] >= 4.5, "no empieza un anuncio con menos de 4,5 GB de RAM libre (medido: la unidad necesita ~3,7 GB que no se pueden soltar; el servidor tiene 8 GB sin swap y comparte con el radar, el runner y Hermes)")
    comprobar(real["exigir_arranque"] is True and falla(exigir_arranque="si") != "", "exigir_arranque va encendido en el repo (solo toma ofertas que una persona mando arrancar) y debe ser true o false")
    comprobar(falla(carpeta_ofertas="https://example.com/x") != "", "la carpeta de las ofertas tiene que ser de Drive")
    comprobar(falla(ofertas_permitidas="algunas") != "", "ofertas_permitidas es «todas» o una lista")
    comprobar(falla(ofertas_permitidas=["fieltro", "Mal Nombre"]) != "", "un nombre de oferta con mayusculas o espacios se rechaza")
    comprobar(falla(tiempos_max_min={"montaje": 0}) != "", "un tiempo maximo de 0 minutos se rechaza")
    comprobar(falla(pasada_max_min=500) != "", "una pasada de mas de 175 minutos no cabe en el tiempo de la unidad (3 h) y se rechaza")
    try:
        cv.cargar_config(tmp / "no_existe.json")
        comprobar(False, "sin corredor.json se lanza ErrorConfig")
    except cv.ErrorConfig:
        comprobar(True, "sin corredor.json se lanza ErrorConfig")

    print("Llave del dash")
    def secreto(texto: str) -> Path:
        r = tmp / f"dash_{len(list(tmp.glob('dash_*')))}.env"
        r.write_text(texto, encoding="utf-8")
        return r

    llave = "LLAVE-DE-PRUEBA-0123456789abcdef"
    url, k = cv.leer_secreto_dash(secreto(f"DASH_URL=https://ventas.example.com/\nDASH_CLAVE_EDITOR={llave}\n"))
    comprobar(url == "https://ventas.example.com" and k == llave, "lee DASH_URL (sin la barra del final) y la llave")
    for texto, que in ((f"DASH_URL=http://ventas.example.com\nDASH_CLAVE_EDITOR={llave}\n", "una direccion sin https"), ("DASH_URL=https://a.example.com\nDASH_CLAVE_EDITOR=corta\n", "una llave corta"),
                       (f"DASH_URL=https://a.example.com\nDASH_CLAVE_EDITOR={llave} con espacio\n", "una llave con espacios"), (f"DASH_CLAVE_EDITOR={llave}\n", "sin DASH_URL")):
        try:
            cv.leer_secreto_dash(secreto(texto))
            comprobar(False, f"se rechaza {que}")
        except cv.ErrorConfig as e:
            comprobar(llave not in str(e), f"se rechaza {que} (y el mensaje no trae la llave)")

    print("Registro sin llaves")
    salida: list[str] = []
    reg = cv.Registro([llave], salida.append)
    reg(f"mandando DASH_CLAVE_EDITOR={llave} y Authorization: Bearer ya29.a0AfH6SMBx-secreto_largo.token y x-clave-editor: {llave} y sk-abcdefghijklmnopqrstuvwxyz0123")
    reg("1//0gRefrescoDeGoogle-abc_DEF")
    texto = "\n".join(salida)
    comprobar(llave not in texto and "ya29.a0AfH6SMBx" not in texto and "1//0gRefresco" not in texto and "abcdefghijklmnopqrstuvwxyz0123" not in texto, "el registro tapa la llave exacta y las formas conocidas de llaves de Google, Bearer y sk-")
    reg("x" * 5000)
    comprobar(max(len(s) for s in salida) < 700, "una linea enorme se recorta")
    reg.agregar_secreto("OTRA-LLAVE-NUEVA-123456")
    reg("lleva OTRA-LLAVE-NUEVA-123456 dentro")
    comprobar("OTRA-LLAVE-NUEVA" not in salida[-1], "una llave agregada despues tambien se tapa")


# ---- el proceso de verdad (sin shell, con tiempo maximo, corte y salida en vivo)
def pruebas_proceso_real(cv, tmp: Path) -> None:
    print("Procesos hijos de verdad")
    lineas: list[str] = []
    t0 = time.time()
    r = cv.correr_proceso([sys.executable, "-c", "print('hola'); print('adios')"], dict(os.environ), 30, lineas.append)
    comprobar(r.codigo == 0 and not r.tiempo_agotado and any("hola" in x for x in lineas) and any("adios" in x for x in lineas), "corre y pasa lo que imprime linea por linea")
    r = cv.correr_proceso([sys.executable, "-c", "import sys; print('mal'); sys.exit(3)"], dict(os.environ), 30, lineas.append)
    comprobar(r.codigo == 3, "devuelve el codigo de salida del script")
    t0 = time.time()
    r = cv.correr_proceso([sys.executable, "-c", "import time; print('empiezo', flush=True); time.sleep(60)"], dict(os.environ), 2, lineas.append)
    comprobar(r.tiempo_agotado and r.codigo is None and time.time() - t0 < 25, "se pasa del tiempo maximo: lo mata y lo dice", f"{time.time() - t0:.1f} s")
    marca = {"v": False}

    def cortar() -> bool:
        return marca["v"]

    def activar() -> None:
        time.sleep(1.5)
        marca["v"] = True

    threading.Thread(target=activar, daemon=True).start()
    t0 = time.time()
    r = cv.correr_proceso([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), 120, lineas.append, cortar)
    comprobar(r.cortado and time.time() - t0 < 25, "cuando le piden parar (systemd), corta el proceso hijo", f"{time.time() - t0:.1f} s")
    hijo = tmp / "hijo.py"
    hijo.write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\nprint('nieto lanzado', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    if hasattr(os, "killpg"):
        r = cv.correr_proceso([sys.executable, str(hijo)], dict(os.environ), 3, lineas.append)
        comprobar(r.tiempo_agotado, "con un proceso nieto, el tiempo maximo tambien lo termina (se mata el grupo entero)")
    r = cv.correr_proceso([sys.executable, "-c", "print('\\x1b[31mcolor\\x1b[0m ' + 'ñ' * 900)"], dict(os.environ), 30, lineas.append)
    comprobar(r.codigo == 0 and all(len(x) < 700 for x in lineas), "una salida rara (colores, letras raras, lineas largas) no rompe nada")


# ---- el espejo de Drive contra el Drive de mentira
def pruebas_espejo(da, ed, est, tmp: Path) -> None:
    print("Espejo de Drive")
    est.reiniciar()
    nuevo = lambda: da.Drive(dict(est.cred), dormir=lambda s: None, registrar=lambda *a, **k: None)  # noqa: E731
    RAIZ_ID, GOLDEN_ID = "idprueba_OFERTAS_LATAM_000001", "idprueba_MI_NEGOCIO_000000001"
    est.poner("MI NEGOCIO", AD.MIME_CARPETA, [], id_fijo=GOLDEN_ID)
    est.poner("OFERTAS LATAM", AD.MIME_CARPETA, [GOLDEN_ID], id_fijo=RAIZ_ID)
    otra = est.poner("DINAMICAS", AD.MIME_CARPETA, [RAIZ_ID])
    fieltro = est.poner("FIELTRO", AD.MIME_CARPETA, [RAIZ_ID], id_fijo="idprueba_FIELTRO_0000000001")
    est.poner("Notas sueltas.txt", "text/plain", [RAIZ_ID], b"x")
    brolls = est.poner("B-rolls", AD.MIME_CARPETA, [fieltro["id"]])
    videos = {}
    for i in range(8):
        n = f"tt_99990000000251905{i:02d}.mp4"
        videos[n] = os.urandom(300_000 + i * 1000)
        est.poner(n, "video/mp4", [brolls["id"]], videos[n])
    est.poner("fuentes.csv", "text/csv", [brolls["id"]], b"id,autor\n1,a\n")
    est.poner("LEEME.txt", "text/plain", [brolls["id"]], b"leeme")
    est.poner("nota.docx", "application/vnd.google-apps.document", [brolls["id"]], b"")
    est.poner("tt_9999999999999999999.exe", "application/octet-stream", [brolls["id"]], b"MZ")
    est.poner("..oculto.mp4", "video/mp4", [brolls["id"]], b"x")
    est.poner("sub/carpeta.mp4", "video/mp4", [brolls["id"]], b"x")
    est.poner("_descartados", AD.MIME_CARPETA, [brolls["id"]])
    r1 = est.poner("REFERENCIA", AD.MIME_CARPETA, [fieltro["id"]])
    r2 = est.poner("REFERENCIA 2", AD.MIME_CARPETA, [fieltro["id"]])
    est.poner("REF1_referencia.mp4", "video/mp4", [r1["id"]], b"viejo-anunciante")
    est.poner("REF1_referencia.mp4", "video/mp4", [r2["id"]], b"nuevo-anunciante-uno")
    est.poner("REF2_copy.txt", "text/plain", [r2["id"]], b"copy")

    local = tmp / "espejo"
    e = ed.Espejo(nuevo(), RAIZ_ID, local, registrar=lambda *a, **k: None, dormir=lambda s: None)
    comprobar(e.hallar_oferta("Fieltro", "fieltro")["id"] == fieltro["id"], "halla la carpeta de la oferta por nombre (sin importar mayusculas)")
    comprobar(ed.clave_nombre("Dinámicas para mujeres") == "DINAMICASPARAMUJERES", "clave_nombre quita acentos, espacios y simbolos")
    comprobar(e.hallar_oferta("No existe", "noexiste") is None, "una oferta sin carpeta da None")
    oferta = e.hallar_oferta("FIELTRO", "fieltro")
    comprobar(e.contar_brolls(oferta) == 8, "cuenta solo los videos tt_<id> (8 de los 14 archivos de B-rolls)", str(e.contar_brolls(oferta)))
    comprobar(e.referencia_vigente(oferta)[1] == "REFERENCIA 2", "la REFERENCIA vigente es la de numero mas alto")

    est.peticiones.clear()
    res = e.bajar_oferta(oferta)
    comprobar(not res["fallos"], "baja la oferta sin fallos", str(res["fallos"]))
    carpeta = local / "FIELTRO"
    bajados = sorted(p.name for p in (carpeta / "B-rolls").iterdir())
    comprobar(sorted(videos) + ["LEEME.txt", "fuentes.csv"] == sorted(bajados) or sorted(bajados) == sorted(list(videos) + ["LEEME.txt", "fuentes.csv"]), "de B-rolls baja los 8 videos, fuentes.csv y LEEME.txt, nada mas", str(bajados))
    comprobar(all((carpeta / "B-rolls" / n).read_bytes() == c for n, c in videos.items()), "cada video llega con el mismo contenido")
    comprobar(not any(p.name.endswith(".parte") for p in carpeta.rglob("*")), "no queda ningun .parte")
    comprobar(not (carpeta / "B-rolls" / "tt_9999999999999999999.exe").exists() and not list(carpeta.rglob("*oculto*")) and not list(carpeta.rglob("carpeta.mp4")), "no baja extensiones raras ni nombres peligrosos (oculto, con barra, ..)")
    comprobar(not (carpeta / "B-rolls" / "nota.docx").exists(), "no baja documentos de Google")
    comprobar((carpeta / "REFERENCIA 2" / "REF1_referencia.mp4").read_bytes() == b"nuevo-anunciante-uno" and not (carpeta / "REFERENCIA").exists(), "de la referencia baja SOLO la vigente (REFERENCIA 2), no la del anunciante anterior")
    comprobar(not any(m in ("DELETE", "PATCH", "PUT") for m, _ in est.peticiones), "bajar NO hace ninguna escritura en Drive (ni borrar, ni cambiar, ni subir)", str([x for x in est.peticiones if x[0] != "GET"]))

    est.peticiones.clear()
    res = e.bajar_oferta(oferta)
    comprobar(res["carpetas"]["B-rolls"]["bajados"] == 0 and res["carpetas"]["B-rolls"]["ya_estaban"] == 10, "la segunda vez no baja nada: todo estaba ya", str(res["carpetas"]["B-rolls"]))

    # el equipo borra un video de Drive (no sirve): desaparece del espejo
    primero = sorted(videos)[0]
    for f in list(est.archivos.values()):
        if f["name"] == primero:
            f["trashed"] = True
    res = e.bajar_oferta(oferta)
    comprobar(not (carpeta / "B-rolls" / primero).exists() and res["carpetas"]["B-rolls"]["quitados"] == 1, "un video que el equipo borro de Drive se quita del espejo (borrar en Drive es «este no sirve»)")
    comprobar(e.contar_brolls(oferta) == 7, "y ya no cuenta como b-roll")

    # una bajada que se corta a la mitad se retoma con Range y queda igual
    nuevo_video = os.urandom(900_000)
    est.poner("tt_9999000000000260599.mp4", "video/mp4", [brolls["id"]], nuevo_video)
    est.fallas["corta_bajada"] = 1
    res = e.bajar_oferta(oferta)
    comprobar((carpeta / "B-rolls" / "tt_9999000000000260599.mp4").read_bytes() == nuevo_video and not res["fallos"], "una bajada que se corta a la mitad se retoma donde iba y el video queda identico")
    # el MD5 de Drive no coincide: no se guarda
    malo = est.poner("tt_9999000000000260598.mp4", "video/mp4", [brolls["id"]], os.urandom(100_000))
    est.md5_falso[malo["id"]] = "0" * 32
    res = e.bajar_oferta(oferta)
    comprobar(not (carpeta / "B-rolls" / malo["name"]).exists() and any("MD5" in x for x in res["fallos"]), "un archivo cuyo MD5 no coincide no se guarda y se reporta")
    comprobar(not any(p.name.endswith(".parte") for p in carpeta.rglob("*")), "y no deja un .parte")
    # sin quitar nada cuando hubo fallos (para no borrar de mas por un error)
    n_antes = len(list((carpeta / "B-rolls").glob("tt_*.mp4")))
    comprobar(n_antes >= 7, "con un fallo en la bajada, los sobrantes NO se quitan (no se borra de mas por un error de red)")
    est.md5_falso.clear()
    est.archivos[malo["id"]]["trashed"] = True

    # tope de bytes
    chica = ed.Espejo(nuevo(), RAIZ_ID, tmp / "espejo2", registrar=lambda *a, **k: None, dormir=lambda s: None)
    r = chica.bajar_carpeta(brolls["id"], tmp / "espejo2" / "x", ext=ed.EXT_VIDEO, tope_bytes=700_000)
    comprobar(r["fallos"] and r["bajados"] <= 2, "con un tope de bytes por corrida, no pasa del tope y lo dice")

    print("Subir anuncios a Drive")
    comprobar(ed.nombre_seguro("fieltro_AD4.mp4") == "fieltro_AD4.mp4" and ed.nombre_seguro("../x.mp4") is None and ed.nombre_seguro(".x") is None and ed.nombre_seguro("a/b") is None and ed.nombre_seguro("") is None, "nombre_seguro deja pasar nombres normales y rechaza los peligrosos")
    video = tmp / "fieltro_AD4.mp4"
    video.write_bytes(b"VIDEO-FINAL-" + os.urandom(2_000_000))
    est.peticiones.clear()
    d = nuevo()
    s = ed.Espejo(d, RAIZ_ID, tmp / "espejo", registrar=lambda *a, **k: None, dormir=lambda s: None)
    sub = s.subir_anuncio(oferta, video)
    comprobar(sub["estado"] == "subido" and sub["name"] == "fieltro_AD4.mp4" and sub["enlace"].startswith("https://drive.google.com/file/d/"), "sube el video (crea ANUNCIOS si no existe) y devuelve su enlace")
    anuncios = [f for f in est.archivos.values() if f["name"] == "ANUNCIOS" and fieltro["id"] in f["parents"]]
    comprobar(len(anuncios) == 1, "crea UNA carpeta ANUNCIOS dentro de la oferta")
    en_drive = [f for f in est.archivos.values() if f["name"] == "fieltro_AD4.mp4" and anuncios[0]["id"] in f["parents"]]
    comprobar(len(en_drive) == 1 and en_drive[0]["content"] == video.read_bytes(), "el video queda en ANUNCIOS con el mismo contenido")
    comprobar(not d.cercado, "al terminar no queda ninguna carpeta con permiso de escritura (la cerca se cierra)")
    comprobar(not any(m in ("DELETE", "PATCH") for m, _ in est.peticiones), "subir NO borra ni modifica nada existente en Drive", str(est.peticiones))
    sub2 = s.subir_anuncio(oferta, video)
    comprobar(sub2["estado"] == "ya estaba" and sub2["id"] == sub["id"] and len([f for f in est.archivos.values() if f["name"] == "fieltro_AD4.mp4"]) == 1, "subir el mismo video otra vez no lo duplica")
    video.write_bytes(b"OTRO-VIDEO-" + os.urandom(1_000_000))
    sub3 = s.subir_anuncio(oferta, video)
    comprobar(sub3["name"] == "fieltro_AD4_v2.mp4" and sub3["estado"] == "subido", "otro video con el mismo nombre pero distinto contenido NO pisa al anterior: sube como _v2")
    comprobar(en_drive[0]["content"].startswith(b"VIDEO-FINAL-"), "y el primero sigue intacto")
    vacio = tmp / "vacio.mp4"
    vacio.write_bytes(b"")
    try:
        s.subir_anuncio(oferta, vacio)
        comprobar(False, "un video vacio no se sube")
    except ed.ErrorEspejo:
        comprobar(True, "un video vacio no se sube")
    est.fallas["md5_malo"] = True
    video.write_bytes(b"TERCERO-" + os.urandom(500_000))
    try:
        s.subir_anuncio(oferta, video)
        comprobar(False, "si Drive guarda otro contenido, no se da por entregado")
    except (da.ErrorDrive, ed.ErrorEspejo):
        comprobar(True, "si Drive guarda otro contenido, no se da por entregado")
    est.fallas.pop("md5_malo", None)

    print("Musica")
    musica = est.poner("BIBLIOTECA MUSICAL", AD.MIME_CARPETA, [GOLDEN_ID])
    cal = est.poner("calida", AD.MIME_CARPETA, [musica["id"]])
    est.poner("pista1.mp3", "audio/mpeg", [cal["id"]], os.urandom(50_000))
    est.poner("LICENCIAS.txt", "text/plain", [cal["id"]], b"licencias")
    est.poner("suelto.mp3", "audio/mpeg", [musica["id"]], os.urandom(1000))
    m = s.bajar_musica(tmp / "musica")
    comprobar(m["bajados"] == 1 and (tmp / "musica" / "calida" / "pista1.mp3").is_file() and not (tmp / "musica" / "calida" / "LICENCIAS.txt").exists(), "baja las pistas .mp3 de cada estilo (y nada mas)")
    comprobar(not any(p.name == "suelto.mp3" for p in (tmp / "musica").rglob("*")), "un mp3 suelto fuera de un estilo no se baja")
    est.reiniciar()


# ---- el corredor
def pruebas_corredor(cv, da, ed, fab, A, est, tmp: Path) -> None:
    import argparse

    print("Corredor: eleccion de pedidos, reclamo, etapas, dinero, retomar y pausa")
    LLAVE_DASH = "LLAVE-DEL-DASH-DE-PRUEBA-0123456789"
    LLAVE_APIMART = "sk-llave-de-apimart-de-prueba-0123456789"
    RAIZ_ID, GOLDEN_ID = "idprueba_OFERTAS_LATAM_000001", "idprueba_MI_NEGOCIO_000000001"
    cfg0 = json.loads(json.dumps(cv.DEFECTOS))
    cfg0["carpeta_ofertas"] = f"https://drive.google.com/drive/folders/{RAIZ_ID}"

    def mundo(nombre: str, brolls: int = 8, con_referencia: bool = False, cfg_extra: dict | None = None, avatar=None):
        est.reiniciar()
        est.poner("MI NEGOCIO", AD.MIME_CARPETA, [], id_fijo=GOLDEN_ID)
        est.poner("OFERTAS LATAM", AD.MIME_CARPETA, [GOLDEN_ID], id_fijo=RAIZ_ID)
        fieltro = est.poner("FIELTRO", AD.MIME_CARPETA, [RAIZ_ID])
        b = est.poner("B-rolls", AD.MIME_CARPETA, [fieltro["id"]])
        for i in range(brolls):
            est.poner(f"tt_99990000000251905{i:02d}.mp4", "video/mp4", [b["id"]], os.urandom(20_000))
        est.poner("fuentes.csv", "text/csv", [b["id"]], b"id\n")
        if avatar:          # MI NEGOCIO > OFERTAS LATAM > FIELTRO > AVATAR: una foto y la ficha; un dict cambia campos de la ficha ("_sin_fotos": True deja la carpeta sin la foto)
            carpeta_avatar = est.poner("AVATAR", AD.MIME_CARPETA, [fieltro["id"]])
            ficha = {"nombre": "lucia", "genero": "mujer", "fotos": ["foto1.jpg"], "autorizado": True, "aspecto": "apparent age 36, shoulder-length dark brown hair, warm brown eyes, natural makeup",
                     "voz": "warm, friendly mid-range voice", "acento": "neutral Colombian Spanish accent", "casa_base": "a bright living room with a beige sofa and green plants"}
            sin_fotos = False
            if isinstance(avatar, dict):
                avatar = dict(avatar)
                sin_fotos = bool(avatar.pop("_sin_fotos", False))
                ficha.update(avatar)
            if not sin_fotos:
                est.poner("foto1.jpg", "image/jpeg", [carpeta_avatar["id"]], os.urandom(4000))
            est.poner("avatar.json", "application/json", [carpeta_avatar["id"]], json.dumps(ficha).encode("utf-8"))
        if con_referencia:
            r = est.poner("REFERENCIA", AD.MIME_CARPETA, [fieltro["id"]])
            est.poner("REF1_referencia.mp4", "video/mp4", [r["id"]], b"v")
        raiz = tmp / nombre
        raiz.mkdir()
        (raiz / "estado").mkdir()
        A.COSTOS = raiz / "costos.jsonl"
        dash = DashFalso(fab)
        tiempo = [1000.0]
        reloj_f = datetime(2026, 10, 23, 12, 0, 0)
        procesos = ProcesosFalsos(cv, A, raiz, tiempo, {})
        procesos.por_hacer = {"nombre_carpeta": "FIELTRO"}
        procesos.mapa = {"mpruebaprueba01": "fieltro", "mpruebaprueba02": "dinamicas"}
        salida: list[str] = []
        reg = cv.Registro([LLAVE_DASH, LLAVE_APIMART], salida.append, lambda: reloj_f)
        deps = cv.Dependencias(llamar_dash=dash.llamar, fabricar_drive=lambda registrar: da.Drive(dict(est.cred), dormir=lambda s: None, registrar=lambda *a, **k: None), correr=procesos,
                               recursos=lambda: {"ram_gb": 6.0, "carga5": 0.2, "disco_gb": 70.0}, ahora=lambda: reloj_f, reloj=lambda: tiempo[0], dormir=lambda s: None,
                               en_pausa=lambda: (raiz / "estado" / "PAUSA").exists())
        cfg = json.loads(json.dumps(cfg0))
        for k, v in (cfg_extra or {}).items():
            if isinstance(v, dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        return {"raiz": raiz, "dash": dash, "procesos": procesos, "deps": deps, "cfg": cfg, "log": reg, "salida": salida, "tiempo": tiempo, "fieltro": fieltro, "brolls": b}

    def args(**kw):
        d = dict(simular=False, solo_cola=False, max_pedidos=None, tope_usd=None, sin_gancho=False, pedido=None, rehacer=False)
        d.update(kw)
        return argparse.Namespace(**d)

    def correr(m, **kw):
        c = cv.Corredor(m["cfg"], args(**kw), m["deps"], m["log"], raiz=m["raiz"])
        codigo = c.pasada()
        return codigo, c

    def estado_local(m, p="mpruebaprueba01", a="AD4"):
        return cv.leer_estado(p, a, m["raiz"] / "estado" / "pedidos")

    # la llave del dash llega por el entorno del proceso (secretos/dash.env). La de APIMart ya NO se usa en el servidor (todo pasa por el dash), pero se pone igual, con la carpeta de
    # credenciales de systemd, como si una unidad vieja todavia la trajera: ninguna etapa debe verla
    os.environ["APIMART_API_KEY"], os.environ["DASH_CLAVE_EDITOR"] = LLAVE_APIMART, LLAVE_DASH
    os.environ["CREDENTIALS_DIRECTORY"] = "/run/credentials/anuncios-corredor.service"

    # --- cola vacia
    m = mundo("vacia")
    codigo, _ = correr(m)
    comprobar(codigo == 0 and not m["procesos"].lanzados and not m["dash"].avisos(), "cola vacia: nada que hacer, no lanza nada y no le dice nada al dash")
    comprobar((m["raiz"] / "estado" / "ultima_corrida.json").is_file(), "deja el resumen en ultima_corrida.json")

    # --- el camino feliz, de punta a punta
    m = mundo("feliz")
    m["dash"].poner()
    codigo, c = correr(m)
    p = m["procesos"]
    if not p.lanzados:
        print("    (diagnostico) el corredor dijo:")
        for x in m["salida"][-14:]:
            print("      " + x)
    esperado = ["fabrica.py traer", "broll_tiktok.py", "emparejar_tomas.py", "guion_auto.py", "voz_auto.py", "gancho_ia.py", "montaje_auto.py", "fabrica.py entregar"]
    comprobar(p.scripts() == esperado, "el camino feliz corre los scripts del editor en este orden: traer, recoger b-roll, analizar, guion, voz, gancho (si el tope alcanza), montaje, entregar", str(p.scripts()))
    comprobar(codigo == 0 and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "termina con codigo 0 y el dash lo tiene en «lista»")
    av = m["dash"].avisos()
    comprobar([x["estado"] for x in av] == ["tomado", "lista"], "le avisa al dash dos veces: «tomado» y «lista» (el avance intermedio lo cuentan guion_auto, voz_auto y montaje_auto)", str([x["estado"] for x in av]))
    comprobar(av[0]["nota"].startswith("El servidor (VPS)"), "el aviso de «tomado» dice que lo hizo el servidor")
    lista = av[1]
    comprobar(lista.get("archivo") == "fieltro_AD4.mp4" and lista.get("drive", "").startswith("https://drive.google.com/file/d/") and abs(lista.get("costoUsd", 0) - 0.016) < 1e-6, "el aviso de «lista» lleva el nombre del video, el enlace de Drive y el costo real (0,004 + 0,012)", str(lista))
    anuncios = [f for f in est.archivos.values() if f["name"] == "ANUNCIOS"]
    comprobar(len(anuncios) == 1 and any(f["name"] == "fieltro_AD4.mp4" and anuncios[0]["id"] in f["parents"] for f in est.archivos.values()), "el video esta en MI NEGOCIO > OFERTAS LATAM > FIELTRO > ANUNCIOS")
    st = estado_local(m)
    comprobar(st and st["estado"] == "entregado" and all(st["etapas"][e]["ok"] for e in ("espejo", "traer", "reclamar", "material", "guion", "voz", "gancho", "montaje", "entrega")), "el estado local queda «entregado» con todas las etapas")
    comprobar(abs(st["gasto_usd"] - 0.016) < 1e-6 and abs(json.loads((m["raiz"] / "estado" / "gasto.json").read_text())["2026-10-23"] - 0.016) < 1e-6, "el gasto del pedido y el del dia quedan anotados")
    # el reclamo viene DESPUES de traer (hasta ahi no habia tocado el pedido)
    orden_llamadas = [("avisar:" + c[2]["estado"]) if (c[0] == "POST" and c[2]) else "leer" for c in m["dash"].llamadas]
    comprobar(orden_llamadas.index("avisar:tomado") > 0, "antes de reclamar solo leyo el dash")
    # las llaves
    texto_log = "\n".join(m["salida"])
    comprobar(LLAVE_DASH not in texto_log and LLAVE_APIMART not in texto_log, "ninguna llave aparece en el registro")
    todo_estado = "".join(f.read_text(encoding="utf-8", errors="replace") for f in (m["raiz"] / "estado").rglob("*.json"))
    comprobar(LLAVE_DASH not in todo_estado and LLAVE_APIMART not in todo_estado, "ninguna llave aparece en los archivos de estado")
    comprobar(all(("--tope-usd" in x) for x in p.lanzados if Path(x[2]).name in ("guion_auto.py", "voz_auto.py")), "guion_auto y voz_auto reciben siempre --tope-usd (nunca gastan por su cuenta)")
    topes = [float(x[x.index("--tope-usd") + 1]) for x in p.lanzados if Path(x[2]).name in ("guion_auto.py", "voz_auto.py")]
    comprobar(topes[0] == 0.1 and abs(topes[1] - (0.1 - 0.004)) < 1e-6, "al guion le da todo el tope de la receta (0,10) y a la voz lo que queda despues del guion (0,096)", str(topes))
    comprobar(not any(x[0] == "sh" or x[0] == "bash" for x in p.lanzados) and all(x[0] == sys.executable for x in p.lanzados), "todo se lanza como lista de argumentos con el Python del entorno, sin shell")
    # minimo privilegio: cada script ve solo la llave que usa
    nombre_de = lambda c: Path(c[2]).name + (" " + c[3] if Path(c[2]).name == "fabrica.py" else "")      # noqa: E731
    por_script = {nombre_de(c): e for c, e in zip(p.lanzados, p.envs)}
    comprobar(not [s for s, e in por_script.items() if "APIMART_API_KEY" in e], "NINGUNA etapa ve la llave de APIMart, aunque el corredor la tenga en su entorno: lo que cuesta pasa por el dash",
              str([s for s, e in por_script.items() if "APIMART_API_KEY" in e]))
    comprobar(not [s for s, e in por_script.items() if "CREDENTIALS_DIRECTORY" in e], "ninguna etapa ve la carpeta de credenciales de systemd (donde llegaba la llave de APIMart)",
              str([s for s, e in por_script.items() if "CREDENTIALS_DIRECTORY" in e]))
    comprobar(all("DASH_CLAVE_EDITOR" in por_script[s] for s in ("guion_auto.py", "gancho_ia.py")), "el guion y el gancho con IA (los que gastan) reciben la llave del dash: piden a APIMart por el dash")
    comprobar(not any(q == "apimart" for q2 in cv.LLAVES_POR_ETAPA.values() for q in q2) and set(cv.VARIABLE_DE_LLAVE.values()) == {"DASH_CLAVE_EDITOR"},
              "la tabla de llaves ya no reparte la de APIMart a ninguna etapa")
    comprobar(not any("DASH_CLAVE_EDITOR" in por_script[s] for s in ("broll_tiktok.py", "emparejar_tomas.py", "fabrica.py entregar")), "el b-roll y la entrega no ven la llave del dash")
    comprobar(all("DASH_CLAVE_EDITOR" in por_script[s] for s in ("fabrica.py traer", "guion_auto.py", "voz_auto.py", "gancho_ia.py", "montaje_auto.py")), "las etapas que le avisan al dash (traer, guion, voz, gancho, montaje) si ven su llave")
    comprobar(set(cv.LLAVES_POR_ETAPA) >= {"traer", "material", "guion", "voz", "gancho", "montaje", "entrega"}, "toda etapa que lanza un script esta en la tabla de llaves (una que no este no veria ninguna)")
    codigo2, _ = correr(m)
    comprobar(codigo2 == 0 and len(p.lanzados) == len(esperado), "una segunda pasada no vuelve a tocar nada (el dash ya lo tiene en «lista»)")

    # --- que NO toma
    def no_toma(nombre: str, motivo_esperado: str, que: str, **poner):
        m = mundo(nombre)
        m["dash"].poner(**poner)
        codigo, _ = correr(m)
        ok = not m["procesos"].lanzados and not m["dash"].avisos()
        t = "\n".join(m["salida"])
        comprobar(ok and motivo_esperado in t, que, t[-300:] if not ok or motivo_esperado not in t else "")
        return m

    no_toma("avatar", "avatar con IA", "no toma un pedido de avatar con IA (lo hace una persona)", tipo="ia")
    no_toma("correcciones", "correcciones", "no toma un pedido con correcciones del equipo (el servidor no las interpreta)", correcciones=[{"t": "2026-10-23", "por": "owner", "texto": "cambia el inicio"}])
    no_toma("tomado", "ya lo trabaja alguien", "no toma un pedido que el dash tiene «tomado» por otro (no se pisa a una persona ni a otra sesion)", estado="tomado")
    no_toma("enedicion", "ya lo trabaja alguien", "ni uno «en-edicion»", estado="en-edicion")
    no_toma("sinportada", "ebooks", "espera si los ebooks no estan listos (no hay portada)", portada=False)

    # --- entregables que YA VIENEN HECHOS: el equipo pego en la Fabrica la carpeta de Drive con los PDF; el servidor los baja y saca la portada del principal
    comprobar(cv.titulo_de_pdf("20_Desayunos_Navidenos.pdf") == "20 Desayunos Navidenos" and not re.search(r"[<>/\\]", cv.titulo_de_pdf("../../x<script>alert(1)</script>.pdf")) and cv.titulo_de_pdf("___.pdf") == "Ebook",
              "el titulo de un ebook sale del nombre del archivo, sin rutas ni etiquetas (texto de terceros)")
    comprobar(cv.elegir_principal([{"archivo": "a.pdf", "bytes": 9}, {"archivo": "Ebook_principal.pdf", "bytes": 1}, {"archivo": "z.pdf", "bytes": 5}])["archivo"] == "Ebook_principal.pdf"
              and cv.elegir_principal([{"archivo": "a.pdf", "bytes": 9}, {"archivo": "b.pdf", "bytes": 12}, {"archivo": "c.pdf", "bytes": 12}])["archivo"] == "c.pdf",
              "el principal es el que lo dice en su nombre; si ninguno, el mas pesado (y a igualdad el de nombre mas alto)")

    def con_entregables(nombre: str, pdfs: list, vinetas=None, imagenes: list | None = None):
        m = mundo(nombre)
        carpeta = est.poner("Entregables PDF", AD.MIME_CARPETA, [m["fieltro"]["id"]])
        for n, tam in pdfs:
            est.poner(n, "application/pdf", [carpeta["id"]], b"%PDF-1.7\n" + os.urandom(tam))
        for n, tam in imagenes or []:
            est.poner(n, "image/png", [carpeta["id"]], b"\x89PNG\r\n\x1a\n" + os.urandom(tam))
        datos = m["dash"].poner(portada=False, entregables=carpeta["id"])
        if vinetas is not None:
            datos["oferta"]["vinetas"] = vinetas
        m["procesos"].orden_json = json.dumps(datos)
        return m, carpeta

    m, carpeta = con_entregables("hechos", [("20_Desayunos_Navidenos.pdf", 2000), ("60_Postres_Navidenos.pdf", 9000), ("30_Ensaladas_y_Acompanantes.pdf", 4000), ("notas.txt", 10)], vinetas=[])
    codigo, _ = correr(m)
    p = m["procesos"]
    esperado_h = [esperado[0], "pdf_a_imagenes.py"] + esperado[1:]
    comprobar(p.scripts() == esperado_h and codigo == 0, "con los ebooks ya hechos NO espera la portada: despues de traer baja los PDF y saca la portada con pdf_a_imagenes.py, y sigue el camino normal", str(p.scripts()) + "\n" + "\n".join(m["salida"][-6:]))
    prod_h = m["raiz"] / "public" / "productos" / "fieltro"
    comprobar(sorted(f.name for f in (m["raiz"] / "drive" / "ENTREGABLES" / "fieltro").iterdir()) == ["20_Desayunos_Navidenos.pdf", "30_Ensaladas_y_Acompanantes.pdf", "60_Postres_Navidenos.pdf"],
              "baja solo los PDF de la carpeta (el .txt se queda en Drive) y los deja en drive/ENTREGABLES/<oferta>")
    comprobar(p.pdf_usado.name == "60_Postres_Navidenos.pdf" and (prod_h / "portada.jpg").is_file() and p.lanzados[[Path(c[2]).name for c in p.lanzados].index("pdf_a_imagenes.py")][-2:] == ["--ancho", "1200"],
              "la portada sale del PDF principal (el mas pesado si ninguno lo dice) y queda en public/productos/<oferta>/portada.jpg")
    i_pdf = [Path(c[2]).name for c in p.lanzados].index("pdf_a_imagenes.py")
    comprobar("DASH_CLAVE_EDITOR" not in p.envs[i_pdf] and "APIMART_API_KEY" not in p.envs[i_pdf] and "CREDENTIALS_DIRECTORY" not in p.envs[i_pdf],
              "el proceso que abre el PDF de un tercero no ve ninguna llave (ni la del dash ni la de APIMart)")
    orden_h = json.loads((m["raiz"] / "anuncios" / "fieltro" / "tiktok" / "AD4" / "orden.json").read_text(encoding="utf-8"))
    comprobar(sorted(e["titulo"] for e in orden_h["oferta"]["ebooks"]) == ["20 Desayunos Navidenos", "30 Ensaladas y Acompanantes", "60 Postres Navidenos"],
              "deja en orden.json los titulos de los ebooks (los que el guion puede nombrar)", str(orden_h["oferta"]))
    comprobar([e["clave"] for e in orden_h["oferta"]["ebooks"]].count("principal") == 1 and sorted(orden_h["oferta"]["vinetas"]) == ["20 Desayunos Navidenos", "30 Ensaladas y Acompanantes", "60 Postres Navidenos"],
              "marca un solo principal y, si la oferta no traia viñetas, usa los titulos como lo que recibe el cliente")
    comprobar(json.loads((prod_h / "entregables.json").read_text(encoding="utf-8"))["principal"] == "60_Postres_Navidenos.pdf", "anota en entregables.json cuales PDF y cual fue el principal")
    # otra pasada del mismo pedido (el estado local se borra): ya esta todo, no vuelve a sacar la portada pero si vuelve a poner los titulos que traer borro
    for f in (m["raiz"] / "estado" / "pedidos").glob("*"):
        f.unlink()
    m["dash"].poner(portada=False, entregables=carpeta["id"])
    p.lanzados.clear()
    p.orden_json = json.dumps(m["dash"].ordenes[("mpruebaprueba01", "AD4")]["datos"])
    correr(m)
    comprobar("pdf_a_imagenes.py" not in [Path(c[2]).name for c in p.lanzados] and "fabrica.py traer" in p.scripts()
              and len(json.loads((m["raiz"] / "anuncios" / "fieltro" / "tiktok" / "AD4" / "orden.json").read_text(encoding="utf-8"))["oferta"]["ebooks"]) == 3,
              "en otra pasada, con los mismos PDF, no vuelve a sacar la portada pero si vuelve a poner los titulos en orden.json", str(p.scripts()))

    # --- el mockup que el equipo puso en la misma carpeta (una imagen) es la portada de los anuncios; la primera pagina del PDF queda como pagina
    comprobar(cv.elegir_mockup([{"archivo": "00000000-0d6c.png", "bytes": 5}])["archivo"] == "00000000-0d6c.png"
              and cv.elegir_mockup([{"archivo": "a.png", "bytes": 5}, {"archivo": "b.png", "bytes": 9}]) is None
              and cv.elegir_mockup([{"archivo": "a.png", "bytes": 9}, {"archivo": "Mockup_navidad.png", "bytes": 1}])["archivo"] == "Mockup_navidad.png"
              and cv.elegir_mockup([]) is None,
              "el mockup es la unica imagen de la carpeta, o la que dice mockup/portada/cover/caratula en su nombre; con varias sin nombre claro no se adivina")
    m, _ = con_entregables("mockup", [("20_Desayunos_Navidenos.pdf", 2000), ("60_Postres_Navidenos.pdf", 9000)], imagenes=[("00000000-0000-4000-a000-000000000001.png", 3000)])
    codigo, _ = correr(m)
    p = m["procesos"]
    nombres_h = [Path(c[2]).name for c in p.lanzados]
    comprobar(nombres_h[:3] == ["fabrica.py", "pdf_a_imagenes.py", "-c"] and p.scripts() == [esperado_h[0], "pdf_a_imagenes.py", "-c"] + esperado_h[2:] and codigo == 0,
              "con un mockup en la carpeta: saca las paginas del PDF y despues convierte el mockup en la portada, y sigue el camino normal", str(p.scripts()))
    prod_m = m["raiz"] / "public" / "productos" / "fieltro"
    comprobar((prod_m / "portada.jpg").read_bytes().startswith(b"jpg-del-mockup-00000000") and (prod_m / "paginas" / "p001.jpg").is_file()
              and json.loads((prod_m / "entregables.json").read_text(encoding="utf-8"))["portada_de"].startswith("mockup: 00000000")
              and sorted(f.name for f in (m["raiz"] / "drive" / "ENTREGABLES" / "fieltro").iterdir()) == ["00000000-0000-4000-a000-000000000001.png", "20_Desayunos_Navidenos.pdf", "60_Postres_Navidenos.pdf"],
              "portada.jpg es el mockup (no la pagina del PDF), la pagina queda en paginas/, y entregables.json lo anota; la imagen no cuenta como ebook")
    i_mk = nombres_h.index("-c")
    comprobar("DASH_CLAVE_EDITOR" not in p.envs[i_mk] and "APIMART_API_KEY" not in p.envs[i_mk] and p.lanzados[i_mk][1] == "-I" and "'.tmp'" in p.lanzados[i_mk][3],
              "el mockup (archivo de un tercero) se convierte en un proceso aparte con python -I y sin ninguna llave")
    orden_mk = json.loads((m["raiz"] / "anuncios" / "fieltro" / "tiktok" / "AD4" / "orden.json").read_text(encoding="utf-8"))
    comprobar(len(orden_mk["oferta"]["ebooks"]) == 2, "la imagen del mockup no aparece como ebook en orden.json")
    m, _ = con_entregables("mockup_roto", [("principal.pdf", 500)], imagenes=[("mockup.png", 800)])
    m["procesos"].codigos["-c"] = 1
    codigo, _ = correr(m)
    t = "\n".join(m["salida"])
    comprobar(codigo == 0 and "no pude convertir el mockup" in t and (m["raiz"] / "public" / "productos" / "fieltro" / "portada.jpg").read_bytes().startswith(b"portada-de-principal.pdf") and "guion_auto.py" in m["procesos"].scripts(),
              "si el mockup no se puede convertir, avisa y la portada sale de la primera pagina del PDF (el pedido sigue)")
    m, _ = con_entregables("dos_imagenes", [("principal.pdf", 500)], imagenes=[("a.png", 800), ("b.png", 900)])
    correr(m)
    comprobar("-c" not in [Path(c[2]).name for c in m["procesos"].lanzados] and (m["raiz"] / "public" / "productos" / "fieltro" / "portada.jpg").read_bytes().startswith(b"portada-de-principal.pdf"),
              "con dos imagenes sin nombre claro no se adivina cual es el mockup: la portada sale del PDF principal")

    m = mundo("fuera")
    privada = est.poner("PRIVADO", AD.MIME_CARPETA, [GOLDEN_ID])
    est.poner("contrato.pdf", "application/pdf", [privada["id"]], b"%PDF-1.7\n" + os.urandom(500))
    m["procesos"].orden_json = json.dumps(m["dash"].poner(portada=False, entregables=privada["id"]))
    correr(m)
    t = "\n".join(m["salida"])
    comprobar(m["procesos"].scripts() == ["fabrica.py traer"] and "no esta dentro de la carpeta de la oferta" in t and not m["dash"].avisos() and not (m["raiz"] / "drive" / "ENTREGABLES").exists(),
              "una carpeta que NO cuelga de la carpeta de la oferta (un enlace del dash a otro lado) no se lee: no baja nada, no gasta y no toca el dash", t[-300:])
    m, _ = con_entregables("sinpdf", [("notas.txt", 20)])
    correr(m)
    t = "\n".join(m["salida"])
    comprobar(m["procesos"].scripts() == ["fabrica.py traer"] and "no tiene PDF" in t and not m["dash"].avisos(), "una carpeta de entregables sin PDF espera (no toca el dash)", t[-300:])
    m, _ = con_entregables("muchos", [(f"b{i}.pdf", 100) for i in range(9)])
    correr(m)
    comprobar("el maximo son 8" in "\n".join(m["salida"]) and "pdf_a_imagenes.py" not in m["procesos"].scripts(), "mas de 8 PDF no se aceptan (un principal y siete bonos, como el dash)")
    m, _ = con_entregables("rota", [("principal.pdf", 500)])
    m["procesos"].codigos["pdf_a_imagenes.py"] = 1
    correr(m)
    comprobar("no pudo sacar la portada" in "\n".join(m["salida"]) and not any(s in m["procesos"].scripts() for s in ("guion_auto.py", "voz_auto.py", "montaje_auto.py")),
              "si pdf_a_imagenes.py falla (falta pymupdf o el PDF esta danado) el pedido se detiene antes de gastar y antes de reclamarlo")
    m = mundo("normal_sin_enlace")
    m["dash"].poner()
    correr(m)
    comprobar("pdf_a_imagenes.py" not in m["procesos"].scripts() and (m["raiz"] / "anuncios" / "fieltro" / "tiktok" / "AD4" / "orden.json").read_text(encoding="utf-8") == "{}",
              "sin enlace de entregables la etapa no hace nada (los ebooks vienen del dash) y no toca orden.json")
    no_toma("sinarranque", "nadie ha mandado arrancar", "NO toma un pedido de una oferta que nadie mando arrancar (falta «Crear toda la oferta»), aunque tenga tope", arranque="")
    no_toma("arranque_raro", "nadie ha mandado arrancar", "una orden de arranque con texto raro (no es una hora) se trata como si no hubiera", arranque="<script>alert(1)</script>")
    no_toma("sintope", "sin OK de gasto", "NO toma un pedido sin tope de gasto en la receta (sin OK no gasta)", tope=0)
    no_toma("sinenlace", "video de referencia", "espera si no hay video de referencia ni enlace del anuncio", enlace="", video_url="")
    m = mundo("pocos", brolls=3)
    m["dash"].poner()
    correr(m)
    comprobar(not m["procesos"].lanzados and "hacen falta al menos 6" in "\n".join(m["salida"]), "espera si la oferta tiene menos de 6 videos de b-roll en Drive")
    m = mundo("sincarpeta")
    m["dash"].poner(nombre="OTRA OFERTA", slug="otraoferta")
    correr(m)
    comprobar(not m["procesos"].lanzados and "todavia no tiene carpeta en Drive" in "\n".join(m["salida"]), "espera si la oferta todavia no tiene carpeta en Drive")
    m = mundo("sin_exigir_arranque", cfg_extra={"exigir_arranque": False})
    m["dash"].poner(arranque="")
    correr(m)
    comprobar(m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "con exigir_arranque en false (corredor.json) toma el pedido aunque nadie haya mandado arrancar")
    m = mundo("pedido_explicito")
    m["dash"].poner(arranque="")
    correr(m, pedido=["mpruebaprueba01", "AD4"])
    comprobar(m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "con --pedido PROYECTO ANUNCIO (una orden explicita del dueno en la linea de comandos) no hace falta la orden de arranque")
    m = mundo("lista_blanca", cfg_extra={"ofertas_permitidas": ["dinamicas"]})
    m["dash"].poner()
    correr(m)
    comprobar(not m["procesos"].lanzados and "lista de ofertas permitidas" in "\n".join(m["salida"]), "respeta la lista de ofertas permitidas de corredor.json")
    m = mundo("referencia_en_drive", con_referencia=True)
    m["dash"].poner(enlace="https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&search_type=page&view_all_page_id=999900000271790")
    correr(m, simular=True)
    comprobar("LISTO" in "\n".join(m["salida"]), "con el enlace de la PAGINA de un anunciante vale la REFERENCIA que dejo el servidor en Drive")
    m = mundo("pagina_sin_referencia")
    m["dash"].poner(enlace="https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&search_type=page&view_all_page_id=999900000271790")
    correr(m, simular=True)
    comprobar("ESPERA" in "\n".join(m["salida"]) and "video de referencia" in "\n".join(m["salida"]), "con el enlace de la pagina pero sin REFERENCIA en Drive, espera")

    # --- simular: no toca nada
    m = mundo("simular")
    m["dash"].poner()
    codigo, _ = correr(m, simular=True)
    comprobar(codigo == 0 and not m["procesos"].lanzados and not m["dash"].avisos() and not (m["raiz"] / "estado" / "pedidos").exists() and not [f for f in est.archivos.values() if f["name"] == "ANUNCIOS"], "--simular dice que lo tomaria pero NO lanza nada, NO avisa al dash, NO escribe estado y NO escribe en Drive")
    comprobar("LISTO" in "\n".join(m["salida"]) and "simulacion" in "\n".join(m["salida"]), "y lo explica")
    codigo, _ = correr(m, solo_cola=True)
    comprobar(codigo == 0 and "Solo mirar la cola" in "\n".join(m["salida"]) and not m["procesos"].lanzados, "--solo-cola solo lista la cola")

    # --- dinero
    m = mundo("tope_techo")
    m["dash"].poner(tope=5.0)
    correr(m)
    topes = [float(x[x.index("--tope-usd") + 1]) for x in m["procesos"].lanzados if Path(x[2]).name == "guion_auto.py"]
    comprobar(topes == [0.3], "aunque la receta diga US$ 5, el techo duro de corredor.json (US$ 0,30 por anuncio) manda", str(topes))
    m = mundo("tope_arg")
    m["dash"].poner(tope=0)
    codigo, _ = correr(m, tope_usd=0.05)
    topes = [float(x[x.index("--tope-usd") + 1]) for x in m["procesos"].lanzados if Path(x[2]).name in ("guion_auto.py", "voz_auto.py")]
    comprobar(len(topes) == 2 and topes[0] == 0.05, "--tope-usd es el OK del dueno para esa corrida: con una receta sin tope, gasta hasta US$ 0,05", str(topes))
    m = mundo("techo_sin_tope", cfg_extra={"topes": {"receta_sin_tope": "usa-el-techo"}})
    m["dash"].poner(tope=0)
    correr(m)
    topes = [float(x[x.index("--tope-usd") + 1]) for x in m["procesos"].lanzados if Path(x[2]).name in ("guion_auto.py", "voz_auto.py")]
    comprobar(len(topes) == 2 and topes[0] == 0.3 and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "con topes.receta_sin_tope = «usa-el-techo» una receta sin tope gasta hasta el techo del servidor (US$ 0,30), no mas", str(topes))
    m = mundo("techo_con_tope", cfg_extra={"topes": {"receta_sin_tope": "usa-el-techo"}})
    m["dash"].poner(tope=0.05)
    correr(m)
    topes = [float(x[x.index("--tope-usd") + 1]) for x in m["procesos"].lanzados if Path(x[2]).name == "guion_auto.py"]
    comprobar(topes == [0.05], "y si la receta SI trae tope, manda el de la receta aunque el servidor use el techo", str(topes))
    m = mundo("techo_arg_cero", cfg_extra={"topes": {"receta_sin_tope": "usa-el-techo"}})
    m["dash"].poner(tope=0.20)
    correr(m, tope_usd=0.0)
    comprobar(not m["procesos"].lanzados and "sin OK de gasto" in "\n".join(m["salida"]), "--tope-usd 0 explicito sigue siendo «no gastes» aunque el servidor use el techo")
    m = mundo("techo_dia_agotado", cfg_extra={"topes": {"receta_sin_tope": "usa-el-techo", "usd_por_dia": 0.5}})
    (m["raiz"] / "estado" / "gasto.json").write_text(json.dumps({"2026-10-23": 0.49}), encoding="utf-8")
    m["dash"].poner(tope=0)
    correr(m)
    comprobar(not m["procesos"].lanzados and "tope de gasto de hoy" in "\n".join(m["salida"]), "usar el techo no salta el tope DIARIO: con el dia casi agotado no empieza nada")
    m = mundo("tope_dia", cfg_extra={"topes": {"usd_por_dia": 0.5}})
    guardar =m["raiz"] / "estado" / "gasto.json"
    guardar.write_text(json.dumps({"2026-10-23": 0.49}), encoding="utf-8")
    m["dash"].poner()
    correr(m)
    comprobar(not m["procesos"].lanzados and "tope de gasto de hoy" in "\n".join(m["salida"]), "con el tope del dia casi agotado (quedan US$ 0,01) no empieza nada")
    m = mundo("tope_dia2", cfg_extra={"topes": {"usd_por_dia": 0.5}})
    (m["raiz"] / "estado" / "gasto.json").write_text(json.dumps({"2026-10-23": 0.45}), encoding="utf-8")
    m["dash"].poner()
    correr(m)
    topes = [float(x[x.index("--tope-usd") + 1]) for x in m["procesos"].lanzados if Path(x[2]).name == "guion_auto.py"]
    comprobar(topes and abs(topes[0] - 0.05) < 1e-6, "con lo que queda del dia (0,05) al guion se le da como mucho eso, no el tope entero del pedido", str(topes))
    m = mundo("gancho_si")
    m["dash"].poner(tope=0.20)
    correr(m)
    comprobar("gancho_ia.py" in m["procesos"].scripts(), "con tope suficiente y gancho «nuevo-modelado» en la receta, corre gancho_ia.py")
    m = mundo("gancho_no")
    m["dash"].poner(tope=0.20)
    correr(m, sin_gancho=True)
    comprobar("gancho_ia.py" not in m["procesos"].scripts(), "con --sin-gancho no corre gancho_ia.py")
    m = mundo("gancho_nunca", cfg_extra={"gancho_ia": "nunca"})
    m["dash"].poner(tope=0.20)
    correr(m)
    comprobar("gancho_ia.py" not in m["procesos"].scripts(), "con gancho_ia = «nunca» en corredor.json no corre gancho_ia.py")
    m = mundo("gancho_cae")
    m["dash"].poner(tope=0.20)
    m["procesos"].codigos["gancho_ia.py"] = 3
    codigo, _ = correr(m)
    comprobar(codigo == 0 and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "si el gancho con IA no sale (codigo 3: falta tope), sigue con el b-roll y entrega igual")

    # --- reclamo: nada de tocar el dash antes de saber que puede
    m = mundo("sin_ref_tras_traer")
    m["dash"].poner()
    m["procesos"].sin_referencia = True
    codigo, _ = correr(m)
    st = estado_local(m)
    comprobar(not m["dash"].avisos() and st["estado"] == "esperando" and m["dash"].estado_de("mpruebaprueba01", "AD4") == "pedido", "si despues de traer no hay referencia, espera SIN reclamar: el dash lo sigue viendo en «pedido»")
    # --- alguien lo toma mientras se prepara
    m = mundo("carrera")
    m["dash"].poner()
    original = m["procesos"].__call__

    def tomar_mientras_trae(cmd, *a, **k):
        r = original(cmd, *a, **k)
        if Path(cmd[2]).name == "fabrica.py" and cmd[3] == "traer":
            m["dash"].ordenes[("mpruebaprueba01", "AD4")]["datos"]["anuncio"]["estado"] = "tomado"      # una persona (o el PC) lo tomo justo ahora
        return r

    m["deps"].correr = tomar_mientras_trae
    codigo, _ = correr(m)
    comprobar(not m["dash"].avisos() and "paso a «tomado»" in "\n".join(m["salida"]), "si mientras prepara el material otra persona lo toma, NO lo reclama ni lo pisa")

    # --- fallas de etapa
    def con_falla(nombre, script, codigo_salida=1, **extra):
        m = mundo(nombre)
        m["dash"].poner()
        m["procesos"].codigos[script] = codigo_salida
        for k, v in extra.items():
            setattr(m["procesos"], k, v)
        cod, _ = correr(m)
        return m, cod

    m, cod = con_falla("falla_guion", "guion_auto.py")
    av = m["dash"].avisos("error")
    comprobar(cod == 2 and len(av) == 1 and "guion" in av[0]["nota"] and m["dash"].estado_de("mpruebaprueba01", "AD4") == "error" and estado_local(m)["estado"] == "error", "si el guion falla: codigo 2, el dash lo tiene en «error» con una nota que dice la etapa, y queda anotado")
    comprobar("voz_auto.py" not in m["procesos"].scripts() and "montaje_auto.py" not in m["procesos"].scripts(), "y no sigue gastando en voz ni en render")
    antes = len(m["procesos"].lanzados)
    codigo2, _ = correr(m)
    comprobar(len(m["procesos"].lanzados) == antes and codigo2 == 0, "una segunda pasada no reintenta sola un pedido en error (lo reabre una persona)")
    m, cod = con_falla("lento_montaje", "montaje_auto.py", lento={"montaje_auto.py"})
    av = m["dash"].avisos("error")
    comprobar(cod == 2 and av and "tiempo maximo" in av[0]["nota"], "si el montaje se pasa del tiempo maximo: error con esa razon")
    m, cod = con_falla("montaje_con_fallas", "montaje_auto.py", 2)
    av = m["dash"].avisos("error")
    comprobar(cod == 2 and av and "sin_franjas_negras" in av[0]["nota"] and not m["dash"].avisos("lista"), "si el video queda pero no pasa las compuertas de calidad: error con la compuerta que fallo, NUNCA «lista»")
    comprobar("fabrica.py entregar" not in m["procesos"].scripts() and not [f for f in est.archivos.values() if f["name"] == "ANUNCIOS"], "y no se sube nada a Drive")
    m, cod = con_falla("voz_corta", "voz_auto.py", 3)
    comprobar("voz_auto.py" in m["procesos"].scripts() and cod == 0, "si la voz sale fuera de la duracion (codigo 3) pero existe, ajusta la velocidad y sigue; el control de calidad del montaje decide")
    ajuste = [x for x in m["procesos"].lanzados if Path(x[2]).name == "voz_auto.py"]
    comprobar(len(ajuste) >= 1, "(la voz se pidio)")
    m, cod = con_falla("sin_ok_voz", "voz_auto.py", 2)
    av = m["dash"].avisos("error")
    comprobar(cod == 2 and av and "falta el OK de gasto" in av[0]["nota"], "si voz_auto.py se niega por falta de tope (codigo 2), el pedido queda en error diciendo que falta el OK de gasto")
    m = mundo("sin_video")
    m["dash"].poner()
    m["procesos"].sin_video = True
    cod, _ = correr(m)
    comprobar(cod == 2 and "ningun video" in "\n".join(m["salida"]) and not m["dash"].avisos("lista"), "si el montaje no deja ningun video, no dice «lista»")

    # --- cortes de red: no se pierde lo hecho
    m = mundo("dash_caido")
    m["dash"].poner()
    m["dash"].caido = True
    codigo, _ = correr(m)
    comprobar(codigo == 3 and not m["procesos"].lanzados, "dash caido al leer la cola: codigo 3 y no hace nada")
    m = mundo("llave_mala")
    m["dash"].poner()
    m["dash"].rechaza = True
    codigo, _ = correr(m)
    comprobar(codigo == 1, "el dash rechaza la llave: codigo 1 (configuracion), no 3")
    m = mundo("drive_cae_al_entregar")
    m["dash"].poner()
    original_subir = ed.Espejo.subir_anuncio
    veces = [0]

    def subir_falla_una_vez(self, *a, **k):
        veces[0] += 1
        if veces[0] == 1:
            raise da.ErrorDrive("Drive contesto 503")
        return original_subir(self, *a, **k)

    ed.Espejo.subir_anuncio = subir_falla_una_vez
    try:
        codigo, _ = correr(m)
        st = estado_local(m)
        comprobar(codigo == 3 and st["estado"] == "en-curso" and st["etapas"]["montaje"]["ok"] and not st["etapas"].get("entrega") and not m["dash"].avisos("error") and not m["dash"].avisos("lista"),
                  "si Drive falla al entregar el video YA renderizado: codigo 3, el pedido sigue en curso (no se da por perdido ni por listo) y NO avisa error", str(st))
        lanzados_antes = len(m["procesos"].lanzados)
        codigo, _ = correr(m)
        nuevos = [Path(x[2]).name for x in m["procesos"].lanzados[lanzados_antes:]]
        comprobar(codigo == 0 and "montaje_auto.py" not in nuevos and "guion_auto.py" not in nuevos and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "la pasada siguiente RETOMA en la entrega: no vuelve a gastar ni a renderizar y lo deja «lista»", str(nuevos))
    finally:
        ed.Espejo.subir_anuncio = original_subir

    # --- retomar tras un corte (systemd paro la unidad)
    m = mundo("corte")
    m["dash"].poner()
    m["procesos"].cortar_en = "montaje_auto.py"
    codigo, _ = correr(m)
    st = estado_local(m)
    comprobar(codigo == 3 and st["estado"] == "en-curso" and st["etapas"]["voz"]["ok"] and not st["etapas"].get("montaje") and m["dash"].estado_de("mpruebaprueba01", "AD4") == "tomado",
              "si systemd corta la pasada en pleno montaje: codigo 3, queda «en-curso», con guion y voz hechos y el dash en «tomado»", str(st))
    m["procesos"].cortar_en = None
    antes = len(m["procesos"].lanzados)
    codigo, _ = correr(m)
    nuevos = [Path(x[2]).name for x in m["procesos"].lanzados[antes:]]
    comprobar(codigo == 0 and "guion_auto.py" not in nuevos and "voz_auto.py" not in nuevos and "montaje_auto.py" in nuevos and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "la pasada siguiente retoma desde el montaje: no repite guion ni voz (no vuelve a pagar) y lo deja «lista»", str(nuevos))
    comprobar(len(m["dash"].avisos("tomado")) == 1, "y no vuelve a reclamarlo")

    # --- pausa
    m = mundo("pausa_inicio")
    m["dash"].poner()
    (m["raiz"] / "estado" / "PAUSA").write_text("", encoding="utf-8")
    codigo, _ = correr(m)
    comprobar(codigo == 0 and not m["dash"].llamadas and not m["procesos"].lanzados and "En pausa" in "\n".join(m["salida"]), "con la PAUSA puesta no lee ni el dash: no hace nada")
    m = mundo("pausa_en_medio")
    m["dash"].poner()
    m["procesos"].pausar_al_llegar_a = "voz_auto.py"
    codigo, _ = correr(m)
    st = estado_local(m)
    scripts = m["procesos"].scripts()
    comprobar(codigo == 0 and "voz_auto.py" in scripts and "montaje_auto.py" not in scripts and st["estado"] == "en-curso" and m["dash"].estado_de("mpruebaprueba01", "AD4") == "tomado",
              "si la PAUSA llega mientras corre una etapa, esa etapa termina y se detiene ahi (antes del montaje), sin perder lo hecho", str(scripts))
    (m["raiz"] / "estado" / "PAUSA").unlink()
    m["procesos"].pausar_al_llegar_a = None
    codigo, _ = correr(m)
    comprobar(m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "al quitar la pausa, el pedido se retoma y termina")

    # --- recursos
    m = mundo("ocupado")
    m["dash"].poner()
    m["deps"].recursos = lambda: {"ram_gb": 1.0, "carga5": 0.3, "disco_gb": 70.0}
    codigo, _ = correr(m)
    comprobar(codigo == 2 and not m["procesos"].lanzados and not m["dash"].avisos(), "con menos de 3 GB de memoria libre no empieza (y no toca el dash)")
    m["deps"].recursos = lambda: {"ram_gb": 6.0, "carga5": 3.0, "disco_gb": 70.0}
    codigo, _ = correr(m)
    comprobar(not m["procesos"].lanzados, "con la carga de 5 minutos alta tampoco")
    m["deps"].recursos = lambda: {"ram_gb": 6.0, "carga5": 0.2, "disco_gb": 4.0}
    codigo, _ = correr(m)
    comprobar(not m["procesos"].lanzados, "ni con poco disco libre")

    # --- varios pedidos, tope por pasada, hostiles
    m = mundo("varios")
    for i, ad in enumerate(("AD4", "AD5", "AD6")):
        m["dash"].poner(anuncio=ad)
    m["cfg"]["pedidos_por_corrida"] = 2
    codigo, _ = correr(m)
    hechos = [a for a in ("AD4", "AD5", "AD6") if m["dash"].estado_de("mpruebaprueba01", a) == "lista"]
    comprobar(hechos == ["AD4", "AD5"], "hace como mucho los pedidos por pasada que dice corredor.json (2), el mas antiguo primero", str(hechos))
    m = mundo("hostil")
    m["dash"].poner(nombre="FIELTRO; rm -rf / $(reboot) `id` \n## Libertad", anuncio="AD4")
    codigo, _ = correr(m, simular=True)
    comprobar(codigo == 0, "un nombre de oferta con comandos y saltos de linea no rompe la evaluacion")
    m = mundo("hostil2")
    m["dash"].poner(nombre="FIELTRO")
    m["dash"].pedidos.append({"proyecto": "../../etc", "anuncio": "AD4", "nombre": "x", "slug": "fieltro", "tipo": "tiktok", "estado": "pedido"})
    m["dash"].pedidos.append({"proyecto": "mpruebaprueba01", "anuncio": "AD99", "nombre": "x", "slug": "fieltro", "tipo": "tiktok", "estado": "pedido"})
    m["dash"].pedidos.append({"proyecto": "mpruebaprueba01", "anuncio": "AD5", "nombre": "x", "slug": "../../root", "tipo": "tiktok", "estado": "pedido"})
    cola = cv.Dash(m["dash"].llamar).cola()
    comprobar([x["anuncio"] for x in cola] == ["AD4"], "de la cola del dash se descartan los pedidos con ids o nombres de producto raros (../../etc, AD99, slug con ..)")

    # --- notas al dash: solo frases fijas
    m, _ = con_falla("nota_fija", "guion_auto.py")
    nota = m["dash"].avisos("error")[0]["nota"]
    comprobar(len(nota) < 400 and "\n" not in nota and "rm -rf" not in nota, "la nota de error es corta, de una linea y sin texto de terceros")

    # --- rehacer y entregado
    m = mundo("rehacer")
    m["dash"].poner()
    correr(m)
    antes = len(m["procesos"].lanzados)
    correr(m, pedido=["mpruebaprueba01", "AD4"])
    comprobar(len(m["procesos"].lanzados) == antes and "ya lo entregue antes" in "\n".join(m["salida"]), "--pedido sobre uno ya entregado no lo rehace por accidente")
    m["dash"].ordenes[("mpruebaprueba01", "AD4")]["datos"]["anuncio"]["estado"] = "error"
    correr(m, pedido=["mpruebaprueba01", "AD4"], rehacer=True)
    comprobar(len(m["procesos"].lanzados) > antes, "con --pedido ... --rehacer sí lo vuelve a hacer entero")

    # --- limpieza de trabajo viejo
    m = mundo("limpieza")
    m["dash"].poner()
    correr(m)
    base = m["raiz"] / "anuncios" / "fieltro" / "tiktok" / "AD4"
    comprobar((base / "final").is_dir(), "(el trabajo del anuncio esta en disco)")
    m["deps"].ahora = lambda: datetime(2026, 11, 20, 12, 0, 0)
    c = cv.Corredor(m["cfg"], args(), m["deps"], m["log"], raiz=m["raiz"])
    c.limpiar_viejos()
    comprobar(not (base / "final").exists() and (base / "orden.json").is_file() and (base / "receta.json").is_file(), "pasados los dias, se borran los archivos pesados de un anuncio entregado (video, audio) pero se conservan la orden y la receta")
    # --- b-roll borrado de Drive
    m = mundo("brolls_borrados")
    trabajo = m["raiz"] / "public" / "productos" / "fieltro" / "broll-tiktok"
    trabajo.mkdir(parents=True)
    espejo_b = m["raiz"] / "drive" / "OFERTAS LATAM" / "FIELTRO" / "B-rolls"
    espejo_b.mkdir(parents=True)
    (trabajo / "tt_9999000000000260500.mp4").write_bytes(b"1")
    (trabajo / "tt_9999000000000260501.mp4").write_bytes(b"1")
    (trabajo / "fuentes.csv").write_text("x", encoding="utf-8")
    (espejo_b / "tt_9999000000000260500.mp4").write_bytes(b"1")
    c = cv.Corredor(m["cfg"], args(), m["deps"], m["log"], raiz=m["raiz"])
    c.quitar_brolls_borrados("fieltro", espejo_b)
    comprobar((trabajo / "tt_9999000000000260500.mp4").exists() and not (trabajo / "tt_9999000000000260501.mp4").exists() and (trabajo / "fuentes.csv").exists(), "un b-roll que el equipo borro de Drive se quita del producto; el que sigue y fuentes.csv se quedan")
    # --- ventana de silencio: de 01:30 a 03:00 (hora de Bogota) no empieza ni sigue pedidos, para no encimarse con los vigilantes de las 2 a. m. de la otra sesion
    print("Corredor: ventana de silencio")
    VS = {"ventana_silencio": {"desde": "01:30", "hasta": "03:00"}}
    m = mundo("silencio_dentro", cfg_extra=VS)
    m["dash"].poner()
    m["deps"].ahora = lambda: datetime(2026, 10, 23, 2, 0, 0)
    codigo, _ = correr(m)
    comprobar(codigo == 0 and not m["procesos"].lanzados and not m["dash"].llamadas and "Ventana de silencio" in "\n".join(m["salida"]), "a las 02:00 (dentro de la ventana) no hace nada: ni mira el dash ni lanza un solo proceso")
    m = mundo("silencio_cerca", cfg_extra=VS)
    m["dash"].poner()
    m["deps"].ahora = lambda: datetime(2026, 10, 23, 1, 0, 0)
    codigo, _ = correr(m)
    comprobar(codigo == 0 and not m["procesos"].lanzados and m["dash"].estado_de("mpruebaprueba01", "AD4") == "pedido" and "no empiezo pedidos nuevos" in "\n".join(m["salida"]),
              "a la 01:00 (la ventana empieza en 30 min y un pedido necesita 45) no EMPIEZA un pedido nuevo: no cabria antes del silencio, y el dash lo sigue viendo en «pedido»")
    m = mundo("silencio_lejos", cfg_extra=VS)
    m["dash"].poner()
    m["deps"].ahora = lambda: datetime(2026, 10, 23, 0, 40, 0)
    codigo, _ = correr(m)
    comprobar(m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "a las 00:40 (faltan 50 min para la ventana) si empieza y lo termina")
    m = mundo("silencio_medio", cfg_extra=VS)
    m["dash"].poner()
    reloj_dia = [datetime(2026, 10, 23, 0, 40, 0)]
    m["deps"].ahora = lambda: reloj_dia[0]
    original_silencio = m["procesos"].__call__

    def entra_la_ventana(cmd, *a, **k):
        r = original_silencio(cmd, *a, **k)
        if Path(cmd[2]).name == "voz_auto.py":
            reloj_dia[0] = datetime(2026, 10, 23, 1, 35, 0)         # mientras corre la voz llega la ventana
        return r

    m["deps"].correr = entra_la_ventana
    codigo, _ = correr(m)
    st = estado_local(m)
    scripts = m["procesos"].scripts()
    comprobar(codigo == 0 and "voz_auto.py" in scripts and "montaje_auto.py" not in scripts and st["estado"] == "en-curso" and "silencio" in st["nota"] and m["dash"].estado_de("mpruebaprueba01", "AD4") == "tomado",
              "si la ventana llega mientras corre una etapa, esa termina y se detiene antes de la siguiente (antes del render), sin perder lo hecho ni soltar el pedido", str(scripts))
    reloj_dia[0] = datetime(2026, 10, 23, 3, 40, 0)
    m["deps"].correr = original_silencio
    antes = len(m["procesos"].lanzados)
    codigo, _ = correr(m)
    nuevos = [Path(x[2]).name for x in m["procesos"].lanzados[antes:]]
    comprobar(codigo == 0 and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista" and "guion_auto.py" not in nuevos and "voz_auto.py" not in nuevos and "montaje_auto.py" in nuevos, "pasada la ventana retoma desde el montaje y lo termina, sin repetir guion ni voz", str(nuevos))
    m = mundo("silencio_manual", cfg_extra=VS)
    m["dash"].poner()
    m["deps"].ahora = lambda: datetime(2026, 10, 23, 2, 0, 0)
    codigo, _ = correr(m, pedido=["mpruebaprueba01", "AD4"])
    comprobar(m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "--pedido es una orden explicita de una persona y no la frena la ventana de silencio")

    # --- anuncios de AVATAR con IA (decision del dueno, 2026-10-23: «los de avatar tambien hazlos»)
    print("Corredor: anuncios de avatar con IA")
    IA = {"tipos_permitidos": ["tiktok", "ia"]}
    CORTO = {"duracionMin": 20, "duracionMax": 25}              # 3 clips de 8 s: lo mas largo que cabe en el techo de US$ 0,30

    def mundo_ia(nombre, tope=0.30, receta_extra=None, avatar=True, cfg_extra=None, **poner):
        m = mundo(nombre, brolls=0, cfg_extra={**IA, **(cfg_extra or {})}, avatar=avatar)          # sin b-roll: el avatar no lo usa
        m["procesos"].tipo = "ia"
        m["dash"].poner(tipo="ia", tope=tope, receta_extra=CORTO if receta_extra is None else receta_extra, **poner)
        return m

    m = mundo_ia("ia_feliz")
    codigo, c = correr(m)
    p = m["procesos"]
    if not p.lanzados:
        print("    (diagnostico) el corredor dijo:")
        for x in m["salida"][-14:]:
            print("      " + x)
    esperado_ia = ["fabrica.py traer", "guion_auto.py", "avatar_auto.py planear", "avatar_auto.py generar", "avatar_auto.py montar", "fabrica.py entregar"]
    comprobar(p.scripts() == esperado_ia, "un pedido de avatar corre: traer, guion (en clips), escenas, cuadros y clips, montaje y entrega (sin b-roll, sin voz aparte y sin gancho aparte)", str(p.scripts()))
    av = m["dash"].avisos()
    comprobar(codigo == 0 and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista" and [x["estado"] for x in av] == ["tomado", "lista"], "termina con codigo 0 y el dash lo tiene en «lista» (avisos: tomado y lista)", str([x["estado"] for x in av]))
    g = next(x for x in p.lanzados if Path(x[2]).name == "guion_auto.py")
    comprobar(g[g.index("--tipo") + 1] == "ia" and g[g.index("--clips") + 1] == "3" and "--tope-usd" in g, "al guion se le pide el modo avatar con 3 clips (los que caben en US$ 0,30) y su tope de gasto", str(g))
    pl = next(x for x in p.lanzados if Path(x[2]).name == "avatar_auto.py" and x[3] == "planear")
    desde = Path(pl[pl.index("--avatar-desde") + 1])
    comprobar(desde.name == "AVATAR" and (desde / "avatar.json").is_file() and (desde / "foto1.jpg").is_file(), "planear recibe la carpeta AVATAR que bajo el espejo de Drive (con avatar.json y la foto)", str(desde))
    ge = next(x for x in p.lanzados if Path(x[2]).name == "avatar_auto.py" and x[3] == "generar")
    comprobar(all("--tope-usd" in x for x in (pl, ge)) and float(ge[ge.index("--tope-usd") + 1]) <= 0.30 - 0.004 - 0.006 + 1e-6, "planear y generar reciben el tope de lo que queda (nunca gastan por su cuenta)", f"{pl} {ge}")
    ent = next(x for x in p.lanzados if Path(x[2]).name == "fabrica.py" and x[3] == "entregar")
    video = Path(ent[ent.index("--archivo") + 1])
    comprobar(video.parent.name == "final" and video.parent.parent.name == "AD4" and video.parent.parent.parent.name == "ia", "entregar toma el video de anuncios/<oferta>/ia/<AD>/final/", str(video))
    st = estado_local(m)
    comprobar(st["estado"] == "entregado" and all(st["etapas"][e]["ok"] for e in ("espejo", "traer", "reclamar", "guion", "avatar_plan", "avatar_generar", "avatar_montaje", "entrega")) and "material" not in st["etapas"]
              and st["avatar"]["clips"] == 3, "el estado local lo deja «entregado» con las etapas del avatar (y sin la del b-roll), recordando cuantos clips", str(st))
    espejo_oferta = m["raiz"] / "drive" / "OFERTAS LATAM" / "FIELTRO"
    comprobar((espejo_oferta / "AVATAR" / "avatar.json").is_file() and (espejo_oferta / "AVATAR" / "foto1.jpg").is_file() and not (espejo_oferta / "B-rolls").exists(),
              "el espejo baja la carpeta AVATAR (ficha y foto) y NO baja los b-rolls: el avatar no los usa, y un fallo al bajar uno no tiene por que frenarlo", str(sorted(p.name for p in espejo_oferta.iterdir())))
    comprobar(abs(st["gasto_usd"] - 0.28) < 1e-6 and abs(av[1].get("costoUsd", 0) - 0.28) < 1e-6 and av[1].get("archivo") == "fieltro_AD4.mp4", "el costo real (guion 0,004 + escenas 0,006 + cuadros y clips 0,27) llega al aviso de «lista» y queda por debajo del techo", str(av[1]))
    nombre_de2 = lambda c: Path(c[2]).name + (" " + c[3] if Path(c[2]).name in ("fabrica.py", "avatar_auto.py") else "")      # noqa: E731
    por_script = {nombre_de2(c): e for c, e in zip(p.lanzados, p.envs)}
    comprobar(all("DASH_CLAVE_EDITOR" in por_script[s] for s in ("guion_auto.py", "avatar_auto.py planear", "avatar_auto.py generar")), "el guion, las escenas y la generacion de cuadros y clips (los que gastan) reciben la llave del dash: piden a APIMart por el dash")
    comprobar(not any(k in e for e in por_script.values() for k in ("APIMART_API_KEY", "CREDENTIALS_DIRECTORY")),
              "ninguna etapa del avatar (ni el plan, ni la generacion, ni el montaje, ni traer, ni entregar) ve la llave de APIMart ni la carpeta de credenciales")
    comprobar("DASH_CLAVE_EDITOR" in por_script["avatar_auto.py montar"] and "DASH_CLAVE_EDITOR" in por_script["avatar_auto.py planear"] and "DASH_CLAVE_EDITOR" not in por_script["fabrica.py entregar"], "las etapas del avatar que le cuentan el avance al dash ven su llave; la entrega no")
    texto_log = "\n".join(m["salida"])
    comprobar(LLAVE_DASH not in texto_log and LLAVE_APIMART not in texto_log, "ninguna llave aparece en el registro del pedido de avatar")
    codigo2, _ = correr(m)
    comprobar(codigo2 == 0 and len(p.lanzados) == len(esperado_ia), "una segunda pasada no vuelve a tocar nada (el dash ya lo tiene en «lista»)")
    # limpieza: pasados los dias se borran tambien los cuadros, los clips y la base del avatar
    base_ia = m["raiz"] / "anuncios" / "fieltro" / "ia" / "AD4"
    for sub in ("cuadros", "clips"):
        (base_ia / sub).mkdir(exist_ok=True)
        (base_ia / sub / "x.png").write_bytes(b"1")
    (base_ia / "base.mp4").write_bytes(b"1")
    (base_ia / "voz_norm.wav").write_bytes(b"1")
    m["deps"].ahora = lambda: datetime(2026, 11, 20, 12, 0, 0)
    cc = cv.Corredor(m["cfg"], args(), m["deps"], m["log"], raiz=m["raiz"])
    cc.limpiar_viejos()
    comprobar(not any((base_ia / x).exists() for x in ("final", "cuadros", "clips", "base.mp4", "voz_norm.wav")) and (base_ia / "orden.json").is_file(), "pasados los dias se borran el video, los cuadros, los clips y la base del anuncio de avatar, y se conservan la orden y la receta")

    m = mundo_ia("ia_simular")
    codigo, _ = correr(m, simular=True)
    t_sim = "\n".join(m["salida"])
    comprobar(codigo == 0 and not m["procesos"].lanzados and not m["dash"].avisos() and "avatar: 3 clips de 8 s" in t_sim and "0.29" in t_sim, "--simular dice cuantos clips y cuanto costaria (3 clips, ≈ US$ 0,29) sin lanzar nada ni avisar al dash", t_sim[-300:])

    # --- el dinero: si el avatar mas corto que acepta la receta no cabe en el tope, NO se corre y se le dice a una persona con las cifras
    m = mundo_ia("ia_caro", receta_extra={})                                  # receta de 25 a 60 s (la de fabrica): minimo 4 clips = US$ 0,38 > US$ 0,30
    codigo, _ = correr(m)
    av = m["dash"].avisos()
    comprobar(codigo == 0 and not m["procesos"].lanzados and len(av) == 1 and av[0]["estado"] == "error" and "0.38" in av[0]["nota"] and "0.30" in av[0]["nota"] and "4 clips" in av[0]["nota"],
              "si el avatar mas corto que acepta la receta cuesta mas que el tope (4 clips = US$ 0,38 > 0,30): NO corre, no gasta y le deja al dash UNA nota con las cifras", str(av))
    comprobar(m["dash"].estado_de("mpruebaprueba01", "AD4") == "error" and len(av[0]["nota"]) <= 390 and "\n" not in av[0]["nota"], "el dash lo tiene en «error» con esa nota corta (queda a la vista del equipo)")
    codigo, _ = correr(m)
    comprobar(len(m["dash"].avisos()) == 1 and not m["procesos"].lanzados, "la pasada siguiente no repite la nota ni lo toma (espera a que una persona decida)")
    m = mundo_ia("ia_techo", tope=0.50, receta_extra={})
    codigo, _ = correr(m, pedido=["mpruebaprueba01", "AD4"], tope_usd=0.50)
    comprobar(not m["procesos"].lanzados and len(m["dash"].avisos("error")) == 1, "aunque la receta (US$ 0,50) o --tope-usd digan mas, el techo duro del servidor (US$ 0,30 por anuncio) manda: el corredor no sube el tope por su cuenta, tampoco con --pedido")
    m = mundo_ia("ia_techo_subido", tope=0.50, receta_extra={}, cfg_extra={"topes": {"usd_por_anuncio": 0.50}})
    codigo, _ = correr(m)
    g = next((x for x in m["procesos"].lanzados if Path(x[2]).name == "guion_auto.py"), None)
    comprobar(g is not None and g[g.index("--clips") + 1] == "5" and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista", "si el dueno sube el techo en corredor.json (US$ 0,50), la receta de 25 a 60 s se hace con 5 clips (≈ US$ 0,47)", str(g))
    m = mundo_ia("ia_dia")
    (m["raiz"] / "estado" / "gasto.json").write_text(json.dumps({"2026-10-23": 0.85}), encoding="utf-8")
    codigo, _ = correr(m)
    comprobar(codigo == 0 and not m["procesos"].lanzados and not m["dash"].avisos() and "ESPERA" in "\n".join(m["salida"]) and "tope diario" in "\n".join(m["salida"]),
              "si el tope del dia no alcanza para un avatar (quedan US$ 0,15), ESPERA a otro dia sin molestar al dash: no es cosa de una persona")

    # --- el avatar tiene que estar listo y autorizado en Drive antes de tocar nada
    for nombre, avatar, buscado in (("ia_sin_avatar", False, "AVATAR"), ("ia_no_autorizado", {"autorizado": False}, "autorizado"), ("ia_sin_foto", {"_sin_fotos": True}, "fotos"),
                                    ("ia_ficha_ejemplo", {"aspecto": "Apparent age, hair and eyes of the avatar"}, "ejemplo")):
        m = mundo_ia(nombre, avatar=avatar)
        codigo, _ = correr(m)
        av = m["dash"].avisos()
        comprobar(codigo == 0 and not m["procesos"].lanzados and len(av) == 1 and av[0]["estado"] == "error" and buscado in av[0]["nota"] and m["dash"].estado_de("mpruebaprueba01", "AD4") == "error",
                  f"avatar no listo ({nombre[3:]}): no lanza nada ni gasta, y le dice al equipo por que (la nota nombra «{buscado}»)", str(av))
    m = mundo_ia("ia_nota_cae")
    m["dash"].llamar_original = m["dash"].llamar

    def llamar_sin_avisos(metodo, ruta, *a, **k):
        if metodo == "POST":
            raise fab.ErrorDash("Sin conexion con el dash (ConnectionError). Revisa internet y DASH_URL en .env.")
        return m["dash"].llamar_original(metodo, ruta, *a, **k)

    m["deps"].llamar_dash = llamar_sin_avisos
    m["dash"].poner(tipo="ia", tope=0.30, receta_extra={})
    codigo, _ = correr(m)
    comprobar(codigo == 0 and not m["procesos"].lanzados and m["dash"].estado_de("mpruebaprueba01", "AD4") == "pedido" and "no pude dejarle la nota" in "\n".join(m["salida"]),
              "si el dash no recibe la nota, no se rompe nada: el pedido sigue en «pedido» y se intenta de nuevo en la siguiente pasada")
    m["deps"].llamar_dash = m["dash"].llamar_original
    codigo, _ = correr(m)
    comprobar(len(m["dash"].avisos("error")) == 1, "y cuando el dash vuelve, la nota llega")

    # --- tipos que el servidor no mezcla
    m = mundo("solo_ia", cfg_extra={"tipos_permitidos": ["ia"]})
    m["dash"].poner()
    codigo, _ = correr(m)
    comprobar(not m["procesos"].lanzados and not m["dash"].avisos() and "tipo «tiktok»" in "\n".join(m["salida"]), "con corredor.json permitiendo solo «ia», un pedido tiktok se deja")
    m = mundo_ia("ia_receta_tiktok", receta_extra={"tipoAnuncio": "tiktok"})
    codigo, _ = correr(m)
    comprobar(not m["procesos"].lanzados and not m["dash"].avisos() and "no es el del pedido" in "\n".join(m["salida"]), "si la receta dice tiktok y el pedido ia (no cuadran), no lo toca")

    # --- fallas de etapa del avatar: error con una frase que dice cual, nunca «lista», y no se sigue gastando
    def ia_con_falla(nombre, clave, codigo_salida):
        m = mundo_ia(nombre)
        m["procesos"].codigos[clave] = codigo_salida
        cod, _ = correr(m)
        return m, cod, m["dash"].avisos("error")

    m, cod, av = ia_con_falla("ia_falla_plan", "avatar_auto.py planear", 2)
    comprobar(cod == 2 and len(av) == 1 and "avatar" in av[0]["nota"] and "avatar_auto.py generar" not in m["procesos"].scripts(), "si planear sale con 2 (falta el avatar o el guion): error y no genera nada", str(av))
    m, cod, av = ia_con_falla("ia_falla_generar", "avatar_auto.py generar", 5)
    comprobar(cod == 2 and len(av) == 1 and "incompleta" in av[0]["nota"] and "avatar_auto.py montar" not in m["procesos"].scripts() and not m["dash"].avisos("lista"), "si la generacion queda incompleta (5): error diciendolo, no monta y nunca «lista»", str(av))
    m, cod, av = ia_con_falla("ia_generar_no_cabe", "avatar_auto.py generar", 3)
    comprobar(cod == 2 and len(av) == 1 and "no cabe en el tope" in av[0]["nota"], "si lo que falta generar no cabe en el tope (3): error con esa razon", str(av))
    m, cod, av = ia_con_falla("ia_falla_montar", "avatar_auto.py montar", 2)
    comprobar(cod == 2 and len(av) == 1 and "sin_franjas_negras" in av[0]["nota"] and not m["dash"].avisos("lista") and "fabrica.py entregar" not in m["procesos"].scripts() and not [f for f in est.archivos.values() if f["name"] == "ANUNCIOS"],
              "si el video del avatar queda pero no pasa las compuertas: error con la compuerta que fallo, NUNCA «lista», y no se sube nada a Drive", str(av))
    m = mundo_ia("ia_poco_tope_para_generar")
    m["procesos"].costos["avatar_planear"] = 0.25          # las escenas "costaron" mucho: ya no alcanza ni un cuadro con su clip (US$ 0,09)
    codigo, _ = correr(m)
    av = m["dash"].avisos("error")
    comprobar(codigo == 2 and len(av) == 1 and "ya no queda tope" in av[0]["nota"] and "avatar_auto.py generar" not in m["procesos"].scripts(), "si despues de las escenas no queda para un cuadro y un clip, no lanza la generacion: error diciendo que no queda tope", str(av))

    # --- pausa y retomar: lo que ya se pago no se paga otra vez, y la regla de costo no se cambia a la mitad
    m = mundo_ia("ia_pausa")
    m["procesos"].pausar_al_llegar_a = "avatar_auto.py"           # la PAUSA llega mientras corren las escenas
    codigo, _ = correr(m)
    st = estado_local(m)
    scripts = m["procesos"].scripts()
    comprobar(codigo == 0 and "avatar_auto.py planear" in scripts and "avatar_auto.py generar" not in scripts and st["estado"] == "en-curso" and st["avatar"]["clips"] == 3 and m["dash"].estado_de("mpruebaprueba01", "AD4") == "tomado",
              "si la PAUSA llega durante las escenas, esa etapa termina y se detiene (antes de gastar en cuadros y clips), recordando los 3 clips", str(scripts))
    (m["raiz"] / "estado" / "PAUSA").unlink()
    m["procesos"].pausar_al_llegar_a = None
    m["dash"].poner(tipo="ia", tope=0.30, receta_extra={}, estado="tomado")          # entre tanto la receta cambio a 25-60 s (que hoy no cabria)
    antes = len(m["procesos"].lanzados)
    codigo, _ = correr(m)
    nuevos = [n for n in m["procesos"].scripts()[antes:]]
    comprobar(codigo == 0 and m["dash"].estado_de("mpruebaprueba01", "AD4") == "lista" and nuevos == ["fabrica.py traer", "avatar_auto.py generar", "avatar_auto.py montar", "fabrica.py entregar"] and len(m["dash"].avisos("tomado")) == 1,
              "al reanudar, retoma desde los cuadros y clips: no repite guion ni escenas, no vuelve a pedirle permiso al costo (ya empezo) y no lo reclama otra vez", str(nuevos))

    # --- permiso de Drive
    m = mundo("drive_sin_permiso")
    m["dash"].poner()
    m["deps"].fabricar_drive = lambda registrar: da.Drive(dict(est.cred, refresh_token="TOKEN-REVOCADO-0123456789"), dormir=lambda s: None, registrar=lambda *a, **k: None)
    codigo, _ = correr(m)
    comprobar(codigo == 4 and not m["dash"].avisos() and not m["procesos"].lanzados, "si Google ya no acepta el permiso de Drive: codigo 4 y no le toca el pedido al dash")


# ---- archivos del repo: lo que se despliega, la unidad y los guiones del servidor
def pruebas_archivos(editor: Path) -> None:
    print("Archivos del despliegue y la unidad de systemd")
    lista = []
    for linea in (editor / "vps" / "corredor-archivos.txt").read_text(encoding="utf-8").splitlines():
        t = linea.strip()
        if t and not t.startswith("#"):
            lista.append(t.split())
    comprobar(all(len(x) == 3 and x[2] in ("644", "755") for x in lista), "cada linea del manifiesto es «origen destino modo»")
    comprobar(all((editor / x[0]).is_file() for x in lista), "todos los archivos del manifiesto existen", str([x[0] for x in lista if not (editor / x[0]).is_file()]))
    destinos = [x[1] for x in lista]
    comprobar(len(destinos) == len(set(destinos)), "no hay destinos repetidos")
    comprobar(not any(re.search(r"(^|/)(\.env|google\.json|dash\.env|apimart\.key|secretos)(/|$)|\.pem$|\.key$", d) for d in destinos), "el manifiesto no incluye ninguna llave ni carpeta de secretos")
    comprobar(not any(".." in d or d.startswith("/") for d in destinos), "ningun destino sale de /opt/anuncios")
    # los scripts de python que hacen falta (clausura de imports locales) estan en el manifiesto
    scripts = editor / "scripts"
    modulos = {f.stem for f in scripts.glob("*.py")}
    en_manifiesto = {Path(d).stem for d in destinos if d.startswith("scripts/") and d.endswith(".py")}
    semillas = ["corredor_vps", "espejo_drive", "fabrica", "guion_auto", "voz_auto", "gancho_ia", "montaje_auto", "emparejar_tomas", "transcribir", "mezclar_audio", "aplicar_receta", "validar_videodata",
                "anotar_historial", "broll_tiktok", "nuevo_producto", "drive_api", "crear_sfx", "apimart", "apimart_chat", "texto_quemado", "clip_local", "hoja_fotogramas", "indexar_broll", "prueba_servidor", "avatar_auto", "avatar_comun", "unir_clips"]
    visto, pila = set(), list(semillas)
    while pila:
        m = pila.pop()
        if m in visto:
            continue
        visto.add(m)
        texto = (scripts / f"{m}.py").read_text(encoding="utf-8")
        for r in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", texto, re.M):
            if r.group(1) in modulos and r.group(1) != m:
                pila.append(r.group(1))
    faltan = sorted(visto - en_manifiesto)
    comprobar(not faltan, "todo script que importan los scripts del corredor (y sus importados) esta en el manifiesto", str(faltan))
    comprobar({"corredor_vps", "espejo_drive"} <= en_manifiesto, "el orquestador y el espejo de Drive se despliegan")

    ofertas_b = json.loads((editor / "vps" / "ofertas.json").read_text(encoding="utf-8"))["fabrica"]["carpeta_ofertas"]
    comprobar(json.loads((editor / "vps" / "corredor.json").read_text(encoding="utf-8"))["carpeta_ofertas"] == ofertas_b, "la carpeta OFERTAS LATAM de corredor.json es la MISMA que usa la vigilancia de la Fabrica (ofertas.json): ni una letra distinta")
    servicio = (editor / "vps" / "anuncios-corredor.service").read_text(encoding="utf-8")
    clave = {}
    for linea in servicio.splitlines():
        if "=" in linea and not linea.startswith(("#", "[")):
            k, _, v = linea.partition("=")
            clave.setdefault(k.strip(), []).append(v.strip())
    for k, v in (("User", "broll"), ("NoNewPrivileges", "true"), ("ProtectSystem", "strict"), ("PrivateTmp", "true"), ("CPUQuota", "150%"), ("MemoryMax", "5500M"), ("MemoryHigh", "5G"), ("MemorySwapMax", "0"), ("OOMScoreAdjust", "500"), ("Nice", "10"), ("IOSchedulingClass", "idle"),
                 ("CapabilityBoundingSet", ""), ("ProtectHome", "true"), ("Type", "oneshot")):
        comprobar(clave.get(k) == [v], f"la unidad trae {k}={v}", str(clave.get(k)))
    comprobar("LoadCredential" not in clave, "la unidad ya no carga la llave de APIMart (LoadCredential=): el corredor pide a APIMart por el dash", str(clave.get("LoadCredential")))
    inaccesibles = " ".join(clave.get("InaccessiblePaths", []))
    comprobar("/opt/radar-de-ofertas" in inaccesibles and "/opt/actions-runner" in inaccesibles and "/opt/broll-tiktok/public" in inaccesibles, "la unidad no ve el radar, el runner ni los datos del b-roll")
    escribibles = " ".join(clave.get("ReadWritePaths", []))
    comprobar(escribibles and "/opt/broll-tiktok" not in escribibles and "/opt/radar-de-ofertas" not in escribibles and "secretos" not in escribibles and "node_modules" not in escribibles and "scripts" not in escribibles, "solo escribe en sus carpetas de datos (no en el codigo, node_modules, los secretos ni el b-roll)")
    comprobar(clave.get("MemoryHigh") and clave.get("TasksMax"), "tiene MemoryHigh y TasksMax")
    entornos = " ".join(clave.get("Environment", []))
    comprobar("OFFTHREADVIDEO_CACHE_MB=384" in entornos, "la unidad acota la cache de video de Remotion (sin eso el render llega a 5,9 GB en el servidor de 8 GB)")
    remotion = (editor / "scripts" / "remotion.mjs").read_text(encoding="utf-8")
    comprobar("OFFTHREADVIDEO_CACHE_MB" in remotion and "offthreadVideoCacheSizeInBytes" in remotion and "...opcionesDeMemoria()" in remotion, "remotion.mjs lee OFFTHREADVIDEO_CACHE_MB y se la pasa a renderMedia")
    comprobar("scripts/remotion.mjs" in (editor / "vps" / "corredor-archivos.txt").read_text(encoding="utf-8"), "remotion.mjs viaja al servidor en el manifiesto")
    timer = (editor / "vps" / "anuncios-corredor.timer").read_text(encoding="utf-8")
    comprobar("OnCalendar=*-*-* *:40:00 America/Bogota" in timer, "el reloj corre a los :40 (la vigilancia de la Fabrica corre a los :10)")
    instalador = (editor / "vps" / "instalar-corredor.sh").read_text(encoding="utf-8")
    comprobar("systemctl enable" not in instalador and "enable --now" not in instalador, "el instalador NUNCA enciende el reloj")
    ctl = (editor / "vps" / "corredor-ctl").read_text(encoding="utf-8")
    comprobar(ctl.count("systemctl enable") == 1 and "encender)" in ctl, "solo `corredor-ctl encender` enciende el reloj")
    comprobar("--llave" not in ctl and "cat /opt/broll-tiktok/secretos/dash.env" not in ctl and "cat secretos/apimart.key" not in ctl and "echo $DASH_CLAVE" not in ctl, "corredor-ctl no imprime la llave")
    comprobar("Falta la llave de APIMart" not in ctl and "export APIMART_API_KEY" not in ctl and "probar-apimart|saldo)" in ctl, "corredor-ctl ya no exige ni reparte la llave de APIMart; «saldo» le pregunta al dash")
    despliegue = (editor / "vps" / "desplegar-corredor.sh").read_text(encoding="utf-8")
    comprobar("antes-" in instalador and "sha256sum" in despliegue and "flock" in instalador and "revertir" in instalador, "el despliegue usa SHA-256, copias .antes-<cambio>, candados y marcha atras")
    bash = shutil.which("bash")
    if bash and os.name != "nt":
        for nombre in ("instalar-corredor.sh", "desplegar-corredor.sh", "correr_corredor.sh", "corredor-ctl"):
            r = subprocess.run([bash, "-n", str(editor / "vps" / nombre)], capture_output=True, text=True)
            comprobar(r.returncode == 0, f"{nombre} tiene la sintaxis bien", r.stderr[:200])
    wrapper = (editor / "vps" / "correr_corredor.sh").read_text(encoding="utf-8")
    comprobar("No corras esto como root" in wrapper and "flock -n 9" in wrapper and "PAUSA" in wrapper and "echo $APIMART" not in wrapper, "el arranque no corre como root, toma el candado, respeta la pausa y no imprime la llave")
    comprobar("export APIMART_API_KEY" not in wrapper and "unset APIMART_API_KEY CREDENTIALS_DIRECTORY" in wrapper, "el arranque ya no carga la llave de APIMart (y la quita si el entorno la trajera)")


if __name__ == "__main__":
    main()
