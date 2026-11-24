# Agrega o cambia UNA variable de entorno del proyecto en Cloudflare Pages.
#
# Setup-Cloudflare.ps1 tambien las escribe, pero hace muchas cosas mas -crea el proyecto,
# reescribe los tokens de Facebook, toca las contrasenas de cada rol- y exige tener
# data/facebook-tokens.json a mano. Para poner una sola variable eso es demasiado alcance:
# cada cosa que el script toca de paso es una cosa que puede quedar mal.
#
# El PATCH de Cloudflare REEMPLAZA el bloque entero de variables, asi que lo primero que
# hace esto es leer las que ya hay y reenviarlas. Sin ese paso, agregar una variable borra
# en silencio las contrasenas de los roles y deja a medio equipo fuera del tablero.
#
#   & scripts\Agregar-Variable.ps1 -Nombre ZAPDATA_WEBHOOK_URL
#   & scripts\Agregar-Variable.ps1 -Nombre ZAPDATA_WEBHOOK_URL -Valor 'https://...'
#
# Sin -Valor la pide tecleada y oculta, que es como se meten los secretos sin que queden
# escritos en el historial de la consola.
#
# Cloudflare aplica las variables en el SIGUIENTE despliegue, no en el que ya esta arriba.
# ASCII-only, PS 5.1 safe.
param(
    [Parameter(Mandatory=$true)][string]$Nombre,
    [string]$Valor = "",
    [string]$Root = (Split-Path $PSScriptRoot),
    # Borrar la variable en vez de ponerla.
    [switch]$Quitar
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($Nombre -notmatch '^[A-Z][A-Z0-9_]*$') {
    throw "Nombre invalido: '$Nombre'. Las variables van en MAYUSCULAS con guion bajo."
}

$cfgPath = Join-Path $Root 'data/cloudflare-config.json'
if (-not (Test-Path $cfgPath)) { throw "Falta $cfgPath" }
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$headers = @{ Authorization = "Bearer $($cfg.apiToken)" }
$api = "https://api.cloudflare.com/client/v4/accounts/$($cfg.accountId)/pages/projects/$($cfg.projectName)"

if (-not $Quitar -and -not $Valor) {
    $sec = Read-Host "Valor de $Nombre" -AsSecureString
    $Valor = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    if (-not $Valor) { throw "Valor vacio: no se cambia nada." }
}

# Las 1,5 KB de los tokens de Facebook reventaban ConvertTo-Json de PS 5.1 -crecian a 500 KB
# y Cloudflare las rechazaba-, asi que el cuerpo se arma escapando a mano.
function ConvertTo-JsonStringLiteral([string]$s) {
    $s = $s -replace '\\','\\'; $s = $s -replace '"','\"'
    $s = $s -replace "`r",'\r'; $s = $s -replace "`n",'\n'; $s = $s -replace "`t",'\t'
    return '"' + $s + '"'
}

$vars = [ordered]@{}
$actual = Invoke-RestMethod $api -Headers $headers -TimeoutSec 30
$ev = $actual.result.deployment_configs.production.env_vars
if ($ev) { foreach ($p in $ev.PSObject.Properties) { $vars[$p.Name] = [string]$p.Value.value } }
if (-not $vars.Count) {
    throw "Cloudflare no devolvio ninguna variable. Mandar el PATCH ahora las borraria todas: revisa el token y la cuenta antes de insistir."
}
Write-Output ("Ya habia " + $vars.Count + " variables; se conservan.")

$antes = $vars.Contains($Nombre)
if ($Quitar) {
    if (-not $antes) { Write-Output "$Nombre no estaba puesta. No hay nada que quitar."; exit 0 }
    $vars.Remove($Nombre)
} else {
    $vars[$Nombre] = $Valor
}

$pairs = @()
foreach ($k in $vars.Keys) {
    $pairs += '"' + $k + '":{"type":"plain_text","value":' + (ConvertTo-JsonStringLiteral ([string]$vars[$k])) + '}'
}
$patch = '{"deployment_configs":{"production":{"env_vars":{' + ($pairs -join ',') + '}}}}'
Invoke-RestMethod $api -Method Patch -Headers $headers -ContentType 'application/json' -Body $patch -TimeoutSec 30 | Out-Null

# El valor NO se imprime, ni siquiera recortado: estas variables son contrasenas, llaves de
# firma y direcciones con clave incluida. Se dice el largo, que es lo unico que hace falta
# para saber que llego completa.
if ($Quitar) { Write-Output "$Nombre borrada." }
else { Write-Output ("$Nombre " + $(if ($antes) { 'actualizada' } else { 'agregada' }) + " (largo " + $Valor.Length + ").") }
Write-Output "Toma efecto en el proximo despliegue."
