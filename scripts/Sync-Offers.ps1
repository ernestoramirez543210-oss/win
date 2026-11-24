# Autocompleta data\offers.json desde snapshot.json (ads reales + ventas reales).
# La oferta se deduce del nombre de campana: "CONSERVAS | 12 JUNIO | IN | 105.2" -> "CONSERVAS".
# Etapas automaticas: anuncios / analisis / escalando / pausada.
# Las etapas manuales (idea, contenido, entregable, flujo-wa) se preservan si ya existian.
# Determinista, sin LLM, ~2KB de salida. PS 5.1 safe.
#   & scripts\Sync-Offers.ps1
param(
    [string]$DataDir     = (Join-Path (Split-Path $PSScriptRoot) 'data'),
    [double]$RoasEscalar = 1.5,   # ROAS a partir del cual la oferta se considera ganadora
    [int]   $MinDiasAnalisis = 3  # antes de esto sigue en "anuncios" (muy nueva para juzgar)
)
$ErrorActionPreference = 'Stop'
$snapPath   = Join-Path $DataDir 'snapshot.json'
$offersPath = Join-Path $DataDir 'offers.json'
if (-not (Test-Path $snapPath)) { Write-Output "ERROR: falta snapshot.json. Corre Refresh-Headless.ps1 -SkipDeploy"; exit 1 }

$s   = Get-Content $snapPath   -Raw -Encoding UTF8 | ConvertFrom-Json
$cat = Get-Content $offersPath -Raw -Encoding UTF8 | ConvertFrom-Json

# Que activos existen ya, deducido de los archivos en disco.
#
# Antes esto eran casillas que alguien tenia que marcar a mano, asi que nunca reflejaban
# la realidad. Ahora la pregunta "ya existe el entregable de esta oferta?" la responde el
# disco: si el archivo esta, la etapa esta hecha.
function Slug([string]$s) {
    $s = $s.ToLower()
    foreach ($par in @(@('[áàä]','a'),@('[éèë]','e'),@('[íìï]','i'),@('[óòö]','o'),@('[úùü]','u'),@('ñ','n'))) {
        $s = $s -replace $par[0], $par[1]
    }
    return ($s -replace '[^a-z0-9]+','-').Trim('-')
}
# Indexa por slug lo que haya en cada carpeta de la fabrica.
$activos = @{}
foreach ($def in @(
    @{ k='contenido';  dir='contenido';   ext='.json' },
    @{ k='entregable'; dir='entregables'; ext='.html' },
    @{ k='flujoWa';    dir='clicchat';    ext='.json' }
)) {
    $d = Join-Path $DataDir $def.dir
    if (-not (Test-Path $d)) { continue }
    foreach ($f in (Get-ChildItem $d -Filter "*$($def.ext)" -File)) {
        $sl = Slug ([IO.Path]::GetFileNameWithoutExtension($f.Name) -replace '^flujos-','')
        if (-not $activos.ContainsKey($sl)) { $activos[$sl] = @{} }
        $activos[$sl][$def.k] = $f.Name
    }
}
function ActivosDe([string]$nombre) {
    $sl = Slug $nombre
    $r = [ordered]@{ contenido=$false; entregable=$false; flujoWa=$false; archivos=@() }
    foreach ($k in $activos.Keys) {
        # el archivo puede llamarse mas largo que la oferta ("megapack-de-recetas-...")
        if ($k -eq $sl -or $k -like "*$sl*" -or $sl -like "*$k*") {
            foreach ($campo in 'contenido','entregable','flujoWa') {
                if ($activos[$k].ContainsKey($campo)) { $r[$campo] = $true; $r['archivos'] += $activos[$k][$campo] }
            }
        }
    }
    return [pscustomobject]$r
}

# Videos que ya existen en Drive, por oferta.
#
# Cubre el hueco entre "el editor subio el video" y "el trafficker lo monto": ese trabajo
# existe pero no aparece en Facebook, asi que la etapa 'videos' quedaba siempre vacia.
# La llave es el NUMERO del archivo ("recetas antiflamatorias 104 .mp4" -> 104), el mismo
# que lleva el ad en Facebook, asi que cruzar numeros dice cual video ya esta montado.
$drivePath = Join-Path $DataDir 'drive.json'
$carpetas  = @()
if (Test-Path $drivePath) {
    try { $carpetas = @((Get-Content $drivePath -Raw -Encoding UTF8 | ConvertFrom-Json).carpetas) } catch { $carpetas = @() }
}
function VideosDe([string]$nombre, $numerosAds) {
    $sl = Slug $nombre
    $nums = @(); $cars = @()
    foreach ($c in $carpetas) {
        $cs = Slug ([string]$c.carpeta)
        if (-not ($cs -eq $sl -or $cs -like "*$sl*" -or $sl -like "*$cs*")) { continue }
        $cars += [string]$c.carpeta
        foreach ($v in @($c.videos)) { if ($v.numero) { $nums += [string]$v.numero } }
    }
    $nums = @($nums | Sort-Object -Unique)
    # Un video cuenta como montado si SU numero es el numero de algun ad. Comparar por
    # substring daria falsos positivos: en Venezuela los ads se llaman "AD1".."AD26", y
    # el video 1 "aparece" dentro de AD1, AD10, AD12...
    $adNums = @()
    foreach ($a in $numerosAds) {
        foreach ($m in [regex]::Matches([string]$a, '\d+(?:\.\d+)?')) { $adNums += $m.Value }
    }
    $adNums = @($adNums | Sort-Object -Unique)
    $mont   = @($nums | Where-Object { $adNums -contains $_ })
    return [pscustomobject]@{
        total      = $nums.Count
        montados   = $mont.Count
        pendientes = @($nums | Where-Object { $mont -notcontains $_ })
        carpetas   = @($cars)
    }
}

# Notas escritas a mano: eso si lo conserva entre corridas.
$notas = @{}
foreach ($o in $cat.offers) {
    if ($o.manual -and $o.manual.notas) { $notas[[string]$o.id] = [string]$o.manual.notas }
}

# Cada pais nombra sus campanas distinto. Hay 3 convenciones vivas:
#   MX/CO/PE : "AIRFRYER | 18 JULIO | IN | 101"            -> 1er segmento
#   GT       : "101 | Arteterapia | 23-JUN"                -> 1er segmento NO numerico
#   VE       : "[ CBO ] [ $6 ] [ DINAMICAS AD 101. 1 ] fecha" -> corchete con texto
# Regla unica: descartar segmentos que sean numeros, fechas o ruido, y quedarse con el primero real.
# El OBJETIVO de la campana no es la oferta. 'VENTAS' ya estaba; faltaban los demas, y eso
# bastaba para partir a Mexico en dos: sus campanas se llaman "<fecha> | VENTAS | DINAMICAS |
# 101" y "<fecha> | INTERACCION | DINAMICAS | 101". Las primeras saltaban 'VENTAS' y caian
# en DINAMICAS; las segundas se quedaban con 'INTERACCION' como si fuera el nombre de la
# Dos campanas de la MISMA oferta, contadas en sitios distintos por una palabra.
$NOISE = @('IN','VENTAS','VNTAS','CBO','ABO','COPIA','TESTEO','ADV','ADVANTAGE',
           'INTERACCION','INTERACCIONES','MENSAJES','TRAFICO','ALCANCE','CONVERSIONES',
           # 2026-10-13: el OBJETIVO y el CANAL tampoco son la oferta. 'INT - COM', 'VEN - COM', 'INT > COMPRAS' y 'API | LIMPIEZA FACIAL' se leian como ofertas
           'INT','COM','VEN','VENTA','COMPRA','COMPRAS','CONV','MSG','MENSAJE','INTER','WSP','WPP','WHATSAPP','API','CLICCHAT','ONE','TEST','ZAPD','PUBLICACION')
$MESES = 'ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC'

function Is-Noise([string]$t) {
    if (-not $t) { return $true }
    if ($t -match '^[\d\.\,\-\s]+$')             { return $true }   # solo numeros: 101, 105.2, 10
    if ($t -match '^\$')                          { return $true }   # presupuesto: $6
    if ($t -match "\d\s*[-\s]*($MESES)")          { return $true }   # fecha: 18 JULIO, 23-JUN
    if ($t -match '^\d{1,2}[\/\-]\d{1,2}[\/\-]')  { return $true }   # fecha: 14/07/2026
    # El CANAL no es la oferta. Las campanas de ZapData empiezan por su linea de WhatsApp:
    # "ZAP WPP #3 | DINAMICAS | 16 SEP | ..." -tambien "ZAPDATA WPP #2", "ZAP" o "ZAPDATA"
    # a secas-. Sin esto ese primer segmento se leia como nombre de oferta ("ZAP WPP"), y
    # todo el gasto de ZapData sin ventas propias caia en (SIN ATRIBUIR) en vez de en
    # "ZAPDATA DINAMICAS". El canal se sigue detectando aparte, en OfferKey.
    if ($t -match '^ZAP(DATA)?(\s+WPP)?(\s*#?\s*\d+)?$') { return $true }
    if ($NOISE -contains $t.ToUpper())            { return $true }
    return $false
}

function OfferKey([string]$campaign) {
    if (-not $campaign) { return "(SIN CAMPANA)" }
    # "ZAP" es como el equipo abrevia ZapData en el nombre de campana ("ZAP DINAMICAS
    # MUJER", "[ ZAP ] [ MUNDIAL AD 12 ]"). Sin esto la palabra se trataba como cualquier
    # otro segmento -o, peor, como ruido pegado que se quita y se olvida- y el gasto de
    # ZapData caia dentro de la MISMA oferta que ClicChat: "Dinamicas Mujer" mostraba gasto
    # sin ventas propias porque una parte de esas ventas estaba en la pestana de ZapData,
    # separada. El marcador se busca en la campana ENTERA, no en el segmento que gane, para
    # que de igual si "ZAP" viene pegado al nombre, en su propio corchete o al final.
    # Salvo que el nombre diga CLICCHAT: el equipo duplica campanas de ZapData para correrlas por ClicChat y deja el "ZAP #n" de la copia ("ZAP #2 | DINAMICAS | 20 OCT |
    # ... | CLICCHAT"). Lo escrito a proposito manda: esa campana es de ClicChat y su gasto va a la oferta de ClicChat. Misma regla que esCampanaZap del dashboard.
    $esZap = ($campaign -match '\bZAP(D|DATA)?\b') -and ($campaign -notmatch '(?i)\b(?:CLICC?\s*-?\s*CHAT|CLICHAT)\b')
    $parts = @()
    if ($campaign -match '\[') {
        # Venezuela: partir por corchetes
        foreach ($m in [regex]::Matches($campaign, '\[([^\]]*)\]')) { $parts += $m.Groups[1].Value }
    } else {
        $parts = $campaign -split '\|'
    }
    foreach ($p in $parts) {
        $t = ($p -replace '\s+',' ').Trim()
        # "DINAMICAS AD 101. 1" -> "DINAMICAS"  |  "ARTETERAPIA - Copia" -> "ARTETERAPIA"
        $t = ($t -replace '\s*AD\s*[\d\.\s]+$','') -replace '\s*-\s*Copia\s*$',''
        $t = $t.Trim()
        # PRIMERO se juzga el segmento ENTERO. Es lo unico que reconoce una fecha: "27 AGO"
        # es ruido como conjunto, pero si se le quita el 27 por ser numero, queda "AGO" — que
        # ya no lleva digito delante, deja de parecer fecha, y se convierte en el nombre de
        # la oferta. Ese fue el efecto: las campanas de Mexico pasaron a llamarse "AGO" y 139
        # dolares se soltaron de Dinamicas. El orden no es un detalle de estilo.
        if (Is-Noise $t) { continue }
        # Ya sabiendo que el segmento dice algo, se le quita el ruido PEGADO. Ecuador nombra
        # sus campanas "CBO JABONES | INTER. WSP MC | ...": 'CBO' solo es ruido, pero
        # \p{L} y no A-Za-z: con el rango ASCII, la tilde de "DINÁMICAS" cuenta como
        # separador y la palabra se parte en "DIN" y "MICAS". Guatemala escribe sus campanas
        # "101.3 | Dinámicas | 11-JUL" y la oferta pasaba a llamarse "DIN MICAS", que no
        # cuadra con ninguna pestana. Las clases Unicode se escriben en ASCII, asi que esto
        # no depende de como se lea este archivo.
        $tk = @($t -split '[^\p{L}\p{N}]+' | Where-Object { $_ })
        while ($tk.Count -gt 1 -and (Is-Noise $tk[0]))              { $tk = @($tk[1..($tk.Count - 1)]) }
        while ($tk.Count -gt 1 -and (Is-Noise $tk[$tk.Count - 1]))  { $tk = @($tk[0..($tk.Count - 2)]) }
        if ($tk.Count) { $t = ($tk -join ' ') }
        if (-not (Is-Noise $t)) {
            $t = $t.ToUpper()
            # Prefijo ZAPDATA para que AEtiquetaHoja la busque contra la pestana de ZapData
            # y no contra la del mismo producto en ClicChat, que es justo lo que canonizan
            # igual -"DINAMICAS MUJER" es substring de "ZAPDATA DINAMICAS MUJER"- si no se
            # avisa aqui que son canales distintos.
            if ($esZap -and $t -notmatch '^ZAPDATA\b') { $t = 'ZAPDATA ' + $t }
            return $t
        }
    }
    return "(SIN CAMPANA)"
}

$out = @()
$sospechosas = @()
# Los paises salen del snapshot, no de una lista escrita aqui. La lista fija se quedo sin
# torre y no llegaban a offers.json, asi que el cerebro, el radar y el analisis de
# expansion trabajaban como si Ecuador no vendiera nada.
# Una entrada de oferta-pais en blanco. Estaba escrita cuatro veces con la misma lista de
# campos, y agregar uno significaba acordarse de los cuatro sitios; la serie diaria se
# habria quedado vacia justo en la bolsa de (SIN ATRIBUIR).
function NuevaEntrada {
    # 'dia' es fecha -> @{ g = gastoUsd; v = ventasUsd; n = numero de ventas }: lo que permite que la
    # torre filtre por rango sin volver a bajar nada. Sin esto solo existe el acumulado de toda la
    # vida de la oferta, y una oferta muerta hace un mes sigue luciendo igual de viva.
    [pscustomobject]@{ ads=0; activos=0; spend=0.0; sLoc=0.0; sUsd=0.0; nums=@(); fechas=@(); dia=@{} }
}
# La tasa que regia el DIA de una venta, igual que fxEn() del dashboard. Solo el bolivar tiene
# historial de tasas; para el resto -y para fechas anteriores a la primera anotacion- sirve la
# de hoy. Antes esta pagina convertia TODO el historial con la tasa de hoy y el dashboard con la
# de cada dia, asi que las ventas de Venezuela salian ~14% mas bajas aqui que alla y el ROAS
# global no coincidia entre las dos paginas.
$fxHist = @{}
if ($s.fx.historia) {
    foreach ($m in $s.fx.historia.PSObject.Properties) {
        $fxHist[$m.Name] = @($m.Value.PSObject.Properties | Sort-Object Name | ForEach-Object { [pscustomobject]@{ f = [string]$_.Name; v = [double]$_.Value } })
    }
}
$fxCache = @{}
function FxEn([string]$moneda, [string]$fecha, [double]$hoy) {
    if (-not $fxHist.ContainsKey($moneda) -or -not $fecha) { return $hoy }
    $k = "$moneda|$fecha"
    if ($fxCache.ContainsKey($k)) { return $fxCache[$k] }
    $tasa = $null
    foreach ($e in $fxHist[$moneda]) { if ([string]::CompareOrdinal($e.f, $fecha) -le 0) { $tasa = $e.v } else { break } }
    $r = if ($null -ne $tasa) { $tasa } else { $hoy }
    $fxCache[$k] = $r
    return $r
}
# La serie tal como viaja: fecha -> @(gastoUsd, ventasUsd, nVentas), ordenada, y sin los dias
# en blanco. Las ventas del dia ya vienen en USD, cada una convertida con la tasa de SU dia.
# nVentas (2026-11-04) es cuantas ventas de la hoja suman esos dolares: /api/radar-prueba saca
# de ahi el costo por venta de una prueba. Va TERCERO para que quien lee [0] y [1] siga igual.
function ConvertirSerie([hashtable]$dia, [double]$fx) {
    $out = [ordered]@{}
    foreach ($f in ($dia.Keys | Sort-Object)) {
        $g = [Math]::Round($dia[$f].g, 2)
        $v = [Math]::Round($dia[$f].v, 2)
        $n = [int]$dia[$f].n
        if ($g -eq 0 -and $v -eq 0 -and $n -eq 0) { continue }
        $out[$f] = @($g, $v, $n)
    }
    return $out
}
# Suma un dia a la serie de una entrada, sin pisar lo que ya haya del otro concepto.
function SumaDia([hashtable]$dia, [string]$fecha, [double]$gasto, [double]$ventaLoc, [int]$nVentas = 0) {
    if (-not $fecha) { return }
    if (-not $dia.ContainsKey($fecha)) { $dia[$fecha] = @{ g = 0.0; v = 0.0; n = 0 } }
    $dia[$fecha].g += $gasto
    $dia[$fecha].v += $ventaLoc
    $dia[$fecha].n += $nVentas
}

foreach ($cn in $s.countries.PSObject.Properties.Name) {
    $c = $s.countries.$cn; if (-not $c) { continue }
    $fx = [double]$s.fx.($c.currency)

    # Guarda anti-basura: hay filas con telefonos/cedulas tecleados en el monto
    # no se borran: distorsionan el ROAS por varios ordenes de magnitud.
    $amts = @($c.orders | ForEach-Object { [double]$_.amount } | Where-Object { $_ -gt 0 } | Sort-Object)
    $mediana = if ($amts.Count -gt 0) { $amts[[int]($amts.Count/2)] } else { 0 }
    $techo   = if ($mediana -gt 0) { $mediana * 500 } else { [double]::MaxValue }

    $spendKeys = @($c.spendByAdId.PSObject.Properties.Name)

    # La oferta la manda la HOJA, no el nombre de la campaña.
    #
    # Antes se agrupaba solo por OfferKey(campaña). Venezuela nombra sus campañas
    # "[ CBO ] [ $6 ] [ MUNDIAL AD 12 ]" y de ahi sale la oferta sola, pero Guatemala las
    # nombra "101.5 | figuras | 7-JUN": la torre listaba FIGURAS, VNTAS y PUBLICACION: ""
    # ninguna campaña lo dice en el nombre. Cada venta ya trae su pestaña de origen y su
    # Post ID, asi que ese par es el vinculo bueno: anuncio -> oferta de la hoja.
    # El nombre de campaña queda de respaldo para los anuncios que aun no han vendido.
    $offerByAd = @{}
    foreach ($o in $c.orders) {
        $k = [string]$o.postId
        if (-not $k -or -not $o.offer) { continue }
        $offerByAd[$k] = ([string]$o.offer).ToUpper()
    }

    # Las etiquetas de la hoja son la lista buena de ofertas de este pais.
    $etiquetas = @($c.orders | ForEach-Object { $_.offer } | Where-Object { $_ } |
                   ForEach-Object { ([string]$_).ToUpper() } | Sort-Object -Unique)

    # La hoja y Facebook escriben la misma oferta distinto: "Airfryer" vs "AIR FRYER",
    # "Cartas de Pablo" vs "CARTAS PARA PABLO", y la misma palabra con y sin tilde. Sin
    # unirlas, la oferta sale DOS veces -las ventas en una, el gasto en la otra- y el ROAS
    # de ambas mitades queda mal. Se comparan sin tildes, sin espacios y sin palabras
    # vacias, y vale que una sea prefijo de la otra ("COSMETICA" -> "COSMETICA NATURAL").
    #
    # El rango de tildes va como escape \u0300-\u036f, NO como caracteres literales:
    # lee este archivo sin BOM como ANSI y un literal asi llega roto al motor de regex.
    function Canon([string]$t) {
        $x = [Text.NormalizationForm]::FormD
        $n = ([string]$t).ToUpper().Normalize($x) -replace '[\u0300-\u036f]',''
        foreach ($w in 'DE','DEL','PARA','LA','EL','LOS','LAS','Y') { $n = $n -replace ('\b' + $w + '\b'), '' }
        return ($n -replace '[^A-Z0-9]','')
    }
    $canonHoja = @{}
    foreach ($e in $etiquetas) {
        $ck = Canon $e
        if ($ck.Length -ge 4 -and -not $canonHoja.ContainsKey($ck)) { $canonHoja[$ck] = $e }
    }
    # La hoja y la campana llaman distinto a la MISMA oferta, y cada caso costo dinero
    # perdido en la bolsa de (SIN ATRIBUIR) hasta que alguien lo miro a mano:
    #
    #   FIGURAS / LAMINAS -> asi le dicen en Guatemala al album del Mundial. Ya estaba
    #     resuelto en el tablero (OFFER_KEYWORDS) pero no aqui, asi que las dos mitades del
    #     sistema atribuian distinto sobre los mismos datos.
    #
    # No es una lista de ofertas: es una lista de como se le dice a lo mismo. Si crece mucho,
    # la respuesta es arreglar los nombres en origen, no seguir agregando aqui.
    $ALIAS = @{ 'FIGURAS' = 'MUNDIAL'; 'LAMINAS' = 'MUNDIAL' }

    # Plural y singular son la misma oferta. Colombia gastaba en campanas "LONCHERAS" y su
    # pestana se llama "LONCHERA DIVERTIDA": el prefijo fallaba por una S -LONCHERAS contra
    function SinPlural([string]$x) {
        if ($x.Length -gt 5 -and $x.EndsWith('S')) { return $x.Substring(0, $x.Length - 1) }
        return $x
    }
    function AEtiquetaHoja([string]$clave) {
        $ck = Canon $clave
        if ($ck.Length -lt 4) { return $clave }
        if ($canonHoja.ContainsKey($ck)) { return $canonHoja[$ck] }
        # Un alias vale como si la campana hubiera dicho el nombre de la hoja.
        foreach ($a in $ALIAS.Keys) {
            # Contains y no igualdad: la campana de Colombia dice "RECETAS ANTIINFLAMATORIAS"
            # -la oferta con una palabra delante-, asi que exigir el nombre exacto dejaba el
            # alias sin usar justo en el caso para el que se escribio.
            $ca = Canon $a
            if ($ck -eq $ca -or $ck.Contains($ca)) {
                $destino = Canon $ALIAS[$a]
                if ($canonHoja.ContainsKey($destino)) { return $canonHoja[$destino] }
                foreach ($k in $canonHoja.Keys) {
                    if ($k.StartsWith($destino) -or $destino.StartsWith($k)) { return $canonHoja[$k] }
                }
            }
        }
        $cs = SinPlural $ck
        foreach ($k in $canonHoja.Keys) {
            $ks = SinPlural $k
            if ($ks.Length -ge 5 -and $cs.Length -ge 5 -and ($ks.StartsWith($cs) -or $cs.StartsWith($ks))) {
                return $canonHoja[$k]
            }
        }
        # La oferta puede venir CON UNA PALABRA DELANTE, no solo al principio del segmento.
        # Colombia gasta en "RECETAS ANTIINFLAMATORIAS | 6 AGO | VENTAS | 101" y su pestana se
        #
        # Se exige que el nombre de la HOJA tenga 8 letras o mas antes de aceptar que este
        # contenido: con nombres cortos -"DANIEL", "MOLDES"- cualquier campana que los lleve
        # dentro se los llevaria, y una atribucion equivocada es peor que ninguna.
        foreach ($k in $canonHoja.Keys) {
            if ($k.Length -ge 8 -and $cs.Contains((SinPlural $k))) { return $canonHoja[$k] }
        }
        return $clave
    }

    # LAS VENTAS LAS MANDA LA HOJA, UNA POR UNA. Los anuncios solo aportan gasto.
    #
    # Antes la venta se sumaba recorriendo anuncios: solo contaba si su Post ID coincidia
    # con un anuncio de las cuentas conectadas. Toda venta cuyo Post ID no cruzara -anuncio
    # de una cuenta que no esta conectada, anuncio borrado, publicacion organica- se caia
    # mismo agujero estaba abierto en los seis paises. Una venta de la hoja es una venta:
    # se cuenta por su pestaña, cruce o no cruce con Facebook.
    $ventasPorOferta = @{}
    $ventasUsdPorOferta = @{}
    $ventasPorOfertaDia = @{}
    $nVentasPorOfertaDia = @{}
    foreach ($o in $c.orders) {
        if (-not $o.offer) { continue }
        $amt = [double]$o.amount
        if ($amt -le 0) { continue }
        if ($amt -gt $techo) {
            $script:sospechosas += [pscustomobject]@{ pais=$cn; fecha=$o.date; monto=$amt; adId=[string]$o.postId; moneda=$c.currency }
            continue
        }
        $k = AEtiquetaHoja (([string]$o.offer).ToUpper())
        if (-not $ventasPorOferta.ContainsKey($k)) { $ventasPorOferta[$k] = 0.0 }
        $ventasPorOferta[$k] += $amt
        # Los dolares de ESTA venta, con la tasa de SU dia (ver FxEn).
        $usdVenta = if ($fx -gt 0) { $amt / (FxEn ([string]$c.currency) ([string]$o.date) $fx) } else { 0.0 }
        if (-not $ventasUsdPorOferta.ContainsKey($k)) { $ventasUsdPorOferta[$k] = 0.0 }
        $ventasUsdPorOferta[$k] += $usdVenta
        # La misma venta, ademas, anotada en su dia: es la mitad de ventas de la serie.
        $f = [string]$o.date
        if ($f) {
            if (-not $ventasPorOfertaDia.ContainsKey($k)) { $ventasPorOfertaDia[$k] = @{} }
            if (-not $ventasPorOfertaDia[$k].ContainsKey($f)) { $ventasPorOfertaDia[$k][$f] = 0.0 }
            $ventasPorOfertaDia[$k][$f] += $usdVenta
            # Y contada: una fila de la hoja es una venta (la tercera columna de la serie).
            if (-not $nVentasPorOfertaDia.ContainsKey($k)) { $nVentasPorOfertaDia[$k] = @{} }
            if (-not $nVentasPorOfertaDia[$k].ContainsKey($f)) { $nVentasPorOfertaDia[$k][$f] = 0 }
            $nVentasPorOfertaDia[$k][$f] += 1
        }
    }

    # agrupa ads por oferta
    $g = @{}
    foreach ($p in $c.adMeta.PSObject.Properties) {
        $adId = $p.Name; $m = $p.Value
        # MANDA EL NOMBRE DE LA CAMPANA cuando dice de que oferta es (su clave cae en una etiqueta
        # de la hoja); la venta solo decide cuando el nombre no dice nada. Es lo que hace que el
        # gasto de una oferta cuadre con el que muestra Facebook al filtrar por su campana: antes
        # un anuncio se llevaba TODO su gasto a la oferta con mas ventas, y una sola venta
        # registrada en otra pestana le pasaba a esa los dolares de un anuncio ajeno.
        $porNombre = AEtiquetaHoja (OfferKey $m.campaign)
        $key  = if ($etiquetas -contains $porNombre) { $porNombre }
                elseif ($offerByAd.ContainsKey($adId)) { AEtiquetaHoja $offerByAd[$adId] }
                else { $porNombre }
        if (!$g.ContainsKey($key)) { $g[$key] = NuevaEntrada }
        $e = $g[$key]
        $e.ads++
        if ($m.effectiveStatus -eq 'ACTIVE') { $e.activos++ }
        if ($spendKeys -contains $adId) { $e.spend += [double]$c.spendByAdId.$adId }
        if ($m.adName) { $e.nums += [string]$m.adName }
        # fechas de gasto -> longevidad real, y de paso el gasto de cada dia para la serie
        if ($c.spendByAdIdDate -and $c.spendByAdIdDate.PSObject.Properties.Name -contains $adId) {
            foreach ($d in $c.spendByAdIdDate.$adId.PSObject.Properties.Name) {
                $e.fechas += $d
                SumaDia $e.dia $d ([double]$c.spendByAdIdDate.$adId.$d) 0
            }
        }
    }

    # El total de la hoja entra entero. Una pestaña sin un solo anuncio conocido tambien
    # aparece: esconderla es justo perder de vista lo que falta conectar.
    foreach ($k in $ventasPorOferta.Keys) {
        if (-not $g.ContainsKey($k)) { $g[$k] = NuevaEntrada }
        $g[$k].sLoc = $ventasPorOferta[$k]
        $g[$k].sUsd = $ventasUsdPorOferta[$k]
        if ($ventasPorOfertaDia.ContainsKey($k)) {
            foreach ($f in $ventasPorOfertaDia[$k].Keys) { SumaDia $g[$k].dia $f 0 $ventasPorOfertaDia[$k][$f] ([int]$nVentasPorOfertaDia[$k][$f]) }
        }
    }

    # Gasto de un anuncio que Facebook cobra pero del que no tenemos ficha (adMeta) -cuenta
    # que dejo de estar conectada, anuncio borrado- no puede evaporarse: sin esto el total
    # de la torre seria menor que la factura de Facebook y nadie lo notaria.
    $adsConFicha = @($c.adMeta.PSObject.Properties.Name)
    $huerfano = 0.0
    $huerfanoDia = @{}
    foreach ($p in $c.spendByAdId.PSObject.Properties) {
        if ($adsConFicha -contains $p.Name) { continue }
        $huerfano += [double]$p.Value
        # El gasto huerfano tambien se reparte por dia, o la serie de (SIN ATRIBUIR) diria
        # cero mientras su total dice cientos de dolares.
        if ($c.spendByAdIdDate -and $c.spendByAdIdDate.PSObject.Properties.Name -contains $p.Name) {
            foreach ($d in $c.spendByAdIdDate.($p.Name).PSObject.Properties.Name) {
                if (-not $huerfanoDia.ContainsKey($d)) { $huerfanoDia[$d] = 0.0 }
                $huerfanoDia[$d] += [double]$c.spendByAdIdDate.($p.Name).$d
            }
        }
    }
    if ($huerfano -gt 0) {
        if (-not $g.ContainsKey('(SIN ATRIBUIR)')) { $g['(SIN ATRIBUIR)'] = NuevaEntrada }
        $g['(SIN ATRIBUIR)'].spend += $huerfano
        foreach ($d in $huerfanoDia.Keys) { SumaDia $g['(SIN ATRIBUIR)'].dia $d $huerfanoDia[$d] 0 }
    }

    # Un nombre de campana que no cuadro con ninguna pestana y ademas nunca vendio no es
    # una oferta: es gasto sin atribuir. Juntarlos en una sola fila deja de inventar
    # ofertas como VNTAS o PUBLICACION: "" y a la vez no esconde el dinero.
    $sinAtribuir = @($g.Keys | Where-Object {
        $_ -ne '(SIN ATRIBUIR)' -and $g[$_].sLoc -le 0 -and -not $ventasPorOferta.ContainsKey($_)
    })
    if ($sinAtribuir.Count -ge 1) {
        # Se acumula sobre la bolsa que ya exista: ahi puede estar el gasto huerfano de
        # arriba, y reemplazarla lo borraria.
        if (-not $g.ContainsKey('(SIN ATRIBUIR)')) { $g['(SIN ATRIBUIR)'] = NuevaEntrada }
        $bolsa = $g['(SIN ATRIBUIR)']
        foreach ($k in $sinAtribuir) {
            $bolsa.ads += $g[$k].ads; $bolsa.activos += $g[$k].activos
            $bolsa.spend += $g[$k].spend
            $bolsa.nums += $g[$k].nums; $bolsa.fechas += $g[$k].fechas
            foreach ($d in $g[$k].dia.Keys) { SumaDia $bolsa.dia $d $g[$k].dia[$d].g $g[$k].dia[$d].v ([int]$g[$k].dia[$d].n) }
            $g.Remove($k)
        }
    }

    foreach ($key in $g.Keys) {
        $e = $g[$key]
        # Se muestra si tiene anuncios, ventas O gasto: una fila de puro gasto huerfano
        # es justo la que no puede desaparecer.
        if ($e.ads -eq 0 -and $e.sLoc -le 0 -and $e.spend -le 0) { continue }
        $sUsd = $e.sUsd
        $roas = if ($e.spend -gt 0) { $sUsd / $e.spend } else { $null }

        $dias = 0; $desde = ''; $hasta = ''
        if ($e.fechas.Count -gt 0) {
            $ord = @($e.fechas | Sort-Object -Unique)
            $desde = $ord[0]; $hasta = $ord[-1]
            $dias = [int](([datetime]$hasta - [datetime]$desde).TotalDays) + 1
        }

        # etapa automatica
        $stage = 'pausada'
        if ($e.activos -gt 0) {
            if ($dias -lt $MinDiasAnalisis)      { $stage = 'anuncios' }
            elseif ($roas -ne $null -and $roas -ge $RoasEscalar) { $stage = 'escalando' }
            else                                  { $stage = 'analisis' }
        }

        $id   = ($cn.Substring(0,2) + '-' + ($key -replace '[^A-Z0-9]','')).ToLower()
        $nums = @($e.nums | Sort-Object -Unique)
        $vid  = VideosDe $key $nums

        # Cuando gasto por ultima vez. Un anuncio puede figurar ACTIVE en Facebook y llevar
        # tres semanas sin cobrar un peso -es lo que pasa cuando bloquean la linea de API de
        # una cuenta: la ficha se queda congelada en el ultimo estado que se pudo bajar-.
        # Sin este dato la torre muestra "peru 27/134 activos" de una operacion parada, y
        # quien la mira cree que ese pais esta rodando. Es una fecha y un conteo de dias:
        # no dice cuanto se gasto, asi que puede viajar tambien a la vista sin dinero.
        $ultimoGasto = ''
        foreach ($f in $e.dia.Keys) {
            if ($e.dia[$f].g -gt 0 -and $f -gt $ultimoGasto) { $ultimoGasto = $f }
        }
        $sinGasto = $null
        if ($ultimoGasto) { $sinGasto = [int]((Get-Date).Date - [datetime]$ultimoGasto).TotalDays }
        $o = [pscustomobject]@{
            id       = $id
            nombre   = $key
            pais     = $cn
            stage    = $stage
            ads      = $e.ads
            adsActivos = $e.activos
            numeros  = $nums
            videos   = $vid
            dias     = $dias
            desde    = $desde
            hasta    = $hasta
            ultimoGasto  = $ultimoGasto
            diasSinGasto = $sinGasto
            spendUsd = [Math]::Round($e.spend,2)
            salesLoc = [Math]::Round($e.sLoc,0)
            salesUsd = [Math]::Round($sUsd,2)
            # fecha -> @(gastoUsd, ventasUsd, nVentas). Solo los dias con movimiento: una oferta de
            # cien dias suele tener veinte con gasto, y escribir los otros ochenta en cero
            # engordaria offers.json sin decir nada.
            serie    = ConvertirSerie $e.dia $fx
            moneda   = $c.currency
            roas     = if ($roas -ne $null) { [Math]::Round($roas,2) } else { $null }
            manual   = ActivosDe $key
            notas    = if ($notas.ContainsKey($id)) { $notas[$id] } else { '' }
        }
        $out += $o
    }
}

# Ofertas que solo existen en la fabrica todavia: tienen contenido o flujo generado pero
# ningun anuncio. Sin esto la torre solo mostraba lo ya lanzado, y el trabajo en curso
# -que es justo lo que hay que ver para saber que falta- quedaba invisible.
$yaVistos = @($out | ForEach-Object { Slug $_.nombre })
foreach ($sl in $activos.Keys) {
    if ($yaVistos | Where-Object { $sl -eq $_ -or $sl -like "*$_*" -or $_ -like "*$sl*" }) { continue }
    $a = $activos[$sl]
    $nombreFab = ($sl -replace '-',' ').ToUpper()
    $vidFab    = VideosDe $nombreFab @()
    # Si ya hay videos grabados, la oferta esta mas adelante que su ultimo archivo generado.
    $stage = if ($vidFab.total -gt 0) { 'videos' }
             elseif ($a.ContainsKey('flujoWa')) { 'flujo-wa' }
             elseif ($a.ContainsKey('entregable')) { 'entregable' }
             elseif ($a.ContainsKey('contenido')) { 'contenido' }
             else { 'idea' }
    $out += [pscustomobject]@{
        id = "fab-$sl"; nombre = $nombreFab; pais = 'sin lanzar'
        stage = $stage; ads = 0; adsActivos = 0; numeros = @(); videos = $vidFab; dias = 0; desde = ''; hasta = ''
        ultimoGasto = ''; diasSinGasto = $null
        spendUsd = 0; salesLoc = 0; salesUsd = 0; moneda = ''; roas = $null; serie = [ordered]@{}
        manual = [pscustomobject]@{
            contenido  = $a.ContainsKey('contenido')
            entregable = $a.ContainsKey('entregable')
            flujoWa    = $a.ContainsKey('flujoWa')
            archivos   = @($a.Values)
        }
        notas = ''
    }
}

# Carpetas de Drive que no corresponden a ninguna oferta conocida: el editor grabo algo
# que todavia no existe ni en campanas ni en la fabrica. Es trabajo real y hay que verlo.
$slugsActuales = @($out | ForEach-Object { Slug $_.nombre })
foreach ($c in $carpetas) {
    $cs = Slug ([string]$c.carpeta)
    if (-not $cs) { continue }
    if (@($slugsActuales | Where-Object { $cs -eq $_ -or $cs -like "*$_*" -or $_ -like "*$cs*" }).Count -gt 0) { continue }
    $nums = @(@($c.videos) | Where-Object { $_.numero } | ForEach-Object { [string]$_.numero } | Sort-Object -Unique)
    $out += [pscustomobject]@{
        id = "drv-$cs"; nombre = ([string]$c.carpeta).ToUpper(); pais = 'sin lanzar'
        stage = 'videos'; ads = 0; adsActivos = 0; numeros = @(); dias = 0; desde = ''; hasta = ''
        videos = [pscustomobject]@{ total = $nums.Count; montados = 0; pendientes = $nums; carpetas = @([string]$c.carpeta) }
        ultimoGasto = ''; diasSinGasto = $null
        spendUsd = 0; salesLoc = 0; salesUsd = 0; moneda = ''; roas = $null; serie = [ordered]@{}
        manual = [pscustomobject]@{ contenido=$false; entregable=$false; flujoWa=$false; archivos=@() }
        notas = ''
    }
    $slugsActuales += $cs
}

$cat.generatedAt = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
$cat.offers = @($out | Sort-Object @{e={$_.spendUsd};Descending=$true})
$susOrd = @($sospechosas | Sort-Object monto -Descending)
if ($cat.PSObject.Properties.Name -contains 'sospechosas') { $cat.sospechosas = $susOrd }
else { $cat | Add-Member -NotePropertyName sospechosas -NotePropertyValue $susOrd -Force }
($cat | ConvertTo-Json -Depth 12) | Out-File $offersPath -Encoding utf8 -NoNewline

# resumen compacto
$byStage = $out | Group-Object stage | Sort-Object Count -Descending | ForEach-Object { "$($_.Name)=$($_.Count)" }
$esc = @($out | Where-Object { $_.stage -eq 'escalando' } | Sort-Object roas -Descending)
"OK -> offers.json  |  $($out.Count) ofertas  |  $($byStage -join '  ')"
if ($esc.Count -gt 0) {
    "GANADORAS (ROAS >= $RoasEscalar):"
    foreach ($x in ($esc | Select-Object -First 6)) {
        "   {0,-28} {1,-10} ROAS {2,5:N2}x  gasto `${3,7:N0}  {4} ads  {5} dias" -f $x.nombre, $x.pais, $x.roas, $x.spendUsd, $x.adsActivos, $x.dias
    }
}
if ($sospechosas.Count -gt 0) {
    ""
    "!! {0} ORDENES SOSPECHOSAS AISLADAS (monto > 500x la mediana del pais) - revisa la hoja:" -f $sospechosas.Count
    foreach ($b in ($sospechosas | Sort-Object monto -Descending | Select-Object -First 5)) {
        "   {0}  {1}  monto={2:N0} {3}" -f $b.pais, $b.fecha, $b.monto, $b.moneda
    }
}
