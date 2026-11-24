// Conectar un editor de anuncios con el dash sin pasar la llave a mano (2026-11-10).
//
//   GET /api/editor-conexion?estado=1   -> { ok, puede:true, configurada }              la pagina /editor-anuncios pregunta si muestra la tarjeta «Conectar este editor»
//   GET /api/editor-conexion            -> { ok, lineas:'DASH_URL=<origen>\nDASH_CLAVE_EDITOR=<llave>\n' }   lo que va en el .env del editor (archivo conexion-editor.txt)
//
// Desde que el editor pide APIMart por el dash (/api/editor-apimart), un PC nuevo solo necesita esas dos lineas: ninguna llave de APIMart. El instalador del editor busca conexion-editor.txt junto
// a el y en Descargas, lo copia al .env, quita la linea vieja de APIMart y borra el archivo; en un editor ya instalado lo hace Conectar-Editor.cmd.
//
// Quien: SOLO el dueño y el editor de video (rol `videos`), con su sesion. exigirSesion falla cerrado: sin SESSION_SECRET o sin cookie no abre, aunque el login este apagado. La llave del editor
// (CLAVE_EDITOR) solo sirve para las puertas del editor (editor-costos, fabrica-editor, editor-apimart): con ella se gasta APIMart dentro de los topes, no se leen ventas ni cuentas.
// Sin CLAVE_EDITOR (o de menos de 16 caracteres) las lineas contestan 503 y el estado dice configurada:false. Nada se guarda ni se cachea (no-store). Middleware: en la lista del rol videos.

import { exigirSesion } from '../_sesion.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequest(context) {
  const { request, env } = context;
  const rol = await exigirSesion(request, env, ['owner', 'videos']);
  if (!rol) return json(403, { ok: false, error: 'Solo el dueño o el editor de video conectan un editor de anuncios.' });
  if (request.method !== 'GET') return json(405, { ok: false, error: 'Solo GET' });

  const url = new URL(request.url);
  const clave = env.CLAVE_EDITOR == null ? '' : String(env.CLAVE_EDITOR);
  const configurada = clave.length >= 16 && !/[\s]/.test(clave);
  if (url.searchParams.get('estado')) return json(200, { ok: true, puede: true, configurada });
  if (!configurada) return json(503, { ok: false, error: 'La llave del editor no esta configurada en el dash: el dueño la crea una vez con scripts\\Setup-ClaveEditor.ps1.' });
  return json(200, { ok: true, lineas: 'DASH_URL=' + url.origin + '\nDASH_CLAVE_EDITOR=' + clave + '\n' });
}
