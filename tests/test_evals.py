import evals

def test_missing_search_output_counts_as_failed_response(monkeypatch, capsys):
    test_cases = [{"input": "Find Siemens patents", "expected_calls": [{"name": "search_patent", "args": {"pa": "Siemens"}}]}, {"input": "What is 2 + 2?", "expected_calls": [], "expected_response_contains": ["4"]}]
    monkeypatch.setattr(evals, "EVAL_SET", test_cases)

    def fake_run_agent(input_list, run_id, user_input):
        return [], [], "4"

    evals.run_eval(fake_run_agent)
    output = capsys.readouterr().out
    assert "Final-response eval: 1/2" in output