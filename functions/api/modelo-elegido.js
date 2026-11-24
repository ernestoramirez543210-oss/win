// Deja que el dueno O el editor cambien el modelo de texto o de imagen con el que se
// crean los ebooks, sin abrir el resto de /api/config -llaves, proveedores, carpeta de
// Drive-, que sigue siendo exclusivo del dueno.
//
// Antes esto vivia dentro de /api/config, asi que solo el dueno podia bajar el costo del
// creador de ebooks. Quien arma las ofertas -el editor- es quien mas lo necesita, y no
// tiene por que poder tocar llaves para poder elegir el modelo barato.

import { exigirAlguno } from '../_sesion.js';

const CLAVE_AJUSTES = 'ajustes';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  const rol = await exigirAlguno(request, env, ['owner', 'editor']);
  if (!rol) return json(401, { ok: false, error: 'Entra como dueño o como editor' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const tipo = body.tipo;
  const modelo = String(body.modelo || '').trim().slice(0, 200);
  if (tipo !== 'texto' && tipo !== 'imagen') {
    return json(400, { ok: false, error: 'tipo debe ser "texto" o "imagen"' });
  }
  if (!modelo) return json(400, { ok: false, error: 'Falta el id del modelo' });

  const raw = await env.SUITE.get(CLAVE_AJUSTES);
  const ajustes = raw ? JSON.parse(raw) : {};

  if (tipo === 'texto') {
    // El modelo de texto vive DENTRO del proveedor activo -cada proveedor guarda el suyo,
    // para no perderlo si alguien cambia de proveedor y vuelve-, no en un campo suelto.
    const proveedores = ajustes.proveedores || [];
    const prov = proveedores.find((p) => p.id === ajustes.proveedorActivo);
    if (!prov) return json(400, { ok: false, error: 'No hay proveedor de texto activo. El dueño lo configura en APIs.' });
    prov.modelo = modelo;
  } else {
    // Solo el modelo de las paginas. modeloPortada, tipo y la llave se quedan como
    // estaban: este endpoint no los toca, a proposito.
    ajustes.imagenApi = ajustes.imagenApi || {};
    ajustes.imagenApi.modelo = modelo;
    // Una eleccion a mano manda: /api/modelos-imagen ya no vuelve a poner el barato encima.
    ajustes.imagenApi.baratoAplicado = true;
  }

  // Si KV rechaza la escritura -p. ej. el tope diario de escrituras-, una excepcion sin atrapar
  // llegaba al navegador como una pagina HTML de Cloudflare, y el "Usar los mas baratos" fallaba
  // con "Unexpected token <" sin decir por que. Se devuelve el motivo real.
  try {
    await env.SUITE.put(CLAVE_AJUSTES, JSON.stringify(ajustes));
  } catch (e) {
    return json(502, { ok: false, error: 'No se pudo guardar el modelo: ' + String((e && e.message) || e).slice(0, 200) });
  }
  return json(200, { ok: true });
}
