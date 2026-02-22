# 🤖 Bot de Ventas y Costos — Guía de instalación

## ¿Qué hace este bot?
- Recibe mensajes en Telegram describiendo ventas o compras
- Gemini AI los clasifica como **VENTA** o **COSTO** y extrae el monto
- Guarda todo en Google Sheets automáticamente
- Confirma en Telegram: *"💰 Grabé una venta por $200"*

---

## PASO 1 — Preparar Google Sheets

### 1.1 Crear la hoja
- Abrí [Google Sheets](https://sheets.google.com) y creá una hoja nueva
- En la primera fila, poné estos encabezados (uno por celda):
  ```
  Fecha | Usuario | Mensaje | Clasificación | Monto
  ```
- Copiá el **ID de la hoja** desde la URL:
  `docs.google.com/spreadsheets/d/`**ESTE_ES_EL_ID**`/edit`

### 1.2 Crear credenciales de Google
1. Entrá a [Google Cloud Console](https://console.cloud.google.com)
2. Creá un proyecto nuevo (o usá uno existente)
3. Activá la **Google Sheets API**:
   - Menú → "APIs y servicios" → "Biblioteca" → buscá "Google Sheets API" → Activar
4. Creá una **cuenta de servicio**:
   - "APIs y servicios" → "Credenciales" → "Crear credenciales" → "Cuenta de servicio"
   - Dale un nombre cualquiera, creala
5. Descargá el JSON de la cuenta de servicio:
   - Hacé click en la cuenta de servicio creada
   - Pestaña "Claves" → "Agregar clave" → "Crear clave nueva" → JSON
   - Se descarga un archivo `.json` — **guardalo bien**
6. Compartí tu Google Sheet con el email de la cuenta de servicio:
   - Abrí el JSON descargado y copiá el valor de `"client_email"` (algo como `nombre@proyecto.iam.gserviceaccount.com`)
   - En tu Google Sheet → "Compartir" → pegá ese email → dale permisos de **editor**

---

## PASO 2 — Subir a Railway (gratis)

1. Entrá a [railway.app](https://railway.app) y creá una cuenta (podés entrar con GitHub)
2. Click en **"New Project"** → **"Deploy from GitHub repo"**
   - Si no tenés el código en GitHub, elegí **"Empty project"** y subí los archivos manualmente
3. Railway detecta automáticamente que es Python

### Si subís los archivos manualmente:
- En Railway, abrí tu proyecto → pestaña **"Files"**
- Subí `bot.py` y `requirements.txt`

---

## PASO 3 — Configurar las variables de entorno en Railway

En tu proyecto de Railway:
1. Click en tu servicio → pestaña **"Variables"**
2. Agregá estas 4 variables:

| Variable | Valor |
|---|---|
| `TELEGRAM_TOKEN` | El token que te dio BotFather |
| `GEMINI_API_KEY` | Tu API key de Google AI Studio |
| `SHEET_ID` | El ID de tu Google Sheet |
| `GOOGLE_CREDENTIALS_JSON` | El contenido **completo** del archivo JSON descargado en el Paso 1.2 (copiá y pegá todo el texto del archivo) |

> ⚠️ Para `GOOGLE_CREDENTIALS_JSON`: abrí el archivo `.json` con el Bloc de notas, seleccioná todo (Ctrl+A), copiá (Ctrl+C) y pegalo como valor de la variable.

---

## PASO 4 — Hacer un Deploy

- En Railway, click en **"Deploy"**
- Esperá que termine de instalar las dependencias (1-2 minutos)
- Si ves `Bot iniciado...` en los logs, ¡está funcionando!

---

## PASO 5 — Compartir el bot

- Compartí el link de tu bot con tu equipo: `t.me/NOMBRE_DE_TU_BOT`
- Cualquier persona que le mande un mensaje al bot quedará registrada en el Sheet con su nombre de usuario

---

## Ejemplos de mensajes que puede procesar

✅ `"Vendí 50 unidades de aceite a $300 cada una"`  
✅ `"Compramos mercadería por $15.000"`  
✅ `"Venta al cliente Juan por $8.500"`  
✅ `"Pagamos el proveedor, $22.000 en insumos"`  

---

## Solución de problemas

**El bot no responde:**
- Revisá que el deploy esté activo en Railway (no en pausa)
- Verificá que el TELEGRAM_TOKEN esté bien copiado

**Error en Google Sheets:**
- Verificá que compartiste la hoja con el email de la cuenta de servicio
- Asegurate que el GOOGLE_CREDENTIALS_JSON esté completo (debe empezar con `{` y terminar con `}`)

**El bot no clasifica bien:**
- Intentá ser más específico en el mensaje, por ejemplo: "venta de..." o "compra de..."
