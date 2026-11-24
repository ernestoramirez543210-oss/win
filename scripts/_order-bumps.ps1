# Lectura de las pestanas "order bump ..." de los libros de ventas (2026-11-11, pedido del dueno: "hagamos lo mismo para todos los paises, ofertas y proximas ofertas").
#
# Cada order bump se configura en SU libro, en una pestana VISIBLE cuyo nombre empieza por "order bump" (copia de la de Sermones de Colombia). La pestana trae una fila de
# encabezados y debajo una fila de valores:
#
#   Pestana de ventas | Precio del order bump | Precio base 1 | Precio base 2 | Precio base 3 | Contar desde | Hasta | Producto
#   zapdata kinesiotaping | 6000 | 15000 | 30000 |  | 2026-10-20 |  | Kinesiotaping PRO ...
#
# y, mas abajo, sus propias cuentas con formulas ("Compras desde esa fecha", "Compras CON order bump"), una nota que empieza por "Ojo:" y la lista de quien lo llevo.
# Refresh-Headless.ps1 baja las pestanas (exportacion por gid) y guarda en data/order-bumps-planilla.json SOLO las filas de arriba de la lista de compradores: la lista lleva
# nombres y telefonos y no sale del libro. build-dashboard.ps1 lee ese archivo con Read-BumpPestana y lo mezcla con data/order-bumps.json (la planilla gana).
#
# Las columnas se reconocen por su ENCABEZADO, sin tildes ni mayusculas, no por su posicion: se puede agregar un 4.o precio base o mover una columna sin romper nada.
# Solo ASCII en este archivo a proposito (PS 5.1 lee un .ps1 sin BOM como ANSI).

$script:MarcasBump = "[" + [char]0x300 + "-" + [char]0x36f + "]"
function ConvertTo-BumpClave([string]$s) {
    if ($null -eq $s) { return '' }
    return ((([string]$s).Trim().Normalize([Text.NormalizationForm]::FormD) -replace $script:MarcasBump, '') -replace '\s+', ' ').ToLowerInvariant()
}

# (es_ES con punto de miles, es_MX con coma de miles), asi que un punto o una coma seguidos de EXACTAMENTE 3 cifras son de miles y con 1 o 2 cifras son decimales: ningun
# precio lleva 3 decimales. Un porcentaje o un texto no es un monto (null).
function ConvertTo-BumpNumero([string]$s) {
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    $t = ($s.Trim() -replace '\s', '') -replace '^(?:\$|[A-Za-z]{1,3}\$?\.?)', '' -replace '[A-Za-z]+\.?$', ''
    if (-not $t) { return $null }
    if ($t -match '^\d{1,3}([.,]\d{3})+$') { $t = $t -replace '[.,]', '' }
    elseif ($t -match '^\d{1,3}(\.\d{3})+,\d{1,2}$') { $t = ($t -replace '\.', '') -replace ',', '.' }
    elseif ($t -match '^\d{1,3}(,\d{3})+\.\d{1,2}$') { $t = $t -replace ',', '' }
    elseif ($t -match '^\d+,\d{1,2}$') { $t = $t -replace ',', '.' }
    if ($t -notmatch '^\d+(\.\d{1,2})?$') { return $null }
    return [double]::Parse($t, [Globalization.CultureInfo]::InvariantCulture)
}

# La fecha: la celda tiene formato AAAA-MM-DD (la pestana lo trae puesto), pero si alguien la escribe a mano se acepta tambien dia/mes/ano, como los libros (es_ES / es_MX).
function ConvertTo-BumpFecha([string]$s) {
    if ([string]::IsNullOrWhiteSpace($s)) { return '' }
    $t = $s.Trim()
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $d = [datetime]::MinValue
    foreach ($f in @('yyyy-MM-dd', 'yyyy-M-d', 'd/M/yyyy', 'dd/MM/yyyy', 'd/M/yyyy H:mm:ss', 'd/M/yyyy H:mm')) {
        if ([datetime]::TryParseExact($t, $f, $inv, [Globalization.DateTimeStyles]::None, [ref]$d)) { return $d.ToString('yyyy-MM-dd', $inv) }
    }
    return $null   # escrita pero ilegible (distinto de vacia)
}

# $filas: lista de filas, cada una una lista de celdas (texto, tal como las mostro el libro). Devuelve un hashtable con lo leido y, si algo falta o no se entiende, 'error'
# con la razon en palabras (va al log del build). No lanza.
function Read-BumpPestana($filas) {
    $r = @{ pestanaVentas = ''; precio = $null; preciosBase = @(); desde = ''; hasta = ''; nombre = ''; nota = ''; compras = $null; con = $null; error = '' }
    try {
        $filas = @($filas)
        $iEnc = -1
        for ($i = 0; $i -lt $filas.Count; $i++) {
            foreach ($c in @($filas[$i])) { if ((ConvertTo-BumpClave $c) -eq 'pestana de ventas') { $iEnc = $i; break } }
            if ($iEnc -ge 0) { break }
        }
        if ($iEnc -lt 0) { $r.error = "no tiene la fila de encabezados (una celda que diga 'Pestana de ventas')"; return $r }
        if ($iEnc + 1 -ge $filas.Count) { $r.error = 'no tiene la fila de valores debajo de los encabezados'; return $r }
        $enc = @($filas[$iEnc]); $val = @($filas[$iEnc + 1])
        $bases = New-Object System.Collections.Generic.List[double]
        $baseMala = ''
        $desdeCrudo = ''; $hastaCrudo = ''; $precioCrudo = ''
        for ($j = 0; $j -lt $enc.Count; $j++) {
            $k = ConvertTo-BumpClave $enc[$j]
            $v = if ($j -lt $val.Count -and $null -ne $val[$j]) { ([string]$val[$j]).Trim() } else { '' }
            if (-not $k) { continue }
            if ($k -eq 'pestana de ventas') { $r.pestanaVentas = $v }
            elseif ($k.StartsWith('precio del order bump') -or $k.StartsWith('precio del bump')) { $precioCrudo = $v }
            elseif ($k.StartsWith('precio base')) {
                if ($v) { $n = ConvertTo-BumpNumero $v; if ($null -ne $n -and $n -gt 0) { if (-not $bases.Contains($n)) { $bases.Add($n) } } else { $baseMala = $v } }
            }
            elseif ($k.StartsWith('contar desde') -or $k -eq 'desde') { $desdeCrudo = $v }
            elseif ($k.StartsWith('hasta')) { $hastaCrudo = $v }
            elseif ($k.StartsWith('producto')) { $r.nombre = $v }
        }
        # Las cuentas de la propia pestana (columna B, rotulo en la A) y la nota "Ojo: ...": sirven para cuadrar en el log y para la ayuda de la tarjeta.
        for ($i = $iEnc + 2; $i -lt $filas.Count; $i++) {
            $f = @($filas[$i]); if (-not $f.Count) { continue }
            $a = ConvertTo-BumpClave $f[0]
            $b = if ($f.Count -gt 1 -and $null -ne $f[1]) { ([string]$f[1]).Trim() } else { '' }
            if ($a.StartsWith('compras con order bump')) { $r.con = ConvertTo-BumpNumero $b }
            elseif ($a.StartsWith('compras de') -or $a.StartsWith('compras desde')) { $r.compras = ConvertTo-BumpNumero $b }
            elseif ($a.StartsWith('ojo:') -and -not $r.nota) { $r.nota = ([string]$f[0]).Trim() -replace '^(?i)ojo:\s*', '' }
        }
        if (-not $r.pestanaVentas) { $r.error = "falta la pestana de ventas (debajo de 'Pestana de ventas')"; return $r }
        $p = ConvertTo-BumpNumero $precioCrudo
        if ($null -eq $p -or $p -le 0) { $r.error = ("el precio del order bump tiene que ser un numero mayor que 0 (dice '{0}')" -f $precioCrudo); return $r }
        $r.precio = $p
        if ($baseMala) { $r.error = ("un precio base no es un numero mayor que 0 (dice '{0}')" -f $baseMala); return $r }
        if (-not $bases.Count) { $r.error = 'no tiene ningun precio base'; return $r }
        $r.preciosBase = @($bases | Sort-Object)
        $d = ConvertTo-BumpFecha $desdeCrudo
        if (-not $d) { $r.error = ("'Contar desde' tiene que ser una fecha (dice '{0}')" -f $desdeCrudo); return $r }
        $r.desde = $d
        if ($hastaCrudo) {
            $h = ConvertTo-BumpFecha $hastaCrudo
            if (-not $h) { $r.error = ("'Hasta' tiene que ser una fecha o estar vacia (dice '{0}')" -f $hastaCrudo); return $r }
            if ($h -lt $d) { $r.error = "'Hasta' es anterior a 'Contar desde'"; return $r }
            $r.hasta = $h
        }
    } catch {
        $r.error = ('no se pudo leer ({0})' -f $_.Exception.Message)
    }
    return $r
}

# Las filas que se guardan de una pestana: SOLO las que Read-BumpPestana usa, por lista blanca. La fila de encabezados ("Pestana de ventas" ...), la de valores justo debajo
# y, despues, las de rotulo conocido en la columna A ("Compras de/desde ...", "Compras CON order bump", "Ojo: ...") con sus dos primeras celdas. Todo lo demas -el titulo, la
# explicacion y sobre todo la lista de compradores con nombres y telefonos, se llame como se llame su titulo o este en la columna que este- se queda en el libro. Sin fila de
# encabezados no se guarda nada (el build dira que falta). Tope de 30 filas. $objs viene de ConvertFrom-Csv con encabezados c1..cN.
function Select-FilasBump($objs, [int]$cols) {
    $out = New-Object System.Collections.Generic.List[object]
    $iEnc = -1; $i = -1
    foreach ($o in @($objs)) {
        $i++
        $fila = @()
        for ($j = 1; $j -le $cols; $j++) { $v = $o.("c$j"); $fila += $(if ($null -eq $v) { '' } else { [string]$v }) }
        if ($iEnc -lt 0) {
            if (@($fila | Where-Object { (ConvertTo-BumpClave $_) -eq 'pestana de ventas' }).Count) { $iEnc = $i; $out.Add($fila) }
            continue
        }
        if ($i -eq $iEnc + 1) { $out.Add($fila); continue }
        $a = ConvertTo-BumpClave $fila[0]
        if ($a.StartsWith('compras con order bump') -or $a.StartsWith('compras de') -or $a.StartsWith('compras desde') -or $a.StartsWith('ojo:')) { $out.Add(@($fila[0], $fila[1])) }
        if ($out.Count -ge 30) { break }
    }
    return ,$out.ToArray()
}

# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Order bumps SIN MEDIR (2026-11-11, pedido del dueno: "con una oferta nueva, ya se crea solo?"). El dash mide un bump solo si alguien crea su pestana "order bump ..."; si no, nadie lo notaba.
# scripts/order-bumps-zapdata.cjs deja en data/order-bumps-zapdata.json los bumps que la revision de flujos del dash encontro en las Ventas ENCENDIDAS de ZapData (precio, producto, precios base,
# etiqueta "Pago Pendiente X"); build-dashboard.ps1 los compara aqui con los bumps que mide esta hora (pestanas + respaldo) y lo dice en el log y en order-bumps-sin-medir.json (solo el dueno).

# El nombre de una oferta para comparar ZapData con las pestanas: sin tildes, sin signos y sin "pago pendiente " ni "zapdata " delante. "Pago Pendiente Sermones Biblicos" (la etiqueta del
# flujo), "Sermones Biblicos" (el nombre del flujo), "zapdata sermones biblicos" (la pestana de ventas) y "Zapdata Sermones Biblicos" (la oferta del dash) son la misma.
function ConvertTo-BumpOferta([string]$s) {
    $k = ((ConvertTo-BumpClave $s) -replace '[^a-z0-9]+', ' ').Trim()
    $k = $k -replace '^pago pendiente\s+', ''
    return ($k -replace '^zapdata\s+', '')
}

# Un monto como lo escribe la gente en Colombia: 6000 -> "6.000", 40 -> "40", 2.5 -> "2,50".
function Format-BumpMonto($n) {
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $d = [double]$n
    if ([Math]::Abs($d - [Math]::Round($d)) -lt 0.005) { return ($d.ToString('N0', $inv) -replace ',', '.') }
    return ((($d.ToString('N2', $inv) -replace ',', '_') -replace '\.', ',') -replace '_', '.')
}

# $zap: lo que dejo order-bumps-zapdata.cjs (objeto de ConvertFrom-Json) o $null. $medidos: los bumps que el dash mide esta hora, [{ pais, oferta (la del dash), ofertaCfg (la pestana de ventas
# o la oferta del respaldo), pestana, precio, hasta }]. $hoy: AAAA-MM-DD en Colombia (una pestana con "Hasta" anterior a hoy ya no mide).
# Devuelve @{ estado = 'ok'|'sin_dato'|'error'; motivo; mensaje; avisos (para el dueno); lineas (para el log, las de aviso empiezan por "ATENCION") }. No lanza.
# Un bump de ZapData esta MEDIDO si hay una pestana (o entrada del respaldo) del mismo pais y la misma oferta, sin "Hasta" vencido, con el MISMO precio. Si no:
#   sin_pestana     = ninguna la mide (si el precio no se pudo leer, el aviso lo dice y no pone cifra);
#   cerrada         = la hay, pero dice que el bump se quito ("Hasta" pasado) y el flujo lo sigue ofreciendo;
#   precio_distinto = la hay, pero con otro precio (las compras con el precio nuevo no cuentan).
function Compare-BumpsZapData($zap, $medidos, [string]$hoy) {
    $r = @{ estado = ''; motivo = ''; mensaje = ''; avisos = New-Object System.Collections.Generic.List[object]; lineas = New-Object System.Collections.Generic.List[string] }
    try {
        if ($null -eq $zap) { $r.estado = 'sin_dato'; $r.motivo = 'sin_archivo'; $r.lineas.Add('Order bumps en ZapData: no se revisaron esta hora (no hay data/order-bumps-zapdata.json).'); return $r }
        if ($zap.ok -ne $true) {
            $r.estado = 'sin_dato'; $r.motivo = [string]$zap.motivo; $r.mensaje = [string]$zap.mensaje
            $r.lineas.Add(("Order bumps en ZapData: no se pudieron revisar esta hora ({0}). {1}" -f $zap.motivo, $zap.mensaje)); return $r
        }
        $r.estado = 'ok'
        $med = @($medidos | Where-Object { $null -ne $_ })
        $bumps = @($zap.bumps | Where-Object { $null -ne $_ })
        foreach ($b in $bumps) {
            $pais = ([string]$b.pais).Trim().ToLowerInvariant()
            $claves = @()
            foreach ($s in @([string]$b.etiqueta, [string]$b.oferta)) { $k = ConvertTo-BumpOferta $s; if ($k -and $claves -notcontains $k) { $claves += $k } }
            $todos = @($med | Where-Object { ([string]$_.pais).Trim().ToLowerInvariant() -eq $pais -and (($claves -contains (ConvertTo-BumpOferta ([string]$_.oferta))) -or ($claves -contains (ConvertTo-BumpOferta ([string]$_.ofertaCfg)))) })
            $activos = @($todos | Where-Object { -not [string]$_.hasta -or [string]$_.hasta -ge $hoy })
            $precio = $null
            if ($null -ne $b.precio) { $pv = 0.0; if ([double]::TryParse([string]$b.precio, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$pv) -and $pv -gt 0) { $precio = $pv } }
            $motivo = ''
            if (-not $activos.Count) { $motivo = $(if ($todos.Count) { 'cerrada' } else { 'sin_pestana' }) }
            elseif ($null -ne $precio -and -not @($activos | Where-Object { [Math]::Abs([double]$_.precio - $precio) -lt 0.5 }).Count) { $motivo = 'precio_distinto' }
            $flujo = if ([string]$b.nombre) { [string]$b.nombre } else { ('Venta ' + [string]$b.oferta + ' ' + [string]$b.paisNombre).Trim() }
            $prod = [string]$b.producto
            $cuanto = if ($null -ne $precio) { Format-BumpMonto $precio } else { '' }
            $nombresP = (@($todos | ForEach-Object { if ([string]$_.pestana) { "'" + [string]$_.pestana + "'" } else { 'el respaldo data/order-bumps.json' } }) -join ', ')
            if (-not $motivo) {
                $r.lineas.Add(("Order bump de ZapData '{0}' ({1}): lo mide {2}." -f $flujo, $(if ($cuanto) { $cuanto } else { 'precio sin leer' }), $nombresP))
                continue
            }
            $deProd = if ($prod) { " '" + $prod + "'" } else { '' }
            $baseTxt = (@($b.bases | Where-Object { $null -ne $_ } | ForEach-Object { Format-BumpMonto $_ }) -join ' y ')
            $msg = switch ($motivo) {
                'sin_pestana' {
                    if ($null -ne $precio) {
                        "ATENCION order bump sin medir: el flujo '{0}' ofrece{1} por {2} y ninguna pestana 'order bump ...' del libro de {3} lo mide. Duplica una pestana 'order bump ...' de ese libro, cambia la fila 5 (pestana de ventas, precio {2}, precios base{4}, fecha) y dejala visible: a la hora siguiente sale la tarjeta." -f $flujo, $deProd, $cuanto, $pais, $(if ($baseTxt) { ' ' + $baseTxt } else { '' })
                    } else {
                        "ATENCION order bump sin medir: el flujo '{0}' tiene un bloque de order bump{1}, pero su precio no se pudo leer con seguridad (no se pone cifra). Revisalo en el flujo y crea su pestana 'order bump ...' en el libro de {2}." -f $flujo, $deProd, $pais
                    }
                }
                'cerrada' {
                    $h = (@($todos | ForEach-Object { [string]$_.hasta } | Sort-Object -Descending) | Select-Object -First 1)
                    "ATENCION order bump sin medir: el flujo '{0}' sigue ofreciendo{1}{2}, pero {3} dice que se quito (Hasta {4}). Si lo volvieron a poner, duplica la pestana con la fecha nueva y sin 'Hasta'." -f $flujo, $deProd, $(if ($cuanto) { ' por ' + $cuanto } else { '' }), $nombresP, $h
                }
                default {
                    $pp = (@($activos | ForEach-Object { Format-BumpMonto $_.precio } | Sort-Object -Unique) -join ' / ')
                    "ATENCION order bump sin medir: el flujo '{0}' cobra {1} por{2}, pero {3} mide {4}: las compras con el precio nuevo no cuentan. Si cambio el precio, pon 'Hasta' en la vieja y duplicala con el precio nuevo." -f $flujo, $cuanto, $(if ($deProd) { $deProd } else { ' el order bump' }), $nombresP, $pp
                }
            }
            $r.lineas.Add($msg)
            # [pscustomobject] y no [ordered]: en PS 5.1, ConvertTo-Json de @(<lista de diccionarios>) falla con "Los tipos de argumentos no coinciden" (visto el 2026-11-11).
            $r.avisos.Add([pscustomobject][ordered]@{
                motivo = $motivo; pais = $pais; paisNombre = [string]$b.paisNombre; oferta = [string]$b.oferta; flujo = $flujo; venta = [string]$b.venta; etiqueta = [string]$b.etiqueta
                precio = $precio; producto = $prod; bases = @($b.bases | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ })
                pestanas = @($todos | ForEach-Object { [pscustomobject][ordered]@{ pestana = [string]$_.pestana; precio = [double]$_.precio; hasta = [string]$_.hasta } })
            })
        }
        $pend = 0; [void][int]::TryParse([string]$zap.pendientes, [ref]$pend)
        $resto = if ($pend -gt 0) { (" Faltan {0} flujo(s) de venta por leer en ZapData: se siguen leyendo en cada refresco, y un bump de esos todavia no se ve." -f $pend) } else { '' }
        $trab = 0; [void][int]::TryParse([string]$zap.noLeidas, [ref]$trab)
        if ($trab -gt 0) { $resto += (" {0} de ellos no se dejaron leer (esperan su turno)." -f $trab) }
        if ([string]$zap.pausa) { $resto += (" ZapData esta en pausa ({0}): se retoma sola." -f $zap.pausa) }
        $r.lineas.Add(("Order bumps en ZapData: {0} en {1} flujo(s) de venta encendidos, {2} sin medir.{3} Ecuador no se revisa (su ZapData es otra cuenta)." -f $bumps.Count, $zap.total, $r.avisos.Count, $resto))
    } catch {
        $r.estado = 'error'; $r.motivo = 'error'; $r.mensaje = $_.Exception.Message
        $r.lineas.Add(('Order bumps en ZapData: no se pudieron comparar con las pestanas ({0}).' -f $_.Exception.Message))
    }
    return $r
}
