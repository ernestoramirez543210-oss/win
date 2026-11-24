// Torta interactiva con drill-down, compartida por /objetivos, /numeros y el dash (los scripts de build la inlinean en cada pagina que la usa).
//
// Una torta (anillo) con la lista al lado: se toca una parte -o una fila- y se entra a ella; el anillo viejo se acerca al arco elegido y el nuevo se dibuja.
// Migas para volver, pestañas para cambiar de vista, y al final un detalle propio de cada pagina. La pagina solo dice QUE hay en cada nivel:
//
//   var t = Torta.crear({
//     el: 'id-de-la-tarjeta',                    // contenedor donde se pinta todo
//     titulo: '¿De dónde viene...?', nota: 'Toca una parte...',
//     controles: [ { k: 'modo', rotulo: 'Agrupar por', op: [['pais', 'País › oferta'], ['oferta', 'Oferta › país']] }, { k: 'per', rotulo: 'Periodo', op: [...] } ],
//                                                // grupos de pestañas, cada uno con su rotulo VISIBLE (un boton suelto como «Por parte» no se entiende); est[k] guarda la elegida;
//                                                // cambiar una reinicia el recorrido. Opcional por control: `op` puede ser una function (est) que devuelva las opciones (cada una [valor, texto, desactivada?, ayuda?]),
//                                                // `valor` una function (est) con la elegida y `alElegir` una function (valor, est) que reemplaza el comportamiento de arriba (p. ej. cambiar la moneda sin perder el recorrido);
//                                                // `oculto` una function (est) que devuelve true cuando el grupo no aplica a la vista actual (p. ej. «Colorear por» cuando se agrupa por metodo de pago) y entonces no se pinta
//     nivel: function (est) { return { titulo, total, items: [{ key, label, v, sub?, sub2?, chips?, color?, fijo?, sinHijos? }], hoja?: true, volver?: 'texto',
//                                      detalle?: 'html', centroSub?: 'texto', base?: numero, alMontar?: function (contenedor) }; },
//     fmt: function (v) { return '$' + v; },     // como se escribe un valor (con signo si v puede ser negativo)
//     color: function (item, i, est) { ... },    // opcional: color de cada parte (por defecto una paleta)
//     raiz: 'Todos los países',                  // opcional: texto de la primera miga; puede ser function (est)
//     encabezado: function (est) { return 'html' },       // opcional: html fijo entre las pestañas y las migas (resumenes, ajustes)
//     alMontar: function (contenedor, nv, est) { }   // opcional: despues de pintar (para enlazar botones del detalle o del encabezado)
//   });
//   t.pintar();   t.redibujar() (sin animar)   t.est   t.reiniciar();
//
// Con nivel().items cada parte lleva `key` (lo que se guarda en est.camino al entrar) y `label` (lo que dice la miga). `fijo: true` = no se agrupa en «Otras» ni se entra a ella.
// `chips: [{ t: 'texto', k: 'ok' | 'mejor' | 'warn' | 'neutro' }]` son etiquetas chicas bajo el nombre de la fila (estado, «Mejor de la oferta»...); `sub2` es una segunda linea gris.
// `mostrarCero: true` lista la parte aunque valga 0 (sin arco, en gris, al final): por defecto una parte en 0 no sale, porque no hay nada que dibujar.
// Mas de 8 partes: las chicas (menos del 2% o de la 8.ª en adelante) se juntan en «Otras (N)», que al tocarla despliega sus miembros en la lista.
// Ganancias y perdidas: un valor negativo (una oferta que pierde plata) tambien tiene arco. Cada parte mide su MONTO (|v|): primero las que suman, de mayor a menor, y despues las que restan,
// de la mayor perdida a la menor; las chicas se agrupan por lado («Otras ganancias» y «Otras pérdidas»). Los porcentajes son parte del anillo (|v| ÷ `base`, la suma de montos); el centro
// muestra `total`, que puede ser la suma con signo (la ganancia neta) y sale en rojo si es negativa. El color de cada parte lo decide la pagina (`color`): verde gana, rojo pierde.
// Quien pidio menos movimiento recibe la torta sin animar. requestAnimationFrame se pausa en pestañas ocultas, asi que cada animacion lleva un temporizador de respaldo.
(function (g) {
  'use strict';
  var PALETA = ['#4d8bff', '#a78bfa', '#34d399', '#f5b544', '#f2607a', '#2dd4bf', '#fb923c', '#c084fc', '#60a5fa', '#a3e635', '#f472b6', '#94a3b8'];
  var CX = 200, CY = 200, RO = 182, RI = 112;
  var REDUCIDO = !!(g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches);
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function pct(x, d) { return (x * 100).toFixed(d == null ? 0 : d) + '%'; }
  function pt(ang, rad) { return [CX + rad * Math.cos(ang), CY + rad * Math.sin(ang)]; }
  function trazo(a0, a1) {
    if (a1 - a0 > 2 * Math.PI - 0.0004) a1 = a0 + 2 * Math.PI - 0.0004;
    if (a1 - a0 < 0.0004) return '';
    var p0 = pt(a0, RO), p1 = pt(a1, RO), p2 = pt(a1, RI), p3 = pt(a0, RI), gr = a1 - a0 > Math.PI ? 1 : 0;
    return 'M' + p0[0].toFixed(2) + ' ' + p0[1].toFixed(2) + 'A' + RO + ' ' + RO + ' 0 ' + gr + ' 1 ' + p1[0].toFixed(2) + ' ' + p1[1].toFixed(2) +
      'L' + p2[0].toFixed(2) + ' ' + p2[1].toFixed(2) + 'A' + RI + ' ' + RI + ' 0 ' + gr + ' 0 ' + p3[0].toFixed(2) + ' ' + p3[1].toFixed(2) + 'Z';
  }
  // Cada parte ocupa un arco proporcional a su monto (|v|), ganancia o perdida.
  function angulos(nv) {
    var a = -Math.PI / 2, tot = nv.base || 1, sep = nv.items.length > 1 ? 0.012 : 0, out = [];
    nv.items.forEach(function (x) {
      var tam = Math.abs(x.v);
      if (!tam) { out.push([a, a]); return; }
      var w = Math.max(0.001, (tam / tot) * 2 * Math.PI - sep); out.push([a + sep / 2, a + sep / 2 + w]); a += (tam / tot) * 2 * Math.PI;
    });
    return out;
  }
  function esOtras(x) { return typeof x.key === 'string' && x.key.indexOf('~otras') === 0; }

  var contador = 0;
  function crear(cfg) {
    var est = { camino: [], etq: [], verOtras: false }, uid = 'tt' + (++contador), animId = 0;
    (cfg.controles || []).forEach(function (c) { if (Array.isArray(c.op)) est[c.k] = c.op[0][0]; });
    function raiz() { return document.getElementById(cfg.el); }

    // Agrupa, ordena, colorea y saca porcentajes del nivel que devuelve la pagina.
    function preparar(nv) {
      var items = (nv.items || []).filter(function (x) { return x.v !== 0 && isFinite(x.v); });
      // `mostrarCero: true` en una parte = se lista aunque su valor sea 0: no tiene arco (no hay nada que dibujar) pero sigue siendo parte de la lista exacta, p. ej. una campaña que gasto y no vendio.
      var ceros = (nv.items || []).filter(function (x) { return x.v === 0 && x.mostrarCero; });
      var hayNeg = items.some(function (x) { return x.v < 0; }), hayPos = items.some(function (x) { return x.v > 0; });
      // Primero lo que suma, de mayor a menor; despues lo que resta, de la mayor perdida a la menor.
      items.sort(function (a, b) { return (a.v < 0) !== (b.v < 0) ? (a.v < 0 ? 1 : -1) : (a.v < 0 ? a.v - b.v : b.v - a.v); });
      var abs = items.reduce(function (a, x) { return a + Math.abs(x.v); }, 0);
      var total = nv.total != null ? nv.total : items.reduce(function (a, x) { return a + x.v; }, 0);
      var base = nv.base != null ? nv.base : (hayNeg ? abs : (nv.total != null ? nv.total : abs));
      var lado = { p: [], n: [] }, otras = { p: [], n: [] }, rango = { p: 0, n: 0 }, maxLado = hayNeg && hayPos ? 6 : 7;
      items.forEach(function (x) {
        var l = x.v < 0 ? 'n' : 'p', r = rango[l]++;
        if (items.length > 8 && !x.fijo && (r >= maxLado || Math.abs(x.v) / (base || 1) < 0.02)) otras[l].push(x); else lado[l].push(x);
      });
      function junta(l, clave, etq) {
        var o = otras[l];
        if (o.length > 1) lado[l].push({ key: clave, label: etq + ' (' + o.length + ')', v: o.reduce(function (a, x) { return a + x.v; }, 0), hijos: o });
        else if (o.length === 1) lado[l].push(o[0]);
      }
      junta('p', '~otras', hayNeg ? 'Otras ganancias' : 'Otras');
      junta('n', '~otras-', 'Otras pérdidas');
      var grandes = lado.p.concat(lado.n);
      grandes.forEach(function (x, i) {
        x.color = esOtras(x) || x.key === '~sin' ? '#5b6b94' : (x.color || (cfg.color ? cfg.color(x, i, est) : null) || PALETA[i % PALETA.length]);
        x.pc = base > 0 ? Math.abs(x.v) / base : 0;
        if (x.hijos) x.hijos.forEach(function (h) { h.pc = base > 0 ? Math.abs(h.v) / base : 0; h.color = '#5b6b94'; });
      });
      // Las partes en 0 van al final de la lista, en gris y sin arco.
      ceros.forEach(function (x) { x.color = '#5b6b94'; x.pc = 0; });
      grandes = grandes.concat(ceros);
      return { titulo: nv.titulo, total: total, base: base, hayNeg: hayNeg, items: grandes, hoja: !!nv.hoja, volver: nv.volver || '', detalle: nv.detalle || '', centroSub: nv.centroSub || '', alMontar: nv.alMontar };
    }

    function etiquetaArco(x) { return x.label + ': ' + cfg.fmt(x.v) + ' (' + pct(x.pc) + ')'; }
    function pintarArcos(nv, fase, zoom) {
      var svg = document.getElementById(uid + '-svg'); if (!svg) return;
      var ang = angulos(nv), html = '';
      nv.items.forEach(function (x, i) {
        if (!x.v) return;                                  // una parte en 0 se lista pero no tiene arco
        var a0 = ang[i][0], a1 = ang[i][1];
        if (zoom) {
          var mapa = function (t) { var u = Math.min(1, Math.max(0, (t - zoom.c0) / Math.max(0.0001, zoom.c1 - zoom.c0))); return -Math.PI / 2 + u * 2 * Math.PI; };
          a0 = a0 + (mapa(a0) - a0) * zoom.t; a1 = a1 + (mapa(a1) - a1) * zoom.t;
        } else if (fase < 1) a1 = a0 + (a1 - a0) * fase;
        html += '<path class="tt-arco" data-i="' + i + '" d="' + trazo(a0, a1) + '" fill="' + x.color + '" tabindex="' + (zoom ? -1 : 0) + '" role="button" aria-label="' + esc(etiquetaArco(x)) + '"' + (zoom ? ' style="pointer-events:none"' : '') + '/>';
      });
      svg.innerHTML = '<circle cx="' + CX + '" cy="' + CY + '" r="' + (RO + 8) + '" fill="none" stroke="rgba(47,107,255,.14)"/>' + html;
    }
    // Con red de seguridad: si la pestaña esta oculta rAF se pausa y la torta se quedaria a medias; el temporizador la termina (paso(1) = estado final).
    function animar(dur, paso, fin) {
      var my = ++animId, hecho = false, t0 = null;
      function terminar() { if (hecho || my !== animId) return; hecho = true; paso(1); if (fin) fin(); }
      if (REDUCIDO || dur <= 0) { terminar(); return; }
      function f(ts) {
        if (hecho || my !== animId) return;
        if (t0 == null) t0 = ts;
        var t = Math.min(1, (ts - t0) / dur);
        if (t < 1) { paso(1 - Math.pow(1 - t, 3)); requestAnimationFrame(f); } else terminar();
      }
      requestAnimationFrame(f);
      setTimeout(terminar, dur + 120);
    }

    function centro(nv, item) {
      var c = document.getElementById(uid + '-centro'); if (!c) return;
      if (item) {
        var pc = pct(item.pc, item.pc < 0.1 ? 1 : 0);
        var sub = item.centroSub || (nv.hayNeg ? (item.v > 0 ? 'gana · ' : 'pierde · ') + pc + ' de la torta' : pc + ' de ' + String(nv.titulo || '').toLowerCase());
        c.innerHTML = '<span class="tt-c-et">' + esc(item.label) + '</span><span class="tt-c-val' + (item.v < 0 ? ' tt-neg' : '') + '">' + esc(cfg.fmt(item.v)) + '</span><span class="tt-c-sub">' + esc(sub) + '</span>';
        return;
      }
      c.innerHTML = '<span class="tt-c-et">' + esc(nv.titulo) + '</span><span class="tt-c-val' + (nv.total < 0 ? ' tt-neg' : '') + '">' + esc(cfg.fmt(nv.total)) + '</span><span class="tt-c-sub">' + esc(nv.centroSub) + '</span>';
    }

    function ligar(nv) {
      var r = raiz(), rueda = document.getElementById(uid + '-rueda');
      function sel(i) {
        rueda.querySelectorAll('.tt-arco').forEach(function (a) { a.classList.toggle('sel', Number(a.getAttribute('data-i')) === i); });
        r.querySelectorAll('.tt-fila[data-i]').forEach(function (f) { f.classList.toggle('sel', Number(f.getAttribute('data-i')) === i); });
        rueda.classList.toggle('tenue', i >= 0);
        centro(nv, i >= 0 ? nv.items[i] : null);
      }
      rueda.addEventListener('mouseover', function (e) { var a = e.target.closest('.tt-arco'); sel(a ? Number(a.getAttribute('data-i')) : -1); });
      rueda.addEventListener('mouseleave', function () { sel(-1); });
      rueda.addEventListener('focusin', function (e) { var a = e.target.closest('.tt-arco'); if (a) sel(Number(a.getAttribute('data-i'))); });
      rueda.addEventListener('focusout', function () { sel(-1); });
      rueda.addEventListener('click', function (e) { var a = e.target.closest('.tt-arco'); if (a) bajar(nv, Number(a.getAttribute('data-i'))); });
      rueda.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { var a = e.target.closest('.tt-arco'); if (a) { e.preventDefault(); bajar(nv, Number(a.getAttribute('data-i'))); } } });
      r.querySelectorAll('.tt-fila[data-i]').forEach(function (f) {
        var i = Number(f.getAttribute('data-i'));
        f.addEventListener('mouseenter', function () { sel(i); });
        f.addEventListener('mouseleave', function () { sel(-1); });
        f.addEventListener('click', function () { bajar(nv, i); });
      });
      r.querySelectorAll('.tt-fila[data-h]').forEach(function (f) {
        f.addEventListener('click', function () {
          var o = nv.items.filter(function (x) { return x.key === f.getAttribute('data-g'); })[0], h = o && o.hijos[Number(f.getAttribute('data-h'))];
          if (h) { est.verOtras = ''; est.camino = est.camino.concat([h.key]); est.etq = est.etq.concat([h.label]); pintar(false); }
        });
      });
    }

    function bajar(nv, i) {
      var it = nv.items[i]; if (!it || nv.hoja || it.key === '~sin' || it.sinHijos) return;
      if (esOtras(it)) { est.verOtras = est.verOtras === it.key ? '' : it.key; pintar(false, null, null, true); return; }
      est.verOtras = '';
      var ang = angulos(nv)[i];
      est.camino = est.camino.concat([it.key]); est.etq = est.etq.concat([it.label]);
      pintar(true, nv, ang);                  // zoom: el anillo viejo se acerca al arco clicado y luego se dibuja el nuevo
    }
    function subir(n) { est.verOtras = ''; est.camino = est.camino.slice(0, n); est.etq = est.etq.slice(0, n); pintar(false); }
    function reiniciar() { est.camino = []; est.etq = []; est.verOtras = ''; pintar(false); }

    function chipsHtml(x) {
      return x.chips && x.chips.length ? '<span class="tt-chips">' + x.chips.map(function (c) { return '<span class="tt-chip ' + esc(c.k || 'neutro') + '">' + esc(c.t) + '</span>'; }).join('') + '</span>' : '';
    }
    function nombreHtml(x) { return '<span class="tt-nm">' + esc(x.label) + (x.sub ? '<small>' + esc(x.sub) + '</small>' : '') + (x.sub2 ? '<small>' + esc(x.sub2) + '</small>' : '') + chipsHtml(x) + '</span>'; }

    function pintar(conZoom, viejo, ang, sinAnim) {
      var r = raiz(); if (!r) return;
      var nv = preparar(cfg.nivel(est));
      var grupos = (cfg.controles || []).map(function (c) {
        if (c.oculto && c.oculto(est)) return '';
        var ops = typeof c.op === 'function' ? c.op(est) : c.op, actual = c.valor ? c.valor(est) : est[c.k];
        return '<div class="tt-grupo">' + (c.rotulo ? '<span class="tt-rot">' + esc(c.rotulo) + '</span>' : '') +
          '<div class="tt-tabs" role="tablist" aria-label="' + esc(c.rotulo || c.k) + '">' + ops.map(function (o) { return '<button type="button" class="tt-tab' + (actual === o[0] ? ' on' : '') + '" data-k="' + esc(c.k) + '" data-v="' + esc(o[0]) + '"' + (o[2] ? ' disabled' : '') + (o[3] ? ' title="' + esc(o[3]) + '"' : '') + '>' + esc(o[1]) + '</button>'; }).join('') + '</div></div>';
      });
      var rz = typeof cfg.raiz === 'function' ? cfg.raiz(est) : (cfg.raiz || 'Todo');
      var migas = '<div class="tt-migas"><button type="button" data-n="0">' + esc(rz) + '</button>';
      est.camino.forEach(function (k, n) {
        var et = est.etq[n] || k;
        migas += '<span class="tt-sep">›</span>' + (n === est.camino.length - 1 ? '<span class="tt-aqui">' + esc(et) + '</span>' : '<button type="button" data-n="' + (n + 1) + '">' + esc(et) + '</button>');
      });
      migas += '</div>';
      var filas = [];
      nv.items.forEach(function (x, i) {
        var hoja = nv.hoja || x.key === '~sin' || x.sinHijos;
        filas.push('<button type="button" class="tt-fila' + (hoja ? ' hoja' : '') + '" data-i="' + i + '"><span class="tt-pt" style="background:' + x.color + '"></span>' + nombreHtml(x) + '<span class="tt-vv' + (x.v < 0 ? ' tt-neg' : '') + '">' + esc(cfg.fmt(x.v)) + '</span><span class="tt-pp">' + pct(x.pc, x.pc < 0.1 ? 1 : 0) + '</span><span class="tt-ch">' + (esOtras(x) ? (est.verOtras === x.key ? '˄' : '˅') : '›') + '</span></button>');
        if (esOtras(x) && est.verOtras === x.key) x.hijos.forEach(function (h, k) {
          filas.push('<button type="button" class="tt-fila sub" data-g="' + esc(x.key) + '" data-h="' + k + '"><span class="tt-pt" style="background:' + h.color + '"></span>' + nombreHtml(h) + '<span class="tt-vv' + (h.v < 0 ? ' tt-neg' : '') + '">' + esc(cfg.fmt(h.v)) + '</span><span class="tt-pp">' + pct(h.pc, 1) + '</span><span class="tt-ch">›</span></button>');
        });
      });
      r.innerHTML =
        '<div class="tt-cab"><div><h2>' + esc(cfg.titulo) + '</h2>' + (cfg.nota ? '<p class="tt-nota">' + esc(cfg.nota) + '</p>' : '') + '</div></div>' +
        (grupos.length ? '<div class="tt-controles">' + grupos.join('') + '</div>' : '') + (cfg.encabezado ? cfg.encabezado(est) || '' : '') + migas +
        '<div class="tt-grid"><div class="tt-rueda" id="' + uid + '-rueda"><svg id="' + uid + '-svg" viewBox="0 0 400 400"></svg><div class="tt-centro" id="' + uid + '-centro"></div></div>' +
        '<div class="tt-lado">' + (nv.volver ? '<div><button type="button" class="tt-volver" data-n="' + (est.camino.length - 1) + '">← ' + esc(nv.volver) + '</button></div>' : '') +
        '<div class="tt-lista">' + (filas.length ? filas.join('') : '<div class="tt-vacio">Sin datos en este periodo.</div>') + '</div>' + (nv.detalle ? '<div class="tt-detalle">' + nv.detalle + '</div>' : '') + '</div></div>';
      centro(nv, null);
      ligar(nv);
      r.querySelectorAll('.tt-tab').forEach(function (b) {
        b.addEventListener('click', function () {
          var k = b.getAttribute('data-k'), v = b.getAttribute('data-v'), c = (cfg.controles || []).filter(function (x) { return x.k === k; })[0];
          if (c && c.alElegir) c.alElegir(v, est); else { est[k] = v; reiniciar(); }
        });
      });
      // Solo los botones que traen data-n suben de nivel: otro boton del detalle que reuse la clase .tt-volver no debe devolver a la raiz.
      r.querySelectorAll('.tt-migas [data-n], .tt-volver[data-n]').forEach(function (b) { b.addEventListener('click', function () { subir(Number(b.getAttribute('data-n'))); }); });
      if (cfg.alMontar) cfg.alMontar(r, nv, est);
      if (nv.alMontar) nv.alMontar(r);
      if (sinAnim) pintarArcos(nv, 1);
      else if (conZoom && viejo && !REDUCIDO) {
        var zoom = { c0: ang[0], c1: ang[1], t: 0 }, cont = document.getElementById(uid + '-centro');
        document.getElementById(uid + '-svg').innerHTML = '';
        if (cont) cont.style.opacity = 0;
        animar(340, function (e) { zoom.t = e; pintarArcos(viejo, 1, zoom); }, function () {
          animar(520, function (e) { pintarArcos(nv, e); if (cont) cont.style.opacity = e; }, function () { pintarArcos(nv, 1); if (cont) cont.style.opacity = 1; });
        });
      } else animar(560, function (e) { pintarArcos(nv, e); }, function () { pintarArcos(nv, 1); });
    }
    return { pintar: function () { pintar(false); }, redibujar: function () { pintar(false, null, null, true); }, est: est, reiniciar: reiniciar, subir: subir };
  }
  g.Torta = { crear: crear, PALETA: PALETA };
})(window);
