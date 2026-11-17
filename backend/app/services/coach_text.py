"""코칭 멘트 생성 — Claude 우선, 안 되면 로컬 Ollama 로 폴백한다."""

import logging
import os
import re

import httpx

from app.services.claude_ai import claude_text

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT_SEC = 90.0

# 프롬프트에 '이모지 금지'라 적어도 로컬 모델이 종종 넣는다.
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # 그림문자·이모티콘·교통·보조기호
    "\U00002600-\U000027BF"   # 기타기호 + 딩뱃
    "\U0001F1E6-\U0001F1FF"   # 국기
    "\U0000FE00-\U0000FE0F"   # 변형 선택자
    "\U00002190-\U000021FF"   # 화살표류
    "\U00002B00-\U00002BFF"   # 별·도형 등
    "]+",
    flags=re.UNICODE,
)


class CoachTextError(Exception):
    """Claude 와 로컬 모델 어느 쪽에서도 멘트를 만들지 못했다."""


def _clean_local_output(raw: str) -> str:
    text = raw.replace("**", "").replace("*", "")
    text = _EMOJI.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" \"'")


def _generate_with_ollama(prompt: str, max_tokens: int) -> str:
    try:
        response = httpx.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "keep_alive": "10m",  # 모델을 메모리에 유지 → 다음 호출의 재로딩 지연 제거
                "options": {"temperature": 0.7, "num_predict": max_tokens, "stop": ["\n\n"]},
            },
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        response.raise_for_status()
        raw = response.json().get("response") or ""
    except (httpx.HTTPError, ValueError) as exc:
        raise CoachTextError(f"Ollama 호출 실패 ({OLLAMA_URL}): {exc}") from exc

    text = _clean_local_output(raw)
    if not text:
        raise CoachTextError("Ollama 가 빈 응답을 돌려줬습니다.")
    return text


def generate_coach_text(prompt: str, *, max_tokens: int = 300, local_max_tokens: int = 150) -> str:
    """짧은 한국어 코칭 멘트를 만든다. 두 경로 모두 실패하면 CoachTextError."""
    text = claude_text(prompt, max_tokens=max_tokens)
    if text:
        return text
    return _generate_with_ollama(prompt, local_max_tokens)


def best_effort(task, *args) -> None:
    """BackgroundTasks 용 실행기. 응답이 이미 나간 뒤라 실패를 사용자에게 알릴 수 없으므로
    기록만 남긴다 — 사용자는 화면의 '다시 생성'으로 재시도한다."""
    try:
        task(*args)
    except CoachTextError as exc:
        logger.warning("백그라운드 코칭 멘트 생성 실패 (%s%r): %s", task.__name__, args, exc)
