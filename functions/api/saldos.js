// Saldo estimado de cada API, para saber cuando recargar.
//
// Together, OpenAI, Anthropic y Gemini NO dejan consultar el saldo con la llave de la API (solo
// lo muestra su panel), asi que no hay un numero real que traer. Lo que si se puede es:
//   1. anotar aqui el saldo que se ve en el panel del proveedor -queda con fecha y hora-, y
//   2. restarle todo lo que la suite gasto desde ese momento (functions/_gasto.js).
// Cada vez que se mira el panel y se anota otra vez, el estimado se corrige solo, y el error se
// queda en lo unico que no pasa por la suite: gasto hecho directo en el panel del proveedor.
//
//   GET  /api/saldos                                  -> un renglon por proveedor
//   POST /api/saldos {accion:'saldo',  tipo, usd}     -> anota el saldo que se ve en su panel
//   POST /api/saldos {accion:'umbral', tipo, usd}     -> avisar cuando el saldo baje de esto
//
// Solo el dueno: es plata.

import { exigirDueno } from '../_sesion.js';
import { resumenGasto } from '../_gasto.js';

const ANCLA = 'saldo:ancla';
const UMBRAL = 'saldo:umbral';
const UMBRAL_POR_DEFECTO = 5;   // USD
const DIAS_DE_AVISO = 3;        // tambien se avisa si al ritmo de la ultima semana alcanza para menos

const NOMBRES = {
  together: 'Together AI', openai: 'OpenAI', anthropic: 'Anthropic (Claude)', gemini: 'Google Gemini',
  replicate: 'Replicate', apimart: 'APIMart', compatible: 'Otra API (URL propia)',
};
const PANELES = {
  together: 'https://api.together.ai/settings/billing',
  openai: 'https://platform.openai.com/settings/organization/billing/overview',
  anthropic: 'https://console.anthropic.com/settings/billing',
  gemini: 'https://console.cloud.google.com/billing',
  replicate: 'https://replicate.com/account/billing',
  apimart: 'https://apimart.ai/keys',
};

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

async function leerJson(env, clave) {
  try { return (await env.SUITE.get(clave, { type: 'json' })) || {}; } catch { return {}; }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirDueno(request, env))) return json(401, { ok: false, error: 'Solo el dueño ve los saldos' });

  if (request.method === 'POST') {
    let b;
    try { b = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
    const tipo = String(b.tipo || '').toLowerCase();
    const usd = Number(b.usd);
    if (!/^[a-z0-9_-]{2,30}$/.test(tipo)) return json(400, { ok: false, error: 'Proveedor invalido' });
    if (!isFinite(usd) || usd < 0 || usd > 100000) return json(400, { ok: false, error: 'El monto tiene que ser un número entre 0 y 100.000' });

    // Si KV rechaza la escritura se devuelve el motivo, no una pagina HTML de error de Cloudflare.
    const guardar = async (clave, valor) => {
      try { await env.SUITE.put(clave, JSON.stringify(valor)); return json(200, { ok: true }); }
      catch (e) { return json(502, { ok: false, error: 'No se pudo guardar: ' + String((e && e.message) || e).slice(0, 200) }); }
    };
    if (b.accion === 'saldo') {
      const a = await leerJson(env, ANCLA);
      a[tipo] = { usd: Math.round(usd * 100) / 100, t: new Date().toISOString() };
      return guardar(ANCLA, a);
    }
    if (b.accion === 'umbral') {
      const u = await leerJson(env, UMBRAL);
      u[tipo] = Math.round(usd * 100) / 100;
      return guardar(UMBRAL, u);
    }
    return json(400, { ok: false, error: 'Accion desconocida' });
  }
  if (request.method !== 'GET') return json(405, { ok: false, error: 'Method not allowed' });

  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const nombres = {};
  const tipos = new Set();
  (ajustes.proveedores || []).forEach((p) => { if (p.tipo) { tipos.add(p.tipo); nombres[p.tipo] = nombres[p.tipo] || NOMBRES[p.tipo] || p.nombre || p.tipo; } });
  if (ajustes.imagenApi && ajustes.imagenApi.tipo) tipos.add(ajustes.imagenApi.tipo);

  const ancla = await leerJson(env, ANCLA);
  const umbrales = await leerJson(env, UMBRAL);
  const gasto = await resumenGasto(env, ancla);
  Object.keys(ancla).forEach((t) => tipos.add(t));
  Object.keys(gasto).forEach((t) => tipos.add(t));

  const proveedores = [...tipos].sort().map((tipo) => {
    const g = gasto[tipo] || { hoy: 0, d7: 0, d30: 0, desdeAncla: 0 };
    const a = ancla[tipo] || null;
    const umbral = umbrales[tipo] != null ? umbrales[tipo] : UMBRAL_POR_DEFECTO;
    const ritmo = g.d7 / 7;
    const saldo = a ? Math.round((a.usd - g.desdeAncla) * 100) / 100 : null;
    const dias = (saldo != null && ritmo > 0) ? Math.floor(Math.max(0, saldo) / ritmo) : null;
    let estado = 'ok';
    if (!a) estado = 'sinanotar';
    else if (saldo <= 0) estado = 'agotado';
    else if (saldo <= umbral || (dias != null && dias <= DIAS_DE_AVISO)) estado = 'bajo';
    return {
      tipo, nombre: nombres[tipo] || NOMBRES[tipo] || tipo, panel: PANELES[tipo] || '',
      ancla: a, gastadoDesdeAncla: Math.round(g.desdeAncla * 1000) / 1000,
      hoy: Math.round(g.hoy * 1000) / 1000, d7: Math.round(g.d7 * 1000) / 1000, d30: Math.round(g.d30 * 1000) / 1000,
      ritmoDiario: Math.round(ritmo * 1000) / 1000, saldo, diasRestantes: dias, umbral, estado,
    };
  });
  return json(200, { ok: true, proveedores });
}
