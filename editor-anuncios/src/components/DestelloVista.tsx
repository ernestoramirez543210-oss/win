import React from "react";
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from "remotion";
import type {Destello} from "../types";

/**
 * Destello de pantalla (flash) para dar golpe a un corte o a un efecto de sonido. Se monta dentro de un <Sequence>
 * que dura `dur` segundos: sube de golpe a `intensidad` y baja suave hasta 0.
 */
export const DestelloVista: React.FC<{destello: Destello}> = ({destello}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const total = Math.max(2, Math.round((destello.dur ?? 0.2) * fps));
  const pico = destello.intensidad ?? 0.85;
  const o = interpolate(frame, [0, 1, total], [0, pico, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  return <AbsoluteFill style={{backgroundColor: destello.color ?? "#FFFFFF", opacity: o, pointerEvents: "none"}} />;
};
