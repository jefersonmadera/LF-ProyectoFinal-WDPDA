# Fase 5 — Generación de código (C, Rust) y generador de payloads

Código: `compiler/codegen_c.py`, `compiler/codegen_rust.py`,
`compiler/payload_gen.py`, `compiler/xml_gen.py`. Runtimes:
`runtime/c/wdpda_rt.{h,c}`, `runtime/c/wdpda_xml.{h,c}`,
`runtime/rust/src/wdpda_rt.rs`.

## 1. Una sola fuente de verdad: el IR

El compilador produce un **IR en JSON** (`python -m compiler ir esquema`)
que leen **todos** los consumidores: el generador C, el generador Rust,
el motor de referencia Python, el visualizador web y este informe. Lo que
se dibuja es lo que se ejecuta. El formato está congelado en
[`ir.md`](ir.md).

## 2. Qué se genera

El generador **no emite lógica**: emite **datos** (`const` en C, `static`
en Rust) que un runtime fijo, revisado y probado, interpreta. Tiene tres
ventajas:

1. **Seguridad.** Un esquema malicioso no puede inyectar código. Las
   cadenas se escapan (`c_str`: octal para todo byte no imprimible y para
   `?`, lo que evita trigrafos).
2. **Corrección.** Los tres runtimes (C, Rust y Python) se comparan entre
   sí y contra oráculos (`tests/crosscheck*.py`).
3. **Tamaño predecible.** Las tablas van a la sección de solo lectura del
   binario.

`<nombre>_schema.c` contiene:

| Tabla | Contenido |
| --- | --- |
| `wd_dfa_t` × k | AFD **densos** por clases de bytes: `classes[256]` (byte → clase), `table[estado·ncls + clase]` → estado (−1 = muerto), `accept[estado]` → etiqueta (índice de clave/valor, o −1). |
| `wd_const_t` | constantes decimales exactas `(signo, dígitos, exponente)` |
| `wd_node_t` × n | un subautómata por nodo: tipos (máscara de 7 bits), enum, facetas de cadena y número (ordenadas por costo), objeto (trie de claves, hijos, máscara `required`, `patternProperties`, `additionalProperties`, dependencias), arreglo (`prefixItems`, `items`, `contains`, límites, `uniqueItems`) y combinadores. |
| `wd_schema_t` | raíz, tamaños, tamaño de δ (estados y transiciones) para las métricas |

Para XML se añaden `wx_elem_t` (declaración de elemento, AFD de contenido
de Glushkov, atributos) y `wx_schema_t`.

### 2.1 El DPDA sintáctico es una tabla densa

`runtime/c/wdpda_rt.c` contiene `SYN[8][11]`: estado sintáctico × clase
de token → acción. Es la misma máquina que
`docs/diagramas/lexico/json_dpda.jff` (JFLAP).

### 2.2 API C-ABI (FFI)

```c
void        wd_init  (wd_ctx_t *ctx, const wd_schema_t *s, uint32_t max_depth, int fail_fast);
wd_status_t wd_feed  (wd_ctx_t *ctx, const uint8_t *bytes, size_t n);   /* cualquier tamaño de bloque */
wd_status_t wd_finish(wd_ctx_t *ctx);
const wd_schema_t *wd_schema(void);   /* emitida por el generador */
```

Está declarada `extern "C"`, sin estado global y sin asignación dinámica:
el llamador reserva `wd_ctx_t` (en la pila, de forma estática o como
quiera). Se puede usar desde C++, Rust (FFI), Python (`ctypes`), Go
(cgo) o compilar a **WebAssembly** (`emcc`; la regla está en el `Makefile`
generado). Resultado: `ctx->status`, `ctx->weight` (peso tropical),
`ctx->errors[]` (código, JSON Pointer, byte, mensaje) y `ctx->error_count`.

### 2.3 Proyecto generado

```
build/<nombre>/c/      <nombre>_schema.c  wdpda_rt.[ch]  wdpda_cli.c  Makefile
build/<nombre>/rust/   Cargo.toml  src/{schema.rs, wdpda_rt.rs, main.rs}
build/<nombre>/<nombre>.ir.json
```

```powershell
python -m compiler compile tests\schemas\pedido.json --lang c,rust
```

El C compila con `gcc -std=c11 -Wall -Wextra -Werror -O2` sin
dependencias. El Rust compila con `cargo build --release` (con LTO y
`panic=abort`). La web descarga ambos en un `.zip`.

## 3. Generador de payloads (para las pruebas)

`payload_gen.py` recorre el autómata eligiendo **transiciones de peso 0**:

- elige un tipo permitido;
- genera claves requeridas y opcionales al azar, y respeta
  `dependentRequired`;
- genera longitudes y rangos dentro de los límites, y múltiplos de
  `multipleOf`;
- genera cadenas que aceptan los AFD de `pattern` mediante una
  **caminata aleatoria sobre los estados vivos**: estados desde los que
  se alcanza la aceptación, calculados por punto fijo;
- elige valores de `enum`.

`mutate()` aplica una mutación que debería forzar una transición de peso
1 (cambiar el tipo, quitar una requerida, añadir una clave, un valor
enorme, vaciar, duplicar un elemento). La validez del resultado **no se
supone**: siempre la decide el oráculo.

`xml_gen.py` hace lo mismo para XSD: camina el AFD de contenido de
Glushkov de cada elemento hasta un estado de aceptación, genera texto
según las facetas y los atributos requeridos, y lo **serializa a XML**.
`payload_gen.to_xml` serializa cualquier valor JSON a XML.

En la web: pestaña *Validar*, botones *Generar válido* y *Generar
inválido*.
