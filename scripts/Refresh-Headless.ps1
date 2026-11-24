# Fully headless refresh -- NO agent, NO MCP, NO tokens spent. Pulls every sheet via its
# public CSV export by gid (2026-10-28; see Get-ExportCsv: gviz leaves cells blank when they
# do not match the type it guesses for the column) with the gviz CSV as fallback and for
# hidden tabs, Facebook spend for MX/CO/PE via the Graph API, and the FX rate,
# then runs the deterministic build. Designed to run from Windows Task Scheduler every N
# minutes and (once configured) deploy to Netlify.
#
# ASCII-only on purpose: PowerShell 5.1 misreads a BOM-less .ps1 that has non-ASCII chars
# (accents, em-dashes) and the parser breaks. Keep it plain ASCII.
#
# Venezuela's ad SPEND is the one thing this can't fetch headless (it's on Windsor, which
# needs an agent) -- until VE is migrated to the direct Graph API its spend stays frozen
# at whatever windsor.json holds; VE sales still refresh here via CSV.
#
# Safety: gviz silently returns the FIRST tab (HTTP 200) if a tab name doesn't match, so
# cada descarga se compara con la primera pestana del libro antes de sobrescribir el ultimo
# CSV bueno. Si son identicas y no era la que se pidio, llego la equivocada: se avisa y se
# conserva el CSV anterior.
#
# Cada corrida deja data\refresh-status.json con que pestana se escribio y cual no. Sin esa
# bitacora, una pestana que deja de bajar no se nota: el cuadre compara el CSV contra la
# torre y los dos salen del mismo archivo, asi que un CSV congelado cuadra consigo mismo.
# Lo revisa scripts\Verificar-Frescura.ps1.
param(
    [string]$DataDir = (Join-Path (Split-Path $PSScriptRoot) 'data'),
    [string]$Root    = (Split-Path $PSScriptRoot),
    [switch]$SkipDeploy   # when set, fetch+build only; the caller (e.g. the CI workflow) deploys
)
$ErrorActionPreference = 'Stop'
# Para leer las fechas de la bitacora con el MISMO parseo que el build.
. (Join-Path $PSScriptRoot '_ventas-comun.ps1')
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Write-Log([string]$m) { Write-Output ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

# gid de cada pestana visible, por libro (id del libro -> nombre recortado -> gid). Lo llena Get-SheetTabNames con la misma peticion al htmlview.
$script:GidsDeTabs = @{}
# De donde salio el CSV de cada archivo en esta corrida ('export' o 'gviz'); va a la bitacora.
$script:OrigenTab = @{}

function Get-GvizCsv([string]$sheetId, [string]$tabName) {
    $url = "https://docs.google.com/spreadsheets/d/$sheetId/gviz/tq?tqx=out:csv"
    if ($tabName) { $url += "&sheet=" + [Uri]::EscapeDataString($tabName) }
    $r = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 60 -MaximumRedirection 5
    return $r.Content
}

# La exportacion trae todas las celdas. Se decodifica el cuerpo crudo como UTF-8 (no el .Content de PowerShell 5.1, que depende de la cabecera de la respuesta).
#
# Tiempo y corte: cada peticion espera 30 s (gviz sigue en 60) y el job de CI tiene tope de 10 min, asi que unas cuantas exportaciones colgadas no pueden comerse la corrida. Tras
# $MAX_FALLOS_EXPORT fallos SEGUIDOS (error, tiempo agotado o una pagina HTML en vez del CSV) se deja de intentar la exportacion en esta corrida y todo sigue por gviz, como antes.
# Google a veces contesta 200 con una pagina HTML (limite de peticiones, pantalla de acceso): eso cuenta como fallo, y la pestana se vuelve a pedir por gviz.
$MAX_FALLOS_EXPORT = 4
$script:FallosExport = 0
function Get-ExportCsv([string]$sheetId, [string]$gid) {
    $url = "https://docs.google.com/spreadsheets/d/$sheetId/export?format=csv&gid=$gid"
    $r = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 5
    $texto = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()).TrimStart([char]0xFEFF)
    if ($texto -match '^\s*<') { throw 'Google contesto una pagina HTML y no un CSV' }
    return $texto
}

# Validate the CSV looks like a sales tab AND, when it has data, that its first rows
# mention the expected keyword -- cheap guard against gviz handing back the wrong tab.
# Empty tabs (headers only) pass (they contribute nothing).
function Test-SheetCsv([string]$csv, [string]$primeraCsv, [bool]$esPrimera) {
    if (-not ($csv -match 'PAGO RECIBIDO')) { return @{ ok = $false; reason = 'no PAGO RECIBIDO header' } }
    # Una tabla dinamica tambien dice "PAGO RECIBIDO" -en "SUM de PAGO RECIBIDO"- y pasaba
    # por hoja de ventas. Las dos de Ecuador (DASHBORAD - BOLIS, DASHBORAD - DINAMICAS)
    # resumen las mismas ventas que sus pestanas de origen, asi que entrar habria contado
    # cada venta dos veces. Una hoja de ventas de verdad trae ademas quien compro.
    if (-not ($csv -match 'NOMBRE' -or $csv -match 'ETIQUETAS')) {
        return @{ ok = $false; reason = 'sin columna NOMBRE ni ETIQUETAS (parece un resumen, no ventas)' }
    }
    $lines = $csv -split "`r?`n" | Where-Object { $_.Trim() -ne '' }
    if ($lines.Count -le 1) { return @{ ok = $true; reason = 'empty (headers only)' } }
    # El peligro real es concreto: gviz contesta 200 y devuelve la PRIMERA pestana cuando el
    # nombre pedido no existe. Se detecta comparando con la primera pestana, no adivinando
    # por palabras.
    #
    # Antes se exigia que las primeras filas mencionaran una palabra sacada del nombre de la
    # pestana. Eso rechazaba pestanas buenas cuyo nombre no aparece en los datos: las de
    # Ecuador se llaman EC_BOLIS_CE_L1 y las filas dicen "Compra Realizada Bolis
    # Contraentrega Ecuador", asi que 13 de 14 pestanas del pais se rechazaban cada hora y
    # el CSV se quedo congelado una semana. Lo mismo con AIRFYER en Peru y ANTIIFLAMATORIAS
    # en Colombia.
    if ($primeraCsv -and -not $esPrimera -and $csv -eq $primeraCsv) {
        return @{ ok = $false; reason = 'gviz devolvio la PRIMERA pestana (el nombre pedido no existe)' }
    }
    return @{ ok = $true; reason = 'ok' }
}

# OJO con Write-Log aqui dentro: escribe al stream de salida, y lo que una funcion escribe
# al stream se pega a su valor de retorno. El llamador hace `if (Save-Tab ...) { $ok++ }`,
# asi que una funcion que "devuelve" tres lineas de texto y $false es un array no vacio:
# verdadero. Resultado: el contador decia "14/14 tabs refreshed" mientras se saltaba tabs,
# y el SKIP que explicaba por que nunca llegaba a la pantalla. Ecuador llevaba una semana
# con el CSV de Bolis congelado y el log jurando que todo estaba al dia.
# Dentro de esta funcion se usa Write-Host, que no toca el stream.
function Save-Tab([string]$sheetId, [string]$tabName, [string]$file, [string]$primeraCsv, [bool]$esPrimera) {
    $out = Join-Path $DataDir $file
    $aviso = { param($m) Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }
    try {
        # Primero la exportacion completa (por gid); gviz solo si esa peticion falla o la pestana no tiene gid conocido (las OCULTAS no salen en el htmlview). Ver Get-ExportCsv.
        # El rechazo estructural de Test-SheetCsv -no parece una hoja de ventas- es el mismo con una fuente que con la otra, asi que no se vuelve a pedir por gviz.
        $csv = $null
        $esExport = $false
        $gid = ''
        if ($script:GidsDeTabs.ContainsKey($sheetId) -and $script:GidsDeTabs[$sheetId].ContainsKey($tabName.Trim())) { $gid = [string]$script:GidsDeTabs[$sheetId][$tabName.Trim()] }
        if ($gid -and $script:FallosExport -lt $MAX_FALLOS_EXPORT) {
            try {
                $x = Get-ExportCsv -sheetId $sheetId -gid $gid
                if (-not [string]::IsNullOrWhiteSpace($x)) { $csv = $x; $esExport = $true; $script:FallosExport = 0 }
                else { $script:FallosExport++; & $aviso ("  {0} -- la exportacion llego vacia; uso gviz" -f $file) }
            } catch {
                $script:FallosExport++
                & $aviso ("  {0} -- la exportacion fallo ({1}); uso gviz" -f $file, $_.Exception.Message)
            }
            if ($script:FallosExport -ge $MAX_FALLOS_EXPORT) { & $aviso ("  OJO: {0} fallos seguidos de la exportacion; el resto de esta corrida baja por gviz (puede dejar celdas en blanco)" -f $script:FallosExport) }
        }
        if ($null -eq $csv) { $csv = Get-GvizCsv -sheetId $sheetId -tabName $tabName }
        # La comparacion con la PRIMERA pestana es solo de gviz (devuelve esa cuando el nombre no existe); la exportacion va por gid y no tiene ese fallo.
        $check = Test-SheetCsv -csv $csv -primeraCsv $(if ($esExport) { '' } else { $primeraCsv }) -esPrimera $esPrimera
        if (-not $check.ok) {
            # "Me quedo con el CSV anterior" solo es prudente si hay uno. Si el archivo no
            # existe todavia, quedarse con nada no protege de nada: deja la oferta en cero.
            #
            # Asi se perdieron las ventas de Airfryer de Peru. La pestana esta escrita
            # "AIRFYER" -sin la R- pero las filas dicen "Compra Realizada Airfryer", asi que
            # la clave que sale del nombre de la pestana no aparecia en los datos y esto la
            # rechazaba cada hora, en silencio, sobre un archivo que nunca existio.
            #
            # Pero la excusa vale SOLO para el rechazo por palabra clave, que es el caso de
            # Airfryer. Un rechazo estructural -no parece una hoja de ventas- no se salta
            # nunca: las dos tablas dinamicas de Ecuador (DASHBORAD - BOLIS, DASHBORAD -
            # DINAMICAS) entraron por aqui como si fueran ofertas, con 1.556 filas que no
            # son ventas sino un resumen de las ventas que ya estaban contadas en su
            # pestana de origen. No sumaron dinero de mas porque ninguna fila era legible,
            # pero se colaron dos ofertas fantasma en el tablero y en el detector.
            $estructural = $check.reason -match 'PAGO RECIBIDO|NOMBRE ni ETIQUETAS|PRIMERA pestana'
            $existe = Test-Path $out
            if ($existe -or $estructural) {
                & $aviso ("  SKIP {0} -- {1}; kept previous CSV" -f $file, $check.reason)
                return $false
            }
            & $aviso ("  OJO {0} -- {1}; la guardo igual porque no habia CSV previo" -f $file, $check.reason)
        }
        if ($esExport) { $csv = Remove-FilasVacias $csv }
        # El origen se anota solo si el archivo se escribio: una pestana en SKIP no es "por exportacion".
        $script:OrigenTab[$file] = $(if ($esExport) { 'export' } else { 'gviz' })
        [IO.File]::WriteAllText($out, $csv, (New-Object Text.UTF8Encoding($false)))
        return $true
    } catch {
        & $aviso ("  FAIL {0} -- {1}; kept previous CSV" -f $file, $_.Exception.Message)
        return $false
    }
}

# Pestanas que viven en los libros pero NO son ofertas. 'zapdata log' guarda el payload
# crudo de cada llamada del webhook, para depurar. El exportador ya la descarta al ver que
# no tiene columna PAGO RECIBIDO, pero antes de eso se anuncia como oferta nueva, y el
# detector de ofertas la reportaria como novedad un dia tras otro.
# 'reparaciones' es el registro del reparador de ventas (scripts/reparar-ventas.cjs): una fila por cambio que hizo en las hojas, con el antes y el despues.
$NO_SON_OFERTAS = @('ZAPDATA LOG', 'REPARACIONES')
# Pestanas de MEDICION que viven en el libro junto a las ventas: hoy 'order bump sermones' (Colombia, 2026-11-11), que cuenta con formulas cuantas compras de una oferta llevaron
# su order bump. Toda pestana cuyo nombre empieza por "ORDER BUMP" se ignora, para que el dueno pueda mostrarla (nace oculta) sin que el refresco la tome por una oferta nueva: si no,
# la anunciaria como OFERTA NUEVA cada hora y la bitacora la marcaria como pestana que no se pudo bajar (no tiene columna PAGO RECIBIDO). La conversion del order bump la calcula
# el dash con las ventas de la oferta, no con esta pestana.
# Desde 2026-11-11 (pedido del dueno: "todos los paises, ofertas y proximas ofertas") esas pestanas SON la configuracion de cada bump: no se bajan como oferta, pero se leen aparte
# (Save-PestanasBump, abajo) y el build mide el bump de cualquier oferta que tenga su pestana VISIBLE, sin tocar codigo. Ver scripts/_order-bumps.ps1.
$PREFIJOS_NO_OFERTA = @('ORDER BUMP')
. (Join-Path $PSScriptRoot '_order-bumps.ps1')
# Las pestanas "order bump ..." visibles de cada libro: pais del dash, id del libro, nombre. Las llena el bucle de los libros; Save-PestanasBump las baja al final.
$script:PestanasBump = New-Object System.Collections.Generic.List[object]
function Test-NoEsOferta([string]$nombre) {
    $n = ([string]$nombre).Trim().ToUpper() -replace '\s+', ' '
    if (-not $n) { return $false }
    if ($NO_SON_OFERTAS -contains $n) { return $true }
    foreach ($p in $PREFIJOS_NO_OFERTA) { if ($n.StartsWith($p)) { return $true } }
    return $false
}
# Los libros que llevan 'zapdata log' (el registro del webhook de ZapData). Peru y Ecuador no tienen webhook.
$LIBROS_CON_LOG = @()
# Pestanas de ZapData que el dueno quiere dejar FUERA del tablero a proposito (el nombre exacto de la pestana, en cualquier mayuscula). Antes ocultar una pestana bastaba para sacarla del
# tablero, sin querer: ahora las ocultas con ventas SI cuentan (Get-TabsDelLog), asi que para excluir una hay que anotarla aqui (y en mejora/vigilancia/config.json > hojasArchivadas, para que
# el vigilante de las 2 a. m. no la avise). Vacia a proposito: nadie ha pedido excluir ninguna.
$ARCHIVADAS = @()

# Plantilla: los IDs de tus libros de ventas NO van en el codigo (con el ID cualquiera abre la hoja con los datos de tus clientes).
# En GitHub salen de los SECRETOS SHEET_ID_MX, SHEET_ID_CO... (Settings > Secrets and variables > Actions > Secrets); en tu PC, de
# data\libros-de-ventas.json, que no se sube a GitHub. Ejemplo: { "CO": "<ID del libro de Colombia>", "MX": "" }. Ver CLAUDE.md, paso 1.
$script:LibrosLocales = $null
$librosLocales = Join-Path $DataDir 'libros-de-ventas.json'
if (Test-Path $librosLocales) { $script:LibrosLocales = Get-Content $librosLocales -Raw -Encoding UTF8 | ConvertFrom-Json }
function Get-LibroDeVentas([string]$pais) {
    $v = [string][Environment]::GetEnvironmentVariable('SHEET_ID_' + $pais)
    if (-not $v -and $script:LibrosLocales -and $script:LibrosLocales.PSObject.Properties[$pais]) { $v = [string]$script:LibrosLocales.$pais }
    $v = $v.Trim()
    if ($v -and $v -notmatch '^[A-Za-z0-9_-]{20,}$') { throw ('SHEET_ID_' + $pais + ' no parece el ID de un libro de Google: es lo que va entre /d/ y /edit en la direccion del libro.') }
    return $v
}
$MX_ID = (Get-LibroDeVentas 'MX')   # secreto SHEET_ID_MX de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
$VE_ID = (Get-LibroDeVentas 'VE')   # secreto SHEET_ID_VE de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
$CO_ID = (Get-LibroDeVentas 'CO')   # secreto SHEET_ID_CO de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
$PE_ID = (Get-LibroDeVentas 'PE')   # secreto SHEET_ID_PE de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
$GT_ID = (Get-LibroDeVentas 'GT')   # secreto SHEET_ID_GT de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
$AR_ID = (Get-LibroDeVentas 'AR')   # secreto SHEET_ID_AR de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
$EC_ID = (Get-LibroDeVentas 'EC')   # secreto SHEET_ID_EC de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
$BO_ID = (Get-LibroDeVentas 'BO')   # secreto SHEET_ID_BO de GitHub, o data\libros-de-ventas.json en tu PC (vacio = el pais no se baja). Ver CLAUDE.md
# Bitacora de la corrida: que pestana se escribio y cual no. La lee Verificar-Frescura.ps1.
$script:estado = @()
# Ecuador arranca sin lista fija, igual que Argentina. Su libro trae ademas pestanas que no
# son de ventas -Data_Unificada, Anuncios, los DASHBORAD-: la comprobacion de la cabecera
# PAGO RECIBIDO las descarta sola, sin tener que nombrarlas aqui una por una.
$ecTabs = @()
# Argentina arranca sin lista fija: la descubre entera del htmlview.
$arTabs = @()
# Bolivia (2026-10-09): tambien sin lista fija; las pestanas se descubren del htmlview.
$boTabs = @()

$veTabs = @()
$peTabs = @()
$gtTabs = @()
$coTabs = @()

# Mexico arranca con una sola pestana conocida y descubre el resto, igual que los demas.
#
# Hasta ahora bajaba UNA hoja -la primera del libro, DINAMICAS- con -tabName $null, asi que
# las otras tres pestanas del libro (CUPCAKES, AIRFRYER, LEY DE MORPHY) vendian sin que sus
# ventas llegaran a ningun lado: no existian como oferta, y el gasto de sus campanas caia en
# que pertenecer, y la cuenta "Recetas" corriendo AIRFRYER y CUPCAKES sin aparecer.
#
# Es exactamente el agujero que Venezuela tuvo con Cartas de Pablo y que el descubrimiento
# de abajo vino a tapar; Mexico se quedo fuera de esa migracion y nadie lo noto porque su
# unica oferta visible -Dinamicas- si funcionaba.
$mxTabs = @()

Write-Log "Headless refresh starting"

# Ask the sheet which tabs it has instead of trusting the list above. Venezuela's list
# said 7 while the sheet had grown to 10, so Cartas de Pablo sold there for weeks with
# its sales landing nowhere and the offer unselectable in the dashboard.
#
# The public htmlview page carries the tab names in an items.push({name: "..."}) call.
# It only lists VISIBLE tabs, so discovery is UNIONED with the configured list rather
# than replacing it - Venezuela keeps LONCHERAS and ARTETERAPIA, which are hidden.
function Get-SheetTabNames([string]$sheetId) {
    try {
        $html = (Invoke-WebRequest "https://docs.google.com/spreadsheets/d/$sheetId/htmlview" -UseBasicParsing -TimeoutSec 30).Content
        # El mismo items.push trae el gid de la pestana (items.push({name: "X", pageUrl: "...", gid: "123", ...)): Save-Tab lo usa para pedir la exportacion completa.
        $gids = @{}
        foreach ($m in [regex]::Matches($html, 'items\.push\(\{name:\s*"([^"]+)"[^}]*?gid:\s*"(\d+)"')) { $gids[$m.Groups[1].Value.Trim()] = $m.Groups[2].Value }
        $script:GidsDeTabs[$sheetId] = $gids
        return @([regex]::Matches($html, 'items\.push\(\{name:\s*"([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
    } catch {
        # Write-Host y no Write-Log: lo que una funcion manda al stream de salida se pega a
        # su valor de retorno. Con Write-Log, el dia que Google tardo de mas esta linea
        # volvio como si fuera el nombre de una pestana, se pidio a gviz, gviz contesto con
        # la PRIMERA hoja del libro y un pais acabo con todas sus
        # ventas contadas dos veces, una bajo el nombre del mensaje de error.
        Write-Host ("  no pude listar pestanas ({0}); sigo con la lista configurada" -f $_.Exception.Message)
        return @()
    }
}
# Las pestanas de ZapData que el webhook ESCRIBIO, leidas de su propio registro (la pestana 'zapdata log' de cada libro: una fila por venta con el texto escrito en "<pestana>" (<pais>)).
#
# El htmlview solo lista las pestanas VISIBLES. Cuando alguien oculta una pestana de ventas -ya paso con varias pestanas de ZapData- el
# entre las ocultas de Colombia, Venezuela y Guatemala). El registro del webhook no depende de eso: trae el nombre exacto, con tilde, de cada pestana donde cayo una venta, este oculta o
# no, y una hoja oculta SI se puede pedir por su nombre a gviz. Se agrupa en el servidor (una fila por nombre distinto, no por venta). Ante cualquier falla devuelve @(): el refresco sigue
# con las visibles y las fijas, como antes. OJO con Write-Log aqui dentro: lo que una funcion escribe al stream se pega a su valor de retorno (por eso Write-Host).
function Get-TabsDelLog([string]$sheetId) {
    try {
        $q = "select C, count(C) where C starts with 'escrito en' group by C label count(C) ''"
        $url = "https://docs.google.com/spreadsheets/d/$sheetId/gviz/tq?tqx=out:csv&sheet=" + [Uri]::EscapeDataString('zapdata log') + '&tq=' + [Uri]::EscapeDataString($q)
        $r = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 60 -MaximumRedirection 5
        # UTF-8 explicito: la tilde del nombre tiene que llegar entera, o gviz contesta con la primera hoja del libro.
        $texto = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
        $nombres = @()
        foreach ($m in [regex]::Matches($texto, 'escrito en ""([^"]+)""')) { $nombres += $m.Groups[1].Value }
        return @($nombres | Sort-Object -Unique)
    } catch {
        Write-Host ("  no pude leer el registro del webhook ({0}); sigo con las pestanas visibles y las fijas" -f $_.Exception.Message)
        return @()
    }
}
function New-TabEntry([string]$nombre, [string]$prefijo) {
    # Same filename shape build-dashboard expects, so a discovered tab needs no wiring.
    # Las tildes se transliteran ANTES de limpiar, no se borran: el webhook de ZapData crea la
    # pestana con el nombre exacto de la etiqueta ("zapdata bebes" con tilde, "zapdata dinamicas
    # mujer", "zapdata estampados panos de cocina") y quitar la letra acentuada dejaba "bebs",
    # "dinmicas", "paos": el tablero mostraba "Zapdata Bebs" junto a "Zapdata Bebes" como dos
    # ofertas, y las campanas de PANOS no calzaban con una oferta llamada "Paos".
    # El rango de marcas combinantes se arma con [char] y no se escribe: este archivo es ASCII.
    $marcas = "[" + [char]0x300 + "-" + [char]0x36f + "]"
    $plano = $nombre.Trim().Normalize([Text.NormalizationForm]::FormD) -replace $marcas,""
    $slug = ($plano -replace '\s+','_' -replace '[^A-Za-z0-9_]','')
    $kw = ($nombre.Trim() -split '\s+')[0]
    if ($kw.Length -gt 5) { $kw = $kw.Substring(0, [Math]::Min(6, $kw.Length)) }
    $archivo = "$prefijo$slug.csv"

    # Si ya hay un CSV que solo difiere en mayusculas, se reusa ESE nombre.
    #
    # Windows no distingue mayusculas y aqui nunca se noto, pero CI corre en Linux: la
    # pestana CARTAS DE PABLO de Venezuela se guardaba como ve_xlsx_CARTAS_DE_PABLO.csv
    # junto al viejo ve_xlsx_cartas_de_pablo.csv, el build leia los dos, y las mismas 30
    $existente = Get-ChildItem $DataDir -Filter "$prefijo*.csv" -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -eq $archivo -or $_.Name.ToLower() -eq $archivo.ToLower() } |
                 Select-Object -First 1
    if ($existente) { $archivo = $existente.Name }

    return @{ n = $nombre; f = $archivo; k = $kw.ToUpper() }
}

# Facebook and the sheets do not depend on each other (Facebook reads its own previous
# output and the token file; the sheets write CSVs), so the Facebook pull starts NOW, in
# its own process, and runs while the sheets download. It used to wait for them: ~40 s of
# pure waiting on every refresh. The wait happens further down, where it used to run.
$fbScript = Join-Path $Root 'scripts/Fetch-FacebookGraph.ps1'
$fbJob = Start-Job -ScriptBlock {
    param($s, $d, $t, $o)
    $ErrorActionPreference = 'Stop'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    & $s -DataDir $d -TokenFile $t -OutFile $o
} -ArgumentList $fbScript, $DataDir, (Join-Path $DataDir 'facebook-tokens.json'), (Join-Path $DataDir 'facebook-graph.json')

foreach ($pair in @(
    @{ id=$MX_ID; tabs=$mxTabs; name='Mexico';    pre='mx_' },
    @{ id=$VE_ID; tabs=$veTabs; name='Venezuela'; pre='ve_xlsx_' },
    @{ id=$CO_ID; tabs=$coTabs; name='Colombia';  pre='col_' },
    @{ id=$PE_ID; tabs=$peTabs; name='Peru';      pre='pe_' },
    @{ id=$GT_ID; tabs=$gtTabs; name='Guatemala'; pre='gt_' },
    @{ id=$AR_ID; tabs=$arTabs; name='Argentina'; pre='ar_' },
    @{ id=$EC_ID; tabs=$ecTabs; name='Ecuador';   pre='ec_' },
    @{ id=$BO_ID; tabs=$boTabs; name='Bolivia';   pre='bo_' }
)) {
    if (-not $pair.id) { Write-Log ("Sheets: {0} sin libro configurado (se salta)" -f $pair.name); continue }
    $conocidas = @($pair.tabs | ForEach-Object { $_.n.Trim().ToUpper() })
    $tabs = @($pair.tabs)
    foreach ($nombre in (Get-SheetTabNames $pair.id)) {
        # Segundo cinturon: una pestana de ventas se llama "MUNDIAL" o "CARTAS DE PABLO",
        # nunca una frase de sesenta caracteres. Aunque algo vuelva a colarse por el stream
        # de salida, no llegara a pedirse como pestana.
        # Las pestanas de order bump pueden tener nombre largo ("order bump kinesiotaping colombia ..."): hasta 60 letras pasan a Test-NoEsOferta, que las aparta como configuracion.
        $esBumpLargo = $nombre.Trim().ToUpper().StartsWith('ORDER BUMP') -and $nombre.Trim().Length -le 60
        if ($nombre.Trim().Length -gt 40 -and -not $esBumpLargo) {
            Write-Log ("  IGNORO un nombre de pestana que no parece uno: '{0}'" -f $nombre.Trim().Substring(0, 40))
            continue
        }
        if (Test-NoEsOferta $nombre) {
            if ($nombre.Trim().ToUpper().StartsWith('ORDER BUMP')) {
                $script:PestanasBump.Add(@{ pais = $pair.name.ToLower(); libro = $pair.id; n = $nombre.Trim() })
                Write-Log ("  Pestana de order bump en {0}: '{1}' (se lee como configuracion, no como oferta)" -f $pair.name, $nombre.Trim())
            } else {
                Write-Log ("  IGNORO la pestana '{0}': no es una oferta." -f $nombre.Trim())
            }
            continue
        }
        if ($conocidas -notcontains $nombre.Trim().ToUpper()) {
            $nueva = New-TabEntry $nombre $pair.pre
            $tabs += $nueva
            Write-Log ("  OFERTA NUEVA en {0}: '{1}' -> {2}" -f $pair.name, $nombre, $nueva.f)
        }
    }
    Write-Log ("Sheets: {0} ({1} tabs)" -f $pair.name, $tabs.Count)
    # Una peticion mas por libro: la pestana por defecto. Es la que gviz devuelve cuando el
    # nombre pedido no existe, asi que tenerla a mano convierte "parece la pestana
    # equivocada" en una comparacion exacta.
    $primeraCsv = ''
    try { $primeraCsv = Get-GvizCsv -sheetId $pair.id -tabName '' } catch { $primeraCsv = '' }
    # ZapData: las pestanas que el webhook escribio y que el htmlview no lista porque estan OCULTAS (ver Get-TabsDelLog). Solo se suman las que existen de verdad: una renombrada o
    # borrada contesta con la PRIMERA hoja (la misma comparacion de arriba), y eso no es una averia del refresco sino una pestana que ya no esta. Sin la primera hoja no se puede comparar.
    if ($primeraCsv -and ($LIBROS_CON_LOG -contains $pair.name)) {
        try {
            $yaEstan = @($tabs | ForEach-Object { $_.n.Trim().ToUpper() })
            foreach ($nombre in (Get-TabsDelLog $pair.id)) {
                $n = $nombre.Trim()
                if (-not $n -or $n.Length -gt 60 -or ($yaEstan -contains $n.ToUpper()) -or (Test-NoEsOferta $n) -or (@($ARCHIVADAS | ForEach-Object { $_.ToUpper() }) -contains $n.ToUpper())) { continue }
                $existe = $false
                try { $existe = ((Get-GvizCsv -sheetId $pair.id -tabName $n) -ne $primeraCsv) } catch { $existe = $false }
                if (-not $existe) { continue }
                $nueva = New-TabEntry $n $pair.pre
                $tabs += $nueva
                $yaEstan += $n.ToUpper()
                Write-Log ("  PESTANA OCULTA con ventas en {0}: '{1}' -> {2}" -f $pair.name, $n, $nueva.f)
            }
        } catch {
            # Un imprevisto aqui (nombre raro, red) no puede tumbar el refresco: se sigue con las pestanas visibles y las fijas, como antes.
            Write-Log ("  no pude sumar las pestanas ocultas de {0} ({1}); sigo con las visibles y las fijas" -f $pair.name, $_.Exception.Message)
        }
    }
    $ok = 0
    $i = 0
    foreach ($t in $tabs) {
        $i++
        # La primera pestana de la lista es, casi siempre, la primera del libro: para ella la
        # comparacion coincidiria de forma legitima y hay que desactivarla.
        $esPrimera = ($i -eq 1)
        $bien = Save-Tab -sheetId $pair.id -tabName $t.n -file $t.f -primeraCsv $primeraCsv -esPrimera $esPrimera
        if ($bien) { $ok++ }
        # Se anota el resultado de CADA pestana, se haya escrito o no.
        #
        # Sin esto no habia forma de saber que una pestana llevaba dias sin bajar: el cuadre
        # compara el CSV contra la torre, y los dos salen del mismo archivo, asi que un CSV
        # congelado cuadra perfecto consigo mismo. Ecuador estuvo una semana asi, con el
        $script:estado += [ordered]@{
            pais = $pair.name; pestana = $t.n; archivo = $t.f
            escrito = [bool]$bien
            filas = 0; ultimaVenta = ''; filasAntes = 0
            origen = [string]$script:OrigenTab[$t.f]
        }
    }
    $pair.tabs = $tabs
    $porExport = @($tabs | Where-Object { $script:OrigenTab[$_.f] -eq 'export' }).Count
    Write-Log ("  {0}/{1} tabs refreshed ({2} por exportacion completa, {3} por gviz)" -f $ok, $pair.tabs.Count, $porExport, @($tabs | Where-Object { $script:OrigenTab[$_.f] -eq 'gviz' }).Count)
}

# Las pestanas "order bump ..." (configuracion de cada order bump, ver _order-bumps.ps1) -> data/order-bumps-planilla.json, que lee el build. Solo las VISIBLES: el htmlview no
# lista las ocultas y por eso no se conoce su gid (gviz no sirve aqui: adivina un tipo por columna y deja en blanco lo que no calza, y esta pestana mezcla texto, montos y fechas).
# Se guardan solo las filas de ARRIBA de "Ventas con order bump": la lista de compradores lleva nombres y telefonos y no sale del libro. Una pestana que falla queda en 'fallas'
# (el build lo dice); una falla de todo este paso no tumba el refresco: el build sigue con data/order-bumps.json.
function Save-PestanasBump {
    $salida = [ordered]@{ generado = (Get-Date).ToString('s'); pestanas = @(); fallas = @() }
    foreach ($p in $script:PestanasBump) {
        try {
            # Si Google ya esta limitando las exportaciones en esta corrida (el mismo tope que Save-Tab), no se espera 30 s por cada pestana: el build sigue con el respaldo.
            if ($script:FallosExport -ge $MAX_FALLOS_EXPORT) { throw 'Google esta limitando las exportaciones en esta corrida; se usa el respaldo' }
            $gid = ''
            if ($script:GidsDeTabs.ContainsKey($p.libro) -and $script:GidsDeTabs[$p.libro].ContainsKey($p.n)) { $gid = [string]$script:GidsDeTabs[$p.libro][$p.n] }
            if (-not $gid) { throw 'no se conoce su gid' }
            $csv = Get-ExportCsv -sheetId $p.libro -gid $gid
            $cols = 12
            $objs = @($csv | ConvertFrom-Csv -Header @(1..$cols | ForEach-Object { "c$_" }))
            $filas = Select-FilasBump $objs $cols
            $salida.pestanas += [ordered]@{ pais = $p.pais; pestana = $p.n; filas = $filas }
        } catch {
            if ($_.Exception.Message -notmatch 'limitando|gid') { $script:FallosExport++ }
            $salida.fallas += [ordered]@{ pais = $p.pais; pestana = $p.n; error = $_.Exception.Message }
            Write-Log ("  no pude leer la pestana de order bump '{0}' ({1}): {2}" -f $p.n, $p.pais, $_.Exception.Message)
        }
    }
    $ruta = Join-Path $DataDir 'order-bumps-planilla.json'
    [IO.File]::WriteAllText($ruta, ($salida | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
    Write-Log ("Order bumps: {0} pestana(s) de configuracion leidas, {1} con falla -> {2}" -f @($salida.pestanas).Count, @($salida.fallas).Count, $ruta)
}
try { Save-PestanasBump } catch { Write-Log ("  no pude guardar las pestanas de order bump ({0}); el build sigue con data/order-bumps.json" -f $_.Exception.Message) }

# Se completa la bitacora con lo que quedo en disco -cuantas filas y de que dia es la venta
# mas nueva- y se guarda. Verificar-Frescura.ps1 la lee y avisa si una pestana de ventas se
# quedo sin bajar o si un pais lleva dias sin una venta nueva.
# Las filas de la corrida ANTERIOR, para poder ver si una pestana encogio. Una hoja que se
# alimenta dia a dia solo crece; si pierde filas, o llego la pestana equivocada o alguien
# borro contenido, y las dos cosas hay que verlas el mismo dia.
$antes = @{}
$rutaPrev = Join-Path $DataDir 'refresh-status.json'
if (Test-Path $rutaPrev) {
    try {
        $prev = Get-Content $rutaPrev -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($x in $prev.pestanas) { $antes[[string]$x.archivo] = [int]$x.filas }
    } catch { }
}
foreach ($e in $script:estado) {
    $e.filasAntes = 0
    if ($antes.ContainsKey([string]$e.archivo)) { $e.filasAntes = $antes[[string]$e.archivo] }
    $ruta = Join-Path $DataDir $e.archivo
    if (-not (Test-Path $ruta)) { continue }
    try {
        $filas = @(Import-Csv $ruta)
        $e.filas = $filas.Count
        $max = ''
        foreach ($r in $filas) {
            $f = Convert-SaleDate ([string]$r.FECHA)
            if ($f) { $d = $f.ToString('yyyy-MM-dd'); if ($d -gt $max -and $d -le (Get-Date).ToString('yyyy-MM-dd')) { $max = $d } }
        }
        $e.ultimaVenta = $max
    } catch { }
}
$bitacora = [ordered]@{
    corridaEn = (Get-Date).ToString('s')
    pestanas  = @($script:estado)
}
[IO.File]::WriteAllText((Join-Path $DataDir 'refresh-status.json'),
    ($bitacora | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))

Write-Log "Facebook Graph (MX/CO/PE/GT/VE)"
# The Facebook pull has been running in the background since before the sheets started
# (see $fbJob above); here we only wait for whatever is left of it. Same script, same
# arguments, same warnings -- Receive-Job replays them into this log.
$null = Wait-Job $fbJob
Receive-Job $fbJob -ErrorAction Continue | Out-Null
if ($fbJob.State -eq 'Failed') {
    $motivo = ''
    try { $motivo = [string]$fbJob.ChildJobs[0].JobStateInfo.Reason.Message } catch { }
    Remove-Job $fbJob -Force
    throw ("Fetch-FacebookGraph.ps1 fallo: " + $motivo)
}
Remove-Job $fbJob -Force

Write-Log "FX rate"
try {
    $fx = Invoke-WebRequest 'https://open.er-api.com/v6/latest/USD' -UseBasicParsing -TimeoutSec 30
    $fxObj = $fx.Content | ConvertFrom-Json

    # Tasas fijadas a mano. Venezuela tiene tipo de cambio paralelo: la tasa oficial que
    # publica la API no es a la que el negocio realmente cambia, asi que usarla infla las
    # ventas en USD. La de fx-overrides.json manda sobre la API.
    $ovPath = Join-Path $DataDir 'fx-overrides.json'
    if (Test-Path $ovPath) {
        $ov = Get-Content $ovPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $ov.PSObject.Properties) {
            if ($p.Name -like '_*') { continue }
            # Una moneda puede venir como numero suelto o como fecha->tasa. Del historial se
            # toma la mas reciente para el campo "rates", que es la tasa de HOY: la que se
            # usa para el gasto y para lo que se muestra en el encabezado. La conversion de
            # cada venta por su fecha la hace build-dashboard con el historial completo.
            $valor = $p.Value
            if ($valor -is [psobject] -and -not ($valor -is [string]) -and $valor.PSObject.Properties.Name.Count) {
                $fechas = @($valor.PSObject.Properties.Name | Sort-Object)
                $valor = Get-TasaDe $valor.($fechas[-1])
            }
            $fxObj.rates | Add-Member -NotePropertyName $p.Name -NotePropertyValue ([double]$valor) -Force
            Write-Log ("  override: 1 USD = {0} {1}" -f $valor, $p.Name)
        }
    }
    [IO.File]::WriteAllText((Join-Path $DataDir 'fx.json'), ($fxObj | ConvertTo-Json -Depth 10))
} catch { Write-Log ("  FX failed, kept previous: {0}" -f $_.Exception.Message) }

Write-Log "Build"
# ZapData: el log del webhook trae el anuncio exacto (source_id) de las ventas anteriores al parche; el build lo casa por CTWA_CLID.
# Si falla no se corta nada: las ventas siguen con el ID que ya trae la hoja.
try {
    Write-Log 'ZapData log (anuncio exacto de ventas anteriores al parche)'
    & (Join-Path $Root 'scripts/Fetch-ZapDataLog.ps1') -DataDir $DataDir
} catch { Write-Log ('  Fetch-ZapDataLog fallo, se sigue sin el: ' + $_.Exception.Message) }
# /oficina: lo que hicieron hoy el webhook de ZapData y el reparador (conteos de sus pestanas 'zapdata log' y 'reparaciones'; solo lee). Si falla, la oficina dice que no hay dato.
try {
    Write-Log 'Bots de ventas para /oficina'
    if (Get-Command node -ErrorAction SilentlyContinue) { & node (Join-Path $Root 'scripts/oficina-bots-ventas.cjs') --data $DataDir }
    else { Write-Log '  sin Node: la oficina sigue sin el dato de los bots de ventas' }
} catch { Write-Log ('  oficina-bots-ventas fallo, se sigue sin el: ' + $_.Exception.Message) }
& (Join-Path $Root 'scripts/build-dashboard.ps1') -DataDir $DataDir -OutHtml (Join-Path $Root 'dashboard-ventas.html') -SnapshotPath (Join-Path $DataDir 'snapshot.json') | Out-Null

# Deploy is handled by the CI workflow (wrangler -> Cloudflare Pages), which runs this
# script with -SkipDeploy and then deploys. A local run just builds; there's no local
# deploy path anymore (wrangler needs Node, which this Windows box doesn't have).
Write-Log "Deploy: handled by CI (Cloudflare Pages via wrangler); local run builds only"

Write-Log "Headless refresh done"
