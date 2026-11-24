// Si los flujos de Venta y Entrega de una oferta estan ENCENDIDOS en ZapData ahora (y, cuando la conexion lo deja ver, a que numeros estan asignados). Lo pide la guia de lanzamiento de /fabrica
// para marcar sola la tarea «Venta y Entrega encendidos con ese número» (paso 2): antes la marcaba una persona y nada comprobaba que fuera verdad.
//
//   GET /api/zapdata-flujos?pedido=<id del pedido de /api/zapflow>
//     -> { ok:true, ahora, fuente:'lista'|'borradores', paises:{ <pais>:{ venta:{ id, activo, canales }, entrega:{ id, activo, canales } } } }
//          id:      el flujo que creo el tablero para ese pais ('' = todavia no se creo)
//          activo:  true | false segun ZapData; null = ZapData no lo trae en la lista que contesto (no se puede confirmar)
//          canales: los ids de las conexiones a las que esta asignado (los mismos de /api/zapdata-canales); null = esta conexion de ZapData no lo deja ver
//          (con revisable = true, o sea una credencial con zapdata_flow_list y zapdata_flow_get, cada pais trae ademas)
//            inicio: 'ok'|'mal'|null  ·  pagos: { estado:'ok'|'falta'|'suelta'|'otra'|'casi'|'sin-pagos'|'no-aplica'|null, nombre }  ·  fallo: true si ZapData no dejo leer su Venta o su flujo de Pagos
//            (null = todavia se esta revisando); y la respuesta trae pendientes (cuantos flujos faltan por leer: la pagina vuelve en 20 s), revisionFallo y limite (ZapData en su tope del dia)
//     -> { ok:false, ahora, motivo:'sin_pedido'|'sin_conexion'|'sin_permiso'|'vencida'|'red'|'limite'|'otro', mensaje, detalle? }   (el `detalle` tecnico solo lo ve el dueño)
//
//   GET /api/zapdata-flujos?revision=1   (el aviso del menu y la caja de /fabrica, 2026-11-06)
//     -> { ok:true, ahora, revisadoEn, total, revisadas, noLeidas, completo, limite, revisionFallo, aviso?,
//          problemas:[{ venta, oferta, pais, inicio, pagos, pagosNombre }],   las Ventas ENCENDIDAS con el inicio sin arreglar o sin su rama de pagos
//          dudas:[{ venta, oferta, pais, motivo:'sin-pagos'|'no-aplica'|'sin-inicio' }] }   las que no tienen nada mal pero no se pudieron confirmar del todo
//        aviso = ZapData no contesto ahora y esto es lo ultimo que se supo (de menos de 6 h)
//     -> { ok:false, ahora, motivo:'sin_lista'|'limite'|..., mensaje }   sin_lista = la credencial de ZapData no deja leer los flujos completos y no se puede revisar
//   El inicio y la rama de pagos los revisa functions/_zapdata-revision.js (por que, como y cuanto cuesta, alli).
//
// De donde sale cada cosa: los ids de los flujos, del pedido que el tablero guardo al crearlos (`zapflow:<id>` en KV, `creados` en el mismo orden que `flujosZap`: Venta y Entrega de cada pais); su estado,
// de ZapData por la conexion OAuth del tablero (functions/_zapdata.js > listarFlujos). Esa conexion HOY solo trae `zapdata_flow_draft_list` (los ultimos 50 borradores, con `is_active` y sin numero):
// por eso `fuente` dice cual se uso y la guia avisa cuando no puede comprobar el numero.
//
// Quien puede: todo el equipo (dueño, trafficker, editor y videos), igual que /api/zapdata-canales; los socios de pais no. Falla cerrado aunque el login este apagado (exigirLecturaEquipo).
// Cuesta: 1 lectura de KV por pedido (el del pedido) y, cuando la memoria del aislado ya no vale, una consulta a ZapData (4 llamadas: presentarse, avisar, herramientas y la lista). Memoria de 2 min si
// salio bien, 20 s sin red y 5 min con un error que no se arregla solo; un solo vuelo por aislado. No lista claves ni escribe nada en ZapData. En KV solo escribe si el permiso de ZapData esta vencido
// (leerToken lo renueva y lo guarda, una vez por aislado, lo mismo que ya hace /api/zapdata-canales).
// Lo que sale no lleva texto de ZapData salvo nombres de flujos (la revision: la oferta y su flujo de Pagos, que la pagina pinta con esc()); nada de conexiones ni telefonos.
// La revision suma, por consulta, una lectura de KV (`zapdata:revision`), como mucho un flujo completo leido (dos intentos) y una escritura solo si algo cambio; ver _zapdata-revision.js.

import { exigirLecturaEquipo, safeEqual } from '../_sesion.js';
import { listarFlujos, conPlazo, PLAZOS } from '../_zapdata.js';
import { NOMBRES_RESERVADOS } from '../_fabrica.js';
import { avanzar, cargarAlmacen, guardarAlmacen, enPausa, PAUSA_MS, estadoVenta, revisionGlobal, pagosPorPais, falloReciente, completa, plano, bumpsGlobal } from '../_zapdata-revision.js';
import { clasificar, sinTelefono } from './zapdata-canales.js';

const json = (s, o) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
const VIDA_OK_MS = 120000;
const VIDA_MAL_MS = 20000;      // sin red: se reintenta pronto
const VIDA_FIJO_MS = 300000;    // sin conexion, sin permiso, vencida...: no se arregla solo; 5 min para no preguntarle a ZapData de mas
const VIDA_PRIORIDAD_MS = 15000; // lo que pide un pedido y todavia no se reviso: no se esperan los 2 min de la memoria
const VIDA_LIMITE_MS = 30 * 60 * 1000; // ZapData llego a su tope del dia
// El resultado de la revision guardado en KV vale 30 min para todos (el aviso del menu lo pide cada 10 min por navegador). Si nada cambio, se vuelve a escribir solo por la hora cada 30 min: sin esa
// escritura cada aislado le volveria a preguntar a ZapData por la lista en cada aviso del menu, y la lista tambien tiene tope diario (600). Lo que se lee nuevo se guarda en el momento.
const VIDA_REVISION_MS = 30 * 60 * 1000;
const VIEJO_MAX_MS = 6 * 60 * 60 * 1000;   // si ZapData no contesta, el ultimo resultado se sigue mostrando (con aviso) hasta 6 h; mas viejo, no
const vida = (c) => (c.ok ? (c.pendientes > 0 && !c.pausa ? VIDA_MAL_MS : VIDA_OK_MS) : (c.motivo === 'red' ? VIDA_MAL_MS : (c.motivo === 'limite' ? VIDA_LIMITE_MS : VIDA_FIJO_MS)));
const ID_PEDIDO = /^[a-z0-9]{2,40}$/;
const ID_FLUJO = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const txt = (v, n) => String(v == null ? '' : v).replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
let memoria = null;   // { t, cuerpo } por aislado: lo que contesto ZapData (todos los flujos), no lo de un pedido
let enCurso = null;   // { t, p }: la consulta a ZapData en marcha en este aislado

const MENSAJES = {
  sin_pedido: 'No encontré el pedido con el que se crearon los flujos de esta oferta.',
  sin_conexion: 'La conexión del dash con ZapData no está hecha: el dueño la hace en Flujos ZapData → Conexión.',
  sin_permiso: 'La conexión del dash con ZapData no deja ver los flujos: el dueño tiene que reconectarla (Flujos ZapData → Conexión).',
  vencida: 'Se venció el permiso de ZapData: el dueño vuelve a conectar en Flujos ZapData → Conexión.',
  red: 'No pude preguntarle a ZapData por los flujos en este momento; se intenta de nuevo en un rato.',
  otro: 'ZapData no contestó como se esperaba sobre los flujos.',
  limite: 'ZapData llegó a su tope de consultas de hoy; se vuelve a preguntar más tarde.',
  sin_lista: 'La conexión del dash con ZapData no deja leer los flujos completos: el inicio y la rama de pagos no se pueden revisar.',
  revision: 'No pude revisar el inicio y la rama de pagos en este momento; se intenta de nuevo en un rato.',
};

// Para las pruebas: vacia la memoria del aislado.
export function olvidar() { memoria = null; enCurso = null; }

// El pedido de /api/zapflow -> { <pais>: { venta: '<id>'|'', entrega: '<id>'|'' } }. `creados[i]` es el flujo que se creo para `flujosZap[i]`, y `flujosZap` va Venta, Entrega por cada pais en el orden de `paises`
// (functions/api/zapflow.js). Se confirma por el nombre («Venta …», «Entrega …») en vez de confiar solo en la posicion; un flujo sin crear (pedido trabado a medias) queda ''.
export function flujosDelPedido(p) {
  const out = {};
  if (!p || typeof p !== 'object') return out;
  const paises = Array.isArray(p.paises) ? p.paises.slice(0, 12) : [];
  const nombres = (Array.isArray(p.flujosZap) ? p.flujosZap : []).map((f) => String((f && f.name) || ''));
  const creados = Array.isArray(p.creados) ? p.creados : [];
  paises.forEach((pais, j) => {
    if (typeof pais !== 'string' || !pais || NOMBRES_RESERVADOS.includes(pais) || Object.prototype.hasOwnProperty.call(out, pais)) return;
    const par = { venta: '', entrega: '' };
    [2 * j, 2 * j + 1].forEach((i) => {
      const tipo = /^venta\b/i.test(nombres[i]) ? 'venta' : (/^entrega\b/i.test(nombres[i]) ? 'entrega' : '');
      const c = creados[i], id = c && ID_FLUJO.test(String(c.id || '')) ? String(c.id).toLowerCase() : '';
      if (tipo) par[tipo] = id;
    });
    out[pais] = par;
  });
  return out;
}

// ¿Cambio algo que se le muestra al equipo? (la hora no cuenta: por la hora sola se escribe como mucho cada VIDA_REVISION_MS)
const huella = (r) => JSON.stringify(r ? [r.total, r.revisadas, r.noLeidas, r.problemas, r.dudas] : null);

// UNA consulta a ZapData: la lista de flujos (1 llamada) y, si la credencial deja leer flujos completos, un paso de la revision del inicio y la rama de pagos (1 flujo; ver _zapdata-revision.js).
// KV: 1 lectura y, solo si se leyo o fallo un flujo, la revision se pauso, cambio lo que se muestra o el resultado guardado ya tiene VIDA_REVISION_MS, 1 escritura.
async function consultar(env, prioridad) {
  const ahora = Date.now();
  let cargado = null;
  try {
    const r = await listarFlujos(env);
    const porId = {};
    r.flujos.forEach((f) => { porId[f.id] = { activo: f.activo, canales: f.canales }; });
    const revisable = r.fuente === 'lista' && r.lectura === true;
    const cuerpo = { ok: true, fuente: r.fuente, revisable, porId, flujos: r.flujos.map(({ id, activo, canales, nombre, t, gatillo }) => ({ id, activo, canales, nombre, t, gatillo })),
      almacen: null, pendientes: 0, revisionFallo: false, pausa: '' };
    if (revisable) {
      // un fallo de la revision no tumba lo de encendido/apagado: queda marcado y se intenta en la proxima
      try {
        cargado = await cargarAlmacen(env);
        const antes = cargado.almacen.resultado, pausaAntes = cargado.almacen.pausa, pagosDe = pagosPorPais(r.flujos);
        const a = await avanzar(r.sesion, r.flujos, cargado.almacen, prioridad, ahora, pagosDe);
        const nuevo = Object.assign({ en: ahora }, revisionGlobal(r.flujos, a.almacen, ahora, pagosDe));
        const viejo = !antes || ahora - antes.en > VIDA_REVISION_MS || antes.en > ahora;
        if (viejo || huella(antes) !== huella(nuevo)) a.almacen.resultado = nuevo;
        const guardar = a.leidas > 0 || a.fallos > 0 || a.almacen.pausa !== pausaAntes || a.almacen.resultado === nuevo;
        // guardo = lo leido quedo en KV (sin eso, la proxima consulta volveria a leer lo mismo: el refresco de los order bumps no insiste si es false)
        let guardo = cargado.leido;
        if (guardar && cargado.leido) guardo = await guardarAlmacen(env, a.almacen);
        cuerpo.guardo = guardo;
        a.almacen.resultado = nuevo;   // en la memoria del aislado, lo de ahora (en KV queda el de hace menos de VIDA_REVISION_MS si nada cambio)
        Object.assign(cuerpo, { almacen: a.almacen, pendientes: a.pendientes, revisionFallo: a.fallos > 0 || a.pausa === 'fallos' || a.pausa === 'permiso', pausa: a.pausa, leidasAhora: a.leidas });
      } catch (e) { cuerpo.revisionFallo = true; }
    }
    return cuerpo;
  } catch (e) {
    const motivo = clasificar(e);
    // tope del dia en la lista misma: la revision queda en pausa para todos (una escritura, y solo si no lo estaba), asi el aviso del menu no insiste
    if (motivo === 'limite') {
      try { const c2 = cargado || await cargarAlmacen(env); if (c2.leido && !enPausa(c2.almacen, ahora)) { c2.almacen.pausa = ahora + PAUSA_MS; c2.almacen.pausaPor = 'limite'; await guardarAlmacen(env, c2.almacen); } } catch (e2) { /* sin KV: la memoria del aislado espera 30 min igual */ }
    }
    return { ok: false, motivo, mensaje: MENSAJES[motivo] || MENSAJES.otro, detalle: sinTelefono(txt((e && e.message) || e, 160)) };
  }
}

// El aviso del menu y la caja de /fabrica. En orden: la memoria del aislado (si vale y trae la revision), el resultado guardado en KV (VIDA_REVISION_MS; 20 s mientras falten flujos por revisar) y, si
// no, una consulta. Con la revision en pausa no se le pregunta a ZapData. Si ZapData no contesta vale el ultimo resultado, con `aviso`, mientras tenga menos de VIEJO_MAX_MS: mas viejo ya no se
// muestra como si fuera de ahora.
const MENSAJE_PAUSA = { limite: MENSAJES.limite, permiso: MENSAJES.sin_lista, fallos: 'ZapData no dejó leer varios flujos seguidos; la revisión se retoma en una hora.' };
async function revisionAhora(env) {
  const ahora = Date.now();
  const deMemoria = memoria && memoria.cuerpo.ok && memoria.cuerpo.almacen && memoria.cuerpo.almacen.resultado && ahora - memoria.t <= vida(memoria.cuerpo) ? memoria.cuerpo : null;
  if (deMemoria) return { ok: true, r: deMemoria.almacen.resultado, pausa: deMemoria.pausa || '', fallo: !!deMemoria.revisionFallo };
  const { almacen } = await cargarAlmacen(env);
  const r = almacen.resultado, usable = r && ahora >= r.en && ahora - r.en < VIEJO_MAX_MS;
  const vidaR = r && !completa(r) ? VIDA_MAL_MS : VIDA_REVISION_MS;
  const pausa = enPausa(almacen, ahora) ? almacen.pausaPor || 'limite' : '';
  if (usable && ahora - r.en < vidaR) return { ok: true, r, pausa };
  if (pausa) return usable ? { ok: true, r, pausa, aviso: MENSAJE_PAUSA[pausa] } : { ok: false, motivo: pausa === 'limite' ? 'limite' : (pausa === 'permiso' ? 'sin_lista' : 'otro'), mensaje: MENSAJE_PAUSA[pausa] };
  const c = await estadoActual(env, null);
  if (!c.ok) return usable ? { ok: true, r, pausa: c.motivo === 'limite' ? 'limite' : '', aviso: c.mensaje } : c;
  if (!c.revisable) return { ok: false, motivo: 'sin_lista', mensaje: MENSAJES.sin_lista };
  if (!c.almacen || !c.almacen.resultado) return usable ? { ok: true, r, aviso: MENSAJES.revision } : { ok: false, motivo: 'otro', mensaje: MENSAJES.revision };
  return { ok: true, r: c.almacen.resultado, pausa: c.pausa || '', fallo: !!c.revisionFallo };
}

// ¿A la memoria le falta algo de lo que pide este pedido (sus Ventas o los Pagos de sus paises, sin revisar y sin un fallo reciente)? Con la revision en pausa, nada: no se le pregunta a ZapData.
function faltaPrioridad(c, prioridad) {
  if (!prioridad || !c || !c.ok || !c.revisable || !c.almacen) return false;
  const ahora = Date.now(), a = c.almacen;
  if (enPausa(a, ahora)) return false;
  if ((prioridad.ventas || []).some((id) => !a.ventas[id] && !falloReciente(a, id, null, ahora))) return true;
  const pagosDe = pagosPorPais(c.flujos);
  return (prioridad.paises || []).some((pais) => { const g = pagosDe(plano(pais)); return !!g && !a.pagos[g.id] && !falloReciente(a, g.id, g, ahora); });
}

// Lo que dice ZapData ahora: la memoria si todavia vale; si no, UNA consulta que comparten los pedidos que lleguen mientras tanto (como /api/zapdata-canales: si la de otro se cuelga, se da por perdida).
async function estadoActual(env, prioridad) {
  for (let intento = 0; intento < 3; intento++) {
    const ahora = Date.now();
    if (memoria && ahora - memoria.t <= vida(memoria.cuerpo) && !(ahora - memoria.t > VIDA_PRIORIDAD_MS && faltaPrioridad(memoria.cuerpo, prioridad))) return memoria.cuerpo;
    const viva = enCurso && ahora - enCurso.t < PLAZOS.consultaMs ? enCurso : null;
    let mia = null;
    if (!viva) {
      mia = { t: ahora, p: null };
      mia.p = consultar(env, prioridad).then((cuerpo) => { memoria = { t: Date.now(), cuerpo }; return cuerpo; }).finally(() => { if (enCurso === mia) enCurso = null; });
      enCurso = mia;
    }
    try { return await (mia ? mia.p : conPlazo(viva.p, PLAZOS.consultaMs)); } catch (e) { /* la espera se paso: se vuelve a mirar */ }
  }
  const cuerpo = await consultar(env, prioridad);
  memoria = { t: Date.now(), cuerpo };
  return cuerpo;
}

// Un error, sin el detalle tecnico salvo para el dueño (y sin nada de la memoria: ni flujos ni lo revisado).
function respuestaDeError(c, rol, ahora) {
  const o = { ok: false, ahora, motivo: c.motivo, mensaje: c.mensaje };
  if (rol === 'owner' && c.detalle) o.detalle = c.detalle;
  return o;
}

// GET /api/zapdata-flujos?bumps=1 con la llave CLAVE_REGLAS en la cabecera `x-clave-reglas`: el refresco de GitHub, cada hora (scripts/order-bumps-zapdata.cjs), para el aviso de «order bump sin
// medir» (2026-11-11). Sin sesion (llega de un servidor) y sin la llave, 401: la misma puerta que /api/reglas y /api/apimart-cuadre.
// Hace UNA consulta NUEVA a ZapData (no la memoria del aislado: el refresco quiere lo de ahora): la lista de flujos y, si falta, un paso de la revision (un flujo completo, el que cambio de ultimo
// primero; ver porLeer). El refresco la repite mientras queden `pendientes`.
//   -> { ok:true, ahora, total, leidas, pendientes, noLeidas, leidasAhora, pausa, revisionFallo, bumps:[{ venta, nombre, oferta, pais, paisNombre, etiqueta, precio, producto, bases, viejo }] }
//      leidasAhora = flujos completos leidos en ESTA pregunta: si es 0, preguntar otra vez no avanza nada (el refresco para; ver order-bumps-zapdata.cjs)
//   -> { ok:false, ahora, motivo, mensaje }   motivo 'pausa' = la revision esta en pausa (tope del dia de ZapData, permiso o fallos seguidos): ni se le pregunta a ZapData
// Lo que sale: nombres de flujos, su etiqueta «Pago Pendiente …», el producto del bump y montos, sin telefonos (sinTelefono). Nada de los datos de pago ni de los prompts (_order-bump-flujo.js).
async function bumpsParaElRefresco(request, env) {
  const ahora = new Date().toISOString();
  const llave = request.headers.get('x-clave-reglas') || '';
  if (!llave || !env.CLAVE_REGLAS || !safeEqual(llave, String(env.CLAVE_REGLAS))) return json(401, { ok: false, ahora, error: 'Llave incorrecta' });
  // Con la revision en pausa no se gasta ni la lista: con el tope del dia, ZapData cuenta tambien los intentos rechazados.
  const previo = await cargarAlmacen(env);
  // (la pausa por permiso no se arregla sola: sale como 'sin_lista', que pide reconectar)
  if (previo.leido && enPausa(previo.almacen, Date.now())) {
    return json(200, { ok: false, ahora, motivo: previo.almacen.pausaPor === 'permiso' ? 'sin_lista' : 'pausa', mensaje: MENSAJE_PAUSA[previo.almacen.pausaPor] || MENSAJES.limite });
  }
  const c = await consultar(env, null);
  if (!c.ok) return json(200, { ok: false, ahora, motivo: c.motivo, mensaje: c.mensaje });
  if (!c.revisable) return json(200, { ok: false, ahora, motivo: 'sin_lista', mensaje: MENSAJES.sin_lista });
  if (!c.almacen) return json(200, { ok: false, ahora, motivo: 'otro', mensaje: MENSAJES.revision });
  const r = bumpsGlobal(c.flujos, c.almacen, Date.now());
  // cola = lo que le queda a la revision entera por leer (Pagos incluidos): si no baja de una pregunta a la otra, no se avanzo (el refresco para).
  return json(200, { ok: true, ahora, total: r.total, leidas: r.leidas, pendientes: r.pendientes, noLeidas: r.noLeidas, leidasAhora: c.leidasAhora || 0, guardo: c.guardo !== false, cola: c.pendientes || 0,
    pausa: c.pausa || '', revisionFallo: !!c.revisionFallo,
    bumps: r.bumps.map((b) => Object.assign({}, b, { nombre: sinTelefono(b.nombre), oferta: sinTelefono(b.oferta), producto: sinTelefono(b.producto), etiqueta: sinTelefono(b.etiqueta) })) });
}

export async function onRequestGet(context) {
  const { request, env } = context;
  const u = new URL(request.url);
  if (u.searchParams.get('bumps') === '1') return bumpsParaElRefresco(request, env);
  const rol = await exigirLecturaEquipo(request, env);
  if (!rol) return json(401, { ok: false, error: 'Solo el equipo ve los flujos de ZapData' });
  const ahora = new Date().toISOString();
  if (u.searchParams.get('revision') === '1') {
    const x = await revisionAhora(env);
    if (!x.ok) return json(200, respuestaDeError(x, rol, ahora));
    const r = x.r;
    const o = { ok: true, ahora, revisadoEn: new Date(r.en).toISOString(), total: r.total, revisadas: r.revisadas, noLeidas: r.noLeidas || 0, completo: completa(r),
      limite: x.pausa === 'limite', revisionFallo: !!x.fallo || x.pausa === 'fallos' || x.pausa === 'permiso',
      // los nombres vienen de ZapData: sin telefonos completos (un flujo puede llevar un numero en el nombre)
      problemas: r.problemas.map((p) => Object.assign({}, p, { oferta: sinTelefono(p.oferta), pagosNombre: sinTelefono(p.pagosNombre) })),
      dudas: (r.dudas || []).map((p) => Object.assign({}, p, { oferta: sinTelefono(p.oferta) })) };
    if (x.aviso) o.aviso = x.aviso;
    return json(200, o);
  }
  const id = String(u.searchParams.get('pedido') || '').trim();
  if (!ID_PEDIDO.test(id)) return json(400, { ok: false, ahora, motivo: 'sin_pedido', mensaje: 'Falta el pedido de los flujos (o no tiene la forma de uno).' });
  let pedido = null;
  try { pedido = env.SUITE ? await env.SUITE.get('zapflow:' + id, { type: 'json' }) : null; } catch (e) { pedido = null; }
  if (!pedido || typeof pedido !== 'object') return json(200, { ok: false, ahora, motivo: 'sin_pedido', mensaje: MENSAJES.sin_pedido });
  const deEste = flujosDelPedido(pedido);
  const prioridad = { ventas: Object.keys(deEste).map((p) => deEste[p].venta).filter(Boolean), paises: Object.keys(deEste) };
  const c = await estadoActual(env, prioridad);
  if (!c.ok) return json(200, respuestaDeError(c, rol, ahora));
  // con una credencial que deja leer flujos completos, cada pais trae el inicio y la rama; si la revision fallo en esta consulta (sin almacen), van sin veredicto y con revisionFallo
  const conRevision = !!c.revisable, almacen = c.almacen || { ventas: {}, pagos: {}, fallos: {} };
  const apagados = new Set((c.flujos || []).filter((f) => f.activo === false).map((f) => f.id));
  const pagosDe = conRevision ? pagosPorPais(c.flujos) : null, ms = Date.now();
  const paises = {};
  Object.keys(deEste).forEach((pais) => {
    const par = deEste[pais], fila = {};
    ['venta', 'entrega'].forEach((tipo) => {
      const fid = par[tipo], z = fid && Object.prototype.hasOwnProperty.call(c.porId, fid) ? c.porId[fid] : null;
      fila[tipo] = { id: fid, activo: z ? z.activo : null, canales: z ? z.canales : null };
    });
    if (conRevision) {
      const ev = par.venta ? estadoVenta(c.flujos, almacen, par.venta, par.entrega, apagados, plano(pais), pagosDe, ms) : null;
      fila.inicio = ev ? ev.inicio : null;
      fila.pagos = { estado: ev ? ev.pagos : null, nombre: ev ? sinTelefono(txt(ev.pagosNombre, 60)) : '' };
      if (ev && ev.fallo) fila.fallo = true;   // ZapData no dejo leer la Venta o su flujo de Pagos hace poco: se intenta mas tarde
    }
    paises[pais] = fila;
  });
  const extra = conRevision ? { pendientes: c.pendientes || 0, revisionFallo: !!c.revisionFallo, limite: c.pausa === 'limite' } : {};
  return json(200, Object.assign({ ok: true, ahora, fuente: c.fuente, revisable: conRevision, paises }, extra));
}
