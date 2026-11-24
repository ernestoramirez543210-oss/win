import React from "react";
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import type {EstiloConfig, LlamadaAccion} from "../types";

type Props = {
  cta: LlamadaAccion;
  estilo: EstiloConfig;
  zonaInferiorLibre: number;
};

/** Llamada a la accion final: boton grande encima de la zona libre inferior. */
export const CtaVista: React.FC<Props> = ({cta, estilo, zonaInferiorLibre}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const entrada = spring({frame, fps, config: {damping: 12, stiffness: 170, mass: 0.8}});
  const pulso = 1 + Math.sin((frame / fps) * 6) * 0.025;
  const salida = interpolate(frame, [cta.duracion * fps - 4, cta.duracion * fps], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const color = estilo.colorPrimario ?? "#E11D48";
  const fuente = estilo.fuente ?? '"Montserrat", "Arial Black", Impact, sans-serif';

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "flex-end",
        paddingBottom: zonaInferiorLibre + 80,
        pointerEvents: "none",
        opacity: salida,
      }}
    >
      <div
        style={{
          fontFamily: fuente,
          textAlign: "center",
          transform: `translateY(${interpolate(entrada, [0, 1], [120, 0])}px) scale(${pulso * interpolate(entrada, [0, 1], [0.8, 1])})`,
        }}
      >
        <div
          style={{
            display: "inline-block",
            background: color,
            color: "#FFFFFF",
            fontWeight: 900,
            fontSize: 70,
            lineHeight: 1.08,
            textTransform: "uppercase",
            padding: "28px 56px",
            borderRadius: 999,
            boxShadow: "0 18px 50px rgba(0,0,0,0.5)",
            maxWidth: 960,
          }}
        >
          {cta.texto}
        </div>
        {cta.subtexto ? (
          <div
            style={{
              marginTop: 22,
              color: "#FFFFFF",
              fontWeight: 800,
              fontSize: 46,
              textShadow: "0 4px 14px rgba(0,0,0,0.8)",
              WebkitTextStroke: "6px rgba(0,0,0,0.85)",
              paintOrder: "stroke fill",
            }}
          >
            {cta.subtexto}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
