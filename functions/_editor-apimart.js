// APIMART DEL EDITOR, POR EL DASH (2026-11-10).
//
// El editor de anuncios (un programa en Python en varios PC y en el VPS) pedia imagenes, clips y texto a APIMart con SU llave, en cada PC. El tope por anuncio y la regla de no repetir clips
// todo por /api/editor-apimart (functions/api/editor-apimart.js: la llave del editor, la version y el despacho). Aqui vive TODA la logica, exportada para probarla con KV y APIMart falsos
// (scripts/probar-editor-apimart.mjs).
//
// Acciones (la puerta ya comprobo la llave del editor y la version):
//   estadoEditor  GET ?estado=1[&producto=&anuncio=]  -> topes, las dos versiones minimas (general e imagen/clip), lo gastado hoy, los pedidos y las subidas del dia, los modelos y, si se
//                                                        pide, lo del anuncio (si el anuncio no tiene ficha: `legadoSinCalcular`, sin recorrer el libro). Nunca el saldo de la cuenta.
//   subirImagen   POST ?subir=1&producto=&anuncio=     -> reenvia los bytes (PNG/JPEG/WEBP, hasta 10 MB) a APIMart y devuelve la URL https (vale ~72 h). Subir no cuesta, pero suma 1 en el
//                                                        contador del dia ANTES de reenviar (tope MAX_SUBIDAS_DIA).
//   pedirVideo    { accion:'video', ... }               -> /v1/videos/generations con el modelo FIJO (grok-imagine-1.5-video-ext). Estimado duracion x precio por segundo.
//   consultarTarea { accion:'tarea', ticket }           -> el estado de la tarea; al terminar corrige el libro con lo que APIMart cobro de verdad.
//   pedirTexto    { accion:'texto', ... }               -> /v1/chat/completions (solo los modelos baratos de la lista); devuelve lo que APIMart conteste, con la llave oculta. No deja quitar a
//                                                        la vez max_tokens y max_completion_tokens (400): sin ninguno, la respuesta y el costo no tienen techo.
//
// Reglas de dinero (antes de cobrar, en el dash; el orden importa):
//   validar -> tope del DIA, primero SOLO con el CONTADOR del dia (UN get: si ya no entra, 429 sin ningun list() y sin leer ni crear la ficha) y despues con lo mayor entre el libro y el
//   contador -> leer la ficha del anuncio (o crearla con lo que el libro ya tenia de el: `legado`, una pasada por el libro entero) -> tope del ANUNCIO -> no repetir clips -> ESCRIBIR el
//   contador del dia (si KV rebota la escritura, se relee y se vuelven a mirar los topes del dia: si ya no entra, 429 sin escribir) y la reserva en la ficha (si no se puede, 503 y no se pide
//   nada) -> pedir a APIMart -> si dijo que no, se quita la reserva (el contador no se descuenta: es un freno, no contabilidad; por eso los intentos rechazados tambien cuentan como
//   pedidos); si no contesto, queda «dudoso» y cuenta -> con el id de la tarea, anotar en el libro.
//   Tope por anuncio: TOPE_ANUNCIO_USD; `topeAnuncio` lo sube hasta TECHO_ANUNCIO_USD con un motivo. Tope del dia: TOPE_DIA_USD y MAX_PEDIDOS_DIA para TODO lo del editor en APIMart (filas
//   `editor:` del libro y contador del dia), y MAX_SUBIDAS_DIA para las subidas. El texto no cuenta para el tope por anuncio (como antes), si para los del dia.
// El contador del dia (`apimart:ed:dia:<AAAA-MM-DD UTC>`) se lee con get, que ve al momento lo que se escribio DESDE UN MISMO LUGAR DE CLOUDFLARE: asi los topes del dia son EXACTOS para
// pedidos en serie que entran por el mismo lugar, aunque list() tarde hasta un minuto en ver las filas nuevas del libro. Siguen BLANDOS frente a pedidos EN PARALELO y entre dos maquinas a
// la vez (el PC en Colombia y el VPS entran por lugares distintos): KV no es atomico y tarda hasta ~60 s en llevar lo escrito a otro lugar, asi que cada una puede leer un contador viejo
// (y pisar lo que sumo la otra). Los dolares y los pedidos los corrige el libro en menos de un minuto (vale lo mayor entre los dos); las subidas no tienen libro de respaldo. El tope real
// sigue siendo el saldo prepago de APIMart; el cuadre (functions/_apimart-cuadre.js) dice si algo se escapo.
//
// KV que toca este archivo: `ajustes` (lectura), la ficha `apimart:ed:<16 hex>` y el contador `apimart:ed:dia:<AAAA-MM-DD>` (lectura y escritura). La llave sale de llaveApimart
// (_apimart.js) y el libro se toca solo con las funciones de _gasto.js. La red: solo APIMart (BASE de _apimart.js), por llamarApimart. Lo vigila scripts/Auditar-Seguridad.ps1 (3d-quater).
// Nunca devuelve ni anota la llave: todo texto de APIMart pasa por ocultar() ANTES de recortarlo.
// Costo en KV: imagen o clip = 3 escrituras (contador, ficha y libro) y +2 solo si el real difiere o falla; texto = 2 (contador y libro); subida = 1 (contador); cada pedido que gasta =
// 1 lectura del contador y, si el contador lo deja pasar, 1 list (el libro del dia; el primer pedido de un anuncio recorre ademas el libro entero). Con el dia lleno: 1 get y nada mas.

import { registrarGastoUnico, fijarGastoUnico, leerGasto, filasDelDia } from './_gasto.js';
import { llaveApimart, ocultar, BASE } from './_apimart.js';
import { VERSION_MINIMA_EDITOR } from './_fabrica.js';
import { hmac, safeEqual } from './_sesion.js';
import { revisarImagen, revisarVideo, mensajeRevision, MAX_LETRAS_PROMPT } from './_revisar-prompts.js';

// ---------------------------------------------------------------- topes y precios (los acordo el dueño: subir uno es una decision, con esta prueba y la auditoria)
export const TOPE_ANUNCIO_USD = 0.5;
// La version minima del editor para pedir una IMAGEN o un CLIP (el texto y las subidas siguen con VERSION_MINIMA_EDITOR de _fabrica.js). Desde la 2026.11.10-3 el editor revisa
// cada prompt (y el plan entero del gancho y del avatar) ANTES del primer pago; uno anterior armaba prompts que esta revision niega y podia pagar el cuadro 1 de un gancho cuyo
// cuadro 2 se niega. Con 426 se frena antes de gastar nada y se le dice que se actualice. Debe ser <= editor-anuncios/VERSION (Auditar-Seguridad.ps1, 3d-quinquies).
export const VERSION_MINIMA_IMAGEN_VIDEO = '2026.11.10-3';
export const TECHO_ANUNCIO_USD = 1.0;
export const TOPE_DIA_USD = 5;
// Tope de PEDIDOS del editor por dia UTC (imagenes, clips y textos: filas `editor:` del libro y el contador del dia). No es plata: protege el cupo GRATIS de KV de toda la suite (1.000
// trabajo fuerte son ~70.
export const MAX_PEDIDOS_DIA = 150;
// Tope de SUBIDAS de referencias por dia UTC. Subir no cuesta en APIMart, pero cada subida es 1 escritura de KV (el contador) y sin tope una llave del editor filtrada podria gastar el
// cupo de KV de toda la suite y llenar de archivos la cuenta de APIMart. Un anuncio con IA sube de 5 a 10 referencias.
export const MAX_SUBIDAS_DIA = 100;
export const MODELO_IMAGEN = 'gpt-image-2';
export const MODELO_VIDEO = 'grok-imagine-1.5-video-ext';
// Los mismos estimados que usaba el editor (editor-anuncios/scripts/apimart.py): la prueba compara.
export const PRECIO_IMAGEN_USD = 0.0085;
export const PRECIO_VIDEO_SEG_USD = { '480p': 0.0102, '720p': 0.0191 };
// Texto y vision: solo modelos baratos (los de editor-anuncios/scripts/apimart_chat.py).
export const MODELOS_TEXTO = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite-preview', 'gpt-5-nano', 'gpt-4.1-nano'];
// USD por millon de tokens (entrada, salida), precio de LISTA del fabricante: un techo (APIMart cobra ~20 % menos). Copia de PRECIOS de apimart_chat.py; la prueba compara.
const PRECIOS_TEXTO = [
  [/gemini-3\.8.*flash/i, 0.75, 3.75],
  [/gemini-3\.5.*flash-lite/i, 0.30, 2.50],
  [/gemini-3\.1.*flash-lite/i, 0.25, 1.50],
  [/gemini.*flash-lite/i, 0.10, 0.40],
  [/gemini.*flash/i, 0.30, 2.50],
  [/gpt-5.*nano/i, 0.05, 0.40],
  [/gpt-5.*mini/i, 0.25, 2.00],
  [/gpt-4\.1-nano/i, 0.10, 0.40],
  [/gpt-4o-mini|gpt-4\.1-mini/i, 0.15, 0.60],
];
const PRECIO_TEXTO_DESCONOCIDO = [1.0, 5.0];

export const LIMITES = {
  subirBytes: 10 * 1024 * 1024, prompt: MAX_LETRAS_PROMPT, refs: 15, url: 2048, archivo: 80, motivo: 200, etiqueta: 40, durMin: 6, durMax: 15,
  textoImagenes: 6, dataUri: 400000, sistema: 20000, usuario: 60000, maxTokens: 6000, respuestaTexto: 200 * 1024,
  itemsFicha: 100, repetidos: 1, ticketDias: 3,
};
export const PREFIJO_FICHA = 'apimart:ed:';
// El contador EXACTO del dia (UTC) del editor: `apimart:ed:dia:<AAAA-MM-DD>` = { u: dolares estimados, n: pedidos que gastan, s: subidas }. Una sola clave por dia, leida con get y
// escrita ANTES de pedir a APIMart. Vive 3 dias (solo sirve el de hoy).
export const PREFIJO_DIA = 'apimart:ed:dia:';
const DIAS_CONTADOR = 3;
const RE_DIA = /^\d{4}-\d\d-\d\d$/;
const DIAS_FICHA = 400;
const DIAS_LIBRO = 1100;          // como /api/editor-costos: lo que cuesta CREAR un proyecto se quiere ver años
const HORAS_SUBIDA = 70;          // APIMart borra lo subido a las 72 h
const CAMPOS_QUITABLES = ['response_format', 'max_tokens', 'temperature', 'max_completion_tokens'];

export const RE_PRODUCTO = /^[a-z0-9][a-z0-9_-]{0,40}$/;        // hasta 41, como NOMBRE_PRODUCTO del editor (fabrica.py, referencia_meta.py)
export const RE_ANUNCIO = /^[A-Za-z0-9][A-Za-z0-9_-]{0,15}$/;
const RE_ARCHIVO = /^[\p{L}\p{N}][\p{L}\p{N} ._()-]{0,79}$/u;
const RE_TAREA = /^[A-Za-z0-9_-]{1,100}$/;
const RE_URL = /^https:\/\/[^\s"'<>\\]{1,2040}$/;
const RE_ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/;
const RE_DATA_URI = /^data:image\/(jpeg|png|webp);base64,[A-Za-z0-9+/]{16}/;
// Las UNICAS rutas de APIMart a las que llega este modulo.
const RUTA_APIMART = /^\/v1\/(images\/generations|videos\/generations|uploads\/images|chat\/completions|tasks\/[A-Za-z0-9_-]{1,100}\?language=es)$/;

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});
const r6 = (n) => Math.round(n * 1e6) / 1e6;
const dinero = (n) => 'US$ ' + (n < 0.1 ? n.toFixed(4) : n.toFixed(2)).replace('.', ',');

// ---------------------------------------------------------------- utilidades
const aHex = (buf) => Array.from(new Uint8Array(buf)).map((x) => x.toString(16).padStart(2, '0')).join('');
async function huellaHex(texto, n) {
  return aHex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(texto))).slice(0, n);
}
function azarHex(n) {
  const b = new Uint8Array(n / 2);
  crypto.getRandomValues(b);
  return aHex(b);
}
// Texto corto (etiqueta, motivo): sin controles ni la barra vertical (separa los campos del detalle del libro). null = no es texto o pasa del tope.
function textoCorto(v, max) {
  if (v == null || v === '') return '';
  if (typeof v !== 'string') return null;
  const t = v.replace(/[\x00-\x1f\x7f|]+/g, ' ').replace(/\s+/g, ' ').trim();
  return t.length > max ? null : t;
}
// Un prompt: se quedan los saltos de linea y los tabuladores; el resto de los controles se van. '' = falta o pasa del tope.
function limpiarPrompt(v) {
  if (typeof v !== 'string') return '';
  const t = v.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').trim();
  return t && t.length <= LIMITES.prompt ? t : '';
}
const urlHttps = (u) => typeof u === 'string' && u.length <= LIMITES.url && RE_URL.test(u);

// «03_v2.mp4» (version nueva) y «03_b.mp4» (el reintento de avatar_auto) son el mismo clip que «03.mp4» (igual que en el editor).
export function baseArchivo(n) {
  return String(n == null ? '' : n).replace(/\\/g, '/').split('/').pop().toLowerCase().replace(/(_v\d+|_b)(?=\.[a-z0-9]+$)/, '');
}

// PNG, JPEG o WEBP por sus primeros bytes (nada de HTML ni SVG disfrazado). null = otra cosa.
export function tipoDeImagen(bytes) {
  const u = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || 0);
  if (u.length >= 8 && u[0] === 0x89 && u[1] === 0x50 && u[2] === 0x4e && u[3] === 0x47 && u[4] === 0x0d && u[5] === 0x0a && u[6] === 0x1a && u[7] === 0x0a) return { mime: 'image/png', ext: 'png' };
  if (u.length >= 3 && u[0] === 0xff && u[1] === 0xd8 && u[2] === 0xff) return { mime: 'image/jpeg', ext: 'jpg' };
  if (u.length >= 12 && u[0] === 0x52 && u[1] === 0x49 && u[2] === 0x46 && u[3] === 0x46 && u[8] === 0x57 && u[9] === 0x45 && u[10] === 0x42 && u[11] === 0x50) return { mime: 'image/webp', ext: 'webp' };
  return null;
}

// Lee un cuerpo (de la peticion o de APIMart) sin pasar de `tope` bytes: si se pasa, deja de leer y devuelve null. Asi un cuerpo enorme sin content-length no se carga entero en memoria.
export async function leerCuerpo(cuerpo, tope) {
  const flujo = cuerpo && cuerpo.body !== undefined ? cuerpo.body : cuerpo;
  if (!flujo) return new Uint8Array(0);
  const lector = flujo.getReader();
  const trozos = [];
  let n = 0;
  for (;;) {
    const { done, value } = await lector.read();
    if (done) break;
    n += value.byteLength;
    if (n > tope) { try { await lector.cancel(); } catch { /* ya se corto */ } return null; }
    trozos.push(value);
  }
  const out = new Uint8Array(n);
  let i = 0;
  for (const t of trozos) { out.set(t, i); i += t.byteLength; }
  return out;
}

// Todas las URL https de un resultado de APIMart, en orden (las imagenes y los videos no documentan su forma exacta). Ninguna que lleve la llave.
export function urlsDe(obj, llave) {
  const out = [];
  const ver = (x, prof) => {
    if (out.length >= 20 || prof > 6 || x == null) return;
    if (typeof x === 'string') { if (urlHttps(x) && out.indexOf(x) === -1 && !(llave && x.indexOf(llave) !== -1)) out.push(x); return; }
    if (Array.isArray(x)) { for (const y of x) ver(y, prof + 1); return; }
    if (typeof x === 'object') for (const k of Object.keys(x)) ver(x[k], prof + 1);
  };
  ver(obj, 0);
  return out;
}

function costoValido(v) {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 && n <= 20 ? r6(n) : null;
}

// ---------------------------------------------------------------- la llave de APIMart y la red
async function leerLlave(env) {
  let ajustes = {};
  try { const crudo = await env.SUITE.get('ajustes'); ajustes = (crudo && JSON.parse(crudo)) || {}; } catch { ajustes = {}; }
  try { return (await llaveApimart(env, ajustes)) || null; } catch { return null; }
}
const sinLlave = () => json(503, { ok: false, error: 'No hay llave de APIMart guardada en el dash (página APIs): el editor no puede generar con IA hasta que el dueño la ponga.' });

// La UNICA salida a la red de este modulo: siempre a APIMart (BASE, de _apimart.js) y solo a RUTA_APIMART. Lanza si no contesta (red, tiempo).
async function llamarApimart(llave, metodo, ruta, cuerpo, ms) {
  if (!RUTA_APIMART.test(ruta)) throw new Error('ruta de APIMart no permitida');
  const esForm = cuerpo instanceof FormData;
  const cab = cuerpo == null || esForm ? { Authorization: 'Bearer ' + llave } : { Authorization: 'Bearer ' + llave, 'Content-Type': 'application/json' };
  const ctrl = new AbortController();
  const reloj = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(BASE + ruta, { method: metodo, headers: cab, body: cuerpo == null ? undefined : (esForm ? cuerpo : JSON.stringify(cuerpo)), signal: ctrl.signal });
  } finally { clearTimeout(reloj); }
}

// El motivo que da APIMart cuando dice que no, sin la llave: se oculta ANTES de recortar (un recorte a la mitad de la llave dejaria un pedazo a la vista).
async function errorDeApimart(r, llave) {
  let t = '';
  try { t = new TextDecoder().decode((await leerCuerpo(r, 64 * 1024)) || new Uint8Array(0)); } catch { t = ''; }
  let msg = t;
  try {
    const j = JSON.parse(t);
    const e = j && (j.error || j.message);
    if (typeof e === 'string') msg = e;
    else if (e && typeof e.message === 'string') msg = e.message;
  } catch { /* no era JSON: va el texto */ }
  return ocultar(String(msg), llave).replace(/[\x00-\x1f\x7f]+/g, ' ').trim().slice(0, 300);
}

// ---------------------------------------------------------------- la ficha del anuncio
// `apimart:ed:<16 hex de sha256(producto|anuncio)>`: { v, p, a, legado, gastado, archivado, repetidos, items:[{ r, tp, b, h, e, c?, s, t, et, rep?, motivoTope? }] }
//   r = la reserva (16 hex) · tp = imagen|video · b = base del archivo · h = huella del prompt y la imagen · e = estimado · c = lo cobrado (cuando se sabe) · t = hora de la reserva · et = etiqueta
//   s = 'pedido' (cuenta e) | 'dudoso' (APIMart no contesto: cuenta e) | 'listo' (cuenta c) | 'fallo' (no cuenta, salvo que APIMart haya cobrado algo)
//   legado = lo que el libro ya tenia de este anuncio (lo anotado por /api/editor-costos) la primera vez que se toco aqui · archivado = lo de las imagenes viejas sacadas para no pasar de 100
async function claveFicha(p, a) { return PREFIJO_FICHA + (await huellaHex(p + '|' + a, 16)); }
const fichaValida = (f) => !!(f && typeof f === 'object' && Array.isArray(f.items) && typeof f.p === 'string' && typeof f.a === 'string');
// null = no hay ficha. Si KV falla, LANZA: sin saber que hay no se escribe encima.
async function leerFicha(env, clave) {
  const f = await env.SUITE.get(clave, { type: 'json' });
  return fichaValida(f) ? f : null;
}
async function guardarFicha(env, clave, ficha) {
  ficha.gastado = gastadoFicha(ficha);
  try { await env.SUITE.put(clave, JSON.stringify(ficha), { expirationTtl: DIAS_FICHA * 86400 }); return true; } catch { return false; }
}
// KV acepta UNA escritura por segundo por clave: quitar o marcar la reserva justo despues de escribirla puede rebotar. Un segundo de espera y otro intento.
async function guardarFichaConEspera(env, clave, ficha) {
  if (await guardarFicha(env, clave, ficha)) return true;
  await new Promise((listo) => setTimeout(listo, 1100));
  return guardarFicha(env, clave, ficha);
}
const cuenta = (x) => (x.s === 'fallo' ? (Number(x.c) > 0 ? Number(x.c) : 0) : (x.c != null && Number.isFinite(Number(x.c)) ? Number(x.c) : Number(x.e) || 0));
export function gastadoFicha(f) { return r6((f.items || []).reduce((s, x) => s + cuenta(x), 0) + (Number(f.archivado) || 0)); }

// Lo que el libro ya tenia de ESTE anuncio (imagenes y clips anotados por /api/editor-costos o por esta puerta). El texto no cuenta para el tope del anuncio.
export function legadoDe(filas, p, a) {
  // /api/editor-costos corta el producto a 24 letras: un producto mas largo quedo anotado con su nombre cortado.
  const pres = ['editor:' + p + '|' + a + '|'];
  if (p.length > 24) pres.push('editor:' + p.slice(0, 24) + '|' + a + '|');
  let u = 0;
  for (const f of filas || []) {
    if (f.p !== 'apimart') continue;
    const pre = pres.find((x) => f.d.indexOf(x) === 0);
    if (pre && f.d.slice(pre.length).split('|')[0] !== 'texto') u += f.u;
  }
  return r6(u);
}
// La primera vez que se toca un anuncio: una pasada por el libro para saber lo que ya gasto. Si KV falla, LANZA.
async function fichaNueva(env, p, a) {
  const { filas } = await leerGasto(env);
  return { v: 1, p, a, legado: legadoDe(filas, p, a), gastado: 0, archivado: 0, repetidos: 0, items: [] };
}
function hacerLugar(f) {
  while (f.items.length >= LIMITES.itemsFicha) {
    const i = f.items.findIndex((x) => x.tp === 'imagen');
    if (i < 0) return false;
    f.archivado = r6((Number(f.archivado) || 0) + cuenta(f.items[i]));
    f.items.splice(i, 1);
  }
  return true;
}

// Todo lo del editor en APIMart que el libro tiene del dia (UTC): lo que paso por esta puerta y por /api/editor-costos, imagenes, clips y texto.
export function sumaEditorDelDia(filas) {
  let u = 0;
  for (const f of filas || []) if (f.p === 'apimart' && f.d.indexOf('editor:') === 0) u += f.u;
  return r6(u);
}
export function contarEditorDelDia(filas) {
  let n = 0;
  for (const f of filas || []) if (f.p === 'apimart' && f.d.indexOf('editor:') === 0) n++;
  return n;
}
// El dia UTC de ahora («AAAA-MM-DD»): se toma UNA vez por pedido y vale para el libro y para el contador.
const diaUTC = () => new Date().toISOString().slice(0, 10);
// El contador del dia: { u, n, s }, en 0 si todavia no hay (o si lo guardado no tiene la forma). Si KV falla, LANZA: los topes fallan cerrados.
async function leerContador(env, dia) {
  if (!RE_DIA.test(dia)) throw new Error('dia mal escrito');
  const c = await env.SUITE.get(PREFIJO_DIA + dia, { type: 'json' });
  const num = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : 0);
  return c && typeof c === 'object' ? { u: r6(num(c.u)), n: Math.floor(num(c.n)), s: Math.floor(num(c.s)) } : { u: 0, n: 0, s: 0 };
}
const muchosPedidos = (n) => json(429, { ok: false, pedidosHoy: n, maxPedidosDia: MAX_PEDIDOS_DIA, error: 'Hoy (día UTC, cambia a las 7 p. m. de Colombia) el editor ya hizo ' + n + ' pedidos a APIMart (imágenes, clips y textos; también cuentan los intentos que APIMart rechazó o que no se completaron): el tope es ' + MAX_PEDIDOS_DIA + ' por día para no agotar el almacén del dash. Sigue mañana o avísale al dueño.' });
const muchoGasto = (hoy, costo) => json(429, { ok: false, gastadoHoyUsd: hoy, topeDiaUsd: TOPE_DIA_USD, error: 'Hoy (día UTC, cambia a las 7 p. m. de Colombia) el editor ya gastó ' + dinero(hoy) + ' en APIMart y este pedido sumaría hasta ' + dinero(costo) + ': el tope del día es ' + dinero(TOPE_DIA_USD) + '. Sigue mañana o avísale al dueño.' });
const muchasSubidas = (n) => json(429, { ok: false, subidasHoy: n, maxSubidasDia: MAX_SUBIDAS_DIA, error: 'Hoy (día UTC, cambia a las 7 p. m. de Colombia) el editor ya subió ' + n + ' imágenes de referencia: el tope es ' + MAX_SUBIDAS_DIA + ' subidas por día. Usa las que ya subiste (cada una vale unas 70 horas) o sigue mañana; si de verdad hace falta, avísale al dueño.' });
const sinContador = () => json(503, { ok: false, reintentar: true, error: 'No pude anotar el pedido en el contador del día (el almacén rechazó la escritura): no pido nada a APIMart. Reintenta en un momento.' });
const sinSumarHoy = () => json(503, { ok: false, reintentar: true, error: 'No pude sumar lo gastado hoy (el almacén no contestó): no pido nada. Reintenta en un momento.' });
// El freno del dia SOLO con el contador ya leido: si con el ya no entra este pedido (costo = su estimado o, en el texto, su techo), el 429; null = deja pasar (falta mirar el libro). Va
// ANTES de cualquier list() del libro y de leer o crear la ficha (un dia lleno no gasta listados de KV), y otra vez tras un rebote al escribir el contador (sumarAlContador).
function frenoDelContador(cont, costo) {
  if (cont.n >= MAX_PEDIDOS_DIA) return muchosPedidos(cont.n);
  if (cont.u + costo > TOPE_DIA_USD + 1e-9) return muchoGasto(cont.u, costo);
  return null;
}
// Suma al contador lo de ESTE pedido (du dolares, dn pedidos, ds subidas) y lo ESCRIBE. KV acepta una escritura por segundo por clave (una subida y la imagen que la usa llegan seguidas):
// si rebota, un segundo de espera, se relee (por si otro pedido lo subio entretanto) y, ANTES del otro intento, se vuelven a mirar los topes del dia con lo releido: si este pedido ya no
// entra, no se escribe. Devuelve true = quedo escrito; false = no se pudo escribir (quien llama da 503 y no pide nada); una respuesta 429 = ya no entra (quien llama la devuelve sin pedir).
async function sumarAlContador(env, dia, previo, du, dn, ds) {
  if (!RE_DIA.test(dia)) return false;
  const escribir = async (base) => {
    const c = { u: r6(base.u + du), n: base.n + dn, s: base.s + ds };
    try { await env.SUITE.put(PREFIJO_DIA + dia, JSON.stringify(c), { expirationTtl: DIAS_CONTADOR * 86400 }); return true; } catch { return false; }
  };
  if (await escribir(previo)) return true;
  await new Promise((listo) => setTimeout(listo, 1100));
  let ahora;
  try { ahora = await leerContador(env, dia); } catch { return false; }
  const base = { u: Math.max(ahora.u, previo.u), n: Math.max(ahora.n, previo.n), s: Math.max(ahora.s, previo.s) };
  if (ds > 0 && base.s + ds > MAX_SUBIDAS_DIA) return muchasSubidas(base.s);
  if (dn > 0) { const freno = frenoDelContador(base, du); if (freno) return freno; }
  return escribir(base);
}
// Lo del editor HOY (dia UTC): el libro (UN list: lo de esta puerta y lo de /api/editor-costos, que el contador no ve) y el contador (UN get: lo recien pedido por aqui, que el list puede
// no ver todavia; `yaLeido` = el que quien llama ya leyo para el freno temprano, asi no se lee dos veces). Para los topes vale lo MAYOR de los dos. Si KV falla, LANZA.
async function hoyDelEditor(env, dia, yaLeido) {
  const { filas } = await filasDelDia(env, dia);
  const cont = yaLeido || (await leerContador(env, dia));
  return { hoy: r6(Math.max(sumaEditorDelDia(filas), cont.u)), pedidos: Math.max(contarEditorDelDia(filas), cont.n), cont };
}

// ---------------------------------------------------------------- el ticket de una tarea
// base64url(JSON { v:1, k: id de la tarea, r, p, a, tp, e, t, et, ar, l }) + '.' + HMAC-SHA256(SESSION_SECRET, 'editor-apimart|' + carga). Vence a los 3 dias. Sin el ticket no se consulta
// ninguna tarea: asi la llave del editor no sirve para leer tareas ajenas ni para corregir el libro con datos inventados.
function aB64u(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function deB64u(t) {
  const s = atob(t.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((t.length + 3) % 4));
  const u = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i);
  return new TextDecoder().decode(u);
}
export async function firmarTicket(env, datos) {
  const carga = aB64u(new TextEncoder().encode(JSON.stringify(datos)));
  return carga + '.' + (await hmac('editor-apimart|' + carga, String(env.SESSION_SECRET)));
}
// El contenido del ticket, { vencido:true }, o null si la firma o la forma no sirven.
export async function leerTicket(env, ticket) {
  if (typeof ticket !== 'string' || ticket.length > 2000 || !env.SESSION_SECRET) return null;
  const partes = ticket.split('.');
  if (partes.length !== 2 || !/^[A-Za-z0-9_-]+$/.test(partes[0]) || !/^[A-Za-z0-9_-]+$/.test(partes[1])) return null;
  if (!safeEqual(partes[1], await hmac('editor-apimart|' + partes[0], String(env.SESSION_SECRET)))) return null;
  let d;
  try { d = JSON.parse(deB64u(partes[0])); } catch { return null; }
  if (!d || d.v !== 1 || typeof d.k !== 'string' || !RE_TAREA.test(d.k) || typeof d.r !== 'string' || !/^[0-9a-f]{16}$/.test(d.r)) return null;
  if (typeof d.p !== 'string' || !RE_PRODUCTO.test(d.p) || typeof d.a !== 'string' || !RE_ANUNCIO.test(d.a) || (d.tp !== 'imagen' && d.tp !== 'video')) return null;
  if (costoValido(d.e) == null || typeof d.t !== 'string' || !RE_ISO.test(d.t) || typeof d.et !== 'string' || textoCorto(d.et, LIMITES.etiqueta) !== d.et) return null;
  const t = Date.parse(d.t);
  if (!Number.isFinite(t) || Date.now() - t > LIMITES.ticketDias * 86400000 || t > Date.now() + 3600000) return { vencido: true };
  return d;
}

// ---------------------------------------------------------------- validacion comun
function quienGasta(b) {
  const p = typeof b.producto === 'string' ? b.producto.trim() : '';
  const a = typeof b.anuncio === 'string' ? b.anuncio.trim() : '';
  if (!RE_PRODUCTO.test(p)) return { error: 'Falta «producto» (la oferta, en minúsculas: letras, números, - y _; hasta 41).' };
  if (!RE_ANUNCIO.test(a)) return { error: 'Falta «anuncio» (el ID del video: letras, números, - y _; hasta 16).' };
  const et = textoCorto(b.etiqueta, LIMITES.etiqueta);
  if (et === null) return { error: '«etiqueta» es un texto de hasta ' + LIMITES.etiqueta + ' letras.' };
  return { p, a, et };
}
// El tope de ESTE pedido: el normal, o uno mas alto (hasta el techo) con un motivo que queda en la ficha.
function leerTope(b) {
  if (b.topeAnuncio == null || b.topeAnuncio === '') return { usd: TOPE_ANUNCIO_USD, motivo: '' };
  if (typeof b.topeAnuncio !== 'number' && typeof b.topeAnuncio !== 'string') return { error: '«topeAnuncio» es un número en dólares.' };
  const t = Number(b.topeAnuncio);
  if (!(t > 0 && t <= TECHO_ANUNCIO_USD)) return { error: 'El tope de un anuncio va de más de 0 a ' + dinero(TECHO_ANUNCIO_USD) + ' como máximo (orden del dueño).' };
  if (t <= TOPE_ANUNCIO_USD) return { usd: t, motivo: '' };
  const motivo = textoCorto(b.motivoTope, LIMITES.motivo);
  if (motivo === null) return { error: '«motivoTope» es un texto de hasta ' + LIMITES.motivo + ' letras.' };
  if (!motivo) return { error: 'Para subir el tope del anuncio por encima de ' + dinero(TOPE_ANUNCIO_USD) + ' hace falta «motivoTope» (por qué): queda anotado.' };
  return { usd: t, motivo };
}

async function prepararImagen(b) {
  const texto = limpiarPrompt(b.prompt);
  if (!texto) return { error: 'Falta el prompt de la imagen (o pasa de ' + LIMITES.prompt + ' letras).' };
  const tamano = b.tamano == null || b.tamano === '' ? '9:16' : b.tamano;
  const resolucion = b.resolucion == null || b.resolucion === '' ? '1k' : b.resolucion;
  if (tamano !== '9:16' || resolucion !== '1k') return { error: 'La imagen va en 9:16 y 1k (el precio estimado es para eso).' };
  if (b.imagenes != null && (!Array.isArray(b.imagenes) || b.imagenes.length > LIMITES.refs)) return { error: '«imagenes» es una lista de hasta ' + LIMITES.refs + ' direcciones https (súbelas antes con ?subir=1).' };
  const refs = Array.isArray(b.imagenes) ? b.imagenes : [];
  if (!refs.every(urlHttps)) return { error: 'Cada referencia es una dirección https (súbela antes con ?subir=1): nada de data: ni http.' };
  // La plantilla de los prompts (lugar, angulo, encuadre, accion, expresion, sin texto, producto con referencia...): si no cumple, 400 y no se gasta nada (_revisar-prompts.js).
  const revision = revisarImagen(texto, refs.length);
  if (revision.length) return { error: mensajeRevision(revision, 'El prompt de la imagen'), revision };
  const payload = { model: MODELO_IMAGEN, prompt: texto, n: 1, size: tamano, resolution: resolucion };
  if (refs.length) payload.image_urls = refs;
  return { ruta: '/v1/images/generations', payload, estimado: PRECIO_IMAGEN_USD, h: await huellaHex('imagen|' + texto + '|' + refs.join('|'), 16) };
}

async function prepararVideo(b) {
  const texto = limpiarPrompt(b.prompt);
  if (!texto) return { error: 'Falta el prompt del clip (o pasa de ' + LIMITES.prompt + ' letras).' };
  if (!urlHttps(b.imagen)) return { error: 'Falta «imagen»: el cuadro inicial como dirección https (súbelo antes con ?subir=1).' };
  const dur = b.duracion == null || b.duracion === '' ? 8 : Number(b.duracion);
  if (!Number.isInteger(dur) || dur < LIMITES.durMin || dur > LIMITES.durMax) return { error: '«duracion» va de ' + LIMITES.durMin + ' a ' + LIMITES.durMax + ' segundos (entero).' };
  const resolucion = b.resolucion == null || b.resolucion === '' ? '480p' : b.resolucion;
  if (!Object.prototype.hasOwnProperty.call(PRECIO_VIDEO_SEG_USD, resolucion)) return { error: '«resolucion» es 480p o 720p.' };
  const tamano = b.tamano == null || b.tamano === '' ? '9:16' : b.tamano;
  if (tamano !== '9:16') return { error: 'El clip va en 9:16.' };
  // La plantilla del clip (dialogo de 25 a 30 palabras por 8 s que termina en fin de frase, bloques fijos, silencio en los mudos...): si no cumple, 400 y no se gasta nada.
  // vz = la huella de VOICE y ACCENT: crear() la compara con la de los otros clips del anuncio (tienen que ser identicos en todo el anuncio).
  const rev = revisarVideo(texto, dur);
  if (rev.problemas.length) return { error: mensajeRevision(rev.problemas, 'El prompt del clip'), revision: rev.problemas };
  const payload = { model: MODELO_VIDEO, prompt: texto, size: tamano, duration: dur, resolution: resolucion, image_urls: [b.imagen] };
  return { ruta: '/v1/videos/generations', payload, estimado: r6(dur * PRECIO_VIDEO_SEG_USD[resolucion]), h: await huellaHex('video|' + texto + '|' + b.imagen, 16), vz: rev.voz ? await huellaHex('voz|' + rev.voz, 16) : '' };
}

function idDeTarea(j) {
  const d = j && j.data;
  const x = Array.isArray(d) ? d[0] : d;
  const id = (x && (x.task_id || x.id)) || (j && j.task_id);
  return typeof id === 'string' && RE_TAREA.test(id) ? id : null;
}

// ---------------------------------------------------------------- imagen y clip
async function crear(env, b, tipo) {
  const q = quienGasta(b);
  if (q.error) return json(400, { ok: false, error: q.error });
  const archivo = typeof b.archivo === 'string' ? b.archivo.trim() : '';
  if (!RE_ARCHIVO.test(archivo)) return json(400, { ok: false, error: '«archivo» es solo el nombre del archivo de salida (sin carpetas), hasta ' + LIMITES.archivo + ' letras.' });
  const prep = tipo === 'video' ? await prepararVideo(b) : await prepararImagen(b);
  if (prep.error) return json(400, prep.revision ? { ok: false, error: prep.error, revision: prep.revision } : { ok: false, error: prep.error });
  const tope = leerTope(b);
  if (tope.error) return json(400, { ok: false, error: tope.error });
  const motivoRepetir = textoCorto(b.repetir, LIMITES.motivo);
  if (motivoRepetir === null) return json(400, { ok: false, error: '«repetir» es el motivo, de hasta ' + LIMITES.motivo + ' letras.' });
  const estimado = prep.estimado;
  const llave = await leerLlave(env);
  if (!llave) return sinLlave();

  // Tope del dia, PRIMERO solo con el contador del dia (UN get): si ya no entra, 429 antes de cualquier list() del libro y antes de leer o crear la ficha (la de un anuncio nuevo recorre
  // el libro entero). Si el contador no se puede leer, 503: los topes fallan cerrados.
  const dia = diaUTC();
  let cont;
  try { cont = await leerContador(env, dia); } catch { return sinSumarHoy(); }
  const frenoDia = frenoDelContador(cont, estimado);
  if (frenoDia) return frenoDia;
  // Despues con el libro: todo lo del editor en APIMart, por esta puerta y por /api/editor-costos, o lo del contador del dia si es mas.
  let hoy, pedidos;
  try { ({ hoy, pedidos, cont } = await hoyDelEditor(env, dia, cont)); } catch { return sinSumarHoy(); }
  if (pedidos >= MAX_PEDIDOS_DIA) return muchosPedidos(pedidos);
  if (hoy + estimado > TOPE_DIA_USD + 1e-9) {
    return json(429, { ok: false, gastadoHoyUsd: hoy, topeDiaUsd: TOPE_DIA_USD, error: 'Hoy (día UTC, cambia a las 7 p. m. de Colombia) el editor ya gastó ' + dinero(hoy) + ' en APIMart y esto sumaría ' + dinero(estimado) + ': el tope del día es ' + dinero(TOPE_DIA_USD) + '. Sigue mañana o avísale al dueño. (Lo de los últimos segundos puede no estar sumado todavía.)' });
  }

  // La ficha del anuncio (o una nueva con lo que el libro ya tenia de el), DESPUES de los topes del dia.
  const clave = await claveFicha(q.p, q.a);
  let ficha;
  try { ficha = (await leerFicha(env, clave)) || (await fichaNueva(env, q.p, q.a)); } catch {
    return json(503, { ok: false, reintentar: true, error: 'No pude leer lo que ya gastó este anuncio (el almacén no contestó): no pido nada. Reintenta en un momento.' });
  }

  // Tope del anuncio: lo que ya tenia el libro + lo pedido por aqui.
  const gastadoAnuncio = r6((Number(ficha.legado) || 0) + gastadoFicha(ficha));
  if (gastadoAnuncio + estimado > tope.usd + 1e-9) {
    const mas = tope.usd < TECHO_ANUNCIO_USD ? ' Si de verdad hace falta, una persona puede subirlo hasta ' + dinero(TECHO_ANUNCIO_USD) + ' con topeAnuncio y motivoTope.' : '';
    return json(402, { ok: false, gastadoAnuncioUsd: gastadoAnuncio, topeUsd: tope.usd, error: 'No genero nada: el anuncio ' + q.a + ' de ' + q.p + ' ya gastó ' + dinero(gastadoAnuncio) + ' y esto sumaría ' + dinero(estimado) + ', por encima del tope de ' + dinero(tope.usd) + '. Para lo que falta tapa con stock (videos de stock o una imagen con movimiento).' + mas });
  }

  // No repetir clips: un clip ya pagado (no fallido) con la misma base de archivo o el mismo prompt e imagen no se pide otra vez, salvo que una persona lo pida (uno por anuncio).
  const base = baseArchivo(archivo);
  let repite = false;
  if (tipo === 'video') {
    const previo = ficha.items.find((x) => x.tp === 'video' && x.s !== 'fallo' && (x.b === base || x.h === prep.h));
    if (previo) {
      if (!motivoRepetir) return json(409, { ok: false, error: 'No genero nada: el clip ' + archivo + ' del anuncio ' + q.a + ' ya se pagó (mismo archivo o mismo prompt e imagen). No se repiten clips por un error: tapa ese tramo con un video de stock o una imagen con movimiento, o recórtalo. Si una persona pidió repetirlo, manda «repetir» con el motivo (uno por anuncio).' });
      if ((Number(ficha.repetidos) || 0) >= LIMITES.repetidos) return json(409, { ok: false, error: 'No genero nada: el anuncio ' + q.a + ' ya repitió ' + LIMITES.repetidos + ' clip, el máximo. Tapa ese tramo con un video de stock o una imagen con movimiento.' });
      repite = true;
    }
    // La voz: VOICE y ACCENT van identicos en todos los clips con dialogo del anuncio (si cambian, cambia la voz o el acento a mitad del anuncio). Se compara la huella con la de los
    // clips ya pedidos (no fallidos); los de antes de esta regla no la tienen y no cuentan. De cada clip vale el ULTIMO pedido (el repetido reemplaza al de antes), y al REPETIR un clip
    // no se compara con ese mismo clip: asi se corrige un clip que salio con otra voz y los siguientes se comparan con la corregida.
    const ultimoDeCada = new Map();
    for (const x of ficha.items) if (x.tp === 'video' && x.s !== 'fallo') ultimoDeCada.set(x.b, x);
    const otraVoz = prep.vz ? Array.from(ultimoDeCada.values()).find((x) => !(repite && x.b === base) && typeof x.vz === 'string' && x.vz && x.vz !== prep.vz) : null;
    if (otraVoz) return json(409, { ok: false, error: 'No genero nada: VOICE y ACCENT de este clip no son idénticos a los de los otros clips del anuncio ' + q.a + ' (el clip ' + otraVoz.b + ' ya se pidió con otra voz o acento). Copia esos dos bloques palabra por palabra del primer clip: si cambian, la voz cambia a mitad del anuncio.' });
  }
  if (!hacerLugar(ficha)) return json(409, { ok: false, error: 'Este anuncio ya tiene ' + LIMITES.itemsFicha + ' clips pedidos: es demasiado para un anuncio. Avísale al dueño.' });

  // EL CONTADOR DEL DIA y LA RESERVA, antes de pedir nada: si no se pueden escribir, no se pide (503). El contador va primero: si despues falla la ficha, queda contado un pedido que no
  // se hizo (es un freno: contar de mas no hace dano). Si el contador rebota y al releerlo el dia ya no da (otro pedido lo lleno entretanto), su 429 y no se pide.
  const anotado = await sumarAlContador(env, dia, cont, estimado, 1, 0);
  if (anotado !== true) return anotado || sinContador();
  const tReserva = new Date().toISOString();
  const item = { r: azarHex(16), tp: tipo, b: base, h: prep.h, e: estimado, s: 'pedido', t: tReserva, et: q.et };
  if (repite) item.rep = motivoRepetir;
  if (prep.vz) item.vz = prep.vz;
  if (tope.motivo) item.motivoTope = tope.motivo;
  ficha.items.push(item);
  if (repite) ficha.repetidos = (Number(ficha.repetidos) || 0) + 1;
  if (!(await guardarFicha(env, clave, ficha))) return json(503, { ok: false, reintentar: true, error: 'No pude anotar la reserva del gasto (el almacén rechazó la escritura): no pido nada a APIMart. Reintenta en un momento.' });

  let resp;
  try { resp = await llamarApimart(llave, 'POST', prep.ruta, prep.payload, 30000); } catch {
    item.s = 'dudoso';                     // no se sabe si APIMart lo recibio: la reserva sigue contando
    await guardarFichaConEspera(env, clave, ficha);
    return json(502, { ok: false, error: 'APIMart no contestó al pedido. Puede que lo haya recibido: la reserva de ' + dinero(estimado) + ' queda contada en el anuncio. Mira el resultado antes de pedir otra vez.' });
  }
  if (!resp.ok) {
    const motivo = await errorDeApimart(resp, llave);
    ficha.items = ficha.items.filter((x) => x !== item);          // APIMart dijo que no: no se cobro, se quita la reserva
    if (repite) ficha.repetidos = Math.max(0, (Number(ficha.repetidos) || 0) - 1);
    await guardarFichaConEspera(env, clave, ficha);
    return json(502, { ok: false, estadoApimart: resp.status, error: 'APIMart no aceptó el pedido (HTTP ' + resp.status + ')' + (motivo ? ': ' + motivo : '') });
  }
  let j = null;
  try { j = await resp.json(); } catch { j = null; }
  const k = idDeTarea(j);
  if (!k) {
    item.s = 'dudoso';
    await guardarFichaConEspera(env, clave, ficha);
    return json(502, { ok: false, error: 'APIMart contestó sin el id de la tarea: la reserva de ' + dinero(estimado) + ' queda contada. Avísale al dueño.' });
  }

  // El libro de gasto, con la hora de la reserva y una huella fija: anotarlo dos veces no cuenta doble.
  const detalle = 'editor:' + q.p + '|' + q.a + '|' + tipo + '|' + q.et;
  const libro = await registrarGastoUnico(env, 'apimart', estimado, detalle, tReserva, await huellaHex('dash|' + item.r, 16), DIAS_LIBRO);
  const ticket = await firmarTicket(env, { v: 1, k, r: item.r, p: q.p, a: q.a, tp: tipo, e: estimado, t: tReserva, et: q.et, ar: archivo, l: libro === 'nuevo' || libro === 'repetido' ? 1 : 0 });
  return json(200, { ok: true, tarea: { id: k, ticket }, estimadoUsd: estimado, gastadoAnuncioUsd: r6(gastadoAnuncio + estimado), topeUsd: tope.usd });
}
export const pedirImagen = (env, b) => crear(env, b, 'imagen');
export const pedirVideo = (env, b) => crear(env, b, 'video');

// ---------------------------------------------------------------- consultar una tarea
// Al terminar se corrige el libro UNA vez: completed con un costo distinto del estimado -> el real; failed/cancelled sin costo -> se borra el gasto. Lo ya asentado no se vuelve a escribir.
async function asentar(env, tk, estado, costo) {
  const clave = await claveFicha(tk.p, tk.a);
  let ficha;
  try { ficha = await leerFicha(env, clave); } catch { return; }
  const item = ficha && ficha.items.find((x) => x.r === tk.r);
  if (!item || item.s === 'listo' || item.s === 'fallo') return;
  const e = Number(item.e) || 0;
  let real, nuevo;
  if (estado === 'completed') {
    if (costo == null || Math.abs(costo - e) < 1e-9) {
      if (tk.l === 1) return;                                     // el libro ya tiene el estimado y es lo cobrado (o APIMart no lo dijo): nada que escribir
      real = costo == null ? e : costo;                           // el libro no se pudo anotar al pedir: se anota ahora
    } else real = costo;
    nuevo = 'listo';
  } else {
    real = costo != null && costo > 0 ? costo : 0;                // fallo sin cobrar: el gasto se borra del libro
    nuevo = 'fallo';
  }
  if (!(tk.l === 1 && Math.abs(real - e) < 1e-9)) {
    const r = await fijarGastoUnico(env, 'apimart', real, 'editor:' + tk.p + '|' + tk.a + '|' + tk.tp + '|' + tk.et, tk.t, await huellaHex('dash|' + tk.r, 16), DIAS_LIBRO);
    if (r !== 'fijado' && r !== 'borrado') return;              // la proxima consulta lo intenta otra vez
  }
  item.c = real;
  item.s = nuevo;
  await guardarFicha(env, clave, ficha);
}

export async function consultarTarea(env, b) {
  const tk = await leerTicket(env, b.ticket);
  if (!tk) return json(401, { ok: false, error: 'Ticket no válido: usa el que devolvió el dash al pedir la imagen o el clip.' });
  if (tk.vencido) return json(401, { ok: false, error: 'El ticket venció (dura ' + LIMITES.ticketDias + ' días): lo que se generó ya no se consulta por aquí.' });
  const llave = await leerLlave(env);
  if (!llave) return sinLlave();
  let r;
  try { r = await llamarApimart(llave, 'GET', '/v1/tasks/' + tk.k + '?language=es', null, 20000); } catch {
    return json(502, { ok: false, reintentar: true, error: 'APIMart no contestó al consultar la tarea: prueba otra vez en unos segundos.' });
  }
  if (!r.ok) {
    const motivo = await errorDeApimart(r, llave);
    return json(502, { ok: false, estadoApimart: r.status, error: 'APIMart contestó ' + r.status + ' al consultar la tarea' + (motivo ? ': ' + motivo : '') });
  }
  let j = null;
  try { j = await r.json(); } catch { j = null; }
  const d = j && j.data && typeof j.data === 'object' && !Array.isArray(j.data) ? j.data : (j || {});
  const estado = String(d.status || '').toLowerCase().replace(/[^a-z_-]/g, '').slice(0, 30);
  const avance = Number.isFinite(Number(d.progress)) && d.progress !== null && d.progress !== '' ? Number(d.progress) : null;
  const costo = costoValido(d.cost);
  if (estado === 'completed' || estado === 'failed' || estado === 'cancelled') await asentar(env, tk, estado, costo);
  const salida = { ok: true, estado, avance, urls: estado === 'completed' ? urlsDe(d.result, llave) : [] };
  if (costo != null) salida.costoUsd = costo;
  if (d.error) {
    const crudo = typeof d.error === 'string' ? d.error : (d.error && typeof d.error.message === 'string' ? d.error.message : '');
    const msg = ocultar(crudo, llave).replace(/[\x00-\x1f\x7f]+/g, ' ').trim().slice(0, 300);
    if (msg) salida.error = msg;
  }
  return json(200, salida);
}

// ---------------------------------------------------------------- subir una referencia
export async function subirImagen(env, params, bytes) {
  const p = params.get('producto') || '', a = params.get('anuncio') || '';
  if (!RE_PRODUCTO.test(p) || !RE_ANUNCIO.test(a)) return json(400, { ok: false, error: 'Faltan «producto» y «anuncio» (para qué es la imagen).' });
  if (!bytes || !bytes.byteLength) return json(400, { ok: false, error: 'La imagen llegó vacía.' });
  if (bytes.byteLength > LIMITES.subirBytes) return json(413, { ok: false, error: 'La imagen pesa más de 10 MB: achícala antes.' });
  const tipo = tipoDeImagen(bytes);
  if (!tipo) return json(415, { ok: false, error: 'Solo PNG, JPEG o WEBP (se mira por los primeros bytes del archivo).' });
  const llave = await leerLlave(env);
  if (!llave) return sinLlave();
  // El contador del dia: el tope de subidas y +1 ANTES de reenviar (si no se puede anotar, no se sube; si rebota y al releerlo ya hay MAX_SUBIDAS_DIA, 429 y no se sube). Va antes de
  // cualquier otra cosa de KV: subir no hace list().
  const dia = diaUTC();
  let cont;
  try { cont = await leerContador(env, dia); } catch {
    return json(503, { ok: false, reintentar: true, error: 'No pude leer el contador del día (el almacén no contestó): no subo nada. Reintenta en un momento.' });
  }
  if (cont.s >= MAX_SUBIDAS_DIA) return muchasSubidas(cont.s);
  const anotada = await sumarAlContador(env, dia, cont, 0, 0, 1);
  if (anotada !== true) return anotada || json(503, { ok: false, reintentar: true, error: 'No pude anotar la subida en el contador del día (el almacén rechazó la escritura): no subo nada. Reintenta en un momento.' });
  const forma = new FormData();
  forma.append('file', new Blob([bytes], { type: tipo.mime }), 'referencia.' + tipo.ext);
  let r;
  try { r = await llamarApimart(llave, 'POST', '/v1/uploads/images', forma, 60000); } catch {
    return json(502, { ok: false, reintentar: true, error: 'APIMart no contestó al subir la imagen: prueba otra vez.' });
  }
  if (!r.ok) {
    const motivo = await errorDeApimart(r, llave);
    return json(502, { ok: false, estadoApimart: r.status, error: 'APIMart no aceptó la imagen (HTTP ' + r.status + ')' + (motivo ? ': ' + motivo : '') });
  }
  let j = null;
  try { j = await r.json(); } catch { j = null; }
  const u = j && (typeof j.url === 'string' ? j.url : (j.data && typeof j.data.url === 'string' ? j.data.url : ''));
  if (!urlHttps(u) || u.indexOf(llave) !== -1) return json(502, { ok: false, error: 'APIMart no devolvió la dirección de la imagen subida.' });
  return json(200, { ok: true, url: u, expira: Math.floor(Date.now() / 1000) + HORAS_SUBIDA * 3600 });
}

// ---------------------------------------------------------------- texto y vision
export function costoTexto(modelo, entrada, salida) {
  const fila = PRECIOS_TEXTO.find((x) => x[0].test(modelo || ''));
  const pe = fila ? fila[1] : PRECIO_TEXTO_DESCONOCIDO[0], ps = fila ? fila[2] : PRECIO_TEXTO_DESCONOCIDO[1];
  return r6(((Number(entrada) || 0) * pe + (Number(salida) || 0) * ps) / 1e6);
}
// Los tokens que dice la respuesta (JSON o, si llego en streaming, las lineas «data:»). null si no dice.
export function usoDeTexto(texto) {
  const leer = (u) => {
    if (!u || typeof u !== 'object') return null;
    const entrada = Number(u.prompt_tokens || u.input_tokens || 0);
    let salida = Number(u.completion_tokens || u.output_tokens || 0);
    if (Number(u.total_tokens) > 0) salida = Math.max(salida, Number(u.total_tokens) - entrada);      // los tokens de razonamiento se cobran como salida
    return entrada > 0 || salida > 0 ? { entrada, salida } : null;
  };
  try {
    const j = JSON.parse(texto);
    return leer(j && (j.usage || (j.data && j.data.usage)));
  } catch { /* puede venir en streaming */ }
  let uso = null;
  for (const linea of String(texto).split('\n')) {
    const l = linea.trim();
    if (l.indexOf('data:') !== 0) continue;
    try { const j = JSON.parse(l.slice(5).trim()); uso = leer(j && j.usage) || uso; } catch { /* un trozo raro no importa */ }
  }
  return uso;
}

export async function pedirTexto(env, b) {
  const q = quienGasta(b);
  if (q.error) return json(400, { ok: false, error: q.error });
  const modelo = typeof b.modelo === 'string' ? b.modelo.trim() : '';
  if (MODELOS_TEXTO.indexOf(modelo) === -1) return json(400, { ok: false, error: 'modelo no permitido: usa uno de ' + MODELOS_TEXTO.join(', ') + '.' });
  const sistema = b.sistema == null ? '' : b.sistema;
  if (typeof sistema !== 'string' || sistema.length > LIMITES.sistema) return json(400, { ok: false, error: '«sistema» es un texto de hasta ' + LIMITES.sistema + ' letras.' });
  const usuario = b.usuario;
  if (typeof usuario !== 'string' || !usuario.trim() || usuario.length > LIMITES.usuario) return json(400, { ok: false, error: '«usuario» es un texto de 1 a ' + LIMITES.usuario + ' letras.' });
  const imagenes = b.imagenes == null ? [] : b.imagenes;
  if (!Array.isArray(imagenes) || imagenes.length > LIMITES.textoImagenes) return json(400, { ok: false, error: '«imagenes» es una lista de hasta ' + LIMITES.textoImagenes + ' fotogramas.' });
  if (!imagenes.every((x) => typeof x === 'string' && x.length <= LIMITES.dataUri && RE_DATA_URI.test(x) && /^[A-Za-z0-9+/=]*$/.test(x.slice(-8)))) {
    return json(400, { ok: false, error: 'Cada fotograma es data:image/jpeg, png o webp en base64, de hasta ' + LIMITES.dataUri + ' caracteres (achícalo a 1024 px antes).' });
  }
  const maxTokens = b.max_tokens == null ? 1800 : Number(b.max_tokens);
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > LIMITES.maxTokens) return json(400, { ok: false, error: '«max_tokens» va de 1 a ' + LIMITES.maxTokens + '.' });
  const temperatura = b.temperatura == null ? 0.4 : Number(b.temperatura);
  if (!Number.isFinite(temperatura) || temperatura < 0 || temperatura > 2) return json(400, { ok: false, error: '«temperatura» va de 0 a 2.' });
  if (b.json_modo != null && typeof b.json_modo !== 'boolean') return json(400, { ok: false, error: '«json_modo» es true o false.' });
  const jsonModo = b.json_modo !== false;
  const quitar = b.quitar == null ? [] : b.quitar;
  if (!Array.isArray(quitar) || !quitar.every((c) => CAMPOS_QUITABLES.indexOf(c) !== -1)) return json(400, { ok: false, error: '«quitar» solo puede nombrar: ' + CAMPOS_QUITABLES.join(', ') + '.' });
  // El techo de la respuesta (y del costo) es max_tokens: se puede quitar UNO de los dos campos (cada modelo entiende uno), nunca los dos. El editor pasa entonces al modelo siguiente.
  if (quitar.indexOf('max_tokens') !== -1 && quitar.indexOf('max_completion_tokens') !== -1) return json(400, { ok: false, error: 'No se pueden quitar los dos límites de respuesta (max_tokens y max_completion_tokens): sin ninguno, la respuesta y el costo no tienen techo. Prueba con otro modelo.' });
  const llave = await leerLlave(env);
  if (!llave) return sinLlave();

  // Tope del dia con lo MAXIMO que podria costar esta llamada (un techo: el real se anota despues): primero solo con el contador (UN get: si ya no entra, 429 sin ningun list()), despues
  // con lo mayor entre el libro y el contador.
  const techo = costoTexto(modelo, Math.ceil((sistema.length + usuario.length) / 3) + 1500 * imagenes.length, maxTokens);
  const dia = diaUTC();
  let cont;
  try { cont = await leerContador(env, dia); } catch { return sinSumarHoy(); }
  const frenoDia = frenoDelContador(cont, techo);
  if (frenoDia) return frenoDia;
  let hoy, pedidos;
  try { ({ hoy, pedidos, cont } = await hoyDelEditor(env, dia, cont)); } catch { return sinSumarHoy(); }
  if (pedidos >= MAX_PEDIDOS_DIA) return muchosPedidos(pedidos);
  if (hoy + techo > TOPE_DIA_USD + 1e-9) {
    return json(429, { ok: false, gastadoHoyUsd: hoy, topeDiaUsd: TOPE_DIA_USD, error: 'Hoy (día UTC) el editor ya gastó ' + dinero(hoy) + ' en APIMart: el tope del día es ' + dinero(TOPE_DIA_USD) + '. Sigue mañana o avísale al dueño.' });
  }
  // El contador del dia (con el techo), ANTES de pedir: si no se puede escribir, no se pide (503); si rebota y al releerlo el dia ya no da, su 429 y no se pide.
  const anotado = await sumarAlContador(env, dia, cont, techo, 1, 0);
  if (anotado !== true) return anotado || sinContador();

  const contenido = imagenes.length ? [{ type: 'text', text: usuario }].concat(imagenes.map((u) => ({ type: 'image_url', image_url: { url: u } }))) : usuario;
  const cuerpo = { model: modelo, messages: [{ role: 'system', content: sistema }, { role: 'user', content: contenido }], stream: false, max_tokens: maxTokens, max_completion_tokens: maxTokens, temperature: temperatura };
  if (jsonModo) cuerpo.response_format = { type: 'json_object' };
  for (const c of quitar) delete cuerpo[c];

  let r;
  try { r = await llamarApimart(llave, 'POST', '/v1/chat/completions', cuerpo, 100000); } catch {
    return json(502, { ok: false, reintentar: true, error: 'APIMart no contestó (red o tiempo agotado).' });
  }
  let leido = '';
  try { const bytes = await leerCuerpo(r, 2 * 1024 * 1024); leido = bytes ? new TextDecoder().decode(bytes) : ''; } catch { leido = ''; }
  const visible = ocultar(leido, llave).slice(0, LIMITES.respuestaTexto);

  let costo = 0;
  if (r.status === 200) {
    const uso = usoDeTexto(leido) || { entrada: Math.ceil((sistema.length + usuario.length) / 4) + 1100 * imagenes.length, salida: maxTokens };     // sin «usage»: se estima por lo alto
    costo = costoTexto(modelo, uso.entrada, uso.salida);
    if (costo > 0) await registrarGastoUnico(env, 'apimart', costo, 'editor:' + q.p + '|' + q.a + '|texto|' + q.et, new Date().toISOString(), azarHex(16), DIAS_LIBRO);
  }
  return json(200, { ok: true, status: r.status, cuerpo: visible, costoEstimadoUsd: costo });
}

// ---------------------------------------------------------------- estado
export async function estadoEditor(env, params) {
  const p = params.get('producto') || '', a = params.get('anuncio') || '';
  if ((p || a) && (!RE_PRODUCTO.test(p) || !RE_ANUNCIO.test(a))) return json(400, { ok: false, error: '«producto» y «anuncio» van juntos y bien escritos.' });
  const llave = await leerLlave(env);
  if (!llave) return sinLlave();
  let hoy, pedidos, cont;
  try { ({ hoy, pedidos, cont } = await hoyDelEditor(env, diaUTC())); } catch {
    return json(503, { ok: false, reintentar: true, error: 'No pude sumar lo gastado hoy (el almacén no contestó). Reintenta en un momento.' });
  }
  const salida = {
    // versionMinima vale para el estado, las tareas, el texto y las subidas; versionMinimaImagenVideo, para pedir imagenes y clips (la que revisa los prompts antes de pagar).
    ok: true, versionMinima: VERSION_MINIMA_EDITOR, versionMinimaImagenVideo: VERSION_MINIMA_IMAGEN_VIDEO, topeAnuncioUsd: TOPE_ANUNCIO_USD, techoAnuncioUsd: TECHO_ANUNCIO_USD, topeDiaUsd: TOPE_DIA_USD, gastadoHoyUsd: hoy,
    pedidosHoy: pedidos, maxPedidosDia: MAX_PEDIDOS_DIA, subidasHoy: cont.s, maxSubidasDia: MAX_SUBIDAS_DIA,
    modelos: { imagen: MODELO_IMAGEN, video: MODELO_VIDEO, texto: MODELOS_TEXTO },
  };
  if (p) {
    const clave = await claveFicha(p, a);
    let ficha;
    try { ficha = await leerFicha(env, clave); } catch {
      return json(503, { ok: false, reintentar: true, error: 'No pude leer lo que ya gastó este anuncio. Reintenta en un momento.' });
    }
    if (!ficha) {
      // Sin ficha: este anuncio no ha pedido nada por esta puerta. Lo que el libro ya tenia de el (el legado) se calcula en su PRIMER pedido, con una pasada por el libro entero; el estado
      // no la hace (serian varios list() por consulta y no se guarda nada, y el editor pregunta el estado antes de cada etapa). El editor toma lo mayor entre esto y lo que anoto el.
      salida.anuncio = { gastadoUsd: 0, legadoUsd: null, legadoSinCalcular: true, clipsPagados: [], repetidos: 0 };
    } else {
      const clips = [];
      ficha.items.forEach((x) => { if (x.tp === 'video' && x.s !== 'fallo' && clips.indexOf(x.b) === -1) clips.push(x.b); });
      salida.anuncio = { gastadoUsd: r6((Number(ficha.legado) || 0) + gastadoFicha(ficha)), legadoUsd: Number(ficha.legado) || 0, legadoSinCalcular: false, clipsPagados: clips, repetidos: Number(ficha.repetidos) || 0 };
    }
  }
  return json(200, salida);
}
