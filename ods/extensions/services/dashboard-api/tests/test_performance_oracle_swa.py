from performance_oracle import _estimated_context_kv_gb


def test_swa_reduction_applies():
    base = {"params_b": 7, "context_length": 131072}
    swa = {**base, "attention_sliding_window": 8192}
    assert _estimated_context_kv_gb(swa) < _estimated_context_kv_gb(base)


def test_no_window_matches_no_cap():
    # No window and window >= context both take the full-context path, so they
    # must be identical. This locks the fallback without coupling to the exact
    # KV coefficient.
    base = {"params_b": 7, "context_length": 131072}
    assert _estimated_context_kv_gb(base) == _estimated_context_kv_gb(
        {**base, "attention_sliding_window": 999999}
    )


def test_window_ge_context_is_noop():
    base = {"params_b": 7, "context_length": 131072}
    no_swa = _estimated_context_kv_gb(base)
    assert _estimated_context_kv_gb({**base, "attention_sliding_window": 262144}) == no_swa
    assert _estimated_context_kv_gb({**base, "attention_sliding_window": 131072}) == no_swa


def test_small_window_blends_not_floored():
    # A sub-8192 window at long context must NOT collapse to the 8192 floor;
    # the global-layer blend keeps it above the floor-only estimate.
    swa = {"params_b": 7, "context_length": 131072, "attention_sliding_window": 4096}
    floor = {"params_b": 7, "context_length": 8192}
    assert _estimated_context_kv_gb(swa) > _estimated_context_kv_gb(floor)


def test_blend_never_below_floor():
    # Tiny window + context just above the floor: the blend would dip below
    # 8192, so the floor must clamp it back up (never under-estimate baseline).
    model = {"params_b": 7, "context_length": 9000, "attention_sliding_window": 512}
    floor = {"params_b": 7, "context_length": 8192}
    assert _estimated_context_kv_gb(model) == _estimated_context_kv_gb(floor)


def test_junk_window_ignored():
    base = {"params_b": 7, "context_length": 131072}
    no_swa = _estimated_context_kv_gb(base)
    for junk in ["", None, "abc", "NaN"]:
        assert _estimated_context_kv_gb({**base, "attention_sliding_window": junk}) == no_swa
