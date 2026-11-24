// Convierte las hojas de un ebook ya producido en un PDF, sin que nadie abra la ventana de imprimir.
//
// El creador de ebooks (templates/armar-pdf.template.html) saca el PDF con «Imprimir > Guardar como PDF»: una persona, una ventana y un clic por ebook. Para que la Fabrica de ofertas pueda
// seguir sola de los ebooks a los flujos de ZapData hace falta el PDF como archivo, asi que aqui se hace en el navegador, en dos pasos que se prueban por separado:
//   1. rasterizar(hoja): la hoja (HTML de 768x1024 con su foto) se dibuja en un canvas y sale como JPEG. Es lo unico que necesita el navegador (SVG con foreignObject; las hojas
//      traen su foto como data: URI y fuentes del sistema, asi que no dependen de nada externo).
//   2. armarPdf(paginas): un PDF 1.4 de una imagen por pagina (JPEG tal cual, DCTDecode), escrito a mano: sin librerias, sin red. Es codigo puro y se valida en Node.
//
// El resultado es un PDF «de imagenes»: el texto no se puede seleccionar, pero se ve exactamente como la hoja del creador y pesa unos 150 a 250 KB por pagina. Es para mandarlo por
// WhatsApp y mostrarlo en los anuncios; el ebook editable sigue en el creador.
//
// Se mete en la pagina de la Fabrica al armarla (Build-Suite.ps1 lo inlinea con su marcador de la plantilla fabrica.template.html) y se expone como `EbookPdf`. Es CODIGO, no datos: no debe traer el cierre de una etiqueta script ni el inicio de un comentario HTML (scripts/probar-fabrica-motor.cjs lo comprueba).
(function (raiz, fabrica) {
  var M = fabrica();
  if (typeof module === 'object' && module && module.exports) module.exports = M; else raiz.EbookPdf = M;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var ANCHO = 768, ALTO = 1024;                 // la hoja del creador (el papel es 8 x 10,67 pulgadas)
  var PT_ANCHO = 576, PT_ALTO = 768;            // en puntos de PDF: 768 px a 96 dpi = 576 pt a 72 dpi
  var SOLO_BASE64 = /^[A-Za-z0-9+\/=_\s-]+$/;
  var GRIS = 'data:image/svg+xml;base64,' + (typeof btoa === 'function' ? btoa('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#dcdcdc"/></svg>') : 'PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI4IiBoZWlnaHQ9IjgiPjxyZWN0IHdpZHRoPSI4IiBoZWlnaHQ9IjgiIGZpbGw9IiNkY2RjZGMiLz48L3N2Zz4=');

  // ---------------------------------------------------------------------------------------------------------------------------------------------
  // La hoja como HTML listo para dibujar
  function fotoDe(h) { return (h && h.imagen && SOLO_BASE64.test(h.imagen)) ? 'data:image/png;base64,' + h.imagen : GRIS; }
  function htmlDeHoja(h) { return String((h && h.html) || '').split('__FOTO__').join(fotoDe(h)); }

  // El SVG que envuelve la hoja. `interior` es el HTML de la hoja ya convertido a XHTML (XML bien formado): un SVG es XML y una etiqueta sin cerrar (<br>) o un &nbsp; lo rompen.
  function svgDeHoja(interior) {
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + ANCHO + '" height="' + ALTO + '"><foreignObject x="0" y="0" width="' + ANCHO + '" height="' + ALTO + '">' +
      '<div xmlns="http://www.w3.org/1999/xhtml" style="width:' + ANCHO + 'px;height:' + ALTO + 'px;position:relative;overflow:hidden;background:#fff">' + interior + '</div></foreignObject></svg>';
  }

  // HTML de un modelo -> XHTML. DOMParser no ejecuta nada (los scripts no corren en un documento de DOMParser); ademas se quitan los elementos que no pintan nada y los atributos
  // de evento, porque el resultado se guarda y se vuelve a dibujar. Solo existe en el navegador.
  var SOBRAN = ['script', 'iframe', 'object', 'embed', 'link', 'base', 'meta', 'form', 'noscript', 'template'];
  function aXhtml(html, DOMParserClase, SerializadorClase) {
    var P = DOMParserClase || (typeof DOMParser !== 'undefined' ? DOMParser : null);
    var S = SerializadorClase || (typeof XMLSerializer !== 'undefined' ? XMLSerializer : null);
    if (!P || !S) throw new Error('Este navegador no puede convertir las hojas a PDF.');
    var doc = new P().parseFromString('<!doctype html><html><body>' + html + '</body></html>', 'text/html');
    SOBRAN.forEach(function (t) { var l = doc.body.querySelectorAll(t); for (var i = 0; i < l.length; i++) l[i].parentNode.removeChild(l[i]); });
    var todos = doc.body.querySelectorAll('*');
    for (var i = 0; i < todos.length; i++) {
      var atrs = todos[i].attributes;
      for (var k = atrs.length - 1; k >= 0; k--) if (/^on/i.test(atrs[k].name)) todos[i].removeAttribute(atrs[k].name);
    }
    var s = new S(), out = '';
    for (var n = doc.body.firstChild; n; n = n.nextSibling) out += s.serializeToString(n);
    return out;
  }

  // Dibuja una hoja en un canvas y devuelve su JPEG. opts: { escala (1.25), calidad (0.82), documento }. Solo en el navegador.
  function rasterizar(hoja, opts) {
    opts = opts || {};
    var doc = opts.documento || (typeof document !== 'undefined' ? document : null);
    if (!doc) return Promise.reject(new Error('Falta el navegador para dibujar la hoja.'));
    var escala = opts.escala || 1.25, calidad = opts.calidad || 0.82;
    var svg;
    try { svg = svgDeHoja(aXhtml(htmlDeHoja(hoja))); } catch (e) { return Promise.reject(e); }
    return new Promise(function (ok, mal) {
      var img = new Image();
      img.onload = function () {
        try {
          var c = doc.createElement('canvas');
          c.width = Math.round(ANCHO * escala); c.height = Math.round(ALTO * escala);
          var g = c.getContext('2d');
          g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);
          g.drawImage(img, 0, 0, c.width, c.height);
          c.toBlob(function (blob) {
            if (!blob) { mal(new Error('El navegador no pudo guardar la hoja como imagen.')); return; }
            blob.arrayBuffer().then(function (b) { ok({ bytes: new Uint8Array(b), ancho: c.width, alto: c.height }); }, mal);
          }, 'image/jpeg', calidad);
        } catch (e) { mal(new Error('No se pudo dibujar la hoja (' + (e && e.message ? e.message : e) + ').')); }
      };
      img.onerror = function () { mal(new Error('La hoja no se pudo dibujar (HTML que el navegador no entiende).')); };
      // data: y no blob:, porque con un blob el canvas queda «tainted» en varios navegadores y toBlob falla.
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    });
  }

  // ---------------------------------------------------------------------------------------------------------------------------------------------
  // El PDF
  function latin1(s) { var b = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) b[i] = s.charCodeAt(i) & 255; return b; }
  function hex4(n) { var h = n.toString(16); return ('0000' + h).slice(-4).toUpperCase(); }
  // Titulo como cadena UTF-16BE en hexadecimal: los acentos y la ñ salen bien en cualquier visor.
  function tituloPdf(t) {
    var s = String(t == null ? '' : t).slice(0, 120), out = 'FEFF';
    for (var i = 0; i < s.length; i++) out += hex4(s.charCodeAt(i));
    return '<' + out + '>';
  }
  function fechaPdf(d) {
    function dos(n) { return ('0' + n).slice(-2); }
    return 'D:' + d.getUTCFullYear() + dos(d.getUTCMonth() + 1) + dos(d.getUTCDate()) + dos(d.getUTCHours()) + dos(d.getUTCMinutes()) + dos(d.getUTCSeconds()) + 'Z';
  }
  function esJpeg(b) { return b && b.length > 4 && b[0] === 0xFF && b[1] === 0xD8 && b[b.length - 2] === 0xFF && b[b.length - 1] === 0xD9; }

  // paginas: [{ bytes: Uint8Array (JPEG), ancho, alto }] -> Uint8Array con el PDF. opts: { titulo, anchoPt, altoPt, fecha }
  function armarPdf(paginas, opts) {
    opts = opts || {};
    if (!paginas || !paginas.length) throw new Error('No hay paginas para el PDF.');
    var wPt = opts.anchoPt || PT_ANCHO, hPt = opts.altoPt || PT_ALTO;
    paginas.forEach(function (p, i) {
      if (!esJpeg(p && p.bytes)) throw new Error('La pagina ' + (i + 1) + ' no es un JPEG valido.');
      if (!(p.ancho > 0) || !(p.alto > 0)) throw new Error('La pagina ' + (i + 1) + ' no trae sus medidas.');
    });
    var partes = [], pos = 0, offs = [];
    function emitir(x) { var b = typeof x === 'string' ? latin1(x) : x; partes.push(b); pos += b.length; }
    function objeto(n, cuerpo) { offs[n] = pos; emitir(n + ' 0 obj\n'); emitir(cuerpo); emitir('\nendobj\n'); }
    function objetoFlujo(n, dic, datos) { offs[n] = pos; emitir(n + ' 0 obj\n<< ' + dic + ' /Length ' + datos.length + ' >>\nstream\n'); emitir(datos); emitir('\nendstream\nendobj\n'); }

    emitir('%PDF-1.4\n%' + String.fromCharCode(0xE2, 0xE3, 0xCF, 0xD3) + '\n');     // 4 bytes altos: le dicen a los programas que el archivo es binario
    var n = paginas.length, kids = [];
    for (var i = 0; i < n; i++) kids.push((6 + 3 * i) + ' 0 R');
    objeto(1, '<< /Type /Catalog /Pages 2 0 R >>');
    objeto(2, '<< /Type /Pages /Kids [' + kids.join(' ') + '] /Count ' + n + ' >>');
    objeto(3, '<< /Title ' + tituloPdf(opts.titulo || 'Ebook') + ' /Producer (Fabrica de ofertas) /CreationDate (' + fechaPdf(opts.fecha || new Date()) + ') >>');
    paginas.forEach(function (p, i) {
      var img = 4 + 3 * i, cont = 5 + 3 * i, pag = 6 + 3 * i;
      objetoFlujo(img, '/Type /XObject /Subtype /Image /Width ' + p.ancho + ' /Height ' + p.alto + ' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode', p.bytes);
      objetoFlujo(cont, '', latin1('q ' + wPt + ' 0 0 ' + hPt + ' 0 0 cm /Im0 Do Q'));
      objeto(pag, '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ' + wPt + ' ' + hPt + '] /Resources << /XObject << /Im0 ' + img + ' 0 R >> >> /Contents ' + cont + ' 0 R >>');
    });
    var total = 4 + 3 * n, inicioXref = pos;
    emitir('xref\n0 ' + total + '\n0000000000 65535 f \n');
    for (var k = 1; k < total; k++) emitir(('0000000000' + offs[k]).slice(-10) + ' 00000 n \n');
    emitir('trailer\n<< /Size ' + total + ' /Root 1 0 R /Info 3 0 R >>\nstartxref\n' + inicioXref + '\n%%EOF\n');
    var out = new Uint8Array(pos), at = 0;
    partes.forEach(function (b) { out.set(b, at); at += b.length; });
    return out;
  }

  // Las hojas en el orden del libro: portada, paginas por su lugar en el plan, cierre. Las que no tienen foto o vinieron con error igual entran (una hoja gris es mejor que un libro corto).
  function ordenarHojas(hojas) {
    var peso = function (h) { return h.esPortada ? -1 : h.esCierre ? 1e9 : (Number.isInteger(h.planIdx) ? h.planIdx : 1e8); };
    return (hojas || []).filter(function (h) { return h && h.html; }).slice().sort(function (a, b) { return peso(a) - peso(b); });
  }

  return { ANCHO: ANCHO, ALTO: ALTO, rasterizar: rasterizar, armarPdf: armarPdf, ordenarHojas: ordenarHojas, htmlDeHoja: htmlDeHoja, svgDeHoja: svgDeHoja, aXhtml: aXhtml, esJpeg: esJpeg, tituloPdf: tituloPdf };
});
