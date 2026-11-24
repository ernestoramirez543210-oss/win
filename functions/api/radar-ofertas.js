// Las ofertas que el radar de Brasil dejo en la planilla RADAR OFERTAS, para la pagina /radar.
//
//   GET /api/radar-ofertas             -> { ok, ofertas:[...], conteo, leidoEn, desdeCopia, faltan, planilla }
//   GET /api/radar-ofertas?fresco=1    -> igual, pero salta la copia guardada si ya tiene mas de 20 s
//   Sin conexion o con un problema: { ok:false, codigo, error } con estado 200 (la pagina explica el paso que falta).
//
// De donde sale: la planilla es PRIVADA (el radar entra con el permiso de Google del dueño y este sitio no lo tiene, ni
// debe tenerlo). La pestaña OFERTAS se publica como CSV desde Google Sheets (Archivo > Compartir > Publicar en la web >
// OFERTAS > CSV) y esa direccion vive en la variable RADAR_CSV_URL de Cloudflare Pages. Se pone con
// `scripts\Agregar-Variable.ps1 -Nombre RADAR_CSV_URL` (la pide oculta): la direccion nunca viaja al navegador ni se
// imprime. Quien la tenga puede LEER esa pestaña, nada mas: no escribe, no ve las otras.
//
// Solo lee. El dueño sigue marcando el Status en la planilla; aqui se ve en minutos (Google actualiza la copia
// publicada cada ~5 min y este endpoint guarda la suya 2 min).

import { exigirAlguno, EQUIPO } from '../_sesion.js';
import { parsearCsv, normalizarTabla, contarPorEstado, huella } from '../_radar.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

const MAX_BYTES = 3 * 1024 * 1024;
const VIGENCIA_S = 120;      // la copia guardada se usa hasta 2 min
const MIN_FRESCO_S = 20;     // "Actualizar" no puede martillar a Google: dentro de 20 s se sirve la copia
const TIMEOUT_MS = 15000;

const MENSAJES = {
  sin_conexion: 'La planilla todavía no está conectada al dash.',
  url_invalida: 'La dirección de la planilla (RADAR_CSV_URL) no es de Google Sheets.',
  url_incompleta: 'La dirección guardada en RADAR_CSV_URL no es la del CSV publicado (no trae output=csv): casi seguro quedó cortada al pegarla.',
  no_publicada: 'Google no devolvió la planilla: la pestaña OFERTAS dejó de estar publicada en la web.',
  no_es_ofertas: 'Lo que está publicado no parece la pestaña OFERTAS (faltan sus encabezados).',
  vacia: 'La planilla publicada llegó vacía.',
  grande: 'La planilla publicada pesa demasiado.',
  google: 'Google no respondió. Reintenta en un minuto.',
};
const falla = (codigo, extra) => json(200, { ok: false, codigo, error: MENSAJES[codigo] || 'No se pudo leer la planilla.', ...(extra || {}) });

// Solo se sale a Google Sheets. La variable la pone el dueño, pero una direccion mal pegada no debe convertir esto en un
// lector de cualquier sitio.
export function urlDePlanilla(valor) {
  let u;
  try { u = new URL(String(valor || '').trim()); } catch { return null; }
  if (u.protocol !== 'https:' || u.hostname !== 'docs.google.com' || !u.pathname.startsWith('/spreadsheets/')) return null;
  return u.toString();
}

// La direccion del CSV publicado termina en output=csv (la de exportar lleva format=csv). Una que no lo trae casi siempre esta CORTADA:
// el 2026-10-14 la entrada oculta de PowerShell (Read-Host -AsSecureString) dejo pasar solo 150 de los 169 caracteres de un pegado y
// el valor guardado terminaba en "&sin". Google contesta entonces la pagina web de la hoja y el mensaje "dejo de estar publicada"
// mandaria a buscar el problema en el lugar equivocado.
export function esDireccionCsv(url) {
  let p;
  try { p = new URL(url).searchParams; } catch { return false; }
  return String(p.get('output') || '').toLowerCase() === 'csv'
    || String(p.get('format') || '').toLowerCase() === 'csv'
    || /out:csv/i.test(String(p.get('tqx') || ''));
}

async function descargar(url) {
  const r = await fetch(url, {
    headers: { 'Accept': 'text/csv,text/plain;q=0.9,*/*;q=0.5', 'User-Agent': 'SuiteOfertas/1.0' },
    redirect: 'follow',
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!r.ok) return { codigo: r.status === 404 || r.status === 400 || r.status === 401 || r.status === 403 ? 'no_publicada' : 'google' };
  const tipo = (r.headers.get('content-type') || '').toLowerCase();
  // Una pestaña que dejo de estar publicada contesta con la pagina de acceso (HTML), no con un error.
  if (tipo.includes('text/html')) return { codigo: 'no_publicada' };
  const largo = Number(r.headers.get('content-length') || 0);
  if (largo > MAX_BYTES) return { codigo: 'grande' };
  const texto = await r.text();
  if (texto.length > MAX_BYTES) return { codigo: 'grande' };
  return { texto };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'GET') return json(405, { ok: false, error: 'GET only' });
  // Solo lee: la lista de ofertas del radar es investigacion de mercado (anuncios publicos de otras empresas), sin cifras del negocio ni datos de clientes. La ve todo el equipo
  // desde el 2026-10-15 (decision del dueño); pedir ebooks desde ahi (/api/radar-orden) sigue siendo del dueño y del editor.
  if (!(await exigirAlguno(request, env, EQUIPO))) {
    return json(401, { ok: false, error: 'Entra al equipo para ver el radar' });
  }

  const crudo = env.RADAR_CSV_URL;
  if (!crudo) return falla('sin_conexion');
  const url = urlDePlanilla(crudo);
  if (!url) return falla('url_invalida');
  if (!esDireccionCsv(url)) return falla('url_incompleta');

  const planilla = /^https:\/\/docs\.google\.com\/spreadsheets\/d\/[\w-]{20,80}\//.test(String(env.RADAR_SHEET_URL || ''))
    ? env.RADAR_SHEET_URL : '';
  const pedido = new URL(request.url);
  const fresco = pedido.searchParams.get('fresco') === '1';

  // Copia de 2 min: la pagina la abren dos o tres personas a la vez y Google no necesita tantas visitas. La clave lleva la
  // huella de la direccion para que cambiar RADAR_CSV_URL no sirva datos de la planilla anterior.
  const cache = typeof caches !== 'undefined' && caches.default ? caches.default : null;
  const claveCopia = new Request('https://radar-copia.invalid/ofertas?u=' + huella(url));
  if (cache) {
    try {
      const copia = await cache.match(claveCopia);
      if (copia) {
        const datos = await copia.json();
        const edad = (Date.now() - Date.parse(datos.leidoEn)) / 1000;
        if (edad < (fresco ? MIN_FRESCO_S : VIGENCIA_S)) return json(200, { ...datos, desdeCopia: true, planilla });
      }
    } catch { /* una copia ilegible se ignora: se vuelve a pedir */ }
  }

  let bajada;
  try { bajada = await descargar(url); } catch { return falla('google'); }
  if (bajada.codigo) return falla(bajada.codigo);

  const t = normalizarTabla(parsearCsv(bajada.texto));
  if (t.error) return falla(t.error, { faltan: t.faltan });

  const datos = { ok: true, ofertas: t.ofertas, conteo: contarPorEstado(t.ofertas), faltan: t.faltan, leidoEn: new Date().toISOString() };
  if (cache) {
    try {
      await cache.put(claveCopia, new Response(JSON.stringify(datos), {
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'max-age=' + VIGENCIA_S },
      }));
    } catch { /* sin copia solo se pierde la rapidez */ }
  }
  return json(200, { ...datos, desdeCopia: false, planilla });
}
