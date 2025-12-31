"""
FastAPI server for SoundCloud/YouTube MP3 converter.
"""
import os
from typing import List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import modal as md

app = FastAPI(title="SC/YT Converter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_MODAL_ENV = os.getenv("MODAL_ENV", "dev")
MODAL_APP_NAME = os.getenv("MODAL_APP_NAME", "converter")


class UrlRequest(BaseModel):
    urls: List[str]


def get_modal_function(env: str):
    """Get Modal download function from specified environment."""
    try:
        return md.Function.from_name(MODAL_APP_NAME, "download", environment_name=env)
    except Exception as e:
        print(f"Modal lookup error (env={env}): {e}")
        import traceback
        traceback.print_exc()
        return None


@app.get("/")
def root():
    return {"status": "ok", "app": "converter", "default_env": DEFAULT_MODAL_ENV}


@app.get("/health")
def health():
    """Check if Modal function is accessible."""
    fn = get_modal_function(DEFAULT_MODAL_ENV)
    return {"modal": fn is not None, "default_env": DEFAULT_MODAL_ENV}


@app.post("/convert")
def convert_full(req: UrlRequest, http_req: Request):
    """Convert URLs to MP3."""
    if len(req.urls) > 10:
        raise HTTPException(status_code=400, detail="Max 10 URLs per request")

    if not req.urls:
        raise HTTPException(status_code=400, detail="No URLs provided")

    env = http_req.headers.get("X-Modal-Env", DEFAULT_MODAL_ENV)
    print(f"Using environment: {env}")

    fn = get_modal_function(env)
    if not fn:
        raise HTTPException(
            status_code=503,
            detail=f"Modal function not deployed (env: {env}). Run: modal deploy conversion/worker.py --env {env}"
        )

    reqs = [{"id": str(i), "url": u} for i, u in enumerate(req.urls)]

    try:
        return fn.remote(reqs)
    except Exception as e:
        print(f"Remote call error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
