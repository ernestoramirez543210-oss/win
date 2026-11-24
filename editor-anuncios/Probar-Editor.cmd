@echo off
rem Prueba de punta a punta del editor con material de mentira (no gasta nada de APIMart). Doble clic.
rem Opciones: Probar-Editor.cmd --sin-render   (tarda ~7 minutos en vez de ~12)
title Editor de anuncios - prueba rapida
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Falta instalar el editor: haz doble clic en Instalar-Editor.cmd primero.
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\prueba_rapida.py %*
echo.
pause
