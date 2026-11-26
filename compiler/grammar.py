"""Gramáticas libres de contexto y construcción de tablas LL(1).

Fase 2 del proyecto. Una gramática se escribe como texto en notación
BNF simple y este módulo calcula, sin librerías externas:

- FIRST(α) para cada símbolo y cadena de símbolos,
- FOLLOW(A) para cada no terminal,
- la tabla predictiva M[A, a] y sus conflictos (si hay un conflicto,
  la gramática NO es LL(1) y la construcción falla con un mensaje
  que nombra la celda en conflicto).

Las gramáticas del proyecto (JSON, XML bien formado, metalenguaje de
esquemas y expresiones regulares de ``pattern``) viven en
:mod:`compiler.grammars` y se verifican LL(1) en las pruebas.

Notación:
    A -> B c D | ε
Los no terminales empiezan con mayúscula y no están entre comillas;
los terminales son nombres de token (en mayúsculas, p. ej. ``STRING``)
o lexemas entre comillas simples (p. ej. ``'{'``). ``ε`` (o ``eps``)
es la cadena vacía. ``$`` es el fin de entrada.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EPS = "ε"
END = "$"


@dataclass(frozen=True)
class Production:
    head: str
    body: tuple[str, ...]  # vacío = ε

    def __str__(self) -> str:
        return f"{self.head} → {' '.join(self.body) if self.body else EPS}"


@dataclass
class Grammar:
    name: str
    start: str
    productions: list[Production]
    nonterminals: list[str] = field(default_factory=list)
    terminals: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @classmethod
    def parse(cls, name: str, text: str) -> "Grammar":
        prods: list[Production] = []
        order: list[str] = []
        head = None
        for raw in text.strip().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            words = line.split()
            if len(words) >= 2 and words[1] == "->":
                head = words[0]
                if head not in order:
                    order.append(head)
                words = words[2:]
            elif words[0] == "|" and head is not None:
                words = words[1:]  # continuación de la línea anterior
            else:
                raise ValueError(f"línea de gramática inválida: {raw!r}")
            alt: list[str] = []
            for w in words + ["|"]:
                if w == "|":  # separador de alternativas (no citado)
                    syms = tuple(s for s in alt if s not in (EPS, "eps"))
                    prods.append(Production(head, syms))
                    alt = []
                elif len(w) >= 3 and w[0] == "'" and w[-1] == "'":
                    alt.append(w[1:-1])  # terminal citado, p. ej. '|'
                else:
                    alt.append(w)
        nts = order
        terms: list[str] = []
        for p in prods:
            for s in p.body:
                if s not in nts and s not in terms:
                    terms.append(s)
        g = cls(name, nts[0], prods, nts, terms)
        g._check()
        return g

    def _check(self) -> None:
        for p in self.productions:
            for s in p.body:
                if s[0].isupper() and s not in self.nonterminals and \
                        not s.isupper():
                    raise ValueError(f"no terminal sin definir: {s}")

    def is_nonterminal(self, s: str) -> bool:
        return s in self.nonterminals

    def alternatives(self, head: str) -> list[Production]:
        return [p for p in self.productions if p.head == head]

    # ------------------------------------------------------------------
    def first_sets(self) -> dict[str, set[str]]:
        first: dict[str, set[str]] = {a: set() for a in self.nonterminals}
        changed = True
        while changed:
            changed = False
            for p in self.productions:
                before = len(first[p.head])
                first[p.head] |= self.first_of(p.body, first)
                if len(first[p.head]) != before:
                    changed = True
        return first

    def first_of(self, symbols, first=None) -> set[str]:
        """FIRST de una cadena de símbolos (incluye ε si es anulable)."""
        if first is None:
            first = self.first_sets()
        out: set[str] = set()
        for s in symbols:
            if not self.is_nonterminal(s):
                out.add(s)
                return out
            out |= first[s] - {EPS}
            if EPS not in first[s]:
                return out
        out.add(EPS)
        return out

    def follow_sets(self, first=None) -> dict[str, set[str]]:
        if first is None:
            first = self.first_sets()
        follow: dict[str, set[str]] = {a: set() for a in self.nonterminals}
        follow[self.start].add(END)
        changed = True
        while changed:
            changed = False
            for p in self.productions:
                for i, s in enumerate(p.body):
                    if not self.is_nonterminal(s):
                        continue
                    rest = self.first_of(p.body[i + 1:], first)
                    before = len(follow[s])
                    follow[s] |= rest - {EPS}
                    if EPS in rest:
                        follow[s] |= follow[p.head]
                    if len(follow[s]) != before:
                        changed = True
        return follow

    def ll1_table(self):
        """(tabla, conflictos). tabla[(A, a)] = Production."""
        first = self.first_sets()
        follow = self.follow_sets(first)
        table: dict[tuple[str, str], Production] = {}
        conflicts: list[str] = []
        for p in self.productions:
            f = self.first_of(p.body, first)
            targets = (f - {EPS}) | (follow[p.head] if EPS in f else set())
            for a in sorted(targets):
                key = (p.head, a)
                if key in table and table[key] != p:
                    conflicts.append(
                        f"M[{p.head}, {a}]: '{table[key]}' vs '{p}'")
                else:
                    table[key] = p
        return table, conflicts

    def is_ll1(self) -> bool:
        return not self.ll1_table()[1]

    # ------------------------------------------------------------------
    def to_markdown(self) -> str:
        first = self.first_sets()
        follow = self.follow_sets(first)
        lines = [f"### Gramática {self.name}", "", "```"]
        for nt in self.nonterminals:
            alts = [" ".join(p.body) or EPS for p in self.alternatives(nt)]
            lines.append(f"{nt} → " + " | ".join(alts))
        lines += ["```", "", "| No terminal | FIRST | FOLLOW |",
                  "| --- | --- | --- |"]
        for nt in self.nonterminals:
            fs = ", ".join(f"`{x}`" for x in sorted(first[nt]))
            fo = ", ".join(f"`{x}`" for x in sorted(follow[nt]))
            lines.append(f"| {nt} | {fs} | {fo} |")
        table, conflicts = self.ll1_table()
        lines += ["", f"Celdas de la tabla LL(1): {len(table)}; "
                  f"conflictos: {len(conflicts)}"
                  + (" (la gramática es LL(1))." if not conflicts else ".")]
        return "\n".join(lines)

    def table_markdown(self) -> str:
        table, _ = self.ll1_table()
        terms = [t for t in self.terminals] + [END]
        used = [t for t in terms if any((nt, t) in table
                                        for nt in self.nonterminals)]
        head = "| M | " + " | ".join(f"`{t}`" for t in used) + " |"
        sep = "| --- " * (len(used) + 1) + "|"
        rows = [head, sep]
        for nt in self.nonterminals:
            cells = []
            for t in used:
                p = table.get((nt, t))
                cells.append(("→ " + (" ".join(p.body) or EPS)) if p else "")
            rows.append(f"| {nt} | " + " | ".join(cells) + " |")
        return "\n".join(rows)


# ----------------------------------------------------------------------
@dataclass
class ParseNode:
    """Nodo del árbol de derivación (existe solo en compilación)."""

    symbol: str
    children: list["ParseNode"] = field(default_factory=list)
    token: object = None  # el token, si es una hoja terminal

    def is_leaf(self) -> bool:
        return not self.children

    def leaves(self):
        if self.token is not None:
            yield self
        for c in self.children:
            yield from c.leaves()

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)

    def to_text(self, indent: str = "") -> str:
        label = self.symbol
        if self.token is not None:
            v = getattr(self.token, "value", None)
            if v is not None and self.symbol in ("STRING", "NUMBER", "TEXT",
                                                 "TAG_OPEN", "TAG_CLOSE",
                                                 "TAG_EMPTY"):
                label += f" «{v}»"
        elif not self.children:
            label += " → ε"
        lines = [indent + label]
        for c in self.children:
            lines.append(c.to_text(indent + "  "))
        return "\n".join(lines)


class ParseError(ValueError):
    def __init__(self, message: str, offset: int):
        super().__init__(f"{message} (byte {offset})")
        self.offset = offset


class LL1Parser:
    """Analizador predictivo dirigido por tabla, con pila explícita.

    Es el algoritmo de libro (pila de símbolos, mira el token actual,
    expande con M[A, a] o empareja un terminal). Construye el árbol de
    derivación por la izquierda. ``kind_of(token)`` devuelve el nombre
    de terminal del token.
    """

    def __init__(self, grammar: Grammar, kind_of=lambda t: t.kind):
        self.grammar = grammar
        table, conflicts = grammar.ll1_table()
        if conflicts:
            raise ValueError(f"{grammar.name} no es LL(1): {conflicts}")
        self.table = table
        self.kind_of = kind_of
        self.steps = 0

    def parse(self, tokens, end_offset: int = 0, trace=None) -> ParseNode:
        g = self.grammar
        root = ParseNode(g.start)
        stack: list[tuple[str, ParseNode | None]] = [(END, None),
                                                     (g.start, root)]
        toks = list(tokens)
        i = 0
        self.steps = 0

        def current():
            if i < len(toks):
                return self.kind_of(toks[i]), toks[i]
            return END, None

        while stack:
            sym, node = stack.pop()
            kind, tok = current()
            self.steps += 1
            if trace is not None:
                trace.append((
                    [s for s, _ in reversed(stack)] + [sym], kind))
            if sym == END:
                if kind != END:
                    raise ParseError(f"se esperaba fin de entrada, llegó {kind}",
                                     tok.offset)
                return root
            if not g.is_nonterminal(sym):
                if sym != kind:
                    off = tok.offset if tok is not None else end_offset
                    raise ParseError(f"se esperaba {sym}, llegó {kind}", off)
                node.token = tok
                i += 1
                continue
            prod = self.table.get((sym, kind))
            if prod is None:
                off = tok.offset if tok is not None else end_offset
                expected = sorted(a for (h, a) in self.table if h == sym)
                raise ParseError(
                    f"{sym}: token inesperado {kind}; se esperaba uno de "
                    f"{', '.join(expected)}", off)
            children = [ParseNode(s) for s in prod.body]
            node.children = children
            for s, c in reversed(list(zip(prod.body, children))):
                stack.append((s, c))
        raise ParseError("pila vacía antes del fin", end_offset)
