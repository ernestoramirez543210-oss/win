// Las paginas ya generadas -HTML + la foto en base64- para no volver a pagarlas.
//
// Las fotos no caben en localStorage: veinte imagenes son ~6 MB y el navegador revienta a
// los 5 MB, asi que el borrador local guarda solo el texto. Consecuencia: refrescar la
// pestana obligaba a regenerar las fotos, y eso se cobra otra vez. Con 30 paginas en
// FLUX.2-dev son 75 centavos por cada recarga, por unas imagenes que ya existian.
//
// KV no tiene ese limite. Cada pagina va en su propia clave por dos razones: el tope de
// 25 MB es POR VALOR -treinta fotos juntas lo rozan- y guardar la pagina 12 no obliga a
// reescribir las otras veintinueve.
//
// Se leen a peticion, no al abrir el ebook: son varios MB y casi siempre se entra a
// revisar el texto, no a reimprimir.

import { exigirAlguno, exigirDueno, EQUIPO } from '../_sesion.js';

const PREFIJO = 'ebook:h:';

// El id de un ebook lo crea el servidor (uid() de ebook-proyecto.js: letras minusculas y digitos, 9 a 12 caracteres) y va dentro de la clave de KV y, mas tarde, en el HTML de la pagina. Se acepta ese
// formato y nada mas: con un id libre una peticion a mano podia escribir en claves ajenas (`ebook:indice`, la lista de todos los ebooks) o cerrar un atributo. El minimo de 8 deja fuera la palabra «indice».
export const ID_VALIDO = /^[a-z0-9]{8,32}$/;

// La foto es base64 y nada mas: la pagina la pega dentro de un atributo del HTML de la hoja (data:image/png;base64,<foto>) y las hojas se guardan en KV, asi que lo que no sea base64 -una comilla, un
// signo menor- seria una forma de cerrar ese atributo y abrir otro. Se aceptan los dos alfabetos (normal y URL-safe), el relleno y los saltos de linea que algun proveedor mete.
const SOLO_BASE64 = /^[A-Za-z0-9+\/=_\s-]*$/;

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// El numero va con ceros por delante: KV ordena las claves como texto, y sin esto la
// pagina 10 se leia antes que la 2 y el ebook salia descuadernado.
const clave = (id, n) => PREFIJO + id + ':' + String(n).padStart(3, '0');

export async function borrarHojas(env, id) {
  const lista = await env.SUITE.list({ prefix: PREFIJO + id + ':', limit: 1000 });
  await Promise.all(lista.keys.map((k) => env.SUITE.delete(k.name)));
}

// Las hojas de un ebook en el cuerpo de siempre -{ ok: true, hojas: [...] }-, pero SIN tenerlas todas en memoria ni volver a codificarlas.
//
// Antes: list + get de TODAS a la vez con { type: 'json' } (parsea cada valor) y json() (lo vuelve a escribir entero). Con fotos de 2-3 MB eran decenas de MB vivos a la vez y un parseo
// y una escritura de todo en una sola llamada: Cloudflare cortaba con el error 1102 («Worker exceeded resource limits») y el creador no podia recuperar un ebook ya pagado (y como
// recuperarHojas() calla los fallos, parecia que el ebook estaba vacio y se invitaba a regenerarlo: pagar otra vez).
// Ahora cada hoja se lee de KV como un flujo de bytes y se manda tal cual, una detras de otra, a medida que llega: sin decodificar, sin parsear y con la siguiente ya pedida mientras
// sale la actual. Cada valor YA es JSON (lo escribio JSON.stringify en el POST de abajo), asi que el cuerpo sale identico al de antes. Una clave que desaparezca entre el list y el
// get se salta, y un valor que no empiece por «{» (no es una hoja nuestra) tampoco se manda: no puede romper el JSON de las demas.
const PREFETCH = 4;
async function hojasEnStream(env, id) {
  const lista = await env.SUITE.list({ prefix: PREFIJO + id + ':', limit: 1000 });
  const claves = lista.keys.map((k) => k.name);
  const enc = new TextEncoder();
  let sig = 0, fase = 0, primera = true, lector = null;
  // Las siguientes hojas se piden POR ADELANTADO, de a PREFETCH: con solo la siguiente, un ebook de 120 hojas tardaba 24 s en llegar (una ida y vuelta a KV por hoja, en fila); con 4 por
  // delante baja a unos 5 s sin volver al «todo a la vez» de antes. Abiertas a la vez: la que sale + las que esperan = 5, por debajo de las 6 conexiones simultaneas que deja el Worker.
  const cola = [];
  const pedir = () => {
    if (sig >= claves.length) return;
    const p = env.SUITE.get(claves[sig++], 'stream');
    p.catch(() => {});             // si nadie llega a esperarla (el cliente corto), que no quede como error sin atender
    cola.push(p);
  };
  for (let i = 0; i < PREFETCH; i++) pedir();
  const cuerpo = new ReadableStream({
    async pull(ctl) {
      try {
        for (;;) {
          if (fase === 0) { ctl.enqueue(enc.encode('{"ok":true,"hojas":[')); fase = 1; return; }
          if (fase === 2) { ctl.close(); return; }
          if (!lector) {
            if (!cola.length) { ctl.enqueue(enc.encode(']}')); fase = 2; return; }
            const flujo = await cola.shift();
            pedir();                 // otra por delante, mientras sale esta
            if (!flujo) continue;    // la clave desaparecio entre el list y el get
            // un KV que entregue el valor como texto (otro entorno, una prueba) tambien sirve: se convierte en un flujo de un solo trozo
            lector = (typeof flujo === 'string' ? new ReadableStream({ start(c) { c.enqueue(enc.encode(flujo)); c.close(); } }) : flujo).getReader();
            const primero = await lector.read();
            if (primero.done || !primero.value || !primero.value.length || primero.value[0] !== 0x7B) {
              try { await lector.cancel(); } catch (e) { /* ya cerrado */ }
              lector = null; continue;    // vacio, o no es una hoja (no empieza por «{»)
            }
            ctl.enqueue(primera ? primero.value : concat(enc.encode(','), primero.value));
            primera = false;
            return;
          }
          const r = await lector.read();
          if (r.done) { lector = null; continue; }
          if (r.value && r.value.length) { ctl.enqueue(r.value); return; }
        }
      } catch (e) { ctl.error(e); }
    },
    async cancel() {
      try { if (lector) await lector.cancel(); } catch (e) { /* el cliente se fue */ }
      // las que ya estaban pedidas y nadie va a leer: se sueltan para no dejar conexiones abiertas
      cola.splice(0).forEach((p) => { p.then((s) => { if (s && typeof s.cancel === 'function') s.cancel(); }).catch(() => {}); });
    },
  });
  return new Response(cuerpo, { status: 200, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
}
function concat(a, b) { const o = new Uint8Array(a.length + b.length); o.set(a, 0); o.set(b, a.length); return o; }

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirAlguno(request, env, EQUIPO))) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  const url = new URL(request.url);
  const id = url.searchParams.get('id');

  if (request.method === 'GET') {
    if (!id) return json(400, { ok: false, error: 'Falta el id' });
    if (!ID_VALIDO.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    return hojasEnStream(env, id);
  }

  if (request.method === 'DELETE') {
    // Borrar las hojas son las fotos ya pagadas: es como borrar el ebook, solo el dueno (el DELETE de ebook-proyecto lo hace por dentro con borrarHojas). Ninguna pantalla pide este DELETE.
    if (!(await exigirDueno(request, env))) return json(403, { ok: false, error: 'Solo el dueño puede borrar las hojas de un ebook' });
    if (!id) return json(400, { ok: false, error: 'Falta el id' });
    if (!ID_VALIDO.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    await borrarHojas(env, id);
    return json(200, { ok: true });
  }

  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  if (!body.id) return json(400, { ok: false, error: 'Falta el id del ebook' });
  if (!ID_VALIDO.test(String(body.id))) return json(400, { ok: false, error: 'Id no valido' });
  const hojas = Array.isArray(body.hojas) ? body.hojas : [];
  if (!hojas.length) return json(400, { ok: false, error: 'No hay paginas que guardar' });

  // Solo se escribe lo que CAMBIO. Antes cada guardado borraba las N hojas y las volvia a
  // escribir, y el navegador guarda despues de cada edicion: corregir una palabra en un ebook de
  // 30 paginas costaba ~60 operaciones de KV, y el plan gratuito permite 1.000 escrituras al dia.
  // Quince correcciones agotaban el cupo de toda la suite (ajustes, tablero, flujos de ZapData).
  // Cada hoja lleva una huella de su contenido en la metadata de su clave; list() la devuelve sin
  // leer los valores -son varios MB de fotos-, y si no cambio no se escribe nada.
  const prefijo = PREFIJO + body.id + ':';
  const previas = new Map();
  let cursor;
  do {
    const l = await env.SUITE.list({ prefix: prefijo, cursor, limit: 1000 });
    l.keys.forEach((k) => previas.set(k.name, (k.metadata && k.metadata.h) || ''));
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor);

  const nuevas = new Set();
  let escritas = 0;
  try {
    await Promise.all(hojas.map(async (h, i) => {
      const k = clave(body.id, i + 1);
      nuevas.add(k);
      const valor = JSON.stringify({
        n: i + 1,
        titulo: String((h && h.titulo) || '').slice(0, 200),
        html: String((h && h.html) || ''),
        imagen: (h && typeof h.imagen === 'string' && SOLO_BASE64.test(h.imagen)) ? h.imagen : null,
        // La descripcion con la que se genero la foto. Sin guardarla, al recuperar un ebook de
        // otro dia el boton de "otra foto" no tenia de donde partir y habia que volver a
        // deducirla -otra llamada- o, en portada y cierre, era imposible.
        // 6.000 y no 2.000: el prompt de una pagina dibujada por la IA lleva dentro todo el texto de la pagina (ver functions/_ebook-pagina-ia.js).
        promptImagen: (h && typeof h.promptImagen === 'string') ? h.promptImagen.slice(0, 6000) : null,
        // Que era esta hoja: la portada, el cierre, o la pagina numero X del plan. Es lo que
        // permite reescribir SU texto sin adivinar a que parte del plan corresponde, porque el
        // indice de hojas y el del plan no coinciden (la portada va delante).
        // dudosa: el texto de la imagen (portada o pagina dibujada por la IA) no coincidia con el plan ni despues de repetirla (la lectura del texto de functions/_ebook-verificar.js);
        // se guarda para que el ebook, al abrirlo otro dia, siga marcando las hojas que hay que mirar. Llega como `dudosa` o como la `verificacion` de la respuesta de la pagina.
        dudosa: !!(h && (h.dudosa === true || (h.verificacion && h.verificacion.ok === false))),
        esPortada: !!(h && h.esPortada),
        esCierre: !!(h && h.esCierre),
        planIdx: (h && Number.isInteger(h.planIdx)) ? h.planIdx : null,
      });
      const huella = await huellaDe(valor);
      if (previas.get(k) === huella) return;
      await env.SUITE.put(k, valor, { metadata: { h: huella } });
      escritas++;
    }));
    // Regenerar con menos paginas dejaba las sobrantes de la tanda vieja mezcladas con las
    // nuevas: se borran solo las que ya no corresponden a ninguna hoja.
    await Promise.all([...previas.keys()].filter((k) => !nuevas.has(k)).map((k) => env.SUITE.delete(k)));
  } catch (e) {
    return json(502, { ok: false, error: 'No se pudieron guardar las hojas: ' + String((e && e.message) || e).slice(0, 200) });
  }

  return json(200, { ok: true, guardadas: hojas.length, escritas });
}

// Huella corta del contenido de una hoja (SHA-1 en hexadecimal): no es seguridad, solo detectar
// que algo cambio.
async function huellaDe(texto) {
  const d = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(texto));
  return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, '0')).join('');
}
