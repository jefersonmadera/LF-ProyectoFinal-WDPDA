# Representación intermedia (IR) del WDPDA — versión 1 (congelada)

> Estado: **congelado** (versión 1). Lo producen `compiler/wdpda.py`
> (JSON Schema) y `compiler/xsd.py` (XSD). Lo consumen el generador C, el
> generador Rust, los motores Python, la web y el informe. Ver un IR
> real: `python -m compiler ir tests/schemas/persona.json`.

## Principio

Una sola representación del autómata en JSON: lo que se dibuja es lo que
se ejecuta.

## Campos de nivel superior

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `ir_version` | int | 1 |
| `format` | `"json"` \| `"xml"` | familia de esquema |
| `root` | int | nodo (o elemento, en XML) raíz; `-1` = acepta todo |
| `nodes` | lista | subautómatas (nodos del esquema) ya minimizados |
| `dfas` | lista | AFD densos (claves, enum, pattern, nombres, contenido XSD) |
| `consts` | lista | constantes decimales exactas `{lexeme, neg, digits, exp}` |
| `limits` | objeto | `max_depth`, `max_lanes`, `lane_pool`, `max_errors`, `max_patterns`, `unique_cap`, `path_bytes` |
| `flags` | objeto | `uses_unique` |
| `classification` | lista | `{keyword, category, strategy, count, pointers}` (Fase 3) |
| `formal` | objeto | resumen de δ: `states`, `transitions`, `stack_symbols`, `deterministic`, `determinism_conflicts`, `product_nodes`… |
| `stats` | objeto | nodos antes/después de minimizar, AFD y sus estados antes/después, ancho máximo de carriles |
| `warnings` | lista | avisos del análisis semántico |
| `elements`, `roots`, `root_names_dfa`, `upa` | (solo XML) | declaraciones de elemento y verificación UPA |

## Nodo (`nodes[i]`)

| Campo | Significado |
| --- | --- |
| `id`, `ptr`, `desc`, `title` | identidad y JSON Pointer en el esquema |
| `false` | esquema `false` |
| `types` | máscara: null=1, boolean=2, integer=4, number=8, string=16, array=32, object=64 (`number` incluye `integer`) |
| `enum` | `{strings: id de AFD o -1, values, nums: [id const], lits: true=1 false=2 null=4}` o `null` |
| `str` | `{min_len, max_len, patterns: [id AFD], pattern_src}` o `null` |
| `num` | `{checks: [[op, id const], …]}` con op ∈ ge, gt, le, lt, mul, **en orden de costo** |
| `obj` | `{keys, keys_dfa, key_child, key_declared, required (máscara), patterns: [[AFD, hijo]], additional (hijo, -1 = cualquiera, -2 = prohibido), min_props, max_props, dep_required: [[bit, máscara]], dep_schemas: [[bit, hijo]]}` |
| `arr` | `{prefix, items (-2 = items:false), contains (-3 = no hay), min_contains, max_contains, min_items, max_items, unique}` |
| `all_of`, `any_of`, `one_of` | listas de hijos (producto) |
| `not`, `if`, `then`, `else` | hijos o `-1` |
| `checks` | `[{cost, check}]`: orden de evaluación por costo estático |
| `xsd` | (XML) tipo léxico: 0 string, 1 boolean, 2 integer, 3 decimal, 4 double |

Referencias a hijos: entero ≥ 0 = nodo; `-1` = cualquier valor (⊤, no
crea carril); `-2` = prohibido.

## AFD (`dfas[i]`)

`{id, kind (keys|enum|pattern|names|attrs|content), source, states,
states_before_min, transitions, classes[256], ncls, table[states·ncls],
accept[states], symbols?}`. `table[s·ncls + classes[b]]` = siguiente
estado o −1 (muerto). `accept[s]` = etiqueta (índice de clave/valor, 0 en
`pattern`) o −1.

## API del runtime (acordada)

`init(ctx, esquema, profundidad_max, fail_fast)` · `feed(ctx, bytes, n)`
· `finish(ctx)` → estado `VALID | INVALID | MALFORMED | UTF8 | DEPTH |
LIMIT | SIZE | TIME`, peso tropical y errores con JSON Pointer y byte.
Igual en C (`wd_*`, `wx_*` para XML), Rust (`Ctx::init/feed/finish`) y
Python (`Validator.feed/finish`).
