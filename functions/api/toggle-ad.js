// Cloudflare Pages Function: pause / resume a Facebook ad, ad set or campaign, and
// change a budget. Route: /api/toggle-ad (functions/api/toggle-ad.js maps to that URL).
//
// Security model:
//  - ADMIN_PASSWORD and FACEBOOK_TOKENS live ONLY as Cloudflare Pages project env vars,
//    read here via context.env -- never shipped to the browser.
//  - The caller sends the password on every request; it's checked server-side.
//  - The right System User token is chosen by matching accountId against the accounts
//    list in FACEBOOK_TOKENS, so the browser never sees any token.
//
// Request (POST JSON):
//   { action:'verify', password }
//   { action:'pause'|'resume', id, type:'ad'|'adset'|'campaign', accountId, password }
//   { action:'budget', id, type:'adset'|'campaign', budgetKind:'daily'|'lifetime',
//     amount:<number in account currency>, accountId, password }
//   { action:'duplicate', id, type:'adset'|'campaign', accountId, password }
//     -> copia completa (con sus anuncios) que SIEMPRE nace PAUSADA: duplicar nunca gasta solo.
// Response: { ok:true, ... } | { ok:false, error }
//
// Que Facebook ACEPTE un cambio no es que el cambio haya quedado: encender una campaña en una cuenta inhabilitada, un anuncio en revision o un conjunto dentro
// de una campaña pausada devuelve ok y sigue sin correr. Por eso, despues de aplicar, se LEE DE VUELTA el objeto (status, effective_status y, si fue un
// presupuesto, daily_budget / lifetime_budget) y la respuesta lleva lo real: { status, efectivo, presupuesto:{daily,lifetime}, aviso }. "aviso" viene en español
// cuando lo leido no es lo pedido; la pagina lo muestra y pinta el estado REAL, no el que se esperaba.
//
// Cada intento de cambio -lo acepte o lo rechace Facebook- queda ademas en el REGISTRO compartido (clave reg:..., 90 dias; lo lista /api/reglas-registro), con la regla que lo pidio si
// el dash la manda en `meta` ({ regla, nombre, pais, razon, cifras, tipo, antes, despues, clave, ms, via }). Asi lo aprobado desde el telefono se ve en el computador y al reves.
// `via: 'auto'` = lo pidio el ejecutor automatico de las reglas (functions/api/reglas-ejecutar.js), no una persona.
//
// Condiciones previas (`previa`, solo las manda ese ejecutor): antes de tocar nada se LEE el objeto en Facebook y solo se aplica si sigue como lo vio la regla -su campaña, su estado
// y, en un presupuesto, el monto-: { campana: <id de la campaña a la que debe pertenecer>, presupuesto: <monto que la regla veia, en la moneda de la cuenta> }. Si ya no es asi se
// responde { ok: true, omitido: true, motivo } SIN escribir en Facebook ni en el registro: pausar lo que ya esta pausado, o subir un presupuesto que alguien ya cambio, no se repite.
// Lo que sube gasto (encender, presupuesto) no se hace a ciegas: si no se pudo leer el objeto, no se aplica.
//
// Y se GUARDA en KV (cambio:<tipo>:<id>, 3 dias) para que /api/cambios se lo devuelva al dash: el snapshot es de la ultima hora y, sin esto, al recargar el
// presupuesto o el estado volvian a ser los viejos aunque Facebook ya tuviera los nuevos.

// v21.0 se retira el 21 de enero de 2027; las versiones de Marketing API previas a v24.0
// ya estan obsoletas. Se mueve junto con Fetch-FacebookGraph.ps1 y Pausar-Cuenta.ps1:
// las tres hablan con la misma cuenta y quedarse desparejas solo esconde el problema.
import { safeEqual } from '../_sesion.js';
const GRAPH = 'https://graph.facebook.com/v25.0';

// Currencies Facebook reports and accepts without a minor unit. Sending 24000*100 for a
// budget as 240 before, except here it would spend real money.
const CERO_DECIMALES = ['COP', 'CLP', 'ISK', 'JPY', 'KRW', 'PYG', 'VND', 'IDR', 'HUF', 'TWD'];

async function monedaDeCuenta(accountId, token) {
  try {
    const r = await fetch(`${GRAPH}/act_${accountId}?fields=currency&access_token=${token}`);
    const d = await r.json();
    return (d && d.currency) || 'USD';
  } catch { return 'USD'; }
}

// Lo que Facebook dice del objeto AHORA. null si no se pudo leer (el cambio igual ya se aplico). `extra` = otros campos a leer en la misma llamada (p. ej. campaign_id).
async function leerEstado(id, type, token, conPresupuesto, extra) {
  const campos = ['status', 'effective_status'].concat(extra || []);
  if (conPresupuesto && type !== 'ad') campos.push('daily_budget', 'lifetime_budget');
  try {
    const r = await fetch(`${GRAPH}/${id}?fields=${campos.join(',')}&access_token=${token}`);
    const d = await r.json();
    return (r.ok && d && !d.error) ? d : null;
  } catch { return null; }
}
const espera = (ms) => new Promise((r) => setTimeout(r, ms));

const EXPLICA = {
  PAUSED: 'sigue en pausa', CAMPAIGN_PAUSED: 'su campaña sigue pausada: enciéndela también', ADSET_PAUSED: 'su conjunto sigue pausado: enciéndelo también',
  DISAPPROVED: 'fue rechazado en la revisión de políticas de anuncios', PENDING_REVIEW: 'está en revisión y no corre hasta que lo aprueben',
  IN_PROCESS: 'todavía lo está procesando', WITH_ISSUES: 'tiene problemas', PENDING_BILLING_INFO: 'falta resolver el pago de la cuenta',
  ARCHIVED: 'está archivado', DELETED: 'está eliminado', PREAPPROVED: 'está pre-aprobado y arranca en su fecha de inicio',
};
const CUENTA = { 2: 'La cuenta publicitaria está INHABILITADA', 3: 'La cuenta tiene un pago pendiente (no gasta hasta resolverlo)', 7: 'La cuenta está en revisión de riesgo',
  8: 'La cuenta está pendiente de liquidación de pago', 9: 'La cuenta está en periodo de gracia de pago', 100: 'La cuenta está en cierre', 101: 'La cuenta está cerrada' };
const NIVEL = { ad: 'El anuncio', adset: 'El conjunto', campaign: 'La campaña' };
const ENCENDIDO = { ad: 'encendido', adset: 'encendido', campaign: 'encendida' };

async function motivoDeLaCuenta(accountId, token) {
  try {
    const r = await fetch(`${GRAPH}/act_${String(accountId).replace(/^act_/, '')}?fields=account_status&access_token=${token}`);
    const d = await r.json();
    return CUENTA[d && d.account_status] || '';
  } catch { return ''; }
}
async function resumenDeProblemas(id, token) {
  try {
    const r = await fetch(`${GRAPH}/${id}?fields=issues_info&access_token=${token}`);
    const d = await r.json();
    const i = d && d.issues_info && d.issues_info[0];
    return i ? String(i.error_summary || i.error_message || '').slice(0, 200) : '';
  } catch { return ''; }
}

// Guarda el cambio verificado. Nunca rompe la accion: si KV falla (o se acabo el tope diario de escrituras) el cambio igual ya esta en Facebook.
async function guardarCambio(env, reg) {
  if (!env.SUITE) return;
  const clave = 'cambio:' + reg.type + ':' + reg.id;
  try {
    let previo = null;
    try { previo = await env.SUITE.get(clave, { type: 'json' }); } catch { previo = null; }
    await env.SUITE.put(clave, JSON.stringify(Object.assign({}, previo || {}, reg)), { expirationTtl: 3 * 86400 });
  } catch { /* sin KV: el dash vuelve a mostrar lo del snapshot hasta el proximo refresco */ }
}

// ---- registro compartido de lo aplicado ----
const TIPO_DE = { pause: 'pausar', resume: 'reactivar', budget: 'fijar', duplicate: 'duplicar' };
const TIPOS_REGLA = ['pausar', 'reactivar', 'subir', 'bajar', 'fijar', 'duplicar'];
const texto = (v, n) => String(v == null ? '' : v).replace(/\s+/g, ' ').trim().slice(0, n);
const numeroOnull = (v) => { const x = Number(v); return v != null && v !== '' && isFinite(x) ? Math.round(x * 100) / 100 : null; };
// Lo que manda el dash sobre quien pide el cambio. Nada de esto se confia: se limpia y se recorta (la ficha cabe en 1 KB de metadata).
function limpiarMeta(m) {
  const o = (m && typeof m === 'object') ? m : {};
  const ms = Number(o.ms);
  return {
    tipo: TIPOS_REGLA.includes(o.tipo) ? o.tipo : '', regla: texto(o.regla, 80), nombre: texto(o.nombre, 100), pais: /^[a-z]{3,20}$/.test(String(o.pais || '')) ? String(o.pais) : '',
    razon: texto(o.razon, 200), cifras: texto(o.cifras, 140), antes: numeroOnull(o.antes), despues: numeroOnull(o.despues), clave: texto(o.clave, 90),
    ms: o.ms === -1 || ms < 0 ? -1 : (isFinite(ms) ? Math.min(ms, 30 * 86400000) : 0),
    via: o.via === 'auto' ? 'auto' : '',
  };
}
// Una clave por intento, con la ficha en la METADATA: asi /api/reglas-registro las lista todas de una vez, sin leer una por una. La clave lleva el tiempo al reves
// (las mas nuevas salen primero). Nunca rompe la accion: sin KV, o sin cupo de escrituras del dia, el cambio ya esta en Facebook y solo falta la linea del registro.
// Devuelve true si la linea quedo guardada. Importa: el registro es la MEMORIA de lo ya aplicado (de ahi sale que una subida de presupuesto no se repita), asi que el ejecutor automatico
// deja de aplicar lo que sube gasto cuando no se pudo guardar.
async function registrar(env, c, meta, r) {
  if (!env.SUITE) return false;
  try {
    const t = Date.now();
    const e = {
      t, tipo: meta.tipo || TIPO_DE[c.accion] || '', nivel: c.nivel, id: String(c.id), cuenta: String(c.cuenta || ''), ok: !!r.ok, regla: meta.regla || 'A mano', nombre: meta.nombre, pais: meta.pais,
      razon: meta.razon, cifras: meta.cifras, antes: meta.antes, despues: r.despues != null ? r.despues : (meta.despues != null ? meta.despues : (c.accion === 'budget' ? numeroOnull(c.monto) : null)),
      msg: texto(r.msg, 160), efectivo: r.efectivo || '', copia: r.copia || '', clave: meta.clave, ms: meta.ms, via: meta.via,
    };
    Object.keys(e).forEach((k) => { if (e[k] === '' || e[k] === null || e[k] === undefined) delete e[k]; });
    const bytes = (x) => new TextEncoder().encode(JSON.stringify(x)).length;
    if (bytes(e) > 1000) delete e.cifras;
    if (bytes(e) > 1000) delete e.razon;
    if (bytes(e) > 1000) e.msg = texto(e.msg, 60);
    if (bytes(e) > 1000) delete e.nombre;
    const clave = 'reg:' + String(9999999999999 - t).padStart(13, '0') + '-' + Math.random().toString(36).slice(2, 7);
    await env.SUITE.put(clave, '1', { metadata: e, expirationTtl: 90 * 86400 });
    return true;
  } catch { return false; /* el registro es un extra: el cambio ya esta en Facebook */ }
}

// ---- condiciones previas del ejecutor automatico (ver la cabecera) ----
const omitido = (motivo) => ({ status: 200, cuerpo: { ok: true, omitido: true, motivo } });
// Devuelve null si se puede seguir, o { status, cuerpo } con la razon por la que no se toca nada. `factor` = unidades menores por unidad de moneda de la cuenta (100, o 1 en COP, CLP...).
async function verificarPrevia(previa, c, token, factor) {
  const pideCampana = previa.campana != null && previa.campana !== '' && c.type !== 'campaign';
  const pidePresupuesto = c.action === 'budget' && previa.presupuesto != null;
  const real = await leerEstado(c.id, c.type, token, pidePresupuesto, pideCampana ? ['campaign_id'] : []);
  if (!real) {
    // Pausar es idempotente y va hacia el lado seguro: si no se puede leer, se pausa igual. Encender o tocar un presupuesto sin ver como esta no.
    if (c.action === 'pause') return null;
    return { status: 502, cuerpo: { ok: false, error: 'No pude leer el estado actual en Facebook, y no se enciende ni se cambia un presupuesto a ciegas.' } };
  }
  if (previa.campana != null && previa.campana !== '') {
    const suya = c.type === 'campaign' ? String(c.id) : String(real.campaign_id || '');
    if (suya !== String(previa.campana)) return { status: 409, cuerpo: { ok: false, error: 'Ese objeto no pertenece a la campaña de la regla (Facebook dice que es de ' + (suya || 'ninguna') + ').' } };
  }
  if (c.action === 'pause' && real.status === 'PAUSED') return omitido('ya estaba pausado en Facebook');
  if (c.action === 'resume' && real.status === 'ACTIVE') return omitido('ya estaba encendido en Facebook');
  if (pidePresupuesto) {
    const vivo = real.daily_budget != null ? Number(real.daily_budget) / factor : null, esperado = Number(previa.presupuesto);
    const f = (n) => Number(n).toLocaleString('es-CO', { maximumFractionDigits: 2 });
    if (vivo == null || !isFinite(vivo) || vivo <= 0) return omitido('Facebook no le ve un presupuesto diario a este objeto');
    if (c.budgetKind === 'daily' && Math.abs(vivo - c.monto) <= Math.max(0.5, c.monto * 0.005)) return omitido('ya tenía ese presupuesto (' + f(vivo) + ')');
    if (isFinite(esperado) && Math.abs(vivo - esperado) > Math.max(1, esperado * 0.03)) return omitido('el presupuesto cambió: Facebook dice ' + f(vivo) + ' y la regla veía ' + f(esperado));
  }
  return null;
}

export async function onRequestPost(context) {
  const { request, env } = context;
  const json = (status, obj) => new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json' }
  });

  let p;
  try { p = await request.json(); }
  catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const { id, type, action, password, budgetKind, amount } = p;
  let accountId = p.accountId;

  // 1. Auth -- one wrong password and nothing else runs.
  if (!password || typeof env.ADMIN_PASSWORD !== 'string' || env.ADMIN_PASSWORD.length < 14 || !safeEqual(String(password), env.ADMIN_PASSWORD)) {
    await new Promise((r) => setTimeout(r, 1000));   // plantilla: cada intento fallido tarda un segundo
    return json(401, { ok: false, error: 'Contrasena incorrecta' });
  }
  // 1b. The modal calls with action='verify' just to validate the password once.
  if (action === 'verify') return json(200, { ok: true });

  // 2. Validate inputs.
  if (!id) return json(400, { ok: false, error: 'Falta el id' });
  // El id y la cuenta son numeros de Facebook: se exige esa forma antes de armarles una URL del Graph (un id con «/» o «?» apuntaria a otro objeto) y antes de anotarlos en el registro.
  if (!/^\d{5,20}$/.test(String(id))) return json(400, { ok: false, error: 'id invalido' });
  if (accountId && !/^(act_)?\d{5,20}$/.test(String(accountId))) return json(400, { ok: false, error: 'accountId invalido' });
  if (accountId) accountId = String(accountId).replace(/^act_/, '');           // todo lo de abajo arma «act_» + la cuenta: se guarda sin prefijo para que no salga «act_act_…»
  if (!['ad', 'adset', 'campaign'].includes(type)) {
    return json(400, { ok: false, error: 'type invalido' });
  }
  if (!['pause', 'resume', 'budget', 'duplicate'].includes(action)) {
    return json(400, { ok: false, error: 'action invalida' });
  }

  // Un anuncio suelto no se duplica: la copia completa es la del conjunto o la campaña.
  if (action === 'duplicate' && type === 'ad') {
    return json(400, { ok: false, error: 'Duplica el conjunto o la campaña, no el anuncio suelto' });
  }

  let monto = null;
  if (action === 'budget') {
    // An ad cannot hold a budget - only the ad set or the campaign can.
    if (type === 'ad') return json(400, { ok: false, error: 'Un anuncio no tiene presupuesto' });
    if (!['daily', 'lifetime'].includes(budgetKind)) {
      return json(400, { ok: false, error: 'budgetKind invalido' });
    }
    monto = Number(amount);
    if (!isFinite(monto) || monto <= 0) {
      return json(400, { ok: false, error: 'Monto invalido' });
    }
    if (monto > 100000) {
      return json(400, { ok: false, error: 'Monto sospechosamente alto (>100.000); revisalo' });
    }
  }

  // Quien pide el cambio (una regla, o una persona a mano) lo dice en `meta`; cada intento queda en el registro.
  const meta = limpiarMeta(p.meta);
  const anotar = (r) => registrar(env, { accion: action, nivel: type, id, cuenta: accountId, monto }, meta, r);

  // 3. Pick the token that owns this account.
  let tokens;
  try { tokens = JSON.parse(env.FACEBOOK_TOKENS || '{}'); }
  catch { return json(500, { ok: false, error: 'Config de tokens invalida' }); }

  // A country can hold several token blocks - one per Business Manager, since a token
  // only ever sees its own BM's accounts. Old single-token blocks still work.
  //
  // La lista `accounts` es un PISO, no un techo — igual que en Fetch-FacebookGraph.ps1.
  // Un bloque con la lista vacia significa "lo que este token alcance", que es como los
  // escribe Agregar-Token.ps1, y asi entran solas las cuentas que se asignan en Business
  // Manager sin tocar ningun archivo.
  //
  // Buscar SOLO en la lista explicita rompia justo eso: el tablero mostraba el interruptor
  // encendido -el fetcher si descubre esas cuentas y comprueba que puede escribir en
  // ellas- y al pulsarlo salia "Sin token para la cuenta". Le paso a las cinco cuentas del
  // Business Manager nuevo de Mexico, entre ellas la que esta
  // gastando hoy: se podia apagar antes de cambiar de cuenta y dejo de poderse despues.
  const bloquesTodos = [];
  for (const key of Object.keys(tokens)) {
    const c = tokens[key];
    if (!c) continue;
    for (const b of (Array.isArray(c.tokens) ? c.tokens : [c])) {
      if (b && b.systemUserToken) bloquesTodos.push(b);
    }
  }
  let token = null;
  for (const b of bloquesTodos) {
    if (Array.isArray(b.accounts) && b.accounts.map(String).includes(String(accountId))) {
      token = b.systemUserToken;
      break;
    }
  }
  // Camino lento, solo si la lista explicita no la tenia: se le pregunta a cada token que
  // cuentas alcanza. Cuesta un par de llamadas y ocurre una vez por clic, no por render.
  // Las dos aristas porque no son la misma: /me/adaccounts devuelve vacio para varios de
  // estos tokens aunque /me/assigned_ad_accounts si las liste.
  if (!token) {
    for (const b of bloquesTodos) {
      let visto = false;
      for (const edge of ['me/adaccounts', 'me/assigned_ad_accounts']) {
        if (visto) break;
        try {
          const r = await fetch(`${GRAPH}/${edge}?fields=account_id&limit=200&access_token=${b.systemUserToken}`);
          const d = await r.json();
          for (const a of ((d && d.data) || [])) {
            if (String(a.account_id).replace(/^act_/, '') === String(accountId)) { visto = true; break; }
          }
        } catch { /* un token caido no debe impedir probar los demas */ }
      }
      if (visto) { token = b.systemUserToken; break; }
    }
  }
  if (!token) {
    // Every country has a Graph token now, so reaching here means this specific ad
    // account sits in a Business Manager whose token was never added - each System
    // User token only sees the accounts of its own BM.
    await anotar({ ok: false, msg: 'Sin token para la cuenta ' + accountId });
    return json(400, {
      ok: false,
      error: `Sin token para la cuenta ${accountId}. Ningun System User de los configurados la alcanza: esa cuenta esta en otro Business Manager, o el usuario del sistema de ese BM todavia no la tiene asignada.`,
    });
  }

  // 3a. Condiciones previas (solo las manda el ejecutor automatico de las reglas, ver la cabecera): el objeto se lee en Facebook AHORA y solo se toca si sigue como lo vio la regla.
  // La moneda de la cuenta se pide aqui, una sola vez, porque el presupuesto que Facebook devuelve viene en unidades menores y hay que ponerlo en la moneda de la cuenta para compararlo.
  let factor = 100;
  if (action === 'budget') {
    const moneda = await monedaDeCuenta(accountId, token);
    factor = CERO_DECIMALES.includes(moneda) ? 1 : 100;
  }
  const previa = p.previa && typeof p.previa === 'object' ? p.previa : null;
  if (previa) {
    const v = await verificarPrevia(previa, { id, type, action, budgetKind, monto }, token, factor);
    if (v) return json(v.status, v.cuerpo);
  }

  // 3b. Duplicar: Facebook copia el objeto con todo lo de adentro (deep_copy) y la copia nace
  // PAUSADA (status_option). Asi duplicar no gasta ni un centavo hasta que alguien la active.
  if (action === 'duplicate') {
    const hoy = new Date(Date.now() - 5 * 3600 * 1000).toISOString().slice(5, 10);
    const dup = new URLSearchParams({
      access_token: token, deep_copy: 'true', status_option: 'PAUSED',
      rename_options: JSON.stringify({ rename_suffix: ' - copia ' + hoy }),
    });
    try {
      const resp = await fetch(`${GRAPH}/${id}/copies`, { method: 'POST', body: dup });
      const data = await resp.json();
      if (!resp.ok || (data && data.error)) {
        const e = (data && data.error) || {};
        const msg = e.error_user_msg ? ((e.error_user_title ? e.error_user_title + ': ' : '') + e.error_user_msg)
                                      : (e.message || ('HTTP ' + resp.status));
        await anotar({ ok: false, msg });
        return json(502, { ok: false, error: msg });
      }
      const copia = data.copied_adset_id || data.copied_campaign_id || '';
      const registrado = await anotar({ ok: true, copia });
      return json(200, { ok: true, id, type, action, copia, registrado });
    } catch (e) {
      await anotar({ ok: false, msg: 'No se pudo duplicar: ' + ((e && e.message) || e) });
      return json(502, { ok: false, error: 'No se pudo duplicar: ' + ((e && e.message) || e) });
    }
  }

  // 4. Apply the change. Ads, ad sets and campaigns all take `status`; budgets go in
  // minor units (cents), which is how Facebook stores and returns them.
  const params = new URLSearchParams({ access_token: token });
  if (action === 'budget') {
    params.set(budgetKind === 'daily' ? 'daily_budget' : 'lifetime_budget',
               String(Math.round(monto * factor)));
  } else {
    params.set('status', action === 'pause' ? 'PAUSED' : 'ACTIVE');
  }

  try {
    const resp = await fetch(`${GRAPH}/${id}`, { method: 'POST', body: params });
    const data = await resp.json();
    if (!resp.ok || (data && data.error)) {
      // Facebook manda dos mensajes: uno tecnico ("Invalid parameter") y otro escrito para
      // la persona ("Se requiere un numero de WhatsApp. Vuelve a conectar tu numero..."),
      // que es el que dice que hacer. Se prefiere el segundo: con el primero, pausar un
      // anuncio de clic-a-WhatsApp con el numero desconectado se veia como un fallo del
      // tablero y no como lo que era, algo que arreglar en la pagina de Facebook.
      const e = (data && data.error) || {};
      let msg = e.error_user_msg
        ? ((e.error_user_title ? e.error_user_title + ': ' : '') + e.error_user_msg)
        : (e.message || ('HTTP ' + resp.status));
      // Escribir el estado de un ANUNCIO hace que Facebook vuelva a validar su creativo, y
      // en los de clic-a-WhatsApp esa validacion falla si el token no ve la pagina: sale
      // "Se requiere un numero de WhatsApp" aunque el numero este conectado y el anuncio
      // corriendo. El conjunto y la campana no revalidan el creativo, asi que por ahi si
      // se apaga. Se dice aqui porque el usuario esta mirando el boton que acaba de fallar.
      if (type === 'ad' && (e.error_subcode === 2446880 || /WhatsApp/i.test(msg))) {
        msg += ' — Este anuncio no se puede cambiar desde aquí, pero su conjunto o su campaña sí: usa el botón de la fila de arriba.';
      }
      await anotar({ ok: false, msg });
      return json(502, { ok: false, error: msg });
    }
    // 5. Leer de vuelta como quedo. Facebook tarda un instante en reflejar el estado efectivo: una segunda lectura si la primera no coincide.
    const esperado = action === 'pause' ? 'PAUSED' : 'ACTIVE';
    let real = null;
    for (let intento = 0; intento < 2; intento++) {
      await espera(intento === 0 ? 700 : 1500);
      real = await leerEstado(id, type, token, action === 'budget');
      if (!real) break;
      if (action === 'budget') break;
      if (real.effective_status === esperado) break;
    }
    const ahora = new Date().toISOString();
    const base = { id, type, accountId: String(accountId || ''), at: ahora };

    if (action === 'budget') {
      const dailyReal = real && real.daily_budget != null ? Number(real.daily_budget) / factor : null;
      const lifeReal = real && real.lifetime_budget != null ? Number(real.lifetime_budget) / factor : null;
      const presupuesto = real ? { daily: dailyReal, lifetime: lifeReal } : null;
      const pedido = budgetKind === 'daily' ? dailyReal : lifeReal;
      let aviso;
      if (real && pedido != null && Math.abs(pedido - monto) > Math.max(0.5, monto * 0.005)) {
        aviso = 'Facebook dejó el presupuesto ' + (budgetKind === 'daily' ? 'diario' : 'total') + ' en ' + pedido.toLocaleString('es-CO', { maximumFractionDigits: 2 }) + ', no en ' + monto.toLocaleString('es-CO', { maximumFractionDigits: 2 }) + ' (redondea o tiene un mínimo para esta cuenta).';
      }
      await guardarCambio(env, Object.assign(base, budgetKind === 'daily' ? { daily: pedido != null ? pedido : monto } : { lifetime: pedido != null ? pedido : monto }));
      const registrado = await anotar({ ok: true, msg: aviso || '', despues: pedido != null ? pedido : monto });
      return json(200, { ok: true, id, type, budgetKind, amount: monto, presupuesto, verificado: !!real, aviso, registrado });
    }

    const efectivo = real ? real.effective_status : '';
    let aviso;
    if (real && action === 'resume' && efectivo !== 'ACTIVE') {
      const quien = NIVEL[type] || 'El objeto';
      aviso = quien + ' quedó ' + (ENCENDIDO[type] || 'encendido') + ' en la configuración, pero Facebook dice que ' + (EXPLICA[efectivo] || ('su estado real es ' + efectivo)) + '.';
      if (efectivo !== 'CAMPAIGN_PAUSED' && efectivo !== 'ADSET_PAUSED') {
        const [cuenta, problema] = await Promise.all([motivoDeLaCuenta(accountId, token), resumenDeProblemas(id, token)]);
        if (cuenta) aviso += ' ' + cuenta + '.';
        if (problema) aviso += ' Detalle de Facebook: ' + problema;
      }
    } else if (real && action === 'pause' && efectivo === 'ACTIVE') {
      aviso = (NIVEL[type] || 'El objeto') + ' sigue activo en Facebook (puede tardar unos segundos en reflejar la pausa). Revísalo en Ads Manager.';
    }
    await guardarCambio(env, Object.assign(base, { status: real ? real.status : esperado, efectivo: efectivo || esperado }));
    const registrado = await anotar({ ok: true, msg: aviso || '', efectivo: efectivo || esperado });
    return json(200, { ok: true, id, type, status: real ? real.status : esperado, efectivo, verificado: !!real, aviso, registrado });
  } catch (e) {
    await anotar({ ok: false, msg: 'No se pudo contactar a Facebook' });
    return json(502, { ok: false, error: 'No se pudo contactar a Facebook: ' + (e && e.message) });
  }
}
