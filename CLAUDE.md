# Reglas de este proyecto (para Claude)

Este repositorio es el dashboard de ventas y el radar de ofertas de una persona que vende productos digitales por WhatsApp con anuncios de Facebook en Latinoamérica. Salió de una plantilla: el código funciona, pero hay que **conectarlo a su negocio** (sus países, sus hojas de ventas, sus cuentas de anuncios, su Cloudflare). La persona no es programadora.

## Cómo hablarle

- Español claro, de tú, sin jerga. Si un término técnico es inevitable, explícalo en pocas palabras.
- Habla en el lenguaje del negocio: ventas, gasto, ROAS, ganancia, oferta, anuncio.
- Un paso a la vez. Di qué vas a hacer, hazlo y di qué quedó y qué falta.
- Si algo es ambiguo, di qué entendiste; pregunta solo cuando la respuesta cambie lo que haces.

## Cómo trabajar

- **Nunca pidas ni repitas claves, tokens ni contraseñas en el chat.** Para guardarlas hay scripts que las piden en una ventana aparte (`scripts\Agregar-Token.ps1`, `scripts\Agregar-Variable.ps1`, `scripts\Setup-Cloudflare.ps1`, `scripts\Setup-ReglasClave.ps1`). Si la persona pega una, dile que la borre del chat y que la cambie.
- **Nunca subas a GitHub datos de clientes ni llaves.** `.gitignore` ya deja fuera `data/*.csv` y `data/facebook-tokens.json`: no lo cambies para «arreglar» algo.
- **Verifica con datos reales, no leyendo el código.** Arma el dashboard, ábrelo y cuadra las cifras entre pantallas (las tarjetas de arriba, la tabla por oferta y «Día a día» tienen que dar lo mismo). Si no cuadran y nada lo explica, es un error.
- **Todo se tiene que ver bien en el celular** (360 a 390 px de ancho): nada cortado, botones de 40 px o más. `AGENTS.md` explica cómo medirlo.
- Lo que tiene que dar siempre lo mismo (bajar datos, calcular, cuadrar) va en scripts, no «a ojo».
- Antes de un cambio grande, revisa tu propio diff y prueba los bordes: un país sin datos, varios países, un rango vacío, el celular.

## Cómo está armado

Un **armado determinista en PowerShell** y **páginas HTML que se publican en Cloudflare Pages**:

1. `scripts\Refresh-Headless.ps1` baja cada pestaña de las hojas de ventas como CSV a `data\` (prefijo por país: `col_`, `mx_`, `pe_`, `gt_`, `ar_`, `ec_`, `bo_`, `ve_xlsx_`), baja el gasto de Facebook con `scripts\Fetch-FacebookGraph.ps1` (a `data\facebook-graph.json`) y las tasas de cambio (a `data\fx.json`), y llama a:
2. `scripts\build-dashboard.ps1`: pura aritmética. Une ventas y gasto, empareja cada venta con su anuncio (columna `POST ID` = ID del anuncio), convierte monedas y escribe `data\snapshot.json`. Después mete ese JSON en `templates\dashboard.template.html` y escribe `dashboard-ventas.html` (todos los países), `dashboard-<pais>.html` (uno por país) y `reglas.html` (la misma página en modo reglas).
3. `scripts\Armar-Suite.ps1` arma las páginas de las herramientas (radar, tablero, crear ebook, modelar PDF, APIs, flujos de ZapData, fábrica, editor de anuncios) y les pone a todas la barra de herramientas de arriba (`templates\menu-plantilla.html` + los colores de `templates\suite-tokens.html`).
4. GitHub Actions (`.github\workflows\refresh.yml`) hace todo eso cada tres horas y publica en Cloudflare Pages. Las funciones del servidor están en `functions\` (inicio de sesión, pausar y reactivar anuncios, reglas, radar, tablero, ebooks, flujos, fábrica, editor).

Toda la interacción (filtros, ROAS, gráficas, explorador) es JavaScript dentro del HTML, leyendo el snapshot incrustado.

### Mapa de archivos

- `scripts\build-dashboard.ps1` + `_ventas-comun.ps1`, `_metodos-pago.ps1`, `_salud-comun.ps1`, `_order-bumps.ps1`: el armado.
- `scripts\Refresh-Headless.ps1`: bajar datos + armar. `scripts\Fetch-FacebookGraph.ps1`: gasto de Facebook.
- `templates\dashboard.template.html` (toda la página) + `reglas-modulo.js`, `reglas-ui.js`, `reglas.css`, `torta.js`, `torta.css`, `ganancia-hora.js` (se pegan dentro al armar).
- `templates\radar.template.html` + `radar-seguimiento.js`: la página del radar.
- `functions\_middleware.js`: quién entra a qué (contraseñas por rol). `functions\api\toggle-ad.js`: pausar, reactivar y cambiar presupuestos en Facebook. `functions\api\reglas*.js`: guardar y ejecutar reglas. `functions\api\radar-*.js`: el radar.
- `scripts\reglas-auto.cjs`: el refresco revisa y ejecuta las reglas con él.
- `radar\`: el motor del radar (Python). Su guía: `radar\LEEME.md`.
- Herramientas (ver «Las herramientas» abajo): `templates\produccion.template.html` (tablero), `armar-pdf.template.html` + `ebook-produccion.js` (crear ebook), `modelar-pdf.template.html` + `modelar-motor.js`, `apis.template.html`, `ofertas.template.html` (flujos de ZapData), `fabrica.template.html` + `fabrica-motor.js` + `fabrica-campanas.js`, `editor-anuncios.template.html`; sus funciones en `functions\api\` (`tablero`, `ebook-*`, `config`, `apimart`, `zapflow`, `paises`, `fabrica*`, `editor-*`…) y las piezas compartidas en `functions\_llm.js` (IA), `_zapdata.js`, `_paises.js`, `_fabrica.js`.
- `editor-anuncios\`: el editor de videos 9:16 (Remotion + Whisper) que se instala en un PC o en un servidor. Su guía: `editor-anuncios\CLAUDE.md` y `editor-anuncios\INSTALAR.txt`; la del servidor: `editor-anuncios\vps\LEEME.md`.
- `Ver-Ejemplo.cmd` / `scripts\Ver-Ejemplo.ps1`: arma el dashboard con datos inventados en `_ejemplo\` (sirve también para probar que el armado funciona en el PC).

### Comandos

```
powershell -ExecutionPolicy Bypass -File scripts\Ver-Ejemplo.ps1                       # ejemplo con datos inventados
powershell -ExecutionPolicy Bypass -File scripts\Refresh-Headless.ps1 -SkipDeploy       # bajar datos reales y armar (en el PC)
powershell -ExecutionPolicy Bypass -File scripts\build-dashboard.ps1                    # solo armar con lo que ya hay en data\
powershell -ExecutionPolicy Bypass -File scripts\Armar-Suite.ps1                        # armar las herramientas y el menu (despues del dashboard)
```

Pruebas que no usan red: `scripts\Probar-FechasDeVenta.ps1`, `Probar-MontosImposibles.ps1`, `Probar-OrderBumps.ps1`, `Probar-NoSonOfertas.ps1`. Del radar: `python test_logic.py` (y los demás `test_*.py`) dentro de `radar\`.

## Conectar el dashboard a un negocio nuevo (paso a paso)

Hazlo en este orden y verifica cada paso antes del siguiente. Lleva una lista de lo que quedó hecho.

### Paso 0. Que el armado funcione en el PC

Corre `scripts\Ver-Ejemplo.ps1`. Si se abre el dashboard con datos inventados, PowerShell y el armado están bien.

### Paso 1. Países y hojas de ventas

1. Pregunta en qué países vende y en qué moneda cobra en cada uno. El código ya trae ocho países: México, Venezuela, Colombia, Perú, Guatemala, Argentina, Ecuador y Bolivia. Si vende en otro, ve a «Agregar o quitar un país».
2. **La hoja de ventas**: un libro de Google Sheets por país, una pestaña por oferta, con estas columnas (el orden no importa): `NOMBRE`, `FECHA`, `PAGO RECIBIDO` (obligatorias), `POST ID` (el ID del anuncio que trajo la venta; sin él no se sabe qué anuncio vendió), `NÚMERO`, `MÉTODO DE PAGO`, `Etiquetas`. La `FECHA` como `d/M/yyyy H:mm:ss` o `yyyy-MM-dd HH:mm:ss`. Si la persona usa ZapData o ClicChat, sus integraciones ya escriben así.
3. El libro tiene que estar compartido como **«Cualquier persona con el enlace puede ver»**: el refresco lo baja sin iniciar sesión. Explícale que por eso el enlace es privado: quien lo tenga ve los datos de sus clientes.
4. El ID del libro (lo que va entre `/d/` y `/edit` en la dirección) **no se escribe en el código**: con él cualquiera abre la hoja. Va en dos lugares, ninguno en GitHub como código:
   - En su PC: `data\libros-de-ventas.json` (no se sube a GitHub), por ejemplo `{ "CO": "<ID del libro de Colombia>", "MX": "<ID del de México>" }`. Ábreselo en el Bloc de notas y que lo pegue ella.
   - En GitHub: un **secreto** por país (Settings → Secrets and variables → Actions → Secrets; así no se ve en los registros ni lo ven los colaboradores): `SHEET_ID_CO`, `SHEET_ID_MX`, `SHEET_ID_PE`, `SHEET_ID_GT`, `SHEET_ID_AR`, `SHEET_ID_EC`, `SHEET_ID_BO`, `SHEET_ID_VE`.
   Un país sin ID no se baja. `scripts\Refresh-Headless.ps1` los lee con `Get-LibroDeVentas`.
5. Las pestañas **visibles** se descubren solas. Una pestaña oculta con ventas no se baja: agrégala a la lista del país (`$coTabs`…, con `n` = nombre de la pestaña, `f` = archivo, `k` = palabra clave).

### Paso 2. La llave de Facebook

1. En Business Manager: Configuración del negocio → Usuarios del sistema → crear uno con rol **Empleado** (no Administrador), asignarle **solo** sus cuentas publicitarias (con permiso de administrar campañas), y generar un token **que no venza** con los permisos `ads_read` y `ads_management`. Nada más: ni `business_management` ni acceso al negocio entero; si alguien roba ese token, solo alcanza esas cuentas. Si más adelante va a usar «Programar campañas» de la Fábrica, asígnale también sus **páginas de Facebook** (con permiso de crear anuncios) y suma `pages_show_list`, `pages_read_engagement` y `pages_manage_ads`. Guíala clic por clic.
2. Que copie el token y corra en SU terminal: `& scripts\Agregar-Token.ps1 -Pais colombia` (el nombre del país en minúsculas y sin tildes). El script lo toma del portapapeles, lo valida contra Facebook y lo guarda en `data\facebook-tokens.json` (no se sube a GitHub; si no existe, el script lo crea con el país). Uno por país. Nunca armes ese archivo a mano.
3. Prueba: `scripts\Refresh-Headless.ps1 -SkipDeploy` y abre `dashboard-ventas.html`. Cuadra con la persona: ventas de ayer contra su hoja, gasto de ayer contra el Administrador de anuncios.

**Para que el gasto quede en la oferta correcta**, el nombre de cada campaña tiene que decir el nombre de la oferta (como se llama su pestaña): por ejemplo `RECETAS NAVIDENAS | 1 NOV | INT - COM | 101`. Si la campaña es de ZapData, que empiece por `ZAP` (`ZAP #3 | RECETAS NAVIDENAS | ...`). Lo que no nombra ninguna oferta queda «sin atribuir».

### Paso 3. Publicarlo en internet (Cloudflare Pages)

1. Cuenta gratuita en Cloudflare. Un **token de cuenta** (Mi perfil → API Tokens no; es en la cuenta: Manage Account → Account API Tokens) con permiso de editar Cloudflare Pages. Que lo guarde ella en `data\cloudflare-config.json` así: `{ "apiToken": "...", "accountId": "...", "projectName": "<nombre-sin-espacios>" }` (el archivo no se sube a GitHub; ábreselo en el Bloc de notas para que lo llene ella).
2. `powershell -ExecutionPolicy Bypass -File scripts\Setup-Cloudflare.ps1` (**sin** `-Password`) crea el proyecto y guarda las llaves de Facebook, la contraseña y la firma de las sesiones en Cloudflare. El script le pide la contraseña en la ventana, sin mostrarla, dos veces. **Que lo corra ella en su propia ventana de PowerShell, no a través de Claude**: así la contraseña no queda en el chat ni en el historial. Tiene que tener 14 caracteres o más (una frase larga sirve): el script no acepta una más corta y el sitio no abre con una más corta, tampoco las de rol o de país. Para cambiarla después: el mismo comando con `-CambiarClave`. Las de rol y de país, solo si las pide: `-TraffickerPassword ?` o `-ClavesPais @{ co = '?' }` y el script las pide igual. Cada vez que cambia una contraseña, el script cambia también la llave de las sesiones, para que se cierren las de todos (una sesión dura 30 días) y cada quien vuelva a entrar con la suya. **Nada de eso vale hasta el siguiente despliegue**: Cloudflare aplica las variables nuevas cuando se publica otra vez, así que en seguida que vaya a GitHub → Actions → «Refresco del dashboard» → Run workflow; hasta que termine, la contraseña vieja y las sesiones abiertas siguen sirviendo. Cambiar la llave de las sesiones también corta los pedidos de clips de IA que el editor o el servidor tengan en curso (ya pagados): si están generando un anuncio, que espere a que terminen. Que cierre la ventana al terminar.
   Esas variables quedan visibles para quien entre a su cuenta de Cloudflare (los scripts las mandan como texto normal a propósito: las vuelven a leer cada vez que agregan una). Que active la **verificación en dos pasos** en Cloudflare y en GitHub, que **no invite a nadie** a su cuenta de Cloudflare (ni siquiera de solo lectura: vería todas las contraseñas) y que trate el token de Cloudflare (`data\cloudflare-config.json` y el secreto de GitHub) como su contraseña: solo con permiso de Pages.
   **Nadie más con acceso de escritura a su repositorio de GitHub, tampoco su equipo**: quien pueda cambiar el flujo de `refresh.yml` puede hacer que imprima los secretos (las llaves de Facebook, de Cloudflare y los libros de ventas). Si alguien del equipo necesita ver el código, que sea con permiso de solo lectura.
   **Pausar, reactivar y cambiar presupuestos desde el dash piden la contraseña del dueño**, también a la trafficker (a los demás roles el sitio ni les deja intentarlo). No se la dé a nadie: con ella se ven las llaves de IA y los datos de cobro. La trafficker pausa desde el Administrador de anuncios de Facebook.
   **Ojo con la clave de trafficker: puede crear, cambiar y encender reglas**, y una regla encendida pausa, reactiva y cambia presupuestos sola en cada refresco, **sin** pedir la contraseña del dueño (con los frenos de la regla: su fórmula, un tope de cambios por día y sin repetir lo ya hecho). Así funciona en el negocio de donde salió la plantilla. Que le dé esa clave solo a quien le confiaría el presupuesto y que revise de vez en cuando el registro de `/reglas`.
   **Si alguien deja el equipo**, el mismo día:
   - Que le cambie su contraseña (`-TraffickerPassword ?`, `-EditorPassword ?`, `-VideosPassword ?` o `-ClavesPais @{ co = '?' }`; el script cambia también la llave de las sesiones), o, si no tenía clave propia, que corra el script con `-CerrarSesiones`. Si esa persona sabía la contraseña del dueño, también `-CambiarClave`, y que cambie las llaves que haya podido ver en `/apis`.
   - Si editaba videos o tenía el PC del editor: la llave del editor (`CLAVE_EDITOR`, que va en el `.env` del editor y no depende de ninguna contraseña) le sigue sirviendo para gastar el saldo de APIMart. Que la cambie con `& scripts\Setup-ClaveEditor.ps1 -Rotar`; si el `.env` de ese PC tenía `SCRAPECREATORS_API_KEY` (de pago por uso), que cambie también esa llave en su página.
   - Que revise **todas** las reglas encendidas en `/reglas` (el sitio no guarda quién creó o encendió cada una) y apague las que no reconozca.
   - GitHub → Actions → «Refresco del dashboard» → Run workflow, y que termine **en verde**: hasta ese despliegue nada de lo anterior vale (si sale en rojo, no se publicó y lo viejo sigue sirviendo).
   - Solo después, si tiene servidor, `scripts\Setup-ClaveEditorVps.ps1` (su prueba falla mientras la llave nueva no esté desplegada) y que les pase la línea nueva a los editores que se quedan.
   La página de un socio de país (`socio-<pais>`) muestra solo su país, pero los nombres reales de las ofertas viajan dentro del código de la página: dale una clave de país solo a quien pueda conocerlos.
3. En el panel de Cloudflare, en el proyecto de Pages → Settings → **Bindings → KV namespace**: crea un espacio KV y conéctalo con el nombre **`SUITE`** (ahí se guardan las reglas, el registro y el radar).
   El sitio pide contraseña siempre: no hay ninguna variable que lo deje abierto, y no la agregues.
   **Contra quien pruebe contraseñas:** el propio sitio frena: cada intento fallido tarda un segundo y, tras 10 fallos seguidos desde una misma dirección de internet, la cierra 15 minutos. Es un freno, no un candado: se cuenta en la caché de Cloudflare (cada centro de datos de Cloudflare lleva su propia cuenta, y muchos intentos a la vez pueden colarse antes de que cuente), quien tenga muchas direcciones (por ejemplo con IPv6) puede repartir los intentos, una entrada correcta (con cualquier contraseña, también la de un socio) vuelve a poner la cuenta en cero, y mientras una dirección está cerrada tampoco entra con la contraseña buena (si le pasa a ella, que espere los 15 minutos). Por eso lo que de verdad protege es la contraseña larga. La regla de «Rate limiting» del WAF de Cloudflare **no** sirve sobre `<proyecto>.pages.dev` (solo existe en un dominio propio). **Recomendado si el sitio lo usa más gente o tiene mucha plata en juego: Cloudflare Zero Trust → Access** (gratis hasta 50 usuarios) delante del sitio, que pide un código por correo antes de la contraseña y solo a los correos que ella autorice; o un dominio propio con su regla de WAF y la dirección `pages.dev` redirigida a ese dominio.
4. En GitHub (su repositorio → Settings → Secrets and variables → Actions):
   - Secrets: `FACEBOOK_TOKENS` (el contenido ENTERO de `data\facebook-tokens.json`: que lo copie con `Get-Content data\facebook-tokens.json -Raw | Set-Clipboard` y lo pegue), `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` y los `SHEET_ID_<PAIS>` del paso 1.
   - Variables: `CLOUDFLARE_PROYECTO` = el nombre del proyecto, `DASH_URL` = `https://<proyecto>.pages.dev` (con https).
5. El botón de refrescar del dash (opcional): en `functions\api\refrescar.js`, cambia `TU-USUARIO/TU-REPO` por su repositorio, y en Cloudflare una variable `GH_TOKEN` con un token de GitHub **de grano fino** (Settings → Developer settings → Fine-grained tokens): solo ese repositorio, solo el permiso **Actions: Read and write**, con fecha de vencimiento. Nunca un token clásico con `repo` o `workflow`: quien lo tuviera podría cambiar el flujo y llevarse las llaves. Sin `GH_TOKEN` el botón no hace nada y el refresco sigue solo.
6. En GitHub → Actions → «Refresco del dashboard» → Run workflow. Cuando termine, que entre a `https://<proyecto>.pages.dev` con su contraseña. Desde ahí corre solo **cada tres horas**.
   Por qué cada tres horas: en un repositorio privado GitHub regala unos 2.000 minutos de Actions al mes y cada vuelta gasta unos 5; cada hora pasaría el cupo (le cobrarían o se detendría a mitad de mes). Si quiere otra frecuencia, cámbiala en el `cron` de `refresh.yml` y mira el consumo en GitHub → Settings → Billing. **Nunca hagas público el repositorio para tener minutos gratis**: el historial guarda su gasto de Facebook y el registro de sus reglas.

### Paso 4. Reglas automáticas (opcional)

Las reglas se crean en la página `/reglas` y nacen apagadas. Para que el refresco las revise y las ejecute solo: `powershell -ExecutionPolicy Bypass -File scripts\Setup-ReglasClave.ps1 -Repo <usuario>/<repo>` (crea la llave en Cloudflare y en GitHub). Explícale que una regla encendida mueve plata de verdad en Facebook, y que «Presupuesto diario» es el tope que le puso a la campaña, **no** lo que gastó (para «gastó más de X» se usa «Gasto»).

### Paso 5. La página del radar

1. Instala el motor del radar con `radar\LEEME.md` (llena una planilla de Google Sheets con las ofertas que encuentra).
2. En esa planilla: Archivo → Compartir → Publicar en la web → pestaña **OFERTAS** → **CSV** → Publicar. Que copie el enlace.
3. `& scripts\Agregar-Variable.ps1 -Nombre RADAR_CSV_URL -Valor (Get-Clipboard -Raw).Trim()` (con `&`, no con `-File`). El script imprime el largo del enlace: tiene que coincidir con el copiado.
4. El siguiente refresco lo toma. La página `/radar` muestra las ofertas, permite marcar su estado y vigilarlas.

## Agregar o quitar un país

El código no lee los países de una lista: cada país tiene sus variables. Para **quitar** uno basta con dejar vacío su libro (paso 1). Para **agregar** uno nuevo, busca `bolivia` en el repositorio (fue el último en agregarse) y replica cada aparición:

- `scripts\Refresh-Headless.ps1`: su `$XX_ID = (Get-LibroDeVentas 'XX')`, la lista de pestañas y la entrada en la lista de países (con su prefijo de archivo); y su `SHEET_ID_XX` en el paso «Bajar ventas y gasto» de `.github\workflows\refresh.yml`.
- `scripts\Fetch-FacebookGraph.ps1`: las dos listas de países.
- `scripts\build-dashboard.ps1`: descubrir pestañas, el bloque de lectura del país, montos imposibles, `AccountsFor`, la tasa de cambio (si es moneda nueva), los mapas de gasto, `saludPaises` y `monedaGasto`, el bloque del país en el snapshot y `$countryLabels`.
- `templates\dashboard.template.html`: `COUNTRY_LABEL`, bandera y moneda.
- `functions\_middleware.js` (lista `PAISES`) y `scripts\Setup-Cloudflare.ps1` (`$PAISES_VALIDOS`).
- Si la moneda es nueva, que `data\fx.json` la traiga (sale de open.er-api.com).

Después: arma, abre el dashboard, elige ese país y cuadra ventas y gasto de un día contra la hoja y Facebook.

## Las herramientas (después del dashboard)

Todas se publican con el refresco (no hay que hacer nada para que aparezcan en la barra de arriba), pero cada una necesita algo para **funcionar**. Conéctalas en este orden: primero las que solo usan Cloudflare, al final las que necesitan un servidor. Explícale a la persona qué cuesta cada una ANTES de conectarla. Las llaves de estas herramientas viven en Cloudflare (KV o variables), **nunca en GitHub ni en el chat**.

### 1. APIs (`/apis`): la llave de IA y la voz

Todo lo que escribe o dibuja con IA (ebooks, mockups, audios, anuncios) usa lo que se guarde aquí.
- **Texto e imágenes**: lo más simple es **APIMart** (una sola llave para texto, imagen y video; se recarga saldo en su página). También sirve una llave de **Gemini** (Google AI Studio) para el texto. La persona pega la llave en la página `/apis` desde su navegador (se guarda en el KV `SUITE` del sitio, no pasa por el chat ni por GitHub, y la página solo vuelve a mostrar sus últimos cuatro caracteres).
- **Voz** (audios de la oferta y locución de anuncios): **Fish Audio**, llave de su sección API/Developer y el id de la voz por defecto en «Voz por defecto». Sin voz elegida, Fish usa una al azar. Si quiere una voz fija para todo, también se puede poner su id en `functions\_voces.js` (`VOZ_ESTANDAR.id`).
- Costo de referencia: un ebook de unas treinta páginas con APIMart (texto Gemini Flash-Lite e imágenes gpt-image-2) sale en unos treinta centavos de dólar. Cada herramienta muestra el costo y pide confirmación antes de gastar.

### 2. Tablero (`/tablero`)

Solo usa Cloudflare (KV `SUITE`, el mismo de las reglas). Tres pestañas: el tablero de producción de ofertas, **Expansión LATAM** (ofertas a llevar a otro país) y **Variaciones de anuncios** (con «Revivir ofertas», que sale de las ventas por oferta del dashboard: `scripts\Sync-Offers.ps1` y `scripts\Anuncios-Por-Oferta.ps1`, que corre `Armar-Suite.ps1`). «Hacerla en el servidor» necesita el corredor de anuncios (punto 6) y viene apagado.

### 3. Crear ebook (`/pdf`) y Modelar PDF (`/modelar-pdf`)

Necesitan la llave de IA del punto 1. `/pdf` arma el ebook página por página (y la producción entera de un pedido del radar con un tope de gasto que confirma la persona); `/modelar-pdf` toma el PDF de un entregable ajeno y saca una colección nueva en español. El PDF se arma en el navegador (pdf.js y las fuentes viven en `vendor\`).

### 4. Flujos de ZapData (`/ofertas`)

Crea los flujos de venta de WhatsApp en la cuenta de ZapData de la persona. Nacen **apagados y sin número**: nada le escribe a un cliente hasta que ella los active en ZapData.
1. **Conexión**: en ZapData → Integraciones → Conectar con Claude, que cree una credencial «Token estático» con el permiso «Crear borrador de flujo». Que la pegue ella en `/ofertas`, pestaña **Conexión** (se guarda en KV). También se acepta como variable `ZAPDATA_TOKEN` del proyecto de Cloudflare.
2. **Países**: en la pestaña **Países**, que llene por cada país donde vende: los precios del guion, cómo se escribe el precio, el Pixel de Meta, el flujo de pagos de ZapData de ese país y el texto con sus cuentas de cobro. Se guarda en KV. **Nunca escribas cuentas de cobro ni teléfonos en el código** (`functions\_paises.js` trae los países vacíos a propósito). Un país incompleto no se puede elegir al crear una oferta.
3. **La IA de los flujos**: los bloques que leen los comprobantes de pago usan la clave de OpenAI conectada en SU ZapData (Integraciones → IA de plataforma). ZapData exige el id de esa integración: sácalo de la respuesta de búsqueda de bloques de ZapData (trae `ownIntegrations`) o del panel, y pégalo en `functions\_oferta-plantilla.js`, constante `INTEGRACION_IA_OPENAI`. Sin él, los flujos salen sin esos bloques.
4. Opcional: `ZAPDATA_WEBHOOK_URL` (variable de Cloudflare) si quiere que el flujo de entrega le avise a una hoja o a otro sistema.
ZapData es de pago (su propia suscripción); los flujos no gastan IA de la plantilla.

### 5. Fábrica de ofertas (`/fabrica`)

Junta todo para una oferta: ebooks y material (usa el punto 1), pedidos de anuncios (puntos 6 y 7) y flujos (punto 4), con una guía de lanzamiento. Lo de ebooks y flujos funciona en cuanto estén conectados esos puntos; los anuncios quedan «pedidos» hasta que un editor o el corredor los tome. «Programar campañas en Facebook» usa el mismo token de Facebook del dashboard y crea las campañas **en pausa**; necesita que el token tenga permiso sobre las páginas de Facebook. Si quiere que la guía conozca sus números de WhatsApp, crea `data\lineas-whatsapp.json` (lo explica `scripts\Armar-Suite.ps1`).

### 6. Editor de anuncios (`/editor-anuncios`) y su conexión

El editor de videos 9:16 se instala en un PC con Windows (Node, Python y ffmpeg: lo hace `editor-anuncios\Instalar-Editor.cmd`; la página `/editor-anuncios` baja el instalador). Lee `editor-anuncios\CLAUDE.md` antes de tocarlo.
- La conexión con el dash: `scripts\Setup-ClaveEditor.ps1` crea la llave `CLAVE_EDITOR` en Cloudflare y la deja en el `.env` del editor. Con ella el editor pide imágenes, clips, texto y voz **por el dash** (usa la llave de APIMart del punto 1; el editor no guarda llaves de IA).
- La dirección del dash viene de ejemplo como `https://tu_proyecto.pages.dev` (con «_» a propósito: Cloudflare no deja crear un proyecto con ese nombre, así que hasta que la cambies el editor no le manda su llave a nadie): cámbiala por la de SU dash en todos los archivos que la traen (búscala en el repositorio; entre otros `editor-anuncios\Editor-Comun.ps1`, `editor-anuncios\scripts\_comun.py`, `apimart.py`, `fabrica.py`, `editor-anuncios\env-ejemplo.txt`, `scripts\Setup-ClaveEditor.ps1` y `scripts\Setup-ClaveEditorVps.ps1`), siempre como `https://<proyecto>.pages.dev`. Si no coincide, el editor se niega a mandar la llave a otra dirección.
- Costo: centavos por anuncio (guion y voz); los clips de IA cuestan más y la receta de cada oferta pone un tope por anuncio.

### 7. El servidor: corredor de anuncios y b-roll (opcional, al final)

Para que los anuncios se hagan solos (sin un PC prendido) y para traer b-roll de TikTok cada semana hace falta **un servidor propio** (un VPS Linux de unos 8 GB de RAM, del orden de diez a veinte dólares al mes) y una cuenta de Google con Drive. Guía completa: `editor-anuncios\vps\LEEME.md`.
- **Antes de instalar nada, prepara el servidor** (guíala paso a paso; el servidor va a guardar llaves):
  1. Entrada por SSH **solo con llave**: la llave se genera en su PC con `ssh-keygen -t ed25519` (la privada nunca sale de ahí) y la pública se pone en el servidor. Las líneas van en un archivo propio, **no** en `/etc/ssh/sshd_config`: en Ubuntu ese archivo lee primero `/etc/ssh/sshd_config.d/*.conf`, ahí gana el primer valor, y los servidores nuevos suelen traer `50-cloud-init.conf` con `PasswordAuthentication yes`. Crea `/etc/ssh/sshd_config.d/00-endurecer.conf` con `PasswordAuthentication no`, `KbdInteractiveAuthentication no` y `PermitRootLogin prohibit-password`; luego `sshd -t && systemctl restart ssh`.
  2. Compruébalo: `sshd -T | grep -Ei '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin)'` tiene que decir `no`, `no` y `prohibit-password` (o `without-password`, que es lo mismo). Desde su PC, en una ventana nueva (sin cerrar la que está conectada), `ssh root@<IP>` tiene que entrar con la llave, y `ssh -o PubkeyAuthentication=no root@<IP>` tiene que contestar `Permission denied (publickey)` **sin pedir contraseña**; si pide contraseña, algo quedó mal.
  3. Un cortafuegos que solo deje abierto el puerto de SSH (`ufw allow OpenSSH` y `ufw enable`) y actualizaciones de seguridad automáticas (`apt install unattended-upgrades`).
  4. La verificación en dos pasos en la cuenta del proveedor del servidor: desde ese panel se entra al servidor sin llave.
- La IP del servidor de ejemplo es `203.0.113.10` (una dirección de documentación que no existe): cámbiala por la de SU servidor en los scripts que la usan (búscala en el repositorio: `scripts\Setup-*Vps.ps1`, `scripts\Corredor-Vps.ps1`, `scripts\Setup-DriveBroll.ps1`, `editor-anuncios\vps\*.sh`).
- Las carpetas de Drive de ejemplo (`ID_DE_TU_CARPETA_...`) van en `editor-anuncios\vps\corredor.json`, `editor-puerta.json` y `ofertas.json`: la carpeta «OFERTAS LATAM» de SU Drive y una por oferta.
- Llaves del servidor: `scripts\Setup-ClaveEditorVps.ps1` (la conexión con el dash; el servidor no necesita la llave de APIMart: pide todo por el dash) y `scripts\Setup-DriveBroll.ps1` (permiso de Drive). Las pide en una ventana aparte; nunca en el chat.
- **Una cuenta de Google aparte para el servidor**, no la del negocio: el permiso que guarda el servidor alcanza TODO el Drive y todas las planillas de esa cuenta, y ahí están los libros de ventas con los datos de los clientes. Crea una cuenta solo para el servidor, compártele únicamente la carpeta «OFERTAS LATAM» (y la planilla del radar si el radar corre ahí) y autoriza `Setup-DriveBroll.ps1` con esa cuenta.
- El corredor nace con el reloj **apagado** y topes de gasto por anuncio y por día (`corredor.json`); una receta sin tope propio no gasta (`receta_sin_tope = no-gasta`). Enciéndelo solo cuando la persona lo pida, después de una prueba con un anuncio.

## Lo que NO trae esta plantilla

Viene de un sistema más grande. Algunas partes de las páginas llaman a funciones que aquí no están (costos, objetivos, números de WhatsApp, oficina, equipo): fallan en silencio y no rompen nada. Si la persona las pide, constrúyelas de cero.

## Trampas conocidas (no las repitas)

- **Tildes en PowerShell 5.1**: un `.ps1` sin BOM con tildes se lee mal. Lee y escribe archivos con `-Encoding UTF8`; si un `.ps1` tiene tildes, que conserve su BOM (no lo reescribas entero con otro editor).
- **Pestañas por nombre, nunca por posición**: el orden de las pestañas cambia.
- **El gasto de una cuenta en pesos colombianos (o soles) viene en esa moneda**, no en dólares: el armado lo convierte con la tasa del día. Revisa la moneda de cada cuenta.
- **El gasto de un día o de una oferta** solo suma anuncios de esa oferta y fechas de ese rango; nunca el total de vida de un anuncio en un día.
- **Nombres que vienen de afuera** (campañas, anuncios, ofertas, pestañas) los escribe cualquiera con acceso a la cuenta o a la hoja: en la página siempre pasan por `escHtml()` (o `textContent`), nunca crudos a `innerHTML`.
- **jsdom no es una caja aislada**: `reglas-auto.cjs` abre la página con `runScripts: 'outside-only'`; no lo cambies.
- **Nunca `npm install` fuera de una carpeta con su propio `package.json`** (jsdom se instala en `scripts\jsdom-pin` con `npm ci`).
- **Una regla que pausa sin pedir días con gasto** pausa campañas recién creadas: sugiere siempre «Días con gasto ≥ 2» o un gasto mínimo.
