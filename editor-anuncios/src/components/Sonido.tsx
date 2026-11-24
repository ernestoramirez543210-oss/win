import React from "react";
import {Audio, Sequence, staticFile, useVideoConfig} from "remotion";
import type {AudioConfig} from "../types";

/**
 * Toda la capa de sonido: locucion, musica (con fundidos) y efectos puntuales.
 * Los videos de stock y los clips NO aportan audio aqui: las tomas van en `muted`.
 */
export const Sonido: React.FC<{audio: AudioConfig}> = ({audio}) => {
  const {fps, durationInFrames} = useVideoConfig();
  const {voz, musica, sfx} = audio;

  return (
    <>
      {voz ? (
        <Audio
          src={staticFile(voz.src)}
          volume={voz.volumen ?? 1}
          trimBefore={Math.round((voz.desde ?? 0) * fps)}
        />
      ) : null}

      {musica ? (
        <Audio
          src={staticFile(musica.src)}
          trimBefore={Math.round((musica.desde ?? 0) * fps)}
          loop
          volume={(f) => {
            const base = musica.volumen ?? 0.2;
            const entra = Math.max(1, Math.round((musica.fadeInSeg ?? 0.8) * fps));
            const sale = Math.max(1, Math.round((musica.fadeOutSeg ?? 1.5) * fps));
            const subida = Math.min(1, f / entra);
            const bajada = Math.min(1, Math.max(0, durationInFrames - f) / sale);
            return base * subida * bajada;
          }}
        />
      ) : null}

      {(sfx ?? []).map((e, i) => (
        <Sequence key={`${e.src}-${i}`} from={Math.max(0, Math.round(e.t * fps))} name={`sfx ${e.src}`}>
          <Audio src={staticFile(e.src)} volume={e.volumen ?? 0.7} />
        </Sequence>
      ))}
    </>
  );
};
