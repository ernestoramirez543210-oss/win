// Programar en Facebook las campañas de una oferta nueva (paso 4 de la guia de lanzamiento de /fabrica): reglas puras, sin red. El endpoint es functions/api/fabrica-campanas.js.
//
// Decidido por el dueño (2026-11-05): un BOTON con vista previa que pulsa la trafficker (o el dueño); las campañas nacen PROGRAMADAS a las 5 a. m. (hora de la cuenta) del dia siguiente; el presupuesto es
// el de hoy del pais (el de la ultima campaña de ese numero) con un tope de 6 campañas por pais y lanzamiento; la cuenta es la del numero elegido en la guia: se busca la ultima campaña de ese numero de
// WhatsApp y se copia su cuenta, pagina, Instagram, publico y objetivo. Solo cambian el video, el texto, el nombre, el presupuesto y la hora.
//
// Una campaña = campaña (presupuesto en la campaña, como las monta la trafficker) + un conjunto + un anuncio con el video aprobado. Todo nace con la CAMPAÑA EN PAUSA y solo al final, cuando el anuncio
// quedo armado y el numero de WhatsApp sigue conectado, se enciende la campaña: como el conjunto arranca a las 5 a. m., queda «programada». Si algo falla a mitad de camino, lo creado se queda en pausa
// (no gasta) y el registro dice que falto.

export const GRAPH = 'https://graph.facebook.com/v25.0';
export const PREFIJO = 'fcamp:';                 // fcamp:<proyecto>:<pais>:<anuncio>  (un registro por campaña: estado, ids en Facebook, textos, quien y cuando)
export const TOPE_POR_PAIS = 6;                  // campañas por pais y por lanzamiento (orden del dueño)
export const HORA_INICIO = 5;                    // 5 a. m. en la zona horaria de la cuenta publicitaria
export const MARGEN_MIN = 30;                    // si faltan menos de 30 min para las 5 a. m., se programa para el dia siguiente
export const DIAS_MODELO = 45;                   // se busca la campaña modelo entre los conjuntos creados en los ultimos 45 dias
export const VIDEO_MAX_MIN = 20;                 // si Facebook no termina de procesar el video en 20 min, se da por fallido
// Tope de presupuesto diario POR CAMPAÑA, en las unidades de Facebook (COP sin decimales; USD y PEN en centavos). Es un freno contra un modelo raro, no el presupuesto: el presupuesto es el del modelo.
export const TOPE_PRESUPUESTO = { COP: 60000, USD: 2000, PEN: 7000 };
export const SIN_DECIMALES = ['COP', 'CLP', 'ISK', 'JPY', 'KRW', 'PYG', 'VND', 'IDR', 'HUF', 'TWD'];
export const MESES = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC'];
export const ESTADOS = ['pendiente', 'video', 'armando', 'lista', 'frenada', 'error'];

const propio = (o, k) => !!o && typeof o === 'object' && Object.prototype.hasOwnProperty.call(o, k);
const control = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;
export const una = (v, n) => String(v == null ? '' : v).replace(control, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
// Texto con saltos de linea (el texto principal del anuncio): sin caracteres de control, con a lo sumo dos lineas en blanco seguidas.
export const parrafos = (v, n) => String(v == null ? '' : v).replace(/\r\n?/g, '\n').replace(control, ' ').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, n);
export const plano = (s) => String(s || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase().trim();

// «Colombia» -> «colombia», «México» -> «mexico»: la clave del pais en FACEBOOK_TOKENS y en las reglas.
export function clavePais(pais) { const k = plano(pais).replace(/[^a-z]/g, ''); return /^[a-z]{3,20}$/.test(k) ? k : ''; }
// El id de un proyecto de la fabrica y el de un anuncio (AD1..AD12 o 101..130): se validan antes de armar una clave de KV.
export const ID_PROYECTO = /^[a-z0-9][a-z0-9_-]{2,40}$/i;
export const ID_ANUNCIO = /^(AD([1-9]|1[0-2])|1[0-2]\d|130)$/;
export const ID_FB = /^\d{5,25}$/;
export const claveRegistro = (proyecto, pais, anuncio) => PREFIJO + proyecto + ':' + pais + ':' + anuncio;

// El nombre de la oferta como lo escribe la guia (templates/fabrica.template.html, nombresDeCampana): en mayusculas, sin | # [ ] ( ) ni saltos de linea.
// Igual que la guia (las pruebas de la guia esperan el nombre tal cual): los < > del nombre de una oferta los escapa cada pantalla al pintarlo (revisado por seguridad el 14-oct y el 5-nov).
export function nombreOferta(nombre) { return String(nombre || '').toUpperCase().replace(/[|#\[\]()\r\n]+/g, ' ').replace(/\s+/g, ' ').trim(); }
// El nombre de campaña sin la fecha de arranque («ZAP #10 | OFERTA | INT - COM | * | 101»), en minusculas: para reconocer la del mismo anuncio montada a mano para otro dia.
export function nombreSinFecha(n) { return String(n || '').toLowerCase().replace(/\|\s*\d{1,2}\s+[a-z]{3}\s*\|/g, '| * |').replace(/\s+/g, ' ').trim(); }
export const ultimas4 = (tel) => String(tel || '').replace(/\D/g, '').slice(-4);
// El nombre que leen ofertaDeCampana (gasto por oferta), el explorador (numero del anuncio) y el freno del numero de las reglas.
export function nombreCampana(wpp, oferta, dia, anuncio) { return 'ZAP #' + wpp + ' | ' + nombreOferta(oferta) + ' | INT - COM | ' + dia + ' | ' + anuncio; }

// La proxima 5 a. m. en la zona de la cuenta que quede al menos MARGEN_MIN minutos adelante. `offsetHoras` = timezone_offset_hours_utc de la cuenta (-5 en Colombia).
// Devuelve { ms, iso: '2026-11-06T05:00:00-0500', dia: '6 NOV', fecha: '2026-11-06' }.
export function inicioProgramado(ahoraMs, offsetHoras) {
  const off = Math.round((Number(offsetHoras) || 0) * 60);                      // minutos
  const local = new Date(ahoraMs + off * 60000);
  let ms = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate(), HORA_INICIO) - off * 60000;
  while (ms < ahoraMs + MARGEN_MIN * 60000) ms += 86400000;
  const d = new Date(ms + off * 60000);
  const dos = (n) => String(n).padStart(2, '0');
  const fecha = d.getUTCFullYear() + '-' + dos(d.getUTCMonth() + 1) + '-' + dos(d.getUTCDate());
  const signo = off < 0 ? '-' : '+', a = Math.abs(off);
  return { ms, iso: fecha + 'T' + dos(HORA_INICIO) + ':00:00' + signo + dos(Math.floor(a / 60)) + dos(a % 60), dia: d.getUTCDate() + ' ' + MESES[d.getUTCMonth()], fecha };
}

// El id de un ARCHIVO de Drive a partir del enlace que dejo Claudia (…/file/d/<id>/view, …/open?id=<id>, …/uc?id=<id>). '' si es una carpeta u otra cosa.
export function idDeDrive(url) {
  let u; try { u = new URL(String(url || '')); } catch { return ''; }
  if (u.protocol !== 'https:' || !/^(drive|docs)\.google\.com$/.test(u.hostname)) return '';
  const m = /^\/file\/d\/([A-Za-z0-9_-]{20,80})(?:\/|$)/.exec(u.pathname);
  if (m) return m[1];
  if (/^\/(open|uc)$/.test(u.pathname)) { const id = u.searchParams.get('id') || ''; return /^[A-Za-z0-9_-]{20,80}$/.test(id) ? id : ''; }
  return '';
}
// Descarga directa de un archivo de Drive compartido con enlace: la usa Facebook para bajar el video (file_url) y el dash para comprobar antes que se puede bajar.
export const urlDescargaDrive = (id) => 'https://drive.usercontent.google.com/download?id=' + encodeURIComponent(id) + '&export=download&confirm=t';
// ¿Lo que contesto Drive es un video? (los primeros bytes de un MP4/MOV traen «ftyp»; una pagina de acceso es HTML).
export function pareceVideo(tipo, bytes) {
  if (/text\/html/i.test(String(tipo || ''))) return false;
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(0);
  const s = String.fromCharCode.apply(null, Array.from(b.slice(0, 32)));
  return s.indexOf('ftyp') !== -1 || /^video\//i.test(String(tipo || ''));
}

// La campaña modelo: el conjunto MAS NUEVO de esa cuenta que manda a WhatsApp con ese numero. Se reconoce por las ultimas 4 cifras del telefono (las guarda la guia al elegir el numero) y, si no
// las hay, por el «#N» del nombre de la campaña. Con las dos, gana el que cumple las dos. Devuelve el conjunto o null.
export function elegirModelo(conjuntos, numero) {
  const tel4 = /^\d{4}$/.test(String((numero && numero.tel4) || '')) ? String(numero.tel4) : '';
  const wpp = /^\d{1,3}$/.test(String((numero && numero.wpp) || '')) ? String(Number(numero.wpp)) : '';
  if (!tel4 && !wpp) return null;
  const conNumero = (s) => {
    const po = (s && s.promoted_object) || {};
    const telOk = tel4 ? String(po.whatsapp_phone_number || '').replace(/\D/g, '').slice(-4) === tel4 : false;
    const m = /#\s*0*(\d{1,3})(?!\d)/.exec(String((s && s.campaign && s.campaign.name) || ''));
    const wppOk = wpp ? !!m && m[1] === wpp : false;
    return { telOk, wppOk };
  };
  const validos = (Array.isArray(conjuntos) ? conjuntos : []).filter((s) => s && s.destination_type === 'WHATSAPP' && s.promoted_object && ID_FB.test(String(s.promoted_object.page_id || '')) && s.campaign && s.campaign.id);
  const puntos = (s) => { const c = conNumero(s); return (c.telOk ? 2 : 0) + (c.wppOk ? 1 : 0); };
  // con telefono: tiene que coincidir el telefono (el #N se reusa cuando un numero se cae y se cambia por otro); sin telefono: el #N.
  const candidatos = validos.filter((s) => (tel4 ? conNumero(s).telOk : conNumero(s).wppOk));
  candidatos.sort((a, b) => puntos(b) - puntos(a) || Date.parse(b.created_time || 0) - Date.parse(a.created_time || 0));
  return candidatos[0] || null;
}

// Los textos con los que arranca la vista previa: el texto principal sale de la promesa y las viñetas de la oferta; el titulo, la descripcion y el saludo de WhatsApp, del anuncio modelo; el mensaje que
// el cliente manda al tocar el boton lleva la palabra clave del flujo de venta (sin ella el flujo no arranca).
export function textosIniciales(proyecto, creativoModelo) {
  const c = (proyecto && proyecto.material && proyecto.material.copy) || {};
  const vd = (creativoModelo && creativoModelo.object_story_spec && creativoModelo.object_story_spec.video_data) || {};
  const clave = una(((c.claves || [])[0]) || '', 30);
  const vinetas = (Array.isArray(c.vinetas) ? c.vinetas : []).map((x) => una(x, 160)).filter(Boolean).slice(0, 8);
  const promesa = una(c.promesa || c.promesaCorta || '', 300);
  const mensaje = promesa ? parrafos(promesa + (vinetas.length ? '\n\n' + vinetas.map((v) => '✅ ' + v).join('\n') : '') + '\n\n👇 Escríbeme por WhatsApp y te cuento cómo tenerlo hoy.', 2000) : '';
  let bienvenida = '';
  try { const w = JSON.parse(vd.page_welcome_message || 'null'); bienvenida = una(w && w.text_format && w.text_format.message && w.text_format.message.text, 160); } catch { bienvenida = ''; }
  return {
    mensaje,
    titulo: una(vd.title, 120) || '👇 ¡Escríbeme por WhatsApp!',
    descripcion: una(vd.link_description, 120),
    autollenado: clave ? '¡Hola! Quiero ' + clave : '',
    bienvenida: bienvenida || 'Escríbenos y te contamos todo',
    clave,
  };
}

// Lo que manda la pantalla, limpio y comprobado. { ok, textos, errores:[texto] }
export function limpiarTextos(t, clave) {
  const x = t && typeof t === 'object' ? t : {};
  const textos = { mensaje: parrafos(x.mensaje, 2000), titulo: una(x.titulo, 120), descripcion: una(x.descripcion, 120), autollenado: una(x.autollenado, 160), bienvenida: una(x.bienvenida, 160) };
  const errores = [];
  if (textos.mensaje.length < 20) errores.push('Falta el texto principal del anuncio (al menos una frase).');
  if (!textos.titulo) errores.push('Falta el título del anuncio.');
  if (!textos.autollenado) errores.push('Falta el mensaje que el cliente manda por WhatsApp al tocar el botón.');
  const k = plano(clave);
  if (k && textos.autollenado && plano(textos.autollenado).indexOf(k) === -1) errores.push('El mensaje de WhatsApp tiene que llevar la palabra clave «' + una(clave, 30) + '»: sin ella el flujo de venta no arranca.');
  return { ok: !errores.length, textos, errores };
}

// El saludo de WhatsApp (page_welcome_message) del anuncio modelo con el mensaje NUESTRO: se cambia el texto que el cliente manda (autofill) y el saludo, y se quita la plantilla guardada del modelo
// (template_id): si se dejara, Facebook podria usar la plantilla, que lleva la palabra clave de la OTRA oferta.
export function bienvenidaJson(modeloStr, autollenado, bienvenida) {
  let o = null;
  try { o = JSON.parse(modeloStr || 'null'); } catch { o = null; }
  if (!o || typeof o !== 'object' || Array.isArray(o)) {
    o = { type: 'VISUAL_EDITOR', version: 2, landing_screen_type: 'welcome_message', media_type: 'text', text_format: { customer_action_type: 'autofill_message', message: { autofill_message: { content: '' }, text: '' } } };
  }
  delete o.template_id; delete o.template_version;
  if (!o.text_format || typeof o.text_format !== 'object') o.text_format = {};
  o.text_format.customer_action_type = 'autofill_message';
  if (!o.text_format.message || typeof o.text_format.message !== 'object') o.text_format.message = {};
  o.text_format.message.autofill_message = { content: autollenado };
  o.text_format.message.text = bienvenida;
  ['image_format', 'video_format'].forEach((k) => { if (o[k] && o[k].message && typeof o[k].message === 'object') o[k].message.text = bienvenida; });
  o.user_edit = true; o.autofill_message_edited = true; o.welcome_message_edited = true;
  return JSON.stringify(o);
}

// Lo que se copia del modelo, en limpio (sin ids de otros objetos ni campos de solo lectura). `m` = { conjunto, campana, creativo } tal como los devuelve Facebook.
export function plantillaDeModelo(m) {
  const s = m.conjunto || {}, c = s.campaign || m.campana || {}, cr = m.creativo || {};
  const oss = cr.object_story_spec || {}, vd = oss.video_data || {};
  const targeting = Object.assign({}, s.targeting || {});
  delete targeting.age_range;                          // de solo lectura (sale junto a age_min / age_max)
  const po = Object.assign({}, s.promoted_object || {});
  const cta = vd.call_to_action && vd.call_to_action.type === 'WHATSAPP_MESSAGE' ? vd.call_to_action : { type: 'WHATSAPP_MESSAGE', value: { app_destination: 'WHATSAPP' } };
  return {
    campana: {
      id: String(c.id || ''), nombre: una(c.name, 200), objetivo: c.objective || 'OUTCOME_ENGAGEMENT', compra: c.buying_type || 'AUCTION', puja: c.bid_strategy || 'LOWEST_COST_WITHOUT_CAP',
      categorias: Array.isArray(c.special_ad_categories) ? c.special_ad_categories : [], presupuesto: c.daily_budget != null ? Number(c.daily_budget) : null,
    },
    conjunto: {
      id: String(s.id || ''), optimizacion: s.optimization_goal || 'CONVERSATIONS', cobro: s.billing_event || 'IMPRESSIONS', destino: 'WHATSAPP', objetoPromovido: po, targeting,
      atribucion: Array.isArray(s.attribution_spec) ? s.attribution_spec : null, presupuesto: s.daily_budget != null ? Number(s.daily_budget) : null, puja: s.bid_strategy || '', pujaMonto: s.bid_amount != null ? Number(s.bid_amount) : null,
      creado: s.created_time || '',
    },
    creativo: {
      id: String(cr.id || ''), pagina: String(oss.page_id || po.page_id || ''), instagram: String(oss.instagram_user_id || ''), cta, bienvenida: typeof vd.page_welcome_message === 'string' ? vd.page_welcome_message : '',
      libertad: cr.degrees_of_freedom_spec && typeof cr.degrees_of_freedom_spec === 'object' ? cr.degrees_of_freedom_spec : null,
      multi: cr.contextual_multi_ads && typeof cr.contextual_multi_ads === 'object' ? cr.contextual_multi_ads : null, video: String(vd.video_id || ''),
    },
  };
}
// El presupuesto diario del modelo (en la campaña, como las monta la trafficker; o en el conjunto) y donde va.
export function presupuestoDe(pl) {
  if (pl.campana.presupuesto > 0) return { unidades: pl.campana.presupuesto, en: 'campana' };
  if (pl.conjunto.presupuesto > 0) return { unidades: pl.conjunto.presupuesto, en: 'conjunto' };
  return null;
}
export function presupuestoEnMoneda(unidades, moneda) { return SIN_DECIMALES.includes(moneda) ? unidades : unidades / 100; }
export function textoPlata(unidades, moneda) {
  const v = presupuestoEnMoneda(unidades, moneda);
  return (moneda === 'COP' ? '$' : moneda === 'USD' ? 'US$ ' : moneda + ' ') + v.toLocaleString('es-CO', { maximumFractionDigits: 2 }) + (moneda === 'COP' ? ' pesos' : '');
}
// null si el presupuesto sirve; si no, por que no.
export function presupuestoMalo(unidades, moneda) {
  if (!(unidades > 0) || !Number.isFinite(unidades)) return 'la campaña modelo no tiene un presupuesto diario';
  const tope = TOPE_PRESUPUESTO[moneda];
  if (!tope) return 'la cuenta está en ' + una(moneda, 5) + ' y el dash no tiene un tope de presupuesto para esa moneda';
  if (unidades > tope) return 'el presupuesto del modelo (' + textoPlata(unidades, moneda) + ' al día) pasa el tope de ' + textoPlata(tope, moneda) + ' por campaña';
  return null;
}

// Los parametros de cada llamada a Facebook (lo que va en el cuerpo del POST, todo como texto). `pl` = plantillaDeModelo; `pres` = presupuestoDe.
// TODO nace en PAUSA (campaña, conjunto y anuncio): el conjunto y el anuncio se activan por dentro solo al final, cuando todo cuadra (la campaña sigue en pausa: la enciende el equipo).
// Asi una campaña frenada, fallida o sobrante no gasta aunque alguien la encienda por error en Ads Manager (revision del 6-nov).
export function paramsCampana(pl, nombre, pres) {
  const p = { name: nombre, objective: pl.campana.objetivo, status: 'PAUSED', buying_type: pl.campana.compra, special_ad_categories: JSON.stringify(pl.campana.categorias) };
  if (pres.en === 'campana') { p.daily_budget = String(Math.round(pres.unidades)); p.bid_strategy = pl.campana.puja; }
  else p.is_adset_budget_sharing_enabled = 'false';
  return p;
}
export function paramsConjunto(pl, nombre, campanaId, inicioIso, pres) {
  const p = {
    name: nombre, campaign_id: String(campanaId), status: 'PAUSED', optimization_goal: pl.conjunto.optimizacion, billing_event: pl.conjunto.cobro, destination_type: 'WHATSAPP',
    promoted_object: JSON.stringify(pl.conjunto.objetoPromovido), targeting: JSON.stringify(pl.conjunto.targeting), start_time: inicioIso,
  };
  if (pl.conjunto.atribucion) p.attribution_spec = JSON.stringify(pl.conjunto.atribucion);
  if (pres.en === 'conjunto') {
    p.daily_budget = String(Math.round(pres.unidades));
    if (pl.conjunto.puja) p.bid_strategy = pl.conjunto.puja;
    if (pl.conjunto.pujaMonto > 0) p.bid_amount = String(pl.conjunto.pujaMonto);
  }
  return p;
}
export function paramsCreativo(pl, nombre, videoId, imagenUrl, textos, conInstagram) {
  const vd = { video_id: String(videoId), title: textos.titulo, message: textos.mensaje, call_to_action: pl.creativo.cta, page_welcome_message: bienvenidaJson(pl.creativo.bienvenida, textos.autollenado, textos.bienvenida) };
  if (imagenUrl) vd.image_url = imagenUrl;
  if (textos.descripcion) vd.link_description = textos.descripcion;
  const oss = { page_id: pl.creativo.pagina, video_data: vd };
  if (conInstagram && pl.creativo.instagram) oss.instagram_user_id = pl.creativo.instagram;
  const p = { name: nombre, object_story_spec: JSON.stringify(oss) };
  if (pl.creativo.libertad) p.degrees_of_freedom_spec = JSON.stringify(pl.creativo.libertad);
  if (pl.creativo.multi) p.contextual_multi_ads = JSON.stringify(pl.creativo.multi);
  return p;
}
export function paramsAnuncio(nombre, conjuntoId, creativoId) {
  return { name: nombre, adset_id: String(conjuntoId), creative: JSON.stringify({ creative_id: String(creativoId) }), status: 'PAUSED' };
}

// Las reglas encendidas que van a cuidar una campaña nueva con ese nombre en ese pais: las que tienen una accion de PAUSAR y la toman por «todas» o por «campañas futuras» con un filtro que su nombre
// cumple (la misma cuenta que hace el ejecutor, functions/api/reglas-ejecutar.js, asignada/filtroCoincide). Una regla con lista fija de campañas no puede incluir una que todavia no existe.
export function reglasQueCubren(reglas, pais, nombre, tipo) {
  const accion = tipo || 'pausar';
  const lista = reglas && Array.isArray(reglas.lista) ? reglas.lista : [];
  const n = String(nombre || '').toLowerCase();
  const fila = (f) => { const v = String((f && f.valor) || '').toLowerCase(); return f && f.op === 'no_contiene' ? n.indexOf(v) === -1 : f && f.op === 'empieza' ? n.indexOf(v) === 0 : f && f.op === 'es' ? n === v : n.indexOf(v) !== -1; };
  const filtroOk = (fl) => { const filas = (fl && Array.isArray(fl.filas)) ? fl.filas : []; if (!filas.length) return true; return fl.op === 'or' ? filas.some(fila) : filas.every(fila); };
  return lista.filter((r) => {
    if (!r || r.on !== true) return false;
    const a = r.aplica || {};
    const paises = Array.isArray(a.paises) ? a.paises : [];
    if (paises.length && paises.indexOf(pais) === -1) return false;
    const pausa = (r.tareas || []).some((t) => (t.acciones || []).some((x) => x && x.tipo === accion));
    if (!pausa) return false;
    return !!a.todas || (!!a.futuras && filtroOk(a.filtro));
  }).map((r) => ({ id: una(r.id, 40), nombre: una(r.nombre, 80) }));
}
// Las reglas encendidas que podrian REACTIVAR solas una campaña nueva de ese pais (accion «reactivar» sobre «todas» o «campañas futuras»): nacen en pausa para que el equipo las revise,
// y una regla asi podria encenderlas antes (revision del 6-nov). La vista previa lo avisa.
export function reglasQueReactivan(reglas, pais, nombre) { return reglasQueCubren(reglas, pais, nombre, 'reactivar'); }

// La huella de lo que la persona vio y confirma: cuenta, presupuesto, moneda, hora de arranque y que campañas. Si al confirmar el plan ya es otro (alguien cambio el modelo, aprobo otro anuncio), se
// le pide revisar de nuevo en vez de crear algo que no vio.
export function huellaPlan(x) {
  const s = [x.cuenta, x.presupuesto, x.moneda, x.inicio, (x.anuncios || []).join(','), x.modelo || '', x.pagina || '', x.destino || ''].join('|');
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  return h.toString(36);
}

// El registro de UNA campaña (clave fcamp:...), limpio para mostrarlo: nada de tokens ni de la plantilla entera.
export function resumenRegistro(r) {
  if (!r || typeof r !== 'object') return null;
  return {
    anuncio: r.anuncio, estado: ESTADOS.includes(r.estado) ? r.estado : 'error', nombre: una(r.nombre, 200), cuenta: una(r.cuenta, 30), inicio: una(r.inicio && r.inicio.iso, 40), dia: una(r.inicio && r.inicio.dia, 10),
    presupuesto: Number(r.presupuesto && r.presupuesto.unidades) || 0, moneda: una(r.moneda, 5), ids: Object.assign({}, r.ids || {}), por: una(r.por, 20), t: una(r.t, 30), fin: una(r.fin, 30),
    actualizado: una(r.actualizado || r.t, 30), nota: una(r.nota, 300), error: una(r.error, 400), pasos: (Array.isArray(r.pasos) ? r.pasos : []).slice(-12).map((p) => ({ t: una(p.t, 30), txt: una(p.txt, 200) })),
  };
}
export const sinPropio = propio;
