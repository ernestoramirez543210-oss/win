// Fabrica de ofertas: los proyectos (una oferta que va del tablero a ebooks, anuncios y flujos), las recetas de edicion y los pedidos de anuncio para Claudia.
//
//   GET    /api/fabrica                         -> { ok, rol, numeracion, proyectos:[resumen], recetas:{ base, presets, presetsBase }, esquema }     todo el equipo
//   GET    /api/fabrica?id=<proyecto>           -> { ok, proyecto, anuncios:[...], recetaFinal, recetaBase, guia }
//   GET    /api/fabrica?orden=<proyecto>&anuncio=AD1   -> { ok, markdown, datos }                           la orden de trabajo de un anuncio (la misma que baja Claudia)
//   GET    /api/fabrica?vista=<proyecto>&anuncio=AD1&tipo=imagen|video   -> los bytes de la vista previa del anuncio (JPEG/PNG o MP4, con Range)     todo el equipo
//   POST   /api/fabrica { accion:'crear', nombre, paises, origen, cantidad?, preset?, receta?, notas? }   -> { ok, proyecto, anuncios, ya? }
//   POST   /api/fabrica { accion:'editar', id, nombre?, paises?, notas?, receta? }
//   POST   /api/fabrica { accion:'material', id, material?, etapas? }         el navegador deja lo que va armando (copy, PDF, imagenes, mockup, audios, flujos)
//   POST   /api/fabrica { accion:'anuncio', id, anuncio, estado?, texto?, referencia?, receta? }      aprobar, cancelar, pedir cambios, cargar el video de referencia
//   POST   /api/fabrica { accion:'anuncio_nuevo', id }
//   POST   /api/fabrica { accion:'lanzamiento', id, pais, tarea, hecho } | { accion:'lanzamiento', id, pais, numero:{ id, wpp, nombre, tel4 }|null }   la guia de lanzamiento (F.GUIA_LANZAMIENTO)
//   POST   /api/fabrica { accion:'recetas', base?, presets? }                la receta base y los puntos de partida
//   DELETE /api/fabrica?id=<proyecto>                                        solo el dueño
//
// Quien llama: el equipo (dueño, trafficker, editor, editor de video), con sesion. Falla cerrado (exigirEquipo): sin sesion valida no abre, aunque el login este apagado,
// porque esta puerta lleva a la cola de trabajo de Claudia. Esta ruta NO gasta nada: producir ebooks, audios y mockups lo hace el navegador llamando a las herramientas que
// el equipo ya usa (cada una con su costo y su confirmacion), y los anuncios los edita Claudia en el PC del editor (/api/fabrica-editor).
//
// KV no tiene compare-and-swap, una lectura en otro punto de la red puede ir ~1 minuto atrasada, y el plan gratuito permite 1.000 escrituras Y 1.000 listados al dia (los
// comparte toda la suite). Por eso:
//   - cada cosa tiene UN solo escritor: `fab:p:<id>` (proyecto) y `fab:a:<proyecto>:<AD>` (pedido de anuncio: receta, referencia, correcciones, aprobar o cancelar) los escribe el
//     equipo; `fab:e:<proyecto>:<AD>` (estado, entrega y avisos de Claudia) la escribe SOLO ella. Quien lee junta las dos (F.anuncioCompleto): una lectura atrasada puede retrasar lo
//     que se ve, nunca borrar lo que decidio el otro (antes un aviso de Claudia podia deshacer una cancelacion recien hecha);
//   - el resumen de cada clave viaja en su METADATA y la lista sale de UN `list` por tipo, sin leer cada valor; el detalle de un proyecto no lista nada: lee las claves AD1..ADn
//     por su nombre (las lecturas por clave tienen otro cupo, de 100.000 al dia);
//   - lo que no cambia no se escribe;
//   - crear un proyecto con 3 anuncios son 4 escrituras; un ciclo completo son decenas (lo que mas gasta es el material: 2 por archivo; ver la skill mi-dashboard).
//
// `aprobacion` de la receta (si Claudia sigue sin esperar el OK del plan y cuanto puede gastar sin preguntar) la cambia SOLO el dueño: para cualquier otro rol se descarta.

import { exigirEquipo } from '../_sesion.js';
import * as F from '../_fabrica.js';
import { enlaceMeta } from '../_radar.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const txt = (v, max) => String(v == null ? '' : v).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);

// ---------- acceso a KV (compartido con la puerta de Claudia) ----------
export async function leerProyecto(env, id) {
  const v = await env.SUITE.get(F.PREFIJO_PROYECTO + id, { type: 'json' });
  return v && typeof v === 'object' && v.id === id ? v : null;
}
export async function guardarProyecto(env, p) {
  await env.SUITE.put(F.PREFIJO_PROYECTO + p.id, JSON.stringify(p), { metadata: F.metadataProyecto(p) });
}
// El registro del EQUIPO de un pedido de anuncio (sin lo que dijo Claudia).
export async function leerAnuncio(env, proyecto, id) {
  const v = await env.SUITE.get(F.PREFIJO_ANUNCIO + proyecto + ':' + id, { type: 'json' });
  return v && typeof v === 'object' && v.proyecto === proyecto && v.id === id ? v : null;
}
export async function guardarAnuncio(env, a) {
  await env.SUITE.put(F.PREFIJO_ANUNCIO + a.proyecto + ':' + a.id, JSON.stringify(a), { metadata: F.metadataAnuncio(a) });
}
// Lo que dijo CLAUDIA de ese pedido (la unica clave que ella escribe).
export async function leerEntrega(env, proyecto, id) {
  const v = await env.SUITE.get(F.PREFIJO_ENTREGA + proyecto + ':' + id, { type: 'json' });
  return v && typeof v === 'object' && v.proyecto === proyecto && v.id === id ? v : null;
}
export async function guardarEntrega(env, e) {
  await env.SUITE.put(F.PREFIJO_ENTREGA + e.proyecto + ':' + e.id, JSON.stringify(e), { metadata: F.metadataEntrega(e) });
}
// El pedido tal como vale ahora: { a: registro del equipo, e: registro de Claudia (o null), m: los dos juntos }.
export async function leerPedido(env, proyecto, id) {
  const [a, e] = await Promise.all([leerAnuncio(env, proyecto, id), leerEntrega(env, proyecto, id)]);
  return a ? { a, e, m: F.anuncioCompleto(a, e) } : null;
}
// La vista previa de un anuncio (la imagen o el video que sube el editor): UNA clave por anuncio y tipo, SIEMPRE bajo `fab:v:` (es la unica escritura que la puerta del editor hace fuera de su propio
// registro `fab:e:`; vive aqui, como guardarEntrega, para que la auditoria vea que la puerta no toca KV directo). El tope de subidas del dia se lleva en la metadata del propio archivo: una lectura y
// ninguna escritura de mas. Devuelve null si ya se paso el tope, o la metadata que quedo guardada.
export async function guardarVista(env, proyecto, anuncio, tipo, bytes, tipoReal, ahora) {
  const clave = F.PREFIJO_VISTA + proyecto + ':' + anuncio + ':' + tipo;
  const hoy = ahora.slice(0, 10);
  let n = 0;
  if (typeof env.SUITE.getWithMetadata === 'function') {
    const previa = await env.SUITE.getWithMetadata(clave, { type: 'stream' });
    if (previa && previa.metadata && previa.metadata.dia === hoy) n = Number(previa.metadata.n) || 0;
    try { if (previa && previa.value && typeof previa.value.cancel === 'function') await previa.value.cancel(); } catch { /* ya estaba cerrado */ }
  }
  if (n >= F.VISTAS_POR_DIA) return null;
  const metadata = { t: ahora, bytes: bytes.byteLength, ct: tipoReal, dia: hoy, n: n + 1 };
  await env.SUITE.put(clave, bytes, { metadata });
  return metadata;
}
// Todas las claves con un prefijo y su metadata (sin leer los valores). Tope de paginas para no girar sin fin.
export async function listarMeta(env, prefijo, tope = 400) {
  const out = [];
  let cursor;
  for (let v = 0; v < 8 && out.length < tope; v++) {
    const l = await env.SUITE.list({ prefix: prefijo, cursor, limit: 500 });
    l.keys.forEach((k) => { if (k.metadata) out.push({ clave: k.name, meta: k.metadata }); });
    if (l.list_complete) break;
    cursor = l.cursor;
  }
  return out;
}
export async function leerRecetas(env) {
  const v = await env.SUITE.get(F.CLAVE_RECETAS, { type: 'json' });
  const base = v && typeof v === 'object' ? F.limpiarReceta(v.base, true) : {};
  const presets = {}, presetsPor = {};
  if (v && v.presets && typeof v.presets === 'object') {
    Object.keys(v.presets).slice(0, 12).forEach((n) => {
      const nombre = txt(n, 40); if (!nombre) return;
      presets[nombre] = F.limpiarReceta(v.presets[n], true);
      const por = v.presetsPor && typeof v.presetsPor === 'object' && Object.prototype.hasOwnProperty.call(v.presetsPor, n) ? txt(v.presetsPor[n], 20) : '';
      if (por) presetsPor[nombre] = por;
    });
  }
  return { base, presets, presetsPor, tocadoPor: txt(v && v.tocadoPor, 20), tocadoEn: txt(v && v.tocadoEn, 30) };
}
// Los pedidos de un proyecto, ya juntos, SIN listar: se leen por su clave. Son lecturas por nombre (cupo de 100.000 al dia; las que no existen salen vacias), y asi no hace falta un contador en
// el proyecto: habria que reescribirlo cada vez que se pide un anuncio, y con una lectura atrasada se perderian actualizaciones. Los nombres son de dos familias: AD1..AD12 (los de antes) y
// 101..130 (los nuevos, como los numera el dueño en Meta).
export function idsDeAnuncios() {
  return F.IDS_ANTIGUOS.concat(F.IDS_NUMERADOS);
}
// `completa` = sin cortar nunca (30 lecturas): lo usan las ofertas NUMERADAS, cuyos numeros pueden empezar donde diga el dueño (el 101, el 115...) y un trozo vacio no quiere decir que no haya mas adelante
// (cortar ahi escondia el anuncio de la ficha y hacia que «Pedir otro anuncio» repitiera el numero y pisara el pedido). Lee una familia de nombres de a 6 y sigue mientras el ultimo trozo traiga algun anuncio: una oferta con 3 anuncios cuesta 12 lecturas por la familia que tiene y 6 por la que no, y un hueco (una
// escritura que fallo al crear) de hasta 5 nombres no esconde a los que vienen despues.
export async function leerFamilia(env, proyecto, ids, completa = false) {
  const out = [];
  for (let i = 0; i < ids.length; i += 6) {
    const filas = await Promise.all(ids.slice(i, i + 6).map((id) => leerAnuncio(env, proyecto, id)));
    filas.forEach((a) => { if (a) out.push(a); });
    if (!completa && !filas.some(Boolean)) break;
  }
  return out;
}
export async function anunciosDe(env, p) {
  const [viejos, nuevos] = await Promise.all([leerFamilia(env, p.id, F.IDS_ANTIGUOS), leerFamilia(env, p.id, F.IDS_NUMERADOS, proyectoNumerado(p))]);
  const anuncios = viejos.concat(nuevos);
  const entregas = await Promise.all(anuncios.map((a) => leerEntrega(env, p.id, a.id)));
  return anuncios.map((a, k) => F.anuncioCompleto(a, entregas[k]));
}
// ¿Las ofertas que se CREAN ahora nacen numeradas (101, 102...)? Lo dice el interruptor del codigo (F.NUMERAR_ANUNCIOS_NUEVOS); NUMERAR_ANUNCIOS en el entorno lo fuerza (1/true/si/on enciende, 0/false/no/off apaga; las pruebas, o frenarlo sin tocar el codigo).
export const numerarNuevos = (env) => {
  const crudo = env ? env.NUMERAR_ANUNCIOS : undefined;
  const forzado = crudo === undefined || crudo === null ? '' : String(crudo).trim().toLowerCase();
  if (['1', 'true', 'si', 'sí', 'on'].includes(forzado)) return true;
  if (['0', 'false', 'no', 'off'].includes(forzado)) return false;
  return F.NUMERAR_ANUNCIOS_NUEVOS === true;
};
// ¿Los anuncios nuevos de ESTE proyecto se llaman 101, 102...? Lo decide el propio proyecto (`numerado`), NO el interruptor: una oferta que ya tiene sus AD1.. (y sus propios numeros en Meta) no cambia de nombres
// a mitad de camino, y una que nacio numerada tampoco vuelve a AD si algun dia se apaga el interruptor.
export const proyectoNumerado = (p) => !!p && p.numerado === true;

// Cuenta por proyecto desde la metadata de los pedidos de anuncio (la del equipo y la de Claudia juntas).
function resumenAnuncios(metasA, metasE) {
  const entregas = {};
  metasE.forEach((m) => { if (m.meta && m.meta.pr) entregas[m.meta.pr + ':' + m.meta.id] = m.meta; });
  const por = {};
  metasA.forEach((m) => {
    const x = m.meta; if (!x || !x.pr) return;
    const r = por[x.pr] || (por[x.pr] = { total: 0, pedidos: 0, trabajando: 0, listos: 0, aprobados: 0, errores: 0 });
    const e = F.estadoDeMetas(x, entregas[x.pr + ':' + x.id]);
    if (e === 'cancelado') return;
    r.total++;
    if (e === 'pedido') r.pedidos++;
    else if (e === 'tomado' || e === 'en-edicion') r.trabajando++;
    else if (e === 'lista') r.listos++;
    else if (e === 'aprobada') r.aprobados++;
    else if (e === 'error') r.errores++;
  });
  return por;
}

// La receta de un proyecto: la de fabrica, la base del dueño y los cambios de esta oferta. `extra` = los de un anuncio.
export function recetaDeProyecto(recetas, proyecto, extra) {
  return F.recetaFinal(recetas.base, proyecto && proyecto.receta, extra);
}

// Sin la parte de `aprobacion` si quien guarda no es el dueño: conserva la que ya habia.
function sinAprobacionAjena(nueva, previa, dueno) {
  if (dueno) return nueva;
  const out = Object.assign({}, nueva);
  delete out.aprobacion;
  if (previa && previa.aprobacion) out.aprobacion = previa.aprobacion;
  return out;
}

// La vista previa de un anuncio (la imagen del plan o el video chico) tal como la subio el editor. Solo el equipo (ya paso por exigirEquipo). Se vuelve a mirar por los bytes que SEA lo que dice
// y se entrega con tipo fijo, sin que el navegador adivine (nosniff) y encerrada (sandbox): aunque alguien lograra guardar otra cosa ahi, no corre. Con Range, porque el celular pide el video por trozos.
async function servirVista(env, request, pr, ad, tipo) {
  if (!F.ID.test(pr) || !F.ID_ANUNCIO.test(ad) || !Object.prototype.hasOwnProperty.call(F.VISTAS, tipo)) return json(400, { ok: false, error: 'Vista no valida' });
  const bytes = await env.SUITE.get(F.PREFIJO_VISTA + pr + ':' + ad + ':' + tipo, { type: 'arrayBuffer' });
  if (!bytes) return json(404, { ok: false, error: 'Todavía no hay vista previa' });
  const ct = F.tipoDeVista(bytes);
  if (!F.VISTAS[tipo].tipos.includes(ct)) return json(404, { ok: false, error: 'Todavía no hay vista previa' });
  const base = { 'Content-Type': ct, 'Cache-Control': 'private, max-age=300', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox", 'Content-Disposition': 'inline', 'Accept-Ranges': 'bytes' };
  const total = bytes.byteLength;
  const rango = request.headers.get('Range');
  if (!rango) return new Response(bytes, { headers: Object.assign({}, base, { 'Content-Length': String(total) }) });
  const m = /^bytes=(\d*)-(\d*)$/.exec(rango.trim());
  let ini, fin;
  if (m && (m[1] !== '' || m[2] !== '')) {
    if (m[1] === '') { ini = Math.max(0, total - Number(m[2])); fin = total - 1; }                       // «los ultimos N bytes»
    else { ini = Number(m[1]); fin = m[2] === '' ? total - 1 : Math.min(Number(m[2]), total - 1); }
  }
  if (ini === undefined || ini >= total || ini > fin) return new Response(null, { status: 416, headers: Object.assign({}, base, { 'Content-Range': 'bytes */' + total }) });
  return new Response(bytes.slice(ini, fin + 1), { status: 206, headers: Object.assign({}, base, { 'Content-Range': 'bytes ' + ini + '-' + fin + '/' + total, 'Content-Length': String(fin - ini + 1) }) });
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  const rol = await exigirEquipo(request, env);
  if (!rol) return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  const dueno = rol === 'owner';
  const url = new URL(request.url);
  const id = url.searchParams.get('id') || '';
  const ahora = new Date().toISOString();

  // ---------------- lecturas ----------------
  if (request.method === 'GET') {
    if (url.searchParams.get('vista')) return servirVista(env, request, url.searchParams.get('vista'), url.searchParams.get('anuncio') || '', url.searchParams.get('tipo') || 'imagen');
    const ordenDe = url.searchParams.get('orden') || '';
    if (ordenDe) {
      const ad = url.searchParams.get('anuncio') || '';
      if (!F.ID.test(ordenDe) || !F.ID_ANUNCIO.test(ad)) return json(400, { ok: false, error: 'Proyecto o anuncio no validos' });
      const [p, par, recetas] = await Promise.all([leerProyecto(env, ordenDe), leerPedido(env, ordenDe, ad), leerRecetas(env)]);
      if (!p || !par) return json(404, { ok: false, error: 'Ese pedido no existe' });
      const r = recetaDeProyecto(recetas, p, par.m.receta);
      const o = F.ordenDeAnuncio(p, par.m, r, { origenUrl: url.origin });
      return json(200, { ok: true, markdown: o.markdown, datos: o.datos });
    }
    if (id) {
      if (!F.ID.test(id)) return json(400, { ok: false, error: 'Id no valido' });
      const [p, recetas] = await Promise.all([leerProyecto(env, id), leerRecetas(env)]);
      if (!p) return json(404, { ok: false, error: 'Ese proyecto no existe' });
      const anuncios = await anunciosDe(env, p);
      return json(200, { ok: true, rol, proyecto: p, anuncios, recetaBase: recetas.base, recetaFinal: recetaDeProyecto(recetas, p), guia: F.GUIA_LANZAMIENTO });
    }
    const [proys, anunsA, anunsE, recetas] = await Promise.all([listarMeta(env, F.PREFIJO_PROYECTO), listarMeta(env, F.PREFIJO_ANUNCIO), listarMeta(env, F.PREFIJO_ENTREGA), leerRecetas(env)]);
    const cuentas = resumenAnuncios(anunsA, anunsE);
    const proyectos = proys.map((m) => Object.assign({}, m.meta, { a: cuentas[m.meta.id] || { total: 0, pedidos: 0, trabajando: 0, listos: 0, aprobados: 0, errores: 0 } }))
      .sort((a, b) => String(b.t || '').localeCompare(String(a.t || '')));
    return json(200, { ok: true, rol, numeracion: numerarNuevos(env), proyectos, recetas: { base: recetas.base, presets: recetas.presets, presetsBase: F.PRESETS_FABRICA, tocadoPor: recetas.tocadoPor, tocadoEn: recetas.tocadoEn }, esquema: F.GRUPOS, defecto: F.RECETA_DEFECTO });
  }

  // ---------------- borrar ----------------
  if (request.method === 'DELETE') {
    if (!dueno) return json(403, { ok: false, error: 'Solo el dueño puede borrar un proyecto' });
    if (!F.ID.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    const ids = idsDeAnuncios();
    const hay = await Promise.all(ids.map((ad) => leerAnuncio(env, id, ad)));
    const existentes = ids.filter((ad, i) => hay[i]);
    await Promise.all(existentes.map((ad) => env.SUITE.delete(F.PREFIJO_ANUNCIO + id + ':' + ad)));
    await Promise.all(existentes.map((ad) => env.SUITE.delete(F.PREFIJO_ENTREGA + id + ':' + ad)));
    await Promise.all(existentes.flatMap((ad) => Object.keys(F.VISTAS).map((tipo) => env.SUITE.delete(F.PREFIJO_VISTA + id + ':' + ad + ':' + tipo))));       // y las vistas previas
    await env.SUITE.delete(F.PREFIJO_PROYECTO + id);
    return json(200, { ok: true, borrados: existentes.length + 1 });
  }

  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });
  let b;
  try { b = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const accion = b && b.accion;

  // ---------------- recetas ----------------
  if (accion === 'recetas') {
    const previo = await leerRecetas(env);
    const base = b.base !== undefined ? sinAprobacionAjena(F.limpiarReceta(b.base, dueno), previo.base, dueno) : previo.base;
    let presets = previo.presets, presetsPor = previo.presetsPor;
    const rechazados = [];
    if (b.presets !== undefined) {
      const entrada = b.presets && typeof b.presets === 'object' && !Array.isArray(b.presets) ? b.presets : {};
      const nombres = Object.keys(entrada).slice(0, 12).map((n) => txt(n, 40)).filter(Boolean);
      const hay = (o, n) => Object.prototype.hasOwnProperty.call(o, n);
      if (dueno) {
        // El dueño decide la lista completa. Lo que ya estaba igual conserva su autor; lo nuevo o cambiado queda como suyo.
        presets = {}; presetsPor = {};
        nombres.forEach((n) => {
          presets[n] = F.limpiarReceta(entrada[n], true);
          presetsPor[n] = hay(previo.presets, n) && JSON.stringify(previo.presets[n]) === JSON.stringify(presets[n]) && previo.presetsPor[n] ? previo.presetsPor[n] : 'owner';
        });
      } else {
        // Quien no es el dueño parte SIEMPRE de lo que ya hay: no quita nada, no pisa los del dueño (ni los que llevan libertad de gasto) y solo agrega o cambia los suyos mientras haya cupo.
        presets = Object.assign({}, previo.presets); presetsPor = Object.assign({}, previo.presetsPor);
        for (const n of nombres) {
          const hayPrevio = hay(previo.presets, n);
          if (hayPrevio && (previo.presetsPor[n] === 'owner' || (previo.presets[n] && previo.presets[n].aprobacion))) {
            // La pagina manda la lista completa: lo que viene igual que lo guardado no es un intento de cambiarlo (no se avisa); solo se rechaza lo que sí cambia.
            const sinLibertad = (r) => { const x = Object.assign({}, r); delete x.aprobacion; return JSON.stringify(x); };
            if (sinLibertad(F.limpiarReceta(entrada[n], false)) !== sinLibertad(previo.presets[n])) rechazados.push(n);
            continue;
          }
          if (!hayPrevio && Object.keys(presets).length >= 12) return json(400, { ok: false, error: 'Ya hay 12 puntos de partida: pídele al dueño que quite alguno.' });
          presets[n] = sinAprobacionAjena(F.limpiarReceta(entrada[n], false), previo.presets[n], false);
          presetsPor[n] = rol;
        }
      }
    }
    if (JSON.stringify([base, presets, presetsPor]) === JSON.stringify([previo.base, previo.presets, previo.presetsPor])) return json(200, { ok: true, recetas: { base, presets }, rechazados, igual: true });
    await env.SUITE.put(F.CLAVE_RECETAS, JSON.stringify({ v: 1, base, presets, presetsPor, tocadoPor: rol, tocadoEn: ahora }));
    return json(200, { ok: true, recetas: { base, presets }, rechazados });
  }

  // ---------------- crear ----------------
  if (accion === 'crear') {
    const origen = F.limpiarOrigen(b.origen);
    const pedido = F.ID.test(String(b.id || '')) ? b.id : '';
    const idP = origen.radarId || origen.tarjetaId || pedido || uid();
    const hay = await leerProyecto(env, idP);
    if (hay) return json(200, { ok: true, ya: true, proyecto: hay, anuncios: await anunciosDe(env, hay) });
    const recetas = await leerRecetas(env);
    const preset = txt(b.preset, 40);
    const dePreset = preset && (recetas.presets[preset] || F.PRESETS_FABRICA[preset]) ? F.limpiarReceta(recetas.presets[preset] || F.PRESETS_FABRICA[preset], dueno) : {};
    const propia = F.limpiarReceta(b.receta, dueno);
    const cantidad = F.limpiarReceta({ cantidad: b.cantidad }, dueno);
    const p = F.proyectoNuevo(Object.assign({}, b, { pedidoEbooks: origen.radarId || '' }), rol, ahora, idP);
    // La carpeta del producto en el editor no se comparte entre ofertas: si otra ya usa ese nombre, este lleva un trozo de su id.
    const usados = (await listarMeta(env, F.PREFIJO_PROYECTO)).map((m) => m.meta && m.meta.s).filter(Boolean);
    p.slug = F.slugUnico(p.nombre, idP, usados);
    p.receta = F.mezclarParciales(dePreset, propia, cantidad);
    const rf = recetaDeProyecto(recetas, p);
    const anuncios = [];
    if (numerarNuevos(env)) p.numerado = true;                        // la oferta nace numerada: sus anuncios son 101, 102... y los que se pidan despues tambien
    const inicio = p.numerado ? F.NUMERO_INICIAL : 1;                  // 101, 102... o AD1, AD2...
    for (let n = 0; n < rf.cantidad; n++) anuncios.push(F.anuncioNuevo(p, inicio + n, rf, ahora, rol));
    p.hechos[0].texto = 'Se creó el proyecto con ' + anuncios.length + ' anuncio' + (anuncios.length === 1 ? '' : 's') + ' pedido' + (anuncios.length === 1 ? '' : 's');
    // Si esta oferta se quito antes y un aviso tardio de Claudia dejo una entrega suelta con este id, no se hereda: se borra la que exista (y anuncioCompleto ignora las anteriores al pedido).
    const sueltas = await Promise.all(anuncios.map((a) => leerEntrega(env, idP, a.id)));
    await Promise.all(sueltas.map((e, i) => (e ? env.SUITE.delete(F.PREFIJO_ENTREGA + idP + ':' + anuncios[i].id) : null)));
    // El proyecto primero: si una escritura de anuncio falla, el proyecto existe y se pueden pedir los anuncios que falten.
    await guardarProyecto(env, p);
    await Promise.all(anuncios.map((a) => guardarAnuncio(env, a)));
    return json(200, { ok: true, proyecto: p, anuncios: anuncios.map((a) => F.anuncioCompleto(a, null)) });
  }

  // El resto trabaja sobre un proyecto que ya existe.
  const idP = F.ID.test(String(b.id || '')) ? b.id : '';
  if (!idP) return json(400, { ok: false, error: 'Falta el id del proyecto' });
  const p = await leerProyecto(env, idP);
  if (!p) return json(404, { ok: false, error: 'Ese proyecto no existe' });

  if (accion === 'editar') {
    // El nombre se puede cambiar; la carpeta del producto en el editor (`slug`) NO: Claudia ya puede estar trabajando en ella.
    const antes = JSON.stringify([p.nombre, p.paises, p.notas, p.receta, (p.origen || {}).linkAnuncio || '', p.numeroInicial || 0, p.numerado === true]);
    const enlaceAntes = (p.origen || {}).linkAnuncio || '';
    // Desde que numero se numeran los anuncios NUEVOS de esta oferta (101 por defecto). Sirve cuando la oferta ya usa esos numeros en Meta (FIELTRO ya usa el 101, el 102 y el 103): el proximo es el 104. Decirle un numero a una oferta de antes (con AD1..) la pasa a numerada.
    // Solo lo decide el dueño; se rechaza SIN cambiar nada lo que no sea un numero entre 101 y 130.
    if (b.numeroInicial !== undefined) {
      if (!dueno) return json(403, { ok: false, error: 'Solo el dueño cambia desde qué número se numeran los anuncios de una oferta.' });
      const n = Number(b.numeroInicial);
      const tope = F.NUMERO_INICIAL + F.MAX_NUMERADOS - 1;
      if (!Number.isInteger(n) || n < F.NUMERO_INICIAL || n > tope) return json(400, { ok: false, error: 'El primer número va de ' + F.NUMERO_INICIAL + ' a ' + tope + '.' });
      p.numerado = true;
      if (n === F.NUMERO_INICIAL) delete p.numeroInicial; else p.numeroInicial = n;
    }
    if (b.nombre !== undefined) p.nombre = txt(b.nombre, 100) || p.nombre;
    if (b.paises !== undefined) { const l = (Array.isArray(b.paises) ? b.paises : []).map((x) => txt(x, 30)).filter((x) => x && !F.NOMBRES_RESERVADOS.includes(x)); const u = l.filter((x, i) => l.indexOf(x) === i).slice(0, F.PAISES_MAX); if (u.length) p.paises = u; }
    if (b.notas !== undefined) p.notas = String(b.notas == null ? '' : b.notas).replace(/\r/g, '').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').trim().slice(0, 1000);
    if (b.receta !== undefined) p.receta = sinAprobacionAjena(F.limpiarReceta(b.receta, dueno), p.receta, dueno);
    // El enlace del anuncio original (o de la pagina de un anunciante) se puede pegar o cambiar cuando se quiera, por ejemplo para modelar a OTRO anunciante de la misma oferta: el servidor del dueno nota el
    // cambio y estudia otra vez. Vacio lo quita; algo que no es de la biblioteca de Meta se rechaza SIN cambiar nada (no se borra el que habia por un error al pegar).
    if (b.linkAnuncio !== undefined) {
      const crudo = String(b.linkAnuncio == null ? '' : b.linkAnuncio).trim().slice(0, 1000);
      const enlace = crudo ? enlaceMeta(crudo) : '';
      if (crudo && !enlace) return json(400, { ok: false, error: 'Ese no es un enlace de la biblioteca de anuncios de Meta. Pega el de un anuncio (facebook.com/ads/library/?id=…) o el de la página de un anunciante (el de «Ver todos los anuncios»).' });
      p.origen = Object.assign({}, p.origen || {}, { linkAnuncio: enlace });
    }
    if (JSON.stringify([p.nombre, p.paises, p.notas, p.receta, (p.origen || {}).linkAnuncio || '', p.numeroInicial || 0, p.numerado === true]) === antes) return json(200, { ok: true, proyecto: p, igual: true });
    const cambioEnlace = ((p.origen || {}).linkAnuncio || '') !== enlaceAntes;
    F.anotarHecho(p, rol, b.receta !== undefined ? 'Se cambió la receta de esta oferta' : (cambioEnlace && b.nombre === undefined && b.paises === undefined && b.notas === undefined ? 'Se cambió el enlace del anuncio original' : (b.numeroInicial !== undefined && b.nombre === undefined && b.paises === undefined && b.notas === undefined ? 'Los anuncios nuevos se numeran desde el ' + (p.numeroInicial || F.NUMERO_INICIAL) : 'Se editó el proyecto')), ahora);
    await guardarProyecto(env, p);
    return json(200, { ok: true, proyecto: p });
  }

  if (accion === 'material') {
    const antes = JSON.stringify([p.material, p.etapas]);
    if (b.material !== undefined) p.material = F.limpiarMaterial(b.material, p.material, url.origin);
    if (b.etapas && typeof b.etapas === 'object') {
      F.ETAPAS.forEach((e) => { if (b.etapas[e] && typeof b.etapas[e] === 'object') F.cambiarEtapa(p, e, b.etapas[e].estado, b.etapas[e].nota); });
    }
    // Una etapa no puede decir «lista» si lo que la hace lista no esta guardado: sin el PDF del ebook principal el material no esta listo, y sin el pedido de ZapData los flujos tampoco.
    const m = p.material || {};
    // (con los entregables ya hechos en una carpeta de Drive no hay PDF propio: los baja el servidor de anuncios)
    if (p.etapas.material.estado === 'lista' && !(m.entregablesDrive && m.entregablesDrive.id) && !(m.pdfs || []).some((x) => x.clave === 'principal')) F.cambiarEtapa(p, 'material', 'error', 'Falta el PDF del ebook principal');
    if (p.etapas.flujos.estado === 'lista' && !(m.flujo && m.flujo.pedidoId)) F.cambiarEtapa(p, 'flujos', 'error', 'No quedó guardado el pedido de los flujos');
    if (JSON.stringify([p.material, p.etapas]) === antes) return json(200, { ok: true, proyecto: p, igual: true });
    if (b.hecho) F.anotarHecho(p, rol, b.hecho, ahora); else p.actualizadoEn = ahora;
    await guardarProyecto(env, p);
    return json(200, { ok: true, proyecto: p });
  }

  if (accion === 'arrancar') {
    // La orden de arrancar los anuncios de la oferta (F.ordenDeArranque): una marca que lee el servidor del VPS (o Claudia) por la puerta del editor. No gasta nada: el gasto en IA lo sigue mandando
    // `aprobacion.topeUsd` de la receta (solo el dueño). `parar: true` la quita (frena lo que todavia nadie tomo; lo ya tomado sigue). Repetir la misma orden no cambia nada ni la fecha.
    const parar = b.parar === true;
    const ya = F.arranqueDe(p);
    if (parar ? !ya : !!ya) return json(200, { ok: true, proyecto: p, igual: true });
    if (!parar) {
      const ads = await anunciosDe(env, p);
      if (!ads.some((a) => F.ESTADOS_ABIERTOS.includes(a.estado))) return json(409, { ok: false, error: 'No hay anuncios pedidos que arrancar: pide uno primero (o vuelve a pedir uno cancelado).' });
    }
    F.ordenDeArranque(p, rol, ahora, parar);
    F.anotarHecho(p, rol, parar ? 'Se quitó la orden de arrancar los anuncios (lo que ya se tomó sigue)' : 'Se dio la orden de arrancar los anuncios', ahora);
    await guardarProyecto(env, p);
    return json(200, { ok: true, proyecto: p });
  }

  if (accion === 'lanzamiento') {
    // La guia de lanzamiento (F.GUIA_LANZAMIENTO): marcar o desmarcar una tarea a mano, o elegir el número de WhatsApp, de UN país de la oferta. Todo el equipo; no gasta ni toca ZapData ni Facebook.
    const pais = txt(b.pais, 30);
    if (F.NOMBRES_RESERVADOS.includes(pais) || !(p.paises || []).includes(pais)) return json(400, { ok: false, error: 'Ese país no es de esta oferta' });
    let cambio, hecho = '';
    if (b.numero !== undefined) {
      if (b.numero !== null && !F.limpiarNumero(b.numero)) return json(400, { ok: false, error: 'Ese número de WhatsApp no es válido' });
      cambio = F.elegirNumero(p, pais, b.numero, rol, ahora);
      const n = b.numero === null ? null : F.limpiarNumero(b.numero);
      if (cambio) hecho = n ? 'Número de WhatsApp para ' + pais + ': ' + (n.wpp ? 'WPP #' + n.wpp + ' · ' : '') + (n.nombre || 'conexión') + (n.tel4 ? ' …' + n.tel4 : '') : 'Se quitó el número de WhatsApp de ' + pais;
    } else {
      const tarea = txt(b.tarea, 30);
      if (!F.TAREAS_A_MANO.includes(tarea)) return json(400, { ok: false, error: 'Esa tarea no es de la guía de lanzamiento' });
      cambio = F.marcarLanzamiento(p, pais, tarea, b.hecho === true, rol, ahora);
    }
    if (!cambio) return json(200, { ok: true, proyecto: p, igual: true });
    if (hecho) F.anotarHecho(p, rol, hecho, ahora);
    await guardarProyecto(env, p);
    return json(200, { ok: true, proyecto: p });
  }

  if (accion === 'anuncio_nuevo') {
    // El siguiente es el primer nombre libre (se leen por nombre; el proyecto NO se reescribe, asi no se pisa nada que otro este guardando): el primer 101..130 que no existe (y no es menor que el
    // `numeroInicial` de la oferta) si la oferta es numerada (`numerado`), o el primer AD1..AD12 que no existe si es de antes.
    let n, tope;
    if (proyectoNumerado(p)) {
      const usados = new Set((await leerFamilia(env, idP, F.IDS_NUMERADOS, true)).map((x) => x.id));
      const piso = Math.max(F.NUMERO_INICIAL, Number(p.numeroInicial) || 0);
      const libre = F.IDS_NUMERADOS.find((x) => Number(x) >= piso && !usados.has(x));
      n = libre ? Number(libre) : -1; tope = F.MAX_NUMERADOS;
    } else {
      const existentes = await Promise.all(F.IDS_ANTIGUOS.map((ad) => leerAnuncio(env, idP, ad)));
      n = existentes.findIndex((x) => !x) + 1 || -1; tope = F.MAX_ANUNCIOS;
    }
    if (n < 0) return json(400, { ok: false, error: 'Ya hay ' + tope + ' anuncios en este proyecto.' });
    const recetas = await leerRecetas(env);
    const a = F.anuncioNuevo(p, n, recetaDeProyecto(recetas, p), ahora, rol);
    // Guarda: si el nombre elegido ya esta ocupado (un hueco que la busqueda no vio) se avisa y no se toca nada, ni el pedido ni lo que Claudia dijo de el. NO frena a dos personas que piden en el mismo segundo: KV no tiene comparar-y-escribir y las dos ven libre el mismo nombre (pasaba igual con AD1..AD12).
    if (await leerAnuncio(env, idP, a.id)) return json(409, { ok: false, error: 'Ese número de anuncio ya existe: abre la ficha otra vez y pide el anuncio de nuevo.' });
    const suelta = await leerEntrega(env, idP, a.id);          // una entrega suelta de un pedido que ya no existe no se hereda
    if (suelta) await env.SUITE.delete(F.PREFIJO_ENTREGA + idP + ':' + a.id);
    await guardarAnuncio(env, a);
    return json(200, { ok: true, anuncio: F.anuncioCompleto(a, null) });
  }

  if (accion === 'anuncio') {
    const ad = String(b.anuncio || '');
    if (!F.ID_ANUNCIO.test(ad)) return json(400, { ok: false, error: 'Anuncio no valido' });
    const par = await leerPedido(env, idP, ad);
    if (!par) return json(404, { ok: false, error: 'Ese anuncio no existe' });
    const a = par.a;                       // lo que se guarda: el registro del equipo
    const vigente = par.m.estado;          // lo que vale ahora (con lo que dijo Claudia)
    const antes = JSON.stringify([a.estado, a.cambiadoEn, a.referencia, a.receta, a.correcciones, a.aprobado]);
    const nota = [];
    if (b.referencia && typeof b.referencia === 'object') {
      if (b.referencia.videoUrl !== undefined) a.referencia.videoUrl = F.enlaceVideo(b.referencia.videoUrl);
      if (b.referencia.nota !== undefined) a.referencia.nota = String(b.referencia.nota || '').replace(/\r/g, '').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').trim().slice(0, 600);
      nota.push('Se cargó la referencia');
    }
    if (b.receta !== undefined) { a.receta = sinAprobacionAjena(F.limpiarReceta(b.receta, dueno), a.receta, dueno); if (a.receta.tipoAnuncio) a.tipo = a.receta.tipoAnuncio; nota.push('Se cambió la receta de este anuncio'); }
    if (b.estado !== undefined) {
      if (!['aprobada', 'cancelado', 'pedido'].includes(b.estado)) return json(400, { ok: false, error: 'Desde aquí solo se aprueba, se cancela o se piden cambios.' });
      if (b.estado === 'aprobada' && vigente !== 'lista' && vigente !== 'aprobada') return json(409, { ok: false, error: 'Solo se aprueba un anuncio que Claudia dejó listo.' });
      let texto = '';
      if (b.estado === 'pedido') {
        texto = txt(b.texto, 400);
        if (vigente === 'lista' || vigente === 'aprobada') {
          if (!texto) return json(400, { ok: false, error: 'Di qué hay que cambiar.' });
          nota.push('Se pidieron cambios');
        }
        if (texto) a.correcciones = (a.correcciones || []).concat([{ t: ahora, por: rol, texto }]).slice(-8);
      }
      if (b.estado === 'aprobada') nota.push('Se aprobó');
      if (b.estado === 'cancelado') nota.push('Se canceló');
      // Una persona movio el pedido: desde este momento vale su palabra hasta que Claudia avise de nuevo.
      if (b.estado !== vigente || texto) {
        a.estado = b.estado; a.cambiadoEn = ahora;
        if (b.estado === 'aprobada') a.aprobado = { t: ahora, entrega: par.m.entrega };     // lo aprobado queda con SU entrega: un aviso posterior de Claudia no la cambia
        else delete a.aprobado;
      }
    }
    if (JSON.stringify([a.estado, a.cambiadoEn, a.referencia, a.receta, a.correcciones, a.aprobado]) === antes) return json(200, { ok: true, anuncio: par.m, igual: true });
    F.anotarHecho(a, rol, nota.join('. ') || 'Se editó el pedido', ahora, 20);
    await guardarAnuncio(env, a);
    return json(200, { ok: true, anuncio: F.anuncioCompleto(a, par.e) });
  }

  return json(400, { ok: false, error: 'Accion no reconocida: ' + accion });
}
