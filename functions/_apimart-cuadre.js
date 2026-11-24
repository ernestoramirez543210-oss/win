// El cuadre de APIMart: lo que APIMart cobro DE VERDAD contra lo que quedo anotado en el libro de gasto (functions/_gasto.js).
//
// depende de que cada programa AVISE lo que gasta, y lo que se genera desde un editor sin conectar o directo en la pagina de APIMart no avisa a nadie. El cuadre no depende de nadie: APIMart
// dice cuantos creditos lleva consumidos la cuenta entera (`used_credits` de /v1/user/balance, con cualquier llave de la cuenta), y eso se compara con el libro.
//
// Solo se ve el TOTAL de la cuenta: APIMart no da por API el detalle de cada llamada. El cuadre dice cuanto se escapo y que dia; para saber en que, esta Registros de uso en apimart.ai.
//
// Un solo documento en KV (`apimart:cuadre`), no una clave por foto: se lee con un get, y las listas de KV son lo escaso de la suite. Una escritura cada media hora.
//   { v: 1, inicio: { t, usado }, ultima: { t, usado, saldo }, dias: { 'AAAA-MM-DD' (Colombia): creditos consumidos al cierre de ese dia } }

import { llaveApimart, saldoApimart } from './_apimart.js';

export const CLAVE_CUADRE = 'apimart:cuadre';
const CREDITOS_POR_USD = 10;          // la regla de APIMart (functions/_apimart.js): un credito son 0,10 USD
const MINUTOS_FRESCA = 20;            // abrir la pagina (o el refresco) no vuelve a preguntarle a APIMart si la ultima foto es mas nueva que esto
const HORAS_VIEJA = 2;                // la foto se toma cada media hora: una de mas de 2 h quiere decir que APIMart o el refresco no contestan, y el cuadre no ve lo de ahora
const DIAS_EN_PANTALLA = 60;
// El libro anota el texto a precio de lista (APIMart cobra ~0,8×), y el guion y la voz del editor no se anotan: son centavos. Una diferencia menor que esto es ruido, no un gasto escondido.
export const TOLERANCIA_USD = 0.25;
const TOLERANCIA_PROPORCION = 0.05;
// La alarma mira solo los ultimos DIAS_ALARMA dias (segunda revision, 2026-11-10): con el 5 % de TODO lo cobrado desde la primera foto, la tolerancia crecia sin fin y en unos meses
// muestra la tabla de la pagina, para que la suma de esa tabla sea la cifra del aviso. Lo de antes queda como informacion (total y seguimiento), no enciende nada.
export const DIAS_ALARMA = 14;
// Lo de antes de la ventana se mira en TRAMOS_FUERA tramos de DIAS_ALARMA dias (12 x 14 = 168 dias, unos 6 meses), no hasta la primera foto (tercera revision de seguridad, segunda
// ronda): el libro borra sus filas a los 400 dias (DIAS_GUARDADO de _gasto.js) y, sin este tope, desde unos 400 dias despues de la primera foto los tramos viejos quedarian en ambar
// para siempre sin ninguna fuga. Tambien acota el costo: 12 pasadas, sin importar cuantos años de fotos haya. La prueba exige (TRAMOS_FUERA + 1) x DIAS_ALARMA < DIAS_GUARDADO.
export const TRAMOS_FUERA = 12;

const diaCol = (t) => { const ms = Date.parse(t); return Number.isFinite(ms) ? new Date(ms - 5 * 3600 * 1000).toISOString().slice(0, 10) : ''; };
const sumarDias = (dia, n) => new Date(Date.parse(dia + 'T00:00:00Z') + n * 864e5).toISOString().slice(0, 10);
const r4 = (n) => Math.round(n * 1e4) / 1e4;
const aUsd = (creditos) => creditos / CREDITOS_POR_USD;
const docValido = (d) => !!(d && d.v === 1 && d.inicio && d.ultima && d.dias && typeof d.dias === 'object');
// Un dia del documento cuenta solo si esta bien escrito (AAAA-MM-DD de verdad) y su consumo es un numero finito y no negativo (tercera revision de seguridad, segunda ronda): una
// clave o un valor raro en KV (editado a mano) se ignora. Antes una clave rara tumbaba la pagina del dueño (RangeError), una con «+» colgaba el bucle de los tramos y un valor
// que no era numero dejaba la alarma en verde (NaN). docValido NO se endurece: si leerCuadre devolviera null, la foto siguiente pisaria el inicio y todo el dia a dia.
const DIA_BIEN = /^\d{4}-\d\d-\d\d$/;
const diaValido = (d) => DIA_BIEN.test(d) && Number.isFinite(Date.parse(d + 'T00:00:00Z')) && new Date(Date.parse(d + 'T00:00:00Z')).toISOString().slice(0, 10) === d;
const creditos = (v) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);

// Suma una foto { t, usado, saldo } (creditos) al documento. Pura: la prueba la llama sin KV.
export function sumarFoto(doc, foto) {
  const d = docValido(doc) ? doc : { v: 1, inicio: { t: foto.t, usado: foto.usado }, ultima: null, dias: {} };
  d.ultima = { t: foto.t, usado: foto.usado, saldo: foto.saldo };
  const dia = diaCol(foto.t);
  if (dia) d.dias[dia] = foto.usado;
  return d;
}

// null = todavia no hay cuadre. Si KV falla, LANZA: sin saber que hay guardado no se puede escribir encima (una foto sobre un «no hay nada» falso borraria el inicio y el dia a dia).
export async function leerCuadre(env) {
  const d = await env.SUITE.get(CLAVE_CUADRE, { type: 'json' });
  return docValido(d) ? d : null;
}

export function fotoFresca(doc, ahoraMs) {
  if (!docValido(doc)) return false;
  const edad = (ahoraMs == null ? Date.now() : ahoraMs) - Date.parse(doc.ultima.t);
  return Number.isFinite(edad) && edad >= 0 && edad < MINUTOS_FRESCA * 60 * 1000;
}

// Le pregunta a APIMart cuanto lleva consumido la cuenta y lo guarda. Nunca lanza: una foto que falla no puede tumbar la pagina ni el refresco.
// Devuelve { ok, doc, motivo } con motivo: 'sin-leer' (KV no dejo leer el cuadre: no se escribe nada) | 'sin-llave' (no hay llave de APIMart en la suite) | 'sin-saldo' (APIMart no
// contesto o no trajo el consumo) | 'sin-guardar' (KV).
export async function tomarFoto(env, docPrevio) {
  let previo = docPrevio;
  if (previo === undefined) {
    try { previo = await leerCuadre(env); } catch { return { ok: false, doc: null, motivo: 'sin-leer' }; }
  }
  let llave = null;
  try {
    let ajustes = {};
    try { ajustes = JSON.parse(await env.SUITE.get('ajustes')) || {}; } catch { /* sin ajustes: llaveApimart contesta null */ }
    llave = await llaveApimart(env, ajustes);
  } catch { /* abajo */ }
  if (!llave) return { ok: false, doc: previo, motivo: 'sin-llave' };
  const s = await saldoApimart(llave);
  if (!s || s.usadoCreditos == null) return { ok: false, doc: previo, motivo: 'sin-saldo' };
  const doc = sumarFoto(previo, { t: new Date().toISOString(), usado: s.usadoCreditos, saldo: s.creditos });
  try { await env.SUITE.put(CLAVE_CUADRE, JSON.stringify(doc)); } catch { return { ok: false, doc, motivo: 'sin-guardar' }; }
  return { ok: true, doc };
}

// Documento + filas del libro [{ t, p, u, d }] -> lo que muestra la pagina. Pura (`ahoraMs` solo para la prueba).
//   antes: desde que existe la cuenta hasta la primera foto (lo cobrado de esa epoca, contra lo anotado de esa epoca);
//   seguimiento: desde la primera foto; dias: el mismo seguimiento, dia por dia de Colombia (`real` es lo que subio el consumo ese dia).
//   ventana: los ultimos DIAS_ALARMA dias de `dias` ({ desde, dias, real, libro, dif }): lo UNICO que mira la alarma (total y seguimiento son informacion).
//   alerta: 'falta' (en la ventana cobro mas de lo anotado: gasto sin avisar) | 'sobra' (se anoto mas de lo que cobro: anotaciones de mas, que taparian un gasto sin avisar) | ''.
//   vieja: la ultima foto tiene mas de 2 h (lo de ahora no esta contado).
export function armarCuadre(doc, filas, ahoraMs) {
  if (!docValido(doc)) return null;
  const inicioT = doc.inicio.t;
  const fotos = {};             // los dias con foto que se pueden usar (ver diaValido)
  Object.keys(doc.dias).forEach((d) => { const v = creditos(doc.dias[d]); if (diaValido(d) && v !== null) fotos[d] = v; });
  const conFoto = (d) => Object.prototype.hasOwnProperty.call(fotos, d);
  const usadoInicio = creditos(doc.inicio.usado) ?? 0;           // un inicio raro cuenta 0: lo cobrado sale de mas (alarma ruidosa), nunca NaN (alarma callada)
  const usadoUltima = creditos(doc.ultima.usado) ?? usadoInicio;
  let libroAntes = 0, libroDesde = 0;
  const libroDia = {};
  (filas || []).forEach((f) => {
    if (f.p !== 'apimart') return;
    if (f.t < inicioT) { libroAntes += f.u; return; }
    libroDesde += f.u;
    const d = diaCol(f.t);
    if (DIA_BIEN.test(d)) libroDia[d] = (libroDia[d] || 0) + f.u;     // diaCol ya sale de una fecha real: basta la forma (y es la mitad de trabajo por fila)
  });
  const claves = Array.from(new Set(Object.keys(fotos).concat(Object.keys(libroDia)))).sort();
  let previo = usadoInicio;
  const dias = claves.map((d) => {
    let real = 0;
    if (conFoto(d)) {
      real = Math.max(0, aUsd(fotos[d] - previo));         // un reembolso puede bajar el consumo: ese dia cuenta 0, no negativo
      previo = fotos[d];
    }
    const libro = libroDia[d] || 0;
    return { d, real: r4(real), libro: r4(libro), dif: r4(real - libro) };
  });
  const tramo = (real, libro) => ({ real: r4(real), libro: r4(libro), dif: r4(real - libro) });
  const antes = tramo(aUsd(usadoInicio), libroAntes);
  const seguimiento = tramo(aUsd(usadoUltima - usadoInicio), libroDesde);
  // La ventana de la alarma: los ultimos DIAS_ALARMA dias hasta el ultimo dia con datos. Empieza justo DESPUES de un dia con foto (`ancla`): si la foto fallo unos dias, lo que la cuenta
  // consumio en ellos cae en el primer dia con foto, y asi lo anotado en esos dias tambien entra (sin esto, el borde de la ventana partiria un mismo gasto en «cobrado» y «sin anotar»).
  const diaUltima = diaCol(doc.ultima.t);
  const fin = claves.length ? claves[claves.length - 1] : (diaValido(diaUltima) ? diaUltima : '');
  const corte = fin ? sumarDias(fin, -(DIAS_ALARMA - 1)) : '';
  let ancla = '';
  claves.forEach((d) => { if (d < corte && conFoto(d)) ancla = d; });
  const enVentana = dias.filter((x) => x.d > ancla);
  const ventana = {
    desde: ancla ? sumarDias(ancla, 1) : (enVentana.length ? enVentana[0].d : fin), dias: DIAS_ALARMA,
    ...tramo(enVentana.reduce((s, x) => s + x.real, 0), enVentana.reduce((s, x) => s + x.libro, 0)),
  };
  const tolerancia = (t) => r4(Math.max(TOLERANCIA_USD, TOLERANCIA_PROPORCION * Math.max(t.real, t.libro)));
  const toleranciaVentana = tolerancia(ventana);
  // Lo de antes de la ventana, en tramos de DIAS_ALARMA dias hacia atras, cada uno con su propia tolerancia (tercera revision de seguridad, 2026-11-10): comparar lo acumulado fuera
  const tramosFuera = [];
  for (let hasta = ancla; hasta && tramosFuera.length < TRAMOS_FUERA;) {
    const corteT = sumarDias(hasta, -(DIAS_ALARMA - 1));
    let anclaT = '';
    claves.forEach((d) => { if (d < corteT && conFoto(d)) anclaT = d; });
    const enTramo = dias.filter((x) => x.d > anclaT && x.d <= hasta);
    const t = tramo(enTramo.reduce((s, x) => s + x.real, 0), enTramo.reduce((s, x) => s + x.libro, 0));
    tramosFuera.push({ desde: enTramo.length ? enTramo[0].d : hasta, hasta, ...t, tolerancia: tolerancia(t) });
    if (anclaT >= hasta) break;            // con dias validos no pasa (anclaT < corteT <= hasta); por si acaso, el bucle nunca da vueltas en el mismo sitio
    hasta = anclaT;
  }
  const pasados = (signo) => tramosFuera.filter((t) => signo * t.dif > t.tolerancia).sort((a, b) => signo * (b.dif - a.dif));
  const faltaFuera = pasados(1), sobraFuera = pasados(-1);
  const edad = (ahoraMs == null ? Date.now() : ahoraMs) - Date.parse(doc.ultima.t);
  return {
    desde: inicioT, ultima: doc.ultima.t, saldoUsd: r4(aUsd(creditos(doc.ultima.saldo) ?? 0)),
    total: tramo(aUsd(usadoUltima), libroAntes + libroDesde),
    antes, seguimiento, ventana,
    // Lo del seguimiento que quedo FUERA de la ventana (desde la primera foto hasta ventana.desde; la suma es de todo eso): no enciende la alarma, pero si alguno de los ultimos
    // TRAMOS_FUERA tramos de 14 dias paso de su tolerancia (`alerta`), la pagina lo dice en ambar, para que una fuga que salio de la ventana no quede en verde ni se confunda con lo
    // de antes del cuadre (revision de seguridad, 2026-11-10). `tramo` es el peor tramo que paso (el de mas sin anotar; si ninguno, el de mas anotado de mas) y `tramos`, cuantos
    // pasaron en ese sentido.
    fuera: {
      ...tramo(seguimiento.real - ventana.real, seguimiento.libro - ventana.libro),
      alerta: faltaFuera.length ? 'falta' : (sobraFuera.length ? 'sobra' : ''),
      tramo: faltaFuera[0] || sobraFuera[0] || null,
      tramos: (faltaFuera.length ? faltaFuera : sobraFuera).length,
    },
    tolerancia: TOLERANCIA_USD, toleranciaVentana,
    alerta: ventana.dif > toleranciaVentana ? 'falta' : (ventana.dif < -toleranciaVentana ? 'sobra' : ''),
    vieja: !(Number.isFinite(edad) && edad < HORAS_VIEJA * 3600 * 1000),
    // La pagina pinta la ventana con estos dias: nunca se recortan por debajo de ella (si la foto fallo mucho tiempo, la ventana tiene mas de 14 dias con datos).
    dias: dias.slice(-Math.max(DIAS_EN_PANTALLA, enVentana.length)),
  };
}
