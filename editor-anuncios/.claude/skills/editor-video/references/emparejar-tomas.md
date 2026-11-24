# Anuncio automático: emparejar tomas y montaje sin tokens (2026-10-20)

Pedido del dueño: «cuando elija una oferta, tú creas todo» y «todo tiene que ser por debajo, para consumir la cantidad mínima de tokens». Estos scripts hacen, **por código y sin gastar tokens de Claude**, lo que antes hacía Claude mirando hojas de contacto: clasificar el b-roll, escoger la toma de cada frase, armar el `videoData.json`, renderizar y revisar el resultado. Claude (Claudia) solo entra si alguien quiere pulir el anuncio.

## Los tres scripts

| Script | Qué hace |
|---|---|
| `clip_local.py` | Motor CLIP local (onnxruntime, sin red al usarlo). Imagen: CLIP ViT-B/32 de OpenAI en fp16 (168 MB). Texto: `clip-ViT-B-32-multilingual-v1` en int8 (129 MB): entiende español, portugués e inglés y los lleva al MISMO espacio que las imágenes. Los modelos van en `modelos/clip/`, fijados por revisión y SHA-256 (`python scripts/emparejar_tomas.py --solo-descargar`, ~315 MB, una vez; el instalador lo hace). |
| `emparejar_tomas.py <producto>` | Analiza cada video de `broll-tiktok/` (los nuevos o cambiados): fotogramas cada 1 s, la franja de 9:16 que se verá en el anuncio partida en 3 cuadrados para CLIP, detector de texto (RapidOCR) sobre esa franja, cortes de escena y movimiento. Deja los `tramos` en `indice.json` (con `que_se_ve`, `etiquetas` en español, `calidad` buena/media/mala y `motivos`), la calidad del video entero (`calidad_video`, `limpio_seg`) y los vectores en `broll-tiktok/_clip/<video>.npz`. También trae `Catalogo` y `emparejar()` (lo que usa el montaje) y `--frases` para probar el emparejamiento. |
| `montaje_auto.py <producto> <AD>` | De la voz y su transcripción al mp4 revisado (ver abajo). |

## Cómo escoge la toma de cada frase (reglas fijas, siempre igual)

1. La voz se parte en frases (punto, pregunta o pausa larga) y en cortes de `ritmo.corteMin`–`corteMax` de la receta (por defecto 1–3 s), cortando en las pausas entre palabras. Los cortes tapan todo el anuncio, sin huecos.
2. Cada corte tiene un vector: la frase entera (lo que se dice), el pedazo que cae en ese corte, la descripción visual en inglés del `guion.json` si hay (`visual.en`) y un poco del tema de la oferta (`broll.json › consultas`, `producto.json`).
3. Por cada corte se prueban todas las tomas posibles de esa duración (cada 0,25 s dentro de cada video). Una toma **solo vale** si: todos sus fotogramas están limpios (sin texto quemado, sin cuadro negro, blanco o liso) y los de al lado también; no cruza un corte de escena; no se solapa con otra toma del mismo video ni con las del anuncio anterior (`historial.json`); y no está en un tramo que una persona marcó `mala` (ver «Marcar lo que el automático no ve»).
4. Entre las que valen gana la de mayor parecido (normalizado contra el material que de verdad puede usar), con un poco de bono por movimiento y castigos suaves por repetir video o quedar pegada a la anterior (variedad). Se escoge primero lo más específico (el gancho y las frases cuya mejor toma se parece MÁS a lo que dicen), para que una frase abstracta no se lleve la única toma buena de lo que otra nombra.
5. **Tope por video de otra persona** (decisión del dueño, 2026-10-21): de un mismo video de TikTok (`tt_*`, los que baja `broll_tiktok.py`; un clip bajado a mano cuenta si su nombre empieza con `tt_`) el anuncio usa como máximo `ritmo.maxSegPorVideoAjeno` segundos EN TOTAL (5 por defecto; si es menor que el corte más largo se sube a ese corte). Esto NUNCA se relaja: con 4 videos y tope de 5 s solo alcanzan 20 s de anuncio, y el montaje se detiene diciendo cuántos videos distintos hacen falta. El montaje deja `fuentes_ajenas.json` (autor, enlace, segundos y tramos de cada video usado) y la misma tabla en `plan.md`, y el control de calidad mide el tope en el videoData que se renderiza (`tope_por_video_ajeno`).
6. Si falta material se relaja por pasos y la toma lo anota (`relajada`): 1 = pegada a un fotograma con texto (el suyo está limpio); 2 = además repite una toma del anuncio anterior. Si ni así hay, el montaje se detiene y dice cuántos segundos de b-roll limpio faltan (nunca deja huecos negros).

## Lo que arma `montaje_auto.py`

Textos grandes (5 a 7, lo que pide la receta): el gancho en los primeros segundos y las frases con cifras («30 dinámicas»), garantía, algo gratis o un beneficio, separados 3 s y sin tocar el botón final; si `guion.json` trae `textoGrande`, ese manda. Portada del ebook al nombrarse el producto y hasta `producto.paginas` páginas de adentro (si nunca se nombra, a un tercio y a la mitad del anuncio); nunca encima de un texto grande ni del botón. Botón final con el texto de la receta (o «Pídelo ahora»). Música de la biblioteca según `musica.estilo` (sin música si la biblioteca está vacía). Luego corre `aplicar_receta.py` y `validar_videodata.py` (los de siempre), arma `evidencia.png` y `plan.md`, renderiza con `remotion.mjs` (`--concurrencia 1` si quedan menos de 4,5 GB libres) y hace el control de calidad.

**Gancho nuevo con IA (etapa S5b, `scripts/gancho_ia.py`):** si la receta pide `gancho.modo = nuevo-modelado` y existe `gancho/gancho.json` completo o parcial en la carpeta del anuncio, el montaje pone sus tomas (hasta 3, imágenes quietas o clips de IA nuestros) en los primeros cortes: una imagen cubre un corte y un clip de IA cubre los cortes seguidos que hagan falta hasta llenar su `duracion_sugerida` (cada corte toma el tramo que sigue del mismo clip, sin repetir), con el zoom y el ajuste que traen (si el archivo dura menos que el corte, le baja la velocidad hasta un 25 %), usa su `texto_grande` como el texto grande del gancho y empareja con el b-roll solo los cortes que siguen. Sin ese archivo, o con `sin-gancho` o `conservar`, el gancho también sale del b-roll. El informe anota cuántas tomas del gancho se usaron (`entradas.gancho_ia`).

**Control de calidad** (`informe_calidad.json`): duración dentro de la receta; −14 LUFS (±1,5); sin cuadros negros; sin franjas negras arriba y abajo (`sin_franjas_negras`: un clip de TikTok exportado con marco negro; el catálogo ya no usa esos tramos, `emparejar_tomas.franjas_negras`); ninguna toma repetida; subtítulos presentes (se leen en el video final con el detector de texto); nada en los 280 px de abajo (también con el detector, en cuadros del video final). Cada compuerta dice PASA, FALLA o «sin verificar» (cuando falta el detector de texto: no se da por buena). Código de salida: 0 todo pasó, 2 quedó el video pero algo falló, 1 no se pudo armar.

**`evidencia.png`**: una miniatura por corte (la franja de 9:16 que se ve), con el segundo, lo que se dice y el parecido (`z`); borde verde = tramo bueno, ámbar = medio, rojo = sin toma. Se juzga en 10 segundos: lo que se dice contra lo que se ve.

## Qué entiende de `guion.json` (etapa S3, opcional)

```json
{"frases": [{"texto": "frase en español tal como se dice", "rol": "gancho|problema|giro|prueba|oferta|llamada",
             "visual": {"en": "close-up of women's hands cutting paper", "etiquetas": ["manos"], "producto": false},
             "textoGrande": "30 DINÁMICAS", "inicio": 0.0, "fin": 2.1}],
 "cta": {"texto": "Pídelo ahora", "subtexto": ""}}
```
Las frases se alinean solas con las palabras de la transcripción (`inicio`/`fin` no hacen falta). Sin `guion.json` todo sale de la transcripción.

## Lo medido (PC del dueño: i7-6500U de 2 núcleos y 4 hilos, 16 GB, sin GPU; ocupado con Chrome y otras sesiones)

- **Modelos:** 315 MB en disco, una sola vez. RapidOCR (detector de texto) suma ~135 MB al `.venv`.
- **CLIP imagen (fp16):** 127–139 ms por imagen con 3 hilos si el PC está libre (270–430 ms con el PC ocupado). La versión int8 del mismo repo NO sirve: cambia el orden de los fotogramas (coincide 5,5 de 10 con la completa) y por eso no se usa. La fp16 da coseno 1,00000 contra la fp32.
- **Análisis del b-roll de Dinámicas** (13 videos, 681 fotogramas, 703 s de video), con el PC ocupado: 39 min (ffmpeg 4,6 + CLIP 19,5 + texto 13); RAM pico 969 MB. Es de una sola vez por video; sin competencia sería ~15 min. Para acortar: `--cada 2`.
- **Calidad del emparejamiento** (descripciones en español que escribió Claude de 21 tramos de Dinámicas, buscadas entre 681 fotogramas): la primera toma cae en el video correcto 18 de 21 veces (86 %) y en el tramo correcto 14 de 21; el tramo correcto está entre las 5 primeras 19 de 21 veces (90 %).
- **Un anuncio de punta a punta** (`_autodin` AD1: el b-roll de Dinámicas con enlaces duros, un ebook de mentira y la voz Helena de Windows, 49,5 s, 138 palabras): salió un mp4 de 50,0 s, 1080x1920, 30 fps, −14,3 LUFS, que **pasó solo las 10 compuertas** (subtítulos legibles en 8 de 8 cuadros revisados del video final; nada en los 280 px de abajo en 12 cuadros; sin cuadros negros; ninguna toma repetida). Tiempos con el PC casi sin memoria libre (≤ 1 GB; el navegador del dueño y otras sesiones abiertas): Whisper 49 s (1× la duración del audio), cargar el b-roll ya analizado y los modelos 4 s, emparejar 25 cortes 2 s, `evidencia.png` 30 s, **render 21,6 min** (2 procesos; unos 26 min por minuto de anuncio; sin competencia se esperan ~7 a 10), control de calidad 88 s; en total 24 min sin gastar un token. RAM pico del script 1,05 GB (los modelos de CLIP se sueltan antes del render para dejarle la memoria a Remotion).
- **El anuncio siguiente evita al anterior:** con AD1 anotado en `historial.json`, un AD2 con la misma voz salió con 0 tomas solapadas con las de AD1 (usó 2 videos más y otro color principal); 3 de sus 25 tomas quedaron pegadas a un fotograma con texto porque el material limpio nuevo no alcanzaba (lo dice el informe). Con `guion.json` (9 frases con `visual.en`, `textoGrande` y `cta`) las frases se alinean solas con la voz real (aunque Whisper oiga «30» donde el guion dice «treinta» o «ibup» por «ebook») y mandan sus textos grandes y el botón.
- **Clasificación contra la revisión manual** (12 videos que Claude reviso a mano): ver la sección siguiente.

## Contra la revisión manual de Dinámicas

Los 12 videos de TikTok que Claude clasificó a mano (mirando un fotograma cada 4 s) contra lo que dice el automático (un fotograma por segundo, `python scripts/emparejar_tomas.py dinamicas --comparar`):

- **Por video entero: coinciden 11 de 12.** Los 4 «limpios» son los mismos (de 16 a 63 s de material limpio cada uno), los 5 «malos» y 2 de los 3 «medios». El único desacuerdo: `tt_9999000000000016894` (manual «medio», automático «malo»: todo el video trae el texto fijo del creador abajo, 0 s limpios). Los umbrales del 50 % y el 10 % que separan limpio, medio y malo se escogieron con estos mismos videos: con otros pueden pedir ajuste (`--recalcular --comparar`).
- **Por fotograma (616 fotogramas):** de los 239 que Claude marcó `mala`, **ninguno** queda como `buena` o `media` en el automático a nivel de tramo (el detector de texto marca el 96 % de ellos). El automático es **más estricto** que lo manual en 303 de los 616 fotogramas (186 que Claude dejó `buena` pasan a `media` o `mala`, y 117 `media` pasan a `mala`). Causas, comprobadas mirando los fotogramas: (1) subtítulos en portugués en unos 20 fotogramas de `tt_9999000000000020485` que la revisión manual no vio (solo miraba un fotograma cada 4 s); (2) texto que sale en la ESCENA (papeles con versículos, globos con palabras, un cartel de «Feliz día»), que el detector no distingue de un subtítulo y por eso se descarta; (3) líneas de un edificio (ventanas) que el detector toma por texto en algunos fotogramas de `tt_9999000000000037221`. En sentido contrario, 13 fotogramas que Claude dejó en `media` el automático los ve `buena` (un plano general de un salón de noche en `tt_9999000000000046903`).
- **Lo que se le escapa:** 10 de los 239 fotogramas `mala` no tienen texto marcado por el detector (7 de `tt_9999000000000057415`, con subtítulos y la insignia de otro canal, y 3 de `tt_9999000000000069605`); por eso una toma no puede ir pegada a un fotograma con texto, y por eso lo marcado `mala` a mano nunca se usa.
- **Material limpio que queda** (sin texto ni cuadros negros ni lisos, con vecinos limpios): 159 s de 703 s en 4 videos; para un anuncio de 50 s sobra 3 veces.

## Marcar lo que el automático no ve (niños, logos, caras conocidas)

Después de montar, mira `evidencia.png` una vez (cada miniatura trae su número `#N`, la frase `F`, el segundo y el parecido `z`). Si en la toma #7 se ve un niño:

```
python scripts/emparejar_tomas.py <producto> --marcar-toma AD1 7 --motivo "se ve un niño"
python scripts/montaje_auto.py <producto> AD1          # vuelve a montar; el video nuevo sale como _v2 y el ya entregado no se toca
```

- `--marcar-toma <AD> <N> [N…]` toma el video y los segundos EXACTOS que usó esa toma (del `videoData.json` del anuncio). `--marcar <video> <desde-hasta>` marca un tramo a mano (basta un pedazo del nombre; `plan.md` muestra el final del nombre y el segundo de inicio); `todo` en vez del tramo marca el video entero (por ejemplo, uno lleno de niños).
- La marca se guarda en `indice.json` → `marcas: [{desde, hasta, calidad, motivo, fecha}]`: vale para **todos** los anuncios de ese producto (también los que arme el servidor), y **sobrevive** a `--rehacer`, `--recalcular` y a volver a indexar. Una marca `mala` cubre todo fotograma que roza (con el medio segundo de cada lado), y esa toma **nunca** se usa, ni siquiera para no dejar un hueco: si no alcanza el b-roll, el montaje se detiene con «sin-material» y dice qué trajo falta.
- `--calidad media` solo hace preferir otra toma (si falta material se usa). `--marcas` lista lo marcado con su motivo; `--quitar-marca <video> [tramo]` lo borra.
- Una marca que no se entiende (falta `desde`, `hasta` menor que `desde`, calidad rara) **detiene** el montaje con un mensaje: una marca de seguridad no se ignora en silencio.
- Los tramos `mala` de una revisión hecha a mano en `tramos` (con `revisado_por` distinto de `auto-clip`) siguen valiendo como siempre; `marcas` es lo recomendado porque no se pierde al repetir el análisis.

## Lo que NO hace (dicho con claridad)

- **No detecta niños.** CLIP B/32 confunde globos y fiestas con niños (los fotogramas «más niños» eran manos con globos; el video con niños de verdad sacaba lo mismo que los demás). Por eso lo juzga una persona con `evidencia.png`, y lo que alguien marque `mala` (`--marcar-toma` o `--marcar`, abajo) **nunca** se usa (se respeta aunque el automático lo vea limpio).
- **No reconoce logos sin letras** (los que traen texto los pesca el detector de texto).
- **No usa el video de referencia:** con `gancho.modo` = `conservar` (dejar el gancho del original) o `sin-gancho`, el montaje automático igual arma todo con la voz propia y el b-roll (el primer corte es un corte más); solo `nuevo-modelado` cambia algo, y solo si existe `gancho/gancho.json` hecho por `gancho_ia.py`.
- **Derechos:** el b-roll de TikTok es de terceros; ver `CLAUDE.md`. Lo decide el dueño.
- El detector de texto no distingue un subtítulo de un cartel de la escena (los dos se descartan) y a veces toma las líneas de un edificio por texto; falla en ~4 % de los fotogramas con texto (por eso una toma no puede ir pegada a un fotograma con texto salvo que falte material).
- Corre sin red y sin ningún modelo de lenguaje; la autoprueba (`autoprueba_montaje.py`) lo vigila.

## Si algo sale raro

| Problema | Qué hacer |
|---|---|
| «Faltan los modelos de CLIP» | `python scripts/emparejar_tomas.py --solo-descargar` |
| «No alcanza el b-roll limpio» | Trae más videos (`broll_tiktok.py`, o espera la corrida semanal del servidor y `recoger`) y repite; el mensaje dice qué cortes se quedaron sin toma. |
| Un umbral no convence | Se cambian en `UMBRALES` de `emparejar_tomas.py` y `python scripts/emparejar_tomas.py <producto> --recalcular --comparar` vuelve a calcular los tramos con lo guardado en segundos. |
| El control dice «sin verificar» | Falta `rapidocr-onnxruntime`: `python -m pip install -r requirements.txt`. |
| El render se queda sin memoria | `--concurrencia 1` y cierra otros programas. |
| Quiero que Claude pula el anuncio | Sigue el flujo normal de `editor-video` (plan, fotogramas, `_v2`); el `videoData.json` y `plan.md` ya están. |
