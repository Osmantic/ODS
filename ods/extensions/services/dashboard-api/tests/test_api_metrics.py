"""Tests for the dashboard API Prometheus request metrics."""

from api_metrics import ApiMetrics, api_metrics


def test_registry_renders_counter_gauge_and_cumulative_histogram():
    registry = ApiMetrics()
    registry.request_started()
    registry.request_finished("get", "/api/items/{item_id}", 201, 0.02)

    output = registry.render()

    assert "ods_dashboard_api_http_requests_in_flight 0" in output
    assert (
        'ods_dashboard_api_http_requests_total{method="GET",'
        'route="/api/items/{item_id}",status="201"} 1'
    ) in output
    assert (
        'ods_dashboard_api_http_request_duration_seconds_bucket{method="GET",'
        'route="/api/items/{item_id}",le="0.01"} 0'
    ) in output
    assert (
        'ods_dashboard_api_http_request_duration_seconds_bucket{method="GET",'
        'route="/api/items/{item_id}",le="0.025"} 1'
    ) in output


def test_metrics_endpoint_uses_route_templates_and_excludes_scrapes(test_client):
    api_metrics.reset()

    assert test_client.get("/health").status_code == 200
    assert test_client.get(
        "/api/extensions/example-service/progress",
        headers=test_client.auth_headers,
    ).status_code == 200
    assert test_client.get("/not-a-real-resource/123").status_code == 404
    response = test_client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'route="/health",status="200"} 1' in response.text
    assert 'route="/api/extensions/{service_id}/progress",status="200"} 1' in response.text
    assert 'route="/api/extensions/example-service/progress"' not in response.text
    assert 'route="unmatched",status="404"} 1' in response.text
    assert 'route="/not-a-real-resource/123"' not in response.text
    assert 'route="/metrics"' not in response.text


def test_registry_escapes_prometheus_label_values():
    registry = ApiMetrics()
    registry.request_started()
    registry.request_finished("GET", '/quoted/"value"', 200, 0.001)

    assert 'route="/quoted/\\"value\\""' in registry.render()
