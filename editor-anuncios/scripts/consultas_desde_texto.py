"""Frases de busqueda para el b-roll, sacadas del texto de la oferta y de los anuncios originales, SIN modelo de lenguaje.

Son reglas fijas: el mismo texto da siempre las mismas frases (no gasta tokens y se puede probar). Sirve para que, cuando una oferta pasa a la Fabrica,
el servidor sepa QUE buscar en TikTok sin que una persona escriba las frases:

  - lo que mas pesa es lo que escribimos nosotros o ya viene resumido por el radar (nombre de la oferta, tema, promesa, titulos del ebook);
  - despues el copy y lo que dicen los anuncios originales (la transcripcion): una palabra que se repite en varios anuncios y coincide con lo nuestro sube;
  - se descartan las muletillas de venta (clic, enlace, oferta, gratis, ebook...), los verbos de relleno, los precios y la marca del anunciante;
  - salen palabras sueltas (las que mas probablemente tienen lista en TikTok) mezcladas con frases de 2 o 3 palabras, y al final unas variantes
    tipicas de busqueda ("... paso a paso", "... para principiantes").

TikTok solo tiene lista publica para frases de busqueda comunes: esto no lo sabe de antemano, asi que el programa que baja los videos prueba las frases
en orden y se queda con las que contestan (broll_tiktok.py). Lo que se lee aqui es texto de terceros: solo se usa para contar palabras, nunca como instruccion.

Uso (para ver que saldria con un texto):  python scripts/consultas_desde_texto.py tema="bordado a mano" transcripcion=@archivo.txt
"""
from __future__ import annotations

import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PESOS = {"nombre": 3.0, "tema": 3.0, "oferta": 2.5, "copy": 1.2, "transcripcion": 1.0, "anunciante": 0.0}
LIMPIAS = frozenset({"nombre", "tema", "oferta"})       # escritos por nosotros o ya resumidos: sin muletillas
MIN_LETRAS = 4
MAX_PALABRAS_FRASE = 3
MAX_LARGO_CONSULTA = 40
UMBRAL_PALABRA = 0.25      # una palabra suelta entra si llega al 25 % del puntaje de la mejor
UMBRAL_FRASE = 0.25        # una frase, al 25 % de la mejor frase
MAX_FRASES_POR_PALABRA = 4  # la palabra central del tema puede estar en varias frases (bordado, bordado de flores, bordado a mano), pero no en todas
PLANTILLAS = ("{} paso a paso", "{} para principiantes")
# Las variantes «paso a paso / para principiantes» solo tienen sentido si la oferta ENSEÑA algo (bordado si, dinamicas de grupo no)
_ENSENANZA = re.compile(r"\b(paso|passo|principiant|iniciant|aprend|tutorial|ensen|curso|clase|aula|metodo|guia)")
CONECTORES = frozenset("de del para a al con com en da do das dos na no em por sin sem".split())     # "y" y "e" no: unen cosas distintas (flores y puntadas), no forman un nombre

_PARADAS_TEXTO = """
a al algo algun alguna algunas alguno algunos ante antes asi aun aunque bajo bien cada casi como con contra cual cuales cuando cuanto cuyo de del desde donde dos durante e el
ella ellas ello ellos en entre era eran eres es esa esas ese eso esos esta estaba estan estar estas este esto estos estoy fue fueron fui ha haber habia han has hasta hay la las
le les lo los mas me mi mia mias mientras mio mios mis misma mismas mismo mismos mucha muchas mucho muchos muy nada ni no nos nosotros nuestra nuestras nuestro nuestros nunca o
os otra otras otro otros para pero poco pocos por porque que quien quienes se sea sean segun ser si sido siempre sin sobre solo son soy su sus tal tambien tan tanto te tenemos
tener tengo ti tiene tienen todas todavia todo todos tras tu tus un una unas uno unos usted ustedes va vamos van varias varios vas veces vez voy vosotros vuestra vuestro y ya yo
ainda ao aos apenas as ate bem com da das dela dele deles depois dos ela elas ele eles entao essa essas esse esses estao estava estes eu foi foram isso isto ja mais mas meu
meus minha minhas muito na nas nao nem nossa nossas nosso nossos num numa pela pelas pelo pelos pra qual quando quem sera seu seus sua suas tem temos tenho ter teu tua tudo
um uma umas uns vai voce voces vos
the and you your for with this that are from have has was were what when where which will would can could about into out our their they them she her his him not but all any
one two more most some such only own same than too very just also how why who
hola ola oi amigas amigos amiga amigo mira mire olha escucha escuta gracias obrigada obrigado aqui ahi alli ali agora ahora hoy hoje manana amanha ayer ontem primera primero
primeiro primer segundo tercero ultimo ultima nuevo nueva novo nova mejor melhor peor pior grande pequeno gran facil rapido rapida rapidamente simple simples increible incrivel
gratis gratuito gratuita descuento desconto oferta ofertas promocion promocao precio preco barato barata compra comprar comprarlo compre clic clique click link enlace enlaces
botao boton whatsapp instagram facebook tiktok youtube video videos ebook ebooks libro libros livro livros pdf guia guias curso cursos clase clases aula aulas metodo secreto
segredo truco trucos truque truques tips dicas consejos conselhos aprende aprender aprenda aprendas descubre descubrir descubra conoce conoces conheca sabias sabes sabia
imagina imagine piensa quieres quiere necesitas necesita precisa quer puedes puede podes pode debes debe tienes tenes hacer hacerlo hace haces hago fazer faz fazendo pasar pasa
quedar queda seguir sigue venir viene ver veo vemos dar dame decir dice dices dijo falar fala digo saber sabe sabemos poner pone llegar llega buscar busca encontrar encuentra
llamar llama tomar toma dejar deja parecer parece pensar creer cree creo usar usa uso utilizar utiliza unete suscribete compartir comparte comenta comente cuenta cuentan conte
conta historia hora dia dias semana semanas mes meses ano anos tiempo momento forma manera modo cosa cosas coisa coisas parte persona personas gente pessoas caso punto nivel
total ejemplo exemplo tipo tipos principiante principiantes iniciante iniciantes basico basica basicos basicas avanzado avanzada paso pasos passo passos lista listas minutos segundos hoy ahora ganar gane ganhe receba recibe recibir acceso acesso inmediato imediato
descarga descargar baixe baixar completo completa completos completas incluye incluido incluida ademas alem bonus bono bonos regalo presente solamente apenas unicamente
garantia garantizado garantido resultado resultados cliente clientes pedido pedidos pago pagar pagas pague pagina paginas sitio tienda loja visite visita entra entre ingresa
exclusivo exclusiva exclusivos exclusivas especial especiales limitado limitada limitados limitadas disponible disponibles oportunidad aprovecha aproveche aproveita unico unica
unicos unicas
professora professor professores prof profesora profesor profesores maestra maestro mestre mestra doutora doutor senhora senhor senora tia tio dona galera pessoal queridas queridos
meninas chicas chicos gente conosco comigo contigo consigo vitalicio vitalicia certificado certificados conclusao videoaula videoaulas aula suporte soporte duvidas dudas
converse conversa chame chama chamar chamo tocando toque abaixo debajo abajo explicado explicadinhas explicar explicadas jeito certo errado errada maneira mentira verdade dom
magica turma vaga vagas zero ritmo leve tempo titulo descripcion boton recebe receber recebo ganha comecar comece terminar termine tenham tenha adiando adiar chance difícil
dificil complicam complicado acessivel funcione funcionar devagar ensina ensinam ensinar envia enviar fale falar faca fazem fez ficar fique fica gostar junto lado lhe lindo linda
lindos lindas maior mal mao medo muita muitas nessa nesse nome novas novos onde pagar paga pessoa pode podem poder pois pouco poucos primeira quase quanta quanto quero real sabe
sao seja serve sim sonho tanto tipo toda treinar turma usar vem vida vou pronto prontos pronta prontas mesmo mesma mesmos mesmas tecnica tecnicas
""".split()
PARADAS = frozenset(_PARADAS_TEXTO)

_SEPARADORES = re.compile(r"[\n\r.,!?;:|()\[\]{}\"'`" + "“”‘’¿¡•·" + r"/\\*_=+<>~^]+|\s[-–—]\s")
_ENLACES = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+\.\S+|@\w+", re.I)


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c))


def _raiz(t: str) -> str:
    """Quita el plural mas comun (flores -> flor, dinamicas -> dinamica) para contar juntas las dos formas."""
    if len(t) > 5 and t.endswith("es") and t[-3] in "rnld":
        return t[:-2]
    if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def limpiar_consulta(texto: str) -> str:
    """Una frase de busqueda segura: minusculas sin acentos, solo letras, numeros y espacios, hasta 5 palabras y 40 letras."""
    t = re.sub(r"[^a-z0-9 ]+", " ", sin_acentos(texto).lower())
    palabras = t.split()[:5]
    return " ".join(palabras)[:MAX_LARGO_CONSULTA].strip()


def _trozos(texto: str) -> list[list[str]]:
    """El texto como listas de palabras (sin acentos, con su mayuscula original), cortadas en cada signo de puntuacion o salto de linea."""
    t = _ENLACES.sub(" ", sin_acentos(texto or "").replace("#", " "))
    salida = []
    for trozo in _SEPARADORES.split(t):
        palabras = re.findall(r"[A-Za-z0-9]+", trozo)
        if palabras:
            salida.append(palabras)
    return salida


def segmentos(texto: str) -> list[list[str]]:
    """El texto como listas de palabras (sin acentos, en minuscula), cortadas en cada signo de puntuacion o salto de linea."""
    return [[w.lower() for w in palabras] for palabras in _trozos(texto)]


def _es_contenido(t: str) -> bool:
    return len(t) >= MIN_LETRAS and t.isalpha() and t not in PARADAS and t not in CONECTORES


def analizar(fuentes: list[dict]) -> dict:
    """Cuenta palabras y frases. `fuentes`: [{'tipo': 'tema'|'nombre'|'oferta'|'copy'|'transcripcion'|'anunciante', 'texto': '...', 'peso': opcional}]."""
    palabras: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))     # raiz -> {fuente: veces}
    tipos: dict[str, set[str]] = defaultdict(set)
    superficie: dict[str, Counter] = defaultdict(Counter)
    marca: set[str] = set()
    frases: dict[tuple, dict] = {}
    primera: dict[object, int] = {}          # palabra o frase -> en que lugar del texto se vio por primera vez
    con_mayuscula: Counter = Counter()       # veces que aparece escrita con mayuscula a MITAD de una frase (un nombre propio: «la profesora Kelly»)
    en_minuscula: Counter = Counter()        # veces que aparece escrita en minuscula
    for i, f in enumerate(fuentes):
        tipo = str(f.get("tipo") or "copy")
        if tipo not in PESOS:
            tipo = "copy"
        for escritas in _trozos(str(f.get("texto") or "")):
            seg = [w.lower() for w in escritas]
            if tipo == "anunciante":
                marca.update(_raiz(t) for t in seg if _es_contenido(t))
                continue
            # palabras sueltas
            for k, t in enumerate(seg):
                if _es_contenido(t):
                    r = _raiz(t)
                    primera.setdefault(r, len(primera))
                    palabras[r][i] += 1
                    tipos[r].add(tipo)
                    superficie[r][t] += 1
                    w = escritas[k]
                    if w[0].islower():
                        en_minuscula[r] += 1
                    elif k > 0 and len(w) > 1 and w[1:].islower():
                        con_mayuscula[r] += 1
            # frases: contenido (conector? contenido)*, hasta MAX_PALABRAS_FRASE palabras de contenido
            n = len(seg)
            for a in range(n):
                if not _es_contenido(seg[a]):
                    continue
                cuerpo, usadas, j = [seg[a]], 1, a + 1
                while j < n and usadas < MAX_PALABRAS_FRASE:
                    if _es_contenido(seg[j]):
                        cuerpo.append(seg[j]); usadas += 1; j += 1
                    elif seg[j] in CONECTORES and j + 1 < n and _es_contenido(seg[j + 1]):
                        cuerpo += [seg[j], seg[j + 1]]; usadas += 1; j += 2
                    else:
                        break
                    if usadas >= 2:
                        clave = tuple(_raiz(t) if _es_contenido(t) else t for t in cuerpo)
                        primera.setdefault(clave, len(primera))
                        d = frases.setdefault(clave, {"veces": 0, "tipos": set(), "formas": Counter(), "fuentes": set()})
                        d["veces"] += 1
                        d["tipos"].add(tipo)
                        d["formas"][" ".join(cuerpo)] += 1
                        d["fuentes"].add(i)
    propios = {r for r in con_mayuscula if not en_minuscula[r]}          # siempre con mayuscula y a mitad de frase: es un nombre (Kelly, Maria), no una cosa que se vea
    return {"palabras": palabras, "tipos": tipos, "superficie": superficie, "marca": marca, "frases": frases, "primera": primera, "propios": propios}


def _puntaje_palabra(r: str, a: dict, fuentes: list[dict]) -> float:
    s = 0.0
    for i, veces in a["palabras"][r].items():
        peso = float(fuentes[i].get("peso") or PESOS.get(str(fuentes[i].get("tipo")), 1.0))
        s += peso * (1.0 + math.log(1 + veces))
    return s * (1.0 + 0.5 * (len(a["palabras"][r]) - 1))


def _elegible(r: str, a: dict) -> bool:
    """Una palabra entra si la dicen nuestros textos limpios, o si se repite en dos fuentes distintas; y no es solo la marca del anunciante."""
    if r in a["propios"] and not (a["tipos"][r] & LIMPIAS):          # el nombre de quien presenta el anuncio no es el tema (a menos que lo hayamos escrito nosotros)
        return False
    if r in a["marca"]:                      # el nombre del proyecto suele ser la marca del anunciante: para una palabra de la marca solo cuenta lo que dicen el tema o la oferta
        return bool(a["tipos"][r] & {"tema", "oferta"})
    return bool(a["tipos"][r] & LIMPIAS) or len(a["palabras"][r]) >= 2


def candidatos(fuentes: list[dict]) -> list[dict]:
    """Todas las palabras y frases candidatas con su puntaje, de la mejor a la peor (sin recortar ni quitar repetidas)."""
    a = analizar(fuentes)
    out = []
    for r in a["palabras"]:
        if _elegible(r, a):
            forma = a["superficie"][r].most_common(1)[0][0]
            out.append({"consulta": forma, "claves": (r,), "puntaje": _puntaje_palabra(r, a, fuentes), "tipo": "palabra",
                        "fuentes": sorted(a["tipos"][r]), "orden": a["primera"][r]})
    for clave, d in a["frases"].items():
        contenido = [t for t in clave if t not in CONECTORES]
        if not all(_elegible(t, a) for t in contenido):
            continue
        if not (d["tipos"] & LIMPIAS) and len(d["fuentes"]) < 2 and d["veces"] < 2:
            continue                         # una frase que solo aparece una vez en un texto sin filtrar es casualidad
        base = sum(_puntaje_palabra(t, a, fuentes) for t in contenido)
        puntaje = base * (1.0 + 0.35 * (d["veces"] - 1)) * (1.0 + 0.15 * (len(contenido) - 1))
        out.append({"consulta": d["formas"].most_common(1)[0][0], "claves": tuple(contenido), "puntaje": puntaje, "tipo": "frase",
                    "fuentes": sorted(d["tipos"]), "orden": a["primera"][clave]})
    out.sort(key=lambda c: (-round(c["puntaje"], 6), c["orden"], c["consulta"]))
    return out


def anclas(fuentes: list[dict], maximo: int = 2) -> list[str]:
    """Las palabras que SON el tema (las dicen el tema o la promesa de la oferta, no solo los anuncios), de la mas fuerte a la menos: ['bordado']. Con ellas se arma el filtro de
    la descripcion de los videos de TikTok, para que una frase de busqueda floja no deje pasar videos de otra cosa."""
    a = analizar(fuentes)
    candidatas = [(_puntaje_palabra(r, a, fuentes), a["primera"][r], a["superficie"][r].most_common(1)[0][0]) for r in a["palabras"]
                  if (a["tipos"][r] & {"tema", "oferta"}) and _elegible(r, a)]
    candidatas.sort(key=lambda x: (-round(x[0], 6), x[1]))
    if not candidatas:
        return []
    mejor = candidatas[0][0]
    return [palabra for puntaje, _, palabra in candidatas if puntaje >= 0.5 * mejor][:max(1, int(maximo))]


def raiz_de_filtro(palabra: str) -> str:
    """Lo que se busca dentro de la descripcion de un video: la palabra recortada para que 'bordado' tambien encuentre 'bordar' y 'bordando'."""
    p = limpiar_consulta(palabra).replace(" ", "")
    return p[:5] if len(p) >= 7 else p[:4]


def ensena(fuentes: list[dict]) -> bool:
    """True si lo que escribimos nosotros (nombre, tema, oferta) o al menos dos textos de los anuncios hablan de aprender, de pasos o de principiantes."""
    propios = " ".join(sin_acentos(str(x.get("texto") or "")).lower() for x in fuentes if x.get("tipo") in LIMPIAS)
    ajenos = sum(1 for x in fuentes if x.get("tipo") not in LIMPIAS and x.get("tipo") != "anunciante" and _ENSENANZA.search(sin_acentos(str(x.get("texto") or "")).lower()))
    return bool(_ENSENANZA.search(propios)) or ajenos >= 2


def generar(fuentes: list[dict], maximo: int = 8) -> list[dict]:
    """Hasta `maximo` frases de busqueda, en el orden en que conviene probarlas: [{'consulta', 'tipo', 'puntaje', 'fuentes'}].
    Empieza con la mejor palabra suelta, la mejor frase de 2 palabras y la mejor de 3 (cuanto mas corta, mas chance de que TikTok tenga su lista), sigue con
    mas frases, deja las otras palabras sueltas para el final (una palabra sola busca de todo) y cierra con variantes tipicas de busqueda."""
    maximo = max(1, min(int(maximo), 20))
    cands = candidatos(fuentes)

    def cola(pred, umbral: float) -> list[dict]:
        lista = [c for c in cands if pred(c)]
        # lo que apenas se menciona una vez (un adjetivo suelto, "mano") no es el tema: solo pasan los que llegan a una fraccion del mejor de su clase
        return [c for c in lista if c["puntaje"] >= umbral * lista[0]["puntaje"]] if lista else []

    colas = [cola(lambda c: c["tipo"] == "palabra", UMBRAL_PALABRA),
             cola(lambda c: c["tipo"] == "frase" and len(c["claves"]) == 2, UMBRAL_FRASE),
             cola(lambda c: c["tipo"] == "frase" and len(c["claves"]) >= 3, UMBRAL_FRASE)]
    elegidas: list[dict] = []

    def cabe(c: dict) -> bool:
        palabras = set(c["claves"])
        if not limpiar_consulta(c["consulta"]):
            return False
        if len(palabras) >= 2:                                                       # entre frases: nada que ya cubra otra, ni casi la misma (dos palabras en comun)
            frases = [set(e["claves"]) for e in elegidas if len(e["claves"]) >= 2]
            if any(palabras <= otra or len(palabras & otra) >= 2 for otra in frases):
                return False
        if any(sum(1 for e in elegidas if p in e["claves"]) >= MAX_FRASES_POR_PALABRA for p in palabras):     # ni la misma palabra en todas
            return False
        return True

    organicas = max(1, maximo - min(2, maximo // 3))
    pos = [0, 0, 0]
    # primero una palabra, una frase de 2 y una de 3; despues solo frases (de 2 y de 3, alternadas); las demas palabras sueltas, al final, si sobra lugar
    turnos = [0, 1, 2] + [1, 2] * (len(colas[1]) + len(colas[2]) + 1) + [0] * (len(colas[0]) + 1)
    for q in turnos:
        if len(elegidas) >= organicas:
            break
        while pos[q] < len(colas[q]) and not cabe(colas[q][pos[q]]):
            pos[q] += 1
        if pos[q] < len(colas[q]):
            elegidas.append(colas[q][pos[q]])
            pos[q] += 1
    salida = [{"consulta": limpiar_consulta(c["consulta"]), "tipo": c["tipo"], "puntaje": round(c["puntaje"], 2), "fuentes": c["fuentes"]} for c in elegidas]
    # variantes tipicas de busqueda sobre las dos primeras (de 1 o 2 palabras), solo si la oferta ensena algo
    for c in (elegidas if ensena(fuentes) else []):
        if sum(1 for x in salida if x["tipo"] == "plantilla") >= 2 or len(salida) >= maximo:
            break
        base = limpiar_consulta(c["consulta"])
        if len(c["claves"]) > 2 or {"paso", "principiantes"} & set(base.split()):
            continue
        for plantilla in PLANTILLAS:
            q = limpiar_consulta(plantilla.format(base))
            if q and len(salida) < maximo and q not in {x["consulta"] for x in salida} and sum(1 for x in salida if x["tipo"] == "plantilla") < 2:
                salida.append({"consulta": q, "tipo": "plantilla", "puntaje": round(c["puntaje"] * 0.5, 2), "fuentes": c["fuentes"]})
    vistos, final = set(), []
    for x in salida:
        if x["consulta"] and x["consulta"] not in vistos:
            vistos.add(x["consulta"])
            final.append(x)
    return final[:maximo]


def main() -> None:
    fuentes = []
    for arg in sys.argv[1:]:
        if "=" not in arg:
            print(__doc__)
            sys.exit(2)
        tipo, _, valor = arg.partition("=")
        if valor.startswith("@"):
            valor = Path(valor[1:]).read_text(encoding="utf-8", errors="replace")
        fuentes.append({"tipo": tipo, "texto": valor})
    if not fuentes:
        print(__doc__)
        sys.exit(2)
    for s in generar(fuentes):
        print(f"  {s['consulta']:<34} {s['tipo']:<9} {s['puntaje']:>6}  ({', '.join(s['fuentes'])})")


if __name__ == "__main__":
    main()
