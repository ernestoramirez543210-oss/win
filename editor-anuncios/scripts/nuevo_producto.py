"""Crea la carpeta de un producto nuevo copiando producto-ejemplo (con todas sus subcarpetas,
las plantillas producto.json / avatar.json y los LEEME.txt).

Uso:
  python scripts/nuevo_producto.py <producto> [--avatar <nombre>]

Reglas del nombre: corto, en minusculas, sin espacios ni tildes (p. ej. velas, brownies).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import PUBLIC, configurar_consola, escribir_json, leer_json, salir  # noqa: E402

NOMBRE_VALIDO = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("producto")
    ap.add_argument("--avatar", help="nombre del primer avatar (por defecto se deja avatar-ejemplo para renombrar)")
    a = ap.parse_args()

    if not NOMBRE_VALIDO.match(a.producto):
        salir("Nombre no valido: usa minusculas, numeros, guion o guion bajo, sin espacios ni tildes.")
    ejemplo = PUBLIC / "productos" / "producto-ejemplo"
    destino = PUBLIC / "productos" / a.producto
    if not ejemplo.is_dir():
        salir(f"Falta la carpeta de ejemplo {ejemplo}")
    if destino.exists():
        salir(f"Ya existe {destino}")
    shutil.copytree(ejemplo, destino)

    if a.avatar:
        if not NOMBRE_VALIDO.match(a.avatar):
            salir("Nombre de avatar no valido (minusculas, sin espacios).")
        carpeta_avatar = destino / "avatares" / a.avatar
        (destino / "avatares" / "avatar-ejemplo").rename(carpeta_avatar)
        ficha = carpeta_avatar / "avatar.json"
        if ficha.is_file():
            datos = leer_json(ficha)
            datos["nombre"] = a.avatar
            escribir_json(ficha, datos)

    print(f"LISTO: {destino}")
    print("Ahora sube: ebook.pdf, las fotos del avatar (avatares/<nombre>/), los videos (broll-tiktok/), voces/ y guiones/.")


if __name__ == "__main__":
    main()
