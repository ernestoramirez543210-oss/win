// La revision de lo que el tablero NO puede dejar listo solo en ZapData: el INICIO de cada flujo de Venta y la RAMA DE PAGOS de su oferta en el flujo de Pagos del pais.
//
// Por que existe (2026-11-06): la Venta de «Embarazo» en Colombia quedo ENCENDIDA, con numero y con trafico, pero sin la orden para Claude: su inicio seguia llamandose start-<fecha> (el borrador del
// tablero nace asi y, con ese nombre, ZapData abre la conversacion con la palabra clave pero no manda ningun mensaje; ver la skill arreglar-flujo-zapdata) y «Pagos Col» no tenia la rama de
// «Pago Pendiente Embarazo» (quien pagaba no recibia la entrega). Nada lo avisaba. ZapData NO deja arreglar ninguna de las dos cosas por su API (los flujos de Pagos son `editavel:false` y «o start
// pode ser reconectado mas nunca removido ou reconfigurado»): las sigue haciendo Claude con la orden de la Fabrica. Esto solo LEE (zapdata_flow_get) y dice si quedaron hechas.
//
// Como se lee sin pasarse de lo que da una Function gratuita (50 llamadas y ~10 ms de CPU por peticion):
//   - cada peticion lee como mucho MAX_LECTURAS flujo completo (medido el 2026-11-06: la Venta de «Embarazo» pesa 149 KB y «Pagos Col» 93 KB; cada lectura son dos JSON.parse, ~2,5 ms la Venta) y
//     prueba como mucho MAX_INTENTOS; lo que falta se lee en las siguientes (quien pregunta vuelve en 20 s mientras falte);
//   - lo leido se guarda en KV (`zapdata:revision`, UNA escritura y solo si algo cambio, releyendo antes para sumar lo que guardo otro aislado), con la fecha en que cambio cada flujo segun
//     zapdata_flow_list: un flujo se vuelve a leer solo si cambio (o, si no esta en la lista, cada 10 min mientras no este bien);
//     (scripts/probar-fabrica.mjs lo mide).
// ZapData tiene TOPES DIARIOS por credencial (ver ES_LIMITE en _zapdata.js): con el tope, la revision se pausa 1 h; un flujo que no se deja leer espera (1 h, 4 h, 16 h, 1 dia) en vez de reintentarse
// en cada peticion, y varios fallos seguidos pausan todo 1 h. Asi nunca se come el cupo con el que Ana crea flujos.
// Sin texto de terceros en el codigo de la pagina: lo que viaja son nombres de flujos (los pinta la pagina con esc()/textContent) y veredictos fijos.

import { leerFlujo, ES_LIMITE } from './_zapdata.js';
// Al leer cada Venta se saca tambien su order bump (precio, producto, precios base) para avisarle al dueno del que el dash no mide (2026-11-11; ver _order-bump-flujo.js y bumpsGlobal abajo).
import { bumpsDe, limpiarBumps, BUMP_V } from './_order-bump-flujo.js';

export const CLAVE_REVISION = 'zapdata:revision';
export const MAX_LECTURAS = 1;
export const MAX_INTENTOS = 2;
const RELEER_SIN_FECHA_MS = 10 * 60 * 1000;
const MAX_VENTAS = 400, MAX_PAGOS = 40, MAX_RAMAS = 500, MAX_FALLOS = 200, MAX_LISTA = 30;
const ESPERA_FALLO_MS = 60 * 60 * 1000, ESPERA_FALLO_MAX_MS = 24 * 60 * 60 * 1000, FALLOS_SEGUIDOS = 4;
export const PAUSA_MS = 60 * 60 * 1000;               // tope del dia de ZapData, o FALLOS_SEGUIDOS lecturas fallidas seguidas
export const PAUSA_PERMISO_MS = 6 * 60 * 60 * 1000;   // ZapData dice que la credencial no puede leer flujos completos: no se arregla solo
const ID_FLUJO = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FECHA = /^\d{4}-\d\d-\d\dT[\d:.+\-Z]{1,30}$/;
const MARCAS = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');
const NO_ASCII = new RegExp('[^' + String.fromCharCode(0) + '-' + String.fromCharCode(127) + ']');
const CONTROL = new RegExp('[' + String.fromCharCode(0) + '-' + String.fromCharCode(31) + String.fromCharCode(127) + ']', 'g');
const txt = (v, n) => String(v == null ? '' : v).replace(CONTROL, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
// Un texto que ya salio de txt(…, 80) (lo que se guardo): 1 a 80 caracteres, sin control y sin espacios en las puntas. Validarlo asi es una sola prueba en vez de tres reemplazos.
const LIMPIO_80 = new RegExp('^[^' + String.fromCharCode(0) + '-' + String.fromCharCode(32) + String.fromCharCode(127) + '](?:[^' + String.fromCharCode(0) + '-' + String.fromCharCode(31) +
  String.fromCharCode(127) + ']{0,78}[^' + String.fromCharCode(0) + '-' + String.fromCharCode(32) + String.fromCharCode(127) + '])?$');
const t80 = (v) => (typeof v === 'string' && LIMPIO_80.test(v) ? v : txt(v, 80));
// Lo que esta mal en la rama: falta (no hay condicion con esa etiqueta), suelta (la hay pero no esta conectada), otra (lleva a otro flujo), casi (solo coincide sin tildes ni mayusculas)
export const PROBLEMAS_PAGOS = ['falta', 'suelta', 'otra', 'casi'];
const PAGOS_VALIDOS = ['ok', 'sin-pagos', 'no-aplica'].concat(PROBLEMAS_PAGOS);
const DUDAS_VALIDAS = ['sin-pagos', 'no-aplica', 'sin-inicio'];
const PAUSAS_VALIDAS = ['limite', 'permiso', 'fallos'];

// Sin tildes, sin mayusculas y sin signos: «Pagos Col», «PAGOS  COL» y «pagos col.» son lo mismo. Con memoria (los mismos nombres pasan muchas veces por peticion) y sin normalize si ya es ASCII.
const MEMO_PLANO = new Map();
export function plano(s) {
  const k = String(s == null ? '' : s);
  const ya = MEMO_PLANO.get(k);
  if (ya !== undefined) return ya;
  let t = k.toLowerCase();
  if (NO_ASCII.test(t)) { try { t = t.normalize('NFD').replace(MARCAS, ''); } catch (e) { /* motor sin normalize: se queda con las tildes */ } }
  const r = t.replace(/[^a-z0-9]+/g, ' ').trim();
  if (MEMO_PLANO.size > 4000) MEMO_PLANO.clear();
  if (k.length <= 200) MEMO_PLANO.set(k, r);
  return r;
}

const PAISES = ['colombia', 'mexico', 'venezuela', 'argentina', 'bolivia', 'guatemala', 'ecuador', 'peru', 'chile', 'paraguay', 'uruguay', 'panama', 'honduras',
  'el salvador', 'nicaragua', 'costa rica', 'republica dominicana', 'puerto rico', 'espana', 'estados unidos'];
// «Venta Manteles de crohé Colombia» -> { oferta: 'Manteles de crohé', pais: 'colombia', paisNombre: 'Colombia' }. pais = '' si el nombre no termina en un pais conocido.
// Lo que a veces va despues del pais no cuenta: «Venta Lonchera Divertida Colombia v2», «… Colombia (cola)», «… Colombia 2».
const MEMO_PARTES = new Map();
export function partesDeVenta(nombre) {
  const k = String(nombre == null ? '' : nombre);
  const ya = MEMO_PARTES.get(k);
  if (ya) return ya;
  const n = txt(k, 120).replace(/^venta\s+/i, '').replace(/(\s+(v\d{1,2}|\d{1,2}|\([^()]{0,30}\)|copia|copy))+$/i, ''), p = plano(n);
  let r = { oferta: n, pais: '', paisNombre: '' };
  for (const pais of PAISES) {
    if (p === pais || p.endsWith(' ' + pais)) {
      const palabras = n.split(' '), c = pais.split(' ').length;
      r = { oferta: palabras.slice(0, Math.max(0, palabras.length - c)).join(' '), pais, paisNombre: palabras.slice(-c).join(' ') };
      break;
    }
  }
  if (MEMO_PARTES.size > 2000) MEMO_PARTES.clear();
  MEMO_PARTES.set(k, r);
  return r;
}

// Los flujos de Pagos de la lista, mirados UNA vez: -> (pais en plano) => su flujo de Pagos o null. «Pagos Col», «PAGOS BOLIVIA», «Pagos Mexico»…: el resto del nombre es el pais o su comienzo
// (3 letras o mas). Si hay varios, el encendido y, entre esos, el que cambio de ultimo.
export function pagosPorPais(flujos) {
  const cand = [];
  for (const f of flujos || []) {
    const m = f && /^pagos (.+)$/.exec(plano(f.nombre));
    if (m && m[1].length >= 3) cand.push({ f, r: m[1] });
  }
  cand.sort((a, b) => (b.f.activo === true) - (a.f.activo === true) || String(b.f.t || '').localeCompare(String(a.f.t || '')));
  const memo = new Map();
  return (pais) => {
    if (!pais) return null;
    if (!memo.has(pais)) { const x = cand.find((c) => pais === c.r || pais.startsWith(c.r)); memo.set(pais, x ? x.f : null); }
    return memo.get(pais);
  };
}
export function flujoDePagos(flujos, pais) { return pagosPorPais(flujos)(pais); }

// Las Ventas que estan atendiendo: encendidas, con palabra clave y con al menos un numero.
export function ventasEncendidas(flujos) {
  return (flujos || []).filter((f) => f && f.activo === true && /^venta\b/i.test(String(f.nombre || '')) && f.gatillo === 'keyword' && Array.isArray(f.canales) && f.canales.length > 0);
}

// El inicio de un flujo (de zapdata_flow_get, en referenceFlow.nodes): 'ok' = UN inicio y se llama start-1; 'mal' = se llama de otra forma o hay mas de uno; null = ZapData no lo trajo.
export function inicioDe(g) {
  const nodos = g && g.referenceFlow && Array.isArray(g.referenceFlow.nodes) ? g.referenceFlow.nodes : null;
  if (!nodos) return null;
  const inicios = nodos.filter((n) => n && typeof n === 'object' && (n.type === 'start' || /^start-/.test(String(n.id || ''))));
  if (!inicios.length) return null;
  return inicios.length === 1 && String(inicios[0].id) === 'start-1' ? 'ok' : 'mal';
}

// Las etiquetas «Pago Pendiente …» que pone la Venta (bloques `tag` que agregan), tal como estan escritas: con ella el flujo de Pagos sabe de que oferta es quien pago.
export function etiquetasPago(g) {
  const bloques = g && g.fluxo && Array.isArray(g.fluxo.blocks) ? g.fluxo.blocks : [];
  const out = [];
  for (const b of bloques) {
    if (!b || typeof b !== 'object' || b.type !== 'tag' || !b.data || typeof b.data !== 'object') continue;
    const accion = String(b.data.actionType || b.data.action || '');
    if (accion && !/add/i.test(accion)) continue;
    const e = txt(b.data.tagName, 80);
    if (/^pago pendiente\b/i.test(e) && out.indexOf(e) < 0) out.push(e);
    if (out.length >= 5) break;
  }
  return out;
}

// Las ramas del flujo de Pagos: cada condicion «tiene la etiqueta X», con su salida «si» yendo directo a un «ir a flujo».
//   e = la etiqueta, tal como esta escrita; d = el flujo al que lleva ('' si el «si» no va a un «ir a flujo»); c = la rama esta conectada (otro bloque lleva a su cadena, o es el primer bloque).
// ZapData recorre las condiciones en cadena (cada una sigue a la otra por su salida «no») y se queda con la PRIMERA que se cumple: si la misma etiqueta aparece dos veces en una cadena (un relanzamiento
// con la rama vieja arriba), la de abajo nunca se alcanza y no cuenta. Una condicion con varias reglas puede no cumplirse con la etiqueta sola: no tapa a las de abajo.
export function ramasDePagos(g) {
  const bloques = g && g.fluxo && Array.isArray(g.fluxo.blocks) ? g.fluxo.blocks.filter((b) => b && typeof b === 'object') : [];
  const porRef = new Map();
  bloques.forEach((b) => { if (b.ref != null && !porRef.has(String(b.ref))) porRef.set(String(b.ref), b); });
  const esCond = (b) => !!b && b.type === 'condition' && !!b.data && Array.isArray(b.data.conditions);
  const salida = (b, k) => (b.next && typeof b.next === 'object' && b.next[k] != null && typeof b.next[k] !== 'object' ? porRef.get(String(b.next[k])) || null : null);
  const deCadena = new Set(), deFuera = new Set();
  bloques.forEach((b) => {
    const nx = b.next && typeof b.next === 'object' ? b.next : {};
    Object.keys(nx).forEach((k) => {
      const destino = salida(b, k); if (!destino) return;
      if (k === 'no' && esCond(b) && esCond(destino)) deCadena.add(destino); else deFuera.add(destino);
    });
  });
  const primero = bloques[0] || null, out = [], vistos = new Set();
  const recorrer = (cabeza) => {
    const tapadas = new Set();
    let conectada = false;
    for (let b = cabeza, pasos = 0; esCond(b) && !vistos.has(b) && pasos < 2000; b = salida(b, 'no'), pasos++) {
      vistos.add(b);
      conectada = conectada || deFuera.has(b) || b === primero;
      const si = salida(b, 'yes');
      const d = si && si.type === 'goToFlow' && si.data && ID_FLUJO.test(String(si.data.targetFlowId || '')) ? String(si.data.targetFlowId).toLowerCase() : '';
      const reglas = b.data.conditions.filter((x) => x && typeof x === 'object');
      for (const x of reglas) {
        if (x.type !== 'tag' || (x.tagCondition && x.tagCondition !== 'has')) continue;
        const e = txt(x.tagName, 80);
        if (!e || tapadas.has(e)) continue;
        if (reglas.length === 1) tapadas.add(e);
        out.push({ e, d, c: conectada });
        if (out.length >= MAX_RAMAS) return true;
      }
    }
    return false;
  };
  for (const b of bloques) if (esCond(b) && !deCadena.has(b) && recorrer(b)) return out;
  for (const b of bloques) if (esCond(b) && !vistos.has(b) && recorrer(b)) return out;   // condiciones en un ciclo, sin cabeza
  return out;
}

// La rama de UNA etiqueta: 'ok' | 'falta' | 'casi' (no esta escrita igual: solo coincide sin tildes ni mayusculas, y ZapData puede no reconocerla) | 'suelta' (la hay, pero nada lleva a ella o su «si»
// no va a un flujo) | 'otra' (va a otro flujo: no a la Entrega que se esperaba o, sin saber cual es, a uno apagado).
// Las ramas de un flujo de Pagos se consultan una vez por cada Venta de su pais: se indexan una sola vez por etiqueta (y sin tildes, solo si hace falta).
const INDICES = new WeakMap();
function indiceRamas(lista) {
  let ix = INDICES.get(lista);
  if (!ix) {
    ix = { exactas: new Map(), planas: null };
    for (const x of lista) { if (!x) continue; const l = ix.exactas.get(x.e); if (l) l.push(x); else ix.exactas.set(x.e, [x]); }
    INDICES.set(lista, ix);
  }
  return ix;
}
export function veredictoRama(ramas, etiqueta, entregaId, apagados) {
  const e = t80(etiqueta), lista = Array.isArray(ramas) ? ramas : [], ix = indiceRamas(lista);
  const r = ix.exactas.get(e) || [];
  if (!r.length) {
    if (!ix.planas) ix.planas = new Set(lista.map((x) => (x ? plano(x.e) : '')));
    return ix.planas.has(plano(e)) ? 'casi' : 'falta';
  }
  const vivas = r.filter((x) => x.c && x.d);
  if (!vivas.length) return 'suelta';
  if (entregaId) return vivas.every((x) => x.d === entregaId) ? 'ok' : 'otra';
  return vivas.every((x) => !(apagados && apagados.has(x.d))) ? 'ok' : 'otra';
}

// Lo guardado en KV, validado (puede venir de una version vieja, de un error o de cualquier cosa).
//     resultado:{ en, total, revisadas, noLeidas, problemas:[…], dudas:[…] } | null }
//   pausa = hasta cuando no se le pregunta a ZapData (ms); fallos = flujos que no se dejaron leer (n veces, la ultima en `en`, con el flujo cambiado por ultima vez en `t`)
export function limpiarAlmacen(o) {
  const out = { v: 1, ventas: {}, pagos: {}, fallos: {}, seguidos: 0, resultado: null, pausa: 0, pausaPor: '' };
  if (!o || typeof o !== 'object' || o.v !== 1) return out;
  const fecha = (t) => (FECHA.test(String(t || '')) ? String(t) : '');
  const en = (n) => (Number.isFinite(Number(n)) && Number(n) > 0 ? Math.round(Number(n)) : 0);
  const fila = (p) => ({ venta: String(p.venta).toLowerCase(), oferta: txt(p.oferta, 80), pais: txt(p.pais, 40) });
  const deVentas = (l) => (Array.isArray(l) ? l : []).slice(0, MAX_LISTA).filter((p) => p && typeof p === 'object' && ID_FLUJO.test(String(p.venta || '')));
  const r = o.resultado;
  if (r && typeof r === 'object' && en(r.en)) {
    out.resultado = { en: en(r.en), total: Math.min(1000, en(r.total)), revisadas: Math.min(1000, en(r.revisadas)), noLeidas: Math.min(1000, en(r.noLeidas)),
      problemas: deVentas(r.problemas).map((p) => Object.assign(fila(p), { inicio: p.inicio === 'ok' || p.inicio === 'mal' ? p.inicio : null,
        pagos: PAGOS_VALIDOS.includes(p.pagos) ? p.pagos : null, pagosNombre: txt(p.pagosNombre, 60) })),
      dudas: deVentas(r.dudas).filter((p) => DUDAS_VALIDAS.includes(p.motivo)).map((p) => Object.assign(fila(p), { motivo: p.motivo })) };
  }
  out.pausa = en(o.pausa);
  out.pausaPor = PAUSAS_VALIDAS.includes(o.pausaPor) ? o.pausaPor : (out.pausa ? 'limite' : '');
  out.seguidos = Math.min(100, en(o.seguidos));
  const ids = (x, max) => (x && typeof x === 'object' ? Object.keys(x).filter((id) => ID_FLUJO.test(id)).slice(0, max) : []);
  const ventas = o.ventas, pagos = o.pagos, fallos = o.fallos;
  ids(ventas, MAX_VENTAS).forEach((id) => {
    const v = ventas[id]; if (!v || typeof v !== 'object') return;
    out.ventas[id.toLowerCase()] = { t: fecha(v.t), en: en(v.en), inicio: v.inicio === 'ok' || v.inicio === 'mal' ? v.inicio : null,
      etq: Array.isArray(v.etq) ? v.etq.slice(0, 5).map(t80).filter((x) => /^pago pendiente\b/i.test(x)) : [],
      b: limpiarBumps(v.b), bv: v.bv === BUMP_V ? BUMP_V : 0 };
  });
  const destinos = new Map();   // el mismo flujo de destino se repite en muchas ramas: se valida una vez
  const destino = (d) => { if (!d) return ''; let r = destinos.get(d); if (r === undefined) { r = ID_FLUJO.test(String(d)) ? String(d).toLowerCase() : ''; destinos.set(d, r); } return r; };
  ids(pagos, MAX_PAGOS).forEach((id) => {
    const x = pagos[id]; if (!x || typeof x !== 'object') return;
    const ramas = [];
    for (const z of Array.isArray(x.ramas) ? x.ramas.slice(0, MAX_RAMAS) : []) {
      if (!z || typeof z !== 'object' || typeof z.e !== 'string' || !z.e) continue;
      const e = t80(z.e);
      if (e) ramas.push({ e, d: destino(z.d), c: z.c === true });
    }
    out.pagos[id.toLowerCase()] = { t: fecha(x.t), en: en(x.en), ramas };
  });
  ids(fallos, MAX_FALLOS).forEach((id) => {
    const x = fallos[id]; if (!x || typeof x !== 'object' || !en(x.en)) return;
    out.fallos[id.toLowerCase()] = { t: fecha(x.t), en: en(x.en), n: Math.max(1, Math.min(10, en(x.n))) };
  });
  return out;
}

// ¿Este flujo fallo hace poco y todavia esta esperando? (f = el flujo en la lista de ZapData, si esta: si cambio desde el fallo, se vuelve a intentar ya)
export function falloReciente(almacen, id, f, ahora) {
  const x = almacen && almacen.fallos ? almacen.fallos[id] : null;
  if (!x) return false;
  if (f && f.t && x.t !== f.t) return false;
  return ahora - x.en < Math.min(ESPERA_FALLO_MAX_MS, ESPERA_FALLO_MS * Math.pow(4, x.n - 1));
}

// Lo que todavia hay que leer, en orden: lo del pedido que se esta mirando (sus Ventas y los Pagos de sus paises), despues los Pagos de los paises con Ventas encendidas y despues esas Ventas
// (la que cambio de ultimo primero: un lanzamiento nuevo se revisa antes que una oferta de hace un mes). Lo que fallo hace poco espera su turno (falloReciente).
export function porLeer(flujos, almacen, prioridad, ahora, pagosDe) {
  const lista = flujos || [], porId = new Map(lista.map((f) => [f.id, f]));
  const pagosDeP = pagosDe || pagosPorPais(lista);
  const vencida = (x) => ahora - (x.en || 0) > RELEER_SIN_FECHA_MS;
  const ventaPendiente = (id) => {
    const v = almacen.ventas[id], f = porId.get(id);
    if (falloReciente(almacen, id, f, ahora)) return false;
    if (!v) return true;
    if (v.bv !== BUMP_V) return true;                          // leida antes de que se buscara su order bump (o con otra version de esa lectura): una vez mas
    if (f && f.t) return v.t !== f.t;                          // cambio en ZapData (aunque ya estuviera bien: alguien pudo cambiar su etiqueta)
    return !(v.inicio === 'ok' && v.etq.length) && vencida(v);   // fuera de la lista: solo si todavia no estaba bien
  };
  const pagosPendiente = (f) => { const x = almacen.pagos[f.id]; return !falloReciente(almacen, f.id, f, ahora) && (!x || (f.t ? x.t !== f.t : vencida(x))); };
  const cola = [], ya = new Set();
  const poner = (tipo, id) => { if (!ya.has(id)) { ya.add(id); cola.push({ tipo, id }); } };
  const pv = prioridad && Array.isArray(prioridad.ventas) ? prioridad.ventas.filter((id) => ID_FLUJO.test(String(id || ''))).map((id) => String(id).toLowerCase()) : [];
  const pp = prioridad && Array.isArray(prioridad.paises) ? prioridad.paises.map(plano).filter(Boolean) : [];
  pv.forEach((id) => { if (ventaPendiente(id)) poner('venta', id); });
  pp.forEach((pais) => { const g = pagosDeP(pais); if (g && pagosPendiente(g)) poner('pagos', g.id); });
  const activas = ventasEncendidas(lista).slice().sort((a, b) => String(b.t || '').localeCompare(String(a.t || '')));
  [...new Set(activas.map((f) => partesDeVenta(f.nombre).pais).filter(Boolean))].forEach((pais) => { const g = pagosDeP(pais); if (g && pagosPendiente(g)) poner('pagos', g.id); });
  activas.forEach((f) => { if (ventaPendiente(f.id)) poner('venta', f.id); });
  return cola;
}

function podar(a) {
  const recortar = (o, max) => { const ks = Object.keys(o); if (ks.length <= max) return; ks.sort((x, y) => (o[y].en || 0) - (o[x].en || 0)).slice(max).forEach((k) => { delete o[k]; }); };
  recortar(a.ventas, MAX_VENTAS); recortar(a.pagos, MAX_PAGOS); recortar(a.fallos, MAX_FALLOS);
}

export const esLimite = (e) => (e && e.codigo === 'limite') || ES_LIMITE.test(String((e && e.message) || e || ''));
export const enPausa = (almacen, ahora) => !!almacen && almacen.pausa > ahora && almacen.pausa - ahora <= PAUSA_PERMISO_MS;

// Lo guardado en KV (`zapdata:revision`). leido = false si KV no contesto: entonces no se escribe (se pisaria lo que otros ya revisaron).
// El texto tal como estaba en KV queda en el almacen (sin salir en JSON): al guardar, si nadie escribio mientras tanto, no hay que volver a validarlo y sumarlo (es lo que mas CPU cuesta).
const CRUDO = Symbol('crudo');
const leerTexto = async (kv) => { const s = await kv.get(CLAVE_REVISION); return typeof s === 'string' ? s : null; };
const desdeTexto = (s) => { let o = null; try { o = s ? JSON.parse(s) : null; } catch (e) { o = null; } return limpiarAlmacen(o); };
export async function cargarAlmacen(env) {
  const kv = env && env.SUITE;
  if (!kv) return { almacen: limpiarAlmacen(null), leido: false };
  try { const s = await leerTexto(kv), almacen = desdeTexto(s); almacen[CRUDO] = s; return { almacen, leido: true }; } catch (e) { return { almacen: limpiarAlmacen(null), leido: false }; }
}

// UNA escritura. Se relee antes: si otro aislado guardo mientras tanto, se suma lo suyo (por flujo gana lo mas reciente) en vez de pisarlo. Sin una lectura buena no se escribe.
export async function guardarAlmacen(env, almacen) {
  const kv = env && env.SUITE;
  if (!kv || !almacen) return false;
  let s;
  try { s = await leerTexto(kv); } catch (e) { return false; }
  if (Object.prototype.hasOwnProperty.call(almacen, CRUDO) && s === almacen[CRUDO]) {
    podar(almacen);
    try { const nuevo = JSON.stringify(almacen); await kv.put(CLAVE_REVISION, nuevo); almacen[CRUDO] = nuevo; return true; } catch (e) { return false; }
  }
  const base = desdeTexto(s);
  ['ventas', 'pagos', 'fallos'].forEach((k) => { Object.keys(almacen[k] || {}).forEach((id) => { const a = almacen[k][id], b = base[k][id]; if (!b || (a.en || 0) >= (b.en || 0)) base[k][id] = a; }); });
  // un flujo que se leyo bien despues de su fallo ya no esta fallando (el fallo pudo venir de lo que guardo otro aislado)
  Object.keys(base.fallos).forEach((id) => { const ok = base.ventas[id] || base.pagos[id]; if (ok && ok.en >= base.fallos[id].en) delete base.fallos[id]; });
  if (almacen.resultado && (!base.resultado || almacen.resultado.en >= base.resultado.en)) base.resultado = almacen.resultado;
  if ((almacen.pausa || 0) >= (base.pausa || 0)) { base.pausa = almacen.pausa || 0; base.pausaPor = almacen.pausaPor || ''; }
  base.seguidos = almacen.seguidos || 0;
  podar(base);
  try { await kv.put(CLAVE_REVISION, JSON.stringify(base)); return true; } catch (e) { return false; }   // tope diario de escrituras: queda sin guardar y se vuelve a leer despues
}

// Un paso de la revision: lee en ZapData lo siguiente que falta (MAX_LECTURAS bien leidos, MAX_INTENTOS como mucho) y lo anota en `almacen` (no guarda: eso lo hace quien llama, con guardarAlmacen).
// Nunca escribe en ZapData.
//   -> { almacen, pendientes, leidas, fallos, pausa: ''|'limite'|'permiso'|'fallos' }
//   fallos = flujos que ZapData no dejo leer en esta vuelta (quedan esperando, ver falloReciente); pausa = la revision esta parada y por que
export async function avanzar(sesion, flujos, almacen, prioridad, ahora, pagosDe) {
  const cola = porLeer(flujos, almacen, prioridad, ahora, pagosDe);
  if (enPausa(almacen, ahora)) return { almacen, pendientes: cola.length, leidas: 0, fallos: 0, pausa: almacen.pausaPor || 'limite' };
  const porId = new Map((flujos || []).map((f) => [f.id, f]));
  let leidas = 0, fallos = 0, intentos = 0, pausa = '';
  for (const x of cola) {
    if (leidas >= MAX_LECTURAS || intentos >= MAX_INTENTOS) break;
    intentos++;
    const f = porId.get(x.id), t = f && FECHA.test(String(f.t || '')) ? f.t : '';
    let g;
    try { g = await leerFlujo(sesion, x.id); } catch (e) {
      if (esLimite(e)) { pausa = 'limite'; almacen.pausa = ahora + PAUSA_MS; almacen.pausaPor = pausa; break; }
      if (e && e.codigo === 'sin_permiso') { pausa = 'permiso'; almacen.pausa = ahora + PAUSA_PERMISO_MS; almacen.pausaPor = pausa; break; }
      fallos++;
      const antes = almacen.fallos[x.id];
      almacen.fallos[x.id] = { t, en: ahora, n: Math.min(10, (antes && antes.t === t ? antes.n : 0) + 1) };
      almacen.seguidos = (almacen.seguidos || 0) + 1;
      if (almacen.seguidos >= FALLOS_SEGUIDOS) { pausa = 'fallos'; almacen.pausa = ahora + PAUSA_MS; almacen.pausaPor = pausa; almacen.seguidos = 0; break; }
      continue;
    }
    almacen.seguidos = 0;
    delete almacen.fallos[x.id];
    if (x.tipo === 'venta') almacen.ventas[x.id] = { t, en: ahora, inicio: inicioDe(g), etq: etiquetasPago(g), b: bumpsDe(g), bv: BUMP_V };
    else almacen.pagos[x.id] = { t, en: ahora, ramas: ramasDePagos(g) };
    leidas++;
  }
  return { almacen, pendientes: Math.max(0, cola.length - leidas - fallos), leidas, fallos, pausa };
}

// El estado de UNA Venta: { inicio: 'ok'|'mal'|null, pagos: 'ok'|'falta'|'suelta'|'otra'|'casi'|'sin-pagos'|'no-aplica'|null, pagosNombre, fallo }
//   null = todavia no se reviso; 'sin-pagos' = no hay flujo de Pagos de ese pais entre los de ZapData; 'no-aplica' = la Venta no pone ninguna etiqueta «Pago Pendiente …»;
//   fallo = la Venta o su flujo de Pagos no se dejaron leer hace poco (estan esperando su turno).
//   entregaId: la Entrega que tiene que recibir la rama (la del pedido de la Fabrica); sin ella, vale cualquier flujo que no este apagado. pagosDe: lo de pagosPorPais, si ya se calculo.
export function estadoVenta(flujos, almacen, ventaId, entregaId, apagados, pais, pagosDe, ahora) {
  const t = ahora || Date.now();
  const v = almacen && almacen.ventas ? almacen.ventas[ventaId] : null;
  const g = (pagosDe || pagosPorPais(flujos))(pais);
  let pagos = null, fallo = !v && falloReciente(almacen, ventaId, null, t);
  if (v) {
    if (!v.etq.length) pagos = 'no-aplica';
    else if (!g) pagos = 'sin-pagos';
    else {
      const x = almacen.pagos[g.id];
      if (x) { const vs = v.etq.map((e) => veredictoRama(x.ramas, e, entregaId, apagados)); pagos = vs.find((z) => z !== 'ok') || 'ok'; }
      else if (falloReciente(almacen, g.id, g, t)) fallo = true;
    }
  }
  return { inicio: v ? v.inicio : null, pagos, pagosNombre: g ? txt(g.nombre, 60) : '', fallo };
}

// Lo que se le avisa al equipo: las Ventas ENCENDIDAS (con palabra clave y numero) con el inicio sin arreglar o sin una rama viva en el flujo de Pagos de su pais.
//   -> { total, revisadas, noLeidas, problemas: [{ venta, oferta, pais, inicio, pagos, pagosNombre }], dudas: [{ venta, oferta, pais, motivo }] }
//   revisadas = con veredicto completo; noLeidas = ZapData no dejo leer la Venta o su flujo de Pagos (esperan su turno); dudas = revisadas, sin nada mal, pero sin poder confirmar todo
//   (motivo 'sin-pagos': no esta el flujo de Pagos de su pais; 'no-aplica': la Venta no pone «Pago Pendiente…»; 'sin-inicio': ZapData no mostro su inicio). Lo que no esta en ninguna de las tres sigue en cola.
export function revisionGlobal(flujos, almacen, ahora, pagosDe) {
  const activas = ventasEncendidas(flujos);
  const apagados = new Set((flujos || []).filter((f) => f && f.activo === false).map((f) => f.id));
  const pagosDeP = pagosDe || pagosPorPais(flujos);
  const problemas = [], dudas = [];
  let revisadas = 0, noLeidas = 0;
  for (const f of activas) {
    const pz = partesDeVenta(f.nombre), r = estadoVenta(flujos, almacen, f.id, '', apagados, pz.pais, pagosDeP, ahora);
    const base = { venta: f.id, oferta: txt(pz.oferta || f.nombre, 80), pais: txt(pz.paisNombre, 40) };
    const malInicio = r.inicio === 'mal', malPagos = PROBLEMAS_PAGOS.includes(r.pagos);
    if (malInicio || malPagos) problemas.push(Object.assign(base, { inicio: r.inicio, pagos: r.pagos, pagosNombre: r.pagosNombre }));
    if (r.fallo && !malInicio && !malPagos) { noLeidas++; continue; }
    if (!(almacen && almacen.ventas[f.id]) || r.pagos === null) { if (malInicio) revisadas++; continue; }   // en cola (con el inicio mal ya se sabe que esta mal)
    revisadas++;
    if (malInicio || malPagos) continue;
    const motivo = r.pagos === 'sin-pagos' || r.pagos === 'no-aplica' ? r.pagos : (r.inicio !== 'ok' ? 'sin-inicio' : '');
    if (motivo) dudas.push(Object.assign(base, { motivo }));
  }
  return { total: activas.length, revisadas, noLeidas, problemas: problemas.slice(0, MAX_LISTA), dudas: dudas.slice(0, MAX_LISTA) };
}

// La revision ya dio una vuelta completa (todas las Ventas encendidas con veredicto o esperando por un fallo).
export const completa = (r) => !!r && (r.revisadas || 0) + (r.noLeidas || 0) >= (r.total || 0);

// Los order bumps de las Ventas ENCENDIDAS (con palabra clave y numero), para el aviso de «order bump sin medir» que arma el refresco (GET /api/zapdata-flujos?bumps=1).
//   -> { total, leidas, pendientes, noLeidas, bumps: [{ venta, nombre, oferta, pais, paisNombre, etiqueta, precio, producto, bases, viejo }] }
//   leidas = Ventas cuyo order bump ya se busco en su version de ahora; pendientes = las que faltan (nunca leidas, cambiaron en ZapData despues de leerlas, o leidas antes de esta lectura);
//   noLeidas = de esas, las que ZapData no dejo leer hace poco (esperan su turno). viejo = el bump es de una lectura anterior al ultimo cambio del flujo (se vuelve a leer).
//   precio = null cuando el texto no deja leerlo con seguridad (no se inventa).
export function bumpsGlobal(flujos, almacen, ahora) {
  const t = ahora || Date.now(), activas = ventasEncendidas(flujos), bumps = [];
  let leidas = 0, pendientes = 0, noLeidas = 0;
  for (const f of activas) {
    const v = almacen && almacen.ventas ? almacen.ventas[f.id] : null;
    const conBump = !!v && v.bv === BUMP_V, aldia = conBump && (!f.t || v.t === f.t);
    if (aldia) leidas++;
    else { pendientes++; if (falloReciente(almacen, f.id, f, t)) noLeidas++; }
    if (!conBump || !v.b.length) continue;
    const pz = partesDeVenta(f.nombre);
    for (const b of v.b) {
      if (bumps.length >= 60) break;
      bumps.push({ venta: f.id, nombre: txt(f.nombre, 120), oferta: txt(pz.oferta || f.nombre, 80), pais: pz.pais, paisNombre: txt(pz.paisNombre, 40), etiqueta: v.etq[0] || '',
        precio: b.p, producto: b.pr, bases: b.bs.slice(), viejo: !aldia });
    }
  }
  return { total: activas.length, leidas, pendientes, noLeidas, bumps };
}
