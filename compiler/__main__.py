"""CLI del compilador WDPDA.

Uso (desde la raíz del repo):

  python -m compiler compile  ESQUEMA [-o DIR] [--lang c,rust] [--name N]
  python -m compiler validate ESQUEMA PAYLOAD [--engine py|c|rust]
                              [--chunk N] [--fail-fast] [--trace]
  python -m compiler ir       ESQUEMA [-o ir.json]
  python -m compiler diagrams ESQUEMA [-o DIR]
  python -m compiler lexer-diagrams [-o DIR]
  python -m compiler grammar  [json|xml|schema|regex]
  python -m compiler tree     PAYLOAD            (árbol de derivación)
  python -m compiler gen      ESQUEMA [-n N] [--invalid] [--seed S]

ESQUEMA puede ser JSON Schema (.json) o XSD (.xsd).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_ir(path: str) -> dict:
    data = Path(path).read_bytes()
    if path.lower().endswith(".xsd"):
        from .xsd import compile_xsd
        return compile_xsd(data)
    from .wdpda import compile_schema
    return compile_schema(data)


def cmd_compile(a) -> int:
    ir = load_ir(a.schema)
    name = a.name or Path(a.schema).stem
    out = Path(a.out or f"build/{name}")
    langs = a.lang.split(",")
    (out).mkdir(parents=True, exist_ok=True)
    (out / f"{name}.ir.json").write_text(json.dumps(ir, ensure_ascii=False,
                                                    indent=1), encoding="utf-8")
    print(f"IR: {out / (name + '.ir.json')}")
    if "c" in langs:
        from .codegen_c import build_c
        exe = build_c(ir, out / "c", name)
        print(f"C:    {exe}")
    if "rust" in langs:
        if ir.get("format") == "xml":
            print("Rust: el backend Rust cubre JSON; XML se genera en C")
        else:
            from .codegen_rust import build_rust
            print(f"Rust: {build_rust(ir, out / 'rust', name)}")
    _summary(ir)
    return 0


def _summary(ir: dict) -> None:
    st, fo = ir["stats"], ir["formal"]
    print(f"Formato {ir.get('format', 'json')}: nodos {st.get('nodes_after_minimization')}"
          f", AFD {st['dfa_count']} ({st['dfa_states']} estados; "
          f"{st['dfa_states_before_min']} antes de minimizar), δ: "
          f"{fo['states']} estados / {fo['transitions']} transiciones, "
          f"determinista={fo['deterministic']}")
    for c in ir["classification"]:
        print(f"  {c['keyword']:22} {c['category']:42} {c.get('strategy', '')}")
    for w in ir.get("warnings", []):
        print("  aviso:", w)


def cmd_validate(a) -> int:
    ir = load_ir(a.schema)
    data = Path(a.payload).read_bytes()
    if a.engine == "py":
        if ir.get("format") == "xml":
            from .xml_engine import XmlValidator
            v = XmlValidator(ir, fail_fast=a.fail_fast, trace=a.trace)
        else:
            from .engine import Validator
            v = Validator(ir, fail_fast=a.fail_fast, trace=a.trace)
        r = v.validate(data, a.chunk)
        out = r.to_dict()
        if a.trace:
            out["trace"] = v.trace
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if r.valid else (2 if r.status == "INVALID" else 3)
    name = Path(a.schema).stem
    if a.engine == "c":
        from .codegen_c import build_c
        exe = build_c(ir, Path("build") / name / "c", name)
    else:
        from .codegen_rust import build_rust
        exe = build_rust(ir, Path("build") / name / "rust", name)
    cmd = [str(exe), a.payload] + (["--chunk", str(a.chunk)] if a.chunk else [])
    if a.fail_fast:
        cmd.append("--fail-fast")
    return subprocess.run(cmd).returncode


def cmd_ir(a) -> int:
    ir = load_ir(a.schema)
    txt = json.dumps(ir, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    else:
        print(txt)
    return 0


def cmd_diagrams(a) -> int:
    from . import export
    from .formal import build_formal
    ir = load_ir(a.schema)
    name = Path(a.schema).stem
    out = Path(a.out or f"docs/diagramas/{name}")
    out.mkdir(parents=True, exist_ok=True)
    written = []
    if ir.get("format") != "xml":
        f = build_formal(ir)
        auto = export.formal_auto(f, f"WDPDA de {name}")
        full = export.formal_auto(f, f"WDPDA de {name} (con pesos)",
                                  drop_weighted=False)
        view = export.formal_auto(f, f"WDPDA de {name} (⊤ colapsado)",
                                  collapse_top=True)
        view_w = export.formal_auto(f, f"WDPDA de {name} con pesos "
                                    "(rojo = violación, w=1)",
                                    drop_weighted=False, collapse_top=True)
        written.append(export.to_jff(auto, out / f"{name}_wdpda.jff"))
        (out / f"{name}_wdpda.dot").write_text(export.to_dot(full),
                                               encoding="utf-8")
        (out / f"{name}_wdpda.mmd").write_text(export.to_mermaid(view),
                                               encoding="utf-8")
        written.append(export.to_svg(view, out / f"{name}_wdpda.svg"))
        written.append(export.to_svg(view_w, out / f"{name}_wdpda_pesos.svg"))
        (out / f"{name}_delta.txt").write_text(
            "\n".join(f"{t.src:14} --{t.label()}--> {t.dst}" for t in f.trans),
            encoding="utf-8")
        (out / "LEYENDA_JFLAP.txt").write_text(export.jflap_legend(),
                                               encoding="utf-8")
    for d in ir["dfas"]:
        syms = d.get("symbols")
        title = f"AFD {d['id']} ({d['kind']}): {str(d['source'])[:50]}"
        au = export.dfa_auto(d, title, syms)
        base = out / f"{name}_dfa{d['id']}_{d['kind']}"
        if d["states"] <= 60:
            written.append(export.to_jff(au, base.with_suffix(".jff")))
            written.append(export.to_svg(au, base.with_suffix(".svg")))
        base.with_suffix(".dot").write_text(export.to_dot(au), encoding="utf-8")
    for p in written:
        print(p)
    return 0


def cmd_lexer_diagrams(a) -> int:
    from . import export
    from .formal import build_formal
    from .wdpda import compile_schema
    out = Path(a.out or "docs/diagramas/lexico")
    out.mkdir(parents=True, exist_ok=True)
    autos = {"numero_json": export.lexer_number_dfa(),
             "cadena_json": export.lexer_string_dfa(),
             "literales_json": export.lexer_literal_dfa(),
             "utf8": export.utf8_dfa()}
    f = build_formal(compile_schema(b"{}"))
    autos["json_dpda"] = export.formal_auto(f, "DPDA sintáctico de JSON",
                                            only_top=True)
    for k, au in autos.items():
        print(export.to_jff(au, out / f"{k}.jff"))
        print(export.to_svg(au, out / f"{k}.svg"))
        (out / f"{k}.dot").write_text(export.to_dot(au), encoding="utf-8")
        (out / f"{k}.mmd").write_text(export.to_mermaid(au), encoding="utf-8")
    (out / "LEYENDA_JFLAP.txt").write_text(export.jflap_legend(),
                                           encoding="utf-8")
    return 0


def cmd_grammar(a) -> int:
    from .grammars import (JSON_GRAMMAR, REGEX_GRAMMAR, SCHEMA_GRAMMAR,
                           XML_GRAMMAR)
    g = {"json": JSON_GRAMMAR, "xml": XML_GRAMMAR, "schema": SCHEMA_GRAMMAR,
         "regex": REGEX_GRAMMAR}[a.which]
    print(g.to_markdown())
    if a.which != "schema":
        print()
        print(g.table_markdown())
    return 0


def cmd_tree(a) -> int:
    from .export import tree_to_dot
    from .grammar import LL1Parser
    data = Path(a.payload).read_bytes()
    if a.payload.endswith(".xml"):
        from .grammars import XML_GRAMMAR
        from .xsd import xml_tokens_for_parse
        tree = LL1Parser(XML_GRAMMAR).parse(xml_tokens_for_parse(data))
    else:
        from .grammars import JSON_GRAMMAR
        from .json_lexer import JsonTokenizer
        tree = LL1Parser(JSON_GRAMMAR).parse(JsonTokenizer().tokenize(data))
    print(tree_to_dot(tree) if a.dot else tree.to_text())
    return 0


def cmd_gen(a) -> int:
    ir = load_ir(a.schema)
    for i in range(a.n):
        seed = a.seed + i
        if ir.get("format") == "xml":
            from .xml_gen import gen_xml, mutate_xml
            x = gen_xml(ir, seed)
            print(mutate_xml(x, seed) if a.invalid else x)
        else:
            from .payload_gen import gen_valid, mutate, to_json
            v = gen_valid(ir, seed)
            print(to_json(mutate(ir, v, seed)[0] if a.invalid else v))
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m compiler",
                                 description="Compilador esquema -> WDPDA")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compile")
    p.add_argument("schema")
    p.add_argument("-o", "--out")
    p.add_argument("--lang", default="c")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_compile)
    p = sub.add_parser("validate")
    p.add_argument("schema")
    p.add_argument("payload")
    p.add_argument("--engine", choices=["py", "c", "rust"], default="py")
    p.add_argument("--chunk", type=int)
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--trace", action="store_true")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("ir")
    p.add_argument("schema")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_ir)
    p = sub.add_parser("diagrams")
    p.add_argument("schema")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_diagrams)
    p = sub.add_parser("lexer-diagrams")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_lexer_diagrams)
    p = sub.add_parser("grammar")
    p.add_argument("which", nargs="?", default="json",
                   choices=["json", "xml", "schema", "regex"])
    p.set_defaults(fn=cmd_grammar)
    p = sub.add_parser("tree")
    p.add_argument("payload")
    p.add_argument("--dot", action="store_true")
    p.set_defaults(fn=cmd_tree)
    p = sub.add_parser("gen")
    p.add_argument("schema")
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--invalid", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=cmd_gen)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
