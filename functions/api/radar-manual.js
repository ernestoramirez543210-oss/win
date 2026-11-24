// Las ofertas que el equipo agrega a mano en /radar: un enlace que vio (anuncio, pagina de un anunciante, pagina de ventas, video de TikTok) y de que es la oferta.
//
//   GET    /api/radar-manual                     -> { ok, ofertas: [oferta normalizada], leidoEn }                      todo el equipo
//   POST   /api/radar-manual { nombre, queEs, enlaces: [url], precio? }                  -> { ok, oferta }              todo el equipo (agrega)
//   POST   /api/radar-manual { id, nombre, queEs, enlaces, precio? }                      -> { ok, oferta }              todo el equipo (corrige una que ya esta)
//   DELETE /api/radar-manual?id=<id>                                                    -> { ok }                      solo el dueño
//
// Para que sirve: el radar solo muestra lo que encontro en Brasil (y lo que se anota a mano en la planilla). Aqui el equipo mete una oferta que vio por su cuenta y le dice DE QUE ES, que es
// lo que decide cual de las ofertas de una pagina que vende varias se modela. Se pinta en /radar como cualquier otra (`ofertaManual` de functions/_radar.js la deja con la misma forma que una fila
// de la planilla) y se puede vigilar, pasar a «Testiando» (tablero y Fabrica) y pedirle sus ebooks. NO va a la planilla (este sitio no escribe ahi) y todavia no se sigue dia a dia.
//
// Reglas:
//   - Una clave de KV por oferta (`radar:manual:<id>`), sin indice compartido: dos personas que agregan a la vez no se pisan. El id es `m` + 8 letras al azar (las de la planilla empiezan con a o h).
//   - Los enlaces los clasifica `clasificarEnlace`: el de un anuncio o de un anunciante de la Biblioteca se RECONSTRUYE, cualquier otro tiene que ser http(s) sin credenciales ni red privada.
//   - Lo escrito es del equipo, pero igual se limpia y se acota aqui; quien lo pinta lo escapa. Hasta MAX_OFERTAS: sirve mas que de sobra y mantiene la lectura (1 listado + 1 lectura por oferta) bajo las
//     50 subpeticiones que permite una Function en el plan gratuito.
//   - Agregar y corregir lo hace todo el equipo (como pedir ebooks); quitar es solo del dueño.
//   - La lista se guarda 30 s en la copia del borde (`caches.default`) para no gastar un listado de KV (1.000 al dia) en cada pagina abierta; agregar, corregir y quitar la borran.

//   - El radar del VPS, con su llave (cabecera x-clave-radar, la misma CLAVE_RADAR de /api/radar-estado), puede LEER la lista (GET) para fotografiar cada dia las que estan en «Vigilar» o «Testiando»
//     (`radar.py --seguimiento`): recibe id, nombre, el estado que el dueño le puso y los enlaces ya clasificados (no la descripcion). Solo lee: no agrega, no corrige y no quita. Sin CLAVE_RADAR contesta 503.

import { exigirAlguno, safeEqual, EQUIPO } from '../_sesion.js';
import { limpio, clasificarEnlace, ofertaManual, MANUAL_MAX_ENLACES, MANUAL_MIN_QUE_ES, ESTADOS } from '../_radar.js';

const PREFIJO = 'radar:manual:';
const PREFIJO_ESTADO = 'radar:estado:';
const ID = /^m[a-z0-9]{8}$/;
const MAX_OFERTAS = 40;
const CLAVE_COPIA = 'https://radar-manual.invalid/lista';
const VIGENCIA_S = 30;

const json = (s, o) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
const error = (s, msg) => json(s, { ok: false, error: msg });

function idNuevo() {
  const abc = 'abcdefghijklmnopqrstuvwxyz0123456789', b = new Uint8Array(8);
  crypto.getRandomValues(b);
  return 'm' + [...b].map((x) => abc[x % abc.length]).join('');
}

const copia = () => (typeof caches !== 'undefined' && caches.default ? caches.default : null);
async function borrarCopia() { const c = copia(); if (c) { try { await c.delete(new Request(CLAVE_COPIA)); } catch { /* sin copia no pasa nada */ } } }

// Lo guardado, ya valido: {id, nombre, queEs, precio, enlaces:[{url}], por, creadoEn, editadoEn?}.
function guardadaValida(v, id) {
  return v && typeof v === 'object' && v.id === id && typeof v.nombre === 'string' && typeof v.queEs === 'string' && Array.isArray(v.enlaces) && typeof v.creadoEn === 'string';
}

async function listarIds(env) {
  const l = await env.SUITE.list({ prefix: PREFIJO, limit: 100 });
  return l.keys.map((k) => k.name.slice(PREFIJO.length)).filter((id) => ID.test(id));
}

async function leerTodas(env) {
  const ids = (await listarIds(env)).slice(0, MAX_OFERTAS + 5);
  const crudas = await Promise.all(ids.map((id) => env.SUITE.get(PREFIJO + id, { type: 'json' }).catch(() => null)));
  return crudas.map((v, i) => (guardadaValida(v, ids[i]) ? v : null)).filter(Boolean)
    .sort((a, b) => (a.creadoEn < b.creadoEn ? 1 : a.creadoEn > b.creadoEn ? -1 : 0));
}

// La llave del radar: en tiempo constante, y sin CLAVE_RADAR (o con una de menos de 16 caracteres) nadie entra.
// (Es la misma comprobacion de /api/radar-estado y de /api/radar-seguimiento; se repite aqui para no tocar esos archivos, que ya corren.)
function llaveDelRadar(request, env) {
  const llave = request.headers.get('x-clave-radar');
  if (!llave) return { presente: false };
  if (!env.CLAVE_RADAR || String(env.CLAVE_RADAR).length < 16) return { presente: true, respuesta: json(503, { ok: false, error: 'La llave del radar no esta configurada' }) };
  if (!safeEqual(llave, String(env.CLAVE_RADAR))) return { presente: true, respuesta: json(401, { ok: false, error: 'Llave no valida' }) };
  return { presente: true };
}

// El estado que el dueño le puso a cada oferta a mano en el dash (viaja en la METADATA de `radar:estado:<id>`: un solo listado, sin leer clave por clave). Sin estado guardado = «nuevo».
async function estadosManuales(env) {
  const l = await env.SUITE.list({ prefix: PREFIJO_ESTADO + 'm', limit: 1000 });
  const out = {};
  for (const k of l.keys) {
    const id = k.name.slice(PREFIJO_ESTADO.length), e = k.metadata && k.metadata.e;
    if (ID.test(id) && ESTADOS.includes(e)) out[id] = e;
  }
  return out;
}

// Lo que manda el navegador -> {nombre, queEs, precio, enlaces:[{url}]} o un texto de error para el equipo.
export function limpiarEntrada(b) {
  if (!b || typeof b !== 'object' || Array.isArray(b)) return { error: 'Falta lo que quieres agregar.' };
  const nombre = limpio(b.nombre, 80);
  if (nombre.length < 3) return { error: 'Ponle un nombre a la oferta (al menos 3 letras).' };
  const queEs = limpio(b.queEs, 500);
  if (queEs.length < MANUAL_MIN_QUE_ES) return { error: 'Cuéntanos de qué es la oferta, con el producto exacto (al menos ' + MANUAL_MIN_QUE_ES + ' letras): es lo que decide cuál se modela.' };
  const crudos = (Array.isArray(b.enlaces) ? b.enlaces : String(b.enlaces == null ? '' : b.enlaces).split(/[\s,]+/)).map((x) => String(x == null ? '' : x).trim()).filter(Boolean);
  if (!crudos.length) return { error: 'Pega al menos un enlace de la oferta (el anuncio, la página del anunciante, su página de ventas o un video).' };
  if (crudos.length > MANUAL_MAX_ENLACES) return { error: 'Pega hasta ' + MANUAL_MAX_ENLACES + ' enlaces por oferta.' };
  const enlaces = [], vistos = new Set();
  for (const c of crudos) {
    const e = clasificarEnlace(c);
    if (!e) return { error: 'Este enlace no sirve (tiene que ser una dirección http o https pública): ' + limpio(c, 60) };
    if (!vistos.has(e.url)) { vistos.add(e.url); enlaces.push({ url: e.url }); }
  }
  return { nombre, queEs, precio: limpio(b.precio, 40), enlaces };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return error(501, 'Falta el almacén KV');
  const metodo = request.method;

  // El radar del VPS, con su llave: SOLO lee (para vigilar las que estan en «Vigilar» o «Testiando»). Nada mas se le abre aqui.
  const radar = llaveDelRadar(request, env);
  if (radar.presente) {
    if (radar.respuesta) return radar.respuesta;
    if (metodo !== 'GET') return error(405, 'Con la llave del radar solo se lee (GET)');
    const [guardadas, estados] = await Promise.all([leerTodas(env), estadosManuales(env)]);
    return json(200, { ok: true, ofertas: guardadas.map((m) => {
      const o = ofertaManual(m);
      return { id: o.id, anunciante: o.anunciante, estado: estados[o.id] || 'nuevo', enlaces: o.enlaces.map((e) => ({ url: e.url, tipo: e.tipo })) };
    }) });
  }

  if (metodo === 'GET') {
    if (!(await exigirAlguno(request, env, EQUIPO))) return error(401, 'Entra al equipo para ver el radar');
    const c = copia();
    if (c) {
      try {
        const hit = await c.match(new Request(CLAVE_COPIA));
        if (hit) { const d = await hit.json(); if (Date.now() - Date.parse(d.leidoEn) < VIGENCIA_S * 1000) return json(200, d); }
      } catch { /* una copia ilegible se ignora */ }
    }
    const datos = { ok: true, ofertas: (await leerTodas(env)).map(ofertaManual), leidoEn: new Date().toISOString() };
    if (c) { try { await c.put(new Request(CLAVE_COPIA), new Response(JSON.stringify(datos), { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'max-age=' + VIGENCIA_S } })); } catch { /* sin copia solo se pierde la rapidez */ } }
    return json(200, datos);
  }

  if (metodo === 'POST') {
    const rol = await exigirAlguno(request, env, EQUIPO);
    if (!rol) return error(401, 'Entra al equipo para agregar una oferta');
    let b;
    try { b = await request.json(); } catch { return error(400, 'JSON inválido'); }
    const e = limpiarEntrada(b);
    if (e.error) return error(400, e.error);
    const ahora = new Date().toISOString();
    let id = b && b.id != null ? String(b.id) : '', previa = null;
    if (id) {
      if (!ID.test(id)) return error(400, 'Id no válido');
      previa = await env.SUITE.get(PREFIJO + id, { type: 'json' });
      if (!guardadaValida(previa, id)) return error(404, 'Esa oferta ya no existe (quizá la quitaron).');
    } else {
      if ((await listarIds(env)).length >= MAX_OFERTAS) return error(409, 'Ya hay ' + MAX_OFERTAS + ' ofertas agregadas a mano: pídele al dueño que quite alguna.');
      id = idNuevo();
    }
    const guardada = previa
      ? { ...previa, nombre: e.nombre, queEs: e.queEs, precio: e.precio, enlaces: e.enlaces, editadoEn: ahora, editadoPor: rol }
      : { id, nombre: e.nombre, queEs: e.queEs, precio: e.precio, enlaces: e.enlaces, por: rol, creadoEn: ahora };
    await env.SUITE.put(PREFIJO + id, JSON.stringify(guardada));
    await borrarCopia();
    return json(200, { ok: true, oferta: ofertaManual(guardada) });
  }

  if (metodo === 'DELETE') {
    if (!(await exigirAlguno(request, env, ['owner']))) return error(403, 'Solo el dueño puede quitar una oferta agregada a mano');
    const id = new URL(request.url).searchParams.get('id') || '';
    if (!ID.test(id)) return error(400, 'Id no válido');
    await env.SUITE.delete(PREFIJO + id);
    await env.SUITE.delete(PREFIJO_ESTADO + id);      // el estado que le hubieran puesto no queda huerfano
    await borrarCopia();
    return json(200, { ok: true });
  }

  return error(405, 'Method not allowed');
}
