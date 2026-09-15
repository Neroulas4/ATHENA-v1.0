"""Minimal deployment-facing API factory.

Optional FastAPI integration can wrap `run_workflow`. The core remains
independent of the web server so the same engine can run locally or hosted.
"""
from .bridge import execute

def run_workflow(kind: str):
    return execute(kind)
