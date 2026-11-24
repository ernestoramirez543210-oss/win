// Los pedidos de ebooks que salen de /radar: "haz los ebooks de ESTA oferta".
//
//   GET    /api/radar-orden            -> { ok, ordenes:[resumen] }      la lista (la pintan /radar y /pdf)         todo el equipo (desde 2026-10-15)
//   GET    /api/radar-orden?id=<id>    -> { ok, orden }                  el pedido completo (trae datos de pago)    todo el equipo (desde 2026-10-15: las herramientas las usa todos)
//   POST   /api/radar-orden {accion:'guardar', orden}                    crea o reemplaza el pedido de una oferta           todo el equipo
//   POST   /api/radar-orden {accion:'progreso', id, clave, ...}          el creador avisa en que va cada ebook
//   POST   /api/radar-orden {accion:'cancelar', id}
//   DELETE /api/radar-orden?id=<id>                                      solo el dueño
//
// Un pedido NO gasta plata: guarda lo que hace falta para producir (el brief con el principal y sus bonos, el avatar, los
// datos de pago, cuales ebooks entran). Quien gasta es el creador de ebooks (/pdf) cuando alguien pulsa «Producir», que
// repite el costo y pide confirmacion antes. Asi el pedido sirve igual si lo produce el dueño, el editor o, mas adelante,
// un servidor.
//
// Un pedido por oferta: la clave es el id de la oferta en la planilla. Pedir otra vez la misma oferta reemplaza el pedido
// anterior, salvo que se este produciendo (ahi hay que cancelarlo primero).
//
// KV no tiene compare-and-swap y el plan gratuito permite 1.000 escrituras al dia. Por eso NO hay un indice compartido (un arreglo
// que se lee, se cambia y se vuelve a escribir pierde una entrada si dos personas piden a la vez): la lista sale de un `list` de las
// claves `radar:orden:`, y el resumen de cada pedido viaja en la METADATA de su propia clave (<= 1.024 bytes), que se escribe junto
// con el pedido. Cada pedido y cada avance del creador son UNA escritura; lo que no cambia no se escribe.

import { exigirAlguno, exigirDueno, EQUIPO } from '../_sesion.js';

const PREFIJO = 'radar:orden:';
const LISTA_MAX = 200;

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export const ID_OFERTA = /^[A-Za-z0-9_-]{1,48}$/;
const ID_PROYECTO = /^[a-z0-9]{6,32}$/;
const CLAVE_ENTREGABLE = /^(principal|bono-[0-6])$/;
const ESTADOS_ORDEN = ['pedida', 'produciendo', 'lista', 'cancelada'];
const ESTADOS_ENTREGABLE = ['pendiente', 'produciendo', 'listo', 'error'];
const ETAPAS = ['', 'contenido', 'listo'];
const MAX_BRIEF = 30000;
const MAX_AVATAR = 10000;

// Texto de una sola linea, sin controles y con tope.
function txt(v, max) {
  const s = String(v == null ? '' : v).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim();
  return s.length > max ? s.slice(0, max) : s;
}
const entero = (v, min, max, def) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : def;
};
const dolares = (v) => {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? Math.min(Math.round(n * 10000) / 10000, 500) : 0;
};

// Copia de un JSON cualquiera con topes de profundidad, de largo y de ancho. El brief y el avatar los arma un modelo y el
// navegador los reenvia: se conserva su forma sin atarse a un esquema (cambia con el prompt), pero no puede crecer sin limite
// ni traer claves raras.
export function jsonLimpio(v, prof = 0) {
  if (prof > 6) return null;
  if (typeof v === 'string') return v.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').slice(0, 2500);
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'boolean') return v;
  if (Array.isArray(v)) return v.slice(0, 80).map((x) => jsonLimpio(x, prof + 1));
  if (v && typeof v === 'object') {
    const o = {};
    for (const k of Object.keys(v).slice(0, 50)) {
      if (!/^[A-Za-z][A-Za-z0-9_]{0,39}$/.test(k) || k === '__proto__' || k === 'constructor' || k === 'prototype') continue;
      o[k] = jsonLimpio(v[k], prof + 1);
    }
    return o;
  }
  return null;
}

function limpiarOferta(o) {
  o = o && typeof o === 'object' ? o : {};
  const url = (v, host) => {
    const s = txt(v, 300);
    try { const u = new URL(s); return (u.protocol === 'https:' || u.protocol === 'http:') && (!host || u.hostname === host) ? u.toString().slice(0, 300) : ''; } catch { return ''; }
  };
  return {
    anunciante: txt(o.anunciante, 90), nicho: txt(o.nicho, 160), nichoCorto: txt(o.nichoCorto, 40),
    entregable: txt(o.entregable, 400), angulo: txt(o.angulo, 500),
    linkAnuncio: url(o.linkAnuncio, 'www.facebook.com'), linkPagina: url(o.linkPagina),
    nota: Number.isFinite(Number(o.nota)) ? entero(o.nota, 0, 10, 0) : null,
    fecha: /^\d{4}-\d{2}-\d{2}$/.test(String(o.fecha || '')) ? o.fecha : '',
    status: txt(o.status, 24),
  };
}

function limpiarEntregables(lista) {
  const vistos = new Set();
  const out = [];
  for (const e of (Array.isArray(lista) ? lista : []).slice(0, 8)) {
    if (!e || !CLAVE_ENTREGABLE.test(String(e.clave || '')) || vistos.has(e.clave)) continue;
    vistos.add(e.clave);
    const estado = ESTADOS_ENTREGABLE.includes(e.estado) ? e.estado : 'pendiente';
    out.push({
      clave: e.clave, titulo: txt(e.titulo, 160), paginas: entero(e.paginas, 3, 120, 10),
      incluir: e.incluir !== false,
      estado, etapa: ETAPAS.includes(e.etapa) ? e.etapa : (estado === 'listo' ? 'listo' : ''),
      proyectoId: ID_PROYECTO.test(String(e.proyectoId || '')) ? e.proyectoId : '',
    });
  }
  return out;
}

// Un pedido nuevo (o reemplazado) a partir de lo que manda el navegador. Todo se re-limpia aqui: el navegador no es de fiar.
export function limpiarPedido(cuerpo, rol, ahora) {
  const o = cuerpo && typeof cuerpo === 'object' ? cuerpo : {};
  if (!ID_OFERTA.test(String(o.id || ''))) return { error: 'El pedido no trae el id de la oferta.' };
  const brief = jsonLimpio(o.brief);
  if (!brief || typeof brief !== 'object' || !txt(brief.titulo, 200)) return { error: 'El pedido necesita el brief del ebook principal.' };
  if (JSON.stringify(brief).length > MAX_BRIEF) return { error: 'El brief es demasiado grande.' };
  let avatar = o.avatar && typeof o.avatar === 'object' ? jsonLimpio(o.avatar) : null;
  if (avatar && JSON.stringify(avatar).length > MAX_AVATAR) avatar = null;
  const entregables = limpiarEntregables(o.entregables);
  if (!entregables.some((e) => e.clave === 'principal' && e.incluir)) return { error: 'El ebook principal tiene que entrar en el pedido.' };
  return {
    pedido: {
      v: 1, id: o.id, estado: 'pedida',
      oferta: limpiarOferta(o.oferta),
      pais: txt(o.pais, 40) || 'Colombia',
      brief, avatar,
      datosPago: String(o.datosPago == null ? '' : o.datosPago).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').slice(0, 600),
      entregables,
      costoEstimado: dolares(o.costoEstimado), costoReal: 0,
      pedidoPor: txt(rol, 20), pedidoEn: ahora, actualizadoEn: ahora, terminadoEn: '',
    },
  };
}

// El estado del pedido sale de sus ebooks: nunca se acepta el que mande el navegador.
export function estadoDelPedido(p) {
  if (p.estado === 'cancelada') return 'cancelada';
  const dentro = p.entregables.filter((e) => e.incluir);
  if (dentro.length && dentro.every((e) => e.estado === 'listo')) return 'lista';
  if (dentro.some((e) => e.estado === 'produciendo' || e.estado === 'listo' || e.etapa === 'contenido')) return 'produciendo';
  return 'pedida';
}

export function resumenDe(p) {
  const dentro = p.entregables.filter((e) => e.incluir);
  return {
    id: p.id, anunciante: p.oferta.anunciante, titulo: txt(p.brief && p.brief.titulo, 160), estado: p.estado, pais: p.pais,
    n: dentro.length, listos: dentro.filter((e) => e.estado === 'listo').length,
    costoEstimado: p.costoEstimado, costoReal: p.costoReal,
    pedidoPor: p.pedidoPor, pedidoEn: p.pedidoEn, actualizadoEn: p.actualizadoEn,
  };
}

// El resumen que viaja en la metadata de la clave: KV la limita a 1.024 bytes, asi que si algun dia los textos largos lo pasan se acortan.
export function metadataDe(p) {
  let r = resumenDe(p);
  if (JSON.stringify(r).length > 900) r = { ...r, titulo: String(r.titulo || '').slice(0, 60), anunciante: String(r.anunciante || '').slice(0, 40) };
  return r;
}
async function guardarPedido(env, p) {
  await env.SUITE.put(PREFIJO + p.id, JSON.stringify(p), { metadata: metadataDe(p) });
}
// Todos los pedidos, del mas reciente al mas viejo, con UN list (sin leer cada pedido: son varios KB de brief).
async function listarPedidos(env) {
  const out = [];
  let cursor;
  do {
    const l = await env.SUITE.list({ prefix: PREFIJO, cursor, limit: 100 });
    l.keys.forEach((k) => { if (k.metadata && k.metadata.id) out.push(k.metadata); });
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor && out.length < LISTA_MAX);
  return out.sort((a, b) => String(b.actualizadoEn || '').localeCompare(String(a.actualizadoEn || ''))).slice(0, LISTA_MAX);
}
async function leerPedido(env, id) {
  const raw = await env.SUITE.get(PREFIJO + id);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  const url = new URL(request.url);
  const id = url.searchParams.get('id') || '';
  // Todo el equipo usa los pedidos del radar (ver la lista, abrir un pedido completo —trae los datos de pago de la oferta—, crearlo, avanzarlo y cancelarlo) desde el 2026-10-15: decision del dueño,
  // «las herramientas las puedan usar todos sin problema». Borrar un pedido sigue siendo solo del dueño (mas abajo).
  const rol = await exigirAlguno(request, env, EQUIPO);
  if (!rol) return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });

  if (request.method === 'GET') {
    if (!id) return json(200, { ok: true, ordenes: await listarPedidos(env) });
    if (!ID_OFERTA.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    const p = await leerPedido(env, id);
    return p ? json(200, { ok: true, orden: p }) : json(404, { ok: false, error: 'Ese pedido no existe' });
  }

  if (request.method === 'DELETE') {
    if (!(await exigirDueno(request, env))) return json(403, { ok: false, error: 'Solo el dueño puede borrar pedidos' });
    if (!ID_OFERTA.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    await env.SUITE.delete(PREFIJO + id);
    return json(200, { ok: true });
  }

  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });
  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const ahora = new Date().toISOString();

  if (body.accion === 'guardar') {
    const r = limpiarPedido(body.orden, rol, ahora);
    if (r.error) return json(400, { ok: false, error: r.error });
    const previo = await leerPedido(env, r.pedido.id);
    if (previo && previo.estado === 'produciendo') {
      return json(409, { ok: false, error: 'Ese pedido se está produciendo: cancélalo antes de pedirlo otra vez.' });
    }
    await guardarPedido(env, r.pedido);
    return json(200, { ok: true, orden: r.pedido });
  }

  if (!ID_OFERTA.test(String(body.id || ''))) return json(400, { ok: false, error: 'Falta el id del pedido' });
  const p = await leerPedido(env, body.id);
  if (!p) return json(404, { ok: false, error: 'Ese pedido no existe' });

  if (body.accion === 'cancelar') {
    if (p.estado === 'cancelada') return json(200, { ok: true, orden: p });
    p.estado = 'cancelada'; p.actualizadoEn = ahora;
    await guardarPedido(env, p);
    return json(200, { ok: true, orden: p });
  }

  if (body.accion === 'progreso') {
    if (p.estado === 'cancelada') return json(409, { ok: false, error: 'Ese pedido está cancelado' });
    const e = p.entregables.find((x) => x.clave === body.clave);
    if (!e) return json(400, { ok: false, error: 'Ese ebook no está en el pedido' });
    const antes = JSON.stringify([e.estado, e.etapa, e.proyectoId, p.estado, p.costoReal]);
    if (ESTADOS_ENTREGABLE.includes(body.estado)) e.estado = body.estado;
    if (ETAPAS.includes(body.etapa)) e.etapa = body.etapa;
    if (e.estado === 'listo') e.etapa = 'listo';
    if (ID_PROYECTO.test(String(body.proyectoId || ''))) e.proyectoId = body.proyectoId;
    if (body.titulo) e.titulo = txt(body.titulo, 160) || e.titulo;
    if (body.costo != null) p.costoReal = Math.min(500, Math.round((p.costoReal + dolares(body.costo)) * 10000) / 10000);
    p.estado = estadoDelPedido(p);
    if (p.estado === 'lista' && !p.terminadoEn) p.terminadoEn = ahora;
    if (antes === JSON.stringify([e.estado, e.etapa, e.proyectoId, p.estado, p.costoReal])) return json(200, { ok: true, orden: p });
    p.actualizadoEn = ahora;
    await guardarPedido(env, p);
    return json(200, { ok: true, orden: p });
  }

  return json(400, { ok: false, error: 'Acción desconocida' });
}
