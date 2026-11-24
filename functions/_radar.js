// Lectura y limpieza de la pestaña OFERTAS de la planilla RADAR OFERTAS (la que llena el radar de Brasil).
//
// Funciones PURAS, sin red ni KV: las usa /api/radar-ofertas y las prueba scripts/probar-radar.cjs con filas reales
// y con las trampas conocidas (comillas, saltos de linea dentro de una celda, columnas movidas, texto ajeno).
//
// Dos reglas que mandan sobre todo lo demas:
//   1. Todo lo que viene de la planilla es TEXTO DE TERCEROS. Los nombres de anunciante, el angulo, el entregable y la
//      justificacion salen de anuncios de otras empresas, resumidos por un modelo. Aqui solo se limpia y se recorta;
//      quien lo pinta lo escapa, y quien lo mete en un prompt lo marca como material y no como instruccion.
//   2. Las columnas se buscan por NOMBRE, no por posicion (igual que hace el radar al escribir): el dueño puede
//      mover o agregar columnas sin romper esto.

// «testiando» (2026-10-16) es el sexto: lo elige el dueño en el dash (que ademas manda la oferta al tablero de Produccion) y el radar lo escribe en esta columna como a los otros cinco.
export const ESTADOS = ['nuevo', 'vigilar', 'ganadora', 'descartada', 'no testeada', 'testiando'];

// Nombre canonico -> encabezados aceptados (ya sin tildes ni mayusculas).
export const ALIAS = {
  fecha: ['fecha'],
  palabra: ['palabra clave'],
  anunciante: ['anunciante'],
  dominio: ['dominio'],
  anuncios: ['anuncios activos'],
  dias: ['dias en el aire'],
  nicho: ['nicho'],
  precio: ['precio origen', 'precio'],
  entregable: ['entregable'],
  embudo: ['embudo / checkout', 'embudo'],
  hueco: ['hueco colombia'],
  linkAnuncio: ['link del anuncio'],
  linkPagina: ['link de la pagina'],
  angulo: ['angulo'],
  nota: ['nota'],
  justificacion: ['justificacion'],
  status: ['status', 'estado'],
  // Opcionales (2026-10-22, boton «Flujo de WhatsApp»): las llena el radar o el dueño a mano. Una planilla que no las trae sigue valiendo y no cuentan en `faltan`.
  idPagina: ['id de la pagina', 'id pagina', 'id de pagina', 'page id'],
  idVideo: ['id del video', 'id video', 'video id'],
  // 2026-10-30: quien puso el ID del video. Solo «google» (el radar lo hallo en una busqueda) o «manual» (lo pego alguien) abren la publicacion; vacio = el ID viene del archivo del anuncio y Facebook no lo abre.
  origenFlujo: ['origen del flujo'],
  // 2026-11-05 (radar db05521): una linea por anunciante en espanol con su pagina en la Biblioteca y su WhatsApp, pagina o Facebook. Ver «anunciantesEsDe».
  linksEs: ['anunciantes en espanol (links)', 'anunciantes en espanol'],
  // 2026-11-06: en que paises se vende la oferta (el mismo anunciante, o el mismo angulo con 30+ dias) y una linea por pais con sus links. Ver «paisesVendenDe».
  paises: ['paises que venden'],
  paisesDetalle: ['paises que venden (detalle)'],
};
const OPCIONALES = ['idPagina', 'idVideo', 'origenFlujo', 'linksEs', 'paises', 'paisesDetalle'];
// Sin estas cuatro no es la pestaña OFERTAS (por ejemplo, se publico PALABRAS-CLAVE por error).
const IMPRESCINDIBLES = ['anunciante', 'status', 'linkAnuncio', 'nota'];

// Sin tildes, en minusculas y con espacios simples. Las marcas combinantes (U+0300 a U+036F) se arman por CODIGO y no se
// escriben como caracteres sueltos ni como escapes: un caracter invisible en un patron se corrompe sin hacer ruido al
// reescribir el archivo con ciertas herramientas (ya paso con el patron de tildes de Build-Suite.ps1).
const MARCAS = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');
// Caracteres de control (todos menos tabulador y salto de linea): se arman igual, por codigo.
const CONTROL = new RegExp('[' + String.fromCharCode(0) + '-' + String.fromCharCode(8) + String.fromCharCode(11) + String.fromCharCode(12) +
  String.fromCharCode(14) + '-' + String.fromCharCode(31) + String.fromCharCode(127) + ']', 'g');
export function plegar(s) {
  return String(s == null ? '' : s).normalize('NFD').replace(MARCAS, '')
    .toLowerCase().replace(/\s+/g, ' ').trim();
}

// ---------- CSV ----------
// RFC 4180: comillas dobles escapadas con otra comilla, comas y saltos de linea DENTRO de una celda entre comillas,
// BOM al inicio, \r\n o \n. Devuelve las filas como arreglos de texto; las filas totalmente vacias se descartan.
export function parsearCsv(texto) {
  let t = String(texto == null ? '' : texto);
  if (t.charCodeAt(0) === 0xFEFF) t = t.slice(1);
  const filas = [];
  let fila = [], campo = '', entreComillas = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (entreComillas) {
      if (c === '"') {
        if (t[i + 1] === '"') { campo += '"'; i++; } else entreComillas = false;
      } else campo += c;
    } else if (c === '"' && campo === '') {
      entreComillas = true;
    } else if (c === ',') {
      fila.push(campo); campo = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && t[i + 1] === '\n') i++;
      fila.push(campo); campo = '';
      filas.push(fila); fila = [];
    } else campo += c;
  }
  if (campo !== '' || fila.length) { fila.push(campo); filas.push(fila); }
  // Las filas vacias del FINAL sobran; las del medio se conservan para que el numero de fila siga siendo el de la planilla
  // (la pantalla dice "fila 18" y el dueño la busca ahi).
  while (filas.length && filas[filas.length - 1].every((c) => String(c).trim() === '')) filas.pop();
  return filas;
}

// ---------- campos sueltos ----------
const SIN_DATO = /^(no visible|no disponible|n\/a|sin dato|-+)\b/i;

// Texto de una celda: sin caracteres de control, espacios simples y con tope.
export function limpio(v, max) {
  const s = String(v == null ? '' : v).replace(CONTROL, ' ')
    .replace(/[ \t]+/g, ' ').replace(/\s*[\r\n]\s*/g, ' ').trim();  // tambien el \r suelto (2026-11-17: un «\r4. …» del radar parecia otro paso de la orden del editor)
  return max && s.length > max ? s.slice(0, max - 1).trimEnd() + '…' : s;
}

// '' cuando la celda dice "no visible" y variantes: asi la pantalla no pinta "no visible" como si fuera un dato.
export function visible(v, max) {
  const s = limpio(v, max);
  return SIN_DATO.test(s) ? '' : s;
}

export function numeroDe(v) {
  const m = String(v == null ? '' : v).match(/\d+/);
  return m ? Number(m[0]) : null;
}

// dd/mm/aaaa (o aaaa-mm-dd) -> aaaa-mm-dd; '' si no es una fecha.
export function fechaIso(v) {
  const s = String(v == null ? '' : v).trim();
  let m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (m) {
    const d = Number(m[1]), mes = Number(m[2]);
    return d >= 1 && d <= 31 && mes >= 1 && mes <= 12 ? `${m[3]}-${String(mes).padStart(2, '0')}-${String(d).padStart(2, '0')}` : '';
  }
  m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : '';
}

export function estadoCanon(v) {
  const t = plegar(v);
  return ESTADOS.includes(t) ? t : '';
}

// El radar escribe la justificacion como "[radar.py] ...3+2+2+2+1=10. ..." y la revision humana como "2+2+1+2+1 = 8.":
// cinco puntajes (hueco en Colombia 0-3, dias 0-2, anuncios 0-2, entregable 0-2, validacion 0-1) y su suma.
export function desgloseDe(justificacion) {
  const m = String(justificacion || '').slice(0, 400).match(/(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*=\s*(\d{1,2})/);
  if (!m) return null;
  const p = [1, 2, 3, 4, 5].map((i) => Number(m[i]));
  return { puntos: p, total: Number(m[6]) };
}
export const TOPES_DESGLOSE = [3, 2, 2, 2, 1];
export const ETIQUETAS_DESGLOSE = ['Hueco en Colombia', 'Días en el aire', 'Anuncios activos', 'Entregable modelable', 'Validación'];

// "[sin experiencia · metodo paso a paso] Aprende a ..." -> { promesas: [...], texto: 'Aprende a ...' }
// y el "pt:" de cuando el radar no pudo traducir: se quita y se marca el idioma.
export function anguloDe(v) {
  let s = limpio(v, 500);
  let promesas = [];
  const m = s.match(/^\[([^\]]{1,240})\]\s*(.*)$/);
  if (m) {
    promesas = m[1].split(/\s*[·•|]\s*/).map((x) => x.trim()).filter(Boolean).slice(0, 6);
    s = m[2];
  }
  let idioma = 'es';
  // «pt:» cuando el radar no pudo traducir; «es:» en las de oro (2026-11-05), que ya vienen en espanol.
  const pref = s.match(/^(pt|es):\s*/i);
  if (pref) { s = s.slice(pref[0].length); idioma = pref[1].toLowerCase(); }
  return { promesas, texto: s, idioma };
}

// La justificacion trae, separadas por " | ", la del radar y las notas de las revisiones; dentro de cada una pueden venir
// "Revisar: ..." (lo que el radar dudo) y "No testeada: ..." (por que el dueño o la revision la dejaron sin modelar).
export function notasDe(justificacion) {
  const partes = limpio(justificacion, 4000).split(/\s+\|\s+/);
  const principal = partes[0] || '';
  const revisiones = partes.slice(1, 4).map((x) => limpio(x, 600)).filter(Boolean);
  const todo = partes.join(' | ');
  const avisos = [];
  const r = todo.match(/Revisar:\s*([^|]*)/i);
  if (r) r[1].split(/;\s*/).map((x) => limpio(limpio(x).replace(/\.$/, ''), 240)).filter(Boolean).slice(0, 4).forEach((x) => avisos.push(x));
  const nt = todo.match(/No testeada:\s*([^|]*)/i);
  const motivoNoTesteada = nt ? limpio(limpio(nt[1]).replace(/\.$/, ''), 300) : '';
  return {
    principal: limpio(principal, 900),
    revisiones, avisos, motivoNoTesteada,
    origen: /^\[radar\.py\]/.test(principal) ? 'radar' : 'manual',
    porAngulo: /encontrada por angulo\/promesa/i.test(principal),
  };
}

// Colombia, como semaforo. El TEXTO manda sobre el desglose porque las revisiones corrigen el texto
// ("6 anunciantes comparables ... nota 7 -> 6") y dejan el desglose original.
//   libre: 0 anunciantes · poca: 1 o 2 · saturada: 3 o mas, o lo dice · validar: sin revisar o sin confirmar · sd: sin dato
export function claseColombia(hueco, desglose) {
  // Las busquedas van entre comillas simples («busqueda 'recetas sin grasas saturadas'»): no cuentan como palabras clave.
  const t = plegar(hueco).replace(/'[^']{0,80}'/g, "''");
  if (/pendiente/.test(t) || /no confirmado/.test(t) || /no se pudo revisar/.test(t)) return 'validar';      // «no se pudo revisar Colombia (Meta no contesto...)»
  if (/saturad/.test(t)) return 'saturada';
  const m = t.match(/^\s*(\d+)\s*\+?/);
  if (m) {
    const n = Number(m[1]);
    return n === 0 ? 'libre' : n <= 2 ? 'poca' : 'saturada';
  }
  if (desglose && desglose.rubrica === 'oro') {
    // En la nota del oro Colombia es el ULTIMO punto (1 = nadie mas la vende alla). Un 0 tambien sale cuando Meta no contesto: sin el texto no se sabe cuantos hay.
    return desglose.puntos[4] >= 1 ? 'libre' : 'validar';
  }
  if (desglose) {
    const p = desglose.puntos[0];
    return p >= 3 ? 'libre' : p === 0 ? 'saturada' : 'poca';
  }
  return 'sd';
}

// ---------- oro en espanol (2026-11-05, radar db05521) ----------
// El radar tambien busca «oro»: ofertas de WhatsApp con 30+ dias en Colombia, Mexico, Peru, Argentina y Venezuela, que ya estan en espanol y se modelan mas rapido.
// Las marca en «Palabra clave» con «oro XX: <busqueda>» y su justificacion empieza con «[radar.py] ORO en espanol (Mexico): ...». Solo valen estos cinco paises.
export const PAISES_ORO = { CO: 'Colombia', MX: 'México', PE: 'Perú', AR: 'Argentina', VE: 'Venezuela' };
const CODIGO_ORO = { colombia: 'CO', mexico: 'MX', peru: 'PE', argentina: 'AR', venezuela: 'VE' };
export function oroDe(palabra, justificacion) {
  const m = String(palabra || '').trim().match(/^oro\s+([a-z]{2})\s*:/i);
  let c = m ? m[1].toUpperCase() : '';
  if (!PAISES_ORO[c]) {
    const j = String(justificacion || '').slice(0, 120).match(/^\s*\[radar\.py\]\s*ORO en espa(?:n|ñ)ol\s*\(([^)]{2,20})\)/i);
    c = j ? CODIGO_ORO[plegar(j[1])] || '' : '';
  }
  return PAISES_ORO[c] ? { pais: c, nombre: PAISES_ORO[c] } : null;
}
// «palabra clave» sin el «oro XX:» del principio: la busqueda tal como se hizo.
export function busquedaOro(palabra) { return String(palabra || '').replace(/^\s*oro\s+[a-z]{2}\s*:\s*/i, ''); }
// La nota del oro tiene su propia rubrica (radar.py, nota_oro): «nota oro 3+2+1+1+0=7 (días + anuncios + entregable + validada + Colombia libre)».
// Los dias pesan mas (30-59 = 2, 60-179 = 3, 180+ = 4); no se lee con las etiquetas de Brasil, donde el primer punto es el hueco en Colombia.
export const TOPES_DESGLOSE_ORO = [4, 2, 2, 1, 1];
export const ETIQUETAS_DESGLOSE_ORO = ['Días en el aire', 'Anuncios activos', 'Entregable modelable', 'Validación', 'Colombia libre'];
export function desgloseOroDe(justificacion) {
  const m = String(justificacion || '').slice(0, 1500).match(/nota oro\s*(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*=\s*(\d{1,2})/i);
  if (!m) return null;
  const p = [1, 2, 3, 4, 5].map((i) => Number(m[i]));
  if (p.some((x, i) => x > TOPES_DESGLOSE_ORO[i])) return null;
  return { puntos: p, total: Number(m[6]), rubrica: 'oro' };
}

// Familia visual de la oferta (icono y color de la tarjeta). Mira el nicho primero y despues el resto del texto.
const FAMILIAS = [
  { id: 'manual', nombre: 'Manualidades', re: /manualidad|croch|crochet|bordad|costura|molde|estampa|papercraft|artesan|amigurumi|tejid|patron|riscos?\b|peste/ },
  { id: 'recetas', nombre: 'Recetas', re: /receta|bolo|torta|panes?\b|gluten|reposter|cocina|dulce|postre|navidad.*(cena|comida)/ },
  { id: 'salud', nombre: 'Salud y fitness', re: /fitness|entrenam|nutric|dieta|salud|psicolog|fisioterap|anamnes|terapia|ejercicio|treino|evaluacion fisica|bienestar|ventosa/ },
  { id: 'fe', nombre: 'Fe y espiritualidad', re: /espiritual|evangel|biblic|sermon|pastor|cristian|igreja|iglesia|ministerio|oracion|devocional|dinamicas.*(grupo|mujer)/ },
  { id: 'educ', nombre: 'Educación', re: /educ|profes|caligraf|matematic|calculo|apostil|concurso|escolar|ensenanza|pedagog|material(es)? para|infantil/ },
  { id: 'negocio', nombre: 'Negocios', re: /negocio|finanz|contad|icms|vender|mercado libre|ingreso|emprend|marketing|publicidad/ },
  { id: 'musica', nombre: 'Música', re: /music|cavaquinho|worship|cifra|cancion|guitarra|violao/ },
];
export function familiaDe(...textos) {
  const t = plegar(textos.join(' '));
  for (const f of FAMILIAS) if (f.re.test(t)) return { id: f.id, nombre: f.nombre };
  return { id: 'otros', nombre: 'Otros' };
}

// ---------- enlaces ----------
// El enlace al anuncio se RECONSTRUYE desde su id: asi, pase lo que pase con el texto de la celda, lo que llega a un href
// es siempre https://www.facebook.com/ads/library/?id=<digitos>.
export function adIdDe(link) {
  const m = String(link || '').match(/[?&]id=(\d{5,25})\b/);
  return m ? m[1] : '';
}
export function enlaceAnuncio(link) {
  const id = adIdDe(link);
  return id ? `https://www.facebook.com/ads/library/?id=${id}` : '';
}
// El enlace de la PAGINA de un anunciante en la Biblioteca («Ver todos los anuncios», con los filtros que quien lo copio le puso: pais, solo activos, orden por impresiones, hasta una fecha).
// Se RECONSTRUYE igual que el de un anuncio: solo el numero de la pagina y los filtros conocidos con un valor del tipo esperado; nada mas del texto pegado llega a un href ni a la orden de Claudia.
const FILTROS_PAGINA = [
  ['active_status', /^(active|inactive|all)$/], ['ad_type', /^all$/], ['country', /^(ALL|[A-Z]{2})$/], ['is_targeted_country', /^(true|false)$/],
  ['media_type', /^(all|image|video|meme|image_and_meme|none)$/], ['sort_data[mode]', /^[a-z_]{3,40}$/], ['sort_data[direction]', /^(asc|desc)$/],
  ['start_date[min]', /^\d{4}-\d{2}-\d{2}$/], ['start_date[max]', /^\d{4}-\d{2}-\d{2}$/],
];
export function enlaceAnunciante(link) {
  const s = String(link || '').trim();
  let u;
  try { u = new URL(/^https?:\/\//i.test(s) ? s : 'https://' + s); } catch { return ''; }
  if ((u.protocol !== 'https:' && u.protocol !== 'http:') || u.username || u.password) return '';
  if (!/^(www\.|m\.|web\.)?facebook\.com$/i.test(u.hostname) || !/\/ads\/library\/?$/.test(u.pathname)) return '';
  const id = u.searchParams.get('view_all_page_id') || '';
  if (!/^\d{5,25}$/.test(id)) return '';
  const partes = [];
  for (const [k, re] of FILTROS_PAGINA) { const v = u.searchParams.get(k); if (v && re.test(v)) partes.push(k + '=' + v); }
  partes.push('search_type=page', 'view_all_page_id=' + id);
  return 'https://www.facebook.com/ads/library/?' + partes.join('&');
}
// Lo que acepta el campo «anuncio original»: el de UN anuncio (gana si trae un id) o el de la pagina de un anunciante.
export function enlaceMeta(link) { return enlaceAnuncio(link) || enlaceAnunciante(link); }
export function esEnlaceAnunciante(link) { return !!link && !adIdDe(link) && !!enlaceAnunciante(link); }
// La pagina de la oferta es de un tercero: solo http(s), sin credenciales en la direccion, sin redes privadas.
const PRIVADOS = [/^localhost$/i, /^\[?::1\]?$/, /^0\./, /^10\./, /^127\./, /^169\.254\./, /^192\.168\./, /^172\.(1[6-9]|2\d|3[01])\./, /\.local$/i, /\.internal$/i];
export function enlacePagina(v) {
  const s = limpio(v, 300);
  if (!s || SIN_DATO.test(s)) return '';
  let u;
  try { u = new URL(/^https?:\/\//i.test(s) ? s : 'https://' + s); } catch { return ''; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
  if (u.username || u.password) return '';
  if (!/\./.test(u.hostname) || PRIVADOS.some((p) => p.test(u.hostname))) return '';
  return u.toString().slice(0, 300);
}

// Flujo de WhatsApp (2026-10-22, pedido del dueño): la PUBLICACION del anuncio en la pagina del anunciante, donde esta el boton que abre su WhatsApp.
// Se arma con el id de la pagina y el id del VIDEO del anuncio: facebook.com/permalink.php?story_fbid={VIDEO}&id={PAGINA}. Los dos ids vienen de la planilla (los
// escribe el radar o el dueño, o sea texto de terceros): solo valen si son digitos y el enlace se RECONSTRUYE aqui, asi nada mas de la celda llega a un href.
export function idNumerico(v) {
  const s = String(v == null ? '' : v).trim();
  return /^\d{5,25}$/.test(s) ? s : '';
}
export function enlaceFlujo(idPagina, idVideo) {
  const p = idNumerico(idPagina), v = idNumerico(idVideo);
  return p && v ? `https://www.facebook.com/permalink.php?story_fbid=${v}&id=${p}` : '';
}

// ---------- anunciantes en espanol con sus links (2026-11-05, radar db05521 y 781270f) ----------
// Pedido del dueño: «cuando encuentre anunciantes en Colombia, tenga tambien los links para yo ir a verlos, porque posiblemente de ahi mismo se puedan sacar los entregables».
// La celda trae una linea por anunciante (hasta 6):
//   «[Este anunciante: ]Nombre (CO, 45 días): <su pagina en la Biblioteca>[ | WhatsApp: https://wa.me/57... | página: https://... | Facebook (botón de WhatsApp): https://www.facebook.com/...]»
// y a veces una linea final «[busqueda de nuevo dd/mm/aaaa: ...]», o una sola frase («ninguno en las busquedas en espanol»).
// Los nombres son de terceros y las direcciones tambien: cada enlace se RECONSTRUYE o se valida aqui (solo https, solo Facebook y WhatsApp, o una pagina de venta https
// que no sea de una red privada); el que no pasa se pierde y el anunciante queda sin ese boton.
const WHATSAPP_NUM = /^\d{8,15}$/;
function urlHttps(v) {
  let u;
  try { u = new URL(String(v == null ? '' : v).trim()); } catch { return null; }
  return u.protocol === 'https:' && !u.username && !u.password && !u.port ? u : null;
}
export function enlaceWhatsapp(v) {
  const u = urlHttps(v); if (!u) return '';
  const h = u.hostname.toLowerCase();
  if (h === 'wa.me') { const m = u.pathname.match(/^\/\+?(\d{8,15})\/?$/); return m ? 'https://wa.me/' + m[1] : ''; }
  if (h === 'api.whatsapp.com') { const p = String(u.searchParams.get('phone') || '').replace(/^\+/, ''); return /^\/send\/?$/.test(u.pathname) && WHATSAPP_NUM.test(p) ? 'https://wa.me/' + p : ''; }
  if (h === 'chat.whatsapp.com') { const m = u.pathname.match(/^\/(?:invite\/)?([A-Za-z0-9]{10,40})\/?$/); return m ? 'https://chat.whatsapp.com/' + m[1] : ''; }
  return '';
}
// La pagina de Facebook del anunciante (ahi esta su boton de WhatsApp): solo una ruta simple, sin parametros.
export function enlaceFacebook(v) {
  const u = urlHttps(v); if (!u || u.search || u.hash) return '';
  if (!/^(www\.|m\.|web\.)?facebook\.com$/i.test(u.hostname)) return '';
  // «/people/Recetas-F%C3%A1ciles/<id de la pagina>/»: letras, digitos, . _ - y escapes %XX (el URL ya codifica las tildes), hasta cuatro tramos.
  // (Sin un id de ejemplo completo: functions/ va en el kit del equipo y Empaquetar-Equipo.ps1 lo niega si trae una cifra de 14+ digitos.)
  return /^(\/(?:[\w.-]|%[0-9A-Fa-f]{2}){1,150}){1,4}\/?$/.test(u.pathname) ? 'https://www.facebook.com' + u.pathname : '';
}
// La pagina de venta de un anunciante: https y nada mas (las reglas de `enlacePagina`: sin credenciales ni redes privadas).
export function enlaceVenta(v) {
  const s = String(v == null ? '' : v).trim();
  if (!/^https:\/\//i.test(s) || s.length > 500) return '';
  const u = urlHttps(s);
  if (!u || !/\./.test(u.hostname) || PRIVADOS.some((p) => p.test(u.hostname))) return '';
  const url = u.toString().replace(/'/g, '%27');
  return url.length <= 600 && !/[\s"<>]/.test(url) ? url : '';
}
export const ES_MAX = 6;
const LINEA_ES = /^(Este anunciante:\s*)?(.{1,80}?)\s+\(([A-Z]{2}),\s*(\d{1,5})\s*d[ií]as?\):\s*(\S+)(.*)$/;
const CONTACTO_ES = /^\s*\|\s*(WhatsApp|p[aá]gina|Facebook)(\s*\(bot[oó]n de WhatsApp\))?\s*:\s*(\S+)\s*$/i;
// La cola « | WhatsApp: ... » / « | página: ... » / « | Facebook (botón de WhatsApp): ... » de una linea -> { tipo, url, host, boton } o null.
// El tipo lo decide la DIRECCION, no la etiqueta: un «página:» que apunta a WhatsApp o a Facebook se pinta como tal.
// Si la linea trae varias colas seguidas (« | WhatsApp: ... | página: ... »), vale la primera que sirva.
function contactoDe(cola) {
  for (const trozo of String(cola || '').split(/(?=\|)/).slice(0, 4)) {
    const c = trozo.match(CONTACTO_ES);
    if (!c) continue;
    const wa = enlaceWhatsapp(c[3]), fb = !wa && enlaceFacebook(c[3]), venta = !wa && !fb && /^p/i.test(c[1]) && enlaceVenta(c[3]);
    if (wa) return { tipo: 'whatsapp', url: wa, host: '', boton: false };
    if (fb) return { tipo: 'facebook', url: fb, host: '', boton: !!c[2] };
    if (venta) return { tipo: 'pagina', url: venta, host: hostDe(venta), boton: false };
  }
  return null;
}
// Una linea que es solo una nota («[revisado ...]», aunque traiga corchetes dentro) -> su texto; si no, null.
function notaDe(l) { return l.length > 2 && l[0] === '[' && l[l.length - 1] === ']' ? l.slice(1, -1) : null; }
// «frase [nota]» -> { frase, nota } sin expresiones regulares (la de antes era cuadratica con muchos «[»); sin nota al final -> { frase: l, nota: '' }.
function fraseYNota(l) {
  const i = l[l.length - 1] === ']' ? l.lastIndexOf('[') : -1;
  return i >= 0 ? { frase: l.slice(0, i).trim(), nota: l.slice(i + 1, -1) } : { frase: l, nota: '' };
}
// Las lineas de una celda de texto libre: tope de largo ANTES de las expresiones, sin caracteres de control, espacios simples.
function lineasDe(v, max) {
  return String(v == null ? '' : v).slice(0, 8000).normalize('NFC').replace(CONTROL, ' ').split(/\r\n|\r|\n/)
    .map((l) => l.slice(0, 1000).replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, max);
}
// texto de la celda -> null (vacia) o { anunciantes:[{ nombre, pais, dias, este, biblioteca, contacto:{ tipo, url, host, boton } | null }], texto, nota }
export function anunciantesEsDe(v) {
  const lineas = lineasDe(v, 12);
  if (!lineas.length) return null;
  const anunciantes = [], textos = [], notas = [];
  for (const l of lineas) {
    const n = notaDe(l);
    if (n !== null) { notas.push(limpio(n, 300)); continue; }
    const m = l.match(LINEA_ES);
    if (m) {
      if (anunciantes.length >= ES_MAX) continue;
      const a = { nombre: limpio(m[2], 60), pais: m[3], dias: Number(m[4]), este: !!m[1], biblioteca: enlaceAnunciante(m[5]), contacto: contactoDe(m[6]) };
      if (a.nombre) anunciantes.push(a);
      continue;
    }
    // Una frase («ninguno en Colombia con 'x' [busqueda de nuevo 05/11/2026]»): el texto y, si la trae, su nota entre corchetes.
    const f = fraseYNota(l);
    if (f.frase) textos.push(limpio(f.frase, 300));
    if (f.nota) notas.push(limpio(f.nota, 300));
  }
  return { anunciantes, texto: textos.slice(0, 2).join(' '), nota: notas.slice(0, 2).join(' · ') };
}

// ---------- paises que venden (2026-11-06) ----------
// Pedido del dueño: «un filtro que me diga si la oferta se esta vendiendo en varios paises, puede ser el mismo anunciante o mismo angulo de ventas. Si un mismo angulo de ventas
// se esta vendiendo en varios paises, es una oferta que vende». El radar (radar.py, paises_de_oferta) revisa BR, CO, MX, PE, AR, VE, CL, EC, GT, BO y DO y escribe dos columnas:
//   «Países que venden»            «BR, MX, CO»: codigos separados por coma, el de la oferta primero. Uno solo = solo se vende alli. Vacia = sin revisar todavia.
//   «Países que venden (detalle)»  una linea por pais de mas:
//       «GT: este anunciante, mismo anuncio: <su pagina en la Biblioteca>»                       (la misma pagina corre el mismo anuncio alla)
//       «CL: mismo anuncio — Nombre, 45 días: <Biblioteca>[ | contacto]»                          (otra pagina corre el mismo anuncio, 30+ dias)
//       «MX: este anunciante (12 anuncios activos): <su pagina en la Biblioteca>»                  (la misma pagina tiene anuncios alla)
//       «CO: mismo ángulo — Nombre, 45 días: <Biblioteca>[ | WhatsApp: https://wa.me/57... | página: https://... | Facebook (botón de WhatsApp): https://...]»
//     (la Biblioteca puede faltar si no se hallo la pagina) y al final una nota gris «[revisado dd/mm/aaaa: ...]»; o toda la celda «solo en BR [revisado ...]».
// Solo valen los codigos de esta lista (una palabra de dos letras suelta, como «EN», no es un pais). Nombres de terceros y enlaces como en «anunciantesEsDe»: texto, y cada enlace reconstruido o validado.
export const NOMBRES_PAIS = {
  BR: 'Brasil', CO: 'Colombia', MX: 'México', PE: 'Perú', AR: 'Argentina', VE: 'Venezuela', EC: 'Ecuador', GT: 'Guatemala', BO: 'Bolivia', CL: 'Chile', PY: 'Paraguay',
  UY: 'Uruguay', CR: 'Costa Rica', PA: 'Panamá', DO: 'República Dominicana', SV: 'El Salvador', HN: 'Honduras', NI: 'Nicaragua', PR: 'Puerto Rico', US: 'Estados Unidos',
  ES: 'España', PT: 'Portugal',
};
export const PAISES_MAX = 12;
// «BR, MX, CO» -> ['BR', 'MX', 'CO']: solo codigos conocidos, sin repetir, en el orden de la celda. Cuenta solo una palabra ENTERA de dos letras en MAYUSCULA (asi las
// escribe el radar): en un texto suelto, «Países:» no es Panamá, ni «ni», «es» o «ve» son Nicaragua, España o Venezuela (revision independiente, 2026-11-06).
export function codigosPaisDe(v) {
  const out = [];
  String(v == null ? '' : v).slice(0, 300).split(/[\s,;/]+/).forEach((c) => {
    if (/^[A-Z]{2}$/.test(c) && NOMBRES_PAIS[c] && out.indexOf(c) < 0 && out.length < PAISES_MAX) out.push(c);
  });
  return out;
}
// Grupos: 1 pais · 2 «anuncio» (mismo anuncio) · 3 anuncios activos · 4 Biblioteca (puede venir vacia) · 5 cola de contacto
const PV_ESTE = /^([A-Za-z]{2})\s*:\s*este anunciante\s*(?:,\s*mismo\s+(anuncio)\s*)?(?:\(\s*(\d{1,6})\s*anuncios?(?:\s+activos?)?\s*\))?\s*(?::\s*([^\s|]*))?\s*(\|.*)?$/i;
// Grupos: 1 pais · 2 angulo|producto|anuncio · 3 nombre · 4 dias · 5 Biblioteca (puede venir vacia) · 6 cola de contacto
const PV_ANGULO = /^([A-Za-z]{2})\s*:\s*mismo\s+([aá]ngulo|producto|anuncio)\s*[—–-]+\s*([^|]{1,120}?)(?:\s*,\s*(\d{1,5})\s*d[ií]as?)?\s*(?::\s*([^\s|]*))?\s*(\|.*)?$/i;
const PV_OTRA = /^([A-Z]{2})\s*:\s*(.+)$/;      // en MAYUSCULA: «No: hay datos» o «Es: ...» son frases, no paises
// La Biblioteca de un pais: la pagina del anunciante o un anuncio, solo si la direccion es https de facebook.com (adIdDe busca «?id=» en CUALQUIER texto).
function enlaceBiblioteca(v) {
  const s = String(v == null ? '' : v).trim();
  return /^https:\/\/(www\.|m\.|web\.)?facebook\.com\/ads\/library\/?\?/i.test(s) ? enlaceMeta(s) : '';
}
// texto de las dos celdas -> null (las dos vacias: el radar aun no la reviso) o
//   { codigos:['BR','MX','CO'], lineas:[{ pais, tipo:'este'|'angulo'|'otro', que, anunciante, dias, anuncios, biblioteca, contacto, texto }], texto, nota }
export function paisesVendenDe(codigosTxt, detalleTxt) {
  const codigos = codigosPaisDe(codigosTxt);
  const lineas = lineasDe(detalleTxt, PAISES_MAX + 4);
  if (!codigos.length && !lineas.length) return null;
  const filas = [], textos = [], notas = [];
  for (const l of lineas) {
    const n = notaDe(l);
    if (n !== null) { notas.push(limpio(n, 300)); continue; }
    let m = l.match(PV_ESTE), p = m && m[1].toUpperCase();
    if (m && NOMBRES_PAIS[p]) {
      if (filas.length < PAISES_MAX) filas.push({ pais: p, tipo: 'este', que: m[2] ? 'anuncio' : '', anunciante: '', dias: null, anuncios: m[3] ? Number(m[3]) : null, biblioteca: enlaceBiblioteca(m[4]), contacto: contactoDe(m[5]), texto: '' });
      continue;
    }
    m = l.match(PV_ANGULO); p = m && m[1].toUpperCase();
    if (m && NOMBRES_PAIS[p]) {
      if (filas.length < PAISES_MAX) filas.push({ pais: p, tipo: 'angulo', que: /^p/i.test(m[2]) ? 'producto' : /^anu/i.test(m[2]) ? 'anuncio' : 'ángulo', anunciante: limpio(m[3], 60), dias: m[4] ? Number(m[4]) : null, anuncios: null,
        biblioteca: enlaceBiblioteca(m[5]), contacto: contactoDe(m[6]), texto: '' });
      continue;
    }
    // Otra forma con «XX: ...» (por si el radar cambia la redaccion): el texto sin direcciones, y su Biblioteca y su contacto si los trae con la forma conocida.
    m = l.match(PV_OTRA); p = m && m[1].toUpperCase();
    if (m && !NOMBRES_PAIS[p]) continue;      // «ZZ: ...»: un pais que no es de la lista no se pinta (ni como texto suelto con su direccion)
    if (m) {
      const partes = m[2].split(/\s+(?=\|)/), lib = (partes[0].match(/https?:\/\/\S+/) || [''])[0];
      if (filas.length < PAISES_MAX) filas.push({ pais: p, tipo: 'otro', que: '', anunciante: '', dias: null, anuncios: null, biblioteca: enlaceBiblioteca(lib),
        contacto: contactoDe(partes.slice(1).join(' ')), texto: limpio(partes[0].replace(/https?:\/\/\S+/g, ' ').replace(/[\s:,;—–-]+$/, ''), 200) });
      continue;
    }
    // Una frase («solo en BR [revisado 06/11/2026: ...]»): el texto y, si la trae, su nota entre corchetes.
    const f = fraseYNota(l);
    if (f.frase) textos.push(limpio(f.frase, 300));
    if (f.nota) notas.push(limpio(f.nota, 300));
  }
  return { codigos, lineas: filas, texto: textos.slice(0, 2).join(' '), nota: notas.slice(0, 2).join(' · ') };
}

// Huella corta y estable (FNV-1a de 32 bits) para las filas sin id de anuncio.
export function huella(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(36);
}

// ---------- tabla -> ofertas ----------
export function indiceDeColumnas(encabezados) {
  const idx = {};
  (encabezados || []).forEach((h, i) => {
    const k = plegar(h);
    for (const [clave, alias] of Object.entries(ALIAS)) if (idx[clave] === undefined && alias.includes(k)) idx[clave] = i;
  });
  return idx;
}

export function normalizarFila(celda, n) {
  const c = (k) => celda(k);
  const justif = c('justificacion');
  const oro = oroDe(c('palabra'), justif);
  // Una de oro solo se lee con su rubrica: si no trae «nota oro ...», sin desglose (mejor nada que barras de Brasil con numeros del oro).
  const desglose = oro ? desgloseOroDe(justif) : desgloseDe(justif);
  const ang = anguloDe(c('angulo'));
  const notas = notasDe(justif);
  const nichoCrudo = limpio(c('nicho'), 160);
  const nichoCorto = limpio(nichoCrudo.split(/[\/(]/)[0], 40);
  const palabra = limpio(c('palabra'), 120);
  const anunciante = limpio(c('anunciante'), 90);
  const adId = adIdDe(c('linkAnuncio'));
  const anunciosTxt = limpio(c('anuncios'), 12);
  const notaN = numeroDe(c('nota'));
  const entregable = limpio(c('entregable'), 400);
  const familia = familiaDe(nichoCrudo, entregable, palabra, ang.texto);
  const hueco = limpio(c('hueco'), 400);
  const fecha = fechaIso(c('fecha'));
  const idPaginaTxt = visible(c('idPagina'), 40), idVideoTxt = visible(c('idVideo'), 40);
  const origenFlujo = limpio(c('origenFlujo'), 20).toLowerCase();
  const flujoVerificado = origenFlujo === 'google' || origenFlujo === 'manual';
  const linkFlujo = flujoVerificado ? enlaceFlujo(idPaginaTxt, idVideoTxt) : '';
  // Sin publicacion verificada, el boton lleva a los anuncios de la pagina en la Biblioteca (esa URL si abre con el ID interno de la pagina).
  const linkAnunciosPagina = !linkFlujo && idNumerico(idPaginaTxt) ? `https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&view_all_page_id=${idNumerico(idPaginaTxt)}` : '';
  // Una celda con algo que no son digitos (casi siempre un numero largo que Google Sheets guardo en notacion cientifica, 2,42E+15) no sirve: la ficha lo dice en vez de callar.
  const flujoMalo = !enlaceFlujo(idPaginaTxt, idVideoTxt) && ((!!idPaginaTxt && !idNumerico(idPaginaTxt)) || (!!idVideoTxt && !idNumerico(idVideoTxt)));
  return {
    id: adId ? 'a' + adId : 'h' + huella([anunciante, fecha, palabra].join('|')),
    adId, fila: n,
    fecha, fechaTxt: limpio(c('fecha'), 20),
    palabra, anunciante,
    dominio: visible(c('dominio'), 80),
    anuncios: numeroDe(anunciosTxt), anunciosAprox: /~/.test(anunciosTxt),
    dias: numeroDe(c('dias')),
    nicho: nichoCrudo, nichoCorto: /^por clasificar/i.test(nichoCorto) ? 'Sin clasificar' : (nichoCorto || 'Sin clasificar'),
    familia,
    precio: visible(c('precio'), 80),
    entregable,
    embudo: limpio(c('embudo'), 200),
    hueco, colombia: claseColombia(hueco, desglose),
    linkAnuncio: enlaceAnuncio(c('linkAnuncio')),
    linkPagina: enlacePagina(c('linkPagina')),
    linkFlujo, flujoMalo, linkAnunciosPagina,
    promesas: ang.promesas, angulo: ang.texto, idioma: ang.idioma,
    nota: notaN !== null && notaN <= 10 ? notaN : null,
    desglose,
    justificacion: notas.principal, revisiones: notas.revisiones, avisos: notas.avisos,
    motivoNoTesteada: notas.motivoNoTesteada, origen: notas.origen, porAngulo: notas.porAngulo,
    status: estadoCanon(c('status')),
    statusTxt: limpio(c('status'), 24),
    oro, espanol: anunciantesEsDe(c('linksEs')),
    paises: paisesVendenDe(c('paises'), c('paisesDetalle')),
  };
}

// matriz de texto (la que sale de parsearCsv) -> { ofertas, faltan } o { error, faltan }
export function normalizarTabla(matriz) {
  const filas = Array.isArray(matriz) ? matriz : [];
  if (!filas.length) return { error: 'vacia', faltan: Object.keys(ALIAS) };
  const idx = indiceDeColumnas(filas[0]);
  const faltan = Object.keys(ALIAS).filter((k) => idx[k] === undefined && !OPCIONALES.includes(k));
  if (IMPRESCINDIBLES.some((k) => idx[k] === undefined)) return { error: 'no_es_ofertas', faltan };
  const vistos = new Map();
  const ofertas = [];
  filas.slice(1).forEach((f, i) => {
    if (f.every((c) => String(c).trim() === '')) return;
    const o = normalizarFila((k) => (idx[k] === undefined ? '' : (f[idx[k]] == null ? '' : f[idx[k]])), i + 2);
    // Una fila sin anunciante ni angulo es una celda suelta de otra cosa, no una oferta.
    if (!o.anunciante && !o.angulo) return;
    const n = (vistos.get(o.id) || 0) + 1;
    vistos.set(o.id, n);
    if (n > 1) o.id += '-' + n;
    ofertas.push(o);
  });
  return { ofertas, faltan };
}

// Resumen para la cabecera: cuantas hay por estado.
export function contarPorEstado(ofertas) {
  const c = { total: ofertas.length, sinEstado: 0 };
  ESTADOS.forEach((e) => { c[e] = 0; });
  ofertas.forEach((o) => { if (o.status) c[o.status]++; else c.sinEstado++; });
  return c;
}

// ---------- ofertas que el equipo agrega a mano (functions/api/radar-manual.js) ----------
// El equipo ve una oferta fuera del radar (un anuncio de la Biblioteca, la pagina de un anunciante, una pagina de ventas, un video de TikTok) y dice DE QUE ES: asi se modela esa
// aunque la pagina venda varias cosas. Se guardan en KV (no en la planilla) y se pintan en /radar como cualquier otra, con la misma ficha: se pueden vigilar, pasar a «Testiando»
// (tablero y Fabrica) y pedir sus ebooks. Su id empieza con «m» (las de la planilla, con «a» o «h»), asi que nunca chocan.
export const MANUAL_MAX_ENLACES = 4;
export const MANUAL_MIN_QUE_ES = 15;
// Sitios de video y redes: se rotulan como «red» (no como pagina de ventas) para que la ficha no mande a leer un video como si fuera una pagina.
const REDES = /(^|\.)(tiktok\.com|instagram\.com|youtube\.com|youtu\.be|facebook\.com|fb\.watch|pinterest\.com|x\.com|twitter\.com)$/i;

export function hostDe(url) {
  try { return new URL(url).hostname.replace(/^www\./i, '').toLowerCase(); } catch { return ''; }
}

// Un enlace pegado -> { url, tipo, host } con la direccion RECONSTRUIDA (anuncio, anunciante) o validada (cualquier otra), o null si no sirve.
// Los de la Biblioteca solo se reconocen si son de facebook.com: `adIdDe` busca «?id=<digitos>» en CUALQUIER texto y una pagina de ventas con «?id=123456» no es un anuncio.
export function clasificarEnlace(v) {
  const s = limpio(v, 700);
  if (!s) return null;
  const probe = /^https?:\/\//i.test(s) ? s : 'https://' + s;
  if (/(^|\.)facebook\.com$/i.test(hostDe(probe))) {
    let url = enlaceAnuncio(probe);
    if (url) return { url, tipo: 'anuncio', host: 'facebook.com' };
    url = enlaceAnunciante(probe);
    if (url) return { url, tipo: 'anunciante', host: 'facebook.com' };
  }
  const url = enlacePagina(s);
  if (!url) return null;
  const host = hostDe(url);
  return { url, tipo: REDES.test(host) ? 'red' : 'pagina', host };
}

// La oferta guardada ({ id, nombre, queEs, enlaces:[{url}], precio, por, creadoEn }) con la MISMA forma que `normalizarFila` da a una fila de la planilla, para que /radar la pinte,
// la filtre y la mande al tablero sin saber de donde vino. Lo que no existe para una oferta a mano (nota, dias en el aire, anuncios, hueco en Colombia) queda vacio; Colombia, sin validar.
export function ofertaManual(m) {
  const enlaces = (Array.isArray(m && m.enlaces) ? m.enlaces : []).map((e) => clasificarEnlace(e && e.url)).filter(Boolean).slice(0, MANUAL_MAX_ENLACES);
  const ad = enlaces.find((e) => e.tipo === 'anuncio'), anunciante = enlaces.find((e) => e.tipo === 'anunciante'), pagina = enlaces.find((e) => e.tipo === 'pagina');
  const nombre = limpio(m.nombre, 80), queEs = limpio(m.queEs, 500), precio = limpio(m.precio, 40);
  const fecha = fechaIso(m.creadoEn), p = fecha.split('-');
  return {
    id: String(m.id), adId: ad ? adIdDe(ad.url) : '', fila: 0, manual: true,
    fecha, fechaTxt: fecha ? `${p[2]}/${p[1]}/${p[0]}` : '',
    palabra: '', anunciante: nombre, dominio: pagina ? pagina.host : '',
    anuncios: null, anunciosAprox: false, dias: null,
    nicho: '', nichoCorto: 'Agregada por ti', familia: familiaDe(nombre, queEs),
    precio, entregable: queEs, embudo: '', hueco: '', colombia: 'validar',
    linkAnuncio: ad ? ad.url : '', linkAnunciante: anunciante ? anunciante.url : '', linkPagina: pagina ? pagina.url : '',
    enlaces, linkFlujo: '', flujoMalo: false, linkAnunciosPagina: '',      // el flujo de ZapData de una oferta de la planilla sale de dos columnas que una oferta a mano no tiene
    promesas: [], angulo: '', idioma: 'es', nota: null, desglose: null,
    justificacion: '', revisiones: [], avisos: [], motivoNoTesteada: '', origen: 'manual', porAngulo: false,
    status: 'nuevo', statusTxt: 'nuevo', por: limpio(m.por, 20), editadaEn: limpio(m.editadoEn, 30),
    oro: null, espanol: null, paises: null,
  };
}
