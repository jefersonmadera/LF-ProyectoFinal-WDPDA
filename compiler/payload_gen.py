"""Generador de payloads válidos e inválidos a partir del IR (Fase 5).

- ``gen_valid``: recorre el autómata del esquema eligiendo transiciones
  de peso 0 (tipos permitidos, claves requeridas, longitudes y rangos
  dentro de los límites, cadenas que aceptan los AFD de ``pattern`` y
  ``enum`` por caminata aleatoria sobre estados vivos).
- ``mutate``: aplica una mutación que debería forzar una transición de
  peso 1 (tipo incorrecto, quitar una requerida, salir de rango, clave
  adicional...). No garantiza invalidez (p. ej. con anyOf), por eso las
  pruebas comparan contra un oráculo y no contra la intención.
- ``to_json`` / ``to_xml``: serialización de un valor Python.

Con combinadores la generación es de mejor esfuerzo (elige una rama de
anyOf/oneOf; allOf se genera sobre el primer miembro); el resultado se
clasifica siempre con el validador, no se supone.
"""

from __future__ import annotations

import json
import random
from xml.sax.saxutils import escape

from .wdpda import ANY, FORBIDDEN, TYPE_BITS

PRINTABLE = list(range(0x20, 0x7F))


class _Gen:
    def __init__(self, ir: dict, rng: random.Random, max_depth: int = 6):
        self.ir = ir
        self.nodes = ir["nodes"]
        self.rng = rng
        self.max_depth = max_depth
        self._live = {}
        self.consts = ir["consts"]

    # -- cadenas desde un AFD ---------------------------------------------
    def _live_states(self, d: dict) -> set[int]:
        key = d["id"]
        if key in self._live:
            return self._live[key]
        n = d["states"]
        ncls = d["ncls"]
        live = {s for s in range(n) if d["accept"][s] >= 0}
        changed = True
        while changed:
            changed = False
            for s in range(n):
                if s in live:
                    continue
                if any(d["table"][s * ncls + c] in live for c in range(ncls)):
                    live.add(s)
                    changed = True
        self._live[key] = live
        return live

    def string_from_dfa(self, d: dict, min_len=0, max_len=24) -> str | None:
        live = self._live_states(d)
        if 0 not in live:
            return None
        for _ in range(40):
            s, out = 0, bytearray()
            for _ in range(max_len * 4):
                acc = d["accept"][s] >= 0
                if acc and len(out) >= min_len and self.rng.random() < 0.3:
                    break
                opts = [b for b in PRINTABLE
                        if d["table"][s * d["ncls"] + d["classes"][b]] in live]
                if not opts:
                    opts = [b for b in range(256)
                            if d["table"][s * d["ncls"] + d["classes"][b]]
                            in live]
                if not opts:
                    break
                b = self.rng.choice(opts)
                out.append(b)
                s = d["table"][s * d["ncls"] + d["classes"][b]]
            if d["accept"][s] >= 0:
                try:
                    return out.decode("utf-8")
                except UnicodeDecodeError:
                    continue
        return None

    # -- valores ----------------------------------------------------------
    def value(self, i: int, depth: int = 0):
        rng = self.rng
        if i == ANY:
            return self.any_value(depth)
        n = self.nodes[i]
        if n["false"]:
            return None
        if n["all_of"] and rng.random() < 0.5:
            return self.value(n["all_of"][0], depth)
        if n["any_of"] or n["one_of"]:
            br = n["any_of"] or n["one_of"]
            return self.value(rng.choice(br), depth)
        if n["enum"] is not None:
            e = n["enum"]
            opts = list(e["values"])
            opts += [("num", c) for c in e["nums"]]
            if e["lits"] & 1:
                opts.append(True)
            if e["lits"] & 2:
                opts.append(False)
            if e["lits"] & 4:
                opts.append(None)
            v = rng.choice(opts)
            if isinstance(v, tuple):
                return _Lexeme(self.consts[v[1]]["lexeme"])
            return v
        types = [t for t, b in TYPE_BITS.items() if n["types"] & b]
        if n["obj"] and "object" in types and rng.random() < 0.8:
            t = "object"
        elif n["arr"] and "array" in types and rng.random() < 0.8:
            t = "array"
        else:
            t = rng.choice(types)
        if depth >= self.max_depth and t in ("object", "array"):
            scal = [x for x in types if x not in ("object", "array")]
            if scal:
                t = rng.choice(scal)
        return getattr(self, "g_" + t)(n, depth)

    def any_value(self, depth):
        rng = self.rng
        opts = ["null", "boolean", "integer", "number", "string"]
        if depth < 3:
            opts += ["array", "object"]
        t = rng.choice(opts)
        if t == "object":
            return {f"k{j}": self.any_value(depth + 1)
                    for j in range(rng.randint(0, 3))}
        if t == "array":
            return [self.any_value(depth + 1) for _ in range(rng.randint(0, 3))]
        return getattr(self, "g_" + t)(None, depth)

    def g_null(self, n, depth):
        return None

    def g_boolean(self, n, depth):
        return self.rng.random() < 0.5

    def _num_bounds(self, n):
        lo = hi = None
        mul = None
        if n and n["num"]:
            for op, c in n["num"]["checks"]:
                v = float(self.consts[c]["lexeme"])
                if op in ("ge", "gt"):
                    lo = v + (1e-6 if op == "gt" else 0) if lo is None else \
                        max(lo, v)
                elif op in ("le", "lt"):
                    hi = v - (1e-6 if op == "lt" else 0) if hi is None else \
                        min(hi, v)
                elif op == "mul":
                    mul = float(self.consts[c]["lexeme"])
        return lo, hi, mul

    def g_integer(self, n, depth):
        lo, hi, mul = self._num_bounds(n)
        import math
        a = math.ceil(lo) if lo is not None else -1000
        b = math.floor(hi) if hi is not None else (a + 2000)
        if lo is None and hi is not None:
            a = b - 2000
        if a > b:
            return a
        if mul and mul == int(mul) and mul > 0:
            m = int(mul)
            k0 = -(-a // m)
            k1 = b // m
            if k0 <= k1:
                return self.rng.randint(k0, k1) * m
        return self.rng.randint(a, b)

    def g_number(self, n, depth):
        lo, hi, mul = self._num_bounds(n)
        if mul or self.rng.random() < 0.4:
            return self.g_integer(n, depth)
        a = lo if lo is not None else -1000.0
        b = hi if hi is not None else a + 2000.0
        v = round(self.rng.uniform(a, b), 3)
        return min(max(v, a), b)

    def g_string(self, n, depth):
        s = n["str"] if n else None
        lo = s["min_len"] if s and s["min_len"] >= 0 else 0
        hi = s["max_len"] if s and s["max_len"] >= 0 else max(lo + 8, 8)
        if s and s["patterns"]:
            d = self.ir["dfas"][s["patterns"][0]]
            r = self.string_from_dfa(d, lo, max(hi, 1))
            if r is not None:
                return r
        k = self.rng.randint(lo, max(lo, min(hi, lo + 10)))
        alphabet = "abcdefghijklmnopqrstuvwxyzñé0123456789 _-"
        return "".join(self.rng.choice(alphabet) for _ in range(k))

    def g_object(self, n, depth):
        rng = self.rng
        o = n["obj"] if n else None
        out = {}
        if o is None:
            return {f"k{j}": self.any_value(depth + 1)
                    for j in range(rng.randint(0, 2))}
        for b, k in enumerate(o["keys"]):
            req = o["required"] >> b & 1
            if req or (depth < self.max_depth and rng.random() < 0.6):
                child = o["key_child"][b] if o["key_declared"][b] else \
                    (o["additional"] if o["additional"] >= 0 else ANY)
                out[k] = self.value(child, depth + 1)
        for kb, mask in o["dep_required"]:
            if o["keys"][kb] in out:
                for b, k in enumerate(o["keys"]):
                    if mask >> b & 1 and k not in out:
                        out[k] = self.value(o["key_child"][b]
                                            if o["key_declared"][b] else ANY,
                                            depth + 1)
        if o["additional"] != FORBIDDEN and not o["patterns"] and \
                rng.random() < 0.3:
            out["extra_" + str(rng.randint(0, 99))] = self.value(
                o["additional"], depth + 1)
        for d, child in o["patterns"]:
            if rng.random() < 0.5:
                key = self.string_from_dfa(self.ir["dfas"][d], 1, 10)
                if key is not None and key not in o["keys"]:
                    out[key] = self.value(child, depth + 1)
        return out

    def g_array(self, n, depth):
        rng = self.rng
        a = n["arr"] if n else None
        if a is None:
            return [self.any_value(depth + 1) for _ in range(rng.randint(0, 3))]
        lo = max(a["min_items"], 0)
        hi = a["max_items"] if a["max_items"] >= 0 else lo + 4
        k = rng.randint(lo, max(lo, hi)) if depth < self.max_depth else lo
        out = []
        for j in range(k):
            child = a["prefix"][j] if j < len(a["prefix"]) else a["items"]
            if child == FORBIDDEN:
                break
            v = self.value(child, depth + 1)
            if a["unique"]:
                for _ in range(10):
                    if json.dumps(v, default=str) not in [
                            json.dumps(x, default=str) for x in out]:
                        break
                    v = self.value(child, depth + 1)
            out.append(v)
        if a["contains"] >= 0 and out:
            out[rng.randrange(len(out))] = self.value(a["contains"], depth + 1)
        return out


class _Lexeme(str):
    """Número que debe serializarse con su lexema exacto."""


def gen_valid(ir: dict, seed: int = 0, max_depth: int = 6):
    return _Gen(ir, random.Random(seed), max_depth).value(ir["root"])


def mutate(ir: dict, value, seed: int = 0):
    """Devuelve (valor mutado, descripción de la mutación)."""
    rng = random.Random(seed)
    paths = []

    def walk(v, path):
        paths.append((path, v))
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, path + [k])
        elif isinstance(v, list):
            for j, x in enumerate(v):
                walk(x, path + [j])

    walk(value, [])
    path, target = rng.choice(paths)
    kind = rng.choice(["type", "drop", "extra", "big", "empty", "dup"])
    if kind == "type":
        new = rng.choice(["texto", 12345, 3.5, True, None, [], {}])
    elif kind == "drop" and isinstance(target, dict) and target:
        new = dict(target)
        new.pop(rng.choice(list(new)))
    elif kind == "extra" and isinstance(target, dict):
        new = dict(target)
        new["no_declarada"] = 1
    elif kind == "big":
        new = 10 ** 12 if not isinstance(target, str) else target * 40
    elif kind == "empty":
        new = "" if isinstance(target, str) else ([] if isinstance(
            target, list) else {})
    elif kind == "dup" and isinstance(target, list) and target:
        new = target + [target[0]]
    else:
        new = None
    if not path:
        return new, f"{kind} en la raíz"
    root = json.loads(to_json(value))
    cur = root
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = new
    return root, f"{kind} en /{'/'.join(map(str, path))}"


def to_json(value, indent=None) -> str:
    def enc(v):
        if isinstance(v, _Lexeme):
            return str(v)
        if v is True:
            return "true"
        if v is False:
            return "false"
        if v is None:
            return "null"
        if isinstance(v, (int, float)):
            return json.dumps(v)
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, list):
            return "[" + ",".join(enc(x) for x in v) + "]"
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + enc(x)
                              for k, x in v.items()) + "}"

    s = enc(value)
    if indent:
        return json.dumps(json.loads(s), ensure_ascii=False, indent=indent)
    return s


def to_xml(value, tag: str = "raiz") -> str:
    """Serialización XML simple: objetos -> elementos, arreglos -> 'item'."""
    if isinstance(value, dict):
        inner = "".join(to_xml(v, k) for k, v in value.items())
        return f"<{tag}>{inner}</{tag}>"
    if isinstance(value, list):
        return f"<{tag}>" + "".join(to_xml(v, "item") for v in value) + \
            f"</{tag}>"
    if value is None:
        return f"<{tag}/>"
    if value is True or value is False:
        return f"<{tag}>{'true' if value else 'false'}</{tag}>"
    return f"<{tag}>{escape(str(value))}</{tag}>"
