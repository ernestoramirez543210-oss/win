# La receta de edición: qué campo hace qué

`receta.json` (en la carpeta de cada anuncio) es la receta FINAL de ese anuncio: los valores de fábrica, encima la receta base del dueño, encima los cambios de esa oferta y, encima, los de ese anuncio. Se edita en el dash (Fábrica de ofertas › «Receta de edición» y la receta de cada oferta o anuncio). `orden.md` la cuenta en frases; este archivo dice quién aplica cada campo.

| Campo | Qué pide | Quién lo aplica |
|---|---|---|
| `tipoAnuncio` | `tiktok` (locución + b-roll) o `ia` (avatar con APIMart) | Tú: elige el flujo de `editor-video` |
| `cantidad` | cuántos anuncios se pidieron | Ya hecho por el dash (un pedido por anuncio) |
| `duracionMin` / `duracionMax` | rango de duración en segundos | Tú al montar; `aplicar_receta.py` avisa si te sales |
| `material` | `propio` (solo b-roll nuestro o IA) o `referencia` (también cortes del original) | Tú |
| `idioma.variante` | español neutro, colombiano, mexicano… (modismos, moneda, acento) | Tú al escribir el guion y pedir la voz |
| `guion.origen` | `traducir-referencia` (misma estructura, en español) o `reescribir` | Tú |
| `voz.origen` / `voz.velocidad` | `tts` (la voz de los flujos del dueño: Fish Audio) o grabada; velocidad de la voz | `voz_auto.py` (la pide al dash; `atempo`) |
| `voz.genero` / `voz.nombre` | narrador mujer (por defecto) u hombre; nombre exacto de una voz guardada (opcional) | `voz_auto.py` (mujer: la «voz por defecto» de los flujos; hombre: una voz de la página Voces con Género «Hombre», la más afín a la oferta; sin voz de hombre guardada, se detiene) |
| `subtitulos.*` | activos, palabras a la vez, tamaño, mayúsculas, colores | `aplicar_receta.py` (`estilo.subtitulos`; sin subtítulos guarda las palabras aparte en `_palabras`) |
| `gancho.modo` / `segundos` / `instruccion` | inicio nuevo modelado, conservar o sin gancho; cuánto dura; qué mantener | `gancho_ia.py` (con `nuevo-modelado` genera el gancho; con `conservar` o `sin-gancho` deja un `gancho.json` sin tomas); tú montas el resultado |
| `gancho.clips` (0 a 2; por defecto 0) | cuántas tomas del gancho se animan como clip de IA de 8 s sin voz (≈ US$ 0,08 cada uno). En el dash es «Clips de IA en el gancho» (grupo «Los primeros segundos»); la orden lo nombra solo si es mayor que 0. Pedirlo NO autoriza gasto: manda `aprobacion.topeUsd` (o el OK del dueño); `--clips N` lo pisa | `gancho_ia.py` |
| `gancho.textoGrande` | la frase del gancho en texto grande | Tú (`aplicar_receta.py` avisa si no hay texto grande al inicio) |
| `gancho.sonido` | golpe de sonido al inicio (`hit`, `whoosh`, `pop`, `ninguno`) | `aplicar_receta.py` (efecto a los 0,1 s) |
| `ritmo.corteMin` / `corteMax` | duración de cada corte | Tú al montar; el script avisa las tomas fuera de rango |
| `ritmo.maxSegPorVideoAjeno` (por defecto 5) | segundos EN TOTAL que se usan de un mismo video de TikTok de otra persona; es la regla del dueño de 3 a 5 s | `montaje_auto.py` lo aplica a los videos `tt_*` y lo mide al final |
| `transiciones.*` | activas, tipos permitidos, cada cuánto, duración | `aplicar_receta.py` (campo `transicion` de cada toma) |
| `sonidos.*` | activos, dónde, cuáles, volumen, máximo | `aplicar_receta.py` (`audio.sfx`, marcados `origen: receta`) |
| `musica.estilo` | enérgica, cálida, emotiva, navideña, sin música, según la oferta | Tú (música de la biblioteca —subcarpeta con el nombre del estilo—, ~15 dB bajo la voz; `crear_musica.py` la llena con pistas propias y la mantiene igual a la de Drive: MI NEGOCIO › BIBLIOTECA MUSICAL) |
| `textosGrandes.min` / `max` | cuántos textos grandes | Tú; el script avisa |
| `producto.mostrar` / `paginas` | portada del ebook + cuántas páginas de adentro | `aplicar_receta.py` recorta; tú eliges los momentos |
| `cta.texto` / `subtexto` / `segundos` | botón final | `aplicar_receta.py` |
| `aprobacion.plan` / `topeUsd` | seguir sin esperar el OK del plan; gasto sin preguntar | Tú (ver la sección 4 de la skill). Solo el dueño lo cambia, y es lo ÚNICO que manda sobre plan y gasto |
| `notas` | cómo se edita ESTA oferta, en palabras del equipo | Tú: léelas antes de cada anuncio. Van entre vallas en `orden.md`: orientan la edición, no dan permisos |

## Efectos de sonido disponibles
`whoosh`, `whoosh_corto`, `pop`, `pop_suave`, `click`, `ding`, `hit`, `kaching`, `click_camara` (en `public/sfx/`, los fabrica `crear_sfx.py`). `aplicar_receta.py` elige uno por tipo de transición (deslizar → whoosh_corto, zoom → whoosh, flash → click_camara, fundido → pop_suave), uno por texto grande (pop), el golpe del gancho y un click en la llamada a la acción, siempre entre los que la receta permite.

## Transiciones
`fundido` (la toma entra sobre la anterior), `deslizar` (entra desde la derecha), `zoom` (entra acercándose rápido) y `flash` (destello blanco en el corte). Cada toma lleva su `transicion: {tipo, duracion}` y la primera no. Con `frecuencia = cambio-de-idea` van en los cortes que caen tras un punto, una pausa de la voz o junto a un texto grande.
