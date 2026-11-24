import React, {useEffect, useRef, useState} from "react";
import {AbsoluteFill, continueRender, delayRender, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import type {EstiloConfig, TextoGrande} from "../types";

type Props = {
  texto: TextoGrande;
  estilo: EstiloConfig;
  zonaInferiorLibre: number;
};

/** Ancho maximo de la caja (px). En un cuadro de 1080 deja ~70 px libres a cada lado, y aun con el rebote de la entrada (la escala llega a ~1,08) queda dentro de la pantalla. */
const ANCHO_MAXIMO = 940;
/** Relleno de la caja (px, al tamano completo): se achica junto con la letra. */
const RELLENO_X = 44;
const RELLENO_Y = 26;
/** Piso de seguridad: una «palabra» imposible (un enlace pegado) no deja la letra en cero. */
const ESCALA_MINIMA = 0.3;
const FUENTE_POR_DEFECTO = '"Montserrat", "Arial Black", Impact, sans-serif';

/** Las palabras que el navegador puede partir en renglones: se separa en espacios y despues de un guion (como hace CSS). Nunca parte una palabra por dentro. */
export function palabrasDe(texto: string): string[] {
  return texto
    .split(/(?<=-)|\s+/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/**
 * Cuanto hay que achicar la letra (0,3 a 1) para que la palabra mas larga quepa en una linea de la caja.
 * `anchoLarga` es el ancho de esa palabra al tamano completo. La caja (con relleno que tambien se achica) y el texto con contorno
 * (el trazo sobresale `tamano / 12` por cada lado) tienen que caber en ANCHO_MAXIMO.
 */
export function escalaParaAncho(anchoLarga: number, tamano: number, conCaja: boolean): number {
  const fijo = conCaja ? 2 * RELLENO_X : tamano / 6;
  const escala = (ANCHO_MAXIMO - 2) / Math.max(1, anchoLarga + fijo);
  return Math.min(1, Math.max(ESCALA_MINIMA, escala));
}

/** Ancho (px) de la palabra mas larga, medido con la fuente cargada. Si no se puede medir, se estima por letras (~0,8 em por letra en mayuscula negrita). */
function anchoDeLaPalabraMasLarga(texto: string, tamano: number, fuente: string): number {
  const palabras = palabrasDe(texto.toUpperCase());
  try {
    const ctx = document.createElement("canvas").getContext("2d");
    if (ctx) {
      ctx.font = `900 ${tamano}px ${fuente}`;
      const medidas = palabras.map((p) => ctx.measureText(p).width);
      if (medidas.length > 0 && medidas.every((m) => Number.isFinite(m) && m > 0)) return Math.max(...medidas);
    }
  } catch {
    // sin canvas: se estima abajo
  }
  return Math.max(0, ...palabras.map((p) => p.length * tamano * 0.8));
}

/**
 * Texto grande sobre las frases que venden. Se monta dentro de un <Sequence>, asi que
 * useCurrentFrame() cuenta desde el momento en que el texto entra.
 *
 * La letra se achica sola si la palabra mas larga no cabe en la caja (antes una palabra como «COMPLICACIONES» a 112 px se
 * salia por la derecha de la pantalla y quedaba cortada). Los textos cortos no cambian de tamano.
 */
export const TextoGrandeVista: React.FC<Props> = ({texto, estilo, zonaInferiorLibre}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const duracionFrames = Math.max(1, Math.round((texto.fin - texto.inicio) * fps));
  const cfg = estilo.textoGrande ?? {};

  const estiloTexto = texto.estilo ?? "caja";
  const colorPrimario = texto.color ?? estilo.colorPrimario ?? "#E11D48";
  const tamanoBase = texto.tamano ?? cfg.tamano ?? 112;
  const fuente = texto.fuente ?? cfg.fuente ?? estilo.fuente ?? FUENTE_POR_DEFECTO;
  const conCaja = estiloTexto !== "plano" && estiloTexto !== "resaltado";

  // Ancho de la palabra mas larga al tamano completo. Se mide una vez, con la fuente ya cargada, y el cuadro espera (delayRender) hasta que el texto ya este pintado con la letra ajustada.
  const [espera] = useState(() => delayRender("Midiendo el texto grande"));
  const liberada = useRef(false);
  const liberar = () => {
    if (!liberada.current) {
      liberada.current = true;
      continueRender(espera);
    }
  };
  const [anchoLarga, setAnchoLarga] = useState<number | null>(null);
  useEffect(() => {
    let vivo = true;
    (async () => {
      try {
        // con tope de espera: si la fuente no llega, el render sigue (mide con la de reemplazo) en vez de quedarse esperando hasta el limite de Remotion
        await Promise.race([document.fonts.load(`900 ${tamanoBase}px ${fuente}`, texto.texto.toUpperCase()), new Promise((r) => setTimeout(r, 5000))]);
      } catch {
        // si la fuente no carga, se mide con la de reemplazo
      }
      if (vivo) setAnchoLarga(anchoDeLaPalabraMasLarga(texto.texto, tamanoBase, fuente));
    })();
    return () => {
      vivo = false;
    };
  }, [texto.texto, tamanoBase, fuente]);
  // El cuadro se libera DESPUES de que React pinto el texto ya ajustado (este efecto corre tras ese pintado); y siempre al desmontarse, para no dejar a Remotion esperando.
  useEffect(() => {
    if (anchoLarga !== null) liberar();
  }, [anchoLarga]);
  useEffect(() => liberar, []);

  const escalaLetra = anchoLarga === null ? 1 : escalaParaAncho(anchoLarga, tamanoBase, conCaja);
  const tamano = Math.max(8, Math.round(tamanoBase * escalaLetra));
  // grosor del contorno (px); por defecto como siempre: tamano / 6
  const grosor = texto.grosorContorno ?? cfg.grosorContorno ?? Math.round(tamano / 6);

  const entrada = spring({frame, fps, config: {damping: 11, stiffness: 190, mass: 0.7}});
  const salida = interpolate(frame, [duracionFrames - 4, duracionFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const escala = interpolate(entrada, [0, 1], [0.55, 1]);

  const posicion = texto.posicion ?? "centro";
  const alineacion: React.CSSProperties =
    posicion === "arriba"
      ? {justifyContent: "flex-start", paddingTop: 300}
      : posicion === "abajo"
        ? {justifyContent: "flex-end", paddingBottom: zonaInferiorLibre + 150}
        : {justifyContent: "center"};

  const base: React.CSSProperties = {
    fontFamily: fuente,
    fontWeight: 900,
    fontSize: tamano,
    lineHeight: 1.05,
    textAlign: "center",
    textTransform: "uppercase",
    maxWidth: ANCHO_MAXIMO,
  };

  let caja: React.CSSProperties;
  if (estiloTexto === "plano") {
    caja = {
      ...base,
      color: cfg.color ?? "#FFFFFF",
      WebkitTextStroke: `${grosor}px ${cfg.contorno ?? "#000000"}`,
      paintOrder: "stroke fill",
      textShadow: "0 10px 30px rgba(0,0,0,0.6)",
    };
  } else if (estiloTexto === "resaltado") {
    caja = {
      ...base,
      color: colorPrimario,
      WebkitTextStroke: `${grosor}px ${cfg.contorno ?? "#000000"}`,
      paintOrder: "stroke fill",
      textShadow: "0 10px 30px rgba(0,0,0,0.6)",
    };
  } else {
    caja = {
      ...base,
      color: "#FFFFFF",
      background: colorPrimario,
      padding: `${Math.round(RELLENO_Y * escalaLetra)}px ${Math.round(RELLENO_X * escalaLetra)}px`,
      borderRadius: Math.round(28 * escalaLetra),
      boxShadow: "0 18px 48px rgba(0,0,0,0.45)",
    };
  }

  const anim = texto.animacion ?? "pop";
  if (anim === "pop") {
    return (
      <AbsoluteFill style={{alignItems: "center", pointerEvents: "none", ...alineacion}}>
        <div style={{...caja, opacity: anchoLarga === null ? 0 : salida, transform: `scale(${escala}) rotate(${(1 - entrada) * -3}deg)`}}>
          {texto.texto}
        </div>
      </AbsoluteFill>
    );
  }

  // Otras animaciones de entrada (2026-10-24). Todas salen con el mismo desvanecido de 4 cuadros.
  const letras = Array.from(texto.texto);
  const palabras = texto.texto.split(" ");
  let contenido: React.ReactNode = texto.texto;
  let transformCaja = "none";
  let filtro = "none";
  if (anim === "letras") {
    // cada letra cae desde arriba con rebote, una tras otra (2 cuadros de separacion)
    let n = 0;
    // separacion entre letras: 2 cuadros, pero nunca mas de ~14 cuadros en total para que los textos largos se lean enseguida
    const paso = Math.min(2, 14 / Math.max(1, letras.filter((c) => c !== " ").length));
    contenido = palabras.map((pal, w) => (
      <span key={w} style={{display: "inline-block", whiteSpace: "nowrap", marginRight: w < palabras.length - 1 ? "0.28em" : 0}}>
        {Array.from(pal).map((ch, k) => {
          const e = spring({frame: frame - n * paso, fps, config: {damping: 9, stiffness: 210, mass: 0.6}});
          n++;
          return (
            <span key={k} style={{display: "inline-block", opacity: interpolate(e, [0, 0.25], [0, 1], {extrapolateRight: "clamp"}), transform: `translateY(${interpolate(e, [0, 1], [-170, 0])}px) scale(${interpolate(e, [0, 1], [0.4, 1])})`}}>
              {ch}
            </span>
          );
        })}
      </span>
    ));
  } else if (anim === "deslizar") {
    const e = spring({frame, fps, config: {damping: 10, stiffness: 150, mass: 0.8}});
    transformCaja = `translateX(${interpolate(e, [0, 1], [-1100, 0])}px) skewX(${interpolate(e, [0, 1], [-14, 0])}deg)`;
  } else if (anim === "tipeo") {
    const visibles = Math.min(letras.length, Math.floor(frame / 1.6) + 1);
    contenido = (
      <>
        {letras.slice(0, visibles).join("")}
        <span style={{opacity: visibles < letras.length || Math.floor(frame / 6) % 2 === 0 ? 1 : 0}}>|</span>
      </>
    );
  } else if (anim === "zoom") {
    const e = spring({frame, fps, config: {damping: 14, stiffness: 140, mass: 0.9}});
    transformCaja = `scale(${interpolate(e, [0, 1], [2.8, 1])})`;
    filtro = `blur(${interpolate(e, [0, 1], [18, 0])}px)`;
  }
  return (
    <AbsoluteFill style={{alignItems: "center", pointerEvents: "none", ...alineacion}}>
      {/* sin la medida el texto no se ve (un cuadro con la letra sin ajustar se veria cortado); al renderizar, delayRender espera a tenerla */}
      <div style={{...caja, opacity: anchoLarga === null ? 0 : anim === "zoom" ? salida * Math.min(1, frame / 3) : salida, transform: transformCaja, filter: filtro}}>{contenido}</div>
    </AbsoluteFill>
  );
};
