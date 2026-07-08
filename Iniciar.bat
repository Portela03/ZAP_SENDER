@echo off
cd /d "%~dp0"
title WhatsApp Sender

echo.
echo ==============================================
echo   WhatsApp Sender - Iniciando...
echo ==============================================
echo.

:: Localiza o Python (tenta py launcher, depois python, depois caminho direto)
set PYTHON=
where py >nul 2>&1 && set PYTHON=py
if "%PYTHON%"=="" (
    python -c "print()" >nul 2>&1 && set PYTHON=python
)
if "%PYTHON%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
        set PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
    )
)
if "%PYTHON%"=="" (
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%d\python.exe" set PYTHON=%%d\python.exe
    )
)
if "%PYTHON%"=="" (
    echo [ERRO] Python nao encontrado.
    echo Execute setup.ps1 primeiro, ou instale Python em https://python.org
    echo.
    pause
    exit /b 1
)

:: Verifica Flask
"%PYTHON%" -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Instalando dependencias Python...
    "%PYTHON%" -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo [ERRO] Falha ao instalar dependencias.
        pause
        exit /b 1
    )
)

:: Verifica node_modules
if not exist "node\node_modules" (
    echo [INFO] Instalando dependencias Node.js...
    cd node
    npm install
    cd ..
)

echo O navegador abrira automaticamente em http://127.0.0.1:5000
echo Para encerrar: feche esta janela ou pressione Ctrl+C
echo.

"%PYTHON%" app.py

if errorlevel 1 (
    echo.
    echo [ERRO] O servidor encerrou com erro.
    pause
)
