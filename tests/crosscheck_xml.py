"""Prueba cruzada XML: motor de referencia Python vs validador C generado.

Uso: ``python tests/crosscheck_xml.py [N]``
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from crosscheck import key, run_native  # noqa: E402

from compiler.codegen_c import build_c  # noqa: E402
from compiler.xml_engine import validate_xml  # noqa: E402
from compiler.xml_gen import gen_xml, mutate_xml  # noqa: E402
from compiler.xsd import compile_xsd  # noqa: E402

SCHEMAS = sorted((ROOT / "tests" / "schemas_xsd").glob("*.xsd"))

FIXED = [
    b"<a>", b"</a>", b"<a></b>", b"<a/><b/>", b"texto", b"", b"<a b='1' b='2'/>",
    b"<!DOCTYPE a [<!ENTITY x 'y'>]><a/>", b"<a>&x;</a>", b"<a>&#0;</a>",
    b"<a>&#x1F600;&lt;&amp;</a>", b"<a><!-- c --><?pi x?></a>", b"<a\xff/>",
    b"<a>caf\xc3\xa9</a>", b"<a>\xc3</a>", b"<a x=\"<\"/>",
    b"<" + b"a>" * 1 + b"<a>" * 300 + b"</a>" * 301,
]


def main(n: int = 150) -> int:
    total = bad = 0
    for f in SCHEMAS:
        ir = compile_xsd(f.read_bytes())
        docs = []
        for seed in range(n):
            x = gen_xml(ir, seed)
            docs += [x.encode(), mutate_xml(x, seed).encode()]
        docs += FIXED
        exe = build_c(ir, ROOT / "build" / "crosscheck_xml" / f.stem, f.stem)
        nat = run_native(exe, docs)
        nat1 = run_native(exe, docs, chunk=1)
        diffs = []
        for d, a, b in zip(docs, nat, nat1):
            py = validate_xml(ir, d).to_dict()
            if key(py) != key(a) or key(a) != key(b):
                diffs.append((d[:140], key(py), key(a), key(b)))
        if len(nat) != len(docs):
            diffs.append(("salida incompleta", len(nat), len(docs)))
        total += len(docs)
        bad += len(diffs)
        print(f"{f.name:16} documentos={len(docs):4} diferencias={len(diffs)}")
        for d in diffs[:4]:
            print("   DIF", d)
    print(f"total={total} diferencias={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 150))
