// Los archivos que se adjuntan: PDF que se consiguen fuera o se acaban de crear.
//
// El catalogo de ZapData solo sirve para lo que YA se subio alguna vez. Una oferta nueva
// casi siempre trae material nuevo, y si el unico camino es arrastrarlo despues en el
// editor de ZapData, el flujo generado llega incompleto y hay que acordarse de terminarlo.
// Acordarse es justo lo que falla.
//
// Aqui el archivo se sube desde el mismo formulario, queda con una direccion fija, y el
// flujo sale entero. De regalo: queda en el catalogo, asi que la siguiente oferta que use
// el mismo bono ya no sube nada.
//
// La direccion es PUBLICA a proposito, y tiene que serlo: quien va a descargar el archivo
// es el servidor de ZapData para mandarlo por WhatsApp, y no tiene forma de iniciar sesion
// aqui. Es el mismo trato que ya tiene el almacenamiento de ZapData, donde estos mismos
// PDF viven hoy con direcciones publicas. Lo que la protege es que el identificador es
// aleatorio y largo: no se puede listar ni adivinar. Aun asi, esto es para material de
// marketing que de todos modos se le manda a cualquiera que compre. Nada mas.

import { readSession } from '../_sesion.js';

const DATO   = 'zaparch:d:';   // los bytes
const INDICE = 'zaparch:indice';

// 24 MB. El limite de una entrada de KV son 25; el margen es para la metadata y para no
// quedarse justo en el borde. Un PDF de marketing que pasa de ahi tampoco deberia mandarse
// por WhatsApp: un entregable puede pesar unos 20 MB, y un bono de 18 MB
// hubo que recomprimirlo a 1 MB para que llegara.
const TOPE = 24 * 1024 * 1024;

const TIPOS = {
  pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  mp3: 'audio/mpeg', ogg: 'audio/ogg', mp4: 'video/mp4',
};

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

function tipoDe(nombre) {
  const ext = String(nombre || '').toLowerCase().split('.').pop();
  return TIPOS[ext] || 'application/octet-stream';
}

// Identificador largo y aleatorio: es lo unico que separa el archivo del resto de internet.
function nuevoId() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b).map((x) => x.toString(16).padStart(2, '0')).join('');
}

async function leerIndice(env) {
  const raw = await env.SUITE.get(INDICE, { type: 'json' });
  return Array.isArray(raw) ? raw : [];
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  const url = new URL(request.url);
  const id = url.searchParams.get('id');

  // Entregar el archivo. SIN sesion: quien lo pide es ZapData, no un navegador.
  if ((request.method === 'GET' || request.method === 'HEAD') && id) {
    if (!/^[0-9a-f]{32}$/.test(id)) return json(400, { ok: false, error: 'id invalido' });
    const meta = (await leerIndice(env)).find((a) => a.id === id);
    const bytes = await env.SUITE.get(DATO + id, { type: 'arrayBuffer' });
    if (!bytes || !meta) return json(404, { ok: false, error: 'No existe' });
    return new Response(bytes, {
      headers: {
        'Content-Type': meta.tipo || 'application/octet-stream',
        // El nombre importa: es el que ve la clienta en WhatsApp.
        'Content-Disposition': 'inline; filename="' + meta.archivo.replace(/"/g, '') + '"',
        'Cache-Control': 'public, max-age=31536000, immutable',
      },
    });
  }

  // Todo lo demas sigue pidiendo sesion.
  if (true) {
    const rol = env.SESSION_SECRET
      ? await readSession(request.headers.get('Cookie'), env.SESSION_SECRET)
      : null;
    if (!rol) return json(401, { ok: false, error: 'No autenticado' });
  }

  if (request.method === 'GET') {
    return json(200, { ok: true, archivos: await leerIndice(env) });
  }
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  const archivo = String(url.searchParams.get('nombre') || '').trim();
  if (!archivo) return json(400, { ok: false, error: 'Falta el nombre del archivo' });
  const nombre = String(url.searchParams.get('titulo') || '').trim() ||
    archivo.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');

  const bytes = await request.arrayBuffer();
  if (!bytes.byteLength) return json(400, { ok: false, error: 'El archivo llegó vacío' });
  if (bytes.byteLength > TOPE) {
    return json(413, {
      ok: false,
      error: 'Pesa ' + Math.round(bytes.byteLength / 1048576) + ' MB y el tope son 24. Comprímelo antes.',
    });
  }

  const nuevo = {
    id: nuevoId(), nombre, archivo, tipo: tipoDe(archivo),
    peso: bytes.byteLength, subidoEn: new Date().toISOString(),
  };
  await env.SUITE.put(DATO + nuevo.id, bytes);
  const indice = await leerIndice(env);
  indice.push(nuevo);
  await env.SUITE.put(INDICE, JSON.stringify(indice));

  // Absoluta, porque quien la va a abrir es un servidor de otro dominio.
  return json(200, { ok: true, archivo: { ...nuevo, url: url.origin + '/api/zaparchivo?id=' + nuevo.id } });
}
