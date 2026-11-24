# Antiguedad de las alertas de cuentas publicitarias caidas (2026-10-29).
#
# El dueno pidio ocultar las alertas de cuentas que llevan mas de 15 dias caidas: son cuentas
# que ya no se van a volver a usar y su alerta solo tapa las nuevas. Aqui vive la unica
# definicion de "cuanto lleva caida una cuenta"; la usa build-dashboard.ps1 (que la escribe en
# data\salud.json y en el snapshot) y la prueba scripts\Probar-AlertasViejas.ps1. El aviso, la
# pagina de salud y Verificar-Bandas.ps1 solo LEEN el resultado: nadie vuelve a derivarlo.
#
# De donde sale la edad: de data\salud-historia.json, donde Avisar-Salud.ps1 anota el estado de
# cada cuenta, dia por dia. No se guarda una "primera fecha" aparte: se deduce siempre de esa
# historia, asi no hay un segundo dato que se desincronice y, si alguien corrige la historia a
# mano, la edad se corrige sola.
#
# "Caida" aqui es UNA sola condicion (no puede gastar), sin importar la causa: deshabilitada,
# impaga, cerrada o ilegible (estado -1: Facebook no contesta, p. ej. "API access blocked").
# Que una cuenta cambie de causa -de ilegible a deshabilitada- no reinicia el reloj; lo que lo
# reinicia es que vuelva a estar sana aunque sea un dia (entonces, si se cae otra vez, es una
# alerta nueva y sale completa).
#
# Ante la duda, la alerta SE VE: sin historia, con un hueco grande o con la cuenta apenas
# anotada, la edad sale 0 o corta. Ocultar de mas es peor que avisar de mas.
#
# ASCII-only: PowerShell 5.1 lee un .ps1 sin BOM como ANSI y rompe cualquier acento.

# Una alerta de MAS de este numero de dias se oculta. Con 15 justos todavia se ve.
$DIAS_ALERTA_VIEJA = 15
# Dias de calendario que pueden separar dos anotaciones de la cuenta y aun contarse como
# "seguidos". 3 = hasta dos dias sin anotar (una corrida que no llego a commitear). Mas hueco
# que eso y no hay evidencia de que siguiera caida: el reloj arranca despues del hueco.
$SALTO_MAX_DIAS = 3

# La historia ordenada de la mas nueva a la mas vieja, lista para recorrer una vez por cuenta.
# Deja fuera hoy y lo posterior: el estado de hoy no sale de la historia sino de esta corrida.
# $Historia es el objeto de ConvertFrom-Json de salud-historia.json (o $null si no existe).
function New-IndiceHistoriaSalud($Historia, [string]$Hoy) {
    $indice = New-Object System.Collections.Generic.List[object]
    if ($null -eq $Historia -or $null -eq $Historia.dias) { return ,$indice }
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $dHoy = [datetime]::ParseExact($Hoy, 'yyyy-MM-dd', $inv)
    foreach ($p in $Historia.dias.PSObject.Properties) {
        $f = [datetime]::MinValue
        # Una clave que no es fecha se ignora: un archivo a medio editar no puede tumbar el build.
        if (-not [datetime]::TryParseExact([string]$p.Name, 'yyyy-MM-dd', $inv, [Globalization.DateTimeStyles]::None, [ref]$f)) { continue }
        if ($f -ge $dHoy) { continue }
        $indice.Add([pscustomobject]@{ fecha = $f; texto = [string]$p.Name; estados = $p.Value })
    }
    $ordenado = New-Object System.Collections.Generic.List[object]
    foreach ($x in ($indice | Sort-Object -Property fecha -Descending)) { $ordenado.Add($x) }
    return ,$ordenado
}

# Desde cuando lleva caida una cuenta que HOY esta caida, y cuantos dias son.
# $Indice sale de New-IndiceHistoriaSalud. $EstadosCaida son los estados de la historia que
# cuentan como caida (los de ESTADOS_CAIDA mas el -1 de las ilegibles).
# Devuelve @{ desde = 'AAAA-MM-DD'; dias = N }. Una cuenta recien caida: desde = hoy, dias = 0.
function Get-DesdeCaida($Indice, [string]$Cuenta, [string]$Hoy, [int[]]$EstadosCaida) {
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $dHoy = [datetime]::ParseExact($Hoy, 'yyyy-MM-dd', $inv)
    $desde = $Hoy
    $previo = $dHoy
    foreach ($d in $Indice) {
        $e = $null
        if ($null -ne $d.estados) { $e = $d.estados.PSObject.Properties[$Cuenta] }
        # Ese dia la cuenta no quedo anotada (aun no existia, o la corrida no la vio): no dice
        # nada ni a favor ni en contra, se sigue buscando hacia atras. El hueco se mide contra
        # la ultima anotacion que SI confirmo la caida, no contra este dia vacio.
        if ($null -eq $e) { continue }
        if ((($previo - $d.fecha).Days) -gt $SALTO_MAX_DIAS) { break }
        if ($EstadosCaida -notcontains [int]$e.Value) { break }
        $desde = $d.texto
        $previo = $d.fecha
    }
    $dias = [Math]::Max(0, (($dHoy - [datetime]::ParseExact($desde, 'yyyy-MM-dd', $inv)).Days))
    return @{ desde = $desde; dias = $dias }
}

# Escribe en cada cuenta de $Cuentas (los diccionarios ordenados de saludCuentas, ya con `sana` e `ilegible`) tres campos: caidaDesde, diasCaida y alertaOculta. Es lo unico que decide si una
# alerta se oculta; build-dashboard.ps1 lo llama una vez y todo lo demas solo lee el resultado.
#
# Dos salvaguardas, las dos hacia el lado de AVISAR:
#   - Si calcular la edad de una cuenta falla por cualquier motivo, esa cuenta queda con 0 dias (se avisa) y el build SIGUE: el refresco nunca puede caerse por esto.
#   - Una cuenta ilegible no se oculta si aun tiene anuncios encendidos, ni si en su pais NINGUNA cuenta se puede leer (ver el comentario de mas abajo). Una ilegible vieja y sin anuncios, como
#     las 3 de Ecuador, si se oculta: es lo que pidio el dueno.
function Set-AntiguedadAlertas($Cuentas, $Indice, [string]$Hoy, [int[]]$EstadosCaida) {
    foreach ($acc in @($Cuentas.Keys)) {
        $h = $Cuentas[$acc]
        $h['caidaDesde']   = ''
        $h['diasCaida']    = 0
        $h['alertaOculta'] = $false
        if ($h['sana']) { continue }
        try {
            $edad = Get-DesdeCaida -Indice $Indice -Cuenta ([string]$acc) -Hoy $Hoy -EstadosCaida $EstadosCaida
            $h['caidaDesde']   = [string]$edad.desde
            $h['diasCaida']    = [int]$edad.dias
            $h['alertaOculta'] = ([int]$edad.dias -gt $DIAS_ALERTA_VIEJA)
        } catch {
            Write-Warning ("Salud: no pude calcular cuanto lleva caida la cuenta " + $acc + "; su alerta se queda visible (" + $_.Exception.Message + ")")
        }
    }
    # Una ilegible NO se oculta si (1) todavia tiene anuncios encendidos -no sabemos si esta gastando, y su gasto no entra en ningun ROAS: es la averia que el dueno si quiere ver, un token o un
    # no se puede leer NINGUNA cuenta. Las 3 de Ecuador cumplen ninguna: 0 anuncios y sus cuentas nuevas se leen, asi que se ocultan. `activos` ya viene calculado por build-dashboard.ps1 y,
    # para una ilegible, se conserva de la ultima lectura buena.
    $conLectura = @{}
    foreach ($h in $Cuentas.Values) { if (-not $h['ilegible']) { $conLectura[[string]$h['country']] = $true } }
    foreach ($h in $Cuentas.Values) {
        if (-not ($h['alertaOculta'] -and $h['ilegible'])) { continue }
        if (([int]$h['activos'] -gt 0) -or -not $conLectura.ContainsKey([string]$h['country'])) { $h['alertaOculta'] = $false }
    }
}
