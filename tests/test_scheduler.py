from ironlab.scheduler import plan_admissions

NOW = 10_000.0


def job(id, user, vram, created=None, exclusive=False):
    return {"id": id, "user": user, "vram_gb": vram, "exclusive": int(exclusive),
            "created_at": NOW - 60 if created is None else created}


def plan(queued, running=(), used=0.0, actual=None, max_per_user=1, starvation=1800):
    return plan_admissions(list(queued), list(running), total_gb=24, reserve_gb=1, gpu_used_gb=used,
                           actual_gb=actual or {}, max_per_user=max_per_user,
                           starvation_s=starvation, now=NOW)


def test_admits_in_order_while_vram_fits():
    p = plan([job(1, "a", 10, NOW - 30), job(2, "b", 10, NOW - 20), job(3, "c", 10, NOW - 10)])
    assert p.start == [1, 2]
    assert "VRAM 대기" in p.reasons[3]


def test_backfills_small_job_behind_big_one():
    running = [job(9, "x", 16)]
    p = plan([job(1, "a", 12, NOW - 30), job(2, "b", 4, NOW - 10)], running, used=15, actual={9: 15})
    assert p.start == [2]
    assert "VRAM 대기" in p.reasons[1]


def test_starvation_stops_backfill():
    running = [job(9, "x", 16)]
    p = plan([job(1, "a", 12, NOW - 3600), job(2, "b", 4, NOW - 10)], running, used=15, actual={9: 15})
    assert p.start == []
    assert "기아 방지" in p.reasons[2]


def test_per_user_limit():
    p = plan([job(1, "a", 4, NOW - 30), job(2, "a", 4, NOW - 20), job(3, "b", 4, NOW - 10)])
    assert p.start == [1, 3]
    assert "동시 실행 한도" in p.reasons[2]


def test_exclusive_waits_then_blocks_others():
    running = [job(9, "x", 4)]
    p = plan([job(1, "a", 23, NOW - 30, exclusive=True), job(2, "b", 2, NOW - 10)], running,
             used=3, actual={9: 3})
    assert 1 not in p.start and "독점" in p.reasons[1]
    p2 = plan([job(2, "b", 2)], [job(1, "a", 23, exclusive=True)], used=20, actual={1: 20})
    assert p2.start == [] and "독점 작업 실행 중" in p2.reasons[2]


def test_untracked_usage_reduces_budget():
    # 워크스페이스에서 누군가 노트북으로 10GB를 쓰는 중
    p = plan([job(1, "a", 14)], used=10.0)
    assert p.start == []
    p = plan([job(1, "a", 13)], used=10.0)
    assert p.start == [1]


def test_overuse_counts_actual_not_declared():
    running = [job(9, "x", 4)]  # 4GB 선언했지만 12GB 사용
    p = plan([job(1, "a", 12)], running, used=12, actual={9: 12})  # 선언 기준이면 19GB 여유
    assert p.start == []


def test_job_larger_than_gpu_never_runs():
    p = plan([job(1, "a", 30)])
    assert p.start == [] and "GPU 용량" in p.reasons[1]
