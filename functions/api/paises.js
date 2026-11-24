// Configurar un pais: precios, Pixel, flujo de pagos y datos de pago.
//
// SOLO EL DUENO. No esta en la lista de ningun rol del middleware, asi que solo pasa quien
// entra con la clave de dueno, y aqui se vuelve a comprobar. Aqui viven numeros de cuenta
// reales, y quien crea ofertas no necesita verlos: /api/zapflow los lee por su cuenta, del
// lado del servidor, y los mete en el mensaje sin que pasen por el navegador de nadie.
//
// Lo compartido -las semillas, el formato del precio, la lectura- vive en
// functions/_paises.js, porque el generador tambien lo usa.

import { readSession } from '../_sesion.js';
import { SEMILLAS, CAMPOS, PREFIJO, completo, leerPaises } from '../_paises.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  if (true) {
    const rol = env.SESSION_SECRET
      ? await readSession(request.headers.get('Cookie'), env.SESSION_SECRET)
      : null;
    if (rol !== 'owner') return json(403, { ok: false, error: 'Solo el dueño configura los países.' });
  }

  if (request.method === 'GET') {
    const paises = await leerPaises(env);
    return json(200, { ok: true, paises: paises.map((p) => Object.assign({ completo: completo(p) }, p)) });
  }
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const semilla = SEMILLAS.find((s) => s.id === body.id);
  if (!semilla) return json(400, { ok: false, error: 'País desconocido: ' + body.id });

  const actual = (await env.SUITE.get(PREFIJO + semilla.id, { type: 'json' })) || {};
  const nuevo = Object.assign({ id: semilla.id }, semilla, actual);
  CAMPOS.forEach((c) => { if (body[c] !== undefined) nuevo[c] = String(body[c]); });
  // Sin {v} el precio saldria sin numero, y saldria asi en cada mensaje de cada oferta de
  // ese pais hasta que alguien lo notara leyendo un chat.
  if (String(nuevo.formato || '').indexOf('{v}') < 0) {
    return json(400, { ok: false, error: 'El formato del precio tiene que llevar {v} donde va el número.' });
  }
  nuevo.tocadoEn = new Date().toISOString();
  await env.SUITE.put(PREFIJO + semilla.id, JSON.stringify(nuevo));
  return json(200, { ok: true, pais: Object.assign({ completo: completo(nuevo) }, nuevo) });
}
