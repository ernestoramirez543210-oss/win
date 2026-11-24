// Lo que cuesta crear los anuncios con el editor de videos (APIMart), para verlo junto al costo de los ebooks en /costos-proyectos.
//
//   POST /api/editor-costos { entradas: [ { fecha, tipo, costo, anuncio, producto, etiqueta, task_id } ... ] }   (maximo 40)
//        -> { ok, nuevas, repetidas, sinCosto, rechazadas: [{ i, motivo }], fallidas }
//   GET  /api/editor-costos   -> { ok, llaveConfigurada }                                              solo el dueño (para que la pagina diga si el envio automatico esta activo)
//
// Quien llama:
//   - el dueño, desde /costos-proyectos (boton «Importar costos del editor»: sube el costos.jsonl del editor), con su sesion; o
//   - el editor, en su PC, con la cabecera x-clave-editor (la llave CLAVE_EDITOR; se crea con scripts\Setup-ClaveEditor.ps1): cada imagen y cada clip que genera con APIMart avisa aqui
//     solo. Esa llave SOLO sirve para anotar costos nuevos en el libro: no lee nada, no borra nada. Sin CLAVE_EDITOR puesta contesta 503, nunca abre.
//
// Cada entrada se anota en el MISMO libro de gasto de la suite (functions/_gasto.js), como proveedor `apimart`, con su hora real y una clave determinista: mandar la misma dos veces
// (un reintento, el archivo importado otra vez) no la cuenta doble y no gasta escrituras de KV. Asi el saldo estimado de APIMart de /apis tambien descuenta lo que gasta el editor
// -antes ese gasto no se veia: salia de un PC, no de la suite- y /costos-proyectos agrupa todo por proyecto con el detalle `editor:<producto>|<anuncio>|<tipo>|<etiqueta>`.
// La voz de IA no trae costo desde APIMart: se cuenta como «sin costo» y no se anota (son centavos).
// Desde 2026-11-10 el editor pide APIMart POR EL DASH (/api/editor-apimart), que anota el gasto al pedirlo: las entradas con `via: 'dash'` se cuentan como repetidas y no se escriben.

import { exigirDueno, safeEqual } from '../_sesion.js';
import { registrarGastoUnico } from '../_gasto.js';

const MAX_ENTRADAS = 40;      // una llamada de KV por entrada nueva: de 40 en 40 queda lejos de cualquier tope por invocacion
const MAX_BYTES = 64 * 1024;
const TOPE_USD = 20;          // una sola imagen o clip jamas cuesta esto: un numero mayor es un error o un ataque, no contabilidad
const TTL_DIAS = 1100;        // lo que cuesta CREAR un proyecto se quiere ver años, no los 400 dias del libro normal
const PROVEEDOR = 'apimart';
const TIPOS = ['imagen', 'video', 'voz'];

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});
// Sin controles ni la barra vertical (separa los campos del detalle del libro) y con tope de largo.
const txt = (v, max) => String(v == null ? '' : v).replace(/[\x00-\x1f\x7f|]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);

// La fecha del costos.jsonl del editor es la hora del PC SIN zona («2026-10-14T16:37:40»): se lee como hora de Colombia, la del negocio. El editor nuevo manda la hora en UTC con «Z».
export function aIso(fecha) {
  let s = String(fecha == null ? '' : fecha).trim();
  if (!/^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?$/.test(s)) return null;
  s = s.replace(' ', 'T');
  s = s.replace(/([+-]\d\d)(\d\d)$/, '$1:$2');
  if (!/(Z|[+-]\d\d:\d\d)$/.test(s)) s += '-05:00';
  const t = Date.parse(s);
  if (!isFinite(t)) return null;
  if (t < Date.parse('2024-01-01T00:00:00Z') || t > Date.now() + 36 * 3600 * 1000) return null;   // antes del editor, o del futuro (un reloj mal puesto)
  return new Date(t).toISOString();
}

async function huella(texto) {
  const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(texto));
  return Array.from(new Uint8Array(h)).slice(0, 8).map((x) => x.toString(16).padStart(2, '0')).join('');
}

// Una entrada ya limpia (o el motivo por el que no entra). Pura: la prueba la llama sin KV.
export async function prepararEntrada(e) {
  if (!e || typeof e !== 'object') return { motivo: 'no es un objeto' };
  const tipo = txt(e.tipo, 10).toLowerCase();
  if (TIPOS.indexOf(tipo) === -1) return { motivo: 'tipo' };
  const t = aIso(e.fecha);
  if (!t) return { motivo: 'fecha' };
  const bruto = e.costo;
  if (bruto == null || bruto === '') return { sinCosto: true };
  const usd = Number(bruto);
  if (!isFinite(usd) || usd < 0 || usd > TOPE_USD) return { motivo: 'costo' };
  if (usd === 0) return { sinCosto: true };
  const producto = txt(e.producto, 24) || 'sin-producto';
  const anuncio = txt(e.anuncio, 16) || 'sin-anuncio';
  const etiqueta = txt(e.etiqueta, 24);
  const tarea = txt(e.task_id, 60);
  const id = await huella(tarea ? 'tarea|' + tarea : [producto, anuncio, tipo, etiqueta, t].join('|'));
  const detalle = ('editor:' + producto + '|' + anuncio + '|' + tipo + '|' + etiqueta).slice(0, 80);
  return { t, usd, detalle, id };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  const llave = request.headers.get('x-clave-editor');
  if (request.method === 'GET') {
    if (!(await exigirDueno(request, env))) return json(401, { ok: false, error: 'Solo el dueño' });
    return json(200, { ok: true, llaveConfigurada: !!(env.CLAVE_EDITOR && String(env.CLAVE_EDITOR).length >= 16) });
  }
  if (request.method !== 'POST') return json(405, { ok: false, error: 'POST o GET' });

  if (llave) {
    // El editor del PC: sin sesion, con su propia llave. Sin la variable en Cloudflare no entra nadie (503), y la comparacion es en tiempo constante.
    if (!env.CLAVE_EDITOR || String(env.CLAVE_EDITOR).length < 16) return json(503, { ok: false, error: 'La llave del editor no esta configurada en el servidor (scripts\\Setup-ClaveEditor.ps1)' });
    if (!safeEqual(llave, String(env.CLAVE_EDITOR))) return json(401, { ok: false, error: 'Llave incorrecta' });
  } else if (!(await exigirDueno(request, env))) {
    return json(401, { ok: false, error: 'Entra como dueño' });
  }

  let cuerpo;
  try {
    const crudo = await request.text();
    if (crudo.length > MAX_BYTES) return json(413, { ok: false, error: 'Demasiado grande: manda de ' + MAX_ENTRADAS + ' en ' + MAX_ENTRADAS });
    cuerpo = JSON.parse(crudo);
  } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const entradas = cuerpo && Array.isArray(cuerpo.entradas) ? cuerpo.entradas : null;
  if (!entradas) return json(400, { ok: false, error: 'Falta "entradas" (una lista)' });
  if (entradas.length > MAX_ENTRADAS) return json(413, { ok: false, error: 'Maximo ' + MAX_ENTRADAS + ' entradas por envio' });

  let nuevas = 0, repetidas = 0, sinCosto = 0, fallidas = 0;
  const rechazadas = [];
  for (let i = 0; i < entradas.length; i++) {
    // Lo que el editor pidio POR EL DASH (/api/editor-apimart, desde 2026-11-10) ya lo anoto el dash al pedirlo, con su propia clave: su costos.jsonl lo marca «via: dash». Si llega aqui
    // (un editor que lo manda igual, o «Importar costos del editor» con el archivo entero) se cuenta como repetido y NO se escribe: contarlo otra vez seria cobrarlo doble en el libro.
    if (entradas[i] && typeof entradas[i] === 'object' && entradas[i].via === 'dash') { repetidas++; continue; }
    const p = await prepararEntrada(entradas[i]);
    if (p.sinCosto) { sinCosto++; continue; }
    if (p.motivo) { rechazadas.push({ i, motivo: p.motivo }); continue; }
    const r = await registrarGastoUnico(env, PROVEEDOR, p.usd, p.detalle, p.t, p.id, TTL_DIAS);
    if (r === 'nuevo') nuevas++;
    else if (r === 'repetido') repetidas++;
    else if (r === 'fallo') fallidas++;
    else rechazadas.push({ i, motivo: 'rechazada' });
  }
  // fallidas = KV rechazo la escritura (tope diario): quien manda las deja pendientes y reintenta.
  return json(200, { ok: fallidas === 0, nuevas, repetidas, sinCosto, rechazadas, fallidas });
}
