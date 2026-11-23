"""Prueba cruzada: motor de referencia Python vs validadores nativos.

Para cada esquema de ``tests/schemas`` compila el validador C (y el Rust
si ``--rust``) y le pasa, en un solo proceso (modo ``--jsonl``), los
mismos payloads que al motor Python: generados válidos, mutados y un
conjunto fijo de malformados/extremos. Compara:

- categoría del estado (VALID / INVALID / fatal),
- peso tropical (número de violaciones) cuando no es fatal,
- la lista de errores (código, ruta JSON Pointer), en orden,
- y lo mismo alimentando el validador nativo en bloques de 1 byte.

Uso: ``python tests/crosscheck.py [N] [--rust]``
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.codegen_c import build_c  # noqa: E402
from compiler.engine import FATAL, validate  # noqa: E402
from compiler.payload_gen import gen_valid, mutate, to_json  # noqa: E402
from compiler.wdpda import compile_schema  # noqa: E402

SCHEMAS = sorted((ROOT / "tests" / "schemas").glob("*.json"))

# Casos fijos: malformados, UTF-8, extremos (una línea cada uno).
FIXED = [
    b'{"a":1,}', b'{"a" 1}', b'[1,2', b'{"a":tru}', b'{"a":truex}',
    b'[01]', b'[1.]', b'[-]', b'[1e]', b'"\\x"', b'"\\ud800"', b'"\\udc00"',
    b'"\\ud83d\\ude00"', b'"caf\xc3\xa9"', b'"\xc3"', b'"\xff"', b'"\xed\xa0\x80"',
    b'{} {}', b'', b'   ', b'nul', b'[1 2]', b'{"a":1 "b":2}',
    b'[' * 300 + b']' * 300,
    b'{"k":' * 40 + b'1' + b'}' * 40,
    b'[123456789012345678901234567890123456789012345678901234567890123456789012345]',
    b'[1e400, -1e400, 1e-400, 0.0000000000000000000000000001]',
    b'{"x":"\\u00e9\\u4e2d\\ud83d\\ude00\\n\\t\\"\\\\\\/"}',
]


def category(status: str) -> str:
    return "FATAL" if status in FATAL else status


def payloads(ir: dict, n: int) -> list[bytes]:
    out = []
    for seed in range(n):
        v = gen_valid(ir, seed)
        out.append(to_json(v).encode("utf-8"))
        out.append(to_json(mutate(ir, v, seed)[0]).encode("utf-8"))
    return out + FIXED


def run_native(exe: Path, docs: list[bytes], chunk: int | None = None):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
        f.write(b"\n".join(d.replace(b"\n", b" ") for d in docs) + b"\n")
        path = f.name
    cmd = [str(exe), path, "--jsonl"] + (["--chunk", str(chunk)] if chunk
                                         else [])
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    lines = proc.stdout.decode("utf-8", "replace").splitlines()
    Path(path).unlink(missing_ok=True)
    return [json.loads(x) for x in lines]


def key(res: dict) -> tuple:
    cat = category(res["status"])
    if cat == "FATAL":
        return (cat,)
    return (cat, res["weight"], tuple((e["code"], e["path"])
                                      for e in res["errors"]))


def compare_impl(exe: Path, ir: dict, docs: list[bytes], label: str):
    diffs = []
    native = run_native(exe, docs)
    native1 = run_native(exe, docs, chunk=1)
    if len(native) != len(docs) or len(native1) != len(docs):
        return [("salida incompleta", len(native), len(native1), len(docs))]
    for doc, a, b in zip(docs, native, native1):
        d = doc.replace(b"\n", b" ")
        py = validate(ir, d).to_dict()
        if key(py) != key(a) or key(a) != key(b):
            diffs.append((label, d[:120], key(py), key(a), key(b)))
    return diffs


def main(n: int = 100, rust: bool = False) -> int:
    total_bad = 0
    total = 0
    build = ROOT / "build" / "crosscheck"
    for f in SCHEMAS:
        ir = compile_schema(f.read_bytes())
        docs = payloads(ir, n)
        exe = build_c(ir, build / f.stem / "c", f.stem)
        diffs = compare_impl(exe, ir, docs, "C")
        if rust:
            from compiler.codegen_rust import build_rust
            rexe = build_rust(ir, build / f.stem / "rust", f.stem)
            diffs += compare_impl(rexe, ir, docs, "Rust")
        total += len(docs)
        total_bad += len(diffs)
        print(f"{f.name:18} payloads={len(docs):4} diferencias={len(diffs)}")
        for d in diffs[:3]:
            print("   DIF", d)
    print(f"total={total} diferencias={total_bad}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(int(args[0]) if args else 100, "--rust" in sys.argv))
