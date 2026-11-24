# Una sola vez: le da al VPS el permiso de Google Drive que necesita el b-roll de TikTok para dejar los videos en la carpeta B-rolls de cada oferta.
#
#   1. abre el navegador en la pagina de Google donde TU aceptas el permiso (con la misma aplicacion de Google del radar de ofertas)
#   2. comprueba con ese permiso que se ve la cuenta y las carpetas de las ofertas (/opt/broll-tiktok/ofertas.json) y que se puede escribir en ellas
#   3. solo si todo eso salio bien, guarda el permiso en el servidor: /opt/broll-tiktok/secretos/google.json (dueno root, grupo broll, permisos 640), por SSH con la llave radar_vps
#   4. le pide al servidor que lo pruebe
#
# El permiso NO se imprime ni se guarda en ningun archivo de esta PC: pasa de Google a la memoria de este script y de ahi, por la entrada estandar de SSH, al servidor.
# Tampoco se lee el archivo client_secret del radar en el chat: lo lee este script, en tu PC.
#
# Que permiso es: "Ver, editar, crear y borrar todos tus archivos de Google Drive" (https://www.googleapis.com/auth/drive). Hace falta el completo porque los videos van a carpetas que
# YA existen (el permiso mas chico, drive.file, solo ve lo que crea la propia aplicacion). El codigo del servidor solo escribe dentro de B-rolls y no tiene ninguna funcion de borrar
# (ver scripts/drive_api.py), pero el permiso en si es amplio: tratalo como una llave. Para quitarlo: https://myaccount.google.com/permissions (y borra /opt/broll-tiktok/secretos/google.json).
#
#   & .\scripts\Setup-DriveBroll.ps1             # abre Google, acepta, y deja el permiso en el servidor
#   & .\scripts\Setup-DriveBroll.ps1 -Probar     # solo mira (cliente de Google, SSH y estado del servidor): no abre Google ni escribe nada
#   & .\scripts\Setup-DriveBroll.ps1 -SinNavegador   # en vez de abrir el navegador, escribe el enlace para que lo abras tu
#
# Si Google dice que la API de Drive esta apagada, el script no guarda nada y te da el enlace para encenderla (un clic, gratis); despues lo vuelves a correr.
# Cada llamada al servidor (ssh) tiene tiempo maximo (-SegundosSsh, 40 por defecto; la prueba final del servidor, -SegundosPrueba, 90) y se reintenta una vez; antes de cada una el script dice "Conectando con el servidor...".
# Si el servidor no contesta, el script se detiene con un mensaje claro y SIN guardar nada: abre una pestana NUEVA del Terminal y vuelve a correrlo (ver scripts\_ssh-seguro.ps1).
# Se llama con & (no con "powershell -File"): ver la nota de Agregar-Variable.ps1.
# -UrlGoogle, -ArchivoLocal y -RutaSsh son solo para probar el script en esta misma maquina contra un Google y un ssh de mentira (scripts/probar-setup-drive-broll.cjs).
# ASCII-only, PS 5.1 safe.
param(
    [string]$CarpetaRadar = (Join-Path $HOME 'radar-de-ofertas'),
    [string]$Vps = 'root@203.0.113.10',
    [string]$LlaveSsh = (Join-Path $HOME '.ssh\radar_vps'),
    [string]$Archivo = '/opt/broll-tiktok/secretos/google.json',
    [switch]$Probar,
    [switch]$SinNavegador,
    [int]$Espera = 300,
    [string[]]$CarpetaId = @(),
    [int]$SegundosSsh = 40,
    [int]$SegundosPrueba = 90,
    [string]$UrlGoogle = '',
    [string]$ArchivoLocal = '',
    [string]$RutaSsh = ''
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ayudanteSsh = Join-Path $PSScriptRoot '_ssh-seguro.ps1'
if (-not (Test-Path $ayudanteSsh)) { throw "Falta $ayudanteSsh (esta en la misma carpeta que este script; actualiza el repositorio con git pull)." }
. $ayudanteSsh
$AVISO_SIN_GUARDAR = 'No se guardo nada. Cierra esta ventana, abre una pestana NUEVA del Terminal y vuelve a correr el mismo comando.'

$ALCANCE = 'https://www.googleapis.com/auth/drive'
$urlAutorizar = 'https://accounts.google.com/o/oauth2/v2/auth'
$urlToken = 'https://oauth2.googleapis.com/token'
$urlApi = 'https://www.googleapis.com/drive/v3'
$prueba = $false
if ($UrlGoogle) {
    if ($UrlGoogle -notmatch '^http://127\.0\.0\.1:\d+$') { throw '-UrlGoogle solo acepta http://127.0.0.1:PUERTO (es para probar el script en esta maquina).' }
    $urlAutorizar = "$UrlGoogle/auth"; $urlToken = "$UrlGoogle/token"; $urlApi = "$UrlGoogle/drive/v3"; $prueba = $true
}

function Lit([string]$s) {
    $s = $s -replace '\\','\\'; $s = $s -replace '"','\"'; $s = $s -replace "`r",'\r'; $s = $s -replace "`n",'\n'; $s = $s -replace "`t",'\t'
    return '"' + $s + '"'
}
function B64Url([byte[]]$b) { return [Convert]::ToBase64String($b).TrimEnd('=').Replace('+','-').Replace('/','_') }
function Azar([int]$n) { $b = New-Object byte[] $n; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); return $b }
function Cuerpo-Error($e) {
    # El cuerpo de una respuesta de error de Google (solo trae el motivo, nunca las llaves que mandamos)
    try { $r = $e.Exception.Response; if ($r) { $sr = New-Object IO.StreamReader($r.GetResponseStream()); return $sr.ReadToEnd() } } catch { }
    return [string]$e.Exception.Message
}

# --- 1. el cliente de Google del radar (lo lee este script; nunca se muestra)
$archivos = @(Get-ChildItem -Path $CarpetaRadar -Filter 'client_secret*.json' -ErrorAction SilentlyContinue)
if (-not $archivos.Count) { throw "No encuentro client_secret*.json en $CarpetaRadar (es el de la aplicacion de Google del radar; si esta en otra carpeta pasa -CarpetaRadar)." }
$cfg = Get-Content $archivos[0].FullName -Raw | ConvertFrom-Json
$c = if ($cfg.installed) { $cfg.installed } elseif ($cfg.web) { $cfg.web } else { $null }
if (-not $c -or -not $c.client_id -or -not $c.client_secret) { throw 'El client_secret del radar no tiene el formato esperado (clave installed o web con client_id y client_secret).' }
$cid = [string]$c.client_id; $csec = [string]$c.client_secret
$proyecto = ($cid -split '-')[0]
Write-Output "Aplicacion de Google del radar: proyecto $proyecto."

# --- 2. el servidor (SSH con la llave radar_vps; ssh.exe de Windows o el de Git)
$ssh = $null; $con = $null
if (-not $ArchivoLocal) {
    $ssh = $RutaSsh
    if (-not $ssh) {
        $ssh = Get-Command ssh -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
        if (-not $ssh -and (Test-Path 'C:\Program Files\Git\usr\bin\ssh.exe')) { $ssh = 'C:\Program Files\Git\usr\bin\ssh.exe' }
    }
    if (-not $ssh) { throw 'No encuentro ssh.exe (instala el cliente OpenSSH de Windows o Git for Windows).' }
    if (-not (Test-Path $LlaveSsh)) { throw "Falta la llave SSH del servidor: $LlaveSsh" }
    $con = New-SshConexion -Ssh $ssh -Llave $LlaveSsh -Servidor $Vps -Segundos $SegundosSsh
    $r = Invoke-Ssh $con "stat -c '%U:%G %a %s bytes' $Archivo 2>/dev/null || echo no-existe" -Que 'miro si ya hay un permiso guardado'
    if (-not $r.Ok) { throw ((Get-FalloSsh $r) + ' ' + $AVISO_SIN_GUARDAR) }
    Write-Output ("Servidor: $Archivo = " + ($r.Salida -join ' '))
    $r = Invoke-Ssh $con 'cat /opt/broll-tiktok/ofertas.json 2>/dev/null' -Que 'leo las carpetas de Drive de las ofertas'
    if ($r.SinConexion) { throw ((Get-FalloSsh $r) + ' ' + $AVISO_SIN_GUARDAR) }
    if (-not $r.Ok -or -not $r.Salida.Count) { throw 'El servidor no tiene /opt/broll-tiktok/ofertas.json: falta correr desplegar-vps.sh (lo hace Claude).' }
    $ofertas = @(($r.Salida -join "`n" | ConvertFrom-Json).ofertas)
} else {
    $ofertas = @()
}
$carpetas = @()
foreach ($o in $ofertas) {
    if ($o.drive -and ([string]$o.drive) -match '/folders/([A-Za-z0-9_-]{15,100})') { $carpetas += [pscustomobject]@{ Oferta = [string]$o.producto; Id = $Matches[1] } }
}
foreach ($extra in $CarpetaId) { $carpetas += [pscustomobject]@{ Oferta = 'carpeta-pedida'; Id = $extra } }
Write-Output ("Carpetas de Drive por comprobar: " + $carpetas.Count)
if ($Probar) { Write-Output 'Solo se probo: no se abrio Google ni se escribio nada.'; return }

# --- 3. el permiso: Google te pregunta, tu aceptas (PKCE + escucha en 127.0.0.1; sin librerias)
$verificador = B64Url (Azar 48)
$reto = B64Url ([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::ASCII.GetBytes($verificador)))
$estado = B64Url (Azar 16)
$escucha = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
$escucha.Start()
$puerto = ([System.Net.IPEndPoint]$escucha.LocalEndpoint).Port
$redirige = "http://127.0.0.1:$puerto"
$url = $urlAutorizar + '?client_id=' + [Uri]::EscapeDataString($cid) + '&redirect_uri=' + [Uri]::EscapeDataString($redirige) + '&response_type=code' +
    '&scope=' + [Uri]::EscapeDataString($ALCANCE) + '&code_challenge=' + $reto + '&code_challenge_method=S256&state=' + $estado + '&access_type=offline&prompt=consent'
Write-Output ''
Write-Output 'Elige la cuenta de Google que es DUENA de las carpetas de Drive de las ofertas (la de MI NEGOCIO) y acepta el permiso.'
if ($SinNavegador) { Write-Output "Abre este enlace en tu navegador:`n$url" } else { Write-Output 'Abriendo el navegador...'; Start-Process $url }
$q = @{}
$limite = (Get-Date).AddSeconds($Espera)
try {
    while (-not $q.ContainsKey('code') -and -not $q.ContainsKey('error') -and (Get-Date) -lt $limite) {
        if (-not $escucha.Pending()) { Start-Sleep -Milliseconds 200; continue }
        $cl = $escucha.AcceptTcpClient()
        try {
            $cl.ReceiveTimeout = 5000
            $st = $cl.GetStream()
            $buf = New-Object byte[] 8192
            $n = $st.Read($buf, 0, $buf.Length)
            $linea = ([Text.Encoding]::ASCII.GetString($buf, 0, $n) -split "`r?`n")[0]
            $respuesta = 'HTTP/1.1 404 Not Found'; $html = ''
            if ($linea -match '^GET /\?(\S*) HTTP') {
                $tmp = @{}
                foreach ($par in $Matches[1].Split('&')) { $kv = $par.Split('=', 2); if ($kv.Count -eq 2) { $tmp[$kv[0]] = [Uri]::UnescapeDataString($kv[1].Replace('+', ' ')) } }
                if ($tmp.ContainsKey('code') -or $tmp.ContainsKey('error')) {
                    $q = $tmp
                    $respuesta = 'HTTP/1.1 200 OK'
                    $html = '<html><head><meta charset="utf-8"></head><body><h3>Listo. Ya puedes cerrar esta pestana y volver a la terminal.</h3></body></html>'
                }
            }
            $cuerpoHtml = [Text.Encoding]::UTF8.GetBytes($html)
            $cab = [Text.Encoding]::ASCII.GetBytes($respuesta + "`r`nContent-Type: text/html; charset=utf-8`r`nContent-Length: " + $cuerpoHtml.Length + "`r`nConnection: close`r`n`r`n")
            $st.Write($cab, 0, $cab.Length); if ($cuerpoHtml.Length) { $st.Write($cuerpoHtml, 0, $cuerpoHtml.Length) }
        } finally { $cl.Close() }
    }
} finally { $escucha.Stop() }
if ($q.ContainsKey('error')) { throw ('Google devolvio: ' + $q['error'] + ' (si dice access_denied, no aceptaste el permiso).') }
if (-not $q.ContainsKey('code')) { throw "No llego la respuesta de Google en $Espera segundos. Vuelve a correr el script." }
if ($q['state'] -ne $estado) { throw 'La respuesta de Google no coincide con la solicitud (state). Vuelve a correr el script.' }

try {
    $tok = Invoke-RestMethod -Uri $urlToken -Method Post -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 30 -Body @{
        code = $q['code']; client_id = $cid; client_secret = $csec; code_verifier = $verificador; redirect_uri = $redirige; grant_type = 'authorization_code' }
} catch { throw ('Google no entrego el permiso: ' + (Cuerpo-Error $_)) }
$q = $null; $verificador = $null
if (-not $tok.refresh_token) { throw 'Google no entrego el permiso de largo plazo (refresh_token). Entra a https://myaccount.google.com/permissions, quita la aplicacion del radar y vuelve a correr el script.' }
if (([string]$tok.scope -split ' ') -notcontains $ALCANCE) { throw "Aceptaste menos de lo pedido (Google dio: $($tok.scope)). Vuelve a correr el script y deja marcada la casilla de Google Drive." }
Write-Output 'Permiso recibido. Comprobando con el que se ve en Drive...'

# --- 4. lo comprueba ANTES de guardarlo en el servidor
$h = @{ Authorization = 'Bearer ' + [string]$tok.access_token }
try { $yo = Invoke-RestMethod -Uri ($urlApi + '/about?fields=' + [Uri]::EscapeDataString('user(displayName,emailAddress)')) -Headers $h -TimeoutSec 30 }
catch {
    $cuerpo = Cuerpo-Error $_
    if ($cuerpo -match 'accessNotConfigured|SERVICE_DISABLED|has not been used|is disabled') {
        throw ("La API de Google Drive esta APAGADA en el proyecto $proyecto de Google Cloud. Enciendela (un clic, gratis) en https://console.cloud.google.com/apis/library/drive.googleapis.com?project=$proyecto " +
               'y vuelve a correr este script. No se guardo nada en el servidor.')
    }
    throw ('Drive no contesto como se esperaba: ' + $cuerpo)
}
Write-Output ("Cuenta de Google: " + $yo.user.emailAddress)
$malas = 0
foreach ($f in $carpetas) {
    try {
        $m = Invoke-RestMethod -Uri ($urlApi + '/files/' + $f.Id + '?supportsAllDrives=true&fields=' + [Uri]::EscapeDataString('name,mimeType,trashed,capabilities(canAddChildren)')) -Headers $h -TimeoutSec 30
        if ($m.mimeType -ne 'application/vnd.google-apps.folder' -or $m.trashed) { Write-Output ("  x " + $f.Oferta + ": no es una carpeta valida"); $malas++ }
        elseif (-not $m.capabilities.canAddChildren) { Write-Output ("  x " + $f.Oferta + ": '" + $m.name + "' es de SOLO LECTURA para esta cuenta"); $malas++ }
        else { Write-Output ("  ok " + $f.Oferta + ": carpeta '" + $m.name + "' (se puede escribir)") }
    } catch {
        $msg = ((Cuerpo-Error $_) -replace '\s+', ' ')
        if ($msg.Length -gt 120) { $msg = $msg.Substring(0, 120) }
        Write-Output ("  x " + $f.Oferta + ": esta cuenta no ve esa carpeta (" + $msg + ")"); $malas++
    }
}
if ($malas) { throw "Hay $malas carpeta(s) que esta cuenta no puede usar. Elige otra cuenta (la duena de las carpetas) y vuelve a correr el script. No se guardo nada en el servidor." }

# --- 5. se guarda en el servidor (por la entrada estandar de SSH; nada va en la linea de comandos)
$json = '{"client_id":' + (Lit $cid) + ',"client_secret":' + (Lit $csec) + ',"refresh_token":' + (Lit ([string]$tok.refresh_token)) + ',"scope":' + (Lit $ALCANCE) +
        ',"autorizado_por":' + (Lit ([string]$yo.user.emailAddress)) + ',"autorizado_en":' + (Lit (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')) + '}'
$tok = $null; $h = $null; $csec = $null
if ($ArchivoLocal) {
    [IO.File]::WriteAllText($ArchivoLocal, $json, (New-Object Text.UTF8Encoding($false)))
    Write-Output "PRUEBA: permiso escrito en $ArchivoLocal"
} else {
    # El JSON va como bytes ASCII (todo el JSON lo es) por una tuberia que se cierra: sin marca BOM ni CR. Por si acaso, tr quita el CR y los tres bytes del BOM.
    # Guardar es repetible sin dano (archivo .tmp y mv): si el servidor no contesta a tiempo, Invoke-Ssh lo vuelve a intentar una vez.
    $r = Invoke-Ssh $con "umask 077; tr -d '\r\357\273\277' > $Archivo.tmp && chown root:broll $Archivo.tmp && chmod 640 $Archivo.tmp && mv $Archivo.tmp $Archivo && stat -c '%U:%G %a %s bytes' $Archivo" -Entrada $json -Que 'guardo el permiso en el servidor'
    $json = $null
    if (-not $r.Ok) {
        throw ('No pude guardar el permiso en el servidor. ' + (Get-FalloSsh $r) + ' El permiso NO quedo guardado en ninguna parte (ni aqui ni en el servidor): abre una pestana NUEVA del Terminal y vuelve a correr el script; Google te lo pedira otra vez.')
    }
    Write-Output ("Servidor: $Archivo = " + ($r.Salida -join ' '))
    Write-Output ''
    Write-Output 'El servidor prueba el permiso (renueva, mira las carpetas):'
    $r = Invoke-Ssh $con 'runuser -u broll -- /opt/broll-tiktok/venv/bin/python /opt/broll-tiktok/scripts/drive_api.py probar' -Que 'el servidor prueba el permiso' -Segundos $SegundosPrueba -Intentos 1
    foreach ($linea in @($r.Salida) + @($r.Error)) { Write-Output ([string]$linea) }
    if (-not $r.Ok) { Write-Output ('AVISO: la prueba del servidor no salio bien (' + (Get-FalloSsh $r) + ') Avisale a Claude lo que dice arriba. El permiso SI quedo guardado.') }
}
$json = $null; $cid = $null
Write-Output ''
Write-Output 'Listo. El permiso quedo en el servidor. Para quitarlo: https://myaccount.google.com/permissions (y borra /opt/broll-tiktok/secretos/google.json).'
