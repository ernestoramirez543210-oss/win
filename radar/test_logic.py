"""Prueba offline de la logica (filtros, agregacion, rubrica) con datos SINTETICOS.
No toca la red ni valida el endpoint de Meta: solo que las reglas hagan lo que dice la planilla."""
import contextlib
import io
import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import radar

NOW = time.time()
DAY = 86400


def mk(page, name, n, days_old, link, body="Guia completa em PDF por R$ 27", dup=False, video=False):
    ads = []
    for i in range(n):
        raw = {
            "ad_archive_id": f"{page}{i}", "collation_id": f"c{page}" if dup else f"c{page}-{i}",
            "collation_count": 1, "page_id": page, "page_name": name,
            "start_date": NOW - (days_old - i % 3) * DAY, "is_active": True,
            "snapshot": {"body": {"text": body + (f" variante {i}" if not dup else "")},
                         "link_url": link, "display_format": "VIDEO" if video and i % 2 == 0 else "IMAGE"},
        }
        ads.append(radar.normalize(raw))
    return ads


ads = (mk("1", "Ganadora PDF", 12, 70, "https://pay.hotmart.com/x", video=True)
       + mk("2", "Muy nueva", 12, 10, "https://a.com/")
       + mk("3", "Pocos anuncios", 5, 90, "https://b.com/")
       + mk("4", "Todos duplicados", 12, 90, "https://c.com/", dup=True)
       + mk("5", "Ya en planilla", 12, 90, "https://d.com/")
       + mk("6", "Concurso", 12, 90, "https://e.com/", body="Preparatório para concurso PM")
       + mk("7", "Riesgo Meta", 12, 90, "https://f.com/", body="Receitas para emagrecer rapido em PDF")
       + mk("8", "Kit desfralde", 12, 90, "https://g.com/", body="Kit desfralde em PDF por R$ 27")
       + mk("9", "Dominio repetido", 12, 90, "https://h.com/")
       + mk("10", "Nicho receitas", 12, 90, "https://i.com/", body="Receitas de bolo em PDF por R$ 27"))
g = radar.aggregate(ads, NOW)
seen = {"names": {"ya en planilla"}, "domains": {"h.com"}, "ad_ids": set()}

# A) busqueda generica (sin nicho): solo filtros duros
expected = {"1": True, "2": False, "3": False, "4": False, "5": True, "6": False,
            "7": False, "8": True, "9": False, "10": True}
for pid, want in expected.items():
    ok, why = radar.hard_filter(g[pid], seen, None)
    print(f"{g[pid]['name']:<18} ok={ok!s:<5} {why}")
    assert ok == want, (pid, ok, why)
assert g["5"]["repeat"] is True, "anunciante repetido se marca, no se descarta (regla 3)"
assert g["1"]["repeat"] is False

# B) busqueda de nicho: el caso 'kit desfralde' que se colo con 'receitas fit e-book'
stems = radar.niche_stems("receitas passo a passo em PDF")
assert stems == ["receita"], stems
assert radar.niche_stems("e-book em PDF") == [] and radar.niche_stems("apenas R$ 27") == []
ok, why = radar.hard_filter(g["8"], seen, stems)
assert not ok and "nicho" in why, why
ok, why = radar.hard_filter(g["10"], seen, stems)
assert ok, why
ok, why = radar.hard_filter(g["7"], seen, stems)
assert not ok and "Meta" in why, why
# mismo anuncio ya registrado -> fuera aunque el dominio no coincida
seen_ad = {"names": set(), "domains": set(), "ad_ids": {"100"}}
assert not radar.hard_filter(g["10"], seen_ad, stems)[0]
# minimo de anuncios activos configurable
assert radar.hard_filter(g["3"], seen, None, min_active=5)[0] is False   # 5 activos pero <6 piezas distintas
assert radar.hard_filter(g["1"], seen, None, min_active=20)[1].startswith("12 anuncios activos")

assert g["1"]["distinct"] == 12 and g["4"]["distinct"] == 1, "conteo de piezas distintas"
assert g["1"]["video_pieces"] == 6, g["1"]["video_pieces"]
assert 69 <= g["1"]["days"] <= 70, g["1"]["days"]
assert g["1"]["ads_30d"] == 12 and g["1"]["new_14d"] == 0 and g["2"]["new_14d"] == 12
assert radar.winner_rank(g["1"]) > radar.winner_rank(g["2"])

# Colombia
assert radar.colombia_points([]) == (3, "0 anunciantes")
assert radar.colombia_points([{"days": 10}, {"days": 20}])[0] == 2
assert radar.colombia_points([{"days": 45}])[0] == 1
assert radar.colombia_points([{"days": 5}] * 3)[0] == 0
es = radar.auto_es("receitas passo a passo em PDF")
assert es and es.startswith("recetas paso a paso en"), es
assert radar.auto_es("apenas R$ 27") is None and radar.auto_es("palavra inventada xyzw") is None

# Landing y rubrica: ejemplo del prompt (3+2+2+2+1 = 10)
big = dict(g["1"], active=45, days=74)
land = radar.analyze_landing(big, "<html>hotmart checkout R$ 47,00 ebook pdf</html>")
assert land["price_min"] == 27.0 and land["funnel"].startswith("Pagina de producto + Hotmart"), land
assert land["store"] is False
total, why = radar.score(big, 3, dict(land, video_risk=False), True)
print("rubrica:", why)
assert total == 10 and why == "3+2+2+2+1=10"
tienda = radar.analyze_landing(g["1"], "<script src='cdn.shopify.com'></script>")
assert tienda["store"] is True
# entregable: PDF claro 2, con video 1, solo palabras debiles sin embudo 0, debiles con WhatsApp/checkout 1
assert radar.entregable_points({"pdf": True, "video_risk": False, "pdf_weak": False, "checkout": [], "whatsapp": False}) == 2
assert radar.entregable_points({"pdf": True, "video_risk": True, "pdf_weak": False, "checkout": [], "whatsapp": False}) == 1
assert radar.entregable_points({"pdf": False, "video_risk": False, "pdf_weak": True, "checkout": [], "whatsapp": False}) == 0
assert radar.entregable_points({"pdf": False, "video_risk": False, "pdf_weak": True, "checkout": [], "whatsapp": True}) == 1
assert radar.analyze_landing(g["8"], "")["pdf"] is True        # 'em PDF' en el anuncio
desfralde = dict(g["8"], ads=[dict(a, body="Kit desfralde sem fralda", text="Kit desfralde sem fralda") for a in g["8"]["ads"]])
assert radar.analyze_landing(desfralde, "")["pdf"] is False     # 'kit' solo ya no cuenta como PDF
assert radar.parse_price("R$ 1,00 e R$ 19,90") == [19.9]

# Estado: descartes caducan, candidatos no; lo que no tiene fecha (version vieja) se reevalua
st = {"seen_pages": {
    "a": {"decision": "x", "date": datetime.now().isoformat()},
    "b": {"decision": "x", "date": (datetime.now() - timedelta(days=20)).isoformat()},
    "c": {"decision": "candidato 7"}, "d": {"decision": "x"}}}
assert not radar.needs_review(st, "a") and radar.needs_review(st, "b")
assert not radar.needs_review(st, "c") and radar.needs_review(st, "d") and radar.needs_review(st, "zzz")
# un candidato que NO se escribio en la planilla se reevalua cuando caduca (con otras reglas podria registrarse)
st["seen_pages"]["e"] = {"decision": "candidato no escrito 7", "date": datetime.now().isoformat()}
st["seen_pages"]["f"] = {"decision": "candidato no escrito 7", "date": (datetime.now() - timedelta(days=20)).isoformat()}
assert not radar.needs_review(st, "e") and radar.needs_review(st, "f")

# Palabras: lee la pestana PALABRAS-CLAVE tal cual y aplica el orden del prompt
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "palabras.csv"
    p.write_text(
        "Palabra clave,Idioma,Mercado,Nicho,Origen,Búsquedas,Ofertas registradas,Última búsqueda,Estado\n"
        "a gen,pt-BR,Brasil,general,semilla,0,0,,activa\n"
        "b croche,pt-BR,Brasil,croche,aprendida,2,2,08/10/2026,activa\n"
        "c croche 2,pt-BR,Brasil,croche,aprendida,1,1,01/10/2026,activa\n"
        "d muerta,pt-BR,Brasil,otro,semilla,3,0,,activa\n"
        "e retirada,pt-BR,Brasil,otro,semilla,0,0,,retirada\n"
        "f colombia,es-CO,Colombia,otro,semilla,0,0,,activa\n", encoding="utf-8")
    kws = radar.read_keywords(p)
    assert [k["pt"] for k in kws] == ["a gen", "b croche", "c croche 2", "d muerta"], kws
    chosen = radar.pick_keywords(kws, {"keywords": {}}, 3)
    # d muerta (3 busquedas, 0 ofertas) se retira; b croche (2 ofertas) primero; 1 por nicho antes que repetir
    assert [k["pt"] for k in chosen] == ["b croche", "a gen", "c croche 2"], [k["pt"] for k in chosen]

# Cursos/posgrados no son PDF: "download" suelto en el HTML no prueba un PDF, y la formacion cuenta como curso
curso = dict(g["8"], ads=[dict(a, body="Formação em Terapia do Esquema, pós-graduação",
                               text="Formação em Terapia do Esquema, pós-graduação") for a in g["8"]["ads"]])
lc = radar.analyze_landing(curso, "<html>Baixe o app. Download. Política de cookies</html>")
assert lc["pdf"] is False and lc["video_risk"] is True, lc
assert radar.analyze_landing(curso, "<html>receba seu e-book em pdf</html>")["pdf"] is True
# Un curso NO descarta (se puede reconvertir a PDF): puntua 1 en entregable y se marca para confirmar.
assert lc["course"] is True and lc["app"] is False and radar.entregable_points(lc) == 1, lc
app = radar.analyze_landing(dict(g["8"], ads=[dict(a, body="Entre no aplicativo da comunidade", text="Entre no aplicativo da comunidade")
                                              for a in g["8"]["ads"]]), "")
assert app["app"] is True and app["course"] is False and radar.entregable_points(app) == 0, app

# LANZAMIENTO / aulas en vivo (regla del dueno, 2026-10-14, caso "Vivendo de Bordado": aulas en vivo con un ebook de regalo): no es una oferta
# de bajo ticket que se rehaga como PDF, aunque el anuncio o la pagina mencionen un ebook. Un curso GRABADO sigue sin descartar.
def con_texto(t, html=""):
    return radar.analyze_landing(dict(g["8"], ads=[dict(a, body=t, text=t) for a in g["8"]["ads"]]), html)
assert radar.analyze_landing(g["8"], "")["launch"] == "", "el anuncio de prueba normal no es un lanzamiento"
for t in ["Aulas ao vivo de bordado natalino com ebook de brinde", "AULAS ONLINE E AO VIVO toda semana", "Participe da imersão ao vivo", "Imersão online gratuita de crochê",
          "Aula gratuita: inscreva-se no workshop", "Webinar gratuito de crochê", "Webinário: como vender bordado", "Masterclass de bordado",
          "Lançamento do curso de bordado: turma aberta", "Lançamento da mentoria", "Pré-lançamento exclusivo", "Semana de lançamento", "Evento online gratuito",
          "Encontros ao vivo toda semana", "Mentoria ao vivo em grupo", "Participe da live gratuita", "Aula inaugural dia 20/10", "Curso ao vivo com a professora",
          "Toda terça-feira, às 15h, a Bella borda junto com você", "Todos os sábados às 10h tem encontro", "Todas as quintas a partir das 20:30 com a professora"]:
    assert con_texto(t)["launch"], ("deberia ser lanzamiento: " + t)
# El anuncio REAL del caso (Biblioteca de anuncios, id 9999000000450849, leido el 2026-10-14): una clase de bordado en vivo, gratuita, cada martes
real = ("Toda terça-feira, às 15h, a Bella liga a câmera em cima do bastidor e borda junto com você. Ao vivo e de graça.\n\nEsse é o Terça com Bella: uma aula de bordado ao vivo por semana, "
        "com um projeto novo a cada encontro. Se travar, você pergunta ali e ela responde ao vivo.\n\nTerça, 15h. Gratuito. O aviso da aula vai só pra quem está inscrito.\n\n"
        "Clique em \"Saiba mais\" e se inscreva para participar das aulas.")
assert con_texto(real)["launch"], "el anuncio real de Vivendo de Bordado tiene que contar como lanzamiento"
assert con_texto(real.split("\n\n")[0])["launch"], "y la primera frase sola (el horario fijo) tambien"
for t in ["Receitas de crochê em PDF com 120 vestidos", "Curso completo de bordado com videoaulas gravadas", "Lançamento do ebook de receitas", "Aulas gravadas e material em PDF",
          "Ebook: guia passo a passo com 30 moldes", "Baixe agora o planner", "Assista o vídeo e veja como fazer", "Receba por WhatsApp o material digital",
          "Nova coleção de riscos para bordar", "Formação em Terapia do Esquema, pós-graduação", "Toda segunda você recebe uma receita nova no WhatsApp",
          "O material chega às 10h no seu e-mail", "Receba todos os dias às vezes um bônus"]:
    assert not con_texto(t)["launch"], ("NO deberia ser lanzamiento: " + t)
assert con_texto("Aulas ao vivo de bordado")["launch"] == "Aulas ao vivo", con_texto("Aulas ao vivo de bordado")["launch"]     # dice lo que coincidio
assert con_texto("x" * 400 + " aulas   ao   vivo")["launch"] == "aulas ao vivo", "espacios normalizados"
# la pagina de destino cuenta por su titulo y su descripcion (el html llega en minusculas), no por cualquier parrafo o menu
assert radar.analyze_landing(g["8"], "<html><title>aula inaugural ao vivo | bordado</title></html>")["launch"] == "aula inaugural"
assert radar.analyze_landing(g["8"], "<html><meta name=\"description\" content=\"participe do webinar\"></html>")["launch"] == "webinar"
assert not radar.analyze_landing(g["8"], "<html><title>ebook de receitas</title><body><nav>lives · aulas ao vivo</nav></body></html>")["launch"], "el menu de la pagina no es la oferta"
# el curso grabado sigue contando como curso (se puede reconvertir) y no como lanzamiento
cg = con_texto("Curso completo com videoaulas gravadas e certificado de conclusão")
assert cg["course"] is True and not cg["launch"], cg

# Reparto adaptativo entre modos de busqueda
assert radar.allocate_modes({}, 3) == {"palabra": 1, "combo": 1, "angulo": 1}
rend = {"modos": {"palabra": {"busquedas": 10}, "combo": {"busquedas": 4, "candidatos": 3, "escritas": 2},
                  "angulo": {"busquedas": 10}}}
c6 = radar.allocate_modes(rend, 6)
assert sum(c6.values()) == 6 and c6["combo"] == 4 and min(c6.values()) >= 1, c6        # el que rinde, mas; piso de 1
c2 = radar.allocate_modes({"modos": {"palabra": {"ultima": 5}, "combo": {"ultima": 2}, "angulo": {"ultima": 4}}}, 2)
assert c2 == {"palabra": 0, "combo": 1, "angulo": 1}, c2                                # con 2: rota al menos usado
assert radar.mode_score({}) == 1 and radar.mode_score({"busquedas": 3, "escritas": 1}) == 3 / 4
kw2 = [{"pt": "risco para bordar PDF", "es": "", "nicho": "manualidades", "searches": 0, "offers": 0, "last": "", "row": 2},
       {"pt": "receitas de bolo", "es": "", "nicho": "recetas", "searches": 0, "offers": 0, "last": "", "row": 3}]
qa = radar.build_queries("auto", kw2, radar.read_angles(radar.HERE / "angulos.csv"), {"keywords": {}, "runs": [{}]}, 3)
assert [i["mode"] for i in qa] == ["combo", "angulo", "palabra"], [i["mode"] for i in qa]   # quien abre rota por corrida

# El mercado ensena: promesas que se repiten entre anunciantes pasan a angulos.csv
prom = radar.extract_promises({"pieces": [
    {"text": "Receba agora seu kit. Modelos prontos para imprimir, sem experiencia! Renda extra em casa"},
    {"text": "Aprenda renda extra com modelos prontos"}]})
assert {"renda extra", "modelos prontos", "sem experiencia"} <= set(prom), prom
assert all(" " in p and not any(ch.isdigit() for ch in p) for p in prom)
vistas = {"renda extra": {"1", "2"}, "modelos prontos": {"1"}, "passo a passo": {"1", "2", "3"}}
tip = {"renda extra": "ingreso extra", "modelos prontos": "plantillas listas", "passo a passo": "metodo paso a paso"}
nuevos = radar.learn_angles(vistas, tip, [{"angulo": "renda extra em casa", "tipo": "x", "origen": "semilla"}], "11/10/2026")
assert [a["angulo"] for a in nuevos] == ["passo a passo"], nuevos      # 'renda extra' ya cubierta; 'modelos prontos' lo usa 1 solo
with tempfile.TemporaryDirectory() as tmp:
    pa = Path(tmp) / "ang.csv"
    radar.save_angles(pa, [{"angulo": "a b", "tipo": "t"}] + nuevos)
    back = radar.read_angles(pa)
    assert [x["angulo"] for x in back] == ["a b", "passo a passo"] and back[1]["origen"].startswith("aprendido"), back
sug = radar.suggest_keyword({"pieces": [{"text": "bordado riscos para bordado"}] * 3}, set())
assert sug and {"bordado", "riscos"} <= set(sug.split()) and sug.endswith("PDF"), sug
assert radar.suggest_keyword({"pieces": [{"text": "bordado riscos para bordado"}] * 3}, {radar.fold(sug)}) is None

# REGRESION: "ebook" dentro de "FACEBOOK"/"chromebooks" no es un ebook (asi pasaron Qustodio y MamyPoko como PDF)
for texto in ("https://www.facebook.com/mamypokobr/?locale=pt_br", "para desktops, chromebooks e muito mais",
              "<a href='https://facebook.com/tr?id=1'>", "Termos de uso (PDF) en la politica de cookies"):
    assert not radar.PDF_STRONG_HTML.search(texto), texto
    assert not radar.PDF_STRONG.search(radar.fold(texto).replace("(pdf)", "")), texto
assert radar.PDF_STRONG_HTML.search("receba seu ebook agora") and radar.PDF_STRONG_HTML.search("o e-book completo")
assert radar.PDF_STRONG.search("Receba o PDF no WhatsApp") and radar.PDF_STRONG.search("moldes para imprimir")
face = radar.analyze_landing(g["8"], "<html><a href='https://www.facebook.com/x'>Siga</a> chromebooks</html>")
assert face["pdf"] is True        # el copy del anuncio 'Kit desfralde em PDF' si lo dice
sinpdf = dict(g["8"], ads=[dict(a, body="Controle parental para seus filhos", text="Controle parental para seus filhos")
                           for a in g["8"]["ads"]])
fb = radar.analyze_landing(sinpdf, "<html><a href='https://www.facebook.com/x'>Siga</a> chromebooks</html>")
assert fb["pdf"] is False and radar.entregable_points(fb) == 0, fb      # sin senal real de producto digital
assert radar.BR_ONLY.search("Pontos para a residência médica") and radar.BR_ONLY.search("residencia medica")
assert radar.page_summary("<title>Saúde &amp; Pesquisa</title><meta name=\"description\" content=\"coautoria em artigos\">") == \
    "Saúde &amp; Pesquisa | coautoria em artigos"

# Marcas grandes (O Boticario: 15 M de seguidores) no son embudos de bajo ticket
grande = dict(g["1"], page_like=15_321_330)
ok, why = radar.hard_filter(grande, seen, None)
assert not ok and "marca grande" in why and "15.321.330" in why, why
assert radar.hard_filter(dict(g["1"], page_like=39_413), seen, None)[0]            # los candidatos buenos tenian 0.7-39 mil
assert radar.hard_filter(grande, seen, None, max_likes=20_000_000)[0]              # el tope se puede subir
assert radar.normalize({"snapshot": {"page_like_count": 726}, "page_id": "1"})["page_like"] == 726

# Opciones de Status: 'no testeada' existe y es NEUTRA para el aprendizaje
assert "no testeada" in radar.STATUS_VALUES
assert radar.status_adjust([{"Palabra clave": "kw", "Status": "no testeada"}]) == {}
assert radar.niche_adjust([{"Nicho": "recetas", "Status": "no testeada"}]) == {}

# 'testiando' (se esta probando; el dash la manda ademas al tablero de Produccion) tambien es NEUTRA: todavia no hay resultado, asi que no sube ni baja nada
assert "testiando" in radar.STATUS_VALUES and len(set(radar.STATUS_VALUES)) == len(radar.STATUS_VALUES)
assert radar.status_adjust([{"Palabra clave": "kw", "Status": "testiando"}, {"Palabra clave": "kw", "Status": " Testiando "}]) == {}
assert radar.niche_adjust([{"Nicho": "recetas", "Status": "testiando"}]) == {}
assert radar.status_adjust([{"Palabra clave": "kw", "Status": "testiando"}, {"Palabra clave": "kw", "Status": "ganadora"}]) == {"kw": 3}   # lo demas sigue contando igual

# puntos_ciegos.py: una oferta que se esta probando no es una oferta 'nuevo' sin decision ni una fila del radar sin confirmar
import puntos_ciegos
from datetime import datetime as _dt
_ahora = _dt(2026, 10, 16)
_fila = lambda status: {"Fecha": "01/10/2026", "Anunciante": "Moleque", "Status": status, "Nota": "8", "Justificación": "[radar.py] 3+2+1+1+1=8.",
                        "Anuncios activos": "20", "Días en el aire": "60", "Dominio": "no visible", "Nicho": "manualidades"}
_ganadora = dict(_fila("ganadora"), Anunciante="Otra")
_estado = {"runs": [{"date": "2026-10-15", "candidatos": 3}]}
_titulos = lambda filas: [t for _, t, _ in puntos_ciegos.check_all(filas, [], [], _estado, _ahora)]
assert "Ofertas 'nuevo' sin decision" not in _titulos([_fila("testiando"), _ganadora]) and "Ofertas del radar sin confirmar" not in _titulos([_fila("testiando"), _ganadora]), _titulos([_fila("testiando"), _ganadora])
assert "Ofertas 'nuevo' sin decision" in _titulos([_fila("nuevo"), _ganadora]) and "Ofertas del radar sin confirmar" in _titulos([_fila("nuevo"), _ganadora]), "la misma fila en 'nuevo' si se avisa"

# Busqueda por angulo/promesa
bordado = {"pieces": [
    {"text": "Mais de 100 riscos prontos para imprimir. Receba no WhatsApp! Bordado para iniciantes, renda extra em casa"},
    {"text": "Aprenda bordado do zero, passo a passo. Garantia de 7 dias"},
    {"text": "Riscos de bordado: modelos prontos para imprimir por R$ 9,90"},
    {"text": "Oferta so hoje, bordado facil"}]}
tags = radar.angle_tags(bordado)
assert "plantillas listas" in tags and len(tags) <= 3, tags
terms = radar.product_terms(bordado, exclude=radar.niche_stems("renda extra em casa"))
assert "bordado" in terms and "casa" not in terms and len(terms) <= 2, terms
assert " ".join(radar.GLOSS[t] for t in terms).startswith("bordado")
assert radar.product_terms({"pieces": [{"text": "uma coisa qualquer"}]}) == []    # sin vocabulario conocido -> no se infiere
angles = radar.read_angles(radar.HERE / "angulos.csv")
assert len(angles) >= 10 and all(a["angulo"] and a["tipo"] for a in angles)
pick = radar.pick_angles(angles, {"keywords": {}}, 4)
assert len({a["tipo"] for a in pick}) == 4, pick                                  # un tipo de promesa por busqueda
pick2 = radar.pick_angles(angles, {"keywords": {"ángulo: " + angles[0]["angulo"]: "2026-10-11T10:00:00"}}, 3)
assert angles[0]["angulo"] not in [a["angulo"] for a in pick2], "el recien buscado rota al final"
# Un angulo con 3+ busquedas y 0 candidatos se retira solo; con 1 candidato sigue vivo
lab0, lab1 = "ángulo: " + angles[0]["angulo"], "ángulo: " + angles[1]["angulo"]
st_ret = {"keywords": {}, "etiquetas": {lab0: {"busquedas": 3, "candidatos": 0}, lab1: {"busquedas": 5, "candidatos": 1}}}
elegidos = [a["angulo"] for a in radar.pick_angles(angles, st_ret, len(angles))]
assert angles[0]["angulo"] not in elegidos and angles[1]["angulo"] in elegidos, elegidos
todos_ret = {"keywords": {}, "etiquetas": {"ángulo: " + a["angulo"]: {"busquedas": 9, "candidatos": 0} for a in angles}}
assert len(radar.pick_angles(angles, todos_ret, 2)) == 2          # si todos estan retirados, igual se usan
kw_all = [{"pt": "risco para bordar PDF", "es": "dibujo para bordar pdf", "nicho": "manualidades", "searches": 0, "offers": 0, "last": "", "row": 2},
          {"pt": "receitas de bolo", "es": "", "nicho": "recetas", "searches": 0, "offers": 0, "last": "", "row": 3}]
st0 = {"keywords": {}}
# mixto reparte entre palabra, combo y angulo, y los intercala (cada modo tiene su turno)
q = radar.build_queries("mixto", kw_all, angles, st0, 3)
assert [i["mode"] for i in q] == ["palabra", "combo", "angulo"], [i["mode"] for i in q]
assert len({i["label"] for i in q}) == 3 and q[0]["kwtext"] != q[1]["kwtext"], "combo no repite la palabra de 'palabra'"
assert len({a for a in [q[1]["angulo"], q[2]["query"]]}) == 2, "combo no repite el angulo de 'angulo'"
combo = q[1]
assert combo["label"].startswith("combo: ") and combo["kwtext"] in combo["label"], combo
assert combo["angulo"] in combo["label"] and len(combo["query"].split()) <= 5, combo["query"]     # consulta corta
assert radar.query_words("risco para bordar PDF", 2) == ["risco", "bordar"], radar.query_words("risco para bordar PDF", 2)
q = radar.build_queries("angulo", kw_all, angles, st0, 2)
assert [i["mode"] for i in q] == ["angulo", "angulo"] and q[0]["label"].startswith("ángulo: ")
q = radar.build_queries("palabra", kw_all, angles, st0, 2)
assert [i["mode"] for i in q] == ["palabra", "palabra"]
q = radar.build_queries("combo", kw_all, angles, st0, 2)
assert [i["mode"] for i in q] == ["combo", "combo"] and q[0]["kwtext"] != q[1]["kwtext"], q
# El combo solo parte de palabras con nicho propio (no de "receba no WhatsApp PDF", que es generica)
kw_gen = [{"pt": "receba agora no WhatsApp PDF", "es": "", "nicho": "general", "searches": 0, "offers": 9, "last": "", "row": 2}] + kw_all
qc = radar.build_queries("combo", kw_gen, angles, st0, 2)
assert qc and all(i["kwtext"] != "receba agora no WhatsApp PDF" for i in qc), [i["label"] for i in qc]
# NO_MODELABLE y enlaces cortos de Facebook (el falso PDF de 'Hoje Seguro')
assert radar.NO_MODELABLE.search(radar.fold("Seguro cobrindo 100% Fipe. Seguro auto sem analise"))
assert not radar.NO_MODELABLE.search(radar.fold("Pagamento seguro. Receitas em PDF"))
seg = [mk("20", "Seguradora", 12, 90, "http://fb.me/", body="Seguro auto cobrindo 100% da Fipe. Aceitamos todos os modelos")]
gs = radar.aggregate(seg[0], NOW)["20"]
ok, why = radar.hard_filter(gs, {"names": set(), "domains": set(), "ad_ids": set()}, None)
assert not ok and "no se modela" in why, why
assert radar.NO_MODELABLE.search(radar.fold("Ganhe 30% de comissão vendendo semijóias, seja revendedora"))
assert not radar.NO_MODELABLE.search(radar.fold("Moldes de bordado em PDF para imprimir"))
sinsenal = {"pdf": False, "pdf_weak": False, "course": False, "app": False, "video_risk": False, "checkout": [], "whatsapp": True}
assert radar.entregable_points(sinsenal) == 0            # WhatsApp solo no prueba un producto digital: se descarta
assert "fb.me" in radar.SOCIAL_HOSTS and radar.Meta().inspect_landing("http://fb.me/x") == ""
assert radar.analyze_landing(dict(gs, top_host="fb.me"), "")["funnel"].startswith("Messenger")
# una ganadora hallada por un angulo hace que ese angulo se busque primero
lab = "ángulo: " + angles[3]["angulo"]
assert radar.pick_angles(angles, {"keywords": {}}, 1, radar.status_adjust([{"Palabra clave": lab, "Status": "ganadora"}]))[0]["angulo"] == angles[3]["angulo"]

# Parser con una respuesta graphql sintetica (formato esperado, NO capturado de Meta)
fake = "for (;;);" + json.dumps({"data": {"ad_library_main": {"search_results_connection": {
    "edges": [{"node": {"collated_results": [{
        "ad_archive_id": "9", "collation_id": "c9", "collation_count": 3, "page_id": "7",
        "page_name": "X", "start_date": NOW - 40 * DAY, "is_active": True,
        "snapshot": {"body": {"text": "oi"}, "link_url": "https://wa.me/55"}}]}}],
    "page_info": {"end_cursor": "abc", "has_next_page": True}}}}})
parsed, cur, nxt = radar.parse_response(fake)
assert parsed[0]["collation_count"] == 3 and cur == "abc" and nxt
assert radar.aggregate(parsed, NOW)["7"]["active"] == 3
# Una palabra buscada hace poco cede el turno: la de mas ofertas se repetia en CADA corrida y ya no traia nada nuevo
hace_1h, hace_3d = datetime.now().isoformat(timespec="seconds"), (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
kwc = [{"pt": "estrella", "es": "", "nicho": "n1", "searches": 5, "offers": 5, "last": "", "row": 2},
       {"pt": "discreta", "es": "", "nicho": "n2", "searches": 1, "offers": 1, "last": "", "row": 3}]
assert [k["pt"] for k in radar.pick_keywords(list(kwc), {"keywords": {"estrella": hace_1h, "discreta": hace_3d}}, 1)] == ["discreta"]
assert [k["pt"] for k in radar.pick_keywords(list(kwc), {"keywords": {"estrella": hace_3d, "discreta": hace_3d}}, 1)] == ["estrella"]
rep = radar.pick_keywords(list(kwc), {"keywords": {"estrella": hace_1h, "discreta": hace_1h}}, 2)
assert [k["pt"] for k in rep] == ["estrella", "discreta"]          # si todas son recientes, igual hay palabras
assert not radar.searched_recently("") and not radar.searched_recently("12/10/2026") and radar.searched_recently(hace_1h)

# Temporadas: ventana de fechas, prioridad (Halloween antes que Navidad) y menos dias exigidos
seas = radar.read_seasons(radar.HERE / "temporadas.csv")
assert seas and {r["temporada"] for r in seas} >= {"halloween", "natal", "ano novo"}, seas[:2]
d = lambda m, dd: datetime(2026, m, dd, 12)
abiertas = lambda m, dd: {r["temporada"] for r in seas if radar.season_open(r, d(m, dd))}
assert abiertas(10, 13) == {"halloween", "natal"}                              # 13/oct: las dos vigentes
assert abiertas(11, 10) == {"natal"}                                           # tras Halloween solo queda Navidad
assert abiertas(11, 11) == {"natal", "ano novo"}                               # 11/nov: entra el fin de ano (rituales, agendas 2027...)
assert abiertas(12, 28) == {"ano novo"} and not abiertas(1, 25)                # pasada la fecha se apagan solas
cruce = {"desde": "12-15", "hasta": "01-10"}
assert radar.season_open(cruce, d(12, 31)) and radar.season_open(cruce, d(1, 5)) and not radar.season_open(cruce, d(6, 1))
pick = radar.pick_seasonal(seas, {"keywords": {}}, 3, d(10, 13))
assert [r["temporada"] for r in pick] == ["halloween"] * 3, pick                # prioridad a Halloween
hoy_iso = datetime.now().isoformat(timespec="seconds")
todas_hoy = {"keywords": {"temporada: " + r["pt"]: hoy_iso for r in seas if r["temporada"] == "halloween"}}
pick2 = radar.pick_seasonal(seas, todas_hoy, 3, d(10, 13))
assert [r["temporada"] for r in pick2] == ["natal"] * 3, pick2                   # las de Halloween ya buscadas hoy ceden el turno
assert radar.pick_seasonal(seas, {"keywords": {}}, 0, d(10, 13)) == []
info20 = dict(gs, days=20, active=12, distinct=8)
vacio = {"names": set(), "domains": set(), "ad_ids": set()}
ok30, why30 = radar.hard_filter(info20, vacio, None, 8)
assert not ok30 and "dias (<30)" in why30, why30                                 # con 30 dias exigidos, 20 dias no pasa
_, why14 = radar.hard_filter(info20, vacio, None, 8, None, 14)
assert "dias (<" not in why14, why14                                             # con 14 exigidos ya no cae por antiguedad

# La pagina 1 viene dentro del HTML de la Biblioteca (sin graphql); graphql solo pagina y, si Meta lo limita,
# la corrida conserva la pagina 1 en vez de abortar. HTML y respuestas SINTETICOS (formato visto en el navegador).
conn_html = {"edges": [{"node": {"collated_results": [{
    "ad_archive_id": "11", "collation_id": "c11", "collation_count": 2, "page_id": "8", "page_name": "Y",
    "start_date": NOW - 50 * DAY, "is_active": True, "snapshot": {"body": {"text": "ola"}, "link_url": "https://wa.me/55"}}]}}],
    "page_info": {"end_cursor": "xyz", "has_next_page": True}}
html_fake = ('<script type="application/json">{"require":[["x",{"__bbox":{"result":{"data":{"ad_library_main":{'
             '"search_results_connection":' + json.dumps(conn_html) + '}}}}}]]}</script>')
found = radar.embedded_connection(html_fake)
assert found and radar.connection_ads(found)[1:] == ("xyz", True) and radar.connection_ads(found)[0][0]["archive_id"] == "11"
assert radar.embedded_connection("<html>sin resultados</html>") is None


class MetaFalso(radar.Meta):
    def __init__(self, html_ok=True):
        super().__init__()
        self.llamadas, self.html_ok = 0, html_ok

    def first_page(self, q, country, page_id=None):
        return radar.connection_ads(conn_html) if self.html_ok else None

    def fetch_page(self, *a, **k):
        self.llamadas += 1
        raise radar.RateLimited("1675004")


_polite, radar.polite = radar.polite, lambda *a, **k: None
# Por defecto (desde el 2026-11-03) graphql no se pide: Meta lo limito en todas las corridas. La pagina 1 sola, sin agotar.
md = MetaFalso()
got_ads, agotado = md.collect("receitas", "BR", pages=3)
assert len(got_ads) == 1 and not agotado and md.llamadas == 0, "sin paginar no se llama a graphql"
mf = MetaFalso()
mf.paginar = True
got_ads, agotado = mf.collect("receitas", "BR", pages=3)
assert len(got_ads) == 1 and not agotado and mf.llamadas == 1 and mf.gql_blocked_until > time.time()
mf.collect("otra", "BR", pages=3)                    # graphql ya limito: no insiste durante un rato
assert mf.llamadas == 1
try:                                                 # sin HTML util ni graphql no hay nada que conservar: sube el error
    MetaFalso(html_ok=False).collect("receitas", "BR", pages=3)
    raise SystemExit("debio lanzar RateLimited")
except radar.RateLimited:
    pass


# Un tiempo agotado o una conexion cortada (ReadTimeout de 45 s, 19/10) tambien conserva la pagina 1 al paginar; y si no hay pagina 1 el error SUBE como
# excepcion de red (requests.RequestException) para que run() salte el anunciante o la busqueda en vez de morir
class MetaTimeoutPaginando(MetaFalso):
    def fetch_page(self, *a, **k):
        self.llamadas += 1
        raise requests.exceptions.ReadTimeout("graphql: Read timed out. (read timeout=45)")


mt = MetaTimeoutPaginando()
mt.paginar = True
got_ads, agotado = mt.collect("receitas", "BR", pages=3)
assert len(got_ads) == 1 and not agotado and mt.llamadas == 1 and mt.gql_blocked_until > time.time(), "sigue con la pagina 1 y deja descansar a graphql"
mt.collect("otra", "BR", pages=3)
assert mt.llamadas == 1, "graphql se dejo descansar: no se insiste"


class MetaSinPaginaUno(MetaFalso):
    def first_page(self, *a, **k):
        raise requests.exceptions.ReadTimeout("Biblioteca: Read timed out. (read timeout=45)")


try:
    MetaSinPaginaUno().collect("receitas", "BR", pages=3)
    raise SystemExit("debio lanzar ReadTimeout")
except requests.RequestException:
    pass


# Rebanadas (2026-11-03): con `rebanar` = dias minimos, la pagina 1 se suma a otras «paginas 1» con fechas de inicio y formato, sin graphql;
# los repetidos se cuentan una vez y, si Meta limita una rebanada, se sigue con lo que hay y no se piden mas en la corrida
class MetaRebanadas(MetaFalso):
    def __init__(self, cae_en=None):
        super().__init__()
        self.filtros, self.cae_en = [], cae_en

    def first_page(self, q, country, page_id=None, extra=None):
        self.filtros.append(extra)
        if extra is None:
            return radar.connection_ads(conn_html)                    # pagina 1: anuncio "11", con mas paginas
        if self.cae_en is not None and len(self.filtros) - 1 == self.cae_en:
            raise radar.RateLimited("HTTP 429 al abrir la Biblioteca")
        n = len(self.filtros)
        return [{"archive_id": "11"}, {"archive_id": f"r{n}"}], None, True    # repite el "11" y trae uno nuevo


mr = MetaRebanadas()
hoy_r = radar.date.today()
got_ads, agotado = mr.collect("receitas", "BR", pages=3, rebanar=30)
assert mr.llamadas == 0 and not agotado, "las rebanadas no usan graphql"
assert len(mr.filtros) == 6 and len(got_ads) == 6 and [a["archive_id"] for a in got_ads].count("11") == 1, [a["archive_id"] for a in got_ads]
d = lambda n: (hoy_r - radar.timedelta(days=n)).isoformat()
assert mr.filtros[1] == {"start_date[min]": d(60), "start_date[max]": d(30)} and mr.filtros[2] == {"start_date[min]": d(120), "start_date[max]": d(60)}
assert mr.filtros[3] == {"start_date[min]": d(365), "start_date[max]": d(120)}
# desde el 2026-11-11 las dos rebanadas de formato son de video, en dos ventanas de fechas que no se pisan (antes: una de video y otra de imagen)
assert mr.filtros[4] == {"media_type": "video", "start_date[min]": d(60), "start_date[max]": d(30)}, mr.filtros[4]
assert mr.filtros[5] == {"media_type": "video", "start_date[max]": d(60)}, mr.filtros[5]
assert not any((f or {}).get("media_type") == "image" for f in mr.filtros), "ya no se pide una rebanada solo de imagen"
m14 = MetaRebanadas()
m14.collect("natal", "BR", pages=3, rebanar=14)               # temporada: desde 14 dias
assert m14.filtros[1] == {"start_date[min]": d(60), "start_date[max]": d(14)}
mc = MetaRebanadas(cae_en=2)                                   # la segunda rebanada la limita Meta
got_ads, _ = mc.collect("receitas", "BR", pages=3, rebanar=30)
assert len(mc.filtros) == 3 and len(got_ads) == 2 and mc.rebanadas_hasta > time.time(), "conserva lo que ya tenia"
mc.filtros = []
mc.collect("otra", "BR", pages=3, rebanar=30)
assert mc.filtros == [None], "tras el limite, en esta corrida solo la pagina 1"
assert len(MetaRebanadas().collect("", "BR", pages=3, page_id="99", rebanar=30)[0]) == 1, "la pagina de un anunciante no se rebana"
radar.polite = _polite

# Anuncios en video (dueno, 2026-11-11: «vende mas el video que imagen y yo escalo mas con videos que con imagenes»): el anunciante con la mitad o
# mas de sus anuncios activos en video se revisa primero (despues de los de WhatsApp) y suma 1 punto a la nota (tope 10)
vid = radar.aggregate(mk("v1", "Videos", 8, 70, "https://x.com/", video=True) + mk("v2", "Imagenes", 8, 70, "https://y.com/")
                      + mk("v3", "Casi", 7, 70, "https://z.com/", video=True), NOW)
assert vid["v1"]["video_activos"] == 4 and vid["v1"]["active"] == 8 and radar.cuota_video(vid["v1"]) == 0.5 and radar.en_video(vid["v1"]), vid["v1"]["video_activos"]
assert vid["v2"]["video_activos"] == 0 and not radar.en_video(vid["v2"])
assert vid["v3"]["video_activos"] == 4 and radar.en_video(vid["v3"]), "4 de 7 en video: la mitad o mas"
assert radar.video_txt(vid["v1"]) == "4 de 8 anuncios activos en video"
assert radar.cuota_video({"active": 0}) == 0.0 and not radar.en_video({}), "sin anuncios activos no es video (ni se cae)"
copias = radar.aggregate([dict(a, collation_count=5 if a["video"] else 1) for a in mk("v4", "Copias", 4, 70, "https://w.com/", video=True)], NOW)["v4"]
assert copias["video_activos"] == 10 and copias["active"] == 12, "los anuncios en video cuentan con sus copias, como los activos"
# la nota: +1 si escala con video, tope 10; el desglose solo cambia si la nota sube
medio = dict(vid["v1"], active=12, video_activos=6, days=70)
assert radar.score(medio, 1, dict(land, video_risk=False), False) == (7, "1+2+1+2+0=6 +1 por anuncios en video = 7"), radar.score(medio, 1, dict(land, video_risk=False), False)
assert radar.score(dict(vid["v2"], active=12, days=70), 1, dict(land, video_risk=False), False) == (6, "1+2+1+2+0=6"), "sin video, la nota de siempre"
assert radar.score(dict(medio, active=45, video_activos=45, days=74), 3, dict(land, video_risk=False), True) == (10, "3+2+2+2+1=10"), "con 10 no pasa de 10"
assert radar.con_bono_video(medio, 9, "x=9") == (10, "x=9 +1 por anuncios en video = 10")
# el orden de revision: WhatsApp primero; dentro de cada grupo, los de video; luego el de siempre (winner_rank)
wa = radar.aggregate(mk("w1", "WA imagen", 30, 120, "https://api.whatsapp.com/send?phone=55") + mk("w2", "WA video", 8, 31, "https://wa.me/55", video=True), NOW)
orden = sorted([vid["v2"], vid["v1"], wa["w1"], wa["w2"]], key=radar.orden_revision, reverse=True)
assert [g["page_id"] for g in orden] == ["w2", "w1", "v1", "v2"], [g["page_id"] for g in orden]
assert radar.winner_rank(wa["w1"]) > radar.winner_rank(wa["w2"]), "el de video gana aunque su rank sea menor"

# run() siempre cierra con la linea de uso de Gemini, aunque la corrida muera, y devuelve el codigo de salida de la corrida
_correr_real, salida = radar._correr, io.StringIO()


def _muere(args):
    raise RuntimeError("se cayo")


radar._correr = _muere
try:
    with contextlib.redirect_stdout(salida):
        radar.run(None)
    raise SystemExit("debio propagar el error")
except RuntimeError:
    pass
finally:
    radar._correr = _correr_real
assert salida.getvalue().strip().startswith("Gemini: "), salida.getvalue()
radar._correr = lambda args: 7
with contextlib.redirect_stdout(io.StringIO()):
    assert radar.run(None) == 7
radar._correr = _correr_real
print("\nTODO OK (logica offline, datos sinteticos)")
