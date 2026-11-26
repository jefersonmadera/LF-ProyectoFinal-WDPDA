# Proyecto Final — Lenguajes Formales y Autómatas 2026-2 (UPTC Sogamoso)

## Validación en tiempo real de esquemas JSON/XML mediante autómatas de pila ponderados

Compilador que traduce un esquema **JSON Schema 2020-12** (subconjunto) o
**XSD** (subconjunto) a un **autómata de pila determinista ponderado
(WDPDA)** y emite un validador nativo en **C** y **Rust**. El validador
consume los bytes del payload en una sola pasada, en streaming, sin
construir un AST y sin reservar memoria dinámica.

**Integrantes:** Jeferson Santiago Madera Gaona y Sergio Alexander Gómez Zapata
**Curso:** Lenguajes Formales y Autómatas 2026-2 — Prof. Marco Suárez (UPTC)

### Estado

Las siete fases están implementadas y probadas:

| Fase | Qué hay | Dónde |
| --- | --- | --- |
| 1. Léxico | AFD UTF-8, JSON y XML en streaming; trie de palabras clave del esquema | `compiler/utf8.py`, `json_lexer.py`, `xml_lexer.py` |
| 2. Sintaxis | 4 gramáticas LL(1) (JSON, XML, metalenguaje, regex); FIRST/FOLLOW, tabla y árbol de derivación | `compiler/grammar.py`, `grammars.py` |
| 3. Semántica | tabla de símbolos, `$ref`/`$defs`, ciclos no productivos (Tarjan), meta-esquema, clasificación (a)/(b)/(c) | `compiler/schema.py` |
| 4. WDPDA | definición formal, pesos tropicales, δ explícito, determinismo verificado, minimización (Moore), regex → AFD, producto de VPA | `compiler/wdpda.py`, `formal.py`, `automata.py` |
| 5. Codegen | C y Rust (tablas `const`), API C-ABI, generador de payloads JSON/XML | `compiler/codegen_c.py`, `codegen_rust.py`, `payload_gen.py` |
| 6. Runtime | streaming, JSON Pointer y byte, límites (profundidad, tamaño, tiempo), MB/s, ciclos/byte, 0 asignaciones | `runtime/c/`, `runtime/rust/`, `bench/` |
| 7. Web | FastAPI (REST + WebSocket), SPA, sandbox, MongoDB, Docker, guía de despliegue en Azure/AWS | `web/`, `Dockerfile`, `docker-compose.yml` |
| XSD | Glushkov + UPA, `xs:date` con años bisiestos, mitigación XXE | `compiler/xsd.py`, `xml_engine.py`, `runtime/c/wdpda_xml.c` |

**Resultados clave:**

- 104 pruebas automáticas en verde.
- 6000 payloads JSON contra `jsonschema` y 1200 documentos XML contra
  `lxml`: **0 diferencias**.
- Python, C y Rust dan resultados idénticos en 2280 payloads: estado,
  peso y errores con su ruta, incluso en bloques de 1 byte.
- Validador C: **76 MB/s, 34 ciclos/byte, 0 asignaciones y 1,1 MB
  fijos**, frente a 23 MB/s y 78 MB de una línea base DOM en C con las
  mismas reglas.

Toda la documentación, incluido el **informe final**, la **guía de
sustentación** y los **diagramas en JFLAP**, está en
[`docs/`](docs/README.md).

Pendiente de los integrantes:

- aprobar por escrito el IR y la API propuestos (`docs/decisiones.md`);
- desplegar con su cuenta de AWS: la infraestructura (S3 + EC2 + DynamoDB,
  capa gratuita) está lista en `deploy/aws/`, con la guía en
  `docs/despliegue-aws.md`.

## Estructura

- `compiler/` — Fases 1–5: lexers, gramáticas y parser LL(1), análisis
  semántico, construcción del WDPDA e IR, δ formal, generadores de
  código y de payloads, exportación de diagramas, CLI (`python -m
  compiler`).
- `runtime/c/` — runtime C del validador (JSON `wdpda_rt.*`, XML
  `wdpda_xml.*`), CLI y línea base DOM del benchmark. `wdpda.c` y
  `main.c` son el esqueleto histórico de la Semana 1.
- `runtime/rust/` — runtime Rust (`src/wdpda_rt.rs`) y el esqueleto de
  la Semana 1 (`src/lib.rs`).
- `web/` — backend (API REST + WebSocket, sandbox, NoSQL) y frontend.
- `tests/` — pruebas unitarias, diferenciales y cruzadas; esquemas de
  prueba JSON (`schemas/`) y XSD (`schemas_xsd/`).
- `bench/` — benchmark reproducible y resultados (`RESULTADOS.md`).
- `docs/` — documentación técnica, informe, sustentación y diagramas.
- `avance_mvp/` — prototipo del avance (Semana 2), conservado como historia.

## Cómo correrlo

Requisitos: Python 3.12 o superior, gcc (en Windows, MSYS2 UCRT64), Rust
(opcional) y Docker (opcional).

```powershell
pip install -r requirements.txt
```

Todas las pruebas:

```powershell
python -m pytest tests -q
```

Compilar un esquema a C y Rust (el IR y los binarios quedan en `build/`):

```powershell
python -m compiler compile tests\schemas\persona.json --lang c,rust
```

Validar un payload con el motor de referencia (traza paso a paso) o con
el C generado:

```powershell
python -m compiler validate tests\schemas\persona.json payload.json --trace
```

```powershell
python -m compiler validate tests\schemas\persona.json payload.json --engine c
```

Diagramas (JFLAP, SVG, DOT, Mermaid) de un esquema y del léxico:

```powershell
python -m compiler diagrams tests\schemas\persona.json
```

```powershell
python -m compiler lexer-diagrams
```

Gramáticas con FIRST/FOLLOW y tabla LL(1):

```powershell
python -m compiler grammar json
```

Benchmark (WDPDA C y Rust frente a DOM en C y `jsonschema`, más bombeo):

```powershell
python bench\bench.py --rust
```

Aplicación web, con los casos de prueba cargados en el almacén NoSQL:

```powershell
python -m web.backend.seed
```

```powershell
python -m uvicorn web.backend.app:app --port 8000
```

Luego abrir <http://localhost:8000>. Con MongoDB en contenedores:

```powershell
docker compose up --build
```

En AWS (todo por HTTPS con CloudFront; frontend en S3, backend en EC2, casos en DynamoDB), con la AWS CLI
configurada; ver `docs/despliegue-aws.md`:

```powershell
.\deploy\aws\deploy.ps1 -Region us-east-1 -Branch feat/proyecto-completo
```

> **Git Bash en Windows:** antes de usar `gcc` hay que ejecutar
> `export PATH=/c/msys64/ucrt64/bin:$PATH`. Si no, `/mingw64/bin` carga
> DLL incompatibles con el `cc1` de MSYS2 y `gcc` falla sin mensaje. En
> PowerShell no hace falta, y el compilador lo ajusta solo cuando invoca
> `gcc`.

## Reglas de trabajo

- Ningún cambio entra a la rama principal sin revisión del otro
  integrante (pull request).
- El historial de git debe mostrar aportes de ambos durante todo el
  proyecto, no un solo commit al final.
- Cada viernes, 30 minutos: cada uno le explica al otro lo que hizo esa
  semana, sin mirar el código.
- Las cifras del informe deben coincidir con la demo.

## Avance MVP (Semana 2, histórico)

```powershell
gcc -std=c11 -Wall -Wextra -Werror -O2 -o avance_mvp/validator avance_mvp/validator.c avance_mvp/schema.c
```

```powershell
bash avance_mvp/tests/run_tests.sh
```

Las pruebas del MVP deben dar 15/15. Detalle en `docs/fase2-parser.md`.
