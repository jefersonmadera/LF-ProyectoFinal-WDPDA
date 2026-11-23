"""Prueba diferencial: motor WDPDA contra un oráculo (``jsonschema``).

Para cada esquema de ``tests/schemas`` genera N payloads válidos con el
generador de la Fase 5 y una mutación de cada uno, y compara:

- validez según el motor de referencia (entrada completa),
- validez y peso según el motor alimentado en bloques de 7 bytes
  (equivalencia de streaming),
- validez según ``jsonschema.Draft202012Validator`` (oráculo, basado en
  DOM: parsea con ``json.loads`` y recorre el árbol).

Uso: ``python tests/differential.py [N]``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.engine import validate  # noqa: E402
from compiler.payload_gen import gen_valid, mutate, to_json  # noqa: E402
from compiler.wdpda import compile_schema  # noqa: E402

SCHEMAS = sorted((ROOT / "tests" / "schemas").glob("*.json"))


def run(n: int = 300, verbose: bool = True) -> dict:
    import jsonschema
    report = {}
    for f in SCHEMAS:
        raw = f.read_bytes()
        ir = compile_schema(raw)
        oracle = jsonschema.Draft202012Validator(json.loads(raw))
        diffs, valid, total = [], 0, 0
        for seed in range(n):
            v = gen_valid(ir, seed)
            for doc in (v, mutate(ir, v, seed)[0]):
                txt = to_json(doc)
                exp = oracle.is_valid(json.loads(txt))
                r = validate(ir, txt.encode())
                r7 = validate(ir, txt.encode(), chunk=7)
                total += 1
                valid += exp
                if r.valid != exp or r7.valid != r.valid or \
                        r7.weight != r.weight:
                    diffs.append((txt, exp, r.status,
                                  [(e.code, e.path) for e in r.errors][:3]))
        st, fo = ir["stats"], ir["formal"]
        report[f.name] = {"total": total, "valid": valid,
                          "diffs": len(diffs), "examples": diffs[:3],
                          "nodes": st["nodes_after_minimization"],
                          "reachable": st["reachable_nodes"],
                          "states": fo["states"],
                          "transitions": fo["transitions"],
                          "deterministic": fo["deterministic"]}
        if verbose:
            print(f"{f.name:18} nodos={st['nodes_after_minimization']:2}/"
                  f"{st['reachable_nodes']:2} Q={fo['states']:3} "
                  f"δ={fo['transitions']:4} det={fo['deterministic']} "
                  f"válidos={valid:4}/{total} diferencias={len(diffs)}")
            for d in diffs[:2]:
                print("   DIF", d[0][:140], "oráculo:", d[1], "motor:", d[2],
                      d[3])
    return report


if __name__ == "__main__":
    rep = run(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
    tot = sum(r["total"] for r in rep.values())
    bad = sum(r["diffs"] for r in rep.values())
    print(f"total={tot} diferencias={bad}")
    sys.exit(1 if bad else 0)
