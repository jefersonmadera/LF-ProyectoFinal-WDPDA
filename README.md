# Proyecto Final — Lenguajes Formales y Autómatas 2026-2 (UPTC Sogamoso)

## Validación en Tiempo Real de Esquemas JSON/XML mediante Autómatas de Pila Ponderados

Compilador que traduce un esquema JSON (JSON Schema, con extensión a un subconjunto de XSD) a un autómata de pila determinista ponderado (WDPDA) y emite un validador nativo en C y Rust que consume los bytes del payload en una sola pasada, sin construir un AST ni reservar memoria dinámica.

**Integrantes:** Jeferson Santiago Madera Gaona y Sergio Alexander Gómez Zapata
**Curso:** Lenguajes Formales y Autómatas 2026-2 — Prof. Marco Suárez (UPTC)
**Estado:** Semana 1 completada (26 oct – 1 nov 2026) — Fase 1
léxica: tokenizador JSON/XML escrito a mano en Python con DFA,
UTF-8 y streaming por bloques, esqueleto del runtime en C y Rust
(`init`/`feed`/`finish`, pila fija, sin asignación dinámica),
13 pruebas (incluida equivalencia por bloques de 1 y 7 bytes) y
bench. Detalle en `docs/fase1-lexico.md`. Sin decisiones de
alcance cerradas con el profesor todavía (ver `docs/decisiones.md`).
Revisión cruzada del otro integrante pendiente: la invitación a
Sergio (@sergiomezzz) seguía pendiente de aceptación al cierre
de la Semana 1.

## Estructura

- `compiler/` — Fases 1–4: lexer, parser LL(1), análisis semántico, construcción del WDPDA y emisión del IR.
- `runtime/c/` — Validador generado en C.
- `runtime/rust/` — Validador generado en Rust.
- `web/` — Backend (API REST + WebSocket) y frontend: editor de esquema, compilación, visualización del autómata y panel de métricas.
- `tests/` — Casos válidos, inválidos, malformados, fuera del DPDA puro, de bombeo y extremos.
- `bench/` — Scripts de medición reproducibles (misma máquina, calentamiento, mediana).
- `docs/` — Documentación técnica, decisiones y plan de trabajo.

## API del validador (por acordar y congelar en Semana 0)

Propuesta inicial, pendiente de acuerdo escrito entre ambos integrantes:

- `init(ctx, pila, profundidad)`
- `feed(ctx, bytes, n)` — streaming por bloques, mismo resultado con bloques de 1 byte que con el payload completo.
- `finish(ctx, &resultado)` — resultado con ruta JSON Pointer y posición en bytes en caso de error.

## Reglas de trabajo

- Ningún cambio entra a la rama principal sin revisión del otro integrante (pull request).
- Historial de git con aportes de ambos durante todo el proyecto, no un solo commit al final.
- Cada viernes, 30 minutos: cada uno explica al otro lo que hizo esa semana, sin mirar el código.
- Las cifras del informe deben coincidir con la demo.

## Cómo correrlo en local (Semana 1)

```bash
python3 -m unittest discover -s tests -v
python3 bench/bench.py --size-kb 64 --reps 3
gcc -std=c11 -Wall -Wextra -Werror -O2 -o /tmp/wdpda_cli runtime/c/wdpda.c runtime/c/main.c
cargo test --manifest-path runtime/rust/Cargo.toml
```

_Pendiente (Semana 2): la primera rebanada vertical completa
(esquema mínimo → autómata → C → validación → métrica)._
