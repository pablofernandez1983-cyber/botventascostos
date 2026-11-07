import os
import re
import json
import uuid
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo

import httpx
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, MessageHandler, CallbackQueryHandler,
                          filters, ContextTypes)

# ─────────────────────────────────────────
# Configuración desde variables de entorno
# ─────────────────────────────────────────
TELEGRAM_TOKEN          = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY          = os.environ["GEMINI_API_KEY"]
SHEET_ID                = os.environ.get("SHEET_ID", "1Eswje16JVngNEPTpq8f2-_2tjO8XAbsBBJnCIaqNkRY")
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
# Modelo principal y de respaldo (si el principal falla, se usa el otro)
GEMINI_MODEL            = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_MODEL_FALLBACK   = os.environ.get("GEMINI_MODEL_FALLBACK", "gemini-2.5-flash")
# Opcional: lista de chat ids autorizados separados por coma. Vacío = cualquiera.
ALLOWED_CHAT_IDS        = {c.strip() for c in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if c.strip()}

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SOLAPAS = {"VENTA": "Ventas", "COSTO": "Compra Materia prima - Producto"}
FILAS_CONTEXTO = 40     # últimas filas de cada solapa que ve el modelo (para borrar)
HISTORIAL_MAX = 8       # mensajes recientes de la charla que ve el modelo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────
def hoy() -> date:
    return datetime.now(TZ).date()


def fecha_sheet(d: date) -> str:
    # El Sheet está en locale es: dd/mm/yyyy se guarda como fecha real
    return f"{d.day}/{d.month}/{d.year}"


def parse_monto(v) -> float | None:
    """Convierte '1.234,56', '$ 97.700,00 ', '1234.56', 15000 → float."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s or s in ("-", ".", ","):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        p = s.split(",")
        s = s.replace(",", ".") if len(p) == 2 and len(p[1]) <= 2 else s.replace(",", "")
    elif "." in s:
        p = s.split(".")
        if not (len(p) == 2 and len(p[1]) <= 2):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_fecha(s: str) -> date | None:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", str(s))
    if not m:
        return None
    a = int(m.group(3))
    try:
        return date(a + 2000 if a < 100 else a, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def fmt_monto(m: float) -> str:
    return "$" + f"{m:,.0f}".replace(",", ".")


# ─────────────────────────────────────────
# Google Sheets
# ─────────────────────────────────────────
_client = None


def get_worksheet(nombre_solapa: str):
    global _client
    if _client is None:
        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDENTIALS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets"])
        _client = gspread.authorize(creds)
    return _client.open_by_key(SHEET_ID).worksheet(nombre_solapa)


def leer_filas(tipo: str, ultimas: int | None = None) -> list[dict]:
    """Filas de una solapa: [{fila, fecha, detalle, monto}] (fila = número real en el Sheet)."""
    ws = get_worksheet(SOLAPAS[tipo])
    vals = ws.get_all_values()
    col = 2 if tipo == "VENTA" else 3
    out = []
    for i, r in enumerate(vals[1:], start=2):
        if not r or not r[0].strip():
            continue
        m = parse_monto(r[col]) if len(r) > col else None
        if m is None:
            continue
        out.append({"fila": i, "fecha": r[0].strip(),
                    "detalle": (r[1] if len(r) > 1 else "").strip(), "monto": m})
    return out[-ultimas:] if ultimas else out


def agregar_fila(tipo: str, detalle: str, monto: float, d: date):
    ws = get_worksheet(SOLAPAS[tipo])
    fila = [fecha_sheet(d), detalle, monto, ""] if tipo == "VENTA" else [fecha_sheet(d), detalle, "", monto]
    ws.append_row(fila, value_input_option="USER_ENTERED")
    logger.info(f"Escrito en '{ws.title}': {fila}")


def borrar_fila(tipo: str, esperado: dict) -> bool:
    """Borra la fila que coincide con lo esperado. Primero prueba el número de fila
    guardado; si las filas se corrieron, la busca por contenido (de abajo hacia arriba)."""
    ws = get_worksheet(SOLAPAS[tipo])
    filas = leer_filas(tipo)

    def igual(f):
        return (f["fecha"] == esperado["fecha"] and f["detalle"] == esperado["detalle"]
                and abs(f["monto"] - esperado["monto"]) < 0.01)

    candidata = next((f for f in filas if f["fila"] == esperado["fila"] and igual(f)), None)
    if candidata is None:
        candidata = next((f for f in reversed(filas) if igual(f)), None)
    if candidata is None:
        return False
    ws.delete_rows(candidata["fila"])
    logger.info(f"Borrada fila {candidata['fila']} de '{ws.title}': {candidata}")
    return True


# ─────────────────────────────────────────
# Gemini
# ─────────────────────────────────────────
async def llamar_gemini(prompt: str) -> dict:
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    ultimo_error = None
    for modelo in (GEMINI_MODEL, GEMINI_MODEL_FALLBACK):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{url}?key={GEMINI_API_KEY}", json=payload)
                r.raise_for_status()
                texto = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            texto = re.sub(r"^```(?:json)?|```$", "", texto).strip()
            return json.loads(texto)
        except Exception as e:
            ultimo_error = e
            logger.warning(f"Falló {modelo}: {e}")
    raise ultimo_error


def armar_prompt(mensaje: str, historial: list[str], ventas: list[dict], costos: list[dict]) -> str:
    d = hoy()
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

    def tabla(filas, prefijo):
        return "\n".join(f'{prefijo}{f["fila"]} | {f["fecha"]} | {f["detalle"]} | {fmt_monto(f["monto"])}'
                         for f in filas) or "(vacía)"

    hist = "\n".join(historial[-HISTORIAL_MAX:]) or "(sin mensajes previos)"
    return f"""Sos el asistente que registra ventas y costos de Hechizo, una tienda de bijouterie de Buenos Aires,
en un Google Sheet. Hoy es {dias[d.weekday()]} {d.isoformat()}.

Solapas:
- VENTA → "Ventas" (ventas en el showroom, ventas por fuera de la tienda online, etc.)
- COSTO → "Compra Materia prima - Producto" (compras a proveedores, materiales, importaciones, pagos de mercadería)

Últimas filas de VENTAS (V<n° de fila> | fecha | detalle | monto):
{tabla(ventas, "V")}

Últimas filas de COSTOS (C<n° de fila> | fecha | detalle | monto):
{tabla(costos, "C")}

Charla reciente (para entender respuestas a preguntas tuyas):
{hist}

Mensaje nuevo del usuario: "{mensaje}"

Decidí qué hacer y respondé SOLO un JSON con esta forma:
{{
  "accion": "AGREGAR" | "BORRAR" | "CONSULTAR" | "PREGUNTAR" | "OTRO",
  "agregar": [{{"tipo": "VENTA"|"COSTO", "monto": número, "detalle": "texto corto en español (máx 50 caracteres)", "fecha": "YYYY-MM-DD"}}],
  "borrar": [{{"tipo": "VENTA"|"COSTO", "fila": número de fila de las tablas de arriba}}],
  "consulta": {{"tipo": "VENTA"|"COSTO"|"AMBOS", "desde": "YYYY-MM-DD", "hasta": "YYYY-MM-DD", "buscar": "palabra para filtrar el detalle o vacío"}},
  "pregunta": "texto de la pregunta (solo si accion=PREGUNTAR)",
  "respuesta": "texto breve (solo si accion=OTRO)"
}}

Reglas:
- AGREGAR: puede haber varias operaciones en un mensaje. Si no dice fecha, usá hoy. Entendé "ayer", "el viernes",
  "el 15 de octubre", "en septiembre" (si dice solo el mes, usá el último día de ese mes si ya pasó, o hoy si es el mes actual).
  Nunca inventes fechas futuras. Montos en pesos: "15 lucas" = 15000, "1,2 palos" = 1200000, "1.234,56" = 1234.56.
- BORRAR: cuando pide borrar, eliminar, sacar o corregir una línea ("me equivoqué", "borrá la última", "sacá la de Dharma de 113500").
  Elegí SOLO filas de las tablas de arriba que coincidan. "La última" = la fila con número más alto de la solapa que corresponda
  (si no se sabe la solapa, la última que se habló en la charla). Si pide CORREGIR un monto o dato, devolvé la fila a borrar en
  "borrar" y la línea corregida en "agregar" (accion "BORRAR"); el bot pide confirmación antes de tocar nada.
- CONSULTAR: cuando pide ver, mostrar, listar o sumar movimientos ("los últimos 7 días", "cuánto vendí en octubre",
  "qué le compré a Dharma este año"). Completá "consulta" con el rango de fechas (inclusive) y el tipo; si no dice tipo, AMBOS.
  "últimos 7 días" = desde hoy-6 hasta hoy. Si no dice período, el mes actual.
- PREGUNTAR: si tenés cualquier duda real (no está claro si es venta o costo, falta el monto, el monto parece raro,
  hay varias filas que podrían ser la que quiere borrar, la fecha es ambigua), NO adivines: hacé una pregunta concreta y corta.
- OTRO: saludos o cosas que no son operaciones; respondé breve y explicá qué podés hacer.
"""


# ─────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────
def autorizado(update: Update) -> bool:
    return not ALLOWED_CHAT_IDS or str(update.effective_chat.id) in ALLOWED_CHAT_IDS


def recordar(context, quien: str, texto: str):
    h = context.chat_data.setdefault("historial", [])
    h.append(f"{quien}: {texto}")
    del h[:-HISTORIAL_MAX]


def consultar(c: dict) -> str:
    tipo = str(c.get("tipo") or "AMBOS").upper()
    try:
        desde = date.fromisoformat(c.get("desde"))
    except (TypeError, ValueError):
        desde = hoy().replace(day=1)
    try:
        hasta = date.fromisoformat(c.get("hasta"))
    except (TypeError, ValueError):
        hasta = hoy()
    buscar = str(c.get("buscar") or "").strip().lower()
    tipos = ["VENTA", "COSTO"] if tipo not in SOLAPAS else [tipo]
    partes = [f"📊 Movimientos del {fecha_sheet(desde)} al {fecha_sheet(hasta)}" + (f" con «{buscar}»" if buscar else "")]
    for t in tipos:
        filas = []
        for f in leer_filas(t):
            d = parse_fecha(f["fecha"])
            if d and desde <= d <= hasta and (not buscar or buscar in f["detalle"].lower()):
                filas.append((d, f))
        filas.sort(key=lambda x: x[0])
        titulo = "💰 Ventas" if t == "VENTA" else "🛒 Compras / costos"
        total = sum(f["monto"] for _, f in filas)
        partes.append(f"\n{titulo}: {len(filas)} movimientos · total {fmt_monto(total)}")
        mostrar = filas[-40:]
        if len(filas) > 40:
            partes.append(f"(muestro los últimos 40)")
        partes += [f"  {f['fecha']} · {f['detalle']} · {fmt_monto(f['monto'])}" for _, f in mostrar]
    return "\n".join(partes)


def describir(tipo, fecha, detalle, monto):
    emoji = "💰" if tipo == "VENTA" else "🛒"
    return f"{emoji} {fecha} · {detalle} · {fmt_monto(monto)}"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    mensaje = update.message.text
    try:
        ventas = leer_filas("VENTA", FILAS_CONTEXTO)
        costos = leer_filas("COSTO", FILAS_CONTEXTO)
        res = await llamar_gemini(armar_prompt(mensaje, context.chat_data.get("historial", []), ventas, costos))
        recordar(context, "Usuario", mensaje)
        accion = str(res.get("accion", "")).upper()

        if accion == "PREGUNTAR":
            pregunta = res.get("pregunta") or "¿Me lo podés aclarar?"
            recordar(context, "Bot", pregunta)
            await update.message.reply_text(f"🤔 {pregunta}")
            return

        if accion == "OTRO":
            txt = res.get("respuesta") or ("Mandame una venta o un costo, por ejemplo 'vendí $15000 en el showroom' "
                                           "o 'compré materia prima $50000 el 3/10'. También puedo borrar líneas.")
            recordar(context, "Bot", txt)
            await update.message.reply_text(txt)
            return

        if accion == "CONSULTAR":
            txt = consultar(res.get("consulta") or {})
            recordar(context, "Bot", txt[:600])
            for i in range(0, len(txt), 3900):
                await update.message.reply_text(txt[i:i + 3900])
            return

        if accion == "BORRAR":
            por_fila = {("VENTA", f["fila"]): f for f in ventas} | {("COSTO", f["fila"]): f for f in costos}
            pedidos = [(str(b.get("tipo", "")).upper(), int(b.get("fila", 0))) for b in res.get("borrar", []) or []]
            encontrados = [(t, por_fila[(t, n)]) for t, n in pedidos if (t, n) in por_fila]
            if not encontrados:
                txt = "No encontré esa línea entre las últimas cargadas. ¿Me decís la fecha, el detalle o el monto?"
                recordar(context, "Bot", txt)
                await update.message.reply_text(f"🤔 {txt}")
                return
            reemplazo = [a for a in (res.get("agregar") or []) if parse_monto(a.get("monto")) is not None]
            pendientes = context.chat_data.setdefault("pendientes", {})
            for i, (tipo, f) in enumerate(encontrados):
                clave = uuid.uuid4().hex[:10]
                # Si es una corrección, la línea nueva se carga al confirmar el borrado de la primera fila
                pendientes[clave] = {"tipo": tipo, "fila": f, "agregar": reemplazo if i == 0 else []}
                texto = f"¿Querés borrar esta línea?\n{describir(tipo, f['fecha'], f['detalle'], f['monto'])}"
                if i == 0 and reemplazo:
                    texto += "\n\ny cargar en su lugar:\n" + "\n".join(
                        describir(a["tipo"].upper(), fecha_sheet(date.fromisoformat(a["fecha"])),
                                  a["detalle"], parse_monto(a["monto"])) for a in reemplazo)
                botones = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Sí, borrar", callback_data=f"si:{clave}"),
                    InlineKeyboardButton("No", callback_data=f"no:{clave}")]])
                await update.message.reply_text(texto, reply_markup=botones)
            recordar(context, "Bot", "Pedí confirmación para borrar " +
                     ", ".join(f"{t} fila {f['fila']}" for t, f in encontrados))
            return

        if accion == "AGREGAR":
            items = res.get("agregar") or []
            grabadas, problemas = [], []
            for a in items:
                tipo = str(a.get("tipo", "")).upper()
                monto = parse_monto(a.get("monto"))
                try:
                    d = date.fromisoformat(str(a.get("fecha") or hoy().isoformat()))
                except ValueError:
                    d = hoy()
                if d > hoy():
                    d = hoy()
                if tipo not in SOLAPAS or monto is None or monto <= 0:
                    problemas.append(a)
                    continue
                detalle = (a.get("detalle") or mensaje)[:50]
                agregar_fila(tipo, detalle, monto, d)
                grabadas.append({"tipo": tipo, "fecha": fecha_sheet(d), "detalle": detalle, "monto": monto})
            if not grabadas:
                txt = "No pude identificar la operación o el monto. ¿Me lo repetís? Ej: 'vendí $15000 en el showroom'."
                recordar(context, "Bot", txt)
                await update.message.reply_text(f"🤔 {txt}")
                return
            lineas = ["✅ Grabé:"] + [describir(g["tipo"], g["fecha"], g["detalle"], g["monto"]) +
                                      f"  ({'Ventas' if g['tipo'] == 'VENTA' else 'Compras'})" for g in grabadas]
            if problemas:
                lineas.append("⚠️ Hubo una parte que no entendí, ¿me la repetís?")
            lineas.append("Si me equivoqué, decime 'borrá la última' o qué corregir.")
            txt = "\n".join(lineas)
            recordar(context, "Bot", txt)
            await update.message.reply_text(txt)
            return

        await update.message.reply_text("❓ No entendí. Probá con 'vendí $15000 en el showroom' o 'compré materia prima $50000'.")

    except Exception as e:
        logger.exception(f"Error procesando mensaje: {e}")
        await update.message.reply_text("⚠️ Hubo un error al procesar tu mensaje. Intentá de nuevo.")


async def handle_boton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not autorizado(update):
        return
    decision, _, clave = (q.data or "").partition(":")
    p = context.chat_data.get("pendientes", {}).pop(clave, None)
    if p is None:
        await q.edit_message_text(q.message.text + "\n\n(Esta confirmación ya no está vigente.)")
        return
    f = p["fila"]
    linea = describir(p["tipo"], f["fecha"], f["detalle"], f["monto"])
    if decision != "si":
        recordar(context, "Bot", "El usuario canceló el borrado.")
        await q.edit_message_text(f"👌 No borré nada.\n{linea}")
        return
    try:
        ok = borrar_fila(p["tipo"], f)
        if not ok:
            await q.edit_message_text(f"⚠️ No encontré más esa línea en el Sheet (¿ya la borraron?).\n{linea}")
            return
        txt = f"🗑️ Borrada:\n{linea}"
        for a in p.get("agregar", []):
            tipo, monto = a["tipo"].upper(), parse_monto(a["monto"])
            d = min(date.fromisoformat(a["fecha"]), hoy())
            agregar_fila(tipo, a["detalle"][:50], monto, d)
            txt += f"\n✅ Cargada en su lugar:\n{describir(tipo, fecha_sheet(d), a['detalle'][:50], monto)}"
        recordar(context, "Bot", txt)
        await q.edit_message_text(txt)
    except Exception as e:
        logger.exception(f"Error borrando: {e}")
        await q.edit_message_text("⚠️ Hubo un error al borrar. No se tocó nada; intentá de nuevo.")


# ─────────────────────────────────────────
# Arrancar el bot
# ─────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_boton))
    logger.info(f"Bot iniciado (modelo {GEMINI_MODEL}, respaldo {GEMINI_MODEL_FALLBACK})...")
    app.run_polling()
