// El ORDER BUMP de un flujo de Venta de ZapData, leido de sus propios bloques (2026-11-11).
//
// Por que existe: el dash mide un order bump solo si alguien crea su pestaña «order bump …» en el libro de ventas del pais (skill mi-dashboard, «Order bump»). Una oferta nueva con bump y sin
// pestaña no se media y nada lo decia. ZapData no marca el bump en la venta: es un bloque de TEXTO, sin botones ni etiqueta, despues de los datos de pago. Los tres que habia al 11-nov dicen
// La API publica de ZapData (/v1/flows) NO trae los bloques, solo nombre e instancias: esto se lee con la credencial del tablero (zapdata_flow_get), dentro de la revision de
// functions/_zapdata-revision.js, que ya lee cada Venta encendida cuando cambia.
//
// Lo que sale de aqui es SOLO: el precio del bump (un numero, o null si no se puede leer con seguridad), el nombre del producto (texto corto, limpio) y los precios base (numeros). Nada del
// resto del flujo -datos de pago, numeros de cuenta, prompts de la IA- se guarda ni se devuelve.
//
// Se reconoce por FRASES que solo dice un order bump (no por el nombre del bloque, que puede cambiar): «antes de finalizar tu pedido/compra», «¿lo agregamos a tu compra/pedido?» u «order
// no se inventa. Lo prueba scripts/probar-bumps-sin-medir.mjs con los textos reales de los tres bumps.

// Version de la lectura: una Venta leida con una version anterior (o antes de que existiera esta lectura) se vuelve a leer una vez (ver porLeer en _zapdata-revision.js).
export const BUMP_V = 1;
export const MAX_BUMPS = 3;

const MARCAS = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');
const CONTROL = new RegExp('[' + String.fromCharCode(0) + '-' + String.fromCharCode(31) + String.fromCharCode(127) + ']', 'g');
// Sin tildes y en minusculas, con los saltos de linea como espacios (para buscar frases).
const llano = (s) => {
  let t = String(s == null ? '' : s).toLowerCase();
  try { t = t.normalize('NFD').replace(MARCAS, ''); } catch (e) { /* motor sin normalize */ }
  return t.replace(/\s+/g, ' ');
};

const FRASES_BUMP = [/antes de finalizar (?:tu|su) (?:pedido|compra)/, /lo agregamos a (?:tu|su) (?:compra|pedido)/, /\border ?bump\b/];
export function esTextoDeBump(texto) { const t = llano(texto); return FRASES_BUMP.some((re) => re.test(t)); }

// lleva 3 decimales). Las mismas reglas que ConvertTo-BumpNumero de scripts/_order-bumps.ps1. Lo que no calza -o no es mayor que 0- es null.
export function montoDe(crudo) {
  let t = String(crudo == null ? '' : crudo).replace(/\s/g, '').replace(/[.,]+$/, '');
  if (!/^\d/.test(t)) return null;
  if (/^\d{1,3}([.,]\d{3})+$/.test(t)) t = t.replace(/[.,]/g, '');
  else if (/^\d{1,3}(\.\d{3})+,\d{1,2}$/.test(t)) t = t.replace(/\./g, '').replace(',', '.');
  else if (/^\d{1,3}(,\d{3})+\.\d{1,2}$/.test(t)) t = t.replace(/,/g, '');
  else if (/^\d+,\d{1,2}$/.test(t)) t = t.replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  const n = Number(t);
  return Number.isFinite(n) && n > 0 && n < 1e8 ? n : null;
}

// «por solo», «solo por», «por tan solo», «por apenas», «por solamente», «por unicamente», seguido (como mucho de una moneda: «$», «US$», «S/», «Bs», «Q») del monto.
const PRECIO_TRAS = /(?:por\s+(?:tan\s+)?solo|solo\s+por|por\s+apenas|por\s+solamente|por\s+unicamente)\s*:?\s*(?:[a-z]{1,3}\s*[$\/.]?\s*)?\$?\s*(\d[\d.,]*)/g;
const PRECIO_SUELTO = /\$\s*(\d[\d.,]*)/g;
// El precio del bump en su texto: el UNICO monto que sigue a «por solo» (o, si no hay ninguno, el unico monto con «$»). Dos montos distintos, o ninguno -> null.
export function precioDeBump(texto) {
  const t = llano(texto);
  const unico = (re) => {
    const vistos = new Set();
    for (const m of t.matchAll(re)) { const n = montoDe(m[1]); if (n === null) return { malo: true }; vistos.add(n); }
    return { malo: false, lista: [...vistos] };
  };
  const a = unico(PRECIO_TRAS);
  if (a.malo) return null;
  if (a.lista.length === 1) return a.lista[0];
  if (a.lista.length > 1) return null;
  const b = unico(PRECIO_SUELTO);
  return !b.malo && b.lista.length === 1 ? b.lista[0] : null;
}

// Letras (con tildes), cifras, espacios y la puntuacion de un titulo: lo demas (emojis, simbolos) se va. Tope de 80.
const NO_TITULO = /[^\p{L}\p{N}\s:,.\-()&+'"\/%¿¡?!…]/gu;
// Una racha de 7 o mas cifras (un telefono o una cuenta escritos ahi por error), con lo que sea entre cifras («300 123 4567», «300•123•4567», «300/123/4567»), no se guarda ni sale: queda «…» y
// sus 4 ultimas, como sinTelefono. Va DESPUES de quitar emojis y simbolos (que si no, al volverse espacios, armaban la racha otra vez).
const TAPAR = /\d(?:[^\p{L}\d]{0,2}\d){6,}/gu;
const limpio = (s, n) => String(s == null ? '' : s).replace(CONTROL, ' ').replace(NO_TITULO, ' ').replace(/\s+/g, ' ').replace(TAPAR, (m) => '…' + m.replace(/\D/g, '').slice(-4))
  .replace(/^[\s:,.\-]+|[\s:,.\-]+$/g, '').slice(0, n).trim();
// El producto que ofrece: lo que va entre «llevarte» (o «agregar») y «por solo». '' si no se ve.
export function productoDeBump(texto) {
  const s = String(texto == null ? '' : texto);
  const i = s.search(/llevarte|ll[eé]vate|agregar(?:le)?/i);
  if (i < 0) return '';
  const resto = s.slice(i).replace(/^\S+\s*:?/, '');
  const j = resto.search(/por\s+(?:tan\s+)?s[oó]lo|s[oó]lo\s+por|por\s+apenas|por\s+solamente|por\s+[uú]nicamente/i);
  if (j <= 0) return '';
  return limpio(resto.slice(0, j), 80);
}

// distintas, o ninguno lo dice asi, [] (no se adivina).
const RENGLON_BASE = /^[^\p{L}\p{N}$]*\$?\s*(\d[\d.,]*)\s*(?:[\p{L}.]{1,10}\s*){0,2}[-–—]\s*\S/u;
export function preciosBase(bloques) {
  const juegos = [];
  for (const b of bloques) {
    const m = textoDeBloque(b);
    if (!m || esTextoDeBump(m)) continue;
    const vals = [];
    for (const renglon of m.split(/\r?\n/)) {
      const x = RENGLON_BASE.exec(renglon.trim());
      if (!x) continue;
      const n = montoDe(x[1]);
      if (n !== null && !vals.includes(n)) vals.push(n);
    }
    if (vals.length >= 2 && vals.length <= 4) juegos.push(vals.sort((p, q) => p - q).join('|'));
  }
  const distintos = [...new Set(juegos)];
  return distintos.length === 1 ? distintos[0].split('|').map(Number) : [];
}

// El texto que muestra un bloque: el de un «Texto» o el de un mensaje con botones.
function textoDeBloque(b) {
  if (!b || typeof b !== 'object' || !b.data || typeof b.data !== 'object') return '';
  if (b.type === 'text' && typeof b.data.message === 'string') return b.data.message;
  if (b.type === 'interactiveBlock' && typeof b.data.text === 'string') return b.data.text;
  return '';
}

// Los order bumps de UN flujo (lo que devuelve zapdata_flow_get) -> [{ p: precio|null, pr: producto, bs: [precios base] }] (como mucho MAX_BUMPS). [] si no tiene.
export function bumpsDe(g) {
  const bloques = g && g.fluxo && Array.isArray(g.fluxo.blocks) ? g.fluxo.blocks.filter((b) => b && typeof b === 'object') : [];
  const out = [];
  let bs = null;
  for (const b of bloques) {
    const m = textoDeBloque(b);
    if (!m || !esTextoDeBump(m)) continue;
    if (bs === null) bs = preciosBase(bloques);
    out.push({ p: precioDeBump(m), pr: productoDeBump(m), bs });
    if (out.length >= MAX_BUMPS) break;
  }
  return out;
}

// Lo guardado en KV, validado (puede venir de cualquier cosa): -> la misma lista, con tipos y topes.
export function limpiarBumps(x) {
  if (!Array.isArray(x)) return [];
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) && v > 0 && v < 1e8 ? v : null);
  return x.slice(0, MAX_BUMPS).filter((b) => b && typeof b === 'object').map((b) => ({
    p: num(b.p),
    pr: limpio(b.pr, 80),
    bs: Array.isArray(b.bs) ? b.bs.slice(0, 4).map(num).filter((v) => v !== null) : [],
  }));
}
