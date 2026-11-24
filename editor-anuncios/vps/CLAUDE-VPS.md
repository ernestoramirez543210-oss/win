# Estás trabajando DENTRO del servidor (VPS), no en un PC

Este archivo lo pone `instalar-editor-vps.sh` en tu casa. Si dice algo distinto al `CLAUDE.md` de `editor-anuncios/` (que se escribió para un PC con Windows), **manda este**. Si un comando del editor no funciona aquí (rutas de Windows, `.cmd`, `.ps1`, Drive para escritorio), no lo fuerces: dilo y usa el equivalente de abajo.

## Dónde estás y qué tienes

- Servidor Linux del dueño (tu proveedor de VPS): 2 núcleos y 8 GB de memoria que **comparten** el radar de ofertas, el corredor de anuncios y tu cuenta. Tu usuario es un usuario normal: **sin `sudo`, sin administrador**, y no ves los datos de los otros servicios (radar, runner, corredor, b-roll). No intentes entrar a ellos.
- Tu proyecto del editor: `~/editor-anuncios` (código, `src/` de Remotion, `scripts/`, `public/`, `anuncios/`). Tus proyectos propios de Remotion (por ejemplo «remotion animaciones»): ponlos en `~/proyectos/<nombre>` y haz `npm ci` ahí.
- Python: usa `python` (ya apunta al entorno del servidor con faster-whisper, CLIP, ffmpeg, etc.). Node 24 y ffmpeg ya están.
- Tu cuenta tiene topes: unos 4 GB de memoria y 1,5 núcleos. Si algo se pasa, el servidor lo mata a **tu** proceso.
- **No hay llaves aquí** (ni la de Drive ni la del dash; la de APIMart ya no la usa ningún editor: todo pasa por el dash). Para generar imágenes o videos con IA hace falta que el dueño conecte este espacio al dash (dos líneas en `~/editor-anuncios/.env`) o que lo haga él; no las busques ni las pidas por el chat.

## Lo pesado va con `pesado`

Render de Remotion, transcribir con Whisper, analizar b-roll, montaje: **siempre** con `pesado` delante. Espera a que el corredor de anuncios no esté trabajando y haya memoria libre, y así dos trabajos pesados no se pisan.

```bash
pesado node scripts/remotion.mjs render ...            # espera su turno y corre; ves la salida
pesado --fondo python scripts/montaje_auto.py ...       # sigue aunque se corte tu conexión; la salida va a ~/trabajos/<fecha>-<nombre>.log
```

Un render de un anuncio de 52 s tarda unos 7 min aquí (en el PC del dueño, ~21 min). Usa concurrencia 1 (`--concurrencia 1`).

## Drive: solo por la puerta (`puerta`)

No hay Drive para escritorio ni `G:`. Todo lo que entra o sale de Drive pasa por `puerta`, que lo hace el servidor con su propio permiso (tú nunca lo recibes). Cada comando espera la respuesta y dice si salió bien.

| Quieres | Comando |
|---|---|
| Traer el material de una oferta (B-rolls, REFERENCIA, AVATAR) | `puerta traer "NOMBRE DE LA OFERTA"` |
| Traer la música de fondo | `puerta traer --musica` |
| Subir un video terminado a `OFERTAS LATAM › <OFERTA> › ANUNCIOS` | `puerta entregar "NOMBRE DE LA OFERTA" ruta/al/video.mp4` |
| Guardar una copia de tu proyecto en `MI NEGOCIO › EDITOR › tu nombre` | `puerta respaldar` (o `puerta respaldar ~/proyectos/mi-proyecto`) |
| Ver qué hay en tu cola y los últimos resultados | `puerta estado` |

- Lo que traes queda en `/srv/editor/drive/OFERTAS LATAM/<OFERTA>/…` (solo lectura para ti; ya está puesta la variable `OFERTAS_LATAM_DIR` para que `fabrica.py`, `broll_tiktok.py recoger` y los demás scripts lo encuentren solos). La música queda en `public/musica/biblioteca`.
- **Todo anuncio terminado se entrega con `puerta entregar`** (regla fija del dueño: siempre dentro de la carpeta de la oferta, en `ANUNCIOS`). No existe «dejarlo suelto» ni copiarlo a otro lado de Drive. El nombre del archivo: letras, números, espacios, punto y guion, sin tildes, y termina en `.mp4`. Un video con el mismo nombre y otro contenido no se pisa: sube como `_v2`.
- Después de entregar, **confirma con el enlace que devuelve** (y el tamaño). No digas «subido» sin ese enlace.
- `puerta respaldar` empaqueta tu proyecto **sin** lo que se puede volver a bajar o hacer (`node_modules`, b-rolls, modelos, archivos de más de 30 MB). Hazlo al terminar una sesión de trabajo y antes de limpiar nada. El servidor guarda lo que no está respaldado solo mientras haya disco.
- Las peticiones tienen topes (30 por hora; videos hasta 1,5 GB y 10 min; respaldos hasta 500 MB). Si dice que no, el mensaje explica por qué.

## Tus mejoras al editor llegan al dash: `puerta publicar`

El botón «Crear toda la oferta» del dash usa el editor de producción (`/opt/anuncios`), que NO es tu espacio de trabajo (`~/editor-anuncios` es tu copia). Para que lo que mejoras ahí pase a producción y el dash lo use en el siguiente anuncio:

| Quieres | Comando |
|---|---|
| Ver qué subiría y qué choca con producción (sin enviar nada) | `puerta publicar --simular` |
| Pasar tus mejoras a producción | `puerta publicar` |
| Traer de producción lo que tú NO cambiaste y producción sí (tus archivos «viejos») | `puerta sincronizar --al-dia` |
| Poner tu base al día con un archivo que cambió en producción | `puerta sincronizar --traer ruta/del/archivo` (copia la versión de producción; tu versión queda guardada) o, si ya pasaste a tu archivo lo que cambió allá, `puerta sincronizar ruta/del/archivo` |

Qué pasa con cada cosa (el servidor lo decide por la ruta del archivo, no por lo que digas):
- **Parte visual** (`src/FullVideo.tsx`, `src/types.ts`, `src/Root.tsx`, `src/index.ts`, `src/components/*.tsx`): se revisa sola y, si pasa, queda en producción en uno o dos minutos. Antes de instalar, el servidor lee tu código como programa (nada que lea archivos al empaquetar, salga a la red o ejecute texto) y renderiza el anuncio de demostración, un ejemplo fijo de efectos y las pruebas que lleves, con tu código, en una caja sin red ni llaves; si algo no se ve o falla, **no se instala** y el mensaje dice qué.
- **Guías** (`CLAUDE.md`, `.claude/skills/editor-video` y `fabrica-ofertas`) y **`src/index.css`** (Tailwind lo ejecuta al empaquetar): esperan el visto bueno del dueño, igual que los programas.
- **Programas de montaje** (`scripts/*.py` y `*.mjs`): corren con las llaves de pago del corredor, así que **esperan el visto bueno del dueño** (te dice «pendiente»). No los des por activos hasta que el dueño los apruebe. Un programa no puede llamarse como un módulo estándar de Python (`json.py`, `argparse.py`…).
- **Nunca por aquí**: `puerta_editor.py`, `editor_promover.py`, `drive_api.py`, `corredor_vps.py`, `espejo_drive.py`, `apimart*.py`, `package*.json`, `remotion.config.ts`, llaves, configuración del servidor. Si tu paquete los trae, se rechaza entero.
- El código de `src/` no puede usar red (`fetch`, WebSocket, direcciones http), `eval`, `require` (ni `require.context`), `import()`, `import.meta`, `process`, `window`/`document`/`globalThis`, ni importar nada que no sea de `src/`, `react`, `remotion`, `@remotion/*` o `@fontsource/*`; todo `src=` tiene que ser `staticFile(...)` o una propiedad. Un video no lo necesita.
- **Si produccion cambió** un archivo desde que copiaste tu base (por ejemplo un arreglo desplegado desde git), sale «CONFLICTO» y no se pisa: mira `diff /opt/anuncios/<archivo> ~/editor-anuncios/<archivo>`, une los dos cambios en tu archivo y corre `puerta sincronizar <archivo>`. No lo resuelvas copiando tu versión encima: perderías el arreglo.
- **Pruebas**: lo que pongas en `~/editor-anuncios/pruebas/*.json` (un `videoData.json` que NO use archivos de `public/`: solo texto, fondos y efectos) se renderiza como prueba antes de instalar. Cuando agregues un efecto nuevo, agrega ahí un ejemplo que lo use: así el servidor lo prueba por ti.
- Tienes hasta 6 publicaciones por hora. Si el corredor está haciendo un anuncio, la instalación espera a que termine (nunca cambia el editor a mitad de un anuncio).
- Si algo sale mal en producción, el dueño devuelve todo a como estaba con una orden; tú no puedes (ni debes) tocar `/opt/anuncios`.

## Reglas de este lugar

1. Trabaja solo dentro de tu casa (`~`) y lo que `puerta` te deja en `/srv/editor/drive`. No leas ni listes otras carpetas del servidor, no cambies permisos, no abras puertos, no instales servicios.
2. Nada de contraseñas o llaves: ni las pidas, ni las pegues en archivos, ni las escribas en el chat.
3. Antes de borrar algo que no hiciste tú en esta sesión, pregunta. Lo que está en Drive nunca se borra desde aquí (no hay cómo, a propósito).
4. Si algo falla de forma rara en el servidor (disco lleno, memoria, `puerta` sin respuesta), **para y dile a la persona**; el dueño arregla el servidor.
5. Lo que corre solo en el servidor (el corredor de anuncios) no es tuyo: no toques `/opt/anuncios`. Tus mejoras llegan allá con `puerta publicar`, nunca copiando archivos.
