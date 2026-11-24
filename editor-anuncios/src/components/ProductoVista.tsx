import React from "react";
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from "remotion";
import type {ProductoVisual} from "../types";

type Props = {
  visual: ProductoVisual;
  zonaInferiorLibre: number;
};

/**
 * Portada o paginas del ebook (o fotos del producto) sobre el video. Se monta dentro de un
 * <Sequence>: useCurrentFrame() cuenta desde que entra.
 */
export const ProductoVista: React.FC<Props> = ({visual, zonaInferiorLibre}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const duracionFrames = Math.max(1, Math.round(visual.duracion * fps));

  const entrada = spring({frame, fps, config: {damping: 13, stiffness: 140, mass: 0.8}});
  const salida = interpolate(frame, [duracionFrames - 5, duracionFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Flotacion suave para que no se vea congelado.
  const flota = Math.sin((frame / fps) * 2.2) * 8;

  if (visual.modo === "pantalla") {
    return (
      <AbsoluteFill style={{opacity: salida * entrada, backgroundColor: "#000", pointerEvents: "none"}}>
        <Img src={staticFile(visual.src)} style={{width: "100%", height: "100%", objectFit: "contain"}} />
      </AbsoluteFill>
    );
  }

  const abajo = visual.posicion === "abajo";
  const giro = visual.giro ?? -4;
  const subida = interpolate(entrada, [0, 1], [260, 0]);

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: abajo ? "flex-end" : "center",
        paddingBottom: abajo ? zonaInferiorLibre + 60 : 0,
        pointerEvents: "none",
        opacity: salida,
      }}
    >
      <Img
        src={staticFile(visual.src)}
        style={{
          maxWidth: 760,
          maxHeight: abajo ? 900 : 1150,
          objectFit: "contain",
          borderRadius: 18,
          border: "10px solid #FFFFFF",
          boxShadow: "0 30px 80px rgba(0,0,0,0.55)",
          transform: `translateY(${subida + flota}px) rotate(${giro * entrada}deg) scale(${interpolate(entrada, [0, 1], [0.7, 1])})`,
        }}
      />
    </AbsoluteFill>
  );
};
