#ifndef SCHEMA_H
#define SCHEMA_H

#include <stdbool.h>
#include <stddef.h>

/*
 * Esquema soportado (subconjunto de JSON Schema, Semana 2):
 *   { "type": "object",
 *     "properties": { "nombre": { "type": "string" }, ... },
 *     "required": ["nombre", ...] }
 * Tipos de campo: string, integer, number, boolean, array, object, null.
 * Sin additionalProperties: los campos no listados se permiten y no se
 * validan (eso se documenta, no se esconde). Un campo "required" que no
 * esté en "properties" es un error del esquema, no se ignora en silencio.
 *
 * El validador ya no busca claves con strstr: parsea el payload con un
 * descenso recursivo (LL(1) para este subconjunto, ver docs/fase2-parser.md).
 * Por eso los espacios, saltos de línea y claves dentro de strings ya no
 * lo confunden, y un payload malformado da error de sintaxis en vez de
 * un veredicto de validación inventado.
 */

#define MAX_FIELDS 32
#define MAX_FIELD_NAME 64
#define MAX_FIELD_TYPE 16
#define MAX_JSON_DEPTH 64

typedef struct {
    char name[MAX_FIELD_NAME];
    char type[MAX_FIELD_TYPE];
    bool required;
} SchemaField;

typedef struct {
    char type[MAX_FIELD_TYPE];
    SchemaField fields[MAX_FIELDS];
    int field_count;
} Schema;

void schema_init(Schema *schema);
bool parse_schema_json(const char *json, Schema *schema);
bool parse_schema_json_error(const char *json, Schema *schema, char *error,
                             size_t error_size);
bool validate_json_object(const char *payload, const Schema *schema,
                          char *error, size_t error_size);

#endif
