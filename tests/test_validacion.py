"""Pruebas de las Fases 5–6: validación en streaming (Python, C y Rust),
casos válidos / inválidos / malformados / fuera del DPDA puro, bombeo y
extremos. Las versiones extensas de las pruebas diferenciales están en
``tests/differential*.py`` y ``tests/crosscheck*.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from compiler.engine import validate  # noqa: E402
from compiler.wdpda import compile_schema  # noqa: E402
from compiler.xml_engine import validate_xml  # noqa: E402
from compiler.xsd import XsdError, compile_xsd  # noqa: E402

PERSONA = compile_schema((ROOT / "tests/schemas/persona.json").read_bytes())
# (el patrón de correo de persona.json admite un solo nivel de dominio)
OK = b'{"nombre":"Ana","edad":30,"correo":"ana@uptc.co"}'


def codes(r):
    return [(e.code, e.path) for e in r.errors]


# ------------------------------------------------------------ casos base
def test_valid_and_chunk_equivalence():
    for chunk in (None, 1, 2, 7, 4096):
        r = validate(PERSONA, OK, chunk=chunk)
        assert r.status == "VALID" and r.weight == 0


@pytest.mark.parametrize("payload,expected", [
    (b'{"nombre":"Ana","edad":"30","correo":"a@b.co"}', [("TYPE", "/edad")]),
    (b'{"nombre":"Ana","correo":"a@b.co"}', [("REQUIRED", "")]),
    (b'{"nombre":"Ana","edad":30,"correo":"a@b.co","x":1}',
     [("ADDITIONAL", "/x")]),
    (b'{"nombre":"Ana","edad":200,"correo":"a@b.co"}', [("MAXIMUM", "/edad")]),
    (b'{"nombre":"Ana","edad":30,"correo":"a@b.co","rol":"jefe"}',
     [("ENUM", "/rol")]),
    (b'{"nombre":"","edad":3.5,"correo":"malo"}',
     [("MIN_LENGTH", "/nombre"), ("TYPE", "/edad"), ("PATTERN", "/correo")]),
])
def test_invalid_cases_report_json_pointer(payload, expected):
    r = validate(PERSONA, payload)
    assert r.status == "INVALID"
    assert codes(r) == expected
    assert r.weight == len(expected)  # peso tropical = nº de violaciones


@pytest.mark.parametrize("payload", [
    b'{"nombre":"Ana",}', b'{"a" 1}', b'[1,2', b'{"a":tru}', b'[01]',
    b'"\\ud800"', b'"\xff"', b'{} {}', b'', b'{"a":1}}',
])
def test_malformed_weight_is_infinite(payload):
    r = validate(PERSONA, payload)
    assert r.status in ("MALFORMED", "UTF8")
    assert r.weight == float("inf")


def test_duplicate_declared_key():
    r = validate(PERSONA, b'{"nombre":"A","nombre":"B","edad":1,'
                          b'"correo":"a@b.co"}')
    assert ("DUPLICATE_KEY", "/nombre") in codes(r)


def test_fail_fast_stops_at_first_violation():
    r = validate(PERSONA, b'{"nombre":"","edad":3.5,"correo":"malo"}',
                 fail_fast=True)
    assert r.status == "INVALID" and len(r.errors) == 1


# ------------------------------------------------ fuera del DPDA puro (c)
COMB = compile_schema((ROOT / "tests/schemas/combinadores.json").read_bytes())


@pytest.mark.parametrize("payload,valid", [
    (b'{"id":"abc"}', True), (b'{"id":5}', True), (b'{"id":"ab"}', False),
    (b'{"id":0}', False),
    (b'{"id":1,"forma":{"tipo":"circulo","radio":2}}', True),
    (b'{"id":1,"forma":{"tipo":"rect","ancho":1}}', False),
    (b'{"id":1,"codigo":"abcd"}', True), (b'{"id":1,"codigo":"1234"}', False),
])
def test_combinators_product(payload, valid):
    assert validate(COMB, payload).valid is valid


def test_unique_items_deferred_hash():
    ir = compile_schema(b'{"type":"array","uniqueItems":true}')
    assert validate(ir, b'[1,"1",{"a":1,"b":2},[1]]').valid
    assert not validate(ir, b'[{"a":1,"b":2},{"b":2,"a":1}]').valid  # orden
    assert not validate(ir, b'[1, 1.0]').valid  # igualdad numérica JSON
    assert validate(ir, b'[1, true]').valid      # 1 != true


# ------------------------------------------------------ bombeo y extremos
def test_pumping_linear_time_and_stack():
    """u·v^k·x·y^k·z con v='{"a":', y='}': pila = k, tiempo O(n)."""
    ir = compile_schema(b"{}")
    times = []
    for k in (50, 100, 200):
        payload = b'{"a":' * k + b"1" + b"}" * k
        t0 = time.perf_counter()
        r = validate(ir, payload)
        times.append(time.perf_counter() - t0)
        assert r.valid and r.max_depth == k
    assert times[2] < times[0] * 12  # ~lineal (tolerancia generosa)
    deep = b"[" * 300 + b"]" * 300
    assert validate(ir, deep).status == "DEPTH"  # límite 256 por defecto
    assert validate(ir, deep, max_depth=400).valid


def test_huge_numbers_exact():
    ir = compile_schema(b'{"type":"integer","maximum":1e30,"minimum":-1e30}')
    assert validate(ir, b"999999999999999999999999999999").valid
    assert not validate(ir, b"1000000000000000000000000000001").valid
    assert validate(ir, b"1e30").valid and not validate(ir, b"1.5").valid


def test_limits_size_and_time():
    ir = compile_schema(b"{}")
    assert validate(ir, b"[" + b"1," * 1000 + b"1]",
                    max_bytes=100).status == "SIZE"


# ------------------------------------------------------------- XML / XSD
XSD = compile_xsd((ROOT / "tests/schemas_xsd/pedido.xsd").read_bytes())
XML_OK = (b'<pedido><id>P1</id><cliente><nombre>Ana</nombre><correo>a@b.co'
          b'</correo></cliente><item linea="1"><sku>ABC-1234</sku><cantidad>2'
          b'</cantidad><precio>10.5</precio></item><estado>pagado</estado>'
          b'</pedido>')


def test_xml_valid_and_chunks():
    for chunk in (None, 1, 3):
        assert validate_xml(XSD, XML_OK, chunk=chunk).valid


@pytest.mark.parametrize("doc,status", [
    (b"<pedido><id>1</id></pedid>", "MALFORMED"),
    (b"<!DOCTYPE p [<!ENTITY x 'y'>]><pedido/>", "MALFORMED"),  # XXE
    (b"<pedido>&x;</pedido>", "MALFORMED"),  # expansión de entidades
    (b"<pedido><id>1</id>", "MALFORMED"),  # etiqueta sin cerrar
    (b"<pedido/><pedido/>", "MALFORMED"),
])
def test_xml_malformed(doc, status):
    assert validate_xml(XSD, doc).status == status


def test_xml_upa_violation_rejected():
    bad = (b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
           b'<xs:element name="r"><xs:complexType><xs:choice>'
           b'<xs:sequence><xs:element name="a" type="xs:string"/>'
           b'<xs:element name="b" type="xs:string"/></xs:sequence>'
           b'<xs:sequence><xs:element name="a" type="xs:string"/>'
           b'<xs:element name="c" type="xs:string"/></xs:sequence>'
           b'</xs:choice></xs:complexType></xs:element></xs:schema>')
    with pytest.raises(XsdError, match="Unique Particle Attribution"):
        compile_xsd(bad)


# -------------------------------------------- diferenciales (versión corta)
def test_differential_json_vs_jsonschema():
    pytest.importorskip("jsonschema")
    import differential
    rep = differential.run(40, verbose=False)
    assert sum(r["diffs"] for r in rep.values()) == 0


def test_differential_xml_vs_lxml():
    pytest.importorskip("lxml")
    import differential_xml
    rep = differential_xml.run(40, verbose=False)
    assert sum(r["diffs"] for r in rep.values()) == 0


# ------------------------------------------------------ backends nativos
def _has_gcc():
    from compiler.codegen_c import gcc_env
    return shutil.which("gcc", path=gcc_env()["PATH"]) is not None


@pytest.mark.skipif(not _has_gcc(), reason="gcc no disponible")
def test_c_runtime_has_no_malloc():
    for f in ("wdpda_rt.c", "wdpda_xml.c"):
        src = (ROOT / "runtime/c" / f).read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith(("/*", "*")))
        assert "malloc(" not in code and "free(" not in code


@pytest.mark.skipif(not _has_gcc(), reason="gcc no disponible")
def test_crosscheck_c(capsys):
    import crosscheck
    assert crosscheck.main(15) == 0


@pytest.mark.skipif(not _has_gcc(), reason="gcc no disponible")
def test_crosscheck_xml_c():
    import crosscheck_xml
    assert crosscheck_xml.main(20) == 0


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo no disponible")
def test_rust_backend_matches_python(tmp_path):
    from compiler.codegen_rust import build_rust
    exe = build_rust(PERSONA, ROOT / "build" / "pytest_rust", "persona")
    for payload in (OK, b'{"nombre":"","edad":3.5,"correo":"malo"}',
                    b'{"a":'):
        p = tmp_path / "p.json"
        p.write_bytes(payload)
        out = json.loads(subprocess.run([str(exe), str(p)],
                                        capture_output=True).stdout)
        py = validate(PERSONA, payload)
        assert out["status"] == py.status
        assert [(e["code"], e["path"]) for e in out["errors"]] == codes(py)
