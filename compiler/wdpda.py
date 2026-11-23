"""Fase 4 — Construcción del autómata de pila ponderado (WDPDA) y su IR.

Definición formal (detalle y demostraciones en ``docs/fase4-wdpda.md``):

    M = (Q, Σ, Γ, δ, q0, Z0, F, w)

- Σ: alfabeto de tokens refinados que entrega el transductor léxico
  (AFD sobre bytes UTF-8): ``{ } [ ] : ,`` , ``KEY(k)`` (clave k del
  trie del objeto, o ``KEY(*)`` si no es una clave distinguida) y los
  escalares STRING/NUMBER/TRUE/FALSE/NULL con su *guarda* (resultado
  de los AFD de enum, pattern, longitud y comparación decimal).
- Γ: Z0 y un símbolo O_n / A_n por cada nodo objeto / arreglo del
  esquema, que lleva sus registros acotados (máscara ``required`` de 64
  bits, contadores saturados). Como los registros son acotados, Γ es
  finito.
- δ: construcción composicional por nodo del esquema (ver
  :mod:`compiler.formal`, que genera δ de forma explícita).
- w: δ → (ℕ ∪ {+∞}) en el semianillo tropical (min, +, +∞, 0). Una
  transición que respeta el esquema pesa 0 y una que lo viola pesa 1;
  el peso de una ejecución es la suma (⊗ = +) y el peso de una entrada
  es el mínimo sobre ejecuciones (⊕ = min). Sin combinadores hay una
  sola ejecución (determinismo). Con anyOf el mínimo tropical sobre
  ramas es exactamente la semántica de anyOf. Entrada mal formada: no
  hay ejecución, peso +∞ (el cero del semianillo).
  Además cada comprobación tiene un costo estático ``c`` (estimado en
  compilación) que ordena las comprobaciones de un nodo de menor a
  mayor costo: fallo temprano.

El IR es un JSON único que leen el generador C, el generador Rust, el
motor de referencia Python, el visualizador web y el informe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .automata import DFA, build_trie, compile_regex
from .numbers import Dec
from .schema import Analysis, Num, SNode, analyze

IR_VERSION = 1

TYPE_BITS = {"null": 1, "boolean": 2, "integer": 4, "number": 8,
             "string": 16, "array": 32, "object": 64}
ANY_TYPES = 127

ANY = -1        # hijo que acepta cualquier valor (schema true / ausente)
FORBIDDEN = -2  # additionalProperties: false

# Costos estáticos (unidades: transiciones del AFD por byte ~ 1).
COST = {"type": 1, "bool": 1, "length": 1, "enum": 2, "range": 2,
        "multipleOf": 4, "required": 1, "count": 1, "pattern": 3,
        "unique": 8}

NUM_OPS = ("ge", "gt", "le", "lt", "mul", "eq")

DEFAULT_LIMITS = {
    "max_depth": 256,       # profundidad máxima de anidamiento (pila)
    "max_lanes": 64,        # carriles del producto por nivel
    "lane_pool": 8192,      # carriles en toda la pila
    "max_errors": 16,       # errores guardados (se cuentan todos)
    "max_patterns": 8,      # AFD de pattern/patternProperties por carril
    "unique_cap": 4096,     # elementos por arreglo con uniqueItems
    "path_bytes": 1024,     # búfer del JSON Pointer
}


class CompileError(ValueError):
    pass


def _is_trivial(n: SNode) -> bool:
    """¿El nodo acepta cualquier valor? (``true`` o ``{}``)."""
    if n.bool_schema is True:
        return True
    if n.bool_schema is False:
        return False
    empty = SNode(-1, "")
    for f in ("types", "enum", "min_length", "max_length", "minimum",
              "maximum", "exclusive_minimum", "exclusive_maximum",
              "multiple_of", "additional", "min_properties",
              "max_properties", "items", "contains", "min_items",
              "max_items", "not_", "if_"):
        if getattr(n, f) != getattr(empty, f):
            return False
    return not (n.patterns or n.properties or n.pattern_properties
                or n.required or n.dependent_required or n.dependent_schemas
                or n.prefix_items or n.unique_items or n.all_of or n.any_of
                or n.one_of)


def _dec_key(d: Dec | None):
    return None if d is None else str(d)


def _enum_key(vals):
    if vals is None:
        return None
    out = []
    for v in vals:
        if isinstance(v, Num):
            out.append(("n", str(v.dec)))
        else:
            out.append((type(v).__name__, v))
    return tuple(out)


def minimize_nodes(nodes: list[SNode], root: SNode):
    """Refinamiento de particiones (Moore) sobre el grafo de nodos.

    Dos nodos son equivalentes si tienen las mismas restricciones locales
    y sus hijos, posición a posición, están en bloques equivalentes. Es
    la minimización de estados de Moore aplicada a la "máquina" de
    subautómatas; funciona con ciclos ($ref recursivos) porque refina
    una partición en vez de comparar árboles. Devuelve (representante
    por id de nodo, cantidad de bloques).
    """
    reach: list[SNode] = []
    seen = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.id in seen:
            continue
        seen.add(n.id)
        reach.append(n)
        stack.extend(n.children())

    def local(n: SNode):
        if _is_trivial(n):
            return ("TRUE",)
        return (n.bool_schema, tuple(sorted(n.types)) if n.types else None,
                _enum_key(n.enum), n.min_length, n.max_length,
                tuple(n.patterns), _dec_key(n.minimum), _dec_key(n.maximum),
                _dec_key(n.exclusive_minimum), _dec_key(n.exclusive_maximum),
                _dec_key(n.multiple_of), tuple(n.properties),
                tuple(p for p, _ in n.pattern_properties),
                n.additional is None, tuple(n.required), n.min_properties,
                n.max_properties,
                tuple((k, tuple(v)) for k, v in n.dependent_required.items()),
                tuple(n.dependent_schemas), len(n.prefix_items),
                n.items is None, n.contains is None, n.min_contains,
                n.max_contains, n.min_items, n.max_items, n.unique_items,
                len(n.all_of), len(n.any_of), len(n.one_of),
                n.not_ is None, n.if_ is None, n.then_ is None,
                n.else_ is None)

    def kids(n: SNode):
        if _is_trivial(n):
            return []
        out = list(n.properties.values()) + [c for _, c in
                                             n.pattern_properties]
        out += [n.additional, n.items, n.contains, n.not_, n.if_, n.then_,
                n.else_]
        out += n.prefix_items + n.all_of + n.any_of + n.one_of
        out += list(n.dependent_schemas.values())
        return out

    sigs: dict = {}
    block = {n.id: sigs.setdefault(local(n), len(sigs)) for n in reach}
    nblocks = len(sigs)
    while True:
        sigs = {}
        new = {}
        for n in reach:
            key = (block[n.id], tuple(-9 if c is None else block[c.id]
                                      for c in kids(n)))
            new[n.id] = sigs.setdefault(key, len(sigs))
        block = new
        if len(sigs) == nblocks:
            break
        nblocks = len(sigs)
    rep: dict[int, SNode] = {}
    first: dict[int, SNode] = {}
    for n in reach:
        first.setdefault(block[n.id], n)
        rep[n.id] = first[block[n.id]]
    return rep, len(first), len(reach)


@dataclass
class _Builder:
    analysis: Analysis
    limits: dict

    def build(self) -> dict:
        a = self.analysis
        rep, nblocks, nreach = minimize_nodes(a.nodes, a.root)
        self.rep = rep
        self.ids: dict[int, int] = {}
        self.order: list[SNode] = []
        self.dfas: list[dict] = []
        self.dfa_index: dict[tuple, int] = {}
        self.consts: list[dict] = []
        self.const_index: dict[str, int] = {}
        root = self._ref(a.root)
        ir_nodes = []
        i = 0
        while i < len(self.order):  # self.order crece mientras se compila
            ir_nodes.append(self._compile(self.order[i], i))
            i += 1
        uses_unique = any(n["arr"] and n["arr"]["unique"] for n in ir_nodes)
        widths = _lane_widths(ir_nodes)
        ir = {
            "ir_version": IR_VERSION,
            "format": "json",
            "root": root,
            "nodes": ir_nodes,
            "dfas": self.dfas,
            "consts": self.consts,
            "limits": dict(self.limits),
            "flags": {"uses_unique": uses_unique},
            "classification": a.classification,
            "warnings": a.warnings,
            "stats": {
                "schema_nodes": len(a.nodes),
                "reachable_nodes": nreach,
                "nodes_after_minimization": len(ir_nodes),
                "dfa_count": len(self.dfas),
                "dfa_states": sum(d["states"] for d in self.dfas),
                "dfa_states_before_min": sum(d["states_before_min"]
                                             for d in self.dfas),
                "max_lane_width": max(widths) if widths else 1,
            },
        }
        if root == ANY:
            ir["stats"]["note"] = "el esquema acepta cualquier JSON"
        return ir

    # -- identificadores -------------------------------------------------
    def _ref(self, n: SNode | None) -> int:
        if n is None:
            return ANY
        n = self.rep[n.id]
        if _is_trivial(n):
            return ANY
        if n.id not in self.ids:
            self.ids[n.id] = len(self.order)
            self.order.append(n)
        return self.ids[n.id]

    def _dfa(self, kind: str, key: tuple, make) -> int:
        k = (kind,) + key
        if k in self.dfa_index:
            return self.dfa_index[k]
        d: DFA = make()
        classes, ncls, table = d.dense()
        self.dfa_index[k] = len(self.dfas)
        self.dfas.append({
            "id": len(self.dfas), "kind": kind, "source": d.source or
            list(key)[:20], "states": d.n,
            "states_before_min": d.states_before_min or d.n,
            "transitions": d.transitions_count(), "classes": classes,
            "ncls": ncls, "table": table, "accept": d.tag})
        return self.dfa_index[k]

    def _pattern(self, pat: str) -> int:
        return self._dfa("pattern", (pat,), lambda: compile_regex(pat))

    def _trie(self, kind: str, words: list[str]) -> int:
        return self._dfa(kind, tuple(words), lambda: build_trie(
            [w.encode("utf-8") for w in words], name=kind))

    def _const(self, d: Dec) -> int:
        key = str(d)
        if key not in self.const_index:
            self.const_index[key] = len(self.consts)
            self.consts.append({"lexeme": key, "neg": d.neg,
                                "digits": d.digits, "exp": d.exp})
        return self.const_index[key]

    # -- compilación de un nodo ------------------------------------------
    def _compile(self, n: SNode, idx: int) -> dict:
        node: dict = {"id": idx, "ptr": n.pointer, "title": n.title,
                      "desc": n.describe(), "false": n.bool_schema is False,
                      "types": ANY_TYPES, "enum": None, "str": None,
                      "num": None, "obj": None, "arr": None,
                      "all_of": [], "any_of": [], "one_of": [], "not": ANY,
                      "if": ANY, "then": ANY, "else": ANY, "checks": []}
        if n.bool_schema is False:
            return node
        if n.types:
            node["types"] = sum(TYPE_BITS[t] for t in n.types)
            if "number" in n.types:
                node["types"] |= TYPE_BITS["integer"]
        checks: list[tuple[int, str]] = [(COST["type"], "type")]
        # enum / const
        if n.enum is not None:
            strs = [v for v in n.enum if isinstance(v, str)]
            nums = [v for v in n.enum if isinstance(v, Num)]
            lits = 0
            for v in n.enum:
                if v is True:
                    lits |= 1
                elif v is False:
                    lits |= 2
                elif v is None:
                    lits |= 4
            node["enum"] = {
                "strings": self._trie("enum", strs) if strs else -1,
                "values": strs,
                "nums": [self._const(v.dec) for v in nums],
                "lits": lits}
            checks.append((COST["enum"], "enum"))
        # cadenas
        if n.min_length is not None or n.max_length is not None or n.patterns:
            node["str"] = {
                "min_len": n.min_length if n.min_length is not None else -1,
                "max_len": n.max_length if n.max_length is not None else -1,
                "patterns": [self._pattern(p) for p in n.patterns],
                "pattern_src": list(n.patterns)}
            if len(n.patterns) > self.limits["max_patterns"]:
                raise CompileError("demasiados pattern en un nodo")
            checks.append((COST["length"], "length"))
            for p in n.patterns:
                d = self.dfas[self._pattern(p)]
                checks.append((COST["pattern"] + d["states"] // 8, "pattern"))
        # números: comprobaciones ordenadas por costo (fallo temprano)
        num_checks = []
        for attr, op in (("minimum", "ge"), ("exclusive_minimum", "gt"),
                         ("maximum", "le"), ("exclusive_maximum", "lt")):
            d = getattr(n, attr)
            if d is not None:
                num_checks.append((COST["range"], op, self._const(d)))
        if n.multiple_of is not None:
            num_checks.append((COST["multipleOf"], "mul",
                               self._const(n.multiple_of)))
        if num_checks:
            num_checks.sort(key=lambda c: c[0])
            node["num"] = {"checks": [[op, c] for _, op, c in num_checks]}
            checks += [(c, op) for c, op, _ in num_checks]
        # objetos
        if (n.properties or n.pattern_properties or n.additional is not None
                or n.required or n.min_properties is not None
                or n.max_properties is not None or n.dependent_required
                or n.dependent_schemas):
            keys = n.tracked_keys()
            bit = {k: i for i, k in enumerate(keys)}
            req = 0
            for k in n.required:
                req |= 1 << bit[k]
            pats = []
            for p, child in n.pattern_properties:
                pats.append([self._pattern(p), self._ref(child)])
            if len(pats) > self.limits["max_patterns"]:
                raise CompileError("demasiados patternProperties en un nodo")
            add = ANY
            if n.additional is not None:
                add = FORBIDDEN if n.additional.bool_schema is False else \
                    self._ref(n.additional)
            node["obj"] = {
                "keys": keys,
                "keys_dfa": self._trie("keys", keys) if keys else -1,
                "key_child": [self._ref(n.properties[k]) if k in n.properties
                              else ANY for k in keys],
                "key_declared": [k in n.properties for k in keys],
                "required": req,
                "patterns": pats,
                "pattern_src": [p for p, _ in n.pattern_properties],
                "additional": add,
                "min_props": n.min_properties if n.min_properties
                is not None else -1,
                "max_props": n.max_properties if n.max_properties
                is not None else -1,
                "dep_required": [[bit[k], sum(1 << bit[x] for x in v)]
                                 for k, v in n.dependent_required.items()],
                "dep_schemas": [[bit[k], self._ref(c)]
                                for k, c in n.dependent_schemas.items()],
            }
            checks.append((COST["required"], "required"))
        # arreglos
        if (n.prefix_items or n.items is not None or n.contains is not None
                or n.min_items is not None or n.max_items is not None
                or n.unique_items):
            node["arr"] = {
                "prefix": [self._ref(c) for c in n.prefix_items],
                "items": self._ref(n.items),
                "items_false": n.items is not None and
                n.items.bool_schema is False,
                "contains": self._ref(n.contains) if n.contains is not None
                else -3,
                "min_contains": (n.min_contains if n.min_contains is not None
                                 else 1) if n.contains is not None else -1,
                "max_contains": n.max_contains if n.max_contains
                is not None else -1,
                "min_items": n.min_items if n.min_items is not None else -1,
                "max_items": n.max_items if n.max_items is not None else -1,
                "unique": n.unique_items,
            }
            if node["arr"]["items_false"]:
                node["arr"]["items"] = FORBIDDEN
            checks.append((COST["count"], "items"))
            if n.unique_items:
                checks.append((COST["unique"], "unique"))
        # combinadores (producto)
        node["all_of"] = [x for x in (self._ref(c) for c in n.all_of)
                          if x != ANY]
        node["any_of"] = [self._ref(c) for c in n.any_of]
        node["one_of"] = [self._ref(c) for c in n.one_of]
        if n.not_ is not None:
            node["not"] = self._ref(n.not_)
            if node["not"] == ANY:
                node["not_any"] = True  # not: true -> nada valida
        if n.if_ is not None:
            node["if"] = self._ref(n.if_)
            node["then"] = self._ref(n.then_) if n.then_ is not None else ANY
            node["else"] = self._ref(n.else_) if n.else_ is not None else ANY
            if node["if"] == ANY:  # if: true -> siempre then
                node["all_of"].append(node["then"]) if node["then"] != ANY \
                    else None
                node["if"] = node["then"] = node["else"] = ANY
        checks.sort(key=lambda c: c[0])
        node["checks"] = [{"cost": c, "check": k} for c, k in checks]
        return node


def _lane_widths(nodes: list[dict]) -> list[int]:
    """Ancho de la expansión (carriles) de cada nodo: 1 + combinadores."""
    memo: dict[int, int] = {}

    def w(i: int) -> int:
        if i < 0:
            return 0
        if i in memo:
            return memo[i]
        memo[i] = 1  # sin ciclos no productivos (Fase 3), pero por si acaso
        n = nodes[i]
        tot = 1 + sum(w(c) for c in n["all_of"] + n["any_of"] + n["one_of"])
        for c in (n["not"], n["if"], n["then"], n["else"]):
            tot += w(c)
        if n["obj"]:
            tot += sum(w(c) for _, c in n["obj"]["dep_schemas"])
        memo[i] = tot
        return tot

    return [w(i) for i in range(len(nodes))]


def compile_schema(data: bytes, limits: dict | None = None) -> dict:
    """Esquema JSON (bytes) -> IR del WDPDA."""
    lim = dict(DEFAULT_LIMITS)
    if limits:
        lim.update(limits)
    analysis = analyze(data)
    ir = _Builder(analysis, lim).build()
    widths = _lane_widths(ir["nodes"])
    if widths and max(widths) > lim["max_lanes"]:
        raise CompileError(
            f"el producto de combinadores necesita {max(widths)} carriles "
            f"por nivel (límite {lim['max_lanes']}): riesgo de explosión "
            "al determinizar")
    from .formal import build_formal  # import tardío (evita ciclo)
    formal = build_formal(ir)
    ir["formal"] = formal.summary()
    return ir


def dumps(ir: dict) -> str:
    return json.dumps(ir, ensure_ascii=False, indent=1)
