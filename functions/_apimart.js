// Todo lo que la suite sabe de APIMart (apimart.ai), en un solo lugar.
//
// APIMart reparte con UNA sola llave texto (GPT, Claude, Gemini, DeepSeek...), imagen, video y audio.
// Su listado GET /v1/models?expand=category dice la categoria de cada modelo (chat / image / video /
// audio) y SOLO trae lo que esa llave tiene permitido: una llave con restricciones de modelo, o de un
// grupo sin acceso a todo, ve una lista mas corta (docs.apimart.ai/en/api-reference/texts/models/list).
//
// Las pantallas de texto, de imagen y la de APIs piden el catalogo a traves de aqui, para que haya una
// sola consulta, una sola cache y una sola forma de leer la respuesta. Lo que NO esta aqui: la llamada
// de chat (functions/_llm.js) y la generacion de imagenes (functions/api/ebook-pagina.js).
//
// La llave no sale de este modulo hacia el navegador: se usa en la cabecera Authorization de las
// peticiones a api.apimart.ai y en ningun otro lado.

import { precioImagen, IMAGEN_APIMART_CONOCIDOS } from './_precios.js';

export const BASE = 'https://api.apimart.ai';
export const CLAVE_CATALOGO = 'apimart:catalogo';
const HORAS = 12;
const TIMEOUT_MS = 20000;
const CATEGORIAS = ['chat', 'image', 'video', 'audio'];

// Los modelos con los que se arranca cuando el dueno conecta APIMart sin elegir ninguno. El de imagen es
// el "-ext": precio fijo por imagen, la mitad del mismo modelo sin sufijo (functions/_precios.js).
export const MODELO_IMAGEN_BASE = 'gemini-3.1-flash-lite-image-ext';
export const MODELO_TEXTO_BASE = 'gemini-2.5-flash';
export const URL_VIDEO = BASE + '/v1/videos/generations';

// ---------------------------------------------------------------- la llave

// Lo que se pega del panel casi nunca llega limpio: con un espacio o un salto de linea al final, entre
// comillas, o con el «Bearer » delante. Una llave con esos restos guarda "bien" y falla despues con un
// 401 que no dice por que.
export function limpiarLlave(v) {
  if (typeof v !== 'string') return '';
  let s = v.trim();
  s = s.replace(/^(?:authorization\s*:\s*)?bearer\s+/i, '');
  s = s.replace(/^["'`“”‘’]+|["'`“”‘’]+$/g, '').trim();
  return s;
}
// Una llave de API son caracteres ASCII visibles, sin espacios. Mas corta que 8 es un pedazo, no una llave.
export function llaveValida(s) { return typeof s === 'string' && /^[\x21-\x7e]{8,500}$/.test(s); }

// Un mensaje que va al navegador nunca debe llevar la llave, ni por un error que la repita.
export function ocultar(texto, llave) {
  const t = String(texto == null ? '' : texto);
  return llave && llave.length >= 8 ? t.split(llave).join('****') : t;
}

// La llave de APIMart que haya guardada, venga de donde venga: de un proveedor de texto de tipo apimart,
// de la API de imagenes si es APIMart, o del video. Es la misma llave puesta en tres lugares, porque cada
// pantalla de la suite lee la suya (secreto:<id>, secreto:imagen, secreto:video).
// sinImagen: cuando se esta cambiando la API de imagenes A APIMart, secreto:imagen todavia es la llave del
// proveedor anterior y no se debe tomar por la de APIMart.
export async function llaveApimart(env, ajustes, opciones) {
  const a = ajustes || {};
  const sinImagen = !!(opciones && opciones.sinImagen);
  for (const p of (a.proveedores || [])) {
    if (p && p.tipo === 'apimart') {
      const k = await env.SUITE.get('secreto:' + p.id);
      if (k) return k;
    }
  }
  if (!sinImagen && a.imagenApi && a.imagenApi.tipo === 'apimart') {
    const k = await env.SUITE.get('secreto:imagen');
    if (k) return k;
  }
  if (esVideoApimart(a)) {
    const k = await env.SUITE.get('secreto:video');
    if (k) return k;
  }
  return null;
}
export function esVideoApimart(ajustes) {
  const v = (ajustes && ajustes.videoApi) || {};
  return /apimart/i.test(String(v.nombre || '')) || /api\.apimart\.ai/i.test(String(v.url || ''));
}

// ---------------------------------------------------------------- el catalogo

const RE_IMAGEN = /image|imagen|imagine|flux|seedream|nano-banana|midjourney|dall-e|z-image|ideogram|recraft/i;
const RE_VIDEO = /veo|sora|kling|seedance|hailuo|vidu|happyhorse|pixverse|skyreels|runway|\bluma\b|\bpika\b|gemini-omni|omni-flash|wan\d/i;
const RE_AUDIO = /whisper|tts|suno|lyria|music|speech|voice|flow-music/i;

// La categoria que dice APIMart; y si falta o dice "unknown" (modelos que su plataforma aun no cataloga),
// se deduce del nombre. Un id que no se parece a nada se trata como de texto: es lo mas comun. Si el id dice
// "video" manda eso; si no, "image" gana sobre la familia de video (wan2.7-image es imagen, wan2.6 es video).
export function categoriaDe(m) {
  const c = String((m && m.category) || '').toLowerCase();
  if (CATEGORIAS.indexOf(c) !== -1) return c;
  const id = String((m && m.id) || '');
  if (/video/i.test(id)) return 'video';
  if (RE_IMAGEN.test(id)) return 'image';
  if (RE_VIDEO.test(id)) return 'video';
  if (RE_AUDIO.test(id)) return 'audio';
  return 'chat';
}

// Pide el catalogo completo (las cuatro categorias en una sola llamada) con la llave. Nunca lanza.
//   { ok:true, modelos:[{ id, cat, tags, dueno }] }
//   { ok:false, rechazada:true, error }   la llave no sirve (401/403)
//   { ok:false, error }                   cualquier otro problema (red, 5xx, respuesta rara)
export async function pedirCatalogo(llave) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(BASE + '/v1/models?expand=category', {
      headers: { Authorization: 'Bearer ' + llave }, signal: ctrl.signal,
    });
    if (r.status === 401 || r.status === 403) {
      return { ok: false, rechazada: true, status: r.status,
        error: 'APIMart rechazó la llave (' + r.status + '). Revisa que esté completa y que no esté desactivada en apimart.ai/keys.' };
    }
    if (!r.ok) return { ok: false, status: r.status, error: 'APIMart respondió ' + r.status + ' al pedir la lista de modelos.' };
    const j = await r.json();
    const lista = Array.isArray(j) ? j : (Array.isArray(j && j.data) ? j.data : []);
    const vistos = new Set();
    const modelos = [];
    for (const m of lista) {
      if (!m || typeof m.id !== 'string' || !m.id || vistos.has(m.id)) continue;
      vistos.add(m.id);
      modelos.push({
        id: m.id, cat: categoriaDe(m),
        tags: Array.isArray(m.capability_tags) ? m.capability_tags.filter((x) => typeof x === 'string').slice(0, 6) : [],
        dueno: typeof m.owned_by === 'string' ? m.owned_by : '',
      });
    }
    if (!modelos.length) {
      return { ok: false, error: 'APIMart no devolvió ningún modelo. Si la llave tiene restricciones de modelo, quítalas en apimart.ai/keys.' };
    }
    return { ok: true, modelos };
  } catch (e) {
    return { ok: false, error: e && e.name === 'AbortError' ? 'APIMart no contestó en 20 s.' : 'No pude hablar con APIMart: ' + String((e && e.message) || e).slice(0, 120) };
  } finally {
    clearTimeout(t);
  }
}

// El catalogo con cache de 12 horas en KV, para no pedirlo cada vez que alguien abre una pantalla. Sin
// `refrescar` se sirve la cache aunque no haya llave a mano (el editor lee el catalogo sin ser dueno); con
// `refrescar`, o sin cache, se pide de verdad y hace falta la llave.
export async function catalogoApimart(env, llave, refrescar) {
  if (!refrescar) {
    try {
      const c = await env.SUITE.get(CLAVE_CATALOGO, { type: 'json' });
      if (c && Array.isArray(c.modelos) && c.modelos.length) return { ok: true, modelos: c.modelos, traidoEn: c.traidoEn || '', deCache: true };
    } catch { /* una cache ilegible no puede tumbar la pantalla: se pide de nuevo */ }
  }
  if (!llave) return { ok: false, sinLlave: true, error: 'No hay llave de APIMart guardada.' };
  const r = await pedirCatalogo(llave);
  if (!r.ok) return Object.assign({}, r, { error: ocultar(r.error, llave) });
  const traidoEn = await guardarCatalogo(env, r.modelos);
  return { ok: true, modelos: r.modelos, traidoEn, deCache: false };
}

// Guardarla es un extra: si KV rechaza la escritura (p. ej. el tope diario) el catalogo recien traido se
// entrega igual. Devuelve la hora a la que se trajo.
export async function guardarCatalogo(env, modelos) {
  const traidoEn = new Date().toISOString();
  try {
    await env.SUITE.put(CLAVE_CATALOGO, JSON.stringify({ modelos, traidoEn }), { expirationTtl: HORAS * 3600 });
  } catch { /* ver arriba */ }
  return traidoEn;
}

// documenta su endpoint de uso: credits = amount_usd x 10). remain_balance no se usa como dinero porque su
// unidad depende de como este configurada su plataforma (USD, CNY o tokens). Si algo falla, null.
export async function saldoApimart(llave) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    const r = await fetch(BASE + '/v1/user/balance', { headers: { Authorization: 'Bearer ' + llave }, signal: ctrl.signal });
    if (!r.ok) return null;
    const j = await r.json();
    if (!j || j.success === false) return null;
    const cr = Number(j.remain_credits);
    if (!isFinite(cr) || cr < 0) return null;
    // used_credits = lo consumido por la cuenta entera desde que existe (lo usa el cuadre, functions/_apimart-cuadre.js). null si APIMart no lo trae: el saldo sigue valiendo.
    const usado = j.used_credits == null ? NaN : Number(j.used_credits);
    return { creditos: cr, usd: Math.round(cr / 10 * 100) / 100, usadoCreditos: isFinite(usado) && usado >= 0 ? usado : null };
  } catch { return null; } finally { clearTimeout(t); }
}

// ---------------------------------------------------------------- listas para las pantallas

const NO_ES_CHAT = /embed|moderation|rerank|realtime|transcribe|tts|whisper/i;

// Los de texto: categoria chat, sin los de embeddings, moderacion o voz que viven en la misma categoria.
export function modelosDeTexto(modelos) {
  return (modelos || []).filter((m) => m.cat === 'chat' && !NO_ES_CHAT.test(m.id)
    && !(m.tags && m.tags.length && m.tags.every((t) => t === 'Embedding')));
}

// Los de imagen que la suite sabe pedir. Midjourney tiene sus propias rutas (/v1/midjourney/...) y no
// responde al /v1/images/generations que usa la suite; y un modelo que solo EDITA una imagen ya hecha no
// sirve para crear la de una pagina. «Solo edita» = declara imagen-a-imagen y NO texto-a-imagen: con
// cualquier otra cosa (sin etiquetas, o etiquetas con otro texto que no se ha visto) el modelo se deja, porque
// ocultar uno bueno cuesta mas que mostrar uno que no sirve, y este ultimo ya se avisa al fallar.
const SOLO_EDITA = (tags) => tags.some((t) => /image.?to.?image|img2img/i.test(t)) && !tags.some((t) => /text.?to.?image|txt2img/i.test(t));
export function imagenesGenerables(modelos) {
  return (modelos || []).filter((m) => m.cat === 'image' && !/midjourney/i.test(m.id) && !SOLO_EDITA(m.tags || []));
}

const dolar = (usd) => '~$' + (usd < 0.1 ? usd.toFixed(3) : usd.toFixed(2)) + ' por imagen';

// El selector de imagen: los que tienen precio en la tabla primero (el mas barato arriba), despues el
// resto por nombre. Los modelos puestos (paginas, portada, mockup) van siempre, aunque el catalogo no los
// traiga, para que el estimador de ebooks pueda ponerles precio.
export function listaImagen(modelosCatalogo, puestos) {
  const filas = imagenesGenerables(modelosCatalogo).map((m) => m.id);
  return armarListaImagen(filas, puestos);
}
// Cuando no se pudo pedir el catalogo: los modelos que la tabla de precios ya conoce.
export function listaImagenRespaldo(puestos) {
  return armarListaImagen(IMAGEN_APIMART_CONOCIDOS.map((m) => m.id), puestos);
}
function armarListaImagen(ids, puestos) {
  const todos = ids.slice();
  (puestos || []).forEach((id) => { if (id && todos.indexOf(id) === -1) todos.push(id); });
  const lista = todos.map((id) => {
    const precio = precioImagen(id, null, 'apimart');
    const puesto = (puestos || []).indexOf(id) !== -1;
    return { id, nombre: id, precio,
      nota: precio != null ? dolar(precio) + (puesto ? ' · el que está puesto' : '') : 'sin precio en la tabla' + (puesto ? ' · el que está puesto' : ''),
      orden: precio != null ? 1 : 5 };
  });
  lista.sort((a, b) => a.orden - b.orden
    || (a.precio != null && b.precio != null ? a.precio - b.precio : 0)
    || a.id.localeCompare(b.id));
  return lista;
}

// El modelo de texto con el que arranca una conexion nueva: el conocido si el catalogo lo trae; si no, el
// primero de las familias baratas; y si no, el primero que haya.
export function elegirModeloTexto(modelos) {
  const ids = modelosDeTexto(modelos).map((m) => m.id);
  if (!ids.length || ids.indexOf(MODELO_TEXTO_BASE) !== -1) return MODELO_TEXTO_BASE;
  const barato = ids.filter((id) => /flash-lite|flash|mini|haiku/i.test(id)).sort()[0];
  return barato || ids.slice().sort()[0];
}
export function elegirModeloImagen(modelos) {
  const ids = imagenesGenerables(modelos).map((m) => m.id);
  if (!ids.length || ids.indexOf(MODELO_IMAGEN_BASE) !== -1) return MODELO_IMAGEN_BASE;
  const conPrecio = ids.map((id) => ({ id, p: precioImagen(id, null, 'apimart') })).filter((x) => x.p != null).sort((a, b) => a.p - b.p);
  return conPrecio.length ? conPrecio[0].id : ids.slice().sort()[0];
}

// Lo que la pantalla de APIs necesita saber del catalogo: cuantos hay de cada tipo y la lista para verla.
export function resumenCatalogo(cat) {
  const ms = cat.modelos || [];
  const texto = modelosDeTexto(ms).map((m) => m.id).sort();
  const imagen = imagenesGenerables(ms).map((m) => ({ id: m.id, precio: precioImagen(m.id, null, 'apimart') }))
    .sort((a, b) => a.id.localeCompare(b.id));
  const video = ms.filter((m) => m.cat === 'video').map((m) => m.id).sort();
  const audio = ms.filter((m) => m.cat === 'audio').map((m) => m.id).sort();
  return {
    traidoEn: cat.traidoEn || '', deCache: !!cat.deCache, total: ms.length,
    conteo: { texto: texto.length, imagen: imagen.length, video: video.length, audio: audio.length },
    modelos: { texto, imagen, video, audio },
  };
}
