"""Extensión XSD: subconjunto de XML Schema -> WDPDA para XML.

Tubería (mismas fases que JSON):

1. Léxico: :class:`compiler.xml_lexer.XmlTokenizer` (AFD, streaming,
   sin DTD, entidades predefinidas solamente).
2. Sintaxis: ``XML_GRAMMAR`` (LL(1)) + emparejamiento de nombres con pila
   -> árbol del documento XSD (solo en compilación).
3. Semántica: tabla de símbolos de elementos/tipos globales, resolución
   de ``type=``/``ref=``, tipos simples con facetas, y para cada tipo
   complejo su *modelo de contenido* (``sequence``/``choice`` con
   ``minOccurs``/``maxOccurs``), que es una expresión regular sobre los
   nombres de los hijos.
4. Autómata: el modelo de contenido se compila con la construcción de
   **Glushkov** (posiciones, first/last/follow). XSD exige la regla
   *Unique Particle Attribution* (UPA): que en cada punto haya a lo sumo
   una partícula que pueda consumir un nombre. UPA es exactamente que el
   autómata de Glushkov sea determinista, así que se verifica y, si
   falla, el esquema se rechaza como hacen los validadores reales. El
   AFD resultante se minimiza. La pila del WDPDA lleva, por elemento
   abierto, (declaración, estado del AFD de contenido, máscara de
   atributos vistos).

Subconjunto soportado: ``element`` (name/type/ref/minOccurs/maxOccurs),
``complexType`` (sequence, choice, attribute, mixed, simpleContent con
extension), ``simpleType`` con ``restriction`` (enumeration, pattern,
length, minLength, maxLength, minInclusive, maxInclusive,
minExclusive, maxExclusive) y los tipos predefinidos más comunes.
No soportado (se rechaza con mensaje): ``xs:all``, ``xs:any``, grupos,
``list``/``union``, espacios de nombres de destino múltiples.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .automata import DFA, build_trie, compile_regex, RegexError
from .grammar import LL1Parser, ParseError
from .grammars import XML_GRAMMAR
from .json_lexer import LexerError
from .numbers import Dec, parse_number
from .wdpda import ANY, DEFAULT_LIMITS, IR_VERSION, TYPE_BITS
from .xml_lexer import XmlTokenizer


class XsdError(ValueError):
    pass


# ======================================================================
# Árbol XML (compilación) a partir del parser LL(1)
# ======================================================================
@dataclass
class XNode:
    name: str
    attrs: dict
    children: list = field(default_factory=list)
    text: str = ""
    offset: int = 0

    @property
    def local(self) -> str:
        return self.name.split(":", 1)[-1]


def xml_tokens_for_parse(data: bytes):
    """Tokens XML sin el texto en blanco fuera del elemento raíz."""
    toks = XmlTokenizer().tokenize(data)
    out = []
    depth = 0
    for t in toks:
        if t.kind == "TAG_OPEN":
            depth += 1
        elif t.kind == "TAG_CLOSE":
            depth -= 1
        elif t.kind == "TEXT" and depth == 0:
            if t.value.strip():
                raise XsdError(f"texto fuera del elemento raíz (byte {t.offset})")
            continue
        out.append(t)
    return out


def parse_xml_tree(data: bytes) -> XNode:
    try:
        toks = xml_tokens_for_parse(data)
        tree = LL1Parser(XML_GRAMMAR).parse(toks, end_offset=len(data))
    except (LexerError, ParseError) as exc:
        raise XsdError(f"XML mal formado: {exc}") from exc

    def build(el) -> XNode:
        first = el.children[0]
        tok = first.token
        node = XNode(tok.value, dict(tok.attrs), offset=tok.offset)
        if first.symbol == "TAG_EMPTY":
            return node
        content = el.children[1]
        close = el.children[2].token
        if close.value != tok.value:
            raise XsdError(f"etiqueta <{tok.value}> cerrada con "
                           f"</{close.value}> (byte {close.offset})")
        texts = []
        while content.children:
            item = content.children[0].children[0]
            if item.symbol == "Element":
                node.children.append(build(item))
            elif item.symbol == "TEXT":
                texts.append(item.token.value)
            content = content.children[1]
        node.text = "".join(texts)
        return node

    root_el = tree.children[1]
    return build(root_el)


# ======================================================================
# Tipos simples
# ======================================================================
BUILTIN = {
    "string": ("string", {}), "normalizedString": ("string", {}),
    "token": ("string", {}), "anyURI": ("string", {}),
    "ID": ("string", {}), "IDREF": ("string", {}), "NCName": ("string", {}),
    "language": ("string", {}),
    "boolean": ("boolean", {}),
    "decimal": ("decimal", {}), "float": ("double", {}),
    "double": ("double", {}),
    "integer": ("integer", {}),
    "long": ("integer", {"minInclusive": "-9223372036854775808",
                         "maxInclusive": "9223372036854775807"}),
    "int": ("integer", {"minInclusive": "-2147483648",
                        "maxInclusive": "2147483647"}),
    "short": ("integer", {"minInclusive": "-32768", "maxInclusive": "32767"}),
    "byte": ("integer", {"minInclusive": "-128", "maxInclusive": "127"}),
    "nonNegativeInteger": ("integer", {"minInclusive": "0"}),
    "positiveInteger": ("integer", {"minInclusive": "1"}),
    "nonPositiveInteger": ("integer", {"maxInclusive": "0"}),
    "negativeInteger": ("integer", {"maxInclusive": "-1"}),
    "unsignedInt": ("integer", {"minInclusive": "0",
                                "maxInclusive": "4294967295"}),
    "date": ("string", {"pattern": [lambda: DATE_RE + TZ_RE]}),
    "dateTime": ("string", {"pattern": [lambda: DATE_RE + "T" + TIME_RE +
                                        TZ_RE]}),
    "time": ("string", {"pattern": [lambda: TIME_RE + TZ_RE]}),
}

# La validez de una fecha del calendario gregoriano, AÑOS BISIESTOS
# incluidos, es un lenguaje REGULAR: "divisible por 4" depende solo de
# los dos últimos dígitos y "divisible por 400" de los dos primeros
# cuando los dos últimos son 00. Por eso xs:date se compila a un AFD
# exacto (sin calcular nada en tiempo de ejecución).
_YEAR = r"-?([1-9][0-9]{3}|0[1-9][0-9]{2}|00[1-9][0-9]|000[1-9])"
_MD = (r"((0[13578]|1[02])-(0[1-9]|[12][0-9]|3[01])"
       r"|(0[469]|11)-(0[1-9]|[12][0-9]|30)"
       r"|02-(0[1-9]|1[0-9]|2[0-8]))")
_LEAP = (r"-?([0-9]{2}(0[48]|[2468][048]|[13579][26])"
         r"|(0[48]|[2468][048]|[13579][26])00)-02-29")
DATE_RE = f"({_YEAR}-{_MD}|{_LEAP})"
TIME_RE = r"(([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]+)?|24:00:00(\.0+)?)"
TZ_RE = r"(Z|[+-]((0[0-9]|1[0-3]):[0-5][0-9]|14:00))?"
def _resolve_facets(facets: dict) -> dict:
    """Copia las facetas de un tipo predefinido (los patrones se evalúan
    tarde porque DATE_RE se define después de BUILTIN)."""
    out = {}
    for k, v in facets.items():
        if isinstance(v, list):
            out[k] = [x() if callable(x) else x for x in v]
        else:
            out[k] = v
    return out


XSD_KIND_CODE = {"string": 0, "boolean": 1, "integer": 2, "decimal": 3,
                 "double": 4}


def parse_xsd_number(text: str, kind: str) -> Dec | None:
    """Lexema XSD (integer/decimal/double) -> Dec, o None si no es válido.

    Espacios iniciales/finales permitidos (whiteSpace=collapse). A
    diferencia de JSON, XSD admite ``+``, ceros iniciales y ``.5``.
    """
    s = text.strip(" \t\r\n")
    if not s:
        return None
    i = 0
    neg = False
    if s[0] in "+-":
        neg = s[0] == "-"
        i = 1
    ip = ""
    while i < len(s) and s[i].isdigit() and s[i].isascii():
        ip += s[i]
        i += 1
    fp = ""
    if kind != "integer" and i < len(s) and s[i] == ".":
        i += 1
        while i < len(s) and s[i].isdigit() and s[i].isascii():
            fp += s[i]
            i += 1
    if not ip and not fp:
        return None
    exp = ""
    if kind == "double" and i < len(s) and s[i] in "eE":
        i += 1
        sign = ""
        if i < len(s) and s[i] in "+-":
            sign = s[i]
            i += 1
        while i < len(s) and s[i].isdigit() and s[i].isascii():
            exp += s[i]
            i += 1
        if not exp:
            return None
        exp = sign + exp
    if i != len(s):
        return None
    lex = ("-" if neg else "") + (ip or "0") + (("." + fp) if fp else "") + \
        (("e" + exp) if exp else "")
    return parse_number(lex)


# ======================================================================
# Modelo de contenido -> Glushkov
# ======================================================================
# AST: ("sym", s) | ("cat", [..]) | ("alt", [..]) | ("star", x) | ("opt", x)
#      | ("eps",)
def _unroll(node, lo: int, hi: int | None):
    if lo > 100 or (hi or 0) > 100:
        raise XsdError("minOccurs/maxOccurs mayor que 100 (riesgo de "
                       "explosión de estados al desplegar)")
    items = [node] * lo
    if hi is None:
        items.append(("star", node))
    elif hi > lo:
        # x{0,3} se despliega ANIDADO: (x (x (x)?)?)? — mismo lenguaje que
        # x? x? x?, pero este último NO es determinista en Glushkov (tras
        # leer una x no se sabe qué posición la consumió) y aquel sí.
        tail = ("opt", node)
        for _ in range(hi - lo - 1):
            tail = ("opt", ("cat", [node, tail]))
        items.append(tail)
    if not items:
        return ("eps",)
    return items[0] if len(items) == 1 else ("cat", items)


def glushkov(ast, nsyms: int):
    """(DFA, deterministic, conflicts). Construcción de Glushkov."""
    pos_sym: list[int] = []

    def lin(n):
        k = n[0]
        if k == "sym":
            pos_sym.append(n[1])
            return ("p", len(pos_sym) - 1)
        if k in ("cat", "alt"):
            return (k, [lin(x) for x in n[1]])
        if k in ("star", "opt"):
            return (k, lin(n[1]))
        return ("eps",)

    L = lin(ast)
    follow: list[set[int]] = [set() for _ in pos_sym]

    def info(n):
        """(nullable, first, last)."""
        k = n[0]
        if k == "p":
            return False, {n[1]}, {n[1]}
        if k == "eps":
            return True, set(), set()
        if k == "alt":
            nul, fi, la = False, set(), set()
            for x in n[1]:
                a, b, c = info(x)
                nul |= a
                fi |= b
                la |= c
            return nul, fi, la
        if k == "cat":
            nul, fi, la = True, set(), set()
            for x in n[1]:
                a, b, c = info(x)
                for p in la:
                    follow[p] |= b
                if nul:
                    fi |= b
                la = c | (la if a else set())
                nul = nul and a
            return nul, fi, la
        a, b, c = info(n[1])
        if k == "star":
            for p in c:
                follow[p] |= b
            return True, b, c
        return True, b, c  # opt

    nullable, first, last = info(L)
    conflicts = []

    def check(src: str, ps: set[int]):
        by: dict[int, list[int]] = {}
        for p in ps:
            by.setdefault(pos_sym[p], []).append(p)
        for s, lst in by.items():
            if len(lst) > 1:
                conflicts.append((src, s))

    check("inicio", first)
    for p, f in enumerate(follow):
        check(f"posición {p}", f)
    # Estados: 0 = inicial, p+1 = "acabo de leer la posición p".
    n = len(pos_sym) + 1
    trans: list[dict[int, int]] = [dict() for _ in range(n)]
    for p in first:
        trans[0][pos_sym[p]] = p + 1
    for p, f in enumerate(follow):
        for q in f:
            trans[p + 1][pos_sym[q]] = q + 1
    tags = [-1] * n
    if nullable:
        tags[0] = 0
    for p in last:
        tags[p + 1] = 0
    d = DFA(trans, tags, name="content")
    before = d.n
    m = d.minimize()
    m.states_before_min = before
    m.nfa_states = n
    return m, not conflicts, conflicts


# ======================================================================
# Compilador XSD
# ======================================================================
class XsdCompiler:
    def __init__(self, tree: XNode, limits: dict):
        if tree.local != "schema":
            raise XsdError("la raíz debe ser <xs:schema>")
        self.tree = tree
        self.limits = limits
        self.g_elements = {c.attrs.get("name"): c for c in tree.children
                           if c.local == "element"}
        self.g_complex = {c.attrs.get("name"): c for c in tree.children
                          if c.local == "complexType"}
        self.g_simple = {c.attrs.get("name"): c for c in tree.children
                         if c.local == "simpleType"}
        for c in tree.children:
            if c.local not in ("element", "complexType", "simpleType",
                               "annotation", "import", "include"):
                raise XsdError(f"xs:{c.local} global no soportado")
        self.elements: list[dict] = []
        self.el_memo: dict[int, int] = {}
        self.nodes: list[dict] = []
        self.simple_memo: dict[str, int] = {}
        self.dfas: list[dict] = []
        self.dfa_index: dict = {}
        self.consts: list[dict] = []
        self.const_index: dict = {}
        self.upa: list[dict] = []
        self.classification: dict[str, int] = {}

    # -- utilidades compartidas con el IR JSON ------------------------
    def _dfa(self, kind, key, make) -> int:
        k = (kind,) + tuple(key)
        if k in self.dfa_index:
            return self.dfa_index[k]
        d = make()
        classes, ncls, table = d.dense()
        self.dfa_index[k] = len(self.dfas)
        self.dfas.append({"id": len(self.dfas), "kind": kind,
                          "source": d.source or list(key)[:20],
                          "states": d.n,
                          "states_before_min": d.states_before_min or d.n,
                          "transitions": d.transitions_count(),
                          "classes": classes, "ncls": ncls, "table": table,
                          "accept": d.tag})
        return self.dfa_index[k]

    def _trie(self, kind, words):
        return self._dfa(kind, words, lambda: build_trie(
            [w.encode("utf-8") for w in words], name=kind))

    def _const(self, d: Dec) -> int:
        key = str(d)
        if key not in self.const_index:
            self.const_index[key] = len(self.consts)
            self.consts.append({"lexeme": key, "neg": d.neg,
                                "digits": d.digits, "exp": d.exp})
        return self.const_index[key]

    def _use(self, kw: str):
        self.classification[kw] = self.classification.get(kw, 0) + 1

    # -- tipos simples -------------------------------------------------
    def simple_type(self, ref: str | None, inline: XNode | None,
                    where: str) -> int:
        """Devuelve el id de un nodo IR para el contenido de texto."""
        if inline is not None:
            return self._simple_from_xnode(inline, where)
        if ref is None:
            ref = "xs:string"
        local = ref.split(":", 1)[-1]
        prefix = ref.split(":", 1)[0] if ":" in ref else ""
        if prefix not in ("xs", "xsd") and local in self.g_simple:
            key = "named:" + local
            if key not in self.simple_memo:
                self.simple_memo[key] = self._simple_from_xnode(
                    self.g_simple[local], f"simpleType {local}")
            return self.simple_memo[key]
        if local not in BUILTIN:
            raise XsdError(f"tipo desconocido {ref!r} en {where}")
        key = "builtin:" + local
        if key not in self.simple_memo:
            kind, facets = BUILTIN[local]
            self.simple_memo[key] = self._make_simple(
                kind, _resolve_facets(facets), f"xs:{local}")
        return self.simple_memo[key]

    def _simple_from_xnode(self, st: XNode, where: str) -> int:
        restr = [c for c in st.children if c.local == "restriction"]
        if not restr:
            raise XsdError(f"{where}: solo se soporta xs:restriction "
                           "(no list/union)")
        r = restr[0]
        base = r.attrs.get("base", "xs:string").split(":", 1)[-1]
        if base in BUILTIN:
            kind, facets = BUILTIN[base]
            facets = _resolve_facets(facets)
        elif base in self.g_simple:
            # Restricción de un tipo con nombre: se combinan facetas.
            inner = self.g_simple[base]
            ir = [c for c in inner.children if c.local == "restriction"][0]
            kind, facets = BUILTIN.get(ir.attrs.get("base", "xs:string")
                                       .split(":", 1)[-1], ("string", {}))
            facets = _resolve_facets(facets)
            facets.update(self._facets(ir))
        else:
            raise XsdError(f"{where}: base desconocida {base!r}")
        facets.update(self._facets(r))
        return self._make_simple(kind, facets, where)

    def _facets(self, r: XNode) -> dict:
        f: dict = {}
        for c in r.children:
            v = c.attrs.get("value")
            self._use(c.local)
            if c.local == "enumeration":
                f.setdefault("enumeration", []).append(v)
            elif c.local == "pattern":
                f.setdefault("pattern", []).append(v)
            elif c.local in ("length", "minLength", "maxLength",
                             "minInclusive", "maxInclusive", "minExclusive",
                             "maxExclusive"):
                f[c.local] = v
            elif c.local in ("whiteSpace", "annotation", "totalDigits",
                             "fractionDigits"):
                pass
            else:
                raise XsdError(f"faceta no soportada xs:{c.local}")
        return f

    def _make_simple(self, kind: str, f: dict, where: str) -> int:
        node = {"id": len(self.nodes), "ptr": where, "title": None,
                "desc": f"xs:{kind}", "false": False,
                "types": TYPE_BITS["string"], "xsd": XSD_KIND_CODE[kind],
                "enum": None, "str": None, "num": None, "obj": None,
                "arr": None, "all_of": [], "any_of": [], "one_of": [],
                "not": ANY, "if": ANY, "then": ANY, "else": ANY, "checks": []}
        self.nodes.append(node)
        if "enumeration" in f:
            vals = f["enumeration"]
            if kind in ("integer", "decimal", "double"):
                decs = []
                for v in vals:
                    d = parse_xsd_number(v, kind)
                    if d is None:
                        raise XsdError(f"{where}: enumeración {v!r} no es "
                                       f"un {kind}")
                    decs.append(self._const(d))
                node["enum"] = {"strings": -1, "values": [], "nums": decs,
                                "lits": 0}
            else:
                node["enum"] = {"strings": self._trie("enum", vals),
                                "values": vals, "nums": [], "lits": 0}
        lo = f.get("minLength", f.get("length"))
        hi = f.get("maxLength", f.get("length"))
        pats = f.get("pattern", [])
        if lo is not None or hi is not None or pats:
            ids = []
            for p in pats:
                anchored = f"^(?:{p})$"  # los patrones XSD son anclados
                try:
                    ids.append(self._dfa("pattern", (anchored,),
                                         lambda a=anchored: compile_regex(a)))
                except RegexError as exc:
                    raise XsdError(f"{where}: patrón no soportado {p!r}: "
                                   f"{exc}") from exc
            node["str"] = {"min_len": int(lo) if lo is not None else -1,
                           "max_len": int(hi) if hi is not None else -1,
                           "patterns": ids, "pattern_src": list(pats)}
        checks = []
        for facet, op in (("minInclusive", "ge"), ("minExclusive", "gt"),
                          ("maxInclusive", "le"), ("maxExclusive", "lt")):
            if facet in f:
                d = parse_xsd_number(f[facet], "double")
                if d is None:
                    raise XsdError(f"{where}: {facet} no numérico")
                checks.append([op, self._const(d)])
        if checks:
            if kind not in ("integer", "decimal", "double"):
                raise XsdError(f"{where}: facetas de rango en tipo no "
                               "numérico")
            node["num"] = {"checks": checks}
        return node["id"]

    # -- elementos ------------------------------------------------------
    def element(self, el: XNode) -> int:
        """Compila una declaración de elemento (global o local)."""
        if "ref" in el.attrs:
            name = el.attrs["ref"].split(":", 1)[-1]
            if name not in self.g_elements:
                raise XsdError(f"ref a elemento no declarado {name!r}")
            el = self.g_elements[name]
        key = id(el)
        if key in self.el_memo:
            return self.el_memo[key]
        name = el.attrs.get("name")
        if not name:
            raise XsdError("elemento sin name")
        eid = len(self.elements)
        decl = {"id": eid, "name": name, "simple": -1, "complex": False,
                "mixed": False, "content_dfa": -1, "child_names": [],
                "child_elems": [], "names_dfa": -1, "attrs": [],
                "attr_dfa": -1, "attr_required": 0, "content_src": ""}
        self.elements.append(decl)
        self.el_memo[key] = eid
        self._use("element")
        ct_inline = [c for c in el.children if c.local == "complexType"]
        st_inline = [c for c in el.children if c.local == "simpleType"]
        tref = el.attrs.get("type")
        if ct_inline:
            self._complex(decl, ct_inline[0])
        elif st_inline:
            decl["simple"] = self.simple_type(None, st_inline[0],
                                              f"elemento {name}")
        elif tref and tref.split(":", 1)[-1] in self.g_complex and \
                tref.split(":")[0] not in ("xs", "xsd"):
            self._complex(decl, self.g_complex[tref.split(":", 1)[-1]])
        else:
            decl["simple"] = self.simple_type(tref, None, f"elemento {name}")
        return eid

    def _complex(self, decl: dict, ct: XNode) -> None:
        self._use("complexType")
        decl["complex"] = True
        decl["mixed"] = ct.attrs.get("mixed") == "true"
        attrs = []
        particle = None
        for c in ct.children:
            if c.local in ("sequence", "choice"):
                particle = c
            elif c.local == "attribute":
                attrs.append(c)
            elif c.local == "simpleContent":
                ext = [x for x in c.children
                       if x.local in ("extension", "restriction")]
                if not ext:
                    raise XsdError("simpleContent sin extension")
                decl["complex"] = False
                decl["simple"] = self.simple_type(ext[0].attrs.get("base"),
                                                  None, decl["name"])
                attrs += [x for x in ext[0].children if x.local == "attribute"]
            elif c.local in ("all", "any", "group", "attributeGroup",
                             "complexContent", "anyAttribute"):
                raise XsdError(f"xs:{c.local} no está en el subconjunto")
            elif c.local == "annotation":
                pass
        for a in attrs:
            self._use("attribute")
            an = a.attrs.get("name")
            if not an:
                raise XsdError("atributo sin name")
            inline = [x for x in a.children if x.local == "simpleType"]
            node = self.simple_type(a.attrs.get("type"),
                                    inline[0] if inline else None,
                                    f"atributo {an}")
            decl["attrs"].append({"name": an, "node": node,
                                  "required": a.attrs.get("use") ==
                                  "required"})
        if len(decl["attrs"]) > 64:
            raise XsdError("más de 64 atributos en un elemento")
        if decl["attrs"]:
            decl["attr_dfa"] = self._trie("attrs",
                                          [a["name"] for a in decl["attrs"]])
            decl["attr_required"] = sum(1 << i for i, a in
                                        enumerate(decl["attrs"])
                                        if a["required"])
        if not decl["complex"]:
            return
        if particle is None:
            decl["content_dfa"] = -1  # contenido vacío
            decl["content_src"] = "ε"
            return
        names: list[str] = []
        elems: list[int] = []

        def sym_of(el: XNode) -> int:
            eid = self.element(el)
            nm = self.elements[eid]["name"]
            if nm in names:
                k = names.index(nm)
                if elems[k] != eid:
                    # "Element Declarations Consistent": mismo nombre,
                    # misma declaración dentro de un modelo de contenido.
                    a, b = self.elements[elems[k]], self.elements[eid]
                    if (a["simple"], a["complex"]) != (b["simple"],
                                                       b["complex"]):
                        raise XsdError(f"dos declaraciones distintas de "
                                       f"<{nm}> en un mismo modelo")
                return k
            names.append(nm)
            elems.append(eid)
            return len(names) - 1

        def occurs(x: XNode):
            lo = int(x.attrs.get("minOccurs", "1"))
            mx = x.attrs.get("maxOccurs", "1")
            hi = None if mx == "unbounded" else int(mx)
            if hi is not None and hi < lo:
                raise XsdError("maxOccurs < minOccurs")
            if mx == "unbounded" or hi != 1 or lo != 1:
                self._use("minOccurs/maxOccurs")
            return lo, hi

        def ast(x: XNode, src: list):
            lo, hi = occurs(x)
            if x.local == "element":
                s = sym_of(x)
                src.append(names[s])
                base = ("sym", s)
            elif x.local in ("sequence", "choice"):
                self._use(x.local)
                subs = []
                parts = []
                for c in x.children:
                    if c.local == "annotation":
                        continue
                    if c.local not in ("element", "sequence", "choice"):
                        raise XsdError(f"xs:{c.local} no soportado dentro de "
                                       f"xs:{x.local}")
                    sp: list = []
                    subs.append(ast(c, sp))
                    parts.append("".join(sp))
                sep = " " if x.local == "sequence" else " | "
                src.append("(" + sep.join(parts) + ")")
                if not subs:
                    base = ("eps",)
                else:
                    base = ("cat" if x.local == "sequence" else "alt", subs)
            else:
                raise XsdError(f"partícula xs:{x.local} no soportada")
            if (lo, hi) != (1, 1):
                q = {(0, None): "*", (1, None): "+", (0, 1): "?"}.get(
                    (lo, hi), f"{{{lo},{'' if hi is None else hi}}}")
                src.append(q)
            return _unroll(base, lo, hi)

        src: list[str] = []
        content_ast = ast(particle, src)
        if len(names) > 255:
            raise XsdError("más de 255 nombres de hijo en un modelo")
        dfa, det, conflicts = glushkov(content_ast, len(names))
        decl["content_src"] = "".join(src)
        if not det:
            raise XsdError(
                f"el modelo de contenido de <{decl['name']}> viola Unique "
                f"Particle Attribution (Glushkov no determinista: "
                f"<{names[conflicts[0][1]]}> ambiguo en {conflicts[0][0]})")
        self.upa.append({"element": decl["name"], "deterministic": det,
                         "positions": dfa.nfa_states - 1,
                         "states": dfa.n})
        decl["child_names"] = names
        decl["child_elems"] = elems
        decl["names_dfa"] = self._trie("names", names)
        cls, ncls, table = dfa.dense()
        self.dfa_index[("content", decl["id"])] = len(self.dfas)
        decl["content_dfa"] = len(self.dfas)
        self.dfas.append({"id": len(self.dfas), "kind": "content",
                          "source": decl["content_src"], "states": dfa.n,
                          "states_before_min": dfa.states_before_min,
                          "transitions": dfa.transitions_count(),
                          "classes": cls, "ncls": ncls, "table": table,
                          "accept": dfa.tag, "symbols": names})

    def build(self) -> dict:
        roots = [self.element(el) for el in self.g_elements.values()]
        if not roots:
            raise XsdError("el esquema no declara elementos globales")
        root_names = [self.elements[r]["name"] for r in roots]
        ir = {
            "ir_version": IR_VERSION, "format": "xml",
            "root": roots[0], "roots": roots,
            "root_names_dfa": self._trie("names", root_names),
            "elements": self.elements, "nodes": self.nodes,
            "dfas": self.dfas, "consts": self.consts,
            "limits": dict(self.limits), "flags": {"uses_unique": False},
            "classification": [
                {"keyword": k, "category": _xsd_category(k), "count": v,
                 "strategy": _XSD_STRATEGY.get(k, ""), "pointers": []}
                for k, v in sorted(self.classification.items())],
            "warnings": [], "upa": self.upa,
            "stats": {"elements": len(self.elements),
                      "simple_types": len(self.nodes),
                      "dfa_count": len(self.dfas),
                      "dfa_states": sum(d["states"] for d in self.dfas),
                      "dfa_states_before_min": sum(
                          d["states_before_min"] for d in self.dfas),
                      "nodes_after_minimization": len(self.elements),
                      "max_lane_width": 1},
        }
        ir["formal"] = xml_formal_summary(ir)
        return ir


_XSD_STRATEGY = {
    "element": "símbolo de pila por elemento abierto (push en <e>, pop en "
               "</e>)",
    "complexType": "subautómata con AFD de contenido",
    "sequence": "concatenación en el AFD de Glushkov",
    "choice": "unión en el AFD de Glushkov (UPA => determinista)",
    "minOccurs/maxOccurs": "despliegue acotado (≤100) o estrella",
    "attribute": "trie de nombres + máscara de requeridos (64 bits)",
    "enumeration": "trie de valores / igualdad decimal",
    "pattern": "regex anclada -> AFD mínimo",
    "minLength": "contador saturado", "maxLength": "contador saturado",
    "length": "contador saturado",
    "minInclusive": "comparación decimal exacta",
    "maxInclusive": "comparación decimal exacta",
    "minExclusive": "comparación decimal exacta",
    "maxExclusive": "comparación decimal exacta",
}


def _xsd_category(k: str) -> str:
    if k in ("attribute", "minOccurs/maxOccurs"):
        return "b: registros/contadores acotados"
    if k == "pattern":
        return "c: no determinista o no libre de contexto"
    return "a: directa en el DPDA"


def xml_formal_summary(ir: dict) -> dict:
    """Tamaño de δ para XML: por elemento complejo, un estado por estado
    del AFD de contenido; transiciones = aristas del AFD (cada una apila
    el hijo) + cierres desde estados de aceptación (desapilan)."""
    states = 2  # R y F
    trans = len(ir["roots"]) + 1
    for e in ir["elements"]:
        if e["complex"] and e["content_dfa"] >= 0:
            d = ir["dfas"][e["content_dfa"]]
            states += d["states"]
            trans += d["transitions"] + sum(1 for a in d["accept"] if a >= 0)
            trans += len(e["child_names"])  # retorno ε por hijo
        else:
            states += 1
            trans += 2
    return {"states": states, "transitions": trans, "stack_symbols":
            len(ir["elements"]) + 1, "deterministic": all(
                u["deterministic"] for u in ir.get("upa", [])),
            "determinism_conflicts": [], "product_nodes": [],
            "epsilon_transitions": 0, "violation_transitions": 0,
            "input_symbols": [], "start": "V(raíz)", "final": "F"}


def compile_xsd(data: bytes, limits: dict | None = None) -> dict:
    lim = dict(DEFAULT_LIMITS)
    if limits:
        lim.update(limits)
    return XsdCompiler(parse_xml_tree(data), lim).build()
