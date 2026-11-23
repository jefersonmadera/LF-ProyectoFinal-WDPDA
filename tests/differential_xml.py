"""Prueba diferencial XML: motor WDPDA contra lxml (XMLSchema, DOM).

Uso: ``python tests/differential_xml.py [N]``
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.xml_engine import validate_xml  # noqa: E402
from compiler.xml_gen import gen_xml, mutate_xml  # noqa: E402
from compiler.xsd import compile_xsd  # noqa: E402

SCHEMAS = sorted((ROOT / "tests" / "schemas_xsd").glob("*.xsd"))


def run(n: int = 300, verbose: bool = True) -> dict:
    from lxml import etree
    report = {}
    for f in SCHEMAS:
        ir = compile_xsd(f.read_bytes())
        oracle = etree.XMLSchema(etree.parse(str(f)))
        diffs, valid, total = [], 0, 0
        for seed in range(n):
            x = gen_xml(ir, seed)
            for doc in (x, mutate_xml(x, seed)):
                data = doc.encode("utf-8")
                try:
                    exp = oracle.validate(etree.fromstring(data))
                except etree.XMLSyntaxError:
                    exp = None
                r = validate_xml(ir, data)
                r1 = validate_xml(ir, data, chunk=5)
                total += 1
                valid += bool(exp)
                ok = (r.status == "MALFORMED") if exp is None else \
                    (r.valid == exp)
                if not ok or r1.status != r.status or r1.weight != r.weight:
                    diffs.append((doc[:160], exp, r.status,
                                  [(e.code, e.path) for e in r.errors][:3]))
        report[f.name] = {"total": total, "valid": valid,
                          "diffs": len(diffs), "examples": diffs[:3]}
        if verbose:
            print(f"{f.name:16} elementos={len(ir['elements']):2} "
                  f"Q={ir['formal']['states']:3} δ={ir['formal']['transitions']:3}"
                  f" válidos={valid:4}/{total} diferencias={len(diffs)}")
            for d in diffs[:3]:
                print("   DIF", d)
    return report


if __name__ == "__main__":
    rep = run(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
    bad = sum(r["diffs"] for r in rep.values())
    print(f"total={sum(r['total'] for r in rep.values())} diferencias={bad}")
    sys.exit(1 if bad else 0)
