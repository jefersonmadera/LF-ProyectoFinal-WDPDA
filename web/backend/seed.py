"""Siembra la base NoSQL con el corpus de casos de prueba del enunciado.

Categorías (sección 4 del enunciado):
  validos      esquemas simples, anidados y recursivos, en JSON y XML
  invalidos    tipos, requeridos faltantes, adicionales, enum, rangos
  malformados  sintaxis, UTF-8 inválido, claves duplicadas, XML sin cerrar
  fuera_dpda   anyOf/oneOf ambiguos, pattern, uniqueItems (estrategia c)
  bombeo       u·v^k·x·y^k·z con k creciente y arreglos muy largos
  extremos     payload grande, profundidad sobre el límite, números
               enormes, expansión de entidades XML

El resultado esperado se calcula con el motor WDPDA y, cuando hay
oráculo (jsonschema / lxml), se guarda también su veredicto.

Uso:  python -m web.backend.seed            (MONGODB_URI opcional)
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from compiler.engine import validate  # noqa: E402
from compiler.payload_gen import gen_valid, mutate, to_json  # noqa: E402
from compiler.wdpda import compile_schema  # noqa: E402
from compiler.xml_engine import validate_xml  # noqa: E402
from compiler.xml_gen import gen_xml, mutate_xml  # noqa: E402
from compiler.xsd import compile_xsd  # noqa: E402

from .store import open_store  # noqa: E402

S = ROOT / "tests" / "schemas"
X = ROOT / "tests" / "schemas_xsd"


def oracle_json(schema: str, payload: bytes):
    try:
        import jsonschema
        doc = json.loads(payload)
    except Exception:
        return "MALFORMED"
    return "VALID" if jsonschema.Draft202012Validator(
        json.loads(schema)).is_valid(doc) else "INVALID"


def oracle_xml(xsd_path: Path, payload: bytes):
    try:
        from lxml import etree
        x = etree.XMLSchema(etree.parse(str(xsd_path)))
        doc = etree.fromstring(payload)
    except Exception:
        return "MALFORMED"
    return "VALID" if x.validate(doc) else "INVALID"


def case(cid, cat, fmt, nombre, schema_text, payload: bytes, oracle=None):
    if fmt == "json":
        r = validate(compile_schema(schema_text.encode()), payload)
    else:
        r = validate_xml(compile_xsd(schema_text.encode()), payload)
    doc = {"_id": cid, "categoria": cat, "formato": fmt, "nombre": nombre,
           "esquema": schema_text,
           "esperado": {"status": r.status,
                        "peso": None if r.weight == float("inf") else r.weight,
                        "errores": [e.to_dict() for e in r.errors][:5]},
           "oraculo": oracle}
    try:
        doc["payload"] = payload.decode("utf-8")
    except UnicodeDecodeError:
        doc["payload"] = payload.decode("utf-8", "replace")
        doc["payload_b64"] = base64.b64encode(payload).decode()
    if len(payload) > 200_000:  # documentos grandes: se regeneran al vuelo
        doc["payload"] = payload.decode("utf-8")
    return doc


def build_cases() -> list[dict]:
    cs = []
    # ---- válidos (JSON simple, anidado, recursivo) y XML
    for name in ("persona", "pedido", "config", "arbol", "evento",
                 "numeros", "patrones"):
        t = (S / f"{name}.json").read_text(encoding="utf-8")
        ir = compile_schema(t.encode())
        n = 0
        for seed in range(50):
            p = to_json(gen_valid(ir, seed)).encode()
            if validate(ir, p).valid:
                cs.append(case(f"{name}-valido-{n}", "validos", "json",
                               f"{name}: payload generado válido", t, p,
                               oracle_json(t, p)))
                n += 1
            if n == 2:
                break
    for name in ("pedido", "catalogo"):
        t = (X / f"{name}.xsd").read_text(encoding="utf-8")
        ir = compile_xsd(t.encode())
        for k in range(2):
            p = gen_xml(ir, k).encode()
            cs.append(case(f"{name}-xml-valido-{k}", "validos", "xml",
                           f"{name}.xsd: instancia generada", t, p,
                           oracle_xml(X / f"{name}.xsd", p)))
    # ---- inválidos (cada regla)
    per = (S / "persona.json").read_text(encoding="utf-8")
    for cid, nombre, p in [
        ("tipo", "tipo incorrecto (edad string)",
         b'{"nombre":"Ana","edad":"30","correo":"a@b.co"}'),
        ("requerido", "campo obligatorio faltante (edad)",
         b'{"nombre":"Ana","correo":"a@b.co"}'),
        ("adicional", "propiedad adicional",
         b'{"nombre":"Ana","edad":30,"correo":"a@b.co","x":1}'),
        ("enum", "violación de enum", b'{"nombre":"Ana","edad":30,'
                                       b'"correo":"a@b.co","rol":"jefe"}'),
        ("rango", "violación de rango (edad 200)",
         b'{"nombre":"Ana","edad":200,"correo":"a@b.co"}'),
        ("varias", "tres violaciones (peso 3)",
         b'{"nombre":"","edad":3.5,"correo":"malo"}')]:
        cs.append(case(f"persona-invalido-{cid}", "invalidos", "json", nombre,
                       per, p, oracle_json(per, p)))
    ped = (X / "pedido.xsd").read_text(encoding="utf-8")
    p = (b'<pedido><id>P1</id><cliente><nombre>A</nombre><telefono>1'
         b'</telefono></cliente><item><sku>abc</sku><cantidad>0</cantidad>'
         b'<precio>x</precio></item><estado>otro</estado></pedido>')
    cs.append(case("pedido-xml-invalido", "invalidos", "xml",
                   "XSD: longitud, atributo requerido, patrón, rango, tipo, "
                   "enumeración", ped, p, oracle_xml(X / "pedido.xsd", p)))
    # ---- malformados
    for cid, nombre, p in [
        ("coma", "coma final", b'{"nombre":"Ana",}'),
        ("sin-cerrar", "objeto sin cerrar", b'{"nombre":"Ana"'),
        ("utf8", "UTF-8 inválido", b'{"nombre":"\xff\xfe"}'),
        ("sobrelarga", "UTF-8 sobrelargo", b'{"nombre":"\xc0\xaf"}'),
        ("duplicada", "clave duplicada",
         b'{"nombre":"A","nombre":"B","edad":1,"correo":"a@b.co"}'),
        ("literal", "literal inválido", b'{"activo":tru}'),
        ("cero", "cero inicial", b'{"edad":01}')]:
        cs.append(case(f"malformado-{cid}", "malformados", "json", nombre,
                       per, p, oracle_json(per, p)))
    for cid, nombre, p in [
        ("xml-sin-cerrar", "etiqueta XML sin cerrar", b"<pedido><id>1</id>"),
        ("xml-cruce", "etiquetas cruzadas", b"<pedido><id>1</pedido></id>")]:
        cs.append(case(f"malformado-{cid}", "malformados", "xml", nombre,
                       ped, p, oracle_xml(X / "pedido.xsd", p)))
    # ---- fuera del DPDA puro (categoría c)
    comb = (S / "combinadores.json").read_text(encoding="utf-8")
    for cid, nombre, p in [
        ("anyof-ok", "anyOf: rama entera", b'{"id":5}'),
        ("anyof-falla", "anyOf: ninguna rama (peso = mín. tropical)",
         b'{"id":"ab"}'),
        ("oneof-ambiguo", "oneOf: una sola rama válida",
         b'{"id":1,"forma":{"tipo":"circulo","radio":2}}'),
        ("not", "not + allOf", b'{"id":1,"codigo":"1234"}')]:
        cs.append(case(f"fuera-{cid}", "fuera_dpda", "json", nombre, comb, p,
                       oracle_json(comb, p)))
    uq = '{"type":"array","uniqueItems":true}'
    for cid, p in [("unique-ok", b'[1,"1",{"a":1},[1]]'),
                   ("unique-falla", b'[{"a":1,"b":2},{"b":2,"a":1}]')]:
        cs.append(case(f"fuera-{cid}", "fuera_dpda", "json",
                       "uniqueItems: hash canónico (no libre de contexto)",
                       uq, p, oracle_json(uq, p)))
    pat = '{"type":"string","pattern":"^(a|b)*abb$"}'
    for cid, p in [("pattern-ok", b'"aababb"'), ("pattern-falla", b'"abab"')]:
        cs.append(case(f"fuera-{cid}", "fuera_dpda", "json",
                       "pattern: regex -> AFD mínimo (sin ReDoS)", pat, p,
                       oracle_json(pat, p)))
    # ---- bombeo u·v^k·x·y^k·z
    anyj = "{}"
    for k in (10, 100, 1000):
        p = b'{"a":' * k + b"1" + b"}" * k
        cs.append(case(f"bombeo-objetos-k{k}", "bombeo", "json",
                       f"u·v^k·x·y^k·z con v='{{\"a\":', y='}}', k={k}",
                       anyj, p, None))
    for k in (10, 200):
        p = b"[" * k + b"]" * k
        cs.append(case(f"bombeo-arreglos-k{k}", "bombeo", "json",
                       f"v='[', y=']', k={k}", anyj, p, None))
    arr = '{"type":"array","items":{"type":"integer"}}'
    p = ("[" + ",".join(str(i) for i in range(20000)) + "]").encode()
    cs.append(case("bombeo-arreglo-largo", "bombeo", "json",
                   "arreglo de 20 000 enteros", arr, p, oracle_json(arr, p)))
    # ---- extremos
    p = b"[" * 300 + b"]" * 300
    cs.append(case("extremo-profundidad", "extremos", "json",
                   "profundidad 300 sobre el límite 256", anyj, p, None))
    big = '{"type":"integer","maximum":1e30}'
    for cid, p in [("numero-enorme-ok", b"999999999999999999999999999999"),
                   ("numero-enorme-falla",
                    b"1000000000000000000000000000001"),
                   ("numero-1e400", b"1e400")]:
        cs.append(case(f"extremo-{cid}", "extremos", "json",
                       "comparación decimal exacta (sin float)", big, p,
                       oracle_json(big, p)))
    lol = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
           b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]><pedido>&lol2;'
           b'</pedido>')
    cs.append(case("extremo-billion-laughs", "extremos", "xml",
                   "expansión de entidades XML (rechazada: sin DTD)", ped, lol,
                   "MALFORMED"))
    ev = (ROOT / "bench" / "schemas" / "eventos.json").read_text(
        encoding="utf-8")
    sys.path.insert(0, str(ROOT / "bench"))
    from bench import make_payload
    p = make_payload(1024)
    cs.append(case("extremo-payload-1MB", "extremos", "json",
                   "payload de 1 MB en streaming", ev, p, None))
    return cs


# Discrepancias conocidas con el oráculo (el oráculo es el que se equivoca
# o aplica otra política; se documentan en el informe).
KNOWN = {
    "malformado-duplicada": "json.loads acepta claves duplicadas y se queda "
    "con la última; el enunciado clasifica las claves duplicadas como "
    "malformadas y el WDPDA las detecta con la máscara de bits.",
    "extremo-numero-enorme-falla": "jsonschema convierte 1e30 a float "
    "(= 1000000000000000019884624838656) y acepta 10^30+1; con decimales "
    "exactos 10^30+1 > 1e30 y el WDPDA lo rechaza correctamente.",
}


def main() -> None:
    st = open_store()
    cs = build_cases()
    for d in cs:
        if d["_id"] in KNOWN:
            d["nota"] = KNOWN[d["_id"]]
    for d in cs:
        st.upsert(d)
    by: dict[str, int] = {}
    for d in cs:
        by[d["categoria"]] = by.get(d["categoria"], 0) + 1
    mism = [d["_id"] for d in cs if d.get("oraculo") and "nota" not in d and
            d["oraculo"] != d["esperado"]["status"] and not (
                d["oraculo"] == "MALFORMED" and d["esperado"]["status"]
                in ("MALFORMED", "UTF8"))]
    print(f"almacén: {st.kind}; casos: {len(cs)} {by}")
    print("discrepancias con el oráculo:", mism or "ninguna")


if __name__ == "__main__":
    main()
