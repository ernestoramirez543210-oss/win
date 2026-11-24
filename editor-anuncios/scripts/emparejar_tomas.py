"""Emparejar tomas: indexa el b-roll de un producto POR CODIGO (CLIP local + detector de texto) y empareja cada frase del anuncio con la toma que mejor la ilustra.
Sin LLM: nadie mira hojas de contacto ni escribe planes; es la etapa S5c de mejora/anuncio-automatico.md (lo que antes hacia Claude con los ojos).

Uso:
  python scripts/emparejar_tomas.py <producto>                 llena broll-tiktok/indice.json de los videos nuevos o cambiados
  python scripts/emparejar_tomas.py <producto> --comparar      ademas compara lo automatico con lo que revisaron a mano (no cambia lo manual)
  python scripts/emparejar_tomas.py <producto> --recalcular    vuelve a calcular los tramos con los umbrales de ahora, con lo ya guardado (segundos, sin decodificar video)
  python scripts/emparejar_tomas.py <producto> --frases <archivo>   prueba el emparejamiento: por cada frase, las mejores tomas (archivo .txt con una
                                                                frase por linea, o una transcripcion.json / guion.json)
  python scripts/emparejar_tomas.py --solo-descargar           baja los modelos de CLIP (~315 MB, una sola vez)
  Opciones: --cada 1 (segundos entre fotogramas) · --rehacer (vuelve a analizar todo) · --solo parte_del_nombre · --sin-texto (sin detector de texto)
            --pisar-manual (deja lo automatico en `tramos` tambien donde una persona ya habia revisado; guarda lo viejo en `tramos_manual`)

Lo que el automatico no ve (un nino, un logo sin letras, una cara conocida) lo marca una persona y NUNCA se vuelve a usar, en ningun anuncio:
  python scripts/emparejar_tomas.py <producto> --marcar-toma AD1 7 --motivo "se ve un nino"    la toma #7 de evidencia.png (o varias: 7 9 12)
  python scripts/emparejar_tomas.py <producto> --marcar 05108697 17-21 --motivo "logo"         un tramo de un video (basta un pedazo del nombre); «todo» = el video entero
  python scripts/emparejar_tomas.py <producto> --marcas                                        lista las marcas · --quitar-marca <video> [tramo] las borra
  (--calidad media en vez de mala = «prefiere otra toma», pero si falta material se usa). Quedan en indice.json (`marcas`) y sobreviven a repetir el analisis.

Que hace por cada video (una pasada de ffmpeg + CLIP + detector de texto, todo en este PC o en el servidor, sin red):
  1. saca un fotograma cada `cada` s y, de paso, cuadros chicos a 6 por segundo para ver los CORTES de escena y cuanto se MUEVE la imagen;
  2. de cada fotograma toma la franja de 9:16 que se vera en el anuncio y la parte en 3 cuadrados (arriba, centro, abajo): CLIP saca un vector de cada uno;
  3. el detector de texto (RapidOCR) marca los fotogramas con texto quemado dentro de esa franja; CLIP marca los rostros en primer plano (se prefieren evitar);
  4. parte el video en tramos (entre cortes y donde cambia la calidad) y a cada uno le pone etiquetas en espanol, y calidad buena / media / mala;
  5. guarda lo liviano en indice.json (`tramos`, para leer) y los vectores en broll-tiktok/_clip/<video>.npz (para emparejar).
Aparte (al cargar el catalogo, no al analizar): `franjas_negras` mide con una pasada de ffmpeg si el video trae franjas negras arriba y abajo (muchos de TikTok las traen) y esos tramos
NUNCA se usan, como una marca `mala`; la respuesta queda en _clip/<video>.bandas.json mientras el archivo no cambie.
Si una persona (o Claude) ya reviso los tramos de un video a mano, NO se pisan: lo automatico queda en `tramos_auto` y montaje_auto.py respeta lo manual
(un tramo manual `mala` nunca se usa).

Limites (medidos con los 12 videos de Dinamicas, referencia emparejar-tomas.md): CLIP B/32 ve bien personas, manos, globos, vasos y lugares; NO distingue ninos de globos
ni reconoce logos sin letras: eso lo juzga una persona con la evidencia.png (y lo marcado `mala` a mano nunca se usa). El detector de texto no distingue un subtitulo de un
cartel de la escena (un cartel tambien se descarta) y a veces toma las lineas de un edificio por texto. La calidad `media` y `mala` son avisos: el montaje solo usa fotogramas limpios.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import ANUNCIOS, EXT_VIDEO, PUBLIC, configurar_consola, ejecutable, escribir_json, leer_json, producto_dir, salir  # noqa: E402

VERSION_ANALISIS = "1"            # sube si cambia COMO se calcula lo que se guarda por video: obliga a reanalizar
CADA_DEFECTO = 1.0                # segundos entre fotogramas
FPS_CHICOS = 6                    # cuadros chicos por segundo (cortes y movimiento)
CHICO = (96, 160)                 # tamano de esos cuadros (ancho, alto)
ALTO_HORIZONTAL = 720             # los videos horizontales se bajan a 720 de alto (la franja 9:16 del centro queda de 405 px)
ANCHO_VERTICAL = 720
LOTE_FOTOGRAMAS = 8               # fotogramas por tanda al pasarlos por CLIP (3 recortes cada uno)
TEXTO_CADA_N = 2                  # el detector de texto mira un fotograma de cada N (los demas heredan de sus vecinos)

# --------------------------------------------------------------------------------------------------------------------------------------------
# VOCABULARIO: lo que se le pregunta a CLIP de cada tramo. (etiqueta en espanol, frases en ingles que la describen). Sirve para `etiquetas` y `que_se_ve`.
CONCEPTOS: list[tuple[str, list[str]]] = [
    # personas
    ("mujeres", ["a group of women", "women gathered in a room"]), ("una mujer", ["a photo of a woman"]), ("hombres", ["a group of men", "a man"]),
    ("adultas mayores", ["elderly women", "a grandmother"]), ("jóvenes", ["young people", "teenagers"]), ("pareja", ["a couple together"]),
    ("familia", ["a family together at home"]), ("niños", ["children", "kids playing"]), ("bebé", ["a baby"]),
    ("grupo grande", ["a large crowd of people", "many people in a hall"]), ("grupo en círculo", ["people standing in a circle", "women standing in a circle"]),
    ("fila de personas", ["people standing in a line", "people in a row"]), ("persona hablando a cámara", ["a person talking to the camera"]),
    ("primer plano de rostro", ["a close-up of a face", "a portrait of a face"]), ("sonrisa", ["a smiling woman", "a smile"]),
    ("risa", ["people laughing out loud", "women laughing together"]), ("aplausos", ["people clapping their hands", "applause"]),
    ("abrazo", ["people hugging each other"]), ("baile", ["people dancing"]), ("oración", ["a woman praying with eyes closed", "people praying with raised hands"]),
    ("emoción", ["a woman crying with emotion"]), ("aburrimiento", ["a bored woman", "a sad woman looking down"]), ("conversación", ["people talking to each other"]),
    ("enseñanza", ["a teacher presenting to a group", "a speaker in front of an audience"]), ("predicador", ["a preacher speaking with a microphone"]),
    ("juego en grupo", ["a group playing a party game", "women playing a game together"]),
    # manos y proceso
    ("manos", ["close-up of hands", "hands in the foreground"]), ("manos trabajando", ["hands working on a craft"]),
    ("manos con papel", ["hands holding a piece of paper", "hands writing on paper"]), ("recortando", ["cutting paper with scissors"]), ("pegando", ["gluing or taping something"]),
    ("tejiendo", ["knitting or crocheting with yarn"]), ("bordando", ["embroidering fabric with a needle"]), ("costura", ["sewing with a sewing machine"]),
    ("pintando", ["painting on a canvas"]), ("cocinando", ["cooking in a kitchen"]), ("horneando", ["baking a cake in an oven"]), ("amasando", ["kneading dough with hands"]),
    ("mezclando", ["mixing ingredients in a bowl"]), ("decorando", ["decorating a table or an object"]), ("envolviendo un regalo", ["wrapping a gift"]),
    ("contando dinero", ["counting banknotes of money"]), ("escribiendo en el celular", ["typing on a smartphone"]), ("usando computadora", ["using a laptop computer"]),
    ("leyendo", ["reading a book"]), ("ejercicio", ["doing exercise"]), ("masaje", ["a massage on the back"]), ("cinta kinesiológica", ["kinesiology tape on the skin"]),
    # objetos
    ("globos", ["colorful balloons"]), ("vasos", ["plastic cups stacked on a table"]), ("mesa con materiales", ["a table with craft materials"]), ("libro", ["an open book"]),
    ("ebook en pantalla", ["a digital book on a screen"]), ("celular", ["a smartphone in hand"]), ("pantalla de computadora", ["a computer screen"]),
    ("velas", ["candles burning"]), ("flores", ["flowers"]), ("comida", ["a plate of food"]), ("pastel", ["a cake"]), ("café", ["a cup of coffee"]),
    ("telas", ["fabric and textiles"]), ("hilos y lanas", ["balls of colored yarn"]), ("tijeras", ["a pair of scissors"]), ("papel de colores", ["colored paper"]),
    ("biblia", ["a bible"]), ("regalo", ["a gift box"]), ("dinero", ["money, banknotes and coins"]), ("joyas", ["jewelry"]), ("maquillaje", ["makeup"]),
    ("cabello", ["hair styling"]), ("uñas", ["manicured nails"]), ("mascota", ["a dog or a cat"]), ("plantas", ["potted plants"]),
    # lugares y ambiente
    ("iglesia", ["inside a church"]), ("salón de eventos", ["an event hall with chairs and tables"]), ("sala de casa", ["a living room at home"]), ("cocina", ["a home kitchen"]),
    ("jardín", ["a garden outdoors"]), ("calle", ["a city street"]), ("salón de clases", ["a classroom"]), ("oficina", ["an office desk"]), ("playa", ["a beach"]),
    ("gimnasio", ["a gym"]), ("clínica", ["a clinic or a hospital"]), ("de noche", ["a scene at night"]), ("exterior de día", ["outdoors in daylight"]),
    # camara
    ("plano general", ["a wide shot"]), ("plano medio", ["a medium shot of people"]), ("primer plano", ["a close-up shot"]),
    ("pantalla en negro", ["a black screen"]), ("texto en pantalla", ["text on the screen"]), ("logo", ["a logo"]),
]

# Preguntas de calidad para CLIP: (frases que SI describen el problema, frases que describen un cuadro normal). La probabilidad sale de comparar los dos grupos.
# Se probaron cuatro con los 12 videos de Dinamicas revisados a mano y SOLO dos sirven: el rostro en primer plano (26 de 681 fotogramas, todos rostros de verdad)
# y el texto (solo como reserva cuando no hay detector de texto: acierta ~75 % con ~25 % de falsos). NO sirven: «ninos» (confunde los globos y las fiestas con ninos:
# los fotogramas mas «ninos» eran manos con globos; el video con ninos de verdad sacaba lo mismo que los demas) y «logo» (0,64 de probabilidad media en cuadros
# limpios y 0,66 en los malos). Por eso los ninos NO se detectan solos: los juzga una persona con la evidencia.png (y lo marcado `mala` a mano nunca se usa).
RIESGOS: dict[str, tuple[list[str], list[str]]] = {
    "texto": (["a video frame with big subtitles text overlay", "captions written over the picture", "a screenshot with words on it"],
              ["a clean photo with no text at all", "a natural photo without captions", "a candid photo of people"]),
    "rostro": (["a close-up of a person's face", "a portrait of a face looking at the camera"],
               ["a wide shot of a group of people", "close-up of hands", "an object on a table", "a room"]),
}
TEMPERATURA = 100.0     # la escala de logits de CLIP

# Umbrales de calidad (se calibraron con los 12 videos de Dinamicas que Claude reviso a mano: ver la skill, referencia emparejar-tomas.md)
UMBRALES = {
    "texto_clip_p": 0.80,   # probabilidad (0 a 1) de texto segun CLIP para marcar el fotograma; SOLO si no hay detector de texto
    "rostro_p": 0.70,       # rostro en primer plano: se prefiere evitarlo (pesa menos), no se descarta
    "oscuro_lum": 28.0,     # brillo medio (0-255) por debajo del cual el cuadro es casi negro
    "claro_lum": 235.0,
    "plano_std": 9.0,       # desviacion del brillo por debajo de la cual el cuadro es liso (pantalla en blanco o en negro)
    "corte_fuerte": 34.0,   # salto entre cuadros chicos (0-255) que siempre es un corte
    "corte_debil": 13.0,    # salto que es un corte solo si el contenido de los fotogramas vecinos tambien cambio
    "corte_sim": 0.88,      # parecido entre fotogramas vecinos por debajo del cual hubo cambio de escena
    "tramo_min": 1.0,       # un tramo mas corto que esto no sirve de toma
    "mala_texto_pct": 50,   # % de fotogramas con texto desde el cual el tramo es malo
    "media_texto_pct": 10,
    "mala_oscuro_pct": 50,  # % de fotogramas negros, blancos o lisos desde el cual el tramo es malo
    "media_oscuro_pct": 10,
    "video_limpio_frac": 0.50,   # fraccion de fotogramas limpios para que el VIDEO entero sea `limpio`
    "video_medio_frac": 0.10,    # ... y para que sea `medio` (por debajo, `malo`). Elegidos con los 12 videos de Dinamicas.
}


# --------------------------------------------------------------------------------------------------------------------------------------------
# utilidades
def memoria_pico_mb() -> float:
    """Pico de RAM de este programa (MB). Para anotar cuanto cuesta correr esto en cada equipo."""
    try:
        if os.name == "nt":
            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            p = PMC()
            p.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(p), p.cb)
            return p.PeakWorkingSetSize / 1048576
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:  # noqa: BLE001 - es solo un dato informativo
        return 0.0


def plegar(texto: str) -> str:
    """minusculas sin acentos (para comparar palabras en espanol)."""
    s = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def region_916(w: int, h: int) -> tuple[int, int, int, int]:
    """La franja de 9:16 centrada que se ve en el anuncio (ajuste `cover`): (x0, y0, ancho, alto)."""
    if w * 16 >= h * 9:
        rw, rh = max(1, round(h * 9 / 16)), h
    else:
        rw, rh = w, max(1, round(w * 16 / 9))
    return (w - rw) // 2, (h - rh) // 2, rw, rh


def recortes(im):
    """De un fotograma (PIL): (franja 9:16, [cuadrado de arriba, del centro, de abajo]). Los 3 cuadrados cubren toda la franja, que es lo que ve la gente."""
    w, h = im.size
    x0, y0, rw, rh = region_916(w, h)
    franja = im.crop((x0, y0, x0 + rw, y0 + rh))
    lado = min(rw, rh)
    ys = sorted({0, (rh - lado) // 2, rh - lado})
    cuadros = [franja.crop((0, y, lado, y + lado)) for y in ys]
    while len(cuadros) < 3:      # una franja casi cuadrada: los tres recortes son el mismo
        cuadros.append(cuadros[-1])
    return franja, cuadros[:3]


def tramos_a_segundos(desde: float, hasta: float) -> str:
    return f"{desde:.1f}-{hasta:.1f}"


# --------------------------------------------------------------------------------------------------------------------------------------------
# detector de texto (RapidOCR, el mismo de texto_quemado.py)
class DetectorTexto:
    """Cajas de texto de una imagen. Usa rapidocr-onnxruntime si esta; `disponible()` dice si hay."""

    def __init__(self):
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR

        self.np = np
        self._motor = RapidOCR()

    @staticmethod
    def disponible() -> bool:
        import importlib.util

        return bool(importlib.util.find_spec("rapidocr_onnxruntime"))

    def cajas(self, im) -> list:
        """Lista de cajas [(x, y) x4] en pixeles de `im` (PIL)."""
        arr = self.np.asarray(im.convert("RGB"))[:, :, ::-1].copy()      # RapidOCR espera BGR como OpenCV
        res, _ = self._motor(arr, use_det=True, use_cls=False, use_rec=False)
        return [[(float(x), float(y)) for x, y in caja] for caja in (res or [])]


def texto_en_cuadro(cajas: list, ancho: float, alto: float) -> tuple[bool, float]:
    """(hay texto, altura media 0-1 de las lineas de texto). Las cajas valen lo mismo que en texto_quemado.py: anchas, grandes y con forma de renglon."""
    import texto_quemado as tq

    validas = [c for c in cajas if tq.caja_valida(c, ancho, alto, tq.PARAMETROS)]
    if len(validas) < tq.PARAMETROS["cajas_min"]:
        return False, -1.0
    ys = [sum(p[1] for p in c) / 4 / alto for c in validas]
    return True, round(sum(ys) / len(ys), 3)


# --------------------------------------------------------------------------------------------------------------------------------------------
# CLIP: vocabulario y riesgos (se calculan una vez por corrida con el motor)
class Preguntas:
    """Los vectores del vocabulario y de las preguntas de calidad. `riesgos(emb)` y `etiquetas(emb)` salen de los vectores de los fotogramas, sin volver a ver video."""

    def __init__(self, motor):
        import numpy as np

        self.np = np
        etiquetas, frases, dueno = [], [], []
        for i, (et, prompts) in enumerate(CONCEPTOS):
            etiquetas.append(et)
            for p in prompts:
                frases.append(p)
                dueno.append(i)
        v = motor.textos(frases)
        self.etiquetas = etiquetas
        self.vocab = np.zeros((len(etiquetas), v.shape[1]), dtype=np.float32)
        for i in range(len(etiquetas)):
            m = v[[j for j, d in enumerate(dueno) if d == i]].mean(axis=0)
            self.vocab[i] = m / max(float(np.linalg.norm(m)), 1e-8)
        self.riesgo = {}
        for nombre, (si, no) in RIESGOS.items():
            a = motor.textos(si).mean(axis=0)
            b = motor.textos(no).mean(axis=0)
            self.riesgo[nombre] = (a / max(float(np.linalg.norm(a)), 1e-8), b / max(float(np.linalg.norm(b)), 1e-8))

    def riesgos(self, emb_recortes) -> dict:
        """emb_recortes (N, 3, 512) -> probabilidades 0-1 por fotograma (maximo entre los 3 recortes)."""
        np = self.np
        out = {}
        for nombre, (a, b) in self.riesgo.items():
            sa, sb = emb_recortes @ a, emb_recortes @ b                       # (N, 3)
            p = 1.0 / (1.0 + np.exp(-TEMPERATURA * (sa - sb)))
            out[nombre] = p.max(axis=1).astype(np.float32)
        return out

    def etiquetas_de(self, emb, k: int = 3) -> list[str]:
        """Las k etiquetas del vocabulario que mas se parecen al vector `emb` (512,)."""
        np = self.np
        s = self.vocab @ emb
        return [self.etiquetas[i] for i in np.argsort(-s)[:k]]


# --------------------------------------------------------------------------------------------------------------------------------------------
# un video -> sus datos
def sacar_cuadros(video: Path, cada: float, dur: float, carpeta: Path):
    """UNA pasada de ffmpeg: deja fotogramas f_0001.jpg... (uno cada `cada` s; el 0001 es el segundo 0) y devuelve los cuadros chicos a 6 por segundo (N, 160, 96)."""
    import numpy as np

    escala = "scale=w='if(gt(iw,ih),-2,%d)':h='if(gt(iw,ih),%d,-2)'" % (ANCHO_VERTICAL, ALTO_HORIZONTAL)
    filtro = f"[0:v:0]split=2[a][b];[a]fps=1/{cada:g},{escala}[f];[b]fps={FPS_CHICOS},scale={CHICO[0]}:{CHICO[1]},format=gray[g]"
    cmd = [ejecutable("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", str(video), "-filter_complex", filtro,
           "-map", "[f]", "-q:v", "3", str(carpeta / "f_%04d.jpg"), "-map", "[g]", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=max(900, int(dur * 8)))
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg no pudo leer {video.name}: {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    tam = CHICO[0] * CHICO[1]
    g = np.frombuffer(r.stdout, np.uint8)
    n = len(g) // tam
    return g[: n * tam].reshape(n, CHICO[1], CHICO[0])


def movimiento_y_cortes(chicos, p: dict) -> tuple["np.ndarray", "np.ndarray"]:
    """De los cuadros chicos: (diferencia media entre cuadros vecinos, tiempo de cada diferencia). Una diferencia j va del cuadro j al j+1: ocurre en (j+1)/6 s."""
    import numpy as np

    if len(chicos) < 2:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    d = np.abs(np.diff(chicos.astype(np.int16), axis=0)).mean(axis=(1, 2)).astype(np.float32)
    t = (np.arange(len(d)) + 1) / FPS_CHICOS
    return d, t.astype(np.float32)


def detectar_cortes(d, t, tiempos, emb, p: dict) -> list[float]:
    """Cortes de escena. Un corte es un salto de un solo cuadro: fuerte, o debil pero con cambio de contenido en los fotogramas vecinos (el movimiento de camara no cuenta)."""
    import numpy as np

    if len(d) == 0:
        return []
    cortes: list[float] = []
    for j in range(len(d)):
        vecinos = np.concatenate([d[max(0, j - 2):j], d[j + 1:j + 3]])
        pico = vecinos.size == 0 or d[j] >= 1.8 * float(vecinos.max())
        if not pico:
            continue
        if d[j] >= p["corte_fuerte"]:
            cortes.append(float(t[j]))
        elif d[j] >= p["corte_debil"] and len(tiempos) >= 2:
            k = int(np.searchsorted(tiempos, t[j]))           # fotograma justo despues del salto
            a, b = max(0, k - 1), min(len(tiempos) - 1, k)
            if a != b and float(emb[a] @ emb[b]) < p["corte_sim"]:
                cortes.append(float(t[j]))
    # dos cortes casi pegados son uno solo
    unicos: list[float] = []
    for c in sorted(cortes):
        if not unicos or c - unicos[-1] > 0.4:
            unicos.append(round(c, 2))
    return unicos


def analizar_video(video: Path, motor, preguntas: Preguntas, detector, cada: float, tecnicos: dict, p: dict = UMBRALES, mostrar=print) -> dict:
    """Analiza un video y devuelve sus datos por fotograma (arreglos) y sus tramos. No escribe nada.
    El detector de texto (lo mas lento: ~1 s por fotograma en un PC como el del dueno) mira un fotograma de cada dos: los de en medio se marcan con texto si
    cualquiera de sus vecinos lo tiene (es lo prudente: un subtitulo dura segundos)."""
    import numpy as np
    from PIL import Image

    dur = float(tecnicos["duracion"])
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="empar_") as tmp:
        carpeta = Path(tmp)
        chicos = sacar_cuadros(video, cada, dur, carpeta)
        t_ffmpeg = time.time() - t0
        fotos = sorted(carpeta.glob("f_*.jpg"))
        if not fotos:
            raise RuntimeError(f"{video.name}: ffmpeg no dejo ningun fotograma")
        n = len(fotos)
        tiempos = (np.arange(n) * cada).astype(np.float32)
        emb = np.zeros((n, 3, 512), dtype=np.float32)
        texto = np.full(n, 255, dtype=np.uint8)           # 255 = no se pudo medir
        texto_y = np.full(n, -1.0, dtype=np.float32)
        lum = np.zeros(n, dtype=np.float32)
        lum_std = np.zeros(n, dtype=np.float32)
        t_clip = t_ocr = 0.0
        for i0 in range(0, n, LOTE_FOTOGRAMAS):
            cuadros = []
            for i in range(i0, min(n, i0 + LOTE_FOTOGRAMAS)):
                im = Image.open(fotos[i]).convert("RGB")
                franja, cs = recortes(im)
                g = np.asarray(franja.convert("L").resize((48, 80)), dtype=np.float32)
                lum[i], lum_std[i] = float(g.mean()), float(g.std())
                if detector is not None and i % TEXTO_CADA_N == 0:
                    ta = time.time()
                    texto[i], texto_y[i] = (lambda r: (1 if r[0] else 0, r[1]))(texto_en_cuadro(detector.cajas(franja), franja.width, franja.height))
                    t_ocr += time.time() - ta
                cuadros.extend(cs)
            tb = time.time()
            e = motor.imagenes(cuadros)
            t_clip += time.time() - tb
            emb[i0:i0 + len(cuadros) // 3] = e.reshape(-1, 3, e.shape[-1])
    if detector is not None:
        for i in range(n):
            if texto[i] == 255:                           # un fotograma sin mirar: con texto si uno de sus vecinos mirados lo tiene
                vecinos = [texto[j] for j in (i - 1, i + 1) if 0 <= j < n and texto[j] != 255]
                texto[i] = 1 if any(v == 1 for v in vecinos) else 0
    medio = emb.mean(axis=1)
    medio /= np.maximum(np.linalg.norm(medio, axis=1, keepdims=True), 1e-8)
    riesgos = preguntas.riesgos(emb)
    d, td = movimiento_y_cortes(chicos, p)
    cortes = detectar_cortes(d, td, tiempos, medio, p)
    # movimiento por fotograma: media de las diferencias que caen en su intervalo (0-1, 25 de diferencia media = mucho movimiento)
    mov = np.zeros(n, dtype=np.float32)
    for i in range(n):
        m = (td >= tiempos[i] - cada / 2) & (td < tiempos[i] + cada / 2) if len(td) else np.zeros(0, bool)
        if len(td) and m.any():
            sub = d[m]
            mov[i] = float(np.clip(np.median(sub) / 25.0, 0, 1))      # la mediana ignora el pico de un corte
    return {
        "tiempos": tiempos, "emb": emb.astype(np.float16), "texto": texto, "texto_y": texto_y, "lum": lum, "lum_std": lum_std, "mov": mov,
        "cortes": np.array(cortes, dtype=np.float32), "dif": d.astype(np.float16), "dif_t": td, "texto_clip": riesgos["texto"], "rostro": riesgos["rostro"],
        "meta": {"version": VERSION_ANALISIS, "motor": motor.version, "cada": cada, "duracion": dur, "ocr": detector is not None},
        "tiempos_seg": {"ffmpeg": round(t_ffmpeg, 1), "clip": round(t_clip, 1), "texto": round(t_ocr, 1), "total": round(time.time() - t0, 1)},
    }


def guardar_datos(ruta: Path, datos: dict) -> None:
    import numpy as np
    import json

    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.stem + ".parte.npz")
    np.savez_compressed(tmp, tiempos=datos["tiempos"], emb=datos["emb"], texto=datos["texto"], texto_y=datos["texto_y"], lum=datos["lum"], lum_std=datos["lum_std"],
                        mov=datos["mov"], cortes=datos["cortes"], dif=datos["dif"], dif_t=datos["dif_t"],
                        texto_clip=datos["texto_clip"], rostro=datos["rostro"], meta=np.frombuffer(json.dumps(datos["meta"]).encode("utf-8"), dtype=np.uint8))
    os.replace(tmp, ruta)


def cargar_datos(ruta: Path) -> dict | None:
    import json
    import numpy as np

    if not ruta.is_file():
        return None
    try:
        with np.load(ruta) as z:
            datos = {k: z[k] for k in z.files if k != "meta"}
            datos["meta"] = json.loads(bytes(z["meta"].tolist()).decode("utf-8"))
        return datos
    except Exception:  # noqa: BLE001 - un archivo roto se vuelve a calcular
        return None


def vectores_medios(datos: dict):
    """El vector de cada fotograma: el promedio de sus 3 recortes (normalizado). (N, 512) float32."""
    import numpy as np

    e = datos["emb"].astype(np.float32).mean(axis=1)
    return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-8)


def cortes_de(datos: dict, p: dict = UMBRALES) -> list[float]:
    """Los cortes de escena de un video, calculados de nuevo con los umbrales `p` a partir de lo guardado (no hace falta volver a decodificar el video)."""
    import numpy as np

    if "dif" in datos and len(datos["dif"]):
        return detectar_cortes(datos["dif"].astype(np.float32), datos["dif_t"], datos["tiempos"], vectores_medios(datos), p)
    return [float(c) for c in datos["cortes"]]


# --------------------------------------------------------------------------------------------------------------------------------------------
# calidad y tramos
def fotogramas_buenos(datos: dict, p: dict = UMBRALES, usar_clip_texto: bool | None = None) -> "np.ndarray":
    """Para cada fotograma: 'limpio' (sin texto quemado ni cuadro negro, blanco o liso), True/False. Con los motivos en `motivos_por_fotograma`."""
    return motivos_por_fotograma(datos, p, usar_clip_texto)["limpio"]


def motivos_por_fotograma(datos: dict, p: dict = UMBRALES, usar_clip_texto: bool | None = None) -> dict:
    import numpy as np

    n = len(datos["tiempos"])
    sin_detector = bool(np.all(datos["texto"] == 255)) if n else True
    if usar_clip_texto is None:
        usar_clip_texto = sin_detector
    texto = (datos["texto"] == 1) if not usar_clip_texto else (datos["texto_clip"] >= p["texto_clip_p"])
    oscuro = (datos["lum"] < p["oscuro_lum"]) | (datos["lum"] > p["claro_lum"]) | (datos["lum_std"] < p["plano_std"])
    limpio = ~(texto | oscuro)
    return {"texto": texto, "oscuro": oscuro, "limpio": limpio, "rostro": datos["rostro"] >= p["rostro_p"]}


def partir_en_tramos(datos: dict, p: dict = UMBRALES) -> list[tuple[float, float]]:
    """Tramos [desde, hasta): entre cortes de escena y, ademas, donde un fotograma cambia de limpio a no limpio (para que cada tramo tenga una sola calidad)."""
    import numpy as np

    dur = float(datos["meta"]["duracion"])
    cada = float(datos["meta"]["cada"])
    t = datos["tiempos"]
    limpio = fotogramas_buenos(datos, p)
    limites = [0.0] + [float(c) for c in cortes_de(datos, p) if 0.2 < c < dur - 0.2]
    for i in range(1, len(t)):
        if bool(limpio[i]) != bool(limpio[i - 1]):
            limites.append(float(t[i] - cada / 2))          # el cambio cae entre los dos fotogramas
    limites = sorted(set(round(x, 2) for x in limites)) + [round(dur, 2)]
    tramos = [(a, b) for a, b in zip(limites, limites[1:]) if b - a > 0.05]
    # los tramos de menos de `tramo_min` se pegan al vecino (si no, quedan trocitos inutiles)
    fusion: list[list[float]] = []
    for a, b in tramos:
        if fusion and (b - a) < p["tramo_min"] and (fusion[-1][1] - fusion[-1][0]) >= p["tramo_min"]:
            fusion[-1][1] = b
        elif fusion and (fusion[-1][1] - fusion[-1][0]) < p["tramo_min"]:
            fusion[-1][1] = b
        else:
            fusion.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in fusion]


def calificar_tramo(datos: dict, desde: float, hasta: float, preguntas: Preguntas, p: dict = UMBRALES) -> dict:
    """Etiquetas, calidad (buena / media / mala) y motivos de un tramo, a partir de los fotogramas que caen en el."""
    import numpy as np

    t = datos["tiempos"]
    cada = float(datos["meta"]["cada"])
    idx = [i for i in range(len(t)) if desde - cada / 2 < t[i] < hasta + cada / 2 and t[i] >= desde - 1e-6 and t[i] < hasta + 1e-6] or \
          [int(np.argmin(np.abs(t - (desde + hasta) / 2)))]
    m = motivos_por_fotograma(datos, p)
    pct = lambda k: round(100 * float(np.mean([bool(m[k][i]) for i in idx])))  # noqa: E731
    texto_pct, oscuro_pct, rostro_pct = pct("texto"), pct("oscuro"), pct("rostro")
    motivos = []
    if texto_pct >= p["media_texto_pct"]:
        motivos.append("texto")
    if oscuro_pct >= p["media_oscuro_pct"]:
        motivos.append("cuadro oscuro o liso")
    if (hasta - desde) < p["tramo_min"]:
        calidad = "mala"
        motivos.append("muy corto")
    elif texto_pct >= p["mala_texto_pct"] or oscuro_pct >= p["mala_oscuro_pct"]:
        calidad = "mala"
    elif motivos:
        calidad = "media"
    else:
        calidad = "buena"
    e = datos["emb"][idx].astype(np.float32).mean(axis=1).mean(axis=0)
    etiquetas = preguntas.etiquetas_de(e / max(float(np.linalg.norm(e)), 1e-8), 4)
    if rostro_pct >= 50 and "primer plano de rostro" not in etiquetas:
        etiquetas.append("primer plano de rostro")
    if "texto" in motivos and "texto en pantalla" not in etiquetas:
        etiquetas.append("texto en pantalla")
    mov = round(float(np.mean(datos["mov"][idx])), 2)
    return {
        "desde": round(float(desde), 2), "hasta": round(float(hasta), 2), "que_se_ve": " · ".join(etiquetas[:3]), "etiquetas": etiquetas, "calidad": calidad,
        "motivos": motivos, "texto_pct": texto_pct, "rostro_pct": rostro_pct, "movimiento": mov,
    }


def tramos_de_video(datos: dict, preguntas: Preguntas, p: dict = UMBRALES) -> list[dict]:
    return [calificar_tramo(datos, a, b, preguntas, p) for a, b in partir_en_tramos(datos, p)]


def resumen_video(datos: dict, p: dict = UMBRALES) -> dict:
    """La calidad del video entero, para verla de un vistazo en indice.json: `limpio` (la mitad o mas son fotogramas limpios), `medio` (hay material limpio, pero poco),
    `malo` (casi nada). Es la misma escala con la que Claude clasificaba a mano. Los umbrales (50 % y 10 %) salen de los 12 videos de Dinamicas."""
    import numpy as np

    limpio = fotogramas_buenos(datos, p)
    cada = float(datos["meta"]["cada"])
    frac = float(np.mean(limpio)) if len(limpio) else 0.0
    calidad = "limpio" if frac >= p["video_limpio_frac"] else ("medio" if frac >= p["video_medio_frac"] else "malo")
    return {"calidad_video": calidad, "limpio_seg": round(float(limpio.sum()) * cada, 1), "limpio_pct": round(100 * frac)}


# --------------------------------------------------------------------------------------------------------------------------------------------
# indice.json
def datos_tecnicos(ruta: Path) -> dict:
    """Los mismos datos tecnicos que anota indexar_broll.py (duracion, tamano, vertical u horizontal, audio)."""
    import indexar_broll

    return indexar_broll.datos_tecnicos(ruta)


def es_manual(entrada: dict) -> bool:
    """Una entrada con tramos que alguien reviso a mano (Claude o una persona): revisado y sin la marca de automatico."""
    return bool(entrada.get("revisado")) and bool(entrada.get("tramos")) and entrada.get("revisado_por") != "auto-clip"


def indexar(producto: str, cada: float = CADA_DEFECTO, rehacer: bool = False, solo: list[str] | None = None, sin_texto: bool = False,
            pisar_manual: bool = False, motor=None, detector=None, mostrar=print) -> dict:
    """Analiza los videos nuevos o cambiados de `producto` y los deja en broll-tiktok/indice.json y broll-tiktok/_clip/. Devuelve un resumen."""
    carpeta = producto_dir(producto) / "broll-tiktok"
    if not carpeta.is_dir():
        salir(f"No existe {carpeta}")
    videos = sorted(v for v in carpeta.iterdir() if v.is_file() and v.suffix.lower() in EXT_VIDEO)
    if solo:
        videos = [v for v in videos if any(s.lower() in v.name.lower() for s in solo)]
    if not videos:
        salir(f"No hay videos en {carpeta}")
    ruta_indice = carpeta / "indice.json"
    indice = leer_json(ruta_indice) if ruta_indice.is_file() else {}
    indice.setdefault("producto", producto)
    entradas = indice.setdefault("videos", {})

    pendientes = []
    for v in videos:
        st = v.stat()
        e = entradas.get(v.name) or {}
        clip = e.get("clip") or {}
        al_dia = (e.get("tam") == st.st_size and e.get("mtime") == int(st.st_mtime) and clip.get("version") == VERSION_ANALISIS and clip.get("cada") == cada
                  and (carpeta / "_clip" / f"{v.stem}.npz").is_file())
        if rehacer or not al_dia:
            pendientes.append(v)
    resumen = {"videos": len(videos), "analizados": 0, "al_dia": len(videos) - len(pendientes), "segundos": 0.0, "fotogramas": 0, "tramos": 0}
    if not pendientes:
        mostrar(f"{len(videos)} videos y todos ya estan analizados (usa --rehacer para repetir).")
        return resumen

    import clip_local

    if motor is None:
        try:
            motor = clip_local.MotorClip()
        except clip_local.FaltanModelos as e:
            salir(str(e))
    if detector is None and not sin_texto:
        if DetectorTexto.disponible():
            detector = DetectorTexto()
        else:
            mostrar("AVISO: no esta rapidocr-onnxruntime: el texto quemado se estima solo con CLIP (menos fiable). Instalalo con `pip install -r requirements.txt`.")
    preguntas = Preguntas(motor)
    t_ini = time.time()
    for k, v in enumerate(pendientes, 1):
        st = v.stat()
        previo = entradas.get(v.name) or {}
        mostrar(f"[{k}/{len(pendientes)}] {v.name} ...")
        try:
            tec = datos_tecnicos(v)
            datos = analizar_video(v, motor, preguntas, detector, cada, tec, mostrar=mostrar)
        except Exception as e:  # noqa: BLE001 - un video roto no tumba a los demas
            mostrar(f"    SALTADO: {e}")
            continue
        guardar_datos(carpeta / "_clip" / f"{v.stem}.npz", datos)
        tramos = tramos_de_video(datos, preguntas)
        entrada = {**previo, "tam": st.st_size, "mtime": int(st.st_mtime), **tec}
        entrada.update(resumen_video(datos))
        entrada["clip"] = {"version": VERSION_ANALISIS, "cada": cada, "motor": motor.version, "muestras": int(len(datos["tiempos"])), "cortes": [round(float(c), 2) for c in datos["cortes"]],
                           "texto": "detector" if datos["meta"]["ocr"] else "clip", "archivo": f"_clip/{v.stem}.npz", "analizado": datetime.now().isoformat(timespec="seconds"),
                           "segundos": datos["tiempos_seg"]}
        if es_manual(previo) and not pisar_manual:
            entrada["tramos_auto"] = tramos                      # lo manual manda; lo automatico queda al lado para comparar
        else:
            if es_manual(previo) and pisar_manual:
                entrada["tramos_manual"] = previo["tramos"]
            entrada["tramos"] = tramos
            entrada["revisado"] = True
            entrada["revisado_por"] = "auto-clip"
            entrada.pop("tramos_auto", None)
        entradas[v.name] = entrada
        indice["actualizado"] = datetime.now().isoformat(timespec="seconds")
        escribir_json(ruta_indice, indice)                       # se guarda video por video: si se corta, lo hecho queda
        buenos = sum(1 for t in tramos if t["calidad"] == "buena")
        resumen["analizados"] += 1
        resumen["fotogramas"] += int(len(datos["tiempos"]))
        resumen["tramos"] += len(tramos)
        ts = datos["tiempos_seg"]
        mostrar(f"    {tec['duracion']:.0f} s, {len(datos['tiempos'])} fotogramas, {len(tramos)} tramos ({buenos} buenos), {len(datos['cortes'])} cortes · "
                f"ffmpeg {ts['ffmpeg']} s, CLIP {ts['clip']} s, texto {ts['texto']} s")
    # lo que ya no esta en la carpeta sale del indice y de _clip
    existentes = {v.name for v in carpeta.iterdir() if v.is_file() and v.suffix.lower() in EXT_VIDEO}
    for nombre in [n for n in entradas if n not in existentes]:
        del entradas[nombre]
        (carpeta / "_clip" / f"{Path(nombre).stem}.npz").unlink(missing_ok=True)
        (carpeta / "_clip" / f"{Path(nombre).stem}.bandas.json").unlink(missing_ok=True)
    escribir_json(ruta_indice, indice)
    resumen["segundos"] = round(time.time() - t_ini, 1)
    resumen["ram_pico_mb"] = round(memoria_pico_mb())
    return resumen


def recalcular(producto: str, pisar_manual: bool = False, motor=None, mostrar=print) -> int:
    """Vuelve a calcular `tramos` (y su calidad) de los videos ya analizados con los umbrales actuales, SIN volver a decodificar ni a pasar por CLIP/OCR: lee los vectores y
    banderas guardados en _clip/. Sirve despues de ajustar un umbral. Respeta lo manual igual que indexar(). Devuelve cuantos videos recalculo."""
    import clip_local

    carpeta = producto_dir(producto) / "broll-tiktok"
    ruta_indice = carpeta / "indice.json"
    if not ruta_indice.is_file():
        salir(f"No hay {ruta_indice}: corre primero emparejar_tomas.py {producto}")
    indice = leer_json(ruta_indice)
    if motor is None:
        try:
            motor = clip_local.MotorClip()
        except clip_local.FaltanModelos as e:
            salir(str(e))
    preguntas = Preguntas(motor)
    n = 0
    for nombre, e in indice.get("videos", {}).items():
        datos = cargar_datos(carpeta / "_clip" / f"{Path(nombre).stem}.npz")
        if not datos:
            continue
        tramos = tramos_de_video(datos, preguntas)
        if es_manual(e) and not pisar_manual:
            e["tramos_auto"] = tramos
        else:
            if es_manual(e) and pisar_manual:
                e["tramos_manual"] = e["tramos"]
            e["tramos"] = tramos
            e["revisado"], e["revisado_por"] = True, "auto-clip"
            e.pop("tramos_auto", None)
        e.setdefault("clip", {})["cortes"] = [round(float(c), 2) for c in cortes_de(datos)]
        e.update(resumen_video(datos))
        n += 1
    indice["actualizado"] = datetime.now().isoformat(timespec="seconds")
    escribir_json(ruta_indice, indice)
    mostrar(f"Tramos recalculados en {n} video(s).")
    return n


# --------------------------------------------------------------------------------------------------------------------------------------------
# marcas de una persona: lo que el automatico no puede ver (un nino, un logo sin letras, una cara conocida, un lugar que no va con la oferta)
CALIDADES_MARCA = ("mala", "media")


def leer_marcas(entrada: dict, nombre: str = "") -> list[dict]:
    """Las marcas que una persona puso a un video (`marcas` en indice.json): [{desde, hasta, calidad: mala|media, motivo}]. `mala` = ese tramo nunca se usa (ni cuando falta
    material); `media` = se prefiere otra toma. Si una marca no se entiende se detiene todo: una marca de seguridad que se ignora en silencio dejaria pasar justo lo que la
    persona quiso evitar."""
    marcas = []
    for k, m in enumerate(entrada.get("marcas") or [], 1):
        try:
            desde, hasta, calidad = float(m["desde"]), float(m["hasta"]), m.get("calidad", "mala")
        except (KeyError, TypeError, ValueError, AttributeError):
            desde, hasta, calidad = 0.0, 0.0, None
        if not hasta > desde or calidad not in CALIDADES_MARCA:
            salir(f"La marca {k} de {nombre or 'un video'} en indice.json no se entiende: {m}. Cada marca lleva `desde` y `hasta` (segundos, hasta mayor que desde) "
                  "y `calidad` «mala» o «media».")
        marcas.append({**m, "desde": desde, "hasta": hasta, "calidad": calidad, "motivo": str(m.get("motivo") or "")})
    return marcas


def leer_tramo(texto) -> tuple[float, float] | None:
    """«10-14.5» (o «10 a 14,5», o la pareja ya hecha) -> (10.0, 14.5) segundos; «todo» -> None (el video entero)."""
    if isinstance(texto, (tuple, list)):
        return float(texto[0]), float(texto[1])
    t = str(texto).strip().lower()
    if t in ("todo", "todos", "entero", "video", "*"):
        return None
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(?:s|seg)?\s*(?:-|–|hasta|a)\s*(\d+(?:[.,]\d+)?)\s*(?:s|seg)?", t)
    if not m:
        salir(f"No entiendo el tramo «{texto}». Escribe los segundos asi: 12-15.5 (desde-hasta), o la palabra «todo» para el video entero.")
    desde, hasta = (float(x.replace(",", ".")) for x in m.groups())
    if hasta <= desde:
        salir(f"El tramo «{texto}» termina antes de empezar: escribe primero el segundo de inicio y despues el del final (12-15.5).")
    return desde, hasta


def _video_del_indice(indice: dict, texto: str) -> str:
    """El nombre completo de un video del indice a partir de un pedazo del nombre (basta el final, como sale en plan.md). Si no hay uno solo, se detiene."""
    nombres = list(indice.get("videos", {}))
    t = str(texto).strip().lower()
    for coincide in (lambda n: n.lower() == t or Path(n).stem.lower() == t, lambda n: t in n.lower()):
        hallados = [n for n in nombres if coincide(n)]
        if len(hallados) == 1:
            return hallados[0]
        if len(hallados) > 1:
            salir(f"«{texto}» coincide con {len(hallados)} videos ({', '.join(hallados[:4])}...): escribe mas del nombre.")
    salir(f"No hay ningun video que se llame «{texto}» en indice.json. Hay: {', '.join(nombres[:6])}{'...' if len(nombres) > 6 else ''}")
    return ""


def _indice_de(producto: str) -> tuple[Path, dict]:
    ruta = producto_dir(producto) / "broll-tiktok" / "indice.json"
    if not ruta.is_file():
        salir(f"No hay {ruta}: corre primero `python scripts/emparejar_tomas.py {producto}`.")
    return ruta, leer_json(ruta)


def usable_seg(producto: str, nombre: str, entrada: dict) -> float | None:
    """Segundos de ese video que el montaje puede usar sin apuros (None si todavia no se analizo)."""
    carpeta = producto_dir(producto) / "broll-tiktok"
    datos = cargar_datos(carpeta / "_clip" / f"{Path(nombre).stem}.npz")
    if not datos:
        return None
    v = Video(nombre, "", datos, entrada, bandas=bandas_negras_de(carpeta / nombre, carpeta / "_clip"))
    return round(float(v.estricto.sum()) * v.cada, 1)


def marcar(producto: str, video: str, tramo="todo", calidad: str = "mala", motivo: str = "", mostrar=print) -> dict:
    """Marca a mano un tramo (o el video entero) del b-roll. Queda en indice.json (`marcas`), vale para todos los anuncios de ese producto y sobrevive a repetir el analisis:
    `mala` = nunca se usa; `media` = se prefiere otra toma. Devuelve {video, desde, hasta, calidad, quedaba_seg, queda_seg}."""
    if calidad not in CALIDADES_MARCA:
        salir("La calidad de una marca es «mala» (nunca se usa) o «media» (se prefiere otra toma).")
    ruta_indice, indice = _indice_de(producto)
    nombre = _video_del_indice(indice, video)
    e = indice["videos"][nombre]
    dur = float(e.get("duracion") or 0)
    rango = leer_tramo(tramo)
    if rango is None:
        desde, hasta = 0.0, round(max(dur, 1.0) + 1.0, 2)
    else:
        desde, hasta = rango
        if not hasta > desde:
            salir(f"El tramo {desde:g}-{hasta:g} termina antes de empezar.")
        if dur and desde >= dur:
            salir(f"{nombre} dura {dur:.1f} s: el tramo {desde:g}-{hasta:g} empieza despues de que termina.")
    desde, hasta = round(desde, 2), round(hasta, 2)
    marcas = [m for m in leer_marcas(e, nombre) if (m["calidad"], round(m["desde"], 2), round(m["hasta"], 2)) != (calidad, desde, hasta)]
    antes = usable_seg(producto, nombre, e)
    marcas.append({"desde": desde, "hasta": hasta, "calidad": calidad, "motivo": str(motivo).strip(), "fecha": datetime.now().isoformat(timespec="seconds")})
    e["marcas"] = sorted(marcas, key=lambda m: (m["desde"], m["hasta"]))
    indice["actualizado"] = datetime.now().isoformat(timespec="seconds")
    escribir_json(ruta_indice, indice)
    despues = usable_seg(producto, nombre, e)
    donde = "el video entero" if rango is None else f"de {desde:g} a {hasta:g} s"
    efecto = (f"nunca se va a usar, en ningun anuncio de {producto}" if calidad == "mala"
              else f"se va a preferir otra toma en los anuncios de {producto} (si falta material, se usa)")
    mostrar(f"Marcado como `{calidad}`: {nombre}, {donde}" + (f" ({motivo.strip()})" if str(motivo).strip() else "") + f". Desde ahora {efecto}."
            + (f" De ese video quedan {despues:g} s utilizables (antes {antes:g} s)." if antes is not None and despues is not None else " (El video todavia no se analizo: se aplica cuando se analice.)"))
    return {"video": nombre, "desde": desde, "hasta": hasta, "calidad": calidad, "quedaba_seg": antes, "queda_seg": despues}


def marcar_toma(producto: str, anuncio: str, numeros: list[int], calidad: str = "mala", motivo: str = "", mostrar=print) -> list[dict]:
    """Marca la(s) toma(s) #N de un anuncio ya montado (el numero que sale en evidencia.png y en videoData.json) con el video y los segundos exactos que usaron."""
    ruta = ANUNCIOS / producto / "tiktok" / anuncio / "videoData.json"
    if not ruta.is_file():
        salir(f"No encuentro {ruta}: monta primero el anuncio con `python scripts/montaje_auto.py {producto} {anuncio}`.")
    tomas = leer_json(ruta).get("tomas") or []
    hechas = []
    for n in numeros:
        if not 1 <= n <= len(tomas):
            salir(f"El anuncio {anuncio} tiene {len(tomas)} tomas: la {n} no existe.")
        t = tomas[n - 1]
        src = str(t.get("src", "")).replace("\\", "/")
        if f"productos/{producto}/broll-tiktok/" not in src:
            salir(f"La toma {n} de {anuncio} no es del b-roll de {producto} (es {src or 'otra cosa'}): solo se marcan las tomas de broll-tiktok/.")
        desde = float(t["desde"])
        hechas.append(marcar(producto, Path(src).name, (desde, desde + float(t["duracion"])), calidad, motivo or f"toma {n} de {anuncio}", mostrar))
    return hechas


def quitar_marcas(producto: str, video: str, tramo=None, mostrar=print) -> int:
    """Borra las marcas de un video (todas, o las que se cruzan con `tramo`). Devuelve cuantas borro."""
    ruta_indice, indice = _indice_de(producto)
    nombre = _video_del_indice(indice, video)
    e = indice["videos"][nombre]
    marcas = leer_marcas(e, nombre)
    rango = leer_tramo(tramo) if tramo is not None else None
    quedan = [m for m in marcas if rango is not None and not (m["desde"] < rango[1] and rango[0] < m["hasta"])]
    borradas = len(marcas) - len(quedan)
    if quedan:
        e["marcas"] = quedan
    else:
        e.pop("marcas", None)
    if borradas:
        indice["actualizado"] = datetime.now().isoformat(timespec="seconds")
        escribir_json(ruta_indice, indice)
    mostrar(f"{borradas} marca(s) borrada(s) de {nombre}; le quedan {len(quedan)}.")
    return borradas


def listar_marcas(producto: str, mostrar=print) -> int:
    """Muestra todas las marcas puestas a mano en el b-roll de `producto`. Devuelve cuantas hay."""
    _, indice = _indice_de(producto)
    total = 0
    for nombre, e in indice.get("videos", {}).items():
        for m in leer_marcas(e, nombre):
            total += 1
            mostrar(f"  {nombre}  {m['desde']:g}-{m['hasta']:g} s  {m['calidad']}" + (f"  · {m['motivo']}" if m["motivo"] else "") + (f"  ({m['fecha'][:10]})" if m.get("fecha") else ""))
    mostrar(f"{total} marca(s) en el b-roll de {producto}." if total else f"Nadie marco nada a mano en el b-roll de {producto}.")
    return total


# --------------------------------------------------------------------------------------------------------------------------------------------
# comparar con lo manual
def comparar_con_manual(producto: str, p: dict = UMBRALES, mostrar=print) -> dict:
    """Por cada tramo que revisaron a mano, que dice lo automatico en sus fotogramas. Imprime la matriz y los desacuerdos."""
    import numpy as np

    carpeta = producto_dir(producto) / "broll-tiktok"
    indice = leer_json(carpeta / "indice.json")
    orden = ["buena", "media", "mala"]
    matriz = {a: {b: 0 for b in orden} for a in orden}           # filas: lo manual; columnas: lo automatico (en fotogramas)
    desacuerdos = []
    for nombre, e in indice["videos"].items():
        manual = e.get("tramos_manual") if e.get("tramos_manual") else (e.get("tramos") if es_manual(e) else None)
        auto_tramos = e.get("tramos_auto") if e.get("tramos_auto") else (e.get("tramos") if e.get("revisado_por") == "auto-clip" else None)
        datos = cargar_datos(carpeta / "_clip" / f"{Path(nombre).stem}.npz")
        if not manual or not datos:
            continue
        t = datos["tiempos"]
        m = motivos_por_fotograma(datos, p)
        for tm in manual:
            idx = [i for i in range(len(t)) if tm["desde"] <= t[i] < tm["hasta"]]
            if not idx:
                continue
            limpios = float(np.mean([bool(m["limpio"][i]) for i in idx]))
            auto = "buena" if limpios >= 0.9 else ("media" if limpios >= 0.4 else "mala")
            matriz[tm["calidad"]][auto] += len(idx)
            if tm["calidad"] != auto:
                desacuerdos.append((nombre, tm["desde"], tm["hasta"], tm["calidad"], auto, round(100 * limpios), tm.get("que_se_ve", "")[:70]))
    total = sum(sum(f.values()) for f in matriz.values())
    mostrar("\nLo manual (filas) contra lo automatico (columnas), en fotogramas:")
    mostrar(f"{'':10s}" + "".join(f"{c:>8s}" for c in orden))
    for a in orden:
        mostrar(f"{a:10s}" + "".join(f"{matriz[a][b]:8d}" for b in orden))
    igual = sum(matriz[a][a] for a in orden)
    mostrar(f"Coinciden {igual} de {total} fotogramas ({round(100 * igual / total) if total else 0} %).")
    # lo que importa para el montaje: ¿se cuela algo malo? ¿se pierde algo bueno?
    cuelan = matriz["mala"]["buena"] + matriz["mala"]["media"]
    perdidos = matriz["buena"]["mala"]
    mostrar(f"Lo manual `mala` que lo automatico dejaria pasar (buena o media): {cuelan} fotogramas; lo manual `buena` que lo automatico descarta: {perdidos} fotogramas.")
    for d in desacuerdos:
        mostrar(f"  {d[0][-14:]} {d[1]:5.1f}-{d[2]:5.1f}: manual {d[3]}, automatico {d[4]} ({d[5]} % de fotogramas limpios) · {d[6]}")
    # el video entero: la misma escala que usaba Claude (limpio / medio / malo)
    por_video = []
    for nombre, e in indice["videos"].items():
        manual = e.get("tramos_manual") if e.get("tramos_manual") else (e.get("tramos") if es_manual(e) else None)
        if not manual or not e.get("calidad_video"):
            continue
        dur = float(e.get("duracion") or 0) or max(t["hasta"] for t in manual)
        seg = {c: sum(t["hasta"] - t["desde"] for t in manual if t["calidad"] == c) for c in orden}
        clase = "limpio" if seg["buena"] / dur >= 0.5 else ("medio" if (seg["buena"] + seg["media"]) / dur >= 0.5 else "malo")
        por_video.append((nombre, clase, e["calidad_video"], e.get("limpio_seg", 0), dur))
    if por_video:
        mostrar("\nPor video entero (limpio / medio / malo): lo manual contra lo automatico")
        for nombre, m, a, seg, dur in por_video:
            mostrar(f"  {nombre[-14:]:14s} manual {m:6s} automatico {a:6s} ({seg:.0f} s limpios de {dur:.0f})" + ("" if m == a else "   <- DESACUERDO"))
        mostrar(f"Coinciden {sum(1 for _, m, a, _, _ in por_video if m == a)} de {len(por_video)} videos.")
    return {"matriz": matriz, "coinciden_pct": round(100 * igual / total) if total else 0, "desacuerdos": desacuerdos, "total": total, "por_video": por_video}


# --------------------------------------------------------------------------------------------------------------------------------------------
# catalogo: todos los videos de un producto listos para emparejar
PREFIJOS_AJENOS = ("tt_",)      # con que nombre guarda broll_tiktok.py los videos que baja de TikTok: son de otras personas (un clip bajado a mano cuenta si se le pone tt_ al principio)


def es_ajeno(nombre: str) -> bool:
    """¿Es un video de otra persona (de TikTok)? Se sabe por el nombre con que lo guarda el programa que lo baja: tt_<id>.mp4."""
    return str(nombre).lower().startswith(PREFIJOS_AJENOS)


REGLAS = {
    "paso": 0.25,              # cada cuantos segundos se prueba una nueva toma dentro de un video
    "peso_gancho": 1.25,       # la primera toma pesa mas el parecido (es la que decide si la gente se queda)
    "peso_movimiento": 0.30,   # una toma con algo de movimiento engancha mas que una fija
    "peso_buena": 0.25,        # bono por venir de un tramo `buena` (si hay revision manual)
    "pen_blando": 0.40,        # rostro en primer plano o tramo `media` de la revision manual
    "pen_uso": 0.12,           # por cada toma ya sacada del mismo video (para repartir el anuncio entre varios videos)
    "pen_vecina": 0.35,        # la toma de al lado sale del mismo video
    "pen_cerca": 0.35,         # ya hay otra toma de este video a menos de `cerca` segundos
    "cerca": 1.5,
    "separacion": 0.30,        # entre dos tomas del mismo video tiene que haber al menos esto (nunca se solapan)
    "margen_corte": 0.15,      # una toma no puede tener un corte de escena a menos de esto de sus bordes (se veria un cuadro de la escena de al lado)
    "tope_seg_video": 0,       # 0 = sin tope; si no, los segundos que se usan EN TOTAL, en un anuncio, de un mismo video de otra persona (tt_): la regla del dueño es de 3 a 5 s
}


# --------------------------------------------------------------------------------------------------------------------------------------------
# franjas negras arriba y abajo: muchos videos de TikTok las traen (el creador los exporto asi, o solo su intro y su cierre) y en el anuncio se ven como un marco negro. Ni el detector de texto ni
# el de cuadros negros las ven (el cuadro NO es negro), asi que se miden aparte con una pasada barata de ffmpeg (cuadros de 108x192, 4 por segundo) y esos tramos no se usan nunca. En FIELTRO el
# AD3 abrio con un clip con 12 % de negro arriba y abajo y el control automatico lo dio por bueno.
BARRAS = {"fps": 4, "ancho": 108, "alto": 192, "fila_max": 24, "fila_media": 14, "min_pct": 2.0, "centro_min": 28.0, "version": 1}


def _filas_negras(cuadro: bytes, ancho: int, alto: int, desde_abajo: bool) -> int:
    """Cuantas filas seguidas, contando desde un borde, son negras de verdad: ningun punto brillante y casi todo oscuro."""
    n = 0
    for y in (range(alto - 1, -1, -1) if desde_abajo else range(alto)):
        fila = cuadro[y * ancho:(y + 1) * ancho]
        if max(fila) > BARRAS["fila_max"] or sum(fila) / ancho > BARRAS["fila_media"]:
            break
        n += 1
    return n


def cuadro_con_franjas(cuadro: bytes, ancho: int, alto: int) -> bool:
    """True si el cuadro (grises, fila tras fila) trae franjas negras arriba Y abajo con imagen en medio. Un cuadro entero negro NO cuenta: ese lo ve blackdetect."""
    minimo = max(2, round(alto * BARRAS["min_pct"] / 100))
    if _filas_negras(cuadro, ancho, alto, False) < minimo or _filas_negras(cuadro, ancho, alto, True) < minimo:
        return False
    centro = cuadro[int(alto * 0.45) * ancho:int(alto * 0.55) * ancho]
    return bool(centro) and sum(centro) / len(centro) >= BARRAS["centro_min"]


def franjas_negras(ruta: Path | str, min_muestras: int = 1) -> list[tuple[float, float]]:
    """Los tramos (desde, hasta, en segundos) de un video donde se ven franjas negras arriba y abajo con la imagen en medio; [] si no hay o si ffmpeg no puede leerlo. `min_muestras`: cuantas
    muestras seguidas (una cada 0,25 s) hacen falta para contarlo: con 2 o mas, un solo cuadro de un fundido no cuenta. Un hueco de una muestra no parte un tramo."""
    ancho, alto, fps = BARRAS["ancho"], BARRAS["alto"], BARRAS["fps"]
    try:
        r = subprocess.run([ejecutable("ffmpeg"), "-v", "error", "-nostdin", "-i", str(ruta), "-an", "-vf", f"fps={fps},scale={ancho}:{alto}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                           capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout:
        return []
    tam = ancho * alto
    tramos: list[list[int]] = []
    for k in range(len(r.stdout) // tam):
        if not cuadro_con_franjas(r.stdout[k * tam:(k + 1) * tam], ancho, alto):
            continue
        if tramos and k - tramos[-1][1] <= 2:
            tramos[-1][1] = k
        else:
            tramos.append([k, k])
    return [(a / fps, (b + 1) / fps) for a, b in tramos if b - a + 1 >= min_muestras]


def bandas_negras_de(video: Path, carpeta_clip: Path) -> list[tuple[float, float]]:
    """`franjas_negras` de un video del b-roll, con la respuesta guardada junto a sus vectores (_clip/<video>.bandas.json) mientras el archivo no cambie (tamano y fecha): la primera vez
    son ~1 s por video, despues se lee de ahi."""
    try:
        st = video.stat()
    except OSError:
        return []
    firma = {"tam": st.st_size, "mtime": int(st.st_mtime), "v": BARRAS["version"]}
    ruta = carpeta_clip / f"{video.stem}.bandas.json"
    try:
        g = leer_json(ruta)
        if g.get("firma") == firma:
            return [(float(a), float(b)) for a, b in g["tramos"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    tramos = franjas_negras(video)
    try:
        escribir_json(ruta, {"firma": firma, "tramos": [[round(a, 2), round(b, 2)] for a, b in tramos]})
    except OSError:
        pass
    return tramos


class Video:
    """Un video del b-roll con todo lo que hace falta para escoger tomas: vectores, fotogramas limpios, cortes y tramos."""

    def __init__(self, nombre: str, ruta_rel: str, datos: dict, entrada: dict, p: dict = UMBRALES, bandas: list[tuple[float, float]] | None = None):
        import numpy as np

        self.nombre, self.ruta_rel, self.entrada = nombre, ruta_rel, entrada
        self.dur = float(datos["meta"]["duracion"])
        self.cada = float(datos["meta"]["cada"])
        self.t = datos["tiempos"].astype(np.float32)
        self.E = vectores_medios(datos)
        e3 = datos["emb"].astype(np.float32)
        self.E3 = e3 / np.maximum(np.linalg.norm(e3, axis=2, keepdims=True), 1e-8)      # los 3 recortes de cada fotograma, cada uno normalizado
        m = motivos_por_fotograma(datos, p)
        self.limpio = m["limpio"].copy()
        self.blando = np.where(m["rostro"], 0.5, 0.0).astype(np.float32)
        self.mov = datos["mov"].astype(np.float32)
        self.cortes = sorted(cortes_de(datos, p))
        self.calidad = ["buena"] * len(self.t)
        self.prohibido = np.zeros(len(self.t), dtype=bool)         # lo que una persona marco `mala`: nunca se usa, ni siquiera cuando falta material
        # lo revisado a mano manda: un tramo `mala` no se usa y uno `media` pesa menos
        manual = entrada.get("tramos") if es_manual(entrada) else None            # `tramos_manual` (lo viejo tras --pisar-manual) solo sirve para comparar
        self.manual = bool(manual)
        for tm in manual or []:
            for i, ti in enumerate(self.t):
                if tm["desde"] - 1e-6 <= ti < tm["hasta"] + 1e-6:
                    if tm["calidad"] == "mala":
                        self.limpio[i] = False
                        self.prohibido[i] = True
                        self.calidad[i] = "mala"
                    elif tm["calidad"] == "media":
                        self.blando[i] = max(self.blando[i], 0.5)
                        self.calidad[i] = "media"
        # lo que una persona marco con --marcar / --marcar-toma (un nino, un logo sin letras...): vale aunque el video se haya reanalizado, y toca todo fotograma que la marca roza
        for m in leer_marcas(entrada, nombre):
            toca = (self.t + self.cada / 2 > m["desde"]) & (self.t - self.cada / 2 < m["hasta"])
            if m["calidad"] == "mala":
                self.limpio[toca] = False
                self.prohibido[toca] = True
            else:
                self.blando[toca] = np.maximum(self.blando[toca], 0.5)
            for i in np.flatnonzero(toca):
                if m["calidad"] == "mala" or self.calidad[i] != "mala":
                    self.calidad[i] = m["calidad"]
        # franjas negras arriba y abajo (las trae el video original): como una marca `mala` puesta a mano, nunca se usan, ni siquiera cuando falta material
        for a, b in bandas or []:
            toca = (self.t + self.cada / 2 > a) & (self.t - self.cada / 2 < b)
            self.limpio[toca] = False
            self.prohibido[toca] = True
            for i in np.flatnonzero(toca):
                self.calidad[i] = "mala"
        self.tramos = entrada.get("tramos") or []
        # «estricto»: limpio Y con los dos fotogramas de al lado tambien limpios. Un subtitulo dura segundos y el detector falla en ~4 % de los fotogramas con texto,
        # asi que un fotograma limpio pegado a uno con texto es sospechoso; solo se usa si no hay otro material (emparejar: relajar = 1).
        self.estricto = self.limpio.copy()
        self.estricto[1:] &= self.limpio[:-1]
        self.estricto[:-1] &= self.limpio[1:]
        # sumas acumuladas para contar fotogramas no limpios en un rango sin recorrerlo
        self._malos = np.concatenate([[0], np.cumsum(~self.limpio)])
        self._malos_estricto = np.concatenate([[0], np.cumsum(~self.estricto)])
        self._prohib = np.concatenate([[0], np.cumsum(self.prohibido)])
        self._cache: dict[float, tuple] = {}

    def sim(self, vec):
        """Parecido de cada fotograma con un vector de frase: mitad el del promedio de sus 3 recortes y mitad el del recorte que mas se le parece (una frase que nombra un objeto
        lo encuentra aunque este solo en la parte de arriba o de abajo del cuadro). Con las descripciones manuales de Dinamicas acierta mas que cualquiera de las dos por separado."""
        import numpy as np

        return 0.5 * (self.E @ vec) + 0.5 * (self.E3 @ vec).max(axis=1)

    def rango(self, s, d):
        """Indices (lo, hi) de los fotogramas que toca la toma [s, s+d]. Un fotograma k cubre desde (k-0.5)*cada hasta (k+0.5)*cada."""
        import numpy as np

        lo = np.floor(s / self.cada + 0.5).astype(int)
        hi = np.ceil((s + d) / self.cada + 0.5).astype(int) - 1
        n = len(self.t)
        return np.clip(lo, 0, n - 1), np.clip(hi, 0, n - 1)

    def ventanas(self, d: float, paso: float, margen_corte: float):
        """Todas las tomas de duracion d que caben en el video sin cruzar un corte de escena: (inicios, lo, hi, no_limpios, no_limpios_estrictos, prohibidos)."""
        import numpy as np

        clave = round(d, 2)
        if clave in self._cache:
            return self._cache[clave]
        ultimo = self.dur - d - 0.05
        if ultimo < 0:
            vacio = (np.zeros(0), np.zeros(0, int), np.zeros(0, int), np.zeros(0, int), np.zeros(0, int), np.zeros(0, int))
            self._cache[clave] = vacio
            return vacio
        s = np.arange(0.0, ultimo + 1e-9, paso)
        lo, hi = self.rango(s, d)
        malos = self._malos[hi + 1] - self._malos[lo]
        malos_e = self._malos_estricto[hi + 1] - self._malos_estricto[lo]
        prohib = self._prohib[hi + 1] - self._prohib[lo]
        if self.cortes:
            c = np.array(self.cortes)
            dentro = np.searchsorted(c, s + d - margen_corte) - np.searchsorted(c, s + margen_corte) > 0
            s, lo, hi, malos, malos_e, prohib = s[~dentro], lo[~dentro], hi[~dentro], malos[~dentro], malos_e[~dentro], prohib[~dentro]
        self._cache[clave] = (s, lo, hi, malos, malos_e, prohib)
        return self._cache[clave]


class Catalogo:
    """Los videos de `broll-tiktok/` de un producto con sus vectores. `faltan` = los que todavia no se analizaron (corre indexar())."""

    def __init__(self, producto: str, p: dict = UMBRALES, carpeta: Path | None = None):
        self.producto = producto
        self.carpeta = carpeta or (producto_dir(producto) / "broll-tiktok")
        ruta = self.carpeta / "indice.json"
        indice = leer_json(ruta) if ruta.is_file() else {"videos": {}}
        self.videos: list[Video] = []
        self.faltan: list[str] = []
        for v in sorted(x for x in self.carpeta.iterdir() if x.is_file() and x.suffix.lower() in EXT_VIDEO) if self.carpeta.is_dir() else []:
            entrada = indice.get("videos", {}).get(v.name) or {}
            datos = cargar_datos(self.carpeta / "_clip" / f"{v.stem}.npz")
            if not datos or datos.get("meta", {}).get("version") != VERSION_ANALISIS:
                self.faltan.append(v.name)
                continue
            self.videos.append(Video(v.name, f"productos/{producto}/broll-tiktok/{v.name}", datos, entrada, p, bandas_negras_de(v, self.carpeta / "_clip")))

    def segundos_limpios(self) -> float:
        """Segundos de b-roll que se pueden usar sin apuros (fotogramas limpios con vecinos limpios)."""
        return round(sum(float(v.estricto.sum()) * v.cada for v in self.videos), 1)

    def tramo_de(self, v: Video, t: float) -> dict | None:
        for tm in v.tramos:
            if tm["desde"] - 1e-6 <= t < tm["hasta"] + 1e-6:
                return tm
        return None


def _z(sim, ok, extra: float = 0.0):
    """Normaliza los parecidos: media 0 y desviacion 1 sobre los fotogramas limpios (cada frase se mide contra el material que de verdad puede usar)."""
    import numpy as np

    base = sim[ok] if ok.any() else sim
    mu, sd = float(base.mean()), float(base.std())
    return (sim - mu) / max(sd, 1e-4)


def emparejar(cat: Catalogo, consultas: list[dict], evitar: list[tuple[str, float, float]] | None = None, reglas: dict | None = None) -> list[dict | None]:
    """Escoge una toma por consulta. consultas = [{'vec': vector 512 normalizado, 'dur': segundos, 'gancho': bool, 'texto': str}] en el orden del anuncio.
    Reglas fijas: ninguna toma se solapa con otra del mismo video, ni con las de `evitar` (las del anuncio anterior: lista de (ruta, desde, hasta)), solo fotogramas limpios
    (sin texto quemado ni cuadros negros o lisos, y lo marcado `mala` a mano nunca), nunca cruza un corte de escena. Si falta material se relaja por pasos y la toma lo dice
    en `relajada`: 0 = todo en regla; 1 = pegada a un fotograma con texto (el suyo esta limpio); 2 = ademas repite una toma del anuncio anterior. `reglas={'tope_seg_video': 5}`: de un
    mismo video de otra persona (es_ajeno) no se usan mas de esos segundos EN TOTAL en el anuncio, y eso NUNCA se relaja (si no alcanzan los videos, la toma queda sin material). Devuelve por consulta
    {'ruta','video','desde','dur','sim','z','puntaje','calidad','que_se_ve','relajada'} (o None si no hay con que)."""
    import numpy as np

    r = {**REGLAS, **(reglas or {})}
    tope = float(r.get("tope_seg_video") or 0)
    n = len(consultas)
    if not cat.videos:
        return [None] * n
    evitar_por_video: dict[str, list[tuple[float, float]]] = {}
    for ruta, a, b in evitar or []:
        evitar_por_video.setdefault(ruta, []).append((float(a), float(b)))
    # parecido de cada fotograma de cada video con cada consulta, ya normalizado
    zs = []
    for q in consultas:
        vec = np.asarray(q["vec"], dtype=np.float32)
        todo_sim = np.concatenate([v.sim(vec) for v in cat.videos])
        todo_ok = np.concatenate([v.limpio for v in cat.videos])
        z_total = _z(todo_sim, todo_ok)
        partes, k = [], 0
        for v in cat.videos:
            partes.append(z_total[k:k + len(v.t)])
            k += len(v.t)
        zs.append(partes)
    elegidas: list[dict | None] = [None] * n
    # Se escoge primero lo mas especifico: la primera toma (el gancho) y despues las frases cuya mejor toma se parece MAS a lo que dicen. Asi una frase abstracta
    # («pero espera, hay mas») no se lleva la unica toma buena de lo que otra frase nombra.
    fuerza = [float(max((zs[i][vi][v.limpio].max() if v.limpio.any() else -9.0) for vi, v in enumerate(cat.videos))) for i in range(n)]
    orden = sorted(range(n), key=lambda i: (0 if consultas[i].get("gancho") else 1, -fuerza[i], i))

    def mejor(i: int, relajar: int) -> dict | None:
        q = consultas[i]
        d = float(q["dur"])
        usos, seg_usados = {}, {}
        for j, e in enumerate(elegidas):
            if e is not None and j != i:
                usos[e["video"]] = usos.get(e["video"], 0) + 1
                seg_usados[e["video"]] = seg_usados.get(e["video"], 0.0) + float(e["dur"])
        vecinos = {elegidas[j]["video"] for j in (i - 1, i + 1) if 0 <= j < n and elegidas[j] is not None}
        mejor_c, mejor_p = None, -1e9
        for vi, v in enumerate(cat.videos):
            if tope > 0 and es_ajeno(v.nombre) and seg_usados.get(v.nombre, 0.0) + d > tope + 1e-6:
                continue                                    # este video de otra persona ya dio sus segundos en este anuncio: es una regla, no una preferencia (no se relaja)
            s, lo, hi, malos, malos_e, prohib = v.ventanas(d, r["paso"], r["margen_corte"])
            if len(s) == 0:
                continue
            ok = ((malos_e == 0) if relajar < 1 else (malos == 0)) & (prohib == 0)
            if not ok.any():
                continue
            # parecido medio de los fotogramas que toca cada toma (suma acumulada: sin bucles por toma)
            zf = zs[i][vi]
            cz = np.concatenate([[0.0], np.cumsum(zf)])
            cm = np.concatenate([[0.0], np.cumsum(v.mov)])
            cb = np.concatenate([[0.0], np.cumsum(v.blando)])
            cuantos = (hi - lo + 1).astype(np.float32)
            zmed = (cz[hi + 1] - cz[lo]) / cuantos
            mov = (cm[hi + 1] - cm[lo]) / cuantos
            blando = (cb[hi + 1] - cb[lo]) / cuantos
            puntaje = zmed * (r["peso_gancho"] if q.get("gancho") else 1.0) + r["peso_movimiento"] * mov - r["pen_blando"] * blando
            if v.manual:
                buena = np.array([all(v.calidad[k] == "buena" for k in range(a, b + 1)) for a, b in zip(lo, hi)])
                puntaje = puntaje + r["peso_buena"] * buena
            puntaje = puntaje - r["pen_uso"] * min(usos.get(v.nombre, 0), 4)
            if v.nombre in vecinos:
                puntaje = puntaje - r["pen_vecina"]
            valido = ok.copy()
            for j, e in enumerate(elegidas):
                if e is None or j == i or e["video"] != v.nombre:
                    continue
                a0, b0 = e["desde"] - r["separacion"], e["desde"] + e["dur"] + r["separacion"]
                valido &= ~((s < b0) & (s + d > a0))
                cerca = (np.abs(s - e["desde"]) < r["cerca"] + d) & ~((s < b0) & (s + d > a0))
                puntaje = puntaje - r["pen_cerca"] * cerca
            if relajar < 2:
                for a1, b1 in evitar_por_video.get(v.ruta_rel, []):
                    valido &= ~((s < b1 + 0.3) & (s + d > a1 - 0.3))
            if not valido.any():
                continue
            puntaje = np.where(valido, puntaje, -1e9)
            k = int(np.argmax(puntaje))
            if puntaje[k] > mejor_p:
                mejor_p = float(puntaje[k])
                tm = cat.tramo_de(v, float(s[k]) + d / 2)
                calidad_toma = (tm or {}).get("calidad", "buena")
                if any(v.calidad[q] == "media" for q in range(int(lo[k]), int(hi[k]) + 1)):                      # una marca `media` de una persona pesa aunque el tramo salga `buena`
                    calidad_toma = "media"
                mejor_c = {"ruta": v.ruta_rel, "video": v.nombre, "desde": round(float(s[k]), 2), "dur": round(d, 2), "z": round(float(zmed[k]), 2),
                           "sim": round(float(np.mean(v.sim(np.asarray(q["vec"], dtype=np.float32))[lo[k]:hi[k] + 1])), 3), "puntaje": round(mejor_p, 2),
                           "calidad": calidad_toma, "que_se_ve": (tm or {}).get("que_se_ve", ""), "relajada": relajar}
        return mejor_c

    for i in orden:                                 # pasada 1: de lo mas especifico a lo mas general
        for relajar in (0, 1, 2):
            elegidas[i] = mejor(i, relajar)
            if elegidas[i] is not None:
                break
    for _ in range(2):                              # pasadas de mejora: cada toma se reconsidera sabiendo que escogieron las demas
        for i in orden:
            anterior = elegidas[i]
            elegidas[i] = None
            nueva = None
            for relajar in (0, 1, 2):
                nueva = mejor(i, relajar)
                if nueva is not None:
                    break
            elegidas[i] = nueva or anterior
    return elegidas


def probar_frases(producto: str, ruta: Path, cuantas: int) -> None:
    """Prueba del emparejamiento: por cada frase de `ruta`, las mejores tomas del b-roll (sin repetir entre frases)."""
    import json

    import clip_local

    try:
        motor = clip_local.MotorClip()
    except clip_local.FaltanModelos as e:
        salir(str(e))
    cat = Catalogo(producto)
    if cat.faltan:
        salir(f"Faltan analizar {len(cat.faltan)} video(s): corre primero `python scripts/emparejar_tomas.py {producto}`")
    if ruta.suffix.lower() == ".json":
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        frases = [x.get("texto") or x.get("es") or "" for x in (datos.get("frases") or datos.get("segmentos") or [])]
    else:
        frases = [x.strip() for x in ruta.read_text(encoding="utf-8").splitlines() if x.strip()]
    consultas = [{"vec": v, "dur": 2.0, "gancho": i == 0, "texto": f} for i, (f, v) in enumerate(zip(frases, motor.textos(frases)))]
    elegidas = emparejar(cat, consultas)
    for q, e in zip(consultas, elegidas):
        print(f"- {q['texto'][:80]}")
        if e:
            print(f"    {e['video']} {e['desde']:.1f}-{e['desde'] + e['dur']:.1f} s  z={e['z']:+.2f}  sim={e['sim']}  · {e['que_se_ve']}")
        else:
            print("    (sin toma disponible)")


def main() -> None:
    configurar_consola()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("producto", nargs="?")
    ap.add_argument("--cada", type=float, default=CADA_DEFECTO)
    ap.add_argument("--rehacer", action="store_true")
    ap.add_argument("--solo", nargs="+")
    ap.add_argument("--sin-texto", action="store_true")
    ap.add_argument("--pisar-manual", action="store_true")
    ap.add_argument("--comparar", action="store_true")
    ap.add_argument("--recalcular", action="store_true", help="vuelve a calcular los tramos con los umbrales actuales usando lo ya guardado (no decodifica video)")
    ap.add_argument("--frases")
    ap.add_argument("--cuantas", type=int, default=3)
    ap.add_argument("--solo-descargar", action="store_true", help="solo baja los modelos de CLIP y termina (lo usa el instalador)")
    ap.add_argument("--marcar", nargs=2, metavar=("VIDEO", "TRAMO"), help="marca a mano un tramo del b-roll (segundos: 17-21; o «todo» el video). Nunca se vuelve a usar")
    ap.add_argument("--marcar-toma", nargs="+", metavar="ANUNCIO_Y_TOMAS", help="marca la(s) toma(s) de un anuncio ya montado (el #N de evidencia.png): --marcar-toma AD1 7 9")
    ap.add_argument("--quitar-marca", nargs="+", metavar="VIDEO_Y_TRAMO", help="borra las marcas de un video (o las que se cruzan con el tramo): --quitar-marca 05108697 [17-21]")
    ap.add_argument("--marcas", action="store_true", help="lista las marcas puestas a mano")
    ap.add_argument("--calidad", choices=CALIDADES_MARCA, default="mala", help="con --marcar / --marcar-toma: mala (nunca se usa, por defecto) o media (se prefiere otra toma)")
    ap.add_argument("--motivo", default="", help="con --marcar / --marcar-toma: por que (queda anotado)")
    a = ap.parse_args()

    if a.solo_descargar:
        import clip_local

        print(f"Modelos de CLIP en {clip_local.CARPETA} (~315 MB, una sola vez)...")
        clip_local.descargar()
        print("Modelos de CLIP listos.")
        return
    if not a.producto:
        salir("Falta el producto. Ejemplo: python scripts/emparejar_tomas.py dinamicas")
    if a.marcas:
        listar_marcas(a.producto)
        return
    if a.marcar:
        marcar(a.producto, a.marcar[0], a.marcar[1], a.calidad, a.motivo)
        return
    if a.marcar_toma:
        try:
            numeros = [int(x) for x in a.marcar_toma[1:]]
        except ValueError:
            numeros = []
        if len(a.marcar_toma) < 2 or not numeros:
            salir("Uso: --marcar-toma AD1 7 (el anuncio y el numero de toma que sale en evidencia.png; puedes poner varios numeros).")
        marcar_toma(a.producto, a.marcar_toma[0], numeros, a.calidad, a.motivo)
        return
    if a.quitar_marca:
        quitar_marcas(a.producto, a.quitar_marca[0], a.quitar_marca[1] if len(a.quitar_marca) > 1 else None)
        return
    if a.frases:
        probar_frases(a.producto, Path(a.frases), a.cuantas)
        return
    if a.recalcular:
        recalcular(a.producto, a.pisar_manual)
        if a.comparar:
            comparar_con_manual(a.producto)
        return
    r = indexar(a.producto, a.cada, a.rehacer, a.solo, a.sin_texto, a.pisar_manual)
    print(f"\nListo: {r['analizados']} video(s) analizados, {r['al_dia']} ya estaban al dia; {r['fotogramas']} fotogramas, {r['tramos']} tramos"
          + (f", {r['segundos']} s, RAM pico {r['ram_pico_mb']} MB." if r["analizados"] else "."))
    if a.comparar:
        comparar_con_manual(a.producto)


if __name__ == "__main__":
    main()
