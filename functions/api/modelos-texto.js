// Lista los modelos de texto que el proveedor activo tiene disponibles.
//
// Mismo motivo que modelos-imagen.js: el catalogo cambia sin avisar y escribir el id a
// mano es como se llega a un 400 que no explica nada. Preguntandole al proveedor, el
// selector siempre ofrece lo que de verdad se puede usar hoy.
//
// Se cachea 12 horas por proveedor. El id del proveedor va en la clave porque tener
// Claude y Gemini configurados a la vez es lo normal, y una sola cache les daria a los
// dos la lista del ultimo que se consulto.

import { exigirDueno, exigirAlguno, EQUIPO } from '../_sesion.js';
import { precioTexto } from '../_precios.js';
import { llaveApimart, catalogoApimart, modelosDeTexto } from '../_apimart.js';

const HORAS = 12;
// Lo que ve quien solo mira (trafficker, videos) cuando todavia no hay catalogo guardado: no se le pide nada al proveedor en su nombre.
const MSG_SIN_CATALOGO = 'Todavía no hay catálogo guardado: lo trae el dueño o el editor al abrir esta página.';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Lo que cuesta y para que sirve cada familia. El proveedor no lo expone, y sin esto el
// selector seria una lista de ids donde nadie sabe cual es el barato.
const NOTAS = [
  [/haiku/i,            { nota: 'el mas barato y rapido',            orden: 1 }],
  [/sonnet/i,           { nota: 'equilibrio precio/calidad',         orden: 3 }],
  [/opus/i,             { nota: 'el mejor, mas caro',                orden: 5 }],
  [/fable/i,            { nota: 'para redaccion larga',              orden: 4 }],
  [/flash-lite/i,       { nota: 'lo mas barato de Google',           orden: 1 }],
  [/flash/i,            { nota: 'barato y rapido',                   orden: 2 }],
  [/gemini.*pro/i,      { nota: 'calidad alta',                      orden: 4 }],
  [/gpt-4o-mini|mini/i, { nota: 'barato',                            orden: 2 }],
  [/gpt-4o|gpt-4\.1/i,  { nota: 'calidad alta',                      orden: 4 }],
];

function anotar(id) {
  for (const [re, info] of NOTAS) if (re.test(id)) return info;
  return { nota: '', orden: 6 };
}

function ordenar(modelos) {
  // El precio viaja con el modelo para que el creador de ebooks pueda estimar sin
  // conocer el catalogo ni pedir otra cosa. Si el proveedor ya lo publico -Together lo
  // hace- se respeta el suyo: viene de la fuente, no de una tabla que envejece.
  modelos.forEach((m) => { if (!m.precio) m.precio = precioTexto(m.id); });
  modelos.sort((a, b) => a.orden - b.orden || a.id.localeCompare(b.id));
  return modelos;
}

async function listar(prov, llave) {
  if (prov.tipo === 'anthropic') {
    const r = await fetch('https://api.anthropic.com/v1/models?limit=100', {
      headers: { 'x-api-key': llave, 'anthropic-version': '2023-06-01' },
    });
    if (!r.ok) return { error: 'Anthropic respondio ' + r.status + ': ' + (await r.text().catch(() => '')).slice(0, 160) };
    const j = await r.json();
    return { modelos: ordenar((j.data || []).map((m) => {
      const a = anotar(m.id);
      return { id: m.id, nombre: m.display_name || m.id, nota: a.nota, orden: a.orden };
    })) };
  }

  if (prov.tipo === 'gemini') {
    const r = await fetch('https://generativelanguage.googleapis.com/v1beta/models?key=' +
      encodeURIComponent(llave) + '&pageSize=200');
    if (!r.ok) return { error: 'Gemini respondio ' + r.status + ': ' + (await r.text().catch(() => '')).slice(0, 160) };
    const j = await r.json();
    const utiles = (j.models || []).filter((m) =>
      (m.supportedGenerationMethods || []).indexOf('generateContent') !== -1);
    return { modelos: ordenar(utiles.map((m) => {
      const id = String(m.name || '').replace(/^models\//, '');
      const a = anotar(id);
      return { id, nombre: m.displayName || id, nota: a.nota, orden: a.orden };
    })) };
  }

  if (prov.tipo === 'openai') {
    const r = await fetch('https://api.openai.com/v1/models', {
      headers: { Authorization: 'Bearer ' + llave },
    });
    if (!r.ok) return { error: 'OpenAI respondio ' + r.status + ': ' + (await r.text().catch(() => '')).slice(0, 160) };
    const j = await r.json();
    // El listado trae embeddings, audio y moderacion mezclados con los de chat; aqui solo
    // sirven los que responden texto.
    const utiles = (j.data || []).filter((m) => /^(gpt|o[134])/i.test(m.id) &&
      !/embed|audio|whisper|tts|image|moderation|realtime|transcribe/i.test(m.id));
    return { modelos: ordenar(utiles.map((m) => {
      const a = anotar(m.id);
      return { id: m.id, nombre: m.id, nota: a.nota, orden: a.orden };
    })) };
  }

  if (prov.tipo === 'together') {
    const r = await fetch('https://api.together.xyz/v1/models', {
      headers: { Authorization: 'Bearer ' + llave },
    });
    if (!r.ok) return { error: 'Together respondio ' + r.status + ': ' + (await r.text().catch(() => '')).slice(0, 160) };
    const lista = await r.json();
    // Together publica el precio en el propio catalogo, asi que aqui no se adivina: se usa
    // el suyo. Es el unico proveedor que lo da, y por eso su estimacion es la mas exacta.
    const chat = (Array.isArray(lista) ? lista : []).filter((m) =>
      m.type === 'chat' && m.pricing && m.pricing.input > 0);
    return { modelos: ordenar(chat.map((m) => {
      const ve = /VL|Vision|Llama-4/i.test(m.id);
      return {
        id: m.id,
        nombre: m.display_name || m.id,
        // Sin vision no puede leer los anuncios, que es la mitad del creador de ebooks.
        nota: (ve ? 've imagenes' : 'solo texto'),
        orden: ve ? 1 : 4,
        precio: { entrada: m.pricing.input, salida: m.pricing.output },
      };
    })) };
  }

  // (APIMart no pasa por aqui: su catalogo viene de functions/_apimart.js, ver deApimart.)

  // Una API propia compatible con OpenAI puede no tener /models: se escribe a mano.
  return { modelos: [], manual: true };
}

// Los modelos de TEXTO del catalogo de APIMart: una sola consulta que sirve tambien a imagen y a la pantalla
// de APIs, con cache de 12 h (functions/_apimart.js). El catalogo ya viene filtrado a lo que la llave puede
// usar. Cada modelo lleva si lee imagenes: sin vision no puede analizar anuncios, que es la mitad del creador
// de ebooks (Together lo dice igual).
// APIMart no etiqueta con «Vision» a todos los modelos que SI leen imagenes: el gemini-3.5-flash-lite sale solo con «Text» y, probado el 2026-10-19 con una foto de
// anuncio en /api/ebook-brief, la lee y la entiende. Decir «solo texto» de uno que ve le hace creer a quien elige que con ese no puede modelar anuncios. Estas familias
// leen imagenes siempre (Gemini 2 en adelante, Claude, GPT-4o/4.1/5 y los o3/o4); el resto se rige por las etiquetas del catalogo.
const LEE_IMAGENES = /^(gemini-[2-9]|claude-|gpt-(4o|4.1|5)|o[34](-|$))/i;

async function deApimart(env, ajustes, prov, refrescar, comun, lector) {
  // Quien solo MIRA (trafficker, videos) recibe lo que ya esta guardado: sin llave no hay llamada a APIMart ni escritura de KV (catalogoApimart sin cache y sin llave devuelve sinLlave).
  const llave = lector ? '' : ((await env.SUITE.get('secreto:' + prov.id)) || (await llaveApimart(env, ajustes)));
  const cat = await catalogoApimart(env, llave, refrescar);
  if (lector && !cat.ok) return json(200, { ok: false, error: MSG_SIN_CATALOGO });
  if (cat.ok) {
    const modelos = modelosDeTexto(cat.modelos).map((m) => {
      const a = anotar(m.id);
      const ve = m.tags.indexOf('Vision') !== -1 || m.tags.indexOf('Omni') !== -1 || LEE_IMAGENES.test(m.id);
      const nota = [a.nota, m.tags.length ? (ve ? 've imagenes' : 'solo texto') : ''].filter(Boolean).join(' · ');
      return { id: m.id, nombre: m.id, nota, orden: a.orden };
    });
    if (modelos.length) return json(200, { ok: true, modelos: ordenar(modelos), deCache: cat.deCache, ...comun });
  } else if (cat.sinLlave) {
    return json(200, { ok: false, error: `El proveedor "${prov.nombre}" no tiene API key guardada.` });
  } else if (cat.rechazada) {
    return json(200, { ok: false, error: cat.error });
  }
  // Sin catalogo (APIMart lento, respuesta rara): igual va el modelo que esta puesto, con su precio si la tabla
  // lo conoce, para que el creador de ebooks pueda estimar, y el id se escribe a mano.
  const modelos = prov.modelo
    ? [{ id: prov.modelo, nombre: prov.modelo, nota: 'el que está puesto', orden: 1, precio: precioTexto(prov.modelo) }]
    : [];
  return json(200, { ok: true, modelos, manual: true, ...(cat.error ? { aviso: cat.error } : {}), ...comun });
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  // El editor tambien lee este catalogo: el creador de ebooks le muestra el costo antes
  // de gastar, y sin esto veia "no pude leer la configuracion" y un enlace a /apis que
  // su rol no puede abrir. Aqui no sale ningun secreto -ids de modelo y precios de
  // lista-, asi que leerlo es seguro. Y ahora tambien puede CAMBIARLO: es quien arma las
  // ofertas, asi que es quien mas necesita poder bajarlo a lo barato. Eso lo guarda
  // /api/modelo-elegido, no /api/config -que sigue exclusivo del dueno para llaves,
  // proveedores y la carpeta de Drive-.
  // Desde el 2026-10-15 todo el equipo LEE el catalogo (decision del dueño: ven las mismas paginas que el, con el costo estimado); CAMBIAR el modelo sigue siendo del dueño y del editor (puedeCambiar).
  const rol = await exigirAlguno(request, env, EQUIPO);
  if (!rol) return json(401, { ok: false, error: 'Entra al equipo' });
  const esDueno = !!(await exigirDueno(request, env));
  const puedeCambiar = esDueno || rol === 'editor';

  // Quien solo MIRA (trafficker y videos, desde el 2026-10-15) ve lo que ya esta guardado y nada mas: ni `?refrescar=1` ni `?id=` le hacen preguntarle al proveedor con la llave del dueño, ni escriben
  // en KV (el tope diario de escrituras es de toda la suite). Pedir el catalogo en vivo es de quien puede cambiar el modelo: el dueño y el editor.
  const lector = !puedeCambiar;
  const url = new URL(request.url);
  const refrescar = !lector && url.searchParams.get('refrescar') === '1';
  const idPedido = lector ? null : url.searchParams.get('id');

  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const prov = (ajustes.proveedores || []).find((p) => p.id === (idPedido || ajustes.proveedorActivo));
  if (!prov) return json(200, { ok: false, error: 'No hay proveedor de texto activo. El dueño lo configura en APIs.' });

  // El modelo activo viaja con el catalogo para que el creador de ebooks no tenga que
  // pedir /api/config, que si es solo del dueno.
  const comun = { tipo: prov.tipo, activo: prov.modelo || '', puedeCambiar };

  if (prov.tipo === 'apimart') return deApimart(env, ajustes, prov, refrescar, comun, lector);

  const CACHE = 'modelos:texto:' + prov.id;
  if (!refrescar) {
    const c = await env.SUITE.get(CACHE, { type: 'json' });
    if (c && c.modelos && c.modelos.length) {
      // El precio se vuelve a leer de la tabla (functions/_precios.js) al leer el cache, no se confia en el que se guardo: un cache escrito
      // antes de corregir la tabla -o antes de un cambio de tarifa por fecha- seguia mostrando el precio viejo hasta doce horas, y el
      // estimador del creador de ebooks trabaja con este numero. Together es la excepcion: su precio viene del proveedor y ese manda
      // (solo se rellena si falta).
      const modelos = c.modelos.map((m) => ({ ...m, precio: prov.tipo === 'together' ? (m.precio ?? precioTexto(m.id)) : precioTexto(m.id) }));
      return json(200, { ok: true, modelos, deCache: true, ...comun });
    }
  }

  if (lector) return json(200, { ok: false, error: MSG_SIN_CATALOGO });   // sin catalogo guardado, quien solo mira no lo pide en vivo

  const llave = await env.SUITE.get('secreto:' + prov.id);
  if (!llave) return json(200, { ok: false, error: `El proveedor "${prov.nombre}" no tiene API key guardada.` });

  const res = await listar(prov, llave);
  if (res.error) return json(200, { ok: false, error: res.error });
  if (res.manual) {
    // Sin catalogo igual va el modelo que esta puesto, con su precio si la tabla lo conoce: el
    // creador de ebooks estima con lo que haya en `modelos`, y con la lista vacia decia "no tengo
    // el precio del modelo de texto" aunque fuera uno de los conocidos.
    const modelos = prov.modelo
      ? [{ id: prov.modelo, nombre: prov.modelo, nota: 'el que está puesto', orden: 1, precio: precioTexto(prov.modelo) }]
      : [];
    return json(200, { ok: true, modelos, manual: true, ...comun });
  }

  await env.SUITE.put(CACHE, JSON.stringify({ modelos: res.modelos, traidoEn: new Date().toISOString() }),
    { expirationTtl: HORAS * 3600 });
  return json(200, { ok: true, modelos: res.modelos, deCache: false, ...comun });
}
