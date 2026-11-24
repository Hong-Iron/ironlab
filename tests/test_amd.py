"""AMD GPU: sysfs·fdinfo 로 읽기, 컨테이너에 GPU 한 장 붙이기. 그리고 WSL2 처럼 프로세스별 사용량을 모를 때의 큐 계산."""

import os
import shutil

import pytest

from ironlab import gpu as gpu_mod
from ironlab.config import load_settings
from ironlab.gpu import AmdGpu, amd_cards, amd_name, is_wsl, parse_drm_fdinfo, resolve_vendor
from ironlab.runtime import gpu_container_args
from ironlab.scheduler import plan_admissions

GiB = 2**30


def _card(sys_root, n, pci, vendor="0x1002", device="0x744c", vram_total=24 * GiB, vram_used=3 * GiB,
          render=128, hwmon=True):
    dev = sys_root / "devices" / "pci0000:00" / pci
    (dev / "drm" / f"card{n}").mkdir(parents=True)
    (dev / "drm" / f"renderD{render}").mkdir()
    for name, value in (("vendor", vendor), ("device", device), ("gpu_busy_percent", "37"),
                        ("mem_busy_percent", "12")):
        (dev / name).write_text(value + "\n")
    if vram_total is not None:
        (dev / "mem_info_vram_total").write_text(f"{vram_total}\n")
        (dev / "mem_info_vram_used").write_text(f"{vram_used}\n")
    if hwmon:
        hw = dev / "hwmon" / "hwmon3"
        hw.mkdir(parents=True)
        (hw / "temp1_input").write_text("54000\n")
        (hw / "power1_average").write_text("212000000\n")
        (hw / "power1_cap").write_text("327000000\n")
        (hw / "pwm1").write_text("102\n")
    card = sys_root / "class" / "drm" / f"card{n}"
    card.mkdir(parents=True)
    (card / "device").symlink_to(dev)
    return dev


@pytest.fixture
def sysfs(tmp_path):
    root = tmp_path / "sys"
    _card(root, 0, "0000:03:00.0")                                                        # RX 7900 XTX
    _card(root, 1, "0000:00:02.0", vendor="0x8086", device="0xa780", vram_total=None,    # 내장 Intel
          render=129, hwmon=False)
    _card(root, 2, "0000:0a:00.0", device="0x73bf", vram_total=16 * GiB, render=130)       # RX 6800
    (root / "class" / "drm" / "card0-DP-1").mkdir()                                       # 출력 단자 (카드 아님)
    return root


def _client(proc, pid, fd, target, info):
    (proc / str(pid) / "fd").mkdir(parents=True, exist_ok=True)
    (proc / str(pid) / "fdinfo").mkdir(parents=True, exist_ok=True)
    os.symlink(target, proc / str(pid) / "fd" / str(fd))
    (proc / str(pid) / "fdinfo" / str(fd)).write_text(info)


@pytest.fixture
def procfs(tmp_path):
    proc = tmp_path / "proc"
    old = "pos:\t0\nflags:\t02100002\ndrm-driver:\tamdgpu\ndrm-pdev:\t0000:03:00.0\ndrm-client-id:\t7\n"
    _client(proc, 100, 5, "/dev/dri/renderD128", old + "drm-memory-vram:\t2097152 KiB\ndrm-memory-gtt:\t1024 KiB\n")
    _client(proc, 100, 6, "/dev/dri/renderD128", old + "drm-memory-vram:\t2097152 KiB\n")  # 같은 클라이언트 (dup)
    _client(proc, 200, 3, "/dev/dri/renderD130",                                             # 다른 카드
            "drm-driver:\tamdgpu\ndrm-pdev:\t0000:0a:00.0\ndrm-client-id:\t9\ndrm-memory-vram:\t1048576 KiB\n")
    _client(proc, 300, 4, "/dev/dri/renderD128",                                             # 6.9 이후 커널의 키
            "drm-driver:\tamdgpu\ndrm-pdev:\t0000:03:00.0\ndrm-client-id:\t11\n"
            "drm-total-vram:\t600 MiB\ndrm-resident-vram:\t512 MiB\n")
    _client(proc, 400, 1, "/dev/null", "pos:\t0\n")                                          # GPU 와 상관없는 fd
    _client(proc, 500, 7, "/dev/dri/card1", "drm-driver:\ti915\ndrm-client-id:\t2\n")         # 다른 드라이버
    (proc / "self").mkdir()
    return proc


def test_cards_are_amd_only_in_card_order(sysfs):
    cards = amd_cards(sysfs)
    assert [(c["card"], c["render"], c["pci"]) for c in cards] == [
        ("card0", "renderD128", "0000:03:00.0"), ("card2", "renderD130", "0000:0a:00.0")]


def test_name_comes_from_pci_ids(sysfs, tmp_path):
    ids = tmp_path / "pci.ids"
    ids.write_text("1000  Broadcom\n\t0001  Something\n"
                   "1002  Advanced Micro Devices, Inc. [AMD/ATI]\n"
                   "\t73bf  Navi 21 [Radeon RX 6800/6800 XT / 6900 XT]\n"
                   "\t744c  Navi 31 [Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M]\n"
                   "\t\t1002 0e3b  Radeon RX 7900 XTX\n"
                   "1003  ULSI\n")
    dev = amd_cards(sysfs)[0]["device"]
    assert amd_name(dev, pci_ids=[ids]) == "AMD Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M"
    assert amd_name(dev, pci_ids=[tmp_path / "none"]) == "AMD GPU (1002:744c)"
    (dev / "product_name").write_text("AMD Instinct MI210\n")
    assert amd_name(dev, pci_ids=[ids]) == "AMD Instinct MI210"


def test_snapshot_reads_sysfs_and_hwmon(sysfs, procfs):
    g = AmdGpu(0, sys_root=sysfs, proc_root=procfs)
    assert g.total_gb == 24
    s = g.snapshot()
    assert (s["available"], s["total_mb"], s["used_mb"]) == (True, 24 * 1024, 3 * 1024)
    assert (s["util"], s["mem_util"], s["temp"], s["power_w"], s["power_limit_w"], s["fan"]) == (37, 12, 54, 212.0, 327, 40)
    assert s["per_process"] is True


def test_processes_from_fdinfo(sysfs, procfs):
    clock = [0.0]
    g = AmdGpu(0, sys_root=sysfs, proc_root=procfs, clock=lambda: clock[0])
    # 100: 2 GiB (같은 클라이언트를 쥔 fd 두 개는 한 번만), 300: 새 키 512 MiB. 200 은 다른 카드라 뺀다
    assert g.snapshot()["processes"] == [{"pid": 100, "used_mb": 2048}, {"pid": 300, "used_mb": 512}]
    assert AmdGpu(1, sys_root=sysfs, proc_root=procfs).snapshot()["processes"] == [{"pid": 200, "used_mb": 1024}]

    shutil.rmtree(procfs / "300")                         # 프로세스가 끝났다: 다시 훑기 전에도 빠진다
    clock[0] = 1.0
    assert g.snapshot()["processes"] == [{"pid": 100, "used_mb": 2048}]


def test_fdinfo_parsing_edge_cases():
    assert parse_drm_fdinfo("drm-driver:\ti915\n") is None
    assert parse_drm_fdinfo("pos:\t0\n") is None
    assert parse_drm_fdinfo("drm-driver:\tamdgpu\ndrm-client-id:\t3\n")["vram"] == 0
    assert parse_drm_fdinfo("drm-driver:\tamdgpu\ndrm-memory-vram:\t3 MiB\n")["vram"] == 3 * 2**20


def test_bad_index_and_missing_cards(sysfs, tmp_path):
    with pytest.raises(RuntimeError, match="2장"):
        AmdGpu(2, sys_root=sysfs, proc_root=tmp_path)
    with pytest.raises(RuntimeError, match="amdgpu"):
        AmdGpu(0, sys_root=tmp_path / "empty", proc_root=tmp_path)


def test_container_gets_kfd_and_one_render_node(sysfs, tmp_path):
    dev = tmp_path / "dev"
    (dev / "dri").mkdir(parents=True)
    for f in ("kfd", "dri/renderD128", "dri/renderD130"):
        (dev / f).write_text("")
    gids = sorted({os.stat(dev / "kfd").st_gid, os.stat(dev / "dri/renderD130").st_gid} - {0})

    s = load_settings(gpu_index=1, rocm_gfx_override="10.3.0")
    args, env = gpu_container_args(s, "amd", sys_root=sysfs, dev_root=dev)
    assert args["devices"] == [f"{dev}/kfd:/dev/kfd:rwm", f"{dev}/dri/renderD130:/dev/dri/renderD130:rwm"]
    assert args["group_add"] == [str(g) for g in gids]
    assert env == {"IRONLAB_GPU_GIDS": ",".join(str(g) for g in gids), "HSA_OVERRIDE_GFX_VERSION": "10.3.0"}
    assert "device_requests" not in args

    assert gpu_container_args(load_settings(gpu_index=5), "amd", sys_root=sysfs, dev_root=dev) == ({}, {})


def test_nvidia_container_gets_one_device_request():
    args, env = gpu_container_args(load_settings(gpu_index=1), "nvidia")
    (req,) = args["device_requests"]
    assert req["DeviceIDs"] == ["1"] and req["Capabilities"] == [["gpu"]] and env == {}


def test_vendor_and_llm_image(sysfs, monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_mod.shutil, "which", lambda *_: None)
    assert resolve_vendor(load_settings(gpu_vendor="amd")) == "amd"
    assert resolve_vendor(load_settings(gpu_vendor="nvidia"), sys_root=sysfs) == "nvidia"
    if not os.path.exists("/dev/nvidiactl"):
        assert resolve_vendor(load_settings(gpu_vendor="auto"), sys_root=sysfs) == "amd"
    s = load_settings()
    assert s.llm_image_for("amd") == "ghcr.io/ggml-org/llama.cpp:server-rocm"
    assert s.llm_image_for("nvidia") == "ghcr.io/ggml-org/llama.cpp:server-cuda"
    assert load_settings(llm_image="my/llama:vulkan").llm_image_for("amd") == "my/llama:vulkan"


def test_wsl_is_detected_from_kernel_name(tmp_path):
    (tmp_path / "sys/kernel").mkdir(parents=True)
    (tmp_path / "sys/kernel/osrelease").write_text("5.15.167.4-microsoft-standard-WSL2\n")
    assert is_wsl(tmp_path)
    (tmp_path / "sys/kernel/osrelease").write_text("6.8.0-49-generic\n")
    assert not is_wsl(tmp_path)


def _plan(per_process, queued_gb):
    running = [{"id": 1, "user": "a", "vram_gb": 10, "exclusive": 0, "created_at": 0}]
    queued = [{"id": 2, "user": "b", "vram_gb": queued_gb, "exclusive": 0, "created_at": 1}]
    # GPU 는 11GB 를 쓰고 있다: 작업 10GB + 화면 출력 1GB. 작업의 실제 사용량은 모른다 (actual_gb 비어 있음)
    return plan_admissions(queued, running, total_gb=24, reserve_gb=1, gpu_used_gb=11, actual_gb={},
                           max_per_user=1, starvation_s=1800, now=2, per_process=per_process)


def test_budget_without_per_process_numbers_does_not_count_jobs_twice():
    assert _plan(False, 12).start == [2] and _plan(False, 12).budget_gb == 0
    assert _plan(False, 13).start == []
    # 프로세스별 수치가 있다고 믿으면, 모르는 11GB 를 큐 밖 사용량으로 보고 작업 예약 10GB 와 함께 뺀다 (여유 2GB)
    assert _plan(True, 12).start == []


def test_budget_without_per_process_still_sees_heavy_outside_use():
    running = [{"id": 1, "user": "a", "vram_gb": 4, "exclusive": 0, "created_at": 0}]
    plan = plan_admissions([], running, total_gb=24, reserve_gb=1, gpu_used_gb=15, actual_gb={},
                           max_per_user=1, starvation_s=1800, now=2, per_process=False)
    assert plan.budget_gb == 8          # 23 − max(예약 4, 실제 15)
