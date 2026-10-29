"""Etiquetas internas de cursos y preguntas al usuario.

Las etiquetas de curso y las opciones de las preguntas comparten vocabulario: así una
respuesta del usuario se compara directo contra las etiquetas del curso.
"""

# Etiquetas de curso: el LLM elige solo entre estas, nunca texto libre.
COURSE_TAGS = {
    "area": {  # varias posibles
        "programacion": "Programación y desarrollo de software (Python, JavaScript, Git)",
        "ia": "Inteligencia artificial, agentes, IA aplicada",
        "electronica": "Electrónica, Arduino, automatización, hardware",
        "redes": "Redes de computadoras, Cisco, Linux, infraestructura",
        "ciberseguridad": "Ciberseguridad, hacking ético, defensa",
        "datos": "Ciencia y análisis de datos",
        "legal_gestion": "Leyes, normativa, gestión empresarial o de recursos humanos",
    },
    "nivel": {
        "principiante": "No requiere conocimientos previos",
        "intermedio": "Requiere bases previas del tema",
        "avanzado": "Requiere experiencia sólida",
    },
    "objetivo": {  # varias posibles
        "aprender_desde_cero": "Sirve para empezar en el tema",
        "certificacion": "Prepara para una certificación internacional",
        "mejorar_trabajo": "Aplicable directamente en el trabajo",
    },
    "formato": {
        "autodidacta": "A su ritmo, en plataforma, sin horario fijo",
        "virtual_sesiones": "Clases virtuales en vivo con horario",
        "presencial": "Clases presenciales",
        "hibrido": "Mezcla de virtual y presencial",
    },
}
MULTI_TAGS = {"objetivo", "area"}

# Preguntas al usuario. Cada opción: etiqueta -> (texto del botón, descripción para el decisor)
SLOTS = {
    "objetivo": {
        "pregunta": "¿Qué te gustaría lograr con un curso?",
        "opciones": {
            "aprender_desde_cero": ("Aprender algo nuevo desde cero", "Quiere empezar un tema nuevo, sin experiencia"),
            "certificacion": ("Prepararme para una certificación", "Quiere una certificación internacional (Cisco, GitHub, Arduino, Python Institute)"),
            "mejorar_trabajo": ("Mejorar en mi trabajo", "Quiere aplicar lo aprendido en su trabajo o carrera actual"),
            "explorar": ("Solo estoy explorando", "No tiene un objetivo definido, está mirando opciones"),
        },
    },
    "area": {
        "pregunta": "¿Qué tema te llama más la atención?",
        "opciones": {
            "programacion": ("Programación", "Programar, Python, JavaScript, desarrollo de software, Git"),
            "ia": ("Inteligencia artificial", "IA, agentes, ChatGPT, automatización con IA"),
            "electronica": ("Electrónica / Arduino", "Electrónica, Arduino, robótica, circuitos"),
            "redes": ("Redes y Linux", "Redes, Cisco, CCNA, Linux, servidores"),
            "ciberseguridad": ("Ciberseguridad", "Seguridad informática, hacking ético"),
            "datos": ("Datos", "Análisis de datos, ciencia de datos"),
            "no_se": ("No sé todavía", "No tiene un tema preferido"),
        },
    },
    "experiencia": {
        "pregunta": "¿Cuánta experiencia tienes en ese tema?",
        "opciones": {
            "ninguna": ("Ninguna", "Nunca lo ha estudiado ni practicado"),
            "basica": ("Lo básico", "Conoce lo básico, ha hecho algo"),
            "intermedia": ("Bastante", "Tiene experiencia práctica, quiere profundizar"),
        },
    },
    "formato": {
        "pregunta": "¿Cómo prefieres estudiar?",
        "opciones": {
            "autodidacta": ("A mi ritmo, en línea", "Prefiere avanzar solo cuando pueda, sin horario"),
            "virtual_sesiones": ("Clases virtuales en vivo", "Prefiere clases por Zoom con horario"),
            "presencial": ("Presencial en la UTA", "Prefiere ir a clases en persona"),
            "cualquiera": ("Me da igual", "No tiene preferencia de formato"),
        },
    },
    "tarifa": {
        "pregunta": "Para darte el precio correcto: ¿cuál es tu caso?",
        "opciones": {
            "estudiante": ("Estudiante UTA", "Estudiante actual de la Universidad Técnica de Ambato"),
            "comunidad": ("Graduado, docente o administrativo UTA", "Graduado, docente o personal administrativo de la UTA"),
            "publico": ("Público en general", "No pertenece a la UTA"),
        },
    },
}
# 'tarifa' no afecta qué curso conviene: se pregunta al final, solo para el precio.
PROFILE_SLOTS = ["objetivo", "area", "experiencia", "formato"]

SLOT_PURPOSE = {
    "objetivo": "para qué quiere el curso (certificación, empezar desde cero, trabajo)",
    "area": "qué tema le interesa",
    "experiencia": "cuánta experiencia previa tiene en el tema",
    "formato": "si prefiere autodidacta, virtual en vivo o presencial",
}

# Palabras clave para el decisor por reglas (respaldo sin IA)
KEYWORDS = {
    "objetivo": {
        "aprender_desde_cero": ["cero", "empezar", "nuevo", "aprender", "nunca"],
        "certificacion": ["certific", "ccna", "cisco", "examen", "gh-600", "pcep", "pcap"],
        "mejorar_trabajo": ["trabajo", "empresa", "laboral", "carrera", "ascenso"],
        "explorar": ["explor", "mirando", "viendo", "curios"],
    },
    "area": {
        "programacion": ["program", "python", "javascript", "codigo", "código", "software", "git", "desarroll"],
        "ia": ["inteligencia artificial", " ia", "ia ", "agente", "chatgpt", "copilot"],
        "electronica": ["arduino", "electr", "robot", "circuito"],
        "redes": ["red", "cisco", "ccna", "linux", "servidor"],
        "ciberseguridad": ["segur", "hack", "ciber"],
        "datos": ["dato", "analit", "excel"],
        "no_se": ["no se", "no sé", "cualquier", "ninguno"],
    },
    "experiencia": {
        "ninguna": ["ninguna", "nada", "nunca", "cero", "no tengo"],
        "basica": ["basic", "básic", "poco", "algo"],
        "intermedia": ["bastante", "mucha", "experiencia", "avanzad", "trabajo con"],
    },
    "formato": {
        "autodidacta": ["ritmo", "autodidact", "cuando pueda", "sin horario", "flexible"],
        "virtual_sesiones": ["zoom", "en vivo", "virtual"],
        "presencial": ["presencial", "persona", "campus", "ir a"],
        "cualquiera": ["igual", "cualquier", "da lo mismo"],
    },
    "tarifa": {
        "estudiante": ["estudiante", "estudio en la uta", "alumno"],
        "comunidad": ["graduad", "docente", "profesor", "administrativ"],
        "publico": ["publico", "público", "externo", "no soy", "ninguno"],
    },
}

# Intención de un mensaje libre. Las primeras se responden con un dato del catálogo; las de
# CONVERSATION_INTENTS cambian el flujo (charla, fuera de tema, pagos) y nunca llevan datos inventados.
FAQ_INTENTS = {
    "precio": "Pregunta cuánto cuesta, el precio o el valor de un curso",
    "fechas": "Pregunta cuándo empieza, fechas del curso o de inscripción",
    "horario": "Pregunta el horario o los días de clase",
    "modalidad": "Pregunta si es virtual, presencial, dónde es",
    "certificado": "Pregunta por el certificado que entregan",
    "requisitos": "Pregunta qué necesita saber o tener antes",
    "inscripcion": "Pregunta cómo inscribirse o matricularse, o pide que lo inscriban",
    "temario": "Pregunta qué se ve en el curso, contenidos, temario",
    "duracion": "Pregunta cuántas horas dura",
    "instructor": "Pregunta quién dicta el curso, el profesor o instructor",
    "cupo": "Pregunta por cupos, cuántas personas o si quedan lugares",
    "catalogo": "Pregunta qué cursos hay o si tienen un curso de un tema específico",
    "pago_descuento": "Habla de pagos, transferencias, facturas, becas, descuentos o promociones",
    "saludo": "Solo saluda (hola, buenos días) sin pedir nada más",
    "agradecimiento": "Solo agradece o confirma (gracias, ok, listo, perfecto) sin pedir nada más",
    "fuera_de_tema": ("Pide o habla de algo claramente ajeno a estudiar un curso (hacer tareas, deportes, "
                      "política, chistes). No la uses si menciona experiencia, intereses o preferencias de estudio, "
                      "ni si el mensaje no se entiende"),
    "otra": "Otra pregunta sobre cursos que no encaja en las anteriores",
}
CONVERSATION_INTENTS = {"catalogo", "pago_descuento", "saludo", "agradecimiento", "fuera_de_tema"}
FAQ_KEYWORDS = {
    "pago_descuento": ["transferencia", "pague", "pagué", "factura", "beca", "descuento", "promocion", "promoción", "deposito", "depósito"],
    "precio": ["cuesta", "precio", "valor", "cuanto", "cuánto", "pago"],
    "fechas": ["cuando", "cuándo", "fecha", "empieza", "inicia", "comienza"],
    "horario": ["horario", "hora", "dias", "días"],
    "modalidad": ["virtual", "presencial", "donde", "dónde", "zoom", "lugar"],
    "certificado": ["certificado", "diploma", "titulo", "título"],
    "requisitos": ["requisito", "necesito saber", "conocimientos", "necesito tener"],
    "inscripcion": ["inscrib", "matricul", "registr", "anotar"],
    "temario": ["temario", "contenido", "que se ve", "qué se ve", "temas", "aprendo"],
    "duracion": ["horas", "dura", "duración"],
    "instructor": ["profesor", "instructor", "docente del curso", "quien dicta", "quién dicta"],
    "cupo": ["cupo", "lugares", "vacantes"],
    "catalogo": ["que cursos", "qué cursos", "tienen curso", "hay curso", "cursos hay"],
    "agradecimiento": ["gracias", "ok", "listo", "perfecto", "vale"],
    "saludo": ["hola", "buenas", "buenos dias", "buenos días", "buenas tardes"],
}
HUMAN_KEYWORDS = ["persona", "humano", "asesor", "agente", "alguien real", "llamar", "hablar con"]
