// Los ORIGINALES de las variaciones (2026-11-13, «Hacerla en el servidor» de /tablero › Variaciones): donde esta en Google Drive nuestro video del que sale cada variacion.
//
// Una variacion de «Revivir ofertas» dice `base` = «101» (nuestro anuncio que mas vendio) y `oferta` = «Sermones Biblicos». El video esta en el Drive del equipo con un nombre escrito a mano:
// «5000 sermones 101 .mp4», «KINESIOTAPING 105 .mp4», «conservas 101 argentina .mp4», «fieltro_101.mp4»… El servidor del editor (VPS, editor-anuncios/scripts/originales.py) recorre
// OFERTAS LATAM y sube al dash la lista CRUDA de archivos (id de Drive, nombre, carpeta, raiz, MB y fecha) por /api/variacion-editor. Aqui se interpreta cada nombre (oferta, numero de anuncio,
// pais) y se eligen los candidatos de cada variacion. El enlace de Drive se ARMA aqui con el id (solo letras, digitos, «_» y «-»): nada del texto que llega se vuelve un href.
//
// Puro (sin KV ni red): lo usan functions/api/variacion-editor.js y functions/api/tablero.js, y scripts/probar-variacion-editor.mjs lo prueba con los nombres reales de arriba.

export const RE_ID_DRIVE = /^[A-Za-z0-9_-]{15,100}$/;
// El nombre del archivo de una variacion: empieza por letra o digito, solo letras, digitos, espacio, punto, parentesis, «_» y «-», y termina en .mp4 (sin barras: no sale de su carpeta).
export const RE_ARCHIVO = /^[\p{L}\p{N}][\p{L}\p{N} ._()-]{0,110}\.mp4$/u;

export function enlaceDrive(id) {
  const s = String(id == null ? '' : id);
  return RE_ID_DRIVE.test(s) ? 'https://drive.google.com/file/d/' + s + '/view' : '';
}

// El indice se guarda PARTIDO por las 3 primeras cifras del numero («101», «101.4» y «101.2.1» van juntos en `varedit:i:101`; los «AD 3» en `varedit:i:ad`) y con un resumen chico aparte.
// Es por la CPU de una Function gratuita (~10 ms): un indice de 1.500 archivos son ~280 KB y leerlo entero costaba unos 3 a 10 ms en CADA apertura del tablero; asi /api/tablero lee solo
// los grupos de los numeros que usan sus variaciones (medido en scripts/probar-variacion-editor.mjs). El resumen dice que grupos hay (`grupos`: { '101': huella, … }): un grupo que no esta
// ahi es que ningun video lleva ese numero.
export const PREFIJO_GRUPO = 'varedit:i:';
export const CLAVE_META = 'varedit:indice:meta';
export const RE_GRUPO = /^(?:1\d\d|ad)$/;
export function grupoDe(n) { const s = String(n == null ? '' : n); return /^1\d\d/.test(s) ? s.slice(0, 3) : /^ad\d/.test(s) ? 'ad' : ''; }
// Los grupos que lista el resumen; null si el servidor todavia no ha mandado ningun indice.
export function gruposDelMeta(meta) {
  if (!meta || typeof meta !== 'object' || !meta.grupos || typeof meta.grupos !== 'object' || Array.isArray(meta.grupos)) return null;
  return new Set(Object.keys(meta.grupos).filter((g) => RE_GRUPO.test(g)));
}

// Texto de afuera (nombres de Drive, notas del servidor): sin caracteres de control ni de direccion (los que voltean el texto en pantalla), espacios juntos y con tope de largo.
const RAROS = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/;
const RAROS_G = new RegExp(RAROS.source, 'g');
export function textoLimpio(v, max) {
  let s = String(v == null ? '' : v);
  if (RAROS.test(s)) s = s.replace(RAROS_G, ' ');
  return s.replace(/\s+/g, ' ').trim().slice(0, max);
}

// Sin tildes y en minusculas: «Sermónes» = «sermones».
function normal(s) { return String(s == null ? '' : s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }

// Palabras que no dicen de que oferta es el archivo.
const VACIAS = new Set(['de', 'del', 'la', 'el', 'los', 'las', 'y', 'para', 'con', 'en', 'por', 'latam', 'variacion', 'variaciones', 'version', 'final', 'nuevo', 'nueva', 'copia', 'copy',
  'video', 'anuncio', 'mp4', 'mov', 'm4v', 'correccion', 'corrccion', 'corregido', 'latinoamerica', 'latonoamerica', 'aumento', 'velocidad', 'formato', 'formatos',
  'ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'sept', 'oct', 'nov', 'dic', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
  'noviembre', 'diciembre']);
// Un video de referencia (de otra empresa) o un b-roll NO es nuestro original aunque lleve un numero: «REFERENCIA sermones 101.mp4» no se ofrece.
const NO_ORIGINAL = new Set(['referencia', 'referencias', 'ref', 'broll']);
// El pais de una copia hecha para un pais («conservas 101 argentina»). Las de dos palabras van con su nombre entero. (Un Map: «constructor» no es un pais.)
const PAISES = new Map(Object.entries({ argentina: 'argentina', ecuador: 'ecuador', colombia: 'colombia', mexico: 'mexico', venezuela: 'venezuela', guatemala: 'guatemala', peru: 'peru', bolivia: 'bolivia',
  chile: 'chile', salvador: 'elsalvador', uruguay: 'uruguay', paraguay: 'paraguay', panama: 'panama', costa: 'costarica', rica: 'costarica', dominicana: 'dominicana', honduras: 'honduras',
  nicaragua: 'nicaragua', espana: 'espana', usa: 'usa', guate: 'guatemala', veenzuela: 'venezuela' }));
// Marcas de version que no dicen de que oferta es: «dinamicas102_hookB», «sermones 101 var2», «gancho3».
const RE_RUIDO = /^(?:hook|gancho|var)[a-z]?\d{0,2}[a-z]?$/;
// Un numero pegado a la palabra («dinamicas102», «cosmetica103»): se separa.
const RE_PEGADO = /^([a-z]{3,})(1\d\d(?:\.\d{1,3}){0,3})$/;
// El numero de un anuncio: 101, 101.4, 101.2.1 (del 100 al 199, como numera el equipo) o AD + 1-2 digitos (AD3). «5000» (de «5000 sermones») no es un numero de anuncio.
const RE_NUM = /^1\d\d(?:\.\d{1,3}){0,3}$/;
const RE_AD_NUM = /^ad(1\d\d(?:\.\d{1,3}){0,3})$/;
const RE_AD_CORTO = /^ad(\d{1,2})$/;
const RE_DOS_CIFRAS = /^\d{1,2}$/;
const RE_CIFRAS = /^\d+$/;
const RE_EXT = /\.(mp4|mov|m4v)\s*$/i;
const RE_NO_ASCII = /[^\x00-\x7f]/;
const RE_BROLL = /(^|[^a-z0-9])b[ _-]?roll([^a-z0-9]|$)/;

function sinPuntos(t) { return t.replace(/^\.+|\.+$/g, ''); }

// «5000 sermones 101 .mp4» -> { o: 'sermones', palabras: ['sermones'], n: '101', p: '' }. null si no trae numero de anuncio o palabra de oferta.
// Se lee con el numero de anuncio que aparece de ULTIMO. Escrito con pocas expresiones regulares por nombre: el servidor manda cientos de nombres de una vez (ver la nota de la CPU arriba).
export function parsearNombre(nombre) {
  const crudo = String(nombre == null ? '' : nombre).replace(RE_EXT, '');
  const s = RE_NO_ASCII.test(crudo) ? normal(crudo) : crudo.toLowerCase();
  if (s.indexOf('roll') !== -1 && RE_BROLL.test(s)) return null;
  const t = [];
  for (const parte of s.split(/[^a-z0-9.]+/)) {
    const x = parte.charCodeAt(0) === 46 || parte.charCodeAt(parte.length - 1) === 46 ? sinPuntos(parte) : parte;
    if (!x) continue;
    if (NO_ORIGINAL.has(x)) return null;
    const ultima = x.charCodeAt(x.length - 1), primera = x.charCodeAt(0);
    let m;
    if (ultima >= 48 && ultima <= 57 && primera >= 97 && primera <= 122 && (m = RE_PEGADO.exec(x))) { t.push(m[1], m[2]); continue; }
    t.push(x);
  }
  let n = '', u1 = -1, u2 = -1;
  for (let i = 0; i < t.length; i++) {
    const x = t[i], c = x.charCodeAt(0);
    if (c === 49) { if (RE_NUM.test(x)) { n = x; u1 = i; u2 = -1; } continue; }          // empieza por «1»
    if (c !== 97 || x.charCodeAt(1) !== 100) continue;                                // empieza por «ad» («AD 3», «ads 2»)
    let m;
    if (x.length === 2 || x === 'ads') {
      const y = i + 1 < t.length ? t[i + 1] : '';
      if (RE_NUM.test(y)) { n = y; u1 = i; u2 = i + 1; i++; }
      else if (RE_DOS_CIFRAS.test(y)) { n = 'ad' + Number(y); u1 = i; u2 = i + 1; i++; }
    } else if ((m = RE_AD_NUM.exec(x))) { n = m[1]; u1 = i; u2 = -1; }
    else if ((m = RE_AD_CORTO.exec(x))) { n = 'ad' + Number(m[1]); u1 = i; u2 = -1; }
  }
  if (!n) return null;
  let p = '';
  const palabras = [];
  for (let i = 0; i < t.length; i++) {
    if (i === u1 || i === u2) continue;
    const x = t[i];
    if (PAISES.has(x)) { if (!p) p = PAISES.get(x); continue; }
    if (VACIAS.has(x) || RE_CIFRAS.test(x) || RE_RUIDO.test(x)) continue;
    let letras = 0;
    for (let k = 0; k < x.length && letras < 3; k++) { const d = x.charCodeAt(k); if (d >= 97 && d <= 122) letras++; }
    if (letras < 3) continue;
    palabras.push(x);
  }
  if (!palabras.length) return null;
  return { o: palabras.join(' '), palabras, n, p };
}

// El numero de la base de una variacion, como lo guarda parsearNombre: «AD 101», «101», «AD101» -> «101»; «AD 3», «AD3» -> «ad3»; «101.2» -> «101.2». '' si no es un numero de anuncio.
export function nucleo(base) {
  const s = normal(base).trim().replace(/\s+/g, ' ');
  let m = /^(?:ad ?)?(1\d\d(?:\.\d{1,3}){0,3})$/.exec(s);
  if (m) return m[1];
  m = /^ad ?(\d{1,2})$/.exec(s);
  return m ? 'ad' + Number(m[1]) : '';
}

// ¿El archivo es de esta oferta? Alguna palabra de 4+ letras del nombre de la oferta es igual a una del archivo, o una empieza por la otra y comparten 5+ letras («sermones»/«sermon»,
// «dinamicas»/«dinamica»). Devuelve cuantas palabras de la oferta calzan (0 = no es de esta oferta).
function palabrasOferta(oferta) { return normal(oferta).split(/[^a-z0-9]+/).filter((w) => /^[a-z]{4,}$/.test(w)); }
function calza(a, b) { return a === b || (Math.min(a.length, b.length) >= 5 && (a.indexOf(b) === 0 || b.indexOf(a) === 0)); }
// delArchivo: las palabras del archivo YA normalizadas (asi se guardan en el indice).
function puntajeNormal(deOferta, delArchivo) {
  let pts = 0;
  for (const w of deOferta) { for (const f of delArchivo) if (calza(w, f)) { pts++; break; } }
  return pts;
}
function palabrasDe(v) { return (Array.isArray(v) ? v.map((x) => String(x)) : String(v == null ? '' : v).split(' ')).map(normal).filter(Boolean); }
export function coincideOferta(oferta, palabras) { return puntajeNormal(palabrasOferta(oferta), palabrasDe(palabras)) > 0; }

// Los archivos del indice agrupados por numero, con sus palabras ya separadas. Se calcula UNA vez por indice (o por grupo leido): /api/tablero pide los originales de todas sus
// variaciones con el mismo objeto, y lo que sale para cada numero + oferta tambien se guarda (varias variaciones salen del mismo anuncio). Medido con el indice real (1.143 videos,
// 60 variaciones): normalizar las palabras de cada archivo en cada variacion costaba ~9 ms por apertura del tablero.
const porNumero = new WeakMap();
function grupos(indice) {
  let g = porNumero.get(indice);
  if (g) return g;
  g = { n: new Map(), hechos: new Map() };
  for (const a of Array.isArray(indice.archivos) ? indice.archivos : []) {
    if (!a || typeof a !== 'object' || !RE_ID_DRIVE.test(String(a.id || '')) || !a.n) continue;
    const k = String(a.n), o = String(a.o == null ? '' : a.o);
    if (!g.n.has(k)) g.n.set(k, []);
    // `o` llega normalizado de parsearNombre; si no (indice viejo o raro) se normaliza aqui, una vez.
    g.n.get(k).push({ a, w: (/^[a-z0-9 ]*$/.test(o) ? o : normal(o)).split(' ').filter(Boolean) });
  }
  porNumero.set(indice, g);
  return g;
}

// Hasta 3 candidatos a original de una variacion: mismo numero EXACTO que su base y de su oferta. Solo los que calzan con MAS palabras de la oferta («Recetas Navidad» 102: el de
// «recetas-navidad_102», no el de «Airfryer recetas 102»). Si esos calzan solo con una parte de la oferta y son de productos distintos, no se adivina: «Manteles de crochet» 101 con
// «150 vestidos de croche 101» y «Zapatitos de crochet 101» (solo comparten «crochet») no da ninguno («No encontré el original en Drive»). Luego los sin pais (el original; los de pais
// son copias), luego el mas viejo (la carpeta con fecha va antes que «postproduciones») y luego por nombre.
export function originalesDe(indice, oferta, base) {
  const n = nucleo(base);
  if (!n || !indice || typeof indice !== 'object') return [];
  const deOferta = [...new Set(palabrasOferta(oferta))];
  if (!deOferta.length) return [];
  const g = grupos(indice), clave = n + '|' + deOferta.join(' ');
  let elegidos = g.hechos.get(clave);
  if (!elegidos) {
    const vistos = new Set();
    let out = [], mejor = 0;
    for (const { a, w } of g.n.get(n) || []) {
      if (vistos.has(a.id)) continue;
      const pts = puntajeNormal(deOferta, w);
      if (!pts || pts < mejor) continue;
      if (pts > mejor) { mejor = pts; out = []; }
      vistos.add(a.id);
      out.push(a);
    }
    if (mejor < deOferta.length && new Set(out.map((a) => String(a.o || ''))).size > 1) out = [];
    // Comparacion simple (no localeCompare: es lenta y aqui solo hace falta un orden fijo; las fechas son AAAA-MM-DD).
    const cmp = (u, v) => { u = String(u || ''); v = String(v || ''); return u < v ? -1 : u > v ? 1 : 0; };
    out.sort((x, y) => ((x.p ? 1 : 0) - (y.p ? 1 : 0)) || ((x.fecha ? 0 : 1) - (y.fecha ? 0 : 1)) || cmp(x.fecha, y.fecha) || cmp(x.nombre, y.nombre));
    elegidos = out.slice(0, 3);
    g.hechos.set(clave, elegidos);
  }
  return elegidos.map((a) => ({
    id: a.id, nombre: textoLimpio(a.nombre, 120), carpeta: textoLimpio(a.carpeta, 120), raiz: textoLimpio(a.raiz, 120), pais: textoLimpio(a.p, 20),
    mb: Number.isFinite(Number(a.mb)) ? Number(a.mb) : 0, fecha: /^\d{4}-\d{2}-\d{2}$/.test(String(a.fecha || '')) ? a.fecha : '', enlace: enlaceDrive(a.id),
  }));
}

// El numero con que empieza el nombre de una variacion del tablero («101.7 · Idea: …» -> «101.7»). COPIA de RevivirOfertas.numeroDe (templates/revivir-ofertas.js):
// scripts/probar-variacion-editor.mjs comprueba que las dos digan lo mismo.
export function numeroDeVariacion(nombre) { const m = /^((?:AD )?\d+(?:\.\d+)*)(?=\s|$|[·:|-])/i.exec(String(nombre || '').trim()); return m ? m[1].replace(/^ad /i, 'AD ') : ''; }

// El nombre del archivo de la variacion: el del original con su numero cambiado por el nuevo y la extension .mp4 pegada («5000 sermones 101 .mp4» + «101.7» -> «5000 sermones 101.7.mp4»).
// '' si el original no trae numero o lo que sale no es un nombre de archivo seguro.
export function nombreArchivoVariacion(nombreOriginal, numero) {
  const num = String(numero == null ? '' : numero).trim();
  if (!RE_NUM.test(num)) return '';
  const s = textoLimpio(nombreOriginal, 200).replace(/\.(mp4|mov|m4v)\s*$/i, '').trim();
  const partes = s.split(/([^\p{L}\p{N}.]+)/u);           // con los separadores, para volver a armarlo igual
  let ultimo = -1, pegado = false;
  for (let i = 0; i < partes.length; i += 2) {
    const x = sinPuntos(normal(partes[i]));
    if (RE_NUM.test(x) || /^ad1\d\d(?:\.\d{1,3}){0,3}$/.test(x)) { ultimo = i; pegado = false; }
    else if (RE_PEGADO.test(x)) { ultimo = i; pegado = true; }
  }
  if (ultimo < 0) return '';
  const viejo = sinPuntos(partes[ultimo]);
  // «dinamicas102» + «102.1» -> «dinamicas102.1» (la palabra se queda pegada, como la escribio el equipo)
  partes[ultimo] = pegado ? viejo.replace(/1\d\d(?:\.\d{1,3}){0,3}$/, num) : /^ad/i.test(viejo) ? viejo.slice(0, 2) + num : num;
  let r = partes.join('').replace(/[^\p{L}\p{N} ._()-]/gu, ' ').replace(/\s+/g, ' ').trim().replace(/^[^\p{L}\p{N}]+/u, '');
  r = r.slice(0, 111).trim();
  r = r + '.mp4';
  return RE_ARCHIVO.test(r) ? r : '';
}
