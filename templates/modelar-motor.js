// Las cuentas de /modelar-pdf que no necesitan navegador: que papel es, que paginas son espejo o repetidas de otra, donde dice el PDF que no se
// puede revender, que paginas se le mandan al modelo, cuanto va a costar y como se reparte el texto en una pagina.
//
// Son CODIGO, no datos: Build-Suite.ps1 lo inlinea en la pagina por __MODELAR_MOTOR_JS__ y se expone como `ModelarMotor`. En Node lo carga
// scripts/probar-modelar-pdf.mjs, que tambien comprueba que no traiga texto que cierre el script o abra un comentario HTML.
(function (raiz, fabrica) {
  var M = fabrica();
  if (typeof module === 'object' && module && module.exports) module.exports = M; else raiz.ModelarMotor = M;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ------------------------------------------------------------------------------------------------------------------------------------------
  var PAPELES = [
    { nombre: 'Carta', w: 612, h: 792 },
    { nombre: 'A4', w: 595.28, h: 841.89 },
    { nombre: 'Oficio', w: 612, h: 1008 },
    { nombre: 'A5', w: 419.53, h: 595.28 },
    { nombre: 'Media carta', w: 396, h: 612 },
    { nombre: 'A3', w: 841.89, h: 1190.55 },
  ];
  function cm(pt) { return Math.round(pt / 72 * 2.54 * 10) / 10; }
  function nombrePapel(wPt, hPt) {
    var a = Math.min(wPt, hPt), b = Math.max(wPt, hPt);
    for (var i = 0; i < PAPELES.length; i++) {
      var p = PAPELES[i];
      if (Math.abs(a - Math.min(p.w, p.h)) <= 4 && Math.abs(b - Math.max(p.w, p.h)) <= 4) {
        return p.nombre + (wPt > hPt ? ' horizontal' : '');
      }
    }
    return String(cm(wPt)).replace('.', ',') + ' × ' + String(cm(hPt)).replace('.', ',') + ' cm';
  }

  // ------------------------------------------------------------------------------------------------------------------------------------------
  // Paginas espejo y repetidas. Cada pagina llega como una «firma»: su miniatura en gris de 48 x 64 (3.072 valores de 0 a 255, fila por fila).
  // Dos paginas son la MISMA si la diferencia media es baja; son ESPEJO si se parecen mucho mas con una de las dos volteada de izquierda a
  // derecha. Una pagina simetrica (un marco, un fondo liso) se parece igual derecha y volteada: por eso se pide que volteada se parezca bastante
  // mas, y no solo «poco».
  // La diferencia se mide SOLO donde alguna de las dos tiene tinta (gris por debajo de 240). Promediada sobre toda la hoja, dos paginas de
  // texto distinto salian «iguales»: son casi todo blanco y el blanco coincide (paso con la portada y las instrucciones de un PDF real).
  var FW = 48, FH = 64, TINTA = 240;
  function difMedia(a, b, voltear) {
    var s = 0, n = 0, sTodo = 0;
    for (var y = 0; y < FH; y++) {
      for (var x = 0; x < FW; x++) {
        var va = a[y * FW + x], vb = b[y * FW + (voltear ? FW - 1 - x : x)], d = Math.abs(va - vb);
        sTodo += d;
        if (va < TINTA || vb < TINTA) { s += d; n++; }
      }
    }
    // Casi sin tinta en ninguna de las dos: no hay con que comparar, y se usa el promedio de la hoja.
    return n < FW * FH * 0.02 ? sTodo / (FW * FH) : s / n;
  }
  // Una pagina casi en blanco se parece a cualquier otra casi en blanco: esas no se emparejan (contraste = desviacion de sus tonos).
  function contraste(a) {
    var m = 0, i;
    for (i = 0; i < a.length; i++) m += a[i];
    m /= a.length;
    var v = 0;
    for (i = 0; i < a.length; i++) v += (a[i] - m) * (a[i] - m);
    return Math.sqrt(v / a.length);
  }
  function compararPaginas(firmas, umbral) {
    var u = umbral || 10;
    var espejos = [], repetidas = [], par = {};
    for (var j = 0; j < firmas.length; j++) {
      if (!firmas[j] || contraste(firmas[j]) < 6) continue;
      for (var i = 0; i < j; i++) {
        if (!firmas[i] || par[i] != null || contraste(firmas[i]) < 6) continue;
        var igual = difMedia(firmas[i], firmas[j], false);
        var vuelta = difMedia(firmas[i], firmas[j], true);
        if (igual <= u) { repetidas.push({ pagina: j + 1, de: i + 1 }); par[j] = i; break; }
        if (vuelta <= u && igual >= vuelta * 1.8) { espejos.push({ pagina: j + 1, de: i + 1 }); par[j] = i; break; }
      }
    }
    return { espejos: espejos, repetidas: repetidas };
  }

  // ------------------------------------------------------------------------------------------------------------------------------------------
  // La clausula de licencia, en portugues, espanol e ingles. Devuelve la frase donde aparece, recortada, para mostrarla tal cual.
  var LICENCIA = /(revend|revenda|reventa|revender|resal|resell|redistribu|n[ãa]o (?:é|e|sera|será) permitid|no (?:est[áa] )?permitid|proibid|prohibid|direitos autorais|derechos de autor|todos os direitos|todos los derechos|all rights reserved|copyright|uso pessoal|uso personal|personal use|licen[çcs]a|license)/i;
  function buscarLicencia(textos) {
    var hallazgos = [];
    (textos || []).forEach(function (t, i) {
      var s = String(t || '').replace(/\s+/g, ' ');
      var m = LICENCIA.exec(s);
      if (!m) return;
      var ini = Math.max(0, s.lastIndexOf('.', m.index) + 1, m.index - 140);
      var fin = s.indexOf('.', m.index + m[0].length);
      fin = fin === -1 ? Math.min(s.length, m.index + 160) : Math.min(fin + 1, m.index + 220);
      hallazgos.push({ pagina: i + 1, frase: s.slice(ini, fin).trim() });
    });
    return hallazgos;
  }

  // ------------------------------------------------------------------------------------------------------------------------------------------
  // Que paginas ve el modelo: las distintas (sin espejos ni repetidas), hasta `max`, repartidas por todo el PDF si sobran.
  function paginasParaAnalisis(total, apartadas, max) {
    var fuera = {};
    (apartadas || []).forEach(function (n) { fuera[n] = true; });
    var distintas = [];
    for (var n = 1; n <= total; n++) if (!fuera[n]) distintas.push(n);
    var tope = max || 16;
    if (distintas.length <= tope) return distintas;
    var out = [];
    for (var k = 0; k < tope; k++) out.push(distintas[Math.round(k * (distintas.length - 1) / (tope - 1))]);
    return out.filter(function (v, i, a) { return a.indexOf(v) === i; });
  }

  // ------------------------------------------------------------------------------------------------------------------------------------------
  // Cuanto cuesta. precio = USD por imagen del modelo elegido (null si la tabla no lo conoce). El texto se cuenta como un centavo, que es el
  // techo: con Gemini Flash el analisis de 16 paginas cuesta menos.
  function estimar(nArtes, precio, nYaHechas) {
    var faltan = Math.max(0, nArtes - (nYaHechas || 0));
    return { faltan: faltan, imagenes: precio == null ? null : Math.round(faltan * precio * 10000) / 10000, porImagen: precio };
  }
  function dolares(usd) {
    if (usd == null) return 'sin precio conocido';
    if (usd < 0.01) return 'menos de un centavo';
    return 'US$' + usd.toFixed(usd < 1 ? 3 : 2).replace('.', ',');
  }

  // ------------------------------------------------------------------------------------------------------------------------------------------
  // El orden de las paginas del PDF final y su numero, para el indice de la pagina de instrucciones.
  function ordenPaginas(o) {
    var lista = [], n = 0;
    if (o.portada) lista.push({ tipo: 'portada', n: ++n });
    if (o.instrucciones) lista.push({ tipo: 'instrucciones', n: ++n });
    var i;
    for (i = 0; i < o.artes; i++) lista.push({ tipo: 'arte', arte: i, n: ++n });
    if (o.espejo) for (i = 0; i < o.artes; i++) lista.push({ tipo: 'espejo', arte: i, n: ++n });
    if (o.tarjetas) {
      var porHoja = 6, hojas = Math.ceil(o.artes / porHoja);
      for (i = 0; i < hojas; i++) lista.push({ tipo: 'tarjetas', desde: i * porHoja, hasta: Math.min(o.artes, (i + 1) * porHoja), n: ++n });
    }
    return lista;
  }
  function rango(lista, tipo) {
    var ns = lista.filter(function (p) { return p.tipo === tipo; }).map(function (p) { return p.n; });
    if (!ns.length) return '';
    return ns.length === 1 ? 'página ' + ns[0] : 'páginas ' + ns[0] + ' a ' + ns[ns.length - 1];
  }

  // ------------------------------------------------------------------------------------------------------------------------------------------
  // Partir un texto en lineas que quepan. medir(texto) devuelve el ancho con la letra ya puesta; se prueba de `tam` hacia abajo hasta que quepa
  // en `maxLineas`. Devuelve { tam, lineas }.
  function partirLineas(palabras, medir, ancho) {
    var lineas = [], actual = '';
    palabras.forEach(function (p) {
      var prueba = actual ? actual + ' ' + p : p;
      if (!actual || medir(prueba) <= ancho) actual = prueba;
      else { lineas.push(actual); actual = p; }
    });
    if (actual) lineas.push(actual);
    return lineas;
  }
  function ajustarTexto(texto, medirCon, ancho, tamMax, tamMin, maxLineas) {
    var palabras = String(texto || '').trim().split(/\s+/).filter(Boolean);
    if (!palabras.length) return { tam: tamMax, lineas: [] };
    for (var tam = tamMax; tam >= tamMin; tam = Math.floor(tam * 0.94)) {
      var medir = function (t) { return medirCon(t, tam); };
      var lineas = partirLineas(palabras, medir, ancho);
      var cabe = lineas.length <= maxLineas && lineas.every(function (l) { return medir(l) <= ancho; });
      if (cabe) return { tam: tam, lineas: equilibrar(lineas, palabras, medir, ancho) };
    }
    var m = function (t) { return medirCon(t, tamMin); };
    return { tam: tamMin, lineas: partirLineas(palabras, m, ancho).slice(0, maxLineas) };
  }
  // Dos lineas desparejas («Buenos días con / café») se ven mal en un titulo: se busca el corte que deja las dos mas parecidas.
  function equilibrar(lineas, palabras, medir, ancho) {
    if (lineas.length !== 2) return lineas;
    var mejor = lineas, peor = Math.abs(medir(lineas[0]) - medir(lineas[1]));
    for (var k = 1; k < palabras.length; k++) {
      var a = palabras.slice(0, k).join(' '), b = palabras.slice(k).join(' ');
      if (medir(a) > ancho || medir(b) > ancho) continue;
      var d = Math.abs(medir(a) - medir(b));
      if (d < peor) { peor = d; mejor = [a, b]; }
    }
    return mejor;
  }

  // ------------------------------------------------------------------------------------------------------------------------------------------
  // Una frase que va a quedar IMPRESA y parece seguir en portugues (el modelo barato a veces deja el original sin traducir). Solo letras y
  // palabras que el espanol no usa: ã õ ç ê ô â à, y palabras como «com», «você», «lar», «feito», «bom». No se marca «dos», «das», «da» ni
  // «doce», que tambien son espanol. Es un aviso para que alguien la mire, no una correccion.
  // Antes de la palabra no puede ir letra, cifra, punto ni barra: «2do grado» y «tienda.com» no son portugues (lo encontro la revision de
  // seguridad del 2026-11-07). Despues, ni letra ni cifra.
  var LETRA_ES = 'a-z0-9\\u00e1\\u00e9\\u00ed\\u00f3\\u00fa\\u00f1\\u00fc';
  var PORTUGUES = new RegExp('[\\u00e3\\u00f5\\u00e7\\u00ea\\u00f4\\u00e2\\u00e0]|(^|[^' + LETRA_ES + './])' +
    '(com|voc\\u00ea|voce|seu|sua|n\\u00e3o|nao|muito|muita|obrigad[oa]|lar|feito|feita|bom|boa|boas|festas|em|um|uma|meu|minha|nosso|nossa|do|pra|quentinho|cheirinho)' +
    '(?=$|[^' + LETRA_ES + '])', 'i');
  var FELIZ_NATAL = /feliz\s+natal\b/i;   // aparte: «natal» solo tambien es espanol («mi tierra natal»)
  function pareceNoEspanol(texto) {
    var t = String(texto || '');
    return PORTUGUES.test(t) || FELIZ_NATAL.test(t);
  }

  // Un enlace, un usuario o un telefono en lo que se va a imprimir: serian los de OTRO vendedor, porque las frases salen del PDF ajeno.
  // Telefono: siete cifras o mas, con espacios, puntos, guiones o parentesis entre ellas.
  // {1,63} y no +: un nombre de dominio no pasa de 63, y con + un texto de letras y guiones se recorria una y otra vez.
  var CONTACTO = /(https?:\/\/|www\.|wa\.me\/|t\.me\/|bit\.ly\/|linktr\.ee)|\b[a-z0-9-]{1,63}\.(com|net|org|shop|store|site|online|br|co|mx|pt|ar|pe)\b|(^|[^a-z0-9._-])@[a-z0-9._]{3,}|(\+?\d[\s().-]?){7,}/i;
  // Las fechas y los rangos de anos no son telefonos: estos kits traen calendarios («2026-2027», «24-12-2026»).
  var FECHAS = /\b(?:(?:19|20)\d{2}\s*[-\/.]\s*(?:19|20)\d{2}|\d{1,2}[-\/.]\d{1,2}[-\/.]\d{2,4})\b/g;
  function pareceContacto(texto) {
    return CONTACTO.test(String(texto || '').replace(FECHAS, ' '));
  }

  // Encajar una imagen de ancho x alto dentro de una caja, sin deformarla.
  function encajar(w, h, caja) {
    var k = Math.min(caja.w / w, caja.h / h);
    var ww = w * k, hh = h * k;
    return { x: caja.x + (caja.w - ww) / 2, y: caja.y + (caja.h - hh) / 2, w: ww, h: hh };
  }

  // Un nombre de archivo sin caracteres raros.
  function nombreArchivo(t) {
    var s = String(t || 'coleccion').normalize ? String(t || 'coleccion').normalize('NFD').replace(/[̀-ͯ]/g, '') : String(t || 'coleccion');
    s = s.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
    return (s || 'coleccion') + '.pdf';
  }

  return {
    FW: FW, FH: FH, nombrePapel: nombrePapel, cm: cm, compararPaginas: compararPaginas, difMedia: difMedia, contraste: contraste,
    buscarLicencia: buscarLicencia, paginasParaAnalisis: paginasParaAnalisis, estimar: estimar, dolares: dolares,
    ordenPaginas: ordenPaginas, rango: rango, ajustarTexto: ajustarTexto, encajar: encajar, nombreArchivo: nombreArchivo,
    pareceNoEspanol: pareceNoEspanol, pareceContacto: pareceContacto,
  };
});
