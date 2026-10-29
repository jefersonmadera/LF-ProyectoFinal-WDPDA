# Avance del proyecto: validación de esquemas JSON en C

## Slide 1 — Problema y objetivo
- Validar un payload JSON contra un esquema, en tiempo real y sin
  depender de un intérprete: el esquema se traduce a una validación
  nativa en C, con errores que dicen qué campo falló, dónde y por qué.
- Alcance de hoy: subconjunto de JSON Schema (`type`, `properties`,
  `required`; tipos string, integer, number, boolean, array, object,
  null) sobre objetos JSON de nivel superior.

## Slide 2 — Semana 1: lo léxico ya estaba
- Tokenizador JSON/XML en Python, escrito a mano (sin `json.loads`),
  con DFA, UTF-8 estricto y streaming por bloques: mismo resultado
  con la entrada completa que en bloques de 1 y 7 bytes.
- Esqueleto del runtime en C y Rust con API `init`/`feed`/`finish`,
  pila fija y sin asignación dinámica.
- 13 pruebas Python en verde y documentación en
  `docs/fase1-lexico.md`. Esa parte no se tocó en esta corrección.

## Slide 3 — Semana 2: del token al parser (lo que se corrigió)
- Gramática libre de contexto del subconjunto JSON, LL(1): un solo
  símbolo de anticipación decide la producción, sin retroceso.
  Detalle y FIRST/FOLLOW en `docs/fase2-parser.md`.
- El prototipo anterior buscaba claves con `strstr` y fallaba con el
  ejemplo del propio README. Ahora el payload y el esquema se
  parsean de verdad con descenso recursivo: espacios, claves dentro
  de strings y payloads malformados ya no lo engañan.
- Errores con ruta JSON Pointer y byte: `Field 'edad' expected
  integer but found string (/edad)`, `Missing required field:
  nombre (/nombre)`, `trailing data (byte 30)`.
- Compila limpio con `-Wall -Wextra -Werror`; límites explícitos
  (32 campos, anidamiento 64) en vez de desbordamientos silenciosos.

## Slide 4 — Demo en vivo (2 minutos)
1. `gcc -std=c11 -Wall -Wextra -Werror -O2 -o avance_mvp/validator avance_mvp/validator.c avance_mvp/schema.c`
2. `./avance_mvp/validator avance_mvp/example_payload_valid.json`
   → `VALIDO: OK` (código 0)
3. `./avance_mvp/validator avance_mvp/example_payload_invalid.json`
   → `INVALIDO: Field 'edad' expected integer but found string (/edad)` (código 2)
4. `bash avance_mvp/tests/run_tests.sh` → 15/15, incluyendo
   malformado, duplicado, anidado y clave dentro de un string.
- Salidas reales guardadas en `avance_mvp/validation_output.txt`.

## Slide 5 — Resultados
- MVP corregido: 15/15 pruebas, compilación limpia con `-Werror`.
- Semana 1 intacta: 13/13 pruebas Python.
- Rebanada vertical completa y demostrable: esquema → parser →
  validación → error con ruta. Sin cifras inventadas: no se presenta
  MB/s para el MVP porque medir invocaciones del CLI no mide el
  validador; la medición formal queda para la siguiente iteración.

## Slide 6 — Límites y siguiente paso
- Hoy: solo nivel superior del objeto, solo JSON, validación
  directa (todavía no WDPDA) y el CLI lee el archivo completo
  (el streaming vive en el runtime de la Semana 1).
- Siguiente paso: validación de campos anidados contra subesquemas,
  construcción del WDPDA a partir del esquema, unión con el runtime
  en streaming (`init`/`feed`/`finish`) y validación XML/XSD.
- Los commits de Sergio quedan en la historia: se corrigió hacia
  adelante, no se borró su aporte.
