# Bot de WhatsApp para las tiendas VitaHub

Responde automáticamente los mensajes de WhatsApp de tres tiendas y mantiene separada la información de cada una:

| Tienda | Clave | Qué responde |
|---|---|---|
| 💊 VitaHub Chile | `vitahubchile` | Productos y precios en vivo desde vitahubchile.com, envíos, pagos, devoluciones, promos |
| 📊 VitaHub Finanzas | `finanzas` | Plantillas de Excel: entrega digital, compatibilidad, problemas de compra |
| 📱 VitaHub Digital | `digital` | Productos digitales: entrega, problemas de compra |

## Cómo distingue la tienda

1. **Por el número** que recibió el mensaje. Si cada tienda tiene su propio número de WhatsApp, queda fija y nunca pregunta.
2. **Por el anuncio** desde el que llegó el cliente (Click-to-WhatsApp de Meta).
3. **Por el mensaje**: nombre de la tienda, palabras clave y nombres de productos del catálogo ("creatina" → VitaHub Chile, "plantilla de presupuesto" → Finanzas, "ebook" → Digital).
4. **Si no queda claro, pregunta** con tres botones, recuerda la elección y responde la consulta original.

El cliente puede escribir `menú` para cambiar de tienda y `asesor` para hablar con una persona. Al derivar, el bot se queda en silencio 12 horas en ese chat (configurable) y, si defines `OWNER_NOTIFY_NUMBER`, te avisa por WhatsApp.

## Dos modos de respuesta

- **Reglas (sin costo extra):** menú numerado, preguntas frecuentes y búsqueda de productos con precio y enlace.
- **Con IA (opcional):** si defines `ANTHROPIC_API_KEY`, Claude redacta las respuestas usando solo los datos de la tienda correspondiente (catálogo, políticas y preguntas frecuentes). Si la IA falla, responde el modo reglas.

## Probarlo sin WhatsApp

```bash
cd whatsapp-bot
pip install -r requirements-dev.txt
python -m app.simulate          # chat en la terminal; /1 /2 /3 simulan los botones
python -m pytest                # pruebas automáticas
```

## Editar lo que responde

Todo el contenido está en [`brands.yaml`](brands.yaml): nombre, palabras clave, contacto, promos y preguntas frecuentes de cada tienda. Los campos marcados `TODO` (sitio web, correo y horario de Finanzas y Digital) están vacíos: complétalos. Si esas tiendas son Shopify, pon su dominio en `shopify_domain` y el bot leerá productos y precios en vivo.

## Puesta en marcha

1. **Publica el servidor** en un hosting con HTTPS. Con Render: *New > Blueprint* y elige este repositorio (usa `render.yaml`). También sirve cualquier servicio que ejecute el `Dockerfile`. Debe correr **una sola instancia** (las conversaciones se guardan en memoria).
2. **Variables de entorno:** copia `.env.example` y complétalas en el hosting.
3. **En Meta** (developers.facebook.com), con una app que tenga el producto WhatsApp conectada al negocio:
   - Registra el o los números en WhatsApp Cloud API.
   - Webhook: URL `https://TU-DOMINIO/webhook`, token de verificación igual a `WHATSAPP_VERIFY_TOKEN`, suscrito al campo `messages`.
   - Crea un token permanente (usuario del sistema) con `whatsapp_business_messaging` → `WHATSAPP_TOKEN`.
   - Copia la clave secreta de la app → `META_APP_SECRET`.
4. **Un número por tienda (opcional):** `BRAND_PHONE_MAP=<phone_number_id>:vitahubchile,<phone_number_id>:finanzas,<phone_number_id>:digital`.
5. Comprueba `https://TU-DOMINIO/health` y envía un mensaje de prueba.

## Límites que conviene conocer

- No consulta pedidos ni pagos: para eso pide el número de pedido y deriva a una persona.
- El aviso a `OWNER_NOTIFY_NUMBER` solo llega si ese número le escribió al número del bot en las últimas 24 horas (regla de WhatsApp para mensajes sin plantilla).
- Si respondes a mano desde la app WhatsApp Business (modo coexistencia) y el webhook está suscrito a `smb_message_echoes`, el bot se silencia solo en ese chat.
- Solo lee texto y botones; ante audios o imágenes pide que escriban la consulta.
- Al reiniciar el servidor se olvidan las conversaciones en curso (la tienda elegida se vuelve a detectar o preguntar).
