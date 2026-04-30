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
# Usamos gunicorn para el entorno productivo
gunicorn app:app &
WEB_PID=$!

echo "🐴 Iniciando worker de Celery si Redis está configurado..."
if [ -n "$REDIS_URL" ] || [ -n "$REDIS_BROKER_URL" ] || [ -n "$REDIS_URI" ]; then
    celery -A celery_app.celery worker --loglevel=info &
    WORKER_PID=$!
    echo "   Worker Celery iniciado (PID: $WORKER_PID)."
else
    echo "   REDIS no configurado, worker Celery no iniciado."
fi

# Esperamos a que ambos procesos terminen
wait