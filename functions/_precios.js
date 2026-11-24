// Precios por modelo, para estimar el costo de un ebook ANTES de crearlo y para anotar lo
// que de verdad se gasto despues (functions/_gasto.js).
//
// Un ebook de 30 paginas cuesta unos centavos con un modelo barato de texto y SDXL, o varios
// dolares con Opus + FLUX.2 max. Sin ver el numero antes, esa decision se toma sin saberla.
//
// Las imagenes de Together se cobran de DOS maneras segun el modelo: por imagen (FLUX.2 dev,
// GPT Image, Qwen 2.0...) o por megapixel (SDXL, Juggernaut, FLUX.2 max...). Con una sola cifra
// "por imagen" para todos -como estaba- el estimador se equivocaba justo en los baratos:
// FLUX.2 dev figuraba a 0,025 cuando Together cobra 0,0154, y GPT Image 2 a 0,040 cuando
// cobra 0,053. Aqui cada modelo lleva su unidad y se convierte al tamano real de la hoja del
// ebook (768 x 1024).
//
// Fuente: la pagina de precios de Together (together.ai/pricing), revisada el 2026-10-06. Los
// proveedores cambian precios sin avisar: son para decidir entre modelos y para estimar el
// saldo, no para contabilidad. Si un modelo no figura aqui, la estimacion lo dice en vez de
// inventar un numero.

// USD por millon de tokens: [entrada, salida].
//
// Una fila puede llevar un tercer dato: un cambio de tarifa que el proveedor YA anuncio, { desde: 'AAAA-MM-DD' (UTC), precio: [entrada, salida] }.
// precioTexto() aplica el precio nuevo solo a partir de ese dia, para que el libro de gasto no se quede corto un 1 de enero por no haber editado
// este archivo. Un precio `null` marca un modelo conocido que NO cobra como texto por tokens (voz, imagen, Live...): sale "sin precio", igual
// que uno que no figura.
const GEMINI_DOBLE_2027 = { desde: '2027-01-01', precio: [1.50, 7.50] };   // 3.8 Flash y 3.6 Flash

const TEXTO = [
  [/claude.*opus-5-5|claude.*opus.5\.5/i, [4.00, 20.00]],
  [/claude.*opus/i,         [5.00, 25.00]],
  [/claude.*haiku/i,        [1.00,  5.00]],
  [/claude.*sonnet-5/i,     [2.00, 10.00]],
  [/claude.*sonnet/i,       [3.00, 15.00]],
  [/claude.*(fable|mythos)/i, [10.00, 50.00]],
  // Gemini. Fuente: la pagina oficial de precios de la API (ai.google.dev/gemini-api/docs/pricing), tarifa Estandar de pago, leida el
  // 2026-10-20 (decia "Ultima actualizacion: 2026-10-19 UTC"). La salida INCLUYE los tokens de razonamiento (llamarLLM los suma).
  // Cada fila cubre UNA version; ninguna cubre "toda la familia". Las filas /gemini.*flash/ y /gemini.*flash-lite/ de antes dejaban caer cualquier
  // modelo nuevo en el precio de la generacion 2.5: 3.5 Flash-Lite figuraba a 0,10 / 0,40 cuando Google cobraba 0,30 / 2,50 y el libro de gasto
  // anotaba ~5 veces menos de lo facturado, y "Usar el mas barato" podia elegir un modelo nuevo por un precio que no era el suyo. Ahora el que no
  // figura aqui sale "sin precio" (el estimador lo dice y "Usar el mas barato" lo salta) hasta que alguien lo agregue. Para agregar una version: su fila con el
  // precio de la pagina y su prueba en scripts/probar-precios-texto.mjs. APIMart vende los mismos modelos ~20 % mas baratos: aqui va el precio de
  // Google, que es el techo (una estimacion que se pasa es mejor que una que se queda corta).
  // El audio de ENTRADA (el de un video entero) cobra mas que el texto en algunos modelos (2.5 Flash 1,00; 3.1 Flash-Lite 0,50): el dash no separa
  // modalidades y lo anota todo a precio de texto, asi que un brief con video queda un poco corto.
  // Voz, imagen, Live, embeddings, transcripcion, robotica y video cobran por audio, imagen o minuto, no como texto: sin precio de texto.
  [/gemini.*(tts|image|live|native-audio|embedding|transcribe|translate|robotics|omni|banana|computer-use)/i, null],
  // 4 Argon sube el 1-dic-2026.
  [/gemini-4-argon/i,       [2.00, 10.00], { desde: '2026-12-01', precio: [4.00, 20.00] }],
  // 3.8 Flash y 3.6 Flash: 0,75 / 3,75 hasta el 31-dic-2026 y el doble desde el 1-ene-2027.
  [/gemini-3\.8-flash(?!-lite)/i, [0.75,  3.75], GEMINI_DOBLE_2027],
  [/gemini-3\.6-flash(?!-lite)/i, [0.75,  3.75], GEMINI_DOBLE_2027],
  [/gemini-3\.5-flash-lite/i,     [0.30,  2.50]],      // la pagina no tiene un 3.5 Flash a secas
  [/gemini-3\.1-pro/i,            [2.00, 12.00]],      // hasta 200 mil tokens de prompt; encima, 4,00 / 18,00 (el dash no manda prompts asi)
  [/gemini-3\.1-flash-lite/i,     [0.25,  1.50]],
  [/gemini-3-flash(?!-lite)/i,    [0.50,  3.00]],      // "3 Flash" a secas (gemini-3-flash-preview), anterior al 3.1
  [/gemini-2\.5-pro/i,            [1.25, 10.00]],      // hasta 200 mil tokens de prompt; encima, 2,50 / 15,00
  [/gemini-2\.5-flash-lite/i,     [0.10,  0.40]],
  [/gemini-2\.5-flash(?!-lite)/i, [0.30,  2.50]],
  // Los alias "-latest" (el que /apis propone por defecto es gemini-flash-latest) los mueve Google a la version nueva sin cambiar su nombre:
  // se cobran como el modelo vigente MAS CARO de su linea. gemini-pro-latest no figura: no se sabe a cual de las lineas Pro apunta.
  [/gemini-flash-latest/i,        [0.75,  3.75], GEMINI_DOBLE_2027],
  [/gemini-flash-lite-latest/i,   [0.30,  2.50]],
  // No figuran a proposito: Gemini 2.0 Flash, 2.0 Flash-Lite y 3 Pro Preview (Google los apago y ya no estan en la pagina de precios) ni ningun
  // id que la pagina no liste (3.5 Flash a secas, 3.7, 3.9...): sin precio es mejor que un precio de otra generacion.
  [/gpt-4o-mini|gpt-4\.1-mini/i, [0.15, 0.60]],
  [/gpt-4o|gpt-4\.1/i,      [2.50, 10.00]],
  [/o[134]-mini/i,          [1.10,  4.40]],
];

// Imagen: { img: USD por imagen } o { mp: USD por megapixel }. El orden importa: las
// variantes especificas van antes que la familia.
const IMAGEN = [
  [/FLUX\.1-schnell/i,           { mp: 0.0027 }],
  [/FLUX\.2-dev/i,               { img: 0.0154 }],
  [/FLUX\.1\.1-pro/i,            { mp: 0.040 }],
  [/FLUX\.2-flex/i,              { img: 0.030 }],
  [/FLUX\.2-pro/i,               { img: 0.030 }],
  [/FLUX\.2-max/i,               { mp: 0.070 }],
  // Kontext sale en el selector rotulado "para editar imagenes", asi que se puede elegir;
  // sin precio, el estimador se quedaba mudo justo en el boton que gasta.
  [/FLUX\.1-kontext-max/i,       { mp: 0.080 }],
  [/FLUX\.1-kontext-pro/i,       { mp: 0.040 }],
  [/imagen-4\.0-fast/i,          { mp: 0.020 }],
  [/imagen-4\.0-ultra/i,         { mp: 0.060 }],
  [/imagen-4\.0/i,               { mp: 0.040 }],
  [/gemini-3-pro-image/i,        { img: 0.134 }],
  // flash-image-3.1-lite no figura en la pagina de precios: se cuenta como el 3.1 normal,
  // que es el techo, para que la estimacion se pase de prudente y no se quede corta.
  [/flash-image-3\.1/i,          { img: 0.050 }],
  [/flash-image-2\.5|flash-image/i, { img: 0.039 }],
  [/gpt-image-1\.5/i,            { img: 0.034 }],
  [/gpt-image/i,                 { img: 0.053 }],
  [/seedream-3/i,                { mp: 0.018 }],
  [/seedream/i,                  { mp: 0.030 }],
  [/ideogram-3/i,                { mp: 0.060 }],
  [/ideogram|p-image-ideogram/i, { img: 0.060 }],
  [/qwen-image-2\.0-pro/i,       { img: 0.080 }],
  [/qwen-image-2\.0/i,           { img: 0.040 }],
  [/qwen-image/i,                { mp: 0.0058 }],
  [/wan2\.6/i,                   { img: 0.030 }],
  [/juggernaut.*lightning/i,     { mp: 0.0017 }],
  [/juggernaut/i,                { mp: 0.0049 }],
  [/stable-diffusion/i,          { mp: 0.0019 }],
];

// APIMart (apimart.ai): precio por imagen de cada modelo, en USD, para el tamano que APIMart usa por
// defecto -la suite no manda `resolution`-: 1K en casi todos; 2K en seedream-4.0/4.5/5.0-lite y en
// wan2.7-image; 2MP en flux-2. Tabla APARTE de la de Together: sus ids se parecen (gpt-image-2,
// seedream...) pero el precio es otro, y un id de APIMart que no figura aqui NO debe caer en el precio
// de Together (ver precioImagen).
//
// Fuente: apimart.ai/pricing y docs.apimart.ai, revisadas el 2026-10-14. La pagina da dos columnas:
// el precio de lista de APIMart y el "Gold" (20 % menos, un nivel de miembro). Aqui va el de LISTA,
// el mas alto: la pagina no dice que nivel tiene una cuenta nueva, y una estimacion que se pasa de
// prudente es mejor que una que se queda corta. Lo que se anota en el libro de gasto NO sale de aqui
// cuando APIMart informa lo cobrado (data.cost, en USD, en la tarea terminada): ver generarImagen()
// en functions/api/ebook-pagina.js. Esta tabla solo estima ANTES de generar y cubre lo que no informan.
//
// Los Gemini que cobra por tokens -el id sin "-ext" o con "-official"- van a ~1.120 tokens de salida
// por imagen de 1K (el numero que da la propia documentacion de APIMart): son aproximados. No figuran
// a proposito los gpt-image-2.5-flare/sunburst y gpt-image-2-official -por tokens y segun la calidad
// que elija el modelo-, gpt-image-1 y grok-imagine-1.5 -no aceptan la proporcion 3:4 de la hoja- ni
// los grok-imagine oficiales -piden `aspect_ratio` en vez de `size`, que es lo que manda la suite-.
// Un modelo que no figura sale "sin precio" en el estimador, y su gasto real se anota igual si APIMart
// informa el costo.
const IMAGEN_APIMART = {};
const FILAS_IMAGEN_APIMART = [
  // Google Nano Banana. "-ext" = precio fijo por imagen (barato); sin sufijo / "-official" = por tokens.
  [0.0156, 'gemini-3.1-flash-lite-image-ext', 'nano-banana-2-lite-ext'],
  [0.0336, 'gemini-3.1-flash-lite-image', 'nano-banana-2-lite'],
  [0.0188, 'gemini-3.1-flash-image-preview', 'nano-banana-2-ext'],
  [0.0336, 'gemini-3.1-flash-image-preview-official', 'nano-banana-2'],
  [0.0156, 'gemini-2.5-flash-image-preview', 'nano-banana-ext'],
  [0.0387, 'gemini-2.5-flash-image-preview-official', 'nano-banana'],
  [0.0250, 'gemini-nano-banana-2.1-ext'],
  [0.0336, 'gemini-nano-banana-2.1'],
  [0.0375, 'gemini-3-pro-image-preview', 'nano-banana-pro-ext'],
  [0.1344, 'gemini-3-pro-image-preview-official', 'nano-banana-pro'],
  // OpenAI
  [0.0106, 'gpt-image-2', 'gpt-image-2-ext', 'gpt-image-2.5-ext'],
  // ByteDance Seedream
  [0.0244, 'seedream-4-0', 'seedream-4.0'],
  [0.0325, 'seedream-4-5', 'seedream-4.5'],
  [0.0350, 'seedream-5-0-lite', 'seedream-5.0-lite'],
  [0.0450, 'seedream-5-0-pro', 'seedream-5.0-pro'],
  [0.0171, 'seedream-5-0-flash'],
  // Alibaba
  [0.0250, 'qwen-image-2.0'],
  [0.0625, 'qwen-image-2.0-pro'],
  [0.0257, 'qwen-image-3.0'],
  [0.0357, 'qwen-image-3.0-pro'],
  [0.0125, 'z-image-turbo'],
  [0.0270, 'wan2.7-image'],
  [0.0680, 'wan2.7-image-pro'],
  // Black Forest Labs: flux-2 por defecto sale a 2 megapixeles.
  [0.0450, 'flux-2-pro'],
  [0.1000, 'flux-2-flex', 'flux-2-max'],
  [0.0400, 'flux-kontext-pro'],
  [0.0800, 'flux-kontext-max'],
  [0.0384, 'flux-3-image'],     // la pagina solo da el precio Gold; no hay de lista
  // Google Imagen 4 (ojo: la documentacion se contradice con las proporciones que acepta)
  [0.0500, 'imagen-4.0-apimart'],
];
FILAS_IMAGEN_APIMART.forEach(([usd, ...ids]) => ids.forEach((id) => { IMAGEN_APIMART[id] = usd; }));
// Un id por fila (el primero, el oficial) con su precio: la lista de respaldo del selector cuando no se
// puede pedir el catalogo real a APIMart (functions/_apimart.js).
export const IMAGEN_APIMART_CONOCIDOS = FILAS_IMAGEN_APIMART.map(([usd, id]) => ({ id, usd }));

// Modelos que aparecen en el catalogo de Together pero NO responden: se probaron el 2026-10-07 con
// un pedido real -una imagen de 768x1024- y los dos Juggernaut dejaron la peticion colgada hasta
// el limite de 90 s, mientras FLUX.2-dev contesto en 6 s, SDXL en 16 y Qwen-Image en 16. Son justo
// los mas baratos del catalogo (0,0013 y 0,0039), asi que sin esta lista "el mas barato" -el
// boton "Usar los mas baratos", el modelo por defecto y el reemplazo cuando uno cae- terminaba
// eligiendo uno que no genera nada. Si Together los arregla, se quita de aqui.
const SIN_RESPUESTA = [/juggernaut/i];
export function modeloSirve(id) { return !SIN_RESPUESTA.some((re) => re.test(id || '')); }

// La hoja del ebook: 768 x 1024 px (ANCHO y ALTO de functions/api/ebook-pagina.js).
export const MEGAPIXELES_HOJA = (768 * 1024) / 1e6;

// `ahora` (milisegundos o Date) solo lo pasan las pruebas, para mirar el precio de un dia futuro. null = sin precio (no figura, o es un modelo
// que no cobra como texto): quien llama lo dice, no inventa uno.
export function precioTexto(id, ahora = Date.now()) {
  for (const [re, p, cambio] of TEXTO) {
    if (!re.test(id || '')) continue;
    if (!p) return null;
    const q = cambio && Number(ahora) >= Date.parse(cambio.desde + 'T00:00:00Z') ? cambio.precio : p;
    return { entrada: q[0], salida: q[1] };
  }
  return null;
}

// USD por imagen al tamano de la hoja del ebook (o al de `mp` megapixeles si se pide otro).
// `proveedor` = el "tipo" del proveedor de imagenes configurado. Con 'apimart' se usa SU tabla y
// nada mas; sin proveedor (o con cualquier otro) sigue como siempre, con la de Together.
export function precioImagen(id, mp, proveedor) {
  if (proveedor === 'apimart') {
    const usd = IMAGEN_APIMART[String(id || '').trim().toLowerCase()];
    return usd != null ? usd : null;
  }
  const tam = mp || MEGAPIXELES_HOJA;
  for (const [re, p] of IMAGEN) {
    if (!re.test(id || '')) continue;
    const usd = p.img != null ? p.img : p.mp * tam;
    return Math.round(usd * 10000) / 10000;
  }
  return null;
}
