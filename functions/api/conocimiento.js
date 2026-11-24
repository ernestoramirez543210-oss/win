// El cerebro de ofertas: lo que la suite sabe del negocio.
//
// No es un agente. Es un documento que viaja dentro del prompt cada vez que se escribe
// contenido. La diferencia importa: un agente decide y cobra por decidir; esto solo
// recuerda, y recordar sale gratis. Modelar una oferta es un camino fijo -leer, sacar el
// angulo, escribir, maquetar- asi que lo que hace falta no es que alguien decida el
// camino, es que sepa el negocio.
//
// Vive en KV y se edita desde la suite a proposito: quien mejor sabe que funciona es
// quien crea las ofertas, y si mejorarlo obligara a tocar codigo y desplegar, nadie lo
// mejoraria. Cada vez que una oferta pega, esa leccion se escribe aqui y todas las
// siguientes nacen sabiendola.

import { exigirDueno, readSession } from '../_sesion.js';

const CLAVE = 'conocimiento';
const MAX = 24000;   // ~6k tokens: cabe en cualquier prompt sin encarecerlo de golpe

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Semilla con lo que ya sabemos del negocio por el resto del proyecto. No es relleno:
// es lo minimo para que el primer ebook no salga genérico. La idea es que se reescriba.
export const SEMILLA = {
  negocio: `Vendemos productos digitales (recetarios, plantillas, devocionales, guias) por
WhatsApp, con trafico de Facebook, en Guatemala, Colombia, Venezuela, Peru, Mexico y Argentina.
El cliente ve un anuncio, escribe por WhatsApp, y recibe el archivo. Precio bajo, decision
rapida, sin pagina de ventas larga.`,

  publico: `Mayoritariamente mujeres de 30 a 60 anos, de clase media y media-baja, que leen
en el celular. Compran soluciones concretas para hoy: que cocinar esta noche, como organizar
la semana, como manejar un dolor. No compran conceptos ni teoria.`,

  anatomia: `Una oferta que funciona tiene:

1. UNA PROMESA CONCRETA Y MEDIBLE. "30 cenas en 20 minutos" se entiende sin explicar;
   "come mejor" no promete nada. La prueba: si la persona no puede saber si se cumplio,
   no es una promesa. Numero + resultado + tiempo.
2. UN DOLOR RECONOCIBLE EN LA PRIMERA LINEA, dicho con sus palabras. No "mejora tu
   alimentacion" sino "otra vez son las 7 y no sabes que hacer de comer".
3. CONTENIDO USABLE EL MISMO DIA. Si para aprovecharlo hay que comprar algo, estudiar
   antes o esperar al lunes, se va a quedar sin abrir.
4. UNA COSA A LA VEZ. Un entregable que resuelve tres problemas no resuelve ninguno bien
   y ademas no se puede prometer en una linea. Si sobran temas, son otro entregable.
5. BONOS QUE QUITAN UNA FRICCION REAL de despues de comprar -la lista de mercado, el
   calendario, las porciones para uno- no "acceso al grupo" ni "PDF de regalo".
6. CANTIDAD HONESTA. Si dice 50 recetas, trae 50 recetas completas. Rellenar con
   variantes de la misma es lo que hace que alguien no vuelva a comprar.

COMO SE SABE QUE UNA OFERTA VA A FALLAR
- La promesa se puede aplicar a cualquier otro producto del mercado.
- El titulo dice el formato ("Guia completa de...") en vez del resultado.
- Necesita un parrafo para explicar que es.
- El contenido es correcto pero se consigue gratis en dos busquedas.

QUE ES LO QUE SE COMPRA DE VERDAD
No es la informacion: eso esta en internet. Es el trabajo ya hecho -la seleccion, el
orden, las cantidades ya calculadas, los errores ya cometidos por otro-. Cada pagina
tiene que oler a que alguien lo hizo antes y esta contando como le fue.`,

  estilo: `Espanol neutro latinoamericano, tuteo, frases cortas. Se explica como le explicarias
a una amiga en la cocina, sin tecnicismos y sin lenguaje de coach. Cada receta o seccion cabe
en una pagina y se entiende sin leer las anteriores.

COMO SUENA
- Se le habla a ELLA, no a un publico. "Cuando saques la olla", no "cuando el usuario retire
  el recipiente". Nunca "las personas que sufren de".
- Frases de menos de 20 palabras. Si una frase necesita una coma para respirar, son dos.
- El verbo primero: "Pica la cebolla en cuadros chicos", no "lo que vas a hacer es picar".
- Se usan sus palabras, las que ella diria en su casa y en su pais. Si en Colombia se dice
  "alverjas" no se escribe "guisantes".

LO QUE SE BORRA ANTES DE ENTREGAR
- Introducciones. Se empieza por lo util; ella ya sabe por que abrio el archivo.
- Justificar el tema ("en el mundo de hoy, con el ritmo acelerado...").
- Relleno de coach: "recuerda que cada persona es unica", "date permiso", "lo importante
  es ser constante", "confia en el proceso".
- Adverbios de humo: basicamente, simplemente, realmente, definitivamente.
- Cualquier frase que se pueda borrar sin perder informacion. Si al quitarla no se pierde
  nada, sobraba.

LA REGLA QUE DECIDE
Si el parrafo sirve igual para otro tema, otro pais u otra persona, esta mal escrito. Cada
pagina trae una cifra, un tiempo, una cantidad, un nombre o un momento del dia: algo que
solo sabe quien hizo esto de verdad.

Y en este negocio se entrega antes de cobrar, asi que el texto no tiene que gustar: tiene
que hacer que alguien que ya lo tiene gratis decida pagarlo. Una pagina floja no es una
pagina floja, es una venta perdida.`,

  visual: `Limpio y luminoso, tipo revista de cocina. Foto del resultado terminado, texto sobre
fondo claro y legible en pantalla de celular. Tipografia grande. Nunca texto encima de la foto.`,

  prohibido: `Reglas que no se rompen, porque esto se le vende a personas reales:
- No prometer resultados de salud: nada de curar, sanar, desinflamar, bajar X kilos, reemplazar
  un tratamiento o un medicamento. Se puede decir "recetas sin azucar anadida"; no se puede
  decir "revierte la diabetes".
- No inventar respaldo cientifico, estudios, ni testimonios.
- Cantidades y tiempos de coccion tienen que ser correctos y seguros, sobre todo con pollo,
  cerdo, pescado, huevo y conservas. Si hay duda, se pone el tiempo mas conservador.
- Si el tema roza salud, embarazo, ninos o una condicion medica, incluir una linea que
  recomiende consultar a un profesional.
- No copiar texto, nombres ni imagenes de otra marca. Del anuncio de un competidor se toma
  el angulo y el tipo de oferta, jamas su contenido.`,

  // La seccion que el negocio necesitaba y no tenia.
  //
  // Todo lo demas del cerebro estaba escrito para vender ANTES de entregar: la anatomia de
  // la oferta, las objeciones, el cierre. Pero aqui se entrega primero y se cobra despues,
  // asi que la pregunta que decide la facturacion no es "por que compraria" sino "por que
  // paga alguien que ya tiene el archivo". Sin esta seccion, la pagina de cierre del PDF y
  // los mensajes de cobro se escribian adivinando.
  cobro: `MODELO X1: se entrega el contenido completo y la persona paga despues. No hay
pasarela, no hay candado, no hay nada que la obligue. Si paga es porque quiere.

Que es lo que realmente se esta cobrando
El archivo ya lo tiene, asi que el archivo no es lo que sostiene el pago. Lo que lo sostiene
es que una persona concreta cumplio con ella primero. Se paga para no quedarle mal a alguien
que no la trato como a un numero. Todo lo que rompa esa sensacion -sonar a robot, a empresa,
a cobrador- baja el pago aunque el contenido sea excelente.

Como se pide
- Una sola vez, clara y completa: cuanto, a donde, y que hacer despues. Pedir a medias
  obliga a una segunda pregunta y ahi se pierde la mitad.
- Se le habla a quien YA recibio: "ya lo tienes", "espero que te sirva". Nunca "podrias
  tenerlo", "no te lo pierdas", "ultimo dia": eso es de antes de entregar y suena a mentira
  cuando el archivo ya esta en su telefono.
- Sin pedir perdon por cobrar y sin justificarse. Se entrego algo que vale; se cobra lo que
  vale, con naturalidad.
- El monto y el medio salen tal cual estan escritos. Nunca se aproximan, nunca se convierten
  de memoria a otra moneda, nunca se inventan.

Lo que no se hace nunca
- Culpa, lastima, ni "confie en ti". Convierte a quien iba a pagar en alguien incomodo, y
  quien esta incomodo no vuelve a escribir.
- Urgencia inventada, precios que suben, amenazas de quitar el acceso. No se puede quitar lo
  que ya se entrego, y la amenaza vacia enseña que aqui se miente.
- Reclamar el pago mas de dos veces. La primera es un recordatorio; la segunda es cortesia;
  la tercera es acoso y ademas quema la recompra, que es donde esta el dinero real.

El seguimiento
Quien no contesta casi nunca dijo que no: se le paso. El recordatorio va sin reproche, corto,
y se apoya en el contenido, no en la deuda -"?alcanzaste a probar la del dia 2?"- porque
volver al contenido la devuelve al momento en que le sirvio, y ahi es cuando paga. Si despues
de dos recordatorios no contesta, se deja ir sin ultimo mensaje resentido: sigue siendo
alguien que tiene algo nuestro y le gusto.

(Escribe aqui lo que vayas aprendiendo: que frase hace que paguen, a que hora, cuanto tarda
la mayoria en pagar, que excusa se repite, que ofertas se pagan solas y cuales no.)`,

  clicchat: `Reglas generales de nuestros flujos de ClicChat: tono, largo de cada mensaje,
como se trata al cliente. Lo particular de cada parte del recorrido va abajo, en su propia
seccion; aqui va solo lo que aplica a todas.`,

  // Cada parte del recorrido tiene su propio PDF y sus propias reglas. Un solo campo de
  // texto obligaba a mezclar la bienvenida con el cobro y el modelo terminaba pidiendo el
  // comprobante en el primer mensaje.
  cc_bienvenida: `Como abrimos cuando el cliente escribe desde el anuncio.`,
  cc_presentacion: `Como presentamos la oferta y los bonos.`,
  cc_precio: `Como damos el precio y pedimos la decision.`,
  cc_objeciones: `Como respondemos a "esta caro", "lo pienso", "sirve para mi", "es seguro pagar".`,
  cc_pago: `Como damos los datos de pago y que pasa mientras el cliente paga.`,
  cc_comprobante: `Que decimos cuando manda el comprobante de la transaccion, como se
verifica y como se entrega el archivo.`,
  cc_seguimiento: `Como insistimos con quien no contesto, sin sonar desesperado.`,

  aprendizajes: `(Escribe aqui lo que vas aprendiendo: que angulos pegaron, que titulos
convirtieron, que bonos pidieron los clientes, que no funciono. Una linea por aprendizaje.)`,
};

// El orden importa: es el orden en que el modelo lo lee.
const SECCIONES = [
  ['negocio',      'El negocio'],
  ['publico',      'A quien le vendemos'],
  ['anatomia',     'Anatomia de una oferta que funciona'],
  ['estilo',       'Estilo de escritura'],
  ['visual',       'Estilo visual'],
  ['prohibido',    'Lo que nunca se hace'],
  ['cobro',        'Como se cobra despues de entregar'],
  ['clicchat',     'ClicChat: reglas generales'],
  ['cc_bienvenida',   'ClicChat: bienvenida'],
  ['cc_presentacion', 'ClicChat: presentación de la oferta'],
  ['cc_precio',       'ClicChat: precio y cierre'],
  ['cc_objeciones',   'ClicChat: objeciones'],
  ['cc_pago',         'ClicChat: datos de pago'],
  ['cc_comprobante',  'ClicChat: comprobante y entrega'],
  ['cc_seguimiento',  'ClicChat: seguimiento'],
  ['aprendizajes', 'Aprendizajes de ofertas pasadas'],
];

// Las claves de las secciones, para quien reciba propuestas de un modelo (aprender.js, ofertas-aprendizaje.js): una clave que no sea de esta lista no corresponde a ninguna seccion y no se deja pasar.
export const CLAVES_DE_SECCION = SECCIONES.map((s) => s[0]);

// Que seccion de ClicChat le toca a cada flujo. Escribir la bienvenida no necesita saber
// como se verifica un comprobante, y mandarle las siete secciones a cada llamada es
// pagar por ruido que ademas confunde al modelo.
export const SECCION_DE_FLUJO = {
  bienvenida:   'cc_bienvenida',
  presentacion: 'cc_presentacion',
  precio:       'cc_precio',
  objeciones:   'cc_objeciones',
  pago:         'cc_pago',
  comprobante:  'cc_comprobante',
  seguimiento:  'cc_seguimiento',
  entrega:      'cc_comprobante',
};

export async function leerConocimiento(env) {
  const raw = await env.SUITE.get(CLAVE);
  if (!raw) return { ...SEMILLA, esSemilla: true };
  try { return { ...SEMILLA, ...JSON.parse(raw) }; } catch { return { ...SEMILLA, esSemilla: true }; }
}

// Que secciones ve cada tarea.
//
// Un cerebro por area suena mejor de lo que es: "a quien le vendemos", el tono de la marca
// y las reglas de lo que nunca se promete los necesitan TODAS las areas. Duplicados en
// cuatro documentos, se actualiza uno y los otros tres quedan viejos -y las reglas de salud
// terminan viviendo en un cerebro mientras los demas las violan sin saberlo-.
//
// Asi que hay un solo cerebro con secciones, y cada tarea recibe solo las que le sirven:
// una fuente de verdad, y sin ruido en el prompt. El NUCLEO va siempre; el resto depende
// del trabajo. Quien maqueta una pagina no necesita saber de flujos de WhatsApp, y quien
// escribe mensajes no necesita el tamano de las tipografias.
const NUCLEO = ['negocio', 'publico', 'prohibido'];

const PARA = {
  oferta:    NUCLEO.concat(['anatomia', 'estilo', 'aprendizajes']),
  // 'anatomia' tambien para quien escribe: lo que hace buena a una oferta -promesa
  // concreta, contenido usable el mismo dia, cantidad honesta- es lo mismo que hace buena
  // a una pagina. Sin ella el escritor conoce el tono pero no la vara.
  contenido: NUCLEO.concat(['anatomia', 'estilo', 'aprendizajes']),
  mensajes:  NUCLEO.concat(['estilo', 'clicchat', 'aprendizajes']),
  visual:    ['visual'],
  // La ultima pagina del PDF: la unica que le habla a alguien que ya leyo todo y todavia no
  // ha pagado. Necesita el tono de la marca y, sobre todo, como se cobra aqui.
  cierre:    NUCLEO.concat(['estilo', 'cobro']),
};

// Los flujos que ocurren cuando la persona ya tiene el contenido en la mano tambien leen
// 'cobro': son el mismo momento del negocio que la pagina de cierre, por otro canal.
const CON_COBRO = ['pago', 'comprobante', 'seguimiento', 'entrega', 'precio'];

// para: 'oferta' | 'contenido' | 'mensajes' | 'visual', o 'mensajes:<flujo>' para que un
// flujo reciba ademas SU seccion y no las de los otros seis. Sin el, va el cerebro
// completo (lo que necesita la pantalla de edicion).
export function conocimientoTexto(c, para) {
  let permitidas = PARA[para];
  if (!permitidas && typeof para === 'string' && para.indexOf('mensajes:') === 0) {
    const flujo = para.slice('mensajes:'.length);
    const seccion = SECCION_DE_FLUJO[flujo];
    permitidas = PARA.mensajes.concat(seccion ? [seccion] : []);
    if (CON_COBRO.indexOf(flujo) !== -1) permitidas = permitidas.concat(['cobro']);
  }
  return SECCIONES
    .filter(([k]) => !permitidas || permitidas.indexOf(k) !== -1)
    .map(([k, titulo]) => {
      const v = (c[k] || '').trim();
      return v ? `## ${titulo}\n${v}` : '';
    })
    .filter(Boolean)
    .join('\n\n');
}

export async function onRequest(context) {
  const { request, env } = context;
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });

  // Leer lo puede cualquiera con sesion: el equipo que crea ofertas necesita consultarlo.
  // Escribir es del dueno, porque una mala edicion aqui contamina todo lo que se genere.
  const requiereLogin = true;
  if (request.method === 'GET') {
    if (requiereLogin && env.SESSION_SECRET) {
      const rol = await readSession(request.headers.get('Cookie'), env.SESSION_SECRET);
      if (!rol) return json(401, { ok: false, error: 'No autenticado' });
    }
    const c = await leerConocimiento(env);
    return json(200, { ok: true, conocimiento: c, secciones: SECCIONES });
  }

  if (request.method !== 'PUT') return json(405, { ok: false, error: 'Method not allowed' });
  if (!(await exigirDueno(request, env))) return json(401, { ok: false, error: 'Solo el dueño puede editarlo' });

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }

  const actual = await leerConocimiento(env);
  const nuevo = {};
  for (const [k] of SECCIONES) {
    const v = k in body ? body[k] : actual[k];
    nuevo[k] = typeof v === 'string' ? v.slice(0, MAX) : '';
  }
  await env.SUITE.put(CLAVE, JSON.stringify(nuevo));
  return json(200, { ok: true, conocimiento: nuevo, largo: conocimientoTexto(nuevo).length });
}
