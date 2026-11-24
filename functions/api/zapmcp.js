// La conexion con la que el tablero le habla a ZapData.
//
// SOLO EL DUENO, salvo la vuelta del permiso, que se explica abajo. Esta conexion abre la
// cuenta de ZapData y quien crea ofertas no necesita verla: el generador la lee por su
// cuenta, del lado del servidor.
//
// Hay dos formas de conectar y NO dan lo mismo:
//
//   Pedir permiso (OAuth). Es la buena. El dueno le da a conectar, ZapData le pregunta si
//   autoriza al tablero, y vuelve con el permiso `flows:draft`, que es el de crear flujos.
//   Es lo mismo que hacen los demas clientes que si crean flujos.
//
//   Pegar un token estatico. Sirve, pero hoy su panel no deja marcarle el permiso de crear
//   flujos -lo tiene en "Proximamente"-, asi que una credencial pegada normalmente solo
//   puede leer. Se deja porque el dia que lo habiliten es la via mas simple, y porque
//   alguien puede tener una credencial vieja con mas permisos.
//
// El token NUNCA vuelve entero al navegador: solo el principio y el final, lo justo para
// reconocer cual esta puesto.

import { readSession } from '../_sesion.js';
import {
  SERVIDOR, CLAVE, PENDIENTE, HERRAMIENTA, PERMISOS, probar, comenzar, refrescarSiHaceFalta,
} from '../_zapdata.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

function pista(t) {
  const s = String(t || '');
  if (s.length < 14) return '••••';
  return s.slice(0, 8) + '…' + s.slice(-4);
}

// A donde vuelve ZapData despues de que el dueno autoriza.
//
// Ruta limpia, sin parametros: ZapData rechaza registrar una direccion de vuelta que lleve
// interrogante ("invalid_redirect_uri"). Lo que pasa al volver vive en
// functions/api/zapvuelta.js, que por eso es un archivo aparte.
const vuelta = (request) => new URL("/api/zapvuelta", new URL(request.url).origin).toString();

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: "Falta el almacen KV" });

  if (true) {
    const rol = env.SESSION_SECRET
      ? await readSession(request.headers.get('Cookie'), env.SESSION_SECRET)
      : null;
    if (rol !== 'owner') return json(403, { ok: false, error: 'Solo el dueño configura la conexión.' });
  }

  const guardado = (await env.SUITE.get(CLAVE, { type: 'json' })) || null;

  if (request.method === 'GET') {
    return json(200, {
      ok: true, servidor: SERVIDOR, herramienta: HERRAMIENTA, permisos: PERMISOS,
      enVariable: !!env.ZAPDATA_TOKEN && !guardado,
      credencial: guardado ? {
        tipo: guardado.tipo || 'token', pista: pista(guardado.token),
        nombre: guardado.nombre || '', guardadoEn: guardado.guardadoEn,
        probadoEn: guardado.probadoEn || '', permisos: guardado.permisos || '',
        puedeCrear: !!guardado.puedeCrear, herramientas: guardado.herramientas || [],
      } : null,
    });
  }
  if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  // ---- El permiso conseguido desde el computador del dueno ----
  //
  // ZapData solo acepta volver a localhost o a claude.ai, asi que el tablero no puede
  // pedir el permiso por su cuenta: lo pide scripts/Conectar-ZapData.ps1 desde el PC y
  // aqui se pega el resultado. Se pega UNA vez; de ahi en adelante el tablero se renueva
  // solo con el codigo de refresco, que es lo que hace que esto no sea una cita mensual.
  if (body.pegado) {
    let c;
    try { c = JSON.parse(atob(String(body.pegado).trim())); }
    catch (e) { return json(400, { ok: false, error: 'Eso no es lo que imprime el script. Cópialo completo.' }); }
    if (!c || !c.token) return json(400, { ok: false, error: 'Al texto pegado le falta el permiso.' });

    // Entre pedir el permiso en el PC y pegarlo aqui puede pasar mas de una hora, y el
    // token de acceso dura eso. Se renueva antes de probar: si no, la pantalla diria que
    // la credencial no sirve cuando lo unico que paso es que se enfrio.
    let r;
    try {
      await refrescarSiHaceFalta(c);
      r = await probar(c.token);
    } catch (e) { return json(200, { ok: true, fallo: e.message || String(e) }); }

    const nuevo = Object.assign({}, c, {
      tipo: 'oauth', nombre: String(body.nombre || '').trim(),
      guardadoEn: new Date().toISOString(), probadoEn: new Date().toISOString(),
      puedeCrear: r.puedeCrear, herramientas: r.herramientas,
    });
    await env.SUITE.put(CLAVE, JSON.stringify(nuevo));
    return json(200, { ok: true, puedeCrear: r.puedeCrear, herramientas: r.herramientas });
  }

  // ---- Primera pata: empezar a pedir permiso ----
  if (body.accion === 'conectar') {
    let c;
    try { c = await comenzar(vuelta(request)); }
    catch (e) { return json(200, { ok: true, fallo: e.message || String(e) }); }
    await env.SUITE.put(PENDIENTE, JSON.stringify(c.pendiente), { expirationTtl: 900 });
    return json(200, { ok: true, ir: c.url });
  }

  if (body.accion === 'borrar') {
    await env.SUITE.delete(CLAVE);
    return json(200, { ok: true, credencial: null });
  }

  if (body.accion === 'probar') {
    if (!guardado) return json(400, { ok: false, error: 'No hay ninguna conexión guardada.' });
    let r;
    try { r = await probar(guardado.token); }
    catch (e) { return json(200, { ok: true, fallo: e.message || String(e) }); }
    guardado.probadoEn = new Date().toISOString();
    guardado.puedeCrear = r.puedeCrear;
    guardado.herramientas = r.herramientas;
    await env.SUITE.put(CLAVE, JSON.stringify(guardado));
    return json(200, { ok: true, puedeCrear: r.puedeCrear, herramientas: r.herramientas });
  }

  // ---- El otro camino: pegar un token estatico ----
  const token = String(body.token || '').trim();
  if (!token) return json(400, { ok: false, error: 'Pega la credencial.' });
  if (token.length < 14) return json(400, { ok: false, error: 'Esa credencial se ve incompleta.' });

  // Se prueba ANTES de guardar.
  let r;
  try { r = await probar(token); }
  catch (e) { return json(200, { ok: true, fallo: e.message || String(e) }); }

  const nuevo = {
    tipo: 'token', token, nombre: String(body.nombre || '').trim(),
    guardadoEn: new Date().toISOString(), probadoEn: new Date().toISOString(),
    puedeCrear: r.puedeCrear, herramientas: r.herramientas,
  };
  await env.SUITE.put(CLAVE, JSON.stringify(nuevo));
  return json(200, {
    ok: true, puedeCrear: r.puedeCrear, herramientas: r.herramientas,
    credencial: { pista: pista(token), nombre: nuevo.nombre, guardadoEn: nuevo.guardadoEn },
  });
}
