// Produce los ebooks de un pedido de /radar de punta a punta, sin que nadie pulse nada entre un paso y otro.
//
// Es lo que hace el creador de ebooks (templates/armar-pdf.template.html) a mano, en el mismo orden y con las mismas llamadas:
//   por cada ebook del pedido (el principal y despues sus bonos):
//     [bono]  armar el brief del bono y rehacerle el temario (/api/ebook-brief, modo tema)
//     escribir el contenido por tandas        (/api/ebook-plan)
//     guardar el proyecto                     (/api/ebook-proyecto)
//     portada, una hoja por pagina y cierre   (/api/ebook-pagina)
//     guardar las hojas                       (/api/ebook-hojas)
//
// Vive en su propio archivo, SIN tocar el DOM ni el estado del creador, por tres razones:
//   1. no puede romper lo que ya funciona: el creador de ebooks sigue igual y esto solo le pide a la red lo que el le pediria;
//   2. se prueba en Node con una red falsa (scripts/probar-produccion.cjs), sin gastar un centavo;
//   3. cuando haga falta que lo produzca un servidor y no una pestaña abierta, este mismo archivo corre ahi (solo necesita `post`).
//
// Se mete en la pagina del creador de ebooks al armarla (Build-Suite.ps1 reemplaza su marcador por este archivo) y se expone como `EbookProduccion`.
//
// Lo que NO hace: decidir cuanto gastar. El tope (`tope.usd`) lo pone quien lo llama a partir de lo que el dueño confirmo, y esto se
// detiene cuando lo pasa. Tampoco repite lo ya hecho: un ebook `listo` se salta, y uno cuyo contenido ya esta guardado retoma desde sus
// hojas en vez de volver a pagar el texto y las fotos que ya existen.
(function (raiz, fabrica) {
  var M = fabrica();
  if (typeof module === 'object' && module && module.exports) module.exports = M; else raiz.EbookProduccion = M;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var MAX_PAGS = 120;
  var MAX_PAGS_BONO = 20;
  // El estilo de reserva, para cuando no llega uno (un ebook guardado antes de que el plan lo pidiera): sin estilo comun las fotos salen cada una de un libro distinto.
  // SIN vajilla: este texto sirve a cualquier ebook y la vajilla es de las recetas -el plan, functions/api/ebook-plan.js, ya devuelve el suyo con vajilla para un recetario,
  // y es el mismo servidor el que pone este texto cuando el modelo no trae estilo-. El creador manual (armar-pdf) usa ESTE texto, no una copia:
  // scripts/probar-ebook-estilo-foto.mjs vigila que el del plan y este sean el mismo.
  var ESTILO_NEUTRO = 'Consistent style across all photos: bright natural daylight, light neutral surface, soft shadows, minimal props, shot slightly from above at 45 degrees.';

  // Cuantas hojas se dibujan A LA VEZ. Antes era una detras de otra: gpt-image-2 tarda 30-90 s por imagen, mas la lectura del texto y los reintentos, y un ebook de 22 paginas
  // tardaba 46 minutos (medido el 2026-11-12 con los ebooks de costura del equipo: 36 min uno de 20 paginas, 10-11 min cada bono de 6-8). Con 4 a la vez el mismo ebook
  // tarda ~1/4. No cambia el costo: son las mismas imagenes. 4 y no mas: APIMart no publica su limite y contesta 429 si se le pide demasiado a la vez (el servidor
  // reintenta el envio; ver generarImagen en functions/api/ebook-pagina.js), y varias personas del equipo pueden estar produciendo al mismo tiempo.
  var PARALELO = 4;

  // Corre las tareas (funciones que devuelven una promesa) con a lo sumo `n` a la vez, empezando en el orden de la lista. Si una LANZA, no se empieza ninguna mas, se espera
  // a las que ya estaban corriendo (sus imagenes ya se estan pagando: quien llama las conserva) y se relanza el primer error. Las tareas que quieren seguir pese a un fallo
  // lo atrapan ellas mismas (asi lo hacen las hojas: una pagina mala no tira las otras).
  function enParalelo(tareas, n) {
    var lista = (tareas || []).slice(), sig = 0, fallo = null;
    n = Math.max(1, Math.min(Math.floor(Number(n) || PARALELO), lista.length || 1));
    function trabajador() {
      if (fallo || sig >= lista.length) return Promise.resolve();
      var t = lista[sig++];
      return Promise.resolve().then(t).then(trabajador, function (e) { if (!fallo) fallo = { e: e }; });
    }
    var corriendo = [];
    for (var k = 0; k < n; k++) corriendo.push(trabajador());
    return Promise.all(corriendo).then(function () { if (fallo) throw fallo.e; });
  }

  function copia(o) { return JSON.parse(JSON.stringify(o)); }
  function texto(v) { return String(v == null ? '' : v).trim(); }
  function espera(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  // Cortes controlados: parar() y el tope de gasto se resuelven lanzando estas dos, y producir() las atrapa.
  function Parada() { this.parada = true; }
  function Tope(usd) { this.tope = true; this.usd = usd; }

  // ---- temario (mismas cuentas que normSecciones / sincronizarPaginas del creador) ----
  function normSecciones(v) {
    return (Array.isArray(v) ? v : []).map(function (s) {
      if (typeof s === 'string') return { nombre: s.trim(), paginas: 0, puntos: [] };
      return {
        nombre: texto(s && s.nombre),
        paginas: Math.max(0, Math.min(Number(s && s.paginas) || 0, MAX_PAGS)),
        puntos: (Array.isArray(s && s.puntos) ? s.puntos : []).map(texto).filter(Boolean)
      };
    }).filter(function (s) { return s.nombre; });
  }
  function sumaPaginas(secs) { return secs.reduce(function (t, s) { return t + (Number(s.paginas) || 0); }, 0); }

  // Un bono es corto por definicion. Si al rehacerle el temario el modelo se pasa, se recorta el final: pagar 40 paginas por un bono de 8 no es lo pedido.
  function acotarSecciones(secs, max) {
    var queda = max, out = [];
    secs.forEach(function (s) {
      if (queda <= 0) return;
      var n = Math.min(s.paginas, queda);
      if (n <= 0) return;
      out.push({ nombre: s.nombre, paginas: n, puntos: s.puntos.slice(0, n) });
      queda -= n;
    });
    return out;
  }

  // El brief de un bono, igual que crearBono() del creador: hereda publico, pais, angulo y avisos del principal, y sabe que cubre el principal
  // para no repetirlo. Su temario es PROVISIONAL (un solo bloque con su propio titulo): rehacerTemario() lo disena antes de escribir.
  function bonoDe(brief, i) {
    var b = (brief.bonos || [])[i];
    if (!b || !texto(b.titulo)) throw new Error('El bono ' + (i + 1) + ' no tiene titulo.');
    var paginas = Math.max(3, Math.min(Number(b.paginas) || 8, MAX_PAGS_BONO));
    return {
      titulo: texto(b.titulo), promesa: texto(b.promesa),
      publico: brief.publico, pais: brief.pais, angulo: brief.angulo,
      tipoEntregable: texto(b.tipoEntregable) || brief.tipoEntregable,
      secciones: [{ nombre: texto(b.titulo), paginas: paginas, puntos: [] }],
      bonos: [], avisos: (brief.avisos || []).slice(), nPaginas: paginas,
      esBonoDe: { titulo: brief.titulo, secciones: (brief.secciones || []).map(function (s) { return typeof s === 'string' ? s : s.nombre; }) }
    };
  }

  function contextoTemario(brief) {
    return 'Este entregable ya está definido y NO se redefine: solo hay que diseñarle el temario.' +
      (brief.esBonoDe && brief.esBonoDe.titulo ? ' Es un bono de "' + brief.esBonoDe.titulo + '", así que no repite lo que ese cubre.' : '') +
      (brief.pais ? ' País: ' + brief.pais + '.' : '') +
      (brief.publico ? ' Público: ' + brief.publico + '.' : '') +
      (brief.angulo ? ' Ángulo: ' + brief.angulo + '.' : '') +
      (brief.tipoEntregable ? ' Tipo: ' + brief.tipoEntregable + '.' : '');
  }

  function sumaGasto(j) {
    var t = 0;
    if (j && j.gasto && typeof j.gasto === 'object') Object.keys(j.gasto).forEach(function (k) { var v = Number(j.gasto[k]); if (v > 0) t += v; });
    return t;
  }

  // La hoja que se guarda a partir de una respuesta de /api/ebook-pagina (`extra`: titulo, planIdx y si es portada o cierre).
  function hojaDeRespuesta(j, extra) {
    return Object.assign({ html: j.html, imagen: j.imagen || null, promptImagen: j.promptImagen || null, modeloImagen: j.modeloImagen || null, cambioDeModelo: j.cambioDeModelo || null, verificacion: j.verificacion || null }, extra);
  }
  function hojaDeError(titulo, i, msg) {
    return { html: '<div style="padding:40px;font:16px sans-serif;color:#c00">Error: ' + String(msg || 'no se pudo generar').replace(/[<>&]/g, '') + '</div>', imagen: null, titulo: titulo, planIdx: i, error: true };
  }
  // portada primero, las paginas por su lugar en el plan y el cierre al final: el orden que el creador espera al recuperar un ebook.
  function ordenarHojas(hojas) {
    var peso = function (h) { return h.esPortada ? -1 : h.esCierre ? 1e9 : (Number.isInteger(h.planIdx) ? h.planIdx : 1e8); };
    return hojas.slice().sort(function (a, b) { return peso(a) - peso(b); });
  }

  // ---------------------------------------------------------------------------------------------------------------------------
  // ---- fotos chicas ----
  // gpt-image-2 devuelve PNG de 1086 x 1448 que pesan 2-3 MB. Un ebook de 30 paginas son 60-90 MB: el PDF sale enorme, KV se llena, guardar las hojas manda un cuerpo de 90 MB y
  // /api/ebook-hojas se pasa de recursos al servirlas (Cloudflare: error 1102, y el creador no recupera el ebook ya pagado). Las fotos de antes eran JPEG de ~100 KB. Aqui se pasan a
  // JPEG en el navegador, que es donde esta el canvas: la misma foto a la vista, 10 a 15 veces mas chica. Si algo falla (sin canvas, imagen rota, tarda demasiado) se devuelve la
  // original: comprimir nunca puede costar una foto ya pagada.
  var FOTO_UMBRAL = 450000;      // base64 por debajo de esto (~330 KB) ya es chica: no se vuelve a comprimir (asi comprimir dos veces no degrada)
  function comprimirFoto(b64, o) {
    o = o || {};
    var umbral = o.umbral == null ? FOTO_UMBRAL : o.umbral;
    if (typeof b64 !== 'string' || b64.length <= umbral) return Promise.resolve(b64);
    var doc = o.documento || (typeof document !== 'undefined' ? document : null);
    var Img = o.Imagen || (typeof Image !== 'undefined' ? Image : null);
    if (!doc || !doc.createElement || !Img) return Promise.resolve(b64);
    return new Promise(function (res) {
      var hecho = false, reloj = null;
      function fin(v) { if (hecho) return; hecho = true; if (reloj) clearTimeout(reloj); res(v); }
      reloj = setTimeout(function () { fin(b64); }, o.espera || 20000);
      try {
        var im = new Img();
        im.onload = function () {
          try {
            var w = im.naturalWidth || im.width, h = im.naturalHeight || im.height;
            if (!w || !h) return fin(b64);
            var k = Math.min(1, (o.lado || 1600) / Math.max(w, h));      // por encima de 1600 px no se gana nada en una hoja de 768 px
            var c = doc.createElement('canvas');
            c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
            var g = c.getContext('2d');
            if (!g) return fin(b64);
            g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);      // un PNG con transparencia no puede quedar negro en el JPEG
            g.drawImage(im, 0, 0, c.width, c.height);
            var url = c.toDataURL('image/jpeg', o.calidad || 0.86), i = String(url).indexOf('base64,');
            var out = i < 0 ? '' : String(url).slice(i + 7);
            fin(out && out.length < b64.length ? out : b64);
          } catch (e) { fin(b64); }
        };
        im.onerror = function () { fin(b64); };
        im.src = 'data:image/' + (b64.slice(0, 4) === '/9j/' ? 'jpeg' : 'png') + ';base64,' + b64;
      } catch (e) { fin(b64); }
    });
  }
  // Calidad del JPEG segun lo que lleva la imagen. Una FOTO aguanta 0,86 (10 veces mas chica y la misma a la vista). Una imagen con el TEXTO dibujado adentro (portada con el titulo,
  // pagina dibujada por la IA) no: con 0,86 el navegador submuestrea el color a la mitad y las letras chicas de color sobre fondo de color quedan con un halo. Medido con paginas reales
  // de gpt-image-2: 35-38 dB con 0,86 contra 37-40 dB con 0,93 (que ya no submuestrea), de 190-310 KB a 270-420 KB por pagina; el PNG original pesa 1,4-1,9 MB.
  var CALIDAD_FOTO = 0.86, CALIDAD_TEXTO = 0.93;
  function calidadDeHoja(cuerpo, j) {
    var conTexto = !!((j && (j.modo === 'ia' || /class="mq-hoja mq-ia"/.test(String(j.html || '')))) ||
      (cuerpo && (cuerpo.hojaIA === true || (cuerpo.portada === true && cuerpo.tituloEnImagen !== false))));
    return conTexto ? CALIDAD_TEXTO : CALIDAD_FOTO;
  }

  // ---------------------------------------------------------------------------------------------------------------------------
  // ---- repetir lo que no salio bien ----
  // Una hoja se da por buena cuando trae su imagen y, si la imagen lleva el texto escrito adentro (portada con el titulo dentro, pagina dibujada por la IA), el texto se leyo igual que
  // lo pedido (verificacion.ok). Si no, se repite: la foto que no llego -si el proveedor aun tiene la tarea (tareaImagen), se RETOMA en vez de pedir y pagar otra- o la imagen cuyo
  // texto salio mal (palabras del titulo o de las etiquetas cambiadas: verificacion.estricta). Como maximo DOS repeticiones por el texto y una por la foto (dos si hay tarea que retomar,
  // que no cuesta): un proveedor caido o un modelo terco no pueden girar sin fin ni multiplicar el gasto.
  // «saturado»: el proveedor dijo que hay demasiados pedidos a la vez (HTTP 429). Esa imagen NO se cobro, asi que se espera un poco y se pide otra vez, hasta 3 veces
  // (6, 12 y 18 s): con varias hojas a la vez (PARALELO) y varias personas produciendo, es lo que puede pasar, y sin esto la hoja quedaba sin foto.
  var SATURADO = /\b429\b|too many requests|rate.?limit|demasiad[oa]s? (?:pedidos|peticiones|solicitudes)/i;
  var ESPERA_SATURADO = 6000;
  function motivoDeRepeticion(j) {
    if (!j) return '';
    if (!j.imagen && j.imagenError) return SATURADO.test(String(j.imagenError)) ? 'saturado' : 'foto';
    var v = j.verificacion;
    return (j.imagen && v && v.ok === false && v.estricta) ? 'texto' : '';
  }
  // De dos respuestas para la misma hoja, la mejor: con imagen antes que sin ella; entre dos imagenes, primero la LECTURA del texto (bien > sin leer o sin lectura > mal) y despues
  // la que lee mas palabras del pedido. Empate: la primera. Una lectura mala SIN la lista de lo que falta (asi vuelve una hoja guardada: ebook-hojas solo guarda «dudosa») cuenta como
  // la peor: antes valia «0 faltantes» y ninguna version nueva le podia ganar (la revision del 2026-11-13 lo encontro: rehacer las marcadas de un ebook reabierto pagaba y no cambiaba nada).
  function rangoLectura(h) { var v = h.verificacion; if (!v || v.ok == null) return 1; return v.ok === true ? 2 : 0; }
  function faltantes(h) { var v = h.verificacion; if (!v) return 0; if (Array.isArray(v.faltan)) return v.faltan.length; return v.ok === false ? Infinity : 0; }
  function mejorHoja(a, b) {
    if (!b) return a;
    if (!a) return b;
    if (!!b.imagen !== !!a.imagen) return b.imagen ? b : a;
    var ra = rangoLectura(a), rb = rangoLectura(b);
    if (ra !== rb) return rb > ra ? b : a;
    return faltantes(b) < faltantes(a) ? b : a;
  }
  // pedir(ruta, cuerpo) -> Promise<respuesta>. Si una repeticion lanza se devuelve lo mejor que habia (una imagen ya pagada nunca se pierde); parar y tope SI se propagan.
  // alRepetir(n, motivo) es opcional: para avisar en pantalla. dormir(ms) tambien (por omision, un reloj de verdad): la espera antes de repetir una imagen «saturado».
  async function repetirSiHaceFalta(pedir, ruta, cuerpo, j, alRepetir, dormir) {
    var mejor = j, actual = j, nFoto = 0, nTexto = 0, nSat = 0;
    while (true) {
      var motivo = motivoDeRepeticion(actual);
      if (!motivo) break;
      if (motivo === 'saturado') { if (nSat >= 3) break; nSat++; }
      else if (motivo === 'foto') { if (nFoto >= (actual.tareaImagen ? 2 : 1)) break; nFoto++; }
      else { if (nTexto >= 2) break; nTexto++; }
      try { if (alRepetir) alRepetir(nFoto + nTexto + nSat, motivo); } catch (e) { /* avisar no puede cortar la produccion */ }
      if (motivo === 'saturado') await (dormir || espera)(ESPERA_SATURADO * nSat);
      var c2 = (motivo === 'foto' && actual.tareaImagen) ? Object.assign({}, cuerpo, { tareaImagen: actual.tareaImagen }) : cuerpo;
      var j2;
      // parar o el tope en medio de una repeticion: la mejor imagen ya pagada viaja en el error (err.mejor) y quien llama la guarda (con varias hojas a la vez, si no, se perdian hasta 4)
      try { j2 = await pedir(ruta, c2); } catch (err) { if (err && (err.parada || err.tope)) { if (mejor && mejor.imagen && !err.mejor) err.mejor = mejor; throw err; } break; }
      if (!j2) break;
      mejor = mejorHoja(mejor, j2);
      actual = j2;
    }
    return mejor;
  }

  // Envuelve el `post` de quien llama: la foto que trae cada hoja (/api/ebook-pagina) llega ya comprimida. Todo lo demas pasa tal cual, y si comprimir falla, la foto original.
  function conFotosChicas(post, comprimir) {
    return function (ruta, cuerpo) {
      return Promise.resolve(post(ruta, cuerpo)).then(function (j) {
        if (ruta !== '/api/ebook-pagina' || !j || typeof j.imagen !== 'string') return j;
        return Promise.resolve(comprimir(j.imagen, { calidad: calidadDeHoja(cuerpo, j) })).then(function (c) { if (typeof c === 'string' && c) j.imagen = c; return j; }, function () { return j; });
      });
    };
  }

  // ---------------------------------------------------------------------------------------------------------------------------
  // opts: { orden, post(ruta, cuerpo) -> Promise<json> (lanza si ok:false), leer(ruta) -> Promise<json|null>,
  //         alAvanzar(ev), alEntregable(clave, parche) -> Promise|void, parar() -> boolean, tope: { usd }, espera(ms), paralelo (hojas a la vez; por omision PARALELO) }
  // Devuelve { estado: 'lista' | 'parada' | 'error' | 'tope', hechos: [claves], gasto: usd, error?, clave? }
  function producir(opts) {
    var orden = opts.orden, post = conFotosChicas(opts.post, opts.comprimir || comprimirFoto);
    var leer = opts.leer || function () { return Promise.resolve(null); };
    var dormir = opts.espera || espera;
    var gasto = 0, gastoAvisado = 0;
    var colores = orden.colores || { a: '#e91e63', b: '#2d7a3e' };
    var avatar = orden.avatar || null;
    var datosPago = texto(orden.datosPago).slice(0, 300);

    function avisar(ev) { try { if (opts.alAvanzar) opts.alAvanzar(ev); } catch (e) { /* pintar no puede cortar la produccion */ } }
    async function reportar(clave, parche) {
      // El gasto va como diferencia desde el ultimo aviso: el servidor lo suma al del pedido.
      var d = Math.round((gasto - gastoAvisado) * 10000) / 10000;
      if (d > 0) { parche.costo = d; gastoAvisado = gasto; }
      try { if (opts.alEntregable) await opts.alEntregable(clave, parche); } catch (e) { /* no poder avisar al pedido no debe tirar un ebook ya pagado */ }
    }
    function chequear() {
      if (opts.parar && opts.parar()) throw new Parada();
      if (opts.tope && opts.tope.usd > 0 && gasto > opts.tope.usd) throw new Tope(opts.tope.usd);
    }
    // Toda llamada pasa por aqui: revisa si hay que parar, cuenta el gasto de la respuesta y reintenta fallos de red.
    // `libre` salta la revision de parar/tope: se usa para GUARDAR lo ya generado, que tiene que salir aunque se haya pedido parar.
    async function llamar(ruta, cuerpo, veces, libre) {
      var intentos = (veces == null ? 2 : veces) + 1, ultimo;
      for (var k = 0; k < intentos; k++) {
        if (!libre) chequear();
        try {
          var j = await post(ruta, cuerpo);
          gasto += sumaGasto(j);
          return j;
        } catch (e) {
          if (e && (e.parada || e.tope)) throw e;
          ultimo = e;
          // Un rechazo del servidor (ok:false con su motivo) no mejora repitiendolo; un corte de red si. Se distingue por el mensaje del creador.
          if (/Entra como|API key|proveedor|Falta el|Sin saldo|Configura/i.test(String(e && e.message))) break;
          if (k < intentos - 1) await dormir(1500 * (k + 1));
        }
      }
      throw ultimo || new Error('No se pudo llamar a ' + ruta);
    }

    async function asegurarAvatar(brief) {
      if (avatar && avatar.nombre) return avatar;
      var j = await llamar('/api/ebook-avatar', { brief: brief, notas: '' });
      avatar = j.avatar || null;
      return avatar;
    }

    async function temarioDelBono(brief) {
      var j = await llamar('/api/ebook-brief', { modo: 'tema', tema: brief.titulo + (brief.promesa ? ' — ' + brief.promesa : ''), notas: contextoTemario(brief) });
      var secs = normSecciones((j.brief || {}).secciones);
      if (!secs.length) throw new Error('No devolvió un temario utilizable para el bono «' + brief.titulo + '».');
      secs = acotarSecciones(secs, MAX_PAGS_BONO);
      brief.secciones = secs;
      brief.nPaginas = Math.max(3, Math.min(sumaPaginas(secs) || brief.nPaginas, MAX_PAGS_BONO));
    }

    async function escribirContenido(e, brief) {
      var paginas = [], estilo = '', desde = 1, vueltas = 0;
      while (desde) {
        var j = await llamar('/api/ebook-plan', { brief: brief, desde: desde, avatar: avatar, hechos: paginas.map(function (p) { return p.titulo; }), diseno: orden.diseno === 'codigo' ? 'codigo' : 'ia' });
        paginas = paginas.concat(j.paginas || []);
        if (j.estiloFoto && !estilo) estilo = j.estiloFoto;
        avisar({ tipo: 'texto', clave: e.clave, titulo: brief.titulo, hecho: paginas.length, total: j.total || brief.nPaginas, gasto: gasto });
        var sig = Number(j.siguiente) || 0;
        // Si el servidor repitiera la misma tanda para siempre, esto no puede girar sin fin.
        desde = sig > desde && ++vueltas < 40 ? sig : 0;
      }
      if (!paginas.length) throw new Error('No se escribió ninguna página de «' + brief.titulo + '».');
      return { paginas: paginas, estiloFoto: estilo || ESTILO_NEUTRO };
    }

    async function guardarProyecto(id, brief, paginas, estiloFoto) {
      var j = await llamar('/api/ebook-proyecto', {
        id: id || null, titulo: brief.titulo, brief: brief, avatar: avatar, paginas: paginas, flujos: {}, estiloFoto: estiloFoto,
        datosPago: datosPago, colorPrimario: colores.a, colorSecundario: colores.b
      }, 2, true);
      return j.id || id;
    }
    function guardarHojas(id, hojas) {
      return llamar('/api/ebook-hojas', { id: id, hojas: ordenarHojas(hojas).map(function (h) { var c = {}; Object.keys(h).forEach(function (k) { if (k !== 'error') c[k] = h[k]; }); return c; }) }, 2, true);
    }

    // La foto es lo unico que puede fallar sin que la llamada falle: el proveedor de imagenes se cuelga o contesta un error pasajero, la hoja vuelve con `imagenError`
    // y sin foto, y quedaria con el cuadro roto. Se repite UNA vez el mismo pedido antes de darla por buena; si vuelve a fallar se deja sin foto (el ebook igual sale
    // y el aviso final dice cuantas faltan). Un solo reintento: un proveedor caido no puede girar sin fin ni multiplicar el gasto.
    // Ademas de la foto que no llego, se repite la imagen cuyo texto salio distinto del pedido (ver repetirSiHaceFalta, arriba).
    async function conFoto(ruta, cuerpo, j) {
      return repetirSiHaceFalta(function (r, c) { return llamar(r, c, 0); }, ruta, cuerpo, j, null, dormir);
    }

    // Genera lo que falta: la portada, cada pagina y el cierre. `est.hojas` trae lo que ya existia (retomar) y se va completando EN EL SITIO: si hay que
    // parar o algo revienta a la mitad, quien llamo conserva lo generado y lo guarda (esas fotos ya se pagaron). Si una hoja falla se anota y se sigue, como el
    // creador: una pagina mala no tira las otras veintinueve.
    // Las hojas se dibujan PARALELO a la vez (antes, una detras de otra): se EMPIEZAN en orden (portada, paginas, cierre) pero pueden TERMINAR en cualquier orden;
    // est.hojas queda desordenada y guardarHojas la ordena (ordenarHojas). Parar o pasar el tope deja de empezar hojas nuevas, espera las que ya estaban en camino
    // (se estan pagando) y las conserva; por eso el tope se puede pasar en hasta PARALELO-1 imagenes (unos 3 centavos).
    async function renderizar(e, brief, paginas, estiloFoto, est) {
      // diseno: «ia» (la pagina la dibuja la IA con el texto adentro) o «codigo» (maqueta del sistema + foto). portada: «ia» (titulo dentro de la imagen) o «html».
      var comun = { estiloFoto: estiloFoto, colorPrimario: colores.a, colorSecundario: colores.b, diseno: orden.diseno === 'codigo' ? 'codigo' : 'ia' };
      var tituloEnImagen = orden.portada !== 'html';
      var total = paginas.length + 2;
      var hecho = est.hojas.filter(function (h) { return h.imagen && !h.error; }).length;
      var tiene = function (f) { return est.hojas.some(f); };
      var anotar = function (h) { est.hojas.push(h); hecho++; avisar({ tipo: 'pagina', clave: e.clave, titulo: brief.titulo, hecho: Math.min(hecho, total), total: total, gasto: gasto }); };
      var tareas = [];

      if (!tiene(function (h) { return h.esPortada && h.imagen && !h.error; })) {
        est.hojas = est.hojas.filter(function (h) { return !h.esPortada; });
        tareas.push(async function () {
          try {
            var cuerpoP = Object.assign({ portada: true, brief: brief, tituloEnImagen: tituloEnImagen }, comun);
            var jp = await conFoto('/api/ebook-pagina', cuerpoP, await llamar('/api/ebook-pagina', cuerpoP, 1));
            anotar({ html: jp.html, imagen: jp.imagen || null, titulo: 'Portada', esPortada: true, promptImagen: jp.promptImagen || null, planIdx: null, modeloImagen: jp.modeloImagen || null, cambioDeModelo: jp.cambioDeModelo || null, verificacion: jp.verificacion || null });
          } catch (err) { if (err.parada || err.tope) { if (err.mejor && err.mejor.imagen) anotar(hojaDeRespuesta(err.mejor, { titulo: 'Portada', esPortada: true, planIdx: null })); throw err; } /* sin portada el ebook sirve: se sigue y queda dicho al final */ }
        });
      }
      paginas.forEach(function (pagina, i) {
        var ya = est.hojas.filter(function (h) { return h.planIdx === i && !h.error && h.imagen; })[0];
        if (ya) return;
        est.hojas = est.hojas.filter(function (h) { return h.planIdx !== i; });     // una hoja con error de un intento anterior se reemplaza
        tareas.push(async function () {
          try {
            var cuerpoH = Object.assign({}, pagina, comun, { tipoEntregable: brief.tipoEntregable || '' });
            var j = await conFoto('/api/ebook-pagina', cuerpoH, await llamar('/api/ebook-pagina', cuerpoH, 1));
            anotar({ html: j.html, imagen: j.imagen || null, titulo: pagina.titulo, promptImagen: j.promptImagen || null, planIdx: i, modeloImagen: j.modeloImagen || null, cambioDeModelo: j.cambioDeModelo || null, verificacion: j.verificacion || null });
          } catch (err) {
            if (err.parada || err.tope) { if (err.mejor && err.mejor.imagen) anotar(hojaDeRespuesta(err.mejor, { titulo: pagina.titulo, planIdx: i })); throw err; }
            anotar(hojaDeError(pagina.titulo, i, err && err.message));
          }
        });
      });
      if (!tiene(function (h) { return h.esCierre && h.imagen && !h.error; })) {
        est.hojas = est.hojas.filter(function (h) { return !h.esCierre; });
        tareas.push(async function () {
          try {
            var cuerpoC = Object.assign({ cierre: true, brief: brief, datosPago: datosPago, avatar: avatar }, comun);
            var jc = await conFoto('/api/ebook-pagina', cuerpoC, await llamar('/api/ebook-pagina', cuerpoC, 1));
            anotar({ html: jc.html, imagen: jc.imagen || null, titulo: 'Cierre', esCierre: true, promptImagen: jc.promptImagen || null, planIdx: null, modeloImagen: jc.modeloImagen || null, cambioDeModelo: jc.cambioDeModelo || null });
          } catch (err) { if (err.parada || err.tope) { if (err.mejor && err.mejor.imagen) anotar(hojaDeRespuesta(err.mejor, { titulo: 'Cierre', esCierre: true, planIdx: null })); throw err; } }
        });
      }
      await enParalelo(tareas, opts.paralelo || PARALELO);
    }

    async function producirUno(e) {
      // 1. el brief de ESTE ebook
      var brief = e.clave === 'principal' ? copia(orden.brief) : bonoDe(orden.brief, Number(e.clave.slice(5)));
      var guardado = null;
      if (e.proyectoId && e.etapa === 'contenido') {
        try { var pr = await leer('/api/ebook-proyecto?id=' + encodeURIComponent(e.proyectoId)); guardado = pr && pr.ok ? pr.proyecto : null; } catch (x) { guardado = null; }
      }
      var paginas, estilo, id = guardado ? e.proyectoId : null;
      if (guardado && guardado.paginas && guardado.paginas.length && guardado.brief) {
        // retoma: el contenido ya esta escrito y guardado, no se vuelve a pagar
        brief = guardado.brief; paginas = guardado.paginas; estilo = guardado.estiloFoto || ESTILO_NEUTRO;
        avisar({ tipo: 'retoma', clave: e.clave, titulo: brief.titulo });
      } else {
        await reportar(e.clave, { estado: 'produciendo', etapa: '' });
        avisar({ tipo: 'ebook', clave: e.clave, titulo: brief.titulo, texto: 'Preparando…' });
        await asegurarAvatar(brief);
        if (e.clave !== 'principal') await temarioDelBono(brief);
        var c = await escribirContenido(e, brief);
        paginas = c.paginas; estilo = c.estiloFoto;
        id = await guardarProyecto(id, brief, paginas, estilo);
        await reportar(e.clave, { estado: 'produciendo', etapa: 'contenido', proyectoId: id, titulo: brief.titulo });
      }
      await asegurarAvatar(brief);

      // 2. las hojas (lo que ya existe se conserva)
      var hojas = [];
      if (id) { try { var hj = await leer('/api/ebook-hojas?id=' + encodeURIComponent(id)); hojas = (hj && hj.ok && Array.isArray(hj.hojas)) ? hj.hojas : []; } catch (x) { hojas = []; } }
      var fallo = null, est = { hojas: hojas };
      try { await renderizar(e, brief, paginas, estilo, est); } catch (err) { fallo = err; }
      hojas = est.hojas;
      // Lo generado se guarda SIEMPRE, incluso si hubo que parar: la proxima vez retoma desde aqui y no vuelve a pagar esas fotos.
      if (hojas.length) {
        try { id = await guardarProyecto(id, brief, paginas, estilo); await guardarHojas(id, hojas); } catch (x) { if (!fallo) fallo = x; }
      }
      if (fallo) throw fallo;

      var sinFoto = hojas.filter(function (h) { return !h.imagen; }).length, malas = hojas.filter(function (h) { return h.error; }).length;
      // hojas cuya imagen sigue con el texto distinto del plan aun despues de repetirlas (la lectura del texto la marca): hay que mirarlas
      var textoDudoso = hojas.filter(function (h) { return h.verificacion && h.verificacion.ok === false; }).length;
      var faltaPortada = !hojas.some(function (h) { return h.esPortada; }), faltaCierre = !hojas.some(function (h) { return h.esCierre; });
      if (malas || faltaPortada || faltaCierre) {
        var motivo = (malas ? malas + ' página' + (malas === 1 ? '' : 's') + ' con error' : '') + (faltaPortada ? (malas ? ', ' : '') + 'sin portada' : '') + (faltaCierre ? (malas || faltaPortada ? ', ' : '') + 'sin cierre' : '');
        await reportar(e.clave, { estado: 'error', etapa: 'contenido', proyectoId: id, titulo: brief.titulo });
        throw new Error('«' + brief.titulo + '» quedó con ' + motivo + '. Está guardado: ábrelo en el creador para rehacer lo que falta, o reintenta.');
      }
      await reportar(e.clave, { estado: 'listo', proyectoId: id, titulo: brief.titulo });
      avisar({ tipo: 'listo', clave: e.clave, titulo: brief.titulo, proyectoId: id, sinFoto: sinFoto, textoDudoso: textoDudoso, paginas: paginas.length, gasto: gasto });
      return id;
    }

    return (async function () {
      var dentro = (orden.entregables || []).filter(function (e) { return e.incluir !== false; });
      var hechos = [];
      avisar({ tipo: 'inicio', total: dentro.length });
      for (var n = 0; n < dentro.length; n++) {
        var e = dentro[n];
        if (e.estado === 'listo') { hechos.push(e.clave); continue; }
        try {
          chequear();
          await producirUno(e);
          hechos.push(e.clave);
        } catch (err) {
          if (err && err.parada) { avisar({ tipo: 'fin', estado: 'parada', gasto: gasto }); return { estado: 'parada', hechos: hechos, gasto: gasto, clave: e.clave }; }
          if (err && err.tope) { avisar({ tipo: 'fin', estado: 'tope', gasto: gasto }); return { estado: 'tope', hechos: hechos, gasto: gasto, clave: e.clave, error: 'Se detuvo al pasar de US$ ' + err.usd.toFixed(2) + ' gastados.' }; }
          await reportar(e.clave, { estado: 'error' });
          avisar({ tipo: 'fin', estado: 'error', gasto: gasto, error: String(err && err.message || err) });
          return { estado: 'error', hechos: hechos, gasto: gasto, clave: e.clave, error: String(err && err.message || err) };
        }
      }
      avisar({ tipo: 'fin', estado: 'lista', gasto: gasto });
      return { estado: 'lista', hechos: hechos, gasto: gasto };
    })();
  }

  return { producir: producir, enParalelo: enParalelo, PARALELO: PARALELO, bonoDe: bonoDe, acotarSecciones: acotarSecciones, normSecciones: normSecciones, sumaPaginas: sumaPaginas, ordenarHojas: ordenarHojas, comprimirFoto: comprimirFoto, calidadDeHoja: calidadDeHoja, conFotosChicas: conFotosChicas, repetirSiHaceFalta: repetirSiHaceFalta, motivoDeRepeticion: motivoDeRepeticion, mejorHoja: mejorHoja, ESTILO_NEUTRO: ESTILO_NEUTRO };
});
