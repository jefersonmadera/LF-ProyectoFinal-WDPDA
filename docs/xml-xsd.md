# Extensión XML / XSD

Código: `compiler/xml_lexer.py`, `compiler/xsd.py`, `compiler/xml_engine.py`,
`compiler/xml_gen.py`, `runtime/c/wdpda_xml.c`. Esquemas de prueba:
`tests/schemas_xsd/`.

## 1. Subconjunto de XSD soportado

| Construcción | Soporte |
| --- | --- |
| `xs:element` global y local (`name`, `type`, `ref`, `minOccurs`, `maxOccurs`, `unbounded`) | sí |
| `xs:complexType` con `xs:sequence` / `xs:choice` anidados, `xs:attribute` (`use="required"`), `mixed`, `simpleContent/extension` | sí |
| `xs:simpleType` con `xs:restriction`: `enumeration`, `pattern`, `length`, `minLength`, `maxLength`, `min/maxInclusive`, `min/maxExclusive` | sí |
| Tipos predefinidos: `string`, `token`, `boolean`, `decimal`, `integer`, `int`, `long`, `short`, `byte`, `positiveInteger`, `nonNegativeInteger`, `double`, `float`, `date`, `dateTime`, `time`, `anyURI`… | sí |
| `xs:all`, `xs:any`, grupos, `list`/`union`, varios espacios de nombres | no (error claro) |

## 2. Del modelo de contenido al AFD: construcción de Glushkov

El contenido de un tipo complejo es una **expresión regular sobre los
nombres de los hijos**. Por ejemplo, el de `<pedido>` es
`(id cliente item+ estado)` y el de `<cliente>` es
`(nombre (correo | telefono))`.

Se compila con la construcción de **Glushkov** (autómata de posiciones):
cada aparición de un nombre en la expresión es una **posición**; se
calculan `nullable`, `first`, `last` y `follow`. Los estados son
`{inicio} ∪ posiciones` y la transición `p --a--> q` existe si
`q ∈ follow(p)` y el símbolo de `q` es `a`.

**UPA ⇔ Glushkov determinista.** XSD exige la regla *Unique Particle
Attribution*: cuando se lee un hijo, debe haber a lo sumo una partícula
del modelo que pueda consumirlo. Eso es **exactamente** que el autómata
de Glushkov sea determinista: que no haya dos posiciones con el mismo
símbolo en `first` ni en ningún `follow(p)`. El compilador lo verifica y
rechaza los modelos ambiguos, como hacen los validadores reales:

```
<xs:choice> <xs:sequence> a b </xs:sequence> <xs:sequence> a c </xs:sequence> </xs:choice>
→ error: viola Unique Particle Attribution (<a> ambiguo en inicio)
```

**Hallazgo: el despliegue de `maxOccurs` importa.** `x{0,3}` se puede
desplegar como `x? x? x?` o como `(x (x (x)?)?)?`. Las dos expresiones
denotan **el mismo lenguaje**, pero la primera **no** es determinista en
Glushkov: tras leer una `x` no se sabe si la consumió la primera, la
segunda o la tercera posición. La segunda sí lo es. El esquema
`catalogo.xsd` (`categoria` con `maxOccurs="3"`, que `lxml` acepta) lo
detectó: con el primer despliegue el compilador lo rechazaba. Se usa el
despliegue anidado. Las ocurrencias se acotan a 100, porque desplegar
`{0,n}` cuesta `O(n)` posiciones.

Después, el AFD de Glushkov se minimiza (Moore).

## 3. El WDPDA de XML

- **Pila**: un marco por elemento abierto con (declaración, estado del
  AFD de contenido, máscara de atributos vistos, verificador del texto).
  Se apila en `<e>` y se desapila en `</e>`: es un autómata de pila
  **visible**, igual que en JSON.
- **Apertura `<hijo>`**: el trie de nombres del padre da el símbolo y el
  AFD de contenido del padre avanza. Si no hay transición, el error es
  `XML_CONTENT` (fuera de orden, peso 1). Un nombre no declarado da
  `XML_UNDECLARED`, y ese hijo se valida como ⊤.
- **Cierre `</e>`**: el nombre debe coincidir con el de la pila (buena
  formación; si no, MALFORMED). El AFD de contenido debe estar en un
  estado de aceptación; si no, `XML_INCOMPLETE`.
- **Atributos**: trie de nombres y máscara de 64 bits de requeridos
  (registros acotados, categoría b). Su valor se valida con el tipo
  simple.
- **Texto**: el de un elemento simple se valida en streaming con las
  mismas comprobaciones de escalares que JSON. En los tipos numéricos se
  usa la gramática léxica de XSD, que admite `+`, ceros iniciales y
  `.5`, a diferencia de JSON.

### 3.1 `xs:date` es un lenguaje regular (con años bisiestos)

La validez de una fecha gregoriana **incluidos los años bisiestos** es
regular:

- "Divisible por 4" depende solo de los dos últimos dígitos del año
  (`04, 08, …, 96`, sin `00`).
- "Divisible por 400" depende de los dos primeros cuando los dos últimos
  son `00` (`0400`, `0800`, …, `2000`).

Por eso `xs:date` se compila a una regex (`DATE_RE` en `xsd.py`) y a un
AFD exacto de **43 estados** (85 antes de minimizar):
`docs/diagramas/lexico/xs_date.svg`. Acepta `2024-02-29` y `2000-02-29`, y
rechaza `2023-02-29`, `1900-02-29`, `2023-04-31` y `2023-13-01`.

## 4. Seguridad: XXE y expansión de entidades

- `<!DOCTYPE` se **rechaza en el lexer**. Sin DTD no hay entidades
  externas (XXE) ni entidades internas recursivas (*billion laughs*).
- Solo se aceptan las 5 entidades predefinidas (`&lt; &gt; &amp; &quot;
  &apos;`) y las referencias de carácter (`&#…;`, `&#x…;`, rechazando
  `&#0;`, los sustitutos y los valores > U+10FFFF). Cualquier `&nombre;`
  es MALFORMED.
- Ningún texto se expande: el validador recorre los bytes una vez.

## 5. Verificación

- `tests/differential_xml.py`: 1200 documentos generados y mutados
  frente a `lxml.etree.XMLSchema` (DOM), **0 diferencias**. Una
  diferencia inicial venía de `xs:decimal` con más de 24 dígitos, que
  libxml2 no acepta por un límite de implementación (XSD no lo prohíbe).
- `tests/crosscheck_xml.py`: motor Python frente a C generado, incluidos
  bloques de 1 byte y casos mal formados, **0 diferencias**.
