# Que anuncios PROPIOS vendieron mas en cada oferta y pais, para «Revivir ofertas» de /tablero (pestaña Variaciones).
#
# Pedido del dueño (2026-11-12): «las variaciones que me recomiendas se hicieron en base a que anuncio? esos se supone que son anuncios que ya tenemos y vamos a editarlos
# poniendoles alguna variacion» -> «hagamoslo en base a los anuncios que mas hayan vendido». Una variacion parte de NUESTRO anuncio que mas vendio (el 101 de Sermones en
# Colombia, el 105 de Kinesiotaping) y le cambia el gancho o el angulo; este archivo dice cual es, con sus ventas, su gasto y las variaciones que ya se le hicieron.
#
# Lo usa Build-Suite.ps1 (lo carga con «.» y llama Get-AnunciosOfertas con el snapshot que ya leyo) y lo prueba scripts\probar-revivir.cjs con un snapshot inventado.
# Solo aritmetica: decidir a que PRODUCTO pertenece cada grupo (juntar «Zapdata Sermones Biblicos» con la campaña «SERMONES») lo hace la pagina con el mismo criterio del
# detector (templates\revivir-ofertas.js), para que las dos partes no se peleen.
#
# Fuentes, todas del snapshot (las mismas del explorador de los dashboards):
#   - orders: cada venta con su oferta, fecha, monto en moneda local y postId = el ID del anuncio de Meta que trajo el chat (desde fines de septiembre casi todas lo traen).
#   - adMeta: nombre del anuncio, campaña y estado de cada anuncio de Meta.
#   - spendByAdIdDate: gasto por anuncio y dia, YA en dolares.
# Ventana: el mismo año que mira el detector (365 dias hasta hoy en Colombia).

# El NUMERO del creativo, que es como el equipo nombra los anuncios en Meta: «SERMONES | 17 OCT | INT > COM | 105» -> «105», «101.17 afro» -> «101.17», «AD 102 | 2 | AIRFRYER | CBO 3»
# -> «AD 102», «AD2 S BOLIS | DP1 | 06-08-26» -> «AD 2». Las copias del mismo video en otras campañas llevan el mismo numero y se suman. Sin numero, el nombre tal cual (corto).
function Get-EtiquetaAnuncio([string]$nombre) {
    $t = ([string]$nombre).Trim() -replace '(?i)\s*-\s*cop(ia|y)\b.*$', ''
    if (-not $t) { return '' }
    $segs = @($t -split '\|' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    # 1) «AD 101», «AD101.1», «AD10 ...»: la marca AD manda, en cualquier pedazo.
    foreach ($sg in $segs) {
        $m = [regex]::Match($sg, '(?i)\bAD\s*(\d{1,4}(?:\.\d{1,3})*)')
        if ($m.Success) { return 'AD ' + $m.Groups[1].Value }
    }
    # 2) Un numero de 3 cifras con sus decimales (101, 105.6, 101.2.1, «101.» o «102. (trofeo)» de Peru), del ultimo pedazo al primero: el numero va al final y las fechas
    #    («28 AGOS», «06-08-26») y los años no son de 3 cifras sueltas.
    for ($i = $segs.Count - 1; $i -ge 0; $i--) {
        $m = [regex]::Match($segs[$i], '(?:^|\s)(\d{3}(?:\.\d{1,3})*)(?!\.?\d)')
        if ($m.Success) { return $m.Groups[1].Value }
    }
    if ($t.Length -gt 40) { $t = $t.Substring(0, 40).Trim() }
    return $t
}

# -> [ordered]@{ generadoEn; desde; hasta; gastoDesde = @{ pais = 'AAAA-MM-DD' }; grupos = @( [ordered]@{ o; p; n; id; l = @( @(a, v, u, g, d1, d2, act, c, ug), ... ) } ) }
#   Un grupo por oferta y pais. o = la oferta como la escribe la hoja de ventas, o la de la campaña para los anuncios que no vendieron (asi no se pierde su gasto ni su numero,
#   que cuenta al proponer el de la proxima variacion); n = ventas de la hoja en la ventana; id = de esas, cuantas traen un anuncio con nombre (la pagina dice «en 474 de las 476»).
#   l = una fila por numero de creativo, la que mas vendio primero: a = numero; v = ventas; u = ventas en dolares (con la tasa del dia de cada venta); g = gasto en dolares;
#   d1/d2 = primera y ultima venta ('' si no vendio); act = copias encendidas hoy; c = copias (anuncios de Meta con ese numero); ug = los dolares de las ventas de dias en que el
#   dash YA guardaba el gasto (gastoDesde del pais: el primer dia con gasto guardado; en Colombia, Venezuela y Guatemala, el 21-abr-2026). Los «dolares por cada dolar» se sacan con
#   ug y no con u: ventas de antes de esa fecha sin su gasto inflaban la cifra (revisor-cambios, 2026-11-13: Dinamicas Mujer Guatemala daba 9,4). Listas y no objetos: /tablero las lleva todas.
# $ofertaDeCampana: scriptblock que lee la oferta del nombre de una campaña (Build-Suite pasa su Get-OfertaDeCampana, la misma de /numeros).
function Get-AnunciosOfertas($s, [datetime]$hoyCo, [scriptblock]$ofertaDeCampana, [int]$dias = 365) {
    $res = [ordered]@{ generadoEn = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'); desde = ''; hasta = ''; gastoDesde = [ordered]@{}; grupos = @() }
    if (-not $s -or -not $s.countries) { return $res }
    $desde = $hoyCo.Date.AddDays(-($dias - 1)).ToString('yyyy-MM-dd')
    $hasta = $hoyCo.Date.ToString('yyyy-MM-dd')
    $res.desde = $desde; $res.hasta = $hasta

    # La tasa que regia el DIA de cada venta (igual que TasaNum de Build-Suite, FxEn de Sync-Offers.ps1 y fxEn() del dashboard): el bolivar cambia por dia; las demas usan la de hoy.
    # Se guarda por moneda y dia: son decenas de miles de ventas y pocas fechas.
    $fx = $s.fx
    $fxHist = @{}
    if ($fx -and $fx.historia) {
        foreach ($mh in $fx.historia.PSObject.Properties) {
            $fxHist[$mh.Name] = @($mh.Value.PSObject.Properties | Sort-Object Name | ForEach-Object { [pscustomobject]@{ f = [string]$_.Name; v = [double]$_.Value } })
        }
    }
    $tasas = @{}

    $grupos = New-Object System.Collections.ArrayList
    foreach ($cn in $s.countries.PSObject.Properties.Name) {
        $c = $s.countries.$cn
        if (-not $c) { continue }
        $mon = [string]$c.currency
        $hoyFx = 1.0
        if ($mon -and $mon -ne 'USD') { $hoyFx = 0.0; if ($fx -and $fx.$mon) { $hoyFx = [double]$fx.$mon } }
        # Diccionarios de una pasada: buscar por nombre en PSObject.Properties recorre la lista entera cada vez (miles de anuncios por miles de ventas).
        $meta = @{}
        if ($c.adMeta) { foreach ($p in $c.adMeta.PSObject.Properties) { $meta[[string]$p.Name] = $p.Value } }
        $gasto = @{}
        $primerGasto = ''
        if ($c.spendByAdIdDate) {
            foreach ($p in $c.spendByAdIdDate.PSObject.Properties) {
                $g = 0.0
                foreach ($dp in $p.Value.PSObject.Properties) {
                    $val = $dp.Value
                    if ($val -is [array]) { $val = $val[0] }
                    # El primer dia con gasto guardado en el pais (aunque caiga fuera de la ventana): antes de el, el dash no sabe cuanto costo una venta.
                    if ([double]$val -gt 0 -and (-not $primerGasto -or [string]::CompareOrdinal($dp.Name, $primerGasto) -lt 0)) { $primerGasto = [string]$dp.Name }
                    if ([string]::CompareOrdinal($dp.Name, $desde) -lt 0 -or [string]::CompareOrdinal($dp.Name, $hasta) -gt 0) { continue }
                    $g += [double]$val
                }
                if ($g -gt 0) { $gasto[[string]$p.Name] = $g }
            }
        }
        if ($primerGasto) { $res.gastoDesde[$cn] = $primerGasto }

        $porAd = @{}
        $tot = @{}
        foreach ($o in @($c.orders)) {
            if (-not $o) { continue }
            $f = [string]$o.date
            if ([string]::CompareOrdinal($f, $desde) -lt 0 -or [string]::CompareOrdinal($f, $hasta) -gt 0) { continue }
            $of = ([string]$o.offer).Trim()
            if (-not $of) { continue }
            if (-not $tot.ContainsKey($of)) { $tot[$of] = @{ n = 0; id = 0 } }
            $tot[$of].n += 1
            $ad = [string]$o.postId
            if (-not $ad -or -not $meta.ContainsKey($ad)) { continue }
            $tot[$of].id += 1
            # Por anuncio Y oferta: si un anuncio trajo ventas de dos ofertas (un chat que pidio otra cosa), cada venta cuenta en la suya.
            if (-not $porAd.ContainsKey($ad)) { $porAd[$ad] = @{} }
            $xa = $porAd[$ad]
            if (-not $xa.ContainsKey($of)) { $xa[$of] = @{ v = 0; u = 0.0; ug = 0.0; d1 = ''; d2 = '' } }
            $x = $xa[$of]
            $x.v += 1
            $tx = $hoyFx
            if ($fxHist.ContainsKey($mon)) {
                $kt = $mon + ' ' + $f
                if ($tasas.ContainsKey($kt)) { $tx = $tasas[$kt] } else {
                    $t = $null
                    foreach ($e in $fxHist[$mon]) { if ([string]::CompareOrdinal($e.f, $f) -le 0) { $t = $e.v } else { break } }
                    if ($null -ne $t) { $tx = $t }
                    $tasas[$kt] = $tx
                }
            }
            if ($tx -gt 0) {
                $usd = ([double]$o.amount) / $tx
                $x.u += $usd
                if ($primerGasto -and [string]::CompareOrdinal($f, $primerGasto) -ge 0) { $x.ug += $usd }
            }
            if (-not $x.d1 -or [string]::CompareOrdinal($f, $x.d1) -lt 0) { $x.d1 = $f }
            if (-not $x.d2 -or [string]::CompareOrdinal($f, $x.d2) -gt 0) { $x.d2 = $f }
        }

        $porOferta = @{}
        foreach ($k in $tot.Keys) { $porOferta[$k] = [ordered]@{ o = $k; p = $cn; n = $tot[$k].n; id = $tot[$k].id; f = @{} } }
        foreach ($ad in $meta.Keys) {
            $m = $meta[$ad]
            $xa = $porAd[$ad]
            $g = 0.0; if ($gasto.ContainsKey($ad)) { $g = $gasto[$ad] }
            # Sin ventas ni gasto en la ventana no cuenta, salvo que este ENCENDIDO: un anuncio recien subido que aun no gasta ya usa su numero (101.7) y la proxima no debe repetirlo.
            if (-not $xa -and $g -le 0 -and [string]$m.effectiveStatus -ne 'ACTIVE') { continue }
            $a = Get-EtiquetaAnuncio ([string]$m.adName)
            if (-not $a) { continue }
            # La oferta del anuncio (la que se lleva su gasto, la copia y si esta encendido): la de sus ventas -la que mas se repite; empate = la primera en orden
            # alfabetico- o, si no vendio, la del nombre de su campaña.
            $of = ''
            if ($xa) {
                $mejor = -1
                foreach ($k in ($xa.Keys | Sort-Object)) { if ($xa[$k].v -gt $mejor) { $mejor = $xa[$k].v; $of = $k } }
            } elseif ($ofertaDeCampana -and $m.campaign) {
                $of = ([string](& $ofertaDeCampana ([string]$m.campaign))).Trim()
            }
            $ofertasDelAnuncio = @()
            if ($of) { $ofertasDelAnuncio += $of }
            if ($xa) { foreach ($k in $xa.Keys) { if ($k -ne $of) { $ofertasDelAnuncio += $k } } }
            foreach ($k in $ofertasDelAnuncio) {
                if (-not $porOferta.ContainsKey($k)) { $porOferta[$k] = [ordered]@{ o = $k; p = $cn; n = 0; id = 0; f = @{} } }
                $po = $porOferta[$k]
                if (-not $po.f.ContainsKey($a)) { $po.f[$a] = @{ v = 0; u = 0.0; ug = 0.0; g = 0.0; d1 = ''; d2 = ''; act = 0; c = 0 } }
                $fi = $po.f[$a]
                if ($k -eq $of) {
                    $fi.c += 1
                    $fi.g += $g
                    if ([string]$m.effectiveStatus -eq 'ACTIVE') { $fi.act += 1 }
                }
                if ($xa -and $xa.ContainsKey($k)) {
                    $x = $xa[$k]
                    $fi.v += $x.v; $fi.u += $x.u; $fi.ug += $x.ug
                    if (-not $fi.d1 -or [string]::CompareOrdinal($x.d1, $fi.d1) -lt 0) { $fi.d1 = $x.d1 }
                    if (-not $fi.d2 -or [string]::CompareOrdinal($x.d2, $fi.d2) -gt 0) { $fi.d2 = $x.d2 }
                }
            }
        }
        foreach ($po in $porOferta.Values) {
            $lista = @($po.f.Keys | Sort-Object @{ Expression = { $po.f[$_].v }; Descending = $true }, @{ Expression = { $_ } } | ForEach-Object {
                $fi = $po.f[$_]
                , @([string]$_, [int]$fi.v, [math]::Round($fi.u, 2), [math]::Round($fi.g, 2), [string]$fi.d1, [string]$fi.d2, [int]$fi.act, [int]$fi.c, [math]::Round($fi.ug, 2))
            })
            [void]$grupos.Add([ordered]@{ o = $po.o; p = $po.p; n = $po.n; id = $po.id; l = $lista })
        }
    }
    # Orden estable (oferta, pais): el mismo snapshot da el mismo texto.
    $res.grupos = @($grupos | Sort-Object @{ Expression = { $_.o } }, @{ Expression = { $_.p } })
    return $res
}
