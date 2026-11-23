"""Gramáticas libres de contexto del proyecto (Fase 2).

Cuatro gramáticas, todas LL(1) (se verifica en ``tests/``):

1. ``JSON_GRAMMAR`` — JSON (RFC 8259) sobre los tokens de
   :class:`compiler.json_lexer.JsonTokenizer`.
2. ``XML_GRAMMAR`` — XML bien formado (subconjunto sin DTD) sobre los
   tokens de :class:`compiler.xml_lexer.XmlTokenizer`. El emparejamiento
   del nombre de apertura con el de cierre es una restricción sensible
   al contexto sobre un alfabeto de nombres no acotado; se verifica con
   la pila del parser (acción semántica), igual que hace el DPDA.
3. ``SCHEMA_GRAMMAR`` — metalenguaje de JSON Schema: un esquema es un
   JSON cuyas claves son palabras clave. El lexer del esquema convierte
   cada cadena que coincide con una palabra clave en un terminal propio
   (``"type"``, ``"properties"`` ...), así la gramática exige, por
   ejemplo, que ``minimum`` vaya seguido de un NUMBER: parte del
   meta-esquema queda verificada sintácticamente.
4. ``REGEX_GRAMMAR`` — subconjunto regular de ECMA-262 usado en
   ``pattern`` (sin retrorreferencias ni lookaround, que no son
   regulares).
"""

from __future__ import annotations

from .grammar import Grammar

JSON_GRAMMAR = Grammar.parse("JSON", """
JSON -> Value
Value -> Object | Array | STRING | NUMBER | TRUE | FALSE | NULL
Object -> '{' Members '}'
Members -> Pair MembersTail | ε
MembersTail -> ',' Pair MembersTail | ε
Pair -> STRING ':' Value
Array -> '[' Elements ']'
Elements -> Value ElementsTail | ε
ElementsTail -> ',' Value ElementsTail | ε
""")

XML_GRAMMAR = Grammar.parse("XML", """
Document -> Prolog Element Epilog
Prolog -> PI Prolog | COMMENT Prolog | ε
Epilog -> COMMENT Epilog | PI Epilog | ε
Element -> TAG_EMPTY | TAG_OPEN Content TAG_CLOSE
Content -> Item Content | ε
Item -> Element | TEXT | COMMENT | PI
""")

# Palabras clave del subconjunto, agrupadas por la forma de su valor.
SCHEMA_KW_SCHEMA = ["additionalProperties", "items", "contains", "not",
                    "if", "then", "else", "propertyNames"]
SCHEMA_KW_MAP = ["properties", "patternProperties", "$defs", "definitions",
                 "dependentSchemas"]
SCHEMA_KW_LIST = ["allOf", "anyOf", "oneOf", "prefixItems"]
SCHEMA_KW_NUMBER = ["minimum", "maximum", "exclusiveMinimum",
                    "exclusiveMaximum", "multipleOf", "minLength",
                    "maxLength", "minItems", "maxItems", "minProperties",
                    "maxProperties", "minContains", "maxContains"]
SCHEMA_KW_STRING = ["pattern", "$ref", "format"]
SCHEMA_KW_BOOL = ["uniqueItems"]
SCHEMA_KEYWORDS = (["type", "required", "enum", "const",
                    "dependentRequired"] + SCHEMA_KW_SCHEMA + SCHEMA_KW_MAP
                   + SCHEMA_KW_LIST + SCHEMA_KW_NUMBER + SCHEMA_KW_STRING
                   + SCHEMA_KW_BOOL)


def kw(name: str) -> str:
    """Nombre del terminal de una palabra clave: ``"type"``."""
    return f'"{name}"'


def _schema_grammar_text() -> str:
    q = lambda k: f"'{kw(k)}'"  # noqa: E731
    alts = [f"{q('type')} ':' TypeSpec",
            f"{q('required')} ':' '[' StrList ']'",
            f"{q('enum')} ':' '[' ValueList ']'",
            f"{q('const')} ':' Value",
            f"{q('dependentRequired')} ':' '{{' DepMap '}}'"]
    alts += [f"{q(k)} ':' Schema" for k in SCHEMA_KW_SCHEMA]
    alts += [f"{q(k)} ':' '{{' SchemaMap '}}'" for k in SCHEMA_KW_MAP]
    alts += [f"{q(k)} ':' '[' SchemaList ']'" for k in SCHEMA_KW_LIST]
    alts += [f"{q(k)} ':' NUMBER" for k in SCHEMA_KW_NUMBER]
    alts += [f"{q(k)} ':' Str" for k in SCHEMA_KW_STRING]
    alts += [f"{q(k)} ':' Bool" for k in SCHEMA_KW_BOOL]
    alts.append("STRING ':' Value")  # anotación: title, description...
    str_alts = ["STRING"] + [q(k) for k in SCHEMA_KEYWORDS]
    return "\n".join([
        "Schema -> '{' KwList '}' | TRUE | FALSE",
        "KwList -> Kw KwTail | ε",
        "KwTail -> ',' Kw KwTail | ε",
        "Kw -> " + " | ".join(alts),
        "TypeSpec -> Str | '[' StrList ']'",
        "SchemaMap -> Str ':' Schema SchemaMapTail | ε",
        "SchemaMapTail -> ',' Str ':' Schema SchemaMapTail | ε",
        "SchemaList -> Schema SchemaListTail | ε",
        "SchemaListTail -> ',' Schema SchemaListTail | ε",
        "StrList -> Str StrListTail | ε",
        "StrListTail -> ',' Str StrListTail | ε",
        "DepMap -> Str ':' '[' StrList ']' DepMapTail | ε",
        "DepMapTail -> ',' Str ':' '[' StrList ']' DepMapTail | ε",
        "ValueList -> Value ValueListTail | ε",
        "ValueListTail -> ',' Value ValueListTail | ε",
        "Value -> '{' Members '}' | '[' ValueList ']' | Str | NUMBER"
        " | TRUE | FALSE | NULL",
        "Members -> Str ':' Value MembersTail | ε",
        "MembersTail -> ',' Str ':' Value MembersTail | ε",
        "Bool -> TRUE | FALSE",
        "Str -> " + " | ".join(str_alts),
    ])


SCHEMA_GRAMMAR = Grammar.parse("Metalenguaje de esquemas",
                               _schema_grammar_text())

REGEX_GRAMMAR = Grammar.parse("Regex (pattern)", """
Regex -> Alt
Alt -> Concat AltTail
AltTail -> '|' Concat AltTail | ε
Concat -> Repeat Concat | ε
Repeat -> Atom Quant
Quant -> '*' | '+' | '?' | QUANT | ε
Atom -> CHAR | '.' | CLASS | '(' Alt ')' | CARET | DOLLAR
""")
# Nota: las anclas ^ y $ se lexean como CARET y DOLLAR porque '$' es
# también el marcador de fin de entrada de la tabla LL(1): con el
# mismo nombre la celda M[Concat, $] tendría un conflicto real.

ALL_GRAMMARS = [JSON_GRAMMAR, XML_GRAMMAR, SCHEMA_GRAMMAR, REGEX_GRAMMAR]
