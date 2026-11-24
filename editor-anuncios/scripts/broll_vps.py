"""Corrida del b-roll de TikTok en el VPS, SIN el PC del dueno.

Para cada oferta de ofertas.json (las que estan activas):
  1. abre la carpeta de Drive de la oferta, halla o crea su subcarpeta B-rolls y lee su fuentes.csv (lo que ya se bajo o se descarto:
     lo que baja el PC o lo que borra el equipo no vuelve)
  2. baja videos NUEVOS de TikTok sin marca de agua con scripts/broll_tiktok.py (busqueda por palabra con la lista PUBLICA de TikTok: gratis y sin llave;
     cuentas de TikTok, hashtags), con topes por oferta y por corrida y pausas entre descargas
  3. mira cada video bajado con scripts/texto_quemado.py: cuantos segundos NO traen texto quemado; los que no tienen ni 5 segundos limpios
     se descartan (quedan anotados, y guardados 30 dias en el servidor por si alguien quiere verlos)
  4. sube los buenos a Drive > carpeta de la oferta > B-rolls (subida reanudable, comprobando tamano y MD5) y deja fuentes.csv al dia
  5. borra del servidor los videos ya subidos con mas de 14 dias (en fuentes.csv queda anotado que existieron)
No usa tokens de Claude. Las llaves viven en secretos/google.json y en .env (las pone el dueno; este programa nunca las imprime).

Uso (en el VPS, como el usuario broll; el timer de systemd usa correr_broll.sh que hace esto con candado y registro):
  python scripts/broll_vps.py [correr] [--solo dinamicas,velas] [--max-total 30] [--max-por-oferta 10] [--simular] [--sin-drive] [--sin-texto]
  python scripts/broll_vps.py estado          que paso en la ultima corrida y cuantos videos tiene cada oferta

Codigos de salida: 0 bien | 2 bien pero alguna fuente fallo (por ejemplo una cuenta que TikTok mando a esperar) | 3 TikTok bloqueo toda la corrida |
4 Drive sin permiso (hay que volver a autorizar) | 1 configuracion mala o error inesperado.
(3 tambien cuando no se pudo hablar con Drive al empezar: se reintenta mas tarde.)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:            # Windows: el candado solo existe en el servidor
    fcntl = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broll_tiktok as bt  # noqa: E402
import drive_api as da  # noqa: E402
import texto_quemado as tq  # noqa: E402
from _comun import configurar_consola  # noqa: E402

SALIDA_BIEN, SALIDA_CONFIG, SALIDA_PARCIAL, SALIDA_TIKTOK, SALIDA_DRIVE = 0, 1, 2, 3, 4
RE_PRODUCTO = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
RE_ARCHIVO = re.compile(r"^tt_\d{10,25}\.(mp4|mov|m4v|webm|mkv)$")     # el unico nombre que se sube: nada que venga de fuentes.csv sale de la carpeta del producto
DIAS_DESCARTADOS = 30
CARPETA_DESCARTADOS = "_descartados"
DEFECTOS = {"tope_videos_corrida": 30, "tope_creditos_corrida": 20, "minutos_max": 50, "conservar_dias": 14}
DEFECTOS_TEXTO = {"activo": True, "modo": "descartar", "min_limpio_seg": 5}
DEFECTOS_OFERTA = {"activa": True, "consultas": [], "hashtags": [], "cuentas": [], "filtro_desc": [], "excluir_autores": [], "max": 10, "min_seg": 4, "max_seg": 120,
                   "region": "", "paginas": 2, "orden": "relevancia", "periodo": "todo", "max_creditos": 14, "relacionadas": 0, "proveedor": "descubrir"}
LEEME_BROLLS = (
    "B-rolls de esta oferta, bajados de TikTok por el servidor (scripts/broll_vps.py del editor de anuncios).\n"
    "fuentes.csv trae el enlace, el autor, las vistas, la busqueda y cuanto del video esta limpio de texto quemado (calidad: limpio / medio; tramos_limpios = segundos sin texto).\n"
    "Son videos de sus creadores: solo se usan en anuncios los que se tenga derecho a usar (permiso o licencia).\n"
    "Si un video no sirve, borralo de aqui: no vuelve a bajarse porque queda anotado en fuentes.csv. No borres fuentes.csv.\n")


class ErrorConfig(Exception):
    """ofertas.json no sirve; el mensaje lista todo lo que esta mal."""


# ------------------------------------------------------------------ configuracion
def _entero(errores: list[str], d: dict, clave: str, minimo: int, maximo: int, donde: str) -> None:
    v = d.get(clave)
    if isinstance(v, bool) or not isinstance(v, int) or not (minimo <= v <= maximo):
        errores.append(f"{donde}: '{clave}' debe ser un numero entero entre {minimo} y {maximo}")


def _revisar_oferta(o: dict, donde: str, errores: list[str]) -> None:
    """Todo lo que se exige de UNA oferta (el producto repetido se mira aparte): su carpeta de Drive, sus fuentes, los topes, el proveedor, el orden."""
    if not da.id_de_carpeta(str(o.get("drive", ""))):
        errores.append(f"{donde}: 'drive' debe ser el enlace de su carpeta de Drive (drive.google.com/drive/folders/...)")
    for clave in ("consultas", "hashtags", "cuentas", "filtro_desc", "excluir_autores"):
        if not isinstance(o[clave], list) or not all(isinstance(x, str) and x.strip() for x in o[clave]):
            errores.append(f"{donde}: '{clave}' debe ser una lista de textos")
    if o["activa"] is not False and not (o["consultas"] or o["hashtags"] or o["cuentas"]):
        errores.append(f"{donde}: no tiene ninguna fuente (consultas, hashtags o cuentas)")
    for clave, (mn, mx) in {"max": (1, 100), "min_seg": (1, 600), "max_seg": (2, 3600), "paginas": (1, 5), "max_creditos": (0, 100), "relacionadas": (0, 10)}.items():
        _entero(errores, o, clave, mn, mx, donde)
    if o["proveedor"] not in ("descubrir", "scrapecreators"):
        errores.append(f"{donde}: 'proveedor' debe ser descubrir (gratis, la lista publica de TikTok) o scrapecreators (de pago)")
    for clave, validos in (("orden", bt.ORDENES), ("periodo", bt.PERIODOS)):
        valores = o[clave] if isinstance(o[clave], list) else [o[clave]]
        if not valores or any(v not in validos for v in valores):
            errores.append(f"{donde}: '{clave}' debe ser uno de {', '.join(sorted(validos))} (o una lista de ellos para ir rotando)")
    if not isinstance(o["region"], str):
        errores.append(f"{donde}: 'region' debe ser un texto (por ejemplo CO) o vacio")


def cargar_config(ruta: Path) -> dict:
    try:
        crudo = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except OSError:
        raise ErrorConfig(f"no encuentro {ruta}") from None
    except ValueError as e:
        raise ErrorConfig(f"{ruta} no es un JSON valido: {e}") from None
    if not isinstance(crudo, dict):
        raise ErrorConfig(f"{ruta} debe ser un objeto JSON")
    cfg = {**DEFECTOS, **{k: v for k, v in crudo.items() if not k.startswith("_") and k not in ("ofertas", "texto_quemado")}}
    cfg["texto_quemado"] = {**DEFECTOS_TEXTO, **(crudo.get("texto_quemado") if isinstance(crudo.get("texto_quemado"), dict) else {})}
    errores: list[str] = []

    def entero(d: dict, clave: str, minimo: int, maximo: int, donde: str) -> None:
        v = d.get(clave)
        if isinstance(v, bool) or not isinstance(v, int) or not (minimo <= v <= maximo):
            errores.append(f"{donde}: '{clave}' debe ser un numero entero entre {minimo} y {maximo}")

    for clave, (mn, mx) in {"tope_videos_corrida": (1, 200), "tope_creditos_corrida": (0, 500), "minutos_max": (5, 70), "conservar_dias": (1, 365)}.items():
        entero(cfg, clave, mn, mx, "ofertas.json")
    if cfg["texto_quemado"].get("modo") not in ("descartar", "marcar"):
        errores.append("texto_quemado: 'modo' debe ser 'descartar' o 'marcar'")
    if not isinstance(cfg["texto_quemado"].get("min_limpio_seg"), (int, float)) or isinstance(cfg["texto_quemado"].get("min_limpio_seg"), bool):
        errores.append("texto_quemado: 'min_limpio_seg' debe ser un numero")
    lista = crudo.get("ofertas")
    if not isinstance(lista, list) or not lista:
        errores.append("falta la lista 'ofertas' (con al menos una oferta)")
        lista = []
    ofertas, vistos = [], set()
    for i, bruta in enumerate(lista, 1):
        if not isinstance(bruta, dict):
            errores.append(f"oferta {i}: debe ser un objeto")
            continue
        o = {**DEFECTOS_OFERTA, **{k: v for k, v in bruta.items() if not k.startswith("_")}}
        nombre = str(o.get("producto", "")) or f"#{i}"
        donde = f"oferta '{nombre}'"
        if not RE_PRODUCTO.match(str(o.get("producto", ""))):
            errores.append(f"{donde}: 'producto' debe ser minusculas, numeros, guion o guion bajo (hasta 40)")
        elif o["producto"] in vistos:
            errores.append(f"{donde}: el producto esta repetido")
        vistos.add(o.get("producto"))
        _revisar_oferta(o, donde, errores)
        ofertas.append(o)
    if errores:
        raise ErrorConfig("ofertas.json tiene problemas:\n  - " + "\n  - ".join(errores))
    cfg["ofertas"] = ofertas
    return cfg


def sumar_ofertas_fabrica(cfg: dict, log=print) -> int:
    """Suma a cfg['ofertas'] las ofertas que registro la Fabrica en estado/ofertas_fabrica.json (scripts/fabrica_broll.py), con la MISMA revision que las de
    ofertas.json: una entrada mala se salta (queda dicho en el registro) y no tumba la corrida. Las cerradas (activa: false) no se suman; si ofertas.json ya trae
    el mismo producto, gana ofertas.json; y solo entran las `ofertas_activas_max` mas recientes. Devuelve cuantas sumo."""
    ruta = bt.RAIZ / "estado" / "ofertas_fabrica.json"
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except OSError:
        return 0
    except ValueError:
        log("AVISO: estado/ofertas_fabrica.json no se puede leer: las ofertas de la Fabrica no se suman en esta corrida")
        return 0
    brutas = datos.get("ofertas") if isinstance(datos, dict) else None
    if not isinstance(brutas, list):
        return 0
    bloque = cfg.get("fabrica") if isinstance(cfg.get("fabrica"), dict) else {}
    tope = bloque.get("ofertas_activas_max") if isinstance(bloque.get("ofertas_activas_max"), int) and not isinstance(bloque.get("ofertas_activas_max"), bool) else 6
    usados = {o["producto"] for o in cfg["ofertas"]}
    sumadas: list[dict] = []
    for bruta in reversed(brutas):
        if not isinstance(bruta, dict) or bruta.get("activa") is False:
            continue
        o = {**DEFECTOS_OFERTA, **{k: v for k, v in bruta.items() if not k.startswith("_")}}
        nombre = str(o.get("producto", "?"))
        donde = f"oferta '{nombre}' (de la Fabrica)"
        errores: list[str] = []
        if not RE_PRODUCTO.match(nombre):
            errores.append(f"{donde}: 'producto' no es valido")
        elif nombre in usados:
            log(f"  Fabrica: '{nombre}' ya esta en ofertas.json: se usa esa")
            continue
        else:
            _revisar_oferta(o, donde, errores)
        if errores:
            log("AVISO: se salta una oferta de la Fabrica: " + "; ".join(errores))
            continue
        if len(sumadas) >= tope:
            log(f"  Fabrica: '{nombre}' espera: ya hay {tope} ofertas de la Fabrica activas (las mas recientes)")
            continue
        usados.add(nombre)
        sumadas.append(o)
    cfg["ofertas"].extend(reversed(sumadas))
    return len(sumadas)


def elegir(valor, vuelta: int):
    """Un valor fijo, o el que toca de una lista (para variar de una corrida a otra)."""
    return valor[vuelta % len(valor)] if isinstance(valor, list) else valor


# ------------------------------------------------------------------ fuentes.csv entre el servidor y Drive
def leer_csv_bytes(datos: bytes) -> dict[str, dict]:
    texto = datos.decode("utf-8-sig", errors="replace")
    return {f["id"]: f for f in csv.DictReader(io.StringIO(texto, newline="")) if f.get("id")}


def sincronizar_entrada(drive: da.Drive, brolls_id: str, local: Path, log=print) -> int:
    """Suma al registro del servidor lo que Drive ya sabe (lo que bajo el PC, lo descartado): asi no se vuelve a bajar. Devuelve cuantas filas nuevas."""
    datos = drive.leer_texto(brolls_id, "fuentes.csv")
    if not datos:
        return 0
    delas = leer_csv_bytes(datos)
    filas = bt.leer_fuentes(local)
    nuevas = cambios = 0
    for vid, fila in delas.items():
        if not vid.isdigit():
            continue
        if vid not in filas:
            if fila.get("archivo") and not RE_ARCHIVO.match(fila["archivo"]):
                fila["archivo"] = ""      # un nombre raro escrito a mano en el csv de Drive nunca se usa como ruta
            filas[vid] = fila
            nuevas += 1
        elif fila.get("en_drive") == "si" and filas[vid].get("estado") == "bajado" and filas[vid].get("en_drive") != "si":
            filas[vid]["en_drive"] = "si"
            cambios += 1
    if nuevas or cambios:
        bt.escribir_fuentes(local, filas)
        log(f"  Drive ya tenia anotados {nuevas} video(s) que este servidor no conocia" + (f" y {cambios} marcado(s) como subido(s)" if cambios else ""))
    return nuevas


# ------------------------------------------------------------------ revisar el texto quemado
def revisar_texto(local: Path, filas: dict[str, dict], cfg_texto: dict, analizar, log=print) -> tuple[int, int]:
    """Mira los videos nuevos (todavia no subidos). Devuelve (revisados, descartados). Con modo 'marcar' solo anota la calidad."""
    if not cfg_texto.get("activo"):
        return 0, 0
    revisados = descartados = 0
    for vid, fila in sorted(filas.items(), key=lambda kv: kv[1].get("descargado_en") or ""):
        nombre = fila.get("archivo") or ""
        if fila.get("estado") != "bajado" or fila.get("en_drive") == "si" or fila.get("calidad") or not RE_ARCHIVO.match(nombre):
            continue
        ruta = local / nombre
        if not ruta.is_file():
            continue
        try:
            dur = float(fila["duracion_s"]) if fila.get("duracion_s") else None
            r = analizar(ruta, duracion=dur)
        except tq.FaltaDetector as e:
            log(f"  AVISO: {e}. Sin el detector de texto no se revisa el texto de ningun video (se suben todos tal cual).")
            break
        except tq.ErrorTexto as e:
            log(f"  AVISO: no pude revisar el texto de {nombre}: {e}")
            continue
        fila.update(texto_pct=str(r["texto_pct"]), limpio_seg=str(r["limpio_seg"]), tramos_limpios=tq.tramos_a_texto(r["tramos_limpios"]), calidad=r["calidad"])
        revisados += 1
        etiqueta = f"{nombre}: {r['limpio_seg']:g} s limpios, texto en {r['texto_pct']}% de los cuadros -> {r['calidad']}"
        if cfg_texto.get("modo") == "descartar" and r["limpio_seg"] < float(cfg_texto.get("min_limpio_seg", 5)):
            destino = local / CARPETA_DESCARTADOS
            destino.mkdir(exist_ok=True)
            shutil.move(str(ruta), str(destino / nombre))
            fila.update(estado="descartado", motivo=f"texto quemado: solo {r['limpio_seg']:g} s sin texto (detector de texto)", en_drive="")
            descartados += 1
            log(f"  x {etiqueta}  [descartado]")
        else:
            log(f"  . {etiqueta}")
    return revisados, descartados


# ------------------------------------------------------------------ subir
def subir_pendientes(drive: da.Drive, brolls_id: str, local: Path, filas: dict[str, dict], log=print) -> tuple[int, int, int]:
    """Sube lo bajado que aun no esta en Drive. Devuelve (subidos, sin_subir, fallos). Un ErrorPermiso sube a quien llama."""
    existentes = {f.get("name"): f for f in drive.listar_hijos(brolls_id)}
    subidos = fallos = seguidos = 0
    for vid, fila in sorted(filas.items(), key=lambda kv: kv[1].get("descargado_en") or ""):
        if fila.get("estado") != "bajado" or fila.get("en_drive") == "si":
            continue
        nombre = fila.get("archivo") or ""
        if not RE_ARCHIVO.match(nombre):
            if nombre:
                log(f"  AVISO: {vid}: el nombre de archivo de fuentes.csv no es de los nuestros ({nombre[:40]!r}): no se sube")
            continue
        ruta = local / nombre
        if not ruta.is_file():
            continue        # ya no esta en el servidor (se borro por viejo o no se alcanzo a bajar): nada que subir
        ya = existentes.get(nombre)
        if ya is not None:
            if str(ya.get("size")) == str(ruta.stat().st_size):
                fila["en_drive"] = "si"
                log(f"  = {nombre}: ya estaba en Drive")
            else:
                log(f"  x {nombre}: en Drive ya hay un archivo con ese nombre y OTRO tamano; no se toca")
                fallos += 1
            continue
        try:
            drive.subir_archivo(ruta, brolls_id, nombre)
        except da.ErrorPermiso:
            raise
        except da.ErrorDrive as e:
            fallos += 1
            seguidos += 1
            log(f"  x {nombre}: {e}")
            if seguidos >= 3:
                log("  Tres fallos seguidos subiendo: se detiene la subida (lo que falta se sube en la proxima corrida).")
                break
            continue
        seguidos = 0
        fila["en_drive"] = "si"
        subidos += 1
        bt.escribir_fuentes(local, filas)        # se anota apenas sube: si algo se corta despues, no se vuelve a subir
        log(f"  ^ {nombre}: subido a Drive ({ruta.stat().st_size / 1e6:.1f} MB)")
    sin_subir = sum(1 for f in filas.values() if f.get("estado") == "bajado" and f.get("en_drive") != "si" and (local / (f.get("archivo") or "x")).is_file())
    return subidos, sin_subir, fallos


def limpiar(local: Path, filas: dict[str, dict], dias: int, log=print) -> float:
    """Borra del servidor los videos ya subidos con mas de `dias` dias, y los descartados con mas de 30. Devuelve los MB liberados. El registro no se toca."""
    liberado = 0
    ahora = time.time()
    for fila in filas.values():
        nombre = fila.get("archivo") or ""
        if fila.get("estado") == "bajado" and fila.get("en_drive") == "si" and RE_ARCHIVO.match(nombre):
            p = local / nombre
            if p.is_file() and ahora - p.stat().st_mtime > dias * 86400:
                liberado += p.stat().st_size
                p.unlink()
    carpeta = local / CARPETA_DESCARTADOS
    if carpeta.is_dir():
        for p in carpeta.glob("tt_*"):
            if p.is_file() and ahora - p.stat().st_mtime > DIAS_DESCARTADOS * 86400:
                liberado += p.stat().st_size
                p.unlink()
    if liberado:
        log(f"  Se borraron del servidor {liberado / 1e6:.0f} MB de videos viejos que ya estan en Drive.")
    return liberado / 1e6


# ------------------------------------------------------------------ una oferta
class Contexto:
    def __init__(self, cfg: dict, drive: da.Drive | None, fabrica, analizar, simular: bool, sin_texto: bool, log, max_por_oferta: int | None, rotacion: dict):
        self.cfg, self.drive, self.fabrica, self.analizar = cfg, drive, fabrica, analizar
        self.simular, self.sin_texto, self.log, self.max_por_oferta, self.rotacion = simular, sin_texto, log, max_por_oferta, rotacion
        self.videos_restantes = int(cfg["tope_videos_corrida"])
        self.creditos_restantes = int(cfg["tope_creditos_corrida"])


def argumentos_de_traer(o: dict, maximo: int, creditos: int, vuelta: int, simular: bool) -> list[str]:
    argv = ["traer", o["producto"], "--sin-drive", "--max", str(maximo), "--min-seg", str(o["min_seg"]), "--max-seg", str(o["max_seg"]),
            "--paginas", str(o["paginas"]), "--orden", elegir(o["orden"], vuelta), "--periodo", elegir(o["periodo"], vuelta),
            "--max-creditos", str(max(0, min(int(o["max_creditos"]), creditos)))]
    if o["region"]:
        argv += ["--region", o["region"]]
    if creditos > 0:          # sin pedidos disponibles no se hacen busquedas por palabra (las cuentas no cuentan)
        argv += ["--proveedor", o["proveedor"], "--relacionadas", str(o["relacionadas"])]
        for q in o["consultas"]:
            argv.append(f"--consulta={q}")
        for h in o["hashtags"]:
            argv.append(f"--hashtag={h}")
    # Los textos van pegados a su opcion (--opcion=texto): uno que empiece con «-» (el nombre de un anunciante en la Biblioteca de Meta, una frase sacada de un anuncio ajeno) no lo toma argparse por otra
    # opcion ni rompe la corrida de esa oferta («expected one argument»)
    for c in o["cuentas"]:
        argv.append(f"--cuenta={c}")
    for p in o["filtro_desc"]:
        argv.append(f"--filtro-desc={p}")
    for p in o["excluir_autores"]:
        argv.append(f"--excluir-autor={p}")
    if simular:
        argv.append("--simular")
    return argv


def procesar_oferta(o: dict, ctx: Contexto) -> dict:
    log = ctx.log
    producto = o["producto"]
    res = {"producto": producto, "nombre": o.get("nombre", producto), "estado": "ok", "bajados": 0, "descartados_texto": 0, "subidos": 0, "sin_subir": 0,
           "fallos": 0, "fallos_descarga": 0, "problemas": [], "creditos": 0, "cortada_por_red": False, "intento_bajar": False, "liberado_mb": 0.0, "busquedas": {},
           "de_la_cuenta_del_anunciante": 0}
    local = bt.PUBLIC / "productos" / producto / bt.CARPETA_LOCAL
    local.mkdir(parents=True, exist_ok=True)
    drive, brolls_id = ctx.drive, None

    if drive is not None:
        cid = da.id_de_carpeta(o["drive"])
        info = drive.carpeta(cid)
        if not info["escribible"]:
            raise da.ErrorDrive(f"la carpeta '{info['name']}' es de solo lectura para la cuenta que dio el permiso")
        drive.permitir(cid)
        log(f"  Carpeta de Drive: '{info['name']}'")
        brolls_id, cuantas = drive.hallar_carpeta(cid, bt.CARPETA_DRIVE)
        if cuantas > 1:
            log(f"  AVISO: hay {cuantas} carpetas llamadas {bt.CARPETA_DRIVE} en la oferta; se usa la mas antigua")
        creada = False
        if not brolls_id:
            if ctx.simular:
                log(f"  (simulacion) {bt.CARPETA_DRIVE} todavia no existe: se crearia en la corrida de verdad")
            else:
                brolls_id = drive.crear_carpeta(cid, bt.CARPETA_DRIVE)
                creada = True
                log(f"  Se creo la carpeta {bt.CARPETA_DRIVE} dentro de '{info['name']}'")
        if brolls_id:
            drive.permitir(brolls_id)
            sincronizar_entrada(drive, brolls_id, local, log)
            if creada:           # el LEEME solo se deja cuando la carpeta es nueva: si el equipo lo borra despues, no se vuelve a poner
                drive.guardar_texto(brolls_id, "LEEME.txt", LEEME_BROLLS.encode("utf-8"))

    # --- bajar
    maximo = min(int(o["max"]), ctx.videos_restantes, ctx.max_por_oferta or 10 ** 6)
    if maximo <= 0:
        res["estado"] = "saltada"
        res["nota"] = "se acabo el tope de videos de esta corrida"
        log("  Se acabo el tope de videos de esta corrida: esta oferta espera a la proxima.")
    else:
        vuelta = int(ctx.rotacion.get(producto, 0))
        argv = argumentos_de_traer(o, maximo, ctx.creditos_restantes, vuelta, ctx.simular)
        sintesis: dict = {}
        res["intento_bajar"] = True
        try:
            bt.traer(bt.construir_parser().parse_args(argv), fabrica=ctx.fabrica, resumen=sintesis)
        except SystemExit as e:
            res["problemas"].append(f"traer termino con un error (codigo {e.code}); mira el registro")
        res.update(bajados=int(sintesis.get("bajados", 0)), creditos=int(sintesis.get("creditos", 0)), cortada_por_red=bool(sintesis.get("cortada_por_red")))
        res["busquedas"] = dict(sintesis.get("busquedas") or {})
        res["de_la_cuenta_del_anunciante"] = int(sintesis.get("de_la_cuenta_del_anunciante", 0))      # videos de la cuenta del propio anunciante original: no se bajan
        res["fallos_descarga"] = int(sintesis.get("fallos", 0))      # videos que no sirvieron (privado, borrado, horizontal...): es normal, no cuenta como problema
        res["problemas"] += list(sintesis.get("problemas", []))
        ctx.videos_restantes -= res["bajados"]
        ctx.creditos_restantes -= res["creditos"]
        if sintesis.get("creditos_quedan") is not None:
            res["creditos_quedan"] = sintesis["creditos_quedan"]
        ctx.rotacion[producto] = vuelta + 1

    if ctx.simular:
        return res

    # --- revisar el texto, subir, dejar el registro al dia, limpiar
    filas = bt.leer_fuentes(local)
    revisados, descartados = (0, 0) if ctx.sin_texto else revisar_texto(local, filas, ctx.cfg["texto_quemado"], ctx.analizar, log)
    res["descartados_texto"] = descartados
    if revisados:
        bt.escribir_fuentes(local, filas)
    if drive is not None and brolls_id:
        try:
            res["subidos"], res["sin_subir"], fallos_subida = subir_pendientes(drive, brolls_id, local, filas, log)
        finally:
            bt.escribir_fuentes(local, filas)
        res["fallos"] += fallos_subida
        drive.guardar_texto(brolls_id, "fuentes.csv", (local / "fuentes.csv").read_bytes(), "text/csv")
    res["liberado_mb"] = round(limpiar(local, filas, int(ctx.cfg["conservar_dias"]), log), 1)
    if res["problemas"] or res["fallos"] or res["sin_subir"]:
        res["estado"] = "parcial"
    return res


# ------------------------------------------------------------------ la corrida completa
def escribir_estado(carpeta: Path, resumen: dict) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "ultima_corrida.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (carpeta / "historial.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: resumen[k] for k in ("inicio", "fin", "codigo", "videos", "subidos", "descartados", "creditos")}, ensure_ascii=False) + "\n")


def leer_rotacion(carpeta: Path) -> dict:
    try:
        d = json.loads((carpeta / "rotacion.json").read_text(encoding="utf-8"))
        return {k: int(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def correr(args, *, fabrica=None, analizar=None, drive=None, ruta_credenciales=None, log=print) -> int:
    fabrica = fabrica or bt.DescargadorYtdlp
    analizar = analizar or tq.analizar
    estado_dir = bt.RAIZ / "estado"
    inicio = datetime.now()
    try:
        cfg = cargar_config(Path(args.config) if args.config else bt.RAIZ / "ofertas.json")
    except ErrorConfig as e:
        log(f"ERROR: {e}")
        return SALIDA_CONFIG
    de_fabrica = sumar_ofertas_fabrica(cfg, log)
    if de_fabrica:
        log(f"Fabrica: {de_fabrica} oferta(s) registrada(s) por la Fabrica se suman a ofertas.json")
    solo = {s.strip() for s in (args.solo or "").split(",") if s.strip()}
    desconocidas = solo - {o["producto"] for o in cfg["ofertas"]}
    if desconocidas:
        log(f"ERROR: --solo pide ofertas que no estan en ofertas.json: {', '.join(sorted(desconocidas))}")
        return SALIDA_CONFIG
    if args.max_total:
        cfg["tope_videos_corrida"] = args.max_total
    ofertas = [o for o in cfg["ofertas"] if o["activa"] is not False and (not solo or o["producto"] in solo)]
    log(f"B-roll de TikTok: {len(ofertas)} oferta(s) a revisar" + (" [SIMULACION: no se baja, sube ni borra nada]" if args.simular else "")
        + f" | tope de la corrida: {cfg['tope_videos_corrida']} videos, {cfg['tope_creditos_corrida']} pedidos de busqueda, {cfg['minutos_max']} min")

    fatal_permiso = sin_red = False
    if drive is None and not args.sin_drive:
        try:
            drive = da.Drive(da.cargar_credenciales(ruta_credenciales), registrar=log)
            log(f"Drive: permiso renovado, cuenta {drive.cuenta()}")
        except da.ErrorPermiso as e:
            log(f"SIN PERMISO DE DRIVE: {e}")
            fatal_permiso = True
        except da.ErrorDrive as e:
            log(f"ERROR hablando con Drive: {e}")
            sin_red = True              # sin Drive no se baja nada (los videos se amontonarian sin poder entregarse): se reintenta mas tarde
    resultados: list[dict] = []
    rotacion = leer_rotacion(estado_dir)
    ctx = Contexto(cfg, drive, fabrica, analizar, args.simular, args.sin_texto, log, args.max_por_oferta, rotacion)
    limite = time.time() + 60 * int(cfg["minutos_max"])
    for o in ([] if (fatal_permiso or sin_red) else ofertas):
        log(f"\n=== {o['producto']} ({o.get('nombre', '')}) ===")
        if time.time() > limite:
            log("  Se acabo el tiempo de la corrida: esta oferta espera a la proxima.")
            resultados.append({"producto": o["producto"], "estado": "saltada", "bajados": 0, "descartados_texto": 0, "subidos": 0, "sin_subir": 0, "fallos": 0,
                               "problemas": [], "nota": "se acabo el tiempo de la corrida", "creditos": 0, "cortada_por_red": False, "intento_bajar": False, "liberado_mb": 0})
            continue
        try:
            resultados.append(procesar_oferta(o, ctx))
        except da.ErrorPermiso as e:
            log(f"SIN PERMISO DE DRIVE: {e}")
            fatal_permiso = True
            break
        except da.ErrorDrive as e:
            log(f"  ERROR con Drive en esta oferta: {e}")
            resultados.append({"producto": o["producto"], "estado": "fallo", "bajados": 0, "descartados_texto": 0, "subidos": 0, "sin_subir": 0, "fallos": 1,
                               "problemas": [str(e)], "creditos": 0, "cortada_por_red": False, "intento_bajar": False, "liberado_mb": 0})
        except Exception as e:  # noqa: BLE001 - una oferta con un error raro no debe tumbar a las demas
            log(f"  ERROR inesperado en esta oferta: {e.__class__.__name__}: {str(e)[:200]}")
            resultados.append({"producto": o["producto"], "estado": "fallo", "bajados": 0, "descartados_texto": 0, "subidos": 0, "sin_subir": 0, "fallos": 1,
                               "problemas": [f"error inesperado: {e.__class__.__name__}"], "creditos": 0, "cortada_por_red": False, "intento_bajar": False, "liberado_mb": 0})

    # --- codigo de salida
    intentaron = [r for r in resultados if r.get("intento_bajar")]
    if fatal_permiso:
        codigo = SALIDA_DRIVE
    elif sin_red:
        codigo = SALIDA_TIKTOK
    elif intentaron and all(r["cortada_por_red"] and r["bajados"] == 0 for r in intentaron):
        codigo = SALIDA_TIKTOK
    elif any(r["problemas"] or r["fallos"] or r["estado"] in ("fallo",) for r in resultados):
        codigo = SALIDA_PARCIAL
    else:
        codigo = SALIDA_BIEN
    total = {"videos": sum(r["bajados"] for r in resultados), "subidos": sum(r["subidos"] for r in resultados),
             "descartados": sum(r["descartados_texto"] for r in resultados), "creditos": sum(r["creditos"] for r in resultados)}
    log("\n=== Resumen ===")
    for r in resultados:
        log(f"  {r['producto']}: {r['bajados']} nuevo(s), {r['descartados_texto']} descartado(s) por texto, {r['subidos']} subido(s) a Drive"
            + (f", {r['sin_subir']} sin subir todavia" if r["sin_subir"] else "") + (f", {len(r['problemas'])} problema(s)" if r["problemas"] else "") + f" [{r['estado']}]")
        for p in r["problemas"]:
            log(f"      - {p}")
    fin = datetime.now()
    log(f"  Total: {total['videos']} video(s) bajados, {total['subidos']} subidos, {total['descartados']} descartados, {total['creditos']} pedido(s) de busqueda a TikTok o al proveedor. Salida {codigo}.")
    if not args.simular:
        try:
            escribir_estado(estado_dir, {"inicio": inicio.isoformat(timespec="seconds"), "fin": fin.isoformat(timespec="seconds"), "codigo": codigo,
                                         "sin_permiso_de_drive": fatal_permiso, **total, "ofertas": resultados})
            (estado_dir / "rotacion.json").write_text(json.dumps(rotacion), encoding="utf-8")
        except OSError as e:
            log(f"AVISO: no pude escribir el estado de la corrida: {e}")
    return codigo


def estado_corrida() -> int:
    ruta = bt.RAIZ / "estado" / "ultima_corrida.json"
    if not ruta.is_file():
        print("Todavia no hay ninguna corrida registrada (estado/ultima_corrida.json).")
        return 0
    d = json.loads(ruta.read_text(encoding="utf-8"))
    print(f"Ultima corrida: {d['inicio']} -> {d['fin']} | salida {d['codigo']}" + (" (SIN PERMISO DE DRIVE)" if d.get("sin_permiso_de_drive") else ""))
    print(f"  {d['videos']} video(s) bajados, {d['subidos']} subidos, {d['descartados']} descartados por texto, {d['creditos']} pedido(s) de busqueda")
    for r in d.get("ofertas", []):
        print(f"  - {r['producto']}: {r['bajados']} nuevo(s), {r['subidos']} subido(s) [{r['estado']}]" + "".join(f"\n      {p}" for p in r.get("problemas", [])))
    productos = bt.PUBLIC / "productos"
    if productos.is_dir():
        for p in sorted(productos.iterdir()):
            filas = bt.leer_fuentes(p / bt.CARPETA_LOCAL)
            if filas:
                n = lambda e: sum(1 for f in filas.values() if f.get("estado") == e)  # noqa: E731
                print(f"  registro de {p.name}: {len(filas)} video(s) conocidos ({n('bajado')} bajados, {n('descartado')} descartados)")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", nargs="?", default="correr", choices=("correr", "estado"))
    ap.add_argument("--config", help="ofertas.json (por defecto el de /opt/broll-tiktok)")
    ap.add_argument("--solo", help="solo estas ofertas, separadas por coma")
    ap.add_argument("--max-total", dest="max_total", type=int, help="tope de videos nuevos de toda la corrida (manda sobre ofertas.json)")
    ap.add_argument("--max-por-oferta", dest="max_por_oferta", type=int, help="tope de videos nuevos por oferta (manda sobre el 'max' de cada una)")
    ap.add_argument("--simular", action="store_true", help="mira y dice que bajaria; no baja, no sube, no borra (la busqueda SI hace sus pedidos; con el proveedor de pago gasta creditos)")
    ap.add_argument("--sin-drive", action="store_true", dest="sin_drive", help="no usa Drive (para probar la bajada y el texto quemado)")
    ap.add_argument("--sin-texto", action="store_true", dest="sin_texto", help="no revisa el texto quemado")
    return ap


def main() -> None:
    configurar_consola()
    args = construir_parser().parse_args()
    if args.comando == "estado":
        sys.exit(estado_corrida())
    candado = None
    if fcntl is not None:
        (bt.RAIZ / "estado").mkdir(parents=True, exist_ok=True)
        candado = open(bt.RAIZ / "estado" / ".candado-python", "w")
        try:
            fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Ya hay una corrida en curso: esta no arranca.")
            sys.exit(0)
    try:
        sys.exit(correr(args))
    except KeyboardInterrupt:
        print("Interrumpido.")
        sys.exit(130)
    finally:
        if candado is not None:
            candado.close()


if __name__ == "__main__":
    main()
