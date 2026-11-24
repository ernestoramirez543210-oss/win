# Direct Facebook Graph API fetch for Mexico + Colombia ad spend/status.
# Replaces the Windsor MCP path for these two countries (Venezuela still goes
# through Windsor for now). Uses never-expiring System User tokens stored in
# data\facebook-tokens.json.
#
# Output: data\facebook-graph.json, shaped as { "result": [ ... ] } with the SAME
# field names Windsor produced (date, account_id, account_name, campaign,
# campaign_id, campaign_effective_status, ad_id, ad_name, effective_status,
# clicks, spend) so build-dashboard.ps1 can consume it unchanged. Spend is left
# in each account's native currency (USD for Mexico, COP for Colombia) exactly
# like Windsor did — build-dashboard.ps1 handles the COP->USD divisor.
#
# Unlike the Windsor MCP (only reachable inside a live agent turn), this script is
# a plain HTTP client and can run headless from a scheduled task.
param(
    [string]$DataDir   = (Join-Path (Split-Path $PSScriptRoot) 'data'),
    [string]$TokenFile = (Join-Path (Split-Path $PSScriptRoot) 'data/facebook-tokens.json'),
    [string]$OutFile   = (Join-Path (Split-Path $PSScriptRoot) 'data/facebook-graph.json'),
    [string]$Since     = "2026-04-21",
    [string]$Until     = "",
    # Cuantos dias se vuelven a pedir en cada corrida. El resto del historico se conserva
    # del archivo anterior sin volver a bajarlo: el gasto de un dia cerrado no cambia.
    # Gasto, clics e impresiones quedan firmes en 24-48 horas, pero las COMPRAS que Meta
    # atribuye a cada anuncio (ver purchases/purchase_value) se siguen moviendo hasta una
    # semana -la ventana de atribucion-, asi que se piden 7 dias y no 3: con 3, una compra
    # que Meta reasigna al cuarto dia se quedaba para siempre en el anuncio equivocado.
    # 0 = pedir el historico completo desde $Since (arranque en frio, o reconstruccion).
    [int]$WindowDays   = 7,
    # v21.0 sale de la plataforma el 21 de enero de 2027 y las versiones de Marketing API
    # anteriores a v24.0 ya estan marcadas como obsoletas. v25.0 es la que Meta recomienda
    # como destino de migracion. Verificado contra la API real antes de cambiarlo: los
    # campos que usa este script -insights de gasto y el arbol campaign{}/adset{}- vuelven
    # identicos en v21.0 y v25.0, mismos nombres y mismos valores.
    [string]$ApiVersion = "v25.0",
    # Solo para pruebas: otra direccion en vez de graph.facebook.com (un Graph falso local,
    # ver scripts/probar-gasto-perdido.mjs). Vacio = Facebook de verdad.
    [string]$GraphBase = ""
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ([string]::IsNullOrWhiteSpace($Until)) { $Until = (Get-Date).ToString('yyyy-MM-dd') }

# -GraphBase solo puede apuntar a esta maquina: con otra direccion, los tokens de los ocho
# paises saldrian hacia ese servidor (en la URL y en el cuerpo de la prueba de escritura).
if ($GraphBase -and $GraphBase -notmatch '^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)') {
    throw "-GraphBase solo acepta http://127.0.0.1, http://localhost o http://[::1] (es para pruebas)."
}
$graphBase = if ($GraphBase) { $GraphBase.TrimEnd('/') } else { "https://graph.facebook.com/$ApiVersion" }

# ---------------------------------------------------------------------------
# Paged GET — follows paging.next until exhausted, returns all data rows.
#
# Cada pagina se reintenta sola, hasta 4 veces, antes de rendirse. Sin esto UNA sola
# pagina que falla tumba la cuenta entera: la excepcion sale de aqui, pasa por
# Get-AccountRows y llega al catch de la cuenta en el bucle principal, que la marca
# SIN CONEXION y la congela con el gasto de la corrida anterior. Eso fue lo que le paso
# a Colombia act_9999000000000034 -la cuenta que mas gasta del pais-: Facebook contesta
# "Please reduce the amount of data you're asking for, then retry your request" en la
# pagina de /ads con sus tres niveles anidados (creative{}/campaign{}/adset{}) porque la
# cuenta tiene demasiados anuncios para traerlos de 500 en 500, y esa cuenta se quedaba
# congelada corrida tras corrida sin que el gasto de Colombia volviera a moverse.
#
# Ese error especifico no se arregla reintentando igual: hay que pedir MENOS por
# pagina. Se detecta por el texto -el codigo de error que manda Facebook para esto no es
# estable entre cuentas- y se achica el limit a la mitad (piso 25) antes de reintentar la
# MISMA pagina. El cursor de "paging.next" no se pierde: una pagina mas chica solo
# reparte la misma ventana en mas paginas.
#
# Y si de verdad no hay forma -token vencido, cuenta sin permiso- y ya se habian leido
# paginas anteriores, se devuelve lo que si se pudo leer en vez de tirarlo todo: faltan
# filas, pero menos gasto real es mejor que cero gasto por una pagina que fallo al final.
# Solo se relanza el error -y se deja que la cuenta se congele, como red de seguridad
# final- cuando la PRIMERA pagina falla y no hay nada que devolver.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Un error de Facebook que repetir NO arregla.
#
# El refresco tardaba 8 minutos y casi 3 eran esperas inutiles: Argentina y Ecuador
# contestan 400 "API access blocked" en cada cuenta, y cada una se reintentaba tres veces
# para la moneda y cuatro para los anuncios, con pausas de 2, 4 y 6 segundos, para
# terminar igual. Nueve cuentas por ~18 s cada una.
#
# Un 4xx que no es de limite de velocidad -token sin acceso, app bloqueada, permiso
# negado- es una respuesta, no un tropiezo: se corta en el primer intento y la cuenta
# sigue el camino de siempre (queda como "sin conexion" y se conservan sus filas viejas).
# Los errores temporales de Facebook -is_transient, y los codigos 1, 2, 4, 17, 32, 341
# y 613, que son "algo fallo, o vas muy rapido"- siguen reintentandose como antes.
# ---------------------------------------------------------------------------
function Test-ErrorPermanente($err) {
    $status = 0
    try { $status = [int]$err.Exception.Response.StatusCode } catch { }
    $codigo = 0
    $transitorio = $false
    try {
        $cuerpo = $err.ErrorDetails.Message
        if ($cuerpo) {
            $je = $cuerpo | ConvertFrom-Json
            $codigo = [int]$je.error.code
            $transitorio = [bool]$je.error.is_transient
        }
    } catch { }
    if ($transitorio) { return $false }
    if (@(1, 2, 4, 17, 32, 341, 613) -contains $codigo) { return $false }
    return ($status -ge 400 -and $status -lt 500 -and $status -ne 408 -and $status -ne 429)
}

function Invoke-GraphPaged([string]$url) {
    $all = New-Object System.Collections.Generic.List[object]
    $next = $url
    $guard = 0
    while ($next -and $guard -lt 1000) {
        $guard++
        $urlPagina = $next
        $resp = $null
        for ($intento = 1; $intento -le 4 -and -not $resp; $intento++) {
            try {
                $resp = Invoke-RestMethod -Uri $urlPagina -Method Get -TimeoutSec 120
            } catch {
                $errActual = $_
                $msg = $_.Exception.Message
                try {
                    $cuerpo = $_.ErrorDetails.Message
                    if ($cuerpo) { $je = $cuerpo | ConvertFrom-Json; if ($je.error.message) { $msg = [string]$je.error.message } }
                } catch { }
                if ($msg -match 'reduce the amount of data') {
                    $m = [regex]::Match($urlPagina, 'limit=(\d+)')
                    if ($m.Success) {
                        $limActual = [int]$m.Groups[1].Value
                        $limNuevo = [Math]::Max(25, [int]($limActual / 2))
                        if ($limNuevo -lt $limActual) {
                            $urlPagina = $urlPagina -replace 'limit=\d+', "limit=$limNuevo"
                            Write-Warning "Facebook pide menos datos por pagina (pagina $guard): limit $limActual -> $limNuevo, reintento."
                            Start-Sleep -Seconds 1
                            continue
                        }
                    }
                }
                if ($intento -ge 4 -or (Test-ErrorPermanente $errActual)) {
                    if ($all.Count -gt 0) {
                        Write-Warning "Pagina $guard fallo tras $intento intentos ($msg): se devuelven las $($all.Count) filas ya leidas de $($url -replace 'access_token=[^&]*','access_token=***')."
                        return $all
                    }
                    throw
                }
                Start-Sleep -Seconds (2 * $intento)
            }
        }
        if ($resp.data) { foreach ($d in $resp.data) { $all.Add($d) } }
        if ($resp.paging -and $resp.paging.next) { $next = $resp.paging.next } else { $next = $null }
    }
    # El tope existe para que un paging roto no gire para siempre, pero alcanzarlo
    # significa que faltan filas. Sin este aviso, faltarian calladas: el tablero se
    # construye igual, con menos gasto del que hubo, y nada en pantalla lo delata.
    if ($next) {
        Write-Warning "Paginacion cortada en $guard paginas ($($all.Count) filas): faltan datos de $($url -replace 'access_token=[^&]*','access_token=***')"
    }
    return $all
}

# ---------------------------------------------------------------------------
# El valor de una clase de accion dentro del array 'actions' de una fila de insights.
#
# Devuelve 0 y no $null cuando la clase no viene: una campana de trafico no genera
# conversaciones y su fila no trae la clase, que no es lo mismo que un fallo. Un cero se
# suma sin romper nada; un $null obliga a mirar antes de cada suma.
function Get-Accion($acciones, [string]$clase) {
    if (-not $acciones) { return 0 }
    foreach ($a in @($acciones)) {
        if ([string]$a.action_type -eq $clase) { return [double]$a.value }
    }
    return 0
}

# Per account: daily ad-level insights (spend/clicks) joined with current status.
# ---------------------------------------------------------------------------
# Currencies Facebook reports without a minor unit: their budget values are already the
# amount, not cents. COP is the one that bites here - Colombia's accounts bill in it.
$CERO_DECIMALES = @('COP','CLP','ISK','JPY','KRW','PYG','VND','IDR','HUF','TWD')

# -Desde permite pedirle a UNA cuenta un rango distinto al de las demas.
#
# Hace falta porque la ventana incremental se calcula una sola vez, desde el ultimo dia que
# ya hay en el archivo, y se aplicaba igual a todas. Una cuenta que aparece hoy -porque se
# compartio, o porque se agrego un token- recibia esa misma ventana de tres dias y nacia sin
# historia: su gasto anterior no se pedia nunca y quedaba perdido, porque manana la ventana
# de la cuenta nueva de Mexico.
function Get-AccountRows([string]$accountId, [string]$token, [string]$Desde = '') {
    # The divisor depends on the account's currency, so ask for it first.
    #
    # Un fallo aqui no es cosmetico. Suponer USD para una cuenta en COP divide los
    # documenta el comentario del divisor, mas abajo. El "catch" vacio que habia aqui lo
    # revivia en cualquier timeout, y en silencio. Se reintenta; si aun asi no se sabe la
    # moneda, se avisa y los presupuestos quedan en cero, que en pantalla es un guion.
    # Un guion dice "no se"; un numero cien veces menor dice una mentira convincente.
    # El GASTO no depende de esto: viene en la moneda de la cuenta y lo convierte el build.
    # En la misma llamada viene la salud de la cuenta, que no cuesta una peticion mas.
    #
    # Hace falta porque un anuncio de una cuenta deshabilitada sigue diciendo ACTIVE: Mexico
    # mostraba 13 anuncios activos con la cuenta caida por politica desde el 14/08, y Peru
    # 35 con dos cuentas deshabilitadas y una impaga. El estado del anuncio no sabe nada de
    # eso; hay que preguntarle a la cuenta.
    $monedaCuenta = $null
    $saludCuenta = $null
    for ($intento = 1; $intento -le 3 -and -not $monedaCuenta; $intento++) {
        try {
            $acc = Invoke-RestMethod "$graphBase/act_$accountId`?fields=currency,name,account_status,disable_reason&access_token=$token" -TimeoutSec 30
            if ($acc.currency) { $monedaCuenta = [string]$acc.currency }
            $saludCuenta = [ordered]@{
                nombre = [string]$acc.name
                estado = [int]$acc.account_status
                motivo = [int]$acc.disable_reason
            }
        } catch {
            # Un 4xx que no es de velocidad no se arregla esperando (ver Test-ErrorPermanente).
            if (Test-ErrorPermanente $_) { break }
            if ($intento -lt 3) { Start-Sleep -Seconds (2 * $intento) }
        }
    }
    # Write-Warning y no Write-Output: en una funcion de PowerShell el flujo de salida ES
    # el valor de retorno, y una linea de aviso entraria como una fila de gasto mas.
    if (-not $monedaCuenta) {
        Write-Warning "act_$accountId no respondio su moneda en 3 intentos: presupuestos en cero para esta cuenta (el gasto no se ve afectado)."
    }
    # 1. Current status per ad (all ads, incl. zero-spend) — one row per ad_id.
    # adset comes along because a CBO campaign spreads one budget across its ad sets:
    # per-ad ROAS says little there, and the campaign/ad-set totals are what decide.
    # creative.effective_object_story_id (formato "PAGEID_POSTID") deja cruzar las ventas
    # de ZapData: su webhook no conoce el ad_id, solo el POST detras del anuncio (lo saca
    # siguiendo el link corto hasta la URL de Facebook). build-dashboard.ps1 arma con esto
    # una tabla POSTID -> ad_id y traduce esas ventas antes de cruzarlas con el gasto.
    # instagram_permalink_url tambien hace falta: un anuncio con colocacion en Instagram
    # tiene un post DISTINTO por cada red -effective_object_story_id para Facebook,
    # instagram_permalink_url para Instagram-, y WhatsApp manda el de la red por la que la
    # persona de verdad hizo clic. Un clic en Instagram nunca va a calzar contra el post de
    # Facebook, y viceversa.
    # creative.video_id es la tercera puerta, y la que mas ventas movia: un clic hecho desde
    # un REEL trae el ID del VIDEO -en un reel el id de la publicacion ES el del video-, que
    # no es ni el effective_object_story_id ni el permalink de Instagram del anuncio. En
    $statusFields = 'id,effective_status,creative{effective_object_story_id,instagram_permalink_url,video_id},campaign{id,name,effective_status,objective,daily_budget,lifetime_budget},adset{id,name,effective_status,daily_budget,lifetime_budget}'
    $statusUrl = "$graphBase/act_$accountId/ads?fields=$statusFields&limit=250&access_token=$token"
    $ads = Invoke-GraphPaged $statusUrl

    # Facebook returns budgets in the account currency's smallest unit, and how many of
    # those make one unit depends on the currency. USD and PEN have cents, so 5000 means
    $divisor = if (-not $monedaCuenta) { 0 } elseif ($CERO_DECIMALES -contains $monedaCuenta) { 1 } else { 100 }
    function Presupuesto($obj, [int]$div) {
        # div = 0 significa "no sabemos la moneda", no "dividir por cero".
        if (-not $obj -or $div -le 0) { return @{ diario = 0.0; total = 0.0 } }
        @{
            diario = if ($obj.daily_budget)    { [double]$obj.daily_budget / $div }    else { 0.0 }
            total  = if ($obj.lifetime_budget) { [double]$obj.lifetime_budget / $div } else { 0.0 }
        }
    }

    $statusByAd = @{}
    foreach ($a in $ads) {
        # A campaign carrying its own budget is CBO: the ad sets share one pot, which is
        # why judging its ads individually misleads.
        $esCbo = $false
        if ($a.campaign -and ($a.campaign.daily_budget -or $a.campaign.lifetime_budget)) { $esCbo = $true }
        $pCamp = Presupuesto $a.campaign $divisor
        $pSet  = Presupuesto $a.adset    $divisor
        $statusByAd[[string]$a.id] = [ordered]@{
            effectiveStatus         = [string]$a.effective_status
            objectStoryId           = if ($a.creative) { [string]$a.creative.effective_object_story_id } else { '' }
            instagramPermalink      = if ($a.creative) { [string]$a.creative.instagram_permalink_url } else { '' }
            videoId                 = if ($a.creative) { [string]$a.creative.video_id } else { '' }
            campaignId              = if ($a.campaign) { [string]$a.campaign.id } else { '' }
            campaignName            = if ($a.campaign) { [string]$a.campaign.name } else { '' }
            campaignEffectiveStatus = if ($a.campaign) { [string]$a.campaign.effective_status } else { '' }
            campaignObjective       = if ($a.campaign) { [string]$a.campaign.objective } else { '' }
            isCbo                   = $esCbo
            campaignBudgetDaily     = $pCamp.diario
            campaignBudgetLifetime  = $pCamp.total
            adsetId                 = if ($a.adset) { [string]$a.adset.id } else { '' }
            adsetName               = if ($a.adset) { [string]$a.adset.name } else { '' }
            adsetEffectiveStatus    = if ($a.adset) { [string]$a.adset.effective_status } else { '' }
            adsetBudgetDaily        = $pSet.diario
            adsetBudgetLifetime     = $pSet.total
        }
    }

    # 2. Daily spend rows (only ads with activity in the window).
    # 'actions' trae el embudo de mensajeria, que es el hueco que habia entre el gasto y la
    # venta: cuantas conversaciones arrancaron, cuantas se contestaron y hasta donde
    # llegaron. Sin esto, un ROAS bajo no dice si el anuncio no trae gente o si la trae y no
    # se cierra, que se arreglan de forma muy distinta.
    # action_values trae el VALOR de cada clase de accion (la plata de las compras).
    $insFields = 'account_id,account_name,campaign_name,campaign_id,adset_id,adset_name,ad_id,ad_name,clicks,impressions,spend,actions,action_values'
    # La ventana movil, salvo que quien llama pida otra cosa para esta cuenta en concreto.
    $desdeEsta = if ($Desde) { $Desde } else { $sinceEff }
    $timeRange = '{"since":"' + $desdeEsta + '","until":"' + $Until + '"}'
    $trEnc = [Uri]::EscapeDataString($timeRange)
    $insUrl = "$graphBase/act_$accountId/insights?level=ad&time_increment=1&fields=$insFields&time_range=$trEnc&limit=250&access_token=$token"
    $insights = Invoke-GraphPaged $insUrl

    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($i in $insights) {
        $adId = [string]$i.ad_id
        $st = $statusByAd[$adId]
        # Prefer the campaign name/id from insights (present on the spend row);
        # fall back to the status edge; status values only exist on the edge.
        $campaignName = if ($i.campaign_name) { [string]$i.campaign_name } elseif ($st) { $st.campaignName } else { '' }
        $campaignId   = if ($i.campaign_id) { [string]$i.campaign_id } elseif ($st) { $st.campaignId } else { '' }
        $effStatus    = if ($st) { $st.effectiveStatus } else { '' }
        $campEffStatus = if ($st) { $st.campaignEffectiveStatus } else { '' }

        $rows.Add([ordered]@{
            date                      = [string]$i.date_start
            account_id                = [string]$i.account_id
            account_name              = [string]$i.account_name
            # Per row, because a country can mix currencies: Argentina runs one ARS
            # account beside two USD ones, and converting them all at one country-wide
            # rate would be wrong for whichever half doesn't match.
            account_currency          = $monedaCuenta
            campaign                  = $campaignName
            campaign_id               = $campaignId
            campaign_effective_status = $campEffStatus
            campaign_objective        = if ($st) { $st.campaignObjective } else { '' }
            is_cbo                    = if ($st) { $st.isCbo } else { $false }
            campaign_budget_daily     = if ($st) { $st.campaignBudgetDaily } else { 0.0 }
            campaign_budget_lifetime  = if ($st) { $st.campaignBudgetLifetime } else { 0.0 }
            adset_id                  = if ($i.adset_id) { [string]$i.adset_id } elseif ($st) { $st.adsetId } else { '' }
            adset_name                = if ($i.adset_name) { [string]$i.adset_name } elseif ($st) { $st.adsetName } else { '' }
            adset_effective_status    = if ($st) { $st.adsetEffectiveStatus } else { '' }
            adset_budget_daily        = if ($st) { $st.adsetBudgetDaily } else { 0.0 }
            adset_budget_lifetime     = if ($st) { $st.adsetBudgetLifetime } else { 0.0 }
            ad_id                     = $adId
            object_story_id           = if ($st) { $st.objectStoryId } else { '' }
            instagram_permalink_url   = if ($st) { $st.instagramPermalink } else { '' }
            video_id                  = if ($st) { $st.videoId } else { '' }
            ad_name                   = [string]$i.ad_name
            effective_status          = $effStatus
            clicks                    = [string]$i.clicks
            impressions               = [string]$i.impressions
            spend                     = [string]$i.spend
            # Solo los cuatro numeros del embudo, no el array entero: 'actions' devuelve
            # unas treinta clases por fila -reacciones, guardados, vistas de video- y
            # guardarlas todas multiplicaria por diez un archivo que ya pesa. Las clases
            # salieron de sondear las cuentas de verdad, no del manual: los nombres que no
            # devuelve nadie no sirven de nada.
            conv                      = (Get-Accion $i.actions 'onsite_conversion.messaging_conversation_started_7d')
            conv_resp                 = (Get-Accion $i.actions 'onsite_conversion.messaging_first_reply')
            conv_d2                   = (Get-Accion $i.actions 'onsite_conversion.messaging_user_depth_2_message_send')
            conv_d3                   = (Get-Accion $i.actions 'onsite_conversion.messaging_user_depth_3_message_send')
            # Las compras que Meta atribuye a ESTE anuncio, por dia. ZapData manda el evento
            # de compra con el ctwa_clid del clic de WhatsApp, y Meta lo asigna al anuncio
            # exacto: es la unica fuente que dice de que anuncio salio una venta de ZapData
            # sin adivinar por links ni por video. omni_purchase suma la compra por
            # mensajeria y la del pixel; comparado contra la hoja de Colombia (4-17 sep) dio
            purchases                 = (Get-Accion $i.actions 'omni_purchase')
            purchase_value            = (Get-Accion $i.action_values 'omni_purchase')
        })
    }
    # statusByAd sale tambien: /ads no esta acotado por fecha, asi que trae el estado de
    # HOY de todos los anuncios de la cuenta, incluidos los que no gastaron en la ventana.
    # Con eso se repintan las filas viejas que se conservan (ver Actualizar-Estado).
    return @{ rows = $rows; adCount = $ads.Count; spendRows = $rows.Count; status = $statusByAd; salud = $saludCuenta }
}

# ---------------------------------------------------------------------------
# Scheduled campaigns: configured to run (status ACTIVE) but whose start_time is
# still in the future — Facebook's "Programado" delivery state. These never appear
# in /insights (they haven't spent) so they must be pulled from the /campaigns edge
# separately. start_time comes back with a timezone offset (e.g. -0500); compare as
# an absolute instant against "now" so a campaign starting later today still counts.
# ---------------------------------------------------------------------------
function Get-ScheduledCampaigns([string]$accountId, [string]$token, [datetime]$nowUtc) {
    $fields = 'name,status,effective_status,start_time,stop_time'
    $url = "$graphBase/act_$accountId/campaigns?fields=$fields&limit=200&access_token=$token"
    $camps = Invoke-GraphPaged $url
    $scheduled = New-Object System.Collections.Generic.List[object]
    foreach ($c in $camps) {
        if ([string]$c.status -ne 'ACTIVE') { continue }   # paused/archived aren't "scheduled"
        $startRaw = [string]$c.start_time
        if ([string]::IsNullOrWhiteSpace($startRaw)) { continue }
        $start = [datetime]::MinValue
        if (-not [datetime]::TryParse($startRaw, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal, [ref]$start)) { continue }
        if ($start -le $nowUtc) { continue }               # already started = running, not scheduled
        $scheduled.Add([ordered]@{
            account_id       = $accountId
            campaign         = [string]$c.name
            status           = [string]$c.status
            effective_status = [string]$c.effective_status
            start_time       = $startRaw
            stop_time        = [string]$c.stop_time
        })
    }
    return $scheduled
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Output "Facebook Graph API pull: $Since .. $Until"
$tokens = Get-Content $TokenFile -Raw | ConvertFrom-Json

$nowUtc = (Get-Date).ToUniversalTime()
$allRows = New-Object System.Collections.Generic.List[object]

# Una fila vieja que se conserva trae el estado que el anuncio tenia ENTONCES. Si no se
# repinta, un anuncio pausado ayer -que por eso no aparece en el pull de hoy- sigue
# figurando ACTIVE para siempre, y el tablero lo marcaria como candidato a pausar algo
# que ya esta pausado. El gasto y las fechas de la fila no se tocan: eso es historia.
function Actualizar-Estado($fila, $mapaEstados) {
    $o = [ordered]@{}
    foreach ($p in $fila.PSObject.Properties) { $o[$p.Name] = $p.Value }
    $st = $null
    $adId = [string]$fila.ad_id
    if ($adId -and $mapaEstados -and $mapaEstados.ContainsKey($adId)) { $st = $mapaEstados[$adId] }
    # Sin estado fresco -anuncio borrado, o cuenta que hoy no se pudo leer- se deja la
    # fila como estaba: inventar un estado seria peor que mostrar el ultimo conocido.
    if (-not $st) { return $o }
    $o['campaign_effective_status'] = $st.campaignEffectiveStatus
    $o['campaign_objective']        = $st.campaignObjective
    $o['is_cbo']                    = $st.isCbo
    $o['campaign_budget_daily']     = $st.campaignBudgetDaily
    $o['campaign_budget_lifetime']  = $st.campaignBudgetLifetime
    $o['adset_effective_status']    = $st.adsetEffectiveStatus
    $o['adset_budget_daily']        = $st.adsetBudgetDaily
    $o['adset_budget_lifetime']     = $st.adsetBudgetLifetime
    $o['effective_status']          = $st.effectiveStatus
    $o['object_story_id']           = $st.objectStoryId
    $o['instagram_permalink_url']   = $st.instagramPermalink
    $o['video_id']                  = $st.videoId
    return $o
}

# ---------------------------------------------------------------------------
# Un dia que ya tenia gasto no puede volver a cero.
#
# El 2026-10-21 a las 12:02 UTC Facebook contesto en CERO el gasto, las impresiones y los
# clics -con las conversaciones intactas- de toda la ventana (14 al 20 de octubre) de cuatro
# cuentas a la vez: Mexico act_9999000000000004, Guatemala act_999900000000013, Argentina
# act_9999000000000016 y Venezuela act_9999000000000046. Las corridas siguientes volvieron a
# pedir del 15 al 20 y se corrigieron solas, pero esa era la ULTIMA vez que el 14 entraba en la
# el ROAS de ese dia inflado en los cuatro paises. La API, semanas despues, volvia a dar el
# gasto de siempre (71,20 / 16,08 / 33,66 / 34,02).
#
# El gasto de un dia que ya se cobro no se evapora. Si el pull nuevo trae casi nada (1 % o
# menos) para un dia en el que la corrida anterior ya tenia gasto de esa cuenta, lo que esta
# mal es la respuesta, no la historia: se conservan las filas anteriores de ese dia y se
# avisa. Mientras el dia siga dentro de la ventana se vuelve a pedir en cada corrida, asi que
# en cuanto Facebook conteste bien, gana lo nuevo como siempre. Una correccion normal de
# Facebook (unos centavos o unos dolares menos) no llega ni cerca del 1 % y pasa igual que
# antes.
#
# Devuelve fecha -> @{ antes; ahora } con los dias que hay que proteger. Solo mira los dias
# desde $desde: lo anterior se conserva del archivo de todas formas.
# ---------------------------------------------------------------------------
function ConvertTo-Gasto($v) {
    # Cultura invariante a proposito: en un PC en espanol, [double]::TryParse sin cultura lee
    # "33.66" como 3366.
    $x = 0.0
    if (-not [double]::TryParse([string]$v, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$x)) { return 0.0 }
    return $x
}
function Get-DiasGastoPerdido($filasNuevas, $filasPrevias, [string]$desde) {
    $antes = @{}
    $ahora = @{}
    # Sin @(...) a proposito: en PowerShell 5.1, @() sobre una List[object] recibida como
    # parametro lanza "Argument types do not match" y la cuenta entera caia como SIN CONEXION.
    # foreach sobre $null no da vueltas, asi que tampoco hace falta.
    foreach ($r in $filasPrevias) {
        $d = [string]$r.date
        if (-not $d -or $d -lt $desde) { continue }
        $antes[$d] = [double]$antes[$d] + (ConvertTo-Gasto $r.spend)
    }
    foreach ($r in $filasNuevas) {
        $d = [string]$r.date
        if (-not $d) { continue }
        $ahora[$d] = [double]$ahora[$d] + (ConvertTo-Gasto $r.spend)
    }
    $perdidos = @{}
    foreach ($d in @($antes.Keys)) {
        $a = [double]$antes[$d]
        $n = [double]$ahora[$d]
        if ($a -gt 0 -and $n -le $a * 0.01) { $perdidos[$d] = @{ antes = $a; ahora = $n } }
    }
    return $perdidos
}
# Lo que la guarda protegio en esta corrida, para poder auditarlo en el archivo.
$gastoConservado = New-Object System.Collections.Generic.List[object]
$allScheduled = New-Object System.Collections.Generic.List[object]
# cuenta -> $true escribe, $false solo lectura, $null no se pudo determinar
$permisos = @{}
$motivosPermiso = @{}
# Salud de cada cuenta publicitaria: nombre, estado y motivo de bloqueo. Un anuncio de una
# cuenta caida sigue diciendo ACTIVE, asi que sin esto el tablero cuenta como activos
# anuncios que no pueden gastar.
$saludCuentas = @{}
# Lo que trajo la corrida anterior, por cuenta. Si una cuenta falla hoy, se conservan sus
# filas en vez de dejar el pais en cero: un gasto viejo y marcado como viejo es mucho menos
# danino que un gasto de cero, que convierte el ROAS en infinito.
$previas = @{}
# A que cuentas ya se les pidio el historico completo alguna vez.
#
# "Tiene filas previas" no sirve como prueba: la cuenta nueva de Mexico tenia filas del 18 y
# del 19 -las que alcanzo a ver la ventana movil el dia que se agrego- y aun asi le faltaban
# las del 17, que ya habian quedado fuera. Con un registro explicito, cada cuenta recibe un
# pull completo exactamente una vez y despues entra en la ventana incremental para siempre.
# El dia que se agregue una cuenta con seis meses de gasto, entra entera.
$historicoCompleto = @{}
$congeladas = @{}
$ultimaFechaPrevia = ''
# Lo mismo, pero por CUENTA. El maximo global no sirve para decidir la ventana de una
# cuenta concreta: mientras Mexico y Guatemala reportan al dia, el maximo global es hoy, y
# una cuenta que lleva una semana muda recibiria una ventana de tres dias que no alcanza su
# hueco. Paso el 2026-08-30: cuatro tokens bloqueados, y cuando volvieran, el gasto de los
# dias perdidos no se habria pedido nunca.
$ultimaPorCuenta = @{}
$permisosPrevios = @{}
$permisoVencido = $true
if (Test-Path $OutFile) {
    try {
        $ant = Get-Content $OutFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($ant.writeAccess) {
            foreach ($p in $ant.writeAccess.PSObject.Properties) { $permisosPrevios[$p.Name] = $p.Value }
        }
        foreach ($k in @($ant.historicoCompleto)) { if ($k) { $historicoCompleto[[string]$k] = $true } }
        if ($ant.fetchedAt) {
            $t = [datetime]::MinValue
            if ([datetime]::TryParse([string]$ant.fetchedAt, [ref]$t)) {
                $permisoVencido = ((Get-Date) - $t).TotalHours -ge 12
            }
        }
        foreach ($r in @($ant.result)) {
            $k = [string]$r.account_id
            if (-not $k) { continue }
            if (-not $previas.ContainsKey($k)) { $previas[$k] = New-Object System.Collections.Generic.List[object] }
            $previas[$k].Add($r)
            $d = [string]$r.date
            if ($d -and $d -gt $ultimaFechaPrevia) { $ultimaFechaPrevia = $d }
            if ($d -and (-not $ultimaPorCuenta.ContainsKey($k) -or $d -gt $ultimaPorCuenta[$k])) { $ultimaPorCuenta[$k] = $d }
        }
    } catch { }
}

# La ventana arranca $WindowDays antes del ultimo dia que ya tenemos, no antes de HOY.
# Asi, si el pipeline estuvo caido tres dias, la ventana se estira sola para tapar el
# hueco en vez de dejarlo abierto para siempre. Sin archivo previo se baja todo.
$sinceEff = $Since
if ($WindowDays -gt 0 -and $ultimaFechaPrevia) {
    $ancla = [datetime]::ParseExact($ultimaFechaPrevia, 'yyyy-MM-dd', $null)
    $hoy   = [datetime]::ParseExact($Until, 'yyyy-MM-dd', $null)
    if ($ancla -gt $hoy) { $ancla = $hoy }
    $cand = $ancla.AddDays(-1 * ($WindowDays - 1)).ToString('yyyy-MM-dd')
    if ($cand -gt $Since) { $sinceEff = $cand }
}
if ($sinceEff -ne $Since) {
    Write-Output "Ventana incremental: se piden $sinceEff .. $Until; el historico desde $Since se conserva del archivo anterior."
} else {
    Write-Output "Pull completo: $Since .. $Until (sin archivo previo, o WindowDays=0)."
}
# A country can hold several System User tokens - one per Business Manager - because a
# Puede este token PAUSAR en esta cuenta, o solo leerla?
#
# El scope ads_management no basta: el usuario del sistema tambien necesita el permiso
# "Administrar campañas" sobre la cuenta, y con "Ver rendimiento" Facebook contesta
# "Permissions error" (subcode 1815694). El dashboard mostraba el interruptor igual, asi
# que un pausado fallido parecia aplicado. Se comprueba aqui, una vez por cuenta, y el
# frontend apaga el interruptor donde no va a funcionar.
#
# La prueba es un no-op: se le reescribe a un ad el estado que YA tiene.
# Reescribe el estado que el anuncio ya tiene -no cambia nada- y mira si Facebook deja.
#
# Lo que importa es POR QUE falla, no que falle. Marcaba Mexico como solo lectura teniendo
# el permiso MANAGE concedido: el 400 no venia del permiso sino del anuncio, un
# clic-a-WhatsApp con el numero desconectado ("Se requiere un numero de WhatsApp"). Un
# error de contenido significa que Facebook ya paso el control de permisos y llego a
# validar el anuncio, o sea que el token SI puede escribir.
#
# Se pide con suppress_http_code=1 para que el error venga en el cuerpo con un 200: en
# PowerShell 5.1 el cuerpo de un 400 se pierde, y sin el mensaje todo error se veia igual.
# Devuelve @{ puede = $true/$false/$null; motivo = 'permiso'|'anunciar'|'cuenta'|'token'|'' }.
#
# El MOTIVO importa tanto como el si o el no, porque cada uno se arregla en un sitio distinto
# y el tablero mandaba a todos al mismo: "dale Administrar campañas" no sirve de nada si la
# cuenta esta deshabilitada por politica — ahi no hay permiso que conceder.
#
# Los tres subcodigos se leyeron de error_user_msg preguntandole a las cuentas reales, no de
# la documentacion. Vienen en el idioma de la cuenta -los de Argentina llegan en turco- asi
# que el subcodigo es lo unico estable con lo que decidir:
#   1815694  "El usuario no tiene permiso para realizar esta accion".
#   2490591  "para crear o editar anuncios necesitas activar el permiso Anunciar". Es un
#            permiso distinto de "Administrar campañas", y decirle al usuario que conceda el
#            equivocado lo deja mirando un menu donde ya esta todo bien.
#   2490592  "el estado de la cuenta publicitaria no permite administrar anuncios". Aqui no
#            hay permiso que dar: la cuenta esta inhabilitada o impaga y se resuelve en Meta.
# El subcodigo NO sigue a la salud de la cuenta: Colombia act_999900000000030 esta impaga y
# aun asi contesta 1815694, o sea que su problema es de permiso y no de estado.
function Test-PuedeEscribir([string]$accountId, [string]$token) {
    $no = { param($m) @{ puede = $false; motivo = $m } }
    try {
        $d = Invoke-RestMethod "$graphBase/act_$accountId/ads?fields=id,status&limit=1&access_token=$token" -TimeoutSec 30
        $ad = @($d.data)[0]
        if (-not $ad) { return @{ puede = $null; motivo = '' } }   # sin ads: no se puede saber
        $st = [string]$ad.status
        if ($st -ne 'ACTIVE' -and $st -ne 'PAUSED') { return @{ puede = $null; motivo = '' } }
        $r = Invoke-RestMethod -Method Post -Uri "$graphBase/$($ad.id)?suppress_http_code=1" `
                -Body @{ status = $st; access_token = $token } -TimeoutSec 30
        if (-not $r.error) { return @{ puede = $true; motivo = '' } }
        # Codigos de permiso o de sesion: esos si son un no de verdad.
        #   200 = sin permiso sobre el objeto   10 = permiso no concedido a la app
        #   190/102/104 = token invalido, caducado o ausente
        $codigo = [int]$r.error.code
        $sub    = [int]$r.error.error_subcode
        if ($codigo -in @(200, 10, 190, 102, 104)) {
            if ($sub -eq 2490592)             { return & $no 'cuenta' }
            if ($sub -eq 2490591)             { return & $no 'anunciar' }
            if ($codigo -in @(190, 102, 104)) { return & $no 'token' }
            return & $no 'permiso'
        }
        return @{ puede = $true; motivo = '' }
    } catch {
        # Un fallo de red NO es un "no puede escribir".
        #
        # Devolver $false aqui apagaba el interruptor de una cuenta sana por un tropiezo de
        # treinta segundos, y ademas se cacheaba. Venezuela act_9999000000000043 salio
        # marcada de solo lectura teniendo el permiso: se comprobo llamando a Facebook a
        # mano un minuto despues y escribia sin problema. null significa "no se pudo
        # comprobar", el tablero deja el interruptor y que Facebook decida.
        return @{ puede = $null; motivo = '' }
    }
}

# token only ever sees the accounts of its own BM. Old single-token blocks still work.
function Get-TokenBlocks($c) {
    if ($c.tokens) { return @($c.tokens) }
    if ($c.systemUserToken) { return @([pscustomobject]@{ systemUserToken = $c.systemUserToken; accounts = $c.accounts }) }
    return @()
}

# ---------------------------------------------------------------------------
# Las cuentas se bajan EN PARALELO.
#
# Este paso era el 60 % del refresco: unas treinta cuentas, cada una con tres o cuatro
# llamadas a Facebook que tardan segundos, una detras de otra. Facebook no pide ese orden:
# cada cuenta es independiente. Se hace en dos pasadas del MISMO bucle de paises:
#
#   'plan'  recorre todo igual que siempre -descubrimiento de cuentas, guarda de duplicados,
#           ventana de cada cuenta- pero en lugar de llamar a Facebook anota la tarea.
#   (aqui)  las tareas se reparten entre unos pocos hilos a la vez.
#   'real'  vuelve a recorrer el bucle y, donde antes llamaba a Facebook, usa lo ya bajado.
#           TODO lo demas -filas retenidas, salud, congeladas, mensajes- es el codigo de
#           siempre y ve lo mismo que antes, en el mismo orden.
#
# Si una cuenta no tiene resultado adelantado, o se pidio otra ventana, se baja ahi mismo como
# antes: lo peor que puede pasar es volver a tardar lo de siempre, nunca dar otro numero.
# Un error de una llamada se guarda tal cual y se vuelve a lanzar en el mismo punto del
# bucle, asi que la cuenta cae en el mismo catch con el mismo mensaje.
# ---------------------------------------------------------------------------
$HILOS_FACEBOOK = 5

function New-ErrorGuardado($e) {
    $ex = New-Object System.Exception([string]$e.msg)
    $er = New-Object System.Management.Automation.ErrorRecord($ex, 'FacebookAdelantado',
        [System.Management.Automation.ErrorCategory]::NotSpecified, $null)
    if ($e.body) { $er.ErrorDetails = New-Object System.Management.Automation.ErrorDetails([string]$e.body) }
    return $er
}

# Reparte un guion entre hilos y devuelve un resultado por tarea, EN EL MISMO ORDEN.
# $funciones: nombres de funciones de este script que el guion necesita.
function Invoke-EnParalelo($tareas, [scriptblock]$guion, [string[]]$funciones, [int]$hilos) {
    $resultados = New-Object System.Collections.Generic.List[object]
    if (-not $tareas.Count) { return ,$resultados }
    $iss = [System.Management.Automation.Runspaces.InitialSessionState]::CreateDefault()
    foreach ($n in $funciones) {
        $sb = (Get-Item "function:$n" -ErrorAction Stop).ScriptBlock
        $iss.Commands.Add((New-Object System.Management.Automation.Runspaces.SessionStateFunctionEntry($n, $sb.ToString())))
    }
    foreach ($v in 'graphBase', 'Until', 'sinceEff', 'nowUtc', 'CERO_DECIMALES') {
        $iss.Variables.Add((New-Object System.Management.Automation.Runspaces.SessionStateVariableEntry($v, (Get-Variable -Name $v -ValueOnly), '')))
    }
    $pool = [runspacefactory]::CreateRunspacePool(1, [Math]::Max(1, $hilos), $iss, $Host)
    $pool.Open()
    $lanzados = @()
    foreach ($t in $tareas) {
        $ps = [powershell]::Create()
        $ps.RunspacePool = $pool
        [void]$ps.AddScript($guion.ToString()).AddArgument($t)
        $lanzados += [pscustomobject]@{ ps = $ps; ar = $ps.BeginInvoke() }
    }
    foreach ($l in $lanzados) {
        $salida = $null
        try { $salida = @($l.ps.EndInvoke($l.ar))[0] } catch { $salida = $null }
        foreach ($w in $l.ps.Streams.Warning) { Write-Warning $w.Message }
        $resultados.Add($salida)
        $l.ps.Dispose()
    }
    $pool.Close(); $pool.Dispose()
    return ,$resultados
}

$guionCuenta = {
    param($t)
    $ErrorActionPreference = 'Stop'
    $o = @{ desde = $t.desde }
    try { $o.rows = Get-AccountRows -accountId $t.acc -token $t.token -Desde $t.desde }
    catch { $o.errRows = @{ msg = [string]$_.Exception.Message; body = [string]$_.ErrorDetails.Message } }
    if (-not $o.errRows) {
        try { $o.sched = @(Get-ScheduledCampaigns -accountId $t.acc -token $t.token -nowUtc $nowUtc) }
        catch { $o.errSched = @{ msg = [string]$_.Exception.Message; body = [string]$_.ErrorDetails.Message } }
    }
    if (-not $o.errRows -and -not $o.errSched -and $t.probar) {
        try { $o.escribir = Test-PuedeEscribir -accountId $t.acc -token $t.token } catch { }
    }
    return $o
}

$guionDescubrir = {
    param($t)
    $ErrorActionPreference = 'Stop'
    $ids = @()
    try {
        $d = Invoke-RestMethod ($t.graphBase + '/' + $t.edge + '?fields=account_id&limit=200&access_token=' + $t.token) -TimeoutSec 30
        foreach ($a in @($d.data)) { $ids += (([string]$a.account_id) -replace '^act_', '') }
    } catch { }
    return @{ ids = $ids }
}

$cuentasPorPais = @{}
# Cuentas ya bajadas en ESTA corrida, para que dos tokens que alcanzan la misma no
# sumen su gasto dos veces. Ver la guarda dentro del bucle de cuentas.
$yaBajadas = @{}
$tareasCuentas = New-Object System.Collections.Generic.List[object]
$adelantado = @{}
$descubierto = @{}

# El descubrimiento de cuentas (dos consultas por token) tambien se hace de una vez.
$tareasDesc = New-Object System.Collections.Generic.List[object]
foreach ($ck in @('mexico', 'colombia', 'peru', 'guatemala', 'venezuela', 'argentina', 'ecuador', 'bolivia')) {
    $cc = $tokens.$ck
    if (-not $cc) { continue }
    foreach ($bb in (Get-TokenBlocks $cc)) {
        $tk = [string]$bb.systemUserToken
        if (-not $tk) { continue }
        foreach ($ed in 'me/adaccounts', 'me/assigned_ad_accounts') {
            $tareasDesc.Add(@{ graphBase = $graphBase; edge = $ed; token = $tk; clave = ($ed + '|' + $tk) })
        }
    }
}
$resDesc = Invoke-EnParalelo $tareasDesc $guionDescubrir @() $HILOS_FACEBOOK
for ($i = 0; $i -lt $tareasDesc.Count; $i++) {
    if ($resDesc[$i] -and $resDesc[$i].ids) { $descubierto[$tareasDesc[$i].clave] = @($resDesc[$i].ids) }
    else { $descubierto[$tareasDesc[$i].clave] = @() }
}

foreach ($fase in @('plan', 'real')) {
if ($fase -eq 'real') {
    $yaBajadas = @{}
    $lista = Invoke-EnParalelo $tareasCuentas $guionCuenta @('Test-ErrorPermanente', 'Invoke-GraphPaged', 'Get-Accion', 'Get-AccountRows', 'Get-ScheduledCampaigns', 'Test-PuedeEscribir') $HILOS_FACEBOOK
    for ($i = 0; $i -lt $tareasCuentas.Count; $i++) { if ($lista[$i]) { $adelantado[$tareasCuentas[$i].acc] = $lista[$i] } }
}
foreach ($countryKey in @('mexico', 'colombia', 'peru', 'guatemala', 'venezuela', 'argentina', 'ecuador', 'bolivia')) {
    $c = $tokens.$countryKey
    if (-not $c) { if ($fase -eq 'real') { Write-Warning "No token block for $countryKey, skipping" }; continue }
    foreach ($blk in (Get-TokenBlocks $c)) {
        $token = [string]$blk.systemUserToken
        if (-not $token) { continue }
        # An empty accounts list means "whatever this token can see". Assigning an account
        # in Business Manager is then all it takes - no config edit, no redeploy.
        # La lista configurada es un piso, no un techo: se le UNE lo que el token alcance
        # hoy. Asi, asignarle una cuenta nueva al usuario del sistema en Business Manager
        # basta para que entre al dashboard sola, sin editar este archivo ni redesplegar.
        #
        # Se consultan las dos aristas: /me/adaccounts devuelve vacio para varios de estos
        # tokens (Argentina entre ellos) aunque /me/assigned_ad_accounts si las liste.
        $cuentas = @($blk.accounts | Where-Object { $_ } | ForEach-Object { ([string]$_) -replace '^act_','' })
        $descubiertas = @()
        foreach ($edge in 'me/adaccounts', 'me/assigned_ad_accounts') {
            # Ya se consultaron todos los tokens de una vez (ver arriba); si por lo que sea
            # falta uno, se pregunta aqui como siempre.
            $claveDesc = $edge + '|' + $token
            $idsDesc = $null
            if ($descubierto.ContainsKey($claveDesc)) { $idsDesc = $descubierto[$claveDesc] }
            else {
                try {
                    $d = Invoke-RestMethod "$graphBase/$edge`?fields=account_id&limit=200&access_token=$token" -TimeoutSec 30
                    $idsDesc = @(@($d.data) | ForEach-Object { ([string]$_.account_id) -replace '^act_','' })
                } catch { $idsDesc = @() }
                $descubierto[$claveDesc] = $idsDesc
            }
            foreach ($aid in @($idsDesc)) {
                if ($aid -and $cuentas -notcontains $aid -and $descubiertas -notcontains $aid) { $descubiertas += $aid }
            }
        }
        if ($descubiertas.Count) {
            if ($fase -eq 'real') { Write-Output ("  {0,-9} CUENTA NUEVA detectada: {1}" -f $countryKey, ($descubiertas -join ', ')) }
            $cuentas += $descubiertas
        }
        if (-not $cuentas.Count -and $fase -eq 'real') { Write-Warning "$countryKey : el token no tiene cuentas asignadas todavia" }
        # Se anota que cuentas se leyeron de verdad para este pais. El build las necesita:
        # hasta ahora sacaba su lista de facebook-tokens.json, asi que una cuenta nueva del
        # Business Manager entraba en el pull pero el build la ignoraba y su gasto no
        # aparecia en ningun ROAS. Lo que manda es lo que el token alcanza hoy.
        if (-not $cuentasPorPais.ContainsKey($countryKey)) { $cuentasPorPais[$countryKey] = @() }
        foreach ($x in $cuentas) { if ($cuentasPorPais[$countryKey] -notcontains $x) { $cuentasPorPais[$countryKey] += $x } }
        foreach ($acc in $cuentas) {
            $accId = [string]$acc
            # UNA cuenta se baja UNA vez por corrida, venga por el token que venga.
            #
            # Dos bloques de token pueden alcanzar la misma cuenta -pasa en cuanto dos
            # tokens pertenecen al mismo usuario de sistema, o cuando el descubrimiento
            # automatico le encuentra a uno lo que el otro ya tenia listado-. Sin esta
            # guarda, sus filas se agregan dos veces y el gasto sale EXACTAMENTE al doble:
            # Venezuela mostro 58,56 donde Facebook decia 29,28 el 2026-08-30.
            if ($yaBajadas.ContainsKey($accId)) {
                if ($fase -eq 'real') { Write-Output ("  {0,-9} act_{1}: ya la bajo otro token en esta corrida, se omite" -f $countryKey, $accId) }
                continue
            }
            $yaBajadas[$accId] = $true
            try {
                # Pull completo la primera vez que vemos esta cuenta, incremental despues.
                $pullCompleto = (-not $historicoCompleto.ContainsKey($accId)) -and ($sinceEff -ne $Since)

                # La ventana la fija ESTA cuenta, desde el ultimo dia que tenemos DE ELLA.
                # Con el ancla global, una cuenta muda desde hace dias solo recuperaba los
                # tres ultimos y su hueco quedaba abierto para siempre.
                $ventanaEsta = $sinceEff
                if (-not $pullCompleto -and $WindowDays -gt 0 -and $ultimaPorCuenta.ContainsKey($accId)) {
                    $anclaA = [datetime]::ParseExact($ultimaPorCuenta[$accId], 'yyyy-MM-dd', $null)
                    $hoyA   = [datetime]::ParseExact($Until, 'yyyy-MM-dd', $null)
                    if ($anclaA -gt $hoyA) { $anclaA = $hoyA }
                    $candA = $anclaA.AddDays(-1 * ($WindowDays - 1)).ToString('yyyy-MM-dd')
                    $ventanaEsta = if ($candA -gt $Since) { $candA } else { $Since }
                }
                $desdeEsta = if ($pullCompleto) { $Since } else { $ventanaEsta }
                # Pasada 'plan': solo se anota la tarea; el resto del cuerpo corre en la 'real'.
                if ($fase -eq 'plan') {
                    $probarEscritura = -not ($permisosPrevios.ContainsKey($accId) -and -not $permisoVencido -and $permisosPrevios[$accId] -eq $true)
                    $tareasCuentas.Add(@{ acc = $accId; token = $token; desde = $desdeEsta; probar = $probarEscritura })
                    continue
                }
                $pf = $adelantado[$accId]
                if ($pf -and [string]$pf.desde -eq [string]$desdeEsta) {
                    if ($pf.errRows) { throw (New-ErrorGuardado $pf.errRows) }
                    $res = $pf.rows
                } else {
                    $pf = $null
                    $res = Get-AccountRows -accountId $accId -token $token -Desde $desdeEsta
                }
                if ($pullCompleto) { Write-Output ("  {0,-9} act_{1}: primer historico completo, desde {2}" -f $countryKey, $accId, $Since) }
                elseif ($ventanaEsta -lt $sinceEff) {
                    Write-Output ("  {0,-9} act_{1}: venia atrasada, se piden {2} .. {3}" -f $countryKey, $accId, $ventanaEsta, $Until)
                }
                # Se marca DESPUES de que la llamada salio bien: si fallo, mañana se
                # reintenta el completo en vez de darlo por hecho.
                $historicoCompleto[$accId] = $true
                # Dias de la ventana que ya tenian gasto y que Facebook acaba de devolver en
                # cero (ver Get-DiasGastoPerdido): de esos se quedan las filas anteriores.
                # Si la guarda misma fallara, la cuenta sigue como antes de existir ella (gana lo
                # nuevo): un error aqui no debe dejar la cuenta entera SIN CONEXION.
                $perdidos = @{}
                if (-not $pullCompleto -and $previas.ContainsKey($accId)) {
                    try { $perdidos = Get-DiasGastoPerdido $res.rows $previas[$accId] $ventanaEsta }
                    catch {
                        Write-Warning ("act_{0}: la guarda de gasto en cero fallo ({1}); se usa lo que trajo Facebook." -f $accId, $_.Exception.Message)
                        $perdidos = @{}
                    }
                }
                foreach ($r in $res.rows) {
                    if ($perdidos.ContainsKey([string]$r.date)) { continue }
                    $allRows.Add($r)
                }
                foreach ($dp in ($perdidos.Keys | Sort-Object)) {
                    $pp = $perdidos[$dp]
                    Write-Warning ("{0} act_{1}: Facebook devolvio gasto {2:0.##} para el {3} y la corrida anterior tenia {4:0.##}; se conservan las filas anteriores de ese dia." -f `
                        $countryKey, $accId, $pp.ahora, $dp, $pp.antes)
                    $gastoConservado.Add([ordered]@{ cuenta = $accId; pais = $countryKey; fecha = $dp; antes = [math]::Round($pp.antes, 2); ahora = [math]::Round($pp.ahora, 2) })
                }
                if ($res.salud) { $res.salud['country'] = $countryKey; $saludCuentas[$accId] = $res.salud }
                # Historico anterior a la ventana: se conserva sin volver a bajarlo, con el
                # estado repintado al de hoy. Las filas DENTRO de la ventana se descartan
                # porque el pull que acaba de correr ya las trae, corregidas si Facebook
                # reajusto el gasto de esos dias.
                #
                # Si a esta cuenta se le acaba de pedir el historico COMPLETO, no se conserva
                # nada: el pull ya trae todo el rango y conservar ademas lo anterior mete cada
                # fila dos veces. Paso: el archivo casi duplico sus filas y el gasto de
                # un pais salio exactamente al doble de lo que decia Facebook. El
                # filtro de abajo compara contra $sinceEff, que es la ventana corta, y no
                # contra el rango que de verdad se pidio.
                # El filtro compara contra la ventana que SE PIDIO para esta cuenta, no
                # contra la global. Es el mismo invariante que ya se rompio una vez -y
                # duplico el gasto de Mexico-: si se conserva historico que el pull tambien
                # trajo, cada fila entra dos veces.
                $retenidas = 0
                if (-not $pullCompleto -and $previas.ContainsKey($accId)) {
                    foreach ($r in $previas[$accId]) {
                        $dr = [string]$r.date
                        # Dentro de la ventana solo se conservan los dias protegidos por la
                        # guarda: de esos se descartaron arriba las filas nuevas, asi que
                        # tampoco aqui entra nada dos veces.
                        if ($dr -ge $ventanaEsta -and -not $perdidos.ContainsKey($dr)) { continue }
                        $allRows.Add((Actualizar-Estado $r $res.status))
                        $retenidas++
                    }
                }
                if ($pf -and ($pf.ContainsKey('sched') -or $pf.errSched)) {
                    if ($pf.errSched) { throw (New-ErrorGuardado $pf.errSched) }
                    $sched = @($pf.sched)
                } else {
                    $sched = Get-ScheduledCampaigns -accountId $accId -token $token -nowUtc $nowUtc
                }
                foreach ($s in $sched) { $s['country'] = $countryKey; $allScheduled.Add($s) }
                # El permiso de escritura cuesta dos llamadas por cuenta y por corrida -un
                # GET y un POST- para averiguar algo que cambia cuando alguien toca Business
                # Manager, o sea casi nunca. Se reusa el de la corrida anterior mientras no
                # pase medio dia. No mas: el interruptor del tablero se apaga solo donde no
                # va a funcionar, y con un cache eterno volveria a ofrecer un pausado que
                # falla en silencio, que es justo el bug que Test-PuedeEscribir vino a matar.
                # Solo se reusa el SI. Un "no puede escribir" se vuelve a comprobar en cada
                # corrida, y son dos llamadas: cuando alguien concede "Administrar campañas"
                # en Business Manager, el interruptor se enciende en el proximo refresco en
                # vez de hasta doce horas despues. Cachear el no es cachear justo el estado
                # que el usuario esta tratando de cambiar.
                $escribe = $null
                $motivo  = ''
                if ($permisosPrevios.ContainsKey($accId) -and -not $permisoVencido -and $permisosPrevios[$accId] -eq $true) {
                    $escribe = $true
                } else {
                    if ($pf -and $pf.ContainsKey('escribir') -and $pf.escribir) { $t = $pf.escribir }
                    else { $t = Test-PuedeEscribir -accountId $accId -token $token }
                    $escribe = $t['puede']
                    $motivo  = [string]$t['motivo']
                }
                $permisos[$accId] = $escribe
                if ($motivo) { $motivosPermiso[$accId] = $motivo }
                $etq = if ($escribe -eq $true) { 'escribe' } elseif ($escribe -eq $false) { "SOLO LECTURA ($motivo)" } else { 'permiso ?' }
                Write-Output ("  {0,-9} act_{1,-16} {2,4} ads, {3,5} daily spend rows (+{4} historicas), {5,3} scheduled, {6}" -f $countryKey, $accId, $res.adCount, $res.spendRows, $retenidas, $sched.Count, $etq)
            } catch {
                # Una cuenta caida no debe tumbar el pull entero: los demas paises siguen y el
                # build usa lo que si llego. Antes un token vencido borraba todo el refresco.
                Write-Warning "Account $accId ($countryKey) failed: $($_.Exception.Message)"
                # NO PODER LEER una cuenta no es lo mismo que la cuenta no gaste, y hasta
                # ahora las dos cosas se veian igual: un cero. El BM "Info Digital" de
                # Colombia llevaba nueve dias contestando "API access blocked" -su app esta
                # bloqueada, no sus cuentas- y el tablero pintaba Colombia sin gasto, con
                # toda naturalidad, mientras las campanas corrian. Argentina y Ecuador
                # llevan igual mas tiempo.
                #
                # Se anota con estado -1, que Facebook no usa, para que el resto de la
                # maquinaria de salud la trate como caida y el mensaje pueda decir que esta
                # CIEGA en vez de inventarle un motivo. El texto de la API viaja tal cual:
                # "API access blocked" y "token expirado" se arreglan en sitios distintos.
                $msgApi = [string]$_.Exception.Message
                try {
                    $ed = $_.ErrorDetails.Message
                    if ($ed) { $je = $ed | ConvertFrom-Json; if ($je.error.message) { $msgApi = [string]$je.error.message } }
                } catch { }
                if (-not $saludCuentas.ContainsKey($accId)) {
                    $saludCuentas[$accId] = [ordered]@{
                        nombre   = ''
                        estado   = -1
                        motivo   = 0
                        country  = $countryKey
                        ilegible = $true
                        error    = $msgApi
                    }
                }
                # Y tampoco debe BORRAR lo que ya sabiamos de esa cuenta.
                #
                # Este archivo se reescribe entero en cada corrida, asi que una cuenta que
                # falla desaparecia del gasto y el pais se quedaba en cero. Peru paso de
                # infinito y el tablero decia que todo se vendia sin invertir un peso. Se
                # arrastran las filas de la corrida anterior y se anota que estan viejas.
                if ($previas.ContainsKey($accId)) {
                    foreach ($r in $previas[$accId]) { $allRows.Add($r) }
                    $congeladas[$accId] = $true
                    Write-Output ("  {0,-9} act_{1,-16} SIN CONEXION: se conservan {2} filas de la corrida anterior" -f `
                        $countryKey, $accId, $previas[$accId].Count)
                }
            }
        }
    }
}
}   # fin de las dos pasadas ('plan' y 'real')

# Una fila por cuenta+dia+anuncio. Es la ultima red, y cura el pasado ademas de proteger
# el futuro.
#
# Las filas que quedan fuera de la ventana se heredan del archivo anterior sin volver a
# pedirlas. Si ese archivo venia con duplicados -paso el 2026-08-30, cuando dos bloques de
# token del mismo usuario de sistema bajaron las mismas cuentas y el gasto salio al doble-,
# la herencia los arrastra para siempre: la ventana solo corrige los dias que vuelve a
# pedir. Deduplicar aqui limpia tambien lo que ya estaba mal, sin tener que rebajar meses.
#
# Se conserva la PRIMERA aparicion: las filas frescas se agregan antes que las historicas,
# asi que ante un choque gana lo que acaba de decir Facebook.
$vistas = @{}
$sinRepetir = New-Object System.Collections.Generic.List[object]
$repetidas = 0
foreach ($r in $allRows) {
    $clave = "{0}|{1}|{2}" -f [string]$r.account_id, [string]$r.date, [string]$r.ad_id
    if ($vistas.ContainsKey($clave)) { $repetidas++; continue }
    $vistas[$clave] = $true
    $sinRepetir.Add($r)
}
if ($repetidas) { Write-Output ("Filas repetidas descartadas: {0} (quedan {1})" -f $repetidas, $sinRepetir.Count) }
$allRows = $sinRepetir

$out = [ordered]@{
    fetchedAt          = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')
    # Cuentas cuyo gasto NO se pudo bajar en esta corrida y viene de la anterior.
    # A quien ya se le pidio el historico entero. Sin esto, cada corrida volveria a pedirlo
    # todo o -peor- ninguna lo pediria nunca y las cuentas nuevas nacerian sin pasado.
    historicoCompleto  = @($historicoCompleto.Keys)
    cuentasCongeladas  = @($congeladas.Keys)
    source             = 'facebook-graph-api'
    # since = donde empieza el historico que contiene el archivo (no cambia).
    # windowSince = que pedazo se volvio a pedir hoy. Los dos, para poder auditar de un
    # vistazo si una corrida trajo poco porque asi se pidio, o porque algo fallo.
    since              = $Since
    windowSince        = $sinceEff
    until              = $Until
    result             = $allRows
    scheduledCampaigns = $allScheduled
    writeAccess        = $permisos
    writeReason        = $motivosPermiso
    accountsByCountry  = $cuentasPorPais
    # Salud por cuenta publicitaria: nombre, estado y motivo. Es lo que permite que el aviso
    # diga "deshabilitada por politica" en vez de "revisa que siga conectada", que era una
    # conjetura, y que los anuncios de una cuenta caida dejen de contarse como activos.
    saludCuentas       = $saludCuentas
    # Dias que Facebook devolvio en cero teniendo gasto en la corrida anterior, y que por eso
    # se conservaron de ella (ver Get-DiasGastoPerdido). Vacio en una corrida normal.
    gastoConservado    = $gastoConservado
}
$out | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutFile -Encoding utf8
Write-Output "Wrote $($allRows.Count) spend rows + $($allScheduled.Count) scheduled campaigns -> $OutFile"

$soloLectura = @($permisos.Keys | Where-Object { $permisos[$_] -eq $false })
if ($soloLectura.Count -gt 0) {
    Write-Output ""
    Write-Output ("OJO: {0} cuenta(s) son de SOLO LECTURA - el dashboard no podra pausar ahi:" -f $soloLectura.Count)
    foreach ($a in $soloLectura) { Write-Output "   act_$a" }
    Write-Output "   Arreglo: Business Manager -> Usuarios del sistema -> el usuario -> Agregar activos"
    Write-Output "            -> Cuentas publicitarias -> permiso 'Administrar campanas'."
}
