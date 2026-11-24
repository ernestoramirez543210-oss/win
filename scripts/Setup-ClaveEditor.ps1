# Una sola vez: crea la llave con la que el editor de anuncios (el PC con Claude) avisa al dash lo que gasta en APIMart, para verlo en la pagina "Costo por proyecto".
#
#   - la genera al azar (32 bytes)
#   - la agrega a Cloudflare Pages como CLAVE_EDITOR, conservando TODAS las variables que ya hay (el PATCH reemplaza el bloque completo)
#   - la escribe en el .env del editor (D:\editor-anuncios\.env) como DASH_CLAVE_EDITOR, junto con DASH_URL; el resto del .env (la clave de APIMart) no se toca
#
# La llave NO se imprime. Sirve SOLO para anotar costos nuevos en /api/editor-costos: no lee nada del dash ni borra nada. Tratala como un secreto de todos modos (el .env ya guarda la
# clave de APIMart y es privado). Si CLAVE_EDITOR ya existe en Pages no se cambia: se copia la misma al .env (solo si ahi falta), a menos que pases -Rotar.
#
#   & .\scripts\Setup-ClaveEditor.ps1                          # crea la llave y la pone en los dos lados
#   & .\scripts\Setup-ClaveEditor.ps1 -Probar                  # solo comprueba (Cloudflare y el .env) sin escribir nada
#   & .\scripts\Setup-ClaveEditor.ps1 -Editor C:\otra\carpeta  # si el editor no esta en D:\editor-anuncios
#
# Se llama con & (no con "powershell -File"): ver la nota de Agregar-Variable.ps1. Si corres esto desde un worktree, pasa -Root con la carpeta del proyecto que tiene data\cloudflare-config.json.
# Despues de correrlo, Cloudflare aplica la variable en el SIGUIENTE despliegue (el refresco, cada tres horas, o Run workflow en GitHub). Hasta entonces el dash contesta 503 y el
# editor lo dice en una linea sin parar nada; cuando el despliegue sale, los costos llegan solos. Para mandar lo que ya se gasto: python scripts/apimart.py enviar-costos
# -SinCloudflare es solo para probar el script: usa una llave de mentira y no llama a Cloudflare (la prueba lo usa con una carpeta temporal).
# ASCII-only, PS 5.1 safe.
param(
    [string]$Root = (Split-Path $PSScriptRoot),
    [string]$Editor = 'D:\editor-anuncios',
    [string]$Url = 'https://tu_proyecto.pages.dev',
    [switch]$Rotar,
    [switch]$Probar,
    [switch]$SinCloudflare
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Lit([string]$s) {
    $s = $s -replace '\\','\\'; $s = $s -replace '"','\"'; $s = $s -replace "`r",'\r'; $s = $s -replace "`n",'\n'; $s = $s -replace "`t",'\t'
    return '"' + $s + '"'
}

# Pone NOMBRE=valor en el texto de un .env: reemplaza la linea que EMPIEZA con NOMBRE= (los comentarios "# NOMBRE=" no cuentan) o agrega una al final. Respeta el fin de linea que ya tenia.
function Poner-Linea([string]$texto, [string]$nombre, [string]$valor) {
    $nl = if ($texto.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lineas = New-Object System.Collections.Generic.List[string]
    foreach ($l in ($texto -split "`r?`n")) { $lineas.Add($l) }
    while ($lineas.Count -gt 0 -and $lineas[$lineas.Count - 1] -eq '') { $lineas.RemoveAt($lineas.Count - 1) }
    $puesta = $false
    for ($i = 0; $i -lt $lineas.Count; $i++) {
        if ($lineas[$i].TrimStart().StartsWith($nombre + '=')) { $lineas[$i] = $nombre + '=' + $valor; $puesta = $true }
    }
    if (-not $puesta) { $lineas.Add($nombre + '=' + $valor) }
    return ($lineas -join $nl) + $nl
}
# El valor actual de NOMBRE= en el texto de un .env ('' si no esta o esta vacio).
function Leer-Linea([string]$texto, [string]$nombre) {
    foreach ($l in ($texto -split "`r?`n")) {
        $t = $l.Trim()
        if ($t.StartsWith($nombre + '=')) { return $t.Substring($nombre.Length + 1).Trim().Trim('"').Trim("'") }
    }
    return ''
}

# --- el .env del editor
$envPath = Join-Path $Editor '.env'
if (-not (Test-Path $envPath)) { throw "No encuentro $envPath. Instala el editor primero (Instalar-Editor.cmd) o pasa -Editor con su carpeta." }
$textoEnv = [IO.File]::ReadAllText($envPath, [Text.Encoding]::UTF8)
$envUrl = Leer-Linea $textoEnv 'DASH_URL'
$envLlave = Leer-Linea $textoEnv 'DASH_CLAVE_EDITOR'
Write-Output ("Editor: $envPath; DASH_URL " + $(if ($envUrl) { 'puesta' } else { 'falta' }) + '; DASH_CLAVE_EDITOR ' + $(if ($envLlave.Length -ge 16) { 'puesta' } else { 'falta' }) + '.')

# --- Cloudflare: las variables que ya hay
$vars = [ordered]@{}
$existe = $false
if (-not $SinCloudflare) {
    $cfgPath = Join-Path $Root 'data/cloudflare-config.json'
    if (-not (Test-Path $cfgPath)) { throw "Falta $cfgPath (corre esto desde la carpeta del proyecto o pasa -Root)." }
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    $h = @{ Authorization = "Bearer $($cfg.apiToken)" }
    $api = "https://api.cloudflare.com/client/v4/accounts/$($cfg.accountId)/pages/projects/$($cfg.projectName)"
    $r = Invoke-RestMethod $api -Headers $h -TimeoutSec 30
    $ev = $r.result.deployment_configs.production.env_vars
    if ($ev) { foreach ($p in $ev.PSObject.Properties) { $vars[$p.Name] = [string]$p.Value.value } }
    if (-not $vars.Count) { throw 'Cloudflare no devolvio ninguna variable. Mandar el PATCH ahora las borraria todas: revisa el token y la cuenta antes de insistir.' }
    $existe = $vars.Contains('CLAVE_EDITOR') -and ([string]$vars['CLAVE_EDITOR']).Length -ge 16
    Write-Output ("Cloudflare Pages: " + $vars.Count + " variables; CLAVE_EDITOR " + $(if ($existe) { 'ya existe' } else { 'no existe todavia' }) + '.')
}
if ($Probar) { Write-Output 'Solo se probo: no se escribio nada.'; return }

# --- la llave: la de Cloudflare si ya existe (y no se pide rotar); si no, una al azar
$nueva = $false
if ($existe -and -not $Rotar) { $llave = [string]$vars['CLAVE_EDITOR'] }
else {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $llave = ([Convert]::ToBase64String($bytes)).TrimEnd('=').Replace('+','-').Replace('/','_')
    $nueva = $true
    if (-not $SinCloudflare) {
        $antes = $vars.Count
        $vars['CLAVE_EDITOR'] = $llave
        $pares = @(); foreach ($k in $vars.Keys) { $pares += '"' + $k + '":{"type":"plain_text","value":' + (Lit ([string]$vars[$k])) + '}' }
        $patch = '{"deployment_configs":{"production":{"env_vars":{' + ($pares -join ',') + '}}}}'
        Invoke-RestMethod $api -Method Patch -Headers $h -ContentType 'application/json' -Body $patch -TimeoutSec 30 | Out-Null
        $despues = (Invoke-RestMethod $api -Headers $h -TimeoutSec 30).result.deployment_configs.production.env_vars.PSObject.Properties.Name.Count
        Write-Output "Cloudflare Pages: $antes variables antes, $despues despues (CLAVE_EDITOR agregada)."
    }
}

# --- el .env del editor, solo si hace falta (llave nueva o rotada, o la direccion/llave faltan o son otras)
if ($nueva -or $envLlave -ne $llave -or $envUrl -ne $Url) {
    $nuevoTexto = Poner-Linea (Poner-Linea $textoEnv 'DASH_URL' $Url) 'DASH_CLAVE_EDITOR' $llave
    # UTF-8 sin BOM (el editor lee el .env con Python); solo se tocan las dos lineas DASH_, la clave de APIMart queda como estaba.
    [IO.File]::WriteAllText($envPath, $nuevoTexto, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "Editor: .env actualizado (DASH_URL y DASH_CLAVE_EDITOR)."
} else { Write-Output 'Editor: el .env ya tenia lo mismo; no se toca (usa -Rotar para cambiar la llave).' }
$llave = $null
Write-Output 'Listo. La variable de Cloudflare se aplica en el SIGUIENTE despliegue (el refresco, cada tres horas, o Run workflow en GitHub); despues los costos del editor llegan solos al dash.'
