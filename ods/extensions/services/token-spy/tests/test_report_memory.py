"""SQLite report memory should follow aggregate cardinality, not request count."""
import importlib.util
import sys
import tracemalloc
from uuid import uuid4

from fastapi.testclient import TestClient

from test_usage_report import TOKEN_SPY_DIR, load_sqlite_db


def test_http_report_keeps_large_history_out_of_python_memory(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)
    connection = db._get_conn()
    count = 30_000
    connection.executemany(
        """INSERT INTO usage(timestamp,agent,model,provider_name,cost_source,
            input_tokens,output_tokens,estimated_cost_usd)
            VALUES(?,?,?,?,?,?,?,?)""",
        (("2026-05-01T12:00:00Z", "worker", "fixture-model", "local",
          "local_zero_cost", 100, 25, 0) for _ in range(count)),
    )
    connection.commit()
    monkeypatch.syspath_prepend(str(TOKEN_SPY_DIR))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "report-memory-fixture")
    spec = importlib.util.spec_from_file_location(f"report_api_{uuid4().hex}", TOKEN_SPY_DIR / "main.py")
    api = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, api)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "query_report", db.query_report)
    api.app.dependency_overrides[api.verify_api_key] = lambda: "report-memory-fixture"
    client = TestClient(api.app)
    # Warm the HTTP stack without reading the populated date range.
    empty = client.get('/api/report', params={'start':'2026-04-01','end':'2026-04-01'})
    assert empty.status_code == 200 and empty.json()['summary']['requests'] == 0
    tracemalloc.start()
    try:
        response = client.get('/api/report', params={'start':'2026-05-01','end':'2026-05-02'})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        client.close()
    assert response.status_code == 200
    report = response.json()
    assert report['summary']['requests'] == count
    assert report['summary']['input_tokens'] == count * 100
    assert report['summary']['output_tokens'] == count * 25
    assert report['daily'][0]['requests'] == count
    assert report['daily'][1]['requests'] == 0
    assert len(report['models']) == len(report['services']) == len(report['sources']) == 1
    print(f'HTTP report traced peak: {peak:,} bytes for {count:,} rows')
    assert peak < 2 * 1024 * 1024
