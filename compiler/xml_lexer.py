"""Tokenizador XML (subconjunto bien formado) en streaming por bloques.

Tokens (kind, value):
- ``TAG_OPEN``  : value = nombre del elemento, p. ej. ``<libro autor="x">``.
- ``TAG_CLOSE`` : value = nombre, p. ej. ``</libro>``.
- ``TAG_EMPTY`` : value = nombre, p. ej. ``<libro/>``.
- ``TEXT``      : value = texto entre etiquetas (sin decodificar entidades;
  la resolución de entidades es trabajo del parser, Fase 2).
- ``COMMENT``   : value = contenido de ``<!-- ... -->``.
- ``PI``        : value = contenido de ``<? ... ?>``.

Subconjunto y límites de la Semana 1 (documentados, no ocultos):
- Se rechaza ``<!DOCTYPE`` en el tokenizador: es la mitigación de XXE
  y de expansión de entidades por diseño (el enunciado pide XML sin
  DTD). Cualquier otro ``<!`` que no sea comentario es error.
- Los nombres de elemento y atributo siguen
  ``[A-Za-z_][A-Za-z0-9_.-]*`` (sin espacios de nombres en Semana 1).
- Los valores de atributo van entre comillas simples o dobles.
- El emparejamiento apertura/cierre (``<w>`` debe cerrarse con
  ``</w>``) NO es trabajo del lexer: lo verifica el parser de la
  Fase 2 con la pila. El lexer solo entrega etiquetas bien formadas
  sintácticamente.
- Igual que en JSON, una etiqueta partida entre bloques se queda en
  el buffer y no se emite a medias; ``finish()`` convierte una
  etiqueta o un texto sin cerrar en error (el texto final sin
  ``<`` siguiente sí se emite en ``finish()``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .json_lexer import LexerError
from .utf8 import Utf8Validator


@dataclass(frozen=True)
class XmlToken:
    kind: str
    value: Any
    offset: int

    def __repr__(self) -> str:
        return f"XmlToken({self.kind}, {self.value!r}, @{self.offset})"


def _is_name_start(b: int) -> bool:
    return (0x41 <= b <= 0x5A) or (0x61 <= b <= 0x7A) or b == ord("_")


def _is_name_char(b: int) -> bool:
    return _is_name_start(b) or (0x30 <= b <= 0x39) or b in (ord("-"), ord("."))


class XmlTokenizer:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._offset = 0
        self._utf8 = Utf8Validator()
        self._finished = False

    def feed(self, data: bytes) -> list[XmlToken]:
        if self._finished:
            raise LexerError("feed después de finish", self._offset)
        self._utf8.feed(data)
        self._buf.extend(data)
        return self._scan(final=False)

    def finish(self) -> list[XmlToken]:
        if self._finished:
            return []
        self._finished = True
        self._utf8.finish()
        tokens = self._scan(final=True)
        if self._buf:
            raise LexerError("entrada XML incompleta al final", self._offset)
        return tokens

    def tokenize(self, data: bytes) -> list[XmlToken]:
        return self.feed(data) + self.finish()

    # -- Escaneo --------------------------------------------------------
    def _scan(self, final: bool) -> list[XmlToken]:
        tokens: list[XmlToken] = []
        buf = self._buf
        i = 0
        while i < len(buf):
            if buf[i] == ord("<"):
                result = self._scan_tag(i, final)
                if result is None:
                    break
                token, i = result
                tokens.append(token)
                continue
            # Texto hasta el siguiente '<' (o hasta el final).
            nxt = buf.find(b"<", i)
            if nxt == -1:
                if final:
                    text = bytes(buf[i:]).decode("utf-8")
                    if text:
                        tokens.append(XmlToken("TEXT", text,
                                               self._offset + i))
                    i = len(buf)
                break  # texto partido: esperar más (o finish)
            if nxt > i:
                text = bytes(buf[i:nxt]).decode("utf-8")
                tokens.append(XmlToken("TEXT", text, self._offset + i))
            i = nxt
        del buf[:i]
        self._offset += i
        return tokens

    def _scan_tag(self, start: int, final: bool):
        """(token, índice siguiente) o None si la etiqueta está partida."""
        buf = self._buf
        # Comentario y PI necesitan su terminador completo.
        if bytes(buf[start:start + 4]) == b"<!--":
            end = buf.find(b"-->", start + 4)
            if end == -1:
                if final:
                    raise LexerError("comentario sin cerrar",
                                     self._offset + start)
                return None
            return (XmlToken("COMMENT",
                             bytes(buf[start + 4:end]).decode("utf-8"),
                             self._offset + start), end + 3)
        if bytes(buf[start:start + 2]) == b"<?":
            end = buf.find(b"?>", start + 2)
            if end == -1:
                if final:
                    raise LexerError("instrucción sin cerrar",
                                     self._offset + start)
                return None
            return (XmlToken("PI", bytes(buf[start + 2:end]).decode("utf-8"),
                             self._offset + start), end + 2)
        if bytes(buf[start:start + 2]) == b"<!":
            # No decidir todavía si la declaración está partida entre
            # bloques: "<!" podría ser "<!--" (comentario, ya tratado
            # arriba si hay 4 bytes) o "<!DOCTYPE" (9 bytes).
            prefix = bytes(buf[start:])
            if not final and (b"<!DOCTYPE".startswith(prefix.upper())
                              or b"<!--".startswith(prefix)):
                return None
            if bytes(buf[start:start + 9]).upper() == b"<!DOCTYPE":
                raise LexerError("DOCTYPE rechazado (mitigación XXE)",
                                 self._offset + start)
            raise LexerError("declaración <! no soportada",
                             self._offset + start)
        end = buf.find(b">", start)
        if end == -1:
            if final:
                raise LexerError("etiqueta sin cerrar", self._offset + start)
            return None
        inner = bytes(buf[start + 1:end])
        if inner.startswith(b"/"):
            name = inner[1:].strip()
            if not name or not _valid_name(name):
                raise LexerError("etiqueta de cierre inválida",
                                 self._offset + start)
            return (XmlToken("TAG_CLOSE", name.decode("ascii"),
                             self._offset + start), end + 1)
        empty = inner.rstrip().endswith(b"/")
        if empty:
            inner = inner.rstrip()[:-1]
        parts = inner.split(None, 1)
        if not parts or not _valid_name(parts[0]):
            raise LexerError("etiqueta inválida", self._offset + start)
        if len(parts) == 2:
            self._check_attributes(parts[1], start)
        kind = "TAG_EMPTY" if empty else "TAG_OPEN"
        return (XmlToken(kind, parts[0].decode("ascii"),
                         self._offset + start), end + 1)

    def _check_attributes(self, attrs: bytes, tag_offset: int) -> None:
        i = 0
        while i < len(attrs):
            while i < len(attrs) and attrs[i] in b" \t\n\r":
                i += 1
            if i >= len(attrs):
                break
            j = i
            while j < len(attrs) and _is_name_char(attrs[j]):
                j += 1
            if j == i or not _is_name_start(attrs[i]):
                raise LexerError("atributo inválido", self._offset + tag_offset)
            while j < len(attrs) and attrs[j] in b" \t\n\r":
                j += 1
            if j >= len(attrs) or attrs[j] != ord("="):
                raise LexerError("atributo sin =", self._offset + tag_offset)
            j += 1
            while j < len(attrs) and attrs[j] in b" \t\n\r":
                j += 1
            if j >= len(attrs) or attrs[j] not in (ord('"'), ord("'")):
                raise LexerError("valor de atributo sin comillas",
                                 self._offset + tag_offset)
            quote = attrs[j]
            k = attrs.find(bytes([quote]), j + 1)
            if k == -1:
                raise LexerError("valor de atributo sin cerrar",
                                 self._offset + tag_offset)
            i = k + 1

    # -- Utilidad -------------------------------------------------------


def _valid_name(name: bytes) -> bool:
    return bool(name) and _is_name_start(name[0]) and all(
        _is_name_char(c) for c in name)
