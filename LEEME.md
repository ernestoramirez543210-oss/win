# Tu dashboard de ventas, tu radar de ofertas y tus herramientas

Esta es una plantilla lista para conectar a **tu** negocio. Trae:

- **El dashboard**: cruza tus ventas (tu hoja de Google Sheets) con lo que gastas en anuncios de Facebook. Te dice cuánto vendiste, cuánto gastaste, el ROAS y qué oferta y qué anuncio venden, por país y por día, también desde el celular.
- **Las reglas**: pausan o reactivan campañas solas según las condiciones que tú pongas (por ejemplo, «si gastó más de 8.000 pesos y no vendió, pausar»).
- **El radar de ofertas**: busca en la Biblioteca de anuncios de Meta ofertas que están vendiendo y las anota en una planilla, y una página para revisarlas.
- **Las herramientas para producir ofertas**:
  - **Tablero**: tus ofertas en producción, cuáles llevar a otros países (Expansión) y qué anuncios variar o revivir (Variaciones).
  - **Crear ebook** y **Modelar PDF**: arman los ebooks de tus ofertas con IA.
  - **Flujos de ZapData**: crean los flujos de venta de WhatsApp de cada oferta (nacen apagados).
  - **Fábrica de ofertas**: un botón que arma la oferta entera (ebooks, flujos y pedido de anuncios) y una guía para lanzarla.
  - **Editor de anuncios**: hace los videos de tus anuncios en tu PC o, si quieres, solo en un servidor propio.

No trae datos de nadie: todo arranca vacío y se llena con lo tuyo.

## Lo que cuesta

- El dashboard, las reglas, el tablero y las páginas: **gratis** (Cloudflare y GitHub en su plan gratuito).
- Lo que usa IA (ebooks, audios, anuncios): pagas tú directo al proveedor, con tu llave (recomendado APIMart). Un ebook de unas treinta páginas sale en unos **treinta centavos de dólar**; un anuncio, centavos. Cada herramienta te muestra el costo y te pide confirmación antes de gastar.
- Los flujos de WhatsApp necesitan tu propia cuenta de **ZapData**.
- Opcional, al final: un **servidor propio** (unos diez a veinte dólares al mes) para que los anuncios se hagan solos y para traer b-roll de TikTok.

## Lo que necesitas

- Un computador con Windows 10 u 11.
- La app de Claude con Claude Code, con **tu propia cuenta**.
- Git instalado y una cuenta de GitHub.
- Tu hoja de ventas en Google Sheets (una pestaña por oferta).
- Acceso de administrador a tu Business Manager de Facebook.
- Para publicarlo en internet: una cuenta gratuita de Cloudflare.

## Paso 1: crea tu copia

1. En la página de esta plantilla en GitHub, pulsa **Use this template → Create a new repository**.
2. Ponle un nombre y márcalo **Private**. Es importante: ahí van a vivir los datos de tu negocio.
3. Descárgalo a tu computador (en la app de Claude: abre una sesión nueva y elige ese repositorio como carpeta, o clónalo con Git).

## Paso 2: mira cómo se ve, con datos inventados

Haz doble clic en **Ver-Ejemplo.cmd**. En uno o dos minutos se abre el dashboard en tu navegador con dos países, tres ofertas y ventas **de mentira**. Así ves cómo funciona antes de tocar nada. La barra de arriba lleva a las demás herramientas: desde el disco se ven, pero solo guardan y crean cosas cuando el sitio ya está publicado.

## Paso 3: conéctalo a tu negocio con Claude

Abre la carpeta en Claude Code y escríbele:

> Lee CLAUDE.md y ayúdame a conectar el dashboard a mi negocio.

Claude te va a guiar paso a paso: tus países y tus hojas de ventas, la llave de Facebook, publicarlo en Cloudflare y dejar el refresco automático cada tres horas (así no pasas los minutos gratis de GitHub). Para el radar:

> Lee radar/LEEME.md y ayúdame a instalar el radar de ofertas.

Y cuando el dashboard ya funcione, una herramienta a la vez (Claude te dice qué necesita y cuánto cuesta cada una antes de conectarla):

> Lee CLAUDE.md, «Las herramientas», y ayúdame a conectar el tablero y el creador de ebooks.

## Cinco reglas de seguridad

1. **Nunca pegues claves, tokens ni contraseñas en el chat.** Los scripts te las piden en una ventana aparte y las guardan donde deben ir.
2. **Tu repositorio siempre privado y nadie más con permiso de escritura, tampoco tu equipo**: tus ventas y tus campañas son tuyas, el historial guarda tu gasto, y quien pueda cambiar el refresco de GitHub puede sacar tus llaves.
3. **El enlace de tu hoja de ventas no se comparte**: quien lo tenga puede ver los datos de tus clientes. Por eso su ID tampoco va en el código: va en `data\libros-de-ventas.json` en tu PC y en los secretos de GitHub (Claude te dice cómo).
4. **Contraseñas largas** (14 caracteres o más; una frase sirve) y **verificación en dos pasos** en GitHub y en Cloudflare: ahí viven tus llaves.
5. **La llave de Facebook, la mínima**: un usuario del sistema con rol Empleado, solo con tus cuentas publicitarias.
