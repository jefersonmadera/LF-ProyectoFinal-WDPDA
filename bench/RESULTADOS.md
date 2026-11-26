# Resultados del benchmark

Máquina: Windows-11-10.0.26100-SP0 — Intel64 Family 6 Model 78 Stepping 3, GenuineIntel — Python 3.13.7
Esquema: `bench\schemas\eventos.json` — δ con 40 estados y 217 transiciones.

Mediana de R repeticiones tras 2 de calentamiento. WDPDA (C, Rust, Python) y la línea base DOM-C dieron VALID en todos los tamaños. `jsonschema` dio INVALID: evalúa `multipleOf: 0.01` con `float` binario (1.11 / 0.01 = 110.99999…) y produce falsos negativos; el WDPDA usa aritmética decimal exacta. Su tiempo se reporta igual (recorre todo el documento).

| Tamaño | Implementación | MB/s | ciclos/byte | asignaciones | memoria |
| --- | --- | ---: | ---: | ---: | ---: |
| 64 KB | wdpda-c | 75.4 | 34.38 | 0 | 1108 KB |
| 64 KB | wdpda-c-chunk64 | 78.9 | 32.86 | 0 | 1108 KB |
| 64 KB | wdpda-rust | 53.5 | — | 0 | 1106 KB |
| 64 KB | dom-c | 23.7 | — | 17467 | 638 KB |
| 64 KB | jsonschema | 1.3 | — | — | 556 KB |
| 64 KB | wdpda-py | 0.4 | — | — | 0 KB |
| 1024 KB | wdpda-c | 77.7 | 33.37 | 0 | 1108 KB |
| 1024 KB | wdpda-c-chunk64 | 72.5 | 35.76 | 0 | 1108 KB |
| 1024 KB | wdpda-rust | 52.9 | — | 0 | 1106 KB |
| 1024 KB | dom-c | 22.3 | — | 275686 | 10078 KB |
| 1024 KB | jsonschema | 1.3 | — | — | 8247 KB |
| 8192 KB | wdpda-c | 76.0 | 34.1 | 0 | 1108 KB |
| 8192 KB | wdpda-c-chunk64 | 67.9 | 38.19 | 0 | 1108 KB |
| 8192 KB | wdpda-rust | 52.4 | — | 0 | 1106 KB |
| 8192 KB | dom-c | 23.3 | — | 2181019 | 79791 KB |
| 8192 KB | jsonschema | 1.1 | — | — | 64930 KB |

## Bombeo `u·v^k·x·y^k·z` (v = `{"a":`, y = `}`)

| k | bytes | WDPDA s | pila WDPDA | memoria WDPDA | DOM s | asignaciones DOM | pico DOM |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 61 | 0.000003 | 10 | 1312 KB | 0.000006 | 52 | 2 KB |
| 100 | 601 | 0.000007 | 100 | 1312 KB | 0.000039 | 502 | 18 KB |
| 1000 | 6001 | 0.000067 | 1000 | 1312 KB | 0.000679 | 5002 | 180 KB |
| 5000 | 30001 | 0.000333 | 5000 | 1312 KB | 0.003080 | 25002 | 899 KB |
| 10000 | 60001 | 0.000686 | 10000 | 1312 KB | 0.007298 | 50002 | 1797 KB |

Profundidad 300 con límite 256: `DEPTH` — profundidad máxima superada

## Carga sostenida

20000 documentos en un proceso: 1.20 s, 16711 doc/s, 18.0 MB/s (incluye arranque del proceso y E/S).
