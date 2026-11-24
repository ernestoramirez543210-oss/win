// Prueba real de un proveedor: le pide una palabra y devuelve lo que conteste.
//
// Existe porque "guardado" no significa "funciona": una llave con typo, un modelo que ya
// no existe o una cuota agotada solo se ven al llamar de verdad. Sin este boton el primer
// aviso de que la llave no sirve seria una corrida del agente en silencio a las 3 a.m.

import { exigirDueno } from '../_sesion.js';
import { llamarLLM } from '../_llm.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirDueno(request, env))) return json(401, { ok: false, error: 'Necesitas entrar como dueno' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const prov = (ajustes.proveedores || []).find((p) => p.id === body.id);
  if (!prov) return json(404, { ok: false, error: 'Ese proveedor no existe' });

  const llave = await env.SUITE.get('secreto:' + prov.id);
  // Se puede probar un modelo SIN dejarlo puesto. Google retira modelos sin avisar y su
  // catalogo los sigue listando: gemini-2.0-flash-lite primero y 2.5-flash-lite despues
  // aparecian en el selector y solo al generar contestaban "no longer available". Fijar
  // uno para averiguar si vive dejaba la suite rota mientras tanto.
  const prueba = body.modelo ? { ...prov, modelo: String(body.modelo).slice(0, 120) } : prov;
  const r = await llamarLLM(
    prueba, llave,
    'Contesta con una sola palabra, sin puntuacion.',
    'Responde exactamente: listo',
    // 20 tokens alcanzaban para "listo" pero no para un modelo que ademas razona antes.
    // Un margen holgado cuesta una fraccion de centavo y evita un falso negativo.
    { maxTokens: 200, temperatura: 0 }
  );
  if (!r.ok) return json(200, { ok: false, error: r.error });
  return json(200, { ok: true, respuesta: r.texto.trim().slice(0, 60) });
}
