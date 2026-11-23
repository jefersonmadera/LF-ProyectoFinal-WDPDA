# Informe final

**Validación en tiempo real de esquemas JSON/XML mediante autómatas de
pila ponderados**

Jeferson Santiago Madera Gaona · Sergio Alexander Gómez Zapata
Lenguajes Formales y Autómatas 2026-2 — Prof. Marco Suárez — UPTC Sogamoso

---

## Resumen

Construimos un compilador que traduce un esquema JSON Schema (draft
2020-12, subconjunto) o XSD (subconjunto) a un **autómata de pila
determinista ponderado (WDPDA)** y emite validadores nativos en **C y
Rust**. Los validadores procesan el payload **en una sola pasada, en
streaming, sin construir un AST y sin asignar memoria dinámica**. Los
pesos viven en el **semianillo tropical**: el peso de un documento es su
número de violaciones (0 = válido, +∞ = mal formado), y el mínimo
tropical da directamente la semántica de `anyOf`.

Sobre 8 MB de eventos, el validador C alcanza **76 MB/s (34
ciclos/byte)** con memoria constante (1,1 MB) y 0 asignaciones. Una línea
base DOM en C, con las **mismas tablas y reglas**, logra 23 MB/s y
necesita 2,2 millones de asignaciones y 78 MB. La corrección se verificó
de cuatro maneras:

- 6000 payloads JSON contra `jsonschema` y 1200 documentos XML contra
  `lxml`, sin diferencias;
- 2280 payloads con resultados idénticos (estado, peso y errores con su
  ruta) en las tres implementaciones;
- 104 pruebas automáticas;
- un corpus de 54 casos en una base NoSQL.

## 1. Introducción

Los validadores habituales convierten cada payload en un árbol (DOM) y
luego lo recorren interpretando el esquema. Eso cuesta memoria
proporcional al documento, millones de asignaciones y un segundo
recorrido. La pregunta del proyecto es: **¿qué clase de autómata basta
para validar un esquema, y qué se gana compilándolo?**

La respuesta tiene tres partes:

1. La **estructura** de JSON/XML necesita una pila: el lenguaje no es
   regular (lema de bombeo, `fase4-wdpda.md` §1).
2. La mayoría de las palabras clave se deciden con **control finito y
   registros acotados** dentro de esa pila (categorías a y b).
3. Las demás necesitan **producto de autómatas**, **determinización
   acotada** o **verificación diferida** (categoría c).

## 2. Definición formal del lenguaje

- **Alfabeto**: bytes UTF-8, validados por un AFD de 8 estados sobre 11
  clases de bytes (`docs/diagramas/lexico/utf8.svg`).
- **Léxico** (Fase 1): gramáticas regulares y AFD de números, cadenas
  (escapes y `\u`), literales y etiquetas XML (`fase1-lexico.md`).
- **Sintaxis** (Fase 2): gramáticas libres de contexto LL(1) de JSON,
  XML, el metalenguaje de esquemas y las regex de `pattern`, con FIRST,
  FOLLOW, tablas predictivas y árboles de derivación
  (`fase2-sintaxis.md`).
- **Subconjunto de JSON Schema reconocible por un DPDA con registros
  acotados** (Fase 3, `fase3-semantica.md` §4):
  - **(a) directas**: `type`, `properties`, `patternProperties`,
    `additionalProperties`, `items`, `enum`, `const`, `minimum`,
    `maximum`, `exclusive*`, `multipleOf`, `minLength`, `maxLength`,
    `$ref`/`$defs`.
  - **(b) registros**: `required` y `dependentRequired` (máscara de 64
    bits), `minItems`, `maxItems`, `minProperties`, `maxProperties`,
    `prefixItems`, `minContains`, `maxContains` (contadores saturados).
  - **(c) fuera del DPDA puro**: `allOf`, `anyOf`, `oneOf`, `not`,
    `if/then/else`, `contains` y `dependentSchemas` (producto de VPA con
    carriles acotados); `pattern` (determinización acotada);
    `uniqueItems` (verificación diferida con hash).

## 3. Arquitectura del compilador

```
esquema (.json/.xsd)
  │  Fase 1  lexer DFA (+ trie de palabras clave)
  │  Fase 2  parser LL(1) por tabla (metalenguaje) → árbol de derivación
  │  Fase 3  tabla de símbolos, $ref, Tarjan (ciclos no productivos),
  │          meta-esquema, clasificación (a)/(b)/(c)
  │  Fase 4  WDPDA: minimización de nodos (Moore), AFD (Thompson →
  │          subconjuntos → Moore; tries; Glushkov para XSD), δ explícito,
  │          verificación de determinismo  →  IR (JSON)
  │  Fase 5  codegen C / Rust (tablas const) + generador de payloads
  ▼
validador nativo  ── Fase 6: streaming, JSON Pointer, límites, métricas
  │
web (Fase 7): REST + WebSocket, sandbox, MongoDB, despliegue en contenedor
```

El **IR** es la frontera entre la teoría y el runtime: lo leen los tres
runtimes, la web y la documentación (`ir.md`).

## 4. El autómata

Definición `M = (Q, Σ, Γ, δ, q0, Z0, F, w)`, construcción composicional,
pesos tropicales, determinismo, minimización y producto:
**`fase4-wdpda.md`**. Para el esquema del MVP, δ tiene 21 estados, 92
transiciones y |Γ| = 4. Para el más grande de las pruebas (`pedido.json`),
49 estados y 258 transiciones. El verificador automático confirma que
**todos son deterministas**.

## 5. Análisis de complejidad

**Validación en `O(n)` en bytes.** Cada byte se procesa una sola vez:

- en el AFD léxico, un paso;
- dentro de una cadena, un paso por cada AFD activo (como máximo
  `B · (1 + 8)`);
- por cada token, trabajo `O(B)` en los carriles.

Con `B` acotado por una constante del esquema, el total es `O(n)`.
**Pila `O(d)`** con `d` = profundidad de anidamiento: un marco por nivel,
de tamaño fijo. El experimento de bombeo lo confirma: con k ×10 el tiempo
crece ×10 y la profundidad es exactamente k (`fase6-runtime.md` §5.1).

**Tamaño del autómata.** Sin expandir registros, `|Q| = O(|nodos| ·
claves)` y `|δ| = O(|Q| · |Σ|)`. Si los registros se expandieran en el
control finito, `required` con `k` claves multiplicaría Γ por `2^k`. Por
eso se guardan como bits en el marco.

**Riesgo de explosión al determinizar:**

- `pattern`: la construcción de subconjuntos es `O(2^m)` en el peor
  caso, con `m` estados del NFA. Ejemplo medido: `(a|b)*a(a|b){14}`
  supera 4096 estados y se rechaza.
- Combinadores: determinizar un VPA es `2^O(n²)`. Se evita con la
  determinización **acotada en ejecución** (carriles ≤ 64 por nivel).
- Modelos de contenido XSD: Glushkov tiene tantas posiciones como
  apariciones de nombres, y UPA garantiza el determinismo **sin**
  construcción de subconjuntos.

## 6. Decisiones de diseño

Las decisiones están en `decisiones.md`, cada una con la alternativa que
se descartó. Las principales:

1. **Emitir tablas y no código.** Es seguro (no hay inyección), se puede
   verificar con pruebas cruzadas y el runtime se escribe una sola vez.
2. **Tres capas sobre una pila** (lexer → DPDA sintáctico → esquema), en
   vez de un único AFD sobre bytes gigantesco.
3. **Semianillo tropical** para los pesos. Unifica validez, conteo de
   violaciones y `anyOf`.
4. **Decimales exactos** en lugar de `double`.
5. **Producto con carriles** para los combinadores, apoyado en el cierre
   de los VPA bajo intersección.
6. **Glushkov** para XSD, con verificación UPA y despliegue anidado de
   `maxOccurs`.

## 7. Manejo de errores y casos especiales

- **Errores con ruta.** Cada error lleva JSON Pointer (RFC 6901) o
  XPath, byte y código. El WDPDA es total sobre el JSON bien formado:
  tras una violación sigue validando, así que reporta todas.
- **Modo fallo temprano**, con comprobaciones ordenadas por costo.
- **Especiales**: `1 == 1.0` en `enum`/`uniqueItems`, `true != 1`,
  `1e400`, enteros de 31 dígitos, pares sustitutos `😀`,
  `minLength` en puntos de código (no en bytes), objetos con claves en
  distinto orden (`uniqueItems`) y esquemas `true`/`false`.
- **Límites explícitos**, nunca silenciosos (`fase6-runtime.md` §3).

## 8. Vulnerabilidades

ReDoS, desbordamiento de pila, XXE, *billion laughs*, `$ref` cíclicos y
externos, inyección en el código generado, agotamiento de memoria,
precisión numérica y ejecución de código en el servidor: **`seguridad.md`**.

## 9. Resultados de las pruebas

**`pruebas.md`**. Resumen:

- 6000 payloads JSON y 1200 documentos XML diferenciales contra
  oráculos: 0 diferencias.
- 2280 y 634 casos cruzados entre implementaciones: 0 diferencias.
- 104 pruebas automáticas y un corpus de 54 casos en NoSQL: 54/54.

Encontramos cuatro errores de los oráculos (`multipleOf` y `1e30` con
`float` en `jsonschema`, claves duplicadas en `json.loads` y el límite
de 24 dígitos de libxml2).

## 10. Generación de código

`fase5-codegen.md`: AFD densos por clases de bytes, nodos y constantes
como `const`/`static`, API C-ABI y WASM opcional. Los validadores C y
Rust compilan sin advertencias (`-Werror`).

## 11. Pruebas de bombeo

`fase6-runtime.md` §5.1. La familia `u·vᵏ·x·yᵏ·z` ilustra el lema de
bombeo para lenguajes libres de contexto (bombear `v` e `y` juntos
mantiene la palabra en el lenguaje) y mide el costo real: tiempo lineal y
pila igual a `k` en el WDPDA, frente a asignaciones y memoria lineales en
el DOM.

## 12. Conclusiones

1. La validación de esquemas cae casi por completo en la clase de los
   **autómatas de pila visibles con registros acotados**. Esa clase es lo
   bastante rica para casi todo JSON Schema y lo bastante restringida para
   cerrar bajo intersección y validar en `O(n)` con pila `O(d)`.
2. Lo que queda fuera (`uniqueItems`) se puede **demostrar** fuera
   (conjunto engañador) y tratar con un registro acotado explícito.
3. Compilar el esquema rinde: 3,3× frente a un DOM equivalente en C, con
   memoria constante. La aritmética exacta evita errores reales de
   validadores populares.

**Trabajo futuro**:

- lexer SIMD;
- backend XML en Rust;
- `propertyNames` y `unevaluated*` (requieren anotaciones dinámicas);
- detección de claves duplicadas no distinguidas con un hash acotado.

## Referencias

- R. Alur y P. Madhusudan, *Visibly Pushdown Languages*, STOC 2004.
- A. Aho, M. Lam, R. Sethi, J. Ullman, *Compilers: Principles,
  Techniques, and Tools*, 2.ª ed. (FIRST/FOLLOW, LL(1), Thompson,
  subconjuntos).
- J. Hopcroft, R. Motwani, J. Ullman, *Introduction to Automata Theory,
  Languages, and Computation* (lemas de bombeo, PDA, Myhill–Nerode).
- V. M. Glushkov, *The abstract theory of automata*, 1961; A.
  Brüggemann-Klein y D. Wood, *One-unambiguous regular languages*, 1998
  (UPA y Glushkov).
- M. Mohri, *Semiring frameworks and algorithms for shortest-distance
  problems*, 2002 (semianillo tropical).
- RFC 8259 (JSON), RFC 6901 (JSON Pointer), JSON Schema 2020-12, W3C XML
  Schema 1.1.
