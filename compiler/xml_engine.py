"""Motor de referencia del WDPDA para XML (extensión XSD).

Capas, igual que en JSON:

1. AFD léxico XML (:class:`compiler.xml_lexer.XmlTokenizer`): etiquetas,
   texto, comentarios; rechaza DOCTYPE y entidades no predefinidas.
2. DPDA de buena formación: apila en ``<e>``, desapila en ``</e>``
   comprobando que el nombre coincida (pila de nombres acotada). Un solo
   elemento raíz.
3. Capa de esquema: el marco de pila lleva (declaración, estado del AFD
   de contenido de Glushkov, máscara de atributos). Una etiqueta de
   apertura es una transición del AFD del padre sobre el símbolo del
   nombre; una de cierre exige que el AFD esté en un estado de
   aceptación. El texto de un elemento simple se valida con las mismas
   comprobaciones de escalares que JSON (trie de enumeración, AFD de
   patrón, comparación decimal exacta).

Pesos tropicales: cada violación pesa 1; mal formado = +∞.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import FATAL, INF, INVALID, VALID, Error, Result, _DenseDFA
from .json_lexer import LexerError
from .numbers import compare, multiple_of, parse_number
from .utf8 import Utf8Error
from .xml_lexer import XmlTokenizer
from .xsd import parse_xsd_number

XSD_KIND = {0: "string", 1: "boolean", 2: "integer", 3: "decimal",
            4: "double"}


@dataclass
class XFrame:
    elem: int          # -1 = elemento no declarado (se salta su contenido)
    name: str
    seg: str
    dstate: int = 0
    text: list = field(default_factory=list)
    text_flag: bool = False
    counts: dict = field(default_factory=dict)


class _Stop(Exception):
    pass


class XmlValidator:
    def __init__(self, ir: dict, fail_fast: bool = False,
                 max_depth: int | None = None, trace: bool = False):
        assert ir["format"] == "xml"
        self.ir = ir
        self.el = ir["elements"]
        self.nodes = ir["nodes"]
        self.dfas = [_DenseDFA(d) for d in ir["dfas"]]
        self.consts = [parse_number(c["lexeme"]) for c in ir["consts"]]
        self.lim = ir["limits"]
        self.max_depth = max_depth or self.lim["max_depth"]
        self.fail_fast = fail_fast
        self.lexer = XmlTokenizer()
        self.frames: list[XFrame] = []
        self.errors: list[Error] = []
        self.error_count = 0
        self.weight = 0
        self.status = None
        self.fatal_message = ""
        self.fatal_offset = -1
        self.nbytes = 0
        self.ntokens = 0
        self.max_seen = 0
        self.root_seen = False
        self.done = False
        self.offset = 0
        self.trace = [] if trace else None

    # -- API ------------------------------------------------------------
    def feed(self, data: bytes) -> None:
        if self.status is not None:
            return
        self.nbytes += len(data)
        try:
            toks = self.lexer.feed(data)
        except Utf8Error as exc:
            return self._fatal("UTF8", str(exc), exc.offset)
        except LexerError as exc:
            return self._fatal("MALFORMED", str(exc), exc.offset)
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
            if self.status is None and self.frames:
                self._fatal("MALFORMED", f"etiqueta <{self.frames[-1].name}> "
                            "sin cerrar", self.nbytes)
            if self.status is None and not self.root_seen:
                self._fatal("MALFORMED", "documento sin elemento raíz",
                            self.nbytes)
            if self.status is None:
                self.status = VALID if self.weight == 0 else INVALID
        w = INF if self.status in FATAL else self.weight
        return Result(self.status, w, self.errors, self.nbytes, self.max_seen,
                      0, self.ntokens, self.fatal_message, self.fatal_offset,
                      self.error_count)

    def validate(self, data: bytes, chunk: int | None = None) -> Result:
        if chunk:
            for i in range(0, len(data), chunk):
                self.feed(data[i:i + chunk])
        else:
            self.feed(data)
        return self.finish()

    # -- utilidades -----------------------------------------------------
    def _fatal(self, st, msg, off):
        if self.status is None:
            self.status = st
            self.fatal_message = msg
            self.fatal_offset = off

    def _path(self, extra: str = "") -> str:
        return "".join("/" + f.seg for f in self.frames) + extra

    def _violate(self, code: str, msg: str, path: str) -> None:
        self.weight += 1
        self.error_count += 1
        if len(self.errors) < self.lim["max_errors"]:
            self.errors.append(Error(code, msg, path, self.offset))
        if self.fail_fast:
            raise _Stop()

    def _tokens(self, toks) -> None:
        try:
            for t in toks:
                self.ntokens += 1
                self.offset = t.offset
                if self.trace is not None:
                    self.trace.append({
                        "offset": t.offset, "token": t.kind,
                        "value": t.value if t.kind != "COMMENT" else None,
                        "stack": ["Z0"] + [f"{f.name}:q{f.dstate}"
                                           for f in self.frames],
                        "weight": self.weight, "path": self._path()})
                self._token(t)
                if self.status is not None:
                    return
        except _Stop:
            self.status = INVALID

    # -- DPDA de XML ------------------------------------------------------
    def _token(self, t) -> None:
        k = t.kind
        if k in ("TAG_OPEN", "TAG_EMPTY"):
            self._open(t)
            if k == "TAG_EMPTY" and self.status is None:
                self._close(t.value, t.offset)
        elif k == "TAG_CLOSE":
            if not self.frames or self.frames[-1].name != t.value:
                exp = self.frames[-1].name if self.frames else "nada"
                self._fatal("MALFORMED", f"</{t.value}> no cierra <{exp}>",
                            t.offset)
                return
            self._close(t.value, t.offset)
        elif k == "TEXT":
            if not self.frames:
                if t.value.strip():
                    self._fatal("MALFORMED", "texto fuera del elemento raíz",
                                t.offset)
                return
            f = self.frames[-1]
            if f.elem < 0:
                return
            E = self.el[f.elem]
            if E["complex"]:
                if not E["mixed"] and t.value.strip() and not f.text_flag:
                    f.text_flag = True
                    self._violate("XML_TEXT", f"<{E['name']}> no admite texto "
                                  "(contenido solo de elementos)",
                                  self._path())
            else:
                f.text.append(t.value)
        # COMMENT y PI no afectan la validación.

    def _open(self, t) -> None:
        name = t.value
        if self.done:
            self._fatal("MALFORMED", "más de un elemento raíz", t.offset)
            return
        if len(self.frames) + 1 > self.max_depth:
            self._fatal("DEPTH", f"profundidad máxima {self.max_depth} "
                        "superada", t.offset)
            return
        child = -1
        if not self.frames:
            self.root_seen = True
            r = self.dfas[self.ir["root_names_dfa"]].run(name.encode())
            seg = name
            if r < 0:
                self.frames.append(XFrame(-1, name, seg))
                self._violate("XML_ROOT", f"elemento raíz <{name}> no "
                              "declarado", self._path())
                return
            child = self.ir["roots"][r]
        else:
            p = self.frames[-1]
            p.counts[name] = p.counts.get(name, 0) + 1
            seg = f"{name}[{p.counts[name]}]"
            if p.elem >= 0:
                P = self.el[p.elem]
                if not P["complex"]:
                    self._violate("XML_CONTENT", f"<{P['name']}> es de tipo "
                                  f"simple y no admite el hijo <{name}>",
                                  self._path("/" + seg))
                else:
                    sym = self.dfas[P["names_dfa"]].run(name.encode()) \
                        if P["names_dfa"] >= 0 else -1
                    if sym < 0:
                        self._violate("XML_UNDECLARED", f"<{name}> no está "
                                      f"permitido en <{P['name']}>",
                                      self._path("/" + seg))
                    else:
                        d = self.dfas[P["content_dfa"]]
                        ns = d.table[p.dstate * d.ncls + d.cls[sym]]
                        if ns < 0:
                            self._violate("XML_CONTENT", f"<{name}> no se "
                                          "esperaba aquí en <"
                                          f"{P['name']}> (modelo "
                                          f"{self.ir['dfas'][P['content_dfa']]['source']})",
                                          self._path("/" + seg))
                        else:
                            p.dstate = ns
                        child = P["child_elems"][sym]
        f = XFrame(child, name, seg)
        self.frames.append(f)
        self.max_seen = max(self.max_seen, len(self.frames))
        if child < 0:
            return
        E = self.el[child]
        seen = 0
        for an, av in t.attrs:
            idx = self.dfas[E["attr_dfa"]].run(an.encode()) \
                if E["attr_dfa"] >= 0 else -1
            if idx < 0:
                self._violate("XML_ATTR", f"atributo {an!r} no declarado en "
                              f"<{name}>", self._path("/@" + an))
                continue
            seen |= 1 << idx
            self._check_simple(E["attrs"][idx]["node"], av,
                               self._path("/@" + an))
        miss = E["attr_required"] & ~seen
        i = 0
        while miss:
            if miss & 1:
                self._violate("XML_ATTR_REQUIRED", "falta el atributo "
                              f"requerido {E['attrs'][i]['name']!r}",
                              self._path())
            miss >>= 1
            i += 1

    def _close(self, name: str, offset: int) -> None:
        f = self.frames[-1]
        if f.elem >= 0:
            E = self.el[f.elem]
            if E["complex"]:
                if E["content_dfa"] >= 0:
                    d = self.dfas[E["content_dfa"]]
                    if d.accept[f.dstate] < 0:
                        src = self.ir["dfas"][E["content_dfa"]]["source"]
                        self._violate("XML_INCOMPLETE", f"contenido "
                                      f"incompleto de <{name}> (modelo "
                                      f"{src})", self._path())
            elif E["simple"] >= 0:
                self._check_simple(E["simple"], "".join(f.text), self._path())
        self.frames.pop()
        if not self.frames:
            self.done = True

    def _check_simple(self, ni: int, text: str, path: str) -> None:
        n = self.nodes[ni]
        kind = XSD_KIND[n["xsd"]]
        value = text if kind == "string" else text.strip(" \t\r\n")
        dec = None
        if kind == "boolean":
            if value not in ("true", "false", "1", "0"):
                self._violate("TYPE", f"{value!r} no es un xs:boolean", path)
                return
        elif kind != "string":
            dec = parse_xsd_number(text, kind)
            if dec is None:
                self._violate("TYPE", f"{value!r} no es un xs:{kind}", path)
                return
        raw = value.encode("utf-8")
        s = n["str"]
        if s:
            ncp = len(value)
            if s["min_len"] >= 0 and ncp < s["min_len"]:
                self._violate("MIN_LENGTH", f"longitud {ncp} < "
                              f"{s['min_len']}", path)
            if s["max_len"] >= 0 and ncp > s["max_len"]:
                self._violate("MAX_LENGTH", f"longitud {ncp} > "
                              f"{s['max_len']}", path)
        e = n["enum"]
        if e is not None:
            if dec is not None:
                ok = any(compare(dec, self.consts[c]) == 0 for c in e["nums"])
            else:
                ok = e["strings"] >= 0 and self.dfas[e["strings"]].run(raw) >= 0
            if not ok:
                self._violate("ENUM", f"{value!r} no está en la enumeración",
                              path)
        if s:
            for d, src in zip(s["patterns"], s["pattern_src"]):
                if self.dfas[d].run(raw) < 0:
                    self._violate("PATTERN", f"{value!r} no coincide con "
                                  f"{src!r}", path)
        if n["num"] and dec is not None:
            for op, ci in n["num"]["checks"]:
                c = self.consts[ci]
                if op == "mul":
                    if multiple_of(dec, c) is False:
                        self._violate("MULTIPLE_OF", "no es múltiplo", path)
                    continue
                cmp = compare(dec, c)
                bad = {"ge": cmp < 0, "gt": cmp <= 0, "le": cmp > 0,
                       "lt": cmp >= 0}[op]
                if bad:
                    code = {"ge": "MINIMUM", "gt": "EXCLUSIVE_MINIMUM",
                            "le": "MAXIMUM", "lt": "EXCLUSIVE_MAXIMUM"}[op]
                    self._violate(code, f"{value} fuera de rango ({op} {c})",
                                  path)


def validate_xml(ir: dict, data: bytes, **kw) -> Result:
    chunk = kw.pop("chunk", None)
    return XmlValidator(ir, **kw).validate(data, chunk)
