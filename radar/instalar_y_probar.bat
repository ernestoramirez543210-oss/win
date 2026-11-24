@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Radar de ofertas: instalar y probar ===
python --version >nul 2>nul
if errorlevel 1 (
  echo Python no esta instalado. Instalando con winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python instalado. CIERRA esta ventana y vuelve a abrir este archivo.
  pause
  exit /b
)
echo.
echo [1/3] Instalando requests...
python -m pip install --quiet requests
echo.
echo [2/3] Prueba de logica (sin red)...
python test_logic.py
echo.
echo [3/3] Diagnostico contra Meta (usa el proxy del archivo .env)...
python radar.py --diagnostico
echo.
pause
