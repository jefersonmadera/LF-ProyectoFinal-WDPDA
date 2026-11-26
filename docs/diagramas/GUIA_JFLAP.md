# Guía de los diagramas JFLAP, con entradas de prueba

Esta guía explica cada `.jff` de `docs/diagramas/` y da entradas para
probarlo. **Todas las entradas de esta guía se ejecutaron con un simulador
de JFLAP y dan exactamente el resultado indicado.**

## Cómo cargarlos en JFLAP

1. Abrir JFLAP (`java -jar JFLAP7.1.jar`) → **File → Open** → elegir el `.jff`.
2. Probar una sola entrada: **Input → Step by State** (paso a paso) o
   **Input → Fast Run**.
3. Probar varias a la vez: **Input → Multiple Run**. Se escribe una
   entrada por fila y se pulsa **Run Inputs**. Una fila vacía es la cadena
   vacía (λ).
4. En los autómatas de pila (tipo `pda`), JFLAP pregunta el modo de
   aceptación: elegir **Final State**. El fondo de la pila es `Z`, igual que
   el `Z₀` del proyecto.

Si los estados quedan amontonados, usar **View → Apply a Special Layout →
Spiral** o moverlos con el mouse.

## Convenciones de símbolos

Los **AFD léxicos y los AFD auxiliares** leen caracteres reales: se escribe
la palabra tal cual (`2024-02-29`, `nombre`, `ana@uptc.edu`).

Los **autómatas de pila** (DPDA de JSON y WDPDA de esquemas) no leen bytes,
sino **tokens**, porque los produce el analizador léxico. Cada token es un
carácter:

| Símbolo | Token | Ejemplo en JSON |
| --- | --- | --- |
| `{` `}` `[` `]` `:` `,` | ellos mismos | |
| `s` | cadena | `"Ana"` |
| `i` | entero | `25` |
| `d` | número con fracción o exponente | `3.5`, `1e9` |
| `t` / `f` | `true` / `false` | |
| `u` | `null` | |
| `k` | una clave **no declarada** en el esquema | `"apodo":` |
| `A`, `B`, `C`… | una clave **declarada** (ver `LEYENDA_JFLAP.txt` de cada carpeta) | `"nombre":` |

**Ejemplo de traducción.** Con el esquema persona (`A`=nombre, `B`=edad,
`C`=correo, `D`=rol, `E`=activo):

```text
{"nombre":"Ana","edad":25,"correo":"ana@uptc.edu"}   →   {A:s,B:i,C:s}
```

Pila: `Z` = Z₀, `O` = objeto cualquiera (⊤), `A` = arreglo cualquiera (⊤),
`a`, `b`… = el símbolo de pila de un nodo del esquema (ver la leyenda).

### Qué no muestra JFLAP

JFLAP no maneja pesos ni registros. Por eso el `.jff` de un esquema:

- **lleva solo las transiciones de peso 0** (las que respetan el esquema):
  una entrada con un tipo equivocado se **rechaza**, porque su transición
  pesaba 1;
- **no evalúa las guardas** (`required`, `minLength`, `minimum`, `pattern`,
  `enum`, `maxItems`…): esas viven en los registros y en los AFD
  auxiliares, no en el control finito.

En la sustentación conviene decirlo así: *"JFLAP muestra la parte de
estructura y tipos del WDPDA; los pesos y las guardas los ejecuta nuestro
motor, y se ven en la pestaña Autómata (vista con pesos) y en la traza de
la aplicación."*

---

## Fase 1: análisis léxico (`lexico/`)

### `numero_json.jff`: AFD de números (RFC 8259)

**Qué es.** Implementa la gramática regular
`número → [-] (0 | [1-9]d*) [. d+] [(e|E) [+|-] d+]`.

**Estados.** `inicio → menos → cero | entero → punto → frac → exp →
signo_exp → dig_exp`. Aceptan **cero, entero, frac y dig_exp**. `punto`,
`exp` y `signo_exp` no aceptan porque falta al menos un dígito.

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `0` | `01` | desde `cero` no hay transición con dígito: no hay ceros a la izquierda |
| `-12` | `1.` | termina en `punto`, que no es de aceptación |
| `3.14` | `.5` | desde `inicio` no se puede leer `.` |
| `-2.5E-3` | `1e` | termina en `exp` |
| `1e9` | `+1` | el `+` solo se permite en el exponente |
| `6.02e+23` | `--1` | solo se admite un signo |

### `cadena_json.jff`: AFD de cadenas con escapes

**Qué es.** Reconoce `"…"` con escapes `\" \\ \/ \b \f \n \r \t` y
`\uXXXX`. El alfabeto está **reducido** (algunas letras, dígitos, `A-F`)
para que el diagrama se pueda leer; el AFD real del runtime cubre todo
UTF-8.

**Estados.** `q0 →" dentro`; en `dentro` cualquier carácter normal vuelve
a `dentro`; `\` lleva a `escape`; `u` recorre `u1…u4` (cuatro dígitos
hexadecimales); `"` lleva a `fin`, que acepta.

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `""` | `"abc` | no llega a `fin` (falta la comilla de cierre) |
| `"abc"` | `abc"` | no empieza con comilla |
| `"a\nb"` | `"\q"` | `\q` no es un escape válido |
| `"\""` | `"\u12"` | `\u` exige exactamente 4 dígitos hexadecimales |
| `"é"` | `"\u00G0"` | `G` no es hexadecimal |
| `"a\/b"` | | |

### `literales_json.jff`: `true`, `false`, `null`

**Qué es.** Tres caminos (trie) que comparten el estado inicial. Acepta
solo en `true`, `false` y `null`.

| Acepta | Rechaza |
| --- | --- |
| `true`, `false`, `null` | `tru`, `nul` (prefijo: estado no final), `truex`, `nulll` (sobra un carácter), `True` (mayúscula) |

### `utf8.jff`: validación de UTF-8 por clases de bytes

**Qué es.** Valida que una secuencia de bytes sea UTF-8 bien formado. Hay
256 bytes posibles, así que se agrupan en clases y cada letra representa
una:

| Clase | Bytes | Significado |
| --- | --- | --- |
| `A` | 00–7F | ASCII (1 byte) |
| `B` | C2–DF | inicio de un carácter de 2 bytes |
| `C` | E1–EC, EE–EF | inicio de un carácter de 3 bytes |
| `D` | E0 | 3 bytes; el siguiente debe ser `z` (evita formas sobrelargas) |
| `E` | ED | 3 bytes; el siguiente debe ser `x` o `y` (evita sustitutos D800–DFFF) |
| `F` | F1–F3 | inicio de un carácter de 4 bytes |
| `G` | F0 | 4 bytes; el siguiente debe ser `y` o `z` (evita formas sobrelargas) |
| `H` | F4 | 4 bytes; el siguiente debe ser `x` (no pasar de U+10FFFF) |
| `x`, `y`, `z` | 80–8F, 90–9F, A0–BF | bytes de continuación |

**Estados.** `ok` es el inicial y el único que acepta (entre dos
caracteres completos). `c1`, `c2` y `c3` indican cuántos bytes de
continuación faltan.

| Acepta | Qué representa | Rechaza | Por qué se rechaza |
| --- | --- | --- | --- |
| `A` | `a` | `x` | continuación suelta |
| `AAA` | `abc` | `B` | carácter incompleto |
| `Bx` | `é` (C3 A9) | `Dx` | E0 80…: forma sobrelarga |
| `Czy` | `€` (E2 82 AC) | `Ez` | ED A0…: sustituto (no es un carácter) |
| `Dzx` | E0 A0 80 | `Gx` | F0 80…: forma sobrelarga |
| `Eyx` | ED 9F BF | `Hy` | F4 90…: mayor que U+10FFFF |
| `Fxyz`, `Gyxz`, `Hxxx` | 4 bytes | `Cx` | incompleto |

### `json_dpda.jff`: DPDA sintáctico de JSON (autómata de pila)

**Qué es.** El autómata de pila que reconoce JSON bien formado,
equivalente a la gramática LL(1) de la Fase 2. Es el nodo ⊤ (*cualquier
JSON*) del WDPDA.

**Estados.** Son los de la Tabla II del informe:

| Estado | Significado |
| --- | --- |
| `V(⊤)` | espera un valor (inicial) |
| `K1(⊤)` / `K2(⊤)` | tras `{` o tras `,`: espera una clave |
| `C(⊤,*)` | leyó una clave: espera `:` |
| `N(⊤)` | tras un miembro: espera `,` o `}` |
| `A1(⊤)`, `A2(⊤)`, `M(⊤)` | lo mismo para arreglos |
| `R` | retorno: no lee nada (ε) y decide a dónde volver según el tope |
| `F` | aceptación: la pila volvió a `Z` |

`{` apila `O` y `[` apila `A`; `}` y `]` desapilan. Con eso los corchetes
quedan emparejados, que es lo que un AFD no puede hacer (lema de bombeo).

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `{k:[i,s,{}],k:u}` | `{k:}` | falta el valor tras `:` |
| `[]`, `{}` | `[i,]` | coma final |
| `s` | `{s}` | una cadena sin `:` no es un miembro |
| `[[[i]]]` | `[i` | la pila no vuelve a `Z`: falta `]` |
| `{k:{k:{k:t}}}` | `]` | no hay nada que desapilar |
| `[i,d,s,t,f,u]` | `{k:i}}`, `[]]` | cierre de más |

Con **Step by State** se ve la pila subir con cada `{`/`[` y bajar con cada
`}`/`]`.

### `regex_abb.jff`: AFD mínimo de `^(a|b)*abb$`

**Qué es.** El ejemplo clásico de Aho et al.: regex → AFN de Thompson →
subconjuntos (5 estados) → Moore (4 estados). Cada estado `qN` recuerda
cuánto lleva del sufijo `abb`. Solo acepta `q3`.

| Acepta | Rechaza |
| --- | --- |
| `abb`, `aabb`, `babb`, `aababb`, `ababb` | λ (vacía), `ab`, `abba`, `bbb`, `abab` |

### `xs_date.jff`: `xs:date` con años bisiestos (43 estados)

**Qué es.** El tipo fecha de XSD como **lenguaje regular exacto**. El 29
de febrero solo se acepta si el año es bisiesto: divisible entre 4 y no
entre 100, salvo que sea divisible entre 400. Esa regla depende solo de
los últimos dígitos del año, que caben en un número finito de estados. La
zona horaria es opcional (`Z`, `+hh:mm` o `-hh:mm`, hasta ±14:00).

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `2024-02-29` | `2023-02-29` | 2023 no es bisiesto |
| `2000-02-29` | `1900-02-29` | 1900 es divisible entre 100 pero no entre 400 |
| `2023-12-31` | `2023-13-01` | mes 13 |
| `2023-12-31Z` | `2023-04-31` | abril tiene 30 días |
| `2023-01-15-05:00` | `23-01-01` | el año exige 4 dígitos |
| `2023-06-01+14:00` | `2023-1-01` | el mes exige 2 dígitos |
| `-0044-03-15` | `2023-01-01+15:00` | la zona horaria no pasa de 14:00 |

---

## Fase 4: WDPDA de esquemas

Cada carpeta tiene el WDPDA (`*_wdpda.jff`) y sus AFD auxiliares
(`*_dfaN_*.jff`). Recuerde: en el WDPDA las claves declaradas son
mayúsculas, `k` es una clave no declarada y las guardas no se evalúan.

### `ejemplo/`: el esquema del avance

Esquema: `{edad: entero, nombre: cadena, activo: booleano}`, con
requeridos `edad` y `nombre`. Claves: `A`=edad, `B`=nombre, `C`=activo.
Tiene 21 estados y 92 transiciones en δ; el `.jff` lleva las 57 de peso 0.

**`example_schema_wdpda.jff`**

| Acepta | JSON equivalente | Rechaza | Por qué se rechaza |
| --- | --- | --- | --- |
| `{A:i,B:s}` | `{"edad":25,"nombre":"Ana"}` | `{A:s,B:s}` | edad es cadena: esa transición pesa 1 |
| `{A:i,B:s,C:t}` | con `"activo":true` | `{A:i,B:i}` | nombre es número |
| `{B:s,A:i,C:f,k:u}` | otro orden y una clave extra (se permiten) | `{A:i,B:s,C:u}` | activo es `null` |
| `{A:i}` | falta `nombre`, pero `required` es una guarda | `{A:i,B:s` | mal formado: la pila no vuelve a `Z` |

El caso `{A:i}` se acepta en JFLAP y en la aplicación da **peso 1**
(REQUIRED). Sirve para explicar la diferencia entre el control finito y
los registros.

**`example_schema_dfa0_keys.jff`** es el trie de claves que convierte los
bytes de una clave en su identificador.

| Acepta | Rechaza |
| --- | --- |
| `edad`, `nombre`, `activo` | `edades`, `nombres`, `activa` |

### `persona/`: `additionalProperties`, `pattern` y `enum`

Claves: `A`=nombre, `B`=edad, `C`=correo, `D`=rol, `E`=activo. Pila: `a` =
el objeto persona.

**`persona_wdpda.jff`**

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `{A:s,B:i,C:s}` | `{A:i,B:i,C:s}` | nombre debe ser cadena |
| `{A:s,B:i,C:s,D:s,E:t}` | `{A:s,B:d,C:s}` | edad debe ser **entera** (`d` = 3.5) |
| `{E:f,C:s,A:s,B:i}` (el orden no importa) | `{A:s,B:i,C:s,k:s}` | `additionalProperties: false`: una clave no declarada pesa 1 |
| `{A:s,B:i}` (falta correo; es una guarda) | `{A:s,B:i,C:s,E:u}` | activo es `null` |
| | `[s]` | la raíz debe ser objeto |
| | `{A:s,B:i,C:s` | mal formado |

**`persona_dfa0_keys.jff`** (trie de claves)

| Acepta | Rechaza |
| --- | --- |
| `nombre`, `edad`, `correo`, `rol`, `activo` | `nom`, `edades`, `Nombre`, `email` |

**`persona_dfa1_pattern.jff`**: el `pattern` del correo,
`^[a-z0-9._-]+@[a-z0-9-]+\.[a-z]{2,6}$`. Pasó por regex → Thompson →
subconjuntos → Moore. Valida en tiempo lineal y sin retroceso, así que no
hay ReDoS.

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `ana@uptc.edu` | `ana@uptc` | falta `.dominio` |
| `juan.perez@correo.co` | `Ana@uptc.edu` | solo minúsculas |
| `a_b-1@x-y.org` | `ana@uptc.e` | el dominio exige de 2 a 6 letras |
| | `ana@uptc.edujaja` | más de 6 letras |
| | `@uptc.edu` | falta el usuario |
| | `ana.uptc.edu` | falta `@` |

**`persona_dfa2_enum.jff`**: el `enum` del rol.

| Acepta | Rechaza |
| --- | --- |
| `estudiante`, `docente`, `admin` | `Admin`, `doc`, `administrador`, `estudiantes` |

### `arbol/`: recursión con `$ref`

Esquema: `nodo = {valor: entero, hijos: [nodo, …]}` (máximo 4 hijos).
Claves: `A`=valor, `B`=hijos. Pila: `a` = objeto nodo, `b` = arreglo de
hijos.

**`arbol_wdpda.jff`** es el ejemplo para mostrar **por qué hace falta la
pila**. Al entrar a un hijo, la transición `{` con tope `b` apila `a` sobre
`b`; al cerrar `}`, `R` mira el tope y sabe que debe volver al arreglo. Así
la recursión del esquema se convierte en recursión de la pila, y el
autómata es finito aunque el árbol pueda tener cualquier profundidad.

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `{A:i}` | `{A:s}` | valor debe ser entero |
| `{A:i,B:[]}` | `{A:i,B:{A:i}}` | hijos debe ser arreglo |
| `{A:i,B:[{A:i},{A:i}]}` | `{A:i,B:[i]}` | cada hijo debe ser un nodo (objeto) |
| `{A:i,B:[{A:i},{A:i,B:[{A:i}]}]}` (3 niveles) | `{A:i,k:i}` | `additionalProperties: false` |
| `{B:[{A:i}],A:i}` | `{A:i,B:[{A:i}]` | mal formado |

Para la demo, correr `{A:i,B:[{A:i},{A:i,B:[{A:i}]}]}` con **Step by
State** y mostrar cómo la pila crece a `Z a b a b a` y vuelve a `Z`.

**`arbol_dfa0_keys.jff`**

| Acepta | Rechaza |
| --- | --- |
| `valor`, `hijos` | `valores`, `hijo`, `Valor` |

### `combinadores/`: `anyOf`, `oneOf`, `allOf`, `not`

Claves: `A`=id, `B`=forma, `C`=codigo.

**`combinadores_wdpda.jff`.** Los combinadores se validan con varios
**carriles** en paralelo (producto de autómatas de pila visibles), y el
peso se combina con `min` (`anyOf`) o `+` (`allOf`). Un carril que falla
pesa 1, pero el producto todavía tiene una salida de peso 0 por otro
carril. Por eso, en JFLAP, los valores de `id`, `forma` y `codigo`
**solo se verifican en su estructura**: `{A:t}` se acepta en JFLAP, y en
la aplicación da peso 1. Es la mejor evidencia de que los combinadores son
categoría **(c)**: no caben en un solo DPDA sin pesos.

| Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- |
| `{A:s}` | `[s]` | la raíz debe ser objeto |
| `{A:i}` | `s` | idem |
| `{A:s,B:{k:s,k:d},C:s}` | `{A:s` | mal formado |
| `{A:i,B:{k:s,k:i,k:i},k:t}` | `{A:s,}` | coma final |
| | `{A:s}}` | cierre de más |

**AFD auxiliares**

| Archivo | Qué es | Acepta | Rechaza |
| --- | --- | --- | --- |
| `combinadores_dfa0_keys` | claves raíz | `id`, `forma`, `codigo` | `ids`, `form`, `Codigo` |
| `combinadores_dfa1_keys` | claves de círculo | `tipo`, `radio` | `tipos`, `radios` |
| `combinadores_dfa2_keys` | claves de rectángulo | `tipo`, `ancho`, `alto` | `alt`, `anchos` |
| `combinadores_dfa3_enum` | `not: enum ["0000","1234"]` | `0000`, `1234` | `000`, `12345`, `4321` |
| `combinadores_dfa4_enum` | `const: "circulo"` | `circulo` | `circulos`, `Circulo`, `rect` |
| `combinadores_dfa5_enum` | `const: "rect"` | `rect` | `rectangulo`, `rec` |

`dfa3_enum` **acepta** los códigos prohibidos: el `not` invierte el
resultado en el WDPDA (peso 1 si este AFD acepta).

### `pedido_xsd/`: XSD con Glushkov

En XSD no hay WDPDA en `.jff`, porque la pila es de elementos con nombre.
Se exportan los AFD que usa cada marco de pila.

**AFD de contenido (Glushkov).** Su alfabeto son **nombres completos de
elementos**: en JFLAP cada transición lee una palabra entera, así que la
entrada se escribe pegada (`idclienteitemestado` = `<id/><cliente/><item/><estado/>`).
Que sea determinista es la regla UPA de XSD.

| Archivo | Modelo de contenido | Acepta | Rechaza | Por qué se rechaza |
| --- | --- | --- | --- | --- |
| `pedido_dfa9_content` | `(id cliente item+ estado)` | `idclienteitemestado`, `idclienteitemitemitemestado` | `idclienteestado` | `item+` exige al menos uno |
| | | | `clienteiditemestado` | orden incorrecto |
| | | | `idclienteitem` | falta `estado` |
| | | | `idclienteitemestadoitem` | `item` después de `estado` |
| `pedido_dfa2_content` | `(nombre (correo \| telefono))` | `nombrecorreo`, `nombretelefono` | `nombre`, `correonombre`, `nombrecorreotelefono` | falta la elección, orden incorrecto o las dos opciones |
| `pedido_dfa6_content` | `(sku cantidad precio nota?)` | `skucantidadprecio`, `skucantidadprecionota` | `skucantidad`, `cantidadskuprecio`, `skucantidadprecionotanota` | falta `precio`, orden incorrecto o `nota` repetida |

**Otros AFD**

| Archivo | Qué es | Acepta | Rechaza |
| --- | --- | --- | --- |
| `pedido_dfa4_pattern` | `xs:pattern` del SKU `[A-Z]{3}-[0-9]{4}` | `ABC-1234`, `XYZ-0001` | `abc-1234`, `AB-1234`, `ABC1234`, `ABC-123`, `ABC-12345` |
| `pedido_dfa7_enum` | enumeración del estado | `creado`, `pagado`, `enviado` | `cancelado`, `Creado`, `pagada` |
| `pedido_dfa10_names` | nombre de la raíz | `pedido` | `pedidos`, `Pedido` |
| `pedido_dfa8_names` | hijos de pedido | `id`, `cliente`, `item`, `estado` | `items`, `clientes`, `ids` |
| `pedido_dfa1_names` | hijos de cliente | `nombre`, `correo`, `telefono` | `tel`, `correos` |
| `pedido_dfa5_names` | hijos de item | `sku`, `cantidad`, `precio`, `nota` | `skus`, `precios`, `notas` |
| `pedido_dfa0_attrs` | atributos de pedido | `version` | `versio`, `Version` |
| `pedido_dfa3_attrs` | atributos de item | `linea` | `lineas`, `line` |

---

## Orden sugerido para la sustentación

1. `numero_json`: un AFD sencillo; mostrar por qué rechaza `01`.
2. `json_dpda`: con **Step by State** sobre `[[[i]]]`, ver la pila; luego
   `[i` rechazado. *"Un AFD no puede contar corchetes: hace falta la pila."*
3. `arbol_wdpda`: la recursión con `$ref` vive en la pila.
4. `persona_dfa1_pattern`: `pattern` como AFD, sin ReDoS.
5. `xs_date`: un lenguaje regular que parece necesitar aritmética (los
   bisiestos) y no la necesita.
6. `pedido_dfa9_content`: Glushkov y la regla UPA.

## Regenerarlos

```powershell
python -m compiler lexer-diagrams
```

```powershell
python -m compiler diagrams tests\schemas\persona.json -o docs\diagramas\persona
```

(`arbol.json`, `combinadores.json`, `avance_mvp\example_schema.json` y
`tests\schemas_xsd\pedido.xsd` se regeneran igual, cada uno en su carpeta.)
