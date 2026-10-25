# Decisiones

Cada decisión registra su alternativa y por qué se descartó. Es material directo para el informe final.

## Decidido en Semana 0 (2026-10-24)

- **Repositorio único con la estructura de la guía:** `compiler/`, `runtime/c`, `runtime/rust`, `web/`, `tests/`, `docs/`, `bench/`.
  - Alternativa descartada: un repo por integrante. Se descartó porque el IR compartido es la frontera entre teoría/compilador y runtime/plataforma.
- **Revisión cruzada obligatoria:** ningún cambio entra a la rama principal sin aprobación del otro en pull request.
- **Sigla consistente:** WDPDA (weighted DPDA), no "DPDA ponderado (DPDA)" como aparece en el enunciado.

## Pendiente — NO aclarar con el profesor por el momento (decisión de Jeferson, 2026-10-24)

Estas dudas quedan anotadas aquí para retomarlas cuando Jeferson lo decida, sin enviar nada todavía:

- [ ] ¿Se exigen ambos backends, C **y** Rust, o basta uno con el otro opcional?
- [ ] ¿Qué tamaño de subconjunto XSD esperan (sequence, choice, minOccurs/maxOccurs, tipos simples)?
- [ ] ¿El semianillo tropical debe implementarse en tiempo de ejecución o basta con modelarlo y usarlo para ordenar comprobaciones?
- [ ] ¿`pattern` debe aceptar toda la sintaxis ECMA-262 o un subconjunto regular documentado?
- [ ] Fecha exacta de entrega y de sustentación, y si hay entregas parciales.

## Pendiente — acuerdo entre integrantes

- [ ] Reparto A (teoría/compilador) / B (runtime/plataforma): ¿quién es quién?
- [ ] Formato exacto del IR del autómata (JSON único que lean el backend C, el backend Rust, el visualizador web y el informe).
- [ ] API `init/feed/finish` definitiva.
- [ ] Lenguaje del compilador: Python 3.12 (recomendado en la guía por iteración rápida) o Rust si ambos lo dominan.
- [ ] Nube y NoSQL: Azure Container Apps + Cosmos DB, o AWS ECS/EC2 + DynamoDB, según la que ya conozcan.
