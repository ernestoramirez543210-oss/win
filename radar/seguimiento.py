"""
seguimiento.py - Una "foto" diaria de cada oferta que el dueno esta vigilando, para que el dash diga con numeros si vale la pena modelarla.

Pedido del dueno (2026-10-21): al poner una oferta en «Vigilar» y entrar a revisarla, ver cuantos anuncios tuvo activos cada dia y las demas senales que
dicen si conviene modelarla, y seguirla asi hasta que se descarte o se modele. La Biblioteca de Meta NO guarda los anuncios que se apagan (probado el
2026-10-21: con active_status=all solo devuelve activos), asi que el pasado no se puede pedir: hay que fotografiar el presente todos los dias.

Que se sigue: las filas de OFERTAS con Status «vigilar» o «testiando» (ESTADOS_SEGUIR). Al cambiar a otro estado el seguimiento se detiene solo; lo ya
guardado queda en el dash.

Que tiene una foto (todo sale de la Biblioteca, sin Gemini ni tokens; cada oferta cuesta 5 peticiones al HTML de la pagina del anunciante, sin graphql):
    d    dia de la foto en el calendario de Meta (hora del Pacifico; se lee del end_date de un anuncio activo)
    a    anuncios activos: el `count` que cuenta la propia Biblioteca (exacto aunque la pagina 1 solo traiga 30)
    s0, s7, s30, s60   de esos, cuantos empezaron ANTES de hoy / de hace 7 / de hace 30 / de hace 60 dias (filtro start_date[max] de la Biblioteca).
         Son los "sobrevivientes por edad": un anuncio que sigue activo a los 30 dias es uno que su dueno no apago porque vende.
    p    piezas distintas en la pagina 1 (variedad de creativos);  vd = % de esas piezas que son video;  wa = % de los anuncios que mandan a WhatsApp
    v    versiones del anuncio mas repetido (cuantos anuncios comparten el mismo creativo: lo que mas escalan)
    lk   seguidores de la pagina
    vis  anuncios que se alcanzan a ver en la pagina 1 (si vis == a se ven todos y k/nv son exactos; si no, son del top de la pagina 1 por impresiones)
    k    anuncios de la foto anterior que ya no estan (apagados);  nv = anuncios nuevos respecto a la foto anterior;  pd = dia de esa foto anterior
    top  id del anuncio con mas impresiones (el primero que ordena la Biblioteca): el "anuncio estrella" para abrirlo y estudiarlo
    pn, pg, hs   nombre de la pagina que se midio, su id y el destino al que mas apuntan sus anuncios: el dash los muestra para que se vea de quien son los numeros

Donde queda: seguimiento.json (carpeta del radar, 600; es la copia de respaldo y guarda los ids de la foto anterior para calcular k y nv) y el dash
(POST /api/radar-seguimiento con la misma llave dash.key de los estados: ver dash_estados.py). Si el dash no contesta, las fotos esperan en
seguimiento.json y se reenvian en la siguiente corrida: no se pierde ninguna.

Reglas: solo LEE (Meta y la planilla); no escribe en la planilla ni toca ofertas. Una oferta con foto de hoy (hora de Bogota) no se vuelve a fotografiar,
asi que el timer puede pasar dos veces al dia sin duplicar peticiones. Si Meta limita la IP se corta la corrida y se envia lo ya tomado.
"""
import json
import math
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

import dash_estados
from archivos import escribir_atomico

HERE = Path(os.environ.get("RADAR_DATOS") or Path(__file__).resolve().parent)   # carpeta de los DATOS (en el VPS /opt/radar-de-ofertas; el codigo vive aparte)
RUTA = HERE / "seguimiento.json"
BASE = os.environ.get("DASH_URL", "").rstrip("/")
URL = BASE + "/api/radar-seguimiento"
URL_MANUAL = BASE + "/api/radar-manual"
ESTADOS_SEGUIR = ("vigilar", "testiando")
CORTES = (0, 7, 30, 60)            # dias: "empezaron antes de hoy / hace 7 / hace 30 / hace 60 dias"
MAX_FOTOS = 400                    # por oferta en seguimiento.json (mas de un ano de fotos diarias)
POR_ENVIO = 25                     # fotos por POST (el dash guarda una clave de KV por foto y tiene un tope de subpeticiones)
TIMEOUT = 25
RED_FALLOS_MAX = 3                 # fallos de red seguidos tras los cuales se corta la corrida
WHATSAPP_HOSTS = {"wa.me", "api.whatsapp.com", "whatsapp.com", "chat.whatsapp.com"}
SOCIALES = {"fb.me", "m.me", "fb.com", "l.facebook.com", "facebook.com", "instagram.com", "ig.me", "messenger.com"}
BOGOTA = timezone(timedelta(hours=-5))          # Colombia no tiene horario de verano


class SinDatos(Exception):
    """Meta contesto pero la pagina no trae resultados utilizables (otra estructura, un muro)."""


# ---- texto y ids: lo mismo que hace functions/_radar.js al leer la planilla, para que el id de una oferta aqui sea el que ve el dash ----
_CONTROL = re.compile("[" + "".join(chr(c) for c in (*range(0, 9), 11, 12, *range(14, 32), 127)) + "]")


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "").lower().strip())
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def limpio(v, tope=None):
    s = _CONTROL.sub(" ", str("" if v is None else v))
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", " ", s).strip()
    return s[:tope - 1].rstrip() + "…" if tope and len(s) > tope else s


def fecha_iso(v):
    s = str(v or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        d, mes = int(m.group(1)), int(m.group(2))
        return f"{m.group(3)}-{mes:02d}-{d:02d}" if 1 <= d <= 31 and 1 <= mes <= 12 else ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _base36(n):
    if n == 0:
        return "0"
    d, s = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while n:
        n, r = divmod(n, 36)
        s = d[r] + s
    return s


def huella(s):
    """FNV-1a de 32 bits sobre las unidades UTF-16 del texto, en base 36: identica a huella() de functions/_radar.js."""
    h = 0x811C9DC5
    b = s.encode("utf-16-le")
    for i in range(0, len(b), 2):
        h ^= b[i] | (b[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return _base36(h)


def _celdas(fila):
    return {fold(k): v for k, v in fila.items() if not str(k).startswith("_")}


def ad_id_de(link):
    m = re.search(r"[?&]id=(\d{5,25})\b", str(link or ""))
    return m.group(1) if m else ""


def page_id_de_enlace(link):
    """El id de pagina de un enlace de la Biblioteca que ya lo trae (view_all_page_id), como el de las filas pegadas a mano."""
    m = re.search(r"[?&]view_all_page_id=(\d{5,25})\b", str(link or ""))
    return m.group(1) if m else ""


def id_oferta(fila):
    """El id que el dash le da a la fila: 'a<id del anuncio>' o 'h<huella de anunciante|fecha|palabra>'."""
    c = _celdas(fila)
    ad = ad_id_de(c.get("link del anuncio"))
    if ad:
        return "a" + ad
    return "h" + huella("|".join([limpio(c.get("anunciante"), 90), fecha_iso(c.get("fecha")), limpio(c.get("palabra clave"), 120)]))


def pais_de(palabra):
    """El pais en que se fotografia al anunciante: el de su busqueda de oro en espanol («oro MX: recetas pdf», ver radar.py «Oro en espanol») o Brasil."""
    m = re.match(r"oro ([a-z]{2}):", fold(palabra))
    return m.group(1).upper() if m else "BR"


def elegir(filas):
    """Las filas que se siguen: Status vigilar/testiando, una por id (la primera, igual que el dash)."""
    vistas, out = set(), []
    for f in filas:
        c = _celdas(f)
        estado = fold(c.get("status") or c.get("estado"))
        if estado not in ESTADOS_SEGUIR:
            continue
        id_ = id_oferta(f)
        if id_ in vistas:
            continue
        vistas.add(id_)
        link = c.get("link del anuncio") or ""
        out.append({"id": id_, "anunciante": limpio(c.get("anunciante"), 90), "estado": estado, "fila": f.get("_row"),
                    "ad_id": ad_id_de(link), "page_id": page_id_de_enlace(link), "pais": pais_de(c.get("palabra clave"))})
    return out


def traer_manuales(clave, session=None, url=None):
    """Las ofertas que el equipo agrego a mano en /radar (el dash las guarda aparte, no estan en la planilla): GET con la llave del radar, SOLO lectura.
    Devuelve [{id, anunciante, estado, enlaces:[{url, tipo}]}] con lo bien formado (el estado es el que el dueño le puso en el dash); lanza dash_estados.DashError si no contesta o no abre."""
    s = session or requests
    try:
        r = s.get(url or URL_MANUAL, headers=dash_estados._headers(clave), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise dash_estados.DashError(f"el dash no respondio ({e.__class__.__name__})")
    if r.status_code == 401:
        raise dash_estados.DashError("el dash rechazo la llave (401): la de dash.key no es la de CLAVE_RADAR en Cloudflare")
    if r.status_code == 503:
        raise dash_estados.DashError("el dash dice que CLAVE_RADAR no esta puesta o todavia no se aplico (503)")
    if r.status_code == 404:
        raise dash_estados.DashError("el dash todavia no tiene la lectura de las ofertas a mano (404): falta el despliegue")
    if r.status_code != 200:
        raise dash_estados.DashError(f"el dash contesto {r.status_code}")
    try:
        j = r.json()
    except ValueError:
        raise dash_estados.DashError("el dash no contesto JSON")
    if not isinstance(j, dict) or j.get("ok") is not True or not isinstance(j.get("ofertas"), list):
        raise dash_estados.DashError("respuesta inesperada del dash")
    out = []
    for m in j["ofertas"]:
        if not isinstance(m, dict) or not re.fullmatch(r"m[a-z0-9]{8}", str(m.get("id") or "")) or m.get("estado") not in dash_estados.ESTADOS:
            continue
        enlaces = []
        for e in m.get("enlaces") if isinstance(m.get("enlaces"), list) else []:
            if isinstance(e, dict) and isinstance(e.get("url"), str) and e.get("tipo") in ("anuncio", "anunciante", "pagina", "red") and len(e["url"]) <= 400:
                enlaces.append({"url": e["url"], "tipo": e["tipo"]})
        out.append({"id": m["id"], "anunciante": limpio(m.get("anunciante"), 90), "estado": m["estado"], "enlaces": enlaces})
    return out


def elegir_manuales(manuales):
    """De las ofertas a mano que trae `traer_manuales`, las que se siguen (Status del dash vigilar/testiando). Una oferta a mano se ubica en la Biblioteca por el enlace de uno de sus
    anuncios (hasta varios, se prueban en orden) o por el de la pagina del anunciante: con solo una pagina de ventas o un video no hay forma de saber que pagina de Meta es (sin_pagina)."""
    out = []
    for m in manuales:
        if m["estado"] not in ESTADOS_SEGUIR:
            continue
        ad_ids = [ad_id_de(e["url"]) for e in m["enlaces"] if e["tipo"] == "anuncio" and ad_id_de(e["url"])]
        page_id = next((page_id_de_enlace(e["url"]) for e in m["enlaces"] if e["tipo"] == "anunciante" and page_id_de_enlace(e["url"])), "")
        out.append({"id": m["id"], "anunciante": m["anunciante"], "estado": m["estado"], "fila": None, "ad_id": ad_ids[0] if ad_ids else "", "ad_ids": ad_ids,
                    "page_id": page_id, "manual": True})
    return out


def indice_nombres(seen_pages):
    """{nombre sin tildes: [id de pagina]} de lo que el radar ya vio (state.json), para hallar la pagina de una oferta por su anunciante sin gastar una peticion."""
    idx = {}
    for pid, e in (seen_pages or {}).items():
        n = fold((e or {}).get("name"))
        if n and re.fullmatch(r"\d{5,25}", str(pid)):
            idx.setdefault(n, []).append(str(pid))
    return idx


def resolver_pagina(o, reg, nombres, meta, pausa):
    """(page_id, de_donde). Orden: el enlace de la fila, lo ya resuelto, el nombre en state.json (si es unico) y por ultimo la pagina del anuncio en la Biblioteca (1 peticion)."""
    if o["page_id"]:
        return o["page_id"], "enlace"
    if reg.get("pid"):
        return reg["pid"], "guardado"
    # El nombre de una oferta a mano lo puso el equipo (no es el de ninguna pagina): solo las de la planilla se ubican por el nombre que el radar ya vio.
    cands = [] if o.get("manual") else nombres.get(fold(o["anunciante"]), [])
    if len(cands) == 1:
        return cands[0], "state"
    for ad in (o.get("ad_ids") or ([o["ad_id"]] if o["ad_id"] else [])):
        pausa()
        pid = meta.pagina_de_anuncio(ad)
        if pid:
            return pid, "anuncio"
    return "", ""


# ---- la foto ----
def dia_bogota(ahora=None):
    return (ahora or datetime.now(timezone.utc)).astimezone(BOGOTA).strftime("%Y-%m-%d")


def dia_de_meta(ads, ahora=None):
    """El dia de la Biblioteca (calendario del Pacifico): el end_date de un anuncio ACTIVO es la medianoche de hoy en ese calendario. Sin anuncios, hora de Bogota menos 2 h."""
    fines = [a.get("end") for a in ads if a.get("end")]
    if fines:
        return datetime.fromtimestamp(max(fines), timezone.utc).strftime("%Y-%m-%d")
    return ((ahora or datetime.now(timezone.utc)) - timedelta(hours=8)).strftime("%Y-%m-%d")


def host_de(url):
    if not url:
        return ""
    h = urlparse(url if "//" in url else "//" + url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def va_a_whatsapp(a):
    return host_de(a.get("link")) in WHATSAPP_HOSTS or "WHATSAPP" in str(a.get("cta_type") or "").upper()


def foto_de(principal, cortes, previa=None, ahora=None, pid=""):
    """La foto de hoy a partir de la pagina 1 (`principal`, lo que devuelve Meta.pagina_anunciante) y de los totales filtrados por edad (`cortes` = {dias: total}).
    `previa` = {"ids": [...], "d": 'AAAA-MM-DD'} de la foto anterior, o None. `pid` = la pagina que se midio. Devuelve None si no hay un total confiable."""
    ads = principal["ads"]
    total = principal["total"]
    if total is None and not principal["hay_mas"]:
        total = sum(max(1, a["collation_count"]) for a in ads)          # la pagina trae todo: el total es lo que se ve
    if total is None:
        return None
    piezas = {}
    for a in ads:
        piezas.setdefault(a["sig"], a)
    ids = [a["archive_id"] for a in ads if a["archive_id"]]
    foto = {
        "d": dia_de_meta(ads, ahora), "t": (ahora or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "a": total,
        "p": len(piezas),
        "vd": round(100 * sum(1 for p in piezas.values() if p["video"]) / len(piezas)) if piezas else 0,
        "wa": round(100 * sum(1 for a in ads if va_a_whatsapp(a)) / len(ads)) if ads else 0,
        "v": max((a["collation_count"] for a in ads), default=0),
        "lk": max((a.get("page_like") or 0 for a in ads), default=0),
        "vis": len(ads),
        "top": ids[0] if ids else "",
    }
    # QUE pagina se midio: nombre, id y el destino al que mas apuntan sus anuncios. El dash los muestra para que se vea siempre de quien son los numeros y avisa si el nombre no se
    # parece al de la planilla (el 2026-10-21 el resolutor viejo dio Alibaba.com para «Moleque Arteiro»; una fila manual como «Arco Reabilitação» si es de otra pagina: la de un doctor que vende su marca).
    nombre = next((a["page_name"] for a in ads if a.get("page_name")), "")
    if nombre:
        foto["pn"] = re.sub(r"\s+", " ", str(nombre)).strip()[:80]
    if re.fullmatch(r"\d{5,25}", str(pid or "")):
        foto["pg"] = str(pid)
    hosts = Counter(h for h in (host_de(a.get("link")) for a in ads) if h and h not in WHATSAPP_HOSTS and h not in SOCIALES)
    if hosts:
        foto["hs"] = hosts.most_common(1)[0][0][:60]
    # Sobrevivientes por edad: nunca mas que el total ni mas que el corte anterior (una foto raramente se contradice si Meta cambia entre dos peticiones).
    tope = total
    for k in CORTES:
        n = cortes.get(k)
        if n is not None:
            tope = min(tope, n)
            foto["s%d" % k] = tope
    if total == 0:
        for k in CORTES:
            foto["s%d" % k] = 0
    if previa and previa.get("ids") is not None:
        ahora_ids, antes = set(ids), set(previa["ids"])
        foto["k"], foto["nv"], foto["pd"] = len(antes - ahora_ids), len(ahora_ids - antes), previa.get("d", "")
    return foto, ids


def tomar_foto(meta, pid, previa, pausa, ahora=None, pais="BR"):
    """Hasta 5 peticiones: la pagina 1 sin filtro y el total de cada corte de edad. Una pagina sin anuncios (total 0) no necesita los cortes.
    Devuelve (foto, ids de la pagina 1). pais = donde se miran sus anuncios (Brasil, o el de su busqueda de oro)."""
    principal = meta.pagina_anunciante(pid, country=pais)
    if principal is None:
        raise SinDatos("la Biblioteca no trajo resultados para la pagina")
    cortes = {}
    if (principal["total"] or 0) > 0:
        hoy = datetime.strptime(dia_de_meta(principal["ads"], ahora), "%Y-%m-%d")
        for k in CORTES:
            pausa()
            r = meta.pagina_anunciante(pid, country=pais, antes_de=(hoy - timedelta(days=k)).strftime("%Y-%m-%d"))
            cortes[k] = r["total"] if r else None
    res = foto_de(principal, cortes, previa, ahora, pid)
    if res is None:
        raise SinDatos("la Biblioteca no dio el total de anuncios")
    return res


# ---- archivo de respaldo ----
def cargar(ruta=RUTA):
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("ofertas"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "ofertas": {}}


def guardar(datos, ruta=RUTA):
    escribir_atomico(ruta, json.dumps(datos, ensure_ascii=False, separators=(",", ":")))


# ---- envio al dash ----
def enviar(clave, fotos, corrida, session=None, url=None):
    """POST de las fotos (de a POR_ENVIO) al dash con la llave del radar. Devuelve (guardadas, rechazadas); lanza dash_estados.DashError si no contesta o no abre.
    `fotos` = [{"id": ..., + campos de la foto}]. El aviso de la corrida viaja en el primer envio (y solo si no hay fotos, en uno propio)."""
    s = session or requests
    guardadas = rechazadas = 0
    lotes = [fotos[i:i + POR_ENVIO] for i in range(0, len(fotos), POR_ENVIO)] or [[]]
    for i, lote in enumerate(lotes):
        cuerpo = {"fotos": lote}
        if i == 0:
            cuerpo["corrida"] = corrida
        try:
            r = s.post(url or URL, headers=dash_estados._headers(clave), json=cuerpo, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise dash_estados.DashError(f"el dash no respondio ({e.__class__.__name__})")
        if r.status_code == 401:
            raise dash_estados.DashError("el dash rechazo la llave (401): la de dash.key no es la de CLAVE_RADAR en Cloudflare")
        if r.status_code == 503:
            raise dash_estados.DashError("el dash dice que CLAVE_RADAR no esta puesta o todavia no se aplico (503)")
        if r.status_code == 404:
            raise dash_estados.DashError("el dash todavia no tiene /api/radar-seguimiento (404): falta el despliegue")
        if r.status_code != 200:
            raise dash_estados.DashError(f"el dash contesto {r.status_code}")
        try:
            j = r.json()
        except ValueError:
            raise dash_estados.DashError("el dash no contesto JSON")
        if not isinstance(j, dict) or j.get("ok") is not True:
            raise dash_estados.DashError("respuesta inesperada del dash")
        guardadas += int(j.get("guardadas") or 0)
        rechazadas += int(j.get("rechazadas") or 0)
    return guardadas, rechazadas


# ---- la corrida ----
def correr(meta, filas, nombres, clave, limite, red, ruta=RUTA, simular=False, ahora=None, pausa=None, session=None, url=None, log=print, url_manual=None):
    """Una pasada: fotografia las ofertas seguidas que aun no tienen foto de hoy, guarda y envia lo pendiente. Devuelve (codigo, resumen).
    `limite` = excepcion de Meta que corta la corrida (RateLimited); `red` = excepciones de red que solo saltan la oferta (3 seguidas cortan).
    codigo: 0 todo bien, 1 algo fallo (Meta no contesto, el dash no recibio), 3 Meta limito la IP (el timer reintenta)."""
    pausa = pausa or (lambda: None)
    datos = cargar(ruta)
    hoy_b = dia_bogota(ahora)
    seguir = elegir(filas)
    # Las que el equipo agrego a mano: viven en el dash, no en la planilla. Sin llave o si el dash no contesta se sigue solo con las de la planilla (y se dice).
    error_manuales = ""
    if clave:
        try:
            ya = {o["id"] for o in seguir}
            seguir += [m for m in elegir_manuales(traer_manuales(clave, session, url_manual)) if m["id"] not in ya]
        except dash_estados.DashError as e:
            error_manuales = str(e)[:150]
            log(f"!! No pude leer las ofertas agregadas a mano ({error_manuales}): sigo solo con las de la planilla.", flush=True)
    r = {"t": (ahora or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), "seguidas": len(seguir), "fotos": 0, "ya": 0, "sinPagina": 0, "sinPaginaIds": [], "errores": 0, "limitado": False, "error": ""}
    if error_manuales:
        r["error"] = "No pude leer las ofertas a mano: " + error_manuales          # se ve en el aviso al dash y deja el codigo en 1
    fallos_red = 0
    for o in seguir:
        reg = datos["ofertas"].get(o["id"]) or {}          # solo entra al archivo si llega a tener una foto
        if reg.get("ultima") == hoy_b:
            r["ya"] += 1
            continue
        try:
            pid, como = resolver_pagina(o, reg, nombres, meta, pausa)
            if not pid:
                r["sinPagina"] += 1
                r["sinPaginaIds"].append(o["id"])          # el dash dice cuales son y como arreglarlo (pegar el enlace de su pagina en la fila)
                log(f"   {o['anunciante'][:30]}: no pude saber cual es su pagina en la Biblioteca (sin enlace de pagina ni anuncio): no se sigue por ahora", flush=True)
                continue
            previa = {"ids": reg["ids"], "d": reg.get("d", "")} if reg.get("pid") == pid and isinstance(reg.get("ids"), list) else None
            if reg.get("pid") != pid:
                reg.pop("ids", None)
            pausa()
            foto, ids = tomar_foto(meta, pid, previa, pausa, ahora, o.get("pais") or "BR")
            fallos_red = 0
        except limite as e:
            r["limitado"] = True
            r["error"] = f"Meta limito la IP: {str(e)[:80]}"
            log(f"!! Meta limito la IP ({str(e)[:80]}): corto aqui y envio lo ya tomado.", flush=True)
            break
        except red + (SinDatos,) as e:
            r["errores"] += 1
            fallos_red += 1
            log(f"   {o['anunciante'][:30]}: no pude fotografiarla ({e.__class__.__name__}: {str(e)[:80]})", flush=True)
            if fallos_red >= RED_FALLOS_MAX:
                r["error"] = f"Meta no respondio {fallos_red} veces seguidas"
                log(f"!! {r['error']}: corto aqui.", flush=True)
                break
            continue
        r["fotos"] += 1
        log(f"   {o['anunciante'][:30]} ({como}): {foto['a']} activos (>7 d: {foto.get('s7', '?')}, >30 d: {foto.get('s30', '?')}, >60 d: {foto.get('s60', '?')})"
            + (f", apagados {foto['k']}, nuevos {foto['nv']}" if "k" in foto else "") + (" [SIMULACION]" if simular else ""), flush=True)
        if simular:
            continue
        datos["ofertas"][o["id"]] = reg
        reg.update(pid=pid, anun=o["anunciante"], ids=ids, d=foto["d"], ultima=hoy_b)
        fotos = [f for f in reg.get("fotos", []) if f.get("d") != foto["d"]] + [foto]
        reg["fotos"] = fotos[-MAX_FOTOS:]
        reg["pend"] = sorted(set(reg.get("pend", [])) | {foto["d"]})
    if simular:
        return (3 if r["limitado"] else 1 if r["errores"] else 0), r

    # Lo que falta por entregar al dash (de hoy y de corridas anteriores en las que el dash no contesto): solo de las ofertas que se siguen
    pendientes = []
    for id_, reg in datos["ofertas"].items():
        for d in reg.get("pend", []):
            f = next((x for x in reg.get("fotos", []) if x.get("d") == d), None)
            if f is not None:
                pendientes.append((id_, d, f))
    codigo = 3 if r["limitado"] else 1 if (r["errores"] or r["error"]) else 0
    if clave:
        try:
            guardadas, rechazadas = enviar(clave, [{"id": i, **f} for i, _d, f in pendientes], r, session, url)
            r["enviadas"] = guardadas
            if guardadas + rechazadas >= len(pendientes):
                for reg in datos["ofertas"].values():
                    reg["pend"] = []                  # lo que el dash rechazo (no cumple su formato) no mejora reenviandolo
                if rechazadas:
                    log(f"   El dash rechazo {rechazadas} fotos por su formato: no se reenvian.", flush=True)
            else:
                log(f"   El dash guardo {guardadas} de {len(pendientes)} fotos: el resto queda para la proxima corrida.", flush=True)
                codigo = codigo or 1
        except dash_estados.DashError as e:
            r["error"] = r["error"] or str(e)[:150]
            codigo = codigo or 1
            log(f"!! No pude entregar las fotos al dash: {str(e)[:150]}. Quedan guardadas aqui y se reenvian en la proxima corrida.", flush=True)
    else:
        log("Sin dash.key: las fotos quedan guardadas en seguimiento.json y se enviaran cuando haya llave.", flush=True)
    guardar(datos, ruta)
    return codigo, r


# ---- lo que dice el seguimiento, a la planilla (2026-11-04) ----
# Pedido del dueno: la planilla no sabia como iban las ofertas que se probaron, asi que el radar no podia aprender que senales tenian las que vendieron.
# `radar.py --sync-estados` (cada 5 min) escribe en estas columnas, al final de OFERTAS, lo mismo que la tarjeta del dash dice de cada oferta seguida
# («Dia 14 · ▼ -27 %»), sacado de las fotos de seguimiento.json (no del dash: las fotos las toma este radar). Son las SENALES PUBLICAS de la Biblioteca
# (los anuncios del anunciante), no el gasto ni las ventas de nuestra prueba: el dash no sabe que oferta propia corresponde a cada oferta del radar.
# Va en TODAS las filas que tienen fotos, sigan o no en «vigilar»/«testiando»: la ultima foto queda como estaba la oferta cuando se decidio.
RESULTADO_DIA = "Seguimiento: día"                     # dias desde la primera foto hasta la ultima, contando los dos (el «Dia 14» de la tarjeta)
RESULTADO_TENDENCIA = "Seguimiento: tendencia %"       # cambio de los anuncios activos contra la foto de hace 7 dias (o la primera, si lleva 3 a 6): el «-27 %»
RESULTADO_ACTIVOS = "Seguimiento: anuncios activos"    # en la ultima foto
RESULTADO_FOTO = "Seguimiento: última foto"            # dia de esa foto (DD/MM/AAAA, como la columna Fecha)
RESULTADO_COLS = (RESULTADO_DIA, RESULTADO_TENDENCIA, RESULTADO_ACTIVOS, RESULTADO_FOTO)
MAX_RESULTADOS = 60                                    # filas por pasada (como dash_estados.MAX_CAMBIOS): la primera vez se ponen al dia en unas pocas pasadas


def _dia(d):
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").date() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)) else None
    except ValueError:
        return None


def serie_fotos(fotos):
    """[(dia, activos)] ordenado, una por dia (si hay dos del mismo dia gana la ultima), solo las que traen un dia real y un total. Igual que serie() de templates/radar-seguimiento.js."""
    por = {}
    for f in fotos if isinstance(fotos, list) else []:
        if not isinstance(f, dict) or isinstance(f.get("a"), bool):
            continue
        d = _dia(f.get("d"))
        try:
            a = float(f.get("a"))
        except (TypeError, ValueError):
            continue
        if d is not None and math.isfinite(a):
            por[d] = a
    return sorted(por.items())


def _redondo(x):
    return int(math.floor(x + 0.5))                    # Math.round de JavaScript (round() de Python redondea al par)


def resultado(fotos):
    """{dia, tendencia, activos, foto} de las fotos de una oferta, con los mismos calculos que la tarjeta del dash (analizar y cambio en templates/radar-seguimiento.js).
    tendencia = None si aun no hay con que comparar (menos de 3 dias). None si no hay ninguna foto util."""
    s = serie_fotos(fotos)
    if not s:
        return None
    (d0, _a0), (du, au) = s[0], s[-1]
    base = None
    for d, a in reversed(s[:-1]):
        if (du - d).days >= 7:
            base = (d, a)
            break
    if base is None and len(s) > 1 and (du - d0).days >= 3:
        base = s[0]
    tendencia = None
    if base is not None:
        tendencia = _redondo((au - base[1]) / base[1] * 100) if base[1] > 0 else (100 if au > 0 else 0)
    return {"dia": (du - d0).days + 1, "tendencia": tendencia, "activos": _redondo(au), "foto": du}


def valores_resultado(r):
    """Las celdas, como TEXTO (las columnas quedan en «Texto sin formato» y se comparan tal cual se leen). Una tendencia que aun no existe no se escribe."""
    v = {RESULTADO_DIA: str(r["dia"]), RESULTADO_ACTIVOS: str(r["activos"]), RESULTADO_FOTO: r["foto"].strftime("%d/%m/%Y")}
    if r["tendencia"] is not None:
        v[RESULTADO_TENDENCIA] = str(r["tendencia"])
    return v


def planear_resultados(filas, datos, max_filas=MAX_RESULTADOS):
    """filas = OFERTAS (read_dicts, con '_row'); datos = cargar(). Devuelve [{'fila', 'id', 'anunciante', 'celdas': {columna: texto}}] solo con las celdas que
    cambian. Una fila se encuentra por el mismo id que le da el dash (id_oferta; si dos filas comparten id, la primera). Nunca borra: lo que no se sabe no se escribe."""
    ofertas = datos.get("ofertas") if isinstance(datos, dict) else None
    if not isinstance(ofertas, dict) or not ofertas:
        return []
    vistas, out = set(), []
    for f in filas:
        id_ = id_oferta(f)
        if id_ in vistas:
            continue
        vistas.add(id_)
        reg = ofertas.get(id_)
        r = resultado(reg.get("fotos")) if isinstance(reg, dict) else None
        if r is None:
            continue
        actual = {fold(k): re.sub(r"\s+", " ", str(v if v is not None else "")).strip() for k, v in f.items() if not str(k).startswith("_")}
        celdas = {c: v for c, v in valores_resultado(r).items() if actual.get(fold(c), "") != v}
        if celdas:
            out.append({"fila": f.get("_row"), "id": id_, "anunciante": limpio(_celdas(f).get("anunciante"), 60), "celdas": celdas})
        if len(out) >= max_filas:
            break
    return out
