// Las reglas automaticas: las reglas ENCENDIDAS trabajan solas en cada refresco del dash y APLICAN en Facebook lo que deciden.
//
// Corre en dos pasos del refresco de GitHub (.github/workflows/refresh.yml):
//
//   1) REVISAR (antes de publicar):   node scripts/reglas-auto.cjs
//      Abre reglas.html -ya construida, con los numeros de esta corrida- sin pantalla (jsdom), le pasa las reglas guardadas -las encendidas y a las que YA LES TOCA segun su programa: cada cuanto y
//      entre que fechas- y le pide a la MISMA funcion que usa la trafficker (rgEvaluar) lo que harian. Asi los numeros no pueden diferir de los del dashboard: no hay una segunda copia de la
//      logica de las reglas que se desincronice. Lo que decide lo anota en data/reglas-auto-log.json (se publica) y deja las acciones por ejecutar en data/reglas-auto-pendientes.json.
//      Solo EVALUA las que les toca, pero para contar la cobertura («N campañas que corren sin ninguna regla») le pasa TODAS las encendidas: una regla que espera su turno tambien cubre sus campañas.
//   2) EJECUTAR (despues de publicar): node scripts/reglas-auto.cjs --ejecutar
//      Manda esas acciones a /api/reglas-ejecutar (functions/api/reglas-ejecutar.js), que las vuelve a comprobar contra las reglas guardadas y contra Facebook antes de tocar nada, anota que paso
//      con cada una (estado hecha / omitida / fallo, con la razon) y manda UN Telegram solo si alguna FALLO (lo hecho queda en el registro). Va despues del deploy a proposito: si el armado o la publicacion fallan (datos viejos, hojas
//      sin bajar) no se ejecuta nada, y el servidor ya tiene la version nueva del ejecutor.
//
// Nunca ejecuta con datos dudosos: no ejecuta nada de un pais cuya hoja de ventas reciente no se pudo bajar en esta corrida o perdio filas (data/refresh-status.json), ni si la pagina es de hace
// mas de 90 min. Pone un tope por corrida (MAX_POR_CORRIDA) y el servidor otro por dia; lo que no cabe sigue en la proxima revision. No repite lo que ya aplico (la memoria es el registro de /api/reglas-registro):
// presupuestos, duplicar y reactivar esperan al menos 24 h; PAUSAR espera solo lo que la regla pida («Una vez al día»...), y con «Cada vez que se cumple» vuelve a pausar lo que alguien volvio a encender.
//
// El programa de cada regla (`programa` = { cadaMin, desde, hasta }, se edita en /reglas) lo decide ESTE script, antes de abrir la pagina, para no instalar jsdom cuando a ninguna regla le toca:
// cadaMin 0 = en cada refresco; si no, pasa ese tiempo desde su ultima revision (`ultimas[id]` en el registro). desde / hasta = fechas de Colombia, ambas incluidas.
// Un intervalo corto NO hace que el sistema pase mas seguido: la regla se revisa en el primer refresco que la encuentre vencida (2 al dia + al abrir el dash, o cada hora con el runner propio).
//
// Lo que anota en data/reglas-auto-log.json (el refresco lo commitea):
//   - `entradas`: cada decision (hecha / omitida / fallo / pendiente / aviso) con la regla, el objeto, la razon con los numeros y el motivo si no se hizo
//   - `revisiones`: cada corrida («reviso N reglas, M acciones; ejecuto K») o por que no reviso; `corridas` (la hora de cada una) y `ultimas` (la ultima revision de cada regla)
//   - `informe`: las CIFRAS EXACTAS de la ultima revision (por regla y tarea: cuantos elementos miro, cuantos cumplen y, de los primeros, el numero de cada condicion): asi se ve con que numeros se
//     decidio -«el gasto fue 8.345, la regla pide mas de 5.000»- aunque no salga ninguna accion. /reglas > Registro lo muestra.
//
// Para frenarlo: apagar la regla en /reglas, o borrar el secreto REGLAS_CLAVE de GitHub (no lee reglas ni ejecuta), o la variable CLAVE_REGLAS de Cloudflare Pages (el ejecutor contesta 401).
//
// Uso:  node scripts/reglas-auto.cjs [--html reglas.html] [--log data/reglas-auto-log.json]
//                                    [--reglas ruta.json]   (en vez de pedirlas a /api/reglas; para pruebas)
//                                    [--seco]               (no escribe nada, no manda Telegram y NO ejecuta; imprime lo que haria)
//                                    [--ejecutar]           (el segundo paso: ejecuta lo que dejo el primero)
//                                    [--ahora <ms>]         (reloj falso, para probar el programa sin esperar horas)
//                                    [--cifras]             (imprime las cifras exactas de cada fila: gasto, ventas, ROAS… de cada condicion)
//                                    [--pendientes ruta] [--estado data/refresh-status.json]
//                                    [--numeros data/zapdata-instancias.json]   (los numeros de ZapData de esta corrida: el ejecutor no sube el gasto de una campaña con el numero caido)
// Entorno: REGLAS_CLAVE (obligatoria salvo --reglas), REGLAS_URL, AVISO_TELEGRAM_TOKEN, AVISO_TELEGRAM_CHAT, GITHUB_RUN_ID.
// Nunca falla el refresco: ante cualquier problema lo dice y sale con 0.
// Al importarlo (require) no corre nada: solo expone las cuentas del programa y los ayudantes, para scripts/probar-*.cjs.

process.env.TZ = process.env.TZ || 'America/Bogota';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const args = process.argv.slice(2);
const arg = (n, def) => { const i = args.indexOf('--' + n); return i !== -1 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : def; };
const SECO = args.includes('--seco');
const CIFRAS = args.includes('--cifras');
const EJECUTAR = args.includes('--ejecutar');
const RAIZ = path.resolve(__dirname, '..');
const HTML = path.resolve(arg('html', path.join(RAIZ, 'reglas.html')));
const LOG = path.resolve(arg('log', path.join(RAIZ, 'data', 'reglas-auto-log.json')));
const PENDIENTES = path.resolve(arg('pendientes', path.join(RAIZ, 'data', 'reglas-auto-pendientes.json')));
const ESTADO_HOJAS = path.resolve(arg('estado', path.join(RAIZ, 'data', 'refresh-status.json')));
const NUMEROS = path.resolve(arg('numeros', path.join(RAIZ, 'data', 'zapdata-instancias.json')));
const URL_BASE = (/^https:\/\/[^\s\/]+/i.test(process.env.REGLAS_URL || '') ? process.env.REGLAS_URL : '').replace(/\/+$/, '');
const DIA = 86400000, MAX_LOG = 300, MAX_REV = 60, MAX_CORR = 96;
const MAX_POR_CORRIDA = 40;        // acciones que se aplican en una corrida; el resto sigue en la proxima revision (el servidor tiene ademas un tope diario)
const POR_LLAMADA = 5;             // el servidor acepta 5 por llamada (limite de subrequests de Cloudflare)
const TOPE_SEGUNDOS = 170;         // el paso de ejecutar no pasa de esto en total
const REINTENTO_FALLO = 6 * 3600000;   // una accion que fallo no se vuelve a mandar en 6 h (sin esto un permiso que falta se reintentaria en cada refresco)
const DATOS_MAX_MIN = 90;          // la pagina de la que salen los numeros no puede ser mas vieja que esto
const AHORA_FALSO = Number(arg('ahora', 0)) || 0, T_ARRANQUE = Date.now();
const ahoraMs = () => (AHORA_FALSO ? AHORA_FALSO + (Date.now() - T_ARRANQUE) : Date.now());      // el reloj falso arranca en --ahora y sigue corriendo: una revision tarda segundos y no debe quedar «congelada»
const CORRIDA = process.env.GITHUB_RUN_ID || '';

// ---- programa de cada regla: las mismas cuentas que rgProgramaBase / rgEnFechas / rgCadaTxt de templates/reglas-modulo.js (scripts/probar-programa.cjs las compara) ----
const REV_MIN = 15, REV_MAX = 10080;
function programaDe(r) {
  const p = r && r.programa && typeof r.programa === 'object' ? r.programa : {};
  let m = Math.round(Number(p.cadaMin));
  m = m > 0 ? Math.min(REV_MAX, Math.max(REV_MIN, m)) : 0;
  const f = (v) => (/^\d{4}-\d{2}-\d{2}$/.test(String(v || '')) ? String(v) : '');
  // Horario del dia (hora de Colombia): horaDesde 0-23 y horaHasta 1-24, las dos enteras y distintas; «hasta las 0» es medianoche; de 0 a 24 es todo el dia = ningun horario.
  // Solo cuentan numeros o textos con un numero («17»): ni true/false, ni null, ni vacios.
  const hora = (v) => ((typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) ? Number(v) : NaN);
  let hd = null, hh = null;
  {
    const d = hora(p.horaDesde); let h = hora(p.horaHasta);
    if (h === 0) h = 24;
    if (Number.isInteger(d) && Number.isInteger(h) && d >= 0 && d <= 23 && h >= 1 && h <= 24 && d !== h && !(d === 0 && h === 24)) { hd = d; hh = h; }
  }
  return { cadaMin: m, desde: f(p.desde), hasta: f(p.hasta), horaDesde: hd, horaHasta: hh };
}
// La hora de Colombia (0-23) y si la regla esta dentro de su horario: la misma cuenta que rgHoraNumCO / rgEnHorario de la pagina. La ventana es [horaDesde, horaHasta) y cruza la medianoche si horaDesde > horaHasta.
function horaNumCO(ms) {
  const o = {};
  new Intl.DateTimeFormat('en-US', { timeZone: 'America/Bogota', hour: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms == null ? Date.now() : ms)).forEach((x) => { o[x.type] = x.value; });
  return Number(o.hour) % 24;
}
function enHorario(r, ms) {
  const p = programaDe(r);
  if (p.horaDesde == null) return true;
  const h = horaNumCO(ms);
  return p.horaDesde < p.horaHasta ? (h >= p.horaDesde && h < p.horaHasta) : (h >= p.horaDesde || h < p.horaHasta);
}
// «5:00 p. m.», «12:00 a. m.», «medianoche»; y «de 5:00 p. m. a medianoche» ('' sin horario). Mismas cuentas que rgHoraTxt / rgHorasTxt de la pagina.
const horaTxt = (h) => (h === 24 ? 'medianoche' : ((h % 12) || 12) + ':00 ' + (h < 12 ? 'a. m.' : 'p. m.'));
const horasTxt = (p) => (p && p.horaDesde != null && p.horaHasta != null ? 'de ' + horaTxt(p.horaDesde) + ' a ' + horaTxt(p.horaHasta) : '');
function hoyCO(ms) {
  const o = {};
  new Intl.DateTimeFormat('en-US', { timeZone: 'America/Bogota', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(ms == null ? Date.now() : ms)).forEach((x) => { o[x.type] = x.value; });
  return o.year + '-' + o.month + '-' + o.day;
}
function cadaTxt(min) {
  if (!(min > 0)) return 'en cada refresco';
  if (min < 60) return 'cada ' + min + ' min';
  if (min === 60) return 'cada hora';
  if (min % 1440 === 0 && min >= 2880) return 'cada ' + (min / 1440) + ' días';
  if (min % 60 === 0) return 'cada ' + (min / 60) + ' h';
  return 'cada ' + Math.floor(min / 60) + ' h ' + (min % 60) + ' min';
}
function fechaCorta(s, ahora) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); if (!m) return '';
  const o = {};
  new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short' }).formatToParts(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).forEach((x) => { o[x.type] = x.value; });
  return o.day + ' ' + String(o.month || '').replace(/\.$/, '') + (m[1] !== hoyCO(ahora).slice(0, 4) ? ' ' + m[1] : '');
}
const horaCO = (ms) => new Date(ms).toLocaleTimeString('es-CO', { timeZone: 'America/Bogota', hour: 'numeric', minute: '2-digit' });
// Cuando le toca de nuevo a una regla: su ultima revision + el intervalo, menos una tolerancia (10 % del intervalo, hasta 5 min) porque un refresco no cae a la hora exacta (sin ella, uno que
// llega 20 segundos antes dejaria la regla esperando otro turno entero). 0 = no hay intervalo o nunca se reviso. Misma cuenta que rgProximaRevision de la pagina.
const toleranciaMs = (cadaMin) => Math.min(300000, cadaMin * 6000);
function proximaRevision(r, ultima) {
  const p = programaDe(r);
  return p.cadaMin > 0 && ultima > 0 ? ultima + p.cadaMin * 60000 - toleranciaMs(p.cadaMin) : 0;
}
// «desde las 4:34 p. m.», «mañana desde las 7:00 a. m.» o «el 20 oct desde las 6:55 a. m.» (hora y dia de Colombia). Misma cuenta que rgDesdeTxt de la pagina.
function desdeTxt(ms, ahora) {
  const ya = ahora == null ? Date.now() : ahora, dia = hoyCO(ms);
  return (dia === hoyCO(ya) ? '' : dia === hoyCO(ya + 864e5) ? 'mañana ' : 'el ' + fechaCorta(dia, ya) + ' ') + 'desde las ' + horaCO(ms);
}
// ¿Le toca revisarse a esta regla ahora? `ultima` = cuando se reviso por ultima vez (0 = nunca; una fecha en el futuro, de un reloj mal puesto o un registro de prueba, se ignora: dejaria la
// regla esperando hasta esa fecha). Devuelve { toca, motivo }; `motivo` solo cuando espera.
function tocaRevisar(r, ahora, ultima) {
  const p = programaDe(r), hoy = hoyCO(ahora);
  if (p.desde && hoy < p.desde) return { toca: false, motivo: 'empieza el ' + fechaCorta(p.desde, ahora) };
  if (p.hasta && hoy > p.hasta) return { toca: false, motivo: 'terminó el ' + fechaCorta(p.hasta, ahora) + ', ya está fuera de fechas' };
  // Fuera de su horario del dia la regla espera (no se revisa ni se ejecuta). El motivo no lleva la hora de ahora: asi las corridas seguidas en espera dicen lo mismo y el registro las junta.
  if (!enHorario(r, ahora)) return { toca: false, motivo: 'solo trabaja ' + horasTxt(p) + ' (hora de Colombia)' };
  if (ultima > ahora + 300000) ultima = 0;
  const prox = proximaRevision(r, ultima);
  if (prox > ahora) return { toca: false, motivo: cadaTxt(p.cadaMin) + '; última revisión a las ' + horaCO(ultima) + ', le toca ' + desdeTxt(prox, ahora) };
  return { toca: true, motivo: '' };
}

// ---- el registro ----
// Lee el registro que ya hay (o uno vacio la primera vez).
function leerLog() {
  try { const l = JSON.parse(fs.readFileSync(LOG, 'utf8')); if (l && Array.isArray(l.entradas)) { delete l.modo; return l; } } catch (e) { /* primera vez */ }
  return { v: 1, actualizado: null, entradas: [] };
}
// La constancia de una corrida: estado 'ok' (reviso), 'espera' (habia reglas encendidas pero a ninguna le tocaba todavia), 'omitida' (no habia que revisar: sin llave, sin reglas encendidas) o 'error'.
// Es lo unico que escribe cuando no hay nada nuevo. `fusionar` junta una corrida con la anterior si dicen lo mismo (con 'espera', cada 30 minutos llenaria el registro de lineas iguales).
function anotarRevision(log, estado, texto, extra, fusionar, t) {
  const ahora = t || ahoraMs(), rev = Array.isArray(log.revisiones) ? log.revisiones : [];
  if (fusionar && rev[0] && rev[0].estado === estado && rev[0].texto === texto) { rev[0].t = ahora; rev[0].veces = (rev[0].veces || 1) + 1; log.revisiones = rev; }
  else log.revisiones = [Object.assign({ t: ahora, estado, texto }, extra || {})].concat(rev).slice(0, MAX_REV);
  log.corridas = [ahora].concat(Array.isArray(log.corridas) ? log.corridas : []).slice(0, MAX_CORR);
  log.actualizado = new Date(ahora).toISOString();
}
function guardarLog(log) {
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.writeFileSync(LOG, JSON.stringify(log, null, 1));
}
function guardarPendientes(obj) {
  fs.mkdirSync(path.dirname(PENDIENTES), { recursive: true });
  fs.writeFileSync(PENDIENTES, JSON.stringify(obj));
}
// Borra lo que dejo una corrida anterior: en un runner propio la carpeta de trabajo NO se vacia entre corridas, y el paso de ejecutar no debe encontrar acciones de hace horas.
function limpiarPendientes() { try { fs.unlinkSync(PENDIENTES); } catch (e) { /* no habia */ } }
// Termina la corrida sin evaluar: lo dice por pantalla y lo deja anotado (salvo con --seco). Nunca falla el refresco.
// `ui` es el texto que ve la persona en el registro (que secreto falta, que regla encender); `msg` es el de la bitacora de la corrida.
const salir = (msg, estado, ui) => {
  console.log('Reglas automaticas: ' + msg);
  if (!SECO) { try { limpiarPendientes(); const log = leerLog(); anotarRevision(log, estado || 'omitida', ui || (msg.charAt(0).toUpperCase() + msg.slice(1))); guardarLog(log); } catch (e) { /* el registro es un extra */ } }
  process.exit(0);
};

// ---- el servidor ----
const llave = () => {
  if (!URL_BASE) throw new Error('REGLAS_URL tiene que ser la direccion https de tu dash (variable DASH_URL de GitHub): sin ella la llave de las reglas no se manda a ningun lado.');
  return process.env.REGLAS_CLAVE || '';
};
async function leerReglas() {
  const f = arg('reglas', '');
  if (f) return JSON.parse(fs.readFileSync(f, 'utf8'));
  if (!llave()) salir('falta REGLAS_CLAVE; no se revisa nada.', 'omitida', 'No se revisó: falta el secreto REGLAS_CLAVE en GitHub (se crea con scripts/Setup-ReglasClave.ps1).');
  const r = await fetch(URL_BASE + '/api/reglas', { headers: { 'x-clave-reglas': llave() }, signal: AbortSignal.timeout(30000) });
  if (!r.ok) salir('/api/reglas contesto ' + r.status + '; no se revisa nada.', 'error', 'No se revisó: el dash no entregó las reglas (respondió ' + r.status + '). Revisa que CLAVE_REGLAS en Cloudflare sea la misma que REGLAS_CLAVE en GitHub.');
  const j = await r.json();
  return j && j.reglas;
}
// Lo ya aplicado, del registro compartido. null = el ejecutor no esta (todavia) o no contesto: se evalua sin esa memoria y el servidor igual no repite nada (la vuelve a mirar al ejecutar).
async function leerMemoria() {
  if (!llave() || arg('reglas', '')) return { memoria: null, motivo: arg('reglas', '') ? 'prueba local' : 'sin llave' };
  try {
    const r = await fetch(URL_BASE + '/api/reglas-ejecutar', { headers: { 'x-clave-reglas': llave() }, signal: AbortSignal.timeout(30000) });
    if (!r.ok) { let cuerpo = null; try { cuerpo = await r.json(); } catch (e) { /* sin cuerpo */ } return { memoria: null, motivo: 'el ejecutor contestó ' + r.status, status: r.status, delEjecutor: !!(cuerpo && cuerpo.ejecutor === true) }; }
    const j = await r.json();
    return j && j.ok && j.memoria && typeof j.memoria === 'object' ? { memoria: j.memoria, autoHoy: j.autoHoy, tope: j.tope } : { memoria: null, motivo: 'respuesta rara del ejecutor' };
  } catch (e) { return { memoria: null, motivo: 'no se pudo hablar con el ejecutor (' + String((e && e.message) || e).slice(0, 60) + ')' }; }
}
async function avisar(texto, opciones) {
  // Con la pausa puesta solo salen los avisos que piden `siempre`: lo que una persona tiene que mirar (una accion que fallo, una llave rechazada, dos reglas que se contradicen).
  // Lo que las reglas hicieron bien queda en /reglas > Registro > «Lo que se aplicó», no en Telegram.
  if (process.env.AVISOS_PAUSADOS === '1' && !(opciones && opciones.siempre)) return 'avisos en pausa';
  const tk = process.env.AVISO_TELEGRAM_TOKEN, ch = process.env.AVISO_TELEGRAM_CHAT;
  if (!tk || !ch) return 'sin credenciales de Telegram';
  try {
    // TELEGRAM_API_BASE solo la usan las pruebas (un servidor falso que recibe el mensaje); en produccion no existe y se habla con Telegram.
    const r = await fetch((process.env.TELEGRAM_API_BASE || 'https://api.telegram.org') + '/bot' + tk + '/sendMessage', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: AbortSignal.timeout(20000),
      body: JSON.stringify({ chat_id: ch, text: texto.slice(0, 3900), disable_web_page_preview: true }),
    });
    return r.ok ? 'enviado' : 'Telegram contesto ' + r.status;
  } catch (e) { return 'Telegram fallo: ' + String((e && e.message) || e).slice(0, 80); }
}

// ---- datos en los que se puede confiar ----
// Un pais cuyas hojas de ventas recientes no se bajaron en ESTA corrida (el refresco se queda con la ultima copia buena) o perdieron filas se ve «sin ventas» en las que le faltan: pausar o subir plata
// con eso seria decidir a ciegas. Se mira data/refresh-status.json, que deja cada refresco. Solo cuentan las pestañas con ventas de los ultimos 7 dias (una oferta muerta que ya no baja no bloquea a nadie).
// Pestañas que no son de ventas (las de Ecuador: «Data_Unificada», «Data Old Anuncios», «Anuncios», «DASHBORAD - …»). Se reconocen por el NOMBRE de la pestaña y al principio: mirar el nombre del archivo
// descartaba por error las ofertas «zapdata_…» (contienen «data_») y «MOLDES» (contiene «old»), o sea 34 de las 102 pestañas.
const esNoVentas = (pestana) => /^(data[_ ]|anuncios|dashbo?rad)/i.test(String(pestana || '').trim());
const sinTildes = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
function bloqueosPorDatos(ahora, estado) {
  const out = {};
  let b = estado;
  if (b === undefined) { try { b = JSON.parse(fs.readFileSync(ESTADO_HOJAS, 'utf8').replace(/^\uFEFF/, '')); } catch (e) { b = null; } }
  if (!b || !Array.isArray(b.pestanas)) return { bloqueos: out, sinBitacora: true };
  const desde = hoyCO(ahora - 7 * DIA);
  b.pestanas.forEach((p) => {
    if (!p || esNoVentas(p.pestana) || !(String(p.ultimaVenta || '') >= desde)) return;
    const pais = sinTildes(p.pais); if (!pais || out[pais]) return;
    if (p.escrito === false) out[pais] = 'la hoja de ventas «' + p.pestana + '» no se pudo bajar en este refresco (se usó la copia anterior)';
    else if (Number(p.filasAntes) > 0 && Number(p.filas) < Number(p.filasAntes)) out[pais] = 'la hoja de ventas «' + p.pestana + '» perdió filas (de ' + p.filasAntes + ' a ' + p.filas + ')';
  });
  return { bloqueos: out, sinBitacora: false };
}

// ---- la pagina ----
// jsdom solo se instala cuando hay algo que evaluar: sin reglas encendidas, o con todas esperando su turno, el paso cuesta un par de segundos, no los ~8 de npm.
function cargarJsdom() {
  try { return require('jsdom'); } catch (e) { /* no esta instalado todavia */ }
  const dir = path.join(process.env.RUNNER_TEMP || require('os').tmpdir(), 'jsd');
  fs.mkdirSync(dir, { recursive: true });
  // Se instala EXACTAMENTE lo que fija el candado de scripts/jsdom-pin (version y hash de cada uno de sus 60 paquetes) y sin correr scripts de instalacion. Antes era
  // `npm install jsdom@25.0.1` a secas: jsdom estaba fijo pero sus dependencias se resolvian a lo ultimo de su rango en cada corrida, y esta corrida tiene a mano los tokens de Facebook,
  // la llave de las reglas y el token de Telegram: una dependencia comprometida en npm habria corrido ahi. El package.json propio tambien evita que npm suba por las carpetas e instale en la de otra herramienta.
  const pin = path.join(__dirname, 'jsdom-pin');
  // Si ya hay una instalacion COMPLETA de este mismo candado (npm escribe node_modules/.package-lock.json al terminar), se reutiliza: reinstalar borra node_modules, y con dos procesos a la vez
  // (las pruebas en local) uno cargaba un jsdom a medias. En CI la carpeta de temporales llega vacia y se instala una vez por corrida.
  try {
    const igual = fs.readFileSync(path.join(pin, 'package-lock.json'), 'utf8') === fs.readFileSync(path.join(dir, 'package-lock.json'), 'utf8');
    if (igual && fs.existsSync(path.join(dir, 'node_modules', '.package-lock.json'))) return require(path.join(dir, 'node_modules', 'jsdom'));
  } catch (e) { /* no hay instalacion usable: se instala */ }
  ['package.json', 'package-lock.json'].forEach((f) => fs.copyFileSync(path.join(pin, f), path.join(dir, f)));
  require('child_process').execSync('npm ci --ignore-scripts --no-audit --no-fund --silent', { cwd: dir, stdio: 'inherit', timeout: 180000 });
  return require(path.join(dir, 'node_modules', 'jsdom'));
}

// Ejecuta los <script> en linea de la pagina dentro de la ventana de jsdom, en orden y como scripts clasicos (comparten las declaraciones de nivel superior, igual que en un navegador).
// Se usa con runScripts:'outside-only': jsdom NO ejecuta nada por su cuenta, asi que lo unico que corre es el codigo que trae el HTML que armamos nosotros. Un manejador en linea que un dato meta
// en la pagina por innerHTML (<img onerror=…>, <svg onload=…>) queda inerte: con 'dangerously' correria aqui, donde estan los tokens de Facebook en disco y los secretos en el entorno.
function correrScriptsDeLaPagina(dom, errores) {
  const ctx = dom.getInternalVMContext();
  [...dom.window.document.querySelectorAll('script')].forEach((s, i) => {
    if (s.hasAttribute('src')) return;                                              // la pagina no carga scripts externos
    const tipo = (s.getAttribute('type') || '').trim().toLowerCase();
    if (tipo && !/^(text|application)\/(x-)?javascript$/.test(tipo)) return;         // bloques de datos o plantillas (application/json, text/template…) no se ejecutan
    try { new vm.Script(s.textContent, { filename: 'reglas.html#script' + (i + 1) }).runInContext(ctx); }
    catch (e) { errores.push('script ' + (i + 1) + ': ' + String((e && e.message) || e).slice(0, 160)); }
  });
}

// `todas` = todas las reglas encendidas (las que esperan su turno tambien): la pagina las usa SOLO para contar que campañas cubre alguna regla; evalua unicamente `lista`.
function evaluar(lista, memoria, todas) {
  const { JSDOM, VirtualConsole } = cargarJsdom();
  const html = fs.readFileSync(HTML, 'utf8');
  const errores = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errores.push(String((e && e.message) || e).slice(0, 160)));
  const dom = new JSDOM(html, {
    runScripts: 'outside-only', pretendToBeVisual: true, url: URL_BASE + '/reglas', virtualConsole: vc,
    beforeParse(w) {
      // La pagina no necesita red para evaluar. Los reemplazos se crean DENTRO de la ventana (w.eval) y no en Node: una funcion hecha en Node y pasada a la pagina le entrega el Function de
      // Node (f.constructor('return process')()) y con el todo el sistema. Esto no vuelve seguro a jsdom -su propia documentacion dice que no aisla-, solo quita la salida mas facil.
      w.eval('window.fetch = function () { return Promise.reject(new Error("sin red")); }; window.scrollTo = function () {}; HTMLCanvasElement.prototype.getContext = function () { return null; };');
    },
  });
  correrScriptsDeLaPagina(dom, errores);
  const w = dom.window;
  if (typeof w.__reglasAuto !== 'function') {
    throw new Error('la pagina no expone __reglasAuto (' + (errores[0] || 'sin error visible') + ')');
  }
  const props = w.__reglasAuto(JSON.parse(JSON.stringify(lista)), memoria ? JSON.parse(JSON.stringify(memoria)) : null, todas ? JSON.parse(JSON.stringify(todas)) : undefined);
  const salida = JSON.parse(JSON.stringify(props));
  // Las cifras con las que decidio (la pagina las deja en window.__reglasInforme). Una pagina que no las traiga no tumba la revision: sigue sin ellas.
  const informe = w.__reglasInforme ? JSON.parse(JSON.stringify(w.__reglasInforme)) : null;
  w.close();
  return { props: salida, informe };
}

// ---- las cifras de la revision: se acotan, se resumen en una frase y se pueden imprimir ----
const NIV_PL = { campaign: 'campañas', adset: 'conjuntos', ad: 'anuncios' };
// El informe se commitea en cada refresco: se acota para que una regla sobre miles de anuncios no lo infle (se recortan las filas de detalle; los conteos quedan).
function acotarInforme(inf) {
  if (!inf || !Array.isArray(inf.reglas)) return null;
  const MAX = 120000;                                       // caracteres de JSON
  let filas = 30;
  while (JSON.stringify(inf).length > MAX && filas > 0) {
    filas = filas > 2 ? Math.floor(filas / 2) : 0;                  // 30, 15, 7, 3, 1... y al final solo los conteos
    inf.reglas.forEach((r) => (r.tareas || []).forEach((t) => { if (Array.isArray(t.filas)) t.filas = t.filas.slice(0, filas); }));
  }
  return JSON.stringify(inf).length > MAX ? null : inf;
}
// «miró 34 conjuntos y 2 campañas, 2 cumplen sus condiciones» (o por que no miró nada) y los totales.
function resumenMiradas(inf) {
  const r = { txt: '', miradas: 0, cumplen: 0 };
  if (!inf || !Array.isArray(inf.reglas)) return r;
  const por = {}, corr = {};
  inf.reglas.forEach((x) => (x.tareas || []).forEach((t) => { por[t.nivel] = (por[t.nivel] || 0) + (t.miradas || 0); corr[t.nivel] = (corr[t.nivel] || 0) + (t.corren || 0); r.miradas += t.miradas || 0; r.cumplen += t.cumplen || 0; }));
  if (!r.miradas) { r.txt = 'no encontró ninguna campaña asignada para mirar (revisa «Asignar campañas» y el país de la regla)'; return r; }
  r.txt = 'miró ' + ['campaign', 'adset', 'ad'].filter((n) => por[n]).map((n) => por[n] + ' ' + NIV_PL[n] + ' (' + corr[n] + ' corriendo)').join(' y ') + ', ' + r.cumplen + (r.cumplen === 1 ? ' cumple' : ' cumplen') + ' sus condiciones';
  return r;
}
// ---- cobertura: lo que la revision NO miro, para que nada se descarte en silencio ----
// Una frase para el registro: asignadas que no se evaluaron (su cuenta de anuncios esta caida), ids asignados que los datos no ven, y campañas que corren sin ninguna regla encendida.
function resumenCobertura(inf) {
  if (!inf || !Array.isArray(inf.reglas)) return '';
  let caidas = 0, activas = 0, ilegibles = 0, noVistas = 0;
  inf.reglas.forEach((r) => {
    noVistas += Number(r.nNoVistas) || 0;
    (r.tareas || []).forEach((t) => { if (t.caidas) { caidas += Number(t.caidas.n) || 0; activas += Number(t.caidas.activas) || 0; ilegibles += Number(t.caidas.ilegibles) || 0; } });
  });
  const f = [], cb = inf.cobertura;
  if (caidas) f.push(caidas + (caidas === 1 ? ' asignada NO se miró' : ' asignadas NO se miraron') + ' porque su cuenta de anuncios está caída' + (activas ? ' (' + (activas === 1 ? '1 figura encendida' : activas + ' figuran encendidas') + ')' : '') + (ilegibles ? '; hay cuentas que NO SE PUEDEN LEER y ahí puede haber gasto sin control' : ''));
  if (noVistas) f.push(noVistas + (noVistas === 1 ? ' campaña asignada no aparece' : ' campañas asignadas no aparecen') + ' en los datos del dash');
  if (cb && cb.sinRegla > 0) f.push('hay ' + cb.sinRegla + ' de las ' + cb.corriendo + ' campañas que corren que ninguna regla encendida cubre');
  return f.length ? 'Ojo: ' + f.join('; ') + '.' : '';
}
// Campañas que CUMPLEN una regla de pausar, siguen encendidas y NO se pausaron. Salen de tres lados: la pagina (la regla pide espaciar la pausa y ya se aplico), las puertas del script (datos dudosos, cuenta de
// solo lectura, fallo reciente) y el ejecutor (omitio la accion por algo que no es «ya estaba pausado»). Cada una lleva su motivo en palabras de quien lo lee.
const MOTIVO_ESPACIADA = 'la acción de la regla está espaciada («Una vez al día»…) y ya se aplicó antes';
const OMISION_NORMAL = /^(ya estaba pausado|la regla (esta apagada|ya no existe|esta fuera de sus fechas|esta fuera de su horario)|la campaña ya no esta asignada|el pais de la campaña no esta en el alcance|repetida en la misma llamada)/;
function sinTocarDe(informe, otras) {
  const l = [];
  ((informe && informe.sinTocar && informe.sinTocar.lista) || []).forEach((x) => l.push({ regla: x.regla, pais: x.pais, id: String(x.id || ''), nombre: x.nombre, motivo: MOTIVO_ESPACIADA }));
  (otras || []).forEach((o) => { if (o.estado === 'omitida' && o.p && o.p.tipo === 'pausar') l.push({ regla: o.p.regla, pais: o.p.country, id: String(o.p.fbId || ''), nombre: o.p.nombre, motivo: String(o.motivo || '').replace(/^no se ejecutó: /, '') }); });
  return l;
}
function sinTocarDelEjecutor(resultados, nombres) {
  const l = [];
  (resultados || []).forEach((x) => {
    const n = nombres[x.clave]; if (!n || n.tipo !== 'pausar' || x.estado !== 'omitida' || OMISION_NORMAL.test(String(x.motivo || ''))) return;
    l.push({ regla: n.regla, pais: n.pais, id: String(n.id || ''), nombre: n.nombre, motivo: String(x.motivo || 'el ejecutor no la aplicó') });
  });
  return l;
}
function textoSinTocar(lista) {
  const filas = lista.slice(0, 8).map((x) => '• ' + String(x.pais || '').toUpperCase() + ' · ' + x.nombre + (x.id ? ' (id …' + String(x.id).slice(-6) + ')' : '') + '\n  ' + (x.regla ? '«' + x.regla + '»: ' : '') + x.motivo);
  // El consejo depende del motivo: espaciar la pausa se arregla en la frecuencia de la accion; datos dudosos o una cuenta de solo lectura se resuelven solos o a mano.
  const porFrecuencia = lista.some((x) => x.motivo === MOTIVO_ESPACIADA), porOtros = lista.some((x) => x.motivo !== MOTIVO_ESPACIADA);
  return '⚠ Reglas automáticas: ' + lista.length + (lista.length === 1 ? ' campaña CUMPLE' : ' campañas CUMPLEN') + ' una regla de pausar y SIGUE ENCENDIDA (no se pausó)\n\n' + filas.join('\n') + (lista.length > 8 ? '\n… y ' + (lista.length - 8) + ' más (mira /reglas > Registro)' : '') +
    (porFrecuencia ? '\n\nSi quieres que la regla la pause cada vez que se encienda, pon la frecuencia de su acción en «Cada vez que se cumple».' : '') +
    (porOtros ? '\n\nLo demás se reintenta solo en el próximo refresco; si urge, pausa la campaña a mano.' : '') + '\nPara frenar una regla, apágala en /reglas.';
}
// Avisa UNA vez por situacion: la misma lista no se repite en 12 h (una campaña que alguien enciende todos los dias no puede llenar el chat), y si cambia se avisa de nuevo. Respeta AVISOS_PAUSADOS (desde 2026-10-28):
// la lista cambia casi cada hora y llenaba el chat; queda en el registro de /reglas.
async function alertarSinTocar(lista, ahora) {
  if (!lista || !lista.length) return;
  const firma = lista.map((x) => String(x.id) + '|' + String(x.motivo).replace(/\d+/g, '#').slice(0, 40)).sort().join(';');
  const log = leerLog(), prev = log.alertas && log.alertas.sinTocar;
  if (prev && prev.firma === firma && ahora - prev.t < 12 * 3600000) { console.log('Reglas automaticas: ' + lista.length + ' campañas cumplen y siguen encendidas; ya se avisó hace menos de 12 h.'); return; }
  console.log('Telegram (cumplen y siguen encendidas): ' + await avisar(textoSinTocar(lista)));
  log.alertas = Object.assign({}, log.alertas, { sinTocar: { t: ahora, firma } });
  guardarLog(log);
}

// ---- reglas que se contradicen ----
// La pagina (rgChoques) mira, entre las reglas encendidas y vivas en este momento (fechas y horario), las campañas que cumplen a la vez una tarea que las pausa y otra que las reactiva (o una que sube el
// presupuesto y otra que lo baja) y las deja en `informe.choques`, cada una con su `frase`. Aqui solo se resumen y se AVISAN: no se cambia ninguna regla ni ninguna campaña. Un aviso por situacion: el mismo
// par de reglas no se repite en 24 h (y si el choque desaparece dos revisiones seguidas, el siguiente se avisa de nuevo). Sale aunque los avisos de rutina esten en pausa.
function choquesDe(informe) {
  return informe && Array.isArray(informe.choques) ? informe.choques.filter((c) => c && typeof c.frase === 'string' && c.frase && c.a && c.b) : [];
}
function resumenChoques(informe) {
  const cs = choquesDe(informe); if (!cs.length) return '';
  // Cada frase termina con lo que pasa en cada vuelta («Una la apaga y la otra…»): se quita esa parte, sin cortar por el primer punto (un nombre de regla puede llevarlo: «Pausa v2. noche»).
  const corta = (f) => f.replace(/\. (Una la apaga|Una le sube el presupuesto)[^]*$/, '');
  return 'ATENCIÓN: ' + (cs.length === 1 ? 'dos reglas se contradicen' : cs.length + ' choques entre reglas') + ' (' + cs.slice(0, 2).map((c) => corta(c.frase)).join('; ').slice(0, 260) + ').';
}
function textoChoques(lista) {
  const filas = lista.slice(0, 4).map((c) => '• ' + c.frase + (Array.isArray(c.ejemplos) && c.ejemplos.length ? '\n  Por ejemplo: ' + c.ejemplos.slice(0, 2).map((e) => String(e.pais || '').toUpperCase() + ' · ' + e.nombre + (e.id ? ' (id …' + String(e.id).slice(-6) + ')' : '')).join('; ') : ''));
  return '⚠ Reglas automáticas: ' + (lista.length === 1 ? 'dos reglas se contradicen' : lista.length + ' choques entre reglas') + ' (solo es un aviso: no se cambió ninguna regla ni ninguna campaña)\n\n' + filas.join('\n') + (lista.length > 4 ? '\n… y ' + (lista.length - 4) + ' más (mira /reglas)' : '') +
    '\n\nCambia las condiciones de una de las dos reglas, o apaga una, en /reglas.';
}
async function alertarChoques(lista, ahora) {
  if (!lista || !lista.length) return;
  const firma = lista.map((c) => [c.a.reglaId, c.a.accion, c.b.reglaId, c.b.accion].join('|')).sort().join(';');
  const log = leerLog(), prev = log.alertas && log.alertas.choques;
  if (prev && prev.firma === firma && ahora - prev.t < DIA) { console.log('Reglas automaticas: ' + lista.length + ' choques entre reglas; ya se avisó hace menos de 24 h.'); return; }
  // `siempre: true` (como el aviso de «cumple una regla de pausar y sigue encendida»): dos reglas que se deshacen una a la otra gastan plata sin que nadie lo haya decidido, y con los avisos de rutina
  // en pausa (AVISOS_PAUSADOS) este aviso no llegaria nunca. Sale a lo sumo una vez por par de reglas cada 24 h.
  const envio = await avisar(textoChoques(lista), { siempre: true });
  console.log('Telegram (reglas que se contradicen): ' + envio);
  // Solo se anota como «avisado» si de verdad salio: si Telegram fallo o no hay credenciales, la proxima revision lo intenta de nuevo.
  if (envio !== 'enviado') return;
  log.alertas = Object.assign({}, log.alertas, { choques: { t: ahora, firma, limpias: 0 } });
  guardarLog(log);
}

// El informe en texto plano (--cifras): una fila por campaña / conjunto / anuncio, con cada condicion y su numero.
function textoInforme(inf) {
  const out = [];
  (inf.reglas || []).forEach((r) => (r.tareas || []).forEach((t, i) => {
    out.push('Regla «' + r.nombre + '»' + ((r.tareas || []).length > 1 ? ' · tarea ' + (i + 1) : '') + ': miró ' + t.miradas + ' ' + (NIV_PL[t.nivel] || t.nivel) + ' (' + (t.corren || 0) + ' corriendo), cumplen ' + t.cumplen + '.');
    (t.filas || []).forEach((x) => {
      out.push('  ' + (x.cumple ? '[CUMPLE]    ' : '[no cumple] ') + x.pais + ' · ' + (t.nivel === 'ad' ? [x.campana, x.conjunto, x.nombre] : t.nivel === 'adset' ? [x.campana, x.nombre] : [x.nombre]).filter(Boolean).join(' › ') + (x.estado === 'PAUSED' ? ' (pausado)' : ''));
      (x.g || []).forEach((g, gi) => { if ((x.g || []).length > 1) out.push('      grupo ' + (gi + 1)); g.forEach((c) => out.push('      ' + (c[0] ? '✓ ' : '✗ ') + c[1])); });
      out.push('      ' + t.cuando + ': ' + x.cifras);
      if (x.cumple) (x.acc || []).forEach((a) => out.push('      → ' + a[0] + (a[1] ? ': se ejecuta' : ': no se ejecuta (' + a[2] + ')')));
    });
    if (t.miradas > (t.filas || []).length) out.push('  … y ' + (t.miradas - t.filas.length) + ' más que no se detallan (van primero los que cumplen y los que más gastaron).');
  }));
  return out.join('\n');
}

// ---- de la accion que decide la pagina a la que se manda al ejecutor ----
const ETIQUETA = { pausar: 'Pausar', reactivar: 'Reactivar', subir: 'Subir presupuesto', bajar: 'Bajar presupuesto', fijar: 'Fijar presupuesto', duplicar: 'Duplicar', avisar: 'Aviso' };
const PASADO = { pausar: 'Pausó', reactivar: 'Reactivó', subir: 'Subió el presupuesto de', bajar: 'Bajó el presupuesto de', fijar: 'Fijó el presupuesto de', duplicar: 'Duplicó' };
function accionDe(p) {
  return { reglaId: p.reglaId, clave: p.clave, tipo: p.tipo, nivel: p.nivel, id: String(p.fbId), campanaId: String(p.campanaId || ''), campana: p.campana, cuenta: String(p.accountId || ''), pais: p.country, nombre: p.nombre,
    razon: p.razon, cifras: p.cifras, actual: p.actual == null ? null : p.actual, nuevo: p.nuevo == null ? null : p.nuevo, copiaNuevo: p.copiaNuevo || 0, ms: p.ms };
}
// Cada decision que anota el registro (lo que se ve en /reglas > Registro > Revisiones). `estado`: pendiente | hecha | omitida | fallo | aviso.
function entradaDe(p, t, estado, motivo) {
  const e = { t, clave: p.clave, reglaId: p.reglaId, regla: p.regla, tipo: p.tipo, nivel: p.nivel, fbId: p.fbId, accountId: p.accountId || '', country: p.country, nombre: p.nombre, campana: p.campana,
    texto: p.texto, cifras: p.cifras || '', actual: p.actual == null ? null : p.actual, nuevo: p.nuevo == null ? null : p.nuevo, estado };
  if (motivo) e.motivo = String(motivo).slice(0, 220);
  return e;
}
// Reparte lo que decidio la pagina: lo que se puede ejecutar, lo que no (con su motivo) y los avisos.
// `o.bloqueos` = { pais: motivo } (datos dudosos de ese pais), `o.fallos` = { clave: hora } de las acciones que fallaron hace poco, `o.global` = motivo que frena TODO (la pagina es vieja).
function clasificar(props, ahora, o) {
  o = o || {};
  const ejecutables = [], otras = [], bloqueos = o.bloqueos || {}, fallos = o.fallos || {};
  props.forEach((p) => {
    if (p.tipo === 'avisar') { otras.push({ p, estado: 'aviso', motivo: '' }); return; }
    if (o.global) { otras.push({ p, estado: 'omitida', motivo: 'no se ejecutó: ' + o.global }); return; }
    if (bloqueos[p.country]) { otras.push({ p, estado: 'omitida', motivo: 'no se ejecutó: ' + bloqueos[p.country] }); return; }
    if (p.sinPermiso) { otras.push({ p, estado: 'omitida', motivo: 'la cuenta de anuncios es de solo lectura para el token del dash: no se puede escribir (dale «Administrar campañas» al usuario del sistema en Business Manager)' }); return; }
    if (fallos[p.clave] && ahora - fallos[p.clave] < REINTENTO_FALLO) { otras.push({ p, estado: 'omitida', motivo: 'falló hace poco; se reintenta pasadas 6 h' }); return; }
    ejecutables.push(p);
  });
  return { ejecutables, otras };
}

// ---- 1) REVISAR ----
async function revisar() {
  if (!fs.existsSync(HTML)) salir(HTML + ' no existe; no se revisa nada.', 'error', 'No se revisó: no existe reglas.html (el armado del dash falló antes de este paso).');
  let cfg;
  try { cfg = await leerReglas(); } catch (e) { const m = String((e && e.message) || e).slice(0, 100); salir('no pude leer las reglas (' + m + ').', 'error', 'No se revisó: no pude leer las reglas guardadas (' + m + ').'); }
  if (!cfg || !Array.isArray(cfg.lista)) salir('no hay reglas guardadas.', 'omitida', 'No se revisó: todavía no hay reglas guardadas.');
  const encendidas = cfg.lista.filter((r) => r && r.on === true);
  if (!encendidas.length) salir('ninguna regla esta encendida (' + cfg.lista.length + ' guardadas).', 'omitida',
    'No se revisó: ninguna regla está encendida (hay ' + cfg.lista.length + (cfg.lista.length === 1 ? ' regla guardada' : ' reglas guardadas') + '). Enciende una en /reglas y asígnale campañas.');

  // Cuales de las encendidas les toca ahora, segun su programa y su ultima revision.
  const log = leerLog();
  const ahora = ahoraMs();
  const ultimas = log.ultimas && typeof log.ultimas === 'object' && !Array.isArray(log.ultimas) ? log.ultimas : {};
  const turnos = encendidas.map((r) => ({ r, t: tocaRevisar(r, ahora, Number(ultimas[r.id]) || 0) }));
  const activas = turnos.filter((x) => x.t.toca).map((x) => x.r);
  const esperando = turnos.filter((x) => !x.t.toca);
  const txtEspera = esperando.length ? 'Esperan su turno: ' + esperando.map((x) => '«' + String(x.r.nombre || 'sin nombre') + '» (' + x.t.motivo + ')').join('; ') + '.' : '';

  if (!activas.length) {
    console.log('Reglas automaticas: a ninguna regla le toca todavia (' + esperando.length + ' en espera).');
    anotarRevision(log, 'espera', 'A ninguna regla le tocaba revisarse todavía. ' + txtEspera, { esperando: esperando.length }, true);
    if (!SECO) { limpiarPendientes(); guardarLog(log); }
    return;
  }

  // Lo ya aplicado (para no volver a proponerlo) y de donde salen los numeros.
  const mem = await leerMemoria();
  let props, informe;
  try { ({ props, informe } = evaluar(activas, mem.memoria, encendidas)); } catch (e) { const m = String((e && e.message) || e).slice(0, 200); salir('no pude evaluar: ' + m, 'error', 'Falló al revisar las reglas: ' + m); }

  // Datos en los que se puede confiar: hojas de ventas al dia y pagina reciente.
  const { bloqueos, sinBitacora } = bloqueosPorDatos(ahora);
  const generado = informe && informe.datos ? Date.parse(informe.datos) : NaN;
  const viejos = isFinite(generado) && ahora - generado > DATOS_MAX_MIN * 60000;
  const fallos = {};
  log.entradas.forEach((x) => { if (x.estado === 'fallo' && x.clave && !(fallos[x.clave] > x.t)) fallos[x.clave] = x.t; });
  const { ejecutables, otras } = clasificar(props, ahora, { bloqueos, fallos, global: viejos ? 'la página de la que salen los números es de hace más de ' + DATOS_MAX_MIN + ' min' : '' });

  // Lo que acaba de revisarse queda con su hora (solo si la evaluacion salio bien: si fallo, se reintenta en la proxima corrida). Las reglas que ya no existen se olvidan.
  const ultimasAntes = {};
  activas.forEach((r) => { ultimasAntes[r.id] = Number(ultimas[r.id]) || 0; ultimas[r.id] = ahora; });
  const ids = new Set(cfg.lista.map((r) => r && r.id));
  Object.keys(ultimas).forEach((k) => { if (!ids.has(k)) delete ultimas[k]; });
  log.ultimas = ultimas;
  // Las cifras exactas de esta revision quedan en el registro (/reglas > Registro): el gasto, las ventas, el ROAS… con los que se decidio cada fila, aunque no salga ninguna accion.
  const inf = acotarInforme(informe), mir = resumenMiradas(inf);
  // Lo que NO se miro (cuentas caidas, ids que los datos no ven, campañas sin regla) y lo que cumple una regla de pausar y sigue encendido: se dice en el registro con todas sus letras.
  const cob = resumenCobertura(informe), sinTocar = sinTocarDe(informe, otras);
  // Reglas que se contradicen ahora (solo se avisa). Si ya no hay choques se olvida el aviso anterior: el proximo, aunque sea del mismo par, se avisa de nuevo.
  const choques = choquesDe(informe);
  // El aviso anterior se olvida cuando pasan DOS revisiones seguidas sin choque (asi, uno que va y viene cada hora no manda un mensaje cada vez, y uno que de verdad se arreglo y vuelve se avisa de nuevo).
  if (log.alertas && log.alertas.choques) {
    if (choques.length) log.alertas.choques.limpias = 0;
    else { log.alertas.choques.limpias = (Number(log.alertas.choques.limpias) || 0) + 1; if (log.alertas.choques.limpias >= 2) delete log.alertas.choques; }
  }
  if (inf && Array.isArray(inf.choques)) inf.choques = choques;          // al registro solo van los bien formados (la pantalla lo lee de ahi)
  if (inf) log.informe = Object.assign({ t: ahora }, inf);
  if (CIFRAS && inf) console.log(textoInforme(inf));

  // Las decisiones nuevas al registro. Las que no se pueden ejecutar y ya estaban anotadas igual en las ultimas 24 h no se repiten (cada refresco volveria a listar lo mismo); las por ejecutar siempre se anotan.
  const vistas = new Set(log.entradas.filter((x) => ahora - x.t < DIA && x.estado !== 'pendiente').map((x) => x.clave + '|' + x.estado + '|' + (x.motivo || '')));
  const nuevas = [];
  ejecutables.forEach((p) => nuevas.push(entradaDe(p, ahora, 'pendiente', '')));
  otras.forEach((o) => { const e = entradaDe(o.p, ahora, o.estado, o.motivo); if (!vistas.has(e.clave + '|' + e.estado + '|' + (e.motivo || ''))) { vistas.add(e.clave + '|' + e.estado + '|' + (e.motivo || '')); nuevas.push(e); } });
  const avisosNuevos = nuevas.filter((e) => e.estado === 'aviso');

  const bloqueadosTxt = Object.keys(bloqueos).map((c) => c.toUpperCase() + ': ' + bloqueos[c]).join('; ');
  console.log('Reglas automaticas: ' + activas.length + ' reglas revisadas, ' + props.length + ' acciones (' + ejecutables.length + ' por ejecutar, ' + (otras.length - otras.filter((o) => o.estado === 'aviso').length) + ' no ejecutables, ' + otras.filter((o) => o.estado === 'aviso').length + ' avisos)' + (esperando.length ? ', ' + esperando.length + ' esperan su turno' : '') + '.');
  if (bloqueadosTxt) console.log('  Sin ejecutar por datos dudosos -> ' + bloqueadosTxt);
  if (viejos) console.log('  La pagina es de hace mas de ' + DATOS_MAX_MIN + ' min: no se ejecuta nada.');
  if (mem.memoria === null) console.log('  Memoria de lo ya aplicado no disponible (' + mem.motivo + '): el ejecutor vuelve a comprobarlo.');
  if (sinBitacora) console.log('  Sin data/refresh-status.json: no se pudo comprobar que las hojas de ventas esten al dia.');
  const nombres = activas.map((r) => String(r.nombre || 'sin nombre')).join(', ').slice(0, 140);
  const resumen = 'Revisó ' + activas.length + (activas.length === 1 ? ' regla' : ' reglas') + ' (' + nombres + '): ' + (mir.txt ? mir.txt + '; ' : '') +
    (ejecutables.length ? 'acciones por ejecutar: ' + ejecutables.length : 'nada por ejecutar') + '.' +
    (bloqueadosTxt ? ' No se ejecutó nada en ' + Object.keys(bloqueos).map((c) => c.toUpperCase()).join(', ') + ' porque sus datos son dudosos (' + bloqueadosTxt + ').' : '') +
    (viejos ? ' No se ejecutó nada: la página con los números tiene más de ' + DATOS_MAX_MIN + ' minutos.' : '') +
    (sinTocar.length ? ' ATENCIÓN: ' + sinTocar.length + (sinTocar.length === 1 ? ' campaña cumple' : ' campañas cumplen') + ' una regla de pausar y sigue encendida sin pausarse (' + Array.from(new Set(sinTocar.map((x) => x.motivo))).slice(0, 2).join('; ').slice(0, 200) + ').' : '') +
    (cob ? ' ' + cob : '') + (choques.length ? ' ' + resumenChoques(informe) : '') + (txtEspera ? ' ' + txtEspera : '');
  anotarRevision(log, 'ok', resumen, { reglas: activas.length, acciones: ejecutables.length, noEjecutables: otras.filter((o) => o.estado !== 'aviso').length, avisos: avisosNuevos.length, esperando: esperando.length, miradas: mir.miradas, cumplen: mir.cumplen }, false, ahora);
  log.entradas = nuevas.concat(log.entradas).slice(0, MAX_LOG);

  if (SECO) {
    console.log('(--seco) Por ejecutar:');
    ejecutables.forEach((p) => console.log('  • ' + ETIQUETA[p.tipo] + ' · ' + p.country + ' · ' + p.nombre + '  ← ' + p.razon + '  [' + p.cifras + ']'));
    otras.forEach((o) => console.log('  ✗ (' + o.estado + ') ' + ETIQUETA[o.p.tipo] + ' · ' + o.p.country + ' · ' + o.p.nombre + (o.motivo ? '  ← ' + o.motivo : '')));
    choques.forEach((c) => console.log('  ⚠ (se contradicen) ' + c.frase));
    return;
  }
  guardarLog(log);
  // El paso de ejecutar solo toma esto. `run` y `t` evitan que ejecute lo de otra corrida si el runner conserva la carpeta.
  guardarPendientes({ v: 1, t: ahora, run: CORRIDA, datos: informe && informe.datos ? informe.datos : '', url: URL_BASE, ultimasAntes, sinTocar: sinTocar.slice(0, 30), acciones: ejecutables.map(accionDe), nombres:ejecutables.map((p) => ({ clave: p.clave, tipo: p.tipo, pais: p.country, nombre: p.nombre, id: String(p.fbId), regla: p.regla, reglaId: p.reglaId })) });

  if (choques.length) await alertarChoques(choques, ahora);
  if (avisosNuevos.length) {
    const lineas = avisosNuevos.slice(0, 12).map((x) => '• ' + x.country.toUpperCase() + ' · ' + x.nombre + ' (' + x.campana + ')\n  ' + x.texto);
    console.log('Telegram (avisos): ' + await avisar('Reglas automaticas: ' + avisosNuevos.length + (avisosNuevos.length === 1 ? ' aviso nuevo' : ' avisos nuevos') + '\n\n' + lineas.join('\n') + (avisosNuevos.length > 12 ? '\n… y ' + (avisosNuevos.length - 12) + ' mas (mira /reglas)' : '')));
  }
}

// ---- 2) EJECUTAR ----
const pausa = (ms) => new Promise((r) => setTimeout(r, ms));
// Los numeros de ZapData que bajo esta corrida (paso «Estado de los numeros de ZapData», antes de armar el dash): con el id de cada conexion el ejecutor le pregunta a ZapData en vivo si el numero de la
// campaña esta conectado antes de encenderla o subirle el presupuesto, y si ZapData no contesta usa este estado. Sin el archivo va vacio y el ejecutor no sube el gasto de ninguna campaña de ZapData.
// Solo viaja lo que hace falta para decidir: ni el nombre de la conexion ni telefonos.
// La marca BOM se arma con su codigo y no con un escape escrito: el editor convierte el escape en el caracter invisible (ver RG_MARCAS en templates/reglas-modulo.js).
const MARCA_BOM = new RegExp('^' + String.fromCharCode(0xfeff));
function numerosDeLaCorrida() {
  try {
    const d = JSON.parse(fs.readFileSync(NUMEROS, 'utf8').replace(MARCA_BOM, ''));
    return { capturadoEn: String(d.capturadoEn || ''), instancias: (Array.isArray(d.instancias) ? d.instancias : []).map((i) => ({ wpp: String((i && i.wpp) || ''), pais: String((i && i.pais) || ''), estado: i && i.estado === 'connected' ? 'connected' : 'disconnected', id: String((i && i.id) || '') })) };
  } catch (e) { return { capturadoEn: '', instancias: [] }; }
}
async function llamarEjecutor(acciones) {
  const r = await fetch(URL_BASE + '/api/reglas-ejecutar', { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-clave-reglas': llave() }, body: JSON.stringify({ acciones, numeros: numerosDeLaCorrida() }), signal: AbortSignal.timeout(90000) });
  let j = null; try { j = await r.json(); } catch (e) { /* sin cuerpo */ }
  return { status: r.status, j };
}
// Ordena lo que dijo el servidor en {hechas, omitidas, fallos} y arma las lineas para Telegram.
function resumenEjecucion(res, nombres) {
  const por = { hecha: [], omitida: [], fallo: [], pendiente: [] };
  res.forEach((x) => { (por[x.estado] || por.pendiente).push(x); });
  const info = (clave) => nombres[clave] || { tipo: '', pais: '', nombre: clave, regla: '' };
  // Con el final del id de Facebook: el equipo duplica campañas y varias llevan EXACTAMENTE el mismo nombre («… | 106» x2); sin el id no se sabria cual se toco.
  const linea = (x) => { const n = info(x.clave); return '• ' + (PASADO[n.tipo] || n.tipo) + ' · ' + String(n.pais || '').toUpperCase() + ' · ' + n.nombre + (n.id ? ' (id …' + String(n.id).slice(-6) + ')' : ''); };
  return { por, info, linea };
}
function textoTelegramEjecucion(res, nombres, extra) {
  const { por, info, linea } = resumenEjecucion(res, nombres);
  let t = 'Reglas automáticas: ' + (por.hecha.length ? 'ejecutó ' + por.hecha.length + (por.hecha.length === 1 ? ' cambio' : ' cambios') + ' en Facebook' : 'no cambió nada en Facebook') + (por.fallo.length ? ' · ' + por.fallo.length + (por.fallo.length === 1 ? ' falló' : ' fallaron') : '') + '\n';
  if (por.hecha.length) t += '\n' + por.hecha.slice(0, 12).map((x) => linea(x) + (x.detalle ? '\n  ' + x.detalle : '')).join('\n') + (por.hecha.length > 12 ? '\n… y ' + (por.hecha.length - 12) + ' más' : '') + '\n';
  if (por.fallo.length) t += '\nFallaron:\n' + por.fallo.slice(0, 6).map((x) => linea(x) + '\n  ' + (x.motivo || '')).join('\n') + '\n';
  if (por.omitida.length) t += '\nNo se hizo (' + por.omitida.length + '): ' + Object.entries(por.omitida.reduce((o, x) => { const m = String(x.motivo || '').slice(0, 60); o[m] = (o[m] || 0) + 1; return o; }, {})).slice(0, 4).map((p) => p[1] + ' × ' + p[0]).join('; ') + '\n';
  if (extra) t += '\n' + extra + '\n';
  return t + '\nPara frenar una regla, apágala en /reglas (Registro > «Lo que se aplicó» tiene el detalle).';
}

async function ejecutar() {
  let pend = null;
  try { pend = JSON.parse(fs.readFileSync(PENDIENTES, 'utf8')); } catch (e) { /* no hay */ }
  if (!pend || !Array.isArray(pend.acciones)) { console.log('Reglas automaticas (ejecutar): no hay acciones por ejecutar en esta corrida.'); return; }
  const ahora = ahoraMs();
  if (CORRIDA && pend.run && pend.run !== CORRIDA) { console.log('Reglas automaticas (ejecutar): las acciones pendientes son de otra corrida (' + pend.run + '); no se ejecuta nada.'); return; }
  if (ahora - pend.t > 60 * 60000) { console.log('Reglas automaticas (ejecutar): las acciones pendientes son de hace mas de 1 h; no se ejecuta nada.'); return; }
  if (!pend.acciones.length) {
    console.log('Reglas automaticas (ejecutar): nada por ejecutar.');
    // Aunque no haya nada que ejecutar, puede haber campañas que cumplen una regla de pausar y siguen encendidas (la regla pide espaciar la pausa, datos dudosos...): eso se avisa.
    if (!SECO) await alertarSinTocar(pend.sinTocar || [], ahora);
    return;
  }
  const nombres = {}; (pend.nombres || []).forEach((n) => { nombres[n.clave] = n; });

  const lote = pend.acciones.slice(0, MAX_POR_CORRIDA), aplazadasPorTope = pend.acciones.slice(MAX_POR_CORRIDA);
  if (SECO) {
    console.log('(--seco) Se mandarian ' + lote.length + ' acciones al ejecutor' + (aplazadasPorTope.length ? ' (y ' + aplazadasPorTope.length + ' quedarian para la proxima revision por el tope de ' + MAX_POR_CORRIDA + ')' : '') + ':');
    lote.forEach((a) => console.log('  • ' + ETIQUETA[a.tipo] + ' · ' + a.pais + ' · ' + a.nombre + (a.nuevo != null ? '  ' + a.actual + ' -> ' + a.nuevo : '')));
    return;
  }
  if (!llave()) { console.log('Reglas automaticas (ejecutar): falta REGLAS_CLAVE; no se ejecuta nada.'); return; }

  // ¿Esta el ejecutor? Justo despues de publicar, el sitio ANTERIOR puede seguir contestando unos segundos o minutos en algunos centros de datos: sin la ruta nueva contesta 404 o 401 («pide sesion»).
  // Eso se espera. Lo que NO se espera es un 401 con el sello del ejecutor (`ejecutor: true`): ese es una llave mala (CLAVE_REGLAS en Cloudflare distinta de REGLAS_CLAVE en GitHub).
  let vivo = null;
  const INTENTOS = 9, espera = Number(process.env.REGLAS_ESPERA_MS) || 20000;          // 9 intentos cada 20 s = 3 min (las pruebas acortan la espera)
  for (let i = 0; i < INTENTOS; i++) {
    const m = await leerMemoria();
    if (m.memoria) { vivo = m; break; }
    if (m.status === 401 && m.delEjecutor) { vivo = { error: 'el ejecutor rechazó la llave (401): CLAVE_REGLAS en Cloudflare y REGLAS_CLAVE en GitHub deben ser la misma' }; break; }
    vivo = { error: m.status === 401 || m.status === 404 ? 'el ejecutor todavía no está publicado en el sitio (contestó ' + m.status + ' sin su sello): se espera al siguiente refresco' : m.motivo };
    if (i < INTENTOS - 1) { console.log('  el ejecutor no contesta todavia (' + m.motivo + '); se reintenta en ' + Math.round(espera / 1000) + ' s…'); await pausa(espera); }
  }
  const log = leerLog();
  const marcar = (e, estado, motivo, extra) => { e.estado = estado; if (motivo) e.motivo = String(motivo).slice(0, 220); else delete e.motivo; Object.assign(e, extra || {}); };
  const entradaDeClave = (clave) => log.entradas.filter((x) => x.t === pend.t && x.clave === clave)[0];

  if (!vivo || vivo.error) {
    const msg = 'No se ejecutó nada: ' + (vivo && vivo.error ? vivo.error : 'el ejecutor no contesta') + '. Las ' + pend.acciones.length + ' acciones quedan para la próxima revisión.';
    console.log('Reglas automaticas (ejecutar): ' + msg);
    // Las reglas se revisaran de nuevo en el proximo refresco (no esperan su intervalo).
    if (pend.ultimasAntes) { log.ultimas = Object.assign({}, log.ultimas, pend.ultimasAntes); }
    const rev = (log.revisiones || [])[0]; if (rev && rev.t === pend.t) rev.texto += ' ' + msg;
    pend.acciones.forEach((a) => { const e = entradaDeClave(a.clave); if (e) marcar(e, 'pendiente', 'el ejecutor no contestó: sigue en la próxima revisión'); });
    guardarLog(log);
    console.log('Telegram: ' + await avisar('Reglas automáticas: ' + msg, { siempre: /rechazó la llave/.test(msg) }));
    return;
  }

  // Se manda por tandas. Lo que el servidor «aplaza» (mucho trabajo para una llamada) se vuelve a mandar en la siguiente.
  const resultados = [], inicio = Date.now();
  let cola = lote.slice(), corte = '';
  while (cola.length) {
    if (Date.now() - inicio > TOPE_SEGUNDOS * 1000) { corte = 'se acabó el tiempo del paso (' + TOPE_SEGUNDOS + ' s)'; break; }
    const tanda = cola.splice(0, POR_LLAMADA);
    let r;
    try { r = await llamarEjecutor(tanda); } catch (e) { r = { status: 0, j: null, err: String((e && e.message) || e).slice(0, 80) }; }
    if (r.status !== 200 || !r.j || !r.j.ok || !Array.isArray(r.j.resultados)) {
      corte = 'el ejecutor contestó ' + (r.status || r.err) + (r.j && r.j.error ? ' (' + r.j.error + ')' : '');
      tanda.forEach((a) => resultados.push({ clave: a.clave, estado: 'fallo', motivo: corte }));
      break;
    }
    const aplazadas = [];
    tanda.forEach((a) => {
      const x = r.j.resultados.filter((y) => y.clave === a.clave)[0];
      if (!x) resultados.push({ clave: a.clave, estado: 'fallo', motivo: 'el ejecutor no devolvió resultado para esta acción' });
      else if (x.estado === 'aplazada') aplazadas.push(a);
      else resultados.push(x);
    });
    if (aplazadas.length === tanda.length && aplazadas.length) { corte = 'el ejecutor aplazó todo lo de una llamada'; aplazadas.forEach((a) => resultados.push({ clave: a.clave, estado: 'pendiente', motivo: 'aplazada por el servidor' })); break; }
    cola = aplazadas.concat(cola);
    // Si todo lo que se ha intentado falla, no se sigue golpeando a Facebook.
    const hechas = resultados.filter((x) => x.estado === 'hecha').length, fallos = resultados.filter((x) => x.estado === 'fallo').length;
    if (fallos >= 5 && !hechas) { corte = 'las primeras ' + fallos + ' acciones fallaron: se detiene para no insistir'; break; }
  }
  cola.forEach((a) => { resultados.push({ clave: a.clave, estado: 'pendiente', motivo: corte ? corte + ': sigue en la próxima revisión' : 'sigue en la próxima revisión' }); });
  aplazadasPorTope.forEach((a) => { resultados.push({ clave: a.clave, estado: 'pendiente', motivo: 'tope de ' + MAX_POR_CORRIDA + ' por corrida: sigue en la próxima revisión' }); });

  // A cada decision del registro se le pone lo que paso.
  resultados.forEach((x) => {
    const e = entradaDeClave(x.clave); if (!e) return;
    const extra = {}; if (x.aviso) extra.aviso = String(x.aviso).slice(0, 200); if (x.despues != null) extra.despues = x.despues; if (x.copia) extra.copia = x.copia; if (x.efectivo) extra.efectivo = x.efectivo;
    marcar(e, x.estado === 'hecha' ? 'hecha' : x.estado === 'fallo' ? 'fallo' : x.estado === 'pendiente' ? 'pendiente' : 'omitida', x.estado === 'hecha' ? (x.aviso || x.notas || '') : x.motivo, extra);
    if (x.estado === 'hecha' && x.despues != null && e.nuevo != null) e.nuevo = x.despues;
  });
  const cuenta = (est) => resultados.filter((x) => x.estado === est).length;
  const hechas = cuenta('hecha'), omitidas = cuenta('omitida'), fallaron = cuenta('fallo'), pendientes = cuenta('pendiente');
  const rev = (log.revisiones || [])[0];
  const frase = 'ejecutó ' + hechas + (hechas === 1 ? ' acción' : ' acciones') + (omitidas ? ', ' + omitidas + (omitidas === 1 ? ' no se hizo' : ' no se hicieron') : '') + (fallaron ? ', ' + fallaron + (fallaron === 1 ? ' falló' : ' fallaron') : '') + (pendientes ? ', ' + pendientes + ' siguen para la próxima revisión' : '') + '.';
  if (rev && rev.t === pend.t) {
    rev.texto = /acciones por ejecutar: \d+\./.test(rev.texto) ? rev.texto.replace(/acciones por ejecutar: \d+\./, frase) : rev.texto + ' Se ' + frase;
    Object.assign(rev, { hechas, omitidas, fallaron, pendientes });
  }
  // Si algo de lo que tocaba quedo sin hacer por un corte, esas reglas no esperan su intervalo para volver a intentarlo.
  if (pendientes && pend.ultimasAntes) {
    const reglasPend = new Set(resultados.filter((x) => x.estado === 'pendiente').map((x) => (nombres[x.clave] || {}).reglaId).filter(Boolean));
    reglasPend.forEach((id) => { if (pend.ultimasAntes[id] != null) log.ultimas[id] = pend.ultimasAntes[id]; });
  }
  log.actualizado = new Date(ahoraMs()).toISOString();
  guardarLog(log);
  // Lo que se muestra bajo cada cambio del Telegram: los numeros con los que se decidio y, si el servidor lo dice, la nota (p. ej. «ya se había pausado a las 3:17 p. m. y volvió a estar encendida»).
  const conDetalle = resultados.map((x) => (x.estado === 'hecha' ? Object.assign({}, x, { detalle: [(entradaDeClave(x.clave) || {}).cifras || '', x.notas || ''].filter(Boolean).join(' · ') }) : x));
  console.log('Reglas automaticas (ejecutar): ' + frase.charAt(0).toUpperCase() + frase.slice(1) + (corte ? ' (' + corte + ')' : ''));
  resultados.filter((x) => x.estado === 'fallo').slice(0, 10).forEach((x) => console.log('  fallo · ' + ((nombres[x.clave] || {}).nombre || x.clave) + ': ' + x.motivo));
  resultados.filter((x) => x.estado === 'omitida').slice(0, 10).forEach((x) => console.log('  no se hizo · ' + ((nombres[x.clave] || {}).nombre || x.clave) + ': ' + x.motivo));
  // Solo avisa si algo FALLO (lo que salio bien queda en el registro de /reglas): antes salia un mensaje por cada revision que movia algo, cerca de uno por hora.
  if (fallaron) console.log('Telegram: ' + await avisar(textoTelegramEjecucion(conDetalle, nombres, corte ? 'Corte: ' + corte + '.' : ''), { siempre: true }));
  // Lo que cumple una regla de pausar y sigue encendido: lo que vio la revision (la regla pide espaciar la pausa, datos dudosos...) mas lo que el ejecutor omitio por algo que no es «ya estaba pausado».
  await alertarSinTocar((pend.sinTocar || []).concat(sinTocarDelEjecutor(resultados, nombres)), ahora);
  limpiarPendientes();
}

if (require.main === module) {
  // La pagina sigue con vida un instante despues de cerrar su ventana: si sus datos pasan de 1 h pide un refresco, la peticion falla (aqui no hay red) y al pintar el aviso se cae porque ya no hay
  // `document`. Sin esto Node moria con codigo 1 en medio de la corrida -incluso antes de que saliera el mensaje de Telegram-. No es un error de la revision: se dice y se sigue.
  process.on('unhandledRejection', (e) => { console.log('Reglas automaticas: se ignora un error de la pagina despues de evaluar (' + String((e && e.message) || e).slice(0, 100) + ').'); });
  (EJECUTAR ? ejecutar() : revisar()).catch((e) => { console.log('Reglas automaticas: error inesperado (' + String((e && e.message) || e).slice(0, 160) + '); no se tumba el refresco.'); process.exit(0); });
} else {
  module.exports = { numerosDeLaCorrida, programaDe, hoyCO, cadaTxt, fechaCorta, proximaRevision, desdeTxt, tocaRevisar, enHorario, horaNumCO, horasTxt, choquesDe, resumenChoques, textoChoques, cargarJsdom, correrScriptsDeLaPagina, acotarInforme, resumenMiradas, textoInforme,
    bloqueosPorDatos, esNoVentas, clasificar, accionDe, entradaDe, textoTelegramEjecucion, resumenCobertura, sinTocarDe, sinTocarDelEjecutor, textoSinTocar, MOTIVO_ESPACIADA, MAX_POR_CORRIDA, POR_LLAMADA };
}
