"""Generador de instancias XML válidas e inválidas desde el IR de un XSD.

Recorre el AFD de contenido de cada elemento con una caminata aleatoria
hacia estados de aceptación (como el generador JSON hace con los AFD de
``pattern``), genera texto que cumple las facetas del tipo simple y los
atributos requeridos. ``mutate_xml`` rompe una regla al azar.
"""

from __future__ import annotations

import random
from xml.sax.saxutils import escape, quoteattr

from .payload_gen import _Gen

XSD_KIND = {0: "string", 1: "boolean", 2: "integer", 3: "decimal",
            4: "double"}


class _XGen:
    def __init__(self, ir: dict, rng: random.Random, max_depth: int = 5):
        self.ir = ir
        self.rng = rng
        self.max_depth = max_depth
        self.g = _Gen({"nodes": ir["nodes"], "consts": ir["consts"],
                       "dfas": ir["dfas"]}, rng)

    def text(self, ni: int) -> str:
        n = self.ir["nodes"][ni]
        kind = XSD_KIND[n["xsd"]]
        rng = self.rng
        e = n["enum"]
        if e is not None:
            if e["values"]:
                return rng.choice(e["values"])
            return self.ir["consts"][rng.choice(e["nums"])]["lexeme"]
        if kind == "boolean":
            return rng.choice(["true", "false", "1", "0"])
        if kind in ("integer", "decimal", "double"):
            v = self.g.g_integer(n, 0) if kind == "integer" else \
                self.g.g_number(n, 0)
            return str(v)
        return self.g.g_string(n, 0)

    def _live(self, d: dict) -> set[int]:
        return self.g._live_states(d)

    def element(self, eid: int, depth: int = 0) -> str:
        E = self.ir["elements"][eid]
        rng = self.rng
        attrs = ""
        for a in E["attrs"]:
            if a["required"] or rng.random() < 0.5:
                attrs += f" {a['name']}={quoteattr(self.text(a['node']))}"
        name = E["name"]
        if not E["complex"]:
            if E["simple"] < 0:
                return f"<{name}{attrs}/>"
            return f"<{name}{attrs}>{escape(self.text(E['simple']))}</{name}>"
        if E["content_dfa"] < 0:
            return f"<{name}{attrs}/>"
        d = self.ir["dfas"][E["content_dfa"]]
        live = self._live(d)
        s, parts = 0, []
        for _ in range(30):
            acc = d["accept"][s] >= 0
            stop_p = 0.95 if depth >= self.max_depth else 0.35
            if acc and rng.random() < stop_p:
                break
            opts = [k for k in range(len(E["child_names"]))
                    if d["table"][s * d["ncls"] + d["classes"][k]] in live]
            if depth >= self.max_depth and acc:
                break
            if not opts:
                break
            k = rng.choice(opts)
            parts.append(self.element(E["child_elems"][k], depth + 1))
            s = d["table"][s * d["ncls"] + d["classes"][k]]
        return f"<{name}{attrs}>{''.join(parts)}</{name}>"


def gen_xml(ir: dict, seed: int = 0) -> str:
    return _XGen(ir, random.Random(seed)).element(ir["root"])


def mutate_xml(xml: str, seed: int = 0) -> str:
    rng = random.Random(seed)
    import re
    tags = [m for m in re.finditer(r"<([A-Za-z_][\w.-]*)([^>]*)>([^<]*)</\1>",
                                   xml)]
    kind = rng.choice(["text", "drop", "extra", "attr", "swap"])
    if kind == "text" and tags:
        m = rng.choice(tags)
        # (sin números de más de 24 dígitos: libxml2 no los acepta en
        # xs:decimal aunque XSD no lo prohíbe; sería medir su límite)
        return xml[:m.start(3)] + rng.choice(["x", "", "-5", "9" * 20,
                                              "abc def"]) + xml[m.end(3):]
    if kind == "drop" and tags:
        m = rng.choice(tags)
        return xml[:m.start()] + xml[m.end():]
    if kind == "extra":
        i = xml.find(">")
        return xml[:i + 1] + "<intruso>1</intruso>" + xml[i + 1:]
    if kind == "attr":
        return re.sub(r' [A-Za-z_][\w.-]*="[^"]*"', "", xml, count=1)
    if tags and len(tags) > 1:
        a, b = rng.sample(tags, 2)
        if a.start() > b.start():
            a, b = b, a
        if a.end() <= b.start():
            return (xml[:a.start()] + b.group(0) + xml[a.end():b.start()]
                    + a.group(0) + xml[b.end():])
    return xml
