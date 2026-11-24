"""
dash_estados.py - Pasa a la planilla los estados que el dueno elige en el dash (/radar).

En el dash (la pagina /radar) cada oferta tiene seis botones: nuevo, vigilar, testiando, ganadora, descartada, no testeada («testiando» = se esta probando: el
dash ademas la manda al tablero de Produccion; para el aprendizaje del radar es NEUTRA, como no testeada). El dash NO escribe en la planilla
(solo tiene su copia publicada en solo lectura): guarda lo elegido y lo ofrece en /api/radar-estado, protegido con una llave (cabecera x-clave-radar,
la misma CLAVE_RADAR de Cloudflare). El radar es quien tiene permiso de escribir en la planilla, asi que es el quien lo pasa (`radar.py --sync-estados`,
cada 5 minutos en el VPS con radar-estados.timer).

Reglas que no se saltan (la planilla es del dueno; esto solo reenvia SU decision):
  - Solo escribe la celda Status de una fila que YA existe, con uno de los seis valores. Nunca agrega ni borra filas; fuera de Status solo toca sus propias columnas
    al final de OFERTAS («Motivo del estado» y las «Prueba: …» de nuestro resultado, PRUEBA_COLS), y solo las celdas que cambian.
  - Solo la cambia si la planilla todavia dice lo que decia cuando el dueno eligio (`desde`). Si alguien la cambio a mano despues, gana la planilla.
  - Una fila se encuentra por el id de su anuncio ('a<digitos>', el mismo que arma el dash desde "Link del anuncio") o, si no lo tiene, por su
    numero de fila + el nombre del anunciante (si la fila se movio o es otra, no se toca).
  - Tope de MAX_CAMBIOS por pasada: si algo saliera mal en el dash no se reescribe media planilla de una vez.
  - Sin dash.key no hace nada. La llave viaja solo en una cabecera y no se imprime.
Al terminar le avisa al dash como le fue (para que la ficha diga "la planilla se actualiza sola" o "el radar no responde").
"""
import math
import os
import re
from datetime import datetime
from pathlib import Path

import requests

import sheets

HERE = Path(os.environ.get("RADAR_DATOS") or Path(__file__).resolve().parent)   # carpeta de los DATOS (en el VPS /opt/radar-de-ofertas; el codigo vive aparte)
BASE = os.environ.get("DASH_URL", "").rstrip("/")
URL = BASE + "/api/radar-estado"
ESTADOS = ("nuevo", "vigilar", "ganadora", "descartada", "no testeada", "testiando")   # los mismos que radar.STATUS_VALUES (test_estados.py lo comprueba)
MAX_CAMBIOS = 60
TIMEOUT = 20
# 2026-11-04, pedido del dueno: «cuando pongo descartada no hay un espacio para escribir por que la estoy descartando». Si el dash manda «motivo» con el
# estado (opcional; lo escribe quien decide), va a la columna MOTIVO_COL de esa fila (se crea al final de OFERTAS la primera vez). Solo esa celda, solo
# si la fila quedo en ese estado y solo si el texto cambio. El radar lo usa para aprender (los motivos de las descartadas le llegan a la IA).
MOTIVO_COL = "Motivo del estado"
MOTIVO_MAX = 300

# 2026-11-04, pedido del dueno: que el radar aprenda que senales tenian las ofertas que VENDIERON. En el dash, en «Testiando», el dueno dice con que oferta PROPIA
# se prueba cada oferta del radar y desde que dia; /api/radar-prueba (la misma llave) devuelve como le va: gasto, ventas, numero de ventas, ROAS y costo por venta
# desde ese dia (la serie de offers.json del dash, la misma de /api/datos). Va a estas columnas al final de OFERTAS (se crean la primera vez, en formato texto).
# Es NUESTRO resultado; las columnas «Seguimiento: …» (seguimiento.py) son las senales publicas del competidor en la Biblioteca. Solo celdas que cambian, nunca borra.
URL_PRUEBA = BASE + "/api/radar-prueba"
PRUEBA_OFERTA = "Prueba: oferta del dash"          # el nombre de nuestra oferta en el dash (el de su pestana de ventas)
PRUEBA_DESDE = "Prueba: desde"                     # el dia en que empezo la prueba (DD/MM/AAAA, como la columna Fecha)
PRUEBA_DIAS = "Prueba: días"                       # dias de prueba hasta hoy, contando el primero
PRUEBA_GASTO = "Prueba: gasto USD"
PRUEBA_VENTAS_USD = "Prueba: ventas USD"
PRUEBA_VENTAS = "Prueba: n.º de ventas"
PRUEBA_ROAS = "Prueba: ROAS"                       # ventas / gasto
PRUEBA_CPA = "Prueba: costo por venta USD"         # gasto / numero de ventas
PRUEBA_COLS = (PRUEBA_OFERTA, PRUEBA_DESDE, PRUEBA_DIAS, PRUEBA_GASTO, PRUEBA_VENTAS_USD, PRUEBA_VENTAS, PRUEBA_ROAS, PRUEBA_CPA)
MAX_PRUEBAS = 60                                   # filas por pasada, como MAX_CAMBIOS
OFERTA_MAX = 80


class DashError(Exception):
    pass


def leer_clave():
    """La llave del radar: de la variable DASH_CLAVE_RADAR o del archivo dash.key de esta carpeta (permisos 600). Vacia si no hay."""
    k = os.environ.get("DASH_CLAVE_RADAR", "").strip()
    f = HERE / "dash.key"
    if not k and f.exists():
        k = f.read_text(encoding="utf-8-sig").strip()          # utf-8-sig: si PowerShell dejo una marca BOM al principio, no entra en la llave
    return k


def _headers(clave):
    return {"x-clave-radar": clave, "Accept": "application/json", "User-Agent": "RadarOfertas/1.0"}


def _get_json(clave, session, url):
    """GET al dash con la llave; el JSON de la respuesta. Lanza DashError (sin repetir la llave) si no responde, no abre o no contesta JSON."""
    s = session or requests
    try:
        r = s.get(url, headers=_headers(clave), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise DashError(f"el dash no respondio ({e.__class__.__name__})")
    if r.status_code == 401:
        raise DashError("el dash rechazo la llave (401): la de dash.key no es la de CLAVE_RADAR en Cloudflare")
    if r.status_code == 503:
        raise DashError("el dash dice que CLAVE_RADAR no esta puesta o todavia no se aplico (503)")
    if r.status_code != 200:
        raise DashError(f"el dash contesto {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise DashError("el dash no contesto JSON")


def traer_estados(clave, session=None, url=None):
    """GET al dash con la llave. Devuelve {id: {estado, desde, fila, anun}} solo con lo bien formado; lanza DashError si no responde o no abre."""
    j = _get_json(clave, session, url or URL)
    crudos = j.get("estados") if isinstance(j, dict) else None
    if not isinstance(j, dict) or j.get("ok") is not True or not isinstance(crudos, dict):
        raise DashError("respuesta inesperada del dash")
    out = {}
    for id_, e in crudos.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", str(id_)) or not isinstance(e, dict) or e.get("estado") not in ESTADOS:
            continue
        desde = e.get("desde", "")
        if desde != "" and desde not in ESTADOS:
            continue
        try:
            fila = int(e.get("fila") or 0)
        except (TypeError, ValueError):
            fila = 0
        out[str(id_)] = {"estado": e["estado"], "desde": desde, "fila": fila, "anun": re.sub(r"\s+", " ", str(e.get("anun") or "")).strip()[:60],
                         "motivo": re.sub(r"\s+", " ", str(e.get("motivo") or "")).strip()[:MOTIVO_MAX]}
    return out


def avisar(clave, resumen, session=None, url=None):
    """Le dice al dash como le fue (aplicados, ya, conflictos, sinFila, error). Mejor esfuerzo: si no se puede, no pasa nada."""
    s = session or requests
    try:
        r = s.post(url or URL, headers=_headers(clave), json={"sync": resumen}, timeout=TIMEOUT)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _id_anuncio(fila):
    m = re.search(r"[?&]id=(\d{5,25})\b", fila.get("Link del anuncio") or "")
    return m.group(1) if m else ""


def _norm(s):
    return sheets.fold(re.sub(r"\s+", " ", str(s or "")).strip())[:60]


def planear(filas, estados, max_cambios=MAX_CAMBIOS):
    """filas = sheet.read_dicts('OFERTAS') (cada una con '_row'); estados = lo que devuelve traer_estados.
    Devuelve (cambios, resumen). cambios = [{'fila', 'id', 'de', 'a', 'anunciante'}] (solo lo que hay que escribir);
    resumen = {'aplicados', 'ya', 'conflictos', 'sinFila'} (aplicados = lo planeado, el que llama lo corrige si la escritura falla)."""
    por_anuncio = {}
    for f in filas:
        a = _id_anuncio(f)
        if a and a not in por_anuncio:
            por_anuncio[a] = f                  # la primera, igual que la pagina
    por_fila = {f.get("_row"): f for f in filas}
    cambios, ya, conflictos, sin_fila = [], 0, 0, 0
    for id_, e in sorted(estados.items()):
        f = None
        if re.fullmatch(r"a\d{5,25}", id_):
            f = por_anuncio.get(id_[1:])
        elif e.get("fila"):
            c = por_fila.get(e["fila"])
            if c is not None and e.get("anun") and _norm(c.get("Anunciante")) == _norm(e["anun"]):
                f = c
        if f is None:
            sin_fila += 1
            continue
        actual = (f.get("Status") or "").strip().lower()
        if actual == e["estado"]:
            ya += 1
        elif actual == e["desde"]:
            cambios.append({"fila": f["_row"], "id": id_, "de": actual, "a": e["estado"], "anunciante": re.sub(r"\s+", " ", str(f.get("Anunciante") or "")).strip()})
        else:
            conflictos += 1                      # la planilla ya dice otra cosa: alguien la cambio a mano despues, gana la planilla
    return cambios[:max_cambios], {"aplicados": len(cambios[:max_cambios]), "ya": ya, "conflictos": conflictos, "sinFila": sin_fila}


def planear_motivos(filas, estados, cambios):
    """Motivos que hay que escribir: [{'fila', 'motivo', 'anunciante'}]. Solo filas cuyo Status ya es el estado elegido (o lo va a ser con `cambios`) y cuyo
    MOTIVO_COL dice otra cosa. Una fila se encuentra igual que en planear."""
    por_anuncio, por_fila = {}, {f.get("_row"): f for f in filas}
    for f in filas:
        a = _id_anuncio(f)
        if a and a not in por_anuncio:
            por_anuncio[a] = f
    va_a = {c["fila"]: c["a"] for c in cambios}
    out = []
    for id_, e in sorted(estados.items()):
        if not e.get("motivo"):
            continue
        f = por_anuncio.get(id_[1:]) if re.fullmatch(r"a\d{5,25}", id_) else None
        if f is None and e.get("fila"):
            c = por_fila.get(e["fila"])
            f = c if c is not None and e.get("anun") and _norm(c.get("Anunciante")) == _norm(e["anun"]) else None
        if f is None:
            continue
        estado = va_a.get(f["_row"], (f.get("Status") or "").strip().lower())
        if estado == e["estado"] and re.sub(r"\s+", " ", str(f.get(MOTIVO_COL) or "")).strip() != e["motivo"]:
            out.append({"fila": f["_row"], "motivo": e["motivo"], "anunciante": re.sub(r"\s+", " ", str(f.get("Anunciante") or "")).strip()})
    return out


# ---- nuestra prueba de cada oferta, a la planilla (2026-11-04; ver PRUEBA_COLS) ----
def _cifra(v, entero=False):
    """Un numero del dash que se puede escribir: finito, sin signo y no booleano; None si no (lo que no se sabe no se escribe)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return int(round(v)) if entero else round(float(v), 2)


def traer_resultados(clave, session=None, url=None):
    """GET /api/radar-prueba con la llave. Devuelve {id: {oferta, inicio, dias, encontrada, gasto, ventas_usd, ventas, roas, cpa}} solo con lo bien formado
    (una cifra que falta o no es un numero queda en None); lanza DashError si no responde o no abre."""
    j = _get_json(clave, session, url or URL_PRUEBA)
    crudos = j.get("resultados") if isinstance(j, dict) else None
    if not isinstance(j, dict) or j.get("ok") is not True or not isinstance(crudos, dict):
        raise DashError("respuesta inesperada del dash")
    out = {}
    for id_, r in crudos.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", str(id_)) or not isinstance(r, dict):
            continue
        oferta = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(r.get("oferta") or ""))).strip()[:OFERTA_MAX]
        inicio = str(r.get("inicio") or "")
        try:
            inicio = datetime.strptime(inicio, "%Y-%m-%d").date() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", inicio) else None
        except ValueError:
            inicio = None
        if not oferta or inicio is None:
            continue
        out[str(id_)] = {"oferta": oferta, "inicio": inicio, "dias": _cifra(r.get("dias"), True), "encontrada": r.get("encontrada") is True,
                         "gasto": _cifra(r.get("gastoUsd")), "ventas_usd": _cifra(r.get("ventasUsd")), "ventas": _cifra(r.get("ventas"), True),
                         "roas": _cifra(r.get("roas")), "cpa": _cifra(r.get("cpaUsd"))}
    return out


def valores_prueba(r):
    """Las celdas, como TEXTO (las columnas quedan en «Texto sin formato» y se comparan tal cual se leen): dinero con 2 decimales y punto. Lo que el dash no sabe
    (la oferta ya no esta en su catalogo, o un offers.json sin el numero de ventas) no se escribe, para no pisar con ceros lo que ya habia."""
    v = {PRUEBA_OFERTA: r["oferta"], PRUEBA_DESDE: r["inicio"].strftime("%d/%m/%Y")}
    if r.get("dias") is not None:
        v[PRUEBA_DIAS] = str(r["dias"])
    if not r.get("encontrada"):
        return v
    for col, k in ((PRUEBA_GASTO, "gasto"), (PRUEBA_VENTAS_USD, "ventas_usd"), (PRUEBA_ROAS, "roas"), (PRUEBA_CPA, "cpa")):
        if r.get(k) is not None:
            v[col] = f"{r[k]:.2f}"
    if r.get("ventas") is not None:
        v[PRUEBA_VENTAS] = str(r["ventas"])
    return v


def planear_pruebas(filas, resultados, max_filas=MAX_PRUEBAS):
    """filas = OFERTAS (read_dicts, con '_row'); resultados = traer_resultados(). Devuelve [{'fila', 'id', 'anunciante', 'celdas': {columna: texto}}] solo con las celdas
    que cambian. Una fila se encuentra por el mismo id que le da el dash (seguimiento.id_oferta; si dos filas comparten id, la primera). Nunca borra."""
    import seguimiento                       # tarde: seguimiento importa este modulo
    if not resultados:
        return []
    vistas, out = set(), []
    for f in filas:
        id_ = seguimiento.id_oferta(f)
        if id_ in vistas:
            continue
        vistas.add(id_)
        r = resultados.get(id_)
        if r is None:
            continue
        actual = {sheets.fold(k): re.sub(r"\s+", " ", str(v if v is not None else "")).strip() for k, v in f.items() if not str(k).startswith("_")}
        celdas = {c: v for c, v in valores_prueba(r).items() if actual.get(sheets.fold(c), "") != v}
        if celdas:
            out.append({"fila": f.get("_row"), "id": id_, "anunciante": re.sub(r"\s+", " ", str(f.get("Anunciante") or "")).strip()[:60], "celdas": celdas})
        if len(out) >= max_filas:
            break
    return out
