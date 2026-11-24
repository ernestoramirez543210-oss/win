// Programar en Facebook las campañas de una oferta nueva: el paso 4 de la guia de lanzamiento de /fabrica, que antes montaba la trafficker a mano. Reglas puras en functions/_campanas.js.
//
//   GET  /api/fabrica-campanas?id=<proyecto>&pais=<Pais>           -> { ok, plan }       la vista previa: cuenta, pagina, numero, presupuesto, hora, nombres, videos, textos, reglas que las cuidan, bloqueos
//   POST /api/fabrica-campanas { accion:'comprobar', id, pais, textos }  -> { ok, checks }  Facebook revisa la campaña y el conjunto SIN crear nada (execution_options = validate_only)
//   POST /api/fabrica-campanas { accion:'empezar', id, pais, textos, huella } -> { ok, campanas }   crea los registros (fcamp:...) de las campañas que faltan; todavia no toca Facebook
//   POST /api/fabrica-campanas { accion:'avanzar', id, pais, anuncio }        -> { ok, campana, esperar? }   hace el siguiente paso de UNA campaña (la pantalla lo llama hasta que queda lista)
//
// Quien: el dueño y la trafficker (decision del dueño, 2026-11-05: «botón con vista previa» que pulsa Carla). Es plata: falla cerrado (exigirSesion, sin la salida de REQUIRE_LOGIN). El resto del equipo
// ve el boton en la guia pero este servidor le contesta 403.
//
// Como se arma una campaña (todo con la llave del usuario del sistema del pais, FACEBOOK_TOKENS, que nunca sale de aqui):
//   1. video: Facebook lo baja de Drive (file_url) -> hay que tener el archivo compartido «cualquiera con el enlace»; la vista previa lo comprueba antes;
//   2. espera a que Facebook procese el video y toma su miniatura;
//   3. creativo: el del anuncio modelo con el video, el texto y el mensaje de WhatsApp NUESTROS (pagina, Instagram, boton y saludo del modelo);
//   4. campaña EN PAUSA (presupuesto del modelo, en la campaña) -> 5. conjunto EN PAUSA que arranca a las 5 a. m. -> 6. anuncio EN PAUSA;
//   7. NO se enciende (decision del dueño, 2026-11-06): queda LISTA EN PAUSA para que el equipo la revise y la encienda en Ads Manager; con el conjunto a las 5 a. m., al encenderla arranca a esa hora.
//      Antes de darla por lista se revisa que sea la unica con ese nombre, que el conjunto mande al telefono elegido y que la campaña siga en pausa; entonces se activan el conjunto y el anuncio
//      POR DENTRO (la campaña sigue en pausa). Si algo no cuadra, todo queda en pausa. Si el numero no esta conectado en ZapData queda un «Ojo» en su nota (y la guia no marca «botón»).
// Si algo falla a mitad, lo creado queda en pausa (no gasta) y el registro dice que paso; «avanzar» otra vez sigue desde donde quedo, sin volver a crear lo que ya existe (ids guardados, y antes de crear
// la campaña se busca en Facebook una con el mismo nombre creada en pausa hace poco: si una lectura atrasada de KV no trajera el id, no se duplica).
//
// Registro: una clave por campaña (fcamp:<proyecto>:<pais>:<anuncio>, sin vencimiento) con cada paso, quien y cuando; al terminar, una linea en el registro compartido de /reglas (reg:..., tipo «crear») y,
// cuando todas las del lote quedaron listas, las tareas del paso 4 de la guia se marcan solas y queda una linea en «Qué ha pasado» de la oferta.
// Cupos de KV: ~10 escrituras por campaña mas una cada 30 s mientras Facebook procesa el video (el candado; 6 campañas con videos de 3 min = ~100 de las 1.000 diarias de la suite). Subpedidos: el peor «avanzar» hace ~31 con el paso final (leer la campaña, activar conjunto y anuncio; tope del plan gratuito: 50).

import { exigirSesion } from '../_sesion.js';
import * as F from '../_fabrica.js';
import * as C from '../_campanas.js';
import { leerProyecto, guardarProyecto, anunciosDe, leerAnuncio, leerEntrega } from './fabrica.js';
import { canalesAhora } from './zapdata-canales.js';

const ROLES = ['owner', 'trafficker'];
const TAREAS_PASO_4 = ['cuenta', 'campanas', 'boton'];          // «programada» la marca quien las enciende (nacen en pausa)
const json = (s, o) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
const propio = C.sinPropio;
const sinLlave = (s) => String(s == null ? '' : s).replace(/access_token=[^&\s"]+/g, 'access_token=…');

// ---------------------------------------------------------------- Facebook
// Mensaje de error para una persona: Facebook manda uno tecnico y otro escrito para quien usa Ads Manager; se prefiere el segundo.
function errorDe(d, status) {
  const e = (d && d.error) || {};
  const m = e.error_user_msg ? ((e.error_user_title ? e.error_user_title + ': ' : '') + e.error_user_msg) : (e.message || ('HTTP ' + status));
  return C.una(sinLlave(m), 300);
}
async function fbGet(ruta, token, params) {
  const u = new URL(C.GRAPH + '/' + ruta);
  Object.keys(params || {}).forEach((k) => u.searchParams.set(k, params[k]));
  u.searchParams.set('access_token', token);
  try {
    const r = await fetch(u.toString());
    const d = await r.json().catch(() => null);
    if (!r.ok || !d || d.error) return { error: errorDe(d, r.status), codigo: d && d.error ? d.error.code : r.status };
    return d;
  } catch (e) { return { error: 'No pude hablar con Facebook: ' + C.una(sinLlave((e && e.message) || e), 120) }; }
}
async function fbPost(ruta, token, params) {
  const cuerpo = new URLSearchParams(Object.assign({}, params, { access_token: token }));
  try {
    const r = await fetch(C.GRAPH + '/' + ruta, { method: 'POST', body: cuerpo });
    const d = await r.json().catch(() => null);
    if (!r.ok || !d || d.error) return { error: errorDe(d, r.status), codigo: d && d.error ? d.error.code : r.status };
    return d;
  } catch (e) { return { error: 'No pude hablar con Facebook: ' + C.una(sinLlave((e && e.message) || e), 120) }; }
}

// Los bloques de llave de UN pais (un pais puede tener varios Business Manager). La lista `accounts` es un piso: vacia = «lo que la llave alcance».
function bloquesDe(env, pais) {
  let t;
  try { t = JSON.parse(env.FACEBOOK_TOKENS || '{}'); } catch { return []; }
  const c = t && propio(t, pais) ? t[pais] : null;
  if (!c) return [];
  return (Array.isArray(c.tokens) ? c.tokens : [c]).filter((b) => b && typeof b.systemUserToken === 'string' && b.systemUserToken)
    .map((b) => ({ token: b.systemUserToken, cuentas: (Array.isArray(b.accounts) ? b.accounts : []).map((x) => String(x).replace(/^act_/, '')).filter((x) => C.ID_FB.test(x)) }));
}
async function cuentasDe(b) {
  if (b.cuentas.length) return b.cuentas;
  const d = await fbGet('me/assigned_ad_accounts', b.token, { fields: 'account_id', limit: '50' });
  return ((d && d.data) || []).map((x) => String(x.account_id || '').replace(/^act_/, '')).filter((x) => C.ID_FB.test(x));
}
// La llave que puede con esa cuenta (la que encontro el modelo). null si ninguna.
async function llaveDeCuenta(env, pais, cuenta) {
  const bs = bloquesDe(env, pais);
  for (const b of bs) if (b.cuentas.includes(cuenta)) return b.token;
  for (const b of bs) if (!b.cuentas.length && (await cuentasDe(b)).includes(cuenta)) return b.token;
  return null;
}

// Busca la campaña modelo en las cuentas del pais: el conjunto mas nuevo que manda a WhatsApp con el numero elegido.
async function buscarModelo(env, pais, numero, ahoraMs) {
  const bs = bloquesDe(env, pais);
  if (!bs.length) return { error: 'El dash no tiene llave de Facebook para ' + pais + '.' };
  const desde = Math.floor(ahoraMs / 1000) - C.DIAS_MODELO * 86400;
  const campos = 'name,created_time,destination_type,promoted_object,optimization_goal,billing_event,targeting,attribution_spec,daily_budget,bid_strategy,bid_amount,campaign{id,name,objective,daily_budget,bid_strategy,buying_type,special_ad_categories}';
  let mejor = null, leidas = 0;
  const nombres = {};                // cuenta -> nombres de campañas recientes (para no repetir una que alguien ya monto a mano)
  for (const b of bs) {
    const cuentas = await cuentasDe(b);
    const res = await Promise.all(cuentas.map((c) => fbGet('act_' + c + '/adsets', b.token, { fields: campos, limit: '500', filtering: JSON.stringify([{ field: 'created_time', operator: 'GREATER_THAN', value: desde }]) })));
    res.forEach((d, i) => {
      if (!d || d.error || !Array.isArray(d.data)) return;
      leidas++;
      nombres[cuentas[i]] = d.data.map((s) => s.campaign && s.campaign.name).filter(Boolean);
      const m = C.elegirModelo(d.data, numero);
      if (m && (!mejor || Date.parse(m.created_time || 0) > Date.parse(mejor.conjunto.created_time || 0))) mejor = { cuenta: cuentas[i], token: b.token, conjunto: m };
    });
  }
  if (!mejor) return { error: leidas ? 'Ese número de WhatsApp no ha tenido campañas en las cuentas de ' + pais + ' en los últimos ' + C.DIAS_MODELO + ' días: la primera la monta la trafficker a mano y desde la segunda el dash copia esa.' : 'No pude leer ninguna cuenta publicitaria de ' + pais + ' con la llave del dash.' };
  mejor.nombres = nombres[mejor.cuenta] || [];
  return mejor;
}

// ---------------------------------------------------------------- ZapData (el freno del numero)
// 'conectado' | 'caido' | 'sin-saber'. Con `destino` (las ultimas 4 cifras del telefono al que manda el anuncio) exige ademas que la conexion elegida SEA ese telefono: si el numero se cambio por otro
// con el mismo «WPP #N», el freno no puede vigilar una linea y el anuncio mandar a otra (revision de seguridad 5-nov). Devuelve { estado, tel4 } (tel4 = el de la conexion viva, '' si no se sabe).
async function estadoNumero(env, num, destino) {
  if (!num || !num.id) return { estado: 'sin-saber', tel4: '' };
  let c = null;
  try { c = await canalesAhora(env); } catch { c = null; }
  if (!c || !c.ok || !Array.isArray(c.canales)) return { estado: 'sin-saber', tel4: '' };
  const vivo = c.canales.filter((x) => String(x.id || '').toLowerCase() === String(num.id).toLowerCase())[0];
  const tel4 = vivo ? C.ultimas4(vivo.tel4) : '';
  if (!vivo || vivo.estado !== 'connected') return { estado: 'caido', tel4 };
  if (destino !== undefined && (!tel4 || tel4 !== destino)) return { estado: 'otro-telefono', tel4 };
  return { estado: 'conectado', tel4 };
}

// ---------------------------------------------------------------- Drive
// ¿Facebook podra bajar el video? Se piden los primeros bytes del archivo por la misma direccion que usara Facebook.
async function videoAlcanzable(driveId) {
  if (!driveId) return { ok: false, motivo: 'El anuncio aprobado no tiene el enlace de su ARCHIVO en Drive (tiene una carpeta u otro enlace).' };
  try {
    const r = await fetch(C.urlDescargaDrive(driveId), { headers: { Range: 'bytes=0-63' }, redirect: 'follow' });
    const tipo = r.headers.get('content-type') || '';
    // solo el primer trozo: si Drive ignorara el Range, el video entero no entra a la memoria del servidor
    let buf = new Uint8Array(0);
    if (r.body && r.body.getReader) {
      const lector = r.body.getReader();
      try { const t = await lector.read(); if (t && t.value) buf = new Uint8Array(t.value).slice(0, 64); } catch { buf = new Uint8Array(0); }
      try { await lector.cancel(); } catch { /* ya cerrado */ }
    }
    if ((r.status === 200 || r.status === 206) && C.pareceVideo(tipo, buf)) return { ok: true };
    return { ok: false, motivo: 'Facebook no podrá bajar el video: en Drive, compártelo como «Cualquier persona con el enlace» (solo ese archivo, no la carpeta).' };
  } catch { return { ok: false, motivo: 'No pude revisar el video en Drive ahora; intenta de nuevo en un momento.' }; }
}

// ---------------------------------------------------------------- KV
async function leerRegistro(env, proyecto, pais, anuncio) {
  try { const v = await env.SUITE.get(C.claveRegistro(proyecto, pais, anuncio), { type: 'json' }); return v && typeof v === 'object' && v.proyecto === proyecto && v.anuncio === anuncio ? v : null; } catch { return null; }
}
async function guardarRegistro(env, r) {
  await env.SUITE.put(C.claveRegistro(r.proyecto, r.pais, r.anuncio), JSON.stringify(r));
}
function paso(r, txt) { r.pasos = (Array.isArray(r.pasos) ? r.pasos : []).concat([{ t: new Date().toISOString(), txt: C.una(txt, 200) }]).slice(-30); }
// Una linea en el registro compartido de /reglas (el mismo formato que escribe /api/toggle-ad, ficha en la metadata). Nunca rompe lo hecho.
async function lineaRegistroCompartido(env, r, ok, msg) {
  try {
    const t = Date.now();
    const e = { t, tipo: 'crear', nivel: 'campaign', id: String(r.ids.campana || ''), cuenta: r.cuenta, ok: !!ok, regla: 'Fábrica: programar campañas', nombre: C.una(r.nombre, 100), pais: r.pais,
      razon: C.una('Oferta nueva, creada EN PAUSA: al encenderla arranca el ' + ((r.inicio && r.inicio.dia) || '') + ' a las 5 a. m. con ' + C.textoPlata(r.presupuesto.unidades, r.moneda) + ' al día. La armó ' + (r.por === 'owner' ? 'el dueño' : 'la trafficker') + '.', 200), msg: C.una(msg, 160) };
    Object.keys(e).forEach((k) => { if (e[k] === '' || e[k] == null) delete e[k]; });
    const clave = 'reg:' + String(9999999999999 - t).padStart(13, '0') + '-' + Math.random().toString(36).slice(2, 7);
    await env.SUITE.put(clave, '1', { metadata: e, expirationTtl: 90 * 86400 });
  } catch { /* el registro compartido es un extra: el de la campaña ya quedo */ }
}

// ---------------------------------------------------------------- el plan (vista previa)
async function armarPlan(env, p, pais, ahoraMs) {
  const k = C.clavePais(pais);
  const bloqueos = [], avisos = [];
  const lz = propio(p.lanzamiento, pais) ? p.lanzamiento[pais] : null;
  const num = lz && lz.num && typeof lz.num === 'object' ? lz.num : null;
  const todos = await anunciosDe(env, p);
  const aprobados = todos.filter((a) => a.estado === 'aprobada');
  // los registros de TODOS los anuncios de la oferta (tambien los cancelados despues de programarlos): el tope de 6 cuenta lo que ya se programo, se haya cancelado el anuncio o no
  const regTodos = await Promise.all(todos.map((a) => leerRegistro(env, p.id, k, a.id)));
  const registros = aprobados.map((a) => regTodos[todos.indexOf(a)]);
  const plan = {
    pais, numero: num ? { wpp: num.wpp || '', nombre: C.una(num.nombre, 80), tel4: num.tel4 || '', estado: '' } : null, cuenta: null, pagina: null, modelo: null, presupuesto: null, inicio: null,
    campanas: [], textos: null, clave: '', reglas: [], bloqueos, avisos, huella: '', tope: C.TOPE_POR_PAIS,
  };
  if (!num) bloqueos.push('Falta elegir el número de WhatsApp de ' + pais + ' (paso 2 de la guía).');
  if (!aprobados.length) bloqueos.push('No hay anuncios aprobados todavía (paso 3 de la ficha).');
  let vivo = null;
  if (num) {
    vivo = await estadoNumero(env, num);
    plan.numero.estado = vivo.estado;
    if (plan.numero.estado === 'caido') bloqueos.push('El número ' + (num.wpp ? 'WPP #' + num.wpp : 'elegido') + ' está desconectado en ZapData ahora: no se programa una campaña que mande a un número caído.');
    if (plan.numero.estado === 'sin-saber') bloqueos.push('ZapData no contestó si el número sigue conectado: inténtalo en un momento.');
  }
  if (!num || !aprobados.length) { plan.campanas = aprobados.map((a, i) => ({ anuncio: a.id, registro: C.resumenRegistro(registros[i]) })); return { plan }; }

  // sin las 4 cifras guardadas en la guia (ZapData no contesto al elegir), las de la conexion VIVA: el modelo se busca por telefono, no solo por el #N
  const mod = await buscarModelo(env, k, { wpp: num.wpp, tel4: num.tel4 || (vivo && vivo.tel4) || '' }, ahoraMs);
  if (mod.error) { bloqueos.push(mod.error); plan.campanas = aprobados.map((a, i) => ({ anuncio: a.id, registro: C.resumenRegistro(registros[i]) })); return { plan }; }
  const [info, paginas, ads, recientes] = await Promise.all([
    fbGet('act_' + mod.cuenta, mod.token, { fields: 'name,currency,timezone_offset_hours_utc,account_status' }),
    fbGet('act_' + mod.cuenta + '/promote_pages', mod.token, { fields: 'id,name', limit: '100' }),
    fbGet(mod.conjunto.id + '/ads', mod.token, { fields: 'creative{id,object_story_spec,degrees_of_freedom_spec,contextual_multi_ads}', limit: '1' }),
    // las campañas de los ultimos 14 dias de esa cuenta (el nombre lleva el dia de arranque: una mas vieja no puede chocar): para no repetir una que alguien ya monto a mano
    fbGet('act_' + mod.cuenta + '/campaigns', mod.token, { fields: 'name', limit: '500', filtering: JSON.stringify([{ field: 'created_time', operator: 'GREATER_THAN', value: Math.floor(ahoraMs / 1000) - 14 * 86400 }]) }),
  ]);
  if (recientes && Array.isArray(recientes.data)) recientes.data.forEach((x) => { if (x && x.name) mod.nombres.push(x.name); });
  else bloqueos.push('No pude leer las campañas recientes de la cuenta (para no repetir una montada a mano): inténtalo en un momento.');
  const creativo = ads && Array.isArray(ads.data) && ads.data[0] && ads.data[0].creative ? ads.data[0].creative : null;
  const pl = C.plantillaDeModelo({ conjunto: mod.conjunto, creativo });
  const moneda = (info && info.currency) || '';
  plan.cuenta = { id: mod.cuenta, nombre: C.una(info && info.name, 80), moneda, estado: info && info.account_status };
  plan.modelo = { campana: pl.campana.nombre, creada: pl.conjunto.creado, objetivo: pl.campana.objetivo, optimizacion: pl.conjunto.optimizacion };
  if (info && info.error) bloqueos.push('No pude leer la cuenta publicitaria: ' + info.error);
  else if (Number(info.account_status) !== 1) bloqueos.push('La cuenta publicitaria «' + plan.cuenta.nombre + '» no está activa en Facebook (estado ' + C.una(info.account_status, 4) + '): no gasta.');
  const puede = paginas && Array.isArray(paginas.data) && paginas.data.some((x) => String(x.id) === pl.creativo.pagina);
  plan.pagina = { id: pl.creativo.pagina, puede: !!puede };
  if (!pl.creativo.pagina) bloqueos.push('El anuncio modelo no dice con qué página de Facebook sale.');
  else if (!puede) bloqueos.push('Falta un permiso en Facebook: el usuario del sistema del dash no puede anunciar con la página ' + pl.creativo.pagina + '. En el administrador comercial, asígnele esa página con permiso para crear anuncios.');
  if (!creativo) avisos.push('No pude leer el anuncio de la campaña modelo: el título y el saludo de WhatsApp salen por defecto.');
  // el telefono al que manda el anuncio (el del modelo) TIENE que ser el de la conexion elegida: si no, el freno vigilaria una linea y el anuncio mandaria a otra
  const destino = C.ultimas4(pl.conjunto.objetoPromovido.whatsapp_phone_number);
  plan.numero.destino = destino;
  if (!destino) bloqueos.push('La campaña modelo no dice a qué teléfono de WhatsApp manda.');
  else if (vivo && vivo.estado !== 'sin-saber' && vivo.tel4 !== destino) bloqueos.push('La última campaña de este número manda al teléfono …' + destino + ', pero la conexión elegida en ZapData es ' + (vivo.tel4 ? 'la …' + vivo.tel4 : 'otra (ZapData no dice su teléfono)') + ': revisa el número en el paso 2. La primera campaña de un teléfono nuevo la monta la trafficker a mano.');
  const pres = C.presupuestoDe(pl);
  const malo = pres ? C.presupuestoMalo(pres.unidades, moneda) : 'la campaña modelo no tiene un presupuesto diario';
  if (malo) bloqueos.push('Presupuesto: ' + malo + '.');
  else plan.presupuesto = { unidades: pres.unidades, en: pres.en, moneda, texto: C.textoPlata(pres.unidades, moneda) };
  const off = info && info.timezone_offset_hours_utc != null ? Number(info.timezone_offset_hours_utc) : -5;
  const ini = C.inicioProgramado(ahoraMs, off);
  plan.inicio = { iso: ini.iso, dia: ini.dia, fecha: ini.fecha, offset: off, ms: ini.ms };
  const t = C.textosIniciales(p, creativo);
  plan.clave = t.clave;
  plan.textos = { mensaje: t.mensaje, titulo: t.titulo, descripcion: t.descripcion, autollenado: t.autollenado, bienvenida: t.bienvenida };
  if (!t.clave) avisos.push('La oferta no tiene la palabra clave del flujo guardada: escríbela en «Palabra clave del flujo» (la del inicio del flujo de Venta en ZapData). Sin ella no se programa.');

  // Las campañas: una por anuncio aprobado, hasta el tope por pais (contando las que ya se programaron).
  const ya = regTodos.filter((r) => r && (r.estado !== 'error' || (r.ids && r.ids.campana))).length;
  let cupo = Math.max(0, C.TOPE_POR_PAIS - ya);
  // se compara SIN la fecha: una montada a mano para otro dia («… | 5 NOV | 101») tambien es la campaña de ese anuncio
  const nombresHechos = new Set(mod.nombres.map((x) => C.nombreSinFecha(x)));
  const revisar = [];
  aprobados.forEach((a, i) => {
    const r = registros[i];
    const nombre = C.nombreCampana(num.wpp || '?', p.nombre, ini.dia, a.id);
    const did = C.idDeDrive(a.entrega && a.entrega.drive);
    const fila = { anuncio: a.id, nombre: r ? C.una(r.nombre, 200) : nombre, registro: C.resumenRegistro(r), drive: did ? 'https://drive.google.com/file/d/' + did + '/view' : '', video: null, listo: false, motivo: '' };
    if (r && !(r.estado === 'error' && !(r.ids && r.ids.campana))) { plan.campanas.push(fila); return; }
    if (!num.wpp) { fila.motivo = 'El número elegido no tiene «WPP #N»: el nombre de la campaña no lo diría y las reglas no podrían frenar el número.'; plan.campanas.push(fila); return; }
    if (nombresHechos.has(C.nombreSinFecha(nombre))) { fila.motivo = 'Ya hay en Facebook una campaña de este anuncio con este número (alguien la montó a mano, quizá con otra fecha).'; plan.campanas.push(fila); return; }
    if (cupo <= 0) { fila.motivo = 'Pasa el tope de ' + C.TOPE_POR_PAIS + ' campañas por país en este lanzamiento.'; plan.campanas.push(fila); return; }
    cupo--;
    revisar.push({ fila, drive: C.idDeDrive(a.entrega && a.entrega.drive) });
    plan.campanas.push(fila);
  });
  const vistos = await Promise.all(revisar.map((x) => videoAlcanzable(x.drive)));
  revisar.forEach((x, i) => { x.fila.video = vistos[i].ok ? 'ok' : 'no'; if (vistos[i].ok) x.fila.listo = true; else x.fila.motivo = vistos[i].motivo; });
  const nuevas = plan.campanas.filter((c) => c.listo);
  if (!nuevas.length && !bloqueos.length && !plan.campanas.some((c) => c.registro)) bloqueos.push('Ningún anuncio está listo para programar: mira el motivo de cada uno.');
  if (plan.presupuesto) plan.presupuesto.totalDia = C.textoPlata(plan.presupuesto.unidades * nuevas.length, moneda);

  // Las reglas que las van a cuidar
  let reglas = null;
  try { reglas = await env.SUITE.get('reglas:config', { type: 'json' }); } catch { reglas = null; }
  const ejemplo = nuevas[0] ? nuevas[0].nombre : C.nombreCampana(num.wpp || '?', p.nombre, ini.dia, aprobados[0].id);
  plan.reglas = C.reglasQueCubren(reglas, k, ejemplo);
  if (!plan.reglas.length) avisos.push('Ninguna regla encendida de pausar las va a cuidar sola (ninguna toma «todas» o «campañas futuras» de ' + pais + ' con este nombre): asígnalas en Reglas cuando empiecen a gastar.');
  // nacen en pausa para revisarlas: una regla de REACTIVAR sobre «todas» o «futuras» podria encenderlas antes (revision del 6-nov)
  const reactivan = C.reglasQueReactivan(reglas, k, ejemplo);
  if (reactivan.length) avisos.push('Ojo: ' + (reactivan.length === 1 ? 'la regla ' : 'las reglas ') + reactivan.map((x) => '«' + x.nombre + '»').join(', ') + ' puede' + (reactivan.length === 1 ? '' : 'n') + ' REACTIVAR sola' + (reactivan.length === 1 ? '' : 's') + ' campañas de ' + pais + ' (toma «todas» o «campañas futuras»): podría encender estas antes de que las revisen. Revísala en Reglas.');

  plan.huella = C.huellaPlan({ cuenta: mod.cuenta, presupuesto: pres ? pres.unidades : 0, moneda, inicio: ini.iso, anuncios: nuevas.map((c) => c.anuncio), modelo: pl.campana.id, pagina: pl.creativo.pagina, destino });
  return { plan, interno: { mod, pl, pres, ini, moneda, off, num, nuevas: nuevas.map((c) => c.anuncio), aprobados, k, destino, regTodos, todos } };
}

// ---------------------------------------------------------------- un paso de una campaña
const listoVideo = (d) => { const s = d && d.status; return !!s && (s.video_status === 'ready' || (s.processing_phase && s.processing_phase.status === 'complete')); };
const errorVideo = (d) => { const s = d && d.status; return !!s && (s.video_status === 'error' || (s.processing_phase && s.processing_phase.status === 'error') || (s.uploading_phase && s.uploading_phase.status === 'error')); };

// ¿El anuncio sigue aprobado en la Fabrica? (alguien pudo cancelarlo despues de pulsar «Programar»). Lee solo ese pedido y su entrega.
async function sigueAprobado(env, r) {
  try {
    const [a, e] = await Promise.all([leerAnuncio(env, r.proyecto, r.anuncio), leerEntrega(env, r.proyecto, r.anuncio)]);
    const c = F.anuncioCompleto(a, e);
    return !!c && c.estado === 'aprobada';
  } catch { return false; }
}
// Las campañas de la cuenta con el nombre de este registro creadas desde 2 min antes del registro (el filtro es por fecha; el nombre se compara aqui, sin depender de que Facebook filtre por nombre).
// propias = mismo presupuesto y objetivo (las creo el dash), de la mas vieja a la mas nueva; ajenas = el resto. { error } si Facebook no contesto.
async function campanasConNombre(token, r) {
  const desde = Math.floor(Date.parse(r.t || 0) / 1000) - 120;
  const d = await fbGet('act_' + r.cuenta + '/campaigns', token, { fields: 'id,name,status,daily_budget,objective,created_time', limit: '200', filtering: JSON.stringify([{ field: 'created_time', operator: 'GREATER_THAN', value: desde }]) });
  if (d.error || !Array.isArray(d.data)) return { error: d.error || 'respuesta sin lista' };
  const mismas = d.data.filter((x) => x && x.name === r.nombre)
    .sort((a, b) => Date.parse(a.created_time || 0) - Date.parse(b.created_time || 0) || String(a.id).localeCompare(String(b.id)));
  const pres = r.presupuesto && r.presupuesto.en === 'campana' ? String(Math.round(r.presupuesto.unidades)) : null;
  // propia = la que crearia el dash: mismo nombre, objetivo y presupuesto, Y en pausa (una igual pero encendida la monto o la encendio alguien: no se adopta; revision del 6-nov)
  const propia = (x) => x.status === 'PAUSED' && x.objective === r.plantilla.campana.objetivo && (pres ? String(x.daily_budget) === pres : !(Number(x.daily_budget) > 0));
  return { propias: mismas.filter(propia).map((x) => ({ id: String(x.id) })), ajenas: mismas.filter((x) => !propia(x)) };
}
// Los conjuntos de una campaña o los anuncios de un conjunto, del mas viejo al mas nuevo. { error } si Facebook no contesto.
async function hijos(token, ruta) {
  const d = await fbGet(ruta, token, { fields: 'id,created_time', limit: '25' });
  if (d.error || !Array.isArray(d.data)) return { error: d.error || 'respuesta sin lista' };
  return { lista: d.data.map((x) => ({ id: String(x.id), t: Date.parse(x.created_time || 0) || 0 })).sort((a, b) => a.t - b.t || a.id.localeCompare(b.id)) };
}
// Despues de crear un conjunto o un anuncio: si hay mas de uno (dos pestañas a la vez), todos siguen con el MAS VIEJO y los demas se pausan (no gastan). Devuelve el id con el que se sigue.
async function quedarseConElPrimero(token, ruta, creado, r) {
  const h = await hijos(token, ruta);
  if (h.error || h.lista.length <= 1) return creado;
  const primero = h.lista[0].id;
  for (const x of h.lista.slice(1)) await fbPost(x.id, token, { status: 'PAUSED' });
  paso(r, 'Había ' + h.lista.length + ' copias (dos pestañas a la vez): se sigue con la primera y las demás quedaron en pausa.');
  return primero;
}
// La hora de arranque ya paso: se toma la siguiente 5 a. m. (y si cambia el dia, tambien el nombre de la campaña, que lo lleva). Con `conjunto`, se cambia en Facebook.
async function correrInicio(token, r, conjunto) {
  const nuevo = C.inicioProgramado(Date.now(), r.offset);
  if (conjunto) {
    const s = await fbPost(conjunto, token, { start_time: nuevo.iso });
    if (s.error) return { error: s.error };
  }
  if (nuevo.dia !== r.inicio.dia && r.oferta) {
    const nombre = C.nombreCampana(r.numero.wpp, r.oferta, nuevo.dia, r.anuncio);
    if (r.ids.campana) { const c = await fbPost(r.ids.campana, token, { name: nombre }); if (!c.error) r.nombre = nombre; } else r.nombre = nombre;
  }
  r.inicio = { ms: nuevo.ms, iso: nuevo.iso, dia: nuevo.dia };
  paso(r, 'La hora de arranque ya había pasado: ahora arranca el ' + nuevo.dia + ' a las 5 a. m.');
  return {};
}

async function avanzarUna(env, r, rol) {
  const token = await llaveDeCuenta(env, r.pais, r.cuenta);
  const guardar = async () => { r.actualizado = new Date().toISOString(); await guardarRegistro(env, r); };
  const fallar = async (msg) => { r.estado = 'error'; r.error = C.una(msg, 400); paso(r, 'Falló: ' + msg); await guardar(); return { r }; };
  if (!token) return fallar('El dash ya no tiene una llave de Facebook que alcance la cuenta ' + r.cuenta + '.');
  r.error = '';

  // 1. el video: Facebook lo baja de Drive
  if (!r.ids.video) {
    const d = await fbPost('act_' + r.cuenta + '/advideos', token, { file_url: C.urlDescargaDrive(r.drive), name: C.una(r.nombre, 200) });
    if (d.error || !C.ID_FB.test(String(d.id || ''))) return fallar('Facebook no aceptó el video: ' + (d.error || 'no devolvió el id'));
    r.ids.video = String(d.id); r.estado = 'video'; r.videoDesde = new Date().toISOString(); paso(r, 'Video enviado a Facebook (lo baja de Drive y lo procesa).');
    await guardar();
    return { r, esperar: true };
  }
  // 2. que termine de procesarlo, y su miniatura
  if (!r.imagen) {
    const d = await fbGet(r.ids.video, token, { fields: 'status' });
    // un video que fallo o se quedo pegado se vuelve a mandar con «Seguir» (sin el id, el paso 1 lo sube de nuevo; un video de mas en la biblioteca no gasta)
    if (errorVideo(d)) { r.ids.video = ''; return fallar('Facebook no pudo procesar el video (¿está compartido con enlace en Drive y es un MP4?). «Seguir» lo vuelve a enviar.'); }
    if (!listoVideo(d)) {
      if (Date.now() - Date.parse(r.videoDesde || 0) > C.VIDEO_MAX_MIN * 60000) { r.ids.video = ''; return fallar('Facebook lleva más de ' + C.VIDEO_MAX_MIN + ' minutos procesando el video. «Seguir» lo vuelve a enviar.'); }
      return { r, esperar: true };
    }
    const th = await fbGet(r.ids.video + '/thumbnails', token, { fields: 'uri,is_preferred' });
    const lista = (th && Array.isArray(th.data)) ? th.data : [];
    const mini = lista.filter((x) => x.is_preferred)[0] || lista[0];
    if (!mini || !/^https:\/\//.test(String(mini.uri || ''))) return fallar('Facebook no dio una miniatura del video.');
    r.imagen = String(mini.uri); r.estado = 'armando'; paso(r, 'Video listo en Facebook.');
    await guardar();
  }
  r.estado = 'armando';
  // 3. el creativo (no gasta; si el Instagram del modelo no se deja usar, va sin el)
  if (!r.ids.creativo) {
    let d = await fbPost('act_' + r.cuenta + '/adcreatives', token, C.paramsCreativo(r.plantilla, r.nombre, r.ids.video, r.imagen, r.textos, true));
    if (d.error && /instagram/i.test(d.error) && r.plantilla.creativo.instagram) {
      d = await fbPost('act_' + r.cuenta + '/adcreatives', token, C.paramsCreativo(r.plantilla, r.nombre, r.ids.video, r.imagen, r.textos, false));
      if (!d.error) paso(r, 'El Instagram del modelo no se pudo usar: el anuncio sale con la página.');
    }
    if (d.error || !C.ID_FB.test(String(d.id || ''))) return fallar('Facebook no aceptó el anuncio (texto, video o página): ' + (d.error || 'sin id'));
    r.ids.creativo = String(d.id); paso(r, 'Anuncio armado con el video y el texto.');
    await guardar();
  }
  // 4. la campaña, EN PAUSA. Nada a ciegas (revision de seguridad 5-nov): antes de crear se listan las campañas de la cuenta con ESTE nombre creadas desde que se armo el registro; si Facebook no
  // contesta, no se crea. Una campaña con el nombre que no tiene el presupuesto y el objetivo nuestros no se adopta (la monto alguien a mano). Despues de crear se vuelve a listar: si dos personas
  // crearon a la vez, todas se quedan con la MAS VIEJA y la sobrante queda en pausa y vacia.
  if (!r.ids.campana) {
    if (!(await sigueAprobado(env, r))) return fallar('El anuncio ' + r.anuncio + ' ya no está aprobado en la Fábrica: no se crea su campaña.');
    const antes = await campanasConNombre(token, r);
    if (antes.error) return fallar('No pude revisar en Facebook si la campaña ya existe (' + antes.error + '): no se crea otra a ciegas. Pulsa «Seguir» en un momento.');
    let suya = antes.propias[0] || null;
    if (!suya && antes.ajenas.length) return fallar('Ya hay en Facebook una campaña con este nombre que no creó el dash (u otro presupuesto u objetivo): no se toca. Revísala en Ads Manager.');
    if (suya) paso(r, 'La campaña ya estaba creada: se sigue con ella.');
    else {
      const d = await fbPost('act_' + r.cuenta + '/campaigns', token, C.paramsCampana(r.plantilla, r.nombre, r.presupuesto));
      if (d.error || !C.ID_FB.test(String(d.id || ''))) return fallar('Facebook no creó la campaña: ' + (d.error || 'sin id'));
      paso(r, 'Campaña creada en pausa.');
      const despues = await campanasConNombre(token, r);
      suya = (!despues.error && despues.propias[0]) || { id: String(d.id) };
      if (String(suya.id) !== String(d.id)) paso(r, 'Otra pestaña creó la misma campaña al mismo tiempo: se sigue con la primera; la sobrante (…' + String(d.id).slice(-6) + ') quedó vacía y en pausa.');
    }
    r.ids.campana = String(suya.id);
    await guardar();
  }
  // 5. el conjunto, que arranca a las 5 a. m. Si la hora guardada ya paso (se reintento tarde), se toma la siguiente 5 a. m.
  if (!r.ids.conjunto) {
    const hay = await hijos(token, r.ids.campana + '/adsets');
    if (hay.error) return fallar('No pude revisar los conjuntos de la campaña (' + hay.error + '): no se crea otro a ciegas.');
    if (hay.lista.length) r.ids.conjunto = hay.lista[0].id;
    else {
      if (!(r.inicio && r.inicio.ms > Date.now() + 5 * 60000)) await correrInicio(token, r, null);
      const d = await fbPost('act_' + r.cuenta + '/adsets', token, C.paramsConjunto(r.plantilla, r.anuncio, r.ids.campana, r.inicio.iso, r.presupuesto));
      if (d.error || !C.ID_FB.test(String(d.id || ''))) return fallar('Facebook no creó el conjunto: ' + (d.error || 'sin id'));
      paso(r, 'Conjunto creado: arranca el ' + r.inicio.dia + ' a las 5 a. m.');
      r.ids.conjunto = await quedarseConElPrimero(token, r.ids.campana + '/adsets', String(d.id), r);
    }
    await guardar();
  }
  // 6. el anuncio
  if (!r.ids.anuncio) {
    const hay = await hijos(token, r.ids.conjunto + '/ads');
    if (hay.error) return fallar('No pude revisar los anuncios del conjunto (' + hay.error + '): no se crea otro a ciegas.');
    if (hay.lista.length) r.ids.anuncio = hay.lista[0].id;
    else {
      const d = await fbPost('act_' + r.cuenta + '/ads', token, C.paramsAnuncio('INT | ' + r.anuncio, r.ids.conjunto, r.ids.creativo));
      if (d.error || !C.ID_FB.test(String(d.id || ''))) return fallar('Facebook no creó el anuncio: ' + (d.error || 'sin id'));
      paso(r, 'Anuncio creado (Facebook lo revisa antes de mostrarlo).');
      r.ids.anuncio = await quedarseConElPrimero(token, r.ids.conjunto + '/ads', String(d.id), r);
    }
    await guardar();
  }
  // 7. dejarla LISTA EN PAUSA (decision del dueño, 6-nov: «se publican apagadas, nosotros las revisamos y las encendemos»). Campaña, conjunto y anuncio nacieron en pausa; aqui, si todo
  // cuadra, se activan el conjunto y el anuncio POR DENTRO y la campaña sigue en pausa: encenderla en Ads Manager es lo unico que falta. Si algo no cuadra, el conjunto y el anuncio quedan
  // (o vuelven a quedar) en pausa: encender esa campaña por error no gasta (revision del 6-nov).
  if (!(await sigueAprobado(env, r))) {
    await apagarPorDentro(token, r);
    r.estado = 'frenada'; r.nota = 'El anuncio ' + r.anuncio + ' ya no está aprobado en la Fábrica: la campaña quedó armada EN PAUSA, con el conjunto y el anuncio también en pausa. No la enciendan.';
    paso(r, r.nota); await guardar(); return { r };
  }
  // la campaña, el conjunto y el anuncio pudieron ADOPTARSE (otra ventana, o una igual creada a mano): se mira en Facebook que la nuestra siga siendo la MAS VIEJA con ese nombre,
  // a que telefono manda de verdad el conjunto y su hora de arranque real (revision de seguridad, 2.a ronda)
  const ult = await campanasConNombre(token, r);
  if (ult.error) return fallar('No pude confirmar en Facebook que esta sea la única campaña con este nombre (' + ult.error + '): queda en pausa. Pulsa «Seguir» en un momento.');
  if (ult.propias.length && ult.propias[0].id !== r.ids.campana) {
    await apagarPorDentro(token, r);
    return fallar('Hay otra campaña igual creada un instante antes (…' + String(ult.propias[0].id).slice(-6) + '): esta queda en pausa, con su conjunto y su anuncio también en pausa, para no gastar dos veces. No la enciendan.');
  }
  const cs = await fbGet(r.ids.conjunto, token, { fields: 'start_time,promoted_object' });
  if (cs.error) return fallar('No pude leer el conjunto en Facebook (' + cs.error + '): la campaña sigue en pausa.');
  if (r.destino && C.ultimas4(cs.promoted_object && cs.promoted_object.whatsapp_phone_number) !== r.destino) {
    await apagarPorDentro(token, r);
    return fallar('El conjunto manda a otro teléfono de WhatsApp que el elegido (…' + r.destino + '): todo queda en pausa. Revísalo en Ads Manager antes de encenderla.');
  }
  if (cs.start_time && Date.parse(cs.start_time) < Date.now() + 5 * 60000 && r.inicio && r.inicio.ms > Date.now() + 5 * 60000) r.inicio = { ms: Date.parse(cs.start_time), iso: cs.start_time, dia: r.inicio.dia };
  // si la hora de arranque ya paso (un «Seguir» tarde), se corre a la siguiente 5 a. m.: al encenderla no arranca de golpe a una hora sin nadie en los chats
  if (!(r.inicio && r.inicio.ms > Date.now() + 5 * 60000)) {
    const movida = await correrInicio(token, r, r.ids.conjunto);
    if (movida.error) return fallar('La hora de arranque ya había pasado y Facebook no dejó correrla a la siguiente 5 a. m. (' + movida.error + '): la campaña sigue en pausa.');
    await guardar();
  }
  // la campaña tiene que seguir EN PAUSA: si alguien la encendio mientras se armaba, el dash no activa nada por dentro (seria gastar sin la revision que pidio el dueño)
  const real = await fbGet(r.ids.campana, token, { fields: 'status,effective_status' });
  if (real.error) return fallar('No pude leer la campaña en Facebook (' + real.error + '): todo sigue en pausa. Pulsa «Seguir» en un momento.');
  if (real.status !== 'PAUSED') {
    await apagarPorDentro(token, r);
    r.estado = 'frenada'; r.nota = 'La campaña ya estaba ' + (real.status === 'ACTIVE' ? 'ENCENDIDA' : C.una(real.status, 20)) + ' en Facebook mientras se armaba: el dash no activó el conjunto ni el anuncio (siguen en pausa). Revísenla en Ads Manager.';
    paso(r, r.nota); await guardar(); return { r };
  }
  // activar por dentro (la campaña sigue en pausa: no gasta)
  for (const [id, que] of [[r.ids.conjunto, 'el conjunto'], [r.ids.anuncio, 'el anuncio']]) {
    const a = await fbPost(id, token, { status: 'ACTIVE' });
    if (a.error) { await apagarPorDentro(token, r); return fallar('Facebook no dejó activar ' + que + ' dentro de la campaña (' + a.error + '): todo sigue en pausa. Pulsa «Seguir».'); }
  }
  // el numero ya no frena: la campaña esta apagada y la encienden ustedes. Si hoy no esta conectado (o es otro telefono), queda escrito para que no la enciendan hasta arreglarlo.
  const est = await estadoNumero(env, r.numero, r.destino);
  const ojo = est.estado === 'conectado' ? ''
    : est.estado === 'caido' ? 'Ojo: el número ' + (r.numero.wpp ? 'WPP #' + r.numero.wpp + ' ' : '') + 'está desconectado en ZapData. No la enciendan hasta reconectarlo.'
      : est.estado === 'otro-telefono' ? 'Ojo: la conexión elegida en ZapData ya no es el teléfono …' + r.destino + ' al que manda el anuncio. Revisen el número en el paso 2 antes de encenderla.'
        : 'Ojo: ZapData no contestó si el número sigue conectado. Revísenlo antes de encenderla.';
  r.estado = 'lista'; r.nota = ojo; r.fin = new Date().toISOString();
  r.efectivo = C.una(real.effective_status, 30);
  paso(r, 'Lista EN PAUSA para revisar: al encender la campaña en Ads Manager arranca a las 5 a. m. del ' + r.inicio.dia + ' (si la encienden después, arranca en ese momento).');
  if (ojo) paso(r, ojo);
  await guardar();
  await lineaRegistroCompartido(env, r, true, ojo);
  await cerrarLote(env, r, rol);
  return { r };
}

// Deja en pausa el conjunto y el anuncio de una campaña que no quedo «lista»: asi encenderla por error en Ads Manager no gasta. Si Facebook no contesta, se sigue (ya nacieron en pausa).
async function apagarPorDentro(token, r) {
  for (const id of [r.ids.anuncio, r.ids.conjunto]) {
    if (!id) continue;
    try { await fbPost(id, token, { status: 'PAUSED' }); } catch { /* nacieron en pausa: solo importa si un intento anterior alcanzo a activarlos */ }
  }
}

// Cuando TODAS las campañas del lote quedaron listas: se marcan solas las tareas del paso 4 de la guia y queda una linea en «Qué ha pasado» de la oferta. Si alguna quedo con «Ojo» (el numero
// no estaba conectado o era otro telefono), «Botón de WhatsApp al número elegido» NO se marca y la linea lo dice (revision del 6-nov).
async function cerrarLote(env, r, rol) {
  try {
    const otros = await Promise.all((r.lote || []).filter((x) => x !== r.anuncio).map((x) => leerRegistro(env, r.proyecto, r.pais, x)));
    if (otros.some((x) => !x || x.estado !== 'lista')) return;
    const p = await leerProyecto(env, r.proyecto);
    if (!p) return;
    const ahora = new Date().toISOString();
    const conOjo = otros.concat([r]).filter((x) => x.nota).length;
    TAREAS_PASO_4.filter((t) => !(conOjo && t === 'boton')).forEach((t) => { F.marcarLanzamiento(p, r.paisNombre, t, true, rol, ahora); });
    const n = (r.lote || []).length;
    F.anotarHecho(p, rol, 'Armó ' + n + ' campaña' + (n === 1 ? '' : 's') + ' EN PAUSA en ' + r.paisNombre + ' (al encenderlas arrancan el ' + r.inicio.dia + ' a las 5 a. m., ' + C.textoPlata(r.presupuesto.unidades, r.moneda) + ' al día cada una)'
      + (conOjo ? '. ' + conOjo + ' con «Ojo»: revisen el número antes de encenderlas' : ''), ahora);
    await guardarProyecto(env, p);          // siempre: la linea de «Qué ha pasado» es nueva aunque las tareas ya estuvieran marcadas
  } catch { /* la guia es un extra: las campañas ya estan en Facebook y en su registro */ }
}

// ---------------------------------------------------------------- la puerta
export async function onRequest(context) {
  const { request, env } = context;
  const rol = await exigirSesion(request, env, ROLES);
  if (!rol) return json(403, { ok: false, error: 'Programar campañas en Facebook es de la trafficker o del dueño.' });
  if (!env.SUITE) return json(503, { ok: false, error: 'Falta el almacenamiento del sitio (KV).' });
  // interruptor sin desplegar: CAMPANAS_APAGADAS=1 en las variables de Cloudflare Pages frena crear (la vista previa sigue)
  const apagado = /^(1|si|true|on)$/i.test(String(env.CAMPANAS_APAGADAS || ''));
  const url = new URL(request.url);
  let b = {};
  if (request.method === 'POST') { try { b = await request.json(); } catch { return json(400, { ok: false, error: 'JSON inválido' }); } if (!b || typeof b !== 'object') b = {}; }
  else if (request.method !== 'GET') return json(405, { ok: false, error: 'Método no permitido' });
  const id = String((request.method === 'GET' ? url.searchParams.get('id') : b.id) || '');
  const pais = C.una(request.method === 'GET' ? url.searchParams.get('pais') : b.pais, 30);
  if (!C.ID_PROYECTO.test(id)) return json(400, { ok: false, error: 'Falta la oferta' });
  const p = await leerProyecto(env, id);
  if (!p) return json(404, { ok: false, error: 'Esa oferta no existe' });
  if (F.NOMBRES_RESERVADOS.includes(pais) || !(p.paises || []).includes(pais) || !C.clavePais(pais)) return json(400, { ok: false, error: 'Ese país no es de esta oferta' });
  const k = C.clavePais(pais);

  if (request.method === 'GET') {
    const { plan } = await armarPlan(env, p, pais, Date.now());
    return json(200, { ok: true, rol, plan });
  }

  const accion = String(b.accion || '');
  if (accion === 'comprobar') {
    // Facebook revisa la campaña y el conjunto tal como se crearian, SIN crear nada (validate_only). El conjunto se prueba dentro de la campaña modelo (tampoco se crea).
    const { plan, interno } = await armarPlan(env, p, pais, Date.now());
    if (!interno) return json(200, { ok: true, checks: [], plan });
    const token = interno.mod.token, cuenta = interno.mod.cuenta, solo = { execution_options: JSON.stringify(['validate_only']) };
    const nombre = interno.nuevas[0] ? C.nombreCampana(interno.num.wpp || '?', p.nombre, interno.ini.dia, interno.nuevas[0]) : 'PRUEBA';
    const checks = [];
    if (interno.pres) {
      const a = await fbPost('act_' + cuenta + '/campaigns', token, Object.assign(C.paramsCampana(interno.pl, nombre, interno.pres), solo));
      checks.push({ que: 'Campaña (objetivo y presupuesto)', ok: !a.error, msg: a.error || '' });
      // en PAUSA: si Facebook no respetara el «solo validar», no quedaria un conjunto encendido dentro de la campaña modelo
      const s = await fbPost('act_' + cuenta + '/adsets', token, Object.assign(C.paramsConjunto(interno.pl, 'PRUEBA', interno.pl.campana.id, interno.ini.iso, interno.pres), { status: 'PAUSED' }, solo));
      checks.push({ que: 'Conjunto (público, número de WhatsApp y hora de arranque)', ok: !s.error, msg: s.error || '' });
    }
    checks.push({ que: 'Página de Facebook del anuncio', ok: !!(plan.pagina && plan.pagina.puede), msg: plan.pagina && plan.pagina.puede ? '' : 'El usuario del sistema del dash no puede anunciar con esa página.' });
    return json(200, { ok: true, checks });
  }

  if (accion === 'empezar') {
    if (apagado) return json(503, { ok: false, error: 'Programar campañas está apagado por ahora (CAMPANAS_APAGADAS en Cloudflare). Lo creado sigue en Facebook.' });
    const ahora = Date.now();
    const { plan, interno } = await armarPlan(env, p, pais, ahora);
    if (plan.bloqueos.length || !interno) return json(409, { ok: false, error: plan.bloqueos[0] || 'No se puede programar todavía.', plan });
    if (String(b.huella || '') !== plan.huella) return json(409, { ok: false, error: 'Algo cambió desde que abriste la vista previa (cuenta, presupuesto, hora o anuncios): revísala de nuevo antes de programar.', plan });
    if (!interno.nuevas.length) return json(409, { ok: false, error: 'No hay campañas nuevas para programar.', plan });
    const clave = plan.clave || C.una(b.clave, 30);
    if (!clave) return json(400, { ok: false, error: 'Falta la palabra clave del flujo de venta: sin ella el mensaje de WhatsApp no arranca el flujo.', plan });
    const lt = C.limpiarTextos(b.textos, clave);
    if (!lt.ok) return json(400, { ok: false, error: lt.errores.join(' '), plan });
    const t = new Date(ahora).toISOString();
    const hechos = [];
    for (const anuncio of interno.nuevas) {
      const a = interno.aprobados.filter((x) => x.id === anuncio)[0];
      // un registro que ya existe (otra pestaña pulso a la vez) no se pisa: se sigue con el suyo
      const previo = await leerRegistro(env, p.id, k, anuncio);
      if (previo && !(previo.estado === 'error' && !(previo.ids && previo.ids.campana))) { hechos.push(C.resumenRegistro(previo)); continue; }
      const r = {
        v: 1, proyecto: p.id, pais: k, paisNombre: pais, anuncio, estado: 'pendiente', t, por: rol, lote: interno.nuevas.slice(), oferta: C.una(p.nombre, 120),
        nombre: C.nombreCampana(interno.num.wpp, p.nombre, interno.ini.dia, anuncio), cuenta: interno.mod.cuenta, moneda: interno.moneda, offset: interno.off,
        inicio: { ms: interno.ini.ms, iso: interno.ini.iso, dia: interno.ini.dia }, presupuesto: interno.pres, plantilla: interno.pl, drive: C.idDeDrive(a.entrega && a.entrega.drive),
        textos: lt.textos, numero: { id: interno.num.id, wpp: interno.num.wpp || '', tel4: interno.num.tel4 || '' }, destino: interno.destino, ids: {}, pasos: [],
      };
      paso(r, 'La programó ' + (rol === 'owner' ? 'el dueño' : 'la trafficker') + ' desde la Fábrica.');
      try { await guardarRegistro(env, r); } catch { return json(503, { ok: false, error: 'No pude guardar el registro de la campaña (¿se acabó el cupo de escrituras del día?): no se creó nada en Facebook.', campanas: hechos }); }
      hechos.push(C.resumenRegistro(r));
    }
    return json(200, { ok: true, campanas: hechos });
  }

  if (accion === 'avanzar') {
    if (apagado) return json(503, { ok: false, error: 'Programar campañas está apagado por ahora (CAMPANAS_APAGADAS en Cloudflare). Lo creado sigue en Facebook.' });
    const anuncio = String(b.anuncio || '');
    if (!C.ID_ANUNCIO.test(anuncio)) return json(400, { ok: false, error: 'Falta el anuncio' });
    const r = await leerRegistro(env, p.id, k, anuncio);
    if (!r) return json(404, { ok: false, error: 'Esa campaña no está en la cola: abre la vista previa y pulsa «Programar».' });
    if (r.estado === 'lista') return json(200, { ok: true, campana: C.resumenRegistro(r) });
    r.ids = r.ids && typeof r.ids === 'object' ? r.ids : {};
    // candado de 60 s: si otra ventana esta armando ESTA campaña, esta espera (KV no es atomico: el candado achica la carrera y lo que quede lo resuelve avanzarUna, que se queda con la primera en Facebook)
    const ventana = /^[a-z0-9]{6,24}$/.test(String(b.ventana || '')) ? String(b.ventana) : 'sin-ventana';
    const ahoraB = Date.now();
    if (r.bloqueo && r.bloqueo.id !== ventana && ahoraB - Number(r.bloqueo.t || 0) < 60000) return json(200, { ok: true, campana: C.resumenRegistro(r), esperar: true, ocupada: true });
    // el candado se escribe solo si no es propio o tiene mas de 30 s: mientras Facebook procesa el video la pantalla pregunta cada 5 s, y escribir en cada pregunta gastaba cientos de escrituras de KV
    // (1.000 al dia para toda la suite; revision de seguridad, 2.a ronda)
    if (!(r.bloqueo && r.bloqueo.id === ventana && ahoraB - Number(r.bloqueo.t || 0) < 30000)) {
      r.bloqueo = { id: ventana, t: ahoraB };
      try { await guardarRegistro(env, r); } catch { return json(503, { ok: false, error: 'No pude guardar el registro de la campaña: no se sigue a ciegas.' }); }
    }
    let res;
    try { res = await avanzarUna(env, r, rol); }
    catch (e) { return json(502, { ok: false, error: 'Se cortó a mitad de camino: ' + C.una(sinLlave((e && e.message) || e), 200) + '. Pulsa «Seguir»: lo creado no se repite.', campana: C.resumenRegistro(r) }); }
    return json(200, { ok: true, campana: C.resumenRegistro(res.r), esperar: !!res.esperar });
  }

  return json(400, { ok: false, error: 'Acción desconocida' });
}
