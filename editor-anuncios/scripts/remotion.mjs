// Driver de Remotion del editor de anuncios. Lo usa Claude; tambien se puede correr a mano.
//
//   node scripts/remotion.mjs render <videoData.json> [--salida ruta.mp4] [--concurrencia N] [--crf 18] [--frames 0-90]
//   node scripts/remotion.mjs fotogramas <videoData.json> [--frames 0,45,120 | --segundos 1.5,3.2] [--escala 0.5] [--salida carpeta]
//   node scripts/remotion.mjs datos <videoData.json>      (imprime las props que recibira Remotion; no renderiza)
//   node scripts/remotion.mjs navegador
//
// Por que existe en vez de `npx remotion render`:
//  - En Windows Remotion COPIA public/ entero al empaquetar. Con la biblioteca de b-roll y de
//    musica serian gigas por cada render. Aqui se arma una carpeta temporal con enlaces duros
//    (instantaneos, sin gastar disco) SOLO a los archivos que el anuncio usa.
//  - Empaqueta una sola vez y saca todos los fotogramas de prueba de un tiron.
//  - Nunca pisa un video ya entregado: si final/<nombre>.mp4 existe, guarda _v2, _v3...
//
// Las rutas de videoData.json (src) son relativas a public/. Tambien se aceptan rutas que
// empiezan por anuncios/ (p. ej. el video base de un anuncio con IA).

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {bundle} from "@remotion/bundler";
import {ensureBrowser, renderMedia, renderStill, selectComposition} from "@remotion/renderer";
import {enableTailwind} from "@remotion/tailwind-v4";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CACHE = path.join(RAIZ, ".cache");
const ID_COMPOSICION = "FullVideo";

// Memoria que puede guardar Remotion para los cuadros de los videos de b-roll (OffthreadVideo). Sin esto usa hasta la MITAD de la memoria del equipo (en el servidor de 8 GB medimos 2 GB solo ahi,
// y el render entero pasaba de 4 GB); en el PC de 16 GB no estorba. El servidor (vps/anuncios-corredor.service) la acota con OFFTHREADVIDEO_CACHE_MB; sin esa variable no cambia nada.
function opcionesDeMemoria() {
  const mb = Number(process.env.OFFTHREADVIDEO_CACHE_MB ?? "");
  return Number.isFinite(mb) && mb >= 64 && mb <= 8192 ? {offthreadVideoCacheSizeInBytes: Math.round(mb * 1048576)} : {};
}

function leerOpciones(argv) {
  const opciones = {};
  const posicionales = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      opciones[a.slice(2)] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
    } else {
      posicionales.push(a);
    }
  }
  return {opciones, posicionales};
}

function rutasUsadas(data) {
  const rutas = new Set();
  (data.tomas ?? []).forEach((t) => rutas.add(t.src));
  (data.producto ?? []).forEach((p) => rutas.add(p.src));
  (data.libros ?? []).forEach((l) => rutas.add(l.src));
  if (data.botonCta?.icono) rutas.add(data.botonCta.icono);
  if (data.fijos?.boton?.icono) rutas.add(data.fijos.boton.icono);
  (data.rotulos ?? []).forEach((r) => r.icono?.src && rutas.add(r.icono.src)); // 2026-10-30: icono bajo un rotulo (oferta «1000 pegatinas»)
  const audio = data.audio ?? {};
  if (audio.voz?.src) rutas.add(audio.voz.src);
  if (audio.musica?.src) rutas.add(audio.musica.src);
  (audio.sfx ?? []).forEach((s) => rutas.add(s.src));
  return [...rutas].filter(Boolean);
}

/** Busca el archivo en public/ y, si no esta, en la raiz del proyecto (anuncios/...). Nunca sale de RAIZ. */
function resolver(rel) {
  const limpio = String(rel).replace(/\\/g, "/").replace(/^\/+/, "");
  for (const base of [path.join(RAIZ, "public"), RAIZ]) {
    const abs = path.resolve(base, limpio);
    if (!abs.startsWith(RAIZ + path.sep)) continue;
    if (fs.existsSync(abs) && fs.statSync(abs).isFile()) return abs;
  }
  return null;
}

function prepararPublico(data) {
  fs.mkdirSync(CACHE, {recursive: true});
  const destino = fs.mkdtempSync(path.join(CACHE, "publico-"));
  const faltan = [];
  for (const rel of rutasUsadas(data)) {
    const abs = resolver(rel);
    if (!abs) {
      faltan.push(rel);
      continue;
    }
    const limpio = String(rel).replace(/\\/g, "/").replace(/^\/+/, "");
    const salida = path.join(destino, ...limpio.split("/"));
    fs.mkdirSync(path.dirname(salida), {recursive: true});
    try {
      fs.linkSync(abs, salida);
    } catch {
      fs.copyFileSync(abs, salida);
    }
  }
  if (faltan.length > 0) {
    limpiar(destino);
    throw new Error(`Faltan archivos que usa el anuncio (rutas relativas a public/):\n${faltan.map((f) => `  - ${f}`).join("\n")}`);
  }
  return destino;
}

function limpiar(...carpetas) {
  for (const c of carpetas) {
    try {
      fs.rmSync(c, {recursive: true, force: true});
    } catch {
      /* si algo sigue abierto, se limpia en la proxima corrida */
    }
  }
}

// Remotion mezcla las props del anuncio sobre las de la demo (src/config/videoData.json, el defaultProps de
// Root.tsx) clave por clave de primer nivel: lo que el videoData.json NO trae se heredaba de la demo (p. ej.
// el CTA "Prueba superada / demo del editor" a los 4,8 s de un anuncio sin llamada a la accion). El validador
// ya lee una clave ausente como "no hay", asi que aqui se deja vacia a proposito. "estilo" y
// "zonaInferiorLibre" si se siguen heredando: son el estilo base.
const SIN_CONTENIDO = {tomas: [], palabras: [], textosGrandes: [], producto: [], cta: null, audio: {}};

function leerVideoData(ruta) {
  if (!ruta || !fs.existsSync(ruta)) throw new Error(`No existe el archivo videoData.json: ${ruta ?? "(falta la ruta)"}`);
  const data = {...SIN_CONTENIDO, ...JSON.parse(fs.readFileSync(ruta, "utf8"))};
  if (!data.meta) throw new Error(`El videoData.json no trae "meta" (anuncio, producto, fps, ancho, alto, duracion): ${ruta}`);
  return data;
}

async function empaquetar(publicDir) {
  fs.mkdirSync(CACHE, {recursive: true});
  const outDir = fs.mkdtempSync(path.join(CACHE, "bundle-"));
  const serveUrl = await bundle({
    entryPoint: path.join(RAIZ, "src", "index.ts"),
    publicDir,
    outDir,
    webpackOverride: (config) => enableTailwind(config),
  });
  return {serveUrl, outDir};
}

function nombreLibre(carpeta, base) {
  let candidato = path.join(carpeta, `${base}.mp4`);
  let n = 2;
  while (fs.existsSync(candidato)) candidato = path.join(carpeta, `${base}_v${n++}.mp4`);
  return candidato;
}

function parseLista(texto) {
  return String(texto)
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n));
}

/** Fotogramas utiles por defecto: arranque, cada texto grande, cada vista de producto, CTA y tres del medio. */
function fotogramasAutomaticos(data, totalFrames) {
  const fps = data.meta.fps;
  const f = new Set([Math.round(0.4 * fps)]);
  (data.textosGrandes ?? []).forEach((t) => f.add(Math.round((t.inicio + 0.5) * fps)));
  (data.producto ?? []).forEach((p) => f.add(Math.round((p.inicio + 0.8) * fps)));
  if (data.cta) f.add(Math.round((data.cta.inicio + 0.8) * fps));
  [0.25, 0.5, 0.75].forEach((x) => f.add(Math.round(totalFrames * x)));
  return [...f].sort((a, b) => a - b).slice(0, 14);
}

async function comandoRender(ruta, op) {
  const data = leerVideoData(ruta);
  const carpetaAnuncio = path.dirname(path.resolve(ruta));
  const base = `${data.meta.producto}_${data.meta.anuncio}`;
  const salida = op.salida ? path.resolve(op.salida) : nombreLibre(path.join(carpetaAnuncio, "final"), base);
  fs.mkdirSync(path.dirname(salida), {recursive: true});
  const concurrencia = op.concurrencia ? Number(op.concurrencia) : Math.max(1, Math.floor(os.cpus().length / 2));

  console.log(`Anuncio ${base}: ${data.meta.duracion} s a ${data.meta.fps} fps, ${data.meta.ancho}x${data.meta.alto}`);
  const publico = prepararPublico(data);
  let outDir = null;
  try {
    console.log("Navegador de Remotion (la primera vez descarga ~100 MB)...");
    await ensureBrowser();
    console.log("Empaquetando...");
    const empaque = await empaquetar(publico);
    outDir = empaque.outDir;
    const composicion = await selectComposition({serveUrl: empaque.serveUrl, id: ID_COMPOSICION, inputProps: data});

    let rango = null;
    if (op.frames && typeof op.frames === "string") {
      const [a, b] = op.frames.split("-").map(Number);
      rango = [a, Number.isFinite(b) ? b : a];
    }
    console.log(`Renderizando ${composicion.durationInFrames} cuadros con concurrencia ${concurrencia}...`);
    const t0 = Date.now();
    let ultimo = -5;
    await renderMedia({
      composition: composicion,
      serveUrl: empaque.serveUrl,
      codec: "h264",
      outputLocation: salida,
      inputProps: data,
      concurrency: concurrencia,
      ...opcionesDeMemoria(),
      crf: op.crf ? Number(op.crf) : 18,
      frameRange: rango,
      timeoutInMilliseconds: 180000,
      onProgress: ({progress, renderedFrames}) => {
        const pct = Math.floor(progress * 100);
        if (pct >= ultimo + 5) {
          ultimo = pct;
          console.log(`  ${String(pct).padStart(3)} %  (${renderedFrames} cuadros, ${Math.round((Date.now() - t0) / 1000)} s)`);
        }
      },
    });
    const mb = (fs.statSync(salida).size / 1048576).toFixed(1);
    console.log(`LISTO en ${Math.round((Date.now() - t0) / 1000)} s -> ${salida} (${mb} MB)`);
  } finally {
    limpiar(publico, ...(outDir ? [outDir] : []));
  }
}

async function comandoFotogramas(ruta, op) {
  const data = leerVideoData(ruta);
  const carpetaAnuncio = path.dirname(path.resolve(ruta));
  const dirSalida = op.salida ? path.resolve(op.salida) : path.join(carpetaAnuncio, "pruebas");
  fs.mkdirSync(dirSalida, {recursive: true});
  const escala = op.escala ? Number(op.escala) : 0.5;

  const publico = prepararPublico(data);
  let outDir = null;
  try {
    await ensureBrowser();
    console.log("Empaquetando...");
    const empaque = await empaquetar(publico);
    outDir = empaque.outDir;
    const composicion = await selectComposition({serveUrl: empaque.serveUrl, id: ID_COMPOSICION, inputProps: data});
    const total = composicion.durationInFrames;
    const fps = composicion.fps;

    let frames;
    if (op.frames && typeof op.frames === "string") frames = parseLista(op.frames);
    else if (op.segundos && typeof op.segundos === "string") frames = parseLista(op.segundos).map((s) => Math.round(s * fps));
    else frames = fotogramasAutomaticos(data, total);
    frames = [...new Set(frames.map((f) => Math.min(Math.max(0, f), total - 1)))].sort((a, b) => a - b);

    console.log(`Sacando ${frames.length} fotogramas (escala ${escala})...`);
    const archivos = [];
    for (const frame of frames) {
      const archivo = path.join(dirSalida, `f_${String(frame).padStart(5, "0")}.jpg`);
      await renderStill({
        composition: composicion,
        serveUrl: empaque.serveUrl,
        output: archivo,
        frame,
        inputProps: data,
        imageFormat: "jpeg",
        jpegQuality: 82,
        scale: escala,
        timeoutInMilliseconds: 180000,
      });
      archivos.push(archivo);
      console.log(`  cuadro ${frame} (${(frame / fps).toFixed(2)} s) -> ${archivo}`);
    }
    console.log(`LISTO: ${archivos.length} fotogramas en ${dirSalida}`);
  } finally {
    limpiar(publico, ...(outDir ? [outDir] : []));
  }
}

async function main() {
  const [comando, ...resto] = process.argv.slice(2);
  const {opciones, posicionales} = leerOpciones(resto);
  if (comando === "render") return comandoRender(posicionales[0], opciones);
  if (comando === "fotogramas") return comandoFotogramas(posicionales[0], opciones);
  if (comando === "datos") {
    // Las props que recibira Remotion (sin empaquetar ni renderizar): para probar y depurar.
    console.log(JSON.stringify(leerVideoData(posicionales[0])));
    return;
  }
  if (comando === "navegador") {
    await ensureBrowser();
    console.log("Navegador de Remotion listo.");
    return;
  }
  console.log("Uso:\n  node scripts/remotion.mjs render <videoData.json> [--salida ruta.mp4] [--concurrencia N] [--crf 18] [--frames 0-90]\n  node scripts/remotion.mjs fotogramas <videoData.json> [--frames 0,45 | --segundos 1.5,3.2] [--escala 0.5] [--salida carpeta]\n  node scripts/remotion.mjs datos <videoData.json>\n  node scripts/remotion.mjs navegador");
  process.exit(comando ? 1 : 0);
}

main().catch((e) => {
  console.error(`ERROR: ${e?.message ?? e}`);
  process.exit(1);
});
