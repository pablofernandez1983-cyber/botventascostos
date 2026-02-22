import os
import logging
import re
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────
# Configuración desde variables de entorno
# ─────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]  # contenido del JSON como string

# ─────────────────────────────────────────
# Setup de logging
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# Setup de Gemini
# ─────────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash-lite")

# ─────────────────────────────────────────
# Setup de Google Sheets
# ─────────────────────────────────────────
import json

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).sheet1
    return sheet

# ─────────────────────────────────────────
# Función principal: procesar mensaje
# ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.message.text
    usuario = update.message.from_user.first_name or update.message.from_user.username or "Desconocido"
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Prompt para Gemini
    prompt = f"""Analizá el siguiente mensaje sobre una operación comercial y respondé ÚNICAMENTE en este formato exacto:

CLASIFICACION: VENTA o COSTO
MONTO: solo el número (sin símbolo de moneda), o "no especificado" si no se menciona

Mensaje: "{mensaje}"

Respondé solo con las dos líneas del formato, sin explicaciones adicionales."""

    try:
        respuesta_gemini = model.generate_content(prompt)
        respuesta_texto = respuesta_gemini.text.strip()

        # Parsear respuesta
        clasificacion = "DESCONOCIDO"
        monto = "no especificado"

        for linea in respuesta_texto.splitlines():
            linea = linea.strip()
            if linea.upper().startswith("CLASIFICACION:"):
                clasificacion = linea.split(":", 1)[1].strip().upper()
            elif linea.upper().startswith("MONTO:"):
                monto = linea.split(":", 1)[1].strip()

        # Guardar en Google Sheets
        sheet = get_sheet()
        sheet.append_row([fecha, usuario, mensaje, clasificacion, monto])

        # Armar respuesta para Telegram
        if clasificacion == "VENTA":
            emoji = "💰"
            texto_clasificacion = "venta"
        elif clasificacion == "COSTO":
            emoji = "🛒"
            texto_clasificacion = "costo/compra"
        else:
            emoji = "❓"
            texto_clasificacion = "operación (no pude clasificarla bien)"

        if monto != "no especificado":
            respuesta_bot = f"{emoji} ¡Perfecto! Grabé una {texto_clasificacion} por ${monto}."
        else:
            respuesta_bot = f"{emoji} ¡Perfecto! Grabé una {texto_clasificacion} (monto no especificado)."

        await update.message.reply_text(respuesta_bot)

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}")
        await update.message.reply_text("⚠️ Hubo un error al procesar tu mensaje. Intentá de nuevo.")

# ─────────────────────────────────────────
# Arrancar el bot
# ─────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot iniciado...")
    app.run_polling()
