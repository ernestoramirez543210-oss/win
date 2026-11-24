"""Prueba offline del modulo de IA con una API de Gemini FALSA: no usa la clave real ni gasta nada."""
import json
import os
import tempfile
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import requests

import ia
import radar

TMP = Path(tempfile.mkdtemp(prefix="test_ia_"))
time.sleep = lambda s: None            # sin esperas reales en los reintentos
AHORA_REAL = ia._ahora


def aislar():
    """Nada de esta prueba toca la carpeta real: ni ia_uso.json, ni .env, ni el entorno de gasto, ni la clave."""
    ia.USO_FILE, ia.ENV_FILE = TMP / "ia_uso.json", TMP / "no-existe.env"
    for k in ("GEMINI_MODEL", "GEMINI_MAX_USD_DIA", "GEMINI_API_KEY"):
        os.environ.pop(k, None)
    ia._key = lambda: "clave-falsa-de-prueba"
    ia._ahora = AHORA_REAL
    ia._model_cache.clear()
    ia._think_cache.clear()
    ia.reiniciar_corrida()
    (TMP / "ia_uso.json").unlink(missing_ok=True)


def reloj(iso_utc):
    """Fija la hora (UTC) que ve el modulo, para probar la medianoche de Bogota."""
    ia._ahora = lambda: datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self.payload


def gem(text, uso=(100, 20, 0), finish="STOP"):
    """Respuesta de Gemini con usageMetadata = (promptTokenCount, candidatesTokenCount, thoughtsTokenCount); uso=None la quita."""
    d = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}
    if uso is not None:
        d["usageMetadata"] = {"promptTokenCount": uso[0], "candidatesTokenCount": uso[1], "thoughtsTokenCount": uso[2],
                              "totalTokenCount": sum(uso)}
    return d


class Fake:
    def __init__(self, post_responses, models=None):
        self.post_responses, self.models, self.calls = list(post_responses), models, []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        r = self.post_responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, headers, params))
        return Resp({"models": self.models or []})


def falla(fn, *clases):
    """Ejecuta fn y devuelve la excepcion que lanza (debe ser de alguna de `clases`); si no lanza nada, la prueba falla."""
    try:
        fn()
    except clases or (Exception,) as e:
        return e
    raise AssertionError("debio fallar")


aislar()

# 1) respuesta normal; la clave viaja en la CABECERA, nunca en la URL
f = Fake([Resp(gem('{"a": 1}'))])
assert ia.ask_json("hola", f) == {"a": 1}
m, url, headers, body = f.calls[0]
assert "clave-falsa" not in url and headers["x-goog-api-key"] == "clave-falsa-de-prueba", (url, headers)
assert body["generationConfig"]["responseMimeType"] == "application/json" and body["generationConfig"]["temperature"] == 0
# con valla de codigo alrededor del JSON tambien se entiende
assert ia.ask_json("x", Fake([Resp(gem('```json\n{"b": 2}\n```'))])) == {"b": 2}

# 2) modelo retirado (404): lista los modelos y toma el Flash-Lite estable MAS NUEVO (sin preview; el lite ya no se descarta: es el barato)
ia._model_cache.clear()
modelos = [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/gemini-3.0-flash", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/gemini-4.0-flash-lite-preview", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/gemini-4.0-flash-preview", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/gemini-3.0-pro", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]}]
f = Fake([Resp({"error": {"message": "not found"}}, 404), Resp(gem('{"ok": true}'))], models=modelos)
assert ia.ask_json("x", f) == {"ok": True}
assert ":generateContent" in f.calls[-1][1] and "gemini-3.5-flash-lite:" in f.calls[-1][1], f.calls[-1][1]
assert ia._model_cache["m"] == "gemini-3.5-flash-lite"
# sin ningun Lite estable, cae al 'flash' estable mas nuevo (como antes); y con GEMINI_MODEL fijo no busca otro
sin_lite = [m for m in modelos if "lite" not in m["name"]]
assert ia.pick_model(Fake([], models=sin_lite)) == "gemini-3.0-flash"
assert ia.pick_model(Fake([], models=[m for m in modelos if "gemini-4" not in m["name"]])) == "gemini-3.5-flash-lite"
solo_malos = [{"name": "models/gemini-4.0-flash-lite-preview", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/gemini-3.0-pro", "supportedGenerationMethods": ["generateContent"]}]
assert "flash" in str(falla(lambda: ia.pick_model(Fake([], models=solo_malos)), ia.IAError))
os.environ["GEMINI_MODEL"] = "gemini-fijo"
f = Fake([Resp({"error": {"message": "not found"}}, 404)])
assert "404" in str(falla(lambda: ia.ask_json("x", f), ia.IAError)) and len(f.calls) == 1, "con GEMINI_MODEL fijo no se busca otro modelo"
os.environ.pop("GEMINI_MODEL")
aislar()

# 3) limite de uso (429) y caida (500): reintenta y se recupera
assert ia.ask_json("x", Fake([Resp({}, 429), Resp({}, 500), Resp(gem('{"z": 3}'))])) == {"z": 3}
# 4) clave invalida -> error legible; no es JSON -> error; nunca sale la clave
e = falla(lambda: ia.ask_json("x", Fake([Resp({"error": {"message": "API key not valid"}}, 403)])), ia.IAError)
assert "403" in str(e) and "clave-falsa" not in str(e)
assert "JSON" in str(falla(lambda: ia.ask_json("x", Fake([Resp(gem("esto no es json"))])), ia.IAError))
assert "limite" in str(falla(lambda: ia.ask_json("x", Fake([Resp({}, 429)] * 4)), ia.IAError))

# 4a) modelo por defecto y cuerpo de la peticion: Flash-Lite, razonamiento al minimo y tope de salida
aislar()
assert ia.DEFAULT_MODEL == "gemini-3.1-flash-lite"
f = Fake([Resp(gem('{"a": 1}'))])
ia.ask_json("hola", f)
assert "/models/gemini-3.1-flash-lite:generateContent" in f.calls[0][1], f.calls[0][1]
gen = f.calls[0][3]["generationConfig"]
assert gen["thinkingConfig"] == {"thinkingBudget": 0} and gen["maxOutputTokens"] == ia.MAX_TOKENS, gen
f = Fake([Resp(gem('{"a": 1}'))])
ia.ask_json("hola", f, max_tokens=300)
assert f.calls[0][3]["generationConfig"]["maxOutputTokens"] == 300
# GEMINI_MODEL cambia el modelo sin tocar codigo (en el entorno, o en .env si no esta en el entorno)
os.environ["GEMINI_MODEL"] = "gemini-3.5-flash-lite"
f = Fake([Resp(gem('{"a": 1}'))])
ia.ask_json("hola", f)
assert "gemini-3.5-flash-lite:" in f.calls[0][1]
os.environ.pop("GEMINI_MODEL")
(TMP / "prueba.env").write_text("# comentario\nGEMINI_MODEL = 'gemini-3.8-flash'\nOTRA=1\n", encoding="utf-8")
ia.ENV_FILE = TMP / "prueba.env"
f = Fake([Resp(gem('{"a": 1}'))])
ia.ask_json("hola", f)
assert "gemini-3.8-flash:" in f.calls[0][1], f.calls[0][1]
os.environ["GEMINI_MODEL"] = "gemini-3.1-flash-lite"
f = Fake([Resp(gem('{"a": 1}'))])
ia.ask_json("hola", f)
assert "gemini-3.1-flash-lite:" in f.calls[0][1], "el entorno manda sobre .env"
aislar()

# 4b) escalera de razonamiento (presupuesto 0 -> nivel minimal -> presupuesto 128 -> nivel low -> sin campo): un 400 no se cobra, pero tampoco se repite en cada llamada
SIN_CERO = Resp({"error": {"message": "Budget 0 is invalid. This model only works in thinking mode."}}, 400)
NIVEL_DESCONOCIDO = Resp({"error": {"message": 'Invalid JSON payload received. Unknown name "thinkingLevel" at '
                                                "'generation_config.thinking_config': Cannot find field."}}, 400)
SIN_MINIMAL = Resp({"error": {"message": "Thinking level minimal is not supported by this model. Supported levels: low, medium, high."}}, 400)
SIN_PRESUPUESTO = Resp({"error": {"message": 'Invalid JSON payload received. Unknown name "thinkingBudget" at '
                                                "'generation_config.thinking_config': Cannot find field."}}, 400)


def escalones(fake):
    return [c[3]["generationConfig"].get("thinkingConfig") for c in fake.calls]


# a) un modelo 3.x como gemini-3.1-flash-lite: rechaza el presupuesto 0 y su nivel por defecto ya es el minimal (lo mas barato que ofrece)
f = Fake([SIN_CERO, Resp(gem('{"a": 1}'))])
assert ia.ask_json("x", f) == {"a": 1}
assert escalones(f) == [{"thinkingBudget": 0}, {"thinkingLevel": "minimal"}], escalones(f)
assert f.calls[1][3]["generationConfig"]["maxOutputTokens"] == ia.MAX_TOKENS + 512, "deja margen para que el razonamiento no se coma la respuesta"
f2 = Fake([Resp(gem('{"b": 2}'))])
ia.ask_json("y", f2)
assert len(f2.calls) == 1 and escalones(f2) == [{"thinkingLevel": "minimal"}], "recuerda el escalon que funciono"
aislar()
# b) un flash de la generacion 2.5 que no deja apagar el razonamiento y no conoce los niveles: cae al presupuesto 128
f = Fake([SIN_CERO, NIVEL_DESCONOCIDO, Resp(gem('{"a": 1}'))])
assert ia.ask_json("x", f) == {"a": 1}
assert escalones(f) == [{"thinkingBudget": 0}, {"thinkingLevel": "minimal"}, {"thinkingBudget": 128}], escalones(f)
assert f.calls[2][3]["generationConfig"]["maxOutputTokens"] == ia.MAX_TOKENS + 384
f2 = Fake([Resp(gem('{"b": 2}'))])
ia.ask_json("y", f2)
assert len(f2.calls) == 1 and escalones(f2) == [{"thinkingBudget": 128}]
aislar()
# c) un flash 3.x sin nivel minimal (gemini-3.8-flash solo ofrece low, medium y high) que tampoco acepta presupuestos: llega al nivel low
f = Fake([SIN_CERO, SIN_MINIMAL, SIN_PRESUPUESTO, Resp(gem('{"a": 1}'))])
assert ia.ask_json("x", f) == {"a": 1}
assert escalones(f) == [{"thinkingBudget": 0}, {"thinkingLevel": "minimal"}, {"thinkingBudget": 128}, {"thinkingLevel": "low"}], escalones(f)
assert f.calls[3][3]["generationConfig"]["maxOutputTokens"] == ia.MAX_TOKENS + 768
assert all(len(c[3]["generationConfig"].get("thinkingConfig", {})) <= 1 for c in f.calls), "nunca nivel y presupuesto juntos (Google responde 400)"
aislar()
# d) un modelo que acepta el presupuesto 0 (los flash 2.5) se queda ahi
f = Fake([Resp(gem('{"a": 1}'))])
ia.ask_json("x", f)
assert escalones(f) == [{"thinkingBudget": 0}] and ia._think_cache == {"gemini-3.1-flash-lite": 0}
aislar()
f = Fake([Resp({"error": {"message": "x"}}, 400)] * len(ia.ESCALERA))             # ningun escalon sirve
e = falla(lambda: ia.ask_json("x", f), ia.IAError)
assert "400" in str(e) and len(f.calls) == len(ia.ESCALERA), (str(e), len(f.calls))
assert "thinkingConfig" not in f.calls[-1][3]["generationConfig"], "el ultimo escalon es sin campo"
assert ia._think_cache == {}, "si nada funciono no se recuerda nada"
aislar()
f = Fake([Resp({"error": {"message": "API key not valid. Please pass a valid API key."}}, 400)])
e = falla(lambda: ia.ask_json("x", f), ia.IAError)
assert len(f.calls) == 1 and "API key" in str(e) and "clave-falsa" not in str(e), "un 400 de la llave no recorre la escalera"
f = Fake([Resp({"error": {"message": "User location is not supported for the API use."}}, 400)])
falla(lambda: ia.ask_json("x", f), ia.IAError)
assert len(f.calls) == 1, "un 400 de region tampoco"
aislar()
f = Fake([SIN_CERO, Resp(gem('{"a": 1}'))])
ia.ask_json("x", f)
os.environ["GEMINI_MODEL"] = "gemini-3.5-flash-lite"
f2 = Fake([Resp(gem('{"b": 2}'))])
ia.ask_json("y", f2)
assert f2.calls[0][3]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}, "lo que funciono se recuerda por modelo, no para todos"
os.environ.pop("GEMINI_MODEL")
aislar()
# el 400 en medio de un 429: la escalera y los reintentos no se estorban
f = Fake([Resp({}, 429), SIN_CERO, Resp({}, 500), Resp(gem('{"a": 1}'))])
assert ia.ask_json("x", f) == {"a": 1} and len(f.calls) == 4
aislar()

# 4c) medicion: usageMetadata de cada respuesta, sumada por corrida y por dia en ia_uso.json con el costo estimado
f = Fake([Resp(gem('{"a": 1}', uso=(1000, 100, 50))), Resp(gem('{"a": 2}', uso=(2000, 200, 0)))])
ia.ask_json("x", f)
ia.ask_json("y", f)
usd = (3000 * 0.25 + (150 + 200) * 1.50) / 1e6                  # entrada 0.25 y salida 1.50 por millon; el razonamiento se cobra como salida
linea = ia.linea_uso()
assert "Gemini: 2 llamadas, 3000 tokens de entrada, 350 de salida (50 de razonamiento)" in linea, linea
assert f"US$ {usd:.4f}" in linea and "[gemini-3.1-flash-lite]" in linea and "de US$ 0.30" in linea, linea
dia = ia.dia_bogota()
dd = json.loads((TMP / "ia_uso.json").read_text(encoding="utf-8"))["dias"][dia]
assert (dd["llamadas"], dd["entrada"], dd["salida"], dd["razonamiento"]) == (2, 3000, 350, 50) and abs(dd["usd"] - usd) < 1e-9, dd
assert dd["modelos"]["gemini-3.1-flash-lite"]["llamadas"] == 2 and abs(ia.gasto_hoy_usd() - usd) < 1e-9
# otra corrida (proceso nuevo) en el mismo dia: la linea de la corrida empieza en cero y la del dia sigue sumando
ia.reiniciar_corrida()
assert ia.linea_uso().startswith("Gemini: 0 llamadas en esta corrida") and "hoy (" + dia + "): 2 llamadas" in ia.linea_uso()
ia.ask_json("z", Fake([Resp(gem('{"a": 3}', uso=(10, 5, 0)))]))
assert json.loads((TMP / "ia_uso.json").read_text(encoding="utf-8"))["dias"][dia]["llamadas"] == 3
# lo que no se cobra no se cuenta (429, 5xx, 400 de la escalera, 404); lo que si se cobra se cuenta aunque la respuesta no sirva
aislar()
ia.ask_json("x", Fake([Resp({}, 429), Resp({}, 500), SIN_CERO, Resp(gem('{"a": 1}', uso=(100, 10, 0)))]))
assert ia.linea_uso().startswith("Gemini: 1 llamadas, 100 tokens de entrada, 10 de salida (0 de razonamiento)"), ia.linea_uso()
falla(lambda: ia.ask_json("x", Fake([Resp(gem("esto no es json", uso=(500, 20, 400)))])), ia.IAError)
assert "2 llamadas, 600 tokens de entrada, 430 de salida (400 de razonamiento)" in ia.linea_uso(), ia.linea_uso()
# sin usageMetadata (no deberia pasar) se estima por el tamano del texto, para que el tope no quede ciego
aislar()
ia.ask_json("p" * 300, Fake([Resp(gem('{"a": 1}', uso=None))]))
assert "1 llamadas, 100 tokens de entrada, 3 de salida" in ia.linea_uso() and "1 sin usageMetadata" in ia.linea_uso(), ia.linea_uso()
# el texto con partes de "pensamiento" ignora las partes thought, y una respuesta vacia dice por que
aislar()
raro = {"candidates": [{"content": {"parts": [{"text": "pienso...", "thought": True}, {"text": '{"k": '}, {"text": "1}"}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 5, "thoughtsTokenCount": 30}}
assert ia.ask_json("x", Fake([Resp(raro)])) == {"k": 1}
vacio = {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}],
         "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 0, "thoughtsTokenCount": 2048}}
e = falla(lambda: ia.ask_json("x", Fake([Resp(vacio)])), ia.IAError)
assert "JSON" in str(e) and "MAX_TOKENS" in str(e), str(e)
assert "2 llamadas, 10 tokens de entrada, 2083 de salida (2078 de razonamiento)" in ia.linea_uso(), ia.linea_uso()   # el razonamiento de la respuesta vacia tambien se cobro
# precios: tabla por modelo, el 3.8 Flash se duplica el 2027-01-01 y un modelo desconocido se estima con el precio mas alto
assert ia.precio_modelo("gemini-3.1-flash-lite", "2026-10-19") == (0.25, 1.5, True)
assert ia.precio_modelo("gemini-3.5-flash-lite-001", "2026-10-19") == (0.30, 2.5, True)
assert ia.precio_modelo("gemini-3.8-flash", "2026-12-31") == (0.75, 3.75, True) and ia.precio_modelo("gemini-3.8-flash", "2027-01-01") == (1.50, 7.50, True)
assert ia.precio_modelo("gemini-9-inventado", "2026-10-19") == (1.5, 7.5, False)
# el archivo: ilegible = vacio (no tumba), y solo se conservan los ultimos DIAS_DE_USO dias
aislar()
(TMP / "ia_uso.json").write_text("{esto no es json", encoding="utf-8")
ia.ask_json("x", Fake([Resp(gem('{"a": 1}'))]))
assert json.loads((TMP / "ia_uso.json").read_text(encoding="utf-8"))["dias"][ia.dia_bogota()]["llamadas"] == 1
viejos = {f"2026-{m:02d}-{d:02d}": {"llamadas": 1, "usd": 0.01} for m in (6, 7, 8) for d in range(1, 29)}
(TMP / "ia_uso.json").write_text(json.dumps({"version": 1, "dias": viejos}), encoding="utf-8")
ia.ask_json("x", Fake([Resp(gem('{"a": 1}'))]))
dias = json.loads((TMP / "ia_uso.json").read_text(encoding="utf-8"))["dias"]
assert len(dias) == ia.DIAS_DE_USO and ia.dia_bogota() in dias and "2026-06-01" not in dias, (len(dias), sorted(dias)[:2])
# sin poder escribir el archivo la corrida sigue y el tope usa lo que este proceso lleva
aislar()
ia.USO_FILE = TMP / "carpeta-que-no-existe" / "ia_uso.json"
os.environ["GEMINI_MAX_USD_DIA"] = "0.001"
ia.ask_json("x", Fake([Resp(gem('{"a": 1}', uso=(2000, 400, 0)))]))
assert not (TMP / "carpeta-que-no-existe").exists() and ia.estado_presupuesto()["agotado"]
assert falla(lambda: ia.ask_json("x", Fake([])), ia.PresupuestoAgotado)
aislar()

# 4d) presupuesto diario: al llegar al tope ask_json lanza IAError ANTES de llamar; 0 = sin tope; se reinicia a la medianoche de Bogota
assert ia.presupuesto_dia_usd() == 0.30 and ia.estado_presupuesto()["agotado"] is False
for crudo, esperado in (("0.25", 0.25), ("0,05", 0.05), ("0", 0.0), ("abc", 0.30), ("-1", 0.30), ("nan", 0.30), ("inf", 0.30), ("  ", 0.30)):
    os.environ["GEMINI_MAX_USD_DIA"] = crudo
    assert ia.presupuesto_dia_usd() == esperado, (crudo, ia.presupuesto_dia_usd())
os.environ.pop("GEMINI_MAX_USD_DIA")
(TMP / "prueba.env").write_text("GEMINI_MAX_USD_DIA=0.02\n", encoding="utf-8")
ia.ENV_FILE = TMP / "prueba.env"
assert ia.presupuesto_dia_usd() == 0.02, "el tope tambien se lee de .env (asi la revision diaria ve el mismo que el radar)"
os.environ["GEMINI_MAX_USD_DIA"] = "0.5"
assert ia.presupuesto_dia_usd() == 0.5, "el entorno manda sobre .env"
aislar()
os.environ["GEMINI_MAX_USD_DIA"] = "0.001"
caro = Resp(gem('{"a": 1}', uso=(2000, 400, 0)))                  # (2000*0.25 + 400*1.5)/1e6 = 0.0011 > 0.001
f = Fake([caro, caro])
ia.ask_json("x", f)
assert ia.estado_presupuesto()["agotado"] and ia.gasto_hoy_usd() > 0.001
e = falla(lambda: ia.ask_json("x", f), ia.PresupuestoAgotado)
assert isinstance(e, ia.IAError) and "Presupuesto diario de Gemini agotado" in str(e) and "medianoche" in str(e), str(e)
assert len(f.calls) == 1, "el tope frena ANTES de llamar: la segunda pregunta no llego a Gemini"
assert "clave-falsa" not in str(e)
os.environ["GEMINI_MAX_USD_DIA"] = "0"
ia.ask_json("x", f)
assert len(f.calls) == 2 and ia.estado_presupuesto()["agotado"] is False and "(sin tope)" in ia.linea_uso(), "0 = sin tope"
# la funcion de mas arriba (colombia_plan) tambien se frena sin presupuesto
os.environ["GEMINI_MAX_USD_DIA"] = "0.001"
f = Fake([])
falla(lambda: ia.colombia_plan("x", ["t"], f), ia.PresupuestoAgotado)
assert f.calls == []
# medianoche de Bogota (UTC-5 todo el ano): a las 23:59 del 19 sigue el tope; a las 00:00 del 20 se reinicia (y el archivo guarda los dos dias)
aislar()
os.environ["GEMINI_MAX_USD_DIA"] = "0.001"
reloj("2026-10-20T04:59:00")
assert ia.dia_bogota() == "2026-10-19"
ia.ask_json("x", Fake([caro]))
falla(lambda: ia.ask_json("x", Fake([])), ia.PresupuestoAgotado)
reloj("2026-10-20T05:00:00")
assert ia.dia_bogota() == "2026-10-20" and ia.gasto_hoy_usd() == 0.0
ia.reiniciar_corrida()
assert ia.ask_json("x", Fake([caro])) == {"a": 1}
assert sorted(json.loads((TMP / "ia_uso.json").read_text(encoding="utf-8"))["dias"]) == ["2026-10-19", "2026-10-20"]
reloj("2026-10-20T00:00:00")                                        # 19:00 del 19 en Bogota: sigue siendo el dia 19, con su gasto
assert ia.dia_bogota() == "2026-10-19" and ia.gasto_hoy_usd() > 0.001
aislar()

# 4e) red: un tiempo agotado o una conexion cortada con Gemini se trata como caida (reintenta) y nunca escapa como excepcion de requests
f = Fake([requests.exceptions.ReadTimeout("t"), requests.exceptions.ConnectionError("c"), Resp(gem('{"a": 1}'))])
assert ia.ask_json("x", f) == {"a": 1} and len(f.calls) == 3
e = falla(lambda: ia.ask_json("x", Fake([requests.exceptions.ReadTimeout("t")] * 4)), ia.IAError)
assert "no respondio" in str(e)
# cuota DIARIA agotada (nivel gratis): reintentar no sirve; un 429 por minuto si se reintenta
cuota = {"error": {"message": "You exceeded your current quota", "details": [{"violations": [
    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
f = Fake([Resp(cuota, 429)])
assert "cuota diaria" in str(falla(lambda: ia.ask_json("x", f), ia.IAError)) and len(f.calls) == 1
minuto = {"error": {"message": "quota", "details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}
assert ia.ask_json("x", Fake([Resp(minuto, 429), Resp(gem('{"a": 1}'))])) == {"a": 1}

# 4f) cada funcion pide solo lo que necesita: JSON cortos
aislar()
for fn, args, esperado in ((ia.colombia_plan, ("n", ["t"]), 512), (ia.producto_digital, ("h", ["t"], "p"), 256),
                           (ia.broaden, ({"producto_es": "x"}, ["a"]), 256), (ia.same_offer, ("x", [{"nombre": "A", "textos": ["a"]}]), 256)):
    f = Fake([Resp(gem('{"consultas_co": ["a b"], "tipo": "otro", "consultas_amplias": [], "misma_oferta": []}'))])
    fn(*args, f)
    assert f.calls[0][3]["generationConfig"]["maxOutputTokens"] == esperado, (fn.__name__, f.calls[0][3]["generationConfig"])
aislar()

# 3) limite de uso (429) y caida (500): reintenta y se recupera
assert ia.ask_json("x", Fake([Resp({}, 429), Resp({}, 500), Resp(gem('{"z": 3}'))])) == {"z": 3}
# 4) clave invalida -> error legible; no es JSON -> error; nunca sale la clave
try:
    ia.ask_json("x", Fake([Resp({"error": {"message": "API key not valid"}}, 403)]))
    raise AssertionError("debio fallar")
except ia.IAError as e:
    assert "403" in str(e) and "clave-falsa" not in str(e)
try:
    ia.ask_json("x", Fake([Resp(gem("esto no es json"))]))
    raise AssertionError("debio fallar")
except ia.IAError as e:
    assert "JSON" in str(e)
try:
    ia.ask_json("x", Fake([Resp({}, 429)] * 4))
    raise AssertionError("debio fallar")
except ia.IAError as e:
    assert "limite" in str(e)

# 4g) un modelo chico a veces contesta una lista o un texto donde se pidio un objeto: IAError (que la corrida atrapa), nunca AttributeError (que la mataria)
aislar()
for fn, args in ((ia.colombia_plan, ("n", ["t"])), (ia.producto_digital, ("h", ["t"], "p")), (ia.broaden, ({"producto_es": "x"}, ["a"])),
                 (ia.same_offer, ("x", [{"nombre": "A", "textos": ["a"]}]))):
    for crudo in ("[0, 1]", '"hola"', "7", "null"):
        e = falla(lambda: fn(*args, Fake([Resp(gem(crudo))])), ia.IAError)
        assert "no es un objeto" in str(e), (fn.__name__, crudo, str(e))
# y una lista que llega como texto no se parte en letras
assert "no propuso busquedas" in str(falla(lambda: ia.colombia_plan("n", ["t"], Fake([Resp(gem('{"consultas_co": "moldes de bordado", "producto_es": "x"}'))])), ia.IAError))
assert ia.broaden({"producto_es": "x"}, ["a"], Fake([Resp(gem('{"consultas_amplias": "bordado", "sustantivo": "bordado"}'))])) == {"consultas_amplias": [], "sustantivo": "bordado"}
assert ia.same_offer("x", [{"nombre": "A", "textos": ["a"]}], Fake([Resp(gem('{"misma_oferta": "0"}'))])) == []
aislar()

# 4h) texto alrededor del JSON (06/11/2026: el semaforo fallo 3 veces con «no es JSON valido»): vale el bloque del primer { o [ al ultimo } o ]
assert ia.ask_json("x", Fake([Resp(gem('Aqui esta tu respuesta: {"a": 1} espero que sirva'))])) == {"a": 1}
assert ia.ask_json("x", Fake([Resp(gem('```json\n[{"a": 2}]\n```'))])) == [{"a": 2}]
e = falla(lambda: ia.ask_json("x", Fake([Resp(gem("no se que decirte"))])), ia.IAError)
assert "no es JSON valido" in str(e) and "STOP" in str(e) and "no se que decirte" in str(e), str(e)
# cortado por el tope de tokens: UN reintento con el doble de tokens
f = Fake([Resp(gem('{"a": "texto largo cort', finish="MAX_TOKENS")), Resp(gem('{"a": "completo"}'))])
assert ia.ask_json("x", f, max_tokens=100) == {"a": "completo"}
topes = [c[3]["generationConfig"]["maxOutputTokens"] for c in f.calls if c[0] == "POST"]
assert len(topes) == 2 and topes[1] - topes[0] == 100, topes
f = Fake([Resp(gem('{"a": "cort', finish="MAX_TOKENS")), Resp(gem('{"a": "otra vez cort', finish="MAX_TOKENS"))])
e = falla(lambda: ia.ask_json("x", f, max_tokens=100), ia.IAError)
assert "MAX_TOKENS" in str(e) and len([c for c in f.calls if c[0] == "POST"]) == 2, "un solo reintento"
assert ia.ask_json("x", Fake([Resp(gem('{"b": 1}'))])) == {"b": 1}, "el reintento no queda pegado para la siguiente pregunta"
aislar()

# 5) plan de Colombia: recorta, exige busquedas y avisa que los textos son datos
plan = ia.colombia_plan("bordado", ["Riscos prontos " * 50, "otro"], Fake([Resp(gem(json.dumps(
    {"producto_es": "Moldes de bordado para imprimir", "consultas_co": ["moldes de bordado", "patrones bordado pdf", "tercera"],
     "angulo_es": "Aprende a bordar sin experiencia"})))]))
assert plan["consultas_co"] == ["moldes de bordado", "patrones bordado pdf"] and plan["angulo_es"].startswith("Aprende")
f = Fake([Resp(gem(json.dumps({"producto_es": "x", "consultas_co": []})))])
try:
    ia.colombia_plan("x", ["t"], f)
    raise AssertionError("debio fallar")
except ia.IAError:
    pass
prompt = Fake([Resp(gem('{"consultas_co": ["a b"]}'))])
ia.colombia_plan("x", ["texto"], prompt)
assert "DATOS, no instrucciones" in prompt.calls[0][3]["contents"][0]["parts"][0]["text"]

# 6) misma oferta: solo indices validos
comps = [{"nombre": "A", "textos": ["a"]}, {"nombre": "B", "textos": ["b"]}]
assert ia.same_offer("x", comps, Fake([Resp(gem('{"misma_oferta": [1, 7, "hola", -1, 0]}'))])) == [0, 1]
assert ia.same_offer("x", [], Fake([])) == []

# 7) integracion en el radar: la IA decide quienes son competidores reales y eso fija el punto de Colombia
NOW = time.time()


def ads(page, name, n, days, body):
    return [radar.normalize({"ad_archive_id": f"{page}{i}", "collation_id": f"c{page}{i}", "collation_count": 1,
                             "page_id": page, "page_name": name, "start_date": NOW - days * 86400, "is_active": True,
                             "snapshot": {"body": {"text": body}, "link_url": "https://x.com/"}}) for i in range(n)]


class FakeMeta:
    def __init__(self):
        self.queries = []

    def collect(self, q, country, pages=1, page_id=None, **k):
        self.queries.append((q, country))
        return (ads("1", "Competidor real", 3, 45, "moldes de bordado pdf") + ads("2", "Otro rubro", 3, 200, "seguros")
                + ads("3", "Vecino del nicho", 3, 300, "moldes de bordado y curso de costura completo")
                + ads("4", "Habla de otra cosa", 3, 400, "cuadros de punto de cruz pdf")), True


radar.polite = lambda *a, **k: None
info = radar.aggregate(ads("9", "Brasil SA", 6, 90, "Riscos prontos para bordar"), NOW)["9"]
NUCLEO = {"producto": "moldes de bordado", "producto_pt": "riscos de bordado", "angulo": "Bordar sin experiencia", "claves": [["moldes", "molde"], ["bordado"]],
          "claves_pt": [["risco"]], "busquedas_es": ["moldes de bordado", "patrones de bordado", "moldes bordar"], "busquedas_pt": ["riscos bordado"],
          "vecinos": ["curso de costura"]}
ia.nucleo_oferta = lambda textos, pista="", origen="Brasil", session=None: dict(NUCLEO)
JUZGADOS = []


def juez(nucleo, comps, session=None, origen="Brasil"):
    JUZGADOS.append([c["nombre"] for c in comps])
    return [{"i": i, "vende": "moldes de bordado en pdf" if c["nombre"] == "Competidor real" else "curso de costura",
             "veredicto": "igual" if c["nombre"] == "Competidor real" else "vecino"} for i, c in enumerate(comps)]


ia.mismo_angulo = juez
meta = FakeMeta()
args = types.SimpleNamespace(pages_co=1)
cache = {}
r = radar.colombia_with_ia(meta, info, "risco para bordar PDF", args, cache)
assert r["pts"] == 1, r                           # 1 competidor que vende LO MISMO con 45 dias (>=30): 1 punto; el vecino del nicho no cuenta
assert "validado con IA" in r["txt"] and "mismo angulo estricto: 'moldes de bordado'" in r["txt"] and r["angulo_es"] == "Bordar sin experiencia", r["txt"]
assert "venden algo vecino: Vecino del nicho (curso de costura)" in r["txt"] and "4 anunciantes revisados, 2 hablan del producto" in r["txt"], r["txt"]
assert JUZGADOS[-1] == ["Vecino del nicho", "Competidor real"], "solo se juzgan los que dicen las palabras del producto (de mas a menos dias)"
assert r["anunciantes"][0]["nombre"] == "Competidor real · moldes de bordado en pdf", r["anunciantes"]     # el link dice que vende
assert r["nucleo"]["producto"] == "moldes de bordado" and radar.consultas_de_hueco(r["txt"]) == ["moldes de bordado", "patrones de bordado"], r["txt"]
assert len(meta.queries) == 2 and all(c == "CO" for _, c in meta.queries)
radar.colombia_with_ia(meta, info, "risco para bordar PDF", args, cache)
assert len(meta.queries) == 2, "las busquedas de Colombia se reutilizan (cache)"
ia.mismo_angulo = lambda nucleo, comps, session=None, origen="Brasil": [{"i": i, "vende": "x", "veredicto": "vecino"} for i in range(len(comps))]
r0 = radar.colombia_with_ia(meta, info, "x", args, cache)
assert r0["pts"] == 3 and r0["confirmado"] and r0["anunciantes"] == [], r0        # nadie vende LO MISMO -> hueco completo (hubo datos: confirmado)
# el propio anunciante (si tambien corre en Colombia) no cuenta como competidor
ia.mismo_angulo = juez
r1 = radar.colombia_with_ia(meta, dict(info, page_id="1"), "x", args, cache)
assert r1["pts"] == 3, r1
# Busquedas de Colombia SIN ningun resultado = falta de datos, no hueco: maximo 2 puntos (caso "Autentica Semijoias")
# pero antes de rendirse amplia en rondas: consultas generales y, al final, la palabra mas general del tema
class MetaVacia:
    def __init__(self):
        self.queries = []

    def collect(self, q, country, pages=1, page_id=None, **k):
        self.queries.append(q)
        return [], True


ia.mismo_angulo = lambda nucleo, comps, session=None, origen="Brasil": []
ia.broaden = lambda plan, usadas, session=None: {"consultas_amplias": ["amplia uno", "amplia dos"], "sustantivo": "bordado"}
mv = MetaVacia()
vacio = radar.colombia_with_ia(mv, info, "x", args, {})
assert vacio["pts"] == 2 and not vacio["confirmado"] and "3 ronda" in vacio["txt"], vacio
assert mv.queries == ["moldes de bordado", "patrones de bordado", "amplia uno", "amplia dos", "bordado"], mv.queries


class MetaRonda2(MetaVacia):
    def collect(self, q, country, pages=1, page_id=None, **k):
        self.queries.append(q)
        return (ads("5", "Competidor tardio", 3, 50, "bordado"), True) if q == "amplia uno" else ([], True)


m2 = MetaRonda2()
r2 = radar.colombia_with_ia(m2, info, "x", args, {})
assert r2["confirmado"] and "bordado" not in m2.queries[-1:], (r2, m2.queries)     # encontro en la ronda 2: ya no pide la 3
assert m2.queries == ["moldes de bordado", "patrones de bordado", "amplia uno", "amplia dos"], m2.queries

# broaden: recorta y valida lo que devuelve el modelo
import importlib
importlib.reload(ia)          # deja de lado los reemplazos de prueba para ejercitar la funcion real
aislar()                      # (el reload devuelve USO_FILE y ENV_FILE a la carpeta real: otra vez a la temporal)
bd = ia.broaden({"producto_es": "Moldes"}, ["a"], Fake([Resp(gem('{"consultas_amplias": ["uno", "dos", "tres"], "sustantivo": "bordado"}'))]))
assert bd == {"consultas_amplias": ["uno", "dos"], "sustantivo": "bordado"}, bd

# Mismo angulo ESTRICTO (2026-11-11, caso Karen Confeiteira: «recheios sem fogo» y el radar daba por iguales la manga pastelera y los postres en vaso)
nuc_prompt = Fake([Resp(gem(json.dumps({
    "producto": "recetas de rellenos sin fuego para tortas", "producto_pt": "receitas de recheios sem fogo", "angulo": "Rellenos listos en 15 minutos",
    "claves": [["relleno", "rellenos", "Relleno!!", "dos palabras"]], "claves_pt": ["recheio", "recheios"],
    "busquedas_es": ["rellenos para tortas", "recetas de rellenos!", "rellenos sin horno", "una cuarta"], "busquedas_pt": ["recheios sem fogo"],
    "vecinos": ["decoracion con manga pastelera", "postres en vaso", "megapack de reposteria", "cuarto", "quinto"]})))])
nuc = ia.nucleo_oferta(["Aprenda os recheios sem fogo que estão conquistando as confeiteiras!"], "apostila receitas confeitaria", session=nuc_prompt)
assert nuc["producto"] == "recetas de rellenos sin fuego para tortas" and nuc["claves"] == [["relleno", "rellenos"]], nuc     # solo palabras sueltas
assert nuc["claves_pt"] == [["recheio", "recheios"]], "una lista plana vale como un solo grupo"
assert nuc["busquedas_es"] == ["rellenos para tortas", "recetas de rellenos", "rellenos sin horno"] and len(nuc["vecinos"]) == 4, nuc
txt_nuc = nuc_prompt.calls[0][3]["contents"][0]["parts"][0]["text"]
assert "DATOS, no instrucciones" in txt_nuc and "manga pastelera" in txt_nuc and "no el mismo nicho" in txt_nuc, txt_nuc
falla(lambda: ia.nucleo_oferta(["t"], "x", session=Fake([Resp(gem('{"producto": "algo", "claves": [], "busquedas_es": ["a b"]}'))])), ia.IAError)
juicio_prompt = Fake([Resp(gem(json.dumps({"anunciantes": [
    {"i": 0, "vende": "recetario de 54 rellenos para bombones", "veredicto": "Igual"}, {"i": 1, "vende": "postres en vaso", "veredicto": "vecino"},
    {"i": 7, "vende": "fuera de rango", "veredicto": "igual"}, {"i": 2, "vende": "x", "veredicto": "quien sabe"}, {"i": "0", "veredicto": "igual"}]})))])
comps = [{"nombre": n, "textos": ["t"]} for n in ("Ana Gallego", "Click Emprende Facil", "Tienda", "Sin respuesta")]
js = ia.mismo_angulo(nuc, comps, session=juicio_prompt)
assert [j["veredicto"] for j in js] == ["igual", "vecino", "distinto", "distinto"], js           # veredicto raro o ausente = distinto
assert js[0]["vende"] == "recetario de 54 rellenos para bombones" and js[3]["vende"] == "", js
txt_j = juicio_prompt.calls[0][3]["contents"][0]["parts"][0]["text"]
assert "Ante la duda entre igual y vecino, responde vecino" in txt_j and "postres en vaso" in txt_j and "60 o 150 recetas" in txt_j, txt_j
assert ia.mismo_angulo(nuc, [], session=Fake([])) == []

# Segunda barrera: Gemini decide si es un producto digital modelable (apps, marcas, servicios y fisicos fuera)
pd = ia.producto_digital("sin experiencia", ["Controle parental"], "Qustodio", Fake([Resp(gem(json.dumps(
    {"tipo": "app_software", "modelable": False, "motivo": "app de control parental"})))]))
assert pd == {"tipo": "app_software", "modelable": False, "motivo": "app de control parental"}, pd
pd = ia.producto_digital("bordado", ["Riscos"], "", Fake([Resp(gem(json.dumps(
    {"tipo": "inventado", "modelable": "true", "motivo": "x"})))]))
assert pd["tipo"] == "otro" and pd["modelable"] is False, pd           # tipo desconocido -> 'otro'; solo `true` real cuenta
pd_prompt = Fake([Resp(gem('{"tipo": "pdf_digital", "modelable": true, "motivo": "m"}'))])
assert ia.producto_digital("x", ["t"], "p", pd_prompt)["modelable"] is True
assert "DATOS, no instrucciones" in pd_prompt.calls[0][3]["contents"][0]["parts"][0]["text"]
# Lanzamientos con aulas en vivo (regla del dueno, 2026-10-14): tipo propio y NO modelable aunque regalen un ebook
pd = ia.producto_digital("bordado natalino", ["Aulas ao vivo de bordado natalino. Ganhe um ebook de brinde. Inscreva-se"], "Imersao de bordado", Fake([Resp(gem(json.dumps(
    {"tipo": "lanzamiento_en_vivo", "modelable": False, "motivo": "aulas en vivo, ebook de regalo"})))]))
assert pd == {"tipo": "lanzamiento_en_vivo", "modelable": False, "motivo": "aulas en vivo, ebook de regalo"}, pd
assert "lanzamiento_en_vivo" in ia.TIPOS
txt_pd = pd_prompt.calls[0][3]["contents"][0]["parts"][0]["text"]
assert "lanzamiento_en_vivo" in txt_pd and "LANZAMIENTO" in txt_pd and "ebook" in txt_pd and "GRABADO" in txt_pd, txt_pd      # el prompt lo pide y distingue el curso grabado

# Revision automatica de filas ya registradas con Colombia sin confirmar
class HojaFalsa:
    def __init__(self, filas):
        self.filas, self.escrito = filas, {}

    def headers(self, tab):
        return ["Anunciante", "Link del anuncio", "Hueco Colombia", "Nota", "Justificación", "Status"]

    def read_dicts(self, tab):
        return self.filas

    def update_cells(self, tab, cells):
        self.escrito.update(cells)


def fila(n, ad, nota, status="nuevo", just="[radar.py] 2+2+1+1+1=7. algo"):
    return {"_row": n, "Anunciante": f"A{n}", "Link del anuncio": f"https://www.facebook.com/ads/library/?id={ad}",
            "Hueco Colombia": "0 sin resultados", "Nota": str(nota), "Justificación": just, "Status": status}


ctx = lambda base: {"name": "Oferta", "hint": "bordado", "textos": ["t"], "base": base}
st = {"revision": {"111": ctx(5), "222": ctx(5), "333": ctx(5), "444": ctx(5), "555": ctx(5)}}
hoja = HojaFalsa([fila(2, "111", 7), fila(3, "222", 7, status="ganadora"), fila(4, "333", 7, just="la escribio una persona"),
                  fila(5, "999", 7), fila(6, "555", 7, status="testiando")])
radar.colombia_with_ia = lambda meta, info, hint, args, cache, textos=None, **k: {"pts": 0, "txt": "6 anunciantes", "confirmado": True,
                                                                              "angulo_es": "", "producto_es": ""}
a2 = types.SimpleNamespace(revisar_max=3, sin_ia=False, simular_sheets=False, min_nota=7, pages_co=1)
assert radar.revisar_pendientes(hoja, None, st, a2, "12/10/2026") == 1
assert hoja.escrito["D2"] == 5 and hoja.escrito["F2"] == "no testeada" and "6 anunciantes" in hoja.escrito["C2"], hoja.escrito
assert "nota 7 -> 5" in hoja.escrito["E2"] and hoja.escrito["E2"].startswith("[radar.py]"), hoja.escrito["E2"]   # conserva lo anterior
assert not any(k.endswith(("3", "4", "5", "6")) for k in hoja.escrito), "no toca la ya decidida por ti, ni la que no es del radar, ni la sin contexto, ni la que ya se esta probando ('testiando')"
assert st["revision"]["222"]["revisada"] and st["revision"]["333"]["revisada"] and st["revision"]["555"]["revisada"] and "revisada" in st["revision"]["111"]
assert radar.revisar_pendientes(hoja, None, st, a2, "12/10/2026") == 0          # no repite lo ya revisado
radar.colombia_with_ia = lambda *a, **k: {"pts": 3, "txt": "0 anunciantes", "confirmado": True, "angulo_es": "", "producto_es": ""}
st2, hoja2 = {"revision": {"111": ctx(5)}}, HojaFalsa([fila(2, "111", 7)])
radar.revisar_pendientes(hoja2, None, st2, a2, "12/10/2026")
assert hoja2.escrito["D2"] == 8 and "F2" not in hoja2.escrito, hoja2.escrito       # nota sube a 8 y sigue 'nuevo'
# Sin presupuesto de Gemini la revision automatica se detiene en la primera fila (ninguna otra podria revisarse hoy) y no toca la planilla
intentos = []


def sin_presupuesto(*a, **k):
    intentos.append(1)
    raise ia.PresupuestoAgotado("Presupuesto diario de Gemini agotado (de prueba)")


radar.colombia_with_ia = sin_presupuesto
st3, hoja3 = {"revision": {"111": ctx(5), "222": ctx(5)}}, HojaFalsa([fila(2, "111", 7), fila(3, "222", 7)])
assert radar.revisar_pendientes(hoja3, None, st3, a2, "12/10/2026") == 0 and len(intentos) == 1 and not hoja3.escrito, (intentos, hoja3.escrito)
assert not st3["revision"]["111"].get("revisada") and not st3["revision"]["222"].get("revisada"), "se revisan cuando el presupuesto se reabra"
print("TODO OK (modulo de IA, API de Gemini simulada)")
