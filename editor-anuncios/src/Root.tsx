import React from "react";
import {Composition} from "remotion";
import "@fontsource/montserrat/latin-700.css";
import "@fontsource/montserrat/latin-800.css";
import "@fontsource/montserrat/latin-900.css";
import "./index.css";
import {FullVideo} from "./FullVideo";
import videoData from "./config/videoData.json";
import type {VideoData} from "./types";

const porDefecto = videoData as unknown as VideoData;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="FullVideo"
      component={FullVideo}
      width={porDefecto.meta.ancho}
      height={porDefecto.meta.alto}
      fps={porDefecto.meta.fps}
      durationInFrames={Math.max(1, Math.round(porDefecto.meta.duracion * porDefecto.meta.fps))}
      defaultProps={porDefecto}
      // Cada anuncio trae su propio videoData.json (--props): tamano, fps y duracion salen de ahi.
      calculateMetadata={({props}) => ({
        width: props.meta.ancho,
        height: props.meta.alto,
        fps: props.meta.fps,
        durationInFrames: Math.max(1, Math.round(props.meta.duracion * props.meta.fps)),
      })}
    />
  );
};
