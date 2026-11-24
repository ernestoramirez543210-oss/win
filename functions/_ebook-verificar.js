// Verificar el texto de una imagen dibujada por la IA (functions/_ebook-pagina-ia.js).
//
// Cuando el modelo de imagenes dibuja la pagina con el texto adentro, el texto puede salir distinto del pedido: en una prueba escribio «INGREDIENTS» donde se le pidio
// «INGREDIENTES». Aqui se le pide a un modelo de texto con vision (el mismo que escribe el contenido) que LEA la imagen, y lo leido se compara con lo pedido:
//   - estrictas: el titulo y las etiquetas de seccion (INGREDIENTES, TIP, ERROR COMUN...). Si falta UNA sola de sus palabras, la pagina se da por mala y el cliente la repite.
//   - cuerpo:    el resto del texto. Se toleran un par de palabras mal leidas (un OCR de una pagina entera no es perfecto): solo se avisa si faltan muchas.
// La comparacion ignora mayusculas, tildes y signos: lo que se busca son palabras cambiadas, no una tilde.
//
// Lo unico que se manda al modelo es el ENLACE publico de la imagen que acaba de generar el proveedor (no la imagen en base64: pesa 2-3 MB y el servidor tiene muy poco CPU por
// peticion). Si no hay enlace, o el modelo no lee imagenes por enlace, no se verifica (resultado null): verificar nunca puede romper ni retrasar una pagina.
import { jsonDeTexto } from './_llm.js';

// Las marcas de acento (U+0300 a U+036F) se arman con sus codigos: escritas tal cual en el archivo no se verian.
const MARCAS = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');
const FRACCIONES = { '½': ' 1 2 ', '¼': ' 1 4 ', '¾': ' 3 4 ' };

// El texto convertido en palabras comparables: minusculas, sin tildes, solo letras y numeros.
export function palabrasDe(texto) {
  let t = String(texto == null ? '' : texto).toLowerCase();
  t = t.replace(/[½¼¾]/g, (c) => FRACCIONES[c]);
  t = t.normalize('NFD').replace(MARCAS, '').replace(/[^a-z0-9]+/g, ' ').trim();
  return t ? t.split(' ') : [];
}
// Una palabra cuenta si tiene 3 letras o mas, o es un numero: «de», «y», «la» se pierden en una lectura sin que importe.
const cuenta = (w) => w.length >= 3 || /^\d+$/.test(w);
const unicas = (textos) => Array.from(new Set([].concat.apply([], textos.map(palabrasDe)).filter(cuenta)));

// Distancia de edicion entre dos palabras (para decir «escribio X donde se pidio Y»); solo se usa con palabras cortas.
function distancia(a, b) {
  if (Math.abs(a.length - b.length) > 2) return 9;
  const d = [];
  for (let i = 0; i <= a.length; i++) { d[i] = [i]; }
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1));
  return d[a.length][b.length];
}

// esperado: { estrictas: [texto...], cuerpo: [texto...], tolerancia?: n }   leido: el texto que se leyo de la imagen (un texto cualquiera).
// Devuelve { ok, estricta, faltan: [palabras], cambiadas: [{ pedida, vista }], leidas } o null si lo leido es tan poco que no sirve para comparar.
export function comparar(esperado, leido) {
  const vistas = new Set(palabrasDe(leido));
  if (vistas.size < 3) return null;
  const estrictas = unicas(esperado.estrictas || []);
  const cuerpo = unicas(esperado.cuerpo || []).filter((w) => estrictas.indexOf(w) === -1);
  const faltanE = estrictas.filter((w) => !vistas.has(w));
  const faltanC = cuerpo.filter((w) => !vistas.has(w));
  const tolerancia = Math.max(esperado.tolerancia != null ? esperado.tolerancia : 2, Math.floor(0.04 * cuerpo.length));
  const conocidas = new Set(estrictas.concat(cuerpo));
  // lo que se leyo y no se esperaba pero se parece a una palabra que falta: el modelo la escribio mal (o en ingles), no es texto de la foto
  const raras = Array.from(vistas).filter((w) => !conocidas.has(w) && w.length >= 4);
  const faltan = faltanE.concat(faltanC);
  const cambiadas = [];
  faltan.forEach((f) => { const v = raras.find((w) => distancia(w, f) <= 2); if (v) cambiadas.push({ pedida: f, vista: v }); });
  return { ok: faltanE.length === 0 && faltanC.length <= tolerancia, estricta: faltanE.length > 0, faltan: faltan.slice(0, 12), cambiadas: cambiadas.slice(0, 8), leidas: vistas.size };
}

const SISTEMA = 'Eres un lector de texto (OCR). Transcribes tal cual el texto que aparece escrito en una imagen, sin corregir, traducir ni completar nada. Contestas solo JSON.';
const INSTRUCCION = 'Transcribe TODO el texto visible en la imagen, exactamente como esta escrito (con sus errores si los hay, sus tildes y sus numeros), en el orden en que se lee. ' +
  'No describas la imagen y no inventes texto que no veas. Devuelve JSON con esta forma: {"textos":["bloque de texto 1","bloque de texto 2"]}';

// llamar: la funcion llamarLLM (se pasa de fuera para poder probar esto sin red).  prov/llave: el proveedor de texto.  url: enlace https de la imagen.
// Devuelve el resultado de comparar() (con ok true/false), o { ok: null, motivo } si no se pudo leer la imagen. Nunca lanza.
export async function verificarTexto(llamar, prov, llave, url, esperado) {
  try {
    if (!prov || !llave || !url) return { ok: null, motivo: 'sin proveedor de texto o sin enlace de la imagen' };
    const r = await llamar(prov, llave, SISTEMA, INSTRUCCION, { imagenes: [{ mime: 'image/png', url }], maxTokens: 2500, temperatura: 0, json: true });
    if (!r || !r.ok) return { ok: null, motivo: String((r && r.error) || 'sin respuesta').slice(0, 160) };
    let textos = null;
    try { const j = jsonDeTexto(r.texto); if (j && Array.isArray(j.textos)) textos = j.textos; } catch (e) { /* se usa el texto crudo */ }
    const leido = textos ? textos.filter((x) => typeof x === 'string').join('\n') : String(r.texto || '');
    const c = comparar(esperado, leido);
    return c || { ok: null, motivo: 'el modelo no leyo texto en la imagen' };
  } catch (e) {
    return { ok: null, motivo: String((e && e.message) || e).slice(0, 160) };
  }
}
