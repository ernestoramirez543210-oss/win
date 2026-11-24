# Funciones compartidas por Instalar-Editor.ps1, Empaquetar-Editor.ps1 y Conectar-Editor.ps1.
#
# Todo en ASCII a proposito: PowerShell 5.1 lee los .ps1 sin BOM como ANSI y rompe los acentos.
# Compatible con PowerShell 5.1 (el que trae Windows 10/11): sin &&, sin ?:, sin ??.

# Carpetas que nunca se instalan ni se empaquetan (dependencias, modelos y cache se bajan en cada PC; vps = lo que corre en el servidor, no en el PC del editor).
$script:CarpetasExcluidas = @('node_modules', '.venv', 'modelos', '.cache', '.git', '_paquete', '__pycache__', 'vps')

# Archivos que son DATOS del usuario: se copian solo si faltan y nunca se pisan (aunque cambien aqui).
$script:SoloSiFalta = @('.claude/skills/editor-video/references/preferencias.md')

function Ruta-Relativa([string]$base, [string]$ruta) {
    $b = (Resolve-Path -LiteralPath $base).Path.TrimEnd('\')
    return $ruta.Substring($b.Length).TrimStart('\', '/').Replace('\', '/')
}

function Es-Dato-Del-Usuario([string]$rel) {
    # Lo que NO viaja en el instalador: material de trabajo, resultados, claves y estado de la instalacion.
    if ($rel -eq '.env' -or $rel -eq 'costos.jsonl' -or $rel -eq '.instalado.json' -or $rel -eq 'instalacion.log') { return $true }
    # La conexion que se baja del dash lleva la llave del editor: nunca viaja en el .zip ni se copia a una instalacion (Conectar-Editor la pasa a .env y la borra).
    if ($rel.Split('/')[-1] -like 'conexion-editor*.txt') { return $true }
    if ($rel.EndsWith('.nuevo')) { return $true }
    if ($rel.StartsWith('anuncios/') -and $rel -ne 'anuncios/LEEME.txt') { return $true }
    if ($rel.StartsWith('public/productos/') -and -not $rel.StartsWith('public/productos/producto-ejemplo/') -and $rel -ne 'public/productos/LEEME.txt') { return $true }
    if ($rel.StartsWith('public/musica/biblioteca/') -and $rel -ne 'public/musica/biblioteca/LEEME.txt') { return $true }
    if ($rel.StartsWith('public/sfx/') -and $rel -ne 'public/sfx/LEEME.txt' -and $rel -ne 'public/sfx/LICENCIAS.txt') { return $true }
    return $false
}

function Archivos-Del-Editor([string]$base) {
    # Rutas relativas (con /) de lo que se instala o se empaqueta: codigo, plantillas y guias.
    $lista = New-Object System.Collections.Generic.List[string]
    foreach ($f in (Get-ChildItem -LiteralPath $base -Recurse -File -Force)) {
        $rel = Ruta-Relativa $base $f.FullName
        $partes = $rel.Split('/')
        $carpetas = @()
        if ($partes.Length -gt 1) { $carpetas = $partes[0..($partes.Length - 2)] }
        $excluida = $false
        foreach ($c in $carpetas) { if ($script:CarpetasExcluidas -contains $c) { $excluida = $true } }
        if ($excluida) { continue }
        if (Es-Dato-Del-Usuario $rel) { continue }
        $lista.Add($rel)
    }
    return $lista
}

# ------------------------------------------------------------------ conexion con el dash (conexion-editor.txt)
# El dash (pagina Editor de anuncios, boton "Descargar conexion") da un archivo conexion-editor.txt con DOS lineas: DASH_URL y DASH_CLAVE_EDITOR. Con ellas el editor
# pide imagenes, clips, texto y voz POR EL DASH: desde la version 2026.11.10-2 ya no usa la llave de APIMart. Estas funciones lo pasan a .env sin mostrar nunca la llave.
$script:DashOficial = 'https://tu_proyecto.pages.dev'

function Leer-Conexion([string]$ruta) {
    # @{ Ok = $true; Url; Llave } si el archivo es una conexion valida del dash; si no @{ Ok = $false; Motivo }. El motivo nunca lleva la llave.
    $r = @{ Ok = $false; Motivo = ''; Url = ''; Llave = '' }
    $item = Get-Item -LiteralPath $ruta -ErrorAction SilentlyContinue
    if (-not $item) { $r.Motivo = 'no se pudo leer'; return $r }
    if ($item.Length -gt 4096) { $r.Motivo = 'es demasiado grande para ser una conexion del dash'; return $r }
    $texto = [IO.File]::ReadAllText($ruta)
    $lineas = @($texto -split "`r?`n" | ForEach-Object { $_.Trim().TrimStart([char]0xFEFF) } | Where-Object { $_ -ne '' })
    if ($lineas.Count -ne 2) { $r.Motivo = "debe traer exactamente 2 lineas (DASH_URL y DASH_CLAVE_EDITOR) y trae $($lineas.Count)"; return $r }
    $url = $null; $llave = $null
    foreach ($l in $lineas) {
        if ($l.StartsWith('DASH_URL=', [StringComparison]::Ordinal)) { $url = $l.Substring(9) }
        elseif ($l.StartsWith('DASH_CLAVE_EDITOR=', [StringComparison]::Ordinal)) { $llave = $l.Substring(18) }
    }
    if ($null -eq $url -or $null -eq $llave) { $r.Motivo = 'debe traer una linea DASH_URL= y una DASH_CLAVE_EDITOR='; return $r }
    if ($url -notmatch ('^' + [regex]::Escape($script:DashOficial) + '/?$')) { $r.Motivo = "DASH_URL debe ser exactamente $($script:DashOficial) (sin ruta, usuario ni puerto)"; return $r }
    if ($llave -cnotmatch '^[\x21-\x7E]{16,256}$') { $r.Motivo = 'DASH_CLAVE_EDITOR debe ir entera: de 16 a 256 caracteres visibles, sin espacios'; return $r }
    $r.Ok = $true; $r.Url = $script:DashOficial; $r.Llave = $llave
    return $r
}

function Escribir-Conexion([string]$envRuta, [string]$url, [string]$llave) {
    # Pone DASH_URL y DASH_CLAVE_EDITOR en .env (reemplaza las que habia), QUITA toda linea APIMART_API_KEY= (el editor ya no la usa) y deja lo demas como estaba.
    # Devuelve cuantas llaves de APIMart con valor se quitaron. UTF-8 sin BOM: Python lee .env y una BOM romperia la primera linea.
    $lineas = @()
    if (Test-Path -LiteralPath $envRuta) { $lineas = @([IO.File]::ReadAllLines($envRuta)) }
    $quedan = New-Object System.Collections.Generic.List[string]
    $quitadas = 0
    foreach ($l in $lineas) {
        $s = $l.TrimStart([char]0xFEFF).Trim()
        if ($s -match '^(DASH_URL|DASH_CLAVE_EDITOR)\s*=') { continue }
        if ($s -match '^APIMART_API_KEY\s*=') { if ($s -match '^APIMART_API_KEY\s*=\s*\S') { $quitadas++ }; continue }
        $quedan.Add($l.TrimStart([char]0xFEFF))
    }
    while ($quedan.Count -gt 0 -and $quedan[$quedan.Count - 1].Trim() -eq '') { $quedan.RemoveAt($quedan.Count - 1) }
    $quedan.Add("DASH_URL=$url")
    $quedan.Add("DASH_CLAVE_EDITOR=$llave")
    [IO.File]::WriteAllText($envRuta, (($quedan -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
    return $quitadas
}

function Buscar-Conexion([string[]]$carpetas) {
    # Los conexion-editor*.txt de esas carpetas, del mas nuevo al mas viejo (si se baja dos veces, el navegador la guarda como "conexion-editor (1).txt").
    $hallados = @()
    foreach ($c in $carpetas) {
        if (-not $c -or -not (Test-Path -LiteralPath $c -PathType Container)) { continue }
        $hallados += @(Get-ChildItem -LiteralPath $c -File -Filter 'conexion-editor*.txt' -ErrorAction SilentlyContinue)
    }
    return @($hallados | Sort-Object LastWriteTime -Descending)
}

function Importar-Conexion([string]$envRuta, [string[]]$carpetas) {
    # Busca la conexion del dash (junto al instalador y en Descargas), la pasa a .env y borra el archivo bajado. Devuelve 'importada', 'no-hay' o 'invalida'.
    # Nunca muestra la llave.
    $archivos = @(Buscar-Conexion $carpetas)
    if ($archivos.Count -eq 0) { return 'no-hay' }
    $f = $archivos[0]
    $c = Leer-Conexion $f.FullName
    if (-not $c.Ok) {
        Write-Host "  [ojo]   $($f.FullName) no es una conexion valida del dash: $($c.Motivo). No se toco .env. Bajala otra vez del dash (Herramientas > Editor de anuncios > Descargar conexion)." -ForegroundColor Yellow
        return 'invalida'
    }
    $quitadas = Escribir-Conexion $envRuta $c.Url $c.Llave
    $c.Llave = ''
    Write-Host "  [ok]    Editor conectado al dash ($($c.Url)): DASH_URL y DASH_CLAVE_EDITOR quedaron en $envRuta (la llave no se muestra)." -ForegroundColor Green
    if ($quitadas -gt 0) { Write-Host '  [ok]    Se quito la llave de APIMart de .env: el editor ya no la usa (todo pasa por el dash).' -ForegroundColor Green }
    try {
        Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
        Write-Host "  [ok]    Borrado $($f.FullName) (la llave ya esta en .env)." -ForegroundColor Green
    } catch {
        Write-Host "  [ojo]   No pude borrar $($f.FullName): borralo tu (lleva la llave del editor)." -ForegroundColor Yellow
    }
    foreach ($otro in ($archivos | Select-Object -Skip 1)) { Write-Host "  [ojo]   Hay otra copia de la conexion: $($otro.FullName). Borrala (lleva una llave del editor)." -ForegroundColor Yellow }
    return 'importada'
}

function Hash-Archivo([string]$ruta) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $ruta).Hash.ToLower()
}

function Buscar-Programa([string]$nombre) {
    $c = Get-Command $nombre -ErrorAction SilentlyContinue | Where-Object { $_.CommandType -eq 'Application' } | Select-Object -First 1
    if ($c) { return $c.Source }
    return $null
}

function Refrescar-Path {
    # Tras instalar algo con winget, esta ventana no ve el PATH nuevo hasta que se lo pedimos.
    $nuevo = @([Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User')) | Where-Object { $_ }
    foreach ($bloque in $nuevo) {
        foreach ($p in $bloque.Split(';')) {
            if ($p -and -not (($env:Path.Split(';')) -contains $p)) { $env:Path = $env:Path + ';' + $p }
        }
    }
}
