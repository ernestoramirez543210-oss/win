// El avatar: quien es exactamente la persona que compra, antes de escribir una linea.
//
// El brief del paso 1 ya trae un "publico", pero es una etiqueta: "mujeres de 30 a 50 que
// cocinan a diario". Con eso el contenido sale correcto y generico. Lo que afina el texto
// es saber a que hora abre el archivo, que se le quemo la ultima vez, que palabra no
// entiende y que la haria cerrar el PDF y no volver.
//
// Va aqui y no dentro del brief porque se revisa y se corrige a mano: es el paso donde el
// que conoce al cliente mete lo que el modelo no puede deducir. Cuesta centesimas de
// centavo y se paga solo en el contenido que evita reescribir.

import { exigirAlguno, EQUIPO } from '../_sesion.js';
import { llamarLLM, conEnv, jsonDeTexto } from '../_llm.js';
import { resumirGasto } from '../_gasto.js';
import { leerConocimiento, conocimientoTexto } from './conocimiento.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

const ESQUEMA = `{
  "nombre": "un nombre propio y una edad, ej. 'Marta, 42'",
  "situacion": "en dos frases: con quien vive, en que trabaja, como es su cocina o su espacio",
  "diaTipico": "el momento exacto en que va a usar esto, con la hora y lo que esta pasando alrededor",
  "dolor": "lo que le duele HOY, dicho como lo diria ella, no en lenguaje de marketing",
  "yaIntento": ["2 o 3 cosas que ya probo y no le funcionaron"],
  "objeciones": ["2 o 3 razones por las que dudaria de comprar o de usarlo"],
  "nivel": "que tanto sabe del tema: nada, algo, bastante. Y que da por sabido y que no",
  "palabras": ["3 a 5 palabras o expresiones que ella usa y que conviene usar en el texto"],
  "loQueLaPierde": "que la haria cerrar el archivo y no volver a abrirlo",
  "comoLeHablo": "el tono exacto: tuteo o usted, cercano o formal, con o sin humor",
  "porQuePagaria": "ella YA tiene el archivo completo y nadie la obliga a pagar: que la mueve a pagar igual, dicho como lo pensaria ella",
  "porQueNoPagaria": "la excusa concreta con la que se queda callada y no vuelve a escribir"
}`;

// El avatar en texto plano, listo para meter en cualquier prompt. Lo usan el escritor de
// contenido y el de mensajes: si cada uno lo formateara a su manera, el contenido y los
// mensajes acabarian hablandole a dos personas distintas.
export function avatarTexto(a) {
  if (!a || !a.nombre) return '';
  const linea = (etiqueta, v) => {
    const t = Array.isArray(v) ? v.filter(Boolean).join(' | ') : String(v || '').trim();
    return t ? etiqueta + ': ' + t + '\n' : '';
  };
  return 'QUIEN LO VA A LEER (escribe para ella, no para un publico)\n' +
    linea('Es', a.nombre) +
    linea('Situacion', a.situacion) +
    linea('Cuando lo usa', a.diaTipico) +
    linea('Lo que le duele', a.dolor) +
    linea('Ya intento', a.yaIntento) +
    linea('Duda de', a.objeciones) +
    linea('Nivel', a.nivel) +
    linea('Habla asi', a.palabras) +
    linea('La pierdes si', a.loQueLaPierde) +
    linea('Tono', a.comoLeHablo) +
    // Aqui se entrega antes de cobrar, asi que el momento que decide la facturacion no es
    // la compra: es cuando ella ya tiene el archivo y decide si paga. Sin estas dos lineas,
    // la pagina de cierre y los mensajes de cobro le hablan a alguien que aun no recibio.
    linea('Paga porque', a.porQuePagaria) +
    linea('No paga si', a.porQueNoPagaria);
}

async function proveedor(env, col) {
  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const prov = conEnv((ajustes.proveedores || []).find((p) => p.id === ajustes.proveedorActivo), env, col);
  if (!prov) return { error: 'No hay proveedor de texto activo. Configuralo en APIs.' };
  const llave = await env.SUITE.get('secreto:' + prov.id);
  if (!llave) return { error: `El proveedor "${prov.nombre}" no tiene API key.` };
  return { prov, llave };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'POST only' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirAlguno(request, env, EQUIPO))) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const brief = body.brief;
  if (!brief || !brief.titulo) return json(200, { ok: false, error: 'Falta el brief del paso 1.' });

  // Modo lote: ver resumirGasto en functions/_gasto.js.
  const col = body.gastoLote === true ? [] : null;
  const cfg = await proveedor(env, col);
  if (cfg.error) return json(200, { ok: false, error: cfg.error });

  // Se le pasa el nucleo del negocio y el publico: quien compra en Venezuela no es quien
  // compra en Colombia, y eso no se deduce del titulo del entregable.
  const cerebro = conocimientoTexto(await leerConocimiento(env), 'oferta');

  const sistema = `Conoces a los clientes de este negocio mejor que nadie:

${cerebro}

Tu trabajo es describir a UNA persona concreta, no a un segmento. Nada de "personas que
buscan mejorar su alimentacion": una persona, con nombre, hora y cocina.
Responde SIEMPRE en JSON valido, sin texto alrededor.`;

  const instruccion = `Describe a la persona que va a comprar y usar este entregable.

ENTREGABLE
Titulo: ${brief.titulo}
Promesa: ${brief.promesa || ''}
Publico segun el brief: ${brief.publico || ''}
Pais: ${brief.pais || ''}
Angulo de entrada: ${brief.angulo || ''}
Tipo: ${brief.tipoEntregable || ''}
${body.notas ? '\nLo que el equipo sabe de ella y el modelo no puede deducir: ' + body.notas + '\n' : ''}
Reglas:
- Habla de ella en concreto. Si dudas entre dos perfiles, elige el que compra mas seguido.
- Aqui se entrega el contenido ANTES de cobrar: cuando ella decide si paga, ya tiene el
  archivo completo y nadie la persigue. "porQuePagaria" y "porQueNoPagaria" son sobre ESE
  momento, no sobre la compra. No pongas "porque es barato": piensa que la haria a ella,
  con su vida y su plata, abrir la aplicacion del banco teniendo ya lo que queria.
- El dolor y las palabras van en su idioma real, con los modismos del pais que dice el brief.
- Nada de halagos ni de lenguaje de agencia.

Devuelve JSON con esta forma exacta:
${ESQUEMA}`;

  const r = await llamarLLM(cfg.prov, cfg.llave, sistema, instruccion,
    { maxTokens: 1400, temperatura: 0.6, json: true });
  if (!r.ok) return json(200, { ok: false, error: r.error });

  const avatar = jsonDeTexto(r.texto);
  if (!avatar || !avatar.nombre) {
    return json(200, { ok: false, error: 'El modelo no devolvio un avatar legible: ' + (r.texto || '').slice(0, 160) });
  }
  return json(200, { ok: true, avatar, ...(col ? { gasto: resumirGasto(col) } : {}) });
}
