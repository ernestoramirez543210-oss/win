"""Pruebas de la puerta de mejoras (scripts/editor_promover.py). No tocan produccion: todo corre en una carpeta temporal con el codigo de la puerta apuntado ahi.

  python scripts/autoprueba_promover.py

Cubre: que archivo es cada cosa, la revision del codigo visual, la lectura segura del paquete (enlaces, rutas raras, repetidos, tamanos), los conflictos con produccion, lo que queda
pendiente, la instalacion con copia, el deshacer, y el atendedor de la carpeta de la persona (enlaces puestos a proposito). La caja de pruebas (render con systemd) se prueba en vivo en el servidor.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import editor_promover as ep  # noqa: E402

fallos = 0


def comprobar(nombre: str, condicion: bool, detalle=None) -> None:
    global fallos
    print(("  ok     " if condicion else "  FALLA  ") + nombre)
    if not condicion:
        fallos += 1
        if detalle is not None:
            print(f"         -> {detalle!r}")


def lanza(f, *a) -> str | None:
    try:
        f(*a)
    except ep.ErrorMejora as e:
        return str(e)
    return None


def tar_crudo(miembros: list[tuple[str, bytes, str]]) -> bytes:
    """(nombre, datos, tipo: file|symlink|dir)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for nombre, datos, tipo in miembros:
            ti = tarfile.TarInfo(nombre)
            if tipo == "symlink":
                ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
            elif tipo == "dir":
                ti.type = tarfile.DIRTYPE
            else:
                ti.size = len(datos)
            tf.addfile(ti, io.BytesIO(datos) if tipo == "file" else None)
    return buf.getvalue()


def meta(persona="ana", base=None) -> tuple[str, bytes, str]:
    return ("PAQUETE.json", json.dumps({"version": 1, "persona": persona, "base": base or {}}).encode(), "file")


print("Que archivo es cada cosa")
c = ep.clasificar
comprobar("src/components/Nuevo.tsx es automatico", c("src/components/Nuevo.tsx") == ("auto", "src/components/Nuevo.tsx"))
comprobar("src/FullVideo.tsx es automatico", c("src/FullVideo.tsx")[0] == "auto")
comprobar("un componente con ruta rara no", c("src/components/../x.tsx")[0] == "no" and c("src/components/a/b.tsx")[0] == "no" and c("src/components/a.ts")[0] == "no")
comprobar("scripts/montaje_auto.py espera visto bueno", c("scripts/montaje_auto.py")[0] == "revision")
comprobar("un programa no puede llamarse como un modulo de la libreria estandar ni como el revisor", all(c(f"scripts/{n}.py")[0] == "no" for n in ("json", "argparse", "os", "pwd", "revisar_src")) and c("scripts/revisar_src.mjs")[0] == "no")
comprobar("la puerta, Drive, el corredor y las llaves nunca", all(c(f"scripts/{n}.py")[0] == "no" for n in ("autoprueba_puerta", "autoprueba_promover", "puerta_editor", "editor_promover", "drive_api", "corredor_vps", "espejo_drive", "apimart", "apimart_chat", "revisar_prompts", "autoprueba_revisar_prompts")))
comprobar("CLAUDE.md y las skills son guias", c("CLAUDE.md") == ("doc", "vps/espacio/CLAUDE.md") and c(".claude/skills/editor-video/SKILL.md")[0] == "doc" and c(".claude/skills/editor-video/references/a.md")[0] == "doc")
comprobar("otra skill no", c(".claude/skills/otra/SKILL.md")[0] == "no")
comprobar("paquetes npm, configuracion, vps/, secretos: no", all(c(r)[0] == "no" for r in ("package.json", "package-lock.json", "remotion.config.ts", "tsconfig.json", "vps/editor-puerta.json", "secretos/apimart.key", "scripts/x.sh", ".bashrc", "../x")))
comprobar("index.css, las guias y los programas esperan visto bueno", c("src/index.css") == ("revision", "src/index.css") and c("CLAUDE.md")[0] == "doc" and c("scripts/montaje_auto.py")[0] == "revision")
comprobar("pruebas/*.json es una prueba", c("pruebas/demo.json")[0] == "prueba" and c("pruebas/a/b.json")[0] == "no")

print("Revision del codigo visual")
rv = ep.revisar_codigo_visual
limpio = b'import React from "react";\nimport {AbsoluteFill} from "remotion";\nimport {X} from "./X";\nexport const A = () => <AbsoluteFill />;\n'
comprobar("codigo limpio pasa", rv("src/components/A.tsx", limpio) == [])
for nombre, codigo in {
    "fetch": b'const x = fetch("a");', "XMLHttpRequest": b"new XMLHttpRequest()", "WebSocket": b"new WebSocket(u)", "una direccion": b'const u = "http://x.y";', "eval": b"eval(s)",
    "new Function": b"new Function('a')", "require": b"require('fs')", "import dinamico": b"import('x')", "process": b"process.env.X", "window[...]": b'window["fe"+"tch"]("x")',
    "self.fetch": b"self.fetch(1)", "Function": b"const F = Function;", ".constructor": b"x.constructor('a')", "un comentario con fetch(": b"// fetch(x)\n",
}.items():
    comprobar(f"rechaza {nombre}", len(rv("src/components/A.tsx", codigo)) > 0, codigo)
comprobar("rechaza un modulo que no es del proyecto", len(rv("src/components/A.tsx", b'import fs from "fs";')) > 0)
comprobar("rechaza import de node:", len(rv("src/components/A.tsx", b'import fs from "node:fs";')) > 0)
comprobar("acepta @remotion/* y @fontsource/*", rv("src/components/A.tsx", b'import {a} from "@remotion/shapes";\nimport "@fontsource/montserrat/700.css";') == [])
comprobar("«//» dentro de una cadena no esconde una llamada", len(rv("src/components/A.tsx", b'const a = "//"; fetch(x);')) > 0)
comprobar("un CSS no se instala solo (Tailwind lo ejecuta al empaquetar: @plugin, @config, @source)", all(len(rv("src/index.css", c)) > 0 for c in (b"body{margin:0}", b'@plugin "./x.js";', b'@config "../x.js";', b'@source "../x";', b'@import "tailwindcss";')))
for nombre, (codigo, ruta) in {
    "un import que sale de src/ (../node_modules)": (b'import x from "../node_modules/evil/index";', "src/components/A.tsx"),
    "un import que sale de src/ (../../scripts)": (b'import x from "../../scripts/y";', "src/components/A.tsx"),
    "un import relativo de un CSS": (b'import "./estilo.css";', "src/components/A.tsx"),
    "un iframe": (b"<iframe src={x} />", "src/components/A.tsx"),
    "una etiqueta img": (b'<img src="a.png" />', "src/components/A.tsx"),
    "un src escrito a mano": (b'<Img src="a.png" />', "src/components/A.tsx"),
    "un src armado": (b'<Img src={"a" + b} />', "src/components/A.tsx"),
    "un src con llamada rara": (b"<Img src={algo(b)} />", "src/components/A.tsx"),
    "un href": (b'<a href={x}>t</a>', "src/components/A.tsx"),
    "url() en un .tsx": (b'const e = {background: "url(x.png)"};', "src/components/A.tsx"),
    "una direccion que empieza por //": (b'const u = "//evil.example/x";', "src/components/A.tsx"),
    "document.cookie": (b"const c = document.cookie;", "src/components/A.tsx"),
    "document.getElementById": (b'document.getElementById("x");', "src/components/A.tsx"),
    "new Image": (b"new Image();", "src/components/A.tsx"),
    "import.meta": (b"const u = import.meta.url;", "src/components/A.tsx"),
    "module.exports": (b"module.exports = 1;", "src/components/A.tsx"),
    "define de AMD": (b"define([\"x\"], f);", "src/components/A.tsx"),
    "un import de fuentes con ..": (b"import \"@fontsource/montserrat/../../evil\";", "src/components/A.tsx"),
}.items():
    comprobar(f"rechaza {nombre}", len(rv(ruta, codigo)) > 0, codigo)
for nombre, (codigo, ruta) in {
    "un src con staticFile": (b"<Img src={staticFile(boton.icono)} />", "src/components/A.tsx"),
    "un src con una propiedad": (b"<Img src={libro.src} />", "src/components/A.tsx"),
    "una referencia interna url(#filtro) de SVG": (b'<circle filter="url(#difuso)" />', "src/components/A.tsx"),
    "document.fonts y un canvas para medir": (b'await document.fonts.load("900 10px x"); document.createElement("canvas").getContext("2d");', "src/components/A.tsx"),
    "el import del CSS del proyecto desde index.ts": (b'import "./index.css";\nimport {registerRoot} from "remotion";', "src/index.ts"),
    "un import relativo dentro de src/": (b'import {B} from "../types";\nimport {C} from "./C";', "src/components/A.tsx"),
}.items():
    comprobar(f"acepta {nombre}", rv(ruta, codigo) == [], rv(ruta, codigo))
comprobar("rechaza lo que no es UTF-8 y los bytes nulos", len(rv("src/components/A.tsx", b"\xff\xfe")) > 0 and len(rv("src/components/A.tsx", b"a\x00b")) > 0)

print("Leer el paquete")
t = ep.armar_tar("ana", {"src/components/A.tsx": limpio}, {"src/components/A.tsx": None})
m, a = ep.leer_tar(t, "ana")
comprobar("ida y vuelta", a == {"src/components/A.tsx": limpio} and m["persona"] == "ana")
comprobar("persona distinta se rechaza", lanza(ep.leer_tar, t, "bea") is not None)
comprobar("no empieza por PAQUETE.json", lanza(ep.leer_tar, tar_crudo([("src/a.tsx", b"x", "file"), meta()]), "ana") is not None)
comprobar("un enlace simbolico se rechaza", lanza(ep.leer_tar, tar_crudo([meta(), ("src/components/A.tsx", b"", "symlink")]), "ana") is not None)
comprobar("una carpeta se rechaza", lanza(ep.leer_tar, tar_crudo([meta(), ("src/components", b"", "dir")]), "ana") is not None)
for mal in ("/etc/passwd", "../x.tsx", "src/../../x", "src\\a.tsx", "src//a.tsx", "./src/a.tsx"):
    comprobar(f"nombre «{mal}» se rechaza", lanza(ep.leer_tar, tar_crudo([meta(), (mal, b"x", "file")]), "ana") is not None)
comprobar("repetidos se rechazan", lanza(ep.leer_tar, tar_crudo([meta(), ("src/a.tsx", b"1", "file"), ("src/a.tsx", b"2", "file")]), "ana") is not None)
comprobar("archivo enorme se rechaza", lanza(ep.leer_tar, tar_crudo([meta(), ("src/a.tsx", b"x" * (ep.MAX_ARCHIVO + 1), "file")]), "ana") is not None)
comprobar("demasiados archivos se rechazan", lanza(ep.leer_tar, tar_crudo([meta()] + [(f"src/components/C{i}.tsx", b"x", "file") for i in range(ep.MAX_ARCHIVOS + 1)]), "ana") is not None)
comprobar("no es un tar", lanza(ep.leer_tar, b"hola" * 200, "ana") is not None)
comprobar("un tar comprimido se rechaza", lanza(ep.leer_tar, __import__("gzip").compress(t), "ana") is not None)
comprobar("base con valores raros se rechaza", lanza(ep.leer_tar, tar_crudo([("PAQUETE.json", json.dumps({"version": 1, "persona": "ana", "base": {"a": "no-es-sha"}}).encode(), "file")]), "ana") is not None)

print("Procesar un paquete (produccion de mentira)")
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    ep.BASE, ep.RAIZ = tmp / "opt", tmp / "srv"
    for d in ("src/components", "scripts", "logs", "vps/espacio"):
        (ep.BASE / d).mkdir(parents=True)
    (ep.BASE / ".versiones").mkdir(mode=0o700)
    viejo = b'import React from "react";\nexport const V = 1;\n'
    (ep.BASE / "src/components/Tomas.tsx").write_bytes(viejo)
    (ep.BASE / "scripts/montaje_auto.py").write_bytes(b"x = 1\n")
    (ep.BASE / "vps/espacio/CLAUDE.md").write_bytes(b"# viejo\n")
    sha = ep.sha_bytes
    llamadas = []
    ep.probar_en_caja = lambda stage, auto, pruebas, id_: (llamadas.append(sorted(auto)) or True, "prueba de mentira ok")

    def paquete(archivos, base):
        return ep.armar_tar("ana", archivos, base)

    nuevo = b'import React from "react";\nexport const V = 2;\n'
    r = ep.procesar_paquete("ana", "20261104T100000-aaaaaa", paquete({"src/components/Tomas.tsx": nuevo}, {"src/components/Tomas.tsx": sha(viejo)}))
    comprobar("una mejora automatica se instala", r["estado"] == "instalado" and (ep.BASE / "src/components/Tomas.tsx").read_bytes() == nuevo, r)
    comprobar("la caja de pruebas se uso con ese archivo", llamadas == [["src/components/Tomas.tsx"]], llamadas)
    comprobar("queda copia de lo reemplazado", any(p.name == "Tomas.tsx" and p.read_bytes() == viejo for p in (ep.BASE / ".versiones").rglob("Tomas.tsx")))
    prom = json.loads((ep.BASE / ".versiones/promovidas.json").read_text())
    comprobar("queda anotado en promovidas.json", prom["src/components/Tomas.tsx"]["sha"] == sha(nuevo) and prom["src/components/Tomas.tsx"]["repo"] == "src/components/Tomas.tsx", prom)
    comprobar("el resultado trae la base nueva", r["base_nueva"] == {"src/components/Tomas.tsx": sha(nuevo)}, r)
    version = r["version"]
    man = json.loads(next((ep.BASE / ".versiones" / version).glob("manifest.json")).read_text())
    comprobar("el manifiesto queda cerrado («listo»)", man["estado"] == "listo" and man["archivos"][0]["sha_despues"] == sha(nuevo), man)

    r = ep.procesar_paquete("ana", "20261104T100001-aaaaaa", paquete({"src/components/Tomas.tsx": nuevo + b"// otra\n"}, {"src/components/Tomas.tsx": sha(viejo)}))
    comprobar("conflicto: la base ya no es la de produccion -> no se pisa", r["estado"] == "rechazado" and "CONFLICTO" in json.dumps(r) and (ep.BASE / "src/components/Tomas.tsx").read_bytes() == nuevo, r)

    r = ep.procesar_paquete("ana", "20261104T100002-aaaaaa", paquete({"src/components/Nueva.tsx": limpio}, {"src/components/Nueva.tsx": None}))
    comprobar("un componente nuevo se instala", r["estado"] == "instalado" and (ep.BASE / "src/components/Nueva.tsx").is_file(), r)
    r = ep.procesar_paquete("ana", "20261104T100003-aaaaaa", paquete({"src/components/Nueva.tsx": limpio + b"//x\n"}, {"src/components/Nueva.tsx": None}))
    comprobar("un componente «nuevo» que ya existe es conflicto", r["estado"] == "rechazado", r)

    llamadas.clear()
    r = ep.procesar_paquete("ana", "20261104T100004-aaaaaa", paquete({"src/components/Mala.tsx": b'fetch("x")'}, {"src/components/Mala.tsx": None}))
    comprobar("codigo con red se rechaza y no llega a la caja", r["estado"] == "rechazado" and llamadas == [] and not (ep.BASE / "src/components/Mala.tsx").exists(), r)

    r = ep.procesar_paquete("ana", "20261104T100005-aaaaaa", paquete({"src/components/Buena.tsx": limpio, "src/components/Mala.tsx": b'fetch("x")'}, {"src/components/Buena.tsx": None, "src/components/Mala.tsx": None}))
    comprobar("un archivo visual malo frena todo lo visual", r["estado"] == "rechazado" and not (ep.BASE / "src/components/Buena.tsx").exists(), r)

    ep.probar_en_caja = lambda *a: (False, "se vio en blanco")
    r = ep.procesar_paquete("ana", "20261104T100006-aaaaaa", paquete({"src/components/Buena.tsx": limpio}, {"src/components/Buena.tsx": None}))
    comprobar("si la prueba de render falla, no se instala", r["estado"] == "rechazado" and not (ep.BASE / "src/components/Buena.tsx").exists(), r)
    ep.probar_en_caja = lambda stage, auto, pruebas, id_: (True, "ok")

    r = ep.procesar_paquete("ana", "20261104T100007-aaaaaa", paquete({"scripts/montaje_auto.py": b"x = 2\n"}, {"scripts/montaje_auto.py": sha(b"x = 1\n")}))
    comprobar("un programa de montaje se instala solo (orden del dueno)", r["estado"] == "instalado" and (ep.BASE / "scripts/montaje_auto.py").read_bytes() == b"x = 2\n" and not r["pendientes"], r)
    r2 = ep.procesar_paquete("ana", "20261104T100008-aaaaaa", paquete({"scripts/otro.py": b"def (:\n"}, {"scripts/otro.py": None}))
    comprobar("un programa que no compila se rechaza de entrada", r2["estado"] == "rechazado" and "no compila" in json.dumps(r2), r2)

    r = ep.procesar_paquete("ana", "20261104T100009-aaaaaa", paquete({"scripts/puerta_editor.py": b"x = 1\n"}, {"scripts/puerta_editor.py": None}))
    comprobar("la puerta no se puede cambiar por aqui: se rechaza el paquete entero", r["estado"] == "rechazado" and not (ep.BASE / "scripts/puerta_editor.py").exists(), r)
    r = ep.procesar_paquete("ana", "20261104T100010-aaaaaa", paquete({"src/components/Z.tsx": limpio, "scripts/drive_api.py": b"x = 1\n"}, {"src/components/Z.tsx": None, "scripts/drive_api.py": None}))
    comprobar("un archivo prohibido junto a uno bueno: no se instala ninguno", r["estado"] == "rechazado" and not (ep.BASE / "src/components/Z.tsx").exists(), r)

    r = ep.procesar_paquete("ana", "20261104T100011-aaaaaa", paquete({"CLAUDE.md": b"# nuevo\n"}, {"CLAUDE.md": sha(b"# viejo\n")}))
    comprobar("las guias se instalan solas en la plantilla del espacio", r["estado"] == "instalado" and (ep.BASE / "vps/espacio/CLAUDE.md").read_bytes() == b"# nuevo\n", r)
    (ep.BASE / "src").mkdir(exist_ok=True)
    (ep.BASE / "src/index.css").write_bytes(b'@import "tailwindcss";\n')
    r = ep.procesar_paquete("ana", "20261104T100013-aaaaaa", paquete({"src/index.css": b'@import "tailwindcss";\n.x{color:red}\n'}, {"src/index.css": sha(b'@import "tailwindcss";\n')}))
    comprobar("un index.css se instala solo", r["estado"] == "instalado" and b".x{color:red}" in (ep.BASE / "src/index.css").read_bytes(), r)
    r = ep.procesar_paquete("ana", "20261104T100012-aaaaaa", paquete({"src/components/Tomas.tsx": nuevo}, {"src/components/Tomas.tsx": sha(nuevo)}))
    comprobar("lo que ya es igual a produccion no es un cambio", r["estado"] == "sin_cambios", r)

    ep.revertir(version)
    comprobar("revertir devuelve el archivo anterior", (ep.BASE / "src/components/Tomas.tsx").read_bytes() == viejo)
    comprobar("y lo saca de promovidas.json", "src/components/Tomas.tsx" not in json.loads((ep.BASE / ".versiones/promovidas.json").read_text()))
    ep.estado()

    print("Atender la carpeta de la persona (con enlaces puestos a proposito)")
    import pwd
    yo = pwd.getpwuid(os.getuid()).pw_name
    carpeta = ep.RAIZ / "personas" / yo / "mejoras"
    carpeta.mkdir(parents=True)
    secreto = tmp / "secreto.txt"
    secreto.write_text("no tocar")
    idp = "20261104T110000-bbbbbb"
    (carpeta / f"{idp}.tar").write_bytes(ep.armar_tar(yo, {"src/components/Tomas.tsx": nuevo}, {"src/components/Tomas.tsx": sha(viejo)}))
    os.symlink(secreto, carpeta / f"{idp}.resultado.json")          # el resultado ya esta puesto como enlace a otro archivo
    (carpeta / "basura.tar").write_bytes(b"x")
    os.symlink(secreto, carpeta / "20261104T110001-cccccc.tar")      # un paquete que es un enlace
    ep.servir(yo)
    comprobar("no escribio a traves del enlace del resultado", secreto.read_text() == "no tocar")
    res = json.loads((carpeta / f"{idp}.resultado.json").read_text())
    comprobar("dejo el resultado como archivo normal", not (carpeta / f"{idp}.resultado.json").is_symlink() and res["estado"] == "instalado", res)
    comprobar("el paquete que era un enlace se rechazo y se quito", not (carpeta / "20261104T110001-cccccc.tar").exists() and json.loads((carpeta / "20261104T110001-cccccc.resultado.json").read_text())["estado"] == "rechazado")
    comprobar("el .tar con nombre raro se quito", not (carpeta / "basura.tar").exists())
    comprobar("no queda ningun .tar (el vigilante no se dispara otra vez)", not list(carpeta.glob("*.tar")))
    comprobar("el secreto sigue intacto", secreto.read_text() == "no tocar")

    print("Cuota por hora (la lleva root)")
    ep.PAQUETES_POR_HORA = 3
    (ep.BASE / ".promover").mkdir(exist_ok=True)
    comprobar("los 3 primeros pasan", all(ep._cuota_ok("zoe") for _ in range(3)))
    comprobar("el cuarto no", not ep._cuota_ok("zoe"))
    comprobar("otra persona tiene su propia cuenta", ep._cuota_ok("liz"))
    ep.PAQUETES_POR_HORA = 6

    print("Archivos raros en la carpeta de la persona")
    for n in os.listdir(carpeta):
        try:
            os.unlink(carpeta / n)
        except OSError:
            pass
    os.mkfifo(carpeta / "20261104T120000-dddddd.tar")                # un FIFO con nombre de paquete
    os.mkdir(carpeta / "20261104T120001-eeeeee.tar")                 # una carpeta con nombre de paquete
    import threading
    hilo = threading.Thread(target=ep.servir, args=(yo,), daemon=True)
    hilo.start()
    hilo.join(20)
    comprobar("un FIFO y una carpeta .tar no cuelgan ni tumban al servicio", not hilo.is_alive())
    comprobar("no queda ningun .tar (el vigilante no se dispara otra vez)", not list(carpeta.glob("*.tar")), os.listdir(carpeta))

    print("Base inicial")
    (ep.RAIZ / "personas" / "ana").mkdir(parents=True)
    (ep.BASE / "src/components/Tomas.tsx.antes-arreglo").write_bytes(b"VIEJISIMO")
    ep.escribir_base_inicial("ana", None)
    bi = json.loads((ep.RAIZ / "personas/ana/base-inicial.json").read_text())
    comprobar("la base inicial normal usa lo que hay hoy", bi["src/components/Tomas.tsx"] == sha(nuevo) and "scripts/puerta_editor.py" not in bi, list(bi))
    ep.escribir_base_inicial("ana", __import__("time").time() - 3600)
    bi = json.loads((ep.RAIZ / "personas/ana/base-inicial.json").read_text())
    comprobar("con --desde usa la copia mas vieja hecha despues de esa fecha", bi["src/components/Tomas.tsx"] == sha(b"VIEJISIMO"), bi.get("src/components/Tomas.tsx"))

print()
print("TODO BIEN" if fallos == 0 else f"{fallos} PRUEBA(S) FALLARON")
sys.exit(0 if fallos == 0 else 1)
