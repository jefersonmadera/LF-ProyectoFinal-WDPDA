# Decisiones

Cada decisión registra su alternativa y por qué se descartó. Es material directo para el informe final.

## Decidido en Semana 0 (2026-10-24)

- **Repositorio único con la estructura de la guía:** `compiler/`, `runtime/c`, `runtime/rust`, `web/`, `tests/`, `docs/`, `bench/`.
  - Alternativa descartada: un repo por integrante. Se descartó porque el IR compartido es la frontera entre teoría/compilador y runtime/plataforma.
- **Revisión cruzada obligatoria:** ningún cambio entra a la rama principal sin aprobación del otro en pull request.
- **Sigla consistente:** WDPDA (weighted DPDA), no "DPDA ponderado (DPDA)" como aparece en el enunciado.

## Pendiente — NO aclarar con el profesor por el momento (decisión de Jeferson, 2026-10-24)

Estas dudas quedan anotadas aquí para retomarlas cuando Jeferson lo decida, sin enviar nada todavía:

- [ ] ¿Se exigen ambos backends, C **y** Rust, o basta uno con el otro opcional?
- [ ] ¿Qué tamaño de subconjunto XSD esperan (sequence, choice, minOccurs/maxOccurs, tipos simples)?
- [ ] ¿El semianillo tropical debe implementarse en tiempo de ejecución o basta con modelarlo y usarlo para ordenar comprobaciones?
- [ ] ¿`pattern` debe aceptar toda la sintaxis ECMA-262 o un subconjunto regular documentado?
- [ ] Fecha exacta de entrega y de sustentación, y si hay entregas parciales.

## Decidido en Semana 1 (2026-10-26, pendiente de revisión de Sergio)

- **Lenguaje del compilador: Python 3.12.** Se implementó la
  Fase 1 en Python por iteración rápida, como recomienda la guía.
  Si Sergio prefiere Rust para el compilador, se revisa antes de
  la Semana 2; el IR en JSON hace que el cambio sea posible sin
  tocar los runtimes.
- **API del runtime (esqueleto):** `init` / `feed` / `finish` en C
  y en Rust, con la misma semántica y los mismos códigos de error.
  La firma exacta en cada lenguaje está en `runtime/c/wdpda.h` y
  en `runtime/rust/src/lib.rs`; falta congelarla por escrito
  entre ambos (ver abajo).

## Pendiente — acuerdo entre integrantes

- [ ] Reparto A (teoría/compilador) / B (runtime/plataforma): ¿quién es quién?
- [x] ~~Formato exacto del IR~~ → **propuesta implementada**: `docs/ir.md` (versión 1). Falta la aprobación escrita de ambos.
- [x] ~~API `init/feed/finish` definitiva~~ → **propuesta implementada** en C (`wd_*`, `wx_*`), Rust y Python (`docs/ir.md`, sección final). Falta la aprobación escrita de ambos.
- [ ] Nube y NoSQL: **propuesta**: Azure Container Apps + Cosmos DB for MongoDB (o MongoDB Atlas en AWS). La app usa `pymongo`, así que sirve para ambas; DynamoDB exigiría reimplementar `web/backend/store.py`. Falta decidir y crear la cuenta.

## Decisiones técnicas de las Fases 2–7 (noviembre 2026, propuestas para revisión)

Cada una con su alternativa descartada; el detalle está en `docs/`.

- **Analizador LL(1) dirigido por tabla, con las tablas calculadas por el
  compilador** (FIRST/FOLLOW/M a partir de la gramática en texto).
  - Descartado: descenso recursivo escrito a mano (como el del MVP).
    Funciona, pero no demuestra que la gramática sea LL(1); la tabla sí,
    y detectó un conflicto real (`$` en las regex).
- **El metalenguaje de esquemas como gramática LL(1)**, con las palabras
  clave como terminales.
  - Descartado: parsear el esquema como JSON genérico y validarlo
    después. Así se pierde la verificación sintáctica de parte del
    meta-esquema.
- **Tres capas sobre una pila** (AFD léxico → DPDA sintáctico → carriles
  del esquema).
  - Descartado: un único AFD/DPDA sobre bytes con las claves expandidas
    en el control. Explota el tamaño (cada clave por cada estado) y no
    deja ver la teoría por capas.
- **Pesos en el semianillo tropical = número de violaciones** (0 válido,
  +∞ mal formado); `anyOf` = min.
  - Descartado: pesos solo como costo de ordenamiento. Esa opción queda
    como costo estático secundario (`checks` ordenados por costo).
- **Combinadores como producto de VPA con carriles acotados**
  (determinización en ejecución).
  - Descartado: determinizar en compilación (`2^O(n²)`) o rechazar la
    categoría (c).
- **`pattern` por regex → Thompson → subconjuntos → Moore**, con un tope
  de 4096 estados.
  - Descartado: un motor de regex con retroceso (ReDoS) o la sintaxis
    ECMA completa (las retrorreferencias no son regulares).
- **Decimales exactos** (dígitos + exponente; `multipleOf` modular).
  - Descartado: `double`. Las pruebas mostraron errores reales de
    `jsonschema` por usarlo.
- **`uniqueItems` con hash canónico de 64 bits en una tabla acotada.**
  - Descartado: guardar los elementos (memoria no acotada) o no
    soportarlo.
- **El generador emite tablas `const`, no lógica**; el runtime es fijo.
  - Descartado: generar `switch`/`goto` por estado. Es más rápido en
    teoría, pero el código generado es más difícil de verificar y más
    expuesto a inyección.
- **XSD con Glushkov y verificación de UPA**; despliegue anidado de
  `maxOccurs`.
  - Descartado: Thompson + subconjuntos. No permitiría detectar las
    violaciones de UPA, que XSD exige rechazar.
- **Motor de referencia en Python** como especificación ejecutable, más
  pruebas cruzadas con C y Rust.
  - Descartado: probar solo C. Sin una segunda implementación no hay
    oráculo interno.
- **Web con FastAPI + JS sin framework**; DOT con viz.js y gráficas con
  Chart.js desde CDN.
  - Descartado: Angular/React. Más infraestructura para una SPA de 6
    pestañas.
- **XML solo en el backend C** (Rust cubre JSON).
  - Descartado por tiempo: portar `wdpda_xml.c` a Rust. Queda como
    trabajo futuro y se dice así en la sustentación.
