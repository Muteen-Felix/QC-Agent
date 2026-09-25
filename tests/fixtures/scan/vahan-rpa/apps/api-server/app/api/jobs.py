from fastapi import APIRouter

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(job_id: str):
    return {}
