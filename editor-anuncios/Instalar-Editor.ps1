# Instala o actualiza el Editor de anuncios en este PC.
#
#   Doble clic en Instalar-Editor.cmd
#   o:  powershell -ExecutionPolicy Bypass -File Instalar-Editor.ps1 [opciones]
#
# Opciones:
#   -Destino <carpeta>   Donde instalar (por defecto D:\editor-anuncios, o C:\ si no hay D:).
#   -Si                  Contesta que si a todas las preguntas (instalar sin atender).
#   -Modelo <nombre>     Modelo de Whisper: tiny, base, small (por defecto) o medium.
#   -SinModelo           No descarga el modelo de Whisper (se baja solo la primera vez que se transcribe).
#   -SinNavegador        No descarga el navegador de Remotion (se baja solo en el primer render).
#   -SinClip             No descarga los modelos de CLIP (~300 MB) del anuncio automatico (emparejar_tomas.py y montaje_auto.py piden que se bajen antes de usarse).
#   -SoloVerificar       No instala nada: dice que falta.
#   -Forzar              Pisa tambien lo que alguien edito en el destino (menos preferencias.md y .env).
#
# Se puede correr tantas veces como haga falta: instala lo que falte, actualiza el codigo y NUNCA toca
# productos, anuncios, musica ni preferencias.md. En .env solo pone la conexion con el dash si encuentra el archivo
# conexion-editor.txt que se baja del dash (junto al instalador o en Descargas): escribe DASH_URL y DASH_CLAVE_EDITOR,
# quita la linea vieja APIMART_API_KEY (el editor ya no la usa: todo pasa por el dash) y borra el archivo bajado.
# Si alguien edito CLAUDE.md, SKILL.md u otro archivo del codigo en el destino, lo conserva y deja la version nueva
# al lado como <archivo>.nuevo.
#
# Instala en el destino: Node.js LTS, Python 3.12 y ffmpeg (con winget, si faltan), las dependencias de
# Remotion (npm), un entorno de Python propio (.venv) con las librerias de IA, el navegador de Remotion,
# el modelo de Whisper y los efectos de sonido. Todo en ASCII a proposito (PowerShell 5.1 y los acentos).
param(
    [string]$Destino = '',
    [switch]$Si,
    [string]$Modelo = 'small',
    [switch]$SinModelo,
    [switch]$SinNavegador,
    [switch]$SinClip,
    [switch]$SoloVerificar,
    [switch]$Forzar
)

$ErrorActionPreference = 'Stop'
$Origen = $PSScriptRoot
. (Join-Path $Origen 'Editor-Comun.ps1')

function Titulo([string]$t)  { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }
function Ok([string]$t)      { Write-Host "  [ok]    $t" -ForegroundColor Green }
function Aviso([string]$t)   { Write-Host "  [ojo]   $t" -ForegroundColor Yellow }
function Falta([string]$t)   { Write-Host "  [falta] $t" -ForegroundColor Red }
function Info([string]$t)    { Write-Host "          $t" }

function Preguntar([string]$pregunta) {
    if ($Si) { Write-Host "  $pregunta -> si (-Si)"; return $true }
    $r = Read-Host "  $pregunta [S/n]"
    return ($r -eq '' -or $r -match '^(s|si|y|yes)$')
}

function Ejecutar-Nativo([string]$titulo, [string]$exe, [string[]]$argumentos, [string]$carpeta) {
    # Los avisos que un programa escribe por stderr (npm, pip) no son errores: PowerShell 5.1 los trataria
    # como tales con 'Stop'. Aqui solo cuenta el codigo de salida.
    Push-Location -LiteralPath $carpeta
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $exe @argumentos
        $codigo = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $anterior
        Pop-Location
    }
    if ($codigo -ne 0) { throw "$titulo fallo (codigo $codigo). Corre el instalador otra vez; si vuelve a fallar, manda el archivo instalacion.log." }
}

function Buscar-Python312 {
    $candidatos = New-Object System.Collections.Generic.List[string]
    $launcher = Buscar-Programa 'py'
    if ($launcher) {
        $r = & $launcher -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $r) { $candidatos.Add([string]($r | Select-Object -First 1)) }
    }
    $p = Buscar-Programa 'python'
    if ($p -and $p -notlike '*\WindowsApps\*') { $candidatos.Add($p) }
    foreach ($f in @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'), (Join-Path $env:ProgramFiles 'Python312\python.exe'))) {
        if (Test-Path -LiteralPath $f) { $candidatos.Add($f) }
    }
    foreach ($c in $candidatos) {
        $v = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -eq '3.12') { return $c }
    }
    return $null
}

function Buscar-Ffmpeg {
    $f = Buscar-Programa 'ffmpeg'
    if ($f) { return $f }
    $paquetes = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $paquetes) {
        $hallado = Get-ChildItem -LiteralPath $paquetes -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hallado) {
            $env:Path = $env:Path + ';' + $hallado.DirectoryName
            return $hallado.FullName
        }
    }
    return $null
}

function Estado-Programas {
    $e = @{}
    $e.Node = Buscar-Programa 'node'
    $e.NodeVersion = $null
    $e.NodeOk = $false
    if ($e.Node) {
        $e.NodeVersion = [string](& $e.Node -v)
        $mayor = 0
        if ([int]::TryParse($e.NodeVersion.TrimStart('v').Split('.')[0], [ref]$mayor)) { $e.NodeOk = ($mayor -ge 20) }
    }
    $e.Npm = Buscar-Programa 'npm.cmd'
    $e.Python = Buscar-Python312
    $e.Ffmpeg = Buscar-Ffmpeg
    $e.Mp3 = $false
    if ($e.Ffmpeg) {
        $codificadores = (& $e.Ffmpeg -hide_banner -encoders 2>$null | Out-String)
        $e.Mp3 = ($codificadores -match 'libmp3lame')
    }
    return $e
}

function Mostrar-Programas($e) {
    if ($e.NodeOk -and $e.Npm) { Ok "Node.js $($e.NodeVersion)" } elseif ($e.Node) { Falta "Node.js $($e.NodeVersion) (se necesita la 20 o mas, con npm)" } else { Falta 'Node.js' }
    if ($e.Python) { Ok "Python 3.12 ($($e.Python))" } else { Falta 'Python 3.12 (las librerias de IA fallan con las versiones mas nuevas)' }
    if ($e.Ffmpeg -and $e.Mp3) { Ok "ffmpeg ($($e.Ffmpeg))" } elseif ($e.Ffmpeg) { Falta 'ffmpeg sin soporte de mp3 (libmp3lame)' } else { Falta 'ffmpeg' }
}

function Instalar-Con-Winget([string]$id, [string]$nombre) {
    $w = Buscar-Programa 'winget'
    if (-not $w) { throw "No encuentro winget (el instalador de programas de Windows). Instala $nombre a mano y vuelve a correr este instalador." }
    Info "Instalando $nombre con winget (puede pedir permiso de Windows; espera)..."
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $w install --id $id -e --silent --accept-package-agreements --accept-source-agreements } finally { $ErrorActionPreference = $anterior }
    Refrescar-Path
}

function Sincronizar-Archivos([string]$destino) {
    # Copia el codigo y las plantillas al destino sin pisar nada que alguien haya editado alla.
    # El manifiesto .instalado.json recuerda el hash de cada archivo tal como se instalo: si el archivo del
    # destino sigue igual, se actualiza; si cambio, es de alguien y se conserva (la version nueva queda al lado).
    $manifiestoRuta = Join-Path $destino '.instalado.json'
    $viejo = $null
    if (Test-Path -LiteralPath $manifiestoRuta) {
        $viejo = (Get-Content -LiteralPath $manifiestoRuta -Raw -Encoding UTF8 | ConvertFrom-Json).archivos
    }
    $nuevo = @{}
    $copiados = 0; $iguales = 0
    $conservados = New-Object System.Collections.Generic.List[string]
    $conflictos = New-Object System.Collections.Generic.List[string]

    foreach ($rel in (Archivos-Del-Editor $Origen)) {
        $o = Join-Path $Origen $rel
        $d = Join-Path $destino $rel
        $hOrigen = Hash-Archivo $o
        $hViejo = $null
        if ($viejo -and $viejo.PSObject.Properties[$rel]) { $hViejo = [string]$viejo.PSObject.Properties[$rel].Value }

        if (-not (Test-Path -LiteralPath $d)) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $d) | Out-Null
            Copy-Item -LiteralPath $o -Destination $d -Force
            $nuevo[$rel] = $hOrigen
            $copiados++
            continue
        }
        if ($script:SoloSiFalta -contains $rel) {
            if ($hViejo) { $nuevo[$rel] = $hViejo }
            $iguales++
            continue
        }
        $hDestino = Hash-Archivo $d
        if ($hDestino -eq $hOrigen) { $nuevo[$rel] = $hOrigen; $iguales++; continue }

        $sinCambiosLocales = $Forzar -or ($hViejo -and $hDestino -eq $hViejo)
        if ($sinCambiosLocales) {
            Copy-Item -LiteralPath $o -Destination $d -Force
            $nuevo[$rel] = $hOrigen
            $copiados++
        } elseif ($hViejo -and $hOrigen -eq $hViejo) {
            # El destino tiene cambios propios y el origen no cambio: no hay nada que ofrecer.
            $nuevo[$rel] = $hViejo
            $conservados.Add($rel)
        } else {
            Copy-Item -LiteralPath $o -Destination ($d + '.nuevo') -Force
            if ($hViejo) { $nuevo[$rel] = $hViejo }
            $conflictos.Add($rel)
        }
    }

    $version = '?'
    $vf = Join-Path $Origen 'VERSION'
    if (Test-Path -LiteralPath $vf) { $version = (Get-Content -LiteralPath $vf -Raw).Trim() }
    $manifiesto = [ordered]@{ version = $version; origen = $Origen; actualizado = (Get-Date).ToString('s'); archivos = $nuevo }
    [IO.File]::WriteAllText($manifiestoRuta, ($manifiesto | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))

    Ok "$copiados archivo(s) nuevos o actualizados, $iguales sin cambios"
    foreach ($c in $conservados) { Info "conservado (lo editaron aqui): $c" }
    foreach ($c in $conflictos) { Aviso "cambio aqui Y en la fuente: se conserva el de aqui y la version nueva queda en ${c}.nuevo" }
}

function Principal {
    $version = '?'
    $vf = Join-Path $Origen 'VERSION'
    if (Test-Path -LiteralPath $vf) { $version = (Get-Content -LiteralPath $vf -Raw).Trim() }
    Write-Host ''
    Write-Host "EDITOR DE ANUNCIOS - instalador (version $version)" -ForegroundColor White
    Write-Host 'Instala o actualiza el editor en este PC. Puedes correrlo otra vez cuando quieras: no toca tus productos, musica ni preferencias (en .env solo pone la conexion con el dash si la bajaste).'

    # ------------------------------------------------------------------ 1. destino
    Titulo '1. Carpeta de instalacion'
    if (-not $Destino) {
        $defecto = 'C:\editor-anuncios'
        if (Test-Path 'D:\') { $defecto = 'D:\editor-anuncios' }
        $Destino = $defecto
        if (-not $Si -and -not $SoloVerificar) {
            $r = Read-Host "  Donde lo instalo? [Enter = $defecto]"
            if ($r) { $Destino = $r.Trim('"') }
        }
    }
    $Destino = [IO.Path]::GetFullPath($Destino).TrimEnd('\')
    $enSitio = ($Destino -ieq $Origen.TrimEnd('\'))
    if ($Destino.StartsWith($Origen.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'La carpeta de instalacion no puede estar dentro de la carpeta del instalador.' }
    Info "Destino: $Destino"
    if ($enSitio) { Info 'Se instala en la misma carpeta del instalador: solo se instalan las dependencias.' }
    if ($Destino -like '*\.claude\worktrees\*') {
        Aviso 'Esa carpeta es un worktree temporal de Claude: se borra al limpiar worktrees y se perderian los videos. Usa otra carpeta.'
        if (-not (Preguntar 'Instalo ahi de todos modos?')) { throw 'Instalacion cancelada.' }
    }
    $sensibles = @($env:OneDrive, [Environment]::GetFolderPath('Desktop')) | Where-Object { $_ }
    foreach ($s in $sensibles) {
        if ($Destino.StartsWith($s.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            Aviso "Esa carpeta esta dentro de ${s}: los videos pesan mucho y la sincronizacion estorba. Mejor una carpeta fuera de OneDrive y del Escritorio (por ejemplo D:\editor-anuncios)."
            if (-not (Preguntar 'Instalo ahi de todos modos?')) { throw 'Instalacion cancelada.' }
        }
    }
    $letra = $Destino.Substring(0, 1)
    $disco = Get-PSDrive -Name $letra -ErrorAction SilentlyContinue
    if ($disco) {
        $libreGb = [math]::Round($disco.Free / 1GB, 1)
        if ($libreGb -lt 8) {
            Aviso "El disco $letra`: tiene solo $libreGb GB libres y la instalacion usa unos 5 GB (mas los videos)."
            if (-not (Preguntar 'Sigo?')) { throw 'Instalacion cancelada.' }
        } else { Ok "Disco $letra`: con $libreGb GB libres" }
    }
    $manifiestoPrevio = Join-Path $Destino '.instalado.json'
    $modo = 'instalar'
    if (Test-Path -LiteralPath $manifiestoPrevio) {
        $m = Get-Content -LiteralPath $manifiestoPrevio -Raw -Encoding UTF8 | ConvertFrom-Json
        $modo = 'actualizar'
        Ok "Ya hay una instalacion aqui (version $($m.version)): se actualiza sin tocar tus datos."
    } elseif (Test-Path -LiteralPath $Destino) {
        Info 'La carpeta ya existe pero sin registro de instalacion: se completa sin borrar nada.'
    }

    # ------------------------------------------------------------------ 2. programas
    Titulo '2. Programas (Node.js, Python 3.12, ffmpeg)'
    $est = Estado-Programas
    Mostrar-Programas $est
    $pendientes = @()
    if (-not ($est.NodeOk -and $est.Npm)) { $pendientes += @{ Id = 'OpenJS.NodeJS.LTS'; Nombre = 'Node.js (LTS)' } }
    if (-not $est.Python) { $pendientes += @{ Id = 'Python.Python.3.12'; Nombre = 'Python 3.12' } }
    if (-not ($est.Ffmpeg -and $est.Mp3)) { $pendientes += @{ Id = 'Gyan.FFmpeg'; Nombre = 'ffmpeg' } }
    if ($pendientes.Count -gt 0) {
        if ($SoloVerificar) {
            foreach ($p in $pendientes) { Info "Se instalaria con winget: $($p.Nombre) ($($p.Id))" }
        } else {
            foreach ($p in $pendientes) {
                if (-not (Preguntar "Falta $($p.Nombre). Lo instalo con winget?")) { throw "Sin $($p.Nombre) el editor no funciona. Instalalo y vuelve a correr este instalador." }
                Instalar-Con-Winget $p.Id $p.Nombre
            }
            $est = Estado-Programas
            Mostrar-Programas $est
            if (-not ($est.NodeOk -and $est.Npm -and $est.Python -and $est.Ffmpeg -and $est.Mp3)) {
                throw 'Windows todavia no ve los programas recien instalados. Cierra esta ventana, abre el instalador otra vez (doble clic) y sigue desde ahi.'
            }
        }
    }
    if ($SoloVerificar) {
        Titulo 'Solo verificar: no se instalo nada'
        if (Test-Path -LiteralPath $manifiestoPrevio) { Ok 'Hay una instalacion en el destino.' } else { Falta 'Todavia no hay instalacion en el destino.' }
        return
    }

    # ------------------------------------------------------------------ 3. archivos
    Titulo '3. Archivos del editor'
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
    try { Start-Transcript -Path (Join-Path $Destino 'instalacion.log') -Append | Out-Null } catch { }
    if ($enSitio) { Ok 'Instalador y carpeta son la misma: nada que copiar.' } else { Sincronizar-Archivos $Destino }

    # ------------------------------------------------------------------ 4. dependencias
    Titulo '4. Dependencias de video (npm)'
    Ejecutar-Nativo 'npm install' $est.Npm @('install', '--no-audit', '--no-fund') $Destino
    Ok 'Remotion y sus paquetes instalados'

    Titulo '5. Entorno de Python propio (.venv) y librerias de IA'
    $venv = Join-Path $Destino '.venv'
    $pyVenv = Join-Path $venv 'Scripts\python.exe'
    $sano = $false
    if (Test-Path -LiteralPath $pyVenv) {
        & $pyVenv -c "import sys" 2>$null
        $sano = ($LASTEXITCODE -eq 0)
        if (-not $sano) { Aviso 'El entorno .venv esta danado: se vuelve a crear.'; Remove-Item -LiteralPath $venv -Recurse -Force }
    }
    if (-not $sano) {
        Ejecutar-Nativo 'crear el entorno de Python' $est.Python @('-m', 'venv', $venv) $Destino
    }
    Ejecutar-Nativo 'pip install' $pyVenv @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements.txt') $Destino
    Ok 'Librerias de Python instaladas (faster-whisper, pymupdf, rembg, pillow, numpy, scipy, requests, yt-dlp, curl_cffi, onnxruntime, tokenizers, rapidocr)'

    if (-not $SinNavegador) {
        Titulo '6. Navegador de Remotion (una sola vez, ~120 MB)'
        Ejecutar-Nativo 'navegador de Remotion' $est.Node @('scripts\remotion.mjs', 'navegador') $Destino
    }
    if (-not $SinModelo) {
        Titulo "7. Modelo de transcripcion '$Modelo' (una sola vez, ~0,5 GB para small)"
        Ejecutar-Nativo 'descarga del modelo de Whisper' $pyVenv @('scripts\transcribir.py', '--solo-descargar', '--modelo', $Modelo) $Destino
    }
    if (-not $SinClip) {
        Titulo '8. Modelos de CLIP para el anuncio automatico (una sola vez, ~300 MB)'
        Ejecutar-Nativo 'descarga de los modelos de CLIP' $pyVenv @('scripts\emparejar_tomas.py', '--solo-descargar') $Destino
    }
    Titulo '9. Efectos de sonido, archivo .env y conexion con el dash'
    if (-not (Test-Path -LiteralPath (Join-Path $Destino 'public\sfx\whoosh.mp3'))) {
        Ejecutar-Nativo 'crear los efectos de sonido' $pyVenv @('scripts\crear_sfx.py') $Destino
    } else { Ok 'Efectos de sonido ya creados' }
    $envRuta = Join-Path $Destino '.env'
    if (-not (Test-Path -LiteralPath $envRuta)) {
        Copy-Item -LiteralPath (Join-Path $Destino 'env-ejemplo.txt') -Destination $envRuta
        Ok 'Creado .env (todavia sin la conexion con el dash)'
    } else { Ok '.env ya existe: se conserva (solo cambia la conexion con el dash si bajaste una nueva)' }
    # La conexion con el dash (conexion-editor.txt, la da el dash): junto al instalador o en Descargas. Por ahi pasan APIMart y la voz; el editor ya no usa la llave de APIMart.
    $conexion = Importar-Conexion $envRuta @($Origen, (Join-Path $env:USERPROFILE 'Downloads'))
    if ($conexion -eq 'no-hay') {
        $textoEnv = ''
        if (Test-Path -LiteralPath $envRuta) { $textoEnv = [IO.File]::ReadAllText($envRuta) }
        if (($textoEnv -match '(?m)^\s*DASH_URL\s*=\s*\S') -and ($textoEnv -match '(?m)^\s*DASH_CLAVE_EDITOR\s*=\s*\S')) { Ok 'El editor ya estaba conectado al dash (DASH_URL y DASH_CLAVE_EDITOR en .env)' }
        else { Aviso 'Falta conectar el editor con el dash: sin eso no genera imagenes, clips ni voz (mira el paso 2 al final).' }
        if ($textoEnv -match '(?m)^\s*APIMART_API_KEY\s*=\s*\S') { Aviso '.env todavia trae APIMART_API_KEY: ya no se usa. Se quita sola al conectar con el archivo del dash (Conectar-Editor.cmd), o borra esa linea tu.' }
        $textoEnv = ''
    }

    # ------------------------------------------------------------------ 9. verificacion
    Titulo '10. Verificacion'
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $Destino
    try { & $pyVenv 'scripts\verificar_instalacion.py'; $codigoVerif = $LASTEXITCODE } finally { Pop-Location; $ErrorActionPreference = $anterior }

    Write-Host ''
    Write-Host "LISTO. Version $version instalada en $Destino" -ForegroundColor Green
    Write-Host ''
    Write-Host 'Lo que falta es tuyo (el verificador de arriba lo marca como FALTA si todavia no esta):'
    Write-Host "  1. Musica: dile a Claude 'crea musica de fondo' (usa scripts\crear_musica.py: pistas propias, sin licencia de terceros) o copia mp3 libres de derechos en $Destino\public\musica\biblioteca\"
    Write-Host "  2. Conexion con el dash (para imagenes, clips y voz con IA): en el dash, menu Herramientas > Editor de anuncios > 'Descargar conexion'; despues doble clic en $Destino\Conectar-Editor.cmd. No la pegues en el chat."
    Write-Host ''
    Write-Host 'Para empezar: abre la app de Claude -> pestana Code -> abrir carpeta -> elige esta carpeta como proyecto:'
    Write-Host "  $Destino"
    Write-Host "Para comprobar que todo funciona: doble clic en $Destino\Probar-Editor.cmd"
    try { Stop-Transcript | Out-Null } catch { }
}

try {
    Principal
    exit 0
} catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
