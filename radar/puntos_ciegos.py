#!/usr/bin/env python3
"""
puntos_ciegos.py - Detector DETERMINISTA de puntos ciegos de la operacion de ofertas.

Lee la planilla RADAR OFERTAS (en vivo si ya autorizaste el acceso; si no, los CSV
planilla_ofertas.csv / planilla_palabras.csv), el estado del radar y, si existe, las ventas
reales del dash. Imprime ~2 KB: hallazgos por severidad + datos para cruzar. Sin IA, sin Meta.

    python puntos_ciegos.py
    python puntos_ciegos.py --dash "..\\data" --sin-sheets
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests

import radar
import sheets

HERE = Path(os.environ.get("RADAR_DATOS") or Path(__file__).resolve().parent)   # carpeta de los DATOS (en el VPS /opt/radar-de-ofertas; el codigo vive aparte)
DEFAULT_DASH = Path(__file__).resolve().parent.parent / "data"
SEV = {"ALTO": 0, "MEDIO": 1, "BAJO": 2}
RESULT_COLS = ("resultado", "ventas", "roas", "probada", "testeada", "gasto", "cpa")


def read_csv(name):
    p = HERE / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load(args):
    """(ofertas, palabras, nichos, fuente)"""
    if not args.sin_sheets and sheets.available():
        try:
            sh = sheets.Sheet()
            of, kw = sh.read_dicts("OFERTAS"), sh.read_dicts("PALABRAS-CLAVE")
            try:
                ni = sh.read_dicts("NICHOS")
            except sheets.SheetsError:
                ni = []
            return of, kw, ni, "planilla en vivo"
        except (sheets.SheetsError, requests.RequestException) as e:
            print(f"(no pude leer la planilla en vivo: {e}; uso los CSV locales)")
    return read_csv("planilla_ofertas.csv"), read_csv("planilla_palabras.csv"), [], "CSV locales"


def num(s):
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else None


def status(r):
    return (r.get("Status") or "").strip().lower()


def row_date(r):
    iso = radar._iso_date(r.get("Fecha"))
    return datetime.fromisoformat(iso) if iso else None


def niche_of(r):
    return radar.fold((r.get("Nicho") or "").split("/")[0]).strip() or "(sin nicho)"


def check_all(of, kw, ni, state, now):
    out = []                                   # (severidad, titulo, detalle)
    n = len(of)
    st = Counter(status(r) for r in of)

    # 1) Bucle de aprendizaje
    marked = st["ganadora"] + st["descartada"]
    old_new = [r for r in of if status(r) == "nuevo" and row_date(r) and (now - row_date(r)).days >= 14]
    if n and marked == 0:
        out.append(("ALTO", "El aprendizaje esta ciego",
                    f"{n} ofertas y 0 marcadas ganadora/descartada. El prompt y el radar priorizan palabras y nichos "
                    "por ese Status; sin el, todo se ve igual. Marca el resultado real de lo que ya probaste."))
    elif old_new:
        out.append(("MEDIO", "Ofertas 'nuevo' sin decision", f"{len(old_new)} llevan 14+ dias en 'nuevo'. Decide: vigilar, ganadora o descartada."))

    # 1b) Ofertas que decidiste no modelar: el motivo dice que filtro le falta al radar
    nt = [r for r in of if status(r) == "no testeada"]
    if len(nt) >= 3:
        motivos = Counter()
        for r in nt:
            m = re.search(r"no testeada:(.*)", radar.fold(r.get("Justificación") or ""))
            # el motivo que el equipo escribe en el dash (columna «Motivo del estado», desde el 2026-11-04) manda sobre el de la Justificacion
            txt = radar.fold(r.get(radar.dash_estados.MOTIVO_COL) or "") or (m.group(1) if m else "")
            hits = [name for name, pat in (("pocos anuncios", r"poc[oa]s? (anuncios|piezas)|anuncios activos"),
                                           ("video", r"video"),
                                           ("entregable dificil", r"entregable|dificil|complej")) if re.search(pat, txt)]
            motivos.update(hits or (["otro"] if txt.strip() else ["sin motivo anotado"]))
        out.append(("MEDIO", "Ofertas que decidiste no modelar",
                    f"{len(nt)} de {n} estan en 'no testeada'. Motivos: " + ", ".join(f"{k} {v}" for k, v in motivos.most_common())
                    + ". Si domina 'pocos anuncios' sube --min-activos; si 'video' o 'entregable dificil' endurece ese filtro. "
                      "Anota el motivo al inicio de la Justificacion como 'No testeada: ...' para poder contarlo."))

    # 2) Cierre de ciclo con TUS resultados
    head = [radar.fold(h) for h in (list(of[0].keys()) if of else [])]
    if of and not any(any(w in h for w in RESULT_COLS) for h in head):
        out.append(("ALTO", "La planilla no conoce tus resultados",
                    "No hay columna de prueba/ventas/ROAS. El radar no sabe que ofertas modelaste ni cuales vendieron en el dash; "
                    "agrega 'Probada' y 'ROAS' y alimenta el Status con eso."))

    # 3) Tus propias reglas no se cumplen en lo registrado
    low_act = [r for r in of if (num(r.get("Anuncios activos")) or 99) < radar.MIN_ACTIVE and status(r) != "vigilar"]
    young = [r for r in of if (num(r.get("Días en el aire")) or 99) < radar.MIN_DAYS and status(r) != "vigilar"]
    weak = [r for r in of if status(r) == "nuevo" and (num(r.get("Nota")) or 10) < 6]
    if low_act or young or weak:
        out.append(("MEDIO", "Lo registrado incumple los filtros duros",
                    f"{len(low_act)} con <{radar.MIN_ACTIVE} anuncios activos, {len(young)} con <{radar.MIN_DAYS} dias, {len(weak)} con nota <6. "
                    "O el filtro esta mal calibrado o se esta registrando de mas: decide cual y ajusta --min-activos o el prompt."))
    doms = Counter(radar.host_of(r.get("Dominio") or "") for r in of)
    dups = [d for d, c in doms.items() if d and d != "no visible" and c > 1]
    names = Counter((r.get("Anunciante") or "").strip().lower() for r in of)
    if dups or any(c > 1 for c in names.values()):
        out.append(("BAJO", "Duplicados", f"dominios repetidos: {len(dups)}; anunciantes repetidos: {sum(1 for c in names.values() if c > 1)}."))

    # 4) Concentracion de nichos y palabras
    if n >= 5:
        top = Counter(niche_of(r) for r in of).most_common(3)
        share = top[0][1] / n
        if share >= 0.5:
            out.append(("MEDIO", "Un solo nicho domina", f"'{top[0][0]}' = {share:.0%} de las ofertas. Riesgo: si ese nicho se satura en Colombia, no hay plan B."))
    kws = radar.parse_keywords(kw)
    dead = [k["pt"] for k in kws if k["searches"] >= 3 and k["offers"] == 0]
    never = [k for k in kws if k["searches"] == 0]
    generic = [k for k in kws if not radar.niche_stems(k["pt"])]
    if dead:
        out.append(("BAJO", "Palabras para retirar", f"{len(dead)} activas con 3+ busquedas y 0 ofertas: {', '.join(dead[:3])}."))
    if kws and len(never) / len(kws) >= 0.4:
        out.append(("BAJO", "Palabras sin usar", f"{len(never)} de {len(kws)} nunca se han buscado."))
    markets = Counter((r.get("Mercado") or "").strip().lower() for r in kw if (r.get("Estado") or "activa").strip().lower() == "activa")
    if kw and set(markets) <= {"brasil", ""}:
        out.append(("MEDIO", "Fuente unica: Meta Brasil",
                    "Todas las palabras activas son de Brasil. El 'Modo Colombia primero', Mexico/Argentina en espanol y los rankings de Hotmart/Kiwify no se usan: "
                    "un ganador alla es modelable mas cerca de tu publico y no depende de una traduccion."))

    # 5) Precio y banda de venta
    prices = [min(radar.parse_price(r.get("Precio origen") or "") or [0]) for r in of]
    unknown = sum(1 for p in prices if not p)
    usd = [radar.usd_of(p) for p in prices if p]
    off = [u for u in usd if u is not None and not (2.5 <= u <= 12)]
    if of and unknown / n >= 0.4:
        out.append(("MEDIO", "Precio de origen desconocido", f"{unknown} de {n} sin precio visible: no puedes calcular margen ni CPA objetivo antes de modelar."))
    if off:
        out.append(("BAJO", "Precios fuera de tu banda", f"{len(off)} ofertas con precio de origen fuera de ~US$2.5-12 (tu banda de venta: US$4-10)."))

    # 6) Confiabilidad del hueco en Colombia y backlog del radar
    radar_rows = [r for r in of if (r.get("Justificación") or "").startswith("[radar.py]")]
    pend = [r for r in radar_rows if status(r) == "nuevo"]
    if pend:
        out.append(("ALTO" if len(pend) >= 8 else "MEDIO", "Ofertas del radar sin confirmar",
                    f"{len(pend)} filas escritas por el radar siguen en 'nuevo'. Su 'Hueco Colombia' viene de una busqueda traducida automaticamente: "
                    "confirmalo a mano antes de gastar en modelar."))
    if of and sum(1 for r in of if (r.get("Dominio") or "").strip().lower() in ("", "no visible")) / n >= 0.5:
        out.append(("BAJO", "Clones indetectables", "La mayoria sin dominio (WhatsApp directo): el mismo producto bajo otro nombre se cuenta como 2 anunciantes y infla 'validacion'."))

    # 7) Salud del radar
    runs = state.get("runs", [])
    if not runs:
        out.append(("MEDIO", "El radar no ha corrido con registro", "No hay corridas en state.json."))
    else:
        last = datetime.fromisoformat(runs[-1]["date"])
        if (now - last).days >= 7:
            out.append(("MEDIO", "Radar parado", f"Ultima corrida hace {(now - last).days} dias."))
        recent = runs[-3:]
        if len(recent) == 3 and all(r.get("limitada") or r.get("candidatos", 0) == 0 for r in recent):
            out.append(("ALTO", "Radar bloqueado o sin resultados", "Las ultimas 3 corridas fueron limitadas por Meta o no dejaron candidatos: revisa proxy/IP."))
    return out


def dash_sales(path, days=30):
    """Registros de venta por producto en los ultimos N dias (aprox.: puede haber duplicados entre archivos)."""
    cutoff, prods, countries = datetime.now() - timedelta(days=days), Counter(), {}
    for f in sorted(Path(path).glob("*.csv")):
        m = re.match(r"([a-z]{2,3})_(.+)\.csv$", f.name, re.I)      # col_ (Colombia) tiene 3 letras, el resto 2
        if not m:
            continue
        toks = [t for t in re.split(r"[_\s]+", radar.fold(m.group(2))) if len(t) > 1 and t not in ("zapdata", "xlsx", "l1", "sc", "ce")]
        key = " ".join(toks)
        try:
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rd = csv.reader(fh)
                header = [radar.fold(h) for h in next(rd, [])]
                if "fecha" not in header:
                    continue
                i = header.index("fecha")
                n = 0
                for row in rd:
                    iso = radar._iso_date(row[i]) if i < len(row) else ""
                    if iso and datetime.fromisoformat(iso) >= cutoff:
                        n += 1
        except (OSError, csv.Error):
            continue
        prods[key] += n
        countries.setdefault(key, set()).add(m.group(1).upper())
    return [(k, c, sorted(countries[k])) for k, c in prods.most_common(10) if c]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-sheets", action="store_true")
    ap.add_argument("--dash", default=str(DEFAULT_DASH), help="carpeta data del dash (ventas reales por producto)")
    a = ap.parse_args()
    now = datetime.now()
    of, kw, ni, fuente = load(a)
    state = json.loads(radar.STATE_FILE.read_text(encoding="utf-8")) if radar.STATE_FILE.exists() else {}
    st = Counter(status(r) or "(vacio)" for r in of)
    print(f"PUNTOS CIEGOS ({now:%d/%m/%Y}) | fuente: {fuente} | OFERTAS {len(of)} {dict(st)} | "
          f"PALABRAS {len(kw)} | NICHOS {len(ni)}")
    found = sorted(check_all(of, kw, ni, state, now), key=lambda x: SEV[x[0]])
    if not found:
        print("\nSin hallazgos automaticos.")
    for sev, title, detail in found:
        print(f"\n[{sev}] {title}\n  {detail}")
    print("\n--- DATOS PARA CRUZAR (lo interpreta el agente, no el script) ---")
    niches = Counter(niche_of(r) for r in of).most_common(8)
    print("Nichos minados (ofertas):", "; ".join(f"{k} {v}" for k, v in niches) or "ninguno")
    win = Counter(niche_of(r) for r in of if status(r) == "ganadora")
    print("Nichos con ganadora:", "; ".join(f"{k} {v}" for k, v in win.most_common()) or "ninguno")
    dash = Path(a.dash)
    if dash.exists():
        sales = dash_sales(dash)
        print("Ventas propias 30d (registros, aprox.):",
              "; ".join(f"{k} {c} [{'/'.join(cs)}]" for k, c, cs in sales) or "sin datos recientes")
    else:
        print(f"Ventas propias: no encuentro {dash} (corre en el PC del dash).")


if __name__ == "__main__":
    main()
