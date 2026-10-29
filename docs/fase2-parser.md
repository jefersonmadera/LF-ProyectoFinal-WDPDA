# Fase 2 — Parser y primera rebanada vertical (Semana 2, adelantada)

Fecha de este documento: 29 de octubre de 2026. Cubre lo que se hizo
después de la Semana 1 y la corrección del prototipo `avance_mvp/`
de Sergio (commits `35a4b8a` y `8a3ffcc`, 28 de octubre).

## 1. Qué se corrigió y por qué

El prototipo original buscaba claves con `strstr` sobre el texto.
Eso no es parsear: un espacio después de una coma, una clave dentro
de un string o un payload malformado lo engañaban, y el ejemplo del
propio README daba `INVALIDO` en vez de `VALIDO: OK`. Además no
compilaba con `-Werror` y copiaba subobjetos a un búfer de 256 bytes
sin comprobar el límite.

La corrección no borra esos commits (la historia queda: el aporte de
Sergio se ve y se corrige hacia adelante). Cambia el mecanismo:

- El payload y el esquema se parsean de verdad, con descenso
  recursivo, en `avance_mvp/schema.c`. No queda ningún `strstr` en
  la ruta de validación.
- El código compila limpio con `gcc -std=c11 -Wall -Wextra -Werror -O2`.
- Se eliminó el código muerto (`skip_string`, `is_required_field`,
  `matches_type`) y todo límite es un error explícito, no un
  desbordamiento: máximo 32 campos, nombres de máximo 63 caracteres,
  anidamiento máximo 64.

## 2. Gramática del subconjunto (GLC)

```
Valor     -> Objeto | Arreglo | Cadena | Número | "true" | "false" | "null"
Objeto    -> "{" [ Miembro ("," Miembro)* ] "}"
Miembro   -> Cadena ":" Valor
Arreglo   -> "[" [ Valor ("," Valor)* ] "]"
Cadena    -> '"' (carácter normal | escape)* '"'
Número    -> ["-"] ( "0" | [1-9] dígito* ) [ "." dígito+ ]
             [ ("e"|"E") ["+"|"-"] dígito+ ]
```

Es LL(1) para este subconjunto: con un solo símbolo de anticipación
se decide la producción. Ejemplos: `{` abre objeto, `[` abre arreglo,
`"` abre cadena, `-` o dígito abre número, `t`/`f`/`n` abre literal.
No hay dos producciones que compartan el mismo primer símbolo, así
que no hace falta retroceso (backtracking). El parser de la Semana 1
(lexer por DFA) entrega los caracteres; este descenso recursivo es
el paso siguiente natural: del token al árbol, aunque aquí el árbol
no se materializa completo: el objeto de nivel superior se recorre
miembro a miembro y los valores anidados solo se validan
sintácticamente y se saltan. Eso es deliberado para la demo: la
validación de campos anidados contra un esquema anidado es el
siguiente paso, no algo que se finja aquí.

FIRST (resumen): FIRST(Valor) = { `{`, `[`, `"`, `-`, dígito, `t`,
`f`, `n` }. FOLLOW(Objeto) incluye `,`, `}`, fin de entrada y los
símbolos que pueden seguir a un valor en su contexto. No hay
recursión por la izquierda en ninguna producción, por eso el
descenso recursivo directo funciona sin reescribir la gramática.

## 3. De esquema a validación (rebanada vertical)

```
esquema JSON (type/properties/required)
      -> parse_schema_json()      (valida el esquema, no lo supone)
      -> Schema { fields[], required }
      -> validate_json_object()   (parsea el payload y, por cada
                                    miembro de nivel superior,
                                    compara tipo contra el esquema)
      -> VALIDO: OK  |  INVALIDO: mensaje + ruta JSON Pointer + byte
```

Errores con ruta y posición, ejemplos reales (ver
`avance_mvp/tests/run_tests.sh`):

- `Field 'edad' expected integer but found string (/edad)`
- `Field 'edad' expected integer but found float (/edad)`
- `Missing required field: nombre (/nombre)`
- `expected a JSON value (byte 9)` (payload malformado)
- `trailing data after JSON object (byte 30)`
- `duplicate field: edad (/edad)`

Códigos de salida: 0 válido, 2 inválido, 1 error de uso o de
archivo/esquema. El validador acepta un segundo argumento opcional
con la ruta del esquema; sin él usa `avance_mvp/example_schema.json`
y hay que correrlo desde la raíz del repo.

## 4. Qué es entero y qué no

Sin usar float, para no perder precisión: un número es entero si,
quitando el exponente, los dígitos fraccionarios que quedarían son
todos cero. Así `25`, `25.0` y `1e3` son enteros (como en JSON
Schema, donde un número con parte fraccionaria cero es entero) y
`25.5` no lo es. Un valor como `abc` sin comillas ya no pasa como
`0`: el parser lo rechaza como error de sintaxis antes de validar.

## 5. Resultados medidos el 29 de octubre de 2026

- Compilación: limpia con `-Wall -Wextra -Werror`.
- Pruebas del MVP: **15/15** en `avance_mvp/tests/run_tests.sh`
  (válido bonito y minificado, tipo equivocado, campo requerido
  ausente, decimal, malformado, basura final, clave dentro de un
  string, literal inválido, campo extra, anidado, duplicado).
- Pruebas de la Semana 1: **13/13** en Python, sin tocar
  (`compiler/`, `runtime/`, `tests/` no se modificaron en esta
  corrección).
- Caso límite: un esquema con 40 propiedades no se cae: da
  `schema has too many properties` y código 1. Una propiedad con
  descripción de 300 caracteres valida bien (antes desbordaba el
  búfer de 256).
- Rendimiento: NO se presenta una cifra de MB/s para el MVP, porque
  medir 1000 invocaciones del CLI (2,2 s en esta máquina, dominadas
  por el arranque del proceso) no mide el validador. La medición
  formal con payload grande y mediana queda para la siguiente
  iteración, con el mismo método de la Semana 1 (`bench/bench.py`).

## 6. Límites honestos (decirlos en la sustentación)

- Solo se valida el nivel superior del objeto. Los objetos y
  arreglos anidados se comprueba que sean JSON válido, pero no se
  validan contra un subesquema.
- Solo JSON, no XML todavía en este validador (el lexer XML de la
  Semana 1 existe, pero la validación XML contra XSD es trabajo
  posterior).
- No hay WDPDA todavía: la validación es directa esquema -> chequeo
  de tipos. La construcción del autómata ponderado a partir del
  esquema es el siguiente paso, no algo que este código ya haga.
- No hay streaming por bloques en este CLI: lee el archivo completo.
  El streaming (`init`/`feed`/`finish`) vive en el runtime de la
  Semana 1 y es el camino para unir ambas piezas.
- Los campos no listados en el esquema se permiten, porque el
  esquema de ejemplo no fija `additionalProperties: false`. Eso es
  una decisión documentada, no un olvido escondido.
