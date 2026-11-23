# Diagramas de autómatas

Todos se **generan desde el código** (lo que se dibuja es lo que se
ejecuta):

```powershell
python -m compiler lexer-diagrams
```

```powershell
python -m compiler diagrams tests\schemas\persona.json -o docs\diagramas\persona
```

| Formato | Para qué |
| --- | --- |
| `.jff` | **JFLAP**: abrir con *File → Open* y probar con *Input → Step* o *Multiple Run* |
| `.svg` | verlos en el navegador o en GitHub |
| `.dot` | Graphviz (la web los dibuja con viz.js) |
| `.mmd` | Mermaid (GitHub los dibuja en un `.md`) |
| `*_delta.txt` | δ completo del WDPDA, una transición por línea con guarda, registros y peso |

## Índice

### Fase 1 — léxico (`lexico/`)

| Archivo | Autómata | Probar en JFLAP |
| --- | --- | --- |
| `numero_json` | AFD de número JSON (RFC 8259) | acepta `0`, `-12`, `1.5e-3`; rechaza `01`, `1.`, `.5`, `1e` |
| `cadena_json` | AFD de cadena con escapes y `\uXXXX` (alfabeto reducido) | `"ab\n"`, `"é"` |
| `literales_json` | `true`, `false`, `null` | |
| `utf8` | AFD de validación UTF-8 sobre clases de bytes (ver la leyenda en la nota del `.jff`) | `A`, `Bx`, `Cxx`, `Dzx`; rechaza `Dx` (sobrelarga) y `Ez` (sustituto) |
| `json_dpda` | **DPDA sintáctico de JSON** (autómata de pila) | `{k:[i,s,{}],k:u}` ✓, `{k:}` ✗, `[i,]` ✗ |
| `regex_abb` | AFD mínimo de `^(a\|b)*abb$` (4 estados, el clásico del libro de Aho) | `aababb` ✓ |
| `xs_date` | AFD exacto de `xs:date` con años bisiestos (43 estados) | `2024-02-29` ✓, `2023-02-29` ✗ |

**Símbolos de entrada de los PDA** (un carácter por clase de token): `s`
= cadena, `i` = número entero, `d` = número con fracción, `t`/`f` =
true/false, `u` = null, `k` = clave (cualquiera), `{ } [ ] : ,` = ellos
mismos. Las claves declaradas de un esquema usan letras mayúsculas: ver
`LEYENDA_JFLAP.txt` en cada carpeta. Pila: `Z` = Z0, `O` = objeto
genérico, `A` = arreglo genérico, y una letra minúscula por cada símbolo
de pila de un nodo del esquema.

### Fase 4 — WDPDA de esquemas

| Carpeta | Esquema | Qué ilustra |
| --- | --- | --- |
| `ejemplo/` | el del MVP (`avance_mvp/example_schema.json`) | δ completo pequeño: 21 estados, 92 transiciones |
| `persona/` | `tests/schemas/persona.json` | `additionalProperties: false`, `pattern`, `enum` |
| `arbol/` | `tests/schemas/arbol.json` | **recursión con `$ref`**: la pila lleva la recursión |
| `combinadores/` | `tests/schemas/combinadores.json` | `anyOf`/`oneOf`/`not`: nodos de **producto** |
| `pedido_xsd/` | `tests/schemas_xsd/pedido.xsd` | AFD de contenido de Glushkov, tries de nombres y atributos |

En cada carpeta:

- `*_wdpda.svg`: vista legible con ⊤ colapsado y solo las transiciones de
  peso 0.
- `*_wdpda_pesos.svg`: la misma vista con las transiciones de violación
  en rojo (w = 1).
- `*_wdpda.jff`: el autómata **completo y ejecutable** en JFLAP. JFLAP no
  tiene pesos ni registros: se exportan solo las transiciones de peso 0 y
  las guardas no se evalúan, así que acepta los payloads cuyo **tipo y
  estructura** cumplen el esquema.
- `*_dfaN_*.jff/svg`: los AFD auxiliares (trie de claves, enum, pattern).
