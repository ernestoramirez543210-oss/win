"""Motor CLIP LOCAL del editor: convierte imagenes y frases en vectores que se pueden comparar, para elegir que toma del b-roll
va con cada frase del anuncio. Sin LLM, sin GPU, sin red al usarlo (la red solo se usa UNA vez, para bajar los modelos).

Dos piezas, corriendo con onnxruntime (que el editor ya trae por rembg):
  - IMAGEN: CLIP ViT-B/32 de OpenAI (licencia MIT), en la conversion de Xenova con pesos de media precision (fp16: identica a la completa, coseno 1,00000, y la
            mitad de peso: 168 MB). La version int8 que trae el mismo repo NO sirve: cambia el orden de los fotogramas (coincide 5,5 de 10 con la completa).
  - TEXTO:  clip-ViT-B-32-multilingual-v1 de sentence-transformers (licencia Apache-2.0), comprimido a int8 (coseno 0,999 con el completo): entiende espanol,
            portugues, ingles y ~50 idiomas mas y los lleva al MISMO espacio que las imagenes de CLIP. 129 MB.
Por eso una frase en espanol de la voz (o el `visual.en` en ingles del guion) se compara directo con un fotograma.
Los archivos van fijados por revision y por SHA-256: si Hugging Face cambia o corrompe uno, no se usa.

Uso desde otros scripts:   from clip_local import MotorClip, modelos_listos
Por linea de comandos:     python scripts/clip_local.py --descargar     (baja y verifica los modelos, ~300 MB, una sola vez)
                           python scripts/clip_local.py --estado        (dice si estan completos)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _comun import RAIZ, configurar_consola  # noqa: E402

CARPETA = RAIZ / "modelos" / "clip"
VERSION_MOTOR = "clip-b32-fp16+mclip-b32-q8/1"   # cambia si cambian los modelos o el pre-proceso: invalida lo guardado en disco
DIM = 512

_XENOVA = ("Xenova/clip-vit-base-patch32", "d15189d7028b43f1d3e65039190477f6af591c2a")
_MCLIP = ("sentence-transformers/clip-ViT-B-32-multilingual-v1", "58edf8cada9e398793dca955574a48cbb7f18be2")

# nombre local -> (repo, revision, archivo en el repo, bytes, sha256)
ARCHIVOS = {
    "imagen.onnx": (*_XENOVA, "onnx/vision_model_fp16.onnx", 176080659, "35c4e0fb0aeee527dcde1693520b214a34424a786babd530f35366bad5844efd"),
    "texto.onnx": (*_MCLIP, "onnx/model_quint8_avx2.onnx", 135377779, "fbc8fbeaa5237d96bd1bf430057c70d34de8334a463e933a5faa305f6caeed9c"),
    "texto_tokenizer.json": (*_MCLIP, "tokenizer.json", 1961847, "5b4e1a8171c81dfd666ae40265b9530c6e0b3d53923fe8ac493dcc84229adf81"),
    "texto_proyeccion.safetensors": (*_MCLIP, "2_Dense/model.safetensors", 1572984, "d12568dc7300970a4d3dbb49068ad16cd89b99840b74b026f8e48071e9414f74"),
}
MEDIA = (0.48145466, 0.4578275, 0.40821073)
DESV = (0.26862954, 0.26130258, 0.27577711)
LADO = 224
MAX_TOKENS = 64          # una frase de un anuncio cabe de sobra; mas largo se recorta


class FaltanModelos(Exception):
    """No estan los modelos de CLIP (o estan incompletos). Se bajan con `python scripts/clip_local.py --descargar`."""


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def estado(carpeta: Path | None = None, verificar_huella: bool = False) -> dict[str, str]:
    """{nombre: 'ok' | 'falta' | 'tamano' | 'huella'} de cada archivo de los modelos."""
    carpeta = carpeta or CARPETA
    out: dict[str, str] = {}
    for nombre, (_, _, _, tam, sha) in ARCHIVOS.items():
        ruta = carpeta / nombre
        if not ruta.is_file():
            out[nombre] = "falta"
        elif ruta.stat().st_size != tam:
            out[nombre] = "tamano"
        elif verificar_huella and _sha256(ruta) != sha:
            out[nombre] = "huella"
        else:
            out[nombre] = "ok"
    return out


def modelos_listos(carpeta: Path | None = None) -> bool:
    return all(v == "ok" for v in estado(carpeta).values())


def descargar(carpeta: Path | None = None, forzar: bool = False, mostrar=print) -> None:
    """Baja lo que falte de Hugging Face (revision fija) y comprueba tamano y SHA-256. Es lo UNICO de este modulo que abre la red."""
    import requests

    carpeta = carpeta or CARPETA
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre, (repo, rev, archivo, tam, sha) in ARCHIVOS.items():
        destino = carpeta / nombre
        if not forzar and destino.is_file() and destino.stat().st_size == tam and _sha256(destino) == sha:
            mostrar(f"  ya esta: {nombre}")
            continue
        url = f"https://huggingface.co/{repo}/resolve/{rev}/{archivo}"
        mostrar(f"  bajando {nombre} ({tam / 1048576:.1f} MB) de {repo} ...")
        t0 = time.time()
        fd, tmp = tempfile.mkstemp(prefix=nombre + ".", suffix=".parte", dir=str(carpeta))
        os.close(fd)
        try:
            h = hashlib.sha256()
            with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "editor-anuncios/clip_local"}) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for bloque in r.iter_content(1 << 20):
                        f.write(bloque)
                        h.update(bloque)
            if Path(tmp).stat().st_size != tam or h.hexdigest() != sha:
                raise RuntimeError(f"{nombre}: lo bajado no coincide con el archivo fijado (tamano o SHA-256). No se usa.")
            os.replace(tmp, destino)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        mostrar(f"    listo en {time.time() - t0:.0f} s")


def _leer_safetensors(ruta: Path, nombre: str):
    """Lee UN tensor de un .safetensors sin la libreria safetensors: 8 bytes con el largo del encabezado, el encabezado en JSON y los datos."""
    import numpy as np

    with open(ruta, "rb") as f:
        n = int.from_bytes(f.read(8), "little")
        cab = json.loads(f.read(n).decode("utf-8"))
        info = cab[nombre]
        if info["dtype"] != "F32":
            raise ValueError(f"{nombre}: se esperaba F32 y es {info['dtype']}")
        a, b = info["data_offsets"]
        f.seek(8 + n + a)
        datos = f.read(b - a)
    return np.frombuffer(datos, dtype="<f4").reshape(info["shape"]).copy()


def normalizar(v):
    import numpy as np

    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-8)


class MotorClip:
    """Vectores de imagenes y de frases en el mismo espacio. Los vectores salen normalizados (largo 1): el parecido es un simple producto punto."""

    version = VERSION_MOTOR

    def __init__(self, carpeta: Path | None = None, hilos: int | None = None):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.carpeta = carpeta or CARPETA
        if not modelos_listos(self.carpeta):
            faltan = [k for k, v in estado(self.carpeta).items() if v != "ok"]
            raise FaltanModelos(f"Faltan los modelos de CLIP ({', '.join(faltan)}). Corre: python scripts/clip_local.py --descargar")
        self.np = np
        opciones = ort.SessionOptions()
        opciones.log_severity_level = 3
        # medido en el PC del dueno (2 nucleos, 4 hilos): con 3 hilos va mas rapido que con 4 (127 contra 202 ms por imagen); un servidor de 2 vCPU usa 2
        opciones.intra_op_num_threads = hilos or max(2, min(4, (os.cpu_count() or 2) - 1))
        opciones.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._img = ort.InferenceSession(str(self.carpeta / "imagen.onnx"), opciones, providers=["CPUExecutionProvider"])
        self._txt = ort.InferenceSession(str(self.carpeta / "texto.onnx"), opciones, providers=["CPUExecutionProvider"])
        self._tok = Tokenizer.from_file(str(self.carpeta / "texto_tokenizer.json"))
        self._tok.enable_truncation(max_length=MAX_TOKENS)
        self._tok.enable_padding(pad_id=self._tok.token_to_id("[PAD]") or 0, pad_token="[PAD]")
        self._proy = _leer_safetensors(self.carpeta / "texto_proyeccion.safetensors", "linear.weight")   # (512, 768), sin sesgo
        self._media = np.array(MEDIA, dtype=np.float32).reshape(1, 1, 3)
        self._desv = np.array(DESV, dtype=np.float32).reshape(1, 1, 3)
        self._cache_texto: dict[str, "np.ndarray"] = {}

    # ---------------------------------------------------------------- imagenes
    def preparar(self, im):
        """PIL -> (3, 224, 224) float32 como lo pide CLIP: cuadrado central (equivale a achicar el lado corto a 224 y recortar el centro, pero 8 veces mas rapido
        porque se recorta ANTES de achicar), bicubico y normalizado con la media y la desviacion de CLIP."""
        from PIL import Image

        np = self.np
        im = im.convert("RGB")
        w, h = im.size
        lado = min(w, h)
        x0, y0 = (w - lado) // 2, (h - lado) // 2
        im = im.crop((x0, y0, x0 + lado, y0 + lado)).resize((LADO, LADO), Image.BICUBIC)
        a = (np.asarray(im, dtype=np.float32) / 255.0 - self._media) / self._desv
        return a.transpose(2, 0, 1)

    def imagenes(self, ims, lote: int = 16):
        """Lista de imagenes PIL -> matriz (N, 512) normalizada."""
        np = self.np
        out = []
        for i in range(0, len(ims), lote):
            x = np.stack([self.preparar(im) for im in ims[i:i + lote]]).astype(np.float32)
            out.append(self._img.run(["image_embeds"], {"pixel_values": x})[0])
        return normalizar(np.concatenate(out)) if out else np.zeros((0, DIM), dtype=np.float32)

    # ---------------------------------------------------------------- textos
    def _vectorizar(self, textos: list[str]):
        np = self.np
        enc = self._tok.encode_batch(textos)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mascara = np.array([e.attention_mask for e in enc], dtype=np.int64)
        oculto = self._txt.run(["last_hidden_state"], {"input_ids": ids, "attention_mask": mascara})[0]
        m = mascara[..., None].astype(np.float32)
        promedio = (oculto * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)       # promedio de las palabras (sin relleno)
        return promedio @ self._proy.T

    def textos(self, textos: list[str], lote: int = 32):
        """Lista de frases (en el idioma que sea) -> matriz (N, 512) normalizada. Recuerda lo ya calculado."""
        np = self.np
        pendientes = [t for t in dict.fromkeys(textos) if t not in self._cache_texto]
        for i in range(0, len(pendientes), lote):
            parte = pendientes[i:i + lote]
            for t, v in zip(parte, normalizar(self._vectorizar(parte))):
                self._cache_texto[t] = v
        return np.stack([self._cache_texto[t] for t in textos]) if textos else np.zeros((0, DIM), dtype=np.float32)


def main() -> None:
    configurar_consola()
    args = sys.argv[1:]
    if "--descargar" in args:
        print(f"Modelos de CLIP en {CARPETA}")
        descargar(forzar="--forzar" in args)
        malos = {k: v for k, v in estado(verificar_huella=True).items() if v != "ok"}
        if malos:
            print(f"ERROR: {malos}", file=sys.stderr)
            sys.exit(1)
        print("Modelos de CLIP completos y verificados.")
        return
    if "--estado" in args:
        e = estado(verificar_huella="--huella" in args)
        for k, v in e.items():
            print(f"  {k}: {v}")
        sys.exit(0 if all(v == "ok" for v in e.values()) else 1)
    print(__doc__)


if __name__ == "__main__":
    main()
