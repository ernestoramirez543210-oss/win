// La configuracion de cada pais donde se vende: precios, Pixel y datos de pago. La usan los flujos de ZapData (/ofertas y la fabrica) para armar
// los mensajes que reciben los clientes.
//
// En la plantilla TODOS los paises nacen VACIOS a proposito: aqui no hay cuentas de cobro de nadie. Cada persona llena los suyos en /ofertas,
// pestana «Paises» (se guardan en el KV del sitio, no en el codigo). Un pais a medias NO se puede elegir al crear una oferta: es preferible eso a
// un flujo que le manda al cliente una cuenta en blanco.
//
// No escribas aqui numeros de cuenta ni telefonos: este archivo se sube a GitHub. El formulario de /ofertas es el lugar.

export const PREFIJO = 'paises:c:';

// Campos de cada pais (todos texto):
//   flujoPagos / flujoPagosId  nombre e ID del flujo «Pagamento detectado» de ZapData de ese pais
//   pixel / pixelNombre        el Pixel de Meta que recibe la compra
//   ancla, aporte1, aporte2    los precios que dice el guion (ancla = precio «antes», aporte1 = primer pago, aporte2 = el que sube)
//   formato                    como se escribe el precio, con {v} donde va el numero. Ej.: "${v} PESOS" -> "$15.000 PESOS"
//   bancos                     el texto con las cuentas donde paga el cliente (tal cual se le manda por WhatsApp)
//   tasaUsd                    solo si la moneda no la acepta el Pixel (VES, GTQ, BOB): cuantas unidades vale 1 USD
const VACIO = { flujoPagos: '', flujoPagosId: '', pixel: '', pixelNombre: '', ancla: '', aporte1: '', aporte2: '', formato: '', bancos: '', tasaUsd: '' };
export const SEMILLAS = [
  { id: 'colombia',  nombre: 'Colombia',  moneda: 'COP' },
  { id: 'mexico',    nombre: 'México',    moneda: 'MXN' },
  { id: 'venezuela', nombre: 'Venezuela', moneda: 'VES' },
  { id: 'peru',      nombre: 'Perú',      moneda: 'PEN' },
  { id: 'guatemala', nombre: 'Guatemala', moneda: 'GTQ' },
  { id: 'argentina', nombre: 'Argentina', moneda: 'ARS' },
  { id: 'ecuador',   nombre: 'Ecuador',   moneda: 'USD' },
  { id: 'bolivia',   nombre: 'Bolivia',   moneda: 'BOB' },
].map((p) => Object.assign({}, VACIO, p));

export const CAMPOS = ['nombre', 'moneda', 'flujoPagos', 'flujoPagosId', 'pixel', 'pixelNombre',
  'ancla', 'aporte1', 'aporte2', 'formato', 'bancos', 'tasaUsd'];

// El bloque "Pixel" de ZapData solo manda estas monedas al evento de compra de Meta. VES, GTQ y BOB no estan: quien las use reporta en USD,
// convertido con tasaUsd.
const MONEDAS_PIXEL = ['BRL', 'USD', 'EUR', 'GBP', 'ARS', 'MXN', 'COP', 'CLP', 'PEN', 'JPY', 'CAD', 'AUD', 'CHF'];

// La moneda que de verdad se le manda al Pixel, y la tasa para llegar a ella si hace falta convertir.
export function monedaPixel(cfg) {
  if (MONEDAS_PIXEL.indexOf(cfg.moneda) >= 0) return { moneda: cfg.moneda, tasaUsd: '' };
  return { moneda: 'USD', tasaUsd: cfg.tasaUsd || '' };
}

// El precio se escribe distinto en cada pais: el formato lleva {v} donde va el numero. Al numero solo le llegan digitos, puntos y comas
// (quien escribe "$3000 Bs" en el formulario ya pone el formato a mano, y sin este filtro se sumaba al del pais).
export function precio(cfg, v) {
  const crudo = String(v == null ? '' : v);
  const numero = crudo.replace(/[^0-9.,]/g, '').trim();
  return String(cfg.formato || '${v}').replace('{v}', numero || crudo.trim());
}

// Un pais esta listo para crear ofertas cuando tiene todo lo que el guion usa.
export function completo(c) {
  return !!(c.pixel && c.ancla && c.aporte1 && c.aporte2 && c.bancos && c.flujoPagos);
}

// Las semillas con lo que cada quien guardo en el formulario encima.
export async function leerPaises(env) {
  const guardados = {};
  try {
    const lista = await env.SUITE.list({ prefix: PREFIJO, limit: 100 });
    const vals = await Promise.all(lista.keys.map((k) => env.SUITE.get(k.name, { type: 'json' })));
    vals.filter(Boolean).forEach((v) => { if (v.id) guardados[v.id] = v; });
  } catch (e) { /* sin KV se sigue con las semillas: mejor incompleto que caido */ }
  return SEMILLAS.map((s) => Object.assign({}, s, guardados[s.id] || {}));
}
