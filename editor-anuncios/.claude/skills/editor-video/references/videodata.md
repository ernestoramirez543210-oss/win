# videoData.json — referencia de campos

Cada anuncio se edita cambiando este archivo, no el código. El contrato exacto (TypeScript) está en `src/types.ts`; la composición que lo pinta es `src/FullVideo.tsx`. Cada anuncio guarda su copia en `anuncios/<producto>/<tipo>/<anuncio>/videoData.json`; `src/config/videoData.json` es solo la demo que se ve en `npm run studio`.

**Tiempos en segundos. Rutas relativas a `public/`** (también se aceptan rutas que empiezan por `anuncios/`).

```json
{
  "meta": {"anuncio": "AD3", "producto": "velas", "tipo": "tiktok", "fps": 30, "ancho": 1080, "alto": 1920, "duracion": 42.5},
  "estilo": {
    "fondo": "#000000", "colorPrimario": "#E11D48",
    "subtitulos": {"tamano": 82, "color": "#FFFFFF", "colorActivo": "#FFE14D", "contorno": "#000000", "maxPalabras": 3, "mayusculas": true, "pausaSeg": 0.4, "margenInferior": 40},
    "textoGrande": {"tamano": 112}
  },
  "zonaInferiorLibre": 280,
  "tomas": [
    {"src": "productos/velas/broll-tiktok/v01.mp4", "desde": 3.2, "inicio": 0, "duracion": 2.0, "ajuste": "cover", "posX": 0.5, "zoom": 1.06, "nota": "manos vertiendo cera · frase 1"}
  ],
  "palabras": [{"texto": "Descubre", "inicio": 0.12, "fin": 0.55}],
  "textosGrandes": [{"texto": "Velas caseras", "inicio": 1.2, "fin": 2.6, "estilo": "caja", "posicion": "centro", "color": "#E11D48"}],
  "producto": [{"src": "productos/velas/portada.jpg", "inicio": 3.0, "duracion": 1.4, "modo": "tarjeta", "posicion": "centro", "giro": -4}],
  "cta": {"texto": "Escríbenos", "subtexto": "link en la descripción", "inicio": 40.0, "duracion": 2.5},
  "audio": {
    "voz": {"src": "anuncios/velas/tiktok/AD3/audio/voz_norm.wav", "volumen": 1},
    "musica": {"src": "anuncios/velas/tiktok/AD3/audio/musica_ajustada.mp3", "volumen": 1, "fadeInSeg": 0.6, "fadeOutSeg": 1.5},
    "sfx": [{"src": "sfx/pop.mp3", "t": 1.2, "volumen": 0.5}]
  }
}
```

| Campo | Qué es |
|---|---|
| `meta.duracion` | Duración total del anuncio. Normalmente = duración de la voz + ~0,5 s de cola. |
| `tomas[]` | Línea de tiempo del video. `desde` = segundo del video original; `inicio`/`duracion` = dónde cae en el anuncio. `ajuste: "cover"` recorta a 9:16 sin deformar (`posX`/`posY` 0–1 eligen el centro del recorte). `velocidad` y `zoom` son opcionales. Las tomas van **siempre silenciadas** (solo suena la locución). Sin huecos: el negro se nota. `transicion: {"tipo": "fundido"|"deslizar"|"zoom"|"flash", "duracion": 0.25}` (opcional; no en la primera toma) es cómo ENTRA la toma sobre la anterior: la pone `scripts/aplicar_receta.py` según la receta de la Fábrica de ofertas (ver la skill `fabrica-ofertas`); sin el campo el corte es seco. `continuacion: true` (opcional) marca un tramo SEGUIDO del mismo clip que la toma anterior (un clip del gancho con IA repartido en varios cortes): no es un corte de verdad y `aplicar_receta.py` no le pone transición ni sonido. |
| `palabras[]` | Palabra por palabra de Whisper (`transcripcion.json` → `palabras`: copiar `texto`, `inicio`, `fin`). Los subtítulos se agrupan solos (`maxPalabras`, pausas, fin de frase) y resaltan la palabra activa. Se ocultan solos cuando hay un texto grande. |
| `textosGrandes[]` | 5 a 7 por anuncio, sobre las frases que venden. `estilo`: `caja` (caja de color), `plano` (blanco con contorno), `resaltado` (color con contorno). `posicion`: `centro`, `arriba`, `abajo`. |
| `producto[]` | Portada, páginas del ebook o fotos del producto sobre el video. `modo`: `tarjeta` (flotante con sombra) o `pantalla` (ocupa el cuadro). |
| `cta` | Llamada a la acción final (botón). Siempre encima de la zona libre inferior. Opcional: si falta (o es `null`) no sale ninguna; hasta la versión 2026.10.14-2 una clave ausente heredaba la de la demo («Prueba superada»), ya no. |
| `audio.voz` | `voz_norm.wav` que deja `mezclar_audio.py` (−14 LUFS). |
| `audio.musica` | `musica_ajustada.mp3` que deja `mezclar_audio.py` (~15 dB bajo la voz): va con `volumen: 1`. Solo música de `public/musica/biblioteca/`. |
| `audio.sfx[]` | Efectos puntuales (`t` = segundo del anuncio). Solo en transiciones, textos grandes y llamada a la acción; nunca en cada subtítulo. Los que llevan `"origen": "receta"` los rehace `aplicar_receta.py` cada vez que se corre. |
| `zonaInferiorLibre` | 280 px mínimo: ahí va la interfaz de la red social. Nada de texto cae ahí. |

## Anuncios con IA

La línea de tiempo es una sola toma con `base.mp4` (los clips ya unidos por `unir_clips.py`, silenciado) y la voz es `voz_norm.wav`. Los textos grandes y el producto se colocan usando `clips.json` (dónde cae cada clip).
