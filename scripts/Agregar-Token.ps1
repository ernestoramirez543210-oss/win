# Agrega un token de Facebook al archivo de tokens, sin editar JSON a mano.
#
# El token se escribe EN TU TERMINAL y no viaja a ningun otro lado: ni al chat, ni al repo
# -data\facebook-tokens.json esta en .gitignore- ni a la pantalla, porque Read-Host -AsSecureString
# no lo muestra mientras lo pegas. Si en vez de pegar solo das Enter, lo toma del portapapeles
# (lo que copiaste en Facebook), que es lo mas seguro: pegar en la entrada oculta se dana a veces.
#
# Ademas valida antes de guardar. Un token sin cuentas asignadas se ve identico a uno bueno
# hasta que el tablero muestra ceros dos dias despues: aqui se dice de una.
#
#   & scripts\Agregar-Token.ps1 -Pais mexico
#   & scripts\Agregar-Token.ps1 -Pais mexico -Cuentas 1234567890      # lista fija en vez de auto
#   & scripts\Agregar-Token.ps1 -Sincronizar                          # solo refresca la lista esperada
param(
    [string]  $Pais    = '',
    [string[]]$Cuentas = @(),
    [string]  $Nota    = '',
    [string]  $DataDir = (Join-Path (Split-Path $PSScriptRoot) 'data'),
    [switch]  $Sincronizar
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$API = 'https://graph.facebook.com/v25.0'

$ruta = Join-Path $DataDir 'facebook-tokens.json'
if (-not (Test-Path $ruta)) { New-Item -ItemType Directory -Force -Path $DataDir | Out-Null; [IO.File]::WriteAllText($ruta, '{}', (New-Object Text.UTF8Encoding($false))); Write-Output "Cree $ruta (vacio; no se sube a GitHub)." }
$json = Get-Content $ruta -Raw -Encoding UTF8 | ConvertFrom-Json

# ---------------------------------------------------------------------------
# La lista de cuentas que este archivo conoce, para que CI pueda darse cuenta si su copia
# del secreto quedo vieja. Los IDs de cuenta NO son secretos -ya se ven en el tablero- asi
# que esta lista si va al repo. Lo que no va es ningun token.
# ---------------------------------------------------------------------------
function Escribir-Esperadas {
    $esperadas = [ordered]@{}
    foreach ($p in ($json.PSObject.Properties.Name | Sort-Object)) {
        if ($p -like '_*') { continue }
        $bloques = if ($json.$p.tokens) { $json.$p.tokens } else { @($json.$p) }
        $ids = New-Object System.Collections.Generic.List[string]
        foreach ($b in $bloques) {
            foreach ($a in @($b.accounts)) { if ($a -and -not $ids.Contains([string]$a)) { $ids.Add([string]$a) } }
            # Un bloque con lista vacia descubre sus cuentas solo. Se le pregunta ahora para
            # que la lista esperada refleje lo que ese token de verdad alcanza.
            if (-not @($b.accounts).Count) {
                try {
                    $d = Invoke-RestMethod "$API/me/adaccounts?fields=account_id&limit=200&access_token=$($b.systemUserToken)" -TimeoutSec 30
                    foreach ($a in @($d.data)) { $id = [string]$a.account_id; if ($id -and -not $ids.Contains($id)) { $ids.Add($id) } }
                } catch { }
            }
        }
        if ($ids.Count) { $esperadas[$p] = [string[]]$ids.ToArray() }
    }
    $doc = [ordered]@{}
    $doc['_doc'] = 'Cuentas publicitarias que conoce data\facebook-tokens.json. Sirve para que CI avise si su copia del secreto FACEBOOK_TOKENS quedo vieja: si el repo espera 6 cuentas de Mexico y CI solo ve 4, el secreto esta desactualizado. Aqui NO hay ningun token.'
    $doc['_como'] = 'Lo escribe scripts\Agregar-Token.ps1. Si editas facebook-tokens.json a mano, corre "Agregar-Token.ps1 -Sincronizar" para ponerlo al dia.'
    $doc['_actualizado'] = [DateTime]::UtcNow.AddHours(-5).ToString('yyyy-MM-dd')
    $doc['porPais'] = $esperadas
    $out = Join-Path $DataDir 'cuentas-esperadas.json'
    [IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
    $n = 0; foreach ($v in $esperadas.Values) { $n += @($v).Count }
    Write-Output "Lista esperada -> $out ($n cuenta(s) en $($esperadas.Count) pais(es))"
}

if ($Sincronizar) { Escribir-Esperadas; exit 0 }

if (-not $Pais) { Write-Output "Falta -Pais. Ej: -Pais mexico"; exit 1 }
# Plantilla: un pais que todavia no esta en el archivo se agrega (solo los ocho que conoce el dash).
if (@($json.PSObject.Properties.Name) -notcontains $Pais) {
    if (@('mexico','venezuela','colombia','peru','guatemala','argentina','ecuador','bolivia') -notcontains $Pais) { Write-Output "Pais '$Pais' desconocido. Usa: mexico, venezuela, colombia, peru, guatemala, argentina, ecuador o bolivia."; exit 1 }
    $json | Add-Member -NotePropertyName $Pais -NotePropertyValue ([pscustomobject]@{ tokens = @() })
    Write-Output "Pais '$Pais' agregado al archivo."
}

# ---------------------------------------------------------------------------
# El token
# ---------------------------------------------------------------------------
Write-Output "Copia el token en Facebook y aqui solo dale Enter: se toma de lo que copiaste."
Write-Output "(Tambien puedes pegarlo y dar Enter. No se va a ver en pantalla.)"
$seguro = Read-Host -AsSecureString "Token"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
$tk = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
$delPortapapeles = $false
if (-not $tk) {
    # Pegar en una entrada oculta falla seguido: la terminal de la app le mete caracteres de
    # pegado o lo corta. Leerlo del portapapeles evita los dos problemas.
    $tk = [string](Get-Clipboard -Raw)
    $delPortapapeles = $true
}
# Un token de Facebook es solo letras y numeros. Lo demas son restos del pegado: espacios,
# saltos de linea o las marcas de pegado de la terminal (ESC[200~ ... ESC[201~).
$tk = ($tk -replace '\x1b\[20[01]~', '') -replace '[^A-Za-z0-9]', ''
if (-not $tk) { Write-Output "No llego ningun token: ni pegado ni en lo copiado."; exit 1 }
Write-Output ("  token de {0} caracteres, {1}" -f $tk.Length, $(if ($delPortapapeles) { 'tomado de lo copiado' } else { 'pegado' }))
if ($tk -notmatch '^EAA') { Write-Output "  OJO: no empieza como un token de Facebook (EAA...). Revisa que copiaste el token y no otra cosa." }

Write-Output ""
Write-Output "Validando contra Facebook..."
try {
    $dbg = Invoke-RestMethod "$API/debug_token?input_token=$tk&access_token=$tk" -TimeoutSec 30
} catch {
    # El 400 a secas no dice nada; Facebook explica el motivo en el cuerpo de la respuesta.
    $motivo = $_.Exception.Message
    try { $motivo = (($_.ErrorDetails.Message | ConvertFrom-Json).error.message) } catch { }
    Write-Output "  FALLO: el token no sirve ($motivo)"
    exit 1
}
if ($delPortapapeles) {
    # Que el token no quede copiado y termine pegado en otro lado por accidente.
    try { Set-Clipboard -Value ' ' } catch { }
}
$tipo = [string]$dbg.data.type
$expira = if ([int]$dbg.data.expires_at -eq 0) { 'nunca' } else { [DateTimeOffset]::FromUnixTimeSeconds([int]$dbg.data.expires_at).ToString('yyyy-MM-dd') }
$scopes = @($dbg.data.scopes)
Write-Output "  tipo: $tipo   expira: $expira"
if ($tipo -ne 'SYSTEM_USER') {
    Write-Output "  OJO: no es un token de usuario de sistema. Los de usuario normal caducan y el refresco se cae solo."
}
if ($scopes -notcontains 'ads_read') { Write-Output "  OJO: le falta el permiso ads_read; no va a poder leer el gasto." }

# Que cuentas alcanza. Es lo que de verdad decide si el token sirve: uno recien creado tiene
# los permisos bien y cero cuentas asignadas, y se ve identico a uno bueno.
$vistas = @($Cuentas)
if (-not $vistas.Count) {
    try {
        $d = Invoke-RestMethod "$API/me/adaccounts?fields=account_id,name,account_status&limit=200&access_token=$tk" -TimeoutSec 40
        $vistas = @($d.data | ForEach-Object { [string]$_.account_id })
        foreach ($a in @($d.data)) { Write-Output ("  ve: act_{0,-18} {1} (estado {2})" -f $a.account_id, $a.name, $a.account_status) }
    } catch { Write-Output "  no pude listar sus cuentas: $($_.Exception.Message)" }
}
if (-not $vistas.Count) {
    Write-Output ""
    Write-Output "  ESTE TOKEN NO VE NINGUNA CUENTA PUBLICITARIA."
    Write-Output "  Falta asignarle activos: business.facebook.com -> Configuracion del negocio ->"
    Write-Output "  Usuarios del sistema -> el usuario -> Agregar activos -> Cuentas publicitarias"
    Write-Output "  -> permiso 'Administrar campanas'."
    Write-Output ""
    $r = Read-Host "Guardarlo igual, para que entre solo cuando le asignes las cuentas? (s/n)"
    if ($r -notmatch '^[sS]') { Write-Output "No se guardo nada."; exit 0 }
}

# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------
# Se guarda siempre como bloque nuevo dentro de 'tokens', que es la forma que soporta varios
# Business Manager por pais. Si el pais estaba en la forma vieja -un solo token suelto- se
# convierte sin perder lo que habia.
$actual = $json.$Pais
if (-not $actual.PSObject.Properties['tokens']) {
    $viejo = [pscustomobject]@{ systemUserToken = $actual.systemUserToken; accounts = @($actual.accounts) }
    if ($actual.nota) { $viejo | Add-Member -NotePropertyName nota -NotePropertyValue $actual.nota -Force }
    $json.$Pais = [pscustomobject]@{ tokens = @($viejo) }
}
$nuevo = [pscustomobject]@{ systemUserToken = $tk; accounts = @($Cuentas) }
if ($Nota) { $nuevo | Add-Member -NotePropertyName nota -NotePropertyValue $Nota -Force }
$json.$Pais.tokens = @($json.$Pais.tokens) + $nuevo
[IO.File]::WriteAllText($ruta, ($json | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
$tk = $null
Write-Output ""
Write-Output "Guardado en $ruta ($(@($json.$Pais.tokens).Count) bloque(s) para $Pais)."
Escribir-Esperadas

Write-Output ""
Write-Output "FALTA UN PASO, y sin el CI sigue usando la copia vieja:"
Write-Output ""
Write-Output "  Get-Content data\facebook-tokens.json -Raw | gh secret set FACEBOOK_TOKENS --repo TU-USUARIO/TU-REPO"
Write-Output ""
Write-Output "Si gh no esta autenticado, pega el contenido del archivo en el secreto por la web:"
Write-Output "  https://github.com/TU-USUARIO/TU-REPO/settings/secrets/actions"
Write-Output ""
Write-Output "Despues commitea data\cuentas-esperadas.json, que es lo que deja a CI darse cuenta"
Write-Output "solo si el secreto quedo viejo."
