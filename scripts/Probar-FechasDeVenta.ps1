# Prueba de la lectura de fechas de las hojas de ventas (Convert-SaleDate) y de la limpieza del CSV exportado (Remove-FilasVacias), ambas en scripts/_ventas-comun.ps1.
#
#   powershell -File scripts\Probar-FechasDeVenta.ps1
#
# Sale con codigo 1 si algo falla. Solo ASCII a proposito (PS 5.1 lee un .ps1 sin BOM como ANSI): los espacios duros se arman con [char].
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_ventas-comun.ps1')

$script:fallos = 0
function Prueba([string]$nombre, [scriptblock]$cuerpo) {
    try { & $cuerpo; Write-Output ('  ok  ' + $nombre) }
    catch { $script:fallos++; Write-Output ('  FALLA  ' + $nombre + ' -> ' + $_.Exception.Message) }
}
function Afirma([bool]$cond, [string]$msg) { if (-not $cond) { throw $msg } }

$nb  = [string][char]0x00A0   # espacio duro
$nnb = [string][char]0x202F   # espacio duro fino: el que ICU mete entre la hora y el AM/PM
function Fecha([string]$txt) {
    $d = Convert-SaleDate $txt
    if ($null -eq $d) { return $null }
    return $d.ToString('yyyy-MM-dd HH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
}
# (texto, esperado). Lista de pares y no un hash: el hash de PowerShell no distingue mayusculas ('pm' y 'PM' serian la misma llave).
function Casos([string]$titulo, $pares) {
    Prueba $titulo {
        foreach ($p in $pares) {
            $got = Fecha $p[0]
            $vis = ([string]$p[0]).Replace($nb, '<NBSP>').Replace($nnb, '<NNBSP>')
            Afirma ($got -eq $p[1]) ("'" + $vis + "' dio " + $(if ($null -eq $got) { '$null' } else { $got }) + ' y se esperaba ' + $(if ($null -eq $p[1]) { '$null' } else { $p[1] }))
        }
    }
}

Write-Output 'Convert-SaleDate'
Casos 'los dos textos de Ecuador de la tarea' @(
    ,@('28/10/26 9:20 p. m.', '2026-10-28 21:20:00')
    ,@('4/10/26 9:36 a. m.',  '2026-10-04 09:36:00')
)
Casos 'bordes de la hora de 12 h: 12 a. m. es medianoche y 12 p. m. es mediodia' @(
    ,@('4/10/26 12:08 a. m.',  '2026-10-04 00:08:00')
    ,@('4/10/26 12:08 p. m.',  '2026-10-04 12:08:00')
    ,@('4/10/26 1:00 a. m.',   '2026-10-04 01:00:00')
    ,@('4/10/26 3:10 p. m.',   '2026-10-04 15:10:00')
    ,@('14/10/26 11:59 p. m.', '2026-10-14 23:59:00')
    ,@('14/10/26 10:05 a. m.', '2026-10-14 10:05:00')
)
Casos 'el sufijo puede venir como "p.m.", "PM", "P. M.", pegado a la hora o con espacio duro' @(
    ,@('28/10/26 9:20 PM',               '2026-10-28 21:20:00')
    ,@('28/10/26 9:20 pm',               '2026-10-28 21:20:00')
    ,@('28/10/26 9:20 p.m.',             '2026-10-28 21:20:00')
    ,@('28/10/26 9:20 P. M.',            '2026-10-28 21:20:00')
    ,@('28/10/26 9:20p. m.',             '2026-10-28 21:20:00')
    ,@(('28/10/26 9:20' + $nb + 'p.' + $nb + 'm.'),   '2026-10-28 21:20:00')
    ,@(('28/10/26 9:20' + $nnb + 'p.' + $nnb + 'm.'), '2026-10-28 21:20:00')
    ,@(('28/10/26' + $nb + '9:20' + $nb + 'p. m.'),   '2026-10-28 21:20:00')
    ,@('  28/10/26 9:20 p. m.  ',        '2026-10-28 21:20:00')
)
Casos 'con segundos y con ano de 4 cifras' @(
    ,@('28/10/26 9:20:15 p. m.',   '2026-10-28 21:20:15')
    ,@('28/10/2026 9:20 p. m.',    '2026-10-28 21:20:00')
    ,@('28/10/2026 9:20:15 a. m.', '2026-10-28 09:20:15')
    ,@('2026-10-28 9:20:15 p. m.', '2026-10-28 21:20:15')
)
Casos 'los formatos de siempre siguen dando lo mismo (Sheets, ISO, serial de Excel)' @(
    ,@('24/6/2026 9:30:25',   '2026-06-24 09:30:25')
    ,@('10/8/2026 9:56:53',   '2026-08-10 09:56:53')
    ,@('10/08/2026 09:56:53', '2026-08-10 09:56:53')
    ,@('18/8/2026 17:59:36',  '2026-08-18 17:59:36')
    ,@('7/4/2026 21:19:27',   '2026-04-07 21:19:27')
    ,@('24/6/2026',           '2026-06-24 00:00:00')
    ,@('2026-07-14 17:55:06', '2026-07-14 17:55:06')
    ,@('2026-07-14',          '2026-07-14 00:00:00')
    ,@('24/6/26 9:30:25',     '2026-06-24 09:30:25')
    ,@('24/6/26',             '2026-06-24 00:00:00')
    ,@('24-6-2026',           '2026-06-24 00:00:00')
    ,@('46091.637708333335',  '2026-03-10 15:18:18')
)
Casos 'lo que no es una fecha sigue siendo $null (no se inventa una hora)' @(
    ,@('',                      $null)
    ,@('   ',                   $null)
    ,@('no es fecha',           $null)
    ,@('28/10/26 25:20 p. m.',  $null)
    ,@('28/10/26 0:20 p. m.',   $null)
    ,@('28/10/26 13:20 p. m.',  $null)
    ,@('31/2/26 9:20 p. m.',    $null)
    ,@('32/10/26 9:20 p. m.',   $null)
    ,@('28/13/26 9:20 p. m.',   $null)
    ,@('28/10/26 p. m.',        $null)
)

Write-Output 'Remove-FilasVacias'
Prueba 'quita las filas que son solo comas y deja las demas intactas' {
    $o = Remove-FilasVacias ("A,B,C`r`n1,2,3`r`n,,`r`n4,5,6`r`n`r`n")
    $f = @($o | ConvertFrom-Csv)
    Afirma ($f.Count -eq 2) ('esperaba 2 filas y dio ' + $f.Count)
    Afirma ($f[0].A -eq '1' -and $f[1].C -eq '6') 'cambio el contenido de las filas con datos'
}
Prueba 'una fila con UNA sola celda llena no es vacia' {
    $f = @((Remove-FilasVacias ("A,B,C`n,,x`n1,2,3`n")) | ConvertFrom-Csv)
    Afirma ($f.Count -eq 2) ('esperaba 2 filas y dio ' + $f.Count)
}
Prueba 'dentro de un campo entre comillas con varias lineas no toca nada, ni una linea de solo comas' {
    $o = Remove-FilasVacias ("A,B,C`n1,`"linea1`n,,,`nlinea3`",3`n,,`n7,8,9`n")
    $f = @($o | ConvertFrom-Csv)
    Afirma ($f.Count -eq 2) ('esperaba 2 filas y dio ' + $f.Count)
    Afirma ($f[0].B -match ',,,') 'perdio la linea de comas que iba dentro del campo'
}
Prueba 'un texto sin filas vacias queda con las mismas filas' {
    $f = @((Remove-FilasVacias ("A,B`n1,2`n3,4")) | ConvertFrom-Csv)
    Afirma ($f.Count -eq 2) ('esperaba 2 filas y dio ' + $f.Count)
}

if ($script:fallos) { Write-Output ('FALLAN ' + $script:fallos); exit 1 }
Write-Output 'Todo bien.'
exit 0
