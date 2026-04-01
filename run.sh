#!/bin/bash

# Función para limpiar procesos al salir
cleanup() {
    echo "🛑 Deteniendo servicios..."
    # Matar el bot de Telegram si está corriendo
    if [ -n "$BOT_PID" ]; then
        kill $BOT_PID 2>/dev/null
        echo "   Bot detenido."
    fi
    # Matar Web Admin si está corriendo
    if [ -n "$WEB_PID" ]; then
        kill $WEB_PID 2>/dev/null
        echo "   Web Admin detenido."
    fi
    exit
}

# Configurar el trap para ejecutar cleanup cuando se presione Ctrl+C
trap cleanup INT TERM

echo "🚀 Iniciando Bot de Telegram..."
# Ejecutamos en background y guardamos el PID
python3 main.py &
BOT_PID=$!

echo "🌐 Iniciando Web Admin (Flask)..."
# Usamos python3 web_admin.py en lugar de Gunicorn para evitar errores de macOS
python3 web_admin.py &
WEB_PID=$!

# Esperamos a que ambos procesos terminen
wait