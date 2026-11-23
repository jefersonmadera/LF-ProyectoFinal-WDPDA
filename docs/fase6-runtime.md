# Fase 6 — Validación en tiempo real (runtime y métricas)

Código: `runtime/c/wdpda_rt.c` (JSON), `runtime/c/wdpda_xml.c` (XML),
`runtime/rust/src/wdpda_rt.rs`, `compiler/engine.py` y
`compiler/xml_engine.py` (referencias ejecutables). Benchmark:
`bench/bench.py` → `bench/RESULTADOS.md`.

## 1. Tres capas, una sola pasada, una sola pila

```
bytes ──► [1] AFD léxico ──tokens──► [2] DPDA sintáctico ──eventos──► [3] capa de esquema
           UTF-8, cadenas,             SYN[8][11]: decide si               carriles por nivel:
           escapes, números,           un STRING es clave o valor;         AFD de claves/enum/pattern,
           literales                   pila de O/A (anidamiento)           registros, pesos tropicales
```

- **[1] Transductor léxico.** AFD byte a byte: validación UTF-8 (rangos
  que excluyen sobrelargas, sustitutos y valores > U+10FFFF), cadenas con
  escapes y `\uXXXX` (pares sustitutos), números con el AFD de RFC 8259 y
  literales. Los bytes **decodificados** de cada cadena alimentan, en el
  mismo paso, los AFD activos: trie de claves, `enum`, `pattern`.
- **[2] DPDA sintáctico de JSON.** 8 estados, tabla densa. Rechaza la
  entrada mal formada (peso +∞).
- **[3] Capa de esquema.** Por cada nivel de la pila hay un vector de
  **carriles** (subautómatas de nodos) con sus registros. Los números se
  normalizan en streaming a `(signo, dígitos, exponente)` con un búfer
  fijo de 64 dígitos. Así se compara exactamente contra las constantes y
  se calcula `multipleOf` con aritmética modular, **sin float**.

Las tres capas comparten **una sola pila** de marcos. Es la composición
de un transductor finito con un DPDA, que sigue siendo un DPDA (el
producto de un control finito con el del autómata de pila).

**Streaming de verdad.** `wd_feed` acepta bloques de cualquier tamaño, y
un token partido entre bloques (una cadena, un número, un `\u` o una
secuencia UTF-8) continúa en el siguiente. Las pruebas verifican el
mismo resultado (estado, peso y errores) con bloques de 1, 2, 7 y 4096
bytes y con la entrada completa.

## 2. Errores: JSON Pointer y posición en bytes

Cada violación registra el código (`TYPE`, `REQUIRED`, `PATTERN`,
`ANY_OF`, …), la **ruta JSON Pointer** (RFC 6901, con `~0` y `~1`), el
**byte** donde empieza el token y un mensaje. Se guardan los 16 primeros
y se **cuentan todos** (el peso). La ruta se mantiene en un búfer fijo de
1024 bytes que crece y decrece con la pila.

```json
{"status":"INVALID","weight":3,"errors":[
 {"code":"MIN_LENGTH","path":"/nombre","offset":10,"message":"longitud 0 < minLength 1"},
 {"code":"TYPE","path":"/edad","offset":20,"message":"tipo no permitido"},
 {"code":"PATTERN","path":"/correo","offset":33,"message":"no coincide con pattern '…'"}]}
```

En XML las rutas son de estilo XPath: `/pedido/item[2]/sku[1]`, `/@linea`.

Modos:

- **Completo** (por defecto): cuenta todas las violaciones.
- **Fallo temprano** (`--fail-fast`): se detiene en la primera
  violación. Las comprobaciones de cada nodo se evalúan en orden de costo
  estático creciente (fase 4 §2.1).

## 3. Límites (y por qué cada uno)

| Límite | Valor por defecto | Protege contra | Resultado |
| --- | --- | --- | --- |
| Profundidad de la pila | 256 (`WD_MAX_DEPTH`, configurable en ejecución hasta ese máximo) | desbordamiento de pila por anidamiento (`[[[[…`) | `DEPTH` |
| Carriles por nivel / totales | 64 / 4096 | explosión del producto de combinadores | `LIMIT` |
| Tamaño del payload | `--max-bytes` | agotamiento de recursos | `SIZE` |
| Tiempo | `--timeout-ms` | payloads lentos o abusivos | `TIME` |
| `uniqueItems` | 4096 elementos por arreglo | memoria no acotada | `LIMIT` |
| Dígitos significativos | 64 (comparaciones siguen exactas; `multipleOf` se reporta como límite) | números enormes | violación `MULTIPLE_OF` |
| Nombres XML | 128 bytes; pila de nombres 16 KB | etiquetas enormes | `LIMIT` |

Un límite **nunca** produce una respuesta incorrecta en silencio: o el
resultado es exacto, o se reporta el límite.

## 4. Métricas instrumentadas

El CLI generado (`validator <payload> [--bench R]`) imprime una línea
JSON con:

| Métrica | Cómo se mide |
| --- | --- |
| MB/s | bytes / mediana de R repeticiones (con 2 de calentamiento) |
| ciclos/byte | `rdtsc` (x86) / bytes |
| pico de memoria | `ctx_bytes` = `sizeof(wd_ctx_t)`, **fijo** e independiente del payload |
| asignaciones | `runtime_allocations: 0` (el runtime no llama a `malloc`; lo verifica `test_c_runtime_has_no_malloc`) |
| profundidad de pila | `max_depth` alcanzada |
| tamaño del autómata | estados y transiciones de δ, nodos, AFD |
| carriles | pico de carriles simultáneos |

## 5. Resultados (máquina de desarrollo)

Intel Core (familia 6, modelo 78), Windows 11, gcc 16.2 (`-O2`), Rust
1.99. Esquema del benchmark: `bench/schemas/eventos.json` (δ con 40
estados y 217 transiciones), lotes de eventos válidos. Cifras completas
en `bench/RESULTADOS.md` (se regeneran con `python bench/bench.py --rust`).

| 8 MB | MB/s | ciclos/byte | asignaciones | memoria |
| --- | ---: | ---: | ---: | ---: |
| **WDPDA C** | **76.0** | **34.1** | **0** | **1.1 MB fija** |
| WDPDA C, bloques de 64 B | 67.9 | 38.2 | 0 | 1.1 MB |
| WDPDA Rust | 52.4 | — | 0 | 1.1 MB |
| DOM C (mismas tablas y reglas) | 23.3 | — | 2 181 019 | 78 MB |
| `jsonschema` (Python, DOM) | 1.1 | — | — | 63 MB |

**Lectura honesta de los resultados:**

- Frente a una línea base **justa** (mismo lenguaje, mismas tablas y
  mismas comprobaciones, pero construyendo primero el árbol), el WDPDA
  es **3,3× más rápido**, no asigna memoria y usa memoria **constante**.
  El DOM necesita ~10 bytes de memoria por byte de entrada y 2,2
  millones de `malloc` para 8 MB.
- Frente a `jsonschema` la diferencia (~70×) mezcla la arquitectura con
  el hecho de que Python es interpretado. No se presenta como "mejora
  del algoritmo".
- 34 ciclos/byte **no es** el límite del enfoque (los parsers SIMD llegan
  a ~1 ciclo/byte). Medimos que el lexer y la sintaxis solos cuestan
  ~22 c/b. Ya se quitaron los cuellos de botella evidentes (`snprintf` en
  el camino crítico, el hash sin `uniqueItems`, la lista de AFD activos
  por cadena), y hay margen con SIMD o saltos directos en lugar de
  `switch`.
- **Hallazgo de corrección:** `jsonschema` marcó como **inválidos**
  eventos con `monto` múltiplo de 0.01 (por ejemplo, `1.11`), porque
  evalúa `multipleOf` con `float` binario (`1.11/0.01 = 110.99999…`). El
  WDPDA, con aritmética decimal exacta, acierta. Para que la comparación
  de tiempos sea justa, `jsonschema` se mide con `iter_errors`, que
  recorre todo el documento.

### 5.1 Bombeo `u·vᵏ·x·yᵏ·z`

Con `v = {"a":` e `y = }`, la palabra `{"a":ᵏ 1 }ᵏ` está en el lenguaje
para todo `k`, y cada bombeo exige `k` símbolos de pila. Es la
manifestación del lema de bombeo para lenguajes libres de contexto (`v`
e `y` se bombean juntos).

| k | bytes | WDPDA (s) | pila | memoria WDPDA | DOM (s) | malloc DOM | pico DOM |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 61 | 0.000003 | 10 | constante | 0.000006 | 52 | 2 KB |
| 100 | 601 | 0.000007 | 100 | constante | 0.000039 | 502 | 18 KB |
| 1000 | 6001 | 0.000067 | 1000 | constante | 0.00068 | 5002 | 180 KB |
| 10000 | 60001 | 0.000686 | 10000 | constante | 0.0073 | 50002 | 1.8 MB |

Tiempo **lineal** en `n` (×10 en `k` ⇒ ×10 en tiempo) y profundidad de
pila = `k`. Con el límite por defecto (256), `k = 300` da `DEPTH`.

**Carga sostenida:** 20 000 documentos seguidos en un proceso: ~16 700
doc/s (incluye E/S y el `wd_init` de cada uno).
