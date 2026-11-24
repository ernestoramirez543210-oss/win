// El libro de gasto: lo que la suite le paga a cada proveedor de IA, anotado al momento de
// gastarlo.
//
// Existe para saber cuando recargar. Together, OpenAI, Anthropic y Gemini no dejan consultar el
// saldo por API, asi que la unica forma de estimarlo es contar lo que se gasta aqui y restarlo
// del ultimo saldo que alguien leyo en el panel del proveedor (/api/saldos).
//
// Una clave de KV POR GASTO, con el monto en la metadata, y no un contador por dia. Un contador
// se lee, se suma y se escribe, y con varias paginas generandose a la vez dos escrituras se
// pisan: se perdia una parte del gasto sin ningun error. Una clave nueva por evento no tiene
// nada que pisar, y list() devuelve la metadata sin leer cada valor, asi que sumar un mes
// cuesta una consulta por cada mil gastos.
//
// Solo cuenta lo que pasa por la suite -ebooks, mockups, textos del agente-. Lo que se gasta
// directo en el panel del proveedor no aparece, y por eso el saldo se re-ancla a mano de vez
// en cuando.

const PREFIJO = 'gasto:';
export const DIAS_GUARDADO = 400;     // lo exporta para la prueba del cuadre de APIMart: sus tramos no pueden mirar mas atras de lo que el libro guarda

// proveedor = el "tipo" del proveedor (together, openai, anthropic, gemini...): el saldo es de
// la cuenta, no del modelo.
export async function registrarGasto(env, proveedor, usd, detalle) {
  if (!env || !env.SUITE || !proveedor || !(usd > 0)) return false;
  try {
    const t = new Date().toISOString();
    await env.SUITE.put(PREFIJO + t + ':' + Math.random().toString(36).slice(2, 7), '1', {
      metadata: { p: String(proveedor), u: Math.round(usd * 1e6) / 1e6, d: String(detalle || '').slice(0, 80) },
      expirationTtl: DIAS_GUARDADO * 24 * 3600,
    });
    return true;
  } catch { return false; /* anotar el gasto nunca debe tumbar la generacion que ya salio bien */ }
}

// MODO LOTE. El creador de ebooks hacia tres anotaciones por pagina -el texto de la foto, la
// maquetacion y la imagen-: unas 90 escrituras de KV por ebook de 30 paginas, de las 1.000 que
// permite al dia el plan gratuito. Si el navegador manda gastoLote:true, el endpoint NO escribe:
// junta lo gastado en un colector, lo devuelve en la respuesta (campo "gasto") y el navegador
// lo suma -asi ve cuanto cuesta de verdad el ebook- y lo manda UNA vez a /api/gasto.
// Resume un colector [{p,u,d}] por proveedor: { together: 0.0521, gemini: 0.012 }.
export function resumirGasto(col) {
  const r = {};
  (col || []).forEach((e) => { r[e.p] = (r[e.p] || 0) + (Number(e.u) || 0); });
  Object.keys(r).forEach((p) => { r[p] = Math.round(r[p] * 1e6) / 1e6; });
  return r;
}

// Suma el gasto por proveedor desde `desdeISO` (inclusive). Devuelve { proveedor: usd }.
export async function gastoDesde(env, desdeISO) {
  const total = {};
  let cursor;
  do {
    const l = await env.SUITE.list({ prefix: PREFIJO, cursor, limit: 1000 });
    for (const k of l.keys) {
      const t = k.name.slice(PREFIJO.length, PREFIJO.length + 24);
      if (desdeISO && t < desdeISO) continue;
      const m = k.metadata || {};
      if (!m.p) continue;
      total[m.p] = (total[m.p] || 0) + (Number(m.u) || 0);
    }
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor);
  return total;
}

// Todo lo que /api/saldos necesita en UNA sola pasada por el libro: por proveedor, el gasto de
// hoy, de los ultimos 7 y 30 dias, y desde el momento en que se anoto su ultimo saldo (`ancla`
// = { proveedor: { t: ISO } }). Una pasada y no cuatro porque cada pasada es una consulta de
// KV por cada mil gastos.
export async function resumenGasto(env, ancla) {
  const ahora = Date.now();
  const cortes = {
    hoy: inicioDeHoyISO(ahora),
    d7: new Date(ahora - 7 * 864e5).toISOString(),
    d30: new Date(ahora - 30 * 864e5).toISOString(),
  };
  const res = {};
  let cursor;
  do {
    const l = await env.SUITE.list({ prefix: PREFIJO, cursor, limit: 1000 });
    for (const k of l.keys) {
      const m = k.metadata || {};
      if (!m.p) continue;
      const t = k.name.slice(PREFIJO.length, PREFIJO.length + 24);
      const u = Number(m.u) || 0;
      const r = res[m.p] || (res[m.p] = { hoy: 0, d7: 0, d30: 0, desdeAncla: 0 });
      if (t >= cortes.hoy) r.hoy += u;
      if (t >= cortes.d7) r.d7 += u;
      if (t >= cortes.d30) r.d30 += u;
      const a = ancla && ancla[m.p];
      if (a && a.t && t >= a.t) r.desdeAncla += u;
    }
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor);
  return res;
}

// Un gasto que llega de FUERA de la suite, con su hora REAL y una clave determinista: el editor de anuncios (un PC con Claude) manda cada imagen y cada clip que genera con
// APIMart, y mandar dos veces lo mismo -un reintento, el archivo de costos importado otra vez- no debe contarlo doble. La clave es `gasto:<hora>:e<huella>`: igual que las demas
// (la hora va delante, que es lo que leen resumenGasto y gastoDesde) pero con una huella en vez de un azar, asi que repetirla cae en la misma clave y se salta SIN escribir
// (las escrituras de KV son lo escaso: 1.000 al dia). Devuelve 'nuevo' | 'repetido' | 'rechazado' | 'fallo'.
// `ttlDias`: el libro normal guarda 400 dias; lo que cuesta CREAR un proyecto se quiere ver mas tiempo, y una clave solo de metadata pesa unos 300 bytes.
export async function registrarGastoUnico(env, proveedor, usd, detalle, tISO, huella, ttlDias) {
  if (!env || !env.SUITE || !proveedor || !(usd > 0) || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(String(tISO)) || !/^[0-9a-f]{8,32}$/.test(String(huella))) return 'rechazado';
  try {
    const clave = PREFIJO + tISO + ':e' + huella;
    const previo = await env.SUITE.getWithMetadata(clave);
    if (previo && previo.value != null) return 'repetido';
    await env.SUITE.put(clave, '1', {
      metadata: { p: String(proveedor), u: Math.round(usd * 1e6) / 1e6, d: String(detalle || '').slice(0, 80) },
      expirationTtl: Math.max(1, Math.min(Number(ttlDias) || DIAS_GUARDADO, 3650)) * 24 * 3600,
    });
    return 'nuevo';
  } catch { return 'fallo'; }
}

// CORREGIR un gasto anotado con registrarGastoUnico: misma clave determinista (`gasto:<hora>:e<huella>`), pero escribe SIEMPRE (put incondicional), porque aqui se sabe que el monto cambio.
// La usa la puerta de APIMart del editor (functions/_editor-apimart.js): al pedir una imagen o un clip anota el ESTIMADO, y cuando APIMart dice lo que cobro de verdad lo fija con el real;
// un trabajo que fallo sin cobrar (usd 0) BORRA la anotacion. Devuelve 'fijado' | 'borrado' | 'rechazado' | 'fallo'. No toca nada mas del libro.
export async function fijarGastoUnico(env, proveedor, usd, detalle, tISO, huella, ttlDias) {
  const monto = Number(usd);
  if (!env || !env.SUITE || !proveedor || !Number.isFinite(monto) || monto < 0 || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(String(tISO)) || !/^[0-9a-f]{8,32}$/.test(String(huella))) return 'rechazado';
  const clave = PREFIJO + tISO + ':e' + huella;
  try {
    if (monto === 0) { await env.SUITE.delete(clave); return 'borrado'; }
    await env.SUITE.put(clave, '1', {
      metadata: { p: String(proveedor), u: Math.round(monto * 1e6) / 1e6, d: String(detalle || '').slice(0, 80) },
      expirationTtl: Math.max(1, Math.min(Number(ttlDias) || DIAS_GUARDADO, 3650)) * 24 * 3600,
    });
    return 'fijado';
  } catch { return 'fallo'; }
}

// Las filas de UN dia (UTC) del libro: [{ t, p, u, d }], con UN list por cada mil gastos de ese dia (tope 3 paginas). La clave empieza por la hora, asi que el prefijo `gasto:<AAAA-MM-DD>`
// trae solo ese dia sin recorrer el libro entero. La usa el tope diario del editor (functions/_editor-apimart.js). Si KV falla, LANZA: quien la usa decide (el tope falla cerrado).
// OJO: list() puede tardar hasta un minuto en ver lo recien escrito, asi que un tope hecho con esto es BLANDO ante una rafaga.
export async function filasDelDia(env, diaUTC) {
  if (!/^\d{4}-\d\d-\d\d$/.test(String(diaUTC))) return { filas: [], completo: true };
  const filas = [];
  let cursor;
  for (let v = 0; v < 3; v++) {
    const l = await env.SUITE.list({ prefix: PREFIJO + diaUTC, cursor, limit: 1000 });
    for (const k of l.keys) {
      const m = k.metadata || {};
      if (!m.p) continue;
      filas.push({ t: k.name.slice(PREFIJO.length, PREFIJO.length + 24), p: String(m.p), u: Number(m.u) || 0, d: String(m.d || '') });
    }
    if (l.list_complete || !l.cursor) return { filas, completo: true };
    cursor = l.cursor;
  }
  return { filas, completo: false };
}

// TODO el libro, sin sumar: [{ t, p, u, d }] (hora ISO, proveedor, dolares, detalle). Lo usa el informe de costo por proyecto, que agrupa por el detalle. Una pasada de list por cada
// mil gastos, con tope de 12 paginas (12.000 gastos): si se llega al tope, `completo` sale false y el informe lo dice en vez de mostrar una suma que parece entera.
export async function leerGasto(env) {
  const filas = [];
  let cursor, completo = true;
  for (let v = 0; v < 12; v++) {
    const l = await env.SUITE.list({ prefix: PREFIJO, cursor, limit: 1000 });
    for (const k of l.keys) {
      const m = k.metadata || {};
      if (!m.p) continue;
      filas.push({ t: k.name.slice(PREFIJO.length, PREFIJO.length + 24), p: String(m.p), u: Number(m.u) || 0, d: String(m.d || '') });
    }
    if (l.list_complete || !l.cursor) return { filas, completo: true };
    cursor = l.cursor;
    completo = false;
  }
  return { filas, completo };
}

// Medianoche de hoy en Colombia (UTC-5) como texto ISO en UTC, para comparar con las claves.
export function inicioDeHoyISO(ahora) {
  const b = new Date((ahora || Date.now()) - 5 * 3600 * 1000);
  return new Date(Date.UTC(b.getUTCFullYear(), b.getUTCMonth(), b.getUTCDate(), 5, 0, 0)).toISOString();
}
