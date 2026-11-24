// Paso 3 del ebook: una pagina terminada = foto del plato + texto maquetado.
//
// Una pagina por llamada. Cloudflare corta a los 30 s y una imagen tarda 5-15 s; de a una
// entra holgado y el cliente va mostrando cada pagina conforme llega, en vez de esperar
// cinco minutos frente a una barra sin nada.
//
// La MAQUETACION es codigo, no IA (functions/_ebook-maqueta.js): recibe los campos de la pagina y arma el HTML con
// el tamaño de letra en que todo cabe, sin gastar una sola llamada de texto. Antes cada pagina hacia dos llamadas de
// texto -la frase de la foto y un "maquetador" que metia en HTML el texto ya escrito- y entre las dos eran ~60-70 % de los
// tokens de un ebook. Ahora la frase de la foto sale del plan (ebook-plan.js) y lo unico que se paga por pagina es la imagen.
//
// Dos formas de hoja (2026-10-20, pedido del dueño: «que se vea profesional, como el PDF de ejemplo»):
//   diseno 'codigo' (por omision en la API): la maqueta de arriba + una foto. El texto es exacto y se puede editar, pero el diseño es el mismo banner arriba en todas las paginas.
//   diseno 'ia': la pagina ENTERA la dibuja el modelo de imagenes (gpt-image-2) con el texto adentro, con un diseño que cambia segun la oferta (functions/_ebook-pagina-ia.js). Una imagen
//   por pagina, el mismo costo. El texto lo dibuja el modelo y puede salir distinto: despues se LEE la imagen con un modelo de texto con vision (functions/_ebook-verificar.js) y el
//   cliente repite la que salga mal. Una pagina con demasiado texto para una imagen se arma por codigo.
// La PORTADA lleva por omision el titulo escrito dentro de la imagen (tituloEnImagen:false = foto sin letras y titulo en HTML, como antes).
// Si la imagen tarda mas de ~100 s la respuesta trae el id de su tarea (tareaImagen) y quien llama la retoma sin pagar otra.
//
// Reparto de responsabilidades, aprendido a golpes:
//   - El MARCO (medidas, foto arriba, alto del texto) y el reparto salen de la reticula del tipo de entregable.
//   - El texto se coloca dentro de una caja de alto fijo, eligiendo el tamaño de letra para que quepa TODO.
// Cuando se le pedia al modelo "que la foto ocupe el 40%", en una de dos pruebas se la llevo al 94% y el texto se
// desbordo 472 px fuera de la hoja, perdiendo medio metodo de preparacion. El layout no se pide: se impone.

import { exigirAlguno, EQUIPO } from '../_sesion.js';
import { llamarLLM, conEnv } from '../_llm.js';
import { precioImagen, modeloSirve } from '../_precios.js';
import { registrarGasto, resumirGasto } from '../_gasto.js';
import { leerConocimiento, conocimientoTexto } from './conocimiento.js';
import { avatarTexto } from './ebook-avatar.js';
import { maquetarPagina, modeloDe, colorSeguro, esPaginaDeReceta } from '../_ebook-maqueta.js';
import { promptPaginaIA, promptPortadaIA, esperadoPortada, cabeEnIA, htmlPaginaIA } from '../_ebook-pagina-ia.js';
import { verificarTexto } from '../_ebook-verificar.js';
import { rescatarFugas, promptConFuga } from '../_ebook-fugas.js';

// 100 s por peticion: gpt-image-2 por APIMart tarda de 25 a 85 s medidos (una portada o una pagina con mucho texto es mas lenta) y con un tope de 90 s se cortaba justo la que iba a
// llegar -en una corrida de 14 hojas, 3 salieron sin foto-. Peor: la tarea SIGUE en el proveedor y se cobra aunque nadie la espere. Por eso, cuando el tiempo se acaba, la respuesta
// trae el id de la tarea (tareaImagen) y quien llama la RETOMA con otra peticion sin pagar otra imagen (ver opt.tarea): asi una tarea de 150 s llega igual, sin que ninguna peticion
// dure mas de ~100 s (el limite al que suelen cortar los intermediarios y las redes del telefono).
// (Antes: 90 s y no 25: la generacion de imagen con los modelos buenos con texto (gpt-image-2,
// seedream) tarda 30-60 s en Together, y el timeout de 25 s cortaba justo en el medio:
// el mockup fallaba con "no respondio en 25 s" y el preview se quedaba con la imagen
// vieja, que parecia un mockup que no se regeneraba. Cloudflare no limita el tiempo total
// de una peticion HTTP mientras el cliente siga conectado -solo el CPU, y esperar un
// fetch no consume CPU-, asi que el margen no cuesta nada.)
const TIMEOUT = 100000;

// Medidas de la hoja (768 x 1024): las usan la portada, el cierre y el tamaño de las imagenes. El marco de las paginas normales y la reticula
// de cada tipo de entregable (cuanto de la hoja es foto) viven en functions/_ebook-maqueta.js.
const ANCHO = 768, ALTO = 1024;

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// opciones de generarImagen:
//   modelo  - pisa el modelo configurado (la portada usa el bueno, las paginas el barato)
//   tarea   - (solo APIMart) el id de una tarea que ya se pidio y no se alcanzo a recibir: se sigue esperando ESA en vez de crear -y pagar- otra
//   encuadre- lo que se le agrega al final de la descripcion de la foto (SUFIJO_PLATO y SUFIJO_NEUTRO, mas abajo):
//               'plato'  el plato servido: SOLO para las paginas que son una receta (esPaginaDeReceta);
//               'neutro' el mismo cuidado de luz y fondo pero SIN comida: guias, devocionales, plantillas...;
//               'propio' -o no decir nada- no se agrega nada: para prompts que ya se describen enteros (portada, cierre, mockup).
//           La comida hay que PEDIRLA: sin encuadre la foto sale tal como se describio. (Antes lo que no se pedia caia en el plato servido por omision, y una
//           guia de crochet, un devocional o un juego de plantillas recibia en cada foto la orden de fotografiar un plato.)
//
// El sufijo de comida NO puede ser incondicional. La portada pedia "composicion vertical
// con la mitad de abajo despejada para el titulo" y a continuacion se le pegaba "el plato
// servido, centrado, llenando el cuadro": dos encuadres opuestos en el mismo prompt, y
// ganaba el plato. De ahi salian portadas que eran una foto de mesa. La portada y el
// cierre ya describen su encuadre, asi que pasan 'propio' y nadie les añade nada.
// La lista de modelos que el proveedor dejo de servir sin endpoint dedicado.
//
// Vive en KV y no en el codigo a proposito: el catalogo de Together cambia solo, y una
// lista escrita a mano aqui obligaria a un despliegue cada vez que retiran uno. Asi el
// sistema aprende del fallo — el primer ebook que se topa con el modelo muerto lo anota, y
// desde ese momento ni el selector lo ofrece ni el generador lo intenta.
const CAIDOS = 'imagen:caidos';

function esNoServerless(msg) {
  return /non-serverless|dedicated endpoint|not available serverless/i.test(String(msg || ''));
}

export async function modelosCaidos(env) {
  try { return (await env.SUITE.get(CAIDOS, { type: 'json' })) || []; } catch { return []; }
}

async function anotarCaido(env, id) {
  if (!id) return;
  const l = await modelosCaidos(env);
  if (l.indexOf(id) !== -1) return;
  l.push(id);
  // Un mes: si el proveedor lo vuelve a ofrecer, la lista se limpia sola en vez de quedar
  // condenando para siempre a un modelo que ya volvio.
  // Anotarlo es un extra: si KV rechaza la escritura (tope diario) la generacion NO debe morir
  // por eso. Antes la excepcion subia y el mockup terminaba en "Unexpected token <".
  try {
    await env.SUITE.put(CAIDOS, JSON.stringify(l.slice(-40)), { expirationTtl: 30 * 24 * 3600 });
  } catch { /* se reintenta la proxima vez que ese modelo falle */ }
}

// El siguiente modelo de imagen que el proveedor SI sirve, preguntandole a el.
//
// No hay lista de respaldo escrita a mano: seria adivinar, y adivinar es exactamente lo
// que dejo el selector ofreciendo un modelo retirado.
async function otroModelo(env, llave, excluir) {
  try {
    const r = await fetch('https://api.together.xyz/v1/models', {
      headers: { Authorization: 'Bearer ' + llave },
    });
    if (!r.ok) return null;
    const lista = await r.json();
    const caidos = await modelosCaidos(env);
    // El reemplazo se elige por PRECIO, no por nombre, y nunca puede costar mas que el que
    // cayo (con un margen de 50%). Antes se preferia cualquier "dev" o "schnell": cuando
    // stable-diffusion-xl dejo de servirse, las paginas cayeron a FLUX.2-dev, 8 veces mas caro,
    // y un ebook que se estimo en 8 centavos costo 19 sin que nadie lo hubiera elegido. Los
    // modelos sin precio conocido no entran: sin precio no se puede prometer que no sea caro.
    const tope = precioImagen(excluir);
    const cand = (Array.isArray(lista) ? lista : [])
      .filter((m) => m.type === 'image' && m.id !== excluir && caidos.indexOf(m.id) === -1 && modeloSirve(m.id))
      .map((m) => ({ id: m.id, precio: precioImagen(m.id) }))
      .filter((m) => m.precio != null && (tope == null || m.precio <= tope * 1.5))
      .sort((a, b) => a.precio - b.precio);
    return cand.length ? cand[0].id : null;
  } catch { return null; }
}

// Saca de un error tipo "Supported values are: '1024x1024', '864x1184'" la lista de tamaños.
function tamanosAceptados(txt) {
  if (!/supported values/i.test(txt)) return [];
  const lista = [];
  const re = /(\d{3,4})x(\d{3,4})/g;
  let m;
  while ((m = re.exec(txt))) lista.push([Number(m[1]), Number(m[2])]);
  return lista;
}

// El tamaño de la lista con la proporcion mas parecida a la pedida. Si dos empatan, el mas
// grande: una foto se puede reducir sin perder nada y ampliar no.
function tamanoMasCercano(lista, ancho, alto) {
  const objetivo = Math.log(ancho / alto);
  let mejor = lista[0], dist = Infinity;
  lista.forEach((t) => {
    const d = Math.abs(Math.log(t[0] / t[1]) - objetivo);
    if (d < dist - 1e-9 || (Math.abs(d - dist) <= 1e-9 && t[0] * t[1] > mejor[0] * mejor[1])) { dist = d; mejor = t; }
  });
  return mejor;
}

// APIMart pide la forma de la imagen como relacion de aspecto. Si quien llama ya dijo una
// ("1:1" en los mockups) se respeta; si no, se toma la mas cercana de las que aceptan casi
// todos sus modelos a partir del ancho y el alto en pixeles.
const ASPECTOS_APIMART = ['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9'];
function aspectoApimart(ancho, alto, pedido) {
  if (pedido && ASPECTOS_APIMART.indexOf(pedido) !== -1) return pedido;
  const objetivo = ancho / alto;
  let mejor = ASPECTOS_APIMART[0], dist = Infinity;
  ASPECTOS_APIMART.forEach((a) => {
    const p = a.split(':');
    const d = Math.abs(Math.log(p[0] / p[1]) - Math.log(objetivo));
    if (d < dist) { dist = d; mejor = a; }
  });
  return mejor;
}

// Los dos cierres que `generarImagen` le pega a la descripcion de una foto de pagina (ver `encuadre` arriba). Solo el del plato habla de comida; el neutro
// conserva lo que la foto necesita pero no nombra ningun objeto: la frase que escribio el plan (campo `foto`) es la que manda sobre QUE se ve.
// Sobre «sujeto unico, centrado»: no es adorno. La hoja recorta la foto a una franja de 200 a 400 px de los 1024 de la hoja (`object-fit:cover`, ver
// functions/_ebook-maqueta.js) y de ella solo se ve el CENTRO; el plan ya pide «un solo sujeto, centrado» en la frase y aqui se refuerza para que un
// encuadre cualquiera -o una frase que la persona edito con «otra foto»- no deje la franja vacia. El texto de los dos esta fijado por
// scripts/probar-ebook-sufijo-foto.mjs: cambiarlo es una decision, no un descuido.
const SIN_TEXTO = 'No text, no letters, no words, no numbers, no watermarks, no hands.';
const SUFIJO_PLATO = '. The finished plated dish, centered, filling the frame, appetizing and freshly served. ' +
  'Clean uncluttered background, soft natural light, shallow depth of field. ' +
  'Professional food photography, luxury magazine quality. ' + SIN_TEXTO;
const SUFIJO_NEUTRO = '. A single subject, centered, filling the frame. ' +
  'Clean uncluttered background, soft natural light, shallow depth of field. ' +
  'Professional editorial photography, luxury magazine quality. ' + SIN_TEXTO;
// Cualquier otro valor (incluido «nada») = sin cierre: la comida nunca se agrega por descuido.
const sufijoDeFoto = (encuadre) => (encuadre === 'plato' ? SUFIJO_PLATO : encuadre === 'neutro' ? SUFIJO_NEUTRO : '');

// Exportada: mockup-oferta.js reusa toda la logica de proveedor/reintento/modelo-caido de
// aqui en vez de duplicarla. El ancho/alto y la relacion de aspecto son opcionales y caen
// en las medidas de la hoja del ebook si no se piden otras, asi que nada de lo existente
// cambia de tamano por este agregado.
export async function generarImagen(env, prompt, opciones) {
  const img = await generarImagenInterna(env, prompt, opciones);
  // El gasto se anota AQUI y no dentro de generarImagenInterna: esa se llama a si misma al
  // reintentar con otro modelo, y anotar adentro contaria dos veces la misma imagen. Un fallo
  // no se anota -no devolvio imagen, el proveedor no lo cobra-.
  if (img.ok) {
    try {
      const raw = await env.SUITE.get('ajustes');
      const tipo = ((raw ? JSON.parse(raw) : {}).imagenApi || {}).tipo;
      // APIMart informa lo que cobro de verdad por la imagen (data.cost, en USD) y ese numero manda:
      // ya trae el descuento de la cuenta y la resolucion real. Sin el, o con otro proveedor, se
      // estima con la tabla de functions/_precios.js (la de APIMart si es APIMart).
      const usd = (img.costo > 0 ? img.costo : 0) || precioImagen(img.modelo, null, tipo);
      if (tipo && usd) {
        // Modo lote: se junta en el colector de la peticion y viaja en la respuesta.
        if (opciones && opciones.colector) opciones.colector.push({ p: tipo, u: usd, d: 'imagen:' + img.modelo });
        else await registrarGasto(env, tipo, usd, 'imagen:' + img.modelo);
      }
    } catch { /* anotar no puede tumbar una imagen que ya salio */ }
  }
  return img;
}

async function generarImagenInterna(env, prompt, opciones) {
  const opt = opciones || {};
  const anchoImg = opt.ancho || ANCHO, altoImg = opt.alto || ALTO;
  const aspectoImg = opt.aspecto || '2:3';
  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const imgCfg = ajustes.imagenApi;
  if (!imgCfg || !imgCfg.tipo) return { ok: false, error: 'No hay proveedor de imágenes. Ve a APIs → Proveedor de imágenes.' };

  const llave = await env.SUITE.get('secreto:imagen');
  if (!llave) return { ok: false, error: 'El proveedor de imágenes no tiene API key.' };

  // Se pide un plato SERVIDO y aislado, no una escena: la foto va dentro de un marco, no
  // como fondo con el texto encima -eso tapaba justo lo que el lector quiere ver-.
  // Eso es solo para las RECETAS. Una guia, un devocional o una pagina de plantillas con "the finished plated dish" pegado al final sale con comida en la foto
  // aunque la frase del plan hable de un escritorio: ahi va el encuadre 'neutro', que conserva la luz, el fondo limpio y el "sin letras" pero sin comida.
  // Y quien no pide ningun encuadre no recibe ninguno (sufijoDeFoto): el plato servido solo sale cuando alguien lo pide con 'plato'.
  const promptFinal = prompt + sufijoDeFoto(opt.encuadre);
  const modeloElegido = opt.modelo || imgCfg.modelo;

  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT);
  let tareaPendiente = null;      // la tarea de APIMart que se esta esperando (si el tiempo se acaba, se devuelve para retomarla)
  try {
    if (imgCfg.tipo === 'together') {
      const pedirTogether = (w, h) => fetch('https://api.together.xyz/v1/images/generations', {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + llave, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: modeloElegido || 'black-forest-labs/FLUX.1-schnell',
          prompt: promptFinal, width: w, height: h, n: 1, response_format: 'b64_json',
        }),
        signal: ctrl.signal,
      });
      let r = await pedirTogether(anchoImg, altoImg);
      if (!r.ok) {
        // Los modelos de Google (flash-image) y otros solo aceptan una lista corta de tamaños
        // y, si el pedido no esta en ella, contestan con la lista completa ("Supported values
        // are: '1024x1024', '864x1184'...") en vez de ajustarlo. No se adivina a mano por
        // modelo: se lee la lista de la propia respuesta y se repite UNA vez con el tamaño de
        // proporcion mas parecida. El intento fallido no cuesta: se rechaza antes de generar.
        const txt0 = await r.text().catch(() => '');
        const aceptados = tamanosAceptados(txt0);
        if (aceptados.length) {
          const tam = tamanoMasCercano(aceptados, anchoImg, altoImg);
          r = await pedirTogether(tam[0], tam[1]);
        } else {
          r = new Response(txt0, { status: r.status });
        }
      }
      if (!r.ok) {
        const txt = await r.text().catch(() => '');
        let msg = txt.slice(0, 200);
        try { msg = JSON.parse(txt).error.message || msg; } catch {}
        if (r.status === 429) return { ok: false, error: 'Límite de peticiones, espera un momento' };
        // Together mueve modelos de "serverless" a "dedicado" sin avisar, y cuando pasa
        // contesta en ingles con un enlace para crear un endpoint dedicado — que cuesta
        //
        // Ya paso una vez con FLUX.1-schnell-Free, que desaparecio del catalogo de un dia
        // para otro. Por eso no se corrige a mano: el modelo caido se anota, el selector
        // deja de ofrecerlo, y aqui se reintenta una sola vez con el siguiente disponible.
        if (esNoServerless(msg)) {
          const caido = modeloElegido || 'black-forest-labs/FLUX.1-schnell';
          await anotarCaido(env, caido);
          if (!opt.yaReintento) {
            const alterno = await otroModelo(env, llave, caido);
            if (alterno) {
              const segundo = await generarImagenInterna(env, prompt,
                Object.assign({}, opt, { modelo: alterno, yaReintento: true }));
              if (segundo.ok) return Object.assign(segundo, { cambioDeModelo: caido });
            }
          }
          return { ok: false, error: 'El proveedor dejó de servir "' + caido + '" sin endpoint dedicado. ' +
            'Ya no aparecerá en la lista: entra a APIs → Proveedor de imágenes y elige otro modelo.' };
        }
        return { ok: false, error: msg };
      }
      const j = await r.json();
      const b64 = j.data && j.data[0] && j.data[0].b64_json;
      return b64 ? { ok: true, base64: b64, modelo: modeloElegido } : { ok: false, error: 'No devolvió imagen' };
    }

    if (imgCfg.tipo === 'replicate') {
      const modelo = modeloElegido || 'black-forest-labs/flux-schnell';
      // Cada familia de modelos pide el tamaño a su manera. Flux acepta width/height;
      // gpt-image y otros los rechazan con un 422 de validacion y solo entienden una
      // relacion de aspecto. Como el catalogo cambia sin avisar -y el selector de portada
      // invita justo a elegir un modelo de otra familia-, no se adivina: se intenta con
      // medidas y, si el proveedor las rechaza, se repite sin ellas. Un intento de mas solo
      // ocurre cuando el primero fallo, y falla gratis: Replicate no cobra un 422.
      const pedir = (input) => fetch(`https://api.replicate.com/v1/models/${modelo}/predictions`, {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + llave, 'Content-Type': 'application/json', Prefer: 'wait' },
        body: JSON.stringify({ input }),
        signal: ctrl.signal,
      });
      let r = await pedir({ prompt: promptFinal, width: anchoImg, height: altoImg });
      if (r.status === 422 || r.status === 400) {
        // El modelo que no entienda aspect_ratio tampoco se rompe por recibirlo de mas en
        // este segundo intento: si vuelve a fallar, el error que se enseña es el suyo.
        r = await pedir({ prompt: promptFinal, aspect_ratio: aspectoImg });
      }
      if (!r.ok) {
        const txt = (await r.text().catch(() => '')).slice(0, 200);
        return { ok: false, error: 'Replicate (' + modelo + '): ' + txt };
      }
      const j = await r.json();
      const url = j.status === 'succeeded' && j.output && (Array.isArray(j.output) ? j.output[0] : j.output);
      if (!url) return { ok: false, error: 'Replicate: ' + (j.error || j.status || 'sin imagen') };
      const buf = await (await fetch(url)).arrayBuffer();
      // En trozos: String.fromCharCode con 300k argumentos de golpe revienta la pila.
      const bytes = new Uint8Array(buf);
      let s = '';
      for (let i = 0; i < bytes.length; i += 8192) {
        s += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
      }
      return { ok: true, base64: btoa(s), modelo: modelo };
    }

    if (imgCfg.tipo === 'compatible') {
      if (!imgCfg.url) return { ok: false, error: 'Falta la URL del endpoint de imágenes.' };
      // Mismo contrato que /v1/images/generations de OpenAI: lo hablan Together, muchas
      // "compatibles con OpenAI" y no hace falta escribirles un adaptador aparte.
      const r = await fetch(imgCfg.url, {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + llave, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: modeloElegido || undefined, prompt: promptFinal, n: 1,
          size: anchoImg + 'x' + altoImg, response_format: 'b64_json',
        }),
        signal: ctrl.signal,
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => '');
        let msg = txt.slice(0, 200);
        try { msg = JSON.parse(txt).error?.message || msg; } catch {}
        return { ok: false, error: imgCfg.url + ' contestó ' + r.status + (msg ? ': ' + msg : '') };
      }
      const j = await r.json();
      const b64 = j.data && j.data[0] && j.data[0].b64_json;
      return b64 ? { ok: true, base64: b64, modelo: modeloElegido } : { ok: false, error: 'No devolvió imagen' };
    }

    if (imgCfg.tipo === 'apimart') {
      // APIMart NO habla el contrato de OpenAI: pide la relacion de aspecto ("3:4") y no
      // el tamano en pixeles, y no devuelve la imagen sino un task_id que hay que consultar
      // hasta que termine. Por eso "Otra (pegar URL propia)" no le sirve y tiene su propio
      // tipo: el envio devuelve {data:[{task_id}]} y la tarea, en /v1/tasks/{id}, pasa por
      // pending -> processing -> completed con la URL en result.images[0].url[0].
      if (!modeloElegido) {
        return { ok: false, error: 'APIMart: falta el modelo. Escríbelo en APIs → Imagen (por ejemplo gemini-3.1-flash-lite-image-ext).' };
      }
      const base = 'https://api.apimart.ai/v1';
      const cab = { Authorization: 'Bearer ' + llave, 'Content-Type': 'application/json' };
      const mensajeDe = (txt) => {
        let m = String(txt || '').slice(0, 200);
        try { const e = JSON.parse(txt).error; if (e && e.message) m = e.message; } catch {}
        return m;
      };
      // Una tarea que ya existe se RETOMA sin pedir otra: la peticion anterior se canso de esperar y la tarea siguio en el proveedor (y se cobra al terminar). Consultarla no cuesta.
      let tarea = (typeof opt.tarea === 'string' && /^[\w-]{4,80}$/.test(opt.tarea)) ? opt.tarea : null;
      if (!tarea) {
        // 429 = «demasiados pedidos a la vez»: el creador dibuja varias hojas a la vez (EbookProduccion.PARALELO) y varias personas pueden estar produciendo. Ese envio no
        // crea tarea ni se cobra: se espera un poco (3 s y 6 s) y se manda otra vez. Si sigue, sale el 429 y el navegador lo repite mas tarde (motivo «saturado»).
        let r, txtEnvio = '';
        for (let intento = 0; intento < 3; intento++) {
          if (intento) await new Promise((res) => setTimeout(res, 3000 * intento));
          r = await fetch(base + '/images/generations', {
            method: 'POST', headers: cab, signal: ctrl.signal,
            body: JSON.stringify({ model: modeloElegido, prompt: promptFinal, size: aspectoApimart(anchoImg, altoImg, opt.aspecto), n: 1 }),
          });
          txtEnvio = await r.text().catch(() => '');
          if (r.status !== 429) break;
        }
        if (!r.ok) return { ok: false, error: 'APIMart (' + modeloElegido + ') contestó ' + r.status + ': ' + mensajeDe(txtEnvio) };
        let envio = {};
        try { envio = JSON.parse(txtEnvio); } catch {}
        tarea = envio.data && envio.data[0] && envio.data[0].task_id;
        if (!tarea) return { ok: false, error: 'APIMart no devolvió la tarea: ' + txtEnvio.slice(0, 200) };
      }
      tareaPendiente = tarea;

      // La primera consulta espera un poco: casi nunca esta lista antes (una tarea retomada ya lleva rato: se mira enseguida). El tope general lo pone el AbortController (TIMEOUT).
      for (let i = 0; i < 40; i++) {
        await new Promise((res) => setTimeout(res, i === 0 ? (opt.tarea ? 1000 : 4000) : 3000));
        const q = await fetch(base + '/tasks/' + encodeURIComponent(tarea) + '?language=es', {
          headers: { Authorization: 'Bearer ' + llave }, signal: ctrl.signal,
        });
        const txtTarea = await q.text().catch(() => '');
        if (!q.ok) {
          if (q.status === 429 || q.status >= 500) continue;   // un tropiezo del servicio no tumba la imagen
          return { ok: false, error: 'APIMart (consulta ' + q.status + '): ' + mensajeDe(txtTarea) };
        }
        let d = {};
        try { d = JSON.parse(txtTarea).data || {}; } catch {}
        if (d.status === 'failed' || d.status === 'cancelled') {
          return { ok: false, error: 'APIMart (' + modeloElegido + '): ' + ((d.error && d.error.message) || d.status) };
        }
        if (d.status !== 'completed') continue;
        const im = d.result && d.result.images && d.result.images[0];
        const url = im && (Array.isArray(im.url) ? im.url[0] : im.url);
        if (!url) return { ok: false, error: 'APIMart terminó pero no trajo la imagen' };
        const buf = await (await fetch(url, { signal: ctrl.signal })).arrayBuffer();
        const bytes = new Uint8Array(buf);
        let s = '';
        for (let k = 0; k < bytes.length; k += 8192) {
          s += String.fromCharCode.apply(null, bytes.subarray(k, k + 8192));
        }
        // data.cost = lo que APIMart cobro por esta imagen, en USD. Un valor fuera de lo razonable
        const costo = Number(d.cost);
        // urlRemota: el enlace publico de la imagen en el proveedor. Sirve para pedirle a un modelo de texto que la LEA (verificar el texto de una pagina dibujada) sin mandarle los MB en base64.
        return { ok: true, base64: btoa(s), modelo: modeloElegido, costo: (costo > 0 && costo <= 5) ? costo : null, urlRemota: /^https:\/\//i.test(url) ? url : null };
      }
      return { ok: false, error: 'APIMart no terminó la imagen a tiempo', tareaImagen: tarea };
    }

    return { ok: false, error: 'Proveedor de imágenes no reconocido: ' + imgCfg.tipo };
  } catch (e) {
    if (e.name === 'AbortError') return { ok: false, error: 'El proveedor de imágenes no respondió en ' + Math.round(TIMEOUT / 1000) + ' s', ...(tareaPendiente ? { tareaImagen: tareaPendiente } : {}) };
    return { ok: false, error: e.message || String(e) };
  } finally {
    clearTimeout(t);
  }
}

async function promptDeFoto(prov, llave, pagina, estiloFoto) {
  const esReceta = esPaginaDeReceta(pagina);
  const r = await llamarLLM(prov, llave,
    'Generas descripciones de fotos para un generador de imagenes. Escribe SOLO la descripcion, en ingles, en una o dos frases.',
    esReceta
      ? `Describe la foto del PLATO YA TERMINADO de esta receta, como para una revista de cocina:
Receta: ${pagina.titulo || ''}
Ingredientes: ${(pagina.ingredientes || []).slice(0, 5).join(', ')}

Describe el plato servido y listo para comer: como se ve, en que se sirve, la guarnicion y el color.
Un solo plato, centrado, fondo limpio. Sin texto, sin letras, sin numeros.`
      : `Describe una foto que ilustre esta seccion de un entregable, sin texto ni letras:
Seccion: ${pagina.titulo || ''}
De que trata: ${(pagina.texto || '').slice(0, 300)}

Un solo sujeto, centrado, fondo limpio, luz suave.`,
    { maxTokens: 300, temperatura: 0.5 });

  const base = r.ok ? r.texto.trim() : fotoFija(pagina);
  // El estilo compartido va en TODAS las paginas. Sin el, veinte fotos generadas por
  // separado salen cada una de un ebook distinto -otra luz, otra vajilla, otro fondo- y el
  // conjunto se ve armado a pedazos aunque cada foto suelta este bien.
  return estiloFoto ? base + '. ' + estiloFoto : base;
}

// ---------------------------------------------------------------------------------------------------------------------------
// La MAQUETACION de la pagina ya no la hace un modelo: la hace functions/_ebook-maqueta.js, que recibe los campos de la pagina del plan y arma el
// HTML con el tamaño de letra en que todo cabe. Cero llamadas de texto, el mismo resultado para el mismo texto, y nada se pierde ni se sale de la hoja.
// (Antes eran dos llamadas de texto por pagina -la frase de la foto y el maquetador- y juntas eran ~60-70 % de los tokens de un ebook.)
function maquetar(pagina) {
  try {
    const r = maquetarPagina(pagina, { tipoEntregable: pagina.tipoEntregable, colorPrimario: pagina.colorPrimario, colorSecundario: pagina.colorSecundario });
    // Solo se le avisa a la persona de lo que es un problema de verdad: letra de 13 px o menos, o texto que no cabe. Una foto mas baja para que quepa
    // un texto largo con letra normal es parte del diseño, no un error: se ve en la hoja y avisar en rojo cada vez seria ruido.
    const problema = r.ajuste.nivel === 'minimo' || r.ajuste.nivel === 'desborde';
    return { ok: true, html: r.html, aviso: problema ? (r.aviso || '') : '' };
  } catch (e) {
    return { ok: false, error: 'Maquetacion: ' + String((e && e.message) || e).slice(0, 200) };
  }
}

// La descripcion de la foto de una pagina, SIN gastar IA siempre que se pueda.
//
// Cada pagina le pedia a un modelo una frase en ingles para el generador de imagenes (otra llamada de texto por pagina). Ahora esa frase viene ya
// escrita en el plan (campo "foto", ver ebook-plan.js). Si no viene -un proyecto guardado antes de que existiera el campo, o una pagina que se rehace
// con «otra foto» sin descripcion guardada- se arma por plantilla con el titulo y el contenido de la pagina. El modelo de texto (promptDeFoto) queda de
// ULTIMO recurso: una pagina sin foto, sin titulo y sin contenido, que es cuando no hay con que armar nada.
const unaLinea = (v, max) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '').replace(/\s+/g, ' ').trim().slice(0, max);
const conEstilo = (base, estilo) => (estilo ? base + '. ' + estilo : base);
const fotoLimpia = (v) => unaLinea(v, 300);

// «2 tazas de harina de maiz» -> «harina de maiz»: la cantidad no dice como se ve el plato.
function sinCantidad(x) {
  return unaLinea(x, 120).replace(/^[\d\s\/.,½¼¾-]*(?:(?:tazas?|cucharad(?:a|as|ita|itas)|cdas?|cdtas?|gramos?|gr|g|kg|ml|l|litros?|unidades?|dientes?|pizca|lata|latas|rebanadas?|hojas?)\b\.?\s*)?(?:de\s+)?/i, '');
}

// Los primeros ingredientes, sin cantidades. Acepta una lista o un texto con un ingrediente por linea, igual que la maqueta (que es la que decide si la
// pagina es una receta: ver esPaginaDeReceta).
function nombresDeIngredientes(pagina) {
  const a = typeof pagina.ingredientes === 'string' ? pagina.ingredientes.split('\n') : pagina.ingredientes;
  return (Array.isArray(a) ? a : []).slice(0, 60).map(sinCantidad).filter(Boolean).slice(0, 5);
}

// La descripcion fija de ULTIMO recurso (el modelo de texto fallo, o no hay, y la pagina no trae nada con que armar una): una receta pide un plato; cualquier
// otra pagina, una foto sin comida. Una pagina sin ingredientes nunca lleva «plated dish», en ningun camino (lo vigila probar-ebook-sin-ia.mjs).
function fotoFija(pagina) {
  const titulo = unaLinea(pagina && pagina.titulo, 120);
  return esPaginaDeReceta(pagina)
    ? 'the finished plated dish of ' + (titulo || 'a healthy meal') + ', centered on a clean background'
    : 'a clean editorial photograph that evokes ' + (titulo ? '"' + titulo + '"' : 'the topic of this ebook') + ', a single subject centered on a clean background';
}

// El sufijo que generarImagen le pega a la foto: el del plato servido SOLO si la pagina es una receta. La portada y el cierre describen su encuadre enteros
// (promptDePortada y el prompt del cierre), asi que no llevan nada -tampoco cuando se rehace solo su foto con «otra foto»: ahi se les pegaba el plato servido-.
const encuadreDe = (pagina) => (pagina.portada || pagina.cierre ? 'propio' : esPaginaDeReceta(pagina) ? 'plato' : 'neutro');

function promptDeFotoPlantilla(pagina, estilo) {
  const titulo = unaLinea(pagina.titulo, 120);
  let base;
  if (esPaginaDeReceta(pagina)) {
    const ingredientes = nombresDeIngredientes(pagina);
    base = 'Professional food photograph of the finished dish' + (titulo ? ' "' + titulo + '"' : '') + ', plated and ready to eat' +
      (ingredientes.length ? ', made with ' + ingredientes.join(', ') : '') + '. One single dish, centered, clean background, appetizing';
  } else {
    const idea = unaLinea(Array.isArray(pagina.texto) ? pagina.texto.join(' ') : pagina.texto, 160);
    if (!titulo && !idea) return '';
    base = 'Editorial photograph that illustrates "' + (titulo || idea) + '"' + (titulo && idea ? ': ' + idea : '') + '. A single subject, centered, clean uncluttered background, soft natural light';
  }
  return conEstilo(base, estilo);
}

// textoListo: funcion que resuelve el proveedor de texto la primera vez que alguien lo pide (casi nunca hace falta: ver arriba).
async function promptDePagina(textoListo, pagina) {
  const estilo = unaLinea(pagina.estiloFoto, 600);
  const foto = fotoLimpia(pagina.foto);
  if (foto) return conEstilo(foto, estilo);
  const plantilla = promptDeFotoPlantilla(pagina, estilo);
  if (plantilla) return plantilla;
  const t = await textoListo();
  if (t.error) return conEstilo(fotoFija(pagina), estilo);
  return promptDeFoto(t.prov, t.llave, pagina, estilo);
}

// El modelo con el que se hace la portada. Si no hay uno elegido, el mismo de las paginas:
// nunca se rompe por no estar configurado, solo deja de mejorar.
async function modeloPortada(env) {
  try {
    const raw = await env.SUITE.get('ajustes');
    const a = raw ? JSON.parse(raw) : {};
    return (a.imagenApi && a.imagenApi.modeloPortada) || null;
  } catch { return null; }
}

// El prompt de la portada, entero y sin sufijos ajenos.
//
// Describe una PORTADA, no una foto: el sujeto arriba, aire abajo para el titulo, y el
// encuadre vertical que pide una tapa de libro. Antes esto se mezclaba con el sufijo de
// comida y salia un plato centrado ocupando todo, que es exactamente lo contrario.
function promptDePortada(brief, estiloFoto) {
  const tipo = String(brief.tipoEntregable || '').toLowerCase();
  const escena = tipo.includes('recet')
    ? 'An elegant overhead or 45-degree food scene with beautiful ingredients and finished dishes, styled like a premium cookbook cover'
    : tipo.includes('devocional')
      ? 'A serene, warm, contemplative scene with natural light, styled like a premium devotional book cover'
      : 'A clean, aspirational editorial scene that evokes the topic, styled like a premium non-fiction book cover';
  // El publico y el angulo son lo unico del brief que cambia de una oferta a otra sin
  // depender del tipo: sin ellos, dos ofertas del mismo tipo -"otro"- pedian el mismo
  // prompt generico y salian con la misma composicion de fondo, solo con el titulo
  // distinto. Con esto el modelo tiene algo propio de ESTA oferta que fotografiar.
  const quien = String(brief.publico || '').slice(0, 100);
  const angulo = String(brief.angulo || '').slice(0, 150);
  return 'Premium book cover photograph for "' + String(brief.titulo || '').slice(0, 120) + '". ' +
    String(brief.promesa || '').slice(0, 200) + '. ' +
    (quien ? 'Made for: ' + quien + '. ' : '') +
    (angulo ? 'Core idea to convey visually: ' + angulo + '. ' : '') +
    escena + '. ' +
    'VERTICAL 2:3 book cover composition: the subject occupies the TOP TWO THIRDS, and the ' +
    'BOTTOM THIRD is calm, darker and uncluttered so a title can be laid over it. ' +
    'Rich depth, cinematic directional lighting, deep saturated colors, sharp focus on the subject, ' +
    'shot on medium format, editorial magazine cover quality, professional art direction, ' +
    'the kind of cover art directed by a top publishing house for a bestseller — distinctive ' +
    'and specific to this exact topic, not a generic interchangeable stock photo. ' +
    'No text, no letters, no words, no numbers, no logos, no watermarks, no hands, no faces.' +
    (estiloFoto ? ' ' + estiloFoto : '');
}

// La portada: fondo generado y el titulo puesto por nosotros, en HTML.
//
// El titulo NO se le pide al modelo de imagen aunque sepa escribir. Qwen dibuja letras
// limpias, pero es un dado: una tilde de mas en la portada es un producto quemado que ya
// entregaste, y en la modalidad de pagar-despues la portada es lo primero que ve alguien
// que todavia no ha pagado. En HTML el texto sale nitido siempre, correcto siempre, y se
// corrige sin volver a pagar una imagen.
function portada(brief, colorPrimario, colorSecundario) {
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  // Los colores llegan en la peticion y van DENTRO de un <style>: solo vale #rgb o #rrggbb (cualquier otra cosa -un `;}</style><script>`...- cae al color por
  // omision). Se valida aqui, en la funcion que escribe el CSS, para que ningun camino (portada nueva, «solo texto», lo que venga) pueda saltarselo.
  const c1 = colorSeguro(colorPrimario, '#e91e63'), c2 = colorSeguro(colorSecundario, '#2d7a3e');
  const titulo = esc(brief.titulo || 'Sin título');
  const promesa = esc(brief.promesa || '');
  // Un titulo de 20 caracteres y uno de 90 no pueden ir al mismo cuerpo: el largo manda.
  const cuerpoTitulo = titulo.length > 62 ? 42 : titulo.length > 38 ? 50 : 60;
  return `<style>
  .pt { position:absolute; inset:0; width:${ANCHO}px; height:${ALTO}px; overflow:hidden; background:#111;
        font-family:ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif; }
  .pt img { position:absolute!important; inset:0!important; width:${ANCHO}px!important;
            height:${ALTO}px!important; object-fit:cover!important; display:block!important; }
  /* Un velo de abajo hacia arriba: la foto se ve, y el texto se lee sobre ella pase lo que
     pase con la imagen que devuelva el modelo. Sin esto, un fondo claro se come el titulo. */
  /* Mas imagen que letra: el velo arranca mas abajo y mas suave, asi la foto se ve casi
     entera y el texto se apoya en el tercio inferior, que es justo el que el prompt pide
     despejado. Antes el degradado empezaba a oscurecer desde media pagina y la portada
     parecia un cartel con una foto detras. */
  .pt .velo { position:absolute; inset:0;
        background:linear-gradient(to top, rgba(0,0,0,.90) 0%, rgba(0,0,0,.62) 26%, rgba(0,0,0,.10) 52%, rgba(0,0,0,0) 72%, rgba(0,0,0,.28) 100%); }
  .pt .txt { position:absolute; left:64px; right:64px; bottom:80px; color:#fff; }
  .pt .filo { width:72px; height:5px; border-radius:3px; background:${c1}; margin-bottom:22px; }
  .pt h1 { margin:0 0 16px; font-size:${cuerpoTitulo}px; line-height:1.06; font-weight:800;
           letter-spacing:-1px; text-wrap:balance; text-shadow:0 2px 18px rgba(0,0,0,.55); }
  .pt p { margin:0; font-size:19px; line-height:1.4; color:#e9e9ef; max-width:78%;
          text-shadow:0 1px 10px rgba(0,0,0,.6); }
  .pt .sello { position:absolute; top:60px; left:64px; font-size:12.5px; letter-spacing:3.2px;
               text-transform:uppercase; color:#fff; opacity:.92; border:1.5px solid ${c2};
               border-radius:999px; padding:8px 16px; }
</style>
<div class="pt">
  <img src="__FOTO__" alt="">
  <div class="velo"></div>
  <div class="sello">${esc(brief.tipoEntregable || 'Guía')}</div>
  <div class="txt">
    <div class="filo"></div>
    <h1>${titulo}</h1>
    ${promesa ? '<p>' + promesa + '</p>' : ''}
  </div>
</div>`;
}

// La ultima pagina: agradecer, recordar lo que la persona acaba de recibir, y -si hay
// datos de pago- decirle como pagar.
//
// En la modalidad X1 el contenido se entrega antes de cobrar, asi que esta es la unica
// pagina del producto que le habla a alguien que ya leyo todo, ya vio la calidad y
// todavia no ha pagado. Sin ella el ebook termina en la ultima receta y ese momento -el
// unico en que la persona esta convencida y con el archivo abierto- se pierde entero.
//
// El bloque de pago es OPCIONAL. Antes, sin datos de pago no se generaba la pagina: el
// ebook terminaba de golpe en la ultima receta aunque el estimador ya hubiera cobrado
// "portada y cierre incluidos". Un entregable que no se cobra -un gratuito, un bono- sigue
// necesitando su ultima pagina; lo que sobra ahi es el recuadro de la cuenta, no el cierre.
//
// Los datos de pago se pegan TAL CUAL los escribio el usuario. El modelo redacta lo que
// rodea, nunca el numero de cuenta: una cifra alucinada aqui no es una errata, es plata
// que se va a la cuenta de otro y un cliente que cree que pago.
function cierre(brief, copy, datosPago, colorPrimario, colorSecundario) {
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  // Igual que en portada(): los colores van dentro de un <style>, asi que solo se acepta #rgb o #rrggbb.
  const c1 = colorSeguro(colorPrimario, '#e91e63'), c2 = colorSeguro(colorSecundario, '#2d7a3e');
  // Los datos de pago son varias lineas escritas a mano; se respetan los saltos.
  const pago = esc(datosPago).replace(/\r?\n/g, '<br>');
  return `<style>
  .pc { position:absolute; inset:0; width:${ANCHO}px; height:${ALTO}px; overflow:hidden; background:#fff;
        font-family:ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif; }
  .pc .foto { position:absolute; top:0; left:0; width:${ANCHO}px; height:300px; overflow:hidden; }
  .pc .foto img { width:${ANCHO}px!important; height:300px!important; object-fit:cover!important; display:block!important; }
  .pc .velo { position:absolute; top:0; left:0; width:${ANCHO}px; height:300px;
        background:linear-gradient(to bottom, rgba(0,0,0,.30) 0%, rgba(0,0,0,.62) 100%); }
  .pc .sobre { position:absolute; top:184px; left:56px; right:56px; color:#fff; }
  .pc .sobre h2 { margin:0; font-size:40px; line-height:1.1; font-weight:800; letter-spacing:-.8px; }
  .pc .cuerpo { position:absolute; top:336px; left:56px; right:56px; bottom:40px; color:#23232b; }
  .pc .cuerpo p { margin:0 0 18px; font-size:19px; line-height:1.55; }
  /* El bloque de pago es lo unico que tiene que saltar a la vista. Marco grueso del color
     de la marca y fondo propio: si la persona hojea de un vistazo, esto es lo que ve. */
  .pc .pago { border:3px solid ${c1}; border-radius:14px; padding:22px 24px;
        background:#faf7f9; margin:26px 0 20px; }
  .pc .pago .rot { font-size:12px; letter-spacing:2.6px; text-transform:uppercase;
        color:${c1}; font-weight:700; margin-bottom:12px; }
  .pc .pago .dat { font-size:22px; line-height:1.5; font-weight:700; color:#15151c; word-break:break-word; }
  .pc .nota { font-size:16.5px; line-height:1.5; color:#55555f; }
  .pc .filo { width:76px; height:5px; border-radius:3px; background:${c2}; margin:0 0 18px; }
</style>
<div class="pc">
  <div class="foto"><img src="__FOTO__" alt=""><div class="velo"></div></div>
  <div class="sobre"><h2>${esc(copy.titulo)}</h2></div>
  <div class="cuerpo">
    <div class="filo"></div>
    <p>${esc(copy.parrafo)}</p>
    ${pago ? `<div class="pago">
      <div class="rot">Para completar tu pedido</div>
      <div class="dat">${pago}</div>
    </div>` : ''}
    <div class="nota">${esc(copy.recordatorio)}</div>
  </div>
</div>`;
}

// Copy de la pagina de cierre. Tres cadenas cortas, nada mas: el resto de la pagina ya
// esta escrito. Si el modelo falla, hay un texto fijo -quedarse sin pagina de cobro por
// un timeout seria perder la venta por ahorrarse un fallback.
async function copyDeCierre(prov, llave, brief, cerebro, avatar, conPago) {
  // Sin bloque de pago la pagina no puede pedir un pago: seria mandar a pagar a un sitio
  // que no existe. Cambia el encargo entero, no solo el recuadro de abajo.
  const fijo = conPago ? {
    titulo: 'Gracias por leerlo',
    parrafo: 'Acabas de recibir ' + String(brief.titulo || 'este material') +
      ' completo. Lo hicimos para que lo uses hoy mismo, no para que quede guardado.',
    recordatorio: 'Cuando hagas el pago, escríbeme por WhatsApp con el comprobante y te confirmo enseguida.',
  } : {
    titulo: 'Gracias por leerlo',
    parrafo: 'Acabas de recibir ' + String(brief.titulo || 'este material') +
      ' completo. Lo hicimos para que lo uses hoy mismo, no para que quede guardado.',
    recordatorio: 'Empieza por lo primero que te haya llamado la atención, hoy mismo.',
  };
  // Esta pagina se escribia con el titulo y nada mas, siendo la unica del producto que le
  // habla a alguien que ya leyo todo y todavia no ha pagado. Ahora lleva como se cobra aqui
  // (cerebro) y a quien se le esta hablando (avatar), igual que el resto del ebook.
  const r = await llamarLLM(
    prov, llave,
    (conPago
      ? 'Escribes la ultima pagina de un ebook que la persona YA recibio y todavia no ha pagado.\n\n'
      : 'Escribes la ultima pagina de un ebook que la persona acaba de recibir. Este entregable NO se cobra aqui: no pidas pago, no menciones pagar, no mandes a ningun sitio a pagar.\n\n') +
    conocimientoTexto(cerebro, 'cierre') +
    (conPago
      ? '\n\nAgradeces, le recuerdas el valor concreto de lo que tiene en las manos y le pides el pago ' +
        'sin rogar y sin culpa: con la naturalidad de quien ya cumplio su parte.\n'
      : '\n\nAgradeces, le recuerdas el valor concreto de lo que tiene en las manos y la empujas a ' +
        'USARLO hoy: el cierre es para que abra el archivo otra vez, no para vender nada.\n') +
    // El precio no se le pasa y aun asi lo escribia: "el valor de este devocional es de 10
    // USD (o su equivalente en moneda local)". Un precio inventado en la pagina que pide el
    // pago es peor que no tener pagina: la persona paga de menos, o reclama, o no paga. El
    // monto y el medio salen del bloque que escribio el usuario, aparte y literal.
    'PROHIBIDO escribir cifras, precios, montos o monedas. No sabes cuanto cuesta y no te ' +
    'toca decirlo: el monto va en un bloque aparte que se pega literal. Si te falta el ' +
    'precio para que la frase funcione, cambia la frase.\n' +
    'Devuelve SOLO JSON: {"titulo":"3-5 palabras","parrafo":"2-3 frases, maximo 300 caracteres",' +
    '"recordatorio":"1 frase, maximo 140 caracteres, ' + (conPago ? 'sobre enviar el comprobante' : 'sobre por donde empezar hoy') + '"}',
    avatarTexto(avatar) +
    '\nEbook: ' + String(brief.titulo || '') + '\nPromesa: ' + String(brief.promesa || '') +
    '\nTipo: ' + String(brief.tipoEntregable || '') +
    // El "paga porque" del avatar es el argumento de esta pagina; el "no paga si" es lo que
    // tiene que desactivar. Se nombran para que no se queden de adorno en el contexto.
    (avatar && avatar.porQuePagaria
      ? '\n\nApoyate en lo que a ELLA la mueve a pagar teniendo ya el archivo, y desactiva de ' +
        'pasada su excusa para no hacerlo. Sin nombrar la excusa de frente.'
      : ''),
    { maxTokens: 500, temperatura: 0.7, timeout: TIMEOUT }
  );
  if (!r.ok) return fijo;
  // La prohibicion en el prompt no basta para algo que cuesta dinero cuando falla. Se
  // comprueba: cualquier frase con una cifra de dinero se descarta y entra la fija, que no
  // menciona precios. Prefiero un cierre mas soso que uno que anuncie un precio inventado.
  const tienePrecio = (t) => /\d\s*(usd|dolar|dólar|peso|sol|bs|quetzal|bolivar|bolívar|eur|mxn|cop|pen|gtq|ars|ves)|[$€]\s*\d|\d+\s*(mil|k)\b/i.test(t);
  const limpio = (v, alterna) => {
    const t = String(v || '').trim();
    return (!t || tienePrecio(t)) ? alterna : t;
  };
  try {
    const m = r.texto.match(/\{[\s\S]*\}/);
    const j = JSON.parse(m ? m[0] : r.texto);
    return {
      titulo: limpio(j.titulo, fijo.titulo).slice(0, 60),
      parrafo: limpio(j.parrafo, fijo.parrafo).slice(0, 400),
      recordatorio: limpio(j.recordatorio, fijo.recordatorio).slice(0, 200),
    };
  } catch { return fijo; }
}

// ---------------------------------------------------------------------------------------------------------------------------
// Las dos formas nuevas de hoja (ver functions/_ebook-pagina-ia.js): la portada con el titulo escrito DENTRO de la imagen y las paginas dibujadas enteras por la IA. En las dos el
// texto lo dibuja el modelo de imagenes y puede salir distinto del pedido, asi que despues se VERIFICA (functions/_ebook-verificar.js) y el cliente repite lo que salga mal.

// Un prompt de portada con el titulo dentro de la imagen lleva siempre esta frase (la pone promptPortadaIA): asi, rehacer solo la foto sabe de que tipo de portada se trata aunque
// el cliente no se lo diga.
const PORTADA_HORNEADA = /TEXT ON THE COVER/;

// La descripcion de la foto de una pagina para el diseño dibujado por la IA (la foto es una parte del dibujo): la frase del plan o, sin ella, una armada con el titulo y los
// ingredientes. Sin IA y sin red.
function descripcionDeFoto(p) {
  const f = fotoLimpia(p.foto);
  if (f) return f;
  const titulo = unaLinea(p.titulo, 120);
  if (esPaginaDeReceta(p)) {
    const ing = nombresDeIngredientes(p);
    return 'the finished dish' + (titulo ? ' ' + titulo : '') + ', plated and ready to eat' + (ing.length ? ', made with ' + ing.join(', ') : '');
  }
  return 'a single clear subject that illustrates ' + (titulo ? titulo : 'the topic of the page');
}

// El modelo de texto lee la imagen que acaba de salir y se compara con lo pedido. Nunca lanza ni retrasa una pagina: sin enlace de la imagen, sin proveedor de texto o con un
// proveedor que no lee imagenes por enlace, devuelve null (o { ok: null, motivo }) y la pagina se da por buena.
async function verificarImagen(textoListo, img, esperado, activo) {
  try {
    if (activo === false || !img || !img.ok || !img.urlRemota || !esperado) return null;
    const t = await textoListo();
    if (t.error) return null;
    return await verificarTexto(llamarLLM, t.prov, t.llave, img.urlRemota, esperado);
  } catch (e) {
    return null;
  }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'POST only' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  const rol = await exigirAlguno(request, env, EQUIPO);
  if (!rol) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return json(400, { ok: false, error: 'JSON invalido' });
  // Una pagina de un plan guardado antes del arreglo del lector puede traer campos metidos dentro del texto: se limpia ANTES de armar el prompt, asi la IA no dibuja codigo
  // y la lectura del texto compara contra el texto bueno (functions/_ebook-fugas.js). Portada y cierre no son paginas del plan.
  if (!body.portada && !body.cierre) rescatarFugas(body);

  // Modo lote (ver resumirGasto en functions/_gasto.js): el gasto de esta peticion se junta aqui y
  // vuelve en la respuesta, sin una escritura de KV por cada llamada al proveedor.
  const col = body.gastoLote === true ? [] : null;
  const jsonG = (s, o) => json(s, col ? { ...o, gasto: resumirGasto(col) } : o);

  // El proveedor de TEXTO ya no se necesita para armar una pagina (la maqueta es codigo y la descripcion de la foto viene del plan): solo lo piden el
  // cierre -el copy de la ultima pagina- y, de ultimo recurso, la descripcion de una foto cuando la pagina no trae nada con que armarla. Se resuelve
  // la primera vez que alguien lo pide, asi una pagina normal no lee ajustes ni llaves de texto (y la portada ya no falla por falta de un proveedor
  // de texto que nunca iba a usar).
  let textoCfg = null;
  const textoListo = async () => {
    if (textoCfg) return textoCfg;
    const rawAj = await env.SUITE.get('ajustes');
    const ajustes = rawAj ? JSON.parse(rawAj) : {};
    const prov = conEnv((ajustes.proveedores || []).find((p) => p.id === ajustes.proveedorActivo), env, col);
    if (!prov) return (textoCfg = { error: 'Configura un proveedor de texto en APIs.' });
    const llave = await env.SUITE.get('secreto:' + prov.id);
    if (!llave) return (textoCfg = { error: 'El proveedor de texto no tiene API key.' });
    return (textoCfg = { prov, llave });
  };

  // Rehacer UNA sola mitad de una pagina que ya existe.
  //
  // Cada pagina son dos cosas que se pagan por separado: una imagen y el armado del texto. Sin esto,
  // cambiar una foto que no convencio obligaba a regenerar el ebook entero -veinte imagenes- para
  //
  //   solo: 'imagen'  devuelve imagen nueva, no toca el HTML
  //   solo: 'texto'   devuelve HTML nuevo, no toca la imagen (ya no cuesta nada: la maqueta es codigo)
  //
  // promptImagen viaja de vuelta al cliente en cada generacion y puede volver editado: es la
  // diferencia entre "otra foto igual" y "otra foto, pero de perfil y sin las manos".
  const solo = body.solo === 'imagen' || body.solo === 'texto' ? body.solo : null;

  // El modelo de las paginas lo manda la pantalla, con el mismo valor con el que calculo el
  // costo. Antes se leia solo de la configuracion guardada: si el selector decia SDXL y lo
  // guardado seguia en FLUX.2-dev -porque el guardado fallo, o lo cambio otra persona-, la
  // pantalla prometia 8 centavos y el servidor cobraba 19. Con este campo, lo que se estima y
  // lo que se genera es lo mismo por construccion. Solo se acepta algo que parezca un id.
  // Elegir el modelo (cuanto cuesta cada pagina) es del dueno y del editor; los demas roles del equipo generan con el configurado. La pantalla ya les bloquea el selector, pero una peticion a mano podia
  // pedir cualquier modelo (el mas caro) con las llaves del dueno: aqui se ignora. Con el login apagado ('abierto') no hay roles y se acepta, como siempre.
  const puedeElegirModelo = rol === 'owner' || rol === 'editor';
  const modeloPaginas = puedeElegirModelo && /^[\w.\/-]{3,80}$/.test(String(body.modeloImagen || '')) ? String(body.modeloImagen) : undefined;

  // Los colores solo valen como #rgb o #rrggbb (colorSeguro): van dentro de un <style> y de un prompt.
  const c1 = colorSeguro(body.colorPrimario, '#e91e63'), c2 = colorSeguro(body.colorSecundario, '#2d7a3e');
  // verificar:false apaga la lectura del texto de la imagen (por defecto se hace). tareaImagen: la tarea de APIMart que una peticion anterior no alcanzo a recibir; se retoma sin pagar otra.
  const verificar = body.verificar !== false;
  const tareaImagen = typeof body.tareaImagen === 'string' ? body.tareaImagen : undefined;
  const pendiente = (img) => (img && img.tareaImagen ? { tareaImagen: img.tareaImagen } : {});
  // diseno 'ia': la pagina entera la dibuja el modelo de imagenes con el texto adentro; con otra cosa, el diseño de codigo de siempre (maqueta + foto). hojaIA: lo que hay HOY en esa hoja
  // (el cliente lo sabe por su HTML): solo asi un «otra foto» sabe si tiene que devolver el HTML nuevo (la hoja cambia de tipo) o dejar el que hay (que puede llevar texto editado a mano).
  const hojaIA = body.hojaIA === true;

  if (solo === 'imagen') {
    const especial = !!(body.portada || body.cierre);
    let prompt = String(body.promptImagen || '').trim();
    let ia = null;      // pagina dibujada por la IA: { prompt, variante, esperado }
    if (!especial && body.diseno === 'ia') {
      const m = modeloDe(body);
      if (cabeEnIA(m)) {
        ia = promptPaginaIA(m, { tipoEntregable: body.tipoEntregable, colorPrimario: c1, colorSecundario: c2, foto: descripcionDeFoto(body), estiloFoto: body.estiloFoto });
        // La descripcion guardada de una pagina cuyo plan traia codigo metido en el texto lleva ese codigo: volver a mandarla lo dibujaria otra vez. Se arma de nuevo con el plan limpio.
        if (!prompt || promptConFuga(prompt)) prompt = ia.prompt;
      }
    }
    if (!prompt) {
      // Sin prompt guardado -paginas de antes de que esto existiera- se vuelve a deducir: de la frase del plan, o por plantilla (sin IA).
      if (especial) {
        return jsonG(200, { ok: false, error: 'Esta página no guardó su descripción de foto. Regenera el ebook para que la tenga.' });
      }
      prompt = await promptDePagina(textoListo, body);
    }
    // horneada: la imagen trae el texto escrito adentro (portada con el titulo dentro, o pagina dibujada por la IA)
    const horneada = body.portada ? PORTADA_HORNEADA.test(prompt) : !!ia;
    const img = await generarImagen(env, prompt, { encuadre: especial || ia ? 'propio' : encuadreDe(body), modelo: modeloPaginas, colector: col, tarea: tareaImagen });
    if (!img.ok) return jsonG(200, { ok: false, error: img.error, ...pendiente(img) });
    const r = { ok: true, imagen: img.base64, promptImagen: prompt, modeloImagen: img.modelo || null, cambioDeModelo: img.cambioDeModelo || null };
    if (horneada) r.verificacion = await verificarImagen(textoListo, img, body.portada ? esperadoPortada(body.brief) : ia.esperado, verificar);
    // El HTML nuevo solo viaja cuando la hoja cambia de tipo («foto + texto» a «imagen entera» o al reves): si no cambia, se deja el que hay.
    if (body.portada) {
      if (horneada !== hojaIA) r.html = horneada ? htmlPaginaIA() : portada(body.brief || {}, c1, c2);
    } else if (!especial && !!ia !== hojaIA) {
      if (ia) r.html = htmlPaginaIA();
      else { const mq = maquetar(body); if (mq.ok) r.html = mq.html; }
    }
    return jsonG(200, r);
  }

  if (solo === 'texto') {
    if (body.portada) {
      const b = body.brief || {};
      // Una portada con el titulo dentro de la imagen no tiene texto que rearmar: se queda como esta.
      if (hojaIA) return jsonG(200, { ok: true, html: htmlPaginaIA(), aviso: 'El título de esta portada está dentro de la imagen: para cambiarlo hay que dibujarla de nuevo con «Otra foto».' });
      return jsonG(200, {
        ok: true,
        html: portada(b, c1, c2),
      });
    }
    if (body.cierre) {
      const b = body.brief || {};
      const datosPago = String(body.datosPago || '').trim();
      const t = await textoListo();
      if (t.error) return jsonG(200, { ok: false, error: t.error });
      const copy = await copyDeCierre(t.prov, t.llave, b, await leerConocimiento(env), body.avatar, !!datosPago);
      return jsonG(200, {
        ok: true,
        html: cierre(b, copy, datosPago.slice(0, 300),
          body.colorPrimario || '#e91e63', body.colorSecundario || '#2d7a3e'),
      });
    }
    // Una pagina que es una imagen entera no tiene texto que rearmar: para rehacerla se dibuja de nuevo (el cliente usa «solo imagen» sin prompt).
    if (hojaIA && body.diseno === 'ia') return jsonG(200, { ok: true, html: htmlPaginaIA(), aviso: 'Esta página es una imagen dibujada por la IA: para rehacerla con el texto del plan hay que dibujarla de nuevo.' });
    const mq = maquetar(body);
    if (!mq.ok) return jsonG(200, { ok: false, error: mq.error });
    return jsonG(200, { ok: true, html: mq.html, ...(mq.aviso ? { aviso: mq.aviso } : {}) });
  }

  // La portada. Por defecto (tituloEnImagen distinto de false) el titulo va ESCRITO DENTRO de la imagen, como el «Menu especial» de ejemplo del dueño: el modelo dibuja la
  // tipografia integrada con la foto. Si la imagen no llega, la hoja sale con el titulo en HTML sobre un fondo (para no quedar en blanco) y el cliente la vuelve a pedir.
  // Con tituloEnImagen:false se hace como antes: una foto sin letras y el titulo puesto por nosotros en HTML (siempre exacto).
  if (body.portada) {
    const b = body.brief || {};
    const enImagen = body.tituloEnImagen !== false;
    const prompt = enImagen ? promptPortadaIA(b, body.estiloFoto, c1, c2) : promptDePortada(b, body.estiloFoto);
    // La portada se lleva el modelo bueno y las paginas el barato. Es UNA imagen por ebook
    // contra treinta, asi que subir de calidad aqui cuesta centavos, y es la unica que
    // alguien mira antes de decidir si el producto se ve serio.
    const img = await generarImagen(env, prompt, { encuadre: 'propio', modelo: await modeloPortada(env), colector: col, tarea: tareaImagen });
    const horneada = enImagen && img.ok;
    return jsonG(200, {
      ok: true,
      imagen: img.ok ? img.base64 : null,
      imagenError: img.ok ? null : img.error,
      // El modelo REAL con el que salio la imagen: si el elegido cayo, este es el que
      // realmente se llamo, no el que estaba configurado. Sin esto no habia forma de saber
      // -ni el usuario ni nosotros- que se estaba pagando y usando otro modelo.
      modeloImagen: img.ok ? (img.modelo || null) : null,
      cambioDeModelo: img.ok ? (img.cambioDeModelo || null) : null,
      html: horneada ? htmlPaginaIA() : portada(b, c1, c2),
      promptImagen: prompt,
      tituloEnImagen: horneada,
      verificacion: horneada ? await verificarImagen(textoListo, img, esperadoPortada(b), verificar) : null,
      ...pendiente(img),
    });
  }

  // La pagina de cierre: una imagen y una llamada de texto corta. Sin datos de pago no
  // tiene sentido generarla -seria una pagina que agradece y no dice donde pagar-, asi que
  // se rechaza aqui y el cliente avisa en vez de gastar una imagen en nada.
  if (body.cierre) {
    const b = body.brief || {};
    const datosPago = String(body.datosPago || '').trim();
    const t = await textoListo();
    if (t.error) return jsonG(200, { ok: false, error: t.error });
    const prompt = 'Warm closing image for the ebook "' + String(b.titulo || '').slice(0, 120) + '". ' +
      'A calm, satisfying scene suggesting completion and gratitude, related to the topic. ' +
      'Horizontal composition, subject centered, soft cinematic light, editorial quality. ' +
      'No text, no letters, no words, no numbers, no watermarks.' +
      (body.estiloFoto ? ' ' + body.estiloFoto : '');
    const img = await generarImagen(env, prompt, { encuadre: 'propio', modelo: modeloPaginas, colector: col });
    const copy = await copyDeCierre(t.prov, t.llave, b, await leerConocimiento(env), body.avatar, !!datosPago);
    return jsonG(200, {
      ok: true,
      imagen: img.ok ? img.base64 : null,
      imagenError: img.ok ? null : img.error,
      modeloImagen: img.ok ? (img.modelo || null) : null,
      cambioDeModelo: img.ok ? (img.cambioDeModelo || null) : null,
      // 300 caracteres son unas 8 lineas de datos bancarios, y medido en la hoja real
      // todavia sobran ~100 px. Mas alla de ahi el bloque de pago empuja el recordatorio
      // fuera de la pagina, que es justo el texto que pide el comprobante.
      html: cierre(b, copy, datosPago.slice(0, 300),
        body.colorPrimario || '#e91e63', body.colorSecundario || '#2d7a3e'),
      promptImagen: prompt,
    });
  }

  // Una pagina normal.
  const m0 = modeloDe(body);
  const pideIA = body.diseno === 'ia';
  if (pideIA && cabeEnIA(m0)) {
    // Dibujada entera por la IA con el texto adentro (functions/_ebook-pagina-ia.js): una imagen por pagina, el mismo costo que la foto sola del diseño de codigo.
    const ia = promptPaginaIA(m0, { tipoEntregable: body.tipoEntregable, colorPrimario: c1, colorSecundario: c2, foto: descripcionDeFoto(body), estiloFoto: body.estiloFoto });
    const imgIA = await generarImagen(env, ia.prompt, { encuadre: 'propio', modelo: modeloPaginas, colector: col, tarea: tareaImagen });
    if (imgIA.ok) {
      return jsonG(200, {
        ok: true, imagen: imgIA.base64, imagenError: null,
        modeloImagen: imgIA.modelo || null, cambioDeModelo: imgIA.cambioDeModelo || null,
        html: htmlPaginaIA(), promptImagen: ia.prompt, modo: 'ia', variante: ia.variante,
        verificacion: await verificarImagen(textoListo, imgIA, ia.esperado, verificar),
      });
    }
    // La imagen no llego: la pagina sale con el diseño de codigo (el texto exacto, sin foto) para no dejar una hoja en blanco, y el cliente la vuelve a pedir
    // (con la tarea pendiente, si la hay: no se paga otra).
    const mqIA = maquetar(body);
    if (!mqIA.ok) return jsonG(200, { ok: false, error: mqIA.error });
    return jsonG(200, { ok: true, imagen: null, imagenError: imgIA.error, modeloImagen: null, cambioDeModelo: null, html: mqIA.html, promptImagen: ia.prompt, modo: 'codigo', ...pendiente(imgIA) });
  }

  // Una pagina con el diseño de codigo. Primero la maqueta (es instantanea y no cuesta nada): si fallara, no se paga una imagen para nada.
  // La descripcion de la foto sale del plan (campo "foto"); si no viene, de una plantilla con el titulo y el contenido (ver promptDePagina).
  const mq = maquetar(body);
  if (!mq.ok) return jsonG(200, { ok: false, error: mq.error });
  const promptImg = await promptDePagina(textoListo, body);
  const img = await generarImagen(env, promptImg, { encuadre: encuadreDe(body), modelo: modeloPaginas, colector: col, tarea: tareaImagen });
  // Pidio el diseño de la IA pero la pagina trae demasiado texto para una imagen: se arma con el de codigo y se le dice.
  const avisoIA = pideIA ? 'Esta página tiene mucho texto para dibujarla con IA: se armó con el diseño de código.' : '';
  const aviso = [mq.aviso, avisoIA].filter(Boolean).join(' ');

  return jsonG(200, {
    ok: true,
    imagen: img.ok ? img.base64 : null,
    imagenError: img.ok ? null : img.error,
    modeloImagen: img.ok ? (img.modelo || null) : null,
    cambioDeModelo: img.ok ? (img.cambioDeModelo || null) : null,
    html: mq.html,
    // Solo cuando la pagina tiene tanto texto que hubo que achicar la letra o la foto: la pantalla se lo dice a quien arma el ebook.
    ...(aviso ? { aviso } : {}),
    promptImagen: promptImg,
    ...pendiente(img),
  });
}
