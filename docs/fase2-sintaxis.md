# Fase 2 — Análisis sintáctico: gramáticas LL(1)

Código: `compiler/grammar.py` (FIRST, FOLLOW, tabla predictiva y analizador
LL(1) con pila explícita), `compiler/grammars.py` (las cuatro gramáticas).
Reproducir las tablas: `python -m compiler grammar json|xml|schema|regex`.

> Este documento reemplaza en alcance a `fase2-parser.md`, que describe el
> parser de descenso recursivo del prototipo `avance_mvp/`. Este se
> conserva como historia del avance.

## 1. El algoritmo

Las gramáticas se escriben como texto BNF y el compilador **calcula**:

- `FIRST(α)`: los terminales con que puede empezar `α`, con `ε` si `α`
  es anulable. Es un punto fijo: se itera sobre las producciones hasta
  que ningún conjunto cambia.
- `FOLLOW(A)`: los terminales que pueden seguir a `A`, con `$` (fin de
  entrada) en `FOLLOW(S)`. También es un punto fijo.
- La tabla predictiva: `M[A, a] = A → α` si `a ∈ FIRST(α)`, o si
  `ε ∈ FIRST(α)` y `a ∈ FOLLOW(A)`.
- **Conflictos**: si una celda recibe dos producciones, la gramática NO
  es LL(1) y la construcción falla, nombrando la celda.

El analizador (`LL1Parser`) es el de libro: una pila de símbolos. En cada
paso se toma el tope: si es terminal, se empareja con el token; si es no
terminal `A`, se reemplaza por el cuerpo de `M[A, token]`. No hay
retroceso y cada token se mira una sola vez, así que el análisis es
lineal. Mientras analiza, construye el **árbol de derivación**, que existe
solo en compilación (o en la web, para mostrarlo).

## 2. Las cuatro gramáticas (todas LL(1), verificado en `tests/`)

| Gramática | No terminales | Terminales | Producciones | Celdas M | Conflictos |
| --- | ---: | ---: | ---: | ---: | ---: |
| JSON (RFC 8259) | 9 | 11 | 19 | 31 | 0 |
| XML bien formado | 6 | 6 | 15 | 24 | 0 |
| Metalenguaje de esquemas | 20 | 50 | 122 | 442 | 0 |
| Expresiones regulares (`pattern`) | 7 | 12 | 18 | 54 | 0 |

### 2.1 JSON

```
JSON → Value
Value → Object | Array | STRING | NUMBER | TRUE | FALSE | NULL
Object → { Members }
Members → Pair MembersTail | ε
MembersTail → , Pair MembersTail | ε
Pair → STRING : Value
Array → [ Elements ]
Elements → Value ElementsTail | ε
ElementsTail → , Value ElementsTail | ε
```

Se eliminó la recursión por la izquierda de la gramática "natural"
`Members → Members , Pair | Pair`, porque un LL(1) no puede expandir
`Members → Members …` sin consumir entrada. La forma `Pair MembersTail`
con la cola derecha es la estándar.

| No terminal | FIRST | FOLLOW |
| --- | --- | --- |
| JSON | `{ [ STRING NUMBER TRUE FALSE NULL` | `$` |
| Value | `{ [ STRING NUMBER TRUE FALSE NULL` | `$ , ] }` |
| Object | `{` | `$ , ] }` |
| Members | `STRING ε` | `}` |
| MembersTail | `, ε` | `}` |
| Pair | `STRING` | `, }` |
| Array | `[` | `$ , ] }` |
| Elements | `{ [ STRING NUMBER TRUE FALSE NULL ε` | `]` |
| ElementsTail | `, ε` | `]` |

Tabla predictiva (celdas vacías = error de sintaxis):

| M | `STRING` | `NUMBER` | `TRUE`/`FALSE`/`NULL` | `{` | `}` | `,` | `[` | `]` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| JSON | Value | Value | Value | Value | | | Value | |
| Value | STRING | NUMBER | el literal | Object | | | Array | |
| Object | | | | `{ Members }` | | | | |
| Members | Pair MembersTail | | | | ε | | | |
| MembersTail | | | | | ε | `, Pair MembersTail` | | |
| Pair | `STRING : Value` | | | | | | | |
| Array | | | | | | | `[ Elements ]` | |
| Elements | Value ElementsTail | Value ElementsTail | Value ElementsTail | Value ElementsTail | | | Value ElementsTail | ε |
| ElementsTail | | | | | | `, Value ElementsTail` | | ε |

**¿Por qué es LL(1)?** Las alternativas de cada no terminal empiezan con
terminales distintos (por ejemplo, `{` abre objeto y `[` abre arreglo), y
para los anulables `FIRST ∩ FOLLOW = ∅` (`Members`: `{STRING} ∩ {}} = ∅`).

**Árbol de derivación** de `{"a":[1,true]}`
(`python -m compiler tree payload.json`; en la web, pestaña *Validar*):

```
JSON
  Value
    Object
      {
      Members
        Pair
          STRING «a»
          :
          Value
            Array
              [
              Elements
                Value
                  NUMBER «1»
                ElementsTail
                  ,
                  Value
                    TRUE
                  ElementsTail → ε
              ]
        MembersTail → ε
      }
```

Para analizarlo se usan 23 pasos del analizador.

### 2.2 XML bien formado (sin DTD)

```
Document → Prolog Element Epilog
Prolog → PI Prolog | COMMENT Prolog | ε
Epilog → COMMENT Epilog | PI Epilog | ε
Element → TAG_EMPTY | TAG_OPEN Content TAG_CLOSE
Content → Item Content | ε
Item → Element | TEXT | COMMENT | PI
```

**El emparejamiento de nombres** (`<a>` … `</a>`) **no es libre de
contexto** sobre un alfabeto de nombres no acotado. Con un conjunto
finito de nombres sí lo es, pero la gramática necesitaría una producción
por nombre. Por eso la gramática trata los nombres como un solo terminal
y la igualdad apertura/cierre se verifica con una **acción semántica
sobre la pila** (`xsd.py::parse_xml_tree` en compilación y la pila de
nombres del runtime en validación). Es exactamente lo que hace el DPDA:
apilar el nombre en `<e>` y compararlo en `</e>`.

### 2.3 Metalenguaje de JSON Schema

Un esquema es un JSON cuyas claves son palabras clave. El **lexer del
esquema** (`schema.py::SchemaLexer`) reconoce cada palabra clave como un
terminal propio (`"type"`, `"minimum"`, …) con un trie (AFD). Así la
gramática **exige la forma del valor de cada palabra clave**:

```
Schema → { KwList } | TRUE | FALSE
Kw → "type" : TypeSpec
   | "minimum" : NUMBER          (13 palabras clave numéricas)
   | "properties" : { SchemaMap } (5 palabras clave de mapa)
   | "allOf" : [ SchemaList ]     (4 de lista de esquemas)
   | "items" : Schema             (8 de subesquema)
   | "pattern" : Str              (3 de cadena)
   | "uniqueItems" : Bool
   | "required" : [ StrList ] | "enum" : [ ValueList ] | "const" : Value
   | "dependentRequired" : { DepMap }
   | STRING : Value               (anotaciones: title, description...)
Str → STRING | "type" | "minimum" | …   (una clave de properties puede
                                        llamarse "type")
```

Consecuencia: parte del **meta-esquema se verifica sintácticamente**. Por
ejemplo, `{"minimum": "x"}` falla en el parser ("se esperaba NUMBER,
llegó STRING"), antes del análisis semántico. Lo que la gramática no
puede expresar (por ejemplo, que `minLength` sea entero no negativo o que
los nombres de `type` sean válidos) lo verifica la Fase 3.

### 2.4 Expresiones regulares de `pattern`

```
Regex → Alt
Alt → Concat AltTail
AltTail → '|' Concat AltTail | ε
Concat → Repeat Concat | ε
Repeat → Atom Quant
Quant → '*' | '+' | '?' | QUANT | ε
Atom → CHAR | '.' | CLASS | '(' Alt ')' | CARET | DOLLAR
```

**Un conflicto real que apareció y se corrigió.** En la primera versión
las anclas eran los terminales `'^'` y `'$'`. Pero `$` es también el
marcador de fin de entrada de la tabla LL(1), y la construcción reportó
`M[Concat, $]: 'Concat → Repeat Concat' vs 'Concat → ε'`. Desde `Concat`,
viendo `$`, no se puede saber si es el ancla (seguir concatenando) o el
fin. La solución fue lexear las anclas como `CARET`/`DOLLAR`. Es un buen
ejemplo de por qué la verificación LL(1) **automática** tiene valor.

## 3. Pruebas

`tests/test_fases.py`: las 4 gramáticas sin conflictos; FIRST/FOLLOW de
JSON; árbol de derivación y errores de sintaxis (`{"a":}`, `[1,]`,
`{"a" 1}`, `[1 2]`); documento XML con prólogo y comentarios.
