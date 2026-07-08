#!/usr/bin/env bash
# WhatsApp Sender — Launcher para Android/Termux e Linux
set -e

cd "$(dirname "$0")"

echo ""
echo "=============================================="
echo "  WhatsApp Sender - Iniciando..."
echo "=============================================="
echo ""

# Verifica Flask
python -c "import flask" 2>/dev/null || {
  echo "[INFO] Instalando dependencias Python..."
  pip install -r requirements.txt
}

# Verifica node_modules
if [ ! -d "node/node_modules" ]; then
  echo "[INFO] Instalando dependencias Node.js..."
  cd node && npm install && cd ..
fi

echo "Acesse no navegador: http://127.0.0.1:5000"
echo "Para encerrar: Ctrl+C"
echo ""

python app.py
