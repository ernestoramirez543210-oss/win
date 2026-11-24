// Pasar una oferta del radar al tablero de Produccion (/tablero): lo que ocurre cuando el dueño marca una oferta como «Testiando».
//
// Las funciones de armado son PURAS (la tarjeta, sus notas y la columna de destino); `mandarAlTablero` es la unica que toca KV. La usa /api/radar-estado y la prueba
// scripts/probar-radar.cjs, que ademas la cruza con /api/tablero de verdad: si el prefijo, la clave de las etapas o la forma de la tarjeta se separan de
// functions/api/tablero.js, esa prueba falla (esta copia no importa de alla para no tocar el tablero que ya corre).
//
// Reglas:
//   1. UNA tarjeta por oferta. Su id es `radar-<id de la oferta>` y es tambien la clave KV (`tablero:t:radar-<id>`): marcar «Testiando» dos veces no la duplica, y si el
//      equipo ya la movio de columna no se le devuelve a la primera. Si el equipo la borro, marcarla otra vez la vuelve a crear.
//   2. Cae en la columna «Nueva oferta» (se busca por nombre, porque el equipo reordena y renombra las columnas); si no existe, en la PRIMERA columna del tablero.
//   3. Lo que describe la oferta es TEXTO DE TERCEROS (anunciante, gancho y entregable salen de anuncios ajenos) y llega desde el navegador: aqui se limpia y se
//      recorta, el enlace al anuncio se RECONSTRUYE desde su id y la pagina de ventas pasa por `enlacePagina`. El tablero lo escapa al pintarlo.
//   4. La pagina de ventas va en las NOTAS y no en `enlace`: el modal del tablero vacia `enlace` al guardar (su contenido "ya vive" en biblioteca, drive o docs) y ninguna
//      de esas tres casillas es la pagina de un tercero.
//   5. El WhatsApp del competidor (2026-11-13, pedido del dueño: «cuando pase una oferta al tablero quiero ver también el botón de WhatsApp para que Ana le quede más
//      fácil y rápido ver los entregables») va en `whatsapp`: el mismo boton de la ficha del radar, para entrar a su WhatsApp como cliente y ver su embudo y lo que
//      entrega. Solo dos formas, RECONSTRUIDAS aqui (`whatsappDeOferta`): la publicacion del anuncio en Facebook (permalink.php con digitos: «Flujo de WhatsApp» o lo que
//      hallo «Buscar su WhatsApp») o un chat de WhatsApp (wa.me / chat.whatsapp.com). Si la tarjeta ya estaba SIN WhatsApp, marcar «Testiando» otra vez se lo pone
//      (una escritura); nunca se pisa el que ya tiene (lo pudo cambiar el equipo).

import { plegar, limpio, enlaceAnuncio, enlacePagina, clasificarEnlace, enlaceFlujo, enlaceWhatsapp } from './_radar.js';

// Los mismos de functions/api/tablero.js (PREFIJO y CLAVE_ETAPAS) y la primera de sus ETAPAS_INICIALES, que es la que manda un tablero sin etapas guardadas.
export const PREFIJO_TARJETA = 'tablero:t:';
export const CLAVE_ETAPAS = 'tablero:etapas';
export const ETAPA_PRIMERA = { id: 'idea', nombre: 'Ideas' };

export const idTarjeta = (idOferta) => 'radar-' + idOferta;

// Una sola linea, sin controles y con tope (los saltos de linea de las notas los pone `notasDeTarjeta`, no el texto ajeno).
const una = (v, max) => limpio(String(v == null ? '' : v).replace(/\r/g, ' '), max);

// La columna «Nueva oferta» (sin importar mayusculas ni tildes) o, si no hay, la primera.
export function etapaNuevaOferta(etapas) {
  const lista = (Array.isArray(etapas) ? etapas : []).filter((e) => e && typeof e.id === 'string' && e.id && typeof e.nombre === 'string');
  return lista.find((e) => plegar(e.nombre) === 'nueva oferta') || lista[0] || ETAPA_PRIMERA;
}

// "Moleque Arteiro · manualidades": el nombre con el que el dueño la ve en el radar y, detras del punto, el tema (para que quien la abra en el tablero sepa de que va).
export function nombreDeTarjeta(o) {
  const anunciante = una(o.anunciante, 90), tema = una(o.nichoCorto, 40);
  const base = anunciante || una(o.palabra, 60) || 'Oferta del radar';
  return una(anunciante && tema && !/^sin clasificar$/i.test(tema) ? base + ' · ' + tema : base, 120);
}

// Lo que el equipo necesita para arrancar, en el tope de 1.000 caracteres que el tablero le pone a las notas (con mas, la primera edicion cortaria el final: el peor caso
// de aqui, con todos los campos al maximo, mide menos de 1.000 y la prueba lo comprueba).
// Los enlaces de una oferta agregada a mano, validados de nuevo aqui (llegan del navegador): [{ url, tipo }] sin repetir, hasta 4.
function enlacesManuales(o) {
  const vistos = new Set(), out = [];
  for (const e of Array.isArray(o.enlaces) ? o.enlaces.slice(0, 8) : []) {
    const c = clasificarEnlace(typeof e === 'string' ? e : e && e.url);
    if (c && !vistos.has(c.url) && out.length < 4) { vistos.add(c.url); out.push(c); }
  }
  return out;
}

export function notasDeTarjeta(o, fila) {
  const f = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(o.fecha || ''));
  const n = Math.round(Number(fila));
  const manual = o.origen === 'manual';
  const donde = [Number.isFinite(n) && n > 0 ? 'fila ' + n + ' de la planilla' : '', f ? f[3] + '/' + f[2] + '/' + f[1] : ''].filter(Boolean).join(', ');
  const lineas = [manual ? 'Agregada a mano por el equipo en el radar de ofertas' + (f ? ' (' + f[3] + '/' + f[2] + '/' + f[1] + ')' : '') + '.'
    : 'Viene del radar de ofertas' + (donde ? ' (' + donde + ')' : '') + '.'];
  const campo = (etiqueta, valor, max) => { const s = una(valor, max); if (s) lineas.push(etiqueta + ': ' + s); };
  if (manual) {
    // Lo que el equipo dijo que es la oferta manda (decide cual se modela cuando la pagina vende varias): va completo y primero; los enlaces solo si caben en el tope.
    campo('De qué es (lo dijo el equipo)', o.entregable, 420);
    campo('Precio de origen', o.precio, 30);
    let largo = lineas.join('\n').length;
    for (const e of enlacesManuales(o)) {
      const l = (e.tipo === 'anuncio' ? 'Anuncio' : e.tipo === 'anunciante' ? 'Anunciante' : e.tipo === 'red' ? 'Video o red' : 'Página') + ': ' + e.url;
      if (largo + 1 + l.length > 1000) break;
      lineas.push(l); largo += 1 + l.length;
    }
    return lineas.join('\n').slice(0, 1000);
  }
  campo('Qué vende', o.entregable, 130);
  campo('Gancho del anuncio', o.angulo, 170);
  campo('Precio de origen', o.precio, 30);
  campo('Cómo vende', o.embudo, 70);
  campo('En Colombia', o.hueco, 120);
  const pagina = enlacePagina(o.linkPagina);
  if (pagina) lineas.push('Página de ventas: ' + pagina);
  return lineas.join('\n').slice(0, 1000);
}

// El WhatsApp del competidor (regla 5): la publicacion del anuncio (permalink.php?story_fbid=<video>&id=<pagina>, rearmada solo con sus digitos) o un chat de WhatsApp
// (`enlaceWhatsapp`: wa.me/<numero>, api.whatsapp.com/send?phone= pasado a wa.me, chat.whatsapp.com/<codigo>). Lo que no tiene una de esas formas se pierde.
const PERMALINK = /^https:\/\/(?:www\.|m\.|web\.)?facebook\.com\/permalink\.php\?story_fbid=(\d{5,25})&id=(\d{5,25})$/i;
export function enlaceWhatsappCompetidor(v) {
  const s = limpio(v, 300);
  const m = PERMALINK.exec(s);
  return m ? enlaceFlujo(m[2], m[1]) : enlaceWhatsapp(s);
}
// Lo que manda la ficha (`whatsapp`: el boton que el dueño ve) y, si no trae nada que sirva, el chat de WhatsApp que ya venga como pagina de la oferta (las de oro suelen
// traer su wa.me ahi) o entre los enlaces de una oferta agregada a mano.
export function whatsappDeOferta(o) {
  const x = o && typeof o === 'object' ? o : {};
  const candidatos = [x.whatsapp, x.linkPagina].concat(Array.isArray(x.enlaces) ? x.enlaces.slice(0, 8).map((e) => (typeof e === 'string' ? e : e && e.url)) : []);
  for (const c of candidatos) { const u = enlaceWhatsappCompetidor(c); if (u) return u; }
  return '';
}

// La tarjeta con la forma que deja /api/tablero (`crear`) mas dos marcas (`origen` y `radarId`) que `editar` y `mover` conservan.
export function tarjetaDeOferta(idOferta, o, fila, etapaId, ahora) {
  const biblioteca = enlaceAnuncio(o.linkAnuncio);
  // Una oferta agregada a mano puede traer varios enlaces de la Biblioteca (un anuncio y la pagina del anunciante): todos van a «biblioteca»; la pagina de ventas y los videos, en las notas.
  const deLaBiblioteca = o.origen === 'manual' ? enlacesManuales(o).filter((e) => e.tipo === 'anuncio' || e.tipo === 'anunciante').map((e) => e.url) : (biblioteca ? [biblioteca] : []);
  return {
    id: idTarjeta(idOferta),
    nombre: nombreDeTarjeta(o),
    enlace: '',
    biblioteca: deLaBiblioteca,
    whatsapp: whatsappDeOferta(o),
    drive: '', docs: '', pais: '',
    etapa: etapaId,
    notas: notasDeTarjeta(o, fila),
    tareas: [],
    creadoEn: ahora,
    origen: o.origen === 'manual' ? 'manual' : 'radar', radarId: idOferta,
  };
}

// Las etapas del tablero tal como las guardo el equipo; sin ninguna guardada (o ilegibles), solo la primera de las de origen.
async function leerEtapas(env) {
  const crudo = await env.SUITE.get(CLAVE_ETAPAS);
  if (!crudo) return [ETAPA_PRIMERA];
  try {
    const e = JSON.parse(crudo);
    const lista = Array.isArray(e) ? e.filter((x) => x && typeof x.id === 'string' && x.id && typeof x.nombre === 'string') : [];
    return lista.length ? lista : [ETAPA_PRIMERA];
  } catch { return [ETAPA_PRIMERA]; }
}

// Deja la oferta en el tablero y cuenta que paso: { creada, ya, tarjeta, etapa, whatsapp }. Una lectura de las etapas, una de la tarjeta y UNA escritura solo si no estaba
// (o si estaba sin WhatsApp y ahora llega uno: regla 5). `whatsapp`: true si la tarjeta queda con el boton, 'agregado' si se lo puso a una que ya estaba.
// Lanza si KV falla: quien llama decide no guardar nada a medias.
export async function mandarAlTablero(env, idOferta, oferta, fila, ahora) {
  const clave = PREFIJO_TARJETA + idTarjeta(idOferta);
  const etapas = await leerEtapas(env);
  const crudo = await env.SUITE.get(clave);
  let hay = null;
  if (crudo) { try { hay = JSON.parse(crudo); } catch { hay = null; } }   // una tarjeta ilegible se reemplaza
  if (hay && typeof hay === 'object') {
    const donde = etapas.find((e) => e.id === hay.etapa);
    const nuevo = hay.whatsapp || Array.isArray(hay) ? '' : whatsappDeOferta(oferta);
    if (nuevo) { hay.whatsapp = nuevo; await env.SUITE.put(clave, JSON.stringify(hay)); }
    return { creada: false, ya: true, tarjeta: idTarjeta(idOferta), etapa: donde ? donde.nombre : '', whatsapp: nuevo ? 'agregado' : !!hay.whatsapp };
  }
  const destino = etapaNuevaOferta(etapas);
  const tarjeta = tarjetaDeOferta(idOferta, oferta && typeof oferta === 'object' ? oferta : {}, fila, destino.id, ahora || new Date().toISOString());
  await env.SUITE.put(clave, JSON.stringify(tarjeta));
  return { creada: true, ya: false, tarjeta: tarjeta.id, etapa: destino.nombre, whatsapp: !!tarjeta.whatsapp };
}
