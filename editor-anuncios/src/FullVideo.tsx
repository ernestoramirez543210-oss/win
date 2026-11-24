import React from "react";
import {AbsoluteFill, Sequence, useVideoConfig} from "remotion";
import {BotonCtaVista} from "./components/BotonCtaVista";
import {CajaLineasVista} from "./components/CajaLineasVista";
import {CtaVista} from "./components/CtaVista";
import {DestelloVista} from "./components/DestelloVista";
import {FijosVista} from "./components/FijosVista";
import {LibroVista} from "./components/LibroVista";
import {NavidadVista} from "./components/NavidadVista";
import {ProductoVista} from "./components/ProductoVista";
import {RotuloVista} from "./components/RotuloVista";
import {Sonido} from "./components/Sonido";
import {Subtitulos} from "./components/Subtitulos";
import {TextoGrandeVista} from "./components/TextoGrandeVista";
import {Tomas} from "./components/Tomas";
import type {VideoData} from "./types";

/**
 * Composicion principal. No decide nada por su cuenta: todo sale de videoData.json
 * (tomas, subtitulos, textos grandes, producto, llamada a la accion y audio).
 * Orden de capas, de abajo hacia arriba: video, producto, subtitulos, textos grandes, CTA, audio.
 */
export const FullVideo: React.FC<VideoData> = (data) => {
  const {fps} = useVideoConfig();
  const {estilo, zonaInferiorLibre} = data;

  // Los subtitulos se ocultan mientras hay un texto grande en pantalla.
  const ocultarSubtitulos = data.textosGrandes.map((t) => ({inicio: t.inicio, fin: t.fin}));
  // Y suben por encima del boton final mientras se ve: el boton (2 lineas de 70 px + el subtexto) ocupa de la zona libre + 80 px hacia arriba unos 285 px, y la ultima linea de la voz
  // suele seguir sonando bajo el (en FIELTRO el subtexto 'Escribe: fieltro navidad' quedaba pisado por 'Y EMPIEZA HOY.'). Sin subtexto el boton es mas bajo.
  const elevarSubtitulos = data.cta ? [{inicio: data.cta.inicio, fin: data.cta.inicio + data.cta.duracion, extra: data.cta.subtexto ? 380 : 300}] : [];

  return (
    <AbsoluteFill style={{backgroundColor: estilo.fondo ?? "#000000"}}>
      <Tomas tomas={data.tomas} />

      {data.producto.map((v, i) => (
        <Sequence
          key={`producto-${i}`}
          from={Math.round(v.inicio * fps)}
          durationInFrames={Math.max(1, Math.round(v.duracion * fps))}
          name={`producto ${i + 1}`}
        >
          <ProductoVista visual={v} zonaInferiorLibre={zonaInferiorLibre} />
        </Sequence>
      ))}

      {(data.libros ?? []).map((l, i) => (
        <Sequence
          key={`libro-${i}`}
          from={Math.round(l.inicio * fps)}
          durationInFrames={Math.max(1, Math.round(l.duracion * fps))}
          name={`libro ${i + 1}`}
        >
          <LibroVista libro={l} zonaInferiorLibre={zonaInferiorLibre} />
        </Sequence>
      ))}

      {data.botonCta ? (
        <Sequence
          from={Math.round(data.botonCta.inicio * fps)}
          durationInFrames={Math.max(1, Math.round((data.botonCta.fin - data.botonCta.inicio) * fps))}
          name="boton de WhatsApp"
        >
          <BotonCtaVista boton={data.botonCta} />
        </Sequence>
      ) : null}

      {(data.destellos ?? []).map((d, i) => (
        <Sequence key={`destello-${i}`} from={Math.round(d.t * fps)} durationInFrames={Math.max(2, Math.round((d.dur ?? 0.2) * fps))} name={`destello ${i + 1}`}>
          <DestelloVista destello={d} />
        </Sequence>
      ))}

      {data.fijos ? (
        <Sequence from={Math.round(data.fijos.inicio * fps)} durationInFrames={Math.max(2, Math.round((data.fijos.fin - data.fijos.inicio) * fps))} name="elementos fijos">
          <FijosVista fijos={data.fijos} />
        </Sequence>
      ) : null}

      {data.navidad ? (
        <Sequence
          from={Math.round((data.navidad.inicio ?? 0) * fps)}
          durationInFrames={Math.max(2, Math.round(((data.navidad.fin ?? data.meta.duracion) - (data.navidad.inicio ?? 0)) * fps))}
          name="nieve y luces"
        >
          <NavidadVista navidad={data.navidad} />
        </Sequence>
      ) : null}

      {data.cajaLineas ? (
        <Sequence
          from={Math.round(data.cajaLineas.inicio * fps)}
          durationInFrames={Math.max(2, Math.round((data.cajaLineas.fin - data.cajaLineas.inicio) * fps))}
          name="caja de llamada a la accion"
        >
          <CajaLineasVista caja={data.cajaLineas} />
        </Sequence>
      ) : null}

      {(data.rotulos ?? []).map((r, i) => (
        <Sequence key={`rotulo-${i}`} from={Math.round(r.inicio * fps)} durationInFrames={Math.max(2, Math.round((r.fin - r.inicio) * fps))} name={`rotulo ${i + 1}`}>
          <RotuloVista rotulo={r} />
        </Sequence>
      ))}

      <Subtitulos
        palabras={data.palabras}
        estilo={estilo}
        zonaInferiorLibre={zonaInferiorLibre}
        ocultarEn={ocultarSubtitulos}
        elevarEn={elevarSubtitulos}
      />

      {data.textosGrandes.map((t, i) => (
        <Sequence
          key={`texto-${i}`}
          from={Math.round(t.inicio * fps)}
          durationInFrames={Math.max(1, Math.round((t.fin - t.inicio) * fps))}
          name={`texto grande ${i + 1}`}
        >
          <TextoGrandeVista texto={t} estilo={estilo} zonaInferiorLibre={zonaInferiorLibre} />
        </Sequence>
      ))}

      {data.cta ? (
        <Sequence
          from={Math.round(data.cta.inicio * fps)}
          durationInFrames={Math.max(1, Math.round(data.cta.duracion * fps))}
          name="llamada a la accion"
        >
          <CtaVista cta={data.cta} estilo={estilo} zonaInferiorLibre={zonaInferiorLibre} />
        </Sequence>
      ) : null}

      <Sonido audio={data.audio} />
    </AbsoluteFill>
  );
};
