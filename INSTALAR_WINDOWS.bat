@echo off
rem Instalador de VideoEditor para Windows: doble clic aqui (con el ZIP ya descomprimido).
rem Ejecuta scripts\instalar_windows.ps1 sin cambiar la configuracion de PowerShell del equipo.
title Instalador de VideoEditor
if not exist "%~dp0scripts\instalar_windows.ps1" (
  echo Primero descomprime el ZIP: clic derecho ^> "Extraer todo", y ejecuta este archivo desde la carpeta extraida.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\instalar_windows.ps1"
echo.
pause
