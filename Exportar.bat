@echo off
cd /d "%~dp0"
title Exportar WhatsApp Sender

echo.
echo ==============================================
echo   WhatsApp Sender - Gerando pacote ZIP
echo ==============================================
echo.

set ZIP_NAME=whatsapp_sender.zip
set ZIP_PATH=%USERPROFILE%\Desktop\%ZIP_NAME%

:: Remove ZIP anterior se existir
if exist "%ZIP_PATH%" del /q "%ZIP_PATH%"

echo Criando pacote em: %ZIP_PATH%
echo (excluindo sessao pessoal, contatos e dependencias)
echo.

powershell -NoProfile -Command ^
  "Compress-Archive -Path @(" ^
  "  'app.py', 'main.py', 'csv_loader.py', 'tracker.py'," ^
  "  'config.json', 'requirements.txt'," ^
  "  'contacts_template.csv'," ^
  "  'Iniciar.bat', 'Iniciar.sh', 'setup.ps1', 'setup.sh'," ^
  "  'templates'," ^
  "  'node\sender.js', 'node\package.json'" ^
  ") -DestinationPath '%ZIP_PATH%' -Force"

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao criar o ZIP.
    pause
    exit /b 1
)

echo.
echo ✓ Arquivo criado com sucesso!
echo   %ZIP_PATH%
echo.
echo ==============================================
echo   Instrucoes para quem receber o ZIP:
echo ==============================================
echo.
echo   Pre-requisitos (instalar uma unica vez):
echo     1. Python  → https://python.org/downloads
echo        (marcar "Add python.exe to PATH")
echo     2. Node.js → https://nodejs.org  (versao LTS)
echo.
echo   Como usar:
echo     1. Extrair o ZIP em qualquer pasta
echo     2. Dar duplo clique em Iniciar.bat
echo     3. O navegador abrira automaticamente
echo     4. Na 1a vez: escanear o QR com o celular
echo ==============================================
echo.

:: Abrir pasta onde o ZIP foi salvo
explorer /select,"%ZIP_PATH%"

pause
