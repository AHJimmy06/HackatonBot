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
