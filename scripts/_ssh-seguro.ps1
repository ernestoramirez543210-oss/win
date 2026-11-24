# Ayudante de scripts\Setup-DriveBroll.ps1 y scripts\Setup-LlaveScrapeCreators.ps1: habla con el servidor por SSH sin que ssh.exe se pueda quedar colgado.
# Se carga con:  . (Join-Path $PSScriptRoot '_ssh-seguro.ps1')
#
# Por que existe (2026-10-20): ssh.exe lanzado "a pelo" desde la terminal hereda la CONSOLA como entrada. En una pestana del Terminal del dueno se quedo esperando algo de la
# consola SIN salir, aunque el servidor ya habia contestado (en una pestana nueva la misma llamada tardaba 1 segundo). Aqui cada ssh corre en su propio proceso, sin consola y
# sin entrada heredada, con tiempo maximo propio: si no contesta se mata y se reintenta una vez; si vuelve a fallar se dice con un mensaje claro.
#   - lo que hay que mandarle al servidor (el permiso de Drive, la llave) va como BYTES por una tuberia que se cierra: sin BOM, sin CR, nunca en la linea de comandos
#   - sin nada que mandar: ssh -n y la tuberia cerrada
#   - ServerAliveInterval/CountMax: si la conexion se cae a medias, ssh se da cuenta en unos 30 segundos (y de todos modos manda el tiempo maximo)
#   - se reintenta una vez si no contesto a tiempo o si ssh no pudo conectar (codigo 255); si el comando remoto falla de verdad (otro codigo) NO se reintenta
# ASCII-only, PS 5.1 safe.

function ConvertTo-ArgWin([string]$Texto) {
    # Una palabra de la linea de comandos de Windows (reglas de CommandLineToArgvW, las que usa ssh.exe): entre comillas, \" para una comilla,
    # y las barras invertidas que quedan antes de una comilla (o antes de la comilla de cierre) se duplican. Las demas barras van tal cual.
    if ($Texto.Length -gt 0 -and $Texto -notmatch '[\s"]') { return $Texto }
    $barra = [char]92
    $comilla = [char]34
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append($comilla)
    $barras = 0
    foreach ($ch in $Texto.ToCharArray()) {
        if ($ch -eq $barra) { $barras++; continue }
        if ($ch -eq $comilla) {
            [void]$sb.Append($barra, ($barras * 2 + 1)); [void]$sb.Append($comilla); $barras = 0; continue
        }
        if ($barras -gt 0) { [void]$sb.Append($barra, $barras); $barras = 0 }
        [void]$sb.Append($ch)
    }
    if ($barras -gt 0) { [void]$sb.Append($barra, ($barras * 2)) }
    [void]$sb.Append($comilla)
    return $sb.ToString()
}

function ConvertTo-Lineas($Texto) {
    $t = [string]$Texto
    if ([string]::IsNullOrEmpty($t)) { return @() }
    $l = @($t -split "\r?\n")
    if ($l.Count -gt 0 -and $l[$l.Count - 1] -eq '') { $l = @($l | Select-Object -First ($l.Count - 1)) }
    return $l
}

function Stop-ArbolProceso([int]$Id) {
    # Mata el proceso y todo lo que haya lanzado (taskkill /T); si taskkill no esta, al menos el proceso.
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'taskkill.exe'; $psi.Arguments = "/PID $Id /T /F"
        $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
        $k = [System.Diagnostics.Process]::Start($psi)
        [void]$k.WaitForExit(10000)
    } catch { }
    try { $p = [System.Diagnostics.Process]::GetProcessById($Id); $p.Kill(); [void]$p.WaitForExit(5000) } catch { }
}

function New-SshConexion([string]$Ssh, [string]$Llave, [string]$Servidor, [int]$Segundos = 40) {
    # Lo que no cambia entre llamadas: el ssh.exe, el servidor, las opciones y el tiempo maximo por llamada.
    return [pscustomobject]@{
        Ssh = $Ssh
        Servidor = $Servidor
        Segundos = $Segundos
        Opciones = @('-T', '-i', $Llave, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', '-o', 'StrictHostKeyChecking=yes')
    }
}

function Invoke-SshUnaVez([string]$Exe, [string]$Linea, $Bytes, [int]$Segundos) {
    $res = [pscustomobject]@{ Ok = $false; Codigo = -1; Salida = @(); Error = @(); TiempoAgotado = $false; NoLanzo = '' }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = $Linea
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $p = $null
    try { $p = [System.Diagnostics.Process]::Start($psi) }
    catch { $res.NoLanzo = 'No pude lanzar ssh (' + $Exe + '): ' + $_.Exception.Message; return $res }
    try {
        $tOut = $p.StandardOutput.ReadToEndAsync()
        $tErr = $p.StandardError.ReadToEndAsync()
        try {
            # La entrada se manda como bytes y la tuberia se CIERRA: asi ssh nunca espera nada de la consola. Si ssh ya salio (no conecto), la tuberia esta rota: el codigo de salida lo dice.
            if ($null -ne $Bytes) { $p.StandardInput.BaseStream.Write($Bytes, 0, $Bytes.Length); $p.StandardInput.BaseStream.Flush() }
            $p.StandardInput.Close()
        } catch { }
        # Se espera de a 250 ms (no de una sola vez) para que Ctrl-C responda enseguida y para poder avisar si el servidor tarda.
        $reloj = [System.Diagnostics.Stopwatch]::StartNew()
        $avisado = $false
        $salio = $false
        while (-not $salio) {
            if ($p.WaitForExit(250)) { $salio = $true; break }
            if ($reloj.ElapsedMilliseconds -ge ($Segundos * 1000)) { break }
            if (-not $avisado -and $Segundos -gt 15 -and $reloj.ElapsedMilliseconds -ge 12000) {
                $avisado = $true
                Write-Host ('  El servidor todavia no contesta; espero hasta ' + $Segundos + ' segundos...')
            }
        }
        if ($salio) {
            $res.Codigo = $p.ExitCode
            $res.Ok = ($res.Codigo -eq 0)
        } else {
            $res.TiempoAgotado = $true
            Stop-ArbolProceso $p.Id
        }
        try { [void][System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($tOut, $tErr), 5000) } catch { }
        if ($tOut.IsCompleted -and -not $tOut.IsFaulted) { $res.Salida = @(ConvertTo-Lineas $tOut.Result) }
        if ($tErr.IsCompleted -and -not $tErr.IsFaulted) { $res.Error = @(ConvertTo-Lineas $tErr.Result) }
    } finally {
        # Si el script se corta a medias (Ctrl-C), que no quede un ssh escondido vivo.
        try { if (-not $p.HasExited) { Stop-ArbolProceso $p.Id } } catch { }
        try { $p.Dispose() } catch { }
    }
    return $res
}

function Invoke-Ssh {
    # Corre UN comando en el servidor y devuelve @{ Ok; Codigo; Salida (lineas); Error (lineas); TiempoAgotado; Segundos; Intentos; NoLanzo }.
    #   -Entrada: texto ASCII que se le manda al comando por su entrada estandar (si no hay, ssh va con -n). Si no pasas -Entrada, no se manda nada.
    #   -Que:     una frase corta de lo que se esta haciendo ("miro el estado del permiso"): sale en pantalla antes de cada conexion.
    #   -Intentos: 2 por defecto (uno de reintento); pon 1 en lo que NO conviene repetir (una busqueda que gasta un credito).
    param(
        [Parameter(Mandatory = $true)]$Con,
        [Parameter(Mandatory = $true)][string]$Comando,
        $Entrada = $null,
        [string]$Que = '',
        [int]$Segundos = 0,
        [int]$Intentos = 2
    )
    if ($Segundos -le 0) { $Segundos = [int]$Con.Segundos }
    $lista = @()
    if ($null -eq $Entrada) { $lista += '-n' }
    $lista += @($Con.Opciones)
    $lista += [string]$Con.Servidor
    $lista += $Comando
    $linea = (@($lista | ForEach-Object { ConvertTo-ArgWin ([string]$_) }) -join ' ')
    $bytes = $null
    if ($null -ne $Entrada) { $bytes = [System.Text.Encoding]::ASCII.GetBytes([string]$Entrada) }
    $detalle = ''
    if ($Que) { $detalle = ' (' + $Que + ')' }
    $r = $null
    $hechos = 0
    for ($i = 1; $i -le $Intentos; $i++) {
        $hechos = $i
        if ($i -eq 1) { Write-Host ('Conectando con el servidor' + $detalle + '...') }
        else { Write-Host ('Intento ' + $i + ' de ' + $Intentos + '...') }
        $r = Invoke-SshUnaVez ([string]$Con.Ssh) $linea $bytes $Segundos
        if ($r.Ok -or $r.NoLanzo) { break }
        if (-not ($r.TiempoAgotado -or $r.Codigo -eq 255)) { break }
        if ($i -lt $Intentos) {
            if ($r.TiempoAgotado) { Write-Host ('  Sin respuesta en ' + $Segundos + ' segundos: cierro esa conexion y lo intento otra vez.') }
            else { Write-Host ('  ssh no pudo conectar' + $(if ($r.Error.Count) { ' (' + $r.Error[0] + ')' } else { '' }) + ': lo intento otra vez.') }
        }
    }
    $r | Add-Member -NotePropertyName Segundos -NotePropertyValue $Segundos
    $r | Add-Member -NotePropertyName Intentos -NotePropertyValue $hechos
    # SinConexion: no se llego a hablar con el servidor (no contesto a tiempo, ssh no conecto o ni siquiera se pudo lanzar); lo contrario es que el comando remoto corrio y devolvio otro codigo.
    $r | Add-Member -NotePropertyName SinConexion -NotePropertyValue ([bool]($r.TiempoAgotado -or $r.Codigo -eq 255 -or $r.NoLanzo))
    return $r
}

function Get-FalloSsh($R) {
    # Una frase para decirle al dueno por que fallo la llamada (sin llaves ni nada secreto: solo lo que dijo ssh o el servidor).
    if ($R.NoLanzo) { return [string]$R.NoLanzo }
    if ($R.TiempoAgotado) { return ('El servidor no contesto en ' + $R.Segundos + ' segundos (' + $R.Intentos + ' intento(s)).') }
    $dijo = ''
    if ($R.Error.Count) {
        $dijo = ' Dijo: ' + (($R.Error | Select-Object -First 2) -join ' / ')
        if ($dijo.Length -gt 300) { $dijo = $dijo.Substring(0, 300) }
        $dijo = $dijo.TrimEnd()
        if (-not $dijo.EndsWith('.')) { $dijo += '.' }
    }
    if ($R.Codigo -eq 255) { return ('ssh no pudo conectar con el servidor (codigo 255, ' + $R.Intentos + ' intento(s)).' + $dijo) }
    return ('El servidor contesto con el codigo ' + $R.Codigo + '.' + $dijo)
}
