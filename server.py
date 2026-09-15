import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .bridge import execute

app = FastAPI(title="ATHENA Runtime", version="0.10.0")

class RunRequest(BaseModel):
    kind: str

@app.get("/health")
def health():
    return {"status": "ok", "service": "athena-runtime"}

@app.post("/run")
def run(req: RunRequest):
    if req.kind not in {"DAILY_BRIEF", "POST_MORTEM"}:
        raise HTTPException(400, "Unsupported workflow")
    result = execute(req.kind)
    return result.model_dump(mode="json")
