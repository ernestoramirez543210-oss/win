"""Anota (o actualiza) un anuncio en anuncios/<producto>/historial.json, sacando del
videoData.json lo que se puede leer solo: colores, musica y tramos usados.

Uso:
  python scripts/anotar_historial.py <videoData.json> --gancho "texto del gancho"
                                     [--estilo "descripcion corta"] [--final ruta.mp4] [--costo 0.0]

Se lee ANTES de planear un anuncio nuevo del mismo producto para no repetir gancho, colores,
estilo ni tomas en anuncios seguidos, y se actualiza al terminar cada anuncio.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, configurar_consola, escribir_json, leer_json, salir  # noqa: E402


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videodata")
    ap.add_argument("--gancho", required=True)
    ap.add_argument("--estilo", default="")
    ap.add_argument("--final")
    ap.add_argument("--costo", type=float, help="costo real en USD (anuncios con IA)")
    a = ap.parse_args()

    ruta = Path(a.videodata)
    if not ruta.is_file():
        salir(f"No existe {ruta}")
    d = leer_json(ruta)
    meta, estilo = d["meta"], d.get("estilo", {})
    entrada = {
        "anuncio": meta["anuncio"],
        "tipo": meta["tipo"],
        "fecha": date.today().isoformat(),
        "gancho": a.gancho,
        "colores": {
            "primario": estilo.get("colorPrimario"),
            "activo": estilo.get("subtitulos", {}).get("colorActivo"),
        },
        "estilo": a.estilo,
        "musica": d.get("audio", {}).get("musica", {}).get("src"),
        "tomas_usadas": [
            {"src": t["src"], "desde": round(float(t["desde"]), 2), "hasta": round(float(t["desde"]) + float(t["duracion"]) * float(t.get("velocidad", 1) or 1), 2)}
            for t in d.get("tomas", [])
        ],
        "final": a.final,
        "costo_usd": a.costo,
    }

    hist_ruta = ANUNCIOS / meta["producto"] / "historial.json"
    hist = leer_json(hist_ruta) if hist_ruta.is_file() else {"producto": meta["producto"], "anuncios": []}
    hist["anuncios"] = [x for x in hist["anuncios"] if x.get("anuncio") != meta["anuncio"]] + [entrada]
    escribir_json(hist_ruta, hist)
    print(f"Historial actualizado: {hist_ruta} ({len(hist['anuncios'])} anuncio(s))")


if __name__ == "__main__":
    main()
