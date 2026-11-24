// Lista los modelos de imagen que el proveedor tiene disponibles.
//
// Existe porque el catalogo cambia sin avisar: FLUX.1-schnell-Free desaparecio de un dia
// para otro y la suite quedo pidiendo un modelo que ya no existe, con un 400 que no
// explicaba nada. Preguntandole al proveedor, el selector siempre ofrece lo que de verdad
// se puede usar hoy.
//
// Se cachea en KV 12 horas: el catalogo no cambia cada minuto y no hace falta gastar una
// llamada por cada vez que alguien abre la pestana.

import { exigirDueno, exigirAlguno, EQUIPO } from '../_sesion.js';
import { precioImagen, modeloSirve } from '../_precios.js';
import { llaveApimart, catalogoApimart, listaImagen, listaImagenRespaldo } from '../_apimart.js';
import { modelosCaidos } from './ebook-pagina.js';

const CACHE = 'modelos:imagen';
const HORAS = 12;
// Lo que ve quien solo mira (trafficker, videos) cuando todavia no hay catalogo guardado: no se le pide nada al proveedor en su nombre.
const MSG_SIN_CATALOGO = 'Todavía no hay catálogo guardado: lo trae el dueño o el editor al abrir esta página.';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Lo que se sabe del precio y la vocacion de cada familia. El proveedor no lo expone en
// /v1/models, y sin esto el selector seria una lista de 30 nombres crudos donde nadie
// sabe cual es el barato.
const NOTAS = [
  [/FLUX\.1-schnell/i,      { nota: 'el mas barato y rapido',        orden: 1 }],
  [/FLUX\.2-dev/i,          { nota: 'mejor calidad, sigue barato',   orden: 2 }],
  [/FLUX\.1\.1-pro/i,       { nota: 'calidad alta',                  orden: 4 }],
  [/FLUX\.2-(flex|pro)/i,   { nota: 'calidad alta',                  orden: 4 }],
  [/FLUX\.2-max/i,          { nota: 'lo mejor de Flux, mas caro',    orden: 6 }],
  [/kontext/i,              { nota: 'para editar imagenes',          orden: 8 }],
  [/imagen-4\.0-fast/i,     { nota: 'Google, rapido',                orden: 3 }],
  [/imagen-4\.0-ultra/i,    { nota: 'Google, maxima calidad',        orden: 6 }],
  [/imagen-4\.0/i,          { nota: 'Google',                        orden: 5 }],
  [/flash-image/i,          { nota: 'Google, rapido y barato',       orden: 3 }],
  [/gemini-3-pro-image/i,   { nota: 'Google, calidad alta',          orden: 5 }],
  [/gpt-image/i,            { nota: 'OpenAI',                        orden: 6 }],
  [/seedream/i,             { nota: 'bueno con texto en la imagen',  orden: 5 }],
  [/ideogram/i,             { nota: 'bueno con texto en la imagen',  orden: 5 }],
  [/qwen-image/i,           { nota: 'alternativa economica',         orden: 4 }],
  [/juggernaut/i,           { nota: 'estilo fotografico',            orden: 4 }],
  [/stable-diffusion/i,     { nota: 'clasico, economico',            orden: 7 }],
];

function anotar(id) {
  for (const [re, info] of NOTAS) if (re.test(id)) return info;
  return { nota: '', orden: 9 };
}

async function deTogether(llave, caidos) {
  const r = await fetch('https://api.together.xyz/v1/models', {
    headers: { Authorization: 'Bearer ' + llave },
  });
  if (!r.ok) {
    const t = await r.text().catch(() => '');
    return { error: 'Together respondio ' + r.status + ': ' + t.slice(0, 160) };
  }
  const lista = await r.json();
  // El catalogo incluye modelos que ya solo funcionan con endpoint dedicado, y no trae
  // ninguna marca que los distinga: FLUX.1-schnell seguia apareciendo aqui mientras
  // devolvia "non-serverless" en cada intento. Se descartan los que ya fallaron asi, que es
  // lo unico que de verdad lo demuestra.
  const fuera = caidos || [];
  const soloImagen = (Array.isArray(lista) ? lista : [])
    .filter((m) => m.type === 'image' && fuera.indexOf(m.id) === -1 && modeloSirve(m.id));
  const modelos = soloImagen.map((m) => {
    const a = anotar(m.id);
    // El precio por imagen viaja con el modelo: es la mitad del costo de un ebook.
    return { id: m.id, nombre: m.display_name || m.id, nota: a.nota, orden: a.orden,
             precio: precioImagen(m.id) };
  });
  // Se ordena por vocacion y no alfabeticamente: el barato tiene que salir primero
  // porque es el que se usa en el 90% de los casos.
  modelos.sort((x, y) => x.orden - y.orden || x.id.localeCompare(y.id));
  return { modelos };
}

// Deja el modelo de imagenes MAS BARATO como el de las paginas, una sola vez. El dueno pidio que
// el barato fuera el predeterminado (la portada sigue con su propio modelo, modeloPortada), pero
// el dia que lo pidio KV habia agotado su tope diario de escrituras y el cambio no se pudo
// guardar. Aqui se reintenta solo en cada lectura del catalogo hasta que KV acepte la escritura,
// y la marca baratoAplicado evita pisar despues lo que alguien elija a mano
// (/api/modelo-elegido tambien la pone). Devuelve el modelo aplicado, o null.
async function elegirBarato(env, ajustes, modelos) {
  const cfg = ajustes.imagenApi || {};
  if (cfg.tipo !== 'together' || cfg.baratoAplicado) return null;
  const barato = modelos.filter((m) => m.precio != null).sort((a, b) => a.precio - b.precio)[0];
  if (!barato) return null;
  try {
    await env.SUITE.put('ajustes', JSON.stringify({ ...ajustes, imagenApi: { ...cfg, modelo: barato.id, baratoAplicado: true } }));
  } catch { return null; }
  return barato.id;
}

// El catalogo de imagen de APIMart. Los modelos que ya estan puestos (paginas, portada, mockup) van siempre,
// aunque el catalogo no los traiga, para que el estimador de ebooks pueda ponerles precio. Sin llave, o si
// APIMart no contesta, se ofrece lo que la tabla de precios ya conoce (deRespaldo) en vez de dejar el
// selector vacio: el dueno igual puede escribir otro id con «Otro». Una llave que APIMart rechaza solo se
// reclama al refrescar a mano: el editor que abre el estimador no puede arreglarla.
async function deApimart(env, ajustes, cfg, refrescar, comun, lector) {
  const puestos = cfg.tipo === 'apimart' ? [cfg.modelo, cfg.modeloPortada, cfg.modeloMockup].filter(Boolean) : [];
  // Quien solo MIRA recibe lo que ya esta guardado: sin llave no hay llamada a APIMart ni escritura de KV (catalogoApimart sin cache y sin llave devuelve sinLlave).
  const llave = lector ? '' : await llaveApimart(env, ajustes);
  const cat = await catalogoApimart(env, llave, refrescar);
  if (lector && !cat.ok) return json(200, { ok: true, apimart: true, deRespaldo: true, modelos: listaImagenRespaldo(puestos), aviso: MSG_SIN_CATALOGO, ...comun });
  if (cat.ok) {
    return json(200, { ok: true, apimart: true, modelos: listaImagen(cat.modelos, puestos),
      deCache: cat.deCache, traidoEn: cat.traidoEn, ...comun });
  }
  if (cat.rechazada && refrescar) return json(200, { ok: false, error: cat.error });
  return json(200, { ok: true, apimart: true, deRespaldo: true, modelos: listaImagenRespaldo(puestos),
    aviso: cat.sinLlave ? 'Falta la llave de APIMart: conéctala en APIs.' : cat.error, ...comun });
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  // Mismo motivo que en modelos-texto: el editor necesita el catalogo para ver el costo
  // del ebook antes de generarlo. Aqui no sale ningun secreto.
  // Desde el 2026-10-15 todo el equipo LEE el catalogo; CAMBIAR el modelo sigue siendo del dueño y del editor (puedeCambiar, mas abajo).
  const rol = await exigirAlguno(request, env, EQUIPO);
  if (!rol) return json(401, { ok: false, error: 'Entra al equipo' });
  const esDueno = !!(await exigirDueno(request, env));
  // Quien arma las ofertas -el editor- es quien decide si el ebook sale barato o caro, asi
  // que ya no solo lee el costo: tambien puede bajar el modelo. Cambiarlo de verdad lo hace
  // /api/modelo-elegido, que no toca llaves ni el resto de /api/config.
  const puedeCambiar = esDueno || rol === 'editor';

  // Quien solo MIRA (trafficker y videos, desde el 2026-10-15) ve lo que ya esta guardado y nada mas: ni `?refrescar=1` ni `?proveedor=` le hacen preguntarle al proveedor con la llave del dueño, ni
  // `elegirBarato` (que reescribe `ajustes`) corre por su culpa, ni se escribe en KV (el tope diario de escrituras es de toda la suite). El catalogo en vivo lo pide quien puede cambiar el modelo.
  const lector = !puedeCambiar;
  const url = new URL(request.url);
  const refrescar = !lector && url.searchParams.get('refrescar') === '1';

  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const cfg = ajustes.imagenApi || {};
  // modeloPortada viaja aparte del catalogo: la portada puede costar distinto que las
  // paginas, y sin este id el estimador no tiene como buscarle su propio precio.
  const comun = { tipo: cfg.tipo || '', activo: cfg.modelo || '', modeloPortada: cfg.modeloPortada || '',
                   puedeCambiar };

  // APIMart: el catalogo REAL de la llave (lo que de verdad puede usar), con el precio donde la tabla de
  // functions/_precios.js lo conoce. Se sirve tambien cuando el proveedor guardado es otro pero la pestana
  // APIs pide ?proveedor=apimart: es lo que ve el dueno al elegir APIMart en el formulario, antes de guardar.
  // Cada modelo sin fila en la tabla sale "sin precio" y el estimador de ebooks lo dice, en vez de inventar uno.
  if (cfg.tipo === 'apimart' || (!lector && url.searchParams.get('proveedor') === 'apimart')) {
    return deApimart(env, ajustes, cfg, refrescar, comun, lector);
  }

  // El catalogo cacheado es el de Together. Con otro proveedor activo NO se sirve: la
  // pestana APIs recibia esa lista con ok:true, la pintaba como selector y ocultaba el
  // campo para escribir el modelo, asi que no habia forma de poner el de Replicate o "Otra"
  // mientras quedara un cache de Together de las ultimas doce horas. Con Replicate o "Otra"
  // no hay catalogo ni tabla de precios: el modelo se escribe a mano.
  if (cfg.tipo && cfg.tipo !== 'together') {
    return json(200, { ok: true, modelos: [], manual: true, ...comun });
  }

  if (!refrescar) {
    const cache = await env.SUITE.get(CACHE, { type: 'json' });
    if (cache && cache.modelos && cache.modelos.length) {
      // El precio se vuelve a poner al leer, no solo al escribir. Un cache guardado por
      // una version anterior del codigo no lo traia, y el estimador se quedaba en "sin
      // precio" hasta que caducara -doce horas-. Como sale de una tabla local, recalcularlo
      // es gratis y hace que ningun cache viejo pueda dejar el panel muerto.
      // El cache tambien se filtra al leer: si un modelo se cayo despues de guardarse, el
      // selector seguiria ofreciendolo hasta doce horas — justo mientras alguien intenta
      // generar el ebook que acaba de fallar.
      const caidos = await modelosCaidos(env);
      const modelos = cache.modelos
        .filter((m) => caidos.indexOf(m.id) === -1 && modeloSirve(m.id))
        // La tabla local manda sobre lo guardado: un cache de hace horas trae el precio de ANTES
        // de corregir la tabla (FLUX.2-dev a 0,025 cuando son 0,0154), y se seguiria estimando
        // con el numero viejo hasta que caducara.
        .map((m) => ({ ...m, precio: precioImagen(m.id) ?? m.precio }));
      // Si el filtro se comio TODO -el cache solo tenia el modelo que acaba de caer-, un
      // "ok:true" con la lista vacia deja al selector sin nada que ofrecer y al estimador
      // sin precio, aunque el proveedor si tenga alternativas: solo hace falta refrescar. En
      // vez de eso se sigue de largo al catalogo en vivo, mismo camino que -refrescar=1.
      if (modelos.length) {
        const nuevo = lector ? null : await elegirBarato(env, ajustes, modelos);   // elegirBarato escribe `ajustes`: solo si quien mira puede cambiar el modelo
        if (nuevo) comun.activo = nuevo;
        return json(200, { ok: true, modelos, deCache: true, traidoEn: cache.traidoEn, ...comun });
      }
    }
  }

  if (lector) return json(200, { ok: false, error: MSG_SIN_CATALOGO });   // sin catalogo guardado, quien solo mira no lo pide en vivo

  const llave = await env.SUITE.get('secreto:imagen');
  if (!llave) return json(200, { ok: false, error: 'No hay API key de imágenes. El dueño la configura en APIs.' });

  if (cfg.tipo !== 'together') {
    // Replicate no tiene un listado equivalente que sirva para esto; se deja escribir a mano.
    return json(200, { ok: true, modelos: [], manual: true, ...comun });
  }

  const caidos = await modelosCaidos(env);
  const res = await deTogether(llave, caidos);
  if (res.error) return json(200, { ok: false, error: res.error });

  // Guardar el catalogo en cache es un extra: si KV rechaza la escritura, el catalogo recien
  // traido igual se entrega. Antes la excepcion tumbaba toda la respuesta -una pagina HTML de
  // error en vez del catalogo, justo al apretar "refrescar"-.
  let cacheError = '';
  try {
    await env.SUITE.put(CACHE, JSON.stringify({ modelos: res.modelos, traidoEn: new Date().toISOString() }),
      { expirationTtl: HORAS * 3600 });
  } catch (e) { cacheError = String((e && e.message) || e).slice(0, 200); }
  const nuevo = await elegirBarato(env, ajustes, res.modelos);
  if (nuevo) comun.activo = nuevo;
  return json(200, { ok: true, modelos: res.modelos, deCache: false, ...(cacheError ? { cacheError } : {}), ...comun });
}
