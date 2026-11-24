# Corrida programada del radar de ofertas (la lanza el Programador de tareas de Windows, tarea "RadarOfertas").
# 3 busquedas por corrida, hasta 3 ofertas registradas, nota minima 7, sin tope diario. Todo lo que imprime va a logs\ con las
# credenciales del proxy ocultas. No usa tokens de Claude: es Python + Meta + la planilla (+ Gemini si hay clave).
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$logs = Join-Path $dir 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
Get-ChildItem $logs -Filter 'radar_*.log' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force
$log = Join-Path $logs ('radar_' + (Get-Date -Format 'yyyyMMdd') + '.log')
Add-Content -Path $log -Encoding utf8 -Value ('=== ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ===')
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8     # sin esto las tildes salen como "serm├Áes" en el log
& $py -u radar.py --max-kw 3 --max-ofertas 3 2>&1 |
    ForEach-Object { $_.ToString() -replace '//[^/@\s''"]*@[^/\s''")]*', '//***' } |
    Add-Content -Path $log -Encoding utf8
