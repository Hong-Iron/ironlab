"""노드 편집기 (Advanced 메뉴): 노드를 이어 붙인 그래프를 GPU 작업의 사슬로 실행한다.

쉬운 메뉴(gen.py)는 양식 하나가 작업 하나다. 여기서는 노드 하나가 작업 하나이고, 앞 노드가 쓴 결과 파일이
뒤 노드의 입력이 된다. 예) 글 → 그림 그리기 → 3D 만들기, 가사 → 악보 쓰기 → 노래 만들기,
글 → 그림 그리기 → 영상 만들기(그 그림이 첫 장면), 대본 + 소리 파일 → 목소리 만들기(그 목소리를 본뜸),
영상 만들기 → 효과음 만들기(그 영상에 맞춘 소리를 붙임), 노래 만들기 → 소리 다듬기(곡과 그 악보를 받아 목소리를 다듬음).

- 노드의 종류 (NODE_TYPES): 받는 것(입력), 내는 것(출력), 조절 값. 조절 값은 gen.py 의 정의를 그대로 쓴다.
- 그래프: 노드 목록과 연결 목록. 화면이 JSON 으로 보낸다.
- 실행 (run): 그래프 한 벌을 돌린 기록. 노드마다 상태와 결과 파일을 가진다.

실행 순서는 advance() 가 정한다. 감시 루프가 주기마다 부르고, 그때마다 "앞 노드가 모두 끝난 노드"를 큐에 넣는다.
GPU 가 하나라 작업은 한 번에 하나씩 돌고, 순서 기다리기·VRAM 예약·로그·취소는 "GPU 작업"과 똑같이 동작한다.

같은 것을 두 번 만들지 않는다. 어떤 노드의 종류·조절 값·입력이 지난 실행의 노드와 똑같고 그 결과 파일이 남아 있으면,
작업을 넣지 않고 그 결과를 다시 쓴다 (상태 reused). 그래서 뒤쪽 노드의 값만 고쳐 다시 실행하면 그 노드부터만 돈다.
실행 스크립트(생성기 폴더의 시작 스크립트와 공용 실행 파일)의 내용이 바뀌면 다시 쓰지 않는다 (gen.runner_fingerprint).

밖에 보이는 글자에 대한 약속은 gen.py 와 같다: 작업 이름에는 노드 종류 이름만 쓰고, 파일 이름은 날짜·무작위 글자와
노드 번호로만 짓고, 글은 명령에 넣지 않고 파일로 넘긴다.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import posixpath
import re
import shlex
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO

from . import files as fs
from . import gen
from .config import Settings
from .db import DB
from .gen import Param
from .scheduler import JobError, Scheduler

PORT_TYPES = {"text": "글", "image": "그림", "mesh": "3D 모델", "audio": "소리", "video": "영상", "views": "여러 방향 묶음"}
MAX_NODES = 40                  # 그래프 하나의 노드 수
MAX_JOB_NODES = 12              # 그 가운데 GPU 작업이 되는 노드 수
MAX_TEXT_CHARS = 30000          # 글 노드 하나의 글자 수
MAX_FLOWS = 60                  # 사용자마다 저장할 수 있는 그래프 수
NODE_ID_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")
SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")    # 명령에 그대로 넣어도 되는 경로 (공백·따옴표·$ 없음)
OK = ("succeeded", "reused")                                    # 결과를 낸 상태
STOPPED = ("failed", "cancelled", "skipped")                    # 결과 없이 끝난 상태
LIVE = ("queued", "running")                                    # 큐에 작업이 있는 상태
VERSION = 1                     # 노드가 명령을 만드는 방식이 바뀌면 올린다 (지난 결과를 다시 쓰지 않게 된다)
BATCH_MARK_RE = re.compile(r"묶음 (\d+)/\d+\s*$", re.M)        # 묶음 작업이 한 장을 시작할 때 찍는 줄
BATCH_TAIL_LINES = 80           # 묶음 작업의 진행을 읽을 때 보는 로그 끝 줄 수 ('묶음 k/n' 줄까지 닿게)

_lock = threading.RLock()       # 감시 루프와 요청 처리가 같은 실행을 동시에 진행시키지 않게 한다
log = logging.getLogger("ironlab.flow")


class FlowError(ValueError):
    pass


# ── 노드 종류 ──

@dataclass(frozen=True)
class In:
    """입력 하나. 연결된 앞 노드의 결과 파일을 `<flag> <파일>` 로 넘긴다."""
    key: str
    label: str
    type: str
    flag: str
    required: bool = True
    max: int = 1                # 연결할 수 있는 수 (참고 그림은 여러 장)
    help: str = ""
    role: str = ""              # 노드 종류에 roles_flag 가 있으면, 이 입력에 이어진 파일의 역할 이름 (3D 여러 장: 찍은 방향)

    def spec(self) -> dict:
        return {"key": self.key, "label": self.label, "type": self.type, "required": self.required, "max": self.max,
                "help": self.help}


@dataclass(frozen=True)
class Out:
    """출력 하나. 실행 폴더의 `<노드 번호>.<key><ext>` 에 쓰게 하고 그 경로를 `<flag> <파일>` 로 넘긴다."""
    key: str
    label: str
    type: str
    ext: str
    flag: str = "--output"
    optional: bool = False      # 설정에 따라 안 생길 수 있는 결과 (악보 없이 만든 곡의 악보)
    hidden: bool = False        # 다른 노드에 잇지 않는 곁 결과 (작은 미리보기, 들어 보기용 mp3)

    def spec(self) -> dict:
        return {"key": self.key, "label": self.label, "type": self.type}


@dataclass(frozen=True)
class NodeType:
    key: str
    title: str                  # 화면 이름이자 작업 이름의 몸통 (다른 사용자에게도 보인다)
    help: str
    inputs: tuple[In, ...] = ()
    outputs: tuple[Out, ...] = ()
    params: tuple[Param, ...] = ()
    kind: str = ""              # 실행 스크립트·폴더·VRAM 을 빌려 올 gen.py 의 종류. 빈 글자면 작업이 없는 노드
    fixed: tuple[str, ...] = ()  # 명령에 늘 붙이는 옵션
    source: str = ""            # "text": 적은 글을 낸다, "picture": 올린 사진을 낸다, "sound": 올린 소리 파일을 낸다
    roles_flag: str = ""        # 이어진 입력의 역할(In.role)을 입력 순서대로 쉼표로 이어 `<roles_flag> <역할,역할>` 로 넘긴다
    # 같은 때 준비된 이 종류의 노드 여럿을 작업 하나로 묶는다: 모델을 한 번만 올리고 차례로 만든다.
    # 실행 스크립트가 `<batch_flag> <묶음.json>` 을 알아들어야 한다 (JSON 배열, 칸마다 한 노드의 옵션 목록).
    # batch_by 의 설정 값이 다르면 다른 모델을 올리므로 따로 묶는다.
    batch_flag: str = ""
    batch_by: tuple[str, ...] = ()
    vram_gb: float = 0.0        # 0 이 아니면 gen.py 종류의 값 대신 이만큼 예약한다 (영상 잇기는 GPU 를 거의 쓰지 않는다)
    needs_flag: str = ""        # 실행 스크립트가 이 옵션을 알아야 돈다 (예전 판 스크립트면 노드를 실패로 알린다)

    def spec(self, settings: Settings, user: dict) -> dict:
        out = {"key": self.key, "title": self.title, "help": self.help, "source": self.source, "kind": self.kind,
               "inputs": [i.spec() for i in self.inputs], "outputs": [o.spec() for o in self.outputs if not o.hidden],
               "params": [p.spec() for p in self.params], "vram_gb": None, "ready": True, "hint": None}
        if self.kind:
            kind = gen.KINDS[self.kind]
            ok = gen.ready(settings, user, kind)
            out |= {"vram_gb": self.vram_gb or kind.vram_gb(settings, 2 if self.roles_flag else 1), "ready": ok,
                    "hint": None if ok else gen.setup_hint(settings, kind)}
        return out


def _pick(kind: gen.Kind, *keys: str) -> tuple[Param, ...]:
    by_key = {p.key: p for p in kind.params}
    return tuple(by_key[k] for k in keys)


# 파일을 올려 내는 노드: 노드 종류 → (받는 확장자, 화면에 쓰는 이름)
UPLOAD_SOURCES = {"picture": (gen.IMAGE_EXTS, "사진"), "sound": (gen.AUDIO_EXTS, "소리 파일"), "clip": (gen.VIDEO_EXTS, "영상 파일")}

_STYLE = In("style", "분위기와 장르", "text", "--style-file", help="장르, 악기, 목소리, 빠르기를 쉼표로 이어 적은 글")
_LYRICS = In("lyrics", "가사", "text", "--lyrics-file", help="[Verse], [Chorus] 같은 구획 표시를 줄 맨 앞에 둔 가사")

NODE_TYPES: dict[str, NodeType] = {t.key: t for t in (
    NodeType("text", "글", "적은 글을 그대로 냅니다. 그릴 내용, 가사, 분위기 설명, 직접 쓴 악보에 씁니다.",
             outputs=(Out("text", "글", "text", ".txt"),), source="text"),
    NodeType("picture", "사진", "올린 사진을 냅니다. 3D 로 만들 사진, 고칠 그림, 영상의 첫 장면에 씁니다.",
             outputs=(Out("image", "그림", "image", ""),), source="picture"),
    NodeType("sound", "소리 파일", "올린 소리 파일을 냅니다. 목소리를 본뜰 참고 목소리, 다듬을 곡에 씁니다.",
             outputs=(Out("audio", "소리", "audio", ""),), source="sound"),
    NodeType("clip", "영상 파일", "올린 영상 파일을 냅니다. 효과음을 붙일 영상, 이어 붙일 영상, 영상 만들기의 참고 영상에 씁니다.",
             outputs=(Out("video", "영상", "video", ""),), source="clip"),
    NodeType("image", "그림 그리기", "글대로 그림을 그립니다. 참고 그림을 이으면 글대로 그 그림을 고칩니다 (Qwen-Image).",
             inputs=(In("prompt", "그릴 내용", "text", "--prompt-file"),
                     In("negative", "피할 내용", "text", "--negative-file", required=False,
                        help="가이던스가 1 보다 클 때만 듣습니다"),
                     In("refs", "참고 그림", "image", "--image", required=False, max=4,
                        help="이으면 글대로 이 그림을 고칩니다 (4장까지). 각도 바꾸기는 첫 장만 씁니다")),
             outputs=(Out("image", "그림", "image", ".png"), Out("thumb", "작은 미리보기", "image", ".jpg", "--thumb", hidden=True)),
             params=gen.KIND_IMAGE.params, kind="image", batch_flag="--batch-file", batch_by=("model",)),
    NodeType("mesh", "3D 만들기", "사진 한 장으로 3D 모델 파일(GLB)을 만듭니다 (Pixal3D).",
             inputs=(In("image", "사진", "image", "--image", help="물체 하나가 가운데에 크게 찍힌 그림"),),
             outputs=(Out("mesh", "3D 모델", "mesh", ".glb"),),
             params=gen.KIND_3D.params, kind="3d"),
    NodeType("mesh_mv", "3D 만들기 (여러 장)",
             "한 물체를 여러 방향에서 찍은 사진으로 3D 모델 파일(GLB)을 만듭니다. 앞 사진은 꼭 잇고, 나머지 방향은 있는 것만 잇습니다. "
             "같은 거리·같은 줌으로 직접 찍은 사진이어야 합니다 (Pixal3D 여러 장). 그림 그리기의 '각도 바꾸기'로 만든 그림은 "
             "방향마다 따로 상상해 그린 것이라 서로 맞지 않아, 앞 얼굴이 뒷머리에도 생기는 것처럼 망가집니다. "
             "사진이 한 장뿐이면 '3D 만들기 (모양 먼저)'를 쓰세요.",
             inputs=tuple(In(key, label, "image", "--image", required=key == "front", help=how, role=key)
                          for key, label, how in gen.VIEW_ROLES),
             outputs=(Out("mesh", "3D 모델", "mesh", ".glb"),),
             params=gen.KIND_3D.params, kind="3d", roles_flag="--views"),
    NodeType("multiview", "여러 방향 그리기",
             "사진 한 장에서 물체를 여러 방향(6 또는 4)에서 본 그림을 한 번에 함께 그려 서로 맞춥니다 (MV-Adapter). "
             "'여러 방향 묶음'을 '3D 만들기 (여러 방향 그림)'에 이으면 3D 모델이 됩니다.",
             inputs=(In("image", "사진", "image", "--image", help="물체 하나가 크게 찍힌 사진. 배경은 알아서 지웁니다"),
                     In("prompt", "물체 설명 (영어)", "text", "--prompt-file", required=False,
                        help="예) a dog. 비우면 'a high quality object'")),
             outputs=(Out("image", "모은 그림", "image", ".png"),
                      Out("views", "여러 방향 묶음", "views", ".json", "--views-out"),
                      *(Out(key, label, "image", ".png", f"--out-{key}", optional=key in ("front-right", "front-left"))
                        for key, label in gen.MV_VIEWS),
                      Out("thumb", "작은 미리보기", "image", ".jpg", "--thumb", hidden=True)),
             params=gen.KIND_MV.params, kind="multiview"),
    NodeType("mesh_views", "3D 만들기 (여러 방향 그림)",
             "'여러 방향 그리기'가 그린 그림 묶음으로 3D 모델 파일(GLB)을 만듭니다. 그림마다 그린 각도와 틀을 그대로 써서 "
             "카메라를 놓습니다 (Pixal3D 여러 장). 개 사진으로 해 보니 얼굴이 하나인 머리가 나왔습니다. 다만 사진의 자세가 "
             "정면·눈높이가 아니면 그림의 모양부터 어긋나 몸이 두 개로 겹친 3D 가 나오기도 합니다 (소 사진). "
             "그럴 때는 '3D 만들기 (모양 먼저)'를 쓰세요.",
             inputs=(In("views", "여러 방향 묶음", "views", "--views-json", help="'여러 방향 그리기'의 '여러 방향 묶음'"),),
             outputs=(Out("mesh", "3D 모델", "mesh", ".glb"),),
             params=gen.KIND_3D.params, kind="3d", vram_gb=20.0, needs_flag="--views-json"),
    NodeType("mesh_shape", "3D 만들기 (모양 먼저)",
             "사진 한 장으로 3D 모델 파일(GLB)을 만듭니다. 모양은 사진 한 장 3D(Pixal3D)가 정하고, 그 모양 위에 MV-Adapter 가 "
             "여섯 방향(앞·오른쪽·뒤·왼쪽·위·아래)의 색만 칠해 입힙니다. 사진이 본 쪽은 사진 색을 그대로 두고, 사진에 안 보이던 "
             "옆과 뒤에 무늬와 결이 생깁니다. 모양을 그림 모델이 정하지 않아 몸이 두 개로 겹치는 일이 없습니다. "
             "한 물체에 약 3분 반 (소·고양이·그루터기·거북이로 확인).",
             inputs=(In("image", "사진", "image", "--image", help="물체 하나가 크게 찍힌 사진. 배경은 알아서 지웁니다"),
                     In("prompt", "물체 설명 (영어)", "text", "--prompt-file", required=False,
                        help="예) a cow. 색을 칠할 때만 씁니다. 비우면 'high quality'")),
             outputs=(Out("mesh", "3D 모델", "mesh", ".glb"),
                      Out("painted", "칠한 여섯 방향", "image", ".png", "--views-image"),
                      Out("thumb", "작은 미리보기", "image", ".jpg", "--thumb", hidden=True)),
             params=gen.KIND_SHAPE.params, kind="shapefirst"),
    NodeType("score", "악보 쓰기", "분위기와 가사를 보고 멜로디와 코드를 글자 악보(ABC 표기)로 씁니다. 소리는 만들지 않습니다 (YuE2).",
             inputs=(_STYLE, _LYRICS),
             outputs=(Out("score", "악보", "text", ".abc"),),
             params=(Param("cot", "악보에 담을 것", "choice", "full", "--cot",
                           choices=(("full", "멜로디와 코드"), ("melody", "멜로디만")),
                           help="멜로디만 쓰게 하면 노래를 만들 때 반주를 더 자유롭게 붙입니다."),
                     *_pick(gen.KIND_MUSIC, "seed", "plan_temperature", "plan_top_p", "plan_top_k", "plan_repeat",
                            "plan_max_tokens")),
             kind="music", fixed=("--stage plan",)),
    NodeType("song", "노래 만들기", "분위기와 가사로 노래와 반주가 든 곡을 만듭니다. 악보를 이으면 그 악보대로 만듭니다 (YuE2).",
             inputs=(_STYLE, _LYRICS,
                     In("score", "악보", "text", "--abc-file", required=False,
                        help="이으면 악보를 새로 쓰지 않고 이 악보대로 소리를 만듭니다")),
             outputs=(Out("audio", "곡", "audio", ".flac"),
                      Out("listen", "들어 보기용 mp3", "audio", ".mp3", "--mp3", hidden=True),
                      Out("score", "쓴 악보", "text", ".abc", "--score", optional=True)),
             params=gen.KIND_MUSIC.params, kind="music"),
    NodeType("video", "영상 만들기", "장면 설명으로 소리가 든 영상을 만듭니다. 첫 장면·끝 장면 그림을 이으면 그 그림에서 시작하고 끝납니다 (MiniMax H3).",
             inputs=(In("prompt", "장면", "text", "--prompt-file", help="화면, 움직임, 카메라, 대사를 시간 순서로 적은 글"),
                     In("sound", "주변 소리", "text", "--sound-file", required=False, help="장면 안에서 나는 소리"),
                     In("music", "배경 음악", "text", "--music-file", required=False, help="보는 사람만 듣는 음악. 없으면 넣지 않습니다"),
                     In("first", "첫 장면", "image", "--image", required=False, help="이으면 영상이 이 그림에서 시작합니다"),
                     In("last", "끝 장면", "image", "--image", required=False, help="이으면 영상이 이 그림에서 끝납니다 (첫 장면도 이어야 합니다)"),
                     In("ref_images", "참고 그림", "image", "--ref-image", required=False, max=4,
                        help="장면 글에서 <Picture 1> 처럼 부릅니다. 첫·끝 장면과 함께 쓸 수 없습니다"),
                     In("ref_videos", "참고 영상", "video", "--ref-video", required=False, max=2,
                        help="<Video 1> 처럼 부릅니다. 앞 3초(이어 만들기면 끝 2초)를 작게 줄여 씁니다"),
                     In("ref_audios", "참고 소리", "audio", "--ref-audio", required=False, max=2,
                        help="<Audio 1> 처럼 부릅니다. 목소리, 배경 음악 스타일 등으로 씁니다")),
             outputs=(Out("video", "영상", "video", ".mp4"),
                      Out("last", "끝 장면", "image", ".png", "--last-frame"),
                      Out("thumb", "작은 미리보기", "image", ".jpg", "--thumb", hidden=True)),
             params=gen.KIND_VIDEO.params, kind="video"),
    NodeType("video_join", "영상 이어 붙이기",
             "영상을 차례대로 이어 하나로 만듭니다. 끝 장면에서 이어 만든 영상은 겹치는 장면을 잘라 이음매가 매끄럽습니다. "
             "GPU 를 거의 쓰지 않습니다.",
             inputs=tuple(In(f"v{i}", label, "video", "--join", required=i <= 2,
                             help="이 차례에 붙일 영상" if i > 2 else "꼭 이어야 합니다")
                          for i, label in enumerate(("첫 영상", "두 번째 영상", "세 번째 영상", "네 번째 영상",
                                                     "다섯 번째 영상", "여섯 번째 영상"), 1)),
             outputs=(Out("video", "영상", "video", ".mp4"),
                      Out("last", "끝 장면", "image", ".png", "--last-frame"),
                      Out("thumb", "작은 미리보기", "image", ".jpg", "--thumb", hidden=True)),
             kind="video", vram_gb=gen.JOIN_VRAM_GB, needs_flag=gen.JOIN_FLAG),
    NodeType("voice", "목소리 만들기", "대본을 목소리로 읽습니다. 참고 목소리를 이으면 그 목소리를 본뜹니다 (Fish Audio Drama 3).",
             inputs=(In("script", "대본", "text", "--text-file", help="여러 사람이면 줄 맨 앞에 '화자1:', '화자2:' 를 붙인 글"),
                     In("refs", "참고 목소리", "audio", "--ref", required=False, max=4,
                        help="화자1, 화자2 … 순서로 잇습니다 (4개까지)"),
                     In("ref_text", "참고 목소리의 말", "text", "--ref-text-file", required=False,
                        help="참고 목소리마다 그 녹음에서 하는 말을 한 줄씩")),
             outputs=(Out("audio", "목소리", "audio", ".wav"),
                      Out("listen", "들어 보기용 mp3", "audio", ".mp3", "--mp3", hidden=True)),
             params=gen.KIND_VOICE.params, kind="voice"),
    NodeType("sfx", "효과음 만들기",
             "영어로 적은 소리 설명대로 효과음(5초까지)을 만듭니다. 영상을 이으면 그 영상의 움직임에 맞춘 소리(8초까지)를 만들고, "
             "그 소리를 붙인 영상도 냅니다 (Sony Woosh).",
             inputs=(In("prompt", "소리 설명 (영어)", "text", "--prompt-file", required=False,
                        help="들릴 소리를 영어로 짧게 적은 글. 영상을 잇지 않으면 꼭 이어야 합니다"),
                     In("negative", "피할 소리", "text", "--negative-file", required=False, help="본판에서만 듣습니다"),
                     In("video", "영상", "video", "--video", required=False,
                        help="이으면 이 영상의 움직임에 맞춘 소리를 만듭니다 (앞 8초까지)")),
             outputs=(Out("audio", "효과음", "audio", ".wav"),
                      Out("listen", "들어 보기용 mp3", "audio", ".mp3", "--mp3", hidden=True),
                      Out("video", "소리를 붙인 영상", "video", ".mp4", "--video-out", optional=True),
                      Out("thumb", "작은 미리보기", "image", ".jpg", "--thumb", hidden=True, optional=True)),
             params=gen.KIND_SFX.params, kind="sfx"),
    NodeType("polish", "소리 다듬기",
             "곡을 목소리와 반주로 나눠 목소리만 다듬고(거슬리는 울림·치찰음 줄이기, 압축, 따뜻하게) 잔향과 함께 다시 섞은 뒤 음량을 맞춥니다. "
             "목소리·효과음은 나누지 않고 다듬습니다. 영상을 이으면 다듬은 소리로 바꾼 영상도 냅니다 (Demucs + 믹싱 처리).",
             inputs=(In("audio", "소리", "audio", "--input", required=False, help="다듬을 곡, 목소리, 효과음. 소리와 영상 가운데 하나만 잇습니다"),
                     In("video", "영상", "video", "--input", required=False, help="이으면 이 영상의 소리를 다듬고, 그 소리로 바꾼 영상을 냅니다"),
                     In("score", "악보", "text", "--score-file", required=False,
                        help="노래 만들기의 '쓴 악보'를 이으면 빠르기를 읽어 메아리를 박자에 맞춥니다")),
             outputs=(Out("audio", "다듬은 소리", "audio", ".flac"),
                      Out("listen", "들어 보기용 mp3", "audio", ".mp3", "--mp3", hidden=True),
                      Out("vocals", "다듬은 목소리만", "audio", ".flac", "--vocals-out", optional=True),
                      Out("backing", "반주만", "audio", ".flac", "--backing-out", optional=True),
                      Out("video", "다듬은 소리의 영상", "video", ".mp4", "--video-out", optional=True),
                      Out("report", "처리 보고", "text", ".json", "--report", hidden=True, optional=True)),
             params=gen.KIND_POLISH.params, kind="polish"),
)}


# ── 그래프 검사 ──

def _node_type(key) -> NodeType:
    if key not in NODE_TYPES:
        raise FlowError(f"없는 노드 종류입니다 (받은 값 {key!r}). {', '.join(NODE_TYPES)} 가운데 하나여야 합니다.")
    return NODE_TYPES[key]


def _label(node: dict) -> str:
    return f"'{NODE_TYPES[node['type']].title}' 노드({node['id']})"


def order_of(graph: dict) -> list[str]:
    """앞 노드가 먼저 오는 순서. 연결이 한 바퀴 돌면 FlowError."""
    waiting = {n["id"]: 0 for n in graph["nodes"]}
    for e in graph["edges"]:
        waiting[e["to"]] += 1
    ready = [n["id"] for n in graph["nodes"] if waiting[n["id"]] == 0]
    out = []
    while ready:
        nid = ready.pop(0)
        out.append(nid)
        for e in graph["edges"]:
            if e["from"] == nid:
                waiting[e["to"]] -= 1
                if waiting[e["to"]] == 0:
                    ready.append(e["to"])
    if len(out) != len(waiting):
        stuck = sorted(set(waiting) - set(out))
        raise FlowError(f"연결이 한 바퀴 돌아 제자리로 옵니다 (노드 {', '.join(stuck)}). 결과가 앞으로 되돌아가는 연결을 끊어 주세요.")
    return out


def clean_graph(raw, *, strict: bool) -> dict:
    """화면이 보낸 그래프를 검사해 정리한 것을 돌려준다.

    strict 는 실행할 때다: 꼭 필요한 입력이 모두 이어져 있고, 글과 사진이 채워져 있고, 작업이 되는 노드가 있어야 한다.
    저장만 할 때는 만들다 만 그래프도 받는다.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not isinstance(raw.get("edges", []), list):
        raise FlowError("그래프는 nodes(노드 목록)와 edges(연결 목록)를 가진 JSON 객체여야 합니다")
    if not 1 <= len(raw["nodes"]) <= MAX_NODES:
        raise FlowError(f"노드는 1~{MAX_NODES}개여야 합니다 (받은 수 {len(raw['nodes'])}개)")
    nodes, by_id = [], {}
    for n in raw["nodes"]:
        if not isinstance(n, dict):
            raise FlowError(f"노드가 JSON 객체가 아닙니다 (받은 값 {str(n)[:40]!r})")
        nid = n.get("id")
        if not isinstance(nid, str) or not NODE_ID_RE.match(nid):
            raise FlowError(f"노드 번호는 소문자로 시작하는 소문자·숫자 16자 이내여야 합니다 (받은 값 {str(nid)[:40]!r})")
        if nid in by_id:
            raise FlowError(f"노드 번호 {nid} 가 두 번 나옵니다")
        nt = _node_type(n.get("type"))
        params = n.get("params") or {}
        if not isinstance(params, dict):
            raise FlowError(f"'{nt.title}' 노드({nid})의 설정이 JSON 객체가 아닙니다")
        try:
            values = gen.clean_params(nt.params, params)
        except gen.GenError as e:
            raise FlowError(f"'{nt.title}' 노드({nid}): {e}") from None
        node = {"id": nid, "type": nt.key, "params": values}
        for axis in ("x", "y"):                 # 화면에서의 자리. 실행과는 상관없다
            v = n.get(axis, 0)
            node[axis] = round(float(v), 1) if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e6 else 0.0
        if nt.source == "text":
            text = n.get("text") or ""
            if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
                raise FlowError(f"'글' 노드({nid})의 글은 {MAX_TEXT_CHARS:,}자까지입니다 (받은 길이 {len(str(text)):,}자)")
            node["text"] = text
            if strict and not text.strip():
                raise FlowError(f"'글' 노드({nid})가 비어 있습니다. 글을 적어 주세요.")
        if nt.source in UPLOAD_SOURCES:
            exts, what = UPLOAD_SOURCES[nt.source]
            path, name = n.get("path") or "", n.get("name") or ""
            if not isinstance(path, str) or not isinstance(name, str):
                raise FlowError(f"'{nt.title}' 노드({nid})의 경로가 글이 아닙니다")
            if path:
                if not SAFE_PATH_RE.match(path) or ".." in path.split("/") or len(path) > 300:
                    raise FlowError(f"'{nt.title}' 노드({nid})의 경로에 쓸 수 없는 글자가 있습니다 (받은 값 {path[:80]!r}). "
                                    f"영문자, 숫자, _ . / - 만 됩니다. {what}을 다시 올려 주세요.")
                gen._file_ext(path, exts)
            elif strict:
                raise FlowError(f"'{nt.title}' 노드({nid})에 {what}이 없습니다. {what}을 올려 주세요.")
            node["path"], node["name"] = path, name[:200]
        nodes.append(node)
        by_id[nid] = node

    edges, seen = [], set()
    for e in raw.get("edges", []):
        if not isinstance(e, dict):
            raise FlowError(f"연결이 JSON 객체가 아닙니다 (받은 값 {str(e)[:40]!r})")
        src, dst = by_id.get(e.get("from")), by_id.get(e.get("to"))
        if src is None or dst is None:
            raise FlowError(f"없는 노드를 잇는 연결입니다 ({e.get('from')!r} → {e.get('to')!r})")
        if src is dst:
            raise FlowError(f"{_label(src)}를 자기 자신에게 이을 수 없습니다")
        out = next((o for o in NODE_TYPES[src["type"]].outputs if o.key == e.get("out") and not o.hidden), None)
        inp = next((i for i in NODE_TYPES[dst["type"]].inputs if i.key == e.get("in")), None)
        if out is None:
            raise FlowError(f"{_label(src)}에는 {e.get('out')!r} 출력이 없습니다")
        if inp is None:
            raise FlowError(f"{_label(dst)}에는 {e.get('in')!r} 입력이 없습니다")
        if out.type != inp.type:
            raise FlowError(f"{_label(src)}의 '{out.label}'({PORT_TYPES[out.type]})은 {_label(dst)}의 '{inp.label}'"
                            f"({PORT_TYPES[inp.type]})에 이을 수 없습니다. 같은 종류끼리만 이어집니다.")
        key = (src["id"], out.key, dst["id"], inp.key)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"from": src["id"], "out": out.key, "to": dst["id"], "in": inp.key})
    graph = {"nodes": nodes, "edges": edges}
    for node in nodes:
        for inp in NODE_TYPES[node["type"]].inputs:
            n = sum(1 for e in edges if e["to"] == node["id"] and e["in"] == inp.key)
            if n > inp.max:
                raise FlowError(f"{_label(node)}의 '{inp.label}'에는 {inp.max}개까지 이을 수 있습니다 (받은 수 {n}개)")
            if strict and inp.required and n == 0:
                raise FlowError(f"{_label(node)}의 '{inp.label}'에 아무것도 이어져 있지 않습니다. "
                                f"{PORT_TYPES[inp.type]}을 내는 노드를 이어 주세요.")
    order_of(graph)
    jobs = [n for n in nodes if NODE_TYPES[n["type"]].kind]
    if len(jobs) > MAX_JOB_NODES:
        raise FlowError(f"GPU 작업이 되는 노드는 {MAX_JOB_NODES}개까지입니다 (받은 수 {len(jobs)}개)")
    if strict:
        if not jobs:
            made = ", ".join(f"'{t.title}'" for t in NODE_TYPES.values() if t.kind)
            raise FlowError(f"만드는 노드가 없습니다. {made} 가운데 하나를 넣어 주세요.")
        for node in nodes:
            if node["type"] == "video":
                linked = {e["in"] for e in edges if e["to"] == node["id"]}
                if "last" in linked and "first" not in linked:
                    raise FlowError(f"{_label(node)}에 끝 장면만 이어져 있습니다. 끝 장면을 쓰려면 첫 장면도 이어 주세요 "
                                    "(끝 장면만으로 끝나는 영상은 아직 만들 수 없습니다).")
                if linked & {"first", "last"} and linked & {"ref_images", "ref_videos", "ref_audios"}:
                    raise FlowError(f"{_label(node)}에 첫·끝 장면과 참고 자료가 함께 이어져 있습니다. 모델이 둘을 함께 받지 않습니다. "
                                    "첫 장면 그림을 '참고 그림'에 잇고 장면 글에 'the shot begins from <Picture 1>' 처럼 쓰세요.")
            if node["type"] == "image" and node["params"].get("view") and not any(
                    e["to"] == node["id"] and e["in"] == "refs" for e in edges):
                raise FlowError(f"{_label(node)}에 보는 방향을 골랐는데 참고 그림이 이어져 있지 않습니다. "
                                "다른 방향에서 볼 물체의 사진을 '참고 그림'에 이어 주세요.")
            if node["type"] == "sfx":
                links = [e for e in edges if e["to"] == node["id"]]
                linked = {e["in"] for e in links}
                if not linked & {"prompt", "video"}:
                    raise FlowError(f"{_label(node)}에 소리 설명도 영상도 이어져 있지 않습니다. 영어로 적은 소리 설명 글이나 영상을 이어 주세요.")
                seconds = node["params"].get("seconds")
                if "video" not in linked and seconds is not None and seconds > gen.SFX_TEXT_SECONDS:
                    raise FlowError(f"{_label(node)}: 글만으로 만드는 소리는 {gen.SFX_TEXT_SECONDS:g}초까지입니다 (받은 값 {seconds:g}초). "
                                    "길이를 줄이거나 영상을 이어 주세요.")
                for e in links:
                    src = by_id[e["from"]]
                    if e["in"] in ("prompt", "negative") and NODE_TYPES[src["type"]].source == "text" and gen.HANGUL_RE.search(src.get("text") or ""):
                        raise FlowError(f"{_label(node)}에 이은 {_label(src)}에 한글이 있습니다. Woosh 는 영어 설명으로만 배워서 한글을 알아듣지 못합니다. "
                                        "영어로 적어 주세요. 예) 나뭇잎 밟는 소리 → footsteps on dry leaves")
            if node["type"] == "polish":
                linked = {e["in"] for e in edges if e["to"] == node["id"]}
                if not linked & {"audio", "video"}:
                    raise FlowError(f"{_label(node)}에 다듬을 소리도 영상도 이어져 있지 않습니다. 곡·목소리·효과음이나 영상을 이어 주세요.")
                if {"audio", "video"} <= linked:
                    raise FlowError(f"{_label(node)}에 소리와 영상이 함께 이어져 있습니다. 한 번에 하나만 다듬습니다. 하나를 끊어 주세요.")
            if node["type"] == "song" and node["params"]["cot"] == "off" and any(
                    e["to"] == node["id"] and e["in"] == "score" for e in edges):
                raise FlowError(f"{_label(node)}에 악보를 이었는데 '악보를 먼저 쓰는 방식'이 '악보 없이 바로'입니다. "
                                "그 값을 '멜로디와 코드'나 '멜로디만'으로 바꾸거나 악보 연결을 끊어 주세요.")
    return graph


# ── 홈의 파일 ──

class _Sizes:
    """홈 기준 경로의 파일 크기. 같은 폴더는 한 번만 읽는다."""

    def __init__(self, settings: Settings, user: dict):
        self.s, self.user, self.folders = settings, user, {}

    def of(self, rel: str | None) -> int | None:
        if not rel:
            return None
        folder, _, name = rel.rpartition("/")
        if folder not in self.folders:
            self.folders[folder] = gen._entries(self.s, self.user, folder) or {}
        entry = self.folders[folder].get(name)
        return entry["size"] if entry is not None and not entry["is_dir"] else None


def _ensure_dir(settings: Settings, user: dict, parent: str, name: str) -> None:
    if name not in (gen._entries(settings, user, parent) or {}):
        fs.make_dir(settings, user, "home", parent, name)


def save_input(settings: Settings, user: dict, filename: str, src: BinaryIO) -> dict:
    """'사진'·'소리 파일'·'영상 파일' 노드에 올린 파일을 홈의 입력 폴더에 저장한다. 이름은 날짜와 무작위 글자로 다시 짓는다."""
    name_lower = (filename or "").lower()
    exts = (gen.AUDIO_EXTS if name_lower.endswith(gen.AUDIO_EXTS) else gen.VIDEO_EXTS if name_lower.endswith(gen.VIDEO_EXTS)
            else gen.IMAGE_EXTS)
    ext = gen._file_ext(filename, exts)
    d = settings.flow_inputs_dir.strip("/")
    _ensure_dir(settings, user, "", d)
    name = f"{gen.new_key(time.time())}{ext}"
    saved = fs.save_upload(settings, user, "home", d, name, src)
    if saved["size"] == 0:
        fs.delete_path(settings, user, "home", f"{d}/{name}", False)
        raise FlowError("빈 파일입니다. 다시 골라 주세요.")
    shown = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()[:200]
    return {"path": f"{d}/{name}", "name": shown, "size": saved["size"]}


# ── 실행 ──

def _sig(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def start_run(db: DB, sched: Scheduler, settings: Settings, user: dict, graph, name: str = "") -> dict:
    """그래프를 검사하고 실행을 시작한다. 글 노드와 사진 노드는 바로 끝나고, 나머지는 차례가 되면 큐에 들어간다."""
    g = clean_graph(graph, strict=True)
    name = " ".join((name or "").split())[:80]
    sizes = _Sizes(settings, user)
    for node in g["nodes"]:
        nt = NODE_TYPES[node["type"]]
        if nt.kind and not gen.ready(settings, user, gen.KINDS[nt.kind]):
            raise FlowError(f"'{nt.title}' 노드를 쓸 수 없습니다. {gen.setup_hint(settings, gen.KINDS[nt.kind])}")
        if nt.source in UPLOAD_SOURCES and not sizes.of(node["path"]):
            what = UPLOAD_SOURCES[nt.source][1]
            raise FlowError(f"'{nt.title}' 노드({node['id']})의 {what}이 홈에 없습니다 ({node['path']}). {what}을 다시 올려 주세요.")

    now = time.time()
    key = gen.new_key(now)
    base = settings.flow_dir.strip("/")
    run_dir = f"{base}/{key}"
    _ensure_dir(settings, user, "", base)
    fs.make_dir(settings, user, "home", base, key)

    by_id = {n["id"]: n for n in g["nodes"]}
    sigs: dict[str, str] = {}
    runner: dict[str, str] = {}                 # 종류 → 실행 스크립트 판 지문 (스크립트가 고쳐지면 지난 결과를 다시 쓰지 않는다)
    rows = []
    for nid in order_of(g):
        node = by_id[nid]
        nt = NODE_TYPES[node["type"]]
        status, outputs = "waiting", {}
        if nt.source == "text":
            fs.save_upload(settings, user, "home", run_dir, f"{nid}.text.txt", io.BytesIO(node["text"].strip().encode("utf-8")))
            sigs[nid] = _sig({"t": "text", "text": node["text"].strip()})
            status, outputs = "succeeded", {"text": f"{run_dir}/{nid}.text.txt"}
        elif nt.source in UPLOAD_SOURCES:
            sigs[nid] = _sig({"t": nt.source, "path": node["path"], "size": sizes.of(node["path"])})
            status, outputs = "succeeded", {nt.outputs[0].key: node["path"]}
        else:
            ins = {i.key: [[sigs[e["from"]], e["out"]] for e in g["edges"] if e["to"] == nid and e["in"] == i.key]
                   for i in nt.inputs}
            if nt.kind not in runner:
                runner[nt.kind] = gen.runner_fingerprint(settings, user, gen.KINDS[nt.kind])
            sigs[nid] = _sig({"t": nt.key, "v": VERSION, "params": node["params"], "in": ins, "r": runner[nt.kind]})
        rows.append((nid, nt.key, sigs[nid], status, json.dumps(outputs)))

    with _lock:
        cur = db.execute("INSERT INTO flow_runs(user, name, dir, graph, status, created_at) VALUES (?,?,?,?,'running',?)",
                         (user["name"], name, run_dir, json.dumps(g, ensure_ascii=False), now))
        run_id = cur.lastrowid
        for nid, type_key, sig, status, outputs in rows:
            db.execute("INSERT INTO flow_nodes(run_id, user, node, type, sig, status, outputs) VALUES (?,?,?,?,?,?,?)",
                       (run_id, user["name"], nid, type_key, sig, status, outputs))
        db.log_event("flow.run", user["name"], f"#{run_id} 노드 {len(rows)}개")
        _advance_run(db, sched, settings, db.one("SELECT * FROM flow_runs WHERE id=?", (run_id,)))
    return run_detail(db, sched, settings, user, run_id)


def _reusable(db: DB, sizes: _Sizes, user: dict, sig: str, nt: NodeType, run_id: int) -> tuple[dict, int] | None:
    """지난 실행에서 똑같은 노드가 낸 결과가 남아 있으면 (결과 파일들, 그 실행 번호)."""
    for old in db.all("""SELECT run_id, outputs FROM flow_nodes WHERE user=? AND sig=? AND run_id<>?
                         AND status IN ('succeeded','reused') ORDER BY id DESC LIMIT 8""", (user["name"], sig, run_id)):
        have = {k: p for k, p in json.loads(old["outputs"] or "{}").items() if sizes.of(p)}
        if all(o.key in have for o in nt.outputs if not o.optional and not o.hidden):
            return have, old["run_id"]
    return None


@dataclass
class _Ready:
    """입력이 다 갖춰져 이번에 작업으로 넣을 노드 하나."""
    row: dict
    nt: NodeType
    kind: gen.Kind
    d: str                      # 작업을 돌릴 폴더 (홈 기준)
    args: list[str]             # 실행 스크립트에 넘길 옵션 (`--옵션 값` 글자들)
    outputs: dict
    vram_gb: float

    def command(self, settings: Settings) -> str:
        return f"./{shlex.quote(self.kind.script(settings))} " + " ".join(self.args)


def _submit(sched: Scheduler, user: dict, items: list[_Ready], put, name: str, command: str) -> int:
    """작업 하나를 큐에 넣고, 그 작업으로 만들 노드들에 작업 번호를 적는다. 넣은 작업 수 (0 또는 1)."""
    try:
        job = sched.submit(user, name=name, command=command, vram_gb=max(it.vram_gb for it in items), workdir=items[0].d)
    except JobError as e:
        for it in items:
            put(it.row, status="failed", error=f"작업을 큐에 넣지 못했습니다: {e}")
        return 0
    for it in items:
        put(it.row, status="queued", job_id=job["id"], outputs=json.dumps(it.outputs))
    return 1


def _runner_batches(settings: Settings, user: dict, kind: gen.Kind, flag: str) -> bool:
    """이 사용자의 실행 스크립트가 묶음 옵션을 아는지."""
    return gen.script_knows(settings, user, kind, flag)


def _batch_progress(kind: gen.Kind, log_tail: str, pos: int, n: int) -> dict | None:
    """묶음 작업의 로그에서 이 노드(묶음 안의 pos 번째)의 진행 단계를 읽는다.

    실행 스크립트는 한 장을 시작할 때마다 '묶음 k/n' 줄을 찍는다. 앞 차례면 기다리는 중, 지난 차례면 다 그린 것이다.
    """
    marks = BATCH_MARK_RE.findall(log_tail)
    at = int(marks[-1]) if marks else 1
    if pos > at:
        return {"stage": f"앞 노드를 그리는 중 (묶음 {at}/{n}, 이 노드는 {pos}번째)", "step": 1, "steps": 1, "percent": 0,
                "waiting": True}
    if pos < at:
        return {"stage": f"다 그림 (묶음 {pos}/{n}, 나머지를 그리는 중)", "step": 1, "steps": 1, "percent": 100}
    tail = log_tail[log_tail.rfind(f"묶음 {at}/"):] if marks else log_tail
    p = kind.progress(tail)
    if p is not None:
        p["stage"] = f"{p['stage']} (묶음 {pos}/{n})"
    return p


def _advance_run(db: DB, sched: Scheduler, settings: Settings, run: dict) -> int:
    """실행 하나를 한 걸음 나아가게 한다. 새로 큐에 넣은 작업 수를 돌려준다."""
    user = db.get_user(run["user"])
    graph = json.loads(run["graph"])
    by_id = {n["id"]: n for n in graph["nodes"]}
    rows = {r["node"]: r for r in db.all("SELECT * FROM flow_nodes WHERE run_id=?", (run["id"],))}
    sizes = _Sizes(settings, user)
    submitted = 0
    ready: dict[tuple, list[_Ready]] = {}               # 이번에 넣을 노드 (묶을 수 있는 것끼리 한 칸)

    def put(row: dict, **fields) -> None:
        row.update(fields)
        sets = ", ".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE flow_nodes SET {sets} WHERE id=?", (*fields.values(), row["id"]))

    def batched(row: dict) -> bool:
        return sum(1 for r in rows.values() if r["job_id"] == row["job_id"]) > 1

    for nid in order_of(graph):
        row, node = rows[nid], by_id[nid]
        nt = NODE_TYPES[node["type"]]
        if row["status"] in LIVE:
            job = sched.get(row["job_id"])
            if job is None:
                put(row, status="failed", error="작업 기록을 찾지 못했습니다")
            elif job["status"] in LIVE:
                if job["status"] != row["status"]:
                    put(row, status=job["status"])
            elif job["status"] == "succeeded" or (job["status"] == "failed" and batched(row)):
                # 묶음 작업은 한 장이 실패하면 작업 전체가 '실패'로 끝난다. 그래도 결과를 낸 노드는 성공이다
                have = {k: p for k, p in json.loads(row["outputs"]).items() if sizes.of(p)}
                missing = [o.label for o in nt.outputs if not o.optional and not o.hidden and o.key not in have]
                if missing and job["status"] == "failed":
                    put(row, status="failed", outputs=json.dumps(have),
                        error=job["error"] or "여러 노드를 한 작업으로 돌렸는데 이 노드의 결과만 나오지 않았습니다. "
                                              "로그에서 '실패' 줄을 확인하세요.")
                elif missing:
                    put(row, status="failed", outputs=json.dumps(have),
                        error=f"작업은 끝났지만 결과 파일이 없습니다 ({', '.join(missing)}). 로그를 확인하세요.")
                else:
                    put(row, status="succeeded", outputs=json.dumps(have))
            elif job["status"] == "cancelled":
                put(row, status="cancelled")
            else:
                put(row, status="failed", error=job["error"] or f"실행 중 오류가 났습니다 (종료 코드 {job['exit_code']}). 로그를 확인하세요.")
            continue
        if row["status"] != "waiting":
            continue
        ups = [rows[e["from"]] for e in graph["edges"] if e["to"] == nid]
        if any(u["status"] in STOPPED for u in ups):
            put(row, status="skipped", error="앞 노드가 결과를 내지 못해 건너뛰었습니다")
            continue
        if not all(u["status"] in OK for u in ups):
            continue                                    # 앞 노드가 아직 돌고 있다
        # 입력 파일을 모은다 (연결을 그린 순서대로)
        inputs: dict[str, list[str]] = {}
        problem = None
        for inp in nt.inputs:
            for e in graph["edges"]:
                if e["to"] != nid or e["in"] != inp.key:
                    continue
                path = json.loads(rows[e["from"]]["outputs"]).get(e["out"])
                if path:
                    inputs.setdefault(inp.key, []).append(path)
                elif inp.required:
                    src = NODE_TYPES[by_id[e["from"]]["type"]]
                    label = next(o.label for o in src.outputs if o.key == e["out"])
                    problem = f"앞의 '{src.title}' 노드({e['from']})가 '{label}'을 내지 않았습니다"
        if problem:
            put(row, status="failed", error=problem)
            continue
        again = _reusable(db, sizes, user, row["sig"], nt, run["id"])
        if again is not None:
            put(row, status="reused", outputs=json.dumps(again[0]), reused_from=again[1])
            continue
        kind = gen.KINDS[nt.kind]
        if nt.needs_flag and not gen.script_knows(settings, user, kind, nt.needs_flag):
            put(row, status="failed", error=f"홈의 {kind.folder(settings)}/{kind.script(settings)} 가 '{nt.title}'({nt.needs_flag})를 "
                                            f"모르는 예전 판입니다. 새 판 실행 스크립트를 받아 두세요.")
            continue
        d = kind.folder(settings)
        args, outputs = [], {}
        for inp in nt.inputs:
            args += [f"{inp.flag} {posixpath.relpath(p, d)}" for p in inputs.get(inp.key, [])]
        roles = [inp.role for inp in nt.inputs if inp.role and inputs.get(inp.key)]
        if nt.roles_flag and len(roles) > 1:            # 앞 사진 하나만 이었으면 한 장의 길로 돈다
            args.append(f"{nt.roles_flag} {','.join(roles)}")
        for out in nt.outputs:
            outputs[out.key] = f"{run['dir']}/{nid}.{out.key}{out.ext}"
            args.append(f"{out.flag} {posixpath.relpath(outputs[out.key], d)}")
        args += [*nt.fixed, *gen.param_flags(nt.params, node["params"])]
        group = (nt.key, *(json.dumps(node["params"].get(k)) for k in nt.batch_by)) if nt.batch_flag else (nid,)
        ready.setdefault(group, []).append(_Ready(row, nt, kind, d, args, outputs,
                                                  nt.vram_gb or kind.vram_gb(settings, len(roles) if nt.roles_flag else 1,
                                                                             node["params"])))

    for items in ready.values():
        first = items[0]
        if len(items) > 1 and not _runner_batches(settings, user, first.kind, first.nt.batch_flag):
            items, alone = [], items                    # 실행 스크립트가 묶음을 모른다 (예전 판): 하나씩 넣는다
        else:
            alone = []
        for it in alone:
            submitted += _submit(sched, user, [it], put, f"{it.nt.title} (노드)", it.command(settings))
        if not items:
            continue
        if len(items) == 1:
            submitted += _submit(sched, user, items, put, f"{first.nt.title} (노드)", first.command(settings))
            continue
        # 묶음 파일: 노드마다 한 장을 만들 때와 같은 옵션 목록. bash 가 명령 줄을 나누는 것과 똑같이 나눈다
        name = f"batch-{first.row['node']}-{len(items)}.json"
        body = json.dumps([shlex.split(" ".join(it.args)) for it in items], ensure_ascii=False, indent=1)
        try:
            fs.save_upload(settings, user, "home", run["dir"], name, io.BytesIO(body.encode("utf-8")))
        except fs.FileError as e:
            for it in items:
                put(it.row, status="failed", error=f"묶음 파일을 쓰지 못했습니다: {e}")
            continue
        batch_file = posixpath.relpath(run["dir"] + "/" + name, first.d)
        command = f"./{shlex.quote(first.kind.script(settings))} {first.nt.batch_flag} {shlex.quote(batch_file)}"
        submitted += _submit(sched, user, items, put, f"{first.nt.title} ×{len(items)} (노드)", command)

    states = [r["status"] for r in rows.values()]
    if all(s in OK or s in STOPPED for s in states):
        final = "failed" if "failed" in states else "cancelled" if "cancelled" in states else "succeeded"
        db.execute("UPDATE flow_runs SET status=?, finished_at=? WHERE id=?", (final, time.time(), run["id"]))
        db.log_event("flow.done", run["user"], f"#{run['id']} {final}")
    return submitted


def _abort_run(db: DB, sched: Scheduler, run: dict, why: str) -> None:
    """더 나아갈 수 없는 실행을 실패로 끝낸다. 큐에 있는 작업은 취소하고, 시작하지 않은 노드는 하지 않는다."""
    for r in db.all("SELECT * FROM flow_nodes WHERE run_id=? AND status IN ('waiting','queued','running')", (run["id"],)):
        if r["job_id"]:
            try:
                sched.cancel(r["job_id"], run["user"])
            except JobError:
                pass                                    # 그사이에 끝난 작업
        db.execute("UPDATE flow_nodes SET status='failed', error=? WHERE id=?", (why[:300], r["id"]))
    db.execute("UPDATE flow_runs SET status='failed', finished_at=? WHERE id=?", (time.time(), run["id"]))
    db.log_event("flow.done", run["user"], f"#{run['id']} failed")


def advance(db: DB, sched: Scheduler, settings: Settings) -> int:
    """돌고 있는 실행을 모두 한 걸음씩 나아가게 한다 (감시 루프가 작업 큐를 돌린 뒤에 부른다). 새로 넣은 작업 수.

    실행 하나가 탈이 나도 다른 실행은 계속 나아가야 하므로, 실행마다 따로 오류를 받는다.
    """
    submitted = 0
    with _lock:
        for run in db.all("SELECT * FROM flow_runs WHERE status='running' ORDER BY id"):
            try:
                submitted += _advance_run(db, sched, settings, run)
            except (FlowError, KeyError, TypeError, ValueError) as e:
                # 저장된 그래프가 지금의 노드 정의와 맞지 않는다 (판이 바뀌어 노드 종류나 설정 이름이 없어졌다).
                # 다음 주기에 다시 해도 같으므로 이 실행은 여기서 끝낸다.
                _abort_run(db, sched, run, f"이 실행의 그래프를 지금 판에서 읽지 못했습니다 ({type(e).__name__}: {e}). 그래프를 다시 열어 실행해 주세요.")
            except Exception:
                log.exception("실행 #%s 을 나아가게 하지 못했습니다. 다음 주기에 다시 합니다", run["id"])
    return submitted


def _run_of(db: DB, user: dict, run_id: int) -> dict:
    run = db.one("SELECT * FROM flow_runs WHERE id=? AND user=?", (run_id, user["name"]))
    if run is None:
        raise FlowError(f"없는 실행입니다 (#{run_id})")
    return run


def run_detail(db: DB, sched: Scheduler, settings: Settings, user: dict, run_id: int) -> dict:
    """실행 하나의 지금 모습: 그래프와, 노드마다의 상태·결과 파일·진행 단계."""
    with _lock:
        run = _run_of(db, user, run_id)
        if run["status"] == "running":
            _advance_run(db, sched, settings, run)
            run = _run_of(db, user, run_id)
    sizes = _Sizes(settings, user)
    nodes = {}
    found = db.all("""SELECT n.*, j.status AS job_status, j.started_at, j.finished_at, j.peak_vram_gb
                      FROM flow_nodes n LEFT JOIN jobs j ON j.id = n.job_id WHERE n.run_id=? ORDER BY n.id""", (run_id,))
    mates: dict[int, list[str]] = {}                    # 작업 번호 → 그 작업으로 만드는 노드들 (묶음 안의 차례대로)
    for r in found:
        if r["job_id"]:
            mates.setdefault(r["job_id"], []).append(r["node"])
    for r in found:
        together = mates.get(r["job_id"], [])
        info = {"status": r["status"], "job_id": r["job_id"], "error": r["error"], "reused_from": r["reused_from"],
                "started_at": r["started_at"], "finished_at": r["finished_at"], "peak_vram_gb": r["peak_vram_gb"],
                "outputs": {}, "progress": None, "wait_reason": None,
                "batch": {"pos": together.index(r["node"]) + 1, "n": len(together)} if len(together) > 1 else None}
        if r["status"] in OK:
            info["outputs"] = {k: {"path": p, "size": sizes.of(p)} for k, p in json.loads(r["outputs"] or "{}").items()}
        if r["status"] == "queued":
            info["wait_reason"] = sched.reasons.get(r["job_id"])
        if r["status"] == "running":
            nt, job = NODE_TYPES.get(r["type"]), sched.get(r["job_id"])
            try:
                if nt and nt.kind and job and info["batch"]:
                    info["progress"] = _batch_progress(gen.KINDS[nt.kind], sched.logs(job, BATCH_TAIL_LINES),
                                                       info["batch"]["pos"], info["batch"]["n"])
                elif nt and nt.kind and job:
                    info["progress"] = gen.KINDS[nt.kind].progress(sched.logs(job, gen.PROGRESS_TAIL_LINES))
            except Exception:
                pass                                    # 로그를 못 읽어도 상태는 보여 준다
        nodes[r["node"]] = info
    return {"id": run["id"], "name": run["name"], "status": run["status"], "created_at": run["created_at"],
            "finished_at": run["finished_at"], "dir": run["dir"], "graph": json.loads(run["graph"]), "nodes": nodes}


def list_runs(db: DB, user: dict, limit: int = 30) -> list[dict]:
    runs = db.all("SELECT id, name, status, created_at, finished_at FROM flow_runs WHERE user=? ORDER BY id DESC LIMIT ?",
                  (user["name"], limit))
    for run in runs:
        rows = db.all("SELECT type, status FROM flow_nodes WHERE run_id=?", (run["id"],))
        made = [r for r in rows if NODE_TYPES.get(r["type"]) and NODE_TYPES[r["type"]].kind]
        run["nodes"] = len(rows)
        run["steps"] = [NODE_TYPES[r["type"]].title for r in made]
        run["done"] = sum(1 for r in made if r["status"] in OK)
        run["reused"] = sum(1 for r in made if r["status"] == "reused")
    return runs


def cancel_run(db: DB, sched: Scheduler, settings: Settings, user: dict, run_id: int) -> dict:
    """돌고 있는 작업을 취소하고, 아직 시작하지 않은 노드는 하지 않는다."""
    with _lock:
        run = _run_of(db, user, run_id)
        if run["status"] != "running":
            raise FlowError(f"이미 끝난 실행입니다 (#{run_id})")
        for r in db.all("SELECT * FROM flow_nodes WHERE run_id=? AND status IN ('waiting','queued','running')", (run_id,)):
            if r["job_id"]:
                try:
                    sched.cancel(r["job_id"], user["name"])
                except JobError:
                    pass                                # 그사이에 끝난 작업
            db.execute("UPDATE flow_nodes SET status='cancelled' WHERE id=?", (r["id"],))
        db.execute("UPDATE flow_runs SET status='cancelled', finished_at=? WHERE id=?", (time.time(), run_id))
        db.log_event("flow.cancel", user["name"], f"#{run_id}")
    return run_detail(db, sched, settings, user, run_id)


def delete_run(db: DB, settings: Settings, user: dict, run_id: int) -> None:
    """실행 기록을 지우고 그 실행 폴더의 파일도 지운다. 다른 실행이 다시 쓰고 있는 결과 파일은 남긴다."""
    with _lock:
        run = _run_of(db, user, run_id)
        if run["status"] == "running":
            raise FlowError("아직 끝나지 않은 실행입니다. 먼저 실행을 취소하세요.")
        kept = set()
        for r in db.all("SELECT outputs FROM flow_nodes WHERE user=? AND run_id<>?", (user["name"], run_id)):
            kept.update(json.loads(r["outputs"] or "{}").values())
        left = 0
        for name, entry in (gen._entries(settings, user, run["dir"]) or {}).items():
            rel = f"{run['dir']}/{name}"
            if rel in kept:
                left += 1
                continue
            try:
                fs.delete_path(settings, user, "home", rel, entry["is_dir"])
            except fs.FileError:
                left += 1
        if not left:
            try:
                fs.delete_path(settings, user, "home", run["dir"], False)
            except fs.FileError:
                pass
        db.execute("DELETE FROM flow_nodes WHERE run_id=?", (run_id,))
        db.execute("DELETE FROM flow_runs WHERE id=?", (run_id,))
        db.log_event("flow.delete", user["name"], f"#{run_id}")


# ── 저장한 그래프 ──

def list_flows(db: DB, user: dict) -> list[dict]:
    rows = db.all("SELECT id, name, graph, updated_at FROM flows WHERE user=? ORDER BY name", (user["name"],))
    for r in rows:
        r["graph"] = json.loads(r["graph"])
    return rows


def save_flow(db: DB, user: dict, name: str, graph) -> dict:
    """그래프를 이름을 붙여 저장한다. 같은 이름이 있으면 덮어쓴다. 만들다 만 그래프도 저장된다."""
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 60:
        raise FlowError(f"그래프의 이름은 1~60자여야 합니다 (받은 길이 {len(name)}자)")
    text = json.dumps(clean_graph(graph, strict=False), ensure_ascii=False)
    now = time.time()
    have = db.one("SELECT id FROM flows WHERE user=? AND name=?", (user["name"], name))
    if have is None:
        n = db.one("SELECT COUNT(*) AS n FROM flows WHERE user=?", (user["name"],))["n"]
        if n >= MAX_FLOWS:
            raise FlowError(f"그래프는 {MAX_FLOWS}개까지 저장할 수 있습니다. 쓰지 않는 것을 지워 주세요.")
        db.execute("INSERT INTO flows(user, name, graph, created_at, updated_at) VALUES (?,?,?,?,?)",
                   (user["name"], name, text, now, now))
    else:
        db.execute("UPDATE flows SET graph=?, updated_at=? WHERE id=?", (text, now, have["id"]))
    return next(f for f in list_flows(db, user) if f["name"] == name)


def delete_flow(db: DB, user: dict, flow_id: int) -> None:
    cur = db.execute("DELETE FROM flows WHERE id=? AND user=?", (flow_id, user["name"]))
    if cur.rowcount == 0:
        raise FlowError(f"없는 그래프입니다 (#{flow_id})")


def state(db: DB, settings: Settings, user: dict) -> dict:
    """노드 편집기 화면이 처음에 읽는 것: 노드 종류의 정의, 저장한 그래프, 지난 실행."""
    return {"types": [t.spec(settings, user) for t in NODE_TYPES.values()],
            "port_types": PORT_TYPES,
            "limits": {"nodes": MAX_NODES, "job_nodes": MAX_JOB_NODES, "text_chars": MAX_TEXT_CHARS},
            "flows": list_flows(db, user),
            "runs": list_runs(db, user)}
