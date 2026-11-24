# Buscar en TikTok con el Chrome del dueño (solo si la lista pública no tiene lo que hace falta)

TikTok solo contesta búsquedas a quien tiene la sesión iniciada (sin sesión devuelve una respuesta vacía y la página dice «Hubo un problema»; probado el 2026-10-19 con un navegador real, con y sin ventana). El Chrome del dueño sí la tiene. Con la extensión «Claude in Chrome» se puede **leer lo que su pantalla muestra** y dejar los enlaces en un archivo para que `scripts/broll_tiktok.py` los baje. Hoy se usa solo cuando la búsqueda gratis (`--consulta`, la lista pública de TikTok) no tiene lo que hace falta.

## Reglas

- Solo con el OK de la persona: se usa su cuenta de TikTok.
- Solo se LEE lo que la página ya muestra (enlaces, textos, capturas). **No se repiten ni se imitan las llamadas internas de TikTok** (`/api/search/...` con sus firmas): el modo automático las bloquea y además pueden marcar la cuenta del dueño.
- Máximo 5 consultas por vez y una vez por oferta al día. Para en cuanto aparezca un captcha, una verificación, un aviso de iniciar sesión o «Hubo un problema».
- Trabaja en una pestaña nueva y ciérrala al terminar. No hagas clic en videos, perfiles ni botones: no hace falta.

## Pasos

1. `tabs_context_mcp` (con `createIfEmpty`) y navega a `https://www.tiktok.com/search/video?q=<consulta con %20 por los espacios>`. Espera unos 10 segundos: primero salen cuadros grises y luego los videos.
2. Mira una captura (escala 0,6). Decide con los ojos: ¿lo que se ve sirve de toma (manos, proceso, producto, ambiente sin caras) o es otra cosa (anuncios, bailes, gente hablando a cámara)?
3. Lee los resultados con este código en la pestaña (solo lee el DOM; se quita lo que sigue al `?` de cada enlace porque la herramienta bloquea los textos con parámetros de dirección):

```js
const vistos = new Set(); const filas = [];
for (const a of document.querySelectorAll('a[href*="/video/"]')) {
  if (a.closest('[data-e2e^="inbox"], [data-e2e="following-accounts"], nav')) continue;   // avisos y cuentas que sigue: no son resultados
  const url = a.href.split('?')[0];
  if (vistos.has(url)) continue; vistos.add(url);
  let c = a; for (let i = 0; i < 6 && c && c.parentElement; i++) { c = c.parentElement; if ((c.innerText || '').length > 15) break; }
  filas.push({ url, txt: (c.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160) });
}
({ n: filas.length, filas })
```

   `txt` trae los «me gusta» (por ejemplo `87.7K`), la descripción y el autor. La línea del `closest(...)` deja fuera el panel de avisos de la cuenta del dueño (si no, el código también lee quién le dio «me gusta» a sus videos, y eso no se toca). Un resultado con «Con más me gusta» delante solo marca que es de los más queridos del tema.
4. Para más resultados baja la página con la rueda del ratón (`scroll` en la pestaña), espera 3 segundos y vuelve a correr el código. Con 30 a 40 enlaces por consulta basta.
5. Elige de 10 a 20 en total por oferta (captura + texto + «me gusta»), repartidos entre las consultas. Prefiere 15 a 60 segundos, verticales y sin texto grande encima.
6. Guarda los enlaces, uno por línea, con un comentario `#` (consulta, autor, «me gusta»), en `public/productos/<producto>/broll-tiktok/_busquedas/<AAAA-MM-DD>.txt` (la carpeta con guion bajo es de trabajo: `indexar_broll.py` no la mira).
7. Corre `python scripts/broll_tiktok.py traer <producto> --enlaces <ese archivo> [--drive "<carpeta de la oferta>"]` y sigue con `indexar_broll.py`.

## Qué consultas sirven

Describen la TOMA, no el producto, y se escriben como las escribiría una persona en TikTok: «dinámica reunión de mujeres», «manos tejiendo crochet paso a paso», «aplicar kinesiotape rodilla». Prueba con 4 a 6 y anota en `broll.json` las que dieron buen material.
