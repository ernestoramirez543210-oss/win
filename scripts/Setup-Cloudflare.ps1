# One-time Cloudflare Pages setup: creates the Pages project (direct-upload) and stores
# the Facebook tokens + admin password as project env vars so the /api/toggle-ad function
# can read them. Run once from Windows PowerShell (pure HTTP API, no Node/wrangler needed).
#
# Reads data/cloudflare-config.json (gitignored):
#   { "apiToken": "...", "accountId": "...", "projectName": "mi-dashboard" }
# ASCII-only, PS 5.1 safe.
param(
    [string]$Root = (Split-Path $PSScriptRoot),
    # Contrasena de dueno. Opcional: si se omite, se conserva la que ya esta en Cloudflare.
    # Era obligatoria, y eso obligaba a volver a teclearla para algo tan pequeno como dar de
    # alta a un socio -mas manos sobre la clave del dueno, y el riesgo de sobrescribirla con
    # un dedazo y quedarse fuera-.
    [string]$Password = "",
    # Plantilla: mejor SIN -Password: si falta (o con -CambiarClave), el script la pide sin mostrarla y asi no queda en el historial de PowerShell.
    # Para las de rol y de pais, pon ? como valor (-TraffickerPassword ?  o  -ClavesPais @{ co = '?' }) y tambien te la pide.
    [switch]$CambiarClave,
    # Plantilla: -CerrarSesiones saca a todos los que ya entraron (las sesiones duran 30 dias). Pasa solo al cambiar cualquier contrasena.
    [switch]$CerrarSesiones,
    # Per-role passwords for _middleware.js. Left empty = that role simply cannot log in.
    [string]$TraffickerPassword = "",
    [string]$EditorPassword     = "",
    # El editor de VIDEO, aparte del editor de contenido: son dos personas y compartir una
    # contrasena obliga a cambiarsela a los dos cuando se va uno.
    [string]$VideosPassword     = "",
    # Un socio por pais: la clave es el codigo de pais en minusculas -mx, ve, co, pe, gt,
    # ar, ec- y se guarda como <CODIGO>_PASSWORD, que es lo que lee _middleware.js.
    # Ej: -ClavesPais @{ ve = '<una clave larga solo para Venezuela>' }. Los paises que no se nombren
    # conservan la contrasena que ya tenian.
    [hashtable]$ClavesPais      = @{},
    # HMAC key for session cookies. Auto-generated if omitted; pass a value to keep
    # existing sessions valid across re-runs (changing it logs everyone out).
    [string]$SessionSecret      = "",
    # Clave de SOLO LECTURA para /api/datos: la que se le da a un proyecto de afuera que
    # quiere consultar los numeros. Va aparte de SESSION_SECRET a proposito -esa firma las
    # cookies, y quien la tenga puede hacerse pasar por el dueno-. Vacio = se conserva la
    # que ya este puesta, o simplemente no existe y el endpoint contesta 503.
    [string]$ClaveDatos         = ""
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$cfg = Get-Content (Join-Path $Root 'data/cloudflare-config.json') -Raw | ConvertFrom-Json
$tokensJson = Get-Content (Join-Path $Root 'data/facebook-tokens.json') -Raw
$acct = $cfg.accountId
$proj = $cfg.projectName
$headers = @{ Authorization = "Bearer $($cfg.apiToken)" }
$api = "https://api.cloudflare.com/client/v4/accounts/$acct/pages/projects"

# 1. Create the project (idempotent -- ignore "already exists").
try {
    $body = @{ name = $proj; production_branch = 'main' } | ConvertTo-Json
    Invoke-RestMethod $api -Method Post -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 30 | Out-Null
    Write-Output "Project '$proj' created."
} catch {
    $msg = $_.ErrorDetails.Message
    if ($msg -and $msg -match 'already') { Write-Output "Project '$proj' already exists (ok)." }
    else { Write-Output "Create project note: $msg" }
}

# 2. Variables del proyecto (las leen las funciones del servidor; nunca llegan al navegador). Van como plain_text A PROPOSITO: este script
# (y Agregar-Variable, Setup-ReglasClave y Setup-ClaveEditor) lee las que ya hay y las vuelve a mandar todas, porque el PATCH reemplaza el
# bloque entero; una secret_text no se puede leer de vuelta y se borraria en la siguiente corrida. Consecuencia: quien entre a tu cuenta de
# Cloudflare ve estas variables. Protegela con verificacion en dos pasos y no la compartas.
function Pedir-Clave([string]$quien) {
    $a = Read-Host ('Escribe la contrasena ' + $quien + ' (14 caracteres o mas; no se ve al escribir)') -AsSecureString
    $b = Read-Host 'Escribela otra vez' -AsSecureString
    $pa = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($a); $pb = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($b)
    try { $ta = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pa); $tb = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pb) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pa); [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pb) }
    if ($ta -cne $tb) { throw 'Las dos no coinciden: vuelve a correr el script.' }
    if ($ta.Length -lt 14) { throw 'La contrasena tiene que tener 14 caracteres o mas (una frase larga sirve).' }
    return $ta
}
#
# NOTE: PS 5.1 ConvertTo-Json mangles/​bloats the large tokens string (a 1.5 KB value
# ballooned to 500 KB and got rejected), so the JSON body is built with manual string
# escaping instead -- reliable and compact.
function ConvertTo-JsonStringLiteral([string]$s) {
    $s = $s -replace '\\','\\'; $s = $s -replace '"','\"'
    $s = $s -replace "`r",'\r'; $s = $s -replace "`n",'\n'; $s = $s -replace "`t",'\t'
    return '"' + $s + '"'
}

# El PATCH reemplaza el bloque env_vars completo, asi que hay que leer lo que ya existe y
# reenviarlo. Sin esto, correr el script para rotar un token borraba silenciosamente las
# contrasenas de rol y dejaba fuera al trafficker, al editor y al socio de Venezuela.
$existentes = @{}
try {
    $actual = Invoke-RestMethod "$api/$proj" -Headers $headers -TimeoutSec 30
    $ev = $actual.result.deployment_configs.production.env_vars
    if ($ev) { foreach ($p in $ev.PSObject.Properties) { $existentes[$p.Name] = [string]$p.Value.value } }
    if ($existentes.Count) { Write-Output "Conservando $($existentes.Count) env vars ya configuradas." }
} catch { Write-Output "Aviso: no pude leer las env vars actuales ($($_.Exception.Message))." }

# La llave de las sesiones se genera solo si NO hay una ya. Antes se generaba siempre que
# se omitiera el parametro, asi que anadir un socio cerraba la sesion de todo el mundo:
# cambiar SESSION_SECRET invalida cada cookie firmada con la anterior.
if (-not $SessionSecret) {
    if ($existentes.ContainsKey('SESSION_SECRET') -and $existentes['SESSION_SECRET']) {
        $SessionSecret = $existentes['SESSION_SECRET']
        Write-Output "SESSION_SECRET: se conserva el que ya estaba (nadie pierde la sesion)."
    } else {
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $SessionSecret = [Convert]::ToBase64String($bytes)
        Write-Output "SESSION_SECRET generado (aleatorio). Queda solo en Cloudflare (no se muestra)."
    }
}

$vars = [ordered]@{}
foreach ($k in $existentes.Keys) { $vars[$k] = $existentes[$k] }   # base: lo que ya habia
$vars['FACEBOOK_TOKENS'] = [string]$tokensJson                      # y encima lo nuevo
if (-not $Password -and ($CambiarClave -or -not $existentes.ContainsKey('ADMIN_PASSWORD'))) { $Password = Pedir-Clave 'del dueno' }
if ($Password) {
    if ($Password.Length -lt 14) { throw 'La contrasena del dueno tiene que tener 14 caracteres o mas (una frase larga sirve).' }
    $vars['ADMIN_PASSWORD'] = [string]$Password
}
elseif (-not $existentes.ContainsKey('ADMIN_PASSWORD')) {
    throw 'No hay ADMIN_PASSWORD en Cloudflare y no se paso -Password. Sin eso nadie podria entrar como dueno.'
}
$vars['SESSION_SECRET']  = [string]$SessionSecret
if ($ClaveDatos) { $vars['CLAVE_DATOS'] = [string]$ClaveDatos }
# Plantilla: cada contrasena de rol o de pais tambien de 14 caracteres o mas (el sitio no acepta una mas corta). Con ? se pide en la ventana.
if ($TraffickerPassword -eq '?') { $TraffickerPassword = Pedir-Clave 'del trafficker' }
if ($EditorPassword -eq '?')     { $EditorPassword     = Pedir-Clave 'del editor' }
if ($VideosPassword -eq '?')     { $VideosPassword     = Pedir-Clave 'de videos' }
foreach ($pk in @($ClavesPais.Keys)) { if ([string]$ClavesPais[$pk] -eq '?') { $ClavesPais[$pk] = Pedir-Clave ('del socio de ' + $pk) } }
foreach ($c in (@($TraffickerPassword, $EditorPassword, $VideosPassword) + @($ClavesPais.Values))) {
    if ($c -and ([string]$c).Length -lt 14) { throw 'Cada contrasena de rol o de pais tiene que tener 14 caracteres o mas (una frase larga sirve).' }
}
if ($TraffickerPassword) { $vars['TRAFFICKER_PASSWORD'] = $TraffickerPassword }
if ($EditorPassword)     { $vars['EDITOR_PASSWORD']     = $EditorPassword }
if ($VideosPassword)     { $vars['VIDEOS_PASSWORD']     = $VideosPassword }
# Un socio por pais: cada contrasena abre SU dashboard y nada mas.
$PAISES_VALIDOS = @('mx','ve','co','pe','gt','ar','ec','bo')
foreach ($pais in $ClavesPais.Keys) {
    $pk = ([string]$pais).ToLower()
    if ($PAISES_VALIDOS -notcontains $pk) {
        throw "Pais '$pais' desconocido. _middleware.js solo conoce: $($PAISES_VALIDOS -join ', '). Un codigo con dedazo guardaria una env var que nadie lee y el socio no podria entrar."
    }
    $vars[$pk.ToUpper() + '_PASSWORD'] = [string]$ClavesPais[$pais]
}

# Plantilla: si cambia cualquier contrasena (o con -CerrarSesiones) se genera otra llave de sesiones, porque una sesion dura 30 dias y quien
# ya habia entrado con la contrasena vieja seguiria adentro. Todos (tambien tu) vuelven a entrar con su contrasena.
# La misma llave firma los pedidos de clips de IA del editor que estan en curso: cambiarla los corta (ya pagados). Si el editor o el servidor
# estan generando un anuncio, espera a que terminen.
$sesionesNuevas = $false
if (-not $PSBoundParameters.ContainsKey('SessionSecret') -and ($CerrarSesiones -or $Password -or $TraffickerPassword -or $EditorPassword -or $VideosPassword -or $ClavesPais.Count)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $vars['SESSION_SECRET'] = [Convert]::ToBase64String($bytes)
    $sesionesNuevas = $true
}
$pairs = @()
foreach ($k in $vars.Keys) {
    $pairs += '"' + $k + '":{"type":"plain_text","value":' + (ConvertTo-JsonStringLiteral ([string]$vars[$k])) + '}'
}
$patch = '{"deployment_configs":{"production":{"env_vars":{' + ($pairs -join ',') + '}}}}'
Invoke-RestMethod "$api/$proj" -Method Patch -Headers $headers -ContentType 'application/json' -Body $patch -TimeoutSec 30 | Out-Null
Write-Output ("Env vars set: " + (($vars.Keys | ForEach-Object { "$_ (len $($vars[$_].Length))" }) -join ', '))
Write-Output "Cloudflare Pages ready. Push/dispatch the workflow to deploy."
if ($sesionesNuevas) {
    Write-Output 'Llave de sesiones nueva: se cierran las sesiones de todos (tambien la tuya) y se cortan los pedidos de clips de IA que esten en curso.'
    Write-Output 'OJO: nada de esto vale hasta el siguiente despliegue. Ve YA a GitHub > Actions > Refresco del dashboard > Run workflow, y que termine en verde: hasta entonces la contrasena vieja y las sesiones abiertas siguen sirviendo.'
}
