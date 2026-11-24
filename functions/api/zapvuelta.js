// La vuelta desde ZapData despues de que el dueno autoriza el tablero.
//
// Vive en su propia ruta y no colgando de /api/zapmcp con un parametro, porque ZapData
// rechaza registrar una direccion de vuelta que lleve interrogante:
//   "invalid_redirect_uri: redirect_uri nao permitido"
// La direccion tiene que ser una ruta limpia. Esa es toda la razon de que este archivo
// exista aparte.
//
// Esta puerta NO pide sesion, y no es un descuido. Llega como navegacion desde zapdata.co,
// o sea desde otro sitio, y la cookie del tablero es SameSite=Strict: no viaja en ese
// salto. Exigirla haria imposible conectar. Lo que protege la puerta es el `state`: lo
// genero este servidor, vive diez minutos en KV y se quema al usarlo. Sin el state exacto
// no se canjea nada, y el codigo de autorizacion por si solo no sirve de nada.

import { CLAVE, PENDIENTE, canjear, probar } from '../_zapdata.js';

// Se devuelve al dueno a la pagina, con el resultado escrito en la direccion para que la
// pestaña Conexion lo pueda decir sin tener que adivinar.
function aLaPagina(request, params) {
  const u = new URL('/ofertas', new URL(request.url).origin);
  Object.keys(params).forEach((k) => u.searchParams.set(k, params[k]));
  u.hash = 'conexion';
  return new Response(null, { status: 302, headers: { Location: u.toString(), 'Cache-Control': 'no-store' } });
}

export async function onRequestGet(context) {
  const { request, env } = context;
  if (!env.SUITE) return aLaPagina(request, { conexion: 'no', motivo: 'Falta el almacén KV.' });
  const q = new URL(request.url).searchParams;

  if (q.get('error')) {
    return aLaPagina(request, { conexion: 'no', motivo: q.get('error_description') || q.get('error') });
  }

  const pend = await env.SUITE.get(PENDIENTE, { type: 'json' });
  const estado = q.get('state') || '';
  if (!pend || !estado || estado !== pend.estado) {
    return aLaPagina(request, { conexion: 'no', motivo: 'La autorización no coincide con la que empezó aquí. Intenta de nuevo.' });
  }
  await env.SUITE.delete(PENDIENTE);
  if (Date.now() - pend.creadoEn > 10 * 60 * 1000) {
    return aLaPagina(request, { conexion: 'no', motivo: 'La autorización tardó demasiado. Intenta de nuevo.' });
  }
  const code = q.get('code');
  if (!code) return aLaPagina(request, { conexion: 'no', motivo: 'ZapData no devolvió el código.' });

  let cred;
  try { cred = await canjear(pend, code); }
  catch (e) { return aLaPagina(request, { conexion: 'no', motivo: e.message || String(e) }); }

  // Se comprueba de una que el permiso de crear flujos vino de verdad. Guardar una conexion
  // que no puede crear y decir "conectado" es la peor forma de fallar.
  try {
    const r = await probar(cred.token);
    cred.puedeCrear = r.puedeCrear;
    cred.herramientas = r.herramientas;
    cred.probadoEn = new Date().toISOString();
  } catch (e) { cred.puedeCrear = false; cred.herramientas = []; }
  cred.guardadoEn = new Date().toISOString();
  await env.SUITE.put(CLAVE, JSON.stringify(cred));
  return aLaPagina(request, { conexion: cred.puedeCrear ? 'si' : 'parcial' });
}
