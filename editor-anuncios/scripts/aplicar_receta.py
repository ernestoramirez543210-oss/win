"""Aplica la receta de edicion de la Fabrica a un videoData.json: subtitulos, transiciones, efectos de sonido, producto y llamada a la accion.

La receta (anuncios/<producto>/<tipo>/<AD>/receta.json, la deja `fabrica.py traer`) dice COMO quiere el dueño que se edite esta oferta. Armar el montaje
(que toma va con que frase, los textos grandes, la voz) es trabajo de Claude; lo que se puede decidir con reglas fijas lo hace este script, siempre igual:

  - estilo.subtitulos: tamaño, colores, palabras a la vez y mayusculas (o sin subtitulos);
  - transiciones: en que cortes van, de que tipo (sin repetir la misma dos veces seguidas) y de cuanto dura cada una (campo `transicion` de cada toma);
  - efectos de sonido: en las transiciones, los textos grandes, el gancho y la llamada a la accion, solo de los permitidos, con el volumen pedido, sin pasar del maximo
    y sin amontonarse (nunca en cada subtitulo);
  - producto: portada y paginas del ebook (cuantas) o ninguna;
  - llamada a la accion: duracion y, si la receta los trae, texto y apoyo.
Y avisa (no cambia) lo que no cumple: duracion fuera del rango, cortes mas largos o cortos de lo pedido, textos grandes de mas o de menos.

Uso:
  python scripts/aplicar_receta.py <carpeta del anuncio | videoData.json> [--receta receta.json] [--dry-run]

Es idempotente: volver a correrlo da el mismo resultado (cambia solo lo que el puso antes: los efectos marcados `origen: receta`). La primera vez deja
videoData.json.antes-receta. Despues: validar_videodata.py, fotogramas y render, como siempre.
"""
from __future__ import annotations

import argparse
import copy
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import PUBLIC, configurar_consola, escribir_json, leer_json, salir  # noqa: E402

TRANSICIONES = ("fundido", "deslizar", "zoom", "flash")
SFX_VALIDOS = ("whoosh", "whoosh_corto", "pop", "pop_suave", "click", "ding", "hit", "kaching", "click_camara")
# Que efecto de sonido acompana a cada transicion (el primero permitido de la lista; si la receta no deja ninguno, esa transicion va sin sonido).
SFX_DE_TRANSICION = {
    "deslizar": ("whoosh_corto", "whoosh", "pop"),
    "zoom": ("whoosh", "whoosh_corto", "hit"),
    "flash": ("click_camara", "pop", "click"),
    "fundido": ("pop_suave", "pop", "ding"),
}
SFX_DE_TEXTO = ("pop", "pop_suave", "hit", "ding", "click")
SFX_DE_CTA = ("click", "kaching", "pop", "ding")
SEPARACION_MIN = 0.14     # dos efectos mas juntos que esto suenan como uno solo y sucio
PAUSA_IDEA = 0.35         # una pausa de la voz asi de larga separa una idea de la siguiente


def val(receta: dict, ruta: str, defecto):
    cur = receta
    for p in ruta.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return defecto
        cur = cur[p]
    return cur


# ------------------------------------------------------------------ transiciones
def candidatos_cambio_de_idea(datos: dict) -> list[int]:
    """Indices de toma (>=1) que arrancan en un cambio de idea: tras una pausa de la voz, tras un punto, o junto a un texto grande."""
    palabras = datos.get("palabras") or []
    tomas = datos["tomas"]
    textos = [float(t["inicio"]) for t in datos.get("textosGrandes") or []]
    out = []
    for i, t in enumerate(tomas):
        if i == 0 or t.get("continuacion"):
            continue
        ini = float(t["inicio"])
        anterior = [p for p in palabras if float(p["fin"]) <= ini + 0.08]
        siguiente = [p for p in palabras if float(p["inicio"]) >= ini - 0.08]
        fin_frase = bool(anterior) and str(anterior[-1]["texto"]).rstrip().endswith((".", "?", "!", "…", ":")) and ini - float(anterior[-1]["fin"]) < 0.6
        pausa = bool(anterior) and bool(siguiente) and float(siguiente[0]["inicio"]) - float(anterior[-1]["fin"]) >= PAUSA_IDEA
        con_texto = any(abs(ini - x) <= 0.3 for x in textos)
        if fin_frase or pausa or con_texto:
            out.append(i)
    return out


def elegir_cortes(datos: dict, receta: dict) -> list[int]:
    n = len(datos["tomas"])
    if n < 2 or not val(receta, "transiciones.activas", True):
        return []
    modo = val(receta, "transiciones.frecuencia", "cambio-de-idea")
    todos = [i for i in range(1, n) if not datos["tomas"][i].get("continuacion")]        # un tramo seguido del mismo clip (continuacion) no es un corte: sin transicion
    if modo == "cada-corte":
        return todos
    cand = candidatos_cambio_de_idea(datos)
    if modo == "pocas":
        tope = max(1, math.ceil(n / 5))
        base = cand if cand else todos
        if len(base) <= tope:
            return base
        paso = len(base) / tope
        return sorted({base[int(k * paso)] for k in range(tope)})
    return cand if cand else [i for i in todos if i % 3 == 0]       # sin pistas en la voz: uno de cada tres cortes


def aplicar_transiciones(datos: dict, receta: dict, avisos: list[str]) -> list[dict]:
    """Pone `transicion` en las tomas elegidas (y la quita de las demas). Devuelve [{i, tipo, t, duracion}]."""
    tomas = sorted(datos["tomas"], key=lambda t: float(t["inicio"]))
    datos["tomas"] = tomas
    for t in tomas:
        t.pop("transicion", None)
    tipos = [x for x in (val(receta, "transiciones.tipos", []) or []) if x in TRANSICIONES]
    cortes = elegir_cortes(datos, receta)
    if not cortes:
        return []
    if not tipos:
        avisos.append("La receta pide transiciones pero no deja ningun tipo permitido: no se puso ninguna.")
        return []
    dur_pedida = int(val(receta, "transiciones.duracionMs", 250)) / 1000.0
    puestas, anterior, k = [], None, 0
    for i in cortes:
        # el tipo siguiente de la lista, saltando el que acaba de usarse (si hay mas de uno)
        tipo = tipos[k % len(tipos)]
        if tipo == anterior and len(tipos) > 1:
            k += 1
            tipo = tipos[k % len(tipos)]
        k += 1
        anterior = tipo
        toma = tomas[i]
        previa = tomas[i - 1]
        # la transicion no puede durar mas que la mitad de la toma que entra ni de la que sale
        dur = round(max(0.1, min(dur_pedida, float(toma["duracion"]) / 2, float(previa["duracion"]) / 2)), 3)
        toma["transicion"] = {"tipo": tipo, "duracion": dur}
        puestas.append({"i": i, "tipo": tipo, "t": float(toma["inicio"]), "duracion": dur})
    return puestas


# ------------------------------------------------------------------ efectos de sonido
def elegir_sfx(preferidos: tuple[str, ...], permitidos: list[str]) -> str | None:
    for s in preferidos:
        if s in permitidos:
            return s
    return None


def aplicar_sonidos(datos: dict, receta: dict, transiciones: list[dict], avisos: list[str]) -> dict:
    audio = datos.setdefault("audio", {})
    previos = audio.get("sfx") or []
    resto = [e for e in previos if not (isinstance(e, dict) and e.get("origen") == "receta")]      # los que puso Claude a mano se respetan; los de la receta se rehacen
    if not val(receta, "sonidos.activos", True):
        audio["sfx"] = resto
        return {"puestos": 0, "descartados": 0}
    permitidos = [x for x in (val(receta, "sonidos.lista", []) or []) if x in SFX_VALIDOS]
    volumen = float(val(receta, "sonidos.volumen", 0.6))
    maximo = int(val(receta, "sonidos.maximo", 14))
    dur_total = float(datos["meta"]["duracion"])
    candidatos: list[tuple[int, float, str]] = []         # (prioridad: menor = mas importante, tiempo, efecto)

    modo = val(receta, "gancho.modo", "nuevo-modelado")
    golpe = val(receta, "gancho.sonido", "hit")
    if modo != "sin-gancho" and golpe in SFX_VALIDOS and golpe != "ninguno":
        candidatos.append((0, 0.1, golpe))
    cta = datos.get("cta")
    if cta and val(receta, "sonidos.enCta", True):
        s = elegir_sfx(SFX_DE_CTA, permitidos)
        if s:
            candidatos.append((1, float(cta["inicio"]), s))
    if val(receta, "sonidos.enTextosGrandes", True):
        s = elegir_sfx(SFX_DE_TEXTO, permitidos)
        if s:
            for t in datos.get("textosGrandes") or []:
                candidatos.append((2, float(t["inicio"]), s))
    if val(receta, "sonidos.enTransiciones", True):
        for tr in transiciones:
            s = elegir_sfx(SFX_DE_TRANSICION[tr["tipo"]], permitidos)
            if s:
                candidatos.append((3, max(0.0, tr["t"] - tr["duracion"] / 2), s))
    if not permitidos and not any(p == 0 for p, _, _ in candidatos):
        avisos.append("La receta pide efectos de sonido pero no deja ninguno permitido: no se puso ninguno (salvo el golpe del gancho si lo pide).")

    # sin amontonarse: de los que chocan se queda el mas importante; y sin pasar del maximo
    candidatos.sort(key=lambda c: (c[0], c[1]))
    elegidos: list[tuple[int, float, str]] = []
    descartados = 0
    for c in candidatos:
        if c[1] > dur_total - 0.05:
            descartados += 1
            continue
        if any(abs(c[1] - e[1]) < SEPARACION_MIN for e in elegidos) or len(elegidos) >= maximo:
            descartados += 1
            continue
        elegidos.append(c)
    elegidos.sort(key=lambda c: c[1])
    nuevos = []
    for _, t, nombre in elegidos:
        ruta = f"sfx/{nombre}.mp3"
        if not (PUBLIC / ruta).is_file():
            avisos.append(f"Falta {ruta} en public/sfx (corre python scripts/crear_sfx.py): ese efecto se salto.")
            continue
        nuevos.append({"src": ruta, "t": round(t, 3), "volumen": volumen, "origen": "receta"})
    audio["sfx"] = sorted(resto + nuevos, key=lambda e: float(e["t"]))
    if descartados:
        avisos.append(f"{descartados} efecto(s) de sonido no entraron (se amontonaban o pasaban del maximo de {maximo}).")
    return {"puestos": len(nuevos), "descartados": descartados}


# ------------------------------------------------------------------ resto
def aplicar_subtitulos(datos: dict, receta: dict) -> None:
    s = receta.get("subtitulos") or {}
    estilo = datos.setdefault("estilo", {})
    sub = estilo.setdefault("subtitulos", {})
    sub.update({
        "tamano": int(s.get("tamano", 82)), "color": s.get("color", "#FFFFFF"), "colorActivo": s.get("colorActivo", "#FFE14D"),
        "maxPalabras": int(s.get("palabrasPorVez", 3)), "mayusculas": bool(s.get("mayusculas", True)),
    })
    sub.setdefault("contorno", "#000000")
    activos = bool(s.get("activos", True))
    guardadas = datos.get("_palabras")
    if not activos and datos.get("palabras"):
        datos["_palabras"] = datos["palabras"]          # las guarda aparte: volver a pedir subtitulos las recupera
        datos["palabras"] = []
    elif activos and guardadas and not datos.get("palabras"):
        datos["palabras"] = guardadas
        datos.pop("_palabras", None)


def aplicar_producto(datos: dict, receta: dict, avisos: list[str]) -> None:
    if not val(receta, "producto.mostrar", True):
        datos["producto"] = []
        return
    limite = 1 + int(val(receta, "producto.paginas", 2))
    prod = datos.get("producto") or []
    if len(prod) > limite:
        avisos.append(f"El producto sale {len(prod)} veces y la receta pide portada + {limite - 1} pagina(s): se dejaron las primeras {limite}.")
        datos["producto"] = sorted(prod, key=lambda x: float(x["inicio"]))[:limite]
    elif not prod:
        avisos.append("La receta pide mostrar el producto (portada del ebook) y el anuncio no la muestra en ningun momento.")


def aplicar_cta(datos: dict, receta: dict, avisos: list[str]) -> None:
    cta = datos.get("cta")
    if not cta:
        avisos.append("No hay llamada a la accion final (la receta pide una).")
        return
    dur = float(val(receta, "cta.segundos", 2.2))
    cta["duracion"] = dur
    if cta["inicio"] + dur > float(datos["meta"]["duracion"]) + 0.05:
        cta["inicio"] = round(float(datos["meta"]["duracion"]) - dur, 3)
    if val(receta, "cta.texto", ""):
        cta["texto"] = val(receta, "cta.texto", "")
    if val(receta, "cta.subtexto", ""):
        cta["subtexto"] = val(receta, "cta.subtexto", "")


def revisar(datos: dict, receta: dict, avisos: list[str]) -> None:
    dur = float(datos["meta"]["duracion"])
    mn, mx = float(val(receta, "duracionMin", 25)), float(val(receta, "duracionMax", 60))
    if dur < mn - 0.5 or dur > mx + 0.5:
        avisos.append(f"El anuncio dura {dur:.1f} s y la receta pide entre {mn:.0f} y {mx:.0f} s.")
    corte_mn, corte_mx = float(val(receta, "ritmo.corteMin", 1)), float(val(receta, "ritmo.corteMax", 3))
    largos = [i + 1 for i, t in enumerate(datos["tomas"]) if float(t["duracion"]) > corte_mx * 1.25]
    cortos = [i + 1 for i, t in enumerate(datos["tomas"]) if float(t["duracion"]) < corte_mn * 0.75]
    if largos:
        avisos.append(f"Tomas mas largas de lo que pide la receta ({corte_mx:g} s): {', '.join(map(str, largos[:8]))}.")
    if cortos:
        avisos.append(f"Tomas mas cortas de lo que pide la receta ({corte_mn:g} s): {', '.join(map(str, cortos[:8]))}.")
    n_txt = len(datos.get("textosGrandes") or [])
    tmn, tmx = int(val(receta, "textosGrandes.min", 5)), int(val(receta, "textosGrandes.max", 7))
    if n_txt < tmn or n_txt > tmx:
        avisos.append(f"Hay {n_txt} textos grandes y la receta pide de {tmn} a {tmx}.")
    if val(receta, "gancho.modo", "nuevo-modelado") == "nuevo-modelado":
        seg = float(val(receta, "gancho.segundos", 6))
        if not any(float(t["inicio"]) < min(seg, 3.0) for t in datos.get("textosGrandes") or []) and val(receta, "gancho.textoGrande", True):
            avisos.append("La receta pide la frase del gancho en texto grande pero ningun texto grande aparece en los primeros segundos.")


def aplicar(datos: dict, receta: dict) -> tuple[dict, list[str], dict]:
    """Devuelve (videoData nuevo, avisos, resumen). No toca disco: se puede probar sin archivos."""
    d = copy.deepcopy(datos)
    if not d.get("tomas"):
        raise ValueError("El videoData no tiene tomas.")
    avisos: list[str] = []
    aplicar_subtitulos(d, receta)
    trans = aplicar_transiciones(d, receta, avisos)
    sonidos = aplicar_sonidos(d, receta, trans, avisos)
    aplicar_producto(d, receta, avisos)
    aplicar_cta(d, receta, avisos)
    revisar(d, receta, avisos)
    resumen = {"transiciones": len(trans), "tipos": sorted({t["tipo"] for t in trans}), **sonidos}
    return d, avisos, resumen


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("destino", help="carpeta del anuncio (con videoData.json y receta.json) o la ruta del videoData.json")
    ap.add_argument("--receta", help="otra receta.json (por defecto la de la carpeta del anuncio)")
    ap.add_argument("--dry-run", action="store_true", help="muestra lo que haria sin escribir nada")
    a = ap.parse_args()

    destino = Path(a.destino)
    ruta_vd = destino / "videoData.json" if destino.is_dir() else destino
    carpeta = ruta_vd.parent
    ruta_receta = Path(a.receta) if a.receta else carpeta / "receta.json"
    if not ruta_vd.is_file():
        salir(f"No existe {ruta_vd}. Arma primero el videoData.json del anuncio.")
    if not ruta_receta.is_file():
        salir(f"No existe {ruta_receta}. Baja el pedido con `python scripts/fabrica.py traer` (deja receta.json en la carpeta del anuncio).")
    try:
        nuevo, avisos, resumen = aplicar(leer_json(ruta_vd), leer_json(ruta_receta))
    except (ValueError, KeyError) as e:
        salir(f"No se pudo aplicar la receta: {e}")

    print(f"Receta aplicada a {ruta_vd.name}: {resumen['transiciones']} transicion(es) {resumen['tipos'] or ''}, {resumen['puestos']} efecto(s) de sonido.")
    for av in avisos:
        print("  AVISO:", av)
    if a.dry_run:
        print("(dry-run: no se escribio nada)")
        return
    respaldo = ruta_vd.with_name(ruta_vd.name + ".antes-receta")
    if not respaldo.exists():
        shutil.copy2(ruta_vd, respaldo)
    escribir_json(ruta_vd, nuevo)
    print(f"Listo. Respaldo en {respaldo.name}. Siguiente: python scripts/validar_videodata.py {ruta_vd}")


if __name__ == "__main__":
    main()
