# Arma las paginas de las herramientas y les pone el menu a todas.
#
#   powershell -ExecutionPolicy Bypass -File scripts\Armar-Suite.ps1 [-DataDir data] [-Salida carpeta]
#
# Corre DESPUES de build-dashboard.ps1 (lee data\snapshot.json). Escribe en -Salida (por defecto la raiz del repo):
#   radar.html            el radar de ofertas (las ofertas las pide al servidor: /api/radar-ofertas)
#   tablero.html          el tablero de produccion, con Expansion LATAM y Variaciones (Revivir ofertas usa las ventas por oferta del snapshot)
#   pdf.html              el creador de ebooks y su produccion automatica
#   modelar-pdf.html      sacar una coleccion nueva a partir del PDF de un entregable ajeno
#   apis.html             las llaves de IA (APIMart, Gemini...) y la voz (Fish Audio)
#   ofertas.html          los flujos de ZapData: nueva oferta, plantilla del guion, mockup, paises (datos de cobro) y conexion
#   fabrica.html          la fabrica de ofertas: ebooks, anuncios y flujos de una oferta, con su guia de lanzamiento
#   editor-anuncios.html  baja el instalador del editor de anuncios (y editor-anuncios.zip, que arma editor-anuncios\Empaquetar-Editor.ps1)
# y pega templates\suite-tokens.html (colores) + templates\menu-plantilla.html (la barra de herramientas) en TODAS las .html de -Salida.
#
# Es la version reducida del armado de la suite del sistema del que salio esta plantilla: solo las paginas que trae la plantilla.
# Todos los marcadores se cambian en UN solo paso: lo pegado (nombres de campanas y ofertas, que escribe cualquiera) nunca se vuelve a leer.
# Si algo de los datos falla, la pagina se arma igual con la lista vacia (un tablero sin "Revivir ofertas" es mejor que un refresco caido).
# Todo en ASCII a proposito (PowerShell 5.1 lee mal las tildes de un .ps1 sin BOM). Corre igual en Windows y en Linux (GitHub Actions).
param(
    [string]$Raiz    = (Split-Path -Parent $PSScriptRoot),
    [string]$DataDir = '',
    [string]$Salida  = ''
)
$ErrorActionPreference = 'Stop'
if (-not $DataDir) { $DataDir = Join-Path $Raiz 'data' }
if (-not $Salida)  { $Salida = $Raiz }
$tplDir = Join-Path $Raiz 'templates'
$utf8 = New-Object Text.UTF8Encoding($false)
function Leer([string]$nombre) { return [IO.File]::ReadAllText((Join-Path $tplDir $nombre), [Text.Encoding]::UTF8) }
# El JSON va DENTRO de un <script>: el signo "menor que" escapado, para que un nombre con </script> no cierre el bloque.
function JsonSeguro([string]$j) { return $j.Replace('<', ('\' + 'u003c')) }

# ---------------------------------------------------------------- datos de las ventas por oferta (tablero > Variaciones > Revivir ofertas)
$snapPath = Join-Path $DataDir 'snapshot.json'
$s = $null
if (Test-Path $snapPath) { $s = [IO.File]::ReadAllText($snapPath, [Text.Encoding]::UTF8) | ConvertFrom-Json }

# offers.json: las ofertas con su serie de gasto y ventas por dia, deducidas del snapshot (scripts\Sync-Offers.ps1). Si no existe, nace vacio.
$pathOf = Join-Path $DataDir 'offers.json'
$ventasOfertas = [ordered]@{ generadoEn = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'); ofertas = @() }
if ($s) {
    try {
        # Sync-Offers.ps1 completa un catalogo que ya existe (generatedAt, offers, sospechosas): si no hay, nace vacio con esos campos.
        if (-not (Test-Path $pathOf)) { [IO.File]::WriteAllText($pathOf, '{"generatedAt":"","offers":[],"sospechosas":[]}', $utf8) }
        & (Join-Path $PSScriptRoot 'Sync-Offers.ps1') -DataDir $DataDir | Out-Null
        $cat = [IO.File]::ReadAllText($pathOf, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $gen = $cat.generatedAt
        if ($gen -is [datetime]) { $ventasOfertas.generadoEn = $gen.ToString('yyyy-MM-ddTHH:mm:ssK') } elseif ($gen) { $ventasOfertas.generadoEn = [string]$gen }
        foreach ($o in $cat.offers) {
            if (-not $o.serie -or ([string]$o.nombre).Trim() -eq '(SIN ATRIBUIR)') { continue }
            if (-not @($o.serie.PSObject.Properties).Count) { continue }
            $ventasOfertas.ofertas += [ordered]@{ n = [string]$o.nombre; p = [string]$o.pais; m = [string]$o.moneda; s = $o.serie }
        }
    } catch { Write-Host ('  ventas por oferta: ' + $_.Exception.Message + ' (el tablero sale sin Revivir ofertas)') }
}
$ventasOfertasJson = ConvertTo-Json $ventasOfertas -Depth 6 -Compress

# La oferta de una campana, leida de su NOMBRE: se quitan los pedazos que no son oferta (canal ZAP / WPP / #N, fechas como 6 OCT, objetivo
# INT - COM, codigos de anuncio AD 101.1, numeros sueltos) y lo que queda es la oferta. Ej.: "ZAP #3 | RECETAS NAVIDENAS | INT - COM | 6 OCT | 102".
$marcas = '[' + [char]0x300 + '-' + [char]0x36f + ']'
function Get-OfertaDeCampana([string]$camp) {
    $t = (($camp.ToUpperInvariant().Normalize([Text.NormalizationForm]::FormD)) -replace $marcas, '') -replace '\s+', ' '
    $b = [regex]::Match($t, '\[\s*ZAP\s*\]\s*\[\s*([^\]]+?)\s*\]')
    if ($b.Success) { return (($b.Groups[1].Value -replace '\s+AD\s*\d+.*$', '').Trim()) }
    foreach ($seg in ($t -split '\|')) {
        $x = $seg.Trim()
        if (-not $x) { continue }
        if ($x -match '\bZAP(DATA)?\b' -or $x -match 'WPP' -or $x -match '#') { continue }
        if ($x -match '^\d{1,2}\s+[A-Z]{3,5}\b') { continue }
        if ($x -match '^(AD|ABO|CBO)\b[\s\d.]*$') { continue }
        if ($x -match '^[\d.\s-]+$') { continue }
        if ($x -match '^(INT|INTERACCION|VENTA|VENTAS|COM|CONV|MSG|TRAFICO)(\s*[-<>]+\s*(INT|COM|CONV|VENTAS?|MSG))*(\s+\d+)?$') { continue }
        return (($x -replace '\s*-\s*COPIA\b.*$', '').Trim())
    }
    return ''
}
# Que anuncios propios vendieron mas en cada oferta y pais: la base de cada variacion de "Revivir ofertas".
$anunciosOfertasJson = '{"grupos":[]}'
if ($s) {
    try {
        . (Join-Path $PSScriptRoot 'Anuncios-Por-Oferta.ps1')
        $anunciosOfertas = Get-AnunciosOfertas $s ([DateTime]::UtcNow.AddHours(-5).Date) { param($camp) Get-OfertaDeCampana $camp }
        $anunciosOfertasJson = ConvertTo-Json $anunciosOfertas -Depth 6 -Compress
    } catch { Write-Host ('  anuncios por oferta: ' + $_.Exception.Message + ' (Revivir ofertas sale sin anuncios base)') }
}

# ---------------------------------------------------------------- fabrica: lineas de WhatsApp y ventas de prueba
# La guia de lanzamiento de /fabrica arma el nombre de la campana con el "WPP #N" y el pais de cada conexion de ZapData. Se leen de
# data\lineas-whatsapp.json si existe: [ { "id": "<id de la conexion en ZapData>", "wpp": "3", "pais": "colombia", "nombre": "...", "estado": "conectado" } ].
# Sin el archivo va la lista vacia y la guia pide el numero a mano.
$fabLineasJson = '[]'
$pathLineas = Join-Path $DataDir 'lineas-whatsapp.json'
if (Test-Path $pathLineas) {
    try {
        $lin = @(([IO.File]::ReadAllText($pathLineas, [Text.Encoding]::UTF8) | ConvertFrom-Json) | Where-Object { $_.id } | ForEach-Object {
            [ordered]@{ id = [string]$_.id; wpp = [string]$_.wpp; pais = [string]$_.pais; nombre = [string]$_.nombre; estado = [string]$_.estado } })
        if ($lin.Count) { $fabLineasJson = ConvertTo-Json @($lin) -Depth 3 -Compress }
        if (-not $fabLineasJson.TrimStart().StartsWith('[')) { $fabLineasJson = '[' + $fabLineasJson + ']' }
    } catch { Write-Host ('  lineas-whatsapp.json ilegible: ' + $_.Exception.Message) }
}
$fabPruebasJson = '{"generadoEn":"","ventas":[]}'

# ---------------------------------------------------------------- editor de anuncios: el instalador que baja la pagina
$editorZip = Join-Path $Salida 'editor-anuncios.zip'
$editorVersion = '?'; $editorKb = 0; $editorZipOk = 'no'; $editorFecha = Get-Date -Format 'yyyy-MM-dd'
try {
    $editorDir = Join-Path $Raiz 'editor-anuncios'
    $vf = Join-Path $editorDir 'VERSION'
    if (Test-Path $vf) { $editorVersion = ([IO.File]::ReadAllText($vf)).Trim() }
    if (Test-Path $editorZip) { Remove-Item $editorZip -Force }
    & (Join-Path $editorDir 'Empaquetar-Editor.ps1') -Salida $editorZip *> $null
    if (Test-Path $editorZip) {
        $editorKb = [int]((Get-Item $editorZip).Length / 1024)
        $editorZipOk = 'si'
        Write-Host ('  editor -> editor-anuncios.zip (version {0}, {1} KB)' -f $editorVersion, $editorKb)
    } else { Write-Host '  editor: el empaquetador no dejo el .zip; la pagina avisara que no esta' }
} catch {
    Write-Host ('  editor: no se pudo armar el paquete (' + $_.Exception.Message + ')')
    if (Test-Path $editorZip) { Remove-Item $editorZip -Force }
}

# ---------------------------------------------------------------- las paginas
$valores = @{
    '__RADAR_SEGUIMIENTO_JS__'  = (Leer 'radar-seguimiento.js')
    '__OFERTAS_PROPIAS_JSON__'  = '[]'
    '__EBOOK_PRODUCCION_JS__'   = (Leer 'ebook-produccion.js')
    '__REVIVIR_OFERTAS_JS__'    = (Leer 'revivir-ofertas.js')
    '__EBOOK_PDF_JS__'          = (Leer 'ebook-pdf.js')
    '__FABRICA_MOTOR_JS__'      = (Leer 'fabrica-motor.js')
    '__FABRICA_CAMPANAS_JS__'   = (Leer 'fabrica-campanas.js')
    '__MODELAR_MOTOR_JS__'      = (Leer 'modelar-motor.js')
    '__VENTAS_OFERTAS_JSON__'   = (JsonSeguro $ventasOfertasJson)
    '__ANUNCIOS_OFERTAS_JSON__' = (JsonSeguro $anunciosOfertasJson)
    '__FABRICA_LINEAS_JSON__'   = (JsonSeguro $fabLineasJson)
    '__FABRICA_PRUEBAS_JSON__'  = (JsonSeguro $fabPruebasJson)
    '__EDITOR_VERSION__'        = [System.Net.WebUtility]::HtmlEncode($editorVersion)
    '__EDITOR_KB__'             = [string]$editorKb
    '__EDITOR_FECHA__'          = $editorFecha
    '__EDITOR_ZIP__'            = $editorZipOk
}
# Un marcador que no esta en la tabla se deja tal cual (algunas paginas traen nombres con guiones bajos que no son marcadores).
$patron = [regex]'__[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*__'
$cambia = [System.Text.RegularExpressions.MatchEvaluator]{ param($m) if ($valores.ContainsKey($m.Value)) { [string]$valores[$m.Value] } else { $m.Value } }
$paginas = [ordered]@{
    'radar.template.html'           = 'radar.html'
    'produccion.template.html'      = 'tablero.html'
    'armar-pdf.template.html'       = 'pdf.html'
    'modelar-pdf.template.html'     = 'modelar-pdf.html'
    'apis.template.html'            = 'apis.html'
    'ofertas.template.html'         = 'ofertas.html'
    'fabrica.template.html'         = 'fabrica.html'
    'editor-anuncios.template.html' = 'editor-anuncios.html'
}
foreach ($p in $paginas.Keys) {
    $html = $patron.Replace((Leer $p), $cambia)
    [IO.File]::WriteAllText((Join-Path $Salida $paginas[$p]), $html, $utf8)
    Write-Host ('  pagina -> ' + $paginas[$p])
}

# ---------------------------------------------------------------- el menu, en todas (idempotente: el bloque viejo se cambia por el nuevo)
$MARCA = '<!--MENU-PLANTILLA-->'; $FIN = '<!--/MENU-PLANTILLA-->'
$bloque = $MARCA + "`n" + (Leer 'suite-tokens.html') + "`n" + (Leer 'menu-plantilla.html') + "`n" + $FIN + "`n"
$n = 0
foreach ($f in (Get-ChildItem $Salida -Filter '*.html' -File)) {
    if ($f.Name -eq '404.html') { continue }
    $html = [IO.File]::ReadAllText($f.FullName, [Text.Encoding]::UTF8)
    if ($html.Contains($MARCA)) { $html = [regex]::Replace($html, '(?s)\s*' + [regex]::Escape($MARCA) + '.*?' + [regex]::Escape($FIN) + '\s*', "`n") }
    # Justo despues del PRIMER <meta charset> (por indice: el creador de ebooks lleva esa misma cadena DENTRO de su JavaScript).
    $m = [regex]::Match($html, '(?i)<meta\s+charset[^>]*>')
    if ($m.Success) { $corte = $m.Index + $m.Length; $html = $html.Substring(0, $corte) + "`n" + $bloque + $html.Substring($corte) }
    else { $html = $bloque + $html }
    [IO.File]::WriteAllText($f.FullName, $html, $utf8)
    $n++
}
Write-Host ('Menu puesto en {0} paginas.' -f $n)
