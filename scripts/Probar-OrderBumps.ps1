# Prueba de la lectura de data/order-bumps.json en build-dashboard.ps1 (el bloque "Order bumps"), sin red y sin datos de clientes.
#
#   powershell -File scripts\Probar-OrderBumps.ps1
#
# Comprueba: una entrada buena llega a countries.<pais>.orderBumps con el nombre EXACTO de la oferta del dash (aunque el archivo la escriba con otras mayusculas o con
# tildes), los precios base ordenados y como lista aunque sea uno solo; cada entrada mal escrita se salta con un aviso que dice por que; una oferta sin ventas entra con aviso;
# un archivo roto no tumba el build; el pais sin bumps no recibe la llave; y el log trae la misma cuenta que la tarjeta (compras con bump de las del periodo).
# Sale con codigo 1 si algo falla. Solo ASCII a proposito.
$ErrorActionPreference = 'Stop'
$src = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'build-dashboard.ps1'))
$i = $src.IndexOf('# Order bumps (data/order-bumps.json')
$j = $src.IndexOf('$snapshot | ConvertTo-Json -Depth 10 | Out-File')
if ($i -lt 0 -or $j -le $i) { Write-Output 'FALLA: no encontre el bloque de order bumps en build-dashboard.ps1'; exit 1 }
$bloque = [scriptblock]::Create($src.Substring($i, $j - $i))
# La lectura de las pestanas "order bump ..." (la carga el build arriba del todo; aqui se carga a mano).
. (Join-Path $PSScriptRoot '_order-bumps.ps1')
# El build escribe order-bumps-sin-medir.json junto a la carpeta data (en la raiz del repo): aqui esa raiz es una carpeta temporal propia, para no dejar nada suelto en %TEMP%.
$Raiz = Join-Path ([IO.Path]::GetTempPath()) ('probar-bumps-' + [guid]::NewGuid().ToString('N'))
$DataDir = Join-Path $Raiz 'data'
New-Item -ItemType Directory $DataDir -Force | Out-Null

$fallos = 0
function Afirma([bool]$c, [string]$m) { if ($c) { "  ok  $m" } else { $script:fallos++; "  FALLA  $m" } }
function Venta([string]$of, [string]$d, [double]$a) { [ordered]@{ offer = $of; date = $d; amount = $a; postId = $null } }
function Foto {
    $col = New-Object System.Collections.Generic.List[object]
    foreach ($v in @(
        (Venta 'Zapdata Sermones Biblicos' '2026-11-03' 21000),     # antes de 'desde': no cuenta
        (Venta 'Zapdata Sermones Biblicos' '2026-11-05' 20000),
        (Venta 'Zapdata Sermones Biblicos' '2026-11-08' 36000),
        (Venta 'Zapdata Sermones Biblicos' '2026-11-09' 15000),
        (Venta 'Salmos' '2026-11-09' 21000))) { $col.Add($v) }
    $mx = New-Object System.Collections.Generic.List[object]
    $mx.Add((Venta 'Dinamicas Mujer' '2026-11-09' 300))
    return [ordered]@{ countries = [ordered]@{ colombia = [ordered]@{ currency = 'COP'; orders = $col }; mexico = [ordered]@{ currency = 'MXN'; orders = $mx } } }
}
function Corre($json) {
    $ruta = Join-Path $DataDir 'order-bumps.json'
    if ($null -eq $json) { Remove-Item $ruta -ErrorAction SilentlyContinue } else { [IO.File]::WriteAllText($ruta, $json, (New-Object Text.UTF8Encoding($true))) }
    $script:snapshot = Foto
    # 6>&1: las lineas de los order bumps de ZapData van con Write-Host (el build corre con '| Out-Null' en Refresh-Headless y Write-Output no llegaria al log)
    $script:salida = @(. $bloque 3>&1 6>&1 | ForEach-Object { [string]$_ })
}
# Se lee con indexadores y no con .colombia.orderBumps: en PS 5.1 el acceso por propiedad a un [ordered] que el bloque acaba de modificar puede lanzar
# "Los tipos de argumentos no coinciden" (visto al escribir esta prueba; el build no lee asi).
function Pais([string]$p) { return $script:snapshot['countries'][$p] }
# Y la lista se copia con foreach: @() sobre esa List[object] tambien lanza el mismo error en PS 5.1 (foreach y ConvertTo-Json, que es lo que usa el build, no).
function Bumps([string]$p) { $c = Pais $p; $r = New-Object System.Collections.ArrayList; if ($c.Contains('orderBumps')) { foreach ($q in $c['orderBumps']) { [void]$r.Add($q) } }; return ,$r.ToArray() }
$i_ = [char]0xED   # la i con tilde, para escribir 'bíblicos' sin salir de ASCII en este archivo

# 1) Entrada buena, con la oferta escrita en minusculas y con tilde.
Corre ('{"bumps":[{"pais":"Colombia","oferta":"zapdata sermones b' + $i_ + 'blicos","nombre":"Producto","desde":"2026-11-05","precio":6000,"preciosBase":[30000,15000],"nota":"una salvedad"}]}')
$b = (Bumps 'colombia')
Afirma ($b.Count -eq 1) 'la entrada buena llega a countries.colombia.orderBumps'
Afirma ($b[0].oferta -ceq 'Zapdata Sermones Biblicos') ("la oferta queda con el nombre exacto del dash: '{0}'" -f $b[0].oferta)
Afirma ((@($b[0].base) -join ',') -eq '15000,30000') 'los precios base quedan ordenados'
Afirma ($b[0].desde -eq '2026-11-05' -and $b[0].precio -eq 6000 -and $b[0].nombre -eq 'Producto' -and $b[0].nota -eq 'una salvedad') 'desde, precio, nombre y nota viajan tal cual'
Afirma (-not $b[0].Contains('hasta')) "sin 'hasta' no se escribe la llave"
Afirma (-not (Pais 'mexico').Contains('orderBumps')) 'el pais sin bumps no recibe la llave'
Afirma (($salida -join '|') -match 'Order bump Zapdata Sermones Biblicos \(colombia\): 1 de 3 compras desde 2026-11-05') ('el log trae la cuenta: ' + ($salida -join ' | '))
$js = (Pais 'colombia') | ConvertTo-Json -Depth 10 -Compress
Afirma ($js -match '"orderBumps":\[\{"oferta":"Zapdata Sermones Biblicos"') 'en el JSON orderBumps es una lista'

# 2) Un solo precio base sigue siendo lista en el JSON (la plantilla hace b.base.some).
Corre '{"bumps":[{"pais":"colombia","oferta":"Zapdata Sermones Biblicos","desde":"2026-11-05","precio":6000,"preciosBase":[15000],"hasta":"2026-11-08"}]}'
$js = (Pais 'colombia') | ConvertTo-Json -Depth 10 -Compress
Afirma ($js -match '"base":\[15000\]') ('un solo precio base sale como lista: ' + ([regex]::Match($js, '"base":[^,}]*')).Value)
Afirma ((Bumps 'colombia')[0].hasta -eq '2026-11-08') "'hasta' viaja cuando esta"
Afirma (($salida -join '|') -match ': 0 de 2 compras desde') "con 'hasta' el log cuenta solo hasta ese dia (20.000 y 36.000 contra base 15.000: ninguna)"

# 3) Entradas mal escritas: cada una se salta con su aviso; las buenas del mismo archivo siguen.
$malas = @(
    @{ j = '{"pais":"chile","oferta":"X","desde":"2026-11-05","precio":6000,"preciosBase":[15000]}'; m = 'el pais no existe' }
    @{ j = '{"pais":"colombia","oferta":"","desde":"2026-11-05","precio":6000,"preciosBase":[15000]}'; m = 'no dice la oferta' }
    @{ j = '{"pais":"colombia","oferta":"Salmos","desde":"5/11/2026","precio":6000,"preciosBase":[15000]}'; m = "'desde' tiene que ser" }
    @{ j = '{"pais":"colombia","oferta":"Salmos","desde":"2026-11-05","hasta":"2026-11-01","precio":6000,"preciosBase":[15000]}'; m = "'hasta' tiene que ser" }
    @{ j = '{"pais":"colombia","oferta":"Salmos","desde":"2026-11-05","precio":0,"preciosBase":[15000]}'; m = "'precio' tiene que ser" }
    @{ j = '{"pais":"colombia","oferta":"Salmos","desde":"2026-11-05","precio":"seis mil","preciosBase":[15000]}'; m = "'precio' tiene que ser" }
    @{ j = '{"pais":"colombia","oferta":"Salmos","desde":"2026-11-05","precio":6000,"preciosBase":["quince"]}'; m = "'preciosBase' tiene que ser" }
    @{ j = '{"pais":"colombia","oferta":"Salmos","desde":"2026-11-05","precio":6000,"preciosBase":[]}'; m = "'preciosBase' tiene que ser" }
)
foreach ($x in $malas) {
    Corre ('{"bumps":[' + $x.j + ',{"pais":"colombia","oferta":"Salmos","desde":"2026-11-01","precio":6000,"preciosBase":[15000]}]}')
    $n = (Bumps 'colombia').Count
    Afirma ((($salida -join '|') -match [regex]::Escape($x.m)) -and $n -eq 1) ("mala ({0}): se salta con aviso y la buena sigue ({1} entrada)" -f $x.m, $n)
}
Corre '{"bumps":[{"pais":"colombia","oferta":"Oferta Que No Existe","desde":"2026-11-05","precio":6000,"preciosBase":[15000]}]}'
Afirma ((($salida -join '|') -match 'no tiene ventas de esa oferta') -and (Bumps 'colombia').Count -eq 1) 'una oferta sin ventas entra, con aviso de revisar el nombre'

# 4) Archivo roto o ausente: el build sigue.
$roto = $true
try { Corre '{"bumps":[{"pais":"colombia",' ; $roto = $false } catch { $roto = $true }
Afirma (-not $roto) 'un JSON roto no lanza error'
Afirma ((($salida -join '|') -match 'no se pudo leer') -and -not (Pais 'colombia').Contains('orderBumps')) 'un JSON roto avisa y el dash queda sin tarjeta'
# (la linea "Order bumps en ZapData: ..." sale siempre: es la revision de los flujos, seccion 8)
function SinZap { return @($script:salida | Where-Object { $_ -notlike 'Order bumps en ZapData*' }) }
Corre '{"bumps":[]}'
Afirma (-not (Pais 'colombia').Contains('orderBumps') -and -not (SinZap).Count) 'una lista vacia no avisa ni agrega nada'
Corre '{}'
Afirma (-not (Pais 'colombia').Contains('orderBumps') -and -not (SinZap).Count) "un archivo sin la llave 'bumps' no avisa ni agrega nada"
Corre $null
Afirma (-not (Pais 'colombia').Contains('orderBumps') -and -not (SinZap).Count) 'sin archivo no pasa nada'

# 5) La pestana "order bump ..." del libro (Read-BumpPestana): se lee por los ENCABEZADOS, no por la posicion.
function Pest([string]$ventas, [string]$precio, [string[]]$bases, [string]$desde, [string]$hasta) {
    $enc = @(('Pesta' + [char]0xF1 + 'a de ventas'), 'Precio del order bump'); $val = @($ventas, $precio)
    for ($k = 0; $k -lt $bases.Count; $k++) { $enc += ('Precio base ' + ($k + 1)); $val += $bases[$k] }
    $enc += @('Contar desde', ('Hasta (si se quit' + [char]0xF3 + ')'), 'Producto'); $val += @($desde, $hasta, 'Un producto')
    return @(
        @('ORDER BUMP X', ''), @('explicacion', ''), @('', ''), $enc, $val, @('', ''),
        @('Compras desde esa fecha', '6'), @('Compras CON order bump', '2'), @(('Conversi' + [char]0xF3 + 'n del order bump'), '33,3%'), @('Plata extra', '12.000'), @('', ''),
        @('Ojo: una salvedad.', ''))
}
$l = Read-BumpPestana (Pest 'zapdata salmos' '6.000' @('15.000', '30000') '2026-11-05' '')
Afirma (-not $l.error -and $l.pestanaVentas -eq 'zapdata salmos' -and $l.precio -eq 6000 -and (($l.preciosBase -join ',') -eq '15000,30000') -and $l.desde -eq '2026-11-05' -and -not $l.hasta) ('pestana buena: ' + $l.error)
Afirma ($l.compras -eq 6 -and $l.con -eq 2 -and $l.nota -eq 'una salvedad.' -and $l.nombre -eq 'Un producto') 'lee las cuentas de la pestana, la nota sin el "Ojo:" y el producto'
$l = Read-BumpPestana (Pest 'zapdata salmos' '6,000' @('30.000', '15000', '', '45000') '5/11/2026' '2026-11-20')
Afirma (-not $l.error -and $l.precio -eq 6000 -and (($l.preciosBase -join ',') -eq '15000,30000,45000') -and $l.desde -eq '2026-11-05' -and $l.hasta -eq '2026-11-20') ('un 4.o precio base, uno vacio, coma de miles y fecha d/M/aaaa: ' + $l.error)
$l = Read-BumpPestana (Pest 'zapdata salmos' '2,50' @('10') '2026-11-05' '')
Afirma (-not $l.error -and $l.precio -eq 2.5) 'un precio con centavos (2,50) se lee 2.5'
$casos = @(
    @{ f = (Pest '' '6000' @('15000') '2026-11-05' ''); m = 'falta la pestana de ventas' }
    @{ f = (Pest 'x' '' @('15000') '2026-11-05' ''); m = 'precio del order bump' }
    @{ f = (Pest 'x' 'seis mil' @('15000') '2026-11-05' ''); m = 'precio del order bump' }
    @{ f = (Pest 'x' '6000' @('', '') '2026-11-05' ''); m = 'ningun precio base' }
    @{ f = (Pest 'x' '6000' @('quince') '2026-11-05' ''); m = 'un precio base' }
    @{ f = (Pest 'x' '6000' @('15000') '' ''); m = "'Contar desde'" }
    @{ f = (Pest 'x' '6000' @('15000') '31/02/2026' ''); m = "'Contar desde'" }
    @{ f = (Pest 'x' '6000' @('15000') '2026-11-05' '2026-11-01'); m = "'Hasta' es anterior" }
    @{ f = (Pest 'x' '6000' @('15000') '2026-11-05' 'pronto'); m = "'Hasta' tiene que ser" }
    @{ f = @(@('sin encabezados', '')); m = 'no tiene la fila de encabezados' }
)
foreach ($c in $casos) { $l = Read-BumpPestana $c.f; Afirma ($l.error -and $l.error.Contains($c.m)) ("pestana mala ({0}): '{1}'" -f $c.m, $l.error) }
# La lista de compradores (nombres y telefonos) NO se guarda: Select-FilasBump guarda por LISTA BLANCA (encabezados, valores y rotulos conocidos), asi que ni cambiando el
# titulo de la lista ("Quien lo llevo"), ni poniendola en otra columna, ni sin titulo, un nombre o un telefono sale del libro (hallazgo del revisor de seguridad, 2026-11-11).
function Objs([object[]]$filas) { return @($filas | ForEach-Object { $f = $_; $o = [ordered]@{}; for ($k = 0; $k -lt 4; $k++) { $o["c$($k + 1)"] = $(if ($k -lt $f.Count) { $f[$k] } else { '' }) }; [pscustomobject]$o }) }
$base = @(@('ORDER BUMP X'), @('explicacion'), @(('Pesta' + [char]0xF1 + 'a de ventas'), 'Precio del order bump', 'Precio base 1', 'Contar desde'), @('zapdata x', '6000', '15000', '2026-11-05'),
    @('Compras desde esa fecha', '6'), @('Compras CON order bump', '1'), @('Ojo: una nota.'))
foreach ($lista in @(
    @(@('Ventas con order bump'), @('Nombre', 'Fecha', 'Numero', 'Pago'), @('Una Persona', '2026-11-08', '573001112233', '21000')),
    @(@('Quien lo llevo'), @('Una Persona', '2026-11-08', '573001112233', '21000')),
    @(@('', 'Una Persona', '2026-11-08', '573001112233')),
    @(@('Una Persona', '573001112233', '', '')))) {
    $sel = Select-FilasBump (Objs ($base + $lista)) 4
    $todo = (($sel | ForEach-Object { $_ -join '|' }) -join '|')
    Afirma ($sel.Count -eq 5 -and -not $todo.Contains('Persona') -and -not $todo.Contains('573001112233') -and -not $todo.Contains('explicacion')) ('la lista de quien lo llevo no sale del libro (' + $lista[0][0] + $lista[0][1] + '): ' + $sel.Count + ' filas')
    $l = Read-BumpPestana $sel
    Afirma (-not $l.error -and $l.compras -eq 6 -and $l.con -eq 1 -and $l.nota -eq 'una nota.') 'y lo guardado alcanza para leer la pestana entera'
}
Afirma ((Select-FilasBump (Objs @(@('Una Persona', '573001112233'), @('otra fila'))) 4).Count -eq 0) 'sin fila de encabezados no se guarda nada'

# 6) El build con la planilla (data/order-bumps-planilla.json): gana sobre el archivo para la misma oferta, agrega las que el archivo no tiene, y una pestana mala no borra el respaldo.
function Planilla([object[]]$pestanas, [object[]]$fallas) {
    $o = [ordered]@{ generado = '2026-11-11T12:00:00'; pestanas = @($pestanas); fallas = @($fallas) }
    return ($o | ConvertTo-Json -Depth 8)
}
function CorreP($json, $planilla) {
    $ruta = Join-Path $DataDir 'order-bumps-planilla.json'
    if ($null -eq $planilla) { Remove-Item $ruta -ErrorAction SilentlyContinue } else { [IO.File]::WriteAllText($ruta, $planilla, (New-Object Text.UTF8Encoding($false))) }
    Corre $json
}
$archivo = '{"bumps":[{"pais":"colombia","oferta":"Zapdata Sermones Biblicos","desde":"2026-11-05","precio":6000,"preciosBase":[15000,30000]}]}'
$pSerm = [ordered]@{ pais = 'colombia'; pestana = 'order bump sermones'; filas = (Pest 'zapdata sermones biblicos' '15000' @('6000') '2026-11-05' '') }
CorreP $archivo (Planilla @($pSerm) @())
$b = (Bumps 'colombia')
Afirma ($b.Count -eq 1 -and $b[0].precio -eq 15000 -and (@($b[0].base) -join ',') -eq '6000') ('la pestana gana sobre el archivo para la misma oferta (precio ' + $b[0].precio + ')')
Afirma ($b[0].oferta -ceq 'Zapdata Sermones Biblicos' -and $b[0].pestana -eq 'order bump sermones') ('la oferta sale con el nombre del dash y lleva su pestana: ' + $b[0].oferta)
Afirma (($salida -join '|') -match 'Order bump Zapdata Sermones Biblicos \(colombia\): 0 de 3 compras desde 2026-11-05; la pestana dice 2 de 6') ('el log trae la cuenta del dash y la de la pestana: ' + ($salida -join ' | '))
$pSal = [ordered]@{ pais = 'colombia'; pestana = 'order bump salmos'; filas = (Pest 'salmos' '6000' @('15000') '2026-11-01' '') }
CorreP $archivo (Planilla @($pSal) @())
$b = (Bumps 'colombia')
Afirma ($b.Count -eq 2 -and (@($b | ForEach-Object { $_.oferta }) -join ',') -eq 'Zapdata Sermones Biblicos,Salmos') 'una oferta nueva en la planilla se suma a las del archivo, sin tocar codigo'
$pMala = [ordered]@{ pais = 'colombia'; pestana = 'order bump sermones'; filas = (Pest 'zapdata sermones biblicos' '' @('15000') '2026-11-05' '') }
CorreP $archivo (Planilla @($pMala) @([ordered]@{ pais = 'mexico'; pestana = 'order bump algo'; error = 'tiempo agotado' }))
$b = (Bumps 'colombia')
Afirma ($b.Count -eq 1 -and $b[0].precio -eq 6000 -and -not $b[0].Contains('pestana')) 'una pestana mala se salta y queda el respaldo del archivo'
Afirma (($salida -join '|') -match "order bump 'Zapdata Sermones Biblicos' \(colombia\): se mide con el respaldo") 'el bump que se mide con el respaldo lo dice (pestana oculta, renombrada o mala)'
Afirma ((($salida -join '|') -match "pestana 'order bump sermones' \(colombia\) el precio del order bump") -and (($salida -join '|') -match "'order bump algo' \(mexico\) no se pudo bajar: tiempo agotado")) ('la pestana mala y la que no bajo avisan: ' + ($salida -join ' | '))
CorreP $null (Planilla @($pSal) @())
Afirma ((Bumps 'colombia').Count -eq 1 -and (Bumps 'colombia')[0].oferta -eq 'Salmos') 'sin data/order-bumps.json la planilla sola funciona'
CorreP $archivo '{"pestanas":[{"pais":"colombia",'
Afirma ((($salida -join '|') -match 'order-bumps-planilla.json: no se pudo leer') -and (Bumps 'colombia').Count -eq 1) 'un order-bumps-planilla.json roto avisa y sigue con el archivo'
# La pestana de ventas se resuelve por el ARCHIVO que la trae (con sus alias), como el resto del build: 'zapdata kine' -> col_zapdata_kine.csv -> la oferta que diga ese archivo.
$colTabs = @(@{ file = 'col_zapdata_kine.csv'; offer = 'Salmos' })
CorreP $null (Planilla @([ordered]@{ pais = 'colombia'; pestana = 'order bump kine'; filas = (Pest 'zapdata kine' '6000' @('15000') '2026-11-01' '') }) @())
Afirma ((Bumps 'colombia').Count -eq 1 -and (Bumps 'colombia')[0].oferta -eq 'Salmos') 'la pestana de ventas se traduce a la oferta del dash por su archivo (alias incluidos)'
$colTabs = $null
CorreP $null $null

# 8) Order bumps SIN MEDIR: los que tienen los flujos de ZapData (data/order-bumps-zapdata.json) contra los que el dash mide esta hora. El camino completo desde ZapData lo prueba
#    scripts/probar-bumps-sin-medir.mjs; aqui, el bloque del build y cada motivo.
$zapRuta = Join-Path $DataDir 'order-bumps-zapdata.json'
$ofRaiz = Join-Path $Raiz 'order-bumps-sin-medir.json'
function ZapBump([string]$oferta, [string]$pais, $precio, [string]$prod) {
    return [ordered]@{ venta = '00000000-0000-4000-8000-000000000009'; nombre = ('Venta ' + $oferta + ' ' + $pais); oferta = $oferta; pais = $pais.ToLowerInvariant(); paisNombre = $pais
                       etiqueta = ('Pago Pendiente ' + $oferta); precio = $precio; producto = $prod; bases = @(15000, 30000); viejo = $false }
}
function ZapDoc($bumps) { return ([ordered]@{ ok = $true; capturadoEn = '2026-11-11T21:00:00Z'; total = 5; leidas = 5; pendientes = 0; pausa = ''; bumps = @($bumps) } | ConvertTo-Json -Depth 5) }
[IO.File]::WriteAllText($zapRuta, (ZapDoc @((ZapBump 'Sermones Biblicos' 'Colombia' 6000 'De un pasaje'), (ZapBump 'Recetas' 'Colombia' 8000 'Recetario de Navidad'))), (New-Object Text.UTF8Encoding($false)))
CorreP $archivo $null
$at = @($salida | Where-Object { $_ -like 'ATENCION order bump sin medir*' })
Afirma ($at.Count -eq 1 -and $at[0] -match "Venta Recetas Colombia" -and $at[0] -match 'Recetario de Navidad' -and $at[0] -match '8\.000' -and $at[0] -match 'libro de colombia' -and $at[0] -match '15\.000 y 30\.000') ('el build avisa del bump de ZapData sin pestana, con flujo, producto, precio y precios base: ' + ($at -join ' | '))
Afirma (@($salida | Where-Object { $_ -match "Sermones Biblicos Colombia' \(6\.000\): lo mide" }).Count -eq 1) 'el bump que si se mide lo dice, sin aviso'
Afirma (@($salida | Where-Object { $_ -match '^Order bumps en ZapData: 2 en 5 .*1 sin medir' }).Count -eq 1) 'el resumen: 2 bumps, 1 sin medir'
$doc = $null; try { $doc = [IO.File]::ReadAllText($ofRaiz) | ConvertFrom-Json } catch { }
Afirma ($null -ne $doc -and $doc.estado -eq 'ok' -and @($doc.avisos).Count -eq 1 -and $doc.avisos[0].motivo -eq 'sin_pestana' -and $doc.avisos[0].precio -eq 8000 -and $doc.avisos[0].pais -eq 'colombia') 'y el archivo del dueno (order-bumps-sin-medir.json, junto a la carpeta data) trae ese aviso'
Afirma ([IO.File]::ReadAllText($ofRaiz) -match '"avisos":\s*\[') 'avisos va como lista aunque sea uno (la pagina lo recorre)'
# Lo que sale del build cuando Refresh-Headless lo llama con '| Out-Null' (asi corre en la corrida de GitHub): el aviso y el resumen tienen que seguir viendose.
$conOutNull = @(powershell -NoProfile -Command ("& { . '" + (Join-Path $PSScriptRoot '_order-bumps.ps1') + "'; `$z = Get-Content -Raw '" + $zapRuta + "' | ConvertFrom-Json; `$r = Compare-BumpsZapData `$z @() '2026-11-11'; foreach (`$l in `$r.lineas) { Write-Host `$l } } | Out-Null") 2>&1 | ForEach-Object { [string]$_ })
Afirma (@($conOutNull | Where-Object { $_ -like 'Order bumps en ZapData:*' }).Count -eq 1) "con '| Out-Null' (como lo llama Refresh-Headless) el resumen de ZapData sigue saliendo (Write-Host)"
Afirma ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'build-dashboard.ps1')) -match "Write-Host \('::warning title=Order bump sin medir::'") 'la anotacion amarilla de GitHub sale con Write-Host'
Remove-Item $zapRuta
CorreP $archivo $null
$doc = [IO.File]::ReadAllText($ofRaiz) | ConvertFrom-Json
Afirma ($doc.estado -eq 'sin_dato' -and @($doc.avisos).Count -eq 0 -and @($salida | Where-Object { $_ -like 'Order bumps en ZapData: no se revisaron*' }).Count -eq 1) 'sin el archivo de ZapData: lo dice en el log y no inventa avisos'

$m = New-Object System.Collections.Generic.List[object]
$m.Add([pscustomobject]@{ pais = 'colombia'; oferta = 'Zapdata Sermones Biblicos'; ofertaCfg = 'zapdata sermones biblicos'; pestana = 'order bump sermones'; precio = 6000; hasta = '' })
$m.Add([pscustomobject]@{ pais = 'colombia'; oferta = 'Kinesio Taping Pro'; ofertaCfg = 'zapdata kinesiotaping'; pestana = 'order bump kinesiotaping'; precio = 6000; hasta = '' })
$m.Add([pscustomobject]@{ pais = 'colombia'; oferta = 'Zapdata Loncheras'; ofertaCfg = 'zapdata loncheras'; pestana = 'order bump loncheras'; precio = 5000; hasta = '2026-11-01' })
function Cmp($bumps) { return Compare-BumpsZapData ((ZapDoc $bumps) | ConvertFrom-Json) $m '2026-11-11' }
$r = Cmp @((ZapBump 'Sermones Biblicos' 'Colombia' 7000 'De un pasaje'))
Afirma ($r.avisos.Count -eq 1 -and $r.avisos[0].motivo -eq 'precio_distinto' -and $r.lineas[0] -match '7\.000' -and $r.lineas[0] -match 'mide 6\.000') 'otro precio en el flujo que en la pestana: precio_distinto, con los dos precios'
$r = Cmp @((ZapBump 'Loncheras' 'Colombia' 5000 'Stickers'))
Afirma ($r.avisos.Count -eq 1 -and $r.avisos[0].motivo -eq 'cerrada' -and $r.lineas[0] -match 'Hasta 2026-11-01') "la pestana dice que se quito ('Hasta' pasado) y el flujo lo sigue ofreciendo: cerrada"
$r = Cmp @((ZapBump 'Kinesiotaping' 'Colombia' 6000 'PRO'))
Afirma ($r.avisos.Count -eq 0) 'se reconoce por la pestana de ventas aunque la oferta del dash se llame distinto (alias)'
$r = Cmp @((ZapBump 'Sermones Biblicos' 'Mexico' 40 'De un pasaje'))
Afirma ($r.avisos.Count -eq 1 -and $r.avisos[0].motivo -eq 'sin_pestana' -and $r.avisos[0].pais -eq 'mexico') 'la pestana de otro pais no cuenta'
$r = Cmp @((ZapBump 'Recetas' 'Colombia' $null 'Recetario'))
Afirma ($r.avisos.Count -eq 1 -and $null -eq $r.avisos[0].precio -and $r.lineas[0] -match 'no se pudo leer con seguridad' -and $r.lineas[0] -notmatch '\d') 'precio ilegible: avisa sin poner ninguna cifra'
$r = Compare-BumpsZapData ('{"ok":false,"motivo":"http_401","mensaje":"El dash no acepto la llave."}' | ConvertFrom-Json) $m '2026-11-11'
Afirma ($r.estado -eq 'sin_dato' -and $r.avisos.Count -eq 0 -and $r.lineas[0] -match 'http_401') 'si el dash no contesto: sin_dato, con el motivo, sin avisos'
Afirma ((ConvertTo-BumpOferta ('Pago Pendiente Sermones B' + $i_ + 'blicos')) -eq (ConvertTo-BumpOferta 'Zapdata Sermones Biblicos')) 'la etiqueta, la pestana y la oferta del dash se comparan sin tildes ni prefijos'
Afirma ((Format-BumpMonto 6000) -eq '6.000' -and (Format-BumpMonto 40) -eq '40' -and (Format-BumpMonto 2.5) -eq '2,50' -and (Format-BumpMonto 1234567) -eq '1.234.567') 'los montos del aviso: 6.000, 40, 2,50, 1.234.567'

# 7) La pagina del socio: su JSON se rearma sin orderBumps y tiene que escapar '<' igual que los demas (un nombre con </script> no puede cortar la pagina).
$lineaSocio = ($src -split "`n" | Where-Object { $_ -match 'socioJson = \(' }) -join ''
Afirma ($lineaSocio -match "Replace\('<', \(\[string\]\[char\]92 \+ 'u003c'\)\)") 'el JSON del socio escapa < como <'
$rx = [regex]::Match($lineaSocio, "Replace\('<', (\([^)]*\))\)")
$expr = if ($rx.Success) { [scriptblock]::Create($rx.Groups[1].Value) } else { { '' } }
Afirma (('</script>').Replace('<', (& $expr)) -eq ([string][char]92 + 'u003c/script>')) 'la expresion de reemplazo produce <'

Remove-Item $Raiz -Recurse -Force -ErrorAction SilentlyContinue
if ($fallos) { Write-Output ("FALLA: {0} comprobacion(es)" -f $fallos); exit 1 }
Write-Output 'PASA: todas las comprobaciones'
exit 0
