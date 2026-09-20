import json
import uuid
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from openai import APIConnectionError, APITimeoutError, APIStatusError, RateLimitError
from contextlib import asynccontextmanager
from logs_db import init_db, try_increment_daily_usage, log_request
from errors import (EPOTimeoutError, EPOConnectionError, EPORateLimitError, EPOUpstreamError, AgentInternalError, AgentRuntimeLimitError, ConversationBusyError)
from chat_service import prepare_history, run_and_save_chat, conversation_guard

MAX_HISTORY_CHARS = 100_000
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 600
DAILY_REQUEST_LIMIT = 50
DAILY_LIMIT_TIMEZONE = ZoneInfo("Europe/Warsaw")

request_times: dict[str, list[float]] = {}

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    conversation_id: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value.strip()

class ChatResponse(BaseModel):
    answer: str
    conversation_id: str

class ErrorResponse(BaseModel):
    detail: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.exception_handler(Exception)
async def internal_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )

def get_client_ip(request: Request) -> str:
    if forwarded_for := request.headers.get("X-Forwarded-For"):
        return forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Client IP unavailable")

def check_rate_limit(client_ip: str) -> None:
    recent_timestamps = []
    now = time.monotonic()
    timestamps = request_times.get(client_ip, [])
    for timestamp in timestamps:
        if now - timestamp < RATE_LIMIT_WINDOW_SECONDS:
            recent_timestamps.append(timestamp)
    if len(recent_timestamps) >= RATE_LIMIT_REQUESTS:
        request_times[client_ip] = recent_timestamps
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Requests limit reached")
    recent_timestamps.append(now)
    request_times[client_ip] = recent_timestamps

@app.post(
        "/chat",
        response_model=ChatResponse,
        status_code=status.HTTP_200_OK,
        responses={
            status.HTTP_404_NOT_FOUND: {
                "model": ErrorResponse,
                "description": "Conversation not found",
            },
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "model": ErrorResponse,
                "description": "Upstream service unavailable",
            },
            status.HTTP_504_GATEWAY_TIMEOUT: {
                "model": ErrorResponse,
                "description": "Upstream service request timed out",
            },
            status.HTTP_502_BAD_GATEWAY: {
                "model": ErrorResponse,
                "description": "Upstream service returned an error",
            },
            status.HTTP_429_TOO_MANY_REQUESTS: {
                "model": ErrorResponse,
                "description": "Rate limit exceeded",
            },
            status.HTTP_500_INTERNAL_SERVER_ERROR: {
                "model": ErrorResponse,
                "description": "Internal server error",
            },
            status.HTTP_413_CONTENT_TOO_LARGE: {
                "model": ErrorResponse,
                "description": "Conversation history is too large",
            },
            status.HTTP_409_CONFLICT: {
                "model": ErrorResponse,
                "description": "Conversation is already being processed",
            },
        },
    )
def chat(payload: ChatRequest, request: Request):
    start_time = time.monotonic()
    run_id = None
    conversation_id = payload.conversation_id
    status_code = status.HTTP_200_OK
    error_type = None
    error_message = None
    token_usage = {
        "input": 0,
        "output": 0,
    }
    try:
        try:
            client_ip = get_client_ip(request)
        except HTTPException:
            error_type = "client_ip_unavailable"
            raise
        try:
            check_rate_limit(client_ip)
        except HTTPException:
            error_type = "ip_rate_limit"
            raise
        conversation_id = payload.conversation_id or str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        is_new_conversation = payload.conversation_id is None
        with conversation_guard(conversation_id):
            input_list = prepare_history(conversation_id, payload.message, is_new_conversation)
            if input_list is None:
                error_type = "conversation_not_found"
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation ID not found")
            if len(json.dumps(input_list)) > MAX_HISTORY_CHARS:
                error_type = "history_too_large"
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Conversation history is too large")
            day = datetime.now(DAILY_LIMIT_TIMEZONE).date().isoformat()
            if not try_increment_daily_usage(day, DAILY_REQUEST_LIMIT):
                error_type = "daily_rate_limit"
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Daily request limit reached")
            try:
                final_response = run_and_save_chat(conversation_id, input_list, run_id, payload.message, token_usage)
            except EPOTimeoutError as e:
                error_type = "epo_timeout"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="EPO request timed out")
            except EPOConnectionError as e:
                error_type = "epo_connection"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="EPO service unavailable")
            except EPORateLimitError as e:
                error_type = "epo_rate_limit"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="EPO rate limit exceeded")
            except EPOUpstreamError as e:
                error_type = "epo_upstream"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="EPO returned an upstream error")
            except AgentRuntimeLimitError as e:
                error_type = "agent_runtime_limit"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Agent runtime limit reached")
            except AgentInternalError as e:
                error_type = "agent_internal"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")
            except APITimeoutError as e:
                error_type = "openai_timeout"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="OpenAI request timed out")
            except RateLimitError as e:
                error_type = "openai_rate_limit"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="OpenAI rate limit exceeded")
            except APIStatusError as e:
                error_type = "openai_upstream"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OpenAI returned an upstream error")
            except APIConnectionError as e:
                error_type = "openai_connection"
                error_message = str(e)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OpenAI service unavailable")
        return {"answer": final_response, "conversation_id": conversation_id}
    except ConversationBusyError as e:
        status_code = status.HTTP_409_CONFLICT
        error_type = "conversation_busy"
        error_message = str(e)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conversation is already being processed")
    except HTTPException as e:
        status_code = e.status_code
        if error_message is None:
            error_message = str(e.detail)
        raise
    except Exception as e:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_type = "internal_error"
        error_message = str(e)
        raise
    finally:
        try:
            latency_ms = (time.monotonic() - start_time) * 1000
            log_request(run_id, conversation_id, status_code, latency_ms, error_type, error_message, token_usage["input"], token_usage["output"])
        except Exception as log_error:
            print(f"Failed to write request log: {log_error}")

@app.get("/", response_class=FileResponse)
def frontend():
    return FileResponse("static/index.html")

@app.get("/health", status_code=status.HTTP_200_OK)
def health():
    return {"status": "ok"}
