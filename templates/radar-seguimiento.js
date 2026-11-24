// Analisis del seguimiento de una oferta vigilada: de las «fotos» diarias que manda el radar (functions/api/radar-seguimiento.js) saca la tendencia, los sobrevivientes por edad,
// siete pruebas y un veredicto («vale la pena modelarla», «sigue vigilandola» o «conviene descartarla»).
//
// Vive en su propio archivo, SIN tocar el DOM, por dos razones: se prueba en Node con fotos inventadas (scripts/probar-seguimiento.cjs) y se mete en /radar al armar la pagina
// (Build-Suite.ps1 lo pega en la plantilla de /radar en lugar de su marcador; queda como `RadarSeguimiento`). Todo son numeros ya validados por el servidor; el texto que arma aqui nunca
// trae nada de terceros (ni nombres ni copy de anuncios), asi que quien lo pinta puede ponerlo como esta... y aun asi la pagina lo escapa.
//
// De donde salen los criterios (UMBRALES): los filtros propios del radar (8 anuncios activos, 6 piezas distintas, 30 dias en el aire) y las reglas practicas que repiten las guias de
// espionaje de la Biblioteca de Anuncios: un anuncio que sigue activo a las 3-4 semanas es uno que su dueño no apago porque le vende; quien sigue lanzando creativos nuevos esta
// probando y escalando; quien apaga muchos de golpe esta perdiendo. Son SENALES PUBLICAS: la Biblioteca no dice gasto, ventas ni resultados, asi que sirven para decidir que probar,
// no para asegurar que venda. La prueba real es el testeo.
(function (raiz, fabrica) {
  var M = fabrica();
  if (typeof module === 'object' && module && module.exports) module.exports = M; else raiz.RadarSeguimiento = M;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var UMBRALES = {
    minActivos: 8,            // los que pide el radar para registrar una oferta (MIN_ACTIVE)
    pocosActivos: 5,          // por debajo de esto la oferta ya no tiene volumen
    minPiezas: 6,             // piezas distintas (MIN_DISTINCT del radar)
    pocasPiezas: 3,
    aguantan30: 3,            // anuncios que superan 30 dias para decir que aguantan
    nuevosRitmo: 2,           // lanzamientos en 7 dias para decir que sigue probando
    sube: 15,                 // % de cambio en ~7 dias: de aqui para arriba crece
    baja: -15,                // y de aqui para abajo se apaga...
    cae: -40,                 // ...y de aqui para abajo se desploma
    apagaPoco: 0.25,          // apagados en 7 dias / anuncios visibles: hasta aqui es rotacion normal
    apagaMucho: 0.5,
    diasParaDecidir: 7,       // dias de seguimiento antes de afirmar «modelala»
    diasParaDescartar: 3,     // dias para descartar por pocos anuncios, 5 por caida
    pagina1: 30               // la pagina 1 de la Biblioteca trae hasta 30 anuncios: si trae menos, los trae todos
  };

  function num(v) { if (v === null || v === undefined || v === '') return null; var n = Number(v); return isFinite(n) ? n : null; }
  function ms(d) { return Date.parse(String(d) + 'T00:00:00Z'); }
  // Dias entre dos AAAA-MM-DD (d2 - d1).
  function dif(d1, d2) { return Math.round((ms(d2) - ms(d1)) / 86400000); }
  // Un dia que existe de verdad: V8 acepta 2026-02-31 y lo corre a marzo, asi que se compara ida y vuelta.
  function esDia(d) { return /^\d{4}-\d{2}-\d{2}$/.test(String(d)) && isFinite(ms(d)) && new Date(ms(d)).toISOString().slice(0, 10) === String(d); }
  function entero(n) { return Math.round(n); }
  function noNeg(n) { return n < 0 ? 0 : n; }

  // Las fotos de una oferta: ordenadas por dia, una por dia (si hay dos del mismo dia gana la ultima), solo las que traen dia y total.
  function serie(fotos) {
    var por = {};
    (Array.isArray(fotos) ? fotos : []).forEach(function (f) {
      if (!f || typeof f !== 'object' || !esDia(f.d) || num(f.a) === null) return;
      por[f.d] = {
        d: f.d, a: num(f.a), s0: num(f.s0), s7: num(f.s7), s30: num(f.s30), s60: num(f.s60), p: num(f.p), vd: num(f.vd), wa: num(f.wa),
        v: num(f.v), lk: num(f.lk), vis: num(f.vis), k: num(f.k), nv: num(f.nv), pd: esDia(f.pd) ? f.pd : '', top: /^\d{5,25}$/.test(String(f.top || '')) ? String(f.top) : '',
        pn: typeof f.pn === 'string' ? f.pn.slice(0, 80) : '', pg: /^\d{5,25}$/.test(String(f.pg || '')) ? String(f.pg) : '', hs: typeof f.hs === 'string' && /^[a-z0-9][a-z0-9.-]{2,59}$/.test(f.hs) ? f.hs : ''
      };
    });
    return Object.keys(por).sort().map(function (d) { return por[d]; });
  }

  // Edad de los anuncios de HOY: los que empezaron hace menos de 7 dias, de 8 a 30, de 31 a 60 y de mas de 60. Sale de los «sobrevivientes» (empezaron antes de hace 7/30/60 dias),
  // asi que existe desde el primer dia de seguimiento. null si la foto no los trae.
  function edad(f) {
    if (!f || f.s7 === null || f.s30 === null || f.s60 === null) return null;
    return { nuevos: noNeg(f.a - f.s7), d8a30: noNeg(f.s7 - f.s30), d31a60: noNeg(f.s30 - f.s60), mas60: f.s60 };
  }

  // Cuanto cambio el total de anuncios activos: contra la foto de hace 7 dias (o la mas cercana que haya si lleva menos, pero al menos 3 dias).
  function cambio(s) {
    if (s.length < 2) return null;
    var ult = s[s.length - 1], base = null, i;
    for (i = s.length - 2; i >= 0; i--) if (dif(s[i].d, ult.d) >= 7) { base = s[i]; break; }
    if (!base && dif(s[0].d, ult.d) >= 3) base = s[0];
    if (!base) return null;
    var pct = base.a > 0 ? entero((ult.a - base.a) / base.a * 100) : (ult.a > 0 ? 100 : 0);
    return { desde: base.d, de: base.a, a: ult.a, dias: dif(base.d, ult.d), pct: pct };
  }
  function tendencia(c) {
    if (!c) return 'sd';
    return c.pct >= UMBRALES.sube ? 'sube' : c.pct <= UMBRALES.cae ? 'cae' : c.pct <= UMBRALES.baja ? 'baja' : 'estable';
  }

  // Anuncios apagados en los ultimos 7 dias, de las fotos que traen k (lo que ya no esta respecto a la foto anterior). Es exacto cuando la pagina 1 mostro todos los anuncios (menos de 30);
  // con mas, son los del tope de la pagina 1 por impresiones: justo los que mas importan, pero no todos.
  function retirados(s) {
    if (s.length < 2) return null;
    var ult = s[s.length - 1], ven = s.filter(function (f) { return f.k !== null && dif(f.d, ult.d) < 7; });
    if (!ven.length) return null;
    var suma = 0, exacto = true;
    ven.forEach(function (f) { suma += f.k; if (f.vis === null || f.vis >= UMBRALES.pagina1) exacto = false; });
    var base = ven[0].vis !== null ? ven[0].vis : ult.a;
    var dias = dif(ven[0].d, ult.d) + 1;
    return { suma: suma, dias: dias, fotos: ven.length, exacto: exacto, ratio: suma / Math.max(1, base) };
  }

  // Dias seguidos (hasta hoy) con al menos `min` anuncios activos.
  function racha(s, min) {
    var n = 0, i;
    for (i = s.length - 1; i >= 0; i--) { if (s[i].a >= min) n++; else break; }
    return n;
  }

  function plural(n, uno, varios) { return n + ' ' + (n === 1 ? uno : varios); }

  // ---- de quien son los numeros: la pagina que el radar midio contra lo que dice la planilla ----
  // Sin tildes ni signos, en minusculas (las marcas combinantes se arman por codigo: un caracter invisible en un patron se corrompe sin hacer ruido).
  var MARCAS = new RegExp('[' + String.fromCharCode(768) + '-' + String.fromCharCode(879) + ']', 'g');
  function plano(s) { return String(s == null ? '' : s).normalize('NFD').replace(MARCAS, '').toLowerCase().replace(/[^a-z0-9]+/g, ''); }
  function palabras(s) { return (String(s == null ? '' : s).normalize('NFD').replace(MARCAS, '').toLowerCase().match(/[a-z0-9]{5,}/g) || []); }
  // ¿El nombre de la pagina medida se parece al anunciante de la planilla? Igual, uno dentro del otro, o una palabra de 5 letras o mas en comun. Sin nombre de alguno de los dos no hay con que comparar.
  function nombresParecidos(pagina, anunciante) {
    var a = plano(pagina), b = plano(anunciante);
    if (!a || !b) return true;
    if (a === b || a.indexOf(b) >= 0 || b.indexOf(a) >= 0) return true;
    var pa = palabras(pagina), pb = palabras(anunciante), i;
    for (i = 0; i < pa.length; i++) if (pb.indexOf(pa[i]) >= 0) return true;
    return false;
  }
  // ¿Los anuncios de la pagina apuntan al mismo sitio que la fila de la planilla? (el dominio de la planilla puede traer ruta o «www.»)
  function mismoDestino(host, dominio) {
    var h = String(host || '').toLowerCase().replace(/^www\./, ''), d = String(dominio || '').toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '').split(/[\/\s(]/)[0];
    if (!h || !d) return false;
    return h === d || h.slice(-(d.length + 1)) === '.' + d || d.slice(-(h.length + 1)) === '.' + h;
  }
  // true = dudoso: el nombre no se parece Y el destino de sus anuncios no es el de la planilla. No bloquea nada: el panel solo pide confirmar que es la pagina correcta.
  function paginaDudosa(pagina, anunciante, dominio) {
    if (!pagina || !pagina.nombre) return false;
    return !nombresParecidos(pagina.nombre, anunciante) && !mismoDestino(pagina.host, dominio);
  }

  // Las pruebas: cada una responde una pregunta con 'si', 'ojo', 'no' o 'sd' (sin datos todavia) y dice con que numero y por que.
  function pruebas(s, ctx) {
    var hoy = s[s.length - 1], ed = edad(hoy), cb = cambio(s), rt = retirados(s), out = [], U = UMBRALES;
    // 1. volumen
    var a = hoy.a;
    out.push({ id: 'volumen', titulo: '¿Tiene volumen?', estado: a >= U.minActivos ? 'si' : a >= U.pocosActivos ? 'ojo' : 'no',
      valor: plural(a, 'anuncio activo', 'anuncios activos'),
      detalle: 'El radar la registró con ' + U.minActivos + ' o más. Con menos de ' + U.pocosActivos + ' ya casi no tiene qué estudiar ni modelar.' });
    // 2. direccion
    if (!cb) {
      out.push({ id: 'direccion', titulo: '¿Crece o se apaga?', estado: 'sd', valor: 'Falta historial',
        detalle: 'Se ve desde el tercer día de seguimiento; la comparación completa, a los 7 días.' });
    } else {
      var t = tendencia(cb);
      out.push({ id: 'direccion', titulo: '¿Crece o se apaga?', estado: t === 'sube' || t === 'estable' ? 'si' : t === 'baja' ? 'ojo' : 'no',
        valor: (cb.pct > 0 ? '+' : '') + cb.pct + ' % en ' + plural(cb.dias, 'día', 'días') + ' (' + cb.de + ' → ' + cb.a + ')',
        detalle: t === 'sube' ? 'Está sumando anuncios: señal de que le funciona.' : t === 'estable' ? 'Se mantiene: no la están apagando ni la están escalando.'
          : t === 'baja' ? 'Está apagando anuncios. Puede ser una limpieza o que ya no rinde: mírala unos días más.' : 'Está apagando casi todo: lo normal es que ya no le rinda.' });
    }
    // 3. resistencia
    if (hoy.s30 === null) {
      out.push({ id: 'resistencia', titulo: '¿Sus anuncios aguantan?', estado: 'sd', valor: 'Sin dato', detalle: 'La foto de hoy no trajo la edad de los anuncios.' });
    } else {
      var pct30 = a > 0 ? entero(hoy.s30 / a * 100) : 0;
      out.push({ id: 'resistencia', titulo: '¿Sus anuncios aguantan?', estado: hoy.s30 >= U.aguantan30 ? 'si' : hoy.s30 >= 1 ? 'ojo' : 'no',
        valor: plural(hoy.s30, 'anuncio con más de 30 días', 'anuncios con más de 30 días') + (a > 0 ? ' (' + pct30 + ' %)' : ''),
        detalle: 'Quien no apaga un anuncio en 30 días casi siempre es porque le vende. Con ' + U.aguantan30 + ' o más, la señal es fuerte.' + (hoy.s60 ? ' Además ' + plural(hoy.s60, 'lleva', 'llevan') + ' más de 60 días.' : '') });
    }
    // 4. ritmo
    if (!ed) {
      out.push({ id: 'ritmo', titulo: '¿Sigue probando creativos nuevos?', estado: 'sd', valor: 'Sin dato', detalle: 'La foto de hoy no trajo la edad de los anuncios.' });
    } else {
      out.push({ id: 'ritmo', titulo: '¿Sigue probando creativos nuevos?', estado: ed.nuevos >= U.nuevosRitmo ? 'si' : 'ojo',
        valor: plural(ed.nuevos, 'anuncio lanzado', 'anuncios lanzados') + ' esta semana',
        detalle: ed.nuevos >= U.nuevosRitmo ? 'Lanza creativos nuevos con frecuencia: está iterando sobre algo que ya le funciona.'
          : ed.nuevos === 1 ? 'Casi no lanza nada nuevo.' : 'No ha lanzado nada nuevo en 7 días: o dejó de invertir o vive de los anuncios ganadores de antes.' });
    }
    // 5. variedad
    if (hoy.p === null) {
      out.push({ id: 'variedad', titulo: '¿Tiene varios creativos distintos?', estado: 'sd', valor: 'Sin dato', detalle: '' });
    } else {
      out.push({ id: 'variedad', titulo: '¿Tiene varios creativos distintos?', estado: hoy.p >= U.minPiezas ? 'si' : hoy.p >= U.pocasPiezas ? 'ojo' : 'no',
        valor: plural(hoy.p, 'pieza distinta', 'piezas distintas') + (hoy.vis !== null && hoy.vis >= U.pagina1 ? ' (de sus ' + U.pagina1 + ' más vistos)' : ''),
        detalle: 'Con ' + U.minPiezas + ' o más hay material suficiente para estudiar qué ángulo gana. Con 1 o 2 es el mismo anuncio repetido.' });
    }
    // 6. estabilidad
    if (!rt) {
      out.push({ id: 'estabilidad', titulo: '¿Apaga muchos anuncios?', estado: 'sd', valor: 'Falta historial', detalle: 'Se ve desde el segundo día: compara los anuncios de hoy con los de ayer.' });
    } else {
      out.push({ id: 'estabilidad', titulo: '¿Apaga muchos anuncios?', estado: rt.ratio <= U.apagaPoco ? 'si' : rt.ratio <= U.apagaMucho ? 'ojo' : 'no',
        valor: plural(rt.suma, 'apagado', 'apagados') + ' en ' + plural(rt.dias, 'día', 'días'),
        detalle: 'Apagar algunos es normal (es como se descartan los que no venden). Apagar la mitad de golpe es mala señal.' + (rt.exacto ? '' : ' Se cuenta entre los 30 anuncios que más impresiones tienen.') });
    }
    // 7. Colombia (lo dice la planilla; no sale de las fotos)
    var col = ctx && ctx.colombia;
    out.push({ id: 'colombia', titulo: '¿Hay hueco en Colombia?',
      estado: col === 'libre' ? 'si' : col === 'poca' ? 'ojo' : col === 'saturada' ? 'no' : 'sd',
      valor: col === 'libre' ? 'Nadie la vende allá' : col === 'poca' ? '1 o 2 anunciantes' : col === 'saturada' ? '3 o más anunciantes' : 'Sin validar',
      detalle: 'Es lo que dice la planilla. Con mucha competencia en Colombia, aunque la oferta funcione en Brasil, allá ya está quemada.' });
    // Lo que se dice en el veredicto cuando una prueba sale en «ojo» o «no». Es una frase propia (no el titulo de la pregunta): «¿Sigue probando creativos nuevos?» con 0 lanzamientos
    // no puede leerse como «Sigue probando creativos nuevos: 0».
    var P = {
      volumen: 'Tiene pocos anuncios activos (' + hoy.a + ')',
      direccion: cb ? 'Está apagando anuncios (de ' + cb.de + ' a ' + cb.a + ' en ' + plural(cb.dias, 'día', 'días') + ')' : '',
      resistencia: 'Pocos de sus anuncios aguantan más de 30 días (' + (hoy.s30 === null ? 0 : hoy.s30) + ')',
      ritmo: ed && ed.nuevos ? 'Casi no lanza creativos nuevos (' + ed.nuevos + ' en 7 días)' : 'No ha lanzado creativos nuevos en 7 días',
      variedad: 'Tiene pocos creativos distintos (' + (hoy.p === null ? 0 : hoy.p) + ')',
      estabilidad: rt ? 'Apaga muchos anuncios (' + rt.suma + ' en ' + plural(rt.dias, 'día', 'días') + ')' : ''
    };
    out.forEach(function (p) { if (P[p.id]) p.problema = P[p.id]; });
    return out;
  }

  function dePrueba(ps, id) { for (var i = 0; i < ps.length; i++) if (ps[i].id === id) return ps[i]; return null; }

  // El veredicto. No es una orden: es lo que dicen los numeros, con la razon a la vista.
  function veredicto(s, ps, ctx) {
    var U = UMBRALES;
    if (!s.length) return { nivel: 'sd', titulo: 'Todavía sin datos', resumen: 'El radar aún no ha tomado la primera foto de esta oferta.', dias: 0, faltan: U.diasParaDecidir };
    var hoy = s[s.length - 1], dias = dif(s[0].d, hoy.d) + 1;
    var claves = ['volumen', 'direccion', 'resistencia', 'ritmo', 'variedad', 'estabilidad'], si = 0, no = 0, quedan = [];
    claves.forEach(function (c) {
      var p = dePrueba(ps, c);
      if (p.estado === 'si') si++; else if (p.estado === 'no') no++;
      if (p.estado === 'ojo' || p.estado === 'no') quedan.push(p);
    });
    var cb = cambio(s), dir = dePrueba(ps, 'direccion'), vol = dePrueba(ps, 'volumen'), col = ctx && ctx.colombia;
    var faltan = Math.max(0, U.diasParaDecidir - dias);

    if (hoy.a === 0) return { nivel: 'descartar', titulo: 'Conviene descartarla', resumen: 'Ya no tiene ningún anuncio activo: apagó la oferta.', dias: dias, faltan: 0 };
    if (dir.estado === 'no' && dias >= 5) {
      return { nivel: 'descartar', titulo: 'Conviene descartarla', dias: dias, faltan: 0,
        resumen: 'Pasó de ' + cb.de + ' a ' + cb.a + ' anuncios activos en ' + plural(cb.dias, 'día', 'días') + ': la está apagando.' };
    }
    if (vol.estado === 'no' && dias >= U.diasParaDescartar) {
      return { nivel: 'descartar', titulo: 'Conviene descartarla', dias: dias, faltan: 0,
        resumen: 'Solo le quedan ' + plural(hoy.a, 'anuncio activo', 'anuncios activos') + ' (el radar la registró con ' + U.minActivos + ' o más): perdió volumen.' };
    }
    if (dias >= U.diasParaDecidir && no === 0 && si >= 4 && col !== 'saturada') {
      var ed = edad(hoy);
      return { nivel: 'modelar', titulo: 'Vale la pena modelarla', dias: dias, faltan: 0,
        resumen: 'Lleva ' + plural(dias, 'día', 'días') + ' de seguimiento con ' + plural(hoy.a, 'anuncio activo', 'anuncios activos')
          + (cb ? ' (' + (cb.pct > 0 ? '+' : '') + cb.pct + ' % en ' + plural(cb.dias, 'día', 'días') + ')' : '')
          + (hoy.s30 ? ', ' + hoy.s30 + ' con más de 30 días' : '') + (ed && ed.nuevos ? ' y ' + plural(ed.nuevos, 'lanzamiento nuevo', 'lanzamientos nuevos') + ' esta semana' : '') + '.' };
    }
    var partes = [];
    if (faltan > 0) partes.push('Lleva ' + dias + ' de ' + U.diasParaDecidir + ' días de seguimiento: aún es pronto para decidir con confianza.');
    if (col === 'saturada') partes.push('Colombia está saturada: aunque funcione en Brasil, allá ya la venden otros.');
    quedan.slice(0, 2).forEach(function (p) { if (p.problema) partes.push(p.problema + '.'); });
    if (!partes.length) partes.push('Sigue con buenas señales: confirma unos días más.');
    return { nivel: 'vigilar', titulo: 'Sigue vigilándola', resumen: partes.join(' '), dias: dias, faltan: faltan };
  }

  // Todo junto, listo para pintar.
  function analizar(fotos, ctx) {
    var s = serie(fotos);
    if (!s.length) return { n: 0, serie: [], veredicto: veredicto([], [], ctx), pruebas: [], hoy: null, edad: null, cambio: null, tendencia: 'sd', dias: 0 };
    var hoy = s[s.length - 1], ps = pruebas(s, ctx || {}), cb = cambio(s), maxA = 0, minA = Infinity, suma = 0, i;
    for (i = 0; i < s.length; i++) { if (s[i].a > maxA) maxA = s[i].a; if (s[i].a < minA) minA = s[i].a; suma += s[i].a; }
    var primeroLk = null;
    for (i = 0; i < s.length; i++) if (s[i].lk) { primeroLk = s[i].lk; break; }
    var top = '', pagina = null;
    for (i = s.length - 1; i >= 0; i--) if (s[i].top) { top = s[i].top; break; }
    for (i = s.length - 1; i >= 0; i--) if (s[i].pn || s[i].pg) { pagina = { nombre: s[i].pn, id: s[i].pg, host: s[i].hs }; break; }
    return {
      n: s.length, serie: s, hoy: hoy, dias: dif(s[0].d, hoy.d) + 1, primero: s[0].d,
      edad: edad(hoy), cambio: cb, tendencia: tendencia(cb), retirados: retirados(s), racha: racha(s, UMBRALES.minActivos),
      maxA: maxA, minA: minA, mediaA: entero(suma / s.length), seguidores: hoy.lk, seguidoresAntes: primeroLk, top: top, pagina: pagina,
      pruebas: ps, veredicto: veredicto(s, ps, ctx || {})
    };
  }

  return { UMBRALES: UMBRALES, serie: serie, edad: edad, cambio: cambio, tendencia: tendencia, retirados: retirados, racha: racha, pruebas: pruebas, veredicto: veredicto, analizar: analizar, dif: dif,
    nombresParecidos: nombresParecidos, mismoDestino: mismoDestino, paginaDudosa: paginaDudosa };
});
