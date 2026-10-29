@echo off
chcp 65001 > nul
title Doce como Mel - Gestão Financeira
cls

echo =======================================================
echo          DOCE COMO MEL - GESTÃO FINANCEIRA
echo =======================================================
echo.
echo Iniciando o sistema localmente...
echo O navegador será aberto automaticamente em alguns instantes.
echo.
echo (Para encerrar o sistema, basta fechar esta janela)
echo.

cd /d "%~dp0"

REM Garante a criacao do atalho na Area de Trabalho
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Criar_Atalho_Area_de_Trabalho.ps1" > nul 2>&1

if not exist "%~dp0venv\Scripts\python.exe" (
    echo.
    echo =======================================================
    echo [ERRO] O ambiente virtual Python nao foi encontrado!
    echo Pasta esperada: %~dp0venv\Scripts\python.exe
    echo Certifique-se de que a pasta 'venv' foi copiada junto.
    echo =======================================================
    echo.
    pause
    exit /b 1
)

REM Abre a pagina no navegador padrao
start "" "http://127.0.0.1:5000"

REM Executa a aplicacao
"%~dp0venv\Scripts\python.exe" "%~dp0run.py"

if %ERRORLEVEL% neq 0 (
    echo.
    echo [AVISO] Ocorreu uma interrupcao no sistema.
    pause
)
