# B-roll de TikTok en el servidor (VPS)

Desde el 2026-10-20 el b-roll de TikTok se trae **solo**, desde el servidor del dueño (tu proveedor de VPS, `203.0.113.10`), sin el PC de nadie. Esta carpeta es el código fuente de ese servicio: el servidor corre una copia, que se sube desde git con `desplegar-vps.sh` (nunca se edita allá).

Desde el 2026-10-21 el mismo servidor hace además la **Fábrica de ofertas**: cuando una oferta pasa a la Fábrica del dash, estudia los anuncios originales más antiguos de ese anunciante y busca b-rolls basados en ellos (ver «Fábrica de ofertas» más abajo).

Desde el 2026-10-23 el mismo servidor puede además **hacer los anuncios solo** (guion, voz, montaje, render y entrega a Drive): es el **corredor de anuncios** (`/opt/anuncios`, `anuncios-corredor`), instalado pero con el reloj **apagado** hasta que el dueño lo encienda. Se pausa con un comando; todo está en la sección «Corredor de anuncios», al final.

## Qué hace, en simple

Una vez por semana (domingo 5:15 a. m. hora de Bogotá), y cuando alguien lo lance a mano, para cada oferta de `ofertas.json`:

1. Abre la carpeta de Drive de la oferta, halla o crea su subcarpeta **B-rolls** y lee su `fuentes.csv` (la lista de lo que ya se bajó o se descartó: lo que baja el PC o lo que el equipo borra **no vuelve**).
2. Baja videos **nuevos** de TikTok, sin marca de agua (`broll_tiktok.py`): de las cuentas de TikTok de la lista y de la **búsqueda por palabra gratis** (la lista pública que TikTok le muestra a cualquier visitante en `tiktok.com/discover/<palabra>`; ver «Búsqueda por palabra, gratis» más abajo).
3. Revisa cada video con el **detector de texto quemado** (`texto_quemado.py`): cuántos segundos no traen subtítulos ni títulos de su creador. Los que no tienen ni 5 segundos limpios se **descartan** (quedan anotados en `fuentes.csv` y guardados 30 días en el servidor).
4. Sube los buenos a **Drive › carpeta de la oferta › B-rolls** (subida reanudable, comprobando tamaño y MD5) y deja `fuentes.csv` al día. Cada video queda anotado con su enlace, su autor y su calidad (`limpio` / `medio`) y los tramos sin texto (`tramos_limpios`, por ejemplo `0-16;44-64`).
5. Borra del servidor los videos ya subidos con más de 14 días (el registro conserva el id).

No usa tokens de Claude ni cuesta plata: el servidor ya es del dueño y la búsqueda por palabra es gratis.

En el PC del editor, lo que el servidor dejó en Drive se trae al editor con `python scripts/broll_tiktok.py recoger <producto>` (copia lo que falta a `public/productos/<producto>/broll-tiktok/`; después `indexar_broll.py`).

## Lo que necesita para funcionar

| Qué | Quién | Estado |
|---|---|---|
| Permiso de Google Drive (completo) para el servidor | el dueño, una vez: `scripts\Setup-DriveBroll.ps1` | ver el estado del servidor con `drive_api.py probar` |
| Llave de ScrapeCreators (proveedor de pago) | **ya no hace falta**: `scripts\Setup-LlaveScrapeCreators.ps1` solo si algún día se prefiere ese proveedor | la búsqueda por palabra es gratis (lista pública de TikTok) |
| Llave del dash para LEER la cola de la Fábrica (`secretos/dash.env`) | el dueño, una vez: `scripts\Setup-ClaveEditorVps.ps1` (la copia del `.env` del editor, la prueba leyendo la cola y enciende el reloj) | sin ella la Fábrica no se vigila (lo dice en el registro y sale con 0) |

Ninguna llave pasa por el chat ni se guarda en el repo. Viven en el servidor: `secretos/google.json`, `secretos/dash.env` y `.env` (dueño `root`, grupo `broll`, permisos 640).

Los tres `Setup-*.ps1` hablan con el servidor por SSH a través de `scripts\_ssh-seguro.ps1`: cada `ssh` corre en su propio proceso (sin la consola del Terminal como entrada), con tiempo máximo (`-SegundosSsh`, 40 s) y un reintento, y antes de cada uno dicen «Conectando con el servidor…». Si un script dice «El servidor no contestó…» o «ssh no pudo conectar…», **no guardó nada**: abre una pestaña NUEVA del Terminal y corre el mismo comando otra vez. (Motivo, 2026-10-20: `ssh.exe` se quedó colgado en la pestaña del dueño aunque el servidor ya había contestado; en una pestaña nueva la misma llamada tardaba 1 s. Con tuberías no se reproduce, solo con la consola real, por eso el arreglo evita la consola y además pone tiempo máximo.)

## Dónde está cada cosa (en el servidor, `/opt/broll-tiktok`)

```
scripts/        el código (de root, el servicio solo lo lee): broll_tiktok.py, broll_vps.py, drive_api.py, texto_quemado.py, y de la Fábrica fabrica_broll.py, referencia_meta.py,
                transcribir_vps.py, consultas_desde_texto.py, original_reutilizable.py; y las autopruebas
venv/           Python con yt-dlp, curl_cffi, requests, el detector de texto y faster-whisper (de root)
modelos/        faster-whisper-small/ (el modelo de Whisper, 4 archivos con SHA-256 fijo; de root: lo baja instalar-vps.sh, el programa no baja nada)
ofertas.json    qué ofertas y qué buscar, y el bloque «fabrica» (viene de este repo: vps/ofertas.json)
secretos/       google.json (permiso de Drive)   dash.env (llave del dash, solo lectura de la cola)   .env  (opcional: llave de ScrapeCreators, de pago)
public/productos/<oferta>/broll-tiktok/   los videos bajados (tt_<id>.mp4), fuentes.csv y _descartados/
public/productos/<oferta>/referencia/     lo que estudió la Fábrica (REFn_referencia.mp4, _copy.txt, _transcripcion.*, _analisis.json, consultas.json, reutilizable.json)
logs/           broll_AAAAMMDD.log y fabrica_AAAAMMDD.log (45 días)
estado/         ultima_corrida.json, historial.jsonl, rotacion.json; de la Fábrica: fabrica/<proyecto>.json (cada paso) y ofertas_fabrica.json (las ofertas que suma el b-roll semanal)
correr_broll.sh y correr_fabrica.sh   los que lanzan los timers (candado compartido, registro, tapan tokens de Google si se colaran)
```
El usuario `broll` no tiene sesión, ni sudo, ni acceso al radar (`/opt/radar-de-ofertas`) ni al runner de GitHub (`/opt/actions-runner`).

## Comandos (en el servidor, como root; `sudo -u broll` para correr)

```bash
sudo -u broll /opt/broll-tiktok/correr_broll.sh --simular                 # mira y dice qué bajaría; no baja, no sube, no borra
sudo -u broll /opt/broll-tiktok/correr_broll.sh --solo dinamicas --max-por-oferta 5   # una corrida a mano
sudo -u broll /opt/broll-tiktok/correr_broll.sh --sin-drive --solo dinamicas          # baja y revisa texto, sin tocar Drive
runuser -u broll -- /opt/broll-tiktok/venv/bin/python /opt/broll-tiktok/scripts/broll_vps.py estado   # qué pasó la última vez
runuser -u broll -- /opt/broll-tiktok/venv/bin/python /opt/broll-tiktok/scripts/drive_api.py probar   # ¿el permiso de Drive sirve? ¿se ven las carpetas?
tail -f /opt/broll-tiktok/logs/broll_$(date +%Y%m%d).log                    # el registro del día
systemctl list-timers broll-tiktok.timer                                     # cuándo corre la próxima
systemctl start broll-tiktok.service                                         # lanzarlo como lo lanza el timer
```

Códigos de salida de una corrida: **0** bien · **2** bien, pero alguna fuente falló (por ejemplo una cuenta que TikTok mandó a esperar, o una palabra sin lista pública) · **3** TikTok o la red bloquearon toda la corrida (systemd reintenta 3 horas después, hasta 3 veces al día) · **4** Drive sin permiso (hay que volver a autorizar con `Setup-DriveBroll.ps1`; no se reintenta, y **no baja nada** de TikTok) · **1** configuración mala o error inesperado.

## Sumar una oferta

1. Editar `vps/ofertas.json` en este repo: una entrada nueva con `producto` (nombre corto en minúsculas, el mismo del editor), `drive` (el enlace de la carpeta de Drive de la oferta, el del campo «drive» de la tarjeta del tablero) y al menos una fuente: `cuentas` (usuarios de TikTok; sin llave), o `consultas` / `hashtags` (búsqueda por palabra, gratis: solo contesta para frases de búsqueda comunes; prueba varias con `traer --simular` y deja las que dan videos). `filtro_desc` = palabras que debe traer la descripción del video (una cuenta publica de todo: peinados, departamentos…; con el filtro solo pasan los que hablan de la oferta). `excluir_autores` = nombres de anunciantes cuya propia cuenta de TikTok NO se usa como b-roll (su material promocional es suyo): se descartan los videos de búsqueda cuyo autor sea esa cuenta (el mismo nombre sin separadores ni tildes, o que lo contenga); lo que se pone a mano (`--enlace`, una cuenta de `cuentas`) no se descarta. Las ofertas de la Fábrica lo traen puesto con el anunciante original, y el informe dice cuántos se dejaron de lado.
2. `bash editor-anuncios/vps/desplegar-vps.sh` desde la raíz del repo (sube lo que cambió comprobando SHA-256 y corre el instalador).
3. `drive_api.py probar` en el servidor (o `Setup-DriveBroll.ps1 -Probar`) para ver que la cuenta autorizada ve y puede escribir en esa carpeta.

Las consultas describen la **toma**, no el producto, y se escriben como las escribiría una persona en TikTok: «manos tejiendo crochet paso a paso», «aplicar kinesiotape rodilla».

## Fábrica de ofertas (desde el 2026-10-21)

Pedido del dueño: «cuando una oferta pase a la Fábrica de ofertas, que empiece a buscar b-rolls, pero basado en los anuncios a modelar de la oferta original: primero se eligen los anuncios de la oferta original, los más antiguos, se transcriben y de ahí ya se sabe cómo buscar b-rolls para hacer nuestros propios anuncios. Si se puede usar el original se usa y solo se cambia el inicio; si no, lo creamos todo.» Todo en el servidor, la información en Drive y sin tokens de Claude.

**Qué hace, en simple.** Cada hora (`fabrica-broll.timer`, a los :10, hora de Bogotá) `fabrica_broll.py vigilar` lee la cola de pedidos del dash (`/api/fabrica-editor`, **solo lectura**: nunca toma ni cambia un pedido). Por cada oferta nueva:

1. **Drive.** Halla o crea `MI NEGOCIO › OFERTAS LATAM › <NOMBRE DE LA OFERTA>` (mayúsculas, sin acentos, como `DINAMICAS`; si ya hay una carpeta con ese nombre, se usa esa) con las subcarpetas **B-rolls** y **REFERENCIA**.
2. **Anuncios originales.** Abre el enlace del anuncio original en la Biblioteca de Meta —el de **un anuncio** o el de **la página de un anunciante** («Ver todos los anuncios», con los filtros que quien lo pegó haya puesto: país, solo activos, orden por impresiones, hasta una fecha)—, ve los anuncios del **mismo anunciante** (con el enlace de un anuncio se lee además su página en **todos los países**: con el país de un solo mercado, un anunciante de 52 anuncios salió con 1) y baja los 3 **más antiguos con video** (primero los que siguen activos; un solo anuncio por creativo: dos con el mismo texto cuentan uno). Video, copy y datos van a `REFERENCIA/` (`REF1_…`, `REF2_…`, `REF3_…`).
3. **Escucha y mira.** Whisper (`small`, en el servidor) transcribe cada video y dice en qué idioma habla; el detector de texto quemado mide cuánto del video trae texto escrito encima.
4. **Decide si sirve el original** (`original_reutilizable.py`): sirve si el audio está en **español** y al menos el **70 % del video no trae texto encima**; avisa (sin descartar) si no es vertical, si dura demasiado o si la voz dice un teléfono. Aunque sirva, **solo se dice «usar el original» si la receta de la oferta lo permite** (`material = referencia`): usar cortes del anuncio de otra persona en un anuncio pago es decisión del dueño. Si sirve y la receta lo permite, el montaje usa el original y solo se rehace el inicio (`gancho_ia.py`, los segundos de la receta); si no, se crea todo. Queda en `reutilizable.json`.
5. **Busca b-rolls.** `consultas_desde_texto.py` saca las frases de búsqueda por **reglas fijas** (sin modelo de lenguaje: lo que pesa son el nombre, el tema, la promesa y los títulos de la oferta; después el copy y lo que dicen los anuncios; se quitan muletillas de venta, verbos de relleno, precios y la marca del anunciante; salen palabras sueltas mezcladas con frases de 2 y 3 palabras, y «… paso a paso» / «… para principiantes» solo si la oferta enseña algo). Se registra la oferta en `estado/ofertas_fabrica.json` y `broll_vps.py` baja de TikTok (lista pública gratis) los videos limpios a **B-rolls**. La oferta queda registrada: la corrida semanal sigue trayendo más **mientras tenga pedidos abiertos** en la Fábrica (si se cierran todos, se apaga sola) y entran las 6 más recientes.
6. **Informe.** `INFORME.md` en la carpeta de la oferta, en palabras del negocio: qué anuncios se estudiaron, qué idioma, cuánto texto traen, si sirve el original, qué se buscó y cuántos videos salieron de cada frase, y lo que no se pudo.

Cada paso queda anotado en `estado/fabrica/<proyecto>.json` y se puede repetir sin rehacer lo hecho. Si un paso falla (Meta limita la conexión, falta Whisper, Drive...) queda dicho en el informe, los demás siguen y se reintenta a las 2 horas (hasta 3 vueltas). Una corrida a medias (`--sin-brolls`, `--simular`, sin Drive) no cierra la oferta: la siguiente vuelta normal la termina.

**Qué hay en Drive al terminar** (`REFERENCIA/`): por cada anuncio original `REFn_referencia.mp4`, `REFn_copy.txt`, `REFn_referencia.json`, `REFn_transcripcion.txt` (con minutos) y `.json`, `REFn_analisis.json` (idioma, palabras, % de texto encima, tramos limpios); y `consultas.json` y `reutilizable.json`. En la carpeta de la oferta, `INFORME.md`. El editor del PC los ve en `G:\Mi unidad\MI NEGOCIO\OFERTAS LATAM\<NOMBRE>\`.

**Si le cambian el enlace del anuncio original** (en la Fábrica se puede pegar o cambiar cuando se quiera, por ejemplo para modelar a **otro anunciante de la misma oferta**), la vuelta siguiente lo nota —mira la orden de las ofertas ya hechas, una lectura por oferta— y la oferta se **estudia otra vez**: si el anunciante es el mismo (por ejemplo, el enlace de un anuncio cambiado por el de su página) sigue en la misma carpeta `REFERENCIA` (los archivos iguales no se vuelven a subir); si es **otro**, su estudio va a una carpeta nueva (`REFERENCIA 2`, `REFERENCIA 3`…) y la anterior no se toca. En el servidor se borran los archivos locales del estudio anterior (para no subirlos por error a la carpeta nueva); en Drive no se borra nada, y el `INFORME.md` deja constancia de los estudios anteriores y de su carpeta. Con `--simular` solo dice que lo volvería a estudiar y no toca ni el estado.

**Comandos** (en el servidor, como root; `sudo -u broll` para correr):

```bash
sudo -u broll /opt/broll-tiktok/correr_fabrica.sh vigilar --solo-cola     # solo lee la cola y dice qué ofertas faltan; no baja ni escribe nada (así prueba la llave Setup-ClaveEditorVps.ps1)
sudo -u broll /opt/broll-tiktok/correr_fabrica.sh vigilar --simular       # además mira Meta y estudia los anuncios, pero NO crea carpetas ni sube nada a Drive ni registra la oferta
sudo -u broll /opt/broll-tiktok/correr_fabrica.sh manual --enlace "https://www.facebook.com/ads/library/?id=…" --nombre "Mi oferta" --slug mioferta --tema "…" [--material referencia] [--rehacer] [--sin-drive] [--sin-brolls]
#   --enlace también acepta el de la PÁGINA de un anunciante (…/ads/library/?…&search_type=page&view_all_page_id=…), entre comillas porque lleva & y [ ]
#   --rehacer vuelve a estudiar la oferta aunque el enlace sea el mismo (el mismo anunciante sigue en la misma carpeta REFERENCIA; no se duplica nada)
runuser -u broll -- /opt/broll-tiktok/venv/bin/python /opt/broll-tiktok/scripts/fabrica_broll.py estado    # cada oferta, su estado y sus pasos
tail -f /opt/broll-tiktok/logs/fabrica_$(date +%Y%m%d).log                # el registro del día
systemctl list-timers fabrica-broll.timer                                  # cuándo corre la próxima
```

`manual` corre la misma tubería con los datos que se le dan (sirve para una oferta que no pasó por la Fábrica, o para probar). Códigos de salida: **0** bien o nada que hacer · **2** bien pero algo quedó pendiente · **3** el dash, Meta o TikTok no contestaron (el timer reintenta en la hora siguiente; el paso fallido, a las 2 horas) · **4** Drive sin permiso · **1** configuración mala (falta o está mal la llave del dash o `fabrica.carpeta_ofertas`). `SuccessExitStatus=2 3`: solo 1 y 4 dejan la unidad en falla visible (`systemctl --failed`).

**Para encenderla (una vez, lo hace el dueño):** `& .\scripts\Setup-ClaveEditorVps.ps1` en su PC. Copia `DASH_URL` y `DASH_CLAVE_EDITOR` del `.env` del editor (la llave de `Setup-ClaveEditor.ps1`) a `secretos/dash.env`, le pide al servidor que lea la cola (prueba de verdad) y solo entonces enciende `fabrica-broll.timer`. Esa llave en el dash puede leer los pedidos abiertos y avisar cómo va uno; este programa **solo lee**. No sirve para leer ventas, gasto, cuentas, sueldos ni clientes.

**Seguridad.**
- Lo que viene del dash (nombre, tema, promesa, notas) y de Meta/TikTok (copy, transcripciones, descripciones) es **texto de terceros**: solo se cuenta, se guarda o se usa para buscar palabras; nunca se ejecuta ni se obedece. Los nombres de carpeta y de producto se limpian; un `slug` raro hace que la oferta se salte con aviso.
- Escrituras en Drive: en `OFERTAS LATAM` solo se **crea la carpeta de la oferta** (la cerca se cierra enseguida y la autoprueba comprueba que no se toca nada más ahí); dentro de la oferta, `B-rolls`, `REFERENCIA` e `INFORME.md`. A `REFERENCIA` solo suben archivos con nombre de la lista (`REFn_…`, `consultas.json`, `reutilizable.json`; n de 1 a 6): cualquier otro archivo de esa carpeta local no sale del servidor. No existe función de borrar.
- Meta: una sola página de la Biblioteca por oferta (con su reto), como el radar; los videos solo se bajan de dominios `fbcdn.net` por https, sin seguir redirecciones a otros dominios, con tope de tamaño y comprobados con ffprobe.
- La llave del dash no se imprime: ni en el registro (el envoltorio además tapa `DASH_CLAVE_EDITOR=`), ni en mensajes de error; el cliente del dash no sigue redirecciones.

**Límites que hay que tener presentes (dichos con honestidad).**
- La página de Meta trae **hasta ~30 anuncios** del anunciante (con el enlace de la página del anunciante, en el orden y con los filtros del enlace; por omisión: activos, todos los países, los de más impresiones primero). Si tiene más, «los más antiguos» son los más antiguos **de esos ~30** (el informe dice «hay más, que no se leyeron»). El radar sí pagina; si hace falta, v2 puede usar su fecha del anuncio más antiguo.
- Las frases salen de reglas, no de un modelo: aciertan con el tema y las palabras repetidas, pero pueden ser flojas (una palabra genérica) o no existir en TikTok (solo contesta para frases de búsqueda comunes). El informe dice cuántos videos dio cada una. Si más adelante se quiere un modelo barato de APIMart (≈ US$ 0,002 por anuncio) para escribir mejores frases, es un cambio aparte.
- «Español» lo decide Whisper con la confianza que da en el audio (se pide 60 %); con música fuerte o poca voz puede equivocarse. El texto escrito encima lo mide el detector de siempre: no ve rostros, niños ni logos sin letras.
- Whisper en 2 núcleos tarda ~1 a 2 veces la duración del audio (más si el servidor está ocupado); una oferta nueva tarda en total unos 10 a 25 minutos.
- Cada vuelta le pide la cola al dash, y eso gasta **3 listados de KV** de los 1.000 que da Cloudflare al día para TODA la suite: cada hora son 72 al día (7 %). No la hagas más seguido sin mirar antes `Ver-UsoKV.ps1`.
- Los anuncios originales son de terceros: se guardan para estudiarlos y modelar; no se usan en anuncios pagos sin decidirlo el dueño (la receta).

## Actualizar el código

Cambiar en el repo, correr `python scripts/autoprueba_broll.py`, `autoprueba_drive.py`, `autoprueba_broll_vps.py`, `autoprueba_consultas.py` y `autoprueba_fabrica_broll.py` (el editor los corre juntos en `prueba_rapida.py`; `node scripts/probar-setup-clave-editor-vps.cjs` prueba el script de la llave del dash), hacer commit y `bash editor-anuncios/vps/desplegar-vps.sh`. El script sube **desde git** (no desde el árbol de trabajo: en Windows está en CRLF y el servidor necesita LF), compara el SHA-256 de cada archivo y solo entonces los pone en su sitio. Después corre las autopruebas en el servidor:
```bash
cd /opt/broll-tiktok && for t in autoprueba_drive autoprueba_broll autoprueba_broll_vps autoprueba_referencia autoprueba_consultas autoprueba_fabrica_broll; do runuser -u broll -- env HOME=/opt/broll-tiktok/estado /opt/broll-tiktok/venv/bin/python scripts/$t.py | tail -1; done
```
Si TikTok cambia sus páginas y deja de bajar: subir `yt-dlp` en `requirements.txt` y en `vps/requirements-vps.txt` (la autoprueba comprueba que coincidan), probar con `autoprueba_broll.py --vivo <enlace de un video> --solo-vivo` y desplegar.

## Qué puede y qué no puede hacer el permiso de Drive (seguridad)

El permiso es el de **Drive completo**: hace falta porque los videos van a carpetas que ya existen (el permiso más chico, `drive.file`, solo ve lo que crea la propia aplicación). Google no deja limitarlo a una carpeta, así que la limitación está en el código y en el servidor:

- `drive_api.py` **no tiene ninguna función de borrar, mover ni compartir** (la autoprueba lo comprueba) y toda escritura pasa por una «cerca»: solo se escribe en la carpeta de la oferta y en su subcarpeta B-rolls. Lo único que se escribe: videos `tt_<id>.mp4`, `fuentes.csv` y `LEEME.txt`.
- Lo que se sube es solo lo que baja este programa: el nombre de archivo debe ser `tt_<número>.mp4` (un `fuentes.csv` de Drive con rutas raras no puede sacar archivos del servidor; probado).
- El servicio corre con un usuario sin privilegios, aislado por systemd (`systemd-analyze security`: 1,7 «OK»): solo escribe en `public/`, `logs/` y `estado/`; el código, el Python y las llaves los ve de solo lectura.
- **Lo que queda abierto:** el servicio baja y abre archivos de terceros (videos de TikTok) con ffmpeg y yt-dlp, y la llave de Drive es legible por ese mismo usuario. Un fallo de ffmpeg o yt-dlp que alguien lograra explotar con un video hecho a propósito podría leer la llave. Es poco probable, pero es el riesgo real; si se quiere cerrarlo, se parte en dos servicios con usuarios distintos (uno que baja, otro que sube).
- El permiso se quita en https://myaccount.google.com/permissions (y se borra `secretos/google.json`). Los que pueden proponer cambios a esta carpeta son los roles con zona `editor-anuncios/**`; el dueño revisa cada propuesta y lo que se sube al servidor sale de `main`, por eso conviene que quien revise una propuesta que toque `drive_api.py`, `broll_vps.py` o `vps/` sepa que corre con ese permiso.
- Derechos: son videos de sus creadores. `fuentes.csv` guarda autor y enlace de cada uno. No usarlos en anuncios pagos sin permiso o licencia.

## El detector de texto quemado: qué mide y qué no

Saca un cuadro cada pocos segundos (30 como máximo por video), el detector de texto de RapidOCR marca las líneas de letras y se calculan los tramos sin texto. Se probó primero con tesseract (un OCR que lee letras): encontraba solo el 19 % de los cuadros con texto porque los subtítulos de TikTok usan letras de adorno. El detector de cajas encuentra el 89 % y el 87 % de lo que marca es texto de verdad.

Medido con 12 videos de Dinámicas que Claude revisó a mano (el 20-oct-2026, en el servidor): descarta los **5 de 5** que Claude marcó «malos», descarta **2 «medios»** que tienen texto del creador a lo largo de todo el video (7447 y 7650) y **no descarta ninguno** de los que Claude marcó «buenos». Tarda ~12 s por video (el de 94 s, 21 s) y usa ~330 MB de memoria.

Límites que hay que tener presentes: la muestra es chica (12 videos de una sola oferta, y los umbrales se ajustaron con esos mismos videos); no distingue un subtítulo de un cartel que sale en la escena (los dos cuentan como texto); no ve rostros, niños ni logos sin letras (eso lo sigue juzgando una persona o Claude mirando la hoja de contacto). Se ajusta en `texto_quemado.py` (`PARAMETROS`) y en `ofertas.json` → `texto_quemado` (`modo`: `descartar` o `marcar` = solo anotar; `min_limpio_seg`).

## Búsqueda por palabra, gratis (desde el 2026-10-21)

El dueño no quiso pagar la búsqueda por palabra (ScrapeCreators) y pidió buscar con código, como se busca en la Biblioteca de anuncios de Meta. Se midió, desde este servidor y sin sesión ni cookies de nadie, lo que TikTok le muestra a cualquier visitante:

- Cada página `tiktok.com/discover/<palabra>` (el `robots.txt` de TikTok la permite a cualquier visitante y los buscadores la indexan) carga su lista con dos llamadas **sin firma, sin captcha y sin cuenta**: `api/seo/kap/video_list` (16 videos por página, hasta unos 100 por palabra: id, autor, vistas, duración, medidas, descripción y el título, la descripción y las palabras clave que TikTok le pone) y `api/seo/kap/related_keywords` (60 palabras relacionadas, todas con su propia lista).
- `broll_tiktok.py` la usa por defecto (`--proveedor descubrir`, clase `Descubrir`): pide la página de visitante una vez y luego la lista con 2 a 3 s entre peticiones, se identifica con su propio User-Agent y, si TikTok contesta un reto, vacío o algo raro, lo explica y **para** (no esquiva nada). Una corrida de Dinámicas hace unas 13 peticiones. `--relacionadas N` (en `ofertas.json`: `relacionadas`) sigue las palabras relacionadas que hablan de lo mismo (comparten raíces con la consulta). El título que TikTok le pone a cada video queda en `fuentes.csv` (columna `tema`) y también cuenta para `filtro_desc`.
- **Límite:** solo contesta para palabras que TikTok tiene en su lista (frases de búsqueda comunes, en cualquier idioma): «dinámicas para mujeres», «globos de colores» o «bordado a mano para principiantes» sí; «mujeres riendo en grupo», «manos haciendo manualidades» o una frase inventada no (TikTok contesta `status_code 4`). Por eso Dinámicas pasó a consultas que sí dan videos (de las 3 que tenía, 2 no tenían lista) y `relacionadas` encuentra más. Para saber si una frase sirve: `python scripts/broll_tiktok.py traer <producto> --consulta "..." --simular`.
- **Riesgos:** TikTok puede cambiar o cerrar estos endpoints (la corrida lo dice y sigue con las cuentas); sus términos de uso prohíben el acceso automatizado, por eso son pocas peticiones, con pausa y sin disfrazarse. Ojo: el `robots.txt` de TikTok **sí** pide que no se use `/embed/@usuario` (lo que usan las cuentas de la lista) y **no** dice nada contra `/discover` ni `/api/seo/kap`: si hay que reducir riesgo, conviene apoyarse más en la lista pública y menos en las cuentas.

## Lo que se probó y cómo deshacerlo

Las autopruebas (`scripts/autoprueba_*.py`) corren sin red, en el PC y en el servidor, con Drive y TikTok de mentira. Antes de encender un reloj, haz una corrida real con una oferta de prueba y mira en Drive lo que dejó.

- **Cómo deshacer la Fábrica:** `systemctl disable --now fabrica-broll.timer` y borrar `secretos/dash.env` (el servidor deja de leer el dash). Lo que ya se creó en Drive queda; las ofertas registradas se cierran quitando `estado/ofertas_fabrica.json`.
- **Cómo deshacer el b-roll:** `systemctl disable --now broll-tiktok.timer` y borrar `secretos/google.json` + quitar el permiso en https://myaccount.google.com/permissions (el servidor deja de poder tocar Drive). Lo que ya subió queda en Drive.

## Corredor de anuncios (desde el 2026-10-23)

Pedido del dueño: «usemos todo desde el VPS» (el anuncio completo, sin el PC de nadie y sin tokens de Claude). El mismo servidor, ahora con un servicio nuevo, **`anuncios-corredor`** (carpeta `/opt/anuncios`, usuario `broll`, unidad propia de systemd): cada hora, a los :40, mira la cola de la Fábrica y, si hay un pedido que le toca, lo hace entero: baja el material de Drive, escribe el guion, pide la voz, arma el plan, renderiza con Remotion, revisa (10 compuertas), sube el mp4 a `MI NEGOCIO › OFERTAS LATAM › <OFERTA> › ANUNCIOS` y marca «lista» en el dash con el enlace y el costo (en `/fabrica` se ve cada etapa con su avance). Código: `scripts/corredor_vps.py` (orquestador) y `scripts/espejo_drive.py` (Drive ⇄ carpeta local); los demás scripts son los del editor, **tal cual** (`fabrica.py`, `broll_tiktok.py recoger`, `emparejar_tomas.py`, `guion_auto.py`, `voz_auto.py`, `gancho_ia.py`, `montaje_auto.py`).

**Estado de partida: el reloj viene APAGADO** y el servidor no tiene llave hasta que corras `Setup-ClaveEditorVps.ps1`. Los topes duros por anuncio y por día están en `corredor.json`. Enciéndelo solo después de una prueba con un anuncio.

### Pausarlo, mirarlo, encenderlo (desde el PC; en el servidor es `/opt/anuncios/corredor-ctl <orden>`)

```powershell
& .\scripts\Corredor-Vps.ps1                      # estado: qué hace ahora, si está en pausa, cuándo es la próxima pasada, lo último que pasó y lo gastado hoy
& .\scripts\Corredor-Vps.ps1 pausar               # PAUSA con un solo comando: no empieza nada nuevo; la etapa en curso termina y se detiene ahí (se retoma al reanudar)
& .\scripts\Corredor-Vps.ps1 pausar --ya          # además corta lo que esté haciendo ahora mismo
& .\scripts\Corredor-Vps.ps1 reanudar             # quita la pausa (vuelve a poner el reloj solo si estaba encendido antes)
& .\scripts\Corredor-Vps.ps1 encender -Si         # enciende el reloj: decisión del dueño (a propósito pide -Si)
& .\scripts\Corredor-Vps.ps1 apagar               # apaga el reloj
& .\scripts\Corredor-Vps.ps1 correr --simular     # pasada de prueba: mira la cola y dice qué haría, sin tocar nada (también --solo-cola, --max-pedidos N, --tope-usd X, --sin-gancho,
                                                  #   --pedido PROYECTO ANUNCIO [--rehacer])
& .\scripts\Corredor-Vps.ps1 logs 80              # las últimas 80 líneas del registro de hoy
& .\scripts\Corredor-Vps.ps1 reintentar PROYECTO AD4   # deja volver a intentar un pedido que quedó en error
```

Más para quien trabaje en el servidor: `corredor-ctl saldo` (o `probar-apimart`: le pregunta al dash, con la llave del editor del servidor, cuánto queda del tope del día; no gasta ni imprime la llave) y `corredor-ctl ejecutar -- <comando>` (corre un comando como `broll` dentro de los MISMOS límites y aislamiento de la unidad, sin la llave: sirve para medir y probar).

### Qué pedidos toma (y cuáles deja a las personas)

Una pasada solo toma un pedido si se cumple TODO: está en estado «pedido» (nadie lo tocó) o ya era suyo y quedó a medias; es de tipo locución + videos (`tiktok`) o de avatar con IA (`ia`, solo si `corredor.json` → `tipos_permitidos` lo trae y si pasa la compuerta propia de «Anuncios de avatar con IA»); **no trae correcciones del equipo**; **una persona dio la orden de arrancar los anuncios de esa oferta** en `/fabrica` («Crear toda la oferta»: campo `arranque` del pedido, `corredor.json` → `exigir_arranque`; `--pedido` es una orden explícita y no la necesita); la oferta tiene sus ebooks listos, un video de referencia, su carpeta en Drive con al menos 6 b-rolls y un tope de gasto en la receta (`aprobacion.topeUsd`, el interruptor del dueño). Sin tope no gasta en IA, y nunca más que `corredor.json` → `topes` (US$ 0,30 por anuncio y US$ 1,00 por día). Si hay menos de 4,5 GB de memoria libre, la carga de 5 minutos pasa de 1,5 o quedan menos de 10 GB de disco, no empieza nada nuevo. Hace uno a la vez (candado `flock`).

**Ventana de silencio (01:30 a 03:00, hora de Bogotá; `corredor.json` → `ventana_silencio`):** ese rato queda libre para otras tareas del servidor (respaldos, revisiones de la madrugada). El corredor no hace nada dentro de la ventana, no empieza pedidos nuevos si la ventana empieza en menos de 45 minutos y, si llega a mitad de un pedido, termina la etapa en curso y se detiene antes de la siguiente (se retoma al terminar; no se pierde lo hecho). `--pedido` (una orden explícita de una persona) no la respeta. Quedan sin trabajar las pasadas de 01:40 y 02:40.

**Choque con el PC (Claudia):** el servidor marca el pedido «tomado» (nota «El servidor (VPS) tomó el pedido…») y desde ahí `fabrica.py traer` del PC **no lo toma** si el dash lo tiene «tomado» o «en-edicion» y ese PC no había creado su carpeta (`--forzar` lo permite a propósito: pausa el corredor antes). Queda una carrera de segundos si una persona y el servidor toman el mismo pedido a la vez (el dash no tiene reclamo atómico).

### Ebooks que ya vienen hechos (carpeta de Drive; desde el 2026-10-29)

Cuando los PDF de una oferta no hay que producirlos sino que se consiguen hechos, en `/fabrica`, paso 2 «Ebooks y bonos», se pega el enlace de la carpeta de Drive («¿Los PDF ya vienen hechos?»). El dash **solo guarda el enlace** (`material.entregablesDrive`, y marca ebooks y material como «lista»); no lee Drive. Lo lee el corredor, con su propio permiso:

1. La etapa **`entregables`** (justo después de `traer`) comprueba que esa carpeta sea la de la oferta o una subcarpeta directa suya (`Espejo.carpeta_de_entregables`: el servidor no lee otras carpetas por un enlace del dash; si no, espera y no toca nada).
2. Baja los `.pdf` y las imágenes (solo lectura, sin subcarpetas, máximo 8 PDF: un principal y siete bonos) a `drive/ENTREGABLES/<oferta>/`.
3. Elige el **principal**: el que diga «principal», «ebook» o «libro» en su nombre; si ninguno, el más pesado. Con `pdf_a_imagenes.py` saca sus `paginas/` (y una `portada.jpg` provisional) a `public/productos/<oferta>/` (lo mismo que el editor en el PC). Corre en su propio proceso, sin llaves.
   **El mockup:** si en la carpeta hay una imagen, es el mockup de la oferta y pasa a ser `portada.jpg` (JPG de hasta 2000 px, el fondo transparente en blanco; se convierte con Pillow en otro proceso `python -I`, sin llaves). Es la única imagen de la carpeta o la que diga «mockup», «portada», «cover» o «carátula» en su nombre; con varias imágenes sin nombre claro no se adivina y la portada sale de la primera página del PDF principal. Si la conversión falla, avisa y usa esa primera página.
4. Deja en `orden.json` los títulos de los ebooks (el nombre del archivo, sin guiones bajos) y, si la oferta no traía viñetas, los usa como «lo que recibe el cliente»: es lo único que el guion puede nombrar de la oferta, además de la promesa que se escribe en el mismo formulario.
5. La compuerta de la portada (`evaluar`) deja pasar si la orden trae `entregablesDrive`; la portada la pone esta etapa. Si `pdf_a_imagenes.py` falla, el pedido se detiene antes de reclamarlo y antes de gastar.

**Una vez en el servidor, como root** (el venv es de root; sin esto la etapa se detiene con «falta pymupdf»):
```bash
/opt/broll-tiktok/venv/bin/pip install --only-binary=:all: pymupdf==1.28.2
runuser -u broll -- /opt/broll-tiktok/venv/bin/python -c "import pymupdf; print(pymupdf.__doc__)"
```
y desplegar el código con `desplegar-corredor.sh` (`pdf_a_imagenes.py` ya está en `corredor-archivos.txt`).

**Lo que no hace:** los flujos de ZapData siguen pidiendo los PDF dentro del sitio (`/api/zaparchivo`), así que con esta opción no se crean solos (la página lo avisa). Los títulos salen del nombre del archivo (sin tildes si el archivo no las trae): la frase de «qué recibe el cliente» del formulario es la que manda en el guion.

### Qué cuesta y cuánto tarda (medido el 2026-10-23 en este servidor, dentro de la unidad aislada)

| | |
|---|---|
| Plata | guion + voz ≈ **US$ 0,01** por anuncio (prueba real del 22-oct: 0,0105; el guion lo escribe un modelo barato de APIMart y la voz la pide al dash, que usa Fish). El gancho nuevo con IA solo si la receta lo pide: ≈ US$ 0,02 (2 imágenes quietas) o ≈ US$ 0,09 (1 imagen + 1 clip). Claude: 0 tokens |
| Analizar el b-roll (una sola vez por video) | 20 videos / 573 s de video: **9,6 min** (CLIP + detector de texto + ffmpeg); en el PC del dueño ocupado, 39 min |
| Transcribir la voz y mezclar | 15 a 28 s |
| Plan y hojas de revisión | ≈ 1,5 min |
| **Render de 52 s de anuncio (1 proceso)** | **7,4** (443 s) min (en el PC del dueño, 21,6 min) |
| Control de calidad (10 compuertas) | ≈ 40 s |
| **Un anuncio completo** | **9,0** min de montaje; **el primero de una oferta ≈ 20 a 30 min** (analiza el b-roll y baja Drive), **los siguientes ≈ 13 a 18 min** |
| Memoria de la unidad | lo que no se puede soltar sin swap llega a ~3,7 GB; con los archivos que el sistema deja en memoria, hasta ~5 GB (aviso a 5 GB, tope duro 5,5 GB; el servidor tiene 7,9 GB sin swap) |
| Efecto en el resto del servidor | con el render a pleno la memoria disponible del servidor no bajó de 3,2 GB (7,0 GB en reposo); una tarea corta de prueba pasó de 0,032 s a 0,041 s en promedio (máximo 0,072 s); la carga del procesador llega a ~56 % de presión mientras dura el render (el corredor corre a la prioridad más baja: el radar, el runner de GitHub y Hermes pasan primero). Sin acotar la memoria de video de Remotion el mismo render llegaba a 5,9 GB y dejaba 1,6 GB libres: por eso se acota |

Los tiempos del anuncio completo (guion, voz, Drive, entrega) se confirman con la prueba real; no se prometen hasta entonces.

### Anuncios de avatar con IA (desde el 2026-10-23; el dueño: «los de avatar también hazlos»)

Un pedido de tipo `ia` es una persona —nuestro avatar— hablando a cámara en clips de 8 s con la voz y los labios generados por el modelo de video. El corredor lo hace con los scripts del editor, tal cual: `guion_auto.py --tipo ia --clips N` (guion en N bloques de 25 a 30 palabras; desde 2026.11.10-3 cada prompt pasa la revisión de `revisar_prompts.py` antes de pagar), `avatar_auto.py planear` (escenas), `avatar_auto.py generar` (un cuadro con `gpt-image-2` y un clip con `grok-imagine-1.5-video-ext` por bloque, revisados con un modelo de visión y con Whisper), `avatar_auto.py montar` (une los clips, mezcla música y renderiza con el mismo Remotion y las mismas 10 compuertas) y la entrega a `ANUNCIOS`. Nada de esto usa tokens de Claude. Se enciende con `corredor.json` → `tipos_permitidos: ["tiktok", "ia"]` (ya viene así en el repo; el reloj sigue apagado).

**Antes de reclamar el pedido** el corredor comprueba dos cosas. Si falla alguna **no gasta**, no lo toma y le deja al dash UNA nota en estado «error» con el motivo y las cifras, para que una persona decida:

1. **El avatar está listo y autorizado en Drive**: `MI NEGOCIO › OFERTAS LATAM › <OFERTA> › AVATAR` con de 1 a 3 fotos de frente y un `avatar.json` (nombre, `genero` mujer|hombre, `fotos`, y en inglés `aspecto`, `voz`, `acento` y `casa_base`, más `"autorizado": true`; plantilla en `public/productos/producto-ejemplo/avatares/avatar-ejemplo/`). El espejo la baja solo-lectura a `drive/OFERTAS LATAM/<OFERTA>/AVATAR` y `avatar_auto.py` la instala en `public/productos/<oferta>/avatares/<nombre>/`. **Hoy no hay ningún avatar autorizado en ningún lado: una persona tiene que ponerlo** (el servidor no puede inventarlo ni elegir la cara de nadie).
2. **El anuncio más corto que acepta la receta cabe en el tope** (el menor entre `aprobacion.topeUsd` de la oferta y el techo del servidor, US$ 0,30). Un clip con su cuadro cuesta ≈ US$ 0,09 y los textos ≈ US$ 0,02:

| Clips | Duración | Costo | ¿Cabe en US$ 0,30? |
|---|---|---|---|
| 3 | ≈ 22 s | ≈ US$ 0,29 | sí, **sin margen** para repetir un clip |
| 4 | ≈ 30 s | ≈ US$ 0,38 | no |
| 5 | ≈ 37 s | ≈ US$ 0,47 | no |
| 6 | ≈ 44 s (el punto medio de la receta de fábrica, 25 a 60 s) | ≈ US$ 0,56 | no |
| 8 | ≈ 59 s | ≈ US$ 0,74 | no |

Con la receta de fábrica (25 a 60 s) el mínimo son 4 clips (US$ 0,38) y **el corredor no lo corre**: la nota al dash dice las cifras y cuántos clips sí caben. El corredor **nunca sube el tope por su cuenta** (ni con `--pedido` ni con `--tope-usd`): lo decide el dueño, o bajando la duración mínima de la receta a 20 s o subiendo `topes.usd_por_anuncio` en `corredor.json`. (El corredor deja 2 s de aire sobre el mínimo de la receta, porque un clip dura entre ≈ 6,5 y 8 s de voz y la compuerta de duración solo tolera 0,5 s: con un mínimo de 22 s ya pide 4 clips, y una receta tan angosta que ningún número de clips la cumple —15 a 20 s, por ejemplo— tampoco se corre: la nota al dash pide ensancharla.) Si el tope del pedido alcanza pero lo que queda del día (US$ 1,00) no, el pedido espera al día siguiente y no molesta a nadie. Desde el 2026-11-10 (orden del dueño) un clip que no dice su texto **no se repite solo** (cada repetición se paga entera): se avisa para que una persona lo oiga, y solo `--repetir-clip` repite uno por anuncio. Si APIMart no entrega un cuadro o un clip, no se cobra: el pedido para y se retoma sin pagar dos veces.

**Topes (desde el 2026-11-10, orden del dueño) y APIMart por el dash (desde la versión 2026.11.10-2):** el servidor **ya no tiene la llave de APIMart**. Las imágenes, los clips y el texto (guion, gancho, escenas y revisión del avatar) los pide `apimart.py` / `apimart_chat.py` por la puerta del dash (`/api/editor-apimart`) con la llave del editor (`DASH_CLAVE_EDITOR` de `/opt/broll-tiktok/secretos/dash.env`, que el corredor reparte solo a las etapas que la usan) y la versión de `/opt/anuncios/VERSION`. El dash aplica el tope de US$ 0,50 por anuncio aunque se suba `topes.usd_por_anuncio` (hasta US$ 1 solo con `--tope-anuncio` y motivo; `avatar_auto.py` le pregunta al dash lo que ya lleva el anuncio antes de empezar, así que un avatar tiene 5 clips como mucho), no deja repetir un clip pagado, lleva el tope del día y anota el costo. Si el dash no contesta, no se genera nada. Los techos propios del corredor (US$ 0,30 por anuncio y US$ 1,00 por día, con `costos.jsonl` de este servidor) siguen igual. Un costo viejo que no llegó al dash queda en `estado/costos_pendientes.jsonl` y sale solo en la siguiente generación.

**Cuánto tarda.** *Medido* (ensayo real del montaje en el servidor, 2026-10-23, con 3 clips de mentira que hablan —voz de Windows sobre cuadros dibujados—, 19,4 s, sin gastar nada): unir y oír con Whisper 20 s, render 2,6 min, control de calidad 19 s = **3,3 min** en total, pico de memoria ≈ 2 GB; pasaron 14 de las 15 compuertas (la de duración falló porque el ensayo dio 19,4 s y la receta pedía 20: de ahí el margen de arriba). *Estimado, sin medir con servicios reales* (la espera a APIMart): 3 clips ≈ 15 a 25 min, 5 clips ≈ 25 a 40 min, 8 clips ≈ 40 a 60 min; casi todo es esperar al servicio de video (casi no usa procesador). Las etapas tienen tiempo máximo en `corredor.json` (`avatar_plan` 15 min, `avatar_generar` 120, `avatar_montaje` 60).

**Ensayo del montaje sin gastar (para volver a medir o después de tocar Remotion):** `vps/ensayo_montaje_avatar.py` arma un pedido de avatar de mentira con 3 clips que hablan y corre `avatar_auto.py montar` de verdad (Whisper, Remotion y las compuertas); no llama a APIMart, a Fish, al dash ni a Drive. Los clips de mentira se hacen en un PC con voz de Windows en español (Helena, `System.Speech`, `Rate = 3`, un texto de ≈ 25 palabras por clip: 6 a 7,5 s) y `ffmpeg -loop 1 -framerate 24 -t 8 -i cuadro.png -i voz.wav -vf scale=480:854,format=yuv420p -af adelay=300|300,apad -c:v libx264 -c:a aac -t 8 NN.mp4`; se suben `cuadros/NN.png` y `clips/NN.mp4` junto con el script a `/opt/anuncios/.cache/ensayo_av/` (dueño `broll`) y se corre `corredor-ctl ejecutar -- /opt/broll-tiktok/venv/bin/python -u /opt/anuncios/.cache/ensayo_av/ensayo_montaje_avatar.py`; deja el mp4 y los informes en `.cache/ensayo_av/salida/` y borra el producto y el anuncio de prueba. Código de salida 5 = el video quedó pero alguna compuerta falló.

**Lo que NO puede hacer el servidor y queda en manos de una persona (no está escondido):**
- Poner el avatar: fotos de una persona que autorizó su cara y su voz, y su `avatar.json`. Sin eso no corre.
- Juzgar si la cara, la ropa y la voz se parecen de un clip a otro, si el producto sale deformado o si el gesto se ve raro. Hay revisión automática (un modelo de visión mira cada cuadro; Whisper oye cada clip y compara con el texto), pero **solo avisa**: una persona debe mirar el video en el dash antes de aprobarlo, como con los demás.
- Decidir si sube el tope cuando el avatar no cabe (el corredor no lo hace solo).
- El gancho y la música del avatar son los de siempre (la música de la biblioteca; no se rehace el gancho con IA aparte porque el avatar ya habla desde el segundo 0).

Los pedidos de avatar con correcciones del equipo, o de una receta que no diga `ia`, se dejan a las personas igual que los demás. Todo queda en `anuncios/<oferta>/ia/<AD>/` (`cuadros/`, `clips/`, `avatar/` con el plan y la generación, `final/`); la limpieza de los 14 días también borra sus cuadros, clips y la base.

### Variaciones hechas por el servidor (desde el 2026-11-13; el dueño: «que coja el original o lo ubique y edite sobre ese mismo la nueva variación… anuncios de calidad y lo más económico posible»)

En `/tablero › Variaciones` cada variación con anuncio base trae «🎞️ Abrir el original» (el video NUESTRO en Drive) y, para el dueño, «Hacerla en el servidor». El corredor, en cada pasada y antes de la Fábrica (`paso_variaciones` → `variaciones_vps.py`), hace dos cosas:

1. **Índice de originales** (`originales.py`, solo lectura de Drive): recorre `OFERTAS LATAM` hasta 3 niveles (sin `B-rolls`, `REFERENCIA`, `AVATAR`, entregables ni `_anteriores`) y le manda al dash nombre, carpeta, tamaño y fecha de cada video (`POST /api/variacion-editor`, acción `indice`). Cada `indice_horas` (12) o cuando cambia lo que el servidor tiene encendido; `corredor-ctl correr --indice` lo fuerza. Medido el 13-nov: 1 143 videos en 187 carpetas, 46 s, 188 lecturas de Drive. El dash empareja oferta + número con el nombre del archivo (`functions/_originales.js`; si dos ofertas distintas empatan, no adivina).
2. **Un pedido por pasada**, solo si `corredor.json › variaciones` tiene `activo: true` y `tope_usd` > 0 (las decide el dueño; vienen apagadas) o con `corredor-ctl correr --variacion <id> --tope-usd 0.10` (el sí del dueño para ESA variación; con el servidor apagado el dueño igual puede dejarla pedida en la página y queda «en espera» hasta esa orden). El servidor hace el gancho, la nota y el cambio que el dueño vio AL PEDIR (el dash los copia dentro del pedido; si se editan después, hay que cancelar y volver a pedir). Etapas, cada una en su proceso con su tiempo máximo (`var_*` en `tiempos_max_min`) y **solo `var_gancho` ve la llave del dash**: bajar el original (solo si está dentro de `OFERTAS LATAM`; tamaño y MD5), `variacion_auto.py analizar` (Whisper: dónde termina la primera frase, corte en el punto más silencioso; lector de texto: los subtítulos quemados del original), `gancho` (el inicio nuevo; antes de pagar estima y respeta el tope), `render` (Remotion, solo esos segundos), `unir` (ffmpeg: inicio nuevo + el original desde el corte, sin tocarlo), `revisar` (compuertas) y entregar a `<carpeta de primer nivel de la oferta> › VARIACIONES DEL SERVIDOR` (nunca pisa: `_v2`). Avisa 4 etapas al dash (bajar, gancho, render, control: cada aviso reescribe la fila) con el número de intento (si el dueño la vuelve a pedir, el dash rechaza con 409 los avisos viejos y el servidor la deja). Con `revision_beto: true` (el defecto) queda «lista para revisar» y Beto la marca Lista; con `false`, Lista directo (el dash solo lo acepta si así estaba el servidor al pedirla). Lo pagado va atado a su gancho (huella de modo + gancho + imágenes + ensayo/real): si cambia, lo anterior queda en `gancho/_anteriores/<huella>/` y no se reusa (antes un «Rehacerla» con otro gancho reusaba el clip o las imágenes viejas, y una real después de un ensayo habría reusado las imágenes de prueba).

**Modos del inicio** (`variaciones.modo`, por defecto `fish`): `fish` (≈ US$ 0,03: la voz de los flujos + 1-3 imágenes nuevas), `original` (≈ US$ 0,03: la voz del inicio viejo + imágenes nuevas) o `clip` (≈ US$ 0,07-0,09: la persona del original dice el gancho en un clip de IA de 6-8 s con voz y labios, que parte de un cuadro del original SIN texto encima). **El clip pone palabras nuevas en la boca de la persona del original**: solo se hace con las ofertas de `variaciones.clip_ofertas` (el dueño pone una oferta ahí solo si TODOS sus originales muestran avatares de IA o personas que autorizaron su cara y su voz: se autoriza la oferta, no cada video; regla del editor); con otra oferta el servidor no la toma y le dice al dash por qué. La voz del clip es neutra (antes decía «predicadora ante una iglesia» para todas las ofertas) y nada mide si se parece a la del resto del video: Beto la oye. Cada prompt pasa `revisar_prompts.py` antes de pagar; el tope del dash por anuncio (US$ 0,50) y el del día del corredor siguen mandando.

**Lo que cuida el control** (`revisar`, todo medido, sin juicio): formato 1080x1920 a 30 fps; inicio de 1,5 a 10 s; el resto igual al original (PSNR ≥ 35 dB en 4 cuadros); sin negro ni silencio en la unión; Whisper oye el gancho (≥ 80 %); sonoridad pareja (±4 LU: los originales de Beto van a ≈ −7 LUFS, el inicio se comprime y sube); y **sin palabras de la frase vieja en el subtítulo quemado** después de la unión. Esto último salió del ensayo: el original de Sermones trae subtítulos quemados (minúsculas blancas a media pantalla, 4 palabras) y el grupo «ti yo sé como» cruzaba el corte; ahora el análisis (RapidOCR, local) mide ese estilo y el inicio nuevo lo imita, y la unión borra con `delogo` SOLO las palabras viejas mientras se ven (0,5 s en la 101). El cuadro de partida del clip se busca en los huecos sin subtítulo (en la 101, el segundo 5,7); borrar el texto con OpenCV dejó una mancha, así que si no hay un cuadro limpio el modo `clip` se niega antes de pagar.

**Ensayo sin gastar** (`variacion_auto.py ensayo <video> --gancho "…"`, o etapa por etapa con `--ensayo`): voz de prueba e imágenes de prueba. 13-nov, Sermones 101.7 sobre la 101 en este servidor: análisis 41 s, inicio 9 s, render 56 s, unión 93 s, control 32 s (≈ 4 min, más 1-3 min de bajada); todas las compuertas bien. Pruebas: `autoprueba_variacion.py` (112 con ffmpeg y lector en el servidor; `--puras` 97; 16 roturas a propósito detectadas). La unión corta la imagen y el sonido del inicio al mismo largo: con el ffmpeg 6.1 del servidor el AAC del inicio trae ~19 ms de relleno y sin ese corte el cuerpo salía 1 cuadro tarde (lo vigila la prueba del destello y el pitido, que solo falla en el servidor) y `autoprueba_corredor.py` (263). **Topes del dash para la llave del editor** (revisor-seguridad 13-nov): un índice completo cada 50 min (dentro de esa ventana solo se anota lo encendido, una vez cada 10 min), 20 grupos reescritos por envío salvo el primero (el resto entra en la pasada siguiente: el servidor lo reintenta solo), 150 escrituras del índice al día, 20 avisos «en-curso» por pedido y la lista de pendientes guardada 60 s. Un `.mp4` que Drive no tiene por video (p. ej. una lista HLS) no entra al índice ni se baja, ni uno que esté en `B-rolls`/`REFERENCIA`/`AVATAR`; y lo bajado debe empezar como un MP4/MOV de verdad (caja `ftyp`, `moov`…: el tipo de Drive lo pone quien sube el archivo). `datos_de` de `drive_api.py` pide `mimeType` (`CAMPOS_DATOS`: Drive v3 solo da los campos que se piden). La orden a mano `--variacion <id>` lee ESE pedido directo del dash (debe estar pendiente) y no sigue con otra a medias; una variación borrada en el tablero se abandona (404) en vez de trabar la cola.

### Dónde está cada cosa (`/opt/anuncios`)

```
scripts/ src/ package*.json remotion.config.ts node_modules/ vps/   el código (root:root, solo lectura; viene de git con desplegar-corredor.sh)
modelos/        clip/ (4 archivos con SHA-256 fijo) y el modelo de Whisper (enlaces duros a /opt/broll-tiktok/modelos: no se duplica)
corredor.json   topes, tipos permitidos, exigir_arranque, tiempos máximos por etapa y recursos mínimos (viene de git: vps/corredor.json)
secretos/       ya no guarda llaves: apimart.key SOBRA desde la versión 2026.11.10-2 (la unidad ya no la carga; la borra el dueño)   [la llave del dash y el permiso de Drive son los del b-roll: /opt/broll-tiktok/secretos]
drive/OFERTAS LATAM/<OFERTA>/{B-rolls,REFERENCIA,ANUNCIOS[,AVATAR]}   espejo local de Drive = el «Drive de escritorio» del servidor (los scripts del editor corren contra él sin cambios; AVATAR solo para los pedidos de avatar)
public/ anuncios/ .cache/ logs/ estado/   lo único con escritura (broll:broll). estado/PAUSA (la pausa), estado/pedidos/<proyecto>_<AD>.json (la etapa de cada pedido: una pasada cortada se retoma ahí), estado/gasto.json (lo gastado por día), estado/ultima_corrida.json
costos.jsonl    lo que costó cada generación de APIMart (broll, 600)
anuncios/_variaciones/<id>/   cada variación hecha por el servidor (original.mp4, analisis.json, gancho/, final/, revision.json, hojas); lo pesado se borra a los `dias_guardar_trabajo`, gancho/ (lo ya pagado) se queda
estado/variaciones/<id>.json  la etapa de cada variación (una pasada cortada se retoma); estado/indice_originales.json  cuándo se mandó el índice
logs/corredor_AAAAMMDD.log   el registro (45 días; con las llaves tapadas)
```

### Seguridad: qué puede y qué no

- Corre como `broll` (sin sesión ni sudo) en una unidad con `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, `CapabilityBoundingSet=` vacío, filtro de llamadas al sistema, sin acceso a `/opt/radar-de-ofertas`, `/opt/actions-runner`, `/root`, `/home`, los datos del b-roll ni `docker.sock`, y solo escribe en sus carpetas de datos. Límites: `Nice=10`, `IOSchedulingClass=idle`, `CPUWeight=20`, `CPUQuota=150%` (de 200 %), `MemoryHigh/Max` 5G (aviso) y 5500M (tope), sin swap, 1024 tareas. Es un trabajo de fondo: el radar, el runner de GitHub y Hermes pasan primero.
- **Drive:** usa el mismo permiso que el b-roll (Drive completo). `espejo_drive.py` **solo lee** (bajar) y **solo crea** (subir el mp4 a `ANUNCIOS`): nunca pisa un archivo con otro contenido (sube `_v2`), ni borra, ni mueve, ni comparte. Lo único que quita del espejo local es lo que el equipo borró de `B-rolls` en Drive.
- **Dinero:** el servidor no tiene la llave de APIMart (desde la versión 2026.11.10-2): lo que cuesta pasa por el dash, que pone el tope por anuncio y el del día. La llave del editor del dash la ven solo los scripts que avisan o gastan (traer, guion, voz, gancho, montaje y, en un avatar, las escenas, la generación y el montaje); el b-roll y la entrega no la reciben, y ninguno ve `APIMART_API_KEY` ni la carpeta de credenciales de systemd aunque una unidad vieja las trajera (`LLAVES_POR_ETAPA` y `NUNCA_A_LOS_HIJOS` en `corredor_vps.py`); cada script recibe `--tope-usd` y se niega solo si no alcanza; el corredor lleva el gasto del día (`estado/gasto.json`) y se detiene en US$ 1,00. Sin `aprobacion.topeUsd` solo gasta si `corredor.json` trae `topes.receta_sin_tope = "usa-el-techo"` (así va desde el 2026-10-28, pedido del dueño: «el tope no hay, depende del tipo de anuncio; se crean y se optimiza»): entonces gasta hasta los techos (US$ 0,30 por anuncio y US$ 1,00 por día); con `"no-gasta"` (el defecto del código) no gasta, y `--tope-usd 0` siempre significa «no gastes». La receta con tope propio sigue mandando.
- Nada de lo que viene del dash, de Meta o de TikTok se obedece (ids validados, ningún comando armado con texto de terceros, notas al dash con frases fijas). El registro tapa las llaves.
- **Lo que queda abierto** (igual que el b-roll): el servicio abre videos de terceros con ffmpeg, onnx y Chrome, y el permiso de Drive lo lee el mismo usuario. La unidad lo acota (no puede reescribir el código ni ver el radar), no lo elimina.
- Los scripts que corre solo (los de `corredor-archivos.txt`) **no los puede proponer el equipo** (`DENEGADAS` de `functions/api/equipo.js`, `equipo-kit/zonas.json`; `probar-equipo.mjs` y la sección 3e de `Auditar-Seguridad.ps1` lo vigilan): los cambia el dueño o una sesión suya y se despliegan desde `main`.

### Actualizar el código del corredor

Cambiar en el repo, correr `python scripts/autoprueba_corredor.py` (263 comprobaciones, sin internet: dash, Drive y APIMart de mentira; el avatar tiene además `autoprueba_avatar.py`, 175; las dos también corren en el servidor, dentro de la unidad: `corredor-ctl ejecutar -- /opt/broll-tiktok/venv/bin/python scripts/autoprueba_avatar.py`, y la del corredor salta ahí la parte que revisa los archivos del repo) y las del editor, hacer commit y `bash editor-anuncios/vps/desplegar-corredor.sh --simular` y luego `bash editor-anuncios/vps/desplegar-corredor.sh --cambio <nombre>` (con `--esperar MIN` si hay un anuncio en curso). Sube **desde git** lo que lista `vps/corredor-archivos.txt` (lista cerrada), comprueba el SHA-256 de cada archivo contra el commit y deja cada reemplazado como `<archivo>.antes-<cambio>`; el instalador toma el mismo candado que el corredor, instala lo que falte (paquetes, Remotion, modelos) de forma idempotente, **nunca enciende el reloj** y, si algo falla, devuelve los archivos. Cómo se deshace todo: `corredor-ctl pausar`, `systemctl disable --now anuncios-corredor.timer`, quitar la unidad y `rm -r /opt/anuncios` (no toca el radar, el runner, Hermes ni el b-roll); la llave de APIMart es `secretos/apimart.key`.

### Límites conocidos (decirlos, no esconderlos)

- El control automático **no ve niños, personajes con licencia ni logos sin letras**: una persona debe mirar cada anuncio en el dash antes de aprobarlo. Las marcas manuales (`--marcar-toma`) que se hicieron en el PC **no viajan** al servidor todavía.
- Si el equipo restaura en Drive un video que había borrado de `B-rolls`, el servidor no lo vuelve a copiar (lo que está en `fuentes.csv` como descartado no vuelve).
- Los videos de b-roll son de terceros (decisión de derechos del dueño, 2026-10-21); nadie ha oído las voces finales de Fish; el servidor no cambia eso.
- Sin la orden de arranque del dash (`arranque`), con `exigir_arranque` en `true`, el reloj no toma nada; solo `--pedido` salta esa regla.
- Pendiente de probar con servicios reales: guion y voz en el servidor (APIMart, Fish), la subida a Drive del mp4 final y el aviso «lista» con la barra de avance en `/fabrica`; el reloj hora tras hora. **Del avatar nada se ha probado con servicios reales** (cuadros, clips con voz, sincronía de labios, revisión con visión): solo con APIMart y Drive de mentira (175 + 231 comprobaciones) y un ensayo real del montaje (Whisper, Remotion y las compuertas) con clips de mentira que hablan; la prueba real pide su propio sí del dueño, con el costo (≈ US$ 0,29 por 3 clips) a la vista.

## Espacio del editor: el equipo de video trabaja DENTRO del servidor (desde el 2026-10-29)

Pedido del dueño: «que en el equipo del editor también edite en el VPS y suba todo al Drive». Cada persona del equipo de video (hoy Beto) tiene en el servidor una **cuenta de Linux propia, sin permisos de administrador y sin la llave de Drive**, con su espacio de trabajo (`~/editor-anuncios`: scripts, Remotion con su navegador, modelos), topes de memoria y procesador, y una **puerta** para todo lo que entra o sale de Drive. Elegido por el dueño el 2026-10-29: «terminal propia sin permisos» y «videos terminados + copia de sus proyectos».

**Qué hay (todo viaja por `corredor-archivos.txt`, con SHA-256, copias `.antes-<cambio>` y marcha atrás):**

| Pieza | Qué es |
|---|---|
| `scripts/puerta_editor.py` | El mismo archivo es el cliente (`puerta traer / entregar / respaldar / estado`, lo corre la persona) y el servidor (`servir`, lo corre systemd). |
| `vps/editor-puerta.service` + `editor-puerta.path` | **Plantillas** que se instalan como `editor-puerta@.service` y `editor-puerta@.path`: una instancia por persona (`editor-puerta@beto.path` vigila su cola y despierta a `editor-puerta@beto.service`; un comodín en el medio de la ruta no despertaba a systemd: medido). La unidad corre como usuario `puerta` (el único que recibe el permiso de Drive por `LoadCredential=`), aislada y sin red salvo Drive; el límite de arranques por hora es por persona. |
| `vps/instalar-editor-vps.sh` | Cuentas, carpetas de peticiones (`/srv/editor/personas/<persona>/{cola,entregas,resultados,procesadas}`, **de root**: la persona no las puede mover), topes de la cuenta, envoltorios `puerta`, `pesado` y `claude`, espacio de trabajo (solo la primera vez), llave pública limitada. Idempotente. **Regla de oro:** root nunca lee ni escribe dentro de la casa de la persona; todo lo que toca ahí lo hace *como la propia persona* (`runuser`), porque ella controla esas rutas y puede dejar enlaces simbólicos hacia las de root (auditoría del 2026-10-29: así se habría podido pisar `/root/.ssh`). |
| `vps/pesado` | Para lo pesado (render, Whisper, montaje): espera a que el corredor de anuncios no esté trabajando y haya 3,5 GB libres, y corre el comando (o lo deja en segundo plano con `--fondo`). |
| `vps/editor-puerta.json` | Personas, carpeta de ofertas, nombre de la carpeta de respaldos (`EDITOR` dentro de MI NEGOCIO) y topes (video 1,5 GB y 10 min, respaldo 500 MB, 30 peticiones por hora, 10 GB de disco libre). |
| `vps/CLAUDE-VPS.md` | La guía que lee el Claude de la persona dentro del servidor (se copia a `~/CLAUDE.md`). |

**Qué hace la puerta (y nada más):** `traer` baja de Drive, solo lectura, la oferta pedida (B-rolls, REFERENCIA vigente, AVATAR) o la música a `/srv/editor/drive` (la persona lo lee pero no lo escribe; `OFERTAS_LATAM_DIR` ya apunta ahí para que `fabrica.py` y `broll_tiktok.py recoger` lo encuentren); `entregar` sube UN video a `OFERTAS LATAM › <oferta> › ANUNCIOS` (nunca pisa: `_v2`, `_v3`; comprueba tamaño y MD5 en Drive y devuelve el enlace); `respaldar` sube UN .zip del proyecto (sin `node_modules`, b-rolls ni archivos de más de 30 MB) a `MI NEGOCIO › EDITOR › <persona>`. No borra, no mueve, no comparte, no escribe en otro lado (la «cerca» de `drive_api`).

**Cómo se protege de una petición mala:** la oferta se busca por nombre entre las carpetas reales (si no existe, se rechaza y se listan las que hay); los archivos de la persona se abren con `O_NOFOLLOW` y se exige que sean archivos normales, **suyos** y sin enlaces duros; se **copian a una carpeta privada del servicio antes de revisarlos y subirlos** (lo que se revisa es lo que se sube: sin cambio entre una cosa y otra); un video pasa por `ffprobe` y un zip tiene que ser zip; la petición es un JSON que solo se lee como datos; todo con topes. El archivo de `entregas` se quita siempre (salga bien o mal).

**Cómo se protege el instalador (lo que corre como root):** la cuenta se crea **vencida** y sin llave; solo `--dar-acceso` la abre y `--quitar-acceso` la cierra (nunca `usermod -U`). El espacio de trabajo se arma en una carpeta de root y se pone en su sitio con `mv -T`, la llave se valida sin escribir archivos hermanos y se escribe con `no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc`, y el ritmo de peticiones vive en `/srv/editor/estado` (la persona no lo alcanza). El ataque de la auditoría (enlaces de `~/.ssh`, `~/bin`, `~/trabajos`, `~/.profile` hacia una carpeta de root) se **reprodujo contra el instalador corregido en el servidor y no tocó nada de root**.

**Dar o quitar el acceso (lo hace el dueño o una sesión suya, no la persona):**
```bash
# la persona genera su llave en su PC (la privada no sale de ahí) y manda SOLO la línea pública (ssh-ed25519 ...)
/opt/anuncios/vps/instalar-editor-vps.sh --dar-acceso beto /ruta/a/beto.pub     # pone su llave (limitada: sin reenvío de puertos, agente ni X11) y quita el vencimiento
/opt/anuncios/vps/instalar-editor-vps.sh --quitar-acceso beto                    # quita la llave, vence la cuenta y corta sus sesiones (no borra datos)
/opt/anuncios/vps/instalar-editor-vps.sh --estado
```
Agregar una persona: su usuario en `editor-puerta.json` → `personas`, desplegar, `instalar-editor-vps.sh --aplicar` y `--dar-acceso`. **La huella del servidor** (que la persona compara al conectarse la primera vez): `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.

**Se despliega con un manifiesto reducido si el servidor tiene cambios que aún no están en `main`:** el 2026-10-29 el servidor tenía `guion_auto.py`, `corredor_vps.py` y `corredor.json` de otras sesiones sin publicar; subir un commit basado en `main` los habría **revertido**. Antes de `desplegar-corredor.sh`, `--simular` y mirar que solo cambien los archivos propios.

**Límites dichos con honestidad:**
- La persona **puede ejecutar código en el servidor** (como usuario sin permisos y con topes). No ve llaves ni los datos del radar, el runner, el corredor ni el b-roll (comprobado), pero comparte 2 núcleos y 8 GB con ellos: un render pesado hecho sin `pesado` puede frenar al corredor (el sistema sacrifica primero lo de la cuenta del editor y, si no, al corredor). Tiene salida a internet.
- `pesado` es un acuerdo, no un candado: la cuenta tiene sus propios topes (4 GB, 1,5 núcleos), pero no hay una cuota de disco por cuenta (la puerta y el corredor no trabajan con menos de 10 GB libres).
- No tiene llaves: generar imágenes o video con IA desde su cuenta requiere la conexión con el dash (desde la versión 2026.11.10-2 bastan las dos líneas `DASH_URL` y `DASH_CLAVE_EDITOR` en `~/editor-anuncios/.env`, con los topes del dash: US$ 0,50 por anuncio y, por día, US$ 5, 150 pedidos y 100 subidas; la llave de APIMart ya no hace falta en ningún lado). **Ojo:** desde el 2026-11-10 `/api/editor-conexion` (la tarjeta «Conectar este editor» de la página Editor de anuncios) le da esas dos líneas al dueño **y al rol `videos`**, o sea que Beto puede bajarlas desde su sesión del dash. Si su espacio del VPS las lleva lo decide el dueño: hoy no las tiene, y con ellas gastaría APIMart (hasta esos topes) y también abriría la cola de la Fábrica.
- No toma pedidos del dash (`fabrica.py traer` necesita la llave del editor del dash, que no está en su cuenta): decisión pendiente del dueño.
- Su Claude Code corre con **su propia cuenta de Anthropic** (la inicia él con `claude`); la copia del programa es la que diga `CLAUDE_ORIGEN` en `instalar-editor-vps.sh` (si no hay ninguna, la persona lo instala con el instalador oficial).
