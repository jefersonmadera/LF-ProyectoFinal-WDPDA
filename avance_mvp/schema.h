#ifndef SCHEMA_H
#define SCHEMA_H

#include <stdbool.h>
#include <stddef.h>

#define MAX_FIELDS 16
#define MAX_FIELD_NAME 32
#define MAX_FIELD_TYPE 16

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
bool validate_json_object(const char *payload, const Schema *schema, char *error, size_t error_size);

#endif
