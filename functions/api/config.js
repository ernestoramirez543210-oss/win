// Ajustes de la suite: enlace de Drive, proveedores de API y sus llaves.
//
// Regla dura: UNA LLAVE QUE ENTRA NO VUELVE A SALIR. Se guarda en KV bajo una clave
// aparte y el GET solo informa si esta puesta y sus ultimos 4 caracteres, lo justo para
// reconocerla. Si el GET devolviera la llave completa, cualquiera que abra la consola en
// esa pestana se la lleva, y el panel dejaria de ser mas seguro que pegarla en el HTML.
//
// Solo el rol dueno entra, y se comprueba aqui mismo: cuando REQUIRE_LOGIN esta apagado
// el middleware no mira la cookie, asi que este endpoint no puede dar por hecho que
// alguien filtro antes. Sin sesion de dueno, 401 - falla cerrado.
//
// Binding necesario en Pages: KV llamado SUITE (lo crea scripts\Deploy-Suite.ps1).

import { exigirDueno } from '../_sesion.js';
import { limpiarLlave, llaveValida, llaveApimart, esVideoApimart } from '../_apimart.js';
import { conVozEstandar } from '../_voces.js';

const CLAVE_AJUSTES = 'ajustes';
const PREFIJO_LLAVE = 'secreto:';

// Lo que el navegador puede leer y escribir. Todo lo demas se ignora: un PUT no puede
// inventar campos nuevos ni sobrescribir las claves de secretos por su nombre interno.
const CAMPOS = ['driveFolderUrl', 'proveedorActivo', 'proveedores', 'agente', 'imagenApi', 'audioApi', 'videoApi'];

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s,
  headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

async function leerAjustes(env) {
  const raw = await env.SUITE.get(CLAVE_AJUSTES);
  if (!raw) return { driveFolderUrl: '', proveedorActivo: '', proveedores: [], agente: {} };
  try { return JSON.parse(raw); } catch { return { driveFolderUrl: '', proveedorActivo: '', proveedores: [], agente: {} }; }
}

// Se exporta para que /api/agente y /api/probar-api usen la llave sin duplicar el
// formato de la clave en KV.
export async function leerLlave(env, idProveedor) {
  if (!idProveedor) return null;
  return await env.SUITE.get(PREFIJO_LLAVE + idProveedor);
}

export async function onRequest(context) {
  const { request, env } = context;

  if (!env.SUITE) {
    return json(501, { ok: false, error: 'Falta el almacen KV. Corre scripts\\Deploy-Suite.ps1 y vuelve a desplegar.' });
  }
  if (!(await exigirDueno(request, env))) {
    return json(401, { ok: false, error: 'Necesitas entrar como dueno para ver o cambiar los ajustes.' });
  }

  if (request.method === 'GET') {
    const a = await leerAjustes(env);
    // Se acompana cada proveedor con el estado de su llave, sin la llave.
    const proveedores = await Promise.all((a.proveedores || []).map(async (p) => {
      const llave = await leerLlave(env, p.id);
      return { ...p, tieneLlave: !!llave, colaLlave: llave ? llave.slice(-4) : '' };
    }));
    // Igual que con los proveedores de texto: se informa que la llave de imagenes esta
    // puesta, nunca su valor. Sin esto la pestana no puede distinguir "no configurado"
    // de "configurado pero sin llave", que fallan distinto.
    const llaveImg = await env.SUITE.get('secreto:imagen');
    const llaveAudio = await env.SUITE.get('secreto:audio');
    const llaveVideo = await env.SUITE.get('secreto:video');
    return json(200, {
      ok: true,
      ajustes: {
        // La lista de voces nunca sale vacía: lo ya guardado como [] se sirve con la estándar.
        ...a, proveedores, audioApi: conVozEstandar(a.audioApi),
        imagenTieneLlave: !!llaveImg,
        imagenColaLlave: llaveImg ? llaveImg.slice(-4) : '',
        audioTieneLlave: !!llaveAudio,
        audioColaLlave: llaveAudio ? llaveAudio.slice(-4) : '',
        videoTieneLlave: !!llaveVideo,
        videoColaLlave: llaveVideo ? llaveVideo.slice(-4) : '',
      },
    });
  }

  if (request.method === 'PUT') {
    let body;
    try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

    const actual = await leerAjustes(env);
    const nuevo = { ...actual };
    for (const c of CAMPOS) if (c in body) nuevo[c] = body[c];
    // Un PUT reemplaza audioApi entero; si llega sin voces (o con []), no se guarda el hueco.
    if (body.audioApi && typeof body.audioApi === 'object') nuevo.audioApi = conVozEstandar(body.audioApi);
    // La pagina APIs manda imagenApi entero y sin esta marca interna (functions/api/modelos-imagen.js):
    // sin copiarla, cada guardado la borraba y el "modelo barato por defecto" volvia a aplicarse.
    if (body.imagenApi && actual.imagenApi && actual.imagenApi.baratoAplicado && nuevo.imagenApi
        && typeof nuevo.imagenApi === 'object') {
      nuevo.imagenApi.baratoAplicado = true;
    }

    // Las llaves llegan aparte, en { llaves: { idProveedor: "..." } }, y se van a su
    // propia clave de KV. Cadena vacia = borrar esa llave.
    //
    // Se limpian y se revisan TODAS antes de escribir nada: una llave pegada con un espacio o un salto de
    // linea al final se guardaba "bien" y despues fallaba con un 401 que no decia por que. Ahora se recorta,
    // y si queda mal (espacios en medio, muy corta) se devuelve un error que lo dice y no se guarda nada.
    const llavesNuevas = {};
    if (body.llaves && typeof body.llaves === 'object') {
      for (const [id, valor] of Object.entries(body.llaves)) {
        if (typeof valor !== 'string') continue;
        // '********' es lo que manda el formulario cuando el usuario no la toco:
        // significa "dejala como esta", no "guardame estos asteriscos".
        if (valor === '********') continue;
        if (valor === '') { llavesNuevas[id] = ''; continue; }
        const limpia = limpiarLlave(valor);
        if (!limpia) continue;                     // solo espacios: no es una llave, no se toca nada
        if (!llaveValida(limpia)) {
          const prov = (nuevo.proveedores || []).find((p) => p && p.id === id);
          const de = id === 'imagen' ? 'imágenes' : (prov ? prov.nombre : id);
          return json(400, { ok: false, error: 'La llave de ' + de + ' tiene espacios o saltos de línea en medio, o está incompleta. Cópiala de nuevo del panel del proveedor.' });
        }
        llavesNuevas[id] = limpia;
      }
    }

    // Si KV rechaza una escritura -p. ej. el tope diario de escrituras-, el navegador recibia una pagina HTML
    // de error de Cloudflare y la pantalla decia "Unexpected token <". Se devuelve el motivo real.
    try {
      for (const [id, valor] of Object.entries(llavesNuevas)) {
        if (valor === '') await env.SUITE.delete(PREFIJO_LLAVE + id);
        else await env.SUITE.put(PREFIJO_LLAVE + id, valor);
      }
      // Nunca dejar una llave huerfana de un proveedor que se borro.
      const vivos = new Set((nuevo.proveedores || []).map((p) => p.id));
      for (const p of (actual.proveedores || [])) {
        if (!vivos.has(p.id)) await env.SUITE.delete(PREFIJO_LLAVE + p.id);
      }

      // APIMart: la llave es UNA para texto, imagen y video (functions/api/apimart.js). Tres casos:
      //  1. Se pego una llave en cualquiera de esos lugares: se copia a los otros, para que cambiarla (rotarla)
      //     no deje el texto con la vieja.
      //  2. Un proveedor de texto de tipo apimart se guarda sin llave: se le pone la que ya haya.
      //  3. La API de imagenes pasa a APIMart sin llave nueva: su llave todavia es la del proveedor anterior
      //     (Together...), asi que se le pone la de APIMart si hay una guardada.
      // Lo que se borro a proposito en esta misma peticion (llave '') no se vuelve a llenar.
      const provApimart = (nuevo.proveedores || []).filter((p) => p && p.tipo === 'apimart');
      const imagenApimart = !!(nuevo.imagenApi && nuevo.imagenApi.tipo === 'apimart');
      const videoApimart = esVideoApimart(nuevo);
      const esSlotApimart = (id) => id === 'imagen' ? imagenApimart : id === 'video' ? videoApimart
        : provApimart.some((p) => p.id === id);
      const pegada = Object.entries(llavesNuevas).find(([id, v]) => v !== '' && esSlotApimart(id));
      const ids = provApimart.map((p) => p.id).concat(imagenApimart ? ['imagen'] : [], videoApimart ? ['video'] : [])
        .filter((id) => llavesNuevas[id] !== '');
      if (pegada) {
        for (const id of ids) if (id !== pegada[0]) await env.SUITE.put(PREFIJO_LLAVE + id, pegada[1]);
      } else {
        const aImagen = imagenApimart && !(actual.imagenApi && actual.imagenApi.tipo === 'apimart') && !('imagen' in llavesNuevas);
        const faltan = [];
        for (const id of ids) {
          if ((id === 'imagen' && aImagen) || !(await leerLlave(env, id))) faltan.push(id);
        }
        if (faltan.length) {
          const k = await llaveApimart(env, nuevo, { sinImagen: aImagen });
          if (k) for (const id of faltan) await env.SUITE.put(PREFIJO_LLAVE + id, k);
        }
      }

      await env.SUITE.put(CLAVE_AJUSTES, JSON.stringify(nuevo));
    } catch (e) {
      return json(502, { ok: false, error: 'No se pudo guardar: ' + String((e && e.message) || e).slice(0, 200) });
    }

    const proveedores = await Promise.all((nuevo.proveedores || []).map(async (p) => {
      const llave = await leerLlave(env, p.id);
      return { ...p, tieneLlave: !!llave, colaLlave: llave ? llave.slice(-4) : '' };
    }));
    return json(200, { ok: true, ajustes: { ...nuevo, proveedores } });
  }

  return json(405, { ok: false, error: 'Method not allowed' });
}
