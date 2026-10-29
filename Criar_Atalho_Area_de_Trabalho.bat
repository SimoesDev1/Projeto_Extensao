@echo off
chcp 65001 > nul
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -File "%~dp0Criar_Atalho_Area_de_Trabalho.ps1"
echo.
pause
