// El maquetador de las paginas del ebook: recibe los campos de una pagina del plan y devuelve el HTML de la hoja. SIN IA, SIN tokens.
//
// Antes cada pagina le pedia a un modelo (Gemini) que "maquetara" texto que YA estaba escrito: meter titulo, parrafos y listas dentro de
// HTML y CSS. Eso era ~60-70 % de los tokens de un ebook y ~45 % de sus llamadas, para un trabajo que no necesita inteligencia, y encima
// a veces se pasaba de la hoja (llego a salirse 472 px) o se comia texto. Aqui lo hace codigo: siempre el mismo resultado para el mismo
// texto, gratis, y con una regla que el modelo no podia cumplir: NO SE PIERDE NADA Y NADA SE SALE DE LA HOJA.
//
// Como se garantiza que quepa sin navegador: el servidor no puede medir una pagina, asi que la ESTIMA con las mismas cifras con las que
// escribe el CSS (cada alto de linea, cada relleno y cada hueco son numeros de este archivo) y con anchos de letra un poco mas generosos
// que los reales (functions/_ebook-medidas.js). Prueba varios tamaños de letra, del mas grande al mas chico, y se queda con el primero en
// el que todo cabe. Si ni con letra de 14 px cabe, achica la foto de ESA pagina (lo justo) antes de achicar mas la letra, y avisa. Equivocarse
// solo puede dejar blanco de sobra abajo, no un texto cortado. scripts/probar-ebook-maqueta.mjs lo comprueba midiendo cientos de hojas en un
// navegador de verdad, con cuatro fuentes distintas.
//
// Reglas de estilo (las que pedia el prompt del maquetador antiguo): titulo 34-42 px, texto 16-18 px, interlineado 1,35, cajas blancas con
// radio 14 y sombra ligera, numero de pagina grande y decorativo, tip al final con el color secundario, y los bloques cortos (error comun,
// variacion, ejemplo, senal) diferenciados con su etiqueta en mayusculas pequeñas. El marco (foto arriba a sangre y un cuerpo de alto fijo)
// es el de siempre y sale de la reticula del tipo de entregable. Con mucho texto la letra baja hasta 14 px (el minimo legible).
//
// El CSS es IGUAL en todas las paginas (los valores de cada pagina viajan como variables en el elemento raiz): el PDF junta todas las hojas en un
// solo documento y un <style> que cambiara por pagina se pisaria con el de las demas.
//
// Es codigo que corre en CADA pagina (el plan gratuito de Cloudflare da ~10 ms de CPU por llamada): cada distribucion se calcula una vez, solo
// el ganador arma su HTML, y la altura de la foto no entra en la cuenta (se despeja al final).
import { lineasDe, anchoDe } from './_ebook-medidas.js';

export const ANCHO = 768;
export const ALTO = 1024;
const LADOS = 40;                      // relleno izquierdo y derecho del cuerpo
const ABAJO = 28;                      // relleno inferior del cuerpo
const W = ANCHO - 2 * LADOS;           // 688 px de ancho util
const FOTO_MIN = 120;                  // lo mas que se achica una foto para que quepa un texto largo

// Una reticula por tipo de entregable: cuanto de la hoja es foto. (El brief ya deduce el tipo desde el paso 1.)
//   recetario  la foto del plato es el argumento de venta: grande
//   devocional la foto acompaña y el blanco es parte del diseño: mediana y con aire
//   plantillas lo que importa es lo que la persona va a usar: foto chica, nada de adornos que quiten espacio
//   guia       foto mediana, texto en una columna
//   otro       el termino medio
export const RETICULAS = {
  recetario:  { nombre: 'recetario',  foto: 400, compacto: false, verso: false, aire: [1.3, 1] },
  devocional: { nombre: 'devocional', foto: 260, compacto: false, verso: true,  aire: [1.6, 1.3, 1] },
  plantillas: { nombre: 'plantillas', foto: 200, compacto: true,  verso: false, aire: [1] },
  guia:       { nombre: 'guia',       foto: 300, compacto: false, verso: false, aire: [1.3, 1] },
  otro:       { nombre: 'otro',       foto: 340, compacto: false, verso: false, aire: [1.3, 1] },
};
export function reticulaDe(tipo) {
  const k = String(tipo || '').toLowerCase();
  for (const nombre of Object.keys(RETICULAS)) if (k.indexOf(nombre) !== -1) return RETICULAS[nombre];
  return RETICULAS.otro;
}

// ---------------------------------------------------------------------------------------------------------------------------
// Los tamaños de letra, del mas grande al mas chico. Cada fila lleva TODO lo que cambia con el tamaño; el estimador y el CSS leen los mismos
// numeros. fs/lh: texto; ft/lt: titulo; fe/le: etiquetas y chips; pad: relleno de las cajas; gap: hueco entre bloques; pp: entre parrafos;
// gi: entre elementos de una lista; ge: entre la etiqueta y su texto; pt: aire entre la foto y el titulo. Desde el tamaño 3 las etiquetas de las
// cajas cortas van EN LA MISMA LINEA que su texto (ri), no en una linea aparte: ahorra ~20 px por caja y la hoja sigue distinguiendo cada bloque.
//   0-3  tamaño normal (18 a 15,5 px)   4-5  apretado (15 y 14 px, el minimo legible)   6-7  de emergencia (13 y 12 px)
const TIERS = [
  { fs: 18,   lh: 24.5, ft: 42, lt: 46, fe: 12,   le: 16, pad: 18, gap: 20, pp: 10, gi: 10, ge: 6, pt: 14 },
  { fs: 17,   lh: 23,   ft: 40, lt: 44, fe: 11.5, le: 15, pad: 16, gap: 18, pp: 9,  gi: 9,  ge: 5, pt: 14 },
  { fs: 16,   lh: 21.5, ft: 38, lt: 42, fe: 11,   le: 15, pad: 15, gap: 16, pp: 8,  gi: 8,  ge: 5, pt: 14 },
  { fs: 15.5, lh: 21,   ft: 36, lt: 40, fe: 11,   le: 14, pad: 12, gap: 13, pp: 7,  gi: 6,  ge: 4, pt: 14 },
  { fs: 15,   lh: 20,   ft: 34, lt: 38, fe: 10.5, le: 14, pad: 11, gap: 11, pp: 7,  gi: 6,  ge: 4, pt: 12 },
  { fs: 14,   lh: 17.5, ft: 31, lt: 35, fe: 10.5, le: 13, pad: 9,  gap: 9,  pp: 6,  gi: 5,  ge: 3, pt: 12 },
  { fs: 13,   lh: 16,   ft: 29, lt: 33, fe: 10,   le: 12, pad: 8,  gap: 8,  pp: 5,  gi: 4,  ge: 3, pt: 10 },
  { fs: 12,   lh: 14.5, ft: 27, lt: 31, fe: 9.5,  le: 11, pad: 6,  gap: 6,  pp: 4,  gi: 3,  ge: 2, pt: 10 },
];
const TIER_APRETADO = 4, TIER_EMERGENCIA = 6;

// El tamaño que de verdad se usa: el de la tabla con el "aire" (mas holgura entre bloques cuando sobra sitio) y, en plantillas, mas compacto.
function tierDe(i, aire, compacto) {
  const b = TIERS[i];
  const a = i <= 3 ? aire : 1;                          // el aire solo se regala con letra normal
  const k = compacto ? 0.8 : 1;
  const r = (x) => Math.round(x * 2) / 2;
  const t = Object.assign({}, b, { pad: r(b.pad * a * k), gap: r(b.gap * a * k), pp: r(b.pp * a), gi: r(b.gi * a) });
  if (compacto) { t.ft = Math.max(26, t.ft - 4); t.lt = Math.max(30, t.lt - 4); }
  t.fn = i <= 3 ? 58 : i <= 5 ? 50 : 42;                // numero de pagina grande, de color
  t.ri = i >= 3;                                        // etiqueta pegada al texto (sin linea propia)
  t.mk = Math.max(12, Math.floor(t.lh - 4));            // circulo con el numero de cada paso (siempre cabe dentro de una linea de texto)
  t.fv = Math.round(t.fs * 1.1 * 2) / 2;                // cita del devocional: un poco mas grande...
  t.lv = Math.round(t.lh * 1.12 * 2) / 2;               // ...y con mas aire entre lineas
  t.etqs = {};                                          // ancho de cada etiqueta pegada al texto (se calcula una vez)
  return t;
}

// ---------------------------------------------------------------------------------------------------------------------------
// Colores: lo que llega del navegador solo vale si es #rgb o #rrggbb. El resto de lo que se calcula (tintes, texto legible sobre cada color)
// sale de aqui, para que un color claro elegido a mano no deje letra blanca sobre amarillo.
export function colorSeguro(c, defecto) {
  const m = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec(String(c == null ? '' : c).trim());
  if (!m) return defecto;
  let h = m[1].toLowerCase();
  if (h.length === 3) h = h.split('').map((x) => x + x).join('');
  return '#' + h;
}
const rgb = (hex) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
const hex2 = (n) => ('0' + Math.max(0, Math.min(255, Math.round(n))).toString(16)).slice(-2);
function luminancia(hex) {
  const [r, g, b] = rgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const contraste = (l1, l2) => (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
const rgba = (hex, a) => { const [r, g, b] = rgb(hex); return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')'; };
// El color, oscurecido hasta que se lea sobre blanco (contraste 4,5): para etiquetas y chips.
function oscurecer(hex) {
  let [r, g, b] = rgb(hex);
  for (let i = 0; i < 14; i++) {
    const h = '#' + hex2(r) + hex2(g) + hex2(b);
    if (contraste(luminancia(h), 1) >= 4.5) return h;
    r *= 0.88; g *= 0.88; b *= 0.88;
  }
  return '#' + hex2(r) + hex2(g) + hex2(b);
}
// Blanco o casi negro, el que mejor se lea sobre ese color.
const textoSobre = (hex) => (contraste(luminancia(hex), 1) >= contraste(luminancia(hex), luminancia('#14141b')) ? '#ffffff' : '#14141b');

// ---------------------------------------------------------------------------------------------------------------------------
// Lo que llega de la pagina, limpio. Nada de esto se confia: viene de un modelo que lee texto ajeno, o de una peticion hecha a mano.
const MAX = { titulo: 300, texto: 20000, corto: 3000, item: 1500, items: 60 };

function aTexto(v) {
  if (typeof v === 'number' && isFinite(v)) return String(v);
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.filter((x) => typeof x === 'string' || typeof x === 'number').join('\n');      // un parrafo por elemento
  return '';
}
// Los caracteres de control (todos menos el tabulador y el salto de linea). La expresion se arma con sus codigos: escritos tal cual en el archivo no se verian.
const CONTROL = new RegExp('[' + String.fromCharCode(0) + '-' + String.fromCharCode(8) + String.fromCharCode(11) + String.fromCharCode(12) + String.fromCharCode(14) + '-' + String.fromCharCode(31) + ']', 'g');
function limpio(v, max) {
  let s = aTexto(v);
  if (s.indexOf('\r') !== -1) s = s.split('\r\n').join('\n').split('\r').join('\n');
  s = s.replace(CONTROL, '');                                           // sin caracteres de control
  if (s.indexOf('__FOTO__') !== -1) s = s.split('__FOTO__').join('FOTO');   // el marcador de la foto no puede colarse en un texto
  s = s.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  if (s.length <= max) return s;                                        // (caracteres de verdad <= unidades de texto <= max)
  return Array.from(s).slice(0, max).join('').trim();
}
function lista(v, max, largo) {
  const a = typeof v === 'string' ? v.split('\n') : v;
  if (!Array.isArray(a)) return [];
  const out = [];
  for (const x of a.slice(0, max)) { const t = limpio(x, largo).split('\n').join(' ').trim(); if (t) out.push(t); }
  return out;
}
// La numeracion y las viñetas las pone el maquetador: se quita la que el modelo haya escrito para no ver "1. 1. Pica la cebolla".
const sinVineta = (t) => t.replace(/^\s*(?:[-*•·]\s+|\d{1,3}\s*[.)]\s+)/, '');

// Exportada: las paginas dibujadas por la IA (functions/_ebook-pagina-ia.js) leen la pagina del plan con la misma limpieza que la maqueta de codigo.
export function modeloDe(p) {
  const q = p && typeof p === 'object' ? p : {};
  const m = {
    numero: limpio(q.numero, 8).split('\n').join(' '),
    titulo: limpio(q.titulo, MAX.titulo).split('\n').join(' '),
    tiempo: limpio(q.tiempo, 80).split('\n').join(' '),
    porciones: limpio(q.porciones, 80).split('\n').join(' '),
    ingredientes: lista(q.ingredientes, MAX.items, 400).map(sinVineta),
    pasos: lista(q.pasos, MAX.items, MAX.item).map(sinVineta),
    texto: limpio(q.texto, MAX.texto),
    puntos: lista(q.puntos, MAX.items, MAX.item).map(sinVineta),
    ejemplo: limpio(q.ejemplo, MAX.corto), errorComun: limpio(q.errorComun, MAX.corto), senal: limpio(q.senal, MAX.corto),
    porQueFunciona: limpio(q.porQueFunciona, MAX.corto), variacion: limpio(q.variacion, MAX.corto), tip: limpio(q.tip, MAX.corto),
  };
  m.esReceta = m.ingredientes.length > 0;
  return m;
}

// ¿La pagina es una receta? Si trae ingredientes: es lo que le da su maqueta de dos columnas y, fuera de aqui (functions/api/ebook-pagina.js), lo unico que
// permite pedirle a la foto «el plato servido». Misma definicion que usa modeloDe (m.esReceta), sin limpiar el resto de la pagina.
export function esPaginaDeReceta(p) {
  return !!(p && typeof p === 'object' && lista(p.ingredientes, MAX.items, 400).length > 0);
}

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESCAPES[c]);
// **negrita** (la unica marca de formato que los modelos escriben solos). Se aplica DESPUES de escapar, asi que no puede abrir una etiqueta.
const conNegritas = (t) => esc(t).replace(/\*\*([^*\n]{1,300}?)\*\*/g, '<strong>$1</strong>').split('**').join('');      // los ** que queden sueltos no se ven
const planos = new Map();
const plano = (t) => {
  let p = planos.get(t);
  if (p === undefined) { p = t.split('**').join(''); if (planos.size > 4000) planos.clear(); planos.set(t, p); }
  return p;
};

// Lineas de un texto en una columna. Con **negrita** se mide un poco mas ancho (la negrita ocupa mas).
function lineas(t, ancho, fs, sangria) {
  const p = plano(t);
  return lineasDe(p, ancho, p.length !== t.length ? fs * 1.04 : fs, { sangria: sangria || 0 });
}
// Lo que ocupa, en la primera linea, una etiqueta pegada al texto (en mayusculas, negrita, con su espaciado y 6 px de separacion).
const anchoEtiqueta = (etq, T) => {
  let w = T.etqs[etq];
  if (w === undefined) w = T.etqs[etq] = anchoDe(etq.toUpperCase(), T.fe, true) + 0.1 * T.fe * etq.length + 8;
  return w;
};

// ---------------------------------------------------------------------------------------------------------------------------
// El CSS. Igual en todas las paginas; lo que cambia viaja en las variables del elemento raiz. Todo alto, ancho y relleno de aqui esta en
// la estimacion de mas abajo: si se toca una medida del CSS hay que tocar la cuenta.
const FUENTE = '"Segoe UI",Roboto,"Helvetica Neue",Arial,Arimo,"Liberation Sans",sans-serif';
const CSS = [
  '.mq-hoja{position:absolute;inset:0;width:768px;height:1024px;overflow:hidden;background:#fff;color:#22222b;font-family:' + FUENTE + ';-webkit-print-color-adjust:exact;print-color-adjust:exact}',
  '.mq-hoja *{box-sizing:border-box;margin:0;padding:0;min-width:0}',
  '.mq-hoja .mq-foto{position:absolute;top:0;left:0;width:768px;height:var(--fh);overflow:hidden}',
  '.mq-hoja .mq-foto img{display:block;width:100%;height:100%;object-fit:cover}',
  '.mq-hoja .mq-fade{position:absolute;left:0;right:0;bottom:0;height:90px;background:linear-gradient(to bottom,rgba(255,255,255,0),#fff)}',
  '.mq-hoja .mq-cuerpo{position:absolute;left:0;top:var(--fh);width:768px;height:calc(1024px - var(--fh));padding:var(--pt) ' + LADOS + 'px ' + ABAJO + 'px;overflow:hidden}',
  '.mq-hoja .mq-cuerpo:before{content:"";position:absolute;top:-4px;left:' + LADOS + 'px;width:64px;height:4px;border-radius:2px;background:var(--c1)}',
  '.mq-hoja .mq-flujo{position:relative;height:100%;display:flex;flex-direction:column;gap:var(--gap)}',
  '.mq-hoja .mq-flujo>*{flex:0 0 auto}',
  '.mq-hoja .mq-cab{display:flex;align-items:flex-start;gap:18px}',
  '.mq-hoja .mq-cab-t{flex:1 1 0;display:flex;flex-direction:column;gap:8px}',
  '.mq-hoja .mq-num{flex:0 0 auto;font-size:var(--fn);line-height:.82;font-weight:700;letter-spacing:-1px;color:var(--c1);margin-top:2px}',
  '.mq-hoja .mq-t{font-size:var(--ft);line-height:var(--lt);font-weight:700;color:#14141b;overflow-wrap:anywhere;word-break:break-word}',
  '.mq-hoja .mq-chips{display:flex;flex-wrap:wrap;gap:8px}',
  '.mq-hoja .mq-chip{font-size:var(--fe);line-height:var(--le);font-weight:700;text-transform:uppercase;letter-spacing:.08em;padding:4px 10px;border-radius:999px;background:var(--c1a);color:var(--c1d)}',
  '.mq-hoja .mq-p{font-size:var(--fs);line-height:var(--lh);overflow-wrap:anywhere;word-break:break-word}',
  '.mq-hoja .mq-p+.mq-p{margin-top:var(--pp)}',
  '.mq-hoja .mq-verso{font-size:var(--fv);line-height:var(--lv);font-style:italic;color:#14141b;background:var(--c1a);border-left:4px solid var(--c1);border-radius:0 14px 14px 0;padding:var(--pad) calc(var(--pad) + 8px) var(--pad) calc(var(--pad) + 14px);overflow-wrap:anywhere;word-break:break-word}',
  '.mq-hoja .mq-caja,.mq-hoja .mq-lista{border-radius:14px;padding:var(--pad) calc(var(--pad) + 2px)}',
  '.mq-hoja .mq-caja{background:#fff;border:1px solid rgba(20,20,40,.1);box-shadow:0 2px 8px rgba(20,20,40,.08)}',
  '.mq-hoja .mq-caja.a1{border-left:4px solid var(--c1)}',
  '.mq-hoja .mq-caja.a2{border-left:4px solid var(--c2)}',
  '.mq-hoja .mq-lista{background:var(--c1a)}',
  '.mq-hoja .mq-etq{font-size:var(--fe);line-height:var(--le);font-weight:700;letter-spacing:.1em;text-transform:uppercase;margin-bottom:var(--ge);color:var(--c1d)}',
  '.mq-hoja .a2 .mq-etq{color:var(--c2d)}',
  '.mq-hoja .ri .mq-etq{display:inline;margin:0 6px 0 0;line-height:1}',
  '.mq-hoja .mq-ul{display:flex;flex-direction:column;gap:var(--gi)}',
  '.mq-hoja .mq-li{display:flex;align-items:flex-start;gap:10px;font-size:var(--fs);line-height:var(--lh)}',
  '.mq-hoja .mq-li>span{flex:1 1 0;overflow-wrap:anywhere;word-break:break-word}',
  '.mq-hoja .mq-n{flex:0 0 var(--mkw,var(--mk));width:var(--mkw,var(--mk));height:var(--mk);margin-top:calc((var(--lh) - var(--mk)) / 2);border-radius:999px;background:var(--c1);color:var(--c1t);font-size:calc(var(--fe) + 1px);font-weight:700;line-height:var(--mk);text-align:center}',
  '.mq-hoja .mq-b{flex:0 0 6px;width:6px;height:6px;margin-top:calc((var(--lh) - 6px) / 2);border-radius:50%;background:var(--c1)}',
  '.mq-hoja .mq-li.b{gap:8px}',
  '.mq-hoja .mq-dos,.mq-hoja .mq-fila{display:grid;gap:var(--gap)}',
  '.mq-hoja .mq-dos{align-items:stretch}',
  '.mq-hoja .mq-col{display:flex;flex-direction:column;gap:var(--gap)}',
  '.mq-hoja .mq-col>.mq-caja:last-child{flex:1 1 auto}',
  '.mq-hoja .mq-tip{margin-top:auto;border-radius:14px;padding:var(--pad) calc(var(--pad) + 4px);background:var(--c2);color:var(--c2t)}',
  '.mq-hoja .mq-tip .mq-etq{color:inherit;opacity:.85}',
  '.mq-hoja .mq-tip .mq-p{font-weight:600}',
  // pagina que ES una imagen entera (la dibuja la IA con el texto adentro): la foto ocupa toda la hoja
  '.mq-hoja.mq-ia img{position:absolute;top:0;left:0;display:block;width:768px;height:1024px;object-fit:cover}',
].join('');

// ---------------------------------------------------------------------------------------------------------------------------
// Bloques. Cada uno devuelve { h, html } con el alto que va a ocupar (sale de las mismas cifras con que se escribe el CSS) y el HTML como
// FUNCION: solo se arma el de la distribucion ganadora, no el de las que se descartan.
const ETQ = { ejemplo: 'Ejemplo', errorComun: 'Error común', senal: 'Señal de que funciona', porQueFunciona: 'Por qué funciona', variacion: 'Variación', tip: 'Tip' };
const ACENTO = { ejemplo: 'a1', errorComun: 'a1', porQueFunciona: 'a1', senal: 'a2', variacion: 'a2' };

export function parrafosDe(texto) { return texto.split('\n').map((x) => x.trim()).filter(Boolean); }

function bCabecera(m, T) {
  // El numero de pagina va en la esquina de la cabecera, grande y del color de la marca; el titulo y los chips usan el ancho que queda.
  const num = m.numero ? (/^\d$/.test(m.numero) ? '0' + m.numero : m.numero) : '';
  const wNum = num ? anchoDe(num, T.fn, true) + 6 : 0;
  const wTxt = num ? W - wNum - 18 : W;
  let h = 0;
  const chips = [];
  if (m.tiempo) chips.push('Tiempo · ' + m.tiempo);
  if (m.porciones) chips.push('Porciones · ' + m.porciones);
  if (m.titulo) h += lineasDe(m.titulo, wTxt, T.ft, { negrita: true }) * T.lt;
  if (chips.length) {
    // en fila; si no caben juntos pasan a la linea siguiente. Un chip mas ancho que la fila (un "tiempo" larguisimo) se parte en varias lineas.
    let filas = 1, usado = 0, altoFila = 0, alto = 0;
    for (const c of chips) {
      const lc = lineasDe(c.toUpperCase(), wTxt - 20, T.fe, { negrita: true, espaciadoEm: 0.08 });
      const w = Math.min(wTxt, anchoDe(c.toUpperCase(), T.fe, true) + 0.08 * T.fe * c.length + 20);
      const hc = lc * T.le + 8;
      if (usado && usado + 8 + w > wTxt) { filas++; alto += altoFila; usado = w; altoFila = hc; }
      else { usado += (usado ? 8 : 0) + w; altoFila = Math.max(altoFila, hc); }
    }
    alto += altoFila;
    h += (m.titulo ? 8 : 0) + alto + (filas - 1) * 8;
  }
  if (!m.titulo && !chips.length && !num) return null;
  h = Math.max(h, num ? Math.ceil(T.fn * 0.82) + 2 : 0);
  return {
    h: h + 1,
    html: () => '<div class="mq-cab"><div class="mq-cab-t">' + (m.titulo ? '<h1 class="mq-t">' + esc(m.titulo) + '</h1>' : '') +
      (chips.length ? '<div class="mq-chips">' + chips.map((c) => '<span class="mq-chip">' + esc(c) + '</span>').join('') + '</div>' : '') +
      '</div>' + (num ? '<div class="mq-num">' + esc(num) + '</div>' : '') + '</div>',
  };
}

function bParrafos(texto, T) {
  const ps = parrafosDe(texto);
  if (!ps.length) return null;
  let h = 0;
  ps.forEach((p, i) => { h += lineas(p, W, T.fs) * T.lh + (i ? T.pp : 0); });
  return { h: h + 1, html: () => '<div class="mq-txt">' + ps.map((p) => '<p class="mq-p">' + conNegritas(p) + '</p>').join('') + '</div>' };
}

// La cita que abre un devocional: el primer parrafo si entero va entre comillas (y a lo sumo una referencia corta detras).
// Exportada: la pagina de devocional dibujada por la IA tambien separa la cita del resto del texto.
export function esCita(p) {
  if (p.length < 12 || p.length > 420) return false;
  if ('«“"'.indexOf(p.charAt(0)) === -1) return false;
  let cierre = -1;
  for (let i = p.length - 1; i > 8; i--) if ('»”"'.indexOf(p.charAt(i)) !== -1) { cierre = i; break; }
  return cierre !== -1 && p.length - cierre <= 72;
}
function bVerso(p, T) {
  const w = W - (4 + (T.pad + 14) + (T.pad + 8));
  const pl = plano(p);
  const h = lineasDe(pl, w, T.fv * (pl.length !== p.length ? 1.04 : 1)) * T.lv + 2 * T.pad;
  return { h: h + 1, html: () => '<blockquote class="mq-verso">' + conNegritas(p) + '</blockquote>' };
}

// El circulo con el numero de cada paso: con 10 pasos o mas el numero tiene dos cifras y el circulo se ensancha (todos igual, para que el texto quede alineado).
const anchoMarca = (n, T) => (n >= 10 ? T.mk + 8 : T.mk);
// Lista numerada en una caja con tinte (los puntos de una guia).
function bLista(items, T) {
  if (!items.length) return null;
  const chrome = 2 * (T.pad + 2);
  const mkw = anchoMarca(items.length, T);
  const wt = W - chrome - mkw - 10;
  let h = 2 * T.pad + (items.length - 1) * T.gi;
  items.forEach((it) => { h += Math.max(T.mk, lineas(it, wt, T.fs) * T.lh); });
  return {
    h: h + 1,
    html: () => '<div class="mq-lista"><div class="mq-ul" style="--mkw:' + mkw + 'px">' +
      items.map((it, i) => '<div class="mq-li"><b class="mq-n">' + (i + 1) + '</b><span>' + conNegritas(it) + '</span></div>').join('') + '</div></div>',
  };
}

// Una caja corta con etiqueta (error comun, ejemplo...), en un ancho dado.
const CROMO_CAJA = (T) => 5 + 2 * (T.pad + 2);      // borde izquierdo de 4 + derecho de 1 + relleno a los dos lados
// Cuanto alto ocupa una etiqueta en su propia linea: en una columna angosta («SEÑAL DE QUE FUNCIONA» en una fila de tres cajas) se parte en dos.
const altoEtiqueta = (etq, ancho, T) => lineasDe(etq.toUpperCase(), ancho, T.fe, { negrita: true, espaciadoEm: 0.1 }) * T.le;
function cajaEn(c, ancho, T) {
  // cada caja recuerda lo que mide a cada ancho (las filas y las columnas la miden varias veces al comparar repartos)
  const memo = c.memo || (c.memo = new Map());
  const previa = memo.get(ancho);
  if (previa) return previa;
  const interior = ancho - CROMO_CAJA(T);
  let r;
  // La etiqueta va pegada al texto solo si ocupa bastante menos que la linea: una mas ancha se partiria sobre si misma y la cuenta se complica.
  if (T.ri && anchoEtiqueta(c.etq, T) <= 0.6 * interior) {
    const h = 2 * T.pad + 2 + lineas(c.texto, interior, T.fs, anchoEtiqueta(c.etq, T)) * T.lh;
    r = { h: h + 1, html: () => '<div class="mq-caja ri ' + c.acento + '"><p class="mq-p"><b class="mq-etq">' + esc(c.etq) + '</b>' + conNegritas(c.texto) + '</p></div>' };
  } else {
    const h = 2 * T.pad + 2 + altoEtiqueta(c.etq, interior, T) + T.ge + lineas(c.texto, interior, T.fs) * T.lh;
    r = { h: h + 1, html: () => '<div class="mq-caja ' + c.acento + '"><div class="mq-etq">' + esc(c.etq) + '</div><p class="mq-p">' + conNegritas(c.texto) + '</p></div>' };
  }
  memo.set(ancho, r);
  return r;
}
// Las cajas cortas en filas de 1 a 3 cajas: se prueban todas las formas de repartirlas (en su orden) y se queda la que ocupa menos alto.
function bFilas(cajas, T) {
  const n = cajas.length;
  if (!n) return [];
  const fila = (i, k) => {                       // las cajas i .. i+k-1 en una fila, cada una en su parte del ancho
    const w = (W - (k - 1) * T.gap) / k;
    const bs = cajas.slice(i, i + k).map((c) => cajaEn(c, w, T));
    const h = Math.max.apply(null, bs.map((b) => b.h));
    return { h, html: () => (k === 1 ? bs[0].html() : '<div class="mq-fila" style="grid-template-columns:repeat(' + k + ',1fr)">' + bs.map((b) => b.html()).join('') + '</div>') };
  };
  const mejor = [{ h: 0, filas: [] }];            // mejor[i] = el mejor reparto de las primeras i cajas
  for (let i = 1; i <= n; i++) {
    let m = null;
    for (let k = 1; k <= 3 && k <= i; k++) {
      const f = fila(i - k, k), previo = mejor[i - k];
      const h = previo.h + f.h + (i - k > 0 ? T.gap : 0);
      if (!m || h < m.h - 0.01) m = { h, filas: previo.filas.concat([f]) };
    }
    mejor.push(m);
  }
  return mejor[n].filas;
}

// Una lista dentro de una caja con etiqueta (los ingredientes con puntos, los pasos con numeros), en un ancho dado.
function altoLista(items, ancho, T, numerada, etq) {
  const w = ancho - CROMO_CAJA(T) - (numerada ? anchoMarca(items.length, T) + 10 : 14);
  let h = 2 * T.pad + 2 + altoEtiqueta(etq, ancho - CROMO_CAJA(T), T) + T.ge + Math.max(0, items.length - 1) * T.gi + 1;
  items.forEach((x) => { h += Math.max(numerada ? T.mk : 0, lineas(x, w, T.fs) * T.lh); });
  return h;
}
function htmlLista(etq, items, numerada, clase, T) {
  const cuerpo = items.map((x, i) => (numerada
    ? '<div class="mq-li"><b class="mq-n">' + (i + 1) + '</b><span>' + conNegritas(x) + '</span></div>'
    : '<div class="mq-li b"><b class="mq-b"></b><span>' + conNegritas(x) + '</span></div>')).join('');
  return '<div class="mq-caja ' + clase + '"><div class="mq-etq">' + esc(etq) + '</div><div class="mq-ul" style="--mkw:' + (numerada ? anchoMarca(items.length, T) : 0) + 'px">' + cuerpo + '</div></div>';
}
function bListaCaja(etq, items, numerada, clase, T) {
  return { h: altoLista(items, W, T, numerada, etq), html: () => htmlLista(etq, items, numerada, clase, T) };
}

// Receta: los ingredientes a un lado, la preparacion al otro, y las cajas cortas (por que funciona, error comun...) debajo de la columna que
// menos estorbe. La proporcion de las columnas y el lado de cada caja se eligen por la combinacion que deja el bloque mas bajo. Las dos
// columnas terminan a la misma altura (la ultima caja de cada una se estira).
function bReceta(m, cortas, T, etqPasos) {
  let mejor = null;
  const k = cortas.length;
  for (const [a, b] of [[4, 8], [5, 7], [6, 6], [7, 5]]) {
    const wc = (W - T.gap) / (a + b), wL = wc * a, wR = wc * b;
    const hIng = altoLista(m.ingredientes, wL, T, false, 'Ingredientes'), hPas = altoLista(m.pasos, wR, T, true, etqPasos);
    const hL = cortas.map((c) => cajaEn(c, wL, T).h), hR = cortas.map((c) => cajaEn(c, wR, T).h);
    for (let mascara = 0; mascara < (1 << k); mascara++) {          // el bit i dice si la caja i va a la derecha
      let izq = hIng, der = hPas;
      for (let i = 0; i < k; i++) { if (mascara & (1 << i)) der += T.gap + hR[i]; else izq += T.gap + hL[i]; }
      const h = Math.max(izq, der);
      if (!mejor || h < mejor.h - 0.01) mejor = { h, a, b, mascara, wL, wR };
    }
  }
  return {
    h: mejor.h + 1,
    html: () => {
      const izq = [htmlLista('Ingredientes', m.ingredientes, false, 'a1', T)], der = [htmlLista(etqPasos, m.pasos, true, 'a2', T)];
      cortas.forEach((c, i) => {
        const aLaDerecha = !!(mejor.mascara & (1 << i));
        (aLaDerecha ? der : izq).push(cajaEn(c, aLaDerecha ? mejor.wR : mejor.wL, T).html());
      });
      return '<div class="mq-dos" style="grid-template-columns:' + mejor.a + 'fr ' + mejor.b + 'fr"><div class="mq-col">' + izq.join('') + '</div><div class="mq-col">' + der.join('') + '</div></div>';
    },
  };
}

function bTip(texto, T) {
  const chrome = 2 * (T.pad + 4);
  // el texto del tip va en seminegrita (600), que en Arial es negrita de verdad: se mide ~9 % mas ancho
  if (T.ri) {
    const h = 2 * T.pad + lineas(texto, W - chrome, T.fs * 1.09, anchoEtiqueta('Tip', T)) * T.lh;
    return { h: h + 1, html: () => '<div class="mq-tip ri"><p class="mq-p"><b class="mq-etq">Tip</b>' + conNegritas(texto) + '</p></div>' };
  }
  const h = 2 * T.pad + T.le + T.ge + lineas(texto, W - chrome, T.fs * 1.09) * T.lh;
  return { h: h + 1, html: () => '<div class="mq-tip"><div class="mq-etq">Tip</div><p class="mq-p">' + conNegritas(texto) + '</p></div>' };
}

// ---------------------------------------------------------------------------------------------------------------------------
// La pagina entera para un tamaño de letra: devuelve cuanto alto ocupa su contenido (sin mirar la foto: cuanto cabe lo decide quien llama).
function armar(m, ret, tierIdx, aire) {
  const T = tierDe(tierIdx, aire, ret.compacto);
  const partes = [];
  const ponerSi = (b) => { if (b) partes.push(b); };
  ponerSi(bCabecera(m, T));
  const ps = parrafosDe(m.texto);
  if (ret.verso && ps.length > 1 && esCita(ps[0])) { partes.push(bVerso(ps[0], T)); ponerSi(bParrafos(ps.slice(1).join('\n'), T)); }
  else ponerSi(bParrafos(m.texto, T));
  const cortas = [];
  for (const k of ['ejemplo', 'porQueFunciona', 'errorComun', 'variacion', 'senal']) if (m[k]) cortas.push({ etq: ETQ[k], texto: m[k], acento: ACENTO[k] });
  // Una receta con ingredientes Y pasos lleva sus cajas cortas dentro del bloque de dos columnas; lo demas va en una sola columna.
  const dosColumnas = m.esReceta && m.pasos.length > 0;
  if (dosColumnas) partes.push(bReceta(m, cortas, T, 'Preparación'));
  else if (m.esReceta) partes.push(bListaCaja('Ingredientes', m.ingredientes, false, 'a1', T));
  else if (m.pasos.length) partes.push(bListaCaja('Pasos', m.pasos, true, 'a2', T));
  ponerSi(bLista(m.puntos, T));
  if (!dosColumnas) bFilas(cortas, T).forEach((b) => partes.push(b));
  if (m.tip) partes.push(bTip(m.tip, T));
  const alto = partes.reduce((s, b) => s + b.h, 0) + Math.max(0, partes.length - 1) * T.gap + 2;
  return { T, partes, alto, tier: tierIdx, aire };
}
const interiorDe = (T, foto) => ALTO - foto - T.pt - ABAJO;
// La foto mas alta (de a 10 px) con la que un contenido de ese alto todavia cabe, o 0 si ni con la foto en su minimo.
const fotoQueCabe = (T, alto) => {
  const f = Math.floor((ALTO - T.pt - ABAJO - alto) / 10) * 10;
  return f >= FOTO_MIN ? f : 0;
};

// Elige como se arma la pagina. El orden de preferencia: letra grande y con aire primero; luego mas chica, hasta 14 px (el minimo legible);
// luego achicar la foto de ESA pagina lo justo, con la letra en 14; solo al final letra de emergencia. Cada distribucion se calcula una vez.
function elegir(m, ret) {
  const cabeNormal = (r) => r.alto <= interiorDe(r.T, ret.foto);
  // La altura de un contenido baja (o se queda igual) al achicar la letra, asi que el tamaño mas grande que cabe con la foto normal se busca por
  // biseccion entre 0 y 14 px (el 5): unas 3 distribuciones en vez de probar las seis una por una.
  const r5 = armar(m, ret, 5, 1);
  if (cabeNormal(r5)) {
    let lo = 0, hi = 5, mejor = r5;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const r = armar(m, ret, mid, 1);
      if (cabeNormal(r)) { hi = mid; mejor = r; } else lo = mid + 1;
    }
    // con letra normal, lo mas holgado (el "aire" entre bloques) que todavia quepa; una letra mas grande no cabria, asi que no se prueba
    if (hi <= 3) {
      for (const a of ret.aire) {
        if (a <= 1) break;
        const r = armar(m, ret, hi, a);
        if (cabeNormal(r)) return Object.assign(r, { foto: ret.foto, cabe: true });
      }
    }
    return Object.assign(mejor, { foto: ret.foto, cabe: true });
  }
  // Ni con 14 px cabe con la foto normal: se achica la foto de ESTA pagina lo justo, con la letra en 14.
  let f = fotoQueCabe(r5.T, r5.alto);
  if (f) return Object.assign(r5, { foto: Math.min(f, ret.foto), cabe: true });
  // Ni asi: letra de emergencia (13 y 12 px), con la foto lo mas alta que deje.
  let ultima = r5;
  for (const t of [6, 7]) {
    const r = armar(m, ret, t, 1);
    f = fotoQueCabe(r.T, r.alto);
    if (f) return Object.assign(r, { foto: Math.min(f, ret.foto), cabe: true });
    ultima = r;
  }
  return Object.assign(ultima, { foto: FOTO_MIN, cabe: false });
}

// ---------------------------------------------------------------------------------------------------------------------------
export function maquetarPagina(pagina, opciones) {
  const o = opciones || {};
  const m = modeloDe(pagina);
  const ret = reticulaDe(o.tipoEntregable);
  const c1 = colorSeguro(o.colorPrimario, '#e91e63'), c2 = colorSeguro(o.colorSecundario, '#2d7a3e');

  const r = elegir(m, ret);
  const T = r.T;
  const nivel = !r.cabe ? 'desborde' : r.tier >= TIER_EMERGENCIA ? 'minimo' : r.foto < ret.foto ? 'foto-reducida' : r.tier >= TIER_APRETADO ? 'apretado' : 'normal';
  const aviso = nivel === 'desborde'
    ? 'Esta página tiene demasiado texto para una hoja: aunque se achicó la letra y la foto al máximo, no cabe entero. Divide el contenido en dos páginas en el paso 2.'
    : nivel === 'minimo'
      ? 'Esta página tiene mucho texto: quedó con letra chica (' + T.fs + ' px) y la foto reducida para que cupiera todo. Si quieres letra más grande, acórtala en el paso 2 y pulsa «Reescribir texto».'
      : nivel === 'foto-reducida'
        ? 'Esta página tiene mucho texto: la foto se hizo más baja para que todo cupiera con letra legible.'
        : '';

  const vars = [
    '--fh:' + r.foto + 'px', '--pt:' + T.pt + 'px',
    '--c1:' + c1, '--c1a:' + rgba(c1, 0.12), '--c1d:' + oscurecer(c1), '--c1t:' + textoSobre(c1),
    '--c2:' + c2, '--c2a:' + rgba(c2, 0.12), '--c2d:' + oscurecer(c2), '--c2t:' + textoSobre(c2),
    '--fs:' + T.fs + 'px', '--lh:' + T.lh + 'px', '--ft:' + T.ft + 'px', '--lt:' + T.lt + 'px', '--fe:' + T.fe + 'px', '--le:' + T.le + 'px',
    '--fn:' + T.fn + 'px', '--fv:' + T.fv + 'px', '--lv:' + T.lv + 'px', '--pad:' + T.pad + 'px', '--gap:' + T.gap + 'px', '--pp:' + T.pp + 'px',
    '--gi:' + T.gi + 'px', '--ge:' + T.ge + 'px', '--mk:' + T.mk + 'px',
  ].join(';');
  const html = '<style>' + CSS + '</style>' +
    '<div class="mq-hoja" style="' + vars + '">' +
      '<div class="mq-foto"><img src="__FOTO__" alt=""><div class="mq-fade"></div></div>' +
      '<div class="mq-cuerpo"><div class="mq-flujo">' + r.partes.map((b) => b.html()).join('') + '</div></div>' +
    '</div>';
  return {
    html, aviso,
    ajuste: { nivel, tier: r.tier, fs: T.fs, lh: T.lh, foto: r.foto, fotoNormal: ret.foto, estimado: Math.round(r.alto * 10) / 10, disponible: interiorDe(T, r.foto), cabe: r.cabe, aire: r.aire, bloques: r.partes.length, alturas: r.partes.map((b) => b.h), hueco: T.gap },
  };
}

// El CSS suelto, para las pruebas (que lo comparan entre paginas).
export const CSS_MAQUETA = CSS;
