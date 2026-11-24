// Hablarle a ZapData desde el servidor, sin que haya una persona en el medio.
//
// Hasta ahora el tablero armaba el pedido y lo dejaba en una cola que recogia alguien con
// una sesion de chat abierta. Eso sirve mientras ese alguien este disponible, y no lo esta:
// quien crea las ofertas trabaja desde el tablero y no tiene acceso a ninguna sesion. Un
// pedido en cola que nadie recoge es un flujo que no existe.
//
// ZapData publica un servidor MCP para esto y lo dice en su propio panel, en
// Integraciones -> Conectar con Claude. Ahi se crea una credencial de tipo "Token
// estatico", pensada justamente para Cursor, n8n y cosas que corren solas, y se le dan
// permisos. El que hace falta aqui es "Crear borrador de flujo", que es estrecho a
// proposito: los flujos nacen apagados y sin numero asignado, asi que ni un error en este
// archivo le manda un mensaje a nadie.
//
// La credencial se pega en el tablero, en la pestaña Conexion, y se guarda en KV. Tambien
// se acepta en la variable ZAPDATA_TOKEN de Cloudflare, que es como estaba antes de que
// existiera la pestaña. Sin ninguna de las dos esto no falla: el pedido se guarda igual y
// se queda en la cola.
//
// El protocolo es JSON-RPC por HTTP. Tres pasos: presentarse, avisar que ya se presento, y
// llamar a la herramienta. La respuesta puede venir como JSON normal o como flujo de
// eventos, segun el dia; se aceptan las dos.

export const SERVIDOR = 'https://webhook.zapdata.co/functions/v1/mcp';
export const CLAVE = 'zapdata:credencial';
export const PENDIENTE = 'zapdata:oauth-pendiente';
const VERSION = '2025-06-18';

// Por que OAuth y no el token estatico que ofrece su panel.
//
// El panel deja crear credenciales de token estatico, pero el permiso que hace falta,
// "Crear borrador de flujo", esta marcado ahi como "Proximamente" y no se deja seleccionar.
// Por OAuth si se entrega: el propio servidor lo anuncia como `flows:draft` en su lista de
// permisos, y es el camino que usan Claude y los demas clientes que si crean flujos. O sea
// que la capacidad existe; lo que falta es la casilla en su formulario.
//
// Asi que el tablero pide permiso como cliente OAuth, igual que cualquier otro, y guarda lo
// que le devuelven. El token de acceso se vence y se renueva solo con el de refresco.
//
// Permisos MINIMOS. `reports:conversations` NO se pide por defecto (2026-10-27): abre la lectura de las conversaciones de las clientas y esta credencial no la usa para nada (lo unico que hace es crear
// borradores de flujo, probarse y listar las conexiones, ver listarCanales mas abajo). Puede que zapdata_channel_list (la lista de TODAS las conexiones, conectadas o no, que usan /numeros y /oficina por
// /api/zapdata-canales) funcione ya con estos dos: su descripcion dice que sirve para asignar un flujo a un canal, y eso si lo hace esta conexion. NO se pudo comprobar con la credencial real. Si la
// pagina Numeros dice «Lista en vivo de ZapData: no disponible» por permiso (motivo `sin_permiso`), el dueno decide si reconecta pidiendo uno mas, a proposito, desde su PC:
//   scripts\Conectar-ZapData.ps1 -Permisos 'flows:draft reports:geography reports:conversations' -SoloPortapapeles
// y pega el resultado en Flujos ZapData > Conexion. Eso no cambia esta constante: la reconexion web y el script sin parametros siguen pidiendo solo los dos permisos de siempre.
export const PERMISOS = 'flows:draft reports:geography';

// Lo que anuncia el servidor. Se consulta en vivo y se cae a estos valores si su
// descubrimiento no contesta, para que una caida momentanea no deje al tablero sin saber a
// donde mandar a la gente.
const RESPALDO = {
  authorization_endpoint: 'https://zapdata.co/authorize',
  token_endpoint: 'https://webhook.zapdata.co/functions/v1/mcp-oauth/token',
  registration_endpoint: 'https://webhook.zapdata.co/functions/v1/mcp-oauth/register',
};

export async function descubrir() {
  try {
    const r = await fetch('https://zapdata.co/.well-known/oauth-authorization-server');
    if (r.ok) {
      const m = await r.json();
      if (m.authorization_endpoint && m.token_endpoint) return m;
    }
  } catch (e) { /* se sigue con el respaldo */ }
  return RESPALDO;
}

const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

function azar(n) {
  const a = new Uint8Array(n);
  crypto.getRandomValues(a);
  return b64url(a.buffer);
}

async function reto(verificador) {
  const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verificador));
  return b64url(h);
}

// Registrarse como cliente. El servidor no reparte identificadores a mano: los da en el
// momento, que es lo que permite que esto funcione sin que nadie cree nada en su panel.
export async function registrarCliente(meta, redirect) {
  const r = await fetch(meta.registration_endpoint || RESPALDO.registration_endpoint, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_name: 'Tablero Mi Negocio',
      redirect_uris: [redirect],
      grant_types: ['authorization_code', 'refresh_token'],
      response_types: ['code'],
      token_endpoint_auth_method: 'none',
      scope: PERMISOS,
    }),
  });
  const txt = await r.text();
  if (!r.ok) throw new Error('ZapData no aceptó registrar el tablero (' + r.status + '): ' + txt.slice(0, 200));
  let c;
  try { c = JSON.parse(txt); } catch (e) { throw new Error('ZapData contestó algo que no es JSON al registrar.'); }
  if (!c.client_id) throw new Error('ZapData no devolvió identificador de cliente.');
  return c;
}

// Primera pata: a donde mandar al dueno para que autorice.
export async function comenzar(redirect) {
  const meta = await descubrir();
  const cliente = await registrarCliente(meta, redirect);
  const verificador = azar(48);
  const estado = azar(24);
  const u = new URL(meta.authorization_endpoint || RESPALDO.authorization_endpoint);
  u.searchParams.set('response_type', 'code');
  u.searchParams.set('client_id', cliente.client_id);
  u.searchParams.set('redirect_uri', redirect);
  u.searchParams.set('scope', PERMISOS);
  u.searchParams.set('state', estado);
  u.searchParams.set('code_challenge', await reto(verificador));
  u.searchParams.set('code_challenge_method', 'S256');
  u.searchParams.set('resource', SERVIDOR);
  return { url: u.toString(), pendiente: { estado, verificador, redirect, cliente, meta, creadoEn: Date.now() } };
}

async function pedirToken(meta, cuerpo) {
  const r = await fetch(meta.token_endpoint || RESPALDO.token_endpoint, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(cuerpo).toString(),
  });
  const txt = await r.text();
  if (!r.ok) throw new Error('ZapData rechazó el intercambio (' + r.status + '): ' + txt.slice(0, 200));
  let t;
  try { t = JSON.parse(txt); } catch (e) { throw new Error('ZapData contestó algo que no es JSON al dar el token.'); }
  if (!t.access_token) throw new Error('ZapData no devolvió token de acceso.');
  return t;
}

// Segunda pata: cambiar el codigo por el token.
export async function canjear(pendiente, code) {
  const t = await pedirToken(pendiente.meta, {
    grant_type: 'authorization_code',
    code,
    redirect_uri: pendiente.redirect,
    client_id: pendiente.cliente.client_id,
    code_verifier: pendiente.verificador,
    resource: SERVIDOR,
  });
  return {
    tipo: 'oauth', token: t.access_token, refresco: t.refresh_token || '',
    vence: t.expires_in ? Date.now() + (t.expires_in - 60) * 1000 : 0,
    permisos: t.scope || PERMISOS, cliente: pendiente.cliente, meta: pendiente.meta,
  };
}

// El token de acceso dura poco y el de refresco mucho. Sin esto, el tablero funciona una
// hora y despues deja de crear flujos sin que nadie sepa por que.
// Renueva si ya vencio. Se usa al guardar un permiso conseguido desde el PC: entre que se
// pide y se pega puede pasar mas de una hora, y el token de acceso dura eso. Sin esto, la
// pantalla diria que la credencial no sirve cuando lo unico que paso es que se enfrio.
export async function refrescarSiHaceFalta(g) {
  if (g && g.refresco && g.vence && Date.now() > g.vence) await renovar(g);
  return g;
}

async function renovar(g) {
  const t = await pedirToken(g.meta || RESPALDO, {
    grant_type: 'refresh_token',
    refresh_token: g.refresco,
    client_id: (g.cliente || {}).client_id,
    resource: SERVIDOR,
  });
  g.token = t.access_token;
  if (t.refresh_token) g.refresco = t.refresh_token;
  g.vence = t.expires_in ? Date.now() + (t.expires_in - 60) * 1000 : 0;
  return g;
}

// La herramienta que importa. Si la credencial no la trae, el tablero puede armar el
// pedido pero no montarlo, y mas vale decirlo al guardar el token que descubrirlo la noche
// que hay tres ofertas que salir a probar.
export const HERRAMIENTA = 'zapdata_flow_draft_create';

// Una respuesta en flujo de eventos son lineas "data: {...}". Interesa la que traiga la
// respuesta a la llamada.
function leerEventos(texto) {
  let ultimo = null;
  texto.split(/\r?\n/).forEach((linea) => {
    if (linea.indexOf('data:') !== 0) return;
    const crudo = linea.slice(5).trim();
    if (!crudo || crudo === '[DONE]') return;
    try { const o = JSON.parse(crudo); if (o && (o.result || o.error)) ultimo = o; } catch (e) { /* trozo partido */ }
  });
  return ultimo;
}

async function llamar(metodo, params, estado) {
  const cabeceras = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
    'MCP-Protocol-Version': VERSION,
    'Authorization': 'Bearer ' + estado.token,
  };
  if (estado.sesion) cabeceras['Mcp-Session-Id'] = estado.sesion;

  const cuerpo = { jsonrpc: '2.0', method: metodo };
  if (params) cuerpo.params = params;
  // Los avisos no llevan identificador y no esperan respuesta. Mandarles uno hace que
  // algunos servidores contesten un error en vez de seguir.
  const esAviso = metodo.indexOf('notifications/') === 0;
  if (!esAviso) cuerpo.id = ++estado.n;

  const r = await fetch(SERVIDOR, { method: 'POST', headers: cabeceras, body: JSON.stringify(cuerpo) });
  const nueva = r.headers.get('mcp-session-id');
  if (nueva) estado.sesion = nueva;
  if (esAviso) return null;

  const texto = await r.text();
  if (!r.ok) {
    if (r.status === 401 || r.status === 403) {
      throw new Error('ZapData no aceptó la credencial (' + r.status + '). Revisa que esté completa y sin vencer.');
    }
    throw new Error('ZapData contestó ' + r.status + (texto ? ': ' + texto.slice(0, 300) : ''));
  }
  let res = null;
  if (texto.indexOf('data:') === 0 || (r.headers.get('content-type') || '').indexOf('event-stream') >= 0) {
    res = leerEventos(texto);
  } else {
    try { res = JSON.parse(texto); } catch (e) { throw new Error('ZapData contestó algo que no es JSON: ' + texto.slice(0, 200)); }
  }
  if (!res) throw new Error('ZapData no contestó nada a ' + metodo);
  if (res.error) throw new Error(res.error.message || JSON.stringify(res.error));
  return res.result;
}

async function abrir(token) {
  const estado = { token, sesion: null, n: 0 };
  await llamar('initialize', {
    protocolVersion: VERSION,
    capabilities: {},
    clientInfo: { name: 'tablero-mi-negocio', version: '1.0' },
  }, estado);
  await llamar('notifications/initialized', {}, estado);
  return estado;
}

// El resultado de una herramienta MCP viene como una lista de trozos de contenido. El que
// interesa es el texto, que trae el JSON con el identificador del flujo creado.
function leerResultado(result) {
  if (result && result.structuredContent) return result.structuredContent;
  const trozos = (result && result.content) || [];
  for (const t of trozos) {
    if (t && t.type === 'text' && t.text) {
      try { return JSON.parse(t.text); } catch (e) { return { texto: t.text }; }
    }
  }
  return {};
}

// La credencial guardada en el tablero manda sobre la variable de Cloudflare: se cambia
// desde la pestaña Conexion sin desplegar nada.
//
// Si es de OAuth y esta por vencerse, se renueva aqui y se vuelve a guardar. Se hace en la
// lectura y no en un trabajo aparte porque no hay ningun momento mejor: el token solo
// importa cuando alguien va a crear un flujo.
//
// Desde 2026-10-27 la leen tambien /numeros y /oficina (por /api/zapdata-canales), no solo quien crea un flujo a mano: varias lecturas pueden llegar justo cuando el token se vence. Cada una gastaria el MISMO
// codigo de refresco (si ZapData lo rota, las demas quedarian con uno ya gastado y el permiso se perderia) y la ultima en escribir pisaria a las otras, o a una reconexion recien pegada. Por eso:
//   1) una sola renovacion a la vez por aislado (`renovando`): las lecturas que llegan mientras tanto esperan esa;
//   2) si la renovacion falla, o antes de escribir, se vuelve a leer KV: si lo guardado YA es otra credencial que sirve (otro aislado renovo, o el dueno reconecto), esa gana y esta no se escribe.
// Entre aislados distintos KV tarda en ponerse al dia (hasta ~60 s): esto reduce la carrera, no la elimina.
// Un «vuelo» que lleva demasiado se da por perdido (PLAZOS): en Workers lo pendiente de un pedido que se cancela a medias (cierran la pagina) no termina nunca, y sin este limite todos los pedidos
// siguientes de ese aislado se quedarian esperando una renovacion o una consulta que ya no existe.
// `consultaMs` es MAYOR que `renovacionMs`: una consulta incluye leer el token, y si espera una renovacion viva no debe darse por perdida antes que ella.
export const PLAZOS = { renovacionMs: 20000, consultaMs: 30000 };

// Espera una promesa como mucho `ms` (y apaga su reloj al terminar); si se pasa, rechaza con Error('espera').
export function conPlazo(promesa, ms) {
  let reloj;
  const plazo = new Promise((_, no) => { reloj = setTimeout(() => no(new Error('espera')), ms); });
  return Promise.race([promesa, plazo]).finally(() => clearTimeout(reloj));
}

let renovando = null;   // { t, p }: la renovacion en marcha en este aislado

async function credencialOtra(env, antes) {
  try {
    const o = await env.SUITE.get(CLAVE, { type: 'json' });
    const vencida = o && o.tipo === 'oauth' && o.vence && Date.now() > o.vence;
    if (o && o.token && !vencida && (o.token !== antes.token || o.refresco !== antes.refresco)) return o;
  } catch (e) { /* sin KV: no hay otra */ }
  return null;
}

async function renovarYGuardar(env, g) {
  const antes = { token: g.token, refresco: g.refresco };
  try {
    await renovar(g);
  } catch (e) {
    const otra = await credencialOtra(env, antes);
    if (otra) return otra.token;
    throw new Error('Se venció el permiso de ZapData y no se pudo renovar. Vuelve a conectar en la pestaña Conexión. (' + (e.message || e) + ')');
  }
  const otra = await credencialOtra(env, antes);
  if (otra) return otra.token;
  // Como siempre: si no se puede GUARDAR lo renovado (p. ej. se acabo el tope diario de escrituras de KV) el aviso es el mismo, no un token vacio.
  try { await env.SUITE.put(CLAVE, JSON.stringify(g)); } catch (e) {
    throw new Error('Se venció el permiso de ZapData y no se pudo renovar. Vuelve a conectar en la pestaña Conexión. (' + (e.message || e) + ')');
  }
  return g.token;
}

// Renueva con UN solo vuelo vivo por aislado: los que llegan mientras otro renueva esperan su resultado (hasta PLAZOS.renovacionMs). Si esa espera se pasa (el que renovaba se cancelo a medias), antes de
// intentar nada se mira KV por si ya hay una credencial nueva (el codigo de refresco puede estar ya gastado) y, si no, se vuelve a entrar al vuelo: el primero que llega abre uno nuevo y los demas lo
// esperan, en vez de renovar cada uno por su cuenta con el mismo codigo.
async function renovarConVuelo(env, g) {
  const antes = { token: g.token, refresco: g.refresco };
  for (let intento = 0; intento < 3; intento++) {
    if (intento > 0) { const otra = await credencialOtra(env, antes); if (otra) return otra.token; }
    const viva = renovando && Date.now() - renovando.t < PLAZOS.renovacionMs ? renovando : null;
    let mia = null;
    if (!viva) {
      mia = { t: Date.now(), p: null };
      mia.p = renovarYGuardar(env, g).finally(() => { if (renovando === mia) renovando = null; });
      renovando = mia;
    }
    try { return await (mia ? mia.p : conPlazo(viva.p, PLAZOS.renovacionMs)); } catch (e) {
      if (mia || /ZapData/.test(String((e && e.message) || ''))) throw e;
      // la espera se paso (o fallo por algo ajeno a ZapData): se vuelve a mirar KV y a entrar al vuelo
    }
  }
  throw new Error('Se venció el permiso de ZapData y no se pudo renovar. Vuelve a conectar en la pestaña Conexión.');
}

export async function leerToken(env) {
  if (env && env.SUITE) {
    try {
      const g = await env.SUITE.get(CLAVE, { type: 'json' });
      if (g && g.token) {
        if (g.tipo === 'oauth' && g.refresco && g.vence && Date.now() > g.vence) return await renovarConVuelo(env, g);
        return g.token;
      }
    } catch (e) {
      if (e && e.message && e.message.indexOf('ZapData') >= 0) throw e;
      /* sin KV se sigue con la variable */
    }
  }
  return (env && env.ZAPDATA_TOKEN) || '';
}

export async function hayToken(env) {
  return !!(await leerToken(env));
}

// Que permite hacer la credencial, preguntandoselo al servidor. Es lo que contesta la
// pestaña Conexion al darle probar, y de paso responde la pregunta que importa: si el
// permiso de crear flujos ya esta dado o sigue pendiente del lado de ellos.
export async function probar(token) {
  const estado = await abrir(token);
  const res = await llamar('tools/list', {}, estado);
  const nombres = ((res && res.tools) || []).map((t) => t.name);
  return { herramientas: nombres, puedeCrear: nombres.indexOf(HERRAMIENTA) !== -1 };
}

// La lista de TODAS las conexiones de WhatsApp de la cuenta, conectadas o no, con su estado REAL (zapdata_channel_list). Solo lectura. La API publica de ZapData (/v1/instances) solo trae las conectadas:
// por eso una conexion caida, o nueva y nunca conectada, no existia para /numeros y /oficina (2026-10-27: 12 numeros en el dash y 20 en ZapData). Devuelve el arreglo `canais` tal como lo manda ZapData
// (con el telefono completo: quien lo use tiene que limpiarlo, ver functions/api/zapdata-canales.js). Si falla, el error trae `codigo`: 'sin_conexion' (no hay credencial) o 'sin_permiso' (la herramienta
// no esta en lo que la credencial puede hacer); lo demas lo clasifica quien llama por el mensaje.
// ZapData tiene TOPES DIARIOS por credencial y por herramienta (comprobado el 2026-11-06: «Limite de uso atingido para hoje (107/600)»; 600 al dia en las de flujos, 60 en las de reportes y
// numeros; los intentos rechazados tambien cuentan). Un 429 de la red es lo mismo. Con `codigo` = 'limite' quien llama deja de insistir en vez de gastar lo poco que quede.
export const ES_LIMITE = /l[ií]mite de uso|usage limit|rate limit|too many requests|contest[oó] 429\b/i;
const SIN_PERMISO = /permiss|scope|autoriz|forbidden|not allowed|insufficient|unauthor|unknown tool|tool not found|no such tool/i;
// El error de una herramienta que contesto isError, con `codigo` 'limite' | 'sin_permiso' cuando se reconoce.
function errorDeHerramienta(d) {
  const msg = d.texto || d.error || d.message || JSON.stringify(d).slice(0, 200);
  const e = new Error(msg);
  if (ES_LIMITE.test(msg)) e.codigo = 'limite';
  else if (SIN_PERMISO.test(msg)) e.codigo = 'sin_permiso';
  return e;
}

export async function listarCanales(env) {
  const token = await leerToken(env);
  if (!token) { const e = new Error('Falta la credencial de ZapData.'); e.codigo = 'sin_conexion'; throw e; }
  const estado = await abrir(token);
  const res = await llamar('tools/call', { name: 'zapdata_channel_list', arguments: {} }, estado);
  const d = leerResultado(res);
  if (res && res.isError) {
    throw errorDeHerramienta(d);
  }
  const lista = Array.isArray(d.canais) ? d.canais : (Array.isArray(d.channels) ? d.channels : null);
  if (!lista) throw new Error('ZapData no devolvió la lista de conexiones: ' + JSON.stringify(d).slice(0, 160));
  return lista;
}

// Los flujos de la cuenta y si estan encendidos, para la guia de lanzamiento de /fabrica (functions/api/zapdata-flujos.js, paso 2 «Venta y Entrega encendidos con ese número»). Solo lectura.
// Comprobado el 2026-11-05 con las dos conexiones que habia a mano (sin escribir nada en ZapData):
//   - una conexion OAuth como la del tablero (los tres permisos que ZapData anuncia: flows:draft, reports:geography, reports:conversations) trae `zapdata_flow_draft_list`: los ULTIMOS 50 borradores
//     creados por MCP en la cuenta (los de la Fabrica lo son, los cree quien los cree), con `is_active` pero SIN el numero asignado; y NO trae `zapdata_flow_list`;
//   - una de token estatico (la del PC del dueño) SI trae `zapdata_flow_list`: todos los flujos, con `ativo` y `channel_ids` (las conexiones a las que esta asignado cada uno).
// Por eso se le pregunta a ZapData que herramientas da ESTA credencial (tools/list) y se usa la mejor: si un dia la conexion del tablero trae zapdata_flow_list, la guia tambien comprueba el numero sin
// tocar codigo. Devuelve { fuente: 'lista' | 'borradores', flujos: [{ id, activo, canales: [...] | null }] } (canales null = esta credencial no lo deja ver). Los errores traen `codigo` como listarCanales.
// Ademas, con la fuente 'lista', cada flujo trae lo que necesita la revision del inicio y la rama de pagos (functions/_zapdata-revision.js): nombre, cuando cambio (`t`), su gatillo, si lo creo el
// tablero y si ZapData deja editarlo; y la respuesta trae `sesion` (la conexion MCP ya abierta, para leer flujos sueltos con leerFlujo sin volver a presentarse; no se manda nunca al navegador) y
// `lectura` (la credencial trae zapdata_flow_get: sin ella no se pueden revisar el inicio ni la rama de pagos, y no se intenta).
// zapdata_flow_list solo acepta `limit` (max. 100, sin paginas) y devuelve los 100 flujos CAMBIADOS MAS RECIENTEMENTE (comprobado el 2026-11-06 con limit 5).
export const HERRAMIENTAS_FLUJOS = { lista: 'zapdata_flow_list', borradores: 'zapdata_flow_draft_list' };
const ID_FLUJO = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ID_CANAL = /^[0-9a-f-]{16,64}$/i;
const textoCorto = (v, n) => String(v == null ? '' : v).replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
// UN flujo completo (zapdata_flow_get: `fluxo` con sus bloques y `referenceFlow` con nodos y aristas, donde esta el inicio). Solo lectura. Pesa ~100-150 KB: quien lo llama lo hace con tope.
export async function leerFlujo(sesion, id) {
  if (!ID_FLUJO.test(String(id || ''))) throw new Error('Ese no es el id de un flujo.');
  const res = await llamar('tools/call', { name: 'zapdata_flow_get', arguments: { flow_id: String(id).toLowerCase() } }, sesion);
  const d = leerResultado(res);
  if (res && res.isError) {
    throw errorDeHerramienta(d);
  }
  if (!d || typeof d !== 'object' || !d.fluxo) throw new Error('ZapData no devolvió el flujo: ' + JSON.stringify(d).slice(0, 160));
  return d;
}
export async function listarFlujos(env) {
  const token = await leerToken(env);
  if (!token) { const e = new Error('Falta la credencial de ZapData.'); e.codigo = 'sin_conexion'; throw e; }
  const estado = await abrir(token);
  const nombres = [];
  let cursor;
  for (let pagina = 0; pagina < 4; pagina++) {
    const res = await llamar('tools/list', cursor ? { cursor } : {}, estado);
    ((res && res.tools) || []).forEach((t) => { if (t && typeof t.name === 'string') nombres.push(t.name); });
    cursor = res && typeof res.nextCursor === 'string' && res.nextCursor ? res.nextCursor : '';
    if (!cursor) break;
  }
  const fuente = nombres.indexOf(HERRAMIENTAS_FLUJOS.lista) >= 0 ? 'lista' : (nombres.indexOf(HERRAMIENTAS_FLUJOS.borradores) >= 0 ? 'borradores' : '');
  if (!fuente) { const e = new Error('La credencial de ZapData no deja leer los flujos (no trae ' + HERRAMIENTAS_FLUJOS.borradores + ').'); e.codigo = 'sin_permiso'; throw e; }
  const res = await llamar('tools/call', { name: HERRAMIENTAS_FLUJOS[fuente], arguments: { limit: fuente === 'lista' ? 100 : 50 } }, estado);
  const d = leerResultado(res);
  if (res && res.isError) {
    throw errorDeHerramienta(d);
  }
  const crudos = fuente === 'lista' ? d.fluxos : d.drafts;
  if (!Array.isArray(crudos)) throw new Error('ZapData no devolvió la lista de flujos: ' + JSON.stringify(d).slice(0, 160));
  const flujos = [];
  for (const f of crudos.slice(0, 200)) {
    if (!f || typeof f !== 'object') continue;
    const id = String(f.flow_id || f.id || '').trim().toLowerCase();
    if (!ID_FLUJO.test(id)) continue;
    const v = fuente === 'lista' ? f.ativo : f.is_active;
    const activo = v === true ? true : (v === false ? false : null);                    // null = ZapData no lo dijo (no se inventa un «apagado»)
    const canales = fuente === 'lista' && Array.isArray(f.channel_ids) ? f.channel_ids.map((x) => String(x || '').trim().toLowerCase()).filter((x) => ID_CANAL.test(x)).slice(0, 50) : null;
    const uno = { id, activo, canales };
    if (fuente === 'lista') {
      uno.nombre = textoCorto(f.nome, 120);
      uno.t = /^\d{4}-\d\d-\d\dT[\d:.+\-Z]{1,30}$/.test(String(f.atualizado_em || '')) ? String(f.atualizado_em) : '';
      uno.gatillo = textoCorto(f.gatilho, 20);
      uno.tablero = /^generado desde el tablero/i.test(String(f.descricao || ''));
      uno.editable = f.editavel !== false;
    }
    flujos.push(uno);
  }
  return { fuente, flujos, sesion: estado, lectura: nombres.indexOf('zapdata_flow_get') >= 0 };
}

// Crea UN flujo. Devuelve el identificador y la direccion para revisarlo, o explota con un
// mensaje que se pueda leer sin abrir los registros.
export async function crearFlujo(flujo, env) {
  const token = await leerToken(env);
  if (!token) throw new Error('Falta la credencial de ZapData. Se pega en la pestaña Conexión.');
  const estado = await abrir(token);

  const res = await llamar('tools/call', {
    name: HERRAMIENTA,
    arguments: { flow: flujo },
  }, estado);

  if (res && res.isError) {
    const d = leerResultado(res);
    // El error real puede venir como "texto", como "error", como "message", como una lista
    // de "errors" (asi contesta zapdata_flow_validate) o en ninguno de esos campos. Antes
    // esto se resolvia siempre a "ZapData rechazó el flujo.", que no dice nada: si el pais
    // que fallaba no era el ultimo tocado, no habia forma de saber por que sin adivinar.
    const msg = d.texto || d.error || d.message
      || (Array.isArray(d.errors) && d.errors.length && d.errors.map((x) => (x && x.message) || x).join('; '))
      || (Object.keys(d).length && JSON.stringify(d).slice(0, 300))
      || JSON.stringify(res).slice(0, 300);
    throw new Error(msg);
  }
  const d = leerResultado(res);
  if (!d.flow_id) {
    throw new Error('ZapData no devolvió el identificador del flujo: ' + JSON.stringify(d).slice(0, 200));
  }
  return {
    id: d.flow_id,
    nombre: d.name || flujo.name,
    url: d.review_url || ('https://zapdata.co/inbox/flows/' + d.flow_id),
    bloques: d.node_count || 0,
    huecos: d.placeholders || [],
    avisos: d.warnings || [],
  };
}
