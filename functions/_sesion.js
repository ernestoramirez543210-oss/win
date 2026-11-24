// Cookie de sesion firmada, compartida por el middleware y por los endpoints.
//
// Vive aparte porque /api/config guarda las API keys y no puede confiar en que el
// middleware ya haya filtrado: si REQUIRE_LOGIN esta apagado el middleware deja pasar
// todo sin mirar la cookie, y un endpoint que administra secretos tiene que negarse por
// su cuenta. Codigo de seguridad duplicado es codigo que se arregla en un solo lado y
// sigue roto en el otro, asi que hay una sola copia y los dos la importan.

export const COOKIE = 'dashsess';
// Treinta dias, no doce horas.
//
// Con 12 h el tablero pide la clave dos veces al dia, y eso en un navegador se tolera pero
// en una app instalada es lo que hace que se deje de abrir: se toca el icono para mirar el
// gasto de la mañana y lo que sale es un formulario. La caducidad corta tampoco compraba
// mucha seguridad — la cookie es HttpOnly + Secure + SameSite=Strict, o sea que el riesgo
// real es un telefono perdido, y contra eso lo que sirve es poder invalidar la sesion
// (cambiar la clave del rol la tumba entera, porque el HMAC deja de validar), no que se
// venza a mediodia mientras el telefono sigue en el mismo bolsillo.
//
// Como se invalidan: cambiando SESSION_SECRET en Cloudflare Pages. La firma se hace sobre
// ese secreto, asi que rotarlo tumba TODAS las sesiones de golpe, de todos los roles.
// Cambiar la clave de un rol NO las tumba: impide entrar de nuevo, pero quien ya tenga la
// cookie sigue dentro hasta que se venza. Si se pierde un telefono, se rota el secreto.
export const SESSION_HOURS = 24 * 30;

const enc = new TextEncoder();

export async function hmac(data, secret) {
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(data));
  return btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// Comparacion de tiempo constante: una contrasena equivocada no debe delatar su prefijo
// por lo que tarda en fallar.
export function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const max = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < max; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export async function makeSession(role, secret) {
  const exp = Date.now() + SESSION_HOURS * 3600 * 1000;
  const body = `${role}.${exp}`;
  return `${body}.${await hmac(body, secret)}`;
}

export async function readSession(cookieHeader, secret) {
  if (!cookieHeader || !secret) return null;
  const m = cookieHeader.match(new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`));
  if (!m) return null;
  const parts = m[1].split('.');
  if (parts.length !== 3) return null;
  const [role, exp, sig] = parts;
  if (!safeEqual(sig, await hmac(`${role}.${exp}`, secret))) return null;
  if (Date.now() > Number(exp)) return null;
  return role;
}

// El rol dueno es el unico que administra secretos. Se pide explicitamente en cada
// endpoint sensible en vez de asumir que alguien filtro antes.
export async function exigirDueno(request, env) {
  const rol = await readSession(request.headers.get('Cookie'), env.SESSION_SECRET);
  if (rol !== 'owner') return null;
  return rol;
}

// Para lo que el equipo tambien usa. Crear entregables es el trabajo de quien crea
// ofertas, asi que no puede ser exclusivo del dueno; administrar llaves si.
//
// Con el login apagado no hay cookie que leer y todo el sitio esta abierto: se deja pasar
// para no dejar la suite inservible, igual que hace el middleware. Los endpoints que
// manejan secretos siguen usando exigirDueno, que falla cerrado pase lo que pase.
export async function exigirAlguno(request, env, roles) {
  // plantilla: el sitio siempre pide sesion; no hay salida «abierto»
  const rol = await readSession(request.headers.get('Cookie'), env.SESSION_SECRET);
  return rol && roles.indexOf(rol) !== -1 ? rol : null;
}

// Como exigirAlguno, pero SIN la salida de emergencia de REQUIRE_LOGIN.
//
// exigirAlguno deja pasar a todo el mundo cuando el login esta apagado, y para leer un
// tablero eso es una decision razonable. Para lo que gasta plata o mueve un token de otro
// sistema, no: con la bandera sin poner -un despliegue nuevo, una variable que alguien
// borro- cualquiera con la URL podria disparar corridas de GitHub Actions hasta agotar el
// plan del mes, que ya paso una vez por otro motivo. Estas puertas fallan cerradas: si no
// hay sesion, no se abren, y peor caso el dueño entra con su clave.
export async function exigirSesion(request, env, roles) {
  if (!env.SESSION_SECRET) return null;
  const rol = await readSession(request.headers.get('Cookie'), env.SESSION_SECRET);
  return rol && roles.indexOf(rol) !== -1 ? rol : null;
}

// El EQUIPO: el dueno y los tres roles de trabajo (trafficker, editor, videos). Los socios de pais NO son del equipo: ven solo su dashboard.
// Una sola lista, importada por quien la necesita, para que un rol de equipo nuevo no se quede sin una puerta (o con una de mas).
export const EQUIPO = ['owner', 'trafficker', 'editor', 'videos'];

// Para las HERRAMIENTAS que usa todo el equipo (decision del dueno, 2026-10-15: «la idea es que las herramientas las puedan usar todos sin problema»): crear ebooks, generar audios y mockups, correr el
// analisis del agente. Cualquier rol del equipo y cualquier metodo, y falla cerrado igual que exigirDueno: sin SESSION_SECRET o sin una cookie valida no abre, aunque REQUIRE_LOGIN este apagado, porque
// estos endpoints gastan plata con las llaves del dueno. No sirve para llaves, cuentas, sueldos ni configuracion (/api/config, /api/paises, /api/zapmcp, /api/saldos, /api/costos...): esos siguen con exigirDueno.
export async function exigirEquipo(request, env) { return exigirSesion(request, env, EQUIPO); }

// Para lo que el dueno ADMINISTRA pero el equipo puede MIRAR (decision del dueno, 2026-10-15: «todo lo que aparece en mi dash, le aparezca al equipo»).
// Leer (GET o HEAD) lo hace cualquier rol del equipo; cualquier otro metodo (guardar, borrar, correr un analisis que cuesta) sigue siendo SOLO del dueno.
// Falla cerrado igual que exigirDueno: sin SESSION_SECRET o sin una cookie valida no abre, aunque REQUIRE_LOGIN este apagado. No sirve para llaves, cuentas ni
// sueldos: esos endpoints (/api/config, /api/paises, /api/zapmcp, /api/apimart, /api/probar-api, /api/saldos, /api/costos...) siguen con exigirDueno.
export async function exigirLecturaEquipo(request, env) {
  if (!env.SESSION_SECRET) return null;
  const rol = await readSession(request.headers.get('Cookie'), env.SESSION_SECRET);
  if (!rol) return null;
  const m = request.method;
  if (m === 'GET' || m === 'HEAD') return EQUIPO.indexOf(rol) !== -1 ? rol : null;
  return rol === 'owner' ? rol : null;
}
