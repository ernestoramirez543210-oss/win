  // ---------- Reglas v3: modeladas sobre AdsCook (ver REGLAS-ADSCOOK.md) ----------
  //
  // Este archivo se mete DENTRO del script del dashboard (build-dashboard.ps1 sustituye el marcador
  // __REGLAS_MODULO__): usa SNAPSHOT, state, arbolDeVentana, el, escHtml, fmtMoney, ensureAuth... de ahi.
  //
  // Una regla = nombre + a que campañas aplica + una o mas TAREAS. Cada tarea = nivel (campaña / conjunto / anuncio)
  // + condiciones (grupos Y / O sobre metricas con su rango de fechas) + acciones (pausar, reactivar, presupuesto,
  // duplicar, solo avisar). Una regla encendida y con campañas asignadas TRABAJA SOLA: el refresco de CI evalua las
  // reglas con esta misma funcion (window.__reglasAuto) y APLICA lo que deciden (scripts/reglas-auto.cjs -> /api/reglas-ejecutar).
  // En la pantalla, lo que todavia no se aplico sale como «pendiente» con su razon y los numeros, y «Ejecutar ahora» lo aplica
  // al instante por /api/toggle-ad con la contraseña de administrador.
  const RG_NIVELES = [['campaign', 'Campaña'], ['adset', 'Conjunto'], ['ad', 'Anuncio']];
  const RG_NIV = { campaign: 'Campaña', adset: 'Conjunto', ad: 'Anuncio' };
  const RG_NIV_ORDEN = { campaign: 0, adset: 1, ad: 2 };
  const RG_RANGOS = [['today', 'Hoy'], ['yesterday', 'Ayer'],
    ['last3x1', 'Últimos 3 días'], ['last3x0', 'Últimos 3 días (con hoy)'],
    ['last7x1', 'Últimos 7 días'], ['last7x0', 'Últimos 7 días (con hoy)'],
    ['last14x1', 'Últimos 14 días'], ['last14x0', 'Últimos 14 días (con hoy)'],
    ['last30x1', 'Últimos 30 días'], ['last30x0', 'Últimos 30 días (con hoy)'], ['ALL', 'Todo el tiempo']];
  const RG_OPS_NUM = [['>', '>'], ['<', '<'], ['>=', '≥'], ['<=', '≤'], ['=', 'es igual a'], ['!=', 'es distinto de']];
  const RG_OPS_RANK = [['topPct', 'está en el N % mejor'], ['bottomPct', 'está en el N % peor'], ['top', 'está entre los N mejores'], ['bottom', 'está entre los N peores']];
  const RG_OPS_TXT = [['contiene', 'contiene'], ['no_contiene', 'no contiene'], ['empieza', 'empieza con'], ['es', 'es']];
  const RG_OPS_EST = [['es', 'es'], ['no_es', 'no es']];
  const RG_CADA = [['siempre', 'Cada vez que se cumple'], ['una_vez', 'Una sola vez'], ['4h', 'Una vez cada 4 horas'], ['6h', 'Una vez cada 6 horas'],
    ['12h', 'Una vez cada 12 horas'], ['dia', 'Una vez al día'], ['2dias', 'Una vez cada 2 días'], ['3dias', 'Una vez cada 3 días']];
  const RG_CADA_MS = { siempre: 0, una_vez: Infinity, '4h': 4 * 36e5, '6h': 6 * 36e5, '12h': 12 * 36e5, dia: 864e5, '2dias': 1728e5, '3dias': 2592e5 };

  // ---- programa de una regla: cada cuanto se revisa sola y entre que fechas (el «Rule Checking» y el «Schedule» de AdsCook) ----
  // `programa` = { cadaMin, desde, hasta, horaDesde, horaHasta }. cadaMin 0 = en cada refresco (lo mas seguido que el sistema pueda); si no, los minutos entre una revision y la siguiente (15 a 10080).
  // desde / hasta = 'AAAA-MM-DD' en hora de Colombia, ambas incluidas ('' = sin limite). El intervalo solo lo usa la revision automatica (scripts/reglas-auto.cjs, que tiene su propia
  // copia de estas cuentas: hay un test que las compara); las fechas valen tambien para las propuestas de la pantalla: fuera de sus fechas una regla no propone nada.
  // horaDesde (0 a 23) / horaHasta (1 a 24) = el HORARIO del dia, en hora de Colombia: la regla solo trabaja desde horaDesde:00 hasta ANTES de horaHasta:00 (24 = medianoche). null en las dos = todo el dia
  // (lo de siempre). Si horaDesde es mayor que horaHasta la ventana cruza la medianoche (22 a 6 = de 10 p. m. a 6 a. m.). Vale donde valen las fechas: en la revision automatica, en las propuestas de la
  // pantalla y, por ultimo, en el ejecutor (functions/api/reglas-ejecutar.js), que vuelve a comprobarlo; esas dos copias (scripts/reglas-auto.cjs y el ejecutor) las compara probar-programa.cjs.
  const RG_REV_MIN = 15, RG_REV_MAX = 10080;
  const RG_CADA_REV = [[0, 'En cada refresco'], [15, 'Cada 15 minutos'], [30, 'Cada 30 minutos'], [60, 'Cada hora'], [360, 'Cada 6 horas'], [720, 'Cada 12 horas'], [1440, 'Cada 24 horas']];
  function rgProgramaBase(p) {
    p = p && typeof p === 'object' ? p : {};
    let m = Math.round(Number(p.cadaMin));
    m = m > 0 ? Math.min(RG_REV_MAX, Math.max(RG_REV_MIN, m)) : 0;
    const f = (v) => (/^\d{4}-\d{2}-\d{2}$/.test(String(v || '')) ? String(v) : '');
    // El horario solo vale con las DOS horas, enteras y distintas; «hasta las 0» se lee como medianoche (24); de 0 a 24 es todo el dia, o sea ningun horario.
    // Solo cuentan numeros o textos con un numero («17»): ni true/false, ni null, ni vacios.
    const hora = (v) => ((typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) ? Number(v) : NaN);
    let hd = null, hh = null;
    {
      const d = hora(p.horaDesde); let h = hora(p.horaHasta);
      if (h === 0) h = 24;
      if (Number.isInteger(d) && Number.isInteger(h) && d >= 0 && d <= 23 && h >= 1 && h <= 24 && d !== h && !(d === 0 && h === 24)) { hd = d; hh = h; }
    }
    return { cadaMin: m, desde: f(p.desde), hasta: f(p.hasta), horaDesde: hd, horaHasta: hh };
  }
  // La hora de Colombia (0 a 23) de un momento. Colombia no tiene horario de verano, pero se pregunta a Intl igual que rgHoyCO.
  function rgHoraNumCO(ms) {
    const o = {};
    new Intl.DateTimeFormat('en-US', { timeZone: 'America/Bogota', hour: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms == null ? Date.now() : ms)).forEach((x) => { o[x.type] = x.value; });
    return Number(o.hour) % 24;
  }
  // ¿Esta la regla dentro de su horario? Sin horario, siempre. La ventana es [horaDesde, horaHasta) y cruza la medianoche cuando horaDesde > horaHasta.
  function rgEnHorario(r, ms) {
    const p = rgProgramaBase(r && r.programa);
    if (p.horaDesde == null) return true;
    const h = rgHoraNumCO(ms);
    return p.horaDesde < p.horaHasta ? (h >= p.horaDesde && h < p.horaHasta) : (h >= p.horaDesde || h < p.horaHasta);
  }
  // «5:00 p. m.», «12:00 a. m.» y, para el 24, «medianoche».
  const rgHoraTxt = (h) => (h === 24 ? 'medianoche' : ((h % 12) || 12) + ':00 ' + (h < 12 ? 'a. m.' : 'p. m.'));
  // «de 5:00 p. m. a medianoche» ('' si la regla no tiene horario).
  const rgHorasTxt = (p) => (p && p.horaDesde != null && p.horaHasta != null ? 'de ' + rgHoraTxt(p.horaDesde) + ' a ' + rgHoraTxt(p.horaHasta) : '');
  // La fecha de hoy en Colombia ('AAAA-MM-DD'): las fechas de una regla son de Colombia aunque el dash se abra desde otro pais.
  function rgHoyCO(ms) {
    const o = {};
    new Intl.DateTimeFormat('en-US', { timeZone: 'America/Bogota', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(ms == null ? Date.now() : ms)).forEach((x) => { o[x.type] = x.value; });
    return o.year + '-' + o.month + '-' + o.day;
  }
  function rgEnFechas(r, ms) {
    const p = rgProgramaBase(r && r.programa), hoy = rgHoyCO(ms);
    return (!p.desde || hoy >= p.desde) && (!p.hasta || hoy <= p.hasta);
  }
  function rgCadaTxt(min) {
    if (!(min > 0)) return 'en cada refresco';
    if (min < 60) return 'cada ' + min + ' min';
    if (min === 60) return 'cada hora';
    if (min % 1440 === 0 && min >= 2880) return 'cada ' + (min / 1440) + ' días';
    if (min % 60 === 0) return 'cada ' + (min / 60) + ' h';
    return 'cada ' + Math.floor(min / 60) + ' h ' + (min % 60) + ' min';
  }
  // Cuando le toca de nuevo a una regla: su ultima revision + el intervalo, menos una tolerancia (10 % del intervalo, hasta 5 min) porque un refresco no cae a la hora exacta.
  // 0 = no hay intervalo o nunca se reviso. scripts/reglas-auto.cjs hace la misma cuenta (probar-programa.cjs las compara).
  const rgToleranciaMs = (cadaMin) => Math.min(300000, cadaMin * 6000);
  function rgProximaRevision(r, ultima) {
    const p = rgProgramaBase(r && r.programa);
    return p.cadaMin > 0 && ultima > 0 ? ultima + p.cadaMin * 60000 - rgToleranciaMs(p.cadaMin) : 0;
  }
  const rgHoraCO = (ms) => new Date(ms).toLocaleTimeString('es-CO', { timeZone: 'America/Bogota', hour: 'numeric', minute: '2-digit' });
  // «desde las 4:34 p. m.», «mañana desde las 7:00 a. m.» o «el 20 oct desde las 6:55 a. m.» (hora y dia de Colombia): con intervalos de un dia o mas, la hora sola no dice cuando.
  function rgDesdeTxt(ms, ahora) {
    const ya = ahora == null ? Date.now() : ahora, dia = rgHoyCO(ms);
    return (dia === rgHoyCO(ya) ? '' : dia === rgHoyCO(ya + 864e5) ? 'mañana ' : 'el ' + rgFechaCorta(dia, ya) + ' ') + 'desde las ' + rgHoraCO(ms);
  }
  // '2026-10-15' -> «15 oct» (con el año si no es el de hoy: «15 oct 2027»).
  function rgFechaCorta(s, ahora) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); if (!m) return '';
    const o = {};
    new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short' }).formatToParts(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).forEach((x) => { o[x.type] = x.value; });
    return o.day + ' ' + String(o.month || '').replace(/\.$/, '') + (m[1] !== rgHoyCO(ahora).slice(0, 4) ? ' ' + m[1] : '');
  }
  function rgFechasTxt(p) {
    if (p.desde && p.hasta) return 'del ' + rgFechaCorta(p.desde) + ' al ' + rgFechaCorta(p.hasta);
    return p.desde ? 'desde el ' + rgFechaCorta(p.desde) : p.hasta ? 'hasta el ' + rgFechaCorta(p.hasta) : '';
  }
  const RG_ACCIONES = {
    pausar:    { txt: 'Pausar',               pill: 'danger',     boton: 'Ejecutar ahora: pausar',    lote: 'Ejecutar ya todas las pausas',          niveles: ['campaign', 'adset', 'ad'] },
    reactivar: { txt: 'Reactivar',            pill: 'good',       boton: 'Ejecutar ahora: reactivar', lote: 'Ejecutar ya todas las reactivaciones',  niveles: ['campaign', 'adset', 'ad'] },
    subir:     { txt: 'Subir el presupuesto', pill: 'good',       boton: 'Ejecutar ahora: subir',     lote: 'Ejecutar ya todas las subidas',         niveles: ['campaign', 'adset'] },
    bajar:     { txt: 'Bajar el presupuesto', pill: 'warn',       boton: 'Ejecutar ahora: bajar',     lote: 'Ejecutar ya todas las bajadas',         niveles: ['campaign', 'adset'] },
    fijar:     { txt: 'Fijar el presupuesto', pill: 'warn',       boton: 'Ejecutar ahora: fijar',     lote: 'Ejecutar ya todos los presupuestos',    niveles: ['campaign', 'adset'] },
    duplicar:  { txt: 'Duplicar',             pill: 'reactivate', boton: 'Ejecutar ahora: duplicar',  lote: 'Ejecutar ya todas las copias',          niveles: ['campaign', 'adset'] },
    avisar:    { txt: 'Solo avisar',          pill: 'neutral',    boton: '',                   lote: '',                                  niveles: ['campaign', 'adset', 'ad'] },
  };
  const RG_ORDEN_ACC = { pausar: 0, bajar: 1, fijar: 2, subir: 3, duplicar: 4, reactivar: 5, avisar: 6 };

  // ---- metricas: lo que el snapshot trae por campaña / conjunto / anuncio, en la ventana que pida la condicion ----
  const usdTxt = (v) => '$' + fmtMoney(v, v >= 100 ? 0 : 2);
  const RG_METRICAS = {
    roas:        { txt: 'ROAS',                          grupo: 'Rendimiento', tipo: 'x',   f: (n) => (n.spendUsd > 0 ? n.salesUsd / n.spendUsd : null) },
    // «Dash» = lo que dice la hoja de ventas (la misma cifra del KPI Ventas); «Meta» = las compras que Meta atribuye al anuncio. `etq` es como se nombra en las frases y en las propuestas.
    facturacion: { txt: 'Facturación (Dash, USD)',       grupo: 'Rendimiento', tipo: 'usd', f: (n) => n.salesUsd || 0 },
    ventas:      { txt: 'Ventas (Dash)',                 etq: 'Ventas Dash', grupo: 'Rendimiento', tipo: 'n',   f: (n) => n.orders || 0 },
    ventasMeta:  { txt: 'Ventas (Meta)',                 etq: 'Ventas Meta', grupo: 'Rendimiento', tipo: 'n',   f: (n) => n.ventasMeta || 0 },
    cpa:         { txt: 'Costo por venta (Dash, USD)',   grupo: 'Rendimiento', tipo: 'usd', f: (n) => (n.orders > 0 ? n.spendUsd / n.orders : null) },
    gasto:       { txt: 'Gasto (USD)',                   grupo: 'Gasto',       tipo: 'usd', f: (n) => n.spendUsd || 0 },
    gastoLocal:  { txt: 'Gasto (moneda de la cuenta)',   etq: 'Gasto', grupo: 'Gasto', tipo: 'n' },
    // El TOPE que se le puso a la campaña, NO lo gastado: en su propio grupo y con el aviso en el nombre, para que nadie lo tome por «Gasto».
    presupuesto: { txt: 'Presupuesto diario: el tope, no lo gastado (moneda de la cuenta)', etq: 'Presupuesto diario', grupo: 'Presupuesto', tipo: 'n', niveles: ['campaign', 'adset'] },
    conv:        { txt: 'Conversaciones iniciadas',      grupo: 'Mensajes',    tipo: 'n',   f: (n) => n.conv || 0 },
    tasaResp:    { txt: 'Tasa de respuesta (%)',         grupo: 'Mensajes',    tipo: 'pct', f: (n) => (n.conv > 0 ? 100 * (n.resp || 0) / n.conv : null) },
    conv2:       { txt: 'Conversaciones con 2+ mensajes', grupo: 'Mensajes',   tipo: 'n',   f: (n) => n.d2 || 0 },
    conv3:       { txt: 'Conversaciones con 3+ mensajes', grupo: 'Mensajes',   tipo: 'n',   f: (n) => n.d3 || 0 },
    costoConv3:  { txt: 'Costo por conversación con 3+ mensajes (USD)', grupo: 'Mensajes', tipo: 'usd', f: (n) => (n.d3 > 0 ? n.spendUsd / n.d3 : null) },
    ctr:         { txt: 'CTR (%)',                       grupo: 'Anuncio',     tipo: 'pct', f: (n) => (n.imp > 0 ? 100 * ((n.clk || 0) / n.imp) : null) },
    cpm:         { txt: 'CPM (USD)',                     grupo: 'Anuncio',     tipo: 'usd', f: (n) => (n.imp > 0 ? n.spendUsd / n.imp * 1000 : null) },
    cpc:         { txt: 'CPC (USD)',                     grupo: 'Anuncio',     tipo: 'usd', f: (n) => { const c = n.clk || 0; return c > 0 ? n.spendUsd / c : null; } },
    impresiones: { txt: 'Impresiones',                   grupo: 'Anuncio',     tipo: 'n',   f: (n) => n.imp || 0 },
    ventasPorConv: { txt: 'Ventas por conversación (%)', grupo: 'Mensajes',    tipo: 'pct', f: (n) => (n.conv > 0 ? 100 * (n.orders || 0) / n.conv : null) },
    costoConv:   { txt: 'Costo por conversación (USD)',  grupo: 'Mensajes',    tipo: 'usd', f: (n) => (n.conv > 0 ? n.spendUsd / n.conv : null) },
    costoConvLocal: { txt: 'Costo por conversación (moneda de la cuenta)', etq: 'Costo por conversación', grupo: 'Mensajes', tipo: 'n' },
    dias:        { txt: 'Días con gasto',                grupo: 'Otros',       tipo: 'n' },
    estado:      { txt: 'Estado',                        grupo: 'Otros',       tipo: 'estado' },
    nombre:      { txt: 'Nombre',                        grupo: 'Otros',       tipo: 'texto' },
    // El numero de WhatsApp de ZapData al que manda la campaña (el «ZAP #10» de su nombre) y si esta conectado AHORA. Siempre se lee de la campaña, no del conjunto ni del anuncio. Ver rgInfoNumero.
    numeroWpp:   { txt: 'Número de WhatsApp de la campaña (ZapData)', etq: 'Número de WhatsApp', grupo: 'Otros', tipo: 'numero' },
  };
  // Los tres valores que puede tener el numero de una campaña. «sin dato» = no se sabe: la campaña no dice a que numero manda (ClicChat, API...), el numero no esta en la lista de ZapData o la lista es vieja.
  // Una condicion «es conectado» solo se cumple cuando SE SABE que esta conectado; «sin dato» nunca cuenta como conectado.
  const RG_NUM_VALORES = [['connected', 'conectado'], ['disconnected', 'desconectado'], ['unknown', 'sin dato']];
  const rgNumTxt = (v) => { const x = RG_NUM_VALORES.filter((e) => e[0] === v)[0]; return x ? x[1] : 'sin dato'; };
  // Edad maxima de la lista de numeros (contada hasta la hora en que se armo la pagina): el refresco la baja cada hora; con mas de 3 h es que la bajada fallo y el estado ya no es confiable.
  const RG_NUM_EDAD_MAX = 3 * 36e5;
  // El nombre de una metrica en las frases: sin la unidad entre parentesis («Gasto (USD)» -> «Gasto»), salvo las que traen `etq` (Ventas Dash / Ventas Meta).
  const rgEtq = (d) => d.etq || d.txt.replace(/ \(.*\)$/, '');
  const RG_VALOR_INICIAL = { roas: 1, facturacion: 50, ventas: 3, ventasMeta: 3, cpa: 10, gasto: 25, gastoLocal: 8000, presupuesto: 20000, conv: 10, tasaResp: 50, conv2: 5, conv3: 3, costoConv3: 3, ctr: 1, cpm: 5, cpc: 0.3, impresiones: 1000, ventasPorConv: 10, costoConv: 1, costoConvLocal: 5000, dias: 3 };
  // Una linea de ayuda bajo la metrica elegida: lo que significa en palabras del negocio.
  const RG_AYUDA = {
    ventas: 'Ventas (Dash): las ventas de tu hoja de ventas que le tocan a este anuncio, conjunto o campaña; es la misma cifra del dash. Cuando la hoja no dice de qué anuncio salió una venta, se reparte con lo que Meta atribuye, así que el total siempre cuadra con el KPI «Ventas».',
    ventasMeta: 'Ventas (Meta): las compras que Meta cuenta para este anuncio. Pueden tardar horas en llegar, así que suelen ir por debajo de las del Dash.',
    gastoLocal: 'Gasto (moneda de la cuenta): lo que de verdad se gastó, en pesos (COP) en Colombia, soles (PEN) en Perú y dólares (USD) en los demás países. Un monto como 8.000 solo sirve para un país (o países con la misma moneda).',
    costoConvLocal: 'Costo por conversación (moneda de la cuenta): lo gastado dividido por las conversaciones iniciadas, en pesos (COP) en Colombia, soles (PEN) en Perú y dólares (USD) en los demás países. Sin conversaciones no hay costo y la condición no se cumple: júntala con un gasto mínimo para atrapar las que gastan sin traer ninguna.',
    presupuesto: 'Presupuesto diario: el TOPE que le diste a la campaña o al conjunto, NO lo que ha gastado (una campaña recién creada con tope de 15.000 ya cumple «> 8.000» sin haber gastado nada). Se lee en la moneda de la cuenta: pesos (COP) en Colombia y dólares (USD) en los demás países. Para «gastó 8.000 pesos» usa «Gasto (moneda de la cuenta)».',
    numeroWpp: 'Número de WhatsApp: el «ZAP #10» del nombre de la campaña es el número 10 de ZapData, y se mira si está conectado según la última lista que bajó el refresco (cada hora). «Sin dato» es una campaña que no dice su número (ClicChat, API), un número que ZapData no tiene o una lista de hace más de 3 horas: nunca cuenta como conectado ni como desconectado. Para reactivar con seguridad usa «es conectado»; para pausar las campañas cuyo número se cayó, usa «es desconectado».',
  };
  const RG_CERO = { spendUsd: 0, salesUsd: 0, orders: 0, conv: 0, resp: 0, d2: 0, d3: 0, imp: 0, clk: 0, ventasMeta: 0, hijos: new Map() };

  // ---- caches por render ----
  const _rgIdx = new Map();            // pais|rango -> Map('nivel:fbId' -> nodo del arbol de esa ventana)
  let _rgCamp = null;                  // todas las campañas de todos los paises (la llena la pantalla: rgTodasCamp)
  let _rgNum = null;                   // la lista de numeros de WhatsApp ya procesada (rgNumeros)
  function rgLimpiar() { _rgIdx.clear(); _rgCamp = null; _rgNum = null; }
  function rgIndice(c, rango) {
    const k = c + '|' + rango;
    if (_rgIdx.has(k)) return _rgIdx.get(k);
    const idx = new Map();
    arbolDeVentana(c, rango).forEach((camp) => {
      if (camp.fbId) idx.set('campaign:' + camp.fbId, camp);
      camp.hijos.forEach((set) => {
        if (set.fbId) idx.set('adset:' + set.fbId, set);
        set.hijos.forEach((ad) => { if (ad.fbId) idx.set('ad:' + ad.fbId, ad); });
      });
    });
    _rgIdx.set(k, idx);
    return idx;
  }
  function arbolDeVentana(c, v) {
    const k = c + '|' + v;
    if (!_arbolV.has(k)) {
      const r0 = state.range, d0 = state.day;
      state.range = v; state.day = 'ALL';
      try { _arbolV.set(k, buildTree(ordersMatching(c, state.offer, v, 'ALL'), c)); }
      finally { state.range = r0; state.day = d0; }
    }
    return _arbolV.get(k);
  }
  // Los elementos de un nivel en un pais: del arbol de "todo el tiempo", que trae todo lo que alguna vez vendio, gasto o sigue encendido.
  function rgItems(c, nivel) {
    const out = [];
    arbolDeVentana(c, 'ALL').forEach((camp) => {
      if (!camp.fbId) return;
      if (nivel === 'campaign') { out.push({ c, nivel, n: camp, camp, set: null, ad: null }); return; }
      camp.hijos.forEach((set) => {
        if (nivel === 'adset') { if (set.fbId) out.push({ c, nivel, n: set, camp, set, ad: null }); return; }
        set.hijos.forEach((ad) => { if (ad.fbId && /^\d{6,}$/.test(String(ad.fbId))) out.push({ c, nivel, n: ad, camp, set, ad }); });
      });
    });
    return out;
  }
  const rgClave = (it) => it.nivel + ':' + it.n.fbId;
  function rgBase(it, ambito) { return ambito === 'campaign' ? it.camp : ambito === 'adset' ? it.set : it.ad; }
  // ---- el numero de WhatsApp de una campaña y si esta conectado ----
  // El numero sale del NOMBRE de la campaña, igual que en /numeros (Build-Suite.ps1): «ZAP #9 | ...», «ZAP WPP #3 | ...», «... | ZAP #8 | 9 OCT»; sin «#», «WPP 3» y «ZAP 1 |» (Mexico escribe asi).
  // Una campaña de ClicChat (dice CLICCHAT aunque conserve el «ZAP #2» de la que se copio) o que no es de ZapData no manda a ningun numero de ZapData: devuelve ''.
  // Las marcas combinantes (tildes) se quitan con codigos y no con un escape escrito: un escape de barra invertida + u en este archivo lo convierte en el caracter real el editor y rompe el patron.
  const RG_MARCAS = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');
  function rgNumeroDeCampana(nombre) {
    const orig = String(nombre == null ? '' : nombre);
    let t = orig.toUpperCase();
    try { t = t.normalize('NFD'); } catch (e) { /* motor sin normalize: se queda con las tildes */ }
    t = t.replace(RG_MARCAS, '');
    if (/\b(?:CLICC?\s*-?\s*CHAT|CLICHAT)\b/.test(t)) return '';
    if (!(t.indexOf('ZAPDATA') !== -1 || /\bZAPD?\b/.test(t))) return '';
    const m = /#\s*(\d+)/.exec(orig) || /WPP\s*(\d+)/i.exec(orig) || /\bZAP\s+(\d+)\s*\|/i.exec(orig);
    return m ? String(Number(m[1])) : '';
  }
  // La lista de numeros que bajo el refresco (SNAPSHOT.numerosWpp: { capturadoEn, instancias:[{ wpp, pais, estado }] }, la arma build-dashboard.ps1 de data/zapdata-instancias.json) lista para consultar.
  // Si la lista falta o es vieja, `motivo` dice por que y NINGUN numero se da por conocido: es lo seguro (no se reactiva una campaña ni se pausa otra por un dato que ya no vale).
  function rgNumeros() {
    if (_rgNum) return _rgNum;
    const d = SNAPSHOT.numerosWpp, lista = d && Array.isArray(d.instancias) ? d.instancias : [];
    const res = { mapa: {}, porWpp: {}, motivo: '', capturadoEn: d && d.capturadoEn ? String(d.capturadoEn) : '' };
    const cap = Date.parse(res.capturadoEn), ref = Date.parse(String(SNAPSHOT.generatedAt || '')) || Date.now();
    if (!lista.length) res.motivo = 'el dash no tiene la lista de números de ZapData';
    else if (!(cap > 0)) res.motivo = 'la lista de números de ZapData no trae su fecha';
    else if (cap > ref + 10 * 6e4) res.motivo = 'la fecha de la lista de números de ZapData es posterior a la de esta página';
    else if (ref - cap > RG_NUM_EDAD_MAX) res.motivo = 'la lista de números de ZapData tiene más de 3 horas (' + Math.round((ref - cap) / 36e5) + ' h)';
    if (!res.motivo) {
      const cuenta = {};
      lista.forEach((i) => {
        const w = String((i && i.wpp) || '').replace(/\D/g, '').replace(/^0+(?=\d)/, ''), p = String((i && i.pais) || '');
        if (!w || !p) return;
        const e = i.estado === 'connected' ? 'connected' : 'disconnected';
        res.mapa[p + '|' + w] = e;
        (cuenta[w] = cuenta[w] || []).push(e);
      });
      // El #N es unico en toda la cuenta de ZapData: si la campaña dice un numero que la lista tiene en OTRO pais (el #9 paso de Colombia a Bolivia), vale ese, mientras haya uno solo.
      Object.keys(cuenta).forEach((w) => { if (cuenta[w].length === 1) res.porWpp[w] = cuenta[w][0]; });
    }
    _rgNum = res;
    return res;
  }
  // { num, estado } del numero de una campaña del pais `c`: estado 'connected' | 'disconnected' | 'unknown'.
  function rgInfoNumero(camp, c) {
    const num = rgNumeroDeCampana(camp && camp.nombre), N = rgNumeros();
    if (!num || N.motivo) return { num, estado: 'unknown' };
    return { num, estado: N.mapa[c + '|' + num] || N.porWpp[num] || 'unknown' };
  }
  function rgDias(c, base, rango) {
    const porAd = (SNAPSHOT.countries[c] || {}).spendByAdIdDate || {}, fechas = new Set();
    const ads = [];
    (function juntar(n) { if (n.nivel === 'ad') { ads.push(n.fbId || n.id); return; } n.hijos.forEach(juntar); })(base);
    ads.forEach((id) => { const pd = porAd[id]; if (pd) Object.keys(pd).forEach((d) => { if (pd[d] > 0 && dateInRange(d, rango, 'ALL')) fechas.add(d); }); });
    return fechas.size;
  }
  // El valor de una metrica para un elemento, mirando el nivel `ambito` (el propio o un ancestro) en la ventana `rango`.
  function rgValor(m, it, ambito, rango) {
    const def = RG_METRICAS[m];
    if (m === 'numeroWpp') return def ? rgInfoNumero(it.camp, it.c).estado : null;      // el numero es de la campaña, sea cual sea el nivel de la tarea
    const base = rgBase(it, ambito);
    if (!def || !base) return null;
    if (m === 'estado') return base.estado;
    if (m === 'nombre') return base.nombre;
    if (m === 'presupuesto') return base.budgetDaily || 0;
    if (m === 'gastoLocal') {
      const f = rgFxCuenta(it.c); if (f == null) return null;                  // sin tasa no se inventa un monto: la condicion no se cumple
      return ((rgIndice(it.c, rango).get(ambito + ':' + base.fbId) || RG_CERO).spendUsd || 0) * f;
    }
    if (m === 'costoConvLocal') {
      const f = rgFxCuenta(it.c); if (f == null) return null;                  // igual que gastoLocal: sin tasa, sin monto
      const n = rgIndice(it.c, rango).get(ambito + ':' + base.fbId) || RG_CERO;
      return n.conv > 0 ? (n.spendUsd || 0) / n.conv * f : null;
    }
    if (m === 'dias') return rgDias(it.c, base, rango);
    const nodo = rgIndice(it.c, rango).get(ambito + ':' + base.fbId) || RG_CERO;
    return def.f(nodo);
  }
  function rgComparar(op, a, b) {
    if (a == null || b == null || isNaN(a) || isNaN(b)) return false;
    switch (op) {
      case '>': return a > b; case '<': return a < b; case '>=': return a >= b; case '<=': return a <= b;
      case '=': return Math.abs(a - b) < 1e-9; case '!=': return Math.abs(a - b) >= 1e-9;
      default: return false;
    }
  }
  function rgFmtValor(tipo, v) {
    if (v == null) return '—';
    if (tipo === 'usd') return usdTxt(v);
    if (tipo === 'x') return v.toFixed(2) + 'x';
    if (tipo === 'pct') return (Math.round(v * 10) / 10) + '%';
    if (tipo === 'estado') return v === 'ACTIVE' ? 'activo' : 'pausado';
    if (tipo === 'numero') return rgNumTxt(v);
    if (tipo === 'texto') return '«' + v + '»';
    return Number.isInteger(v) ? fmtInt(v) : (Math.round(v * 100) / 100).toString();
  }
  function rgRangoTxt(k) {
    const p = RG_RANGOS.filter((r) => r[0] === k)[0]; if (p) return p[1].toLowerCase();
    const m = /^last(\d+)x(\d+)$/.exec(k || '');
    return m ? 'últimos ' + m[1] + ' días' + (Number(m[2]) > 0 ? ' sin los últimos ' + m[2] : ' con hoy') : String(k || '');
  }
  function rgOpTxt(op) {
    const t = RG_OPS_NUM.concat(RG_OPS_TXT, RG_OPS_EST, RG_OPS_RANK).filter((o) => o[0] === op)[0];
    return t ? t[1] : op;
  }

  // ---- una condicion contra un elemento: { ok, txt } ----
  function rgCondicion(cond, it, ctx) {
    const def = RG_METRICAS[cond.m]; if (!def) return { ok: false, txt: '' };
    const ambito = cond.ambito || it.nivel, rango = cond.rango || 'last7x1';
    const v = rgValor(cond.m, it, ambito, rango);
    const etq = rgEtq(def) + (ambito !== it.nivel ? ' del ' + RG_NIV[ambito].toLowerCase() : '');
    const cuando = (def.tipo === 'estado' || def.tipo === 'texto' || def.tipo === 'numero' || cond.m === 'presupuesto') ? '' : ' (' + rgRangoTxt(rango).replace(/ \((.*)\)$/, ', $1') + ')';
    if (def.tipo === 'numero') {
      // Solo «es»: un «no es conectado» tambien lo cumpliria lo que no se sabe (ClicChat, lista vieja) y esa es justo la trampa que esta condicion evita.
      const info = rgInfoNumero(it.camp, it.c), objetivo = RG_NUM_VALORES.some((e) => e[0] === cond.valor) ? cond.valor : 'connected';
      return { ok: info.estado === objetivo, txt: rgEtq(def) + (info.num ? ' #' + info.num : '') + ' es ' + rgNumTxt(objetivo) + ' (ahora: ' + rgNumTxt(info.estado) + ')' };
    }
    if (def.tipo === 'texto') {
      const a = String(v || '').toLowerCase(), b = String(cond.valor == null ? '' : cond.valor).toLowerCase();
      const ok = cond.op === 'contiene' ? a.indexOf(b) !== -1 : cond.op === 'no_contiene' ? a.indexOf(b) === -1 : cond.op === 'empieza' ? a.indexOf(b) === 0 : a === b;
      return { ok, txt: etq + ' ' + rgOpTxt(cond.op) + ' «' + cond.valor + '»' };
    }
    if (def.tipo === 'estado') {
      const objetivo = String(cond.valor) === 'PAUSED' ? 'PAUSED' : 'ACTIVE';
      const ok = cond.op === 'no_es' ? v !== objetivo : v === objetivo;
      return { ok, txt: etq + ' ' + rgOpTxt(cond.op) + ' ' + rgFmtValor('estado', objetivo) };
    }
    if (/^(top|bottom)/.test(cond.op)) {
      const set = ctx.ranking(cond, it);
      return { ok: set.has(rgClave(it)), txt: etq + cuando + ' ' + rgFmtValor(def.tipo, v) + ' ' + rgOpTxt(cond.op).replace('N', String(cond.valor)) };
    }
    let ref, refTxt;
    if (cond.vs && cond.vs.t === 'metrica') {
      const d2 = RG_METRICAS[cond.vs.m]; const f = cond.vs.factor == null ? 1 : Number(cond.vs.factor);
      const r = rgValor(cond.vs.m, it, cond.vs.ambito || it.nivel, cond.vs.rango || rango);
      ref = r == null ? null : r * f;
      refTxt = (f !== 1 ? f + ' × ' : '') + (d2 ? rgEtq(d2) : '?') + ' del ' + RG_NIV[cond.vs.ambito || it.nivel].toLowerCase() + ' ' + rgFmtValor(d2 ? d2.tipo : 'n', r);
    } else { ref = Number(cond.valor); refTxt = rgFmtValor(def.tipo, ref); }
    return { ok: rgComparar(cond.op, v, ref), txt: etq + cuando + ' ' + rgFmtValor(def.tipo, v) + ' ' + rgOpTxt(cond.op) + ' ' + refTxt };
  }

  // ---- asignacion: a que campañas aplica una regla ----
  function rgFiltroCoincide(filtro, nombre) {
    const filas = (filtro && filtro.filas) || []; if (!filas.length) return true;
    const n = String(nombre || '').toLowerCase();
    const una = (f) => { const v = String(f.valor || '').toLowerCase(); return f.op === 'no_contiene' ? n.indexOf(v) === -1 : f.op === 'empieza' ? n.indexOf(v) === 0 : f.op === 'es' ? n === v : n.indexOf(v) !== -1; };
    return filtro.op === 'or' ? filas.some(una) : filas.every(una);
  }
  function rgAsignada(r, it) {
    const a = r.aplica || {}; const id = it.camp.fbId;
    if (a.todas) return true;
    if ((a.campanas || []).indexOf(id) !== -1) return true;
    return !!a.futuras && (a.base || []).indexOf(id) === -1 && rgFiltroCoincide(a.filtro, it.camp.nombre);
  }
  // ---- pais de la regla: `aplica.paises` (vacio = todos). Cada pais lleva la moneda de su cuenta de anuncios (Colombia: COP; el resto: USD), asi que una regla con
  // montos de presupuesto solo tiene sentido en paises con la misma moneda. Se filtra por encima de «todas», de la lista manual y del filtro de futuras.
  const rgPaisOk = (r, c) => { const p = (r.aplica || {}).paises; return !p || !p.length || p.indexOf(c) !== -1; };
  const rgMonedaCuenta = (c) => ((SNAPSHOT.countries[c] || {}).adSpendCurrency) || 'USD';
  // Unidades de la moneda de la cuenta que valen un dolar en el pais `c`: 1 en cuentas en dolares, la tasa del snapshot en las demas; null si falta la tasa.
  function rgFxCuenta(c) { const cur = rgMonedaCuenta(c); if (cur === 'USD') return 1; const fx = Number(SNAPSHOT.fx && SNAPSHOT.fx[cur]); return fx > 0 ? fx : null; }
  // ¿La regla compara o fija MONTOS de presupuesto (que van en la moneda de la cuenta)? Los porcentajes y las metricas en USD no dependen de la moneda.
  const RG_MONTOS_CUENTA = ['presupuesto', 'gastoLocal', 'costoConvLocal'];
  function rgUsaMontos(r) {
    return (r.tareas || []).some((t) => (t.grupos || []).some((g) => g.some((c) => RG_MONTOS_CUENTA.indexOf(c.m) !== -1 || (c.vs && RG_MONTOS_CUENTA.indexOf(c.vs.m) !== -1))) ||
      (t.acciones || []).some((a) => a.tipo === 'fijar' || (a.tipo === 'subir' && (a.modo === 'abs' || Number(a.tope) > 0)) || (a.tipo === 'bajar' && (a.modo === 'abs' || Number(a.piso) > 0)) ||
        (a.tipo === 'duplicar' && a.presupuestoCopia && a.presupuestoCopia.modo === 'fijar')));
  }
  // Monedas de cuenta de los paises en alcance: { COP: ['colombia'], USD: ['mexico', ...] }.
  function rgMonedasAlcance(r) {
    const p = (r.aplica || {}).paises, lista = p && p.length ? p : Object.keys(SNAPSHOT.countries), out = {};
    lista.filter((c) => SNAPSHOT.countries[c]).forEach((c) => { const m = rgMonedaCuenta(c); (out[m] = out[m] || []).push(c); });
    return out;
  }
  function rgNumAsignadas(r) {
    const a = r.aplica || {}; if (a.todas) return -1;
    return (a.campanas || []).length;
  }

  // ---- el presupuesto diario vive en el conjunto o, si la campaña es CBO, en la campaña ----
  function rgPresupuestoDe(it) {
    if (it.nivel === 'campaign') return it.camp.cbo && it.camp.budgetDaily > 0 ? { nivel: 'campaign', fbId: it.camp.fbId, actual: it.camp.budgetDaily, nombre: it.camp.nombre } : null;
    if (it.nivel === 'adset') {
      if (it.set.budgetDaily > 0) return { nivel: 'adset', fbId: it.set.fbId, actual: it.set.budgetDaily, nombre: it.set.nombre };
      if (it.camp.cbo && it.camp.budgetDaily > 0 && it.camp.fbId) return { nivel: 'campaign', fbId: it.camp.fbId, actual: it.camp.budgetDaily, nombre: it.camp.nombre };
    }
    return null;
  }
  function rgNuevoPresupuesto(a, actual) {
    const v = Number(a.valor) || 0;
    if (a.tipo === 'fijar') return Math.max(1, Math.round(v));
    if (a.tipo === 'subir') {
      const x = a.modo === 'abs' ? actual + v : actual * (1 + v / 100);
      const n = Math.round(x); return a.tope > 0 ? Math.min(n, a.tope) : n;
    }
    const x = a.modo === 'abs' ? actual - v : actual * (1 - v / 100);
    return Math.max(a.piso > 0 ? a.piso : 1, Math.round(x));
  }
  const rgActivo = (it) => (it.nivel === 'adset' ? it.n.estado === 'ACTIVE' && it.camp.estado === 'ACTIVE' : it.n.estado === 'ACTIVE');
  const rgPausado = (it) => (it.nivel === 'campaign' ? it.n.estado === 'PAUSED'
    : it.nivel === 'adset' ? it.n.estado === 'PAUSED' && it.camp.estado === 'ACTIVE'
      : it.n.estado === 'PAUSED' && it.set.estado === 'ACTIVE' && it.camp.estado === 'ACTIVE');

  // ---- memoria de lo aprobado / ignorado ----
  // Lo de ESTE navegador (aprobado o ignorado) mas lo que CUALQUIERA aplico desde el dash en las ultimas horas, que viene del registro compartido (/api/reglas-registro, ver
  // rgCargarRegistro): lo que se aprobo en el telefono no se le vuelve a ofrecer a quien abra el computador (aprobarlo dos veces subiria dos veces el presupuesto).
  let rgMemCompartida = {};
  function rgMemoriaLocal() { try { return JSON.parse(localStorage.getItem('rg:hechas') || '{}') || {}; } catch (e) { return {}; } }
  // Lo que viene del registro compartido es siempre algo YA APLICADO con exito (`ap: 1`): solo se anotan ahi los cambios que Facebook acepto.
  function rgMemoria() {
    const m = Object.assign({}, rgMemoriaLocal());
    Object.keys(rgMemCompartida).forEach((k) => { const s = rgMemCompartida[k]; if (s && typeof s === 'object' && (!m[k] || m[k].t < s.t)) m[k] = Object.assign({}, s, { ap: 1 }); });
    return m;
  }
  // ms < 0 = para siempre («una sola vez»); JSON no guarda Infinity. `aplicada` = lo que se recuerda es un cambio YA HECHO (se aprobo y salio bien); si no, es una propuesta IGNORADA (y una marca
  // de antes de que existiera este campo cuenta como ignorada, que es lo prudente: sigue oculta toda su ventana).
  function rgRecordar(clave, ms, aplicada) {
    const m = rgMemoriaLocal(); m[clave] = Object.assign({ t: Date.now(), ms: ms === Infinity ? -1 : (ms == null ? 864e5 : ms) }, aplicada ? { ap: 1 } : {});
    Object.keys(m).forEach((k) => { if (m[k].ms >= 0 && Date.now() - m[k].t > Math.max(m[k].ms, 864e5 * 3)) delete m[k]; });
    try { localStorage.setItem('rg:hechas', JSON.stringify(m)); } catch (e) { /* sin memoria solo se repite la propuesta */ }
  }
  // ¿Ya se aplico (o se ignoro) dentro de su ventana? PAUSAR es la excepcion en lo APLICADO: solo cuenta si la regla pide espaciarlo («Una vez al día»...); no se mira el `ms` guardado en la marca, que traia
  // un piso de 24 h aunque la accion dijera «Cada vez que se cumple». Lo ignorado se oculta toda su ventana, y el resto de las acciones conserva el piso (ver rgMemoriaMs).
  function rgYaHecha(clave, ms, mem, tipo) {
    const m = mem[clave]; if (!m) return false;
    if (tipo === 'pausar' && m.ap) return ms === Infinity || Date.now() - m.t < (ms || 0);
    return m.ms < 0 || ms === Infinity || Date.now() - m.t < Math.max(m.ms, ms || 0);
  }
  // Cuanto tiempo no vuelve a salir algo YA APLICADO: la frecuencia de la accion y, para lo que cambia plata, nunca menos de 24 h (el snapshot tarda en reflejar el cambio; sin esto una subida de presupuesto
  // saldria de nuevo al instante). PAUSAR es la excepcion: no puede gastar mas, lo que ya esta pausado ni se propone (rgActivo) ni se escribe (el ejecutor lee Facebook antes de tocar), y con «Cada vez que se
  // cumple» tiene que volver a pausar lo que alguien volvio a encender: antes un piso oculto de 24 h lo dejaba encendido (una campaña de SERMONES, 2026-10-13).
  const rgMemoriaMs = (p) => (p.ventanaMs === Infinity ? Infinity : p.tipo === 'pausar' ? (p.ventanaMs || 0) : Math.max(p.ventanaMs || 0, 864e5));
  // Cuanto se oculta una propuesta que la persona IGNORO: la frecuencia de la accion y nunca menos de 24 h, para todos los tipos (ignorar una pausa no la vuelve a mostrar a los 5 minutos).
  const rgIgnorarMs = (p) => (p.ventanaMs === Infinity ? Infinity : Math.max(p.ventanaMs || 0, 864e5));
  // «a las 3:17 p. m.», «ayer a las 10:05 a. m.» o «el 12/10 a las 9:00 p. m.» (hora de Colombia).
  function rgCuandoCO(t, ahora) {
    const ya = ahora == null ? Date.now() : ahora, dia = rgHoyCO(t);
    return (dia === rgHoyCO(ya) ? 'a las ' : dia === rgHoyCO(ya - 864e5) ? 'ayer a las ' : 'el ' + dia.slice(8) + '/' + dia.slice(5, 7) + ' a las ') + rgHoraCO(t);
  }
  function rgHistorial() { try { return JSON.parse(localStorage.getItem('rg:historial') || '[]') || []; } catch (e) { return []; } }
  function rgAnotar(p, ok, msg, extra) {
    const h = rgHistorial();
    h.unshift(Object.assign({ t: Date.now(), tipo: p.tipo, nivel: p.nivel, regla: p.regla, nombre: p.nombre, pais: p.country, fbId: p.fbId, ok: !!ok, msg: msg || '', razon: p.texto, antes: p.actual == null ? null : p.actual, despues: p.nuevo == null ? null : p.nuevo }, extra || {}));
    try { localStorage.setItem('rg:historial', JSON.stringify(h.slice(0, 80))); } catch (e) { /* solo se pierde el rastro */ }
  }

  // Lo que se le dice al servidor sobre quien pide el cambio (la regla, sobre que y por que): /api/toggle-ad lo anota en el registro compartido junto con lo que verifico en Facebook.
  function rgMetaDe(p, sobre) {
    const ms = rgMemoriaMs(p);
    return Object.assign({ regla: p.regla, nombre: p.nombre, pais: p.country, razon: p.texto, cifras: p.cifras || '', tipo: p.tipo, antes: p.actual == null ? null : p.actual, despues: p.nuevo == null ? null : p.nuevo, clave: p.clave, ms: ms === Infinity ? -1 : ms }, sobre || {});
  }

  // ---- cobertura: que se mira y que NO, para que nada se descarte en silencio ----
  // Lo asignado a una regla que no se evalua porque su cuenta de anuncios esta caida (deshabilitada, impaga, cerrada o ilegible): cuantos, cuantos de ellos siguen diciendo «encendido», de que cuentas
  // y -importante- si la cuenta es ILEGIBLE (el token no la puede leer: ahi puede haber gasto corriendo que nadie esta controlando). Se detallan hasta 3 cuentas por tarea; los conteos son de todas.
  function rgAnotarCaidas(res, c, asignados) {
    asignados.forEach((it) => {
      if (!enCuentaCaida(it.camp)) return;
      const cd = res.caidas || (res.caidas = { n: 0, activas: 0, ilegibles: 0, cuentas: [] });
      const acc = String(it.camp.accountId || ''), s = (SNAPSHOT.saludCuentas || {})[acc] || {}, activo = rgActivo(it);
      cd.n++; if (activo) cd.activas++; if (s.ilegible) cd.ilegibles++;
      let e = cd.cuentas.filter((x) => x.id === acc)[0];
      if (!e) {
        if (cd.cuentas.length >= 3) return;                 // el informe se guarda en el registro en cada refresco: tres cuentas bastan para ver el problema (los conteos de arriba son de todas)
        e = { id: acc, etiqueta: String(s.veredicto || s.etiqueta || s.nombre || ('cuenta ...' + acc.slice(-6))).slice(0, 100), n: 0, activas: 0 };
        if (s.ilegible) e.ilegible = 1;
        cd.cuentas.push(e);
      }
      e.n++; if (activo) e.activas++;
    });
  }
  // Los ids de campaña que la regla tiene asignados a mano y que NINGUN pais ve en sus datos (borrada, archivada, o que nunca gasto): la regla no puede mirarlas. null si no hay ninguna.
  // Se busca en TODOS los paises con datos, no solo en los que se estan evaluando: con un filtro de pais en la pantalla, una campaña de otro pais no es una campaña perdida.
  function rgNoVistas(r) {
    const a = r.aplica || {}; if (a.todas || !(a.campanas || []).length) return null;
    const vistas = new Set();
    Object.keys(SNAPSHOT.countries).forEach((c) => rgItems(c, 'campaign').forEach((it) => vistas.add(String(it.camp.fbId))));
    const faltan = (a.campanas || []).map(String).filter((id) => !vistas.has(id));
    return faltan.length ? { noVistas: faltan.slice(0, 10), nNoVistas: faltan.length } : null;
  }
  // ¿La campaña tiene algun anuncio que de verdad corre? («Encendida» solo dice que el interruptor de la campaña esta prendido: hay campañas viejas con el interruptor prendido y todos sus anuncios apagados.)
  const rgConAnuncioCorriendo = (camp) => {
    for (const s of camp.hijos.values()) for (const a of s.hijos.values()) if (a.estado === 'ACTIVE' && /^\d{6,}$/.test(String(a.fbId))) return true;
    return false;
  };
  // Campañas corriendo (encendidas, en cuentas sanas, con algun anuncio corriendo) y cuantas NINGUNA regla encendida cubre: lo que nadie esta mirando.
  function rgCobertura(lista, paises) {
    const act = (lista || []).filter((r) => r.on && (r.tareas || []).length && rgEnFechas(r)), ejemplos = [];
    let corriendo = 0, sinRegla = 0;
    paises.forEach((c) => rgItems(c, 'campaign').forEach((it) => {
      if (it.n.estado !== 'ACTIVE' || enCuentaCaida(it.camp) || !rgConAnuncioCorriendo(it.n)) return;
      corriendo++;
      if (act.some((r) => rgPaisOk(r, c) && rgAsignada(r, it))) return;
      sinRegla++; if (ejemplos.length < 8) ejemplos.push({ pais: c, id: String(it.n.fbId), nombre: rgCorto(it.n.nombre, 110) });
    }));
    return { corriendo, sinRegla, ejemplos };
  }

  // ---- la evaluacion ----
  // `col` (opcional) = { tareas: [] }: si viene, ademas de las propuestas anota, por cada regla y tarea, TODO lo que miro y los numeros con los que decidio cada cosa (ver rgInforme):
  // asi se ve la cifra exacta de cada condicion aunque no salga ninguna propuesta. No cambia lo que propone.
  function rgEvaluar(lista, paises, sinMemoria, col) {
    const mem = sinMemoria ? {} : rgMemoria(), props = [], vistos = new Set(), conPausa = new Set();
    // Una regla fuera de sus fechas (`programa.desde` / `hasta`) o de su horario (`programa.horaDesde` / `horaHasta`) no propone nada, aunque este encendida.
    const activas = (lista || []).filter((r) => r.on && (r.tareas || []).length && rgEnFechas(r) && rgEnHorario(r));
    // Cobertura (solo con informe): las campañas que la regla tiene asignadas por su id y que los datos no ven (borradas, o que nunca gastaron). Se calcula una vez por regla.
    if (col) activas.forEach((r) => { const f = rgNoVistas(r); if (f) (col.reglas = col.reglas || {})[r.id] = f; });
    activas.forEach((r) => (r.tareas || []).forEach((t, ti) => {
      const ordenAcc = (t.acciones || []).slice().sort((a, b) => (RG_ORDEN_ACC[a.tipo] || 0) - (RG_ORDEN_ACC[b.tipo] || 0));
      const rangoRef = rgRangoRef(t), rango = col ? rangoRef : '', res = col ? { reglaId: r.id, regla: r.nombre, ti, nivel: t.nivel, modo: t.modo, rango, acciones: ordenAcc.map((a) => a.tipo), asignadas: 0, miradas: 0, corren: 0, cumplen: 0, filas: [] } : null;
      if (res) col.tareas.push(res);
      paises.forEach((c) => {
        if (!rgPaisOk(r, c)) return;                       // la regla solo aplica a los paises que se le eligieron
        const asignados = rgItems(c, t.nivel).filter((it) => rgAsignada(r, it));
        // Una campaña en una cuenta de anuncios CAIDA no se evalua (la cuenta no gasta o no se puede leer), pero no se descarta en silencio: se cuenta y se nombra en el informe (rgAnotarCaidas).
        const todos = asignados.filter((it) => !enCuentaCaida(it.camp));
        if (res) { res.asignadas += asignados.length; rgAnotarCaidas(res, c, asignados); }
        const rankCache = new Map();
        const ctx = { ranking: (cond, it) => {
          if (!rankCache.has(cond)) {
            const arr = todos.map((x) => ({ k: rgClave(x), v: rgValor(cond.m, x, cond.ambito || x.nivel, cond.rango || 'last7x1') })).filter((x) => x.v != null);
            const asc = cond.op === 'bottom' || cond.op === 'bottomPct';
            arr.sort((a, b) => (asc ? a.v - b.v : b.v - a.v));
            const n = Math.max(1, Math.round(Number(cond.valor) || 1));
            const cuantos = /Pct$/.test(cond.op) ? Math.max(1, Math.ceil(arr.length * n / 100)) : n;
            rankCache.set(cond, new Set(arr.slice(0, cuantos).map((x) => x.k)));
          }
          return rankCache.get(cond);
        } };
        todos.forEach((it) => {
          // condiciones: grupos unidos con O y, dentro de cada grupo, Y -o al reves segun el modo de la tarea-
          const grupos = t.grupos || [];
          const evaluados = grupos.map((g) => g.map((cond) => rgCondicion(cond, it, ctx)));
          const ok = t.modo === 'or_and_or' ? evaluados.length && evaluados.every((g) => g.some((e) => e.ok))
            : evaluados.some((g) => g.length && g.every((e) => e.ok));
          // Lo que se miro y con que numeros (solo si piden el informe). `ref` = gasto en los dias de la tarea: sirve para poner primero lo que mas pesa.
          const fila = res ? { it, c, cumple: !!ok, g: evaluados.map((g) => g.map((e) => [e.ok ? 1 : 0, e.txt])), acc: [], ref: Number(rgValor('gasto', it, it.nivel, rango)) || 0 } : null;
          if (res) { res.miradas++; if (rgActivo(it)) res.corren++; if (ok) res.cumplen++; res.filas.push(fila); }
          if (!ok) return;
          const razon = [].concat.apply([], evaluados).filter((e) => e.ok && e.txt).map((e) => e.txt).filter((x, i, a) => a.indexOf(x) === i).join(' · ');
          // Los numeros del elemento en los dias de la tarea (gasto, ventas Dash, ROAS): quedan en el registro de lo que se aplico. Se arman una vez por elemento y solo si sale alguna propuesta.
          let cifras = null; const cifrasDe = () => (cifras == null ? (cifras = rgCifrasTxt(it, c, rangoRef)) : cifras);
          ordenAcc.forEach((a) => {
            const cfg = RG_ACCIONES[a.tipo]; if (!cfg || cfg.niveles.indexOf(it.nivel) === -1) return;
            const ms = RG_CADA_MS[a.cada || 'siempre'];
            const cuenta = it.n.accountId || it.camp.accountId || '';
            const base = { regla: r.nombre, reglaId: r.id, country: c, accountId: cuenta, nivel: it.nivel,
              nombre: it.n.nombre, campana: it.camp.nombre, campanaId: String(it.camp.fbId || ''), fbId: it.n.fbId, conjunto: it.set ? it.set.nombre : '', razon, cifras: cifrasDe(),
              sinPermiso: !!cuenta && typeof puedeEscribir === 'function' && puedeEscribir(cuenta) === false };
            // '' si la clave es nueva (y la anota); si no, por que esta accion no sale como propuesta.
            const unico = (k) => {
              if (vistos.has(k)) return 'ya está entre las propuestas (mismo presupuesto de campaña)';
              // Con la hora en que se aplico (o se ignoro): «ya se aplicó (o la ignoraste) a las 3:17 p. m.» dice mas que «hace poco».
              if (rgYaHecha(k, ms, mem, a.tipo)) return 'ya se aplicó (o la ignoraste) ' + (mem[k] && mem[k].t > 0 ? rgCuandoCO(mem[k].t) : 'hace poco');
              vistos.add(k); return '';
            };
            // Saca la propuesta de esta accion sobre este elemento. Devuelve '' si salio, o el motivo por el que no (el informe lo muestra: «cumple, pero ya esta pausado»).
            const intentar = () => {
              if (a.tipo === 'pausar' || a.tipo === 'reactivar') {
                if (a.tipo === 'pausar' ? !rgActivo(it) : !rgPausado(it)) return a.tipo === 'pausar' ? (it.n.estado === 'ACTIVE' ? 'su campaña está pausada, no está corriendo' : 'ya está pausado') : (it.n.estado === 'ACTIVE' ? 'ya está activo' : 'lo tiene pausado su campaña o su conjunto');
                if (conPausa.has(it.nivel + ':' + it.n.fbId) && a.tipo !== 'pausar') return 'ya se propone pausarlo';
                const k = r.id + '|' + a.tipo + '|' + it.nivel + '|' + it.n.fbId, x = unico(k); if (x) return x;
                if (a.tipo === 'pausar') conPausa.add(it.nivel + ':' + it.n.fbId);
                // Si ya se habia pausado antes y vuelve a estar encendido (alguien lo encendio), se dice: asi se entiende por que una regla toca algo que ya toco.
                const antes = a.tipo === 'pausar' && mem[k] && mem[k].ap ? mem[k].t : 0;
                props.push(Object.assign({ clave: k, tipo: a.tipo, ventanaMs: ms, texto: cfg.txt + ': ' + razon + '.' + (antes ? ' Ya se había pausado ' + rgCuandoCO(antes) + ' y volvió a estar encendido.' : '') }, base));
              } else if (a.tipo === 'avisar') {
                const k = r.id + '|avisar|' + it.nivel + '|' + it.n.fbId, x = unico(k); if (x) return x;
                props.push(Object.assign({ clave: k, tipo: 'avisar', ventanaMs: ms, texto: razon + '.' }, base));
              } else if (a.tipo === 'duplicar') {
                if (!rgActivo(it)) return 'no está corriendo';
                const k = r.id + '|duplicar|' + it.nivel + '|' + it.n.fbId, x = unico(k); if (x) return x;
                const pre = rgPresupuestoDe(it), pcm = a.presupuestoCopia, act = pre && pre.nivel === it.nivel ? pre.actual : null;
                // El presupuesto con el que nacera la copia (se puede cambiar antes de aprobar).
                const copiaNuevo = act > 0 && pcm && (pcm.modo === 'pct' || pcm.modo === 'fijar') ? (pcm.modo === 'fijar' ? Math.round(Number(pcm.valor)) : Math.round(act * (1 + Number(pcm.valor) / 100))) : 0;
                props.push(Object.assign({ clave: k, tipo: 'duplicar', ventanaMs: ms, copiaNuevo, actual: act, extra: { pausarOriginal: !!a.pausarOriginal, presupuestoCopia: a.presupuestoCopia || null },
                  texto: 'Duplicar (la copia nace pausada' + (a.pausarOriginal ? ' y el original se pausa' : '') + '): ' + razon + '.' }, base));
              } else {                                             // subir / bajar / fijar
                if (!rgActivo(it)) return 'no está corriendo';
                const pre = rgPresupuestoDe(it); if (!pre) return 'no tiene un presupuesto diario que cambiar a este nivel';
                const nuevo = rgNuevoPresupuesto(a, pre.actual);
                if (a.tipo === 'subir' ? !(nuevo > pre.actual * 1.02) : a.tipo === 'bajar' ? !(nuevo < pre.actual * 0.98) : nuevo === Math.round(pre.actual)) return a.tipo === 'fijar' ? 'ya tiene ese presupuesto' : 'el cambio de presupuesto sería menor al 2 %';
                const k = r.id + '|' + a.tipo + '|' + pre.nivel + '|' + pre.fbId, x = unico(k); if (x) return x;
                props.push(Object.assign({}, base, { clave: k, tipo: a.tipo, ventanaMs: ms, nivel: pre.nivel, fbId: pre.fbId, nombre: pre.nombre, nuevo, actual: pre.actual,
                  texto: cfg.txt + ' diario de ' + fmtMoney(pre.actual, 0) + ' a ' + fmtMoney(nuevo, 0) + ': ' + razon + '.' }));
              }
              return '';
            };
            const motivo = intentar();
            if (fila) fila.acc.push([cfg.txt, motivo ? 0 : 1, motivo]);
            // Una campaña que CUMPLE una regla de pausar, esta encendida y aun asi no sale como accion porque la regla pide espaciar la pausa y ya se aplico antes: la regla esta haciendo lo que se le configuro, pero
            // quien la mira tiene que enterarse (el aviso de scripts/reglas-auto.cjs lo usa). Se anota completa y acotada: no depende de cuantas filas se detallen.
            if (col && motivo && a.tipo === 'pausar' && rgActivo(it) && /^ya se aplicó/.test(motivo)) {
              const sin = col.sinTocar || (col.sinTocar = { n: 0, lista: [] });
              sin.n++; if (sin.lista.length < 20) sin.lista.push({ regla: rgCorto(r.nombre, 80), pais: c, id: String(it.n.fbId || ''), nombre: rgCorto(it.n.nombre, 110) });
            }
          });
        });
      });
    }));
    return props.sort((a, b) => (RG_ORDEN_ACC[a.tipo] - RG_ORDEN_ACC[b.tipo]) || (a.country < b.country ? -1 : 1));
  }

  // ---- choques: dos reglas que se contradicen ----
  // Un choque es una campaña (conjunto o anuncio) que HOY cumple las condiciones de una tarea que la pausa y, a la vez, las de otra que la reactiva -o una tarea que le sube el presupuesto y otra que se lo baja-:
  // sin vender y su segunda tarea reactivaba toda campaña con buen ROAS historico). SOLO AVISA: no cambia ninguna regla ni ninguna campaña. Se comparan tareas del mismo nivel, de reglas distintas o de la misma.
  // Con `opc.ignorarHorario` el horario de las reglas no cuenta (la pantalla avisa aunque la regla de la noche aun no haya abierto su ventana); sin el, solo se miran las reglas vivas en este momento (la revision
  // automatica). Evalua con la misma rgEvaluar que decide las propuestas, asi lo que cumple aqui es exactamente lo que cumpliria alli.
  const RG_OPUESTAS = [['pausar', 'reactivar'], ['subir', 'bajar']];
  const RG_NIV_SG = { campaign: 'campaña', adset: 'conjunto', ad: 'anuncio' };
  // Las horas del dia (0 a 23) en que trabaja una regla: las 24 si no tiene horario. Dos reglas cuyos horarios no se cruzan nunca (una de 5 p. m. a medianoche y otra de 6 a. m. a 5 p. m.) no pueden chocar.
  function rgHorasDe(r) {
    const p = rgProgramaBase(r && r.programa), s = new Set();
    for (let h = 0; h < 24; h++) if (p.horaDesde == null || (p.horaDesde < p.horaHasta ? (h >= p.horaDesde && h < p.horaHasta) : (h >= p.horaDesde || h < p.horaHasta))) s.add(h);
    return s;
  }
  function rgHorariosSeCruzan(a, b) { const x = rgHorasDe(a); for (const h of rgHorasDe(b)) if (x.has(h)) return true; return false; }
  function rgChoques(lista, paises, opc) {
    opc = opc || {};
    const vivas = (lista || []).filter((r) => r && r.on && (r.tareas || []).length && rgEnFechas(r) && (opc.ignorarHorario || rgEnHorario(r)));
    const tareas = [];
    vivas.forEach((r) => (r.tareas || []).forEach((t, ti) => tareas.push({ r, ti, t, tipos: (t.acciones || []).map((a) => a.tipo) })));
    const pares = [];
    for (let i = 0; i < tareas.length; i++) for (let j = i; j < tareas.length; j++) {
      const A = tareas[i], B = tareas[j];
      if (A.t.nivel !== B.t.nivel) continue;
      // Sin contar el horario (la pantalla), dos reglas cuyas ventanas del dia no se cruzan nunca no se contradicen: avisarlo seria una alarma permanente que enseña a ignorarla.
      if (opc.ignorarHorario && A.r !== B.r && !rgHorariosSeCruzan(A.r, B.r)) continue;
      RG_OPUESTAS.forEach((op) => {
        if (A.tipos.indexOf(op[0]) !== -1 && B.tipos.indexOf(op[1]) !== -1) pares.push({ A, B, a: op[0], b: op[1] });
        else if (i !== j && A.tipos.indexOf(op[1]) !== -1 && B.tipos.indexOf(op[0]) !== -1) pares.push({ A: B, B: A, a: op[0], b: op[1] });
      });
    }
    if (!pares.length) return [];
    // Solo se evaluan las reglas que entran en algun par; si el horario no cuenta, se evalua una copia sin el.
    const ids = new Set(); pares.forEach((p) => { ids.add(p.A.r.id); ids.add(p.B.r.id); });
    const aEvaluar = vivas.filter((r) => ids.has(r.id)).map((r) => (opc.ignorarHorario ? Object.assign({}, r, { programa: Object.assign({}, rgProgramaBase(r.programa), { horaDesde: null, horaHasta: null }) }) : r));
    const col = { tareas: [] };
    rgEvaluar(aEvaluar, paises, true, col);
    const cumplen = new Map();                                    // 'reglaId|numero de tarea' -> Map(clave del elemento -> { pais, id, nombre })
    col.tareas.forEach((x) => {
      const m = new Map();
      x.filas.forEach((f) => { if (f.cumple) m.set(rgClave(f.it), { pais: f.c, id: String(f.it.n.fbId || ''), nombre: rgCorto(f.it.n.nombre, 110) }); });
      cumplen.set(x.reglaId + '|' + x.ti, m);
    });
    const out = [];
    pares.forEach((p) => {
      const a = cumplen.get(p.A.r.id + '|' + p.A.ti), b = cumplen.get(p.B.r.id + '|' + p.B.ti); if (!a || !b) return;
      const comunes = []; a.forEach((v, k) => { if (b.has(k)) comunes.push(v); });
      if (!comunes.length) return;
      const c = { nivel: p.A.t.nivel, a: { reglaId: p.A.r.id, regla: rgCorto(p.A.r.nombre, 80), accion: p.a, horario: rgHorasTxt(rgProgramaBase(p.A.r.programa)) },
        b: { reglaId: p.B.r.id, regla: rgCorto(p.B.r.nombre, 80), accion: p.b, horario: rgHorasTxt(rgProgramaBase(p.B.r.programa)) }, n: comunes.length, ejemplos: comunes.slice(0, 3) };
      c.frase = rgChoqueFrase(c);                                 // la revision automatica la guarda y la manda por Telegram tal cual (el script no tiene su propia copia del texto)
      out.push(c);
    });
    return out.sort((x, y) => y.n - x.n).slice(0, 8);
  }
  // La frase de un choque, en palabras de quien arma las reglas: quien hace que, sobre cuantas y que pasa en cada vuelta.
  function rgChoqueFrase(c) {
    // `c` puede venir del registro guardado (JSON de una revision anterior): se lee con cuidado.
    const a = (c && c.a) || {}, b = (c && c.b) || {}, n = Math.max(0, Math.round(Number(c && c.n)) || 0);
    const misma = a.reglaId === b.reglaId, pausa = a.accion === 'pausar', pl = RG_NIV_PL[c && c.nivel] || 'elementos', sg = RG_NIV_SG[c && c.nivel] || 'elemento';
    const quien = pausa
      ? (misma ? '«' + a.regla + '» pausa y reactiva' : '«' + a.regla + '» pausa y «' + b.regla + '» reactiva')
      : (misma ? '«' + a.regla + '» sube y baja el presupuesto' : '«' + a.regla + '» sube el presupuesto y «' + b.regla + '» lo baja');
    return quien + ' las mismas ' + pl + ': hoy ' + n + ' ' + (n === 1 ? sg + ' cumple' : pl + ' cumplen') + ' las condiciones de las dos a la vez. ' +
      (pausa ? 'Una la apaga y la otra la vuelve a encender en la revisión siguiente.' : 'Una le sube el presupuesto y la otra se lo baja en la revisión siguiente.');
  }

  // ---- informe de una revision: los numeros exactos con los que se decidio ----
  // Lo arma rgEvaluar cuando le pasan `col`. Se pinta en la pantalla («Revisar ahora», «Ver qué haría ahora») y lo guarda en el registro scripts/reglas-auto.cjs (campo `informe`), asi que tiene que ser corto:
  // por regla y tarea cuenta TODO lo que miro y detalla solo las primeras filas (primero las que cumplen y, entre ellas, las que mas gastaron).
  const RG_NIV_PL = { campaign: 'campañas', adset: 'conjuntos', ad: 'anuncios' };
  // Los dias a los que se refieren las cifras de cada fila: los de la primera condicion con periodo de la tarea.
  function rgRangoRef(t) {
    let rango = '';
    (t.grupos || []).forEach((g) => g.forEach((c) => { if (!rango && c.rango && !rgSinPeriodo(c)) rango = c.rango; }));
    return rango || 'last7x1';
  }
  const rgCorto = (s, n) => { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  // El gasto tambien en la moneda de la cuenta de anuncios (Colombia: pesos), como el KPI «Gasto en ads»: gasto en USD por la tasa del dia. En cuentas en dolares no agrega nada.
  function rgGastoLocal(c, usd) {
    const cur = rgMonedaCuenta(c), fx = SNAPSHOT.fx && SNAPSHOT.fx[cur];
    return cur !== 'USD' && fx > 0 ? ' (' + fmtMoney(usd * fx, 0) + ' ' + (CURRENCY_LABEL[cur] || cur) + ')' : '';
  }
  function rgCifrasTxt(it, c, rango) {
    const gasto = Number(rgValor('gasto', it, it.nivel, rango)) || 0, ro = rgValor('roas', it, it.nivel, rango), ve = rgValor('ventas', it, it.nivel, rango) || 0;
    return 'Gasto ' + rgFmtValor('usd', gasto) + rgGastoLocal(c, gasto) + ' · ' + rgFmtValor('n', ve) + (ve === 1 ? ' venta' : ' ventas') + ' Dash · ROAS ' + rgFmtValor('x', ro);
  }
  function rgFilaInforme(f, rango) {
    const it = f.it;
    return { pais: f.c, campana: rgCorto(it.camp.nombre, 110), conjunto: it.set ? rgCorto(it.set.nombre, 110) : '', nombre: rgCorto(it.n.nombre, 110), estado: it.n.estado === 'ACTIVE' ? 'ACTIVE' : 'PAUSED', cumple: f.cumple ? 1 : 0,
      cifras: rgCifrasTxt(it, f.c, rango), g: f.g, acc: f.acc };
  }
  function rgInforme(col, lim) {
    const max = Math.max(1, (lim && lim.filas) || 30), porR = new Map(), reglas = [];
    ((col && col.tareas) || []).forEach((t) => {
      let r = porR.get(t.reglaId);
      if (!r) { r = { id: t.reglaId, nombre: rgCorto(t.regla, 80), tareas: [] }; porR.set(t.reglaId, r); reglas.push(r); if (col.reglas && col.reglas[t.reglaId]) Object.assign(r, col.reglas[t.reglaId]); }
      const filas = t.filas.slice().sort((a, b) => (b.cumple - a.cumple) || (b.ref - a.ref)).slice(0, max).map((f) => rgFilaInforme(f, t.rango));
      // `asignadas` = lo que la regla tiene asignado en este nivel; `miradas` = lo que de verdad se evaluo; la diferencia (`caidas`) son elementos de cuentas de anuncios caidas.
      const tarea = { nivel: t.nivel, modo: t.modo === 'or_and_or' ? 'or_and_or' : 'and_or_and', cuando: rgRangoTxt(t.rango), asignadas: t.asignadas || 0, miradas: t.miradas, corren: t.corren, cumplen: t.cumplen, filas };
      if (t.caidas) tarea.caidas = t.caidas;
      r.tareas.push(tarea);
    });
    const inf = { datos: String(SNAPSHOT.generatedAt || ''), reglas };
    if (col && col.sinTocar) inf.sinTocar = col.sinTocar;            // campañas que cumplen una regla de pausar, siguen encendidas y no se pausaron porque la frecuencia de la regla lo espacia
    return inf;
  }

  // ---- reglas v2 (una condicion plana por regla) -> v3 ----
  function rgMigrar(r) {
    if (r && Array.isArray(r.tareas)) return r;
    const rango = r.ventana === 'today' ? 'today' : ({ last7: 'last7', last15: 'last15', last30: 'last30' }[r.ventana] || 'last7') + 'x' + Math.min(7, Math.max(0, Math.round(r.madurez || 0)));
    const g = [];
    const c = (m, op, valor) => g.push({ m, ambito: 'adset', rango, op, valor });
    if (r.gasto > 0) c('gasto', '>=', r.gasto);
    if (r.roas != null) c('roas', r.roasOp === '>=' ? '>=' : '<', r.roas);
    if (r.ventas != null) c('ventas', r.ventasOp === '>=' ? '>=' : '<=', r.ventas);
    if (r.cpa != null) c('cpa', '>=', r.cpa);
    if (r.minDias > 0) c('dias', '>=', r.minDias);
    const acc = { tipo: r.accion === 'subir' ? 'subir' : r.accion === 'bajar' ? 'bajar' : r.accion, cada: 'dia', modo: 'pct', valor: r.pct || 20, tope: r.tope || 0 };
    return { id: r.id, nombre: r.nombre, on: !!r.on, auto: true, programa: rgProgramaBase(), aplica: { todas: true, campanas: [], futuras: false, base: [], filtro: { op: 'and', filas: [] }, paises: [] },
      tareas: [{ nivel: 'adset', modo: 'and_or_and', grupos: [g], acciones: [acc] }] };
  }
  function rgNuevaRegla() {
    return { id: 'r' + Date.now().toString(36), nombre: 'Regla nueva', on: true, auto: true, programa: rgProgramaBase(),
      aplica: { todas: false, campanas: [], futuras: false, base: [], filtro: { op: 'and', filas: [] }, paises: [] },
      tareas: [rgNuevaTarea()] };
  }
  function rgNuevaTarea() {
    return { nivel: 'adset', modo: 'and_or_and',
      grupos: [[{ m: 'gasto', ambito: 'adset', rango: 'last7x1', op: '>=', valor: 25 }, { m: 'roas', ambito: 'adset', rango: 'last7x1', op: '<', valor: 1 }]],
      acciones: [{ tipo: 'pausar', cada: 'siempre' }] };
  }
  // Las estrategias listas, como las de AdsCook, con nuestras metricas (todo en USD y ventas de la hoja).
  const RG_PLANTILLAS = [
    { nombre: 'Reactivar los que remontan', desc: 'Conjuntos pausados que rinden bien en los últimos 3 días.',
      t: { nivel: 'adset', grupos: [[['roas', 'last3x1', '>', 2], ['ventas', 'last3x1', '>=', 2]]], acciones: [{ tipo: 'reactivar', cada: 'dia' }] } },
    { nombre: 'Pausar a los peores anuncios', desc: 'Anuncio que gasta y rinde menos que su conjunto.',
      t: { nivel: 'ad', grupos: [[['gasto', 'last3x1', '>', 50], { m: 'roas', ambito: 'ad', rango: 'last3x1', op: '<', vs: { t: 'metrica', m: 'roas', ambito: 'adset', rango: 'last3x1', factor: 1 } }]], acciones: [{ tipo: 'pausar', cada: 'siempre' }] } },
    { nombre: 'Pausar sin conversión', desc: 'Conjunto que gastó y no vendió nada.',
      t: { nivel: 'adset', grupos: [[['gasto', 'last3x1', '>', 50], ['ventas', 'last3x1', '=', 0]]], acciones: [{ tipo: 'pausar', cada: 'siempre' }] } },
    { nombre: 'Pausar con ROAS negativo', desc: 'Conjunto que gastó y no recupera lo invertido.',
      t: { nivel: 'adset', grupos: [[['gasto', 'last3x1', '>', 50], ['roas', 'last3x1', '<', 1]]], acciones: [{ tipo: 'pausar', cada: 'siempre' }] } },
    { nombre: 'Escalar agresivo pero seguro', desc: 'Duplica (la copia nace pausada, con +50 % de presupuesto) al conjunto con ROAS alto y ventas.',
      t: { nivel: 'adset', grupos: [[['roas', 'last3x1', '>', 3], ['ventas', 'last3x1', '>', 10]]], acciones: [{ tipo: 'duplicar', cada: 'una_vez', pausarOriginal: false, presupuestoCopia: { modo: 'pct', valor: 50 } }] } },
    { nombre: 'Escalar a los mejores', desc: 'Sube 20 % el presupuesto de la campaña cuyo ROAS está en el 30 % mejor.',
      t: { nivel: 'campaign', grupos: [[['roas', 'last3x1', 'topPct', 30], ['ventas', 'last3x1', '>', 0]]], acciones: [{ tipo: 'subir', modo: 'pct', valor: 20, cada: 'dia' }] } },
    { nombre: 'Pausar si la conversación no avanza', desc: 'Conjunto que gastó, no vendió y cuya conversación con 3+ mensajes sale cara (parte de $3,70: ajústalo a tu promedio).',
      t: { nivel: 'adset', grupos: [[['gasto', 'last3x1', '>', 25], ['ventas', 'last3x1', '=', 0], ['costoConv3', 'last3x1', '>', 3.7]]], acciones: [{ tipo: 'pausar', cada: 'siempre' }] } },
    { nombre: 'Escalar a los que cierran', desc: 'Sube 20 % el presupuesto del conjunto que vende 15 % o más de sus conversaciones con ROAS de 1,5 o más (valores de partida: ajústalos a tu promedio).',
      t: { nivel: 'adset', grupos: [[['gasto', 'last3x1', '>', 15], ['ventasPorConv', 'last3x1', '>=', 15], ['roas', 'last3x1', '>=', 1.5]]], acciones: [{ tipo: 'subir', modo: 'pct', valor: 20, tope: 0, cada: 'dia' }] } },
    { nombre: 'Escalar rentables', desc: 'Sube 30 % el presupuesto del conjunto con 5 ventas o más y ROAS sobre 2.',
      t: { nivel: 'adset', grupos: [[['ventas', 'last3x1', '>', 5], ['roas', 'last3x1', '>', 2]]], acciones: [{ tipo: 'subir', modo: 'pct', valor: 30, tope: 0, cada: 'dia' }] } },
  ];
  function rgDePlantilla(p) {
    const r = rgNuevaRegla(); r.nombre = p.nombre;
    const t = p.t, nivel = t.nivel;
    r.tareas = [{ nivel, modo: 'and_or_and', acciones: t.acciones.map((a) => Object.assign({}, a)),
      grupos: t.grupos.map((g) => g.map((c) => (Array.isArray(c) ? { m: c[0], ambito: nivel, rango: c[1], op: c[2], valor: c[3] } : Object.assign({}, c)))) }];
    return r;
  }

  // ---- frase de una regla para la lista ----
  function rgResumenTarea(t) {
    const accs = (t.acciones || []).map((a) => {
      let s = (RG_ACCIONES[a.tipo] || { txt: a.tipo }).txt;
      if (a.tipo === 'subir' || a.tipo === 'bajar') s += ' ' + (a.modo === 'abs' ? fmtMoney(a.valor, 0) : a.valor + '%');
      if (a.tipo === 'fijar') s += ' a ' + fmtMoney(a.valor, 0);
      return s;
    });
    return RG_NIV[t.nivel] + ': ' + accs.join(' + ');
  }
  // «Costo por venta (USD)» -> «costo por venta»; las siglas se quedan en mayusculas (ROAS, CTR, CPM, CPC) y Dash / Meta tambien («ventas Dash»).
  function rgNomMetrica(d) { const s = rgEtq(d); return /^(ROAS|CTR|CPM|CPC)$/.test(s) ? s : s.toLowerCase().replace(/\bdash\b/, 'Dash').replace(/\bmeta\b/, 'Meta'); }
  // Estado, texto y presupuesto no miran un periodo: ponerselo en la frase («presupuesto diario (ultimos 3 dias)») confunde.
  const rgSinPeriodo = (c) => { const d = RG_METRICAS[c.m]; return !d || d.tipo === 'estado' || d.tipo === 'texto' || d.tipo === 'numero' || c.m === 'presupuesto'; };
  function rgFraseRegla(r) {
    return (r.tareas || []).map((t) => {
      const gr = (t.grupos || []).map((g) => g.map((c) => {
        const d = RG_METRICAS[c.m]; if (!d) return '';
        if (d.tipo === 'numero') return 'el número de WhatsApp de la campaña es ' + rgNumTxt(c.valor);
        if (d.tipo === 'estado' || d.tipo === 'texto') return d.txt.toLowerCase() + ' ' + rgOpTxt(c.op) + ' ' + (d.tipo === 'estado' ? rgFmtValor('estado', c.valor) : '«' + c.valor + '»');
        const ref = c.vs && c.vs.t === 'metrica' ? (c.vs.factor != null && Number(c.vs.factor) !== 1 ? c.vs.factor + ' × ' : '') + (RG_METRICAS[c.vs.m] ? rgEtq(RG_METRICAS[c.vs.m]) : '?') + ' del ' + RG_NIV[c.vs.ambito || t.nivel].toLowerCase() : rgFmtValor(d.tipo, c.valor);
        return rgNomMetrica(d) + (rgSinPeriodo(c) ? '' : ' (' + rgRangoTxt(c.rango) + ')') + ' ' + rgOpTxt(c.op).replace('N', c.valor) + (/^(top|bottom)/.test(c.op) ? '' : ' ' + ref);
      }).filter(Boolean).join(t.modo === 'or_and_or' ? ' o ' : ' y '));
      return 'Si ' + gr.join(t.modo === 'or_and_or' ? ' y ' : ' o ') + ' → ' + rgResumenTarea(t).split(': ')[1].toLowerCase() + '.';
    }).join(' ');
  }
