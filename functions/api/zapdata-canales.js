// Las conexiones de WhatsApp que ZapData tiene AHORA, todas, conectadas o no. Las pide /numeros al abrirse y /oficina cada 2 minutos; templates/numeros-vivo.js las junta con la lista que armo el build.
// El ejecutor de las reglas las usa tambien (canalesAhora), para no encender ni subir el gasto de una campaña cuyo numero esta caido.
//
//   GET /api/zapdata-canales  -> { ok:true, ahora, canales:[{ id, nombre, estado:'connected'|'disconnected', flujos, tel4, canal }] }
//                             -> { ok:false, ahora, motivo:'sin_conexion'|'sin_permiso'|'vencida'|'red'|'otro', mensaje, detalle? }   (el `detalle` tecnico solo lo ve el dueno)
//
// Por que existe (2026-10-27): el dash decia 12 numeros y ZapData tenia 20. La lista del build sale de la API publica de ZapData, que SOLO trae las conectadas, y de un archivo del repo: una conexion caida
// o nueva y nunca conectada no existia para las paginas. Aqui se le pregunta a ZapData por TODAS (zapdata_channel_list, con la conexion OAuth del tablero, functions/_zapdata.js) y las paginas ponen eso
// encima de su lista: el estado real, los numeros que ya no existen y los que el dash no conoce (que se AVISAN, nunca se descartan en silencio).
//
// Quien puede: todo el equipo (dueno, trafficker, editor y videos), lo mismo que ya ve /numeros y /oficina; los socios de pais no. Falla cerrado aunque el login este apagado (exigirLecturaEquipo).
// Lo que sale NO lleva telefonos: solo el nombre de la conexion, su estado, cuantos flujos tiene activos y las ULTIMAS 4 cifras del telefono (para reconocer una conexion nueva sin nombre). Solo
// cuenta WhatsApp (canal 'unofficial' = QR y 'official' = API oficial): Instagram, Messenger y Telegram no son «WPP #N».
// Cache: en la memoria del aislado, 2 min si salio bien y 20 s si fallo (asi se recupera pronto). Un solo vuelo: si ya hay una consulta a ZapData en marcha, los demas pedidos esperan ESA (cada consulta
// renueva si esta por vencerse, una sola vez por aislado: ver functions/_zapdata.js).
// Lo que viene de ZapData es TEXTO de terceros: el nombre se limpia aqui (sin caracteres de control, sin telefonos completos) y las paginas lo escapan al pintarlo.

import { exigirLecturaEquipo } from '../_sesion.js';
import { listarCanales, conPlazo, PLAZOS, ES_LIMITE } from '../_zapdata.js';

const json = (s, o) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
const txt = (v, n) => String(v == null ? '' : v).replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
const VIDA_OK_MS = 120000;
const VIDA_MAL_MS = 20000;
const VIDA_LIMITE_MS = 30 * 60 * 1000;   // ZapData llego a su tope del dia: preguntar cada 20 s solo gastaria intentos (tambien cuentan)
let memoria = null;   // { t, cuerpo } por aislado
let enCurso = null;   // { t, p }: la consulta a ZapData que esta en marcha ahora en este aislado, si hay una (PLAZOS.consultaMs: lo que espera un pedido a la de otro antes de hacer la suya)

const MENSAJES = {
  sin_conexion: 'La conexión del dash con ZapData no está hecha: el dueño la hace en Flujos ZapData → Conexión.',
  sin_permiso: 'La conexión del dash con ZapData no deja leer la lista de números: el dueño tiene que reconectarla desde su PC pidiendo un permiso más (el resultado se pega en Flujos ZapData → Conexión).',
  vencida: 'Se venció el permiso de ZapData: el dueño vuelve a conectar en Flujos ZapData → Conexión.',
  red: 'No pude preguntarle a ZapData en este momento; se intenta de nuevo en un rato y mientras tanto se usa la lista del último refresco.',
  otro: 'ZapData no contestó como se esperaba; se usa la lista del último refresco.',
  limite: 'ZapData llegó a su tope de consultas de hoy; se usa la lista del último refresco y se vuelve a preguntar más tarde.',
};

// Un telefono completo (7 o mas cifras, con espacios, guiones, parentesis o «+») no sale: queda «…» y sus ultimas 4 cifras, como `tel4`. Una conexion sin nombre de perfil puede traer su telefono de nombre.
// El `(?<![\d#])` evita que se coma el «#N» de «WPP #12 (2026-10)» o de «WPP #21 27.10.2026»: una racha de cifras no empieza justo despues de «#» ni en medio de otra racha (a cambio, un telefono
// pegado directamente a un «#» no se tapa: no es la forma en que se nombran las conexiones).
export const sinTelefono = (s) => String(s).replace(/(?<![\d#])[(+]{0,2}\d(?:[\s().-]{0,2}\d){6,}/g, (m) => '…' + m.replace(/\D/g, '').slice(-4));

// Lo que viaja al navegador: nada de telefonos completos ni de campos que no se usan. `lista` es el `canais` de zapdata_channel_list. El tope de 100 se cuenta DESPUES de quitar lo que no es WhatsApp.
export function limpiarCanales(lista) {
  const out = [];
  for (const c of (Array.isArray(lista) ? lista : []).slice(0, 500)) {
    if (out.length >= 100) break;
    if (!c || typeof c !== 'object') continue;
    const id = String(c.channel_id || c.id || '').trim();
    if (!/^[0-9a-f-]{16,64}$/i.test(id)) continue;
    const canal = String(c.canal || 'unofficial').toLowerCase();
    if (canal !== 'unofficial' && canal !== 'official') continue;
    const tel = String(c.telefone || '').replace(/\D/g, '');
    out.push({
      id: id.toLowerCase(),
      nombre: sinTelefono(txt(c.nome || c.nombre || c.name, 400)).slice(0, 80),
      estado: c.conectado === true || String(c.status || '').toLowerCase() === 'connected' ? 'connected' : 'disconnected',
      flujos: Math.max(0, Math.min(999, Math.round(Number(c.fluxos_ativos) || 0))),
      tel4: tel.length >= 4 ? tel.slice(-4) : '',
      canal,
    });
  }
  return out;
}

export function clasificar(e) {
  const m = String((e && e.message) || e || '');
  if (e && e.codigo === 'sin_conexion') return 'sin_conexion';
  if (e && e.codigo === 'sin_permiso') return 'sin_permiso';
  // ZapData tiene topes diarios por credencial (2026-11-06: «Limite de uso atingido para hoje (107/600)»): no es un permiso que falte ni algo que se arregle reconectando
  if ((e && e.codigo === 'limite') || ES_LIMITE.test(m)) return 'limite';
  if (/no acept[oó] la credencial|se venci[oó]|venci/i.test(m)) return 'vencida';
  if (/permiss|scope|forbidden|insufficient|unknown tool|tool not found|no such tool/i.test(m)) return 'sin_permiso';
  if (/fetch failed|network|timed? ?out|abort|econn|failed to fetch/i.test(m)) return 'red';
  return 'otro';
}

async function consultar(env) {
  const ahora = new Date().toISOString();
  try {
    const canales = limpiarCanales(await listarCanales(env));
    // Una cuenta sin ninguna conexion no existe: si ZapData contesta 0 es que algo salio raro, y las paginas se quedan con la lista del build (no borrar todo por una respuesta vacia).
    if (!canales.length) return { ok: false, ahora, motivo: 'otro', mensaje: MENSAJES.otro, detalle: 'ZapData devolvio 0 conexiones de WhatsApp' };
    return { ok: true, ahora, canales };
  } catch (e) {
    const motivo = clasificar(e);
    return { ok: false, ahora, motivo, mensaje: MENSAJES[motivo] || MENSAJES.otro, detalle: sinTelefono(txt((e && e.message) || e, 160)) };
  }
}

// Para las pruebas: vacia la memoria del aislado.
export function olvidar() { memoria = null; enCurso = null; }

// La respuesta de ahora: la del cache si todavia vale; si no, UNA consulta a ZapData que comparten todos los pedidos que lleguen mientras tanto. Si esa consulta lleva demasiado (el pedido que la hacia se
// cancelo a medias: en Workers lo pendiente de un pedido cancelado no termina nunca) se da por perdida: los que esperaban vuelven a mirar el cache y a entrar al vuelo (el primero abre uno nuevo y los
// demas lo esperan, no una consulta cada uno); solo tras 3 vueltas sin respuesta cada uno pregunta por su cuenta.
async function cuerpoActual(env) {
  for (let intento = 0; intento < 3; intento++) {
    const ahora = Date.now();
    if (memoria && ahora - memoria.t <= (memoria.cuerpo.ok ? VIDA_OK_MS : (memoria.cuerpo.motivo === 'limite' ? VIDA_LIMITE_MS : VIDA_MAL_MS))) return memoria.cuerpo;
    const viva = enCurso && ahora - enCurso.t < PLAZOS.consultaMs ? enCurso : null;
    let mia = null;
    if (!viva) {
      mia = { t: ahora, p: null };
      mia.p = consultar(env).then((cuerpo) => { memoria = { t: Date.now(), cuerpo }; return cuerpo; }).finally(() => { if (enCurso === mia) enCurso = null; });
      enCurso = mia;
    }
    try { return await (mia ? mia.p : conPlazo(viva.p, PLAZOS.consultaMs)); } catch (e) { /* la espera se paso: se vuelve a mirar el cache y el vuelo */ }
  }
  const cuerpo = await consultar(env);
  memoria = { t: Date.now(), cuerpo };
  return cuerpo;
}
// Lo mismo para el ejecutor de las reglas (functions/api/reglas-ejecutar.js), que le pregunta a ZapData si el numero de una campaña esta conectado antes de subir su gasto: misma cache y mismo vuelo.
export const canalesAhora = cuerpoActual;

export async function onRequestGet(context) {
  const { request, env } = context;
  const rol = await exigirLecturaEquipo(request, env);
  if (!rol) return json(401, { ok: false, error: 'Solo el equipo ve los números de WhatsApp' });
  const c = await cuerpoActual(env);
  if (c.ok) return json(200, c);
  const { detalle, ...sinDetalle } = c;
  return json(200, rol === 'owner' ? c : sinDetalle);
}
