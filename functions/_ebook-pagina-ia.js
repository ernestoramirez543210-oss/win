// Paginas de ebook DIBUJADAS POR LA IA, con el texto adentro: la imagen ES la pagina.
//
// gpt-image-2 escribe bien en español y sigue una descripcion de diseño casi al pie de la letra: si se le dice donde va cada cosa y se le da el texto, devuelve una pagina terminada,
// con una tipografia y un diseño que ninguna plantilla de codigo iguala (es lo que hace el PDF de ejemplo del dueño: «Menu especial»). Cuesta lo mismo que la foto sola de una
// pagina de codigo -una imagen por pagina- y el diseño cambia segun la oferta (receta, devocional, guia, plantillas) y rota de una pagina a otra para que el libro no se vea repetido.
//
// El precio es que el texto ya no es texto: lo dibuja el modelo y puede equivocarse (en una prueba escribio «INGREDIENTS» donde se le pidio «INGREDIENTES»). Por eso cada pagina
// se VERIFICA despues (functions/_ebook-verificar.js: se le pide a un modelo que lea la imagen y se compara con lo pedido) y el cliente repite la que salga mal.
// Una pagina con demasiado texto para que quepa legible en una sola imagen no se dibuja: se arma con el diseño de codigo (functions/_ebook-maqueta.js), que no se pasa de la hoja.
//
// Aqui no hay IA de texto ni red: solo se arma el PROMPT de cada pagina y la lista de lo que se espera leer en ella. Todo es codigo y da siempre el mismo resultado para la misma pagina.
import { parrafosDe, esCita, colorSeguro, reticulaDe, CSS_MAQUETA } from './_ebook-maqueta.js';

// Cuantas palabras aguanta una pagina dibujada: con mas, la letra baja de lo legible o el modelo se come lineas. Una receta normal (ingredientes + pasos + tip) ronda 90-110.
export const PRESUPUESTO_PALABRAS = 170;

const palabras = (t) => (String(t == null ? '' : t).match(/\S+/g) || []).length;
// Las comillas rectas del texto pasan a simples porque cada texto va entre comillas rectas en el prompt; las tipograficas («» y “”) se dejan: no chocan con esas y el texto sale tal cual.
const unaLinea = (v, max) => String(v == null ? '' : v).replace(/\*\*/g, '').replace(/"/g, "'").replace(/\s+/g, ' ').trim().slice(0, max);
const q = (t) => '"' + unaLinea(t, 600) + '"';
const lista = (a) => a.map(q).join(', ');
// la descripcion de la foto viene del plan con mayuscula inicial: dentro de la frase del prompt va en minuscula (salvo una sigla)
const minuscula1 = (s) => (s.length > 1 && s.charAt(0) === s.charAt(0).toUpperCase() && s.charAt(1) === s.charAt(1).toLowerCase() ? s.charAt(0).toLowerCase() + s.slice(1) : s);

// Los textos de una pagina, ya limpios y en el orden en que se leen.
const ORDEN_CORTAS = ['ejemplo', 'porQueFunciona', 'errorComun', 'variacion', 'senal'];
const ETIQUETAS = { ejemplo: 'EJEMPLO', porQueFunciona: 'POR QUÉ FUNCIONA', errorComun: 'ERROR COMÚN', variacion: 'VARIACIÓN', senal: 'SEÑAL DE QUE FUNCIONA' };
function contenidoDe(m) {
  const n = /^\d{1,3}$/.test(m.numero) ? Number(m.numero) : 0;
  const cortas = ORDEN_CORTAS.filter((k) => m[k]).map((k) => ({ etq: ETIQUETAS[k], texto: m[k] }));
  return {
    n, numero: n ? String(n) : '', numero2: n ? String(n).padStart(2, '0') : '',
    titulo: m.titulo, tiempo: m.tiempo, porciones: m.porciones,
    parrafos: parrafosDe(m.texto), puntos: m.puntos, pasos: m.pasos, ingredientes: m.ingredientes, cortas, tip: m.tip,
  };
}

// Cuantas palabras tiene la pagina entera (lo que hay que dibujar).
export function palabrasDe(m) {
  const c = contenidoDe(m);
  let t = palabras(c.titulo) + palabras(c.tiempo) + palabras(c.porciones) + palabras(c.tip);
  c.parrafos.forEach((p) => { t += palabras(p); });
  [c.puntos, c.pasos, c.ingredientes].forEach((a) => a.forEach((x) => { t += palabras(x); }));
  c.cortas.forEach((x) => { t += palabras(x.texto) + palabras(x.etq); });
  return t;
}
export const cabeEnIA = (m) => palabrasDe(m) <= PRESUPUESTO_PALABRAS;

// ---------------------------------------------------------------------------------------------------------------------------
// Las descripciones de diseño. Cada una recibe el contenido y los datos comunes (colores, foto) y devuelve el parrafo «LAYOUT». Lo que la pagina no trae no se menciona.
const filaMeta = (c) => {
  const p = [];
  if (c.tiempo) p.push('a clock icon with ' + q(c.tiempo));
  if (c.porciones) p.push('a people icon with ' + q(c.porciones));
  return p.join(' and ');
};
const cortasTxt = (c, k, como) => (c.cortas.length
  ? ' ' + como + ' ' + c.cortas.map((x) => 'a small white card with a ' + k.c1 + ' left border, the small-caps bold label ' + q(x.etq) + ' and its text ' + q(x.texto)).join('; ') + '.'
  : '');
const tipTxt = (c, k, como) => (c.tip ? ' ' + como + ' with a lightbulb icon, the bold label ' + q('TIP') + ' and its text ' + q(c.tip) + '.' : '');
const parrafosTxt = (c) => (c.parrafos.length ? ' Paragraph text, ' + (c.parrafos.length > 1 ? 'in ' + c.parrafos.length + ' short paragraphs' : 'one block') + ': ' + lista(c.parrafos) + '.' : '');
const puntosTxt = (c, k) => (c.puntos.length ? ' A rounded box tinted with a very light ' + k.c1 + ' holding ' + c.puntos.length + ' numbered points (small ' + k.c1 + ' circles with white numbers), in this order: ' + lista(c.puntos) + '.' : '');
const pasosTxt = (c, k) => (c.pasos.length ? ' A ' + (c.puntos.length ? 'second ' : '') + 'rounded box with ' + c.pasos.length + ' numbered steps (small ' + k.c2 + ' circles with white numbers), in this order: ' + lista(c.pasos) + '.' : '');

const DISENOS = {
  // ----- recetas
  receta1(c, k) {
    const meta = filaMeta(c);
    return 'LAYOUT (recipe card). Top-left a big rounded-square badge in ' + k.c1 + ' with the white number ' + q(c.numero) + '; next to it the title in bold uppercase on one to three lines, the last line in ' + k.c1 + ': ' + q(c.titulo) + '.' +
      (meta ? ' Under the title a row with ' + meta + '.' : '') +
      ' Below, on the left a large rounded photo (' + k.foto + '), and on the right a white card with a ' + k.c1 + ' pill label ' + q('INGREDIENTES') + ' (a Spanish word: write it exactly like that) and a bulleted list: ' + lista(c.ingredientes) + '.' +
      (c.pasos.length ? ' Below, a ' + k.c1 + ' pill label ' + q('PREPARACIÓN') + ' with ' + c.pasos.length + ' numbered steps (small ' + k.c1 + ' circles with white numbers), in this order: ' + lista(c.pasos) + '.' : '') +
      cortasTxt(c, k, 'Next to or under the steps,') + tipTxt(c, k, 'In the bottom-right corner a soft tinted box');
  },
  receta2(c, k) {
    const meta = filaMeta(c);
    return 'LAYOUT (recipe card with a hero photo). A large rounded photo (' + k.foto + ') fills the top 40% of the page, inset with comfortable margins. A big rounded-square badge in ' + k.c1 + ' with the white number ' + q(c.numero) + ' overlaps the photo\'s top-left corner. ' +
      'Overlapping the photo\'s bottom edge, a white rounded card with a soft shadow holds the title in bold uppercase on one to three lines, the last line in ' + k.c1 + ': ' + q(c.titulo) + (meta ? ', and under it a row with ' + meta : '') + '. ' +
      'Below the card, two columns: on the left a white card with a ' + k.c1 + ' pill label ' + q('INGREDIENTES') + ' (a Spanish word: write it exactly like that) and a bulleted list: ' + lista(c.ingredientes) + '; ' +
      (c.pasos.length ? 'on the right a ' + k.c1 + ' pill label ' + q('PREPARACIÓN') + ' with ' + c.pasos.length + ' numbered steps (small ' + k.c1 + ' circles with white numbers), in this order: ' + lista(c.pasos) + '.' : 'on the right, calm empty space.') +
      cortasTxt(c, k, 'At the bottom of the columns,') + tipTxt(c, k, 'At the very bottom a full-width soft tinted bar');
  },
  // ----- guias (y todo lo que no es receta ni devocional)
  guia1(c, k) {
    return 'LAYOUT (editorial guide page). ' + (c.numero2 ? 'At the top right a very large number ' + q(c.numero2) + ' in ' + k.c1 + '; at the top left' : 'At the top') + ' the title in bold dark type on one to three lines: ' + q(c.titulo) + '. ' +
      'Below, a large rounded photo (' + k.foto + ') across the page width.' + parrafosTxt(c) + puntosTxt(c, k) + pasosTxt(c, k) +
      cortasTxt(c, k, 'Then,') + tipTxt(c, k, 'At the bottom a ' + k.c2 + ' rounded box');
  },
  guia2(c, k) {
    return 'LAYOUT (split guide page). The left 36% of the page is a full-height photo (' + k.foto + ') that bleeds to the left, top and bottom edges' + (c.numero2 ? ', with a white number ' + q(c.numero2) + ' inside a ' + k.c1 + ' circle near its top' : '') + '. ' +
      'The right 64% holds the content on a white background with generous margins: at the top the title in bold dark type with a short ' + k.c1 + ' accent bar under it: ' + q(c.titulo) + '.' +
      parrafosTxt(c) + puntosTxt(c, k) + pasosTxt(c, k) + cortasTxt(c, k, 'Then,') + tipTxt(c, k, 'At the bottom a ' + k.c2 + ' rounded box');
  },
  guia3(c, k) {
    return 'LAYOUT (poster-style guide page). A full-bleed photo (' + k.foto + ') covers the whole page. A large white rounded card with a soft shadow occupies the lower 65% of the page' + (c.numero2 ? '; a ' + k.c1 + ' circle with the white number ' + q(c.numero2) + ' overlaps the card\'s top edge on the left' : '') + '. ' +
      'Inside the card: the title in bold dark type: ' + q(c.titulo) + '.' + parrafosTxt(c) + puntosTxt(c, k) + pasosTxt(c, k) + cortasTxt(c, k, 'Then,') + tipTxt(c, k, 'At the bottom of the card a ' + k.c2 + ' band');
  },
  // ----- devocionales
  devocional1(c, k) {
    const ps = c.parrafos.slice();
    const verso = ps.length > 1 && esCita(ps[0]) ? ps.shift() : '';
    return 'LAYOUT (devotional page). A soft, dreamy full-bleed photo (' + k.foto + ') covers the whole page. In the lower two thirds a large cream rounded card (slightly translucent, with a soft shadow) holds the content, centered: a tiny ornament; the title in elegant serif: ' + q(c.titulo) + '.' +
      (verso ? ' The quote in larger italic serif: ' + q(verso) + '.' : '') +
      (ps.length ? ' The reflection text, in ' + (ps.length > 1 ? ps.length + ' short paragraphs' : 'one block') + ': ' + lista(ps) + '.' : '') +
      puntosTxt(c, k) + cortasTxt(c, k, 'Then,') + tipTxt(c, k, 'At the bottom of the card a pill-shaped band tinted with ' + k.c1);
  },
  devocional2(c, k) {
    const ps = c.parrafos.slice();
    const verso = ps.length > 1 && esCita(ps[0]) ? ps.shift() : '';
    return 'LAYOUT (devotional page). Warm cream background with a thin ' + k.c1 + ' border line. At the top center an arch-shaped (window-shaped) photo (' + k.foto + '). Below it, centered, the title in elegant serif: ' + q(c.titulo) + ', then a small ornament divider.' +
      (verso ? ' The quote in a framed box, in italic serif: ' + q(verso) + '.' : '') +
      (ps.length ? ' The reflection text, in ' + (ps.length > 1 ? ps.length + ' short paragraphs' : 'one block') + ': ' + lista(ps) + '.' : '') +
      puntosTxt(c, k) + cortasTxt(c, k, 'Then,') + tipTxt(c, k, 'At the bottom a pill-shaped band tinted with ' + k.c1);
  },
  // ----- plantillas (hojas para imprimir y usar)
  plantilla1(c, k) {
    return 'LAYOUT (printable worksheet). A ' + k.c1 + ' header band across the top holds the title in white bold type: ' + q(c.titulo) + (c.numero2 ? ' and, at the right, the number ' + q(c.numero2) + ' in a white circle' : '') + '; a small round photo (' + k.foto + ') sits at the left of the band. ' +
      'Below, a clean white area with the content laid out as something to USE and print: ' + (c.parrafos.length ? 'a short intro: ' + lista(c.parrafos) + '. ' : '') +
      (c.puntos.length ? c.puntos.length + ' items, each with an empty checkbox square at its left, in this order: ' + lista(c.puntos) + '. ' : '') +
      (c.pasos.length ? c.pasos.length + ' numbered boxes with a little writing space, in this order: ' + lista(c.pasos) + '. ' : '') +
      cortasTxt(c, k, 'Then,') + tipTxt(c, k, 'At the bottom a ' + k.c2 + ' rounded box') + ' Keep generous empty space so the page feels usable and printable.';
  },
};

// Que diseños rotan en cada tipo de entregable (la pagina n usa el n-esimo de la lista, dando la vuelta).
const ROTACION = {
  recetario: ['receta1', 'receta2'],
  devocional: ['devocional1', 'devocional2'],
  plantillas: ['plantilla1'],
  guia: ['guia1', 'guia2', 'guia3'],
  otro: ['guia1', 'guia2', 'guia3'],
};
const COMO_GUIA = ['guia1', 'guia3', 'guia2'];            // una pagina sin ingredientes dentro de un recetario: se arma como guia (la receta pide ingredientes)

export function disenoDe(m, tipoEntregable, indice) {
  const tipo = reticulaDe(tipoEntregable).nombre;
  const n = indice == null ? (/^\d{1,3}$/.test(m.numero) ? Number(m.numero) : 1) : indice;
  if (tipo === 'recetario' && !m.esReceta) return COMO_GUIA[(n + COMO_GUIA.length - 1) % COMO_GUIA.length];
  const lista2 = tipo === 'recetario' && m.esReceta ? ROTACION.recetario : (ROTACION[tipo] || ROTACION.otro);
  return lista2[(Math.max(1, n) - 1) % lista2.length];
}

const NOMBRE_EN = { recetario: 'cookbook', devocional: 'devotional', plantillas: 'printables', guia: 'guide', otro: 'digital' };
const ESTILO_POR_TIPO = {
  recetario: 'Photos are realistic, bright and appetizing, like a food magazine.',
  devocional: 'Photos are soft, warm and peaceful; typography is elegant and calm.',
  plantillas: 'Fresh, tidy and friendly; easy to print and fill in.',
  guia: 'Photos are realistic, bright and clear.',
  otro: 'Photos are realistic, bright and clear.',
};

// Lo que se espera leer en la pagina dibujada, para compararlo con lo que un modelo lea de la imagen: las palabras del titulo y de las etiquetas deben salir TODAS
// (estrictas); el resto del texto admite un par de lecturas falladas (cuerpo).
function esperadoDe(c, variante) {
  const estrictas = [c.titulo];
  if (c.ingredientes.length) estrictas.push('INGREDIENTES');
  if (c.pasos.length && /^receta/.test(variante)) estrictas.push('PREPARACIÓN');
  c.cortas.forEach((x) => estrictas.push(x.etq));
  if (c.tip) estrictas.push('TIP');
  const cuerpo = [c.tiempo, c.porciones].concat(c.parrafos, c.puntos, c.pasos, c.ingredientes, c.cortas.map((x) => x.texto), [c.tip]).filter(Boolean);
  return { estrictas, cuerpo };
}

// El prompt de una pagina. m: la pagina ya limpia (modeloDe); o: { tipoEntregable, colorPrimario, colorSecundario, foto, estiloFoto, indice, variante }.
export function promptPaginaIA(m, o) {
  const op = o || {};
  const c = contenidoDe(m);
  const tipo = reticulaDe(op.tipoEntregable).nombre;
  const variante = DISENOS[op.variante] ? op.variante : disenoDe(m, op.tipoEntregable, op.indice);
  const k = {
    c1: colorSeguro(op.colorPrimario, '#e91e63'), c2: colorSeguro(op.colorSecundario, '#2d7a3e'),
    foto: minuscula1(unaLinea(op.foto, 300)) || (m.esReceta ? 'the finished dish' : 'a single subject that illustrates the page topic'),
  };
  const kind = /^receta/.test(variante) ? 'recipe card' : /^devocional/.test(variante) ? 'devotional page' : /^plantilla/.test(variante) ? 'printable worksheet page' : 'guide page';
  const prompt =
    'Design ONE complete, finished ' + kind + ' for a professional ' + (NOMBRE_EN[tipo] || 'digital') + ' ebook (PDF), vertical 3:4, with ALL the text integrated into the design, ready to publish. Modern, clean, high-end digital-product look with generous spacing.\n' +
    'TEXT RULES. Every text given below must appear exactly as given: copy it letter by letter, in Spanish, with the same spelling, accents, numbers and punctuation. Never translate (section labels stay in Spanish), rephrase, shorten, add, drop or reorder words. ' +
    'Write NO other text on the page: no headers, footers, page counters, website, watermark or invented words. Keep every text large and perfectly legible.\n' +
    DISENOS[variante](c, k) + '\n' +
    'STYLE: modern premium digital-product design, palette ' + k.c1 + ' (primary) and ' + k.c2 + ' (secondary) on a warm white base, consistent rounded cards with soft shadows, clean sans-serif typography (bold geometric for titles, friendly humanist for body text), strong contrast, everything perfectly aligned. ' +
    ESTILO_POR_TIPO[tipo] + (op.estiloFoto ? ' Photographic mood to match the rest of the book: ' + unaLinea(op.estiloFoto, 300) + '.' : '') + ' No real people or celebrity likenesses.';
  return { prompt, variante, esperado: esperadoDe(c, variante) };
}

// ---------------------------------------------------------------------------------------------------------------------------
// La PORTADA con el titulo escrito dentro de la imagen (como el «Menu especial» de ejemplo): la tipografia sale integrada con la foto. Es una sola imagen por ebook.
const SUBTITULO_MAX = 92;
export function subtituloDe(promesa) {
  const t = unaLinea(promesa, 400);
  if (!t) return '';
  // 92 caracteres y no mas: la cinta de la portada aguanta dos lineas, y con la promesa entera (118) el modelo corto la ultima palabra en una prueba real.
  if (t.length <= SUBTITULO_MAX) return t;
  const frase = new RegExp('^(.{30,' + SUBTITULO_MAX + '}?[.!?])(\\s|$)').exec(t);      // la primera frase, si cabe
  if (frase) return frase[1];
  const corte = t.slice(0, SUBTITULO_MAX);
  const coma = Math.max(corte.lastIndexOf(','), corte.lastIndexOf(';'), corte.lastIndexOf(':'));
  if (coma >= 50) return corte.slice(0, coma);                                      // si no cabe entera, hasta una coma: no se corta a media frase
  const i = corte.lastIndexOf(' ');                                                 // sin coma, hasta una palabra completa
  return (i > 50 ? corte.slice(0, i) : corte).replace(/[,;:\s]+$/, '');
}
const PORTADAS = {
  recetario: {
    escena: 'A mouth-watering, bright overhead food-photography scene with the finished dishes beautifully styled (fresh ingredients around them, warm natural light), like a premium cookbook cover',
    letra: 'friendly, appetizing lettering: a bold rounded sans-serif for the key words mixed with an elegant handwritten script accent, like a modern cookbook or meal-plan cover',
  },
  devocional: {
    escena: 'A serene, warm, contemplative scene with soft golden natural light and a feeling of peace (a window, an open book, a quiet landscape), like a premium devotional book cover',
    letra: 'elegant lettering: a refined serif for the main words with a gentle handwritten script accent, calm and reverent',
  },
  plantillas: {
    escena: 'A bright, tidy flat-lay of colorful printable planner pages, checklists, sticky notes and stationery on a clean desk, like the cover of a premium printable kit',
    letra: 'fresh, friendly lettering: a bold rounded modern sans-serif with one key word highlighted, playful but clean',
  },
  otro: {
    escena: 'A clean, aspirational editorial scene that evokes the topic of the ebook, like a bestselling digital-product cover',
    letra: 'heavy, modern, high-impact sans-serif lettering with one key word highlighted in the accent color',
  },
};
export const TITULO_MAX = 140;
export function textosDePortada(brief) {
  const b = brief || {};
  return { titulo: unaLinea(b.titulo, TITULO_MAX) || 'Sin título', subtitulo: subtituloDe(b.promesa) };
}
export function promptPortadaIA(brief, estiloFoto, colorPrimario, colorSecundario) {
  const b = brief || {};
  const e = PORTADAS[reticulaDe(b.tipoEntregable).nombre] || PORTADAS.otro;
  const { titulo, subtitulo } = textosDePortada(b);
  const quien = unaLinea(b.publico, 100), angulo = unaLinea(b.angulo, 150);
  const c1 = colorSeguro(colorPrimario, '#e91e63'), c2 = colorSeguro(colorSecundario, '#2d7a3e');
  return 'Design a complete, finished ebook cover, vertical 3:4, ready to sell, with the lettering integrated into the artwork like a bestselling Latin American digital product cover (never a plain photo with small text on it).\n' +
    'TEXT ON THE COVER. Copy it exactly, letter by letter, in Spanish, with the same spelling, accents, numbers and punctuation. Do not translate, rephrase, add or remove any word:\n' +
    'Main title, the largest text, 2 to 6 lines, ' + e.letra + ', white or very light with a strong soft shadow or outline so it is perfectly readable, with one or two key words highlighted in the accent color: "' + titulo + '"\n' +
    (subtitulo ? 'Subtitle, much smaller, 1 to 3 lines, on a dark semi-transparent ribbon or panel so it stays readable: "' + subtitulo + '"\n' : '') +
    'No other text anywhere: no author name, no website, no logo, no watermark, no extra badges, no stray numbers or letters.\n' +
    'Scene behind the lettering: ' + e.escena + '. Make it specific to this offer' + (angulo ? ' (' + angulo + ')' : '') + (quien ? ', made for ' + quien : '') + '. Rich depth, vibrant saturated colors, sharp focus, premium art direction. Keep the main subject clearly visible and leave comfortable room for the title and the subtitle.\n' +
    'Accent colors for highlights, ribbons and ornaments: ' + c1 + ' and ' + c2 + '. No real people or celebrity likenesses.' +
    (estiloFoto ? ' Photographic mood to match the interior pages: ' + unaLinea(estiloFoto, 300) + '.' : '');
}
export function esperadoPortada(brief) {
  const { titulo, subtitulo } = textosDePortada(brief);
  // El titulo Y el subtitulo son estrictos: una palabra cambiada o cortada en cualquiera de los dos es un defecto visible en lo primero que ve el cliente.
  return { estrictas: subtitulo ? [titulo, subtitulo] : [titulo], cuerpo: [] };
}

// La hoja de una pagina que ES una imagen entera: el HTML solo pone la imagen (el CSS es el mismo de todas las hojas y vive en functions/_ebook-maqueta.js, regla .mq-ia).
export const htmlPaginaIA = () => '<style>' + CSS_MAQUETA + '</style><div class="mq-hoja mq-ia"><img src="__FOTO__" alt=""></div>';
