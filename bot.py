import os
import logging
import json
import httpx
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────
# Configuración desde variables de entorno
# ─────────────────────────────────────────
TELEGRAM_TOKEN           = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY           = os.environ["GEMINI_API_KEY"]
SHEET_ID                 = os.environ.get("SHEET_ID", "1Eswje16JVngNEPTpq8f2-_2tjO8XAbsBBJnCIaqNkRY")
GOOGLE_CREDENTIALS_JSON  = os.environ["GOOGLE_CREDENTIALS_JSON"]

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"

MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]

# ─────────────────────────────────────────
# Setup logging
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# Fecha formateada: "23-mar"
# ─────────────────────────────────────────
def fecha_hoy():
    now = datetime.now()
    return f"{now.day:02d}-{MESES[now.month - 1]}"

# ─────────────────────────────────────────
# Google Sheets — obtener solapa por nombre
# ─────────────────────────────────────────
def get_worksheet(nombre_solapa: str):
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes     = ["https://www.googleapis.com/auth/spreadsheets"]
    creds      = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client     = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(nombre_solapa)

# ─────────────────────────────────────────
# Escribir en el Sheet según clasificación
# VENTAS:  col A=Fecha, B=Detalle, C=Monto, D=vacío
# COSTOS:  col A=Fecha, B=Detalle, C=vacío, D=Monto
# ─────────────────────────────────────────
def escribir_en_sheet(clasificacion: str, detalle: str, monto_num: float):
    fecha = fecha_hoy()

    if clasificacion == "VENTA":
        ws  = get_worksheet("Ventas")
        fila = [fecha, detalle, monto_num, ""]
    else:  # COSTO
        ws  = get_worksheet("Compra Materia prima - Producto")
        fila = [fecha, detalle, "", monto_num]

    ws.append_row(fila, value_input_option="USER_ENTERED")
    logger.info(f"Escrito en '{ws.title}': {fila}")

# ─────────────────────────────────────────
# Llamada a Gemini
# ─────────────────────────────────────────
async def llamar_gemini(prompt: str) -> str:
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# ─────────────────────────────────────────
# Handler principal
# ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.message.text

    prompt = f"""Analizá el siguiente mensaje sobre una operación comercial de una tienda de bijouterie y respondé ÚNICAMENTE en este formato exacto:

CLASIFICACION: VENTA o COSTO
MONTO: solo el número sin símbolo de moneda ni puntos de miles (ejemplo: 15000), o "no especificado" si no se menciona
DETALLE: descripción corta en español de la operación (máximo 50 caracteres)

Mensaje: "{mensaje}"

Respondé solo con las tres líneas del formato, sin explicaciones adicionales."""

    try:
        respuesta_texto = await llamar_gemini(prompt)

        clasificacion = "DESCONOCIDO"
        monto_str     = "no especificado"
        detalle       = mensaje[:50]

        for linea in respuesta_texto.splitlines():
            linea = linea.strip()
            if linea.upper().startswith("CLASIFICACION:"):
                clasificacion = linea.split(":", 1)[1].strip().upper()
            elif linea.upper().startswith("MONTO:"):
                monto_str = linea.split(":", 1)[1].strip()
            elif linea.upper().startswith("DETALLE:"):
                detalle = linea.split(":", 1)[1].strip()

        # Parsear monto — maneja tanto formato español (1.234,56) como inglés (1234.56)
        monto_num = None
        if monto_str != "no especificado":
            try:
                s = monto_str.strip().replace(" ", "").replace("$", "")
                # Si tiene coma Y punto: determinar cuál es decimal
                if "," in s and "." in s:
                    if s.rfind(",") > s.rfind("."):
                        # Formato español: 1.234,56
                        s = s.replace(".", "").replace(",", ".")
                    else:
                        # Formato inglés: 1,234.56
                        s = s.replace(",", "")
                elif "," in s:
                    # Solo coma: puede ser decimal español (522335,50) o miles (522,335)
                    partes = s.split(",")
                    if len(partes) == 2 and len(partes[1]) <= 2:
                        # Es decimal: 522335,50
                        s = s.replace(",", ".")
                    else:
                        # Es separador de miles: 522,335
                        s = s.replace(",", "")
                elif "." in s:
                    # Solo punto: puede ser decimal inglés (522335.50) o miles español (1.234)
                    partes = s.split(".")
                    if len(partes) == 2 and len(partes[1]) <= 2:
                        # Es decimal: 522335.50
                        pass  # ya está bien
                    else:
                        # Es separador de miles español: 1.234.567
                        s = s.replace(".", "")
                monto_num = float(s)
            except ValueError:
                monto_num = None

        # Escribir en Sheet si la clasificación es válida y hay monto
        if clasificacion in ("VENTA", "COSTO") and monto_num is not None:
            escribir_en_sheet(clasificacion, detalle, monto_num)
            guardado = True
        else:
            guardado = False

        # Respuesta al usuario
        if clasificacion == "VENTA":
            emoji = "💰"
            texto_tipo = "venta"
            solapa = "Ventas"
        elif clasificacion == "COSTO":
            emoji = "🛒"
            texto_tipo = "compra/costo"
            solapa = "Compra Materia prima - Producto"
        else:
            emoji = "❓"
            texto_tipo = "operación"
            solapa = "-"

        if guardado:
            respuesta_bot = (
                f"{emoji} Grabé una {texto_tipo} por ${monto_num:,.0f}\n"
                f"📋 Detalle: {detalle}\n"
                f"📊 Solapa: {solapa}"
            )
        elif monto_num is None and clasificacion in ("VENTA", "COSTO"):
            respuesta_bot = f"{emoji} Clasificado como {texto_tipo} pero no pude identificar el monto. ¿Podés repetirlo con el monto?"
        else:
            respuesta_bot = "❓ No pude clasificar la operación. Intentá con algo como 'vendí $15000 en el showroom' o 'compré materia prima $50000'."

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
