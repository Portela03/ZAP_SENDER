# ===========================================
#  WhatsApp Sender - Setup para Windows
#  Execute uma única vez no PowerShell:
#  .\setup.ps1
# ===========================================

Write-Host ""
Write-Host "==========================================="
Write-Host "  WhatsApp Sender - Configuracao Windows"
Write-Host "==========================================="
Write-Host ""

# Verificar Python (testa execucao real, ignora atalho falso da Microsoft Store)
Write-Host "[1/4] Verificando Python..."
$pyTest = & python -c "print('ok')" 2>&1
if ($LASTEXITCODE -ne 0 -or ($pyTest -notmatch "ok")) {
    Write-Host ""
    Write-Host "[ERRO] Python nao encontrado ou nao funcional."
    Write-Host "  1. Instale em: https://python.org/downloads"
    Write-Host "  2. Durante a instalacao, marque: 'Add python.exe to PATH'"
    Write-Host "  3. Feche e reabra o PowerShell e rode .\setup.ps1 novamente."
    exit 1
}
$pyVersion = python --version
Write-Host "  OK: $pyVersion"

# Verificar Node.js
Write-Host ""
Write-Host "[2/4] Verificando Node.js..."
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "[ERRO] Node.js nao encontrado."
    Write-Host "  Instale em: https://nodejs.org  (versao LTS recomendada)"
    exit 1
}
$nodeVersion = node --version
Write-Host "  OK: Node.js $nodeVersion"

# Instalar dependencias Python
Write-Host ""
Write-Host "[3/4] Instalando dependencias Python..."
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERRO] Falha ao instalar dependencias Python."
    exit 1
}

# Instalar dependencias Node.js
Write-Host ""
Write-Host "[4/4] Instalando dependencias Node.js (pode demorar alguns minutos)..."
Push-Location node
npm install
$npmExit = $LASTEXITCODE
Pop-Location
if ($npmExit -ne 0) {
    Write-Host "[ERRO] Falha ao instalar dependencias Node.js."
    exit 1
}

Write-Host ""
Write-Host "==========================================="
Write-Host "  Pronto!"
Write-Host ""
Write-Host "  Como usar:"
Write-Host ""
Write-Host "  1. Edite config.json com seu template"
Write-Host "  2. Preencha contacts.csv com seus contatos"
Write-Host ""
Write-Host "  3. Carregar contatos:"
Write-Host "     python main.py --load contacts.csv"
Write-Host ""
Write-Host "  4. Iniciar envio:"
Write-Host "     python main.py --send"
Write-Host "     (primeira vez mostra QR - escaneie com celular)"
Write-Host ""
Write-Host "  Outros:"
Write-Host "     python main.py --status"
Write-Host "     python main.py --retry"
Write-Host "==========================================="
Write-Host ""
