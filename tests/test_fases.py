"""Pruebas de las Fases 2–4: gramáticas LL(1), autómatas finitos,
aritmética decimal, análisis semántico del esquema y δ del WDPDA.

Ejecutar: ``python -m pytest tests -q``
"""

from __future__ import annotations

import random
import re
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal, getcontext
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler import export  # noqa: E402
from compiler.automata import RegexError, build_trie, compile_regex  # noqa
from compiler.formal import build_formal, check_determinism  # noqa: E402
from compiler.grammar import LL1Parser, ParseError  # noqa: E402
from compiler.grammars import (ALL_GRAMMARS, JSON_GRAMMAR,  # noqa: E402
                               XML_GRAMMAR)
from compiler.json_lexer import JsonTokenizer  # noqa: E402
from compiler.numbers import compare, multiple_of, parse_number  # noqa
from compiler.schema import SchemaError, analyze  # noqa: E402
from compiler.wdpda import compile_schema  # noqa: E402

SCHEMAS = sorted((ROOT / "tests" / "schemas").glob("*.json"))


# ---------------------------------------------------------------- Fase 2
@pytest.mark.parametrize("g", ALL_GRAMMARS, ids=lambda g: g.name)
def test_grammars_are_ll1(g):
    table, conflicts = g.ll1_table()
    assert conflicts == []
    assert table


def test_json_first_follow():
    first = JSON_GRAMMAR.first_sets()
    follow = JSON_GRAMMAR.follow_sets(first)
    assert first["Value"] == {"{", "[", "STRING", "NUMBER", "TRUE", "FALSE",
                              "NULL"}
    assert "ε" in first["Members"]
    assert follow["Members"] == {"}"}
    assert follow["Value"] == {"$", ",", "}", "]"}


def test_derivation_tree_and_errors():
    p = LL1Parser(JSON_GRAMMAR)
    tree = p.parse(JsonTokenizer().tokenize(b'{"a":[1,true]}'))
    leaves = [n.symbol for n in tree.leaves()]
    assert leaves == ["{", "STRING", ":", "[", "NUMBER", ",", "TRUE", "]",
                      "}"]
    for bad in (b'{"a":}', b"[1,]", b'{"a" 1}', b"[1 2]"):
        with pytest.raises(ParseError):
            p.parse(JsonTokenizer().tokenize(bad))


def test_xml_grammar_parses_document():
    from compiler.xsd import xml_tokens_for_parse
    tree = LL1Parser(XML_GRAMMAR).parse(
        xml_tokens_for_parse(b"<?xml version='1.0'?><a><b>x</b><!--c--></a>"))
    assert tree.symbol == "Document"


# ------------------------------------------------------- autómatas finitos
PATTERNS = [r"^[a-z]+$", r"ab*c", r"^(a|b)*abb$", r"^\d{3}-\d{4}$",
            r"colou?r", r"^[^@]+@[^@]+\.[a-z]{2,6}$", r"^.$", r"x|y|z",
            r"^(ab){2,3}$", r"[0-9a-fA-F]+", r"^\w+\s\w+$", r"^ñ+é?$",
            r"(?:foo|bar)baz", r"^(a|b)*a(a|b){3}$"]


@pytest.mark.parametrize("pat", PATTERNS)
def test_regex_dfa_matches_python_re(pat):
    d = compile_regex(pat)
    oracle = re.compile(pat[:-1] + r"\Z" if pat.endswith("$") else pat,
                        re.ASCII)
    rnd = random.Random(7)
    alpha = "abcxyz0129-@. ñéAZ_\n"
    for _ in range(1500):
        s = "".join(rnd.choice(alpha) for _ in range(rnd.randint(0, 9)))
        assert d.accepts(s.encode()) == (oracle.search(s) is not None), s


def test_minimization_and_explosion():
    d = compile_regex(r"^(a|b)*abb$")
    assert d.n == 4  # el AFD mínimo clásico del libro (Aho et al.)
    with pytest.raises(RegexError):
        compile_regex(r"^(a|b)*a(a|b){14}$")  # 2^15 estados: tope
    with pytest.raises(RegexError):
        compile_regex(r"(a)\1")  # retrorreferencia: no regular


def test_trie_tags():
    t = build_trie([b"edad", b"nombre", b"edades"])
    assert [t.run(w) for w in (b"edad", b"nombre", b"edades", b"eda")] == \
        [0, 1, 2, -1]


# ------------------------------------------------------- decimales exactos
def test_decimal_compare_and_multiple():
    getcontext().prec = 500
    rnd = random.Random(3)

    def num():
        s = rnd.choice(["", "-"]) + rnd.choice(
            ["0", str(rnd.randint(1, 10 ** rnd.randint(1, 30)))])
        if rnd.random() < .5:
            s += "." + str(rnd.randint(0, 10 ** 5)).zfill(rnd.randint(1, 5))
        if rnd.random() < .4:
            s += "e" + rnd.choice(["", "+", "-"]) + str(rnd.randint(0, 40))
        return s

    for _ in range(4000):
        a, b = num(), num()
        A, B = Decimal(a), Decimal(b)
        assert compare(parse_number(a), parse_number(b)) == (A > B) - (A < B)
        m = rnd.choice(["2", "0.5", "0.01", "7", "1e2"])
        assert multiple_of(parse_number(a), parse_number(m)) == \
            (A % Decimal(m) == 0)
    assert compare(parse_number("1e400"), parse_number("1e399")) == 1
    assert parse_number("1.0").is_integer() and parse_number("1e3").is_integer()


# ------------------------------------------------------------- Fase 3
@pytest.mark.parametrize("schema,msg", [
    (b'{"$defs":{"a":{"$ref":"#/$defs/b"},"b":{"allOf":[{"$ref":"#/$defs/a"}]}},'
     b'"$ref":"#/$defs/a"}', "ciclo no productivo"),
    (b'{"minimum":"x"}', "mal formado"),
    (b'{"type":"entero"}', "tipo desconocido"),
    (b'{"$ref":"#/nada"}', "sin destino"),
    (b'{"$ref":"http://x/y.json"}', "externo"),
    (b'{"type":"string","type":"integer"}', "duplicada"),
    (b'{"enum":[{"a":1}]}', "solo escalares"),
    (b'{"propertyNames":{"maxLength":3}}', "propertyNames"),
])
def test_semantic_errors(schema, msg):
    with pytest.raises(SchemaError, match=msg):
        analyze(schema)


def test_recursive_ref_is_productive():
    a = analyze((ROOT / "tests/schemas/arbol.json").read_bytes())
    hijos = a.root.properties["hijos"]
    assert hijos.items is a.root  # la recursión pasa por la pila


def test_classification_categories():
    ir = compile_schema((ROOT / "tests/schemas/combinadores.json").read_bytes())
    cats = {c["keyword"]: c["category"][0] for c in ir["classification"]}
    assert cats["type"] == "a" and cats["required"] == "b"
    assert cats["anyOf"] == "c" and cats["oneOf"] == "c"


# ------------------------------------------------------------- Fase 4
@pytest.mark.parametrize("f", SCHEMAS, ids=lambda p: p.name)
def test_delta_is_deterministic(f):
    ir = compile_schema(f.read_bytes())
    assert ir["formal"]["deterministic"], ir["formal"]["determinism_conflicts"]
    assert check_determinism(build_formal(ir))["deterministic"]


def test_node_minimization_merges_equivalent_subschemas():
    s = (b'{"type":"object","properties":{"a":{"type":"integer","minimum":0},'
         b'"b":{"type":"integer","minimum":0},"c":{"type":"integer",'
         b'"minimum":0}}}')
    ir = compile_schema(s)
    assert ir["stats"]["reachable_nodes"] == 4
    assert ir["stats"]["nodes_after_minimization"] == 2


# ----------------------------------- el .jff exportado es ejecutable
def _simulate_pda(path: Path, word: str) -> bool:
    """Simulador determinista de un PDA de JFLAP (aceptación por estado)."""
    root = ET.parse(path).getroot().find("automaton")
    states = {s.get("id"): s for s in root.findall("state")}
    init = next(i for i, s in states.items() if s.find("initial") is not None)
    finals = {i for i, s in states.items() if s.find("final") is not None}
    trans = []
    for t in root.findall("transition"):
        trans.append((t.findtext("from"), t.findtext("read") or "",
                      t.findtext("pop") or "", t.findtext("push") or "",
                      t.findtext("to")))
    q, stack, i = init, ["Z"], 0
    for _ in range(10000):
        moved = False
        for src, rd, pop, push, dst in trans:
            if src != q:
                continue
            if rd and (i >= len(word) or word[i] != rd):
                continue
            if pop and (not stack or stack[-1] != pop):
                continue
            if pop:
                stack.pop()
            stack.extend(reversed(push))
            i += len(rd)
            q = dst
            moved = True
            break
        if not moved:
            break
    return i == len(word) and q in finals


def test_jflap_json_dpda_is_executable(tmp_path):
    f = build_formal(compile_schema(b"{}"))
    a = export.formal_auto(f, "json", only_top=True)
    p = export.to_jff(a, tmp_path / "json.jff")
    for w in ["{k:[i,s,{}],k:u}", "[]", "{}", "s", "[[[i]]]", "{k:{k:{k:t}}}"]:
        assert _simulate_pda(p, w), w
    for w in ["{k:}", "[i,]", "{s}", "[i", "]", "i i", "{k:i,}"]:
        assert not _simulate_pda(p, w), w


def test_jflap_lexer_number_dfa(tmp_path):
    a = export.lexer_number_dfa()
    p = export.to_jff(a, tmp_path / "num.jff")
    root = ET.parse(p).getroot().find("automaton")
    ids = {s.get("id"): s.get("name") for s in root.findall("state")}
    tr = {}
    for t in root.findall("transition"):
        tr[(ids[t.findtext("from")], t.findtext("read"))] = ids[t.findtext("to")]

    def run(w):
        q = "inicio"
        for ch in w:
            q = tr.get((q, ch))
            if q is None:
                return False
        return q in a.finals

    for w in ["0", "-0", "12", "1.5", "1e9", "-2.5E-3"]:
        assert run(w), w
    for w in ["01", "1.", ".5", "1e", "-", "+1", "1e+"]:
        assert not run(w), w


def test_jflap_recursive_push_order(tmp_path):
    """Arreglo de objetos: apila dos símbolos y JFLAP espera el tope primero."""
    s = (b'{"$defs":{"n":{"type":"object","properties":{"v":{"type":"integer"},'
         b'"h":{"type":"array","items":{"$ref":"#/$defs/n"}}}}},'
         b'"$ref":"#/$defs/n"}')
    f = build_formal(compile_schema(s))
    p = export.to_jff(export.formal_auto(f, "arbol"), tmp_path / "a.jff")
    for w in ["{A:i}", "{A:i,B:[{A:i},{A:i,B:[{A:i}]}]}"]:
        assert _simulate_pda(p, w), w
    assert not _simulate_pda(p, "{A:i,B:[{A:i}]")


def test_jflap_pattern_ranges_are_expanded(tmp_path):
    """JFLAP lee «0-9» como tres símbolos: el .jff lleva un carácter por arista."""
    d = compile_regex(r"^[A-Z]{3}-[0-9]{4}$")
    classes, ncls, table = d.dense()
    au = export.dfa_auto({"states": d.n, "classes": classes, "ncls": ncls,
                          "table": table, "accept": d.tag}, "sku")
    p = export.to_jff(au, tmp_path / "sku.jff")
    root = ET.parse(p).getroot().find("automaton")
    reads = {t.findtext("read") for t in root.findall("transition")}
    assert all(len(r) == 1 for r in reads)
    assert {"A", "Z", "0", "9", "-"} <= reads
