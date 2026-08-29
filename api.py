import json
import uuid
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from openai import APIConnectionError, APITimeoutError, APIStatusError, RateLimitError
from openai.types.responses.response_input_param import ResponseInputParam
from contextlib import asynccontextmanager
from logs_db import init_db, save_conversation, load_conversation
from patent_agent import (run_agent, EPOTimeoutError, EPOConnectionError, EPORateLimitError, EPOUpstreamError, AgentInternalError)

MAX_HISTORY_CHARS = 100_000

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

@app.exception_handler(Exception)
async def internal_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )

def serialize_history(input_list: ResponseInputParam):
    serialized_history = []
    for item in input_list:
        if isinstance(item, dict):
            serialized_history.append(item)
        elif item.type == "function_call":
            serialized_history.append({
                "type": item.type,
                "name": item.name,
                "arguments": item.arguments,
                "call_id": item.call_id
            })
        elif item.type == "message":
            serialized_history.append({"role": "assistant", "content": item.content[0].text})
    return serialized_history


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
                "description": "Upstream service rate limit exceeded",
            },
            status.HTTP_500_INTERNAL_SERVER_ERROR: {
                "model": ErrorResponse,
                "description": "Internal server error",
            },
            status.HTTP_413_CONTENT_TOO_LARGE: {
                "model": ErrorResponse,
                "description": "Conversation history is too large",
            },
        },
    )
def chat(request: ChatRequest):
    conversation_id = request.conversation_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    history_json = load_conversation(conversation_id)
    if request.conversation_id is not None and history_json is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation ID not found")
    if history_json is None:
        input_list: ResponseInputParam = []
    else:
        input_list = json.loads(history_json)
    input_list.append({"role": "user", "content": request.message})
    if len(json.dumps(input_list)) > MAX_HISTORY_CHARS:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Conversation history is too large")
    try:
        actual_calls, tool_outputs, final_response = run_agent(input_list, run_id, request.message)
    except EPOTimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="EPO request timed out")
    except EPOConnectionError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="EPO service unavailable")
    except EPORateLimitError:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="EPO rate limit exceeded")
    except EPOUpstreamError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="EPO returned an upstream error")
    except AgentInternalError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")
    except APITimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="OpenAI request timed out")
    except RateLimitError:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="OpenAI rate limit exceeded")
    except APIStatusError as e:
        print(e.status_code)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OpenAI returned an upstream error")
    except APIConnectionError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OpenAI service unavailable")
    serialized_history = serialize_history(input_list)
    history_json = json.dumps(serialized_history)
    save_conversation(conversation_id, history_json)
    return {"answer": final_response, "conversation_id": conversation_id}

@app.get("/", status_code=status.HTTP_200_OK)
def health():
    return {"status": "ok"}
