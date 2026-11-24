# Maneja el CORREDOR DE ANUNCIOS del servidor (VPS) desde este PC con un solo comando: ver como va, PAUSARLO, reanudarlo, encender o apagar su reloj, lanzar una pasada y ver el registro.
# Habla por SSH con /opt/anuncios/corredor-ctl (ver editor-anuncios/vps/LEEME.md, seccion "Corredor de anuncios"). Solo lo que se lista abajo; nada se escribe en ninguna otra parte.
#
#   & .\scripts\Corredor-Vps.ps1                         # estado: que hace ahora, si esta pausado, cuando es la proxima pasada, lo ultimo que paso, lo gastado hoy
#   & .\scripts\Corredor-Vps.ps1 pausar                  # PAUSA: no empieza nada nuevo; la etapa en curso termina y se detiene ahi (se retoma al reanudar)
#   & .\scripts\Corredor-Vps.ps1 pausar --ya             # PAUSA y corta lo que esta haciendo ahora mismo (esa etapa se repite al reanudar)
#   & .\scripts\Corredor-Vps.ps1 reanudar                # quita la pausa (vuelve a poner el reloj solo si estaba encendido antes de pausar)
#   & .\scripts\Corredor-Vps.ps1 encender -Si            # enciende el reloj (cada hora a los :40). Es una decision del dueno: pide -Si a proposito
#   & .\scripts\Corredor-Vps.ps1 apagar                  # apaga el reloj
#   & .\scripts\Corredor-Vps.ps1 correr --simular        # una pasada de prueba: mira la cola y dice que haria, sin tocar nada. Tambien: --solo-cola, --max-pedidos N, --tope-usd X,
#                                                        #   --pedido PROYECTO ANUNCIO [--rehacer], --sin-gancho
#   & .\scripts\Corredor-Vps.ps1 logs 80                 # las ultimas 80 lineas del registro de hoy
#   & .\scripts\Corredor-Vps.ps1 reintentar PROYECTO AD4 # deja volver a intentar un pedido que quedo en error
#
# Cada llamada al servidor tiene tiempo maximo (-SegundosSsh, 40) y se reintenta una vez; si el servidor no contesta, el script lo dice y NO deja nada a medias (todas estas ordenes son
# de un solo paso). Los argumentos solo pueden llevar letras, numeros y . _ : = / - (nada de espacios, comillas ni signos de shell). Se llama con & (no con "powershell -File").
# -RutaSsh es solo para probar el script en esta misma maquina contra un ssh de mentira (scripts/probar-scripts-corredor.cjs).
# ASCII-only, PS 5.1 safe.
param(
    [Parameter(Position = 0)][string]$Accion = 'estado',
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Argumentos,
    [switch]$Si,
    [string]$Vps = 'root@203.0.113.10',
    [string]$LlaveSsh = (Join-Path $HOME '.ssh\radar_vps'),
    [int]$SegundosSsh = 40,
    [string]$RutaSsh = ''
)
$ErrorActionPreference = 'Stop'
$ayudanteSsh = Join-Path $PSScriptRoot '_ssh-seguro.ps1'
if (-not (Test-Path $ayudanteSsh)) { throw "Falta $ayudanteSsh (esta en la misma carpeta que este script; actualiza el repositorio con git pull)." }
. $ayudanteSsh

$acciones = @('estado', 'pausar', 'reanudar', 'encender', 'apagar', 'correr', 'logs', 'reintentar')
if ($acciones -notcontains $Accion) { throw ("Accion desconocida: '$Accion'. Las que hay: " + ($acciones -join ', ') + '.') }
if ($Accion -eq 'encender' -and -not $Si) { throw 'Encender el reloj del corredor es una decision del dueno: vuelve a correr con -Si  (& .\scripts\Corredor-Vps.ps1 encender -Si). Para solo mirar usa: estado.' }
$lista = @($Argumentos | Where-Object { $_ -ne $null -and $_ -ne '' })
foreach ($a in $lista) {
    if ($a -notmatch '^[A-Za-z0-9._:=/\-]{1,64}$') { throw "Argumento no permitido: '$a' (solo letras, numeros y . _ : = / -)." }
}
if ($lista.Count -gt 10) { throw 'Demasiados argumentos.' }

$ssh = $RutaSsh
if (-not $ssh) {
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if (-not $ssh -and (Test-Path 'C:\Program Files\Git\usr\bin\ssh.exe')) { $ssh = 'C:\Program Files\Git\usr\bin\ssh.exe' }
}
if (-not $ssh) { throw 'No encuentro ssh.exe (instala el cliente OpenSSH de Windows o Git for Windows).' }
if (-not (Test-Path $LlaveSsh)) { throw "Falta la llave SSH del servidor: $LlaveSsh" }
$con = New-SshConexion -Ssh $ssh -Llave $LlaveSsh -Servidor $Vps -Segundos $SegundosSsh

$comando = '/opt/anuncios/corredor-ctl ' + $Accion
if ($lista.Count -gt 0) { $comando += ' ' + ($lista -join ' ') }
$r = Invoke-Ssh $con $comando -Que ("corredor-ctl $Accion")
foreach ($linea in @($r.Salida) + @($r.Error)) { Write-Output ([string]$linea) }
if (-not $r.Ok) { throw ((Get-FalloSsh $r) + ' (Ninguna de estas ordenes deja nada a medias: puedes repetirla.)') }
