// POST /api/apimart-cuadre: le pregunta a APIMart cuanto lleva consumido la cuenta y lo guarda para el cuadre de /costos-proyectos (functions/_apimart-cuadre.js).
//
// Quien llama: el refresco de GitHub, cada media hora, con la llave CLAVE_REGLAS en la cabecera `x-clave-reglas` (la misma que ya usa /api/reglas). Sin esa llave no entra nadie, ni con sesion.
// No recibe datos, no devuelve el saldo ni el consumo y solo escribe la clave del cuadre. Si la ultima foto tiene menos de 20 minutos no hace nada (ni pregunta a APIMart ni escribe):
// asi quien tuviera la llave no puede gastar las escrituras diarias de KV de la suite llamando sin parar (como mucho, unas 72 fotos al dia).
// Abrir /costos-proyectos como dueno tambien saca la foto si la ultima tiene mas de 20 minutos; esto es lo que la mantiene al dia aunque nadie abra la pagina.

import { safeEqual } from '../_sesion.js';
import { fotoFresca, leerCuadre, tomarFoto } from '../_apimart-cuadre.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Solo POST' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  const llave = request.headers.get('x-clave-reglas') || '';
  if (!llave || !env.CLAVE_REGLAS || !safeEqual(llave, String(env.CLAVE_REGLAS))) return json(401, { ok: false, error: 'Llave incorrecta' });
  let previo;
  try { previo = await leerCuadre(env); } catch { return json(502, { ok: false, motivo: 'sin-leer' }); }
  if (fotoFresca(previo)) return json(200, { ok: true, motivo: 'fresca' });
  const r = await tomarFoto(env, previo);
  return json(r.ok ? 200 : 502, { ok: r.ok, motivo: r.motivo || '' });
}
