// El estado de cada oferta del radar, elegido desde el dash (nuevo, vigilar, testiando, ganadora, no testeada, descartada).
//
//   GET    /api/radar-estado                          -> { ok, estados: { <idOferta>: { estado, desde, t, motivo? } }, sync }   todo el equipo (desde 2026-10-15)
//   POST   /api/radar-estado { id, estado, desde, fila, anunciante, oferta?, motivo? }  -> { ok, id, estado: {...} | null, tablero? }   solo el dueño
//   DELETE /api/radar-estado?id=<idOferta>            -> { ok }                                                       solo el dueño
//
//   Con la cabecera x-clave-radar (la llave CLAVE_RADAR; la usa el radar del VPS, sin sesion):
//   GET    /api/radar-estado                          -> { ok, estados: { <id>: { estado, desde, t, fila, anun, motivo? } } }  solo lee
//   POST   /api/radar-estado { sync: { aplicados, ya, conflictos, sinFila, error } }  -> { ok, sync }                 el radar avisa como le fue
//
// La planilla RADAR OFERTAS sigue siendo la fuente: el radar escribe ahi y lee ahi lo que el dueño marca (sus `ganadora` y `descartada` suben y
// bajan palabras y nichos). Este sitio NO escribe en la planilla (solo tiene su copia publicada en solo lectura): lo que se elige en el dash vive
// ENCIMA de ella, y es el RADAR quien lo pasa a la planilla (scripts del radar: `radar.py --sync-estados`, cada 5 minutos en el VPS, con su propio
// permiso de Google). Cada cambio guarda `estado` (lo elegido) y `desde` (el estado que tenia la planilla en ese momento): ni la pagina ni el radar
// aplican el cambio si la planilla ya dice otra cosa que `desde` (alguien la cambio a mano despues: gana la planilla). Elegir el mismo estado que
// tiene la planilla borra el cambio. `fila` y `anun` (el anunciante) son para que el radar encuentre la fila cuando la oferta no trae id de anuncio.
//
// «Testiando» (2026-10-15) es el sexto estado. Ademas de cambiar el estado, manda la oferta al tablero de Produccion, en la columna «Nueva oferta» (functions/_radar-tablero.js: una
// tarjeta por oferta, sin duplicar). Eso ocurre ANTES de guardar el estado: si el tablero falla se contesta el error y no se guarda nada a medias. `oferta` trae lo que el dueño ve
// en la ficha (anunciante, gancho, entregable, enlaces...): es texto de terceros y aqui se limpia; sin ella la tarjeta sale solo con el nombre. Mandarla al tablero es idempotente
// y se hace tambien cuando ya no hay nada que guardar (la planilla ya dice «testiando»): asi el dueño puede pulsar «Testiando» otra vez si el equipo borro la tarjeta.
// (2026-10-15 a 2026-10-16 fue un estado SOLO DEL DASH, porque el radar del VPS conocia cinco; desde el 2026-10-16 el radar tambien lo conoce y lo escribe en la planilla.)
//
// «Motivo» (2026-11-04, pedido del dueño: «cuando pongo descartada no hay un espacio para escribir por qué la estoy descartando»): el porqué de la decision, texto libre
// del dueño, opcional, una linea de hasta MOTIVO_MAX caracteres. Viaja en el POST (`motivo`) y sale en los dos GET dentro de cada estado, solo si hay uno (sin motivo el
// estado se ve igual que antes). El radar lo escribe en la columna «Motivo del estado» de OFERTAS (dash_estados.py) y se lo pasa a su IA para aprender.
//   - Un POST sin `motivo` conserva el que habia si el estado no cambia (pulsar «Testiando» otra vez no lo borra); si cambia, el motivo viejo no pasa al estado nuevo.
//   - Un POST con `motivo` y el MISMO estado que ya estaba guardado (o que ya dice la planilla) solo cambia el porqué: conserva el `desde` guardado (el radar ya paso
//     el estado a la planilla y la fila dice lo mismo) y NO vuelve a mandar la oferta al tablero de Produccion. `motivo: ''` lo borra.
//   - El estado que ya dice la planilla CON motivo se guarda (estado = desde): asi el dueño puede anotar el porqué de una oferta que ya estaba descartada. Sin motivo se
//     sigue quitando, como siempre.
//   - Va en la metadata de la clave junto al estado (para que la lista siga saliendo de un `list`, sin una lectura por oferta), recortado si hiciera falta para que la
//     metadata no pase de META_MAX bytes (KV corta en 1.024; solo pasa con un motivo lleno de emojis).
//
// La llave del radar SOLO lee los estados y deja anotado como le fue: no cambia ningun estado ni toca la planilla. Sin CLAVE_RADAR puesta contesta
// 503, nunca abre. Se crea con scripts\Setup-ClaveRadar.ps1 (la misma llave queda en Cloudflare y en el VPS).
//
// Sin indice compartido (un arreglo que se lee y se reescribe pierde una entrada si dos personas cambian a la vez): una clave por oferta,
// `radar:estado:<id>`, con el estado en la METADATA de la propia clave; la lista sale de un `list`. Cada cambio es UNA escritura y lo que no
// cambia no se escribe (KV gratis: 1.000 escrituras al dia). El aviso del radar (`radar:sync`) se escribe solo si cambia o pasan 30 minutos.

import { exigirAlguno, safeEqual, EQUIPO } from '../_sesion.js';
import { mandarAlTablero } from '../_radar-tablero.js';

const PREFIJO = 'radar:estado:';
const CLAVE_SYNC = 'radar:sync';
const SYNC_REFRESCO_MS = 30 * 60 * 1000;
// Los seis de la columna Status de la planilla, con las mismas palabras (tambien lo que `desde` puede decir que tenia la planilla).
export const ESTADOS = ['nuevo', 'vigilar', 'ganadora', 'descartada', 'no testeada', 'testiando'];
// El id de una oferta es 'a<id del anuncio>' o 'h<huella>' (ver functions/_radar.js); la misma forma que acepta radar-orden.
const ID_OFERTA = /^[A-Za-z0-9_-]{1,48}$/;

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});
const txt = (v, max) => String(v == null ? '' : v).replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
const entero = (v, min, max) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : min; };

export const MOTIVO_MAX = 300;           // el mismo tope que dash_estados.MOTIVO_MAX del radar
const META_MAX = 1000;                   // bytes de la metadata de una clave (KV corta en 1.024)
// El motivo en una linea, sin controles y con tope en caracteres de verdad (Array.from no parte un emoji en dos, como haria slice).
export function limpiarMotivo(v) {
  let s = String(v == null ? '' : v);
  if (s.toWellFormed) s = s.toWellFormed();
  return Array.from(s.replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').replace(/\s+/g, ' ').trim()).slice(0, MOTIVO_MAX).join('').trim();
}
const bytes = (s) => new TextEncoder().encode(s).length;
// La metadata de una clave: el estado y, si hay, el motivo, recortado hasta que todo quepa en META_MAX bytes.
export function metadataDe(n) {
  const meta = { e: n.estado, d: n.desde, t: n.t, f: n.fila, n: n.anun };
  if (!n.motivo) return meta;
  const letras = Array.from(n.motivo);
  meta.m = n.motivo;
  while (letras.length && bytes(JSON.stringify(meta)) > META_MAX) { letras.pop(); meta.m = letras.join('').trim(); }
  if (!meta.m) delete meta.m;
  return meta;
}

async function leer(env, id) {
  const v = await env.SUITE.get(PREFIJO + id, { type: 'json' });
  return v && ESTADOS.includes(v.estado) ? v : null;
}

async function listar(env, conFila) {
  const out = {};
  let cursor;
  for (let vueltas = 0; vueltas < 5; vueltas++) {
    const l = await env.SUITE.list({ prefix: PREFIJO, cursor, limit: 1000 });
    l.keys.forEach((k) => {
      const m = k.metadata;
      if (!m || !ESTADOS.includes(m.e)) return;
      // Las ofertas que el equipo agrega a mano (id «m…», functions/api/radar-manual.js) no estan en la planilla: al radar del VPS no se le ofrecen (no tendria fila donde escribirlas).
      if (conFila && k.name.slice(PREFIJO.length)[0] === 'm') return;
      const e = { estado: m.e, desde: ESTADOS.includes(m.d) ? m.d : '', t: String(m.t || '') };
      if (conFila) { e.fila = entero(m.f, 0, 1000000); e.anun = txt(m.n, 60); }
      const motivo = limpiarMotivo(m.m);
      if (motivo) e.motivo = motivo;
      out[k.name.slice(PREFIJO.length)] = e;
    });
    if (l.list_complete || !l.cursor) break;
    cursor = l.cursor;
  }
  return out;
}

// Como le fue al radar la ultima vez que paso estados a la planilla (lo pinta la ficha: "la planilla se actualiza sola" o "el radar no responde").
async function leerSync(env) {
  const v = await env.SUITE.get(CLAVE_SYNC, { type: 'json' });
  return v && typeof v === 'object' && v.t ? v : null;
}
async function guardarSync(env, s) {
  const nuevo = { t: new Date().toISOString(), aplicados: entero(s && s.aplicados, 0, 9999), ya: entero(s && s.ya, 0, 9999),
    conflictos: entero(s && s.conflictos, 0, 9999), sinFila: entero(s && s.sinFila, 0, 9999), error: txt(s && s.error, 160) };
  const previo = await leerSync(env);
  const igual = previo && ['aplicados', 'ya', 'conflictos', 'sinFila', 'error'].every((k) => previo[k] === nuevo[k]);
  if (igual && Date.now() - Date.parse(previo.t) < SYNC_REFRESCO_MS) return previo;
  await env.SUITE.put(CLAVE_SYNC, JSON.stringify(nuevo));
  return nuevo;
}

// La llave del radar: en tiempo constante, y sin CLAVE_RADAR (o con una de menos de 16 caracteres) nadie entra.
function llaveDelRadar(request, env) {
  const llave = request.headers.get('x-clave-radar');
  if (!llave) return { presente: false };
  if (!env.CLAVE_RADAR || String(env.CLAVE_RADAR).length < 16) return { presente: true, respuesta: json(503, { ok: false, error: 'La llave del radar no esta configurada' }) };
  if (!safeEqual(llave, String(env.CLAVE_RADAR))) return { presente: true, respuesta: json(401, { ok: false, error: 'Llave no valida' }) };
  return { presente: true };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  // El radar del VPS, con su llave: solo lee los estados y avisa como le fue. Nunca cambia un estado.
  const radar = llaveDelRadar(request, env);
  if (radar.presente) {
    if (radar.respuesta) return radar.respuesta;
    if (request.method === 'GET') return json(200, { ok: true, estados: await listar(env, true) });
    if (request.method === 'POST') {
      let b;
      try { b = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
      if (!b || typeof b.sync !== 'object' || b.sync === null) return json(400, { ok: false, error: 'Solo se acepta el aviso { sync }' });
      return json(200, { ok: true, sync: await guardarSync(env, b.sync) });
    }
    return json(405, { ok: false, error: 'Method not allowed' });
  }

  // Ver los estados lo hace todo el equipo (desde el 2026-10-15, decision del dueño); cualquier otro metodo pasa primero por la puerta de dueño o editor y despues, mas abajo, por la del dueño.
  const rol = await exigirAlguno(request, env, request.method === 'GET' ? EQUIPO : ['owner', 'editor']);
  if (!rol) return json(401, { ok: false, error: request.method === 'GET' ? 'Entra al equipo para ver el radar' : 'Entra como dueño o como editor' });

  if (request.method === 'GET') return json(200, { ok: true, estados: await listar(env, false), sync: await leerSync(env) });

  // Elegir el estado de una oferta es una decision del dueño: sus etiquetas son las que miden al radar, asi que el editor solo lo ve. Con el login
  // apagado todo el sitio esta abierto (igual que en los pedidos): no es un secreto ni mueve plata, solo cambia en que pestaña cae una oferta.
  if (request.method !== 'POST' && request.method !== 'DELETE') return json(405, { ok: false, error: 'Method not allowed' });
  if (!(await exigirAlguno(request, env, ['owner']))) return json(403, { ok: false, error: 'Solo el dueño puede cambiar el estado de una oferta' });

  if (request.method === 'DELETE') {
    const id = new URL(request.url).searchParams.get('id') || '';
    if (!ID_OFERTA.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    await env.SUITE.delete(PREFIJO + id);
    return json(200, { ok: true });
  }

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const id = String(body && body.id || '');
  if (!ID_OFERTA.test(id)) return json(400, { ok: false, error: 'Id no valido' });
  const estado = String(body.estado == null ? '' : body.estado);
  if (!ESTADOS.includes(estado)) return json(400, { ok: false, error: 'Estado no valido' });
  const desde = String(body.desde == null ? '' : body.desde);
  if (desde !== '' && !ESTADOS.includes(desde)) return json(400, { ok: false, error: 'El estado de la planilla no es valido' });

  // El motivo solo se toca si viene (ausente o null = se conserva el que habia, si el estado no cambia). Con el mismo estado de antes (o el que ya dice la planilla) y un
  // motivo, lo unico que cambia es el porque: ni el `desde` guardado ni el tablero.
  const motivoVino = body.motivo !== undefined && body.motivo !== null;
  const previo = await leer(env, id);
  const mismo = !!previo && previo.estado === estado;
  const soloMotivo = motivoVino && (mismo || estado === desde);

  // «Testiando» deja la oferta en el tablero de Produccion ANTES de guardar el estado (una sola tarjeta por oferta: repetir no la duplica). Si el tablero falla, no se guarda nada
  // a medias: el dueño ve el error y el estado sigue como estaba.
  let tablero = null;
  if (estado === 'testiando' && !soloMotivo) {
    const datosOferta = body.oferta && typeof body.oferta === 'object' && !Array.isArray(body.oferta) ? body.oferta : {};
    try { tablero = await mandarAlTablero(env, id, { ...datosOferta, anunciante: datosOferta.anunciante || body.anunciante }, entero(body.fila, 0, 1000000)); }
    catch { return json(502, { ok: false, error: 'No pude pasar la oferta al tablero de Producción, así que no cambié su estado. Intenta de nuevo.' }); }
  }
  const respuesta = (o) => json(200, tablero ? { ...o, tablero } : o);

  const motivo = motivoVino ? limpiarMotivo(body.motivo) : (mismo ? limpiarMotivo(previo.motivo) : '');
  const desdeFinal = motivoVino && mismo && (previo.desde === '' || ESTADOS.includes(previo.desde)) ? previo.desde : desde;
  const salida = (n) => { const o = { estado: n.estado, desde: n.desde, t: n.t }; if (n.motivo) o.motivo = n.motivo; return o; };

  // Lo mismo que dice la planilla, sin un porque, no es un cambio: se quita el que hubiera.
  if (estado === desdeFinal && !motivo) {
    await env.SUITE.delete(PREFIJO + id);
    return respuesta({ ok: true, id, estado: null });
  }
  if (mismo && previo.desde === desdeFinal && limpiarMotivo(previo.motivo) === motivo) return respuesta({ ok: true, id, estado: salida(previo) });
  const nuevo = { estado, desde: desdeFinal, t: new Date().toISOString(), fila: entero(body.fila, 0, 1000000), anun: txt(body.anunciante, 60), motivo };
  const metadata = metadataDe(nuevo);
  nuevo.motivo = metadata.m || '';           // lo que quepa: el valor y la metadata dicen lo mismo
  await env.SUITE.put(PREFIJO + id, JSON.stringify(nuevo), { metadata });
  return respuesta({ ok: true, id, estado: salida(nuevo) });
}
