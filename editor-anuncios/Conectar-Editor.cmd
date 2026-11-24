@echo off
rem Conecta este editor con el dash. Doble clic.
rem Lee conexion-editor.txt (lo bajas del dash: menu Herramientas, Editor de anuncios, boton Descargar conexion) de esta carpeta o de Descargas,
rem pasa sus dos lineas a .env, quita la llave vieja de APIMart y borra el archivo bajado. No muestra la llave.
title Editor de anuncios - conectar con el dash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Conectar-Editor.ps1"
echo.
pause
