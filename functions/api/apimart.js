// APIMart en la suite: UNA llave para texto, imagen y video.
//
//   GET  /api/apimart                -> estado: si hay llave, que la usa, el catalogo por tipo y el saldo real
//   GET  /api/apimart?refrescar=1    -> igual, pidiendole de nuevo el catalogo a APIMart
//   POST /api/apimart { llave }      -> la valida con APIMart y la deja lista donde la suite la lee
//
// Por que existe: cada pantalla de la suite lee SU llave (la de cada proveedor de texto, la de imagenes, la de
// video). Con APIMart, que vende todo eso con una sola, el dueno tenia que pegarla en cada formulario, y la de
// Imagen ni siquiera salia en la lista de Proveedores. Aqui se pega una vez y se copia a los lugares que la
// necesitan, sin tocar lo que ya funciona con otro proveedor:
//   - texto:  si no hay un proveedor APIMart se crea uno (modelo del catalogo real). Solo pasa a ser el activo
//             si no habia ninguno: el activo lo usa todo el agente y cambiarlo gasta plata.
//   - imagen: se configura si no habia API de imagenes o ya era APIMart. Si hay Together o Replicate NO se pisa
//             su llave: se elige APIMart en Imagen cuando se quiera pasar, y ahi la llave ya esta.
//   - video:  se guarda para cuando la suite tenga generador, solo si el lugar estaba libre.
//
// La llave se valida ANTES de guardar (una llave rechazada no se guarda) y nunca vuelve al navegador: las
// respuestas llevan solo sus ultimos 4 caracteres. Solo el dueno: es una llave que gasta plata.

import { exigirDueno } from '../_sesion.js';
import {
  URL_VIDEO, limpiarLlave, llaveValida, ocultar, llaveApimart, esVideoApimart, pedirCatalogo, catalogoApimart,
  guardarCatalogo, saldoApimart, resumenCatalogo, elegirModeloTexto, elegirModeloImagen, MODELO_TEXTO_BASE, MODELO_IMAGEN_BASE,
} from '../_apimart.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

async function leerAjustes(env) {
  const raw = await env.SUITE.get('ajustes');
  if (!raw) return {};
  try { return JSON.parse(raw) || {}; } catch { return {}; }
}

// Quien usa APIMart hoy y con que llave (solo los 4 ultimos caracteres).
async function usosDe(env, a) {
  const texto = [];
  for (const p of (a.proveedores || [])) {
    if (!p || p.tipo !== 'apimart') continue;
    const k = await env.SUITE.get('secreto:' + p.id);
    texto.push({ id: p.id, nombre: p.nombre || 'APIMart', modelo: p.modelo || '', activo: p.id === a.proveedorActivo,
      tieneLlave: !!k, cola: k ? k.slice(-4) : '' });
  }
  const img = a.imagenApi || {};
  const imagen = { esApimart: img.tipo === 'apimart', otro: img.tipo && img.tipo !== 'apimart' ? img.tipo : '',
    modelo: img.tipo === 'apimart' ? (img.modelo || '') : '', tieneLlave: false, cola: '' };
  if (imagen.esApimart) {
    const k = await env.SUITE.get('secreto:imagen');
    imagen.tieneLlave = !!k; imagen.cola = k ? k.slice(-4) : '';
  }
  const vid = a.videoApi || {};
  const video = { esApimart: esVideoApimart(a), otro: (vid.nombre || vid.url) && !esVideoApimart(a) ? (vid.nombre || 'otro') : '',
    tieneLlave: false, cola: '' };
  if (video.esApimart) {
    const k = await env.SUITE.get('secreto:video');
    video.tieneLlave = !!k; video.cola = k ? k.slice(-4) : '';
  }
  return { texto, imagen, video };
}

async function estado(env, refrescar, extra) {
  const a = await leerAjustes(env);
  const usos = await usosDe(env, a);
  const llave = await llaveApimart(env, a);
  if (!llave) return Object.assign({ ok: true, conectada: false, usos }, extra || {});
  const cat = await catalogoApimart(env, llave, refrescar);
  const saldo = await saldoApimart(llave);
  return Object.assign({
    ok: true, conectada: true, cola: llave.slice(-4), usos,
    // La llave ya esta, pero falta dejarla en alguna parte (p. ej. la pegaron solo en Imagen y no hay proveedor
    // de texto): la pantalla ofrece completarlo con la llave guardada, sin pegarla otra vez.
    faltaCompletar: usos.texto.length === 0 || (!usos.imagen.esApimart && !usos.imagen.otro) || (!usos.video.esApimart && !usos.video.otro),
    catalogo: cat.ok ? resumenCatalogo(cat) : null,
    errorCatalogo: cat.ok ? '' : ocultar(cat.error || '', llave),
    llaveRechazada: !!cat.rechazada,
    saldo,
  }, extra || {});
}

async function conectar(env, cruda) {
  const llave = limpiarLlave(cruda);
  if (!llave) return json(400, { ok: false, error: 'Pega la llave de APIMart primero.' });
  if (!llaveValida(llave)) {
    return json(400, { ok: false, error: 'La llave tiene espacios o saltos de línea en medio, o está incompleta. Cópiala de nuevo desde apimart.ai/keys.' });
  }

  // La prueba de verdad: pedir el catalogo con ella. Una llave que APIMart rechaza no se guarda. Si APIMart no
  // contesta (caida, red) no se puede saber: se guarda igual y se avisa, para no dejar al dueno sin poder
  // pegarla justo cuando APIMart esta lento.
  const cat = await pedirCatalogo(llave);
  if (cat.rechazada) return json(200, { ok: false, error: ocultar(cat.error, llave) + ' No se guardó nada.' });
  const modelos = cat.ok ? cat.modelos : [];

  const a = await leerAjustes(env);
  const nuevo = Object.assign({}, a);
  const hecho = {};
  try {
    // ---- texto
    const proveedores = Array.isArray(a.proveedores) ? a.proveedores.slice() : [];
    let deApimart = proveedores.filter((p) => p && p.tipo === 'apimart');
    if (!deApimart.length) {
      const id = proveedores.some((p) => p && p.id === 'apimart') ? 'apimart-' + Date.now().toString(36) : 'apimart';
      const p = { id, nombre: 'APIMart', tipo: 'apimart', modelo: modelos.length ? elegirModeloTexto(modelos) : MODELO_TEXTO_BASE, url: '' };
      proveedores.push(p);
      deApimart = [p];
      if (!a.proveedorActivo) nuevo.proveedorActivo = id;
      hecho.texto = 'creado';
    } else {
      hecho.texto = 'actualizado';
    }
    nuevo.proveedores = proveedores;
    for (const p of deApimart) await env.SUITE.put('secreto:' + p.id, llave);

    // ---- imagen
    const img = (a.imagenApi && typeof a.imagenApi === 'object') ? a.imagenApi : {};
    if (!img.tipo || img.tipo === 'apimart') {
      nuevo.imagenApi = Object.assign({}, img, {
        tipo: 'apimart',
        modelo: img.modelo || (modelos.length ? elegirModeloImagen(modelos) : MODELO_IMAGEN_BASE),
        modeloPortada: img.modeloPortada || '', modeloMockup: img.modeloMockup || '',
      });
      await env.SUITE.put('secreto:imagen', llave);
      hecho.imagen = 'lista';
    } else {
      hecho.imagen = 'sigue con ' + img.tipo;
    }

    // ---- video: solo si el lugar estaba libre o ya era APIMart
    const vid = (a.videoApi && typeof a.videoApi === 'object') ? a.videoApi : {};
    const libre = !vid.url && !vid.nombre && !(await env.SUITE.get('secreto:video'));
    if (libre || esVideoApimart(a)) {
      nuevo.videoApi = { tipo: 'compatible', nombre: 'APIMart', url: URL_VIDEO };
      await env.SUITE.put('secreto:video', llave);
      hecho.video = 'lista';
    } else {
      hecho.video = 'sigue con ' + (vid.nombre || 'otro proveedor');
    }

    await env.SUITE.put('ajustes', JSON.stringify(nuevo));
  } catch (e) {
    return json(502, { ok: false, error: 'No se pudo guardar: ' + ocultar(String((e && e.message) || e).slice(0, 200), llave) });
  }
  if (cat.ok) await guardarCatalogo(env, cat.modelos);

  const n = cat.ok ? resumenCatalogo({ modelos: cat.modelos }).conteo : null;
  const mensaje = 'APIMart conectada.' + (n
    ? ' Tu llave ve ' + n.texto + ' modelos de texto, ' + n.imagen + ' de imagen, ' + n.video + ' de video y ' + n.audio + ' de audio.'
    : ' No pude traer la lista de modelos todavía (' + ocultar(cat.error || 'sin respuesta', llave) + '): la llave quedó guardada, pulsa «Actualizar» en un rato.');
  return json(200, await estado(env, false, { mensaje, hecho }));
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV. Corre scripts\\Deploy-Suite.ps1 y vuelve a desplegar.' });
  if (!(await exigirDueno(request, env))) return json(401, { ok: false, error: 'Necesitas entrar como dueño para conectar APIMart.' });

  if (request.method === 'GET') {
    const refrescar = new URL(request.url).searchParams.get('refrescar') === '1';
    return json(200, await estado(env, refrescar));
  }
  if (request.method === 'POST') {
    let body;
    try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
    // «Completar»: se reparte la llave que YA esta guardada (sin pegarla otra vez) a lo que le falte.
    if (body && body.usarGuardada) {
      const guardada = await llaveApimart(env, await leerAjustes(env));
      if (!guardada) return json(400, { ok: false, error: 'No hay una llave de APIMart guardada: pégala primero.' });
      return conectar(env, guardada);
    }
    return conectar(env, body && body.llave);
  }
  return json(405, { ok: false, error: 'Method not allowed' });
}
