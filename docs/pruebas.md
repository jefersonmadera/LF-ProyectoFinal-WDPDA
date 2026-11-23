# Estrategia de pruebas y resultados

```powershell
python -m pytest tests -q
```

```powershell
python tests\differential.py 300
```

```powershell
python tests\differential_xml.py 300
```

```powershell
python tests\crosscheck.py 100 --rust
```

```powershell
python tests\crosscheck_xml.py 150
```

> En Git Bash, poner antes `export PATH=/c/msys64/ucrt64/bin:$PATH`. Si
> no, `/mingw64/bin` carga DLL incompatibles con el `cc1` de MSYS2 y
> `gcc` falla sin mensaje. En PowerShell no hace falta.

## 1. Niveles de verificación

| Nivel | Qué asegura | Dónde | Resultado |
| --- | --- | --- | --- |
| Unitarias por fase | gramáticas LL(1), FIRST/FOLLOW, árbol de derivación, regex frente a `re` (14 patrones × 1500 cadenas), minimización, decimales frente a `Decimal` (4000 pares), errores semánticos, determinismo de δ | `tests/test_fases.py` | ✓ |
| **Diagramas ejecutables** | el `.jff` del DPDA de JSON y el del AFD de números **se ejecutan** con un simulador de JFLAP y aceptan y rechazan lo correcto | `test_jflap_*` | ✓ |
| Validación | válidos, inválidos (con JSON Pointer y peso exacto), mal formados (peso +∞), combinadores, `uniqueItems`, bombeo lineal, números enormes, límites, XML, UPA | `tests/test_validacion.py` | ✓ |
| **Diferencial contra oráculo** (JSON) | 10 esquemas × 300 semillas × (válido + mutado) = **6000 payloads**, motor WDPDA frente a `jsonschema` (DOM), y entrada completa frente a bloques de 7 bytes | `tests/differential.py` | **0 diferencias** |
| **Diferencial contra oráculo** (XML) | 2 XSD × 300 × 2 = **1200 documentos**, frente a `lxml.etree.XMLSchema`, y bloques de 5 bytes | `tests/differential_xml.py` | **0 diferencias** |
| **Cruzada entre implementaciones** | Python, C y Rust generados: estado, **peso** y **lista exacta de errores (código y ruta)**, con entrada completa y bloques de **1 byte**; incluye 28 casos fijos mal formados o extremos | `tests/crosscheck.py` | **2280 payloads, 0 diferencias** |
| Cruzada XML | Python frente a C, bloques de 1 byte, 17 casos fijos (DOCTYPE, entidades, profundidad 300, UTF-8…) | `tests/crosscheck_xml.py` | **634 documentos, 0 diferencias** |
| Sin asignación dinámica | el runtime C no contiene `malloc`/`free` | `test_c_runtime_has_no_malloc` | ✓ |
| Corpus NoSQL | 54 casos en 6 categorías, ejecutados desde la web con Python y C | pestaña *Casos* | **54/54** |

Total de `pytest`: **104 pruebas y 20 subpruebas, todas en verde**
(~50 s, incluye compilar C y Rust).

## 2. El corpus de casos (sección 4 del enunciado)

`python -m web.backend.seed` lo genera y lo guarda en la base NoSQL:

| Categoría | Casos | Ejemplos |
| --- | ---: | --- |
| validos | 18 | 7 esquemas JSON (simple, anidado, recursivo `$ref`), 2 XSD (incluido el recursivo `catalogo.xsd`) |
| invalidos | 7 | tipo, requerido faltante, propiedad adicional, enum, rango, tres violaciones a la vez (peso 3), XSD con 7 violaciones |
| malformados | 9 | coma final, sin cerrar, UTF-8 inválido y sobrelargo, clave duplicada, literal y cero inicial, XML sin cerrar y cruzado |
| fuera_dpda | 8 | `anyOf` (peso = mínimo tropical), `oneOf`, `not`, `uniqueItems` (hash canónico, orden de claves y `1 == 1.0`), `pattern` → AFD |
| bombeo | 6 | `u·vᵏ·x·yᵏ·z` con k = 10, 100, 1000 (objetos) y 10, 200 (arreglos); arreglo de 20 000 elementos |
| extremos | 6 | profundidad 300 sobre el límite de 256, números de 31 dígitos y `1e400`, *billion laughs* XML, payload de 1 MB |

## 3. Discrepancias con los oráculos (documentadas)

Las pruebas diferenciales también sirven para auditar a los oráculos.
Encontramos cuatro diferencias, y en ninguna el WDPDA está equivocado:

1. **`multipleOf` con decimales** (`jsonschema`). `1.11` con
   `multipleOf: 0.01` se reporta inválido porque Python calcula
   `1.11 / 0.01 = 110.99999999999999` con `float`. El WDPDA usa
   aritmética decimal exacta. Apareció en el benchmark.
2. **`maximum: 1e30`** (`jsonschema`). Convierte `1e30` a `float`
   (= 1000000000000000019884624838656) y acepta `10³⁰ + 1`. El WDPDA lo
   rechaza correctamente.
3. **Claves duplicadas** (`json.loads`). Las acepta y se queda con la
   última. El enunciado las clasifica como mal formadas y el WDPDA las
   detecta para las claves distinguidas (máscara de bits).
4. **`xs:decimal` de más de 24 dígitos** (libxml2/`lxml`). Lo rechaza
   por un límite de implementación; XSD no lo prohíbe y el WDPDA lo
   compara exactamente.

## 4. Lo que las pruebas NO cubren (límites honestos)

- **Claves duplicadas no distinguidas.** Una clave que no aparece en
  `properties`, `required` ni en las dependencias se repite sin
  detectarse: hacerlo exige recordar un conjunto no acotado de claves.
- **Colisiones de hash en `uniqueItems`.** Con 64 bits la probabilidad
  es ≈ `n²/2⁶⁵`, no nula.
- **`multipleOf` sobre números de más de 64 dígitos significativos.** Se
  reporta como límite, no se decide.
- **Fechas negativas y años de más de 4 cifras** en `xs:date`: no se
  probaron contra `lxml`.
