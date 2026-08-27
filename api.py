import json
import uuid
from fastapi import FastAPI
from pydantic import BaseModel
from openai.types.responses.response_input_param import ResponseInputParam
from contextlib import asynccontextmanager
from logs_db import init_db, save_conversation, load_conversation
from patent_agent import run_agent

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None

class ChatResponse(BaseModel):
    answer: str
    conversation_id: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

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


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    conversation_id = request.conversation_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    history_json = load_conversation(conversation_id)
    if history_json is None:
        input_list: ResponseInputParam = []
    else:
        input_list = json.loads(history_json)
    input_list.append({"role": "user", "content": request.message})
    actual_calls, tool_outputs, final_response = run_agent(input_list, run_id, request.message)
    serialized_history = serialize_history(input_list)
    history_json = json.dumps(serialized_history)
    save_conversation(conversation_id, history_json)
    return {"answer": final_response, "conversation_id": conversation_id}

@app.get("/")
def health():
    return {"status": "ok"}
