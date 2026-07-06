"""Claude API 공용 클라이언트 — 모든 대형 LLM 호출이 이 모듈을 거친다.

정책: Claude 우선, 실패 시 None 반환 → 호출자가 기존 로컬 모델(Ollama·EfficientNet·규칙)로
폴백한다. ANTHROPIC_API_KEY 가 없으면 모든 함수가 조용히 None (무료/로컬 모드 그대로 동작).

모델은 기능별 혼합:
- 텍스트 코칭 멘트 / 음식 사진 인식: Haiku 4.5 (저비용, vision 지원)
- 폼체크 자세 판정: Opus 4.8 (정확도 우선)
환경변수 CLAUDE_TEXT_MODEL / CLAUDE_VISION_MODEL / CLAUDE_FORM_MODEL 로 교체 가능.
"""

import base64
import json
import os
from typing import Optional

TEXT_MODEL = os.getenv("CLAUDE_TEXT_MODEL", "claude-haiku-4-5")
VISION_MODEL = os.getenv("CLAUDE_VISION_MODEL", "claude-haiku-4-5")
FORM_MODEL = os.getenv("CLAUDE_FORM_MODEL", "claude-opus-4-8")

_client = None
if os.getenv("ANTHROPIC_API_KEY"):
    try:
        import anthropic

        _client = anthropic.Anthropic()
    except Exception as exc:  # SDK 미설치 등 — 로컬 모드로 폴백
        print(f"[claude_ai] 초기화 실패, 로컬 모델로 동작: {exc}")


def is_available() -> bool:
    return _client is not None


def _first_text(response) -> Optional[str]:
    text = next((b.text for b in response.content if b.type == "text"), "").strip()
    return text or None


def claude_text(prompt: str, max_tokens: int = 400) -> Optional[str]:
    """코칭 멘트 등 짧은 한국어 텍스트 생성. 실패 시 None."""
    if _client is None:
        return None
    try:
        response = _client.messages.create(
            model=TEXT_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return _first_text(response)
    except Exception as exc:
        print(f"[claude_ai] 텍스트 호출 실패: {exc}")
        return None


_FOOD_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "food_name": {"type": "string", "description": "음식 이름 (한국어)"},
                    "weight": {"type": "integer", "description": "사진 기준 추정 섭취량(g)"},
                    "calories": {"type": "number", "description": "100g당 칼로리(kcal)"},
                    "carbs": {"type": "number", "description": "100g당 탄수화물(g)"},
                    "protein": {"type": "number", "description": "100g당 단백질(g)"},
                    "fat": {"type": "number", "description": "100g당 지방(g)"},
                    "confidence": {"type": "number", "description": "확신도 0~100"},
                },
                "required": ["food_name", "weight", "calories", "carbs", "protein", "fat", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def claude_food_analyze(image_bytes: bytes, media_type: str = "image/jpeg") -> Optional[list]:
    """음식 사진 → [{food_name, weight, calories, carbs, protein, fat, confidence}, ...].

    영양소는 기존 파이프라인과 동일하게 100g 기준 값. 실패 시 None.
    """
    if _client is None:
        return None
    try:
        response = _client.messages.create(
            model=VISION_MODEL,
            max_tokens=2000,
            output_config={"format": {"type": "json_schema", "schema": _FOOD_SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(image_bytes).decode(),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "사진 속 음식을 모두 식별하세요. 각 음식에 대해 한국어 음식명, "
                                "사진 기준 추정 섭취량(g), 그리고 100g당 영양소(칼로리·탄수화물·단백질·지방)를 "
                                "일반적인 영양 성분 기준으로 추정해 주세요. 음식이 아닌 물체는 제외합니다."
                            ),
                        },
                    ],
                }
            ],
        )
        text = _first_text(response)
        if not text:
            return None
        items = json.loads(text).get("items", [])
        return items or None
    except Exception as exc:
        print(f"[claude_ai] 음식 인식 호출 실패: {exc}")
        return None


# 축 키는 pose_pipeline.CATEGORY_ORDER 와 동일해야 함
_FORM_AXES = ["Stability", "ROM", "Movement Quality", "Posture", "Core"]

_FORM_SCHEMA = {
    "type": "object",
    "properties": {
        "cat_scores": {
            "type": "object",
            "properties": {axis: {"type": "integer"} for axis in _FORM_AXES},
            "required": _FORM_AXES,
            "additionalProperties": False,
        },
        "cat_details": {
            "type": "object",
            "properties": {axis: {"type": "string"} for axis in _FORM_AXES},
            "required": _FORM_AXES,
            "additionalProperties": False,
        },
        "overall": {"type": "string", "description": "한 줄 총평 (한국어)"},
    },
    "required": ["cat_scores", "cat_details", "overall"],
    "additionalProperties": False,
}


def claude_form_judge(
    exercise: str,
    angle_summary: str,
    keyframes: Optional[list] = None,
    media_type: str = "image/jpeg",
) -> Optional[dict]:
    """폼체크 판정 — 관절 각도 요약 + 대표 프레임을 보고 5축 점수·피드백 생성.

    반환: {"cat_scores": {축: 0~100}, "cat_details": {축: 코멘트}, "overall": 총평}
    실패 시 None → 호출자가 기존 RF/규칙 판정으로 폴백.
    """
    if _client is None:
        return None
    try:
        content = []
        for frame_bytes in (keyframes or [])[:5]:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(frame_bytes).decode(),
                    },
                }
            )
        content.append(
            {
                "type": "text",
                "text": (
                    f"당신은 웨이트 트레이닝 자세 분석 전문가입니다. 운동: {exercise}\n\n"
                    "아래는 MediaPipe로 추출한 관절 각도·동작 데이터 요약이고, 이미지는 수행 중 대표 프레임입니다.\n\n"
                    f"[동작 데이터]\n{angle_summary}\n\n"
                    "다섯 가지 축 Stability(안정성)·ROM(가동범위)·Movement Quality(동작품질)·"
                    "Posture(자세)·Core(코어)로 각각 0~100점을 매기세요.\n\n"
                    "[채점 기준]\n"
                    "- 90~100: 교정할 점이 거의 없는 우수한 자세\n"
                    "- 75~89: 전반적으로 좋고 사소한 교정만 필요\n"
                    "- 60~74: 뚜렷이 교정할 문제가 1~2개 있음\n"
                    "- 60 미만: 부상 위험이 있는 명확한 문제\n"
                    "채점은 관대하게 — 애매하면 높은 쪽 점수를 주고, 잘한 축은 아낌없이 90점 이상을 주세요. "
                    "단, 부상 위험이 보이는 축만은 점수를 낮춰 분명히 경고하세요.\n\n"
                    "축마다 구체적인 한국어 교정 코멘트를 1~2문장으로 작성하세요. "
                    "코멘트는 '~하세요' 어미로, 무엇을 어떻게 고칠지 신체 부위를 짚어 구체적으로. "
                    "마지막으로 한 줄 총평을 작성하세요. 이모지·마크다운 금지."
                ),
            }
        )
        response = _client.messages.create(
            model=FORM_MODEL,
            max_tokens=3000,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": _FORM_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
        text = _first_text(response)
        if not text:
            return None
        return json.loads(text)
    except Exception as exc:
        print(f"[claude_ai] 폼체크 판정 호출 실패: {exc}")
        return None
