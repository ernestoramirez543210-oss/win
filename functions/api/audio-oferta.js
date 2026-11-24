// Escribe los guiones de los dos audios de una oferta (presentacion y cierre de
// confianza), con el proveedor de texto activo, para no dejarlos en blanco esperando que
// alguien los grabe a mano.
//
// La estructura de abajo no se inventa: sale de comparar los audios que ya venden (moldes
// en foamy, cupcakes, recetas biblicas, kinesiotaping) y encontrar que los cuatro pares
// siguen el mismo esqueleto, cambiando solo el producto y a quien se dirige. Por eso el
// prompt no dice "escribe algo persuasivo": le da el esqueleto exacto y solo pide llenarlo
// con los datos de la oferta nueva.
//
// La presentadora queda fija en "Laura" a proposito: es el nombre que se pide estandarizar
// en los audios, sin depender de quien atienda el chat en cada oferta.

import { exigirEquipo } from '../_sesion.js';
import { llamarLLM, conEnv } from '../_llm.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

const SISTEMA = `Escribes guiones de notas de voz de WhatsApp para vender infoproductos en
Latinoamerica, en la voz de una presentadora llamada Laura. Sigues EXACTAMENTE esta
estructura, aprendida de guiones que ya venden:

AUDIO 1 (presentacion, va justo despues del saludo del chat):
1. Saludo y quien es: "Hola, que bueno tenerte por aqui. Soy Laura y por anos me he
   dedicado a [su campo de experiencia, segun la oferta]."
2. Empatia: algo que ha visto o vivido acompanando a personas con el mismo problema que
   resuelve la oferta.
3. Transicion a la solucion: "Pensando en ayudarte..., reuni en este [nombre del
   producto] ... que ya ha transformado [la vida / el negocio / la salud] de muchisimas
   personas."
4. Diferenciador con 2 o 3 cosas concretas de lo que incluye: "No son solo [algo
   generico], son los secretos/protocolos para..."
5. Entrega: "Recibes todo al instante aqui en WhatsApp, listo para ver en tu celular o
   imprimir si prefieres."
6. Precio y llamado: "Y lo mejor, es que ofrezco este [producto] completo por un valor
   simbolico para que puedas [beneficio] sin arriesgar casi nada."
7. Cierre con prueba social: "Este material ya ha ayudado a muchas y estoy segura de que
   tambien sera el comienzo de tu propia [transformacion]."

AUDIO 2 (cierre de confianza, va justo antes de pedir el si; casi el mismo guion siempre,
solo cambia el producto y a quien se dirige):
"Se que en internet hay muchas estafas y es normal tener desconfianza. Por eso quiero
que hagamos un acuerdo de confianza tu y yo. Te voy a enviar ahora mismo [el producto y
sus bonos] antes de que pagues. Confio de corazon en la palabra y la honestidad de [la
audiencia] como tu. Despues de que lo recibas y veas que todo esta en orden, haces el
pago y me envias el comprobante. Te parece si te doy este voto de confianza?"

Reglas:
- Tuteas. Calido, como se lo dirias a una amiga por WhatsApp, nunca como un anuncio.
- Una frase corta por linea: cada salto de linea es una pausa real al generar el audio.
- Nada de precios exactos ni promesas medicas.
- Marca la entonacion metiendo, pegada antes de la frase que se dice con esa emocion, una
  etiqueta de esta lista y no mas de dos por audio: [excited] [laughing]. Si todo suena
  emocionado nada se nota, usalas solo donde de verdad aporten.
- Responde EXACTAMENTE en este formato, sin nada mas antes ni despues:
AUDIO1:
(guion del audio 1, una frase por linea)
AUDIO2:
(guion del audio 2, una frase por linea)`;

function partir(texto) {
  const m = /AUDIO1:\s*([\s\S]*?)\s*AUDIO2:\s*([\s\S]*)/i.exec(texto || '');
  if (!m) return null;
  return { audio1: m[1].trim(), audio2: m[2].trim() };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirEquipo(request, env))) return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const oferta = String(body.oferta || '').trim();
  const promesa = String(body.promesa || '').trim();
  if (!oferta || !promesa) return json(400, { ok: false, error: 'Faltan el nombre de la oferta y la promesa.' });
  const vinetas = (Array.isArray(body.vinetas) ? body.vinetas : []).map((v) => String(v).trim()).filter(Boolean);
  const bonos = (Array.isArray(body.bonos) ? body.bonos : []).map((v) => String(v).trim()).filter(Boolean);

  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const prov = conEnv((ajustes.proveedores || []).find((p) => p.id === ajustes.proveedorActivo), env);
  if (!prov) return json(200, { ok: false, error: 'No hay proveedor de texto activo. Configuralo en APIs.' });
  const llave = await env.SUITE.get('secreto:' + prov.id);
  if (!llave) return json(200, { ok: false, error: `"${prov.nombre}" no tiene API key guardada.` });

  const mensaje = 'Producto: ' + oferta + '\nPromesa: ' + promesa +
    (vinetas.length ? '\nLo que incluye:\n' + vinetas.map((v) => '- ' + v).join('\n') : '') +
    (bonos.length ? '\nBonos de regalo con el pago: ' + bonos.join(', ') : '');

  const r = await llamarLLM(prov, llave, SISTEMA, mensaje, { maxTokens: 900, temperatura: 0.8 });
  if (!r.ok) return json(200, { ok: false, error: r.error });

  const partido = partir(r.texto);
  if (!partido) return json(200, { ok: false, error: 'La IA no devolvio el formato esperado. Intenta de nuevo.' });
  return json(200, { ok: true, audio1: partido.audio1, audio2: partido.audio2 });
}
