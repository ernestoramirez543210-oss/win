# Deterministic build pipeline: raw sheet/Windsor/FX data -> snapshot.json -> dashboard-ventas.html.
# No LLM reasoning involved here on purpose - all arithmetic is plain PowerShell so numbers
# are reproducible run over run. Requires the raw pulls already sitting in DataDir (see
# scripts\fetch-data.md for what a scheduled run must fetch before calling this script).
param(
    [string]$DataDir = (Join-Path (Split-Path $PSScriptRoot) 'data'),
    [string]$OutHtml = (Join-Path (Split-Path $PSScriptRoot) 'dashboard-ventas.html'),
    [string]$SnapshotPath = (Join-Path (Split-Path $PSScriptRoot) 'data/snapshot.json'),
    [switch]$DataOnly
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# El parseo de fechas y montos vive en _ventas-comun.ps1 para que Cuadre-Hojas.ps1 lea
# EXACTAMENTE igual que el build. Cuando eran dos copias, la del cuadre se quedo sin la
# rama de fechas serial de Excel y dio por buena una pestana que se contaba dos veces.
. (Join-Path $PSScriptRoot '_ventas-comun.ps1')
# El metodo de pago de cada venta (Nequi, Pago Movil, Transferencia...) sale del texto libre de la columna METODO DE PAGO; el agrupador y su prueba viven aparte.
. (Join-Path $PSScriptRoot '_metodos-pago.ps1')
# Cuanto lleva caida cada cuenta publicitaria (alertas de mas de 15 dias se ocultan); la definicion vive aparte para probarla sin correr el build.
. (Join-Path $PSScriptRoot '_salud-comun.ps1')
# La lectura de las pestanas «order bump ...» de los libros (la configuracion de cada order bump); vive aparte para que Refresh-Headless.ps1 y la prueba usen la misma.
. (Join-Path $PSScriptRoot '_order-bumps.ps1')


# ---------------------------------------------------------------------------
# ID exacto del anuncio de las ventas de ZapData anteriores al parche del webhook
# ---------------------------------------------------------------------------
# El webhook guarda en su pestana 'zapdata log' el payload de cada venta, y desde el 21-22 de septiembre ese payload trae source_id (el
# ad_id de Meta). Las hojas solo lo guardan en POST ID desde que se parcho el webhook (9-10 de octubre), asi que lo anterior tenia un
# enlace en vez del anuncio. scripts/Fetch-ZapDataLog.ps1 arma data/source-ids.json (por pais: CTWA_CLID -> source_id) y aqui se usa para
# casar cada venta de ZapData por su CTWA_CLID, que es unico por clic. Verificado contra las ventas que YA traian el ID: 100% de
# coincidencia en los cinco paises. Lo que no tiene registro en el log queda como estaba (nada se estima).
$script:SrcPorCtwa = @{}
$script:PaisActual = ''
$script:SrcRecuperados = @{}
# Ventas de PRUEBA del equipo (Nombre Prueba...): se dejan en 0, o sea fuera del tablero. La regla y la lista viven en _ventas-comun.ps1 (Test-VentaDePrueba); aqui solo se cuentan por pais para decirlo en el log.
$script:PruebasFuera = @{}
$script:PruebasDetalle = New-Object System.Collections.Generic.List[string]
# Y las mismas ventas de prueba, sin nombre, telefono ni monto (solo pais, pestaña y dia), para la guia de lanzamiento de /fabrica: data\ventas-de-prueba.json, que Build-Suite.ps1 mete en la pagina.
$script:PruebasLista = New-Object System.Collections.Generic.List[object]
$srcFile = Join-Path $DataDir 'source-ids.json'
if (Test-Path $srcFile) {
    try {
        $sj = Get-Content $srcFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $sj.PSObject.Properties) {
            $h = @{}
            foreach ($q in $p.Value.PSObject.Properties) {
                # Formato nuevo: { s: source_id, h: hora }. Formato viejo: solo el source_id como texto.
                if ($q.Value -is [string]) { $h[$q.Name] = @{ s = [string]$q.Value; h = -1 } }
                else { $h[$q.Name] = @{ s = [string]$q.Value.s; h = $(if ($null -ne $q.Value.h) { [int]$q.Value.h } else { -1 }) } }
            }
            $script:SrcPorCtwa[$p.Name] = $h
        }
    } catch { Write-Warning ("source-ids.json ilegible, se ignora: " + $_.Exception.Message) }
}
# Metodo de pago que la IA del comprobante dejo en ese mismo log (data/metodos-log.txt, lo arma Fetch-ZapDataLog.ps1): completa las ventas de ZapData que la hoja dejo «sin
# especificar» con la cuenta a la que pagaron (reglas y cifras en _metodos-pago.ps1). Sin el archivo, no se completa nada y todo queda como antes.
$script:MetCompletadas = @{}
Import-MetodosLog (Join-Path $DataDir 'metodos-log.txt')
# Montos EN REVISION (data/montos-en-revision.json, lo escribe scripts/reparar-ventas.cjs en este mismo refresco): cifras que no son un precio habitual de su oferta, que la hoja subio respecto al
# comprobante o con el punto decimal perdido. No cuentan en la facturacion ni en el ROAS hasta que alguien las confirme (data/montos-confirmados.json). Solo vale si el archivo tiene menos de 3 horas:
# uno viejo se ignora (la venta cuenta como siempre) para que una lista congelada no aparte ventas que ya se confirmaron o corrigieron.
# Desde 2026-11-12 la lectura y la llave viven en _ventas-comun.ps1 (Import-VentasPorRevisar, Get-ClaveRevision), compartidas con Cuadre-Hojas.ps1; y la lista trae tambien las ventas con
# COMPROBANTE DE OTRO DIA (solo Colombia), que se apartan igual pero con su propio motivo en la cuarentena. Por pais: llave -> motivo.
$script:EnRevision = Import-VentasPorRevisar (Join-Path $DataDir 'montos-en-revision.json')
$script:IvaQuitado = 0          # ventas de ZapData de Mexico contadas sin los 17 pesos de IVA (ver Parse-VeTab)
$script:EnRevisionFuera = @{}
$script:RecibosFuera = @{}

# ---------------------------------------------------------------------------
# Sheet parsing
# ---------------------------------------------------------------------------

function Parse-VeTab([string]$csvPath, [string]$offer) {
    $rows = Import-Csv $csvPath
    # Que la pestana es de ZapData (hora, anuncio y metodo de pago sacados del registro del webhook; IVA de Mexico) lo dice la etiqueta O el ARCHIVO (col_zapdata_x.csv,
    # ve_xlsx_zapdata_x.csv): en Ecuador la pestana 'zapdata dinámicas mujer' se muestra como «Dinamicas Mujer» (alias de $ETIQUETAS) y sigue siendo de ZapData.
    $esZap = ($offer -match '(?i)^zapdata') -or ([IO.Path]::GetFileName($csvPath) -match '(?i)^[a-z]+_(?:xlsx_)?zapdata_')
    $mapaCtwa = $script:SrcPorCtwa[$script:PaisActual]
    $usaLog = ($mapaCtwa -and $mapaCtwa.Count -gt 0 -and $esZap)
    $orders = New-Object System.Collections.Generic.List[object]
    $quarantined = New-Object System.Collections.Generic.List[object]
    foreach ($r in $rows) {
        # Venta de PRUEBA del equipo (ver Test-VentaDePrueba en _ventas-comun.ps1): se deja en 0, o sea fuera. No pasa por la cuarentena: no es un dato ilegible, es una prueba.
        if (Test-VentaDePrueba $r) {
            $script:PruebasFuera[$script:PaisActual] = 1 + [int]$script:PruebasFuera[$script:PaisActual]
            $script:PruebasDetalle.Add(("{0} {1}: {2}, {3}, pago {4}" -f $script:PaisActual, $offer, ([string]$r.NOMBRE).Trim(), [string]$r.FECHA, [string]$r.'PAGO RECIBIDO'))
            # Para la guia de /fabrica: el dia (AAAA-MM-DD, '' si la fecha no se lee) y la pestaña por su archivo (col_zapdata_embarazo) y por su nombre en el tablero (Zapdata Embarazo). Nada mas.
            $diaPrueba = ''
            try { $dp = Convert-SaleDate ([string]$r.FECHA); if ($dp) { $diaPrueba = $dp.ToString('yyyy-MM-dd') } } catch { $diaPrueba = '' }
            $script:PruebasLista.Add([PSCustomObject]@{ p = [string]$script:PaisActual; o = [string]$offer; k = [IO.Path]::GetFileNameWithoutExtension($csvPath); f = $diaPrueba })
            continue
        }
        $rawPostId = [string]$r.'POST ID'
        $rawMetodo = [string]$r.'MÉTODO DE PAGO'
        $postId = $null
        if (Test-PostIdShape $rawPostId) {
            $postId = Get-CleanPostId $rawPostId
        } elseif (Test-PostIdShape $rawMetodo) {
            # Conservas-style column swap: header says POST ID | MÉTODO DE PAGO but the
            # actual values are swapped. Detect by content shape, not header position.
            $postId = Get-CleanPostId $rawMetodo
        } elseif (Test-LinkShape $rawPostId) {
            # ZapData: no hay Post ID numerico, pero el link corto agrupa las ventas del
            # mismo anuncio. Se usa tal cual -no cruza con gasto de Facebook, pero permite
            # ver la facturacion por anuncio en vez de perderla en "sin Post ID".
            $postId = Get-CleanLink $rawPostId
        }
        $date = Convert-SaleDate ([string]$r.FECHA)
        $amount = Convert-Amount ([string]$r.'PAGO RECIBIDO')
        $nombre = [string]$r.NOMBRE
        # Una venta con fecha posterior a hoy no existe todavia. Se aisla igual que un
        # monto imposible, en vez de dejarla entrar y ensuciar todo lo que se calcule
        # despues.
        #
        # diciembre de 2026. El archivo esta en orden cronologico y el bloque rompe la
        # secuencia -la fila anterior dice 28/12/2025 y la siguiente 1/1/2026-, o sea que
        # eran de diciembre de 2025 y a alguien se le adelanto el año tres dias. Colgadas
        # cuatro meses en el futuro no aparecian en ningun mes, pero estiraban el eje de
        # todos los graficos hasta diciembre y dejaban un vacio plano al final.
        $esFutura = ($null -ne $date) -and ($date.Date -gt (Get-Date).Date)
        if ($null -eq $date -or $null -eq $amount -or $amount -le 0 -or $esFutura) {
            $motivo = if ($null -eq $date) { 'unparseable date' }
                      elseif ($esFutura) { 'fecha futura' }
                      else { 'unparseable/non-positive amount' }
            $quarantined.Add([PSCustomObject]@{
                offer  = $offer
                nombre = $nombre
                fecha  = [string]$r.FECHA
                pago   = [string]$r.'PAGO RECIBIDO'
                reason = $motivo
            })
            continue
        }
        # Monto en revision (ver arriba): se aparta igual que un monto imposible, con su motivo a la vista en la cuarentena. La llave es la misma que arma reparar-ventas.cjs (claveRevision).
        if ($script:EnRevision.ContainsKey($script:PaisActual)) {
            $motivoRev = Get-MotivoPorRevisar $script:EnRevision[$script:PaisActual] $r $date
            if ($motivoRev) {
                $script:EnRevisionFuera[$script:PaisActual] = 1 + [int]$script:EnRevisionFuera[$script:PaisActual]
                if ($motivoRev -eq $script:MOTIVO_RECIBO_VIEJO) { $script:RecibosFuera[$script:PaisActual] = 1 + [int]$script:RecibosFuera[$script:PaisActual] }
                $quarantined.Add([PSCustomObject]@{
                    offer  = $offer
                    nombre = $nombre
                    fecha  = [string]$r.FECHA
                    pago   = [string]$r.'PAGO RECIBIDO'
                    reason = $motivoRev
                })
                continue
            }
        }
        # IVA de Mexico (ver Get-PagoSinIvaMexico en _ventas-comun.ps1): 107 cuenta como 90 aunque la hoja todavia no este corregida. Va despues de la llave de los montos en revision, que es la celda tal cual.
        if ($script:PaisActual -eq 'mexico' -and $esZap) {
            $sinIva = Get-PagoSinIvaMexico $amount
            if ($sinIva -ne $amount) { $amount = $sinIva; $script:IvaQuitado++ }
        }
        # ZapData sin ID numerico en la hoja: se busca su CTWA_CLID en el log del webhook (ver arriba). Si esta, esa venta pasa a ser exacta.
        $horaLog = -1
        if ($usaLog) {
            $ct = ([string]$r.CTWA_CLID).Trim()
            if ($ct -and $mapaCtwa.ContainsKey($ct)) {
                $reg = $mapaCtwa[$ct]
                $horaLog = [int]$reg.h
                if ($reg.s -and ((-not $postId) -or (Test-LinkShape $postId))) {
                    $postId = [string]$reg.s
                    $script:SrcRecuperados[$script:PaisActual] = 1 + [int]$script:SrcRecuperados[$script:PaisActual]
                }
            }
        }
        # ex = 1: el POST ID de la hoja ya era un ID numerico de anuncio (ClicChat, o ZapData con source_id desde que el webhook lo
        # guarda), no un enlace que despues se resolvio a un anuncio probable. Es lo unico que se puede llamar exacto por anuncio.
        $fila = [ordered]@{
            offer  = $offer
            date   = $date.ToString('yyyy-MM-dd')
            amount = [math]::Round($amount, 2)
            postId = $postId
        }
        if ($postId -and -not (Test-LinkShape $postId)) { $fila.ex = 1 }
        # La hora de la venta (0-23), tal como la registra la hoja: sirve para ver a que horas se vende. Una fecha sin hora (00:00:00) no la trae.
        if ($date.TimeOfDay.TotalSeconds -gt 0) { $fila.h = $date.Hour }
        elseif ($horaLog -ge 0) { $fila.h = $horaLog }       # ZapData: la hoja solo guarda la fecha; la hora sale del registro del webhook
        # pm = metodo de pago agrupado (clave corta: nequi, pago_movil, transferencia...; ver _metodos-pago.ps1). Sin dato NO se escribe: en Venezuela es mas de la mitad
        # de las ventas y la plantilla lo lee como «sin especificar». El texto original no viaja (puede traer cuentas y nombres). Nunca debe tumbar el build: si el
        # agrupador falla con un texto raro, esa venta queda sin metodo y el resto sigue igual.
        # Una venta de ZapData que la hoja dejo sin metodo se completa con el log del comprobante (cuenta a la que pago): pl = 1 dice que el metodo salio de ahi y no de la hoja.
        try {
            $pm = Get-MetodoPago (Get-TextoMetodoFila $rawMetodo $rawPostId)
            if ($pm -eq 'sin' -and $esZap) {
                $telFila = [string]$r.'NÚMERO'; if (-not $telFila) { $telFila = [string]$r.NUMERO }
                $rl = Resolve-MetodoDeLog $script:PaisActual ([string]$r.CTWA_CLID).Trim() (Get-IdentidadTel $telFila) $amount $fila.date
                if ($rl) {
                    $pm = $rl.pm
                    if ($rl.destino) { $fila.pl = 1 }
                    $script:MetCompletadas[$script:PaisActual] = 1 + [int]$script:MetCompletadas[$script:PaisActual]
                }
            }
            if ($pm -and $pm -ne 'sin') { $fila.pm = $pm }
            # El reparador de ventas (scripts/reparar-ventas.cjs) ya escribe en la hoja el banco que falta; cuando solo supo a que cuenta pago el cliente lo marca al final con
            # "(cuenta destino)". Esa marca cuenta igual que la completada aqui con el log: pl = 1.
            if ($fila.pm -and $rawMetodo -match '\(cuenta destino\)\s*$') { $fila.pl = 1 }
        } catch { }
        $orders.Add([PSCustomObject]$fila)
    }
    return @{ orders = $orders; quarantined = $quarantined }
}

function Parse-MexicoTab([string]$csvPath, [string]$offer) {
    return Parse-VeTab -csvPath $csvPath -offer $offer
}

# Cuantas ventas quedaron aisladas y por que, para que el tablero pueda decirlo.
#
# de los siete paises estaban saliendo de todos los calculos sin una linea en pantalla
# que lo dijera. Un total que no cuadra con la hoja y ninguna pista de por que.
#
# Va el desglose y no solo el numero, porque las causas piden cosas distintas: una fecha
# futura es un digito mal tecleado y se arregla en la hoja; un monto que no se pudo leer
# puede ser una fila de basura o una venta de verdad con el formato cambiado.
function Resumir-Cuarentena($lista) {
    $r = [ordered]@{}
    foreach ($q in $lista) {
        $k = [string]$q.reason
        if (-not $k) { $k = 'desconocido' }
        if ($r.Contains($k)) { $r[$k] = $r[$k] + 1 } else { $r[$k] = 1 }
    }
    return $r
}

# ---------------------------------------------------------------------------
# Windsor spend
# ---------------------------------------------------------------------------

# $accountIds takes an array so a country can span multiple Facebook ad accounts
# (Colombia runs 4 separate accounts, not 1).
#
# The divisor that turns an account's spend into USD is chosen PER ROW from the
# account's own currency, because a country can mix them: Argentina runs one ARS
# account beside two USD ones, and one country-wide rate would be wrong for whichever
# half doesn't match. $localCurrencyDivisor stays as the fallback for rows that predate
# the account_currency field (old Windsor data).
function Divisor-DeFila($row, [double]$fallback, $fxRates) {
    $cur = [string]$row.account_currency
    if (-not $cur -or $cur -eq 'USD') { if ($cur -eq 'USD') { return 1.0 } else { return $fallback } }
    $r = $fxRates.$cur
    if ($r -and [double]$r -gt 0) { return [double]$r }
    return $fallback
}
function Get-SpendByAdId($windsorRows, [string[]]$accountIds, [double]$localCurrencyDivisor = 1.0, $fxRates = $null) {
    $map = @{}
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        if ([string]::IsNullOrWhiteSpace($adId)) { continue }
        $spend = 0.0
        [double]::TryParse([string]$row.spend, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$spend) | Out-Null
        $spend = $spend / (Divisor-DeFila $row $localCurrencyDivisor $fxRates)
        if ($map.ContainsKey($adId)) { $map[$adId] += $spend } else { $map[$adId] = $spend }
    }
    return $map
}

# Spend per (ad, calendar day) — NOT collapsed into a lifetime total. The frontend
# needs this to attribute spend to the day it actually happened; summing an ad's
# whole-history spend into every day it happened to also get an order massively
# overstated daily figures (confirmed against real data: reported ~$73 actual vs.
# added to that single day just because each had one order that day).
# ZapData no conoce el ad_id: su webhook solo llega al POST detras del anuncio (sigue el
# link corto hasta la URL de Facebook y saca el numero de ahi, que es el story_fbid del
# post usado como creativo). Un mismo post puede estar detras de varios anuncios con
# gasto distinto, asi que hace falta esta tabla -por creative.effective_object_story_id,
# formato "PAGEID_POSTID"- para traducir ese numero al ad_id real antes de cruzar contra
# el gasto. Sin esto esas ventas nunca calzan: el POST ID que llega no es una clave que
# exista en el mapa de gasto, que esta indexado por ad_id.
function Get-AdIdByStoryId($windsorRows, [string[]]$accountIds) {
    $map = @{}
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        if ([string]::IsNullOrWhiteSpace($adId)) { continue }
        $story = [string]$row.object_story_id
        if ($story -and $story -match '_') {
            $postPart = $story.Substring($story.IndexOf('_') + 1)
            if ((Test-PostIdShape $postPart) -and -not $map.ContainsKey($postPart)) { $map[$postPart] = $adId }
        }
        # Un mismo anuncio puede tener colocacion en Facebook Y en Instagram, cada una con
        # su propio post: object_story_id es el de Facebook, este es el de Instagram. Quien
        # haga clic desde Instagram nunca va a traer un numero que calce con el primero.
        $ig = Normalizar-Enlace ([string]$row.instagram_permalink_url)
        if ($ig -and -not $map.ContainsKey($ig)) { $map[$ig] = $adId }
    }
    return $map
}

# Anuncios que hay detras de cada VIDEO, con su gasto por dia.
#
# Un clic hecho desde un reel no trae el post del anuncio: trae el ID del VIDEO, porque en un
# reel el id de la publicacion ES el del video. Ese numero no esta ni en
# effective_object_story_id ni en el permalink de Instagram, asi que las dos tablas de arriba
#
# Un mismo video puede estar detras de varios anuncios -2 en promedio, hasta 5: el mismo
# creativo en distintos conjuntos-. Por eso el valor no es un ad_id sino todos los que lo usan,
# con lo que gasto cada uno cada dia, para poder elegir entre ellos (ver Resolve-...ViaVideoId).
function Get-AdsByVideoId($windsorRows, [string[]]$accountIds) {
    $map = @{}
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        $vid  = [string]$row.video_id
        if ([string]::IsNullOrWhiteSpace($adId) -or [string]::IsNullOrWhiteSpace($vid)) { continue }
        if (-not $map.ContainsKey($vid)) { $map[$vid] = @{} }
        if (-not $map[$vid].ContainsKey($adId)) { $map[$vid][$adId] = @{ total = 0.0; dias = @{} } }
        $gasto = [double]$row.spend
        $a = $map[$vid][$adId]
        $a.total += $gasto
        $dia = [string]$row.date
        if ($dia) { $a.dias[$dia] = [double]$a.dias[$dia] + $gasto }
    }
    return $map
}

# Mismo enlace, mismo texto: sin esto "https://www.instagram.com/p/X/" y
# "https://instagram.com/p/X" -con o sin www, con o sin la barra final- se ven como dos
# cosas distintas y el cruce falla por un detalle de formato, no porque el dato este mal.
function Normalizar-Enlace([string]$url) {
    if ([string]::IsNullOrWhiteSpace($url)) { return '' }
    return ($url.Trim().ToLowerInvariant() -replace '^https?://(www\.)?', '' -replace '/+$', '')
}

# ZapData guarda el link CORTO (fb.me/... o instagram.com/p/...), no el numero: el id
# esta al otro lado del redirect. El webhook (zapdata-webhook.gs) intenta seguirlo el, pero
# necesita que su despliegue tenga autorizado UrlFetchApp -una autorizacion de Google que
# solo el dueno de la hoja puede dar-, y mientras no la tenga el link llega tal cual. Este
# build SI tiene permiso de red de sobra -ya le habla a Facebook todo el tiempo-, asi que
# sigue el redirect el mismo: no hace falta esperar a que se autorice nada del otro lado.
$linkResueltoCache = @{}

# El redirect se lee con HttpClient y no con Invoke-WebRequest, a proposito.
#
# Invoke-WebRequest -MaximumRedirection 0 se porta distinto en Windows PowerShell 5.1 (donde
# esto se desarrollo: lanza una excepcion que trae .Response) y en pwsh 7, que es lo que corre
# CI en ubuntu: ahi con SilentlyContinue no lanza nada y devuelve $null, asi que el Location
# nunca se leia. Resultado visible: en la PC los links de ZapData se resolvian al anuncio y
# Kinesiotaping Mexico), ningun gasto les calzaba y el ROAS salia "no medible". HttpClient
# con AllowAutoRedirect=false hace lo mismo en las dos versiones.
$script:httpSinRedirect = $null
function Get-LocationHeader([string]$url) {
    if (-not $script:httpSinRedirect) {
        Add-Type -AssemblyName System.Net.Http
        $h = New-Object System.Net.Http.HttpClientHandler
        $h.AllowAutoRedirect = $false
        $c = New-Object System.Net.Http.HttpClient($h)
        $c.Timeout = [TimeSpan]::FromSeconds(15)
        [void]$c.DefaultRequestHeaders.UserAgent.ParseAdd('Mozilla/5.0 (Windows NT 10.0; Win64; x64) WindowsPowerShell')
        $script:httpSinRedirect = $c
    }
    $r = $script:httpSinRedirect.GetAsync($url).GetAwaiter().GetResult()
    if ($r.Headers.Location) { return $r.Headers.Location.ToString() }
    return ''
}

function Resolve-LinkPostId([string]$link) {
    if ($linkResueltoCache.ContainsKey($link)) { return $linkResueltoCache[$link] }
    $resultado = $link
    try {
        # Hasta 3 saltos: casi siempre el primero ya trae el numero, pero si fb.me apunta a
        # otra direccion corta se sigue un paso mas antes de rendirse.
        $paso = $link
        for ($i = 0; $i -lt 3 -and $paso; $i++) {
            $destino = Get-LocationHeader $paso
            if (-not $destino) { break }
            $nums = [regex]::Matches($destino, '\d{15,19}')
            if ($nums.Count) {
                $mejor = $nums[0].Value
                foreach ($m in $nums) { if ($m.Value.Length -ge $mejor.Length) { $mejor = $m.Value } }
                $resultado = $mejor
                break
            }
            $paso = $destino
        }
    } catch { }
    $linkResueltoCache[$link] = $resultado
    return $resultado
}

# Los links de ZapData que todavia no se resolvieron -toda venta con postId con forma de
# link, no solo las de una oferta- se siguen aqui, una vez por link distinto (el cache de
# arriba evita pedir el mismo link dos veces entre paises u ofertas).
function Resolve-LinkPostIds($orders) {
    foreach ($o in $orders) {
        if ($o.postId -and (Test-LinkShape $o.postId)) { $o.postId = Resolve-LinkPostId $o.postId }
    }
}

# Reescribe el postId de cada venta a su ad_id real cuando el numero que llego es en
# realidad el story_fbid de un post que si esta detras de un anuncio conocido. Una venta
# cargada a mano que ya trae el ad_id correcto no aparece en la tabla y se deja igual.
function Resolve-PostIdsViaStoryId($orders, $storyToAd) {
    if (-not $storyToAd.Count) { return }
    foreach ($o in $orders) {
        if (-not $o.postId) { continue }
        $clave = [string]$o.postId
        # El link de Instagram no se le sigue el redirect -no lo tiene, es su propia URL
        # final- asi que llega tal cual: se normaliza igual que las claves del mapa para
        # que el cruce no falle por www./la barra final.
        if (Test-LinkShape $clave) { $clave = Normalizar-Enlace $clave }
        $real = $storyToAd[$clave]
        if ($real) { $o.postId = $real; $o.PSObject.Properties.Remove('ex') }   # ya no es el ID que trajo la hoja
    }
}

# Lo que quedo sin cruzar despues del story_fbid: si el numero es el video de algun anuncio,
# la venta se le carga a un anuncio de ese video.
#
# Con VARIOS anuncios sobre el mismo video no hay dato que diga cual fue: el clic trae el
# video, no el anuncio. Se elige el que MAS gasto ese dia -es el que mas impresiones repartio
# y, por tanto, el mas probable-; si ninguno gasto ese dia, el que mas gasto en total; y si
# empatan, el ad_id menor, para que dos corridas del mismo dato den el mismo resultado. Es una
# estimacion y se dice asi en el tablero: la alternativa era dejar la venta sin anuncio, que
# es una respuesta segura pero siempre incompleta.
function Resolve-PostIdsViaVideoId($orders, $videoAds) {
    if (-not $videoAds.Count) { return 0 }
    $cargadas = 0
    foreach ($o in $orders) {
        if (-not $o.postId) { continue }
        $clave = [string]$o.postId
        if (-not $videoAds.ContainsKey($clave)) { continue }
        $candidatos = $videoAds[$clave]
        $elegido = $null; $mejorDia = -1.0; $mejorTotal = -1.0
        foreach ($adId in ($candidatos.Keys | Sort-Object)) {
            $a = $candidatos[$adId]
            $dia = [double]$a.dias[[string]$o.date]
            if (($dia -gt $mejorDia) -or ($dia -eq $mejorDia -and $a.total -gt $mejorTotal)) {
                $elegido = $adId; $mejorDia = $dia; $mejorTotal = $a.total
            }
        }
        if ($elegido) { $o.postId = $elegido; $o.PSObject.Properties.Remove('ex'); $cargadas++ }
    }
    return $cargadas
}

function Get-SpendByAdIdDate($windsorRows, [string[]]$accountIds, [double]$localCurrencyDivisor = 1.0, $fxRates = $null) {
    $map = @{}
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        if ([string]::IsNullOrWhiteSpace($adId)) { continue }
        $date = [string]$row.date
        if ([string]::IsNullOrWhiteSpace($date)) { continue }
        $spend = 0.0
        [double]::TryParse([string]$row.spend, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$spend) | Out-Null
        $spend = $spend / (Divisor-DeFila $row $localCurrencyDivisor $fxRates)
        if (-not $map.ContainsKey($adId)) { $map[$adId] = @{} }
        if ($map[$adId].ContainsKey($date)) { $map[$adId][$date] += $spend } else { $map[$adId][$date] = $spend }
    }
    return $map
}

# El embudo de mensajeria por anuncio y por fecha: cuantas conversaciones arrancaron,
# cuantas recibieron primera respuesta y cuantas pasaron de 2 y de 3 mensajes.
#
# Mismo molde que el gasto por fecha, para que la pagina lo filtre con el mismo rango y los
# mismos anuncios de la oferta elegida y los dos numeros hablen siempre del mismo recorte.
#
# Se guarda como array de cuatro y no como objeto con nombres: son cuatro cifras por
# anuncio y por dia, y repetir "conversaciones/respuestas/dos/tres" en cada entrada
# multiplica por cuatro un archivo que ya pesa nueve megas. Y solo entran las filas con
# alguna conversacion: una campana de trafico no genera ninguna y ocuparia lo mismo que una
# que si, para decir cero.
# Impresiones y clics por anuncio y dia: [impresiones, clics]. Alimentan CTR, CPM y CPC. Solo dias con impresiones. Son TODOS los clics que cuenta Meta
# (no solo los del enlace): el historico se conserva del archivo anterior y no trae el campo de clics en el enlace, asi que usarlo mezclaria definiciones.
function Get-ImpByAdIdDate($windsorRows, [string[]]$accountIds) {
    $map = @{}
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        if ([string]::IsNullOrWhiteSpace($adId)) { continue }
        $date = [string]$row.date
        if ([string]::IsNullOrWhiteSpace($date)) { continue }
        $im = [double](0 + $row.impressions)
        if ($im -le 0) { continue }
        $cl = [double](0 + $row.clicks)
        if (-not $map.ContainsKey($adId)) { $map[$adId] = @{} }
        if ($map[$adId].ContainsKey($date)) {
            $v = $map[$adId][$date]
            $map[$adId][$date] = [double[]]@(([double]$v[0] + $im), ([double]$v[1] + $cl))
        } else {
            $map[$adId][$date] = [double[]]@($im, $cl)
        }
    }
    return $map
}

function Get-ConvByAdIdDate($windsorRows, [string[]]$accountIds) {
    $map = @{}
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        if ([string]::IsNullOrWhiteSpace($adId)) { continue }
        $date = [string]$row.date
        if ([string]::IsNullOrWhiteSpace($date)) { continue }
        # Las filas guardadas antes de que el fetcher trajera 'actions' no tienen estos
        # campos. Cero, no error: son dias reales sin el dato, no dias con cero.
        $c  = [double](0 + $row.conv)
        $r  = [double](0 + $row.conv_resp)
        $d2 = [double](0 + $row.conv_d2)
        $d3 = [double](0 + $row.conv_d3)
        if ($c -le 0 -and $r -le 0 -and $d2 -le 0 -and $d3 -le 0) { continue }
        # [double[]] y no @(): con @() PowerShell decide el tipo segun lo que le entre, y al
        # acumular guardaba un array DENTRO del array. La suma reventaba con "Object[] does
        # not contain a method named op_Addition", y solo cuando un mismo anuncio y dia se
        # acumulan — una rama que no se ejecuto hasta que Mexico volvio a traer todos sus
        # datos. Declarando el tipo, la forma es la misma se acumule una vez o veinte.
        if (-not $map.ContainsKey($adId)) { $map[$adId] = @{} }
        if ($map[$adId].ContainsKey($date)) {
            $v = $map[$adId][$date]
            $map[$adId][$date] = [double[]]@(
                ([double]$v[0] + $c), ([double]$v[1] + $r),
                ([double]$v[2] + $d2), ([double]$v[3] + $d3))
        } else {
            $map[$adId][$date] = [double[]]@($c, $r, $d2, $d3)
        }
    }
    return $map
}

# Compras que META atribuye a cada anuncio, por dia: [cantidad, valor en USD].
#
# Es la fuente de las ventas por anuncio de ZapData. Su webhook no dice de que anuncio salio
# la venta -solo un link o un video, que se adivina-, pero el evento de compra que manda a
# Meta lleva el ctwa_clid del clic, y Meta lo asigna al anuncio exacto. Cobertura contra la
#
# El valor se pasa a USD con el MISMO divisor que el gasto (Divisor-DeFila). Asi el ROAS de un
# anuncio -valor / gasto- es exacto: el divisor se cancela y no hay tasa de cambio de por medio.
# Solo entran filas con alguna compra, por la misma razon que en el embudo: una fila sin
# ventas ocuparia lo mismo que una con, para decir cero.
#
# LA MONEDA DEL VALOR NO SIEMPRE ES LA DE LA CUENTA. Meta convierte el valor de la compra a la
# moneda de la cuenta SOLO si el evento declara su moneda. Colombia (cuenta en COP, ventas en COP)
# y Mexico (cuenta en USD, evento en MXN, Meta lo convierte) salen bien, pero Venezuela mando
# 257x. Como no hay un campo que diga cual de los dos casos es, se decide con datos: se lee el
# valor de las dos maneras y se queda la que deje el ticket promedio mas cerca del de la hoja
# del mismo pais ($ticketUsd, la mediana de sus pedidos en USD). Si la moneda de la cuenta es la
# de venta, las dos lecturas son la misma y no cambia nada.
# Solo campanas de ZapData -"ZAPDATA" o "ZAP" como palabra, igual que esCampanaZap en la
# plantilla-: son las unicas cuyas compras usa el tablero, y las unicas donde la moneda del valor
# puede venir mal. Las de otras campanas traen valores de otra escala -en Peru salian tickets de
# CLICCHAT escrito a proposito gana sobre un "ZAP #n" que quedo de duplicar la campana de ZapData (2026-10-23: "ZAP #2 | DINAMICAS | 20 OCT | ... | CLICCHAT"): es de ClicChat,
# y ClicChat se mide con la hoja (su Post ID es el ad_id), no con las compras de Meta. Misma regla que esCampanaZap de la plantilla; la prueba es scripts/probar-canal-clicchat.cjs.
function Test-CampanaZap([string]$campana) {
    if ($campana -match '(?i)\b(?:CLICC?\s*-?\s*CHAT|CLICHAT)\b') { return $false }
    # ZAP y ZAPD son abreviaturas de ZapData ("ZAPD WPP #2 | LEY DE MURPHY | 4 SEP"): sin ZAPD, las compras de Meta de esa campana no entraban.
    return ($campana -match '(?i)ZAPDATA|\bZAPD?\b')
}

function Get-PurchasesByAdIdDate($windsorRows, [string[]]$accountIds, [double]$localCurrencyDivisor = 1.0, $fxRates = $null, [double]$saleDivisor = 0.0, [double]$ticketUsd = 0.0, [string]$etiqueta = '') {
    $map = @{}
    $usarMonedaVenta = $false
    if ($saleDivisor -gt 0 -and $ticketUsd -gt 0) {
        $cnt = 0.0; $lecturaCuenta = 0.0; $lecturaVenta = 0.0
        foreach ($row in $windsorRows) {
            if ($accountIds -notcontains [string]$row.account_id) { continue }
            if (-not (Test-CampanaZap $row.campaign)) { continue }
            $k = [double](0 + $row.purchases)
            if ($k -le 0) { continue }
            $v = [double](0 + $row.purchase_value)
            $cnt += $k
            $lecturaCuenta += $v / (Divisor-DeFila $row $localCurrencyDivisor $fxRates)
            $lecturaVenta  += $v / $saleDivisor
        }
        if ($cnt -gt 0 -and $lecturaCuenta -gt 0 -and $lecturaVenta -gt 0) {
            $distCuenta = [math]::Abs([math]::Log(($lecturaCuenta / $cnt) / $ticketUsd))
            $distVenta  = [math]::Abs([math]::Log(($lecturaVenta  / $cnt) / $ticketUsd))
            if ($distVenta -lt $distCuenta) { $usarMonedaVenta = $true }
            $script:lecturaCompras += ("{0}: ticket {1:N2} (cuenta) vs {2:N2} (venta) vs {3:N2} (hoja) -> {4}" -f $etiqueta, ($lecturaCuenta / $cnt), ($lecturaVenta / $cnt), $ticketUsd, $(if ($usarMonedaVenta) { 'moneda de venta' } else { 'moneda de la cuenta' }))
        }
    }
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        $date = [string]$row.date
        if ([string]::IsNullOrWhiteSpace($adId) -or [string]::IsNullOrWhiteSpace($date)) { continue }
        # Filas guardadas antes de que el fetcher trajera action_values no tienen estos campos.
        if (-not (Test-CampanaZap $row.campaign)) { continue }
        $n = [double](0 + $row.purchases)
        if ($n -le 0) { continue }
        $divisor = if ($usarMonedaVenta) { $saleDivisor } else { Divisor-DeFila $row $localCurrencyDivisor $fxRates }
        $valorUsd = [double](0 + $row.purchase_value) / $divisor
        if (-not $map.ContainsKey($adId)) { $map[$adId] = @{} }
        if ($map[$adId].ContainsKey($date)) {
            $v = $map[$adId][$date]
            $map[$adId][$date] = [double[]]@(([double]$v[0] + $n), ([double]$v[1] + $valorUsd))
        } else {
            $map[$adId][$date] = [double[]]@($n, $valorUsd)
        }
    }
    return $map
}

# Current on/off state + campaign name per ad, so the dashboard can tell "turned
# off already, no action needed" apart from "still spending, needs a look" and
# guess which offer an unmatched-spend ad belongs to from its campaign name.
# Windsor repeats the same status on every date row for a given ad, but takes the
# most recent row per ad_id anyway in case a status genuinely changed mid-range.
function Get-AdMetaById($windsorRows, [string[]]$accountIds) {
    $map = @{}
    $latestDate = @{}
    foreach ($row in $windsorRows) {
        if ($accountIds -notcontains [string]$row.account_id) { continue }
        $adId = [string]$row.ad_id
        if ([string]::IsNullOrWhiteSpace($adId)) { continue }
        $rowDate = [string]$row.date
        if ($latestDate.ContainsKey($adId) -and $rowDate -le $latestDate[$adId]) { continue }
        $latestDate[$adId] = $rowDate
        $map[$adId] = [ordered]@{
            adName                  = [string]$row.ad_name
            campaign                = [string]$row.campaign
            effectiveStatus         = [string]$row.effective_status
            campaignEffectiveStatus = [string]$row.campaign_effective_status
            accountId               = [string]$row.account_id
            campaignId              = [string]$row.campaign_id
            # Ad-set level, so the dashboard can roll ads up the way Ads Manager does.
            # With CBO the budget sits on the campaign and is shared across ad sets, so
            # a single ad's ROAS says little - the rolled-up numbers are the real signal.
            adsetId                 = [string]$row.adset_id
            adsetName               = [string]$row.adset_name
            adsetEffectiveStatus    = [string]$row.adset_effective_status
            isCbo                   = [bool]$row.is_cbo
            objective               = [string]$row.campaign_objective
            # Budgets so the dashboard can show and edit them without another round trip.
            campBudgetDaily         = [double]$row.campaign_budget_daily
            campBudgetLifetime      = [double]$row.campaign_budget_lifetime
            adsetBudgetDaily        = [double]$row.adset_budget_daily
            adsetBudgetLifetime     = [double]$row.adset_budget_lifetime
        }
    }
    return $map
}

# ---------------------------------------------------------------------------
# Load raw inputs
# ---------------------------------------------------------------------------

Write-Output "Loading raw data from $DataDir ..."

# Mexico se carga mas abajo, junto a los demas paises: sus pestañas se descubren en disco
# con Get-TabsFor, que todavia no esta definida a esta altura del archivo. Antes se leia
# aqui un unico 'mexico.csv' con la oferta 'Dinamicas Mujer' escrita a mano, y esa linea
# era la razon de que CUPCAKES, AIRFRYER y LEY DE MORPHY no existieran en el tablero.

# Venezuela's tabs are discovered from the files on disk rather than listed here.
# The hardcoded list said 7 tabs while the sheet had grown to 10, so Cartas de Pablo
# sold in Venezuela for weeks with its sales landing nowhere. Extracting a tab is now
# all it takes for the offer to appear.
#
# Only the label needs saying: 'dinamicas' has to read 'Dinamicas Mujer' to match the
# other countries. Anything not named here gets a title-cased version of its filename,
# which is right often enough for a new offer to be usable the day it starts.
# Canonical label per filename keyword, shared by all four countries: the same product
# must read identically everywhere, because that name is what links an offer across
# countries in the expansion analysis.
$ETIQUETAS = @{}   # archivo de pestana -> nombre que se ve. Ej.: 'col_zapdata_recetas' = 'Zapdata Recetas'
# Tabs come from the files on disk, not a fixed list. Venezuela's list said 7 while the
# sheet had grown to 10, so Cartas de Pablo sold there for weeks with its sales landing
# nowhere. Now a CSV existing is all it takes.
# No Write-Output in here: a PowerShell function's output stream joins its return value,
# so a progress line would arrive as an extra tab entry with an empty filename.
# La identidad de una pestana es su ETIQUETA, no su nombre de archivo.
#
# Dos CSV de la MISMA pestana suman cada venta dos veces. Pasa de dos formas:
#   - solo cambian mayusculas: CI corre en Linux, donde convivieron
#     ve_xlsx_cartas_de_pablo.csv y ve_xlsx_CARTAS_DE_PABLO.csv y el dashboard mostro
#   - la pestana se renombra: "DINAMICAS" paso a "DINAMICAS MUJER", el pull escribio
#     contaron de nuevo en la oferta mas grande de Guatemala.
# Comparar por la etiqueta de la primera fila de datos cubre los dos casos: si dos
# archivos dicen "Compra Realizada Dinamicas Mujer" son el mismo tab y se queda el mas
# reciente. Pestanas distintas traen etiquetas distintas y ninguna se pierde.
$duplicados = @()
function Get-TabsFor([string]$prefijo) {
    $tabs = @()
    $todos = @(Get-ChildItem $DataDir -Filter "$prefijo*.csv" -File | Sort-Object Name)

    # La regla de que archivo repite a cual vive en _ventas-comun.ps1 (Get-CsvRepetidos): Analyze-Ads.ps1 la usa igual.
    $descartados = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($rep in (Get-CsvRepetidos $todos)) {
        [void]$descartados.Add($rep.pierde)
        # Sin Write-Output aqui: lo que una funcion escribe al stream se pega a su
        # valor de retorno y llegaria como una pestana fantasma. Se reporta afuera.
        $script:duplicados += ("{0} repite {1} ({2}% de sus filas); se usa {3}" -f `
            $rep.pierde, $rep.gana, $rep.pct, $rep.gana)
    }
    $archivos = @($todos | Where-Object { -not $descartados.Contains($_.Name) } | Sort-Object Name)
    foreach ($f in $archivos) {
        $key = ($f.BaseName -replace ('^' + [regex]::Escape($prefijo)),'').ToLower()
        # Ecuador nombra sus pestanas EC_<OFERTA>_CE_L1. Sin limpiar eso, la oferta se
        # llamaba "Ec Bolis Ce L1" en el tablero y no casaba con la misma oferta de otro
        # pais, que es lo que sostiene el analisis de expansion.
        $key = $key -replace '^ec_', '' -replace '_ce_l\d+$', ''
        # El prefijo 'zapdata_' SE CONSERVA a proposito: 'col_zapdata_dinamicas.csv' es la
        # oferta "Zapdata Dinamicas", separada de "Dinamicas Mujer".
        #
        # Son el mismo producto pero no el mismo canal, y mezclarlos arruina las dos
        # lecturas: el historico de Dinamicas -meses de ClicChat- quedaria contaminado por
        # una plataforma que se estrena, y el rendimiento de ZapData quedaria escondido
        # dentro de un promedio que no es suyo. Separadas, cada canal tiene su ROAS y se
        # pueden comparar. El gasto se reparte por el nombre de la campana: las que dicen
        # ZAPDATA van a la oferta de ZapData y solo a ella.
        # Un alias puede valer solo para UN pais: su clave lleva el prefijo del pais ('ar_zapdata_dinamicas'). Se mira antes que la general porque la misma pestana puede ser
        # otra cosa en otro pais (en Colombia 'zapdata_dinamicas' ES la oferta principal y su nombre no se toca).
        $kPais = $prefijo.ToLower() + $key
        $label = if ($ETIQUETAS.ContainsKey($kPais)) { $ETIQUETAS[$kPais] } elseif ($ETIQUETAS.ContainsKey($key)) { $ETIQUETAS[$key] } else {
            (Get-Culture).TextInfo.ToTitleCase(($key -replace '_',' '))
        }
        $tabs += @{ file = $f.Name; offer = $label }
    }
    return ,$tabs
}
function Show-Tabs([string]$pais, $tabs) {
    Write-Output ("{0,-10} {1,2} pestanas: {2}" -f $pais, $tabs.Count, (($tabs | ForEach-Object { $_.offer } | Sort-Object -Unique) -join ', '))
    if ($script:duplicados.Count) {
        foreach ($d in $script:duplicados) { Write-Output ("             CSV duplicado ignorado: {0}" -f $d) }
        $script:duplicados = @()
    }
}
$veTabs = Get-TabsFor 've_xlsx_'; Show-Tabs 'Venezuela' $veTabs

# "VENTAS CLICHAT COL" — 18 tabs, several hidden, several near-empty (kept anyway;
# a tab with 0 real orders just contributes nothing, and it's one less thing to
# silently miss if it picks up volume later). Offer labels intentionally match the
# VE ones where the same product line is sold in both countries (Mundial, Conservas,
# Moldes, Dinamicas Mujer, Cupcakes) — country scoping keeps them from mixing.
$colTabs = Get-TabsFor 'col_'
# The Colombia sheet also carries a "Copia de CARTAS DE PABLO" tab, byte-for-byte
# identical to the real one. Discovery must not pick it up or those sales double-count.
$colTabs = @($colTabs | Where-Object { $_.file -notmatch '(?i)^col_Copia' }); Show-Tabs 'Colombia' $colTabs

# "Ventas ClicChat Peru" — 4 tabs, same column layout as VE/CO. Offer labels reuse the
# existing VE ones (same product lines); country scoping keeps them separate.
$peTabs = Get-TabsFor 'pe_'; Show-Tabs 'Peru' $peTabs
$gtTabs = Get-TabsFor 'gt_'; Show-Tabs 'Guatemala' $gtTabs
$mxTabs = Get-TabsFor 'mx_'; Show-Tabs 'Mexico' $mxTabs

$veOrders = New-Object System.Collections.Generic.List[object]
$veQuarantined = New-Object System.Collections.Generic.List[object]
$script:PaisActual = 'venezuela'
foreach ($t in $veTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) {
        Write-Warning "Missing $path, skipping offer $($t.offer)"
        continue
    }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    foreach ($o in $res.orders) { $veOrders.Add($o) }
    foreach ($q in $res.quarantined) { $veQuarantined.Add($q) }
    Write-Output ("  {0,-14} {1,5} orders, {2,4} quarantined  [{3}]" -f $t.offer, $res.orders.Count, $res.quarantined.Count, $t.file)
}

# Mexico, pestaña por pestaña. Mismo parser que Venezuela -Parse-MexicoTab no era mas que
# un envoltorio de Parse-VeTab- asi que no hacia falta nada propio: solo dejar de leer una
# sola hoja. Se conserva la forma { orders, quarantined } porque es lo que espera el resto
# del archivo (el snapshot, los Post ID sin anuncio y el conteo de cuarentena).
$mxOrders = New-Object System.Collections.Generic.List[object]
$mxQuarantined = New-Object System.Collections.Generic.List[object]
$script:PaisActual = 'mexico'
foreach ($t in $mxTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) {
        Write-Warning "Missing $path, skipping offer $($t.offer)"
        continue
    }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    foreach ($o in $res.orders) { $mxOrders.Add($o) }
    foreach ($q in $res.quarantined) { $mxQuarantined.Add($q) }
    Write-Output ("  {0,-20} {1,5} orders, {2,4} quarantined  [{3}]" -f "MX $($t.offer)", $res.orders.Count, $res.quarantined.Count, $t.file)
}
$mexico = @{ orders = $mxOrders; quarantined = $mxQuarantined }
Write-Output ("  {0,-14} {1,5} orders, {2,4} quarantined" -f 'Mexico', $mexico.orders.Count, $mexico.quarantined.Count)

$colOrders = New-Object System.Collections.Generic.List[object]
$colQuarantined = New-Object System.Collections.Generic.List[object]
$script:PaisActual = 'colombia'
foreach ($t in $colTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) {
        Write-Warning "Missing $path, skipping offer $($t.offer)"
        continue
    }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    foreach ($o in $res.orders) { $colOrders.Add($o) }
    foreach ($q in $res.quarantined) { $colQuarantined.Add($q) }
    Write-Output ("  {0,-20} {1,5} orders, {2,4} quarantined  [{3}]" -f "COL $($t.offer)", $res.orders.Count, $res.quarantined.Count, $t.file)
}

$peOrders = New-Object System.Collections.Generic.List[object]
$peQuarantined = New-Object System.Collections.Generic.List[object]
$script:PaisActual = 'peru'
foreach ($t in $peTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) {
        Write-Warning "Missing $path, skipping offer $($t.offer)"
        continue
    }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    foreach ($o in $res.orders) { $peOrders.Add($o) }
    foreach ($q in $res.quarantined) { $peQuarantined.Add($q) }
    Write-Output ("  {0,-20} {1,5} orders, {2,4} quarantined  [{3}]" -f "PE $($t.offer)", $res.orders.Count, $res.quarantined.Count, $t.file)
}

$gtOrders = New-Object System.Collections.Generic.List[object]
$gtQuarantined = New-Object System.Collections.Generic.List[object]
$script:PaisActual = 'guatemala'
foreach ($t in $gtTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) {
        Write-Warning "Missing $path, skipping offer $($t.offer)"
        continue
    }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    foreach ($o in $res.orders) { $gtOrders.Add($o) }
    foreach ($q in $res.quarantined) { $gtQuarantined.Add($q) }
    Write-Output ("  {0,-20} {1,5} orders, {2,4} quarantined  [{3}]" -f "GT $($t.offer)", $res.orders.Count, $res.quarantined.Count, $t.file)
}

$arTabs = Get-TabsFor 'ar_'; Show-Tabs 'Argentina' $arTabs
$arOrders = New-Object System.Collections.Generic.List[object]
$arQuarantined = New-Object System.Collections.Generic.List[object]
$script:PaisActual = 'argentina'
foreach ($t in $arTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) { Write-Warning "Missing $path, skipping offer $($t.offer)"; continue }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    foreach ($o in $res.orders) { $arOrders.Add($o) }
    foreach ($q in $res.quarantined) { $arQuarantined.Add($q) }
    Write-Output ("  {0,-20} {1,5} orders, {2,4} quarantined  [{3}]" -f "AR $($t.offer)", $res.orders.Count, $res.quarantined.Count, $t.file)
}

# Bolivia (BOB): como Argentina, el libro se descubre entero y las ventas vienen en moneda local.
$boTabs = Get-TabsFor 'bo_'; Show-Tabs 'Bolivia' $boTabs
$boOrders = New-Object System.Collections.Generic.List[object]
$boQuarantined = New-Object System.Collections.Generic.List[object]
$script:PaisActual = 'bolivia'
foreach ($t in $boTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) { Write-Warning "Missing $path, skipping offer $($t.offer)"; continue }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    foreach ($o in $res.orders) { $boOrders.Add($o) }
    foreach ($q in $res.quarantined) { $boQuarantined.Add($q) }
    Write-Output ("  {0,-20} {1,5} orders, {2,4} quarantined  [{3}]" -f "BO $($t.offer)", $res.orders.Count, $res.quarantined.Count, $t.file)
}

# Ecuador vende en dolares, asi que aqui no hay conversion que hacer: es el unico pais
# cuyas ventas y cuyo gasto estan ya en la misma moneda del tablero.
$ecTabs = Get-TabsFor 'ec_'; Show-Tabs 'Ecuador' $ecTabs
# El Sheet de Ecuador arrastra el historico completo, pero los anuncios que generaron esas
# ventas viven en OTRO Business Manager: su gasto no esta -ni puede estar- en las cuentas
# que lee este tablero. Contarlas daria un ROAS de fantasia, ventas sin un dolar de
# inversion detras. Desde agosto de 2026 la operacion corre en el BM que si leemos, asi
# que ese es el corte.
#
# Es una fecha fija y no "el mes en curso" a proposito: con un mes movil, en septiembre
# desapareceria agosto, que para entonces ya es historia buena.
$EC_DESDE = '2026-08-01'
$ecOrders = New-Object System.Collections.Generic.List[object]
$ecQuarantined = New-Object System.Collections.Generic.List[object]
$ecAntiguas = 0
$script:PaisActual = 'ecuador'
foreach ($t in $ecTabs) {
    $path = Join-Path $DataDir $t.file
    if (-not (Test-Path $path)) { Write-Warning "Missing $path, skipping offer $($t.offer)"; continue }
    $res = Parse-VeTab -csvPath $path -offer $t.offer
    $nuevas = @($res.orders | Where-Object { $_.date -ge $EC_DESDE })
    $ecAntiguas += ($res.orders.Count - $nuevas.Count)
    foreach ($o in $nuevas) { $ecOrders.Add($o) }
    foreach ($q in $res.quarantined) { $ecQuarantined.Add($q) }
    Write-Output ("  {0,-20} {1,5} orders, {2,4} quarantined  [{3}]" -f "EC $($t.offer)", $nuevas.Count, $res.quarantined.Count, $t.file)
}
$script:PaisActual = ''
$pruTot = 0; foreach ($v in $script:PruebasFuera.Values) { $pruTot += [int]$v }
if ($pruTot -gt 0) {
    Write-Output ('Ventas de PRUEBA del equipo dejadas en 0 (no cuentan como venta ni como facturacion): {0} ({1})' -f $pruTot, (($script:PruebasFuera.Keys | Sort-Object | ForEach-Object { $_ + ' ' + $script:PruebasFuera[$_] }) -join ', '))
    foreach ($d in $script:PruebasDetalle) { Write-Output ('  ' + $d) }
}
# La guia de lanzamiento de /fabrica marca sola «La venta de prueba quedó en la hoja de ventas» con esta lista (Build-Suite.ps1 la lee y la mete en la pagina). Siempre se escribe, aunque este vacia:
# con `generadoEn` la pagina sabe de que refresco es; sin el archivo, la guia dice que no lo puede confirmar. Las mas recientes primero, como mucho 300. Sin nombres, telefonos ni montos.
try {
    $vpOrden = @($script:PruebasLista | Sort-Object -Property f -Descending | Select-Object -First 300)
    $vpJson = ConvertTo-Json -InputObject ([ordered]@{ generadoEn = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); ventas = $vpOrden }) -Depth 4 -Compress
    [IO.File]::WriteAllText((Join-Path $DataDir 'ventas-de-prueba.json'), $vpJson, (New-Object System.Text.UTF8Encoding $false))
    Write-Output ('Ventas de prueba para la guia de /fabrica: {0} (data\ventas-de-prueba.json)' -f $vpOrden.Count)
} catch { Write-Output ('No se pudo escribir data\ventas-de-prueba.json: ' + $_.Exception.Message) }
$revTot = 0; foreach ($v in $script:EnRevisionFuera.Values) { $revTot += [int]$v }
if ($revTot -gt 0) {
    $recTot = 0; foreach ($v in $script:RecibosFuera.Values) { $recTot += [int]$v }
    Write-Output ('Ventas apartadas por revisar (no cuentan hasta confirmarse; las dice el reparador): {0} ({1}); de ellas, con comprobante de otro dia: {2}' -f $revTot, (($script:EnRevisionFuera.Keys | Sort-Object | ForEach-Object { $_ + ' ' + $script:EnRevisionFuera[$_] }) -join ', '), $recTot)
}
if ($script:IvaQuitado -gt 0) { Write-Output ('IVA de Mexico: {0} ventas de ZapData con el pago terminado en 7 se cuentan sin los 17 pesos de IVA (el reparador las corrige en la hoja)' -f $script:IvaQuitado) }
$metTot = 0; foreach ($v in $script:MetCompletadas.Values) { $metTot += [int]$v }
if ($metTot -gt 0) { Write-Output ('Metodo de pago: {0} ventas sin metodo en la hoja se completaron con el log del comprobante ({1})' -f $metTot, (($script:MetCompletadas.Keys | Sort-Object | ForEach-Object { $_ + ' ' + $script:MetCompletadas[$_] }) -join ', ')) }
$recTot = 0; foreach ($v in $script:SrcRecuperados.Values) { $recTot += [int]$v }
if ($recTot -gt 0) { Write-Output ('ZapData: {0} ventas con el anuncio exacto recuperado del log del webhook ({1})' -f $recTot, (($script:SrcRecuperados.Keys | Sort-Object | ForEach-Object { $_ + ' ' + $script:SrcRecuperados[$_] }) -join ', ')) }
if ($ecAntiguas) {
    Write-Output ("  Ecuador    {0} ventas anteriores al {1} fuera: sus anuncios estan en otro Business Manager" -f $ecAntiguas, $EC_DESDE)
}

# Mexico faltaba en esta lista desde que paso a leerse pestana por pestana: el 2026-11-03 una venta del 2 de noviembre con el Post ID pegado en PAGO RECIBIDO
# (N.NNN.NNN.NNN.NNN MXN) entro entera y la meta del mes en el menu marco N.NNN.NNN.NNN.NNN %. La red de abajo, antes de escribir el snapshot, atrapa al pais que falte.
$r = Remove-AmountOutliers $mxOrders  $mxQuarantined;  $mxOrders  = $r.orders; $mexico.orders = $mxOrders; if ($r.removed) { Write-Output ("  Mexico     {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }
$r = Remove-AmountOutliers $veOrders  $veQuarantined;  $veOrders  = $r.orders; if ($r.removed) { Write-Output ("  Venezuela  {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }
$r = Remove-AmountOutliers $colOrders $colQuarantined; $colOrders = $r.orders; if ($r.removed) { Write-Output ("  Colombia   {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }
$r = Remove-AmountOutliers $peOrders  $peQuarantined;  $peOrders  = $r.orders; if ($r.removed) { Write-Output ("  Peru       {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }
$r = Remove-AmountOutliers $gtOrders  $gtQuarantined;  $gtOrders  = $r.orders; if ($r.removed) { Write-Output ("  Guatemala  {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }
$r = Remove-AmountOutliers $arOrders  $arQuarantined;  $arOrders  = $r.orders; if ($r.removed) { Write-Output ("  Argentina  {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }
$r = Remove-AmountOutliers $ecOrders  $ecQuarantined;  $ecOrders  = $r.orders; if ($r.removed) { Write-Output ("  Ecuador    {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }
$r = Remove-AmountOutliers $boOrders  $boQuarantined;  $boOrders  = $r.orders; if ($r.removed) { Write-Output ("  Bolivia    {0} orden(es) con monto imposible aisladas (mediana {1:N0})" -f $r.removed, $r.median) }

# Spend and status come from the Facebook Graph API for every country, Venezuela
# included since it got its own System User token. Windsor is no longer part of the
# pipeline: by the time it was switched off it held a single row and no account the
# Graph pull was missing, so it contributed nothing but a connector that could go
# stale without anyone noticing.
#
# windsor.json is still read when present, purely so an account not yet covered by a
# token keeps contributing its history instead of dropping out of the totals. Delete
# the file and nothing breaks.
$windsorRows = New-Object System.Collections.Generic.List[object]
$windsorRaw = $null
$windsorPath = Join-Path $DataDir 'windsor.json'
if (Test-Path $windsorPath) {
    $windsorRaw = Get-Content $windsorPath -Raw | ConvertFrom-Json
    foreach ($r in $windsorRaw.result) { $windsorRows.Add($r) }
}

$scheduledByCountry = @{ mexico = @(); venezuela = @(); colombia = @(); peru = @(); guatemala = @(); argentina = @(); ecuador = @(); bolivia = @() }
# Cuentas donde el token SI puede pausar (lo comprueba Fetch-FacebookGraph con una
# escritura no-op). Sin esto el dashboard ofrecia el interruptor en cuentas de solo
# lectura y el cambio se perdia sin explicacion.
$writeAccess = [ordered]@{}
$writeReason = [ordered]@{}
# Salud de cada cuenta publicitaria, tal como la reporta Facebook. Viaja al snapshot para
# que la pagina pueda decir POR QUE dejo de llegar el gasto en vez de sugerir donde mirar.
$saludCuentas = [ordered]@{}
$graphPath = Join-Path $DataDir 'facebook-graph.json'
if (Test-Path $graphPath) {
    $graphRaw = Get-Content $graphPath -Raw | ConvertFrom-Json
    if ($graphRaw.saludCuentas) {
        foreach ($p in $graphRaw.saludCuentas.PSObject.Properties) {
            $saludCuentas[$p.Name] = [ordered]@{
                nombre  = [string]$p.Value.nombre
                estado  = [int]$p.Value.estado
                motivo  = [int]$p.Value.motivo
                # El pais lo pone el fetcher. Sin el, salud.json no puede agrupar por pais
                # y cada seccion quedaria vacia sin decir por que.
                country = [string]$p.Value.country
                # Cuenta que no se pudo leer: no hay estado que consultar porque Facebook
                # no contesto. Viaja el mensaje de la API para poder decir que pasa.
                ilegible = [bool]$p.Value.ilegible
                error    = [string]$p.Value.error
            }
        }
    }
    $graphAccounts = @($graphRaw.result | ForEach-Object { [string]$_.account_id } | Sort-Object -Unique)
    $windsorRows = New-Object System.Collections.Generic.List[object]
    if ($windsorRaw) {
        foreach ($r in $windsorRaw.result) {
            if ($graphAccounts -notcontains [string]$r.account_id) { $windsorRows.Add($r) }
        }
    }
    $heredadas = $windsorRows.Count
    foreach ($r in $graphRaw.result) { $windsorRows.Add($r) }
    Write-Output "Graph API: $($graphRaw.result.Count) filas de $($graphAccounts.Count) cuentas$(if ($heredadas) { "; $heredadas filas heredadas de windsor (cuentas sin token)" })"
    # Scheduled campaigns (future start, configured active) — carried straight through
    # to the snapshot, grouped by the country tag the fetch script attached.
    if ($graphRaw.PSObject.Properties.Name -contains 'scheduledCampaigns') {
        foreach ($s in $graphRaw.scheduledCampaigns) {
            $ck = [string]$s.country
            if ($scheduledByCountry.ContainsKey($ck)) { $scheduledByCountry[$ck] = @($scheduledByCountry[$ck]) + $s }
        }
        Write-Output "Scheduled campaigns: MX $($scheduledByCountry.mexico.Count), CO $($scheduledByCountry.colombia.Count), PE $($scheduledByCountry.peru.Count), GT $($scheduledByCountry.guatemala.Count)"
    }
    if ($graphRaw.PSObject.Properties.Name -contains 'writeAccess' -and $graphRaw.writeAccess) {
        foreach ($p in $graphRaw.writeAccess.PSObject.Properties) { $writeAccess[$p.Name] = $p.Value }
        $sinEscritura = @($writeAccess.Keys | Where-Object { $writeAccess[$_] -eq $false })
        Write-Output "Permiso de escritura: $(@($writeAccess.Keys | Where-Object { $writeAccess[$_] -eq $true }).Count) cuentas si, $($sinEscritura.Count) de solo lectura"
        # El porque, que es lo que decide donde ir a arreglarlo: 'permiso' se resuelve en
        # Business Manager y 'cuenta' no se resuelve ahi en absoluto.
        if ($graphRaw.PSObject.Properties.Name -contains 'writeReason' -and $graphRaw.writeReason) {
            foreach ($p in $graphRaw.writeReason.PSObject.Properties) { $writeReason[$p.Name] = [string]$p.Value }
            $porMotivo = @{}
            foreach ($k in $writeReason.Keys) { $m = $writeReason[$k]; $porMotivo[$m] = [int]$porMotivo[$m] + 1 }
            foreach ($m in ($porMotivo.Keys | Sort-Object)) { Write-Output "  motivo '$m': $($porMotivo[$m]) cuenta(s)" }
        }
    }
}
Write-Output "Spend/status rows total: $($windsorRows.Count)"

$fxRaw = Get-Content (Join-Path $DataDir 'fx.json') -Raw | ConvertFrom-Json

# Historial de tasas fijadas a mano, fecha -> tasa.
#
# Venezuela cambia de 850 a 890 a 920 en dos dias. Con una sola tasa para todo, subirla no
# y pasa a valer 92,39 a 920, asi que julio entero se encoge y su ROAS cae sin que nada
# hubiera pasado en julio. Con el historial, cada venta se convierte con la tasa del dia en
# que ocurrio y lo ya registrado no se mueve nunca mas.
$fxHistoria = @{}
# De donde salio cada tasa: fuente, metodo y hora de captura, por moneda y fecha.
#
# Va aparte del historial y no dentro porque la pagina convierte miles de ventas con
# $fxHistoria y ahi solo necesita el numero; la procedencia se consulta una vez, para
# escribir una linea. Mezclarlas obligaria a hacer .tasa en cada una de esas conversiones.
$fxMeta = @{}
# Cuantos dias puede tener una tasa automatica antes de que su numero deje de ser de fiar.
# Pasado ese tope el tablero no deja de convertir -quedarse sin cifras es peor- pero si lo
# dice: el gasto de Mexico enseño que un dato viejo mostrado como fresco es lo unico que no
# se puede permitir.
$fxTopeDias = 7
$ovHist = Join-Path $DataDir 'fx-overrides.json'
if (Test-Path $ovHist) {
    $ovh = Get-Content $ovHist -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($ovh.PSObject.Properties.Name -contains '_topeDias') { $fxTopeDias = [int]$ovh._topeDias }
    foreach ($p in $ovh.PSObject.Properties) {
        if ($p.Name -like '_*') { continue }
        if ($p.Value -is [psobject] -and -not ($p.Value -is [string]) -and $p.Value.PSObject.Properties.Name.Count) {
            $tabla = [ordered]@{}
            $metas = [ordered]@{}
            foreach ($f in ($p.Value.PSObject.Properties.Name | Sort-Object)) {
                $v = $p.Value.$f
                $tabla[$f] = Get-TasaDe $v
                if ($v -is [psobject] -and -not ($v -is [string]) -and ($v.PSObject.Properties.Name -contains 'tasa')) {
                    $m = [ordered]@{}
                    foreach ($campo in @('fuente','metodo','capturado','nota')) {
                        if ($v.PSObject.Properties.Name -contains $campo) { $m[$campo] = [string]$v.$campo }
                    }
                    $metas[$f] = $m
                }
            }
            $fxHistoria[$p.Name] = $tabla
            if ($metas.Count) { $fxMeta[$p.Name] = $metas }
        }
    }
}
# La tasa que regia en una fecha: la ultima anotada en o antes de ese dia. Sin historial
# -o para una fecha anterior a la primera anotacion- se cae a la tasa suelta de siempre.
function Get-FxEn([string]$moneda, [string]$fecha, [double]$porDefecto) {
    if (-not $fxHistoria.ContainsKey($moneda)) { return $porDefecto }
    $t = $fxHistoria[$moneda]
    $elegida = $porDefecto; $mejor = ''
    foreach ($f in $t.Keys) { if ($f -le $fecha -and $f -ge $mejor) { $mejor = $f; $elegida = $t[$f] } }
    return $elegida
}

# Meta de facturacion mensual por pais. Sin archivo o con ceros, la pagina no muestra
# ninguna barra: una meta inventada es peor que ninguna, porque se cumple o se incumple sola.
$metas = $null
$metasPath = Join-Path $DataDir 'metas.json'
if (Test-Path $metasPath) {
    $mj = Get-Content $metasPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $porPais = [ordered]@{}
    foreach ($p in $mj.mensual.PSObject.Properties) {
        if ($p.Name -like '_*') { continue }
        $v = [double]$p.Value
        if ($v -gt 0) { $porPais[$p.Name] = $v }
    }
    if ($porPais.Count) { $metas = [ordered]@{ moneda = [string]$mj.moneda; mensual = $porPais } }
}

# Objetivo global (USD en una ventana movil de N dias, todos los paises): la barra de arriba del dash. Sin archivo o con 0, no se dibuja.
$objetivo = $null
if ((Test-Path $metasPath) -and $mj.PSObject.Properties.Name -contains 'objetivo') {
    $ou = [double]$mj.objetivo.usd; $od = [int]$mj.objetivo.dias
    if ($ou -gt 0) { $objetivo = [ordered]@{ usd = $ou; dias = $(if ($od -ge 7 -and $od -le 90) { $od } else { 30 }) } }
}

# Costo de producto por oferta y por pais. Sin archivo o con todo vacio, la pagina no
# muestra ninguna utilidad neta: sigue con el margen sobre publicidad y lo dice. Una utilidad
# calculada con un costo inventado es peor que no tener utilidad.
$costos = $null
$costosPath = Join-Path $DataDir 'costos.json'
if (Test-Path $costosPath) {
    $cj = Get-Content $costosPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $porPais = [ordered]@{}
    foreach ($p in $cj.porPais.PSObject.Properties) {
        if ($p.Name -like '_*') { continue }
        $ofertas = [ordered]@{}
        foreach ($o in $p.Value.PSObject.Properties) {
            $v = [double]$o.Value
            if ($v -gt 0) { $ofertas[$o.Name] = $v }
        }
        if ($ofertas.Count) { $porPais[$p.Name] = $ofertas }
    }
    if ($porPais.Count) { $costos = [ordered]@{ moneda = [string]$cj._moneda; porPais = $porPais } }
}

$fxMXN = [double]$fxRaw.rates.MXN
$fxVES = [double]$fxRaw.rates.VES
$fxCOP = [double]$fxRaw.rates.COP
$fxPEN = [double]$fxRaw.rates.PEN
$fxGTQ = [double]$fxRaw.rates.GTQ
$fxARS = [double]$fxRaw.rates.ARS
$fxBOB = [double]$fxRaw.rates.BOB
Write-Output "FX: 1 USD = $fxMXN MXN, $fxVES VES, $fxCOP COP, $fxPEN PEN, $fxGTQ GTQ (as of $($fxRaw.time_last_update_utc))"

# Which ad accounts belong to each country. The list lives in data\facebook-tokens.json,
# the same file the fetcher reads, so there is ONE place to add an account.
#
# This used to be a second hardcoded copy, and the two drifted: Guatemala's account
# 9999000000000015 was added to the fetcher, its 580 spend rows were pulled, and this
# nothing in the output to suggest anything was wrong. The fallbacks below only apply if
# the tokens file cannot be read at all.
$accts = @{}
$tokFile = Join-Path $DataDir 'facebook-tokens.json'
if (Test-Path $tokFile) {
    $tk = Get-Content $tokFile -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $tk.PSObject.Properties) {
        if ($p.Name -eq '_note') { continue }
        # A country can hold several token blocks, one per Business Manager, since a
        # token only sees its own BM's accounts. Union them.
        $lista = @()
        if ($p.Value.tokens) { foreach ($b in $p.Value.tokens) { $lista += @($b.accounts) } }
        elseif ($p.Value.accounts) { $lista = @($p.Value.accounts) }
        $lista = @($lista | Where-Object { $_ } | ForEach-Object { [string]$_ } | Sort-Object -Unique)
        if ($lista.Count) { $accts[$p.Name] = $lista }
    }
}
# The fetch also discovers accounts a token can reach but that aren't listed. Trusting
# only the file would drop their spend the same way Guatemala's third account was
# dropped, so take whatever actually arrived as authoritative.
$graphFile = Join-Path $DataDir 'facebook-graph.json'
if (Test-Path $graphFile) {
    $gAll = Get-Content $graphFile -Raw | ConvertFrom-Json
    $vistas = @($gAll.result | ForEach-Object { [string]$_.account_id } | Sort-Object -Unique)
    foreach ($pais in @($accts.Keys)) {
        $nuevas = @($vistas | Where-Object { $accts[$pais] -contains $_ })
        if ($nuevas.Count) { $accts[$pais] = @($accts[$pais] + $nuevas | Sort-Object -Unique) }
    }
}
function AccountsFor([string]$pais, [string[]]$fallback) {
    # Manda lo que el token alcanzo en el ultimo pull, no lo que diga el archivo de
    # configuracion. El fetch une a la lista configurada las cuentas que el usuario del
    # sistema ve hoy en su Business Manager; sin leer eso aqui, una cuenta recien asignada
    # entraba en el pull y el build la ignoraba, asi que su gasto no aparecia en el ROAS de
    # nadie. Los Business Manager de los tokens son la fuente de verdad.
    $delPull = @()
    if ($graphRaw -and $graphRaw.accountsByCountry -and $graphRaw.accountsByCountry.$pais) {
        $delPull = @($graphRaw.accountsByCountry.$pais | ForEach-Object { ([string]$_) -replace '^act_','' })
    }
    if ($delPull.Count) {
        $conf = if ($accts.ContainsKey($pais)) { @($accts[$pais]) } else { @() }
        $union = @($delPull)
        foreach ($x in $conf) { if ($union -notcontains $x) { $union += $x } }
        return $union
    }
    if ($accts.ContainsKey($pais) -and $accts[$pais].Count) { return $accts[$pais] }
    Write-Warning "No hay cuentas de $pais en facebook-tokens.json; usando la lista de respaldo."
    return $fallback
}
$MX_ACCOUNTS = AccountsFor 'mexico'    @()
$VE_ACCOUNTS = AccountsFor 'venezuela' @()
# Colombia runs separate Facebook ad accounts, all denominated in COP (confirmed
# via Windsor's "currency" field — the original 2 accounts are USD, these are not).
$COL_ACCOUNTS = AccountsFor 'colombia' @()
# Peru's accounts are in PEN (Peruvian sol) — like Colombia's COP accounts, their spend
# must be divided by the PEN rate to reach USD.
$PE_ACCOUNTS = AccountsFor 'peru'      @()
$GT_ACCOUNTS = AccountsFor 'guatemala' @()
# Argentina mezcla monedas: una cuenta en ARS y dos en USD. La conversion se hace por
# fila segun la moneda de cada cuenta, no con una tasa unica de pais.
$AR_ACCOUNTS = AccountsFor 'argentina' @()
# Ecuador factura en dolares en sus tres cuentas, asi que no lleva divisor de moneda.
$EC_ACCOUNTS = AccountsFor 'ecuador' @()
# Bolivia factura en pesos bolivianos (BOB) y/o dolares segun la cuenta: la conversion es por fila, como en Argentina.
# Sin cuentas todavia: se agregan al bloque 'bolivia' de facebook-tokens.json (o asignandoselas al usuario del sistema).
$BO_ACCOUNTS = AccountsFor 'bolivia' @()
Write-Output ("Cuentas: MX {0}, VE {1}, CO {2}, PE {3}, GT {4}, AR {5}, EC {6}, BO {7}" -f $MX_ACCOUNTS.Count, $VE_ACCOUNTS.Count, $COL_ACCOUNTS.Count, $PE_ACCOUNTS.Count, $GT_ACCOUNTS.Count, $AR_ACCOUNTS.Count, $EC_ACCOUNTS.Count, $BO_ACCOUNTS.Count)

$mxSpendByAd = Get-SpendByAdId -windsorRows $windsorRows -accountIds $MX_ACCOUNTS -fxRates $fxRaw.rates
$veSpendByAd = Get-SpendByAdId -windsorRows $windsorRows -accountIds $VE_ACCOUNTS -fxRates $fxRaw.rates
$colSpendByAd = Get-SpendByAdId -windsorRows $windsorRows -accountIds $COL_ACCOUNTS -localCurrencyDivisor $fxCOP -fxRates $fxRaw.rates
$peSpendByAd = Get-SpendByAdId -windsorRows $windsorRows -accountIds $PE_ACCOUNTS -localCurrencyDivisor $fxPEN -fxRates $fxRaw.rates
$gtSpendByAd = Get-SpendByAdId -windsorRows $windsorRows -accountIds $GT_ACCOUNTS -fxRates $fxRaw.rates
$arSpendByAd = Get-SpendByAdId -windsorRows $windsorRows -accountIds $AR_ACCOUNTS -fxRates $fxRaw.rates
# El gasto de Ecuador se corta en la misma fecha que sus ventas. Cortar solo un lado
# dejaba el gasto de junio y julio -de la operacion vieja, en otro Business Manager-
# y el ROAS del pais salia por el suelo por comparar cosas de meses distintos.
$ecRows = @($windsorRows | Where-Object { [string]$_.date -ge $EC_DESDE })
$ecSpendByAd = Get-SpendByAdId -windsorRows $ecRows -accountIds $EC_ACCOUNTS -fxRates $fxRaw.rates
$boSpendByAd = Get-SpendByAdId -windsorRows $windsorRows -accountIds $BO_ACCOUNTS -fxRates $fxRaw.rates
Write-Output "Spend map: MX $($mxSpendByAd.Count) ad_ids, VE $($veSpendByAd.Count) ad_ids, COL $($colSpendByAd.Count) ad_ids, PE $($peSpendByAd.Count) ad_ids, GT $($gtSpendByAd.Count) ad_ids"

# Traduccion de Post ID de ZapData (story_fbid) a ad_id real, antes de que nada mas los
# use. Solo Mexico y Colombia traen object_story_id hoy (vienen de Graph API); Venezuela
# sigue por Windsor y su mapa sale vacio, sin romper nada.
Resolve-LinkPostIds $mexico.orders
Resolve-LinkPostIds $veOrders
Resolve-LinkPostIds $colOrders
Resolve-LinkPostIds $peOrders
Resolve-LinkPostIds $gtOrders
Resolve-LinkPostIds $arOrders
Resolve-LinkPostIds $ecOrders
Resolve-LinkPostIds $boOrders
Write-Output "Links de ZapData seguidos hasta su destino: $($linkResueltoCache.Count) distintos"

$mxStoryToAd = Get-AdIdByStoryId -windsorRows $windsorRows -accountIds $MX_ACCOUNTS
$veStoryToAd = Get-AdIdByStoryId -windsorRows $windsorRows -accountIds $VE_ACCOUNTS
$colStoryToAd = Get-AdIdByStoryId -windsorRows $windsorRows -accountIds $COL_ACCOUNTS
$peStoryToAd = Get-AdIdByStoryId -windsorRows $windsorRows -accountIds $PE_ACCOUNTS
$gtStoryToAd = Get-AdIdByStoryId -windsorRows $windsorRows -accountIds $GT_ACCOUNTS
$arStoryToAd = Get-AdIdByStoryId -windsorRows $windsorRows -accountIds $AR_ACCOUNTS
$ecStoryToAd = Get-AdIdByStoryId -windsorRows $ecRows -accountIds $EC_ACCOUNTS
$boStoryToAd = Get-AdIdByStoryId -windsorRows $windsorRows -accountIds $BO_ACCOUNTS
Resolve-PostIdsViaStoryId $mexico.orders $mxStoryToAd
Resolve-PostIdsViaStoryId $veOrders $veStoryToAd
Resolve-PostIdsViaStoryId $colOrders $colStoryToAd
Resolve-PostIdsViaStoryId $peOrders $peStoryToAd
Resolve-PostIdsViaStoryId $gtOrders $gtStoryToAd
Resolve-PostIdsViaStoryId $arOrders $arStoryToAd
Resolve-PostIdsViaStoryId $ecOrders $ecStoryToAd
Resolve-PostIdsViaStoryId $boOrders $boStoryToAd
Write-Output "Post ID -> ad_id via story_fbid: MX $($mxStoryToAd.Count), VE $($veStoryToAd.Count), COL $($colStoryToAd.Count), PE $($peStoryToAd.Count), GT $($gtStoryToAd.Count)"

# Lo que el story_fbid no cruzo: clics hechos desde un reel, que traen el ID del video.
$mxPorVideo  = Resolve-PostIdsViaVideoId $mexico.orders (Get-AdsByVideoId -windsorRows $windsorRows -accountIds $MX_ACCOUNTS)
$vePorVideo  = Resolve-PostIdsViaVideoId $veOrders      (Get-AdsByVideoId -windsorRows $windsorRows -accountIds $VE_ACCOUNTS)
$colPorVideo = Resolve-PostIdsViaVideoId $colOrders     (Get-AdsByVideoId -windsorRows $windsorRows -accountIds $COL_ACCOUNTS)
$pePorVideo  = Resolve-PostIdsViaVideoId $peOrders      (Get-AdsByVideoId -windsorRows $windsorRows -accountIds $PE_ACCOUNTS)
$gtPorVideo  = Resolve-PostIdsViaVideoId $gtOrders      (Get-AdsByVideoId -windsorRows $windsorRows -accountIds $GT_ACCOUNTS)
$arPorVideo  = Resolve-PostIdsViaVideoId $arOrders      (Get-AdsByVideoId -windsorRows $windsorRows -accountIds $AR_ACCOUNTS)
$ecPorVideo  = Resolve-PostIdsViaVideoId $ecOrders      (Get-AdsByVideoId -windsorRows $ecRows -accountIds $EC_ACCOUNTS)
$boPorVideo  = Resolve-PostIdsViaVideoId $boOrders      (Get-AdsByVideoId -windsorRows $windsorRows -accountIds $BO_ACCOUNTS)
Write-Output "Ventas cargadas a un anuncio via video_id: MX $mxPorVideo, VE $vePorVideo, COL $colPorVideo, PE $pePorVideo, GT $gtPorVideo, AR $arPorVideo, EC $ecPorVideo, BO $boPorVideo"

$mxSpendByAdDate = Get-SpendByAdIdDate -windsorRows $windsorRows -accountIds $MX_ACCOUNTS -fxRates $fxRaw.rates
$veSpendByAdDate = Get-SpendByAdIdDate -windsorRows $windsorRows -accountIds $VE_ACCOUNTS -fxRates $fxRaw.rates
$colSpendByAdDate = Get-SpendByAdIdDate -windsorRows $windsorRows -accountIds $COL_ACCOUNTS -localCurrencyDivisor $fxCOP -fxRates $fxRaw.rates
$peSpendByAdDate = Get-SpendByAdIdDate -windsorRows $windsorRows -accountIds $PE_ACCOUNTS -localCurrencyDivisor $fxPEN -fxRates $fxRaw.rates
$gtSpendByAdDate = Get-SpendByAdIdDate -windsorRows $windsorRows -accountIds $GT_ACCOUNTS -fxRates $fxRaw.rates
$arSpendByAdDate = Get-SpendByAdIdDate -windsorRows $windsorRows -accountIds $AR_ACCOUNTS -fxRates $fxRaw.rates
$ecSpendByAdDate = Get-SpendByAdIdDate -windsorRows $ecRows -accountIds $EC_ACCOUNTS -fxRates $fxRaw.rates
$boSpendByAdDate = Get-SpendByAdIdDate -windsorRows $windsorRows -accountIds $BO_ACCOUNTS -fxRates $fxRaw.rates

# El embudo de mensajeria no lleva moneda: son conteos, no dinero, y por eso no recibe
# divisor ni tasas -pasarle una tasa seria convertir conversaciones a dolares-.
$mxConvByAdDate  = Get-ConvByAdIdDate -windsorRows $windsorRows -accountIds $MX_ACCOUNTS
$veConvByAdDate  = Get-ConvByAdIdDate -windsorRows $windsorRows -accountIds $VE_ACCOUNTS
$colConvByAdDate = Get-ConvByAdIdDate -windsorRows $windsorRows -accountIds $COL_ACCOUNTS
$peConvByAdDate  = Get-ConvByAdIdDate -windsorRows $windsorRows -accountIds $PE_ACCOUNTS
$gtConvByAdDate  = Get-ConvByAdIdDate -windsorRows $windsorRows -accountIds $GT_ACCOUNTS
$arConvByAdDate  = Get-ConvByAdIdDate -windsorRows $windsorRows -accountIds $AR_ACCOUNTS
$ecConvByAdDate  = Get-ConvByAdIdDate -windsorRows $ecRows -accountIds $EC_ACCOUNTS
$boConvByAdDate  = Get-ConvByAdIdDate -windsorRows $windsorRows -accountIds $BO_ACCOUNTS
$mxImpByAdDate  = Get-ImpByAdIdDate -windsorRows $windsorRows -accountIds $MX_ACCOUNTS
$veImpByAdDate  = Get-ImpByAdIdDate -windsorRows $windsorRows -accountIds $VE_ACCOUNTS
$colImpByAdDate = Get-ImpByAdIdDate -windsorRows $windsorRows -accountIds $COL_ACCOUNTS
$peImpByAdDate  = Get-ImpByAdIdDate -windsorRows $windsorRows -accountIds $PE_ACCOUNTS
$gtImpByAdDate  = Get-ImpByAdIdDate -windsorRows $windsorRows -accountIds $GT_ACCOUNTS
$arImpByAdDate  = Get-ImpByAdIdDate -windsorRows $windsorRows -accountIds $AR_ACCOUNTS
$ecImpByAdDate  = Get-ImpByAdIdDate -windsorRows $ecRows -accountIds $EC_ACCOUNTS
$boImpByAdDate  = Get-ImpByAdIdDate -windsorRows $windsorRows -accountIds $BO_ACCOUNTS
Write-Output "Conversaciones: MX $($mxConvByAdDate.Count), CO $($colConvByAdDate.Count), PE $($peConvByAdDate.Count), GT $($gtConvByAdDate.Count), AR $($arConvByAdDate.Count), EC $($ecConvByAdDate.Count), BO $($boConvByAdDate.Count), VE $($veConvByAdDate.Count) anuncios con conversaciones"

# Compras que Meta atribuye a cada anuncio. Lleva moneda -es plata-, asi que recibe el mismo
# divisor y las mismas tasas que el gasto: es lo que hace exacto el ROAS por anuncio.
$script:lecturaCompras = @()
function Get-TicketMedianoUsd($orders, [double]$fx) {
    if ($fx -le 0) { return 0.0 }
    $t = @($orders | ForEach-Object { [double]$_.amount / $fx } | Where-Object { $_ -gt 0 } | Sort-Object)
    if (-not $t.Count) { return 0.0 }
    return [double]$t[[int]($t.Count / 2)]
}
$mxPurchByAdDate  = Get-PurchasesByAdIdDate -windsorRows $windsorRows -accountIds $MX_ACCOUNTS -fxRates $fxRaw.rates -saleDivisor $fxMXN -ticketUsd (Get-TicketMedianoUsd $mexico.orders $fxMXN) -etiqueta 'MX'
$vePurchByAdDate  = Get-PurchasesByAdIdDate -windsorRows $windsorRows -accountIds $VE_ACCOUNTS -fxRates $fxRaw.rates -saleDivisor $fxVES -ticketUsd (Get-TicketMedianoUsd $veOrders $fxVES) -etiqueta 'VE'
$colPurchByAdDate = Get-PurchasesByAdIdDate -windsorRows $windsorRows -accountIds $COL_ACCOUNTS -localCurrencyDivisor $fxCOP -fxRates $fxRaw.rates -saleDivisor $fxCOP -ticketUsd (Get-TicketMedianoUsd $colOrders $fxCOP) -etiqueta 'COL'
$pePurchByAdDate  = Get-PurchasesByAdIdDate -windsorRows $windsorRows -accountIds $PE_ACCOUNTS -localCurrencyDivisor $fxPEN -fxRates $fxRaw.rates -saleDivisor $fxPEN -ticketUsd (Get-TicketMedianoUsd $peOrders $fxPEN) -etiqueta 'PE'
$gtPurchByAdDate  = Get-PurchasesByAdIdDate -windsorRows $windsorRows -accountIds $GT_ACCOUNTS -fxRates $fxRaw.rates -saleDivisor $fxGTQ -ticketUsd (Get-TicketMedianoUsd $gtOrders $fxGTQ) -etiqueta 'GT'
$arPurchByAdDate  = Get-PurchasesByAdIdDate -windsorRows $windsorRows -accountIds $AR_ACCOUNTS -fxRates $fxRaw.rates -saleDivisor $fxARS -ticketUsd (Get-TicketMedianoUsd $arOrders $fxARS) -etiqueta 'AR'
$ecPurchByAdDate  = Get-PurchasesByAdIdDate -windsorRows $ecRows -accountIds $EC_ACCOUNTS -fxRates $fxRaw.rates
$boPurchByAdDate  = Get-PurchasesByAdIdDate -windsorRows $windsorRows -accountIds $BO_ACCOUNTS -fxRates $fxRaw.rates -saleDivisor $fxBOB -ticketUsd (Get-TicketMedianoUsd $boOrders $fxBOB) -etiqueta 'BO'
foreach ($l in $script:lecturaCompras) { Write-Output "Valor de las compras de Meta: $l" }
Write-Output "Compras atribuidas por Meta: MX $($mxPurchByAdDate.Count), CO $($colPurchByAdDate.Count), PE $($pePurchByAdDate.Count), GT $($gtPurchByAdDate.Count), AR $($arPurchByAdDate.Count), EC $($ecPurchByAdDate.Count), BO $($boPurchByAdDate.Count), VE $($vePurchByAdDate.Count) anuncios con compras"

$mxAdMeta = Get-AdMetaById -windsorRows $windsorRows -accountIds $MX_ACCOUNTS
$veAdMeta = Get-AdMetaById -windsorRows $windsorRows -accountIds $VE_ACCOUNTS
$colAdMeta = Get-AdMetaById -windsorRows $windsorRows -accountIds $COL_ACCOUNTS
$peAdMeta = Get-AdMetaById -windsorRows $windsorRows -accountIds $PE_ACCOUNTS
$gtAdMeta = Get-AdMetaById -windsorRows $windsorRows -accountIds $GT_ACCOUNTS
$arAdMeta = Get-AdMetaById -windsorRows $windsorRows -accountIds $AR_ACCOUNTS
$ecAdMeta = Get-AdMetaById -windsorRows $ecRows -accountIds $EC_ACCOUNTS
$boAdMeta = Get-AdMetaById -windsorRows $windsorRows -accountIds $BO_ACCOUNTS
$activeCount = ($veAdMeta.Values + $mxAdMeta.Values + $colAdMeta.Values + $peAdMeta.Values + $gtAdMeta.Values | Where-Object { $_.effectiveStatus -eq 'ACTIVE' }).Count
Write-Output "Ad metadata: MX $($mxAdMeta.Count) ad_ids, VE $($veAdMeta.Count) ad_ids, COL $($colAdMeta.Count) ad_ids, PE $($peAdMeta.Count) ad_ids, GT $($gtAdMeta.Count) ad_ids ($activeCount currently ACTIVE across all)"

# ---------------------------------------------------------------------------
# Salud de las cuentas publicitarias: el veredicto se calcula UNA vez, aqui
# ---------------------------------------------------------------------------
#
# Antes esta regla vivia duplicada: cuentaSana() en dashboard.template.html y Es-Sana en
# Verificar-Bandas.ps1, cada una con su copia de los diccionarios de estado y motivo. Con
# la pagina de salud y el aviso serian cuatro copias en dos lenguajes, y la proxima vez que
# Facebook agregue un estado, tres se quedarian viejas sin que nadie lo note. Es la misma
# leccion que dejo Cuadre-Hojas.ps1 leyendo distinto que el build: quien verifica tiene que
# leer lo mismo que lo verificado.
#
# Sana es el estado 1, MAS los estados intermedios que Facebook resuelve solo: revision de
# riesgo (7), pendiente de pago (8), periodo de gracia (9) y en cierre (100) son pasos por
# los que una cuenta sana pasa y sale sin que nadie haga nada -a veces en horas-. Marcarlos
# como "inhabilitada" convertia un tramite normal de Facebook en una alarma falsa: Venezuela
# gritaba una cuenta caida que en realidad seguia gastando bien.
#
# Solo cuentan como caida de verdad: deshabilitada (2, la banearon), impaga (3, dejo de
# pagar) y cerrada (101, ya no vuelve). Esos tres SI son "actua ahora", los demas son
# "esperate a que se resuelva sola".
#
# Una cuenta sin dato se da por sana a proposito: marcarla como caida por no haber podido
# preguntar seria inventar una averia.
$ESTADOS_CAIDA = @(2, 3, 101)
# ---------------------------------------------------------------------------
# En que moneda cobra Facebook, por pais: preguntado al dato, no declarado a mano
# ---------------------------------------------------------------------------
#
# Estaba escrito a mano pais por pais, y Argentina lo rompio: decia 'ARS' y cinco de sus
# seis cuentas facturan en USD. El tablero tomaba el total en dolares, lo multiplicaba por
#
# Ahora sale de las filas de gasto, que ya traen account_currency. Y cuando un pais mezcla
# de verdad -Argentina tiene una cuenta en ARS y cinco en USD- la respuesta correcta NO es
# elegir la mayoritaria: es que no existe una sola cifra local, y entonces se muestra USD y
# se dice por que. Inventar un total en pesos para un cobro que fue en dolares es
# exactamente lo que este tablero lleva una semana dejando de hacer.
$UMBRAL_MONEDA_UNICA = 0.95
function Get-MonedaDeGasto($filas, [string[]]$cuentas, $fxRates) {
    $porMoneda = @{}
    foreach ($row in $filas) {
        if ($cuentas -notcontains [string]$row.account_id) { continue }
        $g = 0.0
        [double]::TryParse([string]$row.spend, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$g) | Out-Null
        if ($g -le 0) { continue }
        $cur = [string]$row.account_currency
        if (-not $cur) { $cur = 'USD' }
        # son la decima parte.
        $usd = $g / (Divisor-DeFila $row 1.0 $fxRates)
        if ($porMoneda.ContainsKey($cur)) { $porMoneda[$cur] += $usd } else { $porMoneda[$cur] = $usd }
    }
    $total = 0.0; foreach ($v in $porMoneda.Values) { $total += $v }
    if ($total -le 0) { return @{ moneda = 'USD'; mixta = $false; detalle = '' } }
    $top = ($porMoneda.GetEnumerator() | Sort-Object -Property Value -Descending | Select-Object -First 1)
    if (($top.Value / $total) -ge $UMBRAL_MONEDA_UNICA) {
        return @{ moneda = [string]$top.Key; mixta = $false; detalle = '' }
    }
    $desglose = (($porMoneda.GetEnumerator() | Sort-Object -Property Value -Descending |
                  ForEach-Object { "{0} {1:P0}" -f $_.Key, ($_.Value / $total) }) -join ', ')
    return @{ moneda = 'USD'; mixta = $true; detalle = $desglose }
}

$DIAS_PARADO_TOPE = 30
# Estos textos SI llevan tilde: salen a pantalla. El archivo se guarda como UTF-8 con BOM
# -igual que Fetch-FacebookGraph.ps1- porque PowerShell 5.1 lee un .ps1 sin BOM como ANSI y
# convierte cualquier acento en mojibake. La primera version de esto escribio "Cuenta #1
# (Ã¢â‚¬Â¦171984)" en el JSON antes de que se notara.
$FB_ESTADO = @{ 1='activa'; 2='deshabilitada'; 3='impaga'; 7='en revisión de riesgo';
                8='pendiente de pago'; 9='en periodo de gracia'; 100='en cierre'; 101='cerrada' }
$FB_MOTIVO = @{ 1='por política de anuncios'; 2='por revisión de propiedad intelectual';
                3='por riesgo de pago'; 5='por revisión de anuncios';
                6='por integridad del negocio'; 7='cerrada de forma permanente' }

# Gasto por cuenta y fecha, en USD. Se arma una sola vez y sirve para el "cuanto se esta
# dejando de gastar": es lo unico que convierte "3 cuentas caidas" -un conteo que se mira-
$gastoPorCuentaFecha = @{}
foreach ($row in $windsorRows) {
    $acc = [string]$row.account_id
    $f   = [string]$row.date
    if ([string]::IsNullOrWhiteSpace($acc) -or [string]::IsNullOrWhiteSpace($f)) { continue }
    $g = 0.0
    [double]::TryParse([string]$row.spend, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$g) | Out-Null
    if ($g -le 0) { continue }
    $g = $g / (Divisor-DeFila $row 1.0 $fxRaw.rates)
    if (-not $gastoPorCuentaFecha.ContainsKey($acc)) { $gastoPorCuentaFecha[$acc] = @{} }
    if ($gastoPorCuentaFecha[$acc].ContainsKey($f)) { $gastoPorCuentaFecha[$acc][$f] += $g }
    else { $gastoPorCuentaFecha[$acc][$f] = $g }
}

# Anuncios encendidos por cuenta, mirando los adMeta de todos los paises.
$activosPorCuenta = @{}
foreach ($mapa in @($mxAdMeta, $veAdMeta, $colAdMeta, $peAdMeta, $gtAdMeta, $arAdMeta, $ecAdMeta, $boAdMeta)) {
    foreach ($m in $mapa.Values) {
        if ($m.effectiveStatus -ne 'ACTIVE') { continue }
        $acc = [string]$m.accountId
        if (-not $acc) { continue }
        if ($activosPorCuenta.ContainsKey($acc)) { $activosPorCuenta[$acc]++ } else { $activosPorCuenta[$acc] = 1 }
    }
}

# El dia de hoy EN COLOMBIA, no en el reloj del runner. CI corre en UTC, asi que desde las
# 7 p.m. de aqui el servidor ya dice manana y "dias sin gastar" saldria uno de mas. Colombia
# es UTC-5 todo el año, sin horario de verano, asi que restar cinco horas es exacto y no
# depende de que el sistema conozca el nombre de la zona -que en Windows y en Linux difiere-.
$hoyIso = [DateTime]::UtcNow.AddHours(-5).ToString('yyyy-MM-dd')
# La historia diaria de salud (la escribe Avisar-Salud.ps1 DESPUES de este build y la commitea el refresco): de ahi sale cuanto lleva caida cada cuenta. Sin el archivo, o ilegible, todas
# salen con 0 dias y NINGUNA alerta se oculta: ante la duda se avisa (ver _salud-comun.ps1).
$histSaludPath = Join-Path $DataDir 'salud-historia.json'
$histSalud = $null
if (Test-Path $histSaludPath) {
    try { $histSalud = Get-Content $histSaludPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Write-Warning ("salud-historia.json ilegible: las alertas viejas no se pueden ocultar esta corrida (" + $_.Exception.Message + ")") }
} else {
    Write-Output "Salud: no existe salud-historia.json; la antiguedad de las alertas cuenta desde hoy y ninguna se oculta."
}
try { $indiceHistSalud = New-IndiceHistoriaSalud -Historia $histSalud -Hoy $hoyIso }
catch {
    Write-Warning ("Salud: no pude armar la historia para contar dias de caida; ninguna alerta se oculta esta corrida (" + $_.Exception.Message + ")")
    $indiceHistSalud = New-Object System.Collections.Generic.List[object]
}
# El primer dia que tiene la historia: una cuenta caida "desde" ese dia puede llevar mucho mas (la historia empezo el 2026-08-20), y la pagina lo dice asi.
$historiaDesde = ''
if ($indiceHistSalud.Count) { $historiaDesde = [string]$indiceHistSalud[$indiceHistSalud.Count - 1].texto }
# Los estados de la historia que cuentan como caida: los de ESTADOS_CAIDA y el -1 de las cuentas ilegibles (Avisar-Salud anota el estado tal cual, y la ilegible lleva -1).
$estadosCaidaHist = @($ESTADOS_CAIDA) + @(-1)
foreach ($acc in @($saludCuentas.Keys)) {
    $h = $saludCuentas[$acc]
    $estado = [int]$h.estado
    # Una cuenta ciega NO esta sana, aunque su estado no sea ninguno de los caidos: es que
    # no hay estado que mirar. Sin esta condicion pasaba por buena, que es justo lo que
    # dejo a Colombia nueve dias en cero sin que nadie lo notara.
    $h['sana'] = ($ESTADOS_CAIDA -notcontains $estado) -and -not $h['ilegible']
    # Tres puntos ASCII y no el caracter de puntos suspensivos: los .ps1 de este repo no
    # llevan BOM y PowerShell 5.1 los lee como ANSI, asi que cualquier caracter fuera de
    # ASCII sale mojibake en el JSON. Es la razon por la que los comentarios de aqui no
    # llevan tildes.
    $cola = $acc.Substring([Math]::Max(0, $acc.Length - 6))
    $nom = "cuenta ...$cola"
    if ($h.nombre) { $nom = "$($h.nombre) (...$cola)" }
    if ($h['sana']) {
        $h['veredicto'] = ''
    } elseif ($h['ilegible']) {
        # El mensaje de Facebook, entero: "API access blocked" se arregla en el Business
        # Manager y un token vencido se arregla generando otro. Resumirlo a "no responde"
        # obligaba a ir al log a averiguar cual de las dos era.
        $por = if ($h['error']) { " -$($h['error'])-" } else { '' }
        $h['veredicto'] = "$nom`: NO SE PUEDE LEER$por. Su gasto no esta en ningun ROAS."
    } else {
        $e  = if ($FB_ESTADO[$estado]) { $FB_ESTADO[$estado] } else { "en estado $estado" }
        $mo = if ($FB_MOTIVO[[int]$h.motivo]) { ' ' + $FB_MOTIVO[[int]$h.motivo] } else { '' }
        $h['veredicto'] = "$nom`: $e$mo"
    }
    $h['etiqueta'] = $nom
    $h['activos']  = if ($activosPorCuenta.ContainsKey($acc)) { $activosPorCuenta[$acc] } else { 0 }

    # Cuanto gastaba esta cuenta cuando gastaba, y cuanto lleva sin hacerlo.
    #
    # El tipico sale de los ULTIMOS 7 dias con gasto, no del promedio de toda la vida: una
    # cuenta que arranco despacio en abril y termino gastando 50 al dia tiene un promedio
    # historico que no se parece a lo que gastaria hoy. Siete dias es corto para que
    # refleje el ritmo actual y largo para que un domingo flojo no lo hunda.
    $ultimo = ''; $tipico = 0.0
    if ($gastoPorCuentaFecha.ContainsKey($acc)) {
        $fechas = @($gastoPorCuentaFecha[$acc].Keys | Sort-Object)
        if ($fechas.Count) {
            $ultimo = $fechas[-1]
            $ventana = @($fechas | Select-Object -Last 7)
            $suma = 0.0
            foreach ($f in $ventana) { $suma += $gastoPorCuentaFecha[$acc][$f] }
            $tipico = [math]::Round($suma / $ventana.Count, 2)
        }
    }
    $h['ultimoGasto']   = $ultimo
    $h['gastoTipico']   = $tipico
    # Nunca menos de cero.
    #
    # Facebook fecha el gasto en la zona horaria de LA CUENTA, no en la nuestra. Argentina va
    # dos horas adelante de Colombia, asi que despues de las 10 de la noche de aqui su gasto
    # ya lleva la fecha de manana y la resta da -1. El calculo no estaba mal -ningun umbral
    # se dispara con un negativo- pero la tabla mostraba "DIAS -1", que no significa nada
    # para quien la lee. Cero es la respuesta correcta: no lleva ningun dia sin gastar.
    $dias = 0
    if ($ultimo) {
        $dias = [Math]::Max(0, (New-TimeSpan -Start ([datetime]$ultimo) -End ([datetime]$hoyIso)).Days)
    }
    $h['diasSinGastar'] = $dias
    # Solo se cuenta como parado lo de una cuenta que NO puede gastar y todavia tiene
    # anuncios encendidos. Una cuenta sana que no gasta es una decision; una caida es una
    # averia. Y sin anuncios encendidos no hay capacidad que este parada.
    #
    # Y la ventana se topa en 30 dias. Peru #3 lleva 75 dias caida: multiplicar su ritmo por
    # se vuelve alarmista y deja de servir para decidir, que es justo lo contrario de para
    # lo que se puso. Con el tope, el numero dice "esto es lo que dejaste de gastar en el
    # ultimo mes" -recuperable y accionable- en vez de arrastrar la historia entera.
    $diasCobrados = [Math]::Min($DIAS_PARADO_TOPE, [Math]::Max(1, $dias))
    $h['diasCobrados'] = $diasCobrados
    $h['paradoUsd'] = 0
    if (-not $h['sana'] -and $h['activos'] -gt 0 -and $tipico -gt 0) {
        $h['paradoUsd'] = [math]::Round($tipico * $diasCobrados, 2)
    }
}
# Cuanto lleva caida cada cuenta y si su alerta ya es vieja (dueno, 2026-10-29: las de mas de 15 dias se ocultan, son cuentas que no se van a volver a usar). El dato NO se borra: la cuenta sigue
# en `cuentas` con todo lo suyo y solo se aparta de la lista de alertas (cuentasCaidas -> cuentasOcultas). `sana` NO cambia: las reglas y el dash siguen tratando la cuenta como caida; esto solo
# decide si su alerta se muestra. Si algo falla, la alerta se queda visible y el build sigue (ver Set-AntiguedadAlertas en _salud-comun.ps1).
try {
    Set-AntiguedadAlertas -Cuentas $saludCuentas -Indice $indiceHistSalud -Hoy $hoyIso -EstadosCaida $estadosCaidaHist
} catch {
    Write-Warning ("Salud: la antiguedad de las alertas fallo y ninguna se oculta esta corrida (" + $_.Exception.Message + ")")
    foreach ($h in $saludCuentas.Values) { $h['caidaDesde'] = ''; $h['diasCaida'] = 0; $h['alertaOculta'] = $false }
}
# Suma a mano y no con Measure-Object: los valores son diccionarios ordenados y sus claves
# no son propiedades, asi que -Property paradoUsd no las ve y devuelve cero en silencio.
# La moneda de facturacion de cada pais, deducida del gasto real de esta corrida.
$monedaGasto = @{}
foreach ($par in @(@{p='mexico';a=$MX_ACCOUNTS;f=$windsorRows}, @{p='venezuela';a=$VE_ACCOUNTS;f=$windsorRows},
                   @{p='colombia';a=$COL_ACCOUNTS;f=$windsorRows}, @{p='peru';a=$PE_ACCOUNTS;f=$windsorRows},
                   @{p='guatemala';a=$GT_ACCOUNTS;f=$windsorRows}, @{p='argentina';a=$AR_ACCOUNTS;f=$windsorRows},
                   @{p='ecuador';a=$EC_ACCOUNTS;f=$ecRows}, @{p='bolivia';a=$BO_ACCOUNTS;f=$windsorRows})) {
    $monedaGasto[$par.p] = Get-MonedaDeGasto -filas $par.f -cuentas $par.a -fxRates $fxRaw.rates
}
foreach ($p in $monedaGasto.Keys) {
    $m = $monedaGasto[$p]
    if ($m.mixta) { Write-Output ("Moneda de gasto {0}: MIXTA ({1}) -> se muestra en USD" -f $p, $m.detalle) }
}

# Las ciegas entran aunque digan cero anuncios: dicen cero porque no se pudieron leer, no
# porque no tengan. Dejarlas fuera era lo que hacia que el aviso callara justo en el caso
# peor -gasto corriendo que no se ve- y contara solo las cuentas cuyo problema ya sabiamos.
$caidasConAnuncios = @($saludCuentas.Values | Where-Object { -not $_['sana'] -and ($_['activos'] -gt 0 -or $_['ilegible']) })
# Las de mas de 15 dias salen de la alerta y de la cifra de capacidad parada (se cuentan aparte, para poder decir cuantas hay ocultas). Una cuenta oculta sigue estando: solo deja de avisar.
$caidasOcultas = @($caidasConAnuncios | Where-Object { $_['alertaOculta'] })
$caidasVisibles = @($caidasConAnuncios | Where-Object { -not $_['alertaOculta'] })
$paradoTotal = 0.0
foreach ($c in $caidasVisibles) { $paradoTotal += [double]$c['paradoUsd'] }
$paradoOcultoTotal = 0.0
foreach ($c in $caidasOcultas) { $paradoOcultoTotal += [double]$c['paradoUsd'] }

# ---------------------------------------------------------------------------
# data\salud.json: lo que hay que atender hoy, en 5 KB
# ---------------------------------------------------------------------------
#
# Aparte del snapshot a proposito. dashboard-ventas.html pesa 2,7 MB porque embebe el
# snapshot entero; una pagina de salud que hiciera lo mismo serian otros 2,7 MB para
# mostrar un checklist de quince lineas. Y el aviso que corre en CI no necesita cargar
# nueve megas para preguntar "¿cayo alguna cuenta?".
#
# Un solo calculo, tres consumidores: el aviso, Verificar-Bandas y -mas adelante- la
# pagina. Es la misma regla del veredicto de arriba: quien lee no vuelve a derivar.
$saludPaises = [ordered]@{}
foreach ($pais in @('mexico','venezuela','colombia','peru','guatemala','argentina','ecuador','bolivia')) {
    $meta = switch ($pais) {
        'mexico'    { $mxAdMeta }  'venezuela' { $veAdMeta } 'colombia' { $colAdMeta }
        'peru'      { $peAdMeta }  'guatemala' { $gtAdMeta } 'argentina' { $arAdMeta }
        'ecuador'   { $ecAdMeta }
        'bolivia'   { $boAdMeta }
    }
    $conv = switch ($pais) {
        'mexico'    { $mxConvByAdDate }  'venezuela' { $veConvByAdDate } 'colombia' { $colConvByAdDate }
        'peru'      { $peConvByAdDate }  'guatemala' { $gtConvByAdDate } 'argentina' { $arConvByAdDate }
        'ecuador'   { $ecConvByAdDate }
        'bolivia'   { $boConvByAdDate }
    }
    $sano = 0; $crudo = 0; $caidas = New-Object System.Collections.Generic.List[string]
    foreach ($m in $meta.Values) {
        if ($m.effectiveStatus -ne 'ACTIVE') { continue }
        $crudo++
        $acc = [string]$m.accountId
        if (-not $acc -or -not $saludCuentas.Contains($acc) -or $saludCuentas[$acc]['sana']) { $sano++ }
        elseif (-not $caidas.Contains($acc)) { $caidas.Add($acc) }
    }
    # Una cuenta que no se pudo leer no aporta un solo anuncio a $meta, asi que el bucle de
    # arriba no la ve jamas y el pais salia "sin cuentas caidas" con cinco cuentas mudas.
    foreach ($acc in $saludCuentas.Keys) {
        if ($saludCuentas[$acc]['country'] -ne $pais) { continue }
        if (-not $saludCuentas[$acc]['ilegible']) { continue }
        if (-not $caidas.Contains($acc)) { $caidas.Add($acc) }
    }
    $ultimo = ''
    foreach ($acc in $saludCuentas.Keys) {
        if ($saludCuentas[$acc]['country'] -ne $pais) { continue }
        $u = [string]$saludCuentas[$acc]['ultimoGasto']
        if ($u -gt $ultimo) { $ultimo = $u }
    }
    $convTotal = 0.0
    foreach ($ad in $conv.Keys) { foreach ($f in $conv[$ad].Keys) { $convTotal += [double]$conv[$ad][$f][0] } }
    # Las alertas viejas (mas de 15 dias caida) se apartan de la lista que leen el aviso, la pagina y Verificar-Bandas: siguen en `cuentas` y se listan en cuentasOcultas. Lo parado del
    # pais es la suma de SUS cuentas visibles, para que el invariante de Verificar-Bandas (parado = suma de cuentasCaidas) siga cuadrando.
    $visibles = New-Object System.Collections.Generic.List[string]
    $ocultas  = New-Object System.Collections.Generic.List[string]
    foreach ($acc in $caidas) {
        if ($saludCuentas[$acc]['alertaOculta']) { $ocultas.Add($acc) } else { $visibles.Add($acc) }
    }
    $parado = 0.0
    foreach ($acc in $visibles) { $parado += [double]$saludCuentas[$acc]['paradoUsd'] }
    # El if se saca del literal a proposito: PowerShell 5.1 no acepta un if como expresion
    # dentro de un hashtable, y el error que da -"los tipos de argumentos no coinciden"-
    # apunta a la linea equivocada.
    # -1 significa "este pais no tiene ni un dia de gasto", que es distinto de "gasto hoy".
    # Con gasto, nunca baja de cero: Facebook fecha en la zona de la cuenta y Argentina va
    # adelante de Colombia, asi que de noche su ultimo gasto lleva la fecha de manana.
    $diasPais = -1
    if ($ultimo) {
        $diasPais = [Math]::Max(0, (New-TimeSpan -Start ([datetime]$ultimo) -End ([datetime]$hoyIso)).Days)
    }
    # Las claves se ponen una a una y no como literal. Un [ordered]@{} que lleve dentro una
    # lista generica VACIA revienta con "los tipos de argumentos no coinciden", y el error
    # apunta a una linea que no es. Guatemala y Ecuador no tienen cuentas caidas, asi que la
    # lista vacia es el caso normal, no el raro.
    $fila = [ordered]@{}
    $fila['ultimoGasto']    = $ultimo
    $fila['diasSinGastar']  = $diasPais
    $fila['activosSanos']   = $sano
    $fila['activosCrudos']  = $crudo
    $fila['conversaciones'] = [int]$convTotal
    $fila['cuentasCaidas']  = [string[]]$visibles.ToArray()
    $fila['cuentasOcultas'] = [string[]]$ocultas.ToArray()
    $fila['paradoUsd']      = [math]::Round($parado, 2)
    $saludPaises[$pais] = $fila
}
# Que datos de negocio faltan por cargar. Sin esto, las funciones que dependen de un JSON
# vacio -metas, costos- quedan construidas y dormidas, y nadie sabe que existen ni que las
# enciende.
$faltantes = New-Object System.Collections.Generic.List[object]
if (-not $metas) {
    $faltantes.Add([ordered]@{ archivo='data/metas.json'; que='Meta de facturacion mensual por pais, en USD';
                               desbloquea='La barra del mes con cuanto falta, a que ritmo y la proyeccion de cierre' })
}
if (-not $costos) {
    $faltantes.Add([ordered]@{ archivo='data/costos.json'; que='Costo de producto por oferta y pais, por PEDIDO (los pedidos no traen cantidad)';
                               desbloquea='Utilidad neta de verdad, en vez de margen sobre publicidad' })
}

# El secreto de CI con una copia vieja del archivo de tokens.
#
# Es el fallo que mas veces se nos ha colado, y siempre igual: se agrega una cuenta al
# archivo local, el tablero de aqui la ve, y el de produccion sigue mirando las de antes sin
# decir nada. Mexico estuvo dias mostrando el gasto hasta el 14 cuando la cuenta nueva ya
# habia gastado hasta el 19.
#
# Los IDs de cuenta no son secretos -salen en el propio tablero- asi que la lista de las que
# el archivo conoce vive en el repo, y aqui se compara contra las que esta corrida alcanzo de
# verdad. Si el repo espera seis y solo se ven cuatro, la copia de CI quedo vieja.
$espPath = Join-Path $DataDir 'cuentas-esperadas.json'
if (Test-Path $espPath) {
    $esp = Get-Content $espPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $vistas = @()
    if ($graphRaw -and $graphRaw.accountsByCountry) {
        foreach ($p in $graphRaw.accountsByCountry.PSObject.Properties) { $vistas += @($p.Value | ForEach-Object { [string]$_ }) }
    }
    $faltan = New-Object System.Collections.Generic.List[string]
    foreach ($p in $esp.porPais.PSObject.Properties) {
        foreach ($id in @($p.Value)) {
            if ($vistas -notcontains [string]$id) { $faltan.Add("$($p.Name)/$id") }
        }
    }
    if ($faltan.Count) {
        Write-Output ("Secreto desactualizado: se esperaban cuentas que esta corrida no vio -> {0}" -f ($faltan -join ', '))
        $faltantes.Add([ordered]@{
            archivo    = 'Secreto FACEBOOK_TOKENS (GitHub)'
            que        = "$($faltan.Count) cuenta(s) que el repo conoce no se pudieron leer: $($faltan -join ', ')"
            desbloquea = 'El gasto y las conversaciones de esas cuentas. Vuelve a subir data/facebook-tokens.json al secreto.'
        })
    }
}
$saludDoc = [ordered]@{}
$saludDoc['generadoEn']  = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')
$saludDoc['dia']         = $hoyIso
$saludDoc['paises']      = $saludPaises
$saludDoc['cuentas']     = $saludCuentas
$saludDoc['paradoTotal'] = [math]::Round($paradoTotal, 2)
# Las alertas ocultas por antiguedad: el umbral, cuantas hay y lo que dejaron de gastar (la pagina lo muestra aparte y las lista una a una, con su `diasCaida`).
$saludDoc['diasAlertaVieja']   = $DIAS_ALERTA_VIEJA
$saludDoc['historiaDesde']     = $historiaDesde
$saludDoc['ocultasN']          = $caidasOcultas.Count
$saludDoc['paradoOcultoTotal'] = [math]::Round($paradoOcultoTotal, 2)
$saludDoc['faltantes']   = [object[]]$faltantes.ToArray()
# Como le fue al ultimo aviso, sacado de la historia que escribe Avisar-Salud.ps1.
#
# Sin esto el avisador no tiene quien lo vigile: si el POST falla o si nadie configuro un
# canal, el sistema se queda sin voz y la unica señal seria la ausencia de mensajes, que se
# confunde con "no ha pasado nada". Es el mismo fallo que el gasto de Mexico -un silencio
# que se lee como buena noticia- aplicado al que avisa.
$histPath = Join-Path $DataDir 'salud-historia.json'
if (Test-Path $histPath) {
    try {
        $hj = Get-Content $histPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($hj.ultimoEnvio) { $saludDoc['ultimoEnvio'] = $hj.ultimoEnvio }
        $saludDoc['diasDeHistoria'] = @($hj.dias.PSObject.Properties.Name).Count
    } catch { }
}
$saludPath = Join-Path $DataDir 'salud.json'
[IO.File]::WriteAllText($saludPath, ($saludDoc | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Output ("Salud -> {0} ({1:N0} KB)" -f $saludPath, ((Get-Item $saludPath).Length / 1KB))
Write-Output ("Salud de cuentas: {0} caidas con anuncios encendidos, ~{1:N0} USD de capacidad parada" -f $caidasVisibles.Count, [double]$paradoTotal)
if ($caidasOcultas.Count) {
    Write-Output ("Salud de cuentas: {0} alerta(s) ocultas por llevar mas de {1} dias caidas (siguen en salud.json; ~{2:N0} USD que dejaron de gastar)" -f $caidasOcultas.Count, $DIAS_ALERTA_VIEJA, [double]$paradoOcultoTotal)
}

# ---------------------------------------------------------------------------
# Unmatched Post ID gap log (handoff §5 step 5)
# ---------------------------------------------------------------------------

function Get-UnmatchedPostIds($orders, $spendMap) {
    $seen = $orders | Where-Object { $_.postId } | Select-Object -ExpandProperty postId -Unique
    return @($seen | Where-Object { -not $spendMap.ContainsKey($_) })
}

$mxUnmatched = Get-UnmatchedPostIds -orders $mexico.orders -spendMap $mxSpendByAd
$veUnmatched = Get-UnmatchedPostIds -orders $veOrders -spendMap $veSpendByAd
$colUnmatched = Get-UnmatchedPostIds -orders $colOrders -spendMap $colSpendByAd
$peUnmatched = Get-UnmatchedPostIds -orders $peOrders -spendMap $peSpendByAd
$gtUnmatched = Get-UnmatchedPostIds -orders $gtOrders -spendMap $gtSpendByAd
$arUnmatched = Get-UnmatchedPostIds -orders $arOrders -spendMap $arSpendByAd
$ecUnmatched = Get-UnmatchedPostIds -orders $ecOrders -spendMap $ecSpendByAd
$boUnmatched = Get-UnmatchedPostIds -orders $boOrders -spendMap $boSpendByAd

$gapLog = Join-Path $DataDir 'unmatched-post-ids.log'
@(
    "# generated $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "Mexico unmatched postIds ($($mxUnmatched.Count)): $($mxUnmatched -join ', ')"
    "Venezuela unmatched postIds ($($veUnmatched.Count)): $($veUnmatched -join ', ')"
    "Colombia unmatched postIds ($($colUnmatched.Count)): $($colUnmatched -join ', ')"
    "Peru unmatched postIds ($($peUnmatched.Count)): $($peUnmatched -join ', ')"
    "Guatemala unmatched postIds ($($gtUnmatched.Count)): $($gtUnmatched -join ', ')"
) | Out-File -FilePath $gapLog -Encoding utf8
Write-Output "Unmatched Post ID gap log -> $gapLog"

# Lecturas por hora (data/lecturas-horarias.json, lo escribe scripts/lecturas-horarias.cjs en cada refresco): lo que el dash llevaba «hoy» (gasto y ventas en USD) a cada hora, por pais.
# Con ellas la tarjeta «Dia a dia > Hora del dia» reparte el gasto y las ventas de cada dia entre sus horas (templates/ganancia-hora.js). Viaja DENTRO del pais, como el gasto: el archivo de
# un pais (y la pagina del socio) solo trae las suyas. Sin el archivo, o si no se puede leer, no pasa nada: esa vista se queda con la tabla por hora de siempre.
$lecturasHora = @{}
$lhPath = Join-Path $DataDir 'lecturas-horarias.json'
if (Test-Path $lhPath) {
    try {
        $lhj = Get-Content $lhPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($lhj.v -eq 1 -and $lhj.dias) { foreach ($lhPais in $lhj.dias.PSObject.Properties) { $lecturasHora[$lhPais.Name] = $lhPais.Value } }
    } catch { Write-Warning ("lecturas-horarias.json ilegible, se ignora: " + $_.Exception.Message) }
}

# Lecturas por oferta (data/lecturas-ofertas.json, lo escribe scripts/lecturas-ofertas.cjs en cada refresco): lo que CADA OFERTA llevaba «hoy» a cada hora, por pais. Con ellas «Dia a dia > Hora del
# dia» reparte el gasto y las ventas de una oferta entre sus horas, como ya hace con el pais. Viaja DENTRO del pais, igual que lecturasHora. Sin el archivo, o si no se puede leer, no pasa nada.
$lecturasOfertas = @{}
$loPath = Join-Path $DataDir 'lecturas-ofertas.json'
if (Test-Path $loPath) {
    try {
        $loj = Get-Content $loPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($loj.v -eq 1 -and $loj.dias) { foreach ($loPais in $loj.dias.PSObject.Properties) { $lecturasOfertas[$loPais.Name] = $loPais.Value } }
    } catch { Write-Warning ("lecturas-ofertas.json ilegible, se ignora: " + $_.Exception.Message) }
}

# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------

# Estado de conexion de cada numero de WhatsApp de ZapData (data\zapdata-instancias.json, que Fetch-ZapDataInstancias.ps1 reescribe en cada refresco con la API de ZapData). Lo lee la condicion
# «Numero de WhatsApp de la campana» de las reglas (templates/reglas-modulo.js, rgNumeros): solo viaja lo que ella necesita (numero, pais, estado) y la fecha en que se bajo, para que la regla pueda
# negarse a decidir con una lista vieja. Solo va en el snapshot COMBINADO: los archivos por pais ($single, mas abajo) no lo llevan. Si el archivo falta o no se entiende, queda vacio y la condicion da «sin dato».
$numerosWpp = [ordered]@{ capturadoEn = ''; fuente = ''; instancias = @() }
$pathNumerosWpp = Join-Path $DataDir 'zapdata-instancias.json'
if (Test-Path $pathNumerosWpp) {
    try {
        $jiNum = Get-Content $pathNumerosWpp -Raw -Encoding UTF8 | ConvertFrom-Json
        # pwsh 7 (el de CI) convierte solo el texto ISO en DateTime y [string] lo dejaria como «10/27/2026 22:58:39», sin zona horaria: la pagina lo leeria en la hora del telefono y el control de «lista vieja» fallaria.
        # Se escribe siempre en UTC y con la zona explicita. PowerShell 5.1 lo deja como texto: se reescribe igual para que ambos den lo mismo.
        $capNum = $jiNum.capturadoEn
        if ($capNum -is [datetime]) { $capNum = $capNum.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [System.Globalization.CultureInfo]::InvariantCulture) }
        else { $tmpCap = [datetimeoffset]::MinValue; if ([datetimeoffset]::TryParse([string]$capNum, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None, [ref]$tmpCap)) { $capNum = $tmpCap.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [System.Globalization.CultureInfo]::InvariantCulture) } else { $capNum = '' } }
        $numerosWpp['capturadoEn'] = [string]$capNum
        $numerosWpp['fuente'] = [string]$jiNum.fuente
        $numerosWpp['instancias'] = @($jiNum.instancias | Where-Object { $_.wpp -and $_.pais } | ForEach-Object { [ordered]@{ wpp = [string]$_.wpp; pais = [string]$_.pais; estado = [string]$_.estado } })
    } catch { Write-Output ('zapdata-instancias.json ilegible (las reglas no sabran si los numeros estan conectados): ' + $_.Exception.Message) }
}

$snapshot = [ordered]@{
    generatedAt = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')
    dataVersion = 5
    fx          = [ordered]@{
        MXN    = $fxMXN
        VES    = $fxVES
        COP    = $fxCOP
        PEN    = $fxPEN
        GTQ    = $fxGTQ
        ARS    = $fxARS
        BOB    = $fxBOB
        # fecha -> tasa, por moneda. Pesa unos cientos de bytes y es lo que permite que
        # cada venta se convierta con la tasa de SU dia en vez de con la de hoy.
        historia = $fxHistoria
        # Procedencia de cada tasa y el tope de dias que puede tener antes de marcarse vieja.
        meta     = $fxMeta
        topeDias = $fxTopeDias
        # El dolar consigo mismo vale uno. Parece de perogrullo, pero Ecuador factura en
        # USD y la pagina divide por la tasa de la moneda del pais para llevar todo a
        # dolares: sin esta entrada dividia por undefined y la facturacion de Ecuador salia
        # "$NaN USD", con el ROAS en "NaNx".
        USD    = 1
        asOf   = [string]$fxRaw.time_last_update_utc
        # Que monedas llevan tasa puesta a mano. La pagina arma el texto con esto, para
        # que el dashboard de Argentina no diga "VES fijado a mano": ahi no viene al caso.
        fijadas = @(
            $ovFile2 = Join-Path $DataDir 'fx-overrides.json'
            if (Test-Path $ovFile2) {
                $ovj2 = Get-Content $ovFile2 -Raw -Encoding UTF8 | ConvertFrom-Json
                @($ovj2.PSObject.Properties.Name | Where-Object { $_ -notlike '_*' })
            }
        )
        # Decir "open.er-api.com" a secas seria falso cuando una tasa esta fijada a mano:
        # el VES paralelo se define en data\fx-overrides.json, no lo trae la API.
        source = $(
            $ovFile = Join-Path $DataDir 'fx-overrides.json'
            if (Test-Path $ovFile) {
                $ovj = Get-Content $ovFile -Raw -Encoding UTF8 | ConvertFrom-Json
                $fijadas = @($ovj.PSObject.Properties.Name | Where-Object { $_ -notlike '_*' })
                if ($fijadas.Count) { "open.er-api.com; $($fijadas -join ', ') fijado a mano (tasa paralela)" }
                else { 'open.er-api.com' }
            } else { 'open.er-api.com' }
        )
    }
    metas       = $metas
    objetivo    = $objetivo
    costos      = $costos
    saludCuentas = $saludCuentas
    numerosWpp  = $numerosWpp
    writeAccess = $writeAccess
    writeReason = $writeReason
    countries   = [ordered]@{
        mexico     = [ordered]@{
            currency        = 'MXN'
            adSpendCurrency = $monedaGasto['mexico'].moneda
            adSpendMixta    = $monedaGasto['mexico'].detalle
            orders          = $mexico.orders
            spendByAdId     = $mxSpendByAd
            spendByAdIdDate = $mxSpendByAdDate
            convByAdIdDate  = $mxConvByAdDate
            impByAdIdDate   = $mxImpByAdDate
            purchByAdIdDate = $mxPurchByAdDate
            adMeta          = $mxAdMeta
            unmatchedPostIds = $mxUnmatched
            quarantinedCount = $mexico.quarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $mexico.quarantined)
            scheduledCampaigns = $scheduledByCountry.mexico
            lecturasHora = $lecturasHora['mexico']
            lecturasOfertas = $lecturasOfertas['mexico']
        }
        venezuela  = [ordered]@{
            currency        = 'VES'
            adSpendCurrency = $monedaGasto['venezuela'].moneda
            adSpendMixta    = $monedaGasto['venezuela'].detalle
            orders          = $veOrders
            spendByAdId     = $veSpendByAd
            spendByAdIdDate = $veSpendByAdDate
            convByAdIdDate  = $veConvByAdDate
            impByAdIdDate   = $veImpByAdDate
            purchByAdIdDate = $vePurchByAdDate
            adMeta          = $veAdMeta
            unmatchedPostIds = $veUnmatched
            quarantinedCount = $veQuarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $veQuarantined)
            scheduledCampaigns = $scheduledByCountry.venezuela
            lecturasHora = $lecturasHora['venezuela']
            lecturasOfertas = $lecturasOfertas['venezuela']
        }
        colombia   = [ordered]@{
            currency        = 'COP'
            adSpendCurrency = $monedaGasto['colombia'].moneda
            adSpendMixta    = $monedaGasto['colombia'].detalle
            orders          = $colOrders
            spendByAdId     = $colSpendByAd
            spendByAdIdDate = $colSpendByAdDate
            convByAdIdDate  = $colConvByAdDate
            impByAdIdDate   = $colImpByAdDate
            purchByAdIdDate = $colPurchByAdDate
            adMeta          = $colAdMeta
            unmatchedPostIds = $colUnmatched
            quarantinedCount = $colQuarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $colQuarantined)
            scheduledCampaigns = $scheduledByCountry.colombia
            lecturasHora = $lecturasHora['colombia']
            lecturasOfertas = $lecturasOfertas['colombia']
        }
        peru       = [ordered]@{
            currency        = 'PEN'
            adSpendCurrency = $monedaGasto['peru'].moneda
            adSpendMixta    = $monedaGasto['peru'].detalle
            orders          = $peOrders
            spendByAdId     = $peSpendByAd
            spendByAdIdDate = $peSpendByAdDate
            convByAdIdDate  = $peConvByAdDate
            impByAdIdDate   = $peImpByAdDate
            purchByAdIdDate = $pePurchByAdDate
            adMeta          = $peAdMeta
            unmatchedPostIds = $peUnmatched
            quarantinedCount = $peQuarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $peQuarantined)
            scheduledCampaigns = $scheduledByCountry.peru
            lecturasHora = $lecturasHora['peru']
            lecturasOfertas = $lecturasOfertas['peru']
        }
        guatemala  = [ordered]@{
            currency        = 'GTQ'
            adSpendCurrency = $monedaGasto['guatemala'].moneda
            adSpendMixta    = $monedaGasto['guatemala'].detalle
            orders          = $gtOrders
            spendByAdId     = $gtSpendByAd
            spendByAdIdDate = $gtSpendByAdDate
            convByAdIdDate  = $gtConvByAdDate
            impByAdIdDate   = $gtImpByAdDate
            purchByAdIdDate = $gtPurchByAdDate
            adMeta          = $gtAdMeta
            unmatchedPostIds = $gtUnmatched
            quarantinedCount = $gtQuarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $gtQuarantined)
            scheduledCampaigns = $scheduledByCountry.guatemala
            lecturasHora = $lecturasHora['guatemala']
            lecturasOfertas = $lecturasOfertas['guatemala']
        }
        argentina  = [ordered]@{
            currency        = 'ARS'
            # Argentina is the first country whose ad accounts DON'T share one currency:
            # Cuenta #1 bills in ARS, #2 and #3 in USD. Spend is converted per row from
            # each account's own currency, so this label is only the one shown on screen.
            adSpendCurrency = $monedaGasto['argentina'].moneda
            adSpendMixta    = $monedaGasto['argentina'].detalle
            orders          = $arOrders
            spendByAdId     = $arSpendByAd
            spendByAdIdDate = $arSpendByAdDate
            convByAdIdDate  = $arConvByAdDate
            impByAdIdDate   = $arImpByAdDate
            purchByAdIdDate = $arPurchByAdDate
            adMeta          = $arAdMeta
            unmatchedPostIds = $arUnmatched
            quarantinedCount = $arQuarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $arQuarantined)
            scheduledCampaigns = $scheduledByCountry.argentina
            lecturasHora = $lecturasHora['argentina']
            lecturasOfertas = $lecturasOfertas['argentina']
        }
        # Ecuador es el unico pais que vende y anuncia en la misma moneda: dolariza. Aqui
        # no hay conversion ni tasa que se quede vieja, asi que sus numeros son los mas
        # fiables del tablero.
        ecuador    = [ordered]@{
            currency        = 'USD'
            adSpendCurrency = $monedaGasto['ecuador'].moneda
            adSpendMixta    = $monedaGasto['ecuador'].detalle
            orders          = $ecOrders
            spendByAdId     = $ecSpendByAd
            spendByAdIdDate = $ecSpendByAdDate
            convByAdIdDate  = $ecConvByAdDate
            impByAdIdDate   = $ecImpByAdDate
            purchByAdIdDate = $ecPurchByAdDate
            adMeta          = $ecAdMeta
            unmatchedPostIds = $ecUnmatched
            quarantinedCount = $ecQuarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $ecQuarantined)
            scheduledCampaigns = $scheduledByCountry.ecuador
            lecturasHora = $lecturasHora['ecuador']
            lecturasOfertas = $lecturasOfertas['ecuador']
        }
        # Bolivia vende en pesos bolivianos (BOB); sus cuentas de anuncios pueden facturar en BOB o USD y se
        # convierte por fila, igual que Argentina. Nace sin cuentas: el gasto aparece cuando se le asignen.
        bolivia    = [ordered]@{
            currency        = 'BOB'
            adSpendCurrency = $monedaGasto['bolivia'].moneda
            adSpendMixta    = $monedaGasto['bolivia'].detalle
            orders          = $boOrders
            spendByAdId     = $boSpendByAd
            spendByAdIdDate = $boSpendByAdDate
            convByAdIdDate  = $boConvByAdDate
            impByAdIdDate   = $boImpByAdDate
            purchByAdIdDate = $boPurchByAdDate
            adMeta          = $boAdMeta
            unmatchedPostIds = $boUnmatched
            quarantinedCount = $boQuarantined.Count
            quarantinedByReason = (Resumir-Cuarentena $boQuarantined)
            scheduledCampaigns = $scheduledByCountry.bolivia
            lecturasHora = $lecturasHora['bolivia']
            lecturasOfertas = $lecturasOfertas['bolivia']
        }
    }
}

# Red final: ningun pais sale al snapshot con un monto imposible, aunque su linea de Remove-AmountOutliers (arriba) falte o se pierda en un cambio.
# Todo lo demas -la barra de la meta, el resumen diario, la torre, los tableros- lee este archivo, asi que basta con cuidarlo aqui.
# Si salta, es un bug: el aviso nombra el pais para agregarle su linea arriba (alli tambien se limpian los cruces con anuncios y el ticket mediano).
foreach ($cn in @($snapshot.countries.Keys)) {
    $c = $snapshot.countries[$cn]
    $sueltas = New-Object System.Collections.Generic.List[object]
    $r = Remove-AmountOutliers $c.orders $sueltas
    if (-not $r.removed) { continue }
    $c.orders = $r.orders
    $c.quarantinedCount = [int]$c.quarantinedCount + $r.removed
    foreach ($q in $sueltas) { $c.quarantinedByReason[[string]$q.reason] = 1 + [int]$c.quarantinedByReason[[string]$q.reason] }
    Write-Warning ("{0}: {1} venta(s) con monto imposible llegaron hasta el snapshot (mediana {2:N0}). Se apartaron aqui; falta la linea de Remove-AmountOutliers de este pais." -f $cn, $r.removed, $r.median)
}

# Order bumps (data/order-bumps.json + las pestanas «order bump ...» de los libros, 2026-11-11): que compras de una oferta llevaron su order bump. ZapData no lo marca en la
# venta, asi que se reconoce por el monto: precio base + precio del bump, exacto (15.000 + 6.000 = 21.000). Cada pais lleva SOLO los suyos en countries.<pais>.orderBumps -el
# archivo por pais y el del socio no traen los de otro pais- y la plantilla arma la tarjeta «Order bump» con las mismas ventas del KPI (tilesOrderBump).
# Dos fuentes: (1) la pestana VISIBLE «order bump ...» del libro de cada pais (data/order-bumps-planilla.json, la baja Refresh-Headless.ps1 y la lee _order-bumps.ps1): asi una
# oferta nueva con bump se mide sin tocar codigo; (2) data/order-bumps.json, de respaldo (si la pestana se oculta o Google falla esa hora). Para la misma oferta del mismo pais
# gana la planilla. Una entrada mal escrita se salta con un aviso que dice por que; un archivo ilegible no tumba el build: el dash sigue sin esa tarjeta.
$bumpsPath = Join-Path $DataDir 'order-bumps.json'
$bumpsPlanillaPath = Join-Path $DataDir 'order-bumps-planilla.json'
# Los bumps que el dash mide esta hora (pais, oferta, pestana, precio, hasta): con ellos se buscan, mas abajo, los order bumps de ZapData que nadie mide.
$medidosB = New-Object System.Collections.Generic.List[object]
if ((Test-Path $bumpsPath) -or (Test-Path $bumpsPlanillaPath)) {
    try {
        $marcasB = "[" + [char]0x300 + "-" + [char]0x36f + "]"
        $normB = { param($s) ((([string]$s).Trim().Normalize([Text.NormalizationForm]::FormD) -replace $marcasB, '') -replace '\s+', ' ').ToLowerInvariant() }
        $inv = [Globalization.CultureInfo]::InvariantCulture
        $nBumps = 0
        $listaB = New-Object System.Collections.Generic.List[object]
        if (Test-Path $bumpsPath) {
            try {
                $bj = Get-Content $bumpsPath -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($b in @($bj.bumps | Where-Object { $null -ne $_ })) { $listaB.Add($b) }
            } catch { Write-Warning ("order-bumps.json: no se pudo leer ({0}); sigo con las pestanas de la planilla" -f $_.Exception.Message) }
        }
        # La pestana de ventas que nombra la planilla («zapdata kinesiotaping») -> la oferta del dash, por el MISMO archivo que la trae (col_zapdata_kinesiotaping.csv ->
        # «Zapdata Kinesiotaping», con sus alias). Si no hay archivo con ese nombre, se busca como nombre de oferta (sin mayusculas ni tildes) mas abajo.
        $tabsPorPaisB = @{ venezuela = @('ve_xlsx_', $veTabs); colombia = @('col_', $colTabs); peru = @('pe_', $peTabs); guatemala = @('gt_', $gtTabs); mexico = @('mx_', $mxTabs); argentina = @('ar_', $arTabs); bolivia = @('bo_', $boTabs); ecuador = @('ec_', $ecTabs) }
        $claveTabB = { param($s) (((& $normB $s) -replace '[_]', ' ') -replace '[^a-z0-9 ]', '' -replace '\s+', ' ').Trim() }
        $ofertaDePestana = {
            param([string]$pais, [string]$pestana)
            $par = $tabsPorPaisB[$pais]
            if (-not $par -or -not $par[1]) { return '' }
            $buscadaT = & $claveTabB $pestana
            foreach ($t in @($par[1])) {
                $k = ([IO.Path]::GetFileNameWithoutExtension([string]$t.file)) -replace ('^' + [regex]::Escape($par[0])), ''
                if ((& $claveTabB $k) -eq $buscadaT) { return [string]$t.offer }
            }
            return ''
        }
        if (Test-Path $bumpsPlanillaPath) {
            try {
                $pj = Get-Content $bumpsPlanillaPath -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($fa in @($pj.fallas | Where-Object { $null -ne $_ })) { Write-Warning ("order bump: la pestana '{0}' ({1}) no se pudo bajar: {2}" -f $fa.pestana, $fa.pais, $fa.error) }
                $dePlanilla = New-Object System.Collections.Generic.List[object]
                foreach ($pt in @($pj.pestanas | Where-Object { $null -ne $_ })) {
                    $lp = Read-BumpPestana @($pt.filas)
                    $paisP = ([string]$pt.pais).Trim().ToLowerInvariant()
                    if ($lp.error) { Write-Warning ("order bump: la pestana '{0}' ({1}) {2}; se salta" -f $pt.pestana, $paisP, $lp.error); continue }
                    $ofP = & $ofertaDePestana $paisP $lp.pestanaVentas
                    if (-not $ofP) { $ofP = $lp.pestanaVentas }
                    $e = [pscustomobject]@{ pais = $paisP; oferta = $ofP; nombre = $lp.nombre; desde = $lp.desde; hasta = $lp.hasta; precio = $lp.precio; preciosBase = @($lp.preciosBase)
                                            nota = $lp.nota; fuente = 'planilla'; pestana = ([string]$pt.pestana).Trim(); planCompras = $lp.compras; planCon = $lp.con }
                    $dePlanilla.Add($e)
                }
                # La planilla gana: se quitan del archivo las entradas de la misma oferta del mismo pais.
                foreach ($e in $dePlanilla) {
                    foreach ($viejo in @($listaB | Where-Object { ([string]$_.pais).Trim().ToLowerInvariant() -eq $e.pais -and (& $normB $_.oferta) -eq (& $normB $e.oferta) })) { [void]$listaB.Remove($viejo) }
                }
                # Un bump del respaldo que no tiene pestana leida: se sigue midiendo, pero se dice, porque lo normal es que alguien la oculto (el htmlview no lista las ocultas).
                foreach ($r in $listaB) { Write-Warning ("order bump '{0}' ({1}): se mide con el respaldo data/order-bumps.json porque no se leyo su pestana 'order bump ...' del libro (oculta, renombrada o con falla)" -f $r.oferta, $r.pais) }
                foreach ($e in $dePlanilla) { $listaB.Add($e) }
            } catch { Write-Warning ("order-bumps-planilla.json: no se pudo leer ({0}); sigo con data/order-bumps.json" -f $_.Exception.Message) }
        }
        foreach ($b in $listaB) {
            $pais = ([string]$b.pais).Trim().ToLowerInvariant()
            $ofCfg = ([string]$b.oferta).Trim()
            # La oferta puede venir de la planilla (lo que alguien escribio en «Pestana de ventas»): con tope, como el producto y la nota.
            if ($ofCfg.Length -gt 120) { $ofCfg = $ofCfg.Substring(0, 120) }
            $quien = if ($b.fuente -eq 'planilla') { "pestana '{0}' ({1}, oferta '{2}')" -f $b.pestana, $pais, $ofCfg } else { "'{0}' de {1}" -f $ofCfg, $pais }
            if (-not $snapshot.countries.Contains($pais)) { Write-Warning ("order-bumps.json: {0}: el pais no existe en el dash; se salta" -f $quien); continue }
            if (-not $ofCfg) { Write-Warning ("order-bumps.json: una entrada de {0} no dice la oferta; se salta" -f $pais); continue }
            $dDesde = [datetime]::MinValue
            if (-not [datetime]::TryParseExact(([string]$b.desde).Trim(), 'yyyy-MM-dd', $inv, [Globalization.DateTimeStyles]::None, [ref]$dDesde)) { Write-Warning ("order-bumps.json: {0}: 'desde' tiene que ser AAAA-MM-DD; se salta" -f $quien); continue }
            $hasta = ''
            if (([string]$b.hasta).Trim()) {
                $dHasta = [datetime]::MinValue
                if (-not [datetime]::TryParseExact(([string]$b.hasta).Trim(), 'yyyy-MM-dd', $inv, [Globalization.DateTimeStyles]::None, [ref]$dHasta) -or $dHasta -lt $dDesde) { Write-Warning ("order-bumps.json: {0}: 'hasta' tiene que ser AAAA-MM-DD y no anterior a 'desde'; se salta" -f $quien); continue }
                $hasta = $dHasta.ToString('yyyy-MM-dd', $inv)
            }
            $precio = 0.0
            if (-not [double]::TryParse(([string]$b.precio), [Globalization.NumberStyles]::Float, $inv, [ref]$precio) -or $precio -le 0) { Write-Warning ("order-bumps.json: {0}: 'precio' tiene que ser un numero mayor que 0; se salta" -f $quien); continue }
            $bases = New-Object System.Collections.Generic.List[double]
            $baseMala = $false
            foreach ($x in @($b.preciosBase)) {
                $v = 0.0
                if ([double]::TryParse(([string]$x), [Globalization.NumberStyles]::Float, $inv, [ref]$v) -and $v -gt 0) { if (-not $bases.Contains($v)) { $bases.Add($v) } } else { $baseMala = $true }
            }
            if ($baseMala -or -not $bases.Count) { Write-Warning ("order-bumps.json: {0}: 'preciosBase' tiene que ser una lista de numeros mayores que 0; se salta" -f $quien); continue }
            # La oferta se busca sin mayusculas ni tildes entre las ventas del pais y se guarda con el nombre EXACTO del dash: es el que compara la plantilla.
            $buscada = & $normB $ofCfg
            $oferta = @($snapshot.countries[$pais].orders | ForEach-Object { [string]$_.offer } | Sort-Object -Unique | Where-Object { (& $normB $_) -eq $buscada }) | Select-Object -First 1
            if (-not $oferta) { Write-Warning ("order-bumps.json: {0}: el pais no tiene ventas de esa oferta (revisa el nombre); la tarjeta no saldra hasta que las tenga" -f $quien); $oferta = $ofCfg }
            $nombre = ([string]$b.nombre).Trim()
            if ($nombre.Length -gt 120) { $nombre = $nombre.Substring(0, 120) }
            $nota = ([string]$b.nota).Trim()
            if ($nota.Length -gt 300) { $nota = $nota.Substring(0, 300) }
            $entrada = [ordered]@{ oferta = $oferta; nombre = $nombre; desde = $dDesde.ToString('yyyy-MM-dd', $inv); precio = $precio; base = @($bases | Sort-Object) }
            if ($hasta) { $entrada.hasta = $hasta }
            if ($nota) { $entrada.nota = $nota }
            # De donde sale: la pestana del libro (la ayuda de la tarjeta dice donde se cambia y donde esta la lista de quien lo llevo) o el archivo de respaldo.
            if ($b.fuente -eq 'planilla' -and $b.pestana) { $p_ = ([string]$b.pestana).Trim(); if ($p_.Length -gt 60) { $p_ = $p_.Substring(0, 60) }; $entrada.pestana = $p_ }
            if (-not $snapshot.countries[$pais].Contains('orderBumps')) { $snapshot.countries[$pais]['orderBumps'] = New-Object System.Collections.Generic.List[object] }
            $snapshot.countries[$pais]['orderBumps'].Add($entrada)
            $medidosB.Add([pscustomobject]@{ pais = $pais; oferta = $oferta; ofertaCfg = $ofCfg; pestana = [string]$b.pestana; precio = $precio; hasta = $hasta })
            $nBumps++
            # La misma cuenta que la tarjeta (todas las ventas desde 'desde'), para poder cuadrarla en el log contra la pestana de la hoja sin abrir el navegador.
            $vb = @($snapshot.countries[$pais].orders | Where-Object { $_.offer -eq $oferta -and $_.date -ge $entrada.desde -and (-not $hasta -or $_.date -le $hasta) })
            $cb = @($vb | Where-Object { $a = [double]$_.amount; @($bases | Where-Object { [Math]::Abs($a - ($_ + $precio)) -lt 0.5 }).Count -gt 0 })
            $enPlanilla = ''
            if ($b.fuente -eq 'planilla' -and $null -ne $b.planCompras -and $null -ne $b.planCon) { $enPlanilla = ("; la pestana dice {0} de {1}" -f $b.planCon, $b.planCompras) }
            Write-Output ("Order bump {0} ({1}): {2} de {3} compras desde {4}{5}" -f $oferta, $pais, $cb.Count, $vb.Count, $entrada.desde, $enPlanilla)
        }
        if (-not $nBumps -and $listaB.Count) { Write-Warning 'order-bumps.json: ninguna entrada valida; el dash sigue sin la tarjeta de order bump' }
    } catch {
        Write-Warning ("order-bumps.json: no se pudo leer ({0}); el dash sigue sin la tarjeta de order bump" -f $_.Exception.Message)
    }
}

# Order bumps SIN MEDIR (2026-11-11): los que tienen los flujos de Venta encendidos de ZapData (data/order-bumps-zapdata.json, lo deja scripts/order-bumps-zapdata.cjs en el paso anterior del
# refresco) contra los que el dash mide esta hora ($medidosB). Lo que falta va al log (lineas "ATENCION order bump sin medir", y una anotacion amarilla en la corrida de GitHub) y a
# order-bumps-sin-medir.json, junto a index.html: refresh.yml lo copia a dist/ y el middleware solo se lo da al dueno (no esta en ninguna lista del equipo ni de los socios). El menu del dueno
# y el dash combinado lo muestran. Nunca tumba el build.
try {
    $zapBumpsPath = Join-Path $DataDir 'order-bumps-zapdata.json'
    $zapB = $null
    if (Test-Path $zapBumpsPath) { try { $zapB = Get-Content $zapBumpsPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $zapB = [pscustomobject]@{ ok = $false; motivo = 'ilegible'; mensaje = $_.Exception.Message } } }
    $hoyCo = [DateTime]::UtcNow.AddHours(-5).ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    $cmpB = Compare-BumpsZapData $zapB $medidosB $hoyCo
    $enCi = $env:GITHUB_ACTIONS -eq 'true'
    # Write-Host y no Write-Output: Refresh-Headless.ps1 llama este build con «| Out-Null» y la salida normal no llega al log de la corrida (visto en la primera corrida, 2026-11-12).
    # La anotacion amarilla de GitHub tambien tiene que ir por ahi, o se pierde.
    foreach ($l in $cmpB.lineas) {
        if ($l.StartsWith('ATENCION')) { Write-Warning $l; if ($enCi) { Write-Host ('::warning title=Order bump sin medir::' + $l) } } else { Write-Host $l }
    }
    $sinMedirDoc = [ordered]@{
        _nota = 'Lo arma build-dashboard.ps1 (Compare-BumpsZapData de scripts/_order-bumps.ps1). Solo lo abre el dueno.'
        generadoEn = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        estado = $cmpB.estado; motivo = $cmpB.motivo; mensaje = $cmpB.mensaje
        revisadoEn = $(if ($zapB -and $zapB.capturadoEn) { [string]$zapB.capturadoEn } else { '' })
        total = $(if ($zapB -and $zapB.ok -eq $true) { [int]$zapB.total } else { 0 })
        pendientes = $(if ($zapB -and $zapB.ok -eq $true) { [int]$zapB.pendientes } else { 0 })
        noLeidas = $(if ($zapB -and $zapB.ok -eq $true) { [int]$zapB.noLeidas } else { 0 })
        avisos = $cmpB.avisos.ToArray()
    }
    [IO.File]::WriteAllText((Join-Path (Split-Path $DataDir) 'order-bumps-sin-medir.json'), ($sinMedirDoc | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
} catch {
    Write-Warning ("order bumps sin medir: no se pudo armar el aviso ({0})" -f $_.Exception.Message)
}

$snapshot | ConvertTo-Json -Depth 10 | Out-File -FilePath $SnapshotPath -Encoding utf8
Write-Output "Snapshot written -> $SnapshotPath"

if ($DataOnly) {
    Write-Output "DataOnly switch set, skipping HTML render."
    exit 0
}

# ---------------------------------------------------------------------------
# HTML render
# ---------------------------------------------------------------------------

$templatePath = Join-Path (Split-Path $DataDir) 'templates/dashboard.template.html'
$templateBruta = Get-Content $templatePath -Raw -Encoding UTF8
# El modulo de reglas vive en archivos aparte (templates/reglas-*.js y reglas.css) y se mete en el mismo script
# del dashboard: comparte SNAPSHOT, state, arbolDeVentana, etc. Los marcadores estan en la plantilla.
$tplDir = Split-Path $templatePath
$reglasJs = (Get-Content (Join-Path $tplDir 'reglas-modulo.js') -Raw -Encoding UTF8) + "`n" + (Get-Content (Join-Path $tplDir 'reglas-ui.js') -Raw -Encoding UTF8)
$reglasCss = Get-Content (Join-Path $tplDir 'reglas.css') -Raw -Encoding UTF8
# La torta interactiva (templates/torta.js y torta.css) la comparten el dash, /numeros y /objetivos.
$tortaJs = Get-Content (Join-Path $tplDir 'torta.js') -Raw -Encoding UTF8
$tortaCss = Get-Content (Join-Path $tplDir 'torta.css') -Raw -Encoding UTF8
# La ganancia por hora del dia (templates/ganancia-hora.js): el modelo que reparte cada dia entre sus horas y su grafico. Va dentro del mismo script.
$horaJs = Get-Content (Join-Path $tplDir 'ganancia-hora.js') -Raw -Encoding UTF8
$templateBruta = $templateBruta.Replace('  __REGLAS_MODULO__', $reglasJs).Replace('  /*__REGLAS_CSS__*/', $reglasCss).Replace('  __TORTA_JS__', $tortaJs).Replace('  __GANANCIA_HORA__', $horaJs).Replace('  /*__TORTA_CSS__*/', $tortaCss)
# __MODO__: el mismo archivo sale como dashboard ('dash') y como pagina de reglas ('reglas').
$template = $templateBruta.Replace('__MODO__', 'dash')
# El JSON va pegado DENTRO de un <script>: un nombre de campana o de anuncio con "</script>" cerraria el bloque y lo que siguiera correria como codigo en el tablero (en el del
# dueño tambien, con la clave de edicion guardada en la pestaña). PowerShell 5.1 ya escapa '<' (lo escribe como barra invertida + u003c), pero PowerShell 7 -el de GitHub
# Actions, que es el que publica- NO: en data/facebook-graph.json los '>' salen crudos en CI y escapados en local. Escapar '<' a mano da el mismo JSON valido (el navegador
# lee el mismo texto) en los dos. Auditar-Seguridad.ps1 (8c) comprueba que este .Replace siga escapando '<' y no sea un '<' por '<' sin efecto.
$snapshotJson = ($snapshot | ConvertTo-Json -Depth 10 -Compress).Replace('<', '\u003c')
$html = $template.Replace('__SNAPSHOT_JSON__', $snapshotJson)
$html | Out-File -FilePath $OutHtml -Encoding utf8
Write-Output "Dashboard written -> $OutHtml"

# Pagina de reglas: mismo codigo y mismos datos que el dashboard combinado, pero solo muestra los filtros y la
# tarjeta de reglas y propuestas (/reglas, en el menu). Solo el combinado: necesita todos los paises.
$reglasHtml = $templateBruta.Replace('__MODO__', 'reglas').Replace('__SNAPSHOT_JSON__', $snapshotJson).Replace('<title>Ventas + Facebook Ads</title>', '<title>Reglas - Mi Negocio</title>')
$reglasOut = Join-Path (Split-Path $OutHtml) 'reglas.html'
$reglasHtml | Out-File -FilePath $reglasOut -Encoding utf8
Write-Output "Reglas page -> $reglasOut"

# Per-country dashboards: same template, but the embedded snapshot contains ONLY that
# one country. This is what makes a per-partner share safe — e.g. the Venezuela partner's
# file has zero data for the other countries, so they can't see them even by inspecting
# the HTML/JSON. Written next to the combined dashboard as dashboard-<country>.html.
$outDir = Split-Path $OutHtml
$countryLabels = @{ mexico = 'México'; venezuela = 'Venezuela'; colombia = 'Colombia'; peru = 'Perú'; guatemala = 'Guatemala'; argentina = 'Argentina'; ecuador = 'Ecuador'; bolivia = 'Bolivia' }
foreach ($ck in $snapshot.countries.Keys) {
    $countriesOne = [ordered]@{}
    $countriesOne[$ck] = $snapshot.countries[$ck]
    # La salud de las cuentas DE ESTE PAIS. Sin ella el archivo por pais degrada en
    # silencio hacia "todo sano": el chip vuelve a decir "Activo" verde sobre una cuenta
    # deshabilitada y el filtro de candidatas a pausar deja de verlas. Se filtra por pais
    # para no filtrar de mas — cada entrada trae su gasto tipico, que es dato de dinero.
    $saludOne = [ordered]@{}
    foreach ($acc in $saludCuentas.Keys) {
        if ($saludCuentas[$acc]['country'] -eq $ck) { $saludOne[$acc] = $saludCuentas[$acc] }
    }
    # writeAccess / writeReason traen UNA entrada por cuenta publicitaria de TODOS los paises (el id de la cuenta, si deja escribir y por que no): el archivo de un pais solo
    # necesita las suyas -ese tablero no pausa ni cambia presupuestos- y el del socio no tiene por que saber de las cuentas de los demas. Son las del pais por la salud de
    # cuentas (saludOne) o porque alguno de sus anuncios cuelga de ellas.
    $cuentasDelPais = @{}
    foreach ($acc in $saludOne.Keys) { $cuentasDelPais[[string]$acc] = $true }
    $adMetaPais = $snapshot.countries[$ck]['adMeta']
    if ($adMetaPais) { foreach ($am in $adMetaPais.Values) { if ($am['accountId']) { $cuentasDelPais[[string]$am['accountId']] = $true } } }
    $writeAccessOne = [ordered]@{}
    foreach ($acc in $snapshot.writeAccess.Keys) { if ($cuentasDelPais.ContainsKey([string]$acc)) { $writeAccessOne[$acc] = $snapshot.writeAccess[$acc] } }
    $writeReasonOne = [ordered]@{}
    foreach ($acc in $snapshot.writeReason.Keys) { if ($cuentasDelPais.ContainsKey([string]$acc)) { $writeReasonOne[$acc] = $snapshot.writeReason[$acc] } }
    $single = [ordered]@{
        generatedAt = $snapshot.generatedAt
        dataVersion = $snapshot.dataVersion
        fx          = $snapshot.fx
        writeAccess = $writeAccessOne         # solo flags de permiso (sin datos de dinero) y solo de las cuentas de este pais
        writeReason = $writeReasonOne
        saludCuentas = $saludOne
        countries   = $countriesOne
    }
    $singleJson = ($single | ConvertTo-Json -Depth 10 -Compress).Replace('<', '\u003c')
    $singleHtml = $template.Replace('__SNAPSHOT_JSON__', $singleJson)
    # Give each per-country file its own <title> so the artifact gallery/tab shows the
    # country instead of four identical "Ventas + Facebook Ads" entries.
    $label = if ($countryLabels.ContainsKey($ck)) { $countryLabels[$ck] } else { $ck }
    $singleHtml = $singleHtml.Replace('<title>Ventas + Facebook Ads</title>', "<title>Ventas $label</title>")
    $singleOut = Join-Path $outDir "dashboard-$ck.html"
    $singleHtml | Out-File -FilePath $singleOut -Encoding utf8
    Write-Output "Per-country dashboard -> $singleOut"

    # La pagina del SOCIO de ese pais (socio-<pais>.html): el mismo dash con el mismo snapshot de un solo pais, pero en MODO SOCIO -la plantilla pinta las ofertas
    # como «Oferta 1», «Oferta 2»...-. Va aparte de dashboard-<pais>.html a proposito: ese lo siguen usando el dueno y la trafficker, con los nombres reales, y
    # el rol de socio (functions/_middleware.js) solo alcanza esta. Los dos salen del mismo $single, asi que sus cifras no pueden diferir.
    # Unica diferencia de datos: el socio no lleva orderBumps (nombre del producto y la nota interna del flujo; su pagina no pinta esa tarjeta). No entra en ninguna cifra: se saca,
    # se arma su JSON y se devuelve al pais para lo que siga.
    $socioJson = $singleJson
    $bumpsPais = $null
    if ($countriesOne[$ck].Contains('orderBumps')) {
        $bumpsPais = $countriesOne[$ck]['orderBumps']
        $countriesOne[$ck].Remove('orderBumps')
        try { $socioJson = ($single | ConvertTo-Json -Depth 10 -Compress).Replace('<', ([string][char]92 + 'u003c')) } finally { $countriesOne[$ck]['orderBumps'] = $bumpsPais }
    }
    $socioHtml = $templateBruta.Replace('__MODO__', 'socio').Replace('__SNAPSHOT_JSON__', $socioJson).Replace('<title>Ventas + Facebook Ads</title>', "<title>Ventas $label</title>")
    $socioOut = Join-Path $outDir "socio-$ck.html"
    $socioHtml | Out-File -FilePath $socioOut -Encoding utf8
    Write-Output "Partner dashboard -> $socioOut"
}


