"""Pricing is derived in ONE place — passiveworkers.net.config.pool_for — after the formula was copy-pasted
across 6 sites (2 diverged, plus a lying hardcoded JS fallback). These pin the single source of truth."""
import passiveworkers.net.config as cfg


def test_pool_for_matches_canonical_formula():
    for jt in cfg.JOB_TYPES:
        for n in (1, 2, 3, 5):
            expected = round(cfg.CONFIG.worker_pool / cfg.CONFIG.fleet_size * n
                             * cfg.JOB_TYPES[jt]["pool_mult"], 4)
            assert cfg.pool_for(jt, n) == expected


def test_pool_for_scales_with_minds_and_mult():
    assert cfg.pool_for("chat", 2) == 2 * cfg.pool_for("chat", 1)          # linear in mind count
    assert cfg.pool_for("research_report", 1) == 3 * cfg.pool_for("chat", 1)   # pool_mult 3.0 vs 1.0


def test_job_types_endpoint_is_single_source(coord_client):
    # /job-types must expose pool_for(k, 1) at full precision (was round(...,1)) so the client never
    # needs a hardcoded fallback — proving a retuned pool would flow through untouched.
    jt = coord_client.get("/job-types").json()
    assert jt, "catalog should be non-empty"
    for k, v in jt.items():
        assert v["price_per_mind"] == cfg.pool_for(k, 1)
        assert v["judge_fee"] == cfg.CONFIG.judge_fee
