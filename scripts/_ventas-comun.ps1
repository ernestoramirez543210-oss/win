# Parseo de las hojas de ventas. UNA sola copia, dos consumidores:
#   build-dashboard.ps1  construye la torre
#   Cuadre-Hojas.ps1     verifica que la torre traiga todo lo que dice la hoja
#
# Vive aparte porque el cuadre nacio copiando estas funciones y la copia se quedo corta:
# le faltaba la rama de fechas serial de Excel, asi que declaraba "ilegible" un archivo
# que el build si leia -y por tanto no vio que ese archivo se estaba contando dos veces.
# Un verificador que no lee igual que lo verificado no verifica nada.

function Convert-SaleDate([string]$raw) {
    # Venezuela cells arrive as Excel serial-date floats (e.g. "46091.637708333335").
    # Mexico cells arrive as plain ISO-ish strings (Drive CSV export), e.g.
    # "2026-07-14 17:55:06". Handle both; return $null if neither parses.
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    $raw = $raw.Trim()
    $d = 0.0
    if ([double]::TryParse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) {
        if ($d -gt 20000 -and $d -lt 60000) {
            try { return [DateTime]::FromOADate($d) } catch { return $null }
        }
    }
    $dt = [DateTime]::MinValue
    # Sheets writes the day and month without padding, so a real cell reads
    # "24/6/2026 9:30:25": two-digit day, one-digit month, one-digit hour. The list used
    # to demand dd/MM/yyyy HH:mm:ss, and every such row was dropped as unreadable - 82 of
    # them in Guatemala alone, 71 in Moldes, which is 42% of that tab's sales gone with
    # nothing in the output to say so. d/M and H accept both padded and unpadded input,
    # so these formats cover the strict spelling too.
    $formats = @(
        'yyyy-MM-dd HH:mm:ss', 'yyyy-MM-dd',
        'd/M/yyyy H:mm:ss', 'd/M/yyyy H:mm', 'd/M/yyyy',
        'd-M-yyyy H:mm:ss', 'd-M-yyyy',
        'd/M/yy H:mm:ss', 'd/M/yy'
    )
    # Hora de 12 h con el sufijo en espanol: Ecuador escribe "28/10/26 9:20 p. m." desde el 4/10/2026 -ano de 2 cifras y "a. m." / "p. m."-. Ninguna cultura lee eso igual: en
    # y se prueba con la cultura invariante, que no cambia entre maquinas. \s cubre tambien el espacio duro (U+00A0) y el fino (U+202F) que Sheets e ICU meten antes del sufijo.
    # Vale "a. m.", "p.m.", "AM", "pm"; la hora tiene que ir de 1 a 12 (.NET leeria "13:20 PM" y "0:20 PM" como horas validas y eso no es una fecha de la hoja).
    $ampm = [regex]::Match($raw, '^(.*\s(\d{1,2}):\d{2}(?::\d{2})?)\s*([ap])\.?\s*m\.?$', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($ampm.Success -and [int]$ampm.Groups[2].Value -ge 1 -and [int]$ampm.Groups[2].Value -le 12) {
        $raw = ([regex]::Replace($ampm.Groups[1].Value, '\s+', ' ')) + ' ' + $ampm.Groups[3].Value.ToUpperInvariant() + 'M'
        $formats = @(
            'd/M/yy h:mm tt', 'd/M/yy h:mm:ss tt',
            'd/M/yyyy h:mm tt', 'd/M/yyyy h:mm:ss tt',
            'yyyy-MM-dd h:mm tt', 'yyyy-MM-dd h:mm:ss tt'
        ) + $formats
    }
    foreach ($f in $formats) {
        if ([DateTime]::TryParseExact($raw, $f, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$dt)) {
            return $dt
        }
    }
    # Last resort with a day-first culture. InvariantCulture reads 24/6/2026 as month 24
    # and fails, which is exactly how these rows were being lost.
    $esES = [Globalization.CultureInfo]::GetCultureInfo('es-ES')
    if ([DateTime]::TryParse($raw, $esES, [Globalization.DateTimeStyles]::None, [ref]$dt)) {
        return $dt
    }
    return $null
}

function Convert-Amount([string]$raw) {
    # Primary path (XLSX numeric cells, e.g. "2000.0") parses directly and unambiguously.
    # Fallback path defends against locale-formatted display strings ('.' thousands,
    # ',' decimal - handoff's "2.000" = 2000, "876,41" = 876.41) in case a future pull
    # ever comes from a display-formatted CSV export instead of raw XLSX.
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    $raw = $raw.Trim()
    # Someone occasionally types the currency unit into the amount cell itself
    # before parsing rather than silently dropping the whole order as unparseable.
    # Real gap found in production data: 2 real Mundial sales on one day were being
    # quarantined this way even though the amount was perfectly legible to a human.
    $raw = $raw -replace '\s*[A-Za-z]+\.?\s*$', ''
    # Solo ASCII en este patron a proposito: PS 5.1 lee un archivo sin BOM como ANSI y un
    # simbolo no-ASCII aqui rompe el script entero al cargarlo.
    $raw = $raw -replace '^\s*(?:\$|Bs\.?|S/\.?|Q|Gs\.?)\s*', ''
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    $d = 0.0
    if ([double]::TryParse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) {
        # Guard against the ambiguous "2.000" case slipping through as 2.0 when it almost
        # certainly means 2000 (three digits after a lone dot, no comma involved).
        if ($raw -match '^\d+\.\d{3}$') {
            $norm = $raw -replace '\.', ''
            if ([double]::TryParse($norm, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) {
                return $d
            }
        }
        return $d
    }
    $norm = ($raw -replace '\.', '') -replace ',', '.'
    if ([double]::TryParse($norm, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) {
        return $d
    }
    return $null
}

# «Pagos Mexico» le pide a la IA que los reste y no siempre lo hace. Un pago ENTERO que termina en 7 y es de al menos 77 es el precio + 17. La misma regla esta en el webhook
# (scripts/zapdata-webhook.gs, sinIvaMexico) y en el reparador (scripts/reparar-ventas.cjs, sinIvaMexico). Aplicarla dos veces no cambia nada: el resultado termina en 0.
function Get-PagoSinIvaMexico([double]$monto) {
    if ($monto -ge 77 -and $monto -eq [Math]::Floor($monto) -and ($monto % 10) -eq 7) { return $monto - 17 }
    return $monto
}

function Test-PostIdShape([string]$val) {
    if ([string]::IsNullOrWhiteSpace($val)) { return $false }
    return [bool]([regex]::IsMatch($val.Trim(), '^\d{10,19}(\.0+)?$'))
}

function Get-CleanPostId([string]$val) {
    return ($val.Trim() -replace '\.0+$', '')
}

# ZapData no entrega el Post ID numerico de Facebook: en su lugar escribe el link corto
# en el que el cliente hizo clic (fb.me/... o instagram.com/p/...). No sirve para cruzar
# con el gasto de Facebook -ese cruce es por Post ID numerico-, pero SI sirve para agrupar
# las ventas que comparten el mismo anuncio/creativo, que es lo unico que tenemos.
function Test-LinkShape([string]$val) {
    if ([string]::IsNullOrWhiteSpace($val)) { return $false }
    return [bool]([regex]::IsMatch($val.Trim(), '^https?://(fb\.me|(www\.)?instagram\.com)/'))
}

function Get-CleanLink([string]$val) {
    return $val.Trim()
}

# La exportacion completa de una pestana (Refresh-Headless.ps1 > Get-ExportCsv) deja las filas totalmente vacias que hay entre las ventas como una linea de solo comas; gviz las omite.
# El build las pondria en cuarentena como "fecha ilegible". Se quitan SOLO fuera de comillas: un campo de varias lineas puede traer cualquier cosa por dentro. El resto del archivo
# queda como lo mando Google. Vive aqui, y no en el refresco, para poder probarla sin red (Probar-FechasDeVenta.ps1).
function Remove-FilasVacias([string]$csv) {
    $salida = New-Object System.Collections.Generic.List[string]
    $dentro = $false
    foreach ($linea in ($csv -split "`n")) {
        if (-not $dentro -and $linea -match '^,*\r?$') { continue }
        $salida.Add($linea)
        if ((($linea.Length - $linea.Replace('"', '').Length) % 2) -eq 1) { $dentro = -not $dentro }
    }
    $texto = $salida -join "`n"
    # Conserva el salto de linea final del original (el ultimo elemento del split quedo vacio y se descarto arriba).
    if ($csv.EndsWith("`n")) { $texto += "`n" }
    return $texto
}

# Huella semantica de una fila: quien, que dia, cuanto, con que Post ID.
#
# Antes se comparaba el TEXTO de la fila para detectar dos CSV de la misma pestana, y por
# eso Peru conto Dinamicas Mujer dos veces: la misma pestana bajada en dos momentos trae
# la fecha como "1/5/2026" en un export y como "46143.829..." en el otro, y el monto como
# "10" o "10.0". Ni una sola linea coincidia textualmente, asi que los dos archivos
# inflados). Comparando la fila ya interpretada, los dos formatos dan la misma huella.
#
# Devuelve $null cuando la fila no tiene identidad confiable: sin telefono y con un
# NOMBRE que no identifica a nadie (".", un emoji, dos letras). En Venezuela media hoja
# se llama "." y varias personas compran el mismo dia el mismo monto; tratarlas como la
# misma venta borraria ventas reales, que es peor que contar de mas. Sin huella, la fila
# simplemente no participa de la deduplicacion.
function Get-HuellaFila($row) {
    $fecha = Convert-SaleDate ([string]$row.FECHA)
    $monto = Convert-Amount ([string]$row.'PAGO RECIBIDO')
    if ($null -eq $fecha -or $null -eq $monto) { return $null }
    $nombre = ([string]$row.NOMBRE).Trim().ToLower()
    # El telefono llega como "59990000001" en un export y como "5.9990000001E10" en otro:
    # el mismo numero escrito distinto. Se normaliza por valor, no quitando caracteres, o
    # las dos formas darian huellas distintas y el duplicado pasaria de largo.
    $telRaw = ([string]$row.'NÚMERO').Trim()
    $tel = ''
    $n = 0.0
    if ($telRaw -match '[eE]' -and [double]::TryParse($telRaw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$n)) {
        $tel = $n.ToString('F0', [Globalization.CultureInfo]::InvariantCulture)
    } else {
        $tel = ($telRaw -replace '[^\d]', '') -replace '^0+', ''
    }
    # Un nombre util tiene al menos 3 letras; "." y los emojis no cuentan.
    $letras = ($nombre -replace '[^\p{L}]', '')
    if (-not $tel -and $letras.Length -lt 3) { return $null }
    $dia = $fecha.ToString('yyyy-MM-dd')
    $m = [math]::Round([double]$monto, 2)
    return ('{0}|{1}|{2}|{3}' -f $nombre, $dia, $m, $tel)
}

# Que CSV de un pais no entran al armado por repetir a otro. UNA sola regla: build-dashboard.ps1 (Get-TabsFor) los descarta y Analyze-Ads.ps1 no frena al pais por ellos.
#
# Se comparan las FILAS, no el nombre del archivo ni la etiqueta.
#
# La columna ETIQUETAS trae varias etiquetas por fila ("Compra realizada Moldes, pendiente
# pago arteterapia"), asi que no identifica la pestana: comparando por ella, Arteterapia y
# Moldes parecian el mismo tab y Guatemala perdia una oferta entera. Las filas si son
# unicas -llevan telefono y Post ID-, asi que dos archivos que comparten casi todas sus
# filas son el mismo tab bajado dos veces.
#
# Y se comparan INTERPRETADAS, no como texto. El mismo tab bajado dos veces trae la fecha
# como "1/5/2026" en un export y como "46143.829..." (serial de Excel) en el otro: ni una
# linea coincide textualmente aunque sea la misma venta. Asi convivieron pe_dinamicas.csv
function Get-HuellasCsv([IO.FileInfo]$f) {
    $h = New-Object 'System.Collections.Generic.HashSet[string]'
    try {
        foreach ($r in (Import-Csv $f.FullName)) {
            $hf = Get-HuellaFila $r
            if ($hf) { [void]$h.Add($hf) }
        }
    } catch {}
    return $h
}

# Recibe los CSV de un pais ordenados por nombre y devuelve uno por cada descartado: pierde, gana y pct (cuanto del menor se repite).
# Se descarta el archivo mas viejo de cada par que se solapa. Un CSV vacio no se compara con nadie: no tiene filas que puedan coincidir.
# No Write-Output aqui: lo que una funcion escribe al stream se pega a su valor de retorno y llegaria como un descartado fantasma.
function Get-CsvRepetidos($todos) {
    $huella = @{}
    foreach ($f in $todos) { $huella[$f.Name] = Get-HuellasCsv $f }
    $descartados = New-Object 'System.Collections.Generic.HashSet[string]'
    for ($i = 0; $i -lt $todos.Count; $i++) {
        for ($j = $i + 1; $j -lt $todos.Count; $j++) {
            $a = $todos[$i]; $b = $todos[$j]
            if ($descartados.Contains($a.Name) -or $descartados.Contains($b.Name)) { continue }
            $ha = $huella[$a.Name]; $hb = $huella[$b.Name]
            $menor = [Math]::Min($ha.Count, $hb.Count)
            if ($menor -lt 3) { continue }
            $comunes = 0
            foreach ($x in $ha) { if ($hb.Contains($x)) { $comunes++ } }
            if (($comunes / $menor) -lt 0.8) { continue }
            $gana   = if ($a.LastWriteTimeUtc -ge $b.LastWriteTimeUtc) { $a } else { $b }
            $pierde = if ($gana.Name -eq $a.Name) { $b } else { $a }
            [void]$descartados.Add($pierde.Name)
            [pscustomobject]@{ pierde = $pierde.Name; gana = $gana.Name; pct = [int](100 * $comunes / $menor) }
        }
    }
}

# Compradores de PRUEBA del equipo. Quien prueba un flujo compra de verdad -el bot anota la venta con el monto del producto-, pero ese dinero nunca entro: la fila es una prueba,
# no una venta. El tablero las deja en 0 (ni cuentan como venta ni suman facturacion, igual que una fila con monto 0), y el cuadre contra la hoja las descuenta igual, para que
# hoja y torre sigan cuadrando. Se reconocen por el telefono o por el nombre EXACTO con el que el webhook las anota (sin importar tildes ni mayusculas).
#   Nombre Prueba = Ana (ofertas y flujos). Pedido del dueno el 2026-10-21: "las de Nombre Prueba dejalas como 0, pues son ventas de prueba".
# Para sumar a otra persona del equipo: su telefono con indicativo y solo digitos (599900000002), o su nombre en minusculas y sin tildes ('nombre prueba').
$script:VENTAS_DE_PRUEBA_TELEFONOS = @()
$script:VENTAS_DE_PRUEBA_NOMBRES   = @()
function Test-VentaDePrueba($row) {
    $nombre = ([string]$row.NOMBRE).Trim()
    if ($nombre) {
        # Nunca debe tumbar el build: si la normalizacion falla en algun runner, se compara el nombre tal cual (minusculas) y la venta cuenta como siempre.
        $n = $nombre.ToLower()
        try { $n = [regex]::Replace($nombre.Normalize([Text.NormalizationForm]::FormD), '\p{Mn}', '').ToLower() } catch { }
        $n = [regex]::Replace($n, '\s+', ' ').Trim()
        if ($script:VENTAS_DE_PRUEBA_NOMBRES -contains $n) { return $true }
    }
    $telRaw = ([string]$row.'NÚMERO').Trim()
    if (-not $telRaw) { $telRaw = ([string]$row.NUMERO).Trim() }
    if (-not $telRaw) { return $false }
    # La misma normalizacion que Get-HuellaFila: el telefono llega como "599900000002" en un export y como "5.99900000002E11" en otro.
    $tel = ''
    $num = 0.0
    if ($telRaw -match '[eE]' -and [double]::TryParse($telRaw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$num)) {
        $tel = $num.ToString('F0', [Globalization.CultureInfo]::InvariantCulture)
    } else {
        $tel = ($telRaw -replace '[^\d]', '') -replace '^0+', ''
    }
    return ($script:VENTAS_DE_PRUEBA_TELEFONOS -contains $tel)
}

# Ventas POR REVISAR (data/montos-en-revision.json; lo escribe scripts/reparar-ventas.cjs en el mismo refresco, antes del build): cifras que no son un precio habitual de su oferta, la hoja mayor
# que el comprobante, lectura dudosa, punto decimal perdido (desde 2026-10-27) y, desde 2026-11-12, el COMPROBANTE DE OTRO DIA (tipo 'recibo_viejo', solo Colombia). No cuentan hasta que alguien
# las confirme (data/montos-confirmados.json, lo lee el reparador). UNA copia para el build (las aparta como cuarentena) y Cuadre-Hojas.ps1 (las descuenta igual: sin esto la hoja y la torre
# no cuadraban y nada lo explicaba). Solo vale si el archivo tiene menos de 3 horas: uno viejo se ignora y todo cuenta como siempre.
$script:MOTIVO_EN_REVISION  = 'en revision: monto por confirmar (no cuenta hasta que se confirme)'
$script:MOTIVO_RECIBO_VIEJO = 'por revisar: comprobante de otro dia (no cuenta hasta que se confirme)'
# @{ pais = Dictionary llave -> motivo }. Vacio si no hay archivo, esta ilegible o es viejo.
function Import-VentasPorRevisar([string]$archivo) {
    $out = @{}
    if (-not (Test-Path $archivo)) { return $out }
    try {
        $rj = Get-Content $archivo -Raw -Encoding UTF8 | ConvertFrom-Json
        $edadH = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - [int64]$rj.generadoMs) / 3600000.0
        if ($edadH -gt 3) { return $out }
        foreach ($p in $rj.paises.PSObject.Properties) {
            $d = New-Object 'System.Collections.Generic.Dictionary[string,string]'      # distingue mayusculas, como los clics de WhatsApp
            foreach ($x in @($p.Value)) {
                $k = [string]$x.k
                if (-not $k) { continue }
                # Si la misma llave sale por los dos motivos, gana el del comprobante (es el que dice que hacer). La llave de una venta con clic NO lleva el dia, y un mismo clic puede traer
                # la venta buena de un dia y el pantallazo reusado de otro: el comprobante de otro dia se guarda con su dia ('c:...|monto|aaaa-mm-dd') para no apartar tambien la buena.
                if (@($x.tipos) -contains 'recibo_viejo') {
                    $dia = [string]$x.dia
                    if ($k.StartsWith('c:') -and $dia -match '^\d{4}-\d{2}-\d{2}$') { $k = $k + '|' + $dia }
                    $d[$k] = $script:MOTIVO_RECIBO_VIEJO
                }
                elseif (-not $d.ContainsKey($k)) { $d[$k] = $script:MOTIVO_EN_REVISION }
            }
            $out[$p.Name] = $d
        }
    } catch { Write-Warning ("montos-en-revision.json ilegible, se ignora: " + $_.Exception.Message); return @{} }
    return $out
}
# La llave de una fila: clic + digitos del pago o, sin clic, telefono + dia + digitos del pago. La MISMA que arma claveRevision en reparar-ventas.cjs (con la celda tal cual: Mexico antes de
# quitarle el IVA). $date es la fecha ya leida de la fila (Convert-SaleDate).
function Get-ClaveRevision($row, [datetime]$date) {
    $kd = ([string]$row.'PAGO RECIBIDO') -replace '[^0-9]', ''
    $kc = ([string]$row.CTWA_CLID).Trim()
    if ($kc) { return 'c:' + $kc + '|' + $kd }
    $colNum = $row.PSObject.Properties | Where-Object { $_.Name -like 'N?MERO' -or $_.Name -eq 'NUMERO' } | Select-Object -First 1
    $kt = if ($colNum) { ([string]$colNum.Value).Trim() } else { '' }
    if ($kt -notmatch '@') { $kt = $kt -replace '[^0-9]', '' }
    return 't:' + $kt + '|' + $date.ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture) + '|' + $kd
}
# El motivo por el que una fila esta POR REVISAR, o $null si cuenta. $mapa = lo de su pais en Import-VentasPorRevisar. Lo usan el build y Cuadre-Hojas.ps1.
function Get-MotivoPorRevisar($mapa, $row, [datetime]$date) {
    if (-not $mapa) { return $null }
    $k = Get-ClaveRevision $row $date
    $m = $null
    if ($mapa.TryGetValue($k, [ref]$m)) { return $m }
    if ($k.StartsWith('c:') -and $mapa.TryGetValue($k + '|' + $date.ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture), [ref]$m)) { return $m }
    return $null
}

# An amount hundreds of times the country's typical order is data entry, not a sale —
# a phone number or ID typed into the payment column. Guatemala had one of 4,781,110,540
# and made its dashboard unreadable. The per-tab quarantine above only catches amounts it
# cannot parse; these parse fine, they are just impossible.
#
# with enough orders for a median to mean anything.
# Returns a hashtable rather than the list directly: anything a PowerShell function
# writes to the output stream joins its return value, so a Write-Output progress line in
# here would silently become an extra element of the orders list.
function Remove-AmountOutliers {
    param($orders, $quarantined, [double]$factor = 500)
    $res = @{ orders = $orders; removed = 0; median = 0 }
    if ($orders.Count -lt 20) { return $res }
    $sorted = @($orders | ForEach-Object { [double]$_.amount } | Sort-Object)
    $median = $sorted[[int]($sorted.Count / 2)]
    if ($median -le 0) { return $res }
    $techo = $median * $factor
    $keep = New-Object System.Collections.Generic.List[object]
    $fuera = 0
    foreach ($o in $orders) {
        if ([double]$o.amount -gt $techo) {
            $fuera++
            $quarantined.Add([PSCustomObject]@{
                offer  = $o.offer; nombre = ''; fecha = $o.date; pago = [string]$o.amount
                reason = "amount $([math]::Round($o.amount,0)) is over $factor x the country median ($([math]::Round($median,0))) - likely a phone/ID typed into the payment column"
            })
        } else { $keep.Add($o) }
    }
    return @{ orders = $keep; removed = $fuera; median = $median }
}

# Una tasa anotada en fx-overrides.json puede ser un numero suelto -850- o un objeto con su
# procedencia -{tasa, fuente, metodo, capturado}-. Las dos formas conviven a proposito:
# escribir "925" a mano en un apuro tiene que seguir funcionando, y la captura automatica si
# guarda de donde salio el numero. Todo el que lea el archivo pasa por aqui, para que ningun
# lector tenga que saber cual de las dos formas le toco.
function Get-TasaDe($v) {
    if ($null -eq $v) { return $null }
    if ($v -is [psobject] -and -not ($v -is [string]) -and ($v.PSObject.Properties.Name -contains 'tasa')) {
        return [double]$v.tasa
    }
    return [double]$v
}
