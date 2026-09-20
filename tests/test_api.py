import api
import json
import pytest
import logs_db
import chat_service
from fastapi.testclient import TestClient
from openai.types.responses import ResponseOutputMessage, ResponseOutputRefusal
from types import SimpleNamespace

@pytest.fixture(autouse=True)
def clear_rate_limit_state():
    api.request_times.clear()

def test_conversation_persists_and_continues(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))
    received_history_lengths = []

    def fake_run_agent(input_list, run_id, message, token_usage):
        received_history_lengths.append(len(input_list))
        input_list.append({"role": "assistant", "content": "Fake response"})
        return [], [], "Fake response"

    monkeypatch.setattr(chat_service, "run_agent", fake_run_agent)

    with TestClient(api.app) as client:
        response = client.post("/chat", json={"message": "test"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "Fake response"
        conversation_id = body["conversation_id"]
        second_response = client.post("/chat", json={"message": "test2", "conversation_id": conversation_id})
        assert second_response.status_code == 200
        second_body = second_response.json()
        assert second_body["conversation_id"] == conversation_id
        saved_history = logs_db.load_conversation(conversation_id)
        assert saved_history is not None
        history = json.loads(saved_history)
        assert len(history) == 4
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        assert history[0]["content"] == "test"
        assert history[1]["content"] == "Fake response"
        assert history[2]["role"] == "user"
        assert history[3]["role"] == "assistant"
        assert history[2]["content"] == "test2"
        assert history[3]["content"] == "Fake response"
        assert received_history_lengths == [1, 3]

def test_conversation_survives_app_restart(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))
    received_history_lengths = []

    def fake_run_agent(input_list, run_id, message, token_usage):
        received_history_lengths.append(len(input_list))
        input_list.append({"role": "assistant", "content": "Fake response"})
        return [], [], "Fake response"

    monkeypatch.setattr(chat_service, "run_agent", fake_run_agent)
    with TestClient(api.app) as client:
        response = client.post("/chat", json={"message": "test"})
        body = response.json()
        conversation_id = body["conversation_id"]
    with TestClient(api.app) as client:
        second_response = client.post("/chat", json={"message": "test2", "conversation_id": conversation_id})
        assert second_response.status_code == 200
        second_body = second_response.json()
        assert second_body["conversation_id"] == conversation_id
        assert received_history_lengths == [1, 3]

def test_nonexistent_conversation_returns_404(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))
    with TestClient(api.app) as client:
        response = client.post("/chat", json={"message": "test", "conversation_id": "does-not-exist"})
        assert response.status_code == 404

def test_blank_message_returns_422(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))
    with TestClient(api.app) as client:
        response = client.post("/chat", json={"message": "     "})
        assert response.status_code == 422

def test_message_too_long_returns_422(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))
    long_message = "a" * 5001
    with TestClient(api.app) as client:
        response = client.post("/chat", json={"message": long_message})
        assert response.status_code == 422

def test_epo_timeout_returns_504(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))

    def fake_run_agent(input_list, run_id, message, token_usage):
        raise api.EPOTimeoutError("timeout")

    monkeypatch.setattr(chat_service, "run_agent", fake_run_agent)
    with TestClient(api.app) as client:
        response = client.post("/chat", json={"message": "test"})
        assert response.status_code == 504
        assert response.json()["detail"] == "EPO request timed out"

def test_tool_history_survives_serialization_and_persistence(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))
    logs_db.init_db()

    input_list = [
        {"role": "user", "content": "Get details for EP1000000"},
        SimpleNamespace(
            type="function_call",
            name="get_patent_details",
            arguments='{"pn":"EP1000000"}',
            call_id="call_1",
        ),
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"publication_number":"EP1000000"}',
        },
        SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(type="output_text", text="Here are the patent details."),
                SimpleNamespace(type="output_text", text="Second part of the answer.")
                ],
        ),
    ]
    serialized = chat_service.serialize_history(input_list)
    logs_db.save_conversation(
        "test-conversation",
        json.dumps(serialized),
    )
    saved_history = logs_db.load_conversation("test-conversation")
    assert saved_history is not None
    restored = json.loads(saved_history)
    assert restored[1] == {
        "type": "function_call",
        "name": "get_patent_details",
        "arguments": '{"pn":"EP1000000"}',
        "call_id": "call_1",
    }
    assert restored[2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"publication_number":"EP1000000"}',
    }
    assert restored[3]["content"] == "Here are the patent details.\nSecond part of the answer."

def test_serialize_history_handles_refusal():
    input_list = [
        ResponseOutputMessage(
            type="message",
            content=[ResponseOutputRefusal(type="refusal", refusal="I cannot help with that request.")],
            id="msg_refudal",
            role="assistant",
            status="completed"
        )
    ]
    serialized = chat_service.serialize_history(input_list)
    assert serialized[0]["content"] == "I cannot help with that request."

def test_busy_conversation_returns_409(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(logs_db, "DATABASE_PATH", str(test_db))
    calls = []

    def fake_run_agent(input_list, run_id, message, token_usage):
        calls.append(1)
        return [], [], "Fake response"

    monkeypatch.setattr(chat_service, "run_agent", fake_run_agent)
    conversation_id = "busy-conversation"
    with TestClient(api.app) as client:
        with chat_service.conversation_guard(conversation_id):
            response = client.post("/chat", json={"message": "Get details for EP1000000", "conversation_id": conversation_id})
    assert response.status_code == 409
    assert response.json() == {"detail": "Conversation is already being processed"}
    assert calls == []
    con = logs_db.get_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT status_code, error_type
        FROM request_logs
        WHERE conversation_id = ?
        """,
        (conversation_id,),
    )
    row = cur.fetchone()
    con.close()
    assert row == (409, "conversation_busy")

def test_conversation_guard_releases_after_error():
    conversation_id = "conversation-with-error"
    with pytest.raises(RuntimeError, match="Test error"):
        with chat_service.conversation_guard(conversation_id):
            raise RuntimeError("Test error")
    with chat_service.conversation_guard(conversation_id):
        assert conversation_id in chat_service.active_conversations
    assert conversation_id not in chat_service.active_conversations

def test_conversation_guard_allows_different_conversations():
    with chat_service.conversation_guard("conversation-a"):
        with chat_service.conversation_guard("conversation-b"):
            assert {"conversation-a", "conversation-b"} <= chat_service.active_conversations
