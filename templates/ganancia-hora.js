  // ---------- Ganancia por hora del dia (Dia a dia > Agrupar por > Hora del dia) ----------
  //
  // Este archivo se mete DENTRO del script del dashboard (build-dashboard.ps1 sustituye el marcador __GANANCIA_HORA__): usa SNAPSHOT, state, el, escHtml, svgEl, niceMax, fmtCompact,
  // fmtInt, monedaLocal, ordersMatching, dailySeries... de ahi. Las funciones `gh...` de arriba (el modelo) no tocan la pagina: las prueba scripts/probar-ganancia-hora.cjs.
  //
  // De donde sale cada hora. Facebook y las hojas solo dicen cuanto se gasto y cuanto se vendio EN EL DIA. Pero el dash se reconstruye cada hora y, desde el 21-oct-2026, guarda
  // lo que llevaba «hoy» en cada reconstruccion (data/lecturas-horarias.json, lo arma scripts/lecturas-horarias.cjs; viene en SNAPSHOT.countries[pais].lecturasHora como
  // { 'AAAA-MM-DD': [[minuto del dia, gasto USD acumulado, ventas USD acumuladas, numero de ventas], ...] }). Lo que paso en un rato es la diferencia entre dos lecturas seguidas.
  //
  // Reglas del modelo:
  //  - Entre dos lecturas el aumento se reparte parejo por minuto, y de ahi a cada hora del reloj (las lecturas caen pasadas las :25, no en punto).
  //  - Cada dia se cierra con el TOTAL que el dash ya muestra para ese dia (Dia a dia): de las lecturas sale solo la FORMA del dia (que parte cayo en cada hora) y el total manda. Asi la
  //    suma de las 24 horas es, por construccion, el total de Dia a dia de esos mismos dias; las ventas que se anotaron o el gasto que Meta reporto tarde quedan repartidos en vez de perderse.
  //  - Un acumulado nunca baja: una lectura MENOR que una anterior (Meta a veces corrige hacia abajo; una cuenta caida un rato da un salto raro) se descarta para esa serie, y el aumento se
  //    reparte parejo entre las lecturas buenas que la rodean. Quedarse con el maximo y seguir daria un hueco seguido de un pico en la hora siguiente.
  //  - Un dia entra solo si sus lecturas cubren el dia: la primera antes de las 3:00 y sin huecos de mas de 2,5 h (hoy, hasta el momento en que se armo el tablero), y esto se mide DESPUES de
  //    descartar las lecturas que bajan: una lectura alta que luego se corrige deja a la serie sin lecturas buenas por horas, y repartir ese hueco «parejo» pondria el dia entero antes de la
  //    lectura alta. Los dias que no cubren se dejan fuera y la pantalla dice cuales y por que. Excepcion: si solo los DOLARES de las ventas no cubren (en Argentina, Venezuela y Mexico los
  //    dolares bajan varias veces al dia cuando se mueve la tasa de cambio), la facturacion de ese dia se reparte con la forma del NUMERO de ventas y la nota lo dice.
  //  - Hoy (el dia que no ha terminado) solo entra si es el unico dia: si entrara junto a dias completos, las horas de la mañana sumarian un dia mas que las de la noche y las barras no serian comparables.
  //  - Todo en hora de Colombia: es la hora en que corre el refresco, sea cual sea el pais.
  const GH_PRIMERA_MAX = 180;
  const GH_HUECO_MAX = 150;
  // Va siempre a la vista bajo la frase del grafico (no plegado): la franja que sale de aqui es una guia, no una orden de apagar.
  const GH_AVISO = 'Antes de frenar una franja, compara el día completo con y sin el freno: el gasto de la noche puede vender al día siguiente y Meta reporta el gasto con retraso, así que la franja exacta se corre un poco.';

  function ghHoraTxt(min) {
    const h = Math.floor(min / 60) % 24, m = min % 60, h12 = h % 12 === 0 ? 12 : h % 12;
    return h12 + ':' + String(m).padStart(2, '0') + (h < 12 ? ' a. m.' : ' p. m.');
  }
  function ghFechaCorta(iso) { const p = String(iso).split('-'); return Number(p[2]) + '/' + Number(p[1]); }

  // Las lecturas de un dia, listas para usar: por minuto y sin repetidos (si hay dos en el mismo minuto gana la ultima). Lo que no tenga la forma esperada se descarta.
  function ghAcumulado(lecturas) {
    const porMinuto = new Map();
    (Array.isArray(lecturas) ? lecturas : []).forEach((r) => {
      if (!Array.isArray(r) || r.length < 4) return;
      const m = r[0], g = r[1], v = r[2], n = r[3];
      if (!Number.isInteger(m) || m < 0 || m > 1439) return;
      if (![g, v, n].every((x) => typeof x === 'number' && isFinite(x) && x >= 0)) return;
      porMinuto.set(m, { m, g, v, n });
    });
    return Array.from(porMinuto.values()).sort((a, b) => a.m - b.m);
  }
  // La llave con la que el recolector (scripts/lecturas-ofertas.cjs, nombreValido) guarda una oferta: sin caracteres de control ni espacios de sobra, a lo mas 80 caracteres. La pagina busca con la misma
  // cuenta para que una pestaña con doble espacio no deje a su oferta sin lecturas.
  const ghSepLinea = String.fromCharCode(0x2028, 0x2029);
  const ghLlave = (n) => Array.from(String(n == null ? '' : n).replace(new RegExp('[\\u0000-\\u001f\\u007f' + ghSepLinea + ']', 'g'), ' ').replace(/\s+/g, ' ').trim()).slice(0, 80).join('').trim();

  // Las lecturas de UNA OFERTA un dia, sobre el RELOJ DEL RECOLECTOR DE OFERTAS. minutos = los minutos del dia en que ese recolector corrio en el pais (aunque ninguna oferta tuviera actividad); lecOferta =
  // lo que la oferta llevaba (solo se anota una oferta que ya tiene algo ese dia, asi que antes de su primera lectura valia 0). En cada minuto del reloj se toma lo ultimo que la oferta llevaba hasta entonces:
  // si una corrida no la anoto, se arrastra lo anterior. La cobertura (primera lectura, huecos de mas de 2,5 h) es la de ESE reloj: si el paso por oferta se activo a las 5 p. m. o fallo horas seguidas,
  // el dia sale con su motivo en vez de repartirse como si la oferta no hubiera gastado antes.
  function ghLecturasOferta(minutos, lecOferta) {
    const of = ghAcumulado(lecOferta);
    const mins = Array.from(new Set((Array.isArray(minutos) ? minutos : []).filter((m) => Number.isInteger(m) && m >= 0 && m <= 1439))).sort((a, b) => a - b);
    let j = 0, ult = [0, 0, 0];
    return mins.map((m) => {
      while (j < of.length && of[j].m <= m) { ult = [of[j].g, of[j].v, of[j].n]; j++; }
      return [m, ult[0], ult[1], ult[2]];
    });
  }

  // Una oferta es un pedazo de su pais: en un rato no puede subir MAS que el pais (gasto y numero de ventas). Si sube mas, un anuncio paso a esta oferta (repartoDeAnuncios mueve anuncios durante el dia: desempate
  // por la venta que mas facturo, una pestaña nueva, un renombre) y su acumulado salto de golpe: repartirlo daria un pico falso. lecOf = las lecturas de la oferta ya sobre su reloj; techo = las lecturas del pais
  // ese dia. Los dolares de las ventas no se comparan: bajan y suben con la tasa de cambio. Sin lecturas del pais no hay con que comparar.
  // Se compara SOLO en los minutos en que el pais tambien anoto (los dos recolectores usan el minuto del mismo resumen): si «Lecturas por hora» no empujo una hora, ese minuto no se compara y el tramo se
  // junta con el siguiente (antes la lectura que faltaba sacaba el dia de todas las ofertas con un motivo falso).
  // cierre = { m, of: { g, n }, pa: { g, n } }: lo que la oferta y el pais valen en el tablero (el dia se cierra a esos totales; m = el minuto del tablero si es hoy, o el final del dia). El tramo que va de la
  // ultima lectura a ese total tambien se compara con el del pais: (1) para HOY todo lo que falta cae en el ultimo tramo, asi que una oferta sin lecturas con su nombre (pestaña renombrada, nombre que el recolector
  // no guarda) metia todo su dia en la ultima hora y entraba sin motivo; (2) en un dia pasado, un anuncio que cambio de oferta DESPUES de ese dia hace que el total de hoy no sea el de las lecturas: la que lo
  // gana sube mas que su pais en el cierre, y la que lo pierde queda con un total MENOR que lo que llevaba (el dia sale en los dos casos).
  // Lo que NO se alcanza a ver: un anuncio con poco gasto acumulado (menos de lo que el pais gasta en una hora) que cambia de oferta durante el dia pasa por una subida normal y sale como un pico de esa hora.
  function ghCoherente(lecOf, techo, cierre) {
    const of = ghAcumulado(lecOf), pa = ghAcumulado(techo);
    if (!of.length || !pa.length) return { ok: true };
    const paEn = new Map(pa.map((r) => [r.m, r]));
    let prevO = { g: 0, n: 0 }, prevP = { g: 0, n: 0 };
    const sube = (o, p) => ['g', 'n'].some((c) => o[c] - prevO[c] > Math.max(0, p[c] - prevP[c]) * 1.02 + (c === 'g' ? 0.05 : 0.5));
    for (const r of of) {
      const p = paEn.get(r.m);
      if (!p) continue;
      if (sube(r, p)) return { ok: false, motivo: 'sube más que su país a las ' + ghHoraTxt(r.m) + ' (un anuncio cambió de oferta)' };
      prevO = r; prevP = p;
    }
    if (cierre) {
      if (['g', 'n'].some((c) => cierre.of[c] < prevO[c] - (prevO[c] * 0.02 + (c === 'g' ? 0.05 : 0.5)))) return { ok: false, motivo: 'su total del día es menor que lo que llevaba en las lecturas (un anuncio salió de la oferta o se corrigió)' };
      if (sube(cierre.of, cierre.pa)) return { ok: false, motivo: 'sube más que su país entre su última lectura y el cierre (' + ghHoraTxt(cierre.m) + '): la oferta es nueva o un anuncio cambió de oferta' };
    }
    return { ok: true };
  }

  // Una serie (g, v o n) que nunca baja: las lecturas que bajan respecto a la mejor anterior se descartan.
  function ghSerie(lect, campo) {
    const out = []; let mx = 0;
    lect.forEach((r) => { if (r[campo] >= mx) { out.push({ m: r.m, x: r[campo] }); mx = r[campo]; } });
    return out;
  }

  // Cuanto aumento la serie en cada hora del reloj, repartiendo el aumento de cada tramo (a, b] parejo por minuto.
  function ghReparto(puntos) {
    const horas = new Array(24).fill(0);
    for (let i = 1; i < puntos.length; i++) {
      const a = puntos[i - 1].m, b = puntos[i].m, d = puntos[i].x - puntos[i - 1].x;
      if (!(b > a) || !(d > 0)) continue;
      for (let h = Math.floor(a / 60); h <= Math.min(23, Math.floor((b - 1) / 60)); h++) {
        const sobre = Math.min(b, (h + 1) * 60) - Math.max(a, h * 60);
        if (sobre > 0) horas[h] += d * sobre / (b - a);
      }
    }
    return horas;
  }

  // La serie (ya sin las lecturas que bajan) cubre el dia: la primera antes de las 3:00, sin huecos de mas de 2,5 h y con la ultima a menos de 2,5 h del final (finMin).
  function ghSerieCubre(serie, finMin) {
    if (!serie.length || serie[0].m > GH_PRIMERA_MAX) return false;
    for (let i = 1; i < serie.length; i++) if (serie[i].m - serie[i - 1].m > GH_HUECO_MAX) return false;
    return finMin - serie[serie.length - 1].m <= GH_HUECO_MAX;
  }

  // La forma de UN dia: { ok: true, g: [24], v: [24], n: [24], vDeN } (fracciones que suman 1, o todas 0 si ese dia no tuvo gasto / ventas; vDeN = la facturacion se repartio con la forma del
  // numero de ventas) o { ok: false, motivo }.
  // tot = { g, v, n }: el total del dia que muestra el dash. finMin = hasta donde cubre el dia: 1440 si ya termino, o el minuto en que se armo el tablero si es hoy (esHoy).
  //
  // El cierre del dia. La ultima lectura de un dia que ya paso cae hacia las 23:25, y el total que muestra el dash es posterior (Meta reporta con retraso; la hoja suma ventas tarde).
  // La diferencia no se sabe cuando ocurrio, asi que se parte en dos: lo que cabe en los minutos que faltan para la medianoche al ritmo de la ultima hora va a esos minutos, y el resto se
  // reparte con la misma forma del dia (al normalizar). Poner TODA la diferencia en los ultimos 35 minutos inventaria un pico a las 23:00 cuando lo que paso fue un reporte tardio. Para
  // hoy, en cambio, el tablero mismo es la ultima lectura: lo que hay entre la lectura anterior y el tablero ocurrio en ese rato.
  function ghFormaDia(lecturas, tot, finMin, esHoy) {
    const lect = ghAcumulado(lecturas);
    if (!lect.length) return { ok: false, motivo: 'no hay lecturas de ese día' };
    const ult = lect[lect.length - 1];
    finMin = Math.min(1440, Math.max(ult.m, Math.round(Number(finMin) || 1440)));
    if (lect[0].m > GH_PRIMERA_MAX) return { ok: false, motivo: 'la primera lectura es de las ' + ghHoraTxt(lect[0].m) };
    for (let i = 1; i < lect.length; i++) {
      if (lect[i].m - lect[i - 1].m > GH_HUECO_MAX) return { ok: false, motivo: 'faltan lecturas entre las ' + ghHoraTxt(lect[i - 1].m) + ' y las ' + ghHoraTxt(lect[i].m) };
    }
    if (finMin - ult.m > GH_HUECO_MAX) return { ok: false, motivo: 'faltan lecturas después de las ' + ghHoraTxt(ult.m) };
    const series = { g: ghSerie(lect, 'g'), v: ghSerie(lect, 'v'), n: ghSerie(lect, 'n') };
    if (!ghSerieCubre(series.g, finMin)) return { ok: false, motivo: 'el gasto de ese día baja o se corrige y las lecturas buenas no cubren el día' };
    if (!ghSerieCubre(series.n, finMin)) return { ok: false, motivo: 'el número de ventas de ese día baja o se corrige y las lecturas buenas no cubren el día' };
    const vDeN = !ghSerieCubre(series.v, finMin);
    const out = { ok: true, vDeN };
    const nombres = { g: 'el gasto', v: 'las ventas', n: 'las ventas' };
    for (const campo of ['g', 'v', 'n']) {
      if (campo === 'v' && vDeN) continue;
      const serie = series[campo];
      const pts = (serie[0].m > 0 ? [{ m: 0, x: 0 }] : []).concat(serie);
      const ult2 = pts[pts.length - 1], pen = pts.length > 1 ? pts[pts.length - 2] : { m: 0, x: 0 };
      const extra = Math.max(0, tot[campo] - ult2.x);
      let cola = extra;
      if (!esHoy) {
        const tasa = ult2.m > pen.m ? (ult2.x - pen.x) / (ult2.m - pen.m) : 0;
        cola = Math.min(extra, Math.max(0, tasa) * (finMin - ult2.m));
      }
      pts.push({ m: finMin, x: ult2.x + cola });
      const horas = ghReparto(pts), suma = horas.reduce((s, x) => s + x, 0);
      if (suma > 0) out[campo] = horas.map((x) => x / suma);
      else if (tot[campo] > 0) return { ok: false, motivo: 'las lecturas no muestran cuándo se movieron ' + nombres[campo] };
      else out[campo] = new Array(24).fill(0);
    }
    if (vDeN) {
      if (!out.n.some((x) => x > 0) && tot.v > 0) return { ok: false, motivo: 'las lecturas no muestran cuándo se vendió' };
      out.v = out.n.slice();
    }
    return out;
  }

  // El modelo de varios paises (o de varias ofertas de uno o varios paises: cada entrada es una «unidad», un pais o un pais+oferta). paises = [{ pais, oferta?, techo? (las lecturas del pais, para comprobar que una oferta no sube mas que su pais), techoDias? ({ fecha: { spendUsd, n } }: los totales del pais para cerrar cada dia), lecturas: { fecha: [[...]] }, dias: [{ date, spendUsd, salesUsd, sales, n, hasSpend }] }] con los totales de cada dia tal como los
  // calcula el dash para los filtros elegidos; hoy = 'AAAA-MM-DD' de Colombia; ahoraMin = minuto del dia en que se armo el tablero.
  // Devuelve { horas: [24 x { spendUsd, salesUsd, sales, n }], usados: [{ pais, fecha }], fuera: [{ pais, fecha, motivo }], aprox: [{ pais, fecha }] (dias usados cuya facturacion se repartio con la forma
  // del numero de ventas), desde: primer dia con lecturas }. Un dia sin gasto conectado no entra: su ganancia no se puede calcular (la grafica de Dia a dia tampoco la dibuja). Hoy solo entra si
  // es el unico dia que entra (ver arriba).
  function ghModelo(paises, hoy, ahoraMin) {
    const horas = Array.from({ length: 24 }, () => ({ spendUsd: 0, salesUsd: 0, sales: 0, n: 0 }));
    const usados = [], fuera = [], aprox = [], listos = [];
    let desde = '';
    (paises || []).forEach((p) => {
      Object.keys(p.lecturas || {}).forEach((f) => { if (!desde || f < desde) desde = f; });
      (p.dias || []).forEach((d) => {
        if (!d.hasSpend) { fuera.push({ pais: p.pais, oferta: p.oferta, fecha: d.date, motivo: 'no tiene gasto conectado' }); return; }
        const esHoy = d.date === hoy;
        const forma = ghFormaDia((p.lecturas || {})[d.date], { g: d.spendUsd, v: d.salesUsd, n: d.n }, esHoy ? ahoraMin : 1440, esHoy);
        if (!forma.ok) { fuera.push({ pais: p.pais, oferta: p.oferta, fecha: d.date, motivo: forma.motivo }); return; }
        if (p.techo) {
          const td = (p.techoDias || {})[d.date];
          const co = ghCoherente((p.lecturas || {})[d.date], p.techo[d.date], td ? { m: esHoy ? Math.min(1439, Math.round(ahoraMin)) : 1439, of: { g: d.spendUsd, n: d.n }, pa: { g: td.spendUsd, n: td.n } } : null);
          if (!co.ok) { fuera.push({ pais: p.pais, oferta: p.oferta, fecha: d.date, motivo: co.motivo }); return; }
        }
        listos.push({ p, d, forma, esHoy });
      });
    });
    const hayOtro = listos.some((x) => !x.esHoy);
    listos.forEach(({ p, d, forma, esHoy }) => {
      if (esHoy && hayOtro) { fuera.push({ pais: p.pais, oferta: p.oferta, fecha: d.date, motivo: 'el día de hoy no ha terminado' }); return; }
      for (let h = 0; h < 24; h++) {
        horas[h].spendUsd += d.spendUsd * forma.g[h];
        horas[h].salesUsd += d.salesUsd * forma.v[h];
        horas[h].sales += (d.sales || 0) * forma.v[h];
        horas[h].n += d.n * forma.n[h];
      }
      usados.push({ pais: p.pais, oferta: p.oferta, fecha: d.date });
      if (forma.vDeN) aprox.push({ pais: p.pais, oferta: p.oferta, fecha: d.date });
    });
    return { horas, usados, fuera, aprox, desde };
  }

  // El tramo seguido de horas que mas suma (signo = 1) o que mas resta (signo = -1) sobre una lista de 24 valores. null si ningun tramo tiene el signo pedido.
  function ghTramo(valores, signo) {
    let mejor = null, ini = 0, acum = 0;
    for (let h = 0; h < valores.length; h++) {
      const v = signo * valores[h];
      if (acum <= 0) { ini = h; acum = v; } else acum += v;
      if (acum > 0 && (!mejor || acum > mejor.suma)) mejor = { desde: ini, hasta: h + 1, suma: acum };
    }
    return mejor ? { desde: mejor.desde, hasta: mejor.hasta, suma: mejor.suma * signo } : null;
  }

  // Las notas: { corta, larga }. La corta va arriba de la tarjeta (de donde sale y si son pocos dias); la larga, plegada bajo el grafico, dice cuales dias no entraron y por que, si la suma
  // cuadra con «Dia a dia» y que es la forma del dia y no la hora exacta.
  const ghPaisTxt = (p) => String(p).charAt(0).toUpperCase() + String(p).slice(1);
  // Como se nombra una unidad en la nota: el pais, o la oferta (con su pais si hay mas de un pais). La oferta pasa por etiquetaOferta, que en la pagina del socio la muestra como «Oferta N».
  const ghOfertaTxt = (o) => (typeof etiquetaOferta === 'function' ? etiquetaOferta(o) : String(o));
  function ghNota(modelo, hoy, etiqueta) {
    const todos = modelo.usados.concat(modelo.fuera);
    const usados = Array.from(new Set(modelo.usados.map((x) => x.fecha)));
    const paisesTodos = new Set(todos.map((x) => x.pais));
    const hayOfertas = todos.some((x) => x.oferta);
    const unidades = new Set(todos.map((x) => x.pais + '|' + (x.oferta || '')));
    const multi = unidades.size > 1;
    const nombre = (x) => (x.oferta ? (paisesTodos.size > 1 ? ghPaisTxt(x.pais) + ' · ' : '') + ghOfertaTxt(x.oferta) : ghPaisTxt(x.pais));
    const n = usados.length, hoyEntra = usados.indexOf(hoy) >= 0;
    let corta = 'Hora de Colombia · ' + (etiqueta ? etiqueta + ' · ' : '') + 'suma de ' + (n === 1 ? '1 día' : n + ' días') + (hoyEntra ? ' (con lo que va de hoy)' : '') +
      (multi && !etiqueta ? ' de ' + (hayOfertas ? unidades.size + ' ofertas' : paisesTodos.size + ' países') : '') + (etiqueta && paisesTodos.size > 1 ? ' en ' + paisesTodos.size + ' países' : '') + '.';
    if (n < 3) corta += ' Con tan pocos días la forma puede ser casualidad: mira varios días antes de decidir.';
    // Las unidades (pais u oferta) que no entran un dia que SI entra para otra: el total ya no es el de «Dia a dia» para ese dia.
    const fechasUsadas = new Set(usados);
    const parciales = modelo.fuera.filter((f) => fechasUsadas.has(f.fecha));
    let larga = 'Sale de las lecturas que el dash toma cada hora (gasto y ventas de «hoy»' + (hayOfertas ? ', y lo que llevaba cada oferta' : '') + '): lo que pasó en cada rato es la diferencia entre dos lecturas seguidas. Cada día se cierra con el total que ya muestra «Día a día»' +
      (parciales.length ? ', pero con ' + (hayOfertas ? 'varias ofertas o países' : 'varios países') + ' hay días que entran para unos y no para otros (abajo), así que este total queda por debajo del de «Día a día» en esos días.' : ', así que la suma de las 24 horas es la de «Día a día» para esos mismos días.');
    // Los dias anteriores a la primera lectura no se pueden repartir y son la mayoria del rango; se dicen en una frase en vez de listarlos uno por uno.
    const antes = modelo.fuera.filter((f) => modelo.desde && f.fecha < modelo.desde);
    const resto = modelo.fuera.filter((f) => !(modelo.desde && f.fecha < modelo.desde));
    if (antes.length) larga += ' Los días anteriores al ' + ghFechaCorta(modelo.desde) + ' no entran: no hay lecturas por hora de antes' + (hayOfertas ? ' (de cada oferta solo se guardan las de los últimos 14 días)' : '') + '.';
    if (resto.length) {
      const motivos = new Map();
      resto.forEach((f) => { if (!motivos.has(f.motivo)) motivos.set(f.motivo, []); motivos.get(f.motivo).push(ghFechaCorta(f.fecha) + (multi ? ' ' + nombre(f) : '')); });
      const partes = [];
      motivos.forEach((fs, k) => { const u = Array.from(new Set(fs)); partes.push(u.slice(0, 4).join(', ') + (u.length > 4 ? ' y ' + (u.length - 4) + ' más' : '') + ' (' + k + ')'); });
      larga += ' No entran: ' + partes.slice(0, 3).join('; ') + (partes.length > 3 ? '…' : '') + '.';
    }
    if ((modelo.aprox || []).length) {
      const u = modelo.aprox.map((x) => ghFechaCorta(x.fecha) + (multi ? ' ' + nombre(x) : ''));
      larga += ' En ' + u.slice(0, 4).join(', ') + (u.length > 4 ? ' y ' + (u.length - 4) + ' más' : '') + ' la facturación por hora se repartió según el número de ventas, porque los dólares de esos días suben y bajan (se mueve la tasa de cambio o se corrigió una venta).';
    }
    if (hayOfertas) larga += ' El gasto de una oferta es el que el dash le reparte por el nombre de la campaña (cada anuncio cuenta en una sola oferta). Si un anuncio pasa de una oferta a otra, el día queda fuera para las dos cuando el cambio se nota (arriba, con su motivo): la que lo gana sube más que su país en ese rato, y la que lo pierde queda con un total menor que lo que llevaba. Un anuncio con poco gasto acumulado —menos de lo que todo el país gasta en una hora— que cambia de oferta durante el día no se alcanza a notar y sale como un pico de esa hora.';
    larga += ' Cada hora es lo que el dash fue viendo durante el día —Meta reporta con retraso y la venta se anota cuando llega a la hoja—, así que muestra la forma del día, no la hora exacta del anuncio ni de la compra. Si Facebook o la hoja no se actualizan unas horas, lo acumulado aparece todo junto en la hora en que se ponen al día. Ganancia = facturación − gasto en publicidad, sin los costos de la operación.';
    return { corta, larga };
  }

  // Lo que se pinta de cada hora en la moneda elegida. mon = monedaDia() (v: facturacion, g: gasto, din: texto de plata).
  function ghFilas(modelo, mon) {
    return modelo.horas.map((x, h) => {
      const v = mon.v(x), g = mon.g(x);
      return { h, v, g, gan: v - g, roas: x.spendUsd > 0 ? x.salesUsd / x.spendUsd : null, n: x.n, spendUsd: x.spendUsd, salesUsd: x.salesUsd };
    });
  }
  function ghTotales(filas) {
    const t = filas.reduce((a, f) => { a.v += f.v; a.g += f.g; a.n += f.n; a.spendUsd += f.spendUsd; a.salesUsd += f.salesUsd; return a; }, { v: 0, g: 0, n: 0, spendUsd: 0, salesUsd: 0 });
    t.gan = t.v - t.g; t.roas = t.spendUsd > 0 ? t.salesUsd / t.spendUsd : null;
    return t;
  }
  const ghRango = (t) => (t.desde < 10 ? '0' : '') + t.desde + ':00 a ' + (t.hasta < 10 ? '0' : '') + t.hasta + ':00';

  // La frase que lee el dueño: el peor y el mejor tramo del dia, con lo que pesan.
  function ghLectura(filas, din) {
    const gan = filas.map((f) => f.gan), tot = ghTotales(filas);
    if (!(tot.g > 0) && !(tot.v > 0)) return '';
    const peor = ghTramo(gan, -1), mejor = ghTramo(gan, 1);
    const gastoEn = (t) => filas.slice(t.desde, t.hasta).reduce((s, f) => s + f.g, 0);
    const partes = [];
    if (mejor) partes.push('Donde más se gana: de ' + ghRango(mejor) + ' (' + din(mejor.suma) + ').');
    if (peor) partes.push('Donde se pierde: de ' + ghRango(peor) + ' (' + din(peor.suma) + (tot.g > 0 ? ', el ' + Math.round(gastoEn(peor) / tot.g * 100) + ' % del gasto' : '') + ').');
    else partes.push('Ninguna franja pierde plata.');
    partes.push('En total: ' + din(tot.gan) + (tot.roas != null ? ' · ROAS ' + tot.roas.toFixed(2) + 'x' : '') + '.');
    return partes.join(' ');
  }

  // Tabla por hora (cabecera, cuerpo y pie en HTML). din = texto de plata de la moneda elegida.
  function ghTabla(filas, din) {
    const tot = ghTotales(filas);
    const cls = (v) => (v >= 0 ? 'profit-positive' : 'profit-negative');
    const roas = (f) => (f.roas == null ? '—' : '<span class="pill ' + (f.roas >= 1.5 ? 'good' : f.roas >= 1 ? 'warn' : 'danger') + '">' + f.roas.toFixed(2) + 'x</span>');
    const cpa = (f) => (f.n >= 0.5 ? din(f.g / f.n) : '—');
    const fila = (rot, f) => '<tr><td>' + rot + '</td><td class="num">' + din(f.v) + '</td><td class="num">' + din(f.g) + '</td><td class="num ' + cls(f.gan) + '">' + din(f.gan) + '</td><td class="num">' + roas(f) +
      '</td><td class="num">' + fmtInt(f.n) + '</td><td class="num">' + cpa(f) + '</td></tr>';
    return {
      head: '<tr><th>Hora</th><th class="num">Facturación</th><th class="num">Gasto</th><th class="num">Ganancia</th><th class="num">ROAS</th><th class="num">Ventas</th><th class="num">Costo/venta</th></tr>',
      body: filas.map((f) => fila(String(f.h).padStart(2, '0') + ':00', f)).join(''),
      foot: fila('Total', tot),
    };
  }
  function ghCsv(filas, cm) {
    return [['hora', 'facturacion_' + cm, 'gasto_' + cm, 'ganancia_' + cm, 'roas', 'ventas']].concat(filas.map((f) => [f.h, f.v.toFixed(2), f.g.toFixed(2), f.gan.toFixed(2), f.roas == null ? '' : f.roas.toFixed(2), f.n.toFixed(1)]));
  }

  // El grafico de barras por hora: ganancia (verde sobre el cero, rojo debajo) o ROAS (verde desde 1x, rojo por debajo). Se toca o se pasa el cursor por una hora para ver sus cifras.
  function ghDibujar(svg, tooltip, filas, modo, din) {
    svg.innerHTML = '';
    tooltip.style.opacity = 0;
    const anchoDe = (n) => (n ? Math.round(n.getBoundingClientRect().width || n.clientWidth || 0) : 0);
    const W = Math.max(320, anchoDe(svg) || anchoDe(svg.parentElement) || 900), H = 240;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    const padL = 52, padR = 10, padT = 16, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const L = monedaLocal();
    const esRoas = modo === 'roas';
    const val = (f) => (esRoas ? (f.roas == null ? 1 : f.roas) : f.gan);   // una hora sin gasto no tiene ROAS: se deja en el equilibrio (sin barra), no se pinta como 0x
    const vals = filas.map(val);
    let techo, suelo, paso;
    if (esRoas) {
      techo = niceMax(Math.max.apply(null, vals.concat([1.2]))); suelo = 0; paso = techo / 4;
    } else {
      paso = Math.max(niceMax(Math.max.apply(null, vals.map((v) => Math.abs(v)).concat([1]))) / 2, 1e-6);
      techo = Math.max(Math.ceil(Math.max.apply(null, vals.concat([0])) / paso), 1) * paso;
      suelo = Math.min(Math.floor(Math.min.apply(null, vals.concat([0])) / paso), 0) * paso;
    }
    const span = (techo - suelo) || 1;
    const slot = plotW / 24, bw = Math.max(3, slot * 0.7);
    const x = (h) => padL + slot * h + slot / 2;
    const y = (v) => padT + plotH - ((v - suelo) / span) * plotH;
    const base = esRoas ? 1 : 0;
    for (let v = suelo; v <= techo + paso / 2; v += paso) {
      const gy = y(v);
      svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: gy, y2: gy, class: 'gridline' }));
      const et = svgEl('text', { x: padL - 8, y: gy + 3, 'text-anchor': 'end', class: 'axis-label' });
      et.textContent = esRoas ? v.toFixed(1) + 'x' : (v < -1e-9 ? '-' : '') + (L ? '' : '$') + fmtCompact(Math.abs(v));
      svg.appendChild(et);
    }
    if (esRoas) {
      const y1 = y(1);
      svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: y1, y2: y1, stroke: 'var(--critical)', 'stroke-width': 1.5, 'stroke-dasharray': '5 4', opacity: 0.9 }));
      const et = svgEl('text', { x: W - padR - 4, y: y1 - 5, 'text-anchor': 'end', class: 'axis-label' });
      et.setAttribute('fill', 'var(--critical)'); et.textContent = 'equilibrio 1x'; svg.appendChild(et);
    } else {
      svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: y(0), y2: y(0), class: 'baseline' }));
    }
    const barras = filas.map((f, h) => {
      const v = val(f), arriba = v >= base, y0 = y(base), y1 = y(v);
      const alto = Math.max(Math.abs(y1 - y0), v === base ? 0 : 1.5);
      const r = svgEl('rect', { x: x(h) - bw / 2, y: arriba ? y0 - alto : y0, width: bw, height: alto, rx: 2, fill: arriba ? 'var(--good)' : 'var(--critical)', opacity: 0.88 });
      svg.appendChild(r); return r;
    });
    // Horas del eje: una de cada tres, para que quepan en el celular.
    for (let h = 0; h < 24; h += 3) {
      const et = svgEl('text', { x: x(h), y: H - 8, 'text-anchor': 'middle', class: 'axis-label' });
      et.textContent = String(h).padStart(2, '0'); svg.appendChild(et);
    }
    const guia = svgEl('line', { x1: 0, x2: 0, y1: padT, y2: H - padB, class: 'crosshair' });
    svg.appendChild(guia);
    const capa = svgEl('rect', { x: padL, y: padT, width: plotW, height: plotH, fill: 'transparent' });
    svg.appendChild(capa);
    const idxDe = (e) => { const rc = svg.getBoundingClientRect(); const px = ((e.clientX - rc.left) / rc.width) * W; return Math.max(0, Math.min(23, Math.floor((px - padL) / slot))); };
    function mostrar(e) {
      const h = idxDe(e), f = filas[h];
      guia.setAttribute('x1', x(h)); guia.setAttribute('x2', x(h)); guia.style.opacity = 1;
      barras.forEach((b, i) => b.setAttribute('opacity', i === h ? 1 : 0.88));
      tooltip.innerHTML = '';
      const cab = document.createElement('div'); cab.className = 't-date'; cab.textContent = String(h).padStart(2, '0') + ':00 a ' + String(h).padStart(2, '0') + ':59';
      tooltip.appendChild(cab);
      [['Facturación', din(f.v), 'var(--series-sales)'], ['Gasto', din(f.g), 'var(--series-spend)'], ['Ganancia', din(f.gan), f.gan >= 0 ? 'var(--good)' : 'var(--critical)'],
        ['ROAS', f.roas == null ? '—' : f.roas.toFixed(2) + 'x', 'var(--series-roas)'], ['Ventas', fmtInt(f.n), 'var(--text-muted)']].forEach(([rot, txt, color]) => {
        const fila = document.createElement('div'); fila.className = 't-row';
        const k = document.createElement('span'); k.className = 'key'; k.style.background = color;
        const a = document.createElement('span'); a.textContent = rot;
        const b = document.createElement('span'); b.className = 'val'; b.textContent = txt;
        fila.appendChild(k); fila.appendChild(a); fila.appendChild(b); tooltip.appendChild(fila);
      });
      const rc = svg.getBoundingClientRect(), esc = rc.width / W;
      tooltip.style.opacity = 1;
      const ancho = tooltip.offsetWidth || 150, alto = tooltip.offsetHeight || 90;
      tooltip.style.left = Math.max(ancho / 2 + 4, Math.min(x(h) * esc, rc.width - ancho / 2 - 4)) + 'px';
      const tope = Math.min(y(val(f)), y(base)) * (rc.height / H);
      tooltip.style.transform = tope > alto + 8 ? 'translate(-50%, -110%)' : 'translate(-50%, 14%)';
      tooltip.style.top = tope + 'px';
    }
    capa.addEventListener('pointermove', mostrar);
    capa.addEventListener('pointerdown', mostrar);
    capa.addEventListener('pointerleave', (e) => {
      if (e.pointerType && e.pointerType !== 'mouse') return;   // en el celular el globo se queda hasta tocar otra hora
      tooltip.style.opacity = 0; guia.style.opacity = 0; barras.forEach((b) => b.setAttribute('opacity', 0.88));
    });
  }
