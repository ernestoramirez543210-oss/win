// El registro de lo que se aplico desde el dash (las reglas y las manos), compartido por todos los dispositivos.
//
//   GET /api/reglas-registro -> { ok, ahora, entradas: [ { t, tipo, nivel, id, cuenta, ok, regla, nombre, pais, razon, antes, despues, msg, efectivo, copia, clave, ms } ] }   (las mas nuevas primero)
//
// /api/toggle-ad escribe una clave reg:... por cada cambio que intenta aplicar -lo acepte o lo rechace Facebook-, con la ficha en la METADATA de la clave: asi se listan todas con una
// sola llamada, sin leer una por una. Duran 90 dias. Antes el registro vivia en el navegador de quien aprobaba: lo aprobado desde el telefono no aparecia en el computador, y limpiar
// los datos del sitio lo borraba. Ahora ademas sirve de memoria compartida (la ficha lleva la clave de la propuesta): lo que alguien ya aplico no se le vuelve a ofrecer a otro.
// Lo lee todo el equipo (dueño, trafficker, editor y videos: desde el 2026-10-15 todos ven /reglas y /oficina); los socios de pais no. Es solo lectura.

import { exigirAlguno, EQUIPO } from '../_sesion.js';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

export async function onRequestGet(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirAlguno(request, env, EQUIPO))) return json(401, { ok: false, error: 'No autenticado' });
  let entradas = [];
  try {
    const lista = await env.SUITE.list({ prefix: 'reg:', limit: 300 });
    entradas = lista.keys.map((k) => k.metadata).filter((m) => m && typeof m === 'object');
  } catch (e) {
    // Si no se pudo LISTAR no se contesta «ok con lista vacia»: la pagina lo leeria como «nadie ha aplicado nada» y volveria a ofrecer una propuesta ya aplicada desde otro
    // dispositivo (un +20 % de presupuesto aprobado dos veces es +44 %). Con ok:false la pagina lo dice y no finge que el registro esta vacio.
    return json(502, { ok: false, error: 'No se pudo leer el registro: ' + String((e && e.message) || e).slice(0, 120) });
  }
  return json(200, { ok: true, ahora: new Date().toISOString(), entradas });
}
