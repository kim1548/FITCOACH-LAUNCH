from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.routine_log import RoutineLog
from app.models.program_state import UserProgramState
from app.models.user import User
from app.services.journal_ai import generate_and_save_ai_comment
from app.api.v1.endpoints.auth import get_current_user
from pydantic import BaseModel

router = APIRouter()

# --- 프로그램 페이지(/program)에서 완료한 운동 세션을 DB에 저장 ---

class SetEntry(BaseModel):
    reps: int
    completed: bool = True


class LiftEntry(BaseModel):
    lift_id: str
    anchor_key: str
    weight: float
    prev_weight: float
    outcome: str = ""
    sets: List[SetEntry] = []

class RoutineLogCreate(BaseModel):
    date: str
    workout: str
    workout_label: str
    duration_sec: int
    lifts: List[LiftEntry]


def _session_volume(lifts: List[LiftEntry]) -> float:
    """완료한 세트만 합산: Σ(무게 × 완료 반복수). 세트 정보가 비어 있으면 0 기여."""
    vol = 0.0
    for lift in lifts:
        done_reps = sum((s.reps or 0) for s in lift.sets if s.completed)
        vol += (lift.weight or 0) * done_reps
    return round(vol, 1)


@router.post("/log")
def create_routine_log(
    data: RoutineLogCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """프론트 program 페이지의 완료된 운동 세션을 로그인 유저 계정에 저장합니다."""
    # 프론트가 보내는 ISO 문자열을 naive datetime으로 변환 (DB의 다른 시각 컬럼과 동일하게)
    try:
        dt = datetime.fromisoformat(data.date.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
    except ValueError:
        dt = datetime.now()

    new_log = RoutineLog(
        user_id=current_user.id,
        routine_name=data.workout_label or data.workout,
        workout_date=dt,
        session_data=[lift.model_dump() for lift in data.lifts],
        total_volume=_session_volume(data.lifts),
        memo=None,
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)

    # 응답은 즉시 돌려보내고, 그날의 Journal AI 코멘트는 백그라운드에서 생성
    background_tasks.add_task(generate_and_save_ai_comment, current_user.id, dt.date())

    return {"status": "success", "log_id": new_log.id}


# --- 프로그램 진행 상태(working weight·증량단계 등) 계정 동기화 ---
# 프론트가 localStorage('fiteating.program')로 관리하던 블롭을 서버에 사용자당 1행으로
# 보관해 기기 간 이어하기를 지원한다. 매 변경마다 PUT 되므로 서버가 항상 최신이다.

class ProgramStatePayload(BaseModel):
    state: dict


@router.get("/program-state")
def get_program_state(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (
        db.query(UserProgramState)
        .filter(UserProgramState.user_id == current_user.id)
        .first()
    )
    if row is None:
        return {"state": None, "updated_at": None}
    return {
        "state": row.state,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/program-state")
def put_program_state(
    payload: ProgramStatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (
        db.query(UserProgramState)
        .filter(UserProgramState.user_id == current_user.id)
        .first()
    )
    if row is None:
        row = UserProgramState(user_id=current_user.id, state=payload.state)
        db.add(row)
    else:
        row.state = payload.state
    db.commit()
    db.refresh(row)
    return {
        "status": "success",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
