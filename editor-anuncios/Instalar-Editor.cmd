@echo off
rem Instala o actualiza el Editor de anuncios en este PC. Doble clic.
rem Las opciones se pasan tal cual: Instalar-Editor.cmd -Si -Destino D:\editor-anuncios
title Editor de anuncios - instalador
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Instalar-Editor.ps1" %*
echo.
pause
