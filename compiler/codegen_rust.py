"""Fase 5 — Generación de código Rust a partir del IR.

Emite ``src/schema.rs`` con las tablas del WDPDA como ``static`` y arma
un crate de Cargo autocontenido junto al runtime
(``runtime/rust/src/wdpda_rt.rs``) y el CLI. Mismas tablas que el
backend C: el IR es la única fuente de verdad.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .codegen_c import ROOT, c_ident

RUNTIME_RS = ROOT / "runtime" / "rust" / "src"
OPS = {"ge": 0, "gt": 1, "le": 2, "lt": 3, "mul": 4}


def rs_str(s: str) -> str:
    out = []
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20:
            out.append(f"\\u{{{ord(ch):x}}}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def rs_bytes(s: str) -> str:
    return "b" + rs_str(s) if s.isascii() else \
        "&[" + ", ".join(str(b) for b in s.encode()) + "]"


def _slice(ty: str, vals) -> str:
    vals = list(vals)
    if not vals:
        return "&[]"
    return "&[" + ", ".join(str(v) for v in vals) + "]"


def generate_rust(ir: dict, name: str = "schema") -> str:
    p = c_ident(name).upper()
    o = [f"//! {name}: tablas del WDPDA — GENERADO por compiler/codegen_rust.py.",
         f"//! Nodos: {len(ir['nodes'])}, AFD: {len(ir['dfas'])}, "
         f"estados de δ: {ir['formal']['states']}, "
         f"transiciones: {ir['formal']['transitions']}.",
         "#![allow(clippy::all)]",
         "use crate::wdpda_rt::{Const, Dfa, Node, Schema};", ""]
    for d in ir["dfas"]:
        i = d["id"]
        o.append(f"static {p}_D{i}_CLS: [u8; 256] = {_slice('u8', d['classes'])[1:]};")
        o.append(f"static {p}_D{i}_TAB: [i32; {len(d['table'])}] = "
                 f"{_slice('i32', d['table'])[1:]};")
        o.append(f"static {p}_D{i}_ACC: [i32; {len(d['accept'])}] = "
                 f"{_slice('i32', d['accept'])[1:]};")
    o.append(f"static {p}_DFAS: [Dfa; {len(ir['dfas'])}] = [")
    for d in ir["dfas"]:
        i = d["id"]
        o.append(f"    Dfa {{ classes: &{p}_D{i}_CLS, ncls: {d['ncls']}, "
                 f"table: &{p}_D{i}_TAB, accept: &{p}_D{i}_ACC }},")
    o.append("];")
    o.append(f"static {p}_CONSTS: [Const; {len(ir['consts'])}] = [")
    for k in ir["consts"]:
        o.append(f"    Const {{ neg: {str(k['neg']).lower()}, digits: "
                 f"{rs_bytes(k['digits'])}, exp: {k['exp']}, lexeme: "
                 f"{rs_str(k['lexeme'])} }},")
    o.append("];")
    o.append(f"static {p}_NODES: [Node; {len(ir['nodes'])}] = [")
    for n in ir["nodes"]:
        e, s, num, ob, a = n["enum"], n["str"], n["num"], n["obj"], n["arr"]
        f = {
            "ptr": rs_str(n["ptr"]), "is_false": str(n["false"]).lower(),
            "types": n["types"],
            "has_enum": str(e is not None).lower(),
            "enum_dfa": e["strings"] if e else -1,
            "enum_nums": _slice("u32", e["nums"] if e else []),
            "enum_lits": e["lits"] if e else 0,
            "has_str": str(s is not None).lower(),
            "min_len": s["min_len"] if s else -1,
            "max_len": s["max_len"] if s else -1,
            "pats": _slice("u32", s["patterns"] if s else []),
            "pat_src": "&[" + ", ".join(rs_str(x) for x in (
                s["pattern_src"] if s else [])) + "]",
            "num_ops": _slice("u8", [OPS[op] for op, _ in num["checks"]]
                              if num else []),
            "num_consts": _slice("u32", [c for _, c in num["checks"]]
                                 if num else []),
            "has_obj": str(ob is not None).lower(),
            "keys_dfa": ob["keys_dfa"] if ob else -1,
            "keys": "&[" + ", ".join(rs_str(k) for k in (
                ob["keys"] if ob else [])) + "]",
            "key_child": _slice("i32", ob["key_child"] if ob else []),
            "key_declared": _slice("u8", [int(x) for x in ob["key_declared"]]
                                   if ob else []),
            "required": f"{ob['required'] if ob else 0}u64",
            "ppat_dfa": _slice("u32", [d for d, _ in ob["patterns"]]
                               if ob else []),
            "ppat_child": _slice("i32", [c for _, c in ob["patterns"]]
                                 if ob else []),
            "additional": ob["additional"] if ob else -1,
            "min_props": ob["min_props"] if ob else -1,
            "max_props": ob["max_props"] if ob else -1,
            "depreq_key": _slice("u8", [k for k, _ in ob["dep_required"]]
                                 if ob else []),
            "depreq_mask": _slice("u64", [f"{m}u64" for _, m in
                                          ob["dep_required"]] if ob else []),
            "depsch_key": _slice("u8", [k for k, _ in ob["dep_schemas"]]
                                 if ob else []),
            "depsch_child": _slice("i32", [c for _, c in ob["dep_schemas"]]
                                   if ob else []),
            "has_arr": str(a is not None).lower(),
            "prefix": _slice("i32", a["prefix"] if a else []),
            "items": a["items"] if a else -1,
            "contains": a["contains"] if a else -3,
            "min_contains": a["min_contains"] if a else -1,
            "max_contains": a["max_contains"] if a else -1,
            "min_items": a["min_items"] if a else -1,
            "max_items": a["max_items"] if a else -1,
            "unique": str(bool(a and a["unique"])).lower(),
            "all_of": _slice("i32", n["all_of"]),
            "any_of": _slice("i32", n["any_of"]),
            "one_of": _slice("i32", n["one_of"]),
            "not_": n["not"], "if_": n["if"], "then_": n["then"],
            "else_": n["else"],
            "not_any": str(bool(n.get("not_any"))).lower(),
        }
        body = ", ".join(f"{k}: {v}" for k, v in f.items())
        o.append(f"    // n{n['id']} {n['ptr']}")
        o.append(f"    Node {{ {body} }},")
    o.append("];")
    o.append(f"pub static SCHEMA: Schema = Schema {{ name: {rs_str(name)}, "
             f"nodes: &{p}_NODES, root: {ir['root']}, dfas: &{p}_DFAS, "
             f"consts: &{p}_CONSTS, uses_unique: "
             f"{str(ir['flags']['uses_unique']).lower()}, formal_states: "
             f"{ir['formal']['states']}, formal_transitions: "
             f"{ir['formal']['transitions']} }};")
    return "\n".join(o) + "\n"


def write_rust_project(ir: dict, outdir: str | Path, name: str = "schema"
                       ) -> Path:
    outdir = Path(outdir)
    src = outdir / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "schema.rs").write_text(generate_rust(ir, name), encoding="utf-8")
    shutil.copyfile(RUNTIME_RS / "wdpda_rt.rs", src / "wdpda_rt.rs")
    shutil.copyfile(RUNTIME_RS / "cli_main.rs", src / "main.rs")
    crate = c_ident(name).lower().replace("_", "-")
    (outdir / "Cargo.toml").write_text(
        f'[package]\nname = "wdpda-{crate}"\nversion = "0.1.0"\n'
        'edition = "2021"\n\n[[bin]]\nname = "validator"\npath = "src/main.rs"\n'
        '\n[profile.release]\nopt-level = 3\nlto = true\ncodegen-units = 1\n'
        'panic = "abort"\n\n[workspace]\n', encoding="utf-8")
    return outdir


def build_rust(ir: dict, outdir: str | Path, name: str = "schema") -> Path:
    outdir = write_rust_project(ir, outdir, name)
    proc = subprocess.run(["cargo", "build", "--release", "--quiet"],
                          cwd=outdir, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("cargo falló:\n" + proc.stderr)
    exe = outdir / "target" / "release" / (
        "validator.exe" if sys.platform == "win32" else "validator")
    return exe
