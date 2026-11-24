"""DockerRuntime 의 컨테이너 정리 동작 (Docker 없이, 가짜 컨테이너로 확인)."""

from ironlab.runtime import DockerRuntime


class FakeContainer:
    def __init__(self, status, stop_error=None):
        self.status, self.stop_error, self.calls = status, stop_error, []

    def stop(self, timeout=None):
        self.calls.append("stop")
        if self.stop_error:
            raise self.stop_error

    def remove(self, force=False):
        self.calls.append(f"remove(force={force})")


def runtime_with(container):
    rt = object.__new__(DockerRuntime)          # Docker 데몬에 붙지 않도록 __init__ 을 건너뛴다
    rt._get = lambda name: container
    rt._find = lambda name: [container] if container else []      # 논리 이름으로 찾기 (예비 이름 포함)
    return rt


def test_stop_running_workspace_stops_then_removes():
    c = FakeContainer("running")
    assert runtime_with(c).stop_workspace("kim") is True
    assert c.calls == ["stop", "remove(force=True)"]


def test_stop_removes_container_that_already_exited():
    # 서버·Docker 재시작 뒤에는 컨테이너가 exited 로 남는다. 멈출 것은 없고 지우기만 하면 된다.
    c = FakeContainer("exited")
    assert runtime_with(c).stop_workspace("kim") is True
    assert c.calls == ["remove(force=True)"]


def test_stop_still_removes_when_graceful_stop_fails():
    c = FakeContainer("running", stop_error=RuntimeError("permission denied"))
    assert runtime_with(c).stop_workspace("kim") is True
    assert c.calls == ["stop", "remove(force=True)"]


def test_stop_without_container_reports_nothing_to_stop():
    assert runtime_with(None).stop_workspace("kim") is False
    assert runtime_with(None).stop_llm() is False


def test_containers_are_created_without_core_dumps():
    # 덤프가 끝나지 않는 프로세스는 죽일 수 없어 컨테이너가 영영 '실행 중'으로 남는다.
    import inspect
    from ironlab import runtime
    assert runtime.NO_CORE_DUMP == [{"name": "core", "soft": 1, "hard": 1}]
    src = inspect.getsource(DockerRuntime)
    # 컨테이너를 만드는 곳은 _run_named 하나뿐이고, 거기서 두 제한을 항상 건다. 세 종류(워크스페이스·작업·LLM)가 모두 그 길로 간다
    assert src.count("containers.run(") == src.count("ulimits=NO_CORE_DUMP") == src.count("log_config=LOG_LIMIT") == 1
    assert src.count("self._run_named(") == 3


def test_stuck_container_error_tells_admin_to_reboot():
    from ironlab.api import stuck_hint
    err = RuntimeError('cannot remove container "ironlab-ws-kim": could not kill container: '
                       'tried to kill container, but did not receive an exit event')
    assert "재부팅" in stuck_hint(err)
    assert stuck_hint(RuntimeError("image not found")) == "image not found"


def test_memory_limit_never_exceeds_real_ram():
    from ironlab.runtime import mem_limit
    gib = 2**30
    assert mem_limit(16, int(62.6 * gib)) == "16384m"              # 평소에는 할당량 그대로
    assert mem_limit(64, int(62.6 * gib)) == "62054m"              # 64GB 할당량, 실제 62.6GB: 2GB 를 남기고 자른다
    assert mem_limit(8, 4 * gib) == "2048m"
