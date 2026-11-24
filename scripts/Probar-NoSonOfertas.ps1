# Prueba de Test-NoEsOferta (Refresh-Headless.ps1): que pestanas del libro NO se toman por ofertas. Sin red.
#
#   powershell -File scripts\Probar-NoSonOfertas.ps1
#
# Una pestana de medicion como 'order bump sermones' (Colombia) tiene que poder mostrarse sin que el refresco la anuncie como OFERTA NUEVA ni la intente bajar como hoja de
# ventas. Las pestanas de ventas de verdad siguen siendo ofertas. Sale con codigo 1 si algo falla. Solo ASCII a proposito.
$Script = Join-Path $PSScriptRoot 'Refresh-Headless.ps1'
$ErrorActionPreference = 'Stop'
$src = [IO.File]::ReadAllText($Script)
# Las dos listas y la funcion se leen del propio script: si alguien cambia una, la prueba mide lo que de verdad corre.
foreach ($patron in '(?m)^\$NO_SON_OFERTAS\s*=.*$', '(?m)^\$PREFIJOS_NO_OFERTA\s*=.*$') {
    $m = [regex]::Match($src, $patron)
    if (-not $m.Success) { throw "no encontre $patron en Refresh-Headless.ps1" }
    . ([scriptblock]::Create($m.Value))
}
$m = [regex]::Match($src, '(?s)(?:^|\r?\n)(function Test-NoEsOferta\b.*?\r?\n\})\r?\n')
if (-not $m.Success) { throw 'no encontre Test-NoEsOferta en Refresh-Headless.ps1' }
. ([scriptblock]::Create($m.Groups[1].Value))
# Y los dos sitios donde se decide si una pestana es oferta tienen que usarla.
$usos = [regex]::Matches($src, 'Test-NoEsOferta \$').Count

$fallos = 0
function Afirma([bool]$c, [string]$m) { if ($c) { "  ok  $m" } else { $script:fallos++; "  FALLA  $m" } }

Afirma (Test-NoEsOferta 'order bump sermones') "'order bump sermones' no es oferta"
Afirma (Test-NoEsOferta 'ORDER BUMP SERMONES') 'en mayusculas tampoco'
Afirma (Test-NoEsOferta '  Order  Bump   sermones ') 'con espacios de mas tampoco'
Afirma (Test-NoEsOferta 'order bump recetas') 'otro order bump futuro tampoco (criterio general)'
Afirma (Test-NoEsOferta 'zapdata log') "'zapdata log' sigue sin ser oferta"
Afirma (Test-NoEsOferta 'reparaciones') "'reparaciones' sigue sin ser oferta"
Afirma (-not (Test-NoEsOferta 'zapdata sermones biblicos')) 'la pestana de ventas de Sermones SI es oferta'
Afirma (-not (Test-NoEsOferta 'SALMOS')) 'SALMOS si es oferta'
Afirma (-not (Test-NoEsOferta 'zapdata order')) "una oferta que solo contiene 'order' sigue siendo oferta"
Afirma (-not (Test-NoEsOferta '')) 'un nombre vacio no se marca (lo descartan los otros filtros)'
Afirma ($usos -ge 2) ("Refresh-Headless.ps1 usa Test-NoEsOferta en las pestanas visibles y en las del registro del webhook ({0} usos)" -f $usos)

if ($fallos) { Write-Output ("FALLA: {0} comprobacion(es)" -f $fallos); exit 1 }
Write-Output 'PASA: todas las comprobaciones'
exit 0
