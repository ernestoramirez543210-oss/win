// «Hacerla en el servidor» (2026-11-13, /tablero › Variaciones): el servidor del editor (VPS) hace SOLO el inicio de una variacion y pega el resto del original, como Beto hizo la 105.1 de
// Sermones a mano. El dueño la pide desde la variacion; el VPS la toma cada hora, avisa como va y deja el resultado en Drive. Tambien recibe el INDICE de nuestros videos en Drive, con el que
// /api/tablero le pone a cada variacion el enlace a su original («🎞️ Abrir el original»).
//
//   GET  /api/variacion-editor                                    equipo -> { ok, rol, indice:{ generadoEn, total, raices }, servidor:{ activo, topeUsd, costoEstimadoUsd, voz, modo, revisionBeto } }
//   POST /api/variacion-editor { accion:'pedir', id, original, numero }     DUEÑO -> { ok, fila }        (fila con sus `originales`, como la da /api/tablero)
//   POST /api/variacion-editor { accion:'cancelar', id }                    DUEÑO -> { ok, fila }        (solo mientras siga «pendiente»)
//   GET  /api/variacion-editor?pendientes=1        (x-clave-editor)  VPS -> { ok, pedidos:[{ id, oferta, nombre, numero, base, gancho, nota, cambio, referencia, paises, original:{id,nombre,carpeta,raiz}, archivo, intento }] }
//   GET  /api/variacion-editor?id=<id>              (x-clave-editor)  VPS -> { ok, pedido, edicion }
//   POST /api/variacion-editor { accion:'avance', id, estado, etapa, nota, driveId, archivo, costoUsd, corteSeg, ganchoSeg, marcarLista, intento? }   (x-clave-editor) VPS -> { ok, edicion }
//   POST /api/variacion-editor { accion:'indice', generadoEn, raices:[{nombre,id}], archivos:[{id,nombre,carpeta,raiz,mb,fecha}], servidor:{...} }   (x-clave-editor) VPS -> { ok, total, descartados,
//        grupos, escritos }   (o { ok, total, descartados, conservado:true } si llego sin ningun original y habia un indice guardado: se conserva el anterior)
//
// Donde vive: la fila de la variacion (`tablero:v:<id>`, la de /api/tablero) gana `edicion`, que SOLO se escribe aqui (limpiarVar de tablero.js no la toma del navegador). Mientras un pedido
// esta pendiente hay una marca `varedit:p:<id>` (3 dias) y la hora del ultimo pedido en `varedit:hay`: el VPS pregunta cada hora y, si esa hora es de hace mas de 72 h, se le contesta que no hay
// nada SIN listar KV (los listados son pocos al dia en toda la suite). El indice va partido por numero de anuncio en `varedit:i:<101|102|…|ad>` (solo se reescriben los grupos que cambiaron)
// y su resumen en `varedit:indice:meta` (lo que lee la pagina; dice que grupos hay): /api/tablero y «pedir» leen solo los grupos que necesitan (functions/_originales.js, nota de la CPU).
//
// Quien llama del lado del servidor: el VPS, SIN sesion, con la cabecera x-clave-editor (la misma CLAVE_EDITOR de /api/fabrica-editor). Sin CLAVE_EDITOR puesta (o de menos de 16 caracteres)
// contesta 503; con otra llave, 401. Con esa cabecera NUNCA se cae al camino de la sesion. Con la llave se puede: leer los pedidos pendientes (solo la variacion pedida, no ventas ni gasto),
// avisar como va uno (con transiciones fijas: no revive un pedido cancelado ni pisa uno ya terminado) y mandar el indice. No se puede borrar nada ni cambiar otra cosa de la fila que `edicion`
// (y, al terminar: el enlace si estaba vacio y «Lista» si el servidor lo tiene configurado).
//
// Todo lo que llega del VPS o de Drive (nombres de archivo, carpetas, notas) es texto de afuera: cada campo se limpia y se acota, los enlaces de Drive se REHACEN con el id (nunca se toman del
// cuerpo) y la pagina escapa todo lo que pinta. Aqui no se arma HTML.
import { exigirEquipo, exigirDueno, safeEqual } from '../_sesion.js';
import { RE_ID_DRIVE, RE_ARCHIVO, RE_GRUPO, PREFIJO_GRUPO, CLAVE_META, enlaceDrive, textoLimpio, parsearNombre, nucleo, grupoDe, gruposDelMeta, originalesDe, numeroDeVariacion,
  nombreArchivoVariacion } from '../_originales.js';

const PREFIJO_VAR = 'tablero:v:';
const PREFIJO_PEND = 'varedit:p:';
const MARCA = 'varedit:hay';
const MARCA_TTL_S = 3 * 24 * 3600;
const MARCA_VIVE_H = 72;
const VIEJO_H = 6;                // un pedido pendiente o tomado sin noticias en 6 h se da por perdido: se puede volver a pedir
const POR_PASADA = 2;             // los que se lleva el VPS en cada pasada
const LISTAR = 20;
const MAX_CUERPO = 64 * 1024;     // un aviso
const MAX_INDICE = 700 * 1024;    // el indice entero
const MAX_ARCHIVOS = 6000;
const MAX_RAICES = 50;
const ID_VAR = /^[a-z0-9]{8,20}$/;
// Topes de lo que la llave del editor puede escribir (revisor-seguridad 2026-11-13: la llave tambien esta en los PC de los editores y KV gratis = 1.000 escrituras al dia en TODA la suite).
const INDICE_CADA_MIN = 50;       // un indice completo cada 50 min como mucho (el servidor lo manda cada 12 h, o al cambiar lo que tiene encendido)
const SERVIDOR_CADA_MIN = 10;     // dentro de esa ventana solo se anota lo que el servidor dice de si mismo, y no mas de una vez cada 10 min
const MAX_GRUPOS_POR_ENVIO = 20;  // grupos reescritos por envio salvo el primero: un cambio grande entra en varias vueltas (los demas quedan con su version anterior)
const MAX_ESCRITURAS_DIA = 150;   // escrituras del indice por dia (UTC), sumando grupos, borrados y resumen
const MAX_AVISOS_INTENTO = 20;    // avisos «en-curso» por intento (el servidor manda unos 4)
const PENDIENTES_CACHE_S = 60;    // la lista de pendientes se guarda 60 s (cada lectura lista KV, y los listados tambien tienen tope diario)
const ETAPAS = ['original', 'analisis', 'gancho', 'render', 'unir', 'revisar', 'entrega'];
const VIVOS = ['pendiente', 'tomado', 'en-curso'];
const MODOS = ['clip', 'fish', 'original'];
// Lo que el VPS puede decir de un pedido, desde cada estado. Lo demas (revivir uno cancelado, pisar uno terminado, saltarse «tomado») es 409.
const PASOS = { pendiente: ['tomado', 'error'], tomado: ['en-curso', 'lista', 'error'], 'en-curso': ['en-curso', 'lista', 'error'] };
const PAISES = ['colombia', 'mexico', 'venezuela', 'guatemala', 'peru', 'argentina', 'ecuador', 'bolivia', 'elsalvador', 'uruguay'];

const json = (s, o) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
const error = (s, msg) => json(s, { ok: false, error: msg });
const horasDesde = (iso) => { const t = Date.parse(iso); return Number.isFinite(t) ? (Date.now() - t) / 3600000 : Infinity; };
const numeroEn = (v, min, max) => { if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null; const n = Number(v); return Number.isFinite(n) && n >= min && n <= max ? n : null; };
const redondo = (n, d) => Math.round(n * Math.pow(10, d)) / Math.pow(10, d);
function urlHttp(v) {
  const s = String(v == null ? '' : v).trim().slice(0, 500);
  try { const u = new URL(s); return u.protocol === 'https:' || u.protocol === 'http:' ? s : ''; } catch { return ''; }
}

// Lo que el VPS dice de si mismo (corredor.json › variaciones), acotado. Sin dato: apagado. Beto revisa salvo que el servidor diga expresamente que no.
export function servidorLimpio(s) {
  if (!s || typeof s !== 'object') return { activo: false };
  const out = { activo: s.activo === true ? true : s.activo === 'ensayo' ? 'ensayo' : false };
  const tope = numeroEn(s.topeUsd, 0, 5); if (tope !== null) out.topeUsd = redondo(tope, 2);
  const costo = numeroEn(s.costoEstimadoUsd, 0, 5); if (costo !== null) out.costoEstimadoUsd = redondo(costo, 3);
  const voz = textoLimpio(s.voz, 40); if (/^[\p{L}\p{N} _.:-]+$/u.test(voz)) out.voz = voz;
  // Como hace el inicio nuevo: «clip» (el avatar del original dice el gancho), «fish» (nuestra voz + imagenes) u «original» (la voz del original + imagenes nuevas).
  if (MODOS.includes(s.modo)) out.modo = s.modo;
  out.revisionBeto = s.revisionBeto !== false;
  return out;
}

// Lo que se guarda de la edicion y lo que se le devuelve a quien la mira (todo ya acotado al escribirlo; se vuelve a acotar al leer por si la fila es vieja).
function edicionPublica(e) {
  if (!e || typeof e !== 'object') return null;
  const o = e.original && typeof e.original === 'object' ? e.original : {};
  const out = {
    estado: ['pendiente', 'tomado', 'en-curso', 'lista', 'error', 'cancelada'].includes(e.estado) ? e.estado : '',
    pedidoEn: textoLimpio(e.pedidoEn, 30), pedidoPor: textoLimpio(e.pedidoPor, 20), actualizadoEn: textoLimpio(e.actualizadoEn, 30), intento: numeroEn(e.intento, 0, 9999) || 0,
    original: { id: RE_ID_DRIVE.test(String(o.id || '')) ? o.id : '', nombre: textoLimpio(o.nombre, 120), carpeta: textoLimpio(o.carpeta, 120), raiz: textoLimpio(o.raiz, 120) },
    numero: textoLimpio(e.numero, 20), archivo: RE_ARCHIVO.test(String(e.archivo || '')) ? e.archivo : '',
  };
  for (const k of ['tomadoEn', 'listaEn', 'errorEn', 'canceladaEn']) if (e[k]) out[k] = textoLimpio(e[k], 30);
  if (ETAPAS.includes(e.etapa)) out.etapa = e.etapa;
  if (e.nota) out.nota = textoLimpio(e.nota, 300);
  if (out.estado === 'lista') {
    out.driveId = RE_ID_DRIVE.test(String(e.driveId || '')) ? e.driveId : '';
    out.enlace = enlaceDrive(out.driveId);
    for (const [k, max] of [['costoUsd', 5], ['corteSeg', 20], ['ganchoSeg', 20]]) { const n = numeroEn(e[k], 0, max); if (n !== null) out[k] = n; }
  }
  return out;
}

// Lo que el dueño vio al pedir (gancho, nota, cambio, oferta, referencia y paises), copiado DENTRO del pedido: si alguien edita la variacion despues (cualquier rol puede, y una idea de
// Revivir es texto de terceros), el servidor hace y gasta en lo que el dueño aprobo, no en lo nuevo (revisor-seguridad 2026-11-13). Para cambiarlo: cancelar y volver a pedir.
function copiaDelPedido(fila) {
  return {
    oferta: textoLimpio(fila.oferta, 80), base: textoLimpio(fila.base, 40), gancho: textoLimpio(fila.gancho, 200), nota: textoLimpio(fila.nota, 300),
    cambio: fila.cambio === 'inicio' ? 'inicio' : '', referencia: urlHttp(fila.referencia),
    paises: (Array.isArray(fila.paises) ? fila.paises : []).map((p) => String(p)).filter((p, i, a) => PAISES.includes(p) && a.indexOf(p) === i),
  };
}

// El pedido como lo lee el VPS: solo lo de esta variacion (nada de ventas, gasto ni cuentas), con lo que el dueño aprobo (pedidos de antes de la copia: lo de la fila).
function pedidoDe(fila) {
  const e = edicionPublica(fila.edicion) || {};
  const aprobado = copiaDelPedido(fila.edicion && fila.edicion.aprobado && typeof fila.edicion.aprobado === 'object' ? fila.edicion.aprobado : fila);
  return {
    id: fila.id, oferta: aprobado.oferta, nombre: textoLimpio(fila.nombre, 100), numero: e.numero || '', base: aprobado.base,
    gancho: aprobado.gancho, nota: aprobado.nota, cambio: aprobado.cambio, referencia: aprobado.referencia,
    paises: aprobado.paises,
    original: e.original || { id: '', nombre: '', carpeta: '', raiz: '' }, archivo: e.archivo || '', intento: e.intento || 0,
  };
}

async function cuerpoJson(request, max) {
  if (Number(request.headers.get('content-length') || 0) > max) return { malo: 413 };
  let t;
  try { t = await request.text(); } catch { return { malo: 400 }; }
  if (t.length > max) return { malo: 413 };
  try { const b = JSON.parse(t); return b && typeof b === 'object' && !Array.isArray(b) ? { b, largo: t.length } : { malo: 400 }; } catch { return { malo: 400 }; }
}

async function leerJson(env, clave) { try { return await env.SUITE.get(clave, { type: 'json' }); } catch { return null; } }
const leerMeta = (env) => leerJson(env, CLAVE_META);
const porProducir = (fila) => fila.estado === 'idea' || fila.estado === 'produccion';

// ---- el VPS (llave del editor) ----
async function delServidor(request, env, url) {
  const metodo = request.method;
  if (metodo === 'GET') {
    if (url.searchParams.get('pendientes') === '1') {
      // Solo LEE: no cambia ninguna fila. Las marcas de pedidos que ya no estan pendientes se borran (son marcas, no datos). La respuesta se guarda 60 s en la cache del sitio:
      // quien repita la llamada con la llave no lista KV cada vez (un pedido nuevo tarda a lo sumo ese minuto en verse; el servidor pregunta cada hora).
      const cache = typeof caches !== 'undefined' && caches.default ? caches.default : null;
      const claveCache = new Request('https://cache.interna/variacion-editor/pendientes');
      if (cache) { try { const hit = await cache.match(claveCache); if (hit) return json(200, await hit.json()); } catch { /* sin cache: se lee */ } }
      const marca = await env.SUITE.get(MARCA);
      if (!marca || horasDesde(marca) > MARCA_VIVE_H) return json(200, { ok: true, pedidos: [] });
      const l = await env.SUITE.list({ prefix: PREFIJO_PEND, limit: LISTAR });
      const pedidos = [];
      for (const k of l.keys) {
        if (pedidos.length >= POR_PASADA) break;
        const id = k.name.slice(PREFIJO_PEND.length);
        if (!ID_VAR.test(id)) { await env.SUITE.delete(k.name); continue; }
        const fila = await env.SUITE.get(PREFIJO_VAR + id, { type: 'json' });
        // Ya no pendiente, o alguien la paso a Lista / la descarto antes de que el servidor la tomara: no se hace (la pagina lo dice en la variacion).
        if (!fila || !fila.edicion || fila.edicion.estado !== 'pendiente' || !porProducir(fila)) { await env.SUITE.delete(PREFIJO_PEND + id); continue; }
        pedidos.push(pedidoDe(fila));
      }
      if (cache) { try { await cache.put(claveCache, new Response(JSON.stringify({ ok: true, pedidos }), { headers: { 'Cache-Control': 'max-age=' + PENDIENTES_CACHE_S } })); } catch { /* sin cache */ } }
      return json(200, { ok: true, pedidos });
    }
    const id = url.searchParams.get('id');
    if (id !== null) {
      if (!ID_VAR.test(id)) return error(400, 'Id no valido');
      const fila = await env.SUITE.get(PREFIJO_VAR + id, { type: 'json' });
      if (!fila || !fila.edicion) return error(404, 'Esa variación no tiene pedido al servidor');
      return json(200, { ok: true, pedido: pedidoDe(fila), edicion: edicionPublica(fila.edicion) });
    }
    return error(400, 'Pide ?pendientes=1 o ?id=<id>');
  }
  if (metodo !== 'POST') return error(405, 'Method not allowed');
  const c = await cuerpoJson(request, MAX_INDICE);
  if (c.malo) return error(c.malo, c.malo === 413 ? 'Cuerpo demasiado grande' : 'JSON invalido');
  const b = c.b;
  if (b.accion === 'indice') return guardarIndice(env, b);
  if (b.accion !== 'avance') return error(400, 'Accion no reconocida');
  if (c.largo > MAX_CUERPO) return error(413, 'Cuerpo demasiado grande');
  return avance(env, b);
}

async function avance(env, b) {
  const id = String(b.id || '');
  if (!ID_VAR.test(id)) return error(400, 'Id no valido');
  const estado = String(b.estado || '');
  if (!['tomado', 'en-curso', 'lista', 'error'].includes(estado)) return error(400, 'El estado debe ser tomado, en-curso, lista o error');
  const fila = await env.SUITE.get(PREFIJO_VAR + id, { type: 'json' });
  if (!fila) return error(404, 'Esa variación ya no existe');
  const e = fila.edicion;
  if (!e || typeof e !== 'object') return error(409, 'Esa variación no tiene pedido al servidor');
  // Cada aviso dice de que intento es (obligatorio): uno tardio de un intento anterior (el dueño la volvio a pedir) no toca el pedido nuevo.
  if (numeroEn(b.intento, 0, 9999) === null) return error(400, 'Falta el intento del pedido');
  if (Number(b.intento) !== Number(e.intento || 0)) return error(409, 'Ese aviso es de otro intento: el dueño la volvió a pedir');
  if (!(PASOS[e.estado] || []).includes(estado)) return error(409, 'Ese pedido está «' + (e.estado || 'sin estado') + '»: no pasa a «' + estado + '» desde el servidor');
  if (estado === 'tomado' && !porProducir(fila)) return error(409, 'Esa variación ya no está por producir: no la haga');
  if (estado === 'en-curso' && (Number(e.avisos) || 0) >= MAX_AVISOS_INTENTO) return error(429, 'Demasiados avisos para este pedido');
  const ahora = new Date().toISOString();
  const nueva = { ...e, estado, actualizadoEn: ahora, avisos: (Number(e.avisos) || 0) + 1 };
  if (estado === 'tomado') {
    nueva.tomadoEn = ahora;
  } else if (estado === 'en-curso') {
    if (b.etapa !== undefined && b.etapa !== null && b.etapa !== '') {
      if (!ETAPAS.includes(b.etapa)) return error(400, 'La etapa debe ser una de: ' + ETAPAS.join(', '));
      nueva.etapa = b.etapa;
    }
    if (b.nota !== undefined) nueva.nota = textoLimpio(b.nota, 200);
  } else if (estado === 'lista') {
    const driveId = String(b.driveId || '');
    if (!RE_ID_DRIVE.test(driveId)) return error(400, 'driveId no valido');
    const archivo = String(b.archivo || '');
    if (!RE_ARCHIVO.test(archivo)) return error(400, 'Nombre de archivo no valido');
    const costo = numeroEn(b.costoUsd, 0, 5);
    if (costo === null) return error(400, 'costoUsd debe ser un numero entre 0 y 5');
    const corte = numeroEn(b.corteSeg, 0, 20), gancho = numeroEn(b.ganchoSeg, 0, 20);
    if ((b.corteSeg != null && corte === null) || (b.ganchoSeg != null && gancho === null)) return error(400, 'corteSeg y ganchoSeg van de 0 a 20 segundos');
    // El enlace se REHACE con el id: lo que mande el cuerpo como «enlace» no se mira.
    Object.assign(nueva, { listaEn: ahora, driveId, enlace: enlaceDrive(driveId), archivo, costoUsd: redondo(costo, 4), corteSeg: corte === null ? null : redondo(corte, 2), ganchoSeg: gancho === null ? null : redondo(gancho, 2), etapa: 'entrega' });
    if (b.nota !== undefined) nueva.nota = textoLimpio(b.nota, 200); else delete nueva.nota;
    if (!fila.enlace) fila.enlace = nueva.enlace;
    // «Lista» la marca el servidor solo si al PEDIR el servidor decia que no la revisa Beto (queda anotado en el pedido: no lo decide la llave sola) y la variacion sigue por producir.
    if (b.marcarLista === true && e.revisionBeto === false && (fila.estado === 'idea' || fila.estado === 'produccion')) { fila.estado = 'lista'; fila.en = ahora; fila.por = 'servidor'; }
  } else {
    nueva.errorEn = ahora;
    nueva.nota = textoLimpio(b.nota, 300) || 'El servidor no dijo el motivo';
  }
  fila.edicion = nueva;
  await env.SUITE.put(PREFIJO_VAR + id, JSON.stringify(fila));
  if (estado !== 'en-curso') await env.SUITE.delete(PREFIJO_PEND + id);
  return json(200, { ok: true, edicion: edicionPublica(nueva) });
}

// La huella de un grupo: si no cambio desde el indice anterior no se vuelve a escribir (KV gratis = 1.000 escrituras al dia en TODA la suite; el indice llega cada 12 h con ~30 grupos).
async function huella(texto) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(texto)));
  let h = '';
  for (let i = 0; i < 8; i++) h += d[i].toString(16).padStart(2, '0');
  return h;
}

async function guardarIndice(env, b) {
  if (!Array.isArray(b.archivos)) return error(400, 'Falta la lista de archivos');
  if (b.archivos.length > MAX_ARCHIVOS) return error(413, 'Son ' + b.archivos.length + ' archivos: el tope es ' + MAX_ARCHIVOS);
  // De las carpetas de primer nivel solo se guarda el nombre (la pagina dice cuales recorrio el servidor); su id no hace falta.
  const raices = (Array.isArray(b.raices) ? b.raices : []).slice(0, MAX_RAICES).map((r) => (r && typeof r === 'object' ? textoLimpio(r.nombre, 120) : '')).filter(Boolean);
  const archivos = [], vistos = new Set();
  for (const a of b.archivos) {
    if (!a || typeof a !== 'object') continue;
    const id = String(a.id || '');
    if (!RE_ID_DRIVE.test(id) || vistos.has(id)) continue;
    const nombre = textoLimpio(a.nombre, 120);
    const p = parsearNombre(nombre);
    if (!p) continue;                      // sin numero de anuncio o sin palabra de oferta: no es un original
    vistos.add(id);
    const mb = numeroEn(a.mb, 0, 100000);
    archivos.push({ id, nombre, carpeta: textoLimpio(a.carpeta, 120), raiz: textoLimpio(a.raiz, 120), mb: mb === null ? 0 : redondo(mb, 1),
      fecha: /^\d{4}-\d{2}-\d{2}$/.test(String(a.fecha || '')) ? a.fecha : '', o: p.o, n: p.n, p: p.p });
  }
  const ahora = new Date().toISOString();
  const generadoEn = /^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?$/.test(String(b.generadoEn || '')) ? b.generadoEn : ahora;
  const servidor = servidorLimpio(b.servidor);
  const antes = await leerMeta(env) || {};
  const huellasAntes = gruposDelMeta(antes) ? antes.grupos : {};
  // Topes de escritura (el resumen ya se lee: no cuestan operaciones). Por dia, y un indice completo cada INDICE_CADA_MIN; dentro de esa ventana solo se anota el cambio de lo que el
  // servidor tiene encendido (asi la pagina lo sabe ya), no mas de una vez cada SERVIDOR_CADA_MIN.
  const dia = ahora.slice(0, 10);
  const usadas = antes.uso && antes.uso.d === dia ? numeroEn(antes.uso.n, 0, 1e6) || 0 : 0;
  if (usadas >= MAX_ESCRITURAS_DIA) return error(429, 'El índice ya se escribió demasiadas veces hoy: se guarda mañana');
  const mismoServidor = JSON.stringify(servidorLimpio(antes.servidor)) === JSON.stringify(servidor);
  const minutosDesde = (iso) => horasDesde(iso) * 60;
  if (minutosDesde(antes.recibidoEn) < INDICE_CADA_MIN) {
    if (mismoServidor) return error(429, 'El índice llegó hace menos de una hora: se conserva el que había');
    if (minutosDesde(antes.servidorEn) < SERVIDOR_CADA_MIN) return error(429, 'Lo que el servidor dice de sí mismo cambió hace menos de 10 minutos');
    await env.SUITE.put(CLAVE_META, JSON.stringify({ ...antes, servidor, servidorEn: ahora, uso: { d: dia, n: usadas + 1 } }));
    return json(200, { ok: true, total: numeroEn(antes.total, 0, MAX_ARCHIVOS) || 0, descartados: 0, conservado: true, soloServidor: true });
  }
  // Un indice SIN originales cuando el guardado tenia: casi seguro fallo la lectura de Drive. Se conserva el anterior y solo se anota lo que el servidor dice de si mismo.
  if (!archivos.length && numeroEn(antes.total, 1, MAX_ARCHIVOS) !== null) {
    await env.SUITE.put(CLAVE_META, JSON.stringify({ ...antes, recibidoEn: ahora, servidor, servidorEn: ahora, uso: { d: dia, n: usadas + 1 } }));
    return json(200, { ok: true, total: numeroEn(antes.total, 1, MAX_ARCHIVOS), descartados: b.archivos.length, conservado: true });
  }
  const porGrupo = new Map();
  for (const a of archivos) {
    const g = grupoDe(a.n);
    if (!g) continue;
    if (!porGrupo.has(g)) porGrupo.set(g, []);
    porGrupo.get(g).push(a);
  }
  // Primero los grupos (solo los que cambiaron), luego el resumen que los nombra y al final se borran los que ya no estan: quien lee el resumen siempre encuentra sus grupos.
  // Salvo el primer indice, a lo sumo MAX_GRUPOS_POR_ENVIO grupos por envio (y siempre dentro del tope del dia): un grupo cambiado que no entra se queda con su version anterior
  // (el resumen conserva su huella vieja, que es la que el grupo guardado trae) y entra en la vuelta siguiente; uno nuevo que no entra todavia no se nombra.
  const primero = !gruposDelMeta(antes);
  let libres = Math.min(primero ? Infinity : MAX_GRUPOS_POR_ENVIO, MAX_ESCRITURAS_DIA - usadas - 1);
  const huellas = {};
  let escritos = 0, aplazados = 0;
  for (const [g, lista] of porGrupo) {
    lista.sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));      // el orden de Drive cambia de una vuelta a otra: no es un cambio
    const texto = JSON.stringify({ archivos: lista });
    const h = await huella(texto);
    if (huellasAntes[g] === h) { huellas[g] = h; continue; }
    if (libres <= 0) { aplazados++; if (huellasAntes[g]) huellas[g] = huellasAntes[g]; continue; }
    // La huella va tambien en los metadatos del grupo: /api/tablero la usa para no volver a leer un grupo que no cambio (y nunca confundir uno viejo con el nuevo).
    await env.SUITE.put(PREFIJO_GRUPO + g, texto, { metadata: { h } });
    huellas[g] = h; escritos++; libres--;
  }
  const borrar = Object.keys(huellasAntes).filter((g) => RE_GRUPO.test(g) && !porGrupo.has(g));
  const borrables = borrar.slice(0, Math.max(0, libres));
  for (const g of borrar) if (!borrables.includes(g)) huellas[g] = huellasAntes[g];        // se borra en la vuelta siguiente: mientras, sigue nombrado y legible
  await env.SUITE.put(CLAVE_META, JSON.stringify({ generadoEn, recibidoEn: ahora, total: archivos.length, raices, servidor, servidorEn: ahora, grupos: huellas,
    uso: { d: dia, n: usadas + escritos + borrables.length + 1 }, aplazados: aplazados + borrar.length - borrables.length }));
  for (const g of borrables) await env.SUITE.delete(PREFIJO_GRUPO + g);
  return json(200, { ok: true, total: archivos.length, descartados: b.archivos.length - archivos.length, grupos: porGrupo.size, escritos, aplazados: aplazados + borrar.length - borrables.length });
}

// ---- el dueño: pedir y cancelar ----
const NO_LA_HACE = {
  'inicio-cierre': 'Esta variación cambia también el cierre: la hace Beto.',
  formato: 'Esta variación rehace el inicio en otro formato: la hace Beto.',
};

async function pedir(env, b, rol) {
  const id = String(b.id || '');
  if (!ID_VAR.test(id)) return error(400, 'Id no valido');
  const fila = await env.SUITE.get(PREFIJO_VAR + id, { type: 'json' });
  if (!fila) return error(404, 'Esa variación ya no existe');
  if (!porProducir(fila)) return error(409, 'Esta variación ya no está por producir.');
  const base = String(fila.base || '').trim();
  if (!base) return error(409, 'Esta variación no dice de cuál anuncio nuestro sale (falta «Anuncio base»).');
  if (!String(fila.gancho || '').trim()) return error(409, 'Falta el gancho: lo que se dice al inicio.');
  const cambio = String(fila.cambio || '');
  if (cambio && cambio !== 'inicio') return error(409, NO_LA_HACE[cambio] || 'Esta variación cambia más que el inicio: la hace Beto.');
  const previa = fila.edicion && typeof fila.edicion === 'object' ? fila.edicion : null;
  if (previa && VIVOS.includes(previa.estado) && horasDesde(previa.actualizadoEn || previa.pedidoEn) < VIEJO_H) {
    return error(409, previa.estado === 'pendiente' ? 'Ya está pedida al servidor: arranca en menos de una hora.' : 'El servidor ya la está haciendo.');
  }
  const n = nucleo(base);
  if (!n) return error(409, 'No reconozco el número del anuncio base («' + textoLimpio(base, 40) + '»).');
  if (/^ad/.test(n)) return error(409, 'El servidor solo hace variaciones de anuncios numerados (101, 105…).');
  const meta = await leerMeta(env) || {};
  const servidor = servidorLimpio(meta.servidor);
  // Con el servidor APAGADO tambien se puede pedir: el pedido queda en espera hasta que el dueño la autorice con su tope en el servidor (corredor-ctl correr --variacion <id> --tope-usd …)
  // o encienda las variaciones (revisor-cambios 2026-11-13: sin esto, el camino manual no tenia ningun pedido que tomar). La pagina lo dice.
  const numero = String(b.numero == null ? '' : b.numero).trim().replace(/^ad\s*/i, '');
  if (!new RegExp('^' + n.replace(/\./g, '\\.') + '\\.\\d{1,2}(?:\\.\\d{1,2})?$').test(numero)) return error(400, 'El número de la variación debe ser el del original y uno más, por ejemplo ' + n + '.7');
  // Solo el grupo del indice con el numero de su base (no el indice entero).
  const g = grupoDe(n), hay = gruposDelMeta(meta);
  const originales = originalesDe(hay && hay.has(g) ? await leerJson(env, PREFIJO_GRUPO + g) : null, fila.oferta, base);
  const o = originales.find((x) => x.id === String(b.original || ''));
  if (!o) return error(409, 'Ese original ya no está en el índice de Drive o no es el de esta variación. Recargue la página.');
  const archivo = nombreArchivoVariacion(o.nombre, numero);
  if (!archivo) return error(409, 'No pude armar el nombre del archivo nuevo a partir de «' + o.nombre + '».');
  const ahora = new Date().toISOString();
  fila.edicion = {
    estado: 'pendiente', pedidoEn: ahora, pedidoPor: textoLimpio(rol, 20), actualizadoEn: ahora,
    original: { id: o.id, nombre: o.nombre, carpeta: o.carpeta, raiz: o.raiz }, numero, archivo,
    intento: ((previa && numeroEn(previa.intento, 0, 9999)) || 0) + 1,
    aprobado: copiaDelPedido(fila),                       // lo que el dueño vio al pedir: es lo que el servidor hace (ver pedidoDe)
    // Si el servidor puede dejarla «Lista» solo lo decide esto, no el aviso: hace falta que el servidor lo diga Y que la ventana del dueño lo mostrara (`sinRevision`, que la pagina
    // manda solo si decia «El servidor la deja Lista solo»). Lo que dice el servidor de si mismo lo escribe la misma llave: no basta solo (revisor-seguridad 2026-11-13).
    revisionBeto: !(servidor.revisionBeto === false && b.sinRevision === true),
  };
  // El nombre de la variacion lleva su numero (asi la cuenta RevivirOfertas.siguienteNumero y asi se llama en Meta): si se eligio otro, se cambia.
  const actual = numeroDeVariacion(fila.nombre);
  if (actual.replace(/^AD /, '') !== numero) {
    const nombre = String(fila.nombre || '').trim();
    fila.nombre = (actual ? nombre.replace(/^(?:AD )?\d+(?:\.\d+)*/i, (/^AD /.test(actual) ? 'AD ' : '') + numero) : numero + ' · ' + nombre).slice(0, 100);
  }
  if (fila.estado === 'idea') { fila.estado = 'produccion'; fila.en = ahora; fila.por = textoLimpio(rol, 20); }
  await env.SUITE.put(PREFIJO_VAR + id, JSON.stringify(fila));
  await env.SUITE.put(PREFIJO_PEND + id, '1', { expirationTtl: MARCA_TTL_S });
  await env.SUITE.put(MARCA, ahora, { expirationTtl: MARCA_TTL_S });
  return json(200, { ok: true, fila: { ...fila, originales } });
}

async function cancelar(env, b) {
  const id = String(b.id || '');
  if (!ID_VAR.test(id)) return error(400, 'Id no valido');
  const fila = await env.SUITE.get(PREFIJO_VAR + id, { type: 'json' });
  if (!fila) return error(404, 'Esa variación ya no existe');
  const e = fila.edicion;
  if (!e || e.estado !== 'pendiente') return error(409, e && (e.estado === 'tomado' || e.estado === 'en-curso') ? 'El servidor ya la está haciendo.' : 'No hay un pedido pendiente que cancelar.');
  const ahora = new Date().toISOString();
  fila.edicion = { ...e, estado: 'cancelada', canceladaEn: ahora, actualizadoEn: ahora };
  await env.SUITE.put(PREFIJO_VAR + id, JSON.stringify(fila));
  await env.SUITE.delete(PREFIJO_PEND + id);
  return json(200, { ok: true, fila });
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return error(501, 'Falta el almacén KV');
  const url = new URL(request.url);

  // ---- el VPS: con la cabecera, SOLO la llave decide (nunca se cae a la sesion) ----
  if (request.headers.has('x-clave-editor')) {
    const llave = request.headers.get('x-clave-editor') || '';
    if (!env.CLAVE_EDITOR || String(env.CLAVE_EDITOR).length < 16) return error(503, 'La llave del editor no esta configurada en el servidor (scripts\\Setup-ClaveEditor.ps1)');
    if (!safeEqual(llave, String(env.CLAVE_EDITOR))) return error(401, 'Llave incorrecta');
    return delServidor(request, env, url);
  }

  // ---- el equipo ----
  const rol = await exigirEquipo(request, env);
  if (!rol) return error(401, 'Entra al equipo para ver las variaciones del servidor');
  if (request.method === 'GET') {
    const meta = await leerMeta(env) || {};
    return json(200, {
      ok: true, rol,
      indice: { generadoEn: textoLimpio(meta.generadoEn, 40), total: numeroEn(meta.total, 0, MAX_ARCHIVOS) || 0, raices: (Array.isArray(meta.raices) ? meta.raices : []).slice(0, MAX_RAICES).map((r) => textoLimpio(r, 120)).filter(Boolean) },
      servidor: servidorLimpio(meta.servidor),
    });
  }
  if (request.method !== 'POST') return error(405, 'Method not allowed');
  // Pedir y cancelar gastan (o dejan de gastar) con las llaves del dueño en el servidor: solo el dueño.
  if (!(await exigirDueno(request, env))) return error(403, 'Solo el dueño puede pedir variaciones al servidor.');
  const c = await cuerpoJson(request, MAX_CUERPO);
  if (c.malo) return error(c.malo, 'JSON invalido o demasiado grande');
  if (c.b.accion === 'pedir') return pedir(env, c.b, rol);
  if (c.b.accion === 'cancelar') return cancelar(env, c.b);
  return error(400, 'Accion no reconocida');
}
