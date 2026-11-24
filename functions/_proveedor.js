// Un proveedor de texto que SEPA hacer lo que se le pide, aunque no sea el activo.
//
// El activo (ajustes.proveedorActivo) lo usan todas las herramientas, y el dueno puede dejar ahi el mas barato para crear ebooks (APIMart con un
// Gemini Flash-Lite: unos centavos por ebook). Pero hay dos cosas que SOLO saben hacer algunos proveedores:
//   - ver un video ENTERO con su audio (el argumento de un anuncio va dicho, no escrito): solo Gemini;
//   - leer un PDF o una imagen adjunta de forma nativa: solo Gemini y Anthropic (los compatibles con OpenAI de la suite reciben imagenes, no PDF).
// Antes, si el activo era de otro tipo, el brief de anuncios perdia el audio de los videos (se quedaba con tres fotogramas) y «Aprender» contestaba que
// hacia falta Gemini o Anthropic como proveedor ACTIVO: cambiar el activo para ahorrar dejaba esas dos herramientas cojas. Aqui se busca, entre los
// proveedores guardados, el primero del tipo que sirve y que tenga llave, y solo se usa para eso.
import { conEnv } from './_llm.js';

// tipos: ['gemini'] o ['gemini', 'anthropic']. Devuelve { prov, llave } o null si no hay ninguno con llave. El activo manda cuando su tipo sirve.
export async function proveedorQueSabe(env, tipos, col) {
  const raw = await env.SUITE.get('ajustes');
  let ajustes = {};
  try { ajustes = raw ? JSON.parse(raw) : {}; } catch { ajustes = {}; }
  const lista = Array.isArray(ajustes.proveedores) ? ajustes.proveedores.filter((p) => p && p.id) : [];
  const sirve = (p) => tipos.indexOf(p.tipo) !== -1;
  const activo = lista.find((p) => p.id === ajustes.proveedorActivo);
  const candidatos = (activo && sirve(activo) ? [activo] : []).concat(lista.filter((p) => sirve(p) && p !== activo));
  for (const p of candidatos) {
    const llave = await env.SUITE.get('secreto:' + p.id);
    if (llave) return { prov: conEnv(p, env, col), llave };
  }
  return null;
}
