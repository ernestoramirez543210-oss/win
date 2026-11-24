// Rescata los campos de una pagina del plan que quedaron METIDOS dentro de otro texto.
//
// Por que existe (medido el 2026-11-13 en los 36 ebooks guardados del equipo, 435 paginas): en 25 paginas de 10 ebooks el modelo de texto
// (gemini-3.5-flash-lite) escribio una clave sin la comilla de abrir (`"texto": "....",\n puntos": [ ... ], "errorComun": "..."`). El lector la tomaba
// como parte del texto: la pagina quedaba con «puntos": [ "Calcula..." ], "errorComun": ...» dentro del parrafo, sin puntos ni error comun, y
// la IA DIBUJABA ese codigo en la imagen. La lectura del texto la daba por buena (el codigo tambien estaba en el plan) y «Dibujar de nuevo»
// volvia a dibujarlo, porque el error estaba en el plan y no en la imagen. El equipo lo arreglaba a mano copiando la descripcion de otra pagina,
// y el ebook terminaba con paginas repetidas.
//
// El lector (functions/_llm.js) ya reconoce esa clave sin comilla en las respuestas nuevas. Esto es la segunda red: arregla lo que llegue igual
// (otra forma de la misma fuga) y los planes YA GUARDADOS, que vuelven por /api/ebook-pagina cada vez que se dibuja una pagina.
//
// Lo que hace, sin llamar a ningun modelo:
//   - busca en cada texto (y en cada elemento de las listas) una coma seguida de una clave conocida entre comillas y dos puntos;
//   - corta el texto ahi (lo de antes es el texto bueno);
//   - lee lo de despues como JSON (con el mismo lector tolerante) y pone cada campo rescatado en la pagina SI la pagina no lo trae ya;
//   - si lo de despues no se puede leer, igual corta: un texto sin el codigo es mejor que una pagina con codigo dibujado.
import { jsonDeTexto } from './_llm.js';

// Los campos de una pagina del plan (functions/api/ebook-plan.js) que puede traer el modelo. Solo estos se buscan y solo estos se rescatan.
const LISTAS = ['puntos', 'ingredientes', 'pasos'];
const TEXTOS = ['titulo', 'texto', 'ejemplo', 'errorComun', 'senal', 'tip', 'foto', 'porQueFunciona', 'variacion', 'tiempo', 'porciones'];
const CLAVES = LISTAS.concat(TEXTOS);
// La comilla que cerraba el texto bueno, una coma, una comilla opcional (la que el modelo olvido), la clave, una comilla, dos puntos y el COMIENZO de un valor (una lista o
// un texto). Las tres exigencias son la forma medida de la fuga y lo que la separa de un texto normal: la revision del 2026-11-13 mostro que sin la comilla de antes
// «…con tu receta, "tiempo": 20 minutos» o «…regla de oro, 'tip': mide» se cortaban.
const FUGA = new RegExp('["\'“”]\\s*,\\s*["\']?(' + CLAVES.join('|') + ')["\']\\s*:\\s*[\\["\']', '');

// El texto de un prompt de imagen con la fuga adentro (las comillas dobles del plan pasan a simples al armar el prompt: functions/_ebook-pagina-ia.js).
const FUGA_EN_PROMPT = new RegExp('\\b(' + LISTAS.concat(['errorComun', 'porQueFunciona', 'variacion', 'senal']).join('|') + ')["\']\\s*:\\s*[\\[\'"]');
export function promptConFuga(prompt) { return FUGA_EN_PROMPT.test(String(prompt || '')); }

export function tieneFuga(v) { return typeof v === 'string' && FUGA.test(v); }

// Lo que sobro despues del corte, como objeto: `puntos": [ "a", "b" ], "errorComun": "x` -> { puntos: [...], errorComun: 'x' }. null si no se lee.
function leerResto(resto) {
  const base = '{"' + resto.replace(/^\s+/, '');
  const conSimples = /^\{"\w+'\s*:/.test(base);
  const candidatos = [base + '"}', base + '}', base];
  if (conSimples) {
    // la misma fuga escrita con comillas simples: 'puntos': ['a', 'b'], 'tip': 'x'
    const dobles = base.replace(/^\{"(\w+)'/, '{"$1"').replace(/'\s*([:,\]}])/g, '"$1').replace(/([[{,:]\s*)'/g, '$1"');
    candidatos.unshift(dobles + '"}', dobles + '}', dobles);
  }
  for (const c of candidatos) {
    const j = jsonDeTexto(c);
    if (j && typeof j === 'object' && !Array.isArray(j)) return j;
  }
  return null;
}

// El texto bueno: lo de antes de la fuga, sin la comilla ni la coma colgando.
function cortar(v, m) { return v.slice(0, m.index).replace(/[\s"'“”,]+$/, '').trim(); }

function limpiarTexto(v) {
  return typeof v === 'string' ? v.replace(/\s+/g, ' ').trim() : '';
}

// Mezcla en la pagina lo rescatado: solo campos conocidos, del tipo correcto, y solo donde la pagina no traia nada.
function mezclar(p, r) {
  let n = 0;
  if (!r) return n;
  for (const k of LISTAS) {
    if (!Array.isArray(r[k])) continue;
    const lista = r[k].map(limpiarTexto).filter(Boolean).slice(0, 20);
    if (lista.length && !(Array.isArray(p[k]) && p[k].length)) { p[k] = lista; n++; }
  }
  for (const k of TEXTOS) {
    const t = limpiarTexto(r[k]);
    if (t && !limpiarTexto(p[k])) { p[k] = t; n++; }
  }
  return n;
}

// Arregla la pagina EN EL SITIO y devuelve cuantos textos tenian fuga (0 = no habia nada que arreglar).
export function rescatarFugas(p) {
  if (!p || typeof p !== 'object' || Array.isArray(p)) return 0;
  let arreglados = 0;
  // Unas pocas vueltas: lo rescatado puede traer su propia fuga (el error comun metido dentro de los puntos, por ejemplo).
  for (let vuelta = 0; vuelta < 4; vuelta++) {
    let hubo = false;
    for (const k of TEXTOS) {
      const v = p[k];
      if (typeof v !== 'string') continue;
      const m = FUGA.exec(v);
      if (!m) continue;
      hubo = true; arreglados++;
      const resto = v.slice(m.index + m[0].lastIndexOf(m[1]));     // desde la clave, sin su comilla de abrir: «puntos": [ ...»
      p[k] = cortar(v, m);
      mezclar(p, leerResto(resto));
    }
    for (const k of LISTAS) {
      if (!Array.isArray(p[k])) continue;
      const nueva = [];
      for (const it of p[k]) {
        if (typeof it !== 'string') { nueva.push(it); continue; }
        const m = FUGA.exec(it);
        if (!m) { nueva.push(it); continue; }
        hubo = true; arreglados++;
        const resto = it.slice(m.index + m[0].lastIndexOf(m[1]));
        const bueno = cortar(it, m);
        if (bueno) nueva.push(bueno);
        mezclar(p, leerResto(resto));
      }
      p[k] = nueva;
    }
    if (!hubo) break;
  }
  return arreglados;
}
