#include "schema.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void trim_spaces(const char **p) {
    while (**p != '\0' && isspace((unsigned char)**p)) {
        (*p)++;
    }
}

static char *skip_string(char *p) {
    if (*p != '"') return p;
    p++;
    while (*p != '\0') {
        if (*p == '\\') {
            p += 2;
            continue;
        }
        if (*p == '"') {
            return p + 1;
        }
        p++;
    }
    return p;
}

static bool read_string_value(const char *json, const char *key, char *out, size_t out_size) {
    const char *pos = strstr(json, key);
    if (!pos) return false;

    pos = strchr(pos, ':');
    if (!pos) return false;
    pos++;
    trim_spaces(&pos);
    if (*pos != '"') return false;
    pos++;

    size_t i = 0;
    while (*pos != '\0' && *pos != '"' && i + 1 < out_size) {
        if (*pos == '\\') {
            pos++;
            if (*pos == '\0') break;
        }
        out[i++] = *pos++;
    }
    out[i] = '\0';
    return true;
}

static bool is_required_field(const char *required_block, const char *field_name) {
    const char *cursor = strstr(required_block, field_name);
    if (!cursor) return false;

    char before[8] = {0};
    size_t len = strlen(field_name);
    if (cursor > required_block) {
        size_t back = cursor - required_block;
        if (back > 0 && back > sizeof(before)) back = sizeof(before);
        memcpy(before, required_block + (back - (back < sizeof(before) ? back : sizeof(before))), 0);
    }

    return true;
}

static void extract_block_between(const char *json, const char *key, char *buffer, size_t buffer_size) {
    const char *pos = strstr(json, key);
    if (!pos) {
        buffer[0] = '\0';
        return;
    }

    pos = strchr(pos, '{');
    if (!pos) {
        buffer[0] = '\0';
        return;
    }

    int depth = 0;
    const char *cur = pos;
    size_t index = 0;

    while (*cur != '\0' && index + 1 < buffer_size) {
        if (*cur == '{') depth++;
        if (*cur == '}') {
            depth--;
            if (depth == 0) {
                buffer[index++] = *cur;
                break;
            }
        }
        buffer[index++] = *cur;
        cur++;
    }
    buffer[index] = '\0';
}

static bool parse_required_names(const char *json, Schema *schema) {
    const char *pos = strstr(json, "\"required\"");
    if (!pos) return true;

    pos = strchr(pos, '[');
    if (!pos) return true;

    pos++;
    while (*pos != '\0' && *pos != ']') {
        trim_spaces(&pos);
        if (*pos == '"') {
            char name[MAX_FIELD_NAME];
            pos++;
            size_t i = 0;
            while (*pos != '\0' && *pos != '"' && i + 1 < sizeof(name)) {
                name[i++] = *pos++;
            }
            name[i] = '\0';
            if (*pos == '"') pos++;

            for (int i = 0; i < schema->field_count; i++) {
                if (strcmp(schema->fields[i].name, name) == 0) {
                    schema->fields[i].required = true;
                    break;
                }
            }
        }
        while (*pos != '\0' && *pos != ',' && *pos != ']') pos++;
        if (*pos == ',') pos++;
    }
    return true;
}

static void parse_properties_block(const char *properties_json, Schema *schema) {
    const char *cursor = properties_json;
    while (*cursor != '\0') {
        trim_spaces(&cursor);
        if (*cursor != '"') {
            cursor++;
            continue;
        }

        char name[MAX_FIELD_NAME];
        cursor++;
        size_t n = 0;
        while (*cursor != '\0' && *cursor != '"' && n + 1 < sizeof(name)) {
            name[n++] = *cursor++;
        }
        name[n] = '\0';

        if (*cursor == '"') cursor++;
        trim_spaces(&cursor);
        if (*cursor == ':') cursor++;
        trim_spaces(&cursor);
        if (*cursor != '{') {
            continue;
        }

        char subobj[256] = {0};
        char *subpos = subobj;
        int depth = 0;
        while (*cursor != '\0') {
            if (*cursor == '{') depth++;
            if (*cursor == '}') {
                depth--;
                if (depth == 0) {
                    *subpos++ = *cursor;
                    break;
                }
            }
            *subpos++ = *cursor;
            cursor++;
        }
        *subpos = '\0';

        SchemaField *field = &schema->fields[schema->field_count++];
        strncpy(field->name, name, sizeof(field->name) - 1);
        field->required = false;
        read_string_value(subobj, "\"type\"", field->type, sizeof(field->type));

        trim_spaces(&cursor);
        if (*cursor == ',') cursor++;
    }
}

void schema_init(Schema *schema) {
    memset(schema, 0, sizeof(*schema));
    snprintf(schema->type, sizeof(schema->type), "object");
}

bool parse_schema_json(const char *json, Schema *schema) {
    if (!json || !schema) return false;

    schema_init(schema);

    if (!read_string_value(json, "\"type\"", schema->type, sizeof(schema->type))) {
        return false;
    }

    char properties_block[1024] = {0};
    extract_block_between(json, "\"properties\"", properties_block, sizeof(properties_block));
    parse_properties_block(properties_block, schema);

    parse_required_names(json, schema);
    return true;
}

static bool matches_type(const char *actual_type, const char *expected_type) {
    return strcmp(actual_type, expected_type) == 0;
}

static bool extract_json_string(const char *json, const char *key, char *value, size_t value_size) {
    const char *pos = strstr(json, key);
    if (!pos) return false;
    pos = strchr(pos, ':');
    if (!pos) return false;
    pos++;
    trim_spaces(&pos);
    if (*pos != '"') return false;
    pos++;
    size_t i = 0;
    while (*pos != '\0' && *pos != '"' && i + 1 < value_size) {
        value[i++] = *pos++;
    }
    value[i] = '\0';
    return true;
}

static bool extract_json_number(const char *json, const char *key, double *value_out) {
    const char *pos = strstr(json, key);
    if (!pos) return false;
    pos = strchr(pos, ':');
    if (!pos) return false;
    pos++;
    trim_spaces(&pos);
    *value_out = strtod(pos, NULL);
    return true;
}

static bool contains_key(const char *json, const char *key) {
    const char *pos = strstr(json, key);
    if (!pos) return false;
    char before = pos > json ? *(pos - 1) : ' ';
    if (before == '"' || before == '{' || before == ',') return true;
    return false;
}

bool validate_json_object(const char *payload, const Schema *schema, char *error, size_t error_size) {
    if (!payload || !schema || !error || error_size == 0) return false;
    error[0] = '\0';

    if (strcmp(schema->type, "object") != 0) {
        snprintf(error, error_size, "Schema type not supported: %s", schema->type);
        return false;
    }

    for (int i = 0; i < schema->field_count; i++) {
        const SchemaField *field = &schema->fields[i];
        char key[64];
        snprintf(key, sizeof(key), "\"%s\"", field->name);

        if (field->required && !contains_key(payload, key)) {
            snprintf(error, error_size, "Missing required field: %s", field->name);
            return false;
        }

        if (contains_key(payload, key)) {
            char buffer[128];
            char actual_value[128];
            snprintf(buffer, sizeof(buffer), "\"%s\"", field->name);

            const char *pos = strstr(payload, buffer);
            if (!pos) continue;
            pos = strchr(pos, ':');
            if (!pos) continue;
            pos++;
            trim_spaces(&pos);

            if (strcmp(field->type, "string") == 0) {
                if (*pos != '"') {
                    snprintf(error, error_size, "Field '%s' expected string but found non-string value", field->name);
                    return false;
                }
                extract_json_string(payload, buffer, actual_value, sizeof(actual_value));
            }
            else if (strcmp(field->type, "integer") == 0) {
                if (*pos == '"') {
                    snprintf(error, error_size, "Field '%s' expected integer but found string", field->name);
                    return false;
                }
                double number = 0.0;
                extract_json_number(payload, buffer, &number);
                if (number != (int)number) {
                    snprintf(error, error_size, "Field '%s' expected integer but found float", field->name);
                    return false;
                }
            }
            else if (strcmp(field->type, "number") == 0) {
                if (*pos == '"') {
                    snprintf(error, error_size, "Field '%s' expected number but found string", field->name);
                    return false;
                }
            }
            else if (strcmp(field->type, "boolean") == 0) {
                if (*pos != 't' && *pos != 'f') {
                    snprintf(error, error_size, "Field '%s' expected boolean", field->name);
                    return false;
                }
            }
        }
    }

    snprintf(error, error_size, "OK");
    return true;
}
