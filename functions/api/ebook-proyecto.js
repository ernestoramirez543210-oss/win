// Guarda el ebook en curso para no perderlo al cerrar la pestana.
//
// Un ebook de 20 paginas son 20 llamadas y cinco minutos de espera. Sin esto, un refresco
// del navegador -o que se cierre el portatil- tira todo el trabajo Y la plata ya gastada
// en las imagenes. Guardar cuesta una escritura en KV.
//
// Las imagenes no van en este mismo valor: 20 fotos en base64 son ~6 MB y guardarlas junto
// al texto obligaria a releer varios MB cada vez que alguien abre la lista de ebooks. Viven
// en ebook-hojas.js, una clave por pagina, y se leen solo cuando se van a ver o a imprimir.

import { exigirAlguno, exigirDueno, EQUIPO } from '../_sesion.js';
import { borrarHojas, ID_VALIDO } from './ebook-hojas.js';
import { FLUJOS } from './clicchat.js';
import { rescatarFugas } from '../_ebook-fugas.js';

const PREFIJO = 'ebook:';
const INDICE = 'ebook:indice';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

// Los flujos de ClicChat que guarda un ebook son solo los conocidos (los ids de FLUJOS en clicchat.js), cada uno con su nombre y sus mensajes como texto: la pagina pinta el id en un atributo y el
// texto en la pantalla, y cualquier rol del equipo puede escribir aqui por la API. Un id de flujo inventado, o un valor que no sea un objeto, no se guarda.
function limpiarFlujos(f) {
  const out = {};
  if (!f || typeof f !== 'object' || Array.isArray(f)) return out;
  for (const def of FLUJOS) {
    const v = Object.prototype.hasOwnProperty.call(f, def.id) ? f[def.id] : null;
    if (!v || typeof v !== 'object' || Array.isArray(v)) continue;
    out[def.id] = {
      nombre: String(v.nombre == null ? def.nombre : v.nombre).slice(0, 120),
      mensajes: (Array.isArray(v.mensajes) ? v.mensajes : []).slice(0, 40).map((m, i) => ({
        orden: i + 1, texto: String((m && m.texto) || '').slice(0, 4000), nota: String((m && m.nota) || '').slice(0, 600),
      })),
    };
  }
  return out;
}

async function leerIndice(env) {
  const raw = await env.SUITE.get(INDICE);
  if (!raw) return [];
  try { return JSON.parse(raw); } catch { return []; }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  const rol = await exigirAlguno(request, env, EQUIPO);
  if (!rol) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  const url = new URL(request.url);

  if (request.method === 'GET') {
    // Las voces de los avatares, juntas.
    //
    // "Aprendizajes" del cerebro es un cuadro de texto vacio que pide disciplina diaria, y
    // por eso lleva vacio desde que existe. Mientras tanto cada ebook produce, gratis, lo
    // que costaria una encuesta: por que dudaria, por que no pagaria, con que palabras. Eso
    // se quedaba enterrado en el JSON de su proyecto y no volvia nunca.
    //
    // Se devuelve para MIRAR, no se escribe solo en el cerebro: son hipotesis de un modelo
    // sobre una clienta, no hechos. Quien conoce el negocio decide cual sube.
    if (url.searchParams.get('voces')) {
      const indice = await leerIndice(env);
      const voces = [];
      // Los 30 mas recientes: el indice ya viene ordenado y leer 200 proyectos por abrir
      // una pantalla costaria mas de lo que aporta el aprendizaje 31.
      for (const p of indice.slice(0, 30)) {
        const raw = await env.SUITE.get(PREFIJO + p.id);
        if (!raw) continue;
        let d; try { d = JSON.parse(raw); } catch { continue; }
        const a = d.avatar;
        if (!a || !a.nombre) continue;
        voces.push({
          titulo: d.titulo || '',
          pais: (d.brief && d.brief.pais) || '',
          quien: a.nombre,
          objeciones: Array.isArray(a.objeciones) ? a.objeciones : [],
          noPaga: a.porQueNoPagaria || '',
          paga: a.porQuePagaria || '',
          pierde: a.loQueLaPierde || '',
          palabras: Array.isArray(a.palabras) ? a.palabras : [],
        });
      }
      return json(200, { ok: true, voces });
    }
    const id = url.searchParams.get('id');
    if (!id) return json(200, { ok: true, proyectos: await leerIndice(env) });
    if (!ID_VALIDO.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    const raw = await env.SUITE.get(PREFIJO + id);
    if (!raw) return json(404, { ok: false, error: 'Ese ebook no existe' });
    const proyecto = JSON.parse(raw);
    // Un plan escrito antes del arreglo del lector puede traer campos metidos dentro del texto (codigo que la IA dibujaba en la pagina): se entrega limpio.
    // No se reescribe aqui en KV (una lectura no gasta escrituras): el creador lo guarda limpio la proxima vez que guarde.
    if (proyecto && Array.isArray(proyecto.paginas)) proyecto.paginas.forEach((p) => rescatarFugas(p));
    return json(200, { ok: true, proyecto });
  }

  if (request.method === 'DELETE') {
    // Borrar es lo unico que no se deshace: un editor podia borrar el ebook de otro
    // editor de un clic y no habia forma de recuperarlo. Escribir lo puede el equipo;
    // borrar, solo el dueno.
    if (!(await exigirDueno(request, env))) {
      return json(403, { ok: false, error: 'Solo el dueño puede borrar ebooks' });
    }
    const id = url.searchParams.get('id');
    if (!id) return json(400, { ok: false, error: 'Falta el id' });
    if (!ID_VALIDO.test(id)) return json(400, { ok: false, error: 'Id no valido' });
    await env.SUITE.delete(PREFIJO + id);
    // Las paginas renderizadas viven en sus propias claves: sin esto se quedaban en KV
    // ocupando megas para siempre, huerfanas de un ebook que ya no existe.
    await borrarHojas(env, id);
    const idx = (await leerIndice(env)).filter((p) => p.id !== id);
    await env.SUITE.put(INDICE, JSON.stringify(idx));
    return json(200, { ok: true });
  }

  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  // Un id que mande el navegador solo vale con el formato que crea uid(): ver ID_VALIDO en ebook-hojas.js.
  if (body.id && !ID_VALIDO.test(String(body.id))) return json(400, { ok: false, error: 'Id no valido' });
  const id = body.id ? String(body.id) : uid();
  const ahora = new Date().toISOString();
  const previo = body.id ? await env.SUITE.get(PREFIJO + id, { type: 'json' }) : null;

  const proyecto = {
    id,
    titulo: (body.titulo || (body.brief && body.brief.titulo) || 'Sin titulo').slice(0, 160),
    brief: body.brief || (previo && previo.brief) || null,
    paginas: Array.isArray(body.paginas) ? body.paginas : ((previo && previo.paginas) || []),
    // Los flujos de ClicChat viven aqui igual que las paginas: sin esto el cliente los
    // mandaba, el servidor los tiraba en silencio, y los seis flujos escritos se perdian
    // al recargar la pestana.
    flujos: (body.flujos && typeof body.flujos === 'object')
      ? limpiarFlujos(body.flujos) : ((previo && previo.flujos) || {}),
    // El avatar se guarda con el ebook porque es lo que hace que el contenido y los
    // mensajes le hablen a la misma persona. Si se perdiera, al retomar el ebook la
    // siguiente tanda de paginas saldria escrita para otra.
    avatar: (body.avatar && typeof body.avatar === 'object')
      ? body.avatar : ((previo && previo.avatar) || null),
    estiloFoto: body.estiloFoto || (previo && previo.estiloFoto) || '',
    // Los datos de pago van con el ebook, no con el navegador de quien lo arme: al retomar
    // un proyecto guardado el PDF tiene que volver a salir con su pagina de cobro.
    datosPago: typeof body.datosPago === 'string'
      ? body.datosPago.slice(0, 600) : ((previo && previo.datosPago) || ''),
    colorPrimario: body.colorPrimario || (previo && previo.colorPrimario) || '#e91e63',
    colorSecundario: body.colorSecundario || (previo && previo.colorSecundario) || '#2d7a3e',
    // Como se hacen las hojas: diseno 'ia' (la IA dibuja cada pagina con el texto adentro) o 'codigo' (maqueta del sistema + foto); portadaModo 'ia' (titulo dentro de la imagen) o 'html'.
    // Solo valen esos dos valores; un proyecto viejo no los trae y el creador los abre con el valor de siempre.
    diseno: body.diseno === 'codigo' ? 'codigo' : body.diseno === 'ia' ? 'ia' : ((previo && previo.diseno) || ''),
    portadaModo: body.portadaModo === 'html' ? 'html' : body.portadaModo === 'ia' ? 'ia' : ((previo && previo.portadaModo) || ''),
    creadoEn: (previo && previo.creadoEn) || ahora,
    guardadoEn: ahora,
    // Quien lo creo y quien lo guardo por ultima vez: lo pone el servidor desde la sesion, no el navegador. Cada rol del equipo lo usa una sola persona, asi que es la huella de quien cambio algo
    // (los datos de pago, por ejemplo) ahora que todo el equipo crea ebooks.
    creadoPor: (previo && previo.creadoPor) || rol,
    guardadoPor: rol,
  };

  await env.SUITE.put(PREFIJO + id, JSON.stringify(proyecto));

  // El indice es la lista que se pinta al abrir: se guarda solo el resumen para no tener
  // que leer veinte proyectos completos para dibujar un listado.
  const idx = await leerIndice(env);
  const resumen = {
    id, titulo: proyecto.titulo, paginas: proyecto.paginas.length,
    creadoEn: proyecto.creadoEn, guardadoEn: ahora,
  };
  const i = idx.findIndex((p) => p.id === id);
  // Si el resumen no cambio -mismo titulo y mismo numero de paginas- y el de la lista se guardo
  // hace menos de una hora, se salta la escritura: el navegador guarda tras cada retoque, y esta
  // clave se reescribia entera cada vez. Solo se pierde la hora exacta del ultimo guardado en
  // el listado, con una hora de margen. KV gratis permite 1.000 escrituras al dia.
  if (i >= 0 && idx[i].titulo === resumen.titulo && idx[i].paginas === resumen.paginas
      && ahora && Date.parse(ahora) - Date.parse(idx[i].guardadoEn || 0) < 3600 * 1000) {
    return json(200, { ok: true, id, proyecto });
  }
  if (i >= 0) idx[i] = resumen; else idx.unshift(resumen);
  await env.SUITE.put(INDICE, JSON.stringify(idx.slice(0, 60)));

  return json(200, { ok: true, id, proyecto });
}
