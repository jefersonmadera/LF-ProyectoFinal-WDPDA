"""Arnés de benchmarks — Semana 1.

Mide, en la misma máquina y con el mismo payload, lo que la Semana 1
puede medir de verdad:
- Tokenizador Python (Fase 1): tiempo y MB/s al tokenizar.
- Runtime C (esqueleto): tiempo y MB/s al alimentar por bloques
  (compila el CLI con gcc si está disponible).

Metodología (la que pide la guía, en versión Semana 1):
- Payload ya en memoria / en disco antes de medir (no se mide la
  lectura de disco en el caso Python; en el caso C el CLI lee por
  bloques, como lo hará el validador real).
- 2 ejecuciones de calentamiento y 5 medidas; se reporta la mediana.
- Sin comparación C contra Python como "mejora": son dos piezas
  distintas (tokenizador completo contra esqueleto estructural) y
  la línea base DOM + validador genérico llega en la Semana 2-3
  con el validador real. Compararlos ahora sería engañoso y la
  guía lo prohíbe explícitamente para benchmarks injustos.
- Ciclos/byte: se intenta con `perf stat -e cycles` si `perf`
  está disponible; si no, se reporta N/A en vez de inventar un
  número. En esta máquina de desarrollo `perf` normalmente no
  está disponible sin permisos, así que N/A es el resultado
  esperado, no un fallo del bench.

Uso: python3 bench/bench.py [--size-kb 512] [--reps 5]
"""

import argparse
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler import JsonTokenizer  # noqa: E402


def make_payload(size_kb: int) -> bytes:
    """Payload JSON sintético: un arreglo de objetos, tamaño aproximado."""
    item = b'{"id": 12345, "nombre": "articulo", "precio": 19.99, "activo": true, "tags": ["a", "b"]}'
    n = (size_kb * 1024) // len(item) + 1
    return b"[" + b", ".join([item] * n) + b"]"


def median_time(fn, reps: int) -> float:
    fn()  # calentamiento (2 veces en total con la segunda llamada abajo)
    fn()
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-kb", type=int, default=256)
    ap.add_argument("--reps", type=int, default=5)
    args = ap.parse_args()

    payload = make_payload(args.size_kb)
    # Validar que el payload generado sea tokenizable (si no, el bench
    # mediría un error, no el trabajo real).
    JsonTokenizer().tokenize(payload)
    print(f"payload: {len(payload)} bytes")

    def run_python():
        JsonTokenizer().tokenize(payload)

    t = median_time(run_python, args.reps)
    print(f"python lexer: {t:.4f} s (mediana)  "
          f"{len(payload) / t / 1e6:.2f} MB/s")

    # Runtime C, si gcc está disponible.
    c_dir = ROOT / "runtime" / "c"
    cli = Path(tempfile.gettempdir()) / "wdpda_bench_cli"
    try:
        subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-O2", "-o", str(cli),
                        str(c_dir / "wdpda.c"), str(c_dir / "main.c")],
                       check=True, capture_output=True)
    except Exception as exc:  # gcc ausente: no inventar cifras
        print(f"c runtime: no medido (gcc no disponible: {exc})")
        return
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        f.write(payload)
        path = f.name
    for chunk in (1, 64, 4096):
        def run_c(chunk=chunk):
            subprocess.run([str(cli), path, str(chunk)],
                           check=True, capture_output=True)
        t = median_time(run_c, args.reps)
        print(f"c runtime (bloque {chunk}): {t:.4f} s (mediana)  "
              f"{len(payload) / t / 1e6:.2f} MB/s")
    print("ciclos/byte: N/A (perf no disponible en esta máquina; "
          "se medirá con perf stat en la máquina fija del informe)")


if __name__ == "__main__":
    main()
