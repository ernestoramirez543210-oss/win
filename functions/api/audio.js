// Genera el audio de un guion con el proveedor de voz configurado (hoy Fish Audio).
//
// La llave nunca sale de aqui: el navegador manda texto y recibe el MP3 ya hecho, igual
// que el patron que ya usan imagenApi/config.js. El campo "tipo" queda listo para el dia
// que se sume otro proveedor (ElevenLabs, PlayHT...) sin tocar la pestana.

import { exigirEquipo } from '../_sesion.js';
import { leerLlave } from './config.js';
import { vozPorDefecto } from '../_voces.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Un tope de largo del guion: el proveedor de voz cobra por letra y desde el 2026-10-15 lo usa todo el equipo. 6.000 letras son unas 1.000 palabras (6 o 7 minutos de voz), mucho mas que el guion
// que escribe /api/audio-copy (500 tokens) o /api/audio-oferta (900): una nota de voz de WhatsApp no es mas larga. Pasado esto se pide partirlo en dos audios.
const MAX_GUION = 6000;

// Cualquier API que hable el mismo idioma que /v1/audio/speech de OpenAI (Groq, DeepInfra,
// y buena parte de las que se anuncian "compatibles con OpenAI") entra por aqui sin que
// haya que escribirle un adaptador propio: solo hace falta la URL y la llave.
async function generarAudioCompatible(llave, { texto, vozId, modelo, url }) {
  if (!url) throw new Error('Falta la URL del endpoint de audio.');
  const r = await fetch(url, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + llave, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: modelo || undefined, input: texto, voice: vozId || undefined, format: 'mp3' }),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => '');
    let msg = txt.slice(0, 300);
    try { msg = JSON.parse(txt).error?.message || msg; } catch {}
    throw new Error(url + ' contesto ' + r.status + (msg ? ': ' + msg : ''));
  }
  return await r.arrayBuffer();
}

async function pedirFish(llave, { texto, vozId, modelo }, formato) {
  const r = await fetch('https://api.fish.audio/v1/tts', {
    method: 'POST',
    headers: {
      'Authorization': 'Bearer ' + llave,
      'Content-Type': 'application/json',
      'model': modelo || 's2.1-pro',
    },
    body: JSON.stringify({
      text: texto,
      reference_id: vozId || undefined,
      format: formato,
    }),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => '');
    let msg = txt.slice(0, 300);
    try { msg = JSON.parse(txt).message || msg; } catch {}
    throw new Error('Fish Audio contesto ' + r.status + (msg ? ': ' + msg : ''));
  }
  return await r.arrayBuffer();
}

// Una nota de voz de WhatsApp tiene que ser OGG/Opus: un MP3 lo acepta el servidor pero el
// bloque de audio de ZapData no lo entrega como nota de voz y la clienta no recibe nada.
// Se pide opus y se comprueba que venga en contenedor Ogg (firma "OggS"); si Fish devolviera
// otra cosa, se cae al MP3 antes que entregar un archivo roto sin avisar.
async function generarFishAudio(llave, args) {
  const buf = await pedirFish(llave, args, 'opus');
  const firma = new Uint8Array(buf.slice(0, 4));
  if (String.fromCharCode(firma[0], firma[1], firma[2], firma[3]) === 'OggS') {
    return { buf, tipo: 'audio/ogg' };
  }
  return { buf: await pedirFish(llave, args, 'mp3'), tipo: 'audio/mpeg' };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirEquipo(request, env))) return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const texto = (body.texto || '').trim();
  if (!texto) return json(400, { ok: false, error: 'Falta el guion' });
  if (texto.length > MAX_GUION) return json(400, { ok: false, error: 'El guion es muy largo (' + texto.length + ' letras; el maximo es ' + MAX_GUION + '). Partelo en dos audios.' });

  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const audioApi = ajustes.audioApi || {};
  const tipo = audioApi.tipo || 'fishaudio';
  const llave = await leerLlave(env, 'audio');
  if (!llave) return json(200, { ok: false, error: 'No hay API key de audio guardada. Ve a la pestaña APIs.' });

  // Sin voz pedida ni guardada, Fish elegiría una al azar: se usa la estándar.
  const vozId = String(body.vozId || '').trim() || vozPorDefecto(audioApi);
  const modelo = (body.modelo || audioApi.modelo || '').trim();

  try {
    let salida;
    if (tipo === 'fishaudio') {
      salida = await generarFishAudio(llave, { texto, vozId, modelo });
    } else if (tipo === 'compatible') {
      salida = { buf: await generarAudioCompatible(llave, { texto, vozId, modelo, url: audioApi.url }), tipo: 'audio/mpeg' };
    } else {
      return json(200, { ok: false, error: 'Proveedor de audio "' + tipo + '" todavia no esta soportado.' });
    }
    return new Response(salida.buf, {
      status: 200,
      headers: { 'Content-Type': salida.tipo, 'Cache-Control': 'no-store' },
    });
  } catch (e) {
    return json(200, { ok: false, error: e.message || String(e) });
  }
}
