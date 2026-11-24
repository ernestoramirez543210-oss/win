// REVISION DE LOS PROMPTS DEL EDITOR ANTES DE COBRAR (2026-11-10).
//
// antes de pedir (editor-anuncios/scripts/revisar_prompts.py), pero el instalador conserva los archivos que alguien haya editado a mano y un PC puede tener un apimart.py viejo:
// por eso el dash repite AQUI las mismas reglas antes de cobrar una imagen o un clip (functions/_editor-apimart.js las llama en prepararImagen y prepararVideo). Lo que no cumple
// se niega con 400 y la lista de lo que falta; no se escribe nada ni se llama a APIMart.
//
// Las dos copias tienen las mismas listas de palabras y los mismos conteos, y se prueban con los MISMOS casos: editor-anuncios/scripts/casos_revisar_prompts.json
// (scripts/probar-revisar-prompts.mjs aqui, autoprueba_revisar_prompts.py en el editor). Si cambias una regla, cambiala en las dos y agrega su caso.
// Lo unico que el dash no puede mirar: si la referencia es de verdad una foto del producto (aqui solo llegan direcciones https); eso lo mira el editor con las rutas.
// Este modulo es puro: no importa nada, no toca KV ni la red (lo vigila scripts/Auditar-Seguridad.ps1, 3d-quinquies).
//
// Reglas (de editor-anuncios/.claude/skills/editor-video/references/prompts-ia.md y preferencias.md):
//   imagen: lugar, angulo, encuadre, accion en curso con objetos concretos y expresion (sin cara: «Nobody's face is visible», sin la expresion; sin nadie: «No people in the shot»,
//           solo lugar, angulo y encuadre); «No text…»; con el producto, alguna referencia; si nombra la foto de referencia, que vaya.
//   clip con dialogo: de 25 a 30 palabras por cada 8 s (de 12 a 15 por toma en la de dos tomas), fin de frase, sin cifras ni simbolos, los bloques fijos, en español; VOICE y ACCENT
//           identicos en todo el anuncio (eso lo compara _editor-apimart.js con la ficha del anuncio, con la huella de `voz`).
//   clip sin dialogo: CAMERA, MICRO-ACTION, STABLE OBJECT CONSISTENCY, RESTRICTIONS y el silencio.
//   todos: en ingles; sin cinematic, 4K, studio lighting, bokeh ni parecidas; sin pedir texto legible ni comillas fuera del dialogo; sin manos escribiendo; «no captions» o «No text».

export const REGLAS_VERSION = 1;
// El prompt mas largo que se revisa (y que el dash acepta para una imagen o un clip: LIMITES.prompt de _editor-apimart.js). Las plantillas de prompts-ia.md llegan a ~1.500 letras;
// el tope deja aire y acota el tiempo de CPU de la revision en el plan gratis de Cloudflare (~10 ms por pedido).
export const MAX_LETRAS_PROMPT = 4000;
const PLANTILLAS = '.claude/skills/editor-video/references/prompts-ia.md';

const PALABRAS_IA = /\b(cinemat\w*|4k|8k|uhd|studio[- ]light\w*|bokeh|shallow depth of field|hdr|dslr|film grain|photoshoot|professional photo\w*)\b/i;
const NEGACION = /\b(no|not|without|never|zero|avoid|nothing|none|nor|don't|doesn't|don’t|doesn’t)\b/i;
const CORTES = ',.;:!?\n()[]';
const VENTANA_CLAUSULA = 160;
const LUGAR = new RegExp('\\b(kitchen|living[- ]room|bedroom|dining[- ]room|dining table|table|tabletop|countertop|counter|desk|workbench|sofa|couch|armchair|bed|garden|backyard|yard|' +
  'patio|balcony|porch|terrace|rooftop|workshop|office|bathroom|hallway|corridor|doorway|entrance|windowsill|laundry|garage|stove|sink|fridge|' +
  'refrigerator|oven|pantry|market|store|shop|street|park|car|classroom|school|church|cafe|restaurant|bakery|salon|gym|floor|rug|carpet|shelf|bookshelf|' +
  'nursery|crib|playroom|stairs|staircase|farm|field|beach|apartment|house|room)\\b', 'i');
// «Place: algo de dos palabras o mas». La primera palabra va acotada a 60 letras: sin eso, «place:place:place:…» sin espacios crece al cuadrado.
const LUGAR_ETIQUETA = /\bplace\s*:\s*[^\s.]{1,60}[ \t]+[^\s.]/i;
const ANGULO = new RegExp("\\b(three[- ]quarters?|3/4|side (view|angle|profile)|from the side|in profile|profile view|low[- ]angle|high[- ]angle|eye[- ]level|overhead|top[- ]down|" +
  "from above|from below|top view|flat[- ]lay|aerial view|bird'?s[- ]eye|over[- ]the[- ]shoulder|front view|frontal|straight[- ]on|head[- ]on|from behind|rear view|dutch angle|angle|angled|" +
  'point of view|pov|first[- ]person)\\b', 'i');
const ENCUADRE = new RegExp('\\b(extreme close[- ]?up|medium close[- ]?up|close[- ]?up|closeup|close shot|medium shot|mid[- ]shot|medium[- ]wide( shot)?|wide shot|full shot|full[- ]body|' +
  'long shot|waist[- ]up|chest[- ]up|shoulders[- ]up|head[- ]and[- ]shoulders|knees?[- ]up|half[- ]body|tight shot|macro( shot)?|detail shot|insert shot|' +
  'establishing shot|two[- ]shot|framed from (the )?(waist|chest|knees|shoulders)|from the (waist|chest|knees) up)\\b', 'i');
const EXPRESION = new RegExp('\\b(smil\\w*|grin|grins|grinning|laugh\\w*|chuckl\\w*|giggl\\w*|focused|concentrat\\w*|surprised|amazed|astonished|curious|excited|proud|relaxed|calm|serious|' +
  'frown\\w*|worried|tired|exhausted|frustrated|annoyed|happy|joyful|cheerful|amused|confident|thoughtful|satisfied|relieved|delighted|pleased|determined|' +
  'skeptical|sceptical|shy|embarrassed|nervous|hopeful|tender|playful|enthusiastic|expression|eyebrows?|wink\\w*|smirk\\w*|sad|upset|stressed|overwhelmed)\\b', 'i');
const SIN_PERSONAS = /\b(no (people|person|humans?|one)|nobody (is )?in (the )?(shot|frame|scene|picture)|without (people|anyone|any person|a person|humans))\b/i;
const SIN_CARA = new RegExp("(\\bnobody['\\u2019]?s face is visible|\\bno faces?\\b|\\bfaces? (is |are )?not visible|\\bface (is )?out of (the )?frame|\\bhands only\\b|" +
  '\\bonly (her|his|their|the) hands\\b|\\bjust (her|his|their|the) hands\\b|\\bwithout (showing )?(her|his|their|a|any) face\\b)', 'i');
const PRODUCTO = /(\bproduct\s*:|\b(e-?books?|the product|our product|the guide|the booklet|the pdf|book cover|the cover|its cover|printed pages)\b)/i;
const REFERENCIA = /\b(reference (photos?|images?|pictures?|cover)|(first|second|third|fourth) reference)\b/i;
const NO_TEXT_IMAGEN = /\bno (readable )?(text|words|lettering)\b/i;
const NO_TEXT_CLIP = /\bno (captions|subtitles|on-screen text|text)\b/i;
const PIDE_TEXTO = new RegExp('\\b(text (that )?(says|reads|saying|reading)|(that|which) (says|reads)|with the (words?|text|phrase|title|name)|the words|lettering|captions?|subtitles?|' +
  'headline|title text|on-screen text|text overlay|speech bubble|signs? (that|reading|with)|labels? (that|reading|with)|written|handwritten|handwriting|' +
  'typography|fonts?|spell(s|ing)? out|brand names?|logos?|watermarks?|price tags?|readable)\\b', 'i');
// «writing desk(s)», «writing-desk», «writing bureau(s)» o «writing table» son un mueble, no manos escribiendo (y en COMPUESTOS no cuentan como accion); «writing tables of …» si es escribir. Los espacios van
// explicitos (espacio, tabulador, salto de linea, espacio duro y guion): \s no cubre lo mismo en Python y en el dash (U+FEFF, U+0085...)
const ESCRIBE = /\b(writ(e|es|ing)(?![ \t\n\u00a0-]+(?:desks?|bureaus?|table)\b)|handwrit\w*|typ(ing|es on)|texting|scribbl\w*|calligraph\w*|signing|draw(s|ing)? (letters|words|a letter|the letters))\b/i;
const SILENCIO = /(absolute silence|zero speech|no speech|no dialogue|no talking|nobody speaks|no one speaks|lips stay closed|mouths? stays? closed|mouth never moves)/i;
const COMPUESTOS = new RegExp('\\b(cutting board|chopping board|baking (sheet|tray|dish|paper|pan)|frying pan|mixing bowl|measuring (cups?|spoons?|tape)|rolling pin|' +
  'sewing (machine|kit|box|room)|ironing board|dining (table|room|chair)|living room|washing machine|serving (bowl|plate|tray|spoon)|knitting needles?|' +
  'wrapping paper|cooling rack|drawing (pad|book)|painting (brush|set)|ceiling (fan|light)|reading glasses|craft room|writing[ \\t\\n\\u00a0-]+(desks?|bureaus?|table))\\b', 'i');
const ETIQUETA = /(UGC LOOK|STABLE OBJECT CONSISTENCY|VOICE|ACCENT LOCK|ACCENT|PACE|RESTRICTIONS|CAMERA|MICRO-ACTION)\s*:/;
const SHOT_1 = /\bSHOT 1\b/;
const SHOT_2 = /\bSHOT 2\b/;
// Las comillas dobles del dialogo y su cierre (se buscan en una pasada en dialogos(): un regex con miles de “ sin cerrar crecia al cuadrado).
const PARES_COMILLAS = new Map([['"', '"'], ['“', '”'], ['«', '»']]);
const SIMPLES = /(?:^|[^A-Za-z0-9])['‘]([^'’\n]{1,120})['’](?![A-Za-z0-9])/;
const PALABRA = /\p{L}+(?:['’-]\p{L}+)*/gu;   // global: String.match. Solo letras (categoria L): ⅓, ², ⑩ o Ⅻ no son palabra (en el editor tampoco).
// Cifra en el dialogo: todo caracter numerico (categoria Unicode N: 0-9, ½, ⅓, ², ⑩, Ⅻ, １…) y estos simbolos. Global: String.match.
const CIFRAS = /[\p{N}%$#@&+=€£/\\_*]/gu;
const TOKEN = /[a-z]+(?:'[a-z]+)?/g;           // global a proposito: se usa con String.match (que vuelve a empezar cada vez)
const IDIOMA = /[a-záéíóúüñ]+/;
const SPANISH = /\bSpanish\b/;
const ANTES_ETIQUETA = /[A-Za-z-]/;

const conjunto = (s) => new Set(s.split(/\s+/).filter(Boolean));
const INGLES = conjunto('the and with her his their of on in at is are she he while from into to it its this that holding same as');
const ESPANOL = conjunto('el la los las de del que con una un para por en su sus está esta mientras sobre y se al lo como muy pero mujer hombre cocina mesa manos');
const NO_ACCION = conjunto('doing posing wearing framing lighting something nothing anything everything morning evening ceiling clothing building during according standing sitting ' +
  'lying leaning kneeling smiling looking facing being having feeling waiting thinking talking speaking saying glancing blinking breathing shining bring string spring thing king ' +
  'sing wing swing sling sting icing stuffing dressing frosting topping seasoning amazing interesting charming stunning relaxing loving caring living dining pudding wedding railing ' +
  'flooring padding bedding packaging matching missing going coming listening watching');
const NO_ACCION_3P = conjunto('wears looks smiles stands sits has is was does seems appears faces poses keeps gets says talks speaks lies leans waits thinks feels');
const SUJETOS = conjunto('she he they hands hand fingers woman man person mom mother');
const FUNCION = conjunto('the a an and or but with her his their its our your my me him them of on in into onto at to from for by over under up down out off around through across ' +
  'along about above below behind beside next near between while as then than very so too just still also again some any more most much many this that these those it there here now ' +
  'away together both each every one two three four five few several little small big large new fresh old same other another own such what which who whom whose where when how all is ' +
  'are was be been being has have had do does did will would can could should may might must not no slowly carefully gently quickly happily calmly softly lightly slightly firmly ' +
  'proudly neatly closely briefly really nearly barely simply warmly naturally casually right left');
const VAGO = conjunto('something anything everything nothing stuff things thing items item objects object whatever someone somebody');
const NEGACION_TOKENS = conjunto("no not without never zero avoid nothing none nor don't doesn't");
// Palabras que solo son un objeto despues de her/his/their: «stretching her back» es una accion; «stepping back» no tiene objeto.
const SOLO_TRAS_POSESIVO = conjunto('back');
const POSESIVOS = conjunto('her his their');
const BLOQUES_DIALOGO = ['UGC LOOK', 'STABLE OBJECT CONSISTENCY', 'VOICE', 'ACCENT', 'PACE', 'RESTRICTIONS'];
const BLOQUES_SILENCIO = ['CAMERA', 'MICRO-ACTION', 'STABLE OBJECT CONSISTENCY', 'RESTRICTIONS'];
const BLOQUES_VOZ = ['VOICE', 'ACCENT', 'ACCENT LOCK'];

// ---------------------------------------------------------------- utilidades (las mismas que en revisar_prompts.py)
const global = (re) => new RegExp(re.source, re.flags.indexOf('g') === -1 ? re.flags + 'g' : re.flags);
const todas = (re, t) => Array.from(t.matchAll(global(re)));
const nfc = (t) => String(t == null ? '' : t).normalize('NFC').replace(/\r\n?/g, '\n');
const junta = (t) => String(t).split(/\s+/).filter(Boolean).join(' ');
const palabras = (t) => (nfc(t).match(PALABRA) || []).length;

// inicio[i] = donde empieza la clausula que contiene la posicion i (justo despues de la ultima coma, punto, dos puntos, salto o parentesis antes de i). Una pasada.
function iniciosDeClausula(texto) {
  const inicio = new Int32Array(texto.length + 1);
  let desde = 0;
  for (let i = 0; i < texto.length; i++) {
    inicio[i] = desde;
    if (CORTES.indexOf(texto.charAt(i)) !== -1) desde = i + 1;
  }
  inicio[texto.length] = desde;
  return inicio;
}
// La negacion se busca en lo que va de la clausula, hasta VENTANA_CLAUSULA letras atras: un prompt enorme sin comas no cuesta tiempo cuadratico (~10 ms de CPU por pedido).
function sinNegar(re, texto) {
  const inicios = iniciosDeClausula(texto);
  return todas(re, texto).filter((m) => !NEGACION.test(texto.slice(Math.max(inicios[m.index], m.index - VENTANA_CLAUSULA), m.index))).map((m) => m[0]);
}
function unicos(xs) {
  const vistos = [];
  for (const x of xs) { const y = junta(x).toLowerCase(); if (vistos.indexOf(y) === -1) vistos.push(y); }
  return vistos.slice(0, 3).map((x) => '«' + x + '»').join(', ');
}

export function dialogos(texto) {
  const t = nfc(texto);
  const cuenta = (c) => t.split(c).length - 1;
  const cerradas = cuenta('"') % 2 === 0 && cuenta('“') === cuenta('”') && cuenta('«') === cuenta('»');
  return { segs: entreComillas(t), cerradas };
}
// [{a, b, x}] de lo que va entre comillas dobles ("…", “…” o «…»), de izquierda a derecha y sin anidar: lo mismo que el regex /"([^"]*)"|“([^”]*)”|«([^»]*)»/g, pero lineal
// (si una comilla de un tipo no tiene cierre, ninguna de ese tipo mas adelante lo tiene: se deja de buscar ese tipo).
function entreComillas(t) {
  const segs = [];
  const sinCierre = new Set();
  let i = 0;
  while (i < t.length) {
    const c = t.charAt(i);
    const cierre = PARES_COMILLAS.get(c);
    if (cierre !== undefined && !sinCierre.has(c)) {
      const j = t.indexOf(cierre, i + 1);
      if (j !== -1) {
        segs.push({ a: i, b: j + 1, x: t.slice(i + 1, j) });
        i = j + 1;
        continue;
      }
      sinCierre.add(c);
    }
    i++;
  }
  return segs;
}
// El prompt con lo que va entre comillas dobles cambiado por espacios (mismas posiciones). t ya en NFC; segs de dialogos(t). Una pasada.
function sinDialogo(t, segs) {
  if (!segs.length) return t;
  const partes = [];
  let p = 0;
  for (const s of segs) { partes.push(t.slice(p, s.a), ' '.repeat(s.b - s.a)); p = s.b; }
  partes.push(t.slice(p));
  return partes.join('');
}
export function bloques(texto) {
  const t0 = nfc(texto);
  return bloquesDe(sinDialogo(t0, dialogos(t0).segs));
}
// Los bloques con etiqueta (VOICE:, ACCENT:, RESTRICTIONS:…) y su contenido, de un texto ya sin dialogo. Si una etiqueta se repite, vale la primera.
function bloquesDe(t) {
  const marcas = todas(ETIQUETA, t).filter((m) => m.index === 0 || !ANTES_ETIQUETA.test(t.charAt(m.index - 1)));
  const salida = new Map();
  marcas.forEach((m, i) => {
    const fin = i + 1 < marcas.length ? marcas[i + 1].index : t.length;
    if (!salida.has(m[1])) salida.set(m[1], junta(t.slice(m.index + m[0].length, fin)));
  });
  return salida;
}
// La voz de un clip con dialogo como UN texto (VOICE, ACCENT y ACCENT LOCK, los que tenga): _editor-apimart.js guarda su huella en la ficha del anuncio y la compara entre clips.
export function vozDe(texto) { return vozDeBloques(bloques(texto)); }
function vozDeBloques(b) {
  return BLOQUES_VOZ.filter((k) => b.has(k)).map((k) => k + '=' + b.get(k)).join('\n');
}
export function rangoPalabras(duracion, minimo, maximo) {
  const d = Number(duracion);
  return [Math.ceil((minimo == null ? 25 : minimo) * d / 8 - 1e-9), Math.floor((maximo == null ? 30 : maximo) * d / 8 + 1e-9)];
}

function esObjeto(tokens, j) {
  const x = tokens[j];
  return x.length >= 3 && !FUNCION.has(x) && !VAGO.has(x) && (!SOLO_TRAS_POSESIVO.has(x) || POSESIVOS.has(tokens[j - 1]));
}
function hayAccion(texto) {
  const t = texto.toLowerCase().replace(global(COMPUESTOS), (m) => m.split(/[ \t\n\u00a0-]+/).pop());
  for (const cl of t.split(/[,.;:!?\n()[\]]/)) {
    const tokens = cl.match(TOKEN) || [];
    let negada = false;                    // ya aparecio un «no», «not», «without»… antes en la clausula
    for (let i = 0; i < tokens.length; i++) {
      const tk = tokens[i];
      const esVerbo = (tk.length >= 5 && tk.endsWith('ing') && !NO_ACCION.has(tk)) ||
        (i > 0 && SUJETOS.has(tokens[i - 1]) && tk.length >= 4 && tk.endsWith('s') && !NO_ACCION_3P.has(tk) && !FUNCION.has(tk));
      if (esVerbo && !negada) {
        for (let j = i + 1; j < Math.min(i + 9, tokens.length); j++) if (esObjeto(tokens, j)) return true;
      }
      negada = negada || NEGACION_TOKENS.has(tk);
    }
  }
  return false;
}

function comunes(fuera, cuerpo, problemas) {
  const tokens = todas(IDIOMA, fuera.toLowerCase()).map((m) => m[0]);
  const es = tokens.filter((x) => ESPANOL.has(x)).length;
  const en = tokens.filter((x) => INGLES.has(x)).length;
  if (es >= 6 && es > en) problemas.push({ codigo: 'idioma', mensaje: 'parece escrito en español: el prompt va en INGLÉS (solo el diálogo, entre comillas, va en español)' });
  const ia = todas(PALABRAS_IA, cuerpo).map((m) => m[0]);
  if (ia.length) problemas.push({ codigo: 'palabras_ia', mensaje: 'usa palabras que delatan la IA (' + unicos(ia) + '): quítalas; el aspecto de video casero grabado con celular ya va en la plantilla' });
  const simples = todas(SIMPLES, fuera).map((m) => m[1]);
  if (simples.length) problemas.push({ codigo: 'comillas', mensaje: 'tiene palabras entre comillas fuera del diálogo (' + unicos(simples) + '): el modelo las dibuja como subtítulos; quita las comillas' });
  const pide = sinNegar(PIDE_TEXTO, fuera);
  if (pide.length) problemas.push({ codigo: 'texto', mensaje: 'pide texto legible (' + unicos(pide) + '): la IA dibuja mal las letras y se ve falso; nada de letreros, etiquetas, títulos, logos ni palabras escritas' });
  const escribe = sinNegar(ESCRIBE, fuera);
  if (escribe.length) problemas.push({ codigo: 'escribir', mensaje: 'pide manos escribiendo (' + unicos(escribe) + '): la IA deforma las manos que escriben; deja la mano quieta (por ejemplo «holding a pen still above the open notebook»)' });
}

const largo = (cuerpo) => ({ codigo: 'largo', mensaje: 'el prompt tiene ' + cuerpo.length + ' letras y el máximo es ' + MAX_LETRAS_PROMPT + ' (las plantillas llegan a unas 1.500): acórtalo' });

// ---------------------------------------------------------------- imagen
// [{codigo, mensaje}] del prompt de un cuadro; [] = cumple. nRefs = cuantas referencias van.
export function revisarImagen(prompt, nRefs) {
  const cuerpo = nfc(prompt);
  if (!cuerpo.trim()) return [{ codigo: 'vacio', mensaje: 'el prompt esta vacio' }];
  if (cuerpo.length > MAX_LETRAS_PROMPT) return [largo(cuerpo)];
  const problemas = [];
  const d = dialogos(cuerpo);
  const fuera = sinDialogo(cuerpo, d.segs);
  if (d.segs.length || !d.cerradas) problemas.push({ codigo: 'comillas', mensaje: 'tiene texto entre comillas: en una imagen el modelo lo dibuja como letras; describe la escena sin comillas' });
  comunes(fuera, cuerpo, problemas);
  if (!NO_TEXT_IMAGEN.test(fuera)) problemas.push({ codigo: 'sin_no_text', mensaje: 'le falta la frase de la plantilla «No text, no readable brand names, no logos, no watermark.»' });
  const sinPersonas = SIN_PERSONAS.test(fuera);
  const sinCara = sinPersonas || SIN_CARA.test(fuera);
  if (!(LUGAR_ETIQUETA.test(fuera) || sinNegar(LUGAR, fuera).length)) problemas.push({ codigo: 'lugar', mensaje: 'no dice el LUGAR (dónde es: «in her small kitchen, next to the stove», o «Place: …» con objetos concretos)' });
  if (!sinNegar(ANGULO, fuera).length) problemas.push({ codigo: 'angulo', mensaje: 'no dice el ÁNGULO de cámara («three-quarter angle», «side view», «low angle», «from above», «eye level»)' });
  if (!sinNegar(ENCUADRE, fuera).length) problemas.push({ codigo: 'encuadre', mensaje: 'no dice el ENCUADRE («medium shot», «waist up», «close-up», «full body»)' });
  if (!sinPersonas && !hayAccion(fuera)) problemas.push({ codigo: 'accion', mensaje: 'no dice la ACCIÓN en curso con objetos concretos (qué está haciendo y con qué: «pouring hot chocolate into a red mug», «gluing felt pieces onto a paper template»); nunca posando' });
  if (!sinCara && !sinNegar(EXPRESION, fuera).length) problemas.push({ codigo: 'expresion', mensaje: 'no dice la EXPRESIÓN de la cara («smiling at the camera», «focused», «surprised»). Si no sale ninguna cara, escribe «Nobody\'s face is visible»; si no sale nadie, «No people in the shot»' });
  const n = Number.isInteger(nRefs) && nRefs > 0 ? nRefs : 0;
  let pide = 0;
  for (const m of todas(REFERENCIA, fuera)) {
    const f = m[0].toLowerCase();
    const k = f.indexOf('fourth') !== -1 ? 4 : f.indexOf('third') !== -1 ? 3 : (f.indexOf('second') !== -1 || f.indexOf('photos') !== -1 || f.indexOf('images') !== -1 || f.indexOf('pictures') !== -1) ? 2 : 1;
    pide = Math.max(pide, k);
  }
  if (pide > n) problemas.push({ codigo: 'referencias', mensaje: 'nombra ' + pide + ' foto(s) de referencia y van ' + n + ': pasa cada foto con --ref, en el orden de la plantilla (1.ª el avatar, 2.ª el cuadro ancla, después las del producto)' });
  if (sinNegar(PRODUCTO, fuera).length && n === 0) problemas.push({ codigo: 'producto_sin_ref', mensaje: 'muestra el producto pero no va ninguna foto de referencia: nunca se genera solo con texto (pasa con --ref la portada o las páginas que producto.json marca como fotos del producto)' });
  return problemas;
}

// ---------------------------------------------------------------- clip
// { problemas: [{codigo, mensaje}], voz: '' (sin dialogo) o el texto de VOICE/ACCENT/ACCENT LOCK, para comparar entre los clips del anuncio }.
export function revisarVideo(prompt, duracion) {
  const cuerpo = nfc(prompt);
  if (!cuerpo.trim()) return { problemas: [{ codigo: 'vacio', mensaje: 'el prompt esta vacio' }], voz: '' };
  if (cuerpo.length > MAX_LETRAS_PROMPT) return { problemas: [largo(cuerpo)], voz: '' };
  const dur = Number(duracion) > 0 ? Number(duracion) : 8;
  const problemas = [];
  const d = dialogos(cuerpo);
  const fuera = sinDialogo(cuerpo, d.segs);
  if (!d.cerradas) problemas.push({ codigo: 'comillas', mensaje: 'tiene comillas sin cerrar: el diálogo va entero entre comillas dobles y no hay más comillas en el prompt' });
  comunes(fuera, cuerpo, problemas);
  if (!NO_TEXT_CLIP.test(fuera)) problemas.push({ codigo: 'sin_no_captions', mensaje: 'le falta «no captions or on-screen text» en RESTRICTIONS (sin eso el modelo puede poner subtítulos)' });
  const b = bloquesDe(fuera);
  let voz = '';
  if (d.segs.length) {
    const dosTomas = SHOT_1.test(fuera) && SHOT_2.test(fuera);
    const esperados = dosTomas ? 2 : 1;
    if (d.segs.length !== esperados) problemas.push({ codigo: 'comillas', mensaje: 'tiene ' + d.segs.length + ' textos entre comillas y la plantilla lleva ' + esperados + ' (el diálogo' + (dosTomas ? ', uno por toma' : '') + '): el modelo dibuja como subtítulo lo que va entre comillas fuera del diálogo' });
    const textos = d.segs.map((s) => s.x);
    const total = textos.reduce((s, x) => s + palabras(x), 0);
    const [lo, hi] = rangoPalabras(dur, 25, 30);
    if (dosTomas) {
      const [loT, hiT] = rangoPalabras(dur, 12, 15);
      textos.slice(0, 2).forEach((x, i) => {
        const n = palabras(x);
        if (n < loT || n > hiT) problemas.push({ codigo: 'dialogo_largo', mensaje: 'la toma ' + (i + 1) + ' dice ' + n + ' palabras y cada toma de un clip de ' + dur + ' s lleva de ' + loT + ' a ' + hiT });
      });
    }
    if (total < lo || total > hi) problemas.push({ codigo: 'dialogo_largo', mensaje: 'el diálogo tiene ' + total + ' palabras y un clip de ' + dur + ' s lleva de ' + lo + ' a ' + hi + ' (con menos sobra tiempo y el modelo inventa o se queda callado; con más corta la frase)' });
    const ultimo = textos[textos.length - 1];
    const final = ultimo.trim().replace(/['’»"” ]+$/, '').replace(/\s+$/, '');
    if (!final || '.!?…'.indexOf(final.charAt(final.length - 1)) === -1) {
      problemas.push({ codigo: 'dialogo_fin', mensaje: 'el diálogo no termina en fin de frase («…' + ultimo.split(/\s+/).filter(Boolean).slice(-4).join(' ') + '»): cada clip termina con punto, ? o ! y nunca corta una frase a la mitad' });
    }
    // En orden de punto de codigo, como sorted() del editor (sort() a secas ordena por unidades UTF-16 y 𝟏 quedaria antes que １).
    const cifras = Array.from(new Set(textos.flatMap((x) => x.match(CIFRAS) || []))).sort((x, y) => x.codePointAt(0) - y.codePointAt(0));
    if (cifras.length) problemas.push({ codigo: 'dialogo_cifras', mensaje: 'el diálogo tiene cifras o símbolos (' + cifras.join(' ') + '): escríbelos en letras («cincuenta por ciento», «veinte mil pesos»)' });
    const faltan = BLOQUES_DIALOGO.filter((k) => !b.get(k));
    if (faltan.length) problemas.push({ codigo: 'bloques', mensaje: 'le faltan los bloques fijos ' + faltan.join(', ') + ' (BLOQUES FIJOS de ' + PLANTILLAS + ': van al final de todo clip con diálogo)' });
    for (const k of ['VOICE', 'ACCENT']) {
      if (b.get(k) && palabras(b.get(k)) < 3) problemas.push({ codigo: 'bloques', mensaje: 'el bloque ' + k + ' está casi vacío: describe la voz y el acento del avatar (sale de avatar.json y va igual en todos los clips)' });
    }
    if (!SPANISH.test(fuera)) problemas.push({ codigo: 'bloques', mensaje: 'no dice que el diálogo es en español (la plantilla: «(Spanish, lip-synced)» y «lip-synced dialogue only in Spanish»)' });
    voz = vozDeBloques(b);
  } else {
    const faltan = BLOQUES_SILENCIO.filter((k) => !b.get(k));
    if (faltan.length) problemas.push({ codigo: 'bloques', mensaje: 'un clip sin diálogo sigue la PLANTILLA 5 de ' + PLANTILLAS + ': le faltan ' + faltan.join(', ') });
    if (!SILENCIO.test(fuera)) problemas.push({ codigo: 'silencio', mensaje: 'un clip sin diálogo dice que va en silencio («RESTRICTIONS: absolute silence for the entire clip: zero speech, zero voice-over…»); si lleva diálogo, va entre comillas dobles' });
    if (b.has('MICRO-ACTION') && palabras(b.get('MICRO-ACTION')) < 3) problemas.push({ codigo: 'bloques', mensaje: 'MICRO-ACTION está casi vacío: una sola acción lenta y concreta («slowly pours the warm milk into the mug»)' });
  }
  return { problemas, voz };
}

// El texto del 400: que falta, una linea por problema.
export function mensajeRevision(problemas, que) {
  return (que || 'El prompt') + ' no cumple la plantilla de ' + PLANTILLAS + ' (revisión automática, antes de pagar; no se gastó nada):\n' + problemas.map((p) => '  - ' + p.mensaje).join('\n');
}

// Calentamiento: V8 prepara cada expresion la primera vez que la usa (~10 ms en total). Se hace aqui, al cargar el modulo (Cloudflare lo carga al arrancar, con su propio limite de
// tiempo), y no en el primer pedido, que tiene ~10 ms de CPU y despues de la revision todavia habla con KV y APIMart. Si algo fallara aqui no importa: es solo para calentar.
try {
  revisarImagen('A woman in her small kitchen, next to the sink. Place: wooden kitchen table with a red mug. Three-quarter angle, medium shot, waist up: she is pouring hot ' +
    'chocolate into the red mug, smiling at the camera. The product: the booklet on the table, as in the reference photos. No text, no readable brand names, no logos, no watermark.', 2);
  revisarVideo('(00:00-00:01) She lifts the red mug. She looks into the lens and says: "Hola amiga, te cuento algo rapido sobre esta receta casera que me salvo las tardes de ' +
    'frio, mira como queda de cremosa y facil." (Spanish, lip-synced).\nUGC LOOK: real home video.\nSTABLE OBJECT CONSISTENCY: the mug keeps its shape.\nVOICE: warm adult ' +
    'female voice.\nACCENT: neutral Colombian Spanish accent.\nPACE: natural pace.\nRESTRICTIONS: no captions or on-screen text, no music.', 8);
  revisarVideo('CAMERA: slow push-in, medium close-up from a three-quarter angle.\nMICRO-ACTION: she slowly pours the warm milk into the mug.\nSTABLE OBJECT CONSISTENCY: the ' +
    'mug keeps its shape.\nRESTRICTIONS: absolute silence for the entire clip: zero speech, nobody speaks. No captions or on-screen text.', 8);
} catch (e) { /* solo calienta */ }
