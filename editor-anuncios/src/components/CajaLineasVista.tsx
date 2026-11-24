import React from "react";
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import type {CajaLineas} from "../types";

/**
 * Caja de llamada a la accion en varias lineas, cada una con su propio fondo (clon del recuadro verde del referente de «recetas de navidad», 2026-10-29):
 * «Escribenos al WhatsApp / y recibe tu pack / ...». Entra con un resorte y sale con un desvanecido corto.
 */
export const CajaLineasVista: React.FC<{caja: CajaLineas}> = ({caja}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const e = spring({frame, fps, config: {damping: 14, stiffness: 160, mass: 0.7}});
  const sale = interpolate(frame, [durationInFrames - 6, durationInFrames], [1, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const tam = caja.tamano ?? 46;
  return (
    <AbsoluteFill style={{pointerEvents: "none", opacity: sale}}>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: (caja.y ?? 0.37) * 1920,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 6,
          transform: `scale(${interpolate(e, [0, 1], [0.8, 1])})`,
          fontFamily: caja.fuente ?? '"Poppins", "Century Gothic", "Segoe UI", Arial, sans-serif',
        }}
      >
        {caja.lineas.map((l, i) => (
          <div
            key={i}
            style={{background: caja.fondo ?? "#1B5E2B", color: l.color ?? caja.colorTexto ?? "#FFFFFF", fontWeight: 800, fontSize: tam, lineHeight: 1.12, padding: "6px 18px", borderRadius: 6, boxShadow: "0 4px 12px rgba(0,0,0,0.35)"}}
          >
            {l.t}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
