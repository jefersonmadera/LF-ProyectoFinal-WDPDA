# Representación intermedia (IR) del autómata — BORRADOR Semana 0

> Borrador para acordar por escrito entre Jeferson y Sergio antes de escribir código de las fases 1–4. Nada de esto está congelado todavía.

## Principio

Una única representación del autómata en JSON. El backend C, el backend Rust, el visualizador web y el informe leen el mismo IR, así que lo que se dibuja es exactamente lo que se ejecuta.

## Definición formal (pendiente de redactar en el informe)

WDPDA: M = (Q, Σ, Γ, δ, q₀, Z₀, F, w), con pesos en el semianillo tropical (ℝ ∪ {+∞}, min, +, +∞, 0).

- El peso estima el costo de cada transición para ordenar comprobaciones (fallo temprano) y acumula el número de violaciones para reportar la conformidad del payload.
- Marco de pila por nivel de anidamiento: (id de nodo del esquema, estado de retorno, registros locales) → memoria O(d) en profundidad d, tiempo O(n) en bytes n.

## Campos mínimos que debe tener el IR (propuesta)

- `schema_id` y versión del IR.
- `states` (Q), `alphabet` (Σ, bytes UTF-8 / tokens), `stack_alphabet` (Γ).
- `initial_state` (q₀), `initial_stack_symbol` (Z₀), `accepting_states` (F).
- `transitions` (δ): cada una con estado origen, símbolo de entrada, símbolo a desapilar, estado destino, símbolos a apilar y peso w.
- `registers`: máscaras de `required`, contadores acotados (minItems/maxItems, etc.) por nodo.
- Metadatos de determinismo: resultado del verificador automático de determinismo de δ.

## Lo que falta acordar

- Nombres exactos de campos y tipos (¿enteros para ids de nodo? ¿cadenas para símbolos?).
- Cómo se representan los subautómatas por nodo del esquema y el producto para allOf/anyOf/oneOf.
- Límites documentados: profundidad máxima de pila, propiedades obligatorias por objeto (p. ej. máscara de 64 bits), tope de ramas del producto.
