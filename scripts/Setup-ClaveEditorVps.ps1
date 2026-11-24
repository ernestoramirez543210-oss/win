# Una sola vez: pone en el servidor (VPS) la llave del editor para que la FABRICA DE OFERTAS del servidor pueda LEER la cola de pedidos del dash y, cuando una oferta pasa a la
# Fabrica, empiece sola a buscar b-rolls basados en los anuncios originales (ver editor-anuncios/vps/LEEME.md, seccion "Fabrica de ofertas").
#
# De donde sale la llave: del .env del editor (D:\editor-anuncios\.env: DASH_URL y DASH_CLAVE_EDITOR, que puso scripts\Setup-ClaveEditor.ps1). Es la MISMA llave con la que el editor anota
# sus costos; si ahi falta, corre primero  & .\scripts\Setup-ClaveEditor.ps1  (no la inventes ni la pegues en el chat).
#
#   - la guarda en el servidor: /opt/broll-tiktok/secretos/dash.env  (dueno root, grupo broll, permisos 640), por SSH con la llave radar_vps
#   - le pide al servidor que lea la cola del dash (SOLO lectura: no toma ni cambia ningun pedido) para confirmar que la llave sirve
#   - enciende el reloj que revisa la Fabrica cada hora (fabrica-broll.timer), si la prueba salio bien
#
# Que puede hacer esa llave en el dash: leer los pedidos de anuncio que siguen abiertos y avisar como va uno. El programa del servidor SOLO lee (nunca avisa ni cambia nada). No sirve
# para leer ventas, gasto, cuentas, sueldos ni datos de clientes. La llave NO se imprime ni se copia a ningun otro archivo de esta PC. No la pegues en el chat.
#
#   & .\scripts\Setup-ClaveEditorVps.ps1                  # copia la llave al servidor, la prueba y enciende la vigilancia
#   & .\scripts\Setup-ClaveEditorVps.ps1 -Probar          # solo mira (SSH, si ya hay llave, si el codigo de la Fabrica esta en el servidor): no lee la llave ni escribe nada
#   & .\scripts\Setup-ClaveEditorVps.ps1 -SinActivar      # guarda y prueba, pero no enciende el reloj
#   & .\scripts\Setup-ClaveEditorVps.ps1 -Editor C:\otra  # si el editor no esta en D:\editor-anuncios
#
# Cada llamada al servidor (ssh) tiene tiempo maximo (-SegundosSsh, 40 por defecto; la prueba de la llave, -SegundosPrueba, 120) y se reintenta una vez; antes de cada una el script dice
# "Conectando con el servidor...". El servidor se mira ANTES de leer la llave: si no contesta, el script se detiene con un mensaje claro y sin guardar nada (abre una pestana NUEVA del
# Terminal y vuelve a correrlo; ver scripts\_ssh-seguro.ps1). La prueba de la llave NO se repite sola.
# Se llama con & (no con "powershell -File"): ver la nota de Agregar-Variable.ps1.
# -RutaSsh es solo para probar el script en esta misma maquina contra un ssh de mentira (scripts/probar-setup-clave-editor-vps.cjs).
# ASCII-only, PS 5.1 safe.
param(
    [string]$Vps = 'root@203.0.113.10',
    [string]$LlaveSsh = (Join-Path $HOME '.ssh\radar_vps'),
    [string]$Editor = 'D:\editor-anuncios',
    [string]$Archivo = '/opt/broll-tiktok/secretos/dash.env',
    [switch]$Probar,
    [switch]$SinActivar,
    [int]$SegundosSsh = 40,
    [int]$SegundosPrueba = 120,
    [string]$RutaSsh = ''
)
$ErrorActionPreference = 'Stop'
$ayudanteSsh = Join-Path $PSScriptRoot '_ssh-seguro.ps1'
if (-not (Test-Path $ayudanteSsh)) { throw "Falta $ayudanteSsh (esta en la misma carpeta que este script; actualiza el repositorio con git pull)." }
. $ayudanteSsh
$AVISO_SIN_GUARDAR = 'No se guardo nada. Cierra esta ventana, abre una pestana NUEVA del Terminal y vuelve a correr el mismo comando.'

# El valor de NOMBRE= en el texto de un .env ('' si no esta o esta vacio).
function Leer-Linea([string]$texto, [string]$nombre) {
    foreach ($l in ($texto -split "`r?`n")) {
        $t = $l.Trim()
        if ($t.StartsWith($nombre + '=')) { return $t.Substring($nombre.Length + 1).Trim().Trim('"').Trim("'") }
    }
    return ''
}

$ssh = $RutaSsh
if (-not $ssh) {
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if (-not $ssh -and (Test-Path 'C:\Program Files\Git\usr\bin\ssh.exe')) { $ssh = 'C:\Program Files\Git\usr\bin\ssh.exe' }
}
if (-not $ssh) { throw 'No encuentro ssh.exe (instala el cliente OpenSSH de Windows o Git for Windows).' }
if (-not (Test-Path $LlaveSsh)) { throw "Falta la llave SSH del servidor: $LlaveSsh" }
$con = New-SshConexion -Ssh $ssh -Llave $LlaveSsh -Servidor $Vps -Segundos $SegundosSsh

# --- el servidor: tiene ya una llave?, esta desplegado el codigo de la Fabrica?, esta encendido su reloj?
$remotoEstado = "stat -c '%U:%G %a %s bytes' $Archivo 2>/dev/null || echo no-existe; test -f /opt/broll-tiktok/scripts/fabrica_broll.py && echo codigo-ok || echo codigo-falta; " + 'echo "reloj: $(systemctl is-enabled fabrica-broll.timer 2>/dev/null || true)"'
$r = Invoke-Ssh $con $remotoEstado -Que 'miro el estado de la Fabrica en el servidor'
if (-not $r.Ok) { throw ((Get-FalloSsh $r) + ' ' + $AVISO_SIN_GUARDAR) }
Write-Output ("Servidor: $Archivo = " + ($r.Salida -join ' | '))
if (-not (@($r.Salida) -contains 'codigo-ok')) {
    throw 'El servidor todavia no tiene el codigo de la Fabrica: primero hay que desplegarlo (bash editor-anuncios/vps/desplegar-vps.sh, lo hace Claude). No se guardo nada.'
}
if ($Probar) { Write-Output 'Solo se probo: no se leyo la llave ni se escribio nada.'; return }

# --- la llave, del .env del editor (nunca se imprime)
$envPath = Join-Path $Editor '.env'
if (-not (Test-Path $envPath)) { throw "No encuentro $envPath. Pasa -Editor con la carpeta del editor de anuncios (la que tiene el archivo .env). No se guardo nada." }
$textoEnv = [IO.File]::ReadAllText($envPath, [Text.Encoding]::UTF8)
$url = (Leer-Linea $textoEnv 'DASH_URL').TrimEnd('/')
$llave = Leer-Linea $textoEnv 'DASH_CLAVE_EDITOR'
$textoEnv = $null
if (-not $url) { $url = 'https://tu_proyecto.pages.dev' }
if ($llave.Length -lt 16) {
    throw "El .env del editor ($envPath) no tiene DASH_CLAVE_EDITOR. Corre primero  & .\scripts\Setup-ClaveEditor.ps1  (crea la llave y la pone ahi). No se guardo nada."
}
if ($llave.Length -gt 200 -or $llave -notmatch '^[A-Za-z0-9._\-]+$') {
    throw 'DASH_CLAVE_EDITOR del .env no tiene la forma de una llave (de 16 a 200 caracteres, solo letras, numeros, punto, guion y guion bajo, sin espacios). No se guardo nada.'
}
if ($url -notmatch '^https://[A-Za-z0-9.\-]+(:\d+)?$') {
    throw 'DASH_URL del .env debe ser la direccion del dash con https:// y sin ruta (por ejemplo https://tu_proyecto.pages.dev). No se guardo nada.'
}

# La llave entra por la entrada estandar de SSH (nunca va en la linea de comandos), como bytes ASCII y con un salto de linea por renglon. tr quita un CR o una marca BOM por si acaso.
# Se guarda en un archivo .tmp y se mueve: repetible sin dano.
$remotoGuardar = "umask 077; install -d -m 0750 -o root -g broll /opt/broll-tiktok/secretos && tr -d '\r\357\273\277' > $Archivo.tmp && chown root:broll $Archivo.tmp && chmod 640 $Archivo.tmp && mv $Archivo.tmp $Archivo && stat -c '%U:%G %a %s bytes' $Archivo"
$r = Invoke-Ssh $con $remotoGuardar -Entrada ("DASH_URL=$url`nDASH_CLAVE_EDITOR=$llave`n") -Que 'guardo la llave en el servidor'
$llave = $null
if (-not $r.Ok) {
    throw ('No pude guardar la llave en el servidor. ' + (Get-FalloSsh $r) + ' La llave NO quedo guardada en ninguna parte: abre una pestana NUEVA del Terminal y vuelve a correr el script.')
}
Write-Output ("Servidor: $Archivo = " + ($r.Salida -join ' '))

# --- prueba: el servidor lee la cola del dash con esa llave (solo lectura)
Write-Output ''
Write-Output 'El servidor lee la cola de la Fabrica con esa llave (solo lectura; si no contesta, NO se repite sola):'
$r = Invoke-Ssh $con 'cd /opt/broll-tiktok && runuser -u broll -- /opt/broll-tiktok/venv/bin/python scripts/fabrica_broll.py vigilar --solo-cola' -Que 'el servidor prueba la llave' -Segundos $SegundosPrueba -Intentos 1
foreach ($linea in @($r.Salida) + @($r.Error)) { Write-Output ([string]$linea) }
if (-not $r.Ok) {
    throw ('La llave quedo guardada, pero la prueba no salio bien (' + (Get-FalloSsh $r) + ') Revisa lo que dijo el servidor arriba; si dice que el dash rechazo la llave, corre  & .\scripts\Setup-ClaveEditor.ps1 -Probar  para ver si Cloudflare ya la tiene (se aplica en el siguiente despliegue). No se encendio el reloj.')
}

# --- encender la vigilancia
if ($SinActivar) { Write-Output 'Listo (sin encender el reloj: usaste -SinActivar). Para encenderlo: systemctl enable --now fabrica-broll.timer en el servidor.'; return }
$r = Invoke-Ssh $con 'systemctl enable --now fabrica-broll.timer && systemctl list-timers fabrica-broll.timer --no-pager | head -3' -Que 'enciendo el reloj de la Fabrica'
foreach ($linea in @($r.Salida)) { Write-Output ([string]$linea) }
if (-not $r.Ok) { throw ('La llave quedo guardada y probada, pero no pude encender el reloj. ' + (Get-FalloSsh $r)) }
Write-Output ''
Write-Output 'Listo. Desde ahora, cuando una oferta pase a la Fabrica, el servidor (en menos de una hora) crea su carpeta en Drive, estudia los anuncios originales mas antiguos y trae b-rolls.'
Write-Output 'Para quitar la llave del servidor: borra /opt/broll-tiktok/secretos/dash.env y apaga el reloj (systemctl disable --now fabrica-broll.timer).'
