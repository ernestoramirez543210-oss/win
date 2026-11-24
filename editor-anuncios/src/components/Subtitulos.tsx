import React, {useMemo} from "react";
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from "remotion";
import type {EstiloConfig, Palabra} from "../types";

type Grupo = {inicio: number; fin: number; palabras: Palabra[]};

const FIN_DE_FRASE = /[.!?…]$/;

/** Cuanto crece la palabra resaltada. Crece con `scale` (no mueve a las demas), asi que su ancho extra se reserva de antemano en el hueco (ver el render). */
const ESCALA_RESALTE = 1.07;

/**
 * Junta las palabras de Whisper en grupos cortos de subtitulos: maximo `maxPalabras` por grupo,
 * y el grupo se parte antes si hay una pausa larga o si la palabra cierra una frase.
 */
export function agruparPalabras(palabras: Palabra[], maxPalabras: number, pausaSeg: number): Grupo[] {
  const grupos: Grupo[] = [];
  let actual: Palabra[] = [];
  const cerrar = () => {
    if (actual.length === 0) return;
    grupos.push({inicio: actual[0].inicio, fin: actual[actual.length - 1].fin, palabras: actual});
    actual = [];
  };
  for (const p of palabras) {
    const anterior = actual[actual.length - 1];
    if (anterior && p.inicio - anterior.fin > pausaSeg) cerrar();
    actual.push(p);
    if (actual.length >= maxPalabras || FIN_DE_FRASE.test(p.texto.trim())) cerrar();
  }
  cerrar();
  return grupos;
}

type Props = {
  palabras: Palabra[];
  estilo: EstiloConfig;
  zonaInferiorLibre: number;
  /** Intervalos (seg) donde los subtitulos se ocultan: los de los textos grandes. */
  ocultarEn: {inicio: number; fin: number}[];
  /** Intervalos (seg) donde los subtitulos suben `extra` px: el del boton final, para no pisar su texto. */
  elevarEn?: {inicio: number; fin: number; extra: number}[];
};

export const Subtitulos: React.FC<Props> = ({palabras, estilo, zonaInferiorLibre, ocultarEn, elevarEn = []}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const cfg = estilo.subtitulos ?? {};
  const maxPalabras = cfg.maxPalabras ?? 3;
  const pausaSeg = cfg.pausaSeg ?? 0.4;

  const grupos = useMemo(() => agruparPalabras(palabras, maxPalabras, pausaSeg), [palabras, maxPalabras, pausaSeg]);

  const t = frame / fps;
  if (ocultarEn.some((o) => t >= o.inicio && t < o.fin)) return null;

  const idx = grupos.findIndex((g, i) => {
    const siguiente = grupos[i + 1];
    // Si el siguiente grupo empieza casi pegado, este se queda hasta ahi para que no parpadee.
    const hasta = siguiente && siguiente.inicio - g.fin < 0.3 ? siguiente.inicio : g.fin + 0.15;
    return t >= g.inicio && t < hasta;
  });
  if (idx === -1) return null;

  const grupo = grupos[idx];
  let activa = 0;
  grupo.palabras.forEach((p, i) => {
    if (t >= p.inicio) activa = i;
  });

  const tamano = cfg.tamano ?? 82;
  const color = cfg.color ?? "#FFFFFF";
  const colorActivo = cfg.colorActivo ?? "#FFE14D";
  const contorno = cfg.contorno ?? "#000000";
  const mayusculas = cfg.mayusculas ?? true;
  const fuente = estilo.fuente ?? '"Montserrat", "Arial Black", Impact, sans-serif';

  // Tramos donde los subtitulos bajan (p. ej. mientras hay un libro o un boton en el centro). Se decide por el inicio del grupo para que no salte a media frase.
  const ajuste = (cfg.ajustes ?? []).find((a) => grupo.inicio >= a.desde && grupo.inicio < a.hasta);
  const margen = ajuste?.margenInferior ?? cfg.margenInferior ?? 40;

  const frameDelGrupo = frame - Math.round(grupo.inicio * fps);
  const entrada = interpolate(frameDelGrupo, [0, 4], [0.88, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});

  // Estilo «etiqueta» (clon del referente 102): caja gris redondeada; cada palabra pasa de gris claro a oscuro cuando se dice.
  if (cfg.tipo === "etiqueta") {
    const e = cfg.etiqueta ?? {};
    const alto = 1920;
    const yCentro = (ajuste?.yCentro ?? e.yCentro ?? 0.667) * alto;
    return (
      <AbsoluteFill style={{pointerEvents: "none"}}>
        <div style={{position: "absolute", left: 0, right: 0, top: yCentro, display: "flex", justifyContent: "center", transform: `translateY(-50%) scale(${entrada})`}}>
          <div
            style={{
              background: e.fondo ?? "#CBCBCB",
              borderRadius: e.radio ?? 40,
              padding: e.relleno ?? "26px 46px",
              fontFamily: e.fuente ?? '"Century Gothic", "Poppins", "Segoe UI", Arial, sans-serif',
              fontWeight: e.peso ?? 700,
              fontSize: tamano,
              lineHeight: 1.1,
              whiteSpace: "nowrap",
            }}
          >
            {grupo.palabras.map((p, i) => {
              const pasada = interpolate(frame - Math.round(p.inicio * fps), [0, 5], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
              const c1 = e.colorPendiente ?? "#9A9A9A";
              const c2 = e.colorTexto ?? "#0A0A0A";
              const mix = (a: string, b: string, k: number) => {
                const h = (s: string) => [1, 3, 5].map((j) => parseInt(s.slice(j, j + 2), 16));
                const [r1, g1, b1] = h(a), [r2, g2, b2] = h(b);
                return `rgb(${Math.round(r1 + (r2 - r1) * k)}, ${Math.round(g1 + (g2 - g1) * k)}, ${Math.round(b1 + (b2 - b1) * k)})`;
              };
              return (
                <span key={`${idx}-${i}`} style={{color: e.colorActivo && i === activa ? e.colorActivo : mix(c1, c2, pasada), marginRight: i < grupo.palabras.length - 1 ? "0.28em" : 0}}>
                  {mayusculas ? p.texto.trim().toUpperCase() : p.texto.trim()}
                </span>
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{pointerEvents: "none"}}>
      <div
        style={{
          position: "absolute",
          left: 56,
          right: 56,
          bottom: zonaInferiorLibre + margen + (elevarEn.find((o) => t >= o.inicio && t < o.fin)?.extra ?? 0),
          textAlign: "center",
          fontFamily: fuente,
          fontWeight: 800,
          fontStyle: cfg.cursiva ? "italic" : "normal",
          fontSize: tamano,
          lineHeight: 1.12,
          textTransform: mayusculas ? "uppercase" : "none",
          transform: `scale(${entrada})`,
          transformOrigin: "50% 100%",
        }}
      >
        {grupo.palabras.map((p, i) => {
          const texto = p.texto.trim();
          // Reserva de ancho solo del lado donde hay otra palabra: la primera no la necesita a su izquierda ni la ultima a su derecha.
          const conVecinas = (i > 0 ? 1 : 0) + (i < grupo.palabras.length - 1 ? 1 : 0);
          const alinear = conVecinas === 1 ? (i === 0 ? "left" : "right") : "center";
          return (
            <span key={`${idx}-${i}`} style={{display: "inline-block", margin: `0 ${cfg.espaciadoPalabras ?? 0.14}em`, textAlign: alinear}}>
              {/* Reserva de ancho: copia invisible de la palabra YA agrandada y sin alto. Sin ella, el resalte de una palabra larga (que crece con `scale`, sin mover nada)
                  se comia el hueco con las vecinas y se leia «YDESEANDOQUE». Con ella el resalte cabe en su hueco: ninguna palabra se pega a otra, sea cual sea su largo o la fuente. */}
              <span aria-hidden="true" style={{display: "block", height: 0, overflow: "hidden", visibility: "hidden", fontSize: `${1 + ((ESCALA_RESALTE - 1) / 2) * conVecinas}em`}}>
                {texto}
              </span>
              <span
                style={{
                  display: "block",
                  color: i === activa ? colorActivo : color,
                  WebkitTextStroke: `${cfg.grosorContorno ?? Math.round(tamano / 7)}px ${contorno}`,
                  paintOrder: "stroke fill",
                  textShadow: `0 6px 18px rgba(0,0,0,0.55)`,
                  transform: i === activa ? `scale(${ESCALA_RESALTE})` : "scale(1)",
                }}
              >
                {texto}
              </span>
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
