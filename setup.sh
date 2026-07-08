#!/usr/bin/env bash
# ===========================================
#  WhatsApp Sender - Setup para Termux (Android)
#  Execute uma única vez após instalar o Termux
# ===========================================

set -e

echo ""
echo "==========================================="
echo "  WhatsApp Sender - Configuração Inicial"
echo "==========================================="
echo ""

echo "[1/5] Atualizando pacotes do Termux..."
pkg update -y && pkg upgrade -y

echo ""
echo "[2/5] Instalando Python e Node.js..."
pkg install python nodejs -y

echo ""
echo "[3/5] Instalando dependências Python..."
pip install -r requirements.txt

echo ""
echo "[4/5] Instalando dependências Node.js (pode demorar alguns minutos)..."
cd node && npm install && cd ..

echo ""
echo "[5/5] Pronto! ✓"
echo ""
echo "==========================================="
echo "  Como usar:"
echo ""
echo "  1. Edite config.json com seu template de mensagem"
echo "  2. Preencha contacts.csv com seus contatos"
echo "     (use contacts_template.csv como exemplo)"
echo ""
echo "  3. Carregar contatos:"
echo "     python main.py --load contacts.csv"
echo ""
echo "  4. Iniciar envio (primeira vez mostra QR):"
echo "     python main.py --send"
echo ""
echo "  Outros comandos:"
echo "     python main.py --status   # ver progresso"
echo "     python main.py --retry    # retentar falhas"
echo "==========================================="
echo ""
