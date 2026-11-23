# Fase 3 — Análisis semántico del esquema

Código: `compiler/schema.py` (`SchemaAnalyzer`), `compiler/xsd.py` (XSD).

Entrada: el valor del esquema que produce el parser LL(1). Salida: un
grafo de nodos `SNode` (posiblemente cíclico) que la Fase 4 convierte en
el WDPDA.

## 1. Tabla de símbolos

Se recorre el esquema y se registra **cada subesquema** con su JSON
Pointer: `#`, `#/properties/edad`, `#/$defs/nodo`, `#/allOf/0`… Las
posiciones que contienen subesquemas son las de las palabras clave de
subesquema (`items`, `not`, …), de mapa (`properties`, `$defs`, …) y de
lista (`allOf`, `prefixItems`, …). También se registran los `$anchor`.

`$ref` se resuelve contra esa tabla:

- `#/...` (puntero local) o `#ancla`: se buscan en la tabla.
- `$ref` externo (`http://...`): error "solo referencias locales". Es
  una decisión de alcance y también de seguridad, porque el compilador
  nunca hace peticiones de red.
- `$ref` sin destino: error con el puntero donde aparece.
- Un nodo que solo tiene `$ref` es un **alias**: es el mismo subautómata
  y no se crea un nodo nuevo. Si `$ref` tiene hermanos, se trata como
  `allOf: [destino, resto]`.

## 2. Ciclos no productivos (análogo a la recursión por la izquierda)

Una **arista no consumidora** va de un subesquema a otro que valida **la
misma instancia**: `$ref`, `allOf/anyOf/oneOf`, `not`, `if/then/else`,
`dependentSchemas`. Un ciclo formado solo por esas aristas, por ejemplo

```json
{"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"allOf": [{"$ref": "#/$defs/a"}]}}}
```

haría que expandir `a` pida expandir `b`, que pide expandir `a`… **sin
consumir ningún byte**. Es el mismo problema que la **recursión por la
izquierda** en una gramática (`A → A α`), que deja a un parser LL en un
bucle infinito.

Se detecta con el algoritmo de **Tarjan** (componentes fuertemente
conexas) sobre el grafo de aristas no consumidoras. Cualquier componente
de tamaño > 1, o con un lazo, es un error que nombra los punteros del
ciclo.

La recursión **productiva** sí se permite. En `arbol.json`, el nodo se
refiere a sí mismo a través de `items` (que consume un `[`), y la
recursión del documento la lleva la pila del WDPDA.

## 3. Validación contra el meta-esquema

La gramática ya exige la forma de cada valor (sección 2.3 de
`fase2-sintaxis.md`). La Fase 3 añade:

| Regla | Ejemplo rechazado |
| --- | --- |
| `type` ∈ {null, boolean, integer, number, string, array, object}, sin repetir | `{"type":"entero"}` |
| enteros no negativos en `minLength`, `maxItems`, … | `{"minLength": -1}`, `{"minLength": 1.5}` |
| `multipleOf > 0` | `{"multipleOf": 0}` |
| `enum` no vacío; solo escalares en `enum`/`const` | `{"enum":[{"a":1}]}` |
| `required` sin repetidos | `{"required":["a","a"]}` |
| `pattern` compilable a AFD (subconjunto regular) | `(a)\1` (retrorreferencia), `(?=x)` (lookahead) |
| ≤ 64 claves distinguidas por objeto | máscara de bits de 64 |
| palabras clave fuera del subconjunto | `propertyNames`, `unevaluatedProperties`, `$dynamicRef`, `dependencies` (draft-07) |
| clave duplicada en el esquema | `{"type":"string","type":"integer"}` |

Las palabras clave de anotación (`title`, `description`, `$schema`,
`$id`, `default`, `examples`…) se aceptan y no generan estados. `format`
es una anotación en 2020-12 y tampoco valida. Las palabras clave
desconocidas generan un **aviso** y se ignoran.

## 4. Clasificación de palabras clave

Es el núcleo teórico de la Fase 3. Cada palabra clave se clasifica según
el **modelo de cómputo** que necesita:

| Cat. | Palabras clave | Por qué | Estrategia |
| --- | --- | --- | --- |
| **(a)** directa en el DPDA | `type`, `properties`, `patternProperties`, `additionalProperties`, `items`, `enum`, `const`, `minimum`, `maximum`, `exclusive*`, `multipleOf`, `minLength`, `maxLength`, `$ref`, `$defs` | Se decide con el control finito más la pila que ya existe para la estructura. Comparar un decimal con una constante, el residuo módulo *m* y un contador de longitud saturado son todos **lenguajes regulares** sobre los dígitos o los caracteres. | Transición según la clase del primer token; trie (AFD) de claves → subautómata hijo; trie de valores de `enum`; comparación decimal exacta; AFD de residuo. |
| **(b)** registros acotados | `required`, `dependentRequired`, `minItems`, `maxItems`, `minProperties`, `maxProperties`, `prefixItems`, `minContains`, `maxContains` | Necesitan memoria **por nivel** que depende del contenido ya leído, pero **acotada**: un bit por clave requerida o un contador que se satura en la constante del esquema. | Máscara de 64 bits y contadores saturados **dentro del símbolo de pila**. Γ sigue siendo finito. |
| **(c)** no deterministas o no libres de contexto | `anyOf`, `oneOf`, `allOf`, `not`, `if/then/else`, `contains`, `dependentSchemas`, `pattern`, `uniqueItems` | Varias ejecuciones simultáneas sobre la misma entrada (producto), determinización potencialmente exponencial (`pattern`) o memoria no acotada (`uniqueItems`). | Producto de autómatas de pila visibles con carriles acotados y combinación tropical; regex → NFA de Thompson → AFD por subconjuntos con tope de estados; hash canónico en tabla acotada (verificación diferida). Ver `fase4-wdpda.md` §6–7. |

La clasificación real de cada esquema se muestra al compilarlo
(`python -m compiler compile esquema.json` o la pestaña *Esquema* de la
web) y queda en el IR (`classification`).

## 5. Pruebas

`tests/test_fases.py::test_semantic_errors` (8 errores de la tabla
anterior), `test_recursive_ref_is_productive`,
`test_classification_categories`.
