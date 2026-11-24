import React from "react";
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from "remotion";
import type {LibroVisual} from "../types";

type Props = {
  libro: LibroVisual;
  zonaInferiorLibre: number;
};

const ANCHO = 520;               // portada
const ALTO = 764;                // proporcion 0,68 (la de la portada)
const GROSOR = 74;               // lomo y canto de las paginas

/**
 * Portada de libro en 3D (CSS) con entrada y salida animadas. Se monta dentro de un <Sequence>:
 * useCurrentFrame() cuenta desde que entra.
 *  - Entrada (~0,7 s): sube desde abajo girando sobre su eje y con un resorte, hasta quedar de tres cuartos.
 *  - Mientras esta: flota y se mece un poco para que no se vea congelado.
 *  - Salida (~0,5 s): gira, se encoge y se va hacia arriba mientras se desvanece.
 */
export const LibroVista: React.FC<Props> = ({libro, zonaInferiorLibre}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const duracion = Math.max(1, Math.round(libro.duracion * fps));
  const salidaFrames = Math.min(14, Math.floor(duracion / 2));

  const e = spring({frame, fps, config: {damping: 12, stiffness: 120, mass: 0.9}});
  const x = interpolate(frame, [duracion - salidaFrames, duracion], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const xs = x * x * (3 - 2 * x);                                     // salida suavizada

  const t = frame / fps;
  const flota = Math.sin(t * 2.2) * 10;
  const base = libro.giro ?? -24;                                     // de tres cuartos: se ve la portada y un poco del lomo
  const meceY = Math.sin(t * 1.3) * 6;

  const anim = libro.animacion ?? "flotante";
  let subida = (1 - e) * 950 - xs * 380;
  let rotY = (1 - e) * -115 + base + meceY + xs * 120;
  let rotZ = (1 - e) * -14 + xs * 10 - 2;
  let escala = (0.5 + 0.5 * e) * (1 - 0.5 * xs);
  let opacidad = Math.min(1, frame / 5) * (1 - xs);
  let desliza = 0;
  let desenfoque = 0;
  if (anim === "lateral") {
    // presentacion lateral: entra de canto por la derecha y se abre a tres cuartos; sale por la izquierda
    subida = Math.sin(t * 2.0) * 8;
    desliza = (1 - e) * 1000 - xs * 1000;
    rotY = (1 - e) * -92 + base + meceY - xs * 70;
    rotZ = (1 - e) * 6 - 2;
    escala = 1 - 0.12 * xs;
    opacidad = Math.min(1, frame / 4) * (1 - xs);
  } else if (anim === "zoom") {
    // entrada con zoom rapido: muy grande y desenfocado, se asienta; sale creciendo
    subida = 0;
    escala = (2.6 - 1.6 * e) * (1 + 0.6 * xs);
    desenfoque = (1 - e) * 16 + xs * 10;
    rotY = (1 - e) * 55 + base + meceY;
    rotZ = (1 - e) * 9 - 2;
    opacidad = Math.min(1, frame / 3) * (1 - xs);
  }

  const color = libro.colorLomo ?? "#8f1717";
  const posicion = libro.posicion ?? "centro";
  const alineacion: React.CSSProperties =
    posicion === "arriba"
      ? {justifyContent: "flex-start", paddingTop: 260}
      : posicion === "abajo"
        ? {justifyContent: "flex-end", paddingBottom: zonaInferiorLibre + 140}
        : {justifyContent: "center"};

  const cara = (extra: React.CSSProperties): React.CSSProperties => ({position: "absolute", ...extra});
  const portada = staticFile(libro.src);

  return (
    <AbsoluteFill style={{alignItems: "center", pointerEvents: "none", ...alineacion}}>
      {/* capas: [opacidad + sombra, aplanan] > [perspectiva + movimiento] > [libro en 3D]. La sombra y la opacidad NO pueden ir en el elemento 3D ni en su perspectiva. */}
      <div style={{opacity: opacidad, filter: `drop-shadow(0 46px 44px rgba(0,0,0,0.55))${desenfoque > 0.2 ? ` blur(${desenfoque}px)` : ""}`}}>
        <div style={{perspective: 1900, transform: `translateX(${desliza}px) translateY(${subida + flota}px) scale(${escala})`}}>
        <div style={{position: "relative", width: 0, height: 0, transformStyle: "preserve-3d", transform: `rotateY(${rotY}deg) rotateZ(${rotZ}deg)`}}>
          {/* tapa */}
          <div
            style={cara({
              left: -ANCHO / 2, top: -ALTO / 2, width: ANCHO, height: ALTO,
              transform: `translateZ(${GROSOR / 2}px)`,
              borderRadius: "3px 10px 10px 3px", overflow: "hidden", backgroundColor: "#2a1208",
            })}
          >
            {/* Img (no fondo CSS): Remotion espera a que cargue antes de capturar el fotograma */}
            <Img src={portada} style={{position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover"}} />
            {/* surco del lomo y brillo */}
            <div style={{position: "absolute", inset: 0, borderRadius: "3px 10px 10px 3px", background: "linear-gradient(90deg, rgba(0,0,0,0.38) 0%, rgba(0,0,0,0.05) 3.5%, rgba(255,255,255,0.18) 5%, rgba(255,255,255,0) 9%)"}} />
            <div style={{position: "absolute", inset: 0, borderRadius: "3px 10px 10px 3px", background: "linear-gradient(115deg, rgba(255,255,255,0.22) 0%, rgba(255,255,255,0) 38%, rgba(0,0,0,0.18) 100%)"}} />
          </div>
          {/* contraportada */}
          <div style={cara({left: -ANCHO / 2, top: -ALTO / 2, width: ANCHO, height: ALTO, transform: `rotateY(180deg) translateZ(${GROSOR / 2}px)`, background: color, borderRadius: "3px 10px 10px 3px"})} />
          {/* lomo (izquierda) con el titulo en vertical */}
          <div
            style={cara({
              left: -GROSOR / 2, top: -ALTO / 2, width: GROSOR, height: ALTO,
              transform: `rotateY(-90deg) translateZ(${ANCHO / 2}px)`,
              background: `linear-gradient(90deg, rgba(0,0,0,0.35), ${color} 30%, ${color} 70%, rgba(0,0,0,0.3))`,
              display: "flex", alignItems: "center", justifyContent: "center",
            })}
          >
            <div style={{writingMode: "vertical-rl", transform: "rotate(180deg)", color: "#FFF3DC", fontFamily: '"Georgia", "Times New Roman", serif', fontWeight: 800, fontSize: 30, letterSpacing: 5, whiteSpace: "nowrap"}}>
              {libro.titulo ?? "150 RECETAS ITALIANAS"}
            </div>
          </div>
          {/* canto de las paginas (derecha) */}
          <div
            style={cara({
              left: -GROSOR / 2, top: -ALTO / 2 + 6, width: GROSOR, height: ALTO - 12,
              transform: `rotateY(90deg) translateZ(${ANCHO / 2 - 4}px)`,
              background: "repeating-linear-gradient(90deg, #f7f0e2 0px, #f7f0e2 3px, #d9cfbb 3px, #d9cfbb 4px)",
            })}
          />
          {/* canto de arriba y de abajo */}
          <div style={cara({left: -ANCHO / 2 + 4, top: -GROSOR / 2, width: ANCHO - 8, height: GROSOR, transform: `rotateX(90deg) translateZ(${ALTO / 2 - 4}px)`, background: "repeating-linear-gradient(0deg, #f7f0e2 0px, #f7f0e2 3px, #d9cfbb 3px, #d9cfbb 4px)"})} />
          <div style={cara({left: -ANCHO / 2 + 4, top: -GROSOR / 2, width: ANCHO - 8, height: GROSOR, transform: `rotateX(-90deg) translateZ(${ALTO / 2 - 4}px)`, background: "repeating-linear-gradient(0deg, #f7f0e2 0px, #f7f0e2 3px, #d9cfbb 3px, #d9cfbb 4px)"})} />
        </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
