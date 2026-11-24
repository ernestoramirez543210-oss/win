// Tablero Kanban de produccion: tarjetas de ofertas que avanzan por etapas.
//
// Cada tarjeta es una oferta con su enlace, pais, etapa, y tareas pendientes.
// Cualquier rol autenticado puede leer y mover; el middleware ya filtra quien llega
// a /tablero.
//
// UNA CLAVE POR TARJETA, no un arreglo entero bajo una sola clave. La version anterior
// leia el arreglo completo, lo modificaba y lo volvia a escribir: con tres personas
// usando el tablero a la vez -que es justo para lo que se hizo- dos movimientos
// simultaneos se pisaban y uno se perdia sin aviso. KV no tiene compare-and-swap, asi
// que la unica forma de arreglarlo es que dos personas moviendo tarjetas distintas
// escriban en claves distintas.
//
// El precio es que leer el tablero son N lecturas en vez de una. Con decenas de
// tarjetas eso es irrelevante; perder el trabajo de alguien no lo es.
//
// Ademas de las tarjetas hay dos listas con el mismo criterio -una clave por fila-:
//   - EXPANSION LATAM (tablero:x:<id>): una fila por oferta y una celda por pais con su estado (flujos en proceso, flujos listos, campaña programada,
//     en testeo, funciona, tablas, no funciona). Colombia va primero; con ella funcionando o en tablas se abre Mexico y Venezuela; con esos dos funcionando, el resto.
//     Ana marca "flujos listos" por pais y Carla ve ahi lo que ya puede programar.
//   - VARIACIONES DE ANUNCIOS (tablero:v:<id>): una fila por variacion de una oferta (idea > en produccion > lista > subida > ganadora o descartada).
//     Beto marca "lista" y Carla la sube. Cualquier rol del equipo mueve estados; el rol y la hora quedan anotados en la fila.

import { readSession } from '../_sesion.js';
import { originalesDe, nucleo, grupoDe, gruposDelMeta, PREFIJO_GRUPO, CLAVE_META } from '../_originales.js';

const PREFIJO = 'tablero:t:';
const CLAVE_VIEJA = 'tablero:tarjetas';   // el arreglo unico de antes, se migra y se borra
const CLAVE_ETAPAS = 'tablero:etapas';
const PREFIJO_EXP = 'tablero:x:';
const PREFIJO_VAR = 'tablero:v:';
// Los paises donde se opera y los que vienen (El Salvador y Uruguay). Una lista cerrada: el texto libre acabo siendo "Mexico", "MX" y "mexico".
const PAISES_EXP = ['colombia', 'mexico', 'venezuela', 'guatemala', 'peru', 'argentina', 'ecuador', 'bolivia', 'elsalvador', 'uruguay'];
const ESTADOS_EXP = ['flujos', 'listo', 'programada', 'testeo', 'funciona', 'tablas', 'no'];   // sin entrada = sin empezar
const ESTADOS_VAR = ['idea', 'produccion', 'lista', 'subida', 'ganadora', 'descartada'];
// Que se le cambia al anuncio base de una variacion (templates/revivir-ofertas.js, CAMBIOS): solo el inicio, el inicio y el cierre, o el inicio en otro formato.
const CAMBIOS_VAR = ['inicio', 'inicio-cierre', 'formato'];

// Las etapas venian fijas en el HTML. Cada operacion tiene su propio recorrido -y el
// tuyo va a cambiar-, asi que viven en KV y se editan desde el tablero. Estas son solo
// el punto de partida de un tablero vacio.
const ETAPAS_INICIALES = [
  { id: 'idea',      nombre: 'Ideas' },
  { id: 'diseno',    nombre: 'Diseño' },
  { id: 'grabacion', nombre: 'Grabación' },
  { id: 'edicion',   nombre: 'Edición' },
  { id: 'landing',   nombre: 'Landing' },
  { id: 'anuncios',  nombre: 'Anuncios' },
  { id: 'activa',    nombre: 'Activa' },
  { id: 'pausada',   nombre: 'Pausada' },
];

async function leerEtapas(env) {
  const raw = await env.SUITE.get(CLAVE_ETAPAS);
  if (!raw) return ETAPAS_INICIALES;
  try {
    const e = JSON.parse(raw);
    return Array.isArray(e) && e.length ? e : ETAPAS_INICIALES;
  } catch { return ETAPAS_INICIALES; }
}

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

// Solo http y https. Un campo de enlace que acepta cualquier cosa acepta tambien
// javascript:... , y ese texto acaba dentro de un href que otro miembro del equipo va a
// pulsar. Lo que no sea una URL navegable no se guarda.
function urlLimpia(v) {
  const s = String(v || '').trim().slice(0, 500);
  if (!s) return '';
  try {
    const u = new URL(s);
    return (u.protocol === 'http:' || u.protocol === 'https:') ? s : '';
  } catch { return ''; }
}

const texto = (v, n) => String(v == null ? '' : v).replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, n);
const ISO = /^\d{4}-\d{2}-\d{2}T[\d:.]+Z?$/;

// Una fila de expansion: { id, nombre, creadoEn, celdas: { pais: { estado, nota, en, por } } }
function limpiarExp(t, datos, rol) {
  if ('nombre' in datos) t.nombre = texto(datos.nombre, 100) || 'Oferta sin nombre';
  if ('celdas' in datos && datos.celdas && typeof datos.celdas === 'object') {
    const out = {};
    for (const p of PAISES_EXP) {
      const c = datos.celdas[p];
      if (!c || ESTADOS_EXP.indexOf(c.estado) === -1) continue;
      out[p] = { estado: c.estado, nota: texto(c.nota, 160), en: ISO.test(String(c.en || '')) ? c.en : new Date().toISOString(), por: texto(c.por || rol, 20) };
    }
    t.celdas = out;
  }
  return t;
}
// Una variacion de anuncio: { id, oferta, nombre, enlace, paises:[], estado, nota, creadoEn, en, por, anuncio?, referencia?, base?, cambio?, gancho?, edicion? }
// `edicion` (el pedido al servidor que hace la variacion sola: estado, original, numero, enlace del resultado, costo) la escribe SOLO /api/variacion-editor, y `originales` (los videos de Drive
// candidatos a original) los calcula el GET de aqui con el indice del servidor y no se guardan: ninguno de los dos se toma de lo que manda el navegador (aqui solo se copian los campos de abajo).
function limpiarVar(t, datos, rol) {
  if ('oferta' in datos) t.oferta = texto(datos.oferta, 80);
  if ('nombre' in datos) t.nombre = texto(datos.nombre, 100) || 'Variacion sin nombre';
  // El anuncio del que sale la variacion (lo manda el dash): con esto el explorador marca los que ya tienen una pedida y no la pide dos veces.
  if ('anuncio' in datos) t.anuncio = texto(datos.anuncio, 80);
  if ('enlace' in datos) t.enlace = urlLimpia(datos.enlace);
  // El anuncio ajeno que se va a modelar (lo manda /radar). Va aparte de `enlace`, que es la carpeta de Drive de la variacion y la llena el equipo; el formulario del tablero no lo manda, asi que editar la fila no lo borra.
  if ('referencia' in datos) t.referencia = urlLimpia(datos.referencia);
  // NUESTRO anuncio del que sale la variacion (2026-11-12, «Revivir ofertas»: el que mas vendio, p. ej. «101»): Beto toma ese video y le cambia el gancho o el angulo. Texto corto;
  // la pagina lo escapa al pintarlo. Va aparte de `anuncio` (la clave para no pedirla dos veces).
  if ('base' in datos) t.base = texto(datos.base, 40);
  // Que se le cambia a la base (2026-11-13: «¿solo se cambia el inicio y se mantiene el mismo cuerpo?»): solo estos valores (los mismos de RevivirOfertas.CAMBIOS); otro = ninguno.
  // Y el gancho nuevo: lo que se dice al inicio (texto de la IA o de un anuncio de afuera: la pagina lo escapa al pintarlo).
  if ('cambio' in datos) t.cambio = CAMBIOS_VAR.indexOf(datos.cambio) !== -1 ? datos.cambio : '';
  if ('gancho' in datos) t.gancho = texto(datos.gancho, 200);
  if ('paises' in datos) t.paises = (Array.isArray(datos.paises) ? datos.paises : []).map((p) => String(p).toLowerCase()).filter((p, i, a) => PAISES_EXP.indexOf(p) !== -1 && a.indexOf(p) === i);
  // 300 (antes 200): la descripcion de una idea del radar llega hasta 240 letras y se cortaba a mitad de frase (2026-11-17, Beto: «queda en ...mostrando a alguien cerrando u»).
  if ('nota' in datos) t.nota = texto(datos.nota, 300);
  if ('estado' in datos && ESTADOS_VAR.indexOf(datos.estado) !== -1) { t.estado = datos.estado; t.en = new Date().toISOString(); t.por = texto(rol, 20); }
  return t;
}

function limpiar(t, datos) {
  if ('nombre' in datos) t.nombre = String(datos.nombre || '').trim().slice(0, 120) || 'Sin nombre';
  if ('enlace' in datos) t.enlace = urlLimpia(datos.enlace);
  // Una oferta no tiene "un enlace": tiene la biblioteca de anuncios -a veces varias, una
  // por pais o por cuenta-, la carpeta de Drive con los creativos y el documento del
  // contenido. Con un solo campo, el equipo pegaba los otros dos como si fueran tareas
  // pendientes, asi que quedaban a medio marcar y no se podian abrir de un clic.
  if ('biblioteca' in datos) {
    t.biblioteca = (Array.isArray(datos.biblioteca) ? datos.biblioteca : [])
      .slice(0, 8).map(urlLimpia).filter(Boolean);
  }
  if ('drive' in datos) t.drive = urlLimpia(datos.drive);
  if ('docs'  in datos) t.docs  = urlLimpia(datos.docs);
  // El WhatsApp del competidor (2026-11-13): el boton verde de la tarjeta, para entrar a su WhatsApp como cliente y ver su embudo y sus entregables (lo pidio el dueño
  // para Ana, que arma los flujos). Lo deja el radar al marcar «Testiando» (functions/_radar-tablero.js) y el equipo lo puede pegar o cambiar en la ventana.
  if ('whatsapp' in datos) t.whatsapp = urlLimpia(datos.whatsapp);
  if ('pais'   in datos) t.pais   = String(datos.pais   || '').trim().slice(0, 30);
  // Una minitarjeta por pais, cada una con SU etapa. El flujo del bot se arma pais por
  // pais, asi que la oferta no vive en una sola columna: el de Mexico puede estar en
  // testeo mientras el de Peru sigue en flujos. Antes esto se llevaba como tareas sueltas
  // -"Flujo listo Mexico"- y no habia forma de mover una sin mover la oferta entera.
  if ('paises' in datos) {
    t.paises = (Array.isArray(datos.paises) ? datos.paises : []).slice(0, 12).map((p) => ({
      pais:  String((p && p.pais)  || '').trim().toLowerCase().slice(0, 30),
      etapa: String((p && p.etapa) || '').trim().slice(0, 30),
      listo: !!(p && p.listo),
    })).filter((p) => p.pais);
  }
  if ('etapa'  in datos) t.etapa  = String(datos.etapa  || '').trim().slice(0, 30) || 'idea';
  if ('notas'  in datos) t.notas  = String(datos.notas  || '').trim().slice(0, 1000);
  // Marca "lista para pasar a la siguiente etapa" - la pone quien termino su parte, no el
  // sistema. Vive aparte de la etapa misma: cambiar de etapa la apaga (ver accion 'mover'),
  // asi que una tarjeta verde de ayer no se confunde con una recien movida.
  if ('listo' in datos) t.listo = !!datos.listo;
  if ('tareas' in datos) {
    t.tareas = (Array.isArray(datos.tareas) ? datos.tareas : []).slice(0, 20).map((x) => ({
      texto: String((x && x.texto) || '').slice(0, 200),
      hecho: !!(x && x.hecho),
    }));
  }
  return t;
}

// Las tarjetas que quedaron en el arreglo viejo se reparten a su propia clave la primera
// vez que alguien abre el tablero. Sin esto, encender el cambio vaciaria el tablero.
async function migrar(env) {
  const raw = await env.SUITE.get(CLAVE_VIEJA);
  if (!raw) return;
  let viejas = [];
  try { viejas = JSON.parse(raw) || []; } catch { viejas = []; }
  await Promise.all(viejas.map((t) => {
    if (!t || !t.id) return null;
    return env.SUITE.put(PREFIJO + t.id, JSON.stringify(t));
  }));
  await env.SUITE.delete(CLAVE_VIEJA);
}

async function leerTodas(env) {
  const lista = await env.SUITE.list({ prefix: PREFIJO, limit: 1000 });
  const tarjetas = await Promise.all(
    lista.keys.map((k) => env.SUITE.get(k.name, { type: 'json' })));
  return tarjetas
    .filter(Boolean)
    .sort((a, b) => String(a.creadoEn || '').localeCompare(String(b.creadoEn || '')));
}

async function leerPrefijo(env, prefijo) {
  const lista = await env.SUITE.list({ prefix: prefijo, limit: 1000 });
  const filas = await Promise.all(lista.keys.map((k) => env.SUITE.get(k.name, { type: 'json' })));
  return filas.filter(Boolean).sort((a, b) => String(a.creadoEn || '').localeCompare(String(b.creadoEn || '')));
}

// Las variaciones con su ORIGINAL en Drive («🎞️ Abrir el original»): a cada una con `base` se le pegan hasta 3 candidatos del indice que sube el servidor del editor (/api/variacion-editor,
// functions/_originales.js), calculados aqui y nunca guardados. Solo si alguna variacion trae base: se lee el resumen del indice y SOLO los grupos de los numeros de esas bases (el indice
// entero no se lee: CPU). Sin indice todavia (el servidor nunca lo mando) o con un grupo que no se pudo leer no se pega nada: la pagina no dice «no encontre el original» de algo que nadie ha
// buscado. Con indice y sin ese numero en el resumen se pega [] («No encontré el original en Drive»).
// En un isolate tibio (Cloudflare reusa el mismo para varias aperturas) un grupo que no cambio no se vuelve a leer ni a interpretar: se reconoce por la huella que da el resumen
// y que el propio grupo trae en sus metadatos. Una lectura atrasada de KV trae el grupo VIEJO con su huella vieja: no calza con la del resumen y no se guarda como el nuevo.
const GRUPOS_EN_MEMORIA = new Map();
export function olvidarGruposEnMemoria() { GRUPOS_EN_MEMORIA.clear(); }   // para las pruebas
async function leerGrupo(env, g, huella) {
  const c = GRUPOS_EN_MEMORIA.get(g);
  if (c && huella && c.h === huella) return c.v;
  let r = null;
  try {
    r = typeof env.SUITE.getWithMetadata === 'function' ? await env.SUITE.getWithMetadata(PREFIJO_GRUPO + g, { type: 'json' })
      : { value: await env.SUITE.get(PREFIJO_GRUPO + g, { type: 'json' }), metadata: null };
  } catch { return null; }
  const v = r && r.value && typeof r.value === 'object' ? r.value : null;
  const h = r && r.metadata && typeof r.metadata.h === 'string' ? r.metadata.h : '';
  if (v && h && h === huella) GRUPOS_EN_MEMORIA.set(g, { h, v });
  return v;
}
async function conOriginales(env, variaciones) {
  const conBase = variaciones.filter((v) => v && String(v.base || '').trim());
  if (!conBase.length) return variaciones;
  let meta = null;
  try { meta = await env.SUITE.get(CLAVE_META, { type: 'json' }); } catch { meta = null; }
  const hay = gruposDelMeta(meta);
  if (!hay) return variaciones;
  const grupoDeVar = (v) => grupoDe(nucleo(v.base));
  const leer = [...new Set(conBase.map(grupoDeVar).filter((g) => hay.has(g)))];
  const leidos = await Promise.all(leer.map((g) => leerGrupo(env, g, meta.grupos[g])));
  const porGrupo = new Map(leer.map((g, i) => [g, leidos[i]]));
  return variaciones.map((v) => {
    if (!v || !String(v.base || '').trim()) return v;
    const g = grupoDeVar(v);
    if (!hay.has(g)) return { ...v, originales: [] };
    const grupo = porGrupo.get(g);
    return grupo && typeof grupo === 'object' ? { ...v, originales: originalesDe(grupo, v.oferta, v.base) } : v;
  });
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  let rolSesion = '';
  if (true) {
    const rol = env.SESSION_SECRET
      ? await readSession(request.headers.get('Cookie'), env.SESSION_SECRET)
      : null;
    if (!rol) return json(401, { ok: false, error: 'No autenticado' });
    rolSesion = rol;
  }

  await migrar(env);

  if (request.method === 'GET') {
    // ?solo=variaciones: lo que necesita el dash para marcar los anuncios con variacion pedida, sin leer todo el tablero (cada lectura de KV cuenta en el plan gratis).
    // Con &originales=1 (solo la pagina de /tablero) trae ademas los originales en Drive; el dash y /radar no los usan y asi no leen el indice (lecturas de KV y CPU).
    const sp = new URL(request.url).searchParams;
    if (sp.get('solo') === 'variaciones') {
      const variaciones = await leerPrefijo(env, PREFIJO_VAR);
      return json(200, { ok: true, variaciones: sp.get('originales') === '1' ? await conOriginales(env, variaciones) : variaciones });
    }
    const [tarjetas, etapas, expansion, variaciones] = await Promise.all([leerTodas(env), leerEtapas(env), leerPrefijo(env, PREFIJO_EXP), leerPrefijo(env, PREFIJO_VAR).then((v) => conOriginales(env, v))]);
    return json(200, { ok: true, tarjetas, etapas, expansion, variaciones });
  }
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const accion = body.accion;

  if (accion === 'crear') {
    // El nombre se exige aqui y no solo en el formulario. La validacion del navegador
    // frena a quien escribe, no a quien llama la API: una tarjeta sin nombre entraba y se
    // guardaba como "Sin nombre", y en el tablero nadie sabia despues que era.
    if (!String(body.nombre || '').trim()) {
      return json(400, { ok: false, error: 'La tarjeta necesita un nombre' });
    }
    const t = limpiar({ id: uid(), tareas: [], creadoEn: new Date().toISOString() }, {
      nombre: body.nombre, enlace: body.enlace, pais: body.pais,
      biblioteca: body.biblioteca || [], drive: body.drive, docs: body.docs, whatsapp: body.whatsapp,
      etapa: body.etapa || 'idea', notas: body.notas, tareas: body.tareas || [],
    });
    await env.SUITE.put(PREFIJO + t.id, JSON.stringify(t));
    return json(200, { ok: true, tarjeta: t });
  }

  if (accion === 'borrar') {
    if (!body.id) return json(400, { ok: false, error: 'Falta el id' });
    await env.SUITE.delete(PREFIJO + body.id);
    return json(200, { ok: true });
  }

  if (accion === 'etapas') {
    const lista = (Array.isArray(body.etapas) ? body.etapas : [])
      .map((e) => ({
        id: String((e && e.id) || '').trim().slice(0, 30),
        nombre: String((e && e.nombre) || '').trim().slice(0, 40),
      }))
      .filter((e) => e.id && e.nombre)
      .slice(0, 20);
    if (!lista.length) return json(400, { ok: false, error: 'El tablero necesita al menos una etapa.' });

    // Borrar una etapa no puede borrar las tarjetas que viven en ella: se mueven a la
    // primera. Perder una oferta por renombrar una columna seria imperdonable.
    const vivas = new Set(lista.map((e) => e.id));
    const refugio = lista[0].id;
    const tarjetas = await leerTodas(env);
    const huerfanas = tarjetas.filter((t) => !vivas.has(t.etapa));
    await Promise.all(huerfanas.map((t) => {
      t.etapa = refugio;
      return env.SUITE.put(PREFIJO + t.id, JSON.stringify(t));
    }));

    await env.SUITE.put(CLAVE_ETAPAS, JSON.stringify(lista));
    return json(200, { ok: true, etapas: lista, movidas: huerfanas.length });
  }

  if (accion === 'mover' || accion === 'editar') {
    if (!body.id) return json(400, { ok: false, error: 'Falta el id' });
    // Solo esta tarjeta se lee y se escribe: mover la de al lado ya no pisa esta.
    const t = await env.SUITE.get(PREFIJO + body.id, { type: 'json' });
    if (!t) return json(404, { ok: false, error: 'Tarjeta no encontrada' });
    // Mover apaga el "listo": esa marca era una promesa sobre la etapa vieja, y en la
    // nueva no significa nada todavia. Sin este reset, la tarjeta llegaba verde a la
    // columna siguiente sin que nadie la hubiera marcado ahi.
    limpiar(t, accion === 'mover' ? { etapa: body.etapa, listo: false } : body);
    // Arrastrar la tarjeta ENTERA se lleva sus minitarjetas: mover la oferta y dejar los
    // paises en la columna de antes la partiria en dos sin que nadie lo pidiera. Mover un
    // solo pais se hace con 'editar' mandando el arreglo completo, no por aqui.
    if (accion === 'mover' && Array.isArray(t.paises) && t.paises.length) {
      t.paises = t.paises.map((p) => ({ pais: p.pais, etapa: t.etapa, listo: false }));
    }
    await env.SUITE.put(PREFIJO + body.id, JSON.stringify(t));
    return json(200, { ok: true, tarjeta: t });
  }

  // ---- Expansion LATAM ----
  if (accion === 'exp_guardar') {
    const hay = body.id ? await env.SUITE.get(PREFIJO_EXP + body.id, { type: 'json' }) : null;
    if (!hay && !texto(body.nombre, 100)) return json(400, { ok: false, error: 'La oferta necesita un nombre' });
    const fila = hay || { id: uid(), creadoEn: new Date().toISOString(), celdas: {} };
    // Una oferta nueva arranca con Colombia en testeo: ahi se prueba primero.
    if (!hay && !body.celdas) body.celdas = { colombia: { estado: 'testeo', por: rolSesion } };
    limpiarExp(fila, body, rolSesion);
    await env.SUITE.put(PREFIJO_EXP + fila.id, JSON.stringify(fila));
    return json(200, { ok: true, fila });
  }
  if (accion === 'exp_celda') {
    if (!body.id) return json(400, { ok: false, error: 'Falta el id' });
    if (PAISES_EXP.indexOf(body.pais) === -1) return json(400, { ok: false, error: 'Pais invalido' });
    const fila = await env.SUITE.get(PREFIJO_EXP + body.id, { type: 'json' });
    if (!fila) return json(404, { ok: false, error: 'Oferta no encontrada' });
    fila.celdas = fila.celdas || {};
    if (!body.estado) delete fila.celdas[body.pais];                       // vacio = sin empezar
    else if (ESTADOS_EXP.indexOf(body.estado) === -1) return json(400, { ok: false, error: 'Estado invalido' });
    else fila.celdas[body.pais] = { estado: body.estado, nota: texto(body.nota, 160), en: new Date().toISOString(), por: texto(rolSesion, 20) };
    await env.SUITE.put(PREFIJO_EXP + fila.id, JSON.stringify(fila));
    return json(200, { ok: true, fila });
  }
  if (accion === 'exp_borrar') {
    if (!body.id) return json(400, { ok: false, error: 'Falta el id' });
    await env.SUITE.delete(PREFIJO_EXP + body.id);
    return json(200, { ok: true });
  }

  // ---- Variaciones de anuncios ----
  if (accion === 'var_guardar') {
    const hay = body.id ? await env.SUITE.get(PREFIJO_VAR + body.id, { type: 'json' }) : null;
    if (!hay && !texto(body.nombre, 100)) return json(400, { ok: false, error: 'La variacion necesita un nombre' });
    // El formulario le puso numero a una que no lo tenia: si mientras tanto otra pestaña ya la numero, no se renumera (2026-11-17, revisor-cambios).
    if (hay && body.sinNumeroAntes === true && /^(AD )?\d+(\.\d+)*(?=\s|$|[·:|-])/i.test(String(hay.nombre || '').trim())) return json(409, { ok: false, error: 'Otra persona ya le puso número a esta variación: recargue la página.', fila: hay });
    const fila = hay || { id: uid(), creadoEn: new Date().toISOString(), estado: 'idea', paises: ['colombia'] };
    limpiarVar(fila, body, rolSesion);
    await env.SUITE.put(PREFIJO_VAR + fila.id, JSON.stringify(fila));
    return json(200, { ok: true, fila });
  }
  // Numerar una variacion por producir que no tiene numero (la pagina lo hace sola al abrir la pestaña, 2026-11-17; revisor-seguridad: no con var_guardar, que crea una fila si el id
  // ya no existe y no comprueba lo guardado). Pone el numero delante del nombre y, solo si la fila no los tenia, la base y lo que se cambia. La nota solo si COMPLETA la que se guardo
  // cortada en el tope viejo (190 letras o mas y empieza exactamente igual): una nota que el equipo vacio o recorto no se vuelve a llenar. 404 si la fila no existe (no la crea);
  // 409 si ya tiene numero o ya no esta por producir (otra persona la numero o la movio).
  if (accion === 'var_numerar') {
    if (!body.id) return json(400, { ok: false, error: 'Falta el id' });
    const numero = texto(body.numero, 20);
    if (!/^(AD )?\d{1,4}(\.\d{1,3}){1,4}$/.test(numero)) return json(400, { ok: false, error: 'Numero invalido' });
    const fila = await env.SUITE.get(PREFIJO_VAR + body.id, { type: 'json' });
    if (!fila) return json(404, { ok: false, error: 'Variacion no encontrada' });
    if (/^(AD )?\d+(\.\d+)*(?=\s|$|[·:|-])/i.test(String(fila.nombre || '').trim())) return json(409, { ok: false, error: 'La variacion ya tiene numero', fila });
    if (fila.estado !== 'idea' && fila.estado !== 'produccion') return json(409, { ok: false, error: 'La variacion ya no esta por producir', fila });
    const datos = { nombre: (numero + ' · ' + String(fila.nombre || '').trim()).slice(0, 100) };
    if (!texto(fila.base, 40) && 'base' in body) datos.base = body.base;
    if (CAMBIOS_VAR.indexOf(fila.cambio) === -1 && 'cambio' in body) datos.cambio = body.cambio;
    const nota = texto(body.nota, 300), vieja = texto(fila.nota, 300);
    if ('nota' in body && vieja.length >= 190 && nota.length > vieja.length && nota.indexOf(vieja) === 0) datos.nota = nota;
    limpiarVar(fila, datos, rolSesion);
    await env.SUITE.put(PREFIJO_VAR + fila.id, JSON.stringify(fila));
    return json(200, { ok: true, fila });
  }
  if (accion === 'var_estado') {
    if (!body.id) return json(400, { ok: false, error: 'Falta el id' });
    if (ESTADOS_VAR.indexOf(body.estado) === -1) return json(400, { ok: false, error: 'Estado invalido' });
    const fila = await env.SUITE.get(PREFIJO_VAR + body.id, { type: 'json' });
    if (!fila) return json(404, { ok: false, error: 'Variacion no encontrada' });
    limpiarVar(fila, { estado: body.estado }, rolSesion);
    await env.SUITE.put(PREFIJO_VAR + fila.id, JSON.stringify(fila));
    return json(200, { ok: true, fila });
  }
  if (accion === 'var_borrar') {
    if (!body.id) return json(400, { ok: false, error: 'Falta el id' });
    await env.SUITE.delete(PREFIJO_VAR + body.id);
    return json(200, { ok: true });
  }

  return json(400, { ok: false, error: 'Accion no reconocida: ' + accion });
}
