# Guía de sustentación

Sirve para preparar la presentación: guion de la demo en vivo, reparto
de temas, cómo explicar cada fase y un banco de preguntas probables con
respuestas. **Regla del curso: las cifras que se digan deben coincidir
con la demo.** Todas las de esta guía salen de `bench/RESULTADOS.md` y
de las pruebas.

## 0. Preparación (el día anterior)

```powershell
pip install -r requirements.txt
python -m pytest tests -q
python -m web.backend.seed
python bench\bench.py --rust
```

`pytest` debe dar 104 pruebas en verde y el benchmark regenera
`bench/RESULTADOS.md`. Abrir JFLAP con
`docs/diagramas/lexico/json_dpda.jff` y
`docs/diagramas/ejemplo/example_schema_wdpda.jff`.

El día de la demo:

```powershell
python -m uvicorn web.backend.app:app --port 8000
```

Se abre en <http://localhost:8000>.

## 1. Reparto sugerido (25–30 min)

| Bloque | Tiempo | Quién | Contenido |
| --- | --- | --- | --- |
| Problema y tesis | 2 min | A | DOM + validación interpretada: memoria, malloc, dos recorridos. Tesis: compilar el esquema a un autómata de pila |
| Fase 1 | 3 min | B | alfabeto UTF-8, AFD de número y de cadena (JFLAP), streaming por bloques |
| Fase 2 | 4 min | A | gramática LL(1) de JSON, FIRST/FOLLOW, tabla, árbol de derivación; metalenguaje; el conflicto `$` de las regex |
| Fase 3 | 3 min | B | `$ref` con tabla de símbolos; ciclos no productivos ≈ recursión por la izquierda (Tarjan); clasificación a/b/c |
| Fase 4 | 6 min | A | definición formal, semianillo tropical, construcción por nodo, δ en JFLAP, determinismo, VPA y producto, minimización |
| Fases 5–6 | 4 min | B | tablas `const`, C-ABI, streaming, JSON Pointer, límites, métricas y benchmark |
| Fase 7 + demo | 6 min | A + B | web en vivo (guion de la sección 2) |
| Cierre | 2 min | B | resultados, hallazgos, límites honestos, trabajo futuro |

(A = Jeferson, B = Sergio, o como lo acuerden. Cada uno debe poder
responder preguntas de **todas** las fases; ver la sección 4).

## 2. Guion de la demo en vivo (6 min)

1. **Pestaña Esquema.** Cargar *persona* y compilar. Señalar estados,
   transiciones, Γ, "δ determinista ✓", nodos antes y después de
   minimizar y la tabla de clasificación: `pattern` en (c), `required` en
   (b) y `type` en (a). Descargar el .zip y mostrar `persona_schema.c`
   (solo tablas `const`).
2. **Pestaña Autómata.** Mostrar el WDPDA y cambiar a la vista con pesos:
   las transiciones rojas son las violaciones (w = 1). En la lista de
   AFD, elegir el `pattern` del correo y mostrar que es un AFD mínimo.
3. **Pestaña Validar.** Usar este payload:

   ```json
   {"nombre":"","edad":3.5,"correo":"malo"}
   ```

   Resultado: INVÁLIDO, **peso 3**, con tres errores con JSON Pointer
   (`/nombre`, `/edad`, `/correo`) y su byte. Bajar a la **traza**: pila
   y peso token a token. Cambiar el motor a **C nativo**: mismo
   resultado, más MB/s, ciclos/byte, **0 asignaciones** y memoria fija.
   Pulsar *Árbol de derivación*.
4. **Mal formado.** Borrar la última `}`: MALFORMED, peso **+∞**, con el
   byte del error.
5. **Pestaña Tiempo real.** Transmitir en bloques de 4 bytes: el
   validador avanza sin ver nunca el documento completo y la violación
   aparece en el bloque en que llega.
6. **Pestaña Comparación.** Medir 64, 512 y 2048 KB: el WDPDA en C supera
   claramente a DOM (C) y a `jsonschema`, con memoria constante.
7. **Pestaña Casos.** *Ejecutar todos*: 54/54. Abrir un caso con ⓘ
   (por ejemplo `extremo-numero-enorme-falla`) para contar el hallazgo
   del `float`.
8. **XSD.** Elegir *pedido (XSD)*, compilar y mostrar los modelos de
   contenido; *Generar inválido* y validar (rutas tipo XPath).

**Plan B sin red** (viz.js y Chart.js vienen de CDN): los grafos se
muestran como texto DOT y las tablas siguen funcionando. Tener abiertos
los SVG de `docs/diagramas/` y los `.jff` en JFLAP.

## 3. Cómo explicar cada fase (frases clave)

- **¿Por qué pila?** "JSON no es regular: con el lema de bombeo, `[^p ]^p`
  bombeado desbalancea los corchetes. Hace falta memoria no acotada, una
  pila. Y es **determinista**: el token y el tope deciden."
- **¿Por qué ponderado?** "El peso cuenta violaciones en el semianillo
  tropical: 0 es válido, k son k violaciones y +∞ es mal formado, porque
  no hay ninguna ejecución. El `min` del semianillo es exactamente
  `anyOf`."
- **¿Qué es un estado?** "`V(n)` significa 'espero un valor del nodo n';
  `R` es el retorno, que mira el tope de pila con una transición ε para
  saber si vuelve a un objeto o a un arreglo."
- **¿Cómo cabe `required` en un DPDA?** "Es un registro acotado: un bit
  por clave dentro del símbolo de pila. Γ sigue siendo finito; si lo
  expandiéramos en estados serían 2^k."
- **¿Y `anyOf`, si los CFL no son cerrados bajo intersección?** "Lo que
  nos salva es que son autómatas de pila **visibles**: todos apilan en
  `{` y desapilan en `}` a la vez. Esa clase sí es cerrada bajo
  intersección, así que el producto usa una sola pila de tuplas. Lo
  implementamos con carriles."
- **¿Por qué no hay ReDoS?** "`pattern` se compila a un AFD: validar es
  lineal y sin retroceso. El riesgo pasa a la compilación y lo acotamos."

## 4. Banco de preguntas y respuestas

**P1. ¿Qué diferencia hay entre su autómata y un DPDA "de libro"?**
Tres agregados formales: (i) **pesos** en el semianillo tropical; (ii)
**guardas** sobre el token, que refinan el alfabeto (STRING ⊨ checks /
STRING ⊭ checks); (iii) **registros acotados** dentro de los símbolos de
pila. Los tres se pueden "compilar" a un DPDA clásico: el alfabeto
refinado es finito y Γ × registros es finito. Los mantenemos compactos
para no explotar el tamaño.

**P2. ¿Dónde está el determinismo y cómo lo comprueban?**
`formal.py::check_determinism` recorre δ y verifica: una transición por
(q, a, X, guarda); no hay ε en conflicto con lecturas desde el mismo
(q, X); no hay λ en conflicto con un tope concreto. Se aplica a los 10
esquemas en `test_delta_is_deterministic`.

**P3. ¿Por qué el estado R usa ε? ¿Eso no rompe el determinismo?**
No. Un DPDA puede tener transiciones ε si desde ese (estado, tope) no
hay ninguna que lea entrada. `R` solo tiene transiciones ε, una por cada
símbolo de tope (O_n, A_n, Z0), así que siempre hay a lo sumo una.

**P4. ¿Qué pasa con un esquema recursivo? ¿Infinitos estados?**
No: `$ref` reutiliza el mismo `V(n)`. La recursión del documento la lleva
la pila. `arbol.json` tiene 3 nodos y 22 estados y valida árboles de
cualquier profundidad (hasta el límite configurado).

**P5. ¿Qué es un ciclo no productivo y por qué es un error?**
Un ciclo de `$ref`/`allOf` que no consume entrada: expandir `a` pide `b`,
que pide `a`… Es la recursión por la izquierda de las gramáticas. Lo
detectamos con Tarjan sobre las aristas no consumidoras.

**P6. Demuestren que `uniqueItems` no se puede hacer con su modelo.**
Conjunto engañador: dentro de un arreglo de nivel 1, la pila de un VPA
tiene altura 1, así que hay finitas configuraciones. Pero los prefijos
`[S` para conjuntos distintos S y T deben distinguirse (agregando `,y]`
con `y ∈ S∖T`). Hay infinitos conjuntos: contradicción. Por eso usamos un
registro acotado: hash de 64 bits en una tabla de 4096 elementos, con
`LIMIT` si se llena.

**P7. ¿Por qué la gramática regex daba conflicto LL(1)?**
El ancla `$` y el marcador de fin de entrada `$` eran el mismo terminal:
`M[Concat, $]` tenía dos producciones. Lo resolvimos lexeando las anclas
como `CARET`/`DOLLAR`. La verificación automática lo detectó.

**P8. ¿Cómo validan un número sin `double`?**
Lo normalizamos en streaming a (signo, dígitos, exponente) y comparamos
por magnitud (`len(dígitos) + exp`) y luego dígito a dígito. `multipleOf`
usa aritmética modular sobre los dígitos. Es exacto con `1e400` y con
enteros de 31 dígitos. `jsonschema` se equivoca en ambos casos (hallazgo
documentado).

**P9. ¿Qué tan rápido es y es justa la comparación?**
76 MB/s y 34 ciclos/byte en C sobre 8 MB, con 0 asignaciones. La
comparación justa es contra el **DOM en C con las mismas tablas y
reglas**: 23 MB/s, 2,2 millones de `malloc` y 78 MB. Contra `jsonschema`
(1,1 MB/s) la diferencia mezcla arquitectura e intérprete, y lo decimos
así. 34 c/b no es el límite: el lexer solo cuesta ~22 c/b y hay margen
con SIMD.

**P10. ¿Por qué el peso de `anyOf` es el mínimo de las ramas?**
Porque ⊕ = min en el semianillo tropical: el peso de una entrada es el
mínimo sobre las ejecuciones. Cada rama es una ejecución. Válido ⇔ alguna
rama pesa 0.

**P11. ¿Qué es UPA y qué tiene que ver con Glushkov?**
*Unique Particle Attribution*: en XSD, cada hijo debe poder atribuirse a
una sola partícula del modelo. Es exactamente que el autómata de
posiciones de Glushkov sea determinista. Si no lo es, rechazamos el
esquema.

**P12. ¿Por qué `x{0,3}` se despliega anidado?**
`x? x? x?` no es determinista en Glushkov (tras una `x` hay tres
posiciones candidatas); `(x(x(x)?)?)?` sí lo es, y genera el mismo
lenguaje. Lo descubrimos con `catalogo.xsd`.

**P13. ¿Cómo mitigan XXE y billion laughs?**
Se rechaza `<!DOCTYPE` en el lexer. Sin DTD no hay entidades definibles;
solo las 5 predefinidas y las referencias de carácter. Nada se expande.

**P14. ¿Cómo saben que C, Rust y Python hacen lo mismo?**
Prueba cruzada: 2280 payloads (generados, mutados y 28 fijos patológicos)
dan el mismo estado, el mismo peso y la misma lista de (código, ruta),
también con bloques de 1 byte. Además, 6000 payloads contra `jsonschema`
y 1200 documentos contra `lxml`.

**P15. ¿Qué significa "sin AST"? ¿No construyen un árbol de derivación?**
El árbol de derivación existe **solo en compilación** (para el esquema)
y en la web como material didáctico. El validador generado nunca
construye ningún árbol del payload: es una sola pasada con una pila de
marcos de tamaño fijo.

**P16. ¿Qué pasa si el payload llega partido a la mitad de un número o
de un carácter UTF-8?**
El estado del AFD léxico persiste entre llamadas a `feed`: la secuencia
UTF-8 o el número continúan en el siguiente bloque. Está probado con
bloques de 1 byte.

**P17. ¿Cuál es la complejidad y de qué depende la constante?**
`O(n · B)` en tiempo y `O(d · B)` en memoria, con `B` = carriles por nivel
(1 sin combinadores, acotado por 64). Más precisamente, por byte de
cadena se avanzan los AFD activos de ese valor.

**P18. ¿Qué minimizan?**
Tres cosas: (1) los nodos del esquema, con refinamiento de particiones de
Moore; funciona con ciclos y fusiona subesquemas equivalentes. (2) Cada
AFD, con Moore (`^[^@]+@…` pasa de 85 a 25 estados y `xs:date` de 85 a
43). (3) El alfabeto, con clases de bytes.

**P19. ¿Qué limitaciones tienen?**
No soportan `propertyNames`, `unevaluated*` ni `$dynamicRef`; no hay
`$ref` externos; las claves duplicadas no distinguidas no se detectan;
`uniqueItems` es probabilístico (64 bits); XML solo en C (no en Rust);
`xs:all` y `xs:any` no están. Todo está documentado.

**P20. ¿Cómo está desplegado?**
Contenedor (Dockerfile) con usuario sin privilegios y sistema de archivos
de solo lectura; Azure Container Apps más Cosmos DB for MongoDB (o
MongoDB Atlas). Los comandos están en `fase7-web.md` §6.
