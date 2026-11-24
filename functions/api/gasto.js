// Recibe de una sola vez lo que costo crear un ebook y lo anota en el libro de gasto.
//
// El creador de ebooks junta el gasto de cada pagina en el navegador (cada respuesta de
// /api/ebook-* trae el campo "gasto" cuando el pedido lleva gastoLote:true) y lo manda aqui
// al terminar: una escritura de KV por proveedor en vez de una por llamada. Un ebook de 30
// paginas son ~90 llamadas; el plan gratuito de KV permite 1.000 escrituras al dia.
//
//   POST /api/gasto  { gasto: { together: 0.052, gemini: 0.011 }, detalle: "titulo del ebook" }
//        -> { ok, anotados, esperados, fallidos }   (fallidos = proveedores que KV rechazo: reintentar)
//
// Todo el equipo: desde el 2026-10-15 todos crean ebooks, y el gasto se anota por proveedor sin decir quien lo hizo (la contrasena es una por rol).

import { exigirAlguno, EQUIPO } from '../_sesion.js';
import { registrarGasto } from '../_gasto.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'POST only' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirAlguno(request, env, EQUIPO))) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const gasto = body && typeof body.gasto === 'object' && body.gasto ? body.gasto : {};
  const detalle = String((body && body.detalle) || 'ebook').slice(0, 70);

  // Un tope por proveedor y por envio: es contabilidad que el propio navegador reporta, y un
  // numero absurdo no debe poder arruinar el saldo estimado.
  const validos = Object.keys(gasto).slice(0, 8).filter((t) => {
    const usd = Number(gasto[t]);
    return /^[a-z0-9_-]{2,30}$/.test(t) && isFinite(usd) && usd > 0 && usd <= 50;
  });
  const fallidos = [];
  for (const t of validos) {
    if (!(await registrarGasto(env, t, Number(gasto[t]), 'ebook:' + detalle))) fallidos.push(t);
  }
  // fallidos = los proveedores que KV rechazo (tope diario): el navegador los vuelve a poner en
  // pendientes y reintenta, en vez de perder ese gasto.
  return json(200, { ok: true, anotados: validos.length - fallidos.length, esperados: validos.length, fallidos });
}
