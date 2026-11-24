"""Docker 가 컨테이너 이름을 쥐고 놓지 않을 때 (Docker 없이, 가짜 client 로 확인).

실제로 겪은 상황: `docker ps -a` 에는 ironlab-llm 이 없는데, 모델을 올리면
"409 Conflict: The container name "/ironlab-llm" is already in use by container "9805…"" 로 실패했다.
서버를 재부팅해야 풀렸다. 이제는 이름을 쥔 컨테이너를 지워 보고, 안 되면 예비 이름으로 띄운다.
"""

import docker
import pytest

from ironlab.api import stuck_hint
from ironlab.runtime import LLM_NAME, LOG_LIMIT, NAME_LABEL, NO_CORE_DUMP, DockerRuntime, job_name, name_holder

GHOST = "9805084ba786" + "0" * 52      # 목록에는 없는데 이름만 쥐고 있는 컨테이너의 ID
MODEL = {"name": "qwen", "file": "q.gguf", "ctx": 32768, "parallel": 2, "vram_gb": 20, "mmproj": "", "extra_args": ""}


def conflict(name: str, holder: str) -> docker.errors.APIError:
    return docker.errors.APIError(
        "409 Client Error: Conflict",
        explanation=f'Conflict. The container name "/{name}" is already in use by container "{holder}". '
                    "You have to remove (or rename) that container to be able to reuse that name.")


class FakeContainer:
    def __init__(self, owner, cid, name, labels, kwargs):
        self.owner, self.id, self.name, self.labels, self.kwargs = owner, cid, name, labels, kwargs
        self.status = "running"
        self.attrs = {"State": {"Status": "running", "Pid": 4242, "StartedAt": "2026-11-13T00:35:00.0Z"}}

    def reload(self):
        pass

    def stop(self, timeout=None):
        self.status = "exited"

    def remove(self, force=False):
        self.owner.items.pop(self.id, None)


class FakeContainers:
    """docker client.containers 흉내. ghosts: 이름 → 그 이름을 쥔 (목록에 없는) 컨테이너 ID."""

    def __init__(self):
        self.items: dict[str, FakeContainer] = {}
        self.ghosts: dict[str, str] = {}
        self.run_error: Exception | None = None
        self.tried: list[str] = []

    def get(self, key):
        for c in self.items.values():
            if key in (c.id, c.name):
                return c
        raise docker.errors.NotFound(f"No such container: {key}")

    def list(self, all=False, filters=None):
        key, _, value = (filters or {}).get("label", "=").partition("=")
        return [c for c in self.items.values() if not key or c.labels.get(key) == value]

    def run(self, image, name, detach, labels, **kwargs):
        self.tried.append(name)
        if name in self.ghosts:
            raise conflict(name, self.ghosts[name])
        if self.run_error:
            raise self.run_error
        cid = f"{len(self.tried):064x}"
        self.items[cid] = FakeContainer(self, cid, name, labels, kwargs)
        return self.items[cid]


class FakeApi:
    """docker client.api 흉내. removable 에 든 ID 만 지울 수 있다 (나머지는 'No such container')."""

    def __init__(self, containers):
        self.containers, self.removable, self.removed = containers, set(), []

    def remove_container(self, cid, force=False):
        if cid not in self.removable:
            raise docker.errors.NotFound(f"No such container: {cid}")
        self.removed.append(cid)
        for name, holder in list(self.containers.ghosts.items()):
            if holder == cid:
                del self.containers.ghosts[name]


class FakeClient:
    def __init__(self):
        self.containers = FakeContainers()
        self.api = FakeApi(self.containers)


@pytest.fixture
def rt(settings):
    r = object.__new__(DockerRuntime)       # Docker 데몬에 붙지 않도록 __init__ 을 건너뛴다
    r.s, r._docker, r.client = settings, docker, FakeClient()
    r._Mount = lambda **kw: kw
    r._gpu = []
    return r


def test_error_text_is_recognised():
    real = ('409 Client Error for http+docker://localhost/v1.56/containers/create?name=ironlab-llm: Conflict '
            '("The container name "/ironlab-llm" is already in use by container "9805084ba786abcdef")')
    assert name_holder(RuntimeError(real)) == "9805084ba786abcdef"
    assert name_holder(conflict("ironlab-llm", GHOST)) == GHOST
    assert name_holder(RuntimeError("is already in use by container")) == ""     # ID 를 못 읽어도 이름 충돌로 본다
    assert name_holder(RuntimeError("port is already allocated")) is None


def test_model_loads_under_a_spare_name_when_the_name_is_stuck(rt):
    fake = rt.client.containers
    fake.ghosts[LLM_NAME] = GHOST                   # 목록에는 없고, 지울 수도 없는데 이름만 쥐고 있다
    assert rt.get_llm() is None
    info = rt.start_llm(MODEL, loaded_by="kim")
    assert fake.tried == [LLM_NAME, f"{LLM_NAME}.r2"]
    assert info["name"] == f"{LLM_NAME}.r2" and info["renamed_from"] == LLM_NAME and info["model"] == "qwen"
    # 이름이 달라도 라벨로 찾으므로 상태 확인과 내리기가 그대로 된다
    assert rt.get_llm()["id"] == info["id"]
    assert [c["name"] for c in rt.list_managed()] == [f"{LLM_NAME}.r2"]
    assert rt.stop_llm() is True and rt.get_llm() is None and not fake.items


def test_holder_is_removed_by_id_and_the_normal_name_is_used(rt):
    fake = rt.client.containers
    fake.ghosts[LLM_NAME] = GHOST
    rt.client.api.removable.add(GHOST)              # 이름으로는 안 보여도 ID 로는 지워지는 경우
    info = rt.start_llm(MODEL)
    assert rt.client.api.removed == [GHOST]
    assert fake.tried == [LLM_NAME, LLM_NAME]
    assert info["name"] == LLM_NAME and info["renamed_from"] is None


def test_normal_name_comes_back_once_the_name_is_free(rt):
    fake = rt.client.containers
    fake.ghosts[LLM_NAME] = GHOST
    rt.start_llm(MODEL)
    del fake.ghosts[LLM_NAME]                       # 재부팅 뒤: Docker 가 이름을 놓았다
    info = rt.start_llm(MODEL)
    assert info["name"] == LLM_NAME
    assert [c.name for c in fake.items.values()] == [LLM_NAME]      # 예비 이름으로 떴던 것은 남기지 않는다


def test_other_errors_are_not_retried_under_spare_names(rt):
    fake = rt.client.containers
    fake.run_error = docker.errors.APIError("500 Server Error", explanation="Bind for 127.0.0.1:8800 failed: "
                                            "port is already allocated")
    with pytest.raises(docker.errors.APIError) as e:
        rt.start_llm(MODEL)
    assert fake.tried == [LLM_NAME]
    assert "포트" in stuck_hint(e.value)


def test_gives_up_with_a_clear_message_when_every_name_is_stuck(rt):
    fake = rt.client.containers
    for n in (LLM_NAME, f"{LLM_NAME}.r2", f"{LLM_NAME}.r3", f"{LLM_NAME}.r4"):
        fake.ghosts[n] = GHOST
    with pytest.raises(docker.errors.APIError) as e:
        rt.start_llm(MODEL)
    assert len(fake.tried) == 4 and not fake.items
    hint = stuck_hint(e.value)
    assert "이름" in hint and "재부팅" in hint


def test_jobs_survive_a_stuck_name_too(rt):
    fake = rt.client.containers
    fake.ghosts[job_name(7)] = GHOST
    user = {"name": "kim", "uid": 20001, "cpus": 4, "mem_gb": 8}
    job = {"id": 7, "command": "python train.py", "workdir": "", "vram_gb": 8, "cpus": 4, "mem_gb": 8}
    cid = rt.run_job(job, user)
    c = fake.items[cid]
    assert c.name == f"{job_name(7)}.r2" and c.labels["ironlab.job_id"] == "7"
    rt.remove(job_name(7))                          # 스케줄러는 작업 이름으로 지운다
    assert not fake.items


def test_every_container_gets_the_limits_and_the_name_label(rt):
    rt.start_llm(MODEL)
    rt.run_job({"id": 1, "command": "true", "workdir": "", "vram_gb": 1, "cpus": 1, "mem_gb": 1},
               {"name": "kim", "uid": 20001, "cpus": 4, "mem_gb": 8})
    cs = list(rt.client.containers.items.values())
    assert len(cs) == 2
    for c in cs:
        assert c.kwargs["ulimits"] == NO_CORE_DUMP and c.kwargs["log_config"] == LOG_LIMIT
        assert c.labels[NAME_LABEL] == c.name


def test_workspace_is_found_and_stopped_under_a_spare_name(rt):
    fake = rt.client.containers
    fake.items["a" * 64] = FakeContainer(fake, "a" * 64, "ironlab-ws-kim.r2",
                                         {NAME_LABEL: "ironlab-ws-kim", "ironlab.kind": "workspace",
                                          "ironlab.user": "kim"}, {})
    ws = rt.get_workspace("kim")
    assert ws["state"] == "running" and ws["renamed_from"] == "ironlab-ws-kim"
    assert rt.get_workspace("lee") is None
    assert rt.stop_workspace("kim") is True and rt.get_workspace("kim") is None


def test_dashboard_tells_the_admin_about_renamed_containers(env, monkeypatch):
    client, ctx, admin, alice = env
    client.post("/api/workspace/start", headers=alice)
    real = ctx.rt.list_managed
    monkeypatch.setattr(ctx.rt, "list_managed", lambda: [c | {"renamed_from": c["name"]} for c in real()])
    ctx.monitor.collect()
    alerts = client.get("/api/overview", headers=alice).json()["alerts"]
    assert any("예비 이름" in a and "ironlab-ws-alice" in a for a in alerts)
