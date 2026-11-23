"""Exportación de autómatas: JFLAP (.jff), DOT, Mermaid y SVG.

Autómatas que se exportan (todos se generan desde el código, así que lo
que se dibuja es lo que se ejecuta):

- AFD del lexer: número JSON, cadena JSON, literales, UTF-8.
- DPDA sintáctico de JSON (el nodo ⊤ de δ, tabla SYN del runtime C).
- WDPDA de un esquema (δ de :mod:`compiler.formal`), con pesos.
- AFD del IR: ``pattern``, trie de claves/enum, modelo de contenido XSD.

Convenciones JFLAP: los AFD usan caracteres reales (se pueden probar
con *Input → Multiple Run*). Para los PDA sobre tokens se usa un
carácter por clase de token (ver ``TOKEN_CHARS``); el ``.jff`` de un
esquema omite las transiciones de peso 1 (JFLAP no tiene pesos ni
registros): acepta los payloads cuyo *tipo* coincide con el esquema.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

TOKEN_CHARS = {"{": "{", "}": "}", "[": "[", "]": "]", ":": ":", ",": ",",
               "STRING": "s", "INT": "i", "FRAC": "d", "TRUE": "t",
               "FALSE": "f", "NULL": "u", "KEY(*)": "k"}


@dataclass
class Auto:
    name: str
    kind: str  # "fa" | "pda"
    states: list[str]
    initial: str
    finals: set[str]
    trans: list[tuple] = field(default_factory=list)
    # fa: (src, label, dst) ; pda: (src, read, pop, push, dst, label)
    notes: str = ""

    def add(self, *t):
        self.trans.append(t)


# ======================================================================
# AFD del lexer (Fase 1), con caracteres reales
# ======================================================================
DIG = "0123456789"


def lexer_number_dfa() -> Auto:
    a = Auto("AFD de número JSON", "fa",
             ["inicio", "menos", "cero", "entero", "punto", "frac", "exp",
              "signo_exp", "dig_exp"], "inicio",
             {"cero", "entero", "frac", "dig_exp"})
    a.add("inicio", "-", "menos")
    a.add("inicio", "0", "cero")
    a.add("menos", "0", "cero")
    for d in DIG[1:]:
        a.add("inicio", d, "entero")
        a.add("menos", d, "entero")
    for d in DIG:
        a.add("entero", d, "entero")
        a.add("punto", d, "frac")
        a.add("frac", d, "frac")
        a.add("exp", d, "dig_exp")
        a.add("signo_exp", d, "dig_exp")
        a.add("dig_exp", d, "dig_exp")
    for s in ("cero", "entero"):
        a.add(s, ".", "punto")
    for s in ("cero", "entero", "frac"):
        a.add(s, "e", "exp")
        a.add(s, "E", "exp")
    a.add("exp", "+", "signo_exp")
    a.add("exp", "-", "signo_exp")
    a.notes = ("Acepta exactamente la gramática regular de número de RFC 8259."
               " Rechaza 01, 1., .5, 1e, -, +1.")
    return a


def lexer_string_dfa() -> Auto:
    a = Auto("AFD de cadena JSON (alfabeto reducido)", "fa",
             ["q0", "dentro", "escape", "u1", "u2", "u3", "u4", "fin"], "q0",
             {"fin"})
    a.add("q0", '"', "dentro")
    for ch in "abcxyz019 ":
        a.add("dentro", ch, "dentro")
    a.add("dentro", "\\", "escape")
    a.add("dentro", '"', "fin")
    for ch in '"\\/bfnrt':
        a.add("escape", ch, "dentro")
    a.add("escape", "u", "u1")
    for src, dst in (("u1", "u2"), ("u2", "u3"), ("u3", "u4"),
                     ("u4", "dentro")):
        for h in "0123456789abcdefABCDEF":
            a.add(src, h, dst)
    a.notes = ("'dentro' acepta cualquier byte UTF-8 >= 0x20 salvo \" y \\ "
               "(aquí se dibuja un alfabeto reducido: a,b,c,x,y,z,0,1,9, "
               "espacio). Los pares sustitutos \\uD8xx\\uDCxx los valida una "
               "acción semántica.")
    return a


def lexer_literal_dfa() -> Auto:
    a = Auto("AFD de literales true/false/null", "fa", ["q0"], "q0", set())
    for word in ("true", "false", "null"):
        prev = "q0"
        for i in range(1, len(word) + 1):
            s = word[:i]
            if s not in a.states:
                a.states.append(s)
            a.add(prev, word[i - 1], s)
            prev = s
        a.finals.add(word)
    return a


def utf8_dfa() -> Auto:
    """AFD de validación UTF-8 sobre clases de bytes (símbolos abstractos)."""
    a = Auto("AFD de validación UTF-8 (clases de bytes)", "fa",
             ["ok", "c1", "c2", "c3", "e0", "ed", "f0", "f4"], "ok", {"ok"})
    a.add("ok", "A", "ok")      # A = 00..7F
    a.add("ok", "B", "c1")      # B = C2..DF
    a.add("ok", "C", "c2")      # C = E1..EC, EE..EF
    a.add("ok", "D", "e0")      # D = E0
    a.add("ok", "E", "ed")      # E = ED
    a.add("ok", "F", "c3")      # F = F1..F3
    a.add("ok", "G", "f0")      # G = F0
    a.add("ok", "H", "f4")      # H = F4
    for k in ("x", "y", "z"):   # x = 80..8F, y = 90..9F, z = A0..BF
        a.add("c1", k, "ok")
        a.add("c2", k, "c1")
        a.add("c3", k, "c2")
    a.add("e0", "z", "c1")
    for k in ("x", "y"):
        a.add("ed", k, "c1")
    for k in ("y", "z"):
        a.add("f0", k, "c2")
    a.add("f4", "x", "c2")
    a.notes = ("Clases: A=00-7F, B=C2-DF, C=E1-EC|EE-EF, D=E0, E=ED, F=F1-F3, "
               "G=F0, H=F4, x=80-8F, y=90-9F, z=A0-BF. Cualquier otra "
               "combinación (C0, C1, F5-FF, continuación suelta, sobrelarga, "
               "sustituto, > U+10FFFF) no tiene transición: rechazo.")
    return a


# ======================================================================
# Autómatas de pila desde δ
# ======================================================================
def formal_auto(formal, name: str, only_top: bool = False,
                drop_weighted: bool = True, collapse_top: bool = False
                ) -> Auto:
    """δ explícito -> Auto (pda). ``only_top`` = DPDA sintáctico de JSON.

    ``collapse_top`` (solo para dibujar) reemplaza el subautómata genérico
    ⊤ por un único nodo "⊤ (cualquier JSON)" con sus entradas y salidas.
    """
    if collapse_top:
        from .formal import T as _T

        def cz(s):
            return "⊤ (cualquier JSON)" if "⊤" in s else s

        seen = set()
        trans = []
        for t in formal.trans:
            s2, d2 = cz(t.src), cz(t.dst)
            if "⊤" in s2 and "⊤" in d2:
                continue
            if "⊤" in t.src and t.dst == "R":
                continue
            key = (s2, t.sym, d2, t.weight)
            if key in seen:
                continue
            seen.add(key)
            trans.append(_T(s2, t.sym, t.pop if "⊤" not in (t.pop or "")
                            else None, tuple(x for x in t.push if "⊤" not in x),
                            d2, t.weight, t.guard, t.action))

        class _F:
            pass
        ff = _F()
        ff.trans = trans
        ff.start = formal.start
        formal = ff
    keep = []
    for t in formal.trans:
        if only_top and not (t.src.endswith("(⊤)") or t.src.startswith(
                "C(⊤") or t.src in ("R", "F")):
            continue
        if only_top and t.pop not in (None, "O⊤", "A⊤", "Z0"):
            continue
        if drop_weighted and t.weight not in (0, "0"):
            continue
        keep.append(t)
    states = []
    for t in keep:
        for s in (t.src, t.dst):
            if s not in states:
                states.append(s)
    init = "V(⊤)" if only_top else formal.start
    if init in states:
        states.remove(init)
    states.insert(0, init)
    a = Auto(name, "pda", states, init, {"F"})
    for t in keep:
        read = "" if t.sym == "ε" else t.sym
        pop = t.pop or ""
        push = tuple(t.push)
        label = t.label()
        a.add(t.src, read, pop, push, t.dst, label, t.weight)
    return a


def dfa_auto(d: dict, name: str, symbols: list[str] | None = None) -> Auto:
    """AFD del IR (tabla densa) -> Auto con etiquetas por rangos."""
    from .automata import _ranges_label
    n = d["states"]
    a = Auto(name, "fa", [f"q{i}" for i in range(n)], "q0",
             {f"q{i}" for i in range(n) if d["accept"][i] >= 0})
    for s in range(n):
        by: dict[int, list[int]] = {}
        for b in range(256):
            t = d["table"][s * d["ncls"] + d["classes"][b]]
            if t >= 0:
                by.setdefault(t, []).append(b)
        for t, bs in sorted(by.items()):
            if symbols is not None:
                lab = ",".join(symbols[b] for b in bs if b < len(symbols))
                if not lab:
                    continue
            else:
                lab = _ranges_label(bs)
            a.add(f"q{s}", lab, f"q{t}")
    return a


# ======================================================================
# Escritores
# ======================================================================
def _layout(a: Auto) -> dict[str, tuple[float, float]]:
    """Capas por distancia BFS desde el inicial (para JFLAP y SVG)."""
    adj: dict[str, list[str]] = {s: [] for s in a.states}
    for t in a.trans:
        src, dst = t[0], t[2] if a.kind == "fa" else t[4]
        adj.setdefault(src, []).append(dst)
    level = {a.initial: 0}
    order = [a.initial]
    i = 0
    while i < len(order):
        for v in adj.get(order[i], []):
            if v not in level:
                level[v] = level[order[i]] + 1
                order.append(v)
        i += 1
    for s in a.states:
        level.setdefault(s, max(level.values(), default=0) + 1)
    cols: dict[int, list[str]] = {}
    for s in a.states:
        cols.setdefault(level[s], []).append(s)
    pos = {}
    for lv, ss in cols.items():
        for j, s in enumerate(ss):
            pos[s] = (80 + 170 * lv, 80 + 120 * j + (lv % 2) * 40)
    return pos


def to_jff(a: Auto, path: str | Path) -> Path:
    path = Path(path)
    pos = _layout(a)
    ids = {s: i for i, s in enumerate(a.states)}
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
           f"<!-- {escape(a.name)} — generado por compiler/export.py -->"]
    if a.notes:
        out.append(f"<!-- {escape(a.notes)} -->")
    out += ["<structure>", f"\t<type>{a.kind}</type>", "\t<automaton>"]
    for s in a.states:
        x, y = pos[s]
        out.append(f'\t\t<state id="{ids[s]}" name="{escape(s)}">')
        out.append(f"\t\t\t<x>{x:.1f}</x>")
        out.append(f"\t\t\t<y>{y:.1f}</y>")
        if s == a.initial:
            out.append("\t\t\t<initial/>")
        if s in a.finals:
            out.append("\t\t\t<final/>")
        out.append("\t\t</state>")
    for t in a.trans:
        if a.kind == "fa":
            src, lab, dst = t[:3]
            reads = lab.split(",") if len(lab) > 1 and "," in lab else [lab]
            for r in reads:
                out += ["\t\t<transition>", f"\t\t\t<from>{ids[src]}</from>",
                        f"\t\t\t<to>{ids[dst]}</to>",
                        f"\t\t\t<read>{escape(r)}</read>", "\t\t</transition>"]
        else:
            src, read, pop, push, dst = t[:5]
            r = _jflap_sym(read)
            p = _jflap_stack(pop)
            u = "".join(_jflap_stack(x) for x in push)
            out += ["\t\t<transition>", f"\t\t\t<from>{ids[src]}</from>",
                    f"\t\t\t<to>{ids[dst]}</to>",
                    f"\t\t\t<read>{escape(r)}</read>" if r else "\t\t\t<read/>",
                    f"\t\t\t<pop>{escape(p)}</pop>" if p else "\t\t\t<pop/>",
                    f"\t\t\t<push>{escape(u)}</push>" if u else "\t\t\t<push/>",
                    "\t\t</transition>"]
    out += ["\t</automaton>", "</structure>"]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


_KEY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXY"
_STACK_CHARS: dict[str, str] = {}
_KEYMAP: dict[str, str] = {}


def _jflap_sym(sym: str) -> str:
    if not sym:
        return ""
    if sym in TOKEN_CHARS:
        return TOKEN_CHARS[sym]
    if sym.startswith("KEY("):
        k = sym[4:-1]
        if k not in _KEYMAP:
            _KEYMAP[k] = _KEY_CHARS[len(_KEYMAP) % len(_KEY_CHARS)]
        return _KEYMAP[k]
    return sym[:1]


def _jflap_stack(x: str) -> str:
    if not x:
        return ""
    if x == "Z0":
        return "Z"
    if x == "O⊤":
        return "O"
    if x == "A⊤":
        return "A"
    if x not in _STACK_CHARS:
        pool = "abcdeghjmopqrvwxy0123456789αβγδεζηθικμνξπρστφχψω"
        _STACK_CHARS[x] = pool[len(_STACK_CHARS) % len(pool)]
    return _STACK_CHARS[x]


def jflap_legend() -> str:
    lines = ["Símbolos de entrada (tokens): " + ", ".join(
        f"{v}={k}" for k, v in TOKEN_CHARS.items())]
    if _KEYMAP:
        lines.append("Claves: " + ", ".join(f"{v}={k}" for k, v in
                                            _KEYMAP.items()))
    if _STACK_CHARS:
        lines.append("Pila: Z=Z0, O=O⊤, A=A⊤, " + ", ".join(
            f"{v}={k}" for k, v in _STACK_CHARS.items()))
    return "\n".join(lines)


def to_dot(a: Auto, weights: bool = True) -> str:
    out = [f'digraph "{a.name}" {{', "  rankdir=LR;",
           '  node [shape=circle fontname="Helvetica" fontsize=11];',
           '  edge [fontname="Helvetica" fontsize=9];',
           '  __start [shape=point];', f'  __start -> "{a.initial}";']
    for s in a.states:
        shape = "doublecircle" if s in a.finals else "circle"
        out.append(f'  "{s}" [shape={shape}];')
    merged: dict[tuple[str, str], list[str]] = {}
    for t in a.trans:
        if a.kind == "fa":
            src, lab, dst = t[:3]
        else:
            src, read, pop, push, dst, lab = t[:6]
            w = t[6] if len(t) > 6 else 0
            lab = (read or "ε") + "," + (pop or "λ") + "/" + ("".join(push) or "λ")
            if weights and w not in (0, "0"):
                lab += f" | w={w}"
        merged.setdefault((src, dst), []).append(lab)
    for (src, dst), labs in merged.items():
        lab = "\\n".join(labs[:6]) + ("\\n…" if len(labs) > 6 else "")
        color = ' color="#c0392b" fontcolor="#c0392b"' if "w=" in lab and \
            "w=0" not in lab else ""
        out.append(f'  "{src}" -> "{dst}" [label="{_dot_esc(lab)}"{color}];')
    out.append("}")
    return "\n".join(out)


def _dot_esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\\\\n", "\\n")


def to_mermaid(a: Auto, max_edges: int = 80) -> str:
    ids = {s: f"s{i}" for i, s in enumerate(a.states)}
    out = ["stateDiagram-v2", "    direction LR"]
    for s in a.states:
        out.append(f'    {ids[s]} : {s}')
    out.append(f"    [*] --> {ids[a.initial]}")
    merged: dict[tuple[str, str], list[str]] = {}
    for t in a.trans:
        if a.kind == "fa":
            src, lab, dst = t[:3]
        else:
            src, read, pop, push, dst = t[:5]
            lab = f"{read or 'ε'}, {pop or 'λ'}/{''.join(push) or 'λ'}"
        merged.setdefault((src, dst), []).append(lab)
    for k, ((src, dst), labs) in enumerate(merged.items()):
        if k >= max_edges:
            out.append(f"    %% … {len(merged) - max_edges} aristas más")
            break
        lab = " | ".join(labs)
        if len(lab) > 60:
            lab = lab[:57] + "…"
        lab = lab.replace(":", "∶").replace(";", "，")
        out.append(f"    {ids[src]} --> {ids[dst]} : {lab}")
    for s in a.finals:
        if s in ids:
            out.append(f"    {ids[s]} --> [*]")
    return "\n".join(out)


def to_svg(a: Auto, path: str | Path, weights: bool = True,
           figsize=None) -> Path:
    """Dibujo del autómata con matplotlib (sin Graphviz)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyArrowPatch

    pos = _nice_layout(a)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    w = (max(xs) - min(xs) + 200) / 100
    h = (max(ys) - min(ys) + 200) / 100
    fig, ax = plt.subplots(figsize=figsize or (max(6, w * 1.1),
                                               max(3, h * 1.1)))
    ax.set_aspect("equal")
    ax.axis("off")
    R = 24
    merged: dict[tuple[str, str], list[str]] = {}
    wmap: dict[tuple[str, str], bool] = {}
    for (src, dst), lab, heavy in _grouped_labels(a, weights):
        merged.setdefault((src, dst), []).append(lab)
        wmap[(src, dst)] = wmap.get((src, dst), False) or heavy
    for (src, dst), labs in merged.items():
        (x1, y1), (x2, y2) = pos[src], pos[dst]
        lab = "; ".join(labs)
        if len(lab) > 46:
            lab = lab[:44] + "…"
        color = "#c0392b" if wmap[(src, dst)] else "#34495e"
        if src == dst:
            ax.add_patch(FancyArrowPatch((x1 - 10, y1 - R + 4),
                                         (x1 + 10, y1 - R + 4),
                                         connectionstyle="arc3,rad=-2.2",
                                         arrowstyle="-|>", mutation_scale=10,
                                         color=color, lw=0.9))
            ax.text(x1, y1 - R - 32, lab, ha="center", fontsize=6,
                    color=color)
            continue
        rad = 0.25 if (dst, src) in merged else 0.08
        dx, dy = x2 - x1, y2 - y1
        d = math.hypot(dx, dy) or 1
        p1 = (x1 + dx / d * R, y1 + dy / d * R)
        p2 = (x2 - dx / d * R, y2 - dy / d * R)
        ax.add_patch(FancyArrowPatch(p1, p2, connectionstyle=f"arc3,rad={rad}",
                                     arrowstyle="-|>", mutation_scale=10,
                                     color=color, lw=0.9))
        mx, my = (x1 + x2) / 2 - dy * rad / 2, (y1 + y2) / 2 + dx * rad / 2
        ax.text(mx, my, lab, ha="center", va="center", fontsize=6,
                color=color, bbox=dict(boxstyle="round,pad=0.15", fc="white",
                                       ec="none", alpha=0.85))
    for s, (x, y) in pos.items():
        ax.add_patch(Circle((x, y), R, fc="#eaf2f8", ec="#2c3e50", lw=1.2))
        if s in a.finals:
            ax.add_patch(Circle((x, y), R - 4, fc="none", ec="#2c3e50",
                                lw=1.0))
        ax.text(x, y, s if len(s) <= 10 else s[:9] + "…", ha="center",
                va="center", fontsize=6.5)
        if s == a.initial:
            ax.add_patch(FancyArrowPatch((x - R - 40, y), (x - R, y),
                                         arrowstyle="-|>", mutation_scale=10,
                                         color="#2c3e50"))
    ax.set_xlim(min(xs) - 90, max(xs) + 90)
    ax.set_ylim(max(ys) + 90, min(ys) - 90)
    ax.set_title(a.name, fontsize=10)
    path = Path(path)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)
    return path


SCALARS = ("STRING", "INT", "FRAC", "TRUE", "FALSE", "NULL")


def _grouped_labels(a: Auto, weights: bool):
    """Etiquetas agrupadas: (src,dst,pop,push,w) -> lecturas; los seis
    escalares juntos se abrevian 'esc'."""
    groups: dict[tuple, list[str]] = {}
    for t in a.trans:
        if a.kind == "fa":
            groups.setdefault((t[0], t[2], None, None, 0), []).append(t[1])
            continue
        src, read, pop, push, dst = t[:5]
        wt = t[6] if len(t) > 6 else 0
        groups.setdefault((src, dst, pop, "".join(push), wt), []).append(
            read or "ε")
    out = []
    for (src, dst, pop, push, wt), reads in groups.items():
        if all(x in reads for x in SCALARS):
            reads = ["esc"] + [r for r in reads if r not in SCALARS]
        r = "|".join(reads) if a.kind == "pda" else ",".join(reads)
        if len(r) > 30:
            r = r[:28] + "…"
        heavy = wt not in (0, "0")
        if a.kind == "pda":
            lab = f"{r}, {pop or 'λ'}/{push or 'λ'}"
            if weights and heavy:
                lab += f" ; w={wt}"
        else:
            lab = r
        out.append(((src, dst), lab, heavy))
    return out


def _nice_layout(a: Auto) -> dict[str, tuple[float, float]]:
    """Layout de fuerzas (Kamada-Kawai) escalado; capas BFS si falla."""
    try:
        import networkx as nx
        g = nx.DiGraph()
        g.add_nodes_from(a.states)
        for t in a.trans:
            dst = t[2] if a.kind == "fa" else t[4]
            if t[0] != dst:
                g.add_edge(t[0], dst)
        try:
            p = nx.kamada_kawai_layout(g.to_undirected())
        except Exception:  # sin scipy: resortes (solo numpy), semilla fija
            init = {s: (float(x), float(y)) for s, (x, y) in
                    _layout(a).items()}
            p = nx.spring_layout(g.to_undirected(), pos=init, seed=7,
                                 iterations=400,
                                 k=2.2 / math.sqrt(max(len(a.states), 1)))
        n = len(a.states)
        scale = 120 * max(2.5, math.sqrt(n) * 1.2)
        return {s: (float(x) * scale, float(y) * scale) for s, (x, y)
                in p.items()}
    except Exception:
        return _layout(a)


def tree_to_dot(node, title: str = "árbol de derivación") -> str:
    """Árbol de derivación (ParseNode) -> DOT."""
    out = [f'digraph "{title}" {{', '  node [fontname="Helvetica" fontsize=10'
           ' shape=box style="rounded"];', '  edge [arrowsize=0.5];']
    counter = [0]

    def walk(n):
        i = counter[0]
        counter[0] += 1
        label = n.symbol
        if n.token is not None and getattr(n.token, "value", None) is not None \
                and n.symbol in ("STRING", "NUMBER", "TEXT", "TAG_OPEN",
                                 "TAG_CLOSE", "TAG_EMPTY"):
            label += f"\\n«{str(n.token.value)[:20]}»"
        elif not n.children and n.token is None:
            label += "\\nε"
        shape = "ellipse" if n.token is not None else "box"
        out.append(f'  n{i} [label="{html.escape(label)}" shape={shape}];')
        for c in n.children:
            j = walk(c)
            out.append(f"  n{i} -> n{j};")
        return i

    walk(node)
    out.append("}")
    return "\n".join(out)
