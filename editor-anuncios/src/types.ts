// Contrato de src/config/videoData.json (y de anuncios/<producto>/<tipo>/<anuncio>/videoData.json).
// Todos los tiempos van en SEGUNDOS. Todas las rutas son relativas a public/ (como staticFile()).
// Explicacion campo por campo: .claude/skills/editor-video/references/videodata.md

export type Palabra = {
  texto: string;
  inicio: number;
  fin: number;
};

/** Como entra una toma sobre la anterior (la pone scripts/aplicar_receta.py segun la receta de la Fabrica). La primera toma no lleva. */
export type Transicion = {
  /** "fundido" (aparece sobre la anterior), "deslizar" (entra desde la derecha), "zoom" (entra acercandose rapido) o "flash" (un destello blanco en el corte). */
  tipo: "fundido" | "deslizar" | "zoom" | "flash";
  /** Cuanto dura, en segundos (por omision 0.25). No puede pasar de la mitad de la toma que entra ni de la que sale. */
  duracion?: number;
};

/** Un tramo de video de stock o de un clip de IA. */
export type Toma = {
  /** Ruta dentro de public/, p. ej. "productos/velas/broll-tiktok/v1.mp4". */
  src: string;
  /** Segundo del video ORIGINAL donde empieza el tramo. */
  desde: number;
  /** Segundo de la LINEA DE TIEMPO del anuncio donde entra. */
  inicio: number;
  /** Cuantos segundos se ve en pantalla. */
  duracion: number;
  /** "cover" recorta a 9:16 sin deformar (por defecto). "contain" lo deja entero con barras. */
  ajuste?: "cover" | "contain";
  /** Centro del recorte, de 0 a 1 (por defecto 0.5 = centro). */
  posX?: number;
  posY?: number;
  /** 1 = velocidad normal. */
  velocidad?: number;
  /** Zoom lento durante la toma: 1 = ninguno, 1.08 = acercarse 8 %. */
  zoom?: number;
  /** Zoom con el que EMPIEZA la toma (por defecto 1): con zoomIni 1.35 y zoom 1.5 la toma arranca ya acercada (corte con golpe de zoom). */
  zoomIni?: number;
  /** Transicion de ENTRADA de esta toma sobre el corte (2026-10-28; opcional, sin ella el corte es seco como siempre): "whip" (latigazo lateral), "slide" (empuje vertical), "zoom" (golpe de zoom con destello), "spin" (giro con zoom), "circulo" (se abre un circulo), "shake" (sacudida), "flash" (destello blanco), "glitch" (fallo digital), "pagina" (la hoja anterior gira sobre su borde izquierdo y deja ver la nueva, como pasar las paginas de un catalogo; 2026-10-29). `dur` = cuadros (por defecto 8). whip, slide y circulo alargan el plano anterior ese rato para poder verlo por debajo. */
  entrada?: {tipo: string; dur?: number};
  /** Solo para personas: que se ve y con que frase va. */
  nota?: string;
  /** Pantalla dividida (2026-10-30, oferta «1000 pegatinas»; opcional, sin ella la toma ocupa todo el cuadro como siempre): la toma solo se pinta en una franja horizontal. `y` = donde empieza (0 a 1 de la altura) y `alto` = cuanto mide (0 a 1). Ej.: {y: 0, alto: 0.47} = mitad de arriba; {y: 0.47, alto: 0.53} = mitad de abajo. */
  region?: {y: number; alto: number};
  /** true si es un tramo SEGUIDO del mismo clip que la toma anterior (un clip de IA repartido en varios cortes): no es un corte de verdad y aplicar_receta.py no le pone transicion. */
  continuacion?: boolean;
  /** Transicion con la que ENTRA esta toma (ver Transicion; la pone la Fabrica del servidor). Sin este campo el corte es seco. No se mezcla con `entrada` (la de los clones hechos a mano): si trae las dos, mandan las dos a la vez. */
  transicion?: Transicion;
};

export type TextoGrande = {
  texto: string;
  inicio: number;
  fin: number;
  /** "caja" (caja de color), "plano" (blanco con contorno), "resaltado" (amarillo con contorno). */
  estilo?: string;
  /** "centro" (por defecto), "arriba" o "abajo" (encima de la zona libre inferior). */
  posicion?: string;
  /** Color de la caja o del texto resaltado (CSS). */
  color?: string;
  /** Animacion de entrada (opcional; sin ella: «pop» de siempre): "pop" | "letras" (las letras caen con rebote una tras otra) | "deslizar" (entra desde la izquierda con rebote) | "tipeo" (se escribe letra a letra) | "zoom" (entra grande y se asienta). Se agrego 2026-10-24 para variar el estilo entre anuncios. */
  animacion?: string;
  /** Grosor del contorno en px (por defecto tamano / 6; en fuentes condensadas como Impact conviene menos para que no se cierren las letras). */
  grosorContorno?: number;
  /** Fuente propia de este texto (CSS font-family) y tamano en px; si faltan, valen los de estilo.textoGrande. */
  fuente?: string;
  tamano?: number;
};

/** Portada o paginas del ebook (o fotos del producto) mostradas sobre el video. */
/** Portada de libro en 3D con entrada y salida animadas (opcional; se agrego 2026-10-23 para el mockup de 150 recetas italianas). */
export type LibroVisual = {
  /** Imagen de la portada (proporcion 0,68), ruta dentro de public/. */
  src: string;
  inicio: number;
  duracion: number;
  /** "centro" (por defecto), "arriba" o "abajo". */
  posicion?: string;
  /** Texto del lomo (por defecto «150 RECETAS ITALIANAS»). */
  titulo?: string;
  /** Color del lomo y la contraportada (CSS). */
  colorLomo?: string;
  /** Giro base en grados sobre el eje vertical (por defecto -24: de tres cuartos). */
  giro?: number;
  /** Animacion de entrada/salida (2026-10-24; para variar entre anuncios): "flotante" (por defecto: sube girando con resorte), "lateral" (entra deslizando desde la derecha girando de canto a tres cuartos y sale por la izquierda), "zoom" (entra muy grande y desenfocado y se asienta; sale creciendo). */
  animacion?: string;
};

/** Etiqueta «Clic en el botón de abajo» + icono grande de WhatsApp que entra deslizado (opcional; clon del referente de 150 recetas italianas, 2026-10-23). */
export type BotonCta = {
  inicio: number;
  fin: number;
  /** Texto de la etiqueta; el salto de linea 
 parte en dos renglones. */
  texto: string;
  /** Ruta del icono dentro de public/ (png). */
  icono: string;
  /** Color del texto de la etiqueta (por defecto verde #02FB1A). */
  colorTexto?: string;
  /** Centro vertical de la etiqueta y del icono, de 0 a 1 de la altura (por defecto 0.377 y 0.6). */
  yEtiqueta?: number;
  yIcono?: number;
  /** Diametro del icono en px (por defecto 360). */
  tamanoIcono?: number;
};

/** Destello de pantalla (opcional): flash blanco corto para dar golpe a un corte o a un sonido (2026-10-24). */
export type Destello = {
  t: number;
  /** Duracion en s (por defecto 0.2). */
  dur?: number;
  color?: string;
  /** Opacidad maxima de 0 a 1 (por defecto 0.85). */
  intensidad?: number;
};

/** Elementos fijos a lo largo del anuncio (opcional; clon de «Letras lindas», 2026-10-28): caja blanca arriba, insignia de color y boton verde de WhatsApp. */
export type FijosVisual = {
  inicio: number;
  fin: number;
  /** Frase de la caja blanca, en partes; `b: true` pone esa parte en negrita. */
  caja?: {t: string; b?: boolean}[];
  /** Insignia bajo la caja (p. ej. «MATERIAL EN PDF»). */
  insignia?: {texto: string; color?: string};
  /** Boton verde de WhatsApp con flecha. */
  boton?: {icono: string; pretexto?: string; texto?: string};
  fuente?: string;
  /** Centro vertical (0 a 1) de la caja (por defecto 0.105) y del boton (por defecto 0.83). */
  yCaja?: number;
  yBoton?: number;
};

/** Capas navidenas opcionales (2026-10-29, oferta «+150 recetas navidenas»): nieve que cae y guirnalda de luces que titilan arriba. Sin este campo no se monta nada. */
export type Navidad = {
  /** Segundos en que empieza y termina (por defecto todo el anuncio). */
  inicio?: number;
  fin?: number;
  nieve?: {cantidad?: number; color?: string; opacidad?: number; velocidad?: number};
  luces?: {colores?: string[]; y?: number};
};

/** Caja de llamada a la accion en varias lineas, cada una con su fondo (clon del recuadro del referente de «recetas de navidad», 2026-10-29). */
export type CajaLineas = {
  inicio: number;
  fin: number;
  lineas: {t: string; color?: string}[];
  fondo?: string;
  colorTexto?: string;
  /** Centro vertical de arriba (0 a 1; por defecto 0.37). */
  y?: number;
  tamano?: number;
  fuente?: string;
};

/** Texto fijo con estilo propio (2026-10-30, oferta «1000 pegatinas»; opcional): cinta rosa de la pantalla dividida, recorte de dinero con brillo e insignia de WhatsApp. No oculta los subtitulos. */
export type Rotulo = {
  inicio: number;
  fin: number;
  /** Renglones de texto (cada elemento es un renglon). */
  lineas: string[];
  /** Centro vertical, de 0 a 1 de la altura (por defecto 0.5). */
  y?: number;
  fuente?: string;
  /** Tamano en px (por defecto 64). */
  tamano?: number;
  peso?: number;
  cursiva?: boolean;
  mayusculas?: boolean;
  /** Color del relleno de las letras. */
  relleno?: string;
  /** Contorno interior (el que toca las letras): color y grosor en px. */
  contorno?: string;
  grosor?: number;
  /** Contorno exterior (segundo borde, mas grueso, detras del primero): color y grosor TOTAL en px. */
  contorno2?: string;
  grosor2?: number;
  /** Resplandor: color y radio en px. */
  brillo?: string;
  brilloPx?: number;
  /** Entrada: "pop" (por defecto) | "ninguna". */
  animacion?: string;
  /** Grados de giro del bloque. */
  giro?: number;
  interlineado?: number;
  /** Icono bajo el texto (png dentro de public/): diametro y separacion en px. */
  icono?: {src: string; tamano?: number; separacion?: number};
  /** Caja detras del texto (2026-11-03, oferta «ejercicios para el embarazo»; opcional, sin ella nada cambia): etiqueta de color con esquinas redondeadas, como las etiquetas rosa y negras de los referentes. */
  caja?: {fondo: string; radio?: number; relleno?: string; borde?: string; bordeGrosor?: number; sombra?: boolean};
  /** Centro horizontal, de 0 a 1 del ancho (opcional; por defecto centrado). */
  x?: number;
};

export type ProductoVisual = {
  src: string;
  inicio: number;
  duracion: number;
  /** "tarjeta" (imagen flotante con sombra, por defecto) o "pantalla" (ocupa todo el cuadro). */
  modo?: string;
  /** "centro" (por defecto) o "abajo". */
  posicion?: string;
  /** Grados de inclinacion de la tarjeta (por defecto -4). */
  giro?: number;
  nota?: string;
};

export type LlamadaAccion = {
  texto: string;
  subtexto?: string;
  inicio: number;
  duracion: number;
};

export type Sfx = {
  /** Archivo de public/sfx/, p. ej. "sfx/pop.mp3". */
  src: string;
  /** Segundo de la linea de tiempo. */
  t: number;
  volumen?: number;
  /** "receta" = lo puso scripts/aplicar_receta.py (se rehace cada vez que se corre); sin este campo lo puso Claude a mano. */
  origen?: string;
};

export type AudioConfig = {
  voz?: {src: string; volumen?: number; desde?: number};
  musica?: {
    src: string;
    /** 0 a 1. mezclar_audio.py calcula el valor que deja la musica ~15 dB bajo la voz. */
    volumen?: number;
    desde?: number;
    fadeInSeg?: number;
    fadeOutSeg?: number;
  };
  sfx?: Sfx[];
};

export type EstiloConfig = {
  fondo?: string;
  fuente?: string;
  colorPrimario?: string;
  colorSecundario?: string;
  subtitulos?: {
    tamano?: number;
    color?: string;
    colorActivo?: string;
    contorno?: string;
    maxPalabras?: number;
    mayusculas?: boolean;
    /** Pausa (seg) entre palabras que parte el grupo de subtitulos. */
    pausaSeg?: number;
    /** Separacion extra sobre la zona libre inferior, en px. */
    margenInferior?: number;
    /** Letra cursiva (por defecto no: sin esta opcion nada cambia). Se agrego 2026-10-23 para clonar el referente de 150 recetas italianas. */
    cursiva?: boolean;
    /** Grosor del contorno en px (2026-11-03, oferta «ejercicios para el embarazo»; opcional: sin el vale tamano / 7 como siempre) y espacio entre palabras en em (opcional; por defecto 0.14 a cada lado). */
    grosorContorno?: number;
    espaciadoPalabras?: number;
    /** Tramos (opcional) donde los subtitulos usan otro margenInferior: {desde, hasta, margenInferior} en segundos y px. Se agrego 2026-10-23 para bajarlos mientras hay un libro o un boton. */
    ajustes?: {desde: number; hasta: number; margenInferior: number; /** solo para tipo "etiqueta": otro centro vertical (0 a 1) en ese tramo */ yCentro?: number}[];
    /** "etiqueta" = subtitulos dentro de una caja gris redondeada con efecto karaoke (la palabra dicha pasa de gris claro a oscuro; clon del referente 102 de 150 recetas italianas, 2026-10-24). Sin esto: el estilo de siempre. */
    tipo?: string;
    /** Opciones del tipo "etiqueta". */
    etiqueta?: {fondo?: string; colorTexto?: string; colorPendiente?: string; radio?: number; yCentro?: number; fuente?: string; peso?: number; relleno?: string; /** color de la palabra que suena (2026-11-03; opcional: sin el el karaoke es solo de gris a oscuro) */ colorActivo?: string};
  };
  textoGrande?: {
    tamano?: number;
    color?: string;
    contorno?: string;
    /** Fuente de todos los textos grandes (CSS font-family). */
    fuente?: string;
    /** Grosor del contorno en px (por defecto tamano / 6). */
    grosorContorno?: number;
  };
};

export type VideoData = {
  meta: {
    anuncio: string;
    producto: string;
    /** "tiktok" o "ia". */
    tipo: string;
    fps: number;
    ancho: number;
    alto: number;
    /** Duracion total en segundos. */
    duracion: number;
  };
  estilo: EstiloConfig;
  /** Alto (px) libre abajo para la interfaz de la red social. Nada de texto cae aqui. */
  zonaInferiorLibre: number;
  tomas: Toma[];
  palabras: Palabra[];
  textosGrandes: TextoGrande[];
  producto: ProductoVisual[];
  /** Portadas de libro en 3D (opcional: si falta, no se muestra ninguna). */
  libros?: LibroVisual[];
  /** Etiqueta + icono de WhatsApp (opcional). */
  botonCta?: BotonCta;
  /** Destellos de pantalla (opcional). */
  destellos?: Destello[];
  /** Elementos fijos (caja arriba, insignia y boton de WhatsApp). */
  fijos?: FijosVisual;
  /** Nieve y luces de Navidad (opcional). */
  navidad?: Navidad;
  /** Caja de llamada a la accion en lineas (opcional). */
  cajaLineas?: CajaLineas;
  /** Rotulos de texto fijo con estilo propio (opcional; 2026-10-30). */
  rotulos?: Rotulo[];
  cta?: LlamadaAccion;
  audio: AudioConfig;
};
