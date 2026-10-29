# Proyecto Final — Lenguajes Formales y Autómatas 2026-2 (UPTC Sogamoso)

## Validación en Tiempo Real de Esquemas JSON/XML mediante Autómatas de Pila Ponderados

Compilador que traduce un esquema JSON (JSON Schema, con extensión a un subconjunto de XSD) a un autómata de pila determinista ponderado (WDPDA) y emite un validador nativo en C y Rust que consume los bytes del payload en una sola pasada, sin construir un AST ni reservar memoria dinámica.

**Integrantes:** Jeferson Santiago Madera Gaona y Sergio Alexander Gómez Zapata
**Curso:** Lenguajes Formales y Autómatas 2026-2 — Prof. Marco Suárez (UPTC)
**Estado:** Semana 1 completada (26 oct – 1 nov 2026) y Semana 2
adelantada al 29 de octubre de 2026 — Fase 1 léxica: tokenizador
JSON/XML escrito a mano en Python con DFA, UTF-8 y streaming por
bloques, esqueleto del runtime en C y Rust
(`init`/`feed`/`finish`, pila fija, sin asignación dinámica),
13 pruebas (incluida equivalencia por bloques de 1 y 7 bytes) y
bench. Detalle en `docs/fase1-lexico.md`. Fase 2: parser LL(1) por
descenso recursivo y primera rebanada vertical (esquema → parser →
validación en C → error con ruta), con el prototipo `avance_mvp/`
corregido: 15/15 pruebas y compilación limpia con `-Werror`.
Detalle en `docs/fase2-parser.md`. Sin decisiones de alcance
cerradas con el profesor todavía (ver `docs/decisiones.md`).
Sergio (@sergiomezzz) ya aceptó la invitación y aportó el
`avance_mvp/` inicial; su revisión de la Semana 1 sigue pendiente
de confirmación explícita.

## Estructura

- `compiler/` — Fases 1–4: lexer, parser LL(1), análisis semántico, construcción del WDPDA y emisión del IR.
- `runtime/c/` — Validador generado en C.
- `runtime/rust/` — Validador generado en Rust.
- `web/` — Backend (API REST + WebSocket) y frontend: editor de esquema, compilación, visualización del autómata y panel de métricas.
- `tests/` — Casos válidos, inválidos, malformados, fuera del DPDA puro, de bombeo y extremos.
- `bench/` — Scripts de medición reproducibles (misma máquina, calentamiento, mediana).
- `docs/` — Documentación técnica, decisiones y plan de trabajo.
- `avance_mvp/` — prototipo de avance con esquema mínimo, validación en C, ejemplos de ejecución y pruebas (`avance_mvp/tests/run_tests.sh`).

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

## Cómo ejecutar el avance MVP en cualquier PC con GCC

Este prototipo no requiere dependencias adicionales. Solo necesitas un compilador C compatible con `gcc` o `clang`.

### 1) Compilar el validador

```bash
gcc -std=c11 -Wall -Wextra -Werror -O2 -o avance_mvp/validator avance_mvp/validator.c avance_mvp/schema.c
```

### 2) Ejecutar con un payload válido

```bash
./avance_mvp/validator avance_mvp/example_payload_valid.json
```

Salida esperada:

```text
VALIDO: OK
```

### 3) Ejecutar con un payload inválido

```bash
./avance_mvp/validator avance_mvp/example_payload_invalid.json
```

Salida esperada:

```text
INVALIDO: Field 'edad' expected integer but found string (/edad)
```

### 3b) Correr las pruebas del MVP

```bash
bash avance_mvp/tests/run_tests.sh
```

Salida esperada al final:

```text
== resultado: 15 pasaron, 0 fallaron ==
```

Códigos de salida del validador: 0 válido, 2 inválido, 1 error de
uso, de archivo o de esquema. El segundo argumento es opcional y
permite usar otro esquema: `./avance_mvp/validator payload.json
otro_esquema.json`. Hay que correrlo desde la raíz del repo, porque
el esquema por defecto se busca como `avance_mvp/example_schema.json`.

### 4) Si no tienes gcc instalado

- Linux (Debian/Ubuntu):
  ```bash
  sudo apt update
  sudo apt install build-essential
  ```
- macOS:
  ```bash
  xcode-select --install
  ```
- Windows:
  - usar WSL2 con Ubuntu, o
  - instalar MinGW/MinGW-w64 y ejecutar `gcc` desde la terminal.

Este avance sirve como base de demostración para mostrar que el proyecto ya tiene:
- un subconjunto formal del esquema,
- validación estructural en C con parser real (no búsqueda de texto),
- errores con ruta JSON Pointer y posición en bytes,
- y ejemplos ejecutables válidos e inválidos.

_Límites honestos: solo nivel superior del objeto, solo JSON, y
validación directa todavía sin WDPDA. El siguiente paso es la
validación anidada, la construcción del WDPDA y la unión con el
runtime en streaming. Ver `docs/fase2-parser.md`._
