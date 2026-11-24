// El ejecutor automatico de las reglas: aplica en Facebook lo que las reglas activas deciden, sin que nadie lo apruebe a mano.
//
//   GET  /api/reglas-ejecutar -> { ok, ahora, memoria, autoHoy, tope }      (memoria = lo ya aplicado y hasta cuando no se repite; la lee scripts/reglas-auto.cjs antes de evaluar)
//   POST /api/reglas-ejecutar { acciones:[...], numeros:{ capturadoEn, instancias:[{ wpp, pais, estado, id }] } } -> { ok, resultados:[ { clave, estado:'hecha'|'omitida'|'aplazada'|'fallo', motivo, aviso, efectivo, despues, copia } ] }
//
// UNA sola puerta: el refresco de GitHub (scripts/reglas-auto.cjs --ejecutar, despues de publicar) con la llave CLAVE_REGLAS en la cabecera `x-clave-reglas`. Las personas siguen aplicando a mano
// desde /reglas con su contraseña de administrador por /api/toggle-ad, como siempre; este archivo no tiene puerta de sesion.
//
// Las condiciones de la regla (gasto, ventas, ROAS...) las evalua la pagina de /reglas con los numeros del dash, no este servidor. Por eso aqui NO se confia en lo que llega: cada accion se
// vuelve a comprobar contra las reglas que estan guardadas AHORA, y contra Facebook, antes de tocar nada:
//   1. la regla existe, esta encendida y dentro de sus fechas y de su horario del dia; el pais de la campaña esta en su alcance; la campaña esta asignada a la regla (o entra por «todas» / «futuras»);
//   2. la regla tiene de verdad esa accion (una llave filtrada no puede pedir «subir presupuesto» con una regla que solo pausa);
//   3. si es presupuesto, el monto sale de aplicar la formula de la regla (+20 %, tope, piso, fijar...) al presupuesto que Facebook tiene AHORA: no se acepta un monto cualquiera;
//   4. el objeto sigue como lo vio la regla -pertenece a esa campaña, sigue encendido o pausado, el presupuesto no cambio- (condiciones previas de /api/toggle-ad: se lee Facebook justo antes);
//   5. no se repite lo ya aplicado: la memoria es el registro compartido (reg:..., lo escribe /api/toggle-ad), con la frecuencia de la accion y minimo 24 h. Excepcion: PAUSAR se espacia solo lo que la regla pide
//      («Una vez al día»...); con «Cada vez que se cumple» vuelve a pausar lo que alguien volvio a encender (no gasta, y Facebook se lee antes de escribir: lo ya pausado se omite);
//   6. topes: pocas acciones por llamada y un maximo de cambios automaticos por dia (si algo se desbocara, se detiene solo);
//   7. lo que sube gasto (encender, presupuesto) se detiene si el registro no se puede guardar: sin memoria no hay forma de no repetirlo. Pausar siempre sigue (es idempotente y va hacia el lado seguro).
//   8. lo que sube gasto en una campaña de ZapData exige que su numero de WhatsApp este CONECTADO, lo diga o no la regla: se le pregunta a ZapData en vivo justo antes (ver frenoNumero). Pausar y bajar nunca se frenan.
// Lo que sale en Facebook queda ademas en el registro con la regla, los numeros exactos y `via: 'auto'`.
//
// «Solo avisar» no se ejecuta: no cambia nada en Facebook.
//
// Para cortarlo de golpe: borrar el secreto REGLAS_CLAVE en GitHub (el refresco deja de leer las reglas y de ejecutar) o la variable CLAVE_REGLAS en Cloudflare Pages. Para frenar una regla: apagarla en /reglas.

import { safeEqual } from '../_sesion.js';
import { onRequestPost as toggle } from './toggle-ad.js';
import { canalesAhora } from './zapdata-canales.js';

const CLAVE_REGLAS = 'reglas:config';
const MAX_POR_LLAMADA = 5;           // pocas por peticion: cada accion hace varias llamadas a Facebook y a KV (ver PRESUPUESTO_LLAMADAS)
const PRESUPUESTO_LLAMADAS = 44;     // el plan gratis de Cloudflare permite 50 subrequests por peticion; lo que no cabe se APLAZA y el refresco lo manda en otra llamada
const COSTO = { pausar: 8, reactivar: 8, subir: 9, bajar: 9, fijar: 9, duplicar: 26 };   // llamadas aproximadas de cada tipo (duplicar = copia + presupuesto de la copia + pausar el original)
const TOPE_DIA = 200;                // cambios automaticos por dia (hora de Colombia)
const FICHAS_POR_LLAMADA = 300;      // lo ya aplicado se lee de las 300 lineas mas nuevas del registro: decodificarlas cuesta ~1 ms de CPU (con 1.000, ~3 ms) y el plan gratis de Cloudflare da 10 ms por peticion
const FICHAS_MEMORIA = 1000;         // el refresco las pide todas UNA vez por corrida (GET), para no proponer lo que ya se aplico
const DIA = 864e5;
const NIVELES = ['campaign', 'adset', 'ad'];
const TIPOS = ['pausar', 'reactivar', 'subir', 'bajar', 'fijar', 'duplicar'];
const SUBE_GASTO = ['reactivar', 'subir', 'fijar', 'duplicar'];      // lo que puede aumentar lo que se gasta (una copia nace pausada, pero se cuenta igual: gasta si alguien la enciende)
const COSTO_NUMEROS = 5;             // preguntarle a ZapData por sus numeros: la credencial (KV), abrir la sesion MCP (2 llamadas), la lista (1) y, si toca, renovar el token (1). Una vez por llamada
const NUM_EDAD_MAX = 3 * 36e5;       // la lista de numeros que manda el refresco vale 3 h, igual que RG_NUM_EDAD_MAX de la pantalla
// La frecuencia de cada accion tal como la elige el usuario en el editor (la misma tabla que RG_CADA_MS de templates/reglas-modulo.js y CADA de reglas.js): ms entre dos aplicaciones.
// 0 = «Cada vez que se cumple»; -1 = «Una sola vez». Un valor desconocido se trata como «cada vez», igual que al guardar la regla.
const FRECUENCIA_MS = { siempre: 0, una_vez: -1, '4h': 4 * 36e5, '6h': 6 * 36e5, '12h': 12 * 36e5, dia: DIA, '2dias': 2 * DIA, '3dias': 3 * DIA };
const frecuenciaMs = (cada) => (Object.prototype.hasOwnProperty.call(FRECUENCIA_MS, cada) ? FRECUENCIA_MS[cada] : 0);
const ID_FB = /^\d{5,20}$/;
const PAIS = /^[a-z]{3,20}$/;

const json = (s, o) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
const texto = (v, n) => String(v == null ? '' : v).replace(/\s+/g, ' ').trim().slice(0, n);
const numero = (v) => { const x = Number(v); return v != null && v !== '' && isFinite(x) ? x : null; };

// ---- el dia de Colombia (UTC-5, sin horario de verano) ----
const hoyCO = (ms) => new Date((ms == null ? Date.now() : ms) - 5 * 36e5).toISOString().slice(0, 10);
const inicioDiaCO = (ms) => { const d = (ms == null ? Date.now() : ms) - 5 * 36e5; return Math.floor(d / DIA) * DIA + 5 * 36e5; };
// «a las 3:17 p. m.», «ayer a las 10:05 a. m.» o «el 12/10 a las 9:00 p. m.» (hora de Colombia): para decir cuando se aplico antes algo que se repite.
const horaCO = (ms) => { const d = new Date(ms - 5 * 36e5), h = d.getUTCHours(); return ((h % 12) || 12) + ':' + String(d.getUTCMinutes()).padStart(2, '0') + (h < 12 ? ' a. m.' : ' p. m.'); };
const cuandoCO = (t, ahora) => {
  const dia = hoyCO(t);
  return (dia === hoyCO(ahora) ? 'a las ' : dia === hoyCO(ahora - DIA) ? 'ayer a las ' : 'el ' + dia.slice(8) + '/' + dia.slice(5, 7) + ' a las ') + horaCO(t);
};

// ---- las cuentas de la regla son las mismas que hace la pantalla (templates/reglas-modulo.js): fechas, pais, asignacion, formula del presupuesto ----
function enFechas(r, ms) {
  const p = r.programa || {}, hoy = hoyCO(ms);
  return (!p.desde || hoy >= p.desde) && (!p.hasta || hoy <= p.hasta);
}
// El horario del dia de la regla (programa.horaDesde 0-23 / horaHasta 1-24, hora de Colombia): trabaja desde horaDesde:00 hasta antes de horaHasta:00 y cruza la medianoche si horaDesde > horaHasta.
// Sin las dos horas validas (enteras, distintas, no de 0 a 24) no hay horario. Misma cuenta que rgEnHorario de la pantalla y enHorario de scripts/reglas-auto.cjs (probar-programa.cjs las compara).
const horaNumCO = (ms) => new Date((ms == null ? Date.now() : ms) - 5 * 36e5).getUTCHours();
function horarioDe(p) {
  p = p || {};
  const hora = (v) => ((typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) ? Number(v) : NaN);      // solo numeros o textos con un numero («17»): ni true/false, ni null, ni vacios
  const d = hora(p.horaDesde); let h = hora(p.horaHasta);
  if (h === 0) h = 24;
  return Number.isInteger(d) && Number.isInteger(h) && d >= 0 && d <= 23 && h >= 1 && h <= 24 && d !== h && !(d === 0 && h === 24) ? { d, h } : null;
}
function enHorario(r, ms) {
  const w = horarioDe(r.programa); if (!w) return true;
  const x = horaNumCO(ms);
  return w.d < w.h ? (x >= w.d && x < w.h) : (x >= w.d || x < w.h);
}
function paisOk(r, c) { const p = (r.aplica || {}).paises; return !p || !p.length || p.indexOf(c) !== -1; }
function filtroCoincide(filtro, nombre) {
  const filas = (filtro && filtro.filas) || []; if (!filas.length) return true;
  const n = String(nombre || '').toLowerCase();
  const una = (f) => { const v = String(f.valor || '').toLowerCase(); return f.op === 'no_contiene' ? n.indexOf(v) === -1 : f.op === 'empieza' ? n.indexOf(v) === 0 : f.op === 'es' ? n === v : n.indexOf(v) !== -1; };
  return filtro.op === 'or' ? filas.some(una) : filas.every(una);
}
function asignada(r, campanaId, campanaNombre) {
  const a = r.aplica || {};
  if (a.todas) return true;
  if ((a.campanas || []).indexOf(campanaId) !== -1) return true;
  return !!a.futuras && (a.base || []).indexOf(campanaId) === -1 && filtroCoincide(a.filtro, campanaNombre);
}
function nuevoPresupuesto(a, actual) {
  const v = Number(a.valor) || 0;
  if (a.tipo === 'fijar') return Math.max(1, Math.round(v));
  if (a.tipo === 'subir') {
    const x = a.modo === 'abs' ? actual + v : actual * (1 + v / 100);
    const n = Math.round(x); return a.tope > 0 ? Math.min(n, a.tope) : n;
  }
  const x = a.modo === 'abs' ? actual - v : actual * (1 - v / 100);
  return Math.max(a.piso > 0 ? a.piso : 1, Math.round(x));
}
// Presupuesto con el que nace la copia al duplicar (0 = el mismo del original): igual que en rgEvaluar.
function presupuestoCopia(pc, actual) {
  if (!(actual > 0) || !pc || (pc.modo !== 'pct' && pc.modo !== 'fijar')) return 0;
  return pc.modo === 'fijar' ? Math.round(Number(pc.valor)) : Math.round(actual * (1 + Number(pc.valor) / 100));
}
const accionesDe = (r, tipo) => [].concat.apply([], (r.tareas || []).map((t) => (t.acciones || []).filter((a) => a.tipo === tipo).map((a) => ({ a, nivel: t.nivel }))));

// ---- el numero de WhatsApp de la campaña: lo que sube gasto no se hace con el numero caido ----
// Pedido del dueño (2026-11-04): «puede pasar que algunas reglas se activen cuando el número está bloqueado… estaría activando un número ya bloqueado y perdemos dinero». La condicion «Número de WhatsApp»
// de la regla (2026-10-27) solo protege a las reglas que la llevan, y mira la lista que bajo el refresco con la API publica de ZapData, que tarda en ver un numero que WhatsApp ya bloqueo. Aqui va, para
// TODAS las reglas, un freno que no depende de como se escribio la regla: antes de reactivar, subir o fijar presupuesto o duplicar una campaña de ZapData, se le pregunta a ZapData EN VIVO
// (zapdata_channel_list por la conexion OAuth del tablero, la misma de /numeros: canalesAhora, cache de 2 min) si su numero esta conectado. Si no lo esta, o no se puede saber, no se hace.
//
// El numero sale del NOMBRE de la campaña: misma cuenta que rgNumeroDeCampana de templates/reglas-modulo.js (scripts/probar-freno-numero.mjs compara las dos copias). La conexion de ZapData de ese numero
// sale de la lista que manda el refresco (`numeros`, de data/zapdata-instancias.json: wpp, pais, estado, id = channel_id): ZapData en vivo llama a sus conexiones por el nombre de perfil, no por «WPP #N»,
// y lo unico que las une es ese id. Si ZapData en vivo no contesta o no trae ese id, vale la lista del refresco (de esta misma corrida) mientras tenga menos de 3 h. Sin ninguna de las dos, no se hace.
// La lista llega del refresco, que es quien tiene la llave: no es una puerta nueva (lo peor que puede hacer una lista falsa es dejar pasar lo que la regla ya decidio, igual que antes de este freno).
const MARCAS = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');
export function numeroDeCampana(nombre) {
  const orig = String(nombre == null ? '' : nombre);
  let t = orig.toUpperCase();
  try { t = t.normalize('NFD'); } catch (e) { /* motor sin normalize: se queda con las tildes */ }
  t = t.replace(MARCAS, '');
  if (/\b(?:CLICC?\s*-?\s*CHAT|CLICHAT)\b/.test(t)) return '';
  if (!(t.indexOf('ZAPDATA') !== -1 || /\bZAPD?\b/.test(t))) return '';
  const m = /#\s*(\d+)/.exec(orig) || /WPP\s*(\d+)/i.exec(orig) || /\bZAP\s+(\d+)\s*\|/i.exec(orig);
  return m ? String(Number(m[1])) : '';
}
// La lista del refresco, limpia: solo lo que hace falta para decidir (nada de nombres ni telefonos).
export function limpiarNumeros(n) {
  const instancias = [];
  for (const i of (n && Array.isArray(n.instancias) ? n.instancias : []).slice(0, 100)) {
    if (!i || typeof i !== 'object') continue;
    const wpp = String(i.wpp == null ? '' : i.wpp).replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 4), pais = String(i.pais || '').toLowerCase(), id = String(i.id || '').trim().toLowerCase();
    if (!wpp || !PAIS.test(pais)) continue;
    instancias.push({ wpp, pais, estado: i.estado === 'connected' ? 'connected' : 'disconnected', id: /^[0-9a-f-]{16,64}$/.test(id) ? id : '' });
  }
  const cap = Date.parse(String((n && n.capturadoEn) || ''));
  return { instancias, capturadoEn: cap > 0 ? cap : 0 };
}
// Los motivos de functions/api/zapdata-canales.js, dichos para quien lee el registro.
const POR_QUE_VIVO = { sin_conexion: 'el dash no tiene la conexión con ZapData', sin_permiso: 'a la conexión le falta un permiso', vencida: 'la conexión se venció', red: 'sin red', limite: 'ZapData llegó a su tope de consultas del día', otro: 'respuesta inesperada' };
// ¿Se puede subir el gasto de una campaña que manda al numero `num` del pais `pais`? { ok, motivo?, nota? }. `ctx.numeros` = la lista del refresco; `ctx.canales` = lo que contesto ZapData en vivo.
// El #N es unico en toda la cuenta: si el pais de la campaña no lo tiene y otro pais si (el #9 paso de Colombia a Bolivia), vale ese, mientras haya uno solo (igual que rgNumeros de la pantalla).
export function frenoNumero(num, pais, ctx) {
  const N = ctx.numeros || { instancias: [], capturadoEn: 0 }, etq = 'WPP #' + num;
  let suyas = N.instancias.filter((i) => i.wpp === num && i.pais === pais);
  if (!suyas.length) { const todas = N.instancias.filter((i) => i.wpp === num); if (todas.length === 1) suyas = todas; }
  const vivo = ctx.canales;
  if (vivo && vivo.ok && suyas.length && suyas.every((i) => i.id)) {
    const vistas = suyas.map((i) => vivo.canales.filter((c) => c.id === i.id)[0]);
    if (vistas.every(Boolean)) {
      if (vistas.every((c) => c.estado === 'connected')) return { ok: true };
      const t = Date.parse(vivo.ahora);
      return { ok: false, motivo: 'el número ' + etq + ' está desconectado en ZapData (consultado en vivo' + (t > 0 ? ' ' + cuandoCO(t, ctx.ahora) : '') + '): no se enciende ni se sube su gasto hasta que se reconecte' };
    }
  }
  const porque = vivo && vivo.ok ? 'ZapData en vivo no trae esa conexión' : 'ZapData en vivo no contestó' + (vivo && POR_QUE_VIVO[vivo.motivo] ? ': ' + POR_QUE_VIVO[vivo.motivo] : '');
  if (!suyas.length) return { ok: false, motivo: 'la campaña manda al número ' + etq + ' y ese número no está en la lista de ZapData: no se sube su gasto sin saber si está conectado' };
  const cap = N.capturadoEn, edad = ctx.ahora - cap;
  if (!(cap > 0) || edad > NUM_EDAD_MAX || edad < -10 * 6e4) {
    return { ok: false, motivo: 'no se pudo comprobar el número ' + etq + ': ' + porque + ' y la lista del refresco ' + (cap > 0 ? 'es de hace ' + Math.round(edad / 36e5) + ' h' : 'no llegó') };
  }
  if (suyas.every((i) => i.estado === 'connected')) return { ok: true, nota: 'número ' + etq + ' conectado según la lista del refresco de ' + cuandoCO(cap, ctx.ahora).replace(/^a las /, 'las ') + ' (' + porque + ')' };
  return { ok: false, motivo: 'el número ' + etq + ' estaba desconectado en la lista del refresco de ' + cuandoCO(cap, ctx.ahora).replace(/^a las /, 'las ') + ' (' + porque + '): no se enciende ni se sube su gasto' };
}
async function consultarNumeros(env) {
  try { const c = await canalesAhora(env); return c && typeof c === 'object' ? c : { ok: false, motivo: 'otro' }; } catch (e) { return { ok: false, motivo: 'otro' }; }
}

// ---- la memoria: el registro compartido que escribe /api/toggle-ad ----
// { memoria: { clave -> { t, ms } }, autoHoy: cambios automaticos de hoy, ok: se pudo leer }. Las claves «una sola vez» tienen ademas su propia marca permanente (uno:<clave>): el registro solo
// alcanza a las lineas mas nuevas y una copia hecha hace meses no debe volver a hacerse.
async function leerMemoria(env, ahora, limite) {
  const mem = {}; let autoHoy = 0;
  try {
    const lista = await env.SUITE.list({ prefix: 'reg:', limit: limite || FICHAS_POR_LLAMADA });
    const desde = inicioDiaCO(ahora);
    lista.keys.forEach((k) => {
      const x = k.metadata; if (!x || typeof x !== 'object' || !x.ok) return;
      if (x.clave && (!mem[x.clave] || mem[x.clave].t < x.t)) mem[x.clave] = { t: x.t, ms: x.ms == null ? DIA : x.ms };
      if (x.via === 'auto' && x.t >= desde) autoHoy++;
    });
    return { memoria: mem, autoHoy, ok: true };
  } catch (e) { return { memoria: mem, autoHoy, ok: false }; }
}
// ¿Ya se aplico dentro de su ventana? ms < 0 = para siempre. Igual que rgYaHecha de la pantalla.
function yaHecha(mem, clave, ventana, ahora) {
  const m = mem[clave]; if (!m) return false;
  return m.ms < 0 || ventana === Infinity || ahora - m.t < Math.max(m.ms, ventana || 0);
}
// Lo mismo para PAUSAR: lo ya aplicado solo cuenta si la regla pide espaciarlo. `cada` = lo que dice la regla guardada (0 = «cada vez que se cumple», -1 = «una sola vez»). No se mira el `ms` que quedo en la ficha:
// las versiones anteriores lo escribian con un piso de 24 h aunque la regla dijera «cada vez», y asi una campaña que alguien volvio a encender no se volvia a pausar.
function yaPausada(mem, clave, cada, ahora) {
  const m = mem[clave]; if (!m) return false;
  return cada < 0 || ahora - m.t < cada;
}

// Una llamada a /api/toggle-ad hecha por el servidor mismo, con la contraseña de administrador que vive aqui (nunca sale). Es la unica puerta a Facebook: el mismo codigo que usan los botones
// del dash, con su lectura de vuelta, sus avisos y su registro.
async function aFacebook(env, cuerpo) {
  const r = await toggle({ request: new Request('https://interno.invalid/api/toggle-ad', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ password: env.ADMIN_PASSWORD }, cuerpo)) }), env });
  let j = {}; try { j = await r.json(); } catch (e) { /* sin cuerpo */ }
  return { status: r.status, j };
}
const motivoDe = (d) => texto((d.j && (d.j.error || d.j.motivo)) || ('Facebook respondio ' + d.status), 200);

// Valida y aplica UNA accion. Devuelve { clave, estado, motivo, ... }.
async function aplicar(a, ctx) {
  const env = ctx.env;
  const omitir = (motivo) => ({ clave: a.clave, estado: 'omitida', motivo });
  const fallar = (motivo) => ({ clave: a.clave, estado: 'fallo', motivo });
  // --- forma ---
  if (TIPOS.indexOf(a.tipo) === -1) return omitir(a.tipo === 'avisar' ? 'solo avisar no cambia nada en Facebook' : 'tipo de accion desconocido');
  if (NIVELES.indexOf(a.nivel) === -1) return omitir('nivel desconocido');
  if (!ID_FB.test(a.id) || !ID_FB.test(a.campanaId) || (a.cuenta && !/^(act_)?\d{5,20}$/.test(a.cuenta))) return omitir('ids invalidos');
  if (!a.clave) return omitir('sin clave de accion');
  if (ctx.vistas.has(a.clave)) return omitir('repetida en la misma llamada');
  ctx.vistas.add(a.clave);
  // --- la regla, tal como esta guardada ahora ---
  const r = ctx.reglas.filter((x) => x && x.id === a.reglaId)[0];
  if (!r) return omitir('la regla ya no existe');
  if (r.on !== true) return omitir('la regla esta apagada');
  if (!enFechas(r, ctx.ahora)) return omitir('la regla esta fuera de sus fechas');
  if (!enHorario(r, ctx.ahora)) return omitir('la regla esta fuera de su horario');
  if (!PAIS.test(a.pais) || !paisOk(r, a.pais)) return omitir('el pais de la campaña no esta en el alcance de la regla');
  if (!asignada(r, a.campanaId, a.campana)) return omitir('la campaña ya no esta asignada a la regla');
  const tiene = accionesDe(r, a.tipo);
  if (!tiene.length) return omitir('la regla no tiene la accion «' + a.tipo + '»');
  const esPresupuesto = a.tipo === 'subir' || a.tipo === 'bajar' || a.tipo === 'fijar';
  if (esPresupuesto && a.nivel === 'ad') return omitir('un anuncio no tiene presupuesto');
  if (a.tipo === 'duplicar' && a.nivel === 'ad') return omitir('un anuncio suelto no se duplica');
  if (!esPresupuesto && !tiene.some((x) => x.nivel === a.nivel)) return omitir('la regla no tiene esa accion a nivel de ' + a.nivel);
  // --- el presupuesto sale de la formula de la regla, no de lo que llega ---
  let nuevo = null, copiaNuevo = 0, def = tiene[0].a;
  if (esPresupuesto) {
    const actual = numero(a.actual), pedido = numero(a.nuevo);
    if (!(actual > 0) || !(pedido >= 1)) return omitir('falta el presupuesto actual o el nuevo');
    const hit = tiene.filter((x) => Math.abs(nuevoPresupuesto(x.a, actual) - pedido) <= 1)[0];
    if (!hit) return omitir('el presupuesto pedido (' + pedido + ') no sale de la formula de la regla aplicada a ' + actual);
    nuevo = pedido; def = hit.a;
  } else if (a.tipo === 'duplicar') {
    const actual = numero(a.actual) || 0, pedido = numero(a.copiaNuevo) || 0;
    const hit = tiene.filter((x) => { const c = presupuestoCopia(x.a.presupuestoCopia, actual); return pedido === 0 ? c === 0 : Math.abs(c - pedido) <= 1; })[0];
    if (!hit) return omitir('el presupuesto de la copia no sale de la regla');
    copiaNuevo = pedido; def = hit.a;
  }
  // --- memoria y topes ---
  // PAUSAR no lleva el piso de 24 h entre dos aplicaciones: no puede gastar mas y, si ya esta pausado, Facebook se lee antes de escribir y se omite (verificarPrevia en toggle-ad.js). Se espacia solo lo que la
  // regla GUARDADA pide («Una vez al día», «Una sola vez»...), no lo que llega: con «Cada vez que se cumple» se vuelve a pausar lo que alguien volvio a encender. Presupuestos, duplicar y reactivar (lo que sube gasto)
  // conservan el piso de 24 h: repetir una subida compone el aumento, y no se pelea con quien pauso a mano.
  const cadaPausa = a.tipo === 'pausar' ? frecuenciaMs((tiene.filter((x) => x.nivel === a.nivel)[0] || tiene[0]).a.cada) : null;
  const ms = cadaPausa != null ? cadaPausa : numero(a.ms), siempre = ms != null && ms < 0, ventana = siempre ? Infinity : Math.max(ms || 0, DIA), msReg = siempre ? -1 : (cadaPausa != null ? ms : Math.max(ms || 0, DIA));
  const cuandoYa = ctx.mem[a.clave] && ctx.mem[a.clave].t > 0 ? cuandoCO(ctx.mem[a.clave].t, ctx.ahora) : 'hace poco';       // «a las 3:17 p. m.»: asi quien lee el registro sabe cuando fue
  if (cadaPausa != null) { if (yaPausada(ctx.mem, a.clave, cadaPausa, ctx.ahora)) return omitir('ya se aplicó ' + cuandoYa + ' (no se repite hasta pasada su frecuencia)'); }
  else if (yaHecha(ctx.mem, a.clave, ventana, ctx.ahora)) return omitir('ya se aplicó ' + cuandoYa + ' (no se repite hasta pasada su frecuencia)');
  if (siempre) { let marca = null; try { marca = await env.SUITE.get('uno:' + a.clave); } catch (e) { return omitir('no se pudo leer la marca de «una sola vez»: no se arriesga a repetirlo'); } if (marca) return omitir('ya se aplicó una vez (esta acción es de «una sola vez»)'); }
  if (ctx.autoHoy >= TOPE_DIA) return omitir('tope diario de cambios automáticos (' + TOPE_DIA + '): sigue mañana, o aplícalo a mano');
  if (SUBE_GASTO.indexOf(a.tipo) !== -1 && ctx.sinMemoria) return omitir('no se pudo guardar el registro de lo aplicado: lo que sube gasto se detiene para no repetirlo');
  const costo = COSTO[a.tipo] || 10;
  const num = SUBE_GASTO.indexOf(a.tipo) !== -1 ? numeroDeCampana(a.campana) : '';
  if (ctx.usado + costo + (num && !ctx.canales ? COSTO_NUMEROS : 0) > PRESUPUESTO_LLAMADAS) return { clave: a.clave, estado: 'aplazada', motivo: 'demasiado trabajo para esta llamada: se manda en la siguiente' };
  // --- el numero de WhatsApp (solo lo que sube gasto en una campaña de ZapData; ZapData se consulta una vez por llamada) ---
  const notas = [];
  if (num) {
    if (!ctx.canales) { ctx.usado += COSTO_NUMEROS; ctx.canales = await consultarNumeros(env); }
    const v = frenoNumero(num, a.pais, ctx);
    if (!v.ok) return omitir(v.motivo);
    if (v.nota) notas.push(v.nota);
  }
  ctx.usado += costo;
  // --- Facebook ---
  const meta = { regla: texto(r.nombre, 80), nombre: texto(a.nombre, 100), pais: a.pais, razon: texto(a.razon, 200), cifras: texto(a.cifras, 140), tipo: a.tipo, antes: numero(a.actual), despues: nuevo, clave: a.clave, ms: msReg, via: 'auto' };
  const base = { id: a.id, type: a.nivel, accountId: a.cuenta || '', meta };
  let d, copia = '', registrado = true, aviso = '', efectivo = '', despues = nuevo;
  if (a.tipo === 'pausar' || a.tipo === 'reactivar') {
    d = await aFacebook(env, Object.assign({ action: a.tipo === 'pausar' ? 'pause' : 'resume', previa: { campana: a.campanaId } }, base));
    if (d.j && d.j.omitido) return omitir(d.j.motivo);
    if (!d.j || !d.j.ok) return fallar(motivoDe(d));
    aviso = d.j.aviso || ''; efectivo = d.j.efectivo || ''; registrado = d.j.registrado !== false;
    // Se pausa de nuevo lo que ya se habia pausado: alguien la volvio a encender (o la pausa anterior no llego a quedar). Que el registro y el Telegram lo digan.
    if (a.tipo === 'pausar' && ctx.mem[a.clave]) notas.push('ya se había pausado ' + cuandoCO(ctx.mem[a.clave].t, ctx.ahora) + ' y volvió a estar encendida');
  } else if (esPresupuesto) {
    d = await aFacebook(env, Object.assign({ action: 'budget', budgetKind: 'daily', amount: nuevo, previa: { campana: a.campanaId, presupuesto: numero(a.actual) } }, base));
    if (d.j && d.j.omitido) return omitir(d.j.motivo);
    if (!d.j || !d.j.ok) return fallar(motivoDe(d));
    aviso = d.j.aviso || ''; registrado = d.j.registrado !== false;
    if (d.j.presupuesto && d.j.presupuesto.daily > 0) despues = d.j.presupuesto.daily;
  } else {                                                           // duplicar: la copia, su presupuesto y (si la regla lo pide) pausar el original; las mismas tres llamadas que hace la pantalla
    d = await aFacebook(env, Object.assign({ action: 'duplicate', previa: { campana: a.campanaId } }, base));
    if (d.j && d.j.omitido) return omitir(d.j.motivo);
    if (!d.j || !d.j.ok) return fallar(motivoDe(d));
    copia = d.j.copia || ''; registrado = d.j.registrado !== false;
    const actual = numero(a.actual) || 0;
    if (copia && copiaNuevo >= 1 && actual > 0 && copiaNuevo !== Math.round(actual)) {
      const b = await aFacebook(env, { action: 'budget', id: copia, type: a.nivel, accountId: a.cuenta || '', budgetKind: 'daily', amount: copiaNuevo,
        meta: Object.assign({}, meta, { nombre: texto('Copia de ' + a.nombre, 100), tipo: 'fijar', razon: 'Parte de duplicar: presupuesto de la copia.', cifras: '', clave: '', antes: null, despues: copiaNuevo }) });
      notas.push(b.j && b.j.ok ? 'presupuesto de la copia: ' + copiaNuevo : 'la copia se creó pero no se le pudo poner presupuesto (' + motivoDe(b) + ')');
    }
    if (def.pausarOriginal) {
      const o = await aFacebook(env, Object.assign({ action: 'pause' }, base, { meta: Object.assign({}, meta, { tipo: 'pausar', razon: 'Parte de duplicar: se pausa el original.', cifras: '', clave: '', antes: null, despues: null }) }));
      notas.push(o.j && o.j.ok ? 'original pausado' : 'no se pudo pausar el original (' + motivoDe(o) + ')');
    }
  }
  ctx.autoHoy++;
  ctx.mem[a.clave] = { t: ctx.ahora, ms: msReg };
  if (siempre) { try { await env.SUITE.put('uno:' + a.clave, '1'); } catch (e) { ctx.sinMemoria = true; } }
  if (!registrado) ctx.sinMemoria = true;
  return { clave: a.clave, estado: 'hecha', aviso: aviso || undefined, efectivo: efectivo || undefined, despues: despues != null ? despues : undefined, copia: copia || undefined,
    notas: notas.length ? notas.join('; ') : undefined, sinRegistro: registrado ? undefined : true };
}

// La llave del refresco, comparada en tiempo constante. Sin CLAVE_REGLAS configurada no hay camino.
const autorizado = (request, env) => { const llave = request.headers.get('x-clave-reglas') || ''; return !!llave && !!env.CLAVE_REGLAS && safeEqual(llave, env.CLAVE_REGLAS); };

// El 401 de ESTE archivo lleva `ejecutor: true`: el refresco lo distingue del 401 del sitio anterior (que justo despues de publicar todavia no tiene esta ruta y pide sesion): ese hay que esperarlo, este es una llave mala.
const sinLlave = () => json(401, { ok: false, ejecutor: true, error: 'Llave de las reglas incorrecta o ausente' });

export async function onRequestGet(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!autorizado(request, env)) return sinLlave();
  const ahora = Date.now(), m = await leerMemoria(env, ahora, FICHAS_MEMORIA);
  if (!m.ok) return json(502, { ok: false, error: 'No se pudo leer el registro de lo aplicado' });
  return json(200, { ok: true, ahora, memoria: m.memoria, autoHoy: m.autoHoy, tope: TOPE_DIA });
}

export async function onRequestPost(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!autorizado(request, env)) return sinLlave();
  let b; try { b = await request.json(); } catch (e) { return json(400, { ok: false, error: 'JSON invalido' }); }
  const acciones = b && Array.isArray(b.acciones) ? b.acciones : null;
  if (!acciones || !acciones.length) return json(400, { ok: false, error: 'Faltan las acciones' });
  if (acciones.length > MAX_POR_LLAMADA) return json(400, { ok: false, error: 'Maximo ' + MAX_POR_LLAMADA + ' acciones por llamada' });

  const ahora = Date.now();
  let reglas = [];
  try { const c = await env.SUITE.get(CLAVE_REGLAS, { type: 'json' }); reglas = c && Array.isArray(c.lista) ? c.lista : []; } catch (e) { return json(502, { ok: false, error: 'No se pudieron leer las reglas' }); }
  const m = await leerMemoria(env, ahora);
  const ctx = { env, reglas, ahora, mem: m.memoria, autoHoy: m.autoHoy, vistas: new Set(), sinMemoria: !m.ok, usado: 2, numeros: limpiarNumeros(b.numeros), canales: null };

  const resultados = [];
  for (const x of acciones) {
    const a = x && typeof x === 'object' ? x : {};
    const limpia = {
      reglaId: texto(a.reglaId, 30), clave: texto(a.clave, 90), tipo: texto(a.tipo, 12), nivel: texto(a.nivel, 10), id: texto(a.id, 24), campanaId: texto(a.campanaId, 24), campana: texto(a.campana, 160),
      cuenta: texto(a.cuenta, 30), pais: texto(a.pais, 20), nombre: texto(a.nombre, 100), razon: a.razon, cifras: a.cifras, actual: a.actual, nuevo: a.nuevo, copiaNuevo: a.copiaNuevo, ms: a.ms,
    };
    try { resultados.push(await aplicar(limpia, ctx)); }
    catch (e) { resultados.push({ clave: limpia.clave, estado: 'fallo', motivo: texto('error inesperado: ' + ((e && e.message) || e), 160) }); }
  }
  return json(200, { ok: true, resultados, autoHoy: ctx.autoHoy, tope: TOPE_DIA });
}
