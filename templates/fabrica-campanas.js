// La ventana «Programar campañas en Facebook» de /fabrica (paso 4 de la guia de lanzamiento). Habla con /api/fabrica-campanas (functions/api/fabrica-campanas.js).
// Es CODIGO que Build-Suite.ps1 pega dentro de un script de la pagina: no debe traer el cierre de un script, ni la apertura de un comentario HTML, ni nombrar marcadores del build.
// Todo texto que llega del servidor (nombres de campañas, cuentas, reglas, errores de Facebook) se escapa con esc() antes de ir a innerHTML.
(function () {
  'use strict';
  var V = { ventana: Math.random().toString(36).slice(2, 12), clave: '', id: '', pais: '', nombre: '', plan: null, rol: '', textos: null, checks: null, comprobando: false, corriendo: false, mensaje: '', alTerminar: null, abierta: false, progreso: {} };
  var ESTADO_TXT = { pendiente: 'En cola', video: 'Facebook está procesando el video', armando: 'Armando la campaña', lista: 'Lista en pausa', frenada: 'En pausa: no la enciendan', error: 'Falló' };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]; }); }
  function raiz() {
    var r = document.getElementById('fc-raiz');
    if (!r) { r = document.createElement('div'); r.id = 'fc-raiz'; document.body.appendChild(r); }
    return r;
  }
  function estilos() {
    if (document.getElementById('fc-estilos')) return;
    var s = document.createElement('style'); s.id = 'fc-estilos';
    s.textContent = [
      '.fc-datos{display:grid;grid-template-columns:minmax(0,1fr);gap:6px;margin:0 0 12px}',
      '.fc-dato{display:flex;flex-wrap:wrap;gap:4px 10px;padding:8px 11px;border-radius:10px;background:#040b1b;border:1px solid #112247;min-width:0}',
      '.fc-dato span{color:#7e93c5;font-size:12px;flex:0 0 112px}.fc-dato b{flex:1 1 180px;min-width:0;font-size:13px;overflow-wrap:anywhere}',
      '.fc-caja{padding:9px 12px;border-radius:10px;margin:8px 0;font-size:13px;overflow-wrap:anywhere}',
      '.fc-mal{background:#2a0b18;border:1px solid #6b1a36;color:#ffc2d3}.fc-ojo{background:#241d06;border:1px solid #6b5514;color:#ffe3a3}.fc-bien{background:#06231a;border:1px solid #11583f;color:#b8f5dc}',
      '.fc-caja ul{margin:4px 0 0;padding-left:18px}.fc-caja li{margin:2px 0}',
      '.fc-camp{display:flex;flex-direction:column;gap:7px;margin:6px 0 12px}',
      '.fc-fila{padding:9px 11px;border-radius:11px;background:#040b1b;border:1px solid #112247;min-width:0}',
      '.fc-fila code{display:block;font-size:12px;color:#d3daeb;overflow-wrap:anywhere;white-space:normal}',
      '.fc-fila small{display:block;color:#7e93c5;font-size:12px;margin-top:3px;overflow-wrap:anywhere}',
      '.fc-chip{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:999px;margin-top:5px;background:#12254e;color:#cfe0ff}',
      '.fc-chip.ok{background:#0e4a35;color:#b8f5dc}.fc-chip.no{background:#5a1430;color:#ffc2d3}.fc-chip.va{background:#1b3f95;color:#fff}.fc-chip.ojo{background:#5a4510;color:#ffe7a8}',
      '.fc-campo{display:flex;flex-direction:column;gap:4px;margin:0 0 10px;min-width:0}',
      '.fc-campo label{font-size:12.5px;color:#bbc7e1}.fc-campo small{color:#7e93c5;font-size:11.5px}',
      '.fc-campo input,.fc-campo textarea{box-sizing:border-box;width:100%;min-height:40px;padding:8px 10px;border-radius:9px;border:1px solid #1a2f66;background:#040b1b;color:#e6ecff;font:inherit;font-size:14px}',
      '.fc-campo textarea{min-height:150px;resize:vertical}',
      '.fc-h{font-size:13px;color:#9fb3e6;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.04em}',
      '.fc-fila .btn{margin-top:7px;min-height:40px}',
      '@media (max-width:520px){.fc-dato span{flex-basis:100%}}'
    ].join('\n');
    document.head.appendChild(s);
  }
  function pedir(url, cuerpo) {
    var o = { credentials: 'same-origin', cache: 'no-store' };
    if (cuerpo) { o.method = 'POST'; o.headers = { 'Content-Type': 'application/json' }; o.body = JSON.stringify(cuerpo); }
    return fetch(url, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || !j || j.ok === false) { var e = new Error((j && j.error) || ('El servidor contestó ' + r.status)); e.status = r.status; e.plan = j && j.plan; throw e; }
        return j;
      });
    });
  }
  function base() { return '/api/fabrica-campanas'; }
  function nuevas() { return V.plan ? V.plan.campanas.filter(function (c) { return c.listo; }) : []; }
  // a medias = frenada, fallida, o quieta hace mas de 1 min (si se movio hace menos, otra ventana la esta armando: no se ofrece «Seguir»)
  function terminal(rg) { return rg.estado === 'lista' || rg.estado === 'frenada' || rg.estado === 'error'; }
  function quieta(rg) { return terminal(rg) || Date.now() - Date.parse(rg.actualizado || 0) > 60000; }
  function pendientes() { return V.plan ? V.plan.campanas.filter(function (c) { return c.registro && c.registro.estado !== 'lista' && quieta(c.registro); }) : []; }
  function horaColombia(plan) { if (!plan || !plan.inicio || !plan.inicio.ms || plan.inicio.offset === -5) return ''; try { return ' · ' + new Date(plan.inicio.ms).toLocaleTimeString('es-CO', { timeZone: 'America/Bogota', hour: 'numeric', minute: '2-digit' }) + ' en Colombia'; } catch (e) { return ''; } }
  function diaLargo(plan) {
    if (!plan || !plan.inicio) return '';
    var p = String(plan.inicio.fecha || '').split('-'), d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], 12));
    var dias = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
    return isNaN(d) ? plan.inicio.dia : dias[d.getUTCDay()] + ' ' + plan.inicio.dia;
  }

  function pintar() {
    if (!V.abierta) return;
    var r = raiz(), plan = V.plan, h = '';
    h += '<div class="modal-bg" data-fc-fondo><div class="modal" role="dialog" aria-modal="true" aria-label="Programar campañas en Facebook">';
    h += '<div class="m-cab"><b>Programar en Facebook · ' + esc(V.pais) + '</b><button class="btn" data-fc="cerrar" aria-label="Cerrar" style="min-width:40px;padding:0">✕</button></div><div class="m-cuerpo">';
    if (!plan) {
      h += V.mensaje ? '<div class="fc-caja fc-mal">' + esc(V.mensaje) + '</div>' : '<p class="girando">Revisando la cuenta, el número, los videos y las reglas…</p>';
      h += '</div></div></div>'; r.innerHTML = h; return;
    }
    var nv = nuevas(), pres = plan.presupuesto;
    h += '<p class="sub2" style="margin:0 0 10px">Una campaña por anuncio aprobado, copiada de la última campaña de este número: misma cuenta, página, público y objetivo. Cambian el video, el texto, el nombre y la hora. Nacen EN PAUSA, con el arranque a las 5 a. m.: ustedes las revisan en Ads Manager y las encienden. Desde que se encienden, las reglas las cuidan.</p>';
    h += '<div class="fc-datos">';
    if (plan.numero) h += dato('Número', (plan.numero.wpp ? 'WPP #' + plan.numero.wpp + ' · ' : '') + plan.numero.nombre + (plan.numero.tel4 ? ' …' + plan.numero.tel4 : '') + (plan.numero.estado === 'conectado' ? ' · conectado ahora' : plan.numero.estado === 'caido' ? ' · DESCONECTADO' : plan.numero.estado ? ' · sin confirmar' : '') + (plan.numero.destino ? ' · el anuncio manda al …' + plan.numero.destino : ''));
    if (plan.cuenta) h += dato('Cuenta', plan.cuenta.nombre || plan.cuenta.id);
    if (plan.modelo) h += dato('Copia de', plan.modelo.campana);
    if (pres) h += dato('Presupuesto', pres.texto + ' al día cada una' + (nv.length ? ' · ' + nv.length + ' campaña' + (nv.length === 1 ? '' : 's') + ' = ' + pres.totalDia + ' al día' : ''));
    if (plan.inicio) h += dato('Arrancan al encenderlas', diaLargo(plan) + ' a las 5:00 a. m. (hora de la cuenta)' + horaColombia(plan));
    if (plan.reglas && plan.reglas.length) h += dato('Las cuidan', plan.reglas.map(function (x) { return '«' + x.nombre + '»'; }).join(', '));
    h += '</div>';
    if (plan.bloqueos && plan.bloqueos.length) h += '<div class="fc-caja fc-mal"><b>Falta esto antes de programar:</b><ul>' + plan.bloqueos.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>';
    if (plan.avisos && plan.avisos.length) h += '<div class="fc-caja fc-ojo"><ul>' + plan.avisos.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>';
    if (V.checks) {
      var malos = V.checks.filter(function (c) { return !c.ok; });
      h += malos.length ? '<div class="fc-caja fc-mal"><b>Facebook lo revisó (sin crear nada) y no lo aceptaría:</b><ul>' + malos.map(function (c) { return '<li>' + esc(c.que) + ': ' + esc(c.msg) + '</li>'; }).join('') + '</ul></div>'
        : '<div class="fc-caja fc-bien">Facebook revisó la campaña y el conjunto sin crear nada: los aceptaría.</div>';
    } else if (V.comprobando) h += '<p class="girando">Facebook está revisando la campaña sin crear nada…</p>';

    h += '<div class="fc-h">Campañas</div><div class="fc-camp">';
    plan.campanas.forEach(function (c) {
      var rg = c.registro, prog = V.progreso[c.anuncio];
      h += '<div class="fc-fila"><code>' + esc(c.nombre || c.anuncio) + '</code>';
      if (rg) {
        var cls = rg.estado === 'lista' ? (rg.nota ? 'ojo' : 'ok') : (rg.estado === 'error' || rg.estado === 'frenada') ? 'no' : 'va';
        h += '<span class="fc-chip ' + cls + '">' + esc(ESTADO_TXT[rg.estado] || rg.estado) + (rg.estado === 'lista' && rg.dia ? ' · al encenderla arranca el ' + esc(rg.dia) + ' a las 5 a. m.' : '') + '</span>';
        var ult = rg.pasos && rg.pasos.length ? rg.pasos[rg.pasos.length - 1].txt : '';
        if (rg.estado === 'error' && rg.error) h += '<small>' + esc(rg.error) + '</small>';
        else if (rg.nota) h += '<small>' + esc(rg.nota) + '</small>';
        else if (ult) h += '<small>' + esc(ult) + '</small>';
        if (prog) h += '<small>' + esc(prog) + '</small>';
        // nacen en pausa: el enlace lleva a la campaña en Ads Manager para revisarla y encenderla (solo ids de puros digitos)
        var cid = String((rg.ids && rg.ids.campana) || ''), cta = String(rg.cuenta || '');
        if (rg.estado === 'lista' && /^[0-9]{6,25}$/.test(cid) && /^[0-9]{6,25}$/.test(cta)) h += '<small><a href="https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=' + cta + '&amp;selected_campaign_ids=' + cid + '" target="_blank" rel="noopener" style="color:#9fc0ff;display:inline-block;padding:12px 0">' + (rg.nota ? 'Revisarla en Ads Manager' : 'Revisarla y encenderla en Ads Manager') + '</a></small>';
        if (rg.estado !== 'lista' && !V.corriendo) h += quieta(rg) ? '<button class="btn" data-fc="seguir" data-anuncio="' + esc(c.anuncio) + '">Seguir</button>' : '<small>Otra ventana la está armando ahora mismo.</small>';
      } else if (c.listo) {
        h += '<span class="fc-chip ok">Lista para crear · video ok</span>';
        // el enlace solo en las que SI se van a publicar
        if (/^https:\/\/drive\.google\.com\/file\/d\/[A-Za-z0-9_-]+\/view$/.test(String(c.drive || ''))) h += '<small><a href="' + esc(c.drive) + '" target="_blank" rel="noopener" style="color:#9fc0ff;display:inline-block;padding:12px 0">Ver el video que se va a publicar</a></small>';
      } else if (c.motivo) h += '<span class="fc-chip no">No se crea</span><small>' + esc(c.motivo) + '</small>';
      h += '</div>';
    });
    h += '</div>';

    var t = V.textos;
    if (nv.length && t) {
      h += '<div class="fc-h">Texto del anuncio</div>';
      h += campo('mensaje', 'Texto principal', '<textarea data-fc-t="mensaje" maxlength="2000">' + esc(t.mensaje) + '</textarea>', 'Sale de la promesa y los bonos de la oferta: revísalo y ajústalo.');
      h += campo('titulo', 'Título', '<input data-fc-t="titulo" maxlength="120" value="' + esc(t.titulo) + '">', '');
      h += campo('descripcion', 'Descripción corta', '<input data-fc-t="descripcion" maxlength="120" value="' + esc(t.descripcion) + '">', '');
      if (!plan.clave) h += campo('clave', 'Palabra clave del flujo de venta', '<input data-fc-clave maxlength="30" value="' + esc(V.clave) + '">', 'La del inicio del flujo de Venta en ZapData. Sin ella no se programa.');
      h += campo('autollenado', 'Mensaje que el cliente manda por WhatsApp', '<input data-fc-t="autollenado" maxlength="160" value="' + esc(t.autollenado) + '">', plan.clave ? 'Tiene que llevar la palabra clave «' + plan.clave + '»: con ella arranca el flujo de venta.' : 'Pon la palabra con la que arranca el flujo de venta.');
      h += campo('bienvenida', 'Saludo en WhatsApp', '<input data-fc-t="bienvenida" maxlength="160" value="' + esc(t.bienvenida) + '">', '');
    }
    if (V.mensaje) h += '<div class="fc-caja ' + (/^✓/.test(V.mensaje) ? 'fc-bien' : 'fc-mal') + '">' + esc(V.mensaje) + '</div>';
    h += '</div><div class="m-pie">';
    var bloqueado = estaBloqueado();
    if (nv.length) h += '<button class="btn prim" data-fc="programar"' + (bloqueado || V.corriendo ? ' disabled' : '') + '>' + (V.corriendo ? 'Creando…' : 'Crear ' + nv.length + ' campaña' + (nv.length === 1 ? '' : 's') + ' en pausa' + (pres ? ' · ' + esc(pres.totalDia) + ' al día' : '')) + '</button>';
    if (pendientes().length && !V.corriendo) h += '<button class="btn" data-fc="seguir-todas">Seguir con las que quedaron a medias</button>';
    h += '<button class="btn" data-fc="cerrar">' + (V.corriendo ? 'Seguir en segundo plano' : 'Cerrar') + '</button>';
    h += '</div></div></div>';
    // no perder el foco ni el cursor de lo que la persona escribe si llega un repintado (la revision de Facebook termina, avanza una campaña)
    var act = document.activeElement, k = act && act.getAttribute && r.contains(act) ? act.getAttribute('data-fc-t') : null, ini = k ? act.selectionStart : 0, fin = k ? act.selectionEnd : 0;
    var desp = r.querySelector('.modal-bg') ? r.querySelector('.modal-bg').scrollTop : 0;
    r.innerHTML = h;
    if (r.querySelector('.modal-bg')) r.querySelector('.modal-bg').scrollTop = desp;
    if (k) { var n = r.querySelector('[data-fc-t="' + k + '"]'); if (n) { n.focus(); try { n.setSelectionRange(ini, fin); } catch (e) { /* sin cursor */ } } }
  }
  // la misma condicion para pintar el boton y para reactivarlo al escribir la palabra clave
  function estaBloqueado() { var plan = V.plan; return !plan || !!(plan.bloqueos && plan.bloqueos.length) || !!(V.checks && V.checks.some(function (c) { return !c.ok; })) || (!plan.clave && !String(V.clave).trim()); }
  function dato(et, v) { return '<div class="fc-dato"><span>' + esc(et) + '</span><b>' + esc(v) + '</b></div>'; }
  function campo(k, et, ctl, ayuda) { return '<div class="fc-campo"><label>' + esc(et) + '</label>' + ctl + (ayuda ? '<small>' + esc(ayuda) + '</small>' : '') + '</div>'; }

  function cargar() {
    V.plan = null; V.checks = null; V.mensaje = ''; pintar();
    return pedir(base() + '?id=' + encodeURIComponent(V.id) + '&pais=' + encodeURIComponent(V.pais)).then(function (j) {
      V.plan = j.plan; V.rol = j.rol;
      if (!V.textos && j.plan.textos) V.textos = JSON.parse(JSON.stringify(j.plan.textos));
      pintar();
      if (!j.plan.bloqueos.length && nuevas().length) comprobar();
    }).catch(function (e) { V.mensaje = e.message; pintar(); });
  }
  function comprobar() {
    V.comprobando = true; pintar();
    return pedir(base(), { accion: 'comprobar', id: V.id, pais: V.pais }).then(function (j) { V.checks = j.checks || []; })
      .catch(function (e) { V.checks = [{ que: 'Revisión de Facebook', ok: false, msg: e.message }]; })
      .then(function () { V.comprobando = false; pintar(); });
  }
  function esperar(ms) { return new Promise(function (ok) { setTimeout(ok, ms); }); }
  // Lleva UNA campaña hasta que queda lista, frenada o fallida. La pantalla pregunta cada 5 s mientras Facebook procesa el video.
  function correrUna(anuncio, vueltas) {
    vueltas = vueltas || 0;
    if (vueltas > 300) return Promise.resolve();
    return pedir(base(), { accion: 'avanzar', id: V.id, pais: V.pais, anuncio: anuncio, ventana: V.ventana }).then(function (j) {
      ponerRegistro(anuncio, j.campana);
      var c = j.campana || {};
      V.progreso[anuncio] = j.ocupada ? 'Otra ventana la está armando: espero a que termine.' : c.estado === 'video' ? 'Facebook está procesando el video… (esto toma unos minutos)' : '';
      pintar();
      if (c.estado === 'lista' || c.estado === 'frenada' || c.estado === 'error') return;
      return esperar(j.esperar ? 5000 : 300).then(function () { return correrUna(anuncio, vueltas + 1); });
    }).catch(function (e) {
      V.progreso[anuncio] = e.message; if (e.status !== 502) return pintar();
      pintar(); return esperar(4000).then(function () { return vueltas < 300 ? correrUna(anuncio, vueltas + 5) : null; });
    });
  }
  function ponerRegistro(anuncio, reg) {
    if (!V.plan || !reg) return;
    V.plan.campanas.forEach(function (c) { if (c.anuncio === anuncio) { c.registro = reg; c.listo = false; c.nombre = reg.nombre || c.nombre; } });
  }
  function correrVarias(lista) {
    V.corriendo = true; pintar();
    var cadena = Promise.resolve();
    lista.forEach(function (a) { cadena = cadena.then(function () { return correrUna(a); }); });
    return cadena.then(function () {
      V.corriendo = false;
      var regs = V.plan.campanas.filter(function (c) { return c.registro; }).map(function (c) { return c.registro; });
      var ok = regs.filter(function (x) { return x.estado === 'lista'; }).length, malas = regs.length - ok, ojos = regs.filter(function (x) { return x.estado === 'lista' && x.nota; }).length;
      V.mensaje = malas ? 'Quedaron ' + ok + ' lista' + (ok === 1 ? '' : 's') + ' en pausa y ' + malas + ' sin terminar: mira el motivo y pulsa «Seguir».' : '✓ Listo: ' + ok + ' campaña' + (ok === 1 ? '' : 's') + ' creada' + (ok === 1 ? '' : 's') + ' EN PAUSA en Facebook. ' + (ojos ? ojos + ' con «Ojo» (en amarillo): léanlo antes de encender' + (ojos === 1 ? 'la' : 'las') + '. ' : '') + 'Revísenla' + (ok === 1 ? '' : 's') + ' en Ads Manager y enciéndanla' + (ok === 1 ? '' : 's') + '.';
      pintar();
      if (typeof V.alTerminar === 'function') { try { V.alTerminar(); } catch (e) { /* la ficha se refresca sola despues */ } }
    });
  }
  function programar() {
    var plan = V.plan, nv = nuevas(), pres = plan.presupuesto;
    if (!nv.length || V.corriendo) return;
    var msg = 'Se van a crear ' + nv.length + ' campaña' + (nv.length === 1 ? '' : 's') + ' EN PAUSA en la cuenta «' + (plan.cuenta && plan.cuenta.nombre) + '», con ' + (pres ? pres.texto : '') + ' al día cada una (' + (pres ? pres.totalDia : '') + ' al día en total cuando las enciendan). Nacen EN PAUSA: no gastan nada hasta que ustedes las revisen y las enciendan en Ads Manager; al encenderlas arrancan el ' + diaLargo(plan) + ' a las 5:00 a. m. (si las encienden después de esa hora, arrancan en ese momento). ¿Las creo?';
    if (!window.confirm(msg)) return;
    V.mensaje = ''; V.corriendo = true; pintar();
    pedir(base(), { accion: 'empezar', id: V.id, pais: V.pais, textos: V.textos, huella: plan.huella, clave: String(V.clave || '').trim() }).then(function (j) {
      (j.campanas || []).forEach(function (c) { ponerRegistro(c.anuncio, c); });
      V.corriendo = false;
      return correrVarias((j.campanas || []).map(function (c) { return c.anuncio; }));
    }).catch(function (e) {
      V.corriendo = false; V.mensaje = e.message;
      if (e.plan) V.plan = e.plan;
      pintar();
    });
  }

  document.addEventListener('click', function (ev) {
    var b = ev.target && ev.target.closest ? ev.target.closest('[data-fc],[data-fc-fondo]') : null;
    if (!b || !V.abierta) return;
    if (b.hasAttribute('data-fc-fondo') && ev.target !== b) return;
    var a = b.getAttribute('data-fc');
    if (b.hasAttribute('data-fc-fondo') || a === 'cerrar') { V.abierta = false; raiz().innerHTML = ''; return; }
    if (a === 'programar') return programar();
    if (a === 'seguir') return correrVarias([b.getAttribute('data-anuncio')]);
    if (a === 'seguir-todas') return correrVarias(pendientes().map(function (c) { return c.anuncio; }));
  });
  document.addEventListener('input', function (ev) {
    var k = ev.target && ev.target.getAttribute ? ev.target.getAttribute('data-fc-t') : null;
    if (k && V.textos) V.textos[k] = ev.target.value;
    if (ev.target && ev.target.hasAttribute && ev.target.hasAttribute('data-fc-clave')) { V.clave = ev.target.value; var b = document.querySelector('[data-fc="programar"]'); if (b) b.disabled = estaBloqueado() || V.corriendo; }
  });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && V.abierta && !V.corriendo) { V.abierta = false; raiz().innerHTML = ''; } });

  window.FabricaCampanas = {
    // opts: { id, pais, nombre, alTerminar }
    abrir: function (opts) {
      estilos();
      var mismo = V.id === opts.id && V.pais === opts.pais;
      V.id = String(opts.id || ''); V.pais = String(opts.pais || ''); V.nombre = String(opts.nombre || ''); V.alTerminar = opts.alTerminar || null; V.abierta = true;
      if (!mismo) { V.textos = null; V.progreso = {}; V.clave = ''; }         // la palabra clave de otra oferta no pasa a esta
      if (V.corriendo && mismo) { pintar(); return; }
      cargar();
    },
    _estado: function () { return V; }
  };
})();
