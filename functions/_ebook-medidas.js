// Cuanto mide un texto, sin navegador: lo necesita el maquetador de paginas de ebook (functions/_ebook-maqueta.js) para decidir, ANTES de
// escribir el HTML, que tamaño de letra deja TODO el texto dentro de la hoja.
//
// Un servidor (Cloudflare) no tiene fuentes ni diseño de pagina. Lo que si se puede es sumar el ancho de cada letra con las medidas de
// Arial/Helvetica (las que toda fuente "sans" del sistema imita) y partir en lineas como parte un navegador: palabra a palabra, la
// palabra que no entra pasa a la linea siguiente. La cuenta se hace de forma CONSERVADORA (se mide un poco mas ancho de lo real, ver
// MARGEN_ANCHO): si se equivoca, es contando mas lineas de las que salen, y lo unico que pasa es que sobra un poco de blanco abajo.
// Contar de menos seria lo grave (el texto se saldria de la hoja), asi que ese es el error que se evita.
//
// Las medidas son milesimas de em (el ancho de la letra a tamaño 1000). Se comprueban contra Segoe UI, Arial, Tahoma, Calibri y Verdana midiendo
// hojas reales en un navegador de verdad: scripts/probar-ebook-maqueta.mjs.
//
// Es CODIGO QUE CORRE EN CADA PAGINA (en el plan gratuito de Cloudflare una llamada tiene ~10 ms de CPU), asi que esta hecho para ser barato: cada
// texto se parte en palabras y se mide UNA vez (prepararTexto), y contar las lineas a otro ancho o tamaño de letra son solo sumas sobre ese resultado
// (recalcular una suma sobre ~30 numeros cuesta menos que recordarla: no hay memoria por ancho).

const ARIAL = [
  // ' ' !   "   #   $   %   &   '   (   )   *   +   ,   -   .   /
  278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
  // 0-9
  556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
  // :   ;   <   =   >   ?   @
  278, 278, 584, 584, 584, 556, 1015,
  // A-Z
  667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
  // [   \   ]   ^   _   `
  278, 278, 278, 469, 556, 333,
  // a-z
  556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
  // {   |   }   ~
  334, 260, 334, 584,
];

// Lo que no es ASCII: signos que salen en español y en las citas; lo demas (letras con tilde) se mide como su letra base.
const OTROS = {
  '¿': 611, '¡': 333, '«': 556, '»': 556, '“': 333, '”': 333, '‘': 222, '’': 222, '„': 333, '—': 1000, '–': 556, '…': 1000, '•': 350, '·': 278,
  '°': 400, 'º': 365, 'ª': 365, '×': 584, '€': 556, '£': 556, '¢': 556, '©': 737, '®': 737, '™': 1000, '´': 333, '¨': 333,
  'ß': 611, 'æ': 889, 'œ': 944, 'Æ': 1000, 'Œ': 1000, 'ø': 611, 'Ø': 778,
};
// Los dos invisibles se arman con su codigo (escritos tal cual en el archivo no se distinguen de un espacio corriente).
OTROS[String.fromCharCode(0xA0)] = 278;       // espacio que no parte linea
OTROS[String.fromCharCode(0x200B)] = 0;       // espacio de ancho cero

// Cuanto se ensancha el texto en negrita respecto al normal (700 en Arial mide ~7-9 % mas; en Segoe UI ~7 %).
export const NEGRITA = 1.12;
// Margen de seguridad contra las fuentes que de verdad se usen. La pila de fuentes de la hoja empieza por Segoe UI (Windows), Roboto (Android),
// Helvetica Neue (Mac, iOS) y Arial (cualquiera): todas dentro de ±3 % de las medidas de Arial. scripts/probar-ebook-maqueta.mjs mide cientos de
// hojas en un navegador de verdad con cuatro fuentes distintas: con 1,02 ya se sale una hoja en Tahoma; con 1,04 ninguna.
export const MARGEN_ANCHO = 1.04;

const otros = new Map();
function anchoOtro(c) {
  let w = otros.get(c);
  if (w !== undefined) return w;
  const cod = c.codePointAt(0);
  if (OTROS[c] !== undefined) w = OTROS[c];
  else if (cod < 32) w = 0;
  else if (cod >= 0x0300 && cod <= 0x036f) w = 0;          // marca de tilde suelta
  else {
    // letra con tilde, ñ, ü...: la de su letra base
    const base = c.normalize('NFD').charAt(0);
    const cb = base.charCodeAt(0);
    w = (base !== c && cb >= 32 && cb <= 126) ? ARIAL[cb - 32] : 1100;     // emojis y alfabetos raros: anchos
  }
  otros.set(c, w);
  return w;
}

// Ancho de una cadena en milesimas de em, sin margen.
function milesimas(s) {
  let t = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 32 && c <= 126) t += ARIAL[c - 32];
    else {
      const cp = s.codePointAt(i);
      if (cp > 0xFFFF) i++;
      t += anchoOtro(String.fromCodePoint(cp));
    }
  }
  return t;
}

// Ancho en px de un texto de UNA linea a tamaño `fs` (px), con el margen de seguridad.
export function anchoDe(texto, fs, negrita) {
  return milesimas(String(texto)) / 1000 * fs * MARGEN_ANCHO * (negrita ? NEGRITA : 1);
}

// Un texto partido en palabras y medido. Se hace una vez por texto (y se recuerda: una pagina mide el mismo texto a ocho tamaños distintos).
const preparados = new Map();
export function prepararTexto(texto) {
  const crudo = String(texto);
  let p = preparados.get(crudo);
  if (p) return p;
  // Solo el espacio corriente, el tabulador y los saltos se juntan y parten linea: el espacio que no parte (U+00A0) mantiene unidas a sus palabras,
  // y contarlo como espacio corriente daria MENOS lineas de las que salen.
  const t = crudo.replace(/[ \t\r\n\f\v]+/g, ' ').trim();
  const palabras = t ? t.split(' ') : [];
  p = { palabras, w: palabras.map(milesimas) };
  if (preparados.size > 4000) preparados.clear();
  preparados.set(crudo, p);
  return p;
}

// Cuantas lineas ocupa un parrafo en una columna de `ancho` px a tamaño `fs`, partiendo como un navegador con `overflow-wrap:anywhere`:
// una palabra que no cabe en la linea pasa a la siguiente, y la que no cabe ni sola se corta por letras. `sangria` = ancho ya ocupado en la
// primera linea (la etiqueta que va pegada al comienzo del texto). `texto` puede ser el texto o lo que devolvio prepararTexto.
export function lineasDe(texto, ancho, fs, opciones) {
  const o = opciones || {};
  const p = typeof texto === 'string' ? prepararTexto(texto) : texto;
  const n = p.palabras.length;
  if (!n) return 0;
  const sangria = o.sangria > 0 ? o.sangria : 0;
  const k = fs * MARGEN_ANCHO * (o.negrita ? NEGRITA : 1) / 1000;
  // `espaciadoEm`: letter-spacing en em (las etiquetas en mayusculas van espaciadas): suma esa fraccion del tamaño por cada letra.
  const esp = (o.espaciadoEm || 0) * fs;
  const espacio = 278 * k + esp;
  let lineas = 1, usado = sangria;
  for (let i = 0; i < n; i++) {
    const wp = p.w[i] * k + esp * p.palabras[i].length;
    if (usado === 0) {
      if (wp <= ancho) { usado = wp; continue; }
    } else if (usado + espacio + wp <= ancho) { usado += espacio + wp; continue; }
    else { lineas++; usado = 0; if (wp <= ancho) { usado = wp; continue; } }
    // palabra mas ancha que la linea: se corta por letras
    for (const c of p.palabras[i]) {
      const wc = milesimas(c) * k + esp;
      if (usado + wc > ancho && usado > 0) { lineas++; usado = 0; }
      usado += wc;
    }
  }
  return lineas;
}
