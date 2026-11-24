// El mockup que va al inicio de cada oferta (el "paquete de libros" que hoy se sube a
// mano). Se arma en dos pasos, como pidio: primero se escribe el prompt con la IA de
// texto, y con ese prompt se genera la imagen con el proveedor de imagenes ya conectado
// (Together). Los dos pasos ocurren con un solo clic: no hay nada manual entre medio.
//
// El estilo no se inventa: sale de comparar varios mockups que ya venden (recetas
// biblicas, formulas de cupcakes, cosmetica natural, jabones terapeuticos, kinesiotaping)
// y quedarse con lo que los cinco repiten -- el producto principal al centro con su titulo
// bien legible, entre uno y tres bonos alrededor con su propia etiqueta "BONO N", algun
// sello o cinta que refuerce la oferta, y un fondo de estudio que combina con el tema sin
// robarle protagonismo al producto. Por eso el prompt de abajo no dice "hazlo bonito": le
// da esa composicion exacta y solo cambia el tema, los colores y que va en cada bono.
//
// El texto adentro de la imagen es justo lo dificil: la mayoria de modelos de imagen
// escriben letras deformes. Por eso, si no se eligio otro modelo en APIs, aqui se usa de
// una vez el mejor de la familia GPT que sirve Together (ver MEJOR_GPT_TOGETHER), que es
// la que mejor saca el texto nitido.
//
// Las instrucciones de arriba (la composicion) no estan fijas en este archivo: viven en
// mockup-plantilla.js y son editables desde Flujos ZapData -> pestaña Mockup, con el mismo
// mecanismo que la pestaña Plantilla del guion de texto.

import { exigirEquipo } from '../_sesion.js';
import { llamarLLM, conEnv } from '../_llm.js';
import { generarImagen } from './ebook-pagina.js';
import { leerInstruccionesMockup } from './mockup-plantilla.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Cuadrado: es la relacion que usan los cinco ejemplos (o muy cerca de 1:1), y es la que
// mejor deja acomodar un producto central con bonos a los lados sin recortar nada.
const LADO = 1024;

// El mockup lleva bastante texto DENTRO de la imagen (titulo, vinetas de los bonos), y ahi
// es donde mas se nota la diferencia entre un modelo generico y uno bueno con texto. Si no
// se eligio un modelo aparte para mockups en APIs, no se cae al barato de las paginas: se
// usa de una vez el mejor de la familia GPT que sirve Together, que es justo la familia
// pensada para que el texto salga nitido en vez de deformado.
const MEJOR_GPT_TOGETHER = 'openai/gpt-image-2';

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
  const bonos = (Array.isArray(body.bonos) ? body.bonos : []).map((v) => String(v).trim()).filter(Boolean).slice(0, 3);
  // Un prompt ya editado a mano no se vuelve a escribir: se usa tal cual llego, y solo se
  // genera la imagen con el.
  let prompt = String(body.prompt || '').trim();

  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};

  if (!prompt) {
    const prov = conEnv((ajustes.proveedores || []).find((p) => p.id === ajustes.proveedorActivo), env);
    if (!prov) return json(200, { ok: false, error: 'No hay proveedor de texto activo. Configuralo en APIs.' });
    const llave = await env.SUITE.get('secreto:' + prov.id);
    if (!llave) return json(200, { ok: false, error: `"${prov.nombre}" no tiene API key guardada.` });

    const sistema = await leerInstruccionesMockup(env);
    const mensaje = 'Producto: ' + oferta + '\nPromesa: ' + promesa +
      (bonos.length ? '\nBonos que van alrededor del producto: ' + bonos.join(', ') : '\nSin bonos: solo el producto principal.');
    const r = await llamarLLM(prov, llave, sistema, mensaje, { maxTokens: 500, temperatura: 0.8 });
    if (!r.ok) return json(200, { ok: false, error: r.error });
    prompt = r.texto.trim().replace(/^["']|["']$/g, '');
  }

  const imgCfg = ajustes.imagenApi || {};
  const modeloMockup = imgCfg.modeloMockup || (imgCfg.tipo === 'together' ? MEJOR_GPT_TOGETHER : undefined);
  const img = await generarImagen(env, prompt, {
    encuadre: 'propio', ancho: LADO, alto: LADO, aspecto: '1:1', modelo: modeloMockup,
  });
  if (!img.ok) return json(200, { ok: false, error: img.error, prompt });

  return json(200, {
    ok: true, prompt, base64: img.base64,
    modelo: img.modelo || null, cambioDeModelo: img.cambioDeModelo || null,
  });
}
