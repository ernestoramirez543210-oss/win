// Las instrucciones estandar del mockup, editables desde el tablero.
//
// Mismo patron que plantilla.js: se guarda solo lo que se cambio. Si nadie lo toca, el
// prompt sale con el texto base de este archivo, que es el que se dedujo comparando los
// mockups que ya venden. Asi una mejora del texto base llega sola a quien no lo haya
// pisado, y "Restaurar" es simplemente borrar lo guardado.

import { exigirAlguno } from '../_sesion.js';

export const CLAVE = 'plantilla:mockup';

// El texto base: la composicion fija aprendida de mockups que ya venden (producto al
// centro, hasta 3 bonos alrededor, titulo y vinetas en español, texto siempre legible).
export const SISTEMA_BASE = `Escribes prompts en ingles para generar la imagen de un mockup de producto
digital (un infoproducto que se vende por WhatsApp), para un modelo de imagen con IA.
Sigues SIEMPRE esta composicion, aprendida de mockups que ya venden:

- El producto principal va al centro: un libro/ebook con tapa dura de aspecto premium, o
  una tablet mostrando la portada si el producto se siente mas "digital" que "libro". El
  titulo del producto y su promesa van escritos EN ESPAÑOL, grandes y perfectamente
  legibles, en la tapa.
- Si hay bonos, van entre uno y tres alrededor del producto principal (mas pequeños,
  detras o a los lados), cada uno con una etiqueta pequeña "BONO 1", "BONO 2", "BONO 3" y
  su propio titulo corto EN ESPAÑOL.
- El fondo es una escena de estudio fotografico limpia y calida, con objetos reales
  relacionados al tema (ingredientes, herramientas, plantas, telas, lo que tenga sentido),
  nunca un fondo blanco vacio ni una escena recargada que compita con el producto.
- La paleta de color combina con el tema (por ejemplo tonos tierra y dorado para algo
  rustico o de comida, rosado y dorado para algo femenino o de reposteria, verde
  botanico para algo natural, azul o teal para algo clinico o de bienestar).
- Iluminacion suave y profesional, calidad de catalogo de ecommerce premium, todo bien
  enfocado, sin gente, sin caras, sin marcas de agua, sin logos ajenos.
- Puede llevar un pequeño sello o cinta decorativa (ej. una cinta en una esquina, un sello
  circular) SOLO si aporta a la oferta, nunca de relleno.
- Todo el texto que aparezca en la imagen va en español, bien escrito, sin errores
  ortograficos ni palabras inventadas. El texto tiene que quedar NITIDO y LEGIBLE, sin
  letras deformes ni mezcladas.

Con esa composicion fija, escribes el prompt para ESTE producto especifico usando su
nombre, su promesa y sus bonos. Responde SOLO con el prompt en ingles, en un solo
parrafo, sin explicaciones ni comillas.`;

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// La lee mockup-oferta.js antes de escribir el prompt. Un KV caido no puede dejar sin
// mockup a nadie: sin plantilla guardada se trabaja con el texto base.
export async function leerInstruccionesMockup(env) {
  try {
    const v = await env.SUITE.get(CLAVE);
    return typeof v === 'string' && v.trim() ? v : SISTEMA_BASE;
  } catch { return SISTEMA_BASE; }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  // Las instrucciones del mockup las cambia SOLO el dueno: cambian como salen los mockups de TODAS las ofertas y «Restaurar» borra su version sin dejar copia. Generar mockups no las necesita
  // (mockup-oferta.js las lee en el servidor). Con el login apagado todo el sitio queda abierto, y aqui tambien, como siempre.
  if (!(await exigirAlguno(request, env, ['owner']))) return json(403, { ok: false, error: 'Solo el dueño cambia las instrucciones del mockup.' });

  if (request.method === 'GET') {
    const guardado = await env.SUITE.get(CLAVE);
    const editado = typeof guardado === 'string' && guardado.trim();
    return json(200, { ok: true, base: SISTEMA_BASE, texto: editado ? guardado : SISTEMA_BASE, editado: !!editado });
  }

  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  if (body.accion === 'restaurar') {
    await env.SUITE.delete(CLAVE);
    return json(200, { ok: true, texto: SISTEMA_BASE, editado: false });
  }

  const texto = String(body.texto || '').trim();
  if (!texto) return json(400, { ok: false, error: 'Las instrucciones no pueden quedar vacías.' });
  if (texto.length > 6000) return json(400, { ok: false, error: 'El texto es demasiado largo.' });
  await env.SUITE.put(CLAVE, texto);
  return json(200, { ok: true, texto, editado: true });
}
