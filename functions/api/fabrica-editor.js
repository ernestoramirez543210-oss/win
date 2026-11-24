// La puerta de Claudia (el editor de anuncios en el PC del editor) a la cola de la Fabrica de ofertas.
//
//   GET  /api/fabrica-editor                                  -> { ok, pedidos:[{ proyecto, anuncio, nombre, slug, tipo, estado, paises, arranque, actualizadoEn }] }   lo que todavia se puede trabajar; `arranque` = cuando una persona dio la orden de arrancar los anuncios de esa oferta (vacio = nadie)
//   GET  /api/fabrica-editor?proyecto=<id>&anuncio=AD1        -> { ok, orden:{ markdown, datos } }                  la orden de trabajo completa (receta incluida)
//   POST /api/fabrica-editor { accion:'avisar', proyecto, anuncio, estado, nota?, drive?, archivo?, costoUsd? }     -> { ok, anuncio:{ id, estado } }
//   POST /api/fabrica-editor { accion:'progreso', proyecto, anuncio, etapa?, pct?, nota?, guion?, plan?, imagen?, video? }   -> { ok, progreso:{ etapa, pct } }     como va el anuncio (la pagina lo pinta)
//   POST /api/fabrica-editor?vista=imagen|video&proyecto=<id>&anuncio=AD1   (cuerpo = los bytes del JPEG/PNG o del MP4)  -> { ok, vista:{ tipo, bytes, t } }     la vista previa del anuncio
//   GET  /api/fabrica-editor?voces=1                          -> { ok, voces:[{ nombre, genero }], porDefecto }       los NOMBRES y el genero de las voces guardadas (nunca sus ids)
//   POST /api/fabrica-editor { accion:'voz', texto, voz? }    -> el audio (OGG/Opus; MP3 si Fish no da Ogg)           la voz del anuncio automatico, con la voz de los flujos (functions/_voz.js)
//        (desde 2026-11-10 la voz pide ademas la cabecera x-version-editor >= VERSION_MINIMA_EDITOR de functions/_fabrica.js; si no, 426. Lo demas de esta puerta no pide version.)
//
// Quien llama: el PC del editor, SIN sesion, con la cabecera x-clave-editor (la llave CLAVE_EDITOR, la misma con la que ya anota sus costos en /api/editor-costos; se crea con
// scripts\Setup-ClaveEditor.ps1). Sin CLAVE_EDITOR puesta (o de menos de 16 caracteres) contesta 503: nunca abre. La comparacion es en tiempo constante.
//
// Que PUEDE esa llave aqui: leer los pedidos de anuncio que siguen abiertos y avisar como va cada uno (estado, nota, enlace de Drive, nombre del archivo, costo, y desde 2026-10-22 el
// PROGRESO: etapa, guion, plan por tiempo y una imagen y un video chicos de vista previa, que el equipo ve en la pagina). Lo que avisa se
// guarda en SU PROPIA clave (`fab:e:`): nunca escribe en el registro del equipo, asi que no puede deshacer una aprobacion, una cancelacion ni unas correcciones recien pedidas
// aunque lea con retraso. Que NO puede: leer ventas, gasto, cuentas, sueldos ni datos de clientes (esta puerta no los toca); cambiar proyectos, recetas, el material ni el estado
// que decide una persona (aprobada, cancelado); abrir pedidos ajenos al editor. Tope: AVISOS_POR_DIA avisos por pedido y por dia (cada uno es una escritura de KV), y AVISOS_RESERVADOS mas que solo valen para «lista» y «error». La orden lleva
// el texto libre enjaulado como DATO (ver ordenDeAnuncio) porque quien la lee es un Claude con acceso a un PC.
// El middleware deja pasar sin cookie SOLO esta ruta y solo con la cabecera; el endpoint exige la llave por su cuenta.

import { safeEqual } from '../_sesion.js';
import * as F from '../_fabrica.js';
import { leerProyecto, leerPedido, guardarEntrega, guardarVista, leerRecetas, listarMeta, recetaDeProyecto } from './fabrica.js';
import { vozDelEditor, vocesParaEditor } from '../_voz.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// «Voy por el plan, el guion es este, mira como queda»: el progreso de un anuncio. Se guarda DENTRO del registro del editor (`fab:e:`, que solo él escribe); decir solo la etapa no borra el
// guion ni el plan que ya mando. Avisar progreso es trabajar el pedido: lo deja «en-edicion» (como cualquier aviso suyo, nunca sobre lo que una persona aprobo o cancelo) y cuenta para el tope
// de avisos del dia. Solo el CAMBIO de etapa deja una linea en «Qué ha pasado»: lo demas no llena la historia.
async function avisarProgreso(env, b, ahora) {
  const par = await leerPedido(env, b.proyecto, b.anuncio);
  if (!par) return json(404, { ok: false, error: 'Ese pedido no existe' });
  const vigente = par.m.estado;
  if (!F.transicionPermitida(vigente, 'en-edicion', 'claudia')) return json(409, { ok: false, error: 'Ese pedido está «' + vigente + '»: una persona lo cerró o lo aprobó, así que no se puede cambiar desde el editor.' });
  const e = par.e && String(par.e.actualizadoEn || '') >= String(par.a.creadoEn || '') ? JSON.parse(JSON.stringify(par.e)) : F.entregaNueva(b.proyecto, b.anuncio);
  const previo = e.progreso && typeof e.progreso === 'object' ? e.progreso : null;
  const nuevo = F.limpiarProgreso(b, previo, ahora);
  const sinHora = (p) => JSON.stringify(Object.assign({}, p, { t: undefined }));
  const resumen = { etapa: nuevo.etapa || '', pct: nuevo.pct == null ? 0 : nuevo.pct };
  if (vigente === 'en-edicion' && previo && sinHora(previo) === sinHora(nuevo)) return json(200, { ok: true, progreso: resumen, igual: true });
  const hoy = ahora.slice(0, 10);
  const hoyN = e.avisos && e.avisos.dia === hoy ? Number(e.avisos.n) || 0 : 0;
  if (hoyN >= F.AVISOS_POR_DIA) return json(429, { ok: false, error: 'Ya van ' + F.AVISOS_POR_DIA + ' avisos de este pedido hoy: no se guardan más. Avísale a una persona o sigue mañana.' });
  e.progreso = nuevo;
  e.estado = 'en-edicion';
  if (!e.tomadoEn) { e.tomadoEn = ahora; e.tomadoPor = 'Claudia'; }
  e.avisos = { dia: hoy, n: hoyN + 1 };
  if (nuevo.etapa && (!previo || previo.etapa !== nuevo.etapa)) F.anotarHecho(e, 'Claudia', 'Claudia: ' + F.ETIQUETA_ETAPA[nuevo.etapa] + (nuevo.nota ? ' · ' + nuevo.nota.slice(0, 100) : ''), ahora, 20);
  else e.actualizadoEn = ahora;
  await guardarEntrega(env, e);
  return json(200, { ok: true, progreso: resumen, anuncio: { id: par.m.id, estado: 'en-edicion' } });
}

// La imagen (la tira de tomas del plan) o el video (una version chica del anuncio) de vista previa. El cuerpo son los bytes. Se mira que SEAN lo que dicen por sus primeros bytes (nada de HTML ni
// SVG disfrazado), el tamano, el tope de subidas del dia (guardarVista, en fabrica.js: se lleva en la metadata del propio archivo) y que el pedido siga abierto. Una escritura de KV por subida.
async function subirVista(env, url, request, ahora) {
  const tipo = url.searchParams.get('vista');
  const pr = url.searchParams.get('proyecto') || '', ad = url.searchParams.get('anuncio') || '';
  if (!Object.prototype.hasOwnProperty.call(F.VISTAS, tipo)) return json(400, { ok: false, error: 'La vista previa es «imagen» o «video»' });
  if (!F.ID.test(pr) || !F.ID_ANUNCIO.test(ad)) return json(400, { ok: false, error: 'Proyecto o anuncio no validos' });
  const tope = F.VISTAS[tipo].tope;
  const mb = (n) => Math.round(n / 1048576 * 10) / 10;
  if (Number(request.headers.get('content-length') || 0) > tope) return json(413, { ok: false, error: 'Pesa demasiado: el tope de la ' + (tipo === 'imagen' ? 'imagen' : 'video') + ' son ' + mb(tope) + ' MB. Comprímelo antes.' });
  const par = await leerPedido(env, pr, ad);
  if (!par) return json(404, { ok: false, error: 'Ese pedido no existe' });
  if (par.m.estado === 'cancelado' || par.m.estado === 'aprobada') return json(409, { ok: false, error: 'Ese pedido está «' + par.m.estado + '»: no se cambia desde el editor.' });
  const bytes = await request.arrayBuffer();
  if (!bytes.byteLength) return json(400, { ok: false, error: 'El archivo llegó vacío' });
  if (bytes.byteLength > tope) return json(413, { ok: false, error: 'Pesa ' + mb(bytes.byteLength) + ' MB y el tope son ' + mb(tope) + '. Comprímelo antes.' });
  const real = F.tipoDeVista(bytes);
  if (!F.VISTAS[tipo].tipos.includes(real)) return json(415, { ok: false, error: tipo === 'imagen' ? 'Eso no es una imagen JPEG ni PNG' : 'Eso no es un video MP4' });
  const guardada = await guardarVista(env, pr, ad, tipo, bytes, real, ahora);
  if (!guardada) return json(429, { ok: false, error: 'Ya van ' + F.VISTAS_POR_DIA + ' subidas de ' + tipo + ' de este pedido hoy: no se guardan más. Sigue mañana.' });
  return json(200, { ok: true, vista: { tipo, bytes: bytes.byteLength, t: ahora } });
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  const llave = request.headers.get('x-clave-editor');
  if (!llave) return json(401, { ok: false, error: 'Falta la llave del editor' });
  if (!env.CLAVE_EDITOR || String(env.CLAVE_EDITOR).length < 16) return json(503, { ok: false, error: 'La llave del editor no esta configurada en el servidor (scripts\\Setup-ClaveEditor.ps1)' });
  if (!safeEqual(llave, String(env.CLAVE_EDITOR))) return json(401, { ok: false, error: 'Llave incorrecta' });

  const url = new URL(request.url);
  const ahora = new Date().toISOString();

  if (request.method === 'GET' && url.searchParams.get('voces')) return await vocesParaEditor(env);

  if (request.method === 'GET') {
    const pr = url.searchParams.get('proyecto') || '';
    const ad = url.searchParams.get('anuncio') || '';
    if (pr || ad) {
      if (!F.ID.test(pr) || !F.ID_ANUNCIO.test(ad)) return json(400, { ok: false, error: 'Proyecto o anuncio no validos' });
      const [p, par, recetas] = await Promise.all([leerProyecto(env, pr), leerPedido(env, pr, ad), leerRecetas(env)]);
      if (!p || !par) return json(404, { ok: false, error: 'Ese pedido no existe' });
      if (par.m.estado === 'cancelado') return json(410, { ok: false, error: 'Ese pedido se canceló' });
      const orden = F.ordenDeAnuncio(p, par.m, recetaDeProyecto(recetas, p, par.m.receta), { origenUrl: url.origin });
      return json(200, { ok: true, orden });
    }
    const [anunsA, anunsE, proys] = await Promise.all([listarMeta(env, F.PREFIJO_ANUNCIO), listarMeta(env, F.PREFIJO_ENTREGA), listarMeta(env, F.PREFIJO_PROYECTO)]);
    const porProyecto = {};
    proys.forEach((m) => { porProyecto[m.meta.id] = m.meta; });
    const entregas = {};
    anunsE.forEach((m) => { if (m.meta && m.meta.pr) entregas[m.meta.pr + ':' + m.meta.id] = m.meta; });
    const pedidos = anunsA
      .map((m) => ({ meta: m.meta, entrega: entregas[m.meta.pr + ':' + m.meta.id], estado: F.estadoDeMetas(m.meta, entregas[m.meta.pr + ':' + m.meta.id]) }))
      .filter((x) => F.ESTADOS_ABIERTOS.includes(x.estado) && porProyecto[x.meta.pr])
      .map((x) => ({ proyecto: x.meta.pr, anuncio: x.meta.id, nombre: porProyecto[x.meta.pr].n, slug: porProyecto[x.meta.pr].s, tipo: x.meta.tp, estado: x.estado, paises: porProyecto[x.meta.pr].p,
        arranque: String(porProyecto[x.meta.pr].ar || ''),          // cuando una persona dio la orden de arrancar los anuncios de esa oferta (vacio = nadie la ha dado)
        actualizadoEn: x.entrega && String(x.entrega.t || '') > String(x.meta.t || '') ? x.entrega.t : x.meta.t }))
      .sort((x, y) => String(x.actualizadoEn).localeCompare(String(y.actualizadoEn)));
    return json(200, { ok: true, pedidos });
  }

  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });
  if (url.searchParams.get('vista')) return subirVista(env, url, request, ahora);               // la imagen o el video de vista previa: el cuerpo son los bytes
  let b;
  try {
    const crudo = await request.text();
    if (crudo.length > 64000) return json(413, { ok: false, error: 'Cuerpo demasiado grande' });          // el aviso de progreso mas grande (guion + 40 tomas del plan) cabe de sobra; la voz pide mucho menos
    b = JSON.parse(crudo);
  } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  if (b && b.accion === 'voz') {
    // La voz gasta (Fish): como APIMart (/api/editor-apimart), solo para un editor al dia. La cola y los avisos no piden version: el lector del VPS no la manda.
    if (!F.versionAlDia(request.headers.get('x-version-editor'))) return json(426, { ok: false, versionMinima: F.VERSION_MINIMA_EDITOR, error: 'Actualiza el editor: el dash pide la versión ' + F.VERSION_MINIMA_EDITOR + ' o más nueva para generar la voz.' });
    return await vozDelEditor(env, b);
  }
  if (!b || (b.accion !== 'avisar' && b.accion !== 'progreso')) return json(400, { ok: false, error: 'Accion no reconocida' });
  if (!F.ID.test(String(b.proyecto || '')) || !F.ID_ANUNCIO.test(String(b.anuncio || ''))) return json(400, { ok: false, error: 'Proyecto o anuncio no validos' });
  if (b.accion === 'progreso') return avisarProgreso(env, b, ahora);

  const aviso = F.limpiarAvisoClaudia(b);
  if (!aviso.estado) return json(400, { ok: false, error: 'El estado debe ser uno de: ' + F.ESTADOS_CLAUDIA.join(', ') });
  const par = await leerPedido(env, b.proyecto, b.anuncio);
  if (!par) return json(404, { ok: false, error: 'Ese pedido no existe' });
  const vigente = par.m.estado;
  if (!F.transicionPermitida(vigente, aviso.estado, 'claudia')) {
    if (vigente === 'pedido' && aviso.estado === 'lista') return json(409, { ok: false, error: 'Primero toma el pedido (estado «tomado» o «en-edicion»): así bajas las correcciones del equipo y el dash sabe que lo estás trabajando.' });
    return json(409, { ok: false, error: 'Ese pedido está «' + vigente + '»: una persona lo cerró o lo aprobó, así que no se puede cambiar desde el editor.' });
  }
  // Lo que cambiaria en la entrega. Si el pedido ya esta en ese estado y no hay nada nuevo que decir, no se escribe (ni cuenta para el tope).
  const entrega = Object.assign({}, par.m.entrega);
  if (aviso.nota) entrega.nota = aviso.nota.slice(0, 400);
  if (aviso.drive) entrega.drive = aviso.drive;
  if (aviso.archivo) entrega.archivo = aviso.archivo;
  if (aviso.costoUsd != null) entrega.costoUsd = aviso.costoUsd;
  if (vigente === aviso.estado && JSON.stringify(entrega) === JSON.stringify(par.m.entrega)) return json(200, { ok: true, anuncio: { id: par.m.id, estado: vigente }, igual: true });

  // Una entrega de ANTES de que existiera este pedido (un aviso tardio de una oferta quitada) no se hereda: ni su contador del dia, ni quien lo tomo, ni su historia.
  const e = par.e && String(par.e.actualizadoEn || '') >= String(par.a.creadoEn || '') ? JSON.parse(JSON.stringify(par.e)) : F.entregaNueva(b.proyecto, b.anuncio);
  const hoy = ahora.slice(0, 10);
  const hoyN = e.avisos && e.avisos.dia === hoy ? Number(e.avisos.n) || 0 : 0;
  // «lista» y «error» son el resultado del trabajo: tienen AVISOS_RESERVADOS avisos mas que el resto, para que el progreso que ya se comio el tope no deje un video terminado sin decirse.
  const tope = F.AVISOS_POR_DIA + (aviso.estado === 'lista' || aviso.estado === 'error' ? F.AVISOS_RESERVADOS : 0);
  if (hoyN >= tope) return json(429, { ok: false, error: 'Ya van ' + tope + ' avisos de este pedido hoy: no se guardan más. Avísale a una persona o sigue mañana.' });

  e.estado = aviso.estado;
  e.entrega = entrega;
  if (aviso.estado === 'tomado' && !e.tomadoEn) { e.tomadoEn = ahora; e.tomadoPor = 'Claudia'; }
  if (aviso.estado === 'lista') e.terminadoEn = ahora;
  e.avisos = { dia: hoy, n: hoyN + 1 };
  F.anotarHecho(e, 'Claudia', { tomado: 'Claudia tomó el pedido', 'en-edicion': 'Claudia está editando', lista: 'Claudia dejó el anuncio listo', error: 'Claudia reportó un problema' }[aviso.estado] + (aviso.nota ? ': ' + aviso.nota.slice(0, 120) : ''), ahora, 20);
  await guardarEntrega(env, e);
  return json(200, { ok: true, anuncio: { id: par.m.id, estado: aviso.estado } });
}
