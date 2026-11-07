# Bot Ventas y Costos

Bot de Telegram para registrar ventas y costos de Hechizo Bijou en lenguaje natural. Clasifica cada mensaje como VENTA o COSTO usando Gemini y lo agrega a Google Sheets.

## Stack

- Python, Telegram Bot API, Google Gemini (GEMINI_MODEL, default gemini-3.8-flash; respaldo gemini-2.5-flash)
- Google Sheets API (gspread)
- Deploy: Railway

## Flujo (nov-2026)

Gemini recibe el mensaje + las últimas 40 filas de cada solapa + la charla reciente y devuelve JSON con una acción:
- **AGREGAR**: una o varias líneas, con fecha (hoy, ayer, "el 20 de octubre"...). Fecha se escribe `d/m/aaaa` (Sheet en locale es → fecha real). Nunca a futuro.
- **BORRAR**: elige filas; el bot pide confirmación con botones ("¿Querés borrar esta línea? … Sí / No"). Al confirmar re-verifica el contenido (si las filas se corrieron, busca por fecha+detalle+monto). Corregir = borrar + cargar la línea nueva, con la misma confirmación.
- **CONSULTAR**: lista y totaliza movimientos por rango de fechas / tipo / palabra.
- **PREGUNTAR**: si hay duda (falta monto, no se sabe si es venta o costo, varias filas posibles) pregunta en vez de adivinar.
VENTA → hoja `"Ventas"` (col C) / COSTO → `"Compra Materia prima - Producto"` (col D).
**Solo si se piden explícitamente** (regex `EXTRAS_RE`: sueldo/Juli/aguinaldo/agencia/publicidad/monotributo en el mensaje o los 2 últimos de la charla): SUELDO → `"Sueldos"`, AGENCIA → `"Publicidad"` (col D, mismo formato que compras) y MONOTRIBUTO → solapa `"Monotributo"` (Fecha `aaaa-mm-01` texto | Importe; una fila por mes, cargar un mes existente reemplaza el importe). Desde 7/11/2026 el reporte lee el monotributo de esa solapa (antes de S3 `monotributo.json`, que queda de respaldo).
Fecha "hoy" en hora Argentina (antes usaba UTC del servidor).

## Conexión con el ecosistema Hechizo

**Este bot alimenta el P&L mensual.** El Sheet al que escribe (`SHEET_ID_GASTOS`) es el mismo que lee `hechizo-reporte-nuevo` en `fetch_manuales()`. Si el bot deja de funcionar, las ventas manuales y compras desaparecen silenciosamente del P&L.

## Variables de entorno (Railway)

```
TELEGRAM_TOKEN
GEMINI_API_KEY
SHEET_ID           ← SHEET_ID_GASTOS del ecosistema Hechizo
GOOGLE_CREDENTIALS_JSON
GEMINI_MODEL           (opcional)
ALLOWED_CHAT_IDS       (opcional, chat ids separados por coma; vacío = cualquiera)
```
