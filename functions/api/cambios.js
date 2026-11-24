// Los cambios de estado y presupuesto hechos desde el dash en las ultimas horas, para que el dash los aplique encima del snapshot.
//
//   GET /api/cambios -> { ok, ahora, cambios: [ { id, type, accountId, status?, efectivo?, daily?, lifetime?, at } ] }
//
// /api/toggle-ad los guarda (clave cambio:<tipo>:<id>, 3 dias) despues de VERIFICAR en Facebook como quedo cada cosa. El snapshot del dash se arma una vez por hora:
// sin esto, cambiar un presupuesto o pausar una campaña y recargar mostraba el valor viejo hasta la siguiente construccion. El dash solo aplica los cambios mas
// nuevos que su snapshot. Los lee todo el equipo (dueño, trafficker, editor y videos: desde el 2026-10-15 todos ven el dash combinado); los socios de pais no llegan aqui.

import { exigirAlguno, EQUIPO } from '../_sesion.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequestGet(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirAlguno(request, env, EQUIPO))) return json(401, { ok: false, error: 'No autenticado' });
  let cambios = [];
  try {
    const lista = await env.SUITE.list({ prefix: 'cambio:', limit: 500 });
    cambios = (await Promise.all(lista.keys.map((k) => env.SUITE.get(k.name, { type: 'json' })))).filter(Boolean);
  } catch { cambios = []; }
  return json(200, { ok: true, ahora: new Date().toISOString(), cambios });
}
