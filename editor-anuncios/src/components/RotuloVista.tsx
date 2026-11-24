import React from "react";
import {AbsoluteFill, Img, spring, staticFile, useCurrentFrame, useVideoConfig} from "remotion";
import type {Rotulo} from "../types";

/**
 * Texto fijo con estilo propio: relleno, contorno interior y exterior (doble borde), resplandor, giro e icono opcional debajo.
 * Se usa para la cinta rosa de la pantalla dividida, el recorte neon del dinero y la insignia verde de WhatsApp (oferta «1000 pegatinas»).
 */
export const RotuloVista: React.FC<{rotulo: Rotulo}> = ({rotulo}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const tamano = rotulo.tamano ?? 64;
  const entrada = rotulo.animacion === "ninguna" ? 1 : spring({frame, fps, config: {damping: 11, mass: 0.55, stiffness: 190}});
  const escala = 0.55 + 0.45 * entrada;
  const opacidad = Math.min(1, frame / 3);
  const comun: React.CSSProperties = {
    fontFamily: rotulo.fuente ?? '"Arial Rounded MT Bold", "Segoe UI Black", sans-serif',
    fontWeight: rotulo.peso ?? 800,
    fontStyle: rotulo.cursiva ? "italic" : "normal",
    fontSize: tamano,
    lineHeight: rotulo.interlineado ?? 1.04,
    textTransform: rotulo.mayusculas ? "uppercase" : "none",
    textAlign: "center",
    whiteSpace: "nowrap",
  };
  const lineas = rotulo.lineas.map((l, i) => <div key={i}>{l}</div>);
  const g1 = rotulo.grosor ?? Math.round(tamano / 9);
  const g2 = rotulo.grosor2 ?? 0;
  const resplandor = rotulo.brillo ? `0 0 ${rotulo.brilloPx ?? 24}px ${rotulo.brillo}, 0 0 ${(rotulo.brilloPx ?? 24) * 2}px ${rotulo.brillo}` : undefined;
  return (
    <AbsoluteFill style={{pointerEvents: "none"}}>
      <div
        style={{
          position: "absolute",
          left: rotulo.x !== undefined ? `${rotulo.x * 100}%` : 0,
          right: rotulo.x !== undefined ? "auto" : 0,
          top: `${(rotulo.y ?? 0.5) * 100}%`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          transform: `${rotulo.x !== undefined ? "translateX(-50%) " : ""}translateY(-50%) rotate(${rotulo.giro ?? 0}deg) scale(${escala})`,
          opacity: opacidad,
        }}
      >
        <div
          style={{
            display: "inline-block",
            ...(rotulo.caja
              ? {
                  background: rotulo.caja.fondo,
                  borderRadius: rotulo.caja.radio ?? 22,
                  padding: rotulo.caja.relleno ?? "14px 30px",
                  border: rotulo.caja.borde ? `${rotulo.caja.bordeGrosor ?? 4}px solid ${rotulo.caja.borde}` : undefined,
                  boxShadow: rotulo.caja.sombra ? "0 8px 22px rgba(0,0,0,0.35)" : undefined,
                }
              : {}),
          }}
        >
        <div style={{position: "relative", display: "inline-block"}}>
          {/* capa de atras: contorno exterior grueso y resplandor (es la que da el tamano) */}
          <div
            style={{
              ...comun,
              color: rotulo.contorno2 ?? rotulo.contorno ?? "transparent",
              WebkitTextStroke: g2 > 0 ? `${g2}px ${rotulo.contorno2}` : `${g1}px ${rotulo.contorno ?? "transparent"}`,
              paintOrder: "stroke fill",
              textShadow: resplandor,
            }}
          >
            {lineas}
          </div>
          {/* capa de delante: relleno con el contorno interior */}
          <div
            style={{
              ...comun,
              position: "absolute",
              left: 0,
              top: 0,
              width: "100%",
              color: rotulo.relleno ?? "#FFFFFF",
              WebkitTextStroke: g2 > 0 && rotulo.contorno ? `${g1}px ${rotulo.contorno}` : undefined,
              paintOrder: "stroke fill",
            }}
          >
            {lineas}
          </div>
        </div>
        </div>
        {rotulo.icono ? (
          <Img
            src={staticFile(rotulo.icono.src)}
            style={{width: rotulo.icono.tamano ?? 220, height: rotulo.icono.tamano ?? 220, marginTop: rotulo.icono.separacion ?? 16, objectFit: "contain"}}
          />
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
