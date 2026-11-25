"""Pruebas del almacén NoSQL de casos (DynamoDB simulado con moto).

Verifica que el corpus completo (54 casos, 6 categorías) cabe en
DynamoDB (ítems < 400 KB), se guarda y se lee sin perder datos, se
filtra por categoría y guarda métricas; y que el caso de 1 MB se
regenera desde su receta con los mismos bytes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

from web.backend.seed import build_cases, payload_of  # noqa: E402
from web.backend.store import DynamoStore  # noqa: E402


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="wdpda-casos",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id",
                                   "AttributeType": "S"}],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5,
                                   "WriteCapacityUnits": 5})
        yield DynamoStore("wdpda-casos", "us-east-1")


def test_corpus_round_trip(store):
    cases = build_cases()
    assert len(cases) == 54
    for c in cases:
        store.upsert(dict(c))
    assert store.count() == 54
    cats = {c["categoria"] for c in cases}
    assert cats == {"validos", "invalidos", "malformados", "fuera_dpda",
                    "bombeo", "extremos"}
    for cat in cats:
        assert len(store.list(cat)) == sum(c["categoria"] == cat
                                           for c in cases)
    for c in cases:
        got = store.get(c["_id"])
        assert got["esquema"] == c["esquema"]
        assert got["esperado"] == json.loads(json.dumps(c["esperado"]))
        assert payload_of(got) == payload_of(c)


def test_metrics_and_delete(store):
    c = build_cases()[0]
    store.upsert(dict(c))
    store.update_metrics(c["_id"], {"py": {"status": "VALID", "seconds":
                                           0.0012}, "coincide": True})
    m = store.get(c["_id"])["metricas"]
    assert m["py"]["seconds"] == pytest.approx(0.0012)
    assert store.delete(c["_id"]) and store.get(c["_id"]) is None


def test_large_payload_is_a_recipe():
    big = next(c for c in build_cases() if c["_id"] == "extremo-payload-1MB")
    assert big["payload"] == "" and big["payload_gen"]["kb"] == 1024
    assert len(payload_of(big)) == big["payload_gen"]["bytes"]
