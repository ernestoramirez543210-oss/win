# Arma un .zip con el instalador del Editor de anuncios para llevarlo a otro PC (el del editor).
#
#   powershell -ExecutionPolicy Bypass -File Empaquetar-Editor.ps1 [-Salida <ruta.zip>]
#
# Por defecto deja _paquete\editor-anuncios-<version>.zip dentro de esta carpeta. El zip lleva solo codigo, plantillas
# y guias (nunca claves, productos, videos ni musica). En el otro PC: descomprimir en cualquier carpeta y doble clic en
# Instalar-Editor.cmd. Lee INSTALAR.txt dentro del zip. Todo en ASCII a proposito (PowerShell 5.1 y los acentos).
param(
    [string]$Salida = ''
)

$ErrorActionPreference = 'Stop'
$Origen = $PSScriptRoot
. (Join-Path $Origen 'Editor-Comun.ps1')
# Solo se usa ZipArchive (ensamblado System.IO.Compression): existe igual en Windows PowerShell 5.1 y en PowerShell 7, el que corre
# en CI (Linux) cuando Build-Suite.ps1 arma el .zip de la pagina del dash. ZipFile y ZipFileExtensions viven en otro ensamblado.
Add-Type -AssemblyName System.IO.Compression

$version = '?'
$vf = Join-Path $Origen 'VERSION'
if (Test-Path -LiteralPath $vf) { $version = (Get-Content -LiteralPath $vf -Raw).Trim() }
if (-not $Salida) { $Salida = Join-Path (Join-Path $Origen '_paquete') "editor-anuncios-$version.zip" }
$Salida = [IO.Path]::GetFullPath($Salida)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Salida) | Out-Null
if (Test-Path -LiteralPath $Salida) { Remove-Item -LiteralPath $Salida -Force }

$archivos = Archivos-Del-Editor $Origen
$flujo = [IO.File]::Open($Salida, [IO.FileMode]::Create, [IO.FileAccess]::ReadWrite)
$zip = New-Object IO.Compression.ZipArchive($flujo, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($rel in $archivos) {
        # Entradas con / (no con \) para que el zip abra igual en cualquier sistema.
        $entrada = $zip.CreateEntry("editor-anuncios/$rel", [IO.Compression.CompressionLevel]::Optimal)
        $bytes = [IO.File]::ReadAllBytes((Join-Path $Origen $rel))
        $destino = $entrada.Open()
        try { $destino.Write($bytes, 0, $bytes.Length) } finally { $destino.Dispose() }
    }
} finally {
    $zip.Dispose()
    $flujo.Dispose()
}

$kb = [math]::Round((Get-Item -LiteralPath $Salida).Length / 1KB)
Write-Host "Paquete listo: $Salida"
Write-Host "  version $version, $($archivos.Count) archivos, $kb KB"
Write-Host 'Mandaselo al editor (Drive o WhatsApp). En su PC: descomprimir, doble clic en Instalar-Editor.cmd y seguir INSTALAR.txt.'
