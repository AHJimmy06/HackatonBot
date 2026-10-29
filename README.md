# Asesor CTT-FISEI

Bot de Telegram y panel de mando para los cursos del Centro de Transferencia y Desarrollo de Tecnologías de la FISEI. El pénsum se lee de [la página oficial](https://ctt-talleresfisei.uta.edu.ec/course.php) cada vez que arranca el servicio, cada seis horas y con el botón **Actualizar pénsum**.

## Arquitectura

FastAPI es solo el marco. Las capas quedan separadas:

- `app/connectors/telegram.py` habla con Telegram.
- `app/inference/` arma el prompt, recorta el historial y llama al modelo.
- `app/inference/guard.py` frena inyecciones y precios no autorizados.
- `app/persistence/database.py` es el único que abre la base.
- `app/catalog/` raspa el sitio y elige como máximo tres cursos para el prompt.
- `app/orchestration/conversations.py` une el mensaje, la pausa de la IA y la respuesta.
- `app/realtime/hub.py` empuja los cambios al panel.
- `app/main.py` publica las rutas.

Al modelo entran el prompt fijo, hasta tres fichas cortas y los últimos ocho turnos. No entra el pénsum completo ni el chat entero.

## Arranque

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abre http://localhost:8000. Sin token de Telegram puedes usar **Simular prospecto**. Con `TELEGRAM_BOT_TOKEN` el bot escucha solo.

`LLM_BASE_URL`, `LLM_API_KEY` y `LLM_MODEL` aceptan cualquier API compatible con OpenAI. En la capa gratis conviene Groq con el modelo `openai/gpt-oss-120b`. Si la clave está vacía, la respuesta sale de la ficha raspada.

## Qué muestra el panel

Conversaciones en vivo, termómetro (interesado, dudoso, frustrado, lead caliente), resumen, perfil y cursos considerados. **Enviar y tomar control** pausa la IA y manda el texto por el mismo bot. **Devolver a la IA** reanuda la inferencia.


## Integración CTT Connect (29 octubre 2026)

Esta carpeta conserva como base `feature/baseProject`, commit `192af5f`, de https://github.com/AHJimmy06/HackatonBot.git. Se incorporó nuestra interfaz sin reemplazar el bot. Adaptación de contratos originales, catálogo con fechas, perfiles y termómetro, simulador con IDs negativos y fallback de refresco cada 3 segundos.

### Acceso desplegado temporalmente

URL: https://analytical-gabriel-scholarships-swap.trycloudflare.com
Local: http://127.0.0.1:8001
Usuario: admin
Contraseña: archivo local `.dashboard-password` (ignorado en Git; no es una variable de entorno).

El `.env` se copió literalmente desde el archivo que editó el usuario, sin añadir variables ni cambiar proveedor. Para posteriores cambios editar el `.env` de esta carpeta y reiniciar el servidor. No publicar `.env` ni `.dashboard-password`.

La publicación usa Cloudflare Quick Tunnel: depende de este equipo encendido y del proceso cloudflared, cambia de URL al reiniciar el túnel y no ofrece SLA. No es alojamiento permanente. Como no soporta SSE, la UI conserva actualización automática por consultas cada 3 segundos. En hosting normal SSE continúa disponible.

### Mejoras de seguridad y funcionamiento

- Pausa e invalidación de inferencia en curso mediante epoch y locks por conversación.
- Autenticación Basic protegida por HTTPS público; restricción de origen en escrituras.
- Simulador solo acepta IDs negativos y no puede suplantar un chat Telegram privado real.
- Offset Telegram persistido después de procesar, sin eliminar webhook automáticamente.
- Logging HTTPX de URLs con token desactivado; logs ignorados.
- El prototipo padre ya no hace polling, evitando competencia por el token.

Verificación anterior: página pública devuelve 401 anónimo y 200 autenticado; Telegram conectado sin error; 18 cursos. La validación actual se describe a continuación.

### Despliegue permanente pendiente

Se necesita elegir proveedor/cuenta (VPS, Render, Railway u otro servidor Python persistente) y autorizar su costo si aplica. Una sola instancia/worker, volumen persistente para `data/ctt.db`, HTTPS y montaje del archivo de contraseña. No se ha contratado hosting ni hecho push remoto. Los cambios permanecen en esta rama local.


## Fusión de refinamientos de feature/baseProject

Se incorpora `0e9d195` (coherencia del agente) sobre nuestra integración `91403ed`. Se conserva CTT Connect, no la UI alternativa de la rama.

- Mensajes confusos solicitan aclaración sin llamar al LLM; nombres extraídos se validan antes de guardar.
- Fotos y documentos en Telegram no se descargan: se orienta al formulario oficial.
- Inscripciones cerradas, próximas y sin fecha se distinguen; las respuestas de respaldo también pasan las guardas.
- Recuperación de hasta tres cursos, incluyendo una alternativa vigente sin superar el presupuesto.
- Una corrección de respuesta no cancela la solicitud explícita de asesor humano.
- La UI ignora respuestas tardías de conversaciones/catálogo, reconecta y muestra el estado real de Telegram.
- No se modifican ni amplían las variables del `.env`. Se conserva la contraseña del archivo local.

### Pruebas de regresión

```powershell
python -m pytest tests -q
node tests/frontend-contract.test.cjs
node tests/frontend-race.test.cjs
node --check static/app.js
```

Resultado: 26 pruebas Python y 9 escenarios JavaScript. Cubren coherencia, fallback con proveedor caído, estado de inscripción, perfil, contexto, archivos privados, autenticación, origen, simulador, pausa/reanudación y respuestas fuera de orden. No arrancan polling ni usan claves reales.

### Blueprint importado

`render.yaml` viene de la rama remota; no significa que se haya desplegado en Render. Antes de usarlo se requiere configurar un servicio persistente, disco para `data/ctt.db`, archivo `.dashboard-password` y una sola instancia/worker. El plan gratuito indicado en el blueprint no resuelve por sí solo la persistencia ni operación continua del bot. La demo sigue desplegada mediante el túnel existente.
