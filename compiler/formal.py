"""δ explícito del WDPDA, verificación de determinismo y ejecución paso a paso.

A partir del IR se genera la función de transición completa

    δ(q, a, X) = (q', γ, w)

con la construcción composicional por nodo del esquema:

======================  =============================================
Estado                  Significado
======================  =============================================
``V(n)``                se espera un valor que debe cumplir el nodo n
``K1(n)``               tras ``{`` de n: clave o ``}``
``K2(n)``               tras ``,`` en un objeto de n: clave
``C(n,k)``              tras la clave k (o ``*``): se espera ``:``
``N(n)``                tras un miembro: ``,`` o ``}``
``A1(n)``               tras ``[`` de n: elemento o ``]``
``A2(n)``               tras ``,`` en un arreglo de n: elemento
``M(n)``                tras un elemento: ``,`` o ``]``
``R``                   retorno: decide con el tope de la pila (ε)
``F``                   aceptación
======================  =============================================

El nodo especial ``⊤`` (cualquier valor) tiene los mismos estados y es,
literalmente, el DPDA sintáctico de JSON: las violaciones de tipo
saltan a ⊤ con peso 1 y la validación continúa (el WDPDA es total
sobre JSON bien formado). Una entrada mal formada no tiene transición:
peso +∞.

Determinismo: para cada (q, a, X) hay a lo sumo una transición, y si
existe una transición ε para (q, X) no hay ninguna que lea entrada desde
(q, X). Las guardas sobre un mismo símbolo (p. ej. STRING que cumple /
no cumple el nodo) son complementarias, es decir, refinan el alfabeto.
:func:`check_determinism` lo comprueba de forma automática.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .wdpda import ANY, FORBIDDEN, TYPE_BITS

TOP = "⊤"
SCALARS = ("STRING", "INT", "FRAC", "TRUE", "FALSE", "NULL")
SCALAR_TYPE = {"STRING": TYPE_BITS["string"], "INT": TYPE_BITS["integer"],
               "FRAC": TYPE_BITS["number"], "TRUE": TYPE_BITS["boolean"],
               "FALSE": TYPE_BITS["boolean"], "NULL": TYPE_BITS["null"]}
VALUE_START = ("{", "[") + SCALARS


@dataclass(frozen=True)
class T:
    src: str
    sym: str            # símbolo de entrada o "ε"
    pop: str | None     # símbolo del tope que se lee (None = cualquiera, λ)
    push: tuple         # lo que se apila en lugar del tope leído
    dst: str
    weight: int | str   # 0, 1 o una expresión (p. ej. "|req∖seen|")
    guard: str = ""     # condición sobre el token o los registros
    action: str = ""    # actualización de registros

    def label(self) -> str:
        pop = self.pop if self.pop is not None else "λ"
        if self.pop is None:
            stack = "" if not self.push else f"; push {''.join(self.push)}"
        else:
            stack = f"; {pop}/{''.join(self.push) or 'λ'}"
        g = f" [{self.guard}]" if self.guard else ""
        a = f" {{{self.action}}}" if self.action else ""
        return f"{self.sym}{g}{stack}{a} / w={self.weight}"


@dataclass
class Formal:
    states: list[str] = field(default_factory=list)
    kinds: dict[str, str] = field(default_factory=dict)
    stack_symbols: list[str] = field(default_factory=list)
    trans: list[T] = field(default_factory=list)
    start: str = ""
    final: str = "F"
    node_names: dict[int, str] = field(default_factory=dict)
    product_nodes: list[int] = field(default_factory=list)
    keys: dict[str, str] = field(default_factory=dict)

    def add_state(self, s: str, kind: str) -> str:
        if s not in self.kinds:
            self.states.append(s)
            self.kinds[s] = kind
        return s

    def add_stack(self, x: str) -> str:
        if x not in self.stack_symbols:
            self.stack_symbols.append(x)
        return x

    def summary(self) -> dict:
        det = check_determinism(self)
        return {"states": len(self.states),
                "stack_symbols": len(self.stack_symbols),
                "transitions": len(self.trans),
                "epsilon_transitions": sum(t.sym == "ε" for t in self.trans),
                "violation_transitions": sum(t.weight not in (0, "0")
                                             for t in self.trans),
                "input_symbols": sorted({t.sym for t in self.trans}),
                "deterministic": det["deterministic"],
                "determinism_conflicts": det["conflicts"][:10],
                "product_nodes": self.product_nodes,
                "start": self.start, "final": self.final}


def _n(i: int) -> str:
    return TOP if i == ANY else f"n{i}"


def build_formal(ir: dict) -> Formal:
    nodes = ir["nodes"]
    f = Formal()
    f.add_stack("Z0")
    built: set = set()

    def obj_cap(i):
        return i != ANY and not nodes[i]["false"] and (
            nodes[i]["types"] & TYPE_BITS["object"])

    def arr_cap(i):
        return i != ANY and not nodes[i]["false"] and (
            nodes[i]["types"] & TYPE_BITS["array"])

    def scalar_guard(i, sym):
        """(guard_ok, guard_bad) para un escalar en el nodo i."""
        n = nodes[i]
        parts = []
        if n["enum"] is not None:
            parts.append("enum")
        if sym == "STRING" and n["str"]:
            s = n["str"]
            if s["min_len"] >= 0 or s["max_len"] >= 0:
                parts.append("long")
            parts += [f"D{d}" for d in s["patterns"]]
        if sym in ("INT", "FRAC") and n["num"]:
            parts += [f"{op}c{c}" for op, c in n["num"]["checks"]]
        if not parts:
            return None
        g = "∧".join(parts)
        return f"⊨ {g}", f"⊭ {g}"

    def value_transitions(i: int, src: str, guard_prefix: str = ""):
        """Las transiciones de V(i), replicadas desde src."""
        gp = (guard_prefix + "; ") if guard_prefix else ""
        out = []
        if i == ANY:
            out.append(T(src, "{", None, ("O⊤",), "K1(⊤)", 0, guard_prefix))
            out.append(T(src, "[", None, ("A⊤",), "A1(⊤)", 0, guard_prefix))
            for s in SCALARS:
                out.append(T(src, s, None, (), "R", 0, guard_prefix))
            return out
        n = nodes[i]
        tb = n["types"]
        false = n["false"]
        # contenedores
        for sym, cap, bit, st, gen in (("{", obj_cap, "object", "K1", "O"),
                                       ("[", arr_cap, "array", "A1", "A")):
            if false or not (tb & TYPE_BITS[bit]):
                out.append(T(src, sym, None, (f"{gen}⊤",), f"{st}(⊤)", 1,
                             gp + ("false" if false else f"tipo≠{bit}")))
            elif n["enum"] is not None:
                out.append(T(src, sym, None, (f"{gen}⊤",), f"{st}(⊤)", 1,
                             gp + "∉enum"))
            elif (bit == "object" and n["obj"]) or (bit == "array"
                                                    and n["arr"]):
                ensure_container(i, bit)
                out.append(T(src, sym, None, (f"{gen}{_n(i)}",),
                             f"{st}({_n(i)})", 0, guard_prefix,
                             "seen:=∅,cnt:=0" if bit == "object"
                             else "i:=0"))
            else:  # objeto/arreglo sin restricciones: el genérico ⊤
                out.append(T(src, sym, None, (f"{gen}⊤",), f"{st}(⊤)", 0,
                             guard_prefix))
        for s in SCALARS:
            ok = (not false) and bool(tb & SCALAR_TYPE[s])
            if not ok:
                out.append(T(src, s, None, (), "R", 1,
                             gp + ("false" if false else "tipo")))
                continue
            g = scalar_guard(i, s)
            if g is None:
                out.append(T(src, s, None, (), "R", 0, guard_prefix))
            else:
                out.append(T(src, s, None, (), "R", 0, gp + g[0]))
                out.append(T(src, s, None, (), "R", "#fallos", gp + g[1]))
        return out

    def ensure_value(i: int) -> str:
        name = f"V({_n(i)})"
        if name in f.kinds:
            return name
        f.add_state(name, "value")
        if i != ANY and nodes[i]["any_of"] + nodes[i]["one_of"] + \
                nodes[i]["all_of"] or (i != ANY and (
                    nodes[i]["not"] != ANY or nodes[i]["if"] != ANY)):
            if i not in f.product_nodes:
                f.product_nodes.append(i)
        f.trans.extend(value_transitions(i, name))
        return name

    def ensure_container(i: int, kind: str) -> None:
        key = (i, kind)
        if key in built:
            return
        built.add(key)
        nn = _n(i)
        if kind == "object":
            X = f.add_stack(f"O{nn}")
            k1 = f.add_state(f"K1({nn})", "object")
            k2 = f.add_state(f"K2({nn})", "object")
            nx = f.add_state(f"N({nn})", "object")
            if i == ANY:
                c = f.add_state(f"C({nn},*)", "object")
                for src in (k1, k2):
                    f.trans.append(T(src, "KEY(*)", None, (), c, 0))
                f.trans.append(T(c, ":", None, (), ensure_value(ANY), 0))
                for src in (k1, nx):
                    f.trans.append(T(src, "}", X, (), "R", 0))
            else:
                o = nodes[i]["obj"]
                for b, k in enumerate(o["keys"]):
                    f.keys.setdefault(k, k)
                    c = f.add_state(f"C({nn},{k})", "object")
                    for src in (k1, k2):
                        f.trans.append(T(src, f"KEY({k})", X, (X,), c, 0,
                                         f"{k}∉seen", f"seen∪={{{k}}}"))
                        f.trans.append(T(src, f"KEY({k})", X, (X,), c, 1,
                                         f"{k}∈seen (duplicada)"))
                    child = o["key_child"][b]
                    if not o["key_declared"][b]:
                        child = o["additional"] if o["additional"] != \
                            FORBIDDEN else ANY
                    w = 1 if (not o["key_declared"][b] and
                              o["additional"] == FORBIDDEN and
                              not o["patterns"]) else 0
                    f.trans.append(T(c, ":", None, (), ensure_value(child), w,
                                     "" if not o["patterns"] else
                                     "sin patternProperties (producto)"))
                c = f.add_state(f"C({nn},*)", "object")
                for src in (k1, k2):
                    f.trans.append(T(src, "KEY(*)", X, (X,), c, 0, "",
                                     "cnt+=1"))
                if o["additional"] == FORBIDDEN and not o["patterns"]:
                    f.trans.append(T(c, ":", None, (), ensure_value(ANY), 1,
                                     "additionalProperties=false"))
                else:
                    child = o["additional"] if o["additional"] != FORBIDDEN \
                        else ANY
                    f.trans.append(T(c, ":", None, (), ensure_value(child), 0,
                                     "" if not o["patterns"] else
                                     "patternProperties: producto"))
                for src in (k1, nx):
                    f.trans.append(T(src, "}", X, (), "R", 0,
                                     "req⊆seen ∧ límites"))
                    f.trans.append(T(src, "}", X, (), "R", "|req∖seen|+…",
                                     "¬(req⊆seen ∧ límites)"))
            f.trans.append(T(nx, ",", None, (), k2, 0))
            f.trans.append(T("R", "ε", X, (X,), nx, 0))
        else:
            X = f.add_stack(f"A{nn}")
            a1 = f.add_state(f"A1({nn})", "array")
            a2 = f.add_state(f"A2({nn})", "array")
            m = f.add_state(f"M({nn})", "array")
            if i == ANY:
                for src in (a1, a2):
                    f.trans.extend(value_transitions(ANY, src))
            else:
                a = nodes[i]["arr"]
                pre = a["prefix"]
                for src in (a1, a2):
                    for j, c in enumerate(pre):
                        f.trans.extend(_reg_guard(value_transitions(
                            c if c != FORBIDDEN else ANY, src), f"i={j}",
                            X))
                    items = a["items"]
                    g = f"i≥{len(pre)}" if pre else ""
                    if items == FORBIDDEN:
                        f.trans.extend(_reg_guard(value_transitions(ANY, src),
                                                  g, X, weight=1))
                    else:
                        f.trans.extend(_reg_guard(value_transitions(items,
                                                                    src), g,
                                                  X))
            f.trans.append(T(a1, "]", X, (), "R", 0, "" if i == ANY else
                             "minItems≤i≤maxItems ∧ contains ∧ unique"))
            f.trans.append(T(m, "]", X, (), "R", 0, "" if i == ANY else
                             "minItems≤i≤maxItems ∧ contains ∧ unique"))
            if i != ANY:
                for src in (a1, m):
                    f.trans.append(T(src, "]", X, (), "R", "#fallos",
                                     "¬(límites de arreglo)"))
            f.trans.append(T(m, ",", None, (), a2, 0))
            f.trans.append(T("R", "ε", X, (X,), m, 0))

    f.add_state("R", "return")
    f.add_state("F", "final")
    f.trans.append(T("R", "ε", "Z0", ("Z0",), "F", 0))
    ensure_container(ANY, "object")
    ensure_container(ANY, "array")
    f.start = ensure_value(ir["root"])
    # El estado inicial va primero en la lista (para diagramas).
    f.states.remove(f.start)
    f.states.insert(0, f.start)
    for i in range(len(nodes)):
        f.node_names[i] = nodes[i]["desc"]
    return f


def _reg_guard(ts: list[T], guard: str, top: str, weight=None) -> list[T]:
    """Añade una guarda sobre el registro del tope (lee X y lo repone)."""
    out = []
    for t in ts:
        g = "; ".join(x for x in (guard, t.guard) if x)
        push = (top,) + t.push
        w = t.weight if weight is None else weight
        out.append(T(t.src, t.sym, top, push, t.dst, w, g, "i+=1"))
    return out


def check_determinism(f: Formal) -> dict:
    """Verifica automáticamente que δ sea determinista.

    Condiciones (definición de DPDA): (1) a lo sumo una transición por
    (q, a, X, guarda); (2) si hay una transición ε desde (q, X), no hay
    transición que lea entrada desde (q, X); (3) una transición que no
    mira la pila (λ) no puede coexistir con otra que sí la mira para el
    mismo (q, a), salvo que sus guardas sean disjuntas. Las guardas
    complementarias ⊨/⊭ y las de registros (i=0, i≥p) se tratan como
    partición del símbolo.
    """
    seen: dict[tuple, T] = {}
    conflicts = []
    eps_keys = set()
    read_keys = set()
    for t in f.trans:
        key = (t.src, t.sym, t.pop, t.guard)
        if key in seen and seen[key] != t:
            conflicts.append(f"δ({t.src}, {t.sym}, {t.pop}) [{t.guard}]: "
                             f"{seen[key].dst} / {t.dst}")
        seen[key] = t
        if t.sym == "ε":
            eps_keys.add((t.src, t.pop))
        else:
            read_keys.add((t.src, t.pop))
    for k in eps_keys:
        if k in read_keys or (k[0], None) in read_keys:
            conflicts.append(f"ε y lectura desde ({k[0]}, {k[1]})")
    lam = {(t.src, t.sym) for t in f.trans if t.pop is None}
    for t in f.trans:
        if t.pop is not None and (t.src, t.sym) in lam:
            conflicts.append(f"λ y tope {t.pop} para ({t.src}, {t.sym})")
    return {"deterministic": not conflicts, "conflicts": conflicts}


# ======================================================================
# Ejecución paso a paso de δ (traza para la sustentación y la web)
# ======================================================================
def abstract_tokens(ir: dict, tokens) -> list:
    """Tokens JSON -> símbolos de Σ, con su valor (para las guardas)."""
    from .numbers import parse_number
    out = []
    stack = []  # 'O' / 'A' para saber si un STRING es clave
    expect_key = False
    for t in tokens:
        k = t.kind
        if k == "STRING" and expect_key:
            out.append(("KEY", t))
            expect_key = False
            continue
        if k == "{":
            stack.append("O")
            expect_key = True
        elif k == "[":
            stack.append("A")
        elif k in ("}", "]"):
            stack.pop()
        elif k == "," and stack and stack[-1] == "O":
            expect_key = True
        if k == "}":
            expect_key = False
        if k == "NUMBER":
            k = "INT" if parse_number(t.value).is_integer() else "FRAC"
        out.append((k, t))
    return out
