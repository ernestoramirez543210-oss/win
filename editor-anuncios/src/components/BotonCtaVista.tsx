import React from "react";
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from "remotion";
import type {BotonCta} from "../types";

type Props = {
  boton: BotonCta;
};

/**
 * Etiqueta «Clic en el botón de abajo» + icono grande de WhatsApp (clon del anuncio de referencia de 150 recetas italianas).
 * Se monta dentro de un <Sequence>: useCurrentFrame() cuenta desde que entra.
 *  - La etiqueta (caja negra, texto verde, dos lineas) aparece con un resorte.
 *  - El icono entra deslizado desde la derecha unos cuadros despues y se queda al centro.
 * Va por debajo de los subtitulos (en el original los subtitulos pasan por encima del icono).
 */
export const BotonCtaVista: React.FC<Props> = ({boton}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();

  const pop = spring({frame, fps, config: {damping: 12, stiffness: 190, mass: 0.7}});
  const desliza = spring({frame: frame - 6, fps, config: {damping: 14, stiffness: 120, mass: 0.8}});
  const opacidad = interpolate(frame, [0, 5], [0, 1], {extrapolateRight: "clamp"});

  const color = boton.colorTexto ?? "#02FB1A";
  const lineas = boton.texto.split("\n");
  const centroEtiqueta = (boton.yEtiqueta ?? 0.377) * height;
  const centroIcono = (boton.yIcono ?? 0.6) * height;
  const diametro = boton.tamanoIcono ?? 360;

  return (
    <AbsoluteFill style={{pointerEvents: "none"}}>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: centroEtiqueta,
          display: "flex",
          justifyContent: "center",
          transform: `translateY(-50%) scale(${interpolate(pop, [0, 1], [0.55, 1])})`,
          opacity: opacidad,
        }}
      >
        <div
          style={{
            background: "#000000",
            borderRadius: 38,
            padding: "30px 54px",
            textAlign: "center",
            color,
            fontFamily: '"Poppins", "Montserrat", "Arial", sans-serif',
            fontWeight: 500,
            fontSize: 84,
            lineHeight: 1.18,
            letterSpacing: -1,
            whiteSpace: "pre",
          }}
        >
          {lineas.join("\n")}
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: centroIcono,
          display: "flex",
          justifyContent: "center",
          transform: `translate(${interpolate(desliza, [0, 1], [760, 0])}px, -50%)`,
          opacity: interpolate(frame, [6, 9], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"}),
        }}
      >
        <Img src={staticFile(boton.icono)} style={{width: diametro, height: diametro, objectFit: "contain", filter: "drop-shadow(0 12px 18px rgba(0,0,0,0.45))"}} />
      </div>
    </AbsoluteFill>
  );
};
