"""Fases 1–3 sobre el esquema: lexer, parser LL(1) y análisis semántico.

Tubería::

    bytes del esquema
      -> SchemaLexer (AFD de JSON + trie de palabras clave)
      -> LL1Parser(SCHEMA_GRAMMAR)      árbol de derivación (compilación)
      -> to_value()                     valor JSON (dicts ordenados)
      -> SchemaAnalyzer                 tabla de símbolos, $ref/$defs,
                                        ciclos no productivos,
                                        meta-esquema y clasificación
      -> SNode (grafo de nodos de esquema, posiblemente cíclico)

El AST (árbol de derivación y grafo de nodos) existe solo en tiempo de
compilación; el validador generado no construye ningún árbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .automata import RegexError, compile_regex
from .grammar import LL1Parser, ParseError, ParseNode
from .grammars import SCHEMA_GRAMMAR, SCHEMA_KEYWORDS, kw
from .json_lexer import JsonTokenizer, LexerError, Token
from .numbers import Dec, as_count, compare, parse_number

JSON_TYPES = ("null", "boolean", "integer", "number", "string", "array",
              "object")


class SchemaError(ValueError):
    """Error de compilación del esquema, con su JSON Pointer."""

    def __init__(self, message: str, pointer: str = "#"):
        super().__init__(f"{message} (en {pointer})")
        self.pointer = pointer
        self.message = message


@dataclass(frozen=True)
class Num:
    """Número del esquema: lexema original + decimal exacto."""

    lexeme: str

    @property
    def dec(self) -> Dec:
        return parse_number(self.lexeme)

    def __repr__(self) -> str:
        return self.lexeme


# ======================================================================
# Fase 1–2: lexer del esquema y parser LL(1)
# ======================================================================
class SchemaLexer:
    """Tokenizador JSON + reconocimiento de palabras clave.

    El reconocimiento de palabras clave es un trie (AFD) sobre el valor
    decodificado de cada STRING; aquí se usa un conjunto de Python por
    claridad, el AFD equivalente se dibuja en la documentación.
    """

    KEYWORDS = frozenset(SCHEMA_KEYWORDS)

    def tokenize(self, data: bytes) -> list[Token]:
        out = []
        for t in JsonTokenizer().tokenize(data):
            if t.kind == "STRING" and t.value in self.KEYWORDS:
                out.append(Token(kw(t.value), t.value, t.offset))
            else:
                out.append(t)
        return out


_PARSER: LL1Parser | None = None


def schema_parser() -> LL1Parser:
    global _PARSER
    if _PARSER is None:
        _PARSER = LL1Parser(SCHEMA_GRAMMAR)
    return _PARSER


def parse_schema_tree(data: bytes) -> ParseNode:
    toks = SchemaLexer().tokenize(data)
    return schema_parser().parse(toks, end_offset=len(data))


def _collect(node: ParseNode, item_fn, tail_sym: str) -> list:
    """X -> item Tail | ε ; Tail -> ',' item Tail | ε  ->  [item...]."""
    out = []
    if not node.children:
        return out
    ch = node.children
    # La primera producción no lleva coma.
    out.append(item_fn(ch))
    tail = ch[-1]
    while tail.children:
        out.append(item_fn(tail.children[1:]))
        tail = tail.children[-1]
    return out


def _str(node: ParseNode) -> str:
    leaf = node.children[0] if node.symbol == "Str" else node
    return leaf.token.value


def to_value(node: ParseNode):
    """Árbol de derivación -> valor (dict ordenado, list, str, Num...)."""
    s = node.symbol
    ch = node.children
    if s in ("Schema", "Value"):
        first = ch[0]
        if first.symbol == "{":
            inner = ch[1]
            if s == "Schema":
                pairs = _collect(inner, lambda c: _kw_pair(c[0]), "KwTail")
            else:
                pairs = _collect(inner, lambda c: (_str(c[0]),
                                                   to_value(c[2])),
                                 "MembersTail")
            return _dict_from_pairs(pairs, first.token.offset)
        if first.symbol == "[":
            return _collect(ch[1], lambda c: to_value(c[0]), "ValueListTail")
        return to_value(first)
    if s == "Str":
        return _str(node)
    if s == "NUMBER":
        return Num(node.token.value)
    if s == "TRUE":
        return True
    if s == "FALSE":
        return False
    if s == "NULL":
        return None
    if s == "Bool":
        return to_value(ch[0])
    if s == "STRING" or s.startswith('"'):
        return node.token.value
    if s == "TypeSpec":
        if ch[0].symbol == "[":
            return _collect(ch[1], lambda c: _str(c[0]), "StrListTail")
        return _str(ch[0])
    raise SchemaError(f"nodo inesperado {s}")


def _kw_pair(kw_node: ParseNode):
    ch = kw_node.children
    key = ch[0].token.value
    rest = ch[2:]
    first = rest[0]
    if first.symbol == "[":
        inner = rest[1]
        if inner.symbol == "StrList":
            val = _collect(inner, lambda c: _str(c[0]), "StrListTail")
        elif inner.symbol == "SchemaList":
            val = _collect(inner, lambda c: to_value(c[0]), "SchemaListTail")
        else:
            val = _collect(inner, lambda c: to_value(c[0]), "ValueListTail")
        return key, val
    if first.symbol == "{":
        inner = rest[1]
        if inner.symbol == "SchemaMap":
            pairs = _collect(inner, lambda c: (_str(c[0]), to_value(c[2])),
                             "SchemaMapTail")
        else:  # DepMap: Str ':' '[' StrList ']'
            pairs = _collect(inner, lambda c: (
                _str(c[0]), _collect(c[3], lambda d: _str(d[0]),
                                     "StrListTail")), "DepMapTail")
        return key, _dict_from_pairs(pairs, first.token.offset)
    return key, to_value(first)


def _dict_from_pairs(pairs, offset: int) -> dict:
    d: dict = {}
    for k, v in pairs:
        if k in d:
            raise SchemaError(f"clave duplicada en el esquema: {k!r} "
                              f"(objeto en el byte {offset})")
        d[k] = v
    return d


def load_schema_value(data: bytes):
    """Bytes -> valor del esquema, pasando por el parser LL(1)."""
    try:
        return to_value(parse_schema_tree(data))
    except (LexerError, ParseError) as exc:
        raise SchemaError(f"esquema mal formado: {exc}") from exc


# ======================================================================
# Fase 3: nodos de esquema
# ======================================================================
@dataclass(eq=False)
class SNode:
    id: int
    pointer: str
    bool_schema: bool | None = None  # True = acepta todo, False = nada
    types: frozenset[str] | None = None  # None = cualquier tipo
    enum: list | None = None  # valores escalares (str, Num, bool, None)
    # cadenas
    min_length: int | None = None
    max_length: int | None = None
    patterns: list[str] = field(default_factory=list)
    # números (Dec)
    minimum: Dec | None = None
    maximum: Dec | None = None
    exclusive_minimum: Dec | None = None
    exclusive_maximum: Dec | None = None
    multiple_of: Dec | None = None
    # objetos
    properties: dict[str, "SNode"] = field(default_factory=dict)
    pattern_properties: list[tuple[str, "SNode"]] = field(
        default_factory=list)
    additional: "SNode | None" = None  # None = cualquiera
    required: list[str] = field(default_factory=list)
    min_properties: int | None = None
    max_properties: int | None = None
    dependent_required: dict[str, list[str]] = field(default_factory=dict)
    dependent_schemas: dict[str, "SNode"] = field(default_factory=dict)
    # arreglos
    prefix_items: list["SNode"] = field(default_factory=list)
    items: "SNode | None" = None
    contains: "SNode | None" = None
    min_contains: int | None = None
    max_contains: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    unique_items: bool = False
    # combinadores (categoría c)
    all_of: list["SNode"] = field(default_factory=list)
    any_of: list["SNode"] = field(default_factory=list)
    one_of: list["SNode"] = field(default_factory=list)
    not_: "SNode | None" = None
    if_: "SNode | None" = None
    then_: "SNode | None" = None
    else_: "SNode | None" = None
    # metadatos
    title: str | None = None

    def is_true(self) -> bool:
        return self.bool_schema is True

    def has_combinators(self) -> bool:
        return bool(self.all_of or self.any_of or self.one_of or self.not_
                    or self.if_ or self.dependent_schemas or self.contains)

    def children(self) -> list["SNode"]:
        out = list(self.properties.values())
        out += [n for _, n in self.pattern_properties]
        for n in (self.additional, self.items, self.contains, self.not_,
                  self.if_, self.then_, self.else_):
            if n is not None:
                out.append(n)
        out += self.prefix_items + self.all_of + self.any_of + self.one_of
        out += list(self.dependent_schemas.values())
        return out

    def tracked_keys(self) -> list[str]:
        """Claves que el autómata del objeto distingue (≤ 64).

        Es el alfabeto de claves del trie: propiedades declaradas,
        requeridas y las que aparecen en dependencias. Las demás claves
        caen en la clase ``otra`` (additionalProperties /
        patternProperties).
        """
        keys: list[str] = []
        for k in list(self.properties) + self.required + list(
                self.dependent_required) + [
                x for v in self.dependent_required.values() for x in v] + \
                list(self.dependent_schemas):
            if k not in keys:
                keys.append(k)
        return keys

    def describe(self) -> str:
        if self.bool_schema is not None:
            return "true" if self.bool_schema else "false"
        parts = []
        if self.types:
            parts.append("|".join(sorted(self.types)))
        if self.enum is not None:
            parts.append(f"enum[{len(self.enum)}]")
        if self.properties:
            parts.append("{" + ",".join(self.properties) + "}")
        if self.required:
            parts.append("req:" + ",".join(self.required))
        if self.items is not None or self.prefix_items:
            parts.append("items")
        for name in ("all_of", "any_of", "one_of"):
            if getattr(self, name):
                parts.append(f"{name}[{len(getattr(self, name))}]")
        return " ".join(parts) or "any"


# Clasificación de palabras clave (Fase 3).
CATEGORY_A = "a: directa en el DPDA"
CATEGORY_B = "b: registros/contadores acotados"
CATEGORY_C = "c: no determinista o no libre de contexto"
CATEGORY_ANN = "anotación (no valida)"

KEYWORD_CLASS: dict[str, tuple[str, str]] = {
    "type": (CATEGORY_A, "transición según la clase del primer token"),
    "properties": (CATEGORY_A, "trie (AFD) de claves -> subautómata hijo"),
    "patternProperties": (CATEGORY_A, "AFD del patrón sobre la clave"),
    "additionalProperties": (CATEGORY_A, "clase 'otra clave' del trie"),
    "items": (CATEGORY_A, "subautómata de elemento"),
    "prefixItems": (CATEGORY_B, "contador de índice saturado"),
    "enum": (CATEGORY_A, "trie de valores / comparación decimal"),
    "const": (CATEGORY_A, "trie de un valor / comparación decimal"),
    "minimum": (CATEGORY_A, "comparación decimal exacta (estado finito)"),
    "maximum": (CATEGORY_A, "comparación decimal exacta (estado finito)"),
    "exclusiveMinimum": (CATEGORY_A, "comparación decimal exacta"),
    "exclusiveMaximum": (CATEGORY_A, "comparación decimal exacta"),
    "multipleOf": (CATEGORY_A, "AFD de residuo módulo m sobre los dígitos"),
    "minLength": (CATEGORY_A, "contador de puntos de código saturado"),
    "maxLength": (CATEGORY_A, "contador de puntos de código saturado"),
    "$ref": (CATEGORY_A, "llamada a subautómata (la recursión usa la pila)"),
    "$defs": (CATEGORY_A, "tabla de símbolos de subautómatas"),
    "definitions": (CATEGORY_A, "tabla de símbolos (draft-07)"),
    "required": (CATEGORY_B, "máscara de bits de 64 en el marco de pila"),
    "minItems": (CATEGORY_B, "contador de elementos saturado"),
    "maxItems": (CATEGORY_B, "contador de elementos saturado"),
    "minProperties": (CATEGORY_B, "contador de miembros saturado"),
    "maxProperties": (CATEGORY_B, "contador de miembros saturado"),
    "dependentRequired": (CATEGORY_B, "máscara de bits: si k ∈ vistas, "
                                      "deps ⊆ vistas"),
    "minContains": (CATEGORY_B, "contador de coincidencias de contains"),
    "maxContains": (CATEGORY_B, "contador de coincidencias de contains"),
    "allOf": (CATEGORY_C, "producto de autómatas (suma de pesos)"),
    "anyOf": (CATEGORY_C, "producto acotado; peso = mín. tropical de ramas"),
    "oneOf": (CATEGORY_C, "producto acotado; exactamente una rama con peso 0"),
    "not": (CATEGORY_C, "producto + complemento del resultado (VPA)"),
    "if": (CATEGORY_C, "producto de 3 ramas; selección diferida al cerrar"),
    "then": (CATEGORY_C, "rama condicionada por if"),
    "else": (CATEGORY_C, "rama condicionada por if"),
    "contains": (CATEGORY_C, "producto por elemento + contador"),
    "dependentSchemas": (CATEGORY_C, "producto con verificación diferida "
                                     "(se decide al cerrar el objeto)"),
    "pattern": (CATEGORY_C, "regex -> NFA -> DFA mínimo (tope de estados, "
                            "sin ReDoS)"),
    "uniqueItems": (CATEGORY_C, "no reconocible por un autómata de pila "
                                "visible (memoria no acotada): hash "
                                "canónico de 64 bits en tabla acotada "
                                "(verificación diferida)"),
    "format": (CATEGORY_ANN, "anotación en 2020-12 (no se valida)"),
}

UNSUPPORTED = {
    "propertyNames": "propertyNames no está en el subconjunto",
    "unevaluatedProperties": "requiere anotaciones dinámicas (fuera del "
                             "subconjunto)",
    "unevaluatedItems": "requiere anotaciones dinámicas (fuera del "
                        "subconjunto)",
    "$dynamicRef": "referencias dinámicas fuera del subconjunto",
    "$dynamicAnchor": "referencias dinámicas fuera del subconjunto",
    "$recursiveRef": "referencias dinámicas fuera del subconjunto",
    "dependencies": "use dependentRequired / dependentSchemas (2020-12)",
}

ANNOTATIONS = {"$schema", "$id", "$comment", "$anchor", "title",
               "description", "default", "examples", "deprecated",
               "readOnly", "writeOnly", "contentMediaType",
               "contentEncoding", "contentSchema", "$vocabulary"}

# Palabras clave cuyos valores son subesquemas que validan la MISMA
# instancia (no consumen estructura): una arista de este tipo en un
# ciclo es un ciclo no productivo (análogo a la recursión por la
# izquierda en una gramática: expandir no avanza la entrada).
NON_CONSUMING = {"$ref", "allOf", "anyOf", "oneOf", "not", "if", "then",
                 "else", "dependentSchemas"}


def _escape_ptr(s: str) -> str:
    return s.replace("~", "~0").replace("/", "~1")


def _unescape_ptr(s: str) -> str:
    return s.replace("~1", "/").replace("~0", "~")


@dataclass
class Analysis:
    root: SNode
    nodes: list[SNode]
    classification: list[dict]
    warnings: list[str]
    symbols: dict[str, str]  # puntero -> descripción
    raw: object


class SchemaAnalyzer:
    """Análisis semántico: meta-esquema, $ref, ciclos, clasificación."""

    def __init__(self, raw):
        self.raw = raw
        self.table: dict[str, object] = {}  # tabla de símbolos
        self.anchors: dict[str, str] = {}
        self.nodes: list[SNode] = []
        self.memo: dict[str, SNode] = {}
        self.usage: dict[str, list[str]] = {}
        self.warnings: list[str] = []

    # -- tabla de símbolos ---------------------------------------------
    def _register(self, value, ptr: str) -> None:
        self.table[ptr] = value
        if not isinstance(value, dict):
            if not isinstance(value, bool):
                raise SchemaError("un subesquema debe ser objeto o booleano",
                                  ptr)
            return
        anchor = value.get("$anchor")
        if isinstance(anchor, str):
            self.anchors["#" + anchor] = ptr
        for k, v in value.items():
            p = f"{ptr}/{_escape_ptr(k)}"
            if k in ("additionalProperties", "items", "contains", "not",
                     "if", "then", "else", "propertyNames"):
                self._register(v, p)
            elif k in ("properties", "patternProperties", "$defs",
                       "definitions", "dependentSchemas"):
                if not isinstance(v, dict):
                    raise SchemaError(f"{k} debe ser un objeto", p)
                for name, sub in v.items():
                    self._register(sub, f"{p}/{_escape_ptr(name)}")
            elif k in ("allOf", "anyOf", "oneOf", "prefixItems"):
                if not isinstance(v, list) or (not v and k != "prefixItems"):
                    raise SchemaError(f"{k} debe ser un arreglo no vacío", p)
                for i, sub in enumerate(v):
                    self._register(sub, f"{p}/{i}")

    def resolve(self, ref: str, at: str) -> str:
        if ref in self.anchors:
            return self.anchors[ref]
        if not ref.startswith("#"):
            raise SchemaError(f"$ref externo no soportado: {ref!r} (solo "
                              "referencias locales '#...')", at)
        # Los punteros de la tabla ya están escapados (~0, ~1), igual que
        # el texto de un $ref local; solo se decodifica %XX de URI.
        from urllib.parse import unquote
        target = ref if ref in self.table else unquote(ref)
        if target not in self.table:
            raise SchemaError(f"$ref sin destino: {ref!r}", at)
        return target

    # -- ciclos no productivos -----------------------------------------
    def _check_cycles(self) -> None:
        edges: dict[str, list[str]] = {p: [] for p in self.table}
        for ptr, v in self.table.items():
            if not isinstance(v, dict):
                continue
            if isinstance(v.get("$ref"), str):
                edges[ptr].append(self.resolve(v["$ref"], ptr))
            for k in ("allOf", "anyOf", "oneOf"):
                for i in range(len(v.get(k, []) or [])):
                    edges[ptr].append(f"{ptr}/{k}/{i}")
            for k in ("not", "if", "then", "else"):
                if k in v:
                    edges[ptr].append(f"{ptr}/{k}")
            for name in (v.get("dependentSchemas") or {}):
                edges[ptr].append(
                    f"{ptr}/dependentSchemas/{_escape_ptr(name)}")
        # Tarjan iterativo (componentes fuertemente conexas).
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on: set[str] = set()
        st: list[str] = []
        counter = [0]
        for start in edges:
            if start in index:
                continue
            work = [(start, 0)]
            while work:
                v, i = work.pop()
                if i == 0:
                    index[v] = low[v] = counter[0]
                    counter[0] += 1
                    st.append(v)
                    on.add(v)
                recurse = False
                for j in range(i, len(edges[v])):
                    w = edges[v][j]
                    if w not in index:
                        work.append((v, j + 1))
                        work.append((w, 0))
                        recurse = True
                        break
                    if w in on:
                        low[v] = min(low[v], index[w])
                if recurse:
                    continue
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = st.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1 or v in edges[v]:
                        raise SchemaError(
                            "ciclo no productivo de $ref/combinadores: "
                            + " -> ".join(sorted(comp))
                            + " (la expansión no consume entrada, como una "
                              "recursión por la izquierda)", v)
                if work:
                    u, _ = work[-1]
                    low[u] = min(low[u], low[v])

    # -- meta-esquema y descenso ----------------------------------------
    def _count(self, v, k: str, ptr: str) -> int:
        if not isinstance(v, Num) or as_count(v.dec) is None:
            raise SchemaError(f"{k} debe ser un entero no negativo", ptr)
        return as_count(v.dec)

    def _num(self, v, k: str, ptr: str) -> Dec:
        if not isinstance(v, Num):
            raise SchemaError(f"{k} debe ser un número", ptr)
        return v.dec

    def _scalar(self, v, ptr: str):
        if isinstance(v, (dict, list)):
            raise SchemaError("enum/const con objetos o arreglos no está en "
                              "el subconjunto (solo escalares)", ptr)
        return v

    def lower(self, ptr: str) -> SNode:
        if ptr in self.memo:
            return self.memo[ptr]
        v = self.table[ptr]
        if isinstance(v, dict) and isinstance(v.get("$ref"), str):
            sib = [k for k in v if k not in ANNOTATIONS
                   and k not in ("$ref", "$defs", "definitions")]
            self._use("$ref", ptr)
            if not sib:  # alias puro: el mismo subautómata
                target = self.resolve(v["$ref"], ptr)
                if "$defs" in v or "definitions" in v:
                    self._use("$defs", ptr)
                node = self.lower(target)
                self.memo[ptr] = node
                return node
        node = SNode(len(self.nodes), ptr)
        self.nodes.append(node)
        self.memo[ptr] = node
        if isinstance(v, bool):
            node.bool_schema = v
            return node
        self._fill(node, v, ptr)
        return node

    def _use(self, k: str, ptr: str) -> None:
        self.usage.setdefault(k, []).append(ptr)

    def _fill(self, n: SNode, v: dict, ptr: str) -> None:
        sub = lambda k: self.lower(f"{ptr}/{_escape_ptr(k)}")  # noqa
        for k, val in v.items():
            p = f"{ptr}/{_escape_ptr(k)}"
            if k in UNSUPPORTED:
                raise SchemaError(f"{k}: {UNSUPPORTED[k]}", p)
            if k in ANNOTATIONS:
                if k == "title" and isinstance(val, str):
                    n.title = val
                continue
            if k not in KEYWORD_CLASS:
                self.warnings.append(f"palabra clave desconocida ignorada "
                                     f"(anotación): {k} en {ptr}")
                continue
            self._use(k, ptr)
            if k == "type":
                ts = [val] if isinstance(val, str) else val
                if not isinstance(ts, list) or not ts:
                    raise SchemaError("type debe ser cadena o arreglo", p)
                for t in ts:
                    if t not in JSON_TYPES:
                        raise SchemaError(f"tipo desconocido: {t!r}", p)
                if len(set(ts)) != len(ts):
                    raise SchemaError("type con tipos repetidos", p)
                n.types = frozenset(ts)
            elif k == "enum":
                if not val:
                    raise SchemaError("enum no puede estar vacío", p)
                n.enum = [self._scalar(x, p) for x in val]
            elif k == "const":
                n.enum = [self._scalar(val, p)] if n.enum is None else \
                    [x for x in n.enum if _jeq(x, val)]
            elif k in ("minLength", "maxLength"):
                setattr(n, "min_length" if k == "minLength" else
                        "max_length", self._count(val, k, p))
            elif k == "pattern":
                if not isinstance(val, str):
                    raise SchemaError("pattern debe ser una cadena", p)
                try:
                    compile_regex(val)
                except RegexError as exc:
                    raise SchemaError(f"pattern no soportado: {exc}", p)
                n.patterns.append(val)
            elif k in ("minimum", "maximum", "exclusiveMinimum",
                       "exclusiveMaximum"):
                attr = {"minimum": "minimum", "maximum": "maximum",
                        "exclusiveMinimum": "exclusive_minimum",
                        "exclusiveMaximum": "exclusive_maximum"}[k]
                setattr(n, attr, self._num(val, k, p))
            elif k == "multipleOf":
                d = self._num(val, k, p)
                if d.is_zero() or d.neg:
                    raise SchemaError("multipleOf debe ser > 0", p)
                if len(d.digits) > 18:
                    raise SchemaError("multipleOf con más de 18 dígitos "
                                      "significativos", p)
                n.multiple_of = d
            elif k == "properties":
                for name in val:
                    n.properties[name] = self.lower(f"{p}/{_escape_ptr(name)}")
            elif k == "patternProperties":
                for pat in val:
                    try:
                        compile_regex(pat)
                    except RegexError as exc:
                        raise SchemaError(f"patrón no soportado: {exc}", p)
                    n.pattern_properties.append(
                        (pat, self.lower(f"{p}/{_escape_ptr(pat)}")))
            elif k == "additionalProperties":
                n.additional = sub(k)
            elif k == "required":
                if len(set(val)) != len(val):
                    raise SchemaError("required con nombres repetidos", p)
                n.required = list(val)
            elif k in ("minProperties", "maxProperties", "minItems",
                       "maxItems", "minContains", "maxContains"):
                attr = {"minProperties": "min_properties",
                        "maxProperties": "max_properties",
                        "minItems": "min_items", "maxItems": "max_items",
                        "minContains": "min_contains",
                        "maxContains": "max_contains"}[k]
                setattr(n, attr, self._count(val, k, p))
            elif k == "dependentRequired":
                n.dependent_required = {a: list(b) for a, b in val.items()}
            elif k == "dependentSchemas":
                for name in val:
                    n.dependent_schemas[name] = self.lower(
                        f"{p}/{_escape_ptr(name)}")
            elif k == "items":
                n.items = sub(k)
            elif k == "prefixItems":
                n.prefix_items = [self.lower(f"{p}/{i}")
                                  for i in range(len(val))]
            elif k == "contains":
                n.contains = sub(k)
            elif k == "uniqueItems":
                n.unique_items = bool(val)
            elif k in ("allOf", "anyOf", "oneOf"):
                lst = [self.lower(f"{p}/{i}") for i in range(len(val))]
                setattr(n, {"allOf": "all_of", "anyOf": "any_of",
                            "oneOf": "one_of"}[k], lst)
            elif k == "not":
                n.not_ = sub(k)
            elif k == "if":
                n.if_ = sub(k)
            elif k == "then":
                n.then_ = sub(k)
            elif k == "else":
                n.else_ = sub(k)
            elif k == "$ref":
                n.all_of.append(self.lower(self.resolve(val, p)))
            elif k in ("$defs", "definitions", "format"):
                pass
        if len(n.tracked_keys()) > 64:
            raise SchemaError("más de 64 claves distinguidas en un objeto "
                              "(límite de la máscara de bits)", ptr)
        if (n.then_ is not None or n.else_ is not None) and n.if_ is None:
            n.then_ = n.else_ = None  # sin if, then/else no tienen efecto
        if n.contains is None:
            n.min_contains = n.max_contains = None

    # -- punto de entrada -----------------------------------------------
    def run(self) -> Analysis:
        self._register(self.raw, "#")
        for ptr, v in list(self.table.items()):
            if isinstance(v, dict) and isinstance(v.get("$ref"), str):
                self.resolve(v["$ref"], ptr)
        self._check_cycles()
        root = self.lower("#")
        # Lower de todos los subesquemas alcanzables desde la raíz ya se
        # hizo por recursión; los $defs no referenciados no generan nodos.
        cls = []
        for k, ptrs in sorted(self.usage.items()):
            cat, strategy = KEYWORD_CLASS.get(k, (CATEGORY_ANN, ""))
            cls.append({"keyword": k, "category": cat, "strategy": strategy,
                        "count": len(ptrs), "pointers": ptrs[:8]})
        symbols = {p: ("bool" if isinstance(v, bool) else
                       ", ".join(list(v)[:6])) for p, v in self.table.items()}
        return Analysis(root, self.nodes, cls, self.warnings, symbols,
                        self.raw)


def _jeq(a, b) -> bool:
    """Igualdad JSON de escalares (1 == 1.0; true != 1)."""
    if isinstance(a, Num) and isinstance(b, Num):
        return compare(a.dec, b.dec) == 0
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return type(a) is type(b) and a == b


def analyze(data: bytes) -> Analysis:
    """Esquema en bytes -> análisis completo (Fases 1–3)."""
    return SchemaAnalyzer(load_schema_value(data)).run()
