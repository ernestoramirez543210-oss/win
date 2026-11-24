"""
buscar_es.py - «La misma oferta en español con WhatsApp»: dada una pagina de Brasil cuyos anuncios llevan a una landing (o que no se halla), busca en la Biblioteca de anuncios, en TODOS los paises,
anuncios en espanol del mismo tema que SI manden a WhatsApp, y devuelve las paginas que los publican (candidatas). El equipo abre cada una o le pide «Buscar su WhatsApp».

Las palabras salen de lo que el equipo escribio en el cuadro o, si no escribio nada, de las primeras palabras del anuncio mas repetido de la pagina (con el glosario pt->es de radar.py).
Solo lee la Biblioteca (2 peticiones como mucho por pedido); no escribe en la planilla.
"""
import re

import sheets

MAX_CANDIDATAS = 6
PAIS = "ALL"
# palabras que delatan el idioma de un texto (ya sin tildes ni mayusculas)
SOLO_ES = {"el", "los", "las", "una", "con", "tu", "tus", "muy", "del", "usted", "ustedes", "tambien", "su", "sus", "te", "para", "ahora", "como", "hacer", "puedes", "tienes", "recibes", "aprende", "descarga", "incluye"}
SOLO_PT = {"voce", "voces", "nao", "uma", "um", "seu", "sua", "seus", "suas", "muito", "mais", "pra", "os", "as", "em", "ficou", "sao", "isso", "esse", "essa", "tem", "ter", "fazer", "aprenda", "baixe", "agora", "pode", "podem"}


def es_espanol(texto):
    """True si el texto parece espanol y no portugues (cuenta palabras de uno y otro idioma; «ao»/«cao» y la ñ desempatan)."""
    t = str(texto or "").lower()
    if re.search(r"[ãõ]|ç[ãa]o", t):
        return False
    pal = re.findall(r"[a-zñ]+", sheets.fold(t).replace("n~", "ñ"))
    es = sum(1 for p in pal if p in SOLO_ES) + (3 if "ñ" in t else 0)
    pt = sum(1 for p in pal if p in SOLO_PT)
    return es > pt


def limpiar_palabras(texto, tope=60):
    """Lo que escribio el equipo: solo letras, numeros y espacios (nada de operadores de busqueda), como mucho 6 palabras."""
    pal = re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", str(texto or ""))[:6]
    return " ".join(pal)[:tope]


def palabras_auto(textos, query_words, gloss, fold=sheets.fold):
    """[consulta] a partir del anuncio mas repetido: sus primeras 4 palabras con contenido, traducidas con el glosario donde se puede.
    Se prueban dos consultas: las 3 primeras y las dos siguientes a la primera (la primera suele ser un verbo en portugues: «ensinar»)."""
    for t in textos or []:
        pal = [gloss.get(fold(w), w) for w in query_words(str(t), 4)]
        if len(pal) >= 2:
            qs = [" ".join(pal[:3])]
            if len(pal) >= 3:
                qs.append(" ".join(pal[1:3]))
            return qs
    return []


def candidatas(meta, consultas, vende_por_whatsapp, pagina_origen, pausa=None, maximo=MAX_CANDIDATAS):
    """[{pagina, nombre, anuncios, texto}] de las paginas con anuncios en espanol que mandan a WhatsApp, de mas a menos anuncios. `meta.first_page(q, pais)` = (anuncios, cursor, hay_mas) o None."""
    por_pagina = {}
    raices = {sheets.fold(w)[:5] for q in consultas for w in q.split() if len(w) >= 5}   # el anuncio tiene que nombrar el tema (la Biblioteca a veces devuelve anuncios sin relacion)
    for q in consultas:
        if pausa:
            pausa()
        res = meta.first_page(q, PAIS)
        for a in (res[0] if res else []) or []:
            pid = str(a.get("page_id") or "")
            texto = str(a.get("text") or a.get("body") or "").strip()
            if not pid.isdigit() or pid == str(pagina_origen) or not a.get("active", True):
                continue
            if not vende_por_whatsapp(a) or not es_espanol(texto):
                continue
            if raices and not any(r in sheets.fold(texto) for r in raices):
                continue
            c = por_pagina.setdefault(pid, {"pagina": pid, "nombre": str(a.get("page_name") or "")[:80], "anuncios": 0, "texto": texto[:140]})
            c["anuncios"] += max(1, int(a.get("collation_count") or 1))
        if len(por_pagina) >= maximo:
            break
    return sorted(por_pagina.values(), key=lambda c: -c["anuncios"])[:maximo]
