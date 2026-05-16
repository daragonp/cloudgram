import os
import logging
from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

logger = logging.getLogger(__name__)


async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Middleware global para restringir el uso del bot únicamente a los usuarios
    autorizados (definidos en la variable de entorno ADMIN_ID).

    Política: DENY-BY-DEFAULT.
      • Sin usuario identificable (canales, edited posts, etc.) → se bloquea.
      • Usuario no presente en ADMIN_ID → se bloquea con aviso.
      • Sólo si el usuario está en la lista se permite continuar.
    """
    admin_ids_str = os.getenv("ADMIN_ID", "")
    # Permite un solo ID o múltiples separados por comas
    admin_ids = {int(i.strip()) for i in admin_ids_str.split(",") if i.strip().isdigit()}

    if not admin_ids:
        logger.warning("auth_middleware: ADMIN_ID no configurado. Bloqueando todo el tráfico.")
        raise ApplicationHandlerStop()

    user = update.effective_user

    # DENY-BY-DEFAULT: si no podemos identificar al usuario, bloqueamos.
    if not user or user.id not in admin_ids:
        try:
            if update.message:
                await update.message.reply_text("⛔️ No tienes permiso para usar este bot.")
            elif update.callback_query:
                await update.callback_query.answer(
                    "⛔️ No tienes permiso para usar este bot.", show_alert=True
                )
        except Exception:
            # Si no podemos avisar (canal, etc.) lo registramos en silencio.
            logger.debug("auth_middleware: no se pudo notificar al usuario rechazado.")

        raise ApplicationHandlerStop()
