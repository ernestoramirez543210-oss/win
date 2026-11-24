// Un solo lugar donde se habla con cualquier proveedor de LLM.
//
// La suite tiene que poder usar la API que el dueno quiera, asi que aqui se traduce un
// unico formato interno (sistema + instruccion -> texto) a los tres dialectos que existen
// en la practica, mas uno generico compatible con OpenAI para cualquier otro. Sin esto,
// cada pase del agente tendria que saber de proveedores y agregar uno serian seis
// ediciones.
//
// No hay reintentos infinitos ni streaming a proposito: esto corre en un cron y en un
// boton, y un fallo tiene que fallar rapido y decir por que.

import { precioTexto } from './_precios.js';
import { registrarGasto } from './_gasto.js';

const TIMEOUT_MS = 55000;

// Anota lo que costo esta llamada en el libro de gasto (functions/_gasto.js), que es de donde
// sale el saldo estimado de /apis. El proveedor llega aqui sin `env`, asi que los endpoints lo
// cuelgan de `prov` con conEnv(): una propiedad NO enumerable, para que nunca se serialice ni
// viaje al navegador con el resto del proveedor. Sin env, o con un modelo sin precio conocido,
// simplemente no se anota: es contabilidad, no puede romper la respuesta.
export function conEnv(prov, env, col) {
  if (prov && env) Object.defineProperty(prov, '_env', { value: env, enumerable: false, configurable: true });
  // Colector del modo lote (functions/_gasto.js): si viene, el gasto se junta ahi en vez de escribirse.
  if (prov && col) Object.defineProperty(prov, '_col', { value: col, enumerable: false, configurable: true });
  return prov;
}
async function anotarUso(prov, entrada, salida) {
  const p = prov && prov._env && precioTexto(prov.modelo);
  if (!p) return;
  const usd = (entrada || 0) / 1e6 * p.entrada + (salida || 0) / 1e6 * p.salida;
  if (prov._col) { prov._col.push({ p: prov.tipo, u: usd, d: 'texto:' + prov.modelo }); return; }
  await registrarGasto(prov._env, prov.tipo, usd, 'texto:' + prov.modelo);
}

// Un 429 no es un fallo, es "vuelve en un rato". El plan gratis de Gemini da 20
// peticiones por minuto y una corrida del agente gasta seis de golpe, asi que basta con
// que dos caigan juntas para perder el analisis entero. Se espera lo que el propio
// proveedor pide y se reintenta una vez; si vuelve a chocar, ahi si es un problema real.
async function pedir(url, opciones, reintentos = 1) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let r;
  try {
    r = await fetch(url, { ...opciones, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
  if (r.status !== 429 || reintentos <= 0) return r;

  // El cuerpo se lee para sacar la espera sugerida, asi que hay que clonar: si el
  // reintento tambien falla, quien llame necesita poder leer ese cuerpo.
  let espera = 20;
  try {
    const txt = await r.clone().text();
    const m = txt.match(/retry in ([\d.]+)s/i) || txt.match(/"retryDelay"\s*:\s*"([\d.]+)s"/);
    if (m) espera = Math.min(parseFloat(m[1]) + 1, 45);
  } catch {}
  await new Promise((res) => setTimeout(res, espera * 1000));
  return pedir(url, opciones, reintentos - 1);
}

// Un error de la API tiene que llegar legible al usuario: "429 quota exceeded" y no
// "[object Object]". Se intenta el campo que cada proveedor usa para el mensaje.
async function mensajeDeError(r) {
  const txt = await r.text().catch(() => '');
  try {
    const j = JSON.parse(txt);
    return (j.error && (j.error.message || j.error.status)) || j.message || txt.slice(0, 300);
  } catch {
    return txt.slice(0, 300) || ('HTTP ' + r.status);
  }
}

// Una respuesta en streaming (SSE: lineas "data: {...}") vuelta a una respuesta completa: el texto de todos los
// trozos juntos y el uso que traiga el ultimo. Devuelve null si no hay ningun trozo reconocible.
function desdeSSE(crudo) {
  let texto = '', usage = null, hubo = false;
  for (const linea of String(crudo).split(/\r?\n/)) {
    const m = linea.match(/^data:\s*(.*)$/);
    if (!m || m[1] === '[DONE]') continue;
    let c;
    try { c = JSON.parse(m[1]); } catch { continue; }
    const j = (c && !c.choices && c.data && c.data.choices) ? c.data : c;
    const ch = j && j.choices && j.choices[0];
    if (ch) { hubo = true; texto += (ch.delta && ch.delta.content) || (ch.message && ch.message.content) || ''; }
    if (j && j.usage) usage = j.usage;
  }
  return hubo ? { choices: [{ message: { content: texto } }], usage: usage || {} } : null;
}

// prov: { tipo, modelo, url }   llave: string
// opts.imagenes: [{ mime, base64 }] para preguntarle sobre imagenes (leer anuncios). Tambien vale { url } (un enlace https a una imagen publica): solo lo entienden los dialectos
//   tipo OpenAI (openai, together, apimart y los compatibles) y Anthropic; Gemini directo no lee imagenes por enlace y devuelve un error claro en vez de contestar sin verla.
//   Cada dialecto las manda distinto, asi que se traducen aqui igual que el texto: sin
//   esto, analizar un anuncio obligaria a escribir el cuerpo de Gemini a mano en el
//   endpoint y perderiamos el reintento y el manejo de errores que ya vive aqui.
// opts.videos: [{ mime, base64 }] el video ENTERO, con su audio. SOLO Gemini lo lee: mira
//   el video muestreando fotogramas y ademas escucha la narracion, que es donde vive el
//   argumento de un anuncio. Los otros dialectos lo ignoran a proposito -mandarles un
//   mp4 como si fuera imagen devuelve un 400 que solo dice "invalid media type"- y quien
//   llama debe pasarles fotogramas.
// Devuelve { ok, texto } o { ok:false, error }
export async function llamarLLM(prov, llave, sistema, instruccion, opts = {}) {
  if (!prov) return { ok: false, error: 'No hay proveedor configurado' };
  if (!llave) return { ok: false, error: 'Ese proveedor no tiene API key guardada' };
  const maxTokens = opts.maxTokens || 2000;
  const modelo = prov.modelo || '';
  const imgs = Array.isArray(opts.imagenes) ? opts.imagenes.filter((i) => i && (i.base64 || (typeof i.url === 'string' && /^https:\/\//i.test(i.url)))) : [];
  const vids = Array.isArray(opts.videos) ? opts.videos.filter((v) => v && v.base64) : [];

  try {
    if (prov.tipo === 'gemini') {
      if (imgs.some((i) => !i.base64)) return { ok: false, error: 'Gemini directo no lee imagenes por enlace: hay que mandarsela en base64.' };
      const url = 'https://generativelanguage.googleapis.com/v1beta/models/' +
                  encodeURIComponent(modelo) + ':generateContent?key=' + encodeURIComponent(llave);
      // Los flash actuales "piensan" antes de contestar y esos tokens salen del mismo
      // presupuesto: con maxOutputTokens bajo se lo gastan pensando y devuelven vacio con
      // finishReason MAX_TOKENS. Aqui nadie necesita cadena de pensamiento -el modelo
      // opina sobre cifras ya calculadas- asi que se pide el minimo posible.
      //
      // Tres intentos porque cada generacion de flash acepta algo distinto:
      //   0    -> lo mas barato, funciona en los flash de 2024/2025
      //   128  -> los flash nuevos (gemini-flash-latest hoy) RECHAZAN el 0 con
      //           "invalid argument" y exigen un minimo positivo
      //   nada -> ultimo recurso para modelos que no conocen el campo
      // Quitar el campo NO sirve como unico fallback: sin limite el modelo se gasta el
      // presupuesto pensando y devuelve texto vacio, que es peor que un 400 claro.
      const partes = [{ text: instruccion }]
        .concat(imgs.map((i) => ({ inlineData: { mimeType: i.mime || 'image/jpeg', data: i.base64 } })))
        .concat(vids.map((v) => ({ inlineData: { mimeType: v.mime || 'video/mp4', data: v.base64 } })));
      const cuerpo = (thinking) => JSON.stringify({
        systemInstruction: sistema ? { parts: [{ text: sistema }] } : undefined,
        contents: [{ role: 'user', parts: partes }],
        generationConfig: {
          // Cuando el modelo piensa, esos tokens salen de aqui: hay que dejar margen
          // por encima de lo que se pidio o la respuesta sale truncada.
          maxOutputTokens: thinking ? maxTokens + thinking + 256 : maxTokens,
          temperature: opts.temperatura ?? 0.4,
          ...(thinking === null ? {} : { thinkingConfig: { thinkingBudget: thinking } }),
        },
      });
      const post = (t) => pedir(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: cuerpo(t),
      });
      let r = await post(0);
      if (r.status === 400) r = await post(128);
      if (r.status === 400) r = await post(null);
      if (!r.ok) return { ok: false, error: await mensajeDeError(r) };
      const j = await r.json();
      const cand = (j.candidates || [])[0] || {};
      const texto = ((cand.content && cand.content.parts) || []).map((p) => p.text || '').join('');
      if (texto) {
        const u = j.usageMetadata || {};
        // Los tokens de razonamiento de Gemini se cobran como salida aunque no vuelvan en el texto.
        await anotarUso(prov, u.promptTokenCount, (u.candidatesTokenCount || 0) + (u.thoughtsTokenCount || 0));
        return { ok: true, texto };
      }
      const razon = cand.finishReason || (j.promptFeedback && j.promptFeedback.blockReason) || 'sin texto';
      return { ok: false, error: 'El modelo no devolvio texto (' + razon + ')' };
    }

    if (prov.tipo === 'anthropic') {
      const r = await pedir('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': llave,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: modelo, max_tokens: maxTokens, system: sistema || undefined,
          messages: [{
            role: 'user',
            // Un PDF no va como 'image' en este dialecto: es un bloque 'document' aparte.
            // Mandarlo como imagen devuelve un 400 que solo dice "invalid media type".
            content: imgs.length
              ? imgs.map((i) => (
                  (i.mime || '') === 'application/pdf'
                    ? { type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: i.base64 } }
                    : (i.base64 ? { type: 'image', source: { type: 'base64', media_type: i.mime || 'image/jpeg', data: i.base64 } } : { type: 'image', source: { type: 'url', url: i.url } })
                )).concat([{ type: 'text', text: instruccion }])
              : instruccion,
          }],
        }),
      });
      if (!r.ok) return { ok: false, error: await mensajeDeError(r) };
      const j = await r.json();
      const texto = (j.content || []).map((b) => b.text || '').join('');
      if (!texto) return { ok: false, error: 'Respuesta vacia del modelo' };
      await anotarUso(prov, j.usage && j.usage.input_tokens, j.usage && j.usage.output_tokens);
      return { ok: true, texto };
    }

    // openai, together, apimart y cualquier otra compatible: mismo cuerpo, distinta URL.
    // Together y APIMart se traen aparte para no obligar a escribir la URL a mano, que es donde
    // se cuela un typo y el error resultante no dice nada util.
    const url = prov.tipo === 'openai'   ? 'https://api.openai.com/v1/chat/completions'
              : prov.tipo === 'together' ? 'https://api.together.xyz/v1/chat/completions'
              : prov.tipo === 'apimart'  ? 'https://api.apimart.ai/v1/chat/completions'
              : (prov.url || '');
    if (!url) return { ok: false, error: 'Ese proveedor no tiene URL configurada' };
    const mensajes = [];
    if (sistema) mensajes.push({ role: 'system', content: sistema });
    mensajes.push({
      role: 'user',
      content: imgs.length
        ? [{ type: 'text', text: instruccion }].concat(imgs.map((i) => ({
            type: 'image_url',
            image_url: { url: i.base64 ? 'data:' + (i.mime || 'image/jpeg') + ';base64,' + i.base64 : i.url },
          })))
        : instruccion,
    });
    // Modo JSON cuando se pide JSON. Casi todo aqui espera JSON estricto, y los modelos
    // abiertos (Llama, Qwen, Mistral por Together) lo devuelven envuelto en explicaciones
    // o con una coma de mas mucho mas seguido que Gemini o Claude. Pedirlo por protocolo
    // en vez de solo por prompt es la diferencia entre que sirvan y que no.
    //
    // Se manda tambien max_tokens: los servidores compatibles no siempre entienden
    // max_completion_tokens, y sin limite algunos escriben hasta agotar el contexto.
    const cuerpoOA = {
      model: modelo, messages: mensajes,
      max_completion_tokens: maxTokens, max_tokens: maxTokens,
      temperature: opts.temperatura ?? 0.4,
    };
    if (opts.json) cuerpoOA.response_format = { type: 'json_object' };
    // El chat de APIMart contesta en STREAMING (SSE) si no se le dice lo contrario -su documentacion marca
    // stream=true como lo predeterminado en /v1/chat/completions-, y aqui se espera un JSON completo: sin esto
    // «Probar conexion» y todo lo que usa texto fallaban con un error de lectura que no explicaba nada.
    if (prov.tipo === 'apimart') cuerpoOA.stream = false;

    const postOA = () => pedir(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + llave },
      body: JSON.stringify(cuerpoOA),
    });

    // Escalera de reintentos: cada 400 quita el campo que ese servidor no acepta, en vez
    // de dejar al usuario con un error que no explica nada.
    //   - response_format -> servidores que no conocen el modo JSON
    //   - max_tokens      -> los modelos de razonamiento de OpenAI (o1/o3) lo RECHAZAN y exigen
    //     max_completion_tokens; mandar los dos es un 400 seguro
    //   - temperature     -> la familia GPT-5 y los modelos de razonamiento solo aceptan la temperatura
    //     por defecto: cualquier otro valor es un 400. APIMart reparte justo esos modelos, y sin este paso
    //     buena parte de su catalogo de texto no respondia.
    // Se quita primero el campo que el propio error nombra; si no nombra ninguno, el siguiente en ese orden.
    // Un 400 no se cobra, asi que reintentar solo cuesta unos milisegundos.
    let r = await postOA();
    for (let paso = 0; paso < 3 && r.status === 400; paso++) {
      const dicho = (await r.clone().text().catch(() => '')).toLowerCase();
      const orden = ['response_format', 'max_tokens', 'temperature'];
      const campo = orden.find((c) => c in cuerpoOA && dicho.indexOf(c) !== -1) || orden.find((c) => c in cuerpoOA);
      if (!campo) break;
      delete cuerpoOA[campo];
      r = await postOA();
    }
    if (!r.ok) return { ok: false, error: await mensajeDeError(r) };
    // Normalmente es un JSON. Si el servidor contesta en streaming (SSE) aunque no se le haya pedido, se arma
    // la respuesta juntando los trozos; y si no es ninguna de las dos cosas, el error dice que llego.
    const crudo = await r.text();
    let resp;
    try { resp = JSON.parse(crudo); } catch { resp = desdeSSE(crudo); }
    if (!resp) return { ok: false, error: 'No pude leer la respuesta del proveedor: ' + crudo.slice(0, 120) };
    // La documentacion de APIMart muestra la respuesta envuelta en { code, data: { choices } }
    // y su pagina de inicio la muestra sin envolver: se aceptan las dos formas.
    const j = (resp && !resp.choices && resp.data && resp.data.choices) ? resp.data : resp;
    const texto = ((j.choices || [])[0]?.message?.content) || '';
    if (!texto) return { ok: false, error: 'Respuesta vacia del modelo' };
    await anotarUso(prov, j.usage && j.usage.prompt_tokens, j.usage && j.usage.completion_tokens);
    return { ok: true, texto };

  } catch (e) {
    if (e.name === 'AbortError') return { ok: false, error: 'El proveedor no contesto en 55 s' };
    return { ok: false, error: e.message || String(e) };
  }
}

// El modelo devuelve JSON dentro de texto, a veces envuelto en ```json, y a veces
// cortado a la mitad porque se acabo el presupuesto de tokens.
//
// Un JSON truncado se puede aprovechar: lo que alcanzo a escribir es valido, solo le
// faltan los cierres. Recuperar cuatro recomendaciones de cinco es mucho mejor que
// perder la corrida entera -y que el modelo la vuelva a cobrar- por una llave que falta.
export function jsonDeTexto(texto) {
  const t = recortarJson(texto);
  if (t === null) return null;

  try { return JSON.parse(t); } catch {}
  // Primero se arregla el formato y despues se cierra lo cortado, no al reves: una comilla de mas
  // da vuelta el estado de "dentro de una cadena" para todo lo que sigue, y cerrar sobre eso
  // "salvaba" solo las paginas de antes de la comilla y tiraba el resto sin avisar.
  const r = repararCadenas(t);
  if (r !== t) { try { return JSON.parse(r); } catch {} }
  try { return JSON.parse(cerrarAbiertos(t)); } catch {}
  if (r !== t) { try { return JSON.parse(cerrarAbiertos(r)); } catch {} }
  return null;
}

// Del texto del modelo, el JSON: sin la cerca ```json y desde el primer delimitador que aparezca
// (objeto o arreglo). null si no hay ninguno.
function recortarJson(texto) {
  if (!texto) return null;
  let t = String(texto).trim();
  const cerca = t.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (cerca) t = cerca[1].trim();
  const i = t.indexOf('{'), a = t.indexOf('[');
  const desde = (a !== -1 && (i === -1 || a < i)) ? a : i;
  return desde === -1 ? null : t.slice(desde);
}

// Una frase que dice POR QUE no se pudo leer la respuesta del modelo, para el mensaje de error: si
// se corto, o en que caracter se rompio y con que alrededor. Sin esto el error solo mostraba el
// principio de la respuesta, que casi siempre esta bien, y no habia como saber que fallaba.
export function diagnosticoJson(texto) {
  const t = recortarJson(texto);
  if (t === null) return 'la respuesta no trae ningun JSON';
  try { JSON.parse(t); return 'el JSON esta bien formado, pero no trae la lista de paginas'; } catch (e) {
    const m = /position (\d+)/.exec((e && e.message) || '');
    if (!m) return 'JSON roto (' + String((e && e.message) || '').slice(0, 100) + ')';
    const p = Number(m[1]);
    if (p >= t.length - 1) return `la respuesta se corto antes de cerrar el JSON (${t.length} caracteres)`;
    const alrededor = (s) => s.replace(/\s+/g, ' ');
    return `JSON roto en el caracter ${p} de ${t.length}: «${alrededor(t.slice(Math.max(0, p - 70), p))} ▸ ${alrededor(t.slice(p, p + 50))}»`;
  }
}

// Lo que debe seguir a una comilla para que CIERRE la cadena que abrio, segun donde vive esa
// cadena (sticky: se prueban en la posicion exacta). Una comilla que no cumple esto es del
// CONTENIDO ("las pestañas "Mateo", "Marcos" y "Lucas"") y se escapa.
//   clave    -> dos puntos
//   objeto   -> un valor de objeto: } o la SIGUIENTE CLAVE ("nombre":). Una coma y otra cadena
//               sin dos puntos NO cierra: es una lista de nombres dentro del texto.
//   arreglo  -> un elemento de lista: ] o la siguiente cadena/objeto/lista/cifra. Una cadena
//               siguiente cuenta si termina en , o ], si viene PEGADA a la coma (",") o si
//               empieza en otra linea (,\n"): asi escribe el JSON un elemento nuevo, mientras
//               que una lista dentro de un texto lleva solo un espacio ("a", "b")
//   raiz     -> el final del texto
//   (objeto) tambien cierra si sigue una coma, un salto de linea y una clave a la que le falta la comilla de ABRIR (`",\n      puntos": [`): lo escribe asi
//   gemini-3.5-flash-lite en ~6 % de las paginas de los ebooks (medido en 435 paginas reales, 2026-11-13) y sin esto la comilla se tomaba como parte del texto y el
//   «texto» se tragaba los puntos y el error comun, que terminaban dibujados en la pagina como codigo. La comilla que falta la pone CLAVE_SIN_COMILLA, abajo.
const CIERRA = {
  clave: /\s*:/y,
  objeto: /\s*(?:\}|,\s*\}|,\s*"[^"\n]{1,80}"\s*:|,[ \t]*\r?\n\s*[A-Za-z_]\w{0,40}"\s*:|$)/y,
  arreglo: /\s*(?:\]|,\s*\]|,\s*(?:\{|\[|-?\d|true\b|false\b|null\b)|,"|,[ \t]*\r?\n\s*"|,\s*"[^"]*"\s*[,\]]|$)/y,
  raiz: /\s*$/y,
};
const COMA_COLGANTE = /\s*[}\]]/y;
// Una clave de objeto sin la comilla de abrir (`puntos": [`): se reconoce por la comilla de cerrar seguida de dos puntos.
const CLAVE_SIN_COMILLA = /[A-Za-z_]\w{0,40}"\s*:/y;
const ESCAPES_VALIDOS = '"\\/bfnrtu';

// Arregla las roturas de formato mas comunes en el JSON de un modelo, sin tocar el contenido:
//   - una comilla de mas dentro de un texto (el caso del contenido biblico: "Mateo", "Salmo 23");
//   - un salto de linea o tabulador CRUDO dentro de un texto (JSON.parse lo rechaza);
//   - una coma de mas antes de } o ];
//   - un escape que no existe (\') .
// Solo se llama cuando el JSON ya no se pudo leer, asi que un JSON valido no pasa nunca por aqui.
function repararCadenas(t) {
  const pila = [];                  // { o [ abiertos fuera de cadenas
  let salida = '', enCadena = false, ctx = '', ultimo = '';
  const sigue = (re, k) => { re.lastIndex = k; return re.test(t); };
  for (let k = 0; k < t.length; k++) {
    const c = t[k];
    if (!enCadena) {
      if (c === '"') {
        const top = pila[pila.length - 1];
        // En un objeto, una cadena tras { o , es una CLAVE; tras : es un valor.
        ctx = top === '{' ? ((ultimo === '{' || ultimo === ',') ? 'clave' : 'objeto') : top === '[' ? 'arreglo' : 'raiz';
        enCadena = true; salida += c;
        continue;
      }
      if (c === ',' && sigue(COMA_COLGANTE, k + 1)) continue;      // coma de mas: se quita
      // donde va una clave hay una palabra pegada a `":` -> le falta la comilla de abrir: se pone y se sigue como clave
      if (pila[pila.length - 1] === '{' && (ultimo === ',' || ultimo === '{') && sigue(CLAVE_SIN_COMILLA, k)) {
        enCadena = true; ctx = 'clave'; salida += '"' + c;
        continue;
      }
      if (c === '{' || c === '[') pila.push(c); else if (c === '}' || c === ']') pila.pop();
      if (c > ' ') ultimo = c;
      salida += c;
      continue;
    }
    if (c === '\\') {
      const n = t[k + 1];
      if (n === undefined) break;                                   // barra suelta al final: se descarta
      salida += ESCAPES_VALIDOS.indexOf(n) !== -1 ? c + n : n;      // \' -> '
      k++;
      continue;
    }
    if (c === '"') {
      if (sigue(CIERRA[ctx], k + 1)) { enCadena = false; ultimo = '"'; salida += c; } else salida += '\\"';
      continue;
    }
    if (c === '\n') salida += '\\n';
    else if (c === '\r') salida += '\\r';
    else if (c === '\t') salida += '\\t';
    else if (c < ' ') salida += '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0');
    else salida += c;
  }
  return salida;
}

// Cierra los corchetes y llaves que quedaron abiertos, ignorando los que van dentro de
// una cadena. Antes de cerrar corta la ultima entrada a medias y cualquier coma colgando,
// que es como se ve siempre un corte por tokens.
function cerrarAbiertos(t) {
  const pila = [];
  let enCadena = false, escapado = false;
  let finSeguro = 0, pilaEnCorte = null;
  for (let k = 0; k < t.length; k++) {
    const c = t[k];
    if (escapado) { escapado = false; continue; }
    if (c === '\\') { escapado = true; continue; }
    if (c === '"') { enCadena = !enCadena; continue; }
    if (enCadena) continue;
    if (c === '{' || c === '[') pila.push(c === '{' ? '}' : ']');
    else if (c === '}' || c === ']') {
      pila.pop();
      // El JSON ya cerro completo: lo que venga despues es charla del modelo ("Listo.", una
      // nota, una segunda copia) y se descarta.
      if (!pila.length) return t.slice(0, k + 1);
      // Hasta aqui hay una estructura completa. Se guarda TAMBIEN como quedo la pila en
      // ese punto: reusar la del final agregaria el cierre del objeto a medias que se
      // acaba de descartar, y el JSON saldria con una llave de mas.
      finSeguro = k + 1;
      pilaEnCorte = pila.slice();
    }
  }
  if (!pila.length) return t;
  if (!pilaEnCorte) return null;      // ni un solo elemento completo: no hay nada que salvar
  const corte = t.slice(0, finSeguro).replace(/,\s*$/, '');
  return corte + pilaEnCorte.reverse().join('');
}
