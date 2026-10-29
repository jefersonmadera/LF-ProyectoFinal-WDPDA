#include "schema.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_file(const char *path) {
    FILE *file = fopen(path, "rb");
    if (!file) {
        return NULL;
    }

    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    if (size < 0) {
        fclose(file);
        return NULL;
    }
    fseek(file, 0, SEEK_SET);

    char *buffer = (char *)malloc((size_t)size + 1);
    if (!buffer) {
        fclose(file);
        return NULL;
    }

    size_t read_size = fread(buffer, 1, (size_t)size, file);
    buffer[read_size] = '\0';
    fclose(file);
    return buffer;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Uso: %s <payload.json>\n", argv[0]);
        return 1;
    }

    char *schema_text = read_file("avance_mvp/example_schema.json");
    if (!schema_text) {
        fprintf(stderr, "No se pudo leer el esquema de ejemplo.\n");
        return 1;
    }

    Schema schema;
    if (!parse_schema_json(schema_text, &schema)) {
        fprintf(stderr, "Error al parsear el esquema JSON.\n");
        free(schema_text);
        return 1;
    }

    char *payload_text = read_file(argv[1]);
    if (!payload_text) {
        fprintf(stderr, "No se pudo leer el payload.\n");
        free(schema_text);
        return 1;
    }

    char error[256];
    bool ok = validate_json_object(payload_text, &schema, error, sizeof(error));
    if (ok) {
        printf("VALIDO: %s\n", error);
    } else {
        printf("INVALIDO: %s\n", error);
    }

    free(payload_text);
    free(schema_text);
    return ok ? 0 : 2;
}
