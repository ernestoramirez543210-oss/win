# Prompts para los anuncios con IA (imágenes y video)

Úsalo en todos los anuncios de tipo «ia». Los corchetes los llenas tú en cada clip. Los prompts se escriben en inglés; solo el diálogo va en español.

Notas de adaptación a este proyecto (no cambian las reglas de abajo):
- Las plantillas están escritas para una avatar mujer («Same woman», «she», «her»). Si el avatar es hombre, cambia woman/she/her por man/he/his en todo el prompt, en cuadros y en video.
- La estructura está hecha para Grok (`grok-imagine-1.5-video-ext`); si algún día se cambia a Veo u otro modelo, sirve de base y se adapta.
- `scripts/apimart.py imagen` y `video` ya guardan el prompt de cada generación (`cuadros/prompts.md` y `clips/NN_prompt.txt`), pero cada prompt se escribe en un archivo de texto (`--prompt-archivo`) para no pelear con las comillas del diálogo.
- El video exige una URL pública para el cuadro: `apimart.py video --imagen cuadros/02.png` lo sube solo (vale 72 h).

## REGLAS

1. Divide el guion en clips de 8 segundos con 25 a 30 palabras cada uno. Cada clip termina en fin de frase; nunca cortes una frase a la mitad. Si no cabe, usa más clips.
2. Escribe números y símbolos en letras dentro del diálogo («cincuenta por ciento», «veinte mil pesos»).
3. Para cada clip crea primero una imagen (cuadro inicial) y luego conviértela en video. El cuadro define lugar, ropa y producto; el prompt de video describe solo movimiento y diálogo, sin volver a describir la escena.
4. Genera primero el cuadro 01 (ancla), revísalo y úsalo como referencia en todos los demás: misma cara, ropa, peinado y casa en todo el anuncio.
5. El avatar siempre está haciendo algo relacionado con lo que dice (preparar, empacar, mostrar), ya en movimiento cuando empieza el clip; nunca posando. Cambia de lugar o de encuadre entre clips seguidos. Más o menos uno de cada tres clips incluye un primer plano de manos.
6. Aspecto de video casero grabado con celular. No uses las palabras cinematic, 4K, studio lighting ni bokeh: delatan la IA.
7. Si aparece el producto, pasa como referencia las páginas del ebook que producto.json marca como fotos del producto y usa la descripcion_ia; nunca lo generes solo con texto. Si aparece el ebook en un celular, pasa portada.jpg como referencia y pide reproducirla tal cual.
8. Los bloques VOICE y ACCENT van idénticos, palabra por palabra, en todos los clips del anuncio.
9. En el clip 1, el primer segundo es un gancho visual (acercar el producto a la cámara, entrar a cuadro, estar a media acción). Cambia de gancho de un anuncio a otro.
10. Guarda cada prompt usado junto al anuncio (cuadros/prompts.md y clips/NN_prompt.txt).

## REVISIÓN AUTOMÁTICA (la hace el programa ANTES de pagar)

Orden del dueño (2026-11-10): cada anuncio por menos de US$ 0,50 y **nunca se regenera nada**; un clip sale mal porque el prompt no fue específico. Por eso estas reglas no dependen del cuidado de quien escribe: `scripts/revisar_prompts.py` las comprueba en cada `apimart.py imagen` y `apimart.py video` (también con `--dry-run`), antes de subir o pedir nada, y el dash las repite antes de cobrar. Si el prompt no cumple, **no se gasta nada** y sale la lista de lo que falta. `gancho_ia.py` y `avatar_auto.py` revisan el anuncio entero antes del primer pago y le devuelven al modelo lo que falla para que reescriba.

- **Cuadro (imagen):** dice el LUGAR («in her small kitchen next to the stove» o «Place: …»), el ÁNGULO («three-quarter angle», «side view», «low angle», «from above», «eye level»), el ENCUADRE («medium shot», «waist up», «close-up»), la ACCIÓN en curso con objetos concretos (un verbo en -ing con lo que tiene en las manos: «pouring hot chocolate into a red mug»; «standing», «posing», «stepping back» o «holding something» no valen; «stretching her back» sí) y la EXPRESIÓN («smiling at the camera», «focused»). Si no sale ninguna cara, escribe «Nobody's face is visible» y no hace falta la expresión; si no sale nadie, «No people in the shot» y bastan lugar, ángulo y encuadre. Lleva «No text, no readable brand names, no logos, no watermark». Si muestra el producto (Product:, ebook, the guide, the booklet…), va con su foto de referencia (la portada o las páginas de `paginas/`); si nombra «the second reference», esa foto va.
- **Clip con diálogo (plantillas 3 y 4):** de 25 a 30 palabras por cada 8 s (6 s: 19 a 22; 10 s: 32 a 37; en la de dos tomas, 12 a 15 por toma y entre las dos igual de 25 a 30: 12 + 12 no alcanza), termina en punto, «?» o «!», sin cifras ni símbolos (tampoco ½, ⅓, ², ⑩ ni números romanos como Ⅻ: todo número va en letras), con los bloques UGC LOOK, STABLE OBJECT CONSISTENCY, VOICE, ACCENT, PACE y RESTRICTIONS, y dice que es en español. **VOICE y ACCENT idénticos** a los de los otros clips del anuncio: el editor los compara con los `<clip>_prompt.txt` de la misma carpeta y el dash con todo el anuncio (si no, 409). Al repetir un clip con `--repetir`, con el mismo nombre o como `03_v2.mp4`, no se compara con ninguna versión de ese clip, así se corrige su voz; `03`, `03_v2` y `03_b` son el mismo clip y de cada clip vale su versión más nueva (en el editor, la de número más alto; en el dash, la última pedida: coinciden, porque el dash deja repetir un solo clip por anuncio).
- **Clip sin diálogo (plantilla 5):** CAMERA, MICRO-ACTION, STABLE OBJECT CONSISTENCY y RESTRICTIONS, y dice que va en silencio («absolute silence… zero speech»).
- **Todos:** en inglés (solo el diálogo, entre comillas dobles, en español); sin cinematic, 4K, studio lighting, bokeh ni parecidas (HDR, DSLR, film grain, photoshoot, shallow depth of field); sin pedir texto legible (letreros, etiquetas, títulos, «that says…») ni palabras entre comillas fuera del diálogo; sin manos escribiendo o tecleando (la IA las deforma: la mano queda quieta, «holding a pen still above the notebook»; el mueble, «writing desk» o «writing-desk», sí vale, pero no es una acción); y con «No text» (imagen) o «no captions or on-screen text» (clip).
- **Largo:** el prompt de una imagen o un clip tiene hasta 4.000 letras (las plantillas llegan a unas 1.500); uno más largo se niega sin revisarlo.

Para probar un prompt sin gastar: `python scripts/revisar_prompts.py imagen cuadros/02.txt --ref <foto> --producto <p>` o `python scripts/revisar_prompts.py video clips/02.txt --duracion 8 --carpeta clips` (sale con 0 si cumple). Es una revisión por palabras y conteos, no un juicio: un prompt que la pasa todavía puede salir mal; uno que no la pasa, no se paga.

## PLANTILLA 1. Cuadro ancla (gpt-image-2, 1k, 9:16; referencia: foto del avatar)

```
Same woman as the reference photo: keep her face, skin tone, eyes, hair and body exactly the same.
She wears [ROPA DEL ANUNCIO]. Place: [CASA_BASE DEL AVATAR, CON OBJETOS CONCRETOS]. Scene: [ÁNGULO:
three-quarter / side / low angle], [ENCUADRE: medium shot / waist up]. [ACCIÓN EN CURSO CON OBJETOS
CONCRETOS], [EXPRESIÓN HACIA LA CÁMARA]. Candid still from a home video filmed on a phone, real UGC
look, she is in the middle of doing something, not posing, natural light, real skin texture, no
beauty filter, slightly imperfect framing. No text, no readable brand names, no watermark.
```

## PLANTILLA 2. Resto de cuadros (referencias: foto del avatar + cuadro ancla + páginas del ebook con el producto)

```
Same woman as the reference photos: keep her face, skin tone, eyes, hair and body exactly the same,
wearing exactly the same outfit as in the second reference, same hairstyle, same home. Candid still
from a home video filmed on a phone, real UGC look, she is in the middle of doing something, not
posing; natural light, real skin texture, no beauty filter, slightly imperfect framing. No text, no
readable brand names, no watermark. Scene: [LUGAR + ÁNGULO + ACCIÓN + OBJETOS + EXPRESIÓN].
Product: [DESCRIPCION_IA].
```

El orden de las referencias importa: 1.ª foto del avatar, 2.ª cuadro ancla (la plantilla dice «the second reference»), después las páginas del ebook.

## BLOQUES FIJOS (van al final de todos los prompts de video con diálogo)

```
UGC LOOK: real home video filmed on a phone, slight natural handheld sway, natural light, real skin
texture, no beauty filter, natural blinks and breathing, she keeps doing the action with her hands
while she talks and glances between her hands and the camera.
STABLE OBJECT CONSISTENCY: whatever product or object is being held or shown must keep its shape,
color and content 100% consistent throughout the clip, never morphing or deforming. This only
protects the object: her body movement, gestures and speech continue as described.
VOICE: [VOZ DEL AVATAR]. The voice is exactly the same in the whole clip.
ACCENT: [ACENTO DEL AVATAR].
PACE: natural, slightly brisk conversational pace, she starts speaking right away; after the last
word she keeps doing the action silently with her mouth closed.
RESTRICTIONS: lip-synced dialogue only in Spanish, natural room tone, no background music, no
captions or on-screen text, no extra words beyond the quoted lines.
```

## PLANTILLA 3. Video de una toma (grok-imagine-1.5-video-ext, 8 s, a partir del cuadro)

```
(00:00-00:01) [GANCHO O ACCIÓN: ya está haciendo algo]. She looks into the lens and says, warmly
and naturally, with small hand gestures: "[25 A 30 PALABRAS DEL GUION]" (Spanish, lip-synced). She
finishes about half a second before the end and holds a soft smile.
[BLOQUES FIJOS]
```

## PLANTILLA 4. Video de dos tomas en un mismo clip (más dinámico)

```
SHOT 1 (00:00-00:04) [ELLA A CÁMARA + ACCIÓN CON EL PRODUCTO]. She says: "[12 A 15 PALABRAS]"
(Spanish, lip-synced)
SHOT 2 (00:04-00:08) HARD CUT, no transition, no fade. [PRIMER PLANO DE MANOS HACIENDO EL PROCESO,
face not visible / O ENCUADRE DISTINTO DE ELLA]. She continues: "[12 A 15 PALABRAS]" (Spanish)
[BLOQUES FIJOS]
```

## PLANTILLA 5. Toma de apoyo sin habla (para tapar un corte o mostrar el producto)

```
CAMERA: [ENCUADRE], filmed on a phone, slight natural handheld sway, soft window daylight.
MICRO-ACTION: [UNA SOLA ACCIÓN LENTA Y CONCRETA: verter, abrir, mostrar, oler, sonreír], natural
blinks and breathing.
STABLE OBJECT CONSISTENCY: [IGUAL QUE ARRIBA]
RESTRICTIONS: absolute silence for the entire clip: zero speech, zero voice-over. Her lips stay
closed; her mouth never moves as if talking. Only natural room tone. No music, no captions or
on-screen text.
```

## GANCHOS MODELADOS (`scripts/gancho_ia.py`)

Para el gancho «100 % modelado» de la Fábrica no se escribe nada a mano: `gancho_ia.py` arma los prompts con las reglas de arriba. Lo que escribe el modelo barato (en inglés, 30 a 70 palabras) es SOLO la escena de cada imagen: lugar, ángulo, sujeto, acción en curso y objetos concretos. El código pone el resto, siempre igual:

- **Con avatar** (`usa_avatar`): «Same person as the reference photo: keep their face, skin tone, eyes, hair and body exactly the same» (como la PLANTILLA 1; con ancla, la de la PLANTILLA 2). La foto del avatar va primera como referencia, el cuadro ancla (la imagen 01) segunda y las páginas del producto después.
- **Sin avatar** (casi todas las tomas): manos, objetos y el producto, con «Nobody's face is visible». El script rechaza (y le pide corregir al modelo) un prompt sin avatar que hable de una cara, un retrato, una sonrisa o «mirando a cámara».
- **Producto** (`usa_producto`): `descripcion_ia` de `producto.json` y sus páginas de foto como referencia; si solo hay portada, «the ebook cover is shown on a phone screen: reproduce the reference cover exactly». Si no hay fotos del producto, la toma no lo muestra (nunca se genera solo con texto).
- **Estilo**: «Candid still from a home video filmed on a phone, real UGC look…» y «No text, no readable brand names, no logos, no watermark» (regla 6: nada de cinematic, 4K, studio lighting ni bokeh).
- **Clip** (solo si se pidió): la PLANTILLA 5 (toma de apoyo SIN habla, silencio absoluto): la voz del anuncio la pone el TTS después; el clip sale del cuadro inicial 01.
- **Nombres prohibidos**: el anunciante del original y las marcas que el modelo de visión vio en pantalla se le pasan al redactor como «no mencionar»; si aparecen en la frase o en un prompt, se rechaza y se le pide reescribir (hasta tres intentos); si sigue igual, no se genera nada.
