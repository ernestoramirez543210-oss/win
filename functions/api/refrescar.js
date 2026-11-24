// Refresco bajo demanda: el tablero se actualiza cuando alguien lo mira, no cada hora
// contra un reloj que corre igual a las 4 de la manana.
//
// El cron horario gastaba ~3.960 minutos de GitHub Actions al mes contra un plan de
// 2.000, y se agoto: desde ese momento GitHub rechaza los jobs y el tablero se queda
// viejo. Mirandolo al reves, el refresco solo hace falta cuando hay alguien del otro
// lado. Diez horas distintas de uso al dia son ~960 minutos al mes, y los dias que
// nadie entra no cuesta nada.
//
// El limite de una corrida por hora NO necesita almacenamiento: el propio historial de
// GitHub dice cuando arranco la ultima. Se le pregunta y se decide. Asi, diez personas
// abriendo el tablero a la vez disparan una sola corrida, y no hay estado que se pueda
// desincronizar entre dos centros de datos.
//
//   GET  /api/refrescar  -> estado: cuando corrio la ultima, si se puede disparar
//   POST /api/refrescar  -> dispara, si paso el minimo; si no, dice cuanto falta
//
// Env vars (Cloudflare Pages -> Settings -> Environment variables). Dos caminos, y basta
// con uno:
//
//   URL_CRON + CLAVE_REFRESCO  - el preferido. Se le pide al Worker del cron, que YA tiene
//              el token de GitHub como secreto de verdad. Las env vars de Pages se guardan
//              en texto plano y se leen enteras desde el panel: el PAT de GitHub abre el
//              repo y sus workflows, y no tiene por que estar ahi. Lo que viaja a Pages es
//              una clave que solo sirve para pedir un refresco.
//   GH_TOKEN - el token de GitHub aqui mismo. Sigue funcionando y tiene prioridad si esta.
//
// Sin ninguno de los dos, contesta 503 y lo dice; nunca falla en silencio.
import { exigirSesion } from '../_sesion.js';

const REPO = 'TU-USUARIO/TU-REPO';   // PON AQUI tu repositorio de GitHub (usuario/nombre). Ver CLAUDE.md
const WORKFLOW = 'refresh.yml';
const MIN_MINUTOS = 60;
// Si la ultima corrida FALLO, la espera es mas corta.
//
// El tope de una hora existe para no quemar los minutos del plan repitiendo un trabajo que
// ya se hizo bien. Pero una corrida fallida no hizo ningun trabajo: no hay dato fresco que
// proteger, y lo normal despues de un fallo es que alguien lo arregle en minutos. Con la
// hora completa, el arreglo quedaba esperando a que venciera el reloj de un intento que
// nunca produjo nada — paso exactamente eso: el workflow se arreglo a las 12:52 y el
// tablero seguia diciendo "se puede volver a pedir en 20 min" por una corrida de las 12:44
// que habia fallado al instante.
//
// Veinte y no cero: si GitHub esta rechazando los jobs por facturacion, todos los intentos
// fallan igual de rapido y reintentar cada treinta segundos no arregla nada.
const MIN_MINUTOS_TRAS_FALLO = 20;
function minimoPara(u) {
  const fallo = u && u.estado === 'completed' &&
                u.conclusion && u.conclusion !== 'success';
  return fallo ? MIN_MINUTOS_TRAS_FALLO : MIN_MINUTOS;
}
// Quien puede pedir un refresco. Es la misma pareja que el middleware deja entrar a
// /api/refrescar; tenerlo escrito aqui tambien es lo que hace que la puerta siga cerrada
// aunque el middleware quede abierto.
const PUEDEN_REFRESCAR = ['owner', 'trafficker'];

function json(cuerpo, status) {
  return new Response(JSON.stringify(cuerpo), {
    status: status || 200,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

function gh(url, env, init) {
  const base = {
    'Authorization': 'Bearer ' + env.GH_TOKEN,
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'mi-dashboard-refresco',
  };
  const o = init || {};
  return fetch(url, Object.assign({}, o, { headers: Object.assign(base, o.headers || {}) }));
}

// Ultima corrida del workflow, sea cual sea su resultado. Tambien cuentan las fallidas:
// si GitHub esta rechazando los jobs por facturacion, reintentar cada treinta segundos
// no arregla nada y solo llena el historial de fallos.
// El Worker del cron, que guarda el token. Devuelve lo mismo que se le pediria a GitHub.
function porWorker(env) { return !env.GH_TOKEN && !!env.URL_CRON && !!env.CLAVE_REFRESCO; }

function worker(env, ruta, init) {
  const base = String(env.URL_CRON).replace(/\/+$/, '');
  const o = init || {};
  return fetch(base + ruta, Object.assign({}, o, {
    headers: Object.assign({ 'x-clave': env.CLAVE_REFRESCO }, o.headers || {}),
  }));
}

async function ultimaCorrida(env) {
  if (porWorker(env)) {
    const r = await worker(env, '/estado');
    if (!r.ok) return { error: 'el worker del cron respondio ' + r.status };
    const j = await r.json();
    return j.error ? { error: j.error } : j;
  }
  const url = 'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW +
              '/runs?per_page=1';
  const r = await gh(url, env);
  if (!r.ok) return { error: 'github respondio ' + r.status };
  const j = await r.json();
  const run = (j.workflow_runs || [])[0];
  if (!run) return { ninguna: true, minutos: null };
  return {
    creada: run.created_at,
    estado: run.status,               // queued | in_progress | completed
    conclusion: run.conclusion,       // success | failure | null
    url: run.html_url,
    minutos: (Date.now() - Date.parse(run.created_at)) / 60000,
  };
}

function sinToken(env) {
  return !env.GH_TOKEN && !porWorker(env);
}

// Cuanto lleva la corrida, en SEGUNDOS y sin redondear a minutos.
//
// Los clientes arrancan su barra desde aqui cuando se suben a una corrida que ya iba. Con
// minutos enteros la barra quedaba hasta 30 s desfasada, y cuando el POST contestaba "ya hay
// una corriendo" ni siquiera venia el dato: la barra volvia a 0% con la corrida a la mitad.
function segundosDe(u) {
  if (u.creada) {
    const t = Date.parse(u.creada);
    if (!isNaN(t)) return Math.max(0, Math.round((Date.now() - t) / 1000));
  }
  return u.minutos == null ? null : Math.max(0, Math.round(u.minutos * 60));
}

export async function onRequestGet(context) {
  const { env, request } = context;
  if (!await exigirSesion(request, env, PUEDEN_REFRESCAR)) return json({ ok: false, motivo: 'No autenticado' }, 401);
  if (sinToken(env)) return json({ ok: false, motivo: 'sin GH_TOKEN configurado en Pages' }, 503);
  const u = await ultimaCorrida(env);
  if (u.error) return json({ ok: false, motivo: u.error }, 502);
  return json({
    ok: true,
    ultima: u.creada || null,
    estado: u.estado || null,
    conclusion: u.conclusion || null,
    minutos: u.minutos == null ? null : Math.round(u.minutos),
    segundos: segundosDe(u),
    corriendo: u.estado === 'queued' || u.estado === 'in_progress',
    puedeRefrescar: u.minutos == null || u.minutos >= minimoPara(u),
  });
}

export async function onRequestPost(context) {
  const { env, request } = context;
  // Dispara una corrida de GitHub Actions, o sea gasta minutos del plan. Falla cerrada a
  // proposito: el plan ya se agoto una vez y dejo el tablero viejo hasta el mes siguiente.
  const rol = await exigirSesion(request, env, PUEDEN_REFRESCAR);
  if (!rol) return json({ ok: false, motivo: 'No autenticado' }, 401);
  if (sinToken(env)) return json({ ok: false, motivo: 'sin GH_TOKEN ni URL_CRON configurados en Pages' }, 503);

  // Publicar es otra cosa que refrescar, aunque lo haga el mismo trabajo.
  //
  // El tope de una hora existe para no repetir la misma bajada de datos y quemar los
  // minutos del plan. Pero esta corrida tambien es la que sube el sitio, y cuando hay un
  // cambio de codigo esperando, esperarse la hora significa mirar una pagina vieja
  // sabiendo que la nueva ya esta lista. Asi que se puede saltar el tope, a proposito,
  // dandole al boton. Lo que NO se salta es el guardia de "ya hay una corriendo": dos
  // corridas en fila hacen el mismo trabajo dos veces y ahi si se gasta al pedo.
  //
  // Solo el dueno, porque es quien responde por los minutos del plan.
  // La bandera se acepta por la direccion Y por el cuerpo. Por el cuerpo solo no basta:
  // el sitio es una app instalable y hay un trabajador de servicio en medio que puede
  // reenviar la peticion sin cuerpo, y entonces publicar se comporta como un refresco
  // normal y choca contra el tope de la hora sin decir por que.
  let publicar = new URL(request.url).searchParams.get('publicar') === '1';
  if (!publicar) {
    try { publicar = (await request.json()).publicar === true; } catch (e) { /* sin cuerpo */ }
  }
  if (publicar && rol !== 'owner') {
    return json({ ok: false, motivo: 'Solo el dueño publica' }, 403);
  }

  const u = await ultimaCorrida(env);
  if (u.error) return json({ ok: false, motivo: u.error }, 502);

  // Ya hay una corriendo: no se encola otra. El workflow tiene concurrency sin
  // cancel-in-progress, asi que un segundo disparo esperaria en fila para hacer
  // exactamente el mismo trabajo, gastando el doble de minutos.
  if (u.estado === 'queued' || u.estado === 'in_progress') {
    return json({ ok: true, disparado: false, motivo: 'ya hay un refresco corriendo', corriendo: true,
                  minutos: u.minutos == null ? null : Math.round(u.minutos), segundos: segundosDe(u) });
  }
  const minimo = minimoPara(u);
  if (!publicar && u.minutos != null && u.minutos < minimo) {
    return json({
      ok: true, disparado: false,
      motivo: minimo === MIN_MINUTOS ? 'limite de una corrida por hora'
                                     : 'la anterior fallo hace poco; se reintenta en un rato',
      faltanMin: Math.ceil(minimo - u.minutos), minutos: Math.round(u.minutos),
    });
  }

  if (porWorker(env)) {
    const rw = await worker(env, '/disparar', { method: 'POST' });
    const jw = await rw.json().catch(() => ({}));
    if (!rw.ok) return json({ ok: false, motivo: 'el worker del cron respondio ' + rw.status, detalle: jw.motivo || '' }, 502);
    return json(jw);
  }

  const url = 'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/dispatches';
  const r = await gh(url, env, {
    method: 'POST',
    body: JSON.stringify({ ref: 'main' }),   // plantilla: su refresh.yml no tiene inputs
  });
  if (!r.ok) {
    const cuerpo = await r.text();
    return json({ ok: false, motivo: 'dispatch fallo: ' + r.status, detalle: cuerpo.slice(0, 300) }, 502);
  }
  return json({ ok: true, disparado: true });
}
