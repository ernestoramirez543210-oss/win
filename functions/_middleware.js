// Gatekeeper for every request served by Cloudflare Pages.
//
// Before this existed the whole site was public: anyone with the URL could read
// revenue, spend and ROAS for all five countries. Now each request must carry a
// signed session cookie, and each role only reaches the pages it needs.
//
// Env vars (set with scripts/Setup-Cloudflare.ps1):
//   SESSION_SECRET       - HMAC key for the session cookie (required)
//   ADMIN_PASSWORD       - owner: full access (already used by toggle-ad)
//   TRAFFICKER_PASSWORD  - el equipo (EQUIPO_VE y EQUIPO_USA, abajo) y el refresco bajo demanda
//   EDITOR_PASSWORD      - el equipo y elegir el modelo de IA de los ebooks
//   VIDEOS_PASSWORD      - editor de video: lo mismo que el equipo (desde el 2026-10-15 los tres roles de trabajo ven y usan lo mismo)
//   VE_PASSWORD          - Venezuela dashboard only (the partner's view)

// La firma de la cookie vive en _sesion.js: /api/config tambien la necesita para
// negarse por su cuenta, y una copia por archivo se arregla en un lado y sigue rota
// en el otro.
import { COOKIE, SESSION_HOURS, hmac, safeEqual, makeSession, readSession } from './_sesion.js';

// Exactly which pages each role may reach.
//
// These are EXACT paths, deliberately. Prefix matching let a role through to a path
// whose file did not exist, and Pages then answered with index.html - the full
// all-countries dashboard. Path allowlists only hold when the path maps to the file
// you think it does, so pair this with a real 404.html in the deploy.
// Objetivos del mes: la barra del menu y la pagina /objetivos. Las ve TODO el equipo -dueno, trafficker, editor y video-, para que cada quien vea cuanto
// falta y que le toca; los socios de pais NO (por eso no estan en sus listas: ven solo su dashboard). Una sola lista y se reparte por spread, para que
// un rol de equipo nuevo no se quede con la pagina y sin la ruta de datos que la alimenta (o al reves).
// /objetivo-barra.json es la cifra del mes que arma Build-Suite.ps1; /api/objetivos guarda la meta, las mini-metas y los plazos.
const OBJETIVOS = ['/objetivos', '/objetivos.html', '/api/objetivos', '/objetivo-barra.json'];

// Claude Code del equipo (/equipo): la pagina para bajar el kit con el que cada quien mejora SU espacio de trabajo con su PROPIO Claude, el .zip del kit (lo arma Build-Suite.ps1 en cada refresco desde
// equipo-kit/ y el commit que se construye) y /api/equipo, donde deja sus propuestas de cambio para que el dueno las revise. Lo ven los tres roles de equipo; los socios de pais NO (por eso no estan en sus
// listas). Una sola lista y se reparte por spread, igual que OBJETIVOS. /api/equipo SOLO guarda texto en una cola (no publica nada, no toca codigo, no lee datos del negocio): ver functions/api/equipo.js.
// Leer las propuestas y aplicarlas es del dueno, desde su PC (scripts/Revisar-Equipo.ps1), no por esta puerta.
const EQUIPO = ['/equipo', '/equipo.html', '/equipo-kit.zip', '/api/equipo'];

// EL EQUIPO VE TODA LA SUITE (decision del dueno, 2026-10-15: «todo lo que aparece en mi dash, le aparezca al equipo»). Una sola lista, repartida por spread a los tres roles de trabajo
// (trafficker, editor y videos), para que un rol no se quede con la pagina y sin la ruta de datos que la alimenta (o al reves) y para que los tres vean exactamente lo mismo.
// VER no es CAMBIAR LO QUE GOBIERNA: aqui van las paginas y las rutas de LECTURA. Lo que cambia algo que mide o gobierna todo lo demas (pausar anuncios, editar reglas, refrescar el sitio, guardar costos,
// editar el cerebro, borrar) lo decide cada endpoint por su cuenta (exigirLecturaEquipo, exigirAlguno o la contraseña de administrador) y sigue siendo de quien ya lo hacia: el dueno, y la trafficker o el editor
// segun el caso. Las herramientas que se USAN (crear ebooks, audios, mockups, pedidos del radar, el agente) no van aqui sino en EQUIPO_USA, mas abajo.
// Quedan SOLO del dueno, a proposito (no estan en ninguna lista y sus endpoints se niegan solos):
//   - /apis y /api/config, /api/apimart, /api/probar-api, /api/saldos, /api/paises, /api/zapmcp: llaves, proveedores, saldos y numeros de cuenta;
//   - /costos y /api/costos: los costos fijos de la empresa, que incluyen lo que se le paga a cada persona del equipo;
//   - cambiar lo que mide o gobierna todo lo demas: estados del radar, editar el cerebro, borrar, y la configuracion (objetivos, nombres de proyecto, la oficina).
// Las HERRAMIENTAS que gastan con las llaves del dueno (ebooks, audios, mockups, pedidos del radar, el analisis del agente) las USA todo el equipo: ver EQUIPO_USA, abajo.
// Los socios de pais NO son del equipo: no estan en esta lista y siguen viendo solo su pais.
const EQUIPO_VE = [
  ...OBJETIVOS,
  // Claude Code del equipo (/equipo): el kit, su .zip y la cola de propuestas (lo agrego otra sesion; la constante EQUIPO de arriba). Lo ven los tres roles de trabajo.
  ...EQUIPO,
  '/', '/index.html',
  // La oficina y su aviso de presencia. Leer la oficina lo hace todo el equipo; cambiarla (asignar nombres de Facebook) sigue siendo del dueno, y /api/latido solo guarda una clave por rol.
  '/oficina', '/oficina.html', '/api/oficina', '/api/latido',
  '/pipeline', '/pipeline.html', '/produccion', '/produccion.html', '/api/drive',
  '/salud', '/salud.html', '/numeros', '/numeros.html',
  // Las conexiones de WhatsApp que ZapData tiene AHORA (conectadas o no): las pide /numeros al abrirse y /oficina cada 2 min. Solo lectura, sin telefonos (solo las 4 ultimas cifras); la conexion con ZapData
  // en si (/api/zapmcp: credenciales) sigue siendo SOLO del dueno. El endpoint vuelve a comprobar el rol por su cuenta (exigirLecturaEquipo).
  '/api/zapdata-canales',
  // Si los flujos de Venta y Entrega de una oferta estan encendidos en ZapData (la guia de lanzamiento de /fabrica, paso 2). Solo lectura, sin texto de ZapData: ids y si/no. Vuelve a comprobar el rol
  // por su cuenta (exigirLecturaEquipo), como /api/zapdata-canales.
  '/api/zapdata-flujos',
  '/tablero', '/tablero.html', '/api/tablero',
  // «Revivir ofertas» (pestaña Variaciones de /tablero): pedir al radar que busque anuncios ajenos que venden lo mismo e ideas de otras industrias, y ver lo hallado. Lo usa todo el equipo;
  // borrar lo hallado es solo del dueno. El endpoint vuelve a comprobar el equipo por su cuenta (exigirEquipo, falla cerrado: pedir gasta centavos de Gemini con la llave del dueno).
  '/api/revivir',
  // «Hacerla en el servidor» (pestaña Variaciones de /tablero, 2026-11-13): el equipo ve si el servidor del editor tiene encendidas las variaciones automaticas; pedir y cancelar son solo del dueño
  // (el endpoint lo exige por su cuenta: exigirEquipo para leer, exigirDueno para POST). El VPS entra por la puerta de la llave del editor, mas abajo.
  '/api/variacion-editor',
  // Las tasas con las que se convierte cada venta: se pueden auditar, pero no se configuran (eso es del dueno, en /apis).
  '/tasas', '/tasas.html',
  // Las reglas de pausar / subir / duplicar. Verlas, y ver lo que aplicaron, lo hace todo el equipo; editarlas (PUT) sigue siendo del dueno y de la trafficker, y aprobar
  // una accion sigue necesitando la contraseña de administrador en /api/toggle-ad (esa ruta NO esta aqui). /api/cambios son los cambios de estado y presupuesto hechos desde el dash.
  '/reglas', '/reglas.html', '/api/reglas', '/reglas-auto-log.json', '/api/reglas-registro', '/api/cambios',
  '/dashboard-mexico', '/dashboard-venezuela', '/dashboard-colombia', '/dashboard-peru',
  '/dashboard-guatemala', '/dashboard-argentina', '/dashboard-ecuador', '/dashboard-bolivia',
  '/dashboard-mexico.html', '/dashboard-venezuela.html', '/dashboard-colombia.html', '/dashboard-peru.html',
  '/dashboard-guatemala.html', '/dashboard-argentina.html', '/dashboard-ecuador.html', '/dashboard-bolivia.html',
  // Cuanto costo CREAR cada proyecto (ebook y anuncios). El equipo lo ve; ponerle nombre a un proyecto (PUT) es del dueno. Los sueldos NO viven aqui sino en /costos.
  '/costos-proyectos', '/costos-proyectos.html', '/api/costos-proyectos',
  // El radar de ofertas: investigacion de mercado (anuncios publicos de otras empresas), sin cifras del negocio. Los pedidos (la lista, el pedido completo con los datos de pago, crear, avanzar y cancelar)
  // los usa todo el equipo; borrar un pedido y cambiar el estado de una oferta son solo del dueno.
  // /api/radar-prueba: con que oferta propia se prueba cada una (solo el nombre y el dia de inicio; las cifras salen solo con la llave del radar).
  '/radar', '/radar.html', '/api/radar-ofertas', '/api/radar-orden', '/api/radar-estado', '/api/radar-seguimiento', '/api/radar-manual', '/api/radar-flujo-buscar', '/api/radar-prueba',
  // El radar de canales de YouTube (yt_radar.py en el VPS): canales ajenos que se pueden modelar con IA, sin cifras del negocio. Solo se ve; el Status se cambia en la planilla.
  '/youtube', '/youtube.html', '/api/radar-youtube',
  // La seccion «App Finanzas» de /radar (app_radar.py en el VPS, 2026-11-13): apps ajenas de finanzas personales que escalan, sin cifras del negocio. Solo se ve; el Status se cambia en la planilla.
  '/api/radar-apps',
  // El creador de ebooks. La pagina y el catalogo de modelos (el costo estimado) los ve todo el equipo; crear, o sea gastar, tambien lo hace todo el equipo (sus rutas, en EQUIPO_USA, mas abajo).
  '/pdf', '/pdf.html', '/api/modelos-texto', '/api/modelos-imagen',
  // Modelar PDF: subir el PDF de un entregable ajeno y sacar una coleccion nueva en espanol. La pagina lee el PDF con pdf.js y pone las frases con sus propias letras: esos
  // archivos viven en /vendor (codigo de Mozilla y fuentes libres, sin ningun dato). Gastar -analizar y dibujar- va por /api/modelar-pdf, en EQUIPO_USA.
  '/modelar-pdf', '/modelar-pdf.html',
  '/vendor/pdfjs/pdf.min.js', '/vendor/pdfjs/pdf.worker.min.js',
  '/vendor/fuentes/great-vibes-400.woff2', '/vendor/fuentes/playfair-display-700.woff2', '/vendor/fuentes/fredoka-600.woff2',
  '/vendor/fuentes/nunito-600.woff2', '/vendor/fuentes/nunito-800.woff2',
  // Los flujos de una oferta: armarlos lo ve todo el equipo (es el cuello de botella para lanzar). La pestaña de Paises NO entra aqui: /api/paises sigue siendo solo del dueno, asi
  // que nadie mas ve un numero de cuenta. Y lo que se genera nace apagado: nada sale a hablar con clientas sin que alguien lo active.
  '/ofertas', '/ofertas.html', '/api/zapflow', '/api/zaparchivo', '/api/plantilla',
  // El estudio de voz es una pagina estatica: todo el trabajo ocurre contra Voicebox en el 127.0.0.1 de QUIEN la abre, asi que no hay llaves ni gasto nuestro detras. Los dos scripts
  // que la pagina ofrece descargar se sirven desde aqui porque el repo es privado.
  '/voces', '/voces.html', '/abrir-voces.ps1', '/voces-puente.ps1',
  // El editor de anuncios 9:16: la pagina para instalarlo en su PC y el .zip del instalador (lo arma Build-Suite.ps1 en cada refresco). Es estatica: solo guia y codigo, sin dinero ni llaves.
  '/editor-anuncios', '/editor-anuncios.html', '/editor-anuncios.zip',
  // El cerebro de ofertas: leerlo lo puede cualquiera con sesion; escribirlo lo bloquea el propio endpoint (solo el dueno).
  '/cerebro', '/cerebro.html', '/api/conocimiento',
  // El agente: ver sus ultimas recomendaciones (GET, no gasta nada) y correr el analisis (POST, cuesta centavos), que puede todo el equipo y la clave del cron. /agente-brief.json es el resumen que arma Build-Suite.ps1.
  '/agente', '/agente.html', '/agente-brief.json', '/api/agente',
  // La Fabrica de ofertas: llevar una oferta del tablero a ebooks, anuncios y flujos. La pagina y /api/fabrica (proyectos, recetas de edicion y pedidos de anuncio) las usa todo el equipo; no gasta nada por su
  // cuenta (producir ebooks, audios y mockups lo hace el navegador con las herramientas de EQUIPO_USA, cada una con su confirmacion de costo). Cambiar cuanta libertad tiene Claudia (`aprobacion`) y borrar
  // un proyecto son solo del dueno: lo decide el propio endpoint. /api/fabrica-editor NO esta en ninguna lista: es la puerta de Claudia, con su propia llave (mas abajo).
  '/fabrica', '/fabrica.html', '/api/fabrica',
  // Programar en Facebook las campañas de una oferta nueva (paso 4 de la guia): la ruta llega al equipo porque el boton esta en la guia, pero el endpoint solo abre al dueño y a la trafficker (exigirSesion).
  '/api/fabrica-campanas',
  // La fuente de las banderas, que cargan todas las paginas: sin esta ruta el equipo veia las letras del pais en vez de la bandera. Es un archivo de fuente, sin ningun dato.
  '/banderas.woff2',
];

// LAS HERRAMIENTAS LAS USA TODO EL EQUIPO (decision del dueno, 2026-10-15: «la idea es que las herramientas las puedan usar todos sin problema»). No solo se VEN: se usan. Son las rutas de lo que
// produce o gasta con las llaves del dueno: crear ebooks (brief, avatar, plan, paginas, hojas, proyecto, leer una pagina de ventas, convertir mensajes en flujos, el gasto), aprender del cerebro,
// generar audios, guiones y mockups, escribir un entregable y correr el analisis del agente. Cada endpoint vuelve a comprobar el rol por su cuenta (exigirEquipo, estricto: sin sesion valida no abre ni con
// el login apagado; o exigirAlguno con EQUIPO), y los socios de pais no estan en ninguna de estas listas. Siguen SOLO del dueno: llaves, proveedores, saldos, paises y la conexion con ZapData
// (/apis, /api/config, /api/apimart, /api/probar-api, /api/saldos, /api/paises, /api/zapmcp), Costos (sueldos), las instrucciones del mockup (/api/mockup-plantilla: cambian todos los mockups y «Restaurar»
// borra su version; para GENERAR mockups no hace falta, /api/mockup-oferta las lee en el servidor), borrar (proyectos, hojas y pedidos), los estados del radar y editar el cerebro. Cambiar el modelo: dueno y editor.
const EQUIPO_USA = [
  '/api/aprender', '/api/ebook-brief', '/api/ebook-avatar', '/api/ebook-plan', '/api/ebook-pagina', '/api/ebook-proyecto', '/api/ebook-hojas',
  '/api/leer-url', '/api/clicchat', '/api/gasto',
  '/api/audio', '/api/audio-copy', '/api/audio-oferta', '/api/mockup-oferta', '/api/contenido',
  '/api/ofertas-aprendizaje',
  '/api/modelar-pdf',
];

const ACCESS = {
  owner:      null,                        // null = everything
  // Ademas de lo de todo el equipo, la trafficker pide el refresco bajo demanda. Va aqui y no en la lista de todos: es quien mira el gasto durante el dia y necesita cifras de hace minutos.
  // El endpoint ya se limita solo a una corrida por hora, asi que esto no es por consumo sino por no darle a nadie un boton que dispara trabajo que no le corresponde.
  trafficker: [...EQUIPO_VE, ...EQUIPO_USA, '/api/refrescar'],
  // El editor arma los entregables (ebooks, flujos, audios): ahora lo hace todo el equipo con las rutas de EQUIPO_USA. Lo que sigue siendo suyo y del dueno es ELEGIR el modelo (el costo de cada ebook):
  // /api/modelo-elegido es angosto a proposito, solo toca el modelo elegido, nunca llaves ni proveedores. Las llaves (/apis, /api/config) siguen siendo solo del dueno.
  editor:     [...EQUIPO_VE, ...EQUIPO_USA,
               // Elegir el modelo (el costo de cada ebook) es del dueno y del editor: /api/modelo-elegido solo toca el modelo elegido, nunca llaves ni proveedores.
               '/api/modelo-elegido'],
  // El editor de VIDEO nace aparte del editor de contenido porque son dos personas y una sola contrasena compartida significa que el dia que se va uno hay que cambiarsela tambien al otro.
  // Ve y usa lo mismo que todo el equipo (EQUIPO_VE y EQUIPO_USA); lo unico que no tiene es lo que ya es solo del dueno.
  videos:     [...EQUIPO_VE, ...EQUIPO_USA,
               // Conectar un editor de anuncios nuevo (2026-11-10, tarjeta «Conectar este editor» de /editor-anuncios): le da las dos lineas del .env (direccion del dash y la llave del editor).
               // Es de quien instala editores: el dueño (ya entra a todo) y el editor de video. El endpoint lo vuelve a exigir por su cuenta (exigirSesion con owner y videos).
               '/api/editor-conexion'],
  // Los socios se agregan abajo, uno por pais, desde PAISES.
};

// Socios: cada uno ve SU pais y nada mas. Ni el tablero completo, ni la torre, ni los
// otros seis dashboards. Un socio de Argentina no tiene por que ver la facturacion de
// Colombia, y con una lista exacta no hay forma de que llegue por accidente.
//
// Los tres sitios donde vive un rol de pais -que rutas abre, que contrasena lo activa y
// donde aterriza- salen de esta sola tabla. Estaban escritos a mano y solo existian dos
// paises de los siete: agregar uno pedia acordarse de tocar los tres, y olvidar el de las
// rutas deja un rol que entra y no puede abrir nada.
const PAISES = {
  mx: 'mexico',    ve: 'venezuela', co: 'colombia', pe: 'peru',
  gt: 'guatemala', ar: 'argentina', ec: 'ecuador', bo: 'bolivia',
};

// El socio NO abre dashboard-<pais> (el que ven el dueno y la trafficker, con los nombres reales de las ofertas) sino socio-<pais>: el mismo dash de su pais, pero con
// las ofertas como «Oferta 1», «Oferta 2»... (build-dashboard.ps1, MODO SOCIO de la plantilla). Con la lista exacta no hay forma de que llegue al otro por accidente.
for (const [rol, pais] of Object.entries(PAISES)) {
  ACCESS[rol] = ['/socio-' + pais, '/socio-' + pais + '.html'];
}

// El enlace de siempre de un socio (dashboard-<pais>) ya no es su pagina: se le lleva a la nueva en vez de decirle que no tiene permiso, que parecia un error suyo.
function paginaDelSocio(rol, p) {
  const pais = PAISES[rol];
  return pais && (p === '/dashboard-' + pais || p === '/dashboard-' + pais + '.html') ? '/socio-' + pais : p;
}

// Cada pagina dibuja el menu con lo que este rol puede abrir. Sin esto el menu tendria
// que adivinar y acabaria mostrando enlaces que llevan a un 403.
function menuPara(role) {
  const rules = propio(ACCESS, role) ? ACCESS[role] : [];
  return { rol: role, puede: rules === null ? null : rules };
}

// Solo cuentan las propiedades PROPIAS de las tablas de roles: una cookie bien firmada con un rol llamado «constructor», «__proto__» o «toString» no debe encontrar nada por herencia
// (antes la prueba `role in ACCESS` daba verdadero y el middleware reventaba con un error en vez de negar limpio; fallaba cerrado, pero por accidente).
function propio(tabla, clave) { return Object.prototype.hasOwnProperty.call(tabla, clave); }





function allowed(role, pathname) {
  if (!propio(ACCESS, role)) return false;
  const rules = ACCESS[role];
  if (rules === null) return true;                    // owner
  return rules.includes(pathname.replace(/\/+$/, '') || '/');
}

// Plantilla: freno contra adivinar contrasenas. La regla de rate limiting del WAF de Cloudflare no se puede poner sobre <proyecto>.pages.dev
// (va dentro de un dominio propio), asi que el sitio se frena solo: cuenta los fallos por IP en la cache de Cloudflare (no gasta escrituras
// de KV) y, tras 10 seguidos, cierra esa IP 15 minutos; ademas cada fallo tarda un segundo. La cache es por centro de datos: frena, no es un
// candado exacto. Si la cache no esta disponible, queda solo la pausa de un segundo.
const FRENO_MAX = 10, FRENO_SEG = 900;
async function frenoLogin(request) {
  const ip = request.headers.get('CF-Connecting-IP') || 'sin-ip';
  const clave = new Request('https://freno-login.invalid/' + encodeURIComponent(ip));
  const cache = (typeof caches !== 'undefined' && caches.default) || null;
  let n = 0;
  if (cache) { try { const r = await cache.match(clave); if (r) n = Number(await r.text()) || 0; } catch { n = 0; } }
  const guardar = async (v) => { if (cache) { try { await cache.put(clave, new Response(String(v), { headers: { 'Cache-Control': 'max-age=' + FRENO_SEG } })); } catch { /* sin cache */ } } };
  return {
    cerrado: n >= FRENO_MAX,
    fallo: async () => { await guardar(n + 1); await new Promise((r) => setTimeout(r, 1000)); },
    acierto: async () => { if (cache && n) { try { await cache.delete(clave); } catch { /* sin cache */ } } },
  };
}

function roleFor(password, env) {
  // Plantilla: una contrasena de menos de 14 caracteres no abre nada (ni la del dueno): se adivina en poco tiempo. Setup-Cloudflare.ps1 no las acepta.
  const larga = (c) => typeof c === 'string' && c.length >= 14;
  if (larga(env.ADMIN_PASSWORD)      && safeEqual(password, env.ADMIN_PASSWORD))      return 'owner';
  if (larga(env.TRAFFICKER_PASSWORD) && safeEqual(password, env.TRAFFICKER_PASSWORD)) return 'trafficker';
  if (larga(env.EDITOR_PASSWORD)     && safeEqual(password, env.EDITOR_PASSWORD))     return 'editor';
  if (larga(env.VIDEOS_PASSWORD)     && safeEqual(password, env.VIDEOS_PASSWORD))     return 'videos';
  // Cada socio con su env var: VE_PASSWORD, AR_PASSWORD, CO_PASSWORD... Sin configurar,
  // ese rol simplemente no puede entrar.
  for (const rol of Object.keys(PAISES)) {
    const clave = env[rol.toUpperCase() + '_PASSWORD'];
    if (larga(clave) && safeEqual(password, clave)) return rol;
  }
  return null;
}

const HOME = {
  owner: '/', trafficker: '/', editor: '/', videos: '/',
};
for (const [rol, pais] of Object.entries(PAISES)) HOME[rol] = '/socio-' + pais;
const casaDe = (rol) => (propio(HOME, rol) ? HOME[rol] : '/');   // la pagina de inicio de un rol; un rol que no existe va a la raiz, nunca a algo heredado

// La frase de la pantalla de entrada.
//
// Esta pagina es PUBLICA: la ve cualquiera que abra la URL, sin contrasena. Por eso lleva
// una frase y ni una sola cifra del negocio. El numero -pedidos, anuncios, ebooks- se
// muestra despues de entrar, en el menu, y elegido segun el rol de quien entro.
//
// Una distinta en cada carga. Eran una por dia -para que fallar la contrasena no pareciera
// una tragamonedas- pero al verlas a diario la repeticion pesa mas que ese detalle, asi
// que manda el banco grande: con sesenta, dos seguidas iguales son raras.
const FRASES_ENTRADA = [
  'Lo que haces hoy decide el mes.',
  'Nadie construyo nada grande en un solo dia.',
  'Empezar cuesta mas que seguir.',
  'La constancia le gana al talento cuando el talento no es constante.',
  'Un dia mas de trabajo es un dia menos de distancia.',
  'Se avanza por acumulacion, no por milagros.',
  'Lo dificil no es tener la idea, es sostenerla.',
  'Hoy tambien cuenta.',
  'Tu competencia tambien esta cansada.',
  'Mejor hecho hoy que perfecto nunca.',
  'Cada oferta que pruebas te acerca a la que funciona.',
  'La suerte llega mas seguido a quien esta trabajando.',
  'No hace falta ganarlas todas, hace falta seguir jugando.',
  'Lo que ayer costaba, hoy te sale solo.',
  'El que mide, mejora. El que adivina, repite.',
  'Una oferta que no se prueba no falla: tampoco gana.',
  'Lo urgente grita; lo importante espera sentado.',
  'El dinero llega donde ya habia trabajo esperandolo.',
  'Nadie recuerda el dia que te costo. Recuerdan que lo hiciste.',
  'Hacerlo mal y corregir gana a planearlo perfecto y no empezar.',
  'Si algo funciona, hazlo mas veces antes de buscar lo siguiente.',
  'Tres cosas terminadas valen mas que diez empezadas.',
  'El negocio crece cuando dejas de improvisar lo que ya sabes hacer.',
  'Un cliente bien atendido vuelve; uno bien vendido no siempre.',
  'Lo barato que funciona le gana a lo caro que impresiona.',
  'La disciplina es acordarte de lo que querias cuando no tenias ganas.',
  'Nada rinde como terminar lo que ya estaba a medias.',
  'El mejor momento fue hace un ano. El segundo mejor es hoy.',
  'Aprender rapido vale mas que acertar a la primera.',
  'Lo que no se anota, se olvida y se vuelve a pagar.',
  'Copiar el angulo no es copiar: es no empezar de cero.',
  'Cansado se trabaja igual; desanimado no.',
  'La calidad se nota antes de que la expliques.',
  'Cobrar bien empieza por entregar algo que valga.',
  'Un dia flojo no borra un mes bueno.',
  'Todo el mundo empieza sin saber. La diferencia es cuanto aguanta.',
  'Al que entrega primero le creen antes.',
  'Es mas facil vender otra vez a quien ya te compro.',
  'Lo que hoy te parece obvio, hace un ano te tomo una semana.',
  'Si esperas motivacion, trabajas la mitad de los dias.',
  'Ordenar el escritorio no es avanzar. Avanzar es avanzar.',
  'Las cuentas claras evitan discusiones y sorpresas.',
  'Una idea sin fecha es un deseo.',
  'Nadie compra lo que no entiende en cinco segundos.',
  'La prisa daña lo que la paciencia arregla gratis.',
  'Vender no es convencer: es encontrar a quien ya queria.',
  'Se aprende mas de una oferta que falla que de una que gana por suerte.',
  'Deja el trabajo listo para el que llegue manana, aunque seas tu.',
  'Lo que se repite sin pensar se hace mal. Revisalo cada tanto.',
  'Un buen dia empieza sabiendo que vas a hacer primero.',
  'No confundas estar ocupado con estar avanzando.',
  'Si no lo puedes explicar simple, todavia no lo entendiste.',
  'El descanso tambien es parte del trabajo bien hecho.',
  'Tu mejor anuncio hoy sera tu peor anuncio en seis meses.',
  'Lo que funciona en un pais casi siempre funciona en otro.',
  'Precio bajo no salva a un producto flojo.',
  'Termina el borrador. Se arregla despues.',
  'Lo que no revisas, alguien lo descubre por ti.',
  'Cada cosa que automatizas te devuelve un dia al mes.',
  'Se gana con lo aburrido hecho todos los dias.',
];
function FRASE_HOY() {
  return FRASES_ENTRADA[Math.floor(Math.random() * FRASES_ENTRADA.length)];
}

// La pagina que se pidio ANTES de que apareciera la pantalla de acceso.
//
// Sin esto el enlace no significaba nada: la contrasena decidia sola el destino, asi que
// abrir el enlace de Argentina y teclear la clave de Colombia entraba a Colombia. El socio
// termina en un pais que no es el suyo y sin ninguna senal de que se equivoco de clave.
//
// Se acepta solo una ruta de ESTE sitio -una sola barra al principio y caracteres de
// ruta- para que la pantalla de entrada no sirva de trampolin a otro dominio. La raiz y
// las rutas de API se descartan: la raiz es "entrar por la puerta de siempre", donde cada
// rol tiene que poder aterrizar en su propia casa.
function destinoSeguro(p) {
  if (typeof p !== 'string' || !/^\/[A-Za-z0-9._\-/]*$/.test(p)) return '';
  if (p.startsWith('//') || p.startsWith('/api/')) return '';
  const limpio = p.replace(/\/+$/, '');
  return limpio === '' ? '' : limpio;
}

function loginPage(msg, destino) {
  return new Response(`<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acceso</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#020916;color:#f3f5fa;
      font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}
 /* border-box en la tarjeta y en el campo: con width:100% mas relleno (content-box) la
    tarjeta media 398px y el campo 28px mas que el boton, y a 390px la pagina se salia 8px.
    398px es el ancho que ya tenia en escritorio, ahora con el relleno adentro. */
 form{box-sizing:border-box;background:#061026;border:1px solid #132751;border-radius:16px;padding:30px 28px;width:min(92vw,398px)}
 h1{margin:0 0 4px;font-size:20px;font-weight:800;letter-spacing:-.4px;
    background:linear-gradient(90deg,#4d8bff,#2f6bff 55%,#ff5be1);
    -webkit-background-clip:text;background-clip:text;color:transparent}
 p{margin:0 0 18px;color:#979fb3;font-size:12.5px}
 input{box-sizing:border-box;width:100%;padding:11px 13px;border-radius:9px;border:1px solid #132751;
       background:#091530;color:#f3f5fa;font-size:14px;margin-bottom:11px}
 input:focus{outline:none;border-color:#4d8bff}
 button{width:100%;padding:11px;border:0;border-radius:9px;cursor:pointer;font-size:14px;font-weight:650;
        background:linear-gradient(90deg,#4d8bff,#2f6bff);color:#020916}
 .err{color:#ff8fb0;font-size:12.5px;margin-top:11px;min-height:17px}
 /* La frase vive dentro de la tarjeta, no en un popup: esta pantalla se ve diez veces al
    dia y un cuadro que hay que cerrar antes de trabajar deja de ser un premio muy rapido.
    Aqui se lee mientras se escribe la contrasena, y no cuesta ni un clic. */
 .frase{margin:0 0 18px;padding:12px 14px;border-left:2px solid #2f6bff;background:#091530;
        border-radius:0 9px 9px 0;color:#b7bece;font-size:12.5px;line-height:1.5;font-style:italic}
</style>
<form id="f">
  <h1>Ventas + Ads</h1>
  <p>Acceso restringido</p>
  <div class="frase">${FRASE_HOY()}</div>
  <input id="p" type="password" placeholder="Contraseña" autofocus autocomplete="current-password">
  <button type="submit">Entrar</button>
  <div class="err" id="e">${msg || ''}</div>
</form>
<script>
document.getElementById('f').addEventListener('submit', async ev => {
  ev.preventDefault();
  const e = document.getElementById('e'); e.textContent = '...';
  const r = await fetch('/api/login', {method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({password: document.getElementById('p').value, destino: ${JSON.stringify(destinoSeguro(destino))}})});
  const j = await r.json().catch(()=>({}));
  if (j.ok) location.href = j.redirect || '/';
  else { e.textContent = j.error || 'Contraseña incorrecta'; document.getElementById('p').select(); }
});
</script>`, { status: 401, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

// Sesion abierta, pero de otro pais. Lleva las dos salidas que se necesitan: ir a lo suyo,
// o cerrar sesion para entrar con la contrasena correcta -sin esto habria que borrar la
// cookie a mano para poder cambiar de socio en el mismo navegador-.
function paginaSinPermiso(casa) {
  return new Response(`<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sin permiso</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#020916;color:#f3f5fa;
      font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}
 div{box-sizing:border-box;background:#061026;border:1px solid #132751;border-radius:16px;padding:30px 28px;width:min(92vw,398px)}
 h1{margin:0 0 8px;font-size:18px;font-weight:800;letter-spacing:-.3px}
 p{margin:0 0 18px;color:#979fb3;font-size:12.5px}
 a,button{display:block;width:100%;box-sizing:border-box;padding:11px;border:0;border-radius:9px;
      cursor:pointer;font:inherit;font-weight:650;text-align:center;text-decoration:none;
      background:linear-gradient(90deg,#4d8bff,#2f6bff);color:#020916;margin-bottom:9px}
 button{background:none;border:1px solid #132751;color:#979fb3;font-weight:500}
</style>
<div>
  <h1>Esta página no es de tu cuenta</h1>
  <p>La sesión abierta pertenece a otro país. Entra con la contraseña de este para verlo.</p>
  <a href="${casa}">Ir a mi tablero</a>
  <button id="s">Salir y entrar con otra contraseña</button>
</div>
<script>
document.getElementById('s').addEventListener('click', async () => {
  await fetch('/api/logout', {method:'POST'});
  location.reload();
});
</script>`, { status: 403, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

// Las cabeceras de seguridad, en UN solo sitio.
//
// Vivian duplicadas en las dos ramas -sitio abierto y sitio con login- y ya habian
// empezado a divergir: la de abajo tenia Cache-Control y la de arriba no, asi que con el
// login apagado las paginas con facturacion de siete paises eran cacheables por cualquier
// proxy intermedio. Una lista de cabeceras copiada es una lista que se queda a medias.
//
// La CSP lleva 'unsafe-inline' porque los tableros son un solo archivo con su <style> y su
// <script> dentro -es lo que los hace compartibles por URL sin nada mas- y quitarlo los
// dejaria en blanco. Aun asi el resto vale: sin object-src no hay plugins, sin base-uri no
// se puede reescribir a donde apuntan las rutas relativas, y connect-src 'self' impide que
// un script inyectado -por ejemplo por el nombre de una campaña de Facebook- se lleve los
// datos a otro dominio, que es justo el escape que importa aqui.
function blindar(resp) {
  const out = new Response(resp.body, resp);
  out.headers.set('Cache-Control', 'no-store');   // fuera de cachés compartidas
  out.headers.set('X-Frame-Options', 'DENY');
  out.headers.set('X-Content-Type-Options', 'nosniff');
  out.headers.set('Referrer-Policy', 'no-referrer');
  // Un año, que es el mínimo que piden las listas de precarga de HSTS.
  out.headers.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  out.headers.set('Permissions-Policy', 'geolocation=(), microphone=(), camera=(), payment=(), usb=()');
  out.headers.set('Content-Security-Policy', [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "media-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; '));
  return out;
}

// Lo que la app instalable necesita PODER pedir sin sesion.
//
// El navegador busca el manifest y los iconos para decidir si la pagina es instalable, y lo
// hace antes de que exista cualquier cookie. Con 401 no falla con un error: simplemente no
// aparece la opcion de instalar, y uno se queda mirando el menu del navegador buscandola.
// El worker igual: se registra desde la pagina, pero el navegador lo pide como recurso
// suelto y una actualizacion suya puede caer sin sesion viva.
//
// Ninguno de estos cinco lleva un solo dato del negocio: son un JSON con el nombre y el
// color de la app, cuatro dibujos de barras, y codigo que ya viaja en cada pagina.
const PUBLICOS = new Set([
  '/manifest.json', '/sw.js',
  '/icon-192.png', '/icon-512.png', '/icon-maskable-512.png', '/apple-touch-icon.png',
]);

// Cualquier excepcion de una ruta /api/ sale como JSON legible, no como la pagina HTML de
// error de Cloudflare. Con la pagina HTML, el navegador intentaba leerla como JSON y el usuario
// veia "Unexpected token '<', '<!DOCTYPE'... is not valid JSON" -en el mockup, al crear flujos
// de ZapData, al guardar un ebook- sin ninguna pista de que KV habia agotado su tope diario de
// escrituras. El detalle de cualquier otro error se queda en los registros, no se manda al
// cliente: algunas rutas son publicas y un mensaje interno no es para cualquiera.
export async function onRequest(context) {
  try {
    return await atender(context);
  } catch (e) {
    const path = new URL(context.request.url).pathname;
    if (path.indexOf('/api/') !== 0) throw e;
    const msg = String((e && e.message) || e);
    console.error('[' + path + '] ' + msg);
    const lleno = /KV (put|delete)\(\) limit exceeded/i.test(msg);
    return blindar(new Response(JSON.stringify({
      ok: false,
      error: lleno
        ? 'Cloudflare agotó el tope diario de escrituras y no puede guardar nada hasta las 7 p. m. (hora Colombia). Lo que se genera con IA sí funciona; lo que se guarda, no.'
        : 'Error del servidor. Reintenta; si se repite, avisa.',
    }), { status: lleno ? 503 : 500, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } }));
  }
}

async function atender(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const path = url.pathname;
  if (PUBLICOS.has(path)) return blindar(await next());
  // Tambien las respuestas de error pasan por blindar(). Un 401 sin cabeceras es un 401
  // que se puede meter en un iframe y cachear, y sobre todo es un 401 sobre el que no se
  // puede comprobar nada desde fuera: la unica respuesta que da el sitio sin sesion es
  // esta, asi que si no las lleva, no hay forma de verificar que estan puestas.
  const json = (s, o, extra = {}) => blindar(new Response(JSON.stringify(o),
    { status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...extra } }));

  // Plantilla: el sitio SIEMPRE pide contrasena (por rol). No hay variable que lo deje abierto: las ventas, el gasto, los datos de cobro de
  // /ofertas y las llaves de IA no pueden quedar a la vista por un paso que alguien se salto.
  const requiereLogin = true;
  // Con el login apagado no hay cookie que leer, pero el menu igual pregunta quien es.
  // Se contesta 'abierto' para que dibuje todo: las paginas sensibles se defienden
  // solas (/api/config exige sesion de dueno pase lo que pase con esta bandera).
  if (!requiereLogin && path === '/api/whoami') {
    return json(200, { ok: true, rol: 'abierto', puede: null });
  }
  if (!requiereLogin && path !== '/api/login' && path !== '/api/logout') {
    const r = blindar(await next());
    // Que el sitio este abierto no puede ser un detalle silencioso: con esta bandera sin
    // poner, cualquiera con la URL lee la facturacion de los siete paises. Se dice en una
    // cabecera para que se vea desde fuera -y para que lo cace Auditar-Seguridad.ps1- sin
    // tener que adivinar como quedo configurado el proyecto en Cloudflare.
    r.headers.set('X-Sitio-Abierto', 'sin-login: el sitio de la plantilla siempre pide sesion');
    return r;
  }

  // Without a secret every session would be forgeable - fail closed instead.
  if (!env.SESSION_SECRET) {
    return json(503, { ok: false, error: 'SESSION_SECRET no configurado' });
  }

  if (path === '/api/login') {
    if (request.method !== 'POST') return json(405, { ok: false, error: 'Method not allowed' });
    let body;
    try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
    const freno = await frenoLogin(request);   // plantilla: ver frenoLogin
    if (freno.cerrado) return json(429, { ok: false, error: 'Demasiados intentos fallidos. Espera 15 minutos.' }, { 'Retry-After': String(FRENO_SEG) });
    const role = roleFor(body && body.password, env);
    if (!role) { await freno.fallo(); return json(401, { ok: false, error: 'Contraseña incorrecta' }); }
    await freno.acierto();
    // Se pidio una pagina concreta y esta contrasena no la abre: no se entra. Antes se
    // entraba igual y se aterrizaba en OTRO pais, que es peor que un error: parece que
    // funciono. Sin cookie, asi que el intento no deja sesion abierta de nada.
    const destino = paginaDelSocio(role, destinoSeguro(body && body.destino));
    if (destino && !allowed(role, destino)) {
      return json(403, { ok: false, error: 'Esa contraseña no abre esta página.' });
    }
    const token = await makeSession(role, env.SESSION_SECRET);
    // ?bienvenida=1 es la senal para que el menu muestre la franja de bienvenida una sola
    // vez, con un dato del rol que acaba de entrar. El propio menu limpia la URL despues,
    // asi que recargar la pagina no la vuelve a disparar.
    return json(200, { ok: true, role, redirect: (destino || casaDe(role)) + '?bienvenida=1' }, {
      'Set-Cookie': `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_HOURS * 3600}`,
    });
  }

  if (path === '/api/logout') {
    return json(200, { ok: true }, { 'Set-Cookie': `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0` });
  }

  // (plantilla) sin puerta interna con la firma de las sesiones: aqui no hay agente que la use

  // Lectura de numeros para un proyecto de afuera. Pasa sin cookie porque quien consulta es
  // un servidor, no un navegador con sesion; se defiende con su PROPIA clave (CLAVE_DATOS),
  // comparada en tiempo constante dentro del endpoint, y solo sabe leer. Sin esa variable
  // configurada contesta 503: nunca queda abierto.
  // (plantilla) sin puerta a /api/datos: esa funcion no viene en la plantilla

  // El refresco de GitHub lee las reglas para evaluarlas. Mismo criterio que /api/datos: llega sin
  // cookie, solo lee, y el endpoint exige su PROPIA llave (CLAVE_REGLAS); sin ella en la cabecera sigue pidiendo sesion.
  if (path === '/api/reglas' && request.method === 'GET' && request.headers.get('x-clave-reglas')) return next();
  // Y el mismo refresco APLICA lo que las reglas deciden, por /api/reglas-ejecutar. Esta puerta si mueve plata, asi que el endpoint no se fia de este paso: exige la llave por su cuenta
  // (en tiempo constante), vuelve a comprobar cada accion contra las reglas guardadas y contra Facebook, y no tiene puerta de sesion. Sin la cabecera sigue pidiendo sesion... y aun con ella, sin llave 401.
  if (path === '/api/reglas-ejecutar' && (request.method === 'GET' || request.method === 'POST') && request.headers.get('x-clave-reglas')) return next();
  // Y saca la foto del consumo real de APIMart para el cuadre de /costos-proyectos. El endpoint exige la misma llave por su cuenta, no recibe datos y no devuelve cifras.
  if (path === '/api/apimart-cuadre' && request.method === 'POST' && request.headers.get('x-clave-reglas')) return next();
  // Y pregunta cada hora que order bumps tienen los flujos de venta de ZapData, para avisar del que el dash no mide (2026-11-11). Solo GET y solo ?bumps=1; el endpoint exige la misma llave por su
  // cuenta (en tiempo constante) y con ella solo lee: precio, producto y nombres de flujos, sin telefonos ni datos de pago. Sin la cabecera sigue pidiendo sesion.
  if (path === '/api/zapdata-flujos' && request.method === 'GET' && url.searchParams.get('bumps') === '1' && request.headers.get('x-clave-reglas')) return next();
  // El radar del VPS lee los estados que el dueño eligio en el dash para pasarlos a la planilla, y avisa como le fue. Mismo criterio que /api/reglas: llega sin cookie, el endpoint exige su
  // PROPIA llave (CLAVE_RADAR, en tiempo constante; sin ella contesta 503) y esa llave solo LEE los estados y deja un aviso: no cambia ningun estado. Sin la cabecera sigue pidiendo sesion.
  if (path === '/api/radar-estado' && (request.method === 'GET' || request.method === 'POST') && request.headers.get('x-clave-radar')) return next();
  // El VPS deja cada 20 minutos el reporte de sus bots para /oficina (/api/oficina-servidor). Misma llave (CLAVE_RADAR) y mismo criterio, solo POST: el endpoint la exige por su cuenta en
  // tiempo constante (sin ella 503), rearma el reporte con solo horas, categorias fijas y conteos, y con ella no lee nada. Sin la cabecera, o con otro metodo, sigue pidiendo sesion.
  // (plantilla) sin puerta a /api/oficina-servidor: esa funcion no viene en la plantilla
  // El mismo radar manda cada dia una «foto» de cada oferta que el dueño vigila (anuncios activos, sobrevivientes por edad...) a /api/radar-seguimiento. Misma llave (CLAVE_RADAR) y mismo criterio, pero aun mas
  // angosto: solo POST. El endpoint la exige por su cuenta en tiempo constante (sin ella 503), acota cada campo y con ella no lee ni borra nada. Sin la cabecera, o con otro metodo, sigue pidiendo sesion.
  // (plantilla) sin puerta a /api/radar-seguimiento: esa funcion no viene en la plantilla
  // Y lee las ofertas que el equipo agrego a mano (/api/radar-manual) para fotografiar las que estan en «Vigilar» o «Testiando». Misma llave y mismo criterio, solo GET: el endpoint la exige por su cuenta en tiempo
  // constante (sin ella 503) y con ella no agrega, no corrige ni quita nada. Sin la cabecera, o con otro metodo, sigue pidiendo sesion.
  if (path === '/api/radar-manual' && request.method === 'GET' && request.headers.get('x-clave-radar')) return next();
  // Y lee como le va a NUESTRA prueba de cada oferta (gasto, ventas, ROAS y costo por venta de la oferta propia con la que el dueño dice que se prueba) para pasarlo a la planilla. Misma llave y mismo
  // criterio, solo GET: el endpoint la exige por su cuenta en tiempo constante (sin ella 503) y con ella no cambia ningun enlace. Sin la cabecera, o con otro metodo, sigue pidiendo sesion.
  // (plantilla) sin puerta a /api/radar-prueba: esa funcion no viene en la plantilla
  // El radar atiende los pedidos de «Buscar el WhatsApp» (/api/radar-flujo-buscar): lee los pendientes (GET) y deja el resultado (POST). Misma llave (CLAVE_RADAR) y mismo criterio: el endpoint la exige por su cuenta
  // en tiempo constante (sin ella 503), acota todo a digitos y solo toca esos pedidos. Sin la cabecera pide sesion del equipo.
  // (plantilla) sin puerta a /api/radar-flujo-buscar: esa funcion no viene en la plantilla
  // Y atiende los pedidos de «Revivir ofertas» (/api/revivir): lee los pendientes (GET ?pendientes=1) y deja lo hallado (POST). Misma llave (CLAVE_RADAR) y mismo criterio: el endpoint la exige por su
  // cuenta en tiempo constante (sin ella 503), con ella solo lee pendientes o escribe el resultado de un pedido pendiente (acotado, enlaces rehechos con digitos). Sin la cabecera pide sesion del equipo.
  if (path === '/api/revivir' && (request.method === 'GET' || request.method === 'POST') && request.headers.get('x-clave-radar')) return next();
  // El radar de canales de YouTube manda, al terminar cada corrida, la lista entera de las pestañas YT LARGOS y YT SHORTS a /api/radar-youtube. Misma llave (CLAVE_RADAR) y mismo criterio que el
  // seguimiento: solo POST, el endpoint la exige por su cuenta en tiempo constante (sin ella 503), acota cada campo y con ella no lee ni borra nada. Sin la cabecera, o con otro metodo, pide sesion del equipo.
  // (plantilla) sin puerta a /api/radar-youtube: esa funcion no viene en la plantilla
  // El radar de apps de finanzas (app_dash.py, cada 5 min si cambio) manda la pestaña APP FINANZAS entera a /api/radar-apps. Mismo criterio que /api/radar-youtube: solo POST con la cabecera,
  // el endpoint exige la llave en tiempo constante (sin CLAVE_RADAR 503), acota cada campo y con ella no lee ni borra nada. Sin la cabecera, o con otro metodo, pide sesion del equipo.
  // (plantilla) sin puerta a /api/radar-apps: esa funcion no viene en la plantilla
  // El editor de anuncios (un PC con Claude) anota lo que gasta en APIMart por /api/editor-costos. Mismo criterio que el radar: llega sin cookie, el endpoint exige su PROPIA llave (CLAVE_EDITOR, en tiempo
  // constante; sin ella contesta 503) y esa llave SOLO sirve para ANOTAR costos nuevos (POST): no lee ni borra nada. Sin la cabecera, o con otro metodo, sigue pidiendo sesion.
  if (path === '/api/editor-costos' && request.method === 'POST' && request.headers.get('x-clave-editor')) return next();
  // Claudia (el editor de anuncios) trae de la Fabrica de ofertas los pedidos de anuncio que debe editar y avisa como van. Misma llave (CLAVE_EDITOR) y mismo criterio: llega sin cookie, el endpoint
  // la exige por su cuenta en tiempo constante (sin ella 503) y SOLO sirve para esta cola: leer los pedidos abiertos y avisar su estado. No toca ventas, gasto, cuentas ni datos de clientes. Sin la cabecera pide sesion.
  if (path === '/api/fabrica-editor' && (request.method === 'GET' || request.method === 'POST') && request.headers.get('x-clave-editor')) return next();
  // El mismo editor pide a APIMart (imagenes, clips, subidas de referencias, tareas y texto) por el dash, que guarda la llave de APIMart, lleva los topes y anota el gasto (2026-11-10). Misma llave
  // (CLAVE_EDITOR) y mismo criterio: llega sin cookie, el endpoint la exige por su cuenta en tiempo constante (sin ella 503) y solo gasta dentro de los topes (functions/_editor-apimart.js). Sin la cabecera pide sesion.
  if (path === '/api/editor-apimart' && (request.method === 'GET' || request.method === 'POST') && request.headers.get('x-clave-editor')) return next();
  // Y el servidor del editor (VPS) toma las variaciones que el dueño pidio «Hacerla en el servidor», avisa como van y sube el indice de nuestros videos en Drive (/api/variacion-editor, 2026-11-13).
  // Misma llave (CLAVE_EDITOR) y mismo criterio: llega sin cookie, el endpoint la exige por su cuenta en tiempo constante (sin ella 503) y con la cabecera NUNCA cae a la sesion; con la llave solo
  // lee los pedidos pendientes, avisa su estado (transiciones fijas, enlaces rehechos con el id de Drive) y guarda el indice. No borra filas. Sin la cabecera pide sesion del equipo.
  if (path === '/api/variacion-editor' && (request.method === 'GET' || request.method === 'POST') && request.headers.get('x-clave-editor')) return next();

  // El PDF que un flujo de ZapData le manda a la clienta lo descarga el servidor de
  // ZapData, que no tiene forma de iniciar sesion aqui. Pasa SOLO la descarga de un
  // archivo concreto: subir y listar siguen pidiendo sesion, y el identificador es de 32
  // caracteres al azar, asi que no se puede adivinar ni recorrer. Es material de marketing
  // que de todos modos se le manda a quien compra.
  if (path === '/api/zaparchivo' && (request.method === 'GET' || request.method === 'HEAD') && url.searchParams.get('id')) return next();

  // La vuelta del permiso de ZapData. Llega como navegacion desde zapdata.co, o sea de
  // otro sitio, y la cookie de sesion es SameSite=Strict: no viaja en ese salto, asi que
  // exigirla aqui haria imposible conectar. Lo que protege la puerta es el `state`, que
  // genero este servidor, vive diez minutos en KV y se quema al usarlo: el endpoint no
  // canjea nada si no coincide exacto. Pasa SOLO esta ruta, que no hace nada mas;
  // /api/zapmcp, donde se configura, sigue siendo del dueno.
  if (path === '/api/zapvuelta' && request.method === 'GET') return next();

  const role = await readSession(request.headers.get('Cookie'), env.SESSION_SECRET);

  // Quien soy: lo pide el menu al cargar para pintar solo lo que este rol abre.
  if (path === '/api/whoami') {
    if (!role) return json(401, { ok: false, error: 'No autenticado' });
    return json(200, { ok: true, ...menuPara(role) });
  }

  if (!role) {
    // API callers get JSON; browsers get the login screen.
    if (path.startsWith('/api/')) return json(401, { ok: false, error: 'No autenticado' });
    // blindar() tambien aqui: esta es LA respuesta que ve quien llega sin sesion, y salia
    // sin ninguna cabecera -sin la anti-iframe el formulario de acceso se podia meter en
    // una pagina ajena-. Auditar-Seguridad.ps1 -Url lo cazo al medir lo que llega por la red.
    return blindar(loginPage('', path));
  }
  if (!allowed(role, path)) {
    if (path.startsWith('/api/')) return json(403, { ok: false, error: 'Sin permiso' });
    // El socio con su enlace de siempre llega a su pagina nueva.
    const suya = paginaDelSocio(role, path.replace(/\/+$/, ''));
    if (suya !== path.replace(/\/+$/, '')) return blindar(Response.redirect(new URL(suya, url).toString(), 302));
    // Pidio el dashboard de otro pais con la sesion de este. Mandarlo callado al suyo hace
    // creer que el enlace estaba mal escrito; se dice que esa sesion no lo abre y se le
    // deja el suyo a un clic.
    if (path.startsWith('/dashboard-') || path.startsWith('/socio-')) return blindar(paginaSinPermiso(casaDe(role)));
    return blindar(Response.redirect(new URL(casaDe(role), url).toString(), 302));
  }

  // Only the owner may flip ads on or off.
  if (path === '/api/toggle-ad' && role !== 'owner' && role !== 'trafficker') {
    return json(403, { ok: false, error: 'Sin permiso' });
  }

  return blindar(await next());
}
