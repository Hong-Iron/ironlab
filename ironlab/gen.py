"""생성 화면: 사진으로 3D 모델을 만들고(3d), 글로 그림을 그리고(image), 가사로 노래를 만들고(music),
장면 설명으로 소리가 든 영상을 만들고(video), 대본을 목소리로 읽고(voice), 소리 설명이나 영상으로 효과음을 만들고(sfx),
만든 소리를 다듬는다(polish: 목소리와 반주를 나눠 목소리를 다듬고 다시 섞어 음량을 맞춘다).

모델을 돌리는 일은 사용자 홈에 있는 실행 스크립트가 한다. 여기서는 종류마다 세 가지만 한다.

1. 올린 파일과 글을 사용자 홈의 <폴더>/inputs/ 에 저장한다.
2. 실행 스크립트를 부르는 명령을 일반 GPU 작업으로 큐에 넣는다.
3. 그 작업의 상태와 결과 파일을 묶어 화면에 돌려준다.

그래서 순서 기다리기, VRAM 예약, 로그 보기, 취소는 "GPU 작업" 화면의 작업과 똑같이 동작한다.

종류 하나는 "무엇을 받고 무엇을 조절하는지"를 적은 정의다 (아래 KINDS).

- Text:   글로 받는 것 (그릴 내용, 가사, 악보 ...). 파일로 저장해 `<옵션> <파일>` 로 넘긴다.
- Upload: 올려 받는 파일 (사진, 참고 그림, 참고 목소리).
- Param:  조절하는 값 (시드, 단계 수 ...). 화면은 이 정의를 읽어 입력 칸을 그리고, 서버는 같은 정의로 값을 검사한다.

화면과 서버가 같은 정의를 쓰므로, 값을 더하거나 종류를 더할 때 한 곳만 고치면 된다.

밖에 보이는 글자에 대한 약속: 작업 이름에는 종류 이름만 쓰고, 파일 이름은 날짜와 무작위 글자로만 짓고,
글은 명령에 넣지 않고 파일로 넘긴다. 작업 목록은 다른 사용자에게도 보이고 관리자는 작업 명령을 볼 수 있기 때문이다.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import secrets
import shlex
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable

from . import files as fs
from .config import Settings
from .db import DB
from .scheduler import Scheduler

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv")
MEDIA_EXTS = AUDIO_EXTS + VIDEO_EXTS        # 소리나 영상 (소리 다듬기는 영상의 소리도 다듬는다)
MAX_SEED = 2**31 - 1
MAX_THUMB_BYTES = 300_000       # 화면이 만들어 보내는 작은 미리보기 사진의 크기 한도
MAX_PRESETS = 40                # 사용자·종류마다 저장할 수 있는 설정 묶음 수
MAX_SCHEDULE = 100              # 직접 주는 잡음 시간표의 최대 단계 수
PROGRESS_TAIL_LINES = 12        # 진행 단계를 읽을 때 보는 로그 끝 줄 수
PERCENT_RE = re.compile(r"(\d{1,3})%\|")
BARE_PERCENT_RE = re.compile(r"(?m)^\s*(\d{1,3})%\|")     # 줄 맨 앞에서 시작하는(이름 없는) 진행 막대


class GenError(ValueError):
    pass


def _num(v: float | None) -> str:
    """오류 글에 넣을 수. 정수는 천 단위 쉼표를 찍고(2,000,000), 소수는 짧게 쓴다(0.05)."""
    if v is None:
        return "제한 없음"
    return f"{int(v):,}" if float(v).is_integer() else f"{v:g}"


# ── 정의에 쓰는 조각 ──

@dataclass(frozen=True)
class Param:
    """조절하는 값 하나.

    type: int | float | choice | bool | schedule. default 가 None 이면 "모델 기본값"이라는 뜻으로, 명령에 넘기지 않는다.
    schedule 은 잡음 시간표다: 1 이하에서 내려가는 수를 쉼표로 이은 글 (예: "1,0.95,0.85,0.6"). 수의 개수가 단계 수가 된다.
    group: 화면의 "상세 설정"에서 묶는 이름. 빈 글자면 기본 양식에 늘 보인다.
    flag: 실행 스크립트에 넘길 옵션 이름.
    """
    key: str
    label: str
    type: str
    default: Any
    flag: str
    help: str = ""
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    choices: tuple[tuple[str, str], ...] = ()       # (값, 화면에 보일 이름)
    group: str = ""
    off_flag: str = ""                              # 참/거짓 값에서 거짓일 때 넘길 옵션 (기본이 참인 값에 쓴다)

    def clean(self, raw: Any) -> Any:
        """받은 값을 검사해 돌려준다. 틀리면 무엇이 틀렸는지와 받은 값을 적은 GenError."""
        if raw is None or raw == "":
            return self.default
        if self.type == "bool":
            if not isinstance(raw, bool):
                raise GenError(f"'{self.label}'은 참 또는 거짓이어야 합니다 (받은 값 {raw!r})")
            return raw
        if self.type == "choice":
            if raw not in [c[0] for c in self.choices]:
                raise GenError(f"'{self.label}'은 {', '.join(c[0] for c in self.choices)} 가운데 하나여야 합니다 (받은 값 {raw!r})")
            return raw
        if self.type == "schedule":
            try:
                values = [float(v) for v in raw.split(",") if v.strip()] if isinstance(raw, str) else None
            except ValueError:
                values = None
            if values is None:
                raise GenError(f"'{self.label}'은 쉼표로 이은 수여야 합니다 (받은 값 {raw!r}). 예: 1,0.95,0.85,0.6")
            if (not 1 <= len(values) <= MAX_SCHEDULE or any(not 0 < v <= 1 for v in values)
                    or values != sorted(values, reverse=True)):
                raise GenError(f"'{self.label}'은 0 보다 크고 1 이하인 수 1~{MAX_SCHEDULE}개를 큰 것부터 적어야 합니다 (받은 값 {raw!r})")
            return ",".join(f"{v:g}" for v in values)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise GenError(f"'{self.label}'은 숫자여야 합니다 (받은 값 {raw!r})")
        if self.type == "int":
            if isinstance(raw, float) and not raw.is_integer():
                raise GenError(f"'{self.label}'은 정수여야 합니다 (받은 값 {raw!r})")
            raw = int(raw)
        else:
            raw = float(raw)
        if (self.lo is not None and raw < self.lo) or (self.hi is not None and raw > self.hi):
            raise GenError(f"'{self.label}'은 {_num(self.lo)} 이상 {_num(self.hi)} 이하여야 합니다 (받은 값 {_num(raw)})")
        return raw

    def spec(self) -> dict:
        return {"key": self.key, "label": self.label, "type": self.type, "default": self.default, "help": self.help,
                "min": self.lo, "max": self.hi, "step": self.step, "choices": [list(c) for c in self.choices],
                "group": self.group}


@dataclass(frozen=True)
class Text:
    """글로 받는 것 하나. 사용자 홈의 inputs/<이름>.<key><ext> 에 저장해 `<flag> <파일>` 로 넘긴다."""
    key: str
    label: str
    flag: str
    required: bool = True
    max_chars: int = 4000
    rows: int = 4
    help: str = ""
    placeholder: str = ""
    group: str = ""
    ext: str = ".txt"

    def clean(self, raw: Any) -> str:
        text = (raw or "").strip() if isinstance(raw, str) or raw is None else None
        if text is None:
            raise GenError(f"'{self.label}'은 글이어야 합니다 (받은 값 {raw!r})")
        if self.required and not text:
            raise GenError(f"'{self.label}'을 적어 주세요.")
        if len(text) > self.max_chars:
            raise GenError(f"'{self.label}'이 너무 깁니다 ({len(text)}자). {self.max_chars}자까지 됩니다.")
        return text

    def spec(self) -> dict:
        return {"key": self.key, "label": self.label, "required": self.required, "max_chars": self.max_chars,
                "rows": self.rows, "help": self.help, "placeholder": self.placeholder, "group": self.group,
                "mono": self.ext != ".txt"}


@dataclass(frozen=True)
class Upload:
    """올려 받는 파일 (사진, 소리, 영상). 사용자 홈의 inputs/ 에 저장해 한 개마다 `<flag> <파일>` 로 넘긴다.

    slots: 고른 순서마다 뜻이 다를 때 그 이름 (영상의 첫 장면, 끝 장면). 화면이 파일 아래에 붙여 보인다.
    roles: 파일마다 사용자가 "무엇인지"를 하나씩 고른다 (3D: 어느 방향에서 찍었는지). 두 장 이상일 때
    그 이름들을 쉼표로 이어 `<roles_flag> <이름,이름,...>` 로 넘긴다. first_role 이 있으면 첫 사진은 그 역할이어야 한다.
    """
    label: str
    flag: str
    lo: int
    hi: int
    help: str = ""
    exts: tuple[str, ...] = IMAGE_EXTS
    slots: tuple[str, ...] = ()
    roles: tuple[tuple[str, str, str], ...] = ()    # (값, 화면 이름, 찍는 법)
    roles_flag: str = ""
    first_role: str = ""

    @property
    def audio(self) -> bool:
        return self.exts == AUDIO_EXTS

    @property
    def video(self) -> bool:
        return self.exts == VIDEO_EXTS

    @property
    def media(self) -> bool:
        return self.exts == MEDIA_EXTS

    def spec(self) -> dict:
        return {"label": self.label, "min": self.lo, "max": self.hi, "help": self.help, "exts": list(self.exts),
                "audio": self.audio, "video": self.video, "media": self.media, "slots": list(self.slots),
                "roles": [list(r) for r in self.roles], "first_role": self.first_role}

    def clean_roles(self, raw, n: int) -> list[str]:
        """사진마다 받은 역할을 검사한다. 한 장이면 역할은 없어도 되고, 두 장 이상이면 장마다 하나씩 있어야 한다."""
        if not self.roles:
            return []
        if raw in (None, "", []) and n <= 1:
            return [self.first_role] if n == 1 and self.first_role else []
        if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
            raise GenError(f"{self.label}마다 붙인 방향은 글자 목록이어야 합니다 (받은 값 {str(raw)[:60]!r})")
        names = {r[0]: r[1] for r in self.roles}
        if len(raw) != n:
            raise GenError(f"{self.label}은 {n}장인데 방향은 {len(raw)}개입니다. {self.label}마다 방향을 하나씩 골라 주세요")
        bad = [r for r in raw if r not in names]
        if bad:
            raise GenError(f"모르는 방향입니다 (받은 값 {', '.join(bad)}). {', '.join(names)} 가운데 하나여야 합니다")
        if len(set(raw)) != len(raw):
            twice = sorted({names[r] for r in raw if raw.count(r) > 1})
            raise GenError(f"같은 방향이 두 번 나옵니다 ({', '.join(twice)}). 방향마다 {self.label}은 한 장입니다")
        if self.first_role and raw[0] != self.first_role:
            raise GenError(f"첫 {self.label}은 '{names[self.first_role]}' 방향이어야 합니다 (받은 값 '{names[raw[0]]}'). "
                           f"다른 {self.label}은 이 {self.label}을 기준으로 놓입니다")
        return list(raw)


@dataclass(frozen=True)
class Refs:
    """참고 자료: 그림·영상·소리를 섞어 받는다 (영상의 Ref2VA). 종류마다 다른 옵션으로 넘기고, 사용자는 글에서
    <Picture 1>, <Video 1>, <Audio 1> 처럼 부른다. 번호는 종류마다 올린 차례로 붙는다.
    exclusive 면 Kind.upload(첫·끝 장면)와 함께 받지 않는다 (모델이 둘을 함께 받지 않는다)."""
    label: str
    help: str
    kinds: tuple[tuple[str, str, str, int, tuple[str, ...]], ...]   # (종류, 화면 이름, 옵션, 최대 수, 확장자)
    exclusive: bool = True

    @property
    def exts(self) -> tuple[str, ...]:
        return tuple(e for k in self.kinds for e in k[4])

    def kind_of(self, ext: str) -> str:
        return next(k[0] for k in self.kinds if ext in k[4])

    def spec(self) -> dict:
        tags = {"image": "Picture", "video": "Video", "audio": "Audio"}
        return {"label": self.label, "help": self.help, "exclusive": self.exclusive, "exts": list(self.exts),
                "kinds": [{"kind": k, "label": label, "max": hi, "exts": list(exts), "tag": tags.get(k, k)}
                          for k, label, _, hi, exts in self.kinds]}


@dataclass(frozen=True)
class Kind:
    key: str                            # 주소(/api/gen/<key>)와 DB 에 쓰는 이름
    title: str                          # 화면 이름이자 작업 이름 (다른 사용자에게도 보이는 글자)
    dir_setting: str                    # 설정 이름: 홈 기준 폴더
    script_setting: str                 # 설정 이름: 그 폴더 안의 실행 스크립트
    vram_setting: str                   # 설정 이름: 작업 하나에 예약할 VRAM
    out_ext: str                        # 결과 파일의 확장자
    texts: tuple[Text, ...]
    params: tuple[Param, ...]
    progress: Callable[[str], dict | None]
    upload: Upload | None = None
    refs: Refs | None = None            # 참고 자료 (영상의 Ref2VA)
    preview: str = ""                   # 작업이 결과 옆에 함께 쓰는 가벼운 사본의 꼬리 이름과 옵션 ("" 이면 없음)
    preview_flag: str = ""
    extra_flags: tuple[tuple[str, str], ...] = ()   # (옵션, 결과 옆에 쓸 파일의 꼬리 이름): 악보 같은 곁 결과
    client_thumb: bool = False          # 화면이 올린 사진에서 작은 미리보기를 만들어 함께 보낸다 (3D)
    vram_multi_setting: str = ""        # 설정 이름: 파일을 여러 장 받았을 때 예약할 VRAM (3D 여러 장은 모델이 다르다)
    seen_flag: str = ""                 # 파일을 여러 장 받았을 때, 모델이 실제로 받은 사진을 `<seen_flag> outputs/<이름>.views` 에 남기게 하는 옵션
    check: Callable[[dict, int, dict], None] | None = None  # 글·파일 수·설정을 함께 보는 검사. 틀리면 GenError
    vram_for: Callable[[dict], float | None] | None = None  # 설정에 따라 예약할 VRAM 을 바꾼다 (None 이면 설정 값). 소리 다듬기가 나누지 않을 때

    def folder(self, s: Settings) -> str:
        return str(getattr(s, self.dir_setting)).strip("/")

    def script(self, s: Settings) -> str:
        return str(getattr(s, self.script_setting))

    def vram_gb(self, s: Settings, files: int = 1, values: dict | None = None) -> float:
        """작업 하나에 예약할 VRAM. 파일이 여러 장이고 따로 정한 값이 있으면 그 값. 설정에 따라 덜 쓰는 종류는 그 값."""
        light = self.vram_for(values) if self.vram_for and values is not None else None
        if light is not None:
            return float(light)
        name = self.vram_multi_setting if files > 1 and self.vram_multi_setting else self.vram_setting
        return float(getattr(s, name))

    def clean_params(self, raw: dict) -> dict:
        return clean_params(self.params, raw)

    def param_flags(self, values: dict) -> list[str]:
        return param_flags(self.params, values)


def clean_params(params: tuple[Param, ...], raw: dict) -> dict:
    """받은 설정을 정의에 맞춰 검사해 돌려준다. 빠진 값은 기본값으로 채우고, 모르는 이름은 거절한다."""
    known = {p.key for p in params}
    extra = sorted(set(raw) - known)
    if extra:
        raise GenError(f"모르는 설정입니다: {', '.join(extra)}. 이 종류가 받는 설정은 {', '.join(sorted(known))} 입니다.")
    return {p.key: p.clean(raw.get(p.key)) for p in params}


def param_flags(params: tuple[Param, ...], values: dict) -> list[str]:
    """검사한 설정을 실행 스크립트에 넘길 옵션 글자로 바꾼다."""
    out = []
    for p in params:
        v = values[p.key]
        if v is False and p.off_flag:
            out.append(p.off_flag)
            continue
        if v is None or v is False:
            continue                                # None: 모델 기본값을 쓴다. False: 켜는 옵션을 넘기지 않는다
        out.append(p.flag if v is True else f"{p.flag} {v:g}" if isinstance(v, float) else f"{p.flag} {v}")
    return out


def _seed(group: str = "") -> Param:
    return Param("seed", "시드", "int", 42, "--seed", lo=0, hi=MAX_SEED, step=1, group=group,
                 help="같은 입력도 시드를 바꾸면 결과가 달라집니다. 같은 시드는 거의 같은 결과를 냅니다.")


def _steps(key: str, flag: str, group: str, default: int, what: str) -> Param:
    return Param(key, "단계 수", "int", default, flag, lo=1, hi=50, step=1, group=group,
                 help=f"{what} 잡음을 걷어 내는 횟수. 늘리면 더 다듬어지고 그만큼 오래 걸립니다. 쓸 만한 범위는 8~25.")


def _guidance(key: str, flag: str, group: str, default: float, what: str) -> Param:
    return Param(key, "가이던스", "float", default, flag, lo=0, hi=20, step=0.1, group=group,
                 help=f"{what} 사진을 얼마나 세게 따를지. 키우면 사진에 더 붙고, 너무 키우면 거칠어집니다. 1 이면 끕니다.")


def _rescale(key: str, flag: str, group: str, default: float) -> Param:
    return Param(key, "가이던스 누르기", "float", default, flag, lo=0, hi=1, step=0.05, group=group,
                 help="가이던스 때문에 과해진 대비를 눌러 주는 정도. 0 이면 누르지 않고 1 이면 가장 세게 누릅니다.")


def _rescale_t(key: str, flag: str, group: str, default: float) -> Param:
    return Param(key, "앞 구간 몰기", "float", default, flag, lo=0.5, hi=10, step=0.1, group=group,
                 help="단계를 잡음이 많은 앞 구간에 몰아 쓰는 정도. 키우면 큰 형태를 잡는 데 단계를 더 씁니다. 1 이면 고르게 씁니다.")


# ── 종류: 3D ──

# 실행 로그에 찍히는 글자 → 화면에 보일 단계 이름. 아래로 갈수록 뒤 단계다 (Pixal3D 가 찍는 진행 막대 이름).
STAGES_3D = [
    ("[Pipeline] Loading", "모델 올리기", False),
    ("Sampling sparse structure", "뼈대 잡기", True),
    ("Sampling shape SLat", "형상 만들기", True),
    ("Sampling HR shape SLat", "형상 다듬기", True),
    ("Sampling texture SLat", "색 입히기", True),
    ("Extracting GLB", "GLB 파일 쓰기", False),
]
# GLB 를 쓰는 동안에는 "Extracting GLB" 줄이 로그 끝에서 밀려난다. 그때 찍히는 막대 이름들.
GLB_BARS = ("Finalizing mesh", "Sampling attributes")


def progress_3d(log_tail: str) -> dict | None:
    """실행 로그의 끝부분에서 지금 단계를 읽는다. 아는 단계가 없으면 None.

    돌려주는 값: {"stage": 단계 이름, "step": 몇 번째, "steps": 전체 단계 수, "percent": 그 단계의 진행률 또는 None}
    """
    text = log_tail.replace("\r", "\n")
    best, at = None, -1
    for i, (mark, _, _) in enumerate(STAGES_3D):
        pos = text.rfind(mark)
        if pos > at:
            best, at = i, pos
    if any(text.rfind(bar) > at for bar in GLB_BARS):
        best, at = len(STAGES_3D) - 1, len(text)
    if best is None:
        return None
    _, label, has_percent = STAGES_3D[best]
    percent = None
    if has_percent:
        found = PERCENT_RE.findall(text[at:])
        if found:
            percent = min(100, int(found[-1]))
    return {"stage": label, "step": best + 1, "steps": len(STAGES_3D), "percent": percent}


G_CAM, G_SS, G_SHAPE, G_TEX, G_GLB = "1. 사진 읽기", "2. 뼈대 잡기", "3. 형상 만들기", "4. 색 입히기", "5. GLB 뽑기"

# 여러 장으로 만들 때 사진마다 고르는 방향 (pixal3d_run.py 의 VIEWS 와 같은 값이어야 한다)
VIEW_ROLES = (
    ("front", "앞", "물체의 정면. 첫 사진은 늘 이 방향이고, 다른 사진은 이 사진을 기준으로 놓입니다."),
    ("right", "오른쪽", "앞에서 보던 사람이 자기 오른쪽으로 90° 돌아가서 찍은 사진."),
    ("back", "뒤", "앞의 정반대에서 찍은 사진."),
    ("left", "왼쪽", "앞에서 보던 사람이 자기 왼쪽으로 90° 돌아가서 찍은 사진."),
    ("top", "위", "바로 위에서 내려다본 사진. 물체의 앞쪽이 사진의 아래쪽에 오게 찍습니다."),
    ("bottom", "아래", "바로 밑에서 올려다본 사진. 이때도 물체의 앞쪽이 사진의 아래쪽에 오게 찍습니다."),
)

KIND_3D = Kind(
    "3d", "3D 생성", "gen3d_dir", "gen3d_script", "gen3d_vram_gb", ".glb",
    texts=(),
    upload=Upload("사진", "--image", 1, len(VIEW_ROLES),
                  "물체 하나가 가운데에 크게 찍힌 사진이 잘 됩니다. 여러 방향에서 찍었으면 방향마다 한 장씩, 같은 거리·같은 줌으로 찍은 사진을 넣습니다.",
                  roles=VIEW_ROLES, roles_flag="--views", first_role="front"),
    vram_multi_setting="gen3d_mv_vram_gb",
    seen_flag="--save-views",
    client_thumb=True,
    progress=progress_3d,
    params=(
        _seed(),
        Param("fov_deg", "화각 (도)", "float", None, "--fov-deg", lo=5, hi=120, step=1, group=G_CAM,
              help="사진을 찍은 카메라의 가로 화각. 비워 두면 사진에서 추정합니다. 모양이 찌그러져 보이면 10~30 으로 직접 정해 보세요."),
        _steps("ss_steps", "--ss-steps", G_SS, 12, "물체가 차지하는 자리(뼈대)를 잡을 때"),
        _guidance("ss_guidance", "--ss-guidance", G_SS, 7.5, "뼈대를 잡을 때"),
        _rescale("ss_rescale", "--ss-rescale", G_SS, 0.7),
        _rescale_t("ss_rescale_t", "--ss-rescale-t", G_SS, 5.0),
        _steps("shape_steps", "--shape-steps", G_SHAPE, 12, "겉면의 모양을 만들 때"),
        _guidance("shape_guidance", "--shape-guidance", G_SHAPE, 7.5, "모양을 만들 때"),
        _rescale("shape_rescale", "--shape-rescale", G_SHAPE, 0.5),
        _rescale_t("shape_rescale_t", "--shape-rescale-t", G_SHAPE, 3.0),
        Param("max_tokens", "모양 조각 수 한도", "int", 49152, "--max-tokens", lo=8192, hi=98304, step=1024, group=G_SHAPE,
              help="모양을 나타내는 조각(토큰)의 최대 개수. 키우면 복잡한 모양을 더 담지만 GPU 메모리를 더 씁니다. 기본 49152."),
        _steps("tex_steps", "--tex-steps", G_TEX, 12, "겉면의 색과 재질을 입힐 때"),
        _guidance("tex_guidance", "--tex-guidance", G_TEX, 1.0, "색을 입힐 때"),
        _rescale("tex_rescale", "--tex-rescale", G_TEX, 0.0),
        _rescale_t("tex_rescale_t", "--tex-rescale-t", G_TEX, 3.0),
        Param("faces", "삼각형 수", "int", 1000000, "--faces", lo=10000, hi=2000000, step=10000, group=G_GLB,
              help="결과 메시를 이 삼각형 수까지 줄입니다. 줄이면 파일이 작아지고 세부가 뭉개집니다. 게임용은 5만~20만."),
        Param("texture_size", "텍스처 크기", "choice", "4096", "--texture-size", group=G_GLB,
              choices=(("1024", "1024×1024"), ("2048", "2048×2048"), ("4096", "4096×4096")),
              help="색을 담는 그림의 한 변 길이(픽셀). 줄이면 파일이 작아지고 가까이서 볼 때 흐려집니다."),
        Param("remesh", "면 다시 짜기", "bool", True, "--remesh", off_flag="--no-remesh", group=G_GLB,
              help="삼각형을 고르게 다시 짭니다. 끄면 빨라지지만 면이 고르지 않습니다."),
    ),
)

# ── 종류: 여러 방향 그림 (사진 한 장 → 물체를 여러 방향에서 본 그림, MV-Adapter) ──

MV_STEPS = ("배경 지우기", "모델 올리기", "그리기", "그림 파일 쓰기")
# 방향 이름 (mv_views.py 의 AZIMUTHS, pixal3d_run.py 의 AZIMUTH 와 같은 약속). 결과 파일은 outputs/<이름>.<방향>.png
MV_VIEWS = (("front", "앞"), ("front-right", "오른쪽 앞 (45°)"), ("right", "오른쪽"), ("back", "뒤"), ("left", "왼쪽"),
            ("front-left", "왼쪽 앞 (315°)"))
G_MV = "1. 그리기"


def progress_mv(log_tail: str) -> dict | None:
    """mv_views.py 가 찍는 줄("[mv] 배경 지우기", "[mv] 모델 올리기", 진행 막대, "[mv] 끝")에서 지금 단계를 읽는다."""
    text = log_tail.replace("\r", "\n")
    found = BARE_PERCENT_RE.findall(text)
    if "[mv] 끝" in text or "[mv] 파일" in text:
        step, percent = 4, None
    elif found:
        percent = min(100, int(found[-1]))
        step, percent = (4, None) if percent >= 100 else (3, percent)
    elif "[mv] 모델 올리기" in text or "[mv] 시작" in text:
        step, percent = 2, None
    elif "[mv]" in text:
        step, percent = 1, None
    else:
        return None
    return {"stage": MV_STEPS[step - 1], "step": step, "steps": len(MV_STEPS), "percent": percent}


KIND_MV = Kind(
    "multiview", "여러 방향 그림", "genmv_dir", "genmv_script", "genmv_vram_gb", ".png",
    texts=(
        Text("prompt", "물체 설명 (영어)", "--prompt-file", required=False, max_chars=300, rows=2,
             placeholder="예) a dog",
             help="무엇을 찍은 사진인지 영어로 짧게 적습니다. 비우면 'a high quality object' 로 그립니다. "
                  "글 이해 모델(SDXL)이 영어만 알아듣습니다."),
    ),
    upload=Upload("사진", "--image", 1, 1,
                  "물체 하나가 크게 찍힌 사진 한 장. 배경은 알아서 지웁니다. 여러 방향을 한 번에 함께 그려 서로 맞춥니다."),
    preview=".thumb.jpg", preview_flag="--thumb",
    extra_flags=(("--views-out", ".views.json"),
                 *((f"--out-{key}", f".{key}.png") for key, _ in MV_VIEWS)),
    progress=progress_mv,
    params=(
        _seed(),
        Param("views", "방향 수", "choice", "6", "--views",
              choices=(("6", "6방향 (45° 둘 포함)"), ("4", "4방향 (앞·오른쪽·뒤·왼쪽)")),
              help="6방향은 앞·오른쪽 앞(45°)·오른쪽·뒤·왼쪽·왼쪽 앞(315°), 4방향은 앞·오른쪽·뒤·왼쪽입니다. "
                     "개 사진으로 3D 를 만들어 보니 둘 다 얼굴이 하나인 머리가 나왔습니다."),
        Param("steps", "단계 수", "int", 50, "--steps", lo=10, hi=100, step=1, group=G_MV,
              help="잡음을 걷어 내는 횟수. 줄이면 빨라지고 그림이 거칠어집니다. 기본 50 (6방향 약 1분)."),
        Param("guidance", "설명 따르기", "float", 3.0, "--guidance", lo=1, hi=10, step=0.1, group=G_MV,
              help="물체 설명 글을 따르는 세기. 키우면 글에 더 붙고, 너무 키우면 색이 거칠어집니다. 기본 3."),
        Param("ref_scale", "사진 따르기", "float", 1.0, "--ref-scale", lo=0, hi=2, step=0.05, group=G_MV,
              help="올린 사진을 따르는 세기. 줄이면 사진과 덜 닮고 모양을 더 지어냅니다. 기본 1."),
    ),
)

# ── 종류: 3D (모양 먼저): 사진 한 장 3D 의 모양 위에 MV-Adapter(ig2mv)가 여섯 방향 색을 칠해 입힌다 ──
# 여러 방향 그림(i2mv)은 사진에서 모양까지 상상해 그려서, 사진 자세가 정면·눈높이가 아니면 몸이 두 개로 겹친 3D 가 나왔다
# (2026-11-16 소 사진). 여기서는 모양을 사진 한 장 3D 가 정하고 모델은 색만 칠한다. 실행 스크립트: deploy/genshape/shapefirst.sh

SHAPE_STEPS = ("배경 지우기", "사진 한 장 3D", "모양 그림 렌더링", "색 칠하기", "텍스처 입히기")
SHAPE_STAGE_RE = re.compile(r"\[shape\] 단계 (\d)/5")
SHAPE_COLORS = (("auto", "자동 (셋 가운데 사진 쪽과 가장 고르게 이어지는 것)"), ("none", "칠한 색 그대로"),
                ("group", "무리별로 맞추기 (진한 색 무리·흰 무리)"), ("overlap", "겹치는 띠로 맞추기"))
G_SHAPE_3D = "1. 모양 (사진 한 장 3D)"


def progress_shape(log_tail: str) -> dict | None:
    """shapefirst.sh 가 찍는 "[shape] 단계 k/5" 줄에서 지금 단계를 읽는다. 사진 한 장 3D 단계는 Pixal3D 의 진행 막대를,
    색 칠하기 단계는 이름 없는 진행 막대(50단계)를 함께 읽는다. 단계 줄이 로그 끝에서 밀려났으면 막대로 짐작한다."""
    text = log_tail.replace("\r", "\n")
    if "[shape] 끝" in text:
        return {"stage": SHAPE_STEPS[-1], "step": len(SHAPE_STEPS), "steps": len(SHAPE_STEPS), "percent": None}
    marks = list(SHAPE_STAGE_RE.finditer(text))
    if marks:
        step, rest = int(marks[-1].group(1)), text[marks[-1].end():]
    elif progress_3d(text) is not None:
        step, rest = 2, text
    elif BARE_PERCENT_RE.search(text):
        step, rest = 4, text
    else:
        return None
    step = min(max(step, 1), len(SHAPE_STEPS))
    stage, percent = SHAPE_STEPS[step - 1], None
    if step == 2:
        p3 = progress_3d(rest)
        if p3 is not None:
            stage, percent = f"{stage} · {p3['stage']}", p3["percent"]
    elif step == 4:
        found = BARE_PERCENT_RE.findall(rest)
        if found:
            percent = min(100, int(found[-1]))
    return {"stage": stage, "step": step, "steps": len(SHAPE_STEPS), "percent": percent}


KIND_SHAPE = Kind(
    "shapefirst", "3D 생성 (모양 먼저)", "genshape_dir", "genshape_script", "genshape_vram_gb", ".glb",
    texts=(
        Text("prompt", "물체 설명 (영어)", "--prompt-file", required=False, max_chars=300, rows=2,
             placeholder="예) a cow",
             help="무엇을 찍은 사진인지 영어로 짧게 적습니다. 색을 칠할 때만 씁니다. 비우면 'high quality' 로 칠합니다."),
    ),
    upload=Upload("사진", "--image", 1, 1,
                  "물체 하나가 크게 찍힌 사진 한 장. 배경은 알아서 지웁니다. 사진이 본 쪽은 사진 색을 그대로 둡니다."),
    preview=".thumb.jpg", preview_flag="--thumb",
    extra_flags=(("--views-image", ".painted.png"),),
    progress=progress_shape,
    params=(
        Param("seed", "모양 시드", "int", 42, "--seed", lo=0, hi=MAX_SEED, step=1,
              help="사진 한 장 3D 가 모양을 만들 때의 시드. 바꾸면 모양이 조금 달라집니다."),
        Param("paint_seed", "색 시드", "int", 1, "--paint-seed", lo=0, hi=MAX_SEED, step=1,
              help="안 보이던 옆과 뒤의 색을 칠할 때의 시드. 무늬가 마음에 들지 않으면 이것만 바꿔 봅니다. "
                   "모양 단계도 처음부터 다시 돕니다 (약 3분 반)."),
        Param("color", "색 맞추기", "choice", "auto", "--color", choices=SHAPE_COLORS,
              help="칠한 쪽의 밝기와 색 기운을 사진 쪽에 맞추는 방법. 모델은 사진의 햇빛을 이어 받아 반대쪽을 그늘처럼 어둡게 "
                   "칠하기도 합니다. 자동은 셋을 다 만들어 보고 0·90·180·270° 에서 사진 쪽과 가장 고르게 이어지는 것을 고릅니다."),
        Param("faces", "삼각형 수", "int", 200000, "--faces", lo=10000, hi=1000000, step=10000, group=G_SHAPE_3D,
              help="결과 메시를 이 삼각형 수까지 줄입니다. 줄이면 파일이 작아지고 세부가 뭉개집니다. 게임용은 5만~20만."),
        Param("texture_size", "텍스처 크기", "choice", "2048", "--texture-size", group=G_SHAPE_3D,
              choices=(("1024", "1024×1024"), ("2048", "2048×2048")),
              help="색을 담는 그림의 한 변 길이(픽셀). 칠하는 그림이 방향마다 768px 이라 2048 보다 키워도 옆·뒤는 더 또렷해지지 않습니다."),
    ),
)

# ── 종류: 이미지 ──

IMAGE_SIZES = (("2048x2048", "정사각 2048×2048"), ("1024x1024", "정사각 1024×1024 (빠름)"),
               ("2400x1792", "가로 4:3, 2400×1792"), ("1792x2400", "세로 3:4, 1792×2400"),
               ("2528x1696", "가로 3:2, 2528×1696"), ("1696x2528", "세로 2:3, 1696×2528"),
               ("2752x1536", "가로 16:9, 2752×1536"), ("1536x2752", "세로 9:16, 1536×2752"))
IMAGE_STEPS = ("모델 올리기", "그리기", "그림 파일 쓰기")
G_DRAW, G_DECODE, G_ANGLE = "1. 그리기", "2. 그림으로 풀어내기", "3. 각도 바꾸기"
ANGLE_VIEWS = (("", "끔 (보통 그리기)"), ("front", "정면"), ("front-right", "오른쪽 앞 (30°)"), ("front-right-quarter", "오른쪽 앞 (60°)"),
               ("right", "오른쪽 옆 (90°)"), ("back-right-quarter", "오른쪽 뒤 (120°)"), ("back-right", "오른쪽 뒤 (150°)"),
               ("back", "뒤 (180°)"), ("back-left", "왼쪽 뒤 (210°)"), ("back-left-quarter", "왼쪽 뒤 (240°)"),
               ("left", "왼쪽 옆 (270°)"), ("front-left-quarter", "왼쪽 앞 (300°)"), ("front-left", "왼쪽 앞 (330°)"))


def check_image(words: dict, n_files: int, values: dict) -> None:
    if values.get("view") and n_files == 0:
        raise GenError("각도 바꾸기는 참고 그림이 있어야 합니다. 다른 방향에서 볼 물체의 사진을 참고 그림으로 넣으세요.")


def progress_image(log_tail: str) -> dict | None:
    """qwen_image.py 가 찍는 줄에서 지금 단계를 읽는다 ("[qwen-image] 시작", 진행 막대, "[qwen-image] 끝")."""
    text = log_tail.replace("\r", "\n")
    # 그리는 단계의 막대는 이름 없이 " 38%|███▊ | 3/8 [...]" 꼴로 찍힌다.
    # 모델을 읽을 때의 막대("Loading weights: 100%|...")는 이름이 앞에 붙어 있어 여기에 걸리지 않는다.
    found = BARE_PERCENT_RE.findall(text)
    if "[qwen-image]" not in text and not found:
        return None
    if "[qwen-image] 끝" in text or (found and int(found[-1]) >= 100):
        step, percent = 3, None
    elif found:
        step, percent = 2, min(100, int(found[-1]))
    else:
        step, percent = 1, None
    return {"stage": IMAGE_STEPS[step - 1], "step": step, "steps": len(IMAGE_STEPS), "percent": percent}


KIND_IMAGE = Kind(
    "image", "이미지 생성", "genimg_dir", "genimg_script", "genimg_vram_gb", ".png",
    texts=(
        Text("prompt", "그릴 내용", "--prompt-file", rows=5,
             placeholder='예) 비 오는 밤, "IRONLAB" 이라고 적힌 네온 간판. 젖은 바닥에 불빛이 비친다',
             help="그림 안에 넣을 글자는 따옴표로 감싸 적습니다. 적은 글은 다른 사용자에게 보이지 않습니다."),
        Text("negative", "피할 내용", "--negative-file", required=False, rows=2, max_chars=2000, group=G_DRAW,
             help="그림에 나오지 않았으면 하는 것. 아래 가이던스가 1 보다 클 때만 듣습니다."),
    ),
    upload=Upload("참고 그림", "--image", 0, 4, "넣으면 글대로 이 그림을 고칩니다. 한 장에 GPU 메모리가 약 3GB 더 듭니다. 각도 바꾸기는 첫 장만 씁니다."),
    preview=".thumb.jpg", preview_flag="--thumb",
    progress=progress_image,
    check=check_image,
    params=(
        Param("size", "크기", "choice", "2048x2048", "--size", choices=IMAGE_SIZES,
              help="모델이 지원하는 일곱 가지 비율과, 빠르게 볼 때 쓰는 1024 정사각입니다."),
        Param("model", "모델", "choice", "turbo", "--model",
              choices=(("turbo", "Turbo (8단계, 빠름)"), ("base", "본체 (40단계, 느림)")),
              help="Turbo 는 8단계로 그려 2048 한 장에 약 1분, 본체는 40단계로 약 5분 걸립니다 (RTX 3090 기준)."),
        _seed(),
        Param("transparent", "투명 배경", "bool", False, "--transparent",
              help="스티커나 아이콘처럼 배경이 없는 그림을 만듭니다."),
        Param("steps", "단계 수", "int", None, "--steps", lo=1, hi=100, step=1, group=G_DRAW,
              help="잡음을 걷어 내는 횟수. 비워 두면 본체는 40단계, Turbo 는 모델에 저장된 8단계 시간표를 씁니다. "
                   "Turbo 에 값을 주면 저장된 시간표 대신 고르게 내려가는 시간표를 쓰는데, 이 서버에서 8단계로 해 보니 그림이 흐려졌습니다. "
                   "Turbo 의 단계를 바꿀 때는 아래 '시간표 직접 주기'를 쓰세요."),
        Param("sigmas", "시간표 직접 주기", "schedule", None, "--sigmas", group=G_DRAW,
              help="단계마다 남길 잡음의 양을 1 에서 시작해 내려가는 수로 직접 적습니다 (예: 1,0.95,0.85,0.6). "
                   "수의 개수가 단계 수가 되고, 단계 수 칸보다 우선합니다. 앞쪽 수를 촘촘히 두면 큰 구도에, 뒤쪽을 촘촘히 두면 세부에 단계를 더 씁니다. "
                   "Turbo 에서 1,0.95,0.9,0.8,0.6,0.3 (6단계)은 8단계보다 빨리 끝나고 그림도 깨끗했습니다."),
        Param("true_cfg", "가이던스", "float", None, "--true-cfg", lo=1, hi=10, step=0.1, group=G_DRAW,
              help="글을 얼마나 세게 따를지. 비워 두면 끕니다 (모델이 권하는 값). 1 보다 크면 '피할 내용'과 비교하느라 "
                   "단계마다 두 번 계산해 시간이 더 듭니다. Turbo 에서 단계 수를 바꾸면서 함께 켜면 그림이 깨집니다 (6단계, 가이던스 3 에서 확인)."),
        Param("kv_cache", "글 계산 다시 쓰기", "bool", True, "--kv-cache", off_flag="--no-kv-cache", group=G_DRAW,
              help="글과 참고 그림을 읽은 계산을 첫 단계에서 한 번만 하고 다음 단계에서 다시 씁니다. 끄면 느려지고, 같은 시드여도 그림이 조금 달라집니다."),
        Param("tile", "조각 크기", "choice", "1024", "--tile", group=G_DECODE,
              choices=(("1024", "1024px"), ("512", "512px"), ("256", "256px"), ("0", "나누지 않기")),
              help="마지막에 그림으로 풀어낼 때 한 번에 푸는 조각의 크기. 줄이면 GPU 메모리를 덜 쓰고 이음매가 늘어납니다. "
                   "나누지 않으면 2048 크기에서 24GB 로도 메모리가 모자랍니다."),
        Param("view", "보는 방향", "choice", None, "--view", group=G_ANGLE, choices=ANGLE_VIEWS,
              help="참고 그림의 물체를 이 방향에서 다시 찍은 모습으로 그립니다 (각도 LoRA). 각도는 참고 그림에 보이는 쪽을 정면 0° 로 보고 "
                   "오른쪽으로 돈 각도입니다. 참고 그림이 있어야 하고, 그릴 내용 칸에는 '-' 만 적는 것이 가장 잘 듣습니다. "
                   "흰 배경의 물체 하나로 배운 LoRA 라 배경은 지워지고, 건물처럼 큰 장면은 방향을 덜 따릅니다. 1024×1024 를 권합니다. "
                   "어떤 시드에서는 모델이 방향을 무시하고 참고 그림을 그대로 베끼며 털·잎의 윤곽을 태웁니다. 그런 결과는 알아보고 "
                   "시드를 하나씩 올려 두 번까지 다시 그립니다 (그만큼 오래 걸리고, 기록에 바꾼 시드가 남습니다)."),
        Param("elevation", "카메라 높이", "choice", None, "--elevation", group=G_ANGLE,
              choices=(("", "눈높이 (기본)"), ("eye", "눈높이 (0°)"), ("elevated", "조금 위 (30°)"),
                       ("high", "높이 위 (60°)"), ("top", "바로 위 (90°)")),
              help="위에서 내려다보는 정도. 참고 그림이 있는데 보는 방향을 비워 두면 정면에서 본 모습으로 그립니다."),
        Param("close_up", "가까이", "bool", False, "--close-up", group=G_ANGLE,
              help="물체를 화면에 꽉 차게 가까이 찍습니다. 참고 그림이 있는데 보는 방향을 비워 두면 정면에서 본 모습으로 그립니다."),
        Param("lora_scale", "각도 LoRA 세기", "float", None, "--lora-scale", lo=0.3, hi=2, step=0.05, group=G_ANGLE,
              help="비워 두면 Turbo 는 1.6, 본체는 바로 옆(왼쪽·오른쪽) 1.2, 그 밖의 방향 1.0 (Turbo 는 1.0 에서 각도가 거의 바뀌지 않았고, "
                   "본체의 옆모습은 1.0 에서 45~60° 까지만 돌았습니다). 각도를 바꿀 때는 그릴 내용 글을 쓰지 않고 각도 글만 씁니다. "
                   "방향이 덜 돌았으면 올리고, 물체가 뭉개지면 내립니다. 참고 그림을 그대로 베끼고 윤곽이 탄 결과는 세기를 올려도 그대로였고 "
                   "(같은 시드에서 1.5), 시드를 바꾸면 고쳐졌습니다."),
    ),
)

# ── 종류: 음악 ──

MUSIC_STEPS = ("모델 올리기", "악보 쓰기", "노래 만들기", "소리로 바꾸기")
G_PLAN, G_SONG, G_AUDIO = "1. 악보 쓰기", "2. 노래 만들기", "3. 소리로 바꾸기"


def _sampling(head: str, group: str, what: str, temperature: float, top_p: float, top_k: int, repeat: float) -> tuple[Param, ...]:
    """다음 글자(토큰)를 확률로 고르는 단계의 네 가지 값. 기본값은 모델과 함께 온 설정 파일의 값이다."""
    return (
        Param(f"{head}_temperature", "고르는 폭", "float", temperature, f"--{head}-temperature", lo=0, hi=2, step=0.05, group=group,
              help=f"{what} 다음 것을 얼마나 과감하게 고를지. 낮추면 뻔하고 안정되게, 높이면 엉뚱한 것도 고릅니다. 0 이면 늘 가장 확률 높은 것만 고릅니다."),
        Param(f"{head}_top_p", "후보 확률 합", "float", top_p, f"--{head}-top-p", lo=0.05, hi=1, step=0.01, group=group,
              help="확률이 큰 후보부터 더해 이 값이 될 때까지만 후보로 둡니다. 낮추면 후보가 줄어 안정되고, 1 이면 자르지 않습니다."),
        Param(f"{head}_top_k", "후보 개수", "int", top_k, f"--{head}-top-k", lo=1, hi=1000, step=1, group=group,
              help="확률이 높은 순서로 이 개수만 후보로 둡니다. 줄이면 안정되고 단조로워집니다."),
        Param(f"{head}_repeat", "되풀이 줄이기", "float", repeat, f"--{head}-repeat", lo=0.5, hi=2, step=0.005, group=group,
              help="조금 전에 고른 것을 다시 고를 확률을 깎는 정도. 1 이면 깎지 않습니다. 키우면 같은 음형이 덜 되풀이됩니다."),
    )
MUSIC_MARKS = ("Loading model", "Planning score", "Generating song", "Synthesizing audio")


def progress_music(log_tail: str) -> dict | None:
    """YuE2 가 찍는 "[YuE2] Running <단계>: ..." 줄에서 지금 단계를 읽는다."""
    text = log_tail.replace("\r", "\n")
    best, at = None, -1
    for i, mark in enumerate(MUSIC_MARKS):
        pos = text.rfind(mark)
        if pos > at:
            best, at = i, pos
    if "Using provided score" in text and (best is None or best < 1):
        best = 1
    if "Decoding audio" in text or "Loading audio decoder" in text:
        best, at = len(MUSIC_STEPS) - 1, len(text)
    if best is None:
        return None
    found = re.findall(r"\((\d{1,3})%\)", text[max(at, 0):])
    percent = min(100, int(found[-1])) if found and best == len(MUSIC_STEPS) - 1 else None
    return {"stage": MUSIC_STEPS[best], "step": best + 1, "steps": len(MUSIC_STEPS), "percent": percent}


# 곡의 길이는 가사를 따라가고, 이 한도는 그 위를 막는 뚜껑이다.
# 토큰과 초의 비는 이 서버에서 잰 값이다 (2026-11-14: 2,285개 → 91.4초, 600개 → 24.0초).
MUSIC_LENGTH_HELP = ("소리의 설계도를 이루는 조각(토큰) 수의 한도. 곡이 이 수에 닿으면 거기서 끊깁니다. "
                     "조각 25개가 소리 1초입니다 (기본 9000개는 6분, 1500개는 1분).")


KIND_MUSIC = Kind(
    "music", "음악 생성", "genmusic_dir", "genmusic_script", "genmusic_vram_gb", ".flac",
    texts=(
        Text("style", "분위기와 장르", "--style-file", rows=2, max_chars=1000,
             placeholder="예) K-pop ballad, emotional, piano and strings, soft female vocal, slow tempo",
             help="장르, 악기, 목소리, 빠르기를 쉼표로 이어 적습니다. 영어로 적은 예가 모델 카드에 있습니다."),
        Text("lyrics", "가사", "--lyrics-file", rows=12, max_chars=6000,
             placeholder="[Verse]\n창밖에 비가 내리는 밤\n불 꺼진 방 안에 혼자 앉아\n\n[Chorus]\n돌아라 돌아라 밤새도록",
             help="[Intro], [Verse], [Chorus], [Bridge], [Outro] 같은 구획 표시를 줄 맨 앞에 두고 그 아래에 한 줄씩 적습니다. "
                  "곡의 길이는 가사의 길이를 따라갑니다."),
        Text("abc", "악보 직접 주기", "--abc-file", required=False, rows=8, max_chars=30000, group=G_PLAN, ext=".abc",
             help="ABC 표기 악보. 넣으면 모델이 악보를 새로 쓰지 않고 이 악보대로 소리를 만듭니다. "
                  "만든 곡의 악보를 가져와 빠르기(Q:)나 코드를 고쳐 넣는 식으로 씁니다."),
    ),
    preview=".mp3", preview_flag="--mp3",
    extra_flags=(("--score", ".abc"),),
    progress=progress_music,
    params=(
        Param("cot", "악보를 먼저 쓰는 방식", "choice", "full", "--cot",
              choices=(("full", "멜로디와 코드"), ("melody", "멜로디만"), ("off", "악보 없이 바로")),
              help="모델은 소리를 만들기 전에 악보를 먼저 씁니다. 멜로디만 쓰게 하면 반주를 더 자유롭게 붙이고, 끄면 조금 빨라집니다."),
        _seed(),
        *_sampling("plan", G_PLAN, "악보의", 0.7, 0.9, 30, 1.005),
        Param("plan_max_tokens", "악보 길이 한도", "int", 4096, "--plan-max-tokens", lo=64, hi=8192, step=64, group=G_PLAN,
              help="악보를 이루는 글자 조각(토큰) 수의 한도. 긴 곡의 악보가 중간에 끊기면 늘립니다."),
        Param("cfg_scale", "가이던스", "float", None, "--cfg-scale", lo=0.5, hi=5, step=0.05, group=G_SONG,
              help="분위기 설명과 가사를 얼마나 세게 따를지. 비워 두면 모델 기본값(1.0). 1 이 아니면 두 번씩 계산해 느려집니다. 실험할 때 1.2 쯤."),
        *_sampling("song", G_SONG, "소리의", 1.0, 0.95, 100, 1.2),
        Param("song_max_tokens", "곡 길이 한도", "int", 9000, "--song-max-tokens", lo=250, hi=12000, step=250, group=G_SONG,
              help=MUSIC_LENGTH_HELP),
        Param("audio_steps", "다듬는 횟수", "int", 32, "--audio-steps", lo=4, hi=100, step=1, group=G_AUDIO,
              help="소리의 설계도를 실제 파형으로 바꿀 때 다듬는 횟수. 줄이면 빨라지고 소리가 거칠어집니다."),
    ),
)

# ── 종류: 영상 ──

VIDEO_SIZES = (("auto", "자동: 첫 장면 사진의 비율, 사진이 없으면 가로 16:9"),
               ("1344x768", "가로 16:9, 1344×768"), ("768x1344", "세로 9:16, 768×1344"),
               ("1024x768", "가로 4:3, 1024×768"), ("768x1024", "세로 3:4, 768×1024"), ("768x768", "정사각 768×768"),
               ("864x480", "가로 16:9 작게, 864×480 (빠른 초안)"), ("480x864", "세로 9:16 작게, 480×864 (빠른 초안)"))
VIDEO_STEPS = ("모델 올리기", "글과 사진 읽기", "영상 만들기", "영상과 소리 풀어내기", "MP4 만들기")
G_VGEN, G_VOUT, G_VREF = "1. 영상 만들기", "2. 풀어내기와 저장", "3. 참고 자료"
H3_STAGE_RE = re.compile(r"\[h3\] 단계 (\d)/(\d)")
# sd-cli 의 단계 막대: "|=====>    |  7/20 - 4.54s/it - ETA 01:17". 모델을 읽는 막대("| 149/535 - 2.89GB/s")는 걸리지 않는다
SD_STEP_RE = re.compile(r"\|\s*(\d+)/(\d+) - +[\d.]+(?:s/it|it/s)")


JOIN_STEPS = ("영상 읽기", "이어지는 장면 찾기", "MP4 쓰기")
JOIN_STAGE_RE = re.compile(r"\[h3-join\] 단계 (\d)/3(?:[^\n%]*?(\d+)%)?")


def progress_video(log_tail: str) -> dict | None:
    """h3_run.py 가 찍는 "[h3] 단계 N/5" 줄과 sd-cli 의 진행 막대(7/20)에서 지금 단계를 읽는다.
    영상을 이어 붙이는 작업(h3_join.py)은 "[h3-join] 단계 N/3 … 40%" 줄을 찍는다."""
    text = log_tail.replace("\r", "\n")
    joined = JOIN_STAGE_RE.findall(text)
    if joined:
        step = min(max(int(joined[-1][0]), 1), len(JOIN_STEPS))
        return {"stage": JOIN_STEPS[step - 1], "step": step, "steps": len(JOIN_STEPS),
                "percent": int(joined[-1][1]) if joined[-1][1] else None}
    found = H3_STAGE_RE.findall(text)
    steps = SD_STEP_RE.findall(text)
    if found:
        step = int(found[-1][0])
    elif steps:
        step = 3                                    # 단계 줄이 로그 끝에서 밀려났다. 막대가 보이면 만드는 중이다
    else:
        return None
    step = min(max(step, 1), len(VIDEO_STEPS))
    percent = None
    if step == 3 and steps:
        k, n = (int(v) for v in steps[-1])
        percent = min(100, round(k * 100 / n)) if n else None
    return {"stage": VIDEO_STEPS[step - 1], "step": step, "steps": len(VIDEO_STEPS), "percent": percent}


KIND_VIDEO = Kind(
    "video", "영상 생성", "genvideo_dir", "genvideo_script", "genvideo_vram_gb", ".mp4",
    texts=(
        Text("prompt", "장면", "--prompt-file", rows=6, max_chars=8000,
             placeholder="예) Live-action, cinematic. A small orange cat sits on a wooden windowsill in warm afternoon light. "
                         "The camera pushes in slowly as the cat turns its head and meows softly.",
             help="화면에 보이는 것, 움직임, 카메라, 대사를 시간 순서로 적습니다. 영어가 가장 잘 듣고 한국어도 읽습니다. "
                  "대사는 <d>[Korean] 안녕하세요</d> 처럼 감싸고 말하는 사람을 (S1) 로 표시합니다. "
                  "장면을 바꾸려면 [Shot 2] At 00:03.000, the camera cuts to … 처럼 씁니다."),
        Text("sound", "주변 소리", "--sound-file", required=False, rows=2, max_chars=2000,
             placeholder="예) Soft birdsong outside the window and a quiet room tone.",
             help="바람, 빗소리, 발소리처럼 장면 안에서 나는 소리. 비워 두면 장면에 맞는 소리를 모델이 정합니다."),
        Text("music", "배경 음악", "--music-file", required=False, rows=2, max_chars=2000,
             placeholder="예) Gentle acoustic guitar at a slow tempo, fading out at the end.",
             help="등장인물은 못 듣고 보는 사람만 듣는 음악. 악기, 빠르기, 세기의 변화를 적습니다. 비워 두면 배경 음악을 넣지 않습니다."),
    ),
    upload=Upload("첫·끝 장면", "--image", 0, 2, "한 장이면 영상이 이 사진에서 시작하고, 두 장이면 두 번째 사진에서 끝납니다.",
                  slots=("첫 장면", "끝 장면")),
    refs=Refs("참고 자료",
              "넣은 그림·영상·소리를 보고 만듭니다 (Ref2VA). 장면 글에서 <Picture\u00a01>, <Video\u00a01>, <Audio\u00a01> 처럼 부릅니다. "
              "첫·끝 장면과 함께 쓸 수 없습니다. 참고 영상은 짧은 변 480 픽셀로 줄여 움직임 참고면 앞 3초, 이어 만들기면 끝 2초만, "
              "소리는 20초까지만 씁니다. 참고 자료는 GPU 메모리를 더 씁니다: 1344×768 5초에서 넘치면 크기나 길이를 줄이세요.",
              (("image", "그림", "--ref-image", 4, IMAGE_EXTS), ("video", "영상", "--ref-video", 2, VIDEO_EXTS),
               ("audio", "소리", "--ref-audio", 2, AUDIO_EXTS))),
    preview=".thumb.jpg", preview_flag="--thumb",
    extra_flags=(("--last-frame", ".last.png"),),
    progress=progress_video,
    params=(
        Param("size", "크기", "choice", "auto", "--size", choices=VIDEO_SIZES,
              help="모델은 짧은 변 768 픽셀에 맞춰 배웠습니다. 이 서버에서 Flash 로 1344×768 5초 영상은 약 6분, "
                   "864×480 4초 영상은 약 1분 30초 걸렸습니다. 작은 크기로 빨리 보고 고른 뒤 큰 크기로 다시 만드세요."),
        Param("model", "모델", "choice", "flash", "--model",
              choices=(("flash", "HyperFlow Flash (14B, 6단계, 빠름)"), ("base", "H3 본체 (33B, 20단계, 느림)")),
              help="Flash 는 H3 를 절반 크기로 줄이고 적은 단계로 만들도록 다시 배운 판입니다. 본체는 더 정교할 수 있지만 "
                   "이 서버에서 1344×768 5초 영상에 단계마다 약 54초 걸립니다."),
        Param("seconds", "길이 (초)", "float", 5.0, "--seconds", lo=2, hi=15, step=0.5,
              help="영상은 1초에 24장입니다. 모델이 받는 장 수(17의 배수 + 5)에 가장 가깝게 맞춥니다 (5초 → 5.17초). "
                   "길수록 오래 걸리고 메모리를 더 씁니다."),
        _seed(),
        Param("steps", "단계 수", "int", None, "--steps", lo=1, hi=60, step=1, group=G_VGEN,
              help="잡음을 걷어 내는 횟수. 비워 두면 Flash 는 6, 본체는 20. Flash 는 4, 6, 8 가운데 하나만 됩니다 "
                   "(4 는 빠르고 8 은 더 다듬어집니다). 늘리면 그만큼 오래 걸립니다."),
        Param("shift", "흐름 이동", "float", None, "--shift", lo=0.5, hi=30, step=0.5, group=G_VGEN,
              help="단계를 잡음이 많은 앞 구간에 얼마나 몰아 쓸지. 비워 두면 모델 값 12. 키우면 큰 움직임과 구도에, 줄이면 세부에 단계를 더 씁니다. "
                   "Flash 는 정해진 시간표를 쓰므로 이 값을 듣지 않습니다."),
        Param("sampler", "샘플러", "choice", None, "--sampler", group=G_VGEN,
              choices=(("", "모델 기본값 (Euler)"), ("euler", "Euler"), ("euler_a", "Euler a (단계마다 잡음을 새로 섞음)"),
                       ("heun", "Heun (단계마다 두 번 계산, 두 배 느림)"), ("dpm++2m", "DPM++ 2M"),
                       ("res_multistep", "RES multistep")),
              help="잡음을 걷어 내는 계산 방법. 같은 단계 수에서 결과의 결이 조금씩 다릅니다."),
        Param("sigmas", "시간표 직접 주기", "schedule", None, "--sigmas", group=G_VGEN,
              help="단계마다 남길 잡음의 양을 1 에서 시작해 내려가는 수로 직접 적습니다 (예: 1,0.9,0.75,0.5,0.25). "
                   "수의 개수가 단계 수가 되고, 단계 수와 흐름 이동 칸보다 우선합니다."),
        Param("attn_budget", "주의 계산 묶음 (GiB)", "float", None, "--attn-budget", lo=0.1, hi=8, step=0.1, group=G_VGEN,
              help="화면의 모든 조각이 서로를 보는 계산(주의)을 몇 GiB 씩 나눠 할지. 비워 두면 2. "
                   "길고 큰 영상에서 GPU 메모리가 모자라 멈추면 0.5 쯤으로 줄입니다. 줄이면 조금 느려집니다."),
        Param("vae_tiling", "공간 조각으로 풀기", "bool", False, "--vae-tiling", group=G_VOUT,
              help="마지막에 영상을 풀 때 화면을 조각으로 나눕니다. 메모리가 모자랄 때 켭니다. 조각 경계가 보일 수 있습니다."),
        Param("temporal_tiling", "시간 조각으로 풀기", "bool", False, "--temporal-tiling", group=G_VOUT,
              help="마지막에 영상을 풀 때 시간을 조각으로 나눕니다. 긴 영상에서 메모리가 모자랄 때 켭니다."),
        Param("crf", "MP4 화질", "int", 18, "--crf", lo=0, hi=51, step=1, group=G_VOUT,
              help="MP4 로 줄일 때의 화질. 낮을수록 원본에 가깝고 파일이 큽니다. 18 은 눈으로 차이를 거의 못 느끼는 값입니다."),
        Param("ref_video_role", "참고 영상의 쓰임새", "choice", None, "--ref-video-role", group=G_VREF,
              choices=(("", "움직임·카메라를 따른다 (기본)"), ("motion", "움직임·카메라를 따른다"),
                       ("continue", "그 영상의 끝에서 이어 만든다")),
              help="참고 영상을 넣었을 때만 듣습니다. '이어 만든다'는 참고 영상의 마지막 2초를 보고 그 뒤를 만듭니다 "
                   "(인물·장소·빛·움직임이 이어집니다). 그 영상의 소리도 함께 참고하고, 크기가 '자동'이면 그 영상의 크기를 따릅니다."),
        Param("ref_audio_role", "참고 소리의 쓰임새", "choice", None, "--ref-audio-role", group=G_VREF,
              choices=(("", "말하는 사람의 목소리 (기본)"), ("voice", "말하는 사람의 목소리"),
                       ("music", "배경 음악의 스타일"), ("copy", "그 소리를 그대로 쓴다")),
              help="참고 소리를 넣었을 때만 듣습니다. 목소리면 장면 글의 (S1) 이 그 목소리로 말합니다."),
    ),
)

# ── 종류: 음성 ──

VOICE_STEPS = ("모델 올리기", "말하기", "파일 쓰기")
G_VSAMPLE, G_VRUN = "1. 말하기", "2. 메모리와 속도"
DRAMA_STAGE_RE = re.compile(r"\[drama3\] 단계 (\d)/(\d)")
DRAMA_PIECE_RE = re.compile(r"\[drama3\] 글 조각 (\d+) 읽는 중")


def progress_voice(log_tail: str) -> dict | None:
    """drama3_run.py 가 찍는 "[drama3] 단계 N/3" 줄과 "글 조각 k 읽는 중" 줄에서 지금 단계를 읽는다."""
    text = log_tail.replace("\r", "\n")
    found = DRAMA_STAGE_RE.findall(text)
    pieces = DRAMA_PIECE_RE.findall(text)
    if found:
        step = int(found[-1][0])
    elif pieces:
        step = 2
    else:
        return None
    step = min(max(step, 1), len(VOICE_STEPS))
    stage = VOICE_STEPS[step - 1]
    if step == 2 and pieces:
        stage = f"말하기, 글 조각 {pieces[-1]}"
    return {"stage": stage, "step": step, "steps": len(VOICE_STEPS), "percent": None}


def check_voice(words: dict, n_files: int, values: dict) -> None:
    """참고 목소리 파일 수와 '참고 목소리의 말' 줄 수가 맞는지 본다. 모델은 목소리와 그 말을 짝지어 읽는다."""
    lines = [x for x in (words.get("ref_text") or "").splitlines() if x.strip()]
    if n_files and len(lines) != n_files:
        raise GenError(f"참고 목소리 {n_files}개에 '참고 목소리의 말'이 {len(lines)}줄입니다. "
                       "목소리마다 그 녹음에서 하는 말을 한 줄씩, 올린 순서대로 적어 주세요.")
    if lines and not n_files:
        raise GenError("'참고 목소리의 말'을 적었는데 참고 목소리 파일이 없습니다. 파일을 올리거나 그 칸을 비워 주세요.")


VOICE_PARAMS = (
    _seed(),
    Param("temperature", "고르는 폭", "float", 1.0, "--temperature", lo=0.1, hi=2, step=0.05, group=G_VSAMPLE,
          help="다음 소리 조각을 얼마나 과감하게 고를지. 낮추면 차분하고 단조롭게, 높이면 감정이 크고 엉뚱한 소리도 냅니다."),
    Param("top_p", "후보 확률 합", "float", 0.9, "--top-p", lo=0.05, hi=1, step=0.01, group=G_VSAMPLE,
          help="확률이 큰 후보부터 더해 이 값이 될 때까지만 후보로 둡니다. 낮추면 또박또박해지고 표현이 줄어듭니다."),
    Param("top_k", "후보 개수", "int", None, "--top-k", lo=1, hi=5000, step=1, group=G_VSAMPLE,
          help="확률이 높은 순서로 이 개수만 후보로 둡니다. 비워 두면 자르지 않습니다(모델 기본). "
               "30 처럼 작게 주면 긴 글에서 소리 없이 멈추는 구간이 생긴다고 알려져 있습니다."),
    Param("max_tokens", "조각 길이 한도", "int", 1024, "--max-tokens", lo=64, hi=4096, step=64, group=G_VSAMPLE,
          help="글 조각 하나에서 만들 소리의 한도. 21.5개가 1초입니다 (1024 는 약 48초). 끝이 잘리면 늘립니다."),
    Param("chunk_size", "조각 크기 (글자)", "int", None, "--chunk-size", lo=20, hi=1000, step=10, group=G_VSAMPLE,
          help="긴 대본을 이만큼씩 나눠 읽습니다. 비워 두면 모델 기본값. 조각 경계에서 말이 되풀이되면 늘리고, 끝이 잘리면 줄입니다."),
    Param("context", "기억 길이", "int", 4096, "--context", lo=1024, hi=16384, step=512, group=G_VRUN,
          help="모델이 한 번에 기억하는 길이 (참고 목소리 + 글 + 만든 소리). 참고 목소리가 길거나 많으면 늘립니다. 늘리면 GPU 메모리를 더 씁니다."),
    Param("cpu_layers", "CPU 에서 계산할 층", "int", 16, "--cpu-layers", lo=16, hi=40, step=1, group=G_VRUN,
          help="모델의 전문가 층 40개 가운데 CPU 메모리에 두고 CPU 로 계산할 수. 늘리면 GPU 메모리를 덜 쓰고 조금 느려집니다. "
               "이 서버에서 16층은 GPU 16.5 GB, 24층은 13 GB 를 썼고 속도 차이는 15% 쯤이었습니다."),
    Param("fast", "CUDA 빠른 경로", "bool", False, "--fast", group=G_VRUN,
          help="여러 계산을 묶어 한 번에 하는 길을 씁니다. 빨라질 수 있고, 같은 시드여도 결과가 조금 달라집니다."),
)

KIND_VOICE = Kind(
    "voice", "음성 생성", "genvoice_dir", "genvoice_script", "genvoice_vram_gb", ".wav",
    texts=(
        Text("script", "대본", "--text-file", rows=8, max_chars=6000,
             placeholder="화자1: 오늘 회의는 여기까지 하죠. 다들 수고 많으셨어요.\n화자2: [laughs] 네, 다음 주에는 꼭 끝내 봅시다!",
             help="한 사람이면 그냥 적습니다. 여러 사람이면 줄 맨 앞에 '화자1:', '화자2:' 를 붙입니다 (16명까지). "
                  "[laughs], [sighs], [whispers] 처럼 대괄호로 웃음·한숨·속삭임을 넣을 수 있습니다."),
        Text("ref_text", "참고 목소리의 말", "--ref-text-file", required=False, rows=3, max_chars=3000,
             help="올린 참고 목소리 녹음에서 하는 말을 한 줄에 하나씩, 올린 순서대로 적습니다. 목소리를 본뜨려면 꼭 필요합니다."),
    ),
    upload=Upload("참고 목소리", "--ref", 0, 4,
                  "3~15초쯤의 깨끗한 녹음 한 사람분. 올린 순서대로 화자1, 화자2 … 의 목소리가 됩니다. "
                  "본인이 동의하지 않은 실제 사람의 목소리는 쓰지 않습니다 (모델 라이선스).",
                  exts=AUDIO_EXTS),
    preview=".mp3", preview_flag="--mp3",
    progress=progress_voice,
    check=check_voice,
    params=VOICE_PARAMS,
)

# ── 종류: 효과음 ──

SFX_STEPS = ("모델 올리기", "글과 영상 읽기", "소리 만들기", "다듬어 저장하기")
G_SGEN, G_SFIT, G_SVID = "1. 소리 만들기", "2. 게임에 쓰기 좋게 다듬기", "3. 영상에 소리 붙이기"
SFX_TEXT_SECONDS = 5.0          # 글로 만드는 모델(Woosh-Flow)이 배운 길이
SFX_VIDEO_SECONDS = 8.0         # 영상으로 만드는 모델(Woosh-VFlow-8s)이 배운 길이
WOOSH_STAGE_RE = re.compile(r"\[woosh\] 단계 (\d)/(\d)")
WOOSH_STEP_RE = re.compile(r"\[woosh\] 소리 만들기 (\d+)/(\d+)")
WOOSH_CALLS_RE = re.compile(r"\[woosh\] 계산 (\d+)번")
HANGUL_RE = re.compile(r"[가-힣ㄱ-ㆎ]")


def progress_sfx(log_tail: str) -> dict | None:
    """woosh_run.py 가 찍는 "[woosh] 단계 N/4" 줄과, 빠른 판의 "소리 만들기 k/n" 줄, 본판의 "계산 k번" 줄에서 지금 단계를 읽는다."""
    text = log_tail.replace("\r", "\n")
    found = WOOSH_STAGE_RE.findall(text)
    if not found:
        return None
    step = min(max(int(found[-1][0]), 1), len(SFX_STEPS))
    stage, percent = SFX_STEPS[step - 1], None
    if step == 3:
        steps, calls = WOOSH_STEP_RE.findall(text), WOOSH_CALLS_RE.findall(text)
        if steps:
            k, n = (int(v) for v in steps[-1])
            percent = min(100, round(k * 100 / n)) if n else None
        elif calls:
            stage = f"소리 만들기, 계산 {calls[-1]}번"     # 본판은 계산 횟수를 스스로 정해 전체를 미리 모른다
    return {"stage": stage, "step": step, "steps": len(SFX_STEPS), "percent": percent}


def check_sfx(words: dict, n_files: int, values: dict) -> None:
    """글만으로 만들 때는 소리 설명이 있어야 하고 5초까지다. 설명은 영어여야 한다 (모델이 영어 설명으로만 배웠다)."""
    if not words.get("prompt") and not n_files:
        raise GenError("소리 설명을 적거나 영상을 넣어 주세요. 글만으로 만들 때는 소리 설명이 있어야 합니다. "
                       "예) footsteps on dry leaves in a quiet forest")
    for key, label in (("prompt", "소리 설명"), ("negative", "피할 소리")):
        found = HANGUL_RE.findall(words.get(key) or "")
        if found:
            raise GenError(f"'{label}'에 한글이 {len(found)}자 있습니다. Woosh 는 영어 설명으로만 배워서 한글을 알아듣지 못합니다. "
                           "영어로 적어 주세요. 예) 나뭇잎 밟는 소리 → footsteps on dry leaves")
    seconds = values.get("seconds")
    if not n_files and seconds is not None and seconds > SFX_TEXT_SECONDS:
        raise GenError(f"글만으로 만드는 소리는 {SFX_TEXT_SECONDS:g}초까지입니다 (받은 값 {seconds:g}초). "
                       f"모델이 5초 조각으로 배웠습니다. 길이를 줄이거나, 긴 배경 소리는 '되풀이 소리로 만들기'를 켜고 게임에서 되풀이해 트세요.")


SFX_PARAMS = (
    Param("model", "모델", "choice", "fast", "--model",
          choices=(("fast", "빠른 판 (4단계)"), ("full", "본판 (약 70번 계산, 느림)")),
          help="빠른 판은 본판을 4단계로 만들도록 다시 배운 판(증류)입니다. Sony 는 품질 차이가 거의 없다고 밝혔습니다. "
               "본판은 계산 횟수를 스스로 정하며 더 오래 걸리고, '피할 소리'를 듣습니다."),
    Param("seconds", "길이 (초)", "float", None, "--seconds", lo=0.1, hi=SFX_VIDEO_SECONDS, step=0.1,
          help="비워 두면 글만일 때 5초, 영상을 넣으면 영상 길이(8초까지). 모델은 늘 5초(영상은 8초)를 만들고 앞에서부터 이 길이만 남깁니다."),
    _seed(),
    Param("loop", "되풀이 소리로 만들기", "bool", False, "--loop",
          help="끝과 처음을 겹쳐, 게임에서 되풀이해 틀어도 이음매가 들리지 않게 만듭니다. 바람, 비, 모닥불처럼 고르게 이어지는 배경 소리에 씁니다. "
               "길이는 겹친 만큼 줄어듭니다."),
    Param("cfg", "가이던스", "float", None, "--cfg", lo=0, hi=15, step=0.1, group=G_SGEN,
          help="소리 설명을 얼마나 세게 따를지. 비워 두면 4.5 (영상 빠른 판은 3). 키우면 설명에 더 붙고, 너무 키우면 소리가 거칠어집니다. "
               "Sony 는 논문에서 7 로 평가했습니다."),
    Param("steps", "단계 수", "int", 4, "--steps", lo=1, hi=16, step=1, group=G_SGEN,
          help="빠른 판만 듣습니다. 잡음을 걷어 내는 횟수. 모델이 4단계로 배웠습니다."),
    Param("solver", "풀이법", "choice", None, "--solver", group=G_SGEN,
          choices=(("", "기본 (dopri5)"), ("dopri5", "dopri5"), ("dopri8", "dopri8 (더 정밀, 느림)"),
                   ("bosh3", "bosh3 (덜 정밀, 빠름)"), ("adaptive_heun", "adaptive Heun")),
          help="본판만 듣습니다. 잡음에서 소리로 가는 길을 계산하는 방법입니다."),
    Param("tolerance", "허용 오차", "float", None, "--tolerance", lo=0.00001, hi=0.1, step=0.0001, group=G_SGEN,
          help="본판만 듣습니다. 비워 두면 0.001. 줄이면 계산 횟수가 늘어 더 오래 걸리고, 키우면 빨라지고 거칠어집니다."),
    Param("trim_silence", "앞뒤 무음 자르기 (dB)", "float", None, "--trim-silence", lo=20, hi=80, step=1, group=G_SFIT,
          help="가장 큰 곳보다 이만큼 이상 작은 앞뒤 구간을 잘라 냅니다. 비워 두면 자르지 않습니다. 발소리·문소리처럼 한 번 나는 소리에 50 이 무난합니다."),
    Param("fade_in", "처음 키우기 (ms)", "int", None, "--fade-in", lo=0, hi=2000, step=5, group=G_SFIT,
          help="처음을 이만큼 동안 0 에서 키우며 시작합니다. 비워 두면 그대로 둡니다. 되풀이 소리에서는 듣지 않습니다."),
    Param("fade_out", "끝 줄이기 (ms)", "int", 20, "--fade-out", lo=0, hi=2000, step=5, group=G_SFIT,
          help="끝을 이만큼 동안 0 까지 줄이며 끝냅니다. 소리를 중간에서 잘랐을 때 끝에서 '틱' 하는 잡음을 막습니다. 되풀이 소리에서는 듣지 않습니다."),
    Param("peak", "소리 크기 맞추기 (dBFS)", "float", None, "--peak", lo=-30, hi=0, step=0.5, group=G_SFIT,
          help="가장 큰 곳이 이 크기가 되게 키우거나 줄입니다. 비워 두면 모델이 낸 크기 그대로. 여러 효과음의 크기를 맞출 때 -1 이 무난합니다."),
    Param("loop_crossfade", "되풀이 겹침 (초)", "float", 0.5, "--loop-crossfade", lo=0.05, hi=2, step=0.05, group=G_SFIT,
          help="'되풀이 소리로 만들기'를 켰을 때만 듣습니다. 끝과 처음을 겹치는 길이. 늘리면 이음매가 더 부드럽고 소리가 더 짧아집니다."),
    Param("video_start", "영상 시작 위치 (초)", "float", None, "--video-start", lo=0, hi=3600, step=0.5, group=G_SVID,
          help="영상을 넣었을 때만 듣습니다. 영상의 이 시각부터 8초까지에 맞춰 소리를 만듭니다. 비워 두면 처음부터."),
)

KIND_SFX = Kind(
    "sfx", "효과음 생성", "gensfx_dir", "gensfx_script", "gensfx_vram_gb", ".wav",
    texts=(
        Text("prompt", "소리 설명 (영어)", "--prompt-file", required=False, rows=3, max_chars=600,
             placeholder="예) footsteps on dry leaves in a quiet forest, close up",
             help="들릴 소리를 영어로 짧게 적습니다. 무엇이, 어디서, 어떻게 나는지(가까이·멀리, 실내·실외)를 적으면 잘 듣습니다. "
                  "모델은 앞 77토큰(영어 단어 약 50개)까지만 읽습니다. 영상을 넣으면 비워도 됩니다."),
        Text("negative", "피할 소리", "--negative-file", required=False, rows=2, max_chars=300, group=G_SGEN,
             placeholder="예) music, speech",
             help="나오지 않았으면 하는 소리. 본판에서만 듣습니다 (빠른 판은 가이던스가 모델 안에 들어 있습니다)."),
    ),
    upload=Upload("영상", "--video", 0, 1,
                  "넣으면 영상의 움직임에 맞춰 소리를 만들고(8초까지), 그 소리를 붙인 MP4 도 만듭니다. 영상에 원래 있던 소리는 쓰지 않습니다.",
                  exts=VIDEO_EXTS),
    preview=".mp3", preview_flag="--mp3",
    extra_flags=(("--video-out", ".mp4"), ("--thumb", ".jpg")),
    progress=progress_sfx,
    check=check_sfx,
    params=SFX_PARAMS,
)

# ── 종류: 소리 다듬기 ──

POLISH_STEPS = ("소리 읽기", "목소리와 반주 나누기", "목소리 다듬기", "다시 섞기", "마무리와 저장")
G_PSEP, G_PVOC, G_PMIX, G_PMAS = "1. 목소리와 반주 나누기", "2. 목소리 다듬기", "3. 다시 섞기 (잔향·메아리)", "4. 마무리 (음량)"
POLISH_STAGE_RE = re.compile(r"\[polish\] 단계 (\d)/(\d)")
POLISH_CPU_VRAM_GB = 1.0        # 나누지 않으면 GPU 를 쓰지 않는다. 큐는 0 을 받지 않으므로 조금만 잡는다
POLISH_SOURCES = (("song", "노래 (목소리 + 반주)"), ("voice", "목소리·말소리만"), ("sfx", "효과음"), ("mix", "곡 전체만 (나누지 않음)"))


def progress_polish(log_tail: str) -> dict | None:
    """polish_run.py 가 찍는 "[polish] 단계 N/5" 줄과, 나누기 모델(Demucs)의 진행 막대(45%|)에서 지금 단계를 읽는다."""
    text = log_tail.replace("\r", "\n")
    found = POLISH_STAGE_RE.findall(text)
    if not found:
        return None
    step = min(max(int(found[-1][0]), 1), len(POLISH_STEPS))
    percent = None
    if step == 2:
        bars = BARE_PERCENT_RE.findall(text)
        percent = min(100, int(bars[-1])) if bars else None
    return {"stage": POLISH_STEPS[step - 1], "step": step, "steps": len(POLISH_STEPS), "percent": percent}


def _by_source(what: str, song, voice, sfx, mix, unit: str) -> str:
    return f"비워 두면 넣은 것에 맞춘 값: 노래 {song}{unit}, 목소리 {voice}{unit}, 효과음 {sfx}{unit}, 곡 전체 {mix}{unit}. {what}"


POLISH_PARAMS = (
    Param("source", "넣은 것", "choice", "song", "--source", choices=POLISH_SOURCES,
          help="노래면 목소리와 반주를 나눠 목소리에만 다듬기를 걸고 다시 섞습니다. 영상의 소리도 '노래'로 두면 대사와 배경 소리로 나눕니다. "
               "목소리만 든 소리(음성 생성 결과)는 나누지 않고 바로 다듬습니다. 나누기가 소리를 망칠 때는 '곡 전체만'으로 가볍게 마무리만 합니다."),
    Param("loudness", "마지막 음량 (LUFS)", "float", None, "--loudness", lo=-30, hi=-8, step=0.5,
          help=_by_source("귀가 듣는 크기 단위입니다. 음원 사이트는 −14, 말소리는 −16, 게임 효과음은 −18 쯤에 맞춥니다. 작게(더 음수로) 하면 조용해집니다.",
                          -14, -16, -18, -14, "")),
    Param("separator", "나누기 모델", "choice", "htdemucs", "--separator", group=G_PSEP,
          choices=(("htdemucs", "빠른 판 (htdemucs)"), ("htdemucs_ft", "정밀한 판 (htdemucs_ft, 약 4배 느림)")),
          help="'노래'에서만 씁니다. 정밀한 판은 같은 모델을 네 갈래로 다시 배운 것을 모두 돌려, 목소리에 섞여 드는 반주가 조금 줄어듭니다."),
    Param("shifts", "나누기 반복", "int", 1, "--shifts", lo=0, hi=5, step=1, group=G_PSEP,
          help="소리를 조금씩 어긋나게 여러 번 나눠 평균 냅니다. 늘리면 조금 깨끗해지고 그 수만큼 느려집니다. 0 이면 한 번만."),
    Param("highpass", "낮은 웅웅거림 자르기 (Hz)", "float", None, "--highpass", lo=0, hi=300, step=5, group=G_PVOC,
          help=_by_source("이 주파수 아래를 자릅니다. 목소리에는 쓸모없는 바람·진동 소리입니다. 0 이면 끕니다.", 90, 80, 30, 25, " Hz")),
    Param("resonance", "거슬리는 울림 깎기 (dB)", "float", None, "--resonance", lo=0, hi=8, step=0.5, group=G_PVOC,
          help=_by_source("2~4 kHz(코맹맹이)와 6~9 kHz(유리 같은 소리)에서 주변보다 튀어나온 봉우리를 찾아 그만큼, 많아야 이 값까지 깎습니다. 0 이면 끕니다.",
                          4, 3, 0, 0, " dB")),
    Param("deess", "치찰음 줄이기 (dB)", "float", None, "--deess", lo=0, hi=12, step=0.5, group=G_PVOC,
          help=_by_source("'ㅅ·ㅊ·ㅆ' 처럼 쉿 하는 소리가 튈 때만 그 띠를 줄이는 최대 양. 압축 앞뒤에서 반씩 나눠 겁니다. 너무 키우면 혀 짧은 소리가 납니다. 0 이면 끕니다.",
                          6, 5, 0, 0, " dB")),
    Param("deess_band", "치찰음 띠", "choice", "auto", "--deess-band", group=G_PVOC,
          choices=(("auto", "재서 찾기"), ("bright", "밝은 목소리 6~8 kHz"), ("dark", "어두운 목소리 5~7 kHz")),
          help="'재서 찾기'는 쉿 소리가 가장 센 주파수를 찾아 그 둘레 2 kHz 를 씁니다."),
    Param("comp", "압축 (dB)", "float", None, "--comp", lo=0, hi=10, step=0.5, group=G_PVOC,
          help=_by_source("큰 소리와 작은 소리의 차이를 줄입니다. 압축기 두 대가 큰 쪽에서 각각 이만큼 줄입니다(봉우리용, 흐름용). 키우면 고르고 납작해집니다. 0 이면 끕니다.",
                          3, 3, 2, 0, " dB")),
    Param("parallel", "세게 누른 사본 섞기", "float", None, "--parallel", lo=0, hi=1, step=0.05, group=G_PVOC,
          help=_by_source("세게 누른 사본을 원래 소리 밑에 이만큼 섞어 숨소리·끝음을 살립니다 (0~1). 0 이면 끕니다.", 0.15, 0.1, 0, 0, "")),
    Param("warmth", "따뜻하게", "float", None, "--warmth", lo=0, hi=1, step=0.05, group=G_PVOC,
          help=_by_source("소리를 둥글게 눌러 배음을 더합니다 (0~1). 많이 하면 찌그러집니다. 0 이면 끕니다.", 0.2, 0.15, 0, 0, "")),
    Param("warmth_style", "따뜻함의 종류", "choice", "tape", "--warmth-style", group=G_PVOC,
          choices=(("tape", "테이프 (부드럽게)"), ("tube", "진공관 (두툼하게)")),
          help="테이프는 높은 소리를 살짝 누르고, 진공관은 짝수 배음을 더해 두툼하게 만듭니다."),
    Param("warmth_band", "따뜻하게 할 곳", "choice", "lowmid", "--warmth-band", group=G_PVOC,
          choices=(("lowmid", "200~800 Hz 만"), ("full", "전체")),
          help="200~800 Hz 만 데우면 높은 소리는 깨끗하게 남습니다."),
    Param("air", "위쪽 광택 (dB)", "float", None, "--air", lo=0, hi=4, step=0.5, group=G_PVOC,
          help=_by_source("10 kHz 위를 이만큼 올립니다. 많이 올리면 잡음까지 커집니다. 0 이면 끕니다.", 1, 1, 0, 0.5, " dB")),
    Param("vocal_level", "목소리 크기 (dB)", "float", 0.0, "--vocal-level", lo=-12, hi=12, step=0.5, group=G_PMIX,
          help="'노래'에서 다시 섞을 때 목소리를 올리거나(+) 내립니다(−). 0 이면 원래 곡의 크기 차이 그대로."),
    Param("reverb", "잔향 크기", "float", None, "--reverb", lo=0, hi=0.6, step=0.01, group=G_PMIX,
          help=_by_source("목소리 뒤에 남는 울림을 목소리 크기에 견준 몫으로 정합니다 (0.12 면 약 −18 dB). 0 이면 끕니다.", 0.12, 0, 0, 0, "")),
    Param("reverb_seconds", "잔향 길이 (초)", "float", 1.6, "--reverb-seconds", lo=0.2, hi=6, step=0.1, group=G_PMIX,
          help="울림이 60 dB 줄어드는 데 걸리는 시간. 작은 방은 0.5초, 큰 홀은 2초 넘게."),
    Param("predelay", "잔향 늦추기 (ms)", "float", 30.0, "--predelay", lo=0, hi=150, step=5, group=G_PMIX,
          help="잔향이 이만큼 늦게 시작해 말이 먼저 또렷이 들립니다. 20~40 ms 가 무난합니다."),
    Param("delay", "메아리 크기", "float", None, "--delay", lo=0, hi=0.5, step=0.01, group=G_PMIX,
          help=_by_source("곡 빠르기에 맞춰 되울리는 소리를 목소리에 견준 몫으로 정합니다. 빠르기를 모르면(악보도 빠르기 값도 없으면) 넣지 않습니다. 0 이면 끕니다.",
                          0.06, 0, 0, 0, "")),
    Param("delay_note", "메아리 간격", "choice", "1/8", "--delay-note", group=G_PMIX,
          choices=(("1/4", "4분음표"), ("1/8", "8분음표"), ("1/8.", "점8분음표"), ("1/16", "16분음표"))),
    Param("tempo", "곡 빠르기 (BPM)", "float", None, "--tempo", lo=20, hi=400, step=1, group=G_PMIX,
          help="1분에 4분음표 몇 개인지. 비워 두면 악보의 빠르기 줄(Q:)을 읽습니다."),
    Param("duck", "목소리 나올 때 반주 낮추기 (dB)", "float", None, "--duck", lo=0, hi=6, step=0.5, group=G_PMIX,
          help=_by_source("목소리가 나오는 동안만 반주를 이만큼 낮춰 가사가 묻히지 않게 합니다. 0 이면 끕니다.", 1.5, 0, 0, 0, " dB")),
    Param("carve", "반주에 목소리 자리 비우기 (dB)", "float", None, "--carve", lo=0, hi=6, step=0.5, group=G_PMIX,
          help=_by_source("반주의 3 kHz 둘레를 이만큼 낮춰 목소리가 또렷이 들릴 자리를 만듭니다. 0 이면 끕니다.", 1.5, 0, 0, 0, " dB")),
    Param("glue", "전체 가볍게 누르기 (dB)", "float", None, "--glue", lo=0, hi=6, step=0.5, group=G_PMAS,
          help=_by_source("섞은 소리 전체를 큰 쪽에서 이만큼 눌러 한 덩어리로 들리게 합니다. 0 이면 끕니다.", 1.5, 0, 0, 1.5, " dB")),
    Param("ceiling", "가장 큰 곳의 천장 (dBTP)", "float", None, "--ceiling", lo=-6, hi=0, step=0.1, group=G_PMAS,
          help="표본 사이의 봉우리까지 재서 이 값을 넘지 않게 합니다. 비워 두면 −1. mp3 로 바꿀 때 찌그러지지 않게 −1 쯤 둡니다."),
    Param("keep_loudness", "음량은 그대로 두기", "bool", False, "--keep-loudness", group=G_PMAS,
          help="켜면 음량을 맞추지 않고 천장만 지킵니다. 여러 소리의 원래 크기 차이를 살릴 때 씁니다."),
    Param("fade_in", "처음 키우기 (ms)", "int", None, "--fade-in", lo=0, hi=10000, step=10, group=G_PMAS,
          help="처음을 이만큼 동안 0 에서 키우며 시작합니다. 비워 두면 그대로."),
    Param("fade_out", "끝 줄이기 (ms)", "int", None, "--fade-out", lo=0, hi=10000, step=10, group=G_PMAS,
          help="끝을 이만큼 동안 0 까지 줄이며 끝냅니다. 곡이 뚝 끊길 때 2000~4000 이 무난합니다."),
)

KIND_POLISH = Kind(
    "polish", "소리 다듬기", "genpolish_dir", "genpolish_script", "genpolish_vram_gb", ".flac",
    texts=(
        Text("score", "곡의 악보 (빠르기 읽기)", "--score-file", required=False, rows=4, max_chars=30000, group=G_PMIX, ext=".abc",
             help="음악 생성이 쓴 ABC 악보를 넣으면 빠르기 줄(Q:)을 읽어 메아리를 박자에 맞춥니다. '음악 생성'에서 '소리 다듬기로 보내기'를 누르면 저절로 채워집니다."),
    ),
    upload=Upload("다듬을 소리나 영상", "--input", 1, 1,
                  "만든 곡, 목소리, 효과음, 또는 소리가 든 영상. 영상을 넣으면 다듬은 소리로 바꾼 영상도 만듭니다.",
                  exts=MEDIA_EXTS),
    preview=".mp3", preview_flag="--mp3",
    extra_flags=(("--video-out", ".mp4"), ("--vocals-out", ".vocals.flac"), ("--backing-out", ".backing.flac"),
                 ("--report", ".report.json")),
    progress=progress_polish,
    params=POLISH_PARAMS,
    vram_for=lambda v: None if v.get("source") == "song" else POLISH_CPU_VRAM_GB,
)

KINDS: dict[str, Kind] = {k.key: k for k in (KIND_3D, KIND_IMAGE, KIND_MUSIC, KIND_VIDEO, KIND_VOICE, KIND_SFX, KIND_POLISH,
                                               KIND_MV, KIND_SHAPE)}


def get_kind(key: str) -> Kind:
    if key not in KINDS:
        raise GenError(f"없는 종류입니다 (받은 값 {key!r}). {', '.join(KINDS)} 가운데 하나여야 합니다.")
    return KINDS[key]


# ── 공통 ──

def _entries(settings: Settings, user: dict, rel: str) -> dict[str, dict] | None:
    """홈 기준 폴더의 항목 (이름 → 정보). 폴더가 없거나 열 수 없으면 None."""
    root, _ = fs.area_root(settings, user, "home")
    try:
        return {e["name"]: e for e in fs.list_dir(root, rel)["entries"]}
    except (fs.FileError, OSError):
        return None


def ready(settings: Settings, user: dict, kind: Kind) -> bool:
    """이 사용자의 홈에 이 종류의 실행 스크립트가 있는지."""
    entry = (_entries(settings, user, kind.folder(settings)) or {}).get(kind.script(settings))
    return entry is not None and not entry["is_dir"]


def setup_hint(settings: Settings, kind: Kind) -> str:
    d, s = kind.folder(settings), kind.script(settings)
    return (f"홈에 {d}/{s} 가 없습니다. 이 스크립트가 모델을 돌려 결과 파일을 씁니다. "
            f"워크스페이스에서 ~/{d}/{s} 를 만든 뒤 다시 여세요.")


def _file_ext(filename: str, exts: tuple[str, ...] = IMAGE_EXTS) -> str:
    """올린 파일 이름의 소문자 확장자. 받는 종류(사진 또는 소리)가 아니면 GenError."""
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    _, dot, ext = base.rpartition(".")
    ext = (dot + ext).lower() if dot else ""
    if ext not in exts:
        if exts == VIDEO_EXTS:
            raise GenError(f"영상 파일이 아닙니다 (받은 이름 {base!r}). mp4, mov, webm, mkv 파일을 올리세요.")
        if exts == MEDIA_EXTS:
            raise GenError(f"소리나 영상 파일이 아닙니다 (받은 이름 {base!r}). 소리(wav, mp3, flac, ogg, m4a)나 "
                           f"영상(mp4, mov, webm, mkv) 파일을 올리세요.")
        if set(VIDEO_EXTS) <= set(exts):
            raise GenError(f"참고 자료로 받지 않는 파일입니다 (받은 이름 {base!r}). 그림(png, jpg, webp), "
                           f"영상(mp4, mov, webm, mkv), 소리(wav, mp3, flac, ogg, m4a)를 올리세요.")
        if exts == AUDIO_EXTS:
            raise GenError(f"소리 파일이 아닙니다 (받은 이름 {base!r}). wav, mp3, flac, ogg, m4a 파일을 올리세요.")
        raise GenError(f"사진 파일이 아닙니다 (받은 이름 {base!r}). png, jpg, webp 파일을 올리세요.")
    return ext


def _image_ext(filename: str) -> str:
    return _file_ext(filename, IMAGE_EXTS)


def new_key(now: float) -> str:
    """파일 이름의 몸통. 날짜·시각과 무작위 글자뿐이라 무엇을 만들었는지 드러나지 않는다."""
    return f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{secrets.token_hex(3)}"


def submit(db: DB, sched: Scheduler, settings: Settings, user: dict, kind_key: str, *,
           files: list[tuple[str, BinaryIO]] | None = None, texts: dict | None = None, params: dict | None = None,
           thumb: BinaryIO | None = None, roles: list | None = None, after: int | None = None,
           refs: list[tuple[str, BinaryIO]] | None = None) -> dict:
    """after: 앞 영상의 끝 장면에서 이어 만드는 영상이면 그 앞 영상의 번호. 나중에 차례대로 이어 붙일 때 쓴다."""
    kind = get_kind(kind_key)
    if not ready(settings, user, kind):
        raise GenError(setup_hint(settings, kind))
    if not isinstance(params or {}, dict) or not isinstance(texts or {}, dict):
        raise GenError("설정과 글은 이름과 값의 묶음(JSON 객체)이어야 합니다")
    files = files or []
    values = kind.clean_params(params or {})
    unknown = sorted(set(texts or {}) - {t.key for t in kind.texts})
    if unknown:
        raise GenError(f"모르는 글 칸입니다: {', '.join(unknown)}")
    words = {t.key: t.clean((texts or {}).get(t.key)) for t in kind.texts}
    lo, hi = (kind.upload.lo, kind.upload.hi) if kind.upload else (0, 0)
    if not lo <= len(files) <= hi:
        unit = "개" if kind.upload and (kind.upload.audio or kind.upload.video or kind.upload.media) else "장"
        want = f"{lo}{unit}" if lo == hi else f"{lo}~{hi}{unit}"
        raise GenError(f"{kind.upload.label if kind.upload else '파일'}은 {want}이어야 합니다 (받은 수 {len(files)}{unit})")
    if roles not in (None, [], "") and not (kind.upload and kind.upload.roles):
        raise GenError(f"이 종류는 {kind.upload.label if kind.upload else '파일'}마다 방향을 받지 않습니다")
    picked = kind.upload.clean_roles(roles, len(files)) if kind.upload else []
    exts = [_file_ext(f, kind.upload.exts) for f, _ in files] if kind.upload else []
    if kind.check:
        kind.check(words, len(files), values)
    refs = refs or []
    ref_kinds: list[tuple[str, str]] = []               # (종류, 확장자) 올린 차례대로
    if refs:
        if not kind.refs:
            raise GenError(f"{kind.title}은 참고 자료를 받지 않습니다")
        if kind.refs.exclusive and files:
            raise GenError(f"{kind.upload.label if kind.upload else '파일'}과 {kind.refs.label}는 함께 쓸 수 없습니다 "
                           f"(모델이 둘을 함께 받지 않습니다). 첫 장면 사진은 참고 그림으로 넣고 장면 글에 "
                           f"'the shot begins from <Picture 1>' 처럼 쓰세요.")
        for f, _ in refs:
            ext = _file_ext(f, kind.refs.exts)
            ref_kinds.append((kind.refs.kind_of(ext), ext))
        for k, label, _, hi, _ in kind.refs.kinds:
            n = sum(1 for x, _ in ref_kinds if x == k)
            if n > hi:
                raise GenError(f"{kind.refs.label} 가운데 {label}은 {hi}개까지입니다 (받은 수 {n}개)")
    if after is not None:
        if kind.key != "video":
            raise GenError("앞 영상에서 이어 만들기는 영상에만 있습니다")
        if db.one("SELECT id FROM gens WHERE id=? AND user=? AND kind='video'", (after, user["name"])) is None:
            raise GenError(f"이어 만들 앞 영상을 찾지 못했습니다 (#{after}). 목록에서 지웠으면 첫 장면 사진만 넣고 만드세요.")

    now = time.time()
    key = new_key(now)
    d = kind.folder(settings)
    uploads: list[tuple[str, BinaryIO]] = []
    args: list[str] = []
    for t in kind.texts:
        if words[t.key]:
            name = f"{key}.{t.key}{t.ext}"
            uploads.append((name, io.BytesIO(words[t.key].encode("utf-8"))))
            args.append(f"{t.flag} inputs/{name}")
    for i, ((_, src), ext) in enumerate(zip(files, exts), 1):
        name = f"{key}-{i}{ext}"
        uploads.append((name, src))
        args.append(f"{kind.upload.flag} inputs/{name}")
    if len(picked) > 1:                                 # 한 장이면 방향을 넘기지 않는다 (한 장의 길로 돈다)
        args.append(f"{kind.upload.roles_flag} {','.join(picked)}")
    if refs:                                            # 종류마다 올린 차례대로 (그 차례가 <Picture 1>, <Video 1> 의 번호다)
        named = [(f"{key}-r{i}{ext}", src, k) for i, ((_, src), (k, ext)) in enumerate(zip(refs, ref_kinds), 1)]
        for k, _, flag, _, _ in kind.refs.kinds:
            for name, src, kk in named:
                if kk == k:
                    uploads.append((name, src))
                    args.append(f"{flag} inputs/{name}")
    output = f"outputs/{key}{kind.out_ext}"
    args.append(f"--output {output}")
    if kind.seen_flag and len(files) > 1:               # 배경을 지우고 크기를 맞춘 사진을 남긴다 (결과가 이상할 때 원인을 보게)
        args.append(f"{kind.seen_flag} outputs/{key}.views")
    preview = None
    if kind.preview:
        preview = f"outputs/{key}{kind.preview}"
        args.append(f"{kind.preview_flag} {preview}")
    args += [f"{flag} outputs/{key}{tail}" for flag, tail in kind.extra_flags]
    args += kind.param_flags(values)
    command = f"./{shlex.quote(kind.script(settings))} " + " ".join(args)

    # 목록에 보일 이름: 첫 글의 앞부분이나 올린 파일 이름 (주인에게만 보인다).
    # 파일 이름에서만 폴더 부분을 뗀다. 글은 그대로 둔다 (영상 대사의 </d> 같은 '/' 가 든 글이 잘리지 않게)
    first = next((words[t.key] for t in kind.texts if words[t.key]), "")
    if first:
        title = " ".join(first.split())[:120]
    else:
        title = (files[0][0] if files else key).replace("\\", "/").rsplit("/", 1)[-1].strip()[:120]
    title = title or key

    have = _entries(settings, user, d) or {}
    for sub in ("inputs", "outputs"):
        if sub not in have:
            fs.make_dir(settings, user, "home", d, sub)
    kept: list[str] = []
    try:
        for name, src in uploads:
            saved = fs.save_upload(settings, user, "home", f"{d}/inputs", name, src)
            kept.append(f"{d}/inputs/{name}")
            if saved["size"] == 0:
                raise GenError("빈 파일입니다. 다시 골라 주세요.")
        thumb_rel = f"{d}/{preview}" if preview else None
        if kind.client_thumb and thumb is not None:
            head = thumb.read(MAX_THUMB_BYTES + 1)
            if 0 < len(head) <= MAX_THUMB_BYTES:        # 너무 크거나 빈 미리보기는 버린다 (없어도 동작한다)
                fs.save_upload(settings, user, "home", f"{d}/inputs", f"{key}.thumb.jpg", io.BytesIO(head))
                thumb_rel = f"{d}/inputs/{key}.thumb.jpg"
                kept.append(thumb_rel)
        job = sched.submit(user, name=kind.title, command=command, vram_gb=kind.vram_gb(settings, len(files), values), workdir=d)
    except Exception:
        for rel in kept:                                # 큐에 넣지 못했으면 올린 파일을 남기지 않는다
            try:
                fs.delete_path(settings, user, "home", rel, False)
            except fs.FileError:
                pass
        raise
    shown = values | ({"refs": len(files)} if kind.upload and kind.upload.hi > 1 and not kind.upload.roles else {})
    if len(picked) > 1:
        shown["views"] = ",".join(picked)              # "이 설정으로 다시"가 사진마다 방향을 되살릴 때 쓴다
    if after is not None:
        shown["after"] = after                          # 이 영상이 이어지는 앞 영상 (이어 붙일 차례를 찾을 때 쓴다)
    if refs:
        shown["references"] = {k: sum(1 for x, _ in ref_kinds if x == k) for k, *_ in kind.refs.kinds}
    cur = db.execute(
        """INSERT INTO gens(kind, user, job_id, title, prompt, texts, params, inputs, output, thumb, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (kind.key, user["name"], job["id"], title, first, json.dumps(words, ensure_ascii=False),
         json.dumps(shown, ensure_ascii=False), json.dumps([r for r in kept if r != thumb_rel]),
         f"{d}/{output}", thumb_rel, now))
    db.log_event(f"gen.{kind.key}.submit", user["name"], f"#{cur.lastrowid} 작업 #{job['id']}")
    return next(r for r in list_runs(db, sched, settings, user, kind) if r["id"] == cur.lastrowid)


MAX_JOIN = 20
JOIN_VRAM_GB = 1.0      # 영상 잇기는 ffmpeg 가 CPU 로 한다. 큐는 0 을 받지 않으므로 조금만 잡는다
JOIN_FLAG = "--join"


SHARED_IN_CONTAINER = "/shared/models/"     # 워크스페이스·작업 안에서 보이는 공용 모델 경로 (runtime.py 가 shared_root/models 를 여기에 붙인다)
FINGERPRINT_MAX_BYTES = 2 * 1024 * 1024     # 실행 파일 판 지문: 이보다 큰 파일은 내용 대신 크기와 고친 시각만 본다
_fingerprint_cache: dict[tuple, str] = {}


def _file_digest(path) -> str:
    """파일 하나의 지문. (경로, 크기, 고친 시각)이 같으면 다시 읽지 않는다."""
    import os

    st = os.stat(path)
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _fingerprint_cache:
        if st.st_size > FINGERPRINT_MAX_BYTES:
            _fingerprint_cache[key] = f"size:{st.st_size}:{st.st_mtime_ns}"
        else:
            with open(path, "rb") as f:
                _fingerprint_cache[key] = hashlib.sha256(f.read()).hexdigest()
    return _fingerprint_cache[key]


def runner_fingerprint(settings: Settings, user: dict, kind: Kind) -> str:
    """이 사용자의 실행 스크립트 판 지문 (노드 편집기가 지난 결과를 다시 쓸지 정할 때 쓴다).

    왜: 노드는 종류·설정·입력이 지난 실행과 같으면 그 결과를 다시 쓴다. 그런데 배포로 실행 스크립트가 고쳐져도 이 셋은
    그대로라, 고치기 전 판이 만든 결과를 계속 다시 썼다 (2026-11-16: 모양 먼저 3D 의 정면 고침 뒤에도 옛 결과가 나왔다).
    그래서 생성기 폴더의 파일 내용을 지문에 넣는다.
    - 폴더 바로 아래의 보통 파일(시작 스크립트 사본 등)은 내용을 읽는다.
    - 공용 실행 파일을 가리키는 링크(/shared/models/apps/…)는 서버 쪽 공용 경로에서 그 파일 내용을 읽는다.
      그 밖을 가리키는 링크와 폴더(모델, 코드 저장소, LoRA)는 읽지 않는다.
    읽을 수 없으면 빈 글자를 돌려준다 (그 경우 예전처럼 설정과 입력만으로 정한다)."""
    import os

    folder = kind.folder(settings)
    entries = _entries(settings, user, folder)
    if not entries:
        return ""
    home = settings.users_root / user["name"] / folder
    apps = (settings.shared_root / "models" / "apps").resolve()
    parts = []
    for name in sorted(entries):
        e = entries[name]
        if e["is_dir"] or name.endswith((".bak", ".pyc")):
            continue
        p = home / name
        try:
            if e["is_link"]:
                target = os.readlink(p)
                if not target.startswith(SHARED_IN_CONTAINER + "apps/"):
                    continue
                real = (settings.shared_root / "models" / target[len(SHARED_IN_CONTAINER):]).resolve()
                if not real.is_relative_to(apps) or not real.is_file():
                    continue
                parts.append(f"{name}={_file_digest(real)}")
            else:
                parts.append(f"{name}={_file_digest(p)}")
        except OSError:
            continue
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16] if parts else ""


def script_knows(settings: Settings, user: dict, kind: Kind, flag: str) -> bool:
    """이 사용자의 실행 스크립트가 그 옵션을 아는지 (스크립트 안에 옵션 글자가 있는지). 예전 판 스크립트를 가려낸다."""
    try:
        f, _, _ = fs.open_for_download(settings, user, "home", f"{kind.folder(settings)}/{kind.script(settings)}")
        with f:
            return flag.encode() in f.read(256 * 1024)
    except (fs.FileError, OSError):
        return False


def join_videos(db: DB, sched: Scheduler, settings: Settings, user: dict, ids) -> dict:
    """다 만든 영상 여러 개를 고른 차례대로 이어 붙이는 작업을 큐에 넣는다. 목록에 새 항목으로 나타난다.

    끝 장면에서 이어 만든 영상끼리는 겹치는 장면을 잘라 이음매가 매끄럽다 (h3_join.py). 고른 영상의 파일은 그대로 둔다.
    """
    kind = KIND_VIDEO
    if not ready(settings, user, kind):
        raise GenError(setup_hint(settings, kind))
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise GenError(f"이을 영상 번호의 목록이어야 합니다 (받은 값 {str(ids)[:60]!r})")
    if not 2 <= len(ids) <= MAX_JOIN:
        raise GenError(f"이을 영상은 2~{MAX_JOIN}개여야 합니다 (받은 수 {len(ids)}개)")
    if len(set(ids)) != len(ids):
        raise GenError("같은 영상을 두 번 골랐습니다. 한 번씩만 고르세요.")
    if not script_knows(settings, user, kind, JOIN_FLAG):
        d, s = kind.folder(settings), kind.script(settings)
        raise GenError(f"홈의 {d}/{s} 가 영상 이어 붙이기({JOIN_FLAG})를 모르는 예전 판입니다. "
                       f"관리자에게 새 판 스크립트(h3.sh, h3_join.py)를 받아 ~/{d} 에 두세요.")
    marks = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in db.all(
        f"""SELECT g.id, g.title, g.output, j.status FROM gens g LEFT JOIN jobs j ON j.id = g.job_id
            WHERE g.user=? AND g.kind=? AND g.id IN ({marks})""", (user["name"], kind.key, *ids))}
    missing = [str(i) for i in ids if i not in rows]
    if missing:
        raise GenError(f"없는 영상입니다 (#{', #'.join(missing)})")
    for i in ids:
        r = rows[i]
        name = " ".join(r["title"].split())[:30]
        if r["status"] != "succeeded":
            raise GenError(f"'{name}'(#{i})은 아직 다 만들지 않았거나 실패한 영상입니다. 다 만든 영상만 이을 수 있습니다.")
        folder, _, base = r["output"].rpartition("/")
        entry = (_entries(settings, user, folder) or {}).get(base)
        if entry is None or entry["is_dir"]:
            raise GenError(f"'{name}'(#{i})의 영상 파일이 없습니다 ({r['output']}). 지웠으면 목록에서도 지우세요.")

    now = time.time()
    key = new_key(now)
    d = kind.folder(settings)
    if "outputs" not in (_entries(settings, user, d) or {}):
        fs.make_dir(settings, user, "home", d, "outputs")
    output = f"outputs/{key}{kind.out_ext}"
    thumb = f"outputs/{key}{kind.preview}"
    clips = " ".join(f"{JOIN_FLAG} {shlex.quote(posixpath.relpath(rows[i]['output'], d))}" for i in ids)
    extras = "".join(f" {flag} outputs/{key}{tail}" for flag, tail in kind.extra_flags)
    command = f"./{shlex.quote(kind.script(settings))} {clips} --output {output} {kind.preview_flag} {thumb}{extras}"
    job = sched.submit(user, name="영상 이어 붙이기", command=command, vram_gb=JOIN_VRAM_GB, workdir=d)
    title = f"이어 붙인 영상 {len(ids)}개: {' '.join(rows[ids[0]]['title'].split())}"[:120]
    cur = db.execute(
        """INSERT INTO gens(kind, user, job_id, title, prompt, texts, params, inputs, output, thumb, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (kind.key, user["name"], job["id"], title, "", "{}", json.dumps({"joined": ids}), "[]",
         f"{d}/{output}", f"{d}/{thumb}", now))
    db.log_event("gen.video.join", user["name"], f"#{cur.lastrowid} 작업 #{job['id']} 영상 {len(ids)}개")
    return next(r for r in list_runs(db, sched, settings, user, kind) if r["id"] == cur.lastrowid)


def list_runs(db: DB, sched: Scheduler, settings: Settings, user: dict, kind: Kind, limit: int = 60) -> list[dict]:
    rows = db.all(
        """SELECT g.id, g.kind, g.job_id, g.title, g.prompt, g.texts, g.params, g.inputs, g.output, g.thumb, g.created_at,
                  j.status, j.error, j.exit_code, j.started_at, j.finished_at, j.peak_vram_gb, j.vram_gb
           FROM gens g LEFT JOIN jobs j ON j.id = g.job_id
           WHERE g.user=? AND g.kind=? ORDER BY g.id DESC LIMIT ?""", (user["name"], kind.key, limit))
    folders: dict[str, dict] = {}

    def size_of(rel: str | None) -> int | None:
        if not rel:
            return None
        folder, _, name = rel.rpartition("/")
        if folder not in folders:
            folders[folder] = _entries(settings, user, folder) or {}
        entry = folders[folder].get(name)
        return entry["size"] if entry is not None and not entry["is_dir"] else None

    for r in rows:
        r["params"] = json.loads(r["params"] or "{}")
        r["inputs"] = json.loads(r["inputs"] or "[]")
        r["texts"] = json.loads(r["texts"] or "{}")
        if not r["texts"] and r["prompt"] and kind.texts:       # 글 칸이 하나뿐이던 앞선 판의 줄
            r["texts"] = {kind.texts[0].key: r["prompt"]}
        r["output_size"] = size_of(r["output"])
        r["thumb_size"] = size_of(r["thumb"])
        # 곁 결과(악보 등): 결과 파일과 같은 이름에 꼬리만 다르다
        stem = r["output"][:-len(kind.out_ext)] if r["output"].endswith(kind.out_ext) else r["output"]
        r["extras"] = {tail: f"{stem}{tail}" for _, tail in kind.extra_flags if size_of(f"{stem}{tail}") is not None}
        r["seen"] = []
        if kind.seen_flag and len(r["inputs"]) > 1:      # 모델이 받은 사진 (여러 장일 때만 생긴다)
            got = _entries(settings, user, f"{stem}.views") or {}
            r["seen"] = [f"{stem}.views/{n}" for n in sorted(got) if n.endswith(".png") and not got[n]["is_dir"]]
        r["wait_reason"] = sched.reasons.get(r["job_id"]) if r["status"] == "queued" else None
        r["progress"] = None
        if r["status"] == "running":
            job = sched.get(r["job_id"])
            try:
                r["progress"] = kind.progress(sched.logs(job, PROGRESS_TAIL_LINES)) if job else None
            except Exception:
                pass                                    # 로그를 못 읽어도 목록은 보여 준다
    return rows


def list_presets(db: DB, user: dict, kind: Kind) -> list[dict]:
    rows = db.all("SELECT id, name, params, created_at FROM gen_presets WHERE user=? AND kind=? ORDER BY name",
                  (user["name"], kind.key))
    for r in rows:
        r["params"] = json.loads(r["params"])
    return rows


def save_preset(db: DB, user: dict, kind_key: str, name: str, params: dict) -> dict:
    """지금 설정을 이름을 붙여 저장한다. 같은 이름이 있으면 덮어쓴다."""
    kind = get_kind(kind_key)
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 40:
        raise GenError(f"설정 묶음의 이름은 1~40자여야 합니다 (받은 길이 {len(name)}자)")
    if not isinstance(params, dict):
        raise GenError("설정은 이름과 값의 묶음(JSON 객체)이어야 합니다")
    values = kind.clean_params(params)
    have = db.one("SELECT id FROM gen_presets WHERE user=? AND kind=? AND name=?", (user["name"], kind.key, name))
    if have is None:
        n = db.one("SELECT COUNT(*) AS n FROM gen_presets WHERE user=? AND kind=?", (user["name"], kind.key))["n"]
        if n >= MAX_PRESETS:
            raise GenError(f"설정 묶음은 종류마다 {MAX_PRESETS}개까지 저장할 수 있습니다. 쓰지 않는 것을 지워 주세요.")
        db.execute("INSERT INTO gen_presets(kind, user, name, params, created_at) VALUES (?,?,?,?,?)",
                   (kind.key, user["name"], name, json.dumps(values, ensure_ascii=False), time.time()))
    else:
        db.execute("UPDATE gen_presets SET params=?, created_at=? WHERE id=?",
                   (json.dumps(values, ensure_ascii=False), time.time(), have["id"]))
    return next(p for p in list_presets(db, user, kind) if p["name"] == name)


def delete_preset(db: DB, user: dict, kind_key: str, preset_id: int) -> None:
    kind = get_kind(kind_key)
    cur = db.execute("DELETE FROM gen_presets WHERE id=? AND user=? AND kind=?", (preset_id, user["name"], kind.key))
    if cur.rowcount == 0:
        raise GenError(f"없는 설정 묶음입니다 (#{preset_id})")


def vram_by(settings: Settings, kind: Kind) -> dict | None:
    """고르는 값 하나에 따라 예약할 VRAM 이 바뀌면 {param, gb: {값: GB}}. 화면이 고른 값에 맞춰 안내하게 한다 (소리 다듬기의 '넣은 것')."""
    if not kind.vram_for:
        return None
    base = {p.key: p.default for p in kind.params}
    for p in kind.params:
        if p.type == "choice":
            gb = {c[0]: kind.vram_gb(settings, 1, base | {p.key: c[0]}) for c in p.choices}
            if len(set(gb.values())) > 1:
                return {"param": p.key, "gb": gb}
    return None


def state(db: DB, sched: Scheduler, settings: Settings, user: dict, kind_key: str) -> dict:
    kind = get_kind(kind_key)
    ok = ready(settings, user, kind)
    return {
        "kind": kind.key,
        "title": kind.title,
        "ready": ok,
        "hint": None if ok else setup_hint(settings, kind),
        "dir": kind.folder(settings),
        "script": kind.script(settings),
        "vram_gb": kind.vram_gb(settings),
        "vram_multi_gb": kind.vram_gb(settings, 2) if kind.vram_multi_setting else None,
        "vram_by": vram_by(settings, kind),
        "texts": [t.spec() for t in kind.texts],
        "upload": kind.upload.spec() if kind.upload else None,
        "refs": kind.refs.spec() if kind.refs else None,
        "params": [p.spec() for p in kind.params],
        "client_thumb": kind.client_thumb,
        "presets": list_presets(db, user, kind),
        "runs": list_runs(db, sched, settings, user, kind),
    }


def delete(db: DB, sched: Scheduler, settings: Settings, user: dict, kind_key: str, run_id: int) -> None:
    """목록에서 지우고, 올린 파일과 결과 파일도 지운다. 작업 기록과 로그는 "GPU 작업"에 남는다."""
    kind = get_kind(kind_key)
    run = db.one("SELECT * FROM gens WHERE id=? AND user=? AND kind=?", (run_id, user["name"], kind.key))
    if run is None:
        raise GenError(f"없는 항목입니다 (#{run_id})")
    job = sched.get(run["job_id"]) if run["job_id"] else None
    if job is not None and job["status"] in ("queued", "running"):
        raise GenError("아직 끝나지 않은 작업입니다. 먼저 작업을 취소하세요.")
    output = run["output"]
    stem = output[:-len(kind.out_ext)] if output.endswith(kind.out_ext) else output
    for rel in [*json.loads(run["inputs"] or "[]"), output, run["thumb"], *(f"{stem}{tail}" for _, tail in kind.extra_flags)]:
        if not rel:
            continue
        try:
            fs.delete_path(settings, user, "home", rel, False)
        except fs.FileError:
            pass                                        # 이미 지웠거나 만들어지지 않은 파일
    if kind.seen_flag:
        try:
            fs.delete_path(settings, user, "home", f"{stem}.views", True)
        except fs.FileError:
            pass                                        # 여러 장이 아니었거나 작업이 남기지 않았다
    db.execute("DELETE FROM gens WHERE id=?", (run_id,))
    db.log_event(f"gen.{kind.key}.delete", user["name"], f"#{run_id}")
