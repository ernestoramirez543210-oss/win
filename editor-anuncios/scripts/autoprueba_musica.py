"""Autoprueba de scripts/crear_musica.py SIN internet y sin tocar tu biblioteca de musica (todo pasa en una carpeta temporal).

Comprueba:
  - la teoria del plan: tempo y tonalidad dentro del perfil de cada estilo, el colchon y el arpegio solo con notas del acorde de su compas, el bajo en la raiz (o la quinta), las campanas
    solo con notas de la escala (y en los tiempos fuertes, del acorde), nada despues del final y solo el colchon en el cierre; la misma semilla da el mismo plan y otra semilla, otro;
  - el sonido de cada estilo (con una pista corta): largo exacto, estereo, pico a -3 dBFS, sin numeros rotos, sin saturar, sin huecos de silencio, entrada y salida suaves, espectro
    razonable (ni solo graves ni solo agudos) y algo de correlacion entre los dos oidos (para que no suene hueco en un celular);
  - la linea de comandos: crea las pistas en <estilo>/<estilo>_NN.mp3 (mp3 estereo de 48 kHz del largo pedido), anota LICENCIAS.txt, no repite lo que ya esta, --sobrescribir lo rehace y
    un estilo o una cantidad mala se rechazan;
  - que el selector de musica del montaje automatico (montaje_auto.elegir_musica) encuentra esas pistas por estilo;
  - la biblioteca compartida en Drive (con una carpeta falsa de Drive, nunca la tuya): lo nuevo sube solo con su LEEME y su LICENCIAS, volver a correr no repite nada, una pista distinta
    que ya esta no se pisa, --sincronizar baja y sube lo que falta sin crear pistas, los .tmp a medias y las carpetas _prueba no viajan, una copia que falla se avisa y da error, otra --carpeta
    nunca busca Drive sola, y un PC nuevo con la biblioteca vacia baja la de Drive en vez de rehacerla.

Uso:  python scripts/autoprueba_musica.py
Termina con "AUTOPRUEBA OK" si todo va bien.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import configurar_consola, ejecutable  # noqa: E402

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

import crear_musica as cm  # noqa: E402

FALLAS: list[str] = []


def comprobar(cond, nombre: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok     {nombre}")
    else:
        FALLAS.append(nombre)
        print(f"  FALLA  {nombre}" + (f"  ->  {detalle}" if detalle else ""))


def sale(fn, *args, **kw) -> bool:
    """True si fn termina con un mensaje de error de los scripts (SystemExit). El mensaje no se imprime."""
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            fn(*args, **kw)
        except SystemExit:
            return True
    return False


def correr_main(*argumentos: str) -> str:
    """Corre cm.main() con esos argumentos y devuelve lo que imprimio."""
    antes = sys.argv
    sys.argv = ["crear_musica.py", *argumentos]
    salida = io.StringIO()
    try:
        with contextlib.redirect_stdout(salida):
            cm.main()
    finally:
        sys.argv = antes
    return salida.getvalue()


def correr_main_con_codigo(*argumentos: str) -> tuple[int, str]:
    """Como correr_main, pero tambien devuelve el codigo de salida (0 si termino bien, el de SystemExit si el script se detuvo)."""
    antes = sys.argv
    sys.argv = ["crear_musica.py", *argumentos]
    salida = io.StringIO()
    codigo = 0
    try:
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(io.StringIO()):
            try:
                cm.main()
            except SystemExit as e:
                codigo = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = antes
    return codigo, salida.getvalue()


def a_wav(mp3: Path) -> tuple[np.ndarray, int]:
    """El mp3 decodificado: (muestras estereo float, tasa)."""
    wav = mp3.with_suffix(".leer.wav")
    r = subprocess.run([ejecutable("ffmpeg"), "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "2", "-ar", "48000", str(wav)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with wave.open(str(wav), "rb") as w:
        datos = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768.0
        sr = w.getframerate()
    wav.unlink(missing_ok=True)
    return datos, sr


def sonda(mp3: Path) -> dict:
    r = subprocess.run([ejecutable("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name,channels,sample_rate:format=duration", "-of", "json", str(mp3)], capture_output=True, text=True)
    j = json.loads(r.stdout)
    return {"codec": j["streams"][0]["codec_name"], "canales": int(j["streams"][0]["channels"]), "tasa": int(j["streams"][0]["sample_rate"]), "dur": float(j["format"]["duration"])}


def prueba_teoria() -> None:
    print("\n[1] el plan de cada estilo (solo teoria, sin sonido)")
    for estilo in cm.ESTILOS:
        P = cm.PERFILES[estilo]
        planes = [cm.componer(estilo, cm.semilla_de(1, estilo, k), 60.0) for k in (1, 2, 3)]
        comprobar(all(P["bpm"][0] <= p["bpm"] <= P["bpm"][1] and p["tonica"] in P["tonicas"] and p["modo"] in P["modos"] for p in planes), f"{estilo}: tempo, tonica y modo dentro de su perfil",
                  str([(p["bpm"], p["tonica"], p["modo"]) for p in planes]))
        malas = []
        fuertes = ok_fuertes = 0
        for p in planes:
            compas = p["compas_s"]
            for e in p["notas"]:
                c = min(int(e["t"] / compas + 1e-3), len(p["acordes"]) - 1)        # (las horas del plan vienen redondeadas: la tolerancia evita caer en el compas de al lado)
                raiz, tipo = p["acordes"][c]
                acorde = {(raiz + i) % 12 for i in cm.TRIADA[tipo]}
                if e["inst"] in ("pad", "arpegio") and e["m"] % 12 not in acorde:
                    malas.append((estilo, e["inst"], round(e["t"], 2), e["m"] % 12, sorted(acorde)))
                if e["inst"] == "bajo" and not (36 <= e["m"] <= 59 and e["m"] % 12 in (raiz, (raiz + 7) % 12)):
                    malas.append((estilo, "bajo", round(e["t"], 2), e["m"]))
                if e["inst"] == "campana":
                    if e["m"] % 12 not in p["escala_pcs"]:
                        malas.append((estilo, "campana fuera de escala", round(e["t"], 2), e["m"] % 12))
                    en_compas = (e["t"] - c * compas) / (compas / 4.0)
                    if abs(en_compas - round(en_compas)) < 1e-3 and round(en_compas) in (0, 2):
                        fuertes += 1
                        ok_fuertes += 1 if e["m"] % 12 in acorde else 0
                if not (0.0 <= e["t"] < p["segundos"] + 1.0 and e["dur"] > 0 and np.isfinite(e["vel"]) and 0.0 < e["vel"] < 2.0 and -1.0 <= e["pan"] <= 1.0):
                    malas.append((estilo, "nota rara", e))
                if e["inst"] != "pad" and e["t"] >= p["segundos"] - 3.0 + 1e-6:
                    malas.append((estilo, "suena algo mas que el colchon en el cierre", e["inst"], round(e["t"], 2)))
        comprobar(not malas, f"{estilo}: colchon y arpegio con notas del acorde, bajo en raiz o quinta, campanas en la escala, notas dentro de la pista y solo el colchon al final", str(malas[:3]))
        comprobar(fuertes == 0 or ok_fuertes / fuertes >= 0.85, f"{estilo}: las campanas de los tiempos fuertes son del acorde (al menos el 85 %)", f"{ok_fuertes} de {fuertes}")
        capas = {e["inst"] for e in planes[0]["notas"]}
        comprobar({"pad", "arpegio", "bajo"} <= capas and ("campana" in capas), f"{estilo}: trae colchon, arpegio, bajo y campanas", str(sorted(capas)))
    a, b = cm.componer("calida", 7, 30.0), cm.componer("calida", 7, 30.0)
    comprobar(a == b and cm.componer("calida", 8, 30.0) != a, "la misma semilla da el mismo plan y otra semilla da otro")
    comprobar(cm.semilla_de(1, "calida", 1) != cm.semilla_de(1, "energica", 1) != cm.semilla_de(2, "energica", 1) and cm.semilla_de(1, "calida", 1) != cm.semilla_de(1, "calida", 2),
              "cada estilo, cada numero y cada semilla base dan una semilla distinta")
    en = cm.componer("energica", 3, 40.0)
    inst = {e["inst"] for e in en["notas"]}
    comprobar({"bombo", "palmada", "hat"} <= inst and "cascabel" not in inst and "cascabel" in {e["inst"] for e in cm.componer("navidad", 3, 40.0)["notas"]},
              "la energica trae bombo, palmada y hi-hat; los cascabeles son solo de la navidad")
    corto = cm.componer("calida", 4, 5.0)
    comprobar(len(corto["notas"]) > 0 and all(e["t"] < 6.0 for e in corto["notas"]), "una pista de 5 s (la mas corta) tambien sale")


def prueba_sonido() -> None:
    print("\n[2] el sonido de cada estilo (pistas de 12 s)")
    for estilo in cm.ESTILOS:
        plan = cm.componer(estilo, cm.semilla_de(9, estilo, 1), 12.0)
        izq, der = cm.renderizar(plan)
        n = len(izq)
        comprobar(n == 12 * cm.SR and len(der) == n and np.all(np.isfinite(izq)) and np.all(np.isfinite(der)), f"{estilo}: largo exacto (12 s), estereo y sin numeros rotos")
        pico = 20 * np.log10(max(np.max(np.abs(izq)), np.max(np.abs(der))))
        comprobar(abs(pico + 3.0) < 0.2, f"{estilo}: el pico queda a -3 dBFS (no se pasa de 0)", f"{pico:.2f}")
        mono = (izq + der) / 2.0
        rms = lambda x: float(np.sqrt(np.mean(x ** 2)) + 1e-12)  # noqa: E731
        medio = rms(mono[2 * cm.SR: 9 * cm.SR])
        comprobar(rms(mono[: int(0.1 * cm.SR)]) < 0.3 * medio and rms(mono[-int(0.1 * cm.SR):]) < 0.3 * medio, f"{estilo}: entrada y salida suaves (el primer y el ultimo decimo de segundo casi no suenan)")
        por_segundo = [20 * np.log10(rms(mono[i * cm.SR:(i + 1) * cm.SR])) for i in range(1, 9)]
        comprobar(min(por_segundo) > -45 and max(por_segundo) - min(por_segundo) < 12, f"{estilo}: sin huecos de silencio ni saltos de volumen entre el segundo 1 y el 9", f"{[round(x) for x in por_segundo]}")
        cresta = 20 * np.log10(np.max(np.abs(mono)) / rms(mono))
        comprobar(7 < cresta < 18, f"{estilo}: ni aplastada ni con picos sueltos (cresta {cresta:.1f} dB)")
        f, pxx = signal.welch(mono, cm.SR, nperseg=8192)
        total = float(np.sum(pxx))
        banda = lambda a, b: 10 * np.log10(float(np.sum(pxx[(f >= a) & (f < b)])) / total + 1e-18)  # noqa: E731
        centro = float(np.sum(f * pxx) / total)
        comprobar(300 < centro < 1500 and banda(2500, 6000) > -30 and banda(80, 250) > -30 and banda(250, 800) > -12,
                  f"{estilo}: espectro razonable (centro {centro:.0f} Hz; ni solo graves ni solo agudos)", f"centro {centro:.0f}, presencia {banda(2500, 6000):.1f} dB, graves {banda(80, 250):.1f} dB")
        corr = float(np.corrcoef(izq, der)[0, 1])
        comprobar(0.2 < corr < 0.95, f"{estilo}: los dos oidos se parecen lo justo ({corr:.2f}): ancho pero sin sonar hueco en un celular")
    i1, d1 = cm.renderizar(cm.componer("navidad", 5, 8.0))
    i2, d2 = cm.renderizar(cm.componer("navidad", 5, 8.0))
    i3, _ = cm.renderizar(cm.componer("navidad", 6, 8.0))
    comprobar(np.array_equal(i1, i2) and np.array_equal(d1, d2) and not np.array_equal(i1, i3), "la misma semilla da exactamente el mismo audio y otra semilla da otro")


def prueba_linea_de_comandos(tmp: Path) -> None:
    print("\n[3] la linea de comandos y la biblioteca")
    biblio = tmp / "biblioteca"
    salida = correr_main("--estilos", "calida,navidad", "--cuantas", "2", "--segundos", "8", "--carpeta", str(biblio), "--analizar")
    archivos = sorted(p.relative_to(biblio).as_posix() for p in biblio.rglob("*.mp3"))
    comprobar(archivos == ["calida/calida_01.mp3", "calida/calida_02.mp3", "navidad/navidad_01.mp3", "navidad/navidad_02.mp3"] and "LISTO: 4 pista(s) nueva(s)" in salida, "crea <estilo>/<estilo>_NN.mp3 por cada estilo y cantidad pedidos", f"{archivos}")
    s = sonda(biblio / "calida" / "calida_01.mp3")
    comprobar(s["codec"] == "mp3" and s["canales"] == 2 and s["tasa"] == 48000 and abs(s["dur"] - 8.0) < 0.2, "cada pista es un mp3 estereo de 48 kHz del largo pedido", f"{s}")
    datos, sr = a_wav(biblio / "navidad" / "navidad_02.mp3")
    comprobar(sr == 48000 and abs(len(datos) / sr - 8.0) < 0.2 and float(np.max(np.abs(datos))) < 0.99, "el mp3 se lee de vuelta, dura 8 s y no se pasa de 0 dBFS")
    comprobar("LUFS" in salida and "pico -3.0 dBFS" in salida, "--analizar dice la sonoridad y el pico de cada pista nueva", salida[-300:])
    lic = (biblio / "LICENCIAS.txt").read_text(encoding="utf-8")
    comprobar(lic.count("propia |") == 4 and "calida/calida_01.mp3" in lic and "semilla" in lic and "sin ninguna cancion" in lic, "LICENCIAS.txt anota cada pista propia con su semilla y dice que no hay nada de terceros")
    antes = {p: p.stat().st_mtime_ns for p in biblio.rglob("*.mp3")}
    salida2 = correr_main("--estilos", "calida,navidad", "--cuantas", "2", "--segundos", "8", "--carpeta", str(biblio))
    despues = {p: p.stat().st_mtime_ns for p in biblio.rglob("*.mp3")}
    comprobar(antes == despues and "ya estaba" in salida2 and "LISTO: 0 pista(s) nueva(s)" in salida2 and (biblio / "LICENCIAS.txt").read_text(encoding="utf-8").count("propia |") == 4,
              "volver a correr no repite lo que ya esta ni duplica lo anotado")
    correr_main("--estilos", "calida", "--cuantas", "1", "--segundos", "8", "--carpeta", str(biblio), "--sobrescribir")
    comprobar((biblio / "calida" / "calida_01.mp3").stat().st_mtime_ns != antes[biblio / "calida" / "calida_01.mp3"], "--sobrescribir rehace la pista que ya estaba")
    comprobar(sale(correr_main, "--estilos", "rock", "--carpeta", str(biblio)) and sale(correr_main, "--cuantas", "0", "--carpeta", str(biblio)) and sale(correr_main, "--segundos", "2", "--carpeta", str(biblio)),
              "un estilo que no existe, 0 pistas o una duracion absurda se rechazan")
    # el selector de musica del montaje automatico encuentra estas pistas por estilo
    import montaje_auto as ma

    real = ma.PUBLIC
    montaje_biblio = tmp / "montaje" / "musica" / "biblioteca"
    shutil.copytree(biblio, montaje_biblio)
    ma.PUBLIC = tmp / "montaje"
    try:
        for estilo in ("calida", "navidad"):
            p = ma.elegir_musica({"musica": {"estilo": estilo}}, None, None)
            comprobar(p is not None and estilo in p.parts and p.suffix == ".mp3", f"montaje_auto.elegir_musica con estilo «{estilo}» elige una pista de esa carpeta", str(p))
        primera = ma.elegir_musica({"musica": {"estilo": "calida"}}, None, None)
        siguiente = ma.elegir_musica({"musica": {"estilo": "calida"}}, None, {"musica": str(primera)})
        comprobar(siguiente is not None and siguiente.name != primera.name, "y el anuncio siguiente no repite la misma pista")
        sin_pistas = ma.elegir_musica({"musica": {"estilo": "energica"}}, None, None)
        comprobar(sin_pistas is not None and sin_pistas.parent.name in ("calida", "navidad"), "si no hay del estilo pedido usa otra de la biblioteca (como siempre)")
        comprobar(ma.elegir_musica({"musica": {"estilo": "sin-musica"}}, None, None) is None, "con «sin musica» no usa ninguna")
    finally:
        ma.PUBLIC = real


def prueba_drive(tmp: Path) -> None:
    print("\n[4] la biblioteca musical en Drive (con una carpeta falsa de Drive: la tuya no se toca)")
    biblio = tmp / "d_biblioteca"
    nube = tmp / "d_nube" / "BIBLIOTECA MUSICAL"
    nube.mkdir(parents=True)
    base = ["--estilos", "calida,navidad", "--cuantas", "1", "--segundos", "6", "--carpeta", str(biblio)]

    def tamanos(raiz: Path) -> dict[str, int]:
        return {p.relative_to(raiz).as_posix(): p.stat().st_size for p in raiz.rglob("*.mp3")}

    def fotos(raiz: Path) -> dict[Path, int]:
        return {p: p.stat().st_mtime_ns for p in raiz.rglob("*") if p.is_file()}

    # lo nuevo sube solo
    correr_main(*base, "--drive", str(nube))
    comprobar(sorted(tamanos(biblio)) == ["calida/calida_01.mp3", "navidad/navidad_01.mp3"] and tamanos(nube) == tamanos(biblio),
              "con --drive, cada pista nueva queda tambien en <Drive>/<estilo>/, entera (mismo tamano)", f"{tamanos(biblio)} {tamanos(nube)}")
    lic_nube = (nube / "LICENCIAS.txt").read_text(encoding="utf-8") if (nube / "LICENCIAS.txt").is_file() else ""
    comprobar((nube / "LEEME.txt").is_file() and "BIBLIOTECA MUSICAL" in (nube / "LEEME.txt").read_text(encoding="utf-8") and lic_nube.count("propia |") == 2 and "semilla" in lic_nube,
              "Drive recibe un LEEME.txt y un LICENCIAS.txt con la linea de cada pista propia (con su semilla)")
    comprobar(not list(nube.rglob("*.tmp")), "no queda ningun .tmp en Drive (se copia a un .tmp y se renombra)")

    # volver a correr no repite nada
    antes = fotos(nube)
    salida2 = correr_main(*base, "--drive", str(nube))
    comprobar(antes == fotos(nube) and "de aqui a Drive: 0 pista(s) copiada(s), 2 ya estaban" in salida2 and (nube / "LICENCIAS.txt").read_text(encoding="utf-8").count("propia |") == 2,
              "volver a correr no sube dos veces lo mismo ni duplica lo anotado")

    # una pista distinta que ya esta no se pisa
    pista = nube / "calida" / "calida_01.mp3"
    original = pista.read_bytes()
    pista.write_bytes(b"otra cosa")
    salida3 = correr_main(*base, "--drive", str(nube))
    comprobar(pista.read_bytes() == b"otra cosa" and (biblio / "calida" / "calida_01.mp3").stat().st_size == len(original) and "AVISO: calida/calida_01.mp3" in salida3,
              "si en Drive hay otra pista con ese nombre y otro tamano, no la pisa (ni aqui ni alla) y avisa")
    pista.write_bytes(original)

    # --sincronizar: baja lo que falta aqui y sube lo que falta alla, sin crear pistas
    (nube / "emotiva").mkdir()
    shutil.copy2(biblio / "calida" / "calida_01.mp3", nube / "emotiva" / "mia.mp3")                         # alguien suelta un mp3 en Drive
    (biblio / "energica").mkdir()
    shutil.copy2(biblio / "navidad" / "navidad_01.mp3", biblio / "energica" / "suya.mp3")                   # y otra persona uno en la carpeta del editor
    (nube / "calida" / "a_medias.mp3.tmp").write_bytes(b"x")                                                # una copia a medias no cuenta
    (biblio / "_prueba").mkdir()
    shutil.copy2(biblio / "calida" / "calida_01.mp3", biblio / "_prueba" / "ensayo.mp3")                    # ni las carpetas de ensayo
    codigo, salida4 = correr_main_con_codigo("--sincronizar", "--carpeta", str(biblio), "--drive", str(nube))
    comprobar(codigo == 0 and (biblio / "emotiva" / "mia.mp3").is_file() and (nube / "energica" / "suya.mp3").is_file(), "--sincronizar baja lo que falta aqui y sube lo que falta en Drive", salida4[-300:])
    comprobar(not (biblio / "calida" / "calida_02.mp3").exists() and "no se crea ninguna pista" in salida4 and "LISTO: la biblioteca" in salida4, "y no crea ninguna pista nueva")
    comprobar(not (biblio / "calida" / "a_medias.mp3.tmp").exists() and not (nube / "_prueba").exists(), "un .tmp a medias y las carpetas _prueba no viajan")
    lic_local = (biblio / "LICENCIAS.txt").read_text(encoding="utf-8")
    lic_nube = (nube / "LICENCIAS.txt").read_text(encoding="utf-8")
    comprobar("sin anotar | emotiva/mia.mp3" in lic_local and "sin anotar | energica/suya.mp3" in lic_nube and lic_local.count("propia |") == 2 and lic_nube.count("propia |") == 2,
              "lo que llega de afuera se anota como «sin anotar» (para que alguien ponga su fuente) y las pistas propias no se duplican")

    # una copia que falla se avisa y el comando termina con error (el resto si se copia)
    (nube / "emotiva" / "bloqueada.mp3").mkdir()                                                             # en Drive hay una CARPETA con el nombre de una pista
    shutil.copy2(biblio / "calida" / "calida_01.mp3", biblio / "emotiva" / "bloqueada.mp3")
    shutil.copy2(biblio / "calida" / "calida_01.mp3", biblio / "emotiva" / "otra.mp3")
    codigo, salida5 = correr_main_con_codigo("--sincronizar", "--carpeta", str(biblio), "--drive", str(nube))
    comprobar(codigo == 1 and "AVISO: no se pudo copiar emotiva/bloqueada.mp3" in salida5 and (nube / "emotiva" / "otra.mp3").is_file() and not list(nube.rglob("*bloqueada*.tmp")),
              "una copia que falla se avisa, termina con error, no deja .tmp y no frena las demas", salida5[-300:])

    # biblioteca_en_drive: la ruta que se pasa, MUSICA_DRIVE_DIR y crear
    viejo = os.environ.get("MUSICA_DRIVE_DIR")
    try:
        os.environ["MUSICA_DRIVE_DIR"] = str(tmp / "env" / "BIBLIOTECA MUSICAL")
        sin_crear = cm.biblioteca_en_drive("")
        creada = cm.biblioteca_en_drive("", crear=True)
        explicita = cm.biblioteca_en_drive(str(nube))
        comprobar(sin_crear is None and creada is not None and creada.is_dir() and explicita == nube,
                  "biblioteca_en_drive: MUSICA_DRIVE_DIR vale si no hay ruta, no crea nada sin pedirlo y la ruta que se pasa le gana")
    finally:
        if viejo is None:
            os.environ.pop("MUSICA_DRIVE_DIR", None)
        else:
            os.environ["MUSICA_DRIVE_DIR"] = viejo

    # otra --carpeta nunca busca Drive sola; con la biblioteca de siempre si
    visto: list = []
    busca_real, public_real = cm.biblioteca_en_drive, cm.PUBLIC
    cm.biblioteca_en_drive = lambda *args, **kw: visto.append(args) or None
    try:
        correr_main("--estilos", "calida", "--cuantas", "1", "--segundos", "5", "--carpeta", str(tmp / "ensayo"))
        comprobar(not visto, "con otra --carpeta y sin --drive ni siquiera se busca Drive (una prueba nunca toca la biblioteca de Drive de verdad)")
        comprobar(sale(correr_main, "--sincronizar", "--carpeta", str(tmp / "ensayo")) and not visto, "--sincronizar con otra --carpeta y sin --drive se rechaza")
        comprobar(sale(correr_main, "--sin-drive", "--sincronizar", "--carpeta", str(tmp / "ensayo")) and sale(correr_main, "--sin-drive", "--drive", str(nube), "--carpeta", str(tmp / "ensayo")),
                  "--sin-drive no se mezcla con --sincronizar ni con --drive")
        nube2 = tmp / "d_nube2" / "BIBLIOTECA MUSICAL"
        nube2.mkdir(parents=True)
        cm.PUBLIC = tmp / "falso" / "public"                                                                 # «la biblioteca de siempre» de esta prueba (no la tuya)
        cm.biblioteca_en_drive = lambda dada="", crear=False: nube2
        correr_main("--estilos", "navidad", "--cuantas", "1", "--segundos", "5")
        en_falsa = cm.PUBLIC / "musica" / "biblioteca" / "navidad" / "navidad_01.mp3"
        comprobar(en_falsa.is_file() and (nube2 / "navidad" / "navidad_01.mp3").is_file(), "con la biblioteca de siempre, Drive se busca solo y recibe las pistas nuevas")
        shutil.rmtree(cm.PUBLIC / "musica")                                                                  # un PC recien instalado: biblioteca vacia, Drive con pistas
        salida6 = correr_main("--estilos", "navidad", "--cuantas", "1", "--segundos", "5")
        comprobar(en_falsa.is_file() and "LISTO: 0 pista(s) nueva(s)" in salida6 and "de Drive aqui: 1 pista(s) copiada(s)" in salida6, "un PC nuevo con la biblioteca vacia baja la de Drive en vez de rehacerla")
        cm.biblioteca_en_drive = lambda dada="", crear=False: None
        salida7 = correr_main("--estilos", "navidad", "--cuantas", "1", "--segundos", "5")
        comprobar("Drive no esta en este PC" in salida7 and "LISTO: 0 pista(s) nueva(s)" in salida7, "sin Drive en el PC, la musica sigue funcionando y solo avisa que queda aqui")
        comprobar(sale(correr_main, "--sincronizar"), "--sincronizar sin Drive en el PC se rechaza con un mensaje claro")
    finally:
        cm.biblioteca_en_drive, cm.PUBLIC = busca_real, public_real
    comprobar(cm.es_la_biblioteca_real(cm.PUBLIC / "musica" / "biblioteca") and not cm.es_la_biblioteca_real(tmp / "ensayo"), "es_la_biblioteca_real distingue la biblioteca de verdad de una de ensayo")


def main() -> None:
    configurar_consola()
    tmp = Path(tempfile.mkdtemp(prefix="autoprueba_musica_"))
    # Por si acaso: aunque alguna pieza fallara, ninguna prueba puede llegar a la biblioteca de Drive de verdad (MUSICA_DRIVE_DIR manda sobre la busqueda en las unidades de Windows).
    os.environ["MUSICA_DRIVE_DIR"] = str(tmp / "drive_por_si_acaso")
    try:
        prueba_teoria()
        prueba_sonido()
        prueba_linea_de_comandos(tmp)
        prueba_drive(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FALLAS:
        print(f"AUTOPRUEBA CON {len(FALLAS)} FALLA(S):")
        for f in FALLAS:
            print(f"  - {f}")
        sys.exit(1)
    print("AUTOPRUEBA OK")


if __name__ == "__main__":
    main()
