// Lee una pagina de ventas (TSL) y devuelve su texto, para modelar la oferta desde ahi.
//
// Muchas ofertas no se modelan de un anuncio sino de la pagina larga que hay detras: ahi
// esta la promesa completa, el orden de los argumentos, los bonos y el precio. Un
// fotograma de un video no trae nada de eso.
//
// El fetch lo hace el servidor y no el navegador porque una pagina ajena no manda
// cabeceras CORS para nosotros: desde el cliente esto siempre falla.
//
// Devuelve TEXTO, nunca HTML. Lo que salga de aqui va a un prompt, y el HTML crudo gasta
// tres veces mas tokens en etiquetas que en contenido.

import { exigirAlguno, EQUIPO } from '../_sesion.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Una TSL larga son ~40 KB de texto. Se corta ahi: mas que eso ya es el pie de pagina,
// los terminos y el aviso de cookies, que no dicen nada de la oferta.
const MAX_TEXTO = 40000;
const MAX_BYTES = 3 * 1024 * 1024;
const TIMEOUT_MS = 15000;

// Solo se sale a internet publica. Sin esto, pegar http://127.0.0.1 o una IP interna
// convierte este endpoint en un lector de la red privada de quien lo hospeda: el
// servidor haria la peticion con SUS permisos y devolveria el resultado por pantalla.
const PROHIBIDOS = [
  /^localhost$/i,
  /^\[?::1\]?$/,
  /^\[/,                              // cualquier IPv6 escrita a mano (loopback, mapeada a IPv4, privada): una pagina de ventas no vive en una IPv6 literal
  /^0\./,
  /^10\./,
  /^127\./,
  /^169\.254\./,                      // link-local, incluye el metadata de las nubes
  /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[01])\./,
  /\.local$/i,
  /\.internal$/i,
];

export function urlSegura(crudo) {
  let u;
  try { u = new URL(crudo); } catch { return { error: 'Esa no es una URL valida.' }; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    return { error: 'Solo se pueden leer direcciones http o https.' };
  }
  const host = u.hostname;
  for (const p of PROHIBIDOS) {
    if (p.test(host)) return { error: 'Esa direccion es de una red privada y no se puede leer.' };
  }
  return { url: u };
}

// Extraer texto de HTML sin libreria. No es un parser: es quitar lo que nunca es contenido
// y despues las etiquetas. Aqui no hay Node ni npm, y un parser propio serian cientos de
// lineas para ganar poco: al modelo le da igual un salto de linea de mas.
export function textoDeHtml(html) {
  let t = String(html);
  // script/style/noscript traen el codigo entero del sitio. Fuera antes que nada.
  t = t.replace(/<(script|style|noscript|svg|iframe)\b[\s\S]*?<\/\1>/gi, ' ');
  t = t.replace(/<!--[\s\S]*?-->/g, ' ');
  // Los cortes de bloque se vuelven saltos de linea para que no se peguen frases de
  // secciones distintas y el modelo lea "gratis hoyPrecio normal".
  t = t.replace(/<\/(p|div|section|h[1-6]|li|tr|br)\s*>/gi, '\n');
  t = t.replace(/<br\s*\/?>/gi, '\n');
  t = t.replace(/<[^>]+>/g, ' ');
  t = t.replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&lt;/gi, '<')
       .replace(/&gt;/gi, '>').replace(/&quot;/gi, '"').replace(/&#39;/gi, "'");
  t = t.replace(/&#(\d+);/g, (_, n) => {
    const c = Number(n);
    return c > 0 && c < 0x10000 ? String.fromCharCode(c) : ' ';
  });
  t = t.replace(/[ \t ]+/g, ' ');
  t = t.replace(/\n{3,}/g, '\n\n');
  return t.split('\n').map((l) => l.trim()).filter(Boolean).join('\n').trim();
}

function tituloDeHtml(html) {
  const m = String(html).match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return m ? textoDeHtml(m[1]).slice(0, 200) : '';
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'POST only' });
  if (!(await exigirAlguno(request, env, EQUIPO))) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const crudo = (body.url || '').trim();
  if (!crudo) return json(200, { ok: false, error: 'Falta la URL.' });

  const chequeo = urlSegura(crudo.match(/^https?:\/\//i) ? crudo : 'https://' + crudo);
  if (chequeo.error) return json(200, { ok: false, error: chequeo.error });

  let r;
  try {
    r = await fetch(chequeo.url.toString(), {
      // Muchas paginas de ventas devuelven un 403 desnudo a un cliente sin User-Agent.
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; SuiteOfertas/1.0)',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'es-ES,es;q=0.9',
      },
      redirect: 'follow',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (e) {
    return json(200, { ok: false, error: 'No pude abrir esa pagina: ' + e.message });
  }

  if (!r.ok) return json(200, { ok: false, error: 'La pagina respondio ' + r.status + '.' });

  const tipo = r.headers.get('content-type') || '';
  if (tipo && !/text\/html|text\/plain|application\/xhtml/i.test(tipo)) {
    return json(200, { ok: false, error: 'Eso no es una pagina web (' + tipo.split(';')[0] + ').' });
  }
  const largo = Number(r.headers.get('content-length') || 0);
  if (largo > MAX_BYTES) {
    return json(200, { ok: false, error: 'La pagina pesa demasiado (' + Math.round(largo / 1048576) + ' MB).' });
  }

  let html;
  try { html = await r.text(); } catch (e) {
    return json(200, { ok: false, error: 'No pude leer el contenido: ' + e.message });
  }

  const texto = textoDeHtml(html);
  if (texto.length < 200) {
    return json(200, {
      ok: false,
      error: 'Esa pagina casi no trae texto: probablemente lo pinta con JavaScript. ' +
             'Abrila, copia el texto a mano y pegalo en el campo de copy.',
    });
  }

  return json(200, {
    ok: true,
    titulo: tituloDeHtml(html),
    url: r.url || chequeo.url.toString(),
    texto: texto.slice(0, MAX_TEXTO),
    recortado: texto.length > MAX_TEXTO,
  });
}
