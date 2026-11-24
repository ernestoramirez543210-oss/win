// El copy del guion, editable desde el tablero.
//
// Antes, cambiar una frase del guion era tocar codigo y desplegar. En la practica eso
// significaba que nadie lo cambiaba: se creaba la oferta y despues se iba mensaje por
// mensaje dentro de ZapData, en los dos flujos, cada vez. Esta pestaña existe para que la
// correccion se haga UNA vez y valga para todas las ofertas que se creen despues.
//
// Lo que se guarda aqui es solo lo que se cambio. Si un mensaje nunca se toca, sigue
// saliendo el texto base de functions/_oferta-plantilla.js: asi una mejora del guion base
// llega sola a quien no lo haya pisado, y "Restaurar" es simplemente borrar lo guardado.
//
// NO cambia los flujos que ya existen en ZapData. Esos ya estan escritos; esto es el molde
// de los que vienen.

import { readSession } from '../_sesion.js';
import { COPYS } from '../_oferta-plantilla.js';

export const CLAVE = 'plantilla:copys';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// La lee el generador antes de armar una oferta. Un KV caido no puede dejar sin flujos a
// nadie: sin plantilla guardada se trabaja con los textos base.
export async function leerPlantilla(env) {
  try {
    return (await env.SUITE.get(CLAVE, { type: 'json' })) || {};
  } catch (e) {
    return {};
  }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  let rol = null;
  if (true) {
    rol = env.SESSION_SECRET
      ? await readSession(request.headers.get('Cookie'), env.SESSION_SECRET)
      : null;
    if (!rol) return json(403, { ok: false, error: 'Sesión no válida.' });
  }

  const guardado = await leerPlantilla(env);

  if (request.method === 'GET') {
    return json(200, {
      ok: true,
      copys: Object.keys(COPYS).map((clave) => ({
        clave,
        titulo: COPYS[clave].titulo,
        ayuda: COPYS[clave].ayuda || '',
        base: COPYS[clave].base,
        texto: typeof guardado[clave] === 'string' ? guardado[clave] : COPYS[clave].base,
        editado: typeof guardado[clave] === 'string',
      })),
      tocadoEn: guardado.__tocadoEn || '',
    });
  }

  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const clave = String(body.clave || '');
  if (!COPYS[clave]) return json(400, { ok: false, error: 'Mensaje desconocido: ' + clave });

  const nuevo = Object.assign({}, guardado);

  // Restaurar es quitar lo guardado, no escribir el texto base: asi el mensaje vuelve a
  // seguir al guion base si el guion base mejora mas adelante.
  if (body.accion === 'restaurar') {
    delete nuevo[clave];
  } else {
    const texto = String(body.texto || '');
    if (!texto.trim()) return json(400, { ok: false, error: 'El mensaje no puede quedar vacío.' });
    if (texto.length > 4000) return json(400, { ok: false, error: 'El mensaje es demasiado largo.' });
    nuevo[clave] = texto;
  }

  nuevo.__tocadoEn = new Date().toISOString();
  await env.SUITE.put(CLAVE, JSON.stringify(nuevo));

  return json(200, {
    ok: true,
    clave,
    texto: typeof nuevo[clave] === 'string' ? nuevo[clave] : COPYS[clave].base,
    editado: typeof nuevo[clave] === 'string',
  });
}
