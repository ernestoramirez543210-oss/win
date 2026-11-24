# Prueba de la guarda de montos imposibles (Remove-AmountOutliers, scripts/_ventas-comun.ps1) y de su uso en el build (scripts/build-dashboard.ps1).
#
#   powershell -File scripts\Probar-MontosImposibles.ps1
#
# El caso real (2026-11-03): Mexico no pasaba por Remove-AmountOutliers. Una venta del 2 de noviembre con el Post ID pegado en PAGO RECIBIDO
# vuelve a quedar sin su linea, o si la red final antes de escribir el snapshot deja de apartar el monto.
#
# Sale con codigo 1 si algo falla. Solo ASCII a proposito (PS 5.1 lee un .ps1 sin BOM como ANSI).
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_ventas-comun.ps1')

$script:fallos = 0
function Prueba([string]$nombre, [scriptblock]$cuerpo) {
    try { & $cuerpo; Write-Output ('  ok  ' + $nombre) }
    catch { $script:fallos++; Write-Output ('  FALLA  ' + $nombre + ' -> ' + $_.Exception.Message) }
}
function Afirma([bool]$cond, [string]$msg) { if (-not $cond) { throw $msg } }

function Ventas([double[]]$montos) {
    $l = New-Object System.Collections.Generic.List[object]
    $i = 0
    foreach ($m in $montos) { $i++; $l.Add([PSCustomObject]@{ offer = 'Zapdata Sermones Biblicos'; date = '2026-11-02'; amount = $m; postId = "p$i" }) }
    return , $l
}
$normales = @(); for ($i = 0; $i -lt 40; $i++) { $normales += (167 + ($i % 6) * 30) }
$POST_ID_COMO_MONTO = 999900000000850016

$build = Get-Content (Join-Path $PSScriptRoot 'build-dashboard.ps1') -Raw -Encoding UTF8

Write-Output 'Remove-AmountOutliers'
Prueba 'aparta el Post ID pegado en el monto (caso Mexico 2026-11-02) y deja las ventas normales' {
    $q = New-Object System.Collections.Generic.List[object]
    $r = Remove-AmountOutliers (Ventas ($normales + $POST_ID_COMO_MONTO)) $q
    Afirma ($r.removed -eq 1) ('aparto ' + $r.removed)
    Afirma ($r.orders.Count -eq 40) ('quedaron ' + $r.orders.Count)
    Afirma ($q.Count -eq 1) 'no dejo el motivo en la cuarentena'
}
Prueba 'no toca una venta alta pero real (50 veces la mediana)' {
    $q = New-Object System.Collections.Generic.List[object]
    $r = Remove-AmountOutliers (Ventas ($normales + 10000)) $q
    Afirma ($r.removed -eq 0) ('aparto ' + $r.removed)
}

Write-Output 'build-dashboard.ps1'
Prueba 'cada pais del snapshot pasa por Remove-AmountOutliers antes de usarse' {
    $bloque = [regex]::Match($build, '(?ms)^\$snapshot = \[ordered\]@\{.*?^\}').Value
    Afirma ($bloque.Length -gt 0) 'no encontre el bloque $snapshot'
    $vars = @([regex]::Matches($bloque, '(?m)^\s*orders\s*=\s*\$(\w+)(?:\.orders)?\s*$') | ForEach-Object { $_.Groups[1].Value })
    Afirma ($vars.Count -ge 8) ('solo encontre ' + $vars.Count + ' paises en el snapshot')
    $faltan = @()
    foreach ($v in $vars) {
        # Mexico entra al snapshot como $mexico.orders, armado con $mxOrders.
        $lista = if ($v -eq 'mexico') { 'mxOrders' } else { $v }
        if ($build -notmatch ('(?m)^\$r = Remove-AmountOutliers \$' + $lista + '\b')) { $faltan += $lista }
    }
    Afirma ($faltan.Count -eq 0) ('sin guarda: ' + ($faltan -join ', '))
}
Prueba 'Mexico vuelve a $mexico.orders despues de limpiarse (lo que leen los cruces con anuncios y el snapshot)' {
    Afirma ($build -match '\$mxOrders  = \$r\.orders; \$mexico\.orders = \$mxOrders') 'la lista limpia no vuelve a $mexico.orders'
}
Prueba 'la red final aparta el monto aunque el pais se haya saltado su linea' {
    $red = [regex]::Match($build, '(?ms)^# Red final.*?^\}').Value
    Afirma ($red.Length -gt 0) 'no encontre la red final'
    $snapshot = [ordered]@{ countries = [ordered]@{
        mexico = [ordered]@{ orders = (Ventas ($normales + $POST_ID_COMO_MONTO)); quarantinedCount = 3; quarantinedByReason = [ordered]@{ 'fecha futura' = 3 } }
        peru   = [ordered]@{ orders = (Ventas $normales); quarantinedCount = 0; quarantinedByReason = [ordered]@{} }
    } }
    Invoke-Expression $red 3>$null
    $mx = $snapshot.countries.mexico
    Afirma ($mx.orders.Count -eq 40) ('Mexico quedo con ' + $mx.orders.Count + ' ventas')
    $suma = 0.0; foreach ($o in $mx.orders) { $suma += [double]$o.amount }
    Afirma ($suma -lt 20000) ('la suma sigue en ' + $suma)
    Afirma ($mx.quarantinedCount -eq 4) ('cuarentena ' + $mx.quarantinedCount)
    Afirma ($mx.quarantinedByReason['fecha futura'] -eq 3) 'perdio los motivos que ya tenia'
    Afirma ($snapshot.countries.peru.orders.Count -eq 40) 'toco un pais limpio'
}

if ($script:fallos) { Write-Output ("$($script:fallos) prueba(s) fallaron"); exit 1 }
Write-Output 'Todo bien.'
