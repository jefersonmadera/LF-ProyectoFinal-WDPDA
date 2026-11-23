# Documentación del proyecto

Orden de lectura recomendado para preparar la sustentación:

| Documento | Contenido |
| --- | --- |
| [informe-final.md](informe-final.md) | **Informe final**: resumen, definición formal, arquitectura, complejidad, decisiones, errores, vulnerabilidades, resultados y conclusiones |
| [sustentacion.md](sustentacion.md) | **Guía de la presentación**: guion de la demo, reparto, frases clave y 20 preguntas probables con respuesta |
| [fase1-lexico.md](fase1-lexico.md) | Fase 1: alfabeto UTF-8, tokens, gramáticas regulares, AFD, streaming |
| [fase2-sintaxis.md](fase2-sintaxis.md) | Fase 2: las 4 gramáticas LL(1), FIRST/FOLLOW, tabla predictiva, árbol de derivación |
| [fase3-semantica.md](fase3-semantica.md) | Fase 3: tabla de símbolos, `$ref`, ciclos no productivos, meta-esquema, clasificación (a)/(b)/(c) |
| [fase4-wdpda.md](fase4-wdpda.md) | Fase 4: **el autómata**: definición formal, semianillo tropical, construcción, determinismo, minimización, producto (VPA), `uniqueItems`, complejidad |
| [fase5-codegen.md](fase5-codegen.md) | Fase 5: generación de C y Rust, API C-ABI/WASM, generador de payloads |
| [fase6-runtime.md](fase6-runtime.md) | Fase 6: streaming, JSON Pointer, límites, métricas, **benchmark y bombeo** |
| [fase7-web.md](fase7-web.md) | Fase 7: aplicación web, API, sandbox, NoSQL y despliegue en la nube |
| [xml-xsd.md](xml-xsd.md) | Extensión XSD: Glushkov y UPA, `xs:date` regular con bisiestos, XXE |
| [pruebas.md](pruebas.md) | Estrategia de pruebas, corpus, resultados y discrepancias con los oráculos |
| [seguridad.md](seguridad.md) | Vulnerabilidades detectadas y mitigadas |
| [ir.md](ir.md) | Formato del IR (congelado, versión 1) |
| [decisiones.md](decisiones.md) | Decisiones de diseño con sus alternativas descartadas |
| [diagramas/](diagramas/README.md) | Diagramas en **JFLAP (.jff)**, SVG, DOT y Mermaid |
| [fase2-parser.md](fase2-parser.md) | (histórico) el parser del prototipo `avance_mvp/` |
