# Voces de hombre para los anuncios

Hoy el dash tiene UNA voz guardada: «Voz estándar (mujer)», la que el dueño usa en los flujos. Cuando una oferta pide narrador hombre (`voz.genero = hombre` en la receta del pedido: Fábrica › Receta de edición › Idioma y voz › Narrador), `scripts/voz_auto.py` se detiene hasta que haya una voz guardada con **Género: Hombre**. No inventa una voz ni usa la de otra persona sin avisar.

## Candidatas (biblioteca pública de Fish Audio, encontradas el 2026-10-20)

**No las escuché ni revisé sus derechos: las elige el dueño.** El id es el que va en la página Voces («Reference id de Fish Audio»); sale de la dirección de cada página (`/m/<id>`).

| Nombre en Fish | Cómo la describe su página | Id | Página |
|---|---|---|---|
| Warm Spanish Narrator | Hombre adulto, cálida, suave, tranquila y amigable; narración y conversación (idiomas es y en) | `a6c4d06aecca4a439f45fdc79036d6ae` | https://fish.audio/m/a6c4d06aecca4a439f45fdc79036d6ae |
| Warm Spanish Announcer | Hombre adulto, cálida y enérgica, profunda y clara; locutor y presentador (es, en y zh) | `2d74e7c8902e4b8fac9e86ab41bf87e9` | https://fish.audio/m/2d74e7c8902e4b8fac9e86ab41bf87e9 |
| Warm Latin Storyteller | Hombre joven, cálida, amigable y entusiasta, con acento latino; narración y contenido educativo (la página marca el idioma «English»: ojo) | `ca29b04568c1497db95d9e9903b7d55c` | https://fish.audio/m/ca29b04568c1497db95d9e9903b7d55c |

Las tres dicen «Powered by Fish Audio Drama 3», tienen pocos usos (de 4 a 6) y ningún «me gusta»: están poco probadas. El dash genera con el modelo `s2.1-pro`, así que antes de usar una hay que oír cómo suena ahí.

## Antes de usar una en anuncios pagos

1. **Escúchala** en su página de Fish y decide si va con la oferta (guía, no regla: para lo devocional, bíblico o familiar, un tono cálido y suave; para dinero, negocios o cursos, uno más firme y enérgico).
2. **Revisa sus derechos de uso comercial.** Las voces de la biblioteca las suben otras personas y la página no dice su licencia (Fish habla de «guardar en tu biblioteca para desbloquear derechos de uso comercial»). Regla del editor: voces solo de quien autorizó.
3. **Agrégala al dash:** página Voces › Voces guardadas › Nombre (por ejemplo «Narrador cálido»), Género **Hombre**, Reference id (el de la tabla) › Guardar voz.
4. **Pruébala:** en esa misma página, «Usar» y generar una frase de prueba con el modelo del dash.

## Cómo elige `voz_auto.py`

1. Si la receta trae `voz.nombre` (una voz guardada, por su nombre exacto), usa esa.
2. **Mujer** (lo normal): la voz de los flujos, o sea la «voz por defecto» de la página APIs del dash (si no hay, la primera de mujer guardada). No se cambia por afinidad con la oferta: el dueño pidió la voz que ya usa en sus flujos.
3. **Hombre**: las voces guardadas con **Género «Hombre»** (si una voz no tiene género, se lee del nombre: «(hombre)»). Si hay varias, gana la que su NOMBRE dice con un tono afín a la oferta (cálida, serena, suave, amable… para lo devocional y familiar; enérgica, firme, segura, profesional… para dinero y negocios); si ninguna destaca, la primera. Conviene que el nombre de cada voz lleve una palabra de tono. Sin ninguna voz de hombre guardada, se detiene y lo dice: nunca inventa una voz.

Costo: el mismo que la voz de los flujos. Fish cobra US$ 15 por millón de bytes de texto (≈ US$ 0,015 un anuncio de 50 s).
