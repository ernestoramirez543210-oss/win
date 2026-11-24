// Paso 4: los mensajes de ClicChat para la oferta ya terminada.
//
// Va despues del ebook a proposito. El vendedor tiene que prometer lo que el entregable
// realmente trae: si los mensajes se escriben antes, prometen cosas que el archivo no
// tiene y el cliente pide reembolso. Aqui se le pasan los titulos reales de las paginas.
//
// Como son los flujos lo dice el cerebro de ofertas (seccion "Flujos de ClicChat"), que se
// alimenta subiendo los flujos que ya funcionan en PDF. Sin eso escribe flujos genericos
// correctos pero que no suenan a la marca.

import { exigirAlguno, EQUIPO } from '../_sesion.js';
import { llamarLLM, conEnv, jsonDeTexto } from '../_llm.js';
import { leerConocimiento, conocimientoTexto } from './conocimiento.js';
import { avatarTexto } from './ebook-avatar.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Los flujos que existen en la operacion. Se piden de a uno para que cada uno salga
// completo: pedirlos todos de golpe hacia que el ultimo llegara truncado.
export const FLUJOS = [
  { id: 'bienvenida', nombre: 'Bienvenida',
    que: 'El primer mensaje cuando el cliente escribe desde el anuncio. Engancha, confirma que llego al lugar correcto y pregunta lo minimo para seguir.' },
  { id: 'presentacion', nombre: 'Presentacion de la oferta',
    que: 'Que es, que trae y por que le sirve. Aqui se listan los bonos. Termina llevando al precio.' },
  { id: 'precio', nombre: 'Precio y cierre',
    que: 'Da el precio, justifica el valor contra lo que trae, y pide la decision con una instruccion clara de pago.' },
  { id: 'objeciones', nombre: 'Objeciones',
    que: 'Respuestas cortas a: esta caro, lo pienso, es digital?, sirve para mi?, como lo recibo?, es seguro pagar?' },
  { id: 'pago', nombre: 'Datos de pago',
    que: 'Se dan los datos para pagar, se explica que hacer y se acompana mientras paga, sin dejarlo solo.' },
  { id: 'comprobante', nombre: 'Comprobante y entrega',
    que: 'El cliente manda el comprobante de la transaccion: se confirma, se agradece y se entrega el archivo.' },
  { id: 'seguimiento', nombre: 'Seguimiento',
    que: 'Para quien no contesto. Dos o tres toques separados en el tiempo, sin sonar desesperado.' },
  { id: 'entrega', nombre: 'Posventa',
    que: 'Se confirma que abrio el archivo, se pide una opinion y se abre la puerta a otra oferta.' },
];

async function proveedor(env) {
  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const prov = conEnv((ajustes.proveedores || []).find((p) => p.id === ajustes.proveedorActivo), env);
  if (!prov) return { error: 'No hay proveedor de texto activo. Configuralo en APIs.' };
  const llave = await env.SUITE.get('secreto:' + prov.id);
  if (!llave) return { error: `El proveedor "${prov.nombre}" no tiene API key.` };
  return { prov, llave };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method === 'GET') return json(200, { ok: true, flujos: FLUJOS });
  if (request.method !== 'POST') return json(405, { ok: false, error: 'POST only' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirAlguno(request, env, EQUIPO))) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const brief = body.brief;
  if (!brief || !brief.titulo) return json(200, { ok: false, error: 'Falta la oferta del paso 1.' });

  const flujo = FLUJOS.find((f) => f.id === body.flujo);
  if (!flujo) return json(200, { ok: false, error: 'Flujo no reconocido: ' + body.flujo });

  const cfg = await proveedor(env);
  if (cfg.error) return json(200, { ok: false, error: cfg.error });

  // Cada flujo recibe las reglas generales MAS su propia seccion del recorrido.
  const cerebro = conocimientoTexto(await leerConocimiento(env), 'mensajes:' + flujo.id);

  // Los titulos reales del entregable: el vendedor no puede prometer lo que no existe.
  const contenido = (body.paginas || []).map((p) => p.titulo).filter(Boolean);

  const sistema = `Escribes los mensajes de WhatsApp con los que este negocio vende. Lo conoces a fondo:

${cerebro}

Reglas de los mensajes:
- Cada mensaje es una burbuja de WhatsApp: corto, se lee en el celular sin hacer scroll.
- Tono de persona real, no de robot ni de folleto. Tuteo.
- Nada de emojis en exceso: uno o dos por mensaje como maximo.
- No prometas nada que el entregable no traiga.
- No hagas promesas de salud ni de resultados garantizados.
- Responde SIEMPRE en JSON valido, sin texto alrededor.`;

  const instruccion = `Escribe el flujo "${flujo.nombre}" para esta oferta.

QUE DEBE LOGRAR ESTE FLUJO
${flujo.que}

${avatarTexto(body.avatar)}
LA OFERTA
Titulo: ${brief.titulo}
Promesa: ${brief.promesa || ''}
Publico: ${brief.publico || ''}
Angulo: ${brief.angulo || ''}
${(brief.bonos || []).length ? 'Bonos: ' + brief.bonos.join(', ') + '\n' : ''}${body.precio ? 'Precio: ' + body.precio + '\n' : ''}${body.pais ? 'Pais: ' + body.pais + '\n' : ''}${contenido.length ? '\nLO QUE REALMENTE TRAE EL ENTREGABLE (' + contenido.length + ' paginas):\n- ' + contenido.slice(0, 30).join('\n- ') + '\n' : ''}
Devuelve JSON:
{"mensajes":[{
  "orden": numero,
  "texto": "el mensaje tal como lo recibe el cliente",
  "nota": "cuando se manda o que espera de respuesta, para el que configura el flujo"
}]}

Entre 3 y 7 mensajes. Si el flujo es de objeciones, un mensaje por objecion y pon la
objecion en "nota".`;

  const r = await llamarLLM(cfg.prov, cfg.llave, sistema, instruccion,
    { maxTokens: 3000, temperatura: 0.7, json: true });
  if (!r.ok) return json(200, { ok: false, error: r.error });

  const j = jsonDeTexto(r.texto);
  const mensajes = (j && (Array.isArray(j) ? j : j.mensajes)) || [];
  if (!mensajes.length) {
    return json(200, { ok: false, error: 'No devolvio mensajes legibles: ' + (r.texto || '').slice(0, 160) });
  }
  mensajes.forEach((m, i) => { m.orden = i + 1; });

  return json(200, { ok: true, flujo: flujo.id, nombre: flujo.nombre, mensajes });
}
