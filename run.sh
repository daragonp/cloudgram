#!/bin/bash
echo "🚀 Iniciando Bot de Telegram..."
python3 main.py &

echo "🌐 Iniciando Web Admin con Gunicorn..."
# Gunicorn es más robusto para Railway
gunicorn --bind 0.0.0.0:$PORT web_admin:app
