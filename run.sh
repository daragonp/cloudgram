#!/bin/bash

# Iniciar el bot de Telegram en segundo plano
echo "🚀 Iniciando Bot de Telegram..."
python3 main.py &

# Iniciar la Web Admin (proceso principal)
echo "🌐 Iniciando Web Admin..."
# Usamos gunicorn para producción si está en requirements.txt, si no, usa python3 web_admin.py
python3 web_admin.py