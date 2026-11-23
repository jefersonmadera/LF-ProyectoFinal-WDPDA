"""Motor de referencia del WDPDA (Fase 6) — interpreta el IR en streaming.

Es la especificación ejecutable del runtime: el C y el Rust generados
hacen exactamente lo mismo (mismo IR, mismos códigos, mismos pesos) y
las pruebas comparan los tres. Se usa además para la traza paso a paso
de la aplicación web.

Arquitectura (tres capas que comparten una sola pila):

1. Transductor léxico: AFD sobre bytes UTF-8 -> tokens
   (:class:`compiler.json_lexer.JsonTokenizer`, en streaming).
2. DPDA sintáctico de JSON: 8 estados, pila de O/A. Decide si un
   STRING es clave o valor y rechaza la entrada mal formada (peso +∞).
3. Capa de esquema: por cada nivel de la pila, un vector de *carriles*.
   Un carril es una copia del subautómata de un nodo del esquema con sus
   registros acotados (máscara de vistas, contadores, estado de AFD). Sin
   combinadores hay un carril por nivel y esto es un DPDA ordinario; con
   allOf/anyOf/oneOf/not/if/contains/dependentSchemas hay varios carriles
   que leen la misma entrada y apilan/desapilan juntos: el producto de
   autómatas de pila *visibles* (las llamadas y retornos son ``{ [`` y
   ``} ]``), que sí es cerrado bajo intersección. Al cerrar el valor los
   pesos se combinan con las operaciones del semianillo tropical.

Memoria: O(d · B) con d = profundidad y B = carriles por nivel
(acotados en el IR); tiempo O(n · B) en bytes n.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .automata import DFA
from .json_lexer import JsonTokenizer, LexerError
from .numbers import compare, multiple_of, parse_number
from .utf8 import Utf8Error
from .wdpda import ANY, FORBIDDEN, TYPE_BITS

# Estados del DPDA sintáctico.
S_VALUE, S_OBJ_FIRST, S_OBJ_KEY, S_OBJ_COLON, S_OBJ_NEXT, S_ARR_FIRST, \
    S_ARR_NEXT, S_DONE = range(8)
STATE_NAMES = ["VALOR", "OBJ_INICIO", "OBJ_CLAVE", "OBJ_DOSPUNTOS",
               "OBJ_SIGUIENTE", "ARR_INICIO", "ARR_SIGUIENTE", "FIN"]

# Roles de carril.
ROOT, ALL, ANYOF, ONE, NOT, IF, THEN, ELSE, DEP, CONTAINS = range(10)
ROLE_NAMES = ["raíz", "allOf", "anyOf", "oneOf", "not", "if", "then",
              "else", "dependentSchemas", "contains"]

# Estados finales (mismos nombres que el runtime C/Rust).
VALID, INVALID = "VALID", "INVALID"
FATAL = ("MALFORMED", "UTF8", "DEPTH", "LIMIT", "SIZE", "TIME")

MASK = (1 << 64) - 1
INF = float("inf")


# ----------------------------------------------------------------------
# Hash canónico de 64 bits para uniqueItems (idéntico en C y Rust)
# ----------------------------------------------------------------------
def mix64(x: int) -> int:
    x &= MASK
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & MASK
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & MASK
    x ^= x >> 31
    return x


def hstr(tag: int, data: bytes) -> int:
    h = 0xCBF29CE484222325
    h = ((h ^ tag) * 0x100000001B3) & MASK
    for b in data:
        h = ((h ^ b) * 0x100000001B3) & MASK
    return mix64(h)


def number_canon(d) -> bytes:
    if d.is_zero():
        return b"0"
    return (("-" if d.neg else "") + d.digits + "e" + str(d.exp)).encode()


H_NULL, H_TRUE, H_FALSE = mix64(1), mix64(2), mix64(3)
H_ARR0 = mix64(4)
H_OBJ_SALT = mix64(5)


def scalar_hash(kind: str, value) -> int:
    if kind == "STRING":
        return hstr(0x53, value.encode("utf-8"))
    if kind == "NUMBER":
        return hstr(0x4E, number_canon(parse_number(value)))
    return {"TRUE": H_TRUE, "FALSE": H_FALSE, "NULL": H_NULL}[kind]


# ----------------------------------------------------------------------
@dataclass
class Error:
    code: str
    message: str
    path: str
    offset: int

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "path": self.path, "offset": self.offset}


@dataclass
class Result:
    status: str
    weight: float  # número de violaciones; inf si mal formado
    errors: list[Error]
    bytes: int
    max_depth: int
    lanes_peak: int
    tokens: int
    fatal_message: str = ""
    fatal_offset: int = -1
    error_count: int = 0

    @property
    def valid(self) -> bool:
        return self.status == VALID

    def to_dict(self) -> dict:
        return {"status": self.status,
                "weight": None if self.weight == INF else self.weight,
                "errors": [e.to_dict() for e in self.errors],
                "error_count": self.error_count, "bytes": self.bytes,
                "max_depth": self.max_depth, "lanes_peak": self.lanes_peak,
                "tokens": self.tokens, "fatal_message": self.fatal_message,
                "fatal_offset": self.fatal_offset}


@dataclass(eq=False)
class Lane:
    node: int
    parent: int   # carril padre en el mismo nivel, -1 si es raíz del nivel
    owner: int    # carril dueño en el nivel de abajo (-1 = documento)
    role: int
    spec: bool    # especulativo: sus errores no se reportan sueltos
    keybit: int = -1
    viol: int = 0
    # acumuladores de combinación
    acc_all: int = 0
    any_totals: list = field(default_factory=list)
    one_totals: list = field(default_factory=list)
    not_total: int = -1
    if_total: int = -1
    then_total: int = 0
    else_total: int = 0
    dep_totals: list = field(default_factory=list)
    # registros de objeto
    seen: int = 0
    count: int = 0
    pending: list = field(default_factory=list)
    # registros de arreglo
    index: int = 0
    contains_count: int = 0
    uniq: set = field(default_factory=set)


@dataclass
class Level:
    kind: str  # "O" | "A"
    lanes: list[Lane]
    seg: str = ""        # segmento actual del JSON Pointer
    count: int = 0       # miembros/elementos vistos (para la ruta)
    hash_acc: int = 0
    key_hash: int = 0


class _Stop(Exception):
    pass


def _ptr_seg(s: str) -> str:
    return s.replace("~", "~0").replace("/", "~1")


class Validator:
    """Validador en streaming: ``feed(bytes)`` ... ``finish()``."""

    def __init__(self, ir: dict, fail_fast: bool = False,
                 max_depth: int | None = None, max_bytes: int | None = None,
                 time_limit: float | None = None, trace: bool = False):
        self.ir = ir
        self.nodes = ir["nodes"]
        self.lim = ir["limits"]
        self.max_depth = max_depth or self.lim["max_depth"]
        self.max_bytes = max_bytes
        self.deadline = (time.perf_counter() + time_limit) if time_limit \
            else None
        self.fail_fast = fail_fast
        self.dfas = [_DenseDFA(d) for d in ir["dfas"]]
        self.consts = [parse_number(c["lexeme"]) for c in ir["consts"]]
        self.lexer = JsonTokenizer()
        self.state = S_VALUE
        self.levels: list[Level] = []
        self.errors: list[Error] = []
        self.error_count = 0
        self.doc_weight = 0
        self.status = None
        self.fatal_message = ""
        self.fatal_offset = -1
        self.nbytes = 0
        self.ntokens = 0
        self.max_seen_depth = 0
        self.lanes_live = 0
        self.lanes_peak = 0
        self.offset = 0
        self.trace = [] if trace else None
        self.root_hash = 0

    # -- API ------------------------------------------------------------
    def feed(self, data: bytes) -> None:
        if self.status is not None:
            return
        self.nbytes += len(data)
        if self.max_bytes is not None and self.nbytes > self.max_bytes:
            self._fatal("SIZE", f"el payload supera {self.max_bytes} bytes",
                        self.max_bytes)
            return
        if self.deadline is not None and time.perf_counter() > self.deadline:
            self._fatal("TIME", "se superó el tiempo límite", self.nbytes)
            return
        try:
            toks = self.lexer.feed(data)
        except Utf8Error as exc:
            self._fatal("UTF8", str(exc), exc.offset)
            return
        except LexerError as exc:
            self._fatal("MALFORMED", str(exc), exc.offset)
            return
        self._tokens(toks)

    def finish(self) -> Result:
        if self.status is None:
            try:
                toks = self.lexer.finish()
            except Utf8Error as exc:
                self._fatal("UTF8", str(exc), exc.offset)
                toks = []
            except LexerError as exc:
                self._fatal("MALFORMED", str(exc), exc.offset)
                toks = []
            if self.status is None:
                self._tokens(toks)
            if self.status is None and self.state != S_DONE:
                self._fatal("MALFORMED", "entrada incompleta: el valor JSON "
                            "no terminó", self.nbytes)
            if self.status is None:
                self.status = VALID if self.doc_weight == 0 else INVALID
        weight = INF if self.status in FATAL else self.doc_weight
        return Result(self.status, weight, self.errors, self.nbytes,
                      self.max_seen_depth, self.lanes_peak, self.ntokens,
                      self.fatal_message, self.fatal_offset,
                      self.error_count)

    def validate(self, data: bytes, chunk: int | None = None) -> Result:
        if chunk:
            for i in range(0, len(data), chunk):
                self.feed(data[i:i + chunk])
        else:
            self.feed(data)
        return self.finish()

    # -- utilidades -----------------------------------------------------
    def _fatal(self, status: str, msg: str, offset: int) -> None:
        if self.status is None:
            self.status = status
            self.fatal_message = msg
            self.fatal_offset = offset

    def _path(self, depth: int) -> str:
        return "".join("/" + _ptr_seg(lv.seg) for lv in self.levels[:depth])

    def _violate(self, lane: Lane, code: str, msg: str, depth: int,
                 weight: int = 1) -> None:
        lane.viol += weight
        if not lane.spec:
            self._record(code, msg, self._path(depth))

    def _record(self, code: str, msg: str, path: str) -> None:
        self.error_count += 1
        if len(self.errors) < self.lim["max_errors"]:
            self.errors.append(Error(code, msg, path, self.offset))
        if self.fail_fast:
            raise _Stop()

    # -- DPDA sintáctico -------------------------------------------------
    def _tokens(self, toks) -> None:
        try:
            for t in toks:
                self.ntokens += 1
                self.offset = t.offset
                self._token(t)
                if self.status is not None:
                    return
        except _Stop:
            self.status = INVALID
            self.doc_weight = max(self.doc_weight, 1)

    def _token(self, t) -> None:
        k = t.kind
        st = self.state
        if self.trace is not None:
            self._trace_step(t)
        if st == S_VALUE or (st == S_ARR_FIRST and k != "]"):
            if k in ("{", "[", "STRING", "NUMBER", "TRUE", "FALSE", "NULL"):
                self._begin_value(t)
            else:
                self._syntax(t, "se esperaba un valor")
        elif st == S_ARR_FIRST:  # k == "]"
            self._end_container(t)
        elif st in (S_OBJ_FIRST, S_OBJ_KEY):
            if k == "STRING":
                self._key(t)
                self.state = S_OBJ_COLON
            elif k == "}" and st == S_OBJ_FIRST:
                self._end_container(t)
            else:
                self._syntax(t, "se esperaba una clave (cadena)")
        elif st == S_OBJ_COLON:
            if k == ":":
                self.state = S_VALUE
            else:
                self._syntax(t, "se esperaba ':'")
        elif st == S_OBJ_NEXT:
            if k == ",":
                self.state = S_OBJ_KEY
            elif k == "}":
                self._end_container(t)
            else:
                self._syntax(t, "se esperaba ',' o '}'")
        elif st == S_ARR_NEXT:
            if k == ",":
                self.state = S_VALUE
            elif k == "]":
                self._end_container(t)
            else:
                self._syntax(t, "se esperaba ',' o ']'")
        else:  # S_DONE
            self._syntax(t, "datos después del valor JSON")

    def _syntax(self, t, msg: str) -> None:
        self._fatal("MALFORMED", f"{msg} (llegó {t.kind})", t.offset)

    def _after_value(self, h: int) -> None:
        if not self.levels:
            self.state = S_DONE
            self.root_hash = h
            return
        lv = self.levels[-1]
        if lv.kind == "O":
            lv.hash_acc = (lv.hash_acc + mix64(lv.key_hash * 3 + h)) & MASK
            self.state = S_OBJ_NEXT
        else:
            lv.hash_acc = mix64(lv.hash_acc ^ h)
            depth = len(self.levels)
            for lane in lv.lanes:
                arr = self.nodes[lane.node]["arr"]
                if arr and arr["unique"] and not self.nodes[lane.node]["false"]:
                    if h in lane.uniq:
                        self._violate(lane, "UNIQUE", "elementos repetidos "
                                      "(uniqueItems)", depth)
                    elif len(lane.uniq) >= self.lim["unique_cap"]:
                        self._fatal("LIMIT", "uniqueItems: el arreglo supera "
                                    "la capacidad acotada "
                                    f"({self.lim['unique_cap']})", self.offset)
                    else:
                        lane.uniq.add(h)
            self.state = S_ARR_NEXT

    # -- capa de esquema --------------------------------------------------
    def _begin_value(self, t) -> None:
        k = t.kind
        parent = self.levels[-1] if self.levels else None
        depth = len(self.levels)
        requests: list[tuple[int, int, int, bool]] = []
        if parent is None:
            if self.ir["root"] != ANY:
                requests.append((self.ir["root"], -1, ROOT, False))
        elif parent.kind == "O":
            for i, lane in enumerate(parent.lanes):
                for c in lane.pending:
                    requests.append((c, i, ROOT, lane.spec))
                lane.pending = []
        else:
            parent.seg = str(parent.count)
            for i, lane in enumerate(parent.lanes):
                n = self.nodes[lane.node]
                arr = n["arr"]
                if arr is None or n["false"]:
                    continue
                idx = lane.index
                lane.index += 1
                child = arr["prefix"][idx] if idx < len(arr["prefix"]) \
                    else arr["items"]
                if child == FORBIDDEN:
                    self._violate(lane, "ITEMS", f"elemento {idx} no "
                                  "permitido (items: false)", depth + 1)
                elif child >= 0:
                    requests.append((child, i, ROOT, lane.spec))
                c = arr["contains"]
                if c == ANY:
                    lane.contains_count += 1
                elif c >= 0:
                    requests.append((c, i, CONTAINS, True))
            parent.count += 1
        lanes = self._expand(requests, t)
        if k in ("{", "["):
            if depth + 1 > self.max_depth:
                self._fatal("DEPTH", f"profundidad máxima {self.max_depth} "
                            "superada", t.offset)
                return
            self.levels.append(Level("O" if k == "{" else "A", lanes))
            self.max_seen_depth = max(self.max_seen_depth, depth + 1)
            self.state = S_OBJ_FIRST if k == "{" else S_ARR_FIRST
            return
        # Escalar: comprobar, combinar y entregar en el acto.
        self._scalar_checks(lanes, t, depth)
        self._finalize(lanes, depth)
        self.lanes_live -= len(lanes)
        self._after_value(scalar_hash(k, t.value))

    def _expand(self, requests, t) -> list[Lane]:
        lanes: list[Lane] = []
        k = t.kind
        if k == "NUMBER":
            jt = TYPE_BITS["integer"] if parse_number(t.value).is_integer() \
                else TYPE_BITS["number"]
        else:
            jt = {"{": TYPE_BITS["object"], "[": TYPE_BITS["array"],
                  "STRING": TYPE_BITS["string"], "TRUE": TYPE_BITS["boolean"],
                  "FALSE": TYPE_BITS["boolean"],
                  "NULL": TYPE_BITS["null"]}[k]
        depth = len(self.levels) + (1 if k in ("{", "[") else 0)
        # Los errores de un contenedor se reportan en su propia ruta.
        path_depth = len(self.levels)

        def add(node, parent, owner, role, spec, keybit=-1):
            idx = len(lanes)
            if idx >= self.lim["max_lanes"]:
                self._fatal("LIMIT", "demasiados carriles en un nivel "
                            f"(límite {self.lim['max_lanes']})", t.offset)
                raise _Stop()
            lane = Lane(node, parent, owner, role, spec, keybit)
            lanes.append(lane)
            n = self.nodes[node]
            if n["false"]:
                self._violate(lane, "FALSE", "el esquema false no acepta "
                              "ningún valor", path_depth)
                return
            if not (n["types"] & jt):
                self._violate(lane, "TYPE", f"tipo {_type_name(jt)} no "
                              f"permitido (se esperaba {_types(n['types'])})",
                              path_depth)
            elif n["enum"] is not None and k in ("{", "["):
                self._violate(lane, "ENUM", "el valor no está en enum/const",
                              path_depth)
            for c in n["all_of"]:
                add(c, idx, -1, ALL, spec)
            for c in n["any_of"]:
                if c == ANY:
                    lane.any_totals.append(0)
                else:
                    add(c, idx, -1, ANYOF, True)
            for c in n["one_of"]:
                if c == ANY:
                    lane.one_totals.append(0)
                else:
                    add(c, idx, -1, ONE, True)
            if n["not"] != ANY:
                add(n["not"], idx, -1, NOT, True)
            if n["if"] != ANY:
                add(n["if"], idx, -1, IF, True)
                if n["then"] != ANY:
                    add(n["then"], idx, -1, THEN, True)
                if n["else"] != ANY:
                    add(n["else"], idx, -1, ELSE, True)
            if k == "{" and n["obj"]:
                for kb, c in n["obj"]["dep_schemas"]:
                    if c != ANY:
                        add(c, idx, -1, DEP, True, kb)

        try:
            for node, owner, role, spec in requests:
                add(node, -1, owner, role, spec or role == CONTAINS)
        except _Stop:
            if self.status is not None:  # LIMIT
                return lanes
            raise
        self.lanes_live += len(lanes)
        self.lanes_peak = max(self.lanes_peak, self.lanes_live)
        return lanes

    def _scalar_checks(self, lanes: list[Lane], t, depth: int) -> None:
        k = t.kind
        if k == "STRING":
            raw = t.value.encode("utf-8")
            ncp = len(t.value)
        elif k == "NUMBER":
            dec = parse_number(t.value)
        for lane in lanes:
            n = self.nodes[lane.node]
            if n["false"]:
                continue
            for chk in n["checks"]:
                c = chk["check"]
                if c == "enum":
                    e = n["enum"]
                    ok = False
                    if k == "STRING":
                        ok = e["strings"] >= 0 and \
                            self.dfas[e["strings"]].run(raw) >= 0
                    elif k == "NUMBER":
                        ok = any(compare(dec, self.consts[i]) == 0
                                 for i in e["nums"])
                    else:
                        ok = bool(e["lits"] & {"TRUE": 1, "FALSE": 2,
                                               "NULL": 4}[k])
                    if not ok:
                        self._violate(lane, "ENUM", "el valor no está en "
                                      "enum/const", depth)
                elif c == "length" and k == "STRING":
                    s = n["str"]
                    if s["min_len"] >= 0 and ncp < s["min_len"]:
                        self._violate(lane, "MIN_LENGTH", f"longitud {ncp} < "
                                      f"minLength {s['min_len']}", depth)
                    if s["max_len"] >= 0 and ncp > s["max_len"]:
                        self._violate(lane, "MAX_LENGTH", f"longitud {ncp} > "
                                      f"maxLength {s['max_len']}", depth)
                elif c == "pattern" and k == "STRING":
                    pass  # se evalúan todos juntos abajo (un solo recorrido)
                elif c in ("ge", "gt", "le", "lt", "mul") and k == "NUMBER":
                    pass
            if k == "STRING" and n["str"]:
                for d, src in zip(n["str"]["patterns"],
                                  n["str"]["pattern_src"]):
                    if self.dfas[d].run(raw) < 0:
                        self._violate(lane, "PATTERN", "no coincide con "
                                      f"pattern {src!r}", depth)
            if k == "NUMBER" and n["num"]:
                for op, ci in n["num"]["checks"]:
                    c = self.consts[ci]
                    if op == "mul":
                        r = multiple_of(dec, c)
                        if r is None:
                            self._violate(lane, "MULTIPLE_OF", "número con "
                                          "demasiados dígitos para "
                                          "multipleOf (límite del registro)",
                                          depth)
                        elif not r:
                            self._violate(lane, "MULTIPLE_OF", f"no es "
                                          f"múltiplo de {c}", depth)
                        continue
                    cmp = compare(dec, c)
                    bad = {"ge": cmp < 0, "gt": cmp <= 0, "le": cmp > 0,
                           "lt": cmp >= 0}[op]
                    if bad:
                        name = {"ge": "MINIMUM", "gt": "EXCLUSIVE_MINIMUM",
                                "le": "MAXIMUM", "lt": "EXCLUSIVE_MAXIMUM"}[op]
                        sym = {"ge": "<", "gt": "<=", "le": ">",
                               "lt": ">="}[op]
                        self._violate(lane, name, f"{t.value} {sym} {c}",
                                      depth)

    def _key(self, t) -> None:
        lv = self.levels[-1]
        lv.seg = t.value
        lv.count += 1
        raw = t.value.encode("utf-8")
        lv.key_hash = hstr(0x53, raw)
        depth = len(self.levels)
        for lane in lv.lanes:
            n = self.nodes[lane.node]
            o = n["obj"]
            lane.pending = []
            if n["false"]:
                continue
            lane.count += 1
            if o is None:
                continue
            idx = self.dfas[o["keys_dfa"]].run(raw) if o["keys_dfa"] >= 0 \
                else -1
            declared = False
            if idx >= 0:
                if lane.seen >> idx & 1:
                    self._violate(lane, "DUPLICATE_KEY",
                                  f"clave duplicada {t.value!r}", depth)
                lane.seen |= 1 << idx
                if o["key_declared"][idx]:
                    declared = True
                    if o["key_child"][idx] >= 0:
                        lane.pending.append(o["key_child"][idx])
            matched = False
            for d, c in o["patterns"]:
                if self.dfas[d].run(raw) >= 0:
                    matched = True
                    if c >= 0:
                        lane.pending.append(c)
            if not declared and not matched:
                if o["additional"] == FORBIDDEN:
                    self._violate(lane, "ADDITIONAL", f"propiedad adicional "
                                  f"no permitida {t.value!r}", depth)
                elif o["additional"] >= 0:
                    lane.pending.append(o["additional"])

    def _end_container(self, t) -> None:
        lv = self.levels[-1]
        depth = len(self.levels) - 1  # ruta del contenedor
        for lane in lv.lanes:
            n = self.nodes[lane.node]
            if n["false"]:
                continue
            if lv.kind == "O" and n["obj"]:
                o = n["obj"]
                missing = o["required"] & ~lane.seen
                b = 0
                while missing:
                    if missing & 1:
                        self._violate(lane, "REQUIRED", "falta la propiedad "
                                      f"requerida {o['keys'][b]!r}", depth)
                    missing >>= 1
                    b += 1
                if o["min_props"] >= 0 and lane.count < o["min_props"]:
                    self._violate(lane, "MIN_PROPERTIES", f"{lane.count} "
                                  f"propiedades < {o['min_props']}", depth)
                if o["max_props"] >= 0 and lane.count > o["max_props"]:
                    self._violate(lane, "MAX_PROPERTIES", f"{lane.count} "
                                  f"propiedades > {o['max_props']}", depth)
                for kb, mask in o["dep_required"]:
                    if lane.seen >> kb & 1:
                        miss = mask & ~lane.seen
                        b = 0
                        while miss:
                            if miss & 1:
                                self._violate(
                                    lane, "DEPENDENT_REQUIRED",
                                    f"{o['keys'][kb]!r} exige "
                                    f"{o['keys'][b]!r}", depth)
                            miss >>= 1
                            b += 1
            elif lv.kind == "A" and n["arr"]:
                a = n["arr"]
                if a["min_items"] >= 0 and lane.index < a["min_items"]:
                    self._violate(lane, "MIN_ITEMS", f"{lane.index} elementos "
                                  f"< minItems {a['min_items']}", depth)
                if a["max_items"] >= 0 and lane.index > a["max_items"]:
                    self._violate(lane, "MAX_ITEMS", f"{lane.index} elementos "
                                  f"> maxItems {a['max_items']}", depth)
                if a["contains"] != -3:
                    if lane.contains_count < a["min_contains"]:
                        self._violate(lane, "CONTAINS", f"{lane.contains_count}"
                                      f" elementos cumplen contains < "
                                      f"{a['min_contains']}", depth)
                    if 0 <= a["max_contains"] < lane.contains_count:
                        self._violate(lane, "CONTAINS", f"{lane.contains_count}"
                                      f" elementos cumplen contains > "
                                      f"{a['max_contains']}", depth)
        if lv.kind == "O":
            h = mix64(lv.hash_acc ^ H_OBJ_SALT)
        else:
            h = mix64(lv.hash_acc ^ H_ARR0 ^ lv.count)
        lanes = lv.lanes
        # Al combinar, la ruta es la del contenedor.
        self._finalize(lanes, depth, closing=True)
        self.lanes_live -= len(lanes)
        self.levels.pop()
        self._after_value(h)

    def _finalize(self, lanes: list[Lane], depth: int,
                  closing: bool = False) -> None:
        """Combina los pesos con ⊕ = min y ⊗ = + y los entrega abajo."""
        parent_level = None
        if closing:
            parent_level = self.levels[-2] if len(self.levels) >= 2 else None
        else:
            parent_level = self.levels[-1] if self.levels else None
        for i in range(len(lanes) - 1, -1, -1):
            L = lanes[i]
            n = self.nodes[L.node]
            total = L.viol + L.acc_all
            if n["any_of"]:
                best = min(L.any_totals) if L.any_totals else 0
                if best > 0:
                    total += best
                    if not L.spec:
                        self._record("ANY_OF", "anyOf: ninguna alternativa es "
                                     f"válida (mejor rama: {best} "
                                     "violaciones)", self._path(depth))
            if n["one_of"]:
                zeros = sum(1 for x in L.one_totals if x == 0)
                if zeros == 0:
                    total += min(L.one_totals)
                    if not L.spec:
                        self._record("ONE_OF", "oneOf: ninguna alternativa es "
                                     "válida", self._path(depth))
                elif zeros > 1:
                    total += 1
                    if not L.spec:
                        self._record("ONE_OF", f"oneOf: {zeros} alternativas "
                                     "válidas (debe ser exactamente una)",
                                     self._path(depth))
            if n["not"] != ANY and L.not_total == 0:
                total += 1
                if not L.spec:
                    self._record("NOT", "el valor no debe cumplir el esquema "
                                 "de not", self._path(depth))
            if n.get("not_any"):
                total += 1
                if not L.spec:
                    self._record("NOT", "not: true no acepta ningún valor",
                                 self._path(depth))
            if n["if"] != ANY:
                if L.if_total == 0:
                    if n["then"] != ANY and L.then_total > 0:
                        total += L.then_total
                        if not L.spec:
                            self._record("IF_THEN", "cumple if pero no then",
                                         self._path(depth))
                elif n["else"] != ANY and L.else_total > 0:
                    total += L.else_total
                    if not L.spec:
                        self._record("IF_ELSE", "no cumple if ni else",
                                     self._path(depth))
            for kb, tot in L.dep_totals:
                if L.seen >> kb & 1 and tot > 0:
                    total += tot
                    if not L.spec:
                        self._record("DEPENDENT_SCHEMAS", "no cumple "
                                     "dependentSchemas de "
                                     f"{n['obj']['keys'][kb]!r}",
                                     self._path(depth))
            # Entrega del peso.
            if L.parent >= 0:
                P = lanes[L.parent]
                if L.role == ALL:
                    P.acc_all += total
                elif L.role == ANYOF:
                    P.any_totals.append(total)
                elif L.role == ONE:
                    P.one_totals.append(total)
                elif L.role == NOT:
                    P.not_total = total
                elif L.role == IF:
                    P.if_total = total
                elif L.role == THEN:
                    P.then_total = total
                elif L.role == ELSE:
                    P.else_total = total
                elif L.role == DEP:
                    P.dep_totals.append((L.keybit, total))
            elif L.owner < 0:
                self.doc_weight += total
            else:
                Q = parent_level.lanes[L.owner]
                if L.role == CONTAINS:
                    if total == 0:
                        Q.contains_count += 1
                else:
                    Q.viol += total

    # -- traza --------------------------------------------------------------
    def _trace_step(self, t) -> None:
        stack = ["Z0"] + [
            f"{lv.kind}[" + ",".join(f"n{ln.node}" for ln in lv.lanes) + "]"
            for lv in self.levels]
        self.trace.append({
            "offset": t.offset, "token": t.kind,
            "value": t.value if t.kind in ("STRING", "NUMBER") else None,
            "state": STATE_NAMES[self.state], "stack": stack,
            "weight": self.doc_weight + sum(
                ln.viol for lv in self.levels for ln in lv.lanes),
            "path": self._path(len(self.levels))})


class _DenseDFA:
    """DFA del IR (tabla densa por clases de bytes), como en C/Rust."""

    __slots__ = ("cls", "ncls", "table", "accept")

    def __init__(self, d: dict):
        self.cls = d["classes"]
        self.ncls = d["ncls"]
        self.table = d["table"]
        self.accept = d["accept"]

    def run(self, data: bytes) -> int:
        s = 0
        t, c, n = self.table, self.cls, self.ncls
        for b in data:
            s = t[s * n + c[b]]
            if s < 0:
                return -1
        return self.accept[s]


def _type_name(bit: int) -> str:
    for k, v in TYPE_BITS.items():
        if v == bit:
            return k
    return "?"


def _types(mask: int) -> str:
    names = [k for k, v in TYPE_BITS.items() if mask & v]
    if "number" in names and "integer" in names:
        names.remove("integer")
    return "|".join(names)


def validate(ir: dict, data: bytes, **kw) -> Result:
    chunk = kw.pop("chunk", None)
    return Validator(ir, **kw).validate(data, chunk)
