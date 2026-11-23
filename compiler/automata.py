"""Autómatas finitos sobre bytes: NFA, DFA, minimización y tries.

Lo usan tres piezas del compilador:

- ``pattern`` (Fase 3, categoría c): la expresión regular se analiza con
  la gramática LL(1) ``REGEX_GRAMMAR``, se convierte en un NFA de
  Thompson **sobre bytes UTF-8**, se determiniza por construcción de
  subconjuntos y se minimiza. Validar una cadena es entonces O(n) en
  bytes, sin retroceso: por construcción no hay ReDoS. El riesgo real se
  traslada a la compilación (la determinización puede crecer
  exponencialmente), así que se fija un tope de estados documentado.
- Claves de ``properties`` y valores de ``enum``: un trie (DFA parcial)
  cuyos estados de aceptación llevan una etiqueta (índice de propiedad
  o de valor), minimizado respetando las etiquetas.
- Diagramas: exportación a DOT y a JFLAP (``.jff``), ver
  :mod:`compiler.export`.

Convención: un DFA parcial usa ``-1`` como estado muerto implícito.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .grammar import LL1Parser, ParseError, ParseNode
from .grammars import REGEX_GRAMMAR

MAX_CP = 0x10FFFF
DEFAULT_MAX_DFA_STATES = 4096
MAX_REPEAT = 100


class RegexError(ValueError):
    pass


# ======================================================================
# Conjuntos de puntos de código -> secuencias de rangos de bytes UTF-8
# ======================================================================
def _encode(cp: int) -> bytes:
    return chr(cp).encode("utf-8", "surrogatepass")


def utf8_ranges(lo: int, hi: int) -> list[list[tuple[int, int]]]:
    """Rangos de bytes que codifican exactamente [lo, hi] en UTF-8.

    Algoritmo clásico (el mismo de RE2 y de ``regex-syntax`` en Rust):
    se parte el intervalo hasta que el primer y el último punto de código
    comparten longitud de codificación y prefijo, y entonces cada byte es
    un rango contiguo. Los sustitutos D800..DFFF se excluyen.
    """
    out: list[list[tuple[int, int]]] = []

    def rec(lo: int, hi: int) -> None:
        if lo > hi:
            return
        if lo <= 0xDFFF and hi >= 0xD800:
            rec(lo, 0xD7FF)
            rec(0xE000, hi)
            return
        for maxv in (0x7F, 0x7FF, 0xFFFF):
            if lo <= maxv < hi:
                rec(lo, maxv)
                rec(maxv + 1, hi)
                return
        if hi <= 0x7F:
            out.append([(lo, hi)])
            return
        n = len(_encode(lo))
        for i in range(1, n):
            m = (1 << (6 * i)) - 1
            if (lo & ~m) != (hi & ~m):
                if (lo & m) != 0:
                    rec(lo, lo | m)
                    rec((lo | m) + 1, hi)
                    return
                if (hi & m) != m:
                    rec(lo, (hi & ~m) - 1)
                    rec(hi & ~m, hi)
                    return
        a, b = _encode(lo), _encode(hi)
        out.append(list(zip(a, b)))

    rec(lo, hi)
    return out


def normalize_ranges(ranges) -> list[tuple[int, int]]:
    rs = sorted((a, b) for a, b in ranges if a <= b)
    out: list[tuple[int, int]] = []
    for a, b in rs:
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def negate_ranges(ranges) -> list[tuple[int, int]]:
    out, cur = [], 0
    for a, b in normalize_ranges(ranges):
        if a > cur:
            out.append((cur, a - 1))
        cur = b + 1
    if cur <= MAX_CP:
        out.append((cur, MAX_CP))
    return out


# ======================================================================
# NFA de Thompson sobre bytes
# ======================================================================
@dataclass
class NFA:
    trans: list[list[tuple[int, int, int]]] = field(default_factory=list)
    eps: list[list[int]] = field(default_factory=list)

    def new(self) -> int:
        self.trans.append([])
        self.eps.append([])
        return len(self.trans) - 1

    def closure(self, states) -> frozenset[int]:
        stack, seen = list(states), set(states)
        while stack:
            s = stack.pop()
            for t in self.eps[s]:
                if t not in seen:
                    seen.add(t)
                    stack.append(t)
        return frozenset(seen)


# Fragmento = (inicio, fin) con un único estado final sin salidas.
def _frag_bytes(nfa: NFA, seqs) -> tuple[int, int]:
    s, f = nfa.new(), nfa.new()
    for seq in seqs:
        cur = s
        for i, (lo, hi) in enumerate(seq):
            nxt = f if i == len(seq) - 1 else nfa.new()
            nfa.trans[cur].append((lo, hi, nxt))
            cur = nxt
    return s, f


def _frag_cpset(nfa: NFA, ranges) -> tuple[int, int]:
    seqs = []
    for a, b in normalize_ranges(ranges):
        seqs.extend(utf8_ranges(a, b))
    if not seqs:  # clase vacía: nunca empareja
        return nfa.new(), nfa.new()
    return _frag_bytes(nfa, seqs)


# ======================================================================
# Lexer y AST de expresiones regulares (subconjunto ECMA-262)
# ======================================================================
@dataclass(frozen=True)
class RTok:
    kind: str
    value: object
    offset: int


_CLASS_ESC = {
    "d": [(0x30, 0x39)],
    "w": [(0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)],
    "s": [(0x09, 0x0D), (0x20, 0x20), (0xA0, 0xA0), (0xFEFF, 0xFEFF),
          (0x2028, 0x2029)],
}
_SIMPLE_ESC = {"n": 0x0A, "t": 0x09, "r": 0x0D, "f": 0x0C, "v": 0x0B,
               "0": 0x00}


def _read_escape(src: str, i: int):
    """Devuelve (rangos, siguiente índice) para el escape en src[i]=='\\'."""
    if i + 1 >= len(src):
        raise RegexError("escape incompleto al final del patrón")
    c = src[i + 1]
    if c in _CLASS_ESC:
        return _CLASS_ESC[c], i + 2
    if c.lower() in _CLASS_ESC and c.isupper():
        return negate_ranges(_CLASS_ESC[c.lower()]), i + 2
    if c in _SIMPLE_ESC:
        v = _SIMPLE_ESC[c]
        return [(v, v)], i + 2
    if c == "u":
        h = src[i + 2:i + 6]
        if len(h) != 4 or any(x not in "0123456789abcdefABCDEF" for x in h):
            raise RegexError("escape \\u inválido en el patrón")
        v = int(h, 16)
        return [(v, v)], i + 6
    if c == "x":
        h = src[i + 2:i + 4]
        if len(h) != 2 or any(x not in "0123456789abcdefABCDEF" for x in h):
            raise RegexError("escape \\x inválido en el patrón")
        v = int(h, 16)
        return [(v, v)], i + 4
    if c.isdigit():
        raise RegexError("retrorreferencias no soportadas (no son regulares)")
    if c in "bB":
        raise RegexError("\\b (frontera de palabra) no soportado")
    if c.isalnum():
        raise RegexError(f"escape desconocido \\{c}")
    v = ord(c)
    return [(v, v)], i + 2


def regex_tokens(src: str) -> list[RTok]:
    toks: list[RTok] = []
    i = 0
    while i < len(src):
        c = src[i]
        if c == "\\":
            rs, j = _read_escape(src, i)
            kind = "CHAR" if len(rs) == 1 and rs[0][0] == rs[0][1] else "CLASS"
            toks.append(RTok(kind, rs, i))
            i = j
        elif c == "[":
            j = i + 1
            neg = False
            if j < len(src) and src[j] == "^":
                neg, j = True, j + 1
            ranges: list[tuple[int, int]] = []
            first = True
            while j < len(src) and (src[j] != "]" or first):
                first = False
                if src[j] == "\\":
                    rs, j = _read_escape(src, j)
                else:
                    rs, j = [(ord(src[j]), ord(src[j]))], j + 1
                if (len(rs) == 1 and rs[0][0] == rs[0][1] and j + 1 < len(src)
                        and src[j] == "-" and src[j + 1] != "]"):
                    if src[j + 1] == "\\":
                        rs2, j2 = _read_escape(src, j + 1)
                    else:
                        rs2, j2 = [(ord(src[j + 1]), ord(src[j + 1]))], j + 2
                    if len(rs2) != 1 or rs2[0][0] != rs2[0][1]:
                        raise RegexError("rango de clase inválido")
                    if rs2[0][0] < rs[0][0]:
                        raise RegexError("rango de clase invertido")
                    ranges.append((rs[0][0], rs2[0][0]))
                    j = j2
                else:
                    ranges.extend(rs)
            if j >= len(src):
                raise RegexError("clase de caracteres sin cerrar")
            ranges = negate_ranges(ranges) if neg else normalize_ranges(ranges)
            toks.append(RTok("CLASS", ranges, i))
            i = j + 1
        elif c == "(":
            if src.startswith("(?:", i):
                toks.append(RTok("(", None, i))
                i += 3
            elif src.startswith("(?", i):
                raise RegexError("lookaround y grupos con nombre no soportados")
            else:
                toks.append(RTok("(", None, i))
                i += 1
        elif c in ")|.*+?":
            toks.append(RTok(c, None, i))
            i += 1
        elif c == "^":
            toks.append(RTok("CARET", None, i))
            i += 1
        elif c == "$":
            toks.append(RTok("DOLLAR", None, i))
            i += 1
        elif c == "{":
            j = src.find("}", i)
            body = src[i + 1:j] if j != -1 else ""
            parts = body.split(",")
            ok = j != -1 and 1 <= len(parts) <= 2 and parts[0].isdigit() and (
                len(parts) == 1 or parts[1] == "" or parts[1].isdigit())
            if not ok:
                toks.append(RTok("CHAR", [(ord(c), ord(c))], i))
                i += 1
                continue
            lo = int(parts[0])
            hi = lo if len(parts) == 1 else (None if parts[1] == ""
                                             else int(parts[1]))
            if hi is not None and hi < lo:
                raise RegexError("cuantificador {m,n} con n < m")
            if lo > MAX_REPEAT or (hi or 0) > MAX_REPEAT:
                raise RegexError(f"cuantificador mayor que {MAX_REPEAT} "
                                 "(riesgo de explosión de estados)")
            toks.append(RTok("QUANT", (lo, hi), i))
            i = j + 1
        else:
            cp = ord(c)
            toks.append(RTok("CHAR", [(cp, cp)], i))
            i += 1
    return toks


_REGEX_PARSER = None


def parse_regex(src: str) -> ParseNode:
    global _REGEX_PARSER
    if _REGEX_PARSER is None:
        _REGEX_PARSER = LL1Parser(REGEX_GRAMMAR)
    try:
        return _REGEX_PARSER.parse(regex_tokens(src), end_offset=len(src))
    except ParseError as exc:
        raise RegexError(f"patrón inválido: {exc}") from exc


# AST: ("cat", [..]) ("alt", [..]) ("rep", node, lo, hi) ("set", ranges)
#      ("bol",) ("eol",) ("eps",)
def _ast(node: ParseNode):
    s = node.symbol
    ch = node.children
    if s == "Regex":
        return _ast(ch[0])
    if s == "Alt":
        first = _ast(ch[0])
        rest = []
        tail = ch[1]
        while tail.children:
            rest.append(_ast(tail.children[1]))
            tail = tail.children[2]
        return ("alt", [first] + rest) if rest else first
    if s == "Concat":
        items = []
        cur = node
        while cur.children:
            items.append(_ast(cur.children[0]))
            cur = cur.children[1]
        if not items:
            return ("eps",)
        return items[0] if len(items) == 1 else ("cat", items)
    if s == "Repeat":
        atom = _ast(ch[0])
        q = ch[1]
        if not q.children:
            return atom
        qs = q.children[0]
        if qs.symbol == "*":
            return ("rep", atom, 0, None)
        if qs.symbol == "+":
            return ("rep", atom, 1, None)
        if qs.symbol == "?":
            return ("rep", atom, 0, 1)
        lo, hi = qs.token.value
        return ("rep", atom, lo, hi)
    if s == "Atom":
        a = ch[0]
        if a.symbol in ("CHAR", "CLASS"):
            return ("set", a.token.value)
        if a.symbol == ".":  # cualquier carácter salvo terminadores de línea
            return ("set", negate_ranges([(0x0A, 0x0A), (0x0D, 0x0D),
                                          (0x2028, 0x2029)]))
        if a.symbol == "(":
            return _ast(ch[1])
        if a.symbol == "CARET":
            return ("bol",)
        if a.symbol == "DOLLAR":
            return ("eol",)
    raise RegexError(f"nodo de regex inesperado: {s}")


def _strip_anchors(ast):
    """Solo se admiten ^ al inicio y $ al final (uso típico en esquemas)."""
    items = list(ast[1]) if ast[0] == "cat" else [ast]
    bol = bool(items) and items[0] == ("bol",)
    if bol:
        items = items[1:]
    eol = bool(items) and items[-1] == ("eol",)
    if eol:
        items = items[:-1]

    def check(n):
        if n in (("bol",), ("eol",)):
            raise RegexError("^ y $ solo se admiten al inicio y al final")
        if n[0] in ("cat", "alt"):
            for x in n[1]:
                check(x)
        elif n[0] == "rep":
            check(n[1])

    for it in items:
        check(it)
    body = ("cat", items) if len(items) != 1 else items[0]
    if not items:
        body = ("eps",)
    return body, bol, eol


def _build(nfa: NFA, ast) -> tuple[int, int]:
    k = ast[0]
    if k == "set":
        return _frag_cpset(nfa, ast[1])
    if k == "eps":
        s, f = nfa.new(), nfa.new()
        nfa.eps[s].append(f)
        return s, f
    if k == "cat":
        s, f = _build(nfa, ast[1][0])
        for sub in ast[1][1:]:
            s2, f2 = _build(nfa, sub)
            nfa.eps[f].append(s2)
            f = f2
        return s, f
    if k == "alt":
        s, f = nfa.new(), nfa.new()
        for sub in ast[1]:
            s2, f2 = _build(nfa, sub)
            nfa.eps[s].append(s2)
            nfa.eps[f2].append(f)
        return s, f
    if k == "rep":
        _, sub, lo, hi = ast
        s = cur = nfa.new()
        for _ in range(lo):
            a, b = _build(nfa, sub)
            nfa.eps[cur].append(a)
            cur = b
        if hi is None:
            a, b = _build(nfa, sub)
            f = nfa.new()
            nfa.eps[cur] += [a, f]
            nfa.eps[b] += [a, f]
            return s, f
        f = nfa.new()
        nfa.eps[cur].append(f)
        for _ in range(hi - lo):
            a, b = _build(nfa, sub)
            nfa.eps[cur].append(a)
            nfa.eps[b].append(f)
            cur = b
        return s, f
    raise RegexError(f"AST inesperado {k}")


# ======================================================================
# DFA
# ======================================================================
@dataclass
class DFA:
    """DFA (posiblemente parcial) sobre bytes.

    ``trans[s]`` es un dict byte -> estado. ``tag[s]`` es la etiqueta
    de aceptación (>= 0) o -1 si el estado no acepta. ``start`` = 0.
    """

    trans: list[dict[int, int]]
    tag: list[int]
    name: str = ""
    source: str = ""
    nfa_states: int = 0
    states_before_min: int = 0

    @property
    def n(self) -> int:
        return len(self.trans)

    def transitions_count(self) -> int:
        return sum(len(t) for t in self.trans)

    def run(self, data: bytes) -> int:
        s = 0
        for b in data:
            s = self.trans[s].get(b, -1)
            if s < 0:
                return -1
        return self.tag[s]

    def accepts(self, data: bytes) -> bool:
        return self.run(data) >= 0

    # -- clases de bytes: bytes indistinguibles en todas las tablas ---
    def byte_classes(self) -> list[int]:
        sig: dict[tuple, int] = {}
        classes = [0] * 256
        for b in range(256):
            key = tuple(t.get(b, -1) for t in self.trans)
            classes[b] = sig.setdefault(key, len(sig))
        return classes

    def dense(self):
        """(clases[256], nclases, tabla[n*nclases]) con -1 = muerto."""
        classes = self.byte_classes()
        ncls = max(classes) + 1
        rep = {}
        for b in range(256):
            rep.setdefault(classes[b], b)
        table = []
        for t in self.trans:
            for c in range(ncls):
                table.append(t.get(rep[c], -1))
        return classes, ncls, table

    # -- minimización (refinamiento de particiones, Moore) -------------
    def minimize(self) -> "DFA":
        """DFA mínimo equivalente (mismo lenguaje y mismas etiquetas).

        Refinamiento de particiones de Moore sobre el DFA parcial
        completado con un estado muerto implícito: la partición inicial
        separa por etiqueta de aceptación; cada ronda separa estados que
        van a bloques distintos con algún byte. Termina cuando la
        partición se estabiliza (a lo sumo n rondas).
        """
        self_reach = self._reachable()
        trans = [self.trans[s] for s in self_reach]
        index = {s: i for i, s in enumerate(self_reach)}
        trans = [{b: index[t] for b, t in tr.items() if t in index}
                 for tr in trans]
        tags = [self.tag[s] for s in self_reach]
        n = len(trans)
        DEAD = n
        classes_bytes = DFA(trans, tags).byte_classes()
        reps = sorted({c: b for b, c in reversed(list(enumerate(
            classes_bytes)))}.values())
        block = [tags[s] + 1 for s in range(n)] + [-1]  # muerto aparte
        while True:
            sigs = {}
            new_block = []
            for s in range(n + 1):
                if s == DEAD:
                    key = (block[s],) + tuple(-1 for _ in reps)
                else:
                    key = (block[s],) + tuple(
                        block[trans[s].get(b, DEAD)] for b in reps)
                new_block.append(sigs.setdefault(key, len(sigs)))
            if len(set(new_block)) == len(set(block)):
                block = new_block
                break
            block = new_block
        dead_block = block[DEAD]
        # Renumerar con el bloque del estado inicial como 0.
        order: dict[int, int] = {}
        queue = [block[0]]
        order[block[0]] = 0
        rep_of = {}
        for s in range(n):
            rep_of.setdefault(block[s], s)
        new_trans: list[dict[int, int]] = []
        new_tags: list[int] = []
        qi = 0
        while qi < len(queue):
            bl = queue[qi]
            qi += 1
            s = rep_of[bl]
            out = {}
            for b, t in trans[s].items():
                tb = block[t]
                if tb == dead_block:
                    continue
                if tb not in order:
                    order[tb] = len(order)
                    queue.append(tb)
                out[b] = order[tb]
            new_trans.append(out)
            new_tags.append(tags[s])
        d = DFA(new_trans, new_tags, self.name, self.source,
                self.nfa_states, self.states_before_min or self.n)
        return d

    def _reachable(self) -> list[int]:
        seen, order = {0}, [0]
        i = 0
        while i < len(order):
            for t in self.trans[order[i]].values():
                if t >= 0 and t not in seen:
                    seen.add(t)
                    order.append(t)
            i += 1
        return order

    def is_universal_sink(self, s: int) -> bool:
        t = self.trans[s]
        return self.tag[s] >= 0 and len(t) == 256 and all(
            v == s for v in t.values())

    # -- etiquetas legibles para diagramas ------------------------------
    def edge_labels(self) -> list[list[tuple[int, str]]]:
        """Por estado: [(destino, etiqueta de rangos de bytes)]."""
        out = []
        for t in self.trans:
            by_target: dict[int, list[int]] = {}
            for b, d in t.items():
                by_target.setdefault(d, []).append(b)
            edges = []
            for d, bs in sorted(by_target.items()):
                edges.append((d, _ranges_label(sorted(bs))))
            out.append(edges)
        return out


def _byte_label(b: int) -> str:
    if 0x21 <= b <= 0x7E and chr(b) not in "\\\"":
        return chr(b)
    return f"\\x{b:02X}"


def _ranges_label(bs: list[int]) -> str:
    if len(bs) == 256:
        return "Σ"
    rs = []
    start = prev = bs[0]
    for b in bs[1:] + [None]:
        if b is not None and b == prev + 1:
            prev = b
            continue
        rs.append((start, prev))
        if b is not None:
            start = prev = b
    if len(bs) > 128:  # más corto expresarlo como complemento
        comp = [b for b in range(256) if b not in set(bs)]
        return "Σ−{" + _ranges_label(comp) + "}" if comp else "Σ"
    parts = [(_byte_label(a) if a == b else
              f"{_byte_label(a)}-{_byte_label(b)}") for a, b in rs]
    return ",".join(parts)


def nfa_to_dfa(nfa: NFA, start: int, final: int,
               max_states: int = DEFAULT_MAX_DFA_STATES,
               absorbing_final: bool = False) -> DFA:
    """Construcción de subconjuntos (determinización).

    Si ``absorbing_final`` es verdadero, cualquier subconjunto que
    contenga el estado final se convierte en un sumidero de aceptación
    (semántica de búsqueda: una vez encontrado el patrón, el resto de la
    cadena no importa).
    """
    s0 = nfa.closure([start])
    index = {s0: 0}
    order = [s0]
    trans: list[dict[int, int]] = []
    tags: list[int] = []
    i = 0
    while i < len(order):
        S = order[i]
        i += 1
        acc = final in S
        tags.append(0 if acc else -1)
        if acc and absorbing_final:
            trans.append({b: i - 1 for b in range(256)})
            continue
        moves: dict[int, set[int]] = {}
        for s in S:
            for lo, hi, t in nfa.trans[s]:
                for b in range(lo, hi + 1):
                    moves.setdefault(b, set()).add(t)
        out = {}
        cache: dict[frozenset, int] = {}
        for b, ts in moves.items():
            key = frozenset(ts)
            if key in cache:
                out[b] = cache[key]
                continue
            T = nfa.closure(ts)
            if T not in index:
                if len(order) >= max_states:
                    raise RegexError(
                        f"la determinización supera {max_states} estados "
                        "(explosión de estados): simplifique el patrón")
                index[T] = len(order)
                order.append(T)
            cache[key] = out[b] = index[T]
        trans.append(out)
    return DFA(trans, tags, nfa_states=len(nfa.trans))


def compile_regex(pattern: str, max_states: int = DEFAULT_MAX_DFA_STATES
                  ) -> DFA:
    """``pattern`` de JSON Schema -> DFA mínimo de búsqueda sobre bytes.

    Semántica ECMA-262 de ``pattern``: la cadena es válida si el patrón
    aparece en alguna posición (búsqueda no anclada), salvo que el
    patrón empiece con ``^`` o termine con ``$``.
    """
    tree = parse_regex(pattern)
    body, bol, eol = _strip_anchors(_ast(tree))
    nfa = NFA()
    s, f = _build(nfa, body)
    if not bol:  # prefijo Σ*
        pre = nfa.new()
        nfa.trans[pre].append((0, 255, pre))
        nfa.eps[pre].append(s)
        s = pre
    dfa = nfa_to_dfa(nfa, s, f, max_states, absorbing_final=not eol)
    dfa.name = "pattern"
    dfa.source = pattern
    before = dfa.n
    m = dfa.minimize()
    m.states_before_min = before
    return m


def build_trie(words: list[bytes], tags: list[int] | None = None,
               name: str = "trie") -> DFA:
    """Trie (DFA parcial) que acepta exactamente ``words`` con etiquetas."""
    if tags is None:
        tags = list(range(len(words)))
    trans: list[dict[int, int]] = [{}]
    tag = [-1]
    for w, t in zip(words, tags):
        s = 0
        for b in w:
            nxt = trans[s].get(b)
            if nxt is None:
                trans.append({})
                tag.append(-1)
                nxt = len(trans) - 1
                trans[s][b] = nxt
            s = nxt
        tag[s] = t
    d = DFA(trans, tag, name=name)
    before = d.n
    m = d.minimize()
    m.name, m.states_before_min = name, before
    return m
