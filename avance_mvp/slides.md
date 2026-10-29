# Avance del proyecto: validación formal de esquemas JSON en C

## Slide 1 — Alcance y formalización
- Definimos un subconjunto de JSON Schema orientado a validación estructural.
- Se soportan campos tipo object, required, properties y tipos básicos: string, integer, boolean.
- Este subconjunto es el punto de partida para demostrar la traducción esquema → validación estructurada y la lógica de validación en tiempo real.

## Slide 2 — Prototipo en C
- Se está desarrollando un núcleo nativo en C para validar payloads JSON sin depender de un AST completo.
- El esquema se interpreta desde un JSON mínimo y se compara contra el payload con validación por campos.
- La idea central es probar la arquitectura del proyecto: esquema → validación nativa → reporte de errores con tipo y ruta.

## Slide 3 — Siguiente paso
- Extender el validador con arrays, enum, formato de errores por ruta JSON Pointer y comparativas de velocidad.
- Mejorar la generación del validador a partir del esquema.
- Consolidar el prototipo como base para la versión con composición de autómatas y validación streaming.

