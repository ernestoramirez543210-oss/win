// Paso 2 del ebook: el contenido completo, en JSON editable.
//
// Escribir todo el texto ANTES de generar una sola imagen es deliberado: el texto cuesta
// centesimas y se corrige en el navegador; una imagen cuesta plata y 15 segundos. Revisar
// aqui evita pagar veinte fotos de un recetario con los tiempos de coccion mal.
//
// Sale en trozos cuando son muchas paginas: un recetario de 25 recetas no cabe en una
// respuesta y el JSON llegaba cortado a la mitad.

import { exigirAlguno, EQUIPO } from '../_sesion.js';
import { llamarLLM, conEnv, jsonDeTexto, diagnosticoJson } from '../_llm.js';
import { resumirGasto } from '../_gasto.js';
import { leerConocimiento, conocimientoTexto } from './conocimiento.js';
import { avatarTexto } from './ebook-avatar.js';
import { rescatarFugas } from '../_ebook-fugas.js';

// Ocho por llamada: con mas, la respuesta se acerca al tope de tokens y el JSON empieza a
// llegar truncado. El cliente pide los trozos que falten.
// Cinco, no ocho.
//
// Con ocho paginas por llamada el modelo reparte su atencion entre ocho y todas salen a
// medias: la primera decente y las ultimas rellenando. Bajar a cinco cuesta una llamada
// mas por ebook -centesimas de centavo- y sube el suelo de calidad de las ultimas paginas,
// que son las que el cliente lee cuando ya decidio si esto vale o no.
const POR_TANDA = 5;

// El estilo comun de las fotos cuando el modelo no devuelve uno (sin estilo comun, cada foto sale de un libro distinto). La vajilla es de las RECETAS: un recetario
// la conserva; una guia de crochet, un devocional o un juego de plantillas no la lleva. El segundo texto es el mismo que ESTILO_NEUTRO de templates/ebook-produccion.js
// (lo que usa el navegador cuando no le llega estilo): scripts/probar-ebook-estilo-foto.mjs vigila que no se separen.
const ESTILO_BASE = 'Consistent style across all photos: bright natural daylight, light neutral surface, soft shadows, ';
const ESTILO_RECETAS = ESTILO_BASE + 'same white ceramic tableware, minimal props, shot slightly from above at 45 degrees.';
const ESTILO_SIN_VAJILLA = ESTILO_BASE + 'minimal props, shot slightly from above at 45 degrees.';

const json = (s, o) => new Response(JSON.stringify(o), {
  status: s, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

// Las paginas que trae lo que contesto el modelo. Pidio {"paginas":[...]}, pero a veces la lista
// viene con otro nombre ("páginas", "pages", "contenido") o un nivel mas adentro: se toma la
// primera lista de objetos que se encuentre, hasta dos niveles. Un texto suelto no es una pagina.
function paginasDe(j, nivel = 0) {
  if (Array.isArray(j)) return j.filter((p) => p && typeof p === 'object' && !Array.isArray(p));
  if (!j || typeof j !== 'object' || nivel > 1) return [];
  if (Array.isArray(j.paginas)) return paginasDe(j.paginas, 2);
  for (const v of Object.values(j)) {
    const l = paginasDe(v, nivel + 1);
    if (l.length) return l;
  }
  return [];
}

async function proveedor(env, col) {
  const raw = await env.SUITE.get('ajustes');
  const ajustes = raw ? JSON.parse(raw) : {};
  const prov = conEnv((ajustes.proveedores || []).find((p) => p.id === ajustes.proveedorActivo), env, col);
  if (!prov) return { error: 'No hay proveedor de texto activo. Configuralo en APIs.' };
  const llave = await env.SUITE.get('secreto:' + prov.id);
  if (!llave) return { error: `El proveedor "${prov.nombre}" no tiene API key.` };
  return { prov, llave };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'POST') return json(405, { ok: false, error: 'POST only' });
  if (!env.SUITE) return json(501, { ok: false, error: 'Falta el almacen KV' });
  if (!(await exigirAlguno(request, env, EQUIPO))) {
    return json(401, { ok: false, error: 'Tu sesion caduco o no es del equipo: entra de nuevo' });
  }

  let body;
  try { body = await request.json(); } catch { return json(400, { ok: false, error: 'JSON invalido' }); }
  const brief = body.brief;
  if (!brief || !brief.titulo) return json(200, { ok: false, error: 'Falta el brief del paso 1.' });

  // Modo lote: ver resumirGasto en functions/_gasto.js.
  const col = body.gastoLote === true ? [] : null;
  const cfg = await proveedor(env, col);
  if (cfg.error) return json(200, { ok: false, error: cfg.error });

  const cerebro = conocimientoTexto(await leerConocimiento(env), 'contenido');

  const desde = Math.max(1, Number(body.desde) || 1);
  // Mismo tope que ebook-brief.js: el largo lo decide el temario, y un recetario de 120
  // recetas cortado en 30 salia mutilado sin que nada lo dijera.
  const total = Math.max(3, Math.min(Number(brief.nPaginas) || 10, 120));
  const hasta = Math.min(total, desde + POR_TANDA - 1);
  // Los titulos ya escritos viajan de vuelta para que no repita la misma receta en la
  // siguiente tanda: cada llamada es independiente y sin esto salian tres arroces.
  const yaHechos = Array.isArray(body.hechos) ? body.hechos.filter(Boolean) : [];

  // El temario con el reparto de paginas. Antes se mandaba `secciones.join(', ')`, que con
  // el temario nuevo -objetos con sus paginas- imprimia "[object Object]"; y aun con la
  // lista de nombres, cada tanda de cinco paginas escribia sin saber en que bloque cae, asi
  // que las ultimas secciones se quedaban sin sus paginas.
  const secs = (brief.secciones || [])
    .map((s) => (typeof s === 'string' ? { nombre: s, paginas: 0 } : s))
    .filter((s) => s && s.nombre);
  // Cada bloque baja con SUS puntos, numerados por la pagina que le toca a cada uno. Sin
  // esto el escritor solo veia "- Tecnicas base: paginas 4 a 7" y se inventaba que poner en
  // esas cuatro paginas, que es de donde salia el relleno: el temario prometia una cosa y
  // el contenido escribia otra. Ahora la pagina 5 sabe que le toca el punto de la pagina 5.
  let cursorPag = 1;
  const temario = secs.map((s) => {
    const n = Math.max(0, Number(s.paginas) || 0);
    if (!n) return '- ' + s.nombre;
    const ini = cursorPag, fin = cursorPag + n - 1;
    const puntos = (Array.isArray(s.puntos) ? s.puntos : [])
      .map((p) => String(p || '').trim()).filter(Boolean);
    const detalle = puntos.length
      ? '\n' + puntos.slice(0, n).map((p, i) => `    pagina ${ini + i}: ${p}`).join('\n')
      : '';
    cursorPag += n;
    return `- ${s.nombre}: paginas ${ini} a ${fin}${detalle}`;
  }).join('\n');

  // Un bono se escribe sabiendo que hay al lado. Sin esto repetia medio principal, que es
  // exactamente lo que hace que un bono se sienta relleno.
  const bono = brief.esBonoDe && brief.esBonoDe.titulo
    ? `\nEsto es un BONO de "${brief.esBonoDe.titulo}". Quita una friccion que el principal no ` +
      `resuelve, y NO repitas su contenido` +
      (Array.isArray(brief.esBonoDe.secciones) && brief.esBonoDe.secciones.length
        ? `, que cubre: ${brief.esBonoDe.secciones.join(', ')}` : '') + '.\n'
    : '';

  const esReceta = /recetario|receta|comida|menu|cocina/i.test(
    (brief.tipoEntregable || '') + ' ' + (brief.titulo || ''));

  // Se piden mas campos, y cada uno pide algo que un texto generico no puede inventar.
  //
  // La forma anterior era "titulo + 120-180 palabras + un tip", y eso lo cumple cualquier
  // parrafo de relleno: el modelo entregaba consejos que valen para cualquier persona y
  // para cualquier tema. Un campo llamado "errorComun" obliga a saber que sale mal de
  // verdad; uno llamado "senal" obliga a decir como se ve el exito. Las casillas dificiles
  // de rellenar con humo son las que suben el suelo del contenido.
  const formaLarga = esReceta
    ? `{"paginas":[{
  "numero": numero,
  "titulo": "nombre del plato, apetitoso y concreto",
  "ingredientes": ["cantidad exacta + ingrediente, con la medida que usa ella en su cocina"],
  "pasos": ["paso en imperativo, con el detalle que decide el resultado: fuego, minutos, textura, senal visual"],
  "porQueFunciona": "una frase: que hace que esta receta salga bien y no sea una mas",
  "errorComun": "el error concreto que arruina este plato, y como evitarlo",
  "variacion": "un cambio realista con lo que ella ya tiene en casa",
  "tip": "un consejo util y corto que no se le ocurre a cualquiera",
  "tiempo": "ej. 25 minutos",
  "porciones": "ej. 2 porciones",
  "foto": "una frase EN INGLES, de 12 a 25 palabras, que describa la foto del plato ya servido de ESTA receta: el plato, en que se sirve, la guarnicion y el color. Un solo plato, centrado, fondo limpio. Sin texto ni letras en la imagen"
}]__ESTILO__}`
    : `{"paginas":[{
  "numero": numero,
  "titulo": "titulo concreto, que prometa una cosa clara",
  "texto": "el contenido, 150-220 palabras. Especifico, con numeros, nombres, tiempos o cantidades",
  "puntos": ["pasos o ideas, cada uno accionable por si solo"],
  "ejemplo": "un ejemplo concreto con nombre, cifra o situacion real; nada de 'por ejemplo, imagina que...'",
  "errorComun": "lo que la mayoria hace mal en esto",
  "senal": "como sabe ella que le esta funcionando",
  "tip": "un consejo util y corto que no se le ocurre a cualquiera",
  "foto": "una frase EN INGLES, de 12 a 25 palabras, que describa UNA foto que ilustre ESTA pagina: un solo sujeto, centrado, fondo limpio, luz suave. Sin texto ni letras en la imagen"
}]__ESTILO__}`;

  // Diseño de las paginas «ia»: cada pagina se DIBUJA entera como un poster, con todo su texto dentro de una sola imagen (functions/_ebook-pagina-ia.js). Cabe poco (unas 110-130
  // palabras legibles; con mas de 170 la pagina se arma por codigo): se piden campos menos y mas cortos. El contenido sigue pasando por la misma vara de arriba (concreto, con cifras).
  const poster = body.diseno === 'ia';
  const formaPoster = esReceta
    ? `{"paginas":[{
  "numero": numero,
  "titulo": "nombre del plato, apetitoso y concreto (maximo 7 palabras)",
  "ingredientes": ["cantidad exacta + ingrediente, maximo 6 palabras cada uno; entre 6 y 9 ingredientes"],
  "pasos": ["entre 5 y 7 pasos en imperativo, de 6 a 14 palabras cada uno, con el detalle que decide el resultado: fuego, minutos, textura"],
  "tip": "un consejo util y corto que no se le ocurre a cualquiera (maximo 20 palabras)",
  "tiempo": "ej. 25 minutos",
  "porciones": "ej. 2 porciones",
  "foto": "una frase EN INGLES, de 12 a 25 palabras, que describa la foto del plato ya servido de ESTA receta: el plato, en que se sirve, la guarnicion y el color. Un solo plato, centrado, fondo limpio. Sin texto ni letras en la imagen"
}]__ESTILO__}`
    : `{"paginas":[{
  "numero": numero,
  "titulo": "titulo concreto, que prometa una cosa clara (maximo 9 palabras)",
  "texto": "el contenido, 45-70 palabras en uno o dos parrafos cortos. Especifico, con numeros, nombres, tiempos o cantidades",
  "puntos": ["3 o 4 pasos o ideas, de 6 a 14 palabras cada uno, accionables por si solos"],
  "errorComun": "lo que la mayoria hace mal en esto (maximo 20 palabras)",
  "tip": "un consejo util y corto que no se le ocurre a cualquiera (maximo 20 palabras)",
  "foto": "una frase EN INGLES, de 12 a 25 palabras, que describa UNA foto que ilustre ESTA pagina: un solo sujeto, centrado, fondo limpio, luz suave. Sin texto ni letras en la imagen"
}]__ESTILO__}`;
  const forma = poster ? formaPoster : formaLarga;

  // La frase de la foto ("foto") se pide AQUI, en el mismo JSON, y no en una llamada aparte por pagina: antes cada pagina le pedia a un modelo otra
  // llamada de texto solo para describir su foto, y quien acaba de escribir la pagina ya sabe como deberia verse. Cuesta unas 25 palabras mas dentro
  // de una respuesta que se paga igual, en vez de una llamada entera por pagina.

  // El estilo de las fotos se decide UNA VEZ, en la primera tanda, y viaja con el
  // contenido. Antes era una frase fija en el navegador igual para todos los ebooks: un
  // recetario de cenas de invierno y uno de batidos salian con la misma luz y la misma
  // vajilla. Se pide aqui porque quien acaba de escribir las recetas es quien sabe como
  // deberian verse, y sale gratis: va en el mismo JSON, no en otra llamada.
  const pideEstilo = desde === 1;
  // La vajilla se le pide SOLO a un recetario. Pedirsela a Gemini para una guia de crochet, un devocional o un juego de plantillas empujaba platos y tazas a TODAS las
  // fotos de un ebook sin nada de cocina; para esos se piden los materiales y las texturas del tema, y se le dice que no es de cocina (y que no nombre lo que NO se ve:
  // un «sin comida» dentro de la frase que va al generador de imagenes tambien invita a la comida). El pedido de un recetario queda igual, letra por letra.
  const bloqueEstilo = pideEstilo ? `,
  "estiloFoto": "una frase EN INGLES que describa el estilo comun de TODAS las fotos del
    ebook: ${esReceta ? 'luz, superficie, vajilla, atrezo y angulo' : 'luz, superficie, materiales y texturas del tema, atrezo y angulo'}. Debe encajar con este entregable en
    concreto, no valer para cualquiera.${esReceta ? '' : ' Este entregable no es de cocina: describe solo lo que SI se ve en sus fotos, sin nombrar platos, vajilla ni comida.'} Sin texto ni letras en la imagen."` : '';

  // La regla que faltaba, dicha con ejemplos.
  //
  // "Concreto y accionable, sin teoria" no cambia nada: el modelo cree que ya lo esta
  // haciendo. Lo que si cambia el resultado es ensenarle la version mala al lado de la
  // buena, porque el fallo tiene una forma reconocible -consejo que vale para cualquiera,
  // sin cifra, sin nombre, sin momento- y con el contraste delante deja de escribirlo.
  const VARA = `LA PRUEBA QUE TIENE QUE PASAR CADA PAGINA
Si lo que escribiste sirve igual para otro tema, otro pais u otra persona, esta mal escrito.
Cada pagina tiene que traer algo que solo sabe alguien que hizo esto de verdad.

MAL: "Es importante organizar tu tiempo para rendir mas durante el dia."
BIEN: "Deja la ropa del dia siguiente puesta sobre la silla antes de acostarte. Son 40
segundos en la noche y te ahorran los 10 minutos que se van decidiendo a las 6 a.m."

MAL: "Usa ingredientes frescos para un mejor sabor."
BIEN: "El tomate de guiso hay que pelarlo: la piel se enrolla en tiritas y queda dura
aunque hierva media hora. Cruz en la base, 30 segundos en agua hirviendo y sale sola."

MAL: "La ansiedad puede afectar tu descanso nocturno."
BIEN: "Si te despiertas a las 3 a.m. con el pecho apretado, no mires el celular: la luz
le dice a tu cuerpo que ya amanecio. Sientate en la cama y respira contando hasta cuatro."

REGLAS DURAS
- Cifras, tiempos, cantidades y nombres concretos. "Un poco" y "lo suficiente" no existen.
- Nada de introducciones ni de justificar por que el tema es importante: ella ya lo sabe,
  por eso pidio esto. Se empieza por lo util.
- Prohibido el relleno de coach: "recuerda que cada persona es unica", "lo importante es
  ser constante", "date permiso de". Si una frase se puede borrar sin perder informacion,
  bórrala tu antes de entregar.
- Se escribe para UNA persona concreta, con sus palabras y su cocina, no para un publico.`;

  const sistema = `Eres quien escribe los entregables de este negocio. Conoces el negocio a fondo:

${cerebro}

Escribes contenido que el cliente pueda usar el mismo dia, y lo escribes tan bien que
paga despues de haberlo recibido gratis. Ese es el listón: aqui se entrega primero y se
cobra despues, asi que una pagina floja no es una pagina floja, es una venta perdida.

${VARA}

Responde SIEMPRE en JSON valido, sin texto alrededor.
FORMATO DENTRO DE LOS TEXTOS: nunca uses comillas dobles rectas (") dentro de un texto, porque
rompen el JSON. Para citar un nombre, un libro, un versiculo o una frase usa comillas angulares
«asi» o comillas simples 'asi'. Un salto de linea dentro de un texto se escribe \\n, no como
salto real.`;

  // Es funcion de `hasta` porque un reintento pide menos paginas (ver abajo).
  const armarInstruccion = (hasta) => `Escribe el contenido de estas paginas del entregable.

${avatarTexto(body.avatar)}
ENTREGABLE
Titulo: ${brief.titulo}
Promesa: ${brief.promesa || ''}
Publico: ${brief.publico || ''}
Angulo: ${brief.angulo || ''}
Tipo: ${brief.tipoEntregable || ''}
${(brief.avisos || []).length ? 'Advertencias que debe respetar: ' + brief.avisos.join(' | ') + '\n' : ''}${temario ? 'TEMARIO (que va en cada pagina):\n' + temario + '\n' : ''}${bono}
ESCRIBE LAS PAGINAS ${desde} A ${hasta} (de ${total} en total).
${yaHechos.length ? '\nYa estan escritas estas, NO las repitas ni hagas variantes obvias:\n- ' + yaHechos.join('\n- ') + '\n' : ''}
Cada pagina se lee sola. ${esReceta
  ? 'Las cantidades y los tiempos de coccion deben ser correctos y seguros.'
  : 'Cada pagina resuelve UNA cosa entera, no da una vuelta general al tema.'}
El temario de arriba dice que va en cada pagina y NO es una sugerencia: escribe ese punto,
no otro parecido. Si un punto te parece flojo, escribelo bien; no lo cambies por el tuyo.

La persona tiene que poder ACTUAR con lo que lee, no solo entenderlo. Cada pagina termina
con algo que se puede hacer: los pasos, las cantidades, el criterio para decidir, la señal
de que va bien o de que hay que corregir. Explica el porque solo cuando cambia lo que la
persona va a hacer; si no cambia nada, sobra.

Antes de devolver, relee cada pagina y quita toda frase que sirva para cualquier tema.
${poster
  ? 'Cada pagina se dibuja como un POSTER, con todo su texto dentro de una sola imagen: cabe poco. Respeta los maximos de palabras de la forma de abajo (lo que sobre no cabe) y escribe frases cortas: aqui cada palabra tiene que ganarse su sitio.'
  : 'Si al quitarlas la pagina queda corta, es que estaba rellena: escribe mas de lo concreto.'}

Devuelve JSON con esta forma exacta:
${forma.replace('__ESTILO__', bloqueEstilo)}`;

  // Mas espacio por pagina: con 6000 para ocho paginas el modelo racionaba, y racionar es
  // exactamente lo que produce el parrafo generico. Ahora son cinco paginas con mas sitio.
  //
  // Si la respuesta no se puede leer (JSON roto o cortado antes de cerrar ni una pagina) se
  // reintenta UNA vez pidiendo solo dos paginas: una respuesta mas corta rara vez se corta, y
  // el cliente ya sabe seguir con las que falten (`siguiente`). Cuesta centesimas; el error
  // le costaba al equipo el ebook entero, tanda tras tanda.
  let r, j, paginas = [];
  for (const hastaIntento of [hasta, Math.min(hasta, desde + 1)]) {
    r = await llamarLLM(cfg.prov, cfg.llave, sistema, armarInstruccion(hastaIntento),
      { maxTokens: 9000, temperatura: 0.7, json: true });
    if (!r.ok) return json(200, { ok: false, error: r.error });
    j = jsonDeTexto(r.texto);
    paginas = paginasDe(j);
    if (paginas.length) break;
  }
  if (!paginas.length) {
    // El inicio de la respuesta casi nunca dice que fallo (suele estar bien): se dice si se corto
    // (se acabaron los tokens: un modelo que "piensa" se los gasta) o en que caracter se rompio
    // el formato y con que alrededor.
    const txt = r.texto || '';
    return json(200, {
      ok: false,
      error: `El modelo no devolvio paginas legibles: ${diagnosticoJson(txt)}. ` +
        'Vuelve a intentar; si sigue igual, avisa al dueño (puede cambiar el modelo de texto en APIs).',
    });
  }
  // Se renumeran contra la posicion real: el modelo a veces empieza en 1 cada tanda y el
  // ebook saldria con tres paginas "1".
  paginas.forEach((p, i) => {
    // Un campo metido dentro de otro texto (`"texto": "...", puntos": [...]`, con la clave sin su comilla de abrir) se rescata aqui, antes de que llegue a la pantalla
    // y a la imagen (functions/_ebook-fugas.js).
    rescatarFugas(p);
    p.numero = desde + i;
    // La frase de la foto va tal cual al modelo de imagenes: una sola linea y de largo acotado. Si el modelo no la trajo (o trajo otra cosa que
    // texto), se quita el campo y la pagina cae a la descripcion armada por plantilla (functions/api/ebook-pagina.js).
    const foto = typeof p.foto === 'string' ? p.foto.replace(/\s+/g, ' ').trim().slice(0, 300) : '';
    if (foto) p.foto = foto; else delete p.foto;
  });

  const escritas = desde - 1 + paginas.length;
  return json(200, {
    ok: true,
    paginas,
    total,
    // Solo viene en la primera tanda; el cliente lo fija para todo el ebook. Si el modelo no lo trajo (o trajo otra cosa que texto), el servidor pone el de siempre
    // segun el tipo -con vajilla un recetario, sin ella lo demas-: antes lo ponia el navegador y era el mismo para todos.
    estiloFoto: pideEstilo ? (((j && typeof j.estiloFoto === 'string') ? j.estiloFoto.trim() : '') || (esReceta ? ESTILO_RECETAS : ESTILO_SIN_VAJILLA)) : '',
    faltan: Math.max(0, total - escritas),
    siguiente: escritas < total ? escritas + 1 : null,
    ...(col ? { gasto: resumirGasto(col) } : {}),
  });
}
