"""Motor de recomendación por pasos.

Separado de la inferencia libre: entiende al usuario con un decisor tipado (Jev, LLM o
reglas), elige la próxima pregunta y el curso, y deja al LLM solo la redacción, con una
guardia que rechaza datos no publicados. Ver README, sección "Motor".
"""
