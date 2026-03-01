import os
from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Middleware global para restringir el uso del bot únicamente a los usuarios autorizados
    (definidos en la variable de entorno ADMIN_ID).
    """
    admin_ids_str = os.getenv("ADMIN_ID", "")
    # Permite un solo ID o múltiples separados por comas
    admin_ids = [int(i.strip()) for i in admin_ids_str.split(",") if i.strip().isdigit()]
    
    # Obtenemos el usuario de la actualización (cubre mensajes, callbacks, inline, etc.)
    user = update.effective_user
    
    # Si no hay usuario en el contexto o si no está en la lista de administradores, bloqueamos
    if user and user.id not in admin_ids:
        # Enviar mensaje de rechazo según el tipo de interacción
        if update.message:
            await update.message.reply_text("⛔️ No tienes permiso para usar este bot.")
        elif update.callback_query:
            await update.callback_query.answer("⛔️ No tienes permiso para usar este bot.", show_alert=True)
            
        # Detener la propagación al resto de los handlers
        raise ApplicationHandlerStop()
