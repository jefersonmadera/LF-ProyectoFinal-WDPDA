"""Fase 5 — Generación de código C a partir del IR.

Emite ``<nombre>_schema.c``: todas las tablas del WDPDA como datos
``const`` (van a la sección de solo lectura del binario):

- un AFD por tabla densa ``[estado × clase de bytes]`` con su tabla de
  clases de 256 entradas (la compresión por clases de bytes es la
  minimización del alfabeto: bytes indistinguibles comparten columna);
- los nodos del esquema (subautómatas) con sus registros acotados;
- las constantes decimales exactas.

Junto a él se copian el runtime (``wdpda_rt.c/.h``, que ejecuta las
tablas) y el CLI. El resultado compila con
``gcc -std=c11 -Wall -Wextra -Werror -O2`` sin dependencias.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_C = ROOT / "runtime" / "c"
RUNTIME_FILES = ["wdpda_rt.h", "wdpda_rt.c", "wdpda_cli.c"]

OPS = {"ge": "WD_OP_GE", "gt": "WD_OP_GT", "le": "WD_OP_LE",
       "lt": "WD_OP_LT", "mul": "WD_OP_MUL"}


def c_ident(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return s if s and not s[0].isdigit() else "s_" + s


def c_str(s: str) -> str:
    out = []
    for b in s.encode("utf-8"):
        ch = chr(b)
        if ch in '"\\':
            out.append("\\" + ch)
        elif 0x20 <= b < 0x7F and ch != "?":
            out.append(ch)
        else:
            out.append(f"\\{b:03o}")
    return '"' + "".join(out) + '"'


def _arr(ctype: str, name: str, vals, per_line: int = 16) -> str:
    vals = list(vals)
    if not vals:
        return ""
    lines = []
    for i in range(0, len(vals), per_line):
        lines.append("    " + ", ".join(str(v) for v in vals[i:i + per_line]))
    return f"static const {ctype} {name}[{len(vals)}] = {{\n" + \
        ",\n".join(lines) + "\n};\n"


def generate_c(ir: dict, name: str = "schema") -> str:
    p = c_ident(name)
    xml = ir.get("format") == "xml"
    out = [f"/* {name}_schema.c — GENERADO por compiler/codegen_c.py.",
           " * No editar: regenerar con `python -m compiler compile`.",
           f" * Formato: {ir.get('format', 'json')}  Nodos: "
           f"{len(ir['nodes'])}  AFD: {len(ir['dfas'])}  "
           f"estados de δ: {ir['formal']['states']}  "
           f"transiciones de δ: {ir['formal']['transitions']}",
           f" * Determinista: {ir['formal']['deterministic']} */",
           '#include "wdpda_xml.h"' if xml else '#include "wdpda_rt.h"', ""]
    _emit_common(ir, p, out)
    if xml:
        _emit_xml(ir, p, name, out)
        return "\n".join(out) + "\n"
    nn = len(ir["nodes"])
    out.append(f"const wd_schema_t {p}_schema = {{\n"
               f"    {c_str(name)}, {nn}, {p + '_nodes' if nn else '0'}, "
               f"{ir['root']},\n"
               f"    {len(ir['dfas'])}, {p + '_dfas' if ir['dfas'] else '0'}, "
               f"{len(ir['consts'])}, "
               f"{p + '_consts' if ir['consts'] else '0'},\n"
               f"    {int(ir['flags']['uses_unique'])}, "
               f"{ir['formal']['states']}, {ir['formal']['transitions']}\n}};\n")
    out.append(f"const wd_schema_t *wd_schema(void) {{ return &{p}_schema; }}")
    return "\n".join(out) + "\n"


def _emit_xml(ir: dict, p: str, name: str, out: list) -> None:
    inits = []
    for e in ir["elements"]:
        i = e["id"]
        if e["child_elems"]:
            out.append(_arr("int32_t", f"{p}_e{i}_ch", e["child_elems"]))
        if e["attrs"]:
            out.append(f"static const wx_attr_t {p}_e{i}_at[] = {{" + ", ".join(
                f"{{{c_str(a['name'])}, {a['node']}, {int(a['required'])}}}"
                for a in e["attrs"]) + "};")
        inits.append(
            f"    /* {e['name']}: {e['content_src'][:60].replace('*/', '* /')} */\n"
            f"    {{{c_str(e['name'])}, {e['simple']}, {int(e['complex'])}, "
            f"{int(e['mixed'])}, {e['content_dfa']}, {len(e['child_elems'])}, "
            f"{p + f'_e{i}_ch' if e['child_elems'] else '0'}, "
            f"{e['names_dfa']}, {len(e['attrs'])}, "
            f"{p + f'_e{i}_at' if e['attrs'] else '0'}, {e['attr_dfa']}, "
            f"{e['attr_required']}ULL, {c_str(e['content_src'])}}}")
    out.append(f"static const wx_elem_t {p}_elems[{len(inits)}] = {{\n"
               + ",\n".join(inits) + "\n};")
    out.append(_arr("int32_t", f"{p}_roots", ir["roots"]))
    nn = len(ir["nodes"])
    out.append(
        f"const wx_schema_t {p}_schema = {{\n    {c_str(name)}, "
        f"{len(ir['elements'])}, {p}_elems, {len(ir['roots'])}, {p}_roots, "
        f"{ir['root_names_dfa']},\n    {nn}, {p + '_nodes' if nn else '0'}, "
        f"{len(ir['dfas'])}, {p}_dfas, {len(ir['consts'])}, "
        f"{p + '_consts' if ir['consts'] else '0'}, "
        f"{ir['formal']['states']}, {ir['formal']['transitions']}\n}};\n")
    out.append(f"const wx_schema_t *wx_schema(void) {{ return &{p}_schema; }}")


def _emit_common(ir: dict, p: str, out: list) -> None:
    # AFD
    for d in ir["dfas"]:
        i = d["id"]
        src = d["source"] if isinstance(d["source"], str) else \
            ", ".join(map(str, d["source"]))
        out.append(f"/* AFD {i} ({d['kind']}): {src[:70].replace('*/', '* /')}"
                   f" — {d['states']} estados (antes de minimizar "
                   f"{d['states_before_min']}), {d['ncls']} clases */")
        out.append(_arr("uint8_t", f"{p}_d{i}_cls", d["classes"], 32))
        out.append(_arr("int32_t", f"{p}_d{i}_tab", d["table"], 24))
        out.append(_arr("int32_t", f"{p}_d{i}_acc", d["accept"], 24))
    if ir["dfas"]:
        out.append(f"static const wd_dfa_t {p}_dfas[{len(ir['dfas'])}] = {{")
        for d in ir["dfas"]:
            i = d["id"]
            out.append(f"    {{{p}_d{i}_cls, {d['ncls']}, {d['states']}, "
                       f"{p}_d{i}_tab, {p}_d{i}_acc}},")
        out.append("};\n")
    # constantes
    if ir["consts"]:
        out.append(f"static const wd_const_t {p}_consts[{len(ir['consts'])}]"
                   " = {")
        for k in ir["consts"]:
            out.append(f"    {{{int(k['neg'])}, {len(k['digits'])}, "
                       f"{c_str(k['digits'])}, {k['exp']}LL, "
                       f"{c_str(k['lexeme'])}}},")
        out.append("};\n")
    # arreglos por nodo
    node_inits = []
    for n in ir["nodes"]:
        i = n["id"]
        pre = f"{p}_n{i}"
        f = {}

        def arr(field, ctype, vals):
            if vals:
                out.append(_arr(ctype, f"{pre}_{field}", vals))
                return f"{pre}_{field}"
            return "0"

        e = n["enum"]
        s = n["str"]
        num = n["num"]
        o = n["obj"]
        a = n["arr"]
        f["enum_nums"] = arr("en", "uint32_t", e["nums"] if e else [])
        f["pats"] = arr("pat", "uint32_t", s["patterns"] if s else [])
        if s and s["patterns"]:
            out.append(f"static const char *const {pre}_psrc[] = {{"
                       + ", ".join(c_str(x) for x in s["pattern_src"]) + "};")
            f["pat_src"] = f"{pre}_psrc"
        else:
            f["pat_src"] = "0"
        f["num_ops"] = arr("op", "uint8_t", [OPS[op] for op, _ in
                                             num["checks"]] if num else [])
        f["num_consts"] = arr("nc", "uint32_t", [c for _, c in num["checks"]]
                              if num else [])
        if o:
            if o["keys"]:
                out.append(f"static const char *const {pre}_keys[] = {{"
                           + ", ".join(c_str(k) for k in o["keys"]) + "};")
                f["keys"] = f"{pre}_keys"
            else:
                f["keys"] = "0"
            f["key_child"] = arr("kc", "int32_t", o["key_child"])
            f["key_declared"] = arr("kd", "uint8_t",
                                    [int(x) for x in o["key_declared"]])
            f["ppat_dfa"] = arr("ppd", "uint32_t",
                                [d for d, _ in o["patterns"]])
            f["ppat_child"] = arr("ppc", "int32_t",
                                  [c for _, c in o["patterns"]])
            f["depreq_key"] = arr("drk", "uint8_t",
                                  [k for k, _ in o["dep_required"]])
            f["depreq_mask"] = arr("drm", "uint64_t",
                                   [f"{m}ULL" for _, m in o["dep_required"]])
            f["depsch_key"] = arr("dsk", "uint8_t",
                                  [k for k, _ in o["dep_schemas"]])
            f["depsch_child"] = arr("dsc", "int32_t",
                                    [c for _, c in o["dep_schemas"]])
        f["prefix"] = arr("pre", "int32_t", a["prefix"] if a else [])
        f["all_of"] = arr("all", "int32_t", n["all_of"])
        f["any_of"] = arr("any", "int32_t", n["any_of"])
        f["one_of"] = arr("one", "int32_t", n["one_of"])
        g = lambda k: f.get(k, "0")  # noqa: E731
        init = [
            f"/* n{i} {n['ptr']} — {n['desc'][:60].replace('*/', '* /')} */",
            "{", f"  {c_str(n['ptr'])}, {int(n['false'])}, {n['types']},",
            f"  {int(e is not None)}, {e['strings'] if e else -1}, "
            f"{len(e['nums']) if e else 0}, {g('enum_nums')}, "
            f"{e['lits'] if e else 0},",
            f"  {int(s is not None)}, {s['min_len'] if s else -1}, "
            f"{s['max_len'] if s else -1}, "
            f"{len(s['patterns']) if s else 0}, {g('pats')}, {g('pat_src')},",
            f"  {len(num['checks']) if num else 0}, {g('num_ops')}, "
            f"{g('num_consts')},",
        ]
        if o:
            init.append(
                f"  1, {o['keys_dfa']}, {len(o['keys'])}, {g('keys')}, "
                f"{g('key_child')}, {g('key_declared')}, {o['required']}ULL, "
                f"{len(o['patterns'])}, {g('ppat_dfa')}, {g('ppat_child')}, "
                f"{o['additional']}, {o['min_props']}, {o['max_props']}, "
                f"{len(o['dep_required'])}, {g('depreq_key')}, "
                f"{g('depreq_mask')}, {len(o['dep_schemas'])}, "
                f"{g('depsch_key')}, {g('depsch_child')},")
        else:
            init.append("  0, -1, 0, 0, 0, 0, 0ULL, 0, 0, 0, -1, -1, -1, 0, 0,"
                        " 0, 0, 0, 0,")
        if a:
            init.append(
                f"  1, {len(a['prefix'])}, {g('prefix')}, {a['items']}, "
                f"{a['contains']}, {a['min_contains']}, {a['max_contains']}, "
                f"{a['min_items']}, {a['max_items']}, {int(a['unique'])},")
        else:
            init.append("  0, 0, 0, -1, -3, -1, -1, -1, -1, 0,")
        init.append(
            f"  {len(n['all_of'])}, {len(n['any_of'])}, {len(n['one_of'])}, "
            f"{g('all_of')}, {g('any_of')}, {g('one_of')}, {n['not']}, "
            f"{n['if']}, {n['then']}, {n['else']}, "
            f"{int(bool(n.get('not_any')))}, {n.get('xsd', 0)}")
        init.append("}")
        node_inits.append("\n".join(init))
    nn = len(ir["nodes"])
    if nn:
        out.append(f"static const wd_node_t {p}_nodes[{nn}] = {{")
        out.append(",\n".join(node_inits))
        out.append("};\n")


def write_c_project(ir: dict, outdir: str | Path, name: str = "schema"
                    ) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{name}_schema.c").write_text(generate_c(ir, name),
                                             encoding="utf-8")
    xml = ir.get("format") == "xml"
    for fn in RUNTIME_FILES + (XML_FILES if xml else []):
        shutil.copyfile(RUNTIME_C / fn, outdir / fn)
    srcs = " ".join(c_sources(ir, name))
    flags = " -DWD_XML" if xml else ""
    api = "wx" if xml else "wd"
    (outdir / "Makefile").write_text(
        f"CC ?= gcc\nCFLAGS ?= -std=c11 -Wall -Wextra -Werror -O2{flags}\n\n"
        f"validator: {srcs}\n"
        f"\t$(CC) $(CFLAGS) -o $@ {srcs}\n\n"
        "# Biblioteca estática con la API C-ABI (sin el CLI):\n"
        f"lib{name}.a: {srcs.replace('wdpda_cli.c', '')}\n"
        f"\t$(CC) $(CFLAGS) -c {srcs.replace('wdpda_cli.c', '')}\n"
        f"\tar rcs $@ *.o\n\n"
        f"# WebAssembly (opcional): emcc -O2{flags} -o validator.js "
        f"{srcs.replace('wdpda_cli.c', '')} -s EXPORTED_FUNCTIONS="
        f"_{api}_init,_{api}_feed,_{api}_finish\n", encoding="utf-8")
    return outdir


XML_FILES = ["wdpda_xml.h", "wdpda_xml.c"]


def c_sources(ir: dict, name: str) -> list[str]:
    srcs = ["wdpda_rt.c", "wdpda_cli.c", f"{name}_schema.c"]
    if ir.get("format") == "xml":
        srcs.insert(1, "wdpda_xml.c")
    return srcs


def gcc_env() -> dict:
    """Entorno para gcc; en Windows antepone MSYS2 UCRT64 al PATH.

    (En Git Bash, /mingw64/bin trae DLL que rompen cc1 de MSYS2.)
    """
    env = dict(os.environ)
    if sys.platform == "win32":
        for cand in (r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin"):
            if Path(cand, "gcc.exe").exists():
                env["PATH"] = cand + os.pathsep + env.get("PATH", "")
                break
    return env


def exe_name(path: Path) -> Path:
    return path.with_suffix(".exe") if sys.platform == "win32" else path


def build_c(ir: dict, outdir: str | Path, name: str = "schema",
            extra_flags: list[str] | None = None) -> Path:
    outdir = write_c_project(ir, outdir, name)
    exe = exe_name(outdir / "validator")
    cmd = ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
           *(["-DWD_XML"] if ir.get("format") == "xml" else []),
           *(extra_flags or []), "-o", str(exe),
           *[str(outdir / f) for f in c_sources(ir, name)]]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=gcc_env())
    if proc.returncode != 0:
        raise RuntimeError("gcc falló:\n" + proc.stderr + proc.stdout)
    return exe
