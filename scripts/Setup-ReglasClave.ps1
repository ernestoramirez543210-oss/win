# Una sola vez: crea la llave con la que el refresco de GitHub lee las reglas Y las ejecuta (scripts/reglas-auto.cjs -> /api/reglas-ejecutar).
#
#   - la genera al azar (32 bytes)
#   - la agrega a Cloudflare Pages como CLAVE_REGLAS, conservando TODAS las variables que ya hay
#   - la guarda en GitHub como el secreto REGLAS_CLAVE
#
# La llave no se imprime ni se guarda en ningun archivo. Sirve para LEER /api/reglas (GET) y, desde el 2026-10-13, para que el refresco APLIQUE en Facebook lo que decidan las reglas
# encendidas (/api/reglas-ejecutar, que ademas comprueba cada accion contra las reglas guardadas y contra Facebook): tratala como un secreto. No da acceso a otra ruta. Si ya existe CLAVE_REGLAS en Pages no hace nada (para rotarla, borra la
# variable en Cloudflare y vuelve a correr esto).
#
#   powershell -ExecutionPolicy Bypass -File scripts\Setup-ReglasClave.ps1
#
# Despues de correrlo, la llave de Pages se aplica en el siguiente deploy (lanza un refresco).
param([string]$Root = (Split-Path $PSScriptRoot), [string]$Repo = 'TU-USUARIO/TU-REPO')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Lit([string]$s) {
    $s = $s -replace '\\','\\'; $s = $s -replace '"','\"'; $s = $s -replace "`r",'\r'; $s = $s -replace "`n",'\n'; $s = $s -replace "`t",'\t'
    return '"' + $s + '"'
}
$cfg = Get-Content (Join-Path $Root 'data/cloudflare-config.json') -Raw | ConvertFrom-Json
$h = @{ Authorization = "Bearer $($cfg.apiToken)" }
$api = "https://api.cloudflare.com/client/v4/accounts/$($cfg.accountId)/pages/projects/$($cfg.projectName)"

$r = Invoke-RestMethod $api -Headers $h -TimeoutSec 30
$vars = [ordered]@{}
foreach ($p in $r.result.deployment_configs.production.env_vars.PSObject.Properties) { $vars[$p.Name] = [string]$p.Value.value }
if ($vars.Contains('CLAVE_REGLAS')) { Write-Output 'CLAVE_REGLAS ya existe en Pages: no se toca nada.'; return }

$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$llave = ([Convert]::ToBase64String($bytes)).TrimEnd('=').Replace('+','-').Replace('/','_')
$antes = $vars.Count
$vars['CLAVE_REGLAS'] = $llave

# Se reenvian todas las variables: el PATCH reemplaza el bloque completo (ver Setup-Cloudflare.ps1).
$pares = @(); foreach ($k in $vars.Keys) { $pares += '"' + $k + '":{"type":"plain_text","value":' + (Lit ([string]$vars[$k])) + '}' }
$patch = '{"deployment_configs":{"production":{"env_vars":{' + ($pares -join ',') + '}}}}'
Invoke-RestMethod $api -Method Patch -Headers $h -ContentType 'application/json' -Body $patch -TimeoutSec 30 | Out-Null
$despues = (Invoke-RestMethod $api -Headers $h -TimeoutSec 30).result.deployment_configs.production.env_vars.PSObject.Properties.Name.Count
Write-Output "Cloudflare Pages: $antes variables antes, $despues despues (CLAVE_REGLAS agregada)."

# GitHub: si gh no tiene sesion, se usa la credencial que git ya guarda.
if (-not $env:GH_TOKEN) {
    $cred = "protocol=https`nhost=github.com`n`n" | git credential fill
    $env:GH_TOKEN = (($cred | Where-Object { $_ -like 'password=*' }) -replace '^password=','')
}
$llave | gh secret set REGLAS_CLAVE --repo $Repo
Write-Output ('GitHub: ' + ((gh secret list --repo $Repo | Select-String 'REGLAS_CLAVE') -replace '\s+',' '))
Write-Output 'Listo. Lanza un refresco (gh workflow run refresh.yml) para que Pages aplique la variable nueva.'
