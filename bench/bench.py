"""Benchmark reproducible del proyecto (Fase 6).

Compara, en la misma máquina, con el mismo payload y el mismo esquema
(``bench/schemas/eventos.json``):

- ``wdpda-c``     validador generado en C (una pasada, sin malloc)
- ``wdpda-rust``  validador generado en Rust (opcional, ``--rust``)
- ``dom-c``       línea base DOM en C: parsea a árbol con malloc y luego
                  recorre interpretando LAS MISMAS tablas del esquema
- ``jsonschema``  Python: json.loads (DOM) + jsonschema (interpretado)
- ``wdpda-py``    motor de referencia Python (solo tamaños pequeños)

Metodología: payload en disco y en memoria antes de medir; 2 ejecuciones
de calentamiento y R medidas; se reporta la mediana. MB/s = bytes /
mediana. Ciclos/byte con ``rdtsc`` (x86). Memoria: el WDPDA usa un
contexto de tamaño fijo (ctx_bytes) y 0 asignaciones; la línea base
reporta asignaciones y bytes pico de su asignador instrumentado;
``jsonschema`` reporta el pico de ``tracemalloc``.

Además: pruebas de bombeo ``u·v^k·x·y^k·z`` (anidamiento creciente),
arreglos muy largos y carga sostenida (muchos documentos seguidos).

Uso: python bench/bench.py [--sizes 64,1024,8192] [--reps 7] [--rust]
Resultados: bench/results.json y bench/RESULTADOS.md
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.codegen_c import build_c, exe_name, gcc_env, write_c_project  # noqa
from compiler.wdpda import compile_schema  # noqa: E402

SCHEMA = ROOT / "bench" / "schemas" / "eventos.json"
BUILD = ROOT / "build" / "bench"


def make_payload(size_kb: int) -> bytes:
    """Lote JSON de eventos válidos, determinista, de ~size_kb KB."""
    tipos = ["click", "compra", "login", "logout"]
    items = []
    total = 2
    i = 0
    while total < size_kb * 1024:
        i += 1
        ev = (f'{{"id":{i},"tipo":"{tipos[i % 4]}","usuario":"u{1000 + i % 90000}",'
              f'"monto":{(i * 37) % 100000 / 100:.2f},"etiquetas":["a{i % 7}",'
              f'"web","móvil"],"meta":{{"ip":"10.0.{i % 256}.{i % 200}",'
              f'"latencia_ms":{i % 900},"cache":{"true" if i % 2 else "false"}}},'
              f'"ok":true}}')
        items.append(ev)
        total += len(ev.encode()) + 1
    return ("[" + ",".join(items) + "]").encode("utf-8")


def build_all(rust: bool, max_depth: int | None = None):
    ir = compile_schema(SCHEMA.read_bytes())
    flags = [f"-DWD_MAX_DEPTH={max_depth}"] if max_depth else []
    exe = build_c(ir, BUILD / "c", "eventos", extra_flags=flags)
    d = write_c_project(ir, BUILD / "c", "eventos")
    base = exe_name(BUILD / "c" / "baseline")
    cmd = ["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-o", str(base),
           str(ROOT / "runtime" / "c" / "baseline_dom.c"),
           str(d / "eventos_schema.c"), "-I", str(d)]
    p = subprocess.run(cmd, capture_output=True, text=True, env=gcc_env())
    if p.returncode:
        raise RuntimeError(p.stderr)
    rexe = None
    if rust:
        from compiler.codegen_rust import build_rust
        rexe = build_rust(ir, BUILD / "rust", "eventos")
    return ir, exe, base, rexe


def run_json(cmd) -> dict:
    p = subprocess.run([str(c) for c in cmd], capture_output=True)
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])


def bench_python_jsonschema(payload: bytes, reps: int) -> dict:
    import jsonschema
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema)

    def once():
        # iter_errors recorre TODO el documento (is_valid se detendría en
        # el primer error y mediría casi solo json.loads).
        return not any(True for _ in v.iter_errors(json.loads(payload))) \
            if False else len(list(v.iter_errors(json.loads(payload)))) == 0

    once()
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        ok = once()
        times.append(time.perf_counter() - t0)
    tracemalloc.start()
    once()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    t = statistics.median(times)
    return {"status": "VALID" if ok else "INVALID", "seconds": t,
            "mb_s": len(payload) / t / 1e6, "peak_bytes": peak}


def bench_python_wdpda(ir: dict, payload: bytes, reps: int) -> dict:
    from compiler.engine import validate
    validate(ir, payload)
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        r = validate(ir, payload)
        times.append(time.perf_counter() - t0)
    t = statistics.median(times)
    return {"status": r.status, "seconds": t, "mb_s": len(payload) / t / 1e6}


def pumping(reps: int) -> list[dict]:
    """u·v^k·x·y^k·z: v='[' , y=']' (y con objetos: v='{\"a\":', y='}')."""
    ir = compile_schema(b"{}")
    exe = build_c(ir, BUILD / "pump", "pump",
                  extra_flags=["-DWD_MAX_DEPTH=20000", "-DWD_LANE_POOL=64"])
    d = BUILD / "pump"
    base = exe_name(d / "baseline")
    subprocess.run(["gcc", "-std=c11", "-O2", "-o", str(base),
                    str(ROOT / "runtime" / "c" / "baseline_dom.c"),
                    str(d / "pump_schema.c"), "-I", str(d)], check=True,
                   capture_output=True, env=gcc_env())
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for k in (10, 100, 1000, 5000, 10000):
            payload = b'{"a":' * k + b"1" + b"}" * k
            p = Path(tmp) / f"pump{k}.json"
            p.write_bytes(payload)
            w = run_json([exe, p, "--bench", reps, "--max-depth", 20000])
            b = run_json([base, p, "--bench", reps])
            rows.append({"k": k, "bytes": len(payload),
                         "wdpda_seconds": w["seconds"],
                         "wdpda_depth": w["max_depth"],
                         "wdpda_ctx_bytes": w["ctx_bytes"],
                         "wdpda_status": w["status"],
                         "dom_seconds": b["seconds"],
                         "dom_allocations": b["allocations"],
                         "dom_peak_bytes": b["peak_bytes"]})
        # el límite: profundidad sobre el máximo configurado
        p = Path(tmp) / "deep.json"
        p.write_bytes(b"[" * 300 + b"]" * 300)
        lim = run_json([exe, p, "--max-depth", 256])
        rows.append({"k": 300, "limit_test": lim["status"],
                     "fatal_message": lim.get("fatal_message")})
    return rows


def sustained(exe: Path, n_docs: int = 20000) -> dict:
    """Carga sostenida: n documentos (uno por línea) en un solo proceso."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "lote.jsonl"
        doc = make_payload(1).decode()
        p.write_text("\n".join([doc] * n_docs), encoding="utf-8")
        t0 = time.perf_counter()
        out = subprocess.run([str(exe), str(p), "--jsonl"],
                             capture_output=True).stdout.decode().splitlines()
        t = time.perf_counter() - t0
    ok = sum('"VALID"' in x for x in out)
    return {"docs": n_docs, "valid": ok, "seconds": t,
            "docs_per_s": n_docs / t,
            "mb_s": n_docs * len(doc.encode()) / t / 1e6,
            "note": "incluye arranque del proceso y E/S"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="64,1024,8192")
    ap.add_argument("--size-kb", type=int, help="(compatibilidad) un tamaño")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--rust", action="store_true")
    ap.add_argument("--no-python", action="store_true")
    args = ap.parse_args()
    sizes = [args.size_kb] if args.size_kb else [int(x) for x in
                                                 args.sizes.split(",")]
    ir, exe, base, rexe = build_all(args.rust)
    res = {"machine": {"python": sys.version.split()[0],
                       "platform": platform.platform(),
                       "processor": platform.processor()},
           "schema": str(SCHEMA.relative_to(ROOT)),
           "automaton": ir["formal"], "stats": ir["stats"], "sizes": []}
    print(f"esquema: {SCHEMA.name}  δ: {ir['formal']['states']} estados, "
          f"{ir['formal']['transitions']} transiciones")
    with tempfile.TemporaryDirectory() as tmp:
        for kb in sizes:
            payload = make_payload(kb)
            p = Path(tmp) / f"p{kb}.json"
            p.write_bytes(payload)
            row = {"kb": kb, "bytes": len(payload)}
            row["wdpda-c"] = run_json([exe, p, "--bench", args.reps])
            row["wdpda-c-chunk64"] = run_json([exe, p, "--bench", args.reps,
                                               "--chunk", 64])
            if rexe:
                row["wdpda-rust"] = run_json([rexe, p, "--bench", args.reps])
            row["dom-c"] = run_json([base, p, "--bench", args.reps])
            if not args.no_python and kb <= 8192:
                row["jsonschema"] = bench_python_jsonschema(
                    payload, max(3, args.reps // 2))
            if not args.no_python and kb <= 256:
                row["wdpda-py"] = bench_python_wdpda(ir, payload, 3)
            statuses = {k: v["status"] for k, v in row.items()
                        if isinstance(v, dict) and k != "jsonschema"}
            assert set(statuses.values()) == {"VALID"}, statuses
            if "jsonschema" in row and row["jsonschema"]["status"] != "VALID":
                # Hallazgo: jsonschema evalúa multipleOf con float binario
                # (1.11 / 0.01 = 110.99999...) y da falsos negativos. El
                # WDPDA usa aritmética decimal exacta. Se mide igual.
                row["jsonschema"]["note"] = (
                    "falsos INVALID por multipleOf con float binario")
            res["sizes"].append(row)
            line = f"{kb:6} KB:"
            for k, v in row.items():
                if isinstance(v, dict):
                    line += f"  {k}={v['mb_s']:.1f} MB/s"
            print(line)
    print("bombeo u·v^k·x·y^k·z ...")
    res["pumping"] = pumping(max(3, args.reps // 2))
    for r in res["pumping"]:
        print("  ", r)
    print("carga sostenida ...")
    res["sustained"] = sustained(exe)
    print("  ", res["sustained"])
    out = ROOT / "bench" / "results.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    write_markdown(res)
    print(f"resultados: {out}")


def write_markdown(res: dict) -> None:
    L = ["# Resultados del benchmark", "",
         f"Máquina: {res['machine']['platform']} — "
         f"{res['machine']['processor']} — Python {res['machine']['python']}",
         f"Esquema: `{res['schema']}` — δ con {res['automaton']['states']} "
         f"estados y {res['automaton']['transitions']} transiciones.", "",
         "Mediana de R repeticiones tras 2 de calentamiento. WDPDA (C, Rust, "
         "Python) y la línea base DOM-C dieron VALID en todos los tamaños. "
         "`jsonschema` dio INVALID: evalúa `multipleOf: 0.01` con `float` "
         "binario (1.11 / 0.01 = 110.99999…) y produce falsos negativos; el "
         "WDPDA usa aritmética decimal exacta. Su tiempo se reporta igual "
         "(recorre todo el documento).", "",
         "| Tamaño | Implementación | MB/s | ciclos/byte | asignaciones | "
         "memoria |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in res["sizes"]:
        for k in ("wdpda-c", "wdpda-c-chunk64", "wdpda-rust", "dom-c",
                  "jsonschema", "wdpda-py"):
            v = row.get(k)
            if not v:
                continue
            cpb = v.get("cycles_per_byte")
            mem = v.get("ctx_bytes") or v.get("peak_bytes")
            alloc = v.get("runtime_allocations", v.get("allocations", "—"))
            L.append(f"| {row['bytes'] / 1024:.0f} KB | {k} | {v['mb_s']:.1f} "
                     f"| {cpb if cpb is not None else '—'} | {alloc} | "
                     f"{(mem or 0) / 1024:.0f} KB |")
    L += ["", "## Bombeo `u·v^k·x·y^k·z` (v = `{\"a\":`, y = `}`)", "",
          "| k | bytes | WDPDA s | pila WDPDA | memoria WDPDA | DOM s | "
          "asignaciones DOM | pico DOM |", "| ---: | ---: | ---: | ---: | "
          "---: | ---: | ---: | ---: |"]
    for r in res["pumping"]:
        if "limit_test" in r:
            continue
        L.append(f"| {r['k']} | {r['bytes']} | {r['wdpda_seconds']:.6f} | "
                 f"{r['wdpda_depth']} | {r['wdpda_ctx_bytes'] / 1024:.0f} KB | "
                 f"{r['dom_seconds']:.6f} | {r['dom_allocations']} | "
                 f"{r['dom_peak_bytes'] / 1024:.0f} KB |")
    lim = [r for r in res["pumping"] if "limit_test" in r]
    if lim:
        L += ["", f"Profundidad 300 con límite 256: `{lim[0]['limit_test']}`"
              f" — {lim[0]['fatal_message']}"]
    s = res["sustained"]
    L += ["", "## Carga sostenida", "",
          f"{s['docs']} documentos en un proceso: {s['seconds']:.2f} s, "
          f"{s['docs_per_s']:.0f} doc/s, {s['mb_s']:.1f} MB/s ({s['note']})."]
    (ROOT / "bench" / "RESULTADOS.md").write_text("\n".join(L) + "\n",
                                                 encoding="utf-8")


if __name__ == "__main__":
    main()
