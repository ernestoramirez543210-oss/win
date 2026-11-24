"""Prueba offline de la conexion con la planilla: usa una API de Google FALSA, no toca tu planilla."""
import types
import urllib.parse

import radar
import sheets

sheets._access_token = lambda: "token-falso"


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self.payload


class Fake:
    """Responde por coincidencia de texto en la URL (ya decodificada) y guarda las llamadas."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, timeout=None, headers=None, params=None, json=None):
        url = urllib.parse.unquote(url)
        self.calls.append({"m": method, "url": url, "params": params, "json": json})
        for key, payload in self.routes.items():
            if key in url:
                return Resp(payload)
        return Resp({"error": {"message": "sin ruta"}}, 404)


assert sheets.col_letter(0) == "A" and sheets.col_letter(5) == "F" and sheets.col_letter(26) == "AA"

# Encabezados de OFERTAS en OTRO orden: el radar debe ubicar cada valor por el nombre de columna
HEAD = ["Fecha", "Anunciante", "Palabra clave", "Dominio", "Anuncios activos", "Días en el aire", "Nicho",
        "Precio origen", "Entregable", "Embudo / checkout", "Hueco Colombia", "Link del anuncio",
        "Link de la página", "Ángulo", "Nota", "Justificación", "Status", "Columna extra del dueño"]
ROWS = [HEAD,
        ["21/09/2026", "Chef Confeiteira", "receitas passo a passo em PDF", "no visible", "~2", "88", "recetas",
         "R$12,90", "PDF", "WhatsApp directo", "1 anunciante", "https://www.facebook.com/ads/library/?id=999900000834673",
         "no visible", "angulo", "6", "2+2+0+2+0=6", "ganadora"],
        ["22/09/2026", "Maria Merces", "receitas passo a passo em PDF", "vestidodecroche.store", "~8", "33", "x",
         "R$17,90", "PDF", "Landing", "2 anunciantes", "https://www.facebook.com/ads/library/?id=9999000000846483",
         "https://vestidodecroche.store/", "a", "6", "j", "descartada"]]
KW = [["Palabra clave", "Idioma", "Mercado", "Nicho", "Origen", "Búsquedas", "Ofertas registradas", "Última búsqueda", "Estado"],
      ["receitas passo a passo em PDF", "pt-BR", "Brasil", "recetas", "semilla", "5", "3", "08/10/2026", "activa"],
      ["cosméticos naturais caseiros", "pt-BR", "Brasil", "belleza", "semilla", "2", "0", "29/09/2026", "activa"]]
fake = Fake({"/values/'OFERTAS'!1:1": {"values": [HEAD]},
             "/values/'OFERTAS'": {"values": ROWS},
             "/values/'PALABRAS-CLAVE'!1:1": {"values": [KW[0]]},
             "/values/'PALABRAS-CLAVE'": {"values": KW},
             ":append": {}, "values:batchUpdate": {}})
sh = sheets.Sheet("SHEET123", session=fake)

of = sh.read_dicts("OFERTAS")
assert len(of) == 2 and of[0]["Anunciante"] == "Chef Confeiteira" and of[1]["_row"] == 3, of
sh.check_headers("OFERTAS", radar.SHEET_COLS)                       # todos presentes
try:
    sheets.Sheet("S", session=Fake({"/values/'OFERTAS'!1:1": {"values": [["Fecha", "Anunciante"]]}})) \
        .check_headers("OFERTAS", radar.SHEET_COLS)
    raise AssertionError("debio fallar con encabezados faltantes")
except sheets.SheetsError as e:
    assert "faltan" in str(e) and "Nota" in str(e)

# Aprendizaje y lo ya registrado, desde las filas vivas
assert radar.status_adjust(of) == {"receitas passo a passo em PDF": 1}      # +3 ganadora, -2 descartada
seen = radar.read_seen(of)
assert "chef confeiteira" in seen["names"] and "vestidodecroche.store" in seen["domains"]
assert {"999900000834673", "9999000000846483"} <= seen["ad_ids"]

# Palabras desde la planilla, con numero de fila
kws = radar.parse_keywords(sh.read_dicts("PALABRAS-CLAVE"))
assert [(k["pt"], k["row"]) for k in kws] == [("receitas passo a passo em PDF", 2), ("cosméticos naturais caseiros", 3)]

# write_offer: una fila nueva, cada valor en SU columna aunque el orden de la planilla sea otro
row = {c: "" for c in radar.CAND_COLS}
row.update({"Fecha": "11/10/2026", "Palabra clave": "kw", "Anunciante": "Nuevo SA", "Nota": 7, "Días en el aire": 40,
            "Status": "nuevo", "Justificación": "=SUMA(1)"})
args = types.SimpleNamespace(simular_sheets=False)
radar.write_offer(sh, row, args)
call = fake.calls[-1]
assert call["m"] == "POST" and ":append" in call["url"], call
assert call["params"] == {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}   # RAW: nada se evalua
vals = call["json"]["values"][0]
assert len(vals) == len(HEAD)
assert vals[HEAD.index("Anunciante")] == "Nuevo SA" and vals[HEAD.index("Palabra clave")] == "kw"
assert vals[HEAD.index("Nota")] == 7 and vals[HEAD.index("Columna extra del dueño")] == ""
assert vals[HEAD.index("Justificación")] == "=SUMA(1)"      # llega como texto crudo, no como formula
assert not any(c["m"] == "DELETE" for c in fake.calls)       # el radar nunca borra filas

# Modo simulacion: no llama a la API
n = len(fake.calls)
radar.write_offer(sh, row, types.SimpleNamespace(simular_sheets=True))
assert len(fake.calls) == n

# Contadores de PALABRAS-CLAVE: Busquedas +1, Ofertas + escritas, Ultima busqueda
written_kw = radar.Counter({"receitas passo a passo em PDF": 2})
radar.bump_keywords(sh, kws, [["receitas passo a passo em PDF", "11/10/2026", 1, 2]], written_kw, args)
upd = fake.calls[-1]["json"]
assert upd["valueInputOption"] == "RAW"
got = {d["range"]: d["values"][0][0] for d in upd["data"]}
assert got == {"'PALABRAS-CLAVE'!F2": 6, "'PALABRAS-CLAVE'!G2": 5, "'PALABRAS-CLAVE'!H2": "11/10/2026"}, got

# Aprendizaje por nicho: una ganadora en 'recetas' sube las palabras del nicho 'recetas'
offers = [{"Nicho": "recetas / renda extra", "Status": "ganadora"}, {"Nicho": "belleza", "Status": "descartada"},
          {"Nicho": "Manualidades / crochê", "Status": "nuevo"}]
nadj = radar.niche_adjust(offers)
assert nadj == {"recetas": 3, "belleza": -2}, nadj
ks = [{"pt": "a", "nicho": "belleza", "offers": 1, "searches": 1, "last": "", "es": "", "row": 2},
      {"pt": "b", "nicho": "general", "offers": 1, "searches": 1, "last": "", "es": "", "row": 3},
      {"pt": "c", "nicho": "Recetas", "offers": 0, "searches": 1, "last": "", "es": "", "row": 4}]
order = [k["pt"] for k in radar.pick_keywords(ks, {"keywords": {}}, 3, None, nadj)]
assert order == ["c", "b", "a"], order          # recetas (+3) > general (1) > belleza (1-2)

# Menu desplegable en Status: no cambia valores, no estricto, cubre desde la fila 2
fake2 = Fake({"/values/'OFERTAS'!1:1": {"values": [HEAD]},
              "SHEET123": {"sheets": [{"properties": {"sheetId": 777, "title": "OFERTAS",
                                                      "gridProperties": {"rowCount": 1000}}}]}})
sheets.Sheet("SHEET123", session=fake2).set_dropdown("OFERTAS", "Status", radar.STATUS_VALUES)
req = fake2.calls[-1]["json"]["requests"][0]["setDataValidation"]
assert fake2.calls[-1]["url"].endswith("SHEET123:batchUpdate"), fake2.calls[-1]["url"]
assert req["range"] == {"sheetId": 777, "startRowIndex": 1, "endRowIndex": 1000,
                        "startColumnIndex": HEAD.index("Status"), "endColumnIndex": HEAD.index("Status") + 1}, req["range"]
assert [v["userEnteredValue"] for v in req["rule"]["condition"]["values"]] == \
    ["nuevo", "vigilar", "testiando", "ganadora", "descartada", "no testeada"]
assert req["rule"]["strict"] is False and req["rule"]["showCustomUi"] is True
try:
    sheets.Sheet("SHEET123", session=fake2).set_dropdown("OFERTAS", "NoExiste", ["x"])
    raise AssertionError("debio fallar")
except sheets.SheetsError as e:
    assert "NoExiste" in str(e)

# Brasil primero, Colombia despues: sin Colombia revisada se registra solo con nota alta y marcada PENDIENTE
ns = types.SimpleNamespace(exigir_colombia=False, nota_sin_colombia=7)
assert radar.write_gate(True, 6, ns) == (True, "")                      # Colombia revisada: manda --min-nota
assert radar.write_gate(False, 7, ns) == (True, "")                     # sin Colombia pero nota 7: se registra
ok, why = radar.write_gate(False, 6, ns)
assert not ok and "nota 6 < 7" in why, why                              # sin Colombia y nota 6: queda en el CSV
assert not radar.write_gate(False, 9, types.SimpleNamespace(exigir_colombia=True, nota_sin_colombia=7))[0]

# Error de Google -> SheetsError legible (sin volcar nada sensible)
try:
    sheets.Sheet("S", session=Fake({})).read("OFERTAS")
    raise AssertionError("debio fallar")
except sheets.SheetsError as e:
    assert "404" in str(e)
# borrar filas: una sola peticion, de abajo hacia arriba (para que los numeros no se corran), nunca la fila de encabezados
fb = Fake({":batchUpdate": {}, "SHEET9": {"sheets": [{"properties": {"sheetId": 77, "title": "OFERTAS"}}]}})
sheets.Sheet("SHEET9", session=fb).borrar_filas("OFERTAS", [5, 130, 37, 130])
pedido = [c for c in fb.calls if c["url"].endswith(":batchUpdate")]
assert len(pedido) == 1, fb.calls
assert [(q["deleteDimension"]["range"]["startIndex"], q["deleteDimension"]["range"]["endIndex"]) for q in pedido[0]["json"]["requests"]] == [(129, 130), (36, 37), (4, 5)]
assert all(q["deleteDimension"]["range"]["sheetId"] == 77 and q["deleteDimension"]["range"]["dimension"] == "ROWS" for q in pedido[0]["json"]["requests"])
try:
    sheets.Sheet("SHEET9", session=fb).borrar_filas("OFERTAS", [1, 4])
    raise AssertionError("la fila 1 (encabezados) no se borra")
except sheets.SheetsError:
    pass
sheets.Sheet("SHEET9", session=fb).borrar_filas("OFERTAS", [])
assert len([c for c in fb.calls if c["url"].endswith(":batchUpdate")]) == 1, "sin filas no hay peticion"

print("TODO OK (conexion con la planilla, API de Google simulada)")
