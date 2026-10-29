#include "schema.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Parser mínimo, sin dependencias: descenso recursivo (LL(1)) para   */
/* el subconjunto de JSON que usa el esquema y el payload. Cada      */
/* decisión se toma con un solo símbolo de anticipación: el carácter  */
/* actual decide entre objeto, arreglo, cadena, número o literal.    */
/* No hay strstr sobre el texto: una clave solo cuenta si aparece    */
/* como clave real de un objeto, en su posición, con su sintaxis.     */
/* ------------------------------------------------------------------ */

typedef struct {
    const char *cur;
    const char *begin;
    char *error;
    size_t error_size;
} Parser;

typedef enum {
    V_NULL,
    V_BOOL,
    V_NUMBER,
    V_STRING,
    V_ARRAY,
    V_OBJECT
} ValueKind;

static bool fail_at(Parser *p, const char *msg) {
    if (p->error != NULL && p->error_size > 0) {
        snprintf(p->error, p->error_size, "%s (byte %zu)", msg,
                 (size_t)(p->cur - p->begin));
    }
    return false;
}

static void skip_ws(Parser *p) {
    while (*p->cur == ' ' || *p->cur == '\n' || *p->cur == '\r' ||
           *p->cur == '\t') {
        p->cur++;
    }
}

static bool expect_char(Parser *p, char c) {
    skip_ws(p);
    if (*p->cur != c) {
        return fail_at(p, "expected character");
    }
    p->cur++;
    return true;
}

static int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Lee \uXXXX y deja el punto de código en *cp. No avanza ante error. */
static bool read_unicode_escape(Parser *p, unsigned *cp) {
    unsigned value = 0;
    for (int i = 0; i < 4; i++) {
        int h = hex_value(p->cur[i]);
        if (h < 0) {
            return fail_at(p, "invalid \\u escape in string");
        }
        value = value * 16u + (unsigned)h;
    }
    p->cur += 4;
    *cp = value;
    return true;
}

static bool append_utf8(char *out, size_t out_size, size_t *len, unsigned cp) {
    char bytes[4];
    size_t n = 0;
    if (cp < 0x80u) {
        bytes[n++] = (char)cp;
    } else if (cp < 0x800u) {
        bytes[n++] = (char)(0xC0u | (cp >> 6));
        bytes[n++] = (char)(0x80u | (cp & 0x3Fu));
    } else if (cp < 0x10000u) {
        bytes[n++] = (char)(0xE0u | (cp >> 12));
        bytes[n++] = (char)(0x80u | ((cp >> 6) & 0x3Fu));
        bytes[n++] = (char)(0x80u | (cp & 0x3Fu));
    } else {
        bytes[n++] = (char)(0xF0u | (cp >> 18));
        bytes[n++] = (char)(0x80u | ((cp >> 12) & 0x3Fu));
        bytes[n++] = (char)(0x80u | ((cp >> 6) & 0x3Fu));
        bytes[n++] = (char)(0x80u | (cp & 0x3Fu));
    }
    if (out != NULL) {
        if (*len + n >= out_size) {
            return false;
        }
        memcpy(out + *len, bytes, n);
    }
    *len += n;
    return true;
}

/*
 * Parsea una cadena JSON. Si out es NULL solo la valida y la salta
 * (sirve para valores de cadena del payload, que no hay que guardar).
 * Si out no es NULL, guarda la cadena decodificada, sin truncar en
 * silencio: una cadena más larga que el búfer es un error.
 */
static bool parse_string(Parser *p, char *out, size_t out_size) {
    size_t len = 0;
    skip_ws(p);
    if (*p->cur != '"') {
        return fail_at(p, "expected string");
    }
    p->cur++;
    while (1) {
        unsigned char c = (unsigned char)*p->cur;
        if (c == '\0') {
            return fail_at(p, "unterminated string");
        }
        if (c == '"') {
            p->cur++;
            break;
        }
        if (c < 0x20u) {
            return fail_at(p, "control character inside string");
        }
        if (c == '\\') {
            p->cur++;
            char e = *p->cur;
            if (e == '\0') {
                return fail_at(p, "unterminated escape in string");
            }
            p->cur++;
            switch (e) {
                case '"': case '\\': case '/':
                    if (!append_utf8(out, out_size, &len, (unsigned)e)) {
                        return fail_at(p, "string too long");
                    }
                    break;
                case 'b':
                    if (!append_utf8(out, out_size, &len, 0x08u)) {
                        return fail_at(p, "string too long");
                    }
                    break;
                case 'f':
                    if (!append_utf8(out, out_size, &len, 0x0Cu)) {
                        return fail_at(p, "string too long");
                    }
                    break;
                case 'n':
                    if (!append_utf8(out, out_size, &len, 0x0Au)) {
                        return fail_at(p, "string too long");
                    }
                    break;
                case 'r':
                    if (!append_utf8(out, out_size, &len, 0x0Du)) {
                        return fail_at(p, "string too long");
                    }
                    break;
                case 't':
                    if (!append_utf8(out, out_size, &len, 0x09u)) {
                        return fail_at(p, "string too long");
                    }
                    break;
                case 'u': {
                    unsigned cp = 0;
                    if (!read_unicode_escape(p, &cp)) {
                        return false;
                    }
                    if (cp >= 0xD800u && cp <= 0xDBFFu) {
                        /* Sustituto alto: exige \uDC00-\uDFFF detrás. */
                        if (p->cur[0] != '\\' || p->cur[1] != 'u') {
                            return fail_at(p, "lone high surrogate in string");
                        }
                        p->cur += 2;
                        unsigned low = 0;
                        if (!read_unicode_escape(p, &low)) {
                            return false;
                        }
                        if (low < 0xDC00u || low > 0xDFFFu) {
                            return fail_at(p, "invalid low surrogate in string");
                        }
                        cp = 0x10000u + ((cp - 0xD800u) << 10) + (low - 0xDC00u);
                    } else if (cp >= 0xDC00u && cp <= 0xDFFFu) {
                        return fail_at(p, "lone low surrogate in string");
                    }
                    if (!append_utf8(out, out_size, &len, cp)) {
                        return fail_at(p, "string too long");
                    }
                    break;
                }
                default:
                    return fail_at(p, "invalid escape in string");
            }
            continue;
        }
        /* Byte normal (incluye UTF-8 multibyte, que se copia tal cual). */
        if (!append_utf8(out, out_size, &len, c)) {
            return fail_at(p, "string too long");
        }
        p->cur++;
    }
    if (out != NULL) {
        out[len] = '\0';
    }
    return true;
}

/* ¿El número es entero? Análisis exacto sobre el texto, sin float. */
static bool number_is_integer(const char *int_digits, size_t int_len,
                              const char *frac_digits, size_t frac_len,
                              long exp_val) {
    size_t total = int_len + frac_len;
    bool all_zero = true;
    for (size_t i = 0; i < int_len; i++) {
        if (int_digits[i] != '0') all_zero = false;
    }
    for (size_t i = 0; i < frac_len; i++) {
        if (frac_digits[i] != '0') all_zero = false;
    }
    if (all_zero) {
        return true;
    }
    long shift = (long)frac_len - exp_val;
    if (shift <= 0) {
        return true;
    }
    if ((size_t)shift >= total) {
        return false; /* Valor < 1 y distinto de cero. */
    }
    for (long i = 0; i < shift; i++) {
        size_t idx = total - 1u - (size_t)i;
        char d = idx < int_len ? int_digits[idx]
                               : frac_digits[idx - int_len];
        if (d != '0') {
            return false;
        }
    }
    return true;
}

static bool parse_number(Parser *p, bool *is_integer) {
    const char *start = p->cur;
    const char *int_digits = NULL;
    const char *frac_digits = NULL;
    size_t int_len = 0;
    size_t frac_len = 0;
    long exp_val = 0;

    if (*p->cur == '-') {
        p->cur++;
    }
    if (*p->cur == '0') {
        int_digits = p->cur;
        int_len = 1;
        p->cur++;
    } else if (*p->cur >= '1' && *p->cur <= '9') {
        int_digits = p->cur;
        while (isdigit((unsigned char)*p->cur)) {
            p->cur++;
        }
        int_len = (size_t)(p->cur - int_digits);
    } else {
        p->cur = start;
        return fail_at(p, "invalid number");
    }
    if (*p->cur == '.') {
        p->cur++;
        if (!isdigit((unsigned char)*p->cur)) {
            return fail_at(p, "invalid number: digit expected after '.'");
        }
        frac_digits = p->cur;
        while (isdigit((unsigned char)*p->cur)) {
            p->cur++;
        }
        frac_len = (size_t)(p->cur - frac_digits);
    }
    if (*p->cur == 'e' || *p->cur == 'E') {
        p->cur++;
        bool neg = false;
        if (*p->cur == '+' || *p->cur == '-') {
            neg = (*p->cur == '-');
            p->cur++;
        }
        if (!isdigit((unsigned char)*p->cur)) {
            return fail_at(p, "invalid number: digit expected in exponent");
        }
        long e = 0;
        while (isdigit((unsigned char)*p->cur)) {
            if (e < 100000L) {
                e = e * 10L + (*p->cur - '0');
            }
            p->cur++;
        }
        exp_val = neg ? -e : e;
    }
    if (frac_digits == NULL) {
        frac_digits = "";
    }
    *is_integer = number_is_integer(int_digits, int_len, frac_digits,
                                    frac_len, exp_val);
    return true;
}

static bool parse_literal(Parser *p, const char *word, ValueKind kind,
                          ValueKind *out_kind) {
    size_t n = strlen(word);
    if (strncmp(p->cur, word, n) != 0) {
        return fail_at(p, "invalid literal");
    }
    /* Un literal no puede ir pegado a una letra: "truely" no es true. */
    char next = p->cur[n];
    if (isalpha((unsigned char)next)) {
        return fail_at(p, "invalid literal");
    }
    p->cur += n;
    *out_kind = kind;
    return true;
}

static bool parse_value(Parser *p, int depth, ValueKind *out_kind,
                        bool *out_is_integer);

static bool parse_array(Parser *p, int depth, ValueKind *out_kind) {
    p->cur++; /* '[' */
    skip_ws(p);
    if (*p->cur == ']') {
        p->cur++;
        *out_kind = V_ARRAY;
        return true;
    }
    while (1) {
        ValueKind inner = V_NULL;
        bool inner_int = false;
        if (!parse_value(p, depth + 1, &inner, &inner_int)) {
            return false;
        }
        skip_ws(p);
        if (*p->cur == ',') {
            p->cur++;
            continue;
        }
        if (*p->cur == ']') {
            p->cur++;
            break;
        }
        return fail_at(p, "expected ',' or ']' in array");
    }
    *out_kind = V_ARRAY;
    return true;
}

static bool parse_object_skip(Parser *p, int depth) {
    p->cur++; /* '{' */
    skip_ws(p);
    if (*p->cur == '}') {
        p->cur++;
        return true;
    }
    while (1) {
        skip_ws(p);
        if (!parse_string(p, NULL, 0)) {
            return false;
        }
        if (!expect_char(p, ':')) {
            return false;
        }
        ValueKind inner = V_NULL;
        bool inner_int = false;
        if (!parse_value(p, depth + 1, &inner, &inner_int)) {
            return false;
        }
        skip_ws(p);
        if (*p->cur == ',') {
            p->cur++;
            continue;
        }
        if (*p->cur == '}') {
            p->cur++;
            break;
        }
        return fail_at(p, "expected ',' or '}' in object");
    }
    return true;
}

static bool parse_value(Parser *p, int depth, ValueKind *out_kind,
                        bool *out_is_integer) {
    skip_ws(p);
    if (depth > MAX_JSON_DEPTH) {
        return fail_at(p, "JSON nesting too deep");
    }
    *out_is_integer = false;
    char c = *p->cur;
    if (c == '"') {
        if (!parse_string(p, NULL, 0)) {
            return false;
        }
        *out_kind = V_STRING;
        return true;
    }
    if (c == '{') {
        if (!parse_object_skip(p, depth)) {
            return false;
        }
        *out_kind = V_OBJECT;
        return true;
    }
    if (c == '[') {
        return parse_array(p, depth, out_kind);
    }
    if (c == 't') {
        return parse_literal(p, "true", V_BOOL, out_kind);
    }
    if (c == 'f') {
        return parse_literal(p, "false", V_BOOL, out_kind);
    }
    if (c == 'n') {
        return parse_literal(p, "null", V_NULL, out_kind);
    }
    if (c == '-' || isdigit((unsigned char)c)) {
        bool is_int = false;
        if (!parse_number(p, &is_int)) {
            return false;
        }
        *out_kind = V_NUMBER;
        *out_is_integer = is_int;
        return true;
    }
    return fail_at(p, "expected a JSON value");
}

/* ------------------------------------------------------------------ */
/* Esquema                                                             */
/* ------------------------------------------------------------------ */

void schema_init(Schema *schema) {
    memset(schema, 0, sizeof(*schema));
    schema->type[0] = '\0';
}

static const SchemaField *find_field(const Schema *schema, const char *name) {
    for (int i = 0; i < schema->field_count; i++) {
        if (strcmp(schema->fields[i].name, name) == 0) {
            return &schema->fields[i];
        }
    }
    return NULL;
}

/* Parsea {"type": "..."} dentro de una propiedad; ignora lo demás. */
static bool parse_property_body(Parser *p, SchemaField *field) {
    bool seen_type = false;
    if (!expect_char(p, '{')) {
        return false;
    }
    skip_ws(p);
    if (*p->cur == '}') {
        p->cur++;
        return fail_at(p, "property without type");
    }
    while (1) {
        char key[MAX_FIELD_NAME];
        skip_ws(p);
        if (!parse_string(p, key, sizeof(key))) {
            return false;
        }
        if (!expect_char(p, ':')) {
            return false;
        }
        if (strcmp(key, "type") == 0) {
            if (!parse_string(p, field->type, sizeof(field->type))) {
                return false;
            }
            seen_type = true;
        } else {
            ValueKind kind = V_NULL;
            bool is_int = false;
            if (!parse_value(p, 0, &kind, &is_int)) {
                return false;
            }
        }
        skip_ws(p);
        if (*p->cur == ',') {
            p->cur++;
            continue;
        }
        if (*p->cur == '}') {
            p->cur++;
            break;
        }
        return fail_at(p, "expected ',' or '}' in property");
    }
    if (!seen_type) {
        return fail_at(p, "property without type");
    }
    return true;
}

static bool parse_schema_inner(Parser *p, Schema *schema) {
    bool seen_type = false;
    skip_ws(p);
    if (!expect_char(p, '{')) {
        return false;
    }
    skip_ws(p);
    if (*p->cur == '}') {
        p->cur++;
        return fail_at(p, "schema without type");
    }
    while (1) {
        char key[MAX_FIELD_NAME];
        skip_ws(p);
        if (!parse_string(p, key, sizeof(key))) {
            return false;
        }
        if (!expect_char(p, ':')) {
            return false;
        }
        if (strcmp(key, "type") == 0) {
            if (!parse_string(p, schema->type, sizeof(schema->type))) {
                return false;
            }
            seen_type = true;
        } else if (strcmp(key, "properties") == 0) {
            if (!expect_char(p, '{')) {
                return false;
            }
            skip_ws(p);
            if (*p->cur != '}') {
                while (1) {
                    char name[MAX_FIELD_NAME];
                    skip_ws(p);
                    if (!parse_string(p, name, sizeof(name))) {
                        return false;
                    }
                    if (!expect_char(p, ':')) {
                        return false;
                    }
                    if (find_field(schema, name) != NULL) {
                        return fail_at(p, "duplicate property in schema");
                    }
                    if (schema->field_count >= MAX_FIELDS) {
                        return fail_at(p, "schema has too many properties");
                    }
                    SchemaField *field = &schema->fields[schema->field_count];
                    memset(field, 0, sizeof(*field));
                    size_t nlen = strlen(name);
                    memcpy(field->name, name, nlen + 1);
                    field->required = false;
                    if (!parse_property_body(p, field)) {
                        return false;
                    }
                    schema->field_count++;
                    skip_ws(p);
                    if (*p->cur == ',') {
                        p->cur++;
                        continue;
                    }
                    if (*p->cur == '}') {
                        p->cur++;
                        break;
                    }
                    return fail_at(p, "expected ',' or '}' in properties");
                }
            } else {
                p->cur++;
            }
        } else if (strcmp(key, "required") == 0) {
            if (!expect_char(p, '[')) {
                return false;
            }
            skip_ws(p);
            if (*p->cur != ']') {
                while (1) {
                    char name[MAX_FIELD_NAME];
                    skip_ws(p);
                    if (!parse_string(p, name, sizeof(name))) {
                        return false;
                    }
                    SchemaField *field = NULL;
                    for (int i = 0; i < schema->field_count; i++) {
                        if (strcmp(schema->fields[i].name, name) == 0) {
                            field = &schema->fields[i];
                            break;
                        }
                    }
                    if (field == NULL) {
                        return fail_at(p, "required field not in properties");
                    }
                    field->required = true;
                    skip_ws(p);
                    if (*p->cur == ',') {
                        p->cur++;
                        continue;
                    }
                    if (*p->cur == ']') {
                        p->cur++;
                        break;
                    }
                    return fail_at(p, "expected ',' or ']' in required");
                }
            } else {
                p->cur++;
            }
        } else {
            ValueKind kind = V_NULL;
            bool is_int = false;
            if (!parse_value(p, 0, &kind, &is_int)) {
                return false;
            }
        }
        skip_ws(p);
        if (*p->cur == ',') {
            p->cur++;
            continue;
        }
        if (*p->cur == '}') {
            p->cur++;
            break;
        }
        return fail_at(p, "expected ',' or '}' in schema");
    }
    if (!seen_type) {
        return fail_at(p, "schema without type");
    }
    skip_ws(p);
    if (*p->cur != '\0') {
        return fail_at(p, "trailing data after schema");
    }
    return true;
}

bool parse_schema_json_error(const char *json, Schema *schema, char *error,
                             size_t error_size) {
    Parser p;
    if (json == NULL || schema == NULL) {
        return false;
    }
    schema_init(schema);
    p.cur = json;
    p.begin = json;
    p.error = error;
    p.error_size = error_size;
    if (error != NULL && error_size > 0) {
        error[0] = '\0';
    }
    return parse_schema_inner(&p, schema);
}

bool parse_schema_json(const char *json, Schema *schema) {
    return parse_schema_json_error(json, schema, NULL, 0);
}

/* ------------------------------------------------------------------ */
/* Validación                                                          */
/* ------------------------------------------------------------------ */

static const char *kind_name(ValueKind kind) {
    switch (kind) {
        case V_NULL: return "null";
        case V_BOOL: return "boolean";
        case V_NUMBER: return "number";
        case V_STRING: return "string";
        case V_ARRAY: return "array";
        case V_OBJECT: return "object";
    }
    return "unknown";
}

static bool type_matches(const char *expected, ValueKind kind,
                         bool is_integer) {
    if (strcmp(expected, "string") == 0) return kind == V_STRING;
    if (strcmp(expected, "integer") == 0) {
        return kind == V_NUMBER && is_integer;
    }
    if (strcmp(expected, "number") == 0) return kind == V_NUMBER;
    if (strcmp(expected, "boolean") == 0) return kind == V_BOOL;
    if (strcmp(expected, "array") == 0) return kind == V_ARRAY;
    if (strcmp(expected, "object") == 0) return kind == V_OBJECT;
    if (strcmp(expected, "null") == 0) return kind == V_NULL;
    return false;
}

bool validate_json_object(const char *payload, const Schema *schema,
                          char *error, size_t error_size) {
    Parser p;
    bool seen[MAX_FIELDS];
    if (payload == NULL || schema == NULL || error == NULL ||
        error_size == 0) {
        return false;
    }
    error[0] = '\0';
    memset(seen, 0, sizeof(seen));

    if (strcmp(schema->type, "object") != 0) {
        snprintf(error, error_size, "Schema type not supported: %s",
                 schema->type);
        return false;
    }

    p.cur = payload;
    p.begin = payload;
    p.error = error;
    p.error_size = error_size;
    skip_ws(&p);
    if (*p.cur != '{') {
        snprintf(error, error_size,
                 "payload must be a JSON object (byte %zu)",
                 (size_t)(p.cur - p.begin));
        return false;
    }
    p.cur++;
    skip_ws(&p);
    if (*p.cur == '}') {
        p.cur++;
    } else {
        while (1) {
            char name[MAX_FIELD_NAME];
            skip_ws(&p);
            if (!parse_string(&p, name, sizeof(name))) {
                return false;
            }
            if (!expect_char(&p, ':')) {
                return false;
            }
            /* Busca el campo en el esquema (o NULL si es extra). */
            int idx = -1;
            for (int i = 0; i < schema->field_count; i++) {
                if (strcmp(schema->fields[i].name, name) == 0) {
                    idx = i;
                    break;
                }
            }
            if (idx >= 0 && seen[idx]) {
                snprintf(error, error_size,
                         "duplicate field: %s (/%s)", name, name);
                return false;
            }
            ValueKind kind = V_NULL;
            bool is_int = false;
            if (!parse_value(&p, 0, &kind, &is_int)) {
                return false;
            }
            if (idx >= 0) {
                seen[idx] = true;
                const SchemaField *field = &schema->fields[idx];
                if (!type_matches(field->type, kind, is_int)) {
                    if (kind == V_NUMBER && !is_int &&
                        strcmp(field->type, "integer") == 0) {
                        snprintf(error, error_size,
                                 "Field '%s' expected integer but found "
                                 "float (/%s)",
                                 field->name, field->name);
                    } else {
                        snprintf(error, error_size,
                                 "Field '%s' expected %s but found %s "
                                 "(/%s)",
                                 field->name, field->type, kind_name(kind),
                                 field->name);
                    }
                    return false;
                }
            }
            skip_ws(&p);
            if (*p.cur == ',') {
                p.cur++;
                continue;
            }
            if (*p.cur == '}') {
                p.cur++;
                break;
            }
            snprintf(error, error_size,
                     "expected ',' or '}' in payload (byte %zu)",
                     (size_t)(p.cur - p.begin));
            return false;
        }
    }
    skip_ws(&p);
    if (*p.cur != '\0') {
        snprintf(error, error_size,
                 "trailing data after JSON object (byte %zu)",
                 (size_t)(p.cur - p.begin));
        return false;
    }
    for (int i = 0; i < schema->field_count; i++) {
        if (schema->fields[i].required && !seen[i]) {
            snprintf(error, error_size, "Missing required field: %s (/%s)",
                     schema->fields[i].name, schema->fields[i].name);
            return false;
        }
    }
    snprintf(error, error_size, "OK");
    return true;
}
