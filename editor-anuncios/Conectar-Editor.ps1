# Conecta este editor con el dash (lo abre Conectar-Editor.cmd con doble clic).
#
# Busca conexion-editor.txt (se baja del dash: menu Herramientas > Editor de anuncios > boton "Descargar conexion") en esta carpeta y en Descargas, pasa sus
# dos lineas (DASH_URL y DASH_CLAVE_EDITOR) al archivo .env de esta carpeta, quita la linea vieja APIMART_API_KEY (el editor ya no la usa: imagenes, clips,
# texto y voz pasan por el dash) y borra el archivo bajado. No hace nada mas y nunca muestra la llave. Todo en ASCII a proposito (PowerShell 5.1).
#
# Codigos de salida: 0 conectado | 1 el archivo no es una conexion valida (no se toco nada) | 2 no hay archivo de conexion
$ErrorActionPreference = 'Stop'
$Aqui = $PSScriptRoot
. (Join-Path $Aqui 'Editor-Comun.ps1')
$envRuta = Join-Path $Aqui '.env'
$descargas = Join-Path $env:USERPROFILE 'Downloads'
Write-Host ''
Write-Host 'EDITOR DE ANUNCIOS - conectar con el dash' -ForegroundColor White
try {
    $r = Importar-Conexion $envRuta @($Aqui, $descargas)
} catch {
    Write-Host "ERROR: no pude escribir la conexion en $envRuta ($($_.Exception.GetType().Name)). Cierra el Bloc de notas si tiene .env abierto y repite." -ForegroundColor Red
    exit 1
}
if ($r -eq 'importada') {
    Write-Host ''
    Write-Host 'Listo. Para comprobarlo (no gasta nada):  .venv\Scripts\python scripts\apimart.py saldo'
    exit 0
}
if ($r -eq 'invalida') { exit 1 }
Write-Host "  [falta] No encontre conexion-editor.txt ni en $Aqui ni en $descargas." -ForegroundColor Red
Write-Host '          Bajalo del dash (menu Herramientas > Editor de anuncios > tarjeta "Conectar este editor" > boton "Descargar conexion") y vuelve a hacer doble clic aqui.'
exit 2
