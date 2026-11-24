// LA VOZ DEL ANUNCIO AUTOMATICO (scripts/voz_auto.py del editor de anuncios).
//
// El dueño ya genera con Fish Audio los audios de sus flujos de WhatsApp: la página APIs (sección Audio / voz) guarda el proveedor, el modelo y la «voz por defecto»; la página Voces guarda las voces
// (nombre, genero y el reference id de Fish) en `ajustes.audioApi`, y la llave de Fish vive aparte en `secreto:audio`. Para que el anuncio suene con ESA voz sin que la llave salga
// del dash, el editor (el PC del editor o el VPS, SIN sesion, con la llave del editor) le pide el audio a /api/fabrica-editor, que usa este modulo.
//
// Que PUEDE quien llama con la llave del editor: pedir voz con una de las voces YA guardadas (por su nombre; nunca un id libre), con un tope de largo por llamada y de llamadas y letras
// por dia; y ver los NOMBRES y el genero de las voces (nunca sus ids). Que NO puede: ver o cambiar la llave de Fish ni los ajustes, usar otro proveedor, ni nada de KV fuera de lo de abajo.
// OJO: con esa llave se puede hacer decir CUALQUIER texto a las voces guardadas (hasta 1.500 letras por vez). Por eso cada voz deja un registro (hora, voz, letras y el comienzo del texto) en el
// mismo valor del contador del dia, que guarda las ultimas 40.
// PERO el tope es BLANDO: el almacen KV no es atomico, asi que una rafaga de pedidos en paralelo puede pasarse de 40 (y el registro de lo pedido puede perder entradas). El tope REAL es el saldo de Fish
// (prepago, sin recarga automatica: hay que comprobarlo en su panel) y, si algun dia hace falta un tope duro, un Durable Object o una regla de limite de frecuencia en Cloudflare. El registro se ve en el
// visor de KV de Cloudflare (clave fab:voz:<dia>; vence a los 3 dias): ninguna pagina del dash lo muestra.
//
// Este modulo NO importa nada a proposito: la puerta de Claudia no debe llegar a mas codigo del que se lee aqui, y scripts/Auditar-Seguridad.ps1 (3d-ter) lo vigila.
// KV que toca: `ajustes` y `secreto:audio` (solo lectura) y `fab:voz:<dia UTC>` (el contador; lectura y escritura). Nada mas.
// Las dos funciones de Fish de abajo son una COPIA de functions/api/audio.js (el generador de los flujos): si Fish cambia su contrato hay que cambiar las dos;
// scripts/probar-fabrica.mjs comprueba que las dos le pidan a Fish exactamente lo mismo.

export const VOZ_LIMITES = { letrasPorLlamada: 1500, bytesPorLlamada: 2400, llamadasPorDia: 40, letrasPorDia: 25000, bytesPorDia: 50000, registroUltimas: 40 };
const PRECIO_FISH_USD_POR_MILLON_BYTES = 15;   // docs.fish.audio (pricing-and-rate-limits), consultado el 2026-10-20: s2.1-pro y s2-pro

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});
const clavePlana = (s) => String(s == null ? '' : s).trim().toLowerCase();
// Un mensaje de error nunca lleva la llave de Fish ni el id de una voz guardada, ni siquiera si el proveedor los repite en el suyo.
const ocultar = (texto, secretos) => {
  let t = String(texto == null ? '' : texto);
  for (const s of secretos || []) { const x = String(s == null ? '' : s); if (x.length >= 8) t = t.split(x).join('****'); }
  return t;
};

// Las voces guardadas: [{ nombre, id, genero }] (la pestaña Voces las guarda asi: nombre, genero mujer|hombre|otra y el reference id de Fish). Si no hay lista pero si una voz por defecto
// (audioApi.vozId), esa es la unica (como en la pantalla de ofertas).
export function vocesGuardadas(ajustes) {
  const a = (ajustes && ajustes.audioApi) || {};
  const lista = (Array.isArray(a.voces) ? a.voces : [])
    .filter((v) => v && typeof v.nombre === 'string' && v.nombre.trim() && typeof v.id === 'string' && v.id.trim())
    .map((v) => ({ nombre: v.nombre.trim().slice(0, 80), id: v.id.trim(), genero: ['mujer', 'hombre'].includes(clavePlana(v.genero)) ? clavePlana(v.genero) : '' }));
  if (!lista.length && typeof a.vozId === 'string' && a.vozId.trim()) lista.push({ nombre: 'Voz por defecto', id: a.vozId.trim(), genero: '' });
  return lista;
}

// La voz con la que se hacen los audios de los flujos cuando nadie elige otra: la «voz por defecto» de APIs si esta guardada en la lista (es lo que usa /api/audio) y, si no, la primera de la lista
// (la que la pantalla de ofertas deja marcada).
export function vozPorDefecto(ajustes, voces) {
  const lista = voces || vocesGuardadas(ajustes);
  const idDefecto = String(((ajustes && ajustes.audioApi) || {}).vozId || '').trim();
  return (idDefecto && lista.find((v) => v.id === idDefecto)) || lista[0] || null;
}

async function leerAjustes(env) {
  try { const raw = await env.SUITE.get('ajustes'); return raw ? JSON.parse(raw) : {}; } catch { return {}; }
}

// ---------------------------------------------------------------- Fish Audio (copia de functions/api/audio.js)
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

// Una nota de voz tiene que ser OGG/Opus: se pide opus y se comprueba que venga en contenedor Ogg (firma «OggS»); si Fish devolviera otra cosa, se cae al MP3 antes que entregar un archivo roto.
export async function generarFishAudio(llave, args) {
  const buf = await pedirFish(llave, args, 'opus');
  const firma = new Uint8Array(buf.slice(0, 4));
  if (String.fromCharCode(firma[0], firma[1], firma[2], firma[3]) === 'OggS') {
    return { buf, tipo: 'audio/ogg' };
  }
  return { buf: await pedirFish(llave, args, 'mp3'), tipo: 'audio/mpeg' };
}

// ---------------------------------------------------------------- lo que usa /api/fabrica-editor
// GET ?voces=1: solo los nombres y el genero (el id de una voz es de la cuenta del proveedor y no sale de aqui), y cual es la voz de los flujos.
export async function vocesParaEditor(env) {
  const ajustes = await leerAjustes(env);
  const voces = vocesGuardadas(ajustes);
  const defecto = vozPorDefecto(ajustes, voces);
  return json(200, { ok: true, voces: voces.map((v) => ({ nombre: v.nombre, genero: v.genero })), porDefecto: defecto ? defecto.nombre : '' });
}

// POST { accion:'voz', texto, voz? }: el audio (OGG/Opus; MP3 si Fish no da Ogg) con la voz pedida por su nombre o, si no se pide, la voz de los flujos.
export async function vozDelEditor(env, b) {
  const texto = String(b.texto == null ? '' : b.texto).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').replace(/[ \t]+/g, ' ').trim();
  if (!texto) return json(400, { ok: false, error: 'Falta el texto de la voz' });
  if (texto.length > VOZ_LIMITES.letrasPorLlamada) return json(400, { ok: false, error: 'El texto tiene ' + texto.length + ' letras y el maximo es ' + VOZ_LIMITES.letrasPorLlamada + ' por voz (un anuncio de 60 s son unas 900). Escribe uno mas corto.' });
  const bytes = new TextEncoder().encode(texto).length;
  if (bytes > VOZ_LIMITES.bytesPorLlamada) return json(400, { ok: false, error: 'El texto pesa ' + bytes + ' bytes y el maximo es ' + VOZ_LIMITES.bytesPorLlamada + ' (el proveedor cobra por byte).' });

  const ajustes = await leerAjustes(env);
  const audioApi = ajustes.audioApi || {};
  const tipo = audioApi.tipo || 'fishaudio';
  if (tipo !== 'fishaudio') return json(501, { ok: false, error: 'El proveedor de audio configurado ("' + String(tipo).slice(0, 30) + '") todavia no esta soportado desde el editor: solo Fish Audio.' });
  const voces = vocesGuardadas(ajustes);
  if (!voces.length) return json(409, { ok: false, error: 'No hay ninguna voz guardada en el dash. Agrega una en la página Voces.' });
  const pedida = b.voz == null || b.voz === '' ? '' : clavePlana(b.voz);
  let voz = vozPorDefecto(ajustes, voces);
  if (pedida) {
    voz = voces.find((v) => clavePlana(v.nombre) === pedida);
    if (!voz) return json(400, { ok: false, error: 'Esa voz no esta guardada. Las que hay: ' + voces.map((v) => v.nombre).join(', ') });
  }
  let llave = null;
  try { llave = await env.SUITE.get('secreto:audio'); } catch { llave = null; }
  if (!llave) return json(503, { ok: false, error: 'No hay llave de audio guardada en el dash (página APIs).' });

  // Topes por dia (UTC) y registro de lo pedido. Se anota ANTES de pedir el audio; si no se puede anotar, no se genera.
  const clave = 'fab:voz:' + new Date().toISOString().slice(0, 10);
  let cont = { n: 0, letras: 0, bytes: 0, ult: [] };
  try {
    const c = await env.SUITE.get(clave, { type: 'json' });
    if (c && typeof c === 'object') cont = { n: Number(c.n) || 0, letras: Number(c.letras) || 0, bytes: Number(c.bytes) || 0, ult: Array.isArray(c.ult) ? c.ult : [] };
  } catch { /* un contador ilegible empieza de cero: el tope por llamada sigue valiendo */ }
  if (cont.n >= VOZ_LIMITES.llamadasPorDia || cont.letras + texto.length > VOZ_LIMITES.letrasPorDia || cont.bytes + bytes > VOZ_LIMITES.bytesPorDia) {
    return json(429, { ok: false, error: 'Hoy ya se pidieron ' + cont.n + ' voces (' + cont.letras + ' letras, ' + cont.bytes + ' bytes): el tope es ' + VOZ_LIMITES.llamadasPorDia + ' voces, ' + VOZ_LIMITES.letrasPorDia + ' letras o ' + VOZ_LIMITES.bytesPorDia + ' bytes por dia. Sigue manana o avisale al dueño.' });
  }
  const registro = cont.ult.slice(-(VOZ_LIMITES.registroUltimas - 1)).concat([{ t: new Date().toISOString(), voz: voz.nombre, n: texto.length, x: texto.slice(0, 80) }]);
  try { await env.SUITE.put(clave, JSON.stringify({ n: cont.n + 1, letras: cont.letras + texto.length, bytes: cont.bytes + bytes, ult: registro }), { expirationTtl: 3 * 86400 }); } catch {
    return json(503, { ok: false, error: 'No pude anotar el contador del dia (el almacen rechazo la escritura): no genero la voz para no pasarme del tope. Intenta mas tarde.' });
  }

  let salida;
  try { salida = await generarFishAudio(llave, { texto, vozId: voz.id, modelo: String(audioApi.modelo || '').trim() }); } catch (e) {
    return json(502, { ok: false, error: ocultar((e && e.message) || e, [llave].concat(voces.map((v) => v.id))).slice(0, 300) });
  }
  const veces = salida.tipo === 'audio/mpeg' ? 2 : 1;      // si Fish no dio Ogg se pidio dos veces y se cobraron las dos
  return new Response(salida.buf, {
    status: 200,
    headers: {
      'Content-Type': salida.tipo, 'Cache-Control': 'no-store',
      'X-Voz': encodeURIComponent(voz.nombre), 'X-Letras': String(texto.length), 'X-Bytes': String(bytes),
      'X-Costo-Estimado-Usd': (bytes * veces * PRECIO_FISH_USD_POR_MILLON_BYTES / 1e6).toFixed(5),
    },
  });
}
