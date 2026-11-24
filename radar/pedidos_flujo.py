"""
pedidos_flujo.py - Atiende los pedidos de «Buscar el WhatsApp» que el equipo deja en el dash (/radar).

El equipo pega el enlace de la Biblioteca de anuncios de una pagina; el dash guarda un pedido (functions/api/radar-flujo-buscar.js) y este modulo, que corre en el VPS
cada pocos minutos (radar-pedidos.timer), lo atiende: abre la pagina en la Biblioteca, busca en Google su publicacion real (la misma busqueda de publicacion.py) y deja el resultado en el dash,
que se lo muestra a quien lo pidio. No escribe en la planilla.

Reglas: sin dash.key no hace nada; la llave viaja solo en una cabecera y no se imprime; hasta MAXIMO pedidos por pasada; si Meta limita la IP se corta y el pedido sigue pendiente
(el dash lo vuelve a ofrecer); si la busqueda en Google se queda sin credito se le dice al equipo con un mensaje claro.

POR ANUNCIO (2026-11-13): el pedido que sale de la ficha de una oferta trae tambien el ID del ANUNCIO de esa oferta. Entonces se busca SOLO la publicacion de ese anuncio (su texto
como unico angulo) y, si Google no la tiene, se contesta «sin_publicacion»; si el anuncio ya no esta activo, «sin_anuncio». Nunca se da la de otra oferta de la pagina: antes se
buscaba con los angulos de TODA la pagina, del mas repetido al menos, y a Mentora (oferta de «cuerpo astral») le toco la publicacion de su oferta de filosofia. Medido ese dia
con la API real: con solo el texto de su anuncio Google trae su publicacion (parecido 1,0). El ID del video del anuncio (xpv_asset_id) NO se usa para armar el enlace: es el archivo
de video dentro del anuncio, no una publicacion, y Facebook no lo abre (verificado el 2026-10-29; ver publicacion.py). Los pedidos sin anuncio (el cuadro «Buscar el WhatsApp»,
donde se pega solo la pagina) siguen como antes: la publicacion del angulo mas repetido de la pagina.
"""
import requests

import buscar_es
import dash_estados
import flujo
import publicacion

URL = dash_estados.BASE + "/api/radar-flujo-buscar"
MAXIMO = 5
TIMEOUT = dash_estados.TIMEOUT


class PedidosError(Exception):
    pass


def _revisar(r):
    if r.status_code == 401:
        raise PedidosError("el dash rechazo la llave (401)")
    if r.status_code == 503:
        raise PedidosError("el dash dice que CLAVE_RADAR no esta puesta o todavia no se aplico (503)")
    if r.status_code >= 400 and r.status_code != 409:
        raise PedidosError(f"el dash contesto {r.status_code}")


def _anuncio(v):
    """El ID del anuncio de un pedido: solo digitos (5 a 25), o ''."""
    s = str(v or "")
    return s if 5 <= len(s) <= 25 and s.isdigit() and s.isascii() else ""


def traer_pedidos(clave, session=None, url=None):
    """[{pagina, anuncio, modo, palabras}] de los pedidos por atender: la pagina solo con digitos; anuncio = el ID del anuncio de la oferta (solo digitos; '' en los pedidos de la pagina);
    modo «es» = buscar la oferta en espanol (palabras = las que escribio el equipo; nunca lleva anuncio)."""
    s = session or requests
    try:
        r = s.get((url or URL) + "?pendientes=1", headers=dash_estados._headers(clave), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise PedidosError(f"el dash no respondio ({e.__class__.__name__})")
    _revisar(r)
    try:
        lista = r.json().get("pedidos") or []
    except ValueError:
        raise PedidosError("el dash no devolvio JSON")
    out = []
    for p in lista:
        if isinstance(p, dict) and str(p.get("pagina") or "").isdigit():
            es = p.get("modo") == "es"
            if p.get("anuncio") and (es or not _anuncio(p.get("anuncio"))):
                continue                    # un anuncio raro (o en un pedido en espanol) no se atiende como si fuera de la pagina: daria la publicacion de otra oferta
            out.append({"pagina": str(p["pagina"]), "anuncio": "" if es else _anuncio(p.get("anuncio")), "modo": "es" if es else "", "palabras": buscar_es.limpiar_palabras(p.get("palabras"))})
    return out


def traer_pendientes(clave, session=None, url=None):
    """[id de pagina] de los pedidos por atender (solo digitos)."""
    return [p["pagina"] for p in traer_pedidos(clave, session, url)]


def enviar(clave, cuerpo, session=None, url=None):
    """Deja el resultado de un pedido. True si el dash lo recibio (409 = ya no estaba pendiente: tambien se da por cerrado)."""
    s = session or requests
    try:
        r = s.post(url or URL, json=cuerpo, headers=dash_estados._headers(clave), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise PedidosError(f"el dash no respondio al dejar el resultado ({e.__class__.__name__})")
    _revisar(r)
    return True


def _en_espanol(meta, pid, escritas, ads, nombre, pausa, traduccion):
    """El resultado «candidatas»: paginas con anuncios en espanol del mismo tema que mandan a WhatsApp. Sin palabras escritas se arman con el anuncio mas repetido de la pagina."""
    if escritas:
        consultas = [escritas]
    else:
        qw, gloss = traduccion or ((lambda t, k: t.split()[:k]), {})
        consultas = buscar_es.palabras_auto(publicacion.angulos(ads, lambda a: True), qw, gloss)
    cands = buscar_es.candidatas(meta, consultas, flujo.vende_por_whatsapp, pid, pausa) if consultas else []
    return {"pagina": pid, "estado": "candidatas", "nombre": nombre, "candidatas": cands,
            "mensaje": ("buscado: " + " | ".join(consultas)) if consultas else "no hay palabras para buscar: escribelas en el cuadro"}


def _dominio(link):
    """El dominio del destino de un anuncio (texto de terceros: una direccion rota no tumba el pedido)."""
    try:
        return publicacion.urlparse(str(link or "")).hostname or ""
    except ValueError:
        return ""


def _del_anuncio(meta, buscar, pid, anuncio, ads, nombre, pausa, r, log):
    """El resultado de un pedido POR ANUNCIO: la publicacion de ESE anuncio y de ninguno mas. Sin su anuncio activo, «sin_anuncio» (sin gastar busquedas en Google);
    si su anuncio no manda a WhatsApp, «sin_whatsapp» con su destino; si Google no trae su publicacion, «sin_publicacion»."""
    propio = flujo.anuncio_propio(meta, pid, anuncio, ads, pausa)
    if propio is None:
        r["sinAnuncio"] += 1
        log(f"   Pedido {pid}/{anuncio} ({nombre[:28]}): su anuncio ya no esta activo en la Biblioteca; no se busca la publicacion de otra oferta de la pagina")
        return {"pagina": pid, "anuncio": anuncio, "estado": "sin_anuncio", "nombre": nombre, "mensaje": "su anuncio ya no esta activo en la Biblioteca"}
    nombre = propio.get("page_name") or nombre
    if not flujo.vende_por_whatsapp(propio):
        dominio = _dominio(propio.get("link"))
        r["sinWhatsapp"] += 1
        log(f"   Pedido {pid}/{anuncio} ({nombre[:28]}): su anuncio no manda a WhatsApp (va a {dominio or 'otro destino'})")
        return {"pagina": pid, "anuncio": anuncio, "estado": "sin_whatsapp", "nombre": nombre, "mensaje": dominio[:80]}
    textos = publicacion.angulos([propio], lambda a: True)          # SOLO el texto de su anuncio
    if not textos:                                                 # sin texto no hay con que comparar: buscar gastaria busquedas de pago para nada
        r["sinPublicacion"] += 1
        log(f"   Pedido {pid}/{anuncio} ({nombre[:28]}): su anuncio no trae texto para buscar su publicacion")
        return {"pagina": pid, "anuncio": anuncio, "estado": "sin_publicacion", "nombre": nombre, "mensaje": "su anuncio no trae texto para buscar su publicacion"}
    pub, vanity = publicacion.datos_de_pagina(next((a.get("page_uri") for a in [propio] + list(ads) if a.get("page_uri")), ""), pid)
    diag = {}
    hit = publicacion.hallar(buscar, pub, vanity, nombre, textos, diag=diag, ajenos=publicacion.textos_ajenos(ads, propio))   # ni una que se parezca mas a otra oferta
    if hit:
        r["halladas"] += 1
        log(f"   Pedido {pid}/{anuncio} ({nombre[:28]}): publicacion {hit['numero']} ({hit['tipo']}, parecido {hit['puntaje']})"
            + (" EMPATE PERDONADO (otra version del mismo anuncio): revisar a mano que sea la suya" if hit.get("perdonado") else ""))
        return {"pagina": pid, "anuncio": anuncio, "estado": "hallado", "publicacion": hit["numero"], "paginaPublica": pub or pid, "nombre": nombre}
    r["sinPublicacion"] += 1
    mensaje = (f"{diag.get('resultados', 0)} resultados de Google, {diag.get('con_numero', 0)} con numero, mejor parecido {diag.get('mejor', 0):.2f} (solo su anuncio"
               + (f"; {diag['de_otra_oferta']} se parecian mas a otra oferta" if diag.get("de_otra_oferta") else "") + ")")
    log(f"   Pedido {pid}/{anuncio} ({nombre[:28]}): sin publicacion de su anuncio ({mensaje})")
    return {"pagina": pid, "anuncio": anuncio, "estado": "sin_publicacion", "nombre": nombre, "mensaje": mensaje}


def atender(meta, buscar, clave, limitado, red, pausa=None, log=print, maximo=MAXIMO, session=None, url=None, traduccion=None):
    """Atiende los pedidos pendientes. Devuelve (codigo, resumen): 0 bien, 1 algo fallo, 3 Meta limito la IP."""
    r = {"pendientes": 0, "atendidas": 0, "halladas": 0, "sinPublicacion": 0, "sinAnuncio": 0, "sinPagina": 0, "sinWhatsapp": 0, "candidatas": 0, "errores": 0, "limitado": False}
    pausa = pausa or (lambda: None)
    pendientes = traer_pedidos(clave, session, url)
    r["pendientes"] = len(pendientes)
    for pedido in pendientes[:max(0, maximo)]:
        pid, es, anuncio = pedido["pagina"], pedido["modo"] == "es", pedido.get("anuncio") or ""
        cuerpo = None
        try:
            pausa()
            res = flujo.anuncios_de_pagina(meta, pid, pausa)      # en Brasil y, si alli no tiene anuncios, en todos los paises (las de oro en espanol)
            ads = (res or {}).get("ads") or []
            nombre = next((a.get("page_name") for a in ads if a.get("page_name")), "")
            if es:
                cuerpo = _en_espanol(meta, pid, pedido["palabras"], ads, nombre, pausa, traduccion)
                r["candidatas"] += 1
                log(f"   Pedido {pid} ({nombre[:28]}) en espanol: {len(cuerpo['candidatas'])} paginas candidatas ({cuerpo['mensaje'][:80]})")
            elif anuncio:
                cuerpo = _del_anuncio(meta, buscar, pid, anuncio, ads, nombre, pausa, r, log)
            elif not ads:
                cuerpo = {"pagina": pid, "estado": "sin_pagina", "mensaje": "La Biblioteca no devolvio anuncios activos de esa pagina"}
                r["sinPagina"] += 1
            elif not any(flujo.vende_por_whatsapp(a) for a in ads):
                dominio = next((_dominio(a.get("link")) for a in ads if a.get("link")), "") or ""
                cuerpo = {"pagina": pid, "estado": "sin_whatsapp", "nombre": nombre, "mensaje": dominio[:80]}
                r["sinWhatsapp"] += 1
                log(f"   Pedido {pid} ({nombre[:28]}): sus {len(ads)} anuncios no mandan a WhatsApp (van a {dominio or 'otro destino'})")
            else:
                pub, vanity = publicacion.datos_de_pagina(next((a.get("page_uri") for a in ads if a.get("page_uri")), ""), pid)
                diag = {}
                hit = publicacion.hallar(buscar, pub, vanity, nombre, publicacion.angulos(ads, flujo.vende_por_whatsapp), diag=diag)
                if hit:
                    cuerpo = {"pagina": pid, "estado": "hallado", "publicacion": hit["numero"], "paginaPublica": pub or pid, "nombre": nombre}
                    r["halladas"] += 1
                    log(f"   Pedido {pid} ({nombre[:28]}): publicacion {hit['numero']} ({hit['tipo']}, parecido {hit['puntaje']})")
                else:
                    cuerpo = {"pagina": pid, "estado": "sin_publicacion", "nombre": nombre,
                              "mensaje": f"{diag.get('resultados', 0)} resultados de Google, {diag.get('con_numero', 0)} con numero, mejor parecido {diag.get('mejor', 0):.2f}"}
                    r["sinPublicacion"] += 1
                    log(f"   Pedido {pid} ({nombre[:28]}): sin publicacion ({cuerpo['mensaje']})")
        except limitado as e:
            r["limitado"] = True
            log(f"   Meta limito la IP ({str(e)[:70]}): el pedido {pid} sigue pendiente.")
            break
        except (publicacion.SinCredito, publicacion.Limitada) as e:
            cuerpo = {"pagina": pid, "estado": "error", "mensaje": "La busqueda en Google no esta disponible ahora (sin credito o limitada). Avisale al dueño."}
            if es:
                cuerpo["modo"] = "es"
            elif anuncio:
                cuerpo["anuncio"] = anuncio
            r["errores"] += 1
            log(f"!! La busqueda en Google se corto: {str(e)[:120]}")
            try:
                enviar(clave, cuerpo, session, url)
                r["atendidas"] += 1
            except PedidosError as e2:
                log(f"!! {e2}")
            break
        except (publicacion.BusquedaError, flujo.SinDatos) + tuple(red) as e:
            cuerpo = {"pagina": pid, "estado": "error", "mensaje": "No se pudo buscar ahora (" + type(e).__name__ + ")"}
            r["errores"] += 1
            log(f"   Pedido {pid}: {type(e).__name__}: {str(e)[:90]}")
        except Exception as e:             # algo raro en los datos de terceros (HTML de Meta, enlaces): ese pedido contesta «error» y los demas siguen
            cuerpo = {"pagina": pid, "estado": "error", "mensaje": "No se pudo buscar ahora (" + type(e).__name__ + ")"}
            r["errores"] += 1
            log(f"!! Pedido {pid}: fallo inesperado {type(e).__name__}: {str(e)[:90]}")
        if cuerpo is not None:
            if es:
                cuerpo["modo"] = "es"
            elif anuncio:
                cuerpo["anuncio"] = anuncio          # el dash guarda el resultado por pagina + anuncio: sin esto no llega a la ficha de esa oferta
            try:
                enviar(clave, cuerpo, session, url)
                r["atendidas"] += 1
            except PedidosError as e:
                r["errores"] += 1
                log(f"!! {e}")
                break
    return (3 if r["limitado"] else 1 if r["errores"] else 0), r
