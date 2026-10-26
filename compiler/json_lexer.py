"""Tokenizador JSON escrito a mano, en streaming por bloques.

No usa ``json.loads`` ni ninguna librería de parseo: el AFD está
implementado byte a byte en este módulo, como exige la Fase 1.

Tokens (kind, value):
- ``{`` ``}`` ``[`` ``]`` ``:`` ``,`` : value None, un byte cada uno.
- ``STRING`` : value = cadena decodificada (escapes y ``\\u`` resueltos).
- ``NUMBER`` : value = lexema original (nunca se convierte a float: en la
  Fase 3 los límites se comparan como cadenas decimales normalizadas,
  así no se pierde precisión con enteros de 40 dígitos ni con 1e400).
- ``TRUE`` / ``FALSE`` / ``NULL`` : value True / False / None.

Estado que persiste entre ``feed``:
- ``_buf`` : bytes aún sin tokenizar (un token partido entre bloques
  se queda aquí, no se emite a medias).
- El validador UTF-8 de :mod:`compiler.utf8` (una secuencia multibyte
  partida entre bloques no es un error, solo queda pendiente).

Regla clave para la equivalencia por bloques: un número o un literal
no se emite hasta que aparece un delimitador o se llama a ``finish()``,
porque ``0`` podría convertirse en ``01`` (inválido) con el siguiente
byte, y ``tru`` podría convertirse en ``true`` o en ``trux`` (inválido).
Una cadena sí se emite en cuanto aparece su comilla de cierre.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .utf8 import Utf8Validator

_PUNCT = {ord("{"): "{", ord("}"): "}", ord("["): "[", ord("]"): "]",
          ord(":"): ":", ord(","): ","}
_WS = {0x20, 0x09, 0x0A, 0x0D}
_DIGITS = set(b"0123456789")
_HEX = set(b"0123456789abcdefABCDEF")
_ESCAPES = {ord('"'): '"', ord("\\"): "\\", ord("/"): "/",
            ord("b"): "\b", ord("f"): "\f", ord("n"): "\n",
            ord("r"): "\r", ord("t"): "\t"}
_LITERALS = {b"true": ("TRUE", True), b"false": ("FALSE", False),
             b"null": ("NULL", None)}


class LexerError(ValueError):
    """Error léxico, con el offset en bytes donde se detectó."""

    def __init__(self, message: str, offset: int):
        super().__init__(f"{message} (byte {offset})")
        self.offset = offset


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any
    offset: int  # offset en bytes del primer byte del token

    def __repr__(self) -> str:  # compacto para depurar y para tests
        return f"Token({self.kind}, {self.value!r}, @{self.offset})"


def _decode_string_content(raw: bytes, start_offset: int) -> str:
    """Decodifica el contenido (sin comillas) de una cadena JSON.

    Valida escapes, pares sustitutos ``\\u`` y UTF-8 estricto. Un
    sustituto alto (D800..DBFF) exige inmediatamente ``\\uDC00..DFFF``;
    un sustituto bajo suelto es error, igual que ``\\uD800`` sin pareja.
    """
    out: list[str] = []
    pending: bytearray = bytearray()  # bytes UTF-8 crudos sin decodificar

    def flush() -> None:
        if pending:
            try:
                out.append(bytes(pending).decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise LexerError("UTF-8 inválido en cadena",
                                 start_offset) from exc
            pending.clear()

    i = 0
    while i < len(raw):
        b = raw[i]
        if b == 0x5C:  # backslash
            if i + 1 >= len(raw):
                raise LexerError("escape incompleto en cadena", start_offset)
            esc = raw[i + 1]
            if esc == ord("u"):
                if i + 5 >= len(raw):
                    raise LexerError("escape \\u incompleto", start_offset)
                hexpart = raw[i + 2:i + 6]
                if any(c not in _HEX for c in hexpart):
                    raise LexerError("escape \\u no hexadecimal", start_offset)
                code = int(bytes(hexpart), 16)
                flush()
                if 0xD800 <= code <= 0xDBFF:  # sustituto alto
                    if (i + 11 < len(raw) and raw[i + 6] == 0x5C
                            and raw[i + 7] == ord("u")):
                        hex2 = raw[i + 8:i + 12]
                        if any(c not in _HEX for c in hex2):
                            raise LexerError("escape \\u no hexadecimal",
                                             start_offset)
                        code2 = int(bytes(hex2), 16)
                        if not (0xDC00 <= code2 <= 0xDFFF):
                            raise LexerError(
                                "sustituto alto sin sustituto bajo",
                                start_offset)
                        out.append(chr(0x10000 + ((code - 0xD800) << 10)
                                       + (code2 - 0xDC00)))
                        i += 12
                        continue
                    raise LexerError("sustituto alto sin pareja", start_offset)
                if 0xDC00 <= code <= 0xDFFF:
                    raise LexerError("sustituto bajo suelto", start_offset)
                out.append(chr(code))
                i += 6
                continue
            if esc not in _ESCAPES:
                raise LexerError(f"escape inválido \\{chr(esc)}", start_offset)
            flush()
            out.append(_ESCAPES[esc])
            i += 2
            continue
        if b < 0x20:
            raise LexerError("carácter de control sin escapar en cadena",
                             start_offset)
        pending.append(b)
        i += 1
    flush()
    return "".join(out)


class JsonTokenizer:
    """Tokenizador JSON en streaming: ``feed`` + ``finish``."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._offset = 0  # offset del primer byte de _buf en la entrada
        self._utf8 = Utf8Validator()
        self._finished = False

    # -- API pública ----------------------------------------------------
    def feed(self, data: bytes) -> list[Token]:
        if self._finished:
            raise LexerError("feed después de finish", self._offset)
        self._utf8.feed(data)
        self._buf.extend(data)
        tokens, _ = self._scan(final=False)
        return tokens

    def finish(self) -> list[Token]:
        if self._finished:
            return []
        self._finished = True
        self._utf8.finish()
        tokens, _ = self._scan(final=True)
        # _scan con final=True ya consumió todo salvo espacios en blanco.
        rest = bytes(self._buf).strip(b" \t\n\r")
        if rest:
            raise LexerError("entrada incompleta al final", self._offset)
        return tokens

    def tokenize(self, data: bytes) -> list[Token]:
        """Atajo para una entrada completa (equivale a feed + finish)."""
        return self.feed(data) + self.finish()

    # -- Escaneo --------------------------------------------------------
    def _scan(self, final: bool) -> tuple[list[Token], int]:
        tokens: list[Token] = []
        i = 0
        buf = self._buf
        while i < len(buf):
            b = buf[i]
            if b in _WS:
                i += 1
                continue
            start = self._offset + i
            if b in _PUNCT:
                tokens.append(Token(_PUNCT[b], None, start))
                i += 1
                continue
            if b == ord('"'):
                result = self._scan_string(i, final)
                if result is None:
                    break  # cadena partida entre bloques: esperar más
                token, i = result
                tokens.append(token)
                continue
            if b == ord("-") or b in _DIGITS:
                result = self._scan_number(i, final)
                if result is None:
                    break  # número partido: esperar delimitador o finish
                token, i = result
                tokens.append(token)
                continue
            if b in (ord("t"), ord("f"), ord("n")):
                result = self._scan_literal(i, final)
                if result is None:
                    break
                token, i = result
                tokens.append(token)
                continue
            raise LexerError(f"byte inesperado 0x{b:02x}", start)
        # Consumir lo escaneado; lo que queda es un token incompleto.
        del buf[:i]
        self._offset += i
        return tokens, i

    def _scan_string(self, start: int, final: bool):
        """Devuelve (token, índice siguiente) o None si está incompleta."""
        buf = self._buf
        i = start + 1
        state = "normal"  # normal | escape | unicode
        hex_left = 0
        while i < len(buf):
            b = buf[i]
            if state == "normal":
                if b == ord('"'):
                    raw = bytes(buf[start + 1:i])
                    value = _decode_string_content(raw, self._offset + start)
                    return Token("STRING", value, self._offset + start), i + 1
                if b == 0x5C:
                    state = "escape"
                elif b < 0x20:
                    raise LexerError("carácter de control sin escapar",
                                     self._offset + i)
                i += 1
            elif state == "escape":
                if b == ord("u"):
                    state, hex_left = "unicode", 4
                elif b in _ESCAPES:
                    state = "normal"
                else:
                    raise LexerError(f"escape inválido \\{chr(b)}",
                                     self._offset + i)
                i += 1
            else:  # unicode: 4 dígitos hexadecimales
                if b not in _HEX:
                    raise LexerError("escape \\u no hexadecimal",
                                     self._offset + i)
                hex_left -= 1
                if hex_left == 0:
                    state = "normal"
                i += 1
        if final:
            raise LexerError("cadena sin cerrar", self._offset + start)
        return None

    def _scan_number(self, start: int, final: bool):
        """AFD de número JSON. None = partido entre bloques (no final)."""
        buf = self._buf
        i = start
        # Estado: int | int0 | frac | frac_digits | exp | exp_sign | exp_digits
        if i < len(buf) and buf[i] == ord("-"):
            i += 1
        if i >= len(buf):
            if final:
                raise LexerError("número incompleto", self._offset + start)
            return None
        if buf[i] == ord("0"):
            i += 1
            state = "int0"
        elif buf[i] in _DIGITS:
            while i < len(buf) and buf[i] in _DIGITS:
                i += 1
            state = "int"
        else:
            raise LexerError("número inválido", self._offset + start)
        accepting = state in ("int", "int0")
        while True:
            if i >= len(buf):
                if final:
                    if accepting:
                        break
                    raise LexerError("número incompleto",
                                     self._offset + start)
                return None
            b = buf[i]
            if state == "int0" and b in _DIGITS:
                raise LexerError("cero inicial seguido de dígito",
                                 self._offset + i)
            if b == ord(".") and state in ("int", "int0"):
                i += 1
                state = "frac"
                accepting = False
                continue
            if b in (ord("e"), ord("E")) and state in ("int", "int0", "frac_digits"):
                i += 1
                state = "exp"
                accepting = False
                continue
            if state == "frac" and b in _DIGITS:
                while i < len(buf) and buf[i] in _DIGITS:
                    i += 1
                state, accepting = "frac_digits", True
                continue
            if state == "exp" and b in (ord("+"), ord("-")):
                i += 1
                state = "exp_sign"
                continue
            if state in ("exp", "exp_sign") and b in _DIGITS:
                while i < len(buf) and buf[i] in _DIGITS:
                    i += 1
                state, accepting = "exp_digits", True
                continue
            if b in _DIGITS and state in ("int",):
                # Ya consumido arriba; este camino no debería alcanzarse.
                i += 1
                continue
            break  # delimitador u otro byte: fin del número
        if not accepting:
            raise LexerError("número incompleto", self._offset + start)
        raw = bytes(buf[start:i]).decode("ascii")
        return Token("NUMBER", raw, self._offset + start), i

    def _scan_literal(self, start: int, final: bool):
        buf = self._buf
        rest = bytes(buf[start:])
        for word, (kind, value) in _LITERALS.items():
            if rest.startswith(word):
                after = start + len(word)
                if after >= len(buf):
                    if final:
                        return Token(kind, value, self._offset + start), after
                    return None  # podría ser "truex" en el siguiente bloque
                nxt = buf[after]
                if nxt in _WS or nxt in _PUNCT:
                    return Token(kind, value, self._offset + start), after
                raise LexerError("literal inválido", self._offset + start)
            if word.startswith(rest) and len(rest) < len(word):
                if final:
                    raise LexerError("literal incompleto", self._offset + start)
                return None
        raise LexerError("literal inválido", self._offset + start)
