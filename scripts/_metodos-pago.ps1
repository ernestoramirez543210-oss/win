# Metodo de pago de una venta: del texto libre de la columna "METODO DE PAGO" de la hoja a UNA clave estable.
#
# Por que existe: esa columna la llena ZapData con lo que su IA lee del comprobante (pix_banco_origem) y, en las hojas viejas, una persona a mano. Trae de todo:
# "Nequi", "Recarga Nequi", "Transferencia (Banco Pichincha)", "PagoMovilBDV (Banco Mercantil)", "{{Metodo de pago}}" (la variable que nadie lleno), la pregunta del
# flujo copiada tal cual, numeros de cuenta y hasta nombres de personas. Con ~1.500 textos distintos en 8 paises no se puede sumar nada sin agruparlos antes.
# Lo usa build-dashboard.ps1 (Parse-VeTab) y lo prueba scripts/Probar-Metodos.ps1.
#
# Lo que sale es una clave en ASCII; la plantilla del dash la traduce al nombre que se ve (METODOS_PAGO en dashboard.template.html). El texto original NUNCA viaja
# al dash: puede traer numeros de cuenta y el nombre de la persona que recibio el pago (en Ecuador la IA copia "transferencia a <nombre> cuenta <numero>"), y para
# sumar no hace falta.
#
#   billeteras y apps  nequi bre_b yape plin mercado_pago uala naranja_x deuna pago_movil tpago paypal binance zelle billetera
#   forma de pagar     transferencia deposito efectivo contraentrega
#   bancos             bancolombia davivienda banco_bogota bbva provincial nu santander banorte hsbc azteca bcp banrural industrial pichincha guayaquil
#                      produbanco banesco mercantil bnc bdv banplus otro_banco
#   sin dato           sin   = la hoja no trae el metodo (vacio, placeholder, pregunta del flujo, numero suelto, "no especificado", solo el tipo de cuenta: "Ahorros")
#                      otro  = hay texto pero no se puede clasificar (nada se inventa)
# DaviPlata y Davivienda son la misma clave (davivienda): DaviPlata es la billetera de Davivienda y se usa desde su misma app (el dueno, 2026-10-14).
#
# ORDEN DE DECISION (el primero que acierte manda; dentro de cada paso gana lo que aparece primero en el texto):
#   0. Sin letras, con signos de pregunta o con una instruccion del flujo ("por favor indicame...")  ->  sin
#   1. Billetera o app (Nequi, Yape, Pago Movil...): es el medio en si, gana aunque el texto diga tambien "transferencia" o un banco ("Transferencia Nequi" = nequi)
#   2. Forma de pagar (deposito, transferencia, efectivo, contraentrega): "Transferencia (Banco Pichincha)" = transferencia; "Depositos en Banrural" = deposito
#   3. Banco con nombre propio: solo si el texto NO dice la forma ("Banrural" a secas = banrural)
#   4. Cualquier otro banco o cooperativa -> otro_banco
#   5. Palabras de "no hay dato" ("no especificado", "pendiente", "por confirmar") -> sin
#   6. Lo demas -> otro
# Un placeholder {{...}} se quita antes de leer el texto. Solo ASCII en este archivo: PowerShell 5.1 lee un .ps1 sin BOM como ANSI y un caracter fuera de ASCII
# lo corrompe; por eso el rango de tildes (U+0300 a U+036F) y la apertura de pregunta (U+00BF) se arman con [char] en vez de escribirse en el archivo.

function New-ReglaMetodo([string]$clave, [string]$patron) {
    [pscustomobject]@{
        Clave = $clave
        Re    = New-Object System.Text.RegularExpressions.Regex($patron, [System.Text.RegularExpressions.RegexOptions]::CultureInvariant)
    }
}

# Marcas de tilde (U+0300 a U+036F, lo que deja Normalize(FormD) separado de la letra) y apertura de pregunta (U+00BF), armadas con [char] a proposito.
$script:MP_Tildes   = New-Object System.Text.RegularExpressions.Regex(('[' + [char]0x0300 + '-' + [char]0x036f + ']'))
$script:MP_Pregunta = New-Object System.Text.RegularExpressions.Regex(('[?' + [char]0x00bf + ']'))

# Todos los patrones se aplican sobre el texto ya plano: minusculas, sin tildes y con cualquier signo convertido en un espacio ("Bre-B" -> "bre b").
$script:MP_Billeteras = @(
    (New-ReglaMetodo 'nequi'         'nequi|\bnek[iy]\b'),
    # DaviPlata es la billetera de Davivienda y se usa desde la misma app: una sola clave.
    (New-ReglaMetodo 'davivienda'    'davi ?plata'),
    (New-ReglaMetodo 'bre_b'         '\bbre ?b\b|\bllaves?\b'),
    (New-ReglaMetodo 'yape'          '\byape\b'),
    (New-ReglaMetodo 'plin'          '\bplin\b'),
    (New-ReglaMetodo 'mercado_pago'  'mercado ?pago'),
    (New-ReglaMetodo 'uala'          '\buala\b'),
    (New-ReglaMetodo 'naranja_x'     'naranja ?x\b|\bnx\b'),
    (New-ReglaMetodo 'deuna'         '\bdeuna\b|(^| )d!'),
    # Dinero Rapido es el nombre que le da BBVA Provincial a su pago movil entre bancos.
    (New-ReglaMetodo 'pago_movil'    'pago ?mo[vb]il|dinero rapido'),
    (New-ReglaMetodo 'tpago'         '\btpago\b'),
    (New-ReglaMetodo 'paypal'        '\bpaypal\b'),
    (New-ReglaMetodo 'binance'       '\bbinance\b'),
    (New-ReglaMetodo 'zelle'         '\bzelle\b'),
    (New-ReglaMetodo 'billetera'     'cuenta dni|personal pay|\bprex\b|brubank|\bstori\b|spin by oxxo|claro pay|skrill|western union|saldo peigo|monedero digital')
)
$script:MP_Formas = @(
    (New-ReglaMetodo 'deposito'      'deposit|consignacion|ventanilla'),
    (New-ReglaMetodo 'transferencia' 'transfer|trasfer|interbancar|\bspei\b|\bach\b|guateach|\bgiro\b'),
    (New-ReglaMetodo 'efectivo'      '\befectivo\b|\bcash\b'),
    (New-ReglaMetodo 'contraentrega' 'contra ?entrega|pago al recibir|contrareembolso')
)
$script:MP_Bancos = @(
    (New-ReglaMetodo 'bancolombia'   'bancolombia'),
    (New-ReglaMetodo 'davivienda'    'davivienda|\bdavienda\b|davimivenda|davimenda|davibank'),
    (New-ReglaMetodo 'banco_bogota'  'banco de bogota'),
    # "BBVA Provincial" es el banco de Venezuela, no el BBVA de Colombia o Mexico (por eso bbva no acepta "provincial" detras).
    (New-ReglaMetodo 'provincial'    '\bprovincial\b'),
    (New-ReglaMetodo 'bbva'          '\bbbva\b(?! provincial)'),
    (New-ReglaMetodo 'nu'            '\bnu ?u?bank\b|\bnu\b'),
    (New-ReglaMetodo 'santander'     'santander'),
    (New-ReglaMetodo 'banorte'       'banorte'),
    (New-ReglaMetodo 'hsbc'          'hsbc'),
    (New-ReglaMetodo 'azteca'        'azteca|guardadito'),
    (New-ReglaMetodo 'bcp'           '\bbcp\b'),
    (New-ReglaMetodo 'banrural'      'banrural|banco rural|caja rural|desarrollo rural'),
    (New-ReglaMetodo 'industrial'    '\bindustrial\b|\bbi\b|bimovil'),
    (New-ReglaMetodo 'pichincha'     'pichincha'),
    (New-ReglaMetodo 'guayaquil'     'guayaquil'),
    (New-ReglaMetodo 'produbanco'    'produbanco'),
    (New-ReglaMetodo 'banesco'       'banesco'),
    # "Mercantil Santa Cruz" es un banco de Bolivia, no el Mercantil de Venezuela.
    (New-ReglaMetodo 'mercantil'     'mercantil(?! santa cruz)'),
    (New-ReglaMetodo 'bnc'           '\bbnc\b'),
    (New-ReglaMetodo 'bdv'           'banco de venezuela|banco venezuela|\bbdv\b'),
    (New-ReglaMetodo 'banplus'       'banplus')
)
$script:MP_BancoGenerico = [regex]'\bbancos?\b|\bbanca\b|\bbank\b|cooperativa|mutualista|\bcaja\b|\bbam\b|\bbac\b|promerica|bantrab|cuscatlan|\bjep\b|santa cruz|bancaribe|bancamiga|venezolano de credito|scotiabank|colpatria|supervielle|\bbancor\b|\bbna\b|inbursa|crediservir'
$script:MP_SinDato       = [regex]('especific|identific|indic|menciona|visible|determin|desconoc|disponible|confirm|pendiente|definir|generic|invalid|valid|coincide|\bn a\b|^no se\b|' +
                                   '^(el |tu |mi |su )?metodo( de pago)?$|^comprobante( de pago)?( verificado)?$|^(ok|y|bs|soles|quetzales|bolivares|pesos|venezuela|guatemala|peru|whatsapp|agente|plata)$|' +
                                   # Solo el tipo de cuenta ("Ahorros", "Cuenta de ahorros *2375", "Cuenta Corriente"): la IA leyo el comprobante pero no dice con que se pago.
                                   '^(cuenta )?(de )?(ahorros?|corriente|monetaria)( [a-z]+)?( \d+)*$')

# El texto del metodo de UNA fila. En algunas pestanas (Conservas de Venezuela) las columnas POST ID y METODO DE PAGO vienen cambiadas: el metodo esta en POST ID y
# el ID del anuncio en METODO DE PAGO. Se detecta por la forma del contenido y no por el encabezado, igual que Parse-VeTab con el Post ID. Necesita las funciones
# Test-PostIdShape y Test-LinkShape de _ventas-comun.ps1 (se cargan antes).
function Get-TextoMetodoFila([string]$rawMetodo, [string]$rawPostId) {
    if (Test-PostIdShape $rawMetodo) {
        if ((Test-PostIdShape $rawPostId) -or (Test-LinkShape $rawPostId)) { return '' }
        return $rawPostId
    }
    return $rawMetodo
}

# La regla que acierta mas arriba en el texto; null si ninguna.
function Get-PrimeraRegla([string]$texto, $reglas) {
    $mejor = $null
    $pos = [int]::MaxValue
    foreach ($r in $reglas) {
        $m = $r.Re.Match($texto)
        if ($m.Success -and $m.Index -lt $pos) { $pos = $m.Index; $mejor = $r.Clave }
    }
    return $mejor
}

# La llave de la memoria es el texto tal cual (PowerShell compara las llaves sin distinguir mayusculas, y el resultado ya ignora las mayusculas).
$script:MP_Memoria = @{}
function Get-MetodoPago([string]$raw) {
    if ([string]::IsNullOrWhiteSpace($raw)) { return 'sin' }
    $k = $script:MP_Memoria[$raw]
    if (-not $k) { $k = Resolve-MetodoPago $raw; $script:MP_Memoria[$raw] = $k }
    return $k
}

function Resolve-MetodoPago([string]$raw) {
    $t = $script:MP_Tildes.Replace($raw.ToLowerInvariant().Normalize([Text.NormalizationForm]::FormD), '')
    # Las variables de ZapData que nadie lleno ({{Metodo de pago}}, {metodo_de_pago}) no son un dato.
    $t = $t -replace '\{+[^{}]*\}+', ' '
    # Texto plano: solo letras, numeros y el "!" de "d!" (Deuna); cualquier otro signo es un espacio.
    $u = ($t -replace '[^a-z0-9!]+', ' ').Trim()
    if ($u -notmatch '[a-z]') { return 'sin' }                       # un monto o una cuenta tecleados donde iba el metodo
    if ($script:MP_Pregunta.IsMatch($t)) { return 'sin' }                    # la pregunta del flujo copiada tal cual: el cliente no contesto
    if ($u -match 'por favor|\bindica(me|r|s)?\b|\bconfirma(me|s|r)?\b|\bindique\b') { return 'sin' }
    $k = Get-PrimeraRegla $u $script:MP_Billeteras; if ($k) { return $k }
    $k = Get-PrimeraRegla $u $script:MP_Formas;     if ($k) { return $k }
    $k = Get-PrimeraRegla $u $script:MP_Bancos;     if ($k) { return $k }
    if ($script:MP_BancoGenerico.IsMatch($u)) { return 'otro_banco' }
    if ($script:MP_SinDato.IsMatch($u)) { return 'sin' }
    return 'otro'
}

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
# Completar el metodo con lo que el LOG del webhook sabe del comprobante (pedido del dueno el 2026-10-14: "las ventas que dicen sin especificar... que algun agente o algo
# les asigne su banco", sin gastar tokens).
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
# La IA de ZapData que lee el comprobante deja en el payload del webhook (pestana 'zapdata log' de cada libro) dos datos: pix_banco_origem (de donde pago: es lo que la hoja
# copia a METODO DE PAGO) y pix_banco_destino (a que cuenta pago: Nequi, Bancolombia, DaviPlata...). Cuando no puede leer el origen la hoja queda en "No especificado", pero el
# destino casi siempre se lee. Colombia, 14-oct: el origen faltaba en 23 % de los comprobantes y del 86 % de esos el log SI trae el destino; cuando los dos se leen, coinciden el
# 82 %. Por eso, una venta de ZapData sin metodo toma el origen del log si dice algo y, si no, el DESTINO (queda marcada con pl = 1 para que el dash lo diga).
# Lo que NO se completa con esto: las ventas de ClicChat (no tienen log ni comprobante en ningun archivo) y las anteriores al log (Colombia: 4 de septiembre).
#
# Que registro del log es de que venta: las mismas reglas de scripts/reparar-ventas.cjs, para no tener dos criterios.
#   1) el clic (CTWA_CLID, unico por clic); 2) solo si la fila NO trae clic: telefono + pago + dia, en una clave con hash (el telefono no queda escrito en el archivo).
#   Si la fila trae clic y el log no lo conoce, no se prueba por telefono: podria ser otra venta de la misma persona. Dos registros que dicen metodos distintos = no se usa.
# El archivo que arma Fetch-ZapDataLog.ps1 (data/metodos-log.txt, una linea por registro: "pais c|t clave valor", valor = metodo|o u metodo|d, o ! si hay conflicto) es
# texto plano y no JSON a proposito: ConvertFrom-Json de PowerShell 5.1 se rinde con textos de mas de 2 MB y este archivo solo crece.
$script:MetLog = @{}        # pais -> @{ c = clic -> valor ; t = hash -> valor }

# 'yyyy-MM-dd' del CUANDO del log ("4/09/2026 2:19:17", "9/22/2026 2:21:34 PM"): dia primero, y si el mes pasa de 12 se cambian (igual que diaDe de reparar-ventas.cjs).
function Get-DiaDeLog([string]$cuando) {
    $m = [regex]::Match([string]$cuando, '^\s*(\d{1,2})/(\d{1,2})/(\d{4})')
    if (-not $m.Success) { return '' }
    $d = [int]$m.Groups[1].Value; $mes = [int]$m.Groups[2].Value
    if ($mes -gt 12 -and $d -le 12) { $t = $d; $d = $mes; $mes = $t }
    if ($mes -lt 1 -or $mes -gt 12 -or $d -lt 1 -or $d -gt 31) { return '' }
    return ('{0}-{1:D2}-{2:D2}' -f $m.Groups[3].Value, $mes, $d)
}

# El telefono como identidad comparable: solo digitos sin ceros a la izquierda, o el identificador "@lid" de WhatsApp tal cual. Las hojas exportadas a xlsx traen el numero en
# notacion cientifica (5.73E+11): se pasa a digitos por valor, igual que Get-HuellaFila.
function Get-IdentidadTel([string]$raw) {
    $t = ([string]$raw).Trim()
    if (-not $t) { return '' }
    if ($t.IndexOf('@') -ge 0) { return $t.ToLowerInvariant() }
    $n = 0.0
    if ($t -match '[eE]' -and [double]::TryParse($t, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$n)) {
        return $n.ToString('F0', [Globalization.CultureInfo]::InvariantCulture)
    }
    return (($t -replace '[^\d]', '') -replace '^0+', '')
}

# La clave de una venta sin clic: telefono + pago (redondeado a entero, igual de los dos lados) + dia, en hash para no dejar el telefono escrito.
function Get-ClaveVentaLog([string]$tel, $monto, [string]$dia) {
    $s = '{0}|{1}|{2}' -f $tel, [long][math]::Round([double]$monto), $dia
    $sha = [System.Security.Cryptography.SHA1]::Create()
    try { $h = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($s)) } finally { $sha.Dispose() }
    return ((($h | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 20))
}

# Que metodo sale de lo que leyo la IA: el origen si dice un metodo y, si no, el destino. Solo cuenta un metodo de verdad: "sin" y "otro" no completan nada.
function Get-MetodoDeLog([string]$origen, [string]$destino) {
    $k = Get-MetodoPago $origen
    if ($k -ne 'sin' -and $k -ne 'otro') { return @{ pm = $k; destino = $false } }
    $k = Get-MetodoPago $destino
    if ($k -ne 'sin' -and $k -ne 'otro') { return @{ pm = $k; destino = $true } }
    return $null
}

# Un texto del payload tal como lo guardo el webhook: JSON dentro de JSON, asi que puede traer escapes de unicode y de comillas. Si no se puede leer, se deja como esta.
function Get-CampoDeLog([string]$payload, [string]$campo) {
    $m = [regex]::Match($payload, '"' + $campo + '"\s*:\s*"((?:[^"\\]|\\.)*)"')
    if (-not $m.Success) { return '' }
    try { return [regex]::Unescape($m.Groups[1].Value) } catch { return $m.Groups[1].Value }
}

# Guarda en `$valor` (un mapa) lo que dice un registro, juntando los que comparten llave: mismo metodo = se queda (el de origen manda sobre el de destino); metodos distintos = "!".
function Add-ValorMetodo($mapa, [string]$llave, [string]$nuevo) {
    if (-not $mapa.ContainsKey($llave)) { $mapa[$llave] = $nuevo; return }
    $a = [string]$mapa[$llave]
    if ($a -eq '!') { return }
    $pa = $a.Split('|'); $pn = $nuevo.Split('|')
    if ($pa[0] -ne $pn[0]) { $mapa[$llave] = '!'; return }
    if ($pa[1] -eq 'd' -and $pn[1] -eq 'o') { $mapa[$llave] = $nuevo }
}

# Para Fetch-ZapDataLog.ps1: suma UNA fila del log (su CUANDO y su PAYLOAD) a los mapas de un pais: @{ c = @{}; t = @{} }. Sin metodo que sacar de ella, no hace nada.
function Add-MetodoDeLog($mapa, [string]$cuando, [string]$payload) {
    $r = Get-MetodoDeLog (Get-CampoDeLog $payload 'pix_banco_origem') (Get-CampoDeLog $payload 'pix_banco_destino')
    if (-not $r) { return }
    $valor = $r.pm + '|' + $(if ($r.destino) { 'd' } else { 'o' })
    $ctwa = [regex]::Match($payload, '"ctwa_clid"\s*:\s*"([^"]+)"')
    if ($ctwa.Success) { Add-ValorMetodo $mapa.c $ctwa.Groups[1].Value $valor }
    $tel = Get-IdentidadTel ([regex]::Match($payload, '"phone"\s*:\s*"([^"]*)"').Groups[1].Value)
    $dia = Get-DiaDeLog $cuando
    $ev = [regex]::Match($payload, '"event_value"\s*:\s*"?([0-9.,]+)')
    $monto = if ($ev.Success) { Convert-Amount $ev.Groups[1].Value } else { $null }
    if ($tel -and $dia -and $null -ne $monto -and $monto -gt 0) { Add-ValorMetodo $mapa.t (Get-ClaveVentaLog $tel $monto $dia) $valor }
}

# Para build-dashboard.ps1: lee data/metodos-log.txt a $script:MetLog. Sin el archivo (o si no se puede leer) no se completa nada: el dash sigue igual que antes.
function Import-MetodosLog([string]$ruta) {
    $script:MetLog = @{}
    if (-not (Test-Path $ruta)) { return }
    try {
        foreach ($l in [IO.File]::ReadAllLines($ruta, [Text.Encoding]::UTF8)) {
            if (-not $l -or $l.StartsWith('#')) { continue }
            $p = $l.Split(' ')
            if ($p.Count -ne 4) { continue }
            if (-not $script:MetLog.ContainsKey($p[0])) { $script:MetLog[$p[0]] = @{ c = @{}; t = @{} } }
            if ($p[1] -eq 'c') { $script:MetLog[$p[0]].c[$p[2]] = $p[3] } elseif ($p[1] -eq 't') { $script:MetLog[$p[0]].t[$p[2]] = $p[3] }
        }
    } catch { Write-Warning ('metodos-log.txt ilegible, se ignora: ' + $_.Exception.Message); $script:MetLog = @{} }
}

# Para Parse-VeTab: el metodo que el log da a una venta de ZapData, o $null. @{ pm = clave; destino = $true|$false }. `$ctwa` y `$tel` son los de la fila de la hoja.
function Resolve-MetodoDeLog([string]$pais, [string]$ctwa, [string]$tel, $monto, [string]$dia) {
    $m = $script:MetLog[$pais]
    if (-not $m) { return $null }
    $v = $null
    if ($ctwa) { if ($m.c.ContainsKey($ctwa)) { $v = $m.c[$ctwa] } }
    elseif ($tel -and $null -ne $monto -and $dia) {
        $k = Get-ClaveVentaLog $tel $monto $dia
        if ($m.t.ContainsKey($k)) { $v = $m.t[$k] }
    }
    if (-not $v -or $v -eq '!') { return $null }
    $p = ([string]$v).Split('|')
    return @{ pm = $p[0]; destino = ($p[1] -eq 'd') }
}
