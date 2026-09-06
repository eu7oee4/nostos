from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
def health():
    return {"ok": True, "service": "nostos", "stage": "scaffold"}


@router.post("/chat")
def chat_stub():
    return JSONResponse({"detail": "not implemented — scaffold only"}, status_code=501)
