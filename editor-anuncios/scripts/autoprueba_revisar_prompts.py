"""Autoprueba de revisar_prompts.py (la revision de cada prompt ANTES de pagar una imagen o un clip). Sin red, sin gastar nada.

  python scripts/autoprueba_revisar_prompts.py

1. Los casos compartidos con el dash (casos_revisar_prompts.json): la copia del dash (functions/_revisar-prompts.js, scripts/probar-revisar-prompts.mjs) corre los mismos y tiene que
   dar lo mismo. Si cambias una regla, cambiala en las dos copias y agrega su caso.
2. Lo que solo mira el editor: que la referencia del producto sea una foto del producto (por la ruta), VOICE y ACCENT contra los otros clips de la carpeta, el uso a mano.
3. apimart.py de verdad (con el APIMart falso de las autopruebas): un prompt que no cumple sale con la lista, sin llamar a nadie ni anotar costo; uno que cumple pasa.
4. Los prompts que arman gancho_ia.py y avatar_auto.py (con escenas que cumplen) pasan la revision; un anuncio de avatar se revisa entero antes del primer pago.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import avatar_comun as AC  # noqa: E402
import revisar_prompts as R  # noqa: E402

FALLOS: list[str] = []
TOTAL = 0


def comprobar(cond: bool, que: str, detalle: str = "") -> None:
    global TOTAL
    TOTAL += 1
    if cond:
        print(f"  ok     {que}")
    else:
        FALLOS.append(que)
        print(f"  FALLA  {que}" + (f"  -> {str(detalle)[:400]}" if detalle else ""))


def codigos(problemas) -> list[str]:
    return sorted({c for c, _m in problemas})


def seccion(t: str) -> None:
    print(f"\n{t}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    datos = json.loads((AQUI / "casos_revisar_prompts.json").read_text(encoding="utf-8"))
    casos = {c["nombre"]: c for c in datos["casos"]}

    seccion("1. Casos compartidos con el dash")
    comprobar(datos["reglas_version"] == R.REGLAS_VERSION, "los casos son de la misma version de las reglas")
    for c in datos["casos"]:
        got = codigos(R.revisar_imagen(c["prompt"], c["refs"]) if c["tipo"] == "imagen" else R.revisar_video(c["prompt"], c["duracion"]))
        comprobar(got == c["espera"], f"{c['tipo']}: {c['nombre']}", f"espera {c['espera']} y da {got}")
    comprobar(R.rango_palabras(8) == (25, 30) and R.rango_palabras(6) == (19, 22) and R.rango_palabras(10) == (32, 37) and R.rango_palabras(8, 12, 15) == (12, 15),
              "palabras por clip: 25 a 30 en 8 s, 19 a 22 en 6 s, 32 a 37 en 10 s; 12 a 15 por toma")

    seccion("2. Lo que solo mira el editor")
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        d_prod = t / "public" / "productos" / "velas"
        (d_prod / "paginas").mkdir(parents=True)
        (d_prod / "avatares" / "lucia").mkdir(parents=True)
        for f in (d_prod / "portada.jpg", d_prod / "paginas" / "p003.jpg", d_prod / "avatares" / "lucia" / "foto1.jpg"):
            f.write_bytes(b"\xff\xd8\xff")
        (t / "_prompts").mkdir()
        (t / "_prompts" / "ref_p003.jpg").write_bytes(b"\xff\xd8\xff")
        (t / "_prompts" / "ref_foto1.jpg").write_bytes(b"\xff\xd8\xff")
        cuadro2 = casos["avatar: cuadro 2 con el producto (plantilla 2) y 3 referencias"]["prompt"]
        avatar, ancla = str(d_prod / "avatares" / "lucia" / "foto1.jpg"), str(t / "cuadros" / "01.png")
        comprobar(codigos(R.revisar_imagen(cuadro2, [avatar, ancla, str(d_prod / "paginas" / "p003.jpg")], d_prod)) == [],
                  "producto: una pagina de paginas/ cuenta como foto del producto")
        comprobar(codigos(R.revisar_imagen(cuadro2, [avatar, ancla, str(d_prod / "portada.jpg")], d_prod)) == [], "producto: la portada cuenta como foto del producto")
        comprobar(codigos(R.revisar_imagen(cuadro2, [str(t / "_prompts" / "ref_foto1.jpg"), ancla, str(t / "_prompts" / "ref_p003.jpg")], d_prod)) == [],
                  "producto: la copia achicada que hacen gancho_ia y avatar_auto (ref_p003.jpg) cuenta")
        comprobar(codigos(R.revisar_imagen(cuadro2, [avatar, ancla, str(t / "_prompts" / "ref_foto1.jpg")], d_prod)) == ["producto_sin_ref"],
                  "producto: tres referencias pero ninguna es del producto (avatar, ancla y otra foto del avatar): no se paga")
        comprobar(codigos(R.revisar_imagen(cuadro2, [avatar, ancla, "https://upload.apimart.ai/f/x.png"], d_prod)) == [],
                  "producto: una direccion https no se puede mirar: con referencias, pasa (el dash solo cuenta)")
        comprobar(codigos(R.revisar_imagen(cuadro2, [avatar, ancla, str(d_prod / "avatares" / "lucia" / "foto1.jpg")], d_prod)) == ["producto_sin_ref"],
                  "producto: una foto de avatares/ dentro de la carpeta del producto no es foto del producto")
        comprobar(codigos(R.revisar_imagen(cuadro2, [avatar, ancla, "x.jpg"], t / "no-existe")) == [], "producto: si la carpeta del producto no esta en este equipo, solo se cuenta")

        clips = t / "clips"
        clips.mkdir()
        c1 = casos["avatar: clip 1 de 29 palabras (plantilla 3)"]["prompt"]
        c2 = casos["avatar: clip 2 de 26 palabras"]["prompt"]
        ch = casos["avatar hombre"]["prompt"]
        (clips / "01_prompt.txt").write_text(c1 + '\n\n# parametros: {"duracion": 8}\n', encoding="utf-8")
        (clips / "02_prompt.txt").write_text(casos["gancho: clip mudo (plantilla 5)"]["prompt"], encoding="utf-8")
        hermanos = R.prompts_hermanos(clips, clips / "03_prompt.txt")
        comprobar([n for n, _ in hermanos] == ["01_prompt.txt", "02_prompt.txt"] and "# parametros" not in hermanos[0][1], "los prompts hermanos: sin el renglon de parametros")
        comprobar(codigos(R.revisar_video(c2, 8, hermanos)) == [], "VOICE y ACCENT: el clip 2 con la misma voz que el 01 pasa (el mudo no cuenta)")
        r = R.revisar_video(ch, 8, hermanos)
        comprobar(codigos(r) == ["voz_distinta"] and "01_prompt.txt" in r[0][1], "VOICE y ACCENT: otro avatar en la misma carpeta no se paga y dice con cual choca", r)
        comprobar(codigos(R.revisar_video(c1, 8, R.prompts_hermanos(clips, clips / "01_prompt.txt"))) == [], "VOICE y ACCENT: un clip no se compara consigo mismo (01_prompt.txt)")
        lock = c1.replace("VOICE:", "ACCENT LOCK: paisa from Medellin, keep it in every word.\nVOICE:", 1)
        comprobar(codigos(R.revisar_video(lock, 8, hermanos)) == ["voz_distinta"], "VOICE y ACCENT: si un clip trae ACCENT LOCK y los otros no, no son identicos")
        comprobar(R.bloques_voz(c1) == R.bloques_voz(c2) and set(R.bloques_voz(c1)) == {"VOICE", "ACCENT"}, "bloques_voz: VOICE y ACCENT del avatar, sin el dialogo")

        # un clip repetido con otro nombre (01_v2, o 01_b del reintento de avatar_auto) es el MISMO clip, como en el dash (baseArchivo de functions/_editor-apimart.js)
        comprobar([R.clip_y_vuelta(x) for x in ("01_v2_prompt.txt", "01_b.mp4", "01_prompt.txt", "C:/x/03_V12.MP4", "01_b_v2.mp4")]
                  == [("01", 2), ("01", 1), ("01", 0), ("03", 12), ("01_b", 2)], "clip_y_vuelta: 01_v2, 01_b y 01 son el mismo clip (como baseArchivo del dash)")
        rep = t / "clips_repetidos"
        rep.mkdir()
        (rep / "01_prompt.txt").write_text(c1, encoding="utf-8")                      # el 01 salio con la voz de la mujer...
        (rep / "01_v2_prompt.txt").write_text(ch + '\n\n# parametros: {"duracion": 8}\n', encoding="utf-8")   # ...y se repitio como 01_v2 con la del hombre
        comprobar(codigos(R.revisar_video(c1, 8, R.prompts_hermanos(rep, rep / "01_v3_prompt.txt"))) == [],
                  "repetir el 01 como 01_v3 con OTRA voz: no se compara con ninguna version del 01 (01, 01_v2), igual que el dash al repetir")
        h03 = R.prompts_hermanos(rep, rep / "03_prompt.txt")
        comprobar([n for n, _ in h03] == ["01_v2_prompt.txt"] and codigos(R.revisar_video(ch, 8, h03)) == [],
                  "el clip 03 se compara con la version mas nueva del 01 (01_v2), no con la vieja", [n for n, _ in h03])
        r = R.revisar_video(c1, 8, h03)
        comprobar(codigos(r) == ["voz_distinta"] and "01_v2_prompt.txt" in r[0][1], "...y con la voz vieja del 01 no se paga (dice que choca con 01_v2)", r)
        (rep / "01_b_prompt.txt").write_text(c1, encoding="utf-8")                    # una vuelta _b (el reintento de avatar_auto) cuenta menos que _v2
        comprobar([n for n, _ in R.prompts_hermanos(rep, rep / "03_prompt.txt")] == ["01_v2_prompt.txt"], "01_b es la vuelta 1: la 01_v2 sigue siendo la mas nueva")
        comprobar(codigos(R.revisar_video(ch, 8, R.prompts_hermanos(rep, rep / "01_b_prompt.txt"))) == [] and
                  codigos(R.revisar_video(ch, 8, R.prompts_hermanos(rep, rep / "01_prompt.txt"))) == [], "repetir con el mismo nombre (01) o como 01_b tampoco se compara con el 01")

        # el uso a mano
        archivo = t / "p.txt"
        archivo.write_text("A woman in her kitchen talking about the ebook", encoding="utf-8")
        salida = subprocess.run([sys.executable, str(AQUI / "revisar_prompts.py"), "imagen", str(archivo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        comprobar(salida.returncode == 1 and "ÁNGULO" in salida.stdout and "no cumple" in salida.stdout, "a mano: un prompt vago sale con 1 y la lista", salida.stdout[-300:])
        archivo.write_text(casos["imagen completa escrita a mano"]["prompt"], encoding="utf-8")
        salida = subprocess.run([sys.executable, str(AQUI / "revisar_prompts.py"), "imagen", str(archivo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        comprobar(salida.returncode == 0 and "cumple" in salida.stdout, "a mano: uno completo sale con 0", salida.stdout[-300:])
        archivo.write_text(ch, encoding="utf-8")
        salida = subprocess.run([sys.executable, str(AQUI / "revisar_prompts.py"), "video", str(archivo), "--carpeta", str(clips)], capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        comprobar(salida.returncode == 1 and "01_prompt.txt" in salida.stdout, "a mano: video con --carpeta compara la voz con los clips que ya hay", salida.stdout[-300:])

        seccion("3. apimart.py con el APIMart falso: lo que no cumple no se pide")
        env = {**os.environ, "APIMART_BASE_URL": "http://127.0.0.1:9", "APIMART_API_KEY": "llave-de-prueba-0123456789", "APIMART_PRUEBA_DIR": str(t), "PYTHONIOENCODING": "utf-8"}
        env.pop("DASH_URL", None)
        env.pop("DASH_CLAVE_EDITOR", None)
        archivo.write_text("A woman in her kitchen talking about the ebook, cinematic 4K", encoding="utf-8")
        r = subprocess.run([sys.executable, str(AQUI / "apimart.py"), "imagen", "--prompt-archivo", str(archivo), "--salida", str(t / "cuadros" / "02.png"), "--producto", "velas",
                            "--anuncio", "ADX"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        texto = r.stdout + r.stderr
        comprobar(r.returncode != 0 and "No genero nada" in texto and "cinematic" in texto and "ÁNGULO" in texto and "No se gasto nada" in texto,
                  "imagen: un prompt vago sale con la lista ANTES de llamar (el APIMart falso en el puerto 9 ni siquiera existe)", texto[-400:])
        comprobar(not (t / "costos.jsonl").exists() and not (t / "cuadros" / "02.png").exists(), "imagen: no anota costo ni deja archivo")
        r = subprocess.run([sys.executable, str(AQUI / "apimart.py"), "imagen", "--prompt-archivo", str(archivo), "--salida", str(t / "cuadros" / "02.png"), "--producto", "velas",
                            "--anuncio", "ADX", "--dry-run"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        comprobar(r.returncode != 0 and "No genero nada" in r.stdout + r.stderr, "imagen --dry-run: tambien revisa (muestra lo que falta)", (r.stdout + r.stderr)[-300:])
        archivo.write_text(ch, encoding="utf-8")
        r = subprocess.run([sys.executable, str(AQUI / "apimart.py"), "video", "--imagen", str(d_prod / "portada.jpg"), "--prompt-archivo", str(archivo), "--salida", str(clips / "03.mp4"),
                            "--producto", "velas", "--anuncio", "ADX"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        texto = r.stdout + r.stderr
        comprobar(r.returncode != 0 and "01_prompt.txt" in texto and "No se gasto nada" in texto, "video: otra voz en la carpeta de clips del anuncio no se pide", texto[-400:])
        archivo.write_text(casos["20 palabras en 8 s"]["prompt"], encoding="utf-8")
        r = subprocess.run([sys.executable, str(AQUI / "apimart.py"), "video", "--imagen", str(d_prod / "portada.jpg"), "--prompt-archivo", str(archivo), "--salida", str(t / "otro" / "01.mp4"),
                            "--producto", "velas", "--anuncio", "ADX", "--duracion", "8"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        texto = r.stdout + r.stderr
        comprobar(r.returncode != 0 and "20 palabras" in texto and "de 25 a 30" in texto, "video: 20 palabras en 8 s no se pide y dice cuantas van", texto[-400:])
        r = subprocess.run([sys.executable, str(AQUI / "apimart.py"), "video", "--imagen", str(d_prod / "portada.jpg"), "--prompt-archivo", str(archivo), "--salida", str(t / "otro" / "01.mp4"),
                            "--producto", "velas", "--anuncio", "ADX", "--duracion", "6", "--dry-run"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        comprobar(r.returncode == 0 and "DRY-RUN" in r.stdout, "video: las mismas 20 palabras en un clip de 6 s si pasan (--dry-run, sin gastar)", (r.stdout + r.stderr)[-300:])

    seccion("4. Los prompts que arma el codigo")
    av = {"genero": "mujer", "casa_base": "a small bright kitchen with a wooden table and plants on the windowsill", "voz": "a warm female voice in her thirties, medium pitch",
          "acento": "Colombian Spanish with a clear paisa accent from Medellin", "fotos": ["foto1.jpg"]}
    prod = {"fotos": [Path("p003.jpg")], "descripcion": "a printed ebook with a red cover", "solo_portada": False}
    frases = ["¿Quieres aprender a hacer moldes sin gastar de más? Hoy te cuento cómo empezar.", "Muchas personas se rinden porque no saben por dónde empezar con sus proyectos.",
              "Con este ebook sigues cada paso con fotos claras y materiales sencillos de conseguir.", "Trae plantillas listas para imprimir y una guía para cada proyecto del libro.",
              "Pídelo hoy y empieza tu primer proyecto este mismo fin de semana en casa.", "Escríbenos por WhatsApp y te enviamos el ebook completo ahora mismo."]
    textos = AC.partir_en_clips(frases)
    escenas = ["Three-quarter angle, medium shot, at the wooden kitchen table, she is cutting paper templates with scissors, smiling at the camera",
               "Side view, waist up, on the living room sofa, she is flipping through the printed ebook on her lap, a curious look",
               "Low angle, medium close-up, at the balcony table, she is arranging finished felt ornaments in a basket, a proud smile"]
    clips = [{"n": i, "texto": x, "escena": escenas[i - 1], "accion": "Lifting a felt star toward the lens", "usa_producto": i == 2} for i, x in enumerate(textos, 1)]
    import avatar_auto as AA
    comprobar(AA.revisar_prompts_del_anuncio(clips, "a sage green blouse and jeans", av, prod) == [], "avatar: un anuncio con escenas completas pasa entero (3 cuadros y 3 clips)",
              AA.revisar_prompts_del_anuncio(clips, "a sage green blouse and jeans", av, prod))
    malo = [dict(c) for c in clips]
    malo[2]["escena"] = "In the kitchen, she talks about the ebook"
    p = AA.revisar_prompts_del_anuncio(malo, "a sage green blouse and jeans", av, prod)
    comprobar(p and all(x.startswith("cuadro 3:") for x in p) and any("ÁNGULO" in x for x in p), "avatar: una escena vaga en el clip 3 se detecta ANTES de pagar el cuadro 1", p)
    corto = [dict(c) for c in clips]
    corto[0]["texto"] = "Mira, hoy te cuento cómo empezar con tus moldes sin gastar de más."
    p = AA.revisar_prompts_del_anuncio(corto, "a sage green blouse and jeans", av, prod)
    comprobar(any(x.startswith("clip 1:") and "de 25 a 30" in x for x in p), "avatar: un clip de 13 palabras no se paga", p)

    # ---- el avatar cuenta las palabras como la revision: una raya suelta no es una palabra
    comprobar(AC.palabras_de("Hola — amiga, mira esto … ya") == R.palabras("Hola — amiga, mira esto … ya") == 5, "avatar: la raya y los puntos sueltos no cuentan como palabras (igual que la revision)",
              str(AC.palabras_de("Hola — amiga, mira esto … ya")))
    doce = "Mira amiga hoy te cuento algo que me cambió las tardes ya."
    try:
        partido = AC.partir_en_clips([doce, doce.replace("Mira", "Y —", 1)])
    except AC.ErrorAvatar:
        partido = None
    comprobar(partido is None, "avatar: 12 + 12 palabras con una raya no se arman como un clip de «25» que la revision cuenta en 24", str(partido))

    # ---- tiempos: nada crece al cuadrado (comillas sin cerrar, «place:» pegados) y lo que pasa del tope se niega sin revisar
    import time
    for que, grande in [("“ sin cerrar", "“" * 4000), ("« sin cerrar", "«" * 4000), ("“« mezcladas", "“«" * 2000), ('una " al principio', '"' + "a" * 3999),
                        ("place: pegados", ("place:" * 700)[:4000]), ("comillas cerradas", ('"a" ' * 1000)[:4000])]:
        t0 = time.perf_counter()
        R.revisar_imagen(grande, 2)
        R.revisar_video(grande, 8)
        seg = time.perf_counter() - t0
        comprobar(seg < 0.2, f"tiempo: un prompt de 4.000 letras con {que} se revisa en menos de 0,2 s", f"{seg:.3f} s")
    comprobar([c for c, _ in R.revisar_imagen("a " * 2500, 0)] == ["largo"] and [c for c, _ in R.revisar_video("a " * 2500, 8)] == ["largo"],
              "un prompt de mas de 4.000 letras se niega por largo (el tope del dash)")

    print("\n" + ("AUTOPRUEBA OK" if not FALLOS else f"FALLAN {len(FALLOS)}") + f" ({TOTAL} comprobaciones)")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
