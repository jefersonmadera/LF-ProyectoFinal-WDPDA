# Fase 7 — Aplicación web, NoSQL y despliegue

Código: `web/backend/` (FastAPI), `web/frontend/` (HTML, CSS y JS sin
framework; viz.js para dibujar DOT y Chart.js para las gráficas, desde
CDN). Contenedor: `Dockerfile`, `docker-compose.yml`.

## 1. Ejecutar en local

```powershell
pip install -r requirements.txt
python -m web.backend.seed
python -m uvicorn web.backend.app:app --port 8000
```

Luego abrir <http://localhost:8000>. Sin `MONGODB_URI`, los casos se
guardan en `web/data/casos.json` (almacén documental en archivo, con la
misma interfaz). Con MongoDB local vía Docker:

```powershell
docker compose up --build
```

```powershell
docker compose exec web python -m web.backend.seed
```

## 2. Pantallas

| Pestaña | Qué muestra | Fase |
| --- | --- | --- |
| 1. Esquema | editor, ejemplos (10 JSON Schema y 2 XSD), compilación: estados y transiciones de δ, Γ, nodos antes/después de minimizar, AFD, determinismo, **clasificación (a)/(b)/(c)** de cada palabra clave, código C generado y **descarga del proyecto C/Rust (.zip)** | 1–5 |
| 2. Autómata | el **WDPDA dibujado** (Graphviz en el navegador): vista de peso 0 o con pesos en rojo; cada AFD auxiliar (trie de claves, enum, pattern, contenido XSD); δ completo como tabla | 4 |
| 3. Validar | payload (o generado válido/inválido), motor Python (con **traza paso a paso**: token, estado, pila de carriles y peso) o **C nativo en sandbox** (MB/s, ciclos/byte, asignaciones, memoria); errores con JSON Pointer/XPath y byte; **árbol de derivación** LL(1) | 2, 5, 6 |
| 4. Tiempo real | el payload se envía por **WebSocket** en bloques; tras cada bloque se ven los bytes consumidos, la profundidad de la pila, el peso tropical y los errores que van apareciendo | 6 |
| 5. Comparación | **en vivo por WebSocket**: WDPDA (C) frente a DOM (C) frente a `jsonschema`, en MB/s y memoria, con gráficas | 6 |
| 6. Casos (NoSQL) | los 54 casos del corpus por categoría; **ejecutar todos** guarda las métricas de cada motor en la base | 4 (casos) |

## 3. API

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `/api/health` | estado, almacén, gcc/cargo disponibles |
| GET | `/api/examples` | esquemas de ejemplo |
| POST | `/api/compile` | `{schema_text, format: json\|xsd}` → id, stats, δ, DOT, AFD, clasificación, código C |
| GET | `/api/download/{id}.zip` | proyecto C (+ Rust) + IR |
| POST | `/api/validate` | `{id, payload, engine: py\|c, chunk, trace, fail_fast}` |
| POST | `/api/generate` | payload válido o mutado (Fase 5) |
| POST | `/api/tree` | árbol de derivación (DOT y texto) |
| GET/POST | `/api/cases` | listar / crear casos (NoSQL) |
| POST | `/api/cases/{id}/run`, `/api/cases/run-all` | ejecutar y guardar métricas |
| WS | `/ws/stream` | `{id}` y luego `{chunk}`… `{end:true}` → estado tras cada bloque |
| WS | `/ws/bench` | `{id, sizes}` → métricas a medida que se miden |

Documentación interactiva automática: <http://localhost:8000/docs> (OpenAPI).

## 4. Sandbox

El servidor **compila y ejecuta** el validador C generado a partir de un
esquema enviado por el usuario. Medidas:

1. **Sin inyección de código.** El generador emite solo tablas de datos
   y el runtime es fijo. Las cadenas del esquema se escapan en octal.
2. **Límites**: esquema ≤ 256 KB, payload ≤ 8 MB, `gcc` con tiempo
   máximo, validador con `--timeout-ms 5000` y `--max-bytes`, árbol de
   derivación ≤ 4 KB.
3. **Sin shell**: `subprocess` con lista de argumentos.
4. **Contenedor** (`docker-compose.yml`): usuario sin privilegios
   (uid 10001), **sistema de archivos de solo lectura** salvo `/tmp`
   (tmpfs, donde se compila), `no-new-privileges`, `cap_drop: ALL`,
   límites de 2 CPU y 1 GB.
5. Caché por hash del IR: cada esquema se compila una sola vez.

## 5. Persistencia NoSQL

Colección `casos` (MongoDB): un documento por caso con `categoria`,
`formato`, `esquema`, `payload` (o `payload_b64` si no es UTF-8),
`esperado` (estado, peso y errores), `oraculo` (`jsonschema`/`lxml`),
`nota` (discrepancias documentadas) y `metricas` (último resultado de
Python y de C: estado, MB/s, ciclos/byte, profundidad, memoria). Hay un
índice por `categoria`.

El driver es `pymongo`, así que la misma aplicación funciona con:

- **MongoDB Atlas** (cualquier nube; tiene capa gratuita M0),
- **Azure Cosmos DB for MongoDB** (vCore o RU),
- un `mongo` local (`docker-compose`).

## 6. Despliegue en la nube

> El despliegue usa las cuentas del equipo. Los comandos están listos,
> pero **no se ejecutaron** desde este repositorio: requieren iniciar
> sesión en Azure o AWS y crear recursos que pueden tener costo.

### Opción A: Azure Container Apps + Cosmos DB for MongoDB

```bash
az login
az group create -n rg-wdpda -l eastus
# Base NoSQL (Cosmos DB, API de MongoDB, capa gratuita si está disponible)
az cosmosdb create -g rg-wdpda -n wdpda-db --kind MongoDB --enable-free-tier true
URI=$(az cosmosdb keys list -g rg-wdpda -n wdpda-db --type connection-strings \
      --query "connectionStrings[0].connectionString" -o tsv)
# Imagen y aplicación
az acr create -g rg-wdpda -n wdpdaacr --sku Basic --admin-enabled true
az acr build -r wdpdaacr -t wdpda-web:1.0 .
az containerapp up -g rg-wdpda -n wdpda-web --image wdpdaacr.azurecr.io/wdpda-web:1.0 \
   --ingress external --target-port 8000 \
   --env-vars MONGODB_URI="$URI" WDPDA_DB=wdpda
# Sembrar los casos una vez (desde la máquina local con la misma URI)
MONGODB_URI="$URI" python -m web.backend.seed
```

### Opción B (implementada): AWS con capa gratuita, S3 + EC2 + DynamoDB

Frontend en S3, backend en EC2 `t3.micro` con Docker y casos en
**DynamoDB** (`WDPDA_STORE=dynamodb`), todo con CloudFormation y un
script. Guía completa, seguridad y estructura de los ítems:
**[despliegue-aws.md](despliegue-aws.md)**.

```powershell
.\deploy\aws\deploy.ps1 -Region us-east-1 -Branch feat/proyecto-completo
```
