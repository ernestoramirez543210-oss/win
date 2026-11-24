// La puerta del editor de anuncios a APIMart (2026-11-10): imagenes, clips, subidas de referencias, el estado de una tarea y texto/vision, con la llave de APIMart guardada en el DASH.
//
//   GET  /api/editor-apimart?estado=1[&producto=&anuncio=]                      -> topes, lo gastado hoy, pedidos y subidas del dia, los modelos y lo del anuncio (nunca el saldo)
//   POST /api/editor-apimart?subir=1&producto=&anuncio=   (cuerpo = los bytes)  -> { ok, url, expira }      la referencia sube a APIMart; no cuesta, pero suma 1 en el contador del dia
//   POST /api/editor-apimart { accion:'imagen' | 'video' | 'tarea' | 'texto', ... }                     ver functions/_editor-apimart.js
//
// Quien llama: el editor (PC o VPS), SIN sesion, con la cabecera x-clave-editor (CLAVE_EDITOR, la misma de /api/editor-costos y /api/fabrica-editor) y x-version-editor (su VERSION).
// Sin CLAVE_EDITOR puesta (o de menos de 16 caracteres) contesta 503: nunca abre. La comparacion es en tiempo constante. Una version menor que VERSION_MINIMA_EDITOR (o sin la cabecera) recibe 426,
// y para pedir una imagen o un clip, una menor que VERSION_MINIMA_IMAGEN_VIDEO de _editor-apimart.js (la que revisa los prompts antes de pagar) tambien.
// Esta puerta solo hace eso y el despacho: TODA la logica (topes, reservas, libro, tickets) esta en functions/_editor-apimart.js, vigilada por scripts/Auditar-Seguridad.ps1 (3d-quater).
// Topes de cuerpo: 64 KB para lo que es JSON (1,5 MB solo para «texto», que trae fotogramas) y 10 MB para una subida; se mira el content-length y despues lo que llega de verdad.
// El middleware deja pasar sin cookie SOLO esta ruta, en GET o POST y con la cabecera; el endpoint exige la llave por su cuenta. No esta en la lista de ningun rol.

import { safeEqual } from '../_sesion.js';
import { VERSION_MINIMA_EDITOR, versionAlDia } from '../_fabrica.js';
import * as E from '../_editor-apimart.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});
const TOPE_JSON = 64 * 1024;
const TOPE_TEXTO = 1536 * 1024;          // 1,5 MB: hasta 6 fotogramas achicados a 1024 px
const TOPE_SUBIDA = 10 * 1024 * 1024;

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  const llave = request.headers.get('x-clave-editor');
  if (!llave) return json(401, { ok: false, error: 'Falta la llave del editor' });
  if (!env.CLAVE_EDITOR || String(env.CLAVE_EDITOR).length < 16) return json(503, { ok: false, error: 'La llave del editor no esta configurada en el servidor (scripts\\Setup-ClaveEditor.ps1)' });
  if (!safeEqual(llave, String(env.CLAVE_EDITOR))) return json(401, { ok: false, error: 'Llave incorrecta' });
  if (!versionAlDia(request.headers.get('x-version-editor'))) return json(426, { ok: false, versionMinima: VERSION_MINIMA_EDITOR, error: 'Actualiza el editor: el dash pide la versión ' + VERSION_MINIMA_EDITOR + ' o más nueva. Baja el instalador de la página Editor de anuncios y córrelo (no toca tus productos ni videos).' });
  if (!env.SESSION_SECRET) return json(503, { ok: false, error: 'SESSION_SECRET no configurado (firma los tickets de las tareas)' });

  const url = new URL(request.url);
  if (request.method === 'GET') {
    if (url.searchParams.get('estado')) return E.estadoEditor(env, url.searchParams);
    return json(400, { ok: false, error: 'Pide ?estado=1' });
  }
  if (request.method !== 'POST') return json(405, { ok: false, error: 'GET o POST' });

  const largo = Number(request.headers.get('content-length') || 0);
  if (url.searchParams.get('subir')) {
    if (largo > TOPE_SUBIDA) return json(413, { ok: false, error: 'La imagen pesa más de 10 MB: achícala antes.' });
    const bytes = await E.leerCuerpo(request, TOPE_SUBIDA);
    if (bytes === null) return json(413, { ok: false, error: 'La imagen pesa más de 10 MB: achícala antes.' });
    return E.subirImagen(env, url.searchParams, bytes);
  }

  if (largo > TOPE_TEXTO) return json(413, { ok: false, error: 'Cuerpo demasiado grande' });
  const crudo = await E.leerCuerpo(request, TOPE_TEXTO);
  if (crudo === null) return json(413, { ok: false, error: 'Cuerpo demasiado grande' });
  let b;
  try { b = JSON.parse(new TextDecoder().decode(crudo)); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  if (!b || typeof b !== 'object' || Array.isArray(b)) return json(400, { ok: false, error: 'JSON invalido' });
  if (b.accion !== 'texto' && crudo.byteLength > TOPE_JSON) return json(413, { ok: false, error: 'Cuerpo demasiado grande (64 KB; solo «texto» admite hasta 1,5 MB)' });
  if ((b.accion === 'imagen' || b.accion === 'video') && !versionAlDia(request.headers.get('x-version-editor'), E.VERSION_MINIMA_IMAGEN_VIDEO)) {
    return json(426, { ok: false, versionMinima: E.VERSION_MINIMA_IMAGEN_VIDEO, error: 'Actualiza el editor: para pedir imágenes y clips el dash pide la versión ' + E.VERSION_MINIMA_IMAGEN_VIDEO + ' o más nueva (revisa cada prompt antes de pagar). Baja el instalador de la página Editor de anuncios y córrelo (no toca tus productos ni videos). No se gastó nada.' });
  }
  if (b.accion === 'imagen') return E.pedirImagen(env, b);
  if (b.accion === 'video') return E.pedirVideo(env, b);
  if (b.accion === 'tarea') return E.consultarTarea(env, b);
  if (b.accion === 'texto') return E.pedirTexto(env, b);
  return json(400, { ok: false, error: 'Accion no reconocida (imagen, video, tarea o texto)' });
}
