"""Compilación y ejecución en sandbox de los validadores generados.

Medidas (defensa en profundidad; el contenedor añade usuario sin
privilegios, sistema de archivos de solo lectura salvo /tmp y límites de
CPU/memoria, ver ``docker-compose.yml``):

- El esquema nunca se interpreta como código: el compilador emite SOLO
  tablas de datos (``const``) y el runtime es fijo y revisado. No hay
  inyección posible en el C generado (las cadenas se escapan con
  ``c_str``, octal para todo byte no ASCII imprimible y para ``?``, que
  evita trigrafos).
- Límites de tamaño: esquema ≤ 256 KB, payload ≤ 8 MB.
- ``gcc`` y el validador se ejecutan con ``subprocess`` sin shell, en un
  directorio temporal propio, con tiempo máximo (``timeout``) y el
  validador además con ``--timeout-ms`` y ``--max-bytes``.
- Cada binario se cachea por hash del IR, así que un esquema se compila
  una vez.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from compiler.codegen_c import build_c, exe_name, gcc_env  # noqa: E402
from compiler.codegen_c import write_c_project  # noqa: E402

MAX_SCHEMA = 256 * 1024
MAX_PAYLOAD = 8 * 1024 * 1024
WORK = Path(tempfile.gettempdir()) / "wdpda-web"
_lock = threading.Lock()


def ir_hash(ir: dict) -> str:
    blob = json.dumps({k: ir[k] for k in ("nodes", "dfas", "consts", "root")
                       } | ({"elements": ir["elements"]} if "elements" in ir
                            else {}), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def validator_exe(ir: dict) -> Path:
    h = ir_hash(ir)
    d = WORK / h
    exe = exe_name(d / "validator")
    with _lock:
        if not exe.exists():
            build_c(ir, d, "schema")
    return exe


def baseline_exe(ir: dict) -> Path | None:
    if ir.get("format") == "xml":
        return None
    h = ir_hash(ir)
    d = WORK / h
    exe = exe_name(d / "baseline")
    with _lock:
        if not exe.exists():
            write_c_project(ir, d, "schema")
            subprocess.run(["gcc", "-std=c11", "-O2", "-o", str(exe),
                            str(ROOT / "runtime" / "c" / "baseline_dom.c"),
                            str(d / "schema_schema.c"), "-I", str(d)],
                           check=True, capture_output=True, env=gcc_env(),
                           timeout=120)
    return exe


def run_validator(ir: dict, payload: bytes, chunk: int | None = None,
                  bench: int = 0, fail_fast: bool = False,
                  max_depth: int | None = None) -> dict:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload mayor que {MAX_PAYLOAD} bytes")
    exe = validator_exe(ir)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".in") as f:
        f.write(payload)
        path = f.name
    cmd = [str(exe), path, "--timeout-ms", "5000",
           "--max-bytes", str(MAX_PAYLOAD)]
    if chunk:
        cmd += ["--chunk", str(chunk)]
    if bench:
        cmd = [str(exe), path, "--bench", str(bench)]
    if fail_fast:
        cmd.append("--fail-fast")
    if max_depth:
        cmd += ["--max-depth", str(max_depth)]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])


def run_baseline(ir: dict, payload: bytes, bench: int = 3) -> dict | None:
    exe = baseline_exe(ir)
    if exe is None:
        return None
    with tempfile.NamedTemporaryFile(delete=False, suffix=".in") as f:
        f.write(payload)
        path = f.name
    try:
        p = subprocess.run([str(exe), path, "--bench", str(bench)],
                           capture_output=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
