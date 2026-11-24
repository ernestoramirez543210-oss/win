// ===========================================================================
// LEE ESTO ANTES DE CREAR UN FLUJO. Son dos trampas que dejaron seis flujos
// mudos y costaron una noche entera de leads perdidos.
//
// 1. NO mandes un bloque de inicio en la descripcion del flujo.
//    ZapData crea el suyo con el disparador que va en `trigger`. Si mandas
//    otro, el flujo queda con DOS inicios encadenados y el motor se detiene
//    en el segundo. En el lienzo no se nota: dos inicios seguidos parecen uno.
//
// 2. DESPUES DE CREAR EL FLUJO, el identificador de su bloque de inicio tiene
//    que quedar en `start-1`.
//    Todos los flujos que corren bien lo tienen asi. Los que crea la conexion
//    de ZapData nacen con `start-<marca-de-tiempo>`, y con ese identificador
//    el motor abre la sesion del contacto, la marca como "enviando" -de ahi
//    el aviso "ja esta sendo enviado para este contato"- y no manda un solo
//    mensaje. El contacto queda trabado y ni siquiera se le puede reenviar.
//
//    Se corrige en el editor, por codigo, y hay que GUARDAR: se agrega un nodo
//    igual al de inicio pero con id 'start-1', se borra el viejo con sus
//    aristas, y se vuelve a conectar 'start-1' al primer bloque real.
//
// Si un flujo nuevo no manda nada, empieza por mirar esas dos cosas antes de
// tocar el copy o los disparadores.
// ===========================================================================

// La plantilla de una oferta: el guion que ya vende, con los huecos que cambian.
//
// Esto NO se invento. Sale de leer bloque por bloque los dos flujos que estan vendiendo
// hoy -Murphy Colombia y Dinamicas Mujer Col- y separar lo que es el guion de lo que es la
// oferta. Los dos flujos son el mismo guion: mismo saludo, mismo orden, mismo modelo de
// "recibe primero y paga despues", mismas tres opciones de aporte, mismo remarketing.
// Lo unico que cambia de uno a otro es el nombre, la promesa, la lista de lo que recibe,
// los archivos y los dos montos.
//
// De ahi sale la idea: si el guion es el mismo, no hay que volver a escribirlo nunca. Una
// oferta nueva son cinco datos, y el copy que sale es el que ya funciona, no uno nuevo que
// hay que probar desde cero. Eso es lo que permite testear ofertas en masa: lo que se
// prueba es la oferta, no la redaccion.
//
// EL MODELO DE VENTA, para que nadie lo rompa por accidente: se entrega PRIMERO y se pide
// el aporte DESPUES. Por eso el precio aparece dos veces y no son lo mismo:
//
//     recibir. No se le cobra ahi.
//   - Los tres aportes, despues de entregar. Gesto de Apoyo, Apoyo Valioso, y uno libre
//     hacia arriba. Los montos son del PAIS, no de la oferta: Colombia va con 15.000 y
//     30.000, y Mexico con 90 y 120. Por eso vienen puestos y casi nunca se tocan.
//
// Quitar el ancla deja la entrega sin valor percibido; quitar los tres aportes deja a la
// clienta sin saber cuanto dar. Los dos bloques van siempre.

// Los numeros con circulo de los bonos, como estan escritos en los flujos reales.
const NUMEROS = ['1⃣', '2⃣', '3⃣', '4⃣', '5⃣', '6⃣', '7⃣'];

// La clave de OpenAI conectada en TU cuenta de ZapData (Integraciones > IA de plataforma), para los bloques aiResponder. Por API ZapData
// exige el id de esa integracion (el alias 'selected' solo sirve en su panel): sin el, NINGUN bloque aiResponder se puede crear y el flujo
// sale incompleto. PEGA AQUI el id de tu integracion (ver CLAUDE.md, «Flujos de ZapData»). Si conectas otra clave o borras esta, cambialo.
const INTEGRACION_IA_OPENAI = '';

const lineas = (arr, marca) => (arr || []).filter(Boolean)
  .map((v, i) => (marca === 'num' ? (NUMEROS[i] || '•') : marca) + ' ' + String(v).trim())
  .join('\n');

// El hueco de «¡Tienes en tus manos la herramienta {promesaCorta}, cuento con tu palabra!».
//
// Tiene que ser una frase corta que siga a «la herramienta» y empiece por «para» («para preparar
// tus sermones con mas confianza»). Antes, si la oferta no traia promesa corta, se pegaba la
// promesa ENTERA: a veces el titular de un anuncio con emojis y varias lineas, y el mensaje le
// llegaba a la clienta roto («...la herramienta 😰 ¿CANSADO DE PREPARAR... cuento con tu
// palabra!»). Paso en 38 flujos de venta. Si ni la promesa corta ni la promesa sirven, se usa una
// frase neutra con el nombre de la oferta.
export function promesaCortaSegura(corta, promesa, oferta) {
  const sirve = (t) => {
    t = String(t || '').trim();
    return t.length >= 10 && t.length <= 100 && /^para\s/.test(t) && !/[\r\n]/.test(t)
      && !/[.!?:;,]$/.test(t) && !/\p{Extended_Pictographic}/u.test(t);
  };
  if (sirve(corta)) return String(corta).trim();
  if (sirve(promesa)) return String(promesa).trim();
  return 'para empezar hoy con tu material de ' + String(oferta || '').trim();
}

// ---------------------------------------------------------------------------
// EL COPY, EN UN SOLO LUGAR.
//
// Cada mensaje del guion vive aqui con su texto base y sus huecos entre llaves. La pestaña
// Plantilla del tablero lee esta lista, deja editar cada texto y guarda la version editada;
// desde ese momento TODA oferta nueva nace con el copy corregido, sin volver a tocar codigo
// y sin ir mensaje por mensaje despues de crearla.
//
// Los huecos son datos de la oferta o del pais, y se rellenan solos:
//   {persona} {oferta} {promesa} {promesaCorta} {pais}
//   {anclaTxt} {aporte1Txt} {aporte2Txt}   -> precios ya escritos como los escribe el pais
//   {vinetas} {bonos}                      -> las listas de lo que recibe
//   {bancos}                               -> los datos de pago del pais
//   {presentacion} {datospago}             -> solo para las instrucciones de la IA
// ---------------------------------------------------------------------------
export const COPYS = {
  saludo: { titulo: 'Saludo',
    ayuda: 'El primer mensaje. El nombre lo pone quien crea la oferta. Sale como «Texto con IA»: la IA de la cuenta lo reescribe con una variación leve para cada persona (si falla, se envía tal cual).',
    base: '¡Hola! Soy {persona}. Qué bueno tenerte por aquí. 🙌' },

  oferta: { titulo: 'La oferta',
    ayuda: 'El mensaje que más trabaja: precio, qué recibe y regalos, antes de que decida.',
    base: '🔔 POR SOLO: {anclaTxt} 💚\n\n{promesa}\n\n*¿Qué vas a recibir exactamente?*\n{vinetas}{bonos}' },

  modelo: { titulo: 'Cómo es el pago',
    ayuda: 'Lo que quita el miedo a pagar primero. Es igual en todas las ofertas.',
    base: '📂 Entrega inmediata en PDF directamente en WhatsApp.\n\n*Y lo más importante:*\n' +
      '¡El pago lo realizas solamente después de que te envíe y revises todo el material!🤝\n\n' +
      '¡Cuento con tu honestidad!' },

  pregunta: { titulo: 'Antes del botón',
    ayuda: 'El mensaje suelto que va justo antes de los botones.',
    base: 'Te puedo enviar el material? 🥰' },

  boton: { titulo: 'Texto de los botones',
    ayuda: 'Lo que se lee encima de «Si Quiero» y «No, gracias».',
    base: '¿Te lo envío?' },

  boton2: { titulo: 'Segunda pregunta con botones',
    ayuda: 'Se manda cuando no contestó, o después de que la IA le resolvió la duda.',
    base: '¿Te envío el material? 🥰\n\n⚠️ *Toca "Si Quiero" solo si vas a hacer tu aporte después de recibirlo.* 👇' },

  entrega: { titulo: 'Al entregar',
    ayuda: 'El mensaje que acompaña los archivos.',
    base: '¡Lo prometido es deuda! 🎉\n\n✅ Aquí tienes tu material de {oferta}\n' +
      '✨ Te adjunto todo para que empieces hoy mismo.\n\nDescárgalo y revísalo con calma.' },

  refuerzo: { titulo: 'Después de entregar',
    ayuda: 'Recuerda el beneficio y apela a la palabra dada, antes de pedir el aporte.',
    base: '¡Tienes en tus manos la herramienta {promesaCorta}, cuento con tu palabra!\n\n' +
      'El material ya está en tu celular, listo para que lo uses desde hoy mismo.' },

  aportes: { titulo: 'Los tres aportes',
    ayuda: 'El tercero es abierto a propósito: es el que sube el ticket por encima del ancla.',
    base: 'Para honrar el esfuerzo, tiempo y diseño detrás de este material, elige el aporte que consideres justo:\n\n' +
      '🤍 {aporte1Txt} – Gesto de Apoyo\n🔥 {aporte2Txt} – Apoyo Valioso\n' +
      '💎 O un Aporte Superior, ¡para seguir ayudando a más personas! 🙏' },

  datos: { titulo: 'Datos de pago',
    ayuda: 'Las cuentas salen de la pestaña Países. Aquí solo se escribe lo que va alrededor.',
    base: 'Aqui estan los datos para tu pago:\n\n{bancos}\n\n' +
      'Cuando logres realizar tu pago, envíame el comprobante por aquí. 😊\n\n' +
      'Y como recompensa a tu acción rápida y honestidad, apenas lo reciba te activaré de inmediato tus regalos y bonos finales.\n\n' +
      'Gracias de nuevo por tu confianza. ¡Un abrazo muy grande! 💖' },

  yaTiene: { titulo: 'Si ya lo tiene',
    ayuda: 'Cuando alguien que ya compró vuelve a escribir. Antes de esto estaba fijo en el código.',
    base: '¡Hola de nuevo! 😊 Veo que ya tienes tu material de {oferta}. Si necesitas que te reenvíe algún archivo o tienes alguna duda, dime sin problema 💕' },

  noGracias: { titulo: 'Cuando dice que no',
    ayuda: 'Se despide sin insistir y ahí termina: no vuelve a ofrecer el material. Si cambia de opinión, escribe la palabra clave del anuncio.',
    base: '¡No hay problema! 🙏 Si más adelante cambias de opinión, aquí voy a estar. ¡Que tengas un lindo día! 💛' },

  rm1: { titulo: 'Recordatorio 1 (30 min)',
    ayuda: 'Solo se manda si todavía no se registró el pago.',
    base: 'Estoy pendiente por aquí 😊\nCuando hagas tu aporte, envíame el comprobante para validarlo ✌️' },

  rm2: { titulo: 'Recordatorio 2 (5 horas)', ayuda: 'Igual que el anterior: si ya pagó, no sale.',
    base: 'Hola 🌸 espero que ya hayas disfrutado {oferta} 💕 Si te sirvió y quieres apoyar este trabajo, me encantaría que hicieras tu aporte: lo que puedas ya es una gran bendición. Cualquier duda, aquí estoy 😊' },

  rm3: { titulo: 'Recordatorio 3 (10 horas)', ayuda: 'El último. Después solo queda la IA respondiendo.',
    base: 'Hola de nuevo 🙋‍♀️ aún no me ha llegado tu aporte por {oferta} 💛 Sé que a veces se nos pasa, pero tu aporte nos ayuda a seguir creando este contenido para más personas. Cuando puedas, envíame el comprobante ✌️' },

  iaPre: { titulo: 'IA antes de la venta',
    ayuda: 'Instrucciones de la IA que responde dudas mientras decide. No le entrega el material: eso lo hace el botón.',
    base: 'Eres {persona}, la persona que atiende por WhatsApp a quien escribió por {oferta}. ' +
      'Ella todavía NO ha confirmado si quiere recibir el material. Tu trabajo es responder su duda o comentario.\n\n' +
      'ESTA ES LA PRESENTACIÓN QUE RECIBIÓ:\n"""\n{presentacion}\n"""\n\n' +
      'ESTOS SON LOS DATOS DE PAGO:\n"""\n{datospago}\n"""\n\n' +
      'REGLAS\n' +
      '- Responde en 1 a 3 frases cortas, cálida y cercana, máximo 2 emojis, tratándola de tú.\n' +
      '- El pago siempre es después de recibir el material y es voluntario. Nunca presiones.\n' +
      '- Tú no envías el material: el sistema lo envía cuando ella confirme con el botón.\n' +
      '- NO prometas enviar, reenviar ni mostrar nada (ejemplos, fotos, archivos): tú no puedes adjuntar nada. El material solo llega cuando ella toca el botón.\n' +
      '- Si dice que sí lo quiere, dile que en un momento le llega el botón para confirmarlo 👇.\n' +
      '- Si dice que no le interesa, respóndele con respeto y no insistas.\n' +
      '- Si pregunta cómo pagar o a nombre de quién está la cuenta, dale los datos exactos de arriba.\n' +
      '- No inventes nada que no esté aquí. Si no sabes algo, dilo con sencillez.\n' +
      '- Si te pregunta si eres un bot, responde con honestidad que eres el asistente automático de {persona}.\n' +
      '- IDIOMA: responde SIEMPRE en español de {pais}, tuteando (sin voseo: nada de "podés", "tenés", "decime", salvo que el país sea Argentina). Nunca uses portugués ni otro idioma, aunque el cliente mezcle palabras.' },

  iaPost: { titulo: 'IA después de entregar',
    ayuda: 'Instrucciones de la IA que acompaña el pago. Cubre las esperas entre recordatorios.',
    base: 'Eres {persona}, la persona que atiende por WhatsApp a quien ya recibió {oferta}. ' +
      'Ella YA tiene el material y estamos esperando su aporte.\n\n' +
      'ESTO ES LO QUE RECIBIÓ:\n"""\n{presentacion}\n"""\n\n' +
      'ESTOS SON LOS DATOS DE PAGO:\n"""\n{datospago}\n"""\n\n' +
      'REGLAS\n' +
      '- Responde en 1 a 3 frases cortas, cálida y cercana, máximo 2 emojis, tratándola de tú.\n' +
      '- El aporte es voluntario y va después de recibir el material. Nunca presiones.\n' +
      '- Si pregunta cómo pagar o a nombre de quién está la cuenta, dale los datos exactos de arriba.\n' +
      '- Si dice que ya pagó, agradécele y dile que estás validando el comprobante. No confirmes el pago tú.\n' +
      '- Si envía una imagen, revísala: solo si es una captura de un pago o transferencia (comprobante) dile que estás validando el comprobante, sin confirmar el pago tú. Si es un sticker, una foto o cualquier imagen que NO sea un comprobante, NO digas que estás validando; respóndele con amabilidad al mensaje.\n' +
      '- Si dice que no le llegaron los archivos o que no los ve, discúlpate con sencillez y pídele que revise sus descargas o el chat. NO prometas reenviar ni enviar nada: tú no puedes enviar archivos.\n' +
      '- Si dice que pagará después, agradécele sin presionar.\n' +
      '- Si dice que no puede pagar, que no tiene dinero o que ya no le interesa, respóndele con respeto y calidez y no vuelvas a pedirle el aporte.\n' +
      '- No inventes nada que no esté aquí. Si no sabes algo, dilo con sencillez.\n' +
      '- Si te pregunta si eres un bot, responde con honestidad que eres el asistente automático de {persona}.\n' +
      '- IDIOMA: responde SIEMPRE en español de {pais}, tuteando (sin voseo: nada de "podés", "tenés", "decime", salvo que el país sea Argentina). Nunca uses portugués ni otro idioma, aunque el cliente mezcle palabras.' },

  cierre: { titulo: 'Cierre del flujo de entrega',
    ayuda: 'El último mensaje después de los bonos. Pide una sola cosa: que guarden el número.',
    base: 'De verdad, valoro muchísimo tu confianza 💛 Espero que este material te sea de gran ayuda.\n\n' +
      '📲 *Guarda mi número en tus contactos* como *{persona}*.\n\n' +
      'Así no se te pierde este chat: por aquí te aviso cuando saque material nuevo, y por aquí me escribes si necesitas algo.' },

  iaEntrega: { titulo: 'IA después de la compra',
    ayuda: 'Queda al final del flujo de entrega, cuando ya pagó y ya tiene sus bonos. Aquí llegan los "no me abre el PDF".',
    base: 'Eres {persona} y atiendes por WhatsApp a quien YA pagó {oferta} y ya recibió su material y sus bonos.\n\n' +
      'ESTO ES LO QUE COMPRÓ:\n"""\n{presentacion}\n"""\n\n' +
      'REGLAS\n' +
      '- Responde en 1 a 2 frases cortas, cálida y cercana, máximo 2 emojis, tratándola de tú.\n' +
      '- Tu único trabajo es agradecerle por su aporte y por su confianza. Nada más.\n' +
      '- NO te comprometas a nada: no prometas enviar, reenviar, adjuntar, crear, revisar, validar ni consultar nada, ni digas "en un momento", "ya te lo paso" o "te lo reenvío".\n' +
      '- No des lecciones, contenido, explicaciones largas ni material nuevo. No inventes nada que no esté aquí.\n' +
      '- Si te pide un archivo, un PDF, ayuda extra, otro material o dice que algo no le llegó o no le abre, agradécele con calidez y dile con sencillez que por este medio no puedes hacerlo, sin prometer nada.\n' +
      '- Si agradece, respóndele con calidez y cierra.\n' +
      '- No prometas devoluciones, descuentos ni fechas.\n' +
      '- Si te pregunta si eres un bot, responde con honestidad que eres el asistente automático de {persona}.\n' +
      '- IDIOMA: responde SIEMPRE en español de {pais}, tuteando (sin voseo: nada de "podés", "tenés", "decime", salvo que el país sea Argentina). Nunca uses portugués ni otro idioma, aunque el cliente mezcle palabras.' },
};

// Rellena los huecos de un texto con los datos de la oferta. Un hueco que no exista se
// queda vacio en vez de quedar escrito como {loquesea} delante de la clienta.
export function render(txt, d) {
  const v = {
    persona: d.persona, oferta: d.oferta, promesa: d.promesa, promesaCorta: d.promesaCorta,
    pais: d.pais, anclaTxt: d.anclaTxt, aporte1Txt: d.aporte1Txt, aporte2Txt: d.aporte2Txt,
    bancos: d.bancos || '[FALTAN LOS DATOS DE PAGO DE ESTE PAIS]',
    vinetas: lineas(d.vinetas, '✅'),
    bonos: (d.bonos && d.bonos.length ? '\n\n🎁 *ADICIONAL, TE REGALAMOS:*\n' + lineas(d.bonos, 'num') : ''),
    presentacion: d.presentacion || '', datospago: d.datospago || '',
  };
  return String(txt || '').replace(/\{(\w+)\}/g, (m, k) => (v[k] === undefined || v[k] === null ? '' : String(v[k])));
}

// El texto de un bloque: el que se guardo desde la pestaña Plantilla, y si no hay, el base.
export function copy(pl, clave, d) {
  const base = (COPYS[clave] || {}).base || '';
  const puesto = pl && typeof pl[clave] === 'string' && pl[clave].trim() ? pl[clave] : base;
  return render(puesto, d);
}

// El saludo. Cada oferta puede tener su presentadora; el nombre es lo unico que
// cambia, y el tono -calido, cercano, nada corporativo- ya viene dado por el resto.
export const saludo = (d) => copy(null, 'saludo', d);

// Los textos de aqui abajo ya no se escriben dos veces: salen de COPYS, que es lo que
// edita la pestaña Plantilla. Se dejan como funciones porque asi se leen en el guion.
export const oferta = (d) => copy(null, 'oferta', d);
export const modelo = (d) => copy(null, 'modelo', d || {});
export const pregunta = (d) => copy(null, 'pregunta', d || {});
export const entrega = (d) => copy(null, 'entrega', d);
export const refuerzo = (d) => copy(null, 'refuerzo', d);
export const aportes = (d) => copy(null, 'aportes', d);
export const datosPago = (bancos) => copy(null, 'datos', { bancos });
export const noGracias = () => copy(null, 'noGracias', {});

// Las tres tandas de remarketing. En los flujos reales la segunda y la tercera de Murphy
// siguen diciendo "Dinamicas para Mujeres": quedaron asi al clonar el flujo y nadie lo
// vio. Aqui el nombre sale de la oferta, que es exactamente el tipo de error que una
// plantilla existe para no repetir.
export const remarketing = (d, pl) => [copy(pl, 'rm1', d), copy(pl, 'rm2', d), copy(pl, 'rm3', d)];

// ---------------------------------------------------------------------------
// Los bloques.
//
// Entre mensaje y mensaje va una pausa con "escribiendo": en los flujos reales no hay dos
// textos seguidos sin una, y es lo que hace que no se note que es un bot. La duracion se
// estira con el largo del texto, porque leer un parrafo toma mas que leer una linea.
// ---------------------------------------------------------------------------

function pausa(ref, seg, presencia) {
  return { ref, type: 'delay',
    data: { delay: seg, delayType: 'variable', minDelay: seg, maxDelay: seg + 2,
      unit: 'seconds', presence: presencia || 'composing' } };
}

// conIA: el mensaje sale como «Texto con IA» (aiText) y no como texto fijo. El texto queda como
// base: la IA de la cuenta lo reescribe con una variacion sutil para cada persona y, si falla o no
// hay clave, ZapData envia el original sin bloquear el flujo. apiKeySource:'integration' es lo
// que llevan los aiText que ya corren en los flujos de venta de Guatemala.
function texto(ref, msg, todo, conIA) {
  const b = conIA
    ? { ref, type: 'aiText', data: { message: msg, apiKeySource: 'integration' } }
    : { ref, type: 'text', data: { message: msg } };
  if (todo) b.todo = todo;
  return b;
}

function conPausa(ref, msg, todo, conIA) {
  const seg = Math.min(9, Math.max(2, Math.round(String(msg).length / 90) + 2));
  return [pausa(ref + '_p', seg), texto(ref, msg, todo, conIA)];
}

// La IA que responde mientras la clienta decide o mientras paga.
//
// El bloque NO tiene salida normal: sale solo cuando se le vence el tiempo o cuando falla
// la clave. Por eso el tiempo de cada uno no es un detalle, es lo que marca cuando sigue el
// flujo. Si se le quita, el contacto se queda con la IA para siempre y no vuelve a recibir
// ni un recordatorio de pago.
function ia(ref, minutos, prompt, next, respondOnEntry) {
  return { ref, type: 'aiResponder',
    data: { provider: 'openai', apiKeySource: 'integration', integrationKeyId: INTEGRACION_IA_OPENAI,
      model: 'gpt-5.4-nano', systemPrompt: prompt, conversationTone: 'informal', useEmojis: true,
      autoSendResponse: true, keepContext: true, contextMessages: 10,
      // respondOnEntry: true -> la IA contesta el mensaje que la activo. Por defecto es false,
      // y entonces el primer texto libre que cae aqui -un "Si Quiero" escrito a mano, una
      // queja- se traga sin respuesta: una clienta de Book Nooks Venezuela escribio seis
      // mensajes seguidos y no recibio ninguno. Dinamicas Col, hecho antes de que existiera
      // ese campo, siempre contesto al entrar; esto lo deja igual.
      //
      // Pero ese default (true) solo tiene sentido cuando ANTES de entrar aqui quedo algo sin
      // contestar. ia_pago y cada rm_ia entran justo despues de un texto propio -los datos de
      // pago, el recordatorio- que ya es un mensaje completo: con el default, la IA generaba
      // una pregunta extra ("¿me confirmas el aporte de X o Y?") que el texto de arriba ya
      // habia dejado clara, y eso llegaba a la clienta como ruido duplicado.
      respondOnEntry: respondOnEntry !== false,
      responseVariable: 'ai.response', enableExpiration: true, expirationMinutes: minutos,
      understandAudio: true, understandImage: true, debounceSeconds: 8 },
    next };
}

function archivo(ref, a, i) {
  const b = { ref, type: 'document',
    data: { mediaUrl: String(a.url || ''), fileName: a.nombre, caption: '' } };
  if (!a.url) b.todo = 'Subir aquí el archivo "' + a.nombre + '".';
  return b;
}

// El flujo de VENTA: del anuncio hasta los datos de pago, con el remarketing colgando de
// la espera.
export function flujoVenta(d, pl) {
  const of = d.oferta;
  const b = [];

  // Lo que la IA necesita saber de esta oferta no se escribe aparte: es el mismo copy que la
  // clienta acaba de leer. Asi la IA responde con el precio, el contenido y las cuentas de
  // esta oferta y de este pais, y si mañana cambia el precio, cambia sola.
  const dIA = Object.assign({}, d, {
    presentacion: copy(pl, 'oferta', d) + '\n\n' + copy(pl, 'modelo', d),
    datospago: copy(pl, 'datos', d),
  });

  // AQUI NO VA UN BLOQUE DE INICIO.
  //
  // ZapData crea el suyo con el disparador que se declara en `trigger`. Si ademas se manda
  // uno, el flujo queda con DOS inicios encadenados -inicio, inicio, y despues el guion- y
  // el motor se detiene en el segundo: el contacto entra al flujo, se le ve el flujo activo
  // en el inbox, y no recibe un solo mensaje. Paso con las seis primeras ofertas y no se ve
  // en el lienzo si no se miran las conexiones.

  // Quien ya compro no vuelve a entrar al guion de venta. En los flujos reales esta es la
  // primera condicion despues del disparador, y existe porque el anuncio se sigue viendo:
  // sin ella, una clienta que ya pago recibe otra vez la oferta completa.
  b.push({ ref: 'yacompro', type: 'condition',
    data: { logicOperator: 'or', conditions: [
      { id: 'c1', type: 'tag', tagName: 'Compra realizada ' + of, tagCondition: 'has' },
      { id: 'c2', type: 'tag', tagName: 'Pago', tagCondition: 'has' },
    ] },
    next: { yes: 'fin', no: 'categoria' } });
  b.push(texto('fin', copy(pl, 'yaTiene', d)));
  b[b.length - 1].next = {};

  b.push({ ref: 'categoria', type: 'tag',
    data: { actionType: 'add_tag', action: 'add', tagName: 'Categoria ' + of } });

  // Solo el saludo va como «Texto con IA» (pedido del dueño, 2026-10-21): asi dos personas que
  // escriben a la vez no reciben el mismo primer mensaje palabra por palabra. El resto del guion
  // sigue como texto fijo a proposito: es el que ya vende y no se varia.
  b.push.apply(b, conPausa('saludo', copy(pl, 'saludo', d), undefined, true));

  // Los audios son la voz de la persona y es lo que hace que esto no parezca un bot. Van
  // en las dos posiciones donde estan en los flujos que venden: el primero justo despues
  // del saludo, el segundo antes de pedir el si. Si no hay archivo, el bloque queda igual y
  // marcado para grabarlo en el editor.
  b.push(pausa('audio_p', 3, 'recording'));
  // voiceNote es la casilla "Enviar como grabación de voz" del editor: el audio llega como
  // nota de voz (burbuja de micrófono) y no como archivo adjunto. Va marcada siempre.
  const a1 = { ref: 'audio', type: 'audio', data: { mediaUrl: d.audio1 || '', voiceNote: true } };
  if (!d.audio1) a1.todo = 'Grabar aquí el audio de ' + d.persona + ' presentando ' + of + '.';
  b.push(a1);

  if (d.portada) {
    b.push(pausa('portada_p', 3));
    b.push({ ref: 'portada', type: 'image', data: { mediaUrl: d.portada, caption: '' } });
  }

  b.push.apply(b, conPausa('oferta', copy(pl, 'oferta', d)));
  b.push.apply(b, conPausa('modelo', copy(pl, 'modelo', d)));

  // El segundo audio: el que remata justo antes de pedir el si.
  if (d.audio2) {
    b.push(pausa('audio2_p', 3, 'recording'));
    b.push({ ref: 'audio2', type: 'audio', data: { mediaUrl: d.audio2, voiceNote: true } });
  }

  b.push.apply(b, conPausa('pregunta', copy(pl, 'pregunta', d)));

  // El boton, con las dos respuestas explicitas.
  //
  // Antes era un solo boton y quien escribia "no puedo ahora" terminaba recibiendo el
  // material igual: eso es lo que hace que la gente bloquee el numero. Ahora el "no" es un
  // boton, y aiCaptureEnabled deja que la IA entienda tambien el texto libre -"dale",
  // "mandamelo", "ahora no"- y lo lleve al boton que corresponde.
  //
  // timeoutEnabled: true NO cambia el comportamiento -el motor siempre aplica un tiempo
  // limite- pero es lo unico que hace que el lienzo DIBUJE la salida de timeout.
  b.push({ ref: 'boton', type: 'interactiveBlock',
    data: { interactionType: 'button', text: copy(pl, 'boton', d), choices: ['Si Quiero', 'No, gracias'],
      aiCaptureEnabled: true, apiKeySource: 'integration', provider: 'openai',
      fallbackEnabled: true, timeoutEnabled: true, timeout: 30, timeoutUnit: 'minutes' },
    next: { 'choice-0': 'entrega_p', 'choice-1': 'no_tag', fallback: 'ia_venta', timeout: 'boton2' } });

  // Lo que escribe y no es ni si ni no -una pregunta, una duda del precio- lo contesta la
  // IA, y a los dos minutos vuelve a aparecer el boton para que confirme.
  b.push(ia('ia_venta', 2, copy(pl, 'iaPre', dIA), { expiration: 'boton2', error: 'boton2' }));
  b.push({ ref: 'boton2', type: 'interactiveBlock',
    data: { interactionType: 'button',
      text: copy(pl, 'boton2', d),
      // El mismo "Si Quiero" del primer boton, a proposito: quien lo escribe a mano o toca el
      // boton viejo despues de que aparecio este -a los 30 min el primero cede su lugar a
      // este- tiene que recibir el material. Con otro texto ("Sí, envíamelo") ese "Si Quiero"
      // no coincidia con nada y la clienta debia adivinar la frase nueva.
      choices: ['Si Quiero', 'No, gracias'],
      aiCaptureEnabled: true, apiKeySource: 'integration', provider: 'openai',
      // Sin limite propio: en Dinamicas Col este boton espera 24 h y quien lo toca al dia
      // siguiente recibe el material. Sin estos campos el motor lo corta a las 6 h.
      fallbackEnabled: true, timeoutEnabled: false, timeout: 24, timeoutUnit: 'hours' },
    next: { 'choice-0': 'entrega_p', 'choice-1': 'no_tag', fallback: 'ia_venta' } });

  b.push.apply(b, conPausa('entrega', copy(pl, 'entrega', d)));
  (d.archivos || []).forEach((a, i) => {
    if (i > 0) b.push(pausa('arch' + i + '_p', 3, 'none'));
    b.push(archivo('arch' + i, a, i));
  });

  // No toda oferta se entrega en PDF. Moldes son carpetas de Drive: lo que llega es un
  // mensaje con el enlace. Cada uno va en su propio mensaje, con su titulo encima, porque
  // tres enlaces en un mismo globo se leen como un muro y no se toca ninguno. Y con vista
  // previa activada, que es lo que hace que el enlace se vea como algo y no como texto.
  (d.enlaces || []).forEach((e, i) => {
    if (!e || !e.url) return;
    b.push(pausa('link' + i + '_p', 3));
    b.push({ ref: 'link' + i, type: 'text',
      data: { message: (e.titulo ? e.titulo + ' 👇\n\n' : '') + e.url, linkPreview: true } });
  });

  b.push.apply(b, conPausa('refuerzo', copy(pl, 'refuerzo', d)));
  b.push.apply(b, conPausa('aportes', copy(pl, 'aportes', d)));
  b.push.apply(b, conPausa('datos', copy(pl, 'datos', d),
    d.bancos ? null : 'REVISAR: faltan los datos de pago de ' + d.pais + '. No los inventes.'));

  // La etiqueta que despues le dice al flujo de pagos de que fue la venta.
  b.push({ ref: 'pendiente', type: 'tag',
    data: { actionType: 'add_tag', action: 'add', tagName: 'Pago Pendiente ' + of } });

  // Media hora de gracia antes de la primera insistencia, como en los flujos reales, pero
  // atendida: antes era una espera muda y quien preguntaba "¿a nombre de quién es la
  // cuenta?" se quedaba sin respuesta, con el flujo dormido. Ahora ese rato lo cubre la
  // IA, y cuando se le vence el tiempo sigue el remarketing igual que antes.
  b.push(ia('ia_pago', 30, copy(pl, 'iaPost', dIA), { expiration: 'pago0', error: 'pago0' }, false));

  // La rama del no. TERMINA aqui, con la despedida (2026-10-10).
  //
  // Antes quedaba escuchando (un waitInput que devolvia a boton2): cualquier mensaje despues del "No,
  // gracias" -incluso un pulgar arriba- volvia a mostrar "¿Te envio el material?", y si decia que no otra
  // vez regresaba al mismo punto, sin limite. Se vio en 8 chats de una muestra de 156. Quien dijo que no no
  // vuelve a recibir la oferta; si cambia de opinion escribe la palabra clave del anuncio y arranca de nuevo.
  b.push({ ref: 'no_tag', type: 'tag',
    data: { actionType: 'add_tag', action: 'add', tagName: 'No Interesado ' + of } });
  b.push.apply(b, conPausa('no', copy(pl, 'noGracias', d)));
  b[b.length - 1].next = {};

  // Cada espera del remarketing tambien la cubre la IA: cinco horas de silencio eran cinco
  // horas sin nadie que contestara. El ultimo tramo son 24 horas, el maximo que acepta el
  // bloque, para que despues del ultimo recordatorio siga habiendo alguien al otro lado.
  //
  // Y antes de cada recordatorio se pregunta si el pago ya entro. Cobrarle a quien ya pago
  // es la forma mas rapida de que alguien que estaba feliz pida el dinero de vuelta: pasa
  // cuando manda el comprobante y el flujo sigue insistiendo como si nada. Si ya esta
  // marcada como comprada, no sale ningun recordatorio mas y queda solo la IA atendiendo.
  remarketing(d, pl).forEach((m, i) => {
    const ultimo = i === 2;
    b.push({ ref: 'pago' + i, type: 'condition',
      data: { logicOperator: 'or', conditions: [
        { id: 'g1', type: 'tag', tagName: 'Compra realizada ' + of, tagCondition: 'has' },
        { id: 'g2', type: 'tag', tagName: 'Pago', tagCondition: 'has' },
      ] },
      next: { yes: 'rm2_ia', no: 'rm' + i + '_tag' } });
    b.push({ ref: 'rm' + i + '_tag', type: 'tag',
      data: { actionType: 'add_tag', action: 'add', tagName: 'REMRKT ' + (i + 1) } });
    b.push(texto('rm' + i + '_txt', m));
    b.push(ia('rm' + i + '_ia', ultimo ? 1440 : 300, copy(pl, 'iaPost', dIA),
      ultimo ? { expiration: 'rm_fin', error: 'rm_fin' } : { expiration: 'pago' + (i + 1), error: 'pago' + (i + 1) },
      false));
  });
  // aiResponder exige conectar expiration y error a algo, aunque sea el final del guion: un
  // bloque de texto suelto (next:{}) vale como destino, uno de IA (next:{}) no. Sin esto
  // ZapData rechazaba el flujo entero: "Conecte a saída error de rm2_ia".
  b.push({ ref: 'rm_fin', type: 'tag',
    data: { actionType: 'add_tag', action: 'add', tagName: 'Remarketing terminado ' + of } });

  return {
    name: ('Venta ' + of + ' ' + d.pais).slice(0, 80),
    description: ('Generado desde el tablero. Se dispara con: ' + d.claves.join(', ') + '.').slice(0, 500),
    trigger: { type: 'keyword', keywords: d.claves, match: 'contains' },
    blocks: b,
  };
}

// El flujo de ENTREGA: lo que pasa cuando el pago ya se detecto. Entra por el salto desde
// el flujo de pagos del pais.
export function flujoEntrega(d, webhookUrl, pl) {
  const of = d.oferta;
  // Sin bloque de inicio, por lo mismo que en el flujo de venta: ZapData pone el suyo con
  // el disparador de `trigger`, y mandar otro deja el flujo con dos inicios y mudo.
  const b = [
    { ref: 'quitar', type: 'tag',
      data: { actionType: 'remove_tag', action: 'remove', tagName: 'Pago Pendiente ' + of } },
    { ref: 'marcar', type: 'tag',
      data: { actionType: 'add_tag', action: 'add', tagName: 'Compra realizada ' + of } },
    { ref: 'pagado', type: 'whatsappLabel', data: { labelName: 'Pagado', action: 'add' } },
  ];

  // El Pixel de Meta no acepta cualquier moneda (VES y GTQ, por ejemplo, no estan en su
  // lista). Cuando la moneda del pais no es una de las suyas, monedaPixel() ya resolvio que
  // hay que mandarlo en USD - aca solo falta convertir el monto antes de que llegue al
  // bloque, dividiendo el valor crudo del comprobante por la tasa del pais. Si no hay tasa
  // cargada, se manda igual sin convertir (queda un "todo": mejor una venta sin Pixel
  // perfecto que una que no se crea).
  const mp = d.monedaPixel || { moneda: d.moneda, tasaUsd: '' };
  const pixelBlock = { ref: 'pixel', type: 'pixel',
    data: { pixelId: d.pixel, eventType: 'Purchase', eventValue: '{{event_value}}', currency: mp.moneda } };
  if (mp.moneda !== d.moneda) {
    if (mp.tasaUsd) {
      b.push({ ref: 'pixel_usd', type: 'calculator',
        data: { operandA: '{{event_value}}', operandB: String(mp.tasaUsd), operation: 'divide',
          decimals: 2, saveToVariable: 'event_value_usd' } });
      pixelBlock.data.eventValue = '{{event_value_usd}}';
    } else {
      pixelBlock.todo = 'Falta tasaUsd de ' + d.pais + ' en la pestaña Países: el valor se manda sin convertir de ' + d.moneda + '.';
    }
  }
  b.push(pixelBlock);

  // Sin esto la entrega funciona y la venta no existe para el negocio: no llega a la hoja
  // del pais y no aparece en el ROAS.
  const wh = { ref: 'hoja', type: 'webhook',
    data: { url: webhookUrl || '', method: 'POST', payloadMode: 'wrapped',
      customVariables: [
        { key: 'event_value', type: 'string', value: '{{event_value}}' },
        { key: 'ad_source_url', type: 'string', value: '{{ad_source_url}}' },
      ] } };
  if (!webhookUrl) wh.todo = 'Falta la dirección del webhook que registra la venta en la hoja.';
  b.push(wh);

  b.push.apply(b, conPausa('grax',
    '¡Gracias de corazón por tu honestidad y por apoyar este trabajo! ✨🌿\n\n' +
    '¡Aquí tienes los BONOS DE REGALO que preparé para ti!'));

  // Con pausa entre uno y otro: seguidos le llegan como una rafaga de cinco notificaciones.
  (d.bonosArchivos || []).forEach((a, i) => {
    if (i > 0) b.push(pausa('bono' + i + '_p', 3, 'none'));
    b.push(archivo('bono' + i, a, i));
  });

  // Mismo caso que el material antes del pago: un bono tambien puede ser una carpeta de
  // Drive en vez de un archivo. Mismo patron -mensaje con el titulo encima y vista previa-
  // solo que aqui llega DESPUES del pago, junto con el resto de los regalos.
  (d.bonosEnlaces || []).forEach((e, i) => {
    if (!e || !e.url) return;
    if (i > 0 || (d.bonosArchivos || []).length) b.push(pausa('bonoLink' + i + '_p', 3, 'none'));
    b.push({ ref: 'bonoLink' + i, type: 'text',
      data: { message: (e.titulo ? e.titulo + ' 👇\n\n' : '') + e.url, linkPreview: true } });
  });

  // El cierre pide UNA cosa y la pide sin rodeos: que guarden el numero.
  //
  // Antes decia "recuerdame como contacto", que no se entiende: parece que pide que se
  // acuerden de uno, no que abran la agenda y lo guarden. Y guardar el numero es lo que
  // hace que el siguiente lanzamiento llegue a alguien que ya conoce la marca.
  b.push.apply(b, conPausa('cierre', copy(pl, 'cierre', d)));

  // Y despues del cierre, alguien que conteste. Aqui es donde llega "no me abre el PDF" o
  // "me falto un archivo": el momento en que la clienta ya pago y una respuesta tarde es lo
  // que convierte una compra feliz en una devolucion.
  b.push(ia('ia_entrega', 1440, copy(pl, 'iaEntrega', Object.assign({}, d, {
    presentacion: copy(pl, 'oferta', d),
  })), { expiration: 'entrega_fin', error: 'entrega_fin' }));
  // Mismo motivo que rm_fin en flujoVenta: aiResponder exige conectar expiration y error a
  // algo, y este es el ultimo bloque del guion de entrega.
  b.push({ ref: 'entrega_fin', type: 'tag',
    data: { actionType: 'add_tag', action: 'add', tagName: 'Entrega terminada ' + of } });

  return {
    name: ('Entrega ' + of + ' ' + d.pais).slice(0, 80),
    description: ('La llama Pagos ' + d.pais + ' cuando detecta el pago.').slice(0, 500),
    trigger: { type: 'tag', tags: ['Reenviar entrega ' + of] },
    blocks: b,
  };
}
