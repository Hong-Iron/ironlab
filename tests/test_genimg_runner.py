"""deploy/genimg/qwen_image.py 의 옵션 처리와 '참고 그림을 베끼고 탄 결과' 알아보기를 GPU 없이 시험한다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "deploy" / "genimg" / "qwen_image.py"


@pytest.fixture()
def qi(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("qwen_image_runner", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lora = tmp_path / "angles.safetensors"
    lora.write_bytes(b"x")
    monkeypatch.setattr(mod, "ANGLE_LORA", str(lora))     # 시험 기계에는 LoRA 파일이 없다
    return mod


def base_args(tmp_path, *extra):
    return ["--prompt", "Dog", "--output", str(tmp_path / "out.png"), "--model", "base", *extra]


def test_height_only_with_reference_turns_to_front(qi, tmp_path, capsys):
    args = qi.prepare(base_args(tmp_path, "--image", "a.png", "--elevation", "top"))
    assert args.view == "front"
    assert args.text == "<mva> front view, top-down shot"
    assert args.copy_retries == qi.COPY_RETRIES_ANGLE
    assert "정면으로 둡니다" in capsys.readouterr().out


def test_close_up_only_with_reference_turns_to_front(qi, tmp_path):
    args = qi.prepare(base_args(tmp_path, "--image", "a.png", "--close-up"))
    assert args.view == "front"
    assert args.text.endswith("close-up")


def test_height_without_reference_is_ignored_with_a_note(qi, tmp_path, capsys):
    args = qi.prepare(base_args(tmp_path, "--elevation", "top"))
    assert args.view is None
    assert args.text == "Dog"
    assert args.copy_retries == 0
    assert "이번에는 쓰지 않습니다" in capsys.readouterr().out


def test_angle_uses_first_reference_only(qi, tmp_path, capsys):
    args = qi.prepare(base_args(tmp_path, "--image", "a.png", "--image", "b.png", "--view", "back"))
    assert args.image == ["a.png"]
    assert "첫 장(a.png)만" in capsys.readouterr().out


def test_plain_edit_keeps_all_references_and_does_not_retry(qi, tmp_path):
    args = qi.prepare(base_args(tmp_path, "--image", "a.png", "--image", "b.png"))
    assert args.image == ["a.png", "b.png"]
    assert args.copy_retries == 0


def test_copy_retries_range(qi, tmp_path):
    assert qi.prepare(base_args(tmp_path, "--image", "a.png", "--view", "back", "--copy-retries", "0")).copy_retries == 0
    with pytest.raises(qi.BadRequest, match="0~5"):
        qi.prepare(base_args(tmp_path, "--image", "a.png", "--view", "back", "--copy-retries", "9"))


def test_copy_ratio_flags_burnt_copy_and_passes_soft_result(qi):
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    rng = np.random.default_rng(0)
    # 참고 그림: 부드러운 무늬 (큰 덩어리 몇 개)
    small = rng.random((8, 8)).astype(np.float32)
    ref = Image.fromarray((small * 255).astype(np.uint8)).resize((1024, 1024), Image.BICUBIC).convert("RGB")
    # 탄 결과: 같은 그림에 털 몇 가닥 굵기(4~8px)의 센 줄무늬를 얹는다
    stripes = (np.indices((1024, 1024)).sum(0) // 6 % 2 * 120).astype(np.float32)
    burnt = np.clip(np.asarray(ref, dtype=np.float32) + stripes[..., None] - 60, 0, 255).astype(np.uint8)
    assert qi.copy_ratio(Image.fromarray(burnt), ref) >= qi.COPY_EDGE_LIMIT
    # 멀쩡한 결과: 참고 그림을 조금 흐리게 한 그림
    soft = ref.resize((512, 512), Image.BICUBIC).resize((1024, 1024), Image.BICUBIC)
    assert qi.copy_ratio(soft, ref) < qi.COPY_EDGE_LIMIT


def test_copy_ratio_compares_at_the_same_size(qi):
    pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    ref = Image.radial_gradient("L").resize((2048, 2048)).convert("RGB")
    out = ref.resize((1024, 1024), Image.LANCZOS)
    assert qi.copy_ratio(out, ref) == pytest.approx(1.0, abs=0.05)    # 크기만 다른 같은 그림은 1 쯤
