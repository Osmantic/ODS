"""Rate observations must survive competing consumers without inventing telemetry."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import helpers


@pytest.fixture
def sampler(monkeypatch, tmp_path):
    clock = [100.0]
    monkeypatch.setattr(helpers, "LLM_BACKEND", "llama")
    monkeypatch.setattr(helpers, "SERVICES", {"llama-server": {"host": "runtime", "port": 8080}})
    monkeypatch.setattr(helpers, "_TOKEN_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(helpers, "_prev_tokens", {})
    monkeypatch.setattr(helpers, "_llama_metrics_sample", {})
    monkeypatch.setattr(helpers, "_llama_metrics_lock", None)
    monkeypatch.setattr(helpers, "_metrics_clock", lambda: clock[0])
    monkeypatch.setattr(helpers, "_metrics_wall_clock", lambda: clock[0])
    client = AsyncMock()
    monkeypatch.setattr(helpers, "_get_httpx_client", AsyncMock(return_value=client))
    return client, clock


def sample(tokens, seconds, timestamp=""):
    response = MagicMock()
    response.text = (f'llamacpp:tokens_predicted_total {tokens}{timestamp}\n'
                     f'llamacpp:tokens_predicted_seconds_total {seconds}{timestamp}\n')
    return response


@pytest.mark.asyncio
async def test_three_consumers_share_each_sample_then_observe_idle(sampler):
    client, clock = sampler
    responses = iter([sample(100, 5), sample(140, 7), sample(140, 7)])

    async def fetch(*_args, **_kwargs):
        await asyncio.sleep(0)  # force callers to overlap during acquisition
        return next(responses)

    client.get.side_effect = fetch
    for expected in [None, 20.0, 20.0]:
        results = await asyncio.gather(*(helpers.get_llama_metrics("model-a") for _ in range(3)))
        assert [r["tokens_per_second"] for r in results] == [expected] * 3
        assert all(r["throughput_mode"] == "generation_interval" for r in results)
        results[0]["tokens_per_second"] = -1
        assert results[1]["tokens_per_second"] == expected
        clock[0] += 2
    assert client.get.await_count == 3
    assert helpers._get_lifetime_tokens() == 140


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "html", "nan", "negative", "infinity"])
async def test_failed_sample_preserves_count_and_recovery_starts_new_interval(sampler, failure):
    client, clock = sampler
    client.get.return_value = sample(100, 5)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] is None
    clock[0] += 2
    client.get.return_value = sample(140, 7)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] == 20
    clock[0] += 2
    if failure == "http":
        client.get.side_effect = httpx.HTTPStatusError("503", request=httpx.Request("GET", "http://runtime/metrics"), response=httpx.Response(503))
    elif failure == "html":
        client.get.return_value = MagicMock(text="<html>starting</html>")
    else:
        client.get.return_value = sample({"nan": "NaN", "negative": "-1", "infinity": "+Inf"}[failure], 8)
    failed = await helpers.get_llama_metrics("model-a")
    assert failed["tokens_per_second"] == 20
    assert failed["throughput_state"] == "unavailable"
    assert failed["throughput_sampled_at"] == 102.0
    assert failed["lifetime_tokens"] == 140
    assert failed["token_count_mode"] == "cumulative"
    assert json.loads(helpers._TOKEN_FILE.read_text())["last_server_counter"] == 140
    client.get.side_effect = None
    clock[0] += 2
    client.get.return_value = sample(200, 10)
    recovered = await helpers.get_llama_metrics("model-a")
    assert recovered["tokens_per_second"] == 20
    assert recovered["throughput_state"] == "retained"
    assert recovered["throughput_sampled_at"] == 102.0
    assert recovered["lifetime_tokens"] == 200
    clock[0] += 2
    client.get.return_value = sample(220, 11)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] == 20


@pytest.mark.asyncio
async def test_model_changes_do_not_reuse_rate_or_cross_counter_intervals(sampler):
    client, clock = sampler
    for model, tokens, seconds, expected in [
        ("model-a", 100, 5, None), ("model-a", 140, 7, 20),
        ("model-b", 1000, 50, None), ("model-b", 1030, 51, 30),
        ("model-a", 160, 8, None), ("model-a", 180, 9, 20),
    ]:
        client.get.return_value = sample(tokens, seconds)
        assert (await helpers.get_llama_metrics(model))["tokens_per_second"] == expected
        assert client.get.call_args.kwargs["params"] == {"model": model}
        clock[0] += 2
    assert helpers._get_lifetime_tokens() == 1210  # A180 + B1030, no cross-model double counting


@pytest.mark.asyncio
async def test_counter_reset_is_unknown_then_new_interval_can_be_measured(sampler):
    client, clock = sampler
    for tokens, seconds, expected in [(100, 5, None), (140, 7, 20), (2, 0.1, None), (12, 0.6, 20)]:
        client.get.return_value = sample(tokens, seconds)
        assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] == expected
        clock[0] += 2


@pytest.mark.asyncio
async def test_missing_generation_time_preserves_counter_but_not_rate(sampler):
    client, clock = sampler
    client.get.return_value = MagicMock(text="llamacpp:tokens_predicted_total 100\n")
    result = await helpers.get_llama_metrics("model-a")
    assert result["tokens_per_second"] is None
    assert result["lifetime_tokens"] == 100
    clock[0] += 2
    client.get.return_value = sample(140, 7)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] is None


@pytest.mark.asyncio
async def test_prometheus_optional_timestamp_is_not_a_counter(sampler):
    client, clock = sampler
    client.get.return_value = sample(100, 5, " 1750000000000")
    assert (await helpers.get_llama_metrics("model-a"))["lifetime_tokens"] == 100
    clock[0] += 2
    client.get.return_value = sample(140, 7, " 1750000002000")
    result = await helpers.get_llama_metrics("model-a")
    assert result["tokens_per_second"] == 20
    assert result["lifetime_tokens"] == 140


@pytest.mark.asyncio
async def test_no_measurement_has_no_fabricated_lifetime_count(sampler):
    client, _clock = sampler
    client.get.side_effect = httpx.ConnectError("offline")
    result = await helpers.get_llama_metrics("model-a")
    assert result["tokens_per_second"] is None
    assert result["lifetime_tokens"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [None, "bad", -1, float("nan"), float("inf"), 0])
async def test_lemonade_rate_is_nullable_but_explicit_zero_is_preserved(sampler, monkeypatch, rate):
    monkeypatch.setattr(helpers, "LLM_BACKEND", "lemonade")
    monkeypatch.setattr(helpers, "read_live_env_value", lambda _key: "host")
    monkeypatch.setattr(helpers, "request_agent_json", AsyncMock(return_value={"stats": {"tokens_per_second": rate, "output_tokens": 0}}))
    result = await helpers.get_llama_metrics("model-a")
    assert result["tokens_per_second"] is None  # no valid positive measurement yet
    assert result["lifetime_tokens"] == 0
    assert result["token_count_mode"] == "latest_completion"
    assert result["throughput_mode"] == "latest_completion"


@pytest.mark.asyncio
async def test_active_to_idle_hold_then_next_run_updates_value_and_measurement_time(sampler):
    client, clock = sampler
    for tokens, seconds, running, expected, state, measured_at in [
        (100, 5, 1, None, "unavailable", None),
        (140, 7, 0, 20, "measured", 102.0),
        (140, 7, 0, 20, "retained", 102.0),
        (140, 7, 1, 20, "retained", 102.0),
        (200, 9, 0, 30, "measured", 108.0),
    ]:
        response = sample(tokens, seconds)
        response.text += f"llamacpp:requests_processing {running}\n"
        response.json.return_value = [{"id": 0, "id_task": 1, "is_processing": bool(running),
                                       "next_token": [{"n_decoded": 0}]}]
        client.get.return_value = response
        result = await helpers.get_llama_metrics("model-a")
        assert result["tokens_per_second"] == expected
        assert result["throughput_state"] == state
        assert result["throughput_sampled_at"] == measured_at
        assert result["inference_active"] is bool(running)
        clock[0] += 2


@pytest.mark.asyncio
async def test_reset_after_unavailable_gap_discards_old_runtime_measurement(sampler):
    client, clock = sampler
    for response in [sample(100, 5), sample(140, 7)]:
        client.get.return_value = response
        await helpers.get_llama_metrics("model-a")
        clock[0] += 2
    client.get.side_effect = OSError("offline")
    failed = await helpers.get_llama_metrics("model-a")
    assert failed["tokens_per_second"] == 20
    assert failed["throughput_state"] == "unavailable"
    client.get.side_effect = None
    clock[0] += 2
    client.get.return_value = sample(2, 0.1)
    reset = await helpers.get_llama_metrics("model-a")
    assert reset["tokens_per_second"] is None
    assert reset["throughput_sampled_at"] is None


@pytest.mark.asyncio
async def test_lemonade_identical_latest_completion_is_held_without_new_timestamp(sampler, monkeypatch):
    _client, clock = sampler
    monkeypatch.setattr(helpers, "LLM_BACKEND", "lemonade")
    monkeypatch.setattr(helpers, "read_live_env_value", lambda _key: "host")
    request = AsyncMock(return_value={"stats": {"tokens_per_second": 50, "output_tokens": 100}})
    monkeypatch.setattr(helpers, "request_agent_json", request)
    first = await helpers.get_llama_metrics("model-a")
    clock[0] += 2
    held = await helpers.get_llama_metrics("model-a")
    assert held["tokens_per_second"] == 50
    assert held["throughput_sampled_at"] == first["throughput_sampled_at"]
    assert held["throughput_state"] == "retained"
    clock[0] += 2
    request.return_value = {"stats": {"tokens_per_second": 50, "output_tokens": 200}}
    next_run = await helpers.get_llama_metrics("model-a")
    assert next_run["tokens_per_second"] == 50
    assert next_run["throughput_sampled_at"] == 104.0
    assert next_run["throughput_state"] == "measured"


def test_legacy_lifetime_counter_migrates_without_recounting(sampler):
    helpers._TOKEN_FILE.write_text(json.dumps({"lifetime": 500, "last_server_counter": 100}))
    assert helpers._update_lifetime_tokens(120, counter_id="model-a") == 520
    assert helpers._update_lifetime_tokens(30, counter_id="model-b") == 550
    assert helpers._update_lifetime_tokens(140, counter_id="model-a") == 570


@pytest.mark.asyncio
async def test_unknown_model_discovery_holds_only_same_endpoint_sample_as_unavailable(sampler, monkeypatch):
    client, clock = sampler
    for response in [sample(100, 5), sample(140, 7)]:
        client.get.return_value = response
        await helpers.get_llama_metrics("model-a")
        clock[0] += 2
    monkeypatch.setattr(helpers, "get_loaded_model", AsyncMock(return_value=None))
    held = await helpers.get_llama_metrics()
    assert held["tokens_per_second"] == 20
    assert held["throughput_state"] == "unavailable"
    assert held["throughput_sampled_at"] == 102
    assert held["throughput_model"] == "model-a"
    assert client.get.await_count == 2  # never query an unidentified model counter
    monkeypatch.setattr(helpers, "SERVICES", {"llama-server": {"host": "other-runtime", "port": 8080}})
    other = await helpers.get_llama_metrics()
    assert other["tokens_per_second"] is None
    assert other["throughput_model"] is None
    assert other["throughput_sampled_at"] is None


@pytest.mark.asyncio
async def test_lemonade_unhinted_and_status_pollers_share_authoritative_model(sampler, monkeypatch):
    _client, clock = sampler
    monkeypatch.setattr(helpers, "LLM_BACKEND", "lemonade")
    monkeypatch.setattr(helpers, "read_live_env_value", lambda _key: "host")
    request = AsyncMock(return_value={"health": {"status": "ok", "model_loaded": "model-a"},
                                     "stats": {"tokens_per_second": 50, "output_tokens": 100}})
    monkeypatch.setattr(helpers, "request_agent_json", request)
    first = await helpers.get_llama_metrics("model-a")
    unhinted = await helpers.get_llama_metrics()
    assert unhinted == first
    assert unhinted["throughput_model"] == "model-a"
    # One stats call and one discovery call; no second metrics acquisition.
    assert request.await_count == 2
    clock[0] += 2
    request.return_value = {"health": {"status": "ok", "model_loaded": "model-b"},
                            "stats": {"tokens_per_second": 0, "output_tokens": 0}}
    changed = await helpers.get_llama_metrics()
    assert changed["throughput_model"] == "model-b"
    assert changed["tokens_per_second"] is None
    assert changed["throughput_sampled_at"] is None


@pytest.mark.asyncio
async def test_missing_timing_after_measurement_retains_rate_as_unavailable(sampler):
    client, clock = sampler
    for response in [sample(100, 5), sample(140, 7)]:
        client.get.return_value = response
        await helpers.get_llama_metrics("model-a")
        clock[0] += 2
    client.get.return_value = MagicMock(text="llamacpp:tokens_predicted_total 180\n")
    result = await helpers.get_llama_metrics("model-a")
    assert result["tokens_per_second"] == 20
    assert result["throughput_state"] == "unavailable"
    assert result["throughput_sampled_at"] == 102.0
    assert result["lifetime_tokens"] == 180


@pytest.mark.parametrize("state,owner,expected", [("retained", "model-a", 0), ("unavailable", "model-a", 0), ("measured", "model-b", 0), ("measured", "model-a", 20)])
def test_catalogue_does_not_treat_held_or_other_model_rate_as_new_measurement(state, owner, expected):
    from routers.models import _newly_measured_tps
    assert _newly_measured_tps({"tokens_per_second": 20, "throughput_state": state, "throughput_model": owner}, "model-a") == expected


@pytest.mark.asyncio
async def test_cancelled_sampler_owner_clears_gap_and_releases_lock(sampler):
    client, clock = sampler
    client.get.return_value = sample(100, 5)
    await helpers.get_llama_metrics("model-a")
    clock[0] += 2
    client.get.return_value = sample(140, 7)
    await helpers.get_llama_metrics("model-a")
    clock[0] += 2
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    client.get.side_effect = blocked
    owner = asyncio.create_task(helpers.get_llama_metrics("model-a"))
    await entered.wait()
    waiter = asyncio.create_task(helpers.get_llama_metrics("model-a"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert helpers._prev_tokens["count"] == 140  # waiting consumer has no ownership
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert helpers._prev_tokens == {}
    assert not helpers._llama_metrics_lock.locked()
    assert helpers.get_cached_llama_metrics()["tokens_per_second"] == 20
    assert helpers.get_cached_llama_metrics()["throughput_state"] == "unavailable"
    client.get.side_effect = None
    client.get.return_value = sample(300, 8)
    recovered = await helpers.get_llama_metrics("model-a")
    assert recovered["tokens_per_second"] == 20  # hold, not a rate spanning the gap
    assert recovered["throughput_state"] == "retained"
    assert recovered["throughput_sampled_at"] == 102


def live_runtime(client, *, count=100, task=11, active=True, total=100, seconds=5,
                 shape="array", slots_failure=None, additional_slots=None):
    """Actual b9014 slots shape; completion counters need not move mid-run."""
    metrics = sample(total, seconds)
    metrics.text += f"llamacpp:requests_processing {int(active)}\nllamacpp:n_decode_total 9000000\n"
    next_token = {"n_decoded": count}
    slot = {"id": 0, "id_task": task, "is_processing": active,
            "next_token": [next_token] if shape == "array" else next_token,
            "params": {"never": "retain this"}, "prompt": "private", "generated": "private"}
    rows = [slot] + (additional_slots or [])
    response = MagicMock()
    response.json.return_value = rows
    async def fetch(url, **kwargs):
        if url.endswith("/metrics"):
            return metrics
        assert url.endswith("/slots")
        assert kwargs["timeout"] == 2.0
        if slots_failure:
            raise slots_failure
        return response
    client.get.side_effect = fetch
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["array", "object"])
async def test_live_output_updates_while_completed_counters_stay_fixed(sampler, shape):
    client, clock = sampler
    live_runtime(client, count=201, shape=shape)
    first = await helpers.get_llama_metrics("model-a")
    assert first["tokens_per_second"] is None
    assert first["inference_active"] is True
    clock[0] += 2
    live_runtime(client, count=342, shape=shape)
    results = await asyncio.gather(*(helpers.get_llama_metrics("model-a") for _ in range(3)))
    assert all(r["tokens_per_second"] == 70.5 for r in results)
    assert all(r["throughput_mode"] == "live_output_interval" for r in results)
    assert all(r["throughput_state"] == "measured" for r in results)
    assert client.get.await_count == 4  # two endpoints per observation, not per consumer
    assert results[0]["lifetime_tokens"] == 100  # live counters never double-count completed usage
    assert helpers._prev_tokens["live_slots"]["counts"] == {(0, 11): 342}
    assert "private" not in repr(helpers._prev_tokens)
    clock[0] += 2
    live_runtime(client, active=False)
    idle = await helpers.get_llama_metrics("model-a")
    assert idle["tokens_per_second"] == 70.5
    assert idle["throughput_sampled_at"] == 102
    assert idle["throughput_state"] == "retained"
    assert idle["throughput_mode"] == "live_output_interval"
    assert idle["inference_active"] is False


@pytest.mark.asyncio
async def test_live_output_count_is_reported_only_for_one_measured_generation(sampler):
    client, clock = sampler
    live_runtime(client, count=201)
    assert (await helpers.get_llama_metrics("model-a"))["live_output_tokens"] == 201
    clock[0] += 2
    live_runtime(client, count=342)
    assert (await helpers.get_llama_metrics("model-a"))["live_output_tokens"] == 342
    clock[0] += 2
    # Two concurrent generations have no single owner-visible count.
    other = {"id": 1, "id_task": 12, "is_processing": True, "next_token": [{"n_decoded": 5}]}
    live_runtime(client, count=400, additional_slots=[other])
    assert (await helpers.get_llama_metrics("model-a"))["live_output_tokens"] is None
    clock[0] += 2
    live_runtime(client, active=False)
    assert (await helpers.get_llama_metrics("model-a"))["live_output_tokens"] is None
    clock[0] += 2
    live_runtime(client, count=500, slots_failure=httpx.ReadTimeout("unavailable"))
    assert (await helpers.get_llama_metrics("model-a"))["live_output_tokens"] is None
    clock[0] += 2
    rows = live_runtime(client, count=600)
    rows[0]["next_token"] = []
    assert (await helpers.get_llama_metrics("model-a"))["live_output_tokens"] is None
    assert "private" not in repr(helpers._prev_tokens)


@pytest.mark.asyncio
async def test_live_task_change_and_counter_reset_start_new_intervals(sampler):
    client, clock = sampler
    live_runtime(client, count=100)
    await helpers.get_llama_metrics("model-a")
    clock[0] += 2
    live_runtime(client, count=200)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] == 50
    for task, count in [(22, 1000), (22, 0)]:
        clock[0] += 2
        live_runtime(client, count=count, task=task)
        held = await helpers.get_llama_metrics("model-a")
        assert held["tokens_per_second"] == 50
        assert held["throughput_sampled_at"] == 102
    clock[0] += 2
    live_runtime(client, count=160, task=22)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] == 80
    clock[0] += 2
    live_runtime(client, count=300, task=22)
    assert (await helpers.get_llama_metrics("model-b"))["tokens_per_second"] is None
    clock[0] += 2
    live_runtime(client, count=400, task=22, total=0, seconds=0)
    assert (await helpers.get_llama_metrics("model-b"))["tokens_per_second"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["slots", "metrics", "bad_shape"])
async def test_slot_failure_holds_unavailable_and_recovery_does_not_cross_gap(sampler, failure):
    client, clock = sampler
    live_runtime(client, count=100)
    await helpers.get_llama_metrics("model-a")
    clock[0] += 2
    live_runtime(client, count=200)
    await helpers.get_llama_metrics("model-a")
    clock[0] += 2
    if failure == "slots":
        live_runtime(client, slots_failure=httpx.ReadTimeout("unavailable"))
    elif failure == "metrics":
        client.get.side_effect = httpx.ReadTimeout("unavailable")
    else:
        rows = live_runtime(client)
        rows[0]["next_token"] = []
    failed = await helpers.get_llama_metrics("model-a")
    assert failed["tokens_per_second"] == 50
    assert failed["throughput_state"] == "unavailable"
    assert failed["throughput_mode"] == "live_output_interval"
    assert "live_slots" not in helpers._prev_tokens
    clock[0] += 2
    live_runtime(client, count=999)
    recovered = await helpers.get_llama_metrics("model-a")
    assert recovered["tokens_per_second"] == 50
    assert recovered["throughput_sampled_at"] == 102
    clock[0] += 2
    live_runtime(client, count=1109)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] == 55


@pytest.mark.asyncio
async def test_completion_interval_fallback_when_slots_unsupported(sampler):
    client, clock = sampler
    failure = httpx.HTTPStatusError("404", request=httpx.Request("GET", "http://runtime/slots"),
                                   response=httpx.Response(404))
    live_runtime(client, slots_failure=failure)
    assert (await helpers.get_llama_metrics("model-a"))["tokens_per_second"] is None
    clock[0] += 2
    live_runtime(client, total=140, seconds=7, slots_failure=failure)
    result = await helpers.get_llama_metrics("model-a")
    assert result["tokens_per_second"] == 20
    assert result["throughput_mode"] == "generation_interval"
    assert result["throughput_state"] == "measured"


@pytest.mark.parametrize("payload", [None, {}, [None], [{}],
    [{"id":0,"id_task":1,"is_processing":True,"next_token":[{"n_decoded":True}]}],
    [{"id":0,"id_task":1,"is_processing":True,"next_token":[{"n_decoded":-1}]}],
    [{"id":0,"id_task":1,"is_processing":True,"next_token":[{"n_decoded":float("nan")}]}],
    [{"id":0,"id_task":1,"is_processing":True,"next_token":[]}],
])
def test_live_slot_shape_cannot_invent_measurements(sampler, payload):
    with pytest.raises(ValueError):
        helpers._observe_live_output_slots(payload, 100)


def test_parallel_slots_sum_accepted_tokens_and_rebaseline_when_set_changes(sampler):
    def rows(a, b):
        return [{"id":i,"id_task":11+i,"is_processing":True,"next_token":[{"n_decoded":n}]}
                for i,n in enumerate((a,b))]
    assert helpers._observe_live_output_slots(rows(10,20),100) is None
    # Speculation accepts multiple tokens per decode: count all accepted outputs,
    # not one decode invocation. Two active slots contribute separate outputs.
    assert helpers._observe_live_output_slots(rows(30,40),102) == 20
    assert helpers._observe_live_output_slots(rows(50,60)[:1],104) is None
    assert helpers._observe_live_output_slots(rows(70,80)[:1],106) == 10
    assert helpers._observe_live_output_slots(rows(80,90)[:1],105) is None
