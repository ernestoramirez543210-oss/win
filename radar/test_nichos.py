"""Pruebas SIN red de los nichos nuevos y de la cosecha (radar.py: nichos.csv): lectura, eleccion (nunca buscados primero y por prioridad, un tema por vez,
vuelta a los 7 dias, descanso de las busquedas agotadas), reposicion y cosecha con la IA (simulada), comparacion con la planilla para no registrar dos
veces la misma oferta, dominio base, negocios locales y el piso de busquedas por promesa del reparto."""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import ia
import radar

ahora = lambda **d: (datetime.now() - timedelta(**d)).isoformat(timespec="seconds")
BOM = chr(0xFEFF)

# ---------------------------------------------------------------- read_nichos
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "nichos.csv"
    p.write_text(BOM + "palabra,es,nicho,origen\nAmigurumi receita PDF,amigurumi,tejido,semilla\namigurumi receita pdf,,tejido,repetida\n"
                 ",sin palabra,,\nmoldes EVA,,,semilla\n", encoding="utf-8")
    filas = radar.read_nichos(p)
    assert [r["pt"] for r in filas] == ["Amigurumi receita PDF", "moldes EVA"], filas
    assert filas[1]["nicho"] == "general" and filas[0]["es"] == "amigurumi" and filas[0]["prioridad"] == 3, "sin columna prioridad = 3"
    assert radar.read_nichos(Path(tmp) / "no-existe.csv") == []

# ---------------------------------------------------------------- agregar_nichos: no toca lo que ya esta (pausadas, columnas viejas)
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "nichos.csv"
    p.write_text(BOM + "palabra,es,nicho,origen,estado\nviejo uno,,a,semilla,\nviejo pausado,,b,semilla,pausada\n", encoding="utf-8")
    radar.agregar_nichos(p, [{"pt": "nuevo uno", "es": "x", "nicho": "c", "prioridad": 1, "origen": "visto en X"}])
    texto = p.read_text(encoding="utf-8-sig")
    assert texto.splitlines()[0] == "palabra,es,nicho,origen,estado,prioridad" and "viejo pausado,,b,semilla,pausada," in texto, texto
    filas = radar.read_nichos(p)
    assert [(r["pt"], r["prioridad"]) for r in filas] == [("viejo uno", 3), ("nuevo uno", 1)], filas

# ---------------------------------------------------------------- busqueda_agotada
st = {"keywords": {}, "etiquetas": {
    "a": {"busquedas": 3, "candidatos": 1, "seguidas_sin": 2, "nuevos_ultima": 9, "ultima": ahora(days=3)},      # 2 seguidas sin nada
    "b": {"busquedas": 1, "candidatos": 0, "seguidas_sin": 1, "nuevos_ultima": 1, "ultima": ahora(days=3)},      # 1 sin nada y casi nadie nuevo
    "c": {"busquedas": 1, "candidatos": 0, "seguidas_sin": 1, "nuevos_ultima": 6, "ultima": ahora(days=3)},      # 1 sin nada pero trajo 6 nuevos
    "d": {"busquedas": 5, "candidatos": 0, "seguidas_sin": 5, "nuevos_ultima": 0, "ultima": ahora(days=22)},     # agotada, pero ya descanso 21 dias
    "e": {"busquedas": 22, "candidatos": 0}, "f": {"busquedas": 15, "candidatos": 1}, "g": {"busquedas": 14, "candidatos": 4},   # de antes del 04/11
}}
st["keywords"].update({"e": ahora(hours=5), "f": ahora(hours=5), "g": ahora(hours=5)})
assert [x for x in "abcdefg" if radar.busqueda_agotada(x, st)] == ["a", "b", "e", "f"], [x for x in "abcdefg" if radar.busqueda_agotada(x, st)]
assert not radar.busqueda_agotada("nunca buscada", st)

# ---------------------------------------------------------------- pick_nichos
F = [{"pt": pt, "es": "", "nicho": n, "origen": "", "prioridad": pr} for pt, n, pr in
     [("a1", "a", 3), ("a2", "a", 3), ("b1", "b", 3), ("c1", "c", 3), ("d1", "d", 3), ("e1", "e", 3), ("z1", "z", 1)]]
st = {"keywords": {}, "etiquetas": {}}
assert [r["pt"] for r in radar.pick_nichos(F, st, 3)] == ["z1", "a1", "b1"], "prioridad 1 primero; luego un tema por vez, en el orden del archivo"
assert radar.pick_nichos(F, st, 0) == []
st = {"keywords": {"nicho: a1": ahora(hours=1), "nicho: b1": ahora(days=8), "nicho: c1": ahora(days=8), "nicho: d1": ahora(days=8), "nicho: z1": ahora(days=8)},
      "etiquetas": {"nicho: b1": {"busquedas": 1, "candidatos": 2}, "nicho: c1": {"busquedas": 2, "candidatos": 0},
                    "nicho: d1": {"busquedas": 1, "candidatos": 0}, "nicho: z1": {"busquedas": 1, "candidatos": 0, "seguidas_sin": 1, "nuevos_ultima": 0}}}
assert [r["pt"] for r in radar.nichos_sin_estrenar(F, st)] == ["a2", "e1"]
assert [r["pt"] for r in radar.pick_nichos(F, st, 9)] == ["a2", "e1", "b1", "d1"], \
    "primero los nunca buscados; luego el que dio candidatos; c1 y z1 (agotados) descansan; a1 (hace 1 h) espera sus 7 dias"

# ---------------------------------------------------------------- reponer_nichos
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "nichos.csv"
    p.write_text(BOM + "palabra,es,nicho,prioridad,origen\nmoldes EVA,,manualidades,3,semilla\n", encoding="utf-8")
    filas = radar.read_nichos(p)
    preguntas = []

    def ia_falsa(prompt):
        preguntas.append(prompt)
        return {"busquedas": [{"palabra": "Moldes eva", "es": "x", "nicho": "y"},           # ya esta (sin tildes ni mayusculas)
                              {"palabra": "croche vestidos", "es": "", "nicho": ""},       # ya se busca como palabra de la planilla
                              {"palabra": "uno", "es": "", "nicho": ""},                   # una sola palabra: muy amplia
                              {"palabra": "palavra " * 8, "es": "", "nicho": ""},           # muy larga
                              "no es un objeto",
                              {"palabra": "planner de estudos PDF", "es": "planner de estudio", "nicho": "estudio"},
                              {"palabra": "planner de estudos pdf", "es": "", "nicho": ""}]}   # repetida dentro de la respuesta
    planilla = [{"Nicho": "tejido", "Status": "ganadora", "Entregable": "PDF 120 vestidos", "Ángulo": "gana dinero con crochet"},
                {"Nicho": "tejido"}, {"Nicho": "por clasificar"}, {"Nicho": "finanzas", "Status": "descartada"}]
    nuevas = radar.reponer_nichos(p, filas, planilla, ["croche vestidos"], "03/11/2026", ia_falsa)
    assert [r["pt"] for r in nuevas] == ["planner de estudos PDF"] and nuevas[0]["origen"] == "IA 03/11/2026", nuevas
    assert "tejido" in preguntas[0] and "por clasificar" not in preguntas[0] and "moldes EVA" in preguntas[0]
    assert "120 vestidos" in preguntas[0] and "descarto (no propongas parecidos): finanzas" in preguntas[0], "usa lo que el equipo eligio y lo que descarto"
    assert [r["pt"] for r in radar.read_nichos(p)] == ["moldes EVA", "planner de estudos PDF"], "el archivo queda con las de antes y las nuevas"
    p.unlink()
    assert radar.reponer_nichos(p, filas, [], [], "03/11/2026", lambda _: {"otra": 1}) == [] and not p.exists(), "respuesta sin lista: no se escribe nada"

# ---------------------------------------------------------------- cosechar_ideas
vistos = [{"label": "nicho: x", "name": "Tienda X", "texto": "loja", "dias": 300, "activos": 40, "whatsapp": False, "resultado": "pagina de destino es una tienda"},
          {"label": "nicho: x", "name": "Kit Kids", "texto": "Kit com 500 atividades PDF", "dias": 90, "activos": 30, "whatsapp": True, "resultado": "candidato nota 8"},
          {"label": "ángulo: y", "name": "Moldes Ana", "texto": "300 moldes de feltro", "dias": 200, "activos": 5, "whatsapp": True,
           "resultado": "5 anuncios activos (<8)"}]
preguntas = []


def ia_cosecha(prompt):
    preguntas.append(prompt)
    return {"busquedas": [{"palabra": "atividades de alfabetização kit", "es": "x", "nicho": "educacion", "de": 0},
                          {"palabra": "moldes de feltro natal", "es": "", "nicho": "manualidades", "de": 1},
                          {"palabra": "moldes EVA", "es": "", "nicho": "", "de": 1},                 # ya en la lista
                          {"palabra": "algo sin origen", "es": "", "nicho": "", "de": True}],       # «de» invalido: sale como cosecha general
            "promesas": [{"angulo": "acesso imediato", "tipo": "x"},                                 # ya la tenemos
                         {"angulo": "arquivos editáveis no Canva", "tipo": "plantillas listas"},
                         {"angulo": "uma", "tipo": ""}]}                                            # una palabra: no


nuevos, promesas = radar.cosechar_ideas(vistos, [{"pt": "moldes EVA"}], ["croche vestidos"], [{"angulo": "acesso imediato"}], [], "04/11/2026", ia_cosecha)
assert "Tienda X" not in preguntas[0] and preguntas[0].index("Kit Kids") < preguntas[0].index("Moldes Ana"), "sin las tiendas; los candidatos primero"
assert [(r["pt"], r["prioridad"]) for r in nuevos] == [("atividades de alfabetização kit", 1), ("moldes de feltro natal", 2), ("algo sin origen", 2)], nuevos
assert nuevos[0]["origen"].startswith("visto en Kit Kids (nicho: x)") and nuevos[2]["origen"] == "cosecha 04/11/2026", nuevos
assert [a["angulo"] for a in promesas] == ["arquivos editáveis no Canva"] and promesas[0]["origen"] == "cosecha 04/11/2026", promesas
assert radar.cosechar_ideas(vistos[:1], [], [], [], [], "04/11/2026", lambda _: 1 / 0) == ([], []), "sin vistos utiles no se pregunta nada"
lleno = [{"angulo": f"promesa {i}"} for i in range(60)]
assert radar.cosechar_ideas(vistos, [], [], lleno, [], "04/11/2026", ia_cosecha)[1] == [], "con 60 promesas no se agregan mas"

# ---------------------------------------------------------------- misma oferta ya en la planilla
planilla = [{"Anunciante": "Lia Crochê", "Nicho": "manualidades / crochê para vender", "Entregable": "Recetas de crochet (+120 vestidos)",
             "Ángulo": "Si ya haces crochet, gana dinero con vestidos"},
            {"Anunciante": "Prof Juliana", "Nicho": "atividades", "Entregable": "PDF", "Ángulo": "actividades listas para imprimir"}]
nueva = {"Anunciante": "Crochê da Vovó", "Nicho": "crochê", "Entregable": "PDF", "Ángulo": "120 vestidos de crochet para vender y ganar dinero"}
pedidas = []
assert radar.misma_oferta_planilla(nueva, planilla, lambda pr: pedidas.append(pr) or {"misma": 0}) == "Lia Crochê"
assert "Lia Crochê" in pedidas[0] and "Prof Juliana" not in pedidas[0], "solo se comparan las parecidas"
assert radar.misma_oferta_planilla(nueva, planilla, lambda pr: {"misma": -1}) is None
assert radar.misma_oferta_planilla(nueva, planilla, lambda pr: {"misma": True}) is None
assert radar.misma_oferta_planilla({"Anunciante": "X", "Nicho": "astrologia", "Ángulo": "mapa astral"}, planilla, lambda pr: 1 / 0) is None, \
    "sin parecidas no se pregunta"

# ---------------------------------------------------------------- dominio base, negocios locales, JSON de la IA
assert radar.dominio_base("leiturinha2.expertacademypro.com.br") == "expertacademypro.com.br"
assert radar.dominio_base("expertacademypro.com.br") == "expertacademypro.com.br" and radar.dominio_base("pay.kiwify.com.br") == "pay.kiwify.com.br"
assert radar.dominio_base("aventaltntprofessora.vercel.app") == "aventaltntprofessora.vercel.app", "en una plataforma el subdominio es el cliente"
assert radar.dominio_base("m.timehibrido.com.br") == "timehibrido.com.br" and radar.dominio_base("calendario.papeleartes.online") == "papeleartes.online"
seen = radar.read_seen([{"Anunciante": "Expert Kids", "Dominio": "leiturinha2.expertacademypro.com.br", "ID de la página": "123"}])
assert "expertacademypro.com.br" in seen["bases"] and "123" in seen["page_ids"]
assert radar.SERVICIO_LOCAL.search(radar.fold("Zezé Costureira Reformas & Reparos de Roupas"))
assert radar.SERVICIO_LOCAL.search(radar.fold("Óticas Mancinni 2")) and radar.SERVICIO_LOCAL.search(radar.fold("Matos & Feitosa Odontologia"))
assert not any(radar.SERVICIO_LOCAL.search(radar.fold(n)) for n in ("Lia Crochê", "Reformando Vidas Devocional", "Prof Juliana", "Atividades Prontas"))
assert ia._objeto([{"modelable": True}]) == {"modelable": True}, "una lista con un objeto se acepta (el 03/11 Gemini contesto asi)"
assert ia._objeto([{"modelable": False}, {"modelable": True}]) == {"modelable": False}, "con varios objetos vale el primero"
assert radar.nucleo("guia adestramento cães") == radar.nucleo("adestramento de cães guia PDF") == {"adestramento", "caes"}
assert radar.nucleo("moldes EVA natal") != radar.nucleo("moldes EVA")
ideas = radar._ideas_validas([{"palabra": "guia adestramento cães"}, {"palabra": "apostila de horta urbana"}, {"palabra": "horta urbana PDF"}],
                             {radar.fold("adestramento de cães guia")})
assert [t for t, _ in ideas] == ["apostila de horta urbana"], ("la misma busqueda con otro orden o formato no entra", ideas)

# ---------------------------------------------------------------- semillas de ganadoras (lo que el equipo marca en el dash)
planilla = [{"Anunciante": "Vieja Ganadora", "Status": "ganadora", "Link del anuncio": "https://www.facebook.com/ads/library/?id=111",
             "Nicho": "crochê", "Entregable": "PDF 120 vestidos", "Ángulo": "gana dinero con crochet"},
            {"Anunciante": "Probando", "Status": "testiando", "Link del anuncio": "https://www.facebook.com/ads/library/?id=222", "Nicho": "pano de prato"},
            {"Anunciante": "Nueva", "Status": "nuevo", "Link del anuncio": "https://www.facebook.com/ads/library/?id=333"},
            {"Anunciante": "Sin Link", "Status": "ganadora", "Nicho": "sermones"},
            {"Anunciante": "Mirando", "Status": "vigilar", "Nicho": "moldes"}]
assert [r["Anunciante"] for r in radar.ganadoras_por_sembrar(planilla, {})] == ["Sin Link", "Vieja Ganadora", "Probando"], \
    "ganadoras primero (las mas recientes antes), luego testiando; ni nuevo ni vigilar"
assert radar.clave_oferta(planilla[0]) == "111" and radar.clave_oferta(planilla[3]) == "n:sin link"
st = {"sembradas": {"111": ["ganadora"], "222": ["testiando"]}}
assert [r["Anunciante"] for r in radar.ganadoras_por_sembrar(planilla, st)] == ["Sin Link"], "una vez por oferta y estado"
planilla[1]["Status"] = "ganadora"
assert [r["Anunciante"] for r in radar.ganadoras_por_sembrar(planilla, st)] == ["Sin Link", "Probando"], "testiando que pasa a ganadora siembra otra vez"
pedidas = []
ideas = radar.sembrar_de_ganadora(planilla[0], [{"pt": "moldes EVA"}], ["croche vestidos"], "04/11/2026",
                                  lambda pr: pedidas.append(pr) or {"busquedas": [{"palabra": "vestidos de crochê receitas", "es": "x", "nicho": "crochet"},
                                                                                  {"palabra": "croche vestidos PDF"},        # ya buscada, con formato
                                                                                  {"palabra": "saias de crochê PDF", "nicho": "crochet"}]})
assert [(r["pt"], r["prioridad"]) for r in ideas] == [("vestidos de crochê receitas", 0), ("saias de crochê PDF", 0)], ideas
assert ideas[0]["origen"] == "de la ganadora Vieja Ganadora 04/11/2026" and "PDF 120 vestidos" in pedidas[0] and "«ganadora»" in pedidas[0]
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "nichos.csv"
    radar.agregar_nichos(p, ideas)
    assert [r["prioridad"] for r in radar.read_nichos(p)] == [0, 0], "la prioridad 0 se lee como 0 (no como vacia)"
F0 = [{"pt": "semilla", "es": "", "nicho": "a", "origen": "", "prioridad": 3}, {"pt": "de ganadora", "es": "", "nicho": "b", "origen": "", "prioridad": 0}]
assert radar.pick_nichos(F0, {"keywords": {}, "etiquetas": {}}, 1)[0]["pt"] == "de ganadora", "lo que sale de una ganadora va primero"
filas_n = [{"pt": "a", "origen": "de la testiando Probando 04/11/2026"}, {"pt": "b", "origen": "de la ganadora Vieja Ganadora 04/11/2026"},
           {"pt": "c", "origen": "visto en Probando (nicho: x) 04/11/2026"}, {"pt": "d", "origen": "semilla 03/11/2026"}]
st = {"sembradas": {"111": ["ganadora"], "222": ["testiando"]}}
assert radar.semillas_de_fallidas(filas_n, planilla, st) == [], "nadie descartado todavia"
planilla[1]["Status"] = "descartada"           # se probo y no funciono
assert [r["pt"] for r in radar.semillas_de_fallidas(filas_n, planilla, st)] == ["a"], "solo las semillas de esa oferta (no lo visto en ella)"
assert radar.semillas_de_fallidas(filas_n, planilla, {"sembradas": {}}) == [], "una descartada que nunca se sembro no quita nada"
elegidas = radar.elegidas_por_el_equipo(planilla)
assert elegidas[0].startswith("[ganadora] sermones") and elegidas[-1].startswith("[vigilar] moldes"), elegidas

# ---------------------------------------------------------------- piso de promesas
sin_angulo = {"modos": {"palabra": {"ultima": 1}, "combo": {"ultima": 2}, "angulo": {"ultima": 9}}}
assert radar.allocate_modes(sin_angulo, 2) == {"palabra": 1, "combo": 1, "angulo": 0}
assert radar.allocate_modes(sin_angulo, 2, min_angulo=1)["angulo"] == 1 and sum(radar.allocate_modes(sin_angulo, 2, 1).values()) == 2
assert radar.allocate_modes(sin_angulo, 1, min_angulo=3) == {"palabra": 0, "combo": 0, "angulo": 1}, "el piso nunca pasa de las busquedas que hay"
assert radar.allocate_modes({}, 0, min_angulo=1)["angulo"] == 0

print("TODO OK (nichos nuevos y cosecha: lectura, eleccion, descanso, reposicion, cosecha con IA, misma oferta, dominio base y piso de promesas)")
