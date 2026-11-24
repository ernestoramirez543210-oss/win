"""Ensayo SIN GASTAR del montaje de un anuncio de AVATAR en el servidor: arma un pedido de mentira con 3 clips que hablan (voz de Windows en espanol sobre cuadros dibujados) y corre
`avatar_auto.py montar` de verdad: Whisper local, plan, Remotion, compuertas de calidad. No llama a APIMart, ni a Fish, ni al dash, ni a Drive. Mide cuanto tarda y deja el mp4 en
.cache/ensayo_av/salida/ para mirarlo. Se corre dentro de la unidad aislada:  corredor-ctl ejecutar -- /opt/broll-tiktok/venv/bin/python /opt/anuncios/.cache/ensayo_av/ensayo_montaje_avatar.py"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/anuncios/scripts")
from _comun import ANUNCIOS, PUBLIC, RAIZ  # noqa: E402
import prueba_servidor as PS  # noqa: E402

PRODUCTO, AD = "_ensayo_av", "AD1"
ASSETS = Path(RAIZ) / ".cache" / "ensayo_av"
SALIDA = ASSETS / "salida"
FRASES = ["¿Tus reuniones de mujeres se ponen aburridas y ya no sabes qué inventar?", "Pasa mucho: llegan, se saludan y al rato todas miran el celular.", "Mira, hay una forma sencilla de cambiar eso en tu próxima reunión.",
          "Son treinta dinámicas listas para usar, con los pasos, el tiempo y los materiales.", "Además te llevas un bono de juegos para romper el hielo cuando llegan nuevas.",
          "Escríbenos por WhatsApp y recibe tus dinámicas hoy mismo en tu celular."]
ROLES = ["gancho", "problema", "giro", "oferta", "oferta", "llamada"]
GRANDES = {0: "¿TE ABURRES?", 3: "30 DINÁMICAS"}


def main() -> int:
    carpeta, base = PUBLIC / "productos" / PRODUCTO, ANUNCIOS / PRODUCTO / "ia" / AD
    for ruta in (carpeta, ANUNCIOS / PRODUCTO):
        if ruta.exists():
            print(f"Ya existe {ruta}: no la toco.")
            return 1
    try:
        from PIL import Image

        PS.crear_portada_y_paginas(carpeta)
        (carpeta / "producto.json").write_text(json.dumps({"nombre": "Producto de prueba", "que_es": "un ebook de dinamicas para reuniones", "promesa_principal": "30 dinamicas listas para usar"}, ensure_ascii=False), encoding="utf-8")
        av = carpeta / "avatares" / "lucia"
        av.mkdir(parents=True)
        Image.frombytes("RGB", (400, 500), os.urandom(400 * 500 * 3)).save(av / "foto1.jpg", quality=70)
        (av / "avatar.json").write_text(json.dumps({"nombre": "lucia", "genero": "mujer", "fotos": ["foto1.jpg"], "autorizado": True, "aspecto": "woman in her mid-30s with long dark hair and warm brown eyes",
                                                    "voz": "a female voice in her mid-30s, medium pitch, calm and confident, warm", "acento": "Spanish with a clear, neutral Colombian accent",
                                                    "casa_base": "kitchen with a wooden counter, a stove on the left and a window with natural light"}), encoding="utf-8")
        for sub in ("avatar", "cuadros", "clips"):
            (base / sub).mkdir(parents=True)
        for i in (1, 2, 3):
            shutil.copy2(ASSETS / "cuadros" / f"{i:02d}.png", base / "cuadros" / f"{i:02d}.png")
            shutil.copy2(ASSETS / "clips" / f"{i:02d}.mp4", base / "clips" / f"{i:02d}.mp4")
        (base / "orden.json").write_text(json.dumps({"proyecto": {"id": "pensayo", "nombre": "Ensayo avatar", "slug": PRODUCTO, "paises": ["Colombia"]},
                                                     "oferta": {"promesa": "30 dinamicas listas para usar en tus reuniones de mujeres", "promesaCorta": "30 dinamicas listas", "vinetas": ["30 dinamicas con pasos"], "ebooks": []}}, ensure_ascii=False), encoding="utf-8")
        (base / "receta.json").write_text(json.dumps({"tipoAnuncio": "ia", "duracionMin": 20, "duracionMax": 25, "idioma": {"variante": "colombiano"}, "aprobacion": {"plan": "mostrar", "topeUsd": 0.30},
                                                      "textosGrandes": {"min": 0, "max": 2}, "cta": {"texto": "", "subtexto": ""}}, ensure_ascii=False), encoding="utf-8")
        frases = [{"texto": t, "rol": ROLES[i], "visual": {"en": "woman talking to the camera in a bright kitchen", "etiquetas": ["avatar"], "producto": False}, "textoGrande": GRANDES.get(i, "")} for i, t in enumerate(FRASES)]
        (base / "guion.json").write_text(json.dumps({"idioma": "es", "frases": frases, "cta": {"texto": "Pídelo por WhatsApp", "subtexto": ""}, "palabras": sum(len(t.split()) for t in FRASES), "tipo": "ia", "clips": 3}, ensure_ascii=False), encoding="utf-8")
        (base / "guion.txt").write_text(" ".join(FRASES) + "\n", encoding="utf-8")
        textos = [" ".join(FRASES[0:2]), " ".join(FRASES[2:4]), " ".join(FRASES[4:6])]
        plan = {"version": 1, "avatar": "lucia", "genero": "mujer", "n_clips": 3, "huella": "ensayo", "ropa": "a plain green sweater", "costo_estimado_usd": 0.29, "modelo": "ensayo", "creado": "2026-10-23T16:00:00",
                "clips": [{"n": i, "texto": t, "palabras": len(t.split()), "escena": "kitchen counter next to the window, medium shot, arranging paper cups", "accion": "enters the frame holding the product", "usa_producto": False}
                          for i, t in enumerate(textos, 1)]}
        (base / "avatar" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        led = {"version": 1, "anteriores_usd": 0.0, "analisis": [], "imagenes": {f"{i:02d}": {"costo": 0.0085, "revision": {"ok": True, "problemas": [], "motivo": "", "visto": True}} for i in (1, 2, 3)},
               "clips": {f"{i:02d}": {"costo": 0.0816, "intentos": 1, "revision": {"ok": True, "cobertura": 1.0, "detalle": "ensayo"}} for i in (1, 2, 3)}}
        (base / "avatar" / "estado.json").write_text(json.dumps(led), encoding="utf-8")

        print("Pedido de avatar de mentira listo: 3 clips con voz, sin gastar nada. Corro `avatar_auto.py montar` de verdad.", flush=True)
        cmd = [sys.executable, "-u", str(Path(RAIZ) / "scripts" / "avatar_auto.py"), "montar", PRODUCTO, AD, "--concurrencia", "1", "--sin-historial"]
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=RAIZ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for linea in proc.stdout:  # type: ignore[union-attr]
            print(f"  [+{int(time.time() - t0) // 60:02d}:{int(time.time() - t0) % 60:02d}] {linea.rstrip()[:260]}", flush=True)
        proc.wait()
        seg = time.time() - t0
        print(f"\nTermino con codigo {proc.returncode} en {seg / 60:.1f} min ({seg:.0f} s).", flush=True)
        inf_ruta = base / "informe_calidad.json"
        if inf_ruta.is_file():
            inf = json.loads(inf_ruta.read_text(encoding="utf-8"))
            print("estado:", inf.get("estado"), "· duracion:", inf.get("duracion_s"), "s · tiempos:", json.dumps(inf.get("tiempos")), flush=True)
            print("compuertas que fallan:", [k for k, v in (inf.get("compuertas") or {}).items() if v.get("ok") is False], "· sin verificar:", inf.get("sin_verificar"), flush=True)
            print("errores del validador:", inf.get("errores_validador"), flush=True)
        tr = base / "transcripcion.json"
        if tr.is_file():
            t = json.loads(tr.read_text(encoding="utf-8"))
            print("Whisper oyo", len(t.get("palabras", [])), "palabras:", str(t.get("texto"))[:300], flush=True)
        SALIDA.mkdir(parents=True, exist_ok=True)
        for mp4 in sorted((base / "final").glob("*.mp4")):
            shutil.copy2(mp4, SALIDA / mp4.name)
            print("mp4:", mp4.name, json.dumps(PS.propiedades_mp4(mp4)), flush=True)
        for nombre in ("informe_calidad.json", "videoData.json", "transcripcion.json", "render.log"):
            if (base / nombre).is_file():
                shutil.copy2(base / nombre, SALIDA / nombre)
        print(f"Memoria maxima de un hijo: {PS.memoria_maxima_mb():.0f} MB", flush=True)
        return proc.returncode
    finally:
        for ruta in (carpeta, ANUNCIOS / PRODUCTO):
            shutil.rmtree(ruta, ignore_errors=True)
        print("Borrado lo que se creo (producto y anuncio de prueba).", flush=True)


if __name__ == "__main__":
    sys.exit(main())
