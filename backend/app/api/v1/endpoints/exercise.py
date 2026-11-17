import logging
import os
import tempfile
from datetime import date as date_t
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.constants import CAMERA_GUIDE, EXERCISE_CATEGORIES, GUIDE_IMAGES
from app.database import get_db
from app.models.formcheck_log import FormCheckLog
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

# 영상 분석 사이드카(YOLO+MediaPipe+모델/룰, 3.10 venv). 업로드 영상을 통째로 위임.
VIDEO_SERVICE_URL = os.environ.get("VIDEO_SERVICE_URL", "http://127.0.0.1:8003")

MAX_VIDEO_BYTES = 200 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v"}
UPLOAD_CHUNK_BYTES = 1024 * 1024


@router.get("/list")
async def get_exercise_list():
    """부위별 운동 목록과 각 운동별 상세 가이드(사진 경로 포함) 반환"""
    return {
        "categories": EXERCISE_CATEGORIES,
        "guides": CAMERA_GUIDE,
        "images": {
            "스쿼트": "squat_guide.jpg",
            "벤치프레스": "bench_guide.jpg",
            "데드리프트": "dead_guide.jpg",
        },
    }


@router.get("/exercises")
async def get_exercises():
    """프론트엔드에서 부위별 운동 선택창을 만들 때 사용"""
    return {
        "categories": EXERCISE_CATEGORIES,
        "guides": CAMERA_GUIDE,
    }


@router.get("/info")
async def get_exercise_info():
    """모든 운동 카테고리, 가이드 문구, 이미지 경로를 한 번에 전달"""
    return {
        "categories": EXERCISE_CATEGORIES,
        "guides": CAMERA_GUIDE,
        "images": GUIDE_IMAGES,
    }


@router.get("/analyze_progress")
async def analyze_progress(job_id: str, current_user: User = Depends(get_current_user)):
    """분석 진행률(%)을 사이드카에서 받아 중계. 프론트가 폴링한다."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{VIDEO_SERVICE_URL}/progress", params={"job_id": job_id})
        return resp.json()
    except Exception:
        return {"percent": 0}


async def _save_upload_to_temp(file: UploadFile) -> str:
    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in VIDEO_SUFFIXES:
        suffix = ".mp4"

    fd, path = tempfile.mkstemp(suffix=suffix)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_VIDEO_BYTES:
                    raise HTTPException(status_code=413, detail="영상 용량이 너무 큽니다. 200MB 이하로 올려주세요.")
                out.write(chunk)
    except BaseException:
        _remove_quietly(path)
        raise
    return path


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


@router.post("/analyze_video")
async def analyze_video_proxy(
    exercise_type: str = Form(...),
    file: UploadFile = File(...),
    job_id: str = Form(""),
    current_user: User = Depends(get_current_user),
):
    """업로드된 운동 영상을 임시 저장 후, 영상 분석 사이드카(8003)에 위임하고 결과를 중계.
    사이드카가 YOLO 크롭 + MediaPipe + 모델/룰 + 이벤트 점수까지 수행한다."""
    path = await _save_upload_to_temp(file)
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                f"{VIDEO_SERVICE_URL}/analyze_video",
                json={"video_path": path, "exercise_type": exercise_type, "job_id": job_id},
            )
        return resp.json()
    except Exception:
        logger.exception("영상 분석 사이드카 호출 실패 (user_id=%s)", current_user.id)
        return {"analysis_error": True, "exercise_supported": True,
                "overall": "분석 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."}
    finally:
        _remove_quietly(path)


class FormCheckLogCreate(BaseModel):
    exercise_type: str
    score: float
    rep_count: Optional[int] = None
    cat_scores: Optional[Dict[str, float]] = None
    cat_details: Optional[Dict[str, str]] = None
    overall: Optional[str] = None
    date: Optional[str] = None  # YYYY-MM-DD (클라이언트 로컬 날짜). 없으면 서버 오늘


@router.post("/formcheck/log")
def save_formcheck_log(
    data: FormCheckLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """analyze_video 로 받은 폼체크 결과를 로그인 유저의 그날 기록으로 저장한다."""
    try:
        logged = date_t.fromisoformat(data.date) if data.date else date_t.today()
    except ValueError:
        logged = date_t.today()

    log = FormCheckLog(
        user_id=current_user.id,
        logged_date=logged,
        exercise_type=data.exercise_type,
        score=data.score,
        rep_count=data.rep_count,
        cat_scores=data.cat_scores,
        cat_details=data.cat_details,
        overall=data.overall,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"status": "success", "id": log.id}
