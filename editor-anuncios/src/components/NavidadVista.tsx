import React from "react";
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from "remotion";
import type {Navidad} from "../types";

/** Numero pseudoaleatorio estable (0 a 1) para el copo i y la propiedad k: el mismo cuadro siempre da el mismo resultado. */
const r = (i: number, k: number): number => {
  const x = Math.sin(i * 12.9898 + k * 78.233) * 43758.5453;
  return x - Math.floor(x);
};

/**
 * Capas navidenas opcionales (2026-10-29, oferta «+150 recetas navidenas»): copos de nieve que caen y una guirnalda de luces que titilan arriba.
 * Va ENCIMA del video y del libro, DEBAJO de los subtitulos. Sin `navidad` en el videoData no se monta nada.
 */
export const NavidadVista: React.FC<{navidad: Navidad}> = ({navidad}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const t = frame / fps;
  const aparece = interpolate(frame, [0, 12, durationInFrames - 12, durationInFrames], [0, 1, 1, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});

  const copos: React.ReactNode[] = [];
  if (navidad.nieve) {
    const n = navidad.nieve.cantidad ?? 70;
    const vel = navidad.nieve.velocidad ?? 1;
    const color = navidad.nieve.color ?? "#FFFFFF";
    const op = navidad.nieve.opacidad ?? 0.8;
    for (let i = 0; i < n; i++) {
      const tam = 3 + r(i, 1) * 9; // los grandes quedan «cerca»: mas rapidos y difusos
      const v = (55 + tam * 11) * vel;
      const y = ((r(i, 2) * 2100 + t * v) % 2040) - 60;
      const x = r(i, 3) * 1080 + Math.sin(t * (0.6 + r(i, 4)) + i) * (18 + tam * 3);
      copos.push(<circle key={i} cx={x} cy={y} r={tam} fill={color} opacity={op * (0.4 + 0.6 * r(i, 5))} filter={tam > 8 ? "url(#difuso)" : undefined} />);
    }
  }

  const focos: React.ReactNode[] = [];
  let cable = "";
  if (navidad.luces) {
    const colores = navidad.luces.colores ?? ["#FF3B3B", "#FFD54A", "#3DDC84", "#FFFFFF"];
    const y0 = navidad.luces.y ?? 34;
    const festones = 6;
    const ancho = 1080 / festones;
    const caida = 58;
    for (let s = 0; s < festones; s++) {
      const x0 = s * ancho;
      cable += `${s === 0 ? "M" : "L"}${x0} ${y0} Q${x0 + ancho / 2} ${y0 + caida * 2} ${x0 + ancho} ${y0} `;
      [0.2, 0.5, 0.8].forEach((u, k) => {
        const i = s * 3 + k;
        const x = x0 + u * ancho;
        const y = y0 + caida * 4 * u * (1 - u);
        const c = colores[i % colores.length];
        const brillo = 0.62 + 0.38 * Math.sin(t * 3.2 + i * 1.7);
        focos.push(
          <g key={`${s}-${k}`}>
            <circle cx={x} cy={y + 22} r={34} fill={c} opacity={0.3 * brillo} filter="url(#difuso)" />
            <rect x={x - 6} y={y + 2} width={12} height={9} rx={2} fill="#2b2b2b" />
            <ellipse cx={x} cy={y + 22} rx={11} ry={16} fill={c} opacity={0.55 + 0.45 * brillo} />
          </g>,
        );
      });
    }
  }

  return (
    <AbsoluteFill style={{pointerEvents: "none", opacity: aparece}}>
      <svg width="1080" height="1920" viewBox="0 0 1080 1920">
        <defs>
          <filter id="difuso" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="5" />
          </filter>
        </defs>
        {navidad.luces ? <path d={cable} fill="none" stroke="#1d3b27" strokeWidth={5} /> : null}
        {focos}
        {copos}
      </svg>
    </AbsoluteFill>
  );
};
