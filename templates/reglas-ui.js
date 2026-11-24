  // ---------- Reglas v3: pantalla (lista, galeria, editor, asignar campañas, registro) ----------
  //
  // Segunda mitad del modulo (la primera es reglas-modulo.js, el motor). Se mete en el mismo script del dashboard.
  // Toda la pantalla cuelga de #rg-root y usa UN solo juego de escuchas delegadas, asi se puede repintar sin perderlas.
  let reglas = [];                 // lista v3 guardada
  let rgNota = '';                 // aviso informativo arriba de la lista de reglas; se va al salir de la lista
  let rgVivasPrevias = null;       // ids de las reglas que YA trabajaban (encendidas, dentro de fechas y con campañas) la ultima vez que se guardo: una que pasa a trabajar pide una revision enseguida
  let reglasDisponibles = false;   // false = no es el dueño / la trafficker (o no hay KV): la tarjeta no sale
  let rgVista = 'reglas';          // 'propuestas' (pestaña «Por ejecutar») | 'reglas' | 'campanas' | 'registro' | 'editor'
  let rgBorrador = null;           // copia de trabajo de la regla que se edita (nada se guarda hasta «Guardar regla»)
  let rgIdx = -1;                  // posicion en `reglas` de la que se edita; -1 = nueva
  let rgPlantilla = '';            // nombre de la plantilla de la que sale el borrador abierto ('' = en blanco o una regla ya guardada)
  let rgDesdeLista = false;        // el modal de asignar se abrio desde la lista: «Listo» guarda de una vez
  let rgModal = null;              // null | 'galeria' | 'asignar' | 'filtro'
  let rgActuales = [];
  let rgCubreAbierta = '';         // id de la regla cuya lista de campañas cubiertas esta desplegada
  const rgQ = { reglas: '', soloOn: false, asig: '', asigSolo: true, asigPais: '' };
  let rgLogPrueba = null, rgFuenteLog = 'dash', rgEstado = '';      // rgLogPrueba = data/reglas-auto-log.json (lo que decidio cada revision); rgFuenteLog = 'dash' (lo aplicado) | 'revisiones'
  let rgRevisarId = '', rgInformeVivo = null;      // «Revisar ahora»: la regla cuyas cifras estan desplegadas en la lista y su informe (se recalcula con cada render)
  let rgRegServidor = null, rgRegListo = false;   // el registro compartido (lo que se aplico desde el dash en cualquier dispositivo); null = no se pudo leer; listo = ya se intento leer
  // pestaña «Campañas»: ver las campañas que corren y asignarles reglas (como el «Automate» de AdsCook)
  const rgC = { q: '', solo: true, vista: 'todas', sel: new Set(), abierta: '', limite: 100, regla: '', orden: 'gasto', sinRegla: false };
  let rgTimerGuardar = 0;
  // El editor del programa (cada cuanto se revisa, entre que fechas y en que horario del dia). Solo de pantalla: lo que se guarda son los minutos, las fechas y las horas.
  let rgProgOtro = false, rgProgUnidad = 'min', rgProgFechas = false, rgProgHoras = false;
  let rgChoquesMemo = { firma: '', v: [] };    // reglas que se contradicen hoy (rgChoques), por huella de las reglas y de los datos: no se repite la cuenta en cada repintado

  const rgCopia = (x) => JSON.parse(JSON.stringify(x));
  const rgOpt = (v, txt, sel) => '<option value="' + escHtml(String(v)) + '"' + (sel ? ' selected' : '') + '>' + escHtml(txt) + '</option>';
  const rgSelect = (path, opts, val, extra) => '<select data-p="' + path + '"' + (extra || '') + '>' + opts.map((o) => rgOpt(o[0], o[1], String(o[0]) === String(val))).join('') + '</select>';
  const rgNumIn = (path, val, extra) => '<input type="number" inputmode="decimal" step="any" data-p="' + path + '" value="' + (val == null || val === '' ? '' : escHtml(String(val))) + '"' + (extra || '') + '>';
  function rgGet(o, path) { return path.split('.').reduce((a, k) => (a == null ? a : a[k]), o); }
  function rgSet(o, path, v) { const ks = path.split('.'), ult = ks.pop(); ks.reduce((a, k) => a[k], o)[ult] = v; }
  const rgAmbitos = (nivel) => RG_NIVELES.filter((n) => RG_NIV_ORDEN[n[0]] <= RG_NIV_ORDEN[nivel]);

  function rgNormalizar(r) {
    r = rgMigrar(r);
    r.aplica = Object.assign({ todas: false, campanas: [], futuras: false, base: [], filtro: { op: 'and', filas: [] }, paises: [] }, r.aplica || {});
    r.aplica.paises = (Array.isArray(r.aplica.paises) ? r.aplica.paises : []).filter((c) => SNAPSHOT.countries[c]);
    r.aplica.filtro = Object.assign({ op: 'and', filas: [] }, r.aplica.filtro || {});
    r.programa = rgProgramaBase(r.programa);
    r.auto = true;                                          // ya no hay «revision automatica» aparte: una regla encendida se revisa y se ejecuta sola
    r.tareas = (r.tareas || []).map((t) => Object.assign({ nivel: 'adset', modo: 'and_or_and', grupos: [], acciones: [] }, t));
    return r;
  }

  // ---- coherencia: lo que cambia al tocar una metrica, un operador, un nivel o una accion ----
  function rgArreglarCond(c, nivel) {
    if (!RG_METRICAS[c.m]) c.m = 'gasto';
    const d = RG_METRICAS[c.m], ambs = rgAmbitos(nivel).map((a) => a[0]);
    if (ambs.indexOf(c.ambito) === -1) c.ambito = nivel;
    if (c.m === 'presupuesto' && c.ambito === 'ad') c.ambito = ambs.indexOf('adset') !== -1 ? 'adset' : 'campaign';
    if (!c.rango) c.rango = 'last7x1';
    if (d.tipo === 'texto') {
      if (RG_OPS_TXT.every((o) => o[0] !== c.op)) c.op = 'contiene';
      if (typeof c.valor !== 'string') c.valor = '';
      delete c.vs;
    } else if (d.tipo === 'estado') {
      if (c.op !== 'es' && c.op !== 'no_es') c.op = 'es';
      if (c.valor !== 'PAUSED') c.valor = 'ACTIVE';
      delete c.vs;
    } else if (d.tipo === 'numero') {
      // El numero de WhatsApp es de la campaña (no del conjunto ni del anuncio) y solo se compara con «es»: ver rgCondicion.
      c.op = 'es'; c.ambito = 'campaign';
      if (RG_NUM_VALORES.every((e) => e[0] !== c.valor)) c.valor = 'connected';
      delete c.vs;
    } else {
      if (RG_OPS_NUM.concat(RG_OPS_RANK).every((o) => o[0] !== c.op)) c.op = '>';
      if (typeof c.valor === 'string') c.valor = Number(c.valor) || 0;
      if (/^(top|bottom)/.test(c.op)) { delete c.vs; if (!(Number(c.valor) > 0)) c.valor = 10; }
      if (c.vs) {
        if (!RG_METRICAS[c.vs.m] || /^(estado|texto|numero)$/.test(RG_METRICAS[c.vs.m].tipo)) c.vs.m = 'roas';
        if (ambs.indexOf(c.vs.ambito) === -1) c.vs.ambito = nivel;
        if (!c.vs.rango) c.vs.rango = c.rango;
        if (c.vs.factor == null || c.vs.factor === '') c.vs.factor = 1;
      }
    }
  }
  function rgArreglarAccion(a, nivel) {
    if (!RG_ACCIONES[a.tipo] || RG_ACCIONES[a.tipo].niveles.indexOf(nivel) === -1) a.tipo = 'pausar';
    if (a.tipo === 'subir' || a.tipo === 'bajar') { if (a.modo !== 'abs') a.modo = 'pct'; if (!(Number(a.valor) > 0)) a.valor = 20; }
    if (a.tipo === 'fijar' && !(Number(a.valor) > 0)) a.valor = 10;
    if (a.tipo === 'duplicar') a.presupuestoCopia = Object.assign({ modo: 'igual', valor: 0 }, a.presupuestoCopia || {});
    if (!RG_CADA_MS.hasOwnProperty(a.cada)) a.cada = 'siempre';
  }
  function rgArreglarTarea(t) {
    t.grupos.forEach((g) => g.forEach((c) => rgArreglarCond(c, t.nivel)));
    t.acciones.forEach((a) => rgArreglarAccion(a, t.nivel));
  }

  // ---- campañas para asignar ----
  function rgCampanas(paises) {
    const out = [];
    paises.forEach((c) => arbolDeVentana(c, 'ALL').forEach((camp) => {
      if (!camp.fbId) return;
      let ads = 0; camp.hijos.forEach((s) => { ads += s.hijos.size; });
      const caida = enCuentaCaida(camp), activa = camp.estado === 'ACTIVE';
      out.push({ c, id: String(camp.fbId), nombre: camp.nombre || '', activa, corre: activa && !caida, sets: camp.hijos.size, ads, caida, acct: camp.accountId || '' });
    }));
    return out;
  }
  // «Activa» en Facebook no es «corre»: una campaña encendida en una cuenta caida no entrega nada. Los filtros «Solo activas» y «Solo corriendo» usan «corre»,
  // y las encendidas sin entrega se dicen aparte en vez de pasar por corriendo.
  const rgNivelCamp = (x) => (x.corre ? 2 : x.activa ? 1 : 0);
  const rgEstadoCamp = (x) => (x.corre ? { txt: 'Corriendo', pill: 'good' } : x.activa ? { txt: 'Encendida · cuenta caída', pill: 'danger' } : { txt: 'Pausada', pill: 'neutral' });
  const rgPaisesTodos = () => Object.keys(SNAPSHOT.countries);
  const rgTodasCamp = () => (_rgCamp || (_rgCamp = rgCampanas(rgPaisesTodos())));
  // ---- el filtro «País» de arriba de la pagina manda en las cuatro pestañas ----
  // Solo decide que se VE: la revision automatica sigue mirando todos los paises y ninguna regla ni campaña cambia por filtrar. null = estan todos los paises elegidos, no hay nada que ocultar.
  function rgFiltroPais() { const cs = selectedCountries(); return cs.length >= rgPaisesTodos().length ? null : new Set(cs); }
  function rgFiltroNombre(fp) { const l = Array.from(fp).map((c) => COUNTRY_LABEL[c] || c); return l.length === 1 ? l[0] : l.length === 2 ? l.join(' y ') : 'los países elegidos'; }
  // ¿La regla trabaja en los paises elegidos? La que tiene paises elegidos, por esos; la de «todos los paises», si entra por «Todas» o por las futuras (caben en cualquiera) o si alguna de sus campañas es de ahi.
  // Una regla que no se puede ubicar en ningun pais (sin nada asignado, o con campañas que ya no estan en los datos) no es de otro pais, asi que se ve siempre: si no, al crearla con un pais filtrado desapareceria de la lista.
  function rgReglaEnFiltro(r, fp) {
    if (!fp) return true;
    const a = r.aplica, p = a.paises || [];
    if (p.length) return p.some((c) => fp.has(c));
    const cub = rgCubre(r); return !!a.todas || !!a.futuras || !cub.length || cub.some((x) => fp.has(x.c));
  }
  // La linea que dice que hay un filtro puesto y cuanto deja por fuera. '' si no oculta nada.
  function rgNotaFiltro(fp, n, sing, plur) { return fp && n > 0 ? '<p class="card-sub">Viendo solo ' + escHtml(rgFiltroNombre(fp)) + '. ' + (n === 1 ? 'No aparece 1 ' + sing : 'No aparecen ' + fmtInt(n) + ' ' + plur) + '. Para ver todo, cambia «País» arriba.</p>' : ''; }
  // Un choque entre reglas se ve si alguna de sus campañas de ejemplo es de los paises elegidos. Si la lista de ejemplos se corto, se decide por las dos reglas: un aviso no debe perderse por un ejemplo que no alcanzo a entrar.
  function rgChoqueEnFiltro(c, fp) {
    if (!fp) return true;
    const ej = Array.isArray(c.ejemplos) ? c.ejemplos.filter(Boolean) : [];
    if (ej.some((e) => fp.has(e.pais))) return true;
    if (ej.length >= (Number(c.n) || 0)) return false;
    const enFiltro = (x) => { const r = reglas.filter((y) => y.id === x.reglaId)[0]; return !!r && rgReglaEnFiltro(r, fp); };
    return enFiltro(c.a) && enFiltro(c.b);
  }
  // Las campañas que una regla cubre HOY: las de sus paises que entran por «Todas», por la lista manual o por el filtro de futuras.
  function rgCubre(r) {
    const a = r.aplica, man = new Set(a.campanas), base = new Set(a.base);
    return rgTodasCamp().filter((x) => rgPaisOk(r, x.c) && (a.todas || man.has(x.id) || (a.futuras && !base.has(x.id) && rgFiltroCoincide(a.filtro, x.nombre))));
  }
  const rgPaisesTxt = (r) => ((r.aplica.paises || []).length ? r.aplica.paises.map(conBandera).join(' · ') : 'todos los países');
  // Aviso cuando la regla usa montos de presupuesto y sus paises no comparten moneda de cuenta.
  function rgAvisoMoneda(r) {
    if (!rgUsaMontos(r)) return '';
    const m = rgMonedasAlcance(r), ks = Object.keys(m);
    if (ks.length < 2) return '';
    return '⚠ Esta regla usa montos (presupuesto, gasto o costo por conversación en la moneda de la cuenta), que se leen en la moneda de la cuenta de anuncios (' + ks.map((k) => k + ': ' + m[k].map((c) => COUNTRY_LABEL[c] || c).join(', ')).join(' · ') + '). Un monto como 8.000 no significa lo mismo en pesos que en dólares: marca solo países con la misma moneda o crea una regla por país.';
  }
  function rgResumenAplica(r, cub) {
    const a = r.aplica, n = (cub || rgCubre(r)).length, p = a.paises || [];
    if (a.todas) return 'Todas las campañas · ' + rgPaisesTxt(r) + ' (' + n + ')';
    return (n ? n + (n === 1 ? ' campaña' : ' campañas') : 'Ninguna campaña') + (p.length ? ' · ' + rgPaisesTxt(r) : '') + (a.futuras ? ' + las futuras que cumplan el filtro' : '');
  }
  // La lista de lo que cubre una regla, por pais: nombre y si esta corriendo.
  function rgCubreHtml(r, cub, soloEn) {
    if (!cub.length) return '<div class="rg-cubre"><p class="rg-vacio">' + (soloEn ? 'Esta regla no tiene campañas en ' + escHtml(soloEn) + '.' : 'Esta regla todavía no cubre ninguna campaña.') + ' Usa «Asignar campañas».</p></div>';
    const por = {}; cub.forEach((x) => { (por[x.c] = por[x.c] || []).push(x); });
    let h = '<div class="rg-cubre">';
    Object.keys(por).forEach((c) => {
      const l = por[c].slice().sort((a, b) => (rgNivelCamp(b) - rgNivelCamp(a)) || (a.nombre < b.nombre ? -1 : a.nombre > b.nombre ? 1 : 0)), corren = l.filter((x) => x.corre).length;
      h += '<div class="rg-cubre-pais"><b>' + escHtml(conBandera(c)) + '</b> · ' + l.length + (l.length === 1 ? ' campaña' : ' campañas') + ' · ' + corren + (corren === 1 ? ' corre' : ' corren') + ' ahora</div>';
      l.slice(0, 25).forEach((x) => { const e = rgEstadoCamp(x); h += '<div class="rg-cubre-fila"><span class="rg-cubre-nom">' + escHtml(x.nombre) + '</span><span class="pill ' + e.pill + '">' + e.txt + '</span></div>'; });
      if (l.length > 25) h += '<p class="rg-mut rg-cubre-mas">…y ' + (l.length - 25) + ' más de ' + escHtml(conBandera(c)) + '. Están todas en «Asignar campañas».</p>';
    });
    return h + '</div>';
  }
  function rgResumenFiltro(f) {
    if (!f || !f.filas.length) return 'sin filtro (todas las nuevas)';
    return f.filas.map((x) => 'nombre ' + rgOpTxt(x.op) + ' «' + x.valor + '»').join(f.op === 'or' ? ' o ' : ' y ');
  }

  // ---- programa: cada cuanto se revisa sola y entre que fechas (las cuentas viven en reglas-modulo.js) ----
  const RG_UNIDAD = { min: 1, h: 60, d: 1440 };
  const rgUnidadDe = (min) => (min > 0 && min % 1440 === 0 ? 'd' : min > 0 && min % 60 === 0 ? 'h' : 'min');
  function rgHaceTxt(t) {
    const m = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (m < 2) return 'hace un momento';
    if (m < 60) return 'hace ' + m + ' min';
    const h = m / 60;
    if (h < 48) return 'hace ' + (Math.round(h * 10) / 10).toString().replace('.', ',') + ' h';
    return 'hace ' + Math.round(h / 24) + ' días';
  }
  // Lo que el sistema tarda DE VERDAD entre una revision y la siguiente, sacado de las corridas que anoto el refresco: es lo unico que se puede prometer, por mas corto que sea el intervalo elegido.
  function rgRitmo() {
    const ahora = Date.now(), c = ((rgLogPrueba && rgLogPrueba.corridas) || []).filter((t) => ahora - t < 2 * 864e5).sort((a, b) => a - b);
    if (c.length < 3) return null;
    const g = []; for (let i = 1; i < c.length; i++) g.push(c[i] - c[i - 1]);
    g.sort((a, b) => a - b);
    return { n24: c.filter((t) => ahora - t < 864e5).length, medianaMin: Math.round(g[Math.floor(g.length / 2)] / 60000), ultima: c[c.length - 1] };
  }
  function rgProgramaFrase(r) {
    const p = rgProgramaBase(r.programa), f = rgFechasTxt(p), h = rgHorasTxt(p);
    return 'Se revisa y se ejecuta ' + rgCadaTxt(p.cadaMin) + ', ' + (f || 'sin límite de fechas mientras esté activa') + (h ? ', solo ' + h + ' (hora de Colombia)' : '') + '.';
  }
  // Lo que decidio la ultima revision de una regla, en una frase: «pausó 3 · 1 no se hizo». Sale de las decisiones que el refresco anoto con la hora de esa revision.
  function rgResumenDecisiones(r, u) {
    const es = ((rgLogPrueba && rgLogPrueba.entradas) || []).filter((x) => x.reglaId === r.id && x.t === u && x.estado !== 'aviso');
    if (!es.length) return '';
    const cuenta = (e) => es.filter((x) => x.estado === e).length, v = { pausar: 'pausó', reactivar: 'reactivó', subir: 'subió presupuesto a', bajar: 'bajó presupuesto a', fijar: 'fijó presupuesto a', duplicar: 'duplicó' };
    const porTipo = {}; es.filter((x) => x.estado === 'hecha').forEach((x) => { porTipo[x.tipo] = (porTipo[x.tipo] || 0) + 1; });
    const partes = Object.keys(porTipo).map((t) => (v[t] || t) + ' ' + porTipo[t]);
    if (cuenta('fallo')) partes.push(cuenta('fallo') + (cuenta('fallo') === 1 ? ' falló' : ' fallaron'));
    if (cuenta('pendiente')) partes.push(cuenta('pendiente') + ' por ejecutar');
    const no = cuenta('omitida'); if (no) partes.push(no + (no === 1 ? ' no se hizo' : ' no se hicieron'));
    return partes.length ? ': ' + partes.join(' · ') : '';
  }
  // En la tarjeta de la regla: que va a pasar con ella, cuando se reviso por ultima vez y desde cuando le toca de nuevo. `nCamp` = cuantas campañas cubre.
  function rgRevisionLinea(r, nCamp) {
    if (!r.on) return 'Apagada: no se revisa ni se ejecuta';
    const p = rgProgramaBase(r.programa), hoy = rgHoyCO();
    if (p.desde && hoy < p.desde) return 'Empieza el ' + rgFechaCorta(p.desde);
    if (p.hasta && hoy > p.hasta) return 'Terminó el ' + rgFechaCorta(p.hasta) + ': ya no se revisa';
    if (!nCamp && !r.aplica.futuras) return 'Sin campañas asignadas: todavía no hace nada. Usa «Asignar campañas».';
    // Fuera de su horario del dia (programa.horaDesde / horaHasta) la regla espera: no se revisa ni se ejecuta hasta que abra su ventana.
    const espera = rgEnHorario(r) ? '' : 'Espera su horario (' + rgHorasTxt(p) + ', hora de Colombia): empieza a las ' + rgHoraTxt(p.horaDesde) + ' ';      // la hora ya termina en punto («p. m.»)
    let u = rgLogPrueba && rgLogPrueba.ultimas && Number(rgLogPrueba.ultimas[r.id]);
    if (u > Date.now() + 300000) u = 0;                                      // una fecha futura (reloj mal puesto, registro de prueba) no cuenta: el script la ignora igual
    if (!u) return espera ? espera.trim() : 'Todavía no se ha revisado: entra en el próximo refresco';
    if (espera) return espera + 'Última revisión ' + rgHaceTxt(u) + rgResumenDecisiones(r, u);
    const prox = rgProximaRevision(r, u);
    return 'Última revisión ' + rgHaceTxt(u) + rgResumenDecisiones(r, u) + (prox > Date.now() ? ' · le toca de nuevo ' + rgDesdeTxt(prox) : ' · le toca en el próximo refresco');
  }
  // Se calla si cada grupo de condiciones de la tarea pide dias con gasto o un gasto minimo.
  // Caso aparte, el mas traicionero (2026-10-14: 15 campañas nuevas pausadas a los minutos de arrancar): la regla pide «Presupuesto diario > X» creyendo que es lo gastado. El presupuesto es el TOPE, asi que
  function rgAvisoPausa(r) {
    const gastoMin = (c) => (c.m === 'gasto' || c.m === 'gastoLocal') && /^(>|>=)$/.test(c.op) && Number(c.valor) > 0;
    // Una pausa por «el número de WhatsApp está desconectado» no necesita madurez: una campaña de hoy con el número caído tampoco puede vender.
    const pide = (g) => g.some((c) => c.m === 'dias' || gastoMin(c) || (c.m === 'numeroWpp' && c.valor === 'disconnected'));
    const pausan = (r.tareas || []).filter((t) => (t.acciones || []).some((a) => a.tipo === 'pausar'));
    const usaTope = pausan.some((t) => (t.grupos || []).some((g) => g.length && !pide(g) && g.some((c) => c.m === 'presupuesto' || (c.vs && c.vs.m === 'presupuesto'))));
    if (usaTope) return '⚠ Esta regla pausa de verdad y usa «Presupuesto diario»: es el tope que le diste a la campaña, NO lo que ha gastado, así que cualquier campaña nueva con ese tope y 0 ventas la cumple desde su primer minuto y se pausaría. Si querías «gastó X», elige «Gasto (moneda de la cuenta)» (o «Gasto (USD)»).';
    const sinMadurez = pausan.some((t) => (t.grupos || []).some((g) => g.length && !pide(g)));
    return sinMadurez ? '⚠ Esta regla pausa de verdad y no pide días con gasto ni un gasto mínimo: una campaña que lanzaste hoy también tiene 0 ventas y se pausaría. Si no quieres eso, agrega a cada grupo una condición de «Días con gasto» ≥ 2 (o de gasto).' : '';
  }
  function rgProgramaHtml(r) {
    const p = r.programa, otro = rgProgOtro || !RG_CADA_REV.some((o) => o[0] === p.cadaMin), rt = rgRitmo();
    const num = p.cadaMin > 0 ? Math.round((p.cadaMin / (RG_UNIDAD[rgProgUnidad] || 1)) * 100) / 100 : '';
    let h = '<div class="rg-bloque rg-prog"><h3>Programación</h3>' +
      '<p class="card-sub">Cuándo se revisa esta regla. En cada revisión, lo que decida se ejecuta en Facebook y queda en el Registro con los números exactos.</p>';
    {
      h += '<div class="rg-campos rg-prog-campos"><label>Cada cuánto se revisa sola<select data-rg-prog="cada">' + RG_CADA_REV.map((o) => rgOpt(o[0], o[1], !otro && o[0] === p.cadaMin)).join('') + rgOpt('otro', 'Otro intervalo…', otro) + '</select></label>' +
        (otro ? '<label>Cada <small>(15 min a 7 días)</small><input type="number" inputmode="decimal" min="1" step="any" id="rg-prog-num" data-rg-prog="num" value="' + escHtml(String(num)) + '"></label>' +
          '<label>Unidad<select id="rg-prog-unidad" data-rg-prog="unidad">' + [['min', 'minutos'], ['h', 'horas'], ['d', 'días']].map((o) => rgOpt(o[0], o[1], o[0] === rgProgUnidad)).join('') + '</select></label>' : '') + '</div>';
    }
    h += '<div class="rg-nivel"><div class="rg-nivel-lbl">Fechas en que corre <span class="rg-info" title="Fuera de estas fechas la regla no se revisa ni se ejecuta. Son fechas de Colombia, desde las 12:00 a. m. del primer día hasta el final del último.">ⓘ</span></div>' +
      '<div class="rg-seg" role="radiogroup" aria-label="Fechas en que corre">' + [['continua', 'Continua, desde hoy'], ['fechas', 'Entre dos fechas']].map((o) =>
        '<button type="button" role="radio" aria-checked="' + ((o[0] === 'fechas') === rgProgFechas) + '" class="' + ((o[0] === 'fechas') === rgProgFechas ? 'on' : '') + '" data-rg="prog-rango" data-v="' + o[0] + '">' + o[1] + '</button>').join('') + '</div></div>';
    if (rgProgFechas) h += '<div class="rg-campos rg-prog-fechas"><label>Desde<input type="date" data-rg-prog="desde" value="' + escHtml(p.desde) + '"></label><label>Hasta <small>(opcional)</small><input type="date" data-rg-prog="hasta" value="' + escHtml(p.hasta) + '"></label></div>' +
      '<p class="rg-mut">Fechas de Colombia, del primer día completo al último completo. Fuera de ellas la regla no se revisa ni se ejecuta.</p>';
    // Horario del dia: la regla solo trabaja entre dos horas (p. ej. de 5:00 p. m. a medianoche para frenar el gasto de la noche). «Hasta» es la hora en que deja de trabajar.
    h += '<div class="rg-nivel"><div class="rg-nivel-lbl">Horario del día <span class="rg-info" title="Fuera de este horario la regla no se revisa ni se ejecuta, aunque esté encendida y dentro de sus fechas. Es hora de Colombia.">ⓘ</span></div>' +
      '<div class="rg-seg" role="radiogroup" aria-label="Horario del día">' + [['todo', 'Todo el día'], ['horas', 'Solo en un horario']].map((o) =>
        '<button type="button" role="radio" aria-checked="' + ((o[0] === 'horas') === rgProgHoras) + '" class="' + ((o[0] === 'horas') === rgProgHoras ? 'on' : '') + '" data-rg="prog-horas" data-v="' + o[0] + '">' + o[1] + '</button>').join('') + '</div></div>';
    if (rgProgHoras) {
      const horas = (desde, hasta, sel) => { const o = []; for (let x = desde; x <= hasta; x++) o.push(rgOpt(x, rgHoraTxt(x), x === sel)); return o.join(''); };
      h += '<div class="rg-campos rg-prog-horas"><label>Desde<select data-rg-prog="horaDesde">' + horas(0, 23, p.horaDesde) + '</select></label>' +
        '<label>Hasta <small>(deja de trabajar)</small><select data-rg-prog="horaHasta">' + horas(1, 24, p.horaHasta) + '</select></label></div>' +
        '<p class="rg-mut">Hora de Colombia. Trabaja desde «Desde» hasta justo antes de «Hasta»; si «Hasta» es anterior a «Desde», cruza la medianoche. Cada refresco del dash que caiga dentro del horario revisa la regla.</p>';
    }
    h += '<p class="card-sub" id="rg-prog-res">' + escHtml(rgProgramaFrase(r)) + '</p><div class="rg-err" id="rg-prog-err"></div>' +
      '<p class="rg-mut">El sistema mira las reglas cuando se refresca el dash: ' + (rt ? 'en las últimas 24 h lo hizo ' + rt.n24 + (rt.n24 === 1 ? ' vez' : ' veces') + ' (la última ' + escHtml(rgHaceTxt(rt.ultima)) + ', normalmente ' + escHtml(rgCadaTxt(rt.medianaMin)) + ')' : 'cada vez que corre el refresco automático del servidor y también cuando alguien abre la página (máximo una vez por hora)') + '. Una regla no se revisa más seguido que eso.</p>';
    if (p.cadaMin > 0 && rt && rt.medianaMin > p.cadaMin * 1.5) {
      h += '<div class="rg-aviso">⚠ Hoy el sistema pasa más o menos ' + escHtml(rgCadaTxt(rt.medianaMin)) + ': esta regla se revisará en cada refresco, no ' + escHtml(rgCadaTxt(p.cadaMin)) + '. Más seguido solo se puede subiendo el ritmo del refresco automático del servidor (cada refresco tarda unos 5 minutos en traer los datos y armar el dash).</div>';
    }
    return h + '</div>';
  }
  // Un cambio en el programa de la regla que se edita: el intervalo (uno de la lista, u «Otro» con numero y unidad) o las fechas.
  function rgProgCambio(x, final) {
    const p = rgBorrador.programa, k = x.getAttribute('data-rg-prog');
    const frase = () => { const f = el('rg-prog-res'); if (f) f.textContent = rgProgramaFrase(rgBorrador); };
    if (k === 'cada') {
      if (x.value === 'otro') { rgProgOtro = true; if (!(p.cadaMin > 0)) p.cadaMin = 90; rgProgUnidad = rgUnidadDe(p.cadaMin); }
      else { rgProgOtro = false; p.cadaMin = Number(x.value) || 0; }
      rgPintar(); return;
    }
    if (k === 'num' || k === 'unidad') {
      if (k === 'unidad') rgProgUnidad = x.value;
      const campo = el('rg-prog-num'), n = parseFloat((campo || {}).value);
      // Mientras escribe no se aplica el minimo (15 min): «1» hora no puede quedar en 15 minutos a media tecla.
      if (n > 0) p.cadaMin = Math.min(RG_REV_MAX, Math.max(1, Math.round(n * (RG_UNIDAD[rgProgUnidad] || 1))));     // max(1): 0,001 h redondea a 0 = «en cada refresco», y no es eso lo que se pidio
      if (final) {
        // Al salir del campo se corrige el valor SIN repintar la pantalla: repintar en medio del toque se comia el primer toque sobre «Guardar regla» o cualquier otro control (en celular
        // se escribia 3, se tocaba «Guardar regla» y no pasaba nada). Se escribe el valor ya corregido en los propios campos: menos de 15 min = 15 min, mas de 7 dias = 7 dias, vacio o 0 = el ultimo valor valido.
        if (p.cadaMin > 0 && p.cadaMin < RG_REV_MIN) { p.cadaMin = RG_REV_MIN; rgProgUnidad = 'min'; }
        if (p.cadaMin === RG_REV_MAX) rgProgUnidad = 'd';
        const sel = el('rg-prog-unidad'); if (sel) sel.value = rgProgUnidad;
        if (campo) campo.value = String(Math.round((p.cadaMin / (RG_UNIDAD[rgProgUnidad] || 1)) * 100) / 100);
      }
      frase();
      return;
    }
    if (k === 'horaDesde' || k === 'horaHasta') {                                  // las horas se guardan enteras; dos horas iguales (o de 12:00 a. m. a medianoche) serian todo el dia, o sea ningun horario
      p[k] = x.value === '' ? null : Number(x.value);
      const err = el('rg-prog-err');
      if (err) err.textContent = rgProgramaBase(p).horaDesde == null ? 'Con estas dos horas la regla trabajaría todo el día (son la misma hora o abarcan el día completo): así no queda ningún horario.' : '';
      frase();
      return;
    }
    p[k] = /^\d{4}-\d{2}-\d{2}$/.test(x.value) ? x.value : '';                    // desde / hasta
    const err = el('rg-prog-err'); if (err) err.textContent = p.desde && p.hasta && p.desde > p.hasta ? 'La fecha final es anterior a la inicial.' : '';
    frase();
  }

  // ---- pintar ----
  function rgPintar() {
    const root = el('rg-root'); if (!root) return;
    const y = window.scrollY;
    const fp = rgFiltroPais(), visibles = reglas.filter((r) => rgReglaEnFiltro(r, fp)), act = visibles.filter((r) => r.on).length;
    let h = '';
    if (rgVista !== 'editor') {
      h += '<div class="rg-tabs" role="tablist">' +
        [['propuestas', 'Por ejecutar' + (rgActuales.length ? ' (' + rgActuales.length + ')' : '')], ['reglas', 'Reglas (' + act + '/' + visibles.length + ')'], ['campanas', 'Campañas'], ['registro', 'Registro']]
          .map((t) => '<button type="button" class="rg-tab' + (rgVista === t[0] ? ' on' : '') + '" data-rg="tab" data-v="' + t[0] + '">' + t[1] + '</button>').join('') + '</div>';
    }
    if (rgVista !== 'reglas') rgNota = '';
    h += rgVista === 'editor' ? rgEditorHtml() : rgVista === 'propuestas' ? rgPropuestasHtml() : rgVista === 'registro' ? rgRegistroHtml() : rgVista === 'campanas' ? rgCampanasHtml() : rgListaHtml();
    h += '<div class="rg-estado" id="rg-estado">' + escHtml(rgEstado) + '</div>';
    root.innerHTML = h;
    rgPintarModal();
    const cnt = el('rg-count'); if (cnt) cnt.textContent = rgActuales.length ? String(rgActuales.length) : '';
    document.documentElement.classList.toggle('rg-modal-abierto', !!rgModal);
    if (window.scrollY !== y) window.scrollTo(0, y);
  }
  // El modal cuelga de .viz-root, no de la tarjeta (#rg-card). Dentro de la tarjeta (.card) el «levantarse» al pasar el mouse es un transform, y un transform
  // vuelve a la tarjeta el bloque contenedor del position:fixed: la capa quedaba del tamaño de la tarjeta y el cuadro centrado en una pagina de 2000 px,
  // fuera de pantalla. En .viz-root y no en <body> porque ahi viven las variables de color (--surface-1, --border...): en <body> el cuadro salia transparente.
  function rgPintarModal() {
    let m = el('rg-modal-raiz');
    if (!m) { if (!rgModal) return; m = document.createElement('div'); m.id = 'rg-modal-raiz'; (document.querySelector('.viz-root') || document.body).appendChild(m); rgEscuchas(m); }
    const yaAbierto = !!m.firstElementChild;
    m.innerHTML = rgModal ? rgModalHtml() : '';
    const caja = rgModal && !yaAbierto ? m.querySelector('.rg-modal-caja') : null;
    if (caja) caja.focus({ preventScroll: true });
  }
  function rgDecir(msg) {
    rgEstado = msg; const e = el('rg-estado'); if (e) e.textContent = msg;
    setTimeout(() => { if (rgEstado === msg) { rgEstado = ''; const e2 = el('rg-estado'); if (e2) e2.textContent = ''; } }, 4500);
  }

  // ---- lista de reglas ----
  function rgListaHtml() {
    const q = rgQ.reglas.toLowerCase();
    const fp = rgFiltroPais(), delPais = reglas.map((r, i) => ({ r, i })).filter((x) => rgReglaEnFiltro(x.r, fp));
    const filas = delPais.filter((x) => (!rgQ.soloOn || x.r.on) && (!q || x.r.nombre.toLowerCase().indexOf(q) !== -1));
    const porRegla = {}; rgActuales.forEach((p) => { porRegla[p.reglaId] = (porRegla[p.reglaId] || 0) + 1; });
    let h = '<div class="rg-barra rg-barra-lista"><input type="search" id="rg-buscar" placeholder="Buscar regla…" value="' + escHtml(rgQ.reglas) + '" data-rg-q="reglas">' +
      '<label class="rg-chk"><input type="checkbox" data-rg-q="soloOn"' + (rgQ.soloOn ? ' checked' : '') + '> Solo activas</label>' +
      '<span class="rg-cuenta">' + (filas.length === delPais.length ? delPais.length + (delPais.length === 1 ? ' regla' : ' reglas') : filas.length + ' de ' + delPais.length + ' reglas') + '</span>' +
      '<button type="button" class="btn-primary" data-rg="nueva">+ Nueva regla</button></div>';
    h += rgNotaFiltro(fp, reglas.length - delPais.length, 'regla de otro país', 'reglas de otros países');
    if (rgNota) h += '<div class="rg-aviso">ℹ ' + escHtml(rgNota) + '</div>';
    h += rgChoquesHtml();
    h += filas.length ? filas.map(({ r, i }) => {
      const fr = rgFraseRegla(r), cub = rgCubre(r), cubPais = fp ? cub.filter((x) => fp.has(x.c)) : cub, abierta = rgCubreAbierta === r.id, aviso = rgAvisoMoneda(r), prog = rgProgramaBase(r.programa), fechas = rgFechasTxt(prog), fuera = !rgEnFechas(r), linea = rgRevisionLinea(r, cub.length), avisoPausa = rgAvisoPausa(r);
      const horas = rgHorasTxt(prog);
      return '<div class="rg-regla' + (r.on ? '' : ' apagada') + '"><div class="rg-reg-top">' +
        '<label class="rg-sw" title="Activar o apagar"><input type="checkbox" data-rg-on="' + i + '"' + (r.on ? ' checked' : '') + '><span class="rg-sw-ui"></span></label>' +
        '<div class="rg-reg-info"><div class="rg-tit">' + escHtml(r.nombre) + '</div><div class="rg-frase">' + escHtml(fr || 'Sin condiciones todavía') + '</div>' +
        '<div class="rg-meta"><button type="button" class="rg-cubre-btn' + (!cub.length && !r.aplica.futuras ? ' warn' : '') + '" data-rg="cubre" data-i="' + i + '" aria-expanded="' + abierta + '" title="Ver qué campañas cubre esta regla">' + escHtml(rgResumenAplica(r, cub) + (cubPais.length !== cub.length ? ' · ' + cubPais.length + ' en ' + rgFiltroNombre(fp) : '')) + (abierta ? ' ▴' : ' ▾') + '</button>' +
        (r.on ? '<span class="pill good">Se revisa ' + escHtml(rgCadaTxt(prog.cadaMin)) + '</span>' : '') +
        (fechas ? '<span class="pill ' + (fuera ? 'warn' : 'neutral') + '">' + (fuera ? 'Fuera de fechas · ' : '') + escHtml(fechas) + '</span>' : '') +
        (horas ? '<span class="pill neutral">Solo ' + escHtml(horas) + '</span>' : '') +
        (porRegla[r.id] ? '<span class="pill reactivate">Por ejecutar: ' + porRegla[r.id] + '</span>' : '') + '</div>' +
        (linea ? '<div class="rg-rev-linea">' + escHtml(linea) + '</div>' : '') +
        (aviso ? '<div class="rg-aviso">' + escHtml(aviso) + '</div>' : '') + (avisoPausa && r.on ? '<div class="rg-aviso">' + escHtml(avisoPausa) + '</div>' : '') + (abierta ? rgCubreHtml(r, cubPais, cubPais.length !== cub.length ? rgFiltroNombre(fp) : '') : '') + '</div></div>' +
        '<div class="rg-acciones"><button type="button" class="btn-secondary" data-rg="revisar" data-i="' + i + '" aria-expanded="' + (rgRevisarId === r.id) + '" title="Mira la regla con los números de ahora y muestra la cifra exacta de cada campaña. No cambia nada en Facebook.">' + (rgRevisarId === r.id ? 'Ocultar cifras' : 'Revisar ahora') + '</button>' +
        '<button type="button" class="btn-secondary" data-rg="editar" data-i="' + i + '">Editar</button>' +
        '<button type="button" class="btn-secondary" data-rg="asignar-lista" data-i="' + i + '">Asignar campañas</button>' +
        '<button type="button" class="btn-secondary" data-rg="dup" data-i="' + i + '">Duplicar</button>' +
        '<button type="button" class="btn-secondary" data-rg="del" data-i="' + i + '">Eliminar</button></div>' +
        (rgRevisarId === r.id && rgInformeVivo ? '<div class="rg-inf-panel">' + rgInformeVivoCab(rgInformeVivo) + rgInformeHtml(rgInformeVivo, { fp }) + '</div>' : '') + '</div>';
    }).join('') : '<p class="rg-vacio">' + (!reglas.length ? 'Todavía no hay reglas. Crea una desde cero o parte de una plantilla con «+ Nueva regla».' : !delPais.length ? 'Ninguna regla trabaja en ' + escHtml(rgFiltroNombre(fp)) + '. Cambia «País» arriba o crea una con «+ Nueva regla».' : rgQ.soloOn && !q ? 'No hay reglas activas: todas están apagadas. Quita «Solo activas» para verlas.' : 'Ninguna regla coincide con la búsqueda' + (rgQ.soloOn ? ' entre las activas' : '') + '.') + '</p>';
    h += '<p class="card-sub">Una regla encendida trabaja sola en las campañas que le asignes: en cada refresco del dash revisa los números y <b>ejecuta en Facebook</b> lo que decide (pausar, reactivar, cambiar el presupuesto o duplicar; las copias siempre nacen pausadas). Lo que hizo queda en «Registro» con los números exactos. Para frenar una regla, apágala.</p>';
    return h;
  }

  // ---- campañas: asignar reglas a las que ya corren ----
  // Misma asignacion que el modal de la regla (`aplica.campanas`), vista desde la campaña: cada fila dice cuantas reglas la
  // cubren y «Automatizar» deja marcar o quitar cada una. Las que entran por «Todas» o por el filtro de futuras se ven pero no se quitan aqui.
  function rgViaSets() { return reglas.map((r) => ({ man: new Set(r.aplica.campanas), base: new Set(r.aplica.base) })); }
  function rgVia(r, s, x) {
    const a = r.aplica;
    if (!rgPaisOk(r, x.c)) return '';
    if (a.todas) return 'todas';
    if (s.man.has(x.id)) return 'manual';
    return a.futuras && !s.base.has(x.id) && rgFiltroCoincide(a.filtro, x.nombre) ? 'futura' : '';
  }
  function rgGuardarLuego() { clearTimeout(rgTimerGuardar); rgTimerGuardar = setTimeout(rgGuardar, 700); }
  // «Vigente» = encendida Y dentro de sus fechas: la unica que de verdad vigila (fuera de fechas no propone nada ni se revisa sola). La cobertura de Campañas y el mensaje de Propuestas cuentan estas, no solo las encendidas.
  const rgVigente = (r) => !!r.on && rgEnFechas(r);
  // ---- reglas que se contradicen (ver rgChoques en reglas-modulo.js): solo AVISA ----
  // Se calcula con las reglas guardadas y los numeros del ultimo refresco, todos los paises y todas las ofertas (como la revision automatica), y sin contar el horario salvo que los horarios de las dos
  // reglas no se crucen nunca (ver rgChoques): una regla de la noche que aun no abre su ventana igual se compara, para avisar al armarla y no cuando ya esta corriendo. La cuenta tarda unos segundos
  // (evalua otra vez las reglas que forman un par, sobre todas las campañas): por eso NO se hace dentro del pintado, que se quedaria congelado un momento en el celular, sino un instante despues, y solo se
  // repinta si el resultado cambia y quien usa la pantalla no esta escribiendo. Se guarda por huella (reglas + datos + dia): no se repite la cuenta en cada repintado.
  let rgChoquesTimer = 0;
  function rgChoquesFirma() {
    const act = reglas.filter((r) => r.on && (r.tareas || []).length && rgEnFechas(r));
    return { act, firma: JSON.stringify([act.map((r) => [r.id, r.tareas, r.aplica, r.programa]), String(SNAPSHOT.generatedAt || ''), rgHoyCO()]) };
  }
  function rgChoquesAhora() {
    const { act, firma } = rgChoquesFirma();
    if (firma === rgChoquesMemo.firma) return rgChoquesMemo.v;
    let v = []; const oferta = state.offer;
    if (oferta !== 'ALL') { _arbolV.clear(); rgLimpiar(); state.offer = 'ALL'; }                    // como rgRevisarRegla: las cifras son las de todas las ofertas, no las del filtro de la pagina
    try { v = rgChoques(act, rgPaisesTodos(), { ignorarHorario: true }); } catch (e) { v = []; }       // un fallo del aviso nunca tumba la pantalla de reglas
    finally { if (oferta !== 'ALL') { state.offer = oferta; _arbolV.clear(); rgLimpiar(); } }
    rgChoquesMemo = { firma, v };
    return v;
  }
  // Lo que corre un instante despues del pintado: calcula y, si el aviso cambio y la persona no esta escribiendo o eligiendo algo, repinta.
  function rgChoquesDespues() {
    rgChoquesTimer = 0;
    const antes = JSON.stringify(rgChoquesMemo.v);
    rgChoquesAhora();
    if (JSON.stringify(rgChoquesMemo.v) === antes) return;
    const raiz = el('rg-root'), activo = document.activeElement;
    const escribiendo = !!(raiz && activo && raiz.contains(activo) && /^(INPUT|SELECT|TEXTAREA)$/.test(activo.tagName));
    if ((rgVista === 'reglas' || rgVista === 'propuestas') && !rgModal && !escribiendo) rgPintar();
  }
  // Las frases de una lista de choques para pintar: todo texto que viene de nombres de reglas o campañas pasa por escHtml. Un choque mal formado (el informe guardado se lee de un JSON) se salta.
  function rgChoquesLista(cs, conEjemplos, fp) {
    return '<ul>' + cs.filter((c) => c && c.a && c.b).map((c) => {
      const horarios = [c.a, c.b].filter((x, i, a) => x && x.horario && a.findIndex((y) => y.reglaId === x.reglaId) === i).map((x) => '«' + x.regla + '» solo trabaja ' + x.horario).join('; ');
      const ejemplos = (Array.isArray(c.ejemplos) ? c.ejemplos : []).filter((e) => e && typeof e === 'object' && (!fp || fp.has(e.pais)));
      return '<li>' + escHtml(rgChoqueFrase(c)) + (horarios ? '<span class="rg-mut">' + escHtml(horarios) + (/\.$/.test(horarios) ? '' : '.') + '</span>' : '') +
        (conEjemplos && ejemplos.length ? '<span class="rg-mut">Por ejemplo: ' + ejemplos.map((e) => escHtml(conBandera(e.pais) + ' ' + e.nombre + (e.id ? ' (id …' + String(e.id).slice(-6) + ')' : ''))).join('; ') + '</span>' : '') + '</li>';
    }).join('') + '</ul>';
  }
  function rgChoquesHtml() {
    if (!reglas.some((r) => r.on)) return '';
    // Si la huella cambio (se edito una regla, llegaron datos nuevos) se programa la cuenta para despues del pintado; mientras tanto se muestra lo ultimo que se calculo.
    if (rgChoquesFirma().firma !== rgChoquesMemo.firma && !rgChoquesTimer) rgChoquesTimer = setTimeout(rgChoquesDespues, 60);
    const fp = rgFiltroPais(), cs = rgChoquesMemo.v.filter((c) => rgChoqueEnFiltro(c, fp)); if (!cs.length) return '';
    return '<div class="rg-aviso rg-choque" role="alert"><b>⚠ ' + (cs.length === 1 ? 'Dos reglas se contradicen' : cs.length + ' choques entre reglas') + '</b> <span class="rg-mut" style="display:inline">(solo es un aviso: no se cambió ninguna regla ni ninguna campaña)</span>' +
      rgChoquesLista(cs.slice(0, 4), true, fp) + (cs.length > 4 ? '<span class="rg-mut">…y ' + (cs.length - 4) + ' más.</span>' : '') +
      '<span class="rg-mut">Cambia las condiciones de una de las dos reglas, o apaga una.</span></div>';
  }
  // «N reglas esperan su horario y no se evaluan ahora: «X» (de 5:00 p. m. a medianoche).» ('' si ninguna).
  function rgEsperanTxt() {
    const fp = rgFiltroPais(), e = reglas.filter((r) => r.on && rgEnFechas(r) && !rgEnHorario(r) && rgReglaEnFiltro(r, fp));
    if (!e.length) return '';
    return e.length + (e.length === 1 ? ' regla activa espera su horario y no se evalúa ahora: ' : ' reglas activas esperan su horario y no se evalúan ahora: ') +
      e.slice(0, 3).map((r) => '«' + escHtml(r.nombre) + '» (' + escHtml(rgHorasTxt(rgProgramaBase(r.programa))) + ')').join('; ') + (e.length > 3 ? ' y ' + (e.length - 3) + ' más' : '') + '.';
  }
  function rgCampanasHtml() {
    const fp = rgFiltroPais(), todas = rgCampanas(selectedCountries());
    // Las marcas de lo que ya no se ve se quitan: «Pausar marcadas» no debe tocar campañas de un pais que no esta a la vista. Por lo mismo, la lista de reglas para asignar en lote solo trae las que pueden trabajar en estos paises.
    const idsVis = new Set(todas.map((x) => x.id)); Array.from(rgC.sel).forEach((id) => { if (!idsVis.has(id)) rgC.sel.delete(id); });
    const reglasAqui = reglas.map((r, i) => ({ r, i })).filter((x) => selectedCountries().some((c) => rgPaisOk(x.r, c)));
    if (rgC.regla !== '' && !reglasAqui.some((x) => String(x.i) === String(rgC.regla))) rgC.regla = '';
    const q = rgC.q.toLowerCase(), sets = rgViaSets();
    let filas = todas.filter((x) => (!rgC.solo || x.corre) && (!q || x.nombre.toLowerCase().indexOf(q) !== -1));
    const totalFiltradas = filas.length;
    // Cobertura: de las que corren de verdad (activas y en cuenta sana), cuantas tiene al menos UNA regla VIGENTE (encendida y dentro de sus fechas). Las demas gastan sin vigilancia.
    const cubierta = (x) => reglas.some((r, i) => rgVigente(r) && rgVia(r, sets[i], x));
    const corriendo = todas.filter((x) => x.corre), cubiertas = corriendo.filter(cubierta).length;
    if (rgC.sinRegla) filas = filas.filter((x) => !cubierta(x));
    if (rgC.vista === 'sel') filas = filas.filter((x) => rgC.sel.has(x.id));
    // Primero las que corren y, entre ellas, por la metrica elegida (por defecto el gasto de 7 dias: son las que mas importa cubrir con una regla).
    const m7 = (x) => { const n = rgIndice(x.c, 'last7x0').get('campaign:' + x.id); return n || RG_CERO; };
    const clave = { gasto: (x) => m7(x).spendUsd, roas: (x) => (m7(x).spendUsd > 0 ? m7(x).salesUsd / m7(x).spendUsd : -1), ventas: (x) => m7(x).orders, conv: (x) => m7(x).conv || 0 }[rgC.orden];
    filas = filas.map((x, i) => ({ x, i, g: clave ? clave(x) : 0 }))
      .sort((p, o) => (rgNivelCamp(o.x) - rgNivelCamp(p.x)) || (rgC.orden === 'nombre' ? (p.x.nombre < o.x.nombre ? -1 : p.x.nombre > o.x.nombre ? 1 : 0) : (o.g - p.g)) || p.i - o.i).map((p) => p.x);
    rgCampanasHtml.vis = filas;
    const nSel = rgC.sel.size;
    let h = '<div class="rg-barra"><input type="search" id="rg-cbuscar" placeholder="Buscar campaña…" value="' + escHtml(rgC.q) + '" data-rg-cq="q">' +
      '<label class="rg-chk"><input type="checkbox" data-rg-cq="solo"' + (rgC.solo ? ' checked' : '') + '> Solo corriendo</label>' +
      '<select data-rg-cq="orden" class="rg-pais-sel" title="Orden">' + [['gasto', 'Más gasto (7 días)'], ['roas', 'Mejor ROAS (7 días)'], ['ventas', 'Más ventas Dash (7 días)'], ['conv', 'Más conversaciones (7 días)'], ['nombre', 'Nombre']].map((o) => rgOpt(o[0], o[1], o[0] === rgC.orden)).join('') + '</select></div>';
    if (reglas.length) {
      const sin = corriendo.length - cubiertas, fueraN = reglas.filter((r) => r.on && !rgEnFechas(r) && rgReglaEnFiltro(r, fp)).length;
      h += '<div class="rg-cobertura"><span>Corriendo <b>' + corriendo.length + '</b> · con una regla encendida <b>' + cubiertas + '</b> · </span>' +
        '<span class="pill ' + (sin > 0 ? 'warn' : 'good') + '">sin regla ' + sin + '</span>' +
        (fueraN ? '<span class="rg-mut">' + fueraN + (fueraN === 1 ? ' regla encendida está fuera de sus fechas y no cuenta' : ' reglas encendidas están fuera de sus fechas y no cuentan') + '</span>' : '') +
        '<label class="rg-chk"><input type="checkbox" data-rg-cq="sinRegla"' + (rgC.sinRegla ? ' checked' : '') + '> Solo las sin regla</label></div>';
    }
    h += '<div class="rg-seg rg-seg-vista"><button type="button" class="' + (rgC.vista === 'todas' ? 'on' : '') + '" data-rg="camp-vista" data-v="todas">Todas <span class="pill neutral">' + totalFiltradas + '</span></button>' +
      '<button type="button" class="' + (rgC.vista === 'sel' ? 'on' : '') + '" data-rg="camp-vista" data-v="sel">Seleccionadas <span class="pill neutral">' + nSel + '</span></button></div>';
    if (!reglas.length) h += '<p class="rg-vacio">Todavía no hay reglas para asignar. Crea una en la pestaña «Reglas».</p>';
    h += '<div class="rg-bulk' + (nSel ? ' sel' : '') + '"><span class="rg-bulk-n">' + (nSel ? nSel + (nSel === 1 ? ' campaña marcada' : ' campañas marcadas') : 'Marca campañas para asignarles una regla de una vez') + '</span>' +
      '<button type="button" class="btn-secondary" data-rg="camp-marcar">Marcar las ' + filas.length + ' visibles</button>' +
      (nSel ? '<select data-rg-cq="regla"><option value="">Elige una regla…</option>' + reglasAqui.map(({ r, i }) => rgOpt(i, r.nombre + (r.on ? '' : ' (apagada)'), String(i) === String(rgC.regla))).join('') + '</select>' +
        '<button type="button" class="btn-primary" data-rg="camp-bulk" data-m="asignar">Asignar</button>' +
        '<button type="button" class="btn-secondary" data-rg="camp-bulk" data-m="quitar">Quitar</button>' +
        '<button type="button" class="btn-secondary" data-rg="camp-estado" data-m="pause" title="Pausar en Facebook las campañas marcadas (pide contraseña)">Pausar marcadas</button>' +
        '<button type="button" class="btn-secondary" data-rg="camp-estado" data-m="resume" title="Reactivar en Facebook las campañas marcadas (pide contraseña)">Reactivar marcadas</button>' +
        '<button type="button" class="btn-secondary" data-rg="camp-limpiar">Limpiar</button>' : '') + '</div>';
    const veces = (x) => reglas.map((r, i) => ({ i, r, via: rgVia(r, sets[i], x) })).filter((v) => v.via);
    h += filas.length ? filas.slice(0, rgC.limite).map((x) => {
      const mias = veces(x), encendidas = mias.filter((v) => rgVigente(v.r)).length;
      const nodo = rgIndice(x.c, 'last7x0').get('campaign:' + x.id);
      const roas = nodo && nodo.spendUsd > 0 ? nodo.salesUsd / nodo.spendUsd : null;
      const abierta = rgC.abierta === x.id;
      return '<div class="rg-camp' + (abierta ? ' abierta' : '') + '"><div class="rg-camp-top">' +
        '<label class="rg-camp-chk"><input type="checkbox" data-rg-csel="' + escHtml(x.id) + '"' + (rgC.sel.has(x.id) ? ' checked' : '') + '></label>' +
        '<div class="rg-camp-info"><div class="rg-camp-nom">' + escHtml(x.nombre) + ' <span class="rg-idc" title="ID de la campaña: ' + escHtml(x.id) + '">…' + escHtml(x.id.slice(-4)) + '</span></div>' +
        '<div class="rg-camp-sub">' + escHtml(conBandera(x.c)) + ' · ' + x.sets + ' conj. · ' + x.ads + ' anuncios · <span class="pill ' + rgEstadoCamp(x).pill + '">' + rgEstadoCamp(x).txt + '</span></div>' +
        '<div class="rg-camp-met">7 días: ' + (nodo ? usdTxt(nodo.spendUsd) + ' gasto · ' + (roas == null ? 'sin ROAS' : 'ROAS ' + roas.toFixed(2) + 'x') + ' · ' + fmtInt(nodo.orders) + ' ventas Dash' : 'sin movimiento') + '</div></div>' +
        '<button type="button" class="rg-regbadge' + (encendidas ? ' on' : mias.length ? ' off' : '') + '" data-rg="camp-abrir" data-id="' + escHtml(x.id) + '" title="Automatizar: elegir las reglas de esta campaña">' +
        (mias.length ? mias.length + (mias.length === 1 ? ' regla' : ' reglas') : 'Automatizar') + ' ▾</button></div>' +
        (abierta ? '<div class="rg-camp-panel">' + (reglas.length ? (reglas.map((r, i) => {
          const via = rgVia(r, sets[i], x), fuera = !rgPaisOk(r, x.c);
          if (fuera && fp) return '';                    // con un pais elegido, las reglas que no pueden trabajar en el no se listan
          return '<label class="rg-chk"><input type="checkbox" data-rg-crule="' + i + '"' + (via ? ' checked' : '') + (via === 'todas' || via === 'futura' || fuera ? ' disabled' : '') + '> ' + escHtml(r.nombre) +
            ' <span class="pill ' + (r.on ? 'good' : 'neutral') + '">' + (r.on ? 'activa' : 'apagada') + '</span>' +
            (fuera ? ' <span class="rg-mut">solo aplica a ' + escHtml(rgPaisesTxt(r)) + '</span>' : via === 'todas' ? ' <span class="rg-mut">entra por «Todas»</span>' : via === 'futura' ? ' <span class="rg-mut">entra por el filtro de futuras</span>' : '') + '</label>';
        }).join('') || '<p class="rg-vacio">Ninguna regla puede trabajar en este país.</p>') : '<p class="rg-vacio">Todavía no hay reglas.</p>') + '</div>' : '') + '</div>';
    }).join('') : '<p class="rg-vacio">' + (rgC.vista === 'sel' ? 'No hay campañas marcadas con este filtro.' : 'No hay campañas con este filtro.') + '</p>';
    if (nSel) h += '<button type="button" class="rg-chip" data-rg="camp-acciones"><b>' + nSel + '</b> ' + (nSel === 1 ? 'marcada' : 'marcadas') + ' · Acciones ▲</button>';
    if (filas.length > rgC.limite) h += '<div class="rg-acciones"><button type="button" class="btn-secondary" data-rg="camp-mas">Ver 100 más (' + (filas.length - rgC.limite) + ' ocultas)</button></div>';
    h += '<p class="card-sub">Campañas de ' + (fp ? escHtml(rgFiltroNombre(fp)) : 'todos los países') + ' (con el filtro de oferta de arriba). Cada regla trabaja en todas las campañas que tenga asignadas, aunque aquí no se vean. Un cambio aquí se guarda solo.</p>';
    return h;
  }

  // Pausar o reactivar de una vez las campañas marcadas: la misma llamada /api/toggle-ad y la misma contraseña que los interruptores
  // de la tabla. Solo en cuentas donde el token puede escribir; el resto se cuenta y se dice. Cada una queda en el Registro.
  async function rgEstadoLote(accion, boton) {
    const marcadas = rgCampanas(rgPaisesTodos()).filter((x) => rgC.sel.has(x.id));
    const aplicables = marcadas.filter((x) => !(x.acct && puedeEscribir(x.acct) === false));
    const verbo = accion === 'pause' ? 'Pausar' : 'Reactivar';
    if (!marcadas.length) { rgDecir('Marca primero las campañas.'); return; }
    if (!aplicables.length) { rgDecir('Ninguna de las marcadas está en una cuenta donde se pueda escribir.'); return; }
    const omitidas = marcadas.length - aplicables.length;
    if (!window.confirm(verbo + ' ' + aplicables.length + ' campaña(s) en Facebook' + (omitidas ? ' (' + omitidas + ' se omiten: cuenta de solo lectura)' : '') + '.\n\nSe aplican una por una con la misma contraseña. ¿Seguir?')) return;
    try {
      const pwd = await ensureAuth();
      boton.disabled = true;
      let bien = 0, mal = 0, ultimo = '';
      const avisos = [];
      for (const x of aplicables) {
        const p = { tipo: accion === 'pause' ? 'pausar' : 'reactivar', nivel: 'campaign', regla: 'Manual (pestaña Campañas)', nombre: x.nombre, country: x.c, texto: verbo + ' a mano desde la pestaña Campañas.' };
        try {
          const d = await rgPost({ action: accion, id: x.id, type: 'campaign', accountId: x.acct, password: pwd, meta: { regla: p.regla, nombre: x.nombre, pais: x.c, razon: p.texto, tipo: p.tipo } });
          if (d.ok) { if (d.efectivo) aplicarCambioReal('campaign', x.id, d); else aplicarEstadoLocal(x.c, 'campaign', x.id, accion === 'resume'); rgAnotar(p, true, d.aviso || ''); bien++; if (d.aviso) avisos.push(x.nombre + ': ' + d.aviso); } else { rgAnotar(p, false, d.error || 'no se pudo aplicar'); mal++; ultimo = d.error || ''; }
        } catch (e) { rgAnotar(p, false, 'error de red'); mal++; ultimo = 'error de red'; }
      }
      toast(bien + ' campaña(s) ' + (accion === 'pause' ? 'pausadas' : 'reactivadas') + (mal ? ', ' + mal + ' con error (' + ultimo + ')' : ''), mal ? 'error' : 'success');
      if (avisos.length) toast('Facebook las dejó distinto a lo pedido: ' + avisos.slice(0, 3).join(' · ') + (avisos.length > 3 ? ' · y ' + (avisos.length - 3) + ' más' : ''), 'warn', 14000);
      rgC.sel.clear(); render();
    } catch (err) { boton.disabled = false; }
  }

  // ---- propuestas ----
  function rgPropuestasHtml() {
    const fp = rgFiltroPais(), delPais = reglas.filter((r) => r.on && rgReglaEnFiltro(r, fp));
    if (!delPais.length) return '<p class="rg-vacio">' + (fp && reglas.some((r) => r.on) ? 'No hay reglas encendidas que trabajen en ' + escHtml(rgFiltroNombre(fp)) + '.' : 'No hay reglas encendidas.') + ' Enciende una en la pestaña «Reglas»: aquí verás lo que va a ejecutar en su próxima revisión.</p>';
    const fuera = delPais.filter((r) => !rgEnFechas(r)), nActivas = delPais.length;
    const fueraTxt = fuera.slice(0, 3).map((r) => '«' + escHtml(r.nombre) + '» (' + escHtml(rgFechasTxt(rgProgramaBase(r.programa))) + ')').join('; ') + (fuera.length > 3 ? ' y ' + (fuera.length - 3) + ' más' : '');
    const conteo = {}; rgActuales.forEach((p) => { conteo[p.tipo] = (conteo[p.tipo] || 0) + 1; });
    // Sin el registro compartido no se sabe que se aplico desde otro dispositivo: se dice antes de aprobar, porque aprobar dos veces un +20 % de presupuesto es +44 %.
    let h = (rgRegListo && rgRegServidor === null ? '<div class="rg-aviso">⚠ No pude leer el registro compartido: puede que alguna de estas acciones ya se haya aplicado desde otro dispositivo. Mira la pestaña «Registro» antes de ejecutar nada.</div>' : '') +
      rgChoquesHtml() +
      '<p class="card-sub">Esto es lo que las reglas <b>van a ejecutar</b> en su próxima revisión (hay una con cada refresco del dash), con los números de ahora. No tienes que aprobar nada. Si no quieres que algo se ejecute, apaga la regla o quítale esa campaña; si quieres adelantarlo, «Ejecutar ahora».</p>' +
      '<div class="rg-resumen">' + Object.keys(RG_ACCIONES).filter((a) => conteo[a]).map((a) => '<span class="pill ' + RG_ACCIONES[a].pill + '">' + RG_ACCIONES[a].txt + ': ' + conteo[a] + '</span>').join('') + '</div>';
    h += '<div class="rg-lote">' + Object.keys(RG_ACCIONES).filter((a) => conteo[a] > 1 && RG_ACCIONES[a].lote)
      .map((a) => '<button type="button" class="btn-secondary" data-rg="lote" data-t="' + a + '">' + RG_ACCIONES[a].lote + ' (' + conteo[a] + ')</button>').join('') + '</div>';
    h += rgActuales.length ? rgActuales.slice(0, 40).map((p, i) => {
      const e = RG_ACCIONES[p.tipo];
      const sinPermiso = p.accountId && puedeEscribir(p.accountId) === false;
      // Una propuesta es un borrador: el presupuesto nuevo (o el de la copia) se puede cambiar antes de aprobar.
      const esPres = !sinPermiso && (p.tipo === 'subir' || p.tipo === 'bajar' || p.tipo === 'fijar'), esCopia = !sinPermiso && p.tipo === 'duplicar' && p.copiaNuevo > 0;
      const edicion = esPres || esCopia ? '<div class="rg-edit"><label>' + (esPres ? 'Nuevo presupuesto diario' : 'Presupuesto de la copia') + ' <small>(solo vale con «Ejecutar ahora»; la revisión automática usa el cálculo de la regla)</small><input type="number" inputmode="decimal" min="1" step="any" data-rg-' + (esPres ? 'nuevo' : 'copia') + '="' + i + '" value="' + (esPres ? p.nuevo : p.copiaNuevo) + '"></label></div>' : '';
      return '<div class="rg-item ' + (p.tipo === 'pausar' ? 'pausa' : p.tipo === 'duplicar' ? 'duplica' : p.tipo === 'avisar' ? 'aviso' : 'sube') + '"><div class="rg-cab"><span class="pill ' + e.pill + '">' + e.txt + (p.nivel !== 'campaign' || p.tipo === 'avisar' ? ' · ' + RG_NIV[p.nivel].toLowerCase() : '') + '</span>' +
        '<span>' + escHtml(conBandera(p.country)) + ' · ' + escHtml(p.campana) + '</span></div>' +
        '<div class="rg-nombre">' + escHtml(p.nombre) + '</div><div class="rg-razon">' + escHtml(p.texto) + '</div>' +
        '<div class="rg-razon rg-mut">Regla: ' + escHtml(p.regla) + '</div>' + edicion +
        '<div class="rg-acc">' + (p.tipo === 'avisar' ? '' : sinPermiso
          ? '<span class="card-sub" style="margin:0">Sin permiso de escritura en esta cuenta: no se puede aplicar (ni sola ni a mano).</span>'
          : '<button type="button" class="btn-primary" data-rg="aprobar" data-i="' + i + '">' + e.boton + '</button>') +
        (p.tipo === 'avisar' ? '<button type="button" class="btn-secondary" data-rg="ignorar" data-i="' + i + '">Entendido</button>' : '') + '</div></div>';
    }).join('') : '<p class="rg-vacio">' + (fuera.length === nActivas
      ? (nActivas === 1 ? 'La única regla activa está fuera de sus fechas' : 'Las ' + nActivas + ' reglas activas están fuera de sus fechas') + ', así que no hace nada: ' + fueraTxt + '. Cambia sus fechas en la pestaña «Reglas».'
      : 'Ninguna regla tiene nada que ejecutar con estos números. Es buena señal, o los umbrales están muy exigentes.' + (fuera.length ? ' ' + fuera.length + (fuera.length === 1 ? ' regla activa está fuera de sus fechas y no se evalúa: ' : ' reglas activas están fuera de sus fechas y no se evalúan: ') + fueraTxt + '.' : '')) + '</p>';
    h += '<p class="card-sub">' + (rgActuales.length > 40 ? 'Se muestran las 40 primeras de ' + rgActuales.length + '. ' : '') + (rgActuales.length && fuera.length ? fuera.length + (fuera.length === 1 ? ' regla activa está fuera de sus fechas y no se evalúa: ' : ' reglas activas están fuera de sus fechas y no se evalúan: ') + fueraTxt + '. ' : '') +
      (rgEsperanTxt() ? rgEsperanTxt() + ' ' : '') +
      'Las reglas miran los países y la oferta que tengas filtrados. Gasto y facturación en USD; el presupuesto va en la moneda de su cuenta. Lo que ya se aplicó no vuelve a salir según la frecuencia de su acción (mínimo 24 h; pausar, la que elijas: con «Cada vez que se cumple» vuelve a pausar lo que alguien encienda).</p>';
    return h;
  }

  // ---- registro ----
  const rgFecha = (t) => new Date(t).toLocaleString('es-CO', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  function rgRegistroHtml() {
    const fp = rgFiltroPais(), todo = rgFuenteLog === 'dash' ? rgRegistroTodo() : ((rgLogPrueba && rgLogPrueba.entradas) || []).map((x) => Object.assign({}, x, { ok: x.estado !== 'fallo', pais: x.country, razon: x.texto, antes: x.actual, despues: x.nuevo }));
    const lista = fp ? todo.filter((x) => !(x.pais || x.country) || fp.has(x.pais || x.country)) : todo;      // una fila sin pais no se esconde: no se sabe que sea de otro
    let h = '<div class="rg-barra"><label class="rg-campos-1">Ver<select data-rg-fuente>' + rgOpt('dash', 'Lo que se aplicó (sola o a mano, todos los dispositivos)', rgFuenteLog === 'dash') + rgOpt('revisiones', 'Revisiones del refresco: qué miró y qué decidió', rgFuenteLog === 'revisiones') + '</select></label></div>';
    h += rgNotaFiltro(fp, todo.length - lista.length, rgFuenteLog === 'dash' ? 'cambio de otro país' : 'decisión de otro país', rgFuenteLog === 'dash' ? 'cambios de otros países' : 'decisiones de otros países');
    if (rgFuenteLog === 'dash') h += '<p class="card-sub">' + (rgRegServidor ? 'Cada cambio que se aplica en Facebook —una regla que trabajó sola, o una persona desde el teléfono o el computador— queda aquí con lo que Facebook confirmó y los números con los que se decidió. Se guardan 90 días.' : !rgRegListo ? 'Leyendo el registro…' : 'No pude leer el registro compartido: se muestra solo lo que se aplicó desde este navegador.') + '</p>';
    if (rgFuenteLog === 'revisiones') h += '<p class="card-sub">Cada refresco revisa las reglas encendidas a las que ya les toca según su programación, y ejecuta lo que deciden.' + (rgLogPrueba && rgLogPrueba.actualizado ? ' Última revisión: ' + escHtml(rgFecha(rgLogPrueba.actualizado)) + '.' : '') + '</p>' + '<div class="rg-lbl">Revisiones del refresco</div>' + rgRevisionesHtml() + rgCifrasUltimaHtml() + '<div class="rg-lbl" style="margin-top:12px">Decisiones de las revisiones</div>';
    const ESTADO = { hecha: ['Hecha', 'good'], omitida: ['No se hizo', 'neutral'], fallo: ['Falló', 'critical'], pendiente: ['Por ejecutar', 'warn'], aviso: ['Aviso', 'neutral'] };
    if (lista.length > 60) h += '<p class="card-sub">Se muestran las 60 más recientes de ' + lista.length + '.' + (selectedCountries().length > 1 ? ' Para ver menos, elige un país arriba.' : '') + '</p>';      // con todos los paises el tope se alcanza siempre: sin esta linea, la lista parece completa
    h += lista.length ? lista.slice(0, 60).map((x) => {
      const cfg = RG_ACCIONES[x.tipo] || { txt: x.tipo, pill: 'neutral' }, est = ESTADO[x.estado];
      const val = x.antes != null && x.despues != null ? '<div class="rg-razon rg-mut">Antes ' + fmtMoney(x.antes, 0) + ' → ahora ' + fmtMoney(x.despues, 0) + '</div>' : '';
      return '<div class="rg-fila"><div class="rg-cab"><span>' + escHtml(rgFecha(x.t)) + '</span><span class="pill ' + cfg.pill + '">' + escHtml(cfg.txt) + '</span>' +
        (rgFuenteLog === 'dash' ? '<span class="pill ' + (x.via === 'auto' ? 'reactivate' : 'neutral') + '">' + (x.via === 'auto' ? 'Automático' : 'A mano') + '</span>' : '') +
        (est ? '<span class="pill ' + est[1] + '">' + est[0] + '</span>' : '') +
        (x.ok === false && !est ? '<span class="pill critical">Falló</span>' : '') + '<span>' + escHtml(x.regla || '') + '</span></div>' +
        '<div class="rg-nombre">' + escHtml((x.pais || x.country ? conBandera(x.pais || x.country) + ' · ' : '') + (x.nombre || '')) + '</div>' +
        '<div class="rg-razon">' + escHtml(x.razon || x.msg || '') + (x.ok === false && x.msg ? ' — ' + escHtml(x.msg) : '') + (x.motivo ? ' — ' + escHtml(x.motivo) : '') + '</div>' +
        (x.cifras ? '<div class="rg-razon rg-mut">Números: ' + escHtml(x.cifras) + '</div>' : '') + val + '</div>';
    }).join('') : '<p class="rg-vacio">' + (fp && todo.length ? 'No hay nada anotado en ' + escHtml(rgFiltroNombre(fp)) + ': lo que hay es de otros países.' : rgFuenteLog === 'dash' ? 'Todavía no se ha aplicado nada: ni una regla sola ni a mano.' : 'Ninguna decisión anotada todavía (mira arriba si el refresco ya revisó las reglas).') + '</p>';
    return h;
  }
  // Las ultimas revisiones del refresco, con o sin propuestas nuevas: prueba de que las reglas SE MIRAN (y si no, por que).
  function rgRevisionesHtml() {
    const rev = (rgLogPrueba && Array.isArray(rgLogPrueba.revisiones)) ? rgLogPrueba.revisiones : [];
    if (!rev.length) return '<p class="rg-vacio">Todavía no hay ninguna revisión anotada. Aparece después del próximo refresco (2 fijos al día y uno cada vez que alguien abre el dash).</p>';
    // 'espera' = habia reglas marcadas pero a ninguna le tocaba todavia (su programa dice cada cuanto): se junta con la anterior igual y dice cuantas corridas seguidas.
    return '<div class="rg-revs">' + rev.slice(0, 8).map((x) => '<div class="rg-fila rg-rev"><div class="rg-cab"><span>' + escHtml(rgFecha(x.t)) + '</span><span class="pill ' + (x.estado === 'ok' ? 'good' : x.estado === 'error' ? 'critical' : x.estado === 'espera' ? 'neutral' : 'warn') + '">' + (x.estado === 'ok' ? 'Revisó' : x.estado === 'error' ? 'Falló' : x.estado === 'espera' ? 'En espera' : 'No revisó') + '</span>' +
      (x.veces > 1 ? '<span class="rg-mut">' + x.veces + ' corridas seguidas</span>' : '') + '</div>' +
      '<div class="rg-razon">' + escHtml(x.texto || '') + '</div></div>').join('') + '</div>';
  }
  // ---- cifras de una revision: lo que se miro y el numero exacto de cada condicion ----
  // La misma pintura sirve para la revision en vivo («Revisar ahora», «Ver qué haría ahora») y para la ultima que dejo el refresco en el registro (`informe`, ver scripts/reglas-auto.cjs).
  // Todo texto que viene de los datos (nombres, textos de las condiciones) pasa por escHtml: el informe guardado se lee de un JSON y los nombres de campaña los escribe cualquiera con acceso a la cuenta publicitaria.
  function rgFilaInformeHtml(f, t) {
    const camino = t.nivel === 'ad' ? [f.campana, f.conjunto, f.nombre] : t.nivel === 'adset' ? [f.campana, f.nombre] : [f.nombre];
    const grupos = Array.isArray(f.g) ? f.g : [], y = t.modo === 'or_and_or';
    const lis = (g) => (Array.isArray(g) ? g : []).map((c) => '<li class="' + (c && c[0] ? 'si' : 'no') + '"><span class="rg-inf-marca">' + (c && c[0] ? '✓' : '✗') + '</span><span>' + escHtml(c ? c[1] : '') + '</span></li>').join('');
    return '<div class="rg-inf-fila' + (f.cumple ? ' cumple' : '') + '"><div class="rg-cab"><span class="pill ' + (f.cumple ? 'good' : 'neutral') + '">' + (f.cumple ? 'Cumple' : 'No cumple') + '</span>' +
      '<span>' + escHtml(conBandera(f.pais)) + (f.estado === 'PAUSED' ? ' · pausado' : '') + '</span></div>' +
      '<div class="rg-nombre">' + escHtml(camino.filter(Boolean).join(' › ')) + '</div>' +
      grupos.map((g, gi) => (grupos.length > 1 ? '<div class="rg-inf-o">' + (gi ? (y ? 'y' : 'o') + ' · ' : '') + 'Grupo ' + (gi + 1) + (y ? ' (alguna)' : ' (todas)') + '</div>' : '') + '<ul class="rg-inf-cond">' + lis(g) + '</ul>').join('') +
      (f.cifras ? '<div class="rg-razon rg-mut">' + (t.cuando ? escHtml(t.cuando) + ': ' : '') + escHtml(f.cifras) + '</div>' : '') +
      (f.cumple ? '<div class="rg-inf-acc">' + (Array.isArray(f.acc) ? f.acc : []).map((a) => escHtml(a[0]) + (a[1] ? ': se ejecuta' : ': no se ejecuta (' + escHtml(a[2]) + ')')).join(' · ') + '</div>' : '') + '</div>';
  }
  function rgInformeHtml(inf, op) {
    op = op || {}; const fp = op.fp || null;
    if (!inf || !Array.isArray(inf.reglas) || !inf.reglas.length) return '<p class="rg-vacio">' + escHtml(op.vacio || 'Todavía no hay cifras de ninguna revisión.') + '</p>';
    let h = '<div class="rg-informe">';
    // Lo que NINGUNA regla encendida mira: campañas que corren y no tienen regla. Solo lo trae la revision automatica (necesita ver todas las reglas a la vez).
    const cb = inf.cobertura;
    if (cb && cb.sinRegla > 0 && !fp) h += '<p class="card-sub rg-aviso-cob">⚠ Hay <b>' + fmtInt(cb.sinRegla) + '</b> de las ' + fmtInt(cb.corriendo) + ' campañas que están corriendo que <b>ninguna regla encendida cubre</b>, así que nadie las está revisando' +
      (Array.isArray(cb.ejemplos) && cb.ejemplos.length ? ' (por ejemplo: ' + cb.ejemplos.slice(0, 3).map((e) => escHtml(conBandera(e.pais) + ' ' + e.nombre + (e.id ? ' (id …' + String(e.id).slice(-6) + ')' : ''))).join('; ') + ')' : '') + '. Se asignan en «Campañas».</p>';
    // Reglas que se contradecian en esa revision (solo las vivas en ese momento). La revision automatica las deja en `choques`; tambien se avisa por Telegram.
    const choquesInf = Array.isArray(inf.choques) ? inf.choques.filter((c) => c && c.a && c.b && rgChoqueEnFiltro(c, fp)) : [];
    if (choquesInf.length) h += '<div class="card-sub rg-aviso-cob"><b>⚠ ' + (choquesInf.length === 1 ? 'Dos reglas se contradecían' : choquesInf.length + ' choques entre reglas') + '</b> en esa revisión (solo es un aviso):' + rgChoquesLista(choquesInf.slice(0, 4), true, fp) + '</div>';
    // Con un pais elegido se ven solo las reglas que trabajan ahi y, de cada una, sus filas de ese pais. Una regla que ya no existe se juzga por sus filas.
    const enFiltro = (r) => { if (!fp) return true; const g = reglas.filter((x) => x.id === r.id)[0]; return g ? rgReglaEnFiltro(g, fp) : (r.tareas || []).some((t) => (t.filas || []).some((f) => fp.has(f.pais))); };
    const listaR = inf.reglas.filter(enFiltro);
    if (!listaR.length) h += '<p class="rg-vacio">En esta revisión ninguna regla trabajó en ' + escHtml(rgFiltroNombre(fp)) + '.</p>';
    listaR.forEach((r) => {
      const ts = Array.isArray(r.tareas) ? r.tareas : [], g = fp ? reglas.filter((x) => x.id === r.id)[0] : null, mezcla = !!fp && !inf.soloPais && !(g && g.aplica.paises.length && g.aplica.paises.every((c) => fp.has(c)));      // mezcla = los totales de esta regla incluyen otros paises
      h += '<div class="rg-inf-regla"><div class="rg-inf-tit">' + escHtml(r.nombre) + '</div>';
      // Lo asignado por id que los datos no ven: la regla no puede mirarlo.
      if (r.nNoVistas > 0) h += '<p class="card-sub rg-aviso-cob">⚠ ' + fmtInt(r.nNoVistas) + (r.nNoVistas === 1 ? ' campaña asignada a esta regla no aparece' : ' campañas asignadas a esta regla no aparecen') + ' en los datos del dash (borradas, archivadas o que nunca han gastado), así que no se pueden mirar: ' +
        (Array.isArray(r.noVistas) ? r.noVistas.slice(0, 5).map((id) => escHtml(String(id))).join(', ') + (r.nNoVistas > 5 ? '…' : '') : '') + '.</p>';
      ts.forEach((t, ti) => {
        const plural = RG_NIV_PL[t.nivel] || 'elementos', todasF = Array.isArray(t.filas) ? t.filas : [], filas = fp ? todasF.filter((f) => fp.has(f.pais)) : todasF, ver = 8;
        h += '<div class="rg-inf-tarea"><div class="rg-inf-res">' + (ts.length > 1 ? 'Tarea ' + (ti + 1) + ' · ' : '') + 'Miró <b>' + fmtInt(t.miradas || 0) + '</b> ' + plural + (t.miradas && t.corren != null ? ' (' + fmtInt(t.corren) + ' corriendo)' : '') + ' · cumplen <b>' + fmtInt(t.cumplen || 0) + '</b>' + (mezcla ? ', contando todos los países. Abajo, solo ' + escHtml(rgFiltroNombre(fp)) + '.' : '') + '</div>';
        // Los asignados que NO se miraron porque su cuenta de anuncios esta caida: se nombran (la cuenta ilegible es la grave: puede haber gasto corriendo que nadie controla).
        if (t.caidas && t.caidas.n > 0) h += '<p class="card-sub rg-aviso-cob">⚠ De lo asignado a esta regla (' + plural + '), <b>' + fmtInt(t.caidas.n) + ' no se ' + (t.caidas.n === 1 ? 'miró' : 'miraron') + '</b> porque su cuenta de anuncios está caída' + (t.caidas.activas ? ' (' + fmtInt(t.caidas.activas) + ' de esos figuran encendidos, pero esa cuenta no gasta)' : '') + ': ' +
          (Array.isArray(t.caidas.cuentas) ? t.caidas.cuentas.map((c) => escHtml(c.etiqueta || c.id) + ' (' + fmtInt(c.n) + ')').join('; ') : '') + (t.caidas.ilegibles ? '. Alguna cuenta NO SE PUEDE LEER: puede haber gasto corriendo sin control' : '') + '.</p>';
        if (!t.miradas) h += '<p class="rg-vacio">No había ' + plural + ' para revisar' + (inf.filtro ? ' con los filtros de arriba (' + escHtml(inf.filtro) + ')' : '') + ': la regla no tiene campañas asignadas (usa «Asignar campañas»), o las que tiene están ' + (inf.filtro ? 'en otros países que los marcados arriba o ' : 'en otro país o ') + 'en una cuenta de anuncios caída.</p>';
        if (fp && t.miradas && !filas.length) h += '<p class="rg-vacio">En esta revisión no se detallaron ' + plural + ' de ' + escHtml(rgFiltroNombre(fp)) + '.</p>';
        h += filas.slice(0, ver).map((f) => rgFilaInformeHtml(f, t)).join('');
        if (filas.length > ver) h += '<details class="rg-inf-mas"><summary>Ver ' + (filas.length - ver) + ' más</summary>' + filas.slice(ver).map((f) => rgFilaInformeHtml(f, t)).join('') + '</details>';
        if ((t.miradas || 0) > todasF.length) h += '<p class="rg-mut">' + (mezcla ? 'Puede haber más de ' + escHtml(rgFiltroNombre(fp)) + ' que no se detallan' : '…y ' + fmtInt(t.miradas - todasF.length) + ' más que no se detallan') + ': van primero los que cumplen y los que más gastaron.</p>';
        h += '</div>';
      });
      h += '</div>';
    });
    return h + '</div>';
  }
  const rgFiltroTxt = (cs) => (cs.length === rgPaisesTodos().length ? 'todos los países' : cs.map(conBandera).join(' · ')) + ' · ' + (offerSel(state.offer) ? offerLabel(state.offer) : 'todas las ofertas');
  // Evalua UNA regla con los numeros que trae la pagina (los del ultimo refresco) y devuelve su informe. Igual que la revision automatica: TODOS los paises y TODAS las ofertas, sin los filtros de la pagina
  // (con un filtro de Mexico, una regla de Colombia daria «miro 0»). Ignora si esta encendida y sus fechas: es para ver sus cifras ahora, no para esperar a que empiece.
  function rgRevisarRegla(r) {
    const col = { tareas: [] }, oferta = state.offer;
    // Los arboles de cada ventana se arman con el filtro de oferta del momento y se guardan en cache: con una oferta filtrada se arman de nuevo sin ella y, al terminar, se devuelve la pagina a como estaba.
    if (oferta !== 'ALL') { _arbolV.clear(); rgLimpiar(); state.offer = 'ALL'; }
    try {
      const props = rgEvaluar([Object.assign(rgCopia(r), { on: true, programa: rgProgramaBase() })], rgPaisesTodos(), true, col);
      // La regla se evalua completa (como la revision automatica) y aqui se deja solo lo de los paises elegidos: asi las 40 filas que se detallan son de ese pais y los totales cuadran con lo que se ve.
      const fp = rgFiltroPais();
      if (fp) col.tareas.forEach((t) => {
        t.filas = t.filas.filter((f) => fp.has(f.c));
        t.miradas = t.filas.length; t.corren = t.filas.filter((f) => rgActivo(f.it)).length; t.cumplen = t.filas.filter((f) => f.cumple).length;
      });
      const inf = rgInforme(col, { filas: 40 });
      inf.propuestas = (fp ? props.filter((p) => fp.has(p.country)) : props).length; inf.sinFiltros = true; if (fp) inf.soloPais = rgFiltroNombre(fp);
      return inf;
    } finally { if (oferta !== 'ALL') { state.offer = oferta; _arbolV.clear(); rgLimpiar(); } }
  }
  // El encabezado de las cifras en vivo: de que datos salen y con que filtros, y que solo se mira.
  function rgInformeVivoCab(inf) {
    const t = inf.datos ? new Date(inf.datos).getTime() : 0;
    return '<p class="card-sub">Datos del dash: <b>' + escHtml(t > 0 ? rgFecha(t) : 'último refresco') + '</b> (el último refresco). ' + (inf.soloPais ? 'Mira la regla igual que la revisión automática, y aquí se ve solo ' + escHtml(inf.soloPais) : inf.sinFiltros ? 'Mira todos los países y todas las ofertas, igual que la revisión automática' : 'Con los filtros de arriba (' + escHtml(inf.filtro || '') + ')') + '. Ejecutaría <b>' + (inf.propuestas || 0) + '</b> ' + (inf.propuestas === 1 ? 'acción' : 'acciones') + (inf.soloPais ? ' en ' + escHtml(inf.soloPais) : '') + '. Esto solo mira: no cambia nada en Facebook.</p>';
  }
  // La ultima revision del refresco: sus cifras quedan en el registro (campo `informe`), asi se ve con que numeros se decidio aunque no saliera ninguna propuesta.
  function rgCifrasUltimaHtml() {
    const inf = rgLogPrueba && rgLogPrueba.informe;
    if (!inf || !Array.isArray(inf.reglas) || !inf.reglas.length) return '';
    const datos = inf.datos ? new Date(inf.datos).getTime() : 0;
    return '<div class="rg-lbl" style="margin-top:12px">Cifras de la última revisión</div>' +
      '<p class="card-sub">Revisión del <b>' + escHtml(inf.t > 0 ? rgFecha(inf.t) : '—') + '</b>' + (datos > 0 ? ' (con los datos del dash de las <b>' + escHtml(rgFecha(datos)) + '</b>)' : '') + '. Son los números exactos con los que se decidió cada fila: el gasto, las ventas, el ROAS… de cada condición.</p>' + rgInformeHtml(inf, { fp: rgFiltroPais() });
  }
  // El registro compartido: lo que se aplico desde el dash en CUALQUIER dispositivo (lo escribe /api/toggle-ad, ver functions/api/reglas-registro.js). Devuelve true si cambio la memoria compartida.
  async function rgCargarRegistro() {
    try {
      const r = await fetch('/api/reglas-registro', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) { rgRegServidor = null; rgRegListo = true; return false; }
      const j = await r.json();
      rgRegServidor = (j && Array.isArray(j.entradas)) ? j.entradas : null;
    } catch (e) { rgRegServidor = null; rgRegListo = true; return false; }
    rgRegListo = true;
    // La memoria compartida: lo aplicado con exito, con la clave de su propuesta, no se vuelve a ofrecer mientras dure su ventana.
    const mem = {};
    (rgRegServidor || []).forEach((x) => { if (x.ok && x.clave && (!mem[x.clave] || mem[x.clave].t < x.t)) mem[x.clave] = { t: x.t, ms: x.ms == null ? 864e5 : x.ms }; });
    const antes = JSON.stringify(rgMemCompartida);
    rgMemCompartida = mem;
    return JSON.stringify(mem) !== antes;
  }
  // Lo aplicado, todo junto: el registro compartido y lo de este navegador que el servidor no tenga (lo de antes de que existiera el compartido, o si no se pudo escribir).
  function rgRegistroTodo() {
    const srv = (rgRegServidor || []).slice(), local = rgHistorial();
    const ya = (l) => srv.some((s) => s.tipo === l.tipo && Math.abs(s.t - l.t) < 120000 && (!l.fbId || !s.id || s.id === l.fbId) && (!l.nombre || !s.nombre || s.nombre === l.nombre));
    return srv.concat(local.filter((l) => !ya(l))).sort((a, b) => b.t - a.t);
  }
  async function rgCargarPrueba() {
    try {
      const r = await fetch('/reglas-auto-log.json', { credentials: 'same-origin', cache: 'no-store' });
      if (r.ok) rgLogPrueba = await r.json();
    } catch (e) { /* sin registro todavia */ }
  }

  // ---- editor ----
  function rgCondHtml(t, ti, gi, ci) {
    const c = t.grupos[gi][ci], d = RG_METRICAS[c.m] || RG_METRICAS.gasto, P = 'tareas.' + ti + '.grupos.' + gi + '.' + ci + '.';
    const amb = rgAmbitos(t.nivel).filter((a) => c.m !== 'presupuesto' || a[0] !== 'ad');
    const rank = /^(top|bottom)/.test(c.op);
    const sinRango = d.tipo === 'estado' || d.tipo === 'texto' || d.tipo === 'numero' || c.m === 'presupuesto';
    const ops = d.tipo === 'texto' ? RG_OPS_TXT : d.tipo === 'estado' ? RG_OPS_EST : d.tipo === 'numero' ? [['es', 'es']] : RG_OPS_NUM.concat(RG_OPS_RANK);
    const rangos = RG_RANGOS.some((r) => r[0] === c.rango) ? RG_RANGOS : RG_RANGOS.concat([[c.rango, rgRangoTxt(c.rango)]]);
    const metr = (path, sel, soloNum) => {
      const g = {}; Object.keys(RG_METRICAS).forEach((k) => { if (soloNum && /^(estado|texto|numero)$/.test(RG_METRICAS[k].tipo)) return; (g[RG_METRICAS[k].grupo] = g[RG_METRICAS[k].grupo] || []).push(k); });
      return '<select data-p="' + path + '">' + Object.keys(g).map((n) => '<optgroup label="' + n + '">' + g[n].map((k) => rgOpt(k, RG_METRICAS[k].txt, k === sel)).join('') + '</optgroup>').join('') + '</select>';
    };
    let h = '<div class="rg-cond"><div class="rg-campos">' +
      '<label>Métrica' + metr(P + 'm', c.m, false) + '</label>' +
      (d.tipo === 'numero' ? '' : '<label>De' + rgSelect(P + 'ambito', amb.map((a) => [a[0], a[0] === t.nivel ? RG_NIV[a[0]] + ' (el propio)' : RG_NIV[a[0]] + ' al que pertenece']), c.ambito) + '</label>') +
      (sinRango ? '' : '<label>Período' + rgSelect(P + 'rango', rangos, c.rango) + '</label>') +
      '<label>Condición' + rgSelect(P + 'op', ops, c.op) + '</label>';
    if (d.tipo === 'texto') h += '<label>Texto<input type="text" maxlength="80" data-p="' + P + 'valor" value="' + escHtml(c.valor || '') + '"></label>';
    else if (d.tipo === 'estado') h += '<label>Estado' + rgSelect(P + 'valor', [['ACTIVE', 'activo'], ['PAUSED', 'pausado']], c.valor) + '</label>';
    else if (d.tipo === 'numero') h += '<label>Número' + rgSelect(P + 'valor', RG_NUM_VALORES, c.valor) + '</label>';
    else if (rank) h += '<label>N (cantidad o %)' + rgNumIn(P + 'valor', c.valor, ' min="1"') + '</label>';
    else {
      h += '<label>Comparar con<select data-rg-vs="' + ti + '.' + gi + '.' + ci + '">' + rgOpt('num', 'un valor', !c.vs) + rgOpt('metrica', 'otra métrica', !!c.vs) + '</select></label>';
      if (!c.vs) h += '<label>Valor' + rgNumIn(P + 'valor', c.valor) + '</label>';
      else h += '<label>Métrica' + metr(P + 'vs.m', c.vs.m, true) + '</label>' +
        '<label>De' + rgSelect(P + 'vs.ambito', rgAmbitos(t.nivel).map((a) => [a[0], RG_NIV[a[0]]]), c.vs.ambito) + '</label>' +
        '<label>Período' + rgSelect(P + 'vs.rango', rangos, c.vs.rango) + '</label>' +
        '<label>× factor' + rgNumIn(P + 'vs.factor', c.vs.factor) + '</label>';
    }
    return h + '</div>' + (RG_AYUDA[c.m] ? '<p class="rg-ayuda">' + escHtml(RG_AYUDA[c.m]) + '</p>' : '') + '<button type="button" class="rg-x" data-rg="quitar-cond" data-ti="' + ti + '" data-gi="' + gi + '" data-ci="' + ci + '">Quitar condición</button></div>';
  }
  function rgAccionHtml(t, ti, ai) {
    const a = t.acciones[ai], P = 'tareas.' + ti + '.acciones.' + ai + '.';
    const tipos = Object.keys(RG_ACCIONES).filter((k) => RG_ACCIONES[k].niveles.indexOf(t.nivel) !== -1).map((k) => [k, RG_ACCIONES[k].txt]);
    let h = '<div class="rg-accion"><div class="rg-campos"><label>Acción' + rgSelect(P + 'tipo', tipos, a.tipo) + '</label>';
    if (a.tipo === 'subir' || a.tipo === 'bajar') {
      h += '<label>Cambio' + rgSelect(P + 'modo', [['pct', 'porcentaje del presupuesto'], ['abs', 'monto fijo']], a.modo) + '</label>' +
        '<label>' + (a.modo === 'abs' ? 'Monto (moneda de la cuenta)' : 'Porcentaje') + rgNumIn(P + 'valor', a.valor, ' min="0"') + '</label>' +
        (a.tipo === 'subir' ? '<label>Hasta (tope diario; 0 = sin tope)' + rgNumIn(P + 'tope', a.tope || 0, ' min="0"') + '</label>' : '<label>No bajar de (piso diario)' + rgNumIn(P + 'piso', a.piso || 0, ' min="0"') + '</label>');
    } else if (a.tipo === 'fijar') h += '<label>Presupuesto diario (moneda de la cuenta)' + rgNumIn(P + 'valor', a.valor, ' min="1"') + '</label>';
    else if (a.tipo === 'duplicar') {
      const pc = a.presupuestoCopia;
      h += '<label>Presupuesto de la copia' + rgSelect(P + 'presupuestoCopia.modo', [['igual', 'el mismo del original'], ['pct', 'cambiar un porcentaje'], ['fijar', 'fijar un monto']], pc.modo) + '</label>' +
        (pc.modo === 'igual' ? '' : '<label>' + (pc.modo === 'pct' ? 'Porcentaje (negativo baja)' : 'Monto diario') + rgNumIn(P + 'presupuestoCopia.valor', pc.valor) + '</label>') +
        '<label class="rg-chk"><input type="checkbox" data-p="' + P + 'pausarOriginal"' + (a.pausarOriginal ? ' checked' : '') + '> Pausar el original</label>';
    }
    h += '<label>Frecuencia' + rgSelect(P + 'cada', RG_CADA, a.cada) + '</label></div>' +
      (t.acciones.length > 1 ? '<button type="button" class="rg-x" data-rg="quitar-accion" data-ti="' + ti + '" data-ai="' + ai + '">Quitar acción</button>' : '') + '</div>';
    return h;
  }
  function rgEditorHtml() {
    const r = rgBorrador, a = r.aplica;
    let h = '<div class="rg-ed-cab"><button type="button" class="btn-secondary" data-rg="volver">' + (rgIdx < 0 ? '← Descartar borrador' : '← Volver sin guardar') + '</button><b>' + (rgIdx < 0 ? 'Regla nueva' : 'Editar regla') + '</b></div>';
    // Una regla nueva es un BORRADOR: lo que trae una plantilla son valores de partida de ejemplo, no reglas fijas.
    if (rgIdx < 0) h += '<div class="rg-banner">' + (rgPlantilla ? 'Borrador de la plantilla <b>«' + escHtml(rgPlantilla) + '»</b>. Los valores que trae son un ejemplo: ' : 'Regla en blanco. Los valores que trae son un ejemplo: ') + 'cambia las métricas, los números, las acciones y las campañas que quieras. <b>Nada se guarda</b> hasta que pulses «Guardar regla»: al guardarla queda <b>encendida</b> y trabaja sola en cuanto le asignes campañas.</div>';

    h += '<div class="rg-bloque"><div class="rg-campos"><label>Nombre<input type="text" maxlength="60" data-p="nombre" value="' + escHtml(r.nombre) + '"></label></div>' +
      '<label class="rg-chk"><input type="checkbox" data-p="on"' + (r.on ? ' checked' : '') + '> Encendida (trabaja sola en las campañas que le asignes)</label>' +
      '<div class="rg-frase rg-ed-frase" id="rg-ed-frase">' + escHtml(rgFraseRegla(r)) + '</div>' +
      '<div class="rg-aviso" id="rg-ed-aviso-pausa"' + (rgAvisoPausa(r) ? '' : ' hidden') + '>' + escHtml(rgAvisoPausa(r)) + '</div></div>';
    h += rgProgramaHtml(r);
    const avisoMoneda = rgAvisoMoneda(r);
    h += '<div class="rg-bloque"><h3>Aplica a</h3><div class="rg-aplica-res">' + escHtml(rgResumenAplica(r)) + '</div>' +
      (avisoMoneda ? '<div class="rg-aviso">' + escHtml(avisoMoneda) + '</div>' : '') +
      '<div class="rg-acciones"><button type="button" class="btn-secondary" data-rg="asignar">Asignar campañas y país</button></div>' +
      '<label class="rg-chk"><input type="checkbox" data-p="aplica.futuras"' + (a.futuras ? ' checked' : '') + '> Asignar a campañas futuras</label>' +
      (a.futuras ? '<div class="rg-acciones"><button type="button" class="btn-secondary" data-rg="filtro">Filtro: ' + escHtml(rgResumenFiltro(a.filtro)) + '</button></div>' +
        '<p class="card-sub">Cuenta como futura una campaña que no existía cuando fijaste el filtro (' + a.base.length + ' campañas ya existentes quedan fuera).</p>' : '') + '</div>';
    r.tareas.forEach((t, ti) => {
      h += '<div class="rg-bloque rg-tarea"><div class="rg-tarea-cab"><h3>Tarea ' + (ti + 1) + '</h3>' +
        (r.tareas.length > 1 ? '<button type="button" class="rg-x" data-rg="quitar-tarea" data-ti="' + ti + '">Quitar tarea</button>' : '') + '</div>' +
        '<div class="rg-nivel"><div class="rg-nivel-lbl">Nivel <span class="rg-info" title="Sobre qué se aplica la regla: cada campaña, cada conjunto o cada anuncio de las campañas asignadas.">ⓘ</span></div>' +
        '<div class="rg-seg" role="radiogroup" aria-label="Nivel">' + RG_NIVELES.map((n) => '<button type="button" role="radio" aria-checked="' + (t.nivel === n[0]) + '" class="' + (t.nivel === n[0] ? 'on' : '') + '" data-rg="nivel" data-ti="' + ti + '" data-v="' + n[0] + '">' + n[1] + '</button>').join('') + '</div></div><h4>Entonces</h4>' +
        t.acciones.map((_, ai) => rgAccionHtml(t, ti, ai)).join('') +
        (t.acciones.length < 4 ? '<button type="button" class="btn-secondary" data-rg="mas-accion" data-ti="' + ti + '">+ Acción</button>' : '') +
        '<h4>Si se cumple</h4><p class="card-sub">' + (t.modo === 'or_and_or' ? 'Se cumple si de cada grupo se da alguna condición.' : 'Se cumple si se dan todas las condiciones de al menos un grupo.') + '</p><div class="rg-campos"><label>Condiciones' + rgSelect('tareas.' + ti + '.modo', [['and_or_and', '(Y) o (Y) · todas del grupo'], ['or_and_or', '(O) y (O) · alguna de cada grupo']], t.modo) + '</label></div>' +
        t.grupos.map((g, gi) => (gi ? '<div class="rg-union">' + (t.modo === 'or_and_or' ? 'y' : 'o') + '</div>' : '') +
          '<div class="rg-grupo"><div class="rg-grupo-cab">Grupo ' + (gi + 1) + (t.modo === 'or_and_or' ? ' · alguna de estas' : ' · todas estas') +
          (t.grupos.length > 1 ? ' <button type="button" class="rg-x" data-rg="quitar-grupo" data-ti="' + ti + '" data-gi="' + gi + '">Quitar grupo</button>' : '') + '</div>' +
          g.map((_, ci) => rgCondHtml(t, ti, gi, ci)).join('') +
          (g.length < 8 ? '<button type="button" class="btn-secondary" data-rg="mas-cond" data-ti="' + ti + '" data-gi="' + gi + '">+ Condición</button>' : '') + '</div>').join('') +
        (t.grupos.length < 6 ? '<button type="button" class="btn-secondary" data-rg="mas-grupo" data-ti="' + ti + '">+ Grupo (' + (t.modo === 'or_and_or' ? 'y' : 'o') + ')</button>' : '') + '</div>';
    });
    if (r.tareas.length < 4) h += '<button type="button" class="btn-secondary" data-rg="mas-tarea">+ Otra tarea</button>';
    h += '<div class="rg-bloque"><div class="rg-acciones"><button type="button" class="btn-secondary" data-rg="probar">Ver qué haría ahora</button></div><div id="rg-ed-prueba"></div></div>';
    h += '<div class="rg-ed-pie"><div class="rg-err" id="rg-ed-err"></div><button type="button" class="btn-primary" data-rg="guardar-regla">Guardar regla</button><button type="button" class="btn-secondary" data-rg="volver">Cancelar</button></div>';
    return h;
  }

  // ---- modales ----
  function rgModalHtml() {
    let cuerpo = '', tit = '';
    if (rgModal === 'galeria') {
      tit = 'Nueva regla';
      cuerpo = '<p class="card-sub">Elige una <b>plantilla</b> y adáptala, o empieza en blanco. Los valores de cada plantilla son solo un punto de partida: en el editor cambias lo que quieras (métricas, números, acciones y campañas). Nada se guarda hasta que pulses «Guardar regla»; al guardarla queda encendida y trabaja sola en las campañas que le asignes (sin campañas no hace nada).</p><div class="rg-galeria">' +
        '<button type="button" class="rg-plantilla" data-rg="plantilla" data-i="-1"><b>Desde cero</b><span>Parte de una regla mínima para que la cambies por completo.</span><span class="rg-ini">Parte de: ' + escHtml(rgFraseRegla(rgNuevaRegla())) + '</span></button>' +
        RG_PLANTILLAS.map((p, i) => '<button type="button" class="rg-plantilla" data-rg="plantilla" data-i="' + i + '"><b>' + escHtml(p.nombre) + '</b><span>' + escHtml(p.desc) + '</span><span class="rg-ini">Parte de: ' + escHtml(rgFraseRegla(rgDePlantilla(p))) + '</span></button>').join('') + '</div>';
    } else if (rgModal === 'asignar') {
      tit = 'Asignar campañas · ' + escHtml(rgBorrador.nombre);
      const ap = rgBorrador.aplica, pp = ap.paises, avisoM = rgAvisoMoneda(rgBorrador);
      cuerpo = '<div class="rg-asig-paises"><div class="rg-lbl">País donde aplica esta regla <span class="rg-info" title="La regla solo mira campañas de los países que marques. Cada país tiene la moneda de su cuenta de anuncios (Colombia: COP; el resto: USD): una regla con montos de presupuesto no debe mezclar monedas.">ⓘ</span></div>' +
        '<div class="rg-chips-pais"><button type="button" class="rg-chip-pais' + (!pp.length ? ' on' : '') + '" data-rg="pais" data-v="">Todos los países</button>' +
        rgPaisesTodos().map((c) => '<button type="button" class="rg-chip-pais' + (pp.indexOf(c) !== -1 ? ' on' : '') + '" data-rg="pais" data-v="' + c + '">' + escHtml(conBandera(c)) + ' <small>' + escHtml(rgMonedaCuenta(c)) + '</small></button>').join('') + '</div>' +
        (avisoM ? '<div class="rg-aviso">' + escHtml(avisoM) + '</div>' : '') + '</div>' +
        '<label class="rg-chk"><input type="checkbox" data-p="aplica.todas"' + (ap.todas ? ' checked' : '') + '> Todas las campañas de ' + (pp.length ? 'estos países' : 'todos los países') + ' (también las que se creen después)</label>' +
        '<div class="rg-barra"><input type="search" placeholder="Buscar campaña…" value="' + escHtml(rgQ.asig) + '" data-rg-q="asig">' +
        '<label class="rg-chk" title="Solo las encendidas que de verdad corren: se ocultan las pausadas y las encendidas en cuentas caídas"><input type="checkbox" data-rg-q="asigSolo"' + (rgQ.asigSolo ? ' checked' : '') + '> Solo activas (las que corren)</label></div>' +
        '<div class="rg-acciones"><button type="button" class="btn-secondary" data-rg="marcar-visibles">Marcar las que coinciden</button><button type="button" class="btn-secondary" data-rg="quitar-todas">Quitar todas</button>' +
        '<span class="rg-cuenta" id="rg-asig-n"></span></div><p class="rg-asig-vis" id="rg-asig-vis"></p><div class="rg-asig-lista" id="rg-asig-lista"></div>' +
        '<p class="card-sub">La lista trae las campañas de los países marcados (con el filtro de oferta de la página). La pestaña «Por ejecutar» muestra lo de esos países que además tengas marcados arriba en el filtro de la página; la regla, en cambio, trabaja en todas las campañas que le asignes.</p>';
    } else if (rgModal === 'filtro') {
      const f = rgBorrador.aplica.filtro;
      tit = 'Filtro de campañas futuras';
      cuerpo = '<p class="card-sub">Solo las campañas nuevas cuyo nombre cumpla esto entran a la regla.</p>' +
        '<div class="rg-campos"><label>Deben cumplir' + rgSelect('aplica.filtro.op', [['and', 'todas las filas (Y)'], ['or', 'alguna fila (O)']], f.op) + '</label></div>' +
        f.filas.map((x, fi) => '<div class="rg-cond"><div class="rg-campos"><label>El nombre' + rgSelect('aplica.filtro.filas.' + fi + '.op', RG_OPS_TXT, x.op) + '</label>' +
          '<label>Texto<input type="text" maxlength="60" data-p="aplica.filtro.filas.' + fi + '.valor" value="' + escHtml(x.valor || '') + '"></label></div>' +
          '<button type="button" class="rg-x" data-rg="filtro-quitar" data-fi="' + fi + '">Quitar fila</button></div>').join('') +
        (f.filas.length < 6 ? '<button type="button" class="btn-secondary" data-rg="filtro-mas">+ Fila</button>' : '') +
        '<p class="card-sub" id="rg-filtro-n"></p>';
    }
    return '<div class="rg-modal" data-rg="fondo"><div class="rg-modal-caja' + (rgModal === 'asignar' ? ' asignar' : '') + '" role="dialog" aria-modal="true" aria-labelledby="rg-modal-tit" tabindex="-1"><div class="rg-modal-cab"><b id="rg-modal-tit">' + tit + '</b><button type="button" class="btn-secondary" data-rg="cerrar-modal">' + (rgModal === 'galeria' ? 'Cerrar' : 'Listo') + '</button></div><div class="rg-modal-cuerpo">' + cuerpo + '</div></div></div>';
  }
  function rgPintarAsignar() {
    const caja = el('rg-asig-lista'); if (!caja || !rgBorrador) return;
    const a = rgBorrador.aplica, sel = new Set(a.campanas), q = rgQ.asig.toLowerCase();
    const todas = rgCampanas(a.paises.length ? a.paises : rgPaisesTodos());
    // Por pais; dentro de cada uno, primero las encendidas.
    const coincide = (x) => !q || x.nombre.toLowerCase().indexOf(q) !== -1;
    const vis = todas.filter((x) => (!rgQ.asigSolo || x.corre) && coincide(x))
      .map((x, i) => ({ x, i })).sort((p, q2) => (p.x.c < q2.x.c ? -1 : p.x.c > q2.x.c ? 1 : (rgNivelCamp(q2.x) - rgNivelCamp(p.x)) || p.i - q2.i)).map((p) => p.x);
    rgPintarAsignar.vis = vis;
    // Lo que el filtro deja fuera, dicho en palabras simples: sin esto marcar o quitar «Solo activas» casi no se nota (las activas van primero y la lista se corta en 250).
    // «Corre» = encendida y con su cuenta de anuncios funcionando.
    const vm = el('rg-asig-vis');
    if (vm) {
      const coinc = todas.filter(coincide), corren = coinc.filter((x) => x.corre).length, pausadas = coinc.filter((x) => !x.activa).length, caidas = coinc.filter((x) => x.activa && x.caida).length;
      const resto = [pausadas ? pausadas + (pausadas === 1 ? ' pausada' : ' pausadas') : '', caidas ? caidas + (caidas === 1 ? ' encendida pero con la cuenta de anuncios caída' : ' encendidas pero con la cuenta de anuncios caída') : ''].filter(Boolean).join(' y ');
      vm.textContent = (rgQ.asigSolo
        ? 'Se muestran las ' + vis.length + ' campañas que corren' + (resto ? '. No se muestran ' + resto + ': quita «Solo activas» para verlas' : '') + '.'
        : 'Se muestran las ' + vis.length + ' campañas: ' + corren + ' corren' + (resto ? ' y hay ' + resto : '') + '.') +
        (vis.length > 250 ? ' Solo se listan las primeras 250: usa el buscador.' : '');
    }
    let h = '', pais = '';
    vis.slice(0, 250).forEach((x) => {
      if (x.c !== pais) { pais = x.c; h += '<div class="rg-asig-pais">' + escHtml(conBandera(x.c)) + '</div>'; }
      h += '<label class="rg-asig-fila' + (a.todas ? ' off' : '') + '"><input type="checkbox" data-rg-camp="' + escHtml(x.id) + '"' + (sel.has(x.id) || a.todas ? ' checked' : '') + (a.todas ? ' disabled' : '') + '>' +
        '<span class="rg-asig-nom">' + escHtml(x.nombre) + '</span><span class="rg-asig-sub">' + x.sets + ' conj. · ' + x.ads + ' anuncios · ' + rgEstadoCamp(x).txt.toLowerCase() + '</span></label>';
    });
    if (vis.length > 250) h += '<p class="card-sub">Hay ' + (vis.length - 250) + ' más: usa el buscador para encontrarlas.</p>';
    caja.innerHTML = h || '<p class="rg-vacio">No hay campañas con ese filtro.</p>';
    rgContarAsignadas();
  }
  function rgContarAsignadas() {
    const n = el('rg-asig-n'); if (!n || !rgBorrador) return;
    // Solo cuentan las marcadas de los paises en alcance: las de un pais que se quito de la regla quedan guardadas pero no aplican.
    const dentro = new Set(rgTodasCamp().filter((x) => rgPaisOk(rgBorrador, x.c)).map((x) => x.id));
    n.textContent = rgBorrador.aplica.todas ? 'Todas' : (() => { const n = rgBorrador.aplica.campanas.filter((id) => dentro.has(id)).length; return n + (n === 1 ? ' seleccionada' : ' seleccionadas'); })();
  }
  function rgContarFiltro() {
    const n = el('rg-filtro-n'); if (!n || !rgBorrador) return;
    const f = rgBorrador.aplica.filtro, todas = rgCampanas(rgPaisesTodos());
    n.textContent = 'Hoy coincidirían ' + todas.filter((x) => rgFiltroCoincide(f, x.nombre)).length + ' de ' + todas.length + ' campañas existentes (esas no cuentan como futuras).';
  }
  // «Futuras» = no estaba cuando se fijo el filtro: se guarda la foto de las campañas de hoy.
  const rgFotoBase = () => rgCampanas(rgPaisesTodos()).map((x) => x.id);

  // ---- guardar ----
  async function rgGuardar() {
    try {
      const r = await fetch('/api/reglas', { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reglas: { v: 3, lista: reglas } }) });
      const j = await r.json();
      if (!j.ok) { rgDecir(j.error || 'No se pudo guardar'); return false; }
      rgDecir('Guardado ✓'); rgDespuesDeGuardar(); return true;
    } catch (e) { rgDecir('Error de red: no se guardó'); return false; }
  }
  // Las reglas que de verdad trabajan: encendidas, dentro de sus fechas y con al menos una campaña (o el filtro de futuras). Cuando una pasa a trabajar -se creo, se encendio, se le asignaron
  // campañas- se pide una revision enseguida (el mismo boton «Actualizar» del menu: un refresco, maximo uno por hora) para que no espere hasta el proximo refresco.
  const rgVivasAhora = () => reglas.filter((r) => rgVigente(r) && (r.aplica.futuras || rgCubre(r).length > 0)).map((r) => r.id);
  function rgDespuesDeGuardar() {
    const ahora = rgVivasAhora(), antes = rgVivasPrevias;
    rgVivasPrevias = ahora;
    if (antes !== null && ahora.some((id) => antes.indexOf(id) === -1)) rgPedirRevision();
  }
  async function rgPedirRevision() {
    try {
      const r = await fetch('/api/refrescar', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const j = await r.json();
      if (j && j.disparado) rgNota = 'Guardada. Se está revisando ahora: en unos 5 minutos la regla habrá hecho lo que le toca. Míralo en «Registro».';
      else if (j && j.corriendo) rgNota = 'Guardada. Ya hay un refresco corriendo; si empezó antes de que guardaras, la regla entra en el siguiente.';
      else if (j && j.ok) rgNota = 'Guardada. Se revisa en el próximo refresco del dash' + (j.faltanMin ? ' (el último fue hace ' + j.minutos + ' min; el siguiente se puede pedir en ' + j.faltanMin + ' min con el botón «Actualizar» del menú)' : '') + '.';
      else rgNota = 'Guardada. No pude pedir la revisión (' + ((j && j.motivo) || r.status) + '): se hará en el próximo refresco.';
    } catch (e) { rgNota = 'Guardada. Se revisa en el próximo refresco del dash.'; }
    if (rgVista === 'reglas' && !rgModal) rgPintar();
  }

  function rgRefrescar() { renderReglas(selectedCountries()); }
  function rgValidar(r) {
    if (!r.nombre.trim()) return 'Ponle un nombre a la regla.';
    const pg = rgProgramaBase(r.programa);
    if (pg.desde && pg.hasta && pg.desde > pg.hasta) return 'En la programación, la fecha final es anterior a la inicial.';
    for (let i = 0; i < r.tareas.length; i++) {
      const t = r.tareas[i];
      if (!t.acciones.length) return 'La tarea ' + (i + 1) + ' no tiene acciones.';
      if (!t.grupos.length || t.grupos.every((g) => !g.length)) return 'La tarea ' + (i + 1) + ' no tiene condiciones: sin ellas no haría nada.';
    }
    return '';
  }
  // Sube hasta el borde de la tarjeta de reglas. Encima hay el titulo, los filtros y la barra del objetivo (casi una pantalla): con scrollTo(0, 0) el editor
  // quedaba bajo la linea de flotacion y la pantalla parecia vacia.
  function rgAlTope() { const c = el('rg-card'); window.scrollTo(0, c ? Math.max(0, Math.round(c.getBoundingClientRect().top + window.scrollY - 8)) : 0); }
  function rgAbrirEditor(r, idx, plantilla) {
    rgBorrador = rgNormalizar(rgCopia(r)); rgIdx = idx; rgPlantilla = plantilla || ''; rgDesdeLista = false; rgModal = null; rgVista = 'editor';
    const pr = rgBorrador.programa; rgProgOtro = !RG_CADA_REV.some((o) => o[0] === pr.cadaMin); rgProgUnidad = rgUnidadDe(pr.cadaMin); rgProgFechas = !!(pr.desde || pr.hasta); rgProgHoras = pr.horaDesde != null;
    rgPintar(); rgAlTope();
  }

  // ---- ejecutar (aprobar) ----
  async function rgPost(cuerpo) {
    const r = await fetch(TOGGLE_ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) });
    return r.json();
  }
  async function rgEjecutar(p, pwd) {
    const base = { id: p.fbId, type: p.nivel, accountId: p.accountId, password: pwd, meta: rgMetaDe(p) };
    try {
      if (p.tipo === 'pausar' || p.tipo === 'reactivar') {
        const d = await rgPost(Object.assign({ action: p.tipo === 'pausar' ? 'pause' : 'resume' }, base));
        if (!d.ok) { rgAnotar(p, false, d.error || 'no se pudo aplicar'); return { ok: false, msg: d.error || 'No se pudo aplicar' }; }
        if (d.efectivo) aplicarCambioReal(p.nivel, p.fbId, d); else aplicarEstadoLocal(p.country, p.nivel, p.fbId, p.tipo === 'reactivar');
        if (d.aviso) { rgAnotar(p, true, d.aviso); rgRecordar(p.clave, rgMemoriaMs(p), true); return { ok: true, aviso: d.aviso }; }
      } else if (p.tipo === 'subir' || p.tipo === 'bajar' || p.tipo === 'fijar') {
        const d = await rgPost(Object.assign({ action: 'budget', budgetKind: 'daily', amount: p.nuevo }, base));
        if (!d.ok) { rgAnotar(p, false, d.error || 'no se pudo aplicar'); return { ok: false, msg: d.error || 'No se pudo aplicar' }; }
        aplicarCambioReal(p.nivel, p.fbId, { presupuesto: { daily: (d.presupuesto && d.presupuesto.daily > 0) ? d.presupuesto.daily : p.nuevo } });
      } else if (p.tipo === 'duplicar') {
        const d = await rgPost(Object.assign({ action: 'duplicate' }, base));
        if (!d.ok) { rgAnotar(p, false, d.error || 'no se pudo aplicar'); return { ok: false, msg: d.error || 'No se pudo aplicar' }; }
        const pc = p.extra && p.extra.presupuestoCopia, notas = [];
        if (d.copia && pc && p.actual > 0 && (pc.modo === 'pct' || pc.modo === 'fijar')) {
          const n = p.copiaNuevo > 0 ? Math.round(p.copiaNuevo) : (pc.modo === 'fijar' ? Math.round(Number(pc.valor)) : Math.round(p.actual * (1 + Number(pc.valor) / 100)));
          if (n >= 1 && n !== Math.round(p.actual)) {
            const b = await rgPost({ action: 'budget', id: d.copia, type: p.nivel, accountId: p.accountId, password: pwd, budgetKind: 'daily', amount: n, meta: rgMetaDe(p, { nombre: 'Copia de ' + p.nombre, tipo: 'fijar', razon: 'Parte de duplicar: presupuesto de la copia.', clave: '', antes: null, despues: n }) });
            notas.push(b.ok ? 'presupuesto de la copia: ' + fmtMoney(n, 0) : 'la copia se creó pero no se le pudo poner presupuesto (' + (b.error || 'error') + ')');
          }
        }
        if (p.extra && p.extra.pausarOriginal) {
          const o = await rgPost(Object.assign({ action: 'pause' }, base, { meta: rgMetaDe(p, { tipo: 'pausar', razon: 'Parte de duplicar: se pausa el original.', clave: '', antes: null, despues: null }) }));
          notas.push(o.ok ? 'original pausado' : 'no se pudo pausar el original (' + (o.error || 'error') + ')');
          if (o.ok) aplicarEstadoLocal(p.country, p.nivel, p.fbId, false);
        }
        rgRecordar(p.clave, rgMemoriaMs(p), true); rgAnotar(p, true, notas.join('; '), { copia: d.copia || '' });
        return { ok: true, msg: notas.join('; ') };
      }
      rgRecordar(p.clave, rgMemoriaMs(p), true); rgAnotar(p, true);
      return { ok: true };
    } catch (err) { rgAnotar(p, false, 'error de red'); return { ok: false, msg: 'Error de red' }; }
  }
  async function rgAprobar(p, boton) {
    try {
      const pwd = await ensureAuth();
      boton.disabled = true;
      const r = await rgEjecutar(p, pwd);
      if (!r.ok) { boton.disabled = false; toast(r.msg, 'error'); render(); return; }
      if (r.aviso) toast(r.aviso, 'warn', 14000);
      else toast(p.tipo === 'pausar' ? 'Pausado' : p.tipo === 'reactivar' ? 'Reactivado' : p.tipo === 'duplicar' ? 'Copia creada (queda pausada)' + (r.msg ? ' · ' + r.msg : '') :
        'Presupuesto ' + (p.tipo === 'subir' ? 'subido' : p.tipo === 'bajar' ? 'bajado' : 'fijado') + ' a ' + fmtMoney(p.nuevo, 0), 'success');
      render();
    } catch (err) { boton.disabled = false; }
  }
  async function rgAprobarLote(tipo, boton) {
    const lista = rgActuales.filter((p) => p.tipo === tipo && !(p.accountId && puedeEscribir(p.accountId) === false));
    if (!lista.length) return;
    const paises = Array.from(new Set(lista.map((p) => COUNTRY_LABEL[p.country] || p.country).filter(Boolean))).join(', ');      // la pagina puede estar viendo varios paises: quien confirma tiene que saber cuales toca
    if (!window.confirm(RG_ACCIONES[tipo].lote + ': ' + lista.length + ' elementos' + (paises ? ' en ' + paises : '') + '.\n\nSe aplican uno por uno con la misma contraseña. ¿Seguir?')) return;
    try {
      const pwd = await ensureAuth();
      boton.disabled = true;
      let bien = 0, mal = 0, ultimo = '';
      for (const p of lista) { const r = await rgEjecutar(p, pwd); if (r.ok) bien++; else { mal++; ultimo = r.msg; } }
      toast(bien + ' aplicadas' + (mal ? ', ' + mal + ' con error (' + ultimo + ')' : ''), mal ? 'error' : 'success');
      render();
    } catch (err) { boton.disabled = false; }
  }

  // ---- gancho de scripts/reglas-auto.cjs ----
  // El refresco de GitHub abre esta misma pagina sin pantalla y llama aqui con las reglas guardadas. Evalua con LA MISMA
  // funcion que ve el equipo, asi los numeros no pueden diferir de los del dashboard. Devuelve las acciones; quien las ejecuta es el script, por /api/reglas-ejecutar.
  // `todas` (opcional) = TODAS las reglas guardadas y encendidas, incluidas las que esperan su turno segun su programa y por eso no vienen en `lista`: solo se usa para la cobertura (las campañas que
  // cubre una regla que espera su turno no estan «sin regla»). Sin ella, la cobertura sale de `lista`.
  window.__reglasAuto = function (lista, memoria, todas) {
    const rs = (lista || []).map((r) => rgNormalizar(rgCopia(r)));
    _arbolV.clear(); rgLimpiar();
    // Lo que ya se aplico (el registro compartido del servidor, { clave: { t, ms } }): una accion dentro de su frecuencia no vuelve a salir.
    if (memoria && typeof memoria === 'object' && !Array.isArray(memoria)) rgMemCompartida = memoria;
    // TODOS los paises con datos, no solo los que esten marcados en el filtro de quien abra la pagina.
    const todosPaises = Object.keys(SNAPSHOT.countries), col = { tareas: [] }, props = rgEvaluar(rs, todosPaises, false, col);
    // Las cifras con las que se decidio cada cosa: las lee scripts/reglas-auto.cjs y las deja en el registro (se ven en /reglas > Registro).
    // Con la cobertura: cuantas campañas corren y cuantas no las cubre ninguna regla encendida (lo que nadie esta mirando).
    window.__reglasInforme = rgInforme(col);
    const encendidas = Array.isArray(todas) ? todas.map((r) => rgNormalizar(rgCopia(r))) : rs;
    window.__reglasInforme.cobertura = rgCobertura(encendidas, todosPaises);
    // Las reglas que se contradicen en este momento (campañas que cumplen a la vez una que pausa y otra que reactiva, o una que sube el presupuesto y otra que lo baja): se miran TODAS las encendidas y
    // vivas ahora (fechas y horario), no solo las que tocaba revisar. Solo avisa; un fallo aqui nunca tumba la revision.
    try { window.__reglasInforme.choques = rgChoques(encendidas, todosPaises); } catch (e) { window.__reglasInforme.choques = []; }
    // JSON no guarda Infinity: la ventana de «una sola vez» viaja como -1 (para siempre); el resto, en ms y nunca menos de 24 h salvo pausar (ver rgMemoriaMs). El ejecutor la usa para todo menos pausar,
    // donde lee la frecuencia de la regla guardada.
    props.forEach((p) => { const w = rgMemoriaMs(p); p.ms = w === Infinity ? -1 : w; });
    return props;
  };
  window.__reglasInformeHtml = function (inf) { return rgInformeHtml(inf); };       // para scripts/probar-informe-reglas.cjs
  // Las cuentas del programa de una regla, para scripts/probar-programa.cjs: comprueba que dan lo mismo que su copia en scripts/reglas-auto.cjs.
  window.__reglasPrograma = { base: rgProgramaBase, enFechas: rgEnFechas, cadaTxt: rgCadaTxt, hoyCO: rgHoyCO, fechaCorta: rgFechaCorta, proxima: rgProximaRevision, desdeTxt: rgDesdeTxt, enHorario: rgEnHorario, horaNum: rgHoraNumCO, horasTxt: rgHorasTxt };
  // Los choques entre reglas, para scripts/probar-choques-reglas.cjs: el detector y la frase (la pantalla y la revision automatica usan estas mismas dos funciones).
  // El numero de WhatsApp de una campaña y su estado, para scripts/probar-numero-conectado.cjs: el nombre -> numero, y la lista de numeros ya procesada (con el motivo si no vale).
  window.__reglasNumero = { deCampana: rgNumeroDeCampana, info: rgInfoNumero, lista: rgNumeros };
  window.__reglasChoques = { calcular: rgChoques, frase: rgChoqueFrase, listo: () => !rgChoquesTimer };       // listo = ya no hay una cuenta del aviso pendiente (las pruebas esperan eso antes de mirar el banner)

  // ---- render y arranque ----
  function renderReglas(cs) {
    const card = el('rg-card'); if (!card) return;
    // Las acciones solo existen en el dashboard combinado, igual que los interruptores del arbol.
    if (MODO !== 'reglas' || !IS_COMBINED || !reglasDisponibles) { card.style.display = 'none'; return; }
    card.style.display = '';
    rgLimpiar();
    rgActuales = reglas.some((r) => r.on) ? rgEvaluar(reglas, cs) : [];
    if (rgRevisarId) { const rc = reglas.filter((r) => r.id === rgRevisarId)[0]; if (rc) rgInformeVivo = rgRevisarRegla(rc); else { rgRevisarId = ''; rgInformeVivo = null; } }
    // Con el editor o un modal abiertos no se repinta: se perderia lo que estas escribiendo.
    if (rgVista === 'editor' || rgModal) { const c = el('rg-count'); if (c) c.textContent = rgActuales.length ? String(rgActuales.length) : ''; return; }
    rgPintar();
  }
  function rgDeAlmacen(j) {
    if (j && Array.isArray(j.lista)) return j.lista.filter((r) => r && typeof r === 'object').map((r) => rgNormalizar(r));
    return null;
  }
  function rgEscuchas(root) {
    root.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-rg]'); if (!b) return;
      const k = b.getAttribute('data-rg'), num = (a) => Number(b.getAttribute(a));
      if (k === 'fondo') { if (e.target !== b) return; const x = b.querySelector('[data-rg="cerrar-modal"]'); if (x) x.click(); return; }       // tocar fuera = «Listo»: lo marcado ya esta en el borrador
      const t = rgBorrador && rgBorrador.tareas[num('data-ti')];
      switch (k) {
        case 'tab': rgVista = b.getAttribute('data-v'); rgPintar(); if (rgVista === 'registro') Promise.all([rgCargarPrueba(), rgCargarRegistro()]).then(() => { if (rgVista === 'registro') rgPintar(); }); break;
        case 'nueva': rgModal = 'galeria'; rgPintar(); break;
        case 'cubre': { const rc = reglas[num('data-i')]; if (rc) { rgCubreAbierta = rgCubreAbierta === rc.id ? '' : rc.id; rgPintar(); } break; }
        case 'pais': {                                                             // pais(es) donde aplica la regla; vacio = todos
          const v = b.getAttribute('data-v'), ap = rgBorrador.aplica, todosP = rgPaisesTodos();
          if (!v) ap.paises = [];
          else if (!ap.paises.length) ap.paises = [v];                             // estaban todos: tocar uno deja solo ese
          else { const s = new Set(ap.paises); if (s.has(v)) s.delete(v); else s.add(v); ap.paises = todosP.filter((c) => s.has(c)); if (ap.paises.length === todosP.length) ap.paises = []; }
          rgPintar(); rgPintarAsignar(); break;
        }
        case 'plantilla': { const i = num('data-i'); rgModal = null; rgAbrirEditor(i < 0 ? rgNuevaRegla() : rgDePlantilla(RG_PLANTILLAS[i]), -1, i < 0 ? '' : RG_PLANTILLAS[i].nombre); break; }
        case 'editar': rgAbrirEditor(reglas[num('data-i')], num('data-i')); break;
        case 'dup': { const c = rgCopia(reglas[num('data-i')]); c.id = 'r' + Date.now().toString(36); c.nombre += ' (copia)'; c.on = false; reglas.splice(num('data-i') + 1, 0, c); rgPintar(); rgGuardar(); break; }
        case 'del': if (window.confirm('¿Eliminar la regla «' + reglas[num('data-i')].nombre + '»?')) { reglas.splice(num('data-i'), 1); rgRefrescar(); rgGuardar(); } break;
        case 'asignar-lista': rgBorrador = rgNormalizar(rgCopia(reglas[num('data-i')])); rgIdx = num('data-i'); rgDesdeLista = true; rgModal = 'asignar'; rgPintar(); rgPintarAsignar(); break;
        case 'asignar': rgModal = 'asignar'; rgPintar(); rgPintarAsignar(); break;
        case 'filtro': rgModal = 'filtro'; if (!rgBorrador.aplica.filtro.filas.length) rgBorrador.aplica.filtro.filas.push({ campo: 'nombre', op: 'contiene', valor: '' }); rgPintar(); rgContarFiltro(); break;
        case 'filtro-mas': rgBorrador.aplica.filtro.filas.push({ campo: 'nombre', op: 'contiene', valor: '' }); rgPintar(); rgContarFiltro(); break;
        case 'filtro-quitar': rgBorrador.aplica.filtro.filas.splice(num('data-fi'), 1); rgPintar(); rgContarFiltro(); break;
        case 'marcar-visibles': { const s = new Set(rgBorrador.aplica.campanas); (rgPintarAsignar.vis || []).forEach((x) => s.add(x.id)); rgBorrador.aplica.campanas = Array.from(s); rgPintarAsignar(); break; }
        case 'quitar-todas': rgBorrador.aplica.campanas = []; rgBorrador.aplica.todas = false; rgPintar(); rgPintarAsignar(); break;
        case 'cerrar-modal': {
          const era = rgModal; rgModal = null;
          if (era === 'filtro') { rgBorrador.aplica.filtro.filas = rgBorrador.aplica.filtro.filas.filter((x) => String(x.valor || '').trim()); rgBorrador.aplica.base = rgFotoBase(); }
          if (era === 'asignar' && rgDesdeLista) { reglas[rgIdx] = rgBorrador; rgBorrador = null; rgDesdeLista = false; rgRefrescar(); rgGuardar(); break; }
          rgPintar(); break;
        }
        case 'volver': rgBorrador = null; rgVista = 'reglas'; rgRefrescar(); rgAlTope(); break;
        case 'nivel': t.nivel = b.getAttribute('data-v'); rgArreglarTarea(t); rgPintar(); break;
        case 'prog-rango': {                                                       // «Continua, desde hoy» limpia las fechas; «Entre dos fechas» parte de hoy y deja el final abierto
          const pr = rgBorrador.programa;
          if (b.getAttribute('data-v') === 'fechas') { rgProgFechas = true; if (!pr.desde) pr.desde = rgHoyCO(); } else { rgProgFechas = false; pr.desde = ''; pr.hasta = ''; }
          rgPintar(); break;
        }
        case 'prog-horas': {                                                       // «Todo el dia» quita el horario; «Solo en un horario» parte de 5:00 p. m. a medianoche (el caso del freno de la noche), editable
          const pr = rgBorrador.programa;
          if (b.getAttribute('data-v') === 'horas') { rgProgHoras = true; if (pr.horaDesde == null || pr.horaHasta == null) { pr.horaDesde = 17; pr.horaHasta = 24; } } else { rgProgHoras = false; pr.horaDesde = null; pr.horaHasta = null; }
          rgPintar(); break;
        }
        case 'camp-vista': rgC.vista = b.getAttribute('data-v'); rgC.limite = 100; rgPintar(); break;
        case 'camp-abrir': rgC.abierta = rgC.abierta === b.getAttribute('data-id') ? '' : b.getAttribute('data-id'); rgPintar(); break;
        case 'camp-mas': rgC.limite += 100; rgPintar(); break;
        case 'camp-acciones': { const barra = document.querySelector('.rg-bulk'); if (barra) window.scrollTo({ top: Math.max(0, Math.round(barra.getBoundingClientRect().top + window.scrollY - 8)), behavior: 'smooth' }); break; }
        case 'camp-limpiar': rgC.sel.clear(); if (rgC.vista === 'sel') rgC.vista = 'todas'; rgPintar(); break;
        case 'camp-marcar': (rgCampanasHtml.vis || []).forEach((x) => rgC.sel.add(x.id)); rgPintar(); break;
        case 'camp-bulk': {
          const r = rgC.regla === '' ? null : reglas[Number(rgC.regla)];              // Number('') es 0: sin esto, con «Elige una regla…» se asignaba a la primera regla de la lista
          if (!r) { rgDecir('Elige primero la regla.'); break; }
          if (r.aplica.todas) { rgDecir('«' + r.nombre + '» ya aplica a todas las campañas.'); break; }
          const set = new Set(r.aplica.campanas), n0 = set.size, paisDe = new Map(rgTodasCamp().map((x) => [x.id, x.c])); let omitidas = 0;
          rgC.sel.forEach((id) => { if (b.getAttribute('data-m') === 'quitar') set.delete(id); else if (rgPaisOk(r, paisDe.get(id))) set.add(id); else omitidas++; });
          r.aplica.campanas = Array.from(set).slice(0, 4000);
          rgDecir((b.getAttribute('data-m') === 'quitar' ? 'Quitada de ' + (n0 - set.size) : 'Asignada a ' + (set.size - n0)) + ' campañas · «' + r.nombre + '»' + (omitidas ? ' · ' + omitidas + ' omitidas: la regla solo aplica a ' + rgPaisesTxt(r) : ''));
          rgRefrescar(); rgGuardarLuego(); break;
        }
        case 'camp-estado': rgEstadoLote(b.getAttribute('data-m'), b); break;
        case 'mas-tarea': rgBorrador.tareas.push(rgNuevaTarea()); rgPintar(); break;
        case 'quitar-tarea': rgBorrador.tareas.splice(num('data-ti'), 1); rgPintar(); break;
        case 'mas-accion': t.acciones.push({ tipo: 'avisar', cada: 'siempre' }); rgArreglarAccion(t.acciones[t.acciones.length - 1], t.nivel); rgPintar(); break;
        case 'quitar-accion': t.acciones.splice(num('data-ai'), 1); rgPintar(); break;
        case 'mas-grupo': t.grupos.push([{ m: 'gasto', ambito: t.nivel, rango: 'last7x1', op: '>=', valor: 25 }]); rgPintar(); break;
        case 'quitar-grupo': t.grupos.splice(num('data-gi'), 1); rgPintar(); break;
        case 'mas-cond': t.grupos[num('data-gi')].push({ m: 'roas', ambito: t.nivel, rango: 'last7x1', op: '<', valor: 1 }); rgPintar(); break;
        case 'quitar-cond': t.grupos[num('data-gi')].splice(num('data-ci'), 1); rgPintar(); break;
        case 'revisar': {                                                          // «Revisar ahora»: las cifras exactas de esta regla con los numeros de ahora (no cambia nada en Facebook)
          const rc = reglas[num('data-i')]; if (!rc) break;
          if (rgRevisarId === rc.id) { rgRevisarId = ''; rgInformeVivo = null; } else { rgRevisarId = rc.id; rgInformeVivo = rgRevisarRegla(rc); }
          rgPintar(); break;
        }
        case 'probar': {
          const caja = el('rg-ed-prueba'); if (!caja) break;
          // «Ver qué haría ahora» ignora las fechas del programa: es para probar la regla, no para esperar a que empiece.
          const col = { tareas: [] }, cs = selectedCountries();
          const props = rgEvaluar([Object.assign(rgCopia(rgBorrador), { on: true, programa: rgProgramaBase() })], cs, true, col);
          const inf = rgInforme(col, { filas: 40 }); inf.propuestas = props.length; inf.filtro = rgFiltroTxt(cs);
          caja.innerHTML = '<p class="card-sub">Con los números de ahora y los países filtrados, esta regla ejecutaría <b>' + props.length + '</b> ' + (props.length === 1 ? 'acción' : 'acciones') + '. Esto solo mira: no cambia nada en Facebook.</p>' +
            props.slice(0, 8).map((p) => '<div class="rg-fila"><span class="pill ' + RG_ACCIONES[p.tipo].pill + '">' + RG_ACCIONES[p.tipo].txt + '</span> ' + escHtml(conBandera(p.country)) + ' · ' + escHtml(p.nombre) + '<div class="rg-razon">' + escHtml(p.texto) + '</div></div>').join('') +
            (props.length > 8 ? '<p class="card-sub">…y ' + (props.length - 8) + ' más.</p>' : '') +
            '<div class="rg-lbl" style="margin-top:12px">Las cifras, una por una</div>' + rgInformeHtml(inf);
          break;
        }
        case 'guardar-regla': {
          rgBorrador.tareas.forEach(rgArreglarTarea);
          // Con «Solo en un horario» elegido y dos horas que no hacen un horario (la misma hora, o de 12:00 a. m. a medianoche) se avisa en vez de guardar la regla trabajando todo el dia sin que se note.
          const horarioMalo = rgProgHoras && rgProgramaBase(rgBorrador.programa).horaDesde == null;
          if (!horarioMalo) rgBorrador.programa = rgProgramaBase(rgBorrador.programa);              // con el horario malo se dejan las horas tal cual para que se vean y se corrijan
          const err = horarioMalo ? 'En «Horario del día» elige dos horas distintas (o pon «Todo el día»).' : rgValidar(rgBorrador), caja = el('rg-ed-err');
          if (err) { if (caja) caja.textContent = err; break; }
          if (rgIdx < 0) reglas.push(rgBorrador); else reglas[rgIdx] = rgBorrador;
          rgNota = '';
          rgBorrador = null; rgVista = 'reglas'; rgRefrescar(); rgAlTope(); rgGuardar();
          break;
        }
        case 'aprobar': { const p = rgActuales[num('data-i')]; if (p) rgAprobar(p, b); break; }
        case 'ignorar': { const p = rgActuales[num('data-i')]; if (p) { rgRecordar(p.clave, rgIgnorarMs(p)); rgRefrescar(); } break; }
        case 'lote': rgAprobarLote(b.getAttribute('data-t'), b); break;
      }
    });
    // Cualquier campo con data-p escribe en el borrador por su ruta; los que cambian la forma del editor lo repintan.
    const alCambiar = (e, final) => {
      const x = e.target;
      if (x.hasAttribute('data-rg-nuevo') || x.hasAttribute('data-rg-copia')) {                // propuestas: presupuesto editable antes de aprobar
        const esN = x.hasAttribute('data-rg-nuevo'), p = rgActuales[Number(x.getAttribute(esN ? 'data-rg-nuevo' : 'data-rg-copia'))], n = Math.round(parseFloat(x.value));
        if (p && n >= 1) {
          if (esN) { p.nuevo = n; p.texto = RG_ACCIONES[p.tipo].txt + ' diario de ' + fmtMoney(p.actual, 0) + ' a ' + fmtMoney(n, 0) + ': ' + (p.razon || '') + '.'; const t = x.closest('.rg-item') && x.closest('.rg-item').querySelector('.rg-razon'); if (t) t.textContent = p.texto; }
          else p.copiaNuevo = n;
        }
        return;
      }
      if (x.hasAttribute('data-rg-q')) {                                        // buscadores y «solo activas»
        const k = x.getAttribute('data-rg-q'); rgQ[k] = x.type === 'checkbox' ? x.checked : x.value;
        // El texto ya se aplico con cada letra (evento input). Al salir del campo llega un change que repintaba otra vez: justo cuando el dedo baja sobre una fila de la lista o una tarjeta, el nodo cambia y el primer toque se pierde.
        if (final && x.type !== 'checkbox') return;
        if (k === 'reglas' || k === 'soloOn') { rgPintar(); const b = el('rg-buscar'); if (b && k === 'reglas' && !final) { b.focus(); b.setSelectionRange(b.value.length, b.value.length); } }
        else rgPintarAsignar();
        return;
      }
      if (x.hasAttribute('data-rg-cq')) {                                       // pestaña Campañas: filtros
        const k = x.getAttribute('data-rg-cq');
        rgC[k] = x.type === 'checkbox' ? x.checked : x.value;
        if (k === 'regla') return;
        rgC.limite = 100; rgPintar();
        const b = el('rg-cbuscar'); if (b && k === 'q' && !final) { b.focus(); b.setSelectionRange(b.value.length, b.value.length); }
        return;
      }
      if (x.hasAttribute('data-rg-csel')) {
        const id = x.getAttribute('data-rg-csel'); if (x.checked) rgC.sel.add(id); else rgC.sel.delete(id);
        rgPintar(); return;
      }
      if (x.hasAttribute('data-rg-crule')) {
        const r = reglas[Number(x.getAttribute('data-rg-crule'))], id = rgC.abierta;
        if (r && id) {
          const c = r.aplica.campanas, i = c.indexOf(id);
          if (x.checked && i === -1) c.push(id); else if (!x.checked && i !== -1) c.splice(i, 1);
          rgRefrescar(); rgGuardarLuego();
        }
        return;
      }
      if (x.hasAttribute('data-rg-on')) { const r = reglas[Number(x.getAttribute('data-rg-on'))]; if (r) { r.on = x.checked; rgRefrescar(); rgGuardar(); } return; }
      if (x.hasAttribute('data-rg-fuente')) { rgFuenteLog = x.value; rgPintar(); (rgFuenteLog === 'revisiones' ? rgCargarPrueba() : rgCargarRegistro()).then(() => { if (rgVista === 'registro') rgPintar(); }); return; }
      if (x.hasAttribute('data-rg-camp')) {
        const id = x.getAttribute('data-rg-camp'), c = rgBorrador.aplica.campanas, i = c.indexOf(id);
        if (x.checked && i === -1) c.push(id); else if (!x.checked && i !== -1) c.splice(i, 1);
        rgContarAsignadas(); return;
      }
      if (x.hasAttribute('data-rg-vs')) {
        const [ti, gi, ci] = x.getAttribute('data-rg-vs').split('.').map(Number), t = rgBorrador.tareas[ti], c = t.grupos[gi][ci];
        if (x.value === 'metrica') c.vs = { t: 'metrica', m: c.m, ambito: c.ambito, rango: c.rango, factor: 1 }; else delete c.vs;
        rgArreglarCond(c, t.nivel); rgPintar(); return;
      }
      if (x.hasAttribute('data-rg-prog')) { if (rgBorrador) rgProgCambio(x, final); return; }
      if (!x.hasAttribute('data-p') || !rgBorrador) return;
      const path = x.getAttribute('data-p');
      let v;
      if (x.type === 'checkbox') v = x.checked;
      else if (x.type === 'number') { const n = parseFloat(x.value); v = isNaN(n) ? null : n; }
      else v = x.value;
      const previo = rgGet(rgBorrador, path);
      rgSet(rgBorrador, path, v);
      const mc = /^tareas\.(\d+)\.grupos\.(\d+)\.(\d+)\.(m|op)$/.exec(path);
      if (mc) {
        const cond = rgBorrador.tareas[Number(mc[1])].grupos[Number(mc[2])][Number(mc[3])], def = RG_METRICAS[cond.m], numerica = !!def && !/^(estado|texto|numero)$/.test(def.tipo), rank = (o) => /^(top|bottom)/.test(String(o));
        if (mc[4] === 'm' && numerica && !cond.vs && !rank(cond.op) && RG_VALOR_INICIAL[cond.m] != null) cond.valor = RG_VALOR_INICIAL[cond.m];
        if (mc[4] === 'op') { if (rank(v) && !rank(previo)) cond.valor = 10; else if (!rank(v) && rank(previo) && numerica && RG_VALOR_INICIAL[cond.m] != null) cond.valor = RG_VALOR_INICIAL[cond.m]; }
      }
      // Texto y numeros: solo se actualiza la frase de arriba. Repintar al salir del campo se comeria el toque que te lleva al siguiente.
      if (x.type === 'number' || x.type === 'text') {
        const f = el('rg-ed-frase'); if (f) f.textContent = rgFraseRegla(rgBorrador);
        const ap = el('rg-ed-aviso-pausa'); if (ap) { const t = rgAvisoPausa(rgBorrador); ap.textContent = t; ap.hidden = !t; }
        if (path.indexOf('aplica.filtro') === 0) rgContarFiltro();
        return;
      }
      const m = /^tareas\.(\d+)\./.exec(path);
      if (m) rgArreglarTarea(rgBorrador.tareas[Number(m[1])]);
      if (path === 'aplica.futuras' && v) { if (!rgBorrador.aplica.base.length) rgBorrador.aplica.base = rgFotoBase(); }
      if (path === 'aplica.todas') { rgPintar(); rgPintarAsignar(); return; }
      if (path === 'nombre' || path === 'on') { const f = el('rg-ed-frase'); if (f) f.textContent = rgFraseRegla(rgBorrador); return; }
      if (path.indexOf('aplica.filtro') === 0) { rgPintar(); rgContarFiltro(); return; }
      rgPintar();
    };
    root.addEventListener('input', (e) => { if (e.target.matches('input[type="text"],input[type="number"],input[type="search"]')) alCambiar(e, false); });
    root.addEventListener('change', (e) => alCambiar(e, true));
  }
  async function iniciarReglas() {
    if (!IS_COMBINED || MODO !== 'reglas') return;
    try {
      const r = await fetch('/api/reglas', { credentials: 'same-origin' });
      if (!r.ok) return;                       // sin sesion de dueño o trafficker no hay tarjeta
      const j = await r.json();
      if (!j || !j.ok) return;
      reglas = rgDeAlmacen(j.reglas) || [];
      reglasDisponibles = true;
    } catch (e) { return; }
    rgVista = 'reglas';
    const root = el('rg-root'); if (root) rgEscuchas(root);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && rgModal) { const x = document.querySelector('#rg-modal-raiz [data-rg="cerrar-modal"]'); if (x) x.click(); } });
    const logPrueba = rgCargarPrueba();
    // Lo que alguien ya aplico desde otro dispositivo no se debe ofrecer otra vez: se espera el registro compartido un momento antes de pintar las propuestas.
    const carga = rgCargarRegistro();
    await Promise.race([carga, new Promise((r) => setTimeout(r, 2500))]);
    renderReglas(selectedCountries());
    rgVivasPrevias = rgVivasAhora();                  // la foto de las reglas que ya trabajan: de aqui en adelante, una que pase a trabajar pide una revision
    carga.then((cambio) => { if (cambio || rgRegServidor === null) renderReglas(selectedCountries()); });     // si no se pudo leer, se repinta para que Propuestas lo avise
    // El registro del modo prueba trae «ultimas» (cuando se reviso cada regla) y «corridas»: cada tarjeta lo pinta, asi que se repinta cuando llega.
    logPrueba.then(() => { if (rgVista === 'reglas' && !rgModal) rgPintar(); });
  }
