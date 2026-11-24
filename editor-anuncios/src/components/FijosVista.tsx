import React from "react";
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from "remotion";
import type {FijosVisual} from "../types";

/**
 * Elementos fijos que acompanan todo el anuncio (clon del estilo del referente de «Letras lindas», 2026-10-28):
 *  - caja blanca arriba con una frase (con palabras en negrita),
 *  - insignia de color debajo (p. ej. «MATERIAL EN PDF»),
 *  - boton verde de WhatsApp abajo con una flecha.
 * Se monta dentro de un <Sequence>: entra con un resorte y sale con un desvanecido corto.
 */
export const FijosVista: React.FC<{fijos: FijosVisual}> = ({fijos}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const e = spring({frame, fps, config: {damping: 13, stiffness: 170, mass: 0.7}});
  const sale = interpolate(frame, [durationInFrames - 6, durationInFrames], [1, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const fuente = fijos.fuente ?? '"Century Gothic", "Poppins", "Segoe UI", Arial, sans-serif';
  const yCaja = (fijos.yCaja ?? 0.105) * 1920;
  const yBoton = (fijos.yBoton ?? 0.83) * 1920;
  // el boton late despacio para llamar la atencion
  const late = 1 + 0.035 * Math.sin((frame / fps) * 5);

  return (
    <AbsoluteFill style={{pointerEvents: "none", opacity: sale, fontFamily: fuente}}>
      {fijos.caja ? (
        <div style={{position: "absolute", left: 0, right: 0, top: yCaja, display: "flex", flexDirection: "column", alignItems: "center", transform: `translateY(${interpolate(e, [0, 1], [-60, 0])}px)`}}>
          <div style={{background: "#FFFFFF", borderRadius: 26, padding: "16px 34px", fontSize: 44, lineHeight: 1.18, textAlign: "center", color: "#1b1b1b", maxWidth: 820, boxShadow: "0 8px 22px rgba(0,0,0,0.28)"}}>
            {fijos.caja.map((p, i) => (
              <span key={i} style={{fontWeight: p.b ? 800 : 500}}>{p.t}</span>
            ))}
          </div>
          {fijos.insignia ? (
            <div style={{marginTop: 14, display: "flex", alignItems: "center", gap: 12, background: fijos.insignia.color ?? "#F26B3A", color: "#FFFFFF", fontWeight: 800, fontSize: 36, padding: "10px 28px 10px 14px", borderRadius: 18, boxShadow: "0 6px 16px rgba(0,0,0,0.25)"}}>
              <div style={{width: 40, height: 48, background: "#FFFFFF", borderRadius: 6, position: "relative", display: "flex", alignItems: "flex-end", justifyContent: "center"}}>
                <div style={{position: "absolute", top: 0, right: 0, width: 14, height: 14, background: "#E5E5E5", clipPath: "polygon(0 0, 100% 100%, 0 100%)"}} />
                <span style={{fontSize: 15, fontWeight: 900, color: "#D32F2F", paddingBottom: 4, letterSpacing: 0}}>PDF</span>
              </div>
              <span>{fijos.insignia.texto}</span>
            </div>
          ) : null}
        </div>
      ) : null}
      {fijos.boton ? (
        <div style={{position: "absolute", left: 0, right: 0, top: yBoton, display: "flex", justifyContent: "center", transform: `translateY(${interpolate(e, [0, 1], [90, 0])}px) scale(${late})`}}>
          <div style={{display: "flex", alignItems: "center", gap: 14, background: "#2DB742", borderRadius: 60, padding: "12px 34px 12px 14px", boxShadow: "0 8px 22px rgba(0,0,0,0.35)", color: "#FFFFFF"}}>
            <div style={{width: 62, height: 62, borderRadius: 31, background: "#FFFFFF", display: "flex", alignItems: "center", justifyContent: "center"}}>
              <Img src={staticFile(fijos.boton.icono)} style={{width: 52, height: 52, objectFit: "contain"}} />
            </div>
            <div style={{display: "flex", flexDirection: "column", lineHeight: 1.05}}>
              <span style={{fontSize: 24, fontWeight: 500, opacity: 0.95}}>{fijos.boton.pretexto ?? "Escríbenos en"}</span>
              <span style={{fontSize: 42, fontWeight: 800}}>{fijos.boton.texto ?? "WhatsApp"}</span>
            </div>
            <svg width="70" height="64" viewBox="0 0 70 64" style={{marginLeft: 6}}>
              <path d="M6 8 C 40 2, 58 20, 56 44" fill="none" stroke="#FFFFFF" strokeWidth="6" strokeLinecap="round" />
              <path d="M40 40 L56 52 L64 32" fill="none" stroke="#FFFFFF" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
