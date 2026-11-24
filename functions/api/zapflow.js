// Crear una oferta en ZapData: el pedido se arma aqui y lo recoge quien tiene la conexion.
//
// Antes esto colgaba del creador de ebooks y tomaba el copy del paso 4, escrito por la IA
// oferta por oferta. Dos problemas. Uno, se mezclaban las dos cosas y no se sabia que se
// estaba creando. Dos, y mas caro: cada oferta estrenaba un copy nuevo, sin probar, cuando
// ya hay un guion que vende.
//
// Ahora el copy sale de functions/_oferta-plantilla.js, que es el guion de Murphy y
// Dinamicas leido bloque por bloque. Una oferta nueva son cinco datos. Lo que se prueba es
// la oferta, no la redaccion: es lo que permite testear en masa.
//
// El tablero SI le habla a ZapData, por el servidor MCP que ellos publican, con una
// credencial de token estatico que solo puede crear borradores. Ver functions/_zapdata.js.
// Antes dejaba el pedido en una cola que recogia alguien con una sesion de chat abierta, y
// eso convertia cada oferta en una cita con una persona. Quien crea las ofertas trabaja
// desde aqui y no tiene esa sesion: un pedido en cola que nadie recoge es un flujo que no
// existe. Lo que no se hace es escribir directo en su base de datos, que seria igual de
// instantaneo y se rompe en silencio el dia que cambien algo por dentro.
//
// Sin la credencial puesta esto no se cae: el pedido se guarda igual y queda en la cola.
//
// Las etiquetas llevan el nombre exacto de la oferta porque de ahi salen dos cosas rio
// abajo: scripts/zapdata-webhook.gs deduce de "Compra realizada X" en que pestana del libro
// va la venta, y el dashboard le cuelga el gasto de las campanas que nombren esas mismas
// palabras. Cambiar el nombre aqui parte las dos cosas.

import { readSession } from '../_sesion.js';
import { flujoVenta, flujoEntrega, promesaCortaSegura } from '../_oferta-plantilla.js';
import { leerPlantilla } from './plantilla.js';
import { leerPaises, precio, completo, monedaPixel } from '../_paises.js';
import { crearFlujo, hayToken } from '../_zapdata.js';

const PREFIJO = 'zapflow:';
const SUBIDOS = 'zaparch:indice';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Lo que cambia de un pais a otro -precios, Pixel, datos de pago- ya no vive aqui: se
// configura en la pestaña Paises y se guarda en KV. Ver functions/_paises.js. Estaba escrito
// a mano con Colombia y Mexico, y eso funciona mientras sean dos: ya son siete.


async function subidos(env) {
  const raw = await env.SUITE.get(SUBIDOS, { type: 'json' });
  return Array.isArray(raw) ? raw : [];
}

const sinTildes = (s) => String(s || '').normalize('NFD');

// Las palabras que disparan el flujo salen del nombre. Murphy dispara con "ley", "murphy"
// y "murpy" -el error de tecleo incluido, que es de donde llega media gente-. Aqui se
// proponen todas las palabras utiles del nombre y se pueden editar antes de crear.
const RELLENO = ['de', 'del', 'la', 'el', 'los', 'las', 'para', 'con', 'sin', 'una', 'uno'];
function clavesDe(titulo) {
  const p = sinTildes(titulo).toLowerCase().split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 3 && RELLENO.indexOf(w) === -1);
  return p.length ? p : [sinTildes(titulo).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 12)];
}

// El nombre del archivo ES el nombre que sale en el copy.
//
// Nadie va a escribir dos veces la lista de lo que entrega: ya la escribio al nombrar los
// PDF. "BONO-1-REINICIA-TU-MENTALIDAD.pdf" se convierte en "Reinicia tu mentalidad" y con
// eso se arma la lista de regalos del mensaje del precio. Se le quita la extension, los
// guiones, y el "BONO 3" del principio, que numera el archivo pero no dice nada de el.
export function nombreBonito(archivo) {
  let s = String(archivo || '').replace(/\.[a-z0-9]{1,5}$/i, '')
    .replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
    .replace(/^bono\s*n?\s*\d+\s*[:.·-]?\s*/i, '')
    .replace(/^\d+\s*[:.·-]\s*/, '');
  // Los nombres de archivo suelen venir a gritos. Se bajan y se sube solo la primera,
  // porque "REINICIA TU MENTALIDAD" en medio de un mensaje se lee como un regano.
  if (s === s.toUpperCase() && /[A-Z]/.test(s)) s = s.toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function resolverArchivos(lista, extra, origen) {
  const out = [];
  (Array.isArray(lista) ? lista : []).forEach((a) => {
    if (typeof a === 'string') {
      const sub = extra.find((x) => x.id === a);
      if (sub) { out.push({ nombre: sub.nombre, url: origen + '/api/zaparchivo?id=' + sub.id }); return; }
      if (a.trim()) out.push({ nombre: nombreBonito(a), url: '' });
    } else if (a && a.nombre) {
      out.push({ nombre: String(a.nombre).trim(), url: String(a.url || '').trim() });
    }
  });
  return out;
}

async function guardar(pedido, env, cambios) {
  Object.assign(pedido, cambios, { tocadoEn: new Date().toISOString() });
  await env.SUITE.put(PREFIJO + pedido.id, JSON.stringify(pedido));
}

// Crear los flujos en ZapData de verdad, uno por uno, guardando el avance despues de cada
// uno. No se hace dentro de la respuesta a proposito: son varios flujos y el navegador se
// quedaria esperando sin saber en que va. El tablero ya tiene el numero del pedido y
// pregunta por el cada pocos segundos, asi que la barra se mueve con esto de aqui.
//
// Si uno falla se para y se deja escrito cuantos alcanzaron a crearse. Lo contrario -seguir
// como si nada- deja media oferta montada y nadie se entera hasta que un lead escribe.
async function montar(pedido, env) {
  const total = pedido.flujosZap.length;
  // Al reintentar se sigue desde donde quedo. Crear otra vez los que ya existen deja el
  // panel con dos "Venta Cosmetica Colombia" y a alguien decidiendo cual borrar.
  const creados = Array.isArray(pedido.creados) ? pedido.creados.slice() : [];
  await guardar(pedido, env, {
    estado: 'creando',
    nota: creados.length ? 'Siguiendo desde el ' + (creados.length + 1) + ' de ' + total + '…'
      : 'Creando el primero de ' + total + '…',
  });
  try {
    for (let i = creados.length; i < total; i++) {
      const r = await crearFlujo(pedido.flujosZap[i], env);
      creados.push(r);
      await guardar(pedido, env, { creados, nota: (i + 1) + ' de ' + total + ' creados en ZapData.' });
    }
  } catch (e) {
    await guardar(pedido, env, {
      creados, estado: 'trabado',
      nota: 'Se crearon ' + creados.length + ' de ' + total + '. ' + (e && e.message ? e.message : e),
    });
    return;
  }
  await guardar(pedido, env, {
    creados, estado: 'hecho', hechoEn: new Date().toISOString(),
    nota: 'Los ' + total + ' flujos ya existen en ZapData, apagados y sin número.',
  });
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  if (true) {
    const rol = env.SESSION_SECRET
      ? await readSession(request.headers.get('Cookie'), env.SESSION_SECRET)
      : null;
    if (!rol) return json(401, { ok: false, error: 'No autenticado' });
  }

  if (request.method === 'GET') {
    const q = new URL(request.url).searchParams;
    if (q.get('catalogo')) {
      // Los datos de pago NO salen de aqui, solo si estan puestos o no: quien crea ofertas
      // no necesita ver numeros de cuenta para hacer su trabajo.
      const paises = await leerPaises(env);
      return json(200, {
        ok: true,
        paises: paises.map((p) => ({
          nombre: p.nombre, moneda: p.moneda, pixel: p.pixelNombre || p.pixel,
          ancla: p.ancla, aporte1: p.aporte1, aporte2: p.aporte2,
          listo: completo(p),
        })),
      });
    }
    // Un pedido suelto, por su id. Es lo que consulta el tablero cada pocos segundos
    // mientras espera: pedir la lista entera para mirar un solo estado sale caro y el
    // navegador lo repite mientras la pestana este abierta.
    if (q.get('id')) {
      const p = await env.SUITE.get(PREFIJO + q.get('id'), { type: 'json' });
      if (!p) return json(404, { ok: false, error: 'No existe el pedido ' + q.get('id') });
      return json(200, {
        ok: true,
        pedido: {
          id: p.id, oferta: p.oferta, estado: p.estado || 'pendiente', nota: p.nota || '',
          creadoEn: p.creadoEn, tocadoEn: p.tocadoEn || p.hechoEn || p.creadoEn,
          paises: p.paises, flujos: (p.flujosZap || []).map((f) => f.name), creados: p.creados || [],
          pagos: (p.paises || []).map((x) => (p.porPais && p.porPais[x] && p.porPais[x].pagos) || null),
        },
      });
    }
    const lista = await env.SUITE.list({ prefix: PREFIJO, limit: 200 });
    const pedidos = (await Promise.all(
      lista.keys.map((k) => env.SUITE.get(k.name, { type: 'json' }))
    )).filter(Boolean).sort((a, b) => String(b.creadoEn).localeCompare(String(a.creadoEn)));
    const solo = q.get('estado');
    const filtrados = solo ? pedidos.filter((p) => p.estado === solo) : pedidos;
    // La cola que se dibuja en el tablero no necesita los bloques de cada flujo, que son la
    // mayor parte del pedido. Solo el nombre y en que va.
    if (q.get('cola')) {
      return json(200, {
        ok: true,
        pedidos: filtrados.slice(0, 12).map((p) => ({
          id: p.id, oferta: p.oferta, estado: p.estado || 'pendiente', nota: p.nota || '',
          creadoEn: p.creadoEn, tocadoEn: p.tocadoEn || p.hechoEn || p.creadoEn,
          paises: p.paises, flujos: (p.flujosZap || []).map((f) => f.name), creados: p.creados || [],
        })),
      });
    }
    return json(200, { ok: true, pedidos: filtrados });
  }
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  // En que va el pedido. Lo mueve quien lo esta creando en ZapData, y es lo unico que ve
  // quien armo la oferta: sin esto el tablero se queda diciendo "guardado" para siempre y
  // desde el otro lado parece que no paso nada. Los estados son estos cuatro y no mas:
  //   pendiente  el pedido esta guardado y nadie lo ha recogido
  //   creando    alguien lo esta armando en ZapData ahora mismo
  //   hecho      los flujos existen, con su numero, probados
  //   trabado    se intento y no se pudo; en 'nota' queda por que
  const ESTADOS = ['pendiente', 'creando', 'hecho', 'trabado'];
  if (body.accion === 'hecho' || body.accion === 'estado') {
    const p = await env.SUITE.get(PREFIJO + body.id, { type: 'json' });
    if (!p) return json(404, { ok: false, error: 'No existe el pedido ' + body.id });
    const nuevo = body.accion === 'hecho' ? 'hecho' : String(body.estado || '');
    if (ESTADOS.indexOf(nuevo) < 0) {
      return json(400, { ok: false, error: 'Estado desconocido: ' + nuevo });
    }
    p.estado = nuevo;
    p.tocadoEn = new Date().toISOString();
    if (nuevo === 'hecho') p.hechoEn = p.tocadoEn;
    if (body.nota !== undefined) p.nota = String(body.nota);
    if (body.enlaces) p.enlaces = body.enlaces;
    await env.SUITE.put(PREFIJO + body.id, JSON.stringify(p));
    return json(200, { ok: true, pedido: { id: p.id, estado: p.estado, nota: p.nota || '' } });
  }
  if (body.accion === 'borrar') {
    await env.SUITE.delete(PREFIJO + body.id);
    return json(200, { ok: true });
  }

  // Volver a intentarlo sin rehacer el formulario. Un pedido se traba por cosas que se
  // arreglan afuera y en un minuto -la credencial vencida, ZapData caido, un archivo
  // pesado- y obligar a llenar la oferta otra vez para reintentar es perder el trabajo dos
  // veces. Sigue desde donde quedo: no vuelve a crear los flujos que ya existen.
  if (body.accion === 'reintentar') {
    const p = await env.SUITE.get(PREFIJO + body.id, { type: 'json' });
    if (!p) return json(404, { ok: false, error: 'No existe el pedido ' + body.id });
    if (!(await hayToken(env))) {
      return json(400, { ok: false, error: 'Falta la credencial de ZapData. Se pega en la pestaña Conexión.' });
    }
    context.waitUntil(montar(p, env));
    return json(200, { ok: true, pedido: { id: p.id, estado: 'creando' } });
  }

  // Duplicar una oferta a otro pais.
  //
  // La oferta es la misma -mismo copy, mismos archivos, mismos audios- y lo unico que
  // cambia es el pais: sus precios, su Pixel, sus datos de pago y sus etiquetas. Rehacer el
  // formulario para eso era volver a adjuntar los PDF y volver a escribir las viñetas, con
  // el riesgo de que la version de Mexico terminara diciendo algo distinto a la de Colombia
  // por un error de tecleo. Aqui se arma el mismo pedido otra vez con el pais nuevo.
  if (body.accion === 'duplicar') {
    const p = await env.SUITE.get(PREFIJO + body.id, { type: 'json' });
    if (!p) return json(404, { ok: false, error: 'No existe el pedido ' + body.id });
    const destino = String(body.pais || '').trim();
    if (!destino) return json(400, { ok: false, error: 'Falta a qué país se duplica.' });
    if ((p.paises || []).indexOf(destino) >= 0) {
      return json(400, { ok: false, error: 'Esa oferta ya tiene flujos en ' + destino + '.' });
    }
    const o = p.origen || {};
    // Se puede duplicar con otro nombre: sirve para una prueba, y para la misma oferta
    // vendida con otro titulo en otro pais. El nombre manda en las etiquetas y en el nombre
    // de los flujos, asi que con nombre nuevo las palabras que disparan salen de ese nombre
    // y no se pisan con las de la oferta original.
    const nombre = String(body.nombre || '').trim();
    body = {
      oferta: nombre || p.oferta, claves: nombre ? [] : p.claves, promesa: p.promesa, persona: p.persona,
      vinetas: p.vinetas, paises: [destino],
      archivos: p.archivos, bonos: p.bonosArchivos,
      promesaCorta: o.promesaCorta || '', portada: o.portada || '',
      audio1: o.audio1 || '', audio2: o.audio2 || '', enlaces: o.enlaces || [],
      bonosEnlaces: o.bonosEnlaces || [],
      // Los precios NO se copian: son del pais nuevo, y estan en la pestaña Paises.
      precios: {},
      duplicadoDe: p.id,
    };
  }

  const oferta = String(body.oferta || '').trim();
  if (!oferta) return json(400, { ok: false, error: 'Falta el nombre de la oferta.' });
  const promesa = String(body.promesa || '').trim();
  if (!promesa) return json(400, { ok: false, error: 'Falta la promesa: es la frase que hace que la persona siga leyendo.' });
  const vinetas = (Array.isArray(body.vinetas) ? body.vinetas : String(body.vinetas || '').split('\n'))
    .map((v) => String(v).trim()).filter(Boolean);
  if (!vinetas.length) return json(400, { ok: false, error: 'Falta qué recibe: sin eso el mensaje del precio queda vacío.' });

  const config = await leerPaises(env);
  const porNombre = {};
  config.forEach((c) => { porNombre[c.nombre] = c; });
  const paises = (Array.isArray(body.paises) ? body.paises : []).filter((p) => porNombre[p]);
  if (!paises.length) return json(400, { ok: false, error: 'Elige al menos un país.' });
  // Un pais a medias no puede generar un flujo: sin Pixel no se registra la compra, sin
  // precios el mensaje sale vacio y sin datos de pago no hay a donde pagar.
  const aMedias = paises.filter((p) => !completo(porNombre[p]));
  if (aMedias.length) {
    return json(400, { ok: false, error: 'Falta configurar ' + aMedias.join(' y ') + ' en la pestaña Países.' });
  }

  const extra = await subidos(env);
  const origen = new URL(request.url).origin;
  const archivos = resolverArchivos(body.archivos, extra, origen);
  const bonosArchivos = resolverArchivos(body.bonos, extra, origen);
  const enlaces = (Array.isArray(body.enlaces) ? body.enlaces : []).filter((e) => e && e.url);
  const bonosEnlaces = (Array.isArray(body.bonosEnlaces) ? body.bonosEnlaces : []).filter((e) => e && e.url);
  // El material antes del pago puede ser un PDF subido o un enlace (carpeta de Drive):
  // cualquiera de los dos cuenta, exigir siempre un archivo bloqueaba las ofertas por enlace.
  if (!archivos.length && !enlaces.length) {
    return json(400, { ok: false, error: 'Falta el material que se entrega antes del pago.' });
  }

  const claves = (Array.isArray(body.claves) && body.claves.length
    ? body.claves : clavesDe(oferta)).map((c) => String(c).trim().toLowerCase()).filter(Boolean);

  // El copy editado en la pestaña Plantilla. Si nunca se toco, viene vacio y el guion sale
  // con los textos base.
  const plantilla = await leerPlantilla(env);

  const flujosZap = [];
  const porPais = {};
  for (const pais of paises) {
    const cfg = porNombre[pais];
    const pr = (body.precios && body.precios[pais]) || {};
    // Los precios son del pais y son los mismos en todas sus ofertas, asi que si no
    // llegan se usan los suyos. No es inventarlos: son los que estan escritos en los
    // flujos que corren hoy.
    const ancla = String(pr.ancla || '').trim() || cfg.ancla;
    const aporte1 = String(pr.aporte1 || '').trim() || cfg.aporte1;
    const aporte2 = String(pr.aporte2 || '').trim() || cfg.aporte2;
    const d = {
      oferta, pais, claves,
      persona: String(body.persona || 'Laura').trim(),
      promesa,
      // Lo que va despues de «la herramienta» en el mensaje de refuerzo: una frase corta con «para».
      // Pegar la promesa entera rompia el mensaje (ver promesaCortaSegura).
      promesaCorta: promesaCortaSegura(body.promesaCorta, promesa, oferta),
      vinetas,
      // La lista de regalos del mensaje del precio sale de los archivos que se entregan
      // con el pago. Si se adjuntan tres, se prometen tres: no hay forma de prometer un
      // bono que despues no llega.
      bonos: bonosArchivos.map((b) => nombreBonito(b.nombre)),
      ancla, aporte1, aporte2,
      anclaTxt: precio(cfg, ancla), aporte1Txt: precio(cfg, aporte1), aporte2Txt: precio(cfg, aporte2),
      moneda: cfg.moneda, pixel: cfg.pixel, monedaPixel: monedaPixel(cfg),
      bancos: cfg.bancos || '',
      archivos, bonosArchivos,
      portada: String(body.portada || '').trim(),
      audio1: String(body.audio1 || '').trim(),
      audio2: String(body.audio2 || '').trim(),
      enlaces, bonosEnlaces,
    };
    const venta = flujoVenta(d, plantilla);
    const entrega = flujoEntrega(d, env.ZAPDATA_WEBHOOK_URL || '', plantilla);
    flujosZap.push(venta, entrega);
    porPais[pais] = {
      ancla, aporte1, aporte2, moneda: cfg.moneda, pixel: cfg.pixel,
      datosPago: !!cfg.bancos,
      // Los dos bloques que hay que agregarle al flujo de pagos del pais, con los nombres
      // exactos para no tener que deducirlos delante del lienzo.
      pagos: {
        flujo: cfg.flujoPagos,
        // El identificador, ademas del nombre: es lo que usa el boton de terminar para abrir
        // el flujo de pagos del pais y colgarle la rama sin buscarlo a mano.
        id: cfg.flujoPagosId || '',
        condicion: 'Tiene la etiqueta "Pago Pendiente ' + oferta + '"',
        salto: entrega.name,
      },
    };
  }

  // Vista previa: los mismos mensajes que va a recibir la clienta, sin guardar nada. Es lo
  // que convierte el formulario en algo que se puede ajustar mirando, en vez de a ciegas.
  if (body.accion === 'previo') {
    const v = flujosZap[0];
    return json(200, {
      ok: true,
      // El «Texto con IA» (aiText) del saludo se muestra como un texto mas: el tablero pinta
      // cualquier tipo que no sea 'text' como archivo adjunto.
      previo: v.blocks.filter((b) => b.type === 'text' || b.type === 'aiText' || b.type === 'audio' || b.type === 'document')
        .map((b) => ({
          tipo: b.type === 'aiText' ? 'text' : b.type,
          texto: (b.type === 'text' || b.type === 'aiText') ? b.data.message : (b.data.fileName || 'audio de ' + (body.persona || 'Laura')),
          nota: b.todo || (b.type === 'aiText' ? 'Texto con IA: se reescribe un poco para cada persona.' : ''),
        })),
      bloques: flujosZap.reduce((n, f) => n + f.blocks.length, 0),
    });
  }

  const faltan = archivos.concat(bonosArchivos).filter((a) => !a.url).map((a) => a.nombre);
  const pedido = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    estado: 'pendiente',
    creadoEn: new Date().toISOString(),
    oferta, claves, paises, porPais,
    persona: String(body.persona || 'Laura').trim(),
    promesa, vinetas, archivos, bonosArchivos,
    // Lo que hace falta para volver a armar esta misma oferta en otro pais sin rehacer el
    // formulario. Sin esto, duplicar perdia la portada, los audios y los enlaces.
    origen: {
      promesaCorta: String(body.promesaCorta || '').trim(),
      portada: String(body.portada || '').trim(),
      audio1: String(body.audio1 || '').trim(),
      audio2: String(body.audio2 || '').trim(),
      enlaces, bonosEnlaces,
    },
    duplicadoDe: body.duplicadoDe || '',
    // Los pasos que hay que dar DESPUES de crear el flujo en ZapData, escritos en el
    // propio pedido para que no dependan de que alguien se acuerde. El primero no es
    // opcional: sin el, el flujo se crea, se activa, abre la sesion del contacto y no
    // manda un solo mensaje. Paso con las seis primeras ofertas.
    // Lo que hay que hacer DESPUES, y por que sigue sin poder hacerse solo.
    //
    // El primero es el unico que hoy hace que un flujo recien creado no arranque, y esta
    // confirmado, no supuesto: un flujo que funciona desde hace meses tiene el bloque de
    // inicio llamado start-1, y los que crea su API lo traen con la fecha pegada. El motor
    // abre la conversacion, la marca como "enviando" y no manda un solo mensaje. Su API
    // solo sabe crear flujos, no editarlos, asi que el tablero no lo puede corregir solo.
    //
    // Antes esto se hacia con un marcador (bookmarklet) que el dueño arrastraba y tocaba a
    // mano en ZapData. Se quito: fallaba en silencio mas de una vez. Ahora se le pide a
    // Claude que lo haga -tiene la tecnica documentada en la skill mi-dashboard- con los
    // enlaces de abajo.
    pasosDespues: [
      "Pedirle a Claude que termine en ZapData (Fábrica de ofertas › «Copiar la orden para Claude»): deja el inicio start-1 en el flujo de Venta y cuelga la rama en el flujo de Pagos del país, en segundos y verificado recargando. Sin esto el flujo se activa y no manda nada, y quien pague no recibe el material.",
      "Revisar los mensajes y el audio en cada flujo antes de soltarlo.",
      "Asignarle el número de WhatsApp que corresponde y activarlo.",
      "Probar escribiendo una palabra clave desde otro número ANTES de darlo por bueno."
    ],
    flujosZap,
  };
  await env.SUITE.put(PREFIJO + pedido.id, JSON.stringify(pedido));

  // Con credencial, el tablero los crea el mismo y el pedido avanza solo. Sin ella queda
  // en la cola como antes, que es lo que hay que evitar: un pedido en cola que nadie
  // recoge es un flujo que no existe.
  const automatico = await hayToken(env);
  if (automatico) context.waitUntil(montar(pedido, env));

  return json(200, {
    ok: true,
    pedido: {
      id: pedido.id, oferta, claves, paises, pasos: pedido.pasosDespues,
      automatico,
      flujos: flujosZap.map((f) => f.name),
      bloques: flujosZap.reduce((n, f) => n + f.blocks.length, 0),
      faltan,
      sinDatosPago: paises.filter((p) => !porPais[p].datosPago),
      pagos: paises.map((p) => porPais[p].pagos),
    },
  });
}
