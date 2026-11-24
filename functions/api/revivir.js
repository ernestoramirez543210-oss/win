// «Revivir ofertas» (pestaña Variaciones de /tablero, 2026-11-11). Pedido del dueño: «la oferta de sermones funcionó bien en Colombia; la de kinesiotaping en su momento también, pero
// actualmente no se están vendiendo… quiero una sección que coja esas ofertas, las analice y busque en Facebook ofertas que estén vendiendo eso, con otro ángulo, en portugués, en español
// o en cualquier otro idioma, para hacerle variaciones nosotros y ver si revivimos esas ofertas».
//
// Que ofertas se apagaron lo calcula la pagina con las ventas (templates/revivir-ofertas.js). Aqui vive lo demas: el PEDIDO de busqueda y lo que el radar del VPS encontro. El dash no
// puede buscar solo (Cloudflare no abre la Biblioteca de anuncios ni tiene la llave de Gemini): deja el pedido y el radar (revivir.py, cada 10 min) lo atiende.
//
//   GET  /api/revivir                                   equipo -> { ok, ahora, ofertas:[...] }   (los pedidos con sus resultados, y las ofertas que el equipo oculto)
//   POST /api/revivir { accion:'pedir', producto, descripcion, paises, origen }  equipo -> { ok, ahora, oferta }
//   POST /api/revivir { accion:'ocultar', id, producto, oculta }                 equipo -> { ok, oferta }   (sacarla de la lista de sugeridas, o volverla a mostrar)
//   POST /api/revivir { accion:'quitar', id }                                     DUEÑO  -> { ok }           (borra el pedido y lo hallado)
//   GET  /api/revivir?pendientes=1          (x-clave-radar)                       radar  -> { ok, pedidos:[{ id, producto, descripcion, paises }] }
//   POST /api/revivir { id, estado, mensaje, consultas, resultados, ideas } (x-clave-radar) radar -> { ok }
//
// `ideas` (2026-11-11, segundo pedido del dueño: «una IA que nos sugiera o busque ángulos que estén funcionando en otras industrias u ofertas y que podamos aplicar en la oferta…
// cómo volver una oferta que ya vendió en dinamita pura»): angulos PRESTADOS de otros nichos, adaptados a nuestro producto por Gemini en el mismo pase del radar, con la industria de
// donde vienen, un gancho de ejemplo, como aplicarlo y que palanca mueve (angulo, oferta, formato, publico o precio). Es texto de Gemini armado con texto de terceros: se acota igual.
//
// Una clave de KV por producto (`revivir:o:<id>`, 120 dias) y, mientras esta pendiente, una marca aparte (`revivir:p:<id>`, 1 hora). Para no gastar un listado de KV (1.000 al dia en toda la
// suite) cada vez que el radar pregunta, pedir deja ademas `revivir:hay` con la hora: el radar la LEE y solo se lista si es de los ultimos 30 min (igual que /api/radar-flujo-buscar).
// La lista del equipo se guarda 30 s en la copia del borde (como /api/radar-manual); cualquier escritura la borra.
//
// Lo que manda el radar es TEXTO DE TERCEROS (nombres de anunciantes, copy de anuncios, y lo que Gemini resumio de eso): cada campo se acota, los ids son solo digitos y los enlaces se
// RECONSTRUYEN aqui con esos digitos; nada del texto llega a un href. La pagina, ademas, escapa todo lo que pinta.
import { exigirEquipo, exigirDueno, safeEqual } from '../_sesion.js';
import { limpio, idNumerico } from '../_radar.js';

const PREFIJO = 'revivir:o:';
const PREFIJO_PEND = 'revivir:p:';
const MARCA = 'revivir:hay';
const MARCA_VIVE_MIN = 60;      // el radar reintenta a los 20 min si Meta lo limita (revivir.py): la marca dura lo que dura un pedido en fila
const PEND_TTL_S = 3600;
const TTL_S = 120 * 24 * 3600;
const PENDIENTE_VENCE_MIN = 30;   // un pedido que el radar no atendio en 30 min se puede volver a pedir
const REPETIR_MIN = 2;            // lo recien resuelto no se vuelve a pedir por un doble clic
const MAX_OFERTAS = 40;           // 1 listado + 1 lectura por oferta: bajo las 50 subpeticiones de una Function gratuita
const MAX_PENDIENTES = 6;         // cada busqueda gasta peticiones a Meta (que limita la IP del radar) y centavos de Gemini
const POR_PASADA = 2;             // los que se lleva el radar en cada pasada
const MAX_CUERPO = 65536;
const MAX_RESULTADOS = 40;
const MAX_CONSULTAS = 16;
const MAX_IDEAS = 8;
const PALANCAS = ['angulo', 'oferta', 'formato', 'publico', 'precio'];
const ESTADOS_RADAR = ['listo', 'sin_resultados', 'error'];
const ORIGENES = ['detectada', 'manual'];
const PAISES = ['colombia', 'mexico', 'venezuela', 'guatemala', 'peru', 'argentina', 'ecuador', 'bolivia', 'elsalvador', 'uruguay'];
const ROLES = ['owner', 'trafficker', 'editor', 'videos'];
const VIGENCIA_S = 30;
const CLAVE_COPIA = 'https://revivir.invalid/lista';
const ID = /^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$/;

const json = (s, o) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
const error = (s, msg) => json(s, { ok: false, error: msg });
const minutosDesde = (iso) => { const t = Date.parse(iso); return Number.isFinite(t) ? (Date.now() - t) / 60000 : Infinity; };
const entero = (v, max) => { const n = Math.round(Number(v)); return Number.isFinite(n) && n > 0 ? Math.min(n, max) : 0; };
const copia = () => (typeof caches !== 'undefined' && caches.default ? caches.default : null);
async function borrarCopia() { const c = copia(); if (c) { try { await c.delete(new Request(CLAVE_COPIA)); } catch { /* sin copia no pasa nada */ } } }

// El MISMO id que calcula la pagina (RevivirOfertas.idDe en templates/revivir-ofertas.js): scripts/probar-revivir.cjs comprueba que los dos digan lo mismo.
export function idDe(nombre) {
  return String(nombre == null ? '' : nombre).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60).replace(/-+$/, '');
}

// El enlace de un anuncio y el de la pagina del anunciante en la Biblioteca, armados SOLO con digitos (y un pais de dos letras).
// Una descripcion entera hasta `max` (2026-11-18, Beto: «que copie la frase entera y no quede cortada a mitad de palabra»; antes `limpio` cortaba en 240 a mitad de palabra).
// Si es mas larga, se corta en la ultima frase completa (o, si no hay, en el ultimo espacio, con «…»): nunca a mitad de palabra. 300 = el tope de la nota en /api/tablero.
export function frase(v, max) {
  const s = limpio(v, 4000);
  if (s.length <= max) return s;
  const corte = s.slice(0, max), fin = Math.max(corte.lastIndexOf('. '), corte.lastIndexOf('; '), corte.lastIndexOf('! '), corte.lastIndexOf('? '));
  if (fin >= max * 0.5) return corte.slice(0, fin).trim() + (corte[fin] === ';' ? '.' : corte[fin]);
  const esp = corte.slice(0, max - 1).lastIndexOf(' ');
  return (esp > 0 ? corte.slice(0, esp) : corte.slice(0, max - 1)).replace(/[\s,;:.-]+$/, '') + '…';
}
export function enlaceAnuncio(ad) { const id = idNumerico(ad); return id ? `https://www.facebook.com/ads/library/?id=${id}` : ''; }
export function enlacePagina(pagina, pais) {
  const id = idNumerico(pagina);
  if (!id) return '';
  const p = /^[A-Z]{2}$/.test(String(pais || '')) ? pais : 'ALL';
  return `https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=${p}&search_type=page&view_all_page_id=${id}`;
}

// Un anuncio hallado por el radar, ya acotado. Sin id de anuncio valido no entra.
export function resultadoLimpio(r) {
  if (!r || typeof r !== 'object') return null;
  const ad = idNumerico(r.ad);
  if (!ad) return null;
  const pais = /^[A-Z]{2}$/.test(String(r.pais || '')) ? r.pais : '';
  return {
    ad, pagina: idNumerico(r.pagina), anunciante: limpio(r.anunciante, 80), pais,
    idioma: /^[a-z]{2}$/.test(String(r.idioma || '')) ? r.idioma : '',
    dias: entero(r.dias, 3650), activos: entero(r.activos, 9999),
    formato: r.formato === 'video' || r.formato === 'imagen' ? r.formato : '',
    angulo: limpio(r.angulo, 60), gancho: limpio(r.gancho, 200), porque: frase(r.porque, 160),
    parecido: r.parecido === 'parecido' ? 'parecido' : 'mismo', consulta: limpio(r.consulta, 60), texto: limpio(r.texto, 280),
  };
}
export function resultadosLimpios(v) {
  if (!Array.isArray(v)) return [];
  const vistos = new Set(), out = [];
  for (const r of v.slice(0, MAX_RESULTADOS * 2)) {
    const x = resultadoLimpio(r);
    if (!x || vistos.has(x.ad)) continue;
    vistos.add(x.ad); out.push(x);
    if (out.length >= MAX_RESULTADOS) break;
  }
  return out;
}
export function consultasLimpias(v) {
  if (!Array.isArray(v)) return [];
  return v.slice(0, MAX_CONSULTAS).map((c) => (c && typeof c === 'object' ? {
    q: limpio(c.q, 60), pais: /^[A-Z]{2}$/.test(String(c.pais || '')) ? c.pais : '', idioma: /^[a-z]{2}$/.test(String(c.idioma || '')) ? c.idioma : '', n: entero(c.n, 999),
  } : null)).filter((c) => c && c.q);
}
export function ideasLimpias(v) {
  if (!Array.isArray(v)) return [];
  return v.slice(0, MAX_IDEAS * 4).map((x) => (x && typeof x === 'object' ? {
    angulo: limpio(x.angulo, 60), de: limpio(x.de, 80), gancho: limpio(x.gancho, 200), como: frase(x.como, 300),
    palanca: PALANCAS.includes(x.palanca) ? x.palanca : 'angulo',
  } : null)).filter((x) => x && x.angulo && (x.gancho || x.como)).slice(0, MAX_IDEAS);
}
const paisesLimpios = (v) => (Array.isArray(v) ? v : []).map((p) => String(p || '').toLowerCase()).filter((p, i, a) => PAISES.includes(p) && a.indexOf(p) === i);

// Lo que ve el equipo, con los enlaces ya reconstruidos.
export function ofertaPublica(v) {
  if (!v || typeof v !== 'object' || !ID.test(String(v.id || ''))) return null;
  const estado = v.estado === 'pendiente' || ESTADOS_RADAR.includes(v.estado) ? v.estado : '';
  return {
    id: v.id, producto: limpio(v.producto, 80), descripcion: limpio(v.descripcion, 300), paises: paisesLimpios(v.paises),
    origen: ORIGENES.includes(v.origen) ? v.origen : '', oculta: v.oculta === true, estado,
    pedidoEn: limpio(v.pedidoEn, 30), pedidoPor: ROLES.includes(v.pedidoPor) ? v.pedidoPor : '', tomadoEn: estado === 'pendiente' ? limpio(v.tomadoEn, 30) : '',
    resueltoEn: limpio(v.resueltoEn, 30), mensaje: limpio(v.mensaje, 200), consultas: consultasLimpias(v.consultas), ideas: ideasLimpias(v.ideas),
    resultados: resultadosLimpios(v.resultados).map((r) => ({ ...r, enlace: enlaceAnuncio(r.ad), enlacePagina: enlacePagina(r.pagina, r.pais) })),
  };
}

async function cuerpoJson(request) {
  const largo = Number(request.headers.get('content-length') || 0);
  if (largo > MAX_CUERPO) return null;
  try { const t = await request.text(); return t.length > MAX_CUERPO ? null : JSON.parse(t); } catch { return null; }
}

// La llave del radar: en tiempo constante, y sin CLAVE_RADAR (o con una de menos de 16 caracteres) nadie entra. (La misma comprobacion de /api/radar-flujo-buscar.)
function llaveDelRadar(request, env) {
  const llave = request.headers.get('x-clave-radar');
  if (!llave) return { presente: false };
  if (!env.CLAVE_RADAR || String(env.CLAVE_RADAR).length < 16) return { presente: true, respuesta: error(503, 'La llave del radar no esta configurada') };
  if (!safeEqual(llave, String(env.CLAVE_RADAR))) return { presente: true, respuesta: error(401, 'Llave no valida') };
  return { presente: true };
}

async function leerTodas(env) {
  const l = await env.SUITE.list({ prefix: PREFIJO, limit: 100 });
  const ids = l.keys.map((k) => k.name.slice(PREFIJO.length)).filter((id) => ID.test(id)).slice(0, MAX_OFERTAS + 5);
  const crudas = await Promise.all(ids.map((id) => env.SUITE.get(PREFIJO + id, { type: 'json' }).catch(() => null)));
  return crudas.map((v, i) => (v && v.id === ids[i] ? v : null)).filter(Boolean);
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return error(501, 'Falta el almacén KV');
  const metodo = request.method;
  const url = new URL(request.url);

  // ---- el radar del VPS ----
  const radar = llaveDelRadar(request, env);
  if (radar.presente) {
    if (radar.respuesta) return radar.respuesta;
    if (metodo === 'GET') {
      if (url.searchParams.get('pendientes') !== '1') return error(400, 'Solo pendientes');
      const marca = await env.SUITE.get(MARCA);
      if (!marca || minutosDesde(marca) > MARCA_VIVE_MIN) return json(200, { ok: true, pedidos: [] });
      const l = await env.SUITE.list({ prefix: PREFIJO_PEND, limit: 50 });
      const ids = l.keys.map((k) => k.name.slice(PREFIJO_PEND.length)).filter((id) => ID.test(id));
      const pedidos = [], tomado = new Date().toISOString();
      for (const id of ids) {
        if (pedidos.length >= POR_PASADA) break;
        const reg = await env.SUITE.get(PREFIJO + id, { type: 'json' });
        if (!reg || reg.estado !== 'pendiente') { await env.SUITE.delete(PREFIJO_PEND + id); continue; }
        // `tomadoEn`: la hora en que el radar se lo llevo; la pagina pasa de «esperando al radar» a «el radar está buscando».
        await env.SUITE.put(PREFIJO + id, JSON.stringify({ ...reg, tomadoEn: tomado }), { expirationTtl: TTL_S });
        pedidos.push({ id, producto: limpio(reg.producto, 80), descripcion: limpio(reg.descripcion, 300), paises: paisesLimpios(reg.paises) });
      }
      // Mientras el radar se lleve pedidos, la marca se renueva: si Meta lo limita antes de buscar, el pedido sigue pendiente y debe volver a salir en la proxima pasada.
      if (pedidos.length) { await env.SUITE.put(MARCA, tomado, { expirationTtl: PEND_TTL_S }); await borrarCopia(); }
      return json(200, { ok: true, pedidos });
    }
    if (metodo !== 'POST') return error(405, 'Method not allowed');
    const b = await cuerpoJson(request);
    if (!b || typeof b !== 'object') return error(400, 'JSON invalido o demasiado grande');
    const id = String(b.id || '');
    if (!ID.test(id)) return error(400, 'Id no valido');
    if (!ESTADOS_RADAR.includes(b.estado)) return error(400, 'Estado no valido');
    const previa = await env.SUITE.get(PREFIJO + id, { type: 'json' });
    if (!previa || previa.estado !== 'pendiente') return error(409, 'Ese pedido ya no esta pendiente');
    const resultados = b.estado === 'listo' ? resultadosLimpios(b.resultados) : [];
    // «listo» sin ningun anuncio valido es «sin_resultados»: la pagina no debe prometer anuncios que no llegaron.
    const estado = b.estado === 'listo' && !resultados.length ? 'sin_resultados' : b.estado;
    // Las ideas no dependen de hallar competidores: llegan tambien con «sin_resultados». Si esta vez no llego ninguna (Gemini sin presupuesto), se quedan las de antes.
    const ideas = estado === 'error' ? [] : ideasLimpias(b.ideas);
    const nuevo = { ...previa, estado, mensaje: limpio(b.mensaje, 200), consultas: consultasLimpias(b.consultas), resultados: estado === 'listo' ? resultados : (previa.resultados || []),
      ideas: ideas.length ? ideas : (previa.ideas || []), resueltoEn: new Date().toISOString() };
    if (estado !== 'listo') nuevo.resultadosViejos = !!(previa.resultados && previa.resultados.length);
    else delete nuevo.resultadosViejos;
    delete nuevo.tomadoEn;
    await env.SUITE.put(PREFIJO + id, JSON.stringify(nuevo), { expirationTtl: TTL_S });
    await env.SUITE.delete(PREFIJO_PEND + id);
    await borrarCopia();
    return json(200, { ok: true, estado });
  }

  // ---- el equipo ----
  const rol = await exigirEquipo(request, env);
  if (!rol) return error(401, 'Entra al equipo para revivir ofertas');

  if (metodo === 'GET') {
    const c = copia();
    if (c) {
      try {
        const hit = await c.match(new Request(CLAVE_COPIA));
        if (hit) { const datos = await hit.json(); return json(200, { ...datos, ahora: new Date().toISOString() }); }
      } catch { /* sin copia se lee KV */ }
    }
    const ofertas = (await leerTodas(env)).map(ofertaPublica).filter(Boolean);
    const datos = { ok: true, ofertas };
    if (c) { try { await c.put(new Request(CLAVE_COPIA), new Response(JSON.stringify(datos), { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'max-age=' + VIGENCIA_S } })); } catch { /* sin copia solo se pierde la rapidez */ } }
    return json(200, { ...datos, ahora: new Date().toISOString() });
  }
  if (metodo !== 'POST') return error(405, 'Method not allowed');
  const b = await cuerpoJson(request);
  if (!b || typeof b !== 'object') return error(400, 'JSON invalido');

  if (b.accion === 'pedir') {
    const producto = limpio(b.producto, 80);
    const id = idDe(producto);
    if (!producto || !ID.test(id)) return error(400, 'Escribe el nombre de la oferta.');
    const descripcion = limpio(b.descripcion, 300);
    const previa = await env.SUITE.get(PREFIJO + id, { type: 'json' });
    if (previa && typeof previa === 'object') {
      const viva = previa.estado === 'pendiente' && minutosDesde(previa.pedidoEn) < PENDIENTE_VENCE_MIN;
      const reciente = ESTADOS_RADAR.includes(previa.estado) && minutosDesde(previa.resueltoEn) < REPETIR_MIN;
      if (viva || reciente) return json(200, { ok: true, ahora: new Date().toISOString(), oferta: ofertaPublica(previa) });
    } else {
      const todas = await env.SUITE.list({ prefix: PREFIJO, limit: MAX_OFERTAS + 1 });
      if (todas.keys.length >= MAX_OFERTAS) return error(429, 'Ya hay ' + MAX_OFERTAS + ' ofertas guardadas aquí. Pídele al dueño que quite alguna.');
    }
    const vivos = await env.SUITE.list({ prefix: PREFIJO_PEND, limit: MAX_PENDIENTES + 1 });
    if (vivos.keys.length >= MAX_PENDIENTES) return error(429, 'Hay ' + MAX_PENDIENTES + ' búsquedas en fila. Espera a que el radar termine alguna (unos 10 a 20 minutos).');
    const ahora = new Date().toISOString();
    // Lo hallado antes se conserva mientras el radar busca de nuevo: el equipo no se queda sin nada que mirar.
    const pedido = {
      ...(previa && typeof previa === 'object' ? previa : {}), id, producto, descripcion, paises: paisesLimpios(b.paises),
      origen: ORIGENES.includes(b.origen) ? b.origen : 'manual', oculta: false, estado: 'pendiente', pedidoEn: ahora, pedidoPor: ROLES.includes(rol) ? rol : '', mensaje: '',
    };
    delete pedido.tomadoEn;
    await env.SUITE.put(PREFIJO + id, JSON.stringify(pedido), { expirationTtl: TTL_S });
    await env.SUITE.put(PREFIJO_PEND + id, '1', { expirationTtl: PEND_TTL_S });
    await env.SUITE.put(MARCA, ahora, { expirationTtl: PEND_TTL_S });
    await borrarCopia();
    return json(200, { ok: true, ahora, oferta: ofertaPublica(pedido) });
  }

  if (b.accion === 'ocultar') {
    const id = String(b.id || '');
    if (!ID.test(id)) return error(400, 'Id no valido');
    const previa = await env.SUITE.get(PREFIJO + id, { type: 'json' });
    if (!previa) {
      if (b.oculta === false) return json(200, { ok: true, oferta: null });
      const todas = await env.SUITE.list({ prefix: PREFIJO, limit: MAX_OFERTAS + 1 });
      if (todas.keys.length >= MAX_OFERTAS) return error(429, 'Ya hay ' + MAX_OFERTAS + ' ofertas guardadas aquí. Pídele al dueño que quite alguna.');
    }
    const producto = limpio((previa && previa.producto) || b.producto, 80);
    if (!producto || idDe(producto) !== id) return error(400, 'Falta el nombre de la oferta');
    const fila = { ...(previa || { id, producto, estado: '', origen: 'detectada' }), oculta: b.oculta !== false };
    await env.SUITE.put(PREFIJO + id, JSON.stringify(fila), { expirationTtl: TTL_S });
    await borrarCopia();
    return json(200, { ok: true, oferta: ofertaPublica(fila) });
  }

  if (b.accion === 'quitar') {
    if (!(await exigirDueno(request, env))) return error(403, 'Solo el dueño puede borrar lo hallado de una oferta.');
    const id = String(b.id || '');
    if (!ID.test(id)) return error(400, 'Id no valido');
    await env.SUITE.delete(PREFIJO + id);
    await env.SUITE.delete(PREFIJO_PEND + id);
    await borrarCopia();
    return json(200, { ok: true });
  }

  return error(400, 'Accion no reconocida');
}
