import json
from openai.types.responses.response_input_param import ResponseInputParam
from logs_db import load_conversation, save_conversation
from agent import run_agent

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

def load_history(conversation_id: str) -> ResponseInputParam | None:
    history_json = load_conversation(conversation_id)
    if history_json is None:
        return None
    return json.loads(history_json)

def save_history(conversation_id: str, input_list: ResponseInputParam) -> None:
    serialized_history = serialize_history(input_list)
    history_json = json.dumps(serialized_history)
    save_conversation(conversation_id, history_json)

def prepare_history(conversation_id: str, message: str, is_new_conversation: bool) -> ResponseInputParam | None:
    input_list = load_history(conversation_id)
    if input_list is None:
        if not is_new_conversation:
            return None
        input_list = []
    input_list.append({
        "role": "user",
        "content": message,
    })
    return input_list

def run_and_save_chat(conversation_id: str, input_list: ResponseInputParam, run_id: str, message: str, token_usage: dict[str, int]) -> str:
    _, _, final_response = run_agent(input_list, run_id, message, token_usage)
    save_history(conversation_id, input_list)
    return final_response
