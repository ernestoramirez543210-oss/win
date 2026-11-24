import React from "react";
import {AbsoluteFill, Easing, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig} from "remotion";
import type {Toma} from "../types";

/** Transiciones que necesitan ver el plano anterior por debajo mientras entra el nuevo (el anterior se alarga ese rato). */
const NECESITA_FONDO = new Set(["whip", "slide", "circulo", "pagina"]);
const durDe = (t?: Toma["entrada"]) => Math.max(2, Math.round(t?.dur ?? 8));
const sua = (x: number) => Easing.out(Easing.cubic)(Math.min(1, Math.max(0, x)));
/** Ruido repetible por cuadro (0 a 1): la misma sacudida cada vez que se renderiza. */
const ruido = (n: number, k: number) => {
  const x = Math.sin(n * 12.9898 + k * 78.233) * 43758.5453;
  return x - Math.floor(x);
};

const DURACION_TRANSICION = 0.25;

const Una: React.FC<{toma: Toma; duracionFrames: number; salida?: Toma["entrada"]; solape: number; frames: number}> = ({toma, duracionFrames, salida, solape, frames}) => {
  // `frameSeq` cuenta desde que arranca la secuencia (con `transicion` fundido/deslizar empieza `solape` cuadros ANTES del corte);
  // `frame` cuenta desde el corte, como siempre (transiciones `entrada`). Sin solape son lo mismo.
  const frameSeq = useCurrentFrame();
  const frame = frameSeq - solape;
  const {fps} = useVideoConfig();
  const zoom = toma.zoom ?? 1;
  const escala = interpolate(frameSeq, [0, duracionFrames + solape], [toma.zoomIni ?? 1, zoom], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const ajuste = toma.ajuste ?? "cover";
  const posX = (toma.posX ?? 0.5) * 100;
  const posY = (toma.posY ?? 0.5) * 100;
  const velocidad = toma.velocidad ?? 1;

  // ---- transicion `transicion` (la que pone la Fabrica del servidor con aplicar_receta.py). fundido y deslizar entran un poco ANTES del corte (`solape` cuadros)
  // y se apoyan sobre la toma anterior, que sigue debajo; zoom y flash arrancan justo en el corte. k va de 0 a 1 durante la transicion y se queda en 1.
  const tr = toma.transicion;
  const k = tr && frames > 0 ? Math.min(1, Math.max(0, frameSeq / frames)) : 1;
  const suave = 1 - Math.pow(1 - k, 3);
  let opacidad = 1;
  let desplazaX = 0;
  let escalaEntrada = 1;
  let destello = 0;
  if (tr && k < 1) {
    if (tr.tipo === "fundido") opacidad = k;
    else if (tr.tipo === "deslizar") desplazaX = (1 - suave) * 100;
    else if (tr.tipo === "zoom") escalaEntrada = 1 + (1 - suave) * 0.3;
    else if (tr.tipo === "flash") destello = (1 - k) * 0.9;
  }
  // Con solape la toma empieza a verse antes: se retrocede lo mismo en el video original para que el contenido caiga en su sitio (si no hay tanto atras, empieza del principio).
  const atras = (solape / fps) * velocidad;
  const desde = Math.max(0, toma.desde - atras);

  // ---- transicion de ENTRADA de esta toma y de SALIDA (la que empuja la siguiente)
  let tx = 0; // %
  let ty = 0; // %
  let px = 0; // px
  let py = 0;
  let rot = 0;
  let esc = 1;
  let desenfoque = 0;
  let filtro = "";
  let clip: string | undefined;
  let giroY = 0; // grados: la pagina que sale gira sobre su borde izquierdo, como la hoja de un catalogo
  let sombra = 0;
  const capas: React.ReactNode[] = [];
  const ent = toma.entrada;
  if (ent) {
    const E = durDe(ent);
    const p = sua(frame / E);
    const r = 1 - p;
    if (frame < E) {
      switch (ent.tipo) {
        case "whip":
          tx = r * 100;
          desenfoque = r * 16;
          break;
        case "slide":
          ty = r * 100;
          desenfoque = r * 10;
          break;
        case "zoom":
          esc = 1 + 0.55 * r;
          desenfoque = r * 12;
          capas.push(<AbsoluteFill key="z" style={{background: "#FFFFFF", opacity: r * 0.55}} />);
          break;
        case "spin":
          rot = -14 * r;
          esc = 1 + 0.6 * r;
          desenfoque = r * 6;
          break;
        case "circulo":
          clip = `circle(${p * 75}% at 50% 50%)`;
          break;
        case "pagina": {
          const g = Easing.inOut(Easing.cubic)(Math.min(1, frame / E)); // la hoja arranca despacio, se acelera y se asienta
          giroY = (1 - g) * -105;
          sombra = 1 - g;
          break;
        }
        case "shake":
          esc = 1 + 0.12 * r;
          px = (ruido(frame, 1) - 0.5) * 90 * r;
          py = (ruido(frame, 2) - 0.5) * 90 * r;
          rot = (ruido(frame, 3) - 0.5) * 6 * r;
          break;
        case "flash":
          esc = 1 + 0.06 * r;
          capas.push(<AbsoluteFill key="f" style={{background: "#FFFFFF", opacity: Math.min(1, r * 1.1)}} />);
          break;
        case "glitch": {
          px = (ruido(frame, 4) - 0.5) * 110 * r;
          filtro = `hue-rotate(${Math.round((ruido(frame, 5) - 0.5) * 140 * r)}deg) contrast(${1 + 0.45 * r}) saturate(${1 + 0.8 * r})`;
          for (let k = 0; k < 4; k++) {
            const y = ruido(frame, 10 + k) * 100;
            const h = 2 + ruido(frame, 20 + k) * 9;
            capas.push(<div key={`g${k}`} style={{position: "absolute", left: 0, right: 0, top: `${y}%`, height: `${h}%`, background: k % 2 ? "#00F0FF" : "#FF2BD6", opacity: 0.55 * r + 0.1, mixBlendMode: "screen", transform: `translateX(${(ruido(frame, 30 + k) - 0.5) * 160}px)`}} />);
          }
          break;
        }
        default:
          break;
      }
    }
  }
  if (salida && frame >= duracionFrames) {
    const S = durDe(salida);
    const q = sua((frame - duracionFrames) / S);
    if (salida.tipo === "whip") {
      tx = -q * 100;
      desenfoque = q * 14;
    } else if (salida.tipo === "slide") {
      ty = -q * 100;
      desenfoque = q * 10;
    }
  }
  const hayFx = giroY !== 0 || tx !== 0 || ty !== 0 || px !== 0 || py !== 0 || rot !== 0 || esc !== 1 || desenfoque > 0.2 || filtro !== "" || clip !== undefined;
  const filtroFinal = `${desenfoque > 0.2 ? `blur(${desenfoque}px) ` : ""}${filtro}`.trim();

  // Las dos familias de transiciones usan `transform`: si una toma trae las dos se encadenan (lo normal es que traiga solo una).
  const transformEntrada = desplazaX !== 0 || escalaEntrada !== 1 ? `translateX(${desplazaX}%) scale(${escalaEntrada})` : "";
  const transformFx = hayFx
    ? `${giroY !== 0 ? `perspective(1700px) rotateY(${giroY}deg) ` : ""}translate(${tx}%, ${ty}%) translate(${px}px, ${py}px) rotate(${rot}deg) scale(${esc})`
    : "";
  const transformTotal = `${transformFx} ${transformEntrada}`.trim();

  return (
    <AbsoluteFill
      style={{
        overflow: "hidden",
        backgroundColor: "#000",
        ...(opacidad < 1 ? {opacity: opacidad} : {}),
        // pantalla dividida: la toma solo ocupa su franja (sin `region` ocupa todo el cuadro, como siempre)
        ...(toma.region ? {top: `${toma.region.y * 100}%`, height: `${toma.region.alto * 100}%`} : {}),
        ...(transformTotal ? {transform: transformTotal} : {}),
        ...(hayFx
          ? {
              transformOrigin: giroY !== 0 ? "0% 50%" : undefined,
              filter: giroY !== 0 ? `brightness(${1 - 0.4 * sombra})` : filtroFinal || undefined,
              clipPath: clip,
            }
          : {}),
      }}
    >
      {/* muted: el audio original de los videos nunca se usa, solo la locucion. */}
      <OffthreadVideo
        src={staticFile(toma.src)}
        trimBefore={Math.round(desde * fps)}
        playbackRate={velocidad}
        muted
        style={{
          width: "100%",
          height: "100%",
          objectFit: ajuste,
          objectPosition: `${posX}% ${posY}%`,
          transform: `scale(${escala})`,
          transformOrigin: `${posX}% ${posY}%`,
        }}
      />
      {capas}
      {destello > 0 ? <AbsoluteFill style={{backgroundColor: "#fff", opacity: destello}} /> : null}
    </AbsoluteFill>
  );
};

/** Linea de tiempo de video: cada toma entra en su `inicio` y dura `duracion`. Con `entrada` (clones hechos a mano) o `transicion` (Fabrica) entra con ese efecto sobre la anterior. */
export const Tomas: React.FC<{tomas: Toma[]}> = ({tomas}) => {
  const {fps} = useVideoConfig();
  return (
    <>
      {tomas.map((toma, i) => {
        const desdeFrame = Math.round(toma.inicio * fps);
        const duracionFrames = Math.max(1, Math.round(toma.duracion * fps));
        const sig = tomas[i + 1]?.entrada;
        const salida = sig && NECESITA_FONDO.has(sig.tipo) ? sig : undefined;
        const extra = salida ? durDe(salida) : 0;
        // `transicion`: la primera toma no tiene a donde entrar; y no puede pasar de la mitad de esta toma ni de la anterior.
        const tr = i > 0 ? toma.transicion : undefined;
        const previa = i > 0 ? tomas[i - 1] : undefined;
        const tope = previa ? Math.min(toma.duracion, previa.duracion) / 2 : toma.duracion / 2;
        const frames = tr ? Math.max(2, Math.round(Math.min(tr.duracion ?? DURACION_TRANSICION, tope) * fps)) : 0;
        const solape = tr && (tr.tipo === "fundido" || tr.tipo === "deslizar") ? Math.min(frames, desdeFrame) : 0;
        return (
          <Sequence key={i} from={desdeFrame - solape} durationInFrames={duracionFrames + solape + extra} name={`toma ${i + 1}${tr ? ` · ${tr.tipo}` : ""}`}>
            <Una toma={{...toma, transicion: tr}} duracionFrames={duracionFrames} salida={salida} solape={solape} frames={frames} />
          </Sequence>
        );
      })}
    </>
  );
};
