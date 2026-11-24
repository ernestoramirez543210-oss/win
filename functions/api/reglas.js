// Las reglas con las que el dashboard pausa, reactiva, sube o baja presupuesto y duplica campañas, conjuntos y anuncios.
//
// Una regla ENCENDIDA (`on`) y con campañas asignadas trabaja sola: en cada refresco del dash el servidor de GitHub la evalua con los numeros nuevos y APLICA en Facebook lo que decide
// (scripts/reglas-auto.cjs, que llama a /api/reglas-ejecutar). Aqui no se ejecuta nada: esto solo guarda la lista para que sea la misma en el telefono y en el computador, y para que el
// ejecutor compruebe cada accion contra la regla tal como esta guardada. Se escribe una vez por cambio, no por visita.
//
//   GET /api/reglas             -> { ok, reglas }   (reglas = null si nunca se guardaron)
//   GET /api/reglas  + cabecera x-clave-reglas  -> lo mismo, sin sesion: lo lee scripts/reglas-auto.cjs en el
//                               refresco de GitHub. La MISMA llave (CLAVE_REGLAS) abre /api/reglas-ejecutar, asi que quien la tenga puede aplicar lo que las reglas
//                               activas permiten: tratarla como un secreto y rotarla si se filtra (se puede rotar sin cerrar sesiones).
//   PUT /api/reglas {reglas}    -> { ok, reglas }   (se limpia: solo campos conocidos, en su rango)
//
// Forma v3 (la actual, modelada sobre AdsCook; ver REGLAS-ADSCOOK.md):
//   { v: 3, lista: [ { id, nombre, on, programa: { cadaMin, desde, hasta, horaDesde, horaHasta },
//       aplica: { todas, campanas:[fbId], futuras, base:[fbId], paises:[codigo] (vacio = todos), filtro:{ op, filas:[{ campo:'nombre', op, valor }] } },
//       tareas: [ { nivel, modo, grupos:[[cond]], acciones:[accion] } ] } ] }
// Forma v2 (una condicion plana por regla; el dashboard la migra a v3 al abrirla): { v: 2, lista: [ { id, nombre, on, accion, ventana, roasOp, roas, gasto, ventasOp,
//                              ventas, cpa, minDias, madurez, pct, tope } ] }   (madurez = ultimos dias que se ignoran)
//
// `programa` = cada cuanto la revisa sola el refresco (cadaMin; 0 = en cada refresco), entre que fechas esta viva (desde / hasta) y en que horario del dia trabaja (horaDesde / horaHasta, hora de
// Colombia; null = todo el dia); fuera de sus fechas o de su horario una regla tampoco propone nada en la pantalla ni se ejecuta.
// Ya no hay «modo prueba» ni interruptor general (`autoModo`) ni casilla «revision automatica» por regla (`auto`): se quitaron el 2026-10-13 a pedido del dueño. Una regla encendida es una regla
// que se ejecuta. Si llegan en lo que guarda una pantalla vieja, se ignoran (`auto` se guarda siempre en true por si algo viejo todavia lo lee).
//
// Editarlas: solo el dueno y la trafficker, porque son decisiones sobre plata. Verlas: todo el equipo (desde el 2026-10-15).

import { exigirAlguno, safeEqual, EQUIPO } from '../_sesion.js';

const CLAVE = 'reglas:config';
const MAX_REGLAS = 30;
const ACCIONES = ['pausar', 'reactivar', 'subir', 'bajar', 'duplicar'];
const VENTANAS = ['today', 'last7', 'last15', 'last30'];

// ---- v3 ----
const MAX_IDS = 4000;
const NIVELES = ['campaign', 'adset', 'ad'];
const ORDEN_NIVEL = { campaign: 0, adset: 1, ad: 2 };
const METRICAS = ['roas', 'facturacion', 'ventas', 'ventasMeta', 'cpa', 'gasto', 'gastoLocal', 'presupuesto', 'conv', 'tasaResp', 'conv2', 'conv3', 'costoConv', 'costoConvLocal', 'costoConv3', 'ventasPorConv', 'ctr', 'cpm', 'cpc', 'impresiones', 'dias', 'estado', 'nombre', 'numeroWpp'];
const TIPOS_ACC = { pausar: NIVELES, reactivar: NIVELES, subir: ['campaign', 'adset'], bajar: ['campaign', 'adset'], fijar: ['campaign', 'adset'], duplicar: ['campaign', 'adset'], avisar: NIVELES };
const OPS_NUM = ['>', '<', '>=', '<=', '=', '!='];
const OPS_RANK = ['topPct', 'bottomPct', 'top', 'bottom'];
const OPS_TXT = ['contiene', 'no_contiene', 'empieza', 'es'];
const OPS_EST = ['es', 'no_es'];
const NUMERO_VALORES = ['connected', 'disconnected', 'unknown'];
const CADA = ['siempre', 'una_vez', '4h', '6h', '12h', 'dia', '2dias', '3dias'];
const RANGO = /^(today|yesterday|ALL|last\d{1,3}x\d{1,2})$/;
const ID_CAMP = /^\d{5,20}$/;
const ids = (v) => Array.from(new Set((Array.isArray(v) ? v : []).map(String).filter((x) => ID_CAMP.test(x)))).slice(0, MAX_IDS);
// Paises a los que aplica una regla («colombia», «mexico»...): solo letras minusculas; vacio = todos. Cada pais tiene la moneda de su cuenta de anuncios, y un monto de presupuesto no vale igual en pesos que en dolares.
const PAIS = /^[a-z]{3,20}$/;
const paisesDe = (v) => Array.from(new Set((Array.isArray(v) ? v : []).filter((x) => typeof x === 'string' && PAIS.test(x)))).slice(0, 12);
const ambitoDe = (a, nivel) => (NIVELES.indexOf(a) !== -1 && ORDEN_NIVEL[a] <= ORDEN_NIVEL[nivel] ? a : nivel);

function limpiarCond(c, nivel) {
  if (!c || typeof c !== 'object' || METRICAS.indexOf(c.m) === -1) return null;
  const tipo = c.m === 'estado' ? 'estado' : c.m === 'nombre' ? 'texto' : c.m === 'numeroWpp' ? 'numero' : 'num';
  const o = { m: c.m, ambito: ambitoDe(c.ambito, nivel), rango: RANGO.test(String(c.rango || '')) ? String(c.rango) : 'last7x1' };
  // El numero de WhatsApp es de la campaña y solo se compara con «es»: conectado, desconectado o sin dato (templates/reglas-modulo.js, rgCondicion).
  if (tipo === 'numero') { o.ambito = 'campaign'; o.op = 'es'; o.valor = NUMERO_VALORES.indexOf(c.valor) !== -1 ? c.valor : 'connected'; return o; }
  if (c.m === 'presupuesto' && o.ambito === 'ad') o.ambito = ORDEN_NIVEL[nivel] >= 1 ? 'adset' : 'campaign';
  if (tipo === 'texto') { o.op = OPS_TXT.indexOf(c.op) !== -1 ? c.op : 'contiene'; o.valor = String(c.valor == null ? '' : c.valor).slice(0, 80); return o; }
  if (tipo === 'estado') { o.op = OPS_EST.indexOf(c.op) !== -1 ? c.op : 'es'; o.valor = c.valor === 'PAUSED' ? 'PAUSED' : 'ACTIVE'; return o; }
  o.op = OPS_NUM.concat(OPS_RANK).indexOf(c.op) !== -1 ? c.op : '>';
  if (OPS_RANK.indexOf(o.op) !== -1) { o.valor = Math.round(num(c.valor, 1, 1000, 10)); return o; }
  o.valor = num(c.valor, -1e9, 1e9, 0);
  const vs = c.vs;
  if (vs && vs.t === 'metrica' && METRICAS.indexOf(vs.m) !== -1 && vs.m !== 'estado' && vs.m !== 'nombre' && vs.m !== 'numeroWpp') {
    o.vs = { t: 'metrica', m: vs.m, ambito: ambitoDe(vs.ambito, nivel), rango: RANGO.test(String(vs.rango || '')) ? String(vs.rango) : o.rango, factor: num(vs.factor, 0, 1000, 1) };
  }
  return o;
}
function limpiarAccion(a, nivel) {
  if (!a || !TIPOS_ACC[a.tipo] || TIPOS_ACC[a.tipo].indexOf(nivel) === -1) return null;
  const o = { tipo: a.tipo, cada: CADA.indexOf(a.cada) !== -1 ? a.cada : 'siempre' };
  if (a.tipo === 'subir' || a.tipo === 'bajar') {
    o.modo = a.modo === 'abs' ? 'abs' : 'pct';
    o.valor = num(a.valor, 0, o.modo === 'pct' ? 1000 : 1e9, 20);
    if (a.tipo === 'subir') o.tope = num(a.tope, 0, 1e9, 0); else o.piso = num(a.piso, 0, 1e9, 0);
  } else if (a.tipo === 'fijar') o.valor = num(a.valor, 1, 1e9, 10);
  else if (a.tipo === 'duplicar') {
    const pc = a.presupuestoCopia || {};
    o.pausarOriginal = a.pausarOriginal === true;
    o.presupuestoCopia = { modo: ['igual', 'pct', 'fijar'].indexOf(pc.modo) !== -1 ? pc.modo : 'igual', valor: num(pc.valor, -95, 1e9, 0) };
  }
  return o;
}
function limpiarTarea(t) {
  if (!t || NIVELES.indexOf(t.nivel) === -1) return null;
  const grupos = (Array.isArray(t.grupos) ? t.grupos : []).slice(0, 6)
    .map((g) => (Array.isArray(g) ? g : []).slice(0, 8).map((c) => limpiarCond(c, t.nivel)).filter(Boolean)).filter((g) => g.length);
  const acciones = (Array.isArray(t.acciones) ? t.acciones : []).slice(0, 4).map((a) => limpiarAccion(a, t.nivel)).filter(Boolean);
  return { nivel: t.nivel, modo: t.modo === 'or_and_or' ? 'or_and_or' : 'and_or_and', grupos, acciones };
}
// Programa de la revision automatica de una regla: cada cuanto (minutos; 0 = en cada refresco, si no entre REV_MIN y REV_MAX), entre que fechas
// ('AAAA-MM-DD' en hora de Colombia, ambas incluidas; '' = sin limite) y en que horario del dia (horaDesde 0 a 23, horaHasta 1 a 24, en hora de Colombia: la regla solo trabaja desde horaDesde:00
// hasta antes de horaHasta:00, y cruza la medianoche si horaDesde > horaHasta; null en las dos = todo el dia). Si las fechas llegan al reves se ordenan: dejarlas asi apagaria la regla en silencio.
// El horario solo vale con las DOS horas, enteras y distintas («hasta las 0» se lee como medianoche; de 0 a 24 es todo el dia): lo mismo hacen rgProgramaBase de la pantalla, programaDe de
// scripts/reglas-auto.cjs y horarioDe del ejecutor, y scripts/probar-programa.cjs comprueba que den igual.
const REV_MIN = 15, REV_MAX = 10080;
const FECHA = /^\d{4}-\d{2}-\d{2}$/;
function limpiarPrograma(p) {
  p = p && typeof p === 'object' ? p : {};
  let cadaMin = Math.round(Number(p.cadaMin));
  cadaMin = cadaMin > 0 ? Math.min(REV_MAX, Math.max(REV_MIN, cadaMin)) : 0;
  const fecha = (v) => (FECHA.test(String(v || '')) && !isNaN(Date.parse(String(v) + 'T00:00:00Z')) ? String(v) : '');
  let desde = fecha(p.desde), hasta = fecha(p.hasta);
  if (desde && hasta && desde > hasta) [desde, hasta] = [hasta, desde];
  // Solo cuentan numeros o textos con un numero («17»): ni true/false, ni null, ni vacios.
  const hora = (v) => ((typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) ? Number(v) : NaN);
  let horaDesde = null, horaHasta = null;
  {
    const d = hora(p.horaDesde); let h = hora(p.horaHasta);
    if (h === 0) h = 24;
    if (Number.isInteger(d) && Number.isInteger(h) && d >= 0 && d <= 23 && h >= 1 && h <= 24 && d !== h && !(d === 0 && h === 24)) { horaDesde = d; horaHasta = h; }
  }
  return { cadaMin, desde, hasta, horaDesde, horaHasta };
}
// El horario del dia de una regla que llega: {tiene, valido}. `tiene` = alguna de las dos horas trae un valor (ni ausente, ni null, ni vacio); `valido` = las dos juntas hacen un horario. Un cliente VIEJO (una
// pestaña de /reglas abierta desde antes de que existiera el horario) rehace `programa` solo con { cadaMin, desde, hasta }: sin las claves horaDesde / horaHasta. Un cliente nuevo las manda SIEMPRE (null = todo el dia).
const traeClavesDeHorario = (p) => !!p && typeof p === 'object' && ('horaDesde' in p || 'horaHasta' in p);
const hayValor = (v) => v != null && v !== '';
// De 0 a 24 («hasta las 0» incluido) es todo el dia: no es un horario, pero tampoco un error.
function horarioEsTodoElDia(p) {
  const n = (v) => ((typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) ? Number(v) : NaN);
  return n(p.horaDesde) === 0 && (n(p.horaHasta) === 24 || n(p.horaHasta) === 0);
}
// `previas` = Map(id -> programa guardado). Devuelve { reglas: {v:3, lista}, errores:[texto] }.
function limpiar3(entrada, previas) {
  const lista = Array.isArray(entrada && entrada.lista) ? entrada.lista : [];
  const salida = [], errores = [];
  for (const r of lista.slice(0, MAX_REGLAS)) {
    if (!r || typeof r !== 'object') continue;
    const id = /^[a-z0-9_-]{1,24}$/.test(String(r.id || '')) ? String(r.id) : 'r' + salida.length + Math.random().toString(36).slice(2, 6);
    const a = r.aplica || {}, f = a.filtro || {};
    const nombre = String(r.nombre || '').replace(/\s+/g, ' ').trim().slice(0, 60) || 'Regla sin nombre';
    const programa = limpiarPrograma(r.programa);
    if (!traeClavesDeHorario(r.programa)) {
      // Cliente viejo: no conoce el horario, asi que NO lo borra: se conserva el que la regla ya tenia guardado (si no, una regla de «5 p. m. a medianoche» pasaria a trabajar las 24 horas sin aviso).
      const previo = previas && previas.get(id);
      if (previo && previo.horaDesde != null && previo.horaHasta != null) { programa.horaDesde = previo.horaDesde; programa.horaHasta = previo.horaHasta; }
    } else if ((hayValor(r.programa.horaDesde) || hayValor(r.programa.horaHasta)) && programa.horaDesde == null && !horarioEsTodoElDia(r.programa)) {
      // Un horario a medias o invalido no se guarda como «todo el dia» en silencio: se rechaza diciendo cual regla es.
      errores.push('La regla «' + nombre + '» trae un horario del día inválido: hacen falta dos horas enteras y distintas (desde 0 a 23, hasta 1 a 24).');
    }
    salida.push({
      id,
      nombre,
      on: r.on === true,
      auto: true,                                                  // ya no hay «revision automatica» aparte: una regla encendida se revisa y se ejecuta sola
      programa,
      aplica: {
        todas: a.todas === true,
        campanas: ids(a.campanas),
        futuras: a.futuras === true,
        base: ids(a.base),
        paises: paisesDe(a.paises),
        filtro: {
          op: f.op === 'or' ? 'or' : 'and',
          filas: (Array.isArray(f.filas) ? f.filas : []).slice(0, 6)
            .map((x) => ({ campo: 'nombre', op: OPS_TXT.indexOf(x && x.op) !== -1 ? x.op : 'contiene', valor: String((x && x.valor) || '').trim().slice(0, 60) }))
            .filter((x) => x.valor),
        },
      },
      tareas: (Array.isArray(r.tareas) ? r.tareas : []).slice(0, 4).map(limpiarTarea).filter(Boolean),
    });
  }
  return { reglas: { v: 3, lista: salida }, errores };
}

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

const num = (v, min, max, def) => {
  if (v === null || v === undefined || v === '') return def;
  const n = Number(v);
  return isFinite(n) ? Math.min(max, Math.max(min, n)) : def;
};

function limpiar(entrada) {
  const lista = Array.isArray(entrada && entrada.lista) ? entrada.lista : [];
  const salida = [];
  for (const r of lista.slice(0, MAX_REGLAS)) {
    if (!r || typeof r !== 'object' || ACCIONES.indexOf(r.accion) === -1) continue;
    const id = /^[a-z0-9_-]{1,24}$/.test(String(r.id || '')) ? String(r.id) : 'r' + salida.length + Math.random().toString(36).slice(2, 6);
    salida.push({
      id,
      nombre: String(r.nombre || '').trim().slice(0, 60) || 'Regla sin nombre',
      on: r.on === true,
      auto: true,
      accion: r.accion,
      ventana: VENTANAS.indexOf(r.ventana) !== -1 ? r.ventana : 'last7',
      roasOp: r.roasOp === '>=' ? '>=' : '<',
      roas: num(r.roas, 0, 50, null),
      gasto: num(r.gasto, 0, 100000, 0),
      ventasOp: r.ventasOp === '>=' ? '>=' : '<=',
      ventas: num(r.ventas, 0, 10000, null),
      cpa: num(r.cpa, 0, 100000, null),
      minDias: num(r.minDias, 0, 60, 0),
      madurez: Math.round(num(r.madurez, 0, 7, 0)),
      pct: num(r.pct, 1, 100, 20),
      tope: num(r.tope, 0, 10000000, 0),
    });
  }
  return { v: 2, lista: salida };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  // EDITAR la lista (PUT) es del dueno y de quien lleva el trafico (la trafficker): ella es quien mas usa las propuestas. Aprobar una accion
  // sigue pidiendo la contraseña de administrador en /api/toggle-ad, asi que ver o editar la lista no ejecuta nada. VERLA (GET) lo hace todo el equipo desde el 2026-10-15
  // (decision del dueno: «todo lo que aparece en mi dash, le aparezca al equipo»); un editor o un editor de video ven las reglas pero no las cambian.
  // Lectura con llave propia (el refresco de GitHub no tiene sesion). Sin CLAVE_REGLAS configurada no hay camino.
  const llave = request.headers.get('x-clave-reglas') || '';
  const conLlave = request.method === 'GET' && llave && env.CLAVE_REGLAS && safeEqual(llave, env.CLAVE_REGLAS);
  const esLectura = request.method === 'GET';
  if (!conLlave && !(await exigirAlguno(request, env, esLectura ? EQUIPO : ['owner', 'trafficker']))) return json(401, { ok: false, error: esLectura ? 'Solo el equipo ve las reglas' : 'Solo el dueño o la trafficker editan las reglas' });

  if (request.method === 'GET') {
    let r = null;
    try { r = await env.SUITE.get(CLAVE, { type: 'json' }); } catch { r = null; }
    return json(200, { ok: true, reglas: r });
  }
  if (request.method === 'PUT') {
    let b;
    try { b = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
    let reglas;
    if (b && b.reglas && b.reglas.v === 3) {
      // Lo que ya esta guardado (para no borrar el horario de una regla cuando guarda un cliente viejo). Si no se puede leer y alguna regla llega sin las claves del horario, no se arriesga: se pide reintentar.
      let previo = null, leyo = true;
      try { previo = await env.SUITE.get(CLAVE, { type: 'json' }); } catch { leyo = false; }
      const previas = new Map(((previo && previo.v === 3 && Array.isArray(previo.lista)) ? previo.lista : []).filter((x) => x && x.id).map((x) => [String(x.id), x.programa || {}]));
      if (!leyo && (Array.isArray(b.reglas.lista) ? b.reglas.lista : []).some((x) => x && typeof x === 'object' && !traeClavesDeHorario(x.programa))) return json(503, { ok: false, error: 'No pude leer las reglas guardadas para conservar sus horarios: reintenta en un momento' });
      const r3 = limpiar3(b.reglas, previas);
      if (r3.errores.length) return json(400, { ok: false, error: r3.errores[0] });
      reglas = r3.reglas;
    } else reglas = limpiar(b && b.reglas);
    try { await env.SUITE.put(CLAVE, JSON.stringify(reglas)); }
    catch (e) { return json(502, { ok: false, error: 'No se pudo guardar: ' + String((e && e.message) || e).slice(0, 160) }); }
    return json(200, { ok: true, reglas });
  }
  return json(405, { ok: false, error: 'Method not allowed' });
}
