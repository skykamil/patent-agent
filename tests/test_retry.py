import pytest
import requests
import patent_agent
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

def test_epo_request_retries_on_500(monkeypatch):
    response = requests.Response()
    response.status_code = 500
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)
    with pytest.raises(requests.exceptions.HTTPError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(calls) == 3

def test_epo_request_does_not_retry_on_404(monkeypatch):
    response = requests.Response()
    response.status_code = 404
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    with pytest.raises(requests.exceptions.HTTPError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(calls) == 1

def test_epo_request_returns_immediately_on_200(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    result = patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert result is response
    assert len(calls) == 1

def test_epo_request_retries_on_429(monkeypatch):
    response = requests.Response()
    response.status_code = 429
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)
    with pytest.raises(requests.exceptions.HTTPError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(calls) == 3

def test_epo_request_retries_on_timeout(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        raise requests.exceptions.Timeout

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)
    with pytest.raises(requests.exceptions.Timeout):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(calls) == 3

def test_epo_request_retries_on_connection_error(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        raise requests.exceptions.ConnectionError

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)
    with pytest.raises(requests.exceptions.ConnectionError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(calls) == 3

def test_epo_retry_logs_each_retry(monkeypatch):
    response = requests.Response()
    response.status_code = 500
    calls = []
    retry_logs = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return response

    def fake_log_retry(run_id, tool_call_id, tool_name, service, attempt, reason, wait_seconds):
        retry_logs.append(
            {
                "service": service,
                "attempt": attempt,
                "reason": reason,
                "wait_seconds": wait_seconds,
            }
        )

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", fake_log_retry)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)

    with pytest.raises(requests.exceptions.HTTPError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(calls) == 3
    assert len(retry_logs) == 2
    assert retry_logs[0]["service"] == "EPO"
    assert retry_logs[0]["attempt"] == 1
    assert retry_logs[0]["reason"] == "HTTP 500"
    assert 0.5 <= retry_logs[0]["wait_seconds"] <= 0.75
    assert retry_logs[1]["service"] == "EPO"
    assert retry_logs[1]["attempt"] == 2
    assert retry_logs[1]["reason"] == "HTTP 500"
    assert 1.0 <= retry_logs[1]["wait_seconds"] <= 1.25

def test_epo_request_uses_retry_after_second(monkeypatch):
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "5"
    calls = []
    retry_logs = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return response

    def fake_log_retry(run_id, tool_call_id, tool_name, service, attempt, reason, wait_seconds):
        retry_logs.append(wait_seconds)

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", fake_log_retry)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)
    with pytest.raises(requests.exceptions.HTTPError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert retry_logs == [5.0, 5.0]

def test_epo_request_uses_retry_after_http_date(monkeypatch):
    retry_time = datetime.now(timezone.utc) + timedelta(seconds=60)
    retry_after = format_datetime(retry_time, usegmt=True)
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = retry_after
    calls = []
    retry_logs = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return response

    def fake_log_retry(run_id, tool_call_id, tool_name, service, attempt, reason, wait_seconds):
        retry_logs.append(wait_seconds)

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", fake_log_retry)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)
    with pytest.raises(requests.exceptions.HTTPError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(calls) == 3
    assert len(retry_logs) == 2
    assert all(55.0 <= wait_seconds <= 60.0 for wait_seconds in retry_logs)

def test_epo_request_falls_back_on_invalid_retry_after(monkeypatch):
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "nonsense"
    retry_logs = []

    def fake_request(method, url, **kwargs):
        return response

    def fake_log_retry(run_id, tool_call_id, tool_name, service, attempt, reason, wait_seconds):
        retry_logs.append(wait_seconds)

    monkeypatch.setattr(patent_agent.requests, "request", fake_request)
    monkeypatch.setattr(patent_agent, "log_retry", fake_log_retry)
    monkeypatch.setattr(patent_agent.epo_request_with_retry.retry, "sleep", lambda seconds: None)
    with pytest.raises(requests.exceptions.HTTPError):
        patent_agent.epo_request_with_retry("GET", "https://example.test")
    assert len(retry_logs) == 2
    assert 0.5 <= retry_logs[0] <= 0.75
    assert 1.0 <= retry_logs[1] <= 1.25
