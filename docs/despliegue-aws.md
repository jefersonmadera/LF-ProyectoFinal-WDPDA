# Despliegue en AWS (capa gratuita) y casos de prueba NoSQL

Infraestructura como código: `deploy/aws/wdpda-free-tier.yaml` (CloudFormation),
`deploy/aws/deploy.ps1` (crear o actualizar) y `deploy/aws/destroy.ps1` (borrar todo).

## 1. Arquitectura

```
navegador ──HTTPS / WSS──► CloudFront (certificado *.cloudfront.net)
                             │  /            ──HTTP──► S3 (sitio web estático)
                             │                         index.html, static/app.js, styles.css, config.js
                             │  /api/*, /ws/*, /docs ──HTTP / WebSocket──► EC2 t3.micro (Amazon Linux 2023)
                             │                         Docker: FastAPI + gcc (sandbox), puerto 80 → 8000
                             │                         rol IAM (sin llaves guardadas) ──► DynamoDB: <pila>-casos
```

**HTTPS.** Una sola distribución de CloudFront atiende todo por HTTPS:
el frontend sale de S3 y las rutas del backend van a la instancia. Como
página y API comparten origen, el navegador no bloquea contenido mixto, no
hace falta CORS y el WebSocket pasa a `wss://`. HTTP se redirige a HTTPS.
El certificado es el `*.cloudfront.net` de AWS, así que no se necesita
dominio propio. El sitio HTTP de S3 sigue funcionando: `config.js` usa el
mismo origen cuando la página carga por HTTPS y la URL de EC2 en otro caso.

| Pieza | Servicio | Configuración para no gastar |
| --- | --- | --- |
| Frontend | S3, *static website hosting* | 4 archivos, unos 30 KB |
| HTTPS | CloudFront (`PriceClass_100`, sin caché) | capa gratuita permanente: 1 TB y 10 millones de solicitudes al mes |
| Backend | EC2 `t3.micro` (o `t2.micro` / `t4g.micro`) | una instancia, disco gp3 de 16 GB, sin IP elástica |
| Base NoSQL | DynamoDB | capacidad aprovisionada 5 lectura / 5 escritura; 54 ítems (~200 KB) |
| Permisos | IAM | el rol de la instancia solo puede leer y escribir **esa** tabla |
| Acceso a la máquina | Systems Manager (Session Manager) | sin SSH y sin pares de llaves |

**Sobre la capa gratuita.** Desde julio de 2025, las cuentas nuevas reciben
hasta USD 200 en créditos y un plan gratuito de 6 meses
([anuncio de AWS](https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/),
[preguntas frecuentes](https://aws.amazon.com/free/free-tier-faqs/)). Los
límites exactos de cada servicio dependen del plan y de la fecha de creación
de la cuenta. **Revísalos en la consola (Billing → Free Tier y Credits)
antes de crear la pila.** La plantilla usa lo más pequeño de cada servicio,
y `destroy.ps1` borra todo al terminar la sustentación.

## 2. Requisitos (una vez)

1. Cuenta de AWS con el plan gratuito.
2. Un usuario IAM para tu trabajo (no la cuenta raíz) con permisos sobre
   CloudFormation, EC2, S3, DynamoDB, IAM y SSM.
3. AWS CLI v2 instalada y configurada con ese usuario:

```powershell
winget install --id Amazon.AWSCLI
```

```powershell
aws configure
```

(Pide la *Access key*, la *Secret key*, la región, por ejemplo `us-east-1`, y el formato `json`.)

## 3. Desplegar

Desde la raíz del repositorio. La instancia clona el repo público desde
GitHub: usa `-Branch main` cuando el PR esté fusionado o
`feat/proyecto-completo` mientras tanto.

```powershell
.\deploy\aws\deploy.ps1 -Region us-east-1 -Branch feat/proyecto-completo
```

El script:

1. Crea la pila: la tabla DynamoDB, el bucket S3 con su política de
   lectura pública, el rol IAM, el grupo de seguridad (puerto 80) y la
   instancia EC2.
2. Al arrancar, la instancia instala Docker, clona el repo, construye la
   imagen y la ejecuta con el sistema de archivos de solo lectura, `/tmp`
   en memoria, sin privilegios y con un límite de 700 MB. Luego **carga los
   54 casos en DynamoDB** (`python -m web.backend.seed`).
3. Sube el frontend a S3 con un `config.js` que apunta a la URL pública
   del backend.
4. Imprime las dos URL: frontend (S3) y backend (EC2).

La primera vez la instancia tarda unos 10 minutos en construir la imagen.
Cuando `http://<backend>/api/health` responda
`"store": "Amazon DynamoDB", "cases": 54`, el sistema está listo.

**Verificación:** abre la URL del frontend, entra a la pestaña **6. Casos
(NoSQL)** y pulsa **Ejecutar todos**. Debe dar 54/54. Las métricas de cada
caso quedan guardadas en DynamoDB, y se ven en la consola en DynamoDB →
Tables → `wdpda-casos` → Explore items.

## 4. Borrar todo (para no gastar créditos)

```powershell
.\deploy\aws\destroy.ps1 -Region us-east-1
```

## 5. Seguridad del despliegue

- La instancia no tiene llaves de AWS: usa las credenciales temporales de
  su rol (IMDSv2 obligatorio). El rol solo puede operar sobre la tabla de
  casos.
- CORS: el backend acepta peticiones solo desde el sitio S3 (`CORS_ORIGINS`).
  Cualquier otro origen recibe `400` en la verificación previa.
- El contenedor corre igual que en `docker-compose.yml`: usuario sin
  privilegios, solo lectura, `cap-drop ALL`, `no-new-privileges` y límite
  de memoria y CPU.
- Limitación conocida: S3 *website* y EC2 sirven por **HTTP**, sin TLS.
  Para HTTPS habría que agregar CloudFront delante de ambos y un
  certificado, lo que agrega costo y complejidad para una demo.
- Para la sustentación se puede restringir el acceso a la API a tu red
  con el parámetro `AllowedCidr` (por ejemplo `181.x.x.x/32`).

## 6. Casos de prueba NoSQL

El corpus está versionado en `tests/casos_nosql.json` y lo regenera
`python -m web.backend.seed --export --no-store`. Es el mismo que se carga
en DynamoDB (en AWS), en MongoDB (`docker-compose`) o en el archivo local.

**Estructura de un ítem** (tabla `wdpda-casos`, clave de partición `id`):

```json
{
  "id": "persona-invalido-varias",
  "categoria": "invalidos",
  "formato": "json",
  "nombre": "tres violaciones (peso 3)",
  "esquema": "{ \"type\": \"object\", ... }",
  "payload": "{\"nombre\":\"\",\"edad\":3.5,\"correo\":\"malo\"}",
  "esperado": {"status": "INVALID", "peso": 3,
               "errores": [{"code": "MIN_LENGTH", "path": "/nombre", "offset": 10}, "..."]},
  "oraculo": "INVALID",
  "metricas": {"py": {"status": "INVALID", "seconds": 0.0004},
               "c": {"status": "INVALID", "mb_s": 0.2, "max_depth": 1}, "coincide": true},
  "actualizado": "2026-11-24T20:07:00+00:00"
}
```

- `payload_b64` aparece cuando el payload no es UTF-8 válido (casos de
  UTF-8 malformado).
- `payload_gen` reemplaza al payload cuando este supera el límite de
  **400 KB por ítem** de DynamoDB (el caso de 1 MB). Es una receta
  determinista que se regenera al ejecutar el caso.
- `nota` documenta las dos discrepancias en las que se equivoca el
  oráculo (`docs/pruebas.md` §3).

| Categoría | Casos | Qué cubre |
| --- | ---: | --- |
| `validos` | 18 | 7 esquemas JSON (simple, anidado, recursivo) y 2 XSD (incluido uno recursivo) |
| `invalidos` | 7 | tipo, requerido, propiedad adicional, enum, rango, 3 violaciones (peso 3) y XSD con 7 violaciones |
| `malformados` | 9 | sintaxis, UTF-8 inválido y sobrelargo, clave duplicada, literal, cero inicial, XML sin cerrar o cruzado |
| `fuera_dpda` | 8 | `anyOf`, `oneOf`, `not`, `uniqueItems`, `pattern` |
| `bombeo` | 6 | `u·vᵏ·x·yᵏ·z` con k = 10, 100, 1000 y 200; arreglo de 20 000 elementos |
| `extremos` | 6 | profundidad 300, números de 31 dígitos y `1e400`, *billion laughs*, 1 MB |

**Prueba automática del almacén:** `tests/test_nosql.py` usa un DynamoDB
simulado (`moto`). Carga los 54 casos y verifica que ninguno supera 400 KB,
que se leen sin pérdida, el filtro por categoría, las métricas y el caso
de 1 MB regenerado byte a byte.
