# Radar de ofertas

Busca en la **Biblioteca de anuncios de Meta** (pública, no pide token de Facebook) ofertas de productos digitales que llevan días anunciándose, les pone una nota y las escribe en una **planilla de Google Sheets** tuya. La página `/radar` del dashboard lee esa planilla.

Está escrito en Python y solo necesita la librería `requests`. Puede correr en tu PC (con el Programador de tareas de Windows) o en un servidor Linux barato (con `systemd`).

## Lo que necesitas

1. **Python 3.12** (`instalar_y_probar.bat` lo instala con winget si falta).
2. **Una planilla de Google Sheets nueva** para el radar. El radar le crea las pestañas `OFERTAS` y `PALABRAS-CLAVE`.
3. **Un cliente OAuth de Google** para que el radar pueda escribir en tu planilla: en Google Cloud Console crea un proyecto, activa la «Google Sheets API», y en Credenciales crea un «ID de cliente de OAuth» de tipo **App de escritorio**. Descarga el JSON y guárdalo en esta carpeta con su nombre (`client_secret_....json`). No se sube a GitHub.
4. Opcional: una llave de **Gemini** (Google AI Studio) para que el radar resuma y califique mejor cada oferta. Se guarda en el archivo `gemini.key` de esta carpeta (o en la variable `GEMINI_API_KEY`). Sin ella el radar funciona con `--sin-ia`. Tope de gasto diario: variable `GEMINI_MAX_USD_DIA`.
5. Opcional: un **proxy** (variable `PROXY_URL` en un archivo `.env` de esta carpeta) si Meta bloquea las búsquedas desde tu servidor.

## Puesta en marcha (en tu PC)

1. Doble clic en `instalar_y_probar.bat`: instala lo necesario y corre la prueba sin red.
2. Pon el ID de tu planilla en la variable `SHEET_ID` (en el archivo `.env` de esta carpeta: una línea `SHEET_ID=...`).
3. `python radar.py --autorizar-sheets`: abre el navegador para que autorices el acceso a tu planilla (guarda `token_sheets.json`, que no se sube a GitHub). **Si el radar va a correr en un servidor, autoriza con una cuenta de Google aparte** (no la de tu negocio) a la que solo le compartas la planilla del radar: el permiso que queda guardado alcanza todas las planillas de esa cuenta, y en la tuya están tus libros de ventas.
4. `python radar.py --preparar-sheets`: crea las pestañas y las columnas.
5. Primera corrida corta: `python radar.py --max-kw 3 --max-ofertas 3`.
6. Para que corra solo: crea una tarea en el Programador de tareas de Windows que ejecute `correr_radar.ps1` cada 2 a 4 horas.

## Ajustarlo a tu mercado

- `keywords.csv`: las búsquedas de arranque (se usan si la planilla no tiene pestaña PALABRAS-CLAVE). Pon las de tus nichos e idiomas.
- `nichos.csv`, `angulos.csv`, `temporadas.csv`, `foco.csv`: nichos, ángulos de venta, fechas especiales y focos por fechas. Traen ejemplos del mercado de Brasil y Latinoamérica: cámbialos por los tuyos. Las filas de `foco.csv` traen fechas ya pasadas a propósito (no hacen nada): pon `desde` y `hasta` (máximo dos semanas por fila) cuando quieras que el radar dé prioridad a esas búsquedas.
- `PROMPT_SISTEMA.md`: cómo se califica una oferta (la nota de 0 a 10). Léelo y ajústalo a tus precios y países.
- El radar usa la hora de Bogotá; si estás en otro país, cambia `TZ` en `vps/correr_radar.sh` y en `vps/radar.service`.

## En un servidor Linux (opcional)

`vps/` trae el servicio y el temporizador de `systemd` (cada 2 horas) y el script que lo corre. Supone el código en `/opt/radar-codigo` (de root, solo lectura) y los datos en `/opt/radar-de-ofertas` (usuario `radar`, con su `venv`). Pídele a Claude que te guíe; nunca lo corras como root.

## Funciones extra que necesitan el dashboard publicado

Los módulos `dash_estados.py`, `seguimiento.py`, `pedidos_flujo.py` y `publicacion.py` conectan el radar con la página `/radar` (pasar a la planilla el estado que eliges en la página, fotografiar cada día las ofertas que vigilas, buscar el WhatsApp de la competencia). Necesitan la dirección de tu dashboard (`DASH_URL`) y la llave `CLAVE_RADAR` (la misma en Cloudflare y en `dash.key`). Son opcionales: el radar normal no las usa.

## Pruebas

`python test_logic.py` (y los demás `test_*.py`): no usan red ni llaves.
