# Arma el dashboard con DATOS INVENTADOS para que veas como se ve antes de conectar tu negocio.
#
#   Doble clic en Ver-Ejemplo.cmd   (o:  powershell -ExecutionPolicy Bypass -File scripts\Ver-Ejemplo.ps1)
#
# No toca tus datos: todo se escribe en la carpeta _ejemplo (no se sube a GitHub). Las ventas, los clientes, los anuncios y las cifras son de mentira:
# dos paises (Colombia y Mexico), tres ofertas y ocho anuncios, con ventas de los ultimos 28 dias hasta hoy.
# Usa el MISMO armado que corre GitHub con tus datos reales (scripts\build-dashboard.ps1), asi que si el ejemplo se ve bien, el armado funciona en tu PC.
# Tambien arma las paginas de las herramientas (scripts\Armar-Suite.ps1). Abiertas desde el disco se ven, pero no guardan ni crean nada: eso lo
# hace el servidor (Cloudflare) cuando el sitio esta publicado.
# Todo en ASCII a proposito: PowerShell 5.1 (el de Windows) lee mal las tildes de un .ps1 sin BOM.
param([switch]$SinAbrir, [int]$Dias = 28)
$ErrorActionPreference = 'Stop'
$Raiz  = Split-Path -Parent $PSScriptRoot
$Ej    = Join-Path $Raiz '_ejemplo'
$Datos = Join-Path $Ej 'data'
if (Test-Path $Ej) { Remove-Item -Recurse -Force $Ej }
New-Item -ItemType Directory -Force -Path $Datos | Out-Null
# El armado busca las plantillas junto a la carpeta de datos (data\..\templates)
Copy-Item -Recurse (Join-Path $Raiz 'templates') (Join-Path $Ej 'templates')
# La fuente de las banderas (Windows no las dibuja solo)
Copy-Item (Join-Path $Raiz 'banderas.woff2') (Join-Path $Ej 'banderas.woff2')

$inv   = [Globalization.CultureInfo]::InvariantCulture
$conBom = New-Object Text.UTF8Encoding($true)
$sinBom = New-Object Text.UTF8Encoding($false)
$rnd   = New-Object System.Random 20261120
$ahora = [DateTime]::UtcNow.AddHours(-5)          # hora de Colombia
$hoy   = $ahora.Date
$MESES = @('ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC')
$FX    = [ordered]@{ MXN = 18.5; VES = 220.0; COP = 4000.0; PEN = 3.7; GTQ = 7.7; ARS = 1400.0; BOB = 6.9 }

function Poisson([double]$l) { $L = [Math]::Exp(-$l); $k = 0; $p = 1.0; do { $k++; $p *= $rnd.NextDouble() } while ($p -gt $L -and $k -lt 60); return $k - 1 }
function Azar([double]$a, [double]$b) { return $a + ($b - $a) * $rnd.NextDouble() }

# El negocio de ejemplo. Los IDs empiezan por 9999: son obviamente falsos. "peso" = que tan bien vende cada anuncio con el mismo gasto.
$negocio = @(
    @{ pais = 'colombia'; pre = 'col_'; cuenta = '9999000000000001'; moneda = 'COP'; fxCuenta = 4000.0; local = 'COP'
       precios = @(15000, 15000, 20000, 30000); metodos = @('Nequi', 'Bancolombia', 'Daviplata', 'Transferencia')
       ofertas = @(
         @{ nombre = 'RECETAS EJEMPLO'; etiqueta = 'Recetas Ejemplo'; archivo = 'RECETAS_EJEMPLO'; ventasDia = 7; gastoDia = 70000; anuncios = @(@{ n = '101'; peso = 1.6 }, @{ n = '102'; peso = 1.0 }, @{ n = '103'; peso = 0.4 }) },
         @{ nombre = 'GUIA EJEMPLO';    etiqueta = 'Guia Ejemplo';    archivo = 'GUIA_EJEMPLO';    ventasDia = 3; gastoDia = 60000; anuncios = @(@{ n = '101'; peso = 1.3 }, @{ n = '102'; peso = 0.7 }) }
       ) },
    @{ pais = 'mexico'; pre = 'mx_'; cuenta = '9999000000000002'; moneda = 'USD'; fxCuenta = 1.0; local = 'MXN'
       precios = @(99, 99, 149); metodos = @('Transferencia', 'Deposito OXXO', 'Mercado Pago')
       ofertas = @(
         @{ nombre = 'RECETAS EJEMPLO'; etiqueta = 'Recetas Ejemplo'; archivo = 'RECETAS_EJEMPLO'; ventasDia = 4; gastoDia = 22; anuncios = @(@{ n = '101'; peso = 1.2 }, @{ n = '102'; peso = 0.9 }, @{ n = '103'; peso = 0.9 }) }
       ) }
)

$U = [char]0x00DA; $E = [char]0x00C9
$cabecera = '"Etiquetas","NOMBRE","FECHA","N' + $U + 'MERO","PAGO RECIBIDO","POST ID","M' + $E + 'TODO DE PAGO"'
$filas   = New-Object System.Collections.Generic.List[object]
$cuentas = [ordered]@{}; $salud = [ordered]@{}; $permiso = [ordered]@{}; $motivo = [ordered]@{}
$inicio  = $hoy.AddDays(-($Dias - 1))
$fechaCampana = '{0} {1}' -f $inicio.Day, $MESES[$inicio.Month - 1]
$cliente = 0; $iPais = 0; $totalVentas = 0

foreach ($p in $negocio) {
    $iPais++
    $cuentas[$p.pais] = @($p.cuenta)
    $salud[$p.cuenta] = [ordered]@{ nombre = 'Cuenta de ejemplo ' + $p.pais; estado = 1; motivo = 0; country = $p.pais }
    $permiso[$p.cuenta] = $false
    $motivo[$p.cuenta] = 'Datos de ejemplo: no hay una cuenta de verdad que pausar'
    $iOf = 0
    foreach ($o in $p.ofertas) {
        $iOf++
        $lineas = New-Object System.Collections.Generic.List[string]
        $lineas.Add($cabecera)
        $pesoTotal = 0.0; foreach ($a in $o.anuncios) { $pesoTotal += $a.peso }
        $iAd = 0
        foreach ($a in $o.anuncios) {
            $iAd++
            $base    = '9999{0}{1}{2}0000000000' -f $iPais, $iOf, $iAd
            $campId  = $base + '1'; $adsetId = $base + '2'; $adId = $base + '3'
            $campana = '{0} | {1} | INT - COM | {2}' -f $o.nombre, $fechaCampana, $a.n
            $gastoAnuncioDia = $o.gastoDia / $o.anuncios.Count
            for ($d = $Dias - 1; $d -ge 0; $d--) {
                $dia   = $hoy.AddDays(-$d)
                $esHoy = ($d -eq 0)
                $parte = 1.0
                if ($esHoy) { $parte = [Math]::Max(0.05, ($ahora.Hour + $ahora.Minute / 60.0) / 24.0) }
                $gasto = $gastoAnuncioDia * (Azar 0.7 1.3) * $parte
                $gastoUsd = $gasto / $p.fxCuenta
                $imp   = [int]($gastoUsd * (Azar 60 110))
                $clics = [int]($imp * (Azar 0.015 0.035))
                $n     = Poisson ($o.ventasDia * ($a.peso / $pesoTotal) * $parte * (Azar 0.7 1.3))
                $conv  = $n * 4 + $rnd.Next(0, 6)
                for ($k = 0; $k -lt $n; $k++) {
                    $cliente++; $totalVentas++
                    if ($esHoy) { $hora = $rnd.Next(0, [Math]::Max(1, $ahora.Hour)) } else { $hora = $rnd.Next(6, 24) }
                    $fecha  = $dia.AddHours($hora).AddMinutes($rnd.Next(0, 60)).AddSeconds($rnd.Next(0, 60))
                    $monto  = $p.precios[$rnd.Next(0, $p.precios.Count)]
                    $metodo = $p.metodos[$rnd.Next(0, $p.metodos.Count)]
                    $tel    = '5999' + $cliente.ToString('00000000')
                    $lineas.Add(('"Compra realizada {0}","Cliente {1}","{2}","{3}","{4}","{5}","{6}"' -f $o.etiqueta, $cliente, $fecha.ToString('d/M/yyyy H:mm:ss', $inv), $tel, $monto, $adId, $metodo))
                }
                $filas.Add([ordered]@{
                    date = $dia.ToString('yyyy-MM-dd', $inv); account_id = $p.cuenta; account_name = 'Cuenta de ejemplo'; account_currency = $p.moneda
                    campaign = $campana; campaign_id = $campId; campaign_effective_status = 'ACTIVE'; campaign_objective = 'OUTCOME_ENGAGEMENT'; is_cbo = $true
                    campaign_budget_daily = [Math]::Round($gastoAnuncioDia * 1.2); campaign_budget_lifetime = 0
                    adset_id = $adsetId; adset_name = $a.n; adset_effective_status = 'ACTIVE'; adset_budget_daily = 0; adset_budget_lifetime = 0
                    ad_id = $adId; ad_name = $a.n; effective_status = 'ACTIVE'
                    clicks = [string]$clics; impressions = [string]$imp; spend = $gasto.ToString('0.00', $inv)
                    conv = $conv; conv_resp = $conv; conv_d2 = [int]($conv * 0.6); conv_d3 = [int]($conv * 0.3); purchases = 0; purchase_value = 0
                })
            }
        }
        [IO.File]::WriteAllText((Join-Path $Datos ($p.pre + $o.archivo + '.csv')), (($lineas -join "`r`n") + "`r`n"), $conBom)
    }
}

$fg = [ordered]@{
    fetchedAt = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ', $inv); source = 'ejemplo'
    since = $inicio.ToString('yyyy-MM-dd', $inv); until = $hoy.ToString('yyyy-MM-dd', $inv)
    result = $filas.ToArray(); scheduledCampaigns = @()
    writeAccess = $permiso; writeReason = $motivo; accountsByCountry = $cuentas; saludCuentas = $salud
}
[IO.File]::WriteAllText((Join-Path $Datos 'facebook-graph.json'), (ConvertTo-Json -InputObject $fg -Depth 6 -Compress), $sinBom)
$fx = [ordered]@{ time_last_update_utc = [DateTime]::UtcNow.ToString('r', $inv); rates = $FX }
[IO.File]::WriteAllText((Join-Path $Datos 'fx.json'), (ConvertTo-Json -InputObject $fx -Depth 4), $sinBom)

Write-Host ("Datos inventados: {0} ventas y {1} filas de gasto en {2} dias. Armando el dashboard (tarda uno o dos minutos)..." -f $totalVentas, $filas.Count, $Dias)
& (Join-Path $PSScriptRoot 'build-dashboard.ps1') -DataDir $Datos -OutHtml (Join-Path $Ej 'dashboard-ventas.html') -SnapshotPath (Join-Path $Datos 'snapshot.json') | Out-Null
& (Join-Path $PSScriptRoot 'Armar-Suite.ps1') -DataDir $Datos -Salida $Ej   # radar, tablero, ebooks, flujos, fabrica, editor y el menu
$pagina = Join-Path $Ej 'dashboard-ventas.html'
if (-not (Test-Path $pagina)) { throw 'El armado no dejo el dashboard. Mira los mensajes de arriba.' }
Write-Host ''
Write-Host 'Listo. El dashboard de ejemplo esta en:'
Write-Host ('  ' + $pagina)
Write-Host 'Las cifras son INVENTADAS. Para conectarlo a tu negocio, abre esta carpeta en Claude y dile: "Lee CLAUDE.md y ayudame a conectar el dashboard a mi negocio".'
if (-not $SinAbrir) { Start-Process $pagina }
